"""System: Infos und Sicherungen -- docs/04-API.md "System".

Rechte:
* Ansehen (`GET /system/info`, `GET /system/backups`, `GET /system/updates`, `GET /system/openapi.json`): `system.read`.
* Nach Updates suchen (`POST /system/updates/check`): ebenfalls `system.read`, aber hoechstens einmal pro
  Minute fuer alle zusammen (sonst 429) -- jede Pruefung ist eine Anfrage an ghcr.io.
* Alles Kritische: nur der Owner (`require_owner`), teils zusaetzlich mit dem aktuellen
  Passwort (`confirm_current_password`, gedrosselt wie ueberall: 400 falsch, 429 zu oft).
  Fehlt das Passwort ganz, antwortet der Endpunkt mit 403.

API-Doku: Swagger-Oberflaeche, ReDoc und `/openapi.json` gibt es ohne Anmeldung nur im
Entwicklungsmodus (`NODVARD_DECK_ENV=dev` oder `NODVARD_DECK_API_DOCS=1`, `config.Settings.api_docs`;
sonst 404). Fuer angemeldete Admins (und den Owner) liefert `GET /api/v1/system/openapi.json` das
OpenAPI-Dokument immer, auch in der Produktion -- fuer die App und Werkzeuge, die die Schnittstelle
lesen. Es steht dort mit allen gerade geladenen Erweiterungen; eine Oberflaeche dazu gibt es nicht.

Passwoerter stehen nur im Body, nie in der Adresse, und werden nie protokolliert. Antworten
mit Geheimnissen (Wiederherstellungsschluessel, Download) tragen `Cache-Control: no-store`.
"""

from __future__ import annotations

import asyncio
import os
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.engine import make_url
from starlette.background import BackgroundTask

from ...core import rate_limit
from ...core import restart as restart_service
from ...core import timezone as timezone_service
from ...core.backup import premigrate, store
from ...core.backup.errors import BackupError
from ...core.backup.tickets import TICKET_TTL_S, get_ticket_registry
from ...db import utcnow
from ...models import User
from ...services import audit as audit_service
from ...services import backups as backups_service
from ...services import restore as restore_service
from ...services import update_check as update_check_service
from ...version import __version__
from ..deps import OwnerUser, SessionDep, SettingsDep, require_permission
from .auth import confirm_current_password
from .restore_common import (
    RestoreInspectIn,
    RestoreScheduleIn,
    decode_header_password,
    do_cancel,
    do_inspect,
    do_schedule,
    do_upload,
)
from .restore_common import http_error as restore_http_error

router = APIRouter(prefix="/system", tags=["system"])

_NO_STORE = {"Cache-Control": "no-store"}

UPDATE_CHECK_WINDOW_S = 60
_update_check_window = rate_limit.SlidingWindow(1, UPDATE_CHECK_WINDOW_S)
"""`POST /system/updates/check`: einmal pro Minute, fuer alle Nutzer zusammen (es ist dieselbe Anfrage ins Netz)."""


class PasswordConfirm(BaseModel):
    current_password: str = Field(default="", max_length=1024)
    """Aktuelles Passwort des Kontos zur Bestaetigung."""


class BackupConfigIn(PasswordConfirm):
    """`current_password` ist optional im Body (additiv): nur noetig, wenn die Aenderung
    Sicherungen gefaehrdet (weniger behalten, ausschalten, anderer Ordner)."""

    enabled: bool
    schedule: str = Field(max_length=200)
    keep: int
    dir: str | None = Field(default=None, max_length=1024)
    include_runs: bool = False


class BackupKeyIn(PasswordConfirm):
    password: str = Field(max_length=1024)
    """Neues Sicherungspasswort (mindestens 12 Zeichen)."""


class DownloadIn(PasswordConfirm):
    mode: Literal["schluessel", "passwort"]
    password: str | None = Field(default=None, max_length=1024)
    """Nur bei `mode="passwort"`: Einmal-Passwort fuer genau diese Datei (mindestens 12 Zeichen)."""


async def _confirm(request: Request, session, user: User, password: str, action: str, *, detail: str | None = None) -> None:
    if not password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=detail or "Bitte zur Bestätigung das aktuelle Passwort eingeben."
        )
    await confirm_current_password(request, session, user, password, action)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (backups_service.BackupBusy, backups_service.NoBackupKey, backups_service.NotSqlite)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, backups_service.NotEnoughSpace):
        return HTTPException(status_code=status.HTTP_507_INSUFFICIENT_STORAGE, detail=str(exc))
    if isinstance(exc, (store.TargetNotAllowed, ValueError)):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diese Sicherung gibt es nicht.")
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ---------------------------------------------------------------------------
# Ansehen
# ---------------------------------------------------------------------------


