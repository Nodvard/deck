"""Sicherungen: Einstellungen, Sicherungsschluessel, automatischer Job, Download-Tickets.

Das Format und die Kryptografie stehen in `core/backup` (siehe dort). Hier liegt, was die
Anwendung damit macht:

* Einstellungen (globale Settings, NICHT ueber `/settings` erreichbar, weil nur der Owner
  sie aendern darf):
    `backup.config`    {enabled, schedule, keep, dir, include_runs}
    `backup.key`       {recipient, key_id, kdf, created_at} -- nie Passwort oder Schluessel
    `backup.last_run`  {at, ok, trigger, name, size, error, deleted, warnings}
    `system.instance_id`  zufaellige Kennung dieser Installation (steht im Manifest)
* Der Kern-Job `system-backup` (Zeitplan aus `backup.config`). Ein Lauf schreibt die
  Sicherung, raeumt nach `keep` auf, merkt sich das Ergebnis, protokolliert
  `system.backup.created`/`.failed` und meldet einen Fehlschlag ueber die Benachrichtigungen.
* Es laeuft immer nur EINE Sicherung (`asyncio.Lock`); "Jetzt sichern" und der Download
  bekommen sonst 409.
* Der Download wird NICHT als Job gebaut: ein Job schreibt seine Parameter ins Laufprotokoll,
  und dort duerfte ein Einmal-Passwort nie landen. Stattdessen ein Hintergrund-Task mit
  Einmal-Ticket (`core/backup/tickets.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..core import timezone as timezone_service
from ..core.backup import container, crypto, snapshot, store
from ..core.backup import format as fmt
from ..core.backup.errors import BackupError, NotEnoughSpace, NotSqlite
from ..core.backup.tickets import DownloadTicket, get_ticket_registry
from ..core.cron import cron_trigger
from ..db import utcnow
from ..db.session import session_scope
from ..version import __version__
from . import audit as audit_service
from . import jobs as jobs_service
from . import settings as settings_service

logger = logging.getLogger("nodvard_deck.backups")

SETTING_CONFIG = "backup.config"
SETTING_KEY = "backup.key"
SETTING_LAST_RUN = "backup.last_run"
SETTING_INSTANCE = "system.instance_id"

EXCLUSIVE_CHECK = "pruefen"
"""Art der Sperre, wenn eine hochgeladene Sicherung zum Wiederherstellen entschluesselt wird
(`services/restore.py`): die Schluesselberechnung braucht bis zu 256 MiB, deshalb nie zugleich mit einer
Sicherung oder einer zweiten Pruefung."""

JOB_KEY = "system-backup"
JOB_NAME = "Sicherung"
DEFAULT_SCHEDULE = "30 2 * * *"
DEFAULT_KEEP = 7
KEEP_MIN, KEEP_MAX = 1, 60
_SPACE_MARGIN = 32 * 1024 * 1024


class BackupBusy(BackupError):
    def __init__(self, kind: str | None = None) -> None:
        if kind == EXCLUSIVE_CHECK:
            super().__init__("Es wird gerade eine Sicherung zum Wiederherstellen geprüft. Bitte warten, bis das fertig ist.")
        else:
            super().__init__("Es läuft gerade schon eine Sicherung. Bitte warten, bis sie fertig ist.")


class NoBackupKey(BackupError):
    def __init__(self) -> None:
        super().__init__("Zuerst ein Sicherungspasswort festlegen.")


@dataclass
class BackupConfig:
    enabled: bool = False
    schedule: str = DEFAULT_SCHEDULE
    keep: int = DEFAULT_KEEP
    dir: str | None = None
    include_runs: bool = False

    @classmethod
    def from_raw(cls, raw: object) -> BackupConfig:
        cfg = cls()
        if isinstance(raw, dict):
            if isinstance(raw.get("enabled"), bool):
                cfg.enabled = raw["enabled"]
            if isinstance(raw.get("schedule"), str) and raw["schedule"].strip():
                cfg.schedule = raw["schedule"]
            if isinstance(raw.get("keep"), int) and not isinstance(raw.get("keep"), bool):
                cfg.keep = min(max(raw["keep"], KEEP_MIN), KEEP_MAX)
            if isinstance(raw.get("dir"), str) and raw["dir"]:
                cfg.dir = raw["dir"]
            if isinstance(raw.get("include_runs"), bool):
                cfg.include_runs = raw["include_runs"]
        return cfg


# ---------------------------------------------------------------------------
# Zustand im Prozess
# ---------------------------------------------------------------------------

_lock: asyncio.Lock | None = None
_running: dict[str, Any] = {}
_tasks: set[asyncio.Task] = set()
_waiting = 0
SCHEDULED_WAIT_S = 3600.0
"""So lange wartet ein Lauf nach Zeitplan, wenn gerade ein Download gebaut wird (statt
ausgelassen zu werden). Danach zaehlt er als fehlgeschlagen, mit Meldung."""


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def _try_acquire(kind: str) -> str:
    """Sperrt oder wirft `BackupBusy`. Liefert ein Token; nur wer es hat, gibt wieder frei
    (ein doppeltes Freigeben darf nie die Sperre eines anderen Laufs loesen)."""
    lock = _get_lock()
    if lock.locked() or _waiting:
        raise BackupBusy(_running.get("kind"))
    await lock.acquire()  # frei, niemand wartet -> kehrt ohne Unterbrechung zurueck, kein Wettlauf
    return _mark_running(kind)


async def _acquire_waiting(kind: str, timeout_s: float) -> str:
    """Wie `_try_acquire`, wartet aber bis `timeout_s` auf einen laufenden Bau."""
    global _waiting
    lock = _get_lock()
    _waiting += 1
    try:
        await asyncio.wait_for(lock.acquire(), timeout_s)
    except asyncio.TimeoutError:
        raise BackupBusy() from None
    finally:
        _waiting -= 1
    return _mark_running(kind)


def _mark_running(kind: str) -> str:
    token = uuid.uuid4().hex
    _running.clear()
    _running.update(kind=kind, started_at=utcnow().isoformat(), token=token)
    return token


def _release(token: str) -> None:
    if _running.get("token") != token:
        return
    _running.clear()
    lock = _get_lock()
    if lock.locked():
        lock.release()


async def acquire_exclusive(kind: str) -> str:
    """Fuer andere Dienste, die dieselbe Sperre brauchen (Wiederherstellen). `BackupBusy`, wenn belegt."""
    return await _try_acquire(kind)


def release_exclusive(token: str) -> None:
    _release(token)


def running() -> dict[str, Any] | None:
    if not _running or _running.get("kind") == EXCLUSIVE_CHECK:
        return None  # eine Pruefung ist keine Sicherung (die Karte zeigt sonst "Sicherung laeuft")
    return {k: v for k, v in _running.items() if k != "token"}


def _spawn(coro) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def wait_for_tasks() -> None:
    """Nur fuer Tests: wartet auf laufende Hintergrund-Tasks."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def reset_for_tests() -> None:
    global _lock, _waiting
    _waiting = 0
    for task in list(_tasks):
        task.cancel()
    _tasks.clear()
    _running.clear()
    _lock = None
    from ..core.backup.tickets import reset_ticket_registry

    reset_ticket_registry()


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------


