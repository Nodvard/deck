"""Viele langsame Widgets zugleich: Solange eine Anfrage auf einen Fremddienst wartet, gehört ihr keine
Datenbankverbindung. Sonst ist der Pool (5 + 10) bei vielen Widgets leer und der Rest scheitert nach 30 s mit
`QueuePool limit … reached`."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import APIRouter, Depends
from nodvard_deck.api.deps import require_permission
from nodvard_deck.db.session import get_engine
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import Host
from nodvard_sdk import ActionSpec, HostStatus
from nodvard_sdk.capabilities import HostProvider, MetricsProvider
from sqlalchemy import update

PASSWORD = "correct-horse-battery"
WIDGETS = 25
# Greift nur ohne Fix: So lange warten die ersten Widgets auf die übrigen, die keine Verbindung bekommen.
GIVE_UP_S = 10.0


async def _owner_token(client) -> str:
    r = await client.post(
        "/api/v1/auth/bootstrap", json={"username": "nico", "password": PASSWORD, "setup_code": "TEST-CODE-2345"}
    )
    assert r.status_code == 201, r.text
    login = await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_25_slow_widgets_wait_together_without_holding_a_connection(client, file_db):
    from nodvard_deck.main import app

    pool = get_engine().pool
    assert (pool.size(), pool._max_overflow) == (5, 10)
    waiting = 0
    all_waiting = asyncio.Event()
    held_when_all_waiting: list[int] = []

    router = APIRouter()

    # Wie die Widget-Route einer Erweiterung: dieselbe Rechte-Prüfung, dann warten auf den Fremddienst. Der antwortet
    # erst, wenn alle 25 zugleich warten; das klappt nur, wenn keine Anfrage dabei eine Verbindung hält.
    @router.get("/api/v1/test-slow-widget", dependencies=[Depends(require_permission("hosts.read"))])
    async def slow_widget() -> dict:
        nonlocal waiting
        waiting += 1
        if waiting == WIDGETS:
            held_when_all_waiting.append(pool.checkedout())
            all_waiting.set()
        await asyncio.wait_for(all_waiting.wait(), timeout=GIVE_UP_S)
        return {"ok": True}

    app.include_router(router)
    headers = {"Authorization": f"Bearer {await _owner_token(client)}"}

    responses = await asyncio.gather(*(client.get("/api/v1/test-slow-widget", headers=headers) for _ in range(WIDGETS)))

    assert held_when_all_waiting == [0], "nicht alle Widgets konnten gleichzeitig warten: der Pool war voll"
    assert [r.status_code for r in responses] == [200] * WIDGETS


class _Providers:
    """Anbieter, die sich merken, wie viele Verbindungen gerade ausgeliehen sind, während sie antworten."""

    def __init__(self) -> None:
        self.held: dict[str, int] = {}

    def _note(self, name: str) -> None:
        self.held[name] = get_engine().pool.checkedout()

    async def sample(self, host) -> dict[str, float]:
        self._note("sample")
        return {"cpu_percent": 3.0}

    async def metric_names(self) -> list[str]:
        return ["cpu_percent"]

    async def history(self, host, range_name: str) -> dict:
        self._note("history")
        return {"step_s": 60, "timestamps": [], "series": {}}

    async def host_status(self, provider_ref: str) -> HostStatus:
        self._note("host_status")
        return HostStatus.UP

    async def host_actions(self, provider_ref: str) -> list[ActionSpec]:
        self._note("host_actions")
        return []


@pytest.mark.asyncio
async def test_host_routes_release_the_connection_while_the_provider_answers(client, file_db):
    headers = {"Authorization": f"Bearer {await _owner_token(client)}"}
    host = (await client.post("/api/v1/hosts", json={"name": "a", "address": "192.0.2.10"}, headers=headers)).json()
    async with file_db() as session:
        await session.execute(
            update(Host).where(Host.id == host["id"]).values(provider_ext_id="slow-provider", provider_ref="vm/100")
        )
        await session.commit()
    providers = _Providers()
    capabilities = get_extension_runtime().capabilities
    capabilities.provide("slow-provider", MetricsProvider, providers)
    capabilities.provide("slow-provider", HostProvider, providers)

    for path in ("metrics", "metrics/history", "status", "actions"):
        r = await client.get(f"/api/v1/hosts/{host['id']}/{path}", headers=headers)
        assert r.status_code == 200, (path, r.text)

    assert providers.held == {"sample": 0, "history": 0, "host_status": 0, "host_actions": 0}

    providers.held.clear()
    r = await client.post(f"/api/v1/hosts/{host['id']}/actions/vm.reboot", json={"reason": "Test"}, headers=headers)
    assert r.status_code == 404, r.text
    assert providers.held == {"host_actions": 0}
