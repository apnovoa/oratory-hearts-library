# Architecture Overview

**Bibliotheca Oratorii Sacratissimorum Cordium**
Digital lending library built with Flask, SQLite, and server-side rendering.

---

## System Diagram

```
                            CLIENTS
           +------------------+------------------+
           |                  |                  |
       Browser            E-Reader           Mobile PWA
      (HTML/CSS/JS)      (OPDS/Atom)        (Service Worker)
           |                  |                  |
           +------------------+------------------+
                              |
                         [ gunicorn ]
                              |
                     +--------+--------+
                     |   Flask App     |
                     |  (app factory)  |
                     +--------+--------+
                              |
          +-------------------+-------------------+
          |          MIDDLEWARE / EXTENSIONS       |
          |  ProxyFix, CSRFProtect, Flask-Login,  |
          |  Flask-Limiter, Authlib (OAuth)        |
          +-------------------+-------------------+
                              |
     +----------+----------+--+--+----------+----------+---------+
     |          |          |     |          |          |         |
  auth_bp   catalog_bp  lending_bp  admin_bp  patron_bp  opds_bp  collections_bp
  /login    /catalog    /borrow     /admin    /patron   /opds    /collections
  /register /catalog/<> /read/<>    /admin/*  /patron/* /opds/*  /collections/<>
  /logout   /covers/<>  /download
  /reset-*  /policy     /waitlist
  /auth/*   /donate
     |          |          |           |          |
     +----------+----------+-----------+----------+
                              |
              +---------------+---------------+
              |        SERVICE LAYER          |
              +------+------+------+------+---+
              |      |      |      |      |
          Lending  PDF    Email  Cover    AI
          Service  Service Service Service Service
              |      |      |      |      |
              |   pikepdf  Brevo  Open    Claude
              |   reportlab HTTP  Library  API
              |      |    API     API
              +------+------+------+------+
                              |
              +---------------+---------------+
              |       DATA / STORAGE          |
              |                               |
              |   SQLite + FTS5               |
              |   (bibliotheca.db)            |
              |                               |
              |   Filesystem Storage:         |
              |     storage/masters/    PDFs  |
              |     storage/circulation/      |
              |     storage/covers/    imgs   |
              |     storage/staging/   import |
              |     storage/backups/          |
              +-------------------------------+

              +-------------------------------+
              |     BACKGROUND SCHEDULER      |
              |        (APScheduler)          |
              |                               |
              |  Every 5 min:  expire_loans   |
              |  Every 60 min: send_reminders |
              |  Mon 8 AM:     new_acq_digest |
              |  Daily 7 AM:   birthday_email |
              +-------------------------------+

              +-------------------------------+
              |     BULK IMPORT PIPELINE      |
              |       (daemon thread)         |
              |                               |
              |  Upload PDFs to staging/      |
              |         |                     |
              |    Scan (background thread)   |
              |    +--> pikepdf metadata      |
              |    +--> filename parsing       |
              |    +--> Claude AI extraction   |
              |    +--> Open Library lookup    |
              |    +--> cover fetch/generate   |
              |    +--> duplicate detection    |
              |         |                     |
              |    StagedBook records         |
              |         |                     |
              |    Admin review & approve     |
              |         |                     |
              |    Move to masters/ + Book    |
              +-------------------------------+
```

---

## Request Flow: Borrowing a Book

```
Patron clicks "Borrow"
        |
  POST /borrow/<public_id>
        |
  lending_bp.borrow()
        |
  +-----+-----+
  | Validation |  user.can_borrow? book.is_available? under loan limit?
  +-----+-----+
        |
  checkout_book()          [lending/service.py]
        |
  +-----+-----+
  | Lock       |  threading.Lock() -- process-local
  +-----+-----+
        |
  Create Loan record       access_token, due_at, snapshots
        |
  generate_circulation_copy()  [pdf_service]
        |
  +-----+-----+
  | pikepdf    |  cover page + watermarked content + end page
  +-----+-----+
        |
  Save to storage/circulation/
        |
  log_event("book_checkout")   [audit]
        |
  send_loan_email()            [email_service --> Brevo API]
        |
  Redirect to download page
```

---

## Data Model

```
User ──< Loan >── Book ──< Tag (M2M via book_tags)
  |                 |
  |──< Favorite >───|
  |──< BookNote >───|
  |──< WaitlistEntry >── Book
  |──< BookRequest
  |
  +-- AuditLog (user_id FK)

ReadingList ──< ReadingListItem >── Book

StagedBook (import staging, links to Book after approval)

SystemConfig (key-value settings store)
```

