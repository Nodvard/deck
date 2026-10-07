"""Texte fuer Container-Vorfaelle: aus dem Docker-Status wird ein verstaendlicher deutscher
Titel OHNE relative Zeitangabe.

`docker ps` liefert z. B. "Exited (137) 4 seconds ago". Die Zeitangabe stimmt nur in der
Sekunde der Abfrage; im gespeicherten Titel stuende sie tagelang falsch da. Die Oberflaeche
zeigt die Zeit ohnehin aus dem Zeitstempel des Vorfalls. Darum steht im Titel nur, was
dauerhaft stimmt (Art des Ereignisses, Exit-Code).

`clean_incident_message()` raeumt zusaetzlich die Titel bereits gespeicherter Vorfaelle beim
Anzeigen auf (rein in der Ausgabe, die Datensaetze bleiben unveraendert).
"""

from __future__ import annotations

import re

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


def describe_restart_loop(restarts: int) -> str:
    return f"Container abgestürzt (Absturzschleife: {restarts}x neu gestartet seit der letzten Prüfung)"


_OLD_TITLE_RE = re.compile(r"^Container (?P<kind>CRASH|MANUELLER STOP|PAUSIERT)(?: \((?P<rest>.*)\))?$", re.DOTALL)
_OLD_LOOP_RE = re.compile(r"^Absturzschleife: (?P<n>\d+)x neu gestartet seit der letzten Prüfung(?:, .*)?$", re.DOTALL)
_OLD_STATE_BY_STATUS = (
    ("restarting", "restarting"),
    ("dead", "dead"),
)


def clean_incident_message(message: str) -> str:
    """Bringt einen gespeicherten (auch aelteren) Titel in die heutige Form, ohne relative Zeit."""
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
