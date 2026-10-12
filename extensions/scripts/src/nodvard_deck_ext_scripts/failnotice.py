"""Merkt sich je Skript den Fehler, der zuletzt gemeldet wurde. Ein Zeitplan, der zum Beispiel alle fuenf Minuten
scheitert, meldet sich so einmal und erst wieder, wenn sich der Fehler aendert oder das Skript zwischendurch
erfolgreich lief -- nicht bei jedem Lauf.

Der Stand liegt wie `skip-notices.json` als kleine JSON-Datei im Datenverzeichnis der Extension (nur Fehlertexte,
keine Skript-Inhalte). Kann sie nicht geschrieben werden, wird nicht gemeldet: Der Lauf steht ohnehin als
fehlgeschlagen im Verlauf des Zeitplans, und eine Meldung bei jedem Lauf waere schlimmer.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_log = logging.getLogger(__name__)


class FailureNotices:
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
        """True, wenn dieser Fehler gemeldet werden soll (er ist neu oder anders als der zuletzt gemeldete). Der neue
        Stand ist dann schon gespeichert; laesst er sich nicht speichern, ist die Antwort False."""
        state = self._read()
        if state.get(script_id) == problem:
            return False
        state[script_id] = problem
        try:
            self._write(state)
        except OSError:
            _log.warning("Skript-Fehlermeldungen: Stand konnte nicht gespeichert werden -- keine Meldung.", exc_info=True)
            return False
        return True

    def forget(self, script_id: str) -> None:
        """Nach einem erfolgreichen Lauf (oder wenn das Skript geloescht ist): der naechste Fehler meldet sich wieder."""
        state = self._read()
        if state.pop(script_id, None) is None:
            return
        try:
            self._write(state)
        except OSError:
            _log.warning("Skript-Fehlermeldungen: Stand konnte nicht gespeichert werden.", exc_info=True)
