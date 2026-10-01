"""Globale Einstellungen -- docs/04-API.md (`GET /settings`, `PUT /settings/{key}`),
docs/03-DATA-MODEL.md §9.

**Gap-Fill-Entscheidung (siehe `services.settings` fuer die volle Begruendung):** kein
bisheriges WP hat diesen Endpunkt gebaut. WP-5 brauchte einen echten Schreibweg fuer
`autonomy.mode`/`autonomy.max_risk`/`security.deny_patterns` (sonst waere "volle
Autonomie" nie ueber die echte API vorfuehrbar); WP-6 ergaenzt `maintenance.windows`
aus demselben Grund -- live gefunden, nicht vorher bedacht: `core.maintenance.
is_in_window()` liest den Schluessel bereits, aber ohne diese Ergaenzung gaebe es
keinen Weg, ein Fenster ueber die echte API zu setzen, "symmetrisch angewandt" waere
nur automatisiert, nie live zu zeigen gewesen. Bewusst weiterhin NICHT `branding.*`
(eigener Endpunkt, docs/01 §6) und NICHT `notifications.defaults`/`locale.default`
(kein Aufrufer braucht sie bisher). Ein generischer Beliebiger-Schluessel-Store waere
hier mehr, als irgendein Aufrufer aktuell verlangt.

**System (seit der Runde "Zeitzone und Aufbewahrung"):** `system.timezone` (Zeitzone aller Zeitplaene
und Wartungsfenster, Vorgabe aus `NODVARD_DECK_TIMEZONE`/`TZ`) und `audit.retention_days` (7 bis
3650 Tage, Rueckfall `NODVARD_DECK_AUDIT_RETENTION_DAYS`) sowie `jobs.run_retention_days` (1 bis 3650
Tage, Vorgabe 30: wie lange Job-Laeufe samt Protokolldateien stehen bleiben, `services.job_retention`).
`system.update_check.enabled`/`.channel` steuern "Nach Updates suchen" (`services.update_check`). Die Zeitzone hat einen Nebeneffekt
(`core.timezone.set_timezone`: Jobs umstellen, neu planen); alle landen als
`system.settings.changed` im Protokoll.

Permission: `settings.write` fuer BEIDE Richtungen -- docs/03 §1 listet nur diesen
einen Schluessel (kein separates `settings.read`), Lesen und Schreiben globaler
Einstellungen ist in dieser Runde eine einheitlich administrative Angelegenheit.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...core import timezone as timezone_service
from ...core.cron import cron_trigger
from ...services import audit as audit_service
from ...services import job_retention as job_retention_service
from ...services import reachability as reachability_service
from ...services import settings as settings_service
from ...services import update_check as update_check_service
from ..deps import CurrentUser, SessionDep, SettingsDep, require_permission

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(require_permission("settings.write"))],
)

_MANAGED_KEYS: dict[str, type] = {
    "autonomy.mode": str,
    "autonomy.max_risk": str,
    "security.deny_patterns": list,
    "maintenance.windows": list,
    "system.timezone": str,
    "audit.retention_days": int,
    "jobs.run_retention_days": int,
    "hosts.reachability.enabled": bool,
    "hosts.reachability.interval_minutes": int,
    "system.update_check.enabled": bool,
    "system.update_check.channel": str,
}
_DEFAULTS: dict[str, Any] = {
    "autonomy.mode": "propose",
    "autonomy.max_risk": "low",
    "security.deny_patterns": [],
    "maintenance.windows": [],
    "jobs.run_retention_days": job_retention_service.DEFAULT_RETENTION_DAYS,
    "hosts.reachability.enabled": reachability_service.DEFAULT_ENABLED,
    "hosts.reachability.interval_minutes": reachability_service.DEFAULT_INTERVAL_MINUTES,
    "system.update_check.enabled": update_check_service.DEFAULT_ENABLED,
    "system.update_check.channel": update_check_service.DEFAULT_CHANNEL,
}
"""Feste Vorgaben. `system.timezone` und `audit.retention_days` haben keine feste: ihre Vorgabe
kommt aus der Umgebung (`_default_for`)."""
_AUDITED_KEYS = {
    "system.timezone", "audit.retention_days", "jobs.run_retention_days", "hosts.reachability.enabled",
    "hosts.reachability.interval_minutes", "system.update_check.enabled", "system.update_check.channel",
}
"""Diese Einstellungen landen mit altem und neuem Wert im Protokoll (`system.settings.changed`)."""
_REACHABILITY_KEYS = {"hosts.reachability.enabled", "hosts.reachability.interval_minutes"}
_UPDATE_CHECK_JOB_KEYS = {"system.update_check.enabled"}
_VALID_AUTONOMY_MODES = {"propose", "full"}
_VALID_RISKS = {"low", "medium", "high", "critical"}


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


class SettingIn(BaseModel):
    value: Any


class SettingOut(BaseModel):
    key: str
    value: Any


async def _default_for(key: str, session: AsyncSession, settings: Settings) -> Any:
    """Wert, den `GET /settings` meldet, solange nichts gespeichert ist."""
    if key == "system.timezone":
        return await timezone_service.get_timezone(session)
    if key == "audit.retention_days":
        return settings.audit_retention_days
    return _DEFAULTS[key]


def _validate(key: str, value: Any) -> None:
    expected_type = _MANAGED_KEYS[key]
    # `bool` ist in Python ein `int` -- `true` ist aber keine Zahl von Tagen.
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
    return stored if stored is not None else await _default_for(key, session, settings)


@router.get("")
async def list_settings(session: SessionDep, settings: SettingsDep) -> list[SettingOut]:
    stored = await settings_service.list_global(session)
    out: list[SettingOut] = []
    for key in _MANAGED_KEYS:
        value = stored[key] if key in stored else await _default_for(key, session, settings)
        out.append(SettingOut(key=key, value=value))
    return out


@router.put("/{key}")
async def put_setting(
    key: Literal[
        "autonomy.mode", "autonomy.max_risk", "security.deny_patterns", "maintenance.windows",
        "system.timezone", "audit.retention_days", "jobs.run_retention_days", "hosts.reachability.enabled",
        "hosts.reachability.interval_minutes", "system.update_check.enabled", "system.update_check.channel",
    ],
    payload: SettingIn,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
) -> SettingOut:
    _validate(key, payload.value)
    if key in _AUDITED_KEYS:
        previous = await _current(key, session, settings)
    if key == "system.timezone":
        # Nebeneffekt: Jobs mit der alten Standardzone umstellen und neu planen.
        changed_jobs = await timezone_service.set_timezone(session, payload.value, updated_by_user_id=user.id)
    else:
        await settings_service.set_global(session, key, payload.value, updated_by_user_id=user.id)
        changed_jobs = None
        if key in _REACHABILITY_KEYS:
            # Schalter/Intervall betreffen den Kern-Job: neu planen. `schedule()` schreibt `next_run_at`
            # ueber eine EIGENE Sitzung -- vorher committen, sonst wartet sie auf unsere Schreibsperre (D-14).
            await session.commit()
            await reachability_service.sync_job()
        if key in _UPDATE_CHECK_JOB_KEYS:
            await session.commit()
            await update_check_service.sync_job()
    if key in _AUDITED_KEYS:
        detail: dict[str, Any] = {"key": key, "old": previous, "new": payload.value}
        if changed_jobs is not None:
            detail["jobs_rescheduled"] = changed_jobs
        await audit_service.log(
            session, actor_type="user", actor_id=user.id, action="system.settings.changed", outcome="success",
            target_type="setting", target_id=key, detail=detail,
        )
    return SettingOut(key=key, value=payload.value)
