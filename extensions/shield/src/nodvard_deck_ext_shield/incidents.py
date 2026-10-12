"""Offener Vorfall-Batch des laufenden Sammelfensters und die Cooldowns je Host und Ziel, im Speicher."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Incident:
    id: str
    host_id: str
    host_name: str
    target: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    status: str = "open"  # open | proposed | resolved | dismissed
    ai_summary: str | None = None
    action_id: str | None = None
    rejection_reason: str | None = None
    attempts: int = 0  # fehlgeschlagene Verarbeitungsversuche (incident_queue.py)
    proposal_started: bool = False  # Vorschlag wurde begonnen (siehe models.IncidentQueueRecord)
    resumed: bool = False  # nach einem Neustart aus der dauerhaften Warteschlange geholt


class IncidentStore:
    """Den Batch sichert `incident_queue.py` über einen Neustart (`restore()`); Erledigtes liegt in `history.py`."""

    def __init__(self) -> None:
        self._pending: list[Incident] = []
        self._cooldowns: dict[tuple[str, str], float] = {}

    def is_in_cooldown(self, host_key: str, target: str, *, cooldown_s: float) -> bool:
        key = (host_key.lower(), target.lower())
        last = self._cooldowns.get(key)
        return last is not None and (time.time() - last) < cooldown_s

    def merge_pending(self, host_name: str, target: str, *, is_crash: bool, seen_at: float) -> Incident | None:
        """Steht derselbe Container (gleicher Host und Name, gleiche Art) schon im offenen Batch?
        Dann zählt dort das neue Auftreten mit, es entsteht kein zweiter Vorfall."""
        key = (host_name.lower(), target.lower())
        for incident in self._pending:
            if (incident.host_name.lower(), incident.target.lower()) == key and bool(
                incident.details.get("is_crash")
            ) == is_crash:
                incident.details["occurrences"] = int(incident.details.get("occurrences") or 1) + 1
                incident.details["last_seen"] = seen_at
                return incident
        return None

    def enqueue(self, incident: Incident) -> bool:
        """Merkt den Cooldown und hängt an. `True`, wenn der Batch vorher leer war: Nur dann startet der Aufrufer
        den Flush-Timer; bei jedem Ereignis neu gestartet, käme der Flush unter Dauerlast nie."""
        key = (incident.host_name.lower(), incident.target.lower())
        self._cooldowns[key] = time.time()
        is_first = len(self._pending) == 0
        self._pending.append(incident)
        return is_first

    def restore(self, incidents: list[Incident]) -> bool:
        """Nimmt beim Start die noch offenen Vorfälle wieder auf, samt Cooldown, damit derselbe Container nicht gleich
        nochmal gemeldet wird. `True`, wenn danach ein Flush-Timer nötig ist."""
        is_first = len(self._pending) == 0
        for incident in incidents:
            self._cooldowns[(incident.host_name.lower(), incident.target.lower())] = time.time()
            self._pending.append(incident)
        return bool(incidents) and is_first

    def oldest_created_at(self) -> float | None:
        return min((i.created_at for i in self._pending), default=None)

    def take_batch(self) -> list[Incident]:
        batch = self._pending
        self._pending = []
        return batch

    def has_pending(self) -> bool:
        return bool(self._pending)

    def pending_count(self) -> int:
        return len(self._pending)


def new_incident_id() -> str:
    return uuid.uuid4().hex
