"""`python -m lattice.boot` -- alter Name von `python -m nodvard_deck.boot`. Siehe `lattice/__init__.py`."""

import sys

from nodvard_deck import boot as _real

if __name__ == "__main__":
    sys.exit(_real.main())
else:
    # Normaler Import (`import lattice.boot`): das echte Modul selbst liefern, nicht eine Huelle darum.
    sys.modules[__name__] = _real
