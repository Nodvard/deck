"""Wiederherstellen einer Sicherung: Hochladen, Pruefen, Vormerken, Abbrechen, Ergebnis.

Die eigentliche Arbeit (Entpacken, Pruefen, Einspielen) steht in `core/backup/restore.py` und
laeuft beim naechsten Start in `nodvard_deck.boot`. Hier ist, was die LAUFENDE Anwendung dazu tut:

* **Hochladen** als roher Datenstrom in eine Datei unter `<Datenordner>/restore/<id>/`; nie der
  ganze Inhalt im Speicher, Obergrenze (`restore_max_upload_bytes`) schon am Kopf der Anfrage,
  Platzpruefung vorher. Es gibt hoechstens EINEN Zwischenstand; ein neuer Upload raeumt den alten weg.
* **Pruefen** (`inspect`): entschluesseln und auspacken in den Staging-Ordner. Das darf immer nur
  eines zugleich laufen (die Schluesselberechnung braucht bis zu 256 MiB) und teilt dafuer die
  Sperre mit den Sicherungen. Laeuft als eigener Task -- bricht die Anfrage ab, laeuft die Pruefung
  sauber zu Ende, statt halb im Hintergrund weiterzuarbeiten.
* **Vormerken** (`schedule`): `restore/pending.json`; eingespielt wird beim naechsten Start.
* **Ergebnis** (`restore/result.json`): `record_result` schreibt nach dem Start das
  Audit-Protokoll in die DANN gueltige Datenbank.

Ein Zwischenstand gehoert zu einem Bereich (`Scope`): dem Owner, der ihn angelegt hat, oder dem
Assistenten (Einrichtungscode). Eine ID des einen Bereichs ist im anderen unbekannt.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
import re
import shutil
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .. import migrate
from ..config import Settings
from ..core.backup import restore, store
from ..core.backup.errors import (
    BackupError,
    BackupTooLarge,
    NotEnoughSpace,
    WrongSecret,
)
from ..db.session import session_scope
from ..version import __version__
from . import audit as audit_service
from . import backups as backups_service

logger = logging.getLogger("nodvard_deck.restore")

FILES_NAME = "files.json"
_WRITE_BUFFER = 1024 * 1024


class RestoreBusy(BackupError):
    """Es laeuft schon ein Upload oder eine Pruefung (409)."""


class PendingExists(BackupError):
    def __init__(self) -> None:
        super().__init__("Es ist schon eine Wiederherstellung vorgemerkt. Bitte zuerst abbrechen oder den Neustart abwarten.")


class UnknownRestore(BackupError):
    def __init__(self) -> None:
        super().__init__("Diese Sicherung ist unbekannt oder abgelaufen (Zwischenstände werden nach einer Stunde gelöscht). Bitte noch einmal hochladen.")


class WrongState(BackupError):
    """Der Schritt passt nicht zum Stand (z. B. Vormerken, bevor geprueft wurde) (409)."""


@dataclass(frozen=True)
class Scope:
    kind: str
    """`owner` (Einstellungen) oder `bootstrap` (Assistent, mit Einrichtungscode)."""
    user_id: str | None = None
    label: str = ""
    ip: str | None = None

    @property
    def key(self) -> str:
        return f"owner:{self.user_id}" if self.kind == "owner" else "bootstrap"

    def actor(self) -> dict[str, Any]:
        if self.kind == "owner":
            return {"type": "user", "id": self.user_id, "label": self.label, "ip": self.ip}
        return {"type": "setup", "id": None, "label": "Einrichtung", "ip": self.ip}


# ---------------------------------------------------------------------------
# Zustand im Prozess
# ---------------------------------------------------------------------------

_upload_active = False
_inspect_active: str | None = None
_tasks: set[asyncio.Task] = set()
_sweeps: list[asyncio.TimerHandle] = []


def reset_for_tests() -> None:
    global _upload_active, _inspect_active
    _upload_active = False
    _inspect_active = None
    for task in list(_tasks):
        task.cancel()
    _tasks.clear()
    for handle in _sweeps:
        handle.cancel()
    _sweeps.clear()


async def wait_for_tasks() -> None:
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def _spawn(coro) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    _tasks.add(task)

    def _done(finished: asyncio.Task) -> None:
        _tasks.discard(finished)
        if not finished.cancelled():
            finished.exception()  # gelesen, damit nichts "nie abgeholt" im Protokoll steht

    task.add_done_callback(_done)
    return task


def layout_for(settings: Settings) -> restore.Layout:
    return restore.Layout.from_settings(settings)


def sweep_replaced_states(settings: Settings) -> list[str]:
    """Kern-Job `restore-cleanup`: der alte Stand (`restore/replaced-...`) geht nach 30 Tagen. Nie ein Fehler."""
    try:
        return restore.sweep_replaced(layout_for(settings))
    except BackupError:
        return []  # keine SQLite-Datenbank: es gibt nichts aufzuraeumen
    except OSError:
        logger.exception("restore_replaced_sweep_failed")
        return []


def limits_info(settings: Settings) -> dict[str, int]:
    return {
        "max_upload_bytes": settings.restore_max_upload_bytes,
        "max_unpacked_bytes": settings.restore_max_unpacked_bytes,
        "expires_in": int(restore.STAGING_TTL_S),
    }


def _sweep(layout: restore.Layout) -> list[str]:
    """Aufraeumen, aber nie unter einer laufenden Pruefung weg."""
    return restore.sweep(layout, keep={_inspect_active} if _inspect_active else frozenset())


def _schedule_sweep(layout: restore.Layout) -> None:
    """Ein aufgegebener Zwischenstand wird auch dann nach einer Stunde geloescht, wenn niemand
    mehr nachsieht."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _sweeps[:] = [h for h in _sweeps if not h.cancelled()]
    _sweeps.append(loop.call_later(restore.STAGING_TTL_S + 30, lambda: _sweep(layout)))


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


