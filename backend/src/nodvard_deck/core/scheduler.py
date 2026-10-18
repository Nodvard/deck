"""Der Kern-Scheduler: APScheduler mit Speicher im Prozess, dauerhaft ist nur die Tabelle `jobs`."""

from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nodvard_sdk.context import _JOB_TRIGGER
from nodvard_sdk.errors import NodvardError

from ..db import utcnow
from ..db.session import session_scope
from ..models import Job, JobRun
from ..services import jobs as jobs_service
from .cron import cron_trigger

logger = logging.getLogger("nodvard_deck.scheduler")

Handler = Callable[..., Awaitable[Any]]


# Bewusst ohne den SQLAlchemy-Jobstore von APScheduler: Er speichert Jobs per Pickle, ein `JobSpec.handler` ist aber eine
# gebundene Methode einer Extension-Instanz, die bei jedem Start neu entsteht; zwei dauerhafte Quellen liefen außerdem
# auseinander. Erweiterungen planen Jobs nach einem Neustart in `setup()` neu ein, Kern-Jobs über `register_core_jobs()`.
class SchedulerService:
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._runs_dir = Path("./data/runs")
        self._running_tasks: dict[str, asyncio.Task] = {}

    @property
    def runs_dir(self) -> Path:
        return self._runs_dir

    def configure(self, runs_dir: Path) -> None:
        self._runs_dir = runs_dir
        self._runs_dir.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    async def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        for task in list(self._running_tasks.values()):
            task.cancel()

    async def schedule(self, job: Job, handler: Handler) -> None:
        """Fügt den Job hinzu oder ersetzt ihn (`replace_existing`), damit ein erneutes `register_job()` auch hier
        idempotent ist, und spiegelt den nächsten Termin nach `Job.next_run_at`."""
        if not job.enabled:
            self.unschedule(job.id)
            async with session_scope() as session:
                await jobs_service.set_next_run_at(session, job.id, None)
            return
        trigger = cron_trigger(job.schedule, timezone=job.timezone)
        self._scheduler.add_job(
            self._run_job,
            trigger=trigger,
            id=job.id,
            replace_existing=True,
            kwargs={
                "job_id": job.id, "ext_id": job.ext_id, "handler": handler,
                "params": job.params, "trigger_kind": "schedule",
            },
            misfire_grace_time=60,
        )
        # Der Trigger statt `next_run_time` des neuen Jobs: Die Jobs werden vor dem Start des Schedulers angelegt, und bis
        # dahin hat das Job-Objekt dieses Attribut gar nicht (AttributeError).
        next_run_at = trigger.get_next_fire_time(None, utcnow())
        async with session_scope() as session:
            await jobs_service.set_next_run_at(session, job.id, next_run_at)

    def unschedule(self, job_id: str) -> None:
        try:
            self._scheduler.remove_job(job_id)
        except Exception:  # noqa: BLE001 - apscheduler.JobLookupError, wenn nie geplant
            pass

    async def unschedule_extension(self, ext_id: str) -> None:
        async with session_scope() as session:
            rows = await jobs_service.list_jobs(session, ext_id=ext_id)
        for row in rows:
            self.unschedule(row.id)

    async def trigger_now(
        self, job: Job, handler: Handler, *, params: dict[str, Any] | None = None
    ) -> str:
        return await self._run_job(
            job_id=job.id, ext_id=job.ext_id, handler=handler,
            params=params if params is not None else job.params, trigger_kind="manual",
        )

    async def _run_job(
        self, *, job_id: str, ext_id: str | None, handler: Handler, params: dict[str, Any], trigger_kind: str
    ) -> str:
        from .ws_hub import get_ws_hub

        async with session_scope() as session:
            run = await jobs_service.create_run(session, job_id=job_id, ext_id=ext_id, trigger=trigger_kind)
            run_id = run.id

        hub = get_ws_hub()
        await hub.publish(f"jobs.{job_id}", {"run_id": run_id, "status": "running"}, required_permission="jobs.read")
        await hub.publish(f"runs.{run_id}", {"status": "running"}, required_permission="jobs.read")

        lines = [f"[{utcnow().isoformat()}] gestartet (trigger={trigger_kind}, params={params!r})"]
        status, exit_code, error = "succeeded", 0, None

        # Der Task übernimmt den Kontext beim Anlegen: So weiß der Handler über `nodvard_sdk.current_job_trigger()`, ob er
        # nach Zeitplan oder von Hand läuft.
        token = _JOB_TRIGGER.set(trigger_kind)
        try:
            task: asyncio.Task = asyncio.ensure_future(handler(**params))
        finally:
            _JOB_TRIGGER.reset(token)
        self._running_tasks[run_id] = task
        try:
            result = await task
            lines.append(f"[{utcnow().isoformat()}] Ergebnis: {result!r}")
        except asyncio.CancelledError:
            status, exit_code, error = "failed", 1, "Abgebrochen (POST /runs/{id}/cancel)."
            lines.append(f"[{utcnow().isoformat()}] {error}")
        except NodvardError as exc:
            # Ein Fehlschlag, den der Handler selbst mit Klartext meldet (z. B. "Das Skript lief auf keinem
            # Server"): kein Programmfehler, deshalb ohne Traceback, weder im Lauf-Protokoll noch im
            # Container-Log, dessen Traceback-Prüfung nach einem Deploy sonst fälschlich zurückrollt.
            status, exit_code, error = "failed", 1, str(exc)
            lines.append(f"[{utcnow().isoformat()}] Fehler: {error}")
            logger.warning("job_run_failed job_id=%s run_id=%s error=%s", job_id, run_id, error)
        except Exception as exc:  # noqa: BLE001 - ein Fehler des Handlers landet immer im Lauf-Protokoll
            status, exit_code, error = "failed", 1, str(exc)
            lines.append(f"[{utcnow().isoformat()}] Fehler: {error}\n{traceback.format_exc()}")
            logger.exception("job_run_failed job_id=%s run_id=%s", job_id, run_id)
        finally:
            self._running_tasks.pop(run_id, None)

        log_path = self._runs_dir / f"{run_id}.log"
        content = "\n".join(lines) + "\n"
        # `configure()` legt den Ordner normalerweise schon an, der Dienst lässt sich aber auch ohne diesen Aufruf benutzen
        # (etwa in Tests über die API); darum hier noch einmal.
        await asyncio.to_thread(self._runs_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(log_path.write_text, content, encoding="utf-8")

        async with session_scope() as session:
            run_row = await session.get(JobRun, run_id)
            await jobs_service.finish_run(
                session, run_row, status=status, exit_code=exit_code, error=error, output_ref=str(log_path)
            )
            # APScheduler berechnet nach jedem Lauf den nächsten Termin neu (auch nach `trigger_now`); die gespiegelte
            # Spalte muss mitziehen, sonst zeigt `GET /jobs` einen veralteten Termin.
            apscheduler_job = self._scheduler.get_job(job_id)
            await jobs_service.set_next_run_at(
                session, job_id, getattr(apscheduler_job, "next_run_time", None)
            )

        await hub.publish(f"jobs.{job_id}", {"run_id": run_id, "status": status}, required_permission="jobs.read")
        await hub.publish(f"runs.{run_id}", {"status": status, "error": error}, required_permission="jobs.read")
        return run_id

    def cancel_run(self, run_id: str) -> bool:
        task = self._running_tasks.get(run_id)
        if task is None:
            return False
        task.cancel()
        return True


CORE_SCHEDULER_EXT_ID = "__core__"
"""Platzhalter-`ext_id` der Kern-Jobs in der `SchedulerRegistry` (`Job.ext_id` ist für sie `None`, die Registry braucht
einen Text). So findet `api/v1/jobs.py` den Handler eines Kern-Jobs auf demselben Weg wie den einer Erweiterung."""


JOB_RUNS_RETENTION_KEY = "job-runs-retention"
"""`ext_job_key` des nächtlichen Kern-Jobs, der alte Job-Läufe und ihre Protokolldateien aufräumt."""

NOTIFICATIONS_RETENTION_KEY = "notifications-retention"
"""`ext_job_key` des nächtlichen Kern-Jobs, der alte Meldungen samt Zustellprotokollen löscht."""

_service: SchedulerService | None = None


def get_scheduler_service() -> SchedulerService:
    global _service
    if _service is None:
        _service = SchedulerService()
    return _service


def reset_scheduler_service() -> None:
    """Nur für Tests."""
    global _service
    _service = None


async def register_core_jobs(runs_dir: Path) -> None:
    """Plant die Kern-Jobs ein (aufgerufen aus der Lifespan in `main.py`); anders als Erweiterungen haben sie kein
    `setup()`, das sie bei jedem Start neu anmeldet."""
    from ..config import get_settings
    from ..services import audit as audit_service

    from ..ext.runtime import get_extension_runtime

    async def _purge_audit(**_: Any) -> dict[str, int]:
        settings = get_settings()
        async with session_scope() as session:
            # Einstellung `audit.retention_days` gewinnt, die Umgebungsvariable ist der Rückfall.
            days = await audit_service.effective_retention_days(session, fallback=settings.audit_retention_days)
            deleted = await audit_service.purge_expired(session, retention_days=days)
        return {"deleted": deleted}

    service = get_scheduler_service()
    service.configure(runs_dir)
    async with session_scope() as session:
        job = await jobs_service.upsert_job(
            session, ext_id=None, ext_job_key="audit-retention-purge", name="Audit-Retention",
            kind="core", schedule="0 3 * * *", params={}, enabled=True,
        )
    get_extension_runtime().scheduler.register(CORE_SCHEDULER_EXT_ID, "audit-retention-purge", _purge_audit)
    await service.schedule(job, _purge_audit)

    # Alte Job-Läufe samt Protokolldateien aufräumen (`services.job_retention`): Aufbewahrung aus
    # `jobs.run_retention_days` (Vorgabe 30 Tage), je Job bleiben immer die letzten 20 Läufe.
    from ..services import job_retention as job_retention_service

    async def _purge_job_runs(**_: Any) -> dict[str, int]:
        return await job_retention_service.run_purge(service.runs_dir)

    async with session_scope() as session:
        runs_job = await jobs_service.upsert_job(
            session, ext_id=None, ext_job_key=JOB_RUNS_RETENTION_KEY, name="Alte Job-Läufe aufräumen",
            kind="core", schedule="20 3 * * *", params={}, enabled=True,
        )
    get_extension_runtime().scheduler.register(CORE_SCHEDULER_EXT_ID, JOB_RUNS_RETENTION_KEY, _purge_job_runs)
    await service.schedule(runs_job, _purge_job_runs)

    # Alte Meldungen samt Zustellprotokollen löschen (`services.notification_retention`): Aufbewahrung aus
    # `notifications.retention_days` (Vorgabe 90 Tage), nur ungelesene kritische bleiben. Eigene Minute (03:30), damit die
    # Aufräum-Jobs (Protokoll 03:00, Job-Läufe 03:20, alte Stände 03:40) nicht gleichzeitig auf die Schreibsperre warten.
    from ..services import notification_retention as notification_retention_service

    async def _purge_notifications(**_: Any) -> dict[str, int]:
        return await notification_retention_service.run_purge()

    async with session_scope() as session:
        notifications_job = await jobs_service.upsert_job(
            session, ext_id=None, ext_job_key=NOTIFICATIONS_RETENTION_KEY, name="Alte Meldungen aufräumen",
            kind="core", schedule="30 3 * * *", params={}, enabled=True,
        )
    get_extension_runtime().scheduler.register(CORE_SCHEDULER_EXT_ID, NOTIFICATIONS_RETENTION_KEY, _purge_notifications)
    await service.schedule(notifications_job, _purge_notifications)

    from . import gate as gate_service

    async def _expire_proposals(**_: Any) -> dict[str, int]:
        async with session_scope() as session:
            return {"expired": await gate_service.expire_stale(session)}

    async with session_scope() as session:
        expire_job = await jobs_service.upsert_job(
            session, ext_id=None, ext_job_key="actions-expire", name="Abgelaufene Vorschläge",
            kind="core", schedule="17 * * * *", params={}, enabled=True,
        )
    get_extension_runtime().scheduler.register(CORE_SCHEDULER_EXT_ID, "actions-expire", _expire_proposals)
    await service.schedule(expire_job, _expire_proposals)

    # Der alte Stand einer Wiederherstellung (`restore/replaced-...`, Konten und Schlüssel im Klartext) bleibt 30 Tage
    # liegen; danach geht er von selbst (die Oberfläche nennt den Tag).
    from ..services import restore as restore_service

    async def _cleanup_restore(**_: Any) -> dict[str, int]:
        removed = await asyncio.to_thread(restore_service.sweep_replaced_states, get_settings())
        return {"removed": len(removed)}

    async with session_scope() as session:
        cleanup_job = await jobs_service.upsert_job(
            session, ext_id=None, ext_job_key="restore-cleanup", name="Alte Stände aufräumen",
            kind="core", schedule="40 3 * * *", params={}, enabled=True,
        )
    get_extension_runtime().scheduler.register(CORE_SCHEDULER_EXT_ID, "restore-cleanup", _cleanup_restore)
    await service.schedule(cleanup_job, _cleanup_restore)

    # Erreichbarkeit von Hand angelegter Server: Zeitplan und Schalter aus `hosts.reachability.*`.
    # Ein Fehler hier darf den Start nicht verhindern, er steht im Protokoll.
    from ..services import reachability as reachability_service

    try:
        await reachability_service.sync_job()
    except Exception:  # noqa: BLE001 - der Start darf daran nie scheitern
        logger.exception("reachability_job_setup_failed")

    # Nach Updates suchen (`services.update_check`): einmal am Tag, Schalter `system.update_check.enabled`.
    from ..services import update_check as update_check_service

    try:
        await update_check_service.sync_job()
    except Exception:  # noqa: BLE001 - der Start darf daran nie scheitern
        logger.exception("update_check_job_setup_failed")

    # Erinnerung an ablaufende Tresor-Einträge.
    from ..services import secret_expiry as secret_expiry_service

    try:
        await secret_expiry_service.sync_job()
    except Exception:  # der Start darf daran nie scheitern
        logger.exception("secret_expiry_job_setup_failed")

    # Hinweis „Server neu einrichten“ in der Übersicht (`services.helper_hints`): alle 6 Stunden ein lesender Befehl je Server.
    from ..services import helper_hints as helper_hints_service

    try:
        await helper_hints_service.sync_job()
    except Exception:  # der Start darf daran nie scheitern
        logger.exception("helper_hints_job_setup_failed")

    # Automatische Sicherungen (`services.backups`): Zeitplan und Schalter aus `backup.config`.
    # Ein Fehler hier darf den Start nicht verhindern, er steht im Protokoll.
    from ..services import backups as backups_service

    try:
        backups_service.cleanup_on_start(get_settings())
        await backups_service.sync_job()
    except Exception:  # noqa: BLE001 - der Start darf daran nie scheitern
        logger.exception("backup_job_setup_failed")
