"""documents: Verknüpfung mit Gegenständen aus dem Inventar

Neue Tabelle `ext_documents_item_links`: welches Dokument (Rechnung, Handbuch, Garantieschein) zu welchem
Gegenstand im Inventar gehört. Ein Dokument kann zu mehreren Gegenständen gehören und umgekehrt.

`item_id` ist bewusst ohne Fremdschlüssel: Der Gegenstand lebt in einer anderen Erweiterung (Inventar), und
die Dokumente sollen auch ohne sie funktionieren. Wird ein Gegenstand gelöscht, meldet das Inventar das über
den Ereignis-Bus (`inventory.item_deleted`), und die Erweiterung entfernt die Verknüpfung selbst. Nur eine neue
Tabelle: an den vorhandenen ändert sich nichts.

Revision ID: e2f3a4b5c6d7
Revises: 9f1e2d3c4b5a
Create Date: 2026-10-08 20:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "9f1e2d3c4b5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ext_documents_item_links",
        sa.Column(
            "document_id", sa.String(length=36),
            sa.ForeignKey("ext_documents_documents.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("item_id", sa.String(length=64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ext_documents_item_links_item_id", "ext_documents_item_links", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_ext_documents_item_links_item_id", table_name="ext_documents_item_links")
    op.drop_table("ext_documents_item_links")
