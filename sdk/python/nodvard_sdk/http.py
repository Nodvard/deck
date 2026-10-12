"""Hilfen fuer Extension-Routen, die HTTP betreffen.

Der Kern begrenzt jede Anfrage auf eine kleine Koerpergroesse (Standard 1 MiB), bevor ein
Handler sie zu sehen bekommt. Eine Route, die bewusst mehr annimmt (Upload einer Datei, eines
Bildes), erklaert das mit `max_body_bytes`; ohne diese Angabe gilt die allgemeine Grenze.
"""

from __future__ import annotations

import re
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


_HEX64 = re.compile(r"[0-9A-F]{64}")
_FINGERPRINT_PREFIXES = ("sha256 fingerprint=", "sha256=", "sha256:", "sha256 ")


def sha256_fingerprint(value: str) -> str:
    """Bringt einen SHA-256-Fingerabdruck in die Schreibweise von `PeerCertificate.sha256` (`AB:CD:...`, 32 Paare).

    Nimmt Gross- und Kleinbuchstaben, mit oder ohne Doppelpunkte, Leerzeichen oder Bindestriche dazwischen, auch mit
    vorangestelltem `sha256` oder `SHA256 Fingerprint=` (wie `openssl` es schreibt). `ValueError`, wenn es kein
    SHA-256-Fingerabdruck ist."""
    text = str(value).strip()
    for prefix in _FINGERPRINT_PREFIXES:
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    compact = "".join(ch for ch in text if ch not in ": -\t").upper()
    if not _HEX64.fullmatch(compact):
        raise ValueError("Das ist kein SHA-256-Fingerabdruck (64 Zeichen aus 0-9 und A-F, gern mit Doppelpunkten).")
    return ":".join(compact[i : i + 2] for i in range(0, 64, 2))
