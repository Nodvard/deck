"""Abstaende in Minuten fuer Zeitplaene der Form `*/N * * * *`.

Nur Teiler von 60 laufen gleichmaessig: `*/7` liefe um :49, :56 und gleich wieder um :00. Die Einstellungen bieten
darum nur diese Werte an (settings.schema.json, `enum`); ein Wert von frueher wird hier auf den naechstkleineren
gesetzt."""

from __future__ import annotations

MINUTE_STEPS = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)


def minute_step(value: int, low: int) -> int:
    """Groesster Teiler von 60, der nicht ueber `value` liegt -- mindestens `low`, hoechstens 30."""
    return max(step for step in MINUTE_STEPS if step <= max(value, low))
