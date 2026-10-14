"""Zeitplan- und Lauf-Verwaltung, reine DB-Operationen; `core/scheduler.py` bindet sie an APScheduler und die Ausführung."""

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
    """Upsert über `(ext_id, ext_job_key)`, damit jedes erneute `register_job()` idempotent ist.
    `timezone=None` heißt: die eingestellte Zone (`core.timezone`). Ein bestehender Job behält seine, sonst setzte jedes
    Neuladen einer Erweiterung eine umgestellte Zone zurück; nur eine ausdrücklich übergebene überschreibt."""
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
    """Spiegelt APSchedulers `next_run_time` (maßgeblich) in `Job.next_run_at`, damit die API den nächsten Termin zeigt."""
    # `astimezone(UTC)` ist nötig: APScheduler liefert den Termin in der Zone des Jobs, `UTCDateTime` normalisiert aber
    # nur naive Werte. Ein aware Wert in anderer Zone verlöre beim Speichern den Offset und läse sich als UTC zurück
    # (aus "0:38 Uhr Berlin" würde "0:38 Uhr UTC").
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
    """Löscht die beendeten Läufe eines Jobs, die vor `older_than` begannen, und liefert die Pfade ihrer Protokolldateien.
    Nötig für Jobs, die sehr oft laufen (Erreichbarkeitsprüfung alle 2 Minuten); laufende Läufe bleiben immer stehen."""
    where = (JobRun.job_id == job_id, JobRun.started_at < older_than, JobRun.status != "running")
    refs = (await session.execute(select(JobRun.output_ref).where(*where))).scalars().all()
    await session.execute(delete(JobRun).where(*where))
    return [ref for ref in refs if ref]


async def get_run(session: AsyncSession, run_id: str) -> JobRun | None:
    return await session.get(JobRun, run_id)


async def mark_interrupted_on_boot(session: AsyncSession) -> int:
    """Markiert beim Start alle noch als `running` geführten Läufe als `interrupted`, damit ein Neustart mitten im Lauf
    sichtbar wird. Muss vor jeder neuen Job-Ausführung laufen (Lifespan), sonst markiert sich ein sofort gestarteter
    echter Lauf selbst."""
    result = await session.execute(
        update(JobRun).where(JobRun.status == "running").values(status="interrupted", finished_at=utcnow())
    )
    await session.flush()
    return result.rowcount or 0
