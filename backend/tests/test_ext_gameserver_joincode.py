"""`nodvard_deck_ext_gameserver.joincode.extract_join_code` -- reine Textverarbeitung,
kein `ctx`/kein Netzwerk."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "gameserver" / "src"))

from nodvard_deck_ext_gameserver.joincode import DEFAULT_JOIN_CODE_PATTERN, extract_join_code  # noqa: E402


def test_extracts_code_from_a_realistic_log_line():
    line = "[12:00:03] Session 'MalteIstGay' with join code 7F3K9QZ2 registered with join code 7F3K9QZ2"
    assert extract_join_code(line) == "7F3K9QZ2"


def test_returns_none_for_empty_output():
    assert extract_join_code("") is None
    assert extract_join_code("   \n") is None


def test_returns_none_when_pattern_does_not_match():
    assert extract_join_code("Server started, waiting for world data...") is None


def test_is_case_insensitive():
    assert extract_join_code("Join Code: ab12cd34") == "ab12cd34"


def test_custom_pattern_overrides_default():
    assert extract_join_code("CODE=ZZZZ9999", pattern=r"CODE=([A-Z0-9]+)") == "ZZZZ9999"
    assert extract_join_code("join code AAAA1111", pattern=r"CODE=([A-Z0-9]+)") is None


def test_default_pattern_is_exported_and_usable_directly():
    assert extract_join_code("registered with join code QW12ER34", pattern=DEFAULT_JOIN_CODE_PATTERN) == "QW12ER34"


def test_default_pattern_and_log_command_verified_against_the_real_valheim_win_log():
    """`DEFAULT_JOIN_CODE_
    PATTERN`/`DEFAULT_LOG_COMMAND` waren beim Schreiben eine Annahme ("kein echtes
    Log-Beispiel lag vor", siehe joincode.py-Docstring). Live gegen die echte
    `C:\\valheim\\logs\\service-out.log` auf einem Windows-Spielserver per SSH geprueft --
    `DEFAULT_LOG_COMMAND` unveraendert ausgefuehrt, drei echte Log-Zeilen zurueck
    (Timestamps/Codes real, hier als Fixture eingefroren). Ergebnis: Annahme war
    korrekt, keine Code-Aenderung noetig -- dieser Test macht das dauerhaft
    nachpruefbar statt nur einmalig live behauptet."""
    real_lines = [
        '09/18/2026 00:27:38: Session "Valheim" with join code 493063 and IP 203.0.113.50:2456 is active with 0 player(s)',
        '09/18/2026 00:28:26: Session "Valheim" registered with join code 493063',
        '09/18/2026 00:28:26: Created new join code 791652 for session "Valheim"',
    ]
    assert [extract_join_code(line) for line in real_lines] == ["493063", "493063", "791652"]
    # DEFAULT_LOG_COMMAND selektiert per `Select-Object -Last 1` -- der aktuell
    # gueltige Code ist der der ZULETZT geschriebenen Zeile, nicht der erste Treffer.
    assert extract_join_code(real_lines[-1]) == "791652"
