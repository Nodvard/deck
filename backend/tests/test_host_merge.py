from __future__ import annotations

import time

import pytest
from nodvard_deck.api.v1.hosts import (
    HostOut,
    get_host_metrics,
    get_host_metrics_history,
    get_hosts_latest_metrics,
)
from nodvard_deck.core import maintenance, metrics_history, ssh
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import Action, AuditEntry, HostMerge, KnownHostKey
from nodvard_deck.services import audit, hosts, settings
from nodvard_deck.services.host_merge import merge_discovered
from nodvard_sdk import DiscoveredHost, HostStatus
from nodvard_sdk.capabilities import MetricsProvider
from sqlalchemy import select
from test_ext_context import _build
from window_helpers import window

pytestmark = pytest.mark.asyncio


def _discovered(address="192.0.2.20", *, kind="vm", identities=None, ref="node/100"):
    return DiscoveredHost(
        name=f"guest-{ref.replace('/', '-')}", display_name="Gast", address=address, address_verified=True,
        provider_ref=ref, kind=kind, status=HostStatus.UP, tags=["virtual"], power_state="running",
        metadata={"identity_addresses": identities if identities is not None else [address]},
    )


@pytest.mark.parametrize(("kind", "address", "identity"), [
    ("hypervisor", "192.0.2.20", "192.0.2.20"),
    ("vm", "DB.example.test.", "db.EXAMPLE.test"),
    ("lxc", "2001:0db8:0:0:0:0:0:20", "2001:db8::20"),
])
async def test_discovery_keeps_manual_id_access_tags_and_address(db_session, test_settings, tmp_path, kind, address, identity):
    host = await hosts.create_host(db_session, name="von-hand", display_name="Datenbank", address=address, tags=["wichtig"])
    credential = await hosts.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password", username="admin", port=2222, secret_value="test-password",
    )
    hosts.record_login_ok(host, credential)
    host_id, credential_id = host.id, credential.id
    await db_session.commit()
    _, _, ctx = _build(tmp_path, permissions=["hosts.write", "hosts.read"])
    dh = _discovered(kind=kind, identities=[identity])
    result = await ctx.hosts.upsert_discovered([dh])
    assert [row.id for row in result] == [host_id]
    assert result[0].provider_ext_id == "test-ext"
    assert result[0].has_credential
    assert result[0].credential_username == "admin" and result[0].credential_port == 2222
    assert set(result[0].tags) == {"wichtig", "virtual"}
    assert result[0].address == address and result[0].display_name == "Datenbank"

    await db_session.refresh(host)
    response = HostOut.from_model(host)
    assert response.manual_entry.name == "von-hand"
    assert response.status == "up" and response.power_state == "running"
    assert response.credential.id == credential_id and response.login_ok_at is not None
    dh.status = HostStatus.DOWN
    dh.power_state = "stopped"
    dh.address = "192.0.2.99"
    again = await ctx.hosts.upsert_discovered([dh])
    assert again[0].id == host_id and again[0].status == HostStatus.DOWN
    assert again[0].address == address and again[0].has_credential
    assert again[0].metadata["manual_entry"]["name"] == "von-hand"
    assert len(await ctx.hosts.list()) == 1
    assert len((await db_session.execute(select(HostMerge))).scalars().all()) == 1


async def test_manual_host_added_after_discovery_inherits_existing_assignments(db_session, test_settings, tmp_path):
    _, _, ctx = _build(tmp_path, permissions=["hosts.write", "hosts.read"])
    source = (await ctx.hosts.upsert_discovered([_discovered()]))[0]
    credential = await hosts.add_credential(
        db_session, test_settings, host_id=source.id, kind="ssh_password", username="admin", port=22, secret_value="test-password",
    )
    await audit.log(
        db_session, actor_type="user", actor_id="u1", action="host.updated", outcome="success",
        target_type="host", target_id=source.id,
    )
    credential_id = credential.id
    await db_session.commit()
    host = await hosts.create_host(db_session, name="von-hand", address="192.0.2.20", tags=["wichtig"])
    assert host.provider_ref == "node/100"
    assert {tag.tag for tag in host.tags} == {"wichtig", "virtual"}
    assert host.credentials[0].id == credential_id and host.credentials[0].is_default
    assert len(await hosts.list_hosts(db_session)) == 1
    entries = await audit.list_entries(db_session, target_type="host", target_id=host.id)
    assert len(entries) == 1 and entries[0].target_id == source.id
    assert (await db_session.execute(select(AuditEntry.target_id))).scalar_one() == source.id
    merge = await db_session.get(HostMerge, source.id)
    assert merge.host_id == host.id
    assert merge.snapshot["source"]["name"] == source.name


