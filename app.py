"""
Schedulr - Smart Study Planner
Flask app with mentor-set exams, adaptive student scheduling, units/topics, and tests.
"""

from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
import secrets
from datetime import datetime, timedelta
import os
import logging

app = Flask(__name__)

# Use environment variable for SECRET_KEY, fallback to a default for development
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

# Database path: relative to this app.py file for portability across servers
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, 'schedulr.db')

# Configure logging for deployment monitoring
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_db():
    """Get database connection with row factory for dict-like access."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    Initialize database schema if not already initialized.
    Tables: Users, StudySessions, StudyUnits, Tests, StudentProgress.
    Safely checks if tables exist before recreating - supports fresh deployments.
    """
    # Check if database already has tables
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables_exist = cursor.fetchone() is not None
    
    if tables_exist:
        logger.info('Database already initialized. Skipping schema creation.')
        conn.close()
        return
    
    logger.info('Initializing database schema...')

    # Drop existing tables in reverse dependency order
    cursor.execute('DROP TABLE IF EXISTS StudySchedule')
    cursor.execute('DROP TABLE IF EXISTS Exams')
    cursor.execute('DROP TABLE IF EXISTS StudentProgress')
    cursor.execute('DROP TABLE IF EXISTS Options')
    cursor.execute('DROP TABLE IF EXISTS Questions')
    cursor.execute('DROP TABLE IF EXISTS Tests')
    cursor.execute('DROP TABLE IF EXISTS StudyUnits')
    cursor.execute('DROP TABLE IF EXISTS StudySessions')
    cursor.execute('DROP TABLE IF EXISTS Users')

    # Users: role, username, password; mentor_code (mentors only); mentor_id (students only, FK to mentor)
    cursor.execute('''
        CREATE TABLE Users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('student', 'mentor')),
            mentor_code TEXT UNIQUE,
            mentor_id INTEGER,
            FOREIGN KEY (mentor_id) REFERENCES Users(id)
        )
    ''')

    # StudySessions: student_id links to Users (students)
    cursor.execute('''
        CREATE TABLE StudySessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            start_time TEXT NOT NULL,
            notes TEXT,
            completed INTEGER DEFAULT 0,
            FOREIGN KEY (student_id) REFERENCES Users(id)
        )
    ''')

    # StudyUnits: mentor-controlled units/topics per subject
    cursor.execute('''
        CREATE TABLE StudyUnits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mentor_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            unit_name TEXT NOT NULL,
            topic_name TEXT NOT NULL,
            FOREIGN KEY (mentor_id) REFERENCES Users(id)
        )
    ''')

    # Tests: mentor-created tests for each unit (test_title only; questions in separate tables)
    cursor.execute('''
        CREATE TABLE Tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mentor_id INTEGER NOT NULL,
            unit_id INTEGER NOT NULL,
            test_title TEXT NOT NULL DEFAULT 'Test',
            FOREIGN KEY (mentor_id) REFERENCES Users(id),
            FOREIGN KEY (unit_id) REFERENCES StudyUnits(id)
        )
    ''')

    # Questions: linked to Tests, type MCQ or ShortAnswer
    cursor.execute('''
        CREATE TABLE Questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('MCQ', 'ShortAnswer')),
            correct_answer TEXT,
            FOREIGN KEY (test_id) REFERENCES Tests(id)
        )
    ''')

    # Options: for MCQ questions only; is_correct 1 = correct answer
    cursor.execute('''
        CREATE TABLE Options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            option_text TEXT NOT NULL,
            is_correct INTEGER DEFAULT 0,
            FOREIGN KEY (question_id) REFERENCES Questions(id)
        )
    ''')

    # StudentProgress: tracks completion, test results, difficulty_level per student per unit
    # difficulty_level: 'easy' (>70%), 'medium' (50-70%), 'hard' (<50%) - derived from test_score
    cursor.execute('''
        CREATE TABLE StudentProgress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            unit_id INTEGER NOT NULL,
            completed INTEGER DEFAULT 0,
            test_taken INTEGER DEFAULT 0,
            test_score REAL,
            difficulty_level TEXT,
            FOREIGN KEY (student_id) REFERENCES Users(id),
            FOREIGN KEY (unit_id) REFERENCES StudyUnits(id),
            UNIQUE(student_id, unit_id)
        )
    ''')

    # Exams: mentor-assigned exam dates per unit (students cannot edit)
    cursor.execute('''
        CREATE TABLE Exams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mentor_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            unit_id INTEGER NOT NULL,
            exam_date TEXT NOT NULL,
            FOREIGN KEY (mentor_id) REFERENCES Users(id),
            FOREIGN KEY (unit_id) REFERENCES StudyUnits(id),
            UNIQUE(unit_id)
        )
    ''')

    # StudySchedule: adaptive suggested study sessions (generated per student)
    cursor.execute('''
        CREATE TABLE StudySchedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            unit_id INTEGER NOT NULL,
            suggested_study_time TEXT NOT NULL,
            completed INTEGER DEFAULT 0,
            FOREIGN KEY (student_id) REFERENCES Users(id),
            FOREIGN KEY (unit_id) REFERENCES StudyUnits(id)
        )
    ''')

    conn.commit()
    conn.close()
    logger.info('Database schema created successfully.')


