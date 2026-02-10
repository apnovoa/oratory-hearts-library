import logging
import os
import secrets
from datetime import UTC
from logging.handlers import RotatingFileHandler
from pathlib import Path

from authlib.integrations.flask_client import OAuth
from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate, upgrade
from flask_wtf.csrf import CSRFProtect

from .config import config_by_name
from .models import db

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to access the library."
login_manager.login_message_category = "info"
login_manager.session_protection = "strong"

# In-memory storage; counters reset on process restart. Acceptable for
# single-worker SQLite deployments. For multi-worker setups use Redis storage.
limiter = Limiter(key_func=get_remote_address)
migrate = Migrate()
csrf = CSRFProtect()
oauth = OAuth()


def create_app(config_name=None):
    # Load .env so gunicorn (production) picks up env vars too
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")

    app = Flask(__name__)
    config_cls = config_by_name.get(config_name, config_by_name["development"])
    app.config.from_object(config_cls)
    if hasattr(config_cls, "init_app"):
        config_cls.init_app(app)

    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Configure logging
    _configure_logging(app)

    # Ensure storage directories exist
    for d in ("MASTER_STORAGE", "CIRCULATION_STORAGE", "COVER_STORAGE", "BACKUP_STORAGE", "STAGING_STORAGE"):
        Path(app.config[d]).mkdir(parents=True, exist_ok=True)

    # Init extensions
    db.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    oauth.init_app(app)

    # Register Google OAuth (only if credentials are configured)
    if app.config.get("GOOGLE_CLIENT_ID"):
        oauth.register(
            name="google",
            client_id=app.config["GOOGLE_CLIENT_ID"],
            client_secret=app.config["GOOGLE_CLIENT_SECRET"],
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    # User loader
    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        user = db.session.get(User, int(user_id))
        if user and user.force_logout_before:
            from datetime import datetime

            from flask import session

            login_time = session.get("login_time")
            if not login_time:
                return None  # No login_time = stale session
            if login_time:
                lt = datetime.fromisoformat(login_time)
                flo = user.force_logout_before
                # Ensure both are aware or both naive for comparison
                if lt.tzinfo is None:
                    lt = lt.replace(tzinfo=UTC)
                if flo.tzinfo is None:
                    flo = flo.replace(tzinfo=UTC)
                if lt < flo:
                    return None
        return user

    # Register blueprints
    from .admin.routes import admin_bp
    from .auth.routes import auth_bp
    from .catalog.routes import catalog_bp
    from .collections.routes import collections_bp
    from .lending.routes import lending_bp
    from .opds.routes import opds_bp
    from .patron.routes import patron_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(lending_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(patron_bp, url_prefix="/patron")
    app.register_blueprint(opds_bp)
    app.register_blueprint(collections_bp)

    # Register error handlers
    from .errors import register_error_handlers

    register_error_handlers(app)

    # Generate a per-request CSP nonce for inline scripts that need template vars
    @app.before_request
    def generate_csp_nonce():
        from flask import g

        g.csp_nonce = secrets.token_urlsafe(16)

    # Security headers
    @app.after_request
    def set_security_headers(response):
        from flask import g

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if not app.debug:
            nonce = getattr(g, "csp_nonce", "")
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                f"script-src 'self' 'nonce-{nonce}'; "
                "worker-src 'self' blob:; "
                f"style-src 'self' 'nonce-{nonce}' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'self'; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'"
            )
        return response

    # Register template context
    @app.context_processor
    def inject_library_branding():
        from flask import g

        return {
            "library_name_latin": app.config["LIBRARY_NAME_LATIN"],
            "library_name_english": app.config["LIBRARY_NAME_ENGLISH"],
            "library_contact_email": app.config["LIBRARY_CONTACT_EMAIL"],
            "csp_nonce": getattr(g, "csp_nonce", ""),
        }

    # Start scheduler for loan expiration
    if app.config.get("SCHEDULER_ENABLED"):
        from .lending.scheduler import init_scheduler

        init_scheduler(app)

    # Health check endpoints
    @app.route("/ping")
    def ping():
        from datetime import datetime

        return {
            "status": "ok",
            "timestamp": datetime.now(UTC).isoformat(),
        }, 200

    @app.route("/health")
    def health():
        from datetime import datetime

        result = {"timestamp": datetime.now(UTC).isoformat()}

        # Check scheduler liveness
        scheduler = getattr(app, "scheduler", None)
        if scheduler is not None:
            running = scheduler.running
            jobs = []
            for job in scheduler.get_jobs():
                job_info = {"id": job.id, "next_run": job.next_run_time.isoformat() if job.next_run_time else None}
                jobs.append(job_info)
            result["scheduler"] = {"running": running, "jobs": jobs}
        else:
            result["scheduler"] = {"running": False, "reason": "disabled"}

        # Check database connectivity
        try:
            db.session.execute(db.text("SELECT 1"))
            result["database"] = {"status": "ok"}
        except Exception:
            app.logger.exception("Health check database probe failed.")
            result["database"] = {"status": "error", "error": "unavailable"}

        scheduler_ok = scheduler is None or scheduler.running
        db_ok = result.get("database", {}).get("status") == "ok"
        all_ok = scheduler_ok and db_ok
        result["status"] = "ok" if all_ok else "degraded"
        return result, 200 if all_ok else 503

    # Apply pending Alembic migrations and seed admin on first run
    with app.app_context():
        upgrade()

        _seed_admin_if_needed(app)

        # Create FTS5 virtual table for full-text search on books
        try:
            db.session.execute(
                db.text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS books_fts "
                    "USING fts5(title, author, description, content=books, content_rowid=id)"
                )
            )
            # Triggers to keep FTS index in sync with books table
            db.session.execute(
                db.text("""
                CREATE TRIGGER IF NOT EXISTS books_ai AFTER INSERT ON books BEGIN
                    INSERT INTO books_fts(rowid, title, author, description)
                    VALUES (new.id, new.title, new.author, new.description);
                END
            """)
            )
            db.session.execute(
                db.text("""
                CREATE TRIGGER IF NOT EXISTS books_ad AFTER DELETE ON books BEGIN
                    INSERT INTO books_fts(books_fts, rowid, title, author, description)
                    VALUES ('delete', old.id, old.title, old.author, old.description);
                END
            """)
            )
            db.session.execute(
                db.text("""
                CREATE TRIGGER IF NOT EXISTS books_au AFTER UPDATE ON books BEGIN
                    INSERT INTO books_fts(books_fts, rowid, title, author, description)
                    VALUES ('delete', old.id, old.title, old.author, old.description);
                    INSERT INTO books_fts(rowid, title, author, description)
                    VALUES (new.id, new.title, new.author, new.description);
                END
            """)
            )
            # Rebuild FTS index from existing data
            db.session.execute(db.text("INSERT OR IGNORE INTO books_fts(books_fts) VALUES('rebuild')"))
            db.session.commit()
        except Exception:
            app.logger.warning("FTS5 setup skipped (may not be supported by this SQLite build)")
            db.session.rollback()

    return app


