"""Liest die JSON-Antworten von Plex defensiv: jedes Feld darf fehlen, dann ist der Wert `None`."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .models import LatestItem, Library, Playback

MAX_TEXT = 80  # längster Name oder Titel, den wir von Plex übernehmen
UNKNOWN_TITLE = "Unbekannter Titel"

_VERSION = re.compile(r"[0-9][0-9A-Za-z .+_-]{0,38}")
_SECTION_KEY = re.compile(r"[0-9]{1,9}")
_KINDS = {
    "movie": "movie",
    "episode": "episode",
    "season": "season",
    "show": "show",
    "track": "audio",
    "album": "audio",
    "artist": "audio",
    "clip": "video",
}
_LIBRARY_KINDS = {"movie", "show", "artist", "photo"}
_DIRECT = {"copy", "directplay"}


def clean_text(value: Any, limit: int = MAX_TEXT) -> str | None:
    """Kurzer Text ohne Steuerzeichen und Zeilenumbrüche; `None`, wenn es keiner ist oder er leer bleibt."""
    if not isinstance(value, str):
        return None
    text = "".join(" " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch for ch in value[: limit * 4])
    text = " ".join(text.split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _version(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if _VERSION.fullmatch(text) else None


def _number(value: Any) -> float | None:
    # Plex schickt Zahlen als Zahlen; Zahlen in Anführungszeichen nehmen wir trotzdem an.
    if isinstance(value, str) and len(value) <= 20:
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _whole(value: Any, high: int = 100_000) -> int | None:
    number = _number(value)
    if number is None or number < 0 or number > high:
        return None
    return int(number)


def _flag(value: Any) -> bool:
    return value is True or value in (1, "1", "true")


def _word(value: Any) -> str | None:
    return value.strip().lower() if isinstance(value, str) and len(value) <= 40 else None


def _entries(body: Any) -> list[dict[str, Any]]:
    return [entry for entry in body if isinstance(entry, dict)] if isinstance(body, list) else []


def _child(item: dict[str, Any], name: str) -> dict[str, Any]:
    """Untergeordnetes Objekt wie `User` oder `Player`; Plex schickt es als Objekt, notfalls als Liste mit einem."""
    value = item.get(name)
    if isinstance(value, list):
        value = value[0] if value else None
    return value if isinstance(value, dict) else {}


def _container(body: Any) -> dict[str, Any] | None:
    """Das Objekt `MediaContainer`, in dem Plex jede JSON-Antwort verpackt."""
    container = body.get("MediaContainer") if isinstance(body, dict) else None
    return container if isinstance(container, dict) else None


def _list(container: dict[str, Any], name: str) -> list[Any] | None:
    """Plex lässt eine leere Liste ganz weg; `None` nur, wenn das Feld da, aber keine Liste ist."""
    value = container.get(name)
    if value is None:
        return []
    return value if isinstance(value, list) else None


@dataclass(frozen=True)
class ServerInfo:
    name: str | None
    version: str | None


def parse_server_info(body: Any) -> ServerInfo | None:
    """`GET /` -> Name und Version; `None`, wenn es nicht nach Plex aussieht."""
    # PlexServer._loadData (python-plexapi): friendlyName, machineIdentifier, version.
    container = _container(body)
    if container is None or not any(isinstance(container.get(key), str) for key in ("friendlyName", "version", "machineIdentifier")):
        return None
    return ServerInfo(name=clean_text(container.get("friendlyName")), version=_version(container.get("version")))


def _media(item: dict[str, Any]) -> dict[str, Any]:
    """Felder von Playback und LatestItem aus einem Eintrag unter `Metadata`."""
    # Video und Episode (python-plexapi): title, type, year, grandparentTitle (Serie), parentIndex (Staffel), index (Folge).
    # Bei Musik ist grandparentTitle der Künstler eines Titels, parentTitle der eines Albums.
    raw_kind = item.get("type")
    raw = raw_kind if isinstance(raw_kind, str) else ""
    kind = _KINDS.get(raw, "other")
    series = season = episode = artist = None
    if kind == "episode":
        series, season, episode = clean_text(item.get("grandparentTitle")), _whole(item.get("parentIndex")), _whole(item.get("index"))
    elif kind == "season":
        series, season = clean_text(item.get("parentTitle")), _whole(item.get("index"))
    elif raw == "track":
        artist = clean_text(item.get("grandparentTitle"))
    elif raw == "album":
        artist = clean_text(item.get("parentTitle"))
    return {
        "title": clean_text(item.get("title")) or UNKNOWN_TITLE,
        "kind": kind,
        "series": series,
        "season": season,
        "episode": episode,
        "artist": artist,
        "year": _whole(item.get("year"), 3000) if kind in ("movie", "video", "show") else None,
    }


def _play_method(transcode: dict[str, Any]) -> str | None:
    """Ohne `TranscodeSession` spielt der Client direkt ab; sonst entscheiden Bild- und Tonentscheidung."""
    # TranscodeSession (python-plexapi): videoDecision und audioDecision, z. B. transcode, copy, directplay.
    if not transcode:
        return "direct_play"
    decisions = [d for d in (_word(transcode.get("videoDecision")), _word(transcode.get("audioDecision"))) if d]
    if "transcode" in decisions:
        return "transcode"
    return "direct_stream" if decisions and all(d in _DIRECT for d in decisions) else None


def _progress(position: Any, total: Any) -> int | None:
    length = _number(total)
    # Plex lässt viewOffset am Anfang weg (python-plexapi setzt dann 0).
    pos = 0.0 if position is None else _number(position)
    if length is None or length <= 0 or pos is None or pos < 0:
        return None
    return max(0, min(100, int(pos * 100 / length)))


def parse_sessions(body: Any) -> list[Playback] | None:
    """`GET /status/sessions` -> die Wiedergaben, nach Benutzer und Titel sortiert; `None`, wenn es keine Liste ist."""
    # PlexSession (python-plexapi): User.title, Player.title/product/state, TranscodeSession; Länge und Stand: duration, viewOffset (ms).
    container = _container(body)
    items = _list(container, "Metadata") if container is not None else None
    if items is None:
        return None
    playbacks: list[Playback] = []
    for item in _entries(items):
        player = _child(item, "Player")
        method = _play_method(_child(item, "TranscodeSession"))
        playbacks.append(
            Playback(
                **_media(item),
                user=clean_text(_child(item, "User").get("title")),
                device=clean_text(player.get("title")) or clean_text(player.get("device")),
                client=clean_text(player.get("product")),
                play_method=method,
                transcoding=method == "transcode",
                progress_percent=_progress(item.get("viewOffset"), item.get("duration")),
                paused=_word(player.get("state")) == "paused",
            )
        )
    playbacks.sort(key=lambda p: ((p.user or "").casefold(), p.title.casefold()))
    return playbacks


@dataclass(frozen=True)
class Section:
    key: str | None  # nur Ziffern; alles andere kommt nie in eine Adresse
    title: str
    kind: str
    scanning: bool


def parse_sections(body: Any) -> list[Section] | None:
    """`GET /library/sections` -> Bibliotheken in der Reihenfolge von Plex; `None`, wenn es keine Liste ist."""
    # LibrarySection (python-plexapi): key, title, type (movie, show, artist, photo), refreshing.
    container = _container(body)
    items = _list(container, "Directory") if container is not None else None
    if items is None:
        return None
    sections: list[Section] = []
    for item in _entries(items):
        raw_key = item.get("key")
        key = str(raw_key) if isinstance(raw_key, (str, int)) and not isinstance(raw_key, bool) else ""
        raw_type = _word(item.get("type"))
        sections.append(
            Section(
                key=key if _SECTION_KEY.fullmatch(key) else None,
                title=clean_text(item.get("title")) or "Bibliothek",
                kind=raw_type if raw_type in _LIBRARY_KINDS else "other",
                scanning=_flag(item.get("refreshing")),
            )
        )
    return sections


def parse_total(body: Any) -> int | None:
    """Antwort einer Bibliothek mit Größe 0 -> Zahl der Einträge; `None`, wenn Plex sie nicht nennt."""
    # `totalSize` steht im MediaContainer (python-plexapi: LibrarySection.totalViewSize).
    container = _container(body)
    return _whole(container.get("totalSize"), 10**9) if container is not None else None


def library_of(section: Section, count: int | None) -> Library:
    return Library(title=section.title, kind=section.kind, count=count, scanning=section.scanning)


def _added(value: Any) -> datetime | None:
    seconds = _number(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def parse_latest(body: Any) -> list[LatestItem] | None:
    """`GET /library/recentlyAdded` -> neueste zuerst; `None`, wenn es keine Liste ist."""
    # addedAt steht als Unix-Zeit in Sekunden am Eintrag (python-plexapi: Video.addedAt).
    container = _container(body)
    items = _list(container, "Metadata") if container is not None else None
    if items is None:
        return None
    latest = [LatestItem(**_media(item), added_at=_added(item.get("addedAt"))) for item in _entries(items)]
    latest.sort(key=lambda i: i.added_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return latest
