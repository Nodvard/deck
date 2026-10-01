"""nexus-soc-Extension -- Ende-zu-Ende gegen einen echten, lokal laufenden
Ollama-API-Mock (echtes HTTP/JSON auf einem echten Port), wie schon
`test_ext_proxmox.py` fuer die Proxmox-API. Nutzt die ECHTE `extensions/nexus-soc`-
Extension (Repo-Pfad) ueber die reale API, wie `test_extensions_api.py` es fuer
hello-world tut.

**Ehrlich abgegrenzt wie bei proxmox:** kein API-Weg fuer `ollama_url` als Extension-
Einstellung (`PUT .../settings` zurueckgestellt) -- direkt ueber die DB
gesetzt. Der Docker-Waechter selbst (SSH-`ctx.exec.run()`-Aufrufe) ist NICHT Teil
dieses Tests -- er ist bereits unit-getestet (`test_ext_nexus_soc_watcher.py`, mit
einem Fake-`ctx`), und `ctx.exec.run()` selbst ist bereits gegen einen echten
Test-SSH-Server bewiesen (`test_ext_terminal.py`). Dieser Test prueft
stattdessen die END-ZU-ENDE-KETTE Chat/Batch -> Ollama -> `parse_ai_response()` ->
`ctx.actions.propose()` -> echte `Action`-Zeile -- ausschliesslich mit einer echten
Ollama-Gegenstelle, keinem Mock von `httpx` selbst.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI, Request

from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import ExtensionRecord, Host
from nodvard_deck.services import extensions as extensions_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


@pytest.fixture(autouse=True)
async def _create_nexus_soc_tables(db_session):
    """Vorfalls-Historie: nexus-soc hat jetzt eine eigene Tabelle (eigener Alembic-
    Branch) -- die Test-DB legt nur die Kern-Tabellen an, wie bei documents/inventory."""
    import importlib.util
    import sys

    name = "_nexus_soc_models_for_tests"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(
            name, REPO_EXTENSIONS_DIR / "nexus-soc" / "src" / "nodvard_deck_ext_nexus_soc" / "models.py"
        )
        module = importlib.util.module_from_spec(spec)
        # SQLAlchemy loest `Mapped[...]`-Annotationen ueber sys.modules auf.
        sys.modules[name] = module
        spec.loader.exec_module(module)
    conn = await db_session.connection()
    await conn.run_sync(module.Base.metadata.create_all)

_CRASH_REPLY = (
    "Lagebericht: nginx-proxy ist wegen eines Speicherfehlers abgestuerzt.\n"
    "NEXUS-Entscheidung:\n"
    "BEGRUENDUNG: Wiederholter Absturz durch OOM, ein Neustart behebt das kurzfristig\n"
    "AKTION: EXEC docker docker restart nginx-proxy"
)
_FORBIDDEN_REPLY = (
    "NEXUS-Entscheidung:\nBEGRUENDUNG: pve2 scheint offline zu sein\n"
    "AKTION: EXEC docker docker restart power_server"
)
_NONE_REPLY = "Lagebericht: alles normal.\nNEXUS-Entscheidung:\nBEGRUENDUNG: kein Handlungsbedarf\nAKTION: KEINE"


def _build_mock_ollama_app() -> tuple[FastAPI, dict]:
    state = {"next_reply": _NONE_REPLY, "calls": []}
    app = FastAPI()

    @app.post("/api/generate")
    async def generate(request: Request) -> dict:
        body = await request.json()
        state["calls"].append(body)
        return {"response": state["next_reply"]}

    @app.get("/api/tags")
    async def tags() -> dict:
        return {"models": []}

    return app, state


@pytest_asyncio.fixture
async def mock_ollama():
    app, state = _build_mock_ollama_app()
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
        yield f"http://127.0.0.1:{port}", state
    finally:
        server.should_exit = True
        await serve_task


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _setup_nexus_soc(client, db_session, test_settings, ollama_url: str) -> str:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)

    token = await _bootstrap_owner(client)
    enabled = await client.post("/api/v1/extensions/nexus-soc/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["state"] == "enabled"

    record = await db_session.get(ExtensionRecord, "nexus-soc")
    record.settings = {"ollama_url": ollama_url}
    await db_session.flush()
    return token


@pytest.mark.asyncio
async def test_ai_models_route_uses_only_saved_addresses(client, db_session, test_settings, mock_ollama):
    """Auswahlliste "KI-Modell": der Mock kennt keine Modelle -> Hinweis, kein Fehlerstatus.
    Sicherheits-Review: eine mitgeschickte Adresse (`url`) wird ignoriert -- die Route fragt nur
    die gespeicherten Adressen, sonst waere sie ein Weg, Anfragen (mit Schluessel) ins
    Heimnetz zu schicken."""
    ollama_url, _state = mock_ollama
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    r = await client.get("/api/v1/ext/nexus-soc/ai/models", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert r.json() == {"options": [], "error": "Auf dem KI-Server ist noch kein Modell geladen."}
    # fremde Adresse: wird ignoriert, Antwort kommt vom gespeicherten Mock (nicht "nicht erreichbar")
    foreign = await client.get("/api/v1/ext/nexus-soc/ai/models", params={"url": "http://127.0.0.1:9"}, headers=_auth_header(token))
    assert foreign.status_code == 200 and foreign.json() == r.json()
    # Ersatz-Server ohne gespeicherte Adresse
    failover = await client.get("/api/v1/ext/nexus-soc/ai/models", params={"which": "failover"}, headers=_auth_header(token))
    assert failover.json()["options"] == [] and "Noch keine Adresse" in failover.json()["error"]
    assert (await client.get("/api/v1/ext/nexus-soc/ai/models", params={"which": "http://x"}, headers=_auth_header(token))).status_code == 422


@pytest.mark.asyncio
async def test_requirements_root_and_docker_group_follow_the_docker_tag(client, db_session, test_settings, mock_ollama):
    """Update-Zentrale, Quarantaene & Co. brauchen root ohne Passwort, die KI-Container-Wache
    die Gruppe "docker" auf den ueberwachten Servern (Tag `docker_host_tag`)."""
    ollama_url, _state = mock_ollama
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    runtime = get_extension_runtime()

    def specs() -> dict:
        return {r.id: r for e, r in runtime.ui.all_host_requirements() if e == "nexus-soc"}

    root = specs()["root"]
    assert root.needs_root is True and root.tags == [] and root.os_families == ["linux"]
    for word in ("Updates", "Quarantäne", "Härtungs-Audit", "Fail2ban"):
        assert word in root.root_reason
    assert specs()["docker-group"].unix_group == "docker"
    assert specs()["docker-group"].tags == ["docker"]
    assert specs()["docker-group"].check_command == "docker ps -q"

    await runtime.loaded["nexus-soc"].ctx.settings.set({"ollama_url": ollama_url, "docker_host_tag": "box"})
    assert specs()["docker-group"].tags == ["box"]
    assert specs()["root"].tags == []
    assert sorted(specs()) == ["docker-group", "root"]

    assert (await client.post("/api/v1/extensions/nexus-soc/disable", headers=_auth_header(token))).status_code == 200
    assert specs() == {}


@pytest.mark.asyncio
async def test_chat_proposes_a_gate_action_for_a_real_exec_response(client, db_session, test_settings, mock_ollama):
    ollama_url, state = mock_ollama
    state["next_reply"] = _CRASH_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)

    await client.post("/api/v1/hosts", json={"name": "docker", "address": "10.0.0.5"}, headers=_auth_header(token))

    r = await client.post(
        "/api/v1/ext/nexus-soc/chat", json={"message": "Wie geht es nginx?"}, headers=_auth_header(token)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "BEGRUENDUNG" not in body["reply"]
    assert "AKTION" not in body["reply"]
    assert len(body["proposals"]) == 1
    assert "docker restart nginx-proxy" in body["proposals"][0]
    assert "proposed" in body["proposals"][0]

    actions = await client.get("/api/v1/actions", headers=_auth_header(token))
    assert actions.status_code == 200
    rows = actions.json()
    matching = [a for a in rows if a["action_type"] == "shell.exec"]
    assert len(matching) == 1
    assert matching[0]["status"] == "proposed"
    assert matching[0]["proposed_by_type"] == "ai"
    assert matching[0]["reason"]

    assert len(state["calls"]) == 1
    assert state["calls"][0]["model"] == "qwen2.5:7b"


@pytest.mark.asyncio
async def test_chat_rejects_forbidden_host_keyword_without_proposing(client, db_session, test_settings, mock_ollama):
    ollama_url, state = mock_ollama
    state["next_reply"] = _FORBIDDEN_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    await client.post("/api/v1/hosts", json={"name": "docker", "address": "10.0.0.5"}, headers=_auth_header(token))

    r = await client.post(
        "/api/v1/ext/nexus-soc/chat", json={"message": "Ist pve2 erreichbar?"}, headers=_auth_header(token)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["proposals"]) == 1
    assert body["proposals"][0].startswith("Abgelehnt:")
    assert "power_server" in body["proposals"][0]

    actions = await client.get("/api/v1/actions", headers=_auth_header(token))
    assert actions.json() == []

    audit = await client.get("/api/v1/audit", params={"action": "nexus_soc.proposal_rejected"}, headers=_auth_header(token))
    assert len(audit.json()) == 1


@pytest.mark.asyncio
async def test_chat_with_no_action_creates_no_proposal(client, db_session, test_settings, mock_ollama):
    ollama_url, state = mock_ollama
    state["next_reply"] = _NONE_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)

    r = await client.post(
        "/api/v1/ext/nexus-soc/chat", json={"message": "Status?"}, headers=_auth_header(token)
    )
    assert r.status_code == 200
    assert r.json()["proposals"] == []


@pytest.mark.asyncio
async def test_incidents_widget_reflects_a_processed_batch(client, db_session, test_settings, mock_ollama):
    """Ruft die Batch-Verarbeitung DIREKT auf (statt 60s auf den echten Timer zu
    warten) -- derselbe Code, den `_batch_flush_loop` nach Ablauf des Fensters
    aufruft. Beweist die Kette Vorfall -> Warteschlange -> KI -> Vorschlag ->
    Vorfalls-Widget, ohne echte Docker-/SSH-Infrastruktur (siehe Modul-Docstring)."""
    from nodvard_deck.ext.runtime import get_extension_runtime

    ollama_url, state = mock_ollama
    state["next_reply"] = _CRASH_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    host_resp = await client.post(
        "/api/v1/hosts", json={"name": "docker", "address": "10.0.0.5"}, headers=_auth_header(token)
    )
    host_id = host_resp.json()["id"]

    from nodvard_deck_ext_nexus_soc.watcher import ContainerTransition

    loaded = get_extension_runtime().loaded["nexus-soc"]
    instance = loaded.instance

    transition = ContainerTransition(
        host=type("H", (), {"id": host_id, "name": "docker"})(),
        target="nginx-proxy",
        message="Container CRASH (Exited (1))",
        details={"status": "Exited (1)", "state": "exited", "is_crash": True},
        is_crash=True,
    )
    await instance._on_transition(transition)
    assert instance._batch_ready.is_set()

    batch = instance._store.take_batch()
    assert len(batch) == 1
    await instance._process_batch(loaded.ctx, batch)

    r = await client.get("/api/v1/ext/nexus-soc/widgets/incidents", headers=_auth_header(token))
    assert r.status_code == 200
    rows = r.json()["data"]
    assert len(rows) == 1
    assert rows[0]["host"] == "docker"
    assert rows[0]["target"] == "nginx-proxy"
    assert rows[0]["severity"] == "warning"  # status wurde durch die Verarbeitung "proposed"

    confirm = await client.post(
        f"/api/v1/ext/nexus-soc/incidents/{rows[0]['id']}/dismiss", headers=_auth_header(token)
    )
    assert confirm.status_code == 200
    r2 = await client.get("/api/v1/ext/nexus-soc/widgets/incidents", headers=_auth_header(token))
    assert r2.json()["data"] == []


def _crash_transition(host_id: str, host_name: str, target: str):
    from nodvard_deck_ext_nexus_soc.watcher import ContainerTransition

    return ContainerTransition(
        host=type("H", (), {"id": host_id, "name": host_name})(), target=target,
        message="Container CRASH (Exited (1))",
        details={"status": "Exited (1)", "state": "exited", "is_crash": True}, is_crash=True,
    )


async def _queue_crash(client, token, instance, *, host_name: str, target: str) -> str:
    resp = await client.post("/api/v1/hosts", json={"name": host_name, "address": "10.0.0.5"}, headers=_auth_header(token))
    host_id = resp.json()["id"]
    await instance._on_transition(_crash_transition(host_id, host_name, target))
    return host_id


async def _incident_report_payloads(db_session) -> list[dict]:
    from sqlalchemy import select

    from nodvard_deck.models import Notification as NotificationRow

    rows = (await db_session.execute(
        select(NotificationRow).where(NotificationRow.source_ext_id == "nexus-soc").order_by(NotificationRow.ts)
    )).scalars().all()
    return [r.payload for r in rows]


class _RecordingChannel:
    """Benachrichtigungskanal, der nur mitschreibt, was der Kern wirklich zustellt."""

    channel_id = "test-kanal"
    label = "Test-Kanal"

    def __init__(self) -> None:
        self.received: list = []

    async def send(self, notification) -> None:
        self.received.append(notification)


def _running_window(host_ids) -> list[dict]:
    """Ein vor 5 Minuten gestartetes Wartungsfenster (Cron in Ortszeit wie der Waehler)."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from nodvard_deck.config import LOCAL_TIMEZONE
    from nodvard_deck.db import utcnow

    start = (utcnow() - timedelta(minutes=5)).astimezone(ZoneInfo(LOCAL_TIMEZONE))
    return [{"cron": f"{start.minute} {start.hour} * * *", "duration_minutes": 60, "host_ids": host_ids}]


