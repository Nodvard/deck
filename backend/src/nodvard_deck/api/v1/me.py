"""Selbstauskunft und TOTP-Verwaltung -- docs/04-API.md §3.

`GET/PATCH /me`, `POST /me/password` und die drei TOTP-Endpunkte (Einstellungen ->
"Mein Konto"). `/me/sessions`, `/me/tokens`, `/me/devices` fehlen noch.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from ...core import security
from ...core import timezone as timezone_service
from ...services import audit as audit_service
from ...services import auth as auth_service
from ...services import settings as settings_service
from ..deps import CurrentSessionId, CurrentUser, SessionDep, SettingsDep
from .auth import (
    TotpMissing,
    _client_ip,
    _too_many_attempts,
    confirm_current_password,
    confirm_current_totp,
)

router = APIRouter(prefix="/me", tags=["me"])


class MeOut(BaseModel):
    id: str
    username: str
    display_name: str
    email: str | None
    is_owner: bool
    locale: str
    permissions: list[str]
    totp_enabled: bool
    recovery_codes_remaining: int = 0
    """Noch nicht benutzte Wiederherstellungs-Codes (0 ohne aktive Zwei-Faktor-Anmeldung)."""
    timezone: str = ""
    """Zeitzone des Dashboards (Einstellung `system.timezone`). Jeder angemeldete Nutzer darf sie
    kennen: Zeitplan-Waehler rechnen "naechster Lauf" damit, auch ohne `settings.write`."""


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]
    """Klartext, EINMALIG -- gespeichert werden nur Hashes, kein Endpunkt liefert sie erneut."""


class RecoveryCodesRequest(BaseModel):
    current_password: str
    totp_code: str = Field(default="", max_length=64)
    """Pflicht, wenn Zwei-Faktor an ist (sonst gibt es ohnehin keine Codes): der aktuelle Code aus der App oder einer
    der bisherigen Wiederherstellungs-Codes. Fehlt er: 403 mit `code: "totp_missing"`."""


class TotpDisableRequest(BaseModel):
    current_password: str
    totp_code: str = Field(default="", max_length=64)
    """Pflicht, wenn Zwei-Faktor an ist: der aktuelle Code aus der Authenticator-App (sechs Ziffern) oder ein
    Wiederherstellungs-Code (`ABCDE-FGHJK`, Schreibweise egal; wird dabei verbraucht). Fehlt er: 403 mit
    `code: "totp_missing"`. Im Schema optional, damit aeltere Aufrufer diese Antwort bekommen statt eines 422."""


class TotpSetupRequest(BaseModel):
    current_password: str | None = None
    """Das aktuelle Passwort. Fuer das Einschalten der Zwei-Faktor-Anmeldung wird es verlangt (ohne
    gibt es 400); im Schema optional, damit aeltere Aufrufer mit ihrer Anfrage ohne Body gueltig
    bleiben und eine verstaendliche Antwort bekommen statt eines 422."""


class TotpSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class TotpConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


@router.get("")
async def get_me(user: CurrentUser, session: SessionDep) -> MeOut:
    totp_enabled = auth_service.two_factor_enabled(user)
    return MeOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        is_owner=user.is_owner,
        locale=user.locale,
        permissions=auth_service.user_permissions(user),
        totp_enabled=totp_enabled,
        recovery_codes_remaining=(
            await auth_service.recovery_codes_remaining(session, user.id) if totp_enabled else 0
        ),
        timezone=await timezone_service.get_timezone(session),
    )


class MeUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    locale: str | None = Field(default=None, pattern=r"^[a-z]{2}$")


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=255)


@router.patch("")
async def update_me(payload: MeUpdate, user: CurrentUser, session: SessionDep) -> MeOut:
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip()
    if payload.email is not None:
        user.email = payload.email.strip() or None
    if payload.locale is not None:
        user.locale = payload.locale
    await session.flush()
    return await get_me(user, session)


FIRST_STEPS_KEY = "ui.first_steps_dismissed"


class PreferencesOut(BaseModel):
    first_steps_dismissed: bool = False
    """„Erste Schritte“ im Cockpit ausgeblendet. Gilt fuer den Benutzer auf jedem Geraet."""


class PreferencesUpdate(BaseModel):
    first_steps_dismissed: bool | None = None


async def _preferences(session: SessionDep, user_id: str) -> PreferencesOut:
    return PreferencesOut(first_steps_dismissed=bool(await settings_service.get_user(session, user_id, FIRST_STEPS_KEY, False)))


@router.get("/preferences")
async def get_preferences(user: CurrentUser, session: SessionDep) -> PreferencesOut:
    return await _preferences(session, user.id)


@router.patch("/preferences")
async def update_preferences(payload: PreferencesUpdate, user: CurrentUser, session: SessionDep) -> PreferencesOut:
    if payload.first_steps_dismissed is not None:
        await settings_service.set_user(session, user.id, FIRST_STEPS_KEY, payload.first_steps_dismissed)
    return await _preferences(session, user.id)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: PasswordChange, request: Request, user: CurrentUser, session: SessionDep, current_session_id: CurrentSessionId
) -> None:
    """Eigenes Passwort aendern -- nur mit dem aktuellen Passwort. Alle ANDEREN
    Anmeldungen werden dabei abgemeldet (sonst holte sich z. B. ein
    verlorenes Handy noch 30 Tage lang neue Tokens), die aktuelle bleibt."""
    await confirm_current_password(request, session, user, payload.current_password, "passwort_aendern")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=422, detail="Das neue Passwort muss sich vom alten unterscheiden.")
    user.password_hash = await security.hash_password_async(payload.new_password, signed_in=True)
    await session.flush()
    revoked = await auth_service.revoke_refresh_tokens(session, user.id, except_token_id=current_session_id)
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="auth.password_change", outcome="success",
        target_type="user", target_id=user.id, detail={"signed_out_sessions": revoked},
    )


TOTP_SETUP_PASSWORD_REQUIRED = (
    "Zum Einschalten der Zwei-Faktor-Anmeldung brauchst du dein aktuelles Passwort."
)


@router.post("/totp/setup", response_model=TotpSetupOut)
async def totp_setup(
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    payload: TotpSetupRequest | None = None,
) -> TotpSetupOut:
    """Beginnt die Einrichtung (Schluessel + QR-Link). Verlangt das aktuelle Passwort (Body
    `{current_password}`): sonst koennte eine gestohlene Sitzung allein die Zwei-Faktor-Anmeldung
    mit einem eigenen Authenticator einschalten und den Besitzer aussperren -- Abschalten und neue
    Wiederherstellungs-Codes verlangen es aus demselben Grund."""
    if payload is None or not payload.current_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=TOTP_SETUP_PASSWORD_REQUIRED)
    await confirm_current_password(request, session, user, payload.current_password, "2fa_einrichten")
    try:
        secret, otpauth_uri = await auth_service.start_totp_setup(session, settings, user)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return TotpSetupOut(secret=secret, otpauth_uri=otpauth_uri)


@router.post("/totp/confirm")
async def totp_confirm(
    payload: TotpConfirmRequest, request: Request, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> RecoveryCodesOut:
    """Schaltet die Zwei-Faktor-Anmeldung scharf und liefert dabei EINMALIG die ersten
    zehn Wiederherstellungs-Codes."""
    try:
        codes = await auth_service.confirm_totp_setup(
            session, settings, user, code=payload.code, ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except auth_service.TooManyAttempts as exc:
        raise _too_many_attempts(exc) from exc
    except auth_service.InvalidMfaCode as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="auth.recovery_codes_generated",
        outcome="success", target_type="user", target_id=user.id, detail={"reason": "2fa_enabled"},
    )
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/recovery-codes", response_model=RecoveryCodesOut)
async def regenerate_recovery_codes(
    payload: RecoveryCodesRequest, request: Request, user: CurrentUser, session: SessionDep, settings: SettingsDep
):
    """Neue Wiederherstellungs-Codes; die bisherigen (auch unbenutzte) sind sofort ungueltig.
    Verlangt das aktuelle Passwort -- eine gestohlene Sitzung allein soll sich keinen
    frischen Satz holen koennen -- und dazu einen Code (`totp_code`) wie beim Abschalten: den aktuellen aus der App
    oder einen der bisherigen Wiederherstellungs-Codes. Sonst holte sich, wer nur das Passwort kennt, frische Codes
    und kaeme damit an jeder Code-Pflicht vorbei (Anmelden, Abschalten, Update). Antworten wie bei `DELETE /me/totp`."""
    if not auth_service.two_factor_enabled(user):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Die Zwei-Faktor-Anmeldung ist nicht aktiv – Wiederherstellungs-Codes gibt es nur dann.",
        )
    await confirm_current_password(request, session, user, payload.current_password, "codes_erneuern")
    # Die Hashes der neuen Codes dauern (auf einem Pi Sekunden). Mit einem Wiederherstellungs-Code bestaetigt, rechnen
    # sie, bevor er verbraucht wird: ab da haelt die Anfrage die Schreibsperre der Datenbank.
    fresh = auth_service.new_recovery_code_set()
    try:
        via = await confirm_current_totp(
            request, session, settings, user, payload.totp_code, "codes_erneuern", allow_recovery=True,
            before_claim=fresh.hash,
        )
    except TotpMissing as exc:
        return exc.response()
    codes = await auth_service.issue_recovery_codes(session, user, fresh)
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="auth.recovery_codes_generated",
        outcome="success", target_type="user", target_id=user.id,
        detail={"reason": "renewed"} | ({"via": via} if via else {}),
    )
    if via == auth_service.VIA_RECOVERY:
        await auth_service.report_recovery_use(
            session, user, action="codes_erneuern", ip=_client_ip(request), user_agent=request.headers.get("user-agent")
        )
    return RecoveryCodesOut(recovery_codes=codes)


@router.delete("/totp", status_code=status.HTTP_204_NO_CONTENT)
async def totp_disable(
    payload: TotpDisableRequest, request: Request, user: CurrentUser, session: SessionDep, settings: SettingsDep,
    current_session_id: CurrentSessionId,
):
    """Zwei-Faktor abschalten -- verlangt das aktuelle Passwort (Body `{current_password}`), damit eine gestohlene
    Sitzung allein den zweiten Faktor nicht entfernen kann, und dazu einen Code (`totp_code`): den aktuellen aus der
    App oder einen Wiederherstellungs-Code. Sonst kaeme, wer nur das Passwort kennt, durch Abschalten an jeder
    Code-Pflicht vorbei (z. B. beim Update). Reihenfolge: erst das Passwort (falsch: 400, der Code bleibt
    unangetastet), dann der Code (fehlt: 403 `totp_missing`, zaehlt nicht; falsch oder schon benutzt: 400 und
    zaehlt wie beim Anmelden; zu viele: 429). Ist Zwei-Faktor aus, reicht das Passwort wie bisher."""
    await confirm_current_password(request, session, user, payload.current_password, "2fa_abschalten")
    try:
        via = await confirm_current_totp(
            request, session, settings, user, payload.totp_code, "2fa_abschalten", allow_recovery=True
        )
    except TotpMissing as exc:
        return exc.response()
    await auth_service.disable_totp(session, user)
    # Wie beim Passwortwechsel: andere Anmeldungen enden, die aktuelle bleibt.
    revoked = await auth_service.revoke_refresh_tokens(session, user.id, except_token_id=current_session_id)
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="auth.2fa_disabled",
        outcome="success", target_type="user", target_id=user.id,
        detail={"signed_out_sessions": revoked} | ({"via": via} if via else {}),
    )
    if via == auth_service.VIA_RECOVERY:
        await auth_service.report_recovery_use(
            session, user, action="2fa_abschalten", ip=_client_ip(request), user_agent=request.headers.get("user-agent")
        )
    return None
