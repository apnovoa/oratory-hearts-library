import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.urandom(32).hex()
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'bibliotheca.db'}")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 200 * 1024 * 1024  # 200 MB
    MAX_FILES_PER_UPLOAD = int(os.environ.get("MAX_FILES_PER_UPLOAD", "20"))
    MAX_PDF_FILE_SIZE = int(os.environ.get("MAX_PDF_FILE_SIZE_MB", "25")) * 1024 * 1024
    MAX_COVER_FILE_SIZE = int(os.environ.get("MAX_COVER_FILE_SIZE_MB", "10")) * 1024 * 1024

    # Storage paths
    MASTER_STORAGE = str(STORAGE_DIR / "masters")
    CIRCULATION_STORAGE = str(STORAGE_DIR / "circulation")
    COVER_STORAGE = str(STORAGE_DIR / "covers")
    BACKUP_STORAGE = str(STORAGE_DIR / "backups")

    # Lending defaults
    DEFAULT_LOAN_DAYS = int(os.environ.get("DEFAULT_LOAN_DAYS", "7"))
    MAX_LOANS_PER_PATRON = int(os.environ.get("MAX_LOANS_PER_PATRON", "5"))
    REMINDER_DAYS_BEFORE_DUE = int(os.environ.get("REMINDER_DAYS_BEFORE_DUE", "2"))
    MAX_RENEWALS = int(os.environ.get("MAX_RENEWALS", "2"))

    # Security
    MAX_FAILED_LOGINS = int(os.environ.get("MAX_FAILED_LOGINS", "5"))
    ACCOUNT_LOCKOUT_MINUTES = int(os.environ.get("ACCOUNT_LOCKOUT_MINUTES", "15"))
    REGISTRATION_ENABLED = os.environ.get("REGISTRATION_ENABLED", "true").lower() == "true"
    TRUST_PROXY = os.environ.get("TRUST_PROXY", "false").lower() == "true"

    # Rate limiting
    RATELIMIT_DEFAULT = os.environ.get("RATELIMIT_DEFAULT", "200 per hour")
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    # Email (Brevo HTTP API)
    BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "library@oratory.example.org")
    MAIL_DEFAULT_SENDER_NAME = os.environ.get("MAIL_DEFAULT_SENDER_NAME", "Custos Oratorii")

    # Library branding
    LIBRARY_NAME_LATIN = "Bibliotheca Oratorii Sacratissimorum Cordium"
    LIBRARY_NAME_ENGLISH = "Library of the Oratory of the Most Sacred Hearts"
    LIBRARY_CONTACT_EMAIL = os.environ.get("LIBRARY_CONTACT_EMAIL", "library@oratory.example.org")
    LIBRARY_DOMAIN = os.environ.get("LIBRARY_DOMAIN", "http://localhost:8080")

    # Donation methods (leave blank to hide from donate page)
    DONATE_PAYPAL_URL = os.environ.get("DONATE_PAYPAL_URL", "")
    DONATE_ZELLE_ADDRESS = os.environ.get("DONATE_ZELLE_ADDRESS", "")
    DONATE_BTC_ADDRESS = os.environ.get("DONATE_BTC_ADDRESS", "")
    DONATE_ETH_ADDRESS = os.environ.get("DONATE_ETH_ADDRESS", "")
    DONATE_XMR_ADDRESS = os.environ.get("DONATE_XMR_ADDRESS", "")
    DONATE_LTC_ADDRESS = os.environ.get("DONATE_LTC_ADDRESS", "")
    DONATE_MAILING_ADDRESS = os.environ.get(
        "DONATE_MAILING_ADDRESS",
        "Library of the Oratory of the Most Sacred Hearts\nPO Box 431521\nMiami, FL 33243-1521",
    )

    # Bulk PDF import staging
    STAGING_STORAGE = os.environ.get("STAGING_STORAGE", str(STORAGE_DIR / "staging"))

    # AI metadata extraction (Claude API)
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    AI_EXTRACTION_ENABLED = os.environ.get("AI_EXTRACTION_ENABLED", "false").lower() == "true"
    AI_EXTRACTION_TIER = os.environ.get("AI_EXTRACTION_TIER", "tier2")
    AI_MODEL_TIER1 = "claude-haiku-4-5-20251001"
    AI_MODEL_TIER2 = "claude-haiku-4-5-20251001"
    AI_MODEL_TIER3 = "claude-sonnet-4-5-20250929"
    AI_MAX_PAGES_METADATA = 3
    AI_MAX_PAGES_DEEP = int(os.environ.get("AI_MAX_PAGES_DEEP", "9999"))
    AI_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("AI_REQUEST_TIMEOUT_SECONDS", "30"))

    # Google OAuth
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    # Session
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 3600 * 8  # 8 hours
    REMEMBER_COOKIE_DURATION = 3600 * 24 * 14  # 14 days (down from 365-day default)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = False  # overridden in production
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # Scheduler
    SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").lower() == "true"
    SCHEDULER_EXPIRY_INTERVAL_MINUTES = int(os.environ.get("SCHEDULER_EXPIRY_INTERVAL_MINUTES", "5"))
    SCHEDULER_REMINDER_INTERVAL_MINUTES = int(os.environ.get("SCHEDULER_REMINDER_INTERVAL_MINUTES", "60"))
    SCHEDULER_MAX_CONSECUTIVE_FAILURES = int(os.environ.get("SCHEDULER_MAX_CONSECUTIVE_FAILURES", "3"))

    # Scanner
    SCAN_FILE_TIMEOUT_SECONDS = int(os.environ.get("SCAN_FILE_TIMEOUT_SECONDS", "300"))


