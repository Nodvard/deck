"""Daten fuer die beiden Dashboard-Kacheln ("Pi-hole", "Zertifikate").

Beide sind `ListView`s mit Zeilen `{title, subtitle, label, tone}` -- die Texte
entstehen hier fertig auf Deutsch (die Widget-Templates kennen keine Bedingungen), die
Farbe kommt als expliziter Maschinenwert (`good`/`warn`/`danger`/`neutral`).
"Nicht eingerichtet" und "nicht erreichbar" sind ganz normale Zeilen, nie ein Fehler.
"""

from __future__ import annotations

import time
from typing import Any

from .npm import WARN_DAYS, parse_datetime

SETTINGS_HINT = "Unter Einstellungen → Erweiterungen → Netzwerk eintragen."


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


def date_text(iso: str | None) -> str:
    parsed = parse_datetime(iso) if iso else None
    return parsed.strftime("%d.%m.%Y") if parsed else "unbekannt"


def _problem_row(service: str, status: dict[str, Any]) -> dict[str, Any]:
    state = status.get("state")
    if state == "not_configured":
        return {"title": f"{service} nicht eingerichtet", "subtitle": status.get("message") or SETTINGS_HINT, "label": "nicht eingerichtet", "tone": "neutral"}
    if state == "auth_failed":
        return {"title": f"{service}: Anmeldung fehlgeschlagen", "subtitle": status.get("message") or "", "label": "Anmeldung fehlgeschlagen", "tone": "danger"}
    if state == "unsupported":
        return {"title": f"{service} wird nicht unterstützt", "subtitle": status.get("message") or "", "label": "veraltet", "tone": "warn"}
    return {"title": f"{service} nicht erreichbar", "subtitle": status.get("message") or "", "label": "nicht erreichbar", "tone": "danger"}


def pihole_rows(status: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
    if status.get("state") != "ok":
        return [_problem_row("Pi-hole", status)]
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
    return rows


def certificate_rows(status: dict[str, Any]) -> list[dict[str, Any]]:
    if status.get("state") != "ok":
        return [_problem_row("Nginx Proxy Manager", status)]
    certificates = status.get("certificates") or []
    if not certificates:
        return [{"title": "Keine Zertifikate", "subtitle": "Im Nginx Proxy Manager ist noch kein Zertifikat angelegt.", "label": "–", "tone": "neutral"}]
    tone = {"warn": "warn", "expired": "danger"}
    urgent = [c for c in certificates if c["status"] in tone]
    if not urgent:
        known = [c for c in certificates if c["expires_at"]]
        nxt = min(known, key=lambda c: c["expires_at"]) if known else None
        subtitle = f"{len(certificates)} Zertifikat{'e' if len(certificates) != 1 else ''}"
        if nxt:
            subtitle += f" · nächstes läuft am {date_text(nxt['expires_at'])} ab ({nxt['name']})"
        return [{"title": f"Alle Zertifikate gültig (mehr als {WARN_DAYS} Tage)", "subtitle": subtitle, "label": "in Ordnung", "tone": "good"}]
    rows = []
    for cert in urgent:
        when = date_text(cert["expires_at"])
        verb = "ist am" if cert["status"] == "expired" else "läuft am"
        rows.append({
            "title": cert["name"],
            "subtitle": f"{verb} {when} {'abgelaufen' if cert['status'] == 'expired' else 'ab'} ({cert['days_text']})",
            "label": cert["status_label"],
            "tone": tone[cert["status"]],
        })
    return rows