def generate_mentor_code():
    """Generate a unique 8-character code for mentor signup linking."""
    return secrets.token_hex(4)


def authenticate_user(username, password):
    """Authenticate user by username and password. Returns user row or None."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Users WHERE username = ? AND password = ?', (username, password))
    user = cursor.fetchone()
    conn.close()
    return user


def get_user_by_id(user_id):
    """Get user by ID. Returns user row or None."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user


def get_mentor_by_code(mentor_code):
    """Look up mentor by their unique code. Returns mentor row or None."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM Users WHERE role = 'mentor' AND mentor_code = ?", (mentor_code,))
    mentor = cursor.fetchone()
    conn.close()
    return mentor


def generate_adaptive_schedule(student_id, mentor_id):
    """
    Generate suggested study sessions for a student based on:
    - Topics not completed (highest priority)
    - Topics where student struggled or failed (test_score < 70)
    - Upcoming exam dates (prioritize units with exams soon)
    Returns list of {unit, suggested_study_time, reason, exam_date} ordered by priority.
    """
    conn = get_db()
    cursor = conn.cursor()

    # All units assigned by mentor
    cursor.execute('''
        SELECT u.id, u.subject, u.unit_name, u.topic_name
        FROM StudyUnits u
        WHERE u.mentor_id = ?
        ORDER BY u.subject, u.unit_name
    ''', (mentor_id,))
    units = [dict(row) for row in cursor.fetchall()]

    # Progress for this student
    cursor.execute('''
        SELECT unit_id, completed, test_taken, test_score, difficulty_level
        FROM StudentProgress WHERE student_id = ?
    ''', (student_id,))
    progress = {row['unit_id']: dict(row) for row in cursor.fetchall()}

    # Exam dates: unit_id -> exam_date
    cursor.execute('SELECT unit_id, exam_date FROM Exams WHERE mentor_id = ?', (mentor_id,))
    exams = {row['unit_id']: row['exam_date'] for row in cursor.fetchall()}

    conn.close()

    now = datetime.now()
    suggestions = []

    for u in units:
        uid = u['id']
        prog = progress.get(uid, {})
        completed = prog.get('completed', 0)
        test_taken = prog.get('test_taken', 0)
        score = prog.get('test_score')
        difficulty = prog.get('difficulty_level')
        exam_date = exams.get(uid)

        # Compute suggested study time: base slot, earlier if exam is soon
        if exam_date:
            try:
                ed = datetime.fromisoformat(exam_date.replace('T', ' '))
                days_until = (ed - now).days
                if days_until <= 3:
                    suggested_time = (now + timedelta(hours=2)).strftime('%Y-%m-%dT%H:00')
                elif days_until <= 7:
                    suggested_time = (now + timedelta(days=1)).strftime('%Y-%m-%dT09:00')
                else:
                    suggested_time = (now + timedelta(days=2)).strftime('%Y-%m-%dT09:00')
            except (ValueError, TypeError):
                suggested_time = (now + timedelta(days=1)).strftime('%Y-%m-%dT09:00')
        else:
            suggested_time = (now + timedelta(days=1)).strftime('%Y-%m-%dT09:00')

        # Priority and reason
        if not completed:
            reason = 'Not started'
            priority = 10 if exam_date else 5
        elif not test_taken:
            reason = 'Completed, test pending'
            priority = 9 if exam_date else 4
        elif score is not None and score < 70:
            reason = f'Struggled (score: {score:.0f}%)'
            priority = 8 if exam_date else 3
        else:
            reason = 'Completed'
            priority = 1

        if exam_date:
            reason += f' | Exam: {exam_date[:10]}'

        suggestions.append({
            'unit': u,
            'unit_id': uid,
            'suggested_study_time': suggested_time,
            'reason': reason,
            'exam_date': exam_date,
            'priority': priority,
            'completed': completed,
            'test_taken': test_taken,
        })

    # Sort by priority (descending), then by exam date proximity
    suggestions.sort(key=lambda x: (-x['priority'], x['suggested_study_time']))
    return suggestions


# --- Routes ---

@app.route('/')
def index():
    """Redirect to login."""
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    Login page. Checks credentials dynamically against Users table.
    No hardcoded accounts.
    """
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password:
            flash('Please enter both username and password', 'error')
            return render_template('login.html')

        user = authenticate_user(username, password)
        if user:
            if user['role'] == 'student':
                return redirect(url_for('student_dashboard', user_id=user['id']))
            elif user['role'] == 'mentor':
                return redirect(url_for('mentor_dashboard', user_id=user['id']))
        else:
            flash('Invalid credentials', 'error')

    return render_template('login.html')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    """
    Signup page for both students and mentors.
    - Mentors: receive a unique mentor_code upon signup.
    - Students: must enter a valid mentor_code to link with that mentor.
    """
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        role = request.form.get('role', '').strip()
        mentor_code_input = request.form.get('mentor_code', '').strip()

        # Validation
        if not username or not password or not role:
            flash('Username, password, and role are required', 'error')
            return render_template('signup.html')

        if role not in ('student', 'mentor'):
            flash('Invalid role', 'error')
            return render_template('signup.html')

        # Students must provide a valid mentor code
        mentor_id = None
        new_mentor_code = None

        if role == 'student':
            if not mentor_code_input:
                flash('Mentor code is required for student signup', 'error')
                return render_template('signup.html')
            mentor = get_mentor_by_code(mentor_code_input)
            if not mentor:
                flash('Invalid mentor code. Please check with your mentor.', 'error')
                return render_template('signup.html')
            mentor_id = mentor['id']

        elif role == 'mentor':
            new_mentor_code = generate_mentor_code()

        conn = get_db()
        cursor = conn.cursor()

        # Check username uniqueness
        cursor.execute('SELECT id FROM Users WHERE username = ?', (username,))
        if cursor.fetchone():
            conn.close()
            flash('Username already exists', 'error')
            return render_template('signup.html')

        # Insert new user
        cursor.execute('''
            INSERT INTO Users (username, password, role, mentor_code, mentor_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (username, password, role, new_mentor_code, mentor_id))
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()

        flash('Account created successfully!', 'success')
        if role == 'mentor':
            flash(f'Your mentor code is: {new_mentor_code}. Share this with your students.', 'info')

        return redirect(url_for('login'))

    return render_template('signup.html')


@app.route('/student_dashboard')
def student_dashboard():
    """
    Student dashboard. Shows sessions, assigned units/topics from mentor, progress, and tests.
    Students can view units, mark complete, and take tests.
    """
    user_id = request.args.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))

    user = get_user_by_id(user_id)
    if not user or user['role'] != 'student':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    mentor_id = user['mentor_id']
    if not mentor_id:
        return render_template('student_dashboard.html', user=user, sessions=[], units=[],
                              progress_map={}, upcoming_exams=[], suggested_schedule=[])
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM StudySessions WHERE student_id = ? ORDER BY start_time ASC
    ''', (user_id,))
    sessions = cursor.fetchall()

    # Units assigned by mentor; one test per unit (LEFT JOIN)
    cursor.execute('''
        SELECT u.id, u.subject, u.unit_name, u.topic_name, u.mentor_id,
               t.id as test_id, t.test_title
        FROM StudyUnits u
        LEFT JOIN Tests t ON t.unit_id = u.id
        WHERE u.mentor_id = ?
        ORDER BY u.subject, u.unit_name
    ''', (mentor_id,))
    units = cursor.fetchall()

    # Progress for this student: unit_id -> {completed, test_taken, test_score, difficulty_level}
    cursor.execute('''
        SELECT unit_id, completed, test_taken, test_score, difficulty_level FROM StudentProgress WHERE student_id = ?
    ''', (user_id,))
    progress_map = {row['unit_id']: row for row in cursor.fetchall()}

    # Upcoming exams (units linked to mentor)
    cursor.execute('''
        SELECT e.exam_date, e.subject, su.unit_name, su.topic_name, su.id as unit_id
        FROM Exams e
        JOIN StudyUnits su ON e.unit_id = su.id
        WHERE e.mentor_id = ? AND e.exam_date >= date('now')
        ORDER BY e.exam_date ASC
    ''', (mentor_id,))
    upcoming_exams = cursor.fetchall()

    conn.close()

    # Generate adaptive suggested study schedule (based on completion, test scores, exams)
    suggested_schedule = generate_adaptive_schedule(user_id, mentor_id)

    return render_template('student_dashboard.html', user=user, sessions=sessions,
                          units=units, progress_map=progress_map,
                          upcoming_exams=upcoming_exams,
                          suggested_schedule=suggested_schedule)


