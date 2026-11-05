import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "deploy/nodvard-dock"
LANGUAGES = (
    ("README.md", (
        "Loslegen", "Unterschied zu Deck", "Sicherheit und Datenschutz auf einen Blick", "Grenzen",
        "Selbst bauen", "Mitmachen", "Support", "Lizenz",
    )),
    ("README.en.md", (
        "Getting started", "Difference to Deck", "Security and privacy at a glance", "Limits",
        "Build it yourself", "Contributing", "Support", "License",
    )),
)


def _source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def _section(text, heading):
    return text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


@pytest.mark.parametrize("name,headings", LANGUAGES)
def test_dock_readme_structure_and_setup_steps(name, headings):
    text = (FOLDER / name).read_text(encoding="utf-8")
    positions = [text.index(f"## {heading}\n") for heading in headings]
    assert positions == sorted(positions)
    quick = _section(text, headings[0])
    assert re.findall(r"^### ([1-4])\. ", quick, re.MULTILINE) == list("1234")
    for label in ("Einrichtungscode", "Server & Zugänge", "Mit Passwort einrichten"):
        assert label in quick
    for token in ("docker compose logs nodvard-dock", "NODVARD_DOCK_PORT=8081", "docker compose up -d"):
        assert token in quick


@pytest.mark.parametrize("name,headings", LANGUAGES)
def test_dock_readme_limits_match_image_update_code(name, headings):
    text = (FOLDER / name).read_text(encoding="utf-8")
    features = _section(text, headings[3])
    settings = json.loads(_source("extensions/docker/settings.schema.json"))["properties"]
    image_updates = _source("extensions/docker/src/nodvard_deck_ext_docker/image_updates.py")
    applier = _source("extensions/docker/src/nodvard_deck_ext_docker/applier.py")
    assert settings["image_update_auto_rollback"]["default"] is True
    assert settings["image_updates_enabled"]["default"] is False
    assert settings["git_watch_enabled"]["default"] is False
    assert "not ia.is_data_store(t.image, t.service)" in applier
    assert "and snap.rollback_image_id == t.image_id" in applier
    assert "Digests" in image_updates
    for pattern in (r"Digest", r"Tag", r"unhealthy", r"Redis", r"MinIO", r"5000", r"20"):
        assert re.search(pattern, features, re.IGNORECASE), pattern
    limits = {
        "README.md": ("Bind-Mounts", "Automatische Image-Rückkehr"),
        "README.en.md": ("Bind mounts", "Automatic image rollback"),
    }
    for phrase in limits[name]:
        assert phrase in features
    history = _source("extensions/docker/src/nodvard_deck_ext_docker/editor.py")
    logs = _source("extensions/docker/frontend/src/Logs.tsx")
    assert "HISTORY_KEEP = 20" in history
    assert "MAX_LINES = 5000" in logs


@pytest.mark.parametrize("name,headings", LANGUAGES)
def test_dock_readme_separates_edition_from_module_selection(name, headings):
    text = (FOLDER / name).read_text(encoding="utf-8")
    build = _section(text, headings[4])
    for token in ("NODVARD_DECK_EXTENSIONS=docker", "EDITION=dock"):
        assert token in build
    labels = ("Ausgabe", "Modulauswahl") if name == "README.md" else ("edition", "module selection")
    for label in labels:
        assert label in build
    assert "choose_modules=False, repo_tools=False" in _source("backend/src/nodvard_deck/edition.py")
    assert "select_extensions.sh" in _source("deploy/Dockerfile")
    assert "nodvard-dock-data" in text and "nodvard-deck-data" in text


@pytest.mark.parametrize("name,headings", LANGUAGES)
def test_dock_readme_license_feedback_and_images(name, headings):
    text = (FOLDER / name).read_text(encoding="utf-8")
    license_text = _section(text, headings[7])
    assert "PolyForm Noncommercial License 1.0.0" in license_text
    assert "kontakt@nodvard.com" in license_text
    assert "LICENSE" in license_text
    assert not re.search(r"open[ -]source", text, re.IGNORECASE)
    assert not re.search(r"!\[[^\]]*\]\((?!https://img\.shields\.io/)", text)
    feedback = _section(text, headings[5])
    assert "https://github.com/nodvard/dock/issues" in feedback
    assert "Pull Requests" in feedback or "pull requests" in feedback
    assert "patches" in feedback.lower()
    assert "https://discord.gg/gTTs7qMabt" in _section(text, headings[6])


@pytest.mark.parametrize("name,headings", LANGUAGES)
def test_dock_readme_links_work_before_and_after_export(name, headings):
    text = (FOLDER / name).read_text(encoding="utf-8")
    deck_raw = "https://raw.githubusercontent.com/nodvard/deck/main/deploy/nodvard-dock/compose.yml"
    dock_raw = "https://raw.githubusercontent.com/nodvard/dock/main/compose.yml"
    assert deck_raw in text
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if not target.startswith(("https://", "http://", "#", "mailto:")):
            assert (FOLDER / target.split("#", 1)[0]).is_file(), target
    exported = text.replace("](../../LICENSE)", "](LICENSE)")
    exported = exported.replace("](../../", "](https://github.com/nodvard/deck/blob/main/")
    exported = exported.replace(deck_raw, dock_raw)
    assert "](LICENSE)" in exported and dock_raw in exported
    assert "../../" not in exported and deck_raw not in exported
    local_files = {"README.md", "README.en.md", "compose.yml", "SECURITY.md", "LICENSE"}
    for target in re.findall(r"\]\(([^)]+)\)", exported):
        if not target.startswith(("https://", "http://", "#", "mailto:")):
            assert target.split("#", 1)[0] in local_files, target


@pytest.mark.parametrize("name,headings", LANGUAGES)
def test_dock_readme_has_no_conflict_markers_or_dashes(name, headings):
    text = (FOLDER / name).read_text(encoding="utf-8")
    assert not re.search(r"^(?:<{7}|={7}|>{7})", text, re.MULTILINE)
    assert not re.search(r"[\u2013\u2014\ufffd]", text)
