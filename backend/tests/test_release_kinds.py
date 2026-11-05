"""scripts/release_kinds.py: Arten der Änderungen einer Version als Annotation am Image-Index."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from nodvard_deck import changelog
from nodvard_deck.core import updates

REPO_ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("release_kinds", REPO_ROOT / "scripts" / "release_kinds.py")
release_kinds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_kinds)


def _version_file(directory: Path, version: str, kinds: list[str]) -> None:
    entries = "".join(f'\n[[entries]]\nkind = "{kind}"\ntext = "Eintrag {i}."\n' for i, kind in enumerate(kinds))
    text = f'version = "{version}"\ndate = "2026-11-09"\n{entries}'
    (directory / f"{version}.toml").write_text(text, encoding="utf-8")


def test_annotation_names_each_kind_once(tmp_path):
    _version_file(tmp_path, "0.12.0", ["neu", "behoben", "neu", "sicherheit"])
    assert release_kinds.kinds_annotation("0.12.0", tmp_path) == (
        "index:io.nodvard.deck.release.kinds=neu,behoben,sicherheit"
    )


@pytest.mark.parametrize(
    ("kinds", "expected"),
    [(["dokumentation"], ["verbessert"]), (["dokumentation", "sicherheit"], ["sicherheit", "verbessert"])],
)
def test_documentation_annotation_is_understood_by_older_decks(tmp_path, monkeypatch, kinds, expected):
    _version_file(tmp_path, "0.13.0", kinds)
    value = release_kinds.kinds_annotation("0.13.0", tmp_path).split("=", 1)[1]
    manifest = json.dumps({"schemaVersion": 2, "annotations": {updates.KINDS_ANNOTATION: value}}).encode()
    monkeypatch.setattr(updates, "KINDS", ("sicherheit", "behoben", "verbessert", "neu"))
    assert updates.kinds_of(manifest) == (expected, True)
    assert "dokumentation" not in value.split(",")


def test_a_prerelease_gets_no_annotation(tmp_path):
    assert release_kinds.kinds_annotation("0.12.0-rc1", tmp_path) == ""


@pytest.mark.parametrize("version", ["0.12", "v0.12.0", "../0.12.0", "0.12.0+b1", ""])
def test_anything_but_a_version_is_refused(tmp_path, version):
    with pytest.raises(ValueError, match="keine Versionsnummer"):
        release_kinds.kinds_annotation(version, tmp_path)


def test_a_final_version_without_its_changelog_is_refused(tmp_path):
    with pytest.raises(ValueError, match="0.12.0.toml fehlt"):
        release_kinds.kinds_annotation("0.12.0", tmp_path)


def test_a_broken_changelog_is_refused(tmp_path):
    (tmp_path / "0.12.0.toml").write_text('version = "0.12.0"\n', encoding="utf-8")
    with pytest.raises(changelog.ChangelogError):
        release_kinds.kinds_annotation("0.12.0", tmp_path)


def test_script_and_update_search_agree_on_name_and_kinds():
    assert release_kinds.ANNOTATION == updates.KINDS_ANNOTATION
    assert set(changelog.KINDS) == set(updates.KINDS)


def test_the_update_search_reads_every_released_version_back():
    for release in changelog.load_changelog().versions:
        value = release_kinds.kinds_annotation(release.version).split("=", 1)[1]
        manifest = json.dumps({"schemaVersion": 2, "annotations": {updates.KINDS_ANNOTATION: value}}).encode()
        kinds, understood = updates.kinds_of(manifest)
        expected = {"verbessert" if entry.kind == "dokumentation" else entry.kind for entry in release.entries}
        assert understood and set(kinds) == expected, release.version


def test_main_prints_the_output_line(capsys):
    assert release_kinds.main(["release_kinds.py", "0.12.0-rc1"]) == 0
    assert capsys.readouterr().out == "annotation=\n"
    assert release_kinds.main(["release_kinds.py"]) == 2
    assert release_kinds.main(["release_kinds.py", "9.9.9"]) == 1
    assert "9.9.9.toml fehlt" in capsys.readouterr().err
