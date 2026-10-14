"""Abstand zwischen zwei Läufen eines Backup-Jobs, gelesen aus seinem Zeitplan."""

from __future__ import annotations

import re

# Proxmox schreibt einen Zeitplan als Kalenderausdruck `[Wochentage] [[Jahr-]Monat-Tag] [Stunde:Minute]` (z. B.
# `21:00`, `*/2:00`, `sat 15:00`, `mon..fri 00:00`, `*-*-* 02:30,22:30`, `sat *-1..7 15:00`) oder als Kurzwort.
_ALIASES = {
    "minutely": 1,
    "hourly": 1,
    "daily": 1,
    "weekly": 7,
    "monthly": 31,
    "quarterly": 92,
    "semiannually": 184,
    "semi-annually": 184,
    "yearly": 366,
    "annually": 366,
}

_WEEKDAYS = {
    **{name: number for number, name in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))},
    **{name: number for number, name in enumerate(
        ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))},
}

_WEEKDAY_TOKEN = re.compile(r"[a-z.,]+")
_TIME_FIELD = re.compile(r"[0-9*/,.]+")
_DAY_STEP = re.compile(r"\*/(\d+)")

MONTH_DAYS = 31
"""Ist ein Tag im Monat festgelegt (`*-1..7`, `*-*-01`, `monthly`), zählt ein ganzer Monat."""


def _weekdays(token: str) -> set[int] | None:
    """`mon..fri`, `sat,sun`, `mon,wed..fri` -> Wochentage (Montag = 0); eine Spanne über den Sonntag hinaus
    (`fri..mon`) wird im Kreis gelesen."""
    days: set[int] = set()
    for part in token.split(","):
        bounds = part.split("..")
        if len(bounds) > 2 or any(bound not in _WEEKDAYS for bound in bounds):
            return None
        day, last = _WEEKDAYS[bounds[0]], _WEEKDAYS[bounds[-1]]
        days.add(day)
        while day != last:
            day = (day + 1) % 7
            days.add(day)
    return days


def _largest_weekday_gap(days: set[int]) -> int:
    """Größte Lücke zwischen den gewählten Tagen, im Kreis: `mon..fri` -> 3 (Freitag bis Montag), `sat` -> 7."""
    ordered = sorted(days)
    gaps = [later - earlier for earlier, later in zip(ordered, ordered[1:], strict=False)]
    gaps.append(ordered[0] + 7 - ordered[-1])
    return max(gaps)


def _time_ok(token: str) -> bool:
    parts = token.split(":")
    return len(parts) <= 3 and all(_TIME_FIELD.fullmatch(part) for part in parts)


def _date_days(token: str) -> int | None:
    """Tag-im-Monat-Teil: 0 = jeder Tag, `*/n` = n, jede feste Angabe = `MONTH_DAYS`; `None` = nicht lesbar
    (auch ein bestimmter Monat oder ein bestimmtes Jahr: das ist kein Rhythmus, den wir ableiten)."""
    fields = token.split("-")
    if len(fields) not in (2, 3) or (len(fields) == 3 and fields[0] != "*"):
        return None
    month, day = fields[-2], fields[-1]
    if month != "*" or not _TIME_FIELD.fullmatch(day):
        return None
    if day == "*":
        return 0
    step = _DAY_STEP.fullmatch(day)
    if step:
        return int(step.group(1)) if 1 <= int(step.group(1)) <= MONTH_DAYS else None
    return MONTH_DAYS


def schedule_gap_days(expression: object) -> int | None:
    """Größter Abstand zwischen zwei Läufen in ganzen Tagen (mindestens 1), `None` wenn der Ausdruck nicht
    eindeutig lesbar ist (es wird nie geraten)."""
    # Der größte Abstand, sonst kippt jede Sicherung eines seltenen Jobs zwischen zwei Läufen auf "zu alt". Wochentage:
    # größte Lücke (`mon..fri` 3, `sat` 7); Tag im Monat, auch mit Wochentagen (der erste Samstag): 31; sonst 1.
    if not isinstance(expression, str):
        return None
    text = expression.strip().lower()
    if text in _ALIASES:
        return _ALIASES[text]
    weekdays: set[int] | None = None
    month_days = 0
    seen: set[str] = set()
    for token in text.split():
        if _WEEKDAY_TOKEN.fullmatch(token):
            kind = "weekdays"
        elif ":" in token or "-" not in token:
            kind = "time"
        else:
            kind = "date"
        if kind in seen:
            return None
        seen.add(kind)
        if kind == "weekdays":
            weekdays = _weekdays(token)
            if weekdays is None:
                return None
        elif kind == "time":
            if not _time_ok(token):
                return None
        else:
            days = _date_days(token)
            if days is None:
                return None
            month_days = days
    if not seen:
        return None
    if month_days:
        return MONTH_DAYS if weekdays else month_days
    return _largest_weekday_gap(weekdays) if weekdays else 1
