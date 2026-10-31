"""Die Beta-Liste in der Erst-Einrichtung (deutsch und englisch) nennt genau die Module mit `stage = "beta"`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from nodvard_sdk import load_manifest

ROOT = Path(__file__).resolve().parents[2]
# Englische Namen, wo die Doku den Modulnamen übersetzt.
ENGLISH = {"Gameserver": "Game servers", "Netzwerk": "Network"}


def _beta_names() -> set[str]:
    # Über das SDK gelesen wie in der App: Dort gilt auch ein unbekannter Reifegrad als Beta.
    manifests = (load_manifest(path) for path in (ROOT / "extensions").glob("*/extension.toml"))
    return {m.name for m in manifests if m.stage == "beta"}


def _listed(doc: str, start: str, verb: str, last_and: str) -> set[str]:
    text = " ".join((ROOT / doc).read_text(encoding="utf-8").split())
    match = re.search(re.escape(start) + r" (.+?) " + verb + " ", text)
    assert match, f"{doc}: Absatz zu Beta fehlt"
    head, _, tail = match.group(1).rpartition(f" {last_and} ")
    return {name.strip() for name in head.split(",")} | {tail.strip()}


def test_there_are_beta_modules():
    assert len(_beta_names()) >= 5


@pytest.mark.parametrize(
    ("doc", "start", "verb", "last_and", "translate"),
    [
        ("docs/11-ERST-EINRICHTUNG.md", "**„Beta“:**", "tragen", "und", False),
        ("docs/en/FIRST-SETUP.md", '**"Beta":**', "carry", "and", True),
    ],
    ids=["deutsch", "englisch"],
)
def test_the_beta_list_names_exactly_the_beta_modules(doc, start, verb, last_and, translate):
    expected = {ENGLISH.get(name, name) for name in _beta_names()} if translate else _beta_names()
    assert _listed(doc, start, verb, last_and) == expected