@app.route('/mentor_dashboard')
def mentor_dashboard():
    """
    Mentor dashboard. Shows sessions, units/topics, tests, and students' progress/test results.
    Mentors manage curriculum; students interact with it.
    """
    user_id = request.args.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))

    user = get_user_by_id(user_id)
    if not user or user['role'] != 'mentor':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    # Sessions for students linked to this mentor
    cursor.execute('''
        SELECT s.id, s.subject, s.start_time, s.notes, s.completed,
               u.username as student_name, u.id as student_id
        FROM StudySessions s
        JOIN Users u ON s.student_id = u.id
        WHERE u.mentor_id = ?
        ORDER BY s.start_time ASC
    ''', (user_id,))
    sessions = cursor.fetchall()

    cursor.execute('''
        SELECT COUNT(*) FROM StudySessions s
        JOIN Users u ON s.student_id = u.id
        WHERE u.mentor_id = ? AND s.completed = 1
    ''', (user_id,))
    completed_count = cursor.fetchone()[0]

    cursor.execute('''
        SELECT COUNT(*) FROM StudySessions s
        JOIN Users u ON s.student_id = u.id
        WHERE u.mentor_id = ? AND s.completed = 0
    ''', (user_id,))
    pending_count = cursor.fetchone()[0]

    # Units/topics created by this mentor
    cursor.execute('''
        SELECT * FROM StudyUnits WHERE mentor_id = ? ORDER BY subject, unit_name
    ''', (user_id,))
    units = cursor.fetchall()

    # Tests: unit_id -> test row
    cursor.execute('SELECT * FROM Tests WHERE mentor_id = ?', (user_id,))
    tests_by_unit = {row['unit_id']: row for row in cursor.fetchall()}

    # Exams: unit_id -> exam row
    cursor.execute('SELECT * FROM Exams WHERE mentor_id = ?', (user_id,))
    exams_by_unit = {row['unit_id']: row for row in cursor.fetchall()}

    # Progress for all linked students (with unit names, difficulty_level)
    cursor.execute('''
        SELECT sp.*, u.username as student_name, su.subject, su.unit_name, su.topic_name
        FROM StudentProgress sp
        JOIN Users u ON sp.student_id = u.id
        JOIN StudyUnits su ON sp.unit_id = su.id
        WHERE u.mentor_id = ?
    ''', (user_id,))
    progress_list = cursor.fetchall()

    conn.close()

    return render_template('mentor_dashboard.html',
                          user=user,
                          sessions=sessions,
                          completed_count=completed_count,
                          pending_count=pending_count,
                          units=units,
                          tests_by_unit=tests_by_unit,
                          exams_by_unit=exams_by_unit,
                          progress_list=progress_list)


