"""Zeilen für die Kachel „Plex“: fertige deutsche Texte, der Ton ein fester Wert (good/warn/danger/neutral)."""

from __future__ import annotations

from typing import Any

from .config import SETTINGS_HINT
from .models import Connection, Media, Overview, Playback

MAX_ROWS = 10  # mehr passt nicht in eine Kachel, der Rest steht in einer Schlusszeile

_PROBLEM = {
    "auth_failed": ("Anmeldung fehlgeschlagen", "danger"),
    "unreachable": ("nicht erreichbar", "danger"),
    "error": ("Fehler", "warn"),
}
# (Art der Bibliothek, Einzahl, Mehrzahl): so viel steht in der Zusammenfassung.
_COUNT_LABELS = (("movie", "Film", "Filme"), ("show", "Serie", "Serien"), ("artist", "Künstler", "Künstler"))


def _row(title: str, subtitle: str, label: str, tone: str) -> dict[str, Any]:
    return {"title": title, "subtitle": subtitle, "label": label, "tone": tone}


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def number_text(count: int) -> str:
    """Tausenderpunkt wie im Deutschen: 1204 wird „1.204“."""
    return f"{count:,}".replace(",", ".")


def episode_code(season: int | None, episode: int | None) -> str:
    if season is not None and episode is not None:
        return f"S{season:02d}E{episode:02d}"
    if season is not None:
        return f"S{season:02d}"
    return f"E{episode:02d}" if episode is not None else ""


def media_title(media: Media) -> str:
    """„Serie S01E03 · Folge“, „Serie S02“, „Künstler – Titel“ oder „Film (2019)“."""
    if media.kind in ("episode", "season"):
        head = " ".join(p for p in (media.series, episode_code(media.season, media.episode)) if p)
        parts = (head,) if media.kind == "season" and head else (head, media.title)
        return " · ".join(p for p in parts if p)
    if media.kind == "audio" and media.artist:
        return f"{media.artist} – {media.title}"
    return f"{media.title} ({media.year})" if media.year else media.title


def _problem_row(connection: Connection) -> dict[str, Any]:
    if connection.state == "not_configured":
        return _row(f"{connection.name}: nicht eingerichtet", connection.message or SETTINGS_HINT, "nicht eingerichtet", "neutral")
    label, tone = _PROBLEM.get(connection.state, _PROBLEM["error"])
    return _row(f"{connection.name}: {label}", connection.message or "", label, tone)


def _playback_row(playback: Playback, connection: Connection, many: bool) -> dict[str, Any]:
    note = "transkodiert" if playback.paused and playback.transcoding else ""
    progress = f"{playback.progress_percent} %" if playback.progress_percent is not None else ""
    where = playback.device or playback.client or ""
    subtitle = " · ".join(p for p in (playback.user or "", where, progress, note, connection.name if many else "") if p)
    if playback.paused:
        label, tone = "pausiert", "neutral"
    elif playback.transcoding:
        label, tone = "transkodiert", "warn"
    else:
        label, tone = "läuft", "good"
    return _row(media_title(playback), subtitle, label, tone)


def _counts_text(connection: Connection) -> str:
    parts = []
    for kind, one, many in _COUNT_LABELS:
        counts = [lib.count for lib in connection.libraries or [] if lib.kind == kind and lib.count is not None]
        if sum(counts):
            parts.append(f"{number_text(sum(counts))} {one if sum(counts) == 1 else many}")
    return ", ".join(parts)


def _playing_text(connection: Connection) -> str:
    if connection.playback_count is None:
        return "Wiedergaben nicht lesbar"
    if connection.playback_count == 0:
        return "nichts läuft"
    text = _plural(connection.playback_count, "Wiedergabe", "Wiedergaben")
    transcoding = sum(1 for p in connection.playbacks or [] if p.transcoding)
    return f"{text} ({transcoding} transkodiert)" if transcoding else text


def _summary_row(connection: Connection) -> dict[str, Any]:
    title = f"Plex {connection.version}" if connection.version else "Plex"
    place = connection.server_name if connection.server_name and connection.server_name.casefold() != connection.name.casefold() else ""
    subtitle = " · ".join(p for p in (connection.name, place, _playing_text(connection), _counts_text(connection), *connection.problems) if p)
    playing = connection.playback_count or 0
    if connection.problems:
        return _row(title, subtitle, "unvollständig", "warn")
    if playing:
        return _row(title, subtitle, "1 läuft" if playing == 1 else f"{playing} laufen", "good")
    return _row(title, subtitle, "nichts läuft", "neutral")


def status_rows(overview: Overview) -> list[dict[str, Any]]:
    if not overview.connections:
        return [_row("Plex nicht eingerichtet", SETTINGS_HINT, "nicht eingerichtet", "neutral")]
    many = len(overview.connections) > 1
    summaries = [_summary_row(c) if c.state == "ok" else _problem_row(c) for c in overview.connections]
    if len(summaries) > MAX_ROWS:
        rest = len(summaries) - (MAX_ROWS - 1)
        return [*summaries[: MAX_ROWS - 1], _row(f"… und {rest} weitere Zeilen", "Alles steht auf der Seite „Plex“.", "–", "neutral")]
    playing = [_playback_row(p, c, many) for c in overview.connections for p in c.playbacks or []]
    total = sum(c.playback_count or 0 for c in overview.connections)
    budget = MAX_ROWS - len(summaries)
    if total <= budget:
        return [*playing, *summaries]
    if budget == 0:
        return summaries
    # Es passen nicht alle: eine Zeile bleibt für den Hinweis auf den Rest.
    keep = budget - 1
    more = _row(f"… und {total - keep} weitere Wiedergaben", "Alle stehen auf der Seite „Plex“.", "–", "neutral")
    return [*playing[:keep], more, *summaries]
