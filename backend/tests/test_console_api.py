"""POST /console/sessions + WS /ws/console/{id} -- die grafische Konsole.
Wie test_terminal_api.py gegen eine echte, laufende App (`running_app`),
weil nur ein echter Server echte WebSockets bedient. Der Konsolen-ANBIETER ist hier
ein Test-Double (der Kern kennt keinen Hypervisor); die echte Proxmox-Kette ist in
test_ext_proxmox.py gegen einen echten vncwebsocket-Mock bewiesen.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient
from nodvard_sdk.capabilities import ConsoleTarget
from sqlalchemy import select

from nodvard_deck.core.console_sessions import reset_console_session_store
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import AuditEntry
from nodvard_deck.services import hosts as hosts_service


@pytest.fixture(autouse=True)
def _fresh_store():
    reset_console_session_store()
    yield
    reset_console_session_store()


class _FakeConsole:
    protocol = "vnc"

    def __init__(self) -> None:
        self.password = "einmal-pw"
        self.written: list[bytes] = []
        self.closed = False
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._queue.put_nowait(b"RFB 003.008\n")

    async def read(self):  # noqa: ANN201
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def write(self, data: bytes) -> None:
        self.written.append(data)
        await self._queue.put(b"echo:" + data)

    async def close(self) -> None:
        self.closed = True
        self._queue.put_nowait(None)


class _FakeConsoleTarget:
    def __init__(self, available_ids: set[str], *, fail: str | None = None) -> None:
        self.available_ids = available_ids
        self.fail = fail
        self.opened: list[_FakeConsole] = []

    async def console_available(self, host) -> bool:  # noqa: ANN001
        return host.id in self.available_ids

    async def open_console(self, host) -> _FakeConsole:  # noqa: ANN001
        if self.fail:
            raise RuntimeError(self.fail)
        console = _FakeConsole()
        self.opened.append(console)
        return console


async def _bootstrap_owner(base_url: str) -> str:
    async with AsyncClient(base_url=base_url) as ac:
        await ac.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
        login = await ac.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
        assert login.status_code == 200, login.text
        return login.json()["access_token"]


async def _close_code(ws_url: str) -> int:
    import websockets
    from websockets.exceptions import ConnectionClosed

    async with websockets.connect(ws_url) as ws:
        try:
            await ws.recv()
        except ConnectionClosed as exc:
            return exc.rcvd.code if exc.rcvd else -1
    return -1


async def _open(http_base: str, token: str, host_id: str):  # noqa: ANN202
    async with AsyncClient(base_url=http_base) as ac:
        return await ac.post(
            "/api/v1/console/sessions", json={"host_id": host_id}, headers={"Authorization": f"Bearer {token}"}
        )


def test_console_target_is_not_confused_with_terminal_target():
    """Der Grund fuer die eigenen Methodennamen im Protokoll: `@runtime_checkable`
    prueft nur Namen. Eine SSH-Terminal-Implementierung darf nie als Konsole gelten
    (sonst lieferte der Kern eine Shell als Bildschirm-Strom aus) -- und umgekehrt."""
    from nodvard_sdk.capabilities import TerminalTarget

    class _Terminal:
        async def can_open(self, host): ...  # noqa: ANN001, ANN201
        async def open(self, host, *, user, cols, rows): ...  # noqa: ANN001, ANN201

    assert not isinstance(_Terminal(), ConsoleTarget)
    assert isinstance(_FakeConsoleTarget(set()), ConsoleTarget)
    assert not isinstance(_FakeConsoleTarget(set()), TerminalTarget)


@pytest.mark.asyncio
async def test_console_session_relays_bytes_both_ways_and_is_audited(running_app, db_session):
    import websockets

    http_base, ws_base = running_app
    host = await hosts_service.create_host(db_session, name="vm-1", address="10.0.0.5")
    await db_session.commit()
    target = _FakeConsoleTarget({host.id})
    get_extension_runtime().capabilities.provide("fake-hv", ConsoleTarget, target)

    token = await _bootstrap_owner(http_base)
    created = await _open(http_base, token, host.id)
    assert created.status_code == 200, created.text
    body = created.json()
    # Das Kennwort muss VOR dem WS-Aufbau beim Browser sein (noVNC oeffnet die URL
    # selbst und beginnt sofort mit dem RFB-Handshake).
    assert body["protocol"] == "vnc"
    assert body["password"] == "einmal-pw"
    assert body["ws_url"] == f"/api/v1/ws/console/{body['session_id']}"

    async with websockets.connect(f"{ws_base}{body['ws_url']}", subprotocols=["binary"]) as ws:
        # Ein angefragtes Subprotokoll muss bestaetigt werden, sonst verwerfen
        # Browser den Handshake.
        assert ws.subprotocol == "binary"
        assert await ws.recv() == b"RFB 003.008\n"
        await ws.send(b"RFB 003.008\n")
        assert await ws.recv() == b"echo:RFB 003.008\n"

    for _ in range(100):
        if target.opened[0].closed:
            break
        await asyncio.sleep(0.02)
    assert target.opened[0].closed, "Browser weg -> Konsolen-Verbindung muss geschlossen werden"

    entries = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "console.open"))).scalars().all()
    assert [(e.outcome, e.target_id) for e in entries] == [("success", host.id)]


@pytest.mark.asyncio
async def test_console_ticket_is_single_use(running_app, db_session):
    http_base, ws_base = running_app
    host = await hosts_service.create_host(db_session, name="vm-1", address="10.0.0.5")
    await db_session.commit()
    get_extension_runtime().capabilities.provide("fake-hv", ConsoleTarget, _FakeConsoleTarget({host.id}))

    token = await _bootstrap_owner(http_base)
    ws_url = (await _open(http_base, token, host.id)).json()["ws_url"]

    import websockets

    async with websockets.connect(f"{ws_base}{ws_url}") as ws:
        assert await ws.recv() == b"RFB 003.008\n"

    assert await _close_code(f"{ws_base}{ws_url}") == 4404


@pytest.mark.asyncio
async def test_unclaimed_console_session_is_closed_after_ttl(running_app, db_session, test_settings):
    """Ein abgebrochener Seitenaufruf darf keine offene Hypervisor-Verbindung
    zuruecklassen -- nicht abgeholte Sitzungen werden GESCHLOSSEN, nicht nur vergessen."""
    http_base, ws_base = running_app
    test_settings.terminal_session_ttl_s = 0.2
    host = await hosts_service.create_host(db_session, name="vm-1", address="10.0.0.5")
    await db_session.commit()
    target = _FakeConsoleTarget({host.id})
    get_extension_runtime().capabilities.provide("fake-hv", ConsoleTarget, target)

    token = await _bootstrap_owner(http_base)
    ws_url = (await _open(http_base, token, host.id)).json()["ws_url"]

    for _ in range(100):
        if target.opened[0].closed:
            break
        await asyncio.sleep(0.02)
    assert target.opened[0].closed
    assert await _close_code(f"{ws_base}{ws_url}") == 4404


@pytest.mark.asyncio
async def test_open_failure_is_reported_and_audited(running_app, db_session):
    http_base, _ = running_app
    host = await hosts_service.create_host(db_session, name="vm-1", address="10.0.0.5")
    await db_session.commit()
    get_extension_runtime().capabilities.provide(
        "fake-hv", ConsoleTarget, _FakeConsoleTarget({host.id}, fail="Permission check failed (VM.Console)")
    )

    token = await _bootstrap_owner(http_base)
    resp = await _open(http_base, token, host.id)
    assert resp.status_code == 502
    assert "VM.Console" in resp.json()["detail"]

    entries = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "console.open"))).scalars().all()
    assert [e.outcome for e in entries] == ["failure"]


@pytest.mark.asyncio
async def test_host_without_console_and_unknown_host_are_404(running_app, db_session):
    http_base, _ = running_app
    host = await hosts_service.create_host(db_session, name="plain", address="10.0.0.6")
    await db_session.commit()
    get_extension_runtime().capabilities.provide("fake-hv", ConsoleTarget, _FakeConsoleTarget(set()))

    token = await _bootstrap_owner(http_base)
    assert (await _open(http_base, token, host.id)).status_code == 404
    assert (await _open(http_base, token, "gibt-es-nicht")).status_code == 404


@pytest.mark.asyncio
async def test_console_requires_login(running_app, db_session):
    http_base, _ = running_app
    async with AsyncClient(base_url=http_base) as ac:
        resp = await ac.post("/api/v1/console/sessions", json={"host_id": "x"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_console_hosts_lists_only_hosts_with_a_console(running_app, db_session):
    http_base, _ = running_app
    vm = await hosts_service.create_host(db_session, name="vm-1", address="10.0.0.5")
    await hosts_service.create_host(db_session, name="plain", address="10.0.0.6")
    await db_session.commit()
    get_extension_runtime().capabilities.provide("fake-hv", ConsoleTarget, _FakeConsoleTarget({vm.id}))

    token = await _bootstrap_owner(http_base)
    async with AsyncClient(base_url=http_base) as ac:
        resp = await ac.get("/api/v1/console/hosts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == [vm.id]


async def _operator_login(http_base: str, owner_token: str) -> tuple[str, str]:
    """Nutzer mit Rolle operator (hat `hosts.execute`): (ID, Access-Token)."""
    async with AsyncClient(base_url=http_base) as ac:
        roles = {r["name"]: r["id"] for r in (await ac.get("/api/v1/roles", headers={"Authorization": f"Bearer {owner_token}"})).json()}
        created = await ac.post(
            "/api/v1/users",
            json={"username": "bedienung", "password": "correct-horse-battery", "role_ids": [roles["operator"]]},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert created.status_code == 201, created.text
        login = await ac.post("/api/v1/auth/login", json={"username": "bedienung", "password": "correct-horse-battery"})
        return created.json()["id"], login.json()["access_token"]


async def _deactivate(http_base: str, owner_token: str, user_id: str) -> None:
    async with AsyncClient(base_url=http_base) as ac:
        r = await ac.patch(f"/api/v1/users/{user_id}", json={"is_active": False}, headers={"Authorization": f"Bearer {owner_token}"})
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_open_console_ends_when_the_account_is_deactivated(running_app, db_session, test_settings, pause_session_guard):
    import websockets
    from websockets.exceptions import ConnectionClosed

    test_settings.terminal_recheck_interval_s = 0.2
    http_base, ws_base = running_app
    host = await hosts_service.create_host(db_session, name="vm-1", address="10.0.0.5")
    await db_session.commit()
    target = _FakeConsoleTarget({host.id})
    get_extension_runtime().capabilities.provide("fake-hv", ConsoleTarget, target)

    owner_token = await _bootstrap_owner(http_base)
    user_id, token = await _operator_login(http_base, owner_token)
    ws_url = (await _open(http_base, token, host.id)).json()["ws_url"]

    from nodvard_deck.api.v1 import console as console_api

    with pause_session_guard(console_api) as lock:
        async with websockets.connect(f"{ws_base}{ws_url}") as ws:
            assert await ws.recv() == b"RFB 003.008\n"
            async with lock:  # siehe conftest.py `pause_session_guard`
                await _deactivate(http_base, owner_token, user_id)
                await db_session.commit()
            with pytest.raises(ConnectionClosed) as closed:
                async with asyncio.timeout(8):
                    while True:
                        await ws.recv()
    assert closed.value.rcvd.code == 4401
    for _ in range(100):
        if target.opened[0].closed:
            break
        await asyncio.sleep(0.02)
    assert target.opened[0].closed


@pytest.mark.asyncio
async def test_console_ticket_of_a_deactivated_account_is_refused(running_app, db_session):
    http_base, ws_base = running_app
    host = await hosts_service.create_host(db_session, name="vm-1", address="10.0.0.5")
    await db_session.commit()
    target = _FakeConsoleTarget({host.id})
    get_extension_runtime().capabilities.provide("fake-hv", ConsoleTarget, target)

    owner_token = await _bootstrap_owner(http_base)
    user_id, token = await _operator_login(http_base, owner_token)
    ws_url = (await _open(http_base, token, host.id)).json()["ws_url"]
    await _deactivate(http_base, owner_token, user_id)
    await db_session.commit()

    import websockets
    from websockets.exceptions import ConnectionClosed

    async with websockets.connect(f"{ws_base}{ws_url}") as ws:
        with pytest.raises(ConnectionClosed) as closed:
            async with asyncio.timeout(8):
                while True:
                    await ws.recv()
    assert closed.value.rcvd.code == 4401
    for _ in range(100):
        if target.opened[0].closed:
            break
        await asyncio.sleep(0.02)
    assert target.opened[0].closed
