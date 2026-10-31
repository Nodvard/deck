"""Wartungsfenster (docs/03-DATA-MODEL.md §9): die symmetrische Unterdrückungsprüfung, ohne services.notifications."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from window_helpers import window

from nodvard_deck.config import LOCAL_TIMEZONE
from nodvard_deck.core import maintenance
from nodvard_deck.models import Job
from nodvard_deck.services import settings as settings_service

pytestmark = pytest.mark.usefixtures("window_clock")


@pytest.mark.asyncio
async def test_no_windows_configured_means_never_suppressed(db_session):
    assert await maintenance.is_in_window(db_session, host_id="host-1") is False


@pytest.mark.asyncio
async def test_currently_active_window_suppresses(db_session):
    await settings_service.set_global(db_session, "maintenance.windows", [window(minutes_ago=10)])
    assert await maintenance.is_in_window(db_session, host_id="host-1") is True


@pytest.mark.asyncio
async def test_window_in_the_past_beyond_duration_does_not_suppress(db_session):
    await settings_service.set_global(db_session, "maintenance.windows", [window(minutes_ago=120)])
    assert await maintenance.is_in_window(db_session, host_id="host-1") is False


@pytest.mark.asyncio
async def test_window_scoped_to_specific_hosts(db_session):
    await settings_service.set_global(db_session, "maintenance.windows", [window(["host-a"])])
    assert await maintenance.is_in_window(db_session, host_id="host-a") is True
    assert await maintenance.is_in_window(db_session, host_id="host-b") is False


@pytest.mark.asyncio
async def test_notification_without_host_id_is_never_suppressed_even_during_all_window(db_session):
    await settings_service.set_global(db_session, "maintenance.windows", [window()])
    assert await maintenance.is_in_window(db_session, host_id=None) is False


@pytest.mark.asyncio
async def test_malformed_window_entries_do_not_crash_or_block(db_session):
    await settings_service.set_global(
        db_session, "maintenance.windows",
        [
            {"cron": "not-a-cron", "duration_minutes": 60, "host_ids": "all"},
            {"cron": "0 3 * * *", "duration_minutes": 0, "host_ids": "all"},
            {"host_ids": "all"},
        ],
    )
    assert await maintenance.is_in_window(db_session, host_id="host-1") is False


@pytest.mark.asyncio
async def test_suppression_is_symmetric_regardless_of_notification_content(db_session):
    """`is_in_window()` kennt den Inhalt der Meldung nicht, behandelt „offline“ und „online“ also gleich."""
    await settings_service.set_global(db_session, "maintenance.windows", [window()])
    assert await maintenance.is_in_window(db_session, host_id="host-1") is True
    assert await maintenance.is_in_window(db_session, host_id="host-1") is True


async def _set_running_windows(db_session, *host_scopes) -> None:
    """Je Eintrag ein gerade laufendes Fenster für diese Server."""
    await settings_service.set_global(db_session, "maintenance.windows", [window(scope) for scope in host_scopes])


@pytest.mark.asyncio
async def test_collective_notification_is_silenced_only_when_every_host_is_in_a_running_window(db_session):
    """Sammelmeldung über mehrere Server (`host_ids`): still nur, wenn jeder Server im Fenster liegt."""
    await _set_running_windows(db_session, ["host-a", "host-b"])
    assert await maintenance.is_in_window(db_session, host_id=None, host_ids=["host-a", "host-b"]) is True
    assert await maintenance.is_in_window(db_session, host_id=None, host_ids=["host-a"]) is True
    assert await maintenance.is_in_window(db_session, host_id=None, host_ids=["host-a", "host-c"]) is False


@pytest.mark.asyncio
async def test_collective_notification_can_be_covered_by_several_windows_together(db_session):
    await _set_running_windows(db_session, ["host-a"], ["host-b"])
    assert await maintenance.is_in_window(db_session, host_id=None, host_ids=["host-a", "host-b"]) is True
    assert await maintenance.is_in_window(db_session, host_id=None, host_ids=["host-a", "host-b", "host-c"]) is False


@pytest.mark.asyncio
async def test_collective_notification_under_an_all_hosts_window_is_silenced(db_session):
    await _set_running_windows(db_session, "all")
    assert await maintenance.is_in_window(db_session, host_id=None, host_ids=["host-a", "host-b"]) is True


@pytest.mark.asyncio
async def test_collective_notification_outside_any_running_window_stays_audible(db_session):
    await settings_service.set_global(db_session, "maintenance.windows", [window(minutes_ago=120)])
    assert await maintenance.is_in_window(db_session, host_id=None, host_ids=["host-a", "host-b"]) is False


@pytest.mark.asyncio
async def test_empty_host_list_is_never_suppressed(db_session):
    """Ohne Server kein Bezug, auch kein leeres `all([])`, das alles still schaltete."""
    await _set_running_windows(db_session, "all")
    assert await maintenance.is_in_window(db_session, host_id=None, host_ids=[]) is False
    assert await maintenance.is_in_window(db_session, host_id=None, host_ids=None) is False


@pytest.mark.asyncio
async def test_single_host_id_takes_precedence_over_the_list(db_session):
    await _set_running_windows(db_session, ["host-a"])
    assert await maintenance.is_in_window(db_session, host_id="host-a", host_ids=["host-x"]) is True
    assert await maintenance.is_in_window(db_session, host_id="host-x", host_ids=["host-a"]) is False


@pytest.mark.asyncio
async def test_window_with_a_text_instead_of_a_host_list_covers_no_host(db_session):
    """Ein kaputter Eintrag (`'host_ids': 'some'`) erfasst keinen Server über einen Teilstring-Treffer."""
    await _set_running_windows(db_session, "some-host-a")
    assert await maintenance.is_in_window(db_session, host_id="host-a") is False
    assert await maintenance.is_in_window(db_session, host_id=None, host_ids=["host-a"]) is False


def test_window_cron_is_local_time_not_utc():
    """'taeglich 03:00, 1 Stunde' meint 03:00 Ortszeit. Im Sommer ist
    03:30 Berliner Zeit 01:30 UTC -- das muss im Fenster liegen, 05:30 Berliner
    Zeit (03:30 UTC) dagegen nicht."""
    summer_0330_local = datetime(2026, 7, 15, 1, 30, tzinfo=UTC)
    summer_0530_local = datetime(2026, 7, 15, 3, 30, tzinfo=UTC)
    assert maintenance._window_covers_now("0 3 * * *", 60, summer_0330_local) is True
    assert maintenance._window_covers_now("0 3 * * *", 60, summer_0530_local) is False
    # Winter (MEZ, UTC+1): 03:30 Ortszeit = 02:30 UTC.
    winter_0330_local = datetime(2026, 1, 15, 2, 30, tzinfo=UTC)
    winter_0430_local = datetime(2026, 1, 15, 3, 30, tzinfo=UTC)
    assert maintenance._window_covers_now("0 3 * * *", 60, winter_0330_local) is True
    assert maintenance._window_covers_now("0 3 * * *", 60, winter_0430_local) is False


def test_window_and_jobs_share_one_timezone_source():
    """Wartungsfenster und Jobs rechnen in derselben Zeitzone -- eine Quelle statt
    zweier Strings, die auseinanderlaufen koennen."""
    assert Job.__table__.c.timezone.default.arg == LOCAL_TIMEZONE


def test_window_sunday_covers_sunday_not_monday():
    """Crontab-Wochentag: 0 = Sonntag (wie im Zeitplan-Waehler), nicht Montag."""
    berlin = ZoneInfo("Europe/Berlin")
    assert maintenance._window_covers_now("0 3 * * 0", 60, datetime(2026, 10, 4, 3, 30, tzinfo=berlin)) is True
    assert maintenance._window_covers_now("0 3 * * 0", 60, datetime(2026, 10, 5, 3, 30, tzinfo=berlin)) is False
    assert maintenance._window_covers_now("0 3 * * 7", 60, datetime(2026, 10, 4, 3, 30, tzinfo=berlin)) is True
    # Werktags: Montag ja, Samstag nein.
    assert maintenance._window_covers_now("0 3 * * 1-5", 60, datetime(2026, 10, 5, 3, 30, tzinfo=berlin)) is True
    assert maintenance._window_covers_now("0 3 * * 1-5", 60, datetime(2026, 10, 3, 3, 30, tzinfo=berlin)) is False
