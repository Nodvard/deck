"""nexus-soc: Update-Laeufe merken sich ihren entkoppelten Lauf auf dem Server

Befund 37: Updates laufen auf dem Server entkoppelt vom SSH-Kanal weiter
(`detached.py`). `remote_id` ist der Name des Laufs dort (Dateien unter
/var/lib/nexus-updates/<remote_id>.*); ist er gesetzt, nimmt das Dashboard einen
Lauf nach einem eigenen Neustart wieder auf, statt ihn als abgebrochen zu markieren.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-30 02:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ext_nexus_soc_update_runs", sa.Column("remote_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ext_nexus_soc_update_runs") as batch:
        batch.drop_column("remote_id")