@pytest.mark.asyncio
async def test_incident_report_is_silenced_end_to_end_by_the_maintenance_window_of_its_host(
    client, db_session, test_settings, mock_ollama
):
    """Der Lagebericht zu Vorfaellen EINES Servers traegt dessen Host-ID --
    ein Wartungsfenster fuer diesen Server haelt den Push zurueck, eins fuer einen anderen
    nicht."""
    from nodvard_sdk.capabilities import NotificationChannel

    from nodvard_deck.services import settings as settings_service

    ollama_url, state = mock_ollama
    state["next_reply"] = _CRASH_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    loaded = get_extension_runtime().loaded["nexus-soc"]
    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)

    host_id = await _queue_crash(client, token, loaded.instance, host_name="docker", target="nginx-proxy")
    await settings_service.set_global(db_session, "maintenance.windows", _running_window(["anderer-host"]))
    await loaded.instance._process_batch(loaded.ctx, loaded.instance._store.take_batch())
    assert len(channel.received) == 1
    assert channel.received[0].payload == {"host_id": host_id}

    await loaded.instance._on_transition(_crash_transition(host_id, "docker", "redis"))
    await settings_service.set_global(db_session, "maintenance.windows", _running_window([host_id]))
    await loaded.instance._process_batch(loaded.ctx, loaded.instance._store.take_batch())
    assert len(channel.received) == 1, "im Wartungsfenster des Servers nicht zugestellt"
    assert await _incident_report_payloads(db_session) == [{"host_id": host_id}, {"host_id": host_id}]


@pytest.mark.asyncio
async def test_incident_report_over_several_hosts_is_silenced_only_when_all_are_in_the_window(
    client, db_session, test_settings, mock_ollama
):
    """Ein Lagebericht ueber Vorfaelle mehrerer Server ist ein Sammelbericht: er traegt
    alle Server (`host_ids`) und bleibt hoerbar, solange einer davon nicht im Fenster liegt."""
    from nodvard_sdk.capabilities import NotificationChannel

    from nodvard_deck.services import settings as settings_service

    ollama_url, state = mock_ollama
    state["next_reply"] = _CRASH_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    loaded = get_extension_runtime().loaded["nexus-soc"]
    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)

    first = await _queue_crash(client, token, loaded.instance, host_name="docker", target="nginx-proxy")
    second = await _queue_crash(client, token, loaded.instance, host_name="docker2", target="redis")
    await settings_service.set_global(db_session, "maintenance.windows", _running_window([first]))
    batch = loaded.instance._store.take_batch()
    assert len(batch) == 2
    await loaded.instance._process_batch(loaded.ctx, batch)
    assert len(channel.received) == 1, "nur einer der beiden Server im Fenster: der Bericht kommt an"

    await loaded.instance._on_transition(_crash_transition(first, "docker", "redis"))
    await loaded.instance._on_transition(_crash_transition(second, "docker2", "nginx-proxy"))
    await settings_service.set_global(db_session, "maintenance.windows", _running_window([first, second]))
    await loaded.instance._process_batch(loaded.ctx, loaded.instance._store.take_batch())
    assert len(channel.received) == 1, "beide Server im Fenster: nicht zugestellt"
    assert await _incident_report_payloads(db_session) == [{"host_ids": sorted([first, second])}] * 2


@pytest.mark.asyncio
async def test_incidents_endpoint_returns_full_detail_including_dismissed(client, db_session, test_settings, mock_ollama):
    """`GET /incidents` (SocPage.tsx braucht ein echtes
    Monitoring statt eines Chat-UIs) liefert -- anders als das absichtlich auf
    das Widget-Format verdichtete `GET /widgets/incidents` -- den vollen
    Datensatz UND auch bereits verworfene Vorfaelle, inkl. `status_filter`."""
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck_ext_nexus_soc.watcher import ContainerTransition

    ollama_url, state = mock_ollama
    state["next_reply"] = _CRASH_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    host_resp = await client.post(
        "/api/v1/hosts", json={"name": "docker", "address": "10.0.0.5"}, headers=_auth_header(token)
    )
    host_id = host_resp.json()["id"]

    loaded = get_extension_runtime().loaded["nexus-soc"]
    instance = loaded.instance
    transition = ContainerTransition(
        host=type("H", (), {"id": host_id, "name": "docker"})(),
        target="nginx-proxy", message="Container CRASH (Exited (1))",
        details={"status": "Exited (1)", "state": "exited", "is_crash": True}, is_crash=True,
    )
    await instance._on_transition(transition)
    batch = instance._store.take_batch()
    await instance._process_batch(loaded.ctx, batch)

    all_rows = await client.get("/api/v1/ext/nexus-soc/incidents", headers=_auth_header(token))
    assert all_rows.status_code == 200, all_rows.text
    rows = all_rows.json()
    assert len(rows) == 1
    incident_id = rows[0]["id"]
    assert rows[0]["host_name"] == "docker"
    assert rows[0]["target"] == "nginx-proxy"
    assert rows[0]["status"] == "proposed"
    assert rows[0]["is_crash"] is True
    assert rows[0]["action_id"] is not None
    assert "docker restart nginx-proxy" in rows[0]["ai_summary"]

    filtered_out = await client.get(
        "/api/v1/ext/nexus-soc/incidents", params={"status_filter": "dismissed"}, headers=_auth_header(token)
    )
    assert filtered_out.json() == []

    dismissed = await client.post(
        f"/api/v1/ext/nexus-soc/incidents/{incident_id}/dismiss", headers=_auth_header(token)
    )
    assert dismissed.status_code == 200

    # Anders als das Widget (blendet dismissed absichtlich aus) bleibt der
    # verworfene Vorfall hier weiter sichtbar -- eine echte Detailseite darf
    # die eigene Historie nicht verstecken.
    still_visible = await client.get("/api/v1/ext/nexus-soc/incidents", headers=_auth_header(token))
    assert len(still_visible.json()) == 1
    assert still_visible.json()[0]["status"] == "dismissed"

    now_filtered = await client.get(
        "/api/v1/ext/nexus-soc/incidents", params={"status_filter": "dismissed"}, headers=_auth_header(token)
    )
    assert len(now_filtered.json()) == 1


