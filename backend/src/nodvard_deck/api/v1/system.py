"""System: Infos, Updates und Sicherungen (docs/04-API.md, Abschnitt „System“)."""

from __future__ import annotations

import asyncio
import os
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from nodvard_sdk import max_body_bytes
from pydantic import BaseModel, Field
from sqlalchemy.engine import make_url
from starlette.background import BackgroundTask

from ...config import get_settings
from ...core import rate_limit, updates
from ...core import restart as restart_service
from ...core import timezone as timezone_service
from ...core import trusted_proxies as trusted_proxies_service
from ...core.backup import premigrate, store
from ...core.backup.errors import BackupError
from ...core.backup.tickets import TICKET_TTL_S, get_ticket_registry
from ...db import utcnow
from ...models import User
from ...services import audit as audit_service
from ...services import backups as backups_service
from ...services import restore as restore_service
from ...services import update_check as update_check_service
from ...services import update_helper as update_helper_service
from ...version import __version__
from ..deps import CurrentUser, OwnerUser, SessionDep, SettingsDep, require_permission
from ..proxy_headers import SCOPE_KEY as FORWARDING_SCOPE_KEY
from ..proxy_headers import Forwarding
from .auth import TotpMissing, confirm_current_password, confirm_current_totp
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

# Ansehen braucht `system.read`. Alles Kritische ist Owner-only, meist mit aktuellem Passwort; ist Zwei-Faktor an,
# verlangen Download, Vormerken, Update und Rückweg zusätzlich den Code aus der App (fehlt er: 403 `totp_missing`).

# Antworten mit Geheimnissen (Wiederherstellungsschlüssel, Download) dürfen nicht im Cache landen.
_NO_STORE = {"Cache-Control": "no-store"}

UPDATE_CHECK_WINDOW_S = 60
# Jede Prüfung ist eine Anfrage ins Netz, darum einmal pro Minute für alle Nutzer zusammen.
_update_check_window = rate_limit.SlidingWindow(1, UPDATE_CHECK_WINDOW_S)


class PasswordConfirm(BaseModel):
    current_password: str = Field(default="", max_length=1024)
    """Aktuelles Passwort des Kontos zur Bestätigung."""


class BackupConfigIn(PasswordConfirm):
    """`current_password` ist nur nötig, wenn die Änderung Sicherungen gefährdet (weniger behalten, ausschalten,
    anderer Ordner)."""

    enabled: bool
    schedule: str = Field(max_length=200)
    keep: int
    dir: str | None = Field(default=None, max_length=1024)
    include_runs: bool = False


class BackupKeyIn(PasswordConfirm):
    password: str = Field(max_length=1024)
    """Neues Sicherungspasswort (mindestens 12 Zeichen)."""


class TotpConfirm(BaseModel):
    totp_code: str = Field(default="", max_length=64)
    """Aktueller Zwei-Faktor-Code aus der App; Pflicht, wenn Zwei-Faktor an ist (sonst 403 mit `code: "totp_missing"`).
    Im Schema optional, ohne Zwei-Faktor entfällt er."""


class DownloadIn(PasswordConfirm, TotpConfirm):
    mode: Literal["schluessel", "passwort"]
    password: str | None = Field(default=None, max_length=1024)
    """Nur bei `mode="passwort"`: Einmal-Passwort für genau diese Datei (mindestens 12 Zeichen)."""


class StoredDownloadIn(PasswordConfirm, TotpConfirm):
    """Eine gespeicherte Sicherung herunterladen: Passwort und, wenn Zwei-Faktor an ist, der Code."""


async def _confirm(request: Request, session, user: User, password: str, action: str, *, detail: str | None = None) -> None:
    if not password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=detail or "Bitte zur Bestätigung das aktuelle Passwort eingeben."
        )
    await confirm_current_password(request, session, user, password, action)


async def _confirm_with_code(
    request: Request, session, settings, user: User, payload, action: str
) -> JSONResponse | None:
    """Passwort und, wenn Zwei-Faktor an ist, der Code aus der App, für alles, was Daten samt Schlüsseln herausgibt oder
    ersetzt. `None` heißt bestätigt; ein fehlender Code kommt als fertige 403-Antwort (`totp_missing`) zurück."""
    # Eine Sicherung enthält auch den Zwei-Faktor-Schlüssel: Wer sie nur mit dem Passwort bekäme, könnte beliebig
    # viele gültige Codes erzeugen.
    await _confirm(request, session, user, payload.current_password, action)
    try:
        await confirm_current_totp(request, session, settings, user, payload.totp_code, action)
    except TotpMissing as exc:
        return exc.response()
    return None


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


