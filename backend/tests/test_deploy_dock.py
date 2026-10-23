"""deploy/nodvard-dock: Compose-Datei und README von Nodvard Dock, dem Image nur mit dem Modul Docker."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "deploy" / "nodvard-dock"
COMPOSE = FOLDER / "compose.yml"
README = FOLDER / "README.md"
STANDALONE = ROOT / "deploy" / "compose.standalone.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
RAW_URL = "https://raw.githubusercontent.com/nodvard/deck/main/deploy/nodvard-dock/compose.yml"


def _load(path: Path) -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _service() -> dict:
    return _load(COMPOSE)["services"]["nodvard-dock"]


def test_one_service_from_the_published_image():
    data = _load(COMPOSE)
    assert list(data["services"]) == ["nodvard-dock"]
    service = _service()
    assert service["image"] == "ghcr.io/nodvard/dock:latest"
    assert "build" not in service
    assert service["restart"] == "unless-stopped"


def test_fixed_project_and_data_volume():
    # Der Name des Datenspeichers bleibt für immer: Der spätere Wechsel zum Deck-Image nimmt genau diesen.
    data = _load(COMPOSE)
    assert data["name"] == "nodvard-dock"
    assert _service()["volumes"] == ["nodvard_dock_data:/app/data"]
    assert data["volumes"] == {"nodvard_dock_data": {"name": "nodvard-dock-data"}}


def test_no_docker_socket_and_no_extra_rights():
    service = _service()
    assert not any("docker.sock" in str(volume) for volume in service["volumes"])
    for key in ("privileged", "cap_add", "devices", "network_mode", "pid", "security_opt", "user"):
        assert key not in service, key
    assert "docker.sock" not in COMPOSE.read_text(encoding="utf-8").split("services:")[1]


def test_port_is_configurable():
    assert _service()["ports"] == ["${NODVARD_DOCK_PORT:-8080}:8080"]


def test_logging_and_healthcheck_as_in_the_deck_file():
    deck = _load(STANDALONE)["services"]["nodvard-deck"]
    service = _service()
    assert service["logging"] == deck["logging"]
    assert service["healthcheck"] == deck["healthcheck"]


def test_header_explains_the_three_steps():
    head = "\n".join(COMPOSE.read_text(encoding="utf-8").splitlines()[:12])
    assert head.startswith("# Nodvard Dock")
    assert f"curl -fsSL -o compose.yml {RAW_URL}" in head
    assert "docker compose up -d" in head
    assert "docker compose logs nodvard-dock" in head
    assert "NODVARD_DOCK_PORT" in head


def test_readme_uses_the_same_commands_and_names():
    text = README.read_text(encoding="utf-8")
    assert f"curl -fsSL -o compose.yml {RAW_URL}" in text
    assert "docker compose logs nodvard-dock" in text
    assert "NODVARD_DOCK_PORT=8081" in text
    assert "--build-arg NODVARD_DECK_EXTENSIONS=docker" in text
    assert "PolyForm Noncommercial License 1.0.0" in text
    assert "[LICENSE](../../LICENSE)" in text
    assert (FOLDER / "../../LICENSE").resolve().is_file()


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _constant(path: str, name: str) -> str:
    match = re.search(rf'^(?:export const )?{name} = "([^"]+)"', _source(path), re.MULTILINE)
    assert match, f"{name} fehlt in {path}"
    return match[1]


def test_readme_names_backups_and_screens_as_the_code_does():
    text = README.read_text(encoding="utf-8")
    root = _constant("extensions/docker/src/nodvard_deck_ext_docker/stack_backup.py", "BACKUP_ROOT")
    user = _constant("frontend/src/routes/settings/hosts/AccessPanel.tsx", "DEFAULT_KEY_USER")
    assert f"(Vorgabe `{user}`) unter `{root}/<Stack>/<Zeit>/`" in text
    assert f"(default `{user}`) under `{root}/<stack>/<time>/`" in text
    assert "# - ./sicherungen:/backups" in COMPOSE.read_text(encoding="utf-8")
    assert text.count("`- ./sicherungen:/backups`") == 2
    assert '{ path: "system", label: "System"' in _source("frontend/src/routes/settings/SettingsLayout.tsx")
    assert '<Card title="Sicherung">' in _source("frontend/src/routes/settings/BackupCard.tsx")
    assert '<Card title="Wiederherstellen"' in _source("frontend/src/routes/settings/RestoreCard.tsx")
    assert "Oder: Sicherung einspielen" in _source("frontend/src/routes/SetupPage.tsx")
    for screen in ("Sicherung", "Wiederherstellen"):
        assert text.count(f"**Einstellungen → System → {screen}**") == 2, screen
    assert text.count("**Oder: Sicherung einspielen**") == 2


def test_readme_has_both_languages_with_working_anchors():
    text = README.read_text(encoding="utf-8")
    titles = re.findall(r"^#+ (.+)$", text, re.MULTILINE)
    headings = {re.sub(r"[^\w\- ]", "", t.strip().lower()).replace(" ", "-") for t in titles}
    for anchor in re.findall(r"\]\(#([^)]+)\)", text):
        assert anchor in headings, anchor
    assert "## English" in text


def test_ci_job_starts_the_image_of_this_file():
    job = _load(CI)["jobs"]["dock-image"]
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert f"--build-arg NODVARD_DECK_EXTENSIONS=docker -t {_service()['image']} ." in runs
    assert any(step.get("working-directory") == "deploy/nodvard-dock" for step in job["steps"])


def _config(tmp_path, env=None):
    if subprocess.run(["docker", "compose", "version"], capture_output=True, check=False).returncode != 0:
        pytest.skip("docker compose fehlt")
    target = tmp_path / "irgendein-ordner"
    target.mkdir(exist_ok=True)
    shutil.copy(COMPOSE, target / "compose.yml")
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "NODVARD_", "LATTICE_"))}
    clean.update(env or {})
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=target, env=clean, capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker wird gebraucht")
@pytest.mark.parametrize(("env", "port"), [({}, "8080"), ({"NODVARD_DOCK_PORT": "9100"}, "9100")])
def test_docker_compose_accepts_the_file_alone(tmp_path, env, port):
    config = _config(tmp_path, env)
    service = config["services"]["nodvard-dock"]
    assert service["ports"][0]["published"] == port and service["ports"][0]["target"] == 8080
    assert config["volumes"]["nodvard_dock_data"]["name"] == "nodvard-dock-data"
