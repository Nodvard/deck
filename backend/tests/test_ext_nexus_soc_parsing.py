"""Regressionstests fuer `parse_ai_response()`/`strip_decision_block()` -- geschrieben
GEGEN die real dokumentierten Fehlerbilder aus dem Vorgaengersystem ("Regressionstests
zuerst"), bevor `parsing.py` als "fertig" gilt. Reine Funktionen, kein I/O -- kein `client`/
`db_session` noetig, nur `sys.path` auf die Extension gerichtet.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

NEXUS_SOC_SRC = Path(__file__).resolve().parents[2] / "extensions" / "nexus-soc" / "src"
if str(NEXUS_SOC_SRC) not in sys.path:
    sys.path.insert(0, str(NEXUS_SOC_SRC))

from nodvard_deck_ext_nexus_soc.parsing import ParsedActionKind, parse_ai_response, strip_decision_block  # noqa: E402


def test_aktion_keine_yields_no_proposal():
    text = "📌 **Lagebericht:** Alles gut.\n⚡ **NEXUS-Entscheidung:**\nBEGRUENDUNG: kein Handlungsbedarf\nAKTION: KEINE"
    assert parse_ai_response(text) == []


def test_missing_aktion_field_yields_no_proposal():
    """Ein Modelltext ohne erkennbares AKTION-Feld schlaegt NICHTS vor -- konservativ,
    kein Rateversuch."""
    assert parse_ai_response("Der Server laeuft normal, keine Auffaelligkeiten.") == []


def test_single_line_output_does_not_leak_trailing_section_into_command():
    """qwen2.5:7b gibt manchmal alles auf EINER Zeile aus --
    ohne Zeilenumbruch griff der gierige `(.+)`-Regex des Vorgaengersystems bis zum Zeilenende und
    zog Status-Emoji/den naechsten Abschnitt in den auszufuehrenden Befehl."""
    text = (
        "📌 Lagebericht: nextcloud-app ist abgestuerzt. "
        "⚡ NEXUS-Entscheidung: BEGRUENDUNG: Container reagiert nicht mehr "
        "AKTION: EXEC docker docker restart nextcloud-app 🟢 System-Status: kritisch"
    )
    actions = parse_ai_response(text)
    assert len(actions) == 1
    assert actions[0].kind == ParsedActionKind.EXEC
    assert actions[0].command == "docker restart nextcloud-app"
    assert "🟢" not in actions[0].command
    assert "System-Status" not in actions[0].command
    assert actions[0].reason == "Container reagiert nicht mehr"


def test_begruendung_typo_without_second_e_is_still_recognized():
    """`startswith` erkannte nur `BEGRUENDUNG:`, nicht den in
    echten Modellantworten beobachteten Tippfehler `BEGRUNDUNG:` (fehlendes zweites
    E)."""
    text = "⚡ NEXUS-Entscheidung:\nBEGRUNDUNG: Speicher voll\nAKTION: EXEC docker docker restart plex"
    actions = parse_ai_response(text)
    assert len(actions) == 1
    assert actions[0].reason == "Speicher voll"


def test_negation_in_prose_is_not_misread_as_an_action():
    """'es gibt keine direkten Fehlermeldungen' erwaehnt weder AKTION: KEINE noch
    AKTION: EXEC woertlich -- ohne ein AKTION-Feld darf trotzdem NICHTS vorgeschlagen
    werden (fehlende Negationserkennung ist ein bekannter Risikofall)."""
    text = "📌 Lagebericht: Es gibt keine direkten Fehlermeldungen in den Logs, der Dienst laeuft normal weiter."
    assert parse_ai_response(text) == []


def test_docker_restart_targeting_a_forbidden_host_keyword_is_rejected():
    """Der real beobachtete Fehlschluss: 'Host nicht erreichbar' -> KI schlaegt
    faelschlich einen Container-Neustart FUER DEN HOST SELBST vor."""
    text = (
        "⚡ NEXUS-Entscheidung:\nBEGRUENDUNG: pve2 scheint offline zu sein\n"
        "AKTION: EXEC docker docker restart power_server"
    )
    actions = parse_ai_response(text)
    assert len(actions) == 1
    assert actions[0].kind == ParsedActionKind.REJECTED
    assert "power_server" in actions[0].rejection_reason


def test_placeholder_command_is_never_proposed():
    text = "⚡ NEXUS-Entscheidung:\nBEGRUENDUNG: Test\nAKTION: EXEC <host> docker restart <target>"
    assert parse_ai_response(text) == []


def test_well_formed_multiline_response_parses_cleanly():
    text = (
        "📌 **Lagebericht:** nginx ist wegen eines Speicherfehlers abgestuerzt.\n"
        "⚡ **NEXUS-Entscheidung:**\n"
        "BEGRUENDUNG: Wiederholter Absturz durch OOM, ein Neustart behebt das kurzfristig\n"
        "AKTION: EXEC docker docker restart nginx-proxy"
    )
    actions = parse_ai_response(text)
    assert len(actions) == 1
    action = actions[0]
    assert action.kind == ParsedActionKind.EXEC
    assert action.host == "docker"
    assert action.command == "docker restart nginx-proxy"
    assert action.reason == "Wiederholter Absturz durch OOM, ein Neustart behebt das kurzfristig"


def test_custom_forbidden_keywords_override_default():
    text = "⚡ NEXUS-Entscheidung:\nBEGRUENDUNG: x\nAKTION: EXEC docker docker restart my-custom-blocked-name"
    actions = parse_ai_response(text, forbidden_keywords=("blocked",))
    assert actions[0].kind == ParsedActionKind.REJECTED

    # Ein Wort, das NUR im Default steht (nicht in der uebergebenen Liste), blockt hier nicht mehr.
    text2 = "⚡ NEXUS-Entscheidung:\nBEGRUENDUNG: x\nAKTION: EXEC docker docker restart pve2-thing"
    actions2 = parse_ai_response(text2, forbidden_keywords=("blocked",))
    assert actions2[0].kind == ParsedActionKind.EXEC


@pytest.mark.parametrize(
    "aktion_line",
    ["AKTION: NONE", "aktion: keine", "AKTION:KEINE"],
)
def test_aktion_none_is_case_and_spacing_insensitive(aktion_line):
    assert parse_ai_response(f"Text davor.\n{aktion_line}\nText danach.") == []


def test_strip_decision_block_removes_raw_markers_for_display():
    raw = (
        "📌 **Lagebericht:** nginx ist abgestuerzt.\n"
        "⚡ **NEXUS-Entscheidung:**\n"
        "BEGRUENDUNG: OOM\n"
        "AKTION: EXEC docker docker restart nginx-proxy\n"
        "🟢 **System-Status:** kritisch"
    )
    display = strip_decision_block(raw)
    assert "BEGRUENDUNG" not in display
    assert "AKTION" not in display
    assert "nginx ist abgestuerzt" in display
    assert "kritisch" in display


@pytest.mark.parametrize("marker", ["NODVARD-Entscheidung", "NEXUS-Entscheidung", "NODVARD-Aktion", "NEXUS-Aktion"])
def test_strip_decision_block_erkennt_neuen_und_alten_marker(marker):
    """Das Modell schreibt jetzt "NODVARD-Entscheidung"; Antworten mit dem frueheren Marker
    (gespeicherte Texte, kleine Modelle, die den alten Prompt kennen) werden weiter bereinigt."""
    raw = (
        "📌 **Lagebericht:** nginx ist abgestuerzt.\n"
        f"⚡ **{marker}:**\n"
        "BEGRUENDUNG: OOM\n"
        "AKTION: EXEC docker docker restart nginx-proxy\n"
        "🟢 **System-Status:** kritisch"
    )
    display = strip_decision_block(raw)
    assert marker not in display
    assert "BEGRUENDUNG" not in display
    assert "AKTION" not in display
    assert "nginx ist abgestuerzt" in display
    assert "kritisch" in display


@pytest.mark.parametrize("marker", ["NODVARD-Entscheidung", "NEXUS-Entscheidung"])
def test_parse_ai_response_mit_neuem_und_altem_marker(marker):
    text = f"⚡ {marker}: BEGRUENDUNG: Container reagiert nicht mehr AKTION: EXEC docker docker restart plex 🟢 System-Status: kritisch"
    actions = parse_ai_response(text)
    assert len(actions) == 1
    assert actions[0].kind == ParsedActionKind.EXEC
    assert actions[0].command == "docker restart plex"
    assert actions[0].reason == "Container reagiert nicht mehr"
    keine = f"⚡ **{marker}:**\nBEGRUENDUNG: nichts zu tun\nAKTION: KEINE"
    assert parse_ai_response(keine) == []