@router.get("/info", dependencies=[Depends(require_permission("system.read"))])
async def system_info(session: SessionDep, settings: SettingsDep) -> dict:
    try:
        free = store.free_bytes(settings.data_dir)
    except OSError:
        free = None
    return {
        "version": __version__,
        # Beide aus `Settings`: Variable, sonst die Datei im Image (`nodvard_deck.image_info`); nie "".
        "build": (settings.build or "").strip() or None,
        "image": (settings.image or "").strip() or None,
        "timezone": await timezone_service.get_timezone(session),
        "data_dir": str(settings.data_dir),
        "data_free_bytes": free,
        "database": make_url(settings.database_url).get_backend_name(),
        "updater_available": False,
        # Die letzten Kopien der Datenbank von vor einer Migration (`nodvard_deck.boot`), neueste zuerst. Ohne Pfade.
        "pre_update_copies": await asyncio.to_thread(premigrate.list_copies, settings.data_dir),
    }


@router.get("/openapi.json", dependencies=[Depends(require_permission("system.read"))])
async def openapi_document(request: Request) -> JSONResponse:
    """Das OpenAPI-Dokument dieser Installation (Admin/Owner, Recht `system.read`; 401 ohne
    Anmeldung, 403 ohne das Recht). Ersatz fuer das oeffentliche `/openapi.json`, das
    ausserhalb des Entwicklungsmodus abgeschaltet ist."""
    app = request.app
    # FastAPI merkt sich das Dokument nach dem ersten Abruf. Erweiterungen koennen seitdem
    # dazugekommen oder weggefallen sein (`ext.runtime.mount_router`), also neu aufbauen.
    app.openapi_schema = None
    return JSONResponse(app.openapi(), headers=_NO_STORE)


class UpdateStatusOut(BaseModel):
    """Stand von "Nach Updates suchen" (`core.updates`, `services.update_check`)."""

    current: str
    """Laufende Version: beim offiziellen Image seine genaue Version (auch eine Vorabversion wie `0.6.0-rc1`),
    sonst die Version des Codes (`services.update_check.running_version`)."""
    latest: str | None
    """Neueste Version im Kanal laut letzter gelungener Pruefung. `null`: noch nie geprueft (`checked_at` ist dann
    auch `null`) oder geprueft, aber keine passende Version gefunden (`checked_at` gesetzt)."""
    latest_digest: str | None = None
    """sha256 des Manifests der neuesten Version, wenn bekannt."""
    available: bool
    """`latest` ist neuer als `current`."""
    channel: Literal["stable", "beta"]
    enabled: bool
    """Taegliche Pruefung an (`system.update_check.enabled`)."""
    checked_at: str | None
    """Zeitpunkt der letzten gelungenen Pruefung (ISO 8601, UTC)."""
    attempted_at: str | None = None
    """Zeitpunkt des letzten Versuchs, auch eines gescheiterten."""
    source: Literal["live", "cache", "offline"]
    """`live` gerade abgefragt, `cache` letzter Stand, `offline` letzter Versuch gescheitert."""
    error: str | None = None
    official_image: bool
    """Der Container stammt aus dem offiziellen Image (`Settings.image`: Datei im Image bzw. `NODVARD_DECK_IMAGE`)."""
    image: str | None = None
    """Herkunft des Images (ohne Tag), `null` bei einem selbst gebauten."""
    official_image_name: str
    """Name des offiziellen Images (ohne Tag) fuer die Anleitungen, z. B. `ghcr.io/nodvard/deck`."""
    helper: bool = False
    """Ein Helfer, der das Update selbst einspielt, ist da (kommt spaeter; bisher immer `false`)."""
    release_notes_url: str | None = None


@router.get("/updates", dependencies=[Depends(require_permission("system.read"))])
async def update_status(session: SessionDep, settings: SettingsDep) -> UpdateStatusOut:
    """Letzter bekannter Stand, ohne Anfrage ins Netz."""
    return UpdateStatusOut(**await update_check_service.current_status(session, settings))


@router.post("/updates/check", dependencies=[Depends(require_permission("system.read"))])
async def update_check(session: SessionDep, settings: SettingsDep) -> UpdateStatusOut:
    """Jetzt bei ghcr.io nachsehen. Scheitert das (offline, Fehler), kommt der letzte Stand mit `source: offline`
    und `error` zurueck, kein Fehlercode. Hoechstens einmal pro Minute, sonst 429."""
    wait = _update_check_window.hit("global")
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Gerade erst nachgesehen. Bitte in {rate_limit.wait_text(wait)} erneut versuchen.",
            headers={"Retry-After": str(wait)},
        )
    return UpdateStatusOut(**await update_check_service.check_now(session, settings))


