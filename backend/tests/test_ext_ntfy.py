"""ntfy-Extension: NotificationChannel gegen einen echten (lokalen Fake-)ntfy-Server
-- docs/02-EXTENSION-API.md §3.

Bewusst NICHT gegen das echte oeffentliche `ntfy.sh`: genau dessen unauthentifizierte
Offenheit ist genau das Problem, das diese Extension beheben soll (konfigurierbare
Server-URL/Topic + optionales Token) -- ein Test gegen den echten Dienst wuerde
entweder ein reales Topic beruehren oder nichts Neues beweisen. Der Fake-Server hier
spricht ntfys echtes JSON-Publish-Protokoll (`POST /`, `{topic,title,message,
priority}`) und `/v1/health`, laeuft aber lokal auf Loopback -- dasselbe Prinzip wie
der lokale asyncssh-Server in test_core_ssh.py."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from local_server_helpers import serve_app
from raw_http_helpers import BROKEN_ANSWERS, REDIRECT_ANSWERS, raw_http_server

from nodvard_deck.ext.context import build_context
from nodvard_deck.ext.runtime import ExtensionRuntime, LoadedExtension

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"

WRITE_ONLY_NOTE = (
    "Der ntfy-Server ist erreichbar und erlaubt das Senden an dieses Thema. "
    "Mit „Testnachricht senden“ prüfst du die Zustellung."
)
TOKEN_REJECTED_TEXT = (
    "ntfy hat das Zugriffstoken abgelehnt (HTTP 401). Prüfe das Token in den Einstellungen, es ist falsch oder abgelaufen."
)
NO_ACCESS = {"code": 40301, "http": 403, "error": "forbidden", "link": "https://ntfy.sh/docs/publish/#authentication"}
INVALID_PRIORITY = {
    "code": 40007, "http": 400, "error": "invalid priority parameter",
    "link": "https://ntfy.sh/docs/publish/#message-priority",
}


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _ntfy_extension_class():
    src_dir = REPO_EXTENSIONS_DIR / "ntfy" / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    import nodvard_deck_ext_ntfy

    return nodvard_deck_ext_ntfy.Extension


async def _setup_ntfy_extension(db_session, tmp_path, settings, *, server_url: str, topic: str = "test-topic"):
    from nodvard_sdk import ExtensionManifest

    from nodvard_deck.models import ExtensionRecord

    manifest = ExtensionManifest(
        id="ntfy", name="ntfy", version="0.1.0", api_version="0.1.0",
        entrypoint="nodvard_deck_ext_ntfy:Extension", permissions=["net.outbound", "secrets.read:ntfy-token"],
    )
    runtime = ExtensionRuntime()
    loaded = LoadedExtension(manifest=manifest, instance=None, ctx=None, granted_permissions=manifest.permissions)
    ctx = build_context(runtime, loaded, manifest, manifest.permissions, tmp_path / "data", settings)
    loaded.ctx = ctx

    # SettingsHandle.set() (docs/02 §2) schreibt in die `extensions`-Zeile -- ohne sie
    # (normalerweise von services.extensions.discover_and_sync() angelegt) tut set()
    # nichts, still, ohne Fehler (siehe dortiger Docstring "ehrlich abgegrenzt").
    db_session.add(ExtensionRecord(id="ntfy", version="0.1.0", api_version="0.1.0", state="enabled"))
    await db_session.flush()

    extension = _ntfy_extension_class()()
    await extension.setup(ctx)
    await ctx.settings.set({"server_url": server_url, "topic": topic})
    return runtime, ctx, extension


@pytest.fixture
async def fake_ntfy_server():
    """Ein echter, lokal laufender uvicorn.Server, der ntfys JSON-Publish- und
    Health-Protokoll nachbildet -- kein Mock der `httpx`-Ebene, ein echter
    HTTP-Roundtrip (dieselbe Begruendung wie `running_app` in conftest.py)."""
    app = FastAPI()
    received: list[dict] = []
    healthy = {"value": True}

    @app.post("/")
    async def publish(request: Request, authorization: str | None = Header(default=None)):
        if healthy.get("write", 200) != 200:
            return JSONResponse(NO_ACCESS, status_code=healthy["write"])
        body = await request.json()
        received.append({"body": body, "authorization": authorization, "priority": request.headers.get("Priority")})
        return {"id": "fake-id"}

    @app.get("/v1/health")
    async def health():
        return {"healthy": healthy["value"]}

    @app.get("/{topic}/auth")
    async def topic_auth(topic: str):
        if healthy.get("auth", 200) == 200:
            return {"success": True}
        return JSONResponse(NO_ACCESS, status_code=healthy["auth"])

    @app.get("/v1/account")
    async def account():
        if healthy.get("account", 200) == 200:
            return {"username": "deck", "role": "user"}
        return JSONResponse({"code": 40101, "http": 401, "error": "unauthorized"}, status_code=401)

    @app.post("/{topic}")
    async def write_probe(topic: str, request: Request):
        healthy.setdefault("probes", []).append({"topic": topic, "headers": dict(request.headers)})
        if healthy.get("account", 200) == 401:
            return JSONResponse({"code": 40101, "http": 401, "error": "unauthorized"}, status_code=401)
        if healthy.get("write", 200) != 200:
            return JSONResponse(NO_ACCESS, status_code=healthy["write"])
        assert request.headers["Priority"] == "invalid"
        assert await request.body() == b""
        return JSONResponse(INVALID_PRIORITY, status_code=400)

    async with serve_app(app) as server_url:
        yield server_url, received, healthy


@pytest.mark.asyncio
async def test_send_posts_json_with_correct_topic_title_and_priority(
    fake_ntfy_server, db_session, tmp_path, test_settings
):
    server_url, received, _ = fake_ntfy_server
    _runtime, ctx, _ext = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url=server_url)

    from nodvard_sdk import Notification, Severity
    from nodvard_sdk.capabilities import NotificationChannel

    channel = _runtime.capabilities.query(NotificationChannel)[0]
    await channel.send(Notification(title="Ährger", body="Ünïcode-Körper", severity=Severity.CRITICAL))

    assert len(received) == 1
    body = received[0]["body"]
    assert body["topic"] == "test-topic"
    assert body["title"] == "Ährger"
    assert body["message"] == "Ünïcode-Körper"
    assert body["priority"] == 5
    assert received[0]["authorization"] is None


@pytest.mark.asyncio
async def test_send_includes_bearer_token_from_vault_when_configured(
    fake_ntfy_server, db_session, tmp_path, test_settings
):
    server_url, received, _ = fake_ntfy_server
    _runtime, ctx, _ext = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url=server_url)

    await ctx.secrets.create(label="ntfy-token", kind="generic", value="geheimes-token")

    from nodvard_sdk import Notification, Severity
    from nodvard_sdk.capabilities import NotificationChannel

    channel = _runtime.capabilities.query(NotificationChannel)[0]
    await channel.send(Notification(title="T", body="B", severity=Severity.INFO))

    assert received[0]["authorization"] == "Bearer geheimes-token"
    assert received[0]["body"]["priority"] == 3


@pytest.mark.asyncio
async def test_a_replaced_token_is_used_for_the_next_message_without_a_hook(
    fake_ntfy_server, db_session, tmp_path, test_settings
):
    server_url, received, _ = fake_ntfy_server
    runtime, ctx, _ext = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url=server_url)
    from nodvard_sdk import Notification, Severity
    from nodvard_sdk.capabilities import NotificationChannel

    channel = runtime.capabilities.query(NotificationChannel)[0]
    await ctx.secrets.create(label="ntfy-token", kind="generic", value="erstes-token")
    await channel.send(Notification(title="T", body="B", severity=Severity.INFO))
    await ctx.secrets.replace(label="ntfy-token", value="zweites-token")
    await channel.send(Notification(title="T", body="B", severity=Severity.INFO))

    assert [r["authorization"] for r in received] == ["Bearer erstes-token", "Bearer zweites-token"]


@pytest.mark.asyncio
async def test_connection_test_reads_the_current_token(fake_ntfy_server, db_session, tmp_path, test_settings, monkeypatch):
    import httpx
    from nodvard_sdk.capabilities import NotificationChannel

    server_url, _, _ = fake_ntfy_server
    runtime, ctx, _ext = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url=server_url)
    channel = runtime.capabilities.query(NotificationChannel)[0]
    seen = []
    expected = "Bearer erstes-token"

    def answer(request):
        seen.append(request.headers.get("authorization"))
        if seen[-1] != expected:
            return httpx.Response(401)
        if request.method == "POST":
            assert request.url.path == "/test-topic" and request.headers["Priority"] == "invalid"
            return httpx.Response(400, json=INVALID_PRIORITY)
        assert request.method == "GET" and request.url.path in ("/v1/health", "/test-topic/auth")
        return httpx.Response(200, json={"healthy": True} if request.url.path == "/v1/health" else {"success": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        monkeypatch.setattr(ctx.http, "_client", http)
        await ctx.secrets.create(label="ntfy-token", kind="generic", value="erstes-token")
        assert (await channel.test()).ok is True
        await ctx.secrets.replace(label="ntfy-token", value="falsch")
        assert (await channel.test()).ok is False
        expected = "Bearer zweites-token"
        await ctx.secrets.replace(label="ntfy-token", value="zweites-token")
        assert (await channel.test()).ok is True
        await ctx.secrets.delete("ntfy-token")
        assert (await channel.test()).ok is False
    assert seen == ["Bearer erstes-token"] * 3 + ["Bearer falsch"] + ["Bearer zweites-token"] * 3 + [None]


@pytest.mark.asyncio
async def test_send_without_configuration_raises_clearly(db_session, tmp_path, test_settings):
    _runtime, ctx, _ext = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url="http://127.0.0.1:1")
    await ctx.settings.set({})  # server_url/topic wieder entfernen

    from nodvard_sdk import Notification, Severity
    from nodvard_sdk.capabilities import NotificationChannel

    channel = _runtime.capabilities.query(NotificationChannel)[0]
    with pytest.raises(RuntimeError, match="noch nicht eingerichtet"):
        await channel.send(Notification(title="T", body="B", severity=Severity.INFO))


@pytest.mark.asyncio
async def test_test_reports_health_from_the_real_server(fake_ntfy_server, db_session, tmp_path, test_settings):
    server_url, _received, healthy = fake_ntfy_server
    _runtime, ctx, _ext = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url=server_url)

    from nodvard_sdk.capabilities import NotificationChannel

    channel = _runtime.capabilities.query(NotificationChannel)[0]
    ok = await channel.test()
    assert ok.ok is True

    healthy["value"] = False
    unhealthy = await channel.test()
    assert unhealthy.ok is False and "nicht gesund" in unhealthy.message


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_token_endpoint_requires_secrets_write_permission_and_is_idempotent_guarded(
    client, db_session, test_settings
):
    """Ende-zu-Ende ueber die echte, aktivierte Extension: beweist gleichzeitig, dass
    `ctx.api.include_router(..., permission=...)` (der Fix fuer die
    `dependency_overrides_provider`-Luecke) fuer eine ECHTE, ueber die Registry
    geladene Extension funktioniert, nicht nur fuer hello-worlds Testroute."""
    settings = test_settings.model_copy(update={"extensions_dir": REPO_EXTENSIONS_DIR})
    from nodvard_deck.services import extensions as extensions_service

    await extensions_service.discover_and_sync(db_session, settings)

    owner_token = await _bootstrap_owner(client)
    enabled = await client.post("/api/v1/extensions/ntfy/enable", headers=_auth_header(owner_token))
    assert enabled.status_code == 200, enabled.text

    unauthenticated = await client.post("/api/v1/ext/ntfy/token", json={"value": "x"})
    assert unauthenticated.status_code == 401

    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    viewer = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    viewer.roles.append(roles["viewer"])
    db_session.add(viewer)
    await db_session.flush()
    viewer_login = await client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"})
    viewer_token = viewer_login.json()["access_token"]

    forbidden = await client.post(
        "/api/v1/ext/ntfy/token", json={"value": "x"}, headers=_auth_header(viewer_token)
    )
    assert forbidden.status_code == 403

    created = await client.post(
        "/api/v1/ext/ntfy/token", json={"value": "geheim"}, headers=_auth_header(owner_token)
    )
    assert created.status_code == 204

    duplicate = await client.post(
        "/api/v1/ext/ntfy/token", json={"value": "anderes-geheim"}, headers=_auth_header(owner_token)
    )
    assert duplicate.status_code == 409


EVIL_LOCATION = "https://angreifer.example/phish?x=geheim"


@pytest.fixture
async def scripted_ntfy_server():
    """Lokaler Server, der jede Anfrage mit einem vorgegebenen Status und Headern beantwortet."""
    from fastapi import Response

    app = FastAPI()
    script: dict = {"status": 200, "headers": {}, "calls": []}

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def anything(path: str, request: Request):
        script["calls"].append(f"{request.method} /{path}")
        if script["status"] == 200:
            if path == "v1/health":
                return JSONResponse(script.get("health_body", {"healthy": True}))
            if path.endswith("/auth"):
                return JSONResponse(script.get("auth_body", {"success": True}))
            if request.method == "POST" and path:
                return JSONResponse(script.get("probe_body", INVALID_PRIORITY), status_code=script.get("probe_status", 400))
        return Response(content=b'{"error":"geheim-text-vom-server"}', status_code=script["status"], headers=script["headers"], media_type="application/json")

    async with serve_app(app) as server_url:
        yield server_url, script


async def _channel(db_session, tmp_path, test_settings, server_url: str):
    from nodvard_sdk.capabilities import NotificationChannel

    runtime, ctx, _ext = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url=server_url)
    return runtime.capabilities.query(NotificationChannel)[0], ctx


def _note():
    from nodvard_sdk import Notification, Severity

    return Notification(title="T", body="B", severity=Severity.INFO)


def _whole_chain(exc: BaseException) -> str:
    """Alles, was ein Fehlertext aus der Ursachenkette abschreiben koennte (wie `describe_exception`)."""
    parts, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return " | ".join(parts)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
async def test_send_redirect_is_a_readable_error_without_the_foreign_address(
    scripted_ntfy_server, db_session, tmp_path, test_settings, code
):
    server_url, script = scripted_ntfy_server
    script.update(status=code, headers={"Location": EVIL_LOCATION})
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url)

    with pytest.raises(RuntimeError) as exc:
        await channel.send(_note())

    text = str(exc.value)
    assert text.startswith(f"ntfy leitet auf eine andere Adresse um (HTTP {code}).")
    assert "endgültige Adresse" in text
    chain = _whole_chain(exc.value)
    assert "angreifer" not in chain.lower() and "geheim" not in chain and server_url not in chain
    assert script["calls"] == ["POST /"], "der Weiterleitung wird nicht gefolgt"


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["javascript:alert(1)", "http://127.0.0.1:angreifer-text/"])
async def test_send_unusable_location_header_never_reaches_the_error(scripted_ntfy_server, db_session, tmp_path, test_settings, location):
    server_url, script = scripted_ntfy_server
    script.update(status=302, headers={"Location": location})
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url)

    with pytest.raises(RuntimeError) as exc:
        await channel.send(_note())

    assert str(exc.value).startswith("ntfy leitet auf eine andere Adresse um")
    chain = _whole_chain(exc.value)
    assert "angreifer" not in chain.lower() and "alert" not in chain


@pytest.mark.asyncio
async def test_send_with_unusable_own_address_says_so(db_session, tmp_path, test_settings):
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, "http://127.0.0.1:abc")
    with pytest.raises(RuntimeError, match="Adresse des ntfy-Servers ist ungültig"):
        await channel.send(_note())
    result = await channel.test()
    assert result.ok is False and "ungültig" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (401, "Zugang abgelehnt (HTTP 401)"),
        (403, "Zugang abgelehnt (HTTP 403)"),
        (404, "antwortet kein ntfy-Server (HTTP 404)"),
        (429, "zu viele Anfragen"),
        (400, "nicht angenommen (HTTP 400)"),
        (500, "meldet einen Fehler (HTTP 500)"),
        (503, "meldet einen Fehler (HTTP 503)"),
    ],
)
async def test_send_error_statuses_are_readable_and_show_neither_url_nor_server_text(
    scripted_ntfy_server, db_session, tmp_path, test_settings, code, expected
):
    server_url, script = scripted_ntfy_server
    script.update(status=code)
    channel, ctx = await _channel(db_session, tmp_path, test_settings, server_url)
    await ctx.secrets.create(label="ntfy-token", kind="generic", value="token-geheim-123")

    with pytest.raises(RuntimeError) as exc:
        await channel.send(_note())

    chain = _whole_chain(exc.value)
    assert expected in str(exc.value)
    assert server_url not in chain and "127.0.0.1" not in chain, "keine Adresse im Text (sie kann Zugangsdaten tragen)"
    assert "geheim" not in chain
    assert "httpx" not in chain.lower() and "For more information" not in chain


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [200, 201, 204])
async def test_send_accepts_every_2xx(scripted_ntfy_server, db_session, tmp_path, test_settings, code):
    server_url, script = scripted_ntfy_server
    script.update(status=code)
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url)
    await channel.send(_note())


@pytest.mark.asyncio
async def test_test_does_not_call_a_redirect_a_working_connection(scripted_ntfy_server, db_session, tmp_path, test_settings):
    server_url, script = scripted_ntfy_server
    script.update(status=302, headers={"Location": EVIL_LOCATION})
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url)

    result = await channel.test()

    assert result.ok is False
    assert result.message.startswith("ntfy leitet auf eine andere Adresse um (HTTP 302).")
    assert "angreifer" not in result.message.lower() and "127.0.0.1" not in result.message
    assert script["calls"] == ["GET /v1/health"]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 404, 503])
async def test_test_reports_error_statuses_as_not_ok(scripted_ntfy_server, db_session, tmp_path, test_settings, code):
    server_url, script = scripted_ntfy_server
    script.update(status=code)
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url)
    result = await channel.test()
    assert result.ok is False
    assert f"HTTP {code}" in result.message and "127.0.0.1" not in result.message


@pytest.mark.asyncio
async def test_test_asks_for_the_topic_after_the_server(scripted_ntfy_server, db_session, tmp_path, test_settings):
    server_url, script = scripted_ntfy_server
    script.update(status=200)
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url)
    assert (await channel.test()).ok is True
    assert script["calls"] == ["GET /v1/health", "GET /test-topic/auth", "POST /test-topic"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"ok": True}, {"healthy": "true"}, {"healthy": 1}, [], None, "<html>Uptime Kuma</html>"])
async def test_test_rejects_a_foreign_server_returning_200(
    scripted_ntfy_server, db_session, tmp_path, test_settings, body
):
    server_url, script = scripted_ntfy_server
    script["health_body"] = body
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url)

    result = await channel.test()

    assert result.ok is False and "kein ntfy-Server" in result.message
    assert script["calls"] == ["GET /v1/health"]


@pytest.mark.asyncio
async def test_test_rejects_html_instead_of_ntfy_json(db_session, tmp_path, test_settings, monkeypatch):
    import httpx

    channel, ctx = await _channel(db_session, tmp_path, test_settings, "http://fremder-server")
    calls = []

    def answer(request):
        calls.append(request.method)
        return httpx.Response(200, text="<html>Uptime Kuma</html>", headers={"Content-Type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        monkeypatch.setattr(ctx.http, "_client", http)
        result = await channel.test()

    assert result.ok is False and "kein ntfy-Server" in result.message
    assert calls == ["GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("with_token", "read_status"), [(False, 403), (False, 200), (True, 200)])
async def test_test_rejects_missing_write_access(
    fake_ntfy_server, db_session, tmp_path, test_settings, with_token, read_status
):
    server_url, received, answers = fake_ntfy_server
    answers.update(auth=read_status, write=403)
    channel, ctx = await _channel(db_session, tmp_path, test_settings, server_url)
    if with_token:
        await ctx.secrets.create(label="ntfy-token", kind="generic", value="nur-lesen")

    result = await channel.test()

    assert result.ok is False and "Schreibrecht" in result.message and "HTTP 403" in result.message
    assert ("Dem Zugriffstoken" if with_token else "Ohne Zugriffstoken") in result.message
    assert "nur-lesen" not in result.message and server_url not in result.message
    assert len(answers["probes"]) == 1 and received == []
    with pytest.raises(RuntimeError, match="HTTP 403"):
        await channel.send(_note())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "body"),
    [(200, {"id": "fremd"}), (400, {}), (400, {"code": 40007}), (400, []),
     (400, {"code": 40009, "http": 400}), (503, {"error": "geheim"})],
)
async def test_test_requires_the_ntfy_priority_error_to_confirm_write_access(
    scripted_ntfy_server, db_session, tmp_path, test_settings, code, body
):
    server_url, script = scripted_ntfy_server
    script.update(probe_status=code, probe_body=body)
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url)

    result = await channel.test()

    assert result.ok is False
    assert "geheim" not in result.message and server_url not in result.message
    assert script["calls"][-1] == "POST /test-topic"


@pytest.mark.asyncio
async def test_test_rejects_an_unrecognized_topic_response(scripted_ntfy_server, db_session, tmp_path, test_settings):
    server_url, script = scripted_ntfy_server
    script["auth_body"] = {"ok": True}
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url)

    result = await channel.test()

    assert result.ok is False and "kein ntfy-Server" in result.message
    assert script["calls"] == ["GET /v1/health", "GET /test-topic/auth"]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 403])
@pytest.mark.parametrize("with_token", [True, False])
async def test_test_stays_green_when_only_reading_is_refused(
    fake_ntfy_server, db_session, tmp_path, test_settings, code, with_token
):
    """Ein Token nur mit Schreibrecht (oder ein Server mit Standardzugriff write-only) darf nicht rot werden."""
    from nodvard_sdk.capabilities import NotificationChannel

    server_url, received, answers = fake_ntfy_server
    answers["auth"] = code
    runtime, ctx, _ext = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url=server_url)
    if with_token:
        await ctx.secrets.create(label="ntfy-token", kind="generic", value="nur-schreiben")

    result = await runtime.capabilities.query(NotificationChannel)[0].test()

    assert result.ok is True
    assert result.message == WRITE_ONLY_NOTE and "Testnachricht senden" in result.message
    assert received == [] and len(answers["probes"]) == 1
    assert answers["probes"][0]["headers"].get("authorization") == ("Bearer nur-schreiben" if with_token else None)


@pytest.mark.asyncio
async def test_test_is_green_with_read_access(fake_ntfy_server, db_session, tmp_path, test_settings):
    server_url, received, answers = fake_ntfy_server
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url)
    assert (await channel.test()).ok is True
    assert received == [] and len(answers["probes"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 403])
async def test_test_is_red_for_a_rejected_token(fake_ntfy_server, db_session, tmp_path, test_settings, code):
    from nodvard_sdk.capabilities import NotificationChannel

    server_url, received, answers = fake_ntfy_server
    answers.update(auth=code, account=401)
    runtime, ctx, _ext = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url=server_url)
    await ctx.secrets.create(label="ntfy-token", kind="generic", value="falsches-token")

    result = await runtime.capabilities.query(NotificationChannel)[0].test()

    assert result.ok is False and result.message == TOKEN_REJECTED_TEXT
    assert "falsches-token" not in result.message and received == []


@pytest.mark.asyncio
async def test_test_is_red_when_the_server_is_not_reachable(db_session, tmp_path, test_settings):
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, "http://127.0.0.1:9")
    result = await channel.test()
    assert result.ok is False and "127.0.0.1" not in (result.message or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [400, 404])
async def test_test_is_not_ok_for_a_topic_the_server_does_not_know(fake_ntfy_server, db_session, tmp_path, test_settings, code):
    server_url, _received, answers = fake_ntfy_server
    answers["auth"] = code
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url)
    result = await channel.test()
    assert result.ok is False
    assert f"(HTTP {code})" in result.message and "anderes Thema" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", ["deck alarme", "deck-älarme", "deck.alarme", "deck/alarme", "a" * 65])
async def test_test_rejects_a_topic_name_ntfy_never_accepts(scripted_ntfy_server, db_session, tmp_path, test_settings, topic):
    server_url, script = scripted_ntfy_server
    script.update(status=200)
    _runtime, _ctx, _ext = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url=server_url, topic=topic)
    from nodvard_sdk.capabilities import NotificationChannel

    result = await _runtime.capabilities.query(NotificationChannel)[0].test()

    assert result.ok is False
    assert result.message.startswith("Dieses Thema nimmt ntfy nicht an.")
    assert script["calls"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", ["deck-alarme", "Deck_Alarme_2", "a" * 64])
async def test_test_accepts_every_valid_topic_name(scripted_ntfy_server, db_session, tmp_path, test_settings, topic):
    server_url, script = scripted_ntfy_server
    script.update(status=200)
    _runtime, _ctx, _ext = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url=server_url, topic=topic)
    from nodvard_sdk.capabilities import NotificationChannel

    assert (await _runtime.capabilities.query(NotificationChannel)[0].test()).ok is True
    assert script["calls"] == ["GET /v1/health", f"GET /{topic}/auth", f"POST /{topic}"]


@pytest.mark.asyncio
async def test_connection_test_of_the_settings_page_shows_the_topic_result_unchanged(
    fake_ntfy_server, db_session, tmp_path, test_settings
):
    from nodvard_deck.services.extension_test import run_connection_test

    server_url, _received, answers = fake_ntfy_server
    answers["auth"] = 403
    runtime, ctx, extension = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url=server_url)
    from nodvard_sdk.capabilities import NotificationChannel

    channel = runtime.capabilities.query(NotificationChannel)[0]
    loaded = SimpleNamespace(instance=extension, ctx=ctx)

    outcome = await run_connection_test(loaded, settings=await ctx.settings.get(), schema=None, secrets=[], channel=channel)

    assert outcome.ok is True and outcome.details[0].message == WRITE_ONLY_NOTE

    answers["auth"] = 404
    outcome = await run_connection_test(loaded, settings=await ctx.settings.get(), schema=None, secrets=[], channel=channel)
    assert outcome.ok is False and outcome.message.startswith("Der ntfy-Server ist erreichbar, kennt das Thema aber so nicht (HTTP 404).")

    answers["auth"] = 200
    outcome = await run_connection_test(loaded, settings=await ctx.settings.get(), schema=None, secrets=[], channel=channel)
    assert outcome.ok is True and outcome.message == "Verbindung funktioniert."

    answers.update(auth=403, write=403)
    outcome = await run_connection_test(loaded, settings=await ctx.settings.get(), schema=None, secrets=[], channel=channel)
    assert outcome.ok is False and outcome.message == outcome.details[0].message
    assert outcome.message.startswith("Ohne Zugriffstoken fehlt das Schreibrecht")

    await ctx.secrets.create(label="ntfy-token", kind="generic", value="nur-lesen")
    answers["auth"] = 200
    outcome = await run_connection_test(loaded, settings=await ctx.settings.get(), schema=None, secrets=[], channel=channel)
    assert outcome.ok is False and outcome.message == outcome.details[0].message
    assert outcome.message.startswith("Dem Zugriffstoken fehlt das Schreibrecht")


@pytest.mark.asyncio
async def test_test_message_flow_of_the_settings_page_never_shows_the_redirect_target(
    scripted_ntfy_server, db_session, tmp_path, test_settings
):
    """Der Weg hinter „Testnachricht senden“: `describe_exception` schreibt die ganze Ursachenkette ab."""
    from nodvard_deck.services.extension_test import run_connection_test, send_test_message

    server_url, script = scripted_ntfy_server
    script.update(status=302, headers={"Location": EVIL_LOCATION})
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url)

    sent = await send_test_message(channel=channel, settings={"server_url": server_url}, schema=None, secrets=[], product_name="Nodvard Deck")
    assert sent.ok is False
    assert "angreifer" not in sent.message.lower() and "geheim" not in sent.message
    assert "andere Adresse" in sent.message

    async def health(_ctx):
        from nodvard_sdk import HealthReport

        result = await channel.test()
        return HealthReport(healthy=result.ok, message=result.message)

    loaded = SimpleNamespace(instance=SimpleNamespace(health=health), ctx=None)
    checked = await run_connection_test(loaded, settings={"server_url": server_url}, schema=None, secrets=[], channel=channel)
    assert checked.ok is False
    assert "angreifer" not in checked.message.lower()


RAW_CASES = [(name, "ntfy leitet auf eine andere Adresse um.") for name in REDIRECT_ANSWERS] + [
    (name, "Der ntfy-Server hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt.") for name in BROKEN_ANSWERS
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("answer", "sentence"), RAW_CASES)
async def test_broken_answers_of_the_real_parser_never_reach_the_error(db_session, tmp_path, test_settings, answer, sentence):
    """Kaputte Kopfzeilen scheitern schon in h11, dessen Text die Zeile zitiert (auch einen `Location`-Header);
    ein ungueltiger Punycode-Name in der Weiterleitung scheitert in httpx mit einem `ValueError` (idna).
    Weder das Zustellprotokoll (`str(exc)` von `send`) noch die Verbindungspruefung bekommt davon etwas mit."""
    from nodvard_deck.services.extension_test import send_test_message

    async with raw_http_server({**REDIRECT_ANSWERS, **BROKEN_ANSWERS}[answer]) as server_url:
        channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url)
        with pytest.raises(RuntimeError) as exc:
            await channel.send(_note())
        result = await channel.test()
        sent = await send_test_message(channel=channel, settings={"server_url": server_url}, schema=None, secrets=[], product_name="Nodvard Deck")

    assert str(exc.value).startswith(sentence)
    assert result.ok is False and result.message.startswith(sentence)
    assert sent.ok is False
    for text in (_whole_chain(exc.value), result.message, sent.message):
        assert "angreifer" not in text.lower() and "bytearray" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["tk_geheim_123\nX-Extra: 1", "tk_geheim_\u00e4123"])
async def test_a_broken_token_never_shows_up_in_the_error(scripted_ntfy_server, db_session, tmp_path, test_settings, token):
    """Ein Zeilenumbruch im Token (etwa beim Einfuegen mitkopiert): h11 lehnt den Header ab und nennt ihn
    samt Wert (`Illegal header value b'Bearer ...'`); ein Umlaut scheitert schon beim Kodieren."""
    server_url, script = scripted_ntfy_server
    channel, ctx = await _channel(db_session, tmp_path, test_settings, server_url)
    await ctx.secrets.create(label="ntfy-token", kind="generic", value=token)

    with pytest.raises(RuntimeError) as exc:
        await channel.send(_note())

    assert str(exc.value).startswith("Die Anfrage an ntfy ließ sich nicht senden.")
    assert "tk_geheim" not in _whole_chain(exc.value)
    assert script["calls"] == []


@pytest.mark.asyncio
async def test_test_reports_unhealthy_when_server_unreachable(db_session, tmp_path, test_settings):
    _runtime, ctx, _ext = await _setup_ntfy_extension(
        db_session, tmp_path, test_settings, server_url="http://127.0.0.1:1"
    )
    from nodvard_sdk.capabilities import NotificationChannel

    channel = _runtime.capabilities.query(NotificationChannel)[0]
    result = await channel.test()
    assert result.ok is False
    assert "lehnt die Verbindung ab" in result.message or "nicht erreichbar" in result.message
    for raw in ("Errno", "refused", "All connection attempts", "127.0.0.1"):
        assert raw not in result.message


@pytest.mark.parametrize(
    ("exc", "fragment"),
    [
        (httpx.ReadTimeout("timed out"), "antwortet nicht (Zeitüberschreitung)"),
        (httpx.ConnectError("[Errno 111] Connection refused"), "lehnt die Verbindung ab"),
        (httpx.ConnectError("[Errno -2] Name or service not known"), "lässt sich nicht auflösen"),
        (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), "Zertifikat"),
        (httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER] wrong version number"), "Verschlüsselung passt nicht"),
        (httpx.ConnectError("All connection attempts failed"), "nicht erreichbar"),
    ],
)
def test_connection_text_is_a_fixed_german_sentence(exc, fragment):
    _ntfy_extension_class()
    from nodvard_deck_ext_ntfy import connection_text

    text = connection_text(exc)
    assert fragment in text
    assert not any(raw in text for raw in ("Errno", "SSL", "refused", "attempts", "timed out"))


def test_build_message_links_to_the_dashboard_and_adds_symbols():
    _ntfy_extension_class()
    from nodvard_deck_ext_ntfy import build_message
    from nodvard_sdk import Notification, Severity

    n = Notification(
        title="Neuer offener Port", body="4444/tcp", severity=Severity.WARNING,
        payload={"path": "/ext/shield/soc?tab=guard", "actions": [{"label": "Einbruchschutz", "path": "ext/shield/soc?tab=guard"}, {"label": "kaputt"}]},
    )
    body = build_message(n, "t", "http://192.168.1.72:8080/")
    assert body["click"] == "http://192.168.1.72:8080/ext/shield/soc?tab=guard"
    assert body["actions"] == [{"action": "view", "label": "Einbruchschutz", "url": "http://192.168.1.72:8080/ext/shield/soc?tab=guard"}]
    assert body["tags"] == ["warning"] and body["priority"] == 4

    plain = build_message(Notification(title="x", body="y", payload={"tags": ["coffee"]}), "t", None)
    assert "click" not in plain and "actions" not in plain and plain["tags"] == ["coffee"]
    assert build_message(Notification(title="x", body="y"), "t", "http://d")["click"] == "http://d/notifications"
