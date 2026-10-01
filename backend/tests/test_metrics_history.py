"""Metrik-Verlauf (core/metrics_history.py): eigener Speicher fuer Hosts ohne Historie
beim Anbieter, Verdichtung, Aufbewahrung, Sammler und API."""

from __future__ import annotations

import pytest

from nodvard_deck.core.metrics_history import ROLLUP_S, MetricsCollector, MetricsStore, reset_metrics_collector

T0 = 1_760_000_000 - 1_760_000_000 % ROLLUP_S  # auf ein 5-Minuten-Fenster ausgerichtet


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
async def test_provider_with_own_history_is_asked_directly_and_never_collected(client, db_session, monkeypatch, tmp_path):
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
    assert provider.calls == 0, "Anbieter mit eigener Historie wird nicht zusaetzlich abgefragt"
