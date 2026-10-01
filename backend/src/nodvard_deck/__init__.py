"""Nodvard Deck (Paket `nodvard_deck`, frueher `lattice`) — generischer Kern einer modularen Self-Hosting-Plattform.

Dieses Paket kennt keine Extension. Wenn hier jemals das Wort "Proxmox", "Shield" oder
"Ollama" auftaucht, ist das Design falsch — nicht der Check, der es findet
(scripts/check_core_purity.py).

Der alte Name `lattice` bleibt als Alias-Paket erhalten (derselbe Ordner `src`, gleiche Distribution): dieselben
Module und Objekte, mit DeprecationWarning (Mechanik: `sdk/python/_nodvard_alias.py`).
"""

from .version import __version__

__all__ = ["__version__"]
