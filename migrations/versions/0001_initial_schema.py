"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-02-09

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active_account", sa.Boolean(), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), nullable=False),
        sa.Column("block_reason", sa.Text(), nullable=True),
        sa.Column("failed_login_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("birth_month", sa.Integer(), nullable=True),
        sa.Column("birth_day", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("force_logout_before", sa.DateTime(), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(), nullable=True),
        sa.Column("google_id", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "birth_month IS NULL OR (birth_month >= 1 AND birth_month <= 12)",
            name="ck_users_birth_month_range",
        ),
        sa.CheckConstraint(
            "birth_day IS NULL OR (birth_day >= 1 AND birth_day <= 31)",
            name="ck_users_birth_day_range",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("google_id"),
        sa.UniqueConstraint("public_id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_email"), ["email"], unique=False)

    # Tags
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    with op.batch_alter_table("tags", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_tags_name"), ["name"], unique=False)

    # Books
    op.create_table(
        "books",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("author", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=50), nullable=False),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("isbn", sa.String(length=20), nullable=True),
        sa.Column("other_identifier", sa.String(length=255), nullable=True),
        sa.Column("dewey_decimal", sa.String(length=20), nullable=True),
        sa.Column("loc_classification", sa.String(length=50), nullable=True),
        sa.Column("cover_filename", sa.String(length=255), nullable=True),
        sa.Column("master_filename", sa.String(length=255), nullable=True),
        sa.Column("owned_copies", sa.Integer(), nullable=False),
        sa.Column("watermark_mode", sa.String(length=20), nullable=False),
        sa.Column("loan_duration_override", sa.Integer(), nullable=True),
        sa.Column("is_visible", sa.Boolean(), nullable=False),
        sa.Column("is_disabled", sa.Boolean(), nullable=False),
        sa.Column("is_featured", sa.Boolean(), nullable=False),
        sa.Column("restricted_access", sa.Boolean(), nullable=False),
        sa.Column("imprimatur", sa.String(length=500), nullable=True),
        sa.Column("nihil_obstat", sa.String(length=500), nullable=True),
        sa.Column("ecclesiastical_approval_date", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    with op.batch_alter_table("books", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_books_title"), ["title"], unique=False)
        batch_op.create_index(batch_op.f("ix_books_author"), ["author"], unique=False)
        batch_op.create_index(batch_op.f("ix_books_isbn"), ["isbn"], unique=False)

    # Book-Tags association
    op.create_table(
        "book_tags",
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("book_id", "tag_id"),
    )

    # Loans
    op.create_table(
        "loans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("borrowed_at", sa.DateTime(), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("returned_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("invalidated", sa.Boolean(), nullable=False),
        sa.Column("invalidated_reason", sa.String(length=500), nullable=True),
        sa.Column("circulation_filename", sa.String(length=255), nullable=True),
        sa.Column("access_token", sa.String(length=64), nullable=False),
        sa.Column("download_count", sa.Integer(), nullable=False),
        sa.Column("renewal_count", sa.Integer(), nullable=False),
        sa.Column("max_renewals", sa.Integer(), nullable=False),
        sa.Column("reminder_sent", sa.Boolean(), nullable=False),
        sa.Column("expiration_notice_sent", sa.Boolean(), nullable=False),
        sa.Column("book_title_snapshot", sa.String(length=500), nullable=True),
        sa.Column("book_author_snapshot", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("access_token"),
        sa.UniqueConstraint("public_id"),
    )
    with op.batch_alter_table("loans", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_loans_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_loans_book_id"), ["book_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_loans_is_active"), ["is_active"], unique=False)

    # Waitlist Entries
    op.create_table(
        "waitlist_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("notified_at", sa.DateTime(), nullable=True),
        sa.Column("is_fulfilled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "book_id", name="uq_waitlist_user_book"),
    )
    with op.batch_alter_table("waitlist_entries", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_waitlist_entries_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_waitlist_entries_book_id"), ["book_id"], unique=False)

    # Audit Logs
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_logs_timestamp"), ["timestamp"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_action"), ["action"], unique=False)

    # System Config
    op.create_table(
        "system_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )

    # Favorites
    op.create_table(
        "favorites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "book_id", name="uq_favorite_user_book"),
    )
    with op.batch_alter_table("favorites", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_favorites_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_favorites_book_id"), ["book_id"], unique=False)

    # Book Notes
    op.create_table(
        "book_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("book_notes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_book_notes_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_book_notes_book_id"), ["book_id"], unique=False)

    # Book Requests
    op.create_table(
        "book_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("author", sa.String(length=500), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("admin_notes", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    with op.batch_alter_table("book_requests", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_book_requests_user_id"), ["user_id"], unique=False)

    # Reading Lists
    op.create_table(
        "reading_lists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("is_featured", sa.Boolean(), nullable=False),
        sa.Column("season", sa.String(length=50), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )

    # Reading List Items
    op.create_table(
        "reading_list_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reading_list_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reading_list_id"], ["reading_lists.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reading_list_id", "book_id", name="uq_reading_list_book"),
    )
    with op.batch_alter_table("reading_list_items", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_reading_list_items_reading_list_id"),
            ["reading_list_id"],
            unique=False,
        )
        batch_op.create_index(batch_op.f("ix_reading_list_items_book_id"), ["book_id"], unique=False)

    # Staged Books
    op.create_table(
        "staged_books",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(length=1000), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("author", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=50), nullable=True),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("isbn", sa.String(length=20), nullable=True),
        sa.Column("tags_text", sa.String(length=1000), nullable=True),
        sa.Column("metadata_sources", sa.Text(), nullable=True),
        sa.Column("cover_filename", sa.String(length=255), nullable=True),
        sa.Column("confidence", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duplicate_of_book_id", sa.Integer(), nullable=True),
        sa.Column("duplicate_type", sa.String(length=20), nullable=True),
        sa.Column("scan_batch_id", sa.String(length=32), nullable=True),
        sa.Column("scanned_at", sa.DateTime(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("imported_book_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["duplicate_of_book_id"], ["books.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["imported_book_id"], ["books.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("staged_books", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_staged_books_file_hash"), ["file_hash"], unique=False)
        batch_op.create_index(batch_op.f("ix_staged_books_status"), ["status"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_staged_books_scan_batch_id"),
            ["scan_batch_id"],
            unique=False,
        )


def downgrade():
    op.drop_table("staged_books")
    op.drop_table("reading_list_items")
    op.drop_table("reading_lists")
    op.drop_table("book_requests")
    op.drop_table("book_notes")
    op.drop_table("favorites")
    op.drop_table("system_config")
    op.drop_table("audit_logs")
    op.drop_table("waitlist_entries")
    op.drop_table("loans")
    op.drop_table("book_tags")
    op.drop_table("books")
    op.drop_table("tags")
    op.drop_table("users")
