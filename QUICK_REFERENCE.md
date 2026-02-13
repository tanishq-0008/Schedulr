# Schedulr Quick Reference - Deployment Guide

## 📋 One-Page Summary

| Aspect | Configuration |
|--------|---|
| **Framework** | Flask 3.0.0 |
| **Database** | SQLite (relative path: `schedulr.db`) |
| **Server Host** | `0.0.0.0` (all interfaces) |
| **Server Port** | `5000` (configurable via `FLASK_PORT`) |
| **Debug Mode** | `False` by default (configurable via `FLASK_DEBUG`) |
| **Static Files Path** | `static/` (referenced via `url_for()`) |
| **Templates Path** | `templates/` |
| **Config Management** | Environment variables (`.env`) |
| **Logging** | INFO level to stdout by default |

---

## 🚀 Quick Deploy (5 minutes)

### Step 1: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 2: Set Environment Variables
```bash
export FLASK_HOST=0.0.0.0
export FLASK_PORT=5000
export FLASK_DEBUG=False
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
```

### Step 3: Run Application
```bash
python app.py
```

**Done!** App runs on `http://0.0.0.0:5000`

---

## 🐳 Docker Deployment

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
ENV FLASK_HOST=0.0.0.0
ENV FLASK_PORT=5000
EXPOSE 5000
CMD ["python", "app.py"]
```

**Deploy:**
```bash
docker build -t schedulr .
docker run -p 5000:5000 -e SECRET_KEY=<generate-key> schedulr
```

---

## ⚙️ Environment Variables

| Variable | Default | Purpose | Example |
|----------|---------|---------|---------|
| `FLASK_HOST` | `0.0.0.0` | Server address | `127.0.0.1` or `0.0.0.0` |
| `FLASK_PORT` | `5000` | Server port | `8000`, `5000` |
| `FLASK_DEBUG` | `False` | Debug mode | `False`, `True` |
| `SECRET_KEY` | fallback | Session encryption | 64-character hex string |

**Generate SECRET_KEY:**
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 📦 Production Server (Gunicorn)

### Install Gunicorn
```bash
pip install gunicorn
```

### Run with Gunicorn (4 workers)
```bash
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### Systemd Service (Linux)
Create `/etc/systemd/system/schedulr.service`:
```ini
[Unit]
Description=Schedulr Flask App
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/schedulr
ExecStart=/usr/local/bin/gunicorn -w 4 -b 0.0.0.0:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable schedulr
sudo systemctl start schedulr
sudo systemctl status schedulr
```

---

## 🔐 Nginx Reverse Proxy (with SSL)

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static {
        alias /opt/schedulr/static;
        expires 30d;
    }
}
```

**Enable SSL with Let's Encrypt:**
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```

---

## 🗄️ Database Management

### Check Database Status
```bash
ls -lh schedulr.db
sqlite3 schedulr.db "SELECT COUNT(*) FROM Users;"
```

### Backup Database
```bash
cp schedulr.db schedulr.db.backup.$(date +%Y%m%d)
```

### Reset Database (WARNING: Deletes all data)
```bash
rm schedulr.db
python app.py  # Recreates from schema
```

---

## 🔍 Monitoring & Logs

### View Real-Time Logs (Gunicorn)
```bash
journalctl -u schedulr -f
# or
tail -f /var/log/schedulr.log
```

### Health Check
```bash
curl http://localhost:5000/
# Should redirect to /login
```

### Database Size
```bash
du -h schedulr.db
```

---

## 📐 Architecture

```
┌─────────────┐
│   Browser   │
└──────┬──────┘
       │ HTTP/HTTPS
       ▼
┌─────────────┐
│   Nginx     │ (Reverse Proxy)
│  (Optional) │
└──────┬──────┘
       │ HTTP
       ▼
┌─────────────────────┐
│  Gunicorn Worker(s) │ (4+ workers in production)
│                     │
│   Flask App         │
│  (app.py)           │
└──────┬──────────────┘
       │
       ▼
┌─────────────┐
│  SQLite DB  │
│  schedulr.db│
└─────────────┘
```

---

## 🚨 Common Issues

### Port Already in Use
```bash
# Kill process on port 5000
lsof -ti:5000 | xargs kill -9

# Or use different port
export FLASK_PORT=8000
python app.py
```

### Database Locked
Multiple processes accessing SQLite simultaneously. 
**Solution:** Upgrade to PostgreSQL for production.

### Static Files Not Loading
Ensure `url_for('static', filename='...')` is used in templates.
Check: `ls -la static/style.css`

### Permission Denied (Systemd)
```bash
sudo chown www-data:www-data /opt/schedulr -R
sudo chmod 755 /opt/schedulr
```

---

## 📊 Performance Tuning

### Gunicorn Workers Formula
```
workers = (2 × CPU cores) + 1
```

For 4-core machine:
```bash
gunicorn -w 9 -b 0.0.0.0:5000 app:app
```

### Worker Timeout
```bash
gunicorn -w 4 --timeout 30 -b 0.0.0.0:5000 app:app
```

### Connection Pooling (For PostgreSQL future upgrade)
```bash
pip install psycopg2-binary
```

---

## 🔐 Security Checklist

- [ ] Generate strong SECRET_KEY
- [ ] Set FLASK_DEBUG=False in production
- [ ] Use HTTPS (Nginx + SSL)
- [ ] Backup database regularly
- [ ] Monitor logs for errors
- [ ] Rotate SECRET_KEY monthly
- [ ] Use PostgreSQL for data > 100K records
- [ ] Add password hashing (bcrypt)

---

## 📚 Documentation Links

- **Full Deployment Guide:** `DEPLOYMENT.md`
- **Change Summary:** `CHANGES.md`
- **Verification Report:** `VERIFICATION.md`
- **Main README:** `README.md`
- **Environment Template:** `.env.example`

---

## 💡 Pro Tips

1. **Local Development:**
   ```bash
   export FLASK_DEBUG=True
   python app.py
   ```

2. **Database Inspection:**
   ```bash
   sqlite3 schedulr.db
   > .tables
   > SELECT * FROM Users LIMIT 5;
   ```

3. **Test Mentor Signup:**
   - Visit http://localhost:5000/signup
   - Select "Mentor" role
   - Get unique mentor code
   - Share code with students

4. **Monitor Gunicorn:**
   ```bash
   ps aux | grep gunicorn
   ```

5. **Backup Before Updates:**
   ```bash
   cp schedulr.db schedulr.db.backup
   git commit -am "Pre-deployment backup"
   ```

---

## 🆘 Getting Help

1. Check `DEPLOYMENT.md` for detailed instructions
2. Review `CHANGES.md` for what changed
3. See `VERIFICATION.md` for testing procedures
4. Check app logs for error messages
5. Verify environment variables are set

---

## 📈 Scaling Path

| Users | Database | Server | Workers |
|-------|----------|--------|---------|
| <100 | SQLite | VPS | 2-4 |
| 100-1000 | PostgreSQL | VPS | 4-8 |
| 1000-10K | PostgreSQL | AWS | 8-16 |
| 10K+ | PostgreSQL | Kubernetes | 16+ |

---

**Ready to deploy? Start with the 5-minute quick deploy above!** 🚀
