"""nexus-soc: Update-Zentrale, Einbruchschutz und Datei-Waechter

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-26 02:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ext_nexus_soc_update_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("host_id", sa.String(length=64), nullable=False),
        sa.Column("host_name", sa.String(length=255), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("upgraded", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("output_tail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ext_nexus_soc_update_runs_host_id", "ext_nexus_soc_update_runs", ["host_id"])
    op.create_index("ix_ext_nexus_soc_update_runs_status", "ext_nexus_soc_update_runs", ["status"])
    op.create_index("ix_ext_nexus_soc_update_runs_started_at", "ext_nexus_soc_update_runs", ["started_at"])

    op.create_table(
        "ext_nexus_soc_baselines",
        sa.Column("id", sa.String(length=160), primary_key=True),
        sa.Column("host_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ext_nexus_soc_baselines_host_id", "ext_nexus_soc_baselines", ["host_id"])
    op.create_index("ix_ext_nexus_soc_baselines_kind", "ext_nexus_soc_baselines", ["kind"])

    op.create_table(
        "ext_nexus_soc_events",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("host_id", sa.String(length=64), nullable=False),
        sa.Column("host_name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ext_nexus_soc_events_host_id", "ext_nexus_soc_events", ["host_id"])
    op.create_index("ix_ext_nexus_soc_events_kind", "ext_nexus_soc_events", ["kind"])
    op.create_index("ix_ext_nexus_soc_events_acknowledged", "ext_nexus_soc_events", ["acknowledged"])
    op.create_index("ix_ext_nexus_soc_events_created_at", "ext_nexus_soc_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("ext_nexus_soc_events")
    op.drop_table("ext_nexus_soc_baselines")
    op.drop_table("ext_nexus_soc_update_runs")
