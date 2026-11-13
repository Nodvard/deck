"""Metrik-Verlauf (core/metrics_history.py): eigener Speicher fuer Hosts ohne Historie
beim Anbieter, Verdichtung, Aufbewahrung, Sammler und API."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from nodvard_deck.core.metrics_history import ROLLUP_S, MetricsCollector, MetricsStore, reset_metrics_collector

T0 = 1_760_000_000 - 1_760_000_000 % ROLLUP_S  # auf ein 5-Minuten-Fenster ausgerichtet


@pytest.mark.parametrize("range_name", ["1h", "24h"])
def test_linked_history_fills_gaps_without_overwriting_manual_samples(tmp_path, range_name):
    store = MetricsStore(tmp_path / "m.db")
    store.insert("hand", T0, {"cpu_percent": 10.0})
    store.insert("source", T0, {"cpu_percent": 99.0, "root_used_bytes": 25.0})
    store.insert("source", T0 + ROLLUP_S, {"cpu_percent": 20.0})
    now = T0 + 2 * ROLLUP_S
    store.maintain(now, raw_retention_s=3600, rollup_retention_s=86400)
    result = store.query_hosts("hand", ["source"], range_name, now, interval_s=30, raw_retention_s=3600)
    assert [v for v in result["series"]["cpu_percent"] if v is not None] == [10.0, 20.0]
    assert [v for v in result["max"]["cpu_percent"] if v is not None] == [10.0, 20.0]
    assert 25.0 in result["series"]["root_used_bytes"]
    assert 99.0 in store.query("source", range_name, now, interval_s=30, raw_retention_s=3600)["series"]["cpu_percent"]
    store.close()


def test_raw_query_returns_a_shared_time_axis_with_gaps(tmp_path):
    store = MetricsStore(tmp_path / "m.db")
    store.insert("h1", T0 + 30, {"cpu_percent": 10.0, "mem_used_bytes": 100.0})
    store.insert("h1", T0 + 60, {"cpu_percent": 30.0})
    # T0+90 fehlt (Host war nicht erreichbar) -> Luecke, keine erfundene Null
    store.insert("h1", T0 + 120, {"cpu_percent": 50.0})
    store.insert("other", T0 + 60, {"cpu_percent": 99.0})

    data = store.query("h1", "1h", T0 + 120, interval_s=30, raw_retention_s=48 * 3600)
    assert data["step_s"] == 30
    ts = data["timestamps"]
    cpu = dict(zip(ts, data["series"]["cpu_percent"]))
    assert (cpu[T0 + 30], cpu[T0 + 60], cpu[T0 + 90], cpu[T0 + 120]) == (10.0, 30.0, None, 50.0)
    assert dict(zip(ts, data["series"]["mem_used_bytes"]))[T0 + 60] is None
    assert ts[-1] == T0 + 120 and ts[1] - ts[0] == 30


def test_long_ranges_use_the_5_minute_rollup_with_true_min_max(tmp_path):
    store = MetricsStore(tmp_path / "m.db")
    for i, value in enumerate([10.0, 90.0, 20.0]):
        store.insert("h1", T0 + i * 30, {"cpu_percent": value})
    store.maintain(T0 + ROLLUP_S + 1, raw_retention_s=48 * 3600, rollup_retention_s=35 * 86400)

    data = store.query("h1", "7d", T0 + ROLLUP_S + 1, interval_s=30, raw_retention_s=48 * 3600)
    assert data["step_s"] >= ROLLUP_S
    values = [v for v in data["series"]["cpu_percent"] if v is not None]
    peaks = [v for v in data["max"]["cpu_percent"] if v is not None]
    assert values == [40.0] and peaks == [90.0], "Mittel und Spitze bleiben beim Verdichten erhalten"


def test_retention_drops_old_raw_and_rollup_rows(tmp_path):
    store = MetricsStore(tmp_path / "m.db")
    store.insert("h1", T0, {"cpu_percent": 1.0})
    store.maintain(T0 + ROLLUP_S, raw_retention_s=3600, rollup_retention_s=86400)
    now = T0 + 2 * 86400
    store.maintain(now, raw_retention_s=3600, rollup_retention_s=86400)
    db = store._db()
    assert db.execute("SELECT COUNT(*) FROM raw").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM rollup").fetchone()[0] == 0


def test_many_points_are_downsampled_to_a_readable_count(tmp_path):
    store = MetricsStore(tmp_path / "m.db")
    now = T0 + 86400
    for i in range(0, 86400, 30):
        store.insert("h1", T0 + i, {"cpu_percent": float(i % 100)})
    data = store.query("h1", "24h", now, interval_s=30, raw_retention_s=48 * 3600)
    assert len(data["timestamps"]) <= 720
    assert data["step_s"] == 120


def test_store_takes_concurrent_calls_from_many_threads(tmp_path):
    """Sammler (ein Thread je Host), Oberfläche (liest) und Verdichtung treffen in wechselnden Threads auf dieselbe
    Datei; jeder Aufruf muss ohne sqlite-Fehler durchgehen und nichts darf verloren gehen."""
    store = MetricsStore(tmp_path / "m.db")  # bewusst noch ohne Verbindung: auch das erste Öffnen läuft nebenläufig
    n_threads, per_thread = 8, 40
    now = T0 + ROLLUP_S + 1
    start = threading.Barrier(n_threads)
    errors: list[str] = []

    def work(i: int) -> None:
        start.wait(timeout=10)
        for k in range(per_thread):
            try:
                store.insert(f"h{i}", T0 + k, {"cpu_percent": float(k), "mem_used_bytes": 2.0})
                if k % 8 == 0:
                    store.latest([f"h{i}"], now, max_age_s=3600)
                    store.query(f"h{i}", "1h", now, interval_s=30, raw_retention_s=48 * 3600)
                    store.maintain(now, raw_retention_s=48 * 3600, rollup_retention_s=35 * 86400)
            except Exception as exc:  # noqa: BLE001 - gesammelt, damit der Test alle Fehlerarten zeigt
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "ein Thread hängt (Sperre nie freigegeben?)"

    assert sorted(set(errors)) == []
    assert store._db().execute("SELECT COUNT(*) FROM raw").fetchone()[0] == n_threads * per_thread * 2
    assert store.latest(["h3"], now, max_age_s=3600)["h3"]["cpu_percent"] == (T0 + per_thread - 1, per_thread - 1.0)
    store.close()


class _SshLike:
    """Anbieter OHNE eigene Historie -> der Sammler legt ab."""

    def __init__(self) -> None:
        self.calls = 0

    async def supports(self, host):
        return host.os_family == "linux"

    async def sample(self, host):
        self.calls += 1
        return {"cpu_percent": 42.0, "temp_c": 51.5}

    async def metric_names(self):
        return ["cpu_percent", "temp_c"]


class _WithHistory(_SshLike):
    async def history(self, host, range_name):
        return {"step_s": 60, "timestamps": [1, 61], "series": {"cpu_percent": [5.0, 6.0]}, "max": {}}


async def _owner_token(client) -> str:
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    return (await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})).json()["access_token"]


@pytest.mark.asyncio
async def test_collector_stores_hosts_without_provider_history_and_api_reads_them(client, db_session, test_settings, monkeypatch, tmp_path):
    from nodvard_deck import config
    from nodvard_deck.core import metrics_history
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_sdk.capabilities import MetricsProvider

    monkeypatch.setattr(config, "_settings", test_settings)
    reset_metrics_collector()
    token = await _owner_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    pi_id = (await client.post("/api/v1/hosts", json={"name": "pi-host", "address": "10.0.0.2"}, headers=headers)).json()["id"]
    await db_session.commit()

    ssh = _SshLike()
    get_extension_runtime().capabilities.provide("system", MetricsProvider, ssh)
    collector = MetricsCollector(MetricsStore(tmp_path / "metrics.db"), interval_s=30, raw_retention_s=48 * 3600, rollup_retention_s=86400)
    monkeypatch.setattr(metrics_history, "_collector", collector)

    async def _same_session_scope():
        yield db_session

    from contextlib import asynccontextmanager

    monkeypatch.setattr("nodvard_deck.db.session.session_scope", asynccontextmanager(_same_session_scope))
    assert await collector.collect_once() == 1
    assert ssh.calls == 1

    r = await client.get(f"/api/v1/hosts/{pi_id}/metrics/history?range=1h", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "lattice"
    assert [v for v in body["series"]["cpu_percent"] if v is not None] == [42.0]
    assert [v for v in body["series"]["temp_c"] if v is not None] == [51.5]

    assert (await client.get(f"/api/v1/hosts/{pi_id}/metrics/history?range=2y", headers=headers)).status_code == 422
    reset_metrics_collector()


@pytest.mark.asyncio
async def test_provider_with_own_history_is_asked_directly_and_never_stored(client, db_session, monkeypatch, tmp_path):
    from contextlib import asynccontextmanager

    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_sdk.capabilities import MetricsProvider

    token = await _owner_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    host_id = (await client.post("/api/v1/hosts", json={"name": "pve2", "address": "10.0.0.3"}, headers=headers)).json()["id"]
    await db_session.commit()
    provider = _WithHistory()
    get_extension_runtime().capabilities.provide("system", MetricsProvider, provider)

    r = await client.get(f"/api/v1/hosts/{host_id}/metrics/history?range=24h", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"range": "24h", "source": "provider", "step_s": 60, "timestamps": [1, 61],
                        "series": {"cpu_percent": [5.0, 6.0]}, "max": {}}

    async def _same_session_scope():
        yield db_session

    monkeypatch.setattr("nodvard_deck.db.session.session_scope", asynccontextmanager(_same_session_scope))
    collector = MetricsCollector(MetricsStore(tmp_path / "metrics.db"), interval_s=30, raw_retention_s=3600, rollup_retention_s=86400)
    assert await collector.collect_once() == 0
    now = int(time.time())
    assert collector.store.latest([host_id], now, max_age_s=600) == {}
    assert provider.calls == 1
    ts, cpu = collector.current([host_id], now, max_age_s=600)[host_id]["cpu_percent"]
    assert cpu == 42.0
    assert collector.current([host_id], ts + 601, max_age_s=600) == {}


class _Bundled(_WithHistory):
    def __init__(self) -> None:
        super().__init__()
        self.many: list[list[str]] = []
        self.fail = False
        self.hang = False
        self.stopped: set[str] = set()

    async def sample_many(self, hosts):
        self.many.append(sorted(h.id for h in hosts))
        if self.fail:
            raise RuntimeError("Proxmox antwortet nicht")
        if self.hang:
            await asyncio.sleep(5)
        values = {"cpu_percent": 7.0, "mem_used_bytes": 1024.0, "mem_total_bytes": 4096.0, "uptime_s": 600.0}
        return {h.id: {} if h.id in self.stopped else values for h in hosts}


class _Failing(_WithHistory):
    async def sample(self, host):
        raise RuntimeError("keine Antwort")


@pytest.fixture
def history_env(client, db_session, monkeypatch, tmp_path):
    """Baut Hosts, einen Anbieter mit eigener Historie und einen Sammler; liefert (Sammler, Header, Host-IDs)."""
    return lambda provider, names: _collector_with_hosts(client, db_session, monkeypatch, tmp_path, provider, names)


async def _collector_with_hosts(client, db_session, monkeypatch, tmp_path, provider, names):
    from contextlib import asynccontextmanager

    from nodvard_deck.core import metrics_history
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_sdk.capabilities import MetricsProvider

    token = await _owner_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    ids = []
    for i, name in enumerate(names):
        r = await client.post("/api/v1/hosts", json={"name": name, "address": f"10.0.1.{i + 1}"}, headers=headers)
        ids.append(r.json()["id"])
    await db_session.commit()
    get_extension_runtime().capabilities.provide("system", MetricsProvider, provider)

    async def _same_session_scope():
        yield db_session

    monkeypatch.setattr("nodvard_deck.db.session.session_scope", asynccontextmanager(_same_session_scope))
    collector = MetricsCollector(
        MetricsStore(tmp_path / "metrics.db"), interval_s=30, raw_retention_s=3600, rollup_retention_s=86400,
    )
    monkeypatch.setattr(metrics_history, "_collector", collector)
    return collector, headers, ids


@pytest.mark.asyncio
async def test_guests_come_in_one_call_and_show_up_in_latest(history_env, client):
    provider = _Bundled()
    collector, headers, ids = await history_env(provider, ["vm-a", "vm-b"])

    assert await collector.collect_once() == 0
    assert provider.many == [sorted(ids)]
    assert provider.calls == 0

    r = await client.get("/api/v1/hosts/metrics/latest", headers=headers)
    assert r.status_code == 200, r.text
    hosts = r.json()["hosts"]
    assert set(hosts) == set(ids)
    for host_id in ids:
        assert hosts[host_id]["cpu"] == 7.0
        assert hosts[host_id]["mem"] == 25.0
        assert hosts[host_id]["uptime_s"] == 600.0
        assert hosts[host_id]["disk"] is None
        assert hosts[host_id]["stale"] is False
    assert provider.many == [sorted(ids)], "die Übersicht selbst fragt den Anbieter nicht"
    reset_metrics_collector()


@pytest.mark.asyncio
async def test_failed_or_hanging_round_keeps_the_value_and_removed_hosts_drop_out(history_env, client, db_session):
    provider = _Bundled()
    collector, headers, ids = await history_env(provider, ["vm-a", "vm-b"])
    await collector.collect_once()
    first = collector.current(ids, int(time.time()), max_age_s=600)

    provider.fail = True
    await collector.collect_once()
    assert collector.current(ids, int(time.time()), max_age_s=600) == first

    provider.fail, provider.hang = False, True
    collector.sample_timeout_s = 0.05
    await collector.collect_once()
    assert collector.current(ids, int(time.time()), max_age_s=600) == first

    r = await client.delete(f"/api/v1/hosts/{ids[0]}", headers=headers)
    assert r.status_code in (200, 204), r.text
    await db_session.commit()
    await collector.collect_once()
    assert set(collector.current(ids, int(time.time()), max_age_s=600)) == {ids[1]}
    reset_metrics_collector()


@pytest.mark.asyncio
async def test_a_stopped_guest_loses_its_value_at_once(history_env):
    provider = _Bundled()
    collector, _, ids = await history_env(provider, ["vm-a", "vm-b"])
    await collector.collect_once()
    provider.stopped = {ids[0]}
    await collector.collect_once()
    assert set(collector.current(ids, int(time.time()), max_age_s=600)) == {ids[1]}
    reset_metrics_collector()


@pytest.mark.asyncio
async def test_without_sample_many_each_host_is_asked_and_a_failure_is_a_gap(history_env):
    collector, _, ids = await history_env(_Failing(), ["vm-a"])
    assert await collector.collect_once() == 0
    assert collector.current(ids, int(time.time()), max_age_s=600) == {}
    reset_metrics_collector()


@pytest.mark.asyncio
async def test_stored_sample_authorizes_only_its_host_provider_and_commands(history_env, monkeypatch):
    from unittest.mock import AsyncMock

    from nodvard_deck.core import gate, metrics_history
    from nodvard_sdk.errors import ActionBlocked

    class Provider(_SshLike):
        async def sample(self, host):
            gate.require_execution(host.id, ext_id="system")
            for host_id, ext_id, operation in (
                ("other-host", "system", "command"),
                (host.id, "other-provider", "command"),
                (host.id, "system", "shell"),
                (host.id, "system", "sftp"),
            ):
                with pytest.raises(ActionBlocked):
                    gate.require_execution(host_id, ext_id=ext_id, operation=operation)
            return await super().sample(host)

    provider = Provider()
    collector, _, ids = await history_env(provider, ["server-a", "server-b"])
    resolve = AsyncMock(wraps=metrics_history.resolve_metrics_provider)
    monkeypatch.setattr(metrics_history, "resolve_metrics_provider", resolve)

    assert await collector.collect_once() == 2
    assert provider.calls == 2
    assert resolve.await_count == 2
    assert set(collector.store.latest(ids, int(time.time()), max_age_s=600)) == set(ids)
    for host_id in ids:
        with pytest.raises(ActionBlocked):
            gate.require_execution(host_id, ext_id="system")


@pytest.mark.asyncio
async def test_stored_sample_respects_configured_timeout(history_env):
    cancelled = asyncio.Event()

    class Provider(_SshLike):
        async def sample(self, host):
            try:
                await asyncio.sleep(5)
                return await super().sample(host)
            finally:
                cancelled.set()

    collector, _, ids = await history_env(Provider(), ["server-a"])
    collector.sample_timeout_s = 0.05

    assert await collector.collect_once() == 0
    assert cancelled.is_set()
    assert collector.store.latest(ids, int(time.time()), max_age_s=600) == {}