async def load_config(session: AsyncSession) -> BackupConfig:
    return BackupConfig.from_raw(await settings_service.get_global(session, SETTING_CONFIG))


async def load_key(session: AsyncSession) -> dict[str, Any] | None:
    raw = await settings_service.get_global(session, SETTING_KEY)
    if isinstance(raw, dict) and isinstance(raw.get("recipient"), str) and isinstance(raw.get("key_id"), str):
        return raw
    return None


async def instance_id(session: AsyncSession) -> str:
    value = await settings_service.get_global(session, SETTING_INSTANCE)
    if isinstance(value, str) and value:
        return value
    value = uuid.uuid4().hex
    await settings_service.set_global(session, SETTING_INSTANCE, value)
    return value


def validate_config(payload: dict[str, Any], settings: Settings) -> BackupConfig:
    """Wirft `ValueError` (-> 422) oder `TargetNotAllowed` mit verstaendlicher Meldung."""
    keep = payload.get("keep")
    if not isinstance(keep, int) or isinstance(keep, bool) or not KEEP_MIN <= keep <= KEEP_MAX:
        raise ValueError(f"Anzahl behalten muss zwischen {KEEP_MIN} und {KEEP_MAX} liegen.")
    schedule = payload.get("schedule")
    if not isinstance(schedule, str) or not schedule.strip():
        raise ValueError("Bitte einen Zeitplan angeben.")
    try:
        cron_trigger(schedule)
    except Exception as exc:
        raise ValueError(f"Ungültiger Zeitplan: {exc}") from exc
    raw_dir = payload.get("dir") or None
    target = store.resolve_target(raw_dir, settings.data_dir)
    stored_dir = None if target == store.default_dir(settings.data_dir) else str(target)
    return BackupConfig(
        enabled=bool(payload.get("enabled")), schedule=schedule.strip(), keep=keep, dir=stored_dir,
        include_runs=bool(payload.get("include_runs")),
    )


