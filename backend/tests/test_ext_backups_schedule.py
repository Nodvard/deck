"""Abstand der Laeufe eines Backup-Jobs aus seinem Proxmox-Zeitplan (`schedule_gap_days`) und die Grenzen der Einstellung
"Backup zu alt" -- Schema und Waechter muessen dieselbe Untergrenze kennen."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "backups"
SCHEMA = json.loads((EXT_DIR / "settings.schema.json").read_text(encoding="utf-8"))["properties"]


@pytest.fixture(autouse=True)
def _ext_backups_on_path(monkeypatch):
    """Die Erweiterung ist kein installiertes Paket (CI installiert nur sdk und backend): Pfad nur fuer
    diese Tests setzen und die Module danach wieder vergessen, wie die anderen Backup-Tests."""
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_backups"):
            del sys.modules[name]


def schedule_gap_days(expression):
    from nodvard_deck_ext_backups.schedule import schedule_gap_days as gap

    return gap(expression)


def _watch():
    from nodvard_deck_ext_backups import watch

    return watch


@pytest.mark.parametrize(
    ("expression", "days"),
    [
        # Beispiele aus der Proxmox-Oberflaeche
        ("21:00", 1),
        ("*/2:00", 1),
        ("sat 15:00", 7),
        ("mon..fri 00:00", 3),
        ("mon,wed,fri 02:30", 3),
        ("sat *-1..7 15:00", 31),
        ("daily", 1),
        ("weekly", 7),
        ("monthly", 31),
        # Wochentage: groesste Luecke im Kreis
        ("sun,wed", 4),
        ("sat,sun 03:00", 6),
        ("mon..sun 03:00", 1),
        ("mon,tue,wed,thu,fri 01:00", 3),
        ("fri..mon 01:00", 4),
        ("Sat 15:00", 7),
        ("saturday 15:00", 7),
        ("mon,wed..fri", 3),
        # mehrere Zeiten am Tag, Datumsteil mit Stern, Minuten ohne Stunde
        ("*-*-* 02:30,22:30", 1),
        ("*-*-*", 1),
        ("*/5", 1),
        ("mon..fri 8..17,22:0/15", 3),
        ("  21:00  ", 1),
        # fester Tag im Monat: ein Monat; Schritt in Tagen: der Schritt
        ("*-*-01", 31),
        ("*-05", 31),
        ("*-*-1,15 02:00", 31),
        ("sat *-*-01..07 02:00", 31),
        ("*-*-*/2 02:00", 2),
        # Kurzwoerter
        ("hourly", 1),
        ("minutely", 1),
        ("quarterly", 92),
        ("yearly", 366),
    ],
)
def test_the_gap_is_read_from_proxmox_schedules(expression, days):
    assert schedule_gap_days(expression) == days


@pytest.mark.parametrize(
    "expression",
    [
        None,
        42,
        "",
        "   ",
        "foo",
        "foo bar",
        "mon..xyz 02:00",
        "mon,,tue",
        "mon..tue..wed",
        "sat sun",  # Wochentage zweimal
        "21:00 22:00",  # Zeit zweimal
        "2015-10-21",  # ein einzelner Tag, kein Rhythmus
        "*-1,7-1",  # nur bestimmte Monate
        "*-*-*/0",
        "*-*-*/40",
        "ab:cd",
        "mon 1:2:3:4",
    ],
)
def test_an_expression_that_cannot_be_read_gives_none(expression):
    assert schedule_gap_days(expression) is None


def test_the_schema_minimum_is_the_one_the_watcher_uses():
    watch = _watch()
    spec = SCHEMA["backup_max_age_days"]
    assert spec["minimum"] == watch.AGE_MAX_DAYS_MIN == 2
    assert spec["maximum"] == watch.AGE_MAX_DAYS_MAX
    assert spec["default"] == watch.AGE_MAX_DAYS_DEFAULT
    assert spec["minimum"] <= spec["default"] <= spec["maximum"]


@pytest.mark.parametrize(
    ("raw", "days"),
    [(1, 2), (2, 2), (3, 3), (60, 60), (0, 3), (-3, 3), (61, 3), ("5", 3), (2.5, 3), (True, 3), (None, 3)],
)
def test_a_stored_one_day_counts_as_two_and_other_wrong_values_as_the_default(raw, days):
    assert _watch()._setting_days(raw) == days
