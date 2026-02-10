"""Add public domain fields to books and staged_books

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-10

Adds columns for tracking public domain status, AI confidence/reasoning,
cached library-edition PDF filename, and download count.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    # -- books table --
    with op.batch_alter_table("books") as batch_op:
        batch_op.add_column(sa.Column("is_public_domain", sa.Boolean(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("public_domain_confidence", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("public_domain_reasoning", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("public_domain_filename", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"))

    # -- staged_books table --
    with op.batch_alter_table("staged_books") as batch_op:
        batch_op.add_column(sa.Column("public_domain_confidence", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("public_domain_reasoning", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("staged_books") as batch_op:
        batch_op.drop_column("public_domain_reasoning")
        batch_op.drop_column("public_domain_confidence")

    with op.batch_alter_table("books") as batch_op:
        batch_op.drop_column("download_count")
        batch_op.drop_column("public_domain_filename")
        batch_op.drop_column("public_domain_reasoning")
        batch_op.drop_column("public_domain_confidence")
        batch_op.drop_column("is_public_domain")
