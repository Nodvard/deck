"""PUT /branding -- der Erstinbetriebnahme-Assistent braucht einen Schreibweg
neben dem bestehenden `GET /branding` (test_public_api.py). Getrennter
Router (api/v1/branding.py), eigene Berechtigung `branding.write` -- nicht
`settings.write` (siehe dortiger Modul-Docstring)."""

from __future__ import annotations

import base64
import hashlib
from xml.etree import ElementTree

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
    "logo_url": "/api/v1/branding/logo",
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
@pytest.mark.parametrize("kind", ["logo", "favicon"])
@pytest.mark.parametrize("extension", ["svg", "png"])
@pytest.mark.parametrize("extra", [0, 1])
async def test_stored_branding_images_respect_size_limit(client, test_settings, kind, extension, extra):
    from nodvard_deck.branding import MAX_FAVICON_BYTES, MAX_LOGO_BYTES, logo_dir

    limit = MAX_LOGO_BYTES if kind == "logo" else MAX_FAVICON_BYTES
    content = b'<svg xmlns="http://www.w3.org/2000/svg"/>' if extension == "svg" else _TINY_PNG
    content += b" " * (limit + extra - len(content))
    directory = logo_dir(test_settings.data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{kind}.{extension}").write_bytes(content)

    response = await client.get(f"/api/v1/branding/{kind}")
    assert response.status_code == (413 if extra else 200), response.text
    if not extra:
        if extension == "svg":
            assert ElementTree.fromstring(response.content).tag == "{http://www.w3.org/2000/svg}svg"
        else:
            assert response.content == content


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["logo", "favicon"])
async def test_stored_svg_read_is_bounded(client, test_settings, monkeypatch, kind):
    from pathlib import Path
    from unittest.mock import mock_open

    from nodvard_deck.branding import MAX_FAVICON_BYTES, MAX_LOGO_BYTES, logo_dir

    limit = MAX_LOGO_BYTES if kind == "logo" else MAX_FAVICON_BYTES
    path = logo_dir(test_settings.data_dir) / f"{kind}.svg"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    path.write_bytes(content)
    original_open = Path.open
    reader = mock_open(read_data=content)

    def open_file(file, *args, **kwargs):
        return reader(*args, **kwargs) if file == path else original_open(file, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    assert (await client.get(f"/api/v1/branding/{kind}")).status_code == 200
    reader().read.assert_called_once_with(limit + 1)


def _svg_with_styles(count):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        + "".join(f'<rect style="fill:#{i:06x}"/>' for i in range(count))
        + '</svg>'
    ).encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["logo", "favicon"])
@pytest.mark.parametrize("count", [73, 74, 5000])
async def test_svg_upload_respects_csp_limit(client, test_settings, kind, count):
    from nodvard_deck.branding import MAX_FAVICON_BYTES, logo_dir

    token = await _bootstrap_owner(client)
    url = f"/api/v1/branding/{kind}"
    first = await client.post(url, content=_TINY_PNG, headers={**_auth_header(token), "Content-Type": "image/png"})
    assert first.status_code == 200, first.text
    content = _svg_with_styles(count)
    assert len(content) < MAX_FAVICON_BYTES
    response = await client.post(url, content=content, headers={**_auth_header(token), "Content-Type": "image/svg+xml"})
    assert response.status_code == (200 if count == 73 else 422), response.text
    image = await client.get(url)
    assert image.status_code == 200
    assert len(image.headers["content-security-policy"].encode("ascii")) <= 4096
    if count == 73:
        assert len(ElementTree.fromstring(image.content)) == count
        assert image.headers["content-security-policy"].count("'sha256-") == count
        assert (logo_dir(test_settings.data_dir) / f"{kind}.svg").read_bytes() == content
    else:
        assert "zu viele unterschiedliche Stile" in response.json()["detail"]
        assert image.content == _TINY_PNG
        assert not (logo_dir(test_settings.data_dir) / f"{kind}.svg").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["logo", "favicon"])