def _configure_logging(app):
    """Set up file-based logging with rotation for production."""
    if app.debug:
        return

    log_dir = Path(app.root_path).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    file_handler = RotatingFileHandler(
        log_dir / "bibliotheca.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)


def _seed_admin_if_needed(app):
    import secrets

    from .models import User

    admin = User.query.filter_by(role="admin").first()
    if admin is None:
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@oratory.example.org")
        admin_password = os.environ.get("ADMIN_PASSWORD")
        generated = False
        if not admin_password:
            admin_password = secrets.token_urlsafe(16)
            generated = True
        admin = User(
            email=admin_email,
            display_name="Administrator",
            role="admin",
        )
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()
        app.logger.info(f"Default admin account created: {admin_email}")
        if generated:
            app.logger.warning(
                "ADMIN_PASSWORD not set -- a random password was generated. "
                "Set ADMIN_PASSWORD env var before deploying."
            )
            # Write to a temporary file instead of stdout
            pw_file = Path(app.instance_path) / ".admin_password"
            pw_file.parent.mkdir(parents=True, exist_ok=True)
            pw_file.write_text(f"Email:    {admin_email}\nPassword: {admin_password}\n")
            pw_file.chmod(0o600)
            app.logger.info("Generated admin credentials written to %s", pw_file)
