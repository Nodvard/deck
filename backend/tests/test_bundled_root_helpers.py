"""Die mitgelieferten Hilfsskripte (`extensions/<id>/root-helper/<id>-root.sh`): Der Kern nimmt sie an, sie laufen mit
`sh` und `dash`, kennen nur ihre festen Unterbefehle, und jede Änderung am Text braucht eine neue Version.

Liest nur Dateien und Manifeste (kein Import der Module)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nodvard_deck.ext import discovery
from nodvard_deck.ext.root_helpers import MAX_HELPER_BYTES, RootHelperFile, read_root_helper

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"

PINNED = {
    ("shield", 1): "56f2bbd11188f6321d2ecca26d84db614bcf7c0814326f262d687b90264652ef",
    ("shield", 2): "06cbf646edf4ee9bc9e265b226b56d4d71822c2ee67d9a9d5d61c7f234c58d14",
    ("shield", 3): "40ba30ad425c6b28600a2838a844a122305911c9533f7df98784c4ae59ce004b",
    ("shield", 4): "2b9bffc8917b4e577b182b0ea74c6df6695a027debee5b23260a749cd7f55fde",
    ("shield", 5): "cac87e20526537fae116290cdf4617b1022a63fd75b2affd5bdca043bde3a47f",
    ("shield", 6): "4baf012fb416bb379a1daa7a86a37b4e224c73c106809766ec8b6e1513089d82",
    ("shield", 7): "54770d1dcd354a8128de17ae3bfa9921f94d798ef4b002fd45037a462d7429cd",
    ("shield", 8): "71831cb3321faed7a0e8eeed95738a7f3dc9b5ca35b0b93601c421377798343b",
    ("shield", 9): "e43e74ef220bfef0688ce445dad1dcb132e082879b124b58445e87e142e7bdb1",
    ("shield", 10): "fe89e36676a35c5d79b60b0a487304b9ff2d16771bb2101a04204304f5c71c60",
    ("shield", 11): "1874a17edd615a3b28d5b7d98ec9114df96ce5ac09085a4636887a870764ce2c",
    ("shield", 12): "1dcd2653cde62af3ea611f97f268f0f421df5e488553f24d6a6daa23e095f886",
    ("shield", 13): "9b24a3da3355d3daf1a47370df5e8cf3912e85ac7540432e0940e736ff78b4cb",
    ("shield", 14): "d41c9c5a3fcc99f382843e39b0186f63759a3f57931528935a5bcd74ca0dff4e",
    ("system", 1): "dbae0a1432917b084dbe06cf43a155bfc0d7de38dfa06342267e545e09004368",
    ("system", 2): "116c19ce9b50057456f4cd1b35091564f6d7ce327aea086f9c965e3c2330c994",
}
"""Prüfsumme je (Modul, Version). Ein Server mit Version n hat genau diesen Text; ein geänderter Text mit derselben Zahl
würde dort nie als veraltet erkannt. Also: Text geändert → `HELPER_VERSION` im Skript und `version` in `extension.toml`
um eins erhöhen und hier eine neue Zeile eintragen (alte Zeilen bleiben stehen)."""

USAGE_EXIT = 64


def _helpers() -> dict[str, RootHelperFile]:
    found = [d for d in discovery.scan_directory(REPO_EXTENSIONS_DIR) if d.ok]
    with_spec = [d for d in found if d.manifest.root_helper is not None]
    helpers = {d.id: read_root_helper(d) for d in with_spec}
    assert all(helpers.values()), f"vom Kern abgelehnt: {[k for k, v in helpers.items() if v is None]}"
    return helpers  # type: ignore[return-value]


def _shells() -> list[str]:
    shells = [path for name in ("sh", "dash") if (path := shutil.which(name))]
    if sys.platform == "win32" or not shells:
        pytest.skip("braucht eine POSIX-Shell")
    return shells


def test_exactly_the_modules_with_root_commands_bring_a_helper():
    assert sorted(_helpers()) == ["shield", "system"]


def test_every_helper_is_pinned_to_its_version():
    for ext_id, helper in _helpers().items():
        pinned = PINNED.get((ext_id, helper.version))
        assert pinned is not None, f"{ext_id}: neue Version {helper.version}, Prüfsumme {helper.sha256} in PINNED eintragen"
        assert helper.sha256 == pinned, (
            f"{ext_id}: Text geändert, aber Version {helper.version} gleich geblieben. HELPER_VERSION im Skript und "
            "root_helper.version in extension.toml erhöhen, dann die neue Prüfsumme in PINNED eintragen."
        )


def test_all_helpers_fit_into_one_setup_command():
    """Alle Skripte reisen base64-kodiert in einem Einrichtungsbefehl mit, der als ein Argument an `sh -c` geht (Linux
    erlaubt 128 KiB je Argument). Mit etwas Luft für weitere Unterbefehle."""
    from nodvard_deck.services.host_setup import MAX_ONE_LINER, build_setup_script

    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPlatzhalterPlatzhalterPlatzhalterPlatzhalter deck@pi"
    _, one_liner = build_setup_script("deck", key, sudo=True, helpers=list(_helpers().values()))
    assert len(one_liner) <= MAX_ONE_LINER * 0.8
    assert all(len(h.text) <= MAX_HELPER_BYTES for h in _helpers().values())


def test_the_file_name_matches_the_place_on_the_server():
    for ext_id, helper in _helpers().items():
        assert helper.install_path == f"/usr/local/lib/nodvard-deck/{ext_id}-root"
        assert (REPO_EXTENSIONS_DIR / ext_id / "root-helper" / f"{ext_id}-root.sh").is_file()


def test_no_eval_and_no_foreign_text_for_a_shell():
    for ext_id, helper in _helpers().items():
        code = [line.strip() for line in helper.text.splitlines() if not line.lstrip().startswith("#")]
        for line in code:
            assert "eval" not in line.split(), (ext_id, line)
            assert "sh -c" not in line and "bash -c" not in line, (ext_id, line)
        assert "PATH=/usr/sbin:/usr/bin:/sbin:/bin" in code, ext_id
        assert "set -eu" in code, ext_id


def _run(shell: str, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    # Fremder PATH, fremde Trennzeichen und Sprache: Das Skript setzt alles selbst. Die Sprache über LANG, nicht LC_ALL:
    # Eine bash als `sh` warnt sonst schon beim Start auf stderr, wenn es die Sprache dort nicht gibt.
    return subprocess.run(
        [shell, script, *args], capture_output=True, text=True, timeout=30, check=False,
        env={"PATH": "/nicht-da", "IFS": "x", "LANG": "de_DE.UTF-8"},
    )


def test_the_helpers_run_in_every_posix_shell():
    for shell in _shells():
        for ext_id, helper in _helpers().items():
            script = str(REPO_EXTENSIONS_DIR / ext_id / "root-helper" / f"{ext_id}-root.sh")
            syntax = subprocess.run([shell, "-n", script], capture_output=True, text=True, timeout=30, check=False)
            assert syntax.returncode == 0, (shell, ext_id, syntax.stderr)

            version = _run(shell, script, "version")
            assert (version.returncode, version.stdout, version.stderr) == (0, f"{helper.version}\n", ""), (shell, ext_id)

            for args in ([], ["version", "x"], ["gibt-es-nicht"], ["VERSION"], ["version;id"], ["--help"]):
                refused = _run(shell, script, *args)
                assert refused.returncode == USAGE_EXIT, (shell, ext_id, args, refused.stdout, refused.stderr)
                assert refused.stdout == "", (shell, ext_id, args)
                assert refused.stderr.startswith(f"{ext_id}-root: "), (shell, ext_id, args, refused.stderr)
