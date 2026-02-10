import uuid
from datetime import UTC, datetime

import bcrypt
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def _utcnow():
    return datetime.now(UTC)


def _uuid():
    return uuid.uuid4().hex


# ── Association tables ──────────────────────────────────────────────

book_tags = db.Table(
    "book_tags",
    db.Column("book_id", db.Integer, db.ForeignKey("books.id", ondelete="CASCADE"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


# ── User ────────────────────────────────────────────────────────────


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), unique=True, nullable=False, default=_uuid)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(255), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="patron")  # admin, librarian, patron
    is_active_account = db.Column(db.Boolean, nullable=False, default=True)
    is_blocked = db.Column(db.Boolean, nullable=False, default=False)
    block_reason = db.Column(db.Text, nullable=True)
    failed_login_count = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    birth_month = db.Column(db.Integer, nullable=True)  # 1-12
    birth_day = db.Column(db.Integer, nullable=True)  # 1-31
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)
    force_logout_before = db.Column(db.DateTime, nullable=True)
    password_changed_at = db.Column(db.DateTime, nullable=True)
    google_id = db.Column(db.String(255), unique=True, nullable=True)

    __table_args__ = (
        db.CheckConstraint(
            "birth_month IS NULL OR (birth_month >= 1 AND birth_month <= 12)",
            name="ck_users_birth_month_range",
        ),
        db.CheckConstraint(
            "birth_day IS NULL OR (birth_day >= 1 AND birth_day <= 31)",
            name="ck_users_birth_day_range",
        ),
    )

    loans = db.relationship("Loan", backref="patron", lazy="dynamic")
    waitlist_entries = db.relationship("WaitlistEntry", backref="patron", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=13)).decode("utf-8")
        self.password_changed_at = datetime.now(UTC)

    def check_password(self, password):
        return bcrypt.checkpw(password.encode("utf-8"), self.password_hash.encode("utf-8"))

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_librarian(self):
        return self.role in ("admin", "librarian")

    @property
    def can_borrow(self):
        return self.is_active_account and not self.is_blocked and self.role == "patron"

    @property
    def is_active(self):
        """Flask-Login uses this to check if user session is valid."""
        return self.is_active_account and not self.is_blocked

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


# ── Tag ─────────────────────────────────────────────────────────────


class Tag(db.Model):
    __tablename__ = "tags"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False, index=True)

    def __repr__(self):
        return f"<Tag {self.name}>"


# Language code -> display name mapping (shared across modules)
LANGUAGE_LABELS = {
    "en": "English",
    "la": "Latin",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "de": "German",
    "pt": "Portuguese",
    "pl": "Polish",
    "el": "Greek",
}

