"""Zeitzone des Dashboards -- Einstellung `system.timezone`.

Eine Zone fuer alles, was der Nutzer als Uhrzeit einstellt: Zeitplaene (`Job.timezone`,
auch die der Erweiterungen, z. B. das Morgen-Briefing um 07:00) und Wartungsfenster
(`core.maintenance`). Vorgabe, solange nichts eingestellt ist: `config.default_timezone()`
(`NODVARD_DECK_TIMEZONE`, sonst `TZ`, sonst Europe/Berlin). Angezeigte Zeitstempel (Protokoll,
Meldungen) formatiert der Server nie; die Oberflaeche zeigt sie in der Zeit des Geraets.

`get_timezone` haelt den Wert im Prozess (er wird bei jedem Zeitplan-Anlegen und jeder
Wartungsfenster-Pruefung gebraucht). `services.settings.set_global` verwirft den Cache, sobald
der Schluessel geschrieben wird, egal auf welchem Weg.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import LOCAL_TIMEZONE, default_timezone, known_timezones
from ..models import Job
from ..services import settings as settings_service

logger = logging.getLogger("nodvard_deck.timezone")

SETTING_KEY = "system.timezone"

_cached: str | None = None


class InvalidTimezone(ValueError):
    """Kein Name aus der Zonendatenbank (`zoneinfo.available_timezones()`)."""


def is_valid_timezone(name: object) -> bool:
    return isinstance(name, str) and name in known_timezones()


def reset_timezone_cache() -> None:
    """Verwirft den Prozess-Cache. Nach jedem Schreiben des Schluessels und in Tests."""
    global _cached
    _cached = None


async def get_timezone(session: AsyncSession) -> str:
    """Die eingestellte Zone, sonst die Vorgabe aus der Umgebung. Ein ungueltiger gespeicherter
    Wert (z. B. nach einer Datenbank-Bearbeitung von Hand) zaehlt als nicht gesetzt."""
    global _cached
    if _cached is not None:
        return _cached
    stored = await settings_service.get_global(session, SETTING_KEY)
    zone = stored if is_valid_timezone(stored) else default_timezone()
    _cached = zone
    return zone


async def set_timezone(session: AsyncSession, name: str, *, updated_by_user_id: str | None = None) -> int:
    """Stellt die Zone um und liefert die Zahl der umgestellten Jobs.

    Umgestellt werden nur Jobs mit der bisherigen Standardzone; eine ausdruecklich andere Zone
    (heute setzt die API keine) bleibt. Beim allerersten Umstellen zaehlt auch das frueher fest
    eingetragene `LOCAL_TIMEZONE` als Standard: jeder Job aus einer aelteren Version traegt es,
    auch wenn die Umgebung (z. B. `TZ=UTC` im Container) eine andere Vorgabe nennt.

    Die Jobs werden danach beim Scheduler neu geplant, damit `next_run_at` stimmt. Fuer
    Jobs einer Erweiterung, die gerade nicht geladen ist, gibt es keinen Handler: ihre Zeile
    traegt schon die neue Zone, geplant wird beim naechsten `register_job()`."""
    global _cached
    if not is_valid_timezone(name):
        raise InvalidTimezone(f"Unbekannte Zeitzone: {name!r}")

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

    # `schedule()` schreibt `next_run_at` ueber eine EIGENE Sitzung -- vorher committen, sonst
    # wartet die auf unsere Schreibsperre (siehe `api/v1/jobs.py::patch_job`, D-14).
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
        if handler is not None:
            await service.schedule(job, handler)
