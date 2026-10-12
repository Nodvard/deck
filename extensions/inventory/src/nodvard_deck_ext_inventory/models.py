"""SQLAlchemy-Modelle des Inventars mit eigener `Base`, unabhängig von der des Kerns (eigener Alembic-Zweig).

Jede Spalte entspricht 1:1 der Migration (ein Test vergleicht beide per `compare_metadata`): nur zusammen ändern.
"""

from __future__ import annotations

from datetime import date, datetime

# Aus dem Kern nur Hilfsmittel ohne Bezug zu seiner `Base`: `IdMixin` (zeitsortierte UUID-Strings) und `UTCDateTime`
# (verlustfreie Rundreise der Zeitzone durch SQLite).
from nodvard_deck.db.base import IdMixin, UTCDateTime, utcnow
from sqlalchemy import Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    # Eigene Metadaten: `declare_tables(Base.metadata)` prüft genau sie auf den Pflicht-Präfix `ext_inventory_`.
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
    # Geldbetrag als Cent-Ganzzahl, nicht Float: keine Rundungsfehler, und der Typ ist auf jeder Datenbank gleich.
    purchase_price_cents: Mapped[int | None] = mapped_column(Integer())
    warranty_until: Mapped[date | None] = mapped_column(Date())
    notes: Mapped[str | None] = mapped_column(Text())
    # Homelab-Baukasten: woran man ein Teil erkennt, wo es hingehört und wo es steckt. `project_id`, `rack_id` und
    # `host_id` haben bewusst keinen Fremdschlüssel; das Löschen eines Projekts oder Racks setzt die Verweise auf NULL.
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
    """Bild eines Gegenstands; die Datei liegt auf der Platte (`images.py`), hier stehen nur Metadaten."""

    # Bewusst keine `relationship()` zu `Item`: das Löschen kaskadiert auf Datenbankebene (`ondelete="CASCADE"`,
    # `PRAGMA foreign_keys=ON`), die Bilder werden immer per eigener Query geladen (`_images_for()` in `__init__.py`).
    # `lazy="selectin"` lädt nur bei einer frischen `select()`-Query zuverlässig, nicht nach `session.get()` auf ein
    # schon gemapptes oder gerade angelegtes Objekt.

    __tablename__ = "ext_inventory_item_images"

    item_id: Mapped[str] = mapped_column(String(36), ForeignKey("ext_inventory_items.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
