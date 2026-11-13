"""Änderungsprotokoll für die Seite „Über“ in den Einstellungen und den Versionshinweis in der Seitenleiste.

Für jeden angemeldeten Nutzer ohne eigene Berechtigung: nur Änderungen an der Software, keine Daten der Installation.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from ...changelog import Entry, get_changelog
from ...edition import of
from ...version import __version__
from ..responses import error_responses
from ..deps import CurrentUser, SettingsDep

router = APIRouter(prefix="/app", tags=["app"])


class ChangelogEntryOut(BaseModel):
    kind: Literal["neu", "verbessert", "behoben", "sicherheit", "dokumentation"]
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


@router.get("/changelog", name="read_changelog", responses=error_responses(401, 403, 507))
async def changelog_response(_user: CurrentUser, settings: SettingsDep, include_documentation: bool = False) -> ChangelogOut:
    response = await read_changelog(_user, settings)
    if not include_documentation:
        entries = [*response.unreleased, *(entry for release in response.versions for entry in release.entries)]
        for entry in entries:
            if entry.kind == "dokumentation":
                entry.kind = "verbessert"
    return response


async def read_changelog(_user: CurrentUser, settings: SettingsDep) -> ChangelogOut:
    data = get_changelog(of(settings).id)
    return ChangelogOut(
        current=__version__,
        build=(settings.build or "").strip() or None,
        unreleased=[_entry_out(e) for e in data.unreleased],
        versions=[
            ChangelogVersionOut(
                version=release.version,
                date=release.date,
                title=release.title if of(settings).choose_modules else None,
                entries=[_entry_out(e) for e in release.entries],
            )
            for release in data.versions
        ],
    )
