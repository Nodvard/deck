"""inventory-Extension -- Homebox-artiges Zuhause-Inventar.
**Die erste Extension mit echten
eigenen Datenbank-Tabellen** (docs/02-EXTENSION-API.md §7, eigener Alembic-Branch) --
dieser Testlauf erschafft die `ext_inventory_*`-Tabellen deshalb selbst in der
In-Memory-Test-DB (`_create_inventory_tables` unten), genau wie `conftest.py`s
`db_session`-Fixture es fuer die Kern-Tabellen tut. Der ECHTE Migrationsweg
(`python -m nodvard_deck.migrate`, zwei unabhaengige Alembic-Koepfe in einer
`alembic_version`-Tabelle) ist separat in `test_migrate.py` bewiesen -- dort gegen
eine echte Datei-DB, hier gegen die schnelle In-Memory-Fixture fuer die eigentliche
API-/Fachlogik.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nodvard_deck.services import extensions as extensions_service

SRC = Path(__file__).resolve().parents[2] / "extensions" / "inventory" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nodvard_deck_ext_inventory.models import Base as InventoryBase  # noqa: E402

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


@pytest.fixture(autouse=True)
async def _create_inventory_tables(db_session):
    """`db_session` (conftest.py) erzeugt nur `nodvard_deck.models.Base.metadata` --
    dieselbe In-Memory-Engine braucht zusaetzlich `InventoryBase.metadata`, sonst
    schlaegt jeder echte Request mit "no such table: ext_inventory_items" fehl."""
    conn = await db_session.connection()
    await conn.run_sync(InventoryBase.metadata.create_all)


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _enable_inventory(client, db_session, test_settings) -> str:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    enabled = await client.post("/api/v1/extensions/inventory/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text
    return token


@pytest.mark.asyncio
async def test_inventory_page_is_registered_after_enabling(client, db_session, test_settings):
    token = await _enable_inventory(client, db_session, test_settings)
    pages = await client.get("/api/v1/pages", headers=_auth_header(token))
    assert pages.status_code == 200
    page = next(p for p in pages.json() if p["ext_id"] == "inventory")
    assert page["path"] == "/inventory"
    assert page["component"] == "InventoryPage"

    bundle = await client.get("/api/v1/extensions/inventory/frontend/index.js")
    assert bundle.status_code == 200
    assert "InventoryPage" in bundle.text


@pytest.mark.asyncio
async def test_category_crud_round_trip(client, db_session, test_settings):
    token = await _enable_inventory(client, db_session, test_settings)
    headers = _auth_header(token)

    empty = await client.get("/api/v1/ext/inventory/categories", headers=headers)
    assert empty.status_code == 200, empty.text
    assert empty.json() == []

    created = await client.post("/api/v1/ext/inventory/categories", json={"name": "Elektronik"}, headers=headers)
    assert created.status_code == 201, created.text
    cat_id = created.json()["id"]
    assert created.json()["name"] == "Elektronik"

    listed = await client.get("/api/v1/ext/inventory/categories", headers=headers)
    assert [c["name"] for c in listed.json()] == ["Elektronik"]

    deleted = await client.delete(f"/api/v1/ext/inventory/categories/{cat_id}", headers=headers)
    assert deleted.status_code == 204, deleted.text
    after = await client.get("/api/v1/ext/inventory/categories", headers=headers)
    assert after.json() == []


@pytest.mark.asyncio
async def test_duplicate_category_name_is_rejected(client, db_session, test_settings):
    token = await _enable_inventory(client, db_session, test_settings)
    headers = _auth_header(token)

    first = await client.post("/api/v1/ext/inventory/categories", json={"name": "Werkzeug"}, headers=headers)
    assert first.status_code == 201
    second = await client.post("/api/v1/ext/inventory/categories", json={"name": "Werkzeug"}, headers=headers)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_location_hierarchy_round_trip(client, db_session, test_settings):
    token = await _enable_inventory(client, db_session, test_settings)
    headers = _auth_header(token)

    garage = await client.post("/api/v1/ext/inventory/locations", json={"name": "Garage"}, headers=headers)
    assert garage.status_code == 201, garage.text
    shelf = await client.post(
        "/api/v1/ext/inventory/locations", json={"name": "Regal 1", "parent_id": garage.json()["id"]},
        headers=headers,
    )
    assert shelf.status_code == 201, shelf.text
    assert shelf.json()["parent_id"] == garage.json()["id"]


@pytest.mark.asyncio
async def test_create_location_with_unknown_parent_returns_404(client, db_session, test_settings):
    token = await _enable_inventory(client, db_session, test_settings)
    res = await client.post(
        "/api/v1/ext/inventory/locations", json={"name": "X", "parent_id": "does-not-exist"},
        headers=_auth_header(token),
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_item_full_crud_with_category_and_location(client, db_session, test_settings):
    token = await _enable_inventory(client, db_session, test_settings)
    headers = _auth_header(token)

    cat = await client.post("/api/v1/ext/inventory/categories", json={"name": "Elektronik"}, headers=headers)
    loc = await client.post("/api/v1/ext/inventory/locations", json={"name": "Keller"}, headers=headers)

    created = await client.post(
        "/api/v1/ext/inventory/items",
        json={
            "name": "Bohrmaschine", "description": "Bosch, gruen", "category_id": cat.json()["id"],
            "location_id": loc.json()["id"], "quantity": 2, "purchase_date": "2024-01-15",
            "purchase_price_cents": 8999, "warranty_until": "2027-01-15", "notes": "Quittung im Ordner",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    item_id = body["id"]
    assert body["name"] == "Bohrmaschine"
    assert body["quantity"] == 2
    assert body["warranty_until"] == "2027-01-15"
    assert body["purchase_price_cents"] == 8999
    assert body["images"] == []

    fetched = await client.get(f"/api/v1/ext/inventory/items/{item_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Bohrmaschine"

    updated = await client.put(
        f"/api/v1/ext/inventory/items/{item_id}",
        json={"name": "Bohrmaschine (defekt)", "quantity": 1},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Bohrmaschine (defekt)"
    assert updated.json()["quantity"] == 1
    # PUT ist ein voller Ersatz (wie proxmox' Connection-PATCH bewusst NICHT ist) --
    # nicht mitgeschickte Felder fallen auf ItemIn's Defaults zurueck.
    assert updated.json()["category_id"] is None

    deleted = await client.delete(f"/api/v1/ext/inventory/items/{item_id}", headers=headers)
    assert deleted.status_code == 204
    missing = await client.get(f"/api/v1/ext/inventory/items/{item_id}", headers=headers)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_get_unknown_item_returns_404(client, db_session, test_settings):
    token = await _enable_inventory(client, db_session, test_settings)
    res = await client.get("/api/v1/ext/inventory/items/does-not-exist", headers=_auth_header(token))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_items_search_and_filter(client, db_session, test_settings):
    token = await _enable_inventory(client, db_session, test_settings)
    headers = _auth_header(token)
    cat = await client.post("/api/v1/ext/inventory/categories", json={"name": "Buecher"}, headers=headers)

    await client.post("/api/v1/ext/inventory/items", json={"name": "Python Kochbuch", "category_id": cat.json()["id"]}, headers=headers)
    await client.post("/api/v1/ext/inventory/items", json={"name": "Staubsauger"}, headers=headers)

    by_name = await client.get("/api/v1/ext/inventory/items?q=Python", headers=headers)
    assert [i["name"] for i in by_name.json()] == ["Python Kochbuch"]

    by_category = await client.get(f"/api/v1/ext/inventory/items?category_id={cat.json()['id']}", headers=headers)
    assert [i["name"] for i in by_category.json()] == ["Python Kochbuch"]

    all_items = await client.get("/api/v1/ext/inventory/items", headers=headers)
    assert {i["name"] for i in all_items.json()} == {"Python Kochbuch", "Staubsauger"}


@pytest.mark.asyncio
async def test_item_image_upload_serve_and_delete_round_trip(client, db_session, test_settings):
    token = await _enable_inventory(client, db_session, test_settings)
    headers = _auth_header(token)
    item = await client.post("/api/v1/ext/inventory/items", json={"name": "Kamera"}, headers=headers)
    item_id = item.json()["id"]

    png_bytes = b"\x89PNG\r\n\x1a\nnot-a-real-png-but-good-enough-for-a-round-trip-test"
    uploaded = await client.post(
        f"/api/v1/ext/inventory/items/{item_id}/images",
        content=png_bytes, headers={**headers, "Content-Type": "image/png"},
    )
    assert uploaded.status_code == 201, uploaded.text
    image_id = uploaded.json()["id"]
    assert uploaded.json()["content_type"] == "image/png"
    assert uploaded.json()["size_bytes"] == len(png_bytes)

    fetched_item = await client.get(f"/api/v1/ext/inventory/items/{item_id}", headers=headers)
    assert len(fetched_item.json()["images"]) == 1
    assert fetched_item.json()["images"][0]["id"] == image_id

    # Die ausgelieferte `url` muss direkt abrufbar sein (mit /api/v1-Praefix,
    # wie ws_url/logo_url im Kern) -- vorher fehlte das Praefix und der Browser bekam
    # vom SPA-Fallback die index.html statt des Bildes.
    image_url = fetched_item.json()["images"][0]["url"]
    assert image_url == f"/api/v1/ext/inventory/items/{item_id}/images/{image_id}"
    assert uploaded.json()["url"] == image_url

    served = await client.get(image_url, headers=headers)
    assert served.status_code == 200
    assert served.content == png_bytes
    assert served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in served.headers["content-security-policy"]

    deleted = await client.delete(f"/api/v1/ext/inventory/items/{item_id}/images/{image_id}", headers=headers)
    assert deleted.status_code == 204
    gone = await client.get(f"/api/v1/ext/inventory/items/{item_id}/images/{image_id}", headers=headers)
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_content_type(client, db_session, test_settings):
    token = await _enable_inventory(client, db_session, test_settings)
    headers = _auth_header(token)
    item = await client.post("/api/v1/ext/inventory/items", json={"name": "X"}, headers=headers)

    res = await client.post(
        f"/api/v1/ext/inventory/items/{item.json()['id']}/images",
        content=b"whatever", headers={**headers, "Content-Type": "application/pdf"},
    )
    assert res.status_code == 415


@pytest.mark.asyncio
async def test_deleting_an_item_removes_its_image_file_from_disk(client, db_session, test_settings, tmp_path):
    """Die DB-Zeile kaskadiert beim Loeschen ueber die FK (ondelete=CASCADE), die
    Bild-DATEI auf der Platte nicht automatisch -- delete_item() muss das explizit
    tun. Prueft das gegen den echten `ctx.data_dir`, nicht nur die DB-Zeile."""
    from nodvard_deck_ext_inventory.images import images_dir

    token = await _enable_inventory(client, db_session, test_settings)
    headers = _auth_header(token)
    item = await client.post("/api/v1/ext/inventory/items", json={"name": "Y"}, headers=headers)
    item_id = item.json()["id"]

    uploaded = await client.post(
        f"/api/v1/ext/inventory/items/{item_id}/images",
        content=b"bild-inhalt", headers={**headers, "Content-Type": "image/png"},
    )
    assert uploaded.status_code == 201

    ext_data_dir = test_settings.ext_data_dir / "inventory"
    files_before = list(images_dir(ext_data_dir).glob("*"))
    assert len(files_before) == 1

    deleted = await client.delete(f"/api/v1/ext/inventory/items/{item_id}", headers=headers)
    assert deleted.status_code == 204

    files_after = list(images_dir(ext_data_dir).glob("*"))
    assert files_after == []


@pytest.mark.asyncio
async def test_warranty_widget_lists_only_soon_or_already_expired_items(client, db_session, test_settings):
    import datetime as dt

    token = await _enable_inventory(client, db_session, test_settings)
    headers = _auth_header(token)
    today = dt.date.today()

    await client.post(
        "/api/v1/ext/inventory/items",
        json={"name": "Laptop (bald abgelaufen)", "warranty_until": (today + dt.timedelta(days=5)).isoformat()},
        headers=headers,
    )
    await client.post(
        "/api/v1/ext/inventory/items",
        json={"name": "Drucker (schon abgelaufen)", "warranty_until": (today - dt.timedelta(days=2)).isoformat()},
        headers=headers,
    )
    await client.post(
        "/api/v1/ext/inventory/items",
        json={"name": "Fernseher (weit in der Zukunft)", "warranty_until": (today + dt.timedelta(days=400)).isoformat()},
        headers=headers,
    )
    await client.post("/api/v1/ext/inventory/items", json={"name": "Ohne Garantie-Datum"}, headers=headers)

    widget = await client.get("/api/v1/ext/inventory/widgets/warranty", headers=headers)
    assert widget.status_code == 200, widget.text
    names_and_tones = {row["name"]: row["tone"] for row in widget.json()["data"]}
    assert names_and_tones == {
        "Laptop (bald abgelaufen)": "warn",
        "Drucker (schon abgelaufen)": "danger",
    }


@pytest.mark.asyncio
async def test_warranty_widget_uses_page_threshold_and_hides_long_expired(client, db_session, test_settings):
    """Die Kachel rechnete mit 30 Tagen (Seite: 90) und ohne Untergrenze --
    vor Jahren abgelaufene Garantien standen oben, die gerade ablaufenden waren
    abgeschnitten. Jetzt: dieselben 90 Tage wie die Seite, Abgelaufenes nur noch aus den
    letzten 30 Tagen, und was noch ablaeuft steht zuerst."""
    import datetime as dt

    from nodvard_deck_ext_inventory import _WARRANTY_SOON_DAYS

    token = await _enable_inventory(client, db_session, test_settings)
    headers = _auth_header(token)
    today = dt.date.today()

    for name, days in (
        ("Router (vor Jahren abgelaufen)", -400),
        ("NAS (letzte Woche abgelaufen)", -7),
        ("Monitor (in 60 Tagen)", 60),
        ("Tastatur (in 10 Tagen)", 10),
    ):
        created = await client.post(
            "/api/v1/ext/inventory/items",
            json={"name": name, "warranty_until": (today + dt.timedelta(days=days)).isoformat()},
            headers=headers,
        )
        assert created.status_code == 201, created.text

    widget = await client.get("/api/v1/ext/inventory/widgets/warranty", headers=headers)
    assert widget.status_code == 200, widget.text
    rows = [(row["name"], row["tone"]) for row in widget.json()["data"]]
    assert rows == [
        ("Tastatur (in 10 Tagen)", "warn"),
        ("Monitor (in 60 Tagen)", "warn"),
        ("NAS (letzte Woche abgelaufen)", "danger"),
    ]

    # Dieselbe Schwelle wie InventoryPage.tsx (warrantyState: <= 90 Tage = "soon").
    assert _WARRANTY_SOON_DAYS == 90
    specs = await client.get("/api/v1/widgets", headers=headers)
    assert specs.status_code == 200, specs.text
    warranty = next(w for w in specs.json() if w["ext_id"] == "inventory" and w["id"] == "warranty")
    assert "90 Tagen" in warranty["view"]["empty_text"]


@pytest.mark.asyncio
async def test_reads_and_writes_require_their_respective_permission(client, db_session, test_settings):
    """`ctx.api.include_router(permission=...)` erlaubt nur EINE Berechtigung pro
    Router (bewusste Einschraenkung) -- deshalb zwei
    separate Router (read_router/write_router) statt Feingranulat pro Route.
    Beweist, dass ein Nutzer mit NUR `inventory.read` lesen, aber nicht schreiben
    kann. Eine eigene (nicht eingebaute) Rolle statt `viewer` -- `inventory.read`
    ist eine brandneue Berechtigung, kein bestehender Builtin kennt sie."""
    from nodvard_deck.core import security
    from nodvard_deck.db.base import refresh_relationships
    from nodvard_deck.models import Role, RolePermission, User

    await _enable_inventory(client, db_session, test_settings)

    role = Role(name="inventory-reader-test", is_builtin=False, description="Testrolle")
    db_session.add(role)
    await db_session.flush()
    db_session.add(RolePermission(role_id=role.id, permission="inventory.read"))
    await db_session.flush()
    await refresh_relationships(db_session, role, "permissions")

    reader = User(username="reader", password_hash=security.hash_password("correct-horse-battery"), is_active=True)
    reader.roles.append(role)
    db_session.add(reader)
    await db_session.flush()
    await db_session.commit()

    login = await client.post("/api/v1/auth/login", json={"username": "reader", "password": "correct-horse-battery"})
    assert login.status_code == 200, login.text
    reader_headers = _auth_header(login.json()["access_token"])

    read_ok = await client.get("/api/v1/ext/inventory/categories", headers=reader_headers)
    assert read_ok.status_code == 200, read_ok.text

    write_forbidden = await client.post("/api/v1/ext/inventory/categories", json={"name": "X"}, headers=reader_headers)
    assert write_forbidden.status_code == 403


@pytest.mark.asyncio
async def test_image_upload_larger_than_the_general_request_limit_is_accepted(client, db_session, test_settings):
    from nodvard_deck_ext_inventory.images import MAX_IMAGE_BYTES

    token = await _enable_inventory(client, db_session, test_settings)
    headers = _auth_header(token)
    item = await client.post("/api/v1/ext/inventory/items", json={"name": "Kamera"}, headers=headers)
    url = f"/api/v1/ext/inventory/items/{item.json()['id']}/images"
    png = {**headers, "Content-Type": "image/png"}

    size = 2 * 1024 * 1024
    assert 1024**2 < size < MAX_IMAGE_BYTES
    ok = await client.post(url, content=b"\x89PNG\r\n\x1a\n" + b"\x00" * size, headers=png)
    assert ok.status_code == 201, ok.text
    too_big = await client.post(url, content=b"\x00" * (MAX_IMAGE_BYTES + 1), headers=png)
    assert too_big.status_code == 413
