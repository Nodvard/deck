"""`python -m lattice.rescue` -- alter Name von `python -m nodvard_deck.rescue`. Siehe `lattice/__init__.py`."""

import sys

from nodvard_deck import rescue as _real

if __name__ == "__main__":
    sys.exit(_real.main())
else:
    # Normaler Import (`import lattice.rescue`): das echte Modul selbst liefern, nicht eine Huelle darum.
    sys.modules[__name__] = _real
