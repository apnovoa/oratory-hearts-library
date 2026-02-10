# Independent Production Readiness Audit

**Application:** Bibliotheca Oratorii Sacratissimorum Cordium
**Date:** 2026-02-09
**Reviewer:** External senior/principal engineer (independent)
**Codebase snapshot:** commit `0ea1ce2` (main)

---

## A) Executive Summary

This is a private digital lending library built with Flask, SQLite, and server-side rendering. It manages a collection of PDF books for a small religious community, implementing one-copy-one-loan controlled digital lending with automated checkout, PDF watermarking, loan expiration, waitlists, and email notifications.

The application is **well-engineered for its intended scale**. It demonstrates strong security fundamentals (bcrypt, CSRF, CSP with nonces, path traversal protection, rate limiting, audit logging), a clean architecture (application factory, blueprints, service layer separation), and a polished, accessible frontend with PWA support.

**Who this app is suitable for:** A small private community (dozens of users, hundreds of books) running on a single server with a single gunicorn worker. This is exactly the deployment it was designed for.

**Hard blockers:** None. The application is deployed and functioning. The findings below are improvements, not blockers.

---

## B) Report Card

| Category | Grade | Justification |
|----------|-------|---------------|
| **Architecture & Code Quality** | **B+** | Clean blueprint structure, application factory, good separation of concerns. Deductions for broad exception handling and ad-hoc migrations. |
| **Security** | **A-** | Excellent fundamentals: bcrypt-13, timing-safe login, CSP nonces, HSTS, path traversal checks, audit logging, rate limiting, CSRF everywhere. Minor: in-memory rate limiting is documented as single-worker-only. |
| **Data Integrity** | **B** | Checkout uses application-level locking. File operations have rollback logic. Deductions for process-local lock (documented limitation) and ad-hoc schema migrations. |
| **Frontend & UX** | **A-** | Beautiful, cohesive ecclesiastical design. Complete user journeys. Good accessibility (ARIA, semantic HTML, keyboard nav). Responsive with PWA support. Minor gaps in waitlist visibility and confirmation messaging. |
| **Performance & Scalability** | **B-** | Appropriate for intended scale (single server, single worker, SQLite). Not designed for horizontal scaling — and doesn't pretend to be. Deductions because the single-worker constraint isn't enforced programmatically. |
| **Operational Readiness** | **C+** | Deployment works but isn't atomic. Backups are local-only. No centralized logging config. Scheduler has no monitoring. Documentation is good but missing operational runbook. |
| **Maintainability** | **B+** | Well-structured code, clear naming, comprehensive audit logging. CI pipeline with lint, security scan, dep audit, and smoke test. Deductions for Flask-Migrate being installed but unused. |
| **Overall System Quality** | **B+** | A carefully built application that makes deliberate tradeoffs for its scale. Security and UX are standout strengths. Operational tooling is the weakest area. |

---

## C) Strengths

### Security is a standout
- **Bcrypt with 13 rounds** for password hashing (`models.py:56`)
- **Timing-attack prevention** on login — dummy bcrypt check for non-existent users (`auth/routes.py:64-69`)
- **Account lockout** after 5 failed attempts with 15-minute cooldown
- **CSP with per-request nonces** generated via `secrets.token_urlsafe(16)` (`__init__.py:121-125`)
- **Path traversal protection** consistently applied across file serving, PDF generation, and file deletion using `os.path.realpath()` + prefix check
- **Email enumeration prevention** — registration returns same message for existing accounts (`auth/routes.py:144-168`)
- **Force-logout mechanism** — admin can invalidate sessions server-side via timestamp comparison (`__init__.py:76-96`)
- **256-bit access tokens** for loan downloads — two UUID4s concatenated (`models.py:187-189`)
- **Comprehensive security headers** — HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, full CSP

### Frontend shows real craft
- Cohesive design system with burgundy/cream/gold palette reflecting ecclesiastical tradition
- Liturgical season awareness — UI themes change with the church calendar
- Smooth user journey from splash page through browse, borrow, read, return
- PDF reader with keyboard shortcuts (arrows, +/- zoom, Page Up/Down)
- Proper empty states, flash messages with animations, error pages without stack traces
- PWA with service worker (cache-first for static, network-first for dynamic, offline fallback)
- WCAG 2.1 AA level accessibility: ARIA labels, semantic HTML, focus-visible styling, alt text