@pytest.mark.parametrize("count", [73, 74, 5000])
async def test_stored_svg_respects_csp_limit(client, test_settings, kind, count):
    from nodvard_deck.branding import MAX_FAVICON_BYTES, logo_dir

    content = _svg_with_styles(count)
    assert len(content) < MAX_FAVICON_BYTES
    directory = logo_dir(test_settings.data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind}.svg"
    path.write_bytes(content)
    response = await client.get(f"/api/v1/branding/{kind}")
    assert response.status_code == (200 if count == 73 else 422), response.text
    assert len(response.headers["content-security-policy"].encode("ascii")) <= 4096
    if count == 73:
        assert len(ElementTree.fromstring(response.content)) == count
        assert response.headers["content-security-policy"].count("'sha256-") == count
    else:
        assert "zu viele unterschiedliche Stile" in response.json()["detail"]
        assert path.read_bytes() == content


@pytest.mark.asyncio
@pytest.mark.parametrize("svg", [
    b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
    b'<svg xmlns="http://www.w3.org/2000/svg"><title>Logo</title><rect width="10" height="8"/></svg>',
])
async def test_second_logo_upload_replaces_the_first_not_adds_to_it(client, test_settings, svg):
    """Ein neuer Upload ersetzt die alte Datei und liefert denselben XML-Inhalt."""
    token = await _bootstrap_owner(client)
    first = await client.post(
        "/api/v1/branding/logo", content=_TINY_PNG, headers={**_auth_header(token), "Content-Type": "image/png"},
    )
    assert first.status_code == 200

    r = await client.post("/api/v1/branding/logo", content=svg, headers={**_auth_header(token), "Content-Type": "image/svg+xml"})
    assert r.status_code == 200

    from nodvard_deck.branding import logo_dir

    remaining = sorted(logo_dir(test_settings.data_dir).glob("logo.*"))
    assert [p.name for p in remaining] == ["logo.svg"]
    assert remaining[0].read_bytes() == svg

    logo = await client.get("/api/v1/branding/logo")
    assert logo.status_code == 200
    assert logo.headers["content-type"] == "image/svg+xml"
    expected = ElementTree.fromstring(svg)
    actual = ElementTree.fromstring(logo.content)
    assert [(el.tag, el.attrib, el.text, el.tail) for el in actual.iter()] == [
        (el.tag, el.attrib, el.text, el.tail) for el in expected.iter()
    ]


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


_SKRIPT_SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><script>fetch('/api/v1/auth/refresh')</script></svg>"
_HARMLOSES_SVG = (
    b"<?xml version='1.0' encoding='UTF-8'?>"
    b"<svg xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink' viewBox='0 0 10 10'>"
    b"<defs><linearGradient id='a'><stop offset='0' stop-color='#fff'/></linearGradient>"
    b"<style>.k{fill:url(#a)}#r{fill:red}</style></defs>"
    b"<title>Logo</title><g><rect id='r' width='10' height='10' style='fill:url(#a)'/><use xlink:href='#a'/>"
    b"<path d='M0 0L10 10' fill='url(#a)'/></g></svg>"
)


