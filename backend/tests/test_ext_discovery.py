"""Entdeckung von Extensions: Verzeichnis-Scan + Entry-Points, ohne Code-Import
(docs/02-EXTENSION-API.md §1).
"""

from __future__ import annotations

from nodvard_deck.ext import discovery


def _write_manifest(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / "extension.toml").write_text(content, encoding="utf-8")


def test_scan_directory_finds_valid_manifest(tmp_path):
    ext_dir = tmp_path / "sample-ext"
    _write_manifest(
        ext_dir / "x",
        """
        [extension]
        id = "sample-ext"
        name = "Sample"
        version = "0.1.0"
        api_version = "0.1"
        entrypoint = "sample_ext:Extension"
        permissions = ["audit.write"]
        """,
    )

    found = discovery.scan_directory(tmp_path)
    assert len(found) == 1
    assert found[0].ok
    assert found[0].id == "sample-ext"
    assert found[0].source == "bundled"
    assert found[0].manifest.permissions == ["audit.write"]


def test_scan_directory_isolates_broken_manifest_from_others(tmp_path):
    _write_manifest(
        tmp_path / "broken" / "x",
        "das ist kein gueltiges TOML {{{",
    )
    _write_manifest(
        tmp_path / "healthy" / "x",
        """
        [extension]
        id = "healthy"
        name = "Healthy"
        version = "0.1.0"
        api_version = "0.1"
        entrypoint = "healthy:Extension"
        """,
    )

    found = {d.id if d.ok else d.source_path.name: d for d in discovery.scan_directory(tmp_path)}

    assert found["broken"].ok is False
    assert found["broken"].error is not None
    assert found["healthy"].ok is True


def test_scan_directory_ignores_subdirs_without_manifest(tmp_path):
    (tmp_path / "not-an-extension").mkdir()
    (tmp_path / "not-an-extension" / "readme.txt").write_text("x")

    assert discovery.scan_directory(tmp_path) == []


def test_scan_directory_missing_root_returns_empty(tmp_path):
    assert discovery.scan_directory(tmp_path / "does-not-exist") == []


def test_scan_entry_points_finds_manifest_via_distribution(tmp_path, monkeypatch):
    manifest_path = tmp_path / "extension.toml"
    manifest_path.write_text(
        """
        [extension]
        id = "pip-ext"
        name = "Pip Extension"
        version = "1.0.0"
        api_version = "0.1"
        entrypoint = "pip_ext:Extension"
        """,
        encoding="utf-8",
    )

    class _FakeDistribution:
        name = "pip-ext-dist"

        def locate_file(self, path: str):
            return tmp_path / path

    class _FakeEntryPoint:
        name = "pip-ext"
        dist = _FakeDistribution()

    monkeypatch.setattr(
        discovery.importlib.metadata, "entry_points", lambda group: [_FakeEntryPoint()]
    )

    found = discovery.scan_entry_points()
    assert len(found) == 1
    assert found[0].ok
    assert found[0].id == "pip-ext"
    assert found[0].source == "pip"


def test_scan_entry_points_isolates_missing_manifest_file(tmp_path, monkeypatch):
    class _FakeDistribution:
        name = "broken-dist"

        def locate_file(self, path: str):
            return tmp_path / "does-not-exist" / path

    class _FakeEntryPoint:
        name = "broken"
        dist = _FakeDistribution()

    monkeypatch.setattr(
        discovery.importlib.metadata, "entry_points", lambda group: [_FakeEntryPoint()]
    )

    found = discovery.scan_entry_points()
    assert len(found) == 1
    assert found[0].ok is False
    assert "extension.toml" in found[0].error


def test_scan_entry_points_no_matching_group_returns_empty():
    assert discovery.scan_entry_points(group="nodvard_deck.does-not-exist") == []
