"""PUT /branding -- der Erstinbetriebnahme-Assistent braucht einen Schreibweg
neben dem bestehenden `GET /branding` (test_public_api.py). Getrennter
Router (api/v1/branding.py), eigene Berechtigung `branding.write` -- nicht
`settings.write` (siehe dortiger Modul-Docstring)."""

from __future__ import annotations

import pytest


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_PAYLOAD = {
    "product_name": "Kaeufer GmbH Dashboard",
    "short_name": "KaeuferGmbH",
    "logo_url": "https://example.invalid/logo.svg",
    "favicon_url": None,
    "login_subtitle": "Betriebszentrale",
    "support_url": None,
    "colors": {
        "accent": "#ff0000",
        "accent_strong": "#aa0000",
        "background": "#000000",
        "surface": "#111111",
        "text": "#ffffff",
    },
}


@pytest.mark.asyncio
async def test_unauthenticated_put_is_rejected(client):
    assert (await client.put("/api/v1/branding", json=_PAYLOAD)).status_code == 401


@pytest.mark.asyncio
async def test_owner_can_write_branding_and_get_reflects_it(client):
    token = await _bootstrap_owner(client)
    put = await client.put("/api/v1/branding", json=_PAYLOAD, headers=_auth_header(token))
    assert put.status_code == 200, put.text
    assert put.json()["product_name"] == "Kaeufer GmbH Dashboard"

    # GET /branding ist absichtlich weiterhin OHNE Authentifizierung (public.router) --
    # dieselbe Anfrage, die die Login-Seite selbst macht, muss den neuen Wert sehen.
    got = await client.get("/api/v1/branding")
    assert got.status_code == 200
    body = got.json()
    assert body["product_name"] == "Kaeufer GmbH Dashboard"
    assert body["colors"]["accent"] == "#ff0000"


@pytest.mark.asyncio
async def test_branding_write_requires_branding_write_permission(client, db_session):
    """Dieselbe Struktur wie test_settings_api.py::test_settings_require_settings_write_permission
    -- ein Viewer hat keine der beiden Berechtigungen, `branding.write` ist keine der
    drei BUILTIN_ROLES ausser `admin` (Wildcard) zugeordnet."""
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    await _bootstrap_owner(client)
    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles["viewer"])
    db_session.add(user)
    await db_session.flush()

    login = await client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"})
    token = login.json()["access_token"]

    assert (
        await client.put("/api/v1/branding", json=_PAYLOAD, headers=_auth_header(token))
    ).status_code == 403


_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a4944415478"
    "9c6300010000050001a5f645400000000049454e44ae426082"
)
"""Kleinstmoegliches valides 1x1-PNG -- ausreichend, um den echten Bild-Pfad (Header,
Allowlist, Dateiendung) zu pruefen, ohne eine echte Bilddatei ins Repo zu legen."""


