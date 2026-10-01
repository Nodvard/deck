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

from typing import Any

from nodvard_sdk import Notification as SdkNotification, Severity
from nodvard_sdk.capabilities import NotificationChannel
from nodvard_sdk.errors import NotificationNotDelivered
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import maintenance
from ..models import Notification, NotificationDelivery


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
        except Exception as exc:  # noqa: BLE001 - ein kaputter Kanal darf weder andere noch den Aufrufer stoppen
            outcomes.append((channel.channel_id, "failed", str(exc)))

    for channel_id, status, error in outcomes:
        session.add(
            NotificationDelivery(
                notification_id=notification_id, channel_id=channel_id, status=status, error=error,
            )
        )
    await session.flush()
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
