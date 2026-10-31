"""Wartungsfenster für Tests: Sie rechnen mit einer festen Uhrzeit statt der echten, damit kein Lauf in eine
Zeitumstellung fällt."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from nodvard_deck.config import LOCAL_TIMEZONE

# Im Hochsommer gibt es jede Berliner Uhrzeit der Stunden davor genau einmal.
WINDOW_NOW = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)


def window(host_ids: str | list[str] = "all", *, minutes_ago: int = 5, duration: int = 60) -> dict:
    """Ein Fenster, das `minutes_ago` Minuten vor `WINDOW_NOW` begann; der Cron meint Ortszeit wie der
    Zeitplan-Wähler."""
    start = (WINDOW_NOW - timedelta(minutes=minutes_ago)).astimezone(ZoneInfo(LOCAL_TIMEZONE))
    return {"cron": f"{start.minute} {start.hour} * * *", "duration_minutes": duration, "host_ids": host_ids}
