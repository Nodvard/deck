from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

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
    from nodvard_deck_ext_shield import guard, intrusion, models, patching

    yield SimpleNamespace(guard=guard, intrusion=intrusion, models=models, patching=patching)
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
