"""SQLAlchemy-Modelle der documents-Extension -- eigene `Base`/`MetaData`,
UNABHAENGIG von `nodvard_deck.models.Base` (dasselbe Muster wie
`nodvard_deck_ext_inventory.models`, siehe dortiger Docstring fuer die volle
Begruendung): `DbHandle.declare_tables(Base.metadata)` (`__init__.py::setup()`)
validiert GENAU diese Metadata gegen den Pflicht-Praefix `ext_documents_`.

Wiederverwendet aus dem Kern: `IdMixin`, `UTCDateTime`, `utcnow()`
(`nodvard_deck.db.base`) -- dieselbe Zugriffsrichtung wie `ctx.db.session()`.

**Bewusst keine ORM-`relationship()`** irgendwo in dieser Datei (weder
Document<->Tag ueber die Assoziationstabelle noch sonstwo) -- dieselbe
Begruendung wie `nodvard_deck_ext_inventory.models.ItemImage`: D-12
(`lazy="selectin"` laedt zuverlaessig nur bei einer frischen `select()`-Query,
nicht nach `session.get()`). Tags eines Dokuments werden immer explizit per
eigener Query geladen (`__init__.py::_tags_for()`)."""

from __future__ import annotations

from datetime import datetime

from nodvard_deck.db.base import IdMixin, UTCDateTime, utcnow
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tag(Base, IdMixin):
    __tablename__ = "ext_documents_tags"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    # Wenn gesetzt: bei jedem Upload wird geprueft, ob dieses Stichwort
    # (klein geschrieben, Teilstring) im OCR-Text vorkommt -- wenn ja, wird der
    # Tag automatisch angehaengt (__init__.py::_auto_tag()). Leer/None = reiner
    # manueller Tag, nie automatisch vergeben.
    match_keyword: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Document(Base, IdMixin):
    __tablename__ = "ext_documents_documents"

    filename: Mapped[str] = mapped_column(String(64))
    original_filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer())
    # Nur fuer PDFs gesetzt (Seitenzahl); None fuer Einzelbilder.
    page_count: Mapped[int | None] = mapped_column(Integer())
    ocr_text: Mapped[str | None] = mapped_column(Text())
    # "done" | "error" -- ein OCR-Fehler (z. B. Tesseract nicht installiert,
    # unlesbares PDF) darf den Upload nicht scheitern lassen, siehe ocr.py;
    # das Dokument bleibt sichtbar, nur ohne durchsuchbaren Text.
    ocr_status: Mapped[str] = mapped_column(String(20))
    ocr_error: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class DocumentTag(Base):
    """Assoziationstabelle Document<->Tag -- als eigenstaendige Tabelle mit
    zusammengesetztem Primaerschluessel modelliert, NICHT als SQLAlchemy
    `secondary=`-Tabelle an einer `relationship()`, damit hier nirgends implizit
    nachgeladen wird (siehe Modul-Docstring)."""

    __tablename__ = "ext_documents_document_tags"

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ext_documents_documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ext_documents_tags.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