@router.get("/backups", dependencies=[Depends(require_permission("system.read"))])
async def list_backups(session: SessionDep, settings: SettingsDep) -> dict:
    return await backups_service.overview(session, settings)


# ---------------------------------------------------------------------------
# Einstellen (Owner)
# ---------------------------------------------------------------------------


@router.put("/backups/config")
async def put_backup_config(
    payload: BackupConfigIn, request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep
) -> dict:
    """Zeitplan, Anzahl, Ordner. Mit aktuellem Passwort (`current_password`, sonst 403), wenn
    weniger Sicherungen behalten werden, die automatische Sicherung ausgeschaltet oder der
    Ordner geaendert wird; alles andere geht ohne."""
    body = payload.model_dump(exclude={"current_password"})
    reasons = backups_service.protected_changes(await backups_service.load_config(session), body, settings)
    if reasons:
        await _confirm(
            request, session, user, payload.current_password, "sicherung_einstellungen",
            detail=f"Für diese Änderung ({', '.join(reasons)}) bitte zur Bestätigung das aktuelle Passwort eingeben.",
        )
    try:
        await backups_service.save_config(session, settings, body, user_id=user.id)
    except (BackupError, ValueError) as exc:
        raise _http_error(exc) from exc
    # Job neu planen: schedule() schreibt ueber eine eigene Session (D-14) -> vorher committen.
    await session.commit()
    await backups_service.sync_job()
    return await backups_service.overview(session, settings)


@router.put("/backups/key")
async def put_backup_key(payload: BackupKeyIn, request: Request, user: OwnerUser, session: SessionDep) -> JSONResponse:
    """Sicherungspasswort festlegen oder aendern. Die Antwort enthaelt EINMALIG den
    Wiederherstellungsschluessel; gespeichert wird er nirgends."""
    await _confirm(request, session, user, payload.current_password, "sicherungsschluessel")
    try:
        key = await backups_service.set_key(session, payload.password, user_id=user.id)
    except ValueError as exc:
        raise _http_error(exc) from exc
    await session.commit()
    await backups_service.sync_job()
    return JSONResponse(
        {"key_id": key.key_id, "recipient": key.recipient, "recovery_key": key.identity},
        headers=_NO_STORE,
    )


@router.post("/backups/run", status_code=status.HTTP_202_ACCEPTED)
async def run_backup_now(user: OwnerUser) -> dict:
    try:
        await backups_service.start_manual_run(user_id=user.id)
    except BackupError as exc:
        raise _http_error(exc) from exc
    return {"started": True}


# ---------------------------------------------------------------------------
# Herunterladen (Owner + Passwort, Einmal-Ticket)
# ---------------------------------------------------------------------------


def _ticket_out(ticket) -> dict:
    return {
        "ticket": ticket.ticket, "status": ticket.status, "filename": ticket.filename,
        "size": ticket.size, "error": ticket.error, "expires_in": int(TICKET_TTL_S),
        "url": f"/api/v1/system/backups/download/{ticket.ticket}",
        "job_id": ticket.job_id,
    }


@router.post("/backups/download", status_code=status.HTTP_202_ACCEPTED)
async def prepare_download(
    payload: DownloadIn, request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep
) -> JSONResponse:
    """Baut die verschluesselte Datei im Hintergrund. Stand ueber `GET .../download-jobs/{job_id}`
    (eigene ID, taugt nicht zum Herunterladen), danach die Datei ueber `GET .../download/{ticket}`
    (einmalig, 5 Minuten)."""
    await _confirm(request, session, user, payload.current_password, "sicherung_herunterladen")
    await session.commit()
    try:
        ticket = await backups_service.prepare_download(settings, user_id=user.id, mode=payload.mode, password=payload.password)
    except (BackupError, ValueError) as exc:
        raise _http_error(exc) from exc
    return JSONResponse(_ticket_out(ticket), status_code=status.HTTP_202_ACCEPTED, headers=_NO_STORE)


@router.get("/backups/download-jobs/{job_id}")
async def download_job_status(job_id: str, user: OwnerUser) -> JSONResponse:
    """Stand eines Downloads ueber die Status-ID. Die Oberflaeche fragt das jede Sekunde ab;
    anders als das Ticket gehoert diese ID in jede Adresse und jedes Zugriffsprotokoll."""
    found = get_ticket_registry().get_by_job(job_id)
    if found is None or found.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter oder abgelaufener Download.")
    return JSONResponse(_ticket_out(found), headers=_NO_STORE)


