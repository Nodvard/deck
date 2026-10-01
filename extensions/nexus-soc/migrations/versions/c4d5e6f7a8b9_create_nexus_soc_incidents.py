"""create nexus-soc incidents table

Revision ID: c4d5e6f7a8b9
Revises:
Create Date: 2026-09-24 09:00:00.000000

Dritte Extension mit eigenem, vom Kern-Branch UNABHAENGIGEN Alembic-Kopf
(`down_revision=None`, eigener `branch_labels`-Wert) -- derselbe Mechanismus wie bei
`inventory`/`documents` (siehe `backend/src/nodvard_deck/migrate.py`). Ersetzt die rein
prozessinterne Vorfalls-Historie (nodvard_deck_ext_nexus_soc/models.py).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = ("nexus-soc",)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ext_nexus_soc_incidents",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("host_id", sa.String(length=64), nullable=True),
        sa.Column("host_name", sa.String(length=255), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_crash", sa.Boolean(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("action_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ext_nexus_soc_incidents_host_id", "ext_nexus_soc_incidents", ["host_id"])
    op.create_index("ix_ext_nexus_soc_incidents_host_name", "ext_nexus_soc_incidents", ["host_name"])
    op.create_index("ix_ext_nexus_soc_incidents_target", "ext_nexus_soc_incidents", ["target"])
    op.create_index("ix_ext_nexus_soc_incidents_status", "ext_nexus_soc_incidents", ["status"])
    op.create_index("ix_ext_nexus_soc_incidents_created_at", "ext_nexus_soc_incidents", ["created_at"])


def downgrade() -> None:
    op.drop_table("ext_nexus_soc_incidents")
