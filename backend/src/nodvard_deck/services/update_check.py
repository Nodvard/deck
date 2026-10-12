"""Nach Updates suchen: Einstellungen und täglicher Kern-Job `system-update-check`.

Die Abfrage selbst und der Cache stehen in `core/updates.py`."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..core import updates
from ..db.session import session_scope
from . import jobs as jobs_service
from . import settings as settings_service

logger = logging.getLogger("nodvard_deck.update_check")

JOB_KEY = "system-update-check"
JOB_NAME = "Nach Updates suchen"
SCHEDULE = "41 4 * * *"
"""Einmal am Tag, nachts in der eingestellten Zeitzone (nicht zur vollen Stunde)."""

# `enabled` (Vorgabe an) schaltet den täglichen Job; der Knopf "Jetzt suchen" geht trotzdem. Bei jeder Prüfung sieht
# GitHub (ghcr.io) die IP-Adresse und den Zeitpunkt, wer das nicht will, schaltet ab. `channel`: `stable` (Vorgabe, nur
# fertige Versionen) oder `beta` (auch Vorabversionen). Beide ändert `PUT /settings/{key}` (`settings.write`).
KEY_ENABLED = "system.update_check.enabled"
KEY_CHANNEL = "system.update_check.channel"
DEFAULT_ENABLED = True
DEFAULT_CHANNEL = updates.DEFAULT_CHANNEL
CHANNELS = updates.CHANNELS


async def load_config(session: AsyncSession) -> tuple[bool, str]:
    """(Schalter, Kanal) mit Vorgaben für fehlende oder ungültige Werte."""
    enabled = await settings_service.get_global(session, KEY_ENABLED)
    channel = await settings_service.get_global(session, KEY_CHANNEL)
    return (enabled if isinstance(enabled, bool) else DEFAULT_ENABLED), updates.normalize_channel(channel)


def _image(settings: Settings) -> str | None:
    return (settings.image or "").strip() or None


def running_version(settings: Settings) -> str:
    """Die Version, mit der "Nach Updates suchen" vergleicht (siehe `core.updates.running_version`)."""
    return updates.running_version(settings)


async def current_status(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    """Letzter bekannter Stand (ohne Netz) plus Schalter."""
    enabled, channel = await load_config(session)
    result = updates.status(settings.data_dir, channel=channel, image=_image(settings), current=running_version(settings))
    return {**result, "enabled": enabled}


async def check_now(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    """Fragt die Registry jetzt (nie eine Ausnahme, siehe `core.updates.check`)."""
    enabled, channel = await load_config(session)
    result = await updates.check(settings.data_dir, channel=channel, image=_image(settings), current=running_version(settings))
    return {**result, "enabled": enabled}


async def _job_handler(**_: Any) -> dict[str, Any]:
    settings = get_settings()
    async with session_scope() as session:
        result = await check_now(session, settings)
    return {k: result[k] for k in ("current", "latest", "available", "channel", "source")}


async def sync_job() -> None:
    """Legt den Kern-Job an bzw. setzt den Schalter und plant neu: beim Start (`register_core_jobs`) und
    nach jeder Änderung von `system.update_check.enabled`."""
    from ..core.scheduler import CORE_SCHEDULER_EXT_ID, get_scheduler_service
    from ..ext.runtime import get_extension_runtime

    # Der Zeitplan kommt nur aus dieser Einstellung: `PATCH /jobs/{id}` lehnt den Job mit 409 ab, sonst setzte ihn der
    # nächste Start oder eine Änderung der Einstellung still zurück.
    async with session_scope() as session:
        enabled, _channel = await load_config(session)
        job = await jobs_service.upsert_job(
            session, ext_id=None, ext_job_key=JOB_KEY, name=JOB_NAME, kind="core",
            schedule=SCHEDULE, params={}, enabled=enabled,
        )
    get_extension_runtime().scheduler.register(CORE_SCHEDULER_EXT_ID, JOB_KEY, _job_handler)
    await get_scheduler_service().schedule(job, _job_handler)
