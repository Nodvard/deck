"""nexus-soc: Vorfaelle des Sammelfensters dauerhaft merken

Ein Vorfall lag bisher bis zum Ende des Sammelfensters nur im Speicher; ein Neustart
des Dashboards in dieser Zeit hat ihn still verloren. Jetzt steht er sofort in dieser
Tabelle ("offen") und wird nach der Verarbeitung auf "verarbeitet" gesetzt; beim Start
werden offene Eintraege wieder aufgenommen. `attempts`/`action_id`/`ai_summary` begrenzen
Wiederholungen und verhindern einen zweiten Aktionsvorschlag.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-30 10:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ext_nexus_soc_incident_queue",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("host_id", sa.String(length=64), nullable=True),
        sa.Column("host_name", sa.String(length=255), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("action_id", sa.String(length=64), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("proposal_started", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_ext_nexus_soc_incident_queue_status", "ext_nexus_soc_incident_queue", ["status"])
    op.create_index("ix_ext_nexus_soc_incident_queue_created_at", "ext_nexus_soc_incident_queue", ["created_at"])


def downgrade() -> None:
    op.drop_table("ext_nexus_soc_incident_queue")
