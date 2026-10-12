"""Hilfen für Extension-Routen, die HTTP betreffen.

Der Kern begrenzt jede Anfrage auf 1 MiB (Standard); eine Route, die mehr annimmt, erklärt das mit `max_body_bytes`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])

MAX_BODY_ATTR = "__nodvard_max_body_bytes__"
"""Name des Attributs, unter dem die Grenze an der Handler-Funktion hängt (liest der Kern)."""


# Der Kern prüft die Länge im Kopf und zählt bei Datenströmen ohne Länge mit. Die Route selbst sollte ihre Daten weiter
# streamen oder eigene Prüfungen behalten.
def max_body_bytes(limit: int | Callable[[], int]) -> Callable[[_F], _F]:
    """Erlaubt der Route (nur für Uploads) einen größeren Anfragekörper; `limit` in Bytes oder eine Funktion ohne
    Argumente, die ihn bei jeder Anfrage liefert. Der Kern antwortet mit 413, sobald die Anfrage größer ist.
    Reihenfolge: `@router.post(...)` ganz außen, `@max_body_bytes(...)` direkt über der Funktion; sie bleibt gleich."""

    def decorate(func: _F) -> _F:
        setattr(func, MAX_BODY_ATTR, limit)
        return func

    return decorate


_HEX64 = re.compile(r"[0-9A-F]{64}")
_FINGERPRINT_PREFIXES = ("sha256 fingerprint=", "sha256=", "sha256:", "sha256 ")


def sha256_fingerprint(value: str) -> str:
    """Bringt einen SHA-256-Fingerabdruck in die Schreibweise von `PeerCertificate.sha256` (`AB:CD:...`, 32 Paare).
    Nimmt Groß-/Kleinbuchstaben, Trenner (`:`, Leerzeichen, `-`) und ein Präfix wie `sha256` oder `SHA256 Fingerprint=`
    (wie `openssl` es schreibt). `ValueError`, wenn es kein SHA-256-Fingerabdruck ist."""
    text = str(value).strip()
    for prefix in _FINGERPRINT_PREFIXES:
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    compact = "".join(ch for ch in text if ch not in ": -\t").upper()
    if not _HEX64.fullmatch(compact):
        raise ValueError("Das ist kein SHA-256-Fingerabdruck (64 Zeichen aus 0-9 und A-F, gern mit Doppelpunkten).")
    return ":".join(compact[i : i + 2] for i in range(0, 64, 2))