@app.route('/add_session', methods=['POST'])
def add_session():
    """Add a new study session (students only)."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))

    user = get_user_by_id(user_id)
    if not user or user['role'] != 'student':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    subject = request.form.get('subject', '').strip()
    start_time = request.form.get('start_time', '').strip()
    notes = request.form.get('notes', '').strip()

    if not subject or not start_time:
        flash('Subject and start time are required', 'error')
        return redirect(url_for('student_dashboard', user_id=user_id))

    try:
        datetime.fromisoformat(start_time.replace('T', ' '))
    except ValueError:
        flash('Invalid date/time format', 'error')
        return redirect(url_for('student_dashboard', user_id=user_id))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO StudySessions (student_id, subject, start_time, notes, completed)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, subject, start_time, notes, 0))
    conn.commit()
    conn.close()

    flash('Study session added successfully', 'success')
    return redirect(url_for('student_dashboard', user_id=user_id))


@app.route('/edit_session/<int:session_id>', methods=['POST'])
def edit_session(session_id):
    """Edit an existing study session (students only)."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))

    user = get_user_by_id(user_id)
    if not user or user['role'] != 'student':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM StudySessions WHERE id = ? AND student_id = ?', (session_id, user_id))
    session = cursor.fetchone()

    if not session:
        conn.close()
        flash('Session not found or access denied', 'error')
        return redirect(url_for('student_dashboard', user_id=user_id))

    subject = request.form.get('subject', '').strip()
    start_time = request.form.get('start_time', '').strip()
    notes = request.form.get('notes', '').strip()

    if not subject or not start_time:
        conn.close()
        flash('Subject and start time are required', 'error')
        return redirect(url_for('student_dashboard', user_id=user_id))

    try:
        datetime.fromisoformat(start_time.replace('T', ' '))
    except ValueError:
        conn.close()
        flash('Invalid date/time format', 'error')
        return redirect(url_for('student_dashboard', user_id=user_id))

    cursor.execute('''
        UPDATE StudySessions
        SET subject = ?, start_time = ?, notes = ?
        WHERE id = ? AND student_id = ?
    ''', (subject, start_time, notes, session_id, user_id))
    conn.commit()
    conn.close()

    flash('Study session updated successfully', 'success')
    return redirect(url_for('student_dashboard', user_id=user_id))


@app.route('/delete_session/<int:session_id>', methods=['POST'])
def delete_session(session_id):
    """Delete a study session (students only)."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))

    user = get_user_by_id(user_id)
    if not user or user['role'] != 'student':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM StudySessions WHERE id = ? AND student_id = ?', (session_id, user_id))
    if not cursor.fetchone():
        conn.close()
        flash('Session not found or access denied', 'error')
        return redirect(url_for('student_dashboard', user_id=user_id))

    cursor.execute('DELETE FROM StudySessions WHERE id = ? AND student_id = ?', (session_id, user_id))
    conn.commit()
    conn.close()

    flash('Study session deleted successfully', 'success')
    return redirect(url_for('student_dashboard', user_id=user_id))


@app.route('/mark_completed/<int:session_id>', methods=['POST'])
def mark_completed(session_id):
    """Toggle completion status of a study session (students only)."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))

    user = get_user_by_id(user_id)
    if not user or user['role'] != 'student':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM StudySessions WHERE id = ? AND student_id = ?', (session_id, user_id))
    session = cursor.fetchone()

    if not session:
        conn.close()
        flash('Session not found or access denied', 'error')
        return redirect(url_for('student_dashboard', user_id=user_id))

    new_status = 1 if session['completed'] == 0 else 0
    cursor.execute('''
        UPDATE StudySessions
        SET completed = ?
        WHERE id = ? AND student_id = ?
    ''', (new_status, session_id, user_id))
    conn.commit()
    conn.close()

    status_text = 'completed' if new_status == 1 else 'marked as pending'
    flash(f'Study session {status_text}', 'success')
    return redirect(url_for('student_dashboard', user_id=user_id))


