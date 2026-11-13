"""Sicherheits-Kopfzeilen (`api/security_headers.py`): Jede Antwort von Deck verbietet fremde Rahmen, das Raten des
Dateityps, den Pfad im Referrer an fremde Seiten, fremde Fenster mit Griff auf Deck, Einbinden von fremden Seiten
und Geräte wie die Kamera. Eine eigene CSP einer Route bleibt stehen und bekommt nur
`frame-ancestors 'none'` dazu."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.testclient import TestClient
from nodvard_deck.api.security_headers import (
    CSP_HEADER,
    FIXED_HEADERS,
    PERMISSIONS_POLICY,
    SecurityHeadersMiddleware,
    secure_headers,
    with_frame_ancestors,
)
from nodvard_deck.branding import (
    UNTRUSTED_FILE_HEADERS,
    save_favicon_file,
    save_logo_file,
)
from nodvard_deck.main import _SpaStaticFiles

FRAME_ANCESTORS = "frame-ancestors 'none'"
# Vollbild und Zwischenablage braucht Deck selbst (Konsole, Kopieren-Knöpfe), sie fehlen hier bewusst.
BLOCKED_DEVICES = (
    "accelerometer", "bluetooth", "camera", "display-capture", "geolocation", "gyroscope", "hid", "magnetometer",
    "microphone", "midi", "payment", "serial", "usb",
)


def _assert_fixed_headers(headers, *, resource_policy="same-origin") -> None:
    assert headers.get_list("x-frame-options") == ["DENY"]
    assert headers.get_list("x-content-type-options") == ["nosniff"]
    assert headers.get_list("referrer-policy") == ["same-origin"]
    assert headers.get_list("cross-origin-opener-policy") == ["same-origin"]
    assert headers.get_list("cross-origin-embedder-policy") == ["require-corp"]
    assert headers.get_list("cross-origin-resource-policy") == [resource_policy]
    [devices] = headers.get_list("permissions-policy")
    assert devices.split(", ") == [f"{name}=()" for name in BLOCKED_DEVICES]


@pytest.mark.asyncio
async def test_every_kind_of_answer_of_the_real_app_carries_the_headers(client):
    answers = {
        "ok": await client.get("/api/v1/health"),
        "nicht angemeldet": await client.get("/api/v1/me"),
        "unbekannt": await client.get("/api/v1/gibt-es-nicht"),
        "falsche Eingabe": await client.post("/api/v1/auth/login", json={}),
        # Die Grenze für die Größe einer Anfrage antwortet selbst, bevor eine Route läuft.
        "zu groß": await client.post("/api/v1/auth/login", content=b"x" * (2 * 1024 * 1024)),
    }
    assert {name: res.status_code for name, res in answers.items()} == {
        "ok": 200, "nicht angemeldet": 401, "unbekannt": 404, "falsche Eingabe": 422, "zu groß": 413,
    }
    for name, res in answers.items():
        _assert_fixed_headers(res.headers, resource_policy="cross-origin" if name == "ok" else "same-origin")
        assert res.headers.get_list("content-security-policy") == [f"default-src 'none'; {FRAME_ANCESTORS}"], name
        assert res.headers.get_list("cache-control") == ["no-store"], name


@pytest.mark.asyncio
async def test_tokens_and_account_data_are_not_stored(client, test_settings):
    credentials = {"username": "demo", "password": "test-password-123"}
    bootstrap = await client.post(
        "/api/v1/auth/bootstrap", json={**credentials, "setup_code": test_settings.setup_code},
    )
    assert bootstrap.status_code == 201
    login = await client.post("/api/v1/auth/login", json=credentials)
    assert login.status_code == 200
    assert bool(login.json().get("access_token"))
    authorization = {"Authorization": f"Bearer {login.json()['access_token']}"}
    account = await client.get("/api/v1/me", headers=authorization)
    assert account.status_code == 200
    assert account.json()["username"] == "demo"
    setup = await client.post(
        "/api/v1/me/totp/setup", headers=authorization,
        json={"current_password": credentials["password"]},
    )
    assert setup.status_code == 200
    assert bool(setup.json().get("secret"))
    refresh = await client.post("/api/v1/auth/refresh", json={})
    assert refresh.status_code == 200
    assert bool(refresh.json().get("access_token"))
    for response in (bootstrap, login, account, setup, refresh):
        assert response.headers.get_list("cache-control") == ["no-store"], response.request.url.path


@pytest.mark.parametrize("path", ["/api", "/api/v1/me", "/api/v1/ext/demo/download"])
def test_api_cache_default_also_covers_downloads(path: str):
    headers = dict(secure_headers([(b"content-type", b"application/octet-stream")], path))
    assert headers.get(b"cache-control") == b"no-store"


@pytest.mark.parametrize(("path", "policy"), [
    ("/api/v1/branding/custom.css", b"no-cache"),
    ("/api/v1/app/manifest.webmanifest", b"no-cache"),
    ("/api/v1/extensions/demo/bundle.js", b"no-cache"),
    ("/api/v1/app/icon-192.png", b"public, max-age=3600"),
    ("/api/v1/system/backups", b"no-store"),
])
def test_explicit_api_cache_policies_are_preserved(path: str, policy: bytes):
    headers = secure_headers([(b"Cache-Control", policy)], path)
    assert [(name, value) for name, value in headers if name.lower() == b"cache-control"] == [
        (b"Cache-Control", policy),
    ]


@pytest.mark.parametrize("path", ["/", "/settings", "/assets/app.js", "/apis"])
def test_api_cache_default_does_not_apply_to_frontend_paths(path: str):
    headers = dict(secure_headers([], path))
    assert b"cache-control" not in headers


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/eigene-regeln")
    async def own_policy() -> PlainTextResponse:
        # So setzt die App-Seite ihre Regeln für Stile und Schriften.
        return PlainTextResponse(
            "x", headers={"Content-Security-Policy": "style-src 'self' 'unsafe-inline'; font-src 'self' data:"}
        )

    @app.get("/lockerer-rahmen")
    async def weaker_frame() -> PlainTextResponse:
        return PlainTextResponse(
            "x",
            headers={
                "Content-Security-Policy": "default-src 'none'; frame-ancestors *;",
                "X-Frame-Options": "SAMEORIGIN",
                "Referrer-Policy": "unsafe-url",
                "Cross-Origin-Opener-Policy": "unsafe-none",
                "Cross-Origin-Embedder-Policy": "unsafe-none",
                "Cross-Origin-Resource-Policy": "cross-origin",
                "Permissions-Policy": "camera=*",
            },
        )

    @app.get("/hochgeladen")
    async def uploaded() -> PlainTextResponse:
        return PlainTextResponse("x", headers=UNTRUSTED_FILE_HEADERS)

    @app.get("/strom")
    async def stream() -> StreamingResponse:
        async def chunks():
            yield b"a"
            yield b"b"

        return StreamingResponse(chunks(), media_type="application/octet-stream")

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("hallo")
        await websocket.close()

    app.add_middleware(SecurityHeadersMiddleware)
    return app


def test_an_own_policy_of_a_route_stays_and_only_gets_frame_ancestors():
    res = TestClient(_app()).get("/eigene-regeln")
    _assert_fixed_headers(res.headers)
    assert res.headers.get_list("content-security-policy") == [
        f"style-src 'self' 'unsafe-inline'; font-src 'self' data:; {FRAME_ANCESTORS}"
    ]


@pytest.mark.parametrize(
    ("media", "policy"),
    [
        ("application/json", f"default-src 'none'; {FRAME_ANCESTORS}"),
        ("application/problem+json; charset=utf-8", f"default-src 'none'; {FRAME_ANCESTORS}"),
        ("Application/JSON", f"default-src 'none'; {FRAME_ANCESTORS}"),
        ("text/plain; charset=utf-8", FRAME_ANCESTORS),
        ("application/javascript", FRAME_ANCESTORS),
        ("application/jsonp", FRAME_ANCESTORS),
    ],
)
def test_json_without_an_own_policy_loads_nothing(media: str, policy: str):
    [csp] = [value for name, value in secure_headers([(b"content-type", media.encode())]) if name == CSP_HEADER]
    assert csp.decode() == policy


def test_a_route_cannot_loosen_the_headers():
    res = TestClient(_app()).get("/lockerer-rahmen")
    _assert_fixed_headers(res.headers)
    assert res.headers.get_list("content-security-policy") == [f"default-src 'none'; {FRAME_ANCESTORS}"]


@pytest.mark.parametrize("policy", [b"unsafe-url", b"strict-origin-when-cross-origin", b"no-referrer"])
@pytest.mark.parametrize("https", [False, True])
def test_referrer_policy_keeps_the_origin_on_the_own_site(policy: bytes, https: bool):
    headers = secure_headers([
        (b"Referrer-Policy", policy),
        (b"referrer-policy", b"origin"),
    ], https=https)
    assert [value for name, value in headers if name.lower() == b"referrer-policy"] == [b"same-origin"]


@pytest.mark.parametrize("value", [b"", b"unsafe-none", b"invalid", b"credentialless", b"require-corp"])
def test_embedder_policy_is_set_once_despite_route_headers(value: bytes):
    headers = secure_headers([
        (b"Cross-Origin-Embedder-Policy", value),
        (b"cross-origin-embedder-policy", b"unsafe-none"),
    ])
    assert [v for name, v in headers if name.lower() == b"cross-origin-embedder-policy"] == [b"require-corp"]


@pytest.mark.asyncio
async def test_only_public_images_can_be_embedded_on_other_sites(client, test_settings):
    image = b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16"/></svg>'
    save_logo_file(test_settings.data_dir, image, "svg")
    save_favicon_file(test_settings.data_dir, image, "svg")
    paths = ["/api/v1/branding/logo", "/api/v1/branding/favicon"]
    paths += [f"/api/v1/app/icon-{size}{variant}.png" for size in (180, 192, 512) for variant in ("", "-maskable")]
    for path in paths:
        response = await client.get(path, headers={"Origin": "https://start.example"})
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("image/"), path
        _assert_fixed_headers(response.headers, resource_policy="cross-origin")
    _assert_fixed_headers((await client.get("/api/v1/health")).headers, resource_policy="cross-origin")
    for path in ("/api/v1/me", "/api/v1/branding", "/api/v1/app/manifest.webmanifest",
                 "/api/v1/branding/custom.css"):
        _assert_fixed_headers((await client.get(path)).headers)
    for path in ("/api/v1/branding/logo", "/api/v1/branding/favicon"):
        response = await client.post(path, content=image, headers={"Content-Type": "image/svg+xml"})
        assert response.status_code == 401
        _assert_fixed_headers(response.headers)


@pytest.mark.parametrize(("path", "expected"), [
    ("/api/v1/branding/favicon", b"cross-origin"),
    ("/api/v1/app/icon-180.png", b"cross-origin"),
    ("/api/v1/app/icon-512-maskable.png", b"cross-origin"),
    ("/api/v1/health", b"cross-origin"),
    ("/api/v1/health/x", b"same-origin"),
    ("/api/v1/healthz", b"same-origin"),
    ("/api/v1/me", b"same-origin"),
    ("/api/v1/app/icon-999.png", b"same-origin"),
    ("/api/v1/app/icon-x/../../index.html", b"same-origin"),
    ("/api/v1/app/icon-180.png.html", b"same-origin"),
    ("/api/v1/app/icon-\u0661\u0668\u0660.png", b"same-origin"),
    ("/api/v1/branding/favicon/x", b"same-origin"),
    ("/api/v1/branding/logos", b"same-origin"),
    ("/api/v1/app/icons", b"same-origin"),
    ("/favicon.ico", b"same-origin"),
    ("/", b"same-origin"),
])
def test_resource_policy(path: str, expected: bytes):
    assert dict(secure_headers([], path))[b"cross-origin-resource-policy"] == expected


def test_uploaded_files_keep_their_sandbox_and_nosniff_is_not_doubled():
    res = TestClient(_app()).get("/hochgeladen")
    _assert_fixed_headers(res.headers)
    assert res.headers.get_list("content-security-policy") == [
        f"{UNTRUSTED_FILE_HEADERS['Content-Security-Policy']}; {FRAME_ANCESTORS}"
    ]


def test_streams_get_the_headers_and_websockets_pass_through():
    client = TestClient(_app())
    res = client.get("/strom")
    assert res.content == b"ab"
    _assert_fixed_headers(res.headers)
    with client.websocket_connect("/ws") as websocket:
        assert websocket.receive_text() == "hallo"


def test_the_app_page_cannot_be_framed(tmp_path: Path):
    """Die App-Seite darf eigene Regeln mitbringen (Stile, Schriften); am Ende steht immer `frame-ancestors 'none'`."""
    (tmp_path / "index.html").write_text("<html>App-Shell</html>", encoding="utf-8")
    app = FastAPI()
    app.mount("/", _SpaStaticFiles(directory=tmp_path, html=True), name="frontend")
    app.add_middleware(SecurityHeadersMiddleware)
    for path in ("/", "/settings"):
        res = TestClient(app).get(path)
        assert "App-Shell" in res.text
        _assert_fixed_headers(res.headers)
        [policy] = res.headers.get_list("content-security-policy")
        assert policy.endswith(FRAME_ANCESTORS) and policy.count("frame-ancestors") == 1
        assert "; script-src 'self'; " in policy


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("", FRAME_ANCESTORS),
        ("default-src 'none'; sandbox", f"default-src 'none'; sandbox; {FRAME_ANCESTORS}"),
        ("default-src 'none';", f"default-src 'none'; {FRAME_ANCESTORS}"),
        ("Frame-Ancestors 'self' https://x.example; style-src 'self'", f"style-src 'self'; {FRAME_ANCESTORS}"),
        (FRAME_ANCESTORS, FRAME_ANCESTORS),
    ],
)
def test_with_frame_ancestors(policy: str, expected: str):
    assert with_frame_ancestors(policy) == expected


def test_the_rescue_page_sends_the_same_headers():
    """Die Notseite ist ein eigener Server (nur Standardbibliothek) und setzt die Kopfzeilen selbst."""
    from nodvard_deck.rescue import _SECURITY_HEADERS

    rescue = {name.lower(): value for name, value in _SECURITY_HEADERS.items()}
    assert rescue["cross-origin-embedder-policy"] == "require-corp"
    for name, value in FIXED_HEADERS.items():
        assert rescue[name.decode()] == value.decode()
    assert rescue["cross-origin-resource-policy"] == "same-origin"
    assert rescue["permissions-policy"] == PERMISSIONS_POLICY.decode()
    assert FRAME_ANCESTORS in rescue["content-security-policy"]


@pytest.mark.parametrize(("url", "trusted", "proto", "https"), [
    ("http://deck.local", False, None, False),
    ("https://deck.local", False, None, True),
    ("http://deck.local", False, "https", False),
    ("http://deck.local", True, "https", True),
    ("http://deck.local", True, "http", False),
    ("http://deck.local", True, "invalid", False),
    ("http://deck.local", True, "https, http", False),
    ("http://deck.local", True, "http, https", True),
])
@pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/me", "/api/v1/gibt-es-nicht"])
async def test_hsts_uses_the_checked_connection(client, url, trusted, proto, https, path):
    from nodvard_deck.core import trusted_proxies

    trusted_proxies.apply(["127.0.0.1"] if trusted else [])
    headers = {"X-Forwarded-Proto": proto} if proto else {}
    response = await client.get(url + path, headers=headers)
    assert response.headers.get_list("strict-transport-security") == (["max-age=31536000"] if https else [])
    policy = "cross-origin" if path == "/api/v1/health" else "same-origin"
    _assert_fixed_headers(response.headers, resource_policy=policy)
    assert response.headers.get_list("cache-control") == ["no-store"]


@pytest.mark.parametrize("https", [False, True])
def test_routes_cannot_set_hsts_on_http_or_duplicate_it(https):
    headers = secure_headers([
        (b"Strict-Transport-Security", b"max-age=0"),
        (b"strict-transport-security", b"max-age=1; includeSubDomains"),
    ], https=https)
    assert [value for name, value in headers if name.lower() == b"strict-transport-security"] == (
        [b"max-age=31536000"] if https else []
    )


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("frontend_built", [False, True])
@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/v1/header-error"), ("POST", "/api/v1/app/icon-192.png"),
])
def test_unhandled_server_errors_also_get_the_headers(
    tmp_path, test_settings, monkeypatch, caplog, scheme, frontend_built, method, path,
):
    from nodvard_deck import config
    from nodvard_deck.main import create_app

    test_settings.data_dir = tmp_path / "data"
    # Nie `./frontend/dist` des Arbeitsordners: ob dort gebaut wurde, darf das Ergebnis nicht ändern.
    test_settings.frontend_dist = dist = tmp_path / "frontend" / "dist"
    monkeypatch.setattr(config, "_settings", test_settings)
    if frontend_built:
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("<html>App-Shell</html>", encoding="utf-8")
    app = create_app()

    @app.api_route(path, methods=[method])
    async def error():
        raise RuntimeError("test")

    app.router.routes.insert(app.state.ext_mount_index, app.router.routes.pop())
    client = TestClient(app, raise_server_exceptions=False)
    shell = client.get("/settings")
    assert shell.status_code == (200 if frontend_built else 404)
    if frontend_built:
        assert "App-Shell" in shell.text
    with caplog.at_level(logging.ERROR, logger="nodvard_deck.errors"):
        response = client.request(method, f"{scheme}://deck.local{path}")
    assert response.status_code == 500
    match = re.search(r"Fehlerkennung: ([0-9a-f]{32})\.", response.json()["detail"])
    assert match is not None
    assert any(f"request_failed id={match[1]} type=RuntimeError " in record.getMessage() for record in caplog.records)
    assert "test" not in response.text
    if scheme == "https":
        assert response.headers.get_list("strict-transport-security") == ["max-age=31536000"]
    _assert_fixed_headers(response.headers)
    assert response.headers.get_list("cache-control") == ["no-store"]
    assert response.headers.get_list("strict-transport-security") == (
        ["max-age=31536000"] if scheme == "https" else []
    )
