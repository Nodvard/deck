"""Kennungen, die der Kern selbst vergibt, kann keine Erweiterung tragen."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nodvard_sdk import RESERVED_EXTENSION_IDS, ExtensionManifest, load_manifest

BASE = {"id": "demo", "name": "Demo", "version": "0.1.0", "api_version": "0.1", "entrypoint": "demo:Extension"}


def test_core_is_reserved():
    assert "core" in RESERVED_EXTENSION_IDS


@pytest.mark.parametrize(("field", "extra"), [
    ("id", {"id": "core"}),
    ("legacy_ids", {"legacy_ids": ["core"]}),
    ("legacy_ids", {"legacy_ids": ["alt", "core"]}),
    ("adopts.from", {"adopts": [{"from": "core"}]}),
], ids=["id", "legacy_ids", "legacy_ids-hinten", "adopts"])
def test_a_reserved_id_is_refused_and_the_message_names_the_field(field, extra):
    with pytest.raises(ValidationError, match=rf"{field}: Die Kennung 'core' ist dem Kern vorbehalten"):
        ExtensionManifest.model_validate({**BASE, **extra})


@pytest.mark.parametrize("ext_id", ["corex", "core-tools", "my-core", "cor"])
def test_similar_ids_stay_allowed(ext_id):
    manifest = ExtensionManifest.model_validate(
        {**BASE, "id": ext_id, "legacy_ids": [ext_id + "-alt"], "adopts": [{"from": ext_id + "-nachbar"}]}
    )
    assert manifest.id == ext_id


def test_a_manifest_file_with_a_reserved_legacy_id_is_refused(tmp_path):
    (tmp_path / "extension.toml").write_text(
        '[extension]\nid = "demo"\nname = "Demo"\nversion = "0.1.0"\napi_version = "0.1"\n'
        'entrypoint = "demo:Extension"\nlegacy_ids = ["core"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="dem Kern vorbehalten"):
        load_manifest(tmp_path / "extension.toml")


@pytest.mark.parametrize("extra", [{"id": "core\n"}, {"legacy_ids": ["core\n"]}], ids=["id", "legacy_ids"])
def test_a_trailing_newline_does_not_slip_past_the_check(extra):
    with pytest.raises(ValidationError, match="Ungueltige"):
        ExtensionManifest.model_validate({**BASE, **extra})