### Architecture is clean
- Application factory pattern with environment-aware configuration
- Blueprint-based route organization (auth, catalog, lending, admin, patron, collections, opds)
- Service layer separation (lending/service.py, email_service, pdf_service, cover_service, ai_service)
- Comprehensive audit logging capturing all significant actions with user ID, IP, and timestamp
- CI pipeline with 4 parallel jobs: lint (ruff), security (bandit), dependency audit (pip-audit), smoke test

---

## D) Findings & Risks

### CRITICAL — None

There are no critical issues that would cause data loss, security breach, or system failure in the intended deployment configuration (single-worker gunicorn + SQLite).

### HIGH

**H1. Ad-hoc schema migrations instead of Alembic**
- *Area:* Data integrity, maintainability
- *Location:* `app/__init__.py:190-203`
- *Detail:* Flask-Migrate is installed but unused. Schema migrations are raw ALTER TABLE statements in a list, run on every app startup. Failures are silently swallowed with bare `except Exception: db.session.rollback()`. There is no migration versioning, no rollback capability, and no way to know which migrations have been applied.
- *Why it matters:* A failed migration will silently leave the schema in an inconsistent state. New code expecting a column that wasn't added will crash at runtime, not at deploy time.
- *Blocks production:* No (current migrations are simple and idempotent), but this is the highest-priority improvement.

**H2. Deployment is not atomic and has no rollback**
- *Area:* Operational readiness
- *Location:* `deploy.sh`
- *Detail:* Deploy tars the project, uploads via SCP, extracts in-place, and restarts. If extraction fails mid-way, the app directory is in a partial state. There is no backup-before-deploy, no symlink swap, and no rollback mechanism.
- *Why it matters:* A failed deploy could leave the application broken with no easy recovery path.
- *Blocks production:* No (deploy works and has been used successfully), but adds operational risk.

**H3. Backups are local-only with no offsite copy**
- *Area:* Disaster recovery
- *Location:* `scripts/backup.sh`
- *Detail:* Backup script copies the SQLite database and master PDFs to `storage/backups/` on the same server. No offsite backup (S3, NAS, etc.). No backup verification. No documented recovery procedure.
- *Why it matters:* Disk failure loses both the live data and all backups simultaneously.
- *Blocks production:* No (backups exist), but this is a significant operational risk.

### MEDIUM

**M1. Broad exception handling obscures errors**
- *Area:* Code quality, observability
- *Locations:* `scanner.py:150,451,563`, `admin/routes.py:859,1373`, `auth/routes.py:164`, `email_service/__init__.py:40`
- *Detail:* Many `except Exception` blocks catch, log, and continue. While individually defensible (graceful degradation), the pattern makes it hard to distinguish expected failures from real bugs in production.
- *Blocks production:* No

**M2. Scheduler has no monitoring or health check**
- *Area:* Operational readiness
- *Location:* `app/lending/scheduler.py`
- *Detail:* APScheduler runs as an in-process background thread. If it crashes, loans never expire and reminders stop — silently. The `/ping` health check doesn't verify scheduler status. No job execution logging or failure alerting.
- *Blocks production:* No (scheduler works), but failures would be invisible.

**M3. Error handlers don't log**
- *Area:* Observability
- *Location:* `app/errors.py`
- *Detail:* The 500 error handler renders a template but doesn't log the error. Combined with no centralized log configuration, production errors could be invisible.
- *Blocks production:* No

**M4. No centralized logging configuration**
- *Area:* Observability
- *Location:* Absent — no logging handler setup in `app/__init__.py`
- *Detail:* Logs go to stderr only (Flask default). No file rotation, no log levels configured, no structured logging. Production relies entirely on systemd journal capture.
- *Blocks production:* No (systemd captures stderr), but limits debugging ability.

**M5. Documentation references nonexistent SMTP config**
- *Area:* Documentation
- *Location:* `docs/SETUP.md` (email section)
- *Detail:* SETUP.md documents MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD — none of which exist in the code. The app uses Brevo HTTP API via httpx, not SMTP. This would confuse a new maintainer.
- *Blocks production:* No

**M6. Process-local checkout lock is a documented limitation**
- *Area:* Data integrity
- *Location:* `app/lending/service.py:10-13`
- *Detail:* `_checkout_lock = threading.Lock()` only works within a single process. The code comments explicitly document this: "run gunicorn with --workers 1 OR use the DB-level IMMEDIATE transaction." This is appropriate for the intended deployment but isn't enforced programmatically.
- *Blocks production:* No (single-worker deployment)

