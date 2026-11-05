"""Welche Ausgabe läuft: Nodvard Deck oder Nodvard Dock (dieselbe Software mit fest eingebauten Modulen). Festgelegt
beim Bauen mit dem Build-Argument `EDITION` (`deploy/Dockerfile`), gelesen aus der Datei im Image. Nur
Standardbibliothek, weil `image_info` den Wert schon beim Bauen prüft."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Edition:
    id: str
    product_name: str
    service: str
    """Name des Dienstes in der mitgelieferten Compose-Datei, für Befehle wie `docker compose logs <service>`."""
    repository: str
    """Das offizielle Image unter `ghcr.io/<repository>`; danach sucht „Nach Updates suchen“."""
    choose_modules: bool
    """Der Assistent fragt, welche Module man nutzen will. Sonst stehen sie mit dem Image fest und er schaltet alle ein."""
    repo_tools: bool
    """Update-Helfer und `scripts/deploy_pi.sh` gelten für diese Ausgabe."""


DECK = Edition(
    id="deck", product_name="Nodvard Deck", service="nodvard-deck", repository="nodvard/deck",
    choose_modules=True, repo_tools=True,
)
DOCK = Edition(
    id="dock", product_name="Nodvard Dock", service="nodvard-dock", repository="nodvard/dock",
    choose_modules=False, repo_tools=False,
)
EDITIONS = {edition.id: edition for edition in (DECK, DOCK)}


def of(settings: Any) -> Edition:
    """Die Ausgabe laut `Settings.edition`; ohne Angabe oder bei einem unbekannten Wert Nodvard Deck."""
    return EDITIONS.get(getattr(settings, "edition", None) or "", DECK)


def display_text(settings: Any, text: str) -> str:
    if of(settings).choose_modules:
        return text
    return re.sub(r"\bDeck\b", of(settings).product_name.removeprefix("Nodvard "), text)
