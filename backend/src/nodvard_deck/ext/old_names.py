"""Die alten Namen von Nodvard Deck: Eine Erweiterung, die sie noch nutzt, bekommt einen klaren Grund."""

from __future__ import annotations

import re

OLD_ENTRY_POINT_GROUP = "lattice.extensions"
OLD_PACKAGES = {"lattice": "nodvard_deck", "lattice_sdk": "nodvard_sdk"}
OLD_CLASSES = {"LatticeExtension": "NodvardExtension", "LatticeError": "NodvardError"}

OLD_GROUP_ERROR = (
    "Das Paket meldet sich nur unter dem alten Namen „lattice.extensions“ an. Seit Version 0.12 sucht Nodvard Deck "
    "Erweiterungen nur unter „nodvard_deck.extensions“: Bitte eine neuere Version der Erweiterung installieren."
)

_CANNOT_IMPORT = re.compile(r"cannot import name '(\w+)'")


def old_import_error(exc: BaseException) -> str | None:
    """Der Grund für einen Import, der an einem alten Paket- oder Klassennamen scheitert, sonst None."""
    found = _old_package(exc) or _old_class(exc)
    if found is None:
        return None
    kind, old, new = found
    return (
        f"Die Erweiterung nutzt noch den alten {kind} „{old}“, den es seit Version 0.12 nicht mehr gibt "
        f"(neu: „{new}“). Bitte eine neuere Version der Erweiterung installieren."
    )


def _old_package(exc: BaseException) -> tuple[str, str, str] | None:
    if not isinstance(exc, ModuleNotFoundError) or not exc.name:
        return None
    old = exc.name.partition(".")[0]
    new = OLD_PACKAGES.get(old)
    return ("Paketnamen", old, new) if new else None


def _old_class(exc: BaseException) -> tuple[str, str, str] | None:
    if isinstance(exc, ModuleNotFoundError):
        return None
    if isinstance(exc, ImportError):
        if (exc.name or "").partition(".")[0] != "nodvard_sdk":
            return None
        # Python nennt den fehlenden Namen nur im Text: „cannot import name 'X' from 'modul'“.
        match = _CANNOT_IMPORT.match(str(exc))
        old = match.group(1) if match else None
    elif isinstance(exc, AttributeError):
        module = getattr(getattr(exc, "obj", None), "__name__", None)
        if not isinstance(module, str) or module.partition(".")[0] != "nodvard_sdk":
            return None
        old = exc.name
    else:
        return None
    new = OLD_CLASSES.get(old) if old else None
    return ("Klassennamen", old, new) if new else None