async def test_ambiguous_addresses_and_unverified_placeholders_stay_separate(db_session, tmp_path):
    manual = await hosts.create_host(db_session, name="von-hand", address="192.0.2.20")
    manual_id = manual.id
    await db_session.commit()
    _, _, ctx = _build(tmp_path, permissions=["hosts.write", "hosts.read"])
    await ctx.hosts.upsert_discovered([
        _discovered(ref="node/100"), _discovered(ref="node/101"),
        _discovered(ref="node/102", identities=["unbekannter-gast"]),
    ])
    listed = await ctx.hosts.list()
    assert len(listed) == 4
    assert next(row for row in listed if row.id == manual_id).provider_ext_id is None


async def test_gone_guest_does_not_claim_reused_address(db_session, tmp_path):
    _, _, ctx = _build(tmp_path, permissions=["hosts.write", "hosts.read"])
    source = (await ctx.hosts.upsert_discovered([_discovered()]))[0]
    assert await ctx.hosts.mark_gone([source.provider_ref]) == 1
    host = await hosts.create_host(db_session, name="neu", address=source.address)
    host_id = host.id
    await db_session.commit()
    await ctx.hosts.upsert_discovered([_discovered(address="192.0.2.99", ref="node/101")])
    await db_session.refresh(host)
    assert host.provider_ext_id is None and host.provider_ref is None
    assert HostOut.from_model(host).gone_since is None
    listed = await ctx.hosts.list()
    assert len(listed) == 3
    assert next(row for row in listed if row.id == source.id).metadata["gone_since"]
    assert (await db_session.execute(select(HostMerge).where(HostMerge.host_id == host_id))).first() is None


@pytest.mark.parametrize("duplicate", [False, True])
async def test_merged_key_is_fully_forgotten_before_connecting(
    db_session, tmp_path, ssh_server_factory, ssh_server_stats, duplicate,
):
    address, port, username, password, fingerprints = await ssh_server_factory()
    key_type, fingerprint = next(iter(fingerprints.items()))
    _, _, ctx = _build(tmp_path, permissions=["hosts.write", "hosts.read"])
    source = (await ctx.hosts.upsert_discovered([_discovered(address=address)]))[0]
    manual = await hosts.create_host(db_session, name="von-hand", address="192.0.2.99")
    host_id = manual.id
    for id_ in (source.id, host_id):
        db_session.add(KnownHostKey(host_id=id_, key_type=key_type, fingerprint=fingerprint))
    manual.address = address
    await db_session.flush()
    assert await merge_discovered(db_session) == {source.id: host_id}
    keys = (await db_session.execute(select(KnownHostKey).where(KnownHostKey.host_id == host_id))).scalars().all()
    assert len(keys) == 1 and keys[0].fingerprint == fingerprint
    if duplicate:
        db_session.add(KnownHostKey(host_id=host_id, key_type=key_type, fingerprint=fingerprint))
        await db_session.flush()
    assert await hosts.clear_known_host_key(db_session, host_id, key_type)
    await db_session.commit()
    target = ssh.ConnectionTarget(
        host_id=host_id, address=address, port=port, username=username,
        kind="ssh_password", secret_value=password, allow_tofu=False,
    )
    with pytest.raises(ssh.HostKeyUnknown):
        conn = await ssh.connect(db_session, target)
        conn.close()
        await conn.wait_closed()
    assert await ssh.load_known_fingerprints(db_session, host_id) == {}
    assert await ssh.host_key_confirm_pending(db_session, host_id)
    assert ssh_server_stats["begin_auth"] == 0
    assert ssh_server_stats["password_tries"] == 0


