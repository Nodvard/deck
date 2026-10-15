"""Daten für die Kachel "Zertifikate": `ListView`-Zeilen `{title, subtitle, label, tone}`.

Die Texte entstehen hier fertig (Widget-Templates kennen keine Bedingungen); `tone`: `good`, `warn`, `danger`, `neutral`.
"""

from __future__ import annotations

from typing import Any

from .client import WARN_DAYS, parse_datetime

SETTINGS_HINT = "Unter Einstellungen → Erweiterungen → Nginx Proxy Manager eintragen."


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
