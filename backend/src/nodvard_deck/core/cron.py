"""Cron-Ausdruecke fuer Zeitplaene und Wartungsfenster (ein Ort fuer alle).

Gespeichert und im Zeitplan-Waehler angezeigt wird normales Crontab: im Wochentagsfeld
sind 0 **und** 7 = Sonntag, 1 = Montag ... 6 = Samstag. APSchedulers
`CronTrigger.from_crontab` zaehlt dagegen selbst (0 = Montag) und lehnt die 7 ab -- jeder
Zeitplan mit Wochentag lief dadurch einen Tag zu spaet. Dieses Modul uebersetzt das
Wochentagsfeld in APSchedulers Namen (`sun`, `mon`, ...); die anderen vier Felder
bleiben unangetastet."""

from __future__ import annotations

from datetime import tzinfo

from apscheduler.triggers.cron import CronTrigger

# Index = Crontab-Nummer (0 = Sonntag); die 7 wird beim Aufloesen auf 0 gelegt.
_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def _bad(field: str, why: str) -> ValueError:
    return ValueError(f"Ungültiger Wochentag „{field}“: {why}")


def _number(token: str, field: str) -> int:
    token = token.strip().lower()
    if token in _NAMES:
        return _NAMES.index(token)
    if token.isascii() and token.isdigit():
        value = int(token)
        if 0 <= value <= 7:
            return value
        raise _bad(field, "erlaubt sind 0 bis 7 (0 und 7 = Sonntag) oder mon bis sun")
    raise _bad(field, "erlaubt sind 0 bis 7 (0 und 7 = Sonntag) oder mon bis sun")


def _expand_part(part: str, field: str) -> list[int]:
    if not part:
        raise _bad(field, "leerer Eintrag")
    base, sep, step_text = part.partition("/")
    step = 1
    if sep:
        if not (step_text.isascii() and step_text.isdigit()) or int(step_text) < 1:
            raise _bad(field, "der Schritt nach „/“ muss eine Zahl ab 1 sein")
        step = int(step_text)

    if base == "*":
        start, end = 0, 6
    elif "-" in base:
        first, _, last = base.partition("-")
        start, end = _number(first, field), _number(last, field)
        if end < start:  # Bereich ueber das Wochenende hinweg, z. B. 6-1 oder fri-mon
            end += 7
    else:
        start = _number(base, field)
        end = start if not sep else (6 if start == 0 else 7)
    return [value % 7 for value in range(start, end + 1, step)]


def translate_day_of_week(field: str) -> str:
    """Crontab-Wochentagsfeld -> APScheduler-Feld (`sun`, `mon`, ...).

    Zahlen 0-7 (0 und 7 = Sonntag), Namen, Listen, Bereiche (auch ueber das Wochenende,
    z. B. 5-7 oder sun-tue) und Schritte werden zu einer expliziten Namensliste; nur `*`
    bleibt. Auch reine Namensbereiche werden aufgeloest: APScheduler zaehlt ab Montag und
    lehnt z. B. `sun-tue` ab. Ungueltiges -> ValueError mit deutscher Meldung."""
    text = field.strip()
    if not text:
        raise _bad(field, "das Feld ist leer")
    if text == "*":
        return "*"

    parts = [part.strip() for part in text.split(",")]
    days: list[int] = []
    for part in parts:
        for day in _expand_part(part, field):
            if day not in days:
                days.append(day)
    return ",".join(_NAMES[day] for day in days)


def cron_trigger(expr: str, timezone: str | tzinfo | None = None) -> CronTrigger:
    """Wie `CronTrigger.from_crontab`, aber mit Crontab-Wochentagen (0/7 = Sonntag)."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"Ein Cron-Ausdruck braucht genau 5 Felder (Minute Stunde Tag Monat Wochentag), gefunden: {len(fields)}.")
    fields[4] = translate_day_of_week(fields[4])
    return CronTrigger.from_crontab(" ".join(fields), timezone=timezone)