@pytest.mark.parametrize("allow_tofu", [False, True])
async def test_forgotten_key_defers_merge_until_confirmation(
    db_session, tmp_path, ssh_server_factory, ssh_server_stats, allow_tofu,
):
    address, port, username, password, fingerprints = await ssh_server_factory()
    key_type, fingerprint = next(iter(fingerprints.items()))
    _, _, ctx = _build(tmp_path, permissions=["hosts.write", "hosts.read"])
    source = (await ctx.hosts.upsert_discovered([_discovered(address=address)]))[0]
    manual = await hosts.create_host(db_session, name="von-hand", address="192.0.2.99")
    host_id = manual.id
    for id_ in (source.id, host_id):
        db_session.add(KnownHostKey(host_id=id_, key_type=key_type, fingerprint=fingerprint))
    await db_session.flush()
    assert await hosts.clear_known_host_key(db_session, host_id, key_type)
    manual.address = address
    await db_session.commit()

    assert await merge_discovered(db_session) == {}
    await db_session.commit()
    target = ssh.ConnectionTarget(
        host_id=host_id, address=address, port=port, username=username,
        kind="ssh_password", secret_value=password, allow_tofu=allow_tofu,
    )
    with pytest.raises(ssh.HostKeyUnknown):
        conn = await ssh.connect(db_session, target)
        conn.close()
        await conn.wait_closed()
    assert await ssh.load_known_fingerprints(db_session, host_id) == {}
    assert await ssh.host_key_confirm_pending(db_session, host_id)
    assert ssh_server_stats["begin_auth"] == 0
    assert ssh_server_stats["password_tries"] == 0

    db_session.add(KnownHostKey(host_id=host_id, key_type=key_type, fingerprint=fingerprint))
    await hosts.host_key_was_confirmed(db_session, host_id)
    assert await merge_discovered(db_session) == {source.id: host_id}
    assert len(await hosts.list_hosts(db_session)) == 1
    assert await ssh.load_known_fingerprints(db_session, host_id) == fingerprints


async def test_merge_keeps_selected_maintenance_hosts(db_session, tmp_path, window_clock):
    _, _, ctx = _build(tmp_path, permissions=["hosts.write", "hosts.read"])
    source = (await ctx.hosts.upsert_discovered([_discovered()]))[0]
    manual = await hosts.create_host(db_session, name="von-hand", address="192.0.2.99")
    host_id = manual.id
    windows = [window([source.id]), window(["other"], minutes_ago=120)]
    await settings.set_global(db_session, "maintenance.windows", windows)
    assert await maintenance.is_in_window(db_session, host_id=source.id)
    assert not await maintenance.is_in_window(db_session, host_id=host_id)

    manual.address = source.address
    await db_session.flush()
    assert await merge_discovered(db_session) == {source.id: host_id}
    assert await maintenance.is_in_window(db_session, host_id=host_id)
    assert await maintenance.is_in_window(db_session, host_id=source.id)
    assert await maintenance.is_in_window(db_session, host_id=None, host_ids=[source.id, host_id])
    assert not await maintenance.is_in_window(db_session, host_id="other")
    assert not await maintenance.is_in_window(db_session, host_id=None, host_ids=[host_id, "other"])
    assert await settings.get_global(db_session, "maintenance.windows") == windows


async def test_conflicting_keys_defer_merge(db_session, tmp_path):
    _, _, ctx = _build(tmp_path, permissions=["hosts.write", "hosts.read"])
    source = (await ctx.hosts.upsert_discovered([_discovered()]))[0]
    manual = await hosts.create_host(db_session, name="von-hand", address="192.0.2.99")
    for host_id in (source.id, manual.id):
        db_session.add(KnownHostKey(host_id=host_id, key_type="ssh-ed25519", fingerprint=f"SHA256:{host_id}"))
    manual.address = source.address
    await db_session.flush()
    assert await merge_discovered(db_session) == {}
    assert len(await hosts.list_hosts(db_session)) == 2
    assert len((await db_session.execute(select(KnownHostKey))).scalars().all()) == 2
    assert (await db_session.execute(select(HostMerge))).first() is None