# Ordered choices for admin language dropdowns (Latin/English first, then alphabetical)
LANGUAGE_CHOICES = [
    ("la", "Latin"),
    ("en", "English"),
    ("de", "German"),
    ("el", "Greek"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("it", "Italian"),
    ("pl", "Polish"),
    ("pt", "Portuguese"),
]

# ── Book ────────────────────────────────────────────────────────────


class Book(db.Model):
    __tablename__ = "books"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), unique=True, nullable=False, default=_uuid)
    title = db.Column(db.String(500), nullable=False, index=True)
    author = db.Column(db.String(500), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    language = db.Column(db.String(50), nullable=False, default="en")
    publication_year = db.Column(db.Integer, nullable=True)
    isbn = db.Column(db.String(20), nullable=True, index=True)
    other_identifier = db.Column(db.String(255), nullable=True)
    dewey_decimal = db.Column(db.String(20), nullable=True)
    loc_classification = db.Column(db.String(50), nullable=True)
    cover_filename = db.Column(db.String(255), nullable=True)
    master_filename = db.Column(db.String(255), nullable=True)

    owned_copies = db.Column(db.Integer, nullable=False, default=1)
    watermark_mode = db.Column(db.String(20), nullable=False, default="standard")  # standard, gentle
    loan_duration_override = db.Column(db.Integer, nullable=True)  # days, or NULL for default
    is_visible = db.Column(db.Boolean, nullable=False, default=True)
    is_disabled = db.Column(db.Boolean, nullable=False, default=False)
    is_featured = db.Column(db.Boolean, nullable=False, default=False)
    restricted_access = db.Column(db.Boolean, nullable=False, default=False)

    # Public domain
    is_public_domain = db.Column(db.Boolean, nullable=False, default=False)
    public_domain_confidence = db.Column(db.Integer, nullable=True)  # 0-100
    public_domain_reasoning = db.Column(db.Text, nullable=True)
    public_domain_filename = db.Column(db.String(255), nullable=True)  # cached library-edition PDF
    download_count = db.Column(db.Integer, nullable=False, default=0)

    imprimatur = db.Column(db.String(500), nullable=True)
    nihil_obstat = db.Column(db.String(500), nullable=True)
    ecclesiastical_approval_date = db.Column(db.String(100), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    tags = db.relationship("Tag", secondary=book_tags, backref="books", lazy="joined")
    loans = db.relationship("Loan", backref="book", lazy="dynamic")
    waitlist_entries = db.relationship("WaitlistEntry", backref="book", lazy="dynamic")

    @property
    def active_loan_count(self):
        # Note: fires a query per access. Acceptable for detail/admin pages (small N).
        # Catalog browse uses a subquery instead to avoid N+1.
        return Loan.query.filter(
            Loan.book_id == self.id,
            Loan.is_active == True,
        ).count()

    @property
    def available_copies(self):
        return max(0, self.owned_copies - self.active_loan_count)

    @property
    def is_available(self):
        return (
            self.is_visible and not self.is_disabled and self.available_copies > 0 and self.master_filename is not None
        )

    @property
    def loan_days(self):
        from flask import current_app

        if self.loan_duration_override:
            return self.loan_duration_override
        return current_app.config.get("DEFAULT_LOAN_DAYS", 14)

    @property
    def authors_list(self):
        """Split ``||``-delimited author string into a list."""
        if not self.author:
            return []
        return [a.strip() for a in self.author.split("||") if a.strip()]

    @property
    def formatted_authors(self):
        """Human-readable author string: 'A & B' or 'A, B & C'."""
        names = self.authors_list
        if len(names) <= 1:
            return self.author or ""
        return ", ".join(names[:-1]) + " & " + names[-1]

    @property
    def language_name(self):
        """Full language name with fallback to raw code."""
        return LANGUAGE_LABELS.get(self.language, self.language or "")

    def __repr__(self):
        return f"<Book {self.title[:40]}>"


# ── Loan ────────────────────────────────────────────────────────────


class Loan(db.Model):
    __tablename__ = "loans"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), unique=True, nullable=False, default=_uuid)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    book_id = db.Column(db.Integer, db.ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)

    borrowed_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    due_at = db.Column(db.DateTime, nullable=False)
    returned_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    invalidated = db.Column(db.Boolean, nullable=False, default=False)
    invalidated_reason = db.Column(db.String(500), nullable=True)

    circulation_filename = db.Column(db.String(255), nullable=True)
    # 64 hex chars (two uuid4 hex values concatenated) — used as unguessable URL token
    access_token = db.Column(
        db.String(64), unique=True, nullable=False, default=lambda: uuid.uuid4().hex + uuid.uuid4().hex
    )
    download_count = db.Column(db.Integer, nullable=False, default=0)

    renewal_count = db.Column(db.Integer, nullable=False, default=0)
    max_renewals = db.Column(db.Integer, nullable=False, default=2)

    reminder_sent = db.Column(db.Boolean, nullable=False, default=False)
    expiration_notice_sent = db.Column(db.Boolean, nullable=False, default=False)

    # Snapshot at time of loan
    book_title_snapshot = db.Column(db.String(500), nullable=True)
    book_author_snapshot = db.Column(db.String(500), nullable=True)

    @property
    def is_expired(self):
        due = self.due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        return _utcnow() >= due

    @property
    def is_accessible(self):
        return self.is_active and not self.invalidated and not self.is_expired

    def __repr__(self):
        return f"<Loan {self.public_id[:8]} book={self.book_id} patron={self.user_id}>"


# ── Waitlist ────────────────────────────────────────────────────────


class WaitlistEntry(db.Model):
    __tablename__ = "waitlist_entries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    book_id = db.Column(db.Integer, db.ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    notified_at = db.Column(db.DateTime, nullable=True)
    is_fulfilled = db.Column(db.Boolean, nullable=False, default=False)

    __table_args__ = (db.UniqueConstraint("user_id", "book_id", name="uq_waitlist_user_book"),)


# ── Audit Log ───────────────────────────────────────────────────────


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=_utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = db.Column(db.String(100), nullable=False, index=True)
    target_type = db.Column(db.String(50), nullable=True)  # book, loan, user
    target_id = db.Column(db.Integer, nullable=True)
    detail = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)

    user = db.relationship("User", backref="audit_logs", lazy="joined")

    def __repr__(self):
        return f"<AuditLog {self.action} at {self.timestamp}>"


# ── System Config ───────────────────────────────────────────────────


class SystemConfig(db.Model):
    __tablename__ = "system_config"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    @staticmethod
    def get(key, default=None):
        entry = SystemConfig.query.filter_by(key=key).first()
        return entry.value if entry else default

    @staticmethod
    def set(key, value):
        entry = SystemConfig.query.filter_by(key=key).first()
        if entry:
            entry.value = str(value)
        else:
            entry = SystemConfig(key=key, value=str(value))
            db.session.add(entry)
        db.session.commit()


