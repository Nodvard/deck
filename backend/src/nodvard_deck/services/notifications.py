"""Das Notification-Center -- docs/03-DATA-MODEL.md §8, docs/02-EXTENSION-API.md §3.

Ein `Notification`-Datensatz entsteht IMMER (sichtbar im In-App-Verlauf), auch wenn
ein Wartungsfenster den Versand an externe Kanaele (ntfy, E-Mail, ...) unterdrueckt --
"unterdruecken" heisst hier "niemanden um 3 Uhr nachts per Push stoeren", nicht "so
tun, als waere nichts passiert". Kanaele sind Connector-Instanzen, die die
`NotificationChannel`-Capability erfuellen (docs/03 §8) -- unrestricted Query, weil
das Notification-Center Kern-Code ist, keine Extension (dasselbe Muster wie
`core.gate.find_executor()`).
"""

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
    """`payload["host_ids"]` nur, wenn es eine nicht leere Liste (oder ein Tupel) aus
    Host-IDs ist. Ein unbrauchbarer Eintrag macht die ganze Liste ungueltig (Meldung
    bleibt hoerbar) -- eine stillschweigend verkuerzte Liste koennte sonst einen fremden
    Server mit still schalten. Ein einzelner String ist keine Liste."""
    raw = payload.get("host_ids")
    if isinstance(raw, (list, tuple)) and raw and all(isinstance(h, str) and h for h in raw):
        return list(raw)
    return None


async def would_suppress(session: AsyncSession, payload: dict[str, Any] | None) -> bool:
    """Die EINE Regel, nach der `deliver()` den Versand an die Kanaele unterdrueckt --
    auch einzeln abfragbar (`ctx.notify.would_suppress()`), ohne etwas anzulegen. Ein
    Waechter holt damit eine im Fenster stumme Meldung danach nach, ohne bei jeder
    Pruefung einen weiteren (stummen) Verlaufseintrag zu erzeugen."""
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
    """Legt die Meldung an, stellt sie zu und liefert `(Datensatz, unterdrueckt)`.

    `payload.get("host_id")` ist die Konvention (kein eigenes SDK-Feld, um den
    stabilen `nodvard_sdk.types.Notification`-Vertrag nicht zu aendern), ueber die
    eine host-bezogene Benachrichtigung fuer die Wartungsfenster-Pruefung markiert
    wird -- eine Benachrichtigung ohne `host_id` im Payload wird nie unterdrueckt.
    Eine Sammelmeldung ueber mehrere Server traegt stattdessen `payload["host_ids"]`
    (Liste von Host-IDs): still nur, wenn alle in einem laufenden Fenster liegen.

    Kanalfehler stoppen den Aufrufer nicht (sie stehen im Zustellprotokoll). Mit
    `raise_on_failure` wirft es NACH dem Speichern `NotificationNotDelivered`, wenn es
    Kanaele gab und keiner zugestellt hat -- fuer Aufrufer, die dann erneut versuchen
    wollen. Kein Kanal oder ein Wartungsfenster (`suppressed`) ist kein Fehler."""
    payload = payload or {}
    row = Notification(
        severity=severity, title=title, body=body, source_ext_id=source_ext_id,
        correlation_id=correlation_id, payload=payload,
    )
    session.add(row)
    await session.flush()
    notification_id = row.id

    suppressed = await would_suppress(session, payload)
    # VOR den Kanaelen committen. Der flush oben startet die
    # Schreibtransaktion; bliebe sie offen, hielte sie die SQLite-Schreibsperre fuer
    # die ganze Dauer des ntfy-Aufrufs (httpx-Timeout 5 s). Ein Kanal, der selbst
    # schreibt (ntfy mit Token -> ctx.vault_use() -> 'secret.used' ueber eine eigene
    # Verbindung), liefe sonst in busy_timeout und scheiterte mit 'database is locked',
    # und jeder andere Schreibzugriff (Bestaetigen, Login, ...) ebenso. Einziger
    # Aufrufer ist NotifyHandle.send() mit einer frischen session_scope() -- der
    # Zwischen-Commit nimmt also nichts Fremdes mit (D-14-Muster).
    await session.commit()

    from ..ext.runtime import get_extension_runtime

    runtime = get_extension_runtime()
    channels = runtime.capabilities.query(NotificationChannel, allowed_ext_ids=None)

    sdk_notification = SdkNotification(
        title=title, body=body, severity=Severity(severity), correlation_id=correlation_id, payload=payload,
    )

    # Erst alle Kanaele anfragen (ohne offene Schreibtransaktion), dann die
    # Zustellprotokolle in einem Rutsch schreiben.
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
    """Schreibt die Zustellprotokolle -- nur, solange die Meldung noch da ist.

    Die Kanaele brauchen Sekunden (ntfy: bis zu 5 s), die Meldung steht aber schon in der Liste:
    wer sie in der Zwischenzeit loescht (von Hand, `DELETE /notifications/{id}`), soll den Versand
    nicht mit einem Fremdschluessel-Fehler abbrechen, und es soll keine verwaiste Zeile bleiben.
    Darum ein einziges `INSERT ... SELECT ... WHERE EXISTS` je Kanal: Pruefung und Schreiben laufen
    in derselben Schreibsperre, kein Loeschen kann dazwischenkommen."""
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
    """Fuer "Alle als gelesen": ein UPDATE statt einer
    Liste von IDs, die der Client erst vollstaendig laden muesste."""
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
    """Loescht die Meldungen `ids` samt ihren Zustellprotokollen und gibt zurueck, wie viele es
    wirklich gab (unbekannte Kennungen zaehlen nicht). Schreibt nur (`flush`), committet nicht.

    Die Zustellprotokolle werden ausdruecklich mitgeloescht, nicht nur ueber `ON DELETE CASCADE`: die
    Fremdschluessel-Pruefung (`PRAGMA foreign_keys`) haengt an der Verbindung (`db.session`), und eine
    verwaiste Zeile wuerde nie wieder aufgeraeumt."""
    removed = 0
    for start in range(0, len(ids), _DELETE_BATCH):
        batch = ids[start:start + _DELETE_BATCH]
        await session.execute(delete(NotificationDelivery).where(NotificationDelivery.notification_id.in_(batch)))
        result = await session.execute(delete(Notification).where(Notification.id.in_(batch)))
        removed += int(result.rowcount or 0)
    await session.flush()
    return removed


