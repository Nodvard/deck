"""SDK-Version: der Kompatibilitätsvertrag zwischen Kern und Extensions."""

from __future__ import annotations

API_VERSION = "0.1.0"
"""SemVer; eine Extension deklariert sie in ihrem Manifest als `api_version`.
Änderungen sind nur abwärtskompatibel: Hinzufügen ja, Entfernen oder Umbenennen nur mit Übergang."""


def is_compatible(required: str) -> bool:
    """Prüft, ob eine Extension mit `api_version = required` geladen werden darf.

    Ab 1.0: gleiche Major-Version, Minor des Kerns >= Minor der Extension; unter 1.0 zählt die Minor-Version als Major.
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
