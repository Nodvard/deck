"""Alte Meldungen aufräumen (Kern-Job `notifications-retention`): Gelöscht wird, was vor mehr als
`notifications.retention_days` Tagen entstand (1 bis 3650, Vorgabe 90), samt Zustellprotokollen. Ausnahme: ungelesene
kritische Meldungen bleiben stehen."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from nodvard_sdk import Severity
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..models import Notification
from . import settings as settings_service
from .notifications import delete_notifications

logger = logging.getLogger("nodvard_deck.notification_retention")

KEEP_UNREAD_SEVERITY = Severity.CRITICAL.value
"""Eine ungelesene Meldung dieser Wichtigkeit (`critical`) bleibt stehen, bis jemand sie liest; gelesen geht sie nach der
Frist wie jede andere."""
RETENTION_KEY = "notifications.retention_days"
RETENTION_MIN_DAYS = 1
RETENTION_MAX_DAYS = 3650
DEFAULT_RETENTION_DAYS = 90
# Häppchen mit Commit und kurzer Pause: SQLite hat nur einen Schreiber, ein langer Löschlauf ließe Anmeldung, Zeitpläne
# und neue Meldungen bis zum `busy_timeout` warten.
CHUNK_SIZE = 5000
CHUNK_PAUSE_SECONDS = 0.05


@dataclass
class PurgeResult:
    deleted: int = 0
    chunks: int = 0


def is_valid_retention(value: object) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool)
        and RETENTION_MIN_DAYS <= value <= RETENTION_MAX_DAYS
    )


async def effective_retention_days(session: AsyncSession) -> int:
    """Aufbewahrung in Tagen: die Einstellung `notifications.retention_days`, sonst 90. Ein Wert außerhalb von 1 bis
    3650 (von Hand in die Tabelle geschrieben) zählt als nicht gesetzt, damit der Verlauf nicht fast leer geräumt
    wird."""
    stored = await settings_service.get_global(session, RETENTION_KEY)
    return stored if is_valid_retention(stored) else DEFAULT_RETENTION_DAYS


async def purge_old_notifications(
    session: AsyncSession, *, retention_days: int, chunk_size: int = CHUNK_SIZE,
    pause: float = CHUNK_PAUSE_SECONDS, now: datetime | None = None,
) -> PurgeResult:
    """Löscht Meldungen, die vor mehr als `retention_days` Tagen entstanden, und committet selbst nach jedem Häppchen.
    Meldungen von heute sind nie betroffen (kleinste Einstellung: ein Tag), eine gerade zugestellte also auch nicht."""
    cutoff = (now or utcnow()) - timedelta(days=retention_days)
    result = PurgeResult()
    while True:
        ids = list(
            (
                await session.execute(
                    select(Notification.id)
                    .where(
                        Notification.ts < cutoff,
                        # Ungelesen und kritisch bleibt stehen (`severity` ist nie NULL).
                        or_(Notification.read_at.isnot(None), Notification.severity != KEEP_UNREAD_SEVERITY),
                    )
                    .order_by(Notification.ts.asc(), Notification.id.asc()).limit(chunk_size)
                )
            ).scalars().all()
        )
        if not ids:
            break
        result.deleted += await delete_notifications(session, ids)
        await session.commit()
        result.chunks += 1
        if len(ids) < chunk_size:
            break
        # Anderen Schreibern (Anmeldung, Zeitpläne, neue Meldungen) eine Lücke lassen.
        await asyncio.sleep(pause)
    # Kein VACUUM, siehe `services.job_retention`.
    if result.deleted:
        logger.info("notifications_purged deleted=%d chunks=%d", result.deleted, result.chunks)
    return result


async def run_purge() -> dict[str, int]:
    """Der Kern-Job: Aufbewahrung aus den Einstellungen lesen und aufräumen."""
    from ..db.session import session_scope

    async with session_scope() as session:
        days = await effective_retention_days(session)
        result = await purge_old_notifications(session, retention_days=days)
    return {"retention_days": days, "deleted": result.deleted, "chunks": result.chunks}


__all__ = [
    "CHUNK_SIZE",
    "DEFAULT_RETENTION_DAYS",
    "KEEP_UNREAD_SEVERITY",
    "RETENTION_KEY",
    "RETENTION_MAX_DAYS",
    "RETENTION_MIN_DAYS",
    "PurgeResult",
    "effective_retention_days",
    "is_valid_retention",
    "purge_old_notifications",
    "run_purge",
]