@router.get("/info", dependencies=[Depends(require_permission("system.read"))])
async def system_info(session: SessionDep, settings: SettingsDep) -> dict:
    try:
        free = store.free_bytes(settings.data_dir)
    except OSError:
        free = None
    return {
        "version": __version__,
        # Aus `Settings` (Variable, sonst Datei im Image); leer wird zu `null`.
        "build": (settings.build or "").strip() or None,
        "image": (settings.image or "").strip() or None,
        "timezone": await timezone_service.get_timezone(session),
        "data_dir": str(settings.data_dir),
        "data_free_bytes": free,
        "database": make_url(settings.database_url).get_backend_name(),
        # Gleiche Quelle wie `helper` in `GET /system/updates`.
        "updater_available": await asyncio.to_thread(update_helper_service.helper_present, settings),
        # Kopien der Datenbank von vor einer Migration, neueste zuerst, ohne Pfade.
        "pre_update_copies": await asyncio.to_thread(premigrate.list_copies, settings.data_dir),
    }


class ConnectionOut(BaseModel):
    """Wie diese Anfrage bei Nodvard Deck ankommt (Einstellungen, Karte „Zugriff über einen Proxy“)."""

    peer: str | None
    """Direkte Gegenstelle der Verbindung (bei einem Proxy: dessen Adresse); `null` ohne IP (Unix-Socket)."""
    client: str | None
    """Die Adresse, die Nodvard Deck für diese Anfrage verwendet (Login-Drossel, Protokoll)."""
    scheme: str
    """`http` oder `https`, wie Nodvard Deck die Anfrage sieht (entscheidet über `Secure` am Anmelde-Cookie)."""
    via_trusted_proxy: bool
    """Die Gegenstelle ist ein eingetragener Proxy, seine `X-Forwarded-*` gelten."""
    forwarded_headers: bool
    """Die Anfrage trug `X-Forwarded-For` oder `X-Forwarded-Proto`, egal ob sie galten. Zusammen mit
    `via_trusted_proxy: false` ein Hinweis auf einen Proxy, der noch nicht eingetragen ist."""
    trusted_proxies: list[str]
    """Die gerade gültige Liste."""
    trusted_proxies_source: Literal["setting", "env"]
    """`env`: die Liste kommt aus `NODVARD_DECK_TRUSTED_PROXIES` und ist in den Einstellungen nicht änderbar."""


@router.get("/connection", dependencies=[Depends(require_permission("system.read"))])
async def connection_info(request: Request, settings: SettingsDep) -> ConnectionOut:
    """Gegenstelle, verwendete Client-Adresse und Schema dieser Anfrage (`api/proxy_headers.py`)."""
    info = request.scope.get(FORWARDING_SCOPE_KEY)
    if not isinstance(info, Forwarding):  # ohne die Middleware (nie im Betrieb): wie ohne Proxy
        info = Forwarding(peer=request.client.host if request.client else None, trusted=False, forwarded_headers=False)
    peer_ip = trusted_proxies_service.parse_address(info.peer)
    trust = trusted_proxies_service.current()
    return ConnectionOut(
        peer=str(peer_ip) if peer_ip is not None else info.peer,
        client=request.client.host if request.client else None,
        scheme="https" if request.url.scheme in ("https", "wss") else "http",
        via_trusted_proxy=info.trusted,
        forwarded_headers=info.forwarded_headers,
        trusted_proxies=list(trust.entries),
        trusted_proxies_source="env" if settings.trusted_proxies is not None else "setting",
    )


@router.get("/openapi.json", dependencies=[Depends(require_permission("system.read"))])
async def openapi_document(request: Request) -> JSONResponse:
    """Das OpenAPI-Dokument dieser Installation (Recht `system.read`; 401 ohne Anmeldung, 403 ohne das Recht)."""
    # Ersatz für das öffentliche `/openapi.json`, das außerhalb des Entwicklungsmodus abgeschaltet ist.
    app = request.app
    # FastAPI hält das Dokument nach dem ersten Abruf fest, Erweiterungen kommen aber dazu oder fallen weg: neu aufbauen
    # (`ext.runtime.mount_router`).
    app.openapi_schema = None
    return JSONResponse(app.openapi(), headers=_NO_STORE)


