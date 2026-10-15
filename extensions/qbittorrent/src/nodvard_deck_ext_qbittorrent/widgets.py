"""Zeilen für die Kachel „qBittorrent“: fertige deutsche Texte, der Ton ein fester Wert (good/warn/danger/neutral)."""

from __future__ import annotations

import math
from typing import Any

from .config import SETTINGS_HINT
from .models import Connection, Overview, Torrent

MAX_ROWS = 10  # mehr passt nicht in eine Kachel, der Rest steht in einer Schlusszeile

_PROBLEM = {
    "auth_failed": ("Anmeldung fehlgeschlagen", "danger"),
    "unreachable": ("nicht erreichbar", "danger"),
    "error": ("Fehler", "warn"),
}
# Zustand eines Torrents -> (Text, Ton)
_STATES = {
    "downloading": ("lädt", "good"),
    "stalled_downloading": ("wartet auf Quellen", "neutral"),
    "uploading": ("verteilt", "good"),
    "stalled_uploading": ("verteilt", "neutral"),
    "paused": ("pausiert", "neutral"),
    "queued": ("wartet", "neutral"),
    "checking": ("prüft", "neutral"),
    "moving": ("verschiebt", "neutral"),
    "error": ("Fehler", "danger"),
    "unknown": ("unbekannt", "neutral"),
}
_UNITS = ("B", "KB", "MB", "GB", "TB")


def _row(title: str, subtitle: str, label: str, tone: str) -> dict[str, Any]:
    return {"title": title, "subtitle": subtitle, "label": label, "tone": tone}


def size_text(count: int) -> str:
    """„1,5 GB“: 1024er-Schritte, unter 10 eine Nachkommastelle (mit Komma), Byte ohne."""
    value, unit = float(count), 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024
        unit += 1
    digits = 1 if unit > 0 and value < 10 else 0
    rounded = math.floor(value * 10**digits + 0.5) / 10**digits  # kaufmännisch, wie die Seite
    return f"{rounded:.{digits}f}".replace(".", ",") + f" {_UNITS[unit]}"


def speed_text(count: int) -> str:
    return f"{size_text(count)}/s"


def duration_text(seconds: int) -> str:
    """„5 Min.“, „1 Std. 12 Min.“ oder „2 Tage 3 Std.“; unter einer Minute „< 1 Min.“."""
    minutes = seconds // 60
    if minutes < 1:
        return "< 1 Min."
    if minutes < 60:
        return f"{minutes} Min."
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} Std. {minutes} Min." if minutes else f"{hours} Std."
    days, hours = divmod(hours, 24)
    day_text = "1 Tag" if days == 1 else f"{days} Tage"
    return f"{day_text} {hours} Std." if hours else day_text


def _problem_row(connection: Connection) -> dict[str, Any]:
    if connection.state == "not_configured":
        return _row(f"{connection.name}: nicht eingerichtet", connection.message or SETTINGS_HINT, "nicht eingerichtet", "neutral")
    label, tone = _PROBLEM.get(connection.state, _PROBLEM["error"])
    return _row(f"{connection.name}: {label}", connection.message or "", label, tone)


def _torrent_row(torrent: Torrent, connection: Connection, many: bool) -> dict[str, Any]:
    label, tone = _STATES.get(torrent.state, _STATES["unknown"])
    speeds = []
    if torrent.download_speed:
        speeds.append(f"↓ {speed_text(torrent.download_speed)}")
    if torrent.upload_speed:
        speeds.append(f"↑ {speed_text(torrent.upload_speed)}")
    eta = f"noch {duration_text(torrent.eta_seconds)}" if torrent.eta_seconds is not None and torrent.state == "downloading" else ""
    size = size_text(torrent.size) if torrent.size is not None else ""
    parts = (f"{torrent.progress_percent} %", *speeds, eta, size, connection.name if many else "")
    return _row(torrent.name, " · ".join(p for p in parts if p), label, tone)


def _counts_text(connection: Connection) -> str:
    counts = connection.counts
    if counts is None:
        return ""
    if counts.total == 0:
        return "keine Torrents"
    parts = (
        (counts.downloading, "laden"), (counts.seeding, "verteilen"), (counts.paused, "pausiert"),
        (counts.error, "mit Fehler"), (counts.other, "warten oder prüfen"),
    )
    return ", ".join(f"{count} {word}" for count, word in parts if count)


def _summary_row(connection: Connection) -> dict[str, Any]:
    title = f"qBittorrent {connection.version}" if connection.version else "qBittorrent"
    transfer = connection.transfer
    speeds = f"↓ {speed_text(transfer.download_speed)} · ↑ {speed_text(transfer.upload_speed)}" if transfer else "Geschwindigkeit nicht lesbar"
    notes = []
    if transfer is not None:
        if transfer.connection == "firewalled":
            notes.append("Firewall")
        elif transfer.connection == "disconnected":
            notes.append("nicht verbunden")
        if transfer.limited:
            notes.append("Tempolimit")
    free = f"{size_text(connection.free_space)} frei" if connection.free_space is not None else ""
    subtitle = " · ".join(p for p in (connection.name, speeds, _counts_text(connection), free, *notes, *connection.problems) if p)
    errors = connection.counts.error if connection.counts else 0
    moving = transfer is not None and (transfer.download_speed > 0 or transfer.upload_speed > 0)
    if connection.problems:
        return _row(title, subtitle, "unvollständig", "warn")
    if transfer is not None and transfer.connection == "disconnected":
        return _row(title, subtitle, "getrennt", "danger")
    if errors:
        return _row(title, subtitle, f"{errors} Fehler", "warn")
    if moving:
        return _row(title, subtitle, "aktiv", "good")
    return _row(title, subtitle, "ruhig", "neutral")


def status_rows(overview: Overview) -> list[dict[str, Any]]:
    if not overview.connections:
        return [_row("qBittorrent nicht eingerichtet", SETTINGS_HINT, "nicht eingerichtet", "neutral")]
    many = len(overview.connections) > 1
    summaries = [_summary_row(c) if c.state == "ok" else _problem_row(c) for c in overview.connections]
    if len(summaries) > MAX_ROWS:
        rest = len(summaries) - (MAX_ROWS - 1)
        return [*summaries[: MAX_ROWS - 1], _row(f"… und {rest} weitere Zeilen", "Alles steht auf der Seite „qBittorrent“.", "–", "neutral")]
    torrents = [_torrent_row(t, c, many) for c in overview.connections for t in c.active or []]
    budget = MAX_ROWS - len(summaries)
    if len(torrents) <= budget:
        return [*summaries, *torrents]
    if budget == 0:
        return summaries
    # Es passen nicht alle: eine Zeile bleibt für den Hinweis auf den Rest.
    keep = budget - 1
    hidden = len(torrents) - keep
    unsure = any(c.active_more for c in overview.connections)
    title = f"… und mehr als {hidden} weitere aktive Torrents" if unsure else f"… und {hidden} weitere aktive Torrents"
    return [*summaries, *torrents[:keep], _row(title, "Alle stehen auf der Seite „qBittorrent“.", "–", "neutral")]