@pytest.mark.asyncio
async def test_logo_upload_accepts_a_harmless_svg(client):
    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/branding/logo", content=_HARMLOSES_SVG,
        headers={**_auth_header(token), "Content-Type": "image/svg+xml"},
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["logo", "favicon"])
async def test_svg_styles_keep_their_values_with_a_fresh_nonce(client, test_settings, kind):
    import re

    from nodvard_deck.branding import save_favicon_file, save_logo_file

    save = save_logo_file if kind == "logo" else save_favicon_file
    save(test_settings.data_dir, _HARMLOSES_SVG, "svg")
    responses = [await client.get(f"/api/v1/branding/{kind}") for _ in range(2)]
    nonces = []
    digest = base64.b64encode(hashlib.sha256(b"fill:url(#a)").digest()).decode()
    for response in responses:
        policy = response.headers["content-security-policy"]
        nonce = re.search(r"'nonce-([^']+)'", policy)[1]
        nonces.append(nonce)
        assert policy == (
            f"default-src 'none'; style-src 'nonce-{nonce}'; "
            f"style-src-attr 'unsafe-hashes' 'sha256-{digest}'; sandbox; frame-ancestors 'none'"
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        svg = ElementTree.fromstring(response.content)
        styles = svg.findall(".//{http://www.w3.org/2000/svg}style")
        assert len(styles) == 1
        assert all(style.get("nonce") == nonce for style in styles)
        assert styles[0].text == ".k{fill:url(#a)}#r{fill:red}"
        assert svg.find(".//{http://www.w3.org/2000/svg}rect").attrib == {
            "id": "r", "width": "10", "height": "10", "style": "fill:url(#a)",
        }
    assert nonces[0] != nonces[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["logo", "favicon"])
async def test_svg_without_style_attributes_keeps_them_blocked(client, test_settings, kind):
    from nodvard_deck.branding import save_favicon_file, save_logo_file

    save = save_logo_file if kind == "logo" else save_favicon_file
    save(test_settings.data_dir, b'<svg xmlns="http://www.w3.org/2000/svg"><rect fill="blue"/></svg>', "svg")
    response = await client.get(f"/api/v1/branding/{kind}")
    assert response.status_code == 200
    assert "style-src-attr 'none';" in response.headers["content-security-policy"]


@pytest.mark.asyncio
@pytest.mark.parametrize("svg", [
    _SKRIPT_SVG,
    b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>",
    b"<svg xmlns='http://www.w3.org/2000/svg'><rect onclick='alert(1)'/></svg>",
    (b"<svg xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink'>"
     b"<a xlink:href='javascript:alert(1)'><rect/></a></svg>"),
    (b"<svg xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink'>"
     b"<use xlink:href='https://boese.example/x.svg#a'/></svg>"),
    b"<svg xmlns='http://www.w3.org/2000/svg'><image href='https://boese.example/p.png'/></svg>",
    b"<svg xmlns='http://www.w3.org/2000/svg'><foreignObject><div/></foreignObject></svg>",
    b"<svg xmlns='http://www.w3.org/2000/svg'><rect fill='url(https://boese.example/x)'/></svg>",
    b"<svg xmlns='http://www.w3.org/2000/svg'><style>@import 'https://boese.example/a.css';</style></svg>",
    b"<svg xmlns='http://www.w3.org/2000/svg'><set attributeName='href' to='javascript:alert(1)'/></svg>",
    b"<svg xmlns='http://www.w3.org/2000/svg'><rect fill='JaVa&#9;Script:alert(1)'/></svg>",
    b"<svg xmlns='http://www.w3.org/2000/svg'><html:script xmlns:html='http://www.w3.org/1999/xhtml'/></svg>",
    b"<!DOCTYPE svg [<!ENTITY a 'aaaa'><!ENTITY b '&a;&a;&a;&a;'>]><svg xmlns='http://www.w3.org/2000/svg'>&b;</svg>",
    b"<html><body>kein svg</body></html>",
    b"kein xml",
    b"<?xml-stylesheet type='text/xsl' href='https://boese.example/x.xsl'?><svg xmlns='http://www.w3.org/2000/svg'/>",
    b"<svg xmlns='http://www.w3.org/2000/svg'><style>rect{fill:\\75rl(https://boese.example/x)}</style></svg>",
    b"<svg xmlns='http://www.w3.org/2000/svg'><style>@\\69mport 'https://boese.example/a.css';</style></svg>",
    b"<svg xmlns='http://www.w3.org/2000/svg'><style>rect{fill:image-set('https://boese.example/x' 1x)}</style></svg>",
    b"<svg xmlns='http://www.w3.org/2000/svg'><rect fill='\\75rl(https://boese.example/x)'/></svg>",
    b"<svg xmlns='http://www.w3.org/2000/svg' xml:base='https://boese.example/'><use href='#a'/></svg>",
    b"<?xml version='1.0' encoding='bogus'?><svg xmlns='http://www.w3.org/2000/svg'/>",
], ids=lambda b: b[:60].decode("latin-1"))
async def test_logo_upload_rejects_dangerous_or_broken_svg(client, test_settings, svg):
    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/branding/logo", content=svg,
        headers={**_auth_header(token), "Content-Type": "image/svg+xml"},
    )
    assert r.status_code == 422, r.text
    assert "SVG" in r.json()["detail"]
    from nodvard_deck.branding import find_logo_file

    assert find_logo_file(test_settings.data_dir) is None


