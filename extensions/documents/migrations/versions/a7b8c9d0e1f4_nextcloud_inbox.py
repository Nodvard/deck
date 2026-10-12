"""documents: Nextcloud-Ordner als Eingang

Neue Tabelle `ext_documents_inbox_files`: welche Dateien aus dem eingestellten Nextcloud-Ordner schon als
Dokument übernommen sind. Damit holt der Abruf keine Datei zweimal, auch wenn das Dokument hier später gelöscht
wird. Eine Datei zählt als neu, wenn es ihren Pfad noch nicht gibt oder Größe bzw. Änderungszeit sich geändert
haben. `document_id` ohne Fremdschlüssel: wird das Dokument gelöscht, bleibt der Eintrag, damit es nicht wieder
auftaucht. Nur eine neue Tabelle: an den vorhandenen ändert sich nichts.

Revision ID: a7b8c9d0e1f4
Revises: e2f3a4b5c6d7
Create Date: 2026-10-09 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f4"
down_revision: str | Sequence[str] | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_documents_inbox_files",
        sa.Column("source_id", sa.String(length=64), primary_key=True),
        sa.Column("path", sa.String(length=1024), primary_key=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("document_id", sa.String(length=36), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ext_documents_inbox_files")