# --- Study Units (Mentor) ---

@app.route('/add_unit', methods=['POST'])
def add_unit():
    """Add a new study unit/topic (mentors only)."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'mentor':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    subject = request.form.get('subject', '').strip()
    unit_name = request.form.get('unit_name', '').strip()
    topic_name = request.form.get('topic_name', '').strip()
    if not subject or not unit_name or not topic_name:
        flash('Subject, unit name, and topic name are required', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO StudyUnits (mentor_id, subject, unit_name, topic_name)
        VALUES (?, ?, ?, ?)
    ''', (user_id, subject, unit_name, topic_name))
    conn.commit()
    conn.close()
    flash('Unit added successfully', 'success')
    return redirect(url_for('mentor_dashboard', user_id=user_id))


@app.route('/edit_unit/<int:unit_id>', methods=['POST'])
def edit_unit(unit_id):
    """Edit a study unit (mentors only)."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'mentor':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM StudyUnits WHERE id = ? AND mentor_id = ?', (unit_id, user_id))
    if not cursor.fetchone():
        conn.close()
        flash('Unit not found', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))

    subject = request.form.get('subject', '').strip()
    unit_name = request.form.get('unit_name', '').strip()
    topic_name = request.form.get('topic_name', '').strip()
    if not subject or not unit_name or not topic_name:
        conn.close()
        flash('Subject, unit name, and topic name are required', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))

    cursor.execute('''
        UPDATE StudyUnits SET subject = ?, unit_name = ?, topic_name = ?
        WHERE id = ? AND mentor_id = ?
    ''', (subject, unit_name, topic_name, unit_id, user_id))
    conn.commit()
    conn.close()
    flash('Unit updated successfully', 'success')
    return redirect(url_for('mentor_dashboard', user_id=user_id))


@app.route('/edit_unit_page/<int:unit_id>')
def edit_unit_page(unit_id):
    """Show edit form for a unit (mentors only)."""
    user_id = request.args.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'mentor':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM StudyUnits WHERE id = ? AND mentor_id = ?', (unit_id, user_id))
    unit = cursor.fetchone()
    conn.close()
    if not unit:
        flash('Unit not found', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))
    return render_template('edit_unit.html', user=user, unit=unit)


@app.route('/set_exam/<int:unit_id>', methods=['POST'])
def set_exam(unit_id):
    """Set or update exam date for a unit (mentors only). Students cannot edit."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'mentor':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    exam_date = request.form.get('exam_date', '').strip()
    if not exam_date:
        flash('Exam date is required', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))

    try:
        datetime.fromisoformat(exam_date.replace('T', ' '))
    except ValueError:
        flash('Invalid date format', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT subject FROM StudyUnits WHERE id = ? AND mentor_id = ?', (unit_id, user_id))
    unit = cursor.fetchone()
    if not unit:
        conn.close()
        flash('Unit not found', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))
    subject = unit['subject']

    cursor.execute('SELECT id FROM Exams WHERE unit_id = ?', (unit_id,))
    if cursor.fetchone():
        cursor.execute('UPDATE Exams SET exam_date = ?, subject = ? WHERE unit_id = ? AND mentor_id = ?',
                       (exam_date, subject, unit_id, user_id))
    else:
        cursor.execute('INSERT INTO Exams (mentor_id, subject, unit_id, exam_date) VALUES (?, ?, ?, ?)',
                       (user_id, subject, unit_id, exam_date))
    conn.commit()
    conn.close()
    flash('Exam date saved', 'success')
    return redirect(url_for('mentor_dashboard', user_id=user_id))


@app.route('/delete_exam/<int:unit_id>', methods=['POST'])
def delete_exam(unit_id):
    """Delete exam date for a unit (mentors only)."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'mentor':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM Exams WHERE unit_id = ? AND mentor_id = ?', (unit_id, user_id))
    conn.commit()
    conn.close()
    flash('Exam date removed', 'success')
    return redirect(url_for('mentor_dashboard', user_id=user_id))


