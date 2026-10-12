"""Nodvard Shield liest Zahlen aus den Einstellungen nur innerhalb der Grenzen aus settings.schema.json.

Seit das Backend die Grenzen beim Speichern prueft, kommt kein neuer Wert ausserhalb mehr an. Ein Wert, der vorher
gespeichert wurde (0, -5, 5000), bleibt aber stehen und landete sonst im Scan-Befehl auf dem Server oder liesse den
Einbruchschutz bei jedem Fehlversuch warnen."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from nodvard_deck_ext_shield.defender import max_filesize_mb, watch_interval_min
from nodvard_deck_ext_shield.guard import bruteforce_threshold

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "extensions" / "shield" / "settings.schema.json").read_text(encoding="utf-8")
)["properties"]


@pytest.mark.parametrize(("read", "key"), [
    (max_filesize_mb, "max_filesize_mb"),
    (watch_interval_min, "watch_interval_min"),
    (bruteforce_threshold, "bruteforce_threshold"),
])
def test_old_values_are_clamped_to_the_schema_bounds(read, key):
    spec = SCHEMA[key]
    low, high, default = spec["minimum"], spec["maximum"], spec["default"]
    assert read({}) == default
    assert read({key: 0}) == default  # 0 hiess schon immer "Standard"
    assert read({key: -5}) == low
    assert read({key: low}) == low
    if read is not bruteforce_threshold:  # nach oben ist eine hohe Schwelle harmlos (es warnt nur seltener)
        assert read({key: high}) == high
        assert read({key: high + 1}) == high


def test_the_largest_file_size_keeps_the_scan_size_within_what_clamav_accepts():
    from nodvard_deck_ext_shield import antivirus as av

    command = av.build_scan_command(["/srv"], max_filesize_mb=max_filesize_mb({"max_filesize_mb": 5000}))
    assert "--max-filesize=1000M" in command and "--max-scansize=4000M" in command


@pytest.mark.parametrize(("value", "expected"), [(7, 6), (8, 6), (11, 10), (25, 20), (45, 30), (59, 30), (60, 30), (1, 2)])
def test_an_old_watch_interval_runs_with_the_next_smaller_divisor_of_60(value, expected):
    """`*/7` liefe um :49, :56 und gleich wieder um :00; die Auswahl bietet nur noch Teiler von 60 an."""
    assert watch_interval_min({"watch_interval_min": value}) == expected


def test_the_guard_interval_follows_the_same_rule():
    from nodvard_deck_ext_shield.guard import guard_interval_min

    assert guard_interval_min({}) == 15
    assert guard_interval_min({"guard_interval_min": 7}) == 6
    assert guard_interval_min({"guard_interval_min": 3}) == 5
    assert guard_interval_min({"guard_interval_min": 59}) == 30
