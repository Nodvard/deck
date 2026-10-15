"""Anmeldung, Token-Erneuerung und Erstinbetriebnahme (docs/04-API.md)."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from nodvard_sdk import max_body_bytes
from pydantic import BaseModel, Field, field_validator

from ...config import Settings, get_settings
from ...core import restart as restart_service
from ...core import setup_code
from ...db import utcnow
from ...services import audit as audit_service
from ...services import auth as auth_service
from ...services import restore as restore_service
from ..deps import SessionDep, SettingsDep
from ..errors import CodedHTTPException
from .restore_common import (
    RestoreInspectIn,
    RestoreScheduleIn,
    do_cancel,
    do_inspect,
    do_schedule,
    do_upload,
)
from .restore_common import http_error as restore_http_error

router = APIRouter(prefix="/auth", tags=["auth"])

# Der Refresh-Token steht in beiden Cookies (gleicher Wert, Pfad und Attribute), damit man nach einem Rollback aufs
# alte Image, das nur `lattice_refresh` kennt, angemeldet bleibt. Lesereihenfolge: neuer Name zuerst.
COOKIE_NAME = "nodvard_deck_refresh"
LEGACY_COOKIE_NAME = "lattice_refresh"
COOKIE_NAMES = (COOKIE_NAME, LEGACY_COOKIE_NAME)
COOKIE_PATH = "/api/v1/auth"

# Web bekommt den Refresh-Token nur als Cookie, Android/CLI nur im Body; ohne Angabe gilt "web" (der sichere Standard).
ClientType = Literal["web", "android", "cli"]


class BootstrapRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=255)
    setup_code: str = Field(default="", max_length=64)
    """Einrichtungscode aus dem Container-Protokoll; fehlt er, antwortet der Endpunkt mit 403 statt 422."""

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        return auth_service.validate_new_username(value)


class LoginRequest(BaseModel):
    username: str
    password: str
    client_type: ClientType = "web"


class MfaRequest(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=32)
    """Sechsstelliger Authenticator-Code oder Wiederherstellungs-Code (`ABCDE-FGHJK`, Schreibweise egal)."""


class RefreshRequest(BaseModel):
    refresh_token: str | None = None
    """Nur für Android/CLI nötig, Web schickt den Cookie automatisch mit."""


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


def _user_out(user) -> dict:  # noqa: ANN001 - User-ORM-Objekt
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "is_owner": user.is_owner,
        "locale": user.locale,
        "permissions": auth_service.user_permissions(user),
    }


def _cookie_secure(request: Request, settings: Settings) -> bool:
    """`Secure` nach dem echten Schema der Anfrage; `settings.cookie_secure` überschreibt es."""
    # Nicht nach `env` entscheiden: Browser verwerfen `Secure`-Cookies über Klartext-HTTP still, im Heimnetz mit
    # http:// ginge jedes Neuladen zurück zur Login-Seite. Hinter einem TLS-Proxy zählt dessen `X-Forwarded-Proto`,
    # wenn er unter `system.trusted_proxies` steht (api/proxy_headers.py).
    if settings.cookie_secure is not None:
        return settings.cookie_secure
    return request.url.scheme == "https"


def _tokens_response(tokens: auth_service.IssuedTokens, *, secure: bool) -> Response:
    """Die 200er-Antwort aus `IssuedTokens`; den Refresh-Token gibt es nie in Cookie und Body zugleich."""
    body: dict = {
        "access_token": tokens.access_token,
        "expires_in": tokens.access_expires_in,
        "user": _user_out(tokens.user),
    }
    if tokens.client_type != "web" and tokens.refresh_token is not None:
        body["refresh_token"] = tokens.refresh_token

    response = Response(content=json.dumps(body), media_type="application/json", status_code=200)

    # Ohne neuen Refresh-Token (Gnadenfrist beim Aktualisieren) Cookies weder setzen noch löschen,
    # sonst steht der Browser danach ohne Anmeldung da.
    if (
        tokens.client_type == "web"
        and tokens.refresh_token is not None
        and tokens.refresh_expires_at is not None
    ):
        max_age = max(1, int((tokens.refresh_expires_at - utcnow()).total_seconds()))
        for name in COOKIE_NAMES:
            response.set_cookie(
                key=name,
                value=tokens.refresh_token,
                max_age=max_age,
                path=COOKIE_PATH,
                httponly=True,
                samesite="strict",
                secure=secure,
            )
    return response


def _too_many_attempts(exc: auth_service.TooManyAttempts) -> HTTPException:
    """429 mit deutschem Hinweis; `Retry-After` nur, wenn Warten überhaupt hilft."""
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc), headers=headers
    )


def _client_ip(request: Request) -> str | None:
    """Die Gegenstelle der Verbindung, nie `X-Forwarded-For` direkt (jeder Client könnte sich damit aus der Drosselung
    herausrechnen). Hinter einem Proxy aus `system.trusted_proxies` hat `api/proxy_headers.py` `request.client` schon
    durch die echte Adresse ersetzt."""
    return request.client.host if request.client else None


def _cookie_tokens(request: Request) -> list[str]:
    """Die Werte der Refresh-Cookies in Lesereihenfolge (`COOKIE_NAMES`), ohne leere und doppelte."""
    values: list[str] = []
    for name in COOKIE_NAMES:
        value = request.cookies.get(name)
        if value and value not in values:
            values.append(value)
    return values


def _refresh_candidates(request: Request, body_token: str | None) -> list[str]:
    """Ein Token im Body (Android/CLI) gilt allein, sonst die Cookie-Werte in Lesereihenfolge."""
    tokens = [body_token] if body_token else _cookie_tokens(request)
    if not tokens:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kein Refresh-Token angegeben (weder Cookie noch Body).",
        )
    return tokens


async def confirm_current_password(
    request: Request, session, user, password: str, action: str
) -> None:
    """Gemeinsamer Weg für Aktionen, die das aktuelle Passwort verlangen: gedrosselt je Nutzer (429),
    falsch = 400 mit deutschem Hinweis, Fehlversuche im Audit-Protokoll."""
    try:
        await auth_service.require_current_password(
            session, user, password, action=action, ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except auth_service.TooManyAttempts as exc:
        raise _too_many_attempts(exc) from exc
    except auth_service.WrongPassword as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


class TotpMissing(CodedHTTPException):
    """403 mit `code: "totp_missing"`: Zwei-Faktor ist an, aber es kam kein Code. `response()` liefert dieselbe
    Antwort wie das Werfen, für Endpunkte, die Fehler selbst beantworten."""

    def __init__(self, detail: str) -> None:
        super().__init__(status.HTTP_403_FORBIDDEN, detail, "totp_missing")


class TotpRejected(CodedHTTPException):
    """400: Der Code stimmt nicht (`code: "totp_wrong"`) oder wurde schon benutzt (`"totp_used"`, nur Codes aus der
    App). Das Passwort davor stimmte, die Oberfläche leert daran nur das Code-Feld."""

    def __init__(self, detail: str, code: str) -> None:
        super().__init__(status.HTTP_400_BAD_REQUEST, detail, code)


async def confirm_current_totp(
    request: Request, session, settings: Settings, user, code: str, action: str, *, allow_recovery: bool = False,
    before_claim=None,
) -> str | None:
    """Zweiter Teil der Bestätigung bei aktivem Zwei-Faktor: ohne Code 403 (`TotpMissing`), falscher oder benutzter
    Code 400, zu viele Fehlversuche 429; `None` ohne Zwei-Faktor, sonst die Art (`totp` oder `recovery_code`)."""
    try:
        return await auth_service.require_current_totp(
            session, settings, user, code, action=action, ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"), allow_recovery=allow_recovery, before_claim=before_claim,
        )
    except auth_service.TooManyAttempts as exc:
        raise _too_many_attempts(exc) from exc
    except auth_service.TotpRequired as exc:
        raise TotpMissing(str(exc)) from exc
    except auth_service.WrongTotp as exc:
        raise TotpRejected(str(exc), exc.code) from exc


async def confirm_password_and_code(
    request: Request, session, settings: Settings, user, password: str, code: str, action: str,
) -> JSONResponse | None:
    """Passwort und, wenn Zwei-Faktor an ist, der Code aus der App; `None` heißt bestätigt. Ohne Passwort 403, ein
    fehlender Code kommt als fertige 403-Antwort (`totp_missing`) zurück, Falsches wie bei den beiden Teilen."""
    if not password:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bitte zur Bestätigung das aktuelle Passwort eingeben.")
    await confirm_current_password(request, session, user, password, action)
    try:
        await confirm_current_totp(request, session, settings, user, code, action)
    except TotpMissing as exc:
        return exc.response()
    return None


CONFIRM_REQUIRED = "confirm_required"


async def confirm_stored_secret_change(
    request: Request, session, settings: Settings, user, password: str, code: str, action: str, detail: str,
) -> JSONResponse | None:
    """Für jeden Weg, der ein schon gespeichertes Geheimnis ersetzt oder löscht (Tresor-Seite, Moduleinstellungen,
    Server-Zugang): ohne Passwort 403 `confirm_required` mit `two_factor`, damit die Oberfläche nachfragen und neu
    senden kann; sonst wie `confirm_password_and_code`. `None` heißt bestätigt."""
    if not password:
        return JSONResponse(
            {"detail": detail, "code": CONFIRM_REQUIRED, "two_factor": auth_service.two_factor_enabled(user)},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return await confirm_password_and_code(request, session, settings, user, password, code, action)


@router.get("/bootstrap")
async def bootstrap_status(session: SessionDep, settings: SettingsDep) -> dict:
    """Ob die Erstinbetriebnahme noch offen ist (`needed`), ohne Anmeldung erreichbar; ohne Konto zusätzlich `restore`
    mit dem Ergebnis einer fehlgeschlagenen Wiederherstellung aus dem Assistenten."""
    needed = await auth_service.needs_bootstrap(session)
    body: dict = {"needed": needed}
    if needed:
        result = await restore_service.failed_result(settings)
        if result is not None:
            body["restore"] = result
    return body


@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
async def bootstrap(
    payload: BootstrapRequest, request: Request, session: SessionDep, settings: SettingsDep
) -> dict:
    """Legt den ersten Nutzer als Owner an (409, sobald einer existiert) und verlangt den Einrichtungscode aus dem
    Container-Protokoll, damit nicht der Erste mit Zugriff auf die Seite Owner wird (falsch 403, zu viele Versuche 429)."""
    if not await auth_service.needs_bootstrap(session):
        # Ohne Code-Prüfung: dass es schon einen Nutzer gibt, verrät `GET /bootstrap` ohnehin.
        # Eine übrig gebliebene Code-Datei gleich mit aufräumen.
        setup_code.clear(settings.data_dir)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Es existiert bereits mindestens ein Nutzer.",
        )
    try:
        await auth_service.verify_setup_code(
            session,
            settings,
            code=payload.setup_code,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        user = await auth_service.bootstrap_owner(
            session, username=payload.username, password=payload.password
        )
    except auth_service.TooManyAttempts as exc:
        raise _too_many_attempts(exc) from exc
    except auth_service.InvalidSetupCode as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except auth_service.UsersAlreadyExist as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await audit_service.log(
        session,
        actor_type="user",
        actor_id=user.id,
        action="setup.completed",
        outcome="success",
        target_type="user",
        target_id=user.id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    # Der Code ist ab jetzt wertlos, die Datei kann weg.
    setup_code.clear(settings.data_dir)
    return {"id": user.id, "username": user.username, "is_owner": user.is_owner}


@router.post("/login")
async def login(
    payload: LoginRequest, request: Request, session: SessionDep, settings: SettingsDep
) -> Response:
    try:
        tokens = await auth_service.login(
            session,
            settings,
            username=payload.username,
            password=payload.password,
            client_type=payload.client_type,
            user_agent=request.headers.get("user-agent"),
            ip=_client_ip(request),
        )
    except auth_service.MfaRequired as exc:
        return Response(
            content=json.dumps({"mfa_required": True, "mfa_token": exc.mfa_token}),
            media_type="application/json",
            status_code=status.HTTP_202_ACCEPTED,
        )
    except auth_service.TooManyAttempts as exc:
        raise _too_many_attempts(exc) from exc
    except auth_service.InvalidCredentials as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    return _tokens_response(tokens, secure=_cookie_secure(request, settings))


@router.post("/mfa")
async def mfa(
    payload: MfaRequest, request: Request, session: SessionDep, settings: SettingsDep
) -> Response:
    try:
        tokens = await auth_service.verify_mfa(
            session,
            settings,
            mfa_token=payload.mfa_token,
            code=payload.code,
            ip=_client_ip(request),
        )
    except auth_service.TooManyAttempts as exc:
        raise _too_many_attempts(exc) from exc
    except auth_service.InvalidMfaCode as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    return _tokens_response(tokens, secure=_cookie_secure(request, settings))


@router.post("/refresh")
async def refresh(
    payload: RefreshRequest, request: Request, session: SessionDep, settings: SettingsDep
) -> Response:
    candidates = _refresh_candidates(request, payload.refresh_token)
    try:
        tokens = await auth_service.refresh_with_candidates(
            session, settings, raw_refresh_tokens=candidates
        )
    except auth_service.InvalidCredentials as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    return _tokens_response(tokens, secure=_cookie_secure(request, settings))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest, request: Request, session: SessionDep, settings: SettingsDep
) -> Response:
    # Ein Token im Body gilt allein (Android/CLI); sonst werden die Tokens aller Cookies widerrufen,
    # auch wenn sie verschieden sind (z. B. nach einem Rollback).
    for raw_token in [payload.refresh_token] if payload.refresh_token else _cookie_tokens(request):
        await auth_service.logout(session, raw_refresh_token=raw_token)

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    # Beide Cookies, mit denselben Attributen wie beim Setzen, sonst überschreibt der
    # Browser das bestehende Cookie u. U. nicht.
    for name in COOKIE_NAMES:
        response.delete_cookie(
            name, path=COOKIE_PATH, httponly=True, samesite="strict",
            secure=_cookie_secure(request, settings),
        )
    return response


# Sicherung einspielen im Assistenten, nur solange es kein Konto gibt.


async def require_setup_access(
    request: Request, session: SessionDep, settings: SettingsDep,
    x_setup_code: Annotated[str | None, Header(description="Einrichtungscode aus dem Protokoll des Containers; bei JEDEM Aufruf.")] = None,
) -> None:
    """Zugang zu den Wiederherstellen-Endpunkten des Assistenten: nur ohne Konto (409) und mit `X-Setup-Code` bei jedem
    Aufruf, gedrosselt wie `POST /auth/bootstrap` (403 falsch, 429 zu oft). Läuft als Dependency vor dem Lesen des
    Bodys, ein falscher Code kostet so nie einen Upload."""
    if not await auth_service.needs_bootstrap(session):
        setup_code.clear(settings.data_dir)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Es existiert bereits mindestens ein Nutzer.")
    try:
        await auth_service.verify_setup_code(
            session, settings, code=(x_setup_code or "")[:64], ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
        )
    except auth_service.TooManyAttempts as exc:
        raise _too_many_attempts(exc) from exc
    except auth_service.InvalidSetupCode as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


bootstrap_restore_router = APIRouter(prefix="/bootstrap", dependencies=[Depends(require_setup_access)])


def _setup_scope(request: Request) -> restore_service.Scope:
    return restore_service.Scope("bootstrap", ip=_client_ip(request))


@bootstrap_restore_router.put("/restore/upload", status_code=status.HTTP_201_CREATED)
@max_body_bytes(lambda: get_settings().restore_max_upload_bytes)
async def bootstrap_restore_upload(request: Request, session: SessionDep, settings: SettingsDep) -> JSONResponse:
    """Wie `PUT /system/restore/upload`, aber im Assistenten: Einrichtungscode statt Owner."""
    try:
        view = await do_upload(request, session, settings, _setup_scope(request))
    except restore_service.BackupError as exc:  # NotSqlite & Co. vor dem eigentlichen Upload
        raise restore_http_error(exc) from exc
    return JSONResponse(view, status_code=status.HTTP_201_CREATED, headers={"Cache-Control": "no-store"})


@bootstrap_restore_router.post("/restore/{restore_id}/inspect")
async def bootstrap_restore_inspect(
    restore_id: str, payload: RestoreInspectIn, request: Request, session: SessionDep, settings: SettingsDep
) -> JSONResponse:
    view = await do_inspect(session, settings, _setup_scope(request), restore_id, payload)
    return JSONResponse(view, headers={"Cache-Control": "no-store"})


@bootstrap_restore_router.post("/restore/{restore_id}/schedule")
async def bootstrap_restore_schedule(
    restore_id: str, payload: RestoreScheduleIn, request: Request, session: SessionDep, settings: SettingsDep
) -> JSONResponse:
    pending = await do_schedule(session, settings, _setup_scope(request), restore_id, payload)
    return JSONResponse(pending, headers={"Cache-Control": "no-store"})


@bootstrap_restore_router.delete("/restore/pending", status_code=status.HTTP_204_NO_CONTENT)
async def bootstrap_restore_cancel(request: Request, session: SessionDep, settings: SettingsDep) -> None:
    await do_cancel(session, settings, _setup_scope(request))


@bootstrap_restore_router.post("/restart", status_code=status.HTTP_202_ACCEPTED)
async def bootstrap_restart(request: Request, session: SessionDep, settings: SettingsDep) -> dict:
    """Startet Nodvard Deck neu (Rückgabewert 75), damit eine vorgemerkte Wiederherstellung eingespielt wird.
    Nur mit Einrichtungscode, nur ohne Konto und nur, wenn etwas vorgemerkt ist."""
    try:
        pending = restore_service.has_pending(settings)
    except restore_service.BackupError as exc:
        raise restore_http_error(exc) from exc
    if not pending:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Es ist keine Wiederherstellung vorgemerkt.")
    await audit_service.log(
        session, actor_type="anonymous", actor_id=_client_ip(request) or "einrichtung", action="system.restart.requested",
        outcome="success", target_type="system", detail={"source": "bootstrap"}, ip=_client_ip(request),
    )
    await session.commit()
    restart_service.request_restart(request.app)
    return {"restarting": True, "exit_code": restart_service.EXIT_CODE}


router.include_router(bootstrap_restore_router)