@pytest.mark.parametrize("manual_keys", [[], [("ssh-rsa", "SHA256:eigen")]])
async def test_unknown_source_key_defers_merge(db_session, tmp_path, manual_keys):
    _, _, ctx = _build(tmp_path, permissions=["hosts.write", "hosts.read"])
    source = (await ctx.hosts.upsert_discovered([_discovered()]))[0]
    manual = await hosts.create_host(db_session, name="von-hand", address="192.0.2.99")
    db_session.add(KnownHostKey(host_id=source.id, key_type="ssh-ed25519", fingerprint="SHA256:fremd"))
    for key_type, fingerprint in manual_keys:
        db_session.add(KnownHostKey(host_id=manual.id, key_type=key_type, fingerprint=fingerprint))
    manual.address = source.address
    await db_session.flush()
    assert await merge_discovered(db_session) == {}
    assert len(await hosts.list_hosts(db_session)) == 2
    assert (await db_session.execute(select(HostMerge))).first() is None


async def test_active_action_defers_the_merge(db_session, tmp_path):
    manual = await hosts.create_host(db_session, name="von-hand", address="192.0.2.20")
    action = Action(
        host_id=manual.id, ext_id="test-ext", action_type="check", status="executing",
        proposed_by_type="user", proposed_by_id="u1", reason="Prüfung",
    )
    db_session.add(action)
    await db_session.commit()
    _, _, ctx = _build(tmp_path, permissions=["hosts.write", "hosts.read"])
    await ctx.hosts.upsert_discovered([_discovered()])
    assert len(await ctx.hosts.list()) == 2
    action.status = "succeeded"
    await db_session.commit()
    assert await merge_discovered(db_session)
    assert len(await hosts.list_hosts(db_session)) == 1


class _SshMetrics:
    async def supports(self, host):
        return host.has_credential

    async def metric_names(self):
        return ["cpu_percent"]

    async def sample(self, host):
        return {"cpu_percent": 12.0}


class _ProviderMetrics:
    async def metric_names(self):
        return ["cpu_percent"]

    async def sample(self, host):
        return {"cpu_percent": 99.0}


async def test_linked_host_shows_provider_state_with_ssh_metrics_and_existing_history(
    db_session, test_settings, tmp_path, monkeypatch,
):
    host = await hosts.create_host(db_session, name="von-hand", address="192.0.2.20")
    await hosts.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password", username="admin", port=22, secret_value="test-password",
    )
    host_id = host.id
    await db_session.commit()
    runtime = get_extension_runtime()
    ssh, provider = _SshMetrics(), _ProviderMetrics()
    runtime.capabilities.provide("system", MetricsProvider, ssh)
    runtime.capabilities.provide("test-ext", MetricsProvider, provider)
    _, _, ctx = _build(tmp_path, permissions=["hosts.write", "hosts.read"])
    await ctx.hosts.upsert_discovered([_discovered()])
    await db_session.refresh(host)
    assert HostOut.from_model(host).power_state == "running"
    assert (await get_host_metrics(host_id, db_session)).values == {"cpu_percent": 12.0}

    collector = metrics_history.MetricsCollector(
        metrics_history.MetricsStore(tmp_path / "metrics.db"), interval_s=30, raw_retention_s=3600, rollup_retention_s=86400,
    )
    monkeypatch.setattr(metrics_history, "_collector", collector)
    now = int(time.time()) // 30 * 30
    collector.store.insert(host_id, now - 30, {"cpu_percent": 7.0})
    source_id = (await db_session.execute(select(HostMerge.source_id))).scalar_one()
    collector.store.insert(source_id, now - 60, {"cpu_percent": 5.0, "root_used_bytes": 20.0, "root_total_bytes": 100.0})
    assert await collector.collect_once() == 1
    history = await get_host_metrics_history(host_id, db_session)
    assert history.source == "lattice"
    assert 7.0 in history.series["cpu_percent"]
    assert 5.0 in history.series["cpu_percent"]
    assert 12.0 in history.series["cpu_percent"]
    latest = await get_hosts_latest_metrics(db_session)
    assert set(latest.hosts) == {host_id}
    assert latest.hosts[host_id].disk == 20.0
    sdk_host = hosts.host_to_sdk(host)
    sdk_host.has_credential = False
    assert await metrics_history.resolve_metrics_provider(sdk_host, "test-ext") is provider
    collector.store.close()
