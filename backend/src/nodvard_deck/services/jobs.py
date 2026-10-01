"""Zeitplan- und Lauf-Verwaltung -- docs/03-DATA-MODEL.md §7.

Reine DB-Operationen. `core/scheduler.py` verdrahtet das mit APScheduler und der
tatsaechlichen Ausfuehrung -- Trennung wie ueberall im Projekt (`services/hosts.py`
vs. `core/ssh.py` ist dasselbe Muster).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.timezone import get_timezone
from ..db import utcnow
from ..models import Job, JobRun


async def upsert_job(
    session: AsyncSession,
    *,
    ext_id: str | None,
    ext_job_key: str,
    name: str,
    kind: str,
    schedule: str,
    params: dict[str, Any],
    enabled: bool,
    timezone: str | None = None,
    target: dict[str, Any] | None = None,
    created_by_user_id: str | None = None,
) -> Job:
    """Upsert ueber `(ext_id, ext_job_key)` -- macht ein erneutes `register_job()`
    (jedes Extension-Enable) idempotent statt einer Blind-Insert-Dopplung
    (docs/00-DECISIONS.md D-08: "der doppelte, ins Leere laufende Cron-Eintrag").

    `timezone=None` heisst: die eingestellte Zone (`core.timezone`, Einstellung
    `system.timezone`). Ein neuer Job bekommt sie; ein bestehender behaelt seine, sonst
    wuerde jedes Neuladen einer Erweiterung eine umgestellte Zone zuruecksetzen. Nur eine
    ausdruecklich uebergebene Zone ueberschreibt."""
    result = await session.execute(
        select(Job).where(Job.ext_id == ext_id, Job.ext_job_key == ext_job_key)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = Job(
            ext_id=ext_id, ext_job_key=ext_job_key, name=name, kind=kind, schedule=schedule,
            timezone=timezone or await get_timezone(session), target=target or {}, params=params, enabled=enabled,
            created_by_user_id=created_by_user_id,
        )
        session.add(row)
    else:
        row.name = name
        row.kind = kind
        row.schedule = schedule
        if timezone is not None:
            row.timezone = timezone
        row.params = params
        row.enabled = enabled
        if target is not None:
            row.target = target
    await session.flush()
    return row


async def list_jobs(
    session: AsyncSession, *, ext_id: str | None = None, enabled: bool | None = None
) -> list[Job]:
    stmt = select(Job).order_by(Job.created_at.desc())
    if ext_id is not None:
        stmt = stmt.where(Job.ext_id == ext_id)
    if enabled is not None:
        stmt = stmt.where(Job.enabled == enabled)
    return list((await session.execute(stmt)).scalars().all())


async def get_job(session: AsyncSession, job_id: str) -> Job | None:
    return await session.get(Job, job_id)


async def set_next_run_at(session: AsyncSession, job_id: str, next_run_at: datetime | None) -> None:
    """`Job.next_run_at`
    existierte seit WP-6 als Spalte, wurde aber vom Scheduler nie beschrieben --
    `GET /jobs` zeigte das Feld dauerhaft als `null`. `core/scheduler.py::schedule()`
    ruft das direkt nach dem Planen bei APScheduler auf (dessen eigenes
    `next_run_time` ist die Wahrheit, hier nur in die `jobs`-Tabelle gespiegelt,
    damit die API es zeigen kann, ohne den Scheduler-Prozessspeicher zu kennen),
    UND nach jedem Lauf erneut (APScheduler berechnet den naechsten Termin danach
    neu).

    **`astimezone(UTC)` ist kein Kosmetik-Detail, live im Regressionstest
    gefunden:** `Job.timezone` ist standardmaessig `"Europe/Berlin"`
    (models/platform.py) -- APSchedulers `next_run_time` kommt deshalb Berlin-aware
    zurueck, nicht UTC. `db.base.UTCDateTime.process_bind_param()` normalisiert nur
    NAIVE Werte auf UTC (nimmt an, sie seien bereits UTC gemeint) -- ein bereits
    aware, aber nicht-UTC Wert wird unveraendert durchgereicht. SQLite serialisiert
    dabei die WALL-CLOCK-Zahlen, verliert den Offset; beim Lesen bekommt der naive
    Rueckgabewert dann faelschlich `tzinfo=UTC` aufgeklebt -- aus "0:38 Uhr Berlin"
    wird stillschweigend "0:38 Uhr UTC", zwei verschiedene Momente. Ohne die
    explizite Konvertierung hier waere `next_run_at` fuer JEDEN Job mit
    Nicht-UTC-Zeitzone (dem Standardfall) falsch, sobald Berlin nicht UTC+0 ist."""
    if next_run_at is not None:
        next_run_at = next_run_at.astimezone(UTC)
    job = await session.get(Job, job_id)
    if job is not None:
        job.next_run_at = next_run_at


async def get_job_by_key(session: AsyncSession, *, ext_id: str | None, ext_job_key: str) -> Job | None:
    result = await session.execute(
        select(Job).where(Job.ext_id == ext_id, Job.ext_job_key == ext_job_key)
    )
    return result.scalar_one_or_none()


async def create_run(
    session: AsyncSession,
    *,
    job_id: str | None,
    ext_id: str | None,
    trigger: str,
    host_id: str | None = None,
    actor: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> JobRun:
    row = JobRun(
        job_id=job_id, ext_id=ext_id, trigger=trigger, status="running", host_id=host_id,
        actor=actor or {}, correlation_id=correlation_id,
    )
    session.add(row)
    await session.flush()
    return row


async def finish_run(
    session: AsyncSession,
    run: JobRun,
    *,
    status: str,
    exit_code: int | None = None,
    error: str | None = None,
    output_ref: str | None = None,
) -> None:
    run.status = status
    run.exit_code = exit_code
    run.error = error
    if output_ref is not None:
        run.output_ref = output_ref
    run.finished_at = utcnow()
    await session.flush()


async def list_runs(session: AsyncSession, *, job_id: str | None = None, limit: int = 100) -> list[JobRun]:
    stmt = select(JobRun).order_by(JobRun.started_at.desc()).limit(min(limit, 1000))
    if job_id is not None:
        stmt = stmt.where(JobRun.job_id == job_id)
    return list((await session.execute(stmt)).scalars().all())


async def prune_runs(session: AsyncSession, *, job_id: str, older_than: datetime) -> list[str]:
    """Loescht die beendeten Laeufe eines Jobs, die vor `older_than` begannen, und liefert die
    Pfade ihrer Protokolldateien (`output_ref`), damit der Aufrufer sie ebenfalls entfernt.
    Fuer Jobs, die sehr oft laufen (Erreichbarkeitspruefung alle 2 Minuten): ohne Aufraeumen
    wuerde jeder Lauf fuer immer eine Zeile und eine Datei hinterlassen. Laufende Laeufe
    bleiben immer stehen."""
    where = (JobRun.job_id == job_id, JobRun.started_at < older_than, JobRun.status != "running")
    refs = (await session.execute(select(JobRun.output_ref).where(*where))).scalars().all()
    await session.execute(delete(JobRun).where(*where))
    return [ref for ref in refs if ref]


async def get_run(session: AsyncSession, run_id: str) -> JobRun | None:
    return await session.get(JobRun, run_id)


async def mark_interrupted_on_boot(session: AsyncSession) -> int:
    """docs/03 §7: "interrupted existiert, damit ein Neustart mitten im Lauf sichtbar
    wird statt zu verschwinden -- beim Start markiert der Kern alle noch als running
    gefuehrten Laeufe um." Muss VOR jeder neuen Job-Ausfuehrung laufen (main.py
    Lifespan), sonst markiert ein sofort danach gestarteter echter Lauf sich selbst."""
    result = await session.execute(
        update(JobRun).where(JobRun.status == "running").values(status="interrupted", finished_at=utcnow())
    )
    await session.flush()
    return result.rowcount or 0
