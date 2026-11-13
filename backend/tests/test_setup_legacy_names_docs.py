"""Alte Umgebungsvariablen bleiben von der Warnung vor Umbenennungen ausgenommen."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("doc", "heading", "exception", "manual_migration"),
    [
        (
            "docs/11-ERST-EINRICHTUNG.md",
            "## M. Alte Namen bei älteren Installationen",
            "Ausgenommen sind `LATTICE_*`-Variablen in `.env`",
            "Wer von Hand ausliefert, benennt die Werte in `.env` selbst um.",
        ),
        (
            "docs/en/FIRST-SETUP.md",
            "## M. Old names on older installations",
            "The exception is `LATTICE_*` variables in `.env`",
            "Anyone deploying manually renames the values in `.env` themselves.",
        ),
    ],
    ids=["deutsch", "englisch"],
)
def test_legacy_name_warning_excepts_env_migration(doc, heading, exception, manual_migration):
    text = (ROOT / doc).read_text(encoding="utf-8")
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    introduction = " ".join(section.split("\n| Name |", 1)[0].split())
    assert exception in introduction
    assert "`NODVARD_DECK_*`" in introduction
    row = next(line for line in section.splitlines() if line.startswith("| `LATTICE_PORT`"))
    assert manual_migration in row
