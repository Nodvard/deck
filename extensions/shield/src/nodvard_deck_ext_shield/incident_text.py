"""Titel und Erklärtexte für Container-Vorfälle, aus dem Docker-Status und ohne relative Zeitangabe."""

from __future__ import annotations

import re

# `docker ps` liefert z. B. "Exited (137) 4 seconds ago". Die Zeitangabe stimmt nur in der Sekunde der Abfrage und
# stünde im gespeicherten Titel tagelang falsch da; die Oberfläche zeigt die Zeit aus dem Zeitstempel des Vorfalls.
_UNIT = r"(?:second|sec|minute|min|hour|hr|day|week|month|year)s?"
# "4 seconds ago", "About an hour ago", "Less than a second ago", "2s ago", "3 days ago"
_RELATIVE_AGO_RE = re.compile(
    rf"\s*(?:about\s+|less\s+than\s+|over\s+|almost\s+)?(?:an?|\d+)\s*{_UNIT}\s+ago\b",
    re.IGNORECASE,
)
# "Up 3 minutes", "Up About an hour", "Up 2 days (healthy)" -> nur "Up"
_UP_DURATION_RE = re.compile(
    rf"^(Up)\s+(?:about\s+|less\s+than\s+|over\s+|almost\s+)?(?:an?|\d+)\s*{_UNIT}\b", re.IGNORECASE
)
_EXIT_CODE_RE = re.compile(r"\b(?:exited|restarting)\s*\(\s*(-?\d+)\s*\)", re.IGNORECASE)


def strip_relative_time(status: str) -> str:
    """"Exited (137) 4 seconds ago" -> "Exited (137)"; "Up 3 hours (Paused)" -> "Up (Paused)"."""
    text = _RELATIVE_AGO_RE.sub("", status or "")
    text = _UP_DURATION_RE.sub(r"\1", text.strip())
    return re.sub(r"\s{2,}", " ", text).strip()


def exit_code_from_status(status: str) -> int | None:
    match = _EXIT_CODE_RE.search(status or "")
    return int(match.group(1)) if match else None


def _with_code(base: str, status: str) -> str:
    code = exit_code_from_status(status)
    return f"{base} (Exit-Code {code})" if code is not None else base


def describe_transition(*, kind: str, state: str, status: str) -> str:
    """Titel eines Container-Vorfalls. `kind`: "crash", "paused" oder "stopped"."""
    state = (state or "").lower()
    if kind == "paused":
        return "Container pausiert"
    if kind == "stopped":
        return _with_code("Container manuell gestoppt", status)
    if state == "restarting":
        return _with_code("Container startet immer wieder neu", status)
    if state == "dead":
        return "Container abgestürzt (Docker-Zustand: dead)"
    return _with_code("Container abgestürzt", status)