@pytest.mark.asyncio
async def test_logo_response_is_sandboxed_without_login(client, test_settings):
    """Ein schon gespeichertes (auch unsicheres) SVG darf beim direkten Öffnen kein Skript
    im Origin des Dashboards ausführen."""
    from nodvard_deck.branding import save_logo_file

    save_logo_file(test_settings.data_dir, _SKRIPT_SVG, "svg")
    r = await client.get("/api/v1/branding/logo")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "sandbox" in csp
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_app_icons_carry_the_same_protective_headers(client):
    r = await client.get("/api/v1/app/icon-192.png")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "JavaScript:alert(1)", " javascript:alert(1)", "java\tscript:alert(1)",
    "data:text/html,<script>1</script>", "vbscript:x", "//example.org/hilfe", "example.org/hilfe", "https:", "ftp://example.org",
])
async def test_put_branding_rejects_unsafe_support_url(client, url):
    token = await _bootstrap_owner(client)
    r = await client.put("/api/v1/branding", json={**_PAYLOAD, "support_url": url}, headers=_auth_header(token))
    assert r.status_code == 422, r.text
    assert "Support-Link" in r.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://example.org/hilfe", "http://hilfe.lan", "mailto:it@example.org", "MAILTO:it@example.org"])
async def test_put_branding_accepts_http_https_and_mailto_support_url(client, url):
    token = await _bootstrap_owner(client)
    r = await client.put("/api/v1/branding", json={**_PAYLOAD, "support_url": url}, headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert r.json()["support_url"] == url


@pytest.mark.asyncio
async def test_put_branding_empty_support_url_means_none(client):
    token = await _bootstrap_owner(client)
    r = await client.put("/api/v1/branding", json={**_PAYLOAD, "support_url": "  "}, headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert r.json()["support_url"] is None


@pytest.mark.asyncio
async def test_stored_unsafe_support_url_does_not_crash_and_is_not_shown(client, db_session):
    """Ein früher gespeicherter, ungültiger Wert bricht das Laden nicht, wird aber nicht als Link angeboten."""
    from nodvard_deck.branding import SETTINGS_KEY_PREFIX
    from nodvard_deck.db import utcnow
    from nodvard_deck.models import Setting

    db_session.add(Setting(
        key=f"{SETTINGS_KEY_PREFIX}support_url", scope="global", user_id="", value="javascript:alert(1)",
        updated_at=utcnow(), updated_by_user_id=None,
    ))
    await db_session.flush()
    r = await client.get("/api/v1/branding")
    assert r.status_code == 200, r.text
    assert r.json()["support_url"] is None


# --- Favicon und Bildadressen (CSP `img-src 'self' data: blob:`) ---------------------------------------------------

_TINY_ICO = bytes.fromhex("0000010001001010000001002000") + b"\x00" * 26
"""Nur der Kopf einer ICO-Datei: Der Server prüft den Content-Type und die ersten Bytes, nicht das ganze Bild."""


async def _store_branding_value(db_session, field: str, value: str) -> None:
    from nodvard_deck.branding import SETTINGS_KEY_PREFIX
    from nodvard_deck.db import utcnow
    from nodvard_deck.models import Setting

    db_session.add(Setting(
        key=f"{SETTINGS_KEY_PREFIX}{field}", scope="global", user_id="", value=value,
        updated_at=utcnow(), updated_by_user_id=None,
    ))
    await db_session.flush()


@pytest.mark.asyncio
async def test_unauthenticated_favicon_upload_is_rejected(client):
    r = await client.post("/api/v1/branding/favicon", content=_TINY_PNG, headers={"Content-Type": "image/png"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_favicon_upload_and_public_retrieval(client, test_settings):
    token = await _bootstrap_owner(client)
    assert (await client.get("/api/v1/branding/favicon")).status_code == 404

    r = await client.post(
        "/api/v1/branding/favicon", content=_TINY_PNG, headers={**_auth_header(token), "Content-Type": "image/png"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["favicon_url"] == "/api/v1/branding/favicon"
    assert (await client.get("/api/v1/branding")).json()["favicon_url"] == "/api/v1/branding/favicon"

    favicon = await client.get("/api/v1/branding/favicon")
    assert favicon.status_code == 200
    assert favicon.headers["content-type"] == "image/png"
    assert favicon.content == _TINY_PNG
    assert "sandbox" in favicon.headers["content-security-policy"]
    assert favicon.headers["x-content-type-options"] == "nosniff"

    # Logo und Favicon liegen nebeneinander, ein Favicon-Upload räumt das Logo nicht weg.
    await client.post("/api/v1/branding/logo", content=_TINY_PNG, headers={**_auth_header(token), "Content-Type": "image/png"})
    from nodvard_deck.branding import logo_dir

    assert sorted(p.name for p in logo_dir(test_settings.data_dir).iterdir()) == ["favicon.png", "logo.png"]


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["image/x-icon", "image/vnd.microsoft.icon"])
async def test_favicon_upload_accepts_ico_and_replaces_the_previous_file(client, test_settings, content_type):
    token = await _bootstrap_owner(client)
    await client.post(
        "/api/v1/branding/favicon", content=_TINY_PNG, headers={**_auth_header(token), "Content-Type": "image/png"},
    )
    r = await client.post(
        "/api/v1/branding/favicon", content=_TINY_ICO, headers={**_auth_header(token), "Content-Type": content_type},
    )
    assert r.status_code == 200, r.text
    from nodvard_deck.branding import logo_dir

    assert [p.name for p in logo_dir(test_settings.data_dir).glob("favicon.*")] == ["favicon.ico"]
    favicon = await client.get("/api/v1/branding/favicon")
    assert favicon.headers["content-type"] == "image/x-icon"
    assert favicon.content == _TINY_ICO


@pytest.mark.asyncio
async def test_favicon_upload_checks_type_size_and_svg(client, test_settings):
    from nodvard_deck.branding import MAX_FAVICON_BYTES, find_favicon_file

    token = await _bootstrap_owner(client)
    jpeg = await client.post(
        "/api/v1/branding/favicon", content=b"\xff\xd8", headers={**_auth_header(token), "Content-Type": "image/jpeg"},
    )
    assert jpeg.status_code == 415
    too_big = await client.post(
        "/api/v1/branding/favicon", content=b"\x00" * (MAX_FAVICON_BYTES + 1),
        headers={**_auth_header(token), "Content-Type": "image/png"},
    )
    assert too_big.status_code == 413
    assert "Favicon zu groß" in too_big.json()["detail"]
    svg = await client.post(
        "/api/v1/branding/favicon", content=_SKRIPT_SVG, headers={**_auth_header(token), "Content-Type": "image/svg+xml"},
    )
    assert svg.status_code == 422
    assert "SVG-Favicon abgelehnt" in svg.json()["detail"]
    assert find_favicon_file(test_settings.data_dir) is None
    harmless = await client.post(
        "/api/v1/branding/favicon", content=_HARMLOSES_SVG, headers={**_auth_header(token), "Content-Type": "image/svg+xml"},
    )
    assert harmless.status_code == 200, harmless.text


@pytest.mark.asyncio
async def test_favicon_upload_needs_branding_write(client, db_session):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    await _bootstrap_owner(client)
    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles["viewer"])
    db_session.add(user)
    await db_session.flush()
    token = (await client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"})).json()["access_token"]

    r = await client.post(
        "/api/v1/branding/favicon", content=_TINY_PNG, headers={**_auth_header(token), "Content-Type": "image/png"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["logo_url", "favicon_url"])
@pytest.mark.parametrize("url", [
    "https://example.org/favicon.png", "http://intern.lan/logo.svg", "//example.org/x.png", "/\\example.org/x.png",
    "example.org/x.png", "javascript:alert(1)", "data:text/html,<b>x</b>", "/bild mit leerzeichen.png",
    "/bild\t.png", "data:image/png;base64,AA\n==",
])
async def test_put_branding_rejects_images_from_elsewhere(client, field, url):
    token = await _bootstrap_owner(client)
    r = await client.put("/api/v1/branding", json={**_PAYLOAD, field: url}, headers=_auth_header(token))
    assert r.status_code == 422, r.text
    assert "fremde Adressen" in r.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["logo_url", "favicon_url"])
@pytest.mark.parametrize("url", [
    "/api/v1/branding/favicon", "/static/symbol.png?v=2", "data:image/png;base64,iVBORw0KGgo=",
    "DATA:IMAGE/svg+xml;base64,PHN2Zy8+",
])
async def test_put_branding_accepts_own_paths_and_embedded_images(client, field, url):
    token = await _bootstrap_owner(client)
    r = await client.put("/api/v1/branding", json={**_PAYLOAD, field: url}, headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert r.json()[field] == url


@pytest.mark.asyncio
async def test_put_branding_empty_image_address_means_none(client):
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/branding", json={**_PAYLOAD, "logo_url": " ", "favicon_url": ""}, headers=_auth_header(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["logo_url"] is None and r.json()["favicon_url"] is None


@pytest.mark.asyncio
async def test_a_stored_foreign_favicon_stays_until_someone_replaces_it(client, db_session):
    """Von vor der Regel: Der alte Wert bleibt gespeichert und blockiert das Speichern der anderen Felder nicht.
    Eine andere fremde Adresse geht aber nicht, und Entfernen geht immer."""
    old = "https://cdn.example.org/favicon.png"
    await _store_branding_value(db_session, "favicon_url", old)
    token = await _bootstrap_owner(client)
    assert (await client.get("/api/v1/branding")).json()["favicon_url"] == old

    keep = await client.put(
        "/api/v1/branding", json={**_PAYLOAD, "product_name": "Neu", "favicon_url": old}, headers=_auth_header(token),
    )
    assert keep.status_code == 200, keep.text
    assert keep.json()["product_name"] == "Neu" and keep.json()["favicon_url"] == old

    other = await client.put(
        "/api/v1/branding", json={**_PAYLOAD, "favicon_url": "https://cdn.example.org/neu.png"}, headers=_auth_header(token),
    )
    assert other.status_code == 422

    removed = await client.put("/api/v1/branding", json={**_PAYLOAD, "favicon_url": None}, headers=_auth_header(token))
    assert removed.status_code == 200
    assert removed.json()["favicon_url"] is None
    again = await client.put("/api/v1/branding", json={**_PAYLOAD, "favicon_url": old}, headers=_auth_header(token))
    assert again.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["logo", "favicon"])
async def test_an_empty_upload_is_refused_and_nothing_is_stored(client, test_settings, kind):
    from nodvard_deck.branding import logo_dir

    token = await _bootstrap_owner(client)
    r = await client.post(f"/api/v1/branding/{kind}", content=b"", headers={**_auth_header(token), "Content-Type": "image/png"})
    assert r.status_code == 422
    assert "leer" in r.json()["detail"]
    assert not list(logo_dir(test_settings.data_dir).glob(f"{kind}.*"))
    assert (await client.get("/api/v1/branding")).json()[f"{kind}_url"] is None


_TINY_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
_TINY_WEBP = b"RIFF" + (12).to_bytes(4, "little") + b"WEBPVP8 " + b"\x00" * 8


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "content_type", "content"), [
    ("logo", "image/png", _TINY_JPEG),
    ("logo", "image/jpeg", _TINY_PNG),
    ("logo", "image/webp", b"RIFF\x00\x00\x00\x00WAVE"),
    ("logo", "image/png", b"<html><script>alert(1)</script></html>"),
    ("favicon", "image/x-icon", _TINY_PNG),
    ("favicon", "image/png", _TINY_ICO),
])
async def test_an_upload_whose_first_bytes_do_not_fit_the_type_is_refused(client, test_settings, kind, content_type, content):
    """Sonst läge etwas anderes unter `logo.png` und ginge mit `image/png` an jeden Browser."""
    from nodvard_deck.branding import logo_dir

    token = await _bootstrap_owner(client)
    r = await client.post(f"/api/v1/branding/{kind}", content=content, headers={**_auth_header(token), "Content-Type": content_type})
    assert r.status_code == 422
    assert "passt nicht zum Typ" in r.json()["detail"]
    assert not list(logo_dir(test_settings.data_dir).glob(f"{kind}.*"))


@pytest.mark.asyncio
@pytest.mark.parametrize(("content_type", "content"), [("image/jpeg", _TINY_JPEG), ("image/webp", _TINY_WEBP)])
async def test_jpeg_and_webp_logos_with_their_own_header_are_accepted(client, content_type, content):
    token = await _bootstrap_owner(client)
    r = await client.post("/api/v1/branding/logo", content=content, headers={**_auth_header(token), "Content-Type": content_type})
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v1/branding/logo")).content == content


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["logo", "favicon"])
async def test_removing_the_image_deletes_the_uploaded_file(client, test_settings, kind):
    """„Entfernen“ setzt die Adresse auf null; die Datei war sonst weiter öffentlich unter `GET /branding/<art>` zu
    holen. Ein anderes Feld zu speichern lässt sie dagegen stehen."""
    from nodvard_deck.branding import logo_dir

    token = await _bootstrap_owner(client)
    up = await client.post(f"/api/v1/branding/{kind}", content=_TINY_PNG, headers={**_auth_header(token), "Content-Type": "image/png"})
    assert up.status_code == 200, up.text
    current = up.json()

    kept = await client.put("/api/v1/branding", json={**current, "product_name": "Anders"}, headers=_auth_header(token))
    assert kept.status_code == 200, kept.text
    assert (await client.get(f"/api/v1/branding/{kind}")).status_code == 200

    removed = await client.put("/api/v1/branding", json={**current, f"{kind}_url": None}, headers=_auth_header(token))
    assert removed.status_code == 200, removed.text
    assert removed.json()[f"{kind}_url"] is None
    assert not list(logo_dir(test_settings.data_dir).glob(f"{kind}.*"))
    assert (await client.get(f"/api/v1/branding/{kind}")).status_code == 404


@pytest.mark.asyncio
async def test_an_embedded_logo_instead_of_the_upload_also_deletes_the_file(client, test_settings):
    from nodvard_deck.branding import logo_dir

    token = await _bootstrap_owner(client)
    up = await client.post("/api/v1/branding/logo", content=_TINY_PNG, headers={**_auth_header(token), "Content-Type": "image/png"})
    embedded = "data:image/png;base64,iVBORw0KGgo="
    r = await client.put("/api/v1/branding", json={**up.json(), "logo_url": embedded}, headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert r.json()["logo_url"] == embedded
    assert not list(logo_dir(test_settings.data_dir).glob("logo.*"))
