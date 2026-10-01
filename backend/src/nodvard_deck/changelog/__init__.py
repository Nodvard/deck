"""Aenderungsprotokoll mit Versionen -- wird im Dashboard unter "Über Nodvard Deck" gezeigt.

Die Daten liegen als kleine TOML-Dateien NEBEN dem Code (Paketdaten, siehe
`backend/pyproject.toml`), damit sie mit jedem Image mitkommen:

- `versions/<version>.toml`: eine Datei je veroeffentlichter Version (`version`, `date`,
  optional `title`, dazu `[[entries]]`-Tabellen mit `kind`, `text`, optional `prs`).
- `unreleased/<name>.toml`: Bruchstuecke NUR mit `[[entries]]`, eine Datei je PR/Branch,
  damit parallele PRs sich nie in die Quere kommen. `scripts/release.py` fasst sie bei
  einem Release zu einer Versionsdatei zusammen.

Dieses Modul hat bewusst nur Standardbibliothek als Abhaengigkeit: `scripts/release.py`
nutzt dieselben Pruefungen, auch auf einem Rechner ohne installiertes Nodvard Deck.

Zwei Betriebsarten: die `parse_*`/`read_*`-Funktionen sind STRENG (werfen
`ChangelogError`, das Release-Skript und die Tests brauchen das), `load_changelog()` ist
GNAEDIG -- eine kaputte Datei wird uebersprungen und protokolliert, damit ein Tippfehler
im Protokoll nie das Dashboard lahmlegt.
"""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger("nodvard_deck.changelog")

CHANGELOG_DIR = Path(__file__).resolve().parent
"""Ordner mit `versions/` und `unreleased/`. Tests biegen ihn per monkeypatch um."""

KINDS = ("neu", "verbessert", "behoben", "sicherheit")
"""Art einer Aenderung -- die Reihenfolge ist auch die Anzeigereihenfolge."""

MAX_TEXT_LENGTH = 1000
MAX_TITLE_LENGTH = 100

_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

_ENTRY_KEYS = {"kind", "text", "prs"}
_RELEASE_KEYS = {"version", "date", "title", "entries"}
_FRAGMENT_KEYS = {"entries"}


class ChangelogError(ValueError):
    """Eine Datei des Aenderungsprotokolls ist ungueltig (Meldung nennt die Datei)."""


@dataclass(frozen=True)
class Entry:
    kind: str
    text: str
    prs: tuple[int, ...] = ()


@dataclass(frozen=True)
class Release:
    version: str
    date: str
    """ISO-Datum `JJJJ-MM-TT`."""
    title: str | None
    entries: tuple[Entry, ...]


@dataclass(frozen=True)
class Changelog:
    versions: tuple[Release, ...]
    """Neueste Version zuerst (numerisch sortiert, nicht als Text)."""
    unreleased: tuple[Entry, ...]
    """Eintraege aus `unreleased/`, noch ohne Versionsnummer."""

    @property
    def latest(self) -> str | None:
        return self.versions[0].version if self.versions else None


# --- Pruefen ------------------------------------------------------------------------


def parse_version(text: object) -> tuple[int, int, int]:
    """`"0.10.2"` -> `(0, 10, 2)`. Nur Zahlen ohne fuehrende Nullen, genau drei Teile."""
    if not isinstance(text, str) or not (match := _VERSION_RE.match(text)):
        raise ChangelogError(f"ungueltige Versionsnummer {text!r} (erwartet z. B. 0.5.0)")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def parse_entries(data: object) -> tuple[Entry, ...]:
    """Prueft die `[[entries]]`-Tabellen (mindestens eine)."""
    if not isinstance(data, list) or not data:
        raise ChangelogError("es fehlen [[entries]]-Eintraege")
    entries: list[Entry] = []
    for index, raw in enumerate(data, start=1):
        where = f"Eintrag {index}"
        if not isinstance(raw, dict):
            raise ChangelogError(f"{where}: keine Tabelle")
        unknown = set(raw) - _ENTRY_KEYS
        if unknown:
            raise ChangelogError(f"{where}: unbekannte Felder {sorted(unknown)}")
        kind = raw.get("kind")
        if kind not in KINDS:
            raise ChangelogError(f"{where}: kind muss eines von {', '.join(KINDS)} sein, ist {kind!r}")
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ChangelogError(f"{where}: text fehlt oder ist leer")
        text = text.strip()
        if len(text) > MAX_TEXT_LENGTH:
            raise ChangelogError(f"{where}: text ist laenger als {MAX_TEXT_LENGTH} Zeichen")
        if _CONTROL_RE.search(text):
            raise ChangelogError(f"{where}: text darf keine Zeilenumbrueche oder Steuerzeichen enthalten")
        prs = raw.get("prs", [])
        # bool ist in Python ein int -- `prs = [true]` waere sonst "PR 1".
        if not isinstance(prs, list) or not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in prs):
            raise ChangelogError(f"{where}: prs muss eine Liste positiver Zahlen sein")
        entries.append(Entry(kind=kind, text=text, prs=tuple(prs)))
    return tuple(entries)


