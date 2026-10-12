"""Merkt sich je Skript den zuletzt gemeldeten Fehler, damit ein Zeitplan, der immer wieder scheitert, nur einmal
meldet."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_log = logging.getLogger(__name__)


class FailureNotices:
    """Stand als kleine JSON-Datei im Datenverzeichnis der Extension (nur Fehlertexte, keine Skript-Inhalte)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> dict[str, str]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(script_id): str(problem) for script_id, problem in raw.items() if isinstance(problem, str)}

    def _write(self, state: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)

    def claim(self, script_id: str, problem: str) -> bool:
        """True, wenn der Fehler gemeldet werden soll (neu oder anders als der zuletzt gemeldete); der Stand ist dann
        gespeichert."""
        state = self._read()
        if state.get(script_id) == problem:
            return False
        state[script_id] = problem
        try:
            self._write(state)
        except OSError:
            # Lieber still bleiben als bei jedem Lauf melden; der Fehlschlag steht ohnehin im Verlauf des Zeitplans.
            _log.warning("Skript-Fehlermeldungen: Stand konnte nicht gespeichert werden -- keine Meldung.", exc_info=True)
            return False
        return True

    def forget(self, script_id: str) -> None:
        """Nach einem erfolgreichen Lauf oder einem gelöschten Skript: der nächste Fehler wird wieder gemeldet."""
        state = self._read()
        if state.pop(script_id, None) is None:
            return
        try:
            self._write(state)
        except OSError:
            _log.warning("Skript-Fehlermeldungen: Stand konnte nicht gespeichert werden.", exc_info=True)
