"""SDK-Version — der Kompatibilitätsvertrag zwischen Kern und Extensions.

Siehe docs/02-EXTENSION-API.md §10.
"""

from __future__ import annotations

API_VERSION = "0.1.0"
"""SemVer. Eine Extension deklariert in ihrem Manifest `api_version`.

Solange die Major-Version 0 ist, sind brechende Änderungen erlaubt — es existieren nur
eigene Extensions. Anhebung auf 1.0.0, sobald die Phase-1-Extensions stehen.
"""


def is_compatible(required: str) -> bool:
    """Prüft, ob eine Extension mit `api_version = required` geladen werden darf.

    Regel ab 1.0: gleiche Major-Version, Minor des Kerns >= Minor der Extension.
    Unter 1.0 zählt die Minor-Version als Major (SemVer-Konvention für 0.x).
    """
    try:
        req = tuple(int(p) for p in required.split(".")[:3])
        cur = tuple(int(p) for p in API_VERSION.split(".")[:3])
    except ValueError:
        return False
    if len(req) < 2 or len(cur) < 2:
        return False
    if cur[0] == 0 or req[0] == 0:
        return req[0] == cur[0] and req[1] == cur[1]
    return req[0] == cur[0] and req[1] <= cur[1]