async def delete_notification(session: AsyncSession, notification_id: str) -> str | None:
    """Loescht eine Meldung und gibt ihren Titel zurueck (fuer das Protokoll); `None`, wenn es sie
    nicht gibt."""
    row = await session.get(Notification, notification_id)
    if row is None:
        return None
    title = row.title
    await delete_notifications(session, [notification_id])
    return title


@dataclass
class DeleteProgress:
    """Wie viele Meldungen `delete_read` schon endgueltig geloescht (committet) hat. Der Aufrufer legt es an und liest es
    auch dann, wenn `delete_read` mitten im Lauf eine Ausnahme wirft: die Haeppchen davor sind dann schon weg."""

    deleted: int = 0


async def delete_read(
    session: AsyncSession, *, chunk_size: int = 5000, pause: float = 0.05, progress: DeleteProgress | None = None,
) -> int:
    """Loescht alle gelesenen Meldungen (`read_at` gesetzt); ungelesene bleiben. Gibt die Anzahl zurueck.

    Wie der naechtliche Aufraeum-Job in Haeppchen mit Commit dazwischen: in einer einzigen Transaktion hielte ein grosser
    Bestand (100 000 gelesene Meldungen, rund 10 s) die Schreibsperre so lange, dass gleichzeitige Schreiber (Anmeldung,
    Zeitplaene) nach 5 s mit "database is locked" scheitern. Scheitert ein spaeteres Haeppchen, sind die frueheren
    schon weg: `progress.deleted` nennt dann, wie viele (die Ausnahme geht unveraendert weiter)."""
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
        if removed == 0 or len(ids) < chunk_size:  # fertig (oder gleichzeitig geloescht): nicht endlos wiederholen
            return total
        await asyncio.sleep(pause)  # anderen Schreibern eine Luecke lassen