async def save_config(session: AsyncSession, settings: Settings, payload: dict[str, Any], *, user_id: str) -> BackupConfig:
    cfg = validate_config(payload, settings)
    if cfg.enabled and await load_key(session) is None:
        raise NoBackupKey()
    old = asdict(await load_config(session))
    await settings_service.set_global(session, SETTING_CONFIG, asdict(cfg), updated_by_user_id=user_id)
    await audit_service.log(
        session, actor_type="user", actor_id=user_id, action="system.backup.config_changed", outcome="success",
        target_type="setting", target_id=SETTING_CONFIG, detail={"old": old, "new": asdict(cfg)},
    )
    return cfg


def protected_changes(old: BackupConfig, payload: dict[str, Any], settings: Settings) -> list[str]:
    """Aenderungen, die Sicherungen gefaehrden und deshalb das Kontopasswort brauchen (als
    Teile eines deutschen Satzes): weniger Sicherungen behalten (die Rotation loescht dann
    beim naechsten Lauf aeltere), automatische Sicherung ausschalten, anderer Ordner.
    Bewusst OHNE Seiteneffekte (legt keinen Ordner an) und vor der Pruefung der Eingabe: ein
    Ordner gilt als geaendert, wenn sich die Schreibweise nach dem Normalisieren unterscheidet."""
    reasons: list[str] = []
    keep = payload.get("keep")
    if isinstance(keep, int) and not isinstance(keep, bool) and keep < old.keep:
        reasons.append("weniger Sicherungen behalten")
    if old.enabled and not payload.get("enabled"):
        reasons.append("die automatische Sicherung ausschalten")
    default = str(store.default_dir(settings.data_dir))
    raw = payload.get("dir")
    new_dir = str(PurePosixPath(raw)) if isinstance(raw, str) and raw else default
    if new_dir != (old.dir or default):
        reasons.append("den Ordner ändern")
    return reasons


async def set_key(session: AsyncSession, password: str, *, user_id: str) -> crypto.DerivedKey:
    if len(password) < crypto.MIN_PASSWORD_LENGTH:
        raise ValueError(f"Das Sicherungspasswort braucht mindestens {crypto.MIN_PASSWORD_LENGTH} Zeichen.")
    old = await load_key(session)
    key = await asyncio.to_thread(crypto.derive_key, password)
    await settings_service.set_global(
        session, SETTING_KEY,
        {"recipient": key.recipient, "key_id": key.key_id, "kdf": key.kdf.to_dict(), "created_at": utcnow().isoformat()},
        updated_by_user_id=user_id,
    )
    await audit_service.log(
        session, actor_type="user", actor_id=user_id, action="system.backup.key_set", outcome="success",
        target_type="setting", target_id=SETTING_KEY,
        detail={"key_id": key.key_id, "previous_key_id": old["key_id"] if old else None},
    )
    return key


# ---------------------------------------------------------------------------
# Pfade und Quellen
# ---------------------------------------------------------------------------


def database_file(settings: Settings) -> Path:
    url = make_url(settings.database_url)
    if not url.drivername.startswith("sqlite") or url.database in (None, "", ":memory:"):
        raise NotSqlite()
    return Path(url.database)


