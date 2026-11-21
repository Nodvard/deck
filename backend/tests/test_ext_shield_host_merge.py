from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nodvard_deck.db.base import utcnow
from nodvard_deck.db.host_merge_v1 import merge_matches, restore_merges
from nodvard_deck.models import Host
from nodvard_deck.services import hosts
from nodvard_sdk import DiscoveredHost
from sqlalchemy import select
from test_ext_context import _build

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "shield"


@pytest.fixture
def shield(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    from nodvard_deck_ext_shield import antivirus, defender, guard, intrusion, models, patching

    yield SimpleNamespace(
        antivirus=antivirus, defender=defender, guard=guard, intrusion=intrusion, models=models, patching=patching,
    )
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


@pytest.mark.asyncio
async def test_merge_keeps_file_baseline_and_reports_changes(db_session, tmp_path, shield):
    connection = await db_session.connection()
    await connection.run_sync(shield.models.Base.metadata.create_all)
    await db_session.commit()
    _, _, discovery = _build(tmp_path, permissions=["hosts.read", "hosts.write"])
    source = (await discovery.hosts.upsert_discovered([DiscoveredHost(
        name="pve", address="192.0.2.20", provider_ref="node/100",
        metadata={"identity_addresses": ["192.0.2.20"]},
    )]))[0]
    manifest = tomllib.loads((EXT_DIR / "extension.toml").read_text(encoding="utf-8"))["extension"]
    _, _, ctx = _build(
        tmp_path / "shield", ext_id=manifest["id"], legacy_ids=manifest["legacy_ids"], permissions=["audit.write"],
    )
    accepted = {"/etc/passwd": {"sha256": "a" * 64, "mode": "644", "owner": "root"}}
    await shield.patching.save_baseline(ctx, source.id, "files", {"accepted": accepted, "alerted": {}})

    host = await hosts.create_host(db_session, name="von-hand", address=source.address)
    await db_session.commit()
    snap = shield.intrusion.parse_guard(
        "@@uid\n0\n@@ssh\n\n@@f2b\nnone\n@@ports\n\n@@files\n"
        + "b" * 64 + " 644 root 1 10 - /etc/passwd\n@@bins\n\n@@end\n",
    )
    events = await shield.guard.Guard(ctx, None)._compare(hosts.host_to_sdk(host), snap, {})
    assert [event["kind"] for event in events] == ["file_changed"]
    baseline = await shield.patching.load_baseline(ctx, host.id, "files")
    assert baseline["accepted"] == accepted
    assert await shield.patching.load_baseline(ctx, source.id, "files") is None
    event = (await db_session.execute(select(shield.models.SecurityEventRecord))).scalar_one()
    assert event.host_id == host.id and not event.acknowledged


@pytest.mark.asyncio
@pytest.mark.parametrize("collision", [False, True])
async def test_baseline_keys_and_collisions_survive_merge_and_restore(db_session, shield, collision):
    connection = await db_session.connection()
    await connection.run_sync(shield.models.Base.metadata.create_all)
    db_session.add_all([
        Host(id="source", name="pve", address="192.0.2.20", provider_ext_id="test-ext", provider_ref="node/100",
             host_metadata={"identity_addresses": ["192.0.2.20"]}),
        Host(id="manual", name="von-hand", address="192.0.2.20"),
        shield.models.BaselineRecord(
            id="source:files", host_id="source", kind="files", data={"accepted": "source"}, updated_at=utcnow(),
        ),
        shield.models.BaselineRecord(
            id="independent", host_id="source", kind="guard", data={"checked_at": 1}, updated_at=utcnow(),
        ),
    ])
    if collision:
        db_session.add(shield.models.BaselineRecord(
            id="manual:files", host_id="manual", kind="files", data={"accepted": "manual"}, updated_at=utcnow(),
        ))
    await db_session.flush()
    table = shield.models.BaselineRecord.__table__
    before = list((await db_session.execute(select(table).order_by(table.c.id))).mappings())

    assert await connection.run_sync(merge_matches) == {"source": "manual"}
    rows = list((await db_session.execute(select(table).order_by(table.c.id))).mappings())
    assert [(row["id"], row["host_id"]) for row in rows] == [("independent", "manual"), ("manual:files", "manual")]
    assert rows[1]["data"] == {"accepted": "manual" if collision else "source"}

    await connection.run_sync(restore_merges)
    assert list((await db_session.execute(select(table).order_by(table.c.id))).mappings()) == before


@pytest.mark.asyncio
async def test_scan_result_uses_merge_target(db_session, tmp_path, shield, monkeypatch):
    connection = await db_session.connection()
    await connection.run_sync(shield.models.Base.metadata.create_all)
    source = Host(
        id="source", name="entdeckt", address="192.0.2.20", provider_ext_id="test-ext", provider_ref="node/100",
        host_metadata={"identity_addresses": ["192.0.2.20"]},
    )
    target = Host(id="target", name="von-hand", address="192.0.2.20")
    db_session.add_all([
        source,
        target,
        shield.models.ScanRecord(
            id="scan", host_id=source.id, host_name=source.name, kind="quick", paths=["/tmp"], trigger="manual",
            status="running", infected=0, started_at=utcnow(),
        ),
    ])
    await db_session.flush()
    await db_session.refresh(source, ["tags", "credentials"])
    scanned_host = hosts.host_to_sdk(source)

    manifest = tomllib.loads((EXT_DIR / "extension.toml").read_text(encoding="utf-8"))["extension"]
    _, _, ctx = _build(
        tmp_path, ext_id=manifest["id"], legacy_ids=manifest["legacy_ids"], permissions=["hosts.read", "audit.write"],
    )
    real_get = ctx.hosts.get

    async def get_then_merge(host_id):
        found = await real_get(host_id)
        assert await connection.run_sync(merge_matches) == {source.id: target.id}
        await db_session.commit()
        return found

    monkeypatch.setattr(ctx.hosts, "get", get_then_merge)
    monkeypatch.setattr(ctx.audit, "log", AsyncMock())
    monkeypatch.setattr(ctx.notify, "send", AsyncMock())
    result = shield.antivirus.ScanResult(
        status="infected", infected=1, files_scanned=1, findings=[("/tmp/eicar", "Test.Eicar")],
    )
    defender = shield.defender.Defender(ctx)
    await defender._record_scan(
        scanned_host, "scan", "quick", result, "", 0, {"auto_quarantine": False},
    )

    finding = (await db_session.execute(select(shield.models.FindingRecord))).scalar_one()
    scan = await db_session.get(shield.models.ScanRecord, "scan")
    saved = await defender.list_findings(status="detected")
    assert finding.host_id == target.id
    assert scan is not None and scan.host_id == target.id and scan.status == "infected"
    assert [row["host_id"] for row in saved] == [target.id]


@pytest.mark.asyncio
async def test_findings_survive_a_merge_that_races_the_scan_write(tmp_path, shield, monkeypatch):
    import sqlite3

    from nodvard_deck.config import Settings
    from nodvard_deck.db.session import create_engine_for, reset_engine_cache, set_engine_for_testing
    from nodvard_deck.models import Base
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker

    path = tmp_path / "race.db"
    engine = create_engine_for(Settings(database_url=f"sqlite+aiosqlite:///{path}"))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(shield.models.Base.metadata.create_all)
        set_engine_for_testing(engine)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add_all([
                Host(id="source", name="entdeckt", address="192.0.2.20"),
                Host(id="target", name="von-hand", address="192.0.2.21"),
                shield.models.ScanRecord(
                    id="scan", host_id="source", host_name="entdeckt", kind="quick", paths=["/tmp"], trigger="manual",
                    status="running", infected=0, started_at=utcnow(),
                ),
            ])
            await session.commit()
            source = await session.get(Host, "source")
            await session.refresh(source, ["tags", "credentials"])
            scanned_host = hosts.host_to_sdk(source)

        manifest = tomllib.loads((EXT_DIR / "extension.toml").read_text(encoding="utf-8"))["extension"]
        _, _, ctx = _build(
            tmp_path / "ctx", ext_id=manifest["id"], legacy_ids=manifest["legacy_ids"],
            permissions=["hosts.read", "audit.write"],
        )
        monkeypatch.setattr(ctx.audit, "log", AsyncMock())
        monkeypatch.setattr(ctx.notify, "send", AsyncMock())

        merged = []
        armed = {"on": False}

        def merge_from_second_connection(_conn, _cursor, statement, *_rest):
            # Der Merge kommt gleichzeitig über eine eigene Verbindung, sobald die erste Anweisung am Scan gelaufen ist.
            if not armed["on"] or "ext_nexus_soc_scans" not in statement:
                return
            armed["on"] = False
            other = sqlite3.connect(path, timeout=0.3)
            try:
                other.execute("UPDATE ext_nexus_soc_scans SET host_id = 'target' WHERE host_id = 'source'")
                other.execute("DELETE FROM hosts WHERE id = 'source'")
                other.commit()
                merged.append(True)
            except sqlite3.OperationalError:
                merged.append(False)
            finally:
                other.close()

        real_get = ctx.hosts.get

        async def get_then_arm(host_id):
            found = await real_get(host_id)
            armed["on"] = True
            return found

        monkeypatch.setattr(ctx.hosts, "get", get_then_arm)
        event.listen(engine.sync_engine, "after_cursor_execute", merge_from_second_connection)
        result = shield.antivirus.ScanResult(
            status="infected", infected=1, files_scanned=1, findings=[("/tmp/eicar", "Test.Eicar")],
        )
        await shield.defender.Defender(ctx)._record_scan(
            scanned_host, "scan", "quick", result, "", 0, {"auto_quarantine": False},
        )

        async with async_sessionmaker(engine)() as session:
            host_ids = set((await session.execute(select(Host.id))).scalars())
            finding = (await session.execute(select(shield.models.FindingRecord))).scalar_one()
        assert merged, "der Merge wurde nicht ausgelöst"
        assert merged == [False] or finding.host_id == "target"
        assert finding.host_id in host_ids
    finally:
        await engine.dispose()
        reset_engine_cache()
