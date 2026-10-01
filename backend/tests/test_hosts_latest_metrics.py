"""Letzte Messwerte aller Server in EINER Abfrage (`GET /hosts/metrics/latest`, Cockpit-Auslastung).

Die Werte kommen aus dem schon gefuehrten Verlauf (`metrics.db`), nie per SSH: Das Cockpit darf beim
Laden keinen Server anrufen und nicht langsamer werden, je mehr Server es gibt."""

from __future__ import annotations

import time

import pytest
from nodvard_deck.core import metrics_history
from nodvard_deck.core.metrics_history import (
    LATEST_WINDOW_S,
    STALE_AFTER_S,
    MetricsCollector,
    MetricsStore,
    reset_metrics_collector,
)

GB = 1024**3


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _owner_token(client) -> str:
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    return (await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})).json()["access_token"]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Eigener Verlauf in tmp_path, als Sammler des Kerns eingehaengt."""
    reset_metrics_collector()
    collector = MetricsCollector(MetricsStore(tmp_path / "metrics.db"), interval_s=30, raw_retention_s=48 * 3600, rollup_retention_s=86400)
    monkeypatch.setattr(metrics_history, "_collector", collector)
    yield collector.store
    reset_metrics_collector()


def _full(cpu: float = 10.0, mem_gb: float = 2.0, root_gb: float = 50.0) -> dict[str, float]:
    return {
        "cpu_percent": cpu, "mem_used_bytes": mem_gb * GB, "mem_total_bytes": 8 * GB,
        "root_used_bytes": root_gb * GB, "root_total_bytes": 200 * GB, "uptime_s": 86400.0, "temp_c": 40.0,
    }


async def _host(client, token: str, name: str, **extra) -> str:
    r = await client.post("/api/v1/hosts", json={"name": name, "address": f"10.0.0.{abs(hash(name)) % 200 + 1}", **extra}, headers=_auth(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_store_latest_takes_the_newest_value_per_host_and_metric(tmp_path):
    store = MetricsStore(tmp_path / "m.db")
    now = 1_760_000_000
    store.insert("h1", now - 90, {"cpu_percent": 10.0, "mem_used_bytes": 1.0})
    store.insert("h1", now - 30, {"cpu_percent": 20.0})  # mem fehlt in der neuesten Messung -> bleibt der aeltere Wert
    store.insert("h2", now - 5, {"cpu_percent": 99.0})
    store.insert("gone", now - 5, {"cpu_percent": 1.0})
    got = store.latest(["h1", "h2", "none"], now, max_age_s=600, metrics=("cpu_percent", "mem_used_bytes"))
    assert got == {
        "h1": {"cpu_percent": (now - 30, 20.0), "mem_used_bytes": (now - 90, 1.0)},
        "h2": {"cpu_percent": (now - 5, 99.0)},
    }


def test_store_latest_ignores_values_outside_the_window(tmp_path):
    store = MetricsStore(tmp_path / "m.db")
    now = 1_760_000_000
    store.insert("h1", now - 601, {"cpu_percent": 10.0})
    assert store.latest(["h1"], now, max_age_s=600, metrics=("cpu_percent",)) == {}


@pytest.mark.asyncio
async def test_latest_returns_cpu_ram_disk_for_a_measured_server(client, db_session, store):
    token = await _owner_token(client)
    pi = await _host(client, token, "pi-host")
    await db_session.commit()
    now = int(time.time())
    store.insert(pi, now - 60, _full(cpu=80.0))
    store.insert(pi, now - 10, _full(cpu=12.34, mem_gb=2.0, root_gb=50.0))

    r = await client.get("/api/v1/hosts/metrics/latest", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stale_after_s"] == STALE_AFTER_S
    m = body["hosts"][pi]
    assert m["cpu"] == 12.3
    assert m["mem"] == 25.0 and m["mem_used_bytes"] == 2 * GB and m["mem_total_bytes"] == 8 * GB
    assert m["disk"] == 25.0 and m["disk_used_bytes"] == 50 * GB and m["disk_total_bytes"] == 200 * GB
    assert m["uptime_s"] == 86400
    assert 9 <= m["age_s"] <= 20 and m["stale"] is False
    assert m["at"].startswith("20") and "T" in m["at"]


@pytest.mark.asyncio
async def test_latest_without_history_is_empty_not_an_error(client, store):
    token = await _owner_token(client)
    await _host(client, token, "bare")
    r = await client.get("/api/v1/hosts/metrics/latest", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["hosts"] == {}


@pytest.mark.asyncio
async def test_latest_marks_old_values_stale_and_drops_very_old_ones(client, db_session, store):
    token = await _owner_token(client)
    fresh = await _host(client, token, "fresh")
    stale = await _host(client, token, "stale")
    ancient = await _host(client, token, "ancient")
    await db_session.commit()
    now = int(time.time())
    store.insert(fresh, now - 20, _full())
    store.insert(stale, now - (STALE_AFTER_S + 60), _full())
    store.insert(ancient, now - (LATEST_WINDOW_S + 60), _full())

    hosts = (await client.get("/api/v1/hosts/metrics/latest", headers=_auth(token))).json()["hosts"]
    assert hosts[fresh]["stale"] is False
    assert hosts[stale]["stale"] is True and hosts[stale]["age_s"] >= STALE_AFTER_S + 60
    assert ancient not in hosts


@pytest.mark.asyncio
async def test_latest_leaves_out_disabled_deleted_and_foreign_ids_and_tolerates_missing_values(client, db_session, store):
    token = await _owner_token(client)
    off = await _host(client, token, "off")
    gone = await _host(client, token, "gone")
    partial = await _host(client, token, "partial")
    assert (await client.patch(f"/api/v1/hosts/{off}", json={"enabled": False}, headers=_auth(token))).status_code == 200
    assert (await client.delete(f"/api/v1/hosts/{gone}", headers=_auth(token))).status_code == 204
    await db_session.commit()
    now = int(time.time())
    for host_id in (off, gone, "not-a-host"):
        store.insert(host_id, now - 5, _full())
    store.insert(partial, now - 5, {"cpu_percent": 3.0, "mem_used_bytes": 1.0, "mem_total_bytes": 0.0})  # keine Platte, RAM-Summe 0

    hosts = (await client.get("/api/v1/hosts/metrics/latest", headers=_auth(token))).json()["hosts"]
    assert list(hosts) == [partial]
    assert hosts[partial]["cpu"] == 3.0
    assert hosts[partial]["mem"] is None and hosts[partial]["disk"] is None
    assert hosts[partial]["uptime_s"] is None


@pytest.mark.asyncio
async def test_latest_needs_hosts_read(client, db_session, store):
    from nodvard_deck.core import security
    from nodvard_deck.models import Role, RolePermission, User
    from nodvard_deck.services import auth as auth_service

    assert (await client.get("/api/v1/hosts/metrics/latest")).status_code == 401
    owner = await _owner_token(client)

    roles = await auth_service.ensure_builtin_roles(db_session)
    other = Role(name="nur-meldungen", description="")
    db_session.add(other)
    await db_session.flush()
    db_session.add(RolePermission(role_id=other.id, permission="notifications.read"))
    await db_session.flush()
    await db_session.refresh(other, ["permissions"])
    for name, role in (("nohosts", other), ("viewer9", roles["viewer"])):
        user = User(username=name, password_hash=security.hash_password("whatever123"), is_active=True)
        user.roles.append(role)
        db_session.add(user)
    await db_session.flush()
    await db_session.commit()

    async def token_of(name: str) -> str:
        return (await client.post("/api/v1/auth/login", json={"username": name, "password": "whatever123"})).json()["access_token"]

    assert (await client.get("/api/v1/hosts/metrics/latest", headers=_auth(await token_of("nohosts")))).status_code == 403
    assert (await client.get("/api/v1/hosts/metrics/latest", headers=_auth(await token_of("viewer9")))).status_code == 200
    assert (await client.get("/api/v1/hosts/metrics/latest", headers=_auth(owner))).status_code == 200


@pytest.mark.asyncio
async def test_latest_is_one_store_query_for_all_hosts_and_never_calls_ssh(client, db_session, store, monkeypatch):
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_sdk.capabilities import MetricsProvider

    class _Ssh:
        calls = 0

        async def supports(self, host):
            return True

        async def sample(self, host):
            _Ssh.calls += 1
            return {}

        async def metric_names(self):
            return []

    get_extension_runtime().capabilities.provide("system", MetricsProvider, _Ssh())
    token = await _owner_token(client)
    ids = [await _host(client, token, f"srv-{i}") for i in range(30)]
    await db_session.commit()
    now = int(time.time())
    for i, host_id in enumerate(ids):
        store.insert(host_id, now - 5, _full(cpu=float(i)))

    queries: list[int] = []
    real = store.latest

    def counting(host_ids, *a, **kw):
        queries.append(len(list(host_ids)))
        return real(host_ids, *a, **kw)

    monkeypatch.setattr(store, "latest", counting)
    hosts = (await client.get("/api/v1/hosts/metrics/latest", headers=_auth(token))).json()["hosts"]
    assert len(hosts) == 30 and hosts[ids[7]]["cpu"] == 7.0
    assert queries == [30], "eine Abfrage fuer alle Server, nicht eine je Server"
    assert _Ssh.calls == 0, "Das Cockpit loest keine SSH-Messung aus"


@pytest.mark.asyncio
async def test_host_routes_are_not_shadowed_by_the_latest_route(client, db_session, store):
    token = await _owner_token(client)
    host_id = await _host(client, token, "one")
    await db_session.commit()
    assert (await client.get(f"/api/v1/hosts/{host_id}", headers=_auth(token))).status_code == 200
    assert (await client.get(f"/api/v1/hosts/{host_id}/metrics", headers=_auth(token))).status_code == 404  # kein Anbieter
