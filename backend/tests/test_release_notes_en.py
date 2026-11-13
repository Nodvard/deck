"""Prüfung der englischen Versionshinweise."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_english_release_notes_exist_and_non_empty():
    versions = ("0.13.0", "0.12.0", "0.12.1")
    for ver in versions:
        path = REPO_ROOT / "docs" / "en" / "release-notes" / f"{ver}.md"
        assert path.is_file(), f"Datei fehlt: {path}"
        content = path.read_text(encoding="utf-8")
        assert len(content.strip()) > 100
        assert f"# Nodvard Deck {ver}" in content


def test_release_notes_013_contains_sudo_rs_notice():
    path = REPO_ROOT / "docs" / "en" / "release-notes" / "0.13.0.md"
    content = path.read_text(encoding="utf-8")
    assert "sudo-rs" in content
    assert "Ubuntu 25.10" in content


def test_english_release_notes_structures():
    expected_013 = [
        "### The essentials",
        "### What you need to do",
        "### Security",
        "### New",
        "### Fixed",
        "### Improved",
        "### Good to know",
    ]
    path_013 = REPO_ROOT / "docs" / "en" / "release-notes" / "0.13.0.md"
    headings_013 = [line for line in path_013.read_text(encoding="utf-8").splitlines() if line.startswith("### ")]
    assert headings_013 == expected_013

    expected_012 = [
        "### The essentials",
        "### What you need to do",
        "### New",
        "### Security",
        "### Improved",
        "### Fixed",
        "### Good to know",
    ]
    path_012 = REPO_ROOT / "docs" / "en" / "release-notes" / "0.12.0.md"
    headings_012 = [line for line in path_012.read_text(encoding="utf-8").splitlines() if line.startswith("### ")]
    assert headings_012 == expected_012

    expected_0121 = [
        "### The essentials",
        "### What you need to do",
        "### Security",
        "### Fixed",
        "### Good to know",
    ]
    path_0121 = REPO_ROOT / "docs" / "en" / "release-notes" / "0.12.1.md"
    headings_0121 = [line for line in path_0121.read_text(encoding="utf-8").splitlines() if line.startswith("### ")]
    assert headings_0121 == expected_0121


def test_keine_gedankenstriche_in_englischen_versionshinweisen():
    for ver in ("0.13.0", "0.12.0", "0.12.1"):
        path = REPO_ROOT / "docs" / "en" / "release-notes" / f"{ver}.md"
        content = path.read_text(encoding="utf-8")
        assert "\u2013" not in content, f"En-Dash in {ver}.md gefunden"
        assert "\u2014" not in content, f"Em-Dash in {ver}.md gefunden"


def test_docs_readme_references_english_release_notes():
    readme_path = REPO_ROOT / "docs" / "README.md"
    content = readme_path.read_text(encoding="utf-8")
    assert "en/release-notes/0.13.0.md" in content
