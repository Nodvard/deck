"""inventory-Extension -- Homebox-artiges Zuhause-Inventar: Gegenstaende, Standorte,
Kategorien, Garantie-Datum, Bilder pro Gegenstand.

**Die erste Extension mit echten eigenen Datenbank-Tabellen** (docs/02-EXTENSION-API.md
§7: "eigener Alembic-Branch") -- `models.py` deklariert eine EIGENE, vom Kern
unabhaengige `Base`/`MetaData`, `migrations/versions/
a1b2c3d4e5f6_create_inventory_tables.py` ist ein eigener, vom Kern-Branch
unabhaengiger Alembic-Kopf (`down_revision=None`, eigener `branch_labels`-Wert).
`python -m nodvard_deck.migrate` (backend/src/nodvard_deck/migrate.py) findet und migriert
beide Branches zusammen ("upgrade heads"). `ctx.db.declare_tables(Base.metadata)`
unten aktiviert den bisher nie aufgerufenen `ext.tables.validate_table_prefix()`
zum ersten Mal gegen echten Code -- ein Tabellenname ohne `ext_inventory_`-Praefix
wuerde die Extension beim Laden zurueckweisen.

Bilder liegen NICHT in der Datenbank, sondern auf der Platte unter
`ctx.data_dir/images/` (siehe images.py) -- dasselbe Muster wie
`nodvard_deck.branding.save_logo_file()`, `ItemImage` traegt nur Metadaten.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from nodvard_sdk import (
    Badge,
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
from pydantic import BaseModel
from sqlalchemy import select

from .images import ALLOWED_CONTENT_TYPES, MAX_IMAGE_BYTES, delete_image_file, save_image
from .models import Base, Category, Item, ItemImage, Location

# Die Bilder kommen von Nutzern. Wird die Adresse direkt geöffnet, soll weder ein Skript laufen
# noch der Browser den Typ erraten. Als <img> eingebettet ändert sich nichts.
_UNTRUSTED_FILE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
}

_WARRANTY_SOON_DAYS = 90
"""Ab wie vielen Tagen vor Ablauf ein Gegenstand im Garantie-Widget auftaucht --
kein Vertragswert, nur ein sinnvoller Default. Muss zur Inventar-Seite passen
(`warrantyState()` in InventoryPage.tsx: <= 90 Tage = "endet bald"); vorher 30,
dann zeigte die Seite "Garantie endet bald: 4" und die Kachel nur einen."""

_WARRANTY_EXPIRED_SHOWN_DAYS = 30
"""Wie lange eine abgelaufene Garantie noch in der Kachel steht. Ohne Untergrenze
fuellten vor Jahren abgelaufene Geraete die 2x2-Kachel, und die gerade ablaufenden
waren abgeschnitten. Die Inventar-Seite zeigt weiterhin alle."""


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


class ItemIn(BaseModel):
    name: str
    description: str | None = None
    category_id: str | None = None
    location_id: str | None = None
    quantity: int = 1
    purchase_date: date | None = None
    purchase_price_cents: int | None = None
    warranty_until: date | None = None
    notes: str | None = None


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


def _category_out(row: Category) -> CategoryOut:
    return CategoryOut(id=row.id, name=row.name, created_at=row.created_at, updated_at=row.updated_at)


def _location_out(row: Location) -> LocationOut:
    return LocationOut(
        id=row.id, name=row.name, parent_id=row.parent_id, created_at=row.created_at, updated_at=row.updated_at
    )


def _image_out(row: ItemImage) -> ItemImageOut:
    # Vollstaendiger Pfad inkl. /api/v1 (wie ws_url/logo_url im Kern), damit das
    # Frontend ihn unveraendert abrufen kann -- ohne Praefix lieferte der
    # SPA-Fallback die index.html statt des Bildes.
    return ItemImageOut(
        id=row.id, filename=row.filename, content_type=row.content_type, size_bytes=row.size_bytes,
        created_at=row.created_at, url=f"/api/v1/ext/inventory/items/{row.item_id}/images/{row.id}",
    )


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
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

        # --- Gegenstaende -------------------------------------------------
        @read_router.get("/items")
        async def list_items(
            q: str | None = None, category_id: str | None = None, location_id: str | None = None,
        ) -> list[ItemOut]:
            async with ctx.db.session() as session:
                stmt = select(Item).order_by(Item.name)
                if q:
                    stmt = stmt.where(Item.name.ilike(f"%{q}%"))
                if category_id:
                    stmt = stmt.where(Item.category_id == category_id)
                if location_id:
                    stmt = stmt.where(Item.location_id == location_id)
                rows = (await session.execute(stmt)).scalars().all()
                return [await _item_out(session, r) for r in rows]

        @write_router.post("/items", status_code=status.HTTP_201_CREATED)
        async def create_item(payload: ItemIn) -> ItemOut:
            async with ctx.db.session() as session:
                row = Item(**payload.model_dump())
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
                for field, value in payload.model_dump().items():
                    setattr(row, field, value)
                await session.flush()
                return await _item_out(session, row)

        @write_router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def delete_item(item_id: str) -> None:
            async with ctx.db.session() as session:
                row = await session.get(Item, item_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Gegenstand nicht gefunden.")
                # Bild-DATEIEN muessen explizit weg -- die DB-Zeilen kaskadieren beim
                # Loeschen der Item-Zeile ueber die FK (ondelete="CASCADE",
                # PRAGMA foreign_keys=ON), die Dateien auf der Platte nicht.
                image_rows = (
                    await session.execute(select(ItemImage).where(ItemImage.item_id == item_id))
                ).scalars().all()
                for image_row in image_rows:
                    delete_image_file(ctx.data_dir, image_row.filename)
                await session.delete(row)

        # --- Bilder -----------------------------------------------------------
        @write_router.post("/items/{item_id}/images", status_code=status.HTTP_201_CREATED)
        @max_body_bytes(MAX_IMAGE_BYTES)
        async def upload_item_image(item_id: str, request: Request) -> ItemImageOut:
            """Rohkoerper-Upload wie `POST /branding/logo` -- dieselbe Konvention
            statt zusaetzlich `multipart/form-data` einzufuehren."""
            content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
            extension = ALLOWED_CONTENT_TYPES.get(content_type)
            if extension is None:
                raise HTTPException(status_code=415, detail=f"Nicht unterstützter Bildtyp: {content_type or '(keiner)'}")

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
                # Zuerst, was noch ablaeuft (naechstes Ende oben) -- da kann man noch
                # etwas tun --, danach das kuerzlich Abgelaufene (neuestes zuerst).
                rows = sorted(rows, key=lambda r: (r.warranty_until < today, abs((r.warranty_until - today).days)))
                return {
                    "data": [
                        {
                            "id": r.id, "name": r.name, "warranty_until": r.warranty_until.isoformat(),
                            "status": "abgelaufen" if r.warranty_until < today else "läuft bald ab",
                            # `tone` ist bereits ein gueltiger Tone-Literal (renderTone()
                            # reicht ihn direkt durch, kein "| tone"-Ratefilter noetig,
                            # siehe frontend/src/widgets/template.ts).
                            "tone": "danger" if r.warranty_until < today else "warn",
                        }
                        for r in rows
                    ],
                    "meta": {},
                }

        # Sicherheits-Nachtrag: hing ohne Berechtigung (= oeffentlich, siehe
        # ApiHandle.include_router()) -- Namen waren ohne Login lesbar.
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

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        async with ctx.db.session() as session:
            count = (await session.execute(select(Item))).scalars().all()
            return HealthReport(healthy=True, message=f"{len(count)} Gegenstand/Gegenstände erfasst.")
