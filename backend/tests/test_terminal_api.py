"""POST /terminal/sessions + GET /ws/terminal/{id} -- Ende-zu-Ende gegen einen echten
lokalen SSH-Server UND eine echte, laufende FastAPI-App (`running_app`-Fixture,
siehe conftest.py) -- httpx' `ASGITransport` kann keine echten WebSockets bedienen,
ein In-Process-`uvicorn.Server` im selben Event-Loop schon (docs/04-API.md §4).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import AsyncClient

from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services import hosts as hosts_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


async def _bootstrap_owner(base_url: str) -> str:
    async with AsyncClient(base_url=base_url) as ac:
        await ac.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
        login = await ac.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
        assert login.status_code == 200, login.text
        return login.json()["access_token"]


@pytest.mark.asyncio
async def test_terminal_session_end_to_end(running_app, db_session, test_settings, local_ssh_server):
    import websockets

    http_base, ws_base = running_app
    host_addr, port, username, password, _ = local_ssh_server

    # Terminal-Extension echt aktivieren -- derselbe Mechanismus wie bei hello-world.
    settings = test_settings.model_copy(update={"extensions_dir": REPO_EXTENSIONS_DIR})
    host = await hosts_service.create_host(db_session, name="test-host", address=host_addr)
    await hosts_service.add_credential(
        db_session, settings, host_id=host.id, kind="ssh_password",
        username=username, port=port, secret_value=password,
    )
    await db_session.commit()

    from nodvard_deck.main import app

    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "terminal")

    token = await _bootstrap_owner(http_base)

    async with AsyncClient(base_url=http_base) as ac:
        created = await ac.post(
            "/api/v1/terminal/sessions",
            json={"host_id": host.id, "cols": 80, "rows": 24},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]

    ws_url = f"{ws_base}/api/v1/ws/terminal/{session_id}"
    async with websockets.connect(ws_url) as ws:
        first = await ws.recv()
        assert first == b"shell-ready\n"

        await ws.send(b"hallo\n")
        second = await ws.recv()
        assert second == b"echo:hallo\n"

        await ws.send(b"exit\n")
        # Nach "exit" beendet der Test-Server den Prozess -- der Kern meldet das als
        # {"type":"exit",...}-Textnachricht (docs/04 §4), bevor der Socket schliesst.
        exit_message = await ws.recv()
        import json

        payload = json.loads(exit_message)
        assert payload["type"] == "exit"


async def _connect_and_get_close_code(ws_url: str) -> int:
    """`accept()` laeuft immer zuerst (siehe api/v1/terminal.py) -- der Handshake
    gelingt also selbst bei einer Ablehnung; der Server schickt danach sofort einen
    Close-Frame mit dem passenden Code. `recv()` ist der zuverlaessige Weg, ihn zu
    beobachten (blosses Betreten/Verlassen von `async with` wirft nicht garantiert)."""
    import websockets
    from websockets.exceptions import ConnectionClosed

    async with websockets.connect(ws_url) as ws:
        try:
            await ws.recv()
        except ConnectionClosed as exc:
            return exc.rcvd.code if exc.rcvd else -1
    return -1


@pytest.mark.asyncio
async def test_unknown_ticket_is_rejected(running_app):
    _, ws_base = running_app
    code = await _connect_and_get_close_code(f"{ws_base}/api/v1/ws/terminal/does-not-exist")
    assert code == 4404


@pytest.mark.asyncio
async def test_ticket_is_single_use(running_app, db_session, test_settings):
    http_base, ws_base = running_app
    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    await db_session.commit()

    token = await _bootstrap_owner(http_base)
    async with AsyncClient(base_url=http_base) as ac:
        created = await ac.post(
            "/api/v1/terminal/sessions",
            json={"host_id": host.id, "cols": 80, "rows": 24},
            headers={"Authorization": f"Bearer {token}"},
        )
    session_id = created.json()["session_id"]

    # Erster Verbindungsversuch verbraucht das Ticket (schlaegt fehl, weil keine
    # Extension eine TerminalTarget-Capability anbietet -- das ist hier irrelevant,
    # wichtig ist NUR, dass das Ticket danach weg ist).
    await _connect_and_get_close_code(f"{ws_base}/api/v1/ws/terminal/{session_id}")

    second_code = await _connect_and_get_close_code(f"{ws_base}/api/v1/ws/terminal/{session_id}")
    assert second_code == 4404


async def _enable_terminal_with_ssh_host(db_session, test_settings, local_ssh_server):  # noqa: ANN202
    host_addr, port, username, password, _ = local_ssh_server
    settings = test_settings.model_copy(update={"extensions_dir": REPO_EXTENSIONS_DIR})
    host = await hosts_service.create_host(db_session, name="ssh-host", address=host_addr)
    await hosts_service.add_credential(
        db_session, settings, host_id=host.id, kind="ssh_password",
        username=username, port=port, secret_value=password,
    )
    await db_session.commit()

    from nodvard_deck.main import app

    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "terminal")
    return host


@pytest.mark.asyncio
async def test_terminal_hosts_lists_only_hosts_a_session_can_open(running_app, db_session, test_settings, local_ssh_server):
    """Terminal-Seite: die Seite bietet nur Hosts an, fuer die eine Sitzung
    moeglich ist -- ein Host ohne SSH-Zugangsdaten taucht nicht auf."""
    http_base, _ = running_app
    ssh_host = await _enable_terminal_with_ssh_host(db_session, test_settings, local_ssh_server)
    await hosts_service.create_host(db_session, name="no-credentials", address="10.0.0.9")
    await db_session.commit()

    token = await _bootstrap_owner(http_base)
    async with AsyncClient(base_url=http_base) as ac:
        resp = await ac.get("/api/v1/terminal/hosts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == [ssh_host.id]


@pytest.mark.asyncio
async def test_terminal_session_is_audited_with_open_and_close(running_app, db_session, test_settings, local_ssh_server):
    """docs/04 §4 versprach `terminal.open`/`terminal.close` mit Dauer im Audit-Log --
    geschrieben wurde es nie (beim Bau der Terminal-Seite gefunden)."""
    import asyncio

    import websockets
    from sqlalchemy import select

    from nodvard_deck.models import AuditEntry

    http_base, ws_base = running_app
    host = await _enable_terminal_with_ssh_host(db_session, test_settings, local_ssh_server)
    token = await _bootstrap_owner(http_base)
    async with AsyncClient(base_url=http_base) as ac:
        created = await ac.post(
            "/api/v1/terminal/sessions", json={"host_id": host.id, "cols": 80, "rows": 24},
            headers={"Authorization": f"Bearer {token}"},
        )
    async with websockets.connect(f"{ws_base}{created.json()['ws_url']}") as ws:
        assert await ws.recv() == b"shell-ready\n"
        await ws.send(b"exit\n")
        await ws.recv()

    rows: list = []
    for _ in range(100):
        rows = (
            await db_session.execute(select(AuditEntry).where(AuditEntry.action.in_(["terminal.open", "terminal.close"])))
        ).scalars().all()
        if len(rows) == 2:
            break
        await asyncio.sleep(0.05)
    by_action = {r.action: r for r in rows}
    assert by_action["terminal.open"].outcome == "success"
    assert by_action["terminal.open"].target_id == host.id
    assert "duration_s" in by_action["terminal.close"].detail
