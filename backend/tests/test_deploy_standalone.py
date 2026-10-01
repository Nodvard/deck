"""deploy/compose.standalone.yml: Installation ohne das Repository (nur diese eine Datei).

Die YAML-Struktur laeuft ueberall; `docker compose config` nur, wenn `docker compose` da ist.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STANDALONE = ROOT / "deploy" / "compose.standalone.yml"
MAIN = ROOT / "deploy" / "docker-compose.yml"


def _load(path: Path) -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_standalone_uses_the_published_image_and_no_build():
    service = _load(STANDALONE)["services"]["nodvard-deck"]
    assert service["image"] == "ghcr.io/nodvard/deck:latest"
    assert "build" not in service, "ohne Repository gibt es nichts zu bauen"
    assert service["restart"] == "unless-stopped"


def test_standalone_port_volume_and_logging():
    data = _load(STANDALONE)
    service = data["services"]["nodvard-deck"]
    assert service["ports"] == ["${NODVARD_DECK_PORT:-${LATTICE_PORT:-8080}}:8080"]
    assert len(service["volumes"]) == 1 and service["volumes"][0].endswith(":/app/data")
    assert service["logging"] == _load(MAIN)["services"]["nodvard-deck"]["logging"]


def test_standalone_healthcheck_is_the_one_of_the_main_compose_file():
    standalone = _load(STANDALONE)["services"]["nodvard-deck"]
    main = _load(MAIN)["services"]["nodvard-deck"]
    assert standalone["healthcheck"] == main["healthcheck"]


def test_standalone_dns_is_only_a_commented_example():
    text = STANDALONE.read_text(encoding="utf-8")
    assert "dns" not in _load(STANDALONE)["services"]["nodvard-deck"]
    assert "#   dns:" in text or "# dns:" in text


def test_standalone_header_explains_the_three_steps_in_german():
    head = "\n".join(STANDALONE.read_text(encoding="utf-8").splitlines()[:12])
    assert head.startswith("#")
    # Der Nutzer speichert die Datei als compose.yml; dann passen alle Befehle aus Oberflaeche und Doku
    # (`docker compose logs nodvard-deck`, `docker compose exec ...`) ohne `-f`.
    assert "-o compose.yml" in head
    assert "docker compose up -d" in head
    assert "Browser" in head


def test_standalone_keeps_a_project_name_that_is_independent_of_the_folder():
    # Ohne festen Namen ergaebe der Ordnername den Volume-Namen: anderer Ordner = leeres Volume.
    data = _load(STANDALONE)
    assert data["name"] == "nodvard-deck"
    assert "nodvard_deck_data" in data["volumes"]


def _config(tmp_path, env=None):
    target = tmp_path / "irgendein-ordner"
    target.mkdir(exist_ok=True)
    shutil.copy(STANDALONE, target / "compose.yml")  # nur diese eine Datei, unter dem Namen aus dem README
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "NODVARD_DECK_", "LATTICE_"))}
    clean.update(env or {})
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=target, env=clean, capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"docker compose config nicht nutzbar: {result.stderr.strip()[:200]}")
    return json.loads(result.stdout)


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker wird gebraucht")
def test_docker_compose_config_accepts_the_file_alone(tmp_path):
    service = _config(tmp_path)["services"]["nodvard-deck"]
    assert service["image"] == "ghcr.io/nodvard/deck:latest"
    assert service["ports"][0]["published"] == "8080" and service["ports"][0]["target"] == 8080


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker wird gebraucht")
@pytest.mark.parametrize(
    ("env", "expected"),
    [({}, "8080"), ({"LATTICE_PORT": "9000"}, "9000"), ({"NODVARD_DECK_PORT": "9100", "LATTICE_PORT": "9000"}, "9100")],
    ids=["standard", "alter-name", "neuer-name-gewinnt"],
)
def test_docker_compose_config_port_variable_with_fallback(tmp_path, env, expected):
    service = _config(tmp_path, env)["services"]["nodvard-deck"]
    assert service["ports"][0]["published"] == expected


def test_standalone_healthcheck_start_period_covers_a_slow_migration():
    service = _load(STANDALONE)["services"]["nodvard-deck"]
    assert service["healthcheck"]["start_period"] == "300s"
