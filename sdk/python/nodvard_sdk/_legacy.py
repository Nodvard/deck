"""Alte Namen (`LatticeExtension`, `LatticeError`, `lattice_sdk`) als Aliase der neuen -- dieselben
Objekte, mit DeprecationWarning.

Jedes SDK-Modul mit einem umbenannten Namen traegt `_LEGACY_NAMES = {alter Name: neuer Name}` und
`__getattr__ = legacy_getattr(...)` (PEP 562). So bleiben `from nodvard_sdk import LatticeExtension` und
`from lattice_sdk.errors import LatticeError` ein Stueck weit lauffaehig, ohne dass die alten Namen im Modul
selbst stehen. `backend/tests/contract/contract_lib.py` liest `_LEGACY_NAMES`, damit der Vertrags-Schnappschuss
die alten Namen weiter kennt (und ein Entfernen als Bruch meldet).
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Mapping
from typing import Any


def legacy_getattr(module_name: str, legacy: Mapping[str, str], namespace: dict[str, Any]) -> Callable[[str], Any]:
    """Baut das Modul-`__getattr__`. Der alte Name liefert das Objekt des neuen (keine Kopie, keine Unterklasse)."""

    def __getattr__(name: str) -> Any:
        new = legacy.get(name)
        if new is None:
            raise AttributeError(f"module {module_name!r} has no attribute {name!r}")
        warnings.warn(
            f"'{name}' heisst jetzt '{new}' ({module_name.split('.')[0]}). Der alte Name bleibt als Alias "
            f"(dasselbe Objekt) mehrere Versionen lang erhalten.",
            DeprecationWarning,
            stacklevel=2,
        )
        return namespace[new]

    return __getattr__
