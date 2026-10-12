"""Inventar-Erweiterung (Homebox-artig): Gegenstände, Standorte, Kategorien, Racks, Bauprojekte und Bilder.

Eigene Tabellen mit eigenem Alembic-Zweig (`models.py`); die Bilder liegen auf der Platte, nicht in der Datenbank.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from nodvard_sdk import (
    Actor,
    Badge,
    Event,
    ExtensionContext,
    GridSize,
    HealthReport,
    ListItem,
    ListView,
    NodvardExtension,
    PageSpec,
    Refresh,
    WidgetSpec,
    max_body_bytes,
)
from nodvard_sdk.errors import HostUnreachable
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select, update

from .hardware import SCAN_COMMAND, parse_scan
from .images import ALLOWED_CONTENT_TYPES, MAX_IMAGE_BYTES, delete_image_file, save_image
from .models import Base, Category, Item, ItemImage, Location, Project, Rack

_log = logging.getLogger(__name__)


def _short(text: str | None, limit: int = 300) -> str:
    """Fremden Text (Fehlerausgabe des Servers) auf eine Zeile ohne Steuerzeichen kürzen."""
    clean = " ".join("".join(ch if ch.isprintable() else " " for ch in (text or "")).split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _scan_error_text(exc: BaseException) -> str:
    """Warum das Auslesen nicht ging, in einem Satz. Nur Fehler, die selbst einen Satz für Menschen tragen
    (`readable` wie der SSH-Fehler des Kerns, `HostUnreachable` etwa ohne Zugang), kommen mit ihrem Text durch;
    alles andere könnte Pfade oder Adressen verraten."""
    if isinstance(exc, TimeoutError):
        return "keine Antwort innerhalb der Zeitgrenze – ist der Server an?"
    if isinstance(exc, ConnectionRefusedError):
        return "Verbindung abgelehnt – läuft der SSH-Dienst?"
    if (getattr(exc, "readable", False) or isinstance(exc, HostUnreachable)) and str(exc).strip():
        return _short(str(exc))
    # Abbruch mitten im Befehl (Netz weg, Server neu gestartet): erwartbar, darum ohne Traceback im Protokoll.
    name = type(exc).__name__
    if name == "PermissionDenied":
        return "Anmeldung abgelehnt – stimmt der Zugang unter Einstellungen → Server & Zugänge?"
    if name == "ChannelOpenError":
        return "der Server lässt gerade keine weitere Sitzung zu (zu viele gleichzeitige Verbindungen?)."
    if name in ("HostKeyNotVerifiable", "HostKeyMismatch"):
        return "der Schlüssel des Servers passt nicht zum gespeicherten – prüfe ihn unter Einstellungen → Server & Zugänge."
    if isinstance(exc, ConnectionError) or type(exc).__module__.split(".")[0] == "asyncssh":
        return "Verbindung abgebrochen – war der Server kurz weg?"
    if isinstance(exc, OSError):
        return "keine Verbindung zum Server."
    _log.warning("Auslesen fehlgeschlagen", exc_info=exc)
    return "unerwarteter Fehler (Einzelheiten im Protokoll des Dashboards)."


# Die Bilder kommen von Nutzern. Wird die Adresse direkt geöffnet, soll weder ein Skript laufen
# noch der Browser den Typ erraten. Als <img> eingebettet ändert sich nichts.
_UNTRUSTED_FILE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
}

# Ab so vielen Tagen vor Ablauf taucht ein Gegenstand im Garantie-Widget auf. Muss zur Inventar-Seite passen
# (`warrantyState()` in InventoryPage.tsx: bis 90 Tage heißt "endet bald"), sonst zählen Seite und Kachel verschieden.
_WARRANTY_SOON_DAYS = 90

# So lange steht eine abgelaufene Garantie noch in der Kachel: sonst füllten alte Geräte die 2x2-Kachel, und die gerade
# ablaufenden wären abgeschnitten. Die Inventar-Seite zeigt weiterhin alle.
_WARRANTY_EXPIRED_SHOWN_DAYS = 30


class CategoryIn(BaseModel):
    name: str


class CategoryOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    updated_at: datetime


class LocationIn(BaseModel):
    name: str
    parent_id: str | None = None


class LocationOut(BaseModel):
    id: str
    name: str
    parent_id: str | None
    created_at: datetime
    updated_at: datetime


# So viele Kennungen beantwortet eine Nachfrage `inventory.items_check_requested` höchstens.
MAX_ITEMS_PER_CHECK = 500
_ITEM_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

MAX_QUANTITY = 100_000
# 10 Millionen Euro in Cent: weit über jedem Homelab, und Preis mal Menge mal Gegenstände bleibt im Zahlenbereich.
MAX_CENTS = 1_000_000_000


class ItemIn(BaseModel):
    name: str
    description: str | None = None
    category_id: str | None = None
    location_id: str | None = None
    # Obergrenzen, damit die Summen (Preis mal Menge je Projekt) nie über das hinauswachsen, was SQLite rechnen kann:
    # sonst endet die Projektliste mit einem Überlauf.
    quantity: int = 1
    purchase_date: date | None = None
    purchase_price_cents: int | None = None
    warranty_until: date | None = None
    notes: str | None = None
    # Homelab-Baukasten: beim Ändern (PUT) gilt ein Feld nur, wenn es mitgeschickt wird, damit ein älterer Aufrufer,
    # der sie nicht kennt, sie nicht versehentlich löscht.
    manufacturer: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    serial_number: str | None = Field(default=None, max_length=200)
    product_url: str | None = Field(default=None, max_length=2000)
    # Kennungen sind 40 Zeichen lang (`new_id()`), obwohl die Spalten String(36) heißen: hier nur eine Obergrenze.
    host_id: str | None = Field(default=None, max_length=64)
    project_id: str | None = Field(default=None, max_length=64)
    rack_id: str | None = Field(default=None, max_length=64)
    rack_position: int | None = Field(default=None, ge=1, le=100)
    rack_units: int | None = Field(default=None, ge=1, le=100)

    @field_validator("host_id", "project_id", "rack_id", "manufacturer", "model", "serial_number", mode="before")
    @classmethod
    def _blank_is_none(cls, value: Any) -> Any:
        # "" aus einem leeren Formularfeld heißt "keins", nicht "ein Verweis mit leerer Kennung".
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("quantity")
    @classmethod
    def _quantity_in_range(cls, value: int) -> int:
        if not 0 <= value <= MAX_QUANTITY:
            raise ValueError("Die Menge muss zwischen 0 und 100.000 liegen.")
        return value

    @field_validator("purchase_price_cents")
    @classmethod
    def _price_in_range(cls, value: int | None) -> int | None:
        if value is not None and not 0 <= value <= MAX_CENTS:
            raise ValueError("Der Preis muss zwischen 0 und 10 Millionen € liegen.")
        return value

    @field_validator("product_url")
    @classmethod
    def _only_web_links(cls, value: str | None) -> str | None:
        # Der Link wird in der Oberfläche anklickbar: nur vollständige Webadressen, kein `javascript:` o. Ä.
        if value is None or not value.strip():
            return None
        value = value.strip()
        parts = urlsplit(value)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname or any(ch.isspace() or ord(ch) < 32 for ch in value):
            raise ValueError("Der Produktlink muss eine vollständige Webadresse sein, die mit http:// oder https:// beginnt.")
        # `https://shop.example@evil.example/` sieht nach dem Shop aus, führt aber woandershin; Zugangsdaten gehören
        # ohnehin nicht in einen Link, den jeder mit Leserecht sieht.
        if parts.username is not None or parts.password is not None:
            raise ValueError("Der Produktlink darf keinen Benutzernamen und kein Passwort enthalten (kein „@“ vor der Adresse).")
        return value


BAUKASTEN_FIELDS = (
    "manufacturer", "model", "serial_number", "product_url", "host_id", "project_id", "rack_id", "rack_position",
    "rack_units",
)


class ItemImageOut(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
    url: str


class ItemOut(BaseModel):
    id: str
    name: str
    description: str | None
    category_id: str | None
    location_id: str | None
    quantity: int
    purchase_date: date | None
    purchase_price_cents: int | None
    warranty_until: date | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    images: list[ItemImageOut]
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    product_url: str | None = None
    host_id: str | None = None
    project_id: str | None = None
    rack_id: str | None = None
    rack_position: int | None = None
    rack_units: int | None = None


PROJECT_STATUSES = ("planned", "building", "done")


class ProjectIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    status: str = "planned"
    description: str | None = None
    budget_cents: int | None = Field(default=None, ge=0)

    @field_validator("budget_cents")
    @classmethod
    def _budget_in_range(cls, value: int | None) -> int | None:
        if value is not None and value > MAX_CENTS:
            raise ValueError("Das Budget darf höchstens 10 Millionen € betragen.")
        return value

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str) -> str:
        if value not in PROJECT_STATUSES:
            raise ValueError(f"Unbekannter Status: {value}")
        return value


class ProjectOut(BaseModel):
    id: str
    name: str
    status: str
    description: str | None
    budget_cents: int | None
    # Teile im Projekt (Zeilen, nicht Stückzahl).
    item_count: int
    # Summe Kaufpreis mal Anzahl der Teile mit Preis.
    spent_cents: int
    # Teile ohne Kaufpreis: die Summe ist dann unvollständig.
    unpriced_count: int = 0
    created_at: datetime
    updated_at: datetime


class RackIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    height_units: int = Field(default=42, ge=1, le=100)
    location_id: str | None = None
    notes: str | None = None


class RackOut(BaseModel):
    id: str
    name: str
    height_units: int
    location_id: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


def _category_out(row: Category) -> CategoryOut:
    return CategoryOut(id=row.id, name=row.name, created_at=row.created_at, updated_at=row.updated_at)


def _location_out(row: Location) -> LocationOut:
    return LocationOut(
        id=row.id, name=row.name, parent_id=row.parent_id, created_at=row.created_at, updated_at=row.updated_at
    )


def _image_out(row: ItemImage) -> ItemImageOut:
    # Vollständiger Pfad samt /api/v1 (wie ws_url/logo_url im Kern), damit das Frontend ihn unverändert abrufen
    # kann; ohne Präfix lieferte der SPA-Fallback die index.html statt des Bildes.
    return ItemImageOut(
        id=row.id, filename=row.filename, content_type=row.content_type, size_bytes=row.size_bytes,
        created_at=row.created_at, url=f"/api/v1/ext/inventory/items/{row.item_id}/images/{row.id}",
    )


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        # Der Kern prüft die Tabellennamen auf den Präfix `ext_inventory_` und weist die Erweiterung sonst ab.
        ctx.db.declare_tables(Base.metadata)

        async def _images_for(session: Any, item_id: str) -> list[ItemImageOut]:
            rows = (
                await session.execute(
                    select(ItemImage).where(ItemImage.item_id == item_id).order_by(ItemImage.created_at)
                )
            ).scalars().all()
            return [_image_out(r) for r in rows]

        async def _item_out(session: Any, row: Item) -> ItemOut:
            return ItemOut(
                id=row.id, name=row.name, description=row.description, category_id=row.category_id,
                location_id=row.location_id, quantity=row.quantity, purchase_date=row.purchase_date,
                purchase_price_cents=row.purchase_price_cents, warranty_until=row.warranty_until,
                notes=row.notes, created_at=row.created_at, updated_at=row.updated_at,
                images=await _images_for(session, row.id),
                **{field: getattr(row, field) for field in BAUKASTEN_FIELDS},
            )

        async def _check_links(session: Any, values: dict[str, Any], item_id: str | None = None) -> None:
            """Projekt, Rack und Platz im Rack prüfen, bevor ein Teil gespeichert wird. Ohne Rack gibt es keinen
            Platz darin: Position und Höhe werden dann geleert (sonst gälten alte Angaben wieder, sobald jemand
            nur `rack_id` setzt)."""
            if not values.get("rack_id"):
                values["rack_position"] = None
                values["rack_units"] = None
            if values.get("project_id") and await session.get(Project, values["project_id"]) is None:
                raise HTTPException(status_code=404, detail="Bauprojekt nicht gefunden.")
            rack_id = values.get("rack_id")
            if not rack_id:
                return
            rack = await session.get(Rack, rack_id)
            if rack is None:
                raise HTTPException(status_code=404, detail="Rack nicht gefunden.")
            position, units = values.get("rack_position"), values.get("rack_units") or 1
            if position is None:
                return  # im Rack, aber (noch) ohne festen Platz
            top = position + units - 1
            if top > rack.height_units:
                raise HTTPException(
                    status_code=422,
                    detail=f"Passt nicht: das Rack „{rack.name}“ hat {rack.height_units} HE, das Gerät reicht bis HE {top}.",
                )
            others = (
                await session.execute(
                    select(Item).where(Item.rack_id == rack_id, Item.rack_position.is_not(None), Item.id != (item_id or ""))
                )
            ).scalars().all()
            for other in others:
                other_top = (other.rack_position or 0) + (other.rack_units or 1) - 1
                if position <= other_top and (other.rack_position or 0) <= top:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Der Platz ist schon belegt: „{other.name}“ steckt dort (HE {other.rack_position}–{other_top}).",
                    )

        async def _project_out(session: Any, row: Project) -> ProjectOut:
            # In Python summiert, nicht in SQL: SQLite bricht bei einem Überlauf mit einem Fehler ab, und Altdaten
            # von vor den Obergrenzen könnten ihn noch auslösen. Ein Projekt hat wenige Teile.
            parts = (
                await session.execute(select(Item.purchase_price_cents, Item.quantity).where(Item.project_id == row.id))
            ).all()
            spent = sum(price * (quantity or 0) for price, quantity in parts if price is not None)
            unpriced = sum(1 for price, _ in parts if price is None)
            return ProjectOut(
                id=row.id, name=row.name, status=row.status, description=row.description, budget_cents=row.budget_cents,
                item_count=len(parts), spent_cents=spent, unpriced_count=unpriced,
                created_at=row.created_at, updated_at=row.updated_at,
            )

        read_router = APIRouter()
        write_router = APIRouter()

        # --- Kategorien -----------------------------------------------------
        @read_router.get("/categories")
        async def list_categories() -> list[CategoryOut]:
            async with ctx.db.session() as session:
                rows = (await session.execute(select(Category).order_by(Category.name))).scalars().all()
                return [_category_out(r) for r in rows]

        @write_router.post("/categories", status_code=status.HTTP_201_CREATED)
        async def create_category(payload: CategoryIn) -> CategoryOut:
            async with ctx.db.session() as session:
                existing = (
                    await session.execute(select(Category).where(Category.name == payload.name))
                ).scalar_one_or_none()
                if existing is not None:
                    raise HTTPException(status_code=409, detail=f"Kategorie '{payload.name}' existiert bereits.")
                row = Category(name=payload.name)
                session.add(row)
                await session.flush()
                return _category_out(row)

        @write_router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def delete_category(category_id: str) -> None:
            async with ctx.db.session() as session:
                row = await session.get(Category, category_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Kategorie nicht gefunden.")
                await session.delete(row)

        # --- Standorte --------------------------------------------------------
        @read_router.get("/locations")
        async def list_locations() -> list[LocationOut]:
            async with ctx.db.session() as session:
                rows = (await session.execute(select(Location).order_by(Location.name))).scalars().all()
                return [_location_out(r) for r in rows]

        @write_router.post("/locations", status_code=status.HTTP_201_CREATED)
        async def create_location(payload: LocationIn) -> LocationOut:
            async with ctx.db.session() as session:
                if payload.parent_id is not None and await session.get(Location, payload.parent_id) is None:
                    raise HTTPException(status_code=404, detail="Übergeordneter Standort nicht gefunden.")
                row = Location(name=payload.name, parent_id=payload.parent_id)
                session.add(row)
                await session.flush()
                return _location_out(row)

        @write_router.delete("/locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def delete_location(location_id: str) -> None:
            async with ctx.db.session() as session:
                row = await session.get(Location, location_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Standort nicht gefunden.")
                await session.delete(row)

        # --- Gegenstände --------------------------------------------------
        @read_router.get("/items")
        async def list_items(
            q: str | None = None, category_id: str | None = None, location_id: str | None = None,
            project_id: str | None = None, rack_id: str | None = None, host_id: str | None = None,
        ) -> list[ItemOut]:
            async with ctx.db.session() as session:
                stmt = select(Item).order_by(Item.name)
                if q:
                    pattern = f"%{q}%"
                    stmt = stmt.where(or_(
                        Item.name.ilike(pattern), Item.manufacturer.ilike(pattern), Item.model.ilike(pattern),
                        Item.serial_number.ilike(pattern),
                    ))
                if category_id:
                    stmt = stmt.where(Item.category_id == category_id)
                if location_id:
                    stmt = stmt.where(Item.location_id == location_id)
                if project_id:
                    stmt = stmt.where(Item.project_id == project_id)
                if rack_id:
                    stmt = stmt.where(Item.rack_id == rack_id)
                if host_id:
                    stmt = stmt.where(Item.host_id == host_id)
                rows = (await session.execute(stmt)).scalars().all()
                return [await _item_out(session, r) for r in rows]

        @write_router.post("/items", status_code=status.HTTP_201_CREATED)
        async def create_item(payload: ItemIn) -> ItemOut:
            async with ctx.db.session() as session:
                values = payload.model_dump()
                await _check_links(session, values)
                row = Item(**values)
                session.add(row)
                await session.flush()
                return await _item_out(session, row)

        @read_router.get("/items/{item_id}")
        async def get_item(item_id: str) -> ItemOut:
            async with ctx.db.session() as session:
                row = await session.get(Item, item_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Gegenstand nicht gefunden.")
                return await _item_out(session, row)

        @write_router.put("/items/{item_id}")
        async def update_item(item_id: str, payload: ItemIn) -> ItemOut:
            async with ctx.db.session() as session:
                row = await session.get(Item, item_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Gegenstand nicht gefunden.")
                values = payload.model_dump()
                # Neue Felder nur, wenn mitgeschickt (siehe ItemIn); die alten wie bisher immer.
                for field in BAUKASTEN_FIELDS:
                    if field not in payload.model_fields_set:
                        values[field] = getattr(row, field)
                await _check_links(session, values, item_id=row.id)
                for field, value in values.items():
                    setattr(row, field, value)
                await session.flush()
                return await _item_out(session, row)

        @write_router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def delete_item(item_id: str) -> None:
            async with ctx.db.session() as session:
                row = await session.get(Item, item_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Gegenstand nicht gefunden.")
                # Die Bilddateien müssen explizit weg: die Datenbankzeilen kaskadieren beim Löschen über den
                # Fremdschlüssel (ondelete="CASCADE", PRAGMA foreign_keys=ON), die Dateien auf der Platte nicht.
                image_rows = (
                    await session.execute(select(ItemImage).where(ItemImage.item_id == item_id))
                ).scalars().all()
                for image_row in image_rows:
                    delete_image_file(ctx.data_dir, image_row.filename)
                await session.delete(row)
            # Nach dem Löschen (die Sitzung ist zu): andere Erweiterungen räumen ihre Verweise auf, etwa die
            # Dokumente ihre Verknüpfung mit diesem Gegenstand.
            await ctx.events.publish(Event(name="inventory.item_deleted", payload={"item_id": item_id}))

        # --- Bauprojekte ---------------------------------------------------------
        @read_router.get("/projects")
        async def list_projects() -> list[ProjectOut]:
            async with ctx.db.session() as session:
                rows = (await session.execute(select(Project).order_by(Project.name))).scalars().all()
                return [await _project_out(session, r) for r in rows]

        @write_router.post("/projects", status_code=status.HTTP_201_CREATED)
        async def create_project(payload: ProjectIn) -> ProjectOut:
            async with ctx.db.session() as session:
                row = Project(**payload.model_dump())
                session.add(row)
                await session.flush()
                return await _project_out(session, row)

        @write_router.put("/projects/{project_id}")
        async def update_project(project_id: str, payload: ProjectIn) -> ProjectOut:
            async with ctx.db.session() as session:
                row = await session.get(Project, project_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Bauprojekt nicht gefunden.")
                for field, value in payload.model_dump().items():
                    setattr(row, field, value)
                await session.flush()
                return await _project_out(session, row)

        @write_router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def delete_project(project_id: str) -> None:
            """Die Teile bleiben im Inventar, sie gehören danach nur zu keinem Projekt mehr."""
            async with ctx.db.session() as session:
                row = await session.get(Project, project_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Bauprojekt nicht gefunden.")
                await session.execute(update(Item).where(Item.project_id == project_id).values(project_id=None))
                await session.delete(row)

        # --- Racks -------------------------------------------------------------
        @read_router.get("/racks")
        async def list_racks() -> list[RackOut]:
            async with ctx.db.session() as session:
                rows = (await session.execute(select(Rack).order_by(Rack.name))).scalars().all()
                return [RackOut.model_validate(r, from_attributes=True) for r in rows]

        @write_router.post("/racks", status_code=status.HTTP_201_CREATED)
        async def create_rack(payload: RackIn) -> RackOut:
            async with ctx.db.session() as session:
                if payload.location_id is not None and await session.get(Location, payload.location_id) is None:
                    raise HTTPException(status_code=404, detail="Standort nicht gefunden.")
                row = Rack(**payload.model_dump())
                session.add(row)
                await session.flush()
                return RackOut.model_validate(row, from_attributes=True)

        @write_router.put("/racks/{rack_id}")
        async def update_rack(rack_id: str, payload: RackIn) -> RackOut:
            async with ctx.db.session() as session:
                row = await session.get(Rack, rack_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Rack nicht gefunden.")
                if payload.location_id is not None and await session.get(Location, payload.location_id) is None:
                    raise HTTPException(status_code=404, detail="Standort nicht gefunden.")
                tallest = (
                    await session.execute(
                        select(func.max(Item.rack_position + func.coalesce(Item.rack_units, 1) - 1)).where(
                            Item.rack_id == rack_id, Item.rack_position.is_not(None)
                        )
                    )
                ).scalar()
                if tallest and tallest > payload.height_units:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Das Rack lässt sich nicht auf {payload.height_units} HE verkleinern: ein Gerät steckt bis HE {tallest}.",
                    )
                for field, value in payload.model_dump().items():
                    setattr(row, field, value)
                await session.flush()
                return RackOut.model_validate(row, from_attributes=True)

        @write_router.delete("/racks/{rack_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def delete_rack(rack_id: str) -> None:
            """Die Geräte bleiben im Inventar, sie stecken danach nur in keinem Rack mehr."""
            async with ctx.db.session() as session:
                row = await session.get(Rack, rack_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Rack nicht gefunden.")
                await session.execute(
                    update(Item).where(Item.rack_id == rack_id).values(rack_id=None, rack_position=None, rack_units=None)
                )
                await session.delete(row)

        # --- Teile eines Servers auslesen ---------------------------------------
        # Eigener Router mit dem Recht `hosts.execute`: der Scan führt einen Befehl auf dem Server aus. Wer nur
        # Inventar-Rechte hat, darf das nicht (angelegt wird danach ganz normal über `POST /items`).
        scan_router = APIRouter()

        @scan_router.post("/hosts/{host_id}/scan")
        async def scan_host(host_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:  # noqa: B008
            """Liest per SSH (nur lesend, `hardware.SCAN_COMMAND`), welche Teile im Server stecken, und gibt
            Vorschläge zurück. Angelegt wird nichts: das macht die Seite mit dem, was der Nutzer auswählt."""
            host = await ctx.hosts.get(host_id)
            if host is None:
                raise HTTPException(status_code=404, detail="Server nicht gefunden.")
            if host.os_family != "linux":
                raise HTTPException(status_code=422, detail="Auslesen geht nur bei Linux-Servern.")
            if not host.has_credential:
                raise HTTPException(
                    status_code=422,
                    detail="Für diesen Server ist kein SSH-Zugang hinterlegt. Richte ihn unter Einstellungen → Server & Zugänge ein.",
                )
            name = host.display_name or host.name
            try:
                result = await ctx.exec.run(host, SCAN_COMMAND, timeout_s=30)
            except Exception as exc:  # nicht erreichbar oder abgelehnt: der Grund gehört in die Antwort
                await _audit_scan(actor, host.id, name, "failure", type(exc).__name__)
                raise HTTPException(status_code=502, detail=f"Der Server ließ sich nicht auslesen: {_scan_error_text(exc)}") from exc
            stdout = result.stdout or ""
            # Das Skript endet immer mit Code 0 und beginnt mit „@@dmi“. Fehlt das, lief es gar nicht (eingeschränkte
            # Shell, erzwungener Befehl des Kontos): dann ein Fehler statt „Nichts gefunden“.
            if result.exit_code != 0 or "@@dmi" not in stdout[:4000]:
                reason = (
                    _short(result.stderr) or _short(stdout[:4000])
                    or (f"Der Befehl endete mit Code {result.exit_code}." if result.exit_code else "Der Server hat nichts geantwortet.")
                )
                await _audit_scan(
                    actor, host.id, name, "failure", f"exit {result.exit_code}" if result.exit_code else "keine Ausgabe des Skripts",
                )
                raise HTTPException(status_code=502, detail=f"Der Server ließ sich nicht auslesen: {reason}")
            try:
                # Im eigenen Thread: die Antwort kommt vom Server, ihr Zerlegen soll das Dashboard nie aufhalten.
                parsed = await asyncio.to_thread(parse_scan, stdout)
            except Exception as exc:  # parse_scan wirft nicht; falls doch: verständlich statt 500
                _log.warning("Ausgabe beim Auslesen von %s nicht lesbar (%s)", name, type(exc).__name__)
                await _audit_scan(actor, host.id, name, "failure", "Antwort nicht lesbar")
                raise HTTPException(status_code=502, detail="Die Antwort des Servers ließ sich nicht lesen.") from exc
            # Eine VM oder ein Container zeigt oft die Teile des Wirts: dann nichts vorab ankreuzen.
            if host.kind in ("vm", "lxc") and not parsed["virtual"]:
                parsed["virtual"] = True
                for suggestion in parsed["suggestions"]:
                    suggestion["preselect"] = False
            await _audit_scan(actor, host.id, name, "success", f"{len(parsed['suggestions'])} Vorschläge")
            return {"host_id": host.id, "host_name": name, **parsed}

        async def _audit_scan(actor: Actor, host_id: str, host_name: str, outcome: str, reason: str) -> None:
            try:
                await ctx.audit.log(
                    action="inventory.hardware_scan", outcome=outcome, target_type="host", target_id=host_id,
                    reason=reason, detail={"host_name": host_name}, actor=actor,
                )
            except Exception:  # noqa: BLE001 - der Protokolleintrag darf den Scan nicht scheitern lassen
                _log.warning("Protokolleintrag für das Auslesen von %s fehlgeschlagen", host_name, exc_info=True)

        # --- Bilder -----------------------------------------------------------
        @write_router.post("/items/{item_id}/images", status_code=status.HTTP_201_CREATED)
        @max_body_bytes(MAX_IMAGE_BYTES)
        async def upload_item_image(item_id: str, request: Request) -> ItemImageOut:
            """Rohkörper-Upload wie `POST /branding/logo`, dieselbe Konvention statt `multipart/form-data`."""
            content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
            extension = ALLOWED_CONTENT_TYPES.get(content_type)
            if extension is None:
                raise HTTPException(status_code=415, detail=f"Nicht unterstützter Bildtyp: {content_type or '(keiner)'}")

            # Erst die Angabe im Kopf prüfen, dann die tatsächliche Größe: sie kann fehlen oder falsch sein.
            content_length = request.headers.get("content-length")
            if content_length is not None and content_length.isdigit() and int(content_length) > MAX_IMAGE_BYTES:
                raise HTTPException(status_code=413, detail=f"Bild zu gross (max. {MAX_IMAGE_BYTES // 1024} KB).")
            content = await request.body()
            if len(content) > MAX_IMAGE_BYTES:
                raise HTTPException(status_code=413, detail=f"Bild zu gross (max. {MAX_IMAGE_BYTES // 1024} KB).")

            async with ctx.db.session() as session:
                item = await session.get(Item, item_id)
                if item is None:
                    raise HTTPException(status_code=404, detail="Gegenstand nicht gefunden.")
                row = ItemImage(item_id=item_id, filename="", content_type=content_type, size_bytes=len(content))
                session.add(row)
                await session.flush()
                row.filename = save_image(ctx.data_dir, row.id, extension, content)
                await session.flush()
                return _image_out(row)

        @read_router.get("/items/{item_id}/images/{image_id}")
        async def get_item_image(item_id: str, image_id: str) -> Response:
            async with ctx.db.session() as session:
                row = await session.get(ItemImage, image_id)
                if row is None or row.item_id != item_id:
                    raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
                from .images import image_path

                path = image_path(ctx.data_dir, row.filename)
                if not path.is_file():
                    raise HTTPException(status_code=404, detail="Bilddatei fehlt auf der Platte.")
                return Response(
                    content=path.read_bytes(), media_type=row.content_type, headers=_UNTRUSTED_FILE_HEADERS,
                )

        @write_router.delete("/items/{item_id}/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def delete_item_image(item_id: str, image_id: str) -> None:
            async with ctx.db.session() as session:
                row = await session.get(ItemImage, image_id)
                if row is None or row.item_id != item_id:
                    raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
                delete_image_file(ctx.data_dir, row.filename)
                await session.delete(row)

        ctx.api.include_router(read_router, permission="inventory.read")
        ctx.api.include_router(write_router, permission="inventory.write")
        ctx.api.include_router(scan_router, permission="hosts.execute")

        # --- Widget: bald ablaufende Garantien --------------------------------
        widget_router = APIRouter()

        @widget_router.get("/widgets/warranty")
        async def warranty_widget_data() -> dict[str, Any]:
            today = date.today()
            cutoff = today + timedelta(days=_WARRANTY_SOON_DAYS)
            oldest = today - timedelta(days=_WARRANTY_EXPIRED_SHOWN_DAYS)
            async with ctx.db.session() as session:
                rows = (
                    await session.execute(
                        select(Item)
                        .where(
                            Item.warranty_until.is_not(None),
                            Item.warranty_until <= cutoff,
                            Item.warranty_until >= oldest,
                        )
                        .order_by(Item.warranty_until)
                    )
                ).scalars().all()
                # Zuerst, was noch abläuft (nächstes Ende oben), da kann man noch etwas tun; danach das kürzlich
                # Abgelaufene (neuestes zuerst).
                rows = sorted(rows, key=lambda r: (r.warranty_until < today, abs((r.warranty_until - today).days)))
                return {
                    "data": [
                        {
                            "id": r.id, "name": r.name, "warranty_until": r.warranty_until.isoformat(),
                            "status": "abgelaufen" if r.warranty_until < today else "läuft bald ab",
                            # `tone` ist schon ein gültiges Tone-Literal (renderTone() reicht es durch, kein
                            # "| tone"-Ratefilter nötig, siehe frontend/src/widgets/template.ts).
                            "tone": "danger" if r.warranty_until < today else "warn",
                        }
                        for r in rows
                    ],
                    "meta": {},
                }

        # Mit Berechtigung einhängen: ohne wäre der Router öffentlich (siehe `ApiHandle.include_router()`), und die
        # Namen wären ohne Login lesbar.
        ctx.api.include_router(widget_router, permission="inventory.read")

        ctx.ui.register_page(
            PageSpec(
                id="inventory",
                path="/inventory",
                title="Inventar",
                icon="package",
                nav_section="Inventar",
                nav_order=10,
                component="InventoryPage",
            )
        )

        ctx.ui.register_widget(
            WidgetSpec(
                id="warranty",
                title="Garantie läuft bald ab",
                icon="shield-alert",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=300),
                data_endpoint="widgets/warranty",
                permissions=["inventory.read"],
                view=ListView(
                    item=ListItem(
                        title="{{ name }}",
                        subtitle="{{ warranty_until }}",
                        badge=Badge(text="{{ status }}", tone="{{ tone }}"),
                    ),
                    empty_text=f"Keine Garantie läuft in den nächsten {_WARRANTY_SOON_DAYS} Tagen ab",
                ),
            )
        )

        # Andere Erweiterungen (die Dokumente) fragen nach, ob Gegenstände, auf die sie verweisen, noch da sind: etwa
        # wenn sie aus waren, während hier etwas gelöscht wurde. Für jede Kennung, die es nicht mehr gibt, kommt
        # dieselbe Meldung wie beim Löschen. Bewusst nur Kennungen, keine Namen: Ereignisse mit `inventory.` gehen
        # wie `inventory.item_deleted` an alle Angemeldeten (es gibt kein eigenes Recht für dieses Präfix).
        async def _on_items_check(event: Event) -> None:
            raw = event.payload.get("item_ids")
            if not isinstance(raw, list):
                return
            ids = {i for i in raw[:MAX_ITEMS_PER_CHECK] if isinstance(i, str) and _ITEM_ID.fullmatch(i)}
            if not ids:
                return
            async with ctx.db.session() as session:
                present = set((await session.execute(select(Item.id).where(Item.id.in_(ids)))).scalars())
            for item_id in sorted(ids - present):
                await ctx.events.publish(Event(name="inventory.item_deleted", payload={"item_id": item_id}))

        ctx.events.subscribe("inventory.items_check_requested", _on_items_check)

    async def on_start(self, ctx: ExtensionContext) -> None:
        # Wer beim eigenen Start noch nicht nachfragen konnte (Inventar lud später), fragt jetzt. Ein Fehler dabei
        # betrifft nur das Aufräumen bei den anderen, nie den Start des Inventars.
        try:
            await ctx.events.publish(Event(name="inventory.started", payload={}))
        except Exception:  # noqa: BLE001 - nur Aufräumen bei anderen, nie den eigenen Start kippen
            _log.warning("Meldung „Inventar gestartet“ gescheitert", exc_info=True)

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        async with ctx.db.session() as session:
            count = (await session.execute(select(Item))).scalars().all()
            return HealthReport(healthy=True, message=f"{len(count)} Gegenstand/Gegenstände erfasst.")
