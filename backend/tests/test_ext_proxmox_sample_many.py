"""Aktuelle Werte vieler Proxmox-Hosts mit einer Abfrage von `/cluster/resources` je Verbindung."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from nodvard_sdk.types import Host as SdkHost

EXT_SRC = Path(__file__).resolve().parents[2] / "extensions" / "proxmox" / "src"


@pytest.fixture(autouse=True)
def _ext_proxmox_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_SRC))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_proxmox"):
            del sys.modules[name]


class _Connection:
    def __init__(self, rows=None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls = 0

    async def cluster_resources(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.rows


def _host(host_id: str, ref: str) -> SdkHost:
    return SdkHost(
        id=host_id, name=host_id, display_name=host_id, address="", provider_ext_id="proxmox", provider_ref=ref,
    )


def _guest(kind: str, vmid: int, status: str, cpu: float, mem: float, maxmem: float, uptime: int, **extra) -> dict:
    return {"type": kind, "vmid": vmid, "node": "pve2", "status": status, "cpu": cpu, "mem": mem, "maxmem": maxmem,
            "uptime": uptime, **extra}


def _provider(monkeypatch, connections):
    from nodvard_deck_ext_proxmox import capabilities

    async def _build(ctx):
        return connections

    monkeypatch.setattr(capabilities, "build_connectors", _build)
    return capabilities.ProxmoxMetricsProvider(None)


@pytest.mark.asyncio
async def test_one_query_per_connection_and_empty_values_for_stopped_offline_or_missing(monkeypatch):
    lab = _Connection([
        {"type": "node", "node": "pve1", "status": "online", "cpu": 0.1, "mem": 4.0, "maxmem": 16.0, "uptime": 900},
        # Beide Gäste stehen auf pve2, ihr Bezug nennt noch pve1 (umgezogen).
        _guest("qemu", 100, "running", 0.25, 1.0, 2.0, 60),
        _guest("lxc", 101, "stopped", 0, 0, 512.0, 0, disk=0, maxdisk=8.0),
        {"type": "node", "node": "pve2", "status": "offline"},
        {"type": "storage", "node": "pve1", "storage": "local"},
    ])
    home = _Connection([_guest("lxc", 200, "running", 0.5, 3.0, 6.0, 30)])
    provider = _provider(monkeypatch, {"lab": lab, "home": home})
    hosts = [
        _host("n1", "lab/node/pve1"), _host("n2", "lab/node/pve2"), _host("vm", "lab/qemu/pve1/100"),
        _host("ct", "lab/lxc/pve1/101"), _host("other", "home/lxc/pve/200"), _host("gone", "lab/qemu/pve1/999"),
    ]

    got = await provider.sample_many(hosts)

    assert lab.calls == 1 and home.calls == 1
    assert got == {
        "n1": {"cpu_percent": 10.0, "mem_used_bytes": 4.0, "mem_total_bytes": 16.0, "uptime_s": 900.0},
        "vm": {"cpu_percent": 25.0, "mem_used_bytes": 1.0, "mem_total_bytes": 2.0, "uptime_s": 60.0},
        "other": {"cpu_percent": 50.0, "mem_used_bytes": 3.0, "mem_total_bytes": 6.0, "uptime_s": 30.0},
        "n2": {}, "ct": {}, "gone": {},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["qemu", "lxc"])
@pytest.mark.parametrize("unreachable", [False, True])
async def test_migration_and_discovery_clear_the_old_hosts_metrics(monkeypatch, db_session, tmp_path, kind, unreachable):
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from nodvard_deck.core import metrics_history
    from nodvard_deck.ext.context import HostsHandle, _PermissionChecker
    from nodvard_deck_ext_proxmox.capabilities import ProxmoxHostProvider
    from nodvard_deck_ext_proxmox.connector import ProxmoxApiError

    guest = _guest(kind, 100, "running", 0.25, 1.0, 2.0, 60)
    guest["node"] = "pve1"
    lab = _Connection([guest])
    lab.host = "192.0.2.1"
    lab.list_nodes = AsyncMock(return_value=[
        {"node": node, "status": "online"} for node in ("pve1", "pve2")
    ])

    async def guests(node):
        return [guest] if guest["node"] == node else []

    lab.list_qemu = guests if kind == "qemu" else AsyncMock(return_value=[])
    lab.list_lxc = guests if kind == "lxc" else AsyncMock(return_value=[])
    lab.qemu_agent_interfaces = AsyncMock(return_value=[])
    lab.lxc_interfaces = AsyncMock(return_value=[])
    lab.guest_config = AsyncMock(return_value={"ostype": "l26"})
    provider = _provider(monkeypatch, {"lab": lab})
    handle = HostsHandle(_PermissionChecker("proxmox", ["hosts.read", "hosts.write"]), "proxmox")
    discovery = ProxmoxHostProvider(SimpleNamespace(ext_id="proxmox", hosts=handle))
    collector = metrics_history.MetricsCollector(
        metrics_history.MetricsStore(tmp_path / "metrics.db"), interval_s=30,
        raw_retention_s=3600, rollup_retention_s=86400,
    )
    monkeypatch.setattr(metrics_history, "resolve_metrics_provider", AsyncMock(return_value=provider))

    await discovery.sync()
    old = next(h for h in await handle.list() if h.provider_ref == f"lab/{kind}/pve1/100")
    await collector.collect_once()
    assert collector.current([old.id], int(time.time()), max_age_s=600)[old.id]["cpu_percent"][1] == 25.0

    guest["node"] = "pve2"
    await discovery.sync()
    hosts = await handle.list()
    old = next(h for h in hosts if h.id == old.id)
    new = next(h for h in hosts if h.provider_ref == f"lab/{kind}/pve2/100")
    assert old.metadata["gone_since"]
    assert not new.metadata.get("gone_since")

    if unreachable:
        lab.error = ProxmoxApiError("Proxmox antwortet nicht")
    found = await provider.sample_many(hosts)
    assert found[old.id] == {}
    if not unreachable:
        assert found[new.id]["cpu_percent"] == 25.0
    await collector.collect_once()
    current = collector.current([old.id, new.id], int(time.time()), max_age_s=600)
    assert old.id not in current
    if not unreachable:
        assert current[new.id]["cpu_percent"][1] == 25.0
    await collector.stop()


@pytest.mark.asyncio
async def test_an_unreachable_connection_does_not_hold_up_the_others(monkeypatch):
    from nodvard_deck_ext_proxmox.connector import ProxmoxApiError

    down = _Connection(error=ProxmoxApiError("GET /cluster/resources -> Proxmox VE antwortet nicht"))
    up = _Connection([_guest("qemu", 300, "running", 0.02, 1.0, 1.0, 5)])
    provider = _provider(monkeypatch, {"down": down, "up": up})

    got = await provider.sample_many([
        _host("a", "down/qemu/pve/100"), _host("b", "up/qemu/pve/300"), _host("c", "removed/qemu/pve/1"),
        _host("d", ""), _host("e", "up/qemu"),
    ])

    assert set(got) == {"b"}
    assert down.calls == 1


@pytest.mark.asyncio
async def test_without_proxmox_hosts_no_connection_is_built(monkeypatch):
    from nodvard_deck_ext_proxmox import capabilities

    async def _never(ctx):
        raise AssertionError("ohne Proxmox-Bezug keine Verbindung")

    monkeypatch.setattr(capabilities, "build_connectors", _never)
    assert await capabilities.ProxmoxMetricsProvider(None).sample_many([_host("x", "")]) == {}


@pytest.mark.asyncio
async def test_disk_for_containers_and_nodes_but_never_for_vms(monkeypatch):
    lab = _Connection([
        {"type": "node", "node": "pve1", "status": "online", "cpu": 0.1, "mem": 4.0, "maxmem": 16.0, "uptime": 900,
         "disk": 30.0, "maxdisk": 100.0},
        _guest("lxc", 101, "running", 0.1, 1.0, 2.0, 60, disk=5.0, maxdisk=20.0),
        _guest("qemu", 102, "running", 0.1, 1.0, 2.0, 60, disk=0, maxdisk=50.0),
    ])
    got = await _provider(monkeypatch, {"lab": lab}).sample_many(
        [_host("n1", "lab/node/pve1"), _host("ct", "lab/lxc/pve1/101"), _host("vm", "lab/qemu/pve1/102")],
    )
    assert (got["n1"]["root_used_bytes"], got["n1"]["root_total_bytes"]) == (30.0, 100.0)
    assert (got["ct"]["root_used_bytes"], got["ct"]["root_total_bytes"]) == (5.0, 20.0)
    assert "root_used_bytes" not in got["vm"] and "root_total_bytes" not in got["vm"]


def test_root_filesystem_from_node_status_and_never_for_empty_or_stopped():
    from nodvard_deck_ext_proxmox.capabilities import _sample_values

    status = {"cpu": 0.05, "memory": {"used": 2.0, "total": 8.0}, "uptime": 10, "rootfs": {"used": 7.0, "total": 70.0}}
    assert _sample_values(status, "node")["root_used_bytes"] == 7.0
    assert "root_used_bytes" not in _sample_values({"cpu": 0.05, "disk": 0, "maxdisk": 0}, "lxc")
    assert "root_used_bytes" not in _sample_values({"status": "stopped", "disk": 0, "maxdisk": 8.0}, "lxc")


@pytest.mark.parametrize("disk", [{}, {"disk": 0}, {"maxdisk": 8.0}, {"disk": None, "maxdisk": 8.0}])
def test_missing_disk_values_never_become_zero(disk):
    from nodvard_deck_ext_proxmox.capabilities import _sample_values

    values = _sample_values({"status": "running", "cpu": 0.05, **disk}, "lxc")
    assert "root_used_bytes" not in values and "root_total_bytes" not in values


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [10**400, float("inf"), float("-inf"), float("nan"), -1],
                         ids=["overflow", "inf", "negative-inf", "nan", "negative"])
async def test_invalid_disk_values_only_cost_their_own_host(monkeypatch, value):
    lab = _Connection([
        _guest("lxc", 100, "running", 0.25, 1.0, 2.0, 60, disk=value, maxdisk=20.0),
        _guest("lxc", 101, "running", 0.5, 1.0, 2.0, 60, disk=5.0, maxdisk=20.0),
        _guest("qemu", 102, "running", 0.1, 1.0, 2.0, 60, disk=value, maxdisk=20.0),
    ])
    got = await _provider(monkeypatch, {"lab": lab}).sample_many([
        _host("bad", "lab/lxc/pve1/100"), _host("good", "lab/lxc/pve1/101"),
        _host("vm", "lab/qemu/pve1/102"),
    ])

    assert got["bad"] == {}
    assert got["good"] == {
        "cpu_percent": 50.0, "mem_used_bytes": 1.0, "mem_total_bytes": 2.0, "uptime_s": 60.0,
        "root_used_bytes": 5.0, "root_total_bytes": 20.0,
    }
    assert got["vm"] == {"cpu_percent": 10.0, "mem_used_bytes": 1.0, "mem_total_bytes": 2.0, "uptime_s": 60.0}
    assert lab.calls == 1


@pytest.mark.asyncio
async def test_only_gone_hosts_clear_values_without_building_connections(monkeypatch):
    from nodvard_deck_ext_proxmox import capabilities

    async def _never(ctx):
        raise AssertionError("für verschwundene Hosts keine Verbindung")

    monkeypatch.setattr(capabilities, "build_connectors", _never)
    host = _host("gone", "lab/qemu/pve1/100")
    host.metadata["gone_since"] = "2026-11-02T12:00:00+00:00"
    assert await capabilities.ProxmoxMetricsProvider(None).sample_many([host]) == {"gone": {}}


class _Waiting(_Connection):
    """Antwortet erst, wenn `gate` gesetzt ist; `opens` setzt es beim eigenen Aufruf."""

    def __init__(self, rows=None, *, gate=None, opens=None) -> None:
        super().__init__(rows)
        self.gate, self.opens = gate, opens

    async def cluster_resources(self):
        self.calls += 1
        if self.opens is not None:
            self.opens.set()
        if self.gate is not None:
            await self.gate.wait()
        return self.rows


@pytest.mark.asyncio
async def test_a_hanging_connection_does_not_cost_the_others_their_values(monkeypatch, caplog):
    import asyncio

    from nodvard_deck_ext_proxmox import capabilities

    monkeypatch.setattr(capabilities, "SAMPLE_MANY_TIMEOUT_S", 0.05)
    hanging = _Waiting(gate=asyncio.Event())
    up = _Connection([_guest("qemu", 300, "running", 0.02, 1.0, 1.0, 5)])
    provider = _provider(monkeypatch, {"hanging": hanging, "up": up})

    with caplog.at_level("INFO", logger="nodvard_deck.ext.proxmox"):
        got = await asyncio.wait_for(
            provider.sample_many([_host("a", "hanging/qemu/pve/100"), _host("b", "up/qemu/pve/300")]), timeout=5,
        )

    assert set(got) == {"b"} and got["b"]["cpu_percent"] == 2.0
    assert "Verbindung 'hanging' nicht lesbar: TimeoutError" in caplog.text


@pytest.mark.asyncio
async def test_connections_are_asked_side_by_side(monkeypatch):
    import asyncio

    asked_second = asyncio.Event()
    first = _Waiting([_guest("qemu", 100, "running", 0.1, 1.0, 2.0, 1)], gate=asked_second)
    second = _Waiting([_guest("lxc", 200, "running", 0.2, 1.0, 2.0, 1)], opens=asked_second)
    provider = _provider(monkeypatch, {"first": first, "second": second})

    got = await asyncio.wait_for(
        provider.sample_many([_host("a", "first/qemu/pve/100"), _host("b", "second/lxc/pve/200")]), timeout=5,
    )

    assert set(got) == {"a", "b"}


def test_the_timeout_per_connection_stays_below_the_collectors_shortest(tmp_path):
    from nodvard_deck.core.metrics_history import MetricsCollector, MetricsStore
    from nodvard_deck_ext_proxmox import capabilities

    collector = MetricsCollector(MetricsStore(tmp_path / "m.db"), interval_s=1, raw_retention_s=60, rollup_retention_s=60)
    assert capabilities.SAMPLE_MANY_TIMEOUT_S < collector.sample_timeout_s


@pytest.mark.asyncio
async def test_broken_rows_only_cost_their_own_host(monkeypatch):
    lab = _Connection([
        "kaputt", 42, None,
        _guest("qemu", 100, "running", "viel", 1.0, 2.0, 60),
        _guest("qemu", 101, "running", 0.5, 1.0, 2.0, 60),
    ])
    weird = _Connection(7)
    provider = _provider(monkeypatch, {"lab": lab, "weird": weird})

    got = await provider.sample_many([
        _host("bad", "lab/qemu/pve/100"), _host("good", "lab/qemu/pve/101"), _host("other", "weird/qemu/pve/1"),
    ])

    assert got == {"bad": {}, "good": {"cpu_percent": 50.0, "mem_used_bytes": 1.0, "mem_total_bytes": 2.0,
                                       "uptime_s": 60.0}}


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["cpu", "mem", "maxmem", "uptime"])
@pytest.mark.parametrize("value", [10**400, float("inf"), float("-inf"), float("nan"), -1],
                         ids=["overflow", "inf", "negative-inf", "nan", "negative"])
async def test_invalid_numbers_only_cost_their_own_host(monkeypatch, field, value):
    broken = _guest("qemu", 100, "running", 0.25, 1.0, 2.0, 60)
    broken[field] = value
    lab = _Connection([broken, _guest("lxc", 101, "running", 0.5, 1.0, 2.0, 60)])
    home = _Connection([_guest("qemu", 200, "running", 0.1, 3.0, 4.0, 30)])
    provider = _provider(monkeypatch, {"lab": lab, "home": home})

    got = await provider.sample_many([
        _host("bad", "lab/qemu/pve/100"), _host("good", "lab/lxc/pve/101"), _host("other", "home/qemu/pve/200"),
    ])

    assert got == {
        "bad": {},
        "good": {"cpu_percent": 50.0, "mem_used_bytes": 1.0, "mem_total_bytes": 2.0, "uptime_s": 60.0},
        "other": {"cpu_percent": 10.0, "mem_used_bytes": 3.0, "mem_total_bytes": 4.0, "uptime_s": 30.0},
    }
    assert lab.calls == home.calls == 1
