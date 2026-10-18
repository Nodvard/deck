"""Zeilen für die Kachel „Home Assistant“: fertige deutsche Texte, der Ton ein fester Wert (good/warn/danger/neutral)."""

from __future__ import annotations

import re
from typing import Any

from .config import SETTINGS_HINT
from .models import Connection, EntityValue, Overview

MAX_ROWS = 10  # mehr passt nicht in eine Kachel, der Rest steht in einer Schlusszeile
MAX_NAMES = 2  # Namen von Updates und nicht verfügbaren Entitäten je Zeile

_PROBLEM = {
    "auth_failed": ("Anmeldung fehlgeschlagen", "danger"),
    "unreachable": ("nicht erreichbar", "danger"),
    "error": ("Fehler", "warn"),
}
_DECIMAL = re.compile(r"-?\d+\.\d+")


def _row(title: str, subtitle: str, label: str, tone: str) -> dict[str, Any]:
    return {"title": title, "subtitle": subtitle, "label": label, "tone": tone}


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _names(names: list[str], total: int) -> str:
    """„A, B und 3 weitere“; `total` zählt auch die gekürzten Namen mit."""
    shown = names[:MAX_NAMES]
    text = ", ".join(shown)
    rest = total - len(shown)
    return f"{text} und {_plural(rest, 'weiteres', 'weitere')}" if rest > 0 else text


def value_text(state: str | None, unit: str | None) -> str:
    """21.5 und °C werden zu „21,5 °C“ (deutsche Schreibweise)."""
    if state is None:
        return "–"
    text = state.replace(".", ",") if _DECIMAL.fullmatch(state) else state
    return f"{text} {unit}" if unit else text


def _problem_row(connection: Connection) -> dict[str, Any]:
    if connection.state == "not_configured":
        return _row(
            f"{connection.name}: nicht eingerichtet", connection.message or SETTINGS_HINT, "nicht eingerichtet", "neutral"
        )
    label, tone = _PROBLEM.get(connection.state, _PROBLEM["error"])
    return _row(f"{connection.name}: {label}", connection.message or "", label, tone)


def _where(connection: Connection) -> list[str]:
    parts = [connection.name]
    place = connection.location_name
    if place and place.casefold() != connection.name.casefold():
        parts.append(place)
    return parts


def _version_row(connection: Connection) -> dict[str, Any]:
    counts = connection.counts
    updates = counts.updates if counts is not None else 0
    title = f"Home Assistant {connection.version}" if connection.version else "Home Assistant"
    if updates == 0:
        return _row(title, " · ".join([*_where(connection), "alles aktuell"]), "aktuell", "good")
    news = f"{_plural(updates, 'Update', 'Updates')}: {_names([u.name for u in connection.updates], updates)}"
    return _row(title, " · ".join([*_where(connection), news]), _plural(updates, "Update", "Updates"), "warn")


def _unavailable_row(connection: Connection) -> dict[str, Any]:
    counts = connection.counts
    gone = counts.unavailable if counts is not None else 0
    unknown = counts.unknown if counts is not None else 0
    unknown_note = f"{unknown} mit Zustand „unbekannt“" if unknown else ""
    if gone == 0:
        return _row("Nicht verfügbar", " · ".join(p for p in (connection.name, "keine", unknown_note) if p), "keine", "good")
    names = _names([u.name for u in connection.unavailable], gone)
    return _row("Nicht verfügbar", " · ".join(p for p in (connection.name, names, unknown_note) if p), str(gone), "warn")


def _entity_row(connection: Connection, entity: EntityValue) -> dict[str, Any]:
    subtitle = f"{connection.name} · {entity.entity_id}"
    if entity.status == "unavailable":
        return _row(entity.name, subtitle, "nicht verfügbar", "warn")
    if entity.status == "unknown":
        return _row(entity.name, subtitle, "unbekannt", "neutral")
    if entity.status == "missing":
        return _row(entity.entity_id, f"{connection.name} · gibt es in Home Assistant nicht", "nicht gefunden", "warn")
    return _row(entity.name, subtitle, value_text(entity.state, entity.unit), "neutral")


def nothing_set_up(overview: Overview) -> bool:
    """Wahr, wenn keine einzige Verbindung eingerichtet ist (keine eingetragen oder alle ohne Zugangsdaten).
    Eine Verbindung mit Fehler oder ohne Antwort zählt als eingerichtet; dann bleibt die Kachel mit dem Fehler sichtbar."""
    return all(c.state == "not_configured" for c in overview.connections)


def status_rows(overview: Overview) -> list[dict[str, Any]]:
    if not overview.connections:
        return [_row("Home Assistant nicht eingerichtet", SETTINGS_HINT, "nicht eingerichtet", "neutral")]
    core: list[list[dict[str, Any]]] = []
    for connection in overview.connections:
        if connection.state == "ok":
            core.append([_version_row(connection), _unavailable_row(connection)])
        else:
            core.append([_problem_row(connection)])
    fixed = sum(len(rows) for rows in core)
    if fixed > MAX_ROWS:
        flat = [row for rows in core for row in rows]
        extra = len(flat) - (MAX_ROWS - 1)
        return [*flat[: MAX_ROWS - 1], _row(f"… und {extra} weitere Zeilen", "Alles steht auf der Seite „Home Assistant“.", "–", "neutral")]
    free = MAX_ROWS - fixed
    wanted = [
        [_entity_row(c, e) for e in c.entities] if c.state == "ok" else []
        for c in overview.connections
    ]
    total_wanted = sum(len(rows) for rows in wanted)
    # Passen nicht alle gewählten Werte, bleibt eine Zeile für den Hinweis auf den Rest.
    budget = free if total_wanted <= free else max(free - 1, 0)
    out: list[dict[str, Any]] = []
    shown = 0
    for rows, entity_rows in zip(core, wanted, strict=True):
        out.extend(rows)
        allowed = entity_rows[: budget - shown]
        out.extend(allowed)
        shown += len(allowed)
    if total_wanted > shown and free > 0:
        out.append(_row(f"… und {total_wanted - shown} weitere Werte", "Alle stehen auf der Seite „Home Assistant“.", "–", "neutral"))
    return out
