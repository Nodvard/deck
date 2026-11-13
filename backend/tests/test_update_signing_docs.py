"""Einrichtung und Schlüsselwechsel für signierte Updates."""

from pathlib import Path

from nodvard_deck.core import updater_signing

ROOT = Path(__file__).resolve().parents[2]


def test_installation_instructions_cover_both_sides():
    text = (ROOT / "deploy/README.md").read_text(encoding="utf-8")
    section = text.split("### Schlüssel für Update-Anforderungen", 1)[1].split("## Demo-Modus", 1)[0]
    assert "python -m nodvard_deck.core.updater_signing" in section
    assert "updater:/state/request-key.pub" in section
    assert updater_signing.KEY_NAME in section
    assert "0600" in section and "root gehören" in section
    assert "alten Schlüssel" in section and "Sicherung" in section
    assert "Die Freigabe ist noch nicht Pflicht" not in text
    assert "fremde Schreiber" in section and "Übernahme des Decks" in section
    assert "Auch der Rückweg ist signiert" in section


def test_release_instructions_cover_offline_keys_and_revocation():
    text = (ROOT / "docs/VEROEFFENTLICHEN.md").read_text(encoding="utf-8")
    section = text.split("### Offline-Schlüssel und Schlüsselwechsel", 1)[1].split("## Bausteinliste", 1)[0]
    assert "Nico" in section and "offline" in section and "getrennt" in section
    assert "current" in section and "next" in section
    assert "Alte Helfer vertrauen" in section
    assert "widerruft bereits heruntergeladene Freigaben nicht" in section


def test_api_documents_the_request_signature_and_refuses_legacy_updates():
    text = (ROOT / "docs/04-API.md").read_text(encoding="utf-8")
    assert "request_key_id" in text and "request_sig" in text
    assert "nodvard-deck-update-request-v1" in text
    assert "409 signature_required" in text
    assert "ein älterer Helfer bekommt `v: 1`" not in text
    assert "Jedes Update bringt eine signierte" in text
    assert "kommt erst mit signierten Anforderungen" not in text
    assert "ohne Signatur aus" not in text
