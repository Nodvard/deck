from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from nodvard_deck.core import maintenance
from nodvard_deck.core.metrics_history import MetricsStore
from nodvard_deck.models import (
    Action,
    AuditEntry,
    CustomApp,
    Host,
    HostCredential,
    HostGroup,
    HostTag,
    Job,
    JobRun,
    KnownHostKey,
    Secret,
    SecretUsage,
    Setting,
    host_group_members,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from test_dashboard_groups_migration import _keep_logging_setup  # noqa: F401
from test_dashboard_groups_migration import migrated as migrated  # noqa: PLC0414
from window_helpers import window

PREVIOUS = "6d9e2a4f8b31"
REVISION = "c8e42a91d6b3"


def _rows(path):
    with sqlite3.connect(path) as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {
            name: sorted(db.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)
            for name in tables if name not in ("alembic_version", "host_merges")
        }


def _seed(path, node_address, legacy, *, conflicting_keys=False):
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as db:
        db.exec_driver_sql("PRAGMA foreign_keys = ON")
        for id_, address, kind, metadata in (
            ("node", "192.0.2.10", "hypervisor", {"node": "pve1"}),
            ("vm", "192.0.2.20", "vm", {"address_source": "guest-agent", "power_state": "running"}),
            ("lxc", "2001:db8::30", "lxc", {"address_source": "container"}),
        ):
            if legacy:
                metadata.pop("address_source", None)
            db.execute(Host.__table__.insert().values(
                id=id_, name=id_, display_name=id_, address=address, kind=kind,
                provider_ext_id="proxmox", provider_ref=f"primary/{id_}", status="up", metadata=metadata,
            ))
            manual_address = {"node": node_address, "lxc": "2001:0db8:0:0:0:0:0:30"}.get(id_, address)
            db.execute(Host.__table__.insert().values(
                id=f"hand-{id_}", name=f"hand-{id_}", display_name=f"Hand {id_}", address=manual_address,
                metadata={"sudo_full_wanted": True},
            ))
        db.execute(Host.__table__.insert().values(
            id="placeholder", name="placeholder", address="192.0.2.10", kind="vm", display_name="unbekannt",
            provider_ext_id="proxmox", provider_ref="primary/placeholder", metadata={"address_source": "placeholder"},
        ))
        for host_id in ("vm", "hand-vm"):
            db.execute(HostTag.__table__.insert().values(host_id=host_id, tag="gemeinsam"))
            db.execute(HostTag.__table__.insert().values(
                host_id=host_id, tag=host_id, managed_by_ext_id="proxmox" if host_id == "vm" else None,
            ))
            db.execute(Secret.__table__.insert().values(
                id=f"secret-{host_id}", label=host_id, kind="ssh_password", ciphertext=b"verschluesselt",
            ))
            db.execute(HostCredential.__table__.insert().values(
                id=f"cred-{host_id}", host_id=host_id, kind="ssh_password", secret_id=f"secret-{host_id}",
            ))
            db.execute(KnownHostKey.__table__.insert().values(
                id=f"key-{host_id}", host_id=host_id, key_type="ssh-ed25519",
                fingerprint=f"SHA256:{host_id}" if conflicting_keys else "SHA256:gemeinsam",
            ))
        db.execute(HostGroup.__table__.insert().values(id="g1", name="Gruppe"))
        db.execute(host_group_members.insert(), [{"group_id": "g1", "host_id": id_} for id_ in ("vm", "hand-vm")])
        db.execute(Action.__table__.insert().values(
            id="a1", host_id="vm", ext_id="proxmox", action_type="start", status="succeeded",
            proposed_by_type="user", proposed_by_id="u1", reason="Test",
        ))
        db.execute(JobRun.__table__.insert().values(id="r1", host_id="vm", trigger="manual", status="succeeded"))
        db.execute(Job.__table__.insert().values(
            id="j1", name="Prüfung", kind="test", schedule="0 * * * *", target={"host_id": "vm"},
        ))
        db.execute(CustomApp.__table__.insert().values(id="app1", name="App", url="https://example.test", host_id="vm"))
        db.execute(AuditEntry.__table__.insert().values(
            id="audit1", actor_type="user", actor_id="u1", action="host.updated", outcome="success",
            target_type="host", target_id="vm",
        ))
        db.execute(Setting.__table__.insert().values(
            key="ssh.host_key_confirm_required", scope="host", user_id="vm", value=True,
        ))
        db.execute(SecretUsage.__table__.insert().values(
            secret_id="secret-vm", consumer_type="host", consumer_id="vm",
        ))
    engine.dispose()


@pytest.mark.parametrize(("node_address", "legacy"), [("PVE1.", False), ("192.0.2.10", True)])
def test_upgrade_and_downgrade_preserve_assignments_and_metrics(migrated, node_address, legacy):
    cfg, path = migrated
    command.upgrade(cfg, PREVIOUS)
    _seed(path, node_address, legacy)
    before = _rows(path)
    store = MetricsStore(path.with_name("metrics.db"))
    store.insert("hand-vm", 300, {"cpu_percent": 12.0})
    store.maintain(600, raw_retention_s=3600, rollup_retention_s=86400)
    metrics_before = _rows(path.with_name("metrics.db"))
    store.close()

    command.upgrade(cfg, REVISION)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT id FROM hosts ORDER BY id").fetchall() == [
            ("hand-lxc",), ("hand-node",), ("hand-vm",), ("placeholder",),
        ]
        host = db.execute("SELECT provider_ext_id, provider_ref, status, metadata FROM hosts WHERE id = 'hand-vm'").fetchone()
        assert host[:3] == ("proxmox", "primary/vm", "up")
        assert json.loads(host[3])["manual_entry"]["name"] == "hand-vm"
        assert json.loads(host[3])["sudo_full_wanted"] is True
        assert db.execute("SELECT tag FROM host_tags WHERE host_id = 'hand-vm' ORDER BY tag").fetchall() == [
            ("gemeinsam",), ("hand-vm",), ("vm",),
        ]
        assert db.execute("SELECT id, host_id, is_default FROM host_credentials ORDER BY id").fetchall() == [
            ("cred-hand-vm", "hand-vm", 1), ("cred-vm", "hand-vm", 0),
        ]
        for table in ("known_hosts", "actions", "job_runs", "custom_apps", "host_group_members"):
            assert set(db.execute(f"SELECT host_id FROM {table}").fetchall()) == {("hand-vm",)}
        assert db.execute("SELECT id, fingerprint FROM known_hosts").fetchall() == [("key-hand-vm", "SHA256:gemeinsam")]
        assert json.loads(db.execute("SELECT target FROM jobs").fetchone()[0]) == {"host_id": "hand-vm"}
        assert db.execute("SELECT user_id FROM settings WHERE scope = 'host'").fetchall() == [("hand-vm",)]
        assert db.execute("SELECT consumer_id FROM secret_usages").fetchall() == [("hand-vm",)]
        assert db.execute("SELECT target_id FROM audit_log").fetchall() == [("vm",)]
        assert db.execute("SELECT count(*) FROM host_merges").fetchone() == (3,)
    after = _rows(path)
    assert _rows(path.with_name("metrics.db")) == metrics_before
    assert after["secrets"] == before["secrets"]
    command.upgrade(cfg, REVISION)
    assert _rows(path) == after

    command.downgrade(cfg, PREVIOUS)
    assert _rows(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    command.upgrade(cfg, REVISION)
    assert _rows(path) == after
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision(REVISION).down_revision == PREVIOUS
    assert PREVIOUS not in script.get_heads()


def test_upgrade_keeps_conflicting_keys_separate(migrated):
    cfg, path = migrated
    command.upgrade(cfg, PREVIOUS)
    _seed(path, "192.0.2.10", False, conflicting_keys=True)
    before = _rows(path)
    command.upgrade(cfg, REVISION)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id FROM hosts WHERE id IN ('vm', 'hand-vm') ORDER BY id").fetchall() == [
            ("hand-vm",), ("vm",),
        ]
        assert db.execute("SELECT host_id, fingerprint FROM known_hosts ORDER BY host_id").fetchall() == [
            ("hand-vm", "SHA256:hand-vm"), ("vm", "SHA256:vm"),
        ]
        assert db.execute("SELECT count(*) FROM host_merges WHERE source_id = 'vm'").fetchone() == (0,)
    command.downgrade(cfg, PREVIOUS)
    assert _rows(path) == before


