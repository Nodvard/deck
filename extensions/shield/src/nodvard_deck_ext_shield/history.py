"""Dauerhafte, durchsuchbare Vorfalls-Historie in der Tabelle `ext_nexus_soc_incidents` (models.py),
neueste zuerst, mit `limit`/`offset` und Gesamtzahl für die Seitennavigation."""

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
"""Deutsche Anzeige je Status, gemeinsam für Widget und Seite, damit beide dieselben Wörter zeigen."""

STATUS_TONE = {
    "open": "danger",
    "proposed": "warn",
    "reviewed": "good",
    "resolved": "good",
    "dismissed": "neutral",
}
"""Farbe je Status, ausdrücklich statt über den `| tone`-Filter: Der erkennt Farben an Stichworten im Text
("critical", "warning" ...), ein deutsches Badge-Wort griffe dort nicht."""


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
        # Ältere Titel tragen noch die eingefrorene Docker-Zeit ("... 4 seconds ago"): beim Anzeigen
        # bereinigt, der gespeicherte Datensatz bleibt unverändert.
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
        # Von selbst erledigt (der Container läuft wieder): der Satz dazu, etwa "läuft wieder seit 04:12".
        "auto_resolved": details.get("auto_resolved") if isinstance(details.get("auto_resolved"), str) else None,
    }


