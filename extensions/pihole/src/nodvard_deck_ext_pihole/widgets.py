"""Zeilen der Kachel „Pi-hole“ (`ListView`: `{title, subtitle, label, tone}`), fertig formuliert, weil Widget-Vorlagen
keine Bedingungen kennen."""

from __future__ import annotations

import time
from typing import Any

from .config import SETTINGS_HINT

SERVICE = "Pi-hole"


def number(value: int | None) -> str:
    return "–" if value is None else f"{value:,}".replace(",", ".")


def percent(value: float | None) -> str:
    return "–" if value is None else f"{value:.1f}".replace(".", ",") + " %"


def minutes_left(seconds: int | None) -> str:
    if not seconds:
        return ""
    minutes = max(1, round(seconds / 60))
    return f"noch {minutes} Min."


def ago(epoch: int | None, *, now: float | None = None) -> str:
    if not epoch:
        return "unbekannt"
    seconds = (now if now is not None else time.time()) - epoch
    if seconds < 3600:
        return "vor weniger als einer Stunde"
    if seconds < 86400:
        hours = round(seconds / 3600)
        return f"vor {hours} Stunde{'n' if hours != 1 else ''}"
    days = round(seconds / 86400)
    return f"vor {days} Tag{'en' if days != 1 else ''}"


def problem_row(status: dict[str, Any]) -> dict[str, Any]:
    state = status.get("state")
    if state == "not_configured":
        return {"title": f"{SERVICE} nicht eingerichtet", "subtitle": status.get("message") or SETTINGS_HINT, "label": "nicht eingerichtet", "tone": "neutral"}
    if state == "auth_failed":
        return {"title": f"{SERVICE}: Anmeldung fehlgeschlagen", "subtitle": status.get("message") or "", "label": "Anmeldung fehlgeschlagen", "tone": "danger"}
    if state == "unsupported":
        return {"title": f"{SERVICE} wird nicht unterstützt", "subtitle": status.get("message") or "", "label": "veraltet", "tone": "warn"}
    return {"title": f"{SERVICE} nicht erreichbar", "subtitle": status.get("message") or "", "label": "nicht erreichbar", "tone": "danger"}


def widget_state(status: dict[str, Any]) -> str:
    """`meta.state` der Kachel: `not_configured`, wenn nichts eingetragen ist (die Übersicht blendet sie dann aus).
    Eine eingetragene, aber unbrauchbare Einstellung ist ein Fehler und bleibt sichtbar."""
    return "error" if status.get("invalid") else str(status["state"])


def status_rows(status: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
    if status.get("state") != "ok":
        return [problem_row(status)]
    summary = status.get("summary") or {}
    blocking = status.get("blocking") or {}
    if blocking.get("enabled") is True:
        block_row = {"title": "Blockierung", "subtitle": "Werbung und Tracker werden blockiert.", "label": "aktiv", "tone": "good"}
    elif blocking.get("enabled") is False:
        rest = minutes_left(blocking.get("timer_s"))
        block_row = {
            "title": "Blockierung",
            "subtitle": f"Pausiert – {rest}" if rest else "Ausgeschaltet – Werbung und Tracker kommen durch.",
            "label": "pausiert" if rest else "aus",
            "tone": "warn" if rest else "danger",
        }
    else:
        block_row = {"title": "Blockierung", "subtitle": "Pi-hole meldet keinen eindeutigen Zustand.", "label": "unklar", "tone": "warn"}
    rows = [
        block_row,
        {
            "title": "Anfragen (letzte 24 Std.)",
            "subtitle": f"{number(summary.get('queries_total'))} Anfragen, davon {number(summary.get('queries_blocked'))} blockiert",
            "label": f"{percent(summary.get('percent_blocked'))} blockiert",
            "tone": "neutral",
        },
    ]
    if summary.get("domains_blocked") is not None:
        rows.append({
            "title": "Sperrliste",
            "subtitle": f"{number(summary.get('domains_blocked'))} Domains · aktualisiert {ago(summary.get('gravity_updated_at'), now=now)}",
            "label": number(summary.get("domains_blocked")),
            "tone": "neutral",
        })
    clients = status.get("top_clients") or []
    if clients:
        rows.append({
            "title": "Aktivste Geräte",
            "subtitle": ", ".join(f"{c['name']} ({number(c['count'])})" for c in clients[:3]),
            "label": f"{len(clients)} Geräte" if len(clients) != 1 else "1 Gerät",
            "tone": "neutral",
        })
    return rows
