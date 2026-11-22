from __future__ import annotations

import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nodvard_deck.models import Host
from nodvard_sdk import Host as SdkHost
from sqlalchemy import select
from test_ext_context import _build
from test_hosts_api import _auth_header, _bootstrap_owner

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "shield"


@pytest.fixture
def shield(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    from nodvard_deck_ext_shield import defender, models, reports

    yield SimpleNamespace(defender=defender, models=models, reports=reports)
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


@pytest.mark.asyncio
@pytest.mark.parametrize("gone", [False, True], ids=["server-loeschen", "server-entfernen"])
async def test_deleted_host_leaves_shield_lists_score_and_reports(client, db_session, tmp_path, shield, gone):
    token = await _bootstrap_owner(client)
    headers = _auth_header(token)
    created = await client.post("/api/v1/hosts", headers=headers,
                                json={"name": "server-alt", "address": "192.0.2.10", "tags": ["shield"]})
    assert created.status_code == 201, created.text
    host_id = created.json()["id"]
    credential = await client.post(f"/api/v1/hosts/{host_id}/credentials", headers=headers,
                                   json={"kind": "ssh_password", "username": "root", "secret_value": "test"})
    assert credential.status_code == 201, credential.text
    now = datetime.now(UTC)
    if gone:
        host = await db_session.get(Host, host_id)
        host.host_metadata = {**host.host_metadata, "gone_since": now.isoformat()}
        host.provider_ext_id = "proxmox"
        host.provider_ref = "qemu/100"
        await db_session.commit()

    connection = await db_session.connection()
    await connection.run_sync(shield.models.Base.metadata.create_all)
    await db_session.commit()
    manifest = tomllib.loads((EXT_DIR / "extension.toml").read_text(encoding="utf-8"))["extension"]
    _, _, context = _build(tmp_path, ext_id=manifest["id"], legacy_ids=manifest["legacy_ids"], permissions=["hosts.read"])
    ctx = SimpleNamespace(
        db=context.db, hosts=context.hosts,
        settings=SimpleNamespace(get=AsyncMock(return_value={}), core=AsyncMock(return_value="UTC")),
        notify=SimpleNamespace(send=AsyncMock()),
    )
    service = shield.defender.Defender(ctx)
    service.host_status = AsyncMock(return_value={"reachable": False, "clamav_installed": False})
    reports = shield.reports.WeeklyReports(ctx)
    async with ctx.db.session() as db:
        db.add_all([
            shield.models.ScanRecord(id="scan", host_id=host_id, host_name="server-alt", kind="quick",
                                     trigger="manual", status="infected", infected=1,
                                     started_at=now - timedelta(days=1)),
            shield.models.AuditRecord(id="audit", host_id=host_id, host_name="server-alt", status="ok",
                                      hardening_index=30, created_at=now - timedelta(days=1)),
            shield.models.BaselineRecord(id=f"{host_id}:updates", host_id=host_id, kind="updates",
                                         data={"count": 12, "security_count": 4}, updated_at=now),
        ])
        for status in ("detected", "quarantined", "deleted"):
            db.add(shield.models.FindingRecord(
                id=status, scan_id="scan", host_id=host_id, host_name="server-alt", path=f"/tmp/{status}",
                signature="Test.Signature", status=status, detected_at=now - timedelta(hours=1),
                status_changed_at=now - timedelta(minutes=30),
            ))
    before = await service.overview()
    assert [row["host_id"] for row in before["hosts"]] == [host_id]
    assert before["summary"]["open_threats"] == 1
    saved = await reports.generate(now=now)
    assert saved["host_count"] == 1 and saved["summary"]["updates"] == 12

    deleted = await client.request("DELETE", f"/api/v1/hosts/{host_id}", headers=headers,
                                   json={"current_password": "correct-horse-battery"})
    assert deleted.status_code == 204, deleted.text
    assert await ctx.hosts.list(tag="shield") == []
    after = await service.overview()
    assert after["hosts"] == [] and after["attention"] == []
    assert after["summary"]["hosts"] == after["summary"]["hosts_known"] == 0
    for key in ("open_threats", "quarantined", "neutralized_total", "findings_30d"):
        assert after["summary"][key] == 0
    assert after["summary"]["score"] == 0
    assert after["summary"]["score_reasons"] == []
    assert await service.list_scans() == []
    assert await service.list_scans(host=host_id) == []
    assert await service.list_findings() == []
    assert await service.latest_audits() == []

    for report in (await reports.get(saved["id"], now=now), await reports.generate(now=now)):
        assert report["hosts"] == [] and report["host_count"] == 0
        assert all(value == 0 for value in report["summary"].values())
    listing = await reports.list(now=now)
    assert listing["items"][0]["host_count"] == 0
    assert all(value == 0 for value in listing["items"][0]["summary"].values())
    next_week = await reports.generate(now=now + timedelta(weeks=1))
    assert next_week["hosts"] == [] and next_week["host_count"] == 0
    assert ctx.notify.send.await_count == 2
    assert ctx.notify.send.call_args.args[0].body.startswith("0 Server:")
    async with ctx.db.session() as db:
        assert (await db.scalars(select(shield.models.ScanRecord))).all()
        assert (await db.get(shield.models.WeeklyReportRecord, saved["id"])).data == saved


@pytest.mark.asyncio
async def test_orphaned_results_do_not_displace_current_hosts_or_lower_their_score(db_session, tmp_path, shield):
    connection = await db_session.connection()
    await connection.run_sync(shield.models.Base.metadata.create_all)
    await db_session.commit()
    manifest = tomllib.loads((EXT_DIR / "extension.toml").read_text(encoding="utf-8"))["extension"]
    _, _, context = _build(tmp_path, ext_id=manifest["id"], legacy_ids=manifest["legacy_ids"])
    ctx = SimpleNamespace(
        db=context.db,
        hosts=SimpleNamespace(list=AsyncMock(return_value=[SdkHost(id="pi", name="pi", display_name="Pi", address="pi")])),
        settings=SimpleNamespace(get=AsyncMock(return_value={})),
    )
    now = datetime.now(UTC)

    def scan(ident, host_id, started_at):
        return shield.models.ScanRecord(id=ident, host_id=host_id, host_name=host_id, kind="quick",
                                         trigger="manual", status="clean", started_at=started_at)

    def audit(ident, host_id, created_at):
        return shield.models.AuditRecord(id=ident, host_id=host_id, host_name=host_id, status="ok",
                                          hardening_index=80, created_at=created_at)

    async with ctx.db.session() as db:
        db.add_all([
            scan("scan-pi", "pi", now - timedelta(days=1)),
            audit("audit-pi", "pi", now - timedelta(days=1)),
            shield.models.FindingRecord(id="finding-pi", host_id="pi", host_name="Pi", path="/tmp/test",
                                         signature="Test.Signature", status="quarantined", detected_at=now),
        ])
    service = shield.defender.Defender(ctx)
    service.host_status = AsyncMock(return_value={"reachable": True, "clamav_installed": True,
                                                  "signature_ts": now.timestamp()})
    before = await service.overview()
    async with ctx.db.session() as db:
        for i in range(501):
            db.add(scan(f"scan-gone-{i}", "gone", now))
            db.add(audit(f"audit-gone-{i}", "gone", now))
        db.add(shield.models.FindingRecord(id="finding-gone", host_id="gone", host_name="server-alt", path="/tmp/test",
                                           signature="Test.Signature", status="detected", detected_at=now + timedelta(minutes=1)))
    after = await service.overview()
    assert after["hosts"] == before["hosts"]
    assert after["summary"] == before["summary"]
    assert [r["id"] for r in await service.list_scans(limit=1)] == ["scan-pi"]
    assert [r["id"] for r in await service.list_findings(limit=1)] == ["finding-pi"]
    assert await service.list_findings(status="detected") == []
    assert [r["id"] for r in await service.latest_audits()] == ["audit-pi"]
    ctx.hosts.list.side_effect = [ctx.hosts.list.return_value, []]
    service.host_status.reset_mock()
    removed = await service.overview()
    assert removed["hosts"] == []
    assert removed["summary"]["hosts"] == removed["summary"]["hosts_known"] == 0
    assert removed["summary"]["score_reasons"] == []
    service.host_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_findings_stay_listed_behind_many_newer_resolved_ones(client, db_session, tmp_path, shield):
    """Viele neuere, erledigte Funde dürfen offene nicht aus der Liste drängen (Zähler und Liste aus einer Menge)."""
    token = await _bootstrap_owner(client)
    created = await client.post("/api/v1/hosts", headers=_auth_header(token),
                                json={"name": "server-viel", "address": "192.0.2.20", "tags": ["shield"]})
    assert created.status_code == 201, created.text
    host_id = created.json()["id"]
    connection = await db_session.connection()
    await connection.run_sync(shield.models.Base.metadata.create_all)
    await db_session.commit()
    manifest = tomllib.loads((EXT_DIR / "extension.toml").read_text(encoding="utf-8"))["extension"]
    _, _, context = _build(tmp_path, ext_id=manifest["id"], legacy_ids=manifest["legacy_ids"], permissions=["hosts.read"])
    ctx = SimpleNamespace(
        db=context.db, hosts=context.hosts,
        settings=SimpleNamespace(get=AsyncMock(return_value={}), core=AsyncMock(return_value="UTC")),
        notify=SimpleNamespace(send=AsyncMock()),
    )
    service = shield.defender.Defender(ctx)
    service.host_status = AsyncMock(return_value={"reachable": False, "clamav_installed": False})
    now = datetime.now(UTC)

    def finding(name: str, status: str, age: timedelta):
        return shield.models.FindingRecord(
            id=name, scan_id="scan", host_id=host_id, host_name="server-viel", path=f"/srv/{name}",
            signature="Test.Signature", status=status, detected_at=now - age, status_changed_at=now - age,
        )

    async with ctx.db.session() as db:
        db.add(shield.models.ScanRecord(id="scan", host_id=host_id, host_name="server-viel", kind="deep",
                                        trigger="manual", status="infected", infected=345, started_at=now - timedelta(days=9)))
        db.add_all([finding(f"offen-{i}", "detected", timedelta(days=8, minutes=i)) for i in range(75)])
        db.add_all([finding(f"tresor-{i}", "quarantined", timedelta(days=7, minutes=i)) for i in range(5)])
        db.add_all([finding(f"weg-{i}", "deleted", timedelta(minutes=i)) for i in range(265)])

    overview = await service.overview()
    assert overview["summary"]["open_threats"] == 75
    listed = await service.list_findings()
    assert sum(1 for f in listed if f["status"] == "detected") == 75
    assert sum(1 for f in listed if f["status"] == "quarantined") == 5
    # Erledigtes bleibt begrenzt, die Reihenfolge bleibt „neueste zuerst“.
    assert sum(1 for f in listed if f["status"] == "deleted") == 200
    stamps = [f["detected_at"] for f in listed]
    assert stamps == sorted(stamps, reverse=True)
    assert len(await service.list_findings(status="detected")) == 75
