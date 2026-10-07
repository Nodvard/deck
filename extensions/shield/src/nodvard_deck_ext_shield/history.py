"""Dauerhafte, durchsuchbare Vorfalls-Historie ueber
`ctx.db.session()` und die eigene Tabelle `ext_nexus_soc_incidents` (models.py).

Suche: ein freier Text trifft Nachricht, Container, Host UND die KI-Zusammenfassung
(man sucht selten nach dem exakten Containernamen, eher nach "OOM" oder "restart");
dazu Status, Host, Zeitraum. Sortierung immer neueste zuerst, mit `limit`/`offset`
und einer Gesamtzahl fuer die Seitennavigation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, func, or_, select

from .incident_text import clean_incident_message
from .models import IncidentRecord

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

    from .incidents import Incident

STATUSES = ("open", "proposed", "reviewed", "resolved", "dismissed")

STATUS_LABEL = {
    "open": "Offen",
    "proposed": "Aktion vorgeschlagen",
    "reviewed": "Geprüft",
    "resolved": "Erledigt",
    "dismissed": "Verworfen",
}
"""Deutsche Anzeige je Status -- fuer das Dashboard-Widget UND die Seite, damit beide
dieselben Woerter zeigen."""

STATUS_TONE = {
    "open": "danger",
    "proposed": "warn",
    "reviewed": "good",
    "resolved": "good",
    "dismissed": "neutral",
}
"""Farbe je Status, EXPLIZIT statt ueber den `| tone`-Filter: der erkennt Farben an
Stichworten im Text ("critical", "warning" ...) -- ein deutsches Badge-Wort wuerde
dort anders oder gar nicht greifen. Text und Farbe sind damit entkoppelt."""


def _ts(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def occurrences_of(details: dict[str, Any] | None) -> int:
    """Wie oft derselbe Vorfall erneut gemeldet wurde (1 = nur das erste Mal)."""
    value = (details or {}).get("occurrences")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 1 else 1


def record_out(r: IncidentRecord) -> dict[str, Any]:
    details = r.details if isinstance(r.details, dict) else {}
    last_seen = details.get("last_seen")
    return {
        "id": r.id,
        "host_id": r.host_id,
        "host_name": r.host_name,
        "target": r.target,
        # Aeltere Titel tragen noch die eingefrorene Docker-Zeit ("... 4 seconds ago"): beim
        # Anzeigen bereinigt, der gespeicherte Datensatz bleibt unveraendert.
        "message": clean_incident_message(r.message),
        "occurrences": occurrences_of(details),
        "last_seen": float(last_seen) if isinstance(last_seen, (int, float)) and not isinstance(last_seen, bool) else _ts(r.created_at),
        "created_at": _ts(r.created_at),
        "status": r.status,
        "status_label": STATUS_LABEL.get(r.status, r.status),
        "tone": STATUS_TONE.get(r.status, "neutral"),
        "status_changed_at": _ts(r.status_changed_at),
        "ai_summary": r.ai_summary,
        "action_id": r.action_id,
        "is_crash": r.is_crash,
    }


class IncidentRepository:
    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx

    async def save(self, incident: "Incident") -> None:
        """Einfuegen oder aktualisieren (ein Vorfall wird beim Batch-Abschluss
        gespeichert; derselbe Datensatz bekommt spaeter nur noch Statuswechsel)."""
        async with self._ctx.db.session() as session:
            row = await session.get(IncidentRecord, incident.id)
            if row is None:
                row = IncidentRecord(
                    id=incident.id,
                    created_at=datetime.fromtimestamp(incident.created_at, UTC),
                )
                session.add(row)
            row.host_id = incident.host_id
            row.host_name = incident.host_name
            row.target = incident.target
            row.message = incident.message
            row.is_crash = bool(incident.details.get("is_crash"))
            row.details = dict(incident.details)
            row.status = incident.status
            row.ai_summary = incident.ai_summary
            row.action_id = incident.action_id

    async def merge_into_active(
        self,
        *,
        host_name: str,
        target: str,
        is_crash: bool,
        seen_at: float,
        window_s: float,
        action_waiting: Callable[[str], Awaitable[bool]] | None = None,
    ) -> dict[str, Any] | None:
        """Gibt es zu diesem Container schon einen Vorfall, den noch niemand bearbeitet hat
        (offen, Aktion vorgeschlagen, oder von Nodvard KI automatisch auf "Geprueft" gesetzt),
        und liegt sein ERSTES Auftreten nicht laenger als `window_s` zurueck? Dann wird dort der
        Zaehler erhoeht und das neue Auftreten vermerkt, statt einen weiteren Vorfall
        anzulegen. Nach "Bestaetigen", "Erledigt" oder "Verwerfen" durch einen Menschen beginnt
        der naechste Absturz wieder einen neuen Vorfall.

        Das Fenster zaehlt ab dem ersten Auftreten, nicht ab dem letzten: sonst bliebe ein
        Container, der jede Nacht abstuerzt, nach der ersten Meldung fuer immer still.

        Ein Vorfall mit verknuepfter Aktion zaehlt nur mit, solange `action_waiting(action_id)`
        sagt, dass die Aktion noch wartet oder laeuft. Ist sie erledigt (etwa der Neustart lief
        schon, und der Container stuerzt trotzdem wieder ab), ist das ein neuer Vorfall.
        Gibt den Vorfall zurueck oder `None`."""
        unhandled = or_(
            IncidentRecord.status.in_(("open", "proposed")),
            and_(IncidentRecord.status == "reviewed", IncidentRecord.status_changed_at.is_(None)),
        )
        since = datetime.fromtimestamp(seen_at - window_s, UTC)
        async with self._ctx.db.session() as session:
            rows = (
                await session.execute(
                    select(IncidentRecord)
                    .where(
                        func.lower(IncidentRecord.host_name) == host_name.lower(),
                        func.lower(IncidentRecord.target) == target.lower(),
                        IncidentRecord.is_crash == is_crash,
                        IncidentRecord.created_at >= since,
                        unhandled,
                    )
                    .order_by(IncidentRecord.created_at.desc(), IncidentRecord.id.desc())
                )
            ).scalars().all()
            for row in rows:
                if row.action_id and (action_waiting is None or not await action_waiting(row.action_id)):
                    continue
                details = dict(row.details) if isinstance(row.details, dict) else {}
                last = details.get("last_seen")
                last_ts = float(last) if isinstance(last, (int, float)) and not isinstance(last, bool) else _ts(row.created_at)
                details["occurrences"] = occurrences_of(details) + 1
                details["last_seen"] = max(seen_at, last_ts or seen_at)
                row.details = details  # neu zuweisen, sonst merkt SQLAlchemy die JSON-Aenderung nicht
                return record_out(row)
        return None

    async def get(self, incident_id: str) -> dict[str, Any] | None:
        async with self._ctx.db.session() as session:
            row = await session.get(IncidentRecord, incident_id)
            return record_out(row) if row is not None else None

    async def set_status(self, incident_id: str, status: str) -> dict[str, Any] | None:
        async with self._ctx.db.session() as session:
            row = await session.get(IncidentRecord, incident_id)
            if row is None:
                return None
            previous = row.status
            row.status = status
            row.status_changed_at = datetime.now(UTC)
            out = record_out(row)
            out["previous_status"] = previous
            return out

    async def search(
        self,
        *,
        q: str | None = None,
        status: str | None = None,
        host: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        exclude_status: tuple[str, ...] = (),
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = []
        if q:
            needle = f"%{q.strip().lower()}%"
            conditions.append(
                or_(
                    func.lower(IncidentRecord.message).like(needle),
                    func.lower(IncidentRecord.target).like(needle),
                    func.lower(IncidentRecord.host_name).like(needle),
                    func.lower(func.coalesce(IncidentRecord.ai_summary, "")).like(needle),
                )
            )
        if status:
            conditions.append(IncidentRecord.status == status)
        if exclude_status:
            conditions.append(IncidentRecord.status.not_in(exclude_status))
        if host:
            conditions.append(func.lower(IncidentRecord.host_name) == host.lower())
        if since:
            conditions.append(IncidentRecord.created_at >= since)
        if until:
            conditions.append(IncidentRecord.created_at <= until)

        async with self._ctx.db.session() as session:
            total = (
                await session.execute(select(func.count()).select_from(IncidentRecord).where(*conditions))
            ).scalar_one()
            rows = (
                await session.execute(
                    select(IncidentRecord)
                    .where(*conditions)
                    .order_by(IncidentRecord.created_at.desc(), IncidentRecord.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars().all()
            return [record_out(r) for r in rows], int(total)

    async def count_by_status(self) -> dict[str, int]:
        async with self._ctx.db.session() as session:
            result = await session.execute(
                select(IncidentRecord.status, func.count()).group_by(IncidentRecord.status)
            )
            return {status: int(count) for status, count in result.all()}

    async def hosts(self) -> list[str]:
        """Alle Hostnamen, die je einen Vorfall hatten -- fuer den Host-Filter der Seite."""
        async with self._ctx.db.session() as session:
            result = await session.execute(
                select(IncidentRecord.host_name).distinct().order_by(IncidentRecord.host_name)
            )
            return [r[0] for r in result.all()]
