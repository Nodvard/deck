"""Jeder Code des Update-Helfers hat in der Oberflaeche einen Text (`frontend/src/routes/settings/updaterTexts.ts`).

Die API liefert nur feste Codes; die Saetze stehen im Frontend. Dieser Test liest die Codes direkt aus dem Helfer
(`deploy/updater/nodvard_deck_updater/policy.py`) und aus der Ansicht des Dashboards und vergleicht sie mit den
Schluesseln der Tabellen in `updaterTexts.ts`: Kommt im Helfer ein Code dazu (oder faellt einer weg), schlaegt er fehl,
bis die Oberflaeche nachgezogen ist. Gegenstueck im Frontend: `updaterTexts.test.ts` (liest protocol.json/status.json).
"""

from __future__ import annotations

import re
from pathlib import Path

from nodvard_deck.core import updater_client
from nodvard_deck.services import update_helper
from updater_helpers import helper_module

TEXTS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "routes" / "settings" / "updaterTexts.ts"


def _table(name: str) -> dict[str, str]:
    """Schluessel und Text einer Tabelle `export const <name>: Record<...> = { ... };` (ein Schluessel je Zeile, zwei
    Leerzeichen eingerueckt; der Text darf in der naechsten Zeile stehen)."""
    source = TEXTS.read_text(encoding="utf-8")
    match = re.search(rf"^export const {name}\b[^=]*= \{{\n(.*?)^\}};", source, re.MULTILINE | re.DOTALL)
    assert match, f"Tabelle {name} fehlt in {TEXTS.name}"
    table: dict[str, str] = {}
    key = None
    for line in match.group(1).splitlines():
        found = re.match(r"^  ([a-z_]+):\s*(.*)$", line)
        if found:
            key = found.group(1)
            assert key not in table, f"{name}: {key} doppelt"
            table[key] = found.group(2)
        elif key is not None and line.strip() and not line.strip().startswith("//"):
            table[key] += line.strip()
    return table


def test_every_code_of_the_helper_has_a_text_and_no_stale_ones():
    policy = helper_module("policy")
    texts = _table("HELPER_CODE_TEXTS")
    assert set(texts) == set(policy.CODES), {"fehlt": sorted(set(policy.CODES) - set(texts)),
                                             "ueberzaehlig": sorted(set(texts) - set(policy.CODES))}
    for code, text in texts.items():
        assert len(text) > 20, code


def test_every_step_and_outcome_has_a_text():
    policy = helper_module("policy")
    assert list(_table("STEP_TEXTS")) == list(policy.STEPS)
    assert list(_table("OUTCOME_TITLES")) == list(policy.OUTCOMES)
    assert list(_table("OUTCOME_TONES")) == list(policy.OUTCOMES)


def test_the_reasons_of_the_dashboard_view_have_a_text():
    presence = _table("PRESENCE_TEXTS")
    assert set(presence) == {updater_client.MISSING, updater_client.STALE, updater_client.UNSAFE, updater_client.PROTO,
                             updater_client.INVALID}
    assert update_helper.FINISHING in _table("VIEW_REASON_TEXTS")


def test_the_minimum_version_in_the_text_is_the_one_of_the_helper():
    policy = helper_module("policy")
    assert f"mindestens {policy.MIN_VERSION}" in _table("HELPER_CODE_TEXTS")["version_too_old"]


def test_texts_address_the_user_informally():
    """Du-Form wie ueberall in Nodvard Deck: kein „Sie“/„Ihr“ als Anrede."""
    source = TEXTS.read_text(encoding="utf-8")
    strings = re.findall(r'"([^"\n]*)"|`([^`]*)`', source)
    for text in (a or b for a, b in strings):
        assert not re.search(r"\b(Sie|Ihnen|Ihre?[nmrs]?)\b", text), text


def test_the_block_after_a_rollback_in_the_card_is_the_one_of_the_helper():
    """Die Karte Updates zeigt nach einem Rueckweg keinen Knopf fuer die gesperrte Version, so lange wie der Helfer sperrt."""
    policy = helper_module("policy")
    card = (TEXTS.parent / "UpdatesCard.tsx").read_text(encoding="utf-8")
    match = re.search(r"^export const BLOCK_AFTER_ROLLBACK_S = ([0-9 *]+);", card, re.MULTILINE)
    assert match, "BLOCK_AFTER_ROLLBACK_S fehlt in UpdatesCard.tsx"
    seconds = 1
    for factor in match.group(1).split("*"):
        seconds *= int(factor)
    assert seconds == policy.BLOCK_AFTER_ROLLBACK_S
