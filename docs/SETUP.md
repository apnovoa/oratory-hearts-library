# Bibliotheca Oratorii Sacratissimorum Cordium — Setup & Operations Guide

## Overview

A private digital lending library for the Oratory of the Most Sacred Hearts, implementing
strict one-copy-one-loan controlled digital lending with automated checkout, PDF
watermarking, expiration, and email notifications.

---

## Quick Start

### 1. Prerequisites

- Python 3.11+ (tested on 3.13)
- A working SMTP email account (Gmail, Fastmail, etc.)

### 2. Install

```bash
cd "Oratory Hearts Library"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env` with your actual values:

| Variable | Purpose | Required |
|----------|---------|----------|
| `SECRET_KEY` | Flask session encryption. Generate with `python -c "import secrets; print(secrets.token_hex(32))"` | **Yes** |
| `ADMIN_EMAIL` | Email for the seed admin account | Yes |
| `ADMIN_PASSWORD` | Password for the seed admin account | Yes |
| `MAIL_SERVER` | SMTP server hostname | Yes for email |
| `MAIL_PORT` | SMTP port (587 for TLS) | Yes for email |
| `MAIL_USERNAME` | SMTP login | Yes for email |
| `MAIL_PASSWORD` | SMTP password or app-specific password | Yes for email |
| `MAIL_DEFAULT_SENDER` | From address on all emails | Yes for email |
| `LIBRARY_CONTACT_EMAIL` | Contact shown in policy and loan slips | Yes |
| `LIBRARY_DOMAIN` | Public URL of the library (e.g. `https://library.oratory.org`) | Yes |
| `DEFAULT_LOAN_DAYS` | Default loan period in days (default: 14) | No |
| `MAX_LOANS_PER_PATRON` | Max simultaneous loans per patron (default: 5) | No |
| `REMINDER_DAYS_BEFORE_DUE` | Days before due date to send reminder (default: 2) | No |

### 4. Run (Development)

```bash
source venv/bin/activate
python run.py
```

The app starts at `http://localhost:5000`. On first run, it creates the database
and seeds the admin account from your `.env` values.

### 5. Run (Production)

```bash
source venv/bin/activate
gunicorn -w 2 -b 0.0.0.0:8000 "app:create_app('production')"
```

Place behind a reverse proxy (nginx, Caddy) with HTTPS.

**Important**: Set `FLASK_ENV=production` and ensure `SECRET_KEY` is a strong random value.

---

## Architecture

```
Oratory Hearts Library/
├── app/                          # Flask application
│   ├── __init__.py               # App factory
│   ├── config.py                 # Configuration classes
│   ├── models.py                 # SQLAlchemy models (User, Book, Loan, etc.)
│   ├── errors.py                 # Error page handlers
│   ├── audit/                    # Audit logging
│   ├── auth/                     # Authentication (login, register, password reset)
│   ├── catalog/                  # Catalog browsing and search
│   ├── lending/                  # Lending engine (checkout, return, expiration)
│   │   ├── service.py            # Core business logic with atomic checkout
│   │   ├── routes.py             # HTTP endpoints
│   │   └── scheduler.py          # Background jobs (expiry, reminders)
│   ├── admin/                    # Admin dashboard
│   ├── patron/                   # Patron dashboard
│   ├── email_service/            # Email sending
│   ├── pdf_service/              # PDF watermarking (pikepdf + reportlab)
│   ├── templates/                # Jinja2 HTML templates
│   └── static/                   # CSS, JS, images
├── storage/
│   ├── masters/                  # Master PDF scans (NEVER served to patrons)
│   ├── circulation/              # Generated watermarked loan copies
│   ├── covers/                   # Book cover images
│   └── backups/                  # Database backups
├── run.py                        # Development entry point
├── requirements.txt              # Python dependencies
└── .env                          # Environment configuration (not in git)
```

---

## Roles

| Role | Capabilities |
|------|-------------|
| **Admin** | Full system access. Manages books, users, loans, configuration. |
| **Librarian** | Edits metadata, descriptions, tags. Cannot change ownership counts or system policies. |
| **Patron** | Browses catalog, borrows books, views own loans, returns early. |

The first admin is created automatically from `ADMIN_EMAIL`/`ADMIN_PASSWORD` on first run.
Additional users register themselves (or admin can change roles from the admin panel).

---

## Adding Books