@router.get("/backups/download/{ticket}/status", deprecated=True)
async def download_status(ticket: str, user: OwnerUser) -> JSONResponse:
    """Veraltet: Stand ueber das Ticket selbst (steht dann in der Adresse). Bleibt als
    Uebergang; neu ist `GET .../download-jobs/{job_id}`."""
    found = get_ticket_registry().get(ticket)
    if found is None or found.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes oder abgelaufenes Ticket.")
    return JSONResponse(_ticket_out(found), headers=_NO_STORE)


@router.get("/backups/download/{ticket}")
async def download(ticket: str, session: SessionDep):
    """Normaler Browser-Download ohne Anmelde-Header: das Ticket ist die Berechtigung.
    Es gilt genau einmal, 5 Minuten, und nur, solange sein Nutzer noch aktiver Owner ist."""
    registry = get_ticket_registry()
    pending = registry.get(ticket)
    if pending is not None and pending.status == "building":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Die Sicherung wird noch erstellt.")
    found = registry.consume(ticket)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes oder abgelaufenes Ticket.")
    owner = await session.get(User, found.user_id)
    if owner is None or not owner.is_active or not owner.is_owner:
        if found.delete_after:
            _unlink(found.path)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes oder abgelaufenes Ticket.")
    if not os.path.isfile(found.path) or os.path.islink(found.path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Die Datei ist nicht mehr da.")
    await audit_service.log(
        session, actor_type="user", actor_id=found.user_id, action="system.backup.downloaded", outcome="success",
        target_type="backup", target_id=found.filename,
        detail={"size": found.size, "stored": not found.delete_after},
    )
    await session.commit()
    return FileResponse(
        found.path, filename=found.filename, media_type="application/octet-stream",
        headers=_NO_STORE, background=BackgroundTask(_unlink, found.path) if found.delete_after else None,
    )


def _unlink(path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Einzelne automatische Sicherung
# ---------------------------------------------------------------------------


@router.post("/backups/{name}/ticket")
async def stored_backup_ticket(
    name: str, payload: PasswordConfirm, request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep
) -> JSONResponse:
    await _confirm(request, session, user, payload.current_password, "sicherung_herunterladen")
    try:
        ticket = await backups_service.ticket_for_stored(settings, session, user_id=user.id, name=name)
    except (BackupError, FileNotFoundError) as exc:
        raise _http_error(exc) from exc
    return JSONResponse(_ticket_out(ticket), headers=_NO_STORE)


@router.post("/backups/{name}/verify")
async def verify_backup(name: str, user: OwnerUser, session: SessionDep, settings: SettingsDep) -> dict:
    try:
        target = await backups_service.target_of(settings, session)
        path = await asyncio.to_thread(store.backup_path, target, name)
        result = await asyncio.to_thread(store.verify, path, now_iso=utcnow().isoformat())
    except (BackupError, FileNotFoundError) as exc:
        raise _http_error(exc) from exc
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="system.backup.verified",
        outcome="success" if result["ok"] else "failure", target_type="backup", target_id=name,
    )
    return result


@router.delete("/backups/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backup(
    name: str, payload: PasswordConfirm, request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep
) -> None:
    await _confirm(request, session, user, payload.current_password, "sicherung_loeschen")
    try:
        target = await backups_service.target_of(settings, session)
        path = await asyncio.to_thread(store.backup_path, target, name)
        await asyncio.to_thread(store.delete, path)
    except (BackupError, FileNotFoundError) as exc:
        raise _http_error(exc) from exc
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="system.backup.deleted", outcome="success",
        target_type="backup", target_id=name,
    )


# ---------------------------------------------------------------------------
# Wiederherstellen (Owner + Passwort) -- docs/04-API.md "System", core/backup/restore.py
# ---------------------------------------------------------------------------


def _scope(user: User, request: Request) -> restore_service.Scope:
    ip = request.client.host if request.client else None
    return restore_service.Scope("owner", user_id=user.id, label=user.username, ip=ip)


@router.get("/restore/status")
async def restore_status(
    request: Request, session: SessionDep, settings: SettingsDep,
    user: Annotated[User, Depends(require_permission("system.read"))],
) -> JSONResponse:
    """Vorgemerkte Wiederherstellung, Zwischenstand (nur fuer den Owner), Ergebnis des letzten
    Einspielens, Grenzen. Fuer alle mit `system.read`."""
    try:
        scope = _scope(user, request) if user.is_owner else None
        return JSONResponse(await restore_service.status(settings, scope), headers=_NO_STORE)
    except backups_service.NotSqlite as exc:
        raise restore_http_error(exc) from exc