def _work_dir(settings: Settings, name: str) -> Path:
    base = store.make_private_dir(store.default_dir(settings.data_dir))
    return store.make_private_dir(base / name)


def downloads_dir(settings: Settings) -> Path:
    return _work_dir(settings, ".downloads")


def sources_for(settings: Settings, *, include_runs: bool) -> snapshot.Sources:
    single = [
        ("master.key", settings.master_key_path),
        ("vault_keyring.json", settings.vault_keyring_path),
    ]
    if not settings.jwt_secret:
        single.append(("jwt_secret.key", settings.jwt_secret_path))
    trees = [("ext", settings.ext_data_dir), ("branding", settings.data_dir / "branding")]
    if include_runs:
        trees.append(("runs", settings.data_dir / "runs"))
    return snapshot.Sources(db_path=database_file(settings), single_files=single, trees=trees)


def _estimate(sources: snapshot.Sources) -> tuple[int, int]:
    db = os.path.getsize(sources.db_path) if sources.db_path.exists() else 0
    files = sum(e.size for e in snapshot.collect(sources))
    return db, files


def _check_space(path: Path, needed: int, what: str) -> None:
    free = store.free_bytes(path)
    if free < needed + _SPACE_MARGIN:
        raise NotEnoughSpace(
            f"Zu wenig freier Speicher {what} ({free // (1024 * 1024)} MB frei, "
            f"etwa {(needed + _SPACE_MARGIN) // (1024 * 1024)} MB nötig)."
        )


def _build_file(
    settings: Settings, *, target: Path, name: str, header: dict[str, Any], encrypt: container.Encryptor,
    include_runs: bool, instance: str,
) -> tuple[Path, str, int, list[str]]:
    """Synchron (laeuft im Thread). Klartext-Kopie der Datenbank nur im Temp-Ordner unter
    dem Datenordner, nie im Zielordner. Vierter Wert: die nicht mitgesicherten Verknuepfungen."""
    sources = sources_for(settings, include_runs=include_runs)
    work = _work_dir(settings, ".tmp")
    tmp = store.make_private_dir(work / uuid.uuid4().hex)
    try:
        db_size, files_size = _estimate(sources)
        _check_space(tmp, db_size, "im Datenordner")
        _check_space(target, db_size + files_size, "im Zielordner")
        store.sweep_parts(target)
        skipped: list[str] = []

        def produce(out: Any) -> None:
            container.write_backup(
                out, header=header, sources=sources, tmp_dir=tmp, encrypt=encrypt, build=settings.build,
                instance_id=instance, jwt_from_env=bool(settings.jwt_secret), extensions_dir=settings.extensions_dir,
                on_skipped_links=lambda links: skipped.extend(links),
            )

        path, digest, size = store.write_file(target, name, produce)
        return path, digest, size, skipped
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


MAX_WARNING_PATHS = 5


def link_warnings(skipped: list[str]) -> list[str]:
    """Meldung fuer Verknuepfungen, die nicht gesichert wurden (hoechstens ein paar Pfade)."""
    if not skipped:
        return []
    shown = ", ".join(skipped[:MAX_WARNING_PATHS])
    more = len(skipped) - MAX_WARNING_PATHS
    if more > 0:
        shown += f" und {more} weitere"
    count = len(skipped)
    noun = "Verknüpfung" if count == 1 else "Verknüpfungen"
    where = " in Erweiterungsdaten" if all(p.startswith("ext/") for p in skipped) else ""
    return [f"{count} {noun}{where} nicht gesichert: {shown}"]


async def _local_now(session: AsyncSession) -> datetime:
    zone = await timezone_service.get_timezone(session)
    return datetime.now(ZoneInfo(zone))


# ---------------------------------------------------------------------------
# Automatische Sicherung
# ---------------------------------------------------------------------------