class UpdateStatusOut(BaseModel):
    """Stand von "Nach Updates suchen" (`core.updates`, `services.update_check`)."""

    current: str
    """Laufende Version: beim offiziellen Image seine genaue Version (auch eine Vorabversion wie `0.6.0-rc1`),
    sonst die Version des Codes (`services.update_check.running_version`)."""
    latest: str | None
    """Neueste Version im Kanal laut letzter gelungener Prüfung. `null`: noch nie geprüft (`checked_at` ist dann
    auch `null`) oder geprüft, aber keine passende Version gefunden (`checked_at` gesetzt)."""
    latest_digest: str | None = None
    """sha256 des Manifests der neuesten Version, wenn bekannt."""
    available: bool
    """`latest` ist neuer als `current`."""
    channel: Literal["stable", "beta"]
    enabled: bool
    """Tägliche Prüfung an (`system.update_check.enabled`)."""
    checked_at: str | None
    """Zeitpunkt der letzten gelungenen Prüfung (ISO 8601, UTC)."""
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
    """Name des offiziellen Images (ohne Tag) für die Anleitungen, z. B. `ghcr.io/nodvard/deck`."""
    helper: bool = False
    """Ein Update-Helfer, der das Update selbst einspielt, ist da und antwortet (`GET /system/updates/helper`; gleich
    `updater_available` in `GET /system/info`)."""
    release_notes_url: str | None = None


@router.get("/updates", dependencies=[Depends(require_permission("system.read"))])
async def update_status(session: SessionDep, settings: SettingsDep) -> UpdateStatusOut:
    """Letzter bekannter Stand, ohne Anfrage ins Netz."""
    result = await update_check_service.current_status(session, settings)
    result["helper"] = await asyncio.to_thread(update_helper_service.helper_present, settings)
    return UpdateStatusOut(**result)


@router.post("/updates/check", dependencies=[Depends(require_permission("system.read"))])
async def update_check(session: SessionDep, settings: SettingsDep) -> UpdateStatusOut:
    """Jetzt bei ghcr.io nachsehen. Scheitert das (offline, Fehler), kommt der letzte Stand mit `source: offline`
    und `error` zurück, kein Fehlercode. Höchstens einmal pro Minute, sonst 429."""
    wait = _update_check_window.hit("global")
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Gerade erst nachgesehen. Bitte in {rate_limit.wait_text(wait)} erneut versuchen.",
            headers={"Retry-After": str(wait)},
        )
    result = await update_check_service.check_now(session, settings)
    result["helper"] = await asyncio.to_thread(update_helper_service.helper_present, settings)
    return UpdateStatusOut(**result)


class HelperTargetOut(BaseModel):
    current_version: str | None
    """Laufende Version laut Image (nur, was der Helfer selbst festgestellt hat)."""
    floating_tag: str | None
    """`latest` oder `X.Y`; `null`, wenn die Version fest eingetragen ist."""
    pinned: bool
    """Die Version steht fest in der Compose-Datei (`X.Y.Z` oder Digest): der Helfer aktualisiert dann nicht."""


class HelperBusyOut(BaseModel):
    id: str
    action: Literal["update", "rollback"]
    step: str
    """Fester Bezeichner des Schritts (z. B. `pulled`, `started`)."""
    since: int
    """Beginn (Unix-Zeit, Sekunden)."""


class HelperPreviousOut(BaseModel):
    version: str
    """Auf diese Version geht der Rückweg."""
    until: int
    """So lange geht er (Unix-Zeit, Sekunden)."""
    data_revert: bool | None = None
    """Gehen die Daten mit zurück? `true`: diese Version hat beim Start die Datenbank umgebaut, alles seit `data_since`
    geht für das Dashboard verloren (`accept_data_loss` ist Pflicht); `false`: die Daten bleiben, wie sie sind; `null`:
    unklar, der Rückweg wird mit `data_unclear` abgelehnt."""
    data_since: int | None = None
    """Nur bei `data_revert: true`: Beginn des Umbaus der Datenbank (Unix-Zeit, Sekunden), die Kopie ist von direkt
    davor; `null`, wenn der Zeitpunkt nicht lesbar ist."""


