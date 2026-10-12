"""Erkennt Befehle, die dreimal erfolgreich liefen, und schlägt dafür einen Skript-Entwurf vor."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .params import escape_dollars

# Gleiche Schwelle wie die Flap-Erkennung (`core/flap.py`).
MAX_COUNT = 3

# Eigene `script.run`-Läufe zählen nie, sonst schlüge sich ein bereits übernommenes Skript immer wieder selbst vor.
_PROMOTABLE_ACTION_TYPES = frozenset({"shell.exec"})


def fingerprint(action_type: str, host_id: str | None, command: str) -> str:
    return f"{action_type}:{host_id or '-'}:{command}"


def draft_content(command: str) -> str:
    """Inhalt des Entwurfs für einen übernommenen Befehl; jedes `$` steht als `$$`, damit er unverändert läuft."""
    return f"#!/bin/sh\n{escape_dollars(command)}\n"


@dataclass
class RecurringFixTracker:
    """Zählt im Arbeitsspeicher; nach einem Neustart beginnt das Zählen bewusst von vorn."""

    _counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _promoted: set[str] = field(default_factory=set)

    def observe(self, *, action_type: str, host_id: str | None, outcome: str, payload: dict[str, Any]) -> str | None:
        """Gibt den Fingerabdruck nur in dem Aufruf zurück, der die Schwelle erreicht; danach nie wieder (ein Entwurf je
        Befehl)."""
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