@app.route('/delete_unit/<int:unit_id>', methods=['POST'])
def delete_unit(unit_id):
    """Delete a study unit (mentors only). Cascades: delete tests first."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'mentor':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM StudyUnits WHERE id = ? AND mentor_id = ?', (unit_id, user_id))
    if not cursor.fetchone():
        conn.close()
        flash('Unit not found', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))

    cursor.execute('DELETE FROM Exams WHERE unit_id = ?', (unit_id,))
    cursor.execute('DELETE FROM StudySchedule WHERE unit_id = ?', (unit_id,))
    cursor.execute('DELETE FROM Options WHERE question_id IN (SELECT id FROM Questions WHERE test_id IN (SELECT id FROM Tests WHERE unit_id = ?))', (unit_id,))
    cursor.execute('DELETE FROM Questions WHERE test_id IN (SELECT id FROM Tests WHERE unit_id = ?)', (unit_id,))
    cursor.execute('DELETE FROM Tests WHERE unit_id = ?', (unit_id,))
    cursor.execute('DELETE FROM StudentProgress WHERE unit_id = ?', (unit_id,))
    cursor.execute('DELETE FROM StudyUnits WHERE id = ? AND mentor_id = ?', (unit_id, user_id))
    conn.commit()
    conn.close()
    flash('Unit deleted successfully', 'success')
    return redirect(url_for('mentor_dashboard', user_id=user_id))


# --- Tests (Mentor) ---


@app.route('/add_test_page/<int:unit_id>')
def add_test_page(unit_id):
    """Show add test form for a unit (mentors only)."""
    user_id = request.args.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'mentor':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM StudyUnits WHERE id = ? AND mentor_id = ?', (unit_id, user_id))
    unit = cursor.fetchone()
    conn.close()
    if not unit:
        flash('Unit not found', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))
    return render_template('add_test.html', user=user, unit=unit)


@app.route('/edit_test_page/<int:test_id>')
def edit_test_page(test_id):
    """Show edit form for a test (mentors only)."""
    user_id = request.args.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'mentor':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT t.*, u.subject, u.unit_name, u.topic_name
        FROM Tests t
        JOIN StudyUnits u ON t.unit_id = u.id
        WHERE t.id = ? AND t.mentor_id = ?
    ''', (test_id, user_id))
    test = cursor.fetchone()
    if not test:
        conn.close()
        flash('Test not found', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))
    cursor.execute('SELECT * FROM Questions WHERE test_id = ? ORDER BY id', (test_id,))
    questions = [dict(row) for row in cursor.fetchall()]
    for q in questions:
        if q['type'] == 'MCQ':
            cursor.execute('SELECT * FROM Options WHERE question_id = ? ORDER BY id', (q['id'],))
            q['options'] = [dict(row) for row in cursor.fetchall()]
        else:
            q['options'] = []
    conn.close()
    return render_template('edit_test.html', user=user, test=test, questions=questions)


def _parse_test_form(request_form):
    """
    Parse form data from Google Form-style test builder.
    Expects: test_title, question_count, and for each i in 0..question_count-1:
      question_i_text, question_i_type (MCQ|ShortAnswer),
      question_i_correct (index for MCQ), question_i_answer (for ShortAnswer),
      question_i_option_0, question_i_option_1, ... (for MCQ)
    Returns (test_title, questions_list) or (None, None) on error.
    """
    test_title = (request_form.get('test_title') or '').strip() or 'Test'
    try:
        count = int(request_form.get('question_count', 0))
    except ValueError:
        count = 0
    if count <= 0:
        return None, None
    questions = []
    for i in range(count):
        text = (request_form.get(f'question_{i}_text') or '').strip()
        qtype = (request_form.get(f'question_{i}_type') or 'MCQ').strip()
        if qtype not in ('MCQ', 'ShortAnswer'):
            qtype = 'MCQ'
        if not text:
            continue
        q = {'text': text, 'type': qtype}
        if qtype == 'MCQ':
            opts = []
            j = 0
            while True:
                opt = (request_form.get(f'question_{i}_option_{j}') or '').strip()
                if not opt:
                    break
                opts.append(opt)
                j += 1
            if not opts:
                continue
            correct_idx = int(request_form.get(f'question_{i}_correct', 0) or 0)
            if correct_idx < 0 or correct_idx >= len(opts):
                correct_idx = 0
            q['options'] = opts
            q['correct'] = correct_idx
        else:
            ans = (request_form.get(f'question_{i}_answer') or '').strip()
            q['answer'] = ans
        questions.append(q)
    if not questions:
        return None, None
    return test_title, questions


def _save_test_questions(cursor, test_id, questions):
    """Save questions and options to DB for a given test_id."""
    for q in questions:
        cursor.execute('INSERT INTO Questions (test_id, question_text, type, correct_answer) VALUES (?, ?, ?, ?)',
                       (test_id, q['text'], q['type'], q.get('answer')))
        qid = cursor.lastrowid
        if q['type'] == 'MCQ':
            for j, opt_text in enumerate(q.get('options', [])):
                is_correct = 1 if j == q.get('correct', 0) else 0
                cursor.execute('INSERT INTO Options (question_id, option_text, is_correct) VALUES (?, ?, ?)',
                               (qid, opt_text, is_correct))


@app.route('/add_test', methods=['POST'])
def add_test():
    """Add a test for a unit (mentors only). Form-based, no JSON."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'mentor':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    unit_id = request.form.get('unit_id')
    if not unit_id:
        flash('Unit is required', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))

    test_title, questions = _parse_test_form(request.form)
    if not questions:
        flash('Add at least one valid question', 'error')
        return redirect(url_for('add_test_page', unit_id=unit_id, user_id=user_id))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM StudyUnits WHERE id = ? AND mentor_id = ?', (unit_id, user_id))
    if not cursor.fetchone():
        conn.close()
        flash('Unit not found', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))

    cursor.execute('SELECT id FROM Tests WHERE unit_id = ?', (unit_id,))
    if cursor.fetchone():
        conn.close()
        flash('A test already exists for this unit', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))

    cursor.execute('INSERT INTO Tests (mentor_id, unit_id, test_title) VALUES (?, ?, ?)',
                   (user_id, unit_id, test_title))
    test_id = cursor.lastrowid
    _save_test_questions(cursor, test_id, questions)
    conn.commit()
    conn.close()
    flash('Test added successfully', 'success')
    return redirect(url_for('mentor_dashboard', user_id=user_id))