class HelperResultOut(BaseModel):
    id: str
    action: Literal["update", "rollback"]
    from_: str | None = Field(alias="from", serialization_alias="from")
    to: str | None
    outcome: Literal["applied", "reverted", "rolled_back", "refused", "aborted", "failed_manual", "external_change"]
    code: str | None
    """Fester Bezeichner des Grundes (Texte hat die Oberfläche)."""
    finished_at: int


class HelperPendingOut(BaseModel):
    id: str
    action: Literal["update", "rollback"]
    to: str | None
    at: int


class UpdateHelperOut(BaseModel):
    """Zustand des Update-Helfers. Alle Gründe und Schritte sind feste Bezeichner, nie Freitext."""

    present: bool
    """Der Helfer ist eingerichtet und antwortet (Herzschlag höchstens 90 s alt)."""
    reason: Literal["missing", "stale", "unsafe", "proto", "invalid"] | None
    """Warum `present` falsch ist: nicht eingerichtet, antwortet nicht, Kanal unsicher, andere Protokollversion,
    unlesbarer Status."""
    ready: bool
    """Der Helfer könnte jetzt ein Update einspielen."""
    ready_reason: str | None
    """Grund des Helfers, wenn er nicht bereit ist (oder ein Hinweis wie `channel_cluttered`). `finishing`: der letzte
    Vorgang ist entschieden (Ergebnis in `last_result`), der Helfer räumt nur noch auf; `busy` und `previous` sind dann
    `null`."""
    state: Literal["idle", "busy", "unsafe", "error"] | None
    helper_version: str | None
    heartbeat_at: int | None
    target: HelperTargetOut | None
    busy: HelperBusyOut | None
    previous: HelperPreviousOut | None
    """Rückweg auf die Version davor, solange er gilt; `null`, solange der Helfer nach einem Commit aufräumt (auch
    während `finishing`): Der neue erscheint, sobald er damit fertig ist."""
    last_result: HelperResultOut | None
    pending: HelperPendingOut | None
    """Eigene Anforderung dieses Dashboards, für die noch kein Ergebnis da ist."""


class UpdateApplyIn(PasswordConfirm):
    version: str = Field(max_length=64)
    """Die Version, auf die aktualisiert werden soll (die neueste gefundene)."""
    totp_code: str = Field(default="", max_length=64)
    """Aktueller Zwei-Faktor-Code, Pflicht, wenn Zwei-Faktor an ist."""


class UpdateRollbackIn(PasswordConfirm):
    accept_data_loss: bool = False
    """Pflicht (`true`), wenn beim Rückweg die Daten mit zurückgehen: alles seit dem Update geht verloren."""
    totp_code: str = Field(default="", max_length=64)
    """Aktueller Zwei-Faktor-Code, Pflicht, wenn Zwei-Faktor an ist."""


class UpdateRequestedOut(BaseModel):
    request_id: str
    action: Literal["update", "rollback"]
    from_: str | None = Field(alias="from", serialization_alias="from")
    to: str
    data_revert: bool = False
    """Nur beim Rückweg: die Daten gehen mit auf den Stand vor dem Update zurück."""


def _helper_refused(exc: update_helper_service.HelperRefused) -> JSONResponse:
    content: dict = {"detail": exc.message, "code": exc.code}
    if exc.helper_reason is not None:
        content["helper_reason"] = exc.helper_reason
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return JSONResponse(content, status_code=exc.status_code, headers=headers)


async def _helper_audit(session, request: Request, user: User, action: str, *, outcome: str, detail: dict) -> None:
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action=action, outcome=outcome, target_type="system",
        detail=detail, ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


async def _confirm_helper_request(
    request: Request, session, settings, user: User, payload, *, kind: str, audit_action: str, detail: dict
) -> JSONResponse | None:
    """Owner, Passwort, ggf. Zwei-Faktor-Code. Jede Ablehnung steht im Audit-Protokoll. `None` = bestätigt."""
    if not user.is_owner:
        await _helper_audit(session, request, user, audit_action, outcome="denied", detail={**detail, "code": "not_owner"})
        return JSONResponse({"detail": "Das darf nur der Inhaber dieser Installation (Owner).", "code": "not_owner"},
                            status_code=status.HTTP_403_FORBIDDEN)
    if not payload.current_password:
        await _helper_audit(session, request, user, audit_action, outcome="denied",
                            detail={**detail, "code": "password_missing"})
        return JSONResponse({"detail": "Bitte zur Bestätigung das aktuelle Passwort eingeben.", "code": "password_missing"},
                            status_code=status.HTTP_403_FORBIDDEN)
    try:
        await confirm_current_password(request, session, user, payload.current_password, kind)
        await confirm_current_totp(request, session, settings, user, payload.totp_code, kind)
    except TotpMissing as exc:
        await _helper_audit(session, request, user, audit_action, outcome="denied", detail={**detail, "code": exc.code})
        return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=status.HTTP_403_FORBIDDEN)
    except HTTPException as exc:
        await _helper_audit(session, request, user, audit_action, outcome="denied",
                            detail={**detail, "code": f"confirm_{exc.status_code}"})
        await session.commit()
        raise
    return None


