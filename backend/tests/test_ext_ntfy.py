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

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, Header, Request

from nodvard_deck.ext.context import build_context
from nodvard_deck.ext.runtime import ExtensionRuntime, LoadedExtension

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


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
    import socket

    import uvicorn

    app = FastAPI()
    received: list[dict] = []
    healthy = {"value": True}

    @app.post("/")
    async def publish(request: Request, authorization: str | None = Header(default=None)):
        body = await request.json()
        received.append({"body": body, "authorization": authorization})
        return {"id": "fake-id"}

    @app.get("/v1/health")
    async def health():
        return {"healthy": healthy["value"]}

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    serve_task = asyncio.ensure_future(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)

    try:
        yield f"http://127.0.0.1:{port}", received, healthy
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(serve_task, timeout=5.0)
        except asyncio.TimeoutError:
            serve_task.cancel()


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
async def test_send_without_configuration_raises_clearly(db_session, tmp_path, test_settings):
    _runtime, ctx, _ext = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url="http://127.0.0.1:1")
    await ctx.settings.set({})  # server_url/topic wieder entfernen

    from nodvard_sdk import Notification, Severity
    from nodvard_sdk.capabilities import NotificationChannel

    channel = _runtime.capabilities.query(NotificationChannel)[0]
    with pytest.raises(RuntimeError, match="nicht konfiguriert"):
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
    still_ok_http = await channel.test()
    assert still_ok_http.ok is True  # HTTP 200 bleibt 200, auch wenn der Body "unhealthy" sagt (kein 5xx)


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


@pytest.mark.asyncio
async def test_test_reports_unhealthy_when_server_unreachable(db_session, tmp_path, test_settings):
    _runtime, ctx, _ext = await _setup_ntfy_extension(
        db_session, tmp_path, test_settings, server_url="http://127.0.0.1:1"
    )
    from nodvard_sdk.capabilities import NotificationChannel

    channel = _runtime.capabilities.query(NotificationChannel)[0]
    result = await channel.test()
    assert result.ok is False


def test_build_message_links_to_the_dashboard_and_adds_symbols():
    _ntfy_extension_class()
    from nodvard_deck_ext_ntfy import build_message
    from nodvard_sdk import Notification, Severity

    n = Notification(
        title="Neuer offener Port", body="4444/tcp", severity=Severity.WARNING,
        payload={"path": "/ext/nexus-soc/soc?tab=guard", "actions": [{"label": "Einbruchschutz", "path": "ext/nexus-soc/soc?tab=guard"}, {"label": "kaputt"}]},
    )
    body = build_message(n, "t", "http://192.168.1.72:8080/")
    assert body["click"] == "http://192.168.1.72:8080/ext/nexus-soc/soc?tab=guard"
    assert body["actions"] == [{"action": "view", "label": "Einbruchschutz", "url": "http://192.168.1.72:8080/ext/nexus-soc/soc?tab=guard"}]
    assert body["tags"] == ["warning"] and body["priority"] == 4

    plain = build_message(Notification(title="x", body="y", payload={"tags": ["coffee"]}), "t", None)
    assert "click" not in plain and "actions" not in plain and plain["tags"] == ["coffee"]
    assert build_message(Notification(title="x", body="y"), "t", "http://d")["click"] == "http://d/notifications"
