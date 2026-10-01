"""Zeitzone des Dashboards (`core/timezone.py`, Einstellung `system.timezone`) und
Aufbewahrung des Protokolls (`audit.retention_days`).

Vorgabe: `NODVARD_DECK_TIMEZONE`, sonst `TZ`, sonst Europe/Berlin. Umstellen verschiebt die
Jobs mit der alten Standardzone (auch die der Erweiterungen) und plant sie neu."""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from nodvard_deck import config
from nodvard_deck.config import LOCAL_TIMEZONE, Settings, default_timezone
from nodvard_deck.core import maintenance
from nodvard_deck.core import timezone as tz_service
from nodvard_deck.core.scheduler import CORE_SCHEDULER_EXT_ID, get_scheduler_service, register_core_jobs
from nodvard_deck.db import utcnow
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import AuditEntry, Job
from nodvard_deck.services import audit as audit_service
from nodvard_deck.services import jobs as jobs_service
from nodvard_deck.services import settings as settings_service

TOKYO = "Asia/Tokyo"  # 7 bis 8 Stunden von Berlin entfernt, nie Sommerzeit: eindeutig verschieden


# ---------------------------------------------------------------------------
# Vorgabe aus der Umgebung
# ---------------------------------------------------------------------------


def test_default_is_berlin_without_any_variable():
    assert default_timezone(Settings()) == LOCAL_TIMEZONE


