"""Hilfen fuer Extension-Routen, die HTTP betreffen.

Der Kern begrenzt jede Anfrage auf eine kleine Koerpergroesse (Standard 1 MiB), bevor ein
Handler sie zu sehen bekommt. Eine Route, die bewusst mehr annimmt (Upload einer Datei, eines
Bildes), erklaert das mit `max_body_bytes`; ohne diese Angabe gilt die allgemeine Grenze.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])

MAX_BODY_ATTR = "__nodvard_max_body_bytes__"
"""Name des Attributs, unter dem die Grenze an der Handler-Funktion haengt (liest der Kern)."""


def max_body_bytes(limit: int | Callable[[], int]) -> Callable[[_F], _F]:
    """Erlaubt der Route einen groesseren Anfragekoerper als die allgemeine Grenze.

    Nur fuer Routen, die einen Upload annehmen. `limit` ist eine Obergrenze in Bytes (oder eine
    Funktion ohne Argumente, die sie bei jeder Anfrage liefert, wenn sie einstellbar ist). Der Kern
    antwortet mit 413, sobald die Anfrage groesser ist; er prueft die Laenge im Kopf und zaehlt bei
    Datenstroemen ohne Laenge mit. Die Route selbst sollte ihre Daten weiter streamen oder eigene
    Pruefungen behalten.

    Reihenfolge: `@router.post(...)` ganz aussen, `@max_body_bytes(...)` direkt ueber der Funktion.
    Der Dekorator veraendert die Funktion nicht.
    """

    def decorate(func: _F) -> _F:
        setattr(func, MAX_BODY_ATTR, limit)
        return func

    return decorate