# ── Favorite ───────────────────────────────────────────────────────


class Favorite(db.Model):
    __tablename__ = "favorites"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    book_id = db.Column(db.Integer, db.ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    user = db.relationship("User", backref=db.backref("favorites", lazy="dynamic"))
    book = db.relationship("Book", backref=db.backref("favorited_by", lazy="dynamic"))

    __table_args__ = (db.UniqueConstraint("user_id", "book_id", name="uq_favorite_user_book"),)


# ── Book Note ──────────────────────────────────────────────────────


class BookNote(db.Model):
    __tablename__ = "book_notes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    book_id = db.Column(db.Integer, db.ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    user = db.relationship("User", backref=db.backref("book_notes", lazy="dynamic"))
    book = db.relationship("Book", backref=db.backref("notes", lazy="dynamic"))


# ── Book Request ───────────────────────────────────────────────────


class BookRequest(db.Model):
    __tablename__ = "book_requests"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), unique=True, nullable=False, default=_uuid)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(500), nullable=False)
    author = db.Column(db.String(500), nullable=True)
    reason = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    admin_notes = db.Column(db.Text, nullable=True)
    resolved_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship(
        "User",
        foreign_keys=[user_id],
        backref=db.backref("book_requests", lazy="dynamic"),
    )
    resolver = db.relationship("User", foreign_keys=[resolved_by])


# ── Reading List ───────────────────────────────────────────────────


class ReadingList(db.Model):
    __tablename__ = "reading_lists"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), unique=True, nullable=False, default=_uuid)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_public = db.Column(db.Boolean, nullable=False, default=True)
    is_featured = db.Column(db.Boolean, nullable=False, default=False)
    season = db.Column(db.String(50), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    creator = db.relationship(
        "User",
        backref=db.backref("reading_lists", lazy="dynamic"),
    )
    items = db.relationship(
        "ReadingListItem",
        backref="reading_list",
        lazy="joined",
        order_by="ReadingListItem.position",
        cascade="all, delete-orphan",
    )


class ReadingListItem(db.Model):
    __tablename__ = "reading_list_items"

    id = db.Column(db.Integer, primary_key=True)
    reading_list_id = db.Column(
        db.Integer,
        db.ForeignKey("reading_lists.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    book_id = db.Column(db.Integer, db.ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    note = db.Column(db.Text, nullable=True)
    added_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    book = db.relationship("Book")

    __table_args__ = (db.UniqueConstraint("reading_list_id", "book_id", name="uq_reading_list_book"),)


# ── Staged Book (Bulk PDF Import) ────────────────────────────────


class StagedBook(db.Model):
    __tablename__ = "staged_books"

    id = db.Column(db.Integer, primary_key=True)

    # Source file info
    original_filename = db.Column(db.String(1000), nullable=False)
    file_size = db.Column(db.BigInteger, nullable=False)
    file_hash = db.Column(db.String(64), nullable=False, index=True)

    # Extracted / editable metadata
    title = db.Column(db.String(500), nullable=True)
    author = db.Column(db.String(500), nullable=True)
    description = db.Column(db.Text, nullable=True)
    language = db.Column(db.String(50), nullable=True, default="en")
    publication_year = db.Column(db.Integer, nullable=True)
    isbn = db.Column(db.String(20), nullable=True)
    tags_text = db.Column(db.String(1000), nullable=True)

    # Public domain (AI assessment during scan)
    public_domain_confidence = db.Column(db.Integer, nullable=True)  # 0-100
    public_domain_reasoning = db.Column(db.Text, nullable=True)

    # Metadata provenance
    metadata_sources = db.Column(db.Text, nullable=True)

    # Cover image
    cover_filename = db.Column(db.String(255), nullable=True)

    # Confidence: high, medium, low
    confidence = db.Column(db.String(10), nullable=False, default="low")

    # Status: pending, approved, dismissed, error
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    error_message = db.Column(db.Text, nullable=True)

    # Duplicate detection
    duplicate_of_book_id = db.Column(db.Integer, db.ForeignKey("books.id", ondelete="SET NULL"), nullable=True)
    duplicate_type = db.Column(db.String(20), nullable=True)

    # Scan tracking
    scan_batch_id = db.Column(db.String(32), nullable=True, index=True)
    scanned_at = db.Column(db.DateTime, nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    # After approval
    imported_book_id = db.Column(db.Integer, db.ForeignKey("books.id", ondelete="SET NULL"), nullable=True)

    duplicate_book = db.relationship("Book", foreign_keys=[duplicate_of_book_id])
    imported_book = db.relationship("Book", foreign_keys=[imported_book_id])

    def __repr__(self):
        return f"<StagedBook {self.original_filename[:40]} ({self.status})>"