class IncidentRepository:
    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx

    async def save(self, incident: "Incident", *, status_changed: bool = False) -> None:
        """Einfügen oder aktualisieren. `status_changed`: Der Status wurde gerade gewechselt (etwa von selbst
        erledigt), der Zeitpunkt wird vermerkt."""
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
            if status_changed:
                row.status_changed_at = datetime.now(UTC)

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
        """Zählt ein weiteres Auftreten auf den noch unbearbeiteten Vorfall dieses Containers, statt einen neuen
        anzulegen. Gibt den Vorfall zurück oder `None`."""
        # Unbearbeitet: offen, Aktion vorgeschlagen oder von Nodvard KI automatisch auf "Geprüft" gesetzt. Nach
        # "Bestätigen", "Erledigt" oder "Verwerfen" durch einen Menschen beginnt der nächste Absturz einen
        # neuen Vorfall.
        unhandled = or_(
            IncidentRecord.status.in_(("open", "proposed")),
            and_(IncidentRecord.status == "reviewed", IncidentRecord.status_changed_at.is_(None)),
        )
        # Das Fenster zählt ab dem ersten Auftreten, nicht ab dem letzten: sonst bliebe ein Container, der jede Nacht
        # abstürzt, nach der ersten Meldung für immer still.
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
                # Mit verknüpfter Aktion zählt der Vorfall nur mit, solange sie noch wartet oder läuft; ist sie
                # erledigt (etwa der Neustart lief und der Container stürzt trotzdem ab), ist es ein neuer Vorfall.
                if row.action_id and (action_waiting is None or not await action_waiting(row.action_id)):
                    continue
                details = dict(row.details) if isinstance(row.details, dict) else {}
                last = details.get("last_seen")
                last_ts = float(last) if isinstance(last, (int, float)) and not isinstance(last, bool) else _ts(row.created_at)
                details["occurrences"] = occurrences_of(details) + 1
                details["last_seen"] = max(seen_at, last_ts or seen_at)
                row.details = details  # neu zuweisen, sonst merkt SQLAlchemy die JSON-Änderung nicht
                return record_out(row)
        return None

    async def resolve_recovered(
        self,
        *,
        host_name: str,
        target: str,
        note: str,
        waiting_note: str = "",
        action_waiting: Callable[[str], Awaitable[bool]] | None = None,
    ) -> list[dict[str, Any]]:
        """Der Container läuft wieder ruhig: setzt alle nicht erledigten Vorfälle dazu auf "Erledigt" und vermerkt
        `note` in den Details (`auto_resolved`). Gibt die Vorfälle mit `previous_status` und `action_waiting`
        zurück."""
        out: list[dict[str, Any]] = []
        async with self._ctx.db.session() as session:
            rows = (
                await session.execute(
                    select(IncidentRecord).where(
                        func.lower(IncidentRecord.host_name) == host_name.lower(),
                        func.lower(IncidentRecord.target) == target.lower(),
                        IncidentRecord.status.in_(("open", "proposed", "reviewed")),
                    )
                )
            ).scalars().all()
            for row in rows:
                waiting = bool(row.action_id) and action_waiting is not None and await action_waiting(row.action_id)
                previous = row.status
                # Unbearbeitete Vorfälle (wie in `merge_into_active`) merken sich den alten Status in
                # `auto_resolved_from` und gehen bei einem neuen Absturz wieder auf (`reopen_auto_resolved`), von einem
                # Menschen bestätigte nie.
                unhandled = previous in ("open", "proposed") or row.status_changed_at is None
                row.status = "resolved"
                row.status_changed_at = datetime.now(UTC)
                details = dict(row.details) if isinstance(row.details, dict) else {}
                # Wartet die Aktion noch auf Freigabe, hängt `waiting_note` an.
                details["auto_resolved"] = note + (f" {waiting_note}" if waiting and waiting_note else "")
                if unhandled:
                    details["auto_resolved_from"] = previous
                else:
                    details.pop("auto_resolved_from", None)
                row.details = details  # neu zuweisen, sonst merkt SQLAlchemy die JSON-Änderung nicht
                item = record_out(row)
                item["previous_status"] = previous
                item["action_waiting"] = waiting
                out.append(item)
        return out

    async def reopen_auto_resolved(
        self,
        *,
        host_name: str,
        target: str,
        is_crash: bool,
        seen_at: float,
        window_s: float,
        action_waiting: Callable[[str], Awaitable[bool]] | None = None,
    ) -> dict[str, Any] | None:
        """Öffnet den eben von selbst erledigten Vorfall wieder (alter Status aus `auto_resolved_from`) und zählt den
        neuen Absturz mit, statt ihn in der Ruhezeit zu verlieren. Gibt den Vorfall mit `previous_status` zurück
        oder `None`."""
        # Es gelten die Regeln von `merge_into_active`: nur Vorfälle, die vor dem Selbst-Erledigen unbearbeitet waren
        # (sonst fehlt `auto_resolved_from`), nie von Hand erledigte, und mit Aktion nur, solange sie noch wartet.
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
                    )
                    .order_by(IncidentRecord.created_at.desc(), IncidentRecord.id.desc())
                    .limit(1)
                )
            ).scalars().all()
            # Nur der jüngste Vorfall des Containers kommt in Frage: Ein älterer, selbst erledigter darf nicht wieder
            # aufgehen, wenn danach schon ein neuerer kam (etwa nach einem gelaufenen Neustart).
            for row in rows:
                details = dict(row.details) if isinstance(row.details, dict) else {}
                if row.status != "resolved" or not details.get("auto_resolved"):
                    return None
                if details.get("auto_resolved_from") not in ("open", "proposed", "reviewed"):
                    return None
                if row.action_id and (action_waiting is None or not await action_waiting(row.action_id)):
                    return None
                previous = details.pop("auto_resolved_from", None)
                details.pop("auto_resolved", None)
                details["occurrences"] = occurrences_of(details) + 1
                details["last_seen"] = seen_at
                row.details = details  # neu zuweisen, sonst merkt SQLAlchemy die JSON-Änderung nicht
                row.status = previous if previous in ("open", "proposed", "reviewed") else "open"
                row.status_changed_at = None
                item = record_out(row)
                item["previous_status"] = "resolved"
                return item
        return None

    async def recently_auto_resolved(self, *, host_name: str, target: str, since: float) -> bool:
        """Hat sich seit `since` (Unix-Zeit) ein Vorfall dieses Containers von selbst erledigt? Dann darf ein neuer
        Absturz nicht in der Ruhezeit verschwinden."""
        async with self._ctx.db.session() as session:
            rows = (
                await session.execute(
                    select(IncidentRecord)
                    .where(
                        func.lower(IncidentRecord.host_name) == host_name.lower(),
                        func.lower(IncidentRecord.target) == target.lower(),
                        IncidentRecord.status == "resolved",
                        IncidentRecord.status_changed_at >= datetime.fromtimestamp(since, UTC),
                    )
                    .order_by(IncidentRecord.status_changed_at.desc())
                    .limit(5)
                )
            ).scalars().all()
        return any(isinstance(row.details, dict) and row.details.get("auto_resolved") for row in rows)

    async def recent_action_ids(self, *, host_name: str, target: str, limit: int = 5) -> list[tuple[str, bool]]:
        """Aktionen der jüngsten Vorfälle dieses Containers (neueste zuerst, ohne Doppelte), je mit der Angabe, ob
        sich der Vorfall danach von selbst erledigt hat (der Container lief also wieder ruhig)."""
        async with self._ctx.db.session() as session:
            rows = (
                await session.execute(
                    select(IncidentRecord)
                    .where(
                        func.lower(IncidentRecord.host_name) == host_name.lower(),
                        func.lower(IncidentRecord.target) == target.lower(),
                        IncidentRecord.action_id.is_not(None),
                    )
                    .order_by(IncidentRecord.created_at.desc(), IncidentRecord.id.desc())
                    .limit(limit * 4)
                )
            ).scalars().all()
        out: list[tuple[str, bool]] = []
        seen: set[str] = set()
        for row in rows:
            if row.action_id in seen:
                continue
            seen.add(row.action_id)
            details = row.details if isinstance(row.details, dict) else {}
            out.append((row.action_id, bool(details.get("auto_resolved"))))
            if len(out) >= limit:
                break
        return out

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
            # Von Hand geändert: nie mehr von selbst wieder öffnen, und nach "Wieder öffnen" gilt der Satz
            # "von selbst erledigt" nicht mehr (auch nicht als Erholung für `recent_action_ids`).
            details = dict(row.details) if isinstance(row.details, dict) else {}
            dropped = [key for key in ("auto_resolved_from",) + (("auto_resolved",) if status != "resolved" else ()) if key in details]
            if dropped:
                for key in dropped:
                    details.pop(key)
                row.details = details  # neu zuweisen, sonst merkt SQLAlchemy die JSON-Änderung nicht
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
            # Der freie Text trifft auch die KI-Zusammenfassung: Man sucht eher nach "OOM" oder "restart" als nach dem
            # exakten Containernamen.
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
        """Alle Hostnamen, die je einen Vorfall hatten, für den Host-Filter der Seite."""
        async with self._ctx.db.session() as session:
            result = await session.execute(
                select(IncidentRecord.host_name).distinct().order_by(IncidentRecord.host_name)
            )
            return [r[0] for r in result.all()]
