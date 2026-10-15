"""Shell IN etwas auf einem Host (`ScopedTerminalTarget`, z. B. ein Container): `POST /terminal/sessions` mit
`scope` und `ref`, dieselbe WS-Bruecke, Protokoll mit `scope`/`ref`. Der Anbieter ist hier ein Test-Double; den
echten (Docker-Modul, `docker exec`) pruefen test_ext_docker_shell.py und die Kette ueber SSH."""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import AsyncClient
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import AuditEntry
from nodvard_deck.services import hosts as hosts_service
from nodvard_sdk.capabilities import ScopedTerminalTarget, TerminalTarget
from sqlalchemy import select
from test_terminal_api import _bootstrap_owner, _connect_and_get_close_code

FAKE_EXT = "fake-scoped-shell"


class _Session:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.written: list[bytes] = []
        self.closed = False

    async def read(self):
        yield b"in-container\n"
        while True:
            data = await self._queue.get()
            if data is None:
                return
            yield data

    async def write(self, data: bytes) -> None:
        self.written.append(data)
        await self._queue.put(None if data == b"exit\n" else b"echo:" + data)

    async def resize(self, cols: int, rows: int) -> None:
        return None

    async def close(self) -> None:
        self.closed = True
        await self._queue.put(None)

    @property
    def exit_code(self) -> int | None:
        return 0


class _Shell:
    scope = "container"

    def __init__(self) -> None:
        self.opened: list[tuple[str, str, int, int]] = []
        self.session = _Session()

    async def can_open_in(self, host, ref: str) -> bool:
        return ref != "fremd"

    async def open_in(self, host, ref: str, *, cols: int, rows: int) -> _Session:
        self.opened.append((host.id, ref, cols, rows))
        return self.session


@pytest.fixture
def shell():
    impl = _Shell()
    runtime = get_extension_runtime()
    runtime.capabilities.provide(FAKE_EXT, ScopedTerminalTarget, impl)
    yield impl
    runtime.capabilities.clear_extension(FAKE_EXT)


def test_the_double_is_a_scoped_target_and_nothing_else():
    assert isinstance(_Shell(), ScopedTerminalTarget) and not isinstance(_Shell(), TerminalTarget)


@pytest.mark.asyncio
async def test_scope_and_ref_are_checked_before_a_ticket_exists(running_app, db_session, shell):
    http_base, _ = running_app
    host = await hosts_service.create_host(db_session, name="docker-host", address="10.0.0.5")
    await db_session.commit()
    token = await _bootstrap_owner(http_base)
    auth = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(base_url=http_base) as ac:
        for body in (
            {"scope": "container"},  # ohne ref
            {"ref": "nginx"},  # ohne scope
            {"scope": "container", "ref": "x;rm -rf /"},
            {"scope": "container", "ref": "-it"},
            {"scope": "Container", "ref": "nginx"},
            {"scope": "container", "ref": "a" * 300},
        ):
            r = await ac.post("/api/v1/terminal/sessions", json={"host_id": host.id, **body}, headers=auth)
            assert r.status_code == 422, (body, r.text)
        for body in ({"scope": "vm", "ref": "nginx"}, {"scope": "container", "ref": "fremd"}):
            r = await ac.post("/api/v1/terminal/sessions", json={"host_id": host.id, **body}, headers=auth)
            assert r.status_code == 404, (body, r.text)
            assert "kein Modul" in r.json()["detail"]
    assert shell.opened == []


@pytest.mark.asyncio
async def test_a_scoped_session_opens_inside_and_is_audited_with_scope_and_ref(running_app, db_session, shell):
    import websockets

    http_base, ws_base = running_app
    host = await hosts_service.create_host(db_session, name="docker-host", address="10.0.0.5")
    await db_session.commit()
    token = await _bootstrap_owner(http_base)
    async with AsyncClient(base_url=http_base) as ac:
        created = await ac.post(
            "/api/v1/terminal/sessions", json={"host_id": host.id, "cols": 100, "rows": 30, "scope": "container", "ref": "nginx"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert created.status_code == 200, created.text
    async with websockets.connect(f"{ws_base}{created.json()['ws_url']}") as ws:
        assert await ws.recv() == b"in-container\n"
        await ws.send(b"ls\n")
        assert await ws.recv() == b"echo:ls\n"
        await ws.send(b"exit\n")
        assert json.loads(await ws.recv())["type"] == "exit"
    assert shell.opened == [(host.id, "nginx", 100, 30)]

    rows: list = []
    for _ in range(100):
        rows = (
            await db_session.execute(select(AuditEntry).where(AuditEntry.action.in_(["terminal.open", "terminal.close"])))
        ).scalars().all()
        if len(rows) == 2:
            break
        await asyncio.sleep(0.05)
    by_action = {r.action: r for r in rows}
    assert by_action["terminal.open"].detail == {"scope": "container", "ref": "nginx"}
    assert by_action["terminal.close"].detail["ref"] == "nginx" and "duration_s" in by_action["terminal.close"].detail


@pytest.mark.asyncio
async def test_a_scoped_ticket_never_falls_back_to_the_host_shell(running_app, db_session, shell):
    """Verschwindet der Anbieter zwischen Ticket und Verbindung, endet die Sitzung -- sie oeffnet nicht stattdessen
    eine Shell auf dem Host."""
    http_base, ws_base = running_app
    host = await hosts_service.create_host(db_session, name="docker-host", address="10.0.0.5")
    await db_session.commit()
    token = await _bootstrap_owner(http_base)
    async with AsyncClient(base_url=http_base) as ac:
        created = await ac.post(
            "/api/v1/terminal/sessions", json={"host_id": host.id, "scope": "container", "ref": "nginx"},
            headers={"Authorization": f"Bearer {token}"},
        )
    get_extension_runtime().capabilities.clear_extension(FAKE_EXT)
    assert await _connect_and_get_close_code(f"{ws_base}{created.json()['ws_url']}") == 4501
