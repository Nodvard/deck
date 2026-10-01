"""SQLAlchemy-Modelle der inventory-Extension -- eigene `Base`/`MetaData`,
UNABHAENGIG von `nodvard_deck.models.Base`, damit die Trennung zum eigenen
Alembic-Branch (docs/02-EXTENSION-API.md §7, `migrations/versions/`) auch auf
ORM-Ebene sichtbar bleibt: `DbHandle.declare_tables(Base.metadata)`
(`__init__.py::setup()`) validiert GENAU diese Metadata gegen den Pflicht-Praefix
`ext_inventory_`, nicht die des Kerns.

Wiederverwendet aus dem Kern (reine, `Base`-unabhaengige Hilfsmittel, kein
Core-Purity-Verstoss -- dieselbe Zugriffsrichtung wie `ctx.db.session()`, nicht
umgekehrt): `IdMixin` (zeitsortierte UUID-Strings, `db/base.py::new_id()`),
`UTCDateTime` (dieselbe SQLite-Rundreise-Garantie, die WP-1 bereits einmal live
gefunden hat -- kein Grund, denselben Bug hier ein zweites Mal zu finden). Jede
Spalte hier hat ihre Entsprechung 1:1 in `migrations/versions/
a1b2c3d4e5f6_create_inventory_tables.py` -- beide Dateien muessen zusammen
geaendert werden.
"""

from __future__ import annotations

from datetime import date, datetime

from nodvard_deck.db.base import IdMixin, UTCDateTime, utcnow
from sqlalchemy import Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Category(Base, IdMixin):
    __tablename__ = "ext_inventory_categories"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Location(Base, IdMixin):
    __tablename__ = "ext_inventory_locations"

    name: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ext_inventory_locations.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Item(Base, IdMixin):
    __tablename__ = "ext_inventory_items"

    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text())
    category_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ext_inventory_categories.id", ondelete="SET NULL")
    )
    location_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ext_inventory_locations.id", ondelete="SET NULL")
    )
    quantity: Mapped[int] = mapped_column(Integer(), default=1)
    purchase_date: Mapped[date | None] = mapped_column(Date())
    # Geldbetrag als Cent-Ganzzahl, nicht Float -- keine Rundungsfehler bei
    # Preisangaben, dieselbe Haltung wie ueberall sonst in diesem Projekt (D-02:
    # dialektneutrale, portable Typen; ein `Numeric` mit fester Präzision wäre die
    # Alternative gewesen, Cent-Integer ist die einfachste, die keinen zusätzlichen
    # Typ-Kommentar für SQLite vs. Postgres braucht).
    purchase_price_cents: Mapped[int | None] = mapped_column(Integer())
    warranty_until: Mapped[date | None] = mapped_column(Date())
    notes: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ItemImage(Base, IdMixin):
    """**Bewusst keine ORM-`relationship()`** zu `Item` (weder hier noch umgekehrt) --
    das Loeschen kaskadiert bereits auf DB-Ebene (`ondelete="CASCADE"` oben,
    `PRAGMA foreign_keys=ON` global aktiv, `db/session.py`). Bilder eines Items werden
    stattdessen immer explizit per eigener Query geladen (`__init__.py::_images_for()`)
    -- vermeidet die in diesem Projekt mehrfach real aufgetretene `lazy="selectin"`-
    Falle (docs/00-DECISIONS.md D-12: laedt zuverlaessig nur bei einer frischen
    `select()`-Query, nicht nach `session.get()` auf ein bereits identity-gemapptes
    oder gerade erst angelegtes Objekt) komplett, statt sie hier ein weiteres Mal zu
    riskieren."""

    __tablename__ = "ext_inventory_item_images"

    item_id: Mapped[str] = mapped_column(String(36), ForeignKey("ext_inventory_items.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
