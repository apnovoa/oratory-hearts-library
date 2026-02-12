"""Add membership_applications table

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-12

Stores intake data for Oratory membership applications.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "membership_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        # About You
        sa.Column("state_of_life", sa.String(100), nullable=False),
        sa.Column("religious_institute", sa.String(255), nullable=True),
        # Where You Are
        sa.Column("city", sa.String(255), nullable=True),
        sa.Column("state_province", sa.String(255), nullable=True),
        sa.Column("country", sa.String(255), nullable=True),
        # Your Faith
        sa.Column("baptismal_status", sa.String(50), nullable=False),
        sa.Column("denomination", sa.String(255), nullable=True),
        sa.Column("rite", sa.String(100), nullable=True),
        sa.Column("diocese", sa.String(255), nullable=True),
        sa.Column("parish", sa.String(255), nullable=True),
        sa.Column("sacrament_baptism", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("sacrament_confirmation", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("sacrament_eucharist", sa.Boolean(), nullable=False, server_default="0"),
        # Your Application
        sa.Column("why_join", sa.Text(), nullable=False),
        sa.Column("how_heard", sa.Text(), nullable=True),
        sa.Column("profession_of_faith", sa.String(10), nullable=False),
        # Admin
        sa.Column("admin_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("membership_applications")