@router.get("/updates/helper", dependencies=[Depends(require_permission("system.read"))])
async def update_helper_status(settings: SettingsDep) -> UpdateHelperOut:
    """Zustand des Update-Helfers (ohne Anfrage ins Netz). Hält nebenbei neue Ergebnisse des Helfers im Audit-Protokoll
    fest (genau einmal je Anforderung)."""
    await update_helper_service.record_results(settings)
    return UpdateHelperOut.model_validate(await update_helper_service.status_view(settings))


@router.post("/updates/apply", status_code=status.HTTP_202_ACCEPTED, response_model=UpdateRequestedOut,
             response_model_by_alias=True)
async def update_apply(
    payload: UpdateApplyIn, request: Request, user: CurrentUser, session: SessionDep, settings: SettingsDep
):
    """Update auf `version` beim Update-Helfer anfordern (Owner, Passwort, ggf. Zwei-Faktor-Code); 202, Ergebnis später
    unter `GET /system/updates/helper`. 403 Owner, Passwort oder Code fehlt, 400 falsch, 409 Helfer nicht bereit oder
    Image/Tag unpassend, 422 Version ungültig oder nicht neuer, 429 höchstens einmal je 10 Min.; Fehler mit `code`."""
    # Nur eine gültige Version ins Protokoll, nie beliebiger Text des Aufrufers.
    detail = {"version": payload.version if updates.is_version(payload.version) else None}
    refused = await _confirm_helper_request(
        request, session, settings, user, payload, kind="update_einspielen", audit_action="system.update.request_refused",
        detail=detail,
    )
    if refused is not None:
        return refused
    try:
        result = await update_helper_service.apply(session, settings, version=payload.version)
    except update_helper_service.HelperRefused as exc:
        await _helper_audit(session, request, user, "system.update.request_refused", outcome="failure",
                            detail={**detail, "code": exc.code})
        return _helper_refused(exc)
    await _helper_audit(session, request, user, "system.update.requested", outcome="success",
                        detail={"request_id": result["request_id"], "from": result["from"], "to": result["to"]})
    return JSONResponse(UpdateRequestedOut.model_validate(result).model_dump(by_alias=True),
                        status_code=status.HTTP_202_ACCEPTED, headers=_NO_STORE)


@router.post("/updates/rollback", status_code=status.HTTP_202_ACCEPTED, response_model=UpdateRequestedOut,
             response_model_by_alias=True)
async def update_rollback(
    payload: UpdateRollbackIn, request: Request, user: CurrentUser, session: SessionDep, settings: SettingsDep
):
    """Rückweg auf die Version vor dem letzten Update anfordern (`previous` in `GET /system/updates/helper`); Antworten
    wie bei `POST /system/updates/apply`. Gehen dabei Daten mit zurück, ist `accept_data_loss: true` Pflicht (sonst
    422); 409 auch ohne Vorgängerversion oder Kopie sowie bei `marker_failed`."""
    # Die Vormerkung für den Datenrückgang wird VOR der Anforderung geschrieben und wieder gelöscht, wenn der Helfer
    # ablehnt.
    detail = {"accept_data_loss": payload.accept_data_loss}
    refused = await _confirm_helper_request(
        request, session, settings, user, payload, kind="update_zurueck", audit_action="system.update.rollback_refused",
        detail=detail,
    )
    if refused is not None:
        return refused
    try:
        result = await update_helper_service.rollback(settings, accept_data_loss=payload.accept_data_loss)
    except update_helper_service.HelperRefused as exc:
        await _helper_audit(session, request, user, "system.update.rollback_refused", outcome="failure",
                            detail={**detail, "code": exc.code})
        return _helper_refused(exc)
    await _helper_audit(
        session, request, user, "system.update.rollback_requested", outcome="success",
        detail={"request_id": result["request_id"], "from": result["from"], "to": result["to"],
                "copy": result["copy"], "data_revert": result["data_revert"]},
    )
    return JSONResponse(UpdateRequestedOut.model_validate(result).model_dump(by_alias=True),
                        status_code=status.HTTP_202_ACCEPTED, headers=_NO_STORE)


