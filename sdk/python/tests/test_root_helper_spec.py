"""`root_helper` im Manifest: Pfad zum Hilfsskript eines Moduls, Version und der feste Ort auf dem Server."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from nodvard_sdk import ROOT_HELPER_DIR, ExtensionManifest, RootHelperSpec, load_manifest, root_helper_path

BASE = {"id": "demo", "name": "Demo", "version": "0.1.0", "api_version": "0.1", "entrypoint": "demo:Extension"}


def test_without_root_helper_nothing_changes():
    assert ExtensionManifest.model_validate(BASE).root_helper is None


@pytest.mark.parametrize("file", ["root-helper/demo-root.sh", "demo.sh", "a/b_c/d-e.1.sh"])
def test_a_relative_sh_file_is_accepted(file):
    assert RootHelperSpec(file=file, version=1).file == file


@pytest.mark.parametrize(
    "file",
    [
        "", "/etc/x.sh", "../x.sh", "a/../x.sh", "./x.sh", "a//x.sh", "a/", r"a\x.sh", "x.txt", "x.sh/",
        "a b.sh", "x;y.sh", "$(id).sh", "-x.sh", ".hidden.sh", "ä.sh", "a\n/x.sh", "x.sh\n",
    ],
)
def test_other_paths_are_refused(file):
    with pytest.raises(ValidationError):
        RootHelperSpec(file=file, version=1)


@pytest.mark.parametrize("version", [0, -1, 10000, "1", 1.5, True])
def test_the_version_is_a_whole_number_from_1_to_9999(version):
    with pytest.raises(ValidationError):
        RootHelperSpec(file="x.sh", version=version)


def test_unknown_keys_are_refused():
    with pytest.raises(ValidationError):
        RootHelperSpec.model_validate({"file": "x.sh", "version": 1, "path": "/usr/bin/x"})


def test_the_place_on_the_server_comes_from_the_sdk():
    assert ROOT_HELPER_DIR == "/usr/local/lib/nodvard-deck"
    assert root_helper_path("shield") == "/usr/local/lib/nodvard-deck/shield-root"
    for bad in ("", "../x", "a/b", "Shield", "a b", "shield\n"):
        with pytest.raises(ValueError):
            root_helper_path(bad)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "extension.toml"
    path.write_text(
        '[extension]\nid = "demo"\nname = "Demo"\nversion = "0.1.0"\napi_version = "0.1"\n'
        f'entrypoint = "demo:Extension"\n{body}',
        encoding="utf-8",
    )
    return path


def test_load_manifest_reads_the_inline_table(tmp_path):
    manifest = load_manifest(_write(
        tmp_path, 'root_helper = { file = "root-helper/demo-root.sh", version = 3 }\npermissions = ["hosts.read"]\n'
    ))
    assert manifest.root_helper == RootHelperSpec(file="root-helper/demo-root.sh", version=3)
    assert manifest.permissions == ["hosts.read"]


def test_a_section_instead_of_the_inline_table_fails_loudly(tmp_path):
    """Als `[extension.root_helper]` geschrieben, gehörten die Rechte darunter zum Hilfsskript: lieber ein Fehler als
    ein Modul ohne Rechte."""
    with pytest.raises(ValidationError):
        load_manifest(_write(
            tmp_path, '[extension.root_helper]\nfile = "x.sh"\nversion = 1\npermissions = ["hosts.read"]\n'
        ))