def _parse_date(value: object) -> str:
    # TOML kennt echte Datumswerte (`date = 2026-09-30`), die Dateien schreiben aber
    # Text -- beides ist erlaubt. `datetime` ist eine Unterklasse von `date`, gemeint
    # ist hier aber nur ein reines Datum.
    if isinstance(value, datetime):
        raise ChangelogError("date darf keine Uhrzeit enthalten")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and _DATE_RE.match(value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    raise ChangelogError(f"ungueltiges Datum {value!r} (erwartet JJJJ-MM-TT)")


def parse_release(data: dict[str, Any]) -> Release:
    unknown = set(data) - _RELEASE_KEYS
    if unknown:
        raise ChangelogError(f"unbekannte Felder {sorted(unknown)}")
    version = data.get("version")
    parse_version(version)
    title = data.get("title")
    if title is not None:
        if not isinstance(title, str) or not title.strip() or _CONTROL_RE.search(title):
            raise ChangelogError("title muss eine kurze Textzeile sein")
        title = title.strip()
        if len(title) > MAX_TITLE_LENGTH:
            raise ChangelogError(f"title ist laenger als {MAX_TITLE_LENGTH} Zeichen")
    return Release(
        version=version,
        date=_parse_date(data.get("date")),
        title=title,
        entries=parse_entries(data.get("entries")),
    )


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ChangelogError(f"{path.name}: {exc}") from exc


def read_release(path: Path) -> Release:
    """Liest `versions/<version>.toml` streng. Der Dateiname muss zur Version passen."""
    data = _read_toml(path)
    try:
        release = parse_release(data)
    except ChangelogError as exc:
        raise ChangelogError(f"{path.name}: {exc}") from exc
    if path.stem != release.version:
        raise ChangelogError(f"{path.name}: Dateiname passt nicht zur Version {release.version}")
    return release


def read_fragment(path: Path) -> tuple[Entry, ...]:
    """Liest `unreleased/<name>.toml` streng (nur `[[entries]]`)."""
    data = _read_toml(path)
    unknown = set(data) - _FRAGMENT_KEYS
    if unknown:
        raise ChangelogError(f"{path.name}: unbekannte Felder {sorted(unknown)} (erlaubt sind nur [[entries]])")
    try:
        return parse_entries(data.get("entries"))
    except ChangelogError as exc:
        raise ChangelogError(f"{path.name}: {exc}") from exc


# --- Laden --------------------------------------------------------------------------


def load_changelog(root: Path | None = None) -> Changelog:
    """Laedt alle Dateien; ungueltige werden uebersprungen und protokolliert."""
    base = root if root is not None else CHANGELOG_DIR

    releases: list[Release] = []
    for path in sorted((base / "versions").glob("*.toml")):
        try:
            releases.append(read_release(path))
        except ChangelogError as exc:
            logger.warning("Aenderungsprotokoll: Datei wird ignoriert: %s", exc)
    releases.sort(key=lambda r: parse_version(r.version), reverse=True)

    unreleased: list[Entry] = []
    for path in sorted((base / "unreleased").glob("*.toml")):
        try:
            unreleased.extend(read_fragment(path))
        except ChangelogError as exc:
            logger.warning("Aenderungsprotokoll: Datei wird ignoriert: %s", exc)

    return Changelog(versions=tuple(releases), unreleased=tuple(unreleased))


@lru_cache(maxsize=8)
def _load_cached(root: Path) -> Changelog:
    return load_changelog(root)


def get_changelog() -> Changelog:
    """Wie `load_changelog()`, aber einmal pro Prozess gelesen: die Dateien aendern sich
    nur mit einem neuen Image, also mit einem Neustart."""
    return _load_cached(CHANGELOG_DIR)


def clear_cache() -> None:
    _load_cached.cache_clear()