@router.get("/backups", dependencies=[Depends(require_permission("system.read"))])
async def list_backups(session: SessionDep, settings: SettingsDep) -> dict:
    return await backups_service.overview(session, settings)


@router.put("/backups/config")
async def put_backup_config(
    payload: BackupConfigIn, request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep
) -> dict:
    """Zeitplan, Anzahl, Ordner. Mit aktuellem Passwort (`current_password`, sonst 403), wenn weniger Sicherungen
    behalten werden, die automatische Sicherung ausgeschaltet oder der Ordner geändert wird; alles andere geht ohne."""
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
    # `schedule()` schreibt über eine eigene Session, deshalb vorher committen.
    await session.commit()
    await backups_service.sync_job()
    return await backups_service.overview(session, settings)


@router.put("/backups/key")
async def put_backup_key(payload: BackupKeyIn, request: Request, user: OwnerUser, session: SessionDep) -> JSONResponse:
    """Sicherungspasswort festlegen oder ändern. Die Antwort enthält EINMALIG den Wiederherstellungsschlüssel;
    gespeichert wird er nirgends."""
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
    """Baut die verschlüsselte Datei im Hintergrund. Stand über `GET .../download-jobs/{job_id}`, danach die Datei über
    `GET .../download/{ticket}` (einmalig, 5 Minuten). Verlangt Passwort und bei aktivem Zwei-Faktor den Code
    (`totp_code`, sonst 403 `totp_missing`)."""
    refused = await _confirm_with_code(request, session, settings, user, payload, "sicherung_herunterladen")
    if refused is not None:
        return refused
    await session.commit()
    try:
        ticket = await backups_service.prepare_download(settings, user_id=user.id, mode=payload.mode, password=payload.password)
    except (BackupError, ValueError) as exc:
        raise _http_error(exc) from exc
    return JSONResponse(_ticket_out(ticket), status_code=status.HTTP_202_ACCEPTED, headers=_NO_STORE)


@router.get("/backups/download-jobs/{job_id}")
async def download_job_status(job_id: str, user: OwnerUser) -> JSONResponse:
    """Stand eines Downloads über die Status-ID (taugt nicht zum Herunterladen)."""
    # Die Oberfläche fragt jede Sekunde ab; die ID steht in Adresse und Zugriffsprotokoll, daher nicht das Ticket.
    found = get_ticket_registry().get_by_job(job_id)
    if found is None or found.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter oder abgelaufener Download.")
    return JSONResponse(_ticket_out(found), headers=_NO_STORE)


@router.get("/backups/download/{ticket}/status", deprecated=True)
async def download_status(ticket: str, user: OwnerUser) -> JSONResponse:
    """Veraltet: Stand über das Ticket selbst (steht dann in der Adresse). Bleibt als Übergang; neu ist
    `GET .../download-jobs/{job_id}`."""
    found = get_ticket_registry().get(ticket)
    if found is None or found.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes oder abgelaufenes Ticket.")
    return JSONResponse(_ticket_out(found), headers=_NO_STORE)


@router.get("/backups/download/{ticket}")
async def download(ticket: str, session: SessionDep):
    """Browser-Download ohne Anmelde-Header: das Ticket ist die Berechtigung. Es gilt genau einmal, 5 Minuten, und nur,
    solange sein Nutzer noch aktiver Owner ist."""
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


@router.post("/backups/{name}/ticket")
async def stored_backup_ticket(
    name: str, payload: StoredDownloadIn, request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep
) -> JSONResponse:
    """Ticket für eine gespeicherte Sicherung. Bestätigung wie bei `POST /system/backups/download`."""
    refused = await _confirm_with_code(request, session, settings, user, payload, "sicherung_herunterladen")
    if refused is not None:
        return refused
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


def _scope(user: User, request: Request) -> restore_service.Scope:
    ip = request.client.host if request.client else None
    return restore_service.Scope("owner", user_id=user.id, label=user.username, ip=ip)


