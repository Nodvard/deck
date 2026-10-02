"""Obergrenze fuer Anfragekoerper (api/body_limit.py): ohne Anmeldung darf keine Anfrage den
Speicher fuellen; Routen mit bewusst grossen Uploads haben eigene, hoehere Grenzen."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from nodvard_deck.api.body_limit import BodyLimitMiddleware, format_bytes
from nodvard_sdk import max_body_bytes
from restore_api_fixtures import OCTET, OWNER_PW, SETUP_CODE, Tripwire, enc, make_owner, rapi  # noqa: F401 - Fixtures

LIMIT = 1024**2


class Stream:
    """Datenstrom ohne Laenge (chunked), der mitzaehlt, wie viel gelesen wurde."""

    def __init__(self, chunk: int = 64 * 1024, chunks: int = 4096) -> None:
        self.chunk, self.chunks, self.sent = chunk, chunks, 0

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for _ in range(self.chunks):
            self.sent += self.chunk
            yield b"a" * self.chunk


def _app(limit: int = 1000) -> tuple[FastAPI, list[int]]:
    app = FastAPI()
    read: list[int] = []

    @app.post("/echo")
    async def echo(request: Request) -> dict:
        body = await request.body()
        read.append(len(body))
        return {"size": len(body)}

    @app.post("/big")
    @max_body_bytes(5000)
    async def big(request: Request) -> dict:
        return {"size": len(await request.body())}

    @app.post("/dynamic")
    @max_body_bytes(lambda: 3000)
    async def dynamic(request: Request) -> dict:
        return {"size": len(await request.body())}

    @app.post("/stream")
    @max_body_bytes(2500)
    async def stream(request: Request) -> dict:
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
        return {"size": total}

    @app.get("/ping")
    async def ping() -> dict:
        return {"ok": True}

    app.add_middleware(BodyLimitMiddleware, max_body_bytes=limit)
    return app, read


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")


# --- Middleware allein ------------------------------------------------------


@pytest.mark.asyncio
async def test_content_length_over_the_limit_is_413_and_the_app_is_not_called():
    app, read = _app()
    async with _client(app) as c:
        trip = Tripwire()
        r = await c.post("/echo", content=trip, headers={"Content-Length": "5000"})
    assert r.status_code == 413
    assert "zu groß" in r.json()["detail"]
    assert trip.consumed is False and read == []


@pytest.mark.asyncio
async def test_chunked_body_over_the_limit_is_413_and_reading_stops():
    app, read = _app()
    stream = Stream()
    async with _client(app) as c:
        r = await c.post("/echo", content=stream)
    assert r.status_code == 413
    assert read == []
    assert stream.sent < 10 * 64 * 1024, "der Strom wurde nicht frueh genug abgebrochen"


@pytest.mark.asyncio
async def test_normal_requests_and_exact_limit_pass():
    app, _ = _app()
    async with _client(app) as c:
        assert (await c.get("/ping")).json() == {"ok": True}
        assert (await c.post("/echo", content=b"x" * 1000)).json() == {"size": 1000}
        assert (await c.post("/echo", content=b"")).json() == {"size": 0}
        r = await c.post("/echo", content=b"x" * 1001)
        assert r.status_code == 413


@pytest.mark.asyncio
async def test_routes_with_their_own_limit_may_take_more_but_not_unlimited():
    app, _ = _app()
    async with _client(app) as c:
        assert (await c.post("/big", content=b"x" * 5000)).json() == {"size": 5000}
        assert (await c.post("/big", content=b"x" * 5001)).status_code == 413
        assert (await c.post("/dynamic", content=b"x" * 3000)).json() == {"size": 3000}
        assert (await c.post("/dynamic", content=b"x" * 3001)).status_code == 413


@pytest.mark.asyncio
async def test_chunked_upload_on_a_route_with_its_own_limit_is_counted_too():
    app, _ = _app()
    async with _client(app) as c:
        ok = Stream(chunk=500, chunks=5)  # 2500 Bytes: genau erlaubt
        assert (await c.post("/stream", content=ok)).json() == {"size": 2500}
        stream = Stream(chunk=500, chunks=1000)
        r = await c.post("/stream", content=stream)
    assert r.status_code == 413
    assert stream.sent < 20_000


@pytest.mark.asyncio
async def test_a_route_limit_never_lowers_the_general_limit():
    app = FastAPI()

    @app.post("/small")
    @max_body_bytes(10)
    async def small(request: Request) -> dict:
        return {"size": len(await request.body())}

    app.add_middleware(BodyLimitMiddleware, max_body_bytes=1000)
    async with _client(app) as c:
        assert (await c.post("/small", content=b"x" * 900)).json() == {"size": 900}


def test_format_bytes():
    assert format_bytes(1024**2) == "1 MB"
    assert format_bytes(20 * 1024**2) == "20 MB"
    assert format_bytes(4 * 1024**3) == "4 GB"
    assert format_bytes(1536 * 1024) == "1,5 MB"
    assert format_bytes(500) == "500 Bytes"


# --- Die echte App ----------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_login_with_a_huge_declared_body_is_413_without_reading(client):
    trip = Tripwire()
    r = await client.post(
        "/api/v1/auth/login", content=trip,
        headers={"Content-Type": "text/plain", "Content-Length": str(500 * 1024**2), "Origin": "http://evil.example"},
    )
    assert r.status_code == 413 and trip.consumed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/bootstrap"])
async def test_unauthenticated_chunked_body_is_413_and_not_read_to_the_end(client, path):
    stream = Stream()
    r = await client.post(path, content=stream)
    assert r.status_code == 413
    assert stream.sent <= LIMIT + 2 * 64 * 1024, f"{stream.sent} Bytes gelesen"


@pytest.mark.asyncio
async def test_normal_login_requests_still_work(client):
    r = await client.post("/api/v1/auth/login", json={"username": "niemand", "password": "falsch-falsch-falsch"})
    assert r.status_code == 401
    r = await client.post("/api/v1/auth/login", json={"username": "x" * 900_000, "password": "y"})
    assert r.status_code in (401, 422)  # unter 1 MiB: normale Pruefung, keine 413


def test_routes_with_bigger_uploads_declare_their_limits():
    from nodvard_deck.api.v1 import auth, branding, files, system
    from nodvard_deck.branding import MAX_LOGO_BYTES
    from nodvard_deck.config import get_settings
    from nodvard_sdk.http import MAX_BODY_ATTR

    settings = get_settings()

    def declared(func):
        value = getattr(func, MAX_BODY_ATTR)
        return value() if callable(value) else value

    assert declared(system.restore_upload) == settings.restore_max_upload_bytes
    assert declared(auth.bootstrap_restore_upload) == settings.restore_max_upload_bytes
    assert declared(files.upload_file) > 64 * 1024**3  # Standard: keine eigene Grenze (ISO-Abbilder, VM-Images)
    assert declared(branding.upload_logo) == MAX_LOGO_BYTES
    assert not hasattr(auth.login, MAX_BODY_ATTR)


@pytest.mark.asyncio
async def test_logo_between_general_and_own_limit_is_accepted_and_above_is_413(client):
    from nodvard_deck.branding import MAX_LOGO_BYTES

    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}", "Content-Type": "image/png"}

    assert MAX_LOGO_BYTES > LIMIT
    ok = await client.post("/api/v1/branding/logo", content=b"\x00" * (LIMIT + 1000), headers=headers)
    assert ok.status_code == 200, ok.text
    too_big = await client.post("/api/v1/branding/logo", content=b"\x00" * (MAX_LOGO_BYTES + 1), headers=headers)
    assert too_big.status_code == 413
    chunked = Stream(chunk=256 * 1024, chunks=64)
    r = await client.post("/api/v1/branding/logo", content=chunked, headers=headers)
    assert r.status_code == 413
    assert chunked.sent <= MAX_LOGO_BYTES + 2 * 256 * 1024


@pytest.mark.asyncio
async def test_restore_upload_larger_than_the_general_limit_reaches_its_handler(client, rapi):  # noqa: F811
    owner = await make_owner(client)
    data = b"x" * (LIMIT + 500_000)
    r = await client.put(
        "/api/v1/system/restore/upload", content=data,
        headers={**owner, **OCTET, "X-Confirm-Password": enc(OWNER_PW)},
    )
    assert r.status_code == 422 and "keine Sicherung" in r.json()["detail"]  # Pruefung des Handlers, kein 413


@pytest.mark.asyncio
async def test_bootstrap_restore_upload_larger_than_the_general_limit_reaches_its_handler(client, rapi):  # noqa: F811
    r = await client.put(
        "/api/v1/auth/bootstrap/restore/upload", content=b"x" * (LIMIT + 500_000),
        headers={**OCTET, "X-Setup-Code": SETUP_CODE},
    )
    assert r.status_code == 422 and "keine Sicherung" in r.json()["detail"]


def test_file_upload_limit_can_be_set_and_zero_means_no_limit(monkeypatch):
    from nodvard_deck.api.body_limit import NO_LIMIT
    from nodvard_deck.api.v1 import files
    from nodvard_deck.config import get_settings
    from nodvard_sdk.http import MAX_BODY_ATTR

    limit = getattr(files.upload_file, MAX_BODY_ATTR)
    assert get_settings().files_max_upload_bytes == 0 and limit() == NO_LIMIT
    monkeypatch.setattr(get_settings(), "files_max_upload_bytes", 8 * 1024**3)
    assert limit() == 8 * 1024**3


# --- Ablauf auf ASGI-Ebene --------------------------------------------------


def _scope(headers: list[tuple[bytes, bytes]]) -> dict:
    return {"type": "http", "method": "POST", "path": "/x", "headers": headers}


def _receiver(chunks: list[bytes]):
    queue = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    return receive


@pytest.mark.asyncio
async def test_after_the_413_later_answers_of_the_app_are_dropped_and_the_connection_closes():
    """Die Anwendung antwortet nach der gemeldeten Trennung noch selbst (FastAPI: 400) -- beim
    Client darf trotzdem genau eine Antwort ankommen, die 413, mit `Connection: close`
    (der ungelesene Rest des Koerpers darf nicht als naechste Anfrage gelesen werden)."""
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send_):
        while (await receive())["type"] != "http.disconnect":
            pass
        await send_({"type": "http.response.start", "status": 400, "headers": []})
        await send_({"type": "http.response.body", "body": b"kaputt"})

    middleware = BodyLimitMiddleware(app, max_body_bytes=10)
    await middleware(_scope([]), _receiver([b"a" * 8, b"a" * 8, b"a" * 8]), send)

    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
    assert sent[0]["status"] == 413
    assert (b"connection", b"close") in sent[0]["headers"]


@pytest.mark.asyncio
async def test_errors_of_the_app_after_the_413_are_swallowed_but_not_otherwise():
    async def send(_message):
        pass

    async def failing_after_read(scope, receive, send_):
        await receive()
        raise RuntimeError("Gegenstelle weg")

    rejected = BodyLimitMiddleware(failing_after_read, max_body_bytes=10)
    await rejected(_scope([(b"content-length", b"50")]), _receiver([b"a" * 50]), send)  # kein Fehler nach aussen

    accepted = BodyLimitMiddleware(failing_after_read, max_body_bytes=100)
    with pytest.raises(RuntimeError):
        await accepted(_scope([(b"content-length", b"50")]), _receiver([b"a" * 50]), send)