@pytest.mark.asyncio
async def test_unauthenticated_logo_upload_is_rejected(client):
    r = await client.post("/api/v1/branding/logo", content=_TINY_PNG, headers={"Content-Type": "image/png"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_logo_upload_and_public_retrieval(client):
    token = await _bootstrap_owner(client)

    # Vorher: kein Logo hinterlegt.
    assert (await client.get("/api/v1/branding/logo")).status_code == 404

    put = await client.post(
        "/api/v1/branding/logo", content=_TINY_PNG,
        headers={**_auth_header(token), "Content-Type": "image/png"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["logo_url"] == "/api/v1/branding/logo"

    # GET /branding selbst zeigt die neue logo_url (wie jede andere PUT /branding-Aenderung).
    branding = await client.get("/api/v1/branding")
    assert branding.json()["logo_url"] == "/api/v1/branding/logo"

    # GET /branding/logo (oeffentlich, kein Token) liefert die tatsaechlichen Bytes.
    logo = await client.get("/api/v1/branding/logo")
    assert logo.status_code == 200
    assert logo.headers["content-type"] == "image/png"
    assert logo.content == _TINY_PNG


@pytest.mark.asyncio
async def test_logo_upload_rejects_unsupported_content_type(client):
    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/branding/logo", content=b"nicht ein bild",
        headers={**_auth_header(token), "Content-Type": "application/pdf"},
    )
    assert r.status_code == 415


@pytest.mark.asyncio
async def test_logo_upload_rejects_oversized_payload(client):
    token = await _bootstrap_owner(client)
    from nodvard_deck.branding import MAX_LOGO_BYTES

    too_big = b"\x00" * (MAX_LOGO_BYTES + 1)
    r = await client.post(
        "/api/v1/branding/logo", content=too_big,
        headers={**_auth_header(token), "Content-Type": "image/png"},
    )
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_second_logo_upload_replaces_the_first_not_adds_to_it(client, test_settings):
    """Regressionstest fuer save_logo_file()s Aufraeum-Schritt: ein zweiter Upload mit
    ANDERER Endung (svg statt png) darf nicht beide Dateien nebeneinander stehen
    lassen -- find_logo_file() muesste sonst nichtdeterministisch eine von zwei
    Dateien liefern."""
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/branding/logo", content=_TINY_PNG, headers={**_auth_header(token), "Content-Type": "image/png"})

    svg = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    r = await client.post("/api/v1/branding/logo", content=svg, headers={**_auth_header(token), "Content-Type": "image/svg+xml"})
    assert r.status_code == 200

    from nodvard_deck.branding import logo_dir

    remaining = sorted(logo_dir(test_settings.data_dir).glob("logo.*"))
    assert [p.name for p in remaining] == ["logo.svg"]

    logo = await client.get("/api/v1/branding/logo")
    assert logo.headers["content-type"] == "image/svg+xml"
    assert logo.content == svg


@pytest.mark.asyncio
async def test_admin_role_can_write_branding_via_wildcard(client, db_session):
    """Nicht nur `is_owner` -- die eingebaute `admin`-Rolle (`("*",)`) muss `branding.write`
    ebenfalls abdecken, ohne dass die Berechtigung dort explizit gelistet ist."""
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    await _bootstrap_owner(client)
    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username="admin1", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles["admin"])
    db_session.add(user)
    await db_session.flush()

    login = await client.post("/api/v1/auth/login", json={"username": "admin1", "password": "whatever123"})
    token = login.json()["access_token"]

    r = await client.put("/api/v1/branding", json=_PAYLOAD, headers=_auth_header(token))
    assert r.status_code == 200, r.text


async def test_app_manifest_and_icons_follow_branding_without_login(client):
    """Als App aufs Handy: Manifest und Symbole sind oeffentlich (der Browser holt sie
    ohne Anmeldung) und tragen Name und Farben aus dem Branding."""
    token = await _bootstrap_owner(client)
    await client.put("/api/v1/branding", json=_PAYLOAD, headers=_auth_header(token))

    res = await client.get("/api/v1/app/manifest.webmanifest")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/manifest+json")
    m = res.json()
    assert m["name"] == "Kaeufer GmbH Dashboard" and m["display"] == "standalone" and m["start_url"] == "/"
    assert {i["sizes"] for i in m["icons"]} == {"192x192", "512x512"}
    assert any(i["purpose"] == "maskable" for i in m["icons"])

    from io import BytesIO

    from PIL import Image

    for name, size in (("192", 192), ("512-maskable", 512), ("180", 180)):
        icon = await client.get(f"/api/v1/app/icon-{name}.png")
        assert icon.status_code == 200 and icon.headers["content-type"] == "image/png"
        img = Image.open(BytesIO(icon.content))
        assert img.size == (size, size)
    # Maskierbar = vollflaechig; normal = abgerundete, transparente Ecken
    assert Image.open(BytesIO((await client.get("/api/v1/app/icon-512-maskable.png")).content)).getpixel((0, 0))[3] == 255
    assert Image.open(BytesIO((await client.get("/api/v1/app/icon-192.png")).content)).getpixel((0, 0))[3] == 0
    assert (await client.get("/api/v1/app/icon-64.png")).status_code == 404
