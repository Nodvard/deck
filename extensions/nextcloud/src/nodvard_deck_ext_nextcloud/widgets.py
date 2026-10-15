"""Zeilen für die Kachel „Nextcloud“: fertige deutsche Texte, der Ton ein fester Wert (good/warn/danger/neutral)."""

from __future__ import annotations

from typing import Any

from .config import SETTINGS_HINT
from .models import ServerInfo

# Last je Kern in Prozent (5-Minuten-Wert): ab 100 % warten mehr Prozesse, als Kerne da sind.
LOAD_WARN_PERCENT = 100
LOAD_DANGER_PERCENT = 200
# Belegter Arbeitsspeicher in Prozent, ohne den Cache.
MEMORY_WARN_PERCENT = 90
MEMORY_DANGER_PERCENT = 97
# Freier Platz im Datenordner; die Gesamtgröße meldet die App nicht, darum feste Werte.
FREE_WARN_BYTES = 10 * 1024**3
FREE_DANGER_BYTES = 2 * 1024**3

_UNITS = ("KB", "MB", "GB", "TB")

_PROBLEM = {
    "auth_failed": ("keine Rechte", "warn", "Keine Rechte für die Kennzahlen"),
    "app_inactive": ("App aus", "warn", "Die App „Server-Info“ ist nicht aktiv"),
    "unreachable": ("nicht erreichbar", "danger", "Nextcloud nicht erreichbar"),
    "error": ("Fehler", "warn", "Kennzahlen nicht lesbar"),
}


def _row(title: str, subtitle: str, label: str, tone: str) -> dict[str, Any]:
    return {"title": title, "subtitle": subtitle, "label": label, "tone": tone}


def number(value: int) -> str:
    """12345 -> "12.345" (deutsche Schreibweise)."""
    return f"{value:,}".replace(",", ".")


def decimal(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def size(value: int) -> str:
    """Bytes -> "48,5 GB" (1024er-Schritte, wie die Nextcloud selbst)."""
    if value < 1024:
        return f"{value} Bytes"
    amount = float(value)
    index = -1
    while amount >= 1024 and index < len(_UNITS) - 1:
        amount /= 1024
        index += 1
    return f"{decimal(amount, 1 if amount < 100 else 0)} {_UNITS[index]}"


def _plural(count: int, one: str, many: str) -> str:
    return f"{number(count)} {one if count == 1 else many}"


def _update_row(info: ServerInfo) -> dict[str, Any]:
    title = f"Nextcloud {info.version}" if info.version else "Nextcloud"
    updates = info.updates
    if updates.core_available is True:
        news = f"Update auf Version {updates.core_version} verfügbar" if updates.core_version else "Update verfügbar"
        return _row(title, news, "Update", "warn")
    if updates.core_available is False:
        if updates.core_checked_at is None:
            return _row(title, "Die Nextcloud hat noch nie nach einer neuen Version gesucht", "–", "neutral")
        return _row(title, "Keine neue Version gemeldet", "aktuell", "good")
    return _row(title, "Ob ein Update bereitsteht, meldet diese Nextcloud nicht", "–", "neutral")


def _apps_row(info: ServerInfo) -> dict[str, Any]:
    count = info.updates.app_updates
    if count is None:
        return _row("Apps", "Ob App-Updates bereitstehen, meldet diese Nextcloud nicht", "–", "neutral")
    if count == 0:
        return _row("Apps", "Alle Apps sind aktuell", "aktuell", "good")
    return _row("Apps", f"{_plural(count, 'App-Update', 'App-Updates')} verfügbar", number(count), "warn")


def _free_row(info: ServerInfo) -> dict[str, Any] | None:
    free = info.storage.free_bytes
    if free is None:
        return None
    tone = "danger" if free < FREE_DANGER_BYTES else "warn" if free < FREE_WARN_BYTES else "good"
    return _row("Freier Speicher", "im Datenordner", size(free), tone)


def _content_row(info: ServerInfo) -> dict[str, Any] | None:
    files, users = info.storage.files, info.storage.users
    parts = []
    if files is not None:
        parts.append(_plural(files, "Datei", "Dateien"))
    if users is not None:
        parts.append(_plural(users, "Konto", "Konten"))
    if not parts:
        return None
    return _row("Dateien und Konten", " · ".join(parts), number(users) if users is not None else "–", "neutral")


def _active_row(info: ServerInfo) -> dict[str, Any] | None:
    a = info.active_users
    spans = ((a.last_5_minutes, "5 Min."), (a.last_hour, "1 Std."), (a.last_24_hours, "24 Std."))
    known = [(count, span) for count, span in spans if count is not None]
    if not known:
        return None
    return _row("Aktive Nutzer", " · ".join(f"{number(c)} in {span}" for c, span in known), number(known[0][0]), "neutral")


def _load_row(info: ServerInfo) -> dict[str, Any] | None:
    load = info.load
    if load.load_1 is None or load.load_5 is None or load.load_15 is None:
        return None
    values = " / ".join(decimal(v, 2) for v in (load.load_1, load.load_5, load.load_15))
    if not load.cpu_cores:
        return _row("Prozessor-Last", f"Last {values} (1 / 5 / 15 Min.)", decimal(load.load_5, 2), "neutral")
    percent = round(load.load_5 / load.cpu_cores * 100)
    tone = "danger" if percent >= LOAD_DANGER_PERCENT else "warn" if percent >= LOAD_WARN_PERCENT else "good"
    subtitle = f"Last {values} (1 / 5 / 15 Min.) bei {_plural(load.cpu_cores, 'Kern', 'Kernen')}"
    return _row("Prozessor-Last", subtitle, f"{percent} %", tone)


def _memory_row(info: ServerInfo) -> dict[str, Any] | None:
    total, available = info.load.memory_total_bytes, info.load.memory_available_bytes
    if not total or available is None:
        return None
    used = total - available
    percent = round(used / total * 100)
    tone = "danger" if percent >= MEMORY_DANGER_PERCENT else "warn" if percent >= MEMORY_WARN_PERCENT else "good"
    return _row("Arbeitsspeicher", f"{size(used)} von {size(total)} belegt", f"{percent} %", tone)


def status_rows(info: ServerInfo) -> list[dict[str, Any]]:
    if info.state == "not_configured":
        return [_row("Nextcloud nicht eingerichtet", info.message or SETTINGS_HINT, "nicht eingerichtet", "neutral")]
    if info.state != "ok":
        label, tone, title = _PROBLEM.get(info.state, _PROBLEM["error"])
        return [_row(title, info.message or "", label, tone)]
    rows = [
        _update_row(info),
        _apps_row(info),
        _free_row(info),
        _content_row(info),
        _active_row(info),
        _load_row(info),
        _memory_row(info),
    ]
    return [row for row in rows if row is not None]
