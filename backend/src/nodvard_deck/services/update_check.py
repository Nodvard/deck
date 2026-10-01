"""Nach Updates suchen: Einstellungen und taeglicher Kern-Job `system-update-check`.

Die Abfrage selbst und der Cache stehen in `core/updates.py`. Hier:

* `system.update_check.enabled` (Vorgabe an): ob der taegliche Job laeuft. Datenschutz: Bei jeder Pruefung
  sieht GitHub (ghcr.io) die IP-Adresse und den Zeitpunkt; wer das nicht will, schaltet hier ab. Der Knopf "Jetzt suchen"
  geht trotzdem (ausdruecklicher Klick).
* `system.update_check.channel`: `stable` (Vorgabe, nur fertige Versionen) oder `beta` (auch Vorabversionen).
* Laufende Version fuer den Vergleich: `running_version` (beim offiziellen Image dessen genaue Version, auch eine
  Vorabversion; sonst `version.__version__`).

Beide aendert `PUT /settings/{key}` (`settings.write`); nach einer Aenderung des Schalters plant
`sync_job()` den Job neu. Ueber `PATCH /jobs/{id}` laesst sich der Job nicht umlegen (409), sonst setzte
der naechste Start oder die naechste Aenderung der Einstellung ihn still zurueck.
"""

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

KEY_ENABLED = "system.update_check.enabled"
KEY_CHANNEL = "system.update_check.channel"
DEFAULT_ENABLED = True
DEFAULT_CHANNEL = updates.DEFAULT_CHANNEL
CHANNELS = updates.CHANNELS


async def load_config(session: AsyncSession) -> tuple[bool, str]:
    """(Schalter, Kanal) mit Vorgaben fuer fehlende oder ungueltige Werte."""
    enabled = await settings_service.get_global(session, KEY_ENABLED)
    channel = await settings_service.get_global(session, KEY_CHANNEL)
    return (enabled if isinstance(enabled, bool) else DEFAULT_ENABLED), updates.normalize_channel(channel)


def _image(settings: Settings) -> str | None:
    return (settings.image or "").strip() or None


def running_version(settings: Settings) -> str:
    """Die Version, mit der "Nach Updates suchen" vergleicht (`core.updates.running_version`): beim offiziellen
    Image dessen genaue Version aus der Datei im Image, auch eine Vorabversion; sonst `version.__version__`."""
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
    """Legt den Kern-Job an bzw. setzt den Schalter und plant neu. Beim Start (`register_core_jobs`) und
    nach jeder Aenderung von `system.update_check.enabled`."""
    from ..core.scheduler import CORE_SCHEDULER_EXT_ID, get_scheduler_service
    from ..ext.runtime import get_extension_runtime

    async with session_scope() as session:
        enabled, _channel = await load_config(session)
        job = await jobs_service.upsert_job(
            session, ext_id=None, ext_job_key=JOB_KEY, name=JOB_NAME, kind="core",
            schedule=SCHEDULE, params={}, enabled=enabled,
        )
    get_extension_runtime().scheduler.register(CORE_SCHEDULER_EXT_ID, JOB_KEY, _job_handler)
    await get_scheduler_service().schedule(job, _job_handler)
