"""SQLAlchemy-Modelle der Dokumente mit eigener `Base`, unabhängig von der des Kerns (eigener Alembic-Zweig).

Jede Spalte entspricht 1:1 der Migration (ein Test vergleicht beide per `compare_metadata`): nur zusammen ändern."""

from __future__ import annotations

from datetime import datetime

from nodvard_deck.db.base import IdMixin, UTCDateTime, utcnow
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    # Eigene Metadaten: `declare_tables(Base.metadata)` prüft genau sie auf den Pflicht-Präfix `ext_documents_`.
    # Bewusst keine `relationship()` in dieser Datei: `lazy="selectin"` lädt nur bei einer frischen `select()`-Query
    # zuverlässig, nicht nach `session.get()`. Tags eines Dokuments werden immer per eigener Query geladen (`_tags_for()`).
    pass


class Tag(Base, IdMixin):
    __tablename__ = "ext_documents_tags"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    # Wenn gesetzt, hängt `_auto_tag()` den Tag bei jedem Upload an, sobald das Stichwort (klein geschrieben,
    # Teilstring) im OCR-Text vorkommt. Leer: nur manuell vergeben.
    match_keyword: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Document(Base, IdMixin):
    __tablename__ = "ext_documents_documents"

    filename: Mapped[str] = mapped_column(String(64))
    original_filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer())
    # Nur für PDFs gesetzt (Seitenzahl); None für Einzelbilder.
    page_count: Mapped[int | None] = mapped_column(Integer())
    ocr_text: Mapped[str | None] = mapped_column(Text())
    # "done" | "error": ein OCR-Fehler (z. B. Tesseract nicht installiert, unlesbares PDF) darf den Upload nicht
    # scheitern lassen; das Dokument bleibt sichtbar, nur ohne durchsuchbaren Text.
    ocr_status: Mapped[str] = mapped_column(String(20))
    ocr_error: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class DocumentTag(Base):
    """Zuordnung Dokument <-> Tag als eigene Tabelle mit zusammengesetztem Schlüssel, ohne `secondary=`-Beziehung."""

    __tablename__ = "ext_documents_document_tags"

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ext_documents_documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ext_documents_tags.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DocumentItemLink(Base):
    """Welches Dokument zu welchem Gegenstand im Inventar gehört (Rechnung, Handbuch, Garantieschein)."""

    # `item_id` ohne Fremdschlüssel: der Gegenstand gehört der Inventar-Erweiterung. Wird er dort gelöscht, räumt
    # `__init__.py` die Verknüpfung auf (Ereignis `inventory.item_deleted`).
    __tablename__ = "ext_documents_item_links"

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ext_documents_documents.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class InboxFile(Base):
    """Eine Datei aus dem Nextcloud-Ordner, die schon als Dokument übernommen ist (`inbox.py`)."""

    # Bleibt auch, wenn das Dokument hier gelöscht wird, sonst käme es beim nächsten Abruf wieder. Ändern sich Größe
    # oder Änderungszeit der Datei, gilt sie als neu und kommt als weiteres Dokument dazu.
    __tablename__ = "ext_documents_inbox_files"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    path: Mapped[str] = mapped_column(String(1024), primary_key=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer())
    modified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    document_id: Mapped[str | None] = mapped_column(String(36))
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
