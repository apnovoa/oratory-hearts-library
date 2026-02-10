"""Add CHECK constraints and FK ondelete behavior for existing databases

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-10

For fresh databases created with 0001 (post-hardening), these constraints
already exist.  This migration retrofits them onto databases that were
created before the 0001 file was updated.  Batch-mode table recreation
is required because SQLite does not support ALTER CONSTRAINT.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Naming convention lets Alembic generate predictable names for the
# unnamed FK constraints it reflects from the existing SQLite schema.
_naming_convention = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _recreate_fks(table_name, fk_specs):
    """Drop existing FKs and recreate with ondelete behavior.

    *fk_specs* is a list of (local_cols, remote_table, remote_cols, ondelete).
    """
    with op.batch_alter_table(
        table_name, schema=None, naming_convention=_naming_convention
    ) as batch_op:
        for local_cols, remote_table, remote_cols, ondelete in fk_specs:
            col_name = local_cols[0]
            old_name = f"fk_{table_name}_{col_name}_{remote_table}"
            new_name = f"fk_{table_name}_{col_name}"
            try:
                batch_op.drop_constraint(old_name, type_="foreignkey")
            except Exception:
                pass  # constraint may already have ondelete (fresh DB)
            batch_op.create_foreign_key(
                new_name, remote_table, local_cols, remote_cols, ondelete=ondelete
            )


def upgrade():
    # ── 1. Users: add CHECK constraints on birth_month / birth_day ──
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_users_birth_month_range",
            "birth_month IS NULL OR (birth_month >= 1 AND birth_month <= 12)",
        )
        batch_op.create_check_constraint(
            "ck_users_birth_day_range",
            "birth_day IS NULL OR (birth_day >= 1 AND birth_day <= 31)",
        )

    # ── 2. FK ondelete on all child tables ──────────────────────────
    _recreate_fks("book_tags", [
        (["book_id"], "books", ["id"], "CASCADE"),
        (["tag_id"], "tags", ["id"], "CASCADE"),
    ])

    _recreate_fks("loans", [
        (["user_id"], "users", ["id"], "CASCADE"),
        (["book_id"], "books", ["id"], "CASCADE"),
    ])

    _recreate_fks("waitlist_entries", [
        (["user_id"], "users", ["id"], "CASCADE"),
        (["book_id"], "books", ["id"], "CASCADE"),
    ])

    _recreate_fks("audit_logs", [
        (["user_id"], "users", ["id"], "SET NULL"),
    ])

    _recreate_fks("favorites", [
        (["user_id"], "users", ["id"], "CASCADE"),
        (["book_id"], "books", ["id"], "CASCADE"),
    ])

    _recreate_fks("book_notes", [
        (["user_id"], "users", ["id"], "CASCADE"),
        (["book_id"], "books", ["id"], "CASCADE"),
    ])

    _recreate_fks("book_requests", [
        (["user_id"], "users", ["id"], "CASCADE"),
        (["resolved_by"], "users", ["id"], "SET NULL"),
    ])

    _recreate_fks("reading_lists", [
        (["created_by"], "users", ["id"], "CASCADE"),
    ])

    _recreate_fks("reading_list_items", [
        (["reading_list_id"], "reading_lists", ["id"], "CASCADE"),
        (["book_id"], "books", ["id"], "CASCADE"),
    ])

    _recreate_fks("staged_books", [
        (["duplicate_of_book_id"], "books", ["id"], "SET NULL"),
        (["imported_book_id"], "books", ["id"], "SET NULL"),
    ])


def downgrade():
    # Remove CHECK constraints from users
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("ck_users_birth_month_range", type_="check")
        batch_op.drop_constraint("ck_users_birth_day_range", type_="check")

    # Revert FKs to no ondelete (original behavior)
    _revert_fks("book_tags", [
        (["book_id"], "books", ["id"]),
        (["tag_id"], "tags", ["id"]),
    ])
    _revert_fks("loans", [
        (["user_id"], "users", ["id"]),
        (["book_id"], "books", ["id"]),
    ])
    _revert_fks("waitlist_entries", [
        (["user_id"], "users", ["id"]),
        (["book_id"], "books", ["id"]),
    ])
    _revert_fks("audit_logs", [
        (["user_id"], "users", ["id"]),
    ])
    _revert_fks("favorites", [
        (["user_id"], "users", ["id"]),
        (["book_id"], "books", ["id"]),
    ])
    _revert_fks("book_notes", [
        (["user_id"], "users", ["id"]),
        (["book_id"], "books", ["id"]),
    ])
    _revert_fks("book_requests", [
        (["user_id"], "users", ["id"]),
        (["resolved_by"], "users", ["id"]),
    ])
    _revert_fks("reading_lists", [
        (["created_by"], "users", ["id"]),
    ])
    _revert_fks("reading_list_items", [
        (["reading_list_id"], "reading_lists", ["id"]),
        (["book_id"], "books", ["id"]),
    ])
    _revert_fks("staged_books", [
        (["duplicate_of_book_id"], "books", ["id"]),
        (["imported_book_id"], "books", ["id"]),
    ])


def _revert_fks(table_name, fk_specs):
    """Recreate FKs without ondelete (restore original behavior)."""
    with op.batch_alter_table(
        table_name, schema=None, naming_convention=_naming_convention
    ) as batch_op:
        for local_cols, remote_table, remote_cols in fk_specs:
            col_name = local_cols[0]
            named = f"fk_{table_name}_{col_name}"
            try:
                batch_op.drop_constraint(named, type_="foreignkey")
            except Exception:
                pass
            batch_op.create_foreign_key(
                None, remote_table, local_cols, remote_cols
            )
