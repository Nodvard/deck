"""Sicherheits-Kopfzeilen (`api/security_headers.py`): Jede Antwort von Deck verbietet fremde Rahmen, das Raten des
Dateityps und den Pfad im Referrer an fremde Seiten. Eine eigene CSP einer Route bleibt stehen und bekommt nur
`frame-ancestors 'none'` dazu."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.testclient import TestClient

from nodvard_deck.api.security_headers import FIXED_HEADERS, SecurityHeadersMiddleware, with_frame_ancestors
from nodvard_deck.branding import UNTRUSTED_FILE_HEADERS
from nodvard_deck.main import _SpaStaticFiles

FRAME_ANCESTORS = "frame-ancestors 'none'"


def _assert_fixed_headers(headers) -> None:
    assert headers.get_list("x-frame-options") == ["DENY"]
    assert headers.get_list("x-content-type-options") == ["nosniff"]
    assert headers.get_list("referrer-policy") == ["same-origin"]


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
    for res in answers.values():
        _assert_fixed_headers(res.headers)
        assert res.headers.get_list("content-security-policy") == [FRAME_ANCESTORS]


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


def test_a_route_cannot_loosen_the_headers():
    res = TestClient(_app()).get("/lockerer-rahmen")
    _assert_fixed_headers(res.headers)
    assert res.headers.get_list("content-security-policy") == [f"default-src 'none'; {FRAME_ANCESTORS}"]


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
    """Die Notseite ist ein eigener Server ohne diese Middleware (nur Standardbibliothek) und setzt die Kopfzeilen selbst."""
    from nodvard_deck.rescue import _SECURITY_HEADERS

    rescue = {name.lower(): value for name, value in _SECURITY_HEADERS.items()}
    for name, value in FIXED_HEADERS.items():
        assert rescue[name.decode()] == value.decode()
    assert FRAME_ANCESTORS in rescue["content-security-policy"]