@router.put("/restore/upload", status_code=status.HTTP_201_CREATED)
async def restore_upload(
    request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep,
    x_confirm_password: Annotated[str | None, Header(description="Anmeldepasswort, prozentkodiert (UTF-8).")] = None,
) -> JSONResponse:
    """Laedt eine Sicherung als ROHEN Datenstrom (`Content-Type: application/octet-stream`, kein
    Formular) hoch. Owner, Passwort im Kopf `X-Confirm-Password`; beides wird geprueft, BEVOR der
    Body gelesen wird. Obergrenze und freier Platz stehen schon am Kopf der Anfrage fest."""
    await _confirm(request, session, user, decode_header_password(x_confirm_password), "wiederherstellen_hochladen")
    await session.commit()
    try:
        view = await do_upload(request, session, settings, _scope(user, request))
    except backups_service.NotSqlite as exc:
        raise restore_http_error(exc) from exc
    return JSONResponse(view, status_code=status.HTTP_201_CREATED, headers=_NO_STORE)


@router.post("/restore/{restore_id}/inspect")
async def restore_inspect(
    restore_id: str, payload: RestoreInspectIn, request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep
) -> JSONResponse:
    """Entschluesselt und prueft die hochgeladene Sicherung (Staging) und liefert die
    Zusammenfassung: Datum, Version, Kennung der Installation, Owner-Name, Zahl der Nutzer und
    Server, Erweiterungen, Warnungen. Es wird noch NICHTS eingespielt."""
    view = await do_inspect(session, settings, _scope(user, request), restore_id, payload)
    return JSONResponse(view, headers=_NO_STORE)


class OwnerScheduleIn(PasswordConfirm, RestoreScheduleIn):
    """Vormerken: Owner mit aktuellem Passwort; ersetzt ALLES, auch die Konten."""


@router.post("/restore/{restore_id}/schedule")
async def restore_schedule(
    restore_id: str, payload: OwnerScheduleIn, request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep
) -> JSONResponse:
    """Merkt die gepruefte Sicherung zum Einspielen vor (`restore/pending.json`). Eingespielt wird
    beim naechsten Start -- der Neustart folgt mit `POST /system/restart`. Gilt eine Stunde."""
    await _confirm(request, session, user, payload.current_password, "wiederherstellen_vormerken")
    await session.commit()
    pending = await do_schedule(session, settings, _scope(user, request), restore_id, payload)
    return JSONResponse(pending, headers=_NO_STORE)


@router.delete("/restore/pending", status_code=status.HTTP_204_NO_CONTENT)
async def restore_cancel(request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep) -> None:
    """Verwirft die Vormerkung und jeden Zwischenstand (auch hochgeladene, noch nicht geprueft)."""
    await do_cancel(session, settings, _scope(user, request))


@router.delete("/restore/replaced", status_code=status.HTTP_204_NO_CONTENT)
async def restore_delete_replaced(
    payload: PasswordConfirm, request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep
) -> None:
    """Loescht den alten Stand, der nach einem Einspielen unter `restore/replaced-...` liegen bleibt
    (er enthaelt die alten Schluessel und Konten). Owner mit Passwort."""
    await _confirm(request, session, user, payload.current_password, "wiederherstellen_alten_stand_loeschen")
    try:
        count = await restore_service.delete_replaced(settings)
    except backups_service.NotSqlite as exc:
        raise restore_http_error(exc) from exc
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="system.restore.replaced_deleted", outcome="success",
        target_type="restore", detail={"removed": count},
    )


@router.post("/restart", status_code=status.HTTP_202_ACCEPTED)
async def restart(payload: PasswordConfirm, request: Request, user: OwnerUser, session: SessionDep) -> dict:
    """Beendet Nodvard Deck mit dem Rueckgabewert 75; der Container startet es neu (Neustart-Regel
    `restart: unless-stopped`). Beim Start wird eine vorgemerkte Wiederherstellung eingespielt.
    Owner mit Passwort. Ohne Neustart-Regel bleibt der Dienst danach aus."""
    await _confirm(request, session, user, payload.current_password, "neustart")
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="system.restart.requested", outcome="success",
        target_type="system", ip=request.client.host if request.client else None,
    )
    await session.commit()
    restart_service.request_restart(request.app)
    return {"restarting": True, "exit_code": restart_service.EXIT_CODE}
