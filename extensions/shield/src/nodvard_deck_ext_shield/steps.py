"""Abstände in Minuten für Zeitpläne der Form `*/N * * * *`."""

from __future__ import annotations

# Nur Teiler von 60 laufen gleichmäßig (`*/7` liefe um :49, :56 und gleich wieder um :00); die Einstellungen
# bieten darum nur diese Werte an (`enum` in settings.schema.json).
MINUTE_STEPS = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)


def minute_step(value: int, low: int) -> int:
    """Größter Teiler von 60, der nicht über `value` liegt, mindestens `low`, höchstens 30."""
    return max(step for step in MINUTE_STEPS if step <= max(value, low))
