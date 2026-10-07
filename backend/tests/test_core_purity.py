"""Der Waechter `scripts/check_core_purity.py`: der Kern kennt keine Extension.

Geprueft werden die Sperrliste (Woerter wie Proxmox oder Ollama), das Wort "Shield" (mit den belegten Ausnahmen
`asyncio.shield(` und Icon-Namen wie "shield-check") und der Paket-Praefix `nodvard_deck_ext_` der Extensions. Die Beispiele
laufen gegen `check_file()` mit kleinen Dateien im tmp-Ordner; der echte Kern ist sauber, und die Ausnahmen darin sind genau die
bekannten drei.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_core_purity.py"
CORE_ROOT = REPO_ROOT / "backend" / "src" / "nodvard_deck"

_spec = importlib.util.spec_from_file_location("check_core_purity_script", SCRIPT)
purity = importlib.util.module_from_spec(_spec)
sys.modules["check_core_purity_script"] = purity
_spec.loader.exec_module(purity)


def words(tmp_path: Path, source: str) -> list[str]:
    """Die gefundenen Woerter fuer eine Datei mit diesem Quelltext (in der Reihenfolge ihrer Stellung)."""
    path = tmp_path / "modul.py"
    path.write_text(source, encoding="utf-8")
    return [word for _lineno, _line, word in purity.check_file(path)]


# --------------------------------------------------------------------------- der echte Kern


def test_the_core_is_clean():
    assert purity.main() == 0


def test_the_script_exits_with_zero_on_the_core():
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("OK")


def test_the_exceptions_for_shield_in_the_core_are_exactly_the_known_ones():
    """Jede Zeile im Kern (ohne Kommentare und Docstrings), in der `shield` ausgenommen wird, ist eine der drei bekannten Stellen.
    Kommt eine vierte dazu, muss jemand sie ansehen und hier eintragen."""
    exempted: dict[str, list[str]] = {}
    for path in sorted(CORE_ROOT.rglob("*.py")):
        code = purity._code_only(path.read_text(encoding="utf-8"))
        for line in code.splitlines():
            stripped = purity._strip_comments(line)
            if re.search(r"\bshield\b(?=[-(])", stripped, re.IGNORECASE):
                exempted.setdefault(path.relative_to(CORE_ROOT).as_posix(), []).append(stripped.strip())
    assert sorted(exempted) == ["core/demo_seed.py", "services/custom_apps.py", "services/restore.py"], exempted
    assert "asyncio.shield(" in exempted["services/restore.py"][0]
    assert '"shield-check"' in exempted["core/demo_seed.py"][0]
    assert '"shield-check"' in exempted["services/custom_apps.py"][0]
    # und sie schlagen nicht an
    for rel in exempted:
        assert purity.check_file(CORE_ROOT / rel) == [], rel


def test_the_core_does_not_name_an_extension_package():
    """Der Kern importiert keine Extension: kein `nodvard_deck_ext_...` im Code (Kommentare und Docstrings ausgenommen)."""
    offenders = [
        path.relative_to(CORE_ROOT).as_posix()
        for path in sorted(CORE_ROOT.rglob("*.py"))
        if re.search(r"\bnodvard_deck_ext_", purity._code_only(path.read_text(encoding="utf-8")))
    ]
    assert offenders == []


# --------------------------------------------------------------------------- Shield


@pytest.mark.parametrize(
    "source",
    [
        'LABEL = "Nodvard Shield"',
        "provider = 'shield'",
        "from somewhere import Shield",
        "x = Shield",
        "kind = shield  ",
        'URL = "/ext/shield/soc"',
        "name = 'SHIELD'",
        "run(shield, shield_id)",
    ],
)
def test_the_word_shield_is_forbidden_in_core_code(tmp_path, source):
    assert words(tmp_path, source + "\n") == ["shield"], source


@pytest.mark.parametrize(
    "source",
    [
        "done = await asyncio.shield(_spawn(work()))",
        "task = shield(inner())",
        'ICONS = ["globe", "shield-check", "lock"]',
        'row = ("Beispiel-Pi-hole", "http://192.0.2.12/admin", "shield-check", "Netzwerk")',
        'icon = "shield-alert"',
        "shielded = True",
        "shield_id = 1",
        "ShieldIcon = 1",
    ],
)
def test_the_known_exceptions_for_shield_do_not_match(tmp_path, source):
    assert words(tmp_path, source + "\n") == [], source


def test_shield_in_comments_and_docstrings_stays_allowed(tmp_path):
    source = (
        '"""Meldungen z. B. von Nodvard Shield."""\n'
        "\n"
        "\n"
        "def f():\n"
        '    """Die Erweiterung Nodvard Shield liefert Lageberichte."""\n'
        "    # reagiert, OHNE Nodvard Shield zu kennen\n"
        "    return 1\n"
        "\n"
        "\n"
        "x = 1\n"
        '"""Attribut-Docstring: Shield."""\n'
    )
    assert words(tmp_path, source) == []


def test_the_shield_rule_has_the_documented_form():
    assert purity.FORBIDDEN_PATTERNS["shield"] == r"\bshield\b(?![-(])"


# --------------------------------------------------------------------------- Paket-Praefix


@pytest.mark.parametrize(
    "source",
    [
        "import nodvard_deck_ext_proxmox",
        "from nodvard_deck_ext_shield import ids",
        "from nodvard_deck_ext_shield.ids import EXT_ID",
        'module = importlib.import_module("nodvard_deck_ext_hello_world")',
        'PREFIX = "nodvard_deck_ext_"',
        'sys.modules["nodvard_deck_ext_network.npm"]',
    ],
)
def test_the_core_does_not_import_or_name_an_extension_package(tmp_path, source):
    assert words(tmp_path, source + "\n") == ["nodvard_deck_ext_"], source


def test_the_package_prefix_in_prose_stays_allowed(tmp_path):
    source = (
        '"""Extensions heissen `nodvard_deck_ext_<id>`."""\n'
        "\n"
        "# Die Pakete tragen den Praefix nodvard_deck_ext_ (Konvention)\n"
        "x = 1\n"
    )
    assert words(tmp_path, source) == []


def test_the_core_package_name_is_not_an_extension_package(tmp_path):
    assert words(tmp_path, "from nodvard_deck.core import gate\nimport nodvard_deck\nimport nodvard_sdk\n") == []


# --------------------------------------------------------------------------- die alte Sperrliste gilt weiter


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import proxmoxer", []),  # nur als ganzes Wort
        ('host = "proxmox"', ["proxmox"]),
        ("url = 'http://ollama:11434'", ["ollama"]),
        ("OLLAMA_URL = 'x'", []),  # Unterstrich: ein Wort, wie bisher
        ("client = Docker()", ["docker"]),
        ("send_ntfy(msg)", []),  # `send_ntfy` ist ein Wort (Unterstrich), wie bisher
        ("notify(ntfy, msg)", ["ntfy"]),
        ('x = "proxmox shield"', ["proxmox", "shield"]),
        ('x = "shield proxmox nodvard_deck_ext_x"', ["shield", "proxmox", "nodvard_deck_ext_"]),  # in der Reihenfolge der Stellung
    ],
)
def test_the_old_word_list_still_applies(tmp_path, source, expected):
    assert words(tmp_path, source + "\n") == expected, source


def test_the_line_number_and_the_line_are_reported(tmp_path):
    path = tmp_path / "modul.py"
    path.write_text('a = 1\nb = "Shield"\n', encoding="utf-8")
    assert purity.check_file(path) == [(2, 'b = "Shield"', "shield")]
