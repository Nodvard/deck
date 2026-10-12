"""Alte Meldungen aufraeumen (Kern-Job `notifications-retention`).

Jede Meldung (`notifications`) steht samt ihrer Zustellprotokolle (`notification_deliveries`) so
lange in der Datenbank, bis jemand sie loescht. Haeufige Meldungen (Erreichbarkeit, Wachen,
Lageberichte) kommen so ueber die Jahre auf tausende Zeilen; die Meldungsseite und die Glocke
laden die neuesten, alles darunter liest niemand mehr.

Regeln:

* Geloescht wird, was **vor mehr als N Tagen entstand** (`ts`, Einstellung
  `notifications.retention_days`, 1 bis 3650, Vorgabe 90) -- gelesen oder ungelesen. **Ausnahme: eine
  ungelesene kritische Meldung (`severity` `critical`) bleibt stehen**, bis jemand sie liest: eine
  kritische Meldung, die noch niemand gesehen hat, soll nicht still verschwinden. Gelesen und kritisch geht
  nach der Frist wie alles andere. Die Oberflaeche sagt das bei der Einstellung.
* Die Zustellprotokolle gehen **ausdruecklich mit**, nicht nur ueber `ON DELETE CASCADE`: die
  Fremdschluessel-Pruefung (`PRAGMA foreign_keys`) haengt an der Verbindung und ist in Test-Datenbanken
  ohne `create_engine_for` aus, und eine verwaiste Zeile waere nie wieder erreichbar.
* Zeilen gehen in **Haeppchen** weg (Vorgabe 5000 je Durchgang, Commit dazwischen, kurze Pause):
  SQLite hat nur einen Schreiber, ein langer Loeschlauf liesse alles andere (Anmeldung, Zeitplaene,
  neue Meldungen) bis zum `busy_timeout` warten.
* **Meldungen von heute sind nie betroffen**: die kleinste Einstellung ist ein Tag, eine Meldung, die
  gerade zugestellt wird (`services.notifications.deliver`), ist also immer juenger. Von Hand
  geloeschte Meldungen waehrend einer Zustellung faengt `deliver()` selbst ab.

Kein `VACUUM`, aus demselben Grund wie in `services.job_retention` (WAL, `auto_vacuum=NONE`, freie
Seiten fuellt SQLite wieder auf).
"""

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
"""Eine ungelesene Meldung dieser Wichtigkeit (`critical`) loescht der Job nie."""
RETENTION_KEY = "notifications.retention_days"
RETENTION_MIN_DAYS = 1
RETENTION_MAX_DAYS = 3650
DEFAULT_RETENTION_DAYS = 90
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
    """Aufbewahrung der Meldungen in Tagen: die Einstellung `notifications.retention_days`, sonst 90.
    Ein gespeicherter Wert ausserhalb von 1 bis 3650 (von Hand in die Tabelle geschrieben) zaehlt als
    nicht gesetzt: lieber die Vorgabe nehmen als den Verlauf fast leer zu raeumen."""
    stored = await settings_service.get_global(session, RETENTION_KEY)
    return stored if is_valid_retention(stored) else DEFAULT_RETENTION_DAYS


async def purge_old_notifications(
    session: AsyncSession, *, retention_days: int, chunk_size: int = CHUNK_SIZE,
    pause: float = CHUNK_PAUSE_SECONDS, now: datetime | None = None,
) -> PurgeResult:
    """Siehe Moduldoku. Committet selbst (nach jedem Haeppchen)."""
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
        # Anderen Schreibern (Anmeldung, Zeitplaene, neue Meldungen) eine Luecke lassen.
        await asyncio.sleep(pause)
    if result.deleted:
        logger.info("notifications_purged deleted=%d chunks=%d", result.deleted, result.chunks)
    return result


async def run_purge() -> dict[str, int]:
    """Der Kern-Job: Aufbewahrung aus den Einstellungen lesen und aufraeumen."""
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
