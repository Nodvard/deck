"""Merkt sich je Skript, welche Server bei 'Alle Server'/'Gruppe' uebersprungen wurden
(Schritt 1) -- damit ein naechtlicher Zeitplan die Liste nur EINMAL meldet
und danach erst wieder, wenn sich die Liste aendert (Server dazugekommen oder anderer
Grund), nicht jede Nacht dieselbe Push-Nachricht.

Der Stand liegt als kleine JSON-Datei im Datenverzeichnis der Extension (ausserhalb des
Git-Repos `repo/`, es sind keine Skript-Inhalte). Kann sie nicht geschrieben werden,
liefert `update()` nichts Neues zurueck: dann lieber keine Meldung als jede Nacht eine.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import ClassVar

_log = logging.getLogger(__name__)

# So oft wird eine Meldung hoechstens versucht, wenn das Senden scheitert (z. B. ntfy nicht
# erreichbar). Jeder Versuch legt im Dashboard einen eigenen Eintrag an -- ein dauerhaft
# kaputter Kanal soll nicht bei jedem Zeitplan-Lauf einen weiteren erzeugen.
MAX_SEND_TRIES = 3


class SkipNotices:
    # Fehlversuche je Skript, nur im Speicher: nach einem Neustart zaehlt es von vorn,
    # das sind dann hoechstens ein paar Versuche mehr.
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
        """`current` = {host_id: Grund} der jetzt uebersprungenen Server. Speichert das
        als neuen Stand (wer nicht mehr uebersprungen wird, faellt raus) und gibt zurueck
        (NEUE oder mit anderem Grund uebersprungene Server, bisheriger Stand). Laesst sich
        der Stand nicht speichern, sind die neuen Server {} (dann wird nichts gemeldet).
        Mit dem bisherigen Stand kann der Aufrufer per `restore()` zurueck, wenn das
        Senden der Meldung scheitert -- sonst ginge sie still verloren."""
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
            _log.warning("Skript-Hinweise: Stand konnte nicht gespeichert werden -- keine Meldung.", exc_info=True)
            return {}, previous
        return new, previous

    def restore(self, script_id: str, previous: dict[str, str]) -> None:
        """Stellt den Stand von vor `update()` wieder her (Meldung nicht gesendet)."""
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
        """Das Senden der Meldung ist gescheitert. Noch Versuche uebrig: stellt den Stand von
        vor `update()` wieder her (der naechste Lauf meldet erneut) und gibt True zurueck.
        Sonst gibt es auf -- der neue Stand bleibt gemerkt, wie nach einer gesendeten
        Meldung -- und gibt False zurueck."""
        key = (self._path, script_id)
        tries = self._failed.get(key, 0) + 1
        if tries >= MAX_SEND_TRIES:
            self._failed.pop(key, None)
            return False
        self._failed[key] = tries
        self.restore(script_id, previous)
        return True

    def send_done(self, script_id: str) -> None:
        """Die Meldung ist raus: die Fehlversuche zaehlen nicht weiter."""
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