CAUSE_TEXTS: dict[str, dict[str, str]] = {
    "shield.cause.paused": {
        "what": "Der Container ist angehalten (pausiert), nicht abgestürzt. Er belegt weiter Speicher, arbeitet aber nicht.",
        "todo": "War das nicht gewollt, auf dem Server mit `docker unpause <Name>` fortsetzen.",
    },
    "shield.cause.unknown": {
        "what": "Warum der Container beendet wurde, ließ sich nicht feststellen: Der Server hat die Angaben dazu nicht geliefert.",
        "todo": "Die letzten Zeilen im Protokoll des Containers ansehen (`docker logs <Name>`). Läuft er nicht mehr, neu starten.",
    },
    "shield.cause.oom": {
        "what": "Der Arbeitsspeicher hat nicht gereicht, deshalb hat Linux den Container beendet.",
        "todo": "Nachsehen, ob der Server dauerhaft knapp an Speicher ist, und die Speichergrenze des Containers erhöhen (falls "
                "eine gesetzt ist) oder andere Dienste entlasten. Ein Neustart hilft nur kurz, solange der Speicher knapp bleibt.",
    },
    "shield.cause.stopped": {
        "what": "Der Container wurde sauber beendet: absichtlich gestoppt (von Hand, durch ein Update, beim Herunterfahren) "
                "oder das Programm hat seine Aufgabe erledigt.",
        "todo": "Nichts zu tun, wenn das geplant war. Sonst den Container wieder starten.",
    },
    "shield.cause.crash_signal": {
        "what": "Das Programm im Container ist selbst abgestürzt, zum Beispiel durch einen Speicherzugriffsfehler. Bei Exit-Code "
                "132 fehlt oft ein Befehlssatz der CPU, etwa in einer VM mit einfachem CPU-Typ.",
        "todo": "Die letzten Zeilen im Protokoll des Containers ansehen. Passiert es öfter, liegt es meist am Image: ein Update "
                "oder die vorige Version probieren.",
    },
    "shield.cause.killed": {
        "what": "Der Container wurde von außen beendet, nicht wegen Speichermangel: zum Beispiel mit `docker kill`, oder mit "
                "`docker stop`, wenn das Programm nicht rechtzeitig von selbst endete.",
        "todo": "Nachsehen, wer oder was ihn beendet hat (ein Skript, ein Update, ein anderes Programm). Ein Neustart ist möglich; "
                "Nodvard Deck stuft ihn trotzdem hoch ein, weil der Container absichtlich beendet worden sein kann.",
    },
    "shield.cause.loop": {
        "what": "Der Container beendet sich immer wieder, und Docker startet ihn jedes Mal neu.",
        "todo": "Die letzten Zeilen im Protokoll (`docker logs <Name>`) zeigen den Grund; oft fehlt eine Einstellung oder eine "
                "Datenbank ist nicht erreichbar. Ein Neustart von Hand hilft hier nicht.",
    },
    "shield.cause.crash": {
        "what": "Das Programm im Container hat sich mit einem Fehler beendet.",
        "todo": "Die letzten Zeilen im Protokoll des Containers (`docker logs <Name>`) zeigen meist den Grund, etwa eine fehlende "
                "Einstellung, ein falsches Passwort oder eine Datenbank, die nicht erreichbar ist.",
    },
}
"""Je Art der festen Einordnung (`remediation.Cause.key`) zwei feste Sätze: "Was heißt das?" und "Was kannst du tun?".
Sie stimmen auch ohne Nodvard KI; nur hier stehen sie (eine englische Oberfläche übersetzt diese Sammlung)."""


def cause_texts(key: str) -> dict[str, str] | None:
    """Die beiden Sätze zu einer Einordnung oder None (unbekannte Art)."""
    return CAUSE_TEXTS.get(f"shield.cause.{key}") if key else None


def describe_restart_loop(restarts: int) -> str:
    return f"Container abgestürzt (Absturzschleife: {restarts}x neu gestartet seit der letzten Prüfung)"


_OLD_TITLE_RE = re.compile(r"^Container (?P<kind>CRASH|MANUELLER STOP|PAUSIERT)(?: \((?P<rest>.*)\))?$", re.DOTALL)
_OLD_LOOP_RE = re.compile(r"^Absturzschleife: (?P<n>\d+)x neu gestartet seit der letzten Prüfung(?:, .*)?$", re.DOTALL)
_OLD_STATE_BY_STATUS = (
    ("restarting", "restarting"),
    ("dead", "dead"),
)


def clean_incident_message(message: str) -> str:
    """Bringt einen gespeicherten Titel in die heutige Form ohne relative Zeit (nur in der Ausgabe)."""
    text = message or ""
    match = _OLD_TITLE_RE.match(text.strip())
    if match is None:
        return strip_relative_time(text) if _RELATIVE_AGO_RE.search(text) else text
    kind, rest = match.group("kind"), (match.group("rest") or "")
    if kind == "PAUSIERT":
        return describe_transition(kind="paused", state="paused", status=rest)
    if kind == "MANUELLER STOP":
        return describe_transition(kind="stopped", state="exited", status=rest)
    loop = _OLD_LOOP_RE.match(rest)
    if loop is not None:
        return describe_restart_loop(int(loop.group("n")))
    lowered = rest.lower()
    state = next((s for prefix, s in _OLD_STATE_BY_STATUS if lowered.startswith(prefix)), "exited")
    return describe_transition(kind="crash", state=state, status=rest)
