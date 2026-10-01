"""Die feste Symbol-Auswahl fuer eigene Apps steht zweimal: im Backend (`services/custom_apps.APP_ICONS`,
Pruefung der Eingabe) und im Frontend (`frontend/src/lib/appIcons.ts`, die tatsaechlichen Bilder). Ein
Name, den nur eine Seite kennt, waere entweder nicht speicherbar oder nicht darstellbar -- dieser Test haelt
beide Listen deckungsgleich."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from nodvard_deck.services.custom_apps import APP_ICONS

FRONTEND_LIST = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "appIcons.ts"


def _frontend_names() -> list[str]:
    source = FRONTEND_LIST.read_text(encoding="utf-8")
    block = re.search(r"export const APP_ICON_NAMES = \[(.*?)\] as const;", source, re.DOTALL)
    assert block, "APP_ICON_NAMES in appIcons.ts nicht gefunden"
    return re.findall(r'"([a-z0-9-]+)"', block.group(1))


@pytest.mark.skipif(not FRONTEND_LIST.exists(), reason="Frontend-Quellen nicht dabei")
def test_backend_and_frontend_offer_the_same_icons():
    assert sorted(_frontend_names()) == sorted(APP_ICONS)


@pytest.mark.skipif(not FRONTEND_LIST.exists(), reason="Frontend-Quellen nicht dabei")
def test_frontend_has_a_component_for_every_name():
    source = FRONTEND_LIST.read_text(encoding="utf-8")
    mapping = re.search(r"const APP_ICONS[^=]*=\s*\{(.*?)\n\};", source, re.DOTALL)
    assert mapping, "Zuordnung Name -> Bild in appIcons.ts nicht gefunden"
    mapped = re.findall(r'^\s*"?([a-z0-9-]+)"?\s*:', mapping.group(1), re.MULTILINE)
    assert sorted(mapped) == sorted(APP_ICONS)
