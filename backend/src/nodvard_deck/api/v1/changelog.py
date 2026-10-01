"""Aenderungsprotokoll fuer die Seite "Über Nodvard Deck" (Einstellungen) und den
Versions-Hinweis unten in der Seitenleiste.

Fuer jeden angemeldeten Nutzer, ohne eigene Berechtigung: das Protokoll enthaelt nur,
was sich an der Software geaendert hat, keine Daten aus der Installation. Die Dateien
selbst liegen unter `nodvard_deck/changelog/` (siehe dortiger Modul-Docstring).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from ...changelog import Entry, get_changelog
from ...version import __version__
from ..deps import CurrentUser, SettingsDep

router = APIRouter(prefix="/app", tags=["app"])


class ChangelogEntryOut(BaseModel):
    kind: Literal["neu", "verbessert", "behoben", "sicherheit"]
    text: str
    prs: list[int]


class ChangelogVersionOut(BaseModel):
    version: str
    date: str
    title: str | None
    entries: list[ChangelogEntryOut]


class ChangelogOut(BaseModel):
    current: str
    """Die laufende Version (`nodvard_deck.version`)."""
    build: str | None
    """Build-Kennung, wenn der Server eine kennt (`NODVARD_DECK_BUILD` bzw. die Version aus der Datei im Release-Image,
    `nodvard_deck.image_info`), sonst `null`."""
    unreleased: list[ChangelogEntryOut]
    """Schon eingebaut, aber noch ohne Versionsnummer."""
    versions: list[ChangelogVersionOut]
    """Neueste zuerst."""


def _entry_out(entry: Entry) -> ChangelogEntryOut:
    return ChangelogEntryOut(kind=entry.kind, text=entry.text, prs=list(entry.prs))  # type: ignore[arg-type]


@router.get("/changelog")
async def read_changelog(_user: CurrentUser, settings: SettingsDep) -> ChangelogOut:
    data = get_changelog()
    return ChangelogOut(
        current=__version__,
        build=(settings.build or "").strip() or None,
        unreleased=[_entry_out(e) for e in data.unreleased],
        versions=[
            ChangelogVersionOut(
                version=release.version,
                date=release.date,
                title=release.title,
                entries=[_entry_out(e) for e in release.entries],
            )
            for release in data.versions
        ],
    )
