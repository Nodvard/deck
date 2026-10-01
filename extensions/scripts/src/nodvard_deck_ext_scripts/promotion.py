"""KI "befoerdert" einen wiederkehrenden Fix zu einem Skript-Entwurf
(docs/02-EXTENSION-API.md Paragraph 6, letzte Zeile): "`ctx.events.subscribe(
'action.executed')` in der scripts-Extension; Vorschlag als Entwurf im Repo. Die beiden
Extensions reden ueber den Event-Bus, nicht miteinander."

Setzt voraus, dass `core.gate.execute_action()` ein `action.executed`-Event publiziert
(siehe `test_core_gate.py::test_execute_action_publishes_action_executed_event`).

Schwelle `MAX_COUNT=3` -- dieselbe Konvention wie die Flap-Erkennung (`core/flap.py`,
WP-5), fuer Konsistenz wiederverwendet statt neu erfunden.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

MAX_COUNT = 3

_PROMOTABLE_ACTION_TYPES = frozenset({"shell.exec"})
"""Nur `shell.exec` (z. B. von nexus-soc oder direkt aus dem Terminal vorgeschlagen)
wird beobachtet. Eigene `script.run`-Ausfuehrungen NIE -- sonst wuerde ein bereits
befoerdertes, wiederkehrend laufendes Skript sich selbst immer wieder neu vorschlagen."""


def fingerprint(action_type: str, host_id: str | None, command: str) -> str:
    return f"{action_type}:{host_id or '-'}:{command}"


@dataclass
class RecurringFixTracker:
    """Reiner Prozessspeicher (wie nexus-socs `IncidentStore`, WP-9) -- ein Neustart
    faengt bewusst bei null an. Ein Reboot ist selten genug, dass das erneute Zaehlen
    von drei echten Wiederholungen kein reales Problem ist; ein Zaehlstand, der einen
    Neustart unbegrenzt ueberlebt, waere unnoetige Komplexitaet hier."""

    _counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _promoted: set[str] = field(default_factory=set)

    def observe(self, *, action_type: str, host_id: str | None, outcome: str, payload: dict[str, Any]) -> str | None:
        """Gibt den fingerprint zurueck, GENAU WENN er in diesem Aufruf die Schwelle
        erreicht -- der Aufrufer soll dann (und nur dann) einen Entwurf anlegen.
        Kein erneutes Zurueckgeben fuer denselben fingerprint danach, sonst wuerde jede
        weitere Wiederholung einen weiteren, ueberfluessigen Entwurf erzeugen."""
        if action_type not in _PROMOTABLE_ACTION_TYPES or outcome != "success":
            return None
        command = payload.get("command")
        if not isinstance(command, str) or not command.strip():
            return None

        fp = fingerprint(action_type, host_id, command)
        self._counts[fp] += 1
        if self._counts[fp] == MAX_COUNT and fp not in self._promoted:
            self._promoted.add(fp)
            return fp
        return None
