"""nexus-soc: Virenschutz-Tabellen (Scans, Funde/Quarantaene, Haertungs-Audits)

Revision ID: e5f6a7b8c9d0
Revises: c4d5e6f7a8b9
Create Date: 2026-09-25 22:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ext_nexus_soc_scans",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("host_id", sa.String(length=64), nullable=False),
        sa.Column("host_name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("paths", sa.JSON(), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("files_scanned", sa.Integer(), nullable=True),
        sa.Column("infected", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("output_tail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ext_nexus_soc_scans_host_id", "ext_nexus_soc_scans", ["host_id"])
    op.create_index("ix_ext_nexus_soc_scans_kind", "ext_nexus_soc_scans", ["kind"])
    op.create_index("ix_ext_nexus_soc_scans_status", "ext_nexus_soc_scans", ["status"])
    op.create_index("ix_ext_nexus_soc_scans_started_at", "ext_nexus_soc_scans", ["started_at"])

    op.create_table(
        "ext_nexus_soc_findings",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("scan_id", sa.String(length=64), nullable=True),
        sa.Column("host_id", sa.String(length=64), nullable=False),
        sa.Column("host_name", sa.String(length=255), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("signature", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("quarantine_path", sa.Text(), nullable=True),
        sa.Column("original_mode", sa.String(length=8), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ext_nexus_soc_findings_scan_id", "ext_nexus_soc_findings", ["scan_id"])
    op.create_index("ix_ext_nexus_soc_findings_host_id", "ext_nexus_soc_findings", ["host_id"])
    op.create_index("ix_ext_nexus_soc_findings_status", "ext_nexus_soc_findings", ["status"])
    op.create_index("ix_ext_nexus_soc_findings_detected_at", "ext_nexus_soc_findings", ["detected_at"])

    op.create_table(
        "ext_nexus_soc_audits",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("host_id", sa.String(length=64), nullable=False),
        sa.Column("host_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("hardening_index", sa.Integer(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("suggestions", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ext_nexus_soc_audits_host_id", "ext_nexus_soc_audits", ["host_id"])
    op.create_index("ix_ext_nexus_soc_audits_created_at", "ext_nexus_soc_audits", ["created_at"])


def downgrade() -> None:
    op.drop_table("ext_nexus_soc_audits")
    op.drop_table("ext_nexus_soc_findings")
    op.drop_table("ext_nexus_soc_scans")