@pytest.mark.asyncio
async def test_stats_endpoint_reflects_incident_counts_and_ai_health(client, db_session, test_settings, mock_ollama):
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck_ext_nexus_soc.watcher import ContainerTransition

    ollama_url, state = mock_ollama
    state["next_reply"] = _CRASH_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    # `docker_host_tag` (Default "docker", siehe stats()/chat()) filtert ueber den
    # TAG, nicht ueber den Host-Namen -- ohne `tags` hier waere `watched_hosts`
    # immer 0, unabhaengig davon, wie der Host heisst.
    host_resp = await client.post(
        "/api/v1/hosts", json={"name": "docker", "address": "10.0.0.5", "tags": ["docker"]}, headers=_auth_header(token)
    )
    host_id = host_resp.json()["id"]

    empty_stats = await client.get("/api/v1/ext/nexus-soc/stats", headers=_auth_header(token))
    assert empty_stats.status_code == 200, empty_stats.text
    assert empty_stats.json()["by_status"] == {}
    assert empty_stats.json()["watched_hosts"] == 1
    assert empty_stats.json()["ai_healthy"] is True

    loaded = get_extension_runtime().loaded["nexus-soc"]
    instance = loaded.instance
    transition = ContainerTransition(
        host=type("H", (), {"id": host_id, "name": "docker"})(),
        target="nginx-proxy", message="Container CRASH", details={"is_crash": True}, is_crash=True,
    )
    await instance._on_transition(transition)
    # NICHT `take_batch()` -- dieser Test prueft `pending_batch`, das braucht
    # den Vorfall unverarbeitet noch in der Warteschlange.
    stats_with_pending = await client.get("/api/v1/ext/nexus-soc/stats", headers=_auth_header(token))
    assert stats_with_pending.json()["pending_batch"] == 1

    batch = instance._store.take_batch()
    await instance._process_batch(loaded.ctx, batch)

    final_stats = await client.get("/api/v1/ext/nexus-soc/stats", headers=_auth_header(token))
    assert final_stats.json()["by_status"] == {"proposed": 1}
    assert final_stats.json()["pending_batch"] == 0


@pytest.mark.asyncio
async def test_no_action_incident_still_gets_a_durable_audit_entry(client, db_session, test_settings, mock_ollama):
    """Frage: reicht das Audit-Log allein fuer Schattenbetrieb,
    ohne eine eigene Incident-Tabelle? Vorher: nein -- ein 'AKTION: KEINE'-Vorfall
    hinterliess UEBERHAUPT KEINE Spur ausserhalb des Prozessspeichers. Dieser Test
    haelt die Antwort fest: JEDER Vorfall bekommt jetzt einen `nexus_soc.incident`-
    Audit-Eintrag, unabhaengig vom Ausgang."""
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck_ext_nexus_soc.watcher import ContainerTransition

    ollama_url, state = mock_ollama
    state["next_reply"] = _NONE_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    host_resp = await client.post(
        "/api/v1/hosts", json={"name": "docker", "address": "10.0.0.5"}, headers=_auth_header(token)
    )
    host_id = host_resp.json()["id"]

    loaded = get_extension_runtime().loaded["nexus-soc"]
    instance = loaded.instance
    transition = ContainerTransition(
        host=type("H", (), {"id": host_id, "name": "docker"})(),
        target="redis",
        message="Container MANUELLER STOP (Exited (0))",
        details={"status": "Exited (0)", "state": "exited", "is_crash": False},
        is_crash=False,
    )
    await instance._on_transition(transition)
    batch = instance._store.take_batch()
    await instance._process_batch(loaded.ctx, batch)

    audit = await client.get(
        "/api/v1/audit", params={"action": "nexus_soc.incident"}, headers=_auth_header(token)
    )
    rows = audit.json()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "success"
    assert rows[0]["detail"]["target"] == "redis"
    assert rows[0]["detail"]["action_id"] is None


@pytest.mark.asyncio
async def test_multi_incident_batch_gets_one_audit_entry_each_sharing_the_outcome(
    client, db_session, test_settings, mock_ollama
):
    """Zwei Container stuerzen im selben Batch-Fenster ab, die KI trifft EINE
    Entscheidung fuer beide (wie die Buendelung im Vorgaengersystem) -- trotzdem bekommt
    JEDER der beiden Vorfaelle seinen EIGENEN, dauerhaften Audit-Eintrag mit seinem
    eigenen Host/Ziel, beide verweisen auf dieselbe entstandene `action_id`."""
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck_ext_nexus_soc.watcher import ContainerTransition

    ollama_url, state = mock_ollama
    state["next_reply"] = _CRASH_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    host_resp = await client.post(
        "/api/v1/hosts", json={"name": "docker", "address": "10.0.0.5"}, headers=_auth_header(token)
    )
    host_id = host_resp.json()["id"]

    loaded = get_extension_runtime().loaded["nexus-soc"]
    instance = loaded.instance
    host_stub = type("H", (), {"id": host_id, "name": "docker"})()
    await instance._on_transition(
        ContainerTransition(host=host_stub, target="nginx-proxy", message="Container CRASH", details={"is_crash": True}, is_crash=True)
    )
    await instance._on_transition(
        ContainerTransition(host=host_stub, target="redis", message="Container CRASH", details={"is_crash": True}, is_crash=True)
    )
    batch = instance._store.take_batch()
    assert len(batch) == 2
    await instance._process_batch(loaded.ctx, batch)

    audit = await client.get(
        "/api/v1/audit", params={"action": "nexus_soc.incident"}, headers=_auth_header(token)
    )
    rows = audit.json()
    assert len(rows) == 2
    targets = {r["detail"]["target"] for r in rows}
    assert targets == {"nginx-proxy", "redis"}
    action_ids = {r["detail"]["action_id"] for r in rows}
    assert len(action_ids) == 1  # dieselbe Aktion fuer beide
    assert None not in action_ids
    assert all(r["outcome"] == "proposed" for r in rows)


# ---------------------------------------------------------------------------
# Dauerhafte, durchsuchbare Vorfalls-Historie + deutsche Badges
# ---------------------------------------------------------------------------


async def _record_incident(client, token, state, *, host_name: str, target: str, message: str, reply: str) -> str:
    from nodvard_deck_ext_nexus_soc.watcher import ContainerTransition

    state["next_reply"] = reply
    host_resp = await client.post("/api/v1/hosts", json={"name": host_name, "address": "10.0.0.5"}, headers=_auth_header(token))
    host_id = host_resp.json().get("id")
    if host_id is None:  # Host existiert schon
        hosts = (await client.get("/api/v1/hosts", headers=_auth_header(token))).json()
        host_id = next(h["id"] for h in hosts if h["name"] == host_name)
    loaded = get_extension_runtime().loaded["nexus-soc"]
    transition = ContainerTransition(
        host=type("H", (), {"id": host_id, "name": host_name})(),
        target=target, message=message,
        details={"status": "Exited (1)", "state": "exited", "is_crash": True}, is_crash=True,
    )
    await loaded.instance._on_transition(transition)
    batch = loaded.instance._store.take_batch()
    await loaded.instance._process_batch(loaded.ctx, batch)
    return batch[0].id


