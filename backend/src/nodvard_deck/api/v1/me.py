"""Selbstauskunft, Passwortwechsel und Zwei-Faktor-Verwaltung des eigenen Kontos (docs/04-API.md)."""

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
    """Zeitzone des Dashboards (`system.timezone`); jeder angemeldete Nutzer darf sie kennen."""


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]
    """Klartext, einmalig: gespeichert werden nur Hashes, kein Endpunkt liefert sie erneut."""


class RecoveryCodesRequest(BaseModel):
    current_password: str
    totp_code: str = Field(default="", max_length=64)
    """Pflicht, wenn Zwei-Faktor an ist: der aktuelle Code aus der App oder ein bisheriger Wiederherstellungs-Code.
    Fehlt er: 403 mit `code: "totp_missing"`."""


class TotpDisableRequest(BaseModel):
    current_password: str
    totp_code: str = Field(default="", max_length=64)
    """Pflicht, wenn Zwei-Faktor an ist: der aktuelle Code aus der App oder ein Wiederherstellungs-Code
    (`ABCDE-FGHJK`, wird verbraucht). Fehlt er: 403 mit `code: "totp_missing"`.
    Im Schema optional, damit ältere Aufrufer diese Antwort statt eines 422 bekommen."""


class TotpSetupRequest(BaseModel):
    current_password: str | None = None
    """Das aktuelle Passwort, fürs Einschalten verlangt (ohne gibt es 400). Im Schema optional, damit ältere Aufrufer
    ohne Body diese Antwort statt eines 422 bekommen."""


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
    totp_code: str = Field(default="", max_length=64)
    """Pflicht, wenn Zwei-Faktor an ist: der aktuelle Code aus der App oder ein Wiederherstellungs-Code
    (`ABCDE-FGHJK`, wird verbraucht). Fehlt er: 403 mit `code: "totp_missing"`.
    Im Schema optional, damit ältere Aufrufer diese Antwort statt eines 422 bekommen."""


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
    """„Erste Schritte“ im Cockpit ausgeblendet; gilt für den Benutzer auf jedem Gerät."""


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
    payload: PasswordChange, request: Request, user: CurrentUser, session: SessionDep, settings: SettingsDep,
    current_session_id: CurrentSessionId,
):
    """Eigenes Passwort ändern: aktuelles Passwort (falsch 400), bei Zwei-Faktor dazu ein Code (fehlt 403
    `totp_missing`, falsch 400, zu viele 429); gleich dem alten gibt 422, bevor ein Code verbraucht wird. Andere
    Anmeldungen werden abgemeldet, die aktuelle bleibt."""
    await confirm_current_password(request, session, user, payload.current_password, "passwort_aendern")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=422, detail="Das neue Passwort muss sich vom alten unterscheiden.")
    # Das Hashen dauert (auf einem Pi Sekunden): bei Bestätigung mit Wiederherstellungs-Code vor dem Verbrauchen rechnen,
    # danach hält die Anfrage die Schreibsperre der Datenbank.
    new_hash: str | None = None

    async def hash_new_password() -> None:
        nonlocal new_hash
        new_hash = await security.hash_password_async(payload.new_password, signed_in=True)

    # Ohne den Code käme, wer nur das alte Passwort kennt, an der Zwei-Faktor-Anmeldung vorbei und sperrte den Besitzer aus.
    try:
        via = await confirm_current_totp(
            request, session, settings, user, payload.totp_code, "passwort_aendern", allow_recovery=True,
            before_claim=hash_new_password,
        )
    except TotpMissing as exc:
        return exc.response()
    user.password_hash = new_hash or await security.hash_password_async(payload.new_password, signed_in=True)
    await session.flush()
    # Andere Anmeldungen enden, sonst holte sich z. B. ein verlorenes Handy noch 30 Tage lang neue Tokens.
    revoked = await auth_service.revoke_refresh_tokens(session, user.id, except_token_id=current_session_id)
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="auth.password_change", outcome="success",
        target_type="user", target_id=user.id,
        detail={"signed_out_sessions": revoked} | ({"via": via} if via else {}),
    )
    if via == auth_service.VIA_RECOVERY:
        await auth_service.report_recovery_use(
            session, user, action="passwort_aendern", ip=_client_ip(request), user_agent=request.headers.get("user-agent")
        )
    return None


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
    """Beginnt die Einrichtung (Schlüssel und QR-Link) und verlangt das aktuelle Passwort (`current_password`)."""
    # Sonst könnte eine gestohlene Sitzung allein einen eigenen Authenticator einschalten und den Besitzer aussperren.
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
    """Schaltet die Zwei-Faktor-Anmeldung scharf und liefert dabei einmalig die ersten zehn Wiederherstellungs-Codes."""
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
    """Neue Wiederherstellungs-Codes, die bisherigen sind sofort ungültig (409 ohne Zwei-Faktor); verlangt das
    aktuelle Passwort und einen Code, Antworten wie bei `DELETE /me/totp`."""
    # Sonst käme, wer nur das Passwort kennt, mit frischen Codes an jeder Code-Pflicht vorbei.
    if not auth_service.two_factor_enabled(user):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Die Zwei-Faktor-Anmeldung ist nicht aktiv – Wiederherstellungs-Codes gibt es nur dann.",
        )
    await confirm_current_password(request, session, user, payload.current_password, "codes_erneuern")
    # Das Hashen dauert (auf einem Pi Sekunden): bei Bestätigung mit Wiederherstellungs-Code vor dem Verbrauchen rechnen,
    # danach hält die Anfrage die Schreibsperre der Datenbank.
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
    """Zwei-Faktor abschalten mit aktuellem Passwort und einem Code (`totp_code`), Antworten wie bei
    `POST /me/password`; ohne Zwei-Faktor reicht das Passwort."""
    # Sonst könnte eine gestohlene Sitzung den zweiten Faktor entfernen und jede Code-Pflicht umgehen.
    await confirm_current_password(request, session, user, payload.current_password, "2fa_abschalten")
    try:
        via = await confirm_current_totp(
            request, session, settings, user, payload.totp_code, "2fa_abschalten", allow_recovery=True
        )
    except TotpMissing as exc:
        return exc.response()
    await auth_service.disable_totp(session, user)
    # Wie beim Passwortwechsel enden andere Anmeldungen, die aktuelle bleibt.
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
