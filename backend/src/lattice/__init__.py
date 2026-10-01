"""`lattice` -- alter Paketname von `nodvard_deck`. Nur ein Alias.

`import lattice` liefert **dasselbe Modulobjekt** wie `import nodvard_deck`, `lattice.db.base` dasselbe wie
`nodvard_deck.db.base` (zum Beispiel gibt es nur eine SQLAlchemy-`Base`). Mechanik und Gruende:
`sdk/python/_nodvard_alias.py`. Fremde Extensions, die `from lattice.db.base import IdMixin` schreiben, und
gewohnte Befehle wie `uvicorn lattice.main:app` laufen so unveraendert weiter -- mit DeprecationWarning.

Die echten Dateien in diesem Ordner (`migrate.py`, `boot.py`, `admin.py`, `rescue.py`) sind die Einstiegspunkte
fuer `python -m lattice.<name>`: `runpy` braucht dafuer echten Code. Sie rufen das echte Modul auf und ersetzen sich
beim normalen Import durch dieses. Alles andere bildet der Finder ab; hier liegt sonst nichts. Die vier Namen stehen
unten ausdruecklich (`launchers`): Eine liegengebliebene alte Datei hier (z. B. `config.py`) wird so nie geladen.
"""

from pathlib import Path

import nodvard_deck
from _nodvard_alias import alias_package

alias_package(__name__, nodvard_deck, shim_dir=Path(__file__).parent, launchers=("admin", "boot", "migrate", "rescue"))