def test_upgrade_defers_forgotten_key_until_confirmation(migrated):
    cfg, path = migrated
    command.upgrade(cfg, PREVIOUS)
    _seed(path, "192.0.2.10", False)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM known_hosts WHERE host_id = 'hand-vm'")
        db.execute(
            "INSERT INTO settings (key, scope, user_id, value) VALUES (?, 'host', 'hand-vm', ?)",
            ("ssh.host_key_confirm_required", json.dumps({"value": True})),
        )
    before = _rows(path)
    command.upgrade(cfg, REVISION)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT provider_ext_id FROM hosts WHERE id = 'hand-vm'").fetchone() == (None,)
        assert db.execute("SELECT host_id, fingerprint FROM known_hosts").fetchall() == [("vm", "SHA256:gemeinsam")]
        assert db.execute("SELECT count(*) FROM host_merges WHERE source_id = 'vm'").fetchone() == (0,)
        assert db.execute(
            "SELECT user_id FROM settings WHERE key = 'ssh.host_key_confirm_required' ORDER BY user_id",
        ).fetchall() == [("hand-vm",), ("vm",)]
    command.downgrade(cfg, PREVIOUS)
    assert _rows(path) == before


def test_upgrade_keeps_selected_maintenance_hosts_and_downgrade_restores_them(migrated, window_clock):
    cfg, path = migrated
    command.upgrade(cfg, PREVIOUS)
    _seed(path, "192.0.2.10", False)
    windows = [window(["vm", "lxc"])]
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO settings (key, scope, user_id, value) VALUES ('maintenance.windows', 'global', '', ?)",
            (json.dumps({"value": windows}),),
        )
    before = _rows(path)
    command.upgrade(cfg, REVISION)

    async def check_windows():
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        try:
            async with AsyncSession(engine) as session:
                assert await maintenance.is_in_window(session, host_id="hand-vm")
                assert await maintenance.is_in_window(session, host_id="hand-lxc")
                assert await maintenance.is_in_window(session, host_id=None, host_ids=["hand-vm", "hand-lxc"])
                assert not await maintenance.is_in_window(session, host_id="hand-node")
        finally:
            await engine.dispose()

    asyncio.run(check_windows())
    command.downgrade(cfg, PREVIOUS)
    assert _rows(path) == before


def test_upgrade_does_not_merge_gone_guests(migrated):
    cfg, path = migrated
    command.upgrade(cfg, PREVIOUS)
    _seed(path, "192.0.2.10", False)
    with sqlite3.connect(path) as db:
        metadata = json.loads(db.execute("SELECT metadata FROM hosts WHERE id = 'vm'").fetchone()[0])
        metadata["gone_since"] = "2026-11-01T12:00:00+00:00"
        db.execute("UPDATE hosts SET metadata = ? WHERE id = 'vm'", (json.dumps(metadata),))
    before = _rows(path)
    command.upgrade(cfg, REVISION)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT provider_ext_id, metadata FROM hosts WHERE id = 'hand-vm'").fetchone() == (
            None, json.dumps({"sudo_full_wanted": True}),
        )
        assert db.execute("SELECT count(*) FROM hosts WHERE id = 'vm'").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM host_merges WHERE source_id = 'vm'").fetchone() == (0,)
    command.downgrade(cfg, PREVIOUS)
    assert _rows(path) == before
