"""Liest die Antworten der qBittorrent-Web-API defensiv: jedes Feld darf fehlen, dann ist der Wert `None` oder 0."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .models import Torrent, TorrentCounts, Transfer

MAX_TEXT = 80  # längster Torrent-Name, den wir übernehmen
UNKNOWN_NAME = "Unbenannter Torrent"
MAX_ETA = 8_640_000  # Sekunden; so viele meldet qBittorrent für „unendlich“ (misc.js der WebUI)
MAX_TORRENTS = 1_000_000  # Obergrenze für die Zahl der Torrents, damit eine Zahl nie absurd wird

_VERSION = re.compile(r"v?[0-9][0-9A-Za-z.+_-]{0,38}")
_API_VERSION = re.compile(r"[0-9]{1,3}(\.[0-9]{1,5}){1,3}")
_COOKIE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_COOKIE_VALUE = re.compile(r"[A-Za-z0-9+/=_.~-]{8,256}")
_CONNECTIONS = {"connected", "firewalled", "disconnected"}

# Zustände aus `torrentStateToString` (serialize_torrent.cpp); ab Version 5 heißt pausiert „stopped“ statt „paused“.
_STATES = {
    "downloading": "downloading", "forcedDL": "downloading", "metaDL": "downloading", "forcedMetaDL": "downloading",
    "stalledDL": "stalled_downloading",
    "uploading": "uploading", "forcedUP": "uploading",
    "stalledUP": "stalled_uploading",
    "pausedDL": "paused", "pausedUP": "paused", "stoppedDL": "paused", "stoppedUP": "paused",
    "queuedDL": "queued", "queuedUP": "queued",
    "checkingDL": "checking", "checkingUP": "checking", "checkingResumeData": "checking",
    "moving": "moving",
    "error": "error", "missingFiles": "error",
}
_GROUPS = {
    "downloading": "downloading", "stalled_downloading": "downloading",
    "uploading": "seeding", "stalled_uploading": "seeding",
    "paused": "paused",
    "error": "error",
}


def clean_text(value: Any, limit: int = MAX_TEXT) -> str | None:
    """Kurzer Text ohne Steuerzeichen und Zeilenumbrüche; `None`, wenn es keiner ist oder er leer bleibt."""
    if not isinstance(value, str):
        return None
    text = "".join(" " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch for ch in value[: limit * 4])
    text = " ".join(text.split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _text(body: bytes) -> str:
    return body.decode("utf-8", errors="replace").strip()


def parse_version(body: bytes) -> str | None:
    """`GET /app/version` (Klartext wie `v5.1.4`); `None`, wenn es nicht nach einer Version aussieht."""
    text = _text(body)
    return text if _VERSION.fullmatch(text) else None


def parse_api_version(body: bytes) -> str | None:
    """`GET /app/webapiVersion` (Klartext wie `2.11.4`)."""
    text = _text(body)
    return text if _API_VERSION.fullmatch(text) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _whole(value: Any, high: int = 10**15) -> int | None:
    number = _number(value)
    if number is None or number < 0 or number > high:
        return None
    return int(number)


def classify_state(raw: Any) -> str:
    """Der Zustand eines Torrents als feste Gruppe (siehe `Torrent.state`); Unbekanntes ist `unknown`."""
    return _STATES.get(raw, "unknown") if isinstance(raw, str) else "unknown"


def parse_transfer(body: Any) -> Transfer | None:
    """`GET /transfer/info` -> Geschwindigkeit, Verbindung und Tempolimits; `None`, wenn es nicht nach qBittorrent aussieht."""
    # transfercontroller.cpp (infoAction): dl_info_speed, up_info_speed, dl_rate_limit, up_rate_limit, connection_status.
    if not isinstance(body, dict) or not any(key in body for key in ("dl_info_speed", "up_info_speed", "connection_status")):
        return None
    down_limit, up_limit = _whole(body.get("dl_rate_limit")) or None, _whole(body.get("up_rate_limit")) or None
    status = body.get("connection_status")
    return Transfer(
        download_speed=_whole(body.get("dl_info_speed")) or 0,
        upload_speed=_whole(body.get("up_info_speed")) or 0,
        connection=status if status in _CONNECTIONS else "unknown",
        download_limit=down_limit,
        upload_limit=up_limit,
        limited=down_limit is not None or up_limit is not None,
        alt_limits=None,
    )


def _eta(value: Any) -> int | None:
    seconds = _whole(value)
    return None if seconds is None or seconds >= MAX_ETA else seconds


def _progress(value: Any) -> int:
    fraction = _number(value)
    return 0 if fraction is None else max(0, min(100, int(fraction * 100)))


def parse_torrents(body: Any) -> list[Torrent] | None:
    """`GET /torrents/info` -> Torrents in der Reihenfolge des Servers; `None`, wenn es keine Liste ist."""
    # serialize_torrent.cpp: name, state, progress (0 bis 1), size (gewählte Dateien), dlspeed, upspeed, eta.
    if not isinstance(body, list):
        return None
    return [
        Torrent(
            name=clean_text(item.get("name")) or UNKNOWN_NAME,
            state=classify_state(item.get("state")),
            progress_percent=_progress(item.get("progress")),
            size=_whole(item.get("size")),
            download_speed=_whole(item.get("dlspeed")) or 0,
            upload_speed=_whole(item.get("upspeed")) or 0,
            eta_seconds=_eta(item.get("eta")),
        )
        for item in body
        if isinstance(item, dict)
    ]


@dataclass(frozen=True)
class MainData:
    counts: TorrentCounts
    free_space: int | None
    alt_limits: bool | None


def parse_maindata(body: Any) -> MainData | None:
    """`GET /sync/maindata?rid=0` -> Zahl je Zustand und freier Platz; `None`, wenn es keine vollständige Antwort ist."""
    # synccontroller.cpp: full_update, torrents (Hash -> Felder), server_state (free_space_on_disk, use_alt_speed_limits).
    if not isinstance(body, dict) or body.get("full_update") is not True:
        return None
    torrents = body.get("torrents", {})
    if not isinstance(torrents, dict) or len(torrents) > MAX_TORRENTS:
        return None
    groups = {"downloading": 0, "seeding": 0, "paused": 0, "error": 0, "other": 0}
    for entry in torrents.values():
        state = classify_state(entry.get("state")) if isinstance(entry, dict) else "unknown"
        groups[_GROUPS.get(state, "other")] += 1
    state_map = body.get("server_state")
    server = state_map if isinstance(state_map, dict) else {}
    free = _number(server.get("free_space_on_disk"))  # negativ, solange qBittorrent den Platz noch nicht geprüft hat
    alt = server.get("use_alt_speed_limits")
    return MainData(
        counts=TorrentCounts(total=len(torrents), **groups),
        free_space=None if free is None or free < 0 else int(free),
        alt_limits=alt if isinstance(alt, bool) else None,
    )


def session_cookie(lines: tuple[str, ...]) -> tuple[str, str] | None:
    """Name und Wert des Sitzungs-Cookies aus den `Set-Cookie`-Zeilen der Anmeldung; `None`, wenn keines gesetzt wurde."""
    # Der Name ist je Version `SID`, frei wählbar (4.6 bis 5.1) oder `QBT_SID_<Port>` (5.2); darum der erste Cookie mit Wert.
    for line in lines:
        pair = line.split(";", 1)[0]
        name, sep, value = pair.partition("=")
        name, value = name.strip(), value.strip()
        if sep and _COOKIE_NAME.fullmatch(name) and _COOKIE_VALUE.fullmatch(value):
            return name, value
    return None
