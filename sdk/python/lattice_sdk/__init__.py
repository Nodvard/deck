"""`lattice_sdk` -- alter Name von `nodvard_sdk` (Umbenennung Lattice -> Nodvard Deck). Nur ein Alias.

`import lattice_sdk` liefert **dasselbe Modulobjekt** wie `import nodvard_sdk`, `lattice_sdk.x.y` dasselbe wie
`nodvard_sdk.x.y` (Mechanik und Gruende: `_nodvard_alias.py`). Bestehende Extensions von Dritten laufen so
unveraendert weiter; sie bekommen eine DeprecationWarning und sollen auf `nodvard_sdk` umstellen.

Hier steht absichtlich nichts anderes: kein Code des SDK, keine eigenen Untermodule. Entfernt wird der Alias
erst mit einer angekuendigten Major-Version (docs/04-API.md, "Kompatibilitaet").
"""

import nodvard_sdk
from _nodvard_alias import alias_package

alias_package(__name__, nodvard_sdk)
