"""Das Notification-Center: legt Meldungen an und stellt sie an die Kanäle (`NotificationChannel`) zu.
Eine Meldung entsteht immer (sichtbar im In-App-Verlauf), auch wenn ein Wartungsfenster den Versand an externe Kanäle
unterdrückt: Das heißt, niemanden nachts per Push zu stören, nicht so zu tun, als wäre nichts passiert."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from nodvard_sdk import Notification as SdkNotification, Severity
from nodvard_sdk.capabilities import NotificationChannel
from nodvard_sdk.errors import NotificationNotDelivered, NotificationSkipped
from sqlalchemy import Text, delete, insert, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import maintenance
from ..db import new_id
from ..models import Notification, NotificationDelivery

_DELETE_BATCH = 500
"""Zeilen je `DELETE ... IN (...)`: bleibt weit unter dem Variablen-Limit alter SQLite-Versionen (999)."""


def _payload_host_ids(payload: dict[str, Any]) -> list[str] | None:
    """`payload["host_ids"]` nur, wenn es eine nicht leere Liste (oder ein Tupel) aus Host-IDs ist. Ein unbrauchbarer
    Eintrag macht die ganze Liste ungültig (Meldung bleibt hörbar), denn eine still verkürzte Liste könnte einen fremden
    Server mit stumm schalten. Ein einzelner String ist keine Liste."""
    raw = payload.get("host_ids")
    if isinstance(raw, (list, tuple)) and raw and all(isinstance(h, str) and h for h in raw):
        return list(raw)
    return None


async def would_suppress(session: AsyncSession, payload: dict[str, Any] | None) -> bool:
    """Die eine Regel, nach der `deliver()` den Versand unterdrückt; auch einzeln abfragbar
    (`ctx.notify.would_suppress()`), damit ein Wächter eine im Fenster stumme Meldung später nachholt, ohne bei jeder
    Prüfung einen neuen Verlaufseintrag anzulegen."""
    payload = payload or {}
    return await maintenance.is_in_window(
        session, host_id=payload.get("host_id"), host_ids=_payload_host_ids(payload)
    )


async def send(session: AsyncSession, **kwargs: Any) -> Notification:
    """Wie `deliver()`, liefert nur den Datensatz (Kern-Aufrufer und Tests)."""
    row, _suppressed = await deliver(session, **kwargs)
    return row


async def deliver(
    session: AsyncSession,
    *,
    title: str,
    body: str,
    severity: str = Severity.INFO.value,
    source_ext_id: str | None = None,
    correlation_id: str | None = None,
    payload: dict[str, Any] | None = None,
    raise_on_failure: bool = False,
) -> tuple[Notification, bool]:
    """Legt die Meldung an, stellt sie zu und liefert `(Datensatz, unterdrückt)`; Kanalfehler stehen im
    Zustellprotokoll. Mit `raise_on_failure` wirft es nach dem Speichern `NotificationNotDelivered`, wenn es Kanäle gab
    und keiner zugestellt hat (kein Kanal oder ein Wartungsfenster ist kein Fehler)."""
    # `payload["host_id"]` markiert eine host-bezogene Meldung für die Wartungsfenster-Prüfung (Konvention statt
    # SDK-Feld, um den stabilen `Notification`-Vertrag nicht zu ändern); ohne `host_id` wird nie unterdrückt. Eine
    # Sammelmeldung trägt `payload["host_ids"]`: still nur, wenn alle in einem laufenden Fenster liegen.
    payload = payload or {}
    row = Notification(
        severity=severity, title=title, body=body, source_ext_id=source_ext_id,
        correlation_id=correlation_id, payload=payload,
    )
    session.add(row)
    await session.flush()
    notification_id = row.id

    suppressed = await would_suppress(session, payload)
    # Vor den Kanälen committen: Der flush hielte sonst die SQLite-Schreibsperre für die ganze Dauer des Push-Aufrufs,
    # und ein Kanal, der selbst schreibt (Token aus dem Tresor, Audit über eine eigene Verbindung), scheiterte mit
    # "database is locked". Einziger Aufrufer ist `NotifyHandle.send()` mit frischer `session_scope()`: der
    # Zwischen-Commit nimmt nichts Fremdes mit.
    await session.commit()

    from ..ext.runtime import get_extension_runtime

    runtime = get_extension_runtime()
    # Ohne Beschränkung auf Extensions: Das Notification-Center ist Kern-Code.
    channels = runtime.capabilities.query(NotificationChannel, allowed_ext_ids=None)

    sdk_notification = SdkNotification(
        title=title, body=body, severity=Severity(severity), correlation_id=correlation_id, payload=payload,
    )

    # Erst alle Kanäle anfragen (ohne offene Schreibtransaktion), dann die Zustellprotokolle in einem Rutsch schreiben.
    outcomes: list[tuple[str, str, str | None]] = []
    for channel in channels:
        if suppressed:
            outcomes.append((channel.channel_id, "suppressed", None))
            continue
        try:
            await channel.send(sdk_notification)
            outcomes.append((channel.channel_id, "sent", None))
        except NotificationSkipped:
            # Bewusst nicht weitergegeben (z. B. unter der Mindest-Stufe des Kanals): weder zugestellt noch Fehler.
            outcomes.append((channel.channel_id, "skipped", None))
        except Exception as exc:  # noqa: BLE001 - ein kaputter Kanal darf weder andere noch den Aufrufer stoppen
            outcomes.append((channel.channel_id, "failed", str(exc)))

    await _log_deliveries(session, notification_id, outcomes)
    await session.commit()

    from ..core.ws_hub import get_ws_hub

    await get_ws_hub().publish(
        "notifications",
        {"id": notification_id, "severity": severity, "title": title, "body": body, "suppressed": suppressed},
        required_permission="notifications.read",
    )
    if raise_on_failure and not any(status == "sent" for _, status, _ in outcomes):
        failures = [(channel_id, error or "") for channel_id, status, error in outcomes if status == "failed"]
        if failures:
            raise NotificationNotDelivered(failures)
    return row, suppressed


async def _log_deliveries(
    session: AsyncSession, notification_id: str, outcomes: list[tuple[str, str, str | None]]
) -> None:
    """Schreibt die Zustellprotokolle, nur solange die Meldung noch da ist (jemand kann sie während des Versands
    löschen). Ein `INSERT ... SELECT ... WHERE EXISTS` je Kanal: Prüfung und Schreiben laufen in derselben
    Schreibsperre, es bleibt keine verwaiste Zeile und der Versand scheitert nicht an einem Fremdschlüssel-Fehler."""
    still_there = select(Notification.id).where(Notification.id == notification_id).exists()
    for channel_id, status, error in outcomes:
        await session.execute(
            insert(NotificationDelivery).from_select(
                ["id", "notification_id", "channel_id", "status", "error"],
                select(
                    literal(new_id()), literal(notification_id), literal(channel_id), literal(status),
                    literal(error, type_=Text()),
                ).where(still_there),
            )
        )
    await session.flush()


async def list_notifications(
    session: AsyncSession, *, unread: bool | None = None, limit: int = 100, offset: int = 0
) -> list[Notification]:
    stmt = select(Notification).order_by(Notification.ts.desc())
    if unread is True:
        stmt = stmt.where(Notification.read_at.is_(None))
    elif unread is False:
        stmt = stmt.where(Notification.read_at.isnot(None))
    stmt = stmt.limit(min(limit, 1000)).offset(max(offset, 0))
    return list((await session.execute(stmt)).scalars().all())


async def get_notification(session: AsyncSession, notification_id: str) -> Notification | None:
    return await session.get(Notification, notification_id)


async def unread_count(session: AsyncSession) -> int:
    from sqlalchemy import func

    stmt = select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))
    return int((await session.execute(stmt)).scalar_one())


async def mark_all_read(session: AsyncSession) -> int:
    """Für "Alle als gelesen": ein UPDATE statt einer ID-Liste, die der Client erst vollständig laden müsste."""
    from sqlalchemy import update

    from ..db import utcnow

    result = await session.execute(
        update(Notification).where(Notification.read_at.is_(None)).values(read_at=utcnow())
    )
    await session.flush()
    return int(result.rowcount or 0)


async def mark_read(session: AsyncSession, notification_ids: list[str]) -> int:
    from ..db import utcnow

    count = 0
    for notification_id in notification_ids:
        row = await session.get(Notification, notification_id)
        if row is not None and row.read_at is None:
            row.read_at = utcnow()
            count += 1
    await session.flush()
    return count


async def delete_notifications(session: AsyncSession, ids: list[str]) -> int:
    """Löscht die Meldungen `ids` samt Zustellprotokollen; gibt zurück, wie viele es gab, und schreibt nur (`flush`).
    Die Protokolle gehen ausdrücklich mit, nicht nur per `ON DELETE CASCADE`: Die Fremdschlüssel-Prüfung hängt an der
    Verbindung, und eine verwaiste Zeile würde nie wieder aufgeräumt."""
    removed = 0
    for start in range(0, len(ids), _DELETE_BATCH):
        batch = ids[start:start + _DELETE_BATCH]
        await session.execute(delete(NotificationDelivery).where(NotificationDelivery.notification_id.in_(batch)))
        result = await session.execute(delete(Notification).where(Notification.id.in_(batch)))
        removed += int(result.rowcount or 0)
    await session.flush()
    return removed


async def delete_notification(session: AsyncSession, notification_id: str) -> str | None:
    """Löscht eine Meldung und gibt ihren Titel zurück (fürs Protokoll); `None`, wenn es sie nicht gibt."""
    row = await session.get(Notification, notification_id)
    if row is None:
        return None
    title = row.title
    await delete_notifications(session, [notification_id])
    return title


@dataclass
class DeleteProgress:
    """Wie viele Meldungen `delete_read` schon endgültig gelöscht (committet) hat; der Aufrufer liest es auch, wenn
    `delete_read` wirft."""

    deleted: int = 0


async def delete_read(
    session: AsyncSession, *, chunk_size: int = 5000, pause: float = 0.05, progress: DeleteProgress | None = None,
) -> int:
    """Löscht alle gelesenen Meldungen und gibt die Anzahl zurück, in Häppchen mit Commit dazwischen.
    In einer Transaktion hielte ein großer Bestand (100 000 Meldungen, rund 10 s) die Schreibsperre so lange, dass andere
    Schreiber nach 5 s scheitern. Wirft ein Häppchen, nennt `progress.deleted`, was schon weg ist."""
    total = 0
    while True:
        ids = list(
            (
                await session.execute(
                    select(Notification.id).where(Notification.read_at.isnot(None))
                    .order_by(Notification.id).limit(chunk_size)
                )
            ).scalars().all()
        )
        if not ids:
            return total
        removed = await delete_notifications(session, ids)
        await session.commit()
        total += removed
        if progress is not None:
            progress.deleted = total
        if removed == 0 or len(ids) < chunk_size:  # fertig (oder gleichzeitig gelöscht): nicht endlos wiederholen
            return total
        await asyncio.sleep(pause)  # anderen Schreibern eine Lücke lassen
