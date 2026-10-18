"""Zeilen für die Kachel „Uptime Kuma“: fertige deutsche Texte, der Ton ein fester Wert (good/warn/danger/neutral)."""

from __future__ import annotations

from typing import Any

from .config import SETTINGS_HINT
from .models import Connection, Overview

MAX_DOWN_ROWS = 30  # mehr passt nicht sinnvoll in eine Kachel, der Rest steht in der Schlusszeile

_PROBLEM = {
    "auth_failed": ("Anmeldung fehlgeschlagen", "danger"),
    "unreachable": ("nicht erreichbar", "danger"),
    "error": ("Fehler", "warn"),
}


def _row(title: str, subtitle: str, label: str, tone: str) -> dict[str, Any]:
    return {"title": title, "subtitle": subtitle, "label": label, "tone": tone}


def _problem_row(connection: Connection) -> dict[str, Any]:
    if connection.state == "not_configured":
        return _row(
            f"{connection.name}: nicht eingerichtet",
            connection.message or SETTINGS_HINT,
            "nicht eingerichtet",
            "neutral",
        )
    label, tone = _PROBLEM.get(connection.state, _PROBLEM["error"])
    return _row(f"{connection.name}: {label}", connection.message or "", label, tone)


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _calm_row(connection: Connection) -> dict[str, Any]:
    """Die Zeile für eine Verbindung ohne ausgefallenen Monitor."""
    s = connection.summary
    if s is None:
        return _problem_row(connection)
    if s.total == 0:
        return _row("Keine Monitore", f"{connection.name}: In Uptime Kuma ist noch kein Monitor angelegt.", "–", "neutral")
    if s.up == s.total:
        title = "Der Monitor läuft" if s.total == 1 else f"Alle {s.total} Monitore laufen"
        return _row(title, connection.name, "in Ordnung", "good")
    notes = []
    if s.pending:
        notes.append(f"{s.pending} ausstehend")
    if s.maintenance:
        notes.append(f"{s.maintenance} in Wartung")
    unknown = s.total - s.up - s.pending - s.maintenance - s.down
    if unknown:
        notes.append(f"{unknown} unbekannt")
    return _row(
        f"{s.up} von {_plural(s.total, 'Monitor', 'Monitoren')} laufen",
        f"{connection.name}: {', '.join(notes)}",
        f"{s.up}/{s.total}",
        "warn" if s.pending or unknown else "neutral",
    )


def nothing_set_up(overview: Overview) -> bool:
    """Wahr, wenn keine einzige Verbindung eingerichtet ist (keine eingetragen oder alle ohne Zugangsdaten).
    Eine Verbindung mit Fehler oder ohne Antwort zählt als eingerichtet; dann bleibt die Kachel mit dem Fehler sichtbar."""
    return all(c.state == "not_configured" for c in overview.connections)


def status_rows(overview: Overview) -> list[dict[str, Any]]:
    if not overview.connections:
        return [_row("Uptime Kuma nicht eingerichtet", SETTINGS_HINT, "nicht eingerichtet", "neutral")]
    down: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for connection in overview.connections:
        for monitor in connection.monitors:
            if monitor.status == "down":
                where = monitor.url or monitor.type or ""
                down.append(_row(monitor.name, f"{connection.name} · {where}" if where else connection.name, "ausgefallen", "danger"))
        if connection.state != "ok":
            rest.append(_problem_row(connection))
        if connection.summary is not None and connection.summary.down == 0:
            rest.append(_calm_row(connection))
    if len(down) > MAX_DOWN_ROWS:
        extra = len(down) - MAX_DOWN_ROWS
        down = down[:MAX_DOWN_ROWS]
        down.append(_row(f"… und {extra} weitere ausgefallen", "Alle stehen auf der Seite „Uptime Kuma“.", "ausgefallen", "danger"))
    return down + rest
