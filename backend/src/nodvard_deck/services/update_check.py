"""Nach Updates suchen: Einstellungen und täglicher Kern-Job `system-update-check`; Abfrage und Cache stehen in
`core/updates.py`."""

from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import edition
from ..config import Settings, get_settings
from ..core import updates
from ..db.session import session_scope
from ..models import Notification, NotificationDelivery
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

KEY_AUTO_SECURITY = "system.update_check.auto_security"
"""Zustimmung des Inhabers, Sicherheitsupdates derselben Reihe selbst einzuspielen. Eingespielt wird erst, wenn
Anforderungen an den Helfer signiert sind; bis dahin meldet Deck sie nur (`notify_security`)."""
DEFAULT_AUTO_SECURITY = False
KEY_SECURITY_NOTIFIED = "system.update_check.security_notified"
"""Die Versionen, für die die Meldung „Sicherheitsupdate verfügbar“ schon kam; nur intern, nicht über die API."""
SECURITY_TITLE = "Sicherheitsupdate verfügbar"


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


async def _auto_security(session: AsyncSession) -> bool:
    return await settings_service.get_global(session, KEY_AUTO_SECURITY) is True


async def current_status(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    """Letzter bekannter Stand (ohne Netz) plus Schalter."""
    enabled, channel = await load_config(session)
    result = updates.status(
        settings.data_dir, channel=channel, image=_image(settings), current=running_version(settings),
        repository=edition.of(settings).repository,
    )
    return {**result, "enabled": enabled, "auto_security": await _auto_security(session)}


async def check_now(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    """Fragt die Registry jetzt (nie eine Ausnahme, siehe `core.updates.check`)."""
    enabled, channel = await load_config(session)
    result = await updates.check(
        settings.data_dir, channel=channel, image=_image(settings), current=running_version(settings),
        repository=edition.of(settings).repository,
    )
    await notify_security(result)
    return {**result, "enabled": enabled, "auto_security": await _auto_security(session)}


_security_slot: tuple[weakref.ReferenceType[asyncio.AbstractEventLoop], asyncio.Lock] | None = None


def _security_lock() -> asyncio.Lock:
    # Job und Knopf können gleichzeitig suchen; je Loop eine Sperre, der Platz hält den Loop nur schwach.
    global _security_slot
    loop = asyncio.get_running_loop()
    if _security_slot is None or _security_slot[0]() is not loop:
        _security_slot = (weakref.ref(loop), asyncio.Lock())
    return _security_slot[1]


async def notify_security(result: dict[str, Any]) -> None:
    """Meldet ein gerade gefundenes Sicherheitsupdate einmal je Version, rot und über alle Kanäle (auch Push)."""
    latest = result.get("latest")
    if result.get("source") != "live" or not result.get("available") or "sicherheit" not in result.get("kinds", []):
        return
    from . import notifications as notifications_service

    # Wie bei der Sicherung: Eine Meldung, die nicht rausgeht, darf die Suche nicht scheitern lassen.
    try:
        async with _security_lock(), session_scope() as session:
            notified = await settings_service.get_global(session, KEY_SECURITY_NOTIFIED, [])
            if isinstance(notified, str):
                notified = [notified]
                await settings_service.set_global(session, KEY_SECURITY_NOTIFIED, notified)
                await session.commit()
            if latest in notified:
                return
            notification = await notifications_service.send(
                session, title=SECURITY_TITLE, severity="critical",
                body=f"Version {latest} schließt Sicherheitslücken. Spiel sie bald ein: Einstellungen → System → "
                     "Updates.",
                payload={"path": "/settings/system", "tags": ["lock"], "notification_type": "deck.security_update"},
            )
            delivered = await session.scalar(select(NotificationDelivery.id).where(
                NotificationDelivery.notification_id == notification.id,
                NotificationDelivery.status == "sent",
            ).limit(1))
            # Auch die gespeicherte In-App-Meldung gilt als zugestellt.
            in_app = await session.scalar(select(Notification.id).where(Notification.id == notification.id))
            if delivered is None and in_app is None:
                return
            await settings_service.set_global(session, KEY_SECURITY_NOTIFIED, [*notified, latest])
            await session.commit()
    except Exception:
        logger.warning("security_notice_failed version=%s", latest, exc_info=True)


async def _job_handler(**_: Any) -> dict[str, Any]:
    settings = get_settings()
    async with session_scope() as session:
        result = await check_now(session, settings)
    return {k: result[k] for k in ("current", "latest", "available", "channel", "source")}


async def sync_job() -> None:
    """Legt den Kern-Job an bzw. setzt den Schalter und plant neu, beim Start und nach jeder Änderung von
    `system.update_check.enabled`."""
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
