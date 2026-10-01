"""Gemeinsame Bausteine der Wiederherstellen-Endpunkte: unter `/system/restore/*` (Owner mit
Passwort) und unter `/auth/bootstrap/restore/*` (Assistent, nur ohne Konto, mit Einrichtungscode).

Beide Wege laufen durch dieselben Funktionen; sie unterscheiden sich nur im `Scope` und darin,
WER vorher geprueft wurde. Diese Pruefung steht in einer Dependency und laeuft IMMER VOR dem
Lesen des Bodys -- FastAPI liest einen Body nur, wenn der Endpunkt einen deklariert, und der
Upload liest ihn erst ueber `request.stream()` im Handler.

Passwort im Kopf: `X-Confirm-Password` traegt das Anmeldepasswort PROZENTKODIERT (UTF-8,
`encodeURIComponent`). HTTP-Kopfzeilen kennen kein Unicode; ein Passwort mit "ä" oder einem
Emoji liesse sich sonst gar nicht senden. Reines ASCII ohne `%` geht unveraendert.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import unquote

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator
from starlette.requests import ClientDisconnect

from ...config import Settings
from ...core.backup import errors as backup_errors
from ...core.backup.errors import BackupError
from ...services import backups as backups_service
from ...services import restore as restore_service
from ...services.restore import Scope

OCTET_STREAM = "application/octet-stream"


class RestoreInspectIn(BaseModel):
    """Genau eines von beiden: das Sicherungspasswort bzw. Einmal-Passwort der Datei, oder der
    Wiederherstellungsschluessel (`AGE-SECRET-KEY-1...`)."""

    password: str | None = Field(default=None, max_length=1024)
    recovery_key: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _exactly_one(self) -> RestoreInspectIn:
        if bool(self.password) == bool(self.recovery_key):
            raise ValueError("Bitte entweder das Passwort oder den Wiederherstellungsschlüssel angeben.")
        return self

    @property
    def secret(self) -> str:
        if self.recovery_key:
            return self.recovery_key.strip()
        return self.password or ""


class RestoreScheduleIn(BaseModel):
    sign_out_all: bool = True
    """Alle Anmeldungen beenden (Standard). Sonst wuerden Sitzungen aus der Zeit der Sicherung wieder gelten."""


def http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, restore_service.UnknownRestore):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, (restore_service.RestoreBusy, restore_service.PendingExists, restore_service.WrongState,
                        backups_service.BackupBusy, backup_errors.NotSqlite)):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, backup_errors.WrongSecret):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    if isinstance(exc, backup_errors.BackupTooLarge):
        return HTTPException(413, str(exc))
    if isinstance(exc, backup_errors.NotEnoughSpace):
        return HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE, str(exc))
    if isinstance(exc, (BackupError, ValueError)):
        return HTTPException(422, str(exc))
    raise exc


def decode_header_password(value: str | None) -> str:
    """`X-Confirm-Password` -> Passwort (prozentkodiert, UTF-8). Leer, wenn der Kopf fehlt."""
    if not value:
        return ""
    try:
        return unquote(value, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Das Passwort im Kopf X-Confirm-Password ist kein gültiges UTF-8 (prozentkodiert).") from exc


def upload_headers(request: Request) -> int | None:
    """Prueft Content-Type und liest Content-Length -- alles aus dem Kopf, vor dem Body."""
    media = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media != OCTET_STREAM:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Bitte die Datei als {OCTET_STREAM} senden (roher Datenstrom, kein Formular).")
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        length = int(raw)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültige Content-Length.") from exc
    if length < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültige Content-Length.")
    return length


async def _stream(request: Request) -> AsyncIterator[bytes]:
    async for chunk in request.stream():
        yield chunk


async def do_upload(request: Request, session: Any, settings: Settings, scope: Scope) -> dict:
    length = upload_headers(request)
    try:
        view = await restore_service.receive_upload(settings, scope, _stream(request), length)
    except ClientDisconnect:
        raise  # die Gegenstelle ist weg; es gibt niemanden, der eine Antwort bekaeme
    except BackupError as exc:
        await restore_service.audit(session, scope, "system.restore.upload_failed", outcome="failure", reason=str(exc))
        await session.commit()
        raise http_error(exc) from exc
    await restore_service.audit(
        session, scope, "system.restore.uploaded", target_id=view["id"],
        detail={"size": view.get("size"), "mode": (view.get("header") or {}).get("mode")},
    )
    return view


async def do_inspect(session: Any, settings: Settings, scope: Scope, rid: str, body: RestoreInspectIn) -> dict:
    instance_id, has_accounts = await restore_service.context_for_summary(session)
    try:
        view = await restore_service.inspect(
            settings, scope, rid, secret=body.secret, instance_id=instance_id, has_accounts=has_accounts,
        )
    except (BackupError, ValueError) as exc:
        # Nie das Passwort oder den Schluessel ins Protokoll -- nur, dass es nicht ging und warum.
        await restore_service.audit(session, scope, "system.restore.inspect_failed", outcome="failure", reason=str(exc), target_id=rid)
        await session.commit()
        raise http_error(exc) from exc
    summary = view.get("summary") or {}
    await restore_service.audit(
        session, scope, "system.restore.inspected", target_id=rid,
        detail={"created_at": summary.get("created_at"), "app_version": summary.get("app_version"),
                "instance_id": summary.get("instance_id"), "owner_name": summary.get("owner_name")},
    )
    return view


async def do_schedule(session: Any, settings: Settings, scope: Scope, rid: str, body: RestoreScheduleIn) -> dict:
    try:
        pending = await restore_service.schedule(settings, scope, rid, sign_out_all=body.sign_out_all)
    except (BackupError, ValueError) as exc:
        await restore_service.audit(session, scope, "system.restore.schedule_failed", outcome="failure", reason=str(exc), target_id=rid)
        await session.commit()
        raise http_error(exc) from exc
    await restore_service.audit(
        session, scope, "system.restore.scheduled", target_id=rid,
        detail={"sign_out_all": body.sign_out_all, "backup": pending.get("backup")},
    )
    return pending


async def do_cancel(session: Any, settings: Settings, scope: Scope) -> None:
    try:
        count = await restore_service.cancel(settings)
    except (BackupError, ValueError) as exc:
        raise http_error(exc) from exc
    await restore_service.audit(session, scope, "system.restore.cancelled", detail={"discarded": count})
