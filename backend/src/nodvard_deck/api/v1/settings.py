"""Globale Einstellungen (`GET /settings`, `PUT /settings/{key}`, `PUT /settings/autonomy`; docs/04-API.md,
docs/03-DATA-MODEL.md)."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...core import timezone as timezone_service
from ...core import trusted_proxies as trusted_proxies_service
from ...core.cron import cron_trigger
from ...models import User
from ...services import audit as audit_service
from ...services import job_retention as job_retention_service
from ...services import notification_retention as notification_retention_service
from ...services import notification_types
from ...services import reachability as reachability_service
from ...services import settings as settings_service
from ...services import update_check as update_check_service
from ..deps import CurrentUser, SessionDep, SettingsDep, require_permission
from ..models import RequestModel
from ..responses import error_responses
from ..errors import CodedHTTPException
from .auth import TotpMissing, confirm_current_password, confirm_current_totp

# `settings.write` gilt für Lesen und Schreiben: docs/03-DATA-MODEL.md kennt kein eigenes `settings.read`.
router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(require_permission("settings.write"))],
)

# Nur diese Schlüssel, kein generischer Store; `branding.*` hat einen eigenen Endpunkt.
_MANAGED_KEYS: dict[str, type] = {
    "autonomy.mode": str,
    "autonomy.max_risk": str,
    "security.deny_patterns": list,
    "maintenance.windows": list,
    "system.timezone": str,
    "audit.retention_days": int,
    "jobs.run_retention_days": int,
    "notifications.retention_days": int,
    notification_types.SETTING_KEY: dict,
    "hosts.reachability.enabled": bool,
    "hosts.reachability.interval_minutes": int,
    "system.update_check.enabled": bool,
    "system.update_check.channel": str,
    "system.update_check.auto_security": bool,
    "ssh.confirm_new_host_keys": bool,
    "system.trusted_proxies": list,
}
_DEFAULTS: dict[str, Any] = {
    "autonomy.mode": "propose",
    "autonomy.max_risk": "low",
    "security.deny_patterns": [],
    "maintenance.windows": [],
    "jobs.run_retention_days": job_retention_service.DEFAULT_RETENTION_DAYS,
    "notifications.retention_days": notification_retention_service.DEFAULT_RETENTION_DAYS,
    "hosts.reachability.enabled": reachability_service.DEFAULT_ENABLED,
    "hosts.reachability.interval_minutes": reachability_service.DEFAULT_INTERVAL_MINUTES,
    "system.update_check.enabled": update_check_service.DEFAULT_ENABLED,
    "system.update_check.channel": update_check_service.DEFAULT_CHANNEL,
    "system.update_check.auto_security": update_check_service.DEFAULT_AUTO_SECURITY,
    "ssh.confirm_new_host_keys": True,
    "system.trusted_proxies": [],
}
"""Feste Vorgaben; Zeitzone, Audit-Aufbewahrung und Push-Katalog kommen aus `_default_for`."""
_AUDITED_KEYS = {
    notification_types.SETTING_KEY,
    "autonomy.mode", "autonomy.max_risk", "security.deny_patterns", "maintenance.windows",
    "system.timezone", "audit.retention_days", "jobs.run_retention_days", "notifications.retention_days",
    "hosts.reachability.enabled", "hosts.reachability.interval_minutes", "system.update_check.enabled",
    "system.update_check.channel", "system.update_check.auto_security", "ssh.confirm_new_host_keys",
    "system.trusted_proxies",
}
"""Diese Einstellungen landen mit altem und neuem Wert im Protokoll (`system.settings.changed`).
Dazu gehören die vier, die das Freigabe-Gate steuern: wer die Autonomie lockert oder Sperrmuster
und Wartungsfenster ändert, hinterlässt eine Spur."""
_AUDIT_MAX_JSON_CHARS = 8000
"""Länger darf eine Liste im Protokoll nicht werden (JSON-Zeichen)."""
_OWNER_CONFIRMED_KEYS = {
    "autonomy.mode", "autonomy.max_risk", "ssh.confirm_new_host_keys", "system.update_check.auto_security",
}
"""Was ohne Rückfrage läuft, ob Deck neuen Server-Schlüsseln vertraut und ob es Sicherheitsupdates selbst einspielt:
nur der Inhaber, mit Passwort und, wenn Zwei-Faktor an ist, dem Code aus der App. Eine gestohlene Sitzung reicht dafür
nicht, die eines Admins auch mit Passwort nicht."""
OWNER_PASSWORD_REQUIRED = "Zur Bestätigung brauchst du dein Passwort."
OWNER_PASSWORD_MISSING_CODE = "password_missing"
"""`code` der 403-Antwort ohne `current_password` (wie bei `PUT /users/{id}`): die Oberfläche zeigt daran das Passwortfeld."""
_CONFIRM_ACTIONS = {
    "ssh.confirm_new_host_keys": "server_schluessel_regel_aendern",
    "system.update_check.auto_security": "sicherheitsupdates_selbst_einspielen",
}
_REACHABILITY_KEYS = {"hosts.reachability.enabled", "hosts.reachability.interval_minutes"}
_UPDATE_CHECK_JOB_KEYS = {"system.update_check.enabled"}
_VALID_AUTONOMY_MODES = {"propose", "full"}
_VALID_RISKS = {"low", "medium", "high", "critical"}


def _audit_value(value: Any) -> Any:
    """Der Wert fürs Protokoll; eine sehr lange Liste (Sperrmuster und Wartungsfenster haben keine Obergrenze) steht
    nur mit ihrer Länge da (`{"truncated": true, "count": n}`), damit eine Zeile nicht beliebig wächst."""
    if isinstance(value, list) and len(json.dumps(value, ensure_ascii=False)) > _AUDIT_MAX_JSON_CHARS:
        return {"truncated": True, "count": len(value)}
    return value


def _validate_maintenance_windows(value: list) -> None:
    for entry in value:
        if not isinstance(entry, dict):
            raise HTTPException(status_code=422, detail="maintenance.windows: jeder Eintrag muss ein Objekt sein.")
        cron = entry.get("cron")
        duration = entry.get("duration_minutes")
        host_ids = entry.get("host_ids", "all")
        if not isinstance(cron, str) or not cron.strip():
            raise HTTPException(status_code=422, detail="maintenance.windows: 'cron' fehlt oder ist kein String.")
        if not isinstance(duration, (int, float)) or duration <= 0:
            raise HTTPException(
                status_code=422, detail="maintenance.windows: 'duration_minutes' muss eine Zahl > 0 sein."
            )
        if host_ids != "all" and not (
            isinstance(host_ids, list) and all(isinstance(h, str) for h in host_ids)
        ):
            raise HTTPException(
                status_code=422, detail="maintenance.windows: 'host_ids' muss 'all' oder eine Liste von Strings sein."
            )
        try:
            cron_trigger(cron)
        except Exception as exc:  # noqa: BLE001 - Nutzereingabe, keine 500er
            raise HTTPException(status_code=422, detail=f"maintenance.windows: ungültiger Cron-Ausdruck: {exc}") from exc


class SettingIn(RequestModel):
    value: Any
    current_password: str = Field(default="", max_length=1024)
    """Nur für `autonomy.mode`, `autonomy.max_risk`, `ssh.confirm_new_host_keys` und
    `system.update_check.auto_security`: das Passwort des Inhabers."""
    totp_code: str = Field(default="", max_length=64)
    """Dazu der Code aus der App, wenn der Inhaber Zwei-Faktor an hat (sonst 403 mit `code: "totp_missing"`)."""


class AutonomyIn(RequestModel):
    """Modus und Risikostufe der Selbstständigkeit in einem Schritt, mit einer Bestätigung (ein Code gilt nur einmal)."""

    mode: str = Field(max_length=32)
    max_risk: str = Field(max_length=32)
    current_password: str = Field(default="", max_length=1024)
    totp_code: str = Field(default="", max_length=64)


class AutonomyOut(BaseModel):
    mode: str
    max_risk: str


class SettingOut(BaseModel):
    key: str
    value: Any


async def _default_for(key: str, session: AsyncSession, settings: Settings) -> Any:
    """Wert, den `GET /settings` meldet, solange nichts gespeichert ist."""
    if key == "system.timezone":
        return await timezone_service.get_timezone(session)
    if key == "audit.retention_days":
        return settings.audit_retention_days
    if key == notification_types.SETTING_KEY:
        return notification_types.defaults(notification_types.catalog(settings.extensions_dir))
    return _DEFAULTS[key]


def _validate(key: str, value: Any) -> None:
    expected_type = _MANAGED_KEYS[key]
    # `bool` ist in Python ein `int`, `true` ist aber keine Zahl von Tagen.
    if not isinstance(value, expected_type) or (expected_type is int and isinstance(value, bool)):
        raise HTTPException(
            status_code=422,
            detail=f"'{key}' erwartet {expected_type.__name__}, bekam {type(value).__name__}.",
        )
    if key == "autonomy.mode" and value not in _VALID_AUTONOMY_MODES:
        raise HTTPException(status_code=422, detail=f"autonomy.mode muss eines von {sorted(_VALID_AUTONOMY_MODES)} sein.")
    if key == "autonomy.max_risk" and value not in _VALID_RISKS:
        raise HTTPException(status_code=422, detail=f"autonomy.max_risk muss eines von {sorted(_VALID_RISKS)} sein.")
    if key == "security.deny_patterns" and not all(isinstance(p, str) for p in value):
        raise HTTPException(status_code=422, detail="security.deny_patterns muss eine Liste von Strings sein.")
    if key == "maintenance.windows":
        _validate_maintenance_windows(value)
    if key == "system.timezone" and not timezone_service.is_valid_timezone(value):
        raise HTTPException(
            status_code=422, detail=f"system.timezone: unbekannte Zeitzone '{value}' (erwartet z. B. 'Europe/Berlin')."
        )
    if key == "audit.retention_days" and not (
        audit_service.RETENTION_MIN_DAYS <= value <= audit_service.RETENTION_MAX_DAYS
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"audit.retention_days muss zwischen {audit_service.RETENTION_MIN_DAYS} "
                f"und {audit_service.RETENTION_MAX_DAYS} Tagen liegen."
            ),
        )
    if key == "jobs.run_retention_days" and not (
        job_retention_service.RETENTION_MIN_DAYS <= value <= job_retention_service.RETENTION_MAX_DAYS
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"jobs.run_retention_days muss zwischen {job_retention_service.RETENTION_MIN_DAYS} "
                f"und {job_retention_service.RETENTION_MAX_DAYS} Tagen liegen."
            ),
        )
    if key == "notifications.retention_days" and not (
        notification_retention_service.RETENTION_MIN_DAYS <= value <= notification_retention_service.RETENTION_MAX_DAYS
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"notifications.retention_days muss zwischen {notification_retention_service.RETENTION_MIN_DAYS} "
                f"und {notification_retention_service.RETENTION_MAX_DAYS} Tagen liegen."
            ),
        )
    if key == "system.update_check.channel" and value not in update_check_service.CHANNELS:
        raise HTTPException(
            status_code=422,
            detail=f"system.update_check.channel muss eines von {list(update_check_service.CHANNELS)} sein.",
        )
    if key == "hosts.reachability.interval_minutes" and not (
        reachability_service.MIN_INTERVAL_MINUTES <= value <= reachability_service.MAX_INTERVAL_MINUTES
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"hosts.reachability.interval_minutes muss zwischen {reachability_service.MIN_INTERVAL_MINUTES} "
                f"und {reachability_service.MAX_INTERVAL_MINUTES} Minuten liegen."
            ),
        )


async def _current(key: str, session: AsyncSession, settings: Settings) -> Any:
    stored = await settings_service.get_global(session, key)
    if key == notification_types.SETTING_KEY:
        return notification_types.with_defaults(stored, notification_types.catalog(settings.extensions_dir))
    return stored if stored is not None else await _default_for(key, session, settings)


@router.get("", responses=error_responses(401, 403, 507))
async def list_settings(session: SessionDep, settings: SettingsDep) -> list[SettingOut]:
    stored = await settings_service.list_global(session)
    out: list[SettingOut] = []
    for key in _MANAGED_KEYS:
        if key == "ssh.confirm_new_host_keys" and settings.ssh_confirm_new_host_keys is not None:
            value = settings.ssh_confirm_new_host_keys  # Umgebungsvariable übersteuert
        elif key == trusted_proxies_service.SETTING_KEY and settings.trusted_proxies is not None:
            value = trusted_proxies_service.env_entries(settings, warn=False)  # Umgebungsvariable übersteuert
        else:
            value = stored[key] if key in stored else await _default_for(key, session, settings)
        if key == notification_types.SETTING_KEY:
            value = notification_types.with_defaults(value, notification_types.catalog(settings.extensions_dir))
        out.append(SettingOut(key=key, value=value))
    return out


@router.get("/notification-types", responses=error_responses(401, 403, 507))
async def list_notification_types(settings: SettingsDep) -> list[notification_types.NotificationTypeOut]:
    return [notification_types.NotificationTypeOut(id=key, label=label)
            for key, label in notification_types.catalog(settings.extensions_dir).items()]


_OWNER_ONLY: dict[str, tuple[str, str]] = {
    # Wer hier einträgt, entscheidet, wessen Angaben zur Absender-Adresse gelten (Sperre, Protokoll, HTTPS).
    trusted_proxies_service.SETTING_KEY: (
        "Nur der Inhaber darf die Proxy-Liste ändern.",
        "Die Liste der vertrauenswürdigen Proxys ändert nur der Inhaber (Owner).",
    ),
    "autonomy": (
        "Nur der Inhaber darf die Selbstständigkeit ändern.",
        "Die Selbstständigkeit ändert nur der Inhaber (Owner).",
    ),
    "ssh.confirm_new_host_keys": (
        "Nur der Inhaber darf die Regel für neue Server-Schlüssel ändern.",
        "Ob neue Server-Schlüssel erst bestätigt werden müssen, legt nur der Inhaber (Owner) fest.",
    ),
    "system.update_check.auto_security": (
        "Nur der Inhaber darf festlegen, ob Sicherheitsupdates selbst eingespielt werden.",
        "Ob Nodvard Deck Sicherheitsupdates selbst einspielt, legt nur der Inhaber (Owner) fest.",
    ),
}
"""Einstellungen nur für den Inhaber, nicht für jeden Admin: Grund fürs Protokoll und Antwort (403). `autonomy` steht für
`autonomy.mode` und `autonomy.max_risk`."""


def _owner_only_entry(key: str) -> str | None:
    return "autonomy" if key in ("autonomy.mode", "autonomy.max_risk", "autonomy") else (key if key in _OWNER_ONLY else None)


async def _refuse_unless_owner(session: AsyncSession, user: User, key: str) -> None:
    """403 für alle außer dem Inhaber, mit Eintrag im Protokoll. Vor jeder Prüfung des Werts, damit es immer 403 ist."""
    entry = _owner_only_entry(key)
    if entry is None or user.is_owner:
        return
    reason, detail = _OWNER_ONLY[entry]
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="system.settings.changed", outcome="denied",
        target_type="setting", target_id=key, reason=reason,
    )
    await session.commit()
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


async def _confirm_owner(
    request: Request, session: AsyncSession, settings: Settings, user: User, password: str, code: str, action: str
) -> JSONResponse | None:
    """Passwort und, wenn Zwei-Faktor an ist, der Code aus der App (nur der App-Code, kein Wiederherstellungs-Code).
    Fehlt das Passwort 403 `password_missing`, falsch 400, zu oft 429; fehlt der Code 403 `totp_missing` als fertige
    Antwort. `None` heißt bestätigt. Erst aufrufen, wenn der Wert geprüft ist, sonst kostet eine Ablehnung einen Code."""
    if not password:
        raise CodedHTTPException(status.HTTP_403_FORBIDDEN, OWNER_PASSWORD_REQUIRED, OWNER_PASSWORD_MISSING_CODE)
    await confirm_current_password(request, session, user, password, action)
    try:
        await confirm_current_totp(request, session, settings, user, code, action)
    except TotpMissing as exc:
        return exc.response()
    return None


async def _log_change(session: AsyncSession, user: User, key: str, previous: Any, value: Any, **extra: Any) -> None:
    detail: dict[str, Any] = {"key": key, "old": _audit_value(previous), "new": _audit_value(value), **extra}
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="system.settings.changed", outcome="success",
        target_type="setting", target_id=key, detail=detail,
    )


@router.put("/autonomy", responses=error_responses(400, 401, 403, 422, 429, 503, 507))
async def put_autonomy(
    payload: AutonomyIn, request: Request, session: SessionDep, settings: SettingsDep, user: CurrentUser
) -> AutonomyOut:
    """Modus und Risikostufe der Selbstständigkeit zusammen: eine Bestätigung, beide Werte in einem Schritt gespeichert,
    kein Zwischenstand. Nur der Inhaber, mit Passwort und, wenn Zwei-Faktor an ist, dem Code aus der App. Im Protokoll
    steht jede geänderte Einstellung einzeln (`system.settings.changed`)."""
    await _refuse_unless_owner(session, user, "autonomy")
    _validate("autonomy.mode", payload.mode)
    _validate("autonomy.max_risk", payload.max_risk)
    refused = await _confirm_owner(
        request, session, settings, user, payload.current_password, payload.totp_code, "selbststaendigkeit_aendern"
    )
    if refused is not None:
        return refused
    for key, value in (("autonomy.mode", payload.mode), ("autonomy.max_risk", payload.max_risk)):
        previous = await _current(key, session, settings)
        if previous == value:
            continue
        await settings_service.set_global(session, key, value, updated_by_user_id=user.id)
        await _log_change(session, user, key, previous, value)
    return AutonomyOut(mode=payload.mode, max_risk=payload.max_risk)


@router.put("/{key}", responses=error_responses(400, 401, 403, 409, 422, 429, 503, 507))
async def put_setting(
    key: Literal[
        "autonomy.mode", "autonomy.max_risk", "security.deny_patterns", "maintenance.windows",
        "system.timezone", "audit.retention_days", "jobs.run_retention_days", "notifications.retention_days",
        "hosts.reachability.enabled", "hosts.reachability.interval_minutes", "system.update_check.enabled",
        "system.update_check.channel", "system.update_check.auto_security", "ssh.confirm_new_host_keys",
        "system.trusted_proxies", "notifications.push_types",
    ],
    payload: SettingIn,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
) -> SettingOut:
    await _refuse_unless_owner(session, user, key)
    _validate(key, payload.value)
    value = payload.value
    if key == notification_types.SETTING_KEY:
        types = notification_types.catalog(settings.extensions_dir)
        normalized = {}
        for kind, preference in value.items():
            if kind not in types:
                raise HTTPException(status_code=422, detail=f"Unbekannte Meldungsart: {kind}.")
            try:
                normalized[kind] = notification_types.PushPreference.model_validate(preference).model_dump()
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=f"Ungültige Push-Einstellung für {kind}.") from exc
        value = notification_types.with_defaults(normalized, types)
    if key == trusted_proxies_service.SETTING_KEY:
        try:
            value = trusted_proxies_service.normalize(value)
        except trusted_proxies_service.InvalidEntry as exc:
            raise HTTPException(status_code=422, detail=f"system.trusted_proxies: {exc}") from exc
        if settings.trusted_proxies is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Das ist über die Umgebungsvariable {trusted_proxies_service.ENV_NAME} festgelegt "
                    "und lässt sich hier nicht ändern."
                ),
            )
    if key == "ssh.confirm_new_host_keys" and settings.ssh_confirm_new_host_keys is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Das ist über die Umgebungsvariable NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS festgelegt "
                "und lässt sich hier nicht ändern."
            ),
        )
    if key in _OWNER_CONFIRMED_KEYS:
        action = "selbststaendigkeit_aendern" if key.startswith("autonomy.") else _CONFIRM_ACTIONS[key]
        refused = await _confirm_owner(
            request, session, settings, user, payload.current_password, payload.totp_code, action
        )
        if refused is not None:
            return refused
    if key in _AUDITED_KEYS:
        previous = await _current(key, session, settings)
    if key == "system.timezone":
        # Nebeneffekt: Jobs mit der alten Standardzone umstellen und neu planen.
        changed_jobs = await timezone_service.set_timezone(session, payload.value, updated_by_user_id=user.id)
    else:
        await settings_service.set_global(session, key, value, updated_by_user_id=user.id)
        changed_jobs = None
        if key in _REACHABILITY_KEYS:
            # Schalter und Intervall betreffen den Kern-Job: neu planen. `schedule()` schreibt `next_run_at` über eine
            # eigene Sitzung, deshalb vorher committen, sonst wartet sie auf unsere Schreibsperre.
            await session.commit()
            await reachability_service.sync_job()
        if key in _UPDATE_CHECK_JOB_KEYS:
            await session.commit()
            await update_check_service.sync_job()
    if key in _AUDITED_KEYS:
        extra = {"jobs_rescheduled": changed_jobs} if changed_jobs is not None else {}
        await _log_change(session, user, key, previous, value, **extra)
    if key == trusted_proxies_service.SETTING_KEY:
        # Erst gespeichert, dann gültig: ohne Neustart, ab der nächsten Anfrage.
        await session.commit()
        trusted_proxies_service.apply(value)
    return SettingOut(key=key, value=value)
