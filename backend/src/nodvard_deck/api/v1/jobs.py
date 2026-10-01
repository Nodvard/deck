"""Zeitplan und Laeufe -- docs/04-API.md.

**Bewusst NICHT Teil dieser Runde:** `POST /jobs` (einen brandneuen Job direkt ueber
die API anlegen). docs/04 nennt den Endpunkt, aber es gibt in Phase 1 keinen
generischen, von der API aus adressierbaren "Handler" -- ein Job braucht IMMER eine
Extension (oder den Kern), die `ctx.scheduler.register_job()` mit einem echten
Python-Handler aufruft (docs/02 §2 `JobSpec.handler`). Ein Job "aus dem Nichts" ohne
Code dahinter waere ein Platzhalter ohne Wirkung. Das Script-Repository (docs/02 §9),
das genau diese Luecke fuellen wuerde (ein Job = ein Skript), ist keine Phase-1-
Core-WP. `GET/PATCH/DELETE /jobs/{id}` und `POST /jobs/{id}/run` funktionieren bereits
fuer jeden Job, den eine Extension (oder der Kern, siehe `core/scheduler.py
register_core_jobs()`) registriert hat.

Permission: `jobs.read` zum Lesen, `jobs.run` zum Ausloesen/Abbrechen (beide schon in
WP-1s `BUILTIN_ROLES` fuer `operator`/`viewer` gesetzt, `core/rbac.py`), `jobs.write`
zum Aendern/Loeschen des Zeitplans selbst (neu, nur `admin` hat es ueber `"*"` -- eine
bewusst engere Voreinstellung, weil das Schema (nicht nur die Ausfuehrung) aendert).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ...core.scheduler import CORE_SCHEDULER_EXT_ID, get_scheduler_service
from ...ext.runtime import get_extension_runtime
from ...models import Job, JobRun
from ...services import jobs as jobs_service
from ...services.backups import JOB_KEY as BACKUP_JOB_KEY
from ...services.reachability import JOB_KEY as REACHABILITY_JOB_KEY
from ...services.update_check import JOB_KEY as UPDATE_CHECK_JOB_KEY
from ..deps import SessionDep, require_permission

router = APIRouter(tags=["jobs"])

_OWNER_ONLY_CORE_JOBS = frozenset({BACKUP_JOB_KEY})
"""Kern-Jobs, deren Schalter nur der Owner unter Einstellungen -> System umlegt
(`services.backups`). Ueber `jobs.write` (Admin) duerften automatische Sicherungen sonst
still pausiert oder geloescht werden, waehrend die Oberflaeche "an" zeigt."""


_SETTINGS_CORE_JOBS: dict[str, str] = {
    REACHABILITY_JOB_KEY: "Diesen Job stellst du unter Einstellungen → Server & Zugänge ein.",
    UPDATE_CHECK_JOB_KEY: "Diesen Job stellst du unter Einstellungen → System → Updates ein; dort gibt es auch „Jetzt suchen“.",
}
"""Kern-Jobs, deren Schalter und Zeitplan aus einer Einstellung kommen (`hosts.reachability.*`,
`system.update_check.enabled`), mit dem Hinweis, wo man sie einstellt: ein Umlegen hier wuerde beim
naechsten Start oder bei der naechsten Aenderung der Einstellung still zurueckgesetzt."""


def _refuse_owner_only(job: Job) -> None:
    if job.ext_id is None and job.ext_job_key in _OWNER_ONLY_CORE_JOBS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diesen Job steuert der Owner unter Einstellungen → System → Sicherung.",
        )
    if job.ext_id is None and job.ext_job_key in _SETTINGS_CORE_JOBS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_SETTINGS_CORE_JOBS[job.ext_job_key])


class JobOut(BaseModel):
    id: str
    ext_id: str | None
    ext_job_key: str | None
    name: str
    kind: str
    schedule: str
    timezone: str
    target: dict[str, Any]
    params: dict[str, Any]
    enabled: bool
    next_run_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, j: Job) -> "JobOut":
        return cls(
            id=j.id, ext_id=j.ext_id, ext_job_key=j.ext_job_key, name=j.name, kind=j.kind,
            schedule=j.schedule, timezone=j.timezone, target=j.target, params=j.params,
            enabled=j.enabled, next_run_at=j.next_run_at, created_at=j.created_at,
        )


class JobPatch(BaseModel):
    enabled: bool | None = None


class RunOut(BaseModel):
    id: str
    job_id: str | None
    ext_id: str | None
    trigger: str
    status: str
    host_id: str | None
    started_at: datetime
    finished_at: datetime | None
    exit_code: int | None
    error: str | None
    correlation_id: str | None

    @classmethod
    def from_model(cls, r: JobRun) -> "RunOut":
        return cls(
            id=r.id, job_id=r.job_id, ext_id=r.ext_id, trigger=r.trigger, status=r.status,
            host_id=r.host_id, started_at=r.started_at, finished_at=r.finished_at,
            exit_code=r.exit_code, error=r.error, correlation_id=r.correlation_id,
        )


@router.get("/jobs", dependencies=[Depends(require_permission("jobs.read"))])
async def list_jobs(session: SessionDep, ext_id: str | None = None, enabled: bool | None = None) -> list[JobOut]:
    rows = await jobs_service.list_jobs(session, ext_id=ext_id, enabled=enabled)
    return [JobOut.from_model(j) for j in rows]


@router.get("/jobs/{job_id}", dependencies=[Depends(require_permission("jobs.read"))])
async def get_job(job_id: str, session: SessionDep) -> JobOut:
    job = await jobs_service.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Job.")
    return JobOut.from_model(job)


@router.patch("/jobs/{job_id}", dependencies=[Depends(require_permission("jobs.write"))])
async def patch_job(job_id: str, payload: JobPatch, session: SessionDep) -> JobOut:
    """**Ehrlich abgegrenzt:** ein erneutes Laden der registrierenden Extension
    (`register_job()`s Upsert) ueberschreibt `enabled` wieder mit dem, was die
    Extension selbst deklariert -- eine manuelle Pause hier ueberlebt keinen
    Extension-Reload. Ein "manuelle Aenderung gewinnt"-Abgleich ist eine eigene
    Entscheidung, hier nicht getroffen."""
    job = await jobs_service.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Job.")
    if payload.enabled is not None:
        _refuse_owner_only(job)
        if not payload.enabled:
            job.enabled = False
            await session.flush()
            get_scheduler_service().unschedule(job.id)
            job.next_run_at = None
        else:
            registry_ext_id = job.ext_id if job.ext_id is not None else CORE_SCHEDULER_EXT_ID
            handler = get_extension_runtime().scheduler.get(registry_ext_id, job.ext_job_key or "")
            if handler is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Kein Handler geladen -- der Besitzer (Extension oder Kern) muss dafür laufen.",
                )
            job.enabled = True
            # D-14: schedule() schreibt next_run_at ueber eine EIGENE
            # session_scope() -- vorher committen, sonst wartet die auf unsere eigene
            # Schreibsperre (busy_timeout 5 s), und am Ende steht HTTP 500 mit
            # zurueckgerolltem enabled, obwohl APScheduler den Job schon eingeplant hat.
            await session.commit()
            await get_scheduler_service().schedule(job, handler)
            await session.refresh(job)  # next_run_at kommt aus der anderen Session
    return JobOut.from_model(job)


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_permission("jobs.write"))])
async def delete_job(job_id: str, session: SessionDep) -> None:
    job = await jobs_service.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Job.")
    _refuse_owner_only(job)
    get_scheduler_service().unschedule(job_id)
    await session.delete(job)
    await session.flush()


@router.post("/jobs/{job_id}/run", dependencies=[Depends(require_permission("jobs.run"))])
async def run_job_now(job_id: str, session: SessionDep) -> RunOut:
    job = await jobs_service.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Job.")
    if job.ext_id is None:
        # Der Job "Nach Updates suchen" hat seinen eigenen Knopf mit Minuten-Drossel (`POST /system/updates/check`);
        # ein Weg daran vorbei wuerde die Anfragen an ghcr.io nicht bremsen.
        detail = (
            _SETTINGS_CORE_JOBS[job.ext_job_key]
            if job.ext_job_key == UPDATE_CHECK_JOB_KEY
            else "Kern-Jobs können in dieser Runde nicht manuell ausgelöst werden."
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    handler = get_extension_runtime().scheduler.get(job.ext_id, job.ext_job_key or "")
    if handler is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Extension '{job.ext_id}' ist nicht (mehr) geladen -- kein Handler verfügbar.",
        )
    run_id = await get_scheduler_service().trigger_now(job, handler)
    run = await jobs_service.get_run(session, run_id)
    assert run is not None
    return RunOut.from_model(run)


@router.get("/jobs/{job_id}/runs", dependencies=[Depends(require_permission("jobs.read"))])
async def list_job_runs(job_id: str, session: SessionDep, limit: int = 100) -> list[RunOut]:
    rows = await jobs_service.list_runs(session, job_id=job_id, limit=limit)
    return [RunOut.from_model(r) for r in rows]


@router.get("/runs/{run_id}", dependencies=[Depends(require_permission("jobs.read"))])
async def get_run(run_id: str, session: SessionDep) -> RunOut:
    run = await jobs_service.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Lauf.")
    return RunOut.from_model(run)


@router.get("/runs/{run_id}/output", dependencies=[Depends(require_permission("jobs.read"))])
async def get_run_output(run_id: str, session: SessionDep) -> dict[str, str]:
    run = await jobs_service.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Lauf.")
    if run.output_ref is None:
        return {"output": ""}
    path = Path(run.output_ref)
    if not path.is_file():
        return {"output": ""}
    return {"output": path.read_text(encoding="utf-8")}


@router.post("/runs/{run_id}/cancel", dependencies=[Depends(require_permission("jobs.run"))])
async def cancel_run(run_id: str, session: SessionDep) -> dict[str, bool]:
    run = await jobs_service.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Lauf.")
    cancelled = get_scheduler_service().cancel_run(run_id)
    if not cancelled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Lauf ist nicht (mehr) aktiv.")
    return {"cancelled": True}
