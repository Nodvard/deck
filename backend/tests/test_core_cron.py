"""Crontab-Wochentage (0 und 7 = Sonntag) statt APSchedulers eigener Zaehlung (0 = Montag)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from nodvard_deck.core.cron import cron_trigger, translate_day_of_week

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("0", "sun"),
        ("7", "sun"),
        ("1", "mon"),
        ("6", "sat"),
        ("1-5", "mon,tue,wed,thu,fri"),
        ("5-7", "fri,sat,sun"),
        ("0-6", "sun,mon,tue,wed,thu,fri,sat"),
        ("0-7", "sun,mon,tue,wed,thu,fri,sat"),
        ("*/2", "sun,tue,thu,sat"),
        ("1-5/2", "mon,wed,fri"),
        ("1,3,5", "mon,wed,fri"),
        ("0,6", "sun,sat"),
        ("6-1", "sat,sun,mon"),
        ("mon-fri", "mon,tue,wed,thu,fri"),
        ("sun-tue", "sun,mon,tue"),
        ("MON", "mon"),
        ("*", "*"),
    ],
)
def test_translate_day_of_week(field, expected):
    assert translate_day_of_week(field) == expected


@pytest.mark.parametrize("field", ["8", "a-b", "", "1-", "-3", "*/0", "1,,2", "9-10"])
def test_translate_day_of_week_rejects_invalid(field):
    with pytest.raises(ValueError, match="Wochentag"):
        translate_day_of_week(field)


def _next(expr: str, start: datetime) -> datetime:
    return cron_trigger(expr, timezone=BERLIN).get_next_fire_time(None, start)


def test_next_fire_time_sunday_is_a_sunday():
    fire = _next("30 3 * * 0", datetime(2026, 9, 30, tzinfo=BERLIN))
    assert fire.strftime("%A") == "Sunday"
    assert (fire.month, fire.day, fire.hour, fire.minute) == (10, 4, 3, 30)


def test_seven_means_sunday_too():
    fire = _next("0 3 * * 7", datetime(2026, 9, 30, tzinfo=BERLIN))
    assert fire.strftime("%A") == "Sunday"


def test_weekdays_never_fire_on_weekend_and_include_monday():
    start = datetime(2026, 9, 30, tzinfo=BERLIN)
    trigger = cron_trigger("0 3 * * 1-5", timezone=BERLIN)
    seen = []
    cursor = start
    for _ in range(10):
        cursor = trigger.get_next_fire_time(None, cursor)
        seen.append(cursor.strftime("%a"))
        cursor = cursor.replace(minute=1)
    assert set(seen) == {"Mon", "Tue", "Wed", "Thu", "Fri"}


def test_other_fields_untouched_and_field_count_checked():
    assert _next("*/15 * * * *", datetime(2026, 9, 30, 10, 1, tzinfo=BERLIN)).minute == 15
    with pytest.raises(ValueError):
        cron_trigger("0 3 * *")
    with pytest.raises(ValueError):
        cron_trigger("0 3 * * 8")