### Key Models

| Model | Purpose | Key Fields |
|-------|---------|------------|
| **User** | Patrons, librarians, admins | email, role, password_hash, google_id, failed_login_count, locked_until |
| **Book** | Library catalog items | title, author, isbn, master_filename, owned_copies, watermark_mode |
| **Loan** | Active/past book loans | access_token, due_at, circulation_filename, renewal_count, is_active |
| **WaitlistEntry** | Queue for unavailable books | user_id, book_id, notified_at, is_fulfilled |
| **Favorite** | Patron bookmarks | user_id, book_id |
| **BookNote** | Personal reading notes | user_id, book_id, content |
| **BookRequest** | Patron acquisition requests | title, author, status (pending/approved/dismissed) |
| **ReadingList** | Curated collections | name, is_public, is_featured, season |
| **StagedBook** | Import pipeline staging | metadata fields, confidence, status, scan_batch_id |
| **AuditLog** | Compliance logging | action, target_type, target_id, ip_address |
| **Tag** | Book categorization | name (unique) |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Flask 3.1 |
| Database | SQLite + FTS5 full-text search |
| ORM | SQLAlchemy 2.0 |
| Auth | Flask-Login + Authlib (Google OAuth) + bcrypt |
| PDF | pikepdf (manipulation) + ReportLab (generation) + PyMuPDF (text extraction) |
| Email | Brevo HTTP API via httpx |
| AI | Anthropic Claude API (metadata extraction) |
| Scheduler | APScheduler (background jobs) |
| Security | Flask-WTF (CSRF), Flask-Limiter, CSP nonces, bcrypt |
| Frontend | Jinja2 templates, vanilla JS, Cormorant Garamond + Inter fonts |
| PWA | Service Worker (cache-first statics, network-first HTML) |
| Images | Pillow (cover generation), Open Library API (cover fetch) |
| Server | gunicorn |

---

## Feature Writeups

### Authentication & Access Control

Standard email/password login with optional Google OAuth. Passwords are hashed with bcrypt (13 rounds). Failed login attempts trigger progressive account lockout (5 failures = 15-minute lock). Password reset uses time-limited signed tokens (1 hour). Sessions last 8 hours; remember-me cookies last 14 days. All auth routes are rate-limited at 5 requests/minute. Admins can force-logout any user by setting a timestamp that invalidates all earlier sessions.

### Book Catalog

Authenticated patrons browse the catalog with full-text search powered by SQLite FTS5. Results can be filtered by tag, language, and availability, and sorted by title, author, recency, or availability. The landing page shows a "New Arrivals" shelf and "Staff Picks" (featured books). Each book detail page shows metadata, availability status, expected return dates for checked-out copies, and the patron's personal notes and favorite status.

### Lending & Circulation

One-copy-one-loan model: each physical copy can only be lent to one patron at a time. On checkout, the system generates a watermarked PDF with a loan slip cover page, the book content with footer watermarks (borrower name + due date), and a return instructions page. Patrons access their book through an in-browser PDF.js reader or direct download, both gated by an unguessable access token. Loans default to 7 days with up to 2 renewals. Expired loans are automatically reclaimed every 5 minutes by the background scheduler, which also deletes the circulation PDF from disk.

Each loan expiration runs inside its own database savepoint so that a failure on one loan (missing file, email error) does not roll back other expirations in the same scheduler tick. Waitlist processing uses the same per-loan isolation.

### Waitlist

When all copies of a book are checked out, patrons can join a FIFO waitlist. When a copy becomes available (via return, expiration, or admin termination), the system automatically notifies the next person in line by email. Waitlist entries are marked as fulfilled once notified, allowing re-joining later.

### Patron Dashboard

Each patron has a dashboard showing active loans with due dates, recent loan history, and quick links to profile settings, favorites, reading history, and book requests. The profile page lets patrons update their display name, set a birthday (for birthday emails), and change their password.

### Favorites & Notes

Patrons can bookmark books as favorites (toggled via AJAX) and write personal reading notes on any book. Notes are visible only to the patron who wrote them and appear on the book detail page.

### Book Requests

Patrons can submit acquisition requests for books not yet in the library. Requests include a title, optional author, and optional reason. Admins review requests and mark them as approved or dismissed with optional notes.

### Reading Lists / Collections

Admins create curated reading lists with names, descriptions, and optional liturgical season tags. Lists can be public or featured. Each list contains ordered books with optional per-item notes. The public-facing collections page groups lists by liturgical season, featured status, and general availability.