1. Log in as Admin.
2. Go to **Admin → Books → Add Book**.
3. Fill in metadata (title, author, language, year, etc.).
4. Upload the **Master PDF** — this is the source file, never exposed to patrons.
5. Upload a **Cover Image** (optional but recommended).
6. Set **Owned Copies** (number of physical copies you own).
7. Choose **Watermark Mode**:
   - **Standard**: Footer watermark on every page.
   - **Gentle**: Watermark only first and last content pages (for children's books, etc.).
8. Save. The book appears in the catalog immediately.

---

## How Lending Works

1. Patron clicks **Borrow** on an available title.
2. System atomically:
   - Verifies availability (prevents race conditions via application-level lock)
   - Creates a loan record with due date
   - Generates a personalized PDF with cover page, watermarks, and end page
   - Sends loan confirmation email with download link
3. Patron downloads their unique circulation copy.
4. On due date, the loan expires automatically:
   - Download link stops working
   - Circulation PDF is deleted from disk
   - Inventory is released
   - Expiration notice sent to patron
5. If a waitlisted patron exists, they are notified that the book is now available.

---

## Background Jobs

The scheduler runs automatically when the app starts:

| Job | Interval | Purpose |
|-----|----------|---------|
| `expire_loans` | Every 5 minutes | Finds overdue loans, marks expired, cleans up files, processes waitlists |
| `send_reminders` | Every 60 minutes | Sends due-date reminders for loans expiring within `REMINDER_DAYS_BEFORE_DUE` days |

Disable with `SCHEDULER_ENABLED=false` if running in a multi-worker setup (to prevent duplicate jobs).

---

## Backups

### Database

The SQLite database is at `bibliotheca.db` in the project root. Back it up regularly:

```bash
cp bibliotheca.db storage/backups/bibliotheca-$(date +%Y%m%d).db
```

### Master Scans

The `storage/masters/` directory contains irreplaceable source files. Back this up
to an offsite location.

### What Doesn't Need Backup

- `storage/circulation/` — regeneratable from masters
- `venv/` — reinstallable from requirements.txt

---

## Security Notes

- **No anonymous access**: All pages except login, register, password reset, and the policy page require authentication.
- **Master scans**: Never served directly. Only watermarked circulation copies are downloadable.
- **Access tokens**: 64-character random hex strings. Not guessable.
- **Expired links**: Return 410 Gone. Cannot be revived.
- **Session security**: HTTPOnly cookies, SameSite=Lax, configurable secure flag.
- **CSRF protection**: All POST forms use CSRF tokens via Flask-WTF.
- **Force logout**: Admin can invalidate all sessions for any user.
- **Rate limiting**: Not included. Add via reverse proxy (nginx `limit_req`) if exposed to the internet.

---

## Concurrency & Atomicity

Checkout uses a `threading.Lock` to prevent race conditions on SQLite (which lacks
`SELECT FOR UPDATE`). This is safe for single-process deployments and for gunicorn
with `--preload` and a single worker. For multi-worker production, use:

```bash
gunicorn -w 1 -b 0.0.0.0:8000 "app:create_app('production')"
```

Or migrate to PostgreSQL and replace the lock with `with_for_update()` in `service.py`.

---

## Customization

### Loan Duration

- **Global default**: `DEFAULT_LOAN_DAYS` in `.env` (default: 14)
- **Per-title override**: Set in Admin → Edit Book → Loan Duration Override

### Branding

- Library names are set in `app/config.py` (`LIBRARY_NAME_LATIN`, `LIBRARY_NAME_ENGLISH`)
- Logo: place at `app/static/img/logo.png` and reference in `base.html`
- Colors: edit `app/static/css/style.css` — primary accent is `#800020` (burgundy)

### Email

All email templates are in `app/templates/email/`. They are standalone HTML files
that can be customized without affecting the rest of the application.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Failed to send email" in logs | Check SMTP settings in `.env`. For Gmail, use an App Password. |
| PDF generation fails | Verify the master PDF exists in `storage/masters/` and is not corrupted. |
| Login doesn't work | Check that the admin account was seeded. Look for the log line on first startup. |
| Scheduler runs twice | Set `SCHEDULER_ENABLED=false` and run scheduler as a separate process, or use 1 worker. |
| Database locked errors | Use a single gunicorn worker for SQLite, or migrate to PostgreSQL. |
