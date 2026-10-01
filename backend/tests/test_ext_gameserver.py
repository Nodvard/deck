"""gameserver-Extension -- Start/Stop/Status + Join-Code-Widget.
Zwei reale Ketten getestet: Start/Stop ueber
gameservers EIGENEN `_GameServerActionExecutor` gegen einen echten lokalen
SSH-Server (eigener Ausfuehrungsweg fuer gameserver: steuert den Windows-Dienst, nicht mehr proxmox' VM-Ebene), und
Join-Code-Extraktion ueber denselben SSH-Weg (wie test_ext_terminal.py). Der
Proxmox-Mock unten wird nur noch fuer die Host-Discovery-/Listing-Tests gebraucht,
nicht mehr fuer Start/Stop selbst.
"""

from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

import pytest
import pytest_asyncio
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException

from nodvard_deck.models import ExtensionRecord, Host
from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services import hosts as hosts_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
_TOKEN_ID = "root@pam!lattice"
_TOKEN_SECRET = "test-secret-uuid"
_EXPECTED_AUTH = f"PVEAPIToken={_TOKEN_ID}={_TOKEN_SECRET}"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _build_mock_proxmox_app() -> tuple[FastAPI, dict]:
    state = {"vms": {"110": {"vmid": 110, "name": "game-win", "status": "stopped"}}, "calls": []}
    app = FastAPI()

    def _check_auth(authorization: str | None = Header(default=None)) -> None:
        if authorization != _EXPECTED_AUTH:
            raise HTTPException(status_code=401, detail="Ungueltiges PVEAPIToken.")

    @app.post("/api2/json/nodes/{node}/qemu/{vmid}/status/{action}")
    async def qemu_action(node: str, vmid: str, action: str, _: None = Depends(_check_auth)) -> dict:
        state["calls"].append((action, node, vmid))
        state["vms"][vmid]["status"] = "running" if action == "start" else "stopped"
        return {"data": f"UPID:mock:{action}:1:{vmid}::"}

    @app.get("/api2/json/nodes/{node}/tasks/{upid}/status")
    async def task_status(node: str, upid: str, _: None = Depends(_check_auth)) -> dict:
        # Task-Abschluss-Tracking: ohne diesen Endpunkt polled ProxmoxActionExecutor gegen ein
        # 404, das als echter Fehlschlag durchschlaegt -- dieser Test prueft aber
        # explizit gameservers Wiederverwendung von proxmoxs Executor, nicht das
        # Task-Polling selbst (das ist test_ext_proxmox.py's Aufgabe).
        return {"data": {"status": "stopped", "exitstatus": "OK"}}

    return app, state


@pytest_asyncio.fixture
async def mock_proxmox():
    app, state = _build_mock_proxmox_app()
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


async def _enable_gameserver_and_proxmox(client, db_session, test_settings, proxmox_base_url: str) -> str:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)

    token = await _bootstrap_owner(client)
    for ext_id in ("proxmox", "gameserver"):
        enabled = await client.post(f"/api/v1/extensions/{ext_id}/enable", headers=_auth_header(token))
        assert enabled.status_code == 200, enabled.text

    # Mehrere Verbindungen: proxmox erwartet jetzt `settings.connections` (eine
    # Liste benannter Verbindungen) statt eines einzelnen base_url/token_id-Blobs,
    # und das Token-Secret haengt am Verbindungsnamen -- siehe
    # nodvard_deck_ext_proxmox.config.build_connectors().
    record = await db_session.get(ExtensionRecord, "proxmox")
    record.settings = {"connections": [{"name": "primary", "base_url": proxmox_base_url, "token_id": _TOKEN_ID}]}
    await db_session.flush()
    token_resp = await client.post(
        "/api/v1/ext/proxmox/connections/primary/token", json={"value": _TOKEN_SECRET}, headers=_auth_header(token)
    )
    assert token_resp.status_code == 204, token_resp.text
    return token


async def _tagged_proxmox_vm_host(db_session, *, name: str, vmid: str, node: str = "pve2") -> Host:
    """Simuliert, was echte proxmox-Discovery schreiben wuerde, OHNE den
    Discovery-Job selbst mit aufzuziehen -- derselbe Ansatz wie
    test_ext_backups.py::test_vm_name_is_enriched_from_a_discovered_proxmox_host.
    `provider_ref` traegt den Verbindungsnamen
    ("primary", siehe `_enable_gameserver_and_proxmox()`) mit."""
    host = await hosts_service.create_host(db_session, name=name, address="10.0.0.1", tags=["gameserver"])
    host.provider_ext_id = "proxmox"
    host.provider_ref = f"primary/qemu/{node}/{vmid}"
    await db_session.flush()
    return host


