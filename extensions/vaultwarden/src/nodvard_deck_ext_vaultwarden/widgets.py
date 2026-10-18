"""Zeilen der Kachel „Vaultwarden“ (`ListView`: `{title, subtitle, label, tone}`), fertig formuliert, weil
Widget-Vorlagen keine Bedingungen kennen."""

from __future__ import annotations

from typing import Any

from .config import SETTINGS_HINT


def widget_state(status: dict[str, Any]) -> str:
    """`meta.state` der Kachel: `not_configured`, wenn nichts eingetragen ist (die Übersicht blendet sie dann aus).
    Eine eingetragene, aber unbrauchbare Einstellung ist ein Fehler und bleibt sichtbar."""
    return "error" if status.get("invalid") else str(status["state"])


def status_rows(status: dict[str, Any]) -> list[dict[str, Any]]:
    state = status.get("state")
    if state == "not_configured":
        return [{"title": "Vaultwarden nicht eingerichtet", "subtitle": status.get("message") or SETTINGS_HINT,
                 "label": "nicht eingerichtet", "tone": "neutral"}]
    if state != "ok":
        return [{"title": "Vaultwarden nicht erreichbar", "subtitle": status.get("message") or "",
                 "label": "nicht erreichbar", "tone": "danger"}]
    version = status.get("version")
    rows = [{
        "title": "Vaultwarden",
        "subtitle": f"Läuft · Version {version}" if version else "Läuft",
        "label": "läuft", "tone": "good",
    }]
    if status.get("https"):
        rows.append({"title": "Verbindung", "subtitle": "Über HTTPS erreichbar.", "label": "HTTPS", "tone": "good"})
    else:
        rows.append({
            "title": "Verbindung",
            "subtitle": "Ohne HTTPS: Der Web-Tresor im Browser geht so nicht, die Apps warnen.",
            "label": "ohne HTTPS", "tone": "warn",
        })
    signups = status.get("signups_open")
    if signups is True:
        rows.append({
            "title": "Registrierung",
            "subtitle": "Jeder, der die Adresse kennt, kann ein Konto anlegen.",
            "label": "offen", "tone": "warn",
        })
    elif signups is False:
        rows.append({
            "title": "Registrierung", "subtitle": "Neue Konten nur über Einladung.", "label": "zu", "tone": "good",
        })
    return rows