class DevelopmentConfig(Config):
    DEBUG = True

    @classmethod
    def init_app(cls, app):
        if not os.environ.get("SECRET_KEY"):
            app.logger.warning("SECRET_KEY not set — using an ephemeral key. Sessions will not survive restarts.")


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    TRUST_PROXY = os.environ.get("TRUST_PROXY", "true").lower() == "true"

    @classmethod
    def init_app(cls, app):
        secret_key = os.environ.get("SECRET_KEY", "").strip()
        if not secret_key:
            raise RuntimeError("SECRET_KEY environment variable must be set in production")
        if len(secret_key) < 32:
            raise RuntimeError(
                "SECRET_KEY is too short for production (minimum 32 characters). "
                'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
            )
        lowered_secret = secret_key.lower()
        weak_markers = ("changeme", "change-this", "replace", "secret", "example", "default")
        if any(marker in lowered_secret for marker in weak_markers):
            raise RuntimeError(
                "SECRET_KEY appears to be a placeholder and is not allowed in production. "
                'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
            )

        library_domain = os.environ.get("LIBRARY_DOMAIN", "").strip()
        parsed_domain = urlparse(library_domain)
        if parsed_domain.scheme != "https" or not parsed_domain.netloc:
            raise RuntimeError(
                "LIBRARY_DOMAIN must be set to a valid https:// URL in production (e.g. https://library.oratory.org)."
            )

        # Guard against multi-worker deployments: this app runs an in-process
        # APScheduler and in-memory limiter state; >1 worker would duplicate
        # scheduler jobs and split rate-limit counters. PaaS platforms often
        # set WEB_CONCURRENCY automatically.
        web_concurrency = os.environ.get("WEB_CONCURRENCY")
        if web_concurrency:
            try:
                worker_count = int(web_concurrency)
            except ValueError as exc:
                raise RuntimeError("WEB_CONCURRENCY must be an integer when set.") from exc
            if worker_count <= 0:
                raise RuntimeError("WEB_CONCURRENCY must be at least 1 when set.")
        else:
            worker_count = 1

        if worker_count > 1:
            raise RuntimeError(
                f"WEB_CONCURRENCY is set to {web_concurrency} but this application "
                "requires a single worker (in-process scheduler + in-memory rate limiting). "
                "Set WEB_CONCURRENCY=1 or remove it."
            )


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    SCHEDULER_ENABLED = False
    BREVO_API_KEY = ""
    SERVER_NAME = "localhost"
    SECRET_KEY = "testing-secret-key"


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
