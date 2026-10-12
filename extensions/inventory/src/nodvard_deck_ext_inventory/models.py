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
Spalte hier hat ihre Entsprechung 1:1 in `migrations/versions/` (Anlage:
`a1b2c3d4e5f6_create_inventory_tables.py`, Baukasten: `c7d8e9f0a1b2_inventory_baukasten.py`)
-- Modell und Migration muessen zusammen geaendert werden.

`Item.project_id`, `Item.rack_id` und `Item.host_id` haben bewusst keinen Fremdschluessel (Begruendung in
der Baukasten-Migration); das Loeschen eines Projekts oder Racks setzt die Verweise in `__init__.py` auf NULL.
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
    # Homelab-Baukasten: woran man ein Teil erkennt, wo es hingehört und wo es steckt.
    manufacturer: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(200))
    serial_number: Mapped[str | None] = mapped_column(String(200))
    product_url: Mapped[str | None] = mapped_column(String(2000))
    host_id: Mapped[str | None] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    rack_id: Mapped[str | None] = mapped_column(String(36), index=True)
    rack_position: Mapped[int | None] = mapped_column(Integer())
    rack_units: Mapped[int | None] = mapped_column(Integer())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Project(Base, IdMixin):
    """Bauprojekt („NAS bauen“): Teile mit `Item.project_id`, Status und Budget."""

    __tablename__ = "ext_inventory_projects"

    name: Mapped[str] = mapped_column(String(200))
    # planned | building | done
    status: Mapped[str] = mapped_column(String(20), default="planned", server_default="planned")
    description: Mapped[str | None] = mapped_column(Text())
    budget_cents: Mapped[int | None] = mapped_column(Integer())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Rack(Base, IdMixin):
    """Serverschrank: Höhe in HE; Geräte stecken über `Item.rack_id`/`rack_position`/`rack_units` darin."""

    __tablename__ = "ext_inventory_racks"

    name: Mapped[str] = mapped_column(String(200))
    height_units: Mapped[int] = mapped_column(Integer(), default=42, server_default="42")
    location_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ext_inventory_locations.id", ondelete="SET NULL")
    )
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
