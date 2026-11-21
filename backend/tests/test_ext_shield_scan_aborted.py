"""Shield: Funde eines abgebrochenen Scans bekommen den Hinweis zum Abbruch, nicht den zum Zeilenumbruch."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"

MARK = "@@scan-rc-0123456789abcdef="
TIMEOUT = "@@scan-timeout-0123456789abcdef"
FOUND = (
    "/var/tmp/payload.bin: Unix.Trojan.Mirai-9798316-0 FOUND\n"
    "/home/kali/Downloads/x.sh: Unix.Trojan.Agent-7437455-0 FOUND\n"
)
SUMMARY = "\n----------- SCAN SUMMARY -----------\nScanned files: 3412\nInfected files: 2\n"


@pytest.fixture
def mods(monkeypatch):
    monkeypatch.syspath_prepend(str(SRC))
    from nodvard_deck_ext_shield import antivirus, defender

    yield SimpleNamespace(av=antivirus, defender=defender, four_hours=defender.SCAN_TIMEOUTS["deep"])
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


def test_findings_without_end_mark_after_the_time_limit_name_the_abort(mods):
    av, FOUR_HOURS = mods.av, mods.four_hours
    res = av.parse_scan_output(FOUND + TIMEOUT + "\n", MARK, ["/"], FOUR_HOURS)
    assert res.status == "infected" and res.infected == 2
    assert res.unreliable is True and res.timed_out is False
    assert "nach etwa 4 Stunden abgebrochen" in res.hint
    assert "nichts automatisch verschoben" in res.hint and "Ordner" in res.hint
    assert res.aborted is True and "stehen unten" not in res.hint
    assert "Zeilenumbruch" not in res.hint


def test_findings_without_any_ending_name_the_abort_without_a_duration(mods):
    av, FOUR_HOURS = mods.av, mods.four_hours
    res = av.parse_scan_output(FOUND, MARK, ["/"], FOUR_HOURS)
    assert res.unreliable is True and res.hint == av.aborted_message()
    assert "nach etwa" not in res.hint and "Tipp" not in res.hint


def test_a_finished_scan_has_no_hint(mods):
    av, FOUR_HOURS = mods.av, mods.four_hours
    res = av.parse_scan_output(FOUND + SUMMARY + f"{MARK}1\n", MARK, ["/"], FOUR_HOURS)
    assert res.unreliable is False and res.hint is None


def test_a_missing_mark_with_odd_lines_keeps_the_line_break_hint(mods):
    av, FOUR_HOURS = mods.av, mods.four_hours
    out = "/tmp/q\n/tmp/rest: Unix.Trojan.X FOUND\n" + TIMEOUT + "\n"
    res = av.parse_scan_output(out, MARK, ["/tmp"], FOUR_HOURS)
    assert res.unreliable is True and res.hint is None


def test_the_scan_hint_uses_the_abort_text(mods):
    av, defender, FOUR_HOURS = mods.av, mods.defender, mods.four_hours
    res = av.parse_scan_output(FOUND + TIMEOUT + "\n", MARK, ["/"], FOUR_HOURS)
    assert defender._unreliable_hint(res) == av.aborted_message(FOUR_HOURS)


def test_the_scan_hint_keeps_the_capped_sentence_next_to_the_abort(mods):
    av, defender, FOUR_HOURS = mods.av, mods.defender, mods.four_hours
    res = av.parse_scan_output(FOUND + "Skipped files: 7\n", MARK, ["/"], FOUR_HOURS)
    hint = defender._unreliable_hint(res)
    assert "abgebrochen" in hint and "2000 von 2007" in hint


def test_without_an_abort_the_generic_hint_stays(mods):
    av, defender, FOUR_HOURS = mods.av, mods.defender, mods.four_hours
    out = "/tmp/q\n/tmp/rest: Unix.Trojan.X FOUND\n" + TIMEOUT + "\n"
    res = av.parse_scan_output(out, MARK, ["/tmp"], FOUR_HOURS)
    assert res.aborted is False and defender._unreliable_hint(res) == defender.UNRELIABLE_HINT
