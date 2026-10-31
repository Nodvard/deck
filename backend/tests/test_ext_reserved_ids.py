"""Kennungen, die der Kern selbst als `ext_id` vergibt, kommen bei keiner Erweiterung durch die Entdeckung."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from nodvard_sdk import RESERVED_EXTENSION_IDS

import nodvard_deck
from nodvard_deck.ext import discovery

MANIFEST = """
[extension]
id = "{ext_id}"
name = "Demo"
version = "0.1.0"
api_version = "0.1"
entrypoint = "m:Extension"
{extra}
"""


@pytest.mark.parametrize(("ext_id", "extra", "field"), [
    ("core", "", "id"),
    ("demo", 'legacy_ids = ["core"]', "legacy_ids"),
    ("demo", '[[extension.adopts]]\nfrom = "core"', "adopts.from"),
], ids=["id", "legacy_ids", "adopts"])
def test_an_extension_with_a_reserved_id_is_refused_when_discovered(tmp_path, ext_id, extra, field):
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "extension.toml").write_text(MANIFEST.format(ext_id=ext_id, extra=extra), encoding="utf-8")

    (found,) = discovery.scan_directory(tmp_path)

    assert not found.ok and found.manifest is None
    assert f"{field}: Die Kennung 'core' ist dem Kern vorbehalten" in found.error
    assert discovery.resolve_legacy([found]).owner == {}


def test_the_core_only_uses_reserved_ids_as_fixed_ext_id():
    """Jede im Kern fest eingetragene `ext_id=` steht in der Sperrliste, und die Liste nennt nur solche."""
    used: set[str] = set()
    for path in Path(nodvard_deck.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.keyword) and node.arg == "ext_id"
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            ):
                used.add(node.value.value)
    assert used == RESERVED_EXTENSION_IDS
