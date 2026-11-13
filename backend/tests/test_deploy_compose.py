"""deploy/docker-compose.yml: Dienstname, Projektname und Volume bleiben so, dass ein Deploy die Daten findet.

Die YAML-Struktur laeuft ueberall; `docker compose config` nur, wenn `docker compose` da ist.
Wichtig ist der Volume-Name `deploy_lattice_data` (Projekt `deploy` + Schluessel `lattice_data`):
ein anderer Name waere ein neues, LEERES Volume.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker-compose.yml"


@pytest.mark.parametrize("filename", ["docker-compose.yml", "compose.standalone.yml", "compose.standalone-mit-helfer.yml"])
def test_nas_backup_hint_points_to_the_dedicated_guide(filename):
    text = (ROOT / "deploy" / filename).read_text(encoding="utf-8")
    hint = '      # docs/16-SICHERUNG.md, Abschnitt "Sicherungen auf ein NAS oder NFS".'
    intro = "      # NAS oder NFS: den eingehängten Ordner eintragen, Rechte für 1000 und Reihenfolge beim Start siehe"
    assert text.count(hint) == 1
    assert intro + "\n" + hint in text
    assert "docs/11-ERST-EINRICHTUNG.md" not in text
    guide = (ROOT / "docs" / "16-SICHERUNG.md").read_text(encoding="utf-8")
    assert "## Sicherungen auf ein NAS oder NFS" in guide


def _compose() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.mark.parametrize(("filename", "service"), [
    ("compose.standalone.yml", "nodvard-deck"),
    ("compose.standalone-mit-helfer.yml", "nodvard-deck"),
    ("nodvard-dock/compose.yml", "nodvard-dock"),
])
def test_hardened_compose_keeps_the_optional_setup_code(filename, service):
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load((ROOT / "deploy" / filename).read_text(encoding="utf-8"))
    app = data["services"][service]
    assert app["user"] == "1000:1000"
    assert app["read_only"] is True
    assert app["tmpfs"] == ["/tmp:rw,noexec,nosuid,nodev,mode=1777"]
    assert app["security_opt"] == ["no-new-privileges:true"]
    assert app["cap_drop"] == ["ALL"]
    assert app["environment"]["NODVARD_DECK_SETUP_CODE"] == "${NODVARD_DECK_SETUP_CODE:-}"


def test_compose_structure_keeps_volume_and_names():
    data = _compose()
    assert data["name"] == "deploy", "Projektname fest, sonst aendert sich der Volume-Name"
    assert list(data["services"]) == ["nodvard-deck"]
    service = data["services"]["nodvard-deck"]
    assert service["image"] == "nodvard-deck:latest"
    # Volume-Schluessel bleibt `lattice_data` (-> deploy_lattice_data), Mount /app/data.
    assert "lattice_data" in data["volumes"]
    # Der echte Name steht fest im Volume, damit COMPOSE_PROJECT_NAME/-p ihn nicht veraendern koennen.
    assert data["volumes"]["lattice_data"] == {"name": "deploy_lattice_data"}
    assert "lattice_data:/app/data" in service["volumes"]
    assert "8080:8080" in service["ports"]
    # Der Hostname ist die Container-ID (is_own_container vergleicht ihn), der Name kommt aus Compose.
    assert "hostname" not in service
    assert "container_name" not in service
    assert "lattice" not in data["services"]


def _config(tmp_path, extra_env=None, extra_args=()):
    # Auch aus einem Ordner mit anderem Namen: `name: deploy` haelt den Projektnamen fest.
    target = tmp_path / "anderer-ordner"
    if not target.exists():
        shutil.copytree(ROOT / "deploy", target, ignore=shutil.ignore_patterns(".env*"))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "NODVARD_DECK_", "LATTICE_"))}
    env.update(extra_env or {})
    result = subprocess.run(
        ["docker", "compose", *extra_args, "-f", str(target / "docker-compose.yml"), "config", "--format", "json"],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"docker compose config nicht nutzbar: {result.stderr.strip()[:200]}")
    return json.loads(result.stdout)


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker wird gebraucht")
def test_docker_compose_config_keeps_the_volume_name(tmp_path):
    config = _config(tmp_path)
    assert config["name"] == "deploy"
    assert list(config["services"]) == ["nodvard-deck"]
    assert config["volumes"]["lattice_data"]["name"] == "deploy_lattice_data"
    assert config["services"]["nodvard-deck"]["volumes"][0]["source"] == "lattice_data"


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker wird gebraucht")
@pytest.mark.parametrize(
    ("extra_env", "extra_args"),
    [({"COMPOSE_PROJECT_NAME": "foo"}, ()), ({}, ("-p", "foo"))],
    ids=["COMPOSE_PROJECT_NAME", "-p"],
)
def test_overriding_the_project_name_cannot_create_an_empty_volume(tmp_path, extra_env, extra_args):
    config = _config(tmp_path, extra_env, extra_args)
    assert config["name"] == "foo", "der Test ueberstimmt den Projektnamen wirklich"
    assert config["volumes"]["lattice_data"]["name"] == "deploy_lattice_data"


def test_compose_healthcheck_start_period_covers_a_slow_migration():
    service = _compose()["services"]["nodvard-deck"]
    check = service["healthcheck"]
    assert check["start_period"] == "300s", "wie im Dockerfile: eine lange Migration samt Kopie gilt nicht als 'unhealthy'"
    assert "/api/v1/health" in " ".join(check["test"])


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker wird gebraucht")
@pytest.mark.parametrize(("filename", "service"), [
    ("docker-compose.yml", "nodvard-deck"),
    ("compose.standalone.yml", "nodvard-deck"),
    ("compose.standalone-mit-helfer.yml", "nodvard-deck"),
    ("nodvard-dock/compose.yml", "nodvard-dock"),
])
@pytest.mark.parametrize("code", [None, "", "mein-eigener-code-7392"], ids=["nicht-gesetzt", "leer", "vorgegeben"])
def test_setup_code_is_optional_and_passed_from_dotenv(tmp_path, filename, service, code):
    if subprocess.run(["docker", "compose", "version"], capture_output=True, check=False).returncode != 0:
        pytest.skip("docker compose fehlt")
    shutil.copy(ROOT / "deploy" / filename, tmp_path / "compose.yml")
    if code is not None:
        (tmp_path / ".env").write_text(f"NODVARD_DECK_SETUP_CODE={code}\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "NODVARD_", "LATTICE_"))}
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    assert services[service]["environment"]["NODVARD_DECK_SETUP_CODE"] == (code or "")
    if "updater" in services:
        assert "NODVARD_DECK_SETUP_CODE" not in services["updater"].get("environment", {})
