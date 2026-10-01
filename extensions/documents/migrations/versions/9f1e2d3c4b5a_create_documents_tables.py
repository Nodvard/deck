"""create documents tables

Revision ID: 9f1e2d3c4b5a
Revises:
Create Date: 2026-09-23 19:00:00.000000

Zweite Extension mit eigenem, vom Kern-Branch UNABHAENGIGEN Alembic-Kopf
(`down_revision=None`, eigener `branch_labels`-Wert) -- derselbe, jetzt bewiesene
Mechanismus wie bei `inventory` (siehe dortige erste Revision und
`backend/src/nodvard_deck/migrate.py`).

Tabellennamen zwingend mit `ext_documents_` praefixiert (docs/02-EXTENSION-
API.md §7), zusaetzlich von `DbHandle.declare_tables()` beim `setup()`-Aufruf
gegen die ORM-Modelle validiert (siehe nodvard_deck_ext_documents/models.py).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f1e2d3c4b5a"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = ("documents",)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ext_documents_tags",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False, unique=True),
        sa.Column("match_keyword", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "ext_documents_documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("filename", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=300), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("ocr_status", sa.String(length=20), nullable=False),
        sa.Column("ocr_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ext_documents_documents_original_filename", "ext_documents_documents", ["original_filename"])

    op.create_table(
        "ext_documents_document_tags",
        sa.Column(
            "document_id", sa.String(length=36),
            sa.ForeignKey("ext_documents_documents.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "tag_id", sa.String(length=36),
            sa.ForeignKey("ext_documents_tags.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ext_documents_document_tags_tag_id", "ext_documents_document_tags", ["tag_id"])


def downgrade() -> None:
    op.drop_table("ext_documents_document_tags")
    op.drop_table("ext_documents_documents")
    op.drop_table("ext_documents_tags")