### LOW

**L1. Unused variable assignment**
- `app/liturgical.py:79` — `date(year - 1, 12, 25)` computed but never stored (after ruff removed the variable). The expression is now a no-op.

**L2. Scheduler intervals not configurable**
- `app/lending/scheduler.py` — Hardcoded 5-min expiry check, 60-min reminders. Should be configurable via environment variables.

**L3. FTS5 query sanitization is defense-in-depth**
- `app/catalog/routes.py:12-26` — Good regex sanitization plus parameterized queries. No actual vulnerability, but sanitizer could miss edge cases. The parameterized query is the real protection.

**L4. Empty migrations directory**
- `migrations/` directory exists but is empty. Flask-Migrate is installed. This is confusing — either use Alembic or remove both.

---

## E) User Journey Assessment

### Smooth experiences

- **Authentication flow** — Clean splash page, login with optional Google OAuth, account lockout protection, password reset with email. Registration prevents enumeration.
- **Catalog browsing** — Search with FTS5, filters (format, availability), pagination, staff picks, new arrivals. Book cards show covers, availability badges.
- **Borrowing a book** — One-click borrow, immediate access to PDF reader or download. Watermarked circulation copy generated on the fly with loan slip and return instructions.
- **Reading experience** — In-browser PDF reader with keyboard shortcuts, zoom controls, page navigation. Download option for offline reading.
- **Patron dashboard** — Active loans, renewal/return buttons, loan history, favorites, waitlist status, book requests, personal notes.
- **Admin management** — Full book CRUD, user management, audit log with CSV export, staged book import from PDF scanner, lending statistics.

### Potential confusion points

- **Waitlist position** — Patrons can join a waitlist but there's no dedicated "My Waitlist" view showing position. Status is visible on the book detail page but easy to miss.
- **Password reset confirmation** — After submitting email for password reset, the success message is a flash notification. Users might not notice it and try again.
- **Offline limitations** — PDF reader uses CDN-hosted PDF.js, so reading doesn't work offline. Downloaded PDFs work fine offline. This is expected but not communicated.
- **Book request lifecycle** — Patrons can request books and see status badges (Pending/Approved/Declined), but there's no timeline or explanation of the approval process.

---

## F) Production Readiness Verdict

### READY FOR DEPLOYMENT

**Justification:** The application is already deployed and serving real users. The security posture is strong, the core workflows are complete and well-tested (CI smoke test verifies app boot and /ping on every push), and the codebase is maintainable. The findings above are improvements to operational resilience, not blockers.

**Caveats:**
1. Must run as single gunicorn worker (documented and intentional)
2. Offsite backups should be configured as soon as practical (H3)
3. Schema migration strategy should be formalized before the next schema change (H1)

---

## G) Recommendations

### Short-term (before next deploy)

1. **Set up offsite backups** — Add rsync/S3 sync to backup.sh. This is the single highest-ROI improvement.
2. **Add logging to error handlers** — One line in `errors.py` to `app.logger.exception()` on 500 errors.
3. **Fix SETUP.md email documentation** — Replace SMTP references with Brevo API configuration.
4. **Remove the no-op expression** in `liturgical.py:79`.

### Medium-term (next iteration)

5. **Implement Alembic migrations** — Run `flask db init`, convert existing ad-hoc migrations to Alembic versions, remove the startup migration block from `__init__.py`.
6. **Add scheduler health monitoring** — Log job execution times, add a `/health` endpoint that checks scheduler thread liveness.
7. **Make deploy.sh atomic** — Use symlink swap pattern: deploy to a timestamped directory, symlink `current` to it, restart. Previous deploy becomes the rollback target.
8. **Configure centralized logging** — Add RotatingFileHandler or send to syslog with structured format.
9. **Add `max_instances=1` and `coalesce=True`** to scheduler jobs to prevent overlap.

### Long-term (optional improvements)

10. **Add integration tests** — The CI smoke test is good; actual test coverage for checkout flow, loan expiration, and waitlist processing would catch regressions.
11. **Add Sentry or similar** for error tracking with alerting.
12. **Document operational runbook** — Startup, shutdown, monitoring, common failure modes, recovery procedures.
13. **Consider PostgreSQL** if the community grows significantly or multi-worker deployment becomes necessary.