@router.get("/restore/status")
async def restore_status(
    request: Request, session: SessionDep, settings: SettingsDep,
    user: Annotated[User, Depends(require_permission("system.read"))],
) -> JSONResponse:
    """Vorgemerkte Wiederherstellung, Zwischenstand (nur für den Owner), Ergebnis des letzten Einspielens, Grenzen.
    Für alle mit `system.read`."""
    try:
        scope = _scope(user, request) if user.is_owner else None
        return JSONResponse(await restore_service.status(settings, scope), headers=_NO_STORE)
    except backups_service.NotSqlite as exc:
        raise restore_http_error(exc) from exc


@router.put("/restore/upload", status_code=status.HTTP_201_CREATED)
@max_body_bytes(lambda: get_settings().restore_max_upload_bytes)
async def restore_upload(
    request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep,
    x_confirm_password: Annotated[str | None, Header(description="Anmeldepasswort, prozentkodiert (UTF-8).")] = None,
) -> JSONResponse:
    """Lädt eine Sicherung als ROHEN Datenstrom hoch (`Content-Type: application/octet-stream`, kein Formular).
    Owner mit Passwort im Kopf `X-Confirm-Password`; beides wird geprüft, BEVOR der Body gelesen wird."""
    # Obergrenze und freier Platz stehen schon am Kopf der Anfrage fest.
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
    """Entschlüsselt und prüft die hochgeladene Sicherung (Staging) und liefert die Zusammenfassung: Datum, Version,
    Kennung der Installation, Owner-Name, Zahl der Nutzer und Server, Erweiterungen, Warnungen. Es wird noch NICHTS
    eingespielt."""
    view = await do_inspect(session, settings, _scope(user, request), restore_id, payload)
    return JSONResponse(view, headers=_NO_STORE)


class OwnerScheduleIn(PasswordConfirm, TotpConfirm, RestoreScheduleIn):
    """Vormerken: Owner mit aktuellem Passwort und, wenn Zwei-Faktor an ist, dem Code aus der App; ersetzt ALLES, auch
    die Konten."""


@router.post("/restore/{restore_id}/schedule")
async def restore_schedule(
    restore_id: str, payload: OwnerScheduleIn, request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep
) -> JSONResponse:
    """Merkt die geprüfte Sicherung zum Einspielen vor (`restore/pending.json`); eingespielt wird beim nächsten Start
    (Neustart mit `POST /system/restart`), die Vormerkung gilt eine Stunde. Passwort und bei aktivem Zwei-Faktor der
    Code (403 `totp_missing`)."""
    # Eine eingespielte Sicherung ersetzt auch die Konten; eine selbst gebaute hätte sonst ein Konto ohne Zwei-Faktor
    # zur Folge.
    refused = await _confirm_with_code(request, session, settings, user, payload, "wiederherstellen_vormerken")
    if refused is not None:
        return refused
    await session.commit()
    pending = await do_schedule(session, settings, _scope(user, request), restore_id, payload)
    return JSONResponse(pending, headers=_NO_STORE)


@router.delete("/restore/pending", status_code=status.HTTP_204_NO_CONTENT)
async def restore_cancel(request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep) -> None:
    """Verwirft die Vormerkung und jeden Zwischenstand (auch hochgeladene, noch nicht geprüfte)."""
    await do_cancel(session, settings, _scope(user, request))


@router.delete("/restore/replaced", status_code=status.HTTP_204_NO_CONTENT)
async def restore_delete_replaced(
    payload: PasswordConfirm, request: Request, user: OwnerUser, session: SessionDep, settings: SettingsDep
) -> None:
    """Löscht den alten Stand, der nach einem Einspielen unter `restore/replaced-...` liegen bleibt (er enthält die
    alten Schlüssel und Konten). Owner mit Passwort."""
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
    """Beendet Nodvard Deck mit Rückgabewert 75; der Container startet es neu (`restart: unless-stopped`) und spielt
    dabei eine vorgemerkte Wiederherstellung ein. Owner mit Passwort. Ohne Neustart-Regel bleibt er danach aus."""
    await _confirm(request, session, user, payload.current_password, "neustart")
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="system.restart.requested", outcome="success",
        target_type="system", ip=request.client.host if request.client else None,
    )
    await session.commit()
    restart_service.request_restart(request.app)
    return {"restarting": True, "exit_code": restart_service.EXIT_CODE}
