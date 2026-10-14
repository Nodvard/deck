"""Funde von Bandit (mittel und hoch): behoben oder an der Stelle als Fehlalarm begründet."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [ROOT / "backend" / "src", ROOT / "sdk" / "python" / "nodvard_sdk", ROOT / "deploy" / "updater"]
SOURCES += sorted((ROOT / "extensions").glob("*/src"))
SCRIPTS_SRC = ROOT / "extensions" / "scripts" / "src"


@pytest.fixture
def scripts_ext(monkeypatch):
    """Die Erweiterung ist kein installiertes Paket: Pfad nur für diesen Test setzen und die Module danach vergessen."""
    monkeypatch.syspath_prepend(str(SCRIPTS_SRC))
    import nodvard_deck_ext_scripts

    yield nodvard_deck_ext_scripts
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_scripts"):
            del sys.modules[name]


def _nosec_lines() -> list[tuple[str, int, str]]:
    found = []
    for base in SOURCES:
        for path in base.rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"#\s*nosec\b", line):
                    found.append((str(path.relative_to(ROOT)), number, line))
    return found


def test_every_nosec_names_the_check_and_only_the_known_false_alarms():
    # Ein `# nosec` ohne Kennung schaltet alle Prüfungen der Zeile ab; erlaubt sind nur die begründeten Fehlalarme.
    found = _nosec_lines()
    assert found, "die Fehlalarme sind markiert"
    for path, number, line in found:
        codes = re.findall(r"#\s*nosec\s+(B\d{3}(?:\s*,\s*B\d{3})*)", line)
        assert codes, f"{path}:{number}: nosec ohne Kennung"
        assert set(re.findall(r"B\d{3}", codes[0])) <= {"B104", "B108"}, f"{path}:{number}: {line.strip()}"


def test_the_rescue_page_listens_only_locally_without_an_address():
    from nodvard_deck import rescue

    assert rescue.build_parser().parse_args([]).host == "127.0.0.1"
    assert rescue.build_parser().parse_args(["--host", "0.0.0.0"]).host == "0.0.0.0"


def test_draft_ids_of_scripts_stay_the_same(scripts_ext):
    # `usedforsecurity=False` ändert nichts am Wert: vorhandene Entwürfe behalten ihre Kennung.
    fingerprint = "host-a|apt upgrade"
    expected = "auto-" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:10]
    assert scripts_ext._draft_id_for(fingerprint) == expected
