"""Merkt sich je Skript, welche Server übersprungen wurden, damit ein nächtlicher Zeitplan die Liste nur einmal meldet."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import ClassVar

_log = logging.getLogger(__name__)

# Höchstzahl der Sendeversuche (z. B. ntfy nicht erreichbar). Jeder Versuch legt im Dashboard einen eigenen Eintrag an,
# ein dauerhaft kaputter Kanal soll nicht bei jedem Lauf einen neuen erzeugen.
MAX_SEND_TRIES = 3


class SkipNotices:
    """Stand als kleine JSON-Datei im Datenverzeichnis der Extension, außerhalb des Git-Repos (keine Skript-Inhalte)."""

    # Fehlversuche je Skript, nur im Speicher; nach einem Neustart zählt es von vorn.
    _failed: ClassVar[dict[tuple[Path, str], int]] = {}

    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> dict[str, dict[str, str]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        state: dict[str, dict[str, str]] = {}
        for script_id, hosts in raw.items():
            if isinstance(hosts, dict):
                state[str(script_id)] = {str(h): str(r) for h, r in hosts.items()}
        return state

    def _write(self, state: dict[str, dict[str, str]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)

    def update(self, script_id: str, current: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
        """Speichert `current` ({host_id: Grund}) als Stand; gibt (neu oder mit anderem Grund übersprungene Server,
        bisheriger Stand) zurück."""
        state = self._read()
        previous = state.get(script_id, {})
        if previous == current:
            return {}, previous
        new = {host_id: reason for host_id, reason in current.items() if previous.get(host_id) != reason}
        if current:
            state[script_id] = dict(current)
        else:
            state.pop(script_id, None)
        try:
            self._write(state)
        except OSError:
            # Lieber keine Meldung als jede Nacht dieselbe.
            _log.warning("Skript-Hinweise: Stand konnte nicht gespeichert werden -- keine Meldung.", exc_info=True)
            return {}, previous
        return new, previous

    def restore(self, script_id: str, previous: dict[str, str]) -> None:
        """Stellt den Stand von vor `update()` wieder her, damit eine nicht gesendete Meldung nicht still verloren geht."""
        state = self._read()
        if previous:
            state[script_id] = dict(previous)
        else:
            state.pop(script_id, None)
        try:
            self._write(state)
        except OSError:
            _log.warning("Skript-Hinweise: Stand konnte nicht zurückgesetzt werden.", exc_info=True)

    def send_failed(self, script_id: str, previous: dict[str, str]) -> bool:
        """True, wenn noch ein Versuch bleibt (Stand zurückgesetzt, der nächste Lauf meldet erneut); sonst False und der
        neue Stand gilt."""
        key = (self._path, script_id)
        tries = self._failed.get(key, 0) + 1
        if tries >= MAX_SEND_TRIES:
            self._failed.pop(key, None)
            return False
        self._failed[key] = tries
        self.restore(script_id, previous)
        return True

    def send_done(self, script_id: str) -> None:
        """Die Meldung ist raus; die Fehlversuche zählen nicht weiter."""
        self._failed.pop((self._path, script_id), None)

    def forget(self, script_id: str) -> None:
        self._failed.pop((self._path, script_id), None)
        state = self._read()
        if script_id not in state:
            return
        del state[script_id]
        try:
            self._write(state)
        except OSError:
            _log.warning("Skript-Hinweise: Stand konnte nicht bereinigt werden.", exc_info=True)
