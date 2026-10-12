"""Nodvard Deck (Paket `nodvard_deck`, alter Name `lattice`): generischer Kern ohne Wissen über einzelne Erweiterungen.

`scripts/check_core_purity.py` prüft das. Der alte Name bleibt als Alias-Paket mit Warnung (`sdk/python/_nodvard_alias.py`).
"""

from .version import __version__

__all__ = ["__version__"]