async def run_backup(
    *, trigger: str = "schedule", user_id: str | None = None, lock_token: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Ein Lauf des Jobs (Zeitplan oder "Jetzt sichern"). Wirft bei Fehler weiter, damit der
    Lauf als fehlgeschlagen im Job-Protokoll steht. `lock_token`: die Sperre hat schon der
    Aufrufer ("Jetzt sichern")."""
    settings = settings or get_settings()
    if lock_token is None:
        # Ein Download wird gerade gebaut: warten statt die naechtliche Sicherung still
        # auszulassen. Klappt es auch dann nicht, steht es wie jeder Fehlschlag in der Karte.
        try:
            lock_token = await _acquire_waiting("automatisch", SCHEDULED_WAIT_S)
        except BackupBusy as exc:
            await _record_failure(exc, trigger=trigger, user_id=user_id)
            raise
    token = lock_token
    try:
        try:
            async with session_scope() as session:
                cfg = await load_config(session)
                key = await load_key(session)
                instance = await instance_id(session)
                local_now = await _local_now(session)
            if key is None:
                raise NoBackupKey()
            target = await asyncio.to_thread(store.resolve_target, cfg.dir, settings.data_dir)
            header = fmt.build_header(
                created_at=utcnow(), app_version=__version__, mode="schluessel", key_id=key["key_id"], kdf=key["kdf"],
            )
            name = fmt.backup_name(local_now)
            path, digest, size, skipped = await asyncio.to_thread(
                _build_file, settings, target=target, name=name, header=header,
                encrypt=container.encryptor_for_recipient(key["recipient"]), include_runs=cfg.include_runs,
                instance=instance,
            )
            await asyncio.to_thread(
                store.write_sidecar, path,
                {"name": name, "sha256": digest, "size": size, "created_at": header["created_at"],
                 "app_version": __version__, "key_id": key["key_id"], "format": fmt.FORMAT_VERSION},
            )
            deleted = await asyncio.to_thread(store.rotate, target, cfg.keep)
        except Exception as exc:
            await _record_failure(exc, trigger=trigger, user_id=user_id)
            raise
        warnings = link_warnings(skipped)
        result = {"name": name, "size": size, "deleted": deleted, "warnings": warnings}
        async with session_scope() as session:
            await settings_service.set_global(
                session, SETTING_LAST_RUN,
                {"at": utcnow().isoformat(), "ok": True, "trigger": trigger, "name": name, "size": size,
                 "error": None, "deleted": deleted, "warnings": warnings},
            )
            await audit_service.log(
                session, actor_type="user" if user_id else "system", actor_id=user_id or "backup",
                action="system.backup.created", outcome="success", target_type="backup", target_id=name,
                detail={"trigger": trigger, "size": size, "key_id": key["key_id"], "deleted": deleted,
                        "warnings": warnings},
            )
        logger.info("backup_created name=%s size=%s deleted=%s", name, size, len(deleted))
        if skipped:
            logger.warning("backup_links_skipped count=%s", len(skipped))
        return result
    finally:
        _release(token)


def _error_text(exc: Exception) -> str:
    if isinstance(exc, BackupError):
        return str(exc)
    return f"Unerwarteter Fehler ({type(exc).__name__}). Details stehen im Protokoll des Containers."


async def _record_failure(exc: Exception, *, trigger: str, user_id: str | None) -> None:
    text = _error_text(exc)
    logger.warning("backup_failed trigger=%s error=%s", trigger, type(exc).__name__, exc_info=not isinstance(exc, BackupError))
    with contextlib.suppress(Exception):
        async with session_scope() as session:
            await settings_service.set_global(
                session, SETTING_LAST_RUN,
                {"at": utcnow().isoformat(), "ok": False, "trigger": trigger, "name": None, "size": None,
                 "error": text, "deleted": [], "warnings": []},
            )
            await audit_service.log(
                session, actor_type="user" if user_id else "system", actor_id=user_id or "backup",
                action="system.backup.failed", outcome="failure", target_type="backup", reason=text,
                detail={"trigger": trigger},
            )
    from . import notifications as notifications_service

    with contextlib.suppress(Exception):
        async with session_scope() as session:
            await notifications_service.send(
                session, title="Sicherung fehlgeschlagen", body=text, severity="warning",
                payload={"path": "/settings/system", "tags": ["floppy_disk"]},
            )


async def _job_handler(lock_token: str | None = None, user_id: str | None = None, **_: Any) -> dict[str, Any]:
    return await run_backup(trigger="manual" if lock_token else "schedule", user_id=user_id, lock_token=lock_token)


async def sync_job() -> None:
    """Legt den Kern-Job an bzw. passt Zeitplan/Schalter an und plant ihn neu. Aufgerufen
    beim Start (`register_core_jobs`) und nach jeder Aenderung der Einstellungen."""
    from ..core.scheduler import CORE_SCHEDULER_EXT_ID, get_scheduler_service
    from ..ext.runtime import get_extension_runtime

    async with session_scope() as session:
        cfg = await load_config(session)
        key = await load_key(session)
        job = await jobs_service.upsert_job(
            session, ext_id=None, ext_job_key=JOB_KEY, name=JOB_NAME, kind="core", schedule=cfg.schedule,
            params={}, enabled=cfg.enabled and key is not None,
        )
    get_extension_runtime().scheduler.register(CORE_SCHEDULER_EXT_ID, JOB_KEY, _job_handler)
    await get_scheduler_service().schedule(job, _job_handler)


async def start_manual_run(*, user_id: str) -> None:
    """"Jetzt sichern": sperrt sofort (409, wenn schon eine laeuft) und laeuft als Job im
    Hintergrund (Ergebnis im Job-Protokoll und unter `last_run`)."""
    from ..core.scheduler import get_scheduler_service

    async with session_scope() as session:
        if await load_key(session) is None:
            raise NoBackupKey()
        job = await jobs_service.get_job_by_key(session, ext_id=None, ext_job_key=JOB_KEY)
    if job is None:
        await sync_job()
        async with session_scope() as session:
            job = await jobs_service.get_job_by_key(session, ext_id=None, ext_job_key=JOB_KEY)
    assert job is not None
    token = await _try_acquire("jetzt")

    async def _go() -> None:
        try:
            await get_scheduler_service().trigger_now(job, _job_handler, params={"lock_token": token, "user_id": user_id})
        finally:
            _release(token)  # falls der Lauf vor dem Handler scheiterte; sonst ohne Wirkung

    _spawn(_go())


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


async def prepare_download(settings: Settings, *, user_id: str, mode: str, password: str | None) -> DownloadTicket:
    """Baut eine verschluesselte Sicherung fuer den Download im Hintergrund und gibt sofort
    ein Ticket zurueck. Das Einmal-Passwort lebt nur in diesem Task."""
    async with session_scope() as session:
        key = await load_key(session)
        instance = await instance_id(session)
        local_now = await _local_now(session)
        cfg = await load_config(session)
    if mode == "schluessel":
        if key is None:
            raise NoBackupKey()
        header = fmt.build_header(created_at=utcnow(), app_version=__version__, mode="schluessel",
                                  key_id=key["key_id"], kdf=key["kdf"])
        encrypt = container.encryptor_for_recipient(key["recipient"])
    elif mode == "passwort":
        if not password or len(password) < crypto.MIN_PASSWORD_LENGTH:
            raise ValueError(f"Das Einmal-Passwort braucht mindestens {crypto.MIN_PASSWORD_LENGTH} Zeichen.")
        header = fmt.build_header(created_at=utcnow(), app_version=__version__, mode="passwort")
        encrypt = container.encryptor_for_password(password)
    else:
        raise ValueError("Unbekannte Art der Verschlüsselung.")
    database_file(settings)  # NotSqlite frueh melden

    token = await _try_acquire("download")
    registry = get_ticket_registry()
    registry.sweep()
    try:
        directory = downloads_dir(settings)
        _sweep_downloads(directory, registry.live_paths())
        file_name = f"{uuid.uuid4().hex}{fmt.FILE_SUFFIX}"
        ticket = registry.create(
            user_id=user_id, path=directory / file_name, filename=fmt.backup_name(local_now),
            delete_after=True, ready=False,
        )
    except BaseException:
        _release(token)
        raise

    async def _build() -> None:
        try:
            *_, skipped = await asyncio.to_thread(
                _build_file, settings, target=directory, name=file_name, header=header, encrypt=encrypt,
                include_runs=cfg.include_runs, instance=instance,
            )
            if skipped:
                logger.warning("backup_links_skipped count=%s", len(skipped))
            registry.mark_ready(ticket.ticket)
            outcome, reason = "success", None
        except Exception as exc:
            reason = _error_text(exc)
            logger.warning("backup_download_failed error=%s", type(exc).__name__, exc_info=not isinstance(exc, BackupError))
            registry.mark_failed(ticket.ticket, reason)
            outcome = "failure"
        finally:
            _release(token)
        with contextlib.suppress(Exception):
            async with session_scope() as session:
                await audit_service.log(
                    session, actor_type="user", actor_id=user_id, action="system.backup.download_prepared",
                    outcome=outcome, target_type="backup", reason=reason,
                    detail={"mode": mode, "key_id": key["key_id"] if (key and mode == "schluessel") else None},
                )

    _spawn(_build())
    return ticket


def _sweep_downloads(directory: Path, live: set[Path]) -> None:
    """Dateien ohne gueltiges Ticket (Neustart, abgebrochener Download) entfernen."""
    for entry in list(os.scandir(directory)):
        path = Path(entry.path)
        if path not in live and entry.is_file(follow_symlinks=False):
            with contextlib.suppress(OSError):
                os.unlink(path)


def cleanup_on_start(settings: Settings) -> None:
    """Beim Start: Reste frueherer Downloads und Temp-Ordner wegraeumen (es gibt noch
    keine Tickets)."""
    base = store.default_dir(settings.data_dir)
    for name in (".downloads", ".tmp"):
        with contextlib.suppress(OSError):
            shutil.rmtree(base / name)


async def ticket_for_stored(settings: Settings, session: AsyncSession, *, user_id: str, name: str) -> DownloadTicket:
    cfg = await load_config(session)
    target = await asyncio.to_thread(store.resolve_target, cfg.dir, settings.data_dir, create=False)
    path = await asyncio.to_thread(store.backup_path, target, name)
    return get_ticket_registry().create(user_id=user_id, path=path, filename=name, delete_after=False, ready=True)


async def target_of(settings: Settings, session: AsyncSession) -> Path:
    cfg = await load_config(session)
    return await asyncio.to_thread(store.resolve_target, cfg.dir, settings.data_dir, create=False)


# ---------------------------------------------------------------------------
# Uebersicht
# ---------------------------------------------------------------------------


async def overview(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    cfg = await load_config(session)
    key = await load_key(session)
    last_run = await settings_service.get_global(session, SETTING_LAST_RUN)
    default = store.default_dir(settings.data_dir)
    target_error = None
    items: list[dict[str, Any]] = []
    same = True
    free = None
    target_str = cfg.dir or str(default)
    try:
        target = await asyncio.to_thread(store.resolve_target, cfg.dir, settings.data_dir)
        items = await asyncio.to_thread(store.list_backups, target)
        same = store.same_storage(target, settings.data_dir)
        free = store.free_bytes(target)
    except BackupError as exc:
        target_error = str(exc)
    for item in items:
        item["key_current"] = bool(key and item.get("key_id") == key["key_id"])
    try:
        database_file(settings)
        sqlite_ok = True
    except NotSqlite:
        sqlite_ok = False
    return {
        "config": {**asdict(cfg), "dir": target_str},
        "key": {"key_id": key["key_id"], "created_at": key.get("created_at")} if key else None,
        "target": {
            "dir": target_str, "default_dir": str(default), "external_root": str(store.EXTERNAL_ROOT),
            "external_available": os.path.isdir(store.EXTERNAL_ROOT) and not os.path.islink(store.EXTERNAL_ROOT),
            "same_storage_as_data": same, "free_bytes": free, "error": target_error,
        },
        "backups": items,
        "last_run": last_run if isinstance(last_run, dict) else None,
        "running": running(),
        "sqlite": sqlite_ok,
        "limits": {"keep_min": KEEP_MIN, "keep_max": KEEP_MAX, "password_min_length": crypto.MIN_PASSWORD_LENGTH},
    }
