"""Liest die Antworten der Jellyfin-API defensiv: jedes Feld darf fehlen, dann ist der Wert `None`."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .models import LatestItem, LibraryCounts, Playback

MAX_TEXT = 80  # längster Name oder Titel, den wir von Jellyfin übernehmen
UNKNOWN_TITLE = "Unbekannter Titel"

_VERSION = re.compile(r"[0-9][0-9A-Za-z .+_-]{0,38}")
_KINDS = {
    "Movie": "movie",
    "Episode": "episode",
    "Audio": "audio",
    "AudioBook": "audio",
    "MusicVideo": "video",
    "Video": "video",
    "Trailer": "video",
}
_METHODS = {"Transcode": "transcode", "DirectStream": "direct_stream", "DirectPlay": "direct_play"}


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
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _whole(value: Any, high: int = 100_000) -> int | None:
    number = _number(value)
    if number is None or number < 0 or number > high:
        return None
    return int(number)


def _entries(body: Any) -> list[dict[str, Any]]:
    return [entry for entry in body if isinstance(entry, dict)] if isinstance(body, list) else []


@dataclass(frozen=True)
class ServerInfo:
    name: str | None
    version: str | None


def parse_system_info(body: Any) -> ServerInfo | None:
    """`GET /System/Info` -> Name und Version; `None`, wenn es nicht nach Jellyfin aussieht."""
    # SystemInfo (MediaBrowser.Model/System): ServerName, Version, Id.
    if not isinstance(body, dict) or not any(isinstance(body.get(key), str) for key in ("ServerName", "Version", "Id")):
        return None
    return ServerInfo(name=clean_text(body.get("ServerName")), version=_version(body.get("Version")))


def _media(item: dict[str, Any]) -> dict[str, Any]:
    """Felder von Playback und LatestItem aus einem `BaseItemDto`."""
    # BaseItemDto: Name, Type, SeriesName, ParentIndexNumber (Staffel), IndexNumber (Folge), Artists, ProductionYear.
    raw_kind = item.get("Type")
    kind = _KINDS.get(raw_kind, "other") if isinstance(raw_kind, str) else "other"
    episode = kind == "episode"
    artists = item.get("Artists")
    artist = clean_text(artists[0]) if kind == "audio" and isinstance(artists, list) and artists else None
    return {
        "title": clean_text(item.get("Name")) or UNKNOWN_TITLE,
        "kind": kind,
        "series": clean_text(item.get("SeriesName")) if episode else None,
        "season": _whole(item.get("ParentIndexNumber")) if episode else None,
        "episode": _whole(item.get("IndexNumber")) if episode else None,
        "artist": artist or (clean_text(item.get("AlbumArtist")) if kind == "audio" else None),
        "year": _whole(item.get("ProductionYear"), 3000) if kind in ("movie", "video") else None,
    }


def _play_method(play_state: dict[str, Any], info: Any) -> str | None:
    """Was Jellyfin tatsächlich tut: Meldet es Umrechnung, gilt sie, auch wenn der Client „DirectStream“ sagt."""
    # PlayerStateInfo.PlayMethod (Transcode, DirectStream, DirectPlay) und TranscodingInfo.IsVideoDirect/IsAudioDirect.
    raw = play_state.get("PlayMethod")
    method = _METHODS.get(raw) if isinstance(raw, str) else None
    if method in ("transcode", "direct_play"):
        return method
    if isinstance(info, dict):
        return "direct_stream" if info.get("IsVideoDirect") is True and info.get("IsAudioDirect") is True else "transcode"
    return method


def _progress(position: Any, total: Any) -> int | None:
    pos, length = _number(position), _number(total)
    if pos is None or length is None or pos < 0 or length <= 0:
        return None
    return max(0, min(100, int(pos * 100 / length)))


def parse_sessions(body: Any) -> list[Playback] | None:
    """`GET /Sessions` -> die Sitzungen mit laufender Wiedergabe, nach Benutzer und Titel sortiert; `None`, wenn es keine Liste ist."""
    # SessionInfoDto: UserName, Client, DeviceName, NowPlayingItem, PlayState (PositionTicks, IsPaused), TranscodingInfo.
    if not isinstance(body, list):
        return None
    playbacks: list[Playback] = []
    for session in _entries(body):
        item = session.get("NowPlayingItem")
        if not isinstance(item, dict):
            continue
        state = session.get("PlayState")
        play_state: dict[str, Any] = state if isinstance(state, dict) else {}
        method = _play_method(play_state, session.get("TranscodingInfo"))
        playbacks.append(
            Playback(
                **_media(item),
                user=clean_text(session.get("UserName")),
                device=clean_text(session.get("DeviceName")),
                client=clean_text(session.get("Client")),
                play_method=method,
                transcoding=method == "transcode",
                progress_percent=_progress(play_state.get("PositionTicks"), item.get("RunTimeTicks")),
                paused=play_state.get("IsPaused") is True,
            )
        )
    playbacks.sort(key=lambda p: ((p.user or "").casefold(), p.title.casefold()))
    return playbacks


def parse_counts(body: Any) -> LibraryCounts | None:
    """`GET /Items/Counts` -> Anzahl je Art; `None`, wenn es kein Objekt ist."""
    # ItemCounts (MediaBrowser.Model/Dto): MovieCount, SeriesCount, EpisodeCount, AlbumCount, SongCount, ...
    if not isinstance(body, dict):
        return None
    return LibraryCounts(
        movies=_whole(body.get("MovieCount"), 10**9),
        series=_whole(body.get("SeriesCount"), 10**9),
        episodes=_whole(body.get("EpisodeCount"), 10**9),
        albums=_whole(body.get("AlbumCount"), 10**9),
        songs=_whole(body.get("SongCount"), 10**9),
        music_videos=_whole(body.get("MusicVideoCount"), 10**9),
        box_sets=_whole(body.get("BoxSetCount"), 10**9),
        books=_whole(body.get("BookCount"), 10**9),
    )


def _created(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value[:40])
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def parse_latest(body: Any) -> list[LatestItem] | None:
    """`GET /Items` (neueste zuerst) -> Liste in der Reihenfolge des Servers; `None`, wenn `Items` fehlt."""
    # QueryResult<BaseItemDto>: Items; BaseItemDto.DateCreated (Fields=DateCreated).
    items = body.get("Items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return None
    return [LatestItem(**_media(item), added_at=_created(item.get("DateCreated"))) for item in _entries(items)]