def test_default_prefers_deck_variable_over_tz(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    assert default_timezone(Settings()) == "America/New_York"
    monkeypatch.setenv("NODVARD_DECK_TIMEZONE", TOKYO)
    assert default_timezone(Settings()) == TOKYO


def test_default_accepts_the_old_prefix_as_fallback(monkeypatch):
    monkeypatch.setenv("LATTICE_TIMEZONE", TOKYO)
    assert default_timezone(Settings()) == TOKYO


def test_default_ignores_an_invalid_value_and_falls_through(monkeypatch):
    monkeypatch.setenv("NODVARD_DECK_TIMEZONE", "Mitteleuropa/Nirgendwo")
    monkeypatch.setenv("TZ", "UTC")
    assert default_timezone(Settings()) == "UTC"
    monkeypatch.delenv("TZ")
    assert default_timezone(Settings()) == LOCAL_TIMEZONE


def test_default_handles_a_leading_colon_in_tz(monkeypatch):
    monkeypatch.setenv("TZ", ":Europe/Vienna")
    assert default_timezone(Settings()) == "Europe/Vienna"


@pytest.mark.asyncio
async def test_get_timezone_uses_default_then_stored_value(db_session, monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    monkeypatch.setattr(config, "_settings", Settings())
    assert await tz_service.get_timezone(db_session) == "America/New_York"

    # Direkt in die Tabelle geschrieben: der Cache wird dabei verworfen.
    await settings_service.set_global(db_session, "system.timezone", TOKYO)
    assert await tz_service.get_timezone(db_session) == TOKYO


# ---------------------------------------------------------------------------
# set_timezone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "Mitteleuropa/Nirgendwo", "europe/berlin", "../etc/passwd", None, 5])
async def test_set_timezone_rejects_invalid_names(db_session, bad):
    with pytest.raises(tz_service.InvalidTimezone):
        await tz_service.set_timezone(db_session, bad)
    assert await settings_service.get_global(db_session, "system.timezone") is None


async def _register(db_session, *, ext_id, key, schedule="0 7 * * *", enabled=True, timezone=None):
    async def handler(**_):
        return "ok"

    job = await jobs_service.upsert_job(
        db_session, ext_id=ext_id, ext_job_key=key, name=key, kind="ext" if ext_id else "core",
        schedule=schedule, params={}, enabled=enabled, timezone=timezone,
    )
    get_extension_runtime().scheduler.register(ext_id or CORE_SCHEDULER_EXT_ID, key, handler)
    if enabled:
        await get_scheduler_service().schedule(job, handler)
    return job


@pytest.mark.asyncio
async def test_switching_the_zone_moves_next_run_of_core_and_extension_jobs(db_session):
    reset_extension_runtime()
    # Gestartet, wie im Betrieb: ein noch nicht laufender Scheduler haelt neu geplante Jobs
    # nur in der Warteschlange und zeigt per get_job() noch den alten.
    get_scheduler_service().start()
    core = await _register(db_session, ext_id=None, key="audit-retention-purge")
    ext = await _register(db_session, ext_id="nexus-soc", key="defender-briefing")
    await db_session.commit()
    before = {}
    for job in (core, ext):
        await db_session.refresh(job)
        assert job.timezone == LOCAL_TIMEZONE
        before[job.id] = job.next_run_at
        assert before[job.id] is not None

    changed = await tz_service.set_timezone(db_session, TOKYO)
    assert changed == 2

    for job in (core, ext):
        await db_session.refresh(job)
        assert job.timezone == TOKYO
        assert job.next_run_at != before[job.id]
        # 07:00 in Tokio ist unabhaengig vom Tag 22:00 UTC des Vortags.
        assert job.next_run_at.astimezone(ZoneInfo(TOKYO)).hour == 7
    # Auch APScheduler selbst plant in der neuen Zone.
    scheduled = get_scheduler_service()._scheduler.get_job(ext.id)
    assert str(scheduled.trigger.timezone) == TOKYO
    assert await tz_service.get_timezone(db_session) == TOKYO
    reset_extension_runtime()


@pytest.mark.asyncio
async def test_only_jobs_with_the_old_default_zone_are_switched(db_session):
    reset_extension_runtime()
    default_job = await _register(db_session, ext_id="a", key="standard")
    own_zone = await _register(db_session, ext_id="a", key="eigene-zone", timezone="America/New_York")
    await db_session.commit()

    assert await tz_service.set_timezone(db_session, TOKYO) == 1
    await db_session.refresh(default_job)
    await db_session.refresh(own_zone)
    assert default_job.timezone == TOKYO
    assert own_zone.timezone == "America/New_York"

    # Zweiter Wechsel: jetzt ist Tokio die "alte Standardzone".
    assert await tz_service.set_timezone(db_session, "Europe/Vienna") == 1
    await db_session.refresh(default_job)
    await db_session.refresh(own_zone)
    assert default_job.timezone == "Europe/Vienna"
    assert own_zone.timezone == "America/New_York"
    reset_extension_runtime()


@pytest.mark.asyncio
async def test_switch_keeps_disabled_jobs_paused_and_skips_jobs_without_handler(db_session):
    reset_extension_runtime()
    paused = await _register(db_session, ext_id="a", key="pausiert", enabled=False)
    orphan = await jobs_service.upsert_job(
        db_session, ext_id="weg", ext_job_key="ohne-handler", name="x", kind="ext",
        schedule="0 7 * * *", params={}, enabled=True,
    )
    await db_session.commit()

    assert await tz_service.set_timezone(db_session, TOKYO) == 2
    await db_session.refresh(paused)
    await db_session.refresh(orphan)
    assert (paused.timezone, paused.next_run_at) == (TOKYO, None)
    assert orphan.timezone == TOKYO  # stimmt, sobald die Erweiterung wieder laeuft
    assert get_scheduler_service()._scheduler.get_job(orphan.id) is None
    reset_extension_runtime()


@pytest.mark.asyncio
async def test_new_and_reloaded_jobs_use_the_configured_zone(db_session):
    await tz_service.set_timezone(db_session, TOKYO)
    created = await jobs_service.upsert_job(
        db_session, ext_id="a", ext_job_key="neu", name="neu", kind="ext",
        schedule="0 7 * * *", params={}, enabled=True,
    )
    assert created.timezone == TOKYO

    # Ein erneutes register_job() (Erweiterung neu geladen) ueberschreibt die Zone nicht ...
    again = await jobs_service.upsert_job(
        db_session, ext_id="a", ext_job_key="neu", name="neu", kind="ext",
        schedule="0 8 * * *", params={}, enabled=True,
    )
    assert (again.id, again.timezone, again.schedule) == (created.id, TOKYO, "0 8 * * *")
    # ... ausser sie wird ausdruecklich uebergeben.
    explicit = await jobs_service.upsert_job(
        db_session, ext_id="a", ext_job_key="neu", name="neu", kind="ext",
        schedule="0 8 * * *", params={}, enabled=True, timezone="UTC",
    )
    assert explicit.timezone == "UTC"


@pytest.mark.asyncio
async def test_first_switch_also_takes_jobs_saved_with_the_old_hard_default(db_session, monkeypatch):
    """Vor dieser Version stand bei jedem Job fest Europe/Berlin. Ist die Vorgabe der
    Umgebung eine andere (TZ=UTC im Container), gehoeren diese Jobs trotzdem zur Standardzone."""
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setattr(config, "_settings", Settings())
    tz_service.reset_timezone_cache()
    legacy = Job(ext_id="a", ext_job_key="alt", name="alt", kind="ext", schedule="0 7 * * *", params={}, enabled=False)
    db_session.add(legacy)
    await db_session.flush()
    assert legacy.timezone == LOCAL_TIMEZONE

    assert await tz_service.set_timezone(db_session, TOKYO) == 1
    await db_session.refresh(legacy)
    assert legacy.timezone == TOKYO


# ---------------------------------------------------------------------------
# Wartungsfenster
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintenance_window_follows_the_configured_zone(db_session):
    start = utcnow() - timedelta(minutes=10)
    local = start.astimezone(ZoneInfo(TOKYO))
    await settings_service.set_global(
        db_session, "maintenance.windows",
        [{"cron": f"{local.minute} {local.hour} * * *", "duration_minutes": 60, "host_ids": "all"}],
    )
    # Die Uhrzeit ist Tokioter Ortszeit: in Berlin liegt sie Stunden daneben ...
    assert await maintenance.is_in_window(db_session, host_id="h1") is False
    # ... nach dem Umstellen der Zone laeuft das Fenster gerade.
    await tz_service.set_timezone(db_session, TOKYO)
    assert await maintenance.is_in_window(db_session, host_id="h1") is True
    # Zurueck nach Berlin: wieder daneben.
    await tz_service.set_timezone(db_session, LOCAL_TIMEZONE)
    assert await maintenance.is_in_window(db_session, host_id="h1") is False


def test_window_covers_now_takes_the_zone_as_argument():
    from datetime import UTC, datetime

    now = datetime(2026, 10, 20, 18, 30, tzinfo=UTC)  # 03:30 in Tokio, 20:30 in Berlin
    assert maintenance._window_covers_now("0 3 * * *", 60, now, TOKYO) is True
    assert maintenance._window_covers_now("0 3 * * *", 60, now) is False  # ohne Angabe: Berlin


# ---------------------------------------------------------------------------
# Aufbewahrung des Protokolls
# ---------------------------------------------------------------------------


async def _old_entries(db_session, *ages_days):
    for age in ages_days:
        entry = await audit_service.log(db_session, actor_type="system", actor_id="t", action="test.alt", outcome="success")
        entry.ts = utcnow() - timedelta(days=age)
    await db_session.flush()


@pytest.mark.asyncio
async def test_retention_setting_beats_the_environment_value(db_session):
    assert await audit_service.effective_retention_days(db_session, fallback=90) == 90
    await settings_service.set_global(db_session, "audit.retention_days", 30)
    assert await audit_service.effective_retention_days(db_session, fallback=90) == 30
    # Ein kaputter gespeicherter Wert faellt auf die Umgebung zurueck, statt alles zu loeschen.
    for broken in (0, 1, -5, 4000, "30", True, None):
        await settings_service.set_global(db_session, "audit.retention_days", broken)
        assert await audit_service.effective_retention_days(db_session, fallback=90) == 90


@pytest.mark.asyncio
async def test_purge_job_reads_the_setting_and_falls_back_to_the_environment(db_session, tmp_path, monkeypatch):
    reset_extension_runtime()
    monkeypatch.setattr(config, "_settings", Settings(audit_retention_days=200, data_dir=tmp_path))
    await register_core_jobs(tmp_path / "runs")
    purge = get_extension_runtime().scheduler.get(CORE_SCHEDULER_EXT_ID, "audit-retention-purge")
    await _old_entries(db_session, 5, 20, 100, 300)
    await db_session.commit()

    async def remaining() -> int:
        rows = await db_session.execute(select(AuditEntry).where(AuditEntry.action == "test.alt"))
        return len(rows.scalars().all())

    # Ohne Einstellung gilt die Umgebung (200 Tage): nur der 300 Tage alte Eintrag geht.
    assert (await purge())["deleted"] == 1
    assert await remaining() == 3

    # Die Einstellung schlaegt die Umgebung: 30 Tage, also geht der 100 Tage alte.
    await settings_service.set_global(db_session, "audit.retention_days", 30)
    await db_session.commit()
    assert (await purge())["deleted"] == 1
    assert await remaining() == 2

    await settings_service.set_global(db_session, "audit.retention_days", 7)
    await db_session.commit()
    assert (await purge())["deleted"] == 1
    assert await remaining() == 1
    reset_extension_runtime()
