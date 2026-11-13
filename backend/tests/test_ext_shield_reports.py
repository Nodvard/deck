from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from nodvard_sdk import Host
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SRC = Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"
NOW = datetime(2026, 11, 9, 7, 0, tzinfo=UTC)


@pytest.fixture
def reports(monkeypatch):
    monkeypatch.syspath_prepend(str(SRC))
    from nodvard_deck_ext_shield import reports

    yield reports
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


@pytest_asyncio.fixture
async def ctx(reports):
    from nodvard_deck_ext_shield.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    active = []

    @asynccontextmanager
    async def session():
        async with sessions() as db:
            active.append(db)
            try:
                yield db
                await db.commit()
            finally:
                active.remove(db)

    async def notify(notification, **kwargs):
        assert not active
        sent.append(notification)

    sent = []
    values = {}
    context = SimpleNamespace(
        db=SimpleNamespace(session=session),
        settings=SimpleNamespace(get=AsyncMock(side_effect=lambda: dict(values)), core=AsyncMock(return_value="Europe/Berlin")),
        hosts=SimpleNamespace(list=AsyncMock(return_value=[Host(id="pi", name="pi", display_name="Pi", address="pi")])),
        notify=SimpleNamespace(send=AsyncMock(side_effect=notify)),
        exec=SimpleNamespace(run=AsyncMock(side_effect=AssertionError("Keine Serverabfragen"))),
        values=values, sent=sent,
    )
    yield context
    await engine.dispose()


async def _seed(ctx):
    from nodvard_deck_ext_shield.models import (
        AuditRecord,
        BaselineRecord,
        FindingRecord,
        ScanRecord,
    )

    async with ctx.db.session() as db:
        db.add_all([
            BaselineRecord(id="pi:updates", host_id="pi", kind="updates", updated_at=NOW - timedelta(hours=1),
                           data={"count": 9, "security_count": 3, "checked_at": NOW.timestamp() - 3600}),
            BaselineRecord(id="pi:guard", host_id="pi", kind="guard", updated_at=NOW - timedelta(hours=2),
                           data={"fail2ban": "running", "banned_count": 4, "checked_at": NOW.timestamp() - 7200}),
            ScanRecord(id="scan", host_id="pi", host_name="Pi", kind="quick", trigger="schedule", status="infected",
                       started_at=NOW - timedelta(hours=3), finished_at=NOW - timedelta(hours=2), infected=1, files_scanned=50),
            ScanRecord(id="old-scan", host_id="pi", host_name="Pi", kind="quick", trigger="schedule", status="clean",
                       started_at=NOW - timedelta(days=3), finished_at=NOW - timedelta(days=3), infected=0),
            AuditRecord(id="audit", host_id="pi", host_name="Pi", status="ok", hardening_index=74,
                        created_at=NOW - timedelta(days=2)),
            AuditRecord(id="bad-audit", host_id="pi", host_name="Pi", status="error", created_at=NOW - timedelta(days=1)),
        ])
        for ident, detected, status, changed in [
            ("new", NOW - timedelta(days=1), "detected", None),
            ("done", NOW - timedelta(days=10), "quarantined", NOW - timedelta(days=1)),
            ("restored", NOW - timedelta(days=10), "restored", NOW - timedelta(days=1)),
            ("old", NOW - timedelta(days=8), "deleted", NOW - timedelta(days=8)),
            ("at-start", NOW - timedelta(days=7), "ignored", NOW - timedelta(days=7)),
            ("at-end", NOW, "detected", None),
        ]:
            db.add(FindingRecord(id=ident, host_id="pi", host_name="Pi", path=f"/tmp/{ident}", signature="Test.Signature",
                                 detected_at=detected, status=status, status_changed_at=changed))


