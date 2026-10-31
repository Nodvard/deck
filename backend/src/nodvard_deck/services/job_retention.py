"""Alte Job-Läufe und ihre Protokolldateien aufräumen (Kern-Job `job-runs-retention`). Gelöscht wird, was beendet ist
und vor mehr als `jobs.run_retention_days` Tagen begann (1 bis 3650, Vorgabe 30); laufende Läufe bleiben immer. Das
Aufräumen der Erreichbarkeitsprüfung (`services.jobs.prune_runs`) bleibt davon unberührt."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..models import JobRun
from . import settings as settings_service
from .jobs import remove_run_file

logger = logging.getLogger("nodvard_deck.job_retention")

RETENTION_KEY = "jobs.run_retention_days"
RETENTION_MIN_DAYS = 1
RETENTION_MAX_DAYS = 3650
DEFAULT_RETENTION_DAYS = 30
# Pro Job bleiben die letzten beendeten Läufe stehen, auch wenn sie älter sind: Ein seltener Job (monatlich) behält so
# seinen Verlauf. Läufe ohne Job (gelöscht, Ad-hoc) gehen nur nach Alter.
KEEP_PER_JOB = 20
# Häppchen mit Commit und kurzer Pause: SQLite hat nur einen Schreiber, ein langer Löschlauf ließe Anmeldung und
# Zeitpläne bis zum `busy_timeout` warten.
CHUNK_SIZE = 5000
CHUNK_PAUSE_SECONDS = 0.05
_DELETE_BATCH = 500
"""Zeilen je `DELETE ... IN (...)`: bleibt weit unter dem Variablen-Limit alter SQLite-Versionen (999)."""
_ORPHAN_BATCH = 400


@dataclass
class PurgeResult:
    runs_deleted: int = 0
    files_deleted: int = 0
    orphans_deleted: int = 0
    chunks: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "runs_deleted": self.runs_deleted, "files_deleted": self.files_deleted,
            "orphans_deleted": self.orphans_deleted, "chunks": self.chunks,
        }


def is_valid_retention(value: object) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool)
        and RETENTION_MIN_DAYS <= value <= RETENTION_MAX_DAYS
    )


async def effective_retention_days(session: AsyncSession) -> int:
    """Aufbewahrung des Job-Verlaufs in Tagen: die Einstellung `jobs.run_retention_days`, sonst 30. Ein Wert außerhalb
    von 1 bis 3650 (von Hand in die Tabelle geschrieben) zählt als nicht gesetzt, damit der Verlauf nicht fast leer
    geräumt wird."""
    stored = await settings_service.get_global(session, RETENTION_KEY)
    return stored if is_valid_retention(stored) else DEFAULT_RETENTION_DAYS


def _unlink_all(runs_dir: Path, refs: list[str]) -> int:
    return sum(1 for ref in refs if remove_run_file(runs_dir, ref))


async def _purge_job(
    session: AsyncSession, job_id: str | None, *, cutoff: datetime, keep: int, chunk_size: int,
    pause: float, runs_dir: Path, result: PurgeResult,
) -> None:
    """Alle abgelaufenen, beendeten Läufe eines Jobs (bei `None`: der Läufe ohne Job)."""
    protected: list[str] = []
    if job_id is not None and keep > 0:
        newest = select(JobRun.id).where(JobRun.job_id == job_id, JobRun.status != "running").order_by(
            JobRun.started_at.desc(), JobRun.id.desc()
        ).limit(keep)
        protected = list((await session.execute(newest)).scalars().all())
    where = [
        JobRun.job_id == job_id if job_id is not None else JobRun.job_id.is_(None),
        JobRun.status != "running", JobRun.started_at < cutoff,
    ]
    if protected:
        where.append(JobRun.id.not_in(protected))
    while True:
        rows = (
            await session.execute(
                select(JobRun.id, JobRun.output_ref).where(*where).order_by(JobRun.started_at.asc(), JobRun.id.asc())
                .limit(chunk_size)
            )
        ).all()
        if not rows:
            return
        ids = [row.id for row in rows]
        for start in range(0, len(ids), _DELETE_BATCH):
            await session.execute(
                delete(JobRun).where(JobRun.id.in_(ids[start:start + _DELETE_BATCH]), JobRun.status != "running")
            )
        # Dateien erst nach dem Commit der Zeilen löschen, nie davor; bleibt eine liegen, fängt sie der Waisen-Durchlauf
        # ein.
        await session.commit()
        result.runs_deleted += len(ids)
        result.chunks += 1
        refs = [row.output_ref for row in rows if row.output_ref]
        if refs:
            result.files_deleted += await asyncio.to_thread(_unlink_all, runs_dir, refs)
        if len(rows) < chunk_size:
            return
        # Anderen Schreibern (Anmeldung, Zeitpläne) eine Lücke lassen.
        await asyncio.sleep(pause)


def _candidate_orphans(runs_dir: Path, cutoff: datetime) -> list[str]:
    """Namen der `*.log`-Dateien direkt in `runs_dir`, die vor `cutoff` zuletzt geändert wurden."""
    limit = cutoff.timestamp()
    found: list[str] = []
    try:
        with os.scandir(runs_dir) as entries:
            for entry in entries:
                if not entry.name.endswith(".log"):
                    continue
                try:
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        continue
                    if entry.stat(follow_symlinks=False).st_mtime < limit:
                        found.append(entry.name)
                except OSError:
                    continue
    except OSError:
        return []
    return found


async def _purge_orphans(session: AsyncSession, runs_dir: Path, cutoff: datetime, result: PurgeResult) -> None:
    """Löscht `*.log`-Dateien in `runs_dir`, zu denen keine Zeile gehört (weder Datei-ID noch `output_ref`) und die
    älter als die Aufbewahrung sind. Symlinks und Unterordner bleiben unangetastet."""
    names = await asyncio.to_thread(_candidate_orphans, runs_dir, cutoff)
    for start in range(0, len(names), _ORPHAN_BATCH):
        batch = names[start:start + _ORPHAN_BATCH]
        stems = {name[: -len(".log")]: name for name in batch}
        paths = {str(runs_dir / name): name for name in batch}
        known_ids = (await session.execute(select(JobRun.id).where(JobRun.id.in_(list(stems))))).scalars().all()
        known_refs = (
            await session.execute(select(JobRun.output_ref).where(JobRun.output_ref.in_(list(paths))))
        ).scalars().all()
        keep = {stems[i] for i in known_ids} | {paths[r] for r in known_refs}
        doomed = [str(runs_dir / name) for name in batch if name not in keep]
        if doomed:
            result.orphans_deleted += await asyncio.to_thread(_unlink_all, runs_dir, doomed)
        # Kein Schreiben in dieser Phase, aber die Lesetransaktion nicht über den ganzen Ordner offen halten.
        await session.commit()


async def purge_old_runs(
    session: AsyncSession, *, runs_dir: Path, retention_days: int, keep_per_job: int = KEEP_PER_JOB,
    chunk_size: int = CHUNK_SIZE, pause: float = CHUNK_PAUSE_SECONDS, now: datetime | None = None,
) -> PurgeResult:
    """Löscht abgelaufene Läufe samt Dateien, danach Waisen; committet selbst nach jedem Häppchen."""
    runs_dir = Path(runs_dir)
    cutoff = (now or utcnow()) - timedelta(days=retention_days)
    result = PurgeResult()
    job_ids = (
        await session.execute(
            select(JobRun.job_id).where(
                JobRun.status != "running", JobRun.started_at < cutoff, JobRun.job_id.is_not(None)
            ).distinct()
        )
    ).scalars().all()
    for job_id in [*job_ids, None]:
        await _purge_job(
            session, job_id, cutoff=cutoff, keep=keep_per_job, chunk_size=chunk_size, pause=pause,
            runs_dir=runs_dir, result=result,
        )
    # Kein VACUUM: Die Datenbank läuft im WAL-Modus mit `auto_vacuum=NONE`, freie Seiten nimmt SQLite für neue Zeilen
    # wieder. Ein volles VACUUM (zum Umstellen nötig) schriebe alles neu, brauchte bis zu doppelten Platz auf der
    # SD-Karte und hielte die Schreibsperre.
    await _purge_orphans(session, runs_dir, cutoff, result)
    if result.runs_deleted or result.orphans_deleted:
        logger.info("job_runs_purged %s", result.as_dict())
    return result


async def run_purge(runs_dir: Path) -> dict[str, int]:
    """Der Kern-Job: Aufbewahrung aus den Einstellungen lesen und aufräumen."""
    from ..db.session import session_scope

    async with session_scope() as session:
        days = await effective_retention_days(session)
        result = await purge_old_runs(session, runs_dir=runs_dir, retention_days=days)
    return {"retention_days": days, **result.as_dict()}


__all__ = [
    "CHUNK_SIZE", "DEFAULT_RETENTION_DAYS", "KEEP_PER_JOB", "PurgeResult", "RETENTION_KEY",
    "RETENTION_MAX_DAYS", "RETENTION_MIN_DAYS", "effective_retention_days", "is_valid_retention",
    "purge_old_runs", "run_purge",
]
