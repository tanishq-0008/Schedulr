# Schedulr - Deployment Guide

## Overview
Schedulr is a Flask-based smart study planner application designed to be deployment-ready for public hosting. This guide explains how to deploy the application to any public server.

## Prerequisites
- Python 3.7+
- pip (Python package manager)
- A Unix-like OS (Linux, macOS) or Windows with PowerShell

## Quick Start Deployment

### 1. Clone or Upload Project
Ensure the project structure is as follows:
```
schedulr/
  app.py
  requirements.txt
  templates/
  static/
  schedulr.db  (created automatically on first run)
```

### 2. Install Dependencies
Navigate to the project directory and install required packages:
```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Create a `.env` file in the project root (copy from `.env.example`):
```bash
cp .env.example .env
```

Edit `.env` and set your configuration:
```bash
export FLASK_HOST=0.0.0.0
export FLASK_PORT=5000
export FLASK_DEBUG=False
export SECRET_KEY=your-secret-key-here
```

**Important:** Generate a strong SECRET_KEY for production:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 4. Load Environment Variables
**On Linux/macOS:**
```bash
source .env
```

**On Windows (PowerShell):**
```powershell
Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*export\s+(.+?)=(.+)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
    }
}
```

Or use python-dotenv (optional):
```bash
pip install python-dotenv
```

Then create a `load_env.py` helper or use `python-dotenv` in your deployment script.

### 5. Run the Application
```bash
python app.py
```

The app will:
- Automatically initialize the database if it doesn't exist (`schedulr.db`)
- Listen on `0.0.0.0:5000` by default (or your configured host/port)
- Log startup information to the console

Access the app at `http://localhost:5000` (or your server's IP/domain).

---

## Production Deployment

### Option A: Using Gunicorn (Recommended)

1. **Install Gunicorn:**
   ```bash
   pip install gunicorn
   ```

2. **Run with Gunicorn:**
   ```bash
   gunicorn -w 4 -b 0.0.0.0:5000 app:app
   ```

   Parameters:
   - `-w 4`: 4 worker processes
   - `-b 0.0.0.0:5000`: Bind to all interfaces on port 5000
   - `app:app`: Flask application module

3. **Optional: Use a Systemd Service**
   Create `/etc/systemd/system/schedulr.service`:
   ```ini
   [Unit]
   Description=Schedulr Flask App
   After=network.target

   [Service]
   User=www-data
   WorkingDirectory=/path/to/schedulr
   Environment="PATH=/path/to/schedulr/.venv/bin"
   ExecStart=/path/to/schedulr/.venv/bin/gunicorn -w 4 -b 0.0.0.0:5000 app:app
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

   Enable and start:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable schedulr
   sudo systemctl start schedulr
   ```

### Option B: Using uWSGI

1. **Install uWSGI:**
   ```bash
   pip install uwsgi
   ```

2. **Create `wsgi.ini`:**
   ```ini
   [uwsgi]
   module = app:app
   master = true
   processes = 4
   socket = /tmp/schedulr.sock
   chmod-socket = 666
   vacuum = true
   die-on-term = true
   ```

3. **Run:**
   ```bash
   uwsgi --ini wsgi.ini
   ```

### Option C: Docker Deployment

Create a `Dockerfile`:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV FLASK_HOST=0.0.0.0
ENV FLASK_PORT=5000
ENV FLASK_DEBUG=False

EXPOSE 5000

CMD ["python", "app.py"]
```

Build and run:
```bash
docker build -t schedulr .
docker run -p 5000:5000 -e SECRET_KEY=your-secret-key schedulr
```

---

## Reverse Proxy Setup (Nginx)

For production, use Nginx as a reverse proxy:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static {
        alias /path/to/schedulr/static;
        expires 30d;
    }
}
```

Enable SSL with Let's Encrypt:
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

---

## Database Management

### Initial Setup
- The database is automatically created on first run as `schedulr.db`
- No manual database initialization needed
- Relative path ensures it works on any server

### Backup
```bash
cp schedulr.db schedulr.db.backup
```

### Reset (if needed)
Remove the database file to start fresh:
```bash
rm schedulr.db
```
The app will recreate it on the next run.

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_HOST` | `0.0.0.0` | Server host (0.0.0.0 = all interfaces) |
| `FLASK_PORT` | `5000` | Server port |
| `FLASK_DEBUG` | `False` | Debug mode (False for production) |
| `SECRET_KEY` | (random fallback) | Flask session secret key |

---

## Security Best Practices

1. **Generate a Strong SECRET_KEY:**
   ```bash
   python -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
   ```

2. **Use HTTPS in Production:**
   - Deploy behind a reverse proxy (Nginx) with SSL/TLS
   - Use Let's Encrypt for free certificates

3. **Set FLASK_DEBUG=False:**
   - Ensure debug mode is disabled in production

4. **Protect Your .env File:**
   - Add `.env` to `.gitignore`
   - Restrict file permissions: `chmod 600 .env`

5. **Use Strong Passwords:**
   - Ensure users create strong account passwords

6. **Regular Backups:**
   - Backup the `schedulr.db` file regularly

---

## Troubleshooting

### Database Issues
- **"database is locked"**: SQLite limitation with concurrent access. Switch to PostgreSQL for high traffic.
- **Database not created**: Ensure `app.py` is run at least once.

### Connection Issues
- **"Connection refused"**: Check if the app is running on the correct port.
- **"Host not available"**: Verify FLASK_HOST is set to `0.0.0.0` for external access.

### Logging
- Check app logs for detailed error messages during startup
- Logs are printed to stdout by default

### Static Files Not Loading
- Verify `static/style.css` exists
- Check that `url_for()` is used in all templates (it is by default)

---

## Performance Optimization

1. **Use Multiple Workers:**
   ```bash
   gunicorn -w 8 -b 0.0.0.0:5000 app:app
   ```

2. **Add Caching:**
   ```bash
   pip install Flask-Caching
   ```

3. **Switch to PostgreSQL:**
   For production with many concurrent users, replace SQLite with PostgreSQL.

---

## Monitoring & Maintenance

1. **Health Check:**
   ```bash
   curl http://localhost:5000/
   ```
   Should redirect to login page.

2. **Monitor Logs:**
   ```bash
   tail -f app.log  # if logging to file
   ```

3. **Database Size:**
   ```bash
   ls -lh schedulr.db
   ```

---

## Support

For issues or questions:
1. Check the app logs for error messages
2. Verify all environment variables are set correctly
3. Ensure Python 3.7+ is installed
4. Test with `FLASK_DEBUG=True` locally to enable detailed error pages

---

**Schedulr is now deployment-ready!** Follow these steps on your public hosting provider to get your app live.