async def test_snapshot_uses_saved_data_and_finding_window(reports, ctx):
    await _seed(ctx)
    result = await reports.WeeklyReports(ctx).generate(now=NOW)
    row = result["hosts"][0]
    assert row["updates"] == {"open": 9, "security": 3, "checked_at": NOW.timestamp() - 3600, "error": None}
    assert row["scan"] == {"status": "infected", "infected": 1, "files_scanned": 50,
                           "started_at": NOW.timestamp() - 10800, "finished_at": NOW.timestamp() - 7200}
    assert row["fail2ban"] == {"status": "running", "banned": 4, "checked_at": NOW.timestamp() - 7200, "error": None}
    assert row["lynis"] == {"hardening_index": 74, "created_at": (NOW - timedelta(days=2)).timestamp()}
    assert [r["id"] for r in row["findings"]["new"]] == ["at-start", "new"]
    assert {r["id"] for r in row["findings"]["resolved"]} == {"at-start", "done"}
    assert row["certificates"] is None and "nicht gespeichert" in result["certificates_note"]
    assert result["summary"] == {"updates": 9, "security_updates": 3, "new_findings": 2, "resolved_findings": 2,
                                 "unknown_update_hosts": 0, "unknown_security_update_hosts": 0}
    assert result["period_start"] == (NOW - timedelta(days=7)).timestamp()
    assert result["period_end"] == NOW.timestamp()
    ctx.exec.run.assert_not_awaited()


async def test_unknown_metrics_remain_unknown(reports, ctx):
    result = await reports.WeeklyReports(ctx).generate(now=NOW)
    row = result["hosts"][0]
    assert all(row[k] is None for k in ("updates", "scan", "fail2ban", "lynis", "certificates"))
    assert result["summary"]["unknown_update_hosts"] == 1
    assert result["summary"]["unknown_security_update_hosts"] == 1
    assert result["host_count"] == 1


async def test_unreadable_metrics_do_not_become_zero(reports, ctx):
    from nodvard_deck_ext_shield.models import BaselineRecord

    async with ctx.db.session() as db:
        db.add_all([
            BaselineRecord(id="pi:updates", host_id="pi", kind="updates", updated_at=NOW,
                           data={"error": "Nicht erreichbar", "checked_at": NOW.timestamp()}),
            BaselineRecord(id="pi:guard", host_id="pi", kind="guard", updated_at=NOW,
                           data={"fail2ban": "noaccess", "banned_count": 0, "checked_at": NOW.timestamp()}),
        ])
    result = await reports.WeeklyReports(ctx).generate(now=NOW)
    assert result["hosts"][0]["updates"] is None
    assert result["hosts"][0]["fail2ban"]["banned"] is None
    assert result["summary"]["unknown_update_hosts"] == 1


async def test_failed_update_check_keeps_last_good_stand_and_error(reports, ctx):
    from nodvard_deck_ext_shield.models import BaselineRecord

    async with ctx.db.session() as db:
        db.add(BaselineRecord(id="pi:updates", host_id="pi", kind="updates", updated_at=NOW,
                              data={"count": 9, "security_count": 3, "checked_at": NOW.timestamp() - 86400,
                                    "error": "Nicht erreichbar", "attempted_at": NOW.timestamp()}))
    result = await reports.WeeklyReports(ctx).generate(now=NOW)
    assert result["hosts"][0]["updates"] == {"open": 9, "security": 3, "checked_at": NOW.timestamp() - 86400,
                                             "error": "Nicht erreichbar"}
    assert result["summary"]["unknown_update_hosts"] == 0


async def test_running_scan_does_not_hide_last_completed_result(reports, ctx):
    from nodvard_deck_ext_shield.models import ScanRecord

    await _seed(ctx)
    async with ctx.db.session() as db:
        db.add(ScanRecord(id="running", host_id="pi", host_name="Pi", kind="deep", trigger="schedule", status="running",
                          started_at=NOW - timedelta(minutes=1), infected=0))
    result = await reports.WeeklyReports(ctx).generate(now=NOW)
    assert result["hosts"][0]["scan"]["status"] == "infected"


