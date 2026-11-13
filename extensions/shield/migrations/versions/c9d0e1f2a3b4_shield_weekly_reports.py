"""Speichert Wochenberichte und ihren Versandstatus."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ext_shield_weekly_reports",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ext_shield_weekly_reports_created_at", "ext_shield_weekly_reports", ["created_at"])


def downgrade() -> None:
    op.drop_table("ext_shield_weekly_reports")
