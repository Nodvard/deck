"""Einrichtung und Schlüsselwechsel für signierte Updates."""

import subprocess
import sys
from pathlib import Path

import pytest

from nodvard_deck.core import updater_signing

ROOT = Path(__file__).resolve().parents[2]
RELEASE_DOC = ROOT / "docs/VEROEFFENTLICHEN.md"
# Die Datei fehlt im öffentlichen Export (export-ignore).
needs_release_doc = pytest.mark.skipif(not RELEASE_DOC.exists(), reason="docs/VEROEFFENTLICHEN.md gehört nicht zum öffentlichen Export")


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


@needs_release_doc
def test_release_instructions_cover_offline_keys_and_revocation():
    text = RELEASE_DOC.read_text(encoding="utf-8")
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


_DOC = """### Offline-Schlüssel und Schlüsselwechsel
Nico hält den Schlüssel offline und getrennt vom Rechner. Es gibt current und next.
Alte Helfer vertrauen dem neuen Schlüssel erst nach einem Update.
Ein Widerruf widerruft bereits heruntergeladene Freigaben nicht: widerruft bereits heruntergeladene Freigaben nicht
## Bausteinliste
"""


def _run_copy(tmp_path, with_doc):
    """Lässt eine Kopie dieser Datei in einem Baum mit oder ohne Anleitung laufen und gibt (Exit-Code, Ausgabe) zurück."""
    tests = tmp_path / "backend" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_copy.py").write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    if with_doc:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "VEROEFFENTLICHEN.md").write_text(_DOC, encoding="utf-8")
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rs", "-q", str(tests / "test_copy.py"),
         "-k", "release_instructions_cover"],
        capture_output=True, text=True, cwd=tmp_path, check=False,
    )
    return run.returncode, run.stdout


def test_release_instructions_test_skips_without_the_file(tmp_path):
    code, out = _run_copy(tmp_path, with_doc=False)
    assert code == 0 and "1 skipped" in out, out


def test_release_instructions_test_runs_with_the_file(tmp_path):
    code, out = _run_copy(tmp_path, with_doc=True)
    assert code == 0 and "1 passed" in out, out
