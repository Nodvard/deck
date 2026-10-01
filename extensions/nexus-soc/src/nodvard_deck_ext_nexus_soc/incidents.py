"""Vorfall-Warteschlange (laufender Batch + Cooldowns) -- ersetzt
`incident_batch_queue`/`INCIDENT_COOLDOWNS`/`queue_incident()` aus dem Skript des
Vorgaengersystems.

**Vorfalls-Historie:** die abgeschlossenen Vorfaelle liegen inzwischen dauerhaft in
`ext_nexus_soc_incidents` (models.py/history.py) -- der Alembic-Branch-Mechanismus pro
Extension, der beim ersten Bau dieser Datei noch fehlte, existiert seit inventory.
Hier bleibt nur, was die naechsten Minuten beschreibt: der offene Batch und die
Cooldowns (wie das globale Dict des Vorgaengersystems). Der offene Batch ist zusaetzlich in
`ext_nexus_soc_incident_queue` abgesichert (incident_queue.py): ein Neustart im
Sammelfenster nimmt ihn beim Start wieder auf (`restore()`); die Cooldowns sind nach
einem Neustart leer, dafuer setzt `restore()` sie fuer die wieder aufgenommenen Vorfaelle neu.

**Behobener Fehler:** das Vorgaengersystem startete den Batch-Timer bei JEDEM neuen
Ereignis neu (`incident_batch_timer.cancel()` + Neustart) -- unter
Dauerlast ueber viele verschiedene (Host, Ziel)-Paare wird der Flush unbegrenzt
verzoegert (Verhungern). Hier startet der Timer nur beim ERSTEN Ereignis eines
leeren Batches (siehe `enqueue()`s Rueckgabewert und dessen Nutzung in `__init__.py`).
"""

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
    def __init__(self) -> None:
        self._pending: list[Incident] = []
        self._cooldowns: dict[tuple[str, str], float] = {}

    def is_in_cooldown(self, host_key: str, target: str, *, cooldown_s: float) -> bool:
        key = (host_key.lower(), target.lower())
        last = self._cooldowns.get(key)
        return last is not None and (time.time() - last) < cooldown_s

    def enqueue(self, incident: Incident) -> bool:
        """Merkt sich den Cooldown, haengt an die Warteschlange an. Gibt `True`
        zurueck, wenn dies das ERSTE Ereignis eines zuvor leeren Batches ist -- nur
        dann soll der Aufrufer einen neuen Flush-Timer starten (Fund G)."""
        key = (incident.host_name.lower(), incident.target.lower())
        self._cooldowns[key] = time.time()
        is_first = len(self._pending) == 0
        self._pending.append(incident)
        return is_first

    def restore(self, incidents: list[Incident]) -> bool:
        """Nimmt beim Start die noch offenen Vorfaelle wieder auf (Cooldown inklusive,
        damit derselbe Container nicht gleich nochmal gemeldet wird). Gibt `True`
        zurueck, wenn danach ein Flush-Timer noetig ist."""
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