### OPDS Feed

An OPDS (Open Publication Distribution System) Atom feed at `/opds/catalog.xml` allows e-reader apps like KOReader, Calibre, and Moon+ Reader to browse and discover the library's catalog. Books are served as paginated acquisition entries.

### Admin Dashboard

The admin panel at `/admin` provides library-wide statistics (total books, patrons, active loans, monthly trends, top-borrowed titles) and management tools for every entity: books, users, loans, reading lists, book requests, and audit logs. Admins can add/edit books, toggle visibility, block/unblock users, change roles, extend or terminate loans, and export audit logs as CSV. All admin POST routes are rate-limited at 30/minute as defense-in-depth.

### Bulk PDF Import

A multi-stage pipeline for importing books from PDF files. Admins upload PDFs to a staging directory, then trigger a background scan. The scanner extracts metadata from three sources: embedded PDF metadata (pikepdf), filename parsing (heuristic), and optionally the Claude AI API (text or vision-based extraction). It also queries Open Library for enrichment and fetches cover images. Each scanned PDF becomes a StagedBook record with a confidence score (high/medium/low). Admins review staged books, edit metadata, and approve (moves PDF to master storage + creates Book record) or dismiss. Bulk approve/dismiss and AI enrichment are available for batch operations.

The scanner acquires an `flock`-based file lock on the staging directory to prevent concurrent scans, even across process restarts or multiple gunicorn workers. Uploads are capped at `MAX_FILES_PER_UPLOAD` files and `MAX_PDF_FILE_SIZE_MB` per file.

### Cover Images

Cover images are sourced in priority order: Open Library by ISBN, Open Library by title/author search, or auto-generated with Pillow (white canvas with burgundy title, gold separator, author name, and the Sacred Hearts seal). Generated covers use the Cormorant Garamond font to match the library's branding.

### Email Notifications

Transactional emails are sent via the Brevo HTTP API for: loan confirmations, due-date reminders (2 days before), expiration notices, waitlist availability alerts, and password resets. Two scheduled digest emails run automatically: birthday greetings (daily at 7 AM for patrons with birthdays that day) and a weekly new acquisitions digest (Monday 8 AM listing books added in the past 7 days). All emails use branded HTML templates.

### PDF Watermarking

The PDF service generates personalized circulation copies using pikepdf and ReportLab. Each copy includes a cover page (loan slip with borrower details and due date), content pages with a footer watermark ("Loaned to [Name] -- Due [Date]"), and an end page with return instructions and library policy. Two watermark modes are available: "standard" (all pages) and "gentle" (cover and back only). The library's Cormorant Garamond font and burgundy color scheme are used throughout.

### AI Metadata Extraction

Optional Claude API integration for extracting book metadata from PDF content. The service extracts text from the first few pages via PyMuPDF and sends it to Claude with a structured prompt requesting title, author, year, ISBN, language, tags, and description. Falls back to vision mode (rendering pages as images) if text extraction yields nothing. Three model tiers are configurable: Haiku for quick metadata, Sonnet for deeper extraction.

### Audit Logging

Every significant action (login, logout, checkout, return, admin operations, password changes, etc.) is recorded in an AuditLog table with timestamp, user ID, action type, target entity, detail text, and IP address. Admins can browse, filter, and export audit logs as CSV from the admin panel.

### Background Scheduler

APScheduler runs four recurring jobs: loan expiration checks (every 5 minutes), due-date reminder emails (every 60 minutes), weekly new acquisitions digest (Monday 8 AM), and daily birthday greetings (7 AM). All jobs run in the same process as the Flask app using a background thread scheduler.

### PWA & Offline Support

The app is installable as a Progressive Web App with a service worker that caches static assets (CSS, JS, fonts, images) using a cache-first strategy and serves HTML pages network-first with an offline fallback. The manifest provides standalone display mode with the library's cream and maroon theme colors.

### Security Posture

Defense-in-depth across multiple layers: bcrypt password hashing, CSRF tokens on all forms, CSP with per-request nonces, security headers (X-Content-Type-Options, X-Frame-Options, HSTS in production), rate limiting on all sensitive endpoints, account lockout on brute force, path traversal protection on file operations, open-redirect prevention on login redirects, timing-attack equalization on login, and audit logging for compliance.

### Deployment

Single-script deployment via `deploy.sh`: verifies `FLASK_ENV=production` on the remote server, creates a tarball excluding dev artifacts, uploads via SCP, extracts on the server, updates the service worker cache version, restarts the systemd service, and runs a health check against `/ping`.
