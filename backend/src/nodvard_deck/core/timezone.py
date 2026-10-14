"""Zeitzone des Dashboards (Einstellung `system.timezone`) für Zeitpläne und Wartungsfenster.
Vorgabe ohne Einstellung: `config.default_timezone()` (`NODVARD_DECK_TIMEZONE`, sonst `TZ`, sonst Europe/Berlin).
Angezeigte Zeitstempel formatiert der Server nie, die Oberfläche zeigt sie in der Zeit des Geräts."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import LOCAL_TIMEZONE, default_timezone, known_timezones
from ..models import Job
from ..services import settings as settings_service

logger = logging.getLogger("nodvard_deck.timezone")

SETTING_KEY = "system.timezone"

# Prozess-Cache: wird bei jedem Zeitplan-Anlegen und jeder Wartungsfenster-Prüfung gebraucht.
# `services.settings.set_global` verwirft ihn beim Schreiben des Schlüssels, egal auf welchem Weg.
_cached: str | None = None


class InvalidTimezone(ValueError):
    """Kein Name aus der Zonendatenbank (`zoneinfo.available_timezones()`)."""


def is_valid_timezone(name: object) -> bool:
    return isinstance(name, str) and name in known_timezones()


def reset_timezone_cache() -> None:
    """Verwirft den Prozess-Cache (nach jedem Schreiben des Schlüssels und in Tests)."""
    global _cached
    _cached = None


async def get_timezone(session: AsyncSession) -> str:
    """Die eingestellte Zone, sonst die Vorgabe; ein ungültiger Wert (z. B. von Hand bearbeitet) zählt als nicht gesetzt."""
    global _cached
    if _cached is not None:
        return _cached
    stored = await settings_service.get_global(session, SETTING_KEY)
    zone = stored if is_valid_timezone(stored) else default_timezone()
    _cached = zone
    return zone


async def set_timezone(session: AsyncSession, name: str, *, updated_by_user_id: str | None = None) -> int:
    """Stellt die Zone um und liefert die Zahl der umgestellten Jobs, die dabei neu geplant werden."""
    global _cached
    if not is_valid_timezone(name):
        raise InvalidTimezone(f"Unbekannte Zeitzone: {name!r}")

    # Umgestellt werden nur Jobs mit der bisherigen Standardzone, eine ausdrücklich andere bleibt. Beim ersten Umstellen
    # gilt auch `LOCAL_TIMEZONE` als Standard: Jobs aus älteren Datenbanken tragen es, auch wenn die Umgebung (z. B.
    # `TZ=UTC`) etwas anderes nennt.
    previous = await get_timezone(session)
    had_setting = await settings_service.get_global(session, SETTING_KEY) is not None
    old_defaults = {previous} if had_setting else {previous, LOCAL_TIMEZONE}
    old_defaults.discard(name)

    jobs = []
    if old_defaults:
        result = await session.execute(select(Job).where(Job.timezone.in_(old_defaults)))
        jobs = list(result.scalars().all())
    for job in jobs:
        job.timezone = name
    await settings_service.set_global(session, SETTING_KEY, name, updated_by_user_id=updated_by_user_id)

    # `schedule()` schreibt `next_run_at` über eine eigene Session: vorher committen, sonst wartet sie auf unsere
    # Schreibsperre.
    await session.commit()
    _cached = name
    await _reschedule(jobs)
    logger.info("timezone_changed to=%s jobs=%d", name, len(jobs))
    return len(jobs)


async def _reschedule(jobs: list[Job]) -> None:
    from ..ext.runtime import get_extension_runtime
    from .scheduler import CORE_SCHEDULER_EXT_ID, get_scheduler_service

    registry = get_extension_runtime().scheduler
    service = get_scheduler_service()
    for job in jobs:
        if not job.enabled:
            continue
        handler = registry.get(job.ext_id if job.ext_id is not None else CORE_SCHEDULER_EXT_ID, job.ext_job_key or "")
        # Jobs einer gerade nicht geladenen Erweiterung haben keinen Handler: Ihre Zeile trägt schon die neue Zone,
        # geplant wird beim nächsten `register_job()`.
        if handler is not None:
            await service.schedule(job, handler)