@app.route('/edit_test/<int:test_id>', methods=['POST'])
def edit_test(test_id):
    """Edit a test (mentors only). Replaces all questions."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'mentor':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    test_title, questions = _parse_test_form(request.form)
    if not questions:
        flash('Add at least one valid question', 'error')
        return redirect(url_for('edit_test_page', test_id=test_id, user_id=user_id))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Tests WHERE id = ? AND mentor_id = ?', (test_id, user_id))
    if not cursor.fetchone():
        conn.close()
        flash('Test not found', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))

    cursor.execute('UPDATE Tests SET test_title = ? WHERE id = ? AND mentor_id = ?', (test_title, test_id, user_id))
    cursor.execute('DELETE FROM Options WHERE question_id IN (SELECT id FROM Questions WHERE test_id = ?)', (test_id,))
    cursor.execute('DELETE FROM Questions WHERE test_id = ?', (test_id,))
    _save_test_questions(cursor, test_id, questions)
    conn.commit()
    conn.close()
    flash('Test updated successfully', 'success')
    return redirect(url_for('mentor_dashboard', user_id=user_id))


@app.route('/delete_test/<int:test_id>', methods=['POST'])
def delete_test(test_id):
    """Delete a test (mentors only)."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'mentor':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Tests WHERE id = ? AND mentor_id = ?', (test_id, user_id))
    if not cursor.fetchone():
        conn.close()
        flash('Test not found', 'error')
        return redirect(url_for('mentor_dashboard', user_id=user_id))

    cursor.execute('DELETE FROM Options WHERE question_id IN (SELECT id FROM Questions WHERE test_id = ?)', (test_id,))
    cursor.execute('DELETE FROM Questions WHERE test_id = ?', (test_id,))
    cursor.execute('DELETE FROM Tests WHERE id = ? AND mentor_id = ?', (test_id, user_id))
    conn.commit()
    conn.close()
    flash('Test deleted successfully', 'success')
    return redirect(url_for('mentor_dashboard', user_id=user_id))


# --- Student Progress & Tests ---

@app.route('/mark_schedule_complete/<int:unit_id>', methods=['POST'])
def mark_schedule_complete(unit_id):
    """Mark a suggested study session as completed (students only). Records in StudySchedule."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'student':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    mentor_id = user['mentor_id']
    if not mentor_id:
        flash('No mentor linked', 'error')
        return redirect(url_for('student_dashboard', user_id=user_id))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM StudyUnits WHERE id = ? AND mentor_id = ?', (unit_id, mentor_id))
    if not cursor.fetchone():
        conn.close()
        flash('Unit not found', 'error')
        return redirect(url_for('student_dashboard', user_id=user_id))

    # Use suggested_study_time from form or default
    suggested_time = request.form.get('suggested_study_time', datetime.now().strftime('%Y-%m-%dT%H:%M'))
    cursor.execute('SELECT id FROM StudySchedule WHERE student_id = ? AND unit_id = ?', (user_id, unit_id))
    if cursor.fetchone():
        cursor.execute('UPDATE StudySchedule SET completed = 1, suggested_study_time = ? WHERE student_id = ? AND unit_id = ?',
                       (suggested_time, user_id, unit_id))
    else:
        cursor.execute('INSERT INTO StudySchedule (student_id, unit_id, suggested_study_time, completed) VALUES (?, ?, ?, 1)',
                       (user_id, unit_id, suggested_time))
    conn.commit()
    conn.close()
    flash('Study session marked complete!', 'success')
    return redirect(url_for('student_dashboard', user_id=user_id))


@app.route('/mark_unit_complete/<int:unit_id>', methods=['POST'])
def mark_unit_complete(unit_id):
    """Mark a unit as completed (students only). Creates or updates StudentProgress."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'student':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    mentor_id = user['mentor_id']
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM StudyUnits WHERE id = ? AND mentor_id = ?', (unit_id, mentor_id))
    if not cursor.fetchone():
        conn.close()
        flash('Unit not found', 'error')
        return redirect(url_for('student_dashboard', user_id=user_id))

    cursor.execute('SELECT id FROM StudentProgress WHERE student_id = ? AND unit_id = ?', (user_id, unit_id))
    if cursor.fetchone():
        cursor.execute('UPDATE StudentProgress SET completed = 1 WHERE student_id = ? AND unit_id = ?', (user_id, unit_id))
    else:
        cursor.execute('''
            INSERT INTO StudentProgress (student_id, unit_id, completed, test_taken, test_score)
            VALUES (?, ?, 1, 0, NULL)
        ''', (user_id, unit_id))
    conn.commit()
    conn.close()
    flash('Unit marked as completed! Take the test when ready.', 'success')
    return redirect(url_for('student_dashboard', user_id=user_id))