@pytest.mark.asyncio
async def test_servers_endpoint_lists_only_tagged_hosts(client, db_session, test_settings, mock_proxmox):
    base_url, _state = mock_proxmox
    host = await _tagged_proxmox_vm_host(db_session, name="game-win", vmid="110")
    other = await hosts_service.create_host(db_session, name="untagged", address="10.0.0.2")
    await db_session.flush()

    token = await _enable_gameserver_and_proxmox(client, db_session, test_settings, base_url)
    res = await client.get("/api/v1/ext/gameserver/servers", headers=_auth_header(token))
    assert res.status_code == 200, res.text
    ids = {s["host_id"] for s in res.json()}
    assert host.id in ids
    assert other.id not in ids
    # Deutsches Wort statt "up"/"down"/"unknown".
    labels = {"up": "läuft", "down": "gestoppt", "unknown": "unbekannt", "maintenance": "Wartung"}
    for row in res.json():
        assert row["status_label"] == labels[row["status"]]
        # Nur passende Knoepfe: laufend -> kein Starten, gestoppt -> kein Stoppen.
        assert row["can_start"] == (row["status"] != "up")
        assert row["can_stop"] == (row["status"] != "down")
    widgets = (await client.get("/api/v1/widgets", headers=_auth_header(token))).json()
    spec = next(w for w in widgets if w["ext_id"] == "gameserver")
    # Spiel-Profile: Badge zeigt den Zustand des Spiel-DIENSTES, Farbe im Backend.
    assert spec["view"]["item"]["badge"] == {"text": "{{ service_label }}", "tone": "{{ tone }}"}
    shown = {a["id"]: a["show_if"] for a in spec["view"]["item"]["actions"]}
    assert shown == {"start": "{{ can_start }}", "stop": "{{ can_stop }}"}


@pytest.mark.asyncio
async def test_start_and_stop_use_gameservers_own_executor_over_real_ssh(
    client, db_session, test_settings, local_ssh_server, fake_powershell
):
    """Eigener Ausfuehrungsweg fuer gameserver: frueher delegierte gameserver komplett an proxmox' Executor --
    dieser Test beweist jetzt das Gegenteil, OHNE proxmox ueberhaupt zu aktivieren.
    `_GameServerActionExecutor` steuert den konfigurierten Windows-DIENST per SSH,
    nicht die VM -- reale SSH-Kette wie `test_join_code_extracted_over_a_real_ssh_
    connection`, `local_ssh_server`s Mock-Handler echot jeden Nicht-Shell-Befehl als
    `ran:<befehl>` mit Exit-Code 0 zurueck, beweist damit, dass der ECHTE
    konfigurierte Befehl (Start-Service/Stop-Service mit dem Default-Dienstnamen)
    tatsaechlich per SSH ankommt."""
    host_addr, port, username, password, _sftp_root = local_ssh_server
    host = await hosts_service.create_host(db_session, name="game-win", address=host_addr, tags=["gameserver"])
    host.status = "up"
    await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username=username, port=port, secret_value=password,
    )
    await db_session.commit()

    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    enabled = await client.post("/api/v1/extensions/gameserver/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text
    headers = _auth_header(token)

    start = await client.post(f"/api/v1/ext/gameserver/servers/{host.id}/start", headers=headers)
    assert start.status_code == 200, start.text
    assert start.json()["status"] == "proposed"  # autonomy.mode=propose ist der Default
    assert start.json()["risk"] == "low"

    approve = await client.post(f"/api/v1/actions/{start.json()['action_id']}/approve", headers=headers)
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "succeeded"
    assert approve.json()["result"]["output"] == "Running"
    # Spiel-Profile: kein fester Dienstname mehr ("valheim" traf den echten
    # Dienst "ValheimServer" nie) -- das Skript erkennt den Dienst selbst.
    script = fake_powershell["scripts"][-1]
    assert "Start-Service -Name $svc.Name" in script
    assert "$_.Name -match 'valheim' -or $_.PathName -match 'valheim'" in script

    stop = await client.post(f"/api/v1/ext/gameserver/servers/{host.id}/stop", headers=headers)
    assert stop.json()["risk"] == "high"
    approve2 = await client.post(f"/api/v1/actions/{stop.json()['action_id']}/approve", headers=headers)
    assert approve2.json()["status"] == "succeeded"
    assert approve2.json()["result"]["output"] == "Stopped"
    assert "Stop-Service -Name $svc.Name -Force" in fake_powershell["scripts"][-1]


@pytest.mark.asyncio
async def test_start_uses_configured_service_name_and_custom_command(client, db_session, test_settings, local_ssh_server):
    """`service_name`/`start_command` sind ueberschreibbar (settings.schema.json) --
    beweist, dass ein Admin einen anderen Dienstnamen/Mechanismus konfigurieren
    kann, ohne Code zu aendern."""
    host_addr, port, username, password, _sftp_root = local_ssh_server
    host = await hosts_service.create_host(db_session, name="ark-server", address=host_addr, tags=["gameserver"])
    host.status = "up"
    await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username=username, port=port, secret_value=password,
    )
    await db_session.commit()

    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/extensions/gameserver/enable", headers=_auth_header(token))
    headers = _auth_header(token)

    record = await db_session.get(ExtensionRecord, "gameserver")
    record.settings = {"service_name": "ark-survival", "start_command": "echo custom-start {service_name}"}
    await db_session.flush()

    start = await client.post(f"/api/v1/ext/gameserver/servers/{host.id}/start", headers=headers)
    approve = await client.post(f"/api/v1/actions/{start.json()['action_id']}/approve", headers=headers)
    assert approve.status_code == 200, approve.text
    assert "custom-start ark-survival" in approve.json()["result"]["output"]


