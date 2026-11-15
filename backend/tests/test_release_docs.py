"""Changelog, Doku-Übersicht und englische Versionshinweise passen zur aktuellen Version."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from nodvard_deck import changelog
from nodvard_deck.version import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"
NOTES_DE = DOCS / "release-notes" / f"{__version__}.md"
NOTES_EN = DOCS / "en" / "release-notes" / f"{__version__}.md"


def _read(path: Path) -> str:
    assert path.is_file(), f"Datei fehlt: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def _german_part(text: str) -> str:
    # Die deutsche Datei darf am Ende eine eingeklappte englische Kurzfassung tragen.
    return text.split("<details>", 1)[0]


def _points(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("- ")]


def _sections(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("### ")]


def test_changelog_has_section_for_current_version():
    text = _read(REPO_ROOT / "CHANGELOG.md")
    parts = re.split(r"^## \[", text, flags=re.MULTILINE)
    section = next((part for part in parts if part.startswith(f"{__version__}]")), None)
    assert section is not None, f"CHANGELOG.md hat keinen Abschnitt [{__version__}]"
    released = tomllib.loads(_read(changelog.CHANGELOG_DIR / "versions" / f"{__version__}.toml"))["date"]
    assert str(released) in section.splitlines()[0], f"Abschnitt [{__version__}] nennt nicht das Datum {released}"
    assert _points(section), f"Abschnitt [{__version__}] ist leer"
    assert parts[1].startswith("Unreleased]") and parts[2].startswith(f"{__version__}]")


def test_docs_index_links_current_release_notes():
    rows = [line for line in _read(DOCS / "README.md").splitlines() if "release-notes/" in line]
    current = [
        row for row in rows
        if row.startswith(f"| [en/release-notes/{__version__}.md](en/release-notes/{__version__}.md)")
    ]
    assert len(current) == 1, f"docs/README.md führt die Versionshinweise {__version__} nicht als aktuelle Zeile"
    assert f"](release-notes/{__version__}.md)" in current[0]
    assert f"Release notes {__version__}" in current[0]


def test_english_release_notes_cover_the_german_ones():
    german = _german_part(_read(NOTES_DE))
    english = _read(NOTES_EN)
    assert english.startswith(f"# Nodvard Deck {__version__}")
    assert len(_sections(english)) == len(_sections(german))
    assert len(_points(english)) >= 0.8 * len(_points(german)), (
        f"Englische Hinweise {__version__}: {len(_points(english))} Punkte, deutsche {len(_points(german))}"
    )
    assert len(english) >= 0.6 * len(german)
    assert "–" not in english and "—" not in english


@pytest.mark.parametrize(
    ("guide", "intro", "row"),
    [
        ("FEHLERSUCHE.md", r"Nachfolgend sind (\d+) häufige Probleme", r"Die (\d+) häufigsten Probleme"),
        ("en/TROUBLESHOOTING.md", r"Below are (\d+) frequent issues", r"Troubleshooting: (\d+) frequent problems"),
    ],
    ids=["deutsch", "englisch"],
)
def test_troubleshooting_counts_match_and_name_no_phone_app(guide, intro, row):
    text = _read(DOCS / guide)
    numbers = [int(number) for number in re.findall(r"^## (\d+)\. ", text, flags=re.MULTILINE)]
    assert numbers == list(range(1, len(numbers) + 1))
    assert int(re.search(intro, text).group(1)) == len(numbers)
    assert int(re.search(row, _read(DOCS / "README.md")).group(1)) == len(numbers)
    # Eine eigene Handy-App ist nicht veröffentlicht, die Fehlersuche darf kein App-Update empfehlen.
    assert not re.search(r"Smartphone-App|App-Fassung|mobilen App|mobile application|app builds", text, flags=re.IGNORECASE)


@pytest.mark.parametrize(
    ("path", "heading", "origin"),
    [
        ("18-SHIELD.md", "## CVE-Abgleich und", "aus dem Heimnetz"),
        ("en/SHIELD.md", "## CVE matching and", "from the home network"),
    ],
    ids=["deutsch", "englisch"],
)
def test_shield_guide_says_where_the_external_check_runs(path, heading, origin):
    text = _read(DOCS / path)
    line = next(line for line in text.splitlines() if line.startswith(heading))
    assert origin in line
    assert "Prüfung von außen" not in text


def test_release_notes_do_not_announce_a_check_from_outside():
    for path in (NOTES_DE, NOTES_EN):
        for line in _read(path).splitlines():
            if re.search(r"Außenprüfung|Prüfung von außen|Externe Prüfung|external check", line, flags=re.IGNORECASE):
                assert re.search(r"Heimnetz|home network", line), f"{path.name}: {line[:80]}"