async def test_snapshot_is_immutable_once_per_local_week(reports, ctx):
    await _seed(ctx)
    first = await reports.WeeklyReports(ctx).generate(now=NOW)
    from nodvard_deck_ext_shield.models import BaselineRecord

    async with ctx.db.session() as db:
        (await db.get(BaselineRecord, "pi:updates")).data = {"count": 0, "security_count": 0}
    repeated = await reports.WeeklyReports(ctx).generate(now=NOW + timedelta(days=2))
    assert repeated == first
    assert len(ctx.sent) == 1
    assert ctx.sent[0].payload == {"notification_type": "shield.weekly_report", "host_id": "pi",
                                  "path": f"/ext/shield/soc?tab=reports&report={first['id']}"}
    assert ctx.sent[0].correlation_id == f"shield-weekly:{first['id']}"
    assert "9" in ctx.sent[0].body and "3" in ctx.sent[0].body
    assert ctx.notify.send.call_args.kwargs == {"raise_on_failure": True}


async def test_report_week_uses_installation_timezone(reports, ctx):
    ctx.settings.core.return_value = "America/Los_Angeles"
    result = await reports.WeeklyReports(ctx).generate(now=NOW)
    assert result["id"] == "2026-11-02"
    assert result["created_at"] == NOW.timestamp()


async def test_delivery_failure_retries_saved_report_without_new_snapshot(reports, ctx):
    service = reports.WeeklyReports(ctx)
    ctx.notify.send.side_effect = RuntimeError("Kanal ausgefallen")
    with pytest.raises(RuntimeError, match="Kanal ausgefallen"):
        await service.generate(now=NOW)
    ctx.notify.send.side_effect = None
    result = await reports.WeeklyReports(ctx).generate(now=NOW + timedelta(hours=1))
    assert result["created_at"] == NOW.timestamp()
    assert ctx.notify.send.await_count == 2
    await service.generate(now=NOW + timedelta(hours=2))
    assert ctx.notify.send.await_count == 2


async def test_cancelled_delivery_retries_saved_snapshot(reports, ctx):
    ctx.notify.send.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await reports.WeeklyReports(ctx).generate(now=NOW)
    ctx.notify.send.side_effect = None
    result = await reports.WeeklyReports(ctx).generate(now=NOW + timedelta(hours=1))
    assert result["created_at"] == NOW.timestamp()
    assert ctx.notify.send.await_count == 2


async def test_unknown_security_count_is_named_in_summary_and_notification(reports, ctx):
    from nodvard_deck_ext_shield.models import BaselineRecord

    async with ctx.db.session() as db:
        db.add(BaselineRecord(id="pi:updates", host_id="pi", kind="updates", updated_at=NOW,
                              data={"count": 9, "checked_at": NOW.timestamp()}))
    result = await reports.WeeklyReports(ctx).generate(now=NOW)
    assert result["hosts"][0]["updates"]["security"] is None
    assert result["summary"]["unknown_security_update_hosts"] == 1
    assert "Sicherheitsupdate-Stand bei 1 Servern unbekannt" in ctx.sent[0].body


async def test_concurrent_runs_deliver_once(reports, ctx):
    service = reports.WeeklyReports(ctx)
    results = await asyncio.gather(service.generate(now=NOW), service.generate(now=NOW))
    assert results[0] == results[1] and len(ctx.sent) == 1


async def test_next_report_starts_at_last_report_end_and_retains_twelve_weeks(reports, ctx):
    from nodvard_deck_ext_shield.models import WeeklyReportRecord

    service = reports.WeeklyReports(ctx)
    for weeks in range(14):
        result = await service.generate(now=NOW + timedelta(weeks=weeks))
        if weeks:
            assert result["period_start"] == (NOW + timedelta(weeks=weeks - 1)).timestamp()
    now = NOW + timedelta(weeks=13)
    listing = await service.list(now=now)
    assert listing["total"] == len(listing["items"]) == 12
    assert listing["items"][0]["id"] == result["id"]
    assert all("hosts" not in row for row in listing["items"])
    assert await service.get("2026-11-09", now=now) is None
    async with ctx.db.session() as db:
        assert len((await db.scalars(select(WeeklyReportRecord))).all()) == 12