@app.route('/take_test/<int:test_id>')
def take_test(test_id):
    """Show test form for student to take (GET). Google Form-style display."""
    user_id = request.args.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'student':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT t.*, u.subject, u.unit_name, u.topic_name
        FROM Tests t
        JOIN StudyUnits u ON t.unit_id = u.id
        WHERE t.id = ? AND u.mentor_id = ?
    ''', (test_id, user['mentor_id']))
    test = cursor.fetchone()
    if not test:
        conn.close()
        flash('Test not found', 'error')
        return redirect(url_for('student_dashboard', user_id=user_id))

    cursor.execute('SELECT * FROM Questions WHERE test_id = ? ORDER BY id', (test_id,))
    questions = [dict(row) for row in cursor.fetchall()]
    for q in questions:
        if q['type'] == 'MCQ':
            cursor.execute('SELECT * FROM Options WHERE question_id = ? ORDER BY id', (q['id'],))
            q['options'] = [dict(row) for row in cursor.fetchall()]
        else:
            q['options'] = []
    conn.close()
    return render_template('take_test.html', user=user, test=test, questions=questions)


@app.route('/submit_test/<int:test_id>', methods=['POST'])
def submit_test(test_id):
    """Submit test answers and store score (students only). Auto-grades using Questions/Options."""
    user_id = request.form.get('user_id')
    if not user_id:
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user or user['role'] != 'student':
        flash('Access denied', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT t.*, u.id as unit_id
        FROM Tests t
        JOIN StudyUnits u ON t.unit_id = u.id
        WHERE t.id = ? AND u.mentor_id = ?
    ''', (test_id, user['mentor_id']))
    test = cursor.fetchone()
    if not test:
        conn.close()
        flash('Test not found', 'error')
        return redirect(url_for('student_dashboard', user_id=user_id))

    cursor.execute('SELECT * FROM Questions WHERE test_id = ? ORDER BY id', (test_id,))
    questions = [dict(row) for row in cursor.fetchall()]
    correct = 0
    for q in questions:
        qid = q['id']
        ans = request.form.get(f'q{qid}', '').strip()
        if q['type'] == 'MCQ':
            cursor.execute('SELECT id FROM Options WHERE question_id = ? AND is_correct = 1 ORDER BY id LIMIT 1', (qid,))
            row = cursor.fetchone()
            correct_opt_id = row['id'] if row else None
            if correct_opt_id and str(ans) == str(correct_opt_id):
                correct += 1
        else:
            expected = (q.get('correct_answer') or '').strip().lower()
            if ans.lower() == expected:
                correct += 1

    total = len(questions)
    score = (correct / total * 100) if total > 0 else 0
    unit_id = test['unit_id']

    # Set difficulty_level from score: hard (<50%), medium (50-70%), easy (>70%)
    if score < 50:
        difficulty_level = 'hard'
    elif score < 70:
        difficulty_level = 'medium'
    else:
        difficulty_level = 'easy'

    cursor.execute('SELECT id FROM StudentProgress WHERE student_id = ? AND unit_id = ?', (user_id, unit_id))
    if cursor.fetchone():
        cursor.execute('''
            UPDATE StudentProgress SET test_taken = 1, test_score = ?, difficulty_level = ?
            WHERE student_id = ? AND unit_id = ?
        ''', (score, difficulty_level, user_id, unit_id))
    else:
        cursor.execute('''
            INSERT INTO StudentProgress (student_id, unit_id, completed, test_taken, test_score, difficulty_level)
            VALUES (?, ?, 1, 1, ?, ?)
        ''', (user_id, unit_id, score, difficulty_level))
    conn.commit()
    conn.close()

    flash(f'Test submitted! Score: {score:.1f}%', 'success')
    return redirect(url_for('student_dashboard', user_id=user_id))


if __name__ == '__main__':
    # Initialize database on startup (creates schema only if needed)
    try:
        init_db()
        logger.info('Database initialization complete.')
    except Exception as e:
        logger.error(f'Failed to initialize database: {e}')
    
    # Deployment configuration via environment variables
    host = os.getenv('FLASK_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_PORT', 5000))
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() in ('true', '1', 'yes')
    
    logger.info(f'Starting Schedulr Flask app on {host}:{port} (debug={debug_mode})')
    app.run(host=host, port=port, debug=debug_mode)
