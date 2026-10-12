"""Alte Meldungen aufraeumen (`services/notification_retention.py`, Kern-Job `notifications-retention`)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, insert, select, text, update

from nodvard_deck import config
from nodvard_deck.config import Settings
from nodvard_deck.core.scheduler import (
    CORE_SCHEDULER_EXT_ID, NOTIFICATIONS_RETENTION_KEY, get_scheduler_service, register_core_jobs,
)
from nodvard_deck.db import utcnow
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import Notification, NotificationDelivery
from nodvard_deck.services import jobs as jobs_service
from nodvard_deck.services import notification_retention
from nodvard_deck.services import settings as settings_service

DAY = timedelta(days=1)
NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=utcnow().tzinfo)


def _note(*, age: timedelta, read: bool = False, title: str = "T", severity: str = "info") -> dict:
    return {
        "id": str(uuid.uuid4()), "ts": NOW - age, "severity": severity, "title": title, "body": "x",
        "read_at": (NOW - age) if read else None, "payload": {},
    }


async def _add(session, rows: list[dict], *, deliveries: int = 0) -> None:
    await session.execute(insert(Notification), rows)
    if deliveries:
        await session.execute(
            insert(NotificationDelivery),
            [
                {"id": str(uuid.uuid4()), "notification_id": row["id"], "channel_id": f"kanal-{n}", "status": "sent"}
                for row in rows for n in range(deliveries)
            ],
        )
    await session.commit()


async def _ids(session) -> set[str]:
    return set((await session.execute(select(Notification.id))).scalars().all())


async def _delivery_count(session) -> int:
    return (await session.execute(select(func.count()).select_from(NotificationDelivery))).scalar_one()


async def _purge(session, *, days: int = 90, **kwargs):
    kwargs.setdefault("pause", 0)
    kwargs.setdefault("now", NOW)
    return await notification_retention.purge_old_notifications(session, retention_days=days, **kwargs)


@pytest.mark.asyncio
async def test_only_notifications_older_than_the_limit_go_and_the_limit_itself_stays(db_session):
    exactly = _note(age=90 * DAY)  # genau 90 Tage alt: bleibt (nur "aelter als" geht)
    just_over = _note(age=90 * DAY + timedelta(seconds=1))
    just_under = _note(age=90 * DAY - timedelta(seconds=1))
    today = _note(age=timedelta(minutes=5))
    old = _note(age=400 * DAY)
    await _add(db_session, [exactly, just_over, just_under, today, old])

    result = await _purge(db_session)

    assert result.deleted == 2
    assert await _ids(db_session) == {exactly["id"], just_under["id"], today["id"]}


@pytest.mark.asyncio
async def test_unread_notifications_are_deleted_too(db_session):
    unread = _note(age=120 * DAY, read=False)
    read = _note(age=120 * DAY, read=True)
    fresh_unread = _note(age=1 * DAY, read=False)
    await _add(db_session, [unread, read, fresh_unread])

    result = await _purge(db_session)

    assert result.deleted == 2
    assert await _ids(db_session) == {fresh_unread["id"]}


@pytest.mark.asyncio
async def test_an_unread_critical_notification_stays_but_a_read_one_goes(db_session):
    unread_critical = _note(age=200 * DAY, read=False, severity="critical")
    read_critical = _note(age=200 * DAY, read=True, severity="critical")
    unread_warning = _note(age=200 * DAY, read=False, severity="warning")
    read_warning = _note(age=200 * DAY, read=True, severity="warning")
    unread_info = _note(age=200 * DAY, read=False, severity="info")
    fresh_critical = _note(age=2 * DAY, read=False, severity="critical")
    await _add(
        db_session, [unread_critical, read_critical, unread_warning, read_warning, unread_info, fresh_critical],
        deliveries=1,
    )

    result = await _purge(db_session)

    assert result.deleted == 4
    assert await _ids(db_session) == {unread_critical["id"], fresh_critical["id"]}
    remaining = set((await db_session.execute(select(NotificationDelivery.notification_id))).scalars().all())
    assert remaining == {unread_critical["id"], fresh_critical["id"]}, "die Zustellprotokolle bleiben bei der Meldung"

    # Wird sie gelesen, geht sie beim naechsten Lauf mit.
    await db_session.execute(update(Notification).where(Notification.id == unread_critical["id"]).values(read_at=NOW))
    await db_session.commit()
    assert (await _purge(db_session)).deleted == 1
    assert await _ids(db_session) == {fresh_critical["id"]}


@pytest.mark.asyncio
async def test_unread_critical_ones_do_not_stop_the_chunks_or_loop_forever(db_session):
    """Sie stehen nicht in der Auswahl: die Haeppchen zaehlen nur, was wirklich weg soll."""
    keep = [_note(age=(300 + i) * DAY, severity="critical") for i in range(5)]  # aelter als alles andere
    gone = [_note(age=(100 + i) * DAY, severity="warning") for i in range(5)]
    await _add(db_session, keep + gone)

    result = await _purge(db_session, chunk_size=2)

    assert result.deleted == 5 and result.chunks == 3  # 2 + 2 + 1
    assert await _ids(db_session) == {row["id"] for row in keep}


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_keys", [False, True])
async def test_the_delivery_logs_go_with_their_notifications(db_session, foreign_keys):
    """Mit und ohne Fremdschluessel-Pruefung der Verbindung: keine verwaiste Zeile, kein Fehler."""
    if foreign_keys:
        await db_session.execute(text("PRAGMA foreign_keys=ON"))
        assert (await db_session.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
    old = [_note(age=100 * DAY) for _ in range(3)]
    fresh = [_note(age=2 * DAY) for _ in range(2)]
    await _add(db_session, old, deliveries=2)
    await _add(db_session, fresh, deliveries=2)
    assert await _delivery_count(db_session) == 10

    result = await _purge(db_session)

    assert result.deleted == 3
    assert await _ids(db_session) == {row["id"] for row in fresh}
    assert await _delivery_count(db_session) == 4
    remaining = set((await db_session.execute(select(NotificationDelivery.notification_id))).scalars().all())
    assert remaining == {row["id"] for row in fresh}


@pytest.mark.asyncio
async def test_deleting_happens_in_chunks_with_a_commit_in_between(db_session):
    await _add(db_session, [_note(age=(100 + i) * DAY) for i in range(10)] + [_note(age=DAY)], deliveries=1)

    result = await _purge(db_session, chunk_size=3)

    assert result.deleted == 10
    assert result.chunks == 4  # 3 + 3 + 3 + 1
    assert len(await _ids(db_session)) == 1
    assert await _delivery_count(db_session) == 1


@pytest.mark.asyncio
async def test_chunks_commit_one_by_one_so_other_writers_are_not_locked_out(db_session, monkeypatch):
    await _add(db_session, [_note(age=(100 + i) * DAY) for i in range(7)])
    commits: list[int] = []
    original = db_session.commit

    async def counting_commit():
        commits.append(len((await db_session.execute(select(Notification.id))).scalars().all()))
        await original()

    monkeypatch.setattr(db_session, "commit", counting_commit)

    await _purge(db_session, chunk_size=2)

    # Vor jedem Commit ist das Haeppchen schon weg: 7 -> 5 -> 3 -> 1 -> 0 (das letzte Haeppchen hat nur eine Zeile).
    assert commits == [5, 3, 1, 0]


@pytest.mark.asyncio
async def test_nothing_to_do_changes_nothing(db_session):
    keep = [_note(age=DAY), _note(age=89 * DAY, read=True)]
    await _add(db_session, keep, deliveries=1)

    result = await _purge(db_session)

    assert (result.deleted, result.chunks) == (0, 0)
    assert await _ids(db_session) == {row["id"] for row in keep}
    assert await _delivery_count(db_session) == 2


@pytest.mark.asyncio
async def test_effective_retention_reads_the_setting_and_ignores_broken_values(db_session):
    assert notification_retention.DEFAULT_RETENTION_DAYS == 90
    assert await notification_retention.effective_retention_days(db_session) == 90
    await settings_service.set_global(db_session, notification_retention.RETENTION_KEY, 7)
    assert await notification_retention.effective_retention_days(db_session) == 7
    for broken in (0, -1, 3651, "7", True, 7.5, None):
        await settings_service.set_global(db_session, notification_retention.RETENTION_KEY, broken)
        assert await notification_retention.effective_retention_days(db_session) == 90, broken


@pytest.mark.asyncio
async def test_the_core_job_is_registered_nightly_with_its_own_minute_and_does_the_cleanup(
    db_session, tmp_path, monkeypatch
):
    reset_extension_runtime()
    monkeypatch.setattr(config, "_settings", Settings(data_dir=tmp_path))
    await register_core_jobs(tmp_path / "runs")

    job = await jobs_service.get_job_by_key(db_session, ext_id=None, ext_job_key=NOTIFICATIONS_RETENTION_KEY)
    assert job is not None and job.kind == "core" and job.enabled
    fields = job.schedule.split()
    assert fields[2:] == ["*", "*", "*"] and fields[1] == "3"  # einmal pro Nacht
    assert get_scheduler_service()._scheduler.get_job(job.id) is not None
    handler = get_extension_runtime().scheduler.get(CORE_SCHEDULER_EXT_ID, NOTIFICATIONS_RETENTION_KEY)
    assert handler is not None

    # Die Aufraeum-Jobs laufen zu verschiedenen Minuten (SQLite hat nur einen Schreiber).
    minutes: dict[str, str] = {}
    for key in ("audit-retention-purge", "job-runs-retention", "restore-cleanup", NOTIFICATIONS_RETENTION_KEY):
        other = await jobs_service.get_job_by_key(db_session, ext_id=None, ext_job_key=key)
        assert other is not None, key
        minutes[key] = other.schedule.split()[0]
    assert len(set(minutes.values())) == len(minutes), minutes

    real_now = utcnow()
    rows = [
        {**_note(age=timedelta(0), title="alt-gelesen", read=True), "ts": real_now - 100 * DAY},
        {**_note(age=timedelta(0), title="alt-ungelesen"), "ts": real_now - 91 * DAY},
        {**_note(age=timedelta(0), title="neu"), "ts": real_now - 89 * DAY},
    ]
    await _add(db_session, rows, deliveries=1)

    outcome = await handler()  # ohne Einstellung: 90 Tage

    assert outcome == {"retention_days": 90, "deleted": 2, "chunks": 1}
    assert await _ids(db_session) == {rows[2]["id"]}
    assert await _delivery_count(db_session) == 1

    await settings_service.set_global(db_session, notification_retention.RETENTION_KEY, 30)
    await db_session.commit()
    outcome = await handler()
    assert outcome == {"retention_days": 30, "deleted": 1, "chunks": 1}
    assert await _ids(db_session) == set()


@pytest.mark.asyncio
async def test_the_core_job_uses_the_default_when_the_stored_value_is_broken(db_session, tmp_path, monkeypatch):
    reset_extension_runtime()
    monkeypatch.setattr(config, "_settings", Settings(data_dir=tmp_path))
    await register_core_jobs(tmp_path / "runs")
    handler = get_extension_runtime().scheduler.get(CORE_SCHEDULER_EXT_ID, NOTIFICATIONS_RETENTION_KEY)

    real_now = utcnow()
    old = {**_note(age=timedelta(0)), "ts": real_now - 95 * DAY}
    recent = {**_note(age=timedelta(0)), "ts": real_now - 80 * DAY}
    await _add(db_session, [old, recent])
    # Von Hand kaputt in die Tabelle geschrieben: 0 Tage haette alles geloescht, 7 nichts Falsches getroffen.
    await settings_service.set_global(db_session, notification_retention.RETENTION_KEY, 0)
    await db_session.commit()

    outcome = await handler()

    assert outcome["retention_days"] == 90 and outcome["deleted"] == 1
    assert await _ids(db_session) == {recent["id"]}
