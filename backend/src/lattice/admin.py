"""`python -m lattice.admin` -- alter Name von `python -m nodvard_deck.admin`. Siehe `lattice/__init__.py`."""

import os
import sys

from nodvard_deck import admin as _real

if __name__ == "__main__":
    # Wie im echten Modul: auch ohne den Entrypoint (`compose exec` erbt dessen umask nicht) nur fuer den Besitzer.
    os.umask(0o077)
    sys.exit(_real.main())
else:
    # Normaler Import (`import lattice.admin`): das echte Modul selbst liefern, nicht eine Huelle darum.
    sys.modules[__name__] = _real