async def test_earlier_schedule_still_keeps_only_twelve_reports(reports, ctx):
    from nodvard_deck_ext_shield.models import WeeklyReportRecord

    service = reports.WeeklyReports(ctx)
    first = None
    for week in range(13):
        now = NOW + timedelta(weeks=week, minutes=-week)
        report = await service.generate(now=now)
        first = first or report
    assert await service.get(first["id"], now=now) is None
    async with ctx.db.session() as db:
        assert len((await db.scalars(select(WeeklyReportRecord))).all()) == 12


async def test_only_shield_target_hosts_are_included(reports, ctx):
    ctx.values["av_host_tag"] = "protected"
    ctx.hosts.list.return_value = [
        Host(id="pi", name="pi", display_name="Pi", address="pi"),
        Host(id="win", name="win", display_name="Windows", address="win", os_family="windows"),
        Host(id="remote", name="remote", display_name="Extern", address="remote", has_credential=False),
    ]
    result = await reports.WeeklyReports(ctx).generate(now=NOW)
    assert [r["host_id"] for r in result["hosts"]] == ["pi"]
    ctx.hosts.list.assert_awaited_once_with(tag="protected")


async def test_api_history_detail_and_missing(reports, ctx):
    saved = await reports.WeeklyReports(ctx).generate(now=NOW)
    app = FastAPI()
    service = reports.WeeklyReports(ctx)
    app.include_router(reports.build_router(service))
    service.list = AsyncMock(return_value={"items": [{k: v for k, v in saved.items() if k not in ("hosts", "certificates_note")}], "total": 1})
    service.get = AsyncMock(side_effect=lambda ident: saved if ident == saved["id"] else None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listing = await client.get("/reports")
        detail = await client.get(f"/reports/{saved['id']}")
        missing = await client.get("/reports/missing")
    assert listing.status_code == 200 and listing.json()["total"] == 1
    assert detail.status_code == 200 and detail.json() == saved
    assert missing.status_code == 404
    schema = app.openapi()
    assert "WeeklyReportOut" in schema["components"]["schemas"]
    assert "application/json" in schema["paths"]["/reports/{report_id}"]["get"]["responses"]["404"]["content"]
    assert schema["paths"]["/reports/{report_id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/WeeklyReportOut")


async def test_job_registers_default_custom_disabled_and_fallback(reports, ctx):
    from nodvard_deck.core.cron import cron_trigger

    jobs = []
    ctx.scheduler = SimpleNamespace(validate_schedule=lambda value: cron_trigger(value), register_job=AsyncMock(side_effect=jobs.append))
    service = reports.WeeklyReports(ctx)
    await reports.register_job(ctx, service)
    assert (jobs[-1].id, jobs[-1].schedule, jobs[-1].enabled) == ("weekly-report", "0 8 * * 1", True)
    next_run = cron_trigger(jobs[-1].schedule, timezone="Europe/Berlin").get_next_fire_time(None, NOW)
    assert next_run == NOW
    ctx.values.update(weekly_report_cron="30 19 * * 5", weekly_report_enabled=False)
    await reports.register_job(ctx, service)
    assert jobs[-1].enabled is False
    ctx.values["weekly_report_enabled"] = True
    await reports.register_job(ctx, service)
    assert jobs[-1].schedule == "30 19 * * 5"
    service.generate = AsyncMock(return_value={"id": "test"})
    assert await jobs[-1].handler() == {"id": "test"}
    ctx.values.update(weekly_report_cron="broken", weekly_report_enabled=True)
    await reports.register_job(ctx, service)
    assert jobs[-1].schedule == reports.DEFAULT_CRON
    assert "ungültig" in ctx.sent[-1].title


async def test_disabled_job_handler_does_not_generate(reports, ctx):
    ctx.values["weekly_report_enabled"] = False
    assert await reports.WeeklyReports(ctx).run() == {"skipped": True}
    ctx.hosts.list.assert_not_awaited()
