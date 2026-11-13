from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", ["scripts/release_grant.py", "deploy/README.md"])
def test_signing_instructions_name_the_project_owner(name):
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    assert "Projektinhaber" in text
    assert "Nico" not in text


def test_release_summary_covers_host_keys_and_update_states():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = text.split("## [0.12.1]", 1)[1].split("## [0.12.0]", 1)[0]
    assert "2026-11-02" in section
    assert "Verbindung prüfen" in section
    assert "fingerprint" in section
    assert "held-back" in section
    assert "locked" in section
    assert "phasing" in section


def test_redirect_protection_is_documented_after_0120():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = text.split("## [0.12.0]", 1)[0]
    assert "ghcr.io" in section
    assert "redirects" in section


def test_fragment_names_describe_the_change():
    fragments = REPO_ROOT / "backend/src/nodvard_deck/changelog/unreleased"
    assert not list(fragments.glob("befund-*.toml"))