async def context_for_summary(session: AsyncSession) -> tuple[str | None, bool]:
    """(Kennung dieser Installation, gibt es schon Konten?) -- fuer die Warnungen der Zusammenfassung."""
    from . import auth as auth_service

    # `instance_id` legt die Kennung beim ersten Mal an: ohne sie liesse sich eine fremde Sicherung
    # nicht als fremd erkennen (die Kennung entsteht sonst erst mit der ersten eigenen Sicherung).
    return await backups_service.instance_id(session), not await auth_service.needs_bootstrap(session)


def _meta(layout: restore.Layout, scope: Scope, rid: str) -> dict[str, Any]:
    try:
        meta = restore.read_meta(layout, rid)
    except ValueError:
        meta = None
    if meta is None or meta.get("scope") != scope.key:
        raise UnknownRestore()
    return meta


def _pending_view(pending: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    remaining = max(0, int(restore.PENDING_TTL_S - (now - pending["scheduled_at"])))
    return {
        "id": pending["id"], "source": pending["source"], "scheduled_at": restore.now_iso(pending["scheduled_at"]),
        "expires_in": remaining, "sign_out_all": pending["sign_out_all"], "backup": pending.get("backup", {}),
    }


def _result_view(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    actor = result.get("actor") if isinstance(result.get("actor"), dict) else {}
    return {
        "ok": bool(result.get("ok")), "at": result.get("at"), "message": result.get("message"), "source": result.get("source"),
        "backup": result.get("backup") or {}, "replaced": result.get("replaced"), "actor": actor.get("label"),
        "rolled_back": result.get("rolled_back"),
    }


def _staged_view(meta: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    return {
        "id": meta["id"], "state": meta.get("state"), "size": meta.get("size"), "header": meta.get("header"),
        "summary": meta.get("summary"), "expires_in": max(0, int(restore.STAGING_TTL_S - (now - meta.get("created_at", now)))),
    }


async def status(settings: Settings, scope: Scope | None) -> dict[str, Any]:
    """Stand fuer die Oberflaeche: Vormerkung, Zwischenstand (nur fuer den Eigentuemer), letztes Ergebnis."""
    layout = layout_for(settings)
    await asyncio.to_thread(_sweep, layout)
    pending = restore.read_pending(layout)
    staged = None
    if scope is not None:
        for rid in restore.list_ids(layout):
            meta = restore.read_meta(layout, rid)
            if meta and meta.get("scope") == scope.key:
                staged = _staged_view(meta)
    return {
        "pending": _pending_view(pending) if pending else None,
        "staged": staged,
        "result": _result_view(restore.read_result(layout)),
        "replaced": await asyncio.to_thread(restore.replaced_info, layout),
        "limits": limits_info(settings),
        "busy": _upload_active or _inspect_active is not None,
    }


# ---------------------------------------------------------------------------
# Hochladen
# ---------------------------------------------------------------------------


def _mb(value: int) -> str:
    return f"{max(1, value // (1024 * 1024))} MB"


async def receive_upload(settings: Settings, scope: Scope, chunks: AsyncIterator[bytes], content_length: int | None) -> dict[str, Any]:
    """Schreibt den Strom nach `restore/<id>/upload.ndbak`. Vorab-Pruefungen (alle VOR dem ersten
    gelesenen Byte): Obergrenze laut Kopf, Vormerkung, laufende Arbeit, freier Platz."""
    global _upload_active
    layout = layout_for(settings)
    limit = settings.restore_max_upload_bytes
    if content_length is not None and content_length > limit:
        raise BackupTooLarge(f"Die Datei ist zu groß ({_mb(content_length)}; erlaubt sind höchstens {_mb(limit)}).")
    if content_length == 0:
        raise BackupError("Die Datei ist leer.")
    if _upload_active or _inspect_active is not None:
        raise RestoreBusy("Es wird gerade eine Sicherung hochgeladen oder geprüft. Bitte warten, bis das fertig ist.")
    _upload_active = True  # sofort, vor dem ersten await: sonst kaemen zwei Uploads zugleich durch
    rid = restore.new_id()
    directory = layout.restore_dir / rid
    try:
        restore.make_private_dir(layout.restore_dir)
        await asyncio.to_thread(restore.sweep, layout)  # eine abgelaufene Vormerkung blockiert nichts mehr
        if restore.pending_exists(layout):
            raise PendingExists()
        free = store.free_bytes(layout.restore_dir)
        needed = (content_length or 0) + restore.SPACE_RESERVE
        if free < needed:
            raise NotEnoughSpace(
                f"Zu wenig freier Speicher für diese Datei ({_mb(free)} frei, etwa {_mb(needed)} nötig). Bitte zuerst Platz schaffen."
            )
        # Es gibt nur einen Zwischenstand: alles Alte (Vorgemerktes gibt es hier nicht) weg.
        await asyncio.to_thread(restore.cleanup_restore_dir, layout)
        restore.make_private_dir(directory)
        path = directory / restore.UPLOAD_NAME
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        total = 0
        buffer = bytearray()
        try:
            with os.fdopen(fd, "wb") as out:
                async for chunk in chunks:
                    total += len(chunk)
                    if total > limit:
                        raise BackupTooLarge(f"Die Datei ist größer als erlaubt (höchstens {_mb(limit)}).")
                    buffer += chunk
                    if len(buffer) >= _WRITE_BUFFER:
                        await asyncio.to_thread(out.write, bytes(buffer))
                        buffer.clear()
                if buffer:
                    await asyncio.to_thread(out.write, bytes(buffer))
                    buffer.clear()
                await asyncio.to_thread(out.flush)
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                raise NotEnoughSpace("Der Speicher ist beim Hochladen vollgelaufen.") from exc
            raise
        if total == 0:
            raise BackupError("Die Datei ist leer.")
        header = await asyncio.to_thread(restore.read_upload_header, path)
        meta = {
            "id": rid, "scope": scope.key, "created_at": time.time(), "state": "uploaded", "size": total,
            "header": {"mode": header["mode"], "created_at": header["created_at"], "app_version": header["app_version"]},
        }
        await asyncio.to_thread(restore.write_meta, layout, rid, meta)
        _schedule_sweep(layout)
        return _staged_view(meta)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        _upload_active = False


# ---------------------------------------------------------------------------
# Pruefen
# ---------------------------------------------------------------------------


def _inspect_sync(settings: Settings, layout: restore.Layout, rid: str, secret: str, instance_id: str | None, has_accounts: bool) -> dict[str, Any]:
    directory = layout.restore_dir / rid
    limits = restore.extract_limits(layout, max_unpacked=settings.restore_max_unpacked_bytes, max_entries=settings.restore_max_entries)
    known, _heads = migrate.known_revisions(layout.repo_root)
    staged = restore.stage_backup(
        directory / restore.UPLOAD_NAME, secret, directory / restore.STAGING_NAME, layout=layout, limits=limits, known=known,
        current_version=__version__, current_instance_id=instance_id, has_accounts=has_accounts,
    )
    restore.write_json_atomic(directory / FILES_NAME, {"files": staged.files, "compat": staged.compat, "unpacked_bytes": staged.unpacked_bytes})
    meta = restore.read_meta(layout, rid) or {}
    meta.update(state="ready", summary=staged.summary, unpacked_bytes=staged.unpacked_bytes)
    restore.write_meta(layout, rid, meta)
    with contextlib.suppress(OSError):
        os.unlink(directory / restore.UPLOAD_NAME)  # der verschluesselte Upload wird nicht mehr gebraucht
    return meta


async def inspect(settings: Settings, scope: Scope, rid: str, *, secret: str, instance_id: str | None, has_accounts: bool) -> dict[str, Any]:
    """Entschluesselt und prueft. Falsches Passwort und zu wenig Platz lassen den Upload stehen (noch
    einmal versuchen); jeder andere Fehler loescht den ganzen Zwischenstand."""
    global _inspect_active
    layout = layout_for(settings)
    meta = _meta(layout, scope, rid)
    if meta.get("state") == "ready":
        return _staged_view(meta)
    if meta.get("state") != "uploaded":
        raise WrongState("Diese Sicherung ist nicht (mehr) zum Prüfen bereit.")
    if _upload_active or _inspect_active is not None:
        raise RestoreBusy("Es wird gerade eine Sicherung hochgeladen oder geprüft. Bitte warten, bis das fertig ist.")
    try:
        token = await backups_service.acquire_exclusive(backups_service.EXCLUSIVE_CHECK)
    except backups_service.BackupBusy as exc:
        raise RestoreBusy(str(exc)) from exc
    _inspect_active = rid

    async def work() -> dict[str, Any]:
        global _inspect_active
        try:
            return await asyncio.to_thread(_inspect_sync, settings, layout, rid, secret, instance_id, has_accounts)
        except (WrongSecret, NotEnoughSpace):
            raise
        except BaseException:
            await asyncio.to_thread(shutil.rmtree, layout.restore_dir / rid, True)
            raise
        finally:
            _inspect_active = None
            backups_service.release_exclusive(token)

    done = await asyncio.shield(_spawn(work()))
    return _staged_view(done)


# ---------------------------------------------------------------------------
# Vormerken, Abbrechen
# ---------------------------------------------------------------------------


async def schedule(settings: Settings, scope: Scope, rid: str, *, sign_out_all: bool) -> dict[str, Any]:
    layout = layout_for(settings)
    meta = _meta(layout, scope, rid)
    if meta.get("state") != "ready":
        raise WrongState("Die Sicherung wurde noch nicht geprüft.")
    if restore.pending_exists(layout):
        raise PendingExists()
    directory = layout.restore_dir / rid
    info = restore.read_json(directory / FILES_NAME)
    if not isinstance(info, dict) or not isinstance(info.get("files"), dict) or not (directory / restore.STAGING_NAME).is_dir():
        raise UnknownRestore()
    staged = restore.Staged(summary=meta["summary"], files=info["files"], unpacked_bytes=int(info.get("unpacked_bytes", 0)),
                            compat=info.get("compat") if isinstance(info.get("compat"), dict) else {})
    pending = await asyncio.to_thread(
        restore.write_pending, layout, restore_id=rid, source=scope.kind, actor=scope.actor(), sign_out_all=sign_out_all, staged=staged,
    )
    meta["state"] = "scheduled"
    await asyncio.to_thread(restore.write_meta, layout, rid, meta)
    return _pending_view(pending)


async def cancel(settings: Settings) -> int:
    """Vormerkung und jeden Zwischenstand verwerfen. Gibt die Zahl der geloeschten Zwischenstaende zurueck."""
    if _upload_active or _inspect_active is not None:
        raise RestoreBusy("Es wird gerade eine Sicherung hochgeladen oder geprüft. Bitte warten, bis das fertig ist.")
    layout = layout_for(settings)
    ids = restore.list_ids(layout)
    restore.clear_pending(layout)
    await asyncio.to_thread(restore.cleanup_restore_dir, layout)
    return len(ids)


async def delete_replaced(settings: Settings) -> int:
    return await asyncio.to_thread(restore.remove_replaced, layout_for(settings))


def has_pending(settings: Settings) -> bool:
    return restore.pending_exists(layout_for(settings))


# ---------------------------------------------------------------------------
# Nach dem Start: Ergebnis ins Audit-Protokoll
# ---------------------------------------------------------------------------


def audit_actor(scope: Scope) -> tuple[str, str]:
    if scope.kind == "owner":
        return "user", scope.user_id or "unbekannt"
    return "anonymous", scope.ip or "einrichtung"


async def audit(session: AsyncSession, scope: Scope, action: str, *, outcome: str = "success", reason: str | None = None,
                detail: dict[str, Any] | None = None, target_id: str | None = None) -> None:
    actor_type, actor_id = audit_actor(scope)
    await audit_service.log(
        session, actor_type=actor_type, actor_id=actor_id, action=action, outcome=outcome, target_type="restore",
        target_id=target_id, reason=reason, detail=detail or {}, ip=scope.ip,
    )


async def record_result(settings: Settings) -> None:
    """Beim Start: ein Ergebnis von `boot.py`, das noch nicht im Audit-Protokoll steht, dort
    eintragen -- in die jetzt gueltige Datenbank. Danach aufraeumen. Nie ein Grund, nicht zu starten."""
    try:
        layout = layout_for(settings)
    except BackupError:
        return
    try:
        await asyncio.to_thread(restore.sweep, layout)
        result = restore.read_result(layout)
        if result is None or result.get("audit_logged"):
            return
        actor = result.get("actor") if isinstance(result.get("actor"), dict) else {}
        source = result.get("source")
        if actor.get("type") == "user" and actor.get("id"):
            actor_type, actor_id = "user", str(actor["id"])
        elif source == "cli":
            actor_type, actor_id = "system", "cli"
        else:
            actor_type, actor_id = "anonymous", str(actor.get("ip") or "einrichtung")
        ok = bool(result.get("ok"))
        async with session_scope() as session:
            await audit_service.log(
                session, actor_type=actor_type, actor_id=actor_id,
                action="system.restore.applied" if ok else "system.restore.failed", outcome="success" if ok else "failure",
                target_type="restore", target_id=result.get("id"), reason=None if ok else str(result.get("message"))[:500],
                detail={"source": source, "backup": result.get("backup") or {}, "replaced": result.get("replaced"),
                        "sign_out_all": result.get("sign_out_all"), "label": actor.get("label")},
                ip=actor.get("ip"),
            )
        result["audit_logged"] = True
        await asyncio.to_thread(restore.write_result, layout, result)
    except Exception:
        logger.exception("restore_record_result_failed")


async def failed_result(settings: Settings) -> dict[str, Any] | None:
    """Fuer `GET /auth/bootstrap`: das letzte Ergebnis, aber nur, wenn es ein Scheitern war und aus dem
    Assistenten kam (sonst nichts, es gibt ja noch kein Konto, das es sehen duerfte)."""
    try:
        layout = layout_for(settings)
    except BackupError:
        return None
    result = await asyncio.to_thread(restore.read_result, layout)
    if result is None or result.get("ok") or result.get("source") != "bootstrap":
        return None
    return {"ok": False, "message": _without_paths(result.get("message")), "at": result.get("at")}


_PATH_RE = re.compile(r"(?:/[^\s/'\"():,]+)+/?")


def _without_paths(message: object) -> str | None:
    """Ohne Anmeldung sichtbar (GET /auth/bootstrap): Dateipfade aus Fehlertexten (z. B. OSError)
    durch „…“ ersetzen, damit niemand im Netz den Aufbau des Datenordners erfährt."""
    if not isinstance(message, str):
        return None
    return _PATH_RE.sub("…", message)[:500]


def result_path(settings: Settings) -> Path:
    return layout_for(settings).restore_dir / restore.RESULT_NAME