@pytest.mark.asyncio
async def test_start_unknown_host_returns_404(client, db_session, test_settings, mock_proxmox):
    base_url, _state = mock_proxmox
    token = await _enable_gameserver_and_proxmox(client, db_session, test_settings, base_url)
    res = await client.post("/api/v1/ext/gameserver/servers/does-not-exist/start", headers=_auth_header(token))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_join_code_extracted_over_a_real_ssh_connection(
    client, db_session, test_settings, local_ssh_server
):
    """Reale SSH-Kette (wie test_ext_terminal.py): `local_ssh_server`s Mock-Prozess-
    Handler echot bei einem Nicht-Shell-Befehl immer `ran:<befehl>` zurueck (siehe
    conftest.py) -- `log_command` wird deshalb bewusst auf einen Text gesetzt, der
    selbst die gesuchte Log-Zeile enthaelt. Das Regex/Extraktion laeuft trotzdem
    ueber die ECHTE `ctx.exec.run()` -> SSH-Verbindung -> Rueckgabe-Kette, nicht nur
    gegen einen vorgetaeuschten `ctx`."""
    host_addr, port, username, password, _sftp_root = local_ssh_server
    host = await hosts_service.create_host(db_session, name="game-win", address=host_addr, tags=["gameserver"])
    host.status = "up"
    await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username=username, port=port, secret_value=password,
    )
    await db_session.commit()

    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    enabled = await client.post("/api/v1/extensions/gameserver/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text

    record = await db_session.get(ExtensionRecord, "gameserver")
    record.settings = {"log_command": "registered with join code AB12CD34"}
    await db_session.flush()

    res = await client.get("/api/v1/ext/gameserver/servers", headers=_auth_header(token))
    assert res.status_code == 200, res.text
    server = next(s for s in res.json() if s["host_id"] == host.id)
    assert server["join_code"] == "AB12CD34"
    assert server["join_code_display"] == "Join-Code: AB12CD34"


@pytest.mark.asyncio
async def test_no_join_code_when_host_is_down(client, db_session, test_settings):
    """Ein Host ohne `status=up` wird nicht per SSH abgefragt -- vermeidet einen
    garantiert scheiternden Verbindungsversuch gegen einen bekanntermassen
    abgeschalteten Gameserver bei jedem Widget-Refresh."""
    host = await hosts_service.create_host(db_session, name="game-win", address="10.0.0.1", tags=["gameserver"])
    host.status = "down"
    await db_session.commit()

    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/extensions/gameserver/enable", headers=_auth_header(token))

    res = await client.get("/api/v1/ext/gameserver/servers", headers=_auth_header(token))
    server = next(s for s in res.json() if s["host_id"] == host.id)
    assert server["join_code"] is None
    assert server["join_code_display"] == "Kein Join-Code gefunden"


@pytest.mark.asyncio
async def test_gameserver_page_is_registered_after_enabling(client, db_session, test_settings):
    """gameserver hatte bisher NUR ein Dashboard-
    Widget, keine eigene Seite -- `GET /pages` muss nach dem Aktivieren die neue
    GameServerPage zeigen, wie es fuer service-matrix bereits bewiesen ist
    (test_ext_service_matrix.py)."""
    token = await _bootstrap_owner(client)
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)

    enabled = await client.post("/api/v1/extensions/gameserver/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text

    pages = await client.get("/api/v1/pages", headers=_auth_header(token))
    assert pages.status_code == 200
    server_page = next(p for p in pages.json() if p["ext_id"] == "gameserver")
    assert server_page["path"] == "/gameservers"
    assert server_page["component"] == "GameServerPage"

    bundle = await client.get("/api/v1/extensions/gameserver/frontend/index.js")
    assert bundle.status_code == 200
    assert "GameServerPage" in bundle.text