@pytest.mark.asyncio
async def test_history_survives_a_restart_of_the_extension(client, db_session, test_settings, mock_ollama):
    """Der eigentliche Fund: die Historie lebte in einer deque im Prozess -- nach jedem
    Neustart/Deploy war die SOC-Seite leer. Jetzt: Extension neu laden (neue Instanz,
    leerer Speicher), der Vorfall ist weiter da."""
    ollama_url, state = mock_ollama
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    incident_id = await _record_incident(
        client, token, state, host_name="docker", target="nginx-proxy",
        message="Container CRASH (Exited (1))", reply=_CRASH_REPLY,
    )

    disabled = await client.post("/api/v1/extensions/nexus-soc/disable", headers=_auth_header(token))
    assert disabled.status_code == 200, disabled.text
    enabled = await client.post("/api/v1/extensions/nexus-soc/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text

    r = await client.get("/api/v1/ext/nexus-soc/history", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == incident_id
    assert body["items"][0]["status"] == "proposed"
    assert body["hosts"] == ["docker"]


@pytest.mark.asyncio
async def test_history_search_by_text_status_host_and_time(client, db_session, test_settings, mock_ollama):
    from datetime import UTC, datetime, timedelta

    ollama_url, state = mock_ollama
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    crash_id = await _record_incident(
        client, token, state, host_name="docker", target="nginx-proxy",
        message="Container CRASH (Exited (1))", reply=_CRASH_REPLY,
    )
    quiet_id = await _record_incident(
        client, token, state, host_name="pi-host", target="pihole",
        message="Container CRASH (Exited (137))", reply=_NONE_REPLY,
    )

    async def search(**params):  # noqa: ANN202
        r = await client.get("/api/v1/ext/nexus-soc/history", params=params, headers=_auth_header(token))
        assert r.status_code == 200, r.text
        return r.json()

    # Freitext trifft auch die KI-Zusammenfassung ("Speicherfehler" steht nur dort).
    assert [i["id"] for i in (await search(q="speicherfehler"))["items"]] == [crash_id]
    assert [i["id"] for i in (await search(q="PIHOLE"))["items"]] == [quiet_id]
    assert [i["id"] for i in (await search(status="reviewed"))["items"]] == [quiet_id]
    assert [i["id"] for i in (await search(host="docker"))["items"]] == [crash_id]
    everything = await search()
    assert everything["total"] == 2
    assert [i["id"] for i in everything["items"]] == [quiet_id, crash_id]  # neueste zuerst
    assert sorted(everything["hosts"]) == ["docker", "pi-host"]

    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    assert (await search(since=future))["total"] == 0
    assert (await search(since=past, until=future))["total"] == 2

    page = await search(limit=1, offset=1)
    assert page["total"] == 2
    assert [i["id"] for i in page["items"]] == [crash_id]

    bad = await client.get("/api/v1/ext/nexus-soc/history", params={"status": "kaputt"}, headers=_auth_header(token))
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_status_changes_persist_and_land_in_the_audit_log(client, db_session, test_settings, mock_ollama):
    from sqlalchemy import select

    from nodvard_deck.models import AuditEntry

    ollama_url, state = mock_ollama
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    incident_id = await _record_incident(
        client, token, state, host_name="docker", target="nginx-proxy",
        message="Container CRASH (Exited (1))", reply=_CRASH_REPLY,
    )

    for verb, expected in (("confirm", "reviewed"), ("resolve", "resolved"), ("reopen", "open"), ("dismiss", "dismissed")):
        r = await client.post(f"/api/v1/ext/nexus-soc/incidents/{incident_id}/{verb}", headers=_auth_header(token))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == expected

    row = (await client.get("/api/v1/ext/nexus-soc/history", headers=_auth_header(token))).json()["items"][0]
    assert row["status"] == "dismissed"
    assert row["status_label"] == "Verworfen"
    assert row["status_changed_at"] is not None

    entries = (
        await db_session.execute(
            select(AuditEntry).where(AuditEntry.action == "nexus_soc.incident_status").order_by(AuditEntry.ts, AuditEntry.id)
        )
    ).scalars().all()
    assert [(e.detail["from"], e.detail["to"]) for e in entries] == [
        ("proposed", "reviewed"), ("reviewed", "resolved"), ("resolved", "open"), ("open", "dismissed"),
    ]
    assert all(e.correlation_id == incident_id for e in entries)
    assert entries[0].reason == "Aktion vorgeschlagen -> Geprüft"
    # D-15: im Audit-Log steht der klickende Mensch, die Extension nur als "via".
    assert {(e.actor_type, e.detail["via"]) for e in entries} == {("user", "nexus-soc")}

    unknown = await client.post("/api/v1/ext/nexus-soc/incidents/gibt-es-nicht/confirm", headers=_auth_header(token))
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_incidents_widget_badge_is_german_and_colour_comes_from_an_explicit_tone(
    client, db_session, test_settings, mock_ollama
):
    """Vorher: Badge-Text "critical/warning/info" (englisch), Farbe per `| tone` aus
    genau diesem Wort. Jetzt: deutscher Text, Farbe aus einem eigenen Feld -- ein
    deutsches Wort kann die Farblogik nicht mehr aushebeln."""
    ollama_url, state = mock_ollama
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    await _record_incident(
        client, token, state, host_name="docker", target="nginx-proxy",
        message="Container CRASH (Exited (1))", reply=_CRASH_REPLY,
    )
    await _record_incident(
        client, token, state, host_name="pi-host", target="pihole",
        message="Container CRASH (Exited (137))", reply=_NONE_REPLY,
    )

    rows = (await client.get("/api/v1/ext/nexus-soc/widgets/incidents", headers=_auth_header(token))).json()["data"]
    by_target = {r["target"]: r for r in rows}
    assert (by_target["nginx-proxy"]["status_label"], by_target["nginx-proxy"]["tone"]) == ("Aktion vorgeschlagen", "warn")
    assert (by_target["pihole"]["status_label"], by_target["pihole"]["tone"]) == ("Geprüft", "good")

    widgets = (await client.get("/api/v1/widgets", headers=_auth_header(token))).json()
    spec = next(w for w in widgets if w["ext_id"] == "nexus-soc" and w["id"] == "incidents")
    badge = spec["view"]["item"]["badge"]
    assert badge == {"text": "{{ status_label }}", "tone": "{{ tone }}"}


@pytest.mark.asyncio
async def test_container_name_is_quoted_when_fetching_crash_logs(client, db_session, test_settings, mock_ollama, monkeypatch):
    """Der Containername kommt aus `docker ps` des ueberwachten Servers -- beim
    Log-Abruf bleibt er ein einziges Argument, egal was drinsteht."""
    from types import SimpleNamespace

    ollama_url, state = mock_ollama
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    commands: list[str] = []

    async def fake_run(host, command, timeout_s=60):
        commands.append(command)
        return SimpleNamespace(exit_code=0, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr(get_extension_runtime().loaded["nexus-soc"].ctx.exec, "run", fake_run)
    await _record_incident(
        client, token, state, host_name="docker", target="web;touch${IFS}x",
        message="Container CRASH (Exited (1))", reply=_NONE_REPLY,
    )
    assert commands == [
        "docker inspect --type container --format "
        "'{{.Name}}|{{.State.OOMKilled}}|{{.State.ExitCode}}|{{.State.Running}}|{{.State.FinishedAt}}|{{.State.Error}}' 'web;touch${IFS}x'",
        "docker logs --tail 25 'web;touch${IFS}x' 2>&1",
    ]


@pytest.mark.asyncio
async def test_host_page_does_not_offer_internal_soc_actions(client, db_session, test_settings, mock_ollama):
    """Die Nexus-SOC-Aktionen bekommen ihren Befehl immer von den eigenen
    /defender-Routen. Auf der Server-Seite erschienen sie als Formular mit freiem Feld
    "command" (z. B. "Server neu starten" mit Befehlsfeld, als root)."""
    ollama_url, _state = mock_ollama
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    host = (await client.post("/api/v1/hosts", json={"name": "docker", "address": "10.0.0.5"}, headers=_auth_header(token))).json()

    r = await client.get(f"/api/v1/hosts/{host['id']}/actions", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert not [a["action_type"] for a in r.json() if a["action_type"].startswith("nexus_soc.")]
    # Registriert bleiben sie trotzdem -- die /defender-Routen schlagen sie weiter ueber das Gate vor.
    registry = get_extension_runtime().actions
    assert all(registry.get(t) is not None for t in ("nexus_soc.ban", "nexus_soc.reboot", "nexus_soc.restore"))


# ---------------------------------------------------------------------------
# Vorfaelle ueberleben einen Neustart waehrend des Sammelfensters
# ---------------------------------------------------------------------------


async def _queue_rows(db_session) -> list[tuple]:
    from sqlalchemy import text

    result = await db_session.execute(
        text("SELECT target, status, processed_at FROM ext_nexus_soc_incident_queue ORDER BY created_at, target")
    )
    return [tuple(r) for r in result.all()]


async def _restart_extension(client, token):
    disabled = await client.post("/api/v1/extensions/nexus-soc/disable", headers=_auth_header(token))
    assert disabled.status_code == 200, disabled.text
    enabled = await client.post("/api/v1/extensions/nexus-soc/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text
    return get_extension_runtime().loaded["nexus-soc"]


async def _restart_extension_paused(client, token, monkeypatch):
    """Neustart ohne laufende Sammel-Schleife. Ein wieder aufgenommener, schon ueberfaelliger Vorfall
    wuerde sonst von der neuen Schleife sofort verarbeitet (`sleep(0)`), und ob der Test ihn noch
    mit `take_batch()` erwischt, hinge davon ab, wie viele Schritte die Anfrage nach `on_start()` noch
    macht -- ein Wettlauf, der mit dem Protokoll beim Ein-/Ausschalten sichtbar wurde."""
    from nodvard_deck_ext_nexus_soc import Extension

    async def idle(self, ctx):  # noqa: ANN001, ARG001
        await asyncio.Event().wait()

    monkeypatch.setattr(Extension, "_batch_flush_loop", idle)
    return await _restart_extension(client, token)


@pytest.mark.asyncio
async def test_incident_is_saved_as_open_the_moment_it_is_detected(client, db_session, test_settings, mock_ollama):
    """Vorher lag der Vorfall bis zum Ende des Sammelfensters nur im Speicher."""
    ollama_url, state = mock_ollama
    state["next_reply"] = _NONE_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    loaded = get_extension_runtime().loaded["nexus-soc"]

    await _queue_crash(client, token, loaded.instance, host_name="docker", target="nginx-proxy")

    rows = await _queue_rows(db_session)
    assert [(t, s, p) for t, s, p in rows] == [("nginx-proxy", "offen", None)]

    await loaded.instance._process_batch(loaded.ctx, loaded.instance._store.take_batch())
    rows = await _queue_rows(db_session)
    assert [(t, s) for t, s, _p in rows] == [("nginx-proxy", "verarbeitet")]
    assert rows[0][2] is not None


@pytest.mark.asyncio
async def test_restart_inside_the_batch_window_resumes_the_incident_and_reports_it_once(
    client, db_session, test_settings, mock_ollama
):
    from nodvard_sdk.capabilities import NotificationChannel

    ollama_url, state = mock_ollama
    state["next_reply"] = _NONE_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)
    old = get_extension_runtime().loaded["nexus-soc"]
    host_id = await _queue_crash(client, token, old.instance, host_name="docker", target="nginx-proxy")
    original_id = old.instance._store.take_batch()[0].id  # wie beim Prozessende: nie verarbeitet
    assert channel.received == []

    loaded = await _restart_extension(client, token)
    assert loaded.instance is not old.instance
    assert loaded.instance._store.pending_count() == 1
    assert loaded.instance._batch_ready.is_set()

    batch = loaded.instance._store.take_batch()
    assert batch[0].id == original_id and batch[0].target == "nginx-proxy" and batch[0].host_id == host_id
    await loaded.instance._process_batch(loaded.ctx, batch)

    audit = await client.get("/api/v1/audit", params={"action": "nexus_soc.incident"}, headers=_auth_header(token))
    assert [r["correlation_id"] for r in audit.json()] == [original_id]
    assert len(channel.received) == 1
    assert "Nach einem Neustart" in channel.received[0].body
    assert [(t, s) for t, s, _p in await _queue_rows(db_session)] == [("nginx-proxy", "verarbeitet")]

    # Ein weiterer Neustart nimmt nichts mehr auf -- keine Doppelmeldung.
    again = await _restart_extension(client, token)
    assert again.instance._store.pending_count() == 0
    assert not again.instance._batch_ready.is_set()
    audit = await client.get("/api/v1/audit", params={"action": "nexus_soc.incident"}, headers=_auth_header(token))
    assert len(audit.json()) == 1
    assert len(channel.received) == 1


@pytest.mark.asyncio
async def test_normal_report_has_no_restart_hint_and_resumed_incident_keeps_its_cooldown(
    client, db_session, test_settings, mock_ollama
):
    from nodvard_sdk.capabilities import NotificationChannel

    ollama_url, state = mock_ollama
    state["next_reply"] = _NONE_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)
    loaded = get_extension_runtime().loaded["nexus-soc"]
    host_id = await _queue_crash(client, token, loaded.instance, host_name="docker", target="nginx-proxy")
    await loaded.instance._process_batch(loaded.ctx, loaded.instance._store.take_batch())
    assert "Neustart" not in channel.received[0].body

    # Wieder aufgenommener Vorfall: derselbe Container wird direkt danach nicht doppelt gemeldet.
    await loaded.instance._on_transition(_crash_transition(host_id, "docker", "redis"))
    loaded = await _restart_extension(client, token)
    assert loaded.instance._store.pending_count() == 1
    await loaded.instance._on_transition(_crash_transition(host_id, "docker", "redis"))
    assert loaded.instance._store.pending_count() == 1
    assert len(await _queue_rows(db_session)) == 2


@pytest.mark.asyncio
async def test_docker_watcher_keeps_its_last_state_in_the_database_across_a_restart(
    client, db_session, test_settings, mock_ollama
):
    """Der Watcher bekommt die dauerhafte Ablage (Baselines, Art "docker") und liest
    nach einem Neustart den zuletzt gespeicherten Stand wieder."""
    from nodvard_deck_ext_nexus_soc.watcher import BaselineStateStore

    ollama_url, _state = mock_ollama
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    loaded = get_extension_runtime().loaded["nexus-soc"]
    assert isinstance(loaded.instance._watcher._state_store, BaselineStateStore)

    store = BaselineStateStore(loaded.ctx)
    assert await store.load("h-1") is None
    await store.save("h-1", {"containers": {"nginx": {"state": "running"}}})
    await store.save("h-1", {"containers": {"nginx": {"state": "exited"}}})  # zweites Mal: aktualisieren

    restarted = await _restart_extension(client, token)
    assert await BaselineStateStore(restarted.ctx).load("h-1") == {"containers": {"nginx": {"state": "exited"}}}


# ---------------------------------------------------------------------------
# Sammelfenster 60 s; ein Fehler in der Verarbeitung beendet die Schleife nicht
# ---------------------------------------------------------------------------


def test_batch_window_default_is_60_seconds():
    import json

    schema = json.loads((REPO_EXTENSIONS_DIR / "nexus-soc" / "settings.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["incident_batch_delay_s"]["default"] == 60


async def _queue_full(db_session) -> list[tuple]:
    from sqlalchemy import text

    result = await db_session.execute(
        text("SELECT target, status, attempts, action_id FROM ext_nexus_soc_incident_queue ORDER BY created_at, target")
    )
    return [tuple(r) for r in result.all()]


async def _set_batch_delay(db_session, seconds: float) -> None:
    record = await db_session.get(ExtensionRecord, "nexus-soc")
    record.settings = {**record.settings, "incident_batch_delay_s": seconds}
    await db_session.flush()


async def _wait_for(predicate, *, timeout_s: float = 5.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("Bedingung wurde nicht rechtzeitig erfuellt")


@pytest.mark.asyncio
async def test_failure_while_processing_a_batch_does_not_stop_the_flush_loop(
    client, db_session, test_settings, mock_ollama, monkeypatch
):
    ollama_url, state = mock_ollama
    state["next_reply"] = _NONE_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    record = await db_session.get(ExtensionRecord, "nexus-soc")
    record.settings = {**record.settings, "incident_batch_delay_s": 0.05}
    await db_session.flush()
    loaded = get_extension_runtime().loaded["nexus-soc"]
    instance = loaded.instance

    real_process = instance._process_batch
    attempts: list[str] = []

    async def flaky(ctx, batch):
        attempts.append(batch[0].target)
        if len(attempts) == 1:
            raise RuntimeError("Datenbank kurz weg")
        await real_process(ctx, batch)

    monkeypatch.setattr(instance, "_process_batch", flaky)

    host_id = await _queue_crash(client, token, instance, host_name="docker", target="nginx-proxy")
    await _wait_for(lambda: _async_value(len(attempts) >= 1))
    await asyncio.sleep(0.1)
    # Der fehlgeschlagene Vorfall bleibt offen (wird beim naechsten Start wieder aufgenommen).
    assert [(t, s) for t, s, _p in await _queue_rows(db_session)] == [("nginx-proxy", "offen")]

    # Die Schleife lebt noch: der naechste Vorfall wird normal verarbeitet.
    await instance._on_transition(_crash_transition(host_id, "docker", "redis"))

    async def redis_done() -> bool:
        return ("redis", "verarbeitet") in [(t, s) for t, s, _p in await _queue_rows(db_session)]

    await _wait_for(redis_done)
    assert attempts == ["nginx-proxy", "redis"]
    assert [(t, s) for t, s, _p in await _queue_rows(db_session)] == [("nginx-proxy", "offen"), ("redis", "verarbeitet")]

    # Nach einem Neustart wird der offene Vorfall genau einmal nachgeholt.
    restarted = await _restart_extension_paused(client, token, monkeypatch)
    assert [i.target for i in restarted.instance._store.take_batch()] == ["nginx-proxy"]


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_report_is_still_sent_when_the_ai_call_itself_raises(
    client, db_session, test_settings, mock_ollama, monkeypatch
):
    from nodvard_sdk.capabilities import NotificationChannel

    ollama_url, _state = mock_ollama
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)
    loaded = get_extension_runtime().loaded["nexus-soc"]

    async def broken(*args, **kwargs):
        raise RuntimeError("Zugangsdaten nicht lesbar")

    monkeypatch.setattr(loaded.instance._ai, "complete", broken)
    await _queue_crash(client, token, loaded.instance, host_name="docker", target="nginx-proxy")
    await loaded.instance._process_batch(loaded.ctx, loaded.instance._store.take_batch())

    assert len(channel.received) == 1
    assert "KI nicht erreichbar" in channel.received[0].body
    assert [(t, s) for t, s, _p in await _queue_rows(db_session)] == [("nginx-proxy", "verarbeitet")]


@pytest.mark.asyncio
async def test_without_any_ai_server_nothing_looks_broken(client, db_session, test_settings):
    """Ohne Ollama (die KI ist optional): die Statistik sagt „nicht eingerichtet“ (kein Fehler), und ein
    abgestürzter Container wird gemeldet, ohne bei jedem Vorfall „KI nicht erreichbar“ zu behaupten."""
    from nodvard_sdk.capabilities import NotificationChannel

    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    enabled = await client.post("/api/v1/extensions/nexus-soc/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text
    from nodvard_deck_ext_nexus_soc.ollama import NO_AI_TEXT  # erst nach dem Einschalten importierbar

    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)

    stats = (await client.get("/api/v1/ext/nexus-soc/stats", headers=_auth_header(token))).json()
    assert stats["ai_configured"] is False and stats["ai_healthy"] is False
    assert "ollama_url" not in stats["ai_message"] and "optional" in stats["ai_message"]

    loaded = get_extension_runtime().loaded["nexus-soc"]
    await _queue_crash(client, token, loaded.instance, host_name="docker", target="nginx-proxy")
    await loaded.instance._process_batch(loaded.ctx, loaded.instance._store.take_batch())
    assert len(channel.received) == 1
    body = channel.received[0].body
    assert "Nodvard KI ist nicht eingerichtet" in body and "KI nicht erreichbar" not in body
    assert NO_AI_TEXT.split(" – ")[0] in body


@pytest.mark.asyncio
async def test_ai_server_entered_but_down_is_still_reported_as_unreachable(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/extensions/nexus-soc/enable", headers=_auth_header(token))
    record = await db_session.get(ExtensionRecord, "nexus-soc")
    record.settings = {"ollama_url": "http://127.0.0.1:9"}  # nichts hört dort zu
    await db_session.flush()

    stats = (await client.get("/api/v1/ext/nexus-soc/stats", headers=_auth_header(token))).json()
    assert stats["ai_configured"] is True and stats["ai_healthy"] is False


# ---------------------------------------------------------------------------
# Fehlgeschlagene Verarbeitung: erneut einplanen, Versuchs-Grenze, kein 2. Vorschlag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_batch_is_retried_after_a_backoff_and_then_reported(
    client, db_session, test_settings, mock_ollama, monkeypatch
):
    import nodvard_deck_ext_nexus_soc as soc

    ollama_url, state = mock_ollama
    state["next_reply"] = _NONE_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    await _set_batch_delay(db_session, 0.05)
    monkeypatch.setattr(soc, "RETRY_BACKOFF_S", 0.1)
    instance = get_extension_runtime().loaded["nexus-soc"].instance
    real_process = instance._process_batch
    calls: list[int] = []

    async def flaky(ctx, batch):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Datenbank kurz weg")
        await real_process(ctx, batch)

    monkeypatch.setattr(instance, "_process_batch", flaky)
    await _queue_crash(client, token, instance, host_name="docker", target="nginx-proxy")

    async def done() -> bool:
        return [r[1] for r in await _queue_full(db_session)] == ["verarbeitet"]

    await _wait_for(done)
    assert len(calls) == 2
    assert [r[2] for r in await _queue_full(db_session)] == [1]
    # Der Retry-Task raeumt sich nach dem Ende selbst weg (keine wachsende Liste).
    await asyncio.sleep(0.05)
    assert instance._retry_tasks == set()
    assert not [t for t in get_extension_runtime().loaded["nexus-soc"].tasks if t.name == "nexus-soc-batch-retry"]


@pytest.mark.asyncio
async def test_batch_failing_three_times_is_marked_failed_and_reported_without_ai(
    client, db_session, test_settings, mock_ollama, monkeypatch
):
    import nodvard_deck_ext_nexus_soc as soc
    from nodvard_sdk.capabilities import NotificationChannel

    ollama_url, state = mock_ollama
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    await _set_batch_delay(db_session, 0.05)
    monkeypatch.setattr(soc, "RETRY_BACKOFF_S", 0.05)
    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)
    instance = get_extension_runtime().loaded["nexus-soc"].instance
    calls: list[int] = []

    async def always_broken(ctx, batch):
        calls.append(1)
        raise RuntimeError("kaputt")

    monkeypatch.setattr(instance, "_process_batch", always_broken)
    await _queue_crash(client, token, instance, host_name="docker", target="nginx-proxy")

    async def failed() -> bool:
        return [r[1] for r in await _queue_full(db_session)] == ["fehlgeschlagen"]

    await _wait_for(failed)
    assert len(calls) == 3
    assert state["calls"] == []  # die einfache Meldung braucht keine KI
    assert len(channel.received) == 1
    assert "gescheitert" in channel.received[0].body and "nginx-proxy" in channel.received[0].body
    await asyncio.sleep(0.3)
    assert len(calls) == 3  # keine weiteren Versuche


@pytest.mark.asyncio
async def test_retry_does_not_create_a_second_action_proposal(
    client, db_session, test_settings, mock_ollama, monkeypatch
):
    ollama_url, state = mock_ollama
    state["next_reply"] = _CRASH_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    loaded = get_extension_runtime().loaded["nexus-soc"]
    instance = loaded.instance
    proposals: list[int] = []
    real_propose = loaded.ctx.actions.propose

    async def counting_propose(request):
        proposals.append(1)
        return await real_propose(request)

    monkeypatch.setattr(loaded.ctx.actions, "propose", counting_propose)
    real_save = instance._history.save
    fail = {"once": True}

    async def save_fails_once(incident):
        if fail["once"]:
            fail["once"] = False
            raise RuntimeError("Datenbank kurz weg")
        await real_save(incident)

    monkeypatch.setattr(instance._history, "save", save_fails_once)
    await _queue_crash(client, token, instance, host_name="docker", target="nginx-proxy")
    batch = instance._store.take_batch()
    with pytest.raises(RuntimeError):
        await instance._process_batch(loaded.ctx, batch)
    rows = await _queue_full(db_session)
    assert rows[0][1] == "offen" and rows[0][3] is not None  # Vorschlag ist am Eintrag vermerkt

    await instance._handle_batch_failure(loaded.ctx, batch)
    # Nach einem Neustart aus der Datenbank geholt: trotzdem kein zweiter Vorschlag.
    restarted = await _restart_extension(client, token)
    monkeypatch.setattr(restarted.ctx.actions, "propose", counting_propose)
    retry_batch = restarted.instance._store.take_batch()
    assert [i.action_id for i in retry_batch] == [rows[0][3]]
    await restarted.instance._process_batch(restarted.ctx, retry_batch)
    assert len(proposals) == 1
    assert [(r[1]) for r in await _queue_full(db_session)] == ["verarbeitet"]
    audit = await client.get("/api/v1/audit", params={"action": "nexus_soc.incident"}, headers=_auth_header(token))
    assert [r["outcome"] for r in audit.json()] == ["proposed"]


@pytest.mark.asyncio
async def test_start_marks_stale_and_exhausted_open_entries_as_failed_instead_of_resuming(
    client, db_session, test_settings, mock_ollama, monkeypatch
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    ollama_url, _state = mock_ollama
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    now = datetime.now(UTC)
    rows = [
        ("alt", now - timedelta(hours=25), 0, "offen"),
        ("verbraucht", now - timedelta(minutes=5), 3, "offen"),
        ("frisch", now - timedelta(minutes=5), 1, "offen"),
        ("lange-fehlgeschlagen", now - timedelta(days=9), 3, "fehlgeschlagen"),
    ]
    for name, created, attempts, status in rows:
        await db_session.execute(
            text(
                "INSERT INTO ext_nexus_soc_incident_queue (id, host_id, host_name, target, message, details, status,"
                " attempts, proposal_started, created_at, processed_at) VALUES (:id, 'h', 'docker', :t, 'x', '{}', :s, :a, 0, :c, :p)"
            ),
            {"id": name, "t": name, "s": status, "a": attempts, "c": created.strftime("%Y-%m-%d %H:%M:%S.%f"),
             "p": created.strftime("%Y-%m-%d %H:%M:%S.%f") if status == "fehlgeschlagen" else None},
        )
    await db_session.flush()

    loaded = await _restart_extension_paused(client, token, monkeypatch)
    assert [i.target for i in loaded.instance._store.take_batch()] == ["frisch"]
    result = {r[0]: r[1] for r in await _queue_full(db_session)}
    assert result == {"alt": "fehlgeschlagen", "verbraucht": "fehlgeschlagen", "frisch": "offen"}  # 9 Tage alte Fehlschlaege sind aufgeraeumt


def test_flush_backoff_grows_from_5_seconds_to_5_minutes():
    from nodvard_deck_ext_nexus_soc import _flush_backoff_s

    assert [_flush_backoff_s(n) for n in range(1, 9)] == [5, 10, 20, 40, 80, 160, 300, 300]


@pytest.mark.asyncio
async def test_repeated_flush_errors_log_one_traceback_per_series_and_back_off(
    client, db_session, test_settings, mock_ollama, monkeypatch, caplog
):
    import logging

    import nodvard_deck_ext_nexus_soc as soc

    ollama_url, _state = mock_ollama
    await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    loaded = get_extension_runtime().loaded["nexus-soc"]
    waits: list[int] = []

    def fast_backoff(failures: int) -> float:
        waits.append(failures)
        return 0.01

    monkeypatch.setattr(soc, "_flush_backoff_s", fast_backoff)

    async def broken_get():
        raise RuntimeError("Einstellungen nicht lesbar")

    real_get = loaded.ctx.settings.get
    monkeypatch.setattr(loaded.ctx.settings, "get", broken_get)
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.ext.nexus-soc"):
        loaded.instance._batch_ready.set()
        await _wait_for(lambda: _async_value(len(waits) >= 4))
    monkeypatch.setattr(loaded.ctx.settings, "get", real_get)
    loaded.instance._batch_ready.clear()

    records = [r for r in caplog.records if r.name == "nodvard_deck.ext.nexus-soc" and "flush" in r.getMessage()]
    assert sum(1 for r in records if r.exc_info) == 1
    assert len(records) >= 4


# ---------------------------------------------------------------------------
# Beendigungs-Fakten (OOMKilled/ExitCode) vor dem KI-Aufruf
# ---------------------------------------------------------------------------


def test_system_prompt_explains_exit_137_without_oomkilled():
    from nodvard_deck_ext_nexus_soc.prompts import SYSTEM_PROMPT

    assert "137" in SYSTEM_PROMPT and "OOMKilled=false" in SYSTEM_PROMPT and "OOMKilled=true" in SYSTEM_PROMPT
    # Regeln aus dem Vorgaengersystem bleiben erhalten
    assert "Erfinde NIEMALS Container-Namen" in SYSTEM_PROMPT and "MANUELLER STOPP" in SYSTEM_PROMPT


def test_incident_prompt_lists_exit_facts_only_when_given():
    from nodvard_deck_ext_nexus_soc.prompts import build_incident_prompt

    plain = build_incident_prompt(summary_lines=["- x"], logs=[], batch_window_s=60)
    assert "OOMKilled" not in plain
    with_facts = build_incident_prompt(
        summary_lines=["- x"], logs=[], batch_window_s=60, exit_facts=["nginx @ docker: OOMKilled=false, ExitCode=137"]
    )
    assert "OOMKilled=false, ExitCode=137" in with_facts


def test_parse_exit_facts_reads_docker_inspect_lines_and_skips_noise():
    from nodvard_deck_ext_nexus_soc.watcher import parse_exit_facts

    out = "/nginx|false|137|false|2026-09-30T10:00:00.5Z|\n/db|true|137|false|2026-09-30T10:01:00Z|oom | wrapper\nError: No such object: x\n/up|false|0|true|0001-01-01T00:00:00Z|"
    facts = parse_exit_facts(out)
    assert facts["nginx"] == {
        "oom_killed": False, "exit_code": 137, "running": False, "finished_at": "2026-09-30T10:00:00.5Z", "error": "",
    }
    assert facts["db"]["oom_killed"] is True and facts["db"]["error"] == "oom | wrapper"
    assert facts["up"]["finished_at"] == "" and facts["up"]["running"] is True  # nie beendet, laeuft
    assert set(facts) == {"nginx", "db", "up"}


@pytest.mark.asyncio
async def test_exit_facts_reach_the_ai_prompt_with_one_inspect_per_host(
    client, db_session, test_settings, mock_ollama, monkeypatch
):
    from types import SimpleNamespace

    ollama_url, state = mock_ollama
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    loaded = get_extension_runtime().loaded["nexus-soc"]
    commands: list[str] = []

    async def fake_run(host, command, timeout_s=60):
        commands.append(command)
        if command.startswith("docker inspect"):
            return SimpleNamespace(
                exit_code=0, duration_ms=1, stderr="",
                stdout="/nginx-proxy|false|137|false|2026-09-30T10:00:00Z|\n/redis|false|1|false|2026-09-30T10:00:01Z|boom\n",
            )
        return SimpleNamespace(exit_code=0, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr(loaded.ctx.exec, "run", fake_run)
    host_id = await _queue_crash(client, token, loaded.instance, host_name="docker", target="nginx-proxy")
    await loaded.instance._on_transition(_crash_transition(host_id, "docker", "redis"))
    await loaded.instance._process_batch(loaded.ctx, loaded.instance._store.take_batch())

    assert len([c for c in commands if c.startswith("docker inspect")]) == 1
    prompt = state["calls"][-1]["prompt"]
    assert "nginx-proxy @ docker: OOMKilled=false, ExitCode=137" in prompt
    assert "redis @ docker: OOMKilled=false, ExitCode=1" in prompt and "Fehler=boom" in prompt


def test_incident_prompt_carries_the_fixed_cause_and_the_system_prompt_forbids_reinterpreting_it():
    from nodvard_deck_ext_nexus_soc.prompts import SYSTEM_PROMPT, build_incident_prompt

    assert "FESTSTEHENDE EINORDNUNG" in SYSTEM_PROMPT and "NICHT umdeuten" in SYSTEM_PROMPT
    plain = build_incident_prompt(summary_lines=["- x"], logs=[], batch_window_s=60)
    assert "FESTSTEHENDE EINORDNUNG" not in plain
    prompt = build_incident_prompt(
        summary_lines=["- x"], logs=[], batch_window_s=60,
        cause_lines=["FESTSTEHENDE EINORDNUNG (vom System ermittelt, nicht ändern): db auf docker: Speichermangel (OOMKilled). x"],
    )
    assert "nicht ändern): db auf docker: Speichermangel (OOMKilled)." in prompt
    assert "Uebernimm die FESTSTEHENDE EINORDNUNG" in prompt


_WRONG_AI_REPLY = (
    "Lagebericht: db wurde von aussen beendet, OOMKilled=true, da OOMKilled=false.\n"
    "NEXUS-Entscheidung:\n"
    "BEGRUENDUNG: Der Container wurde von aussen beendet\n"
    "AKTION: EXEC docker docker restart db"
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inspect_line, label, tag",
    [
        ("/db|false|1|false|2026-09-30T10:00:00Z|", "echter Absturz (Exit-Code 1)", "echter Absturz"),
        ("/db|true|137|false|2026-09-30T10:00:00Z|", "Speichermangel (OOMKilled)", "Speichermangel"),
        ("/db|false|137|false|2026-09-30T10:00:00Z|", "von außen beendet (Signal/Kill)", "von außen beendet"),
        (None, "Ursache unklar (keine Beendigungs-Fakten)", "Ursache unklar"),
    ],
)
async def test_fixed_cause_shows_in_prompt_message_and_action_even_if_the_ai_is_wrong(
    client, db_session, test_settings, mock_ollama, monkeypatch, inspect_line, label, tag
):
    from types import SimpleNamespace

    from nodvard_sdk.capabilities import NotificationChannel

    ollama_url, state = mock_ollama
    state["next_reply"] = _WRONG_AI_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)
    loaded = get_extension_runtime().loaded["nexus-soc"]

    async def fake_run(host, command, timeout_s=60):
        if command.startswith("docker inspect") and inspect_line is not None:
            return SimpleNamespace(exit_code=0, stdout=inspect_line + "\n", stderr="", duration_ms=1)
        return SimpleNamespace(exit_code=1, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr(loaded.ctx.exec, "run", fake_run)
    reasons: list[str] = []
    real_propose = loaded.ctx.actions.propose

    async def recording(request):
        reasons.append(request.reason)
        return await real_propose(request)

    monkeypatch.setattr(loaded.ctx.actions, "propose", recording)
    await _queue_crash(client, token, loaded.instance, host_name="docker", target="db")
    await loaded.instance._process_batch(loaded.ctx, loaded.instance._store.take_batch())

    # 1. Prompt: der feste Satz
    prompt = state["calls"][-1]["prompt"]
    assert f"FESTSTEHENDE EINORDNUNG (vom System ermittelt, nicht ändern): db auf docker: {label}." in prompt
    # 2. Meldung: eigene Zeile VOR dem KI-Text, der KI-Text bleibt unveraendert dahinter
    body = channel.received[0].body
    assert body.startswith(f"Ursache laut System: {label}\n\n")
    assert "von aussen beendet, OOMKilled=true" in body  # die falsche KI-Aussage steht nicht als System-Aussage
    # 3. Aktion: Tag vorn in der Begruendung
    assert reasons == [f"[{tag}] Der Container wurde von aussen beendet"]
    # Dashboard-Vorfall (ai_summary) zeigt dieselbe Zeile
    audit = await client.get("/api/v1/audit", params={"action": "nexus_soc.incident"}, headers=_auth_header(token))
    assert audit.json()[0]["detail"]["ai_summary"].startswith(f"Ursache laut System: {label}")


@pytest.mark.asyncio
async def test_failing_exit_fact_lookup_is_ignored(client, db_session, test_settings, mock_ollama, monkeypatch):
    ollama_url, state = mock_ollama
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    loaded = get_extension_runtime().loaded["nexus-soc"]

    async def broken_run(host, command, timeout_s=60):
        raise RuntimeError("SSH weg")

    monkeypatch.setattr(loaded.ctx.exec, "run", broken_run)
    await _queue_crash(client, token, loaded.instance, host_name="docker", target="nginx-proxy")
    await loaded.instance._process_batch(loaded.ctx, loaded.instance._store.take_batch())
    assert "OOMKilled" not in state["calls"][-1]["prompt"]
    assert [r[1] for r in await _queue_full(db_session)] == ["verarbeitet"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command, incident_host, expected",
    [
        ("docker restart nginx-proxy", "docker", "medium"),
        ("docker restart nginx-proxy", "anderer", "high"),  # der Vorfall war auf einem anderen Host
        ("docker restart redis", "docker", "high"),  # Container nicht aus dem Batch
        ("docker restart -t 0 nginx-proxy", "docker", "high"),
        ("docker rm nginx-proxy", "docker", "high"),
    ],
)
async def test_ai_proposal_risk_is_medium_only_for_a_plain_restart_of_a_batch_container(
    client, db_session, test_settings, mock_ollama, monkeypatch, command, incident_host, expected
):
    ollama_url, state = mock_ollama
    state["next_reply"] = f"Lagebericht: abgestuerzt.\nNEXUS-Entscheidung:\nBEGRUENDUNG: Absturz\nAKTION: EXEC docker {command}"
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    loaded = get_extension_runtime().loaded["nexus-soc"]
    if incident_host != "docker":  # `_queue_crash` legt den Host des Vorfalls selbst an
        await client.post("/api/v1/hosts", json={"name": "docker", "address": "10.0.0.5"}, headers=_auth_header(token))

    async def fake_run(host, command, timeout_s=60):
        from types import SimpleNamespace

        out = "/nginx-proxy|false|1|false|2026-09-30T10:00:00Z|\n" if command.startswith("docker inspect") else ""
        return SimpleNamespace(exit_code=0, stdout=out, stderr="", duration_ms=1)

    monkeypatch.setattr(loaded.ctx.exec, "run", fake_run)  # echter Absturz (Exit 1), Container steht
    risks: list[str] = []
    real_propose = loaded.ctx.actions.propose

    async def recording(request):
        risks.append(request.risk.value)
        return await real_propose(request)

    monkeypatch.setattr(loaded.ctx.actions, "propose", recording)
    await _queue_crash(client, token, loaded.instance, host_name=incident_host, target="nginx-proxy")
    await loaded.instance._process_batch(loaded.ctx, loaded.instance._store.take_batch())
    assert risks == [expected]


@pytest.mark.asyncio
async def test_chat_proposals_stay_high_risk(client, db_session, test_settings, mock_ollama, monkeypatch):
    ollama_url, state = mock_ollama
    state["next_reply"] = _CRASH_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    loaded = get_extension_runtime().loaded["nexus-soc"]
    await client.post("/api/v1/hosts", json={"name": "docker", "address": "10.0.0.5"}, headers=_auth_header(token))
    risks: list[str] = []
    real_propose = loaded.ctx.actions.propose

    async def recording(request):
        risks.append(request.risk.value)
        return await real_propose(request)

    monkeypatch.setattr(loaded.ctx.actions, "propose", recording)
    r = await client.post("/api/v1/ext/nexus-soc/chat", json={"message": "starte nginx-proxy neu"}, headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert risks == ["high"]


async def _risk_of_batch(client, db_session, test_settings, mock_ollama, monkeypatch, *, incidents, inspect_out=None,
                         reply_target="nginx-proxy", prepare=None):
    """Legt die Vorfaelle an (Liste aus (Container, Status-Text, is_crash)), laesst die KI
    `docker restart <reply_target>` auf `docker` vorschlagen und gibt die Risikostufen zurueck."""
    from types import SimpleNamespace

    from nodvard_deck_ext_nexus_soc.watcher import ContainerTransition

    ollama_url, state = mock_ollama
    state["next_reply"] = (
        f"Lagebericht: x.\nNEXUS-Entscheidung:\nBEGRUENDUNG: y\nAKTION: EXEC docker docker restart {reply_target}"
    )
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    loaded = get_extension_runtime().loaded["nexus-soc"]
    host_id = (await client.post("/api/v1/hosts", json={"name": "docker", "address": "10.0.0.5"}, headers=_auth_header(token))).json()["id"]

    async def fake_run(host, command, timeout_s=60):
        if command.startswith("docker inspect") and inspect_out is not None:
            return SimpleNamespace(exit_code=0, stdout=inspect_out, stderr="", duration_ms=1)
        return SimpleNamespace(exit_code=1, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr(loaded.ctx.exec, "run", fake_run)
    risks: list[str] = []
    real_propose = loaded.ctx.actions.propose

    async def recording(request):
        risks.append(request.risk.value)
        return await real_propose(request)

    monkeypatch.setattr(loaded.ctx.actions, "propose", recording)
    host_stub = type("H", (), {"id": host_id, "name": "docker"})()
    for name, status_text, is_crash in incidents:
        await loaded.instance._on_transition(ContainerTransition(
            host=host_stub, target=name, message="Container", is_crash=is_crash,
            details={"status": status_text, "state": "exited", "is_crash": is_crash},
        ))
    batch = loaded.instance._store.take_batch()
    if prepare is not None:
        prepare(batch)
    await loaded.instance._process_batch(loaded.ctx, batch)
    return risks


def _F(name: str, exit_code: int, *, oom: bool = False, running: bool = False) -> str:
    """Eine Zeile `docker inspect` (Name|OOMKilled|ExitCode|Running|FinishedAt|Error)."""
    return f"/{name}|{str(oom).lower()}|{exit_code}|{str(running).lower()}|2026-09-30T10:00:00Z|\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "incidents, inspect_out, expected",
    [
        ([("nginx-proxy", "Exited (1) 2s ago", True)], _F("nginx-proxy", 1), "medium"),  # echter Absturz
        ([("nginx-proxy", "Exited (2) 2s ago", True)], _F("nginx-proxy", 2), "medium"),
        ([("nginx-proxy", "Exited (1) 2s ago", True)], None, "high"),  # ohne Fakten im Zweifel HOCH
        ([("nginx-proxy", "Exited (0) 2s ago", False)], _F("nginx-proxy", 0), "high"),  # manueller Stopp
        ([("nginx-proxy", "Exited (143) 2s ago", False)], None, "high"),
        ([("nginx-proxy", "Up 3 minutes (Paused)", False)], _F("nginx-proxy", 0, running=True), "high"),  # pausiert
        ([("nginx-proxy", "Exited (137) 2s ago", True)], _F("nginx-proxy", 137), "high"),  # docker kill
        ([("nginx-proxy", "Exited (130) 2s ago", True)], _F("nginx-proxy", 130), "high"),  # kill -s INT
        ([("nginx-proxy", "Exited (139) 2s ago", True)], _F("nginx-proxy", 139), "high"),  # Signal ohne OOM
        ([("nginx-proxy", "Exited (137) 2s ago", True)], None, "high"),  # Fakten fehlen
        ([("nginx-proxy", "Exited (137) 2s ago", True)], _F("nginx-proxy", 137, oom=True), "medium"),  # OOM
        ([("nginx-proxy", "Exited (1) 2s ago", True)], _F("nginx-proxy", 1, running=True), "high"),  # laeuft wieder
        # Ein abgestuerzter Container im Batch macht den absichtlich gestoppten nicht berechtigt.
        (
            [("nginx-proxy", "Exited (0) 2s ago", False), ("redis", "Exited (1) 2s ago", True)],
            _F("nginx-proxy", 0) + _F("redis", 1),
            "high",
        ),
    ],
)
async def test_medium_risk_needs_a_real_crash_not_a_manual_stop_or_kill(
    client, db_session, test_settings, mock_ollama, monkeypatch, incidents, inspect_out, expected
):
    risks = await _risk_of_batch(
        client, db_session, test_settings, mock_ollama, monkeypatch, incidents=incidents, inspect_out=inspect_out
    )
    assert risks == [expected]


@pytest.mark.asyncio
async def test_retried_incident_never_gets_medium_risk(client, db_session, test_settings, mock_ollama, monkeypatch):
    def as_retry(batch):
        for incident in batch:
            incident.attempts = 1

    risks = await _risk_of_batch(
        client, db_session, test_settings, mock_ollama, monkeypatch,
        incidents=[("nginx-proxy", "Exited (1) 2s ago", True)], inspect_out=_F("nginx-proxy", 1), prepare=as_retry,
    )
    assert risks == ["high"]


@pytest.mark.asyncio
async def test_incident_resumed_after_a_restart_never_gets_medium_risk(
    client, db_session, test_settings, mock_ollama, monkeypatch
):
    def as_resumed(batch):
        for incident in batch:
            incident.resumed = True

    risks = await _risk_of_batch(
        client, db_session, test_settings, mock_ollama, monkeypatch,
        incidents=[("nginx-proxy", "Exited (1) 2s ago", True)], inspect_out=_F("nginx-proxy", 1), prepare=as_resumed,
    )
    assert risks == ["high"]


@pytest.mark.asyncio
async def test_crash_between_propose_and_saving_the_outcome_never_proposes_twice(
    client, db_session, test_settings, mock_ollama, monkeypatch
):
    from nodvard_sdk.capabilities import NotificationChannel

    ollama_url, state = mock_ollama
    state["next_reply"] = _CRASH_REPLY
    token = await _setup_nexus_soc(client, db_session, test_settings, ollama_url)
    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)
    loaded = get_extension_runtime().loaded["nexus-soc"]
    proposals: list[int] = []

    async def dies_while_proposing(request):
        proposals.append(1)
        raise RuntimeError("Prozess stirbt mitten im Vorschlag")

    monkeypatch.setattr(loaded.ctx.actions, "propose", dies_while_proposing)
    await _queue_crash(client, token, loaded.instance, host_name="docker", target="nginx-proxy")
    batch = loaded.instance._store.take_batch()
    with pytest.raises(RuntimeError):
        await loaded.instance._process_batch(loaded.ctx, batch)
    assert len(proposals) == 1

    restarted = await _restart_extension(client, token)

    async def must_not_be_called(request):
        proposals.append(1)
        raise AssertionError("zweiter Vorschlag")

    monkeypatch.setattr(restarted.ctx.actions, "propose", must_not_be_called)
    await restarted.instance._process_batch(restarted.ctx, restarted.instance._store.take_batch())
    assert len(proposals) == 1
    assert len(channel.received) == 1 and "Vorschlag unklar" in channel.received[0].body
    assert "Aktionen" in channel.received[0].body
    assert [r[1] for r in await _queue_full(db_session)] == ["verarbeitet"]
    audit = await client.get("/api/v1/audit", params={"action": "nexus_soc.incident"}, headers=_auth_header(token))
    assert len(audit.json()) == 1
