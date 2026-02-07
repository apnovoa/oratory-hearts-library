import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.urandom(32).hex()
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'bibliotheca.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 200 * 1024 * 1024  # 200 MB

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

    # Rate limiting
    RATELIMIT_DEFAULT = os.environ.get("RATELIMIT_DEFAULT", "200 per hour")
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    # Email
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get(
        "MAIL_DEFAULT_SENDER", "library@oratory.example.org"
    )

    # Library branding
    LIBRARY_NAME_LATIN = "Bibliotheca Oratorii Sacratissimorum Cordium"
    LIBRARY_NAME_ENGLISH = "Library of the Oratory of the Most Sacred Hearts"
    LIBRARY_CONTACT_EMAIL = os.environ.get(
        "LIBRARY_CONTACT_EMAIL", "library@oratory.example.org"
    )
    LIBRARY_DOMAIN = os.environ.get("LIBRARY_DOMAIN", "http://localhost:5000")

    # Bulk PDF import staging
    STAGING_STORAGE = os.environ.get(
        "STAGING_STORAGE", str(STORAGE_DIR / "staging")
    )

    # Google OAuth
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    # Session
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 3600 * 8  # 8 hours

    # Scheduler
    SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").lower() == "true"


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True

    @classmethod
    def init_app(cls, app):
        if not os.environ.get("SECRET_KEY"):
            raise RuntimeError("SECRET_KEY environment variable must be set in production")


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}
