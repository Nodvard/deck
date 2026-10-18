"""Vorlagen für neue Stacks: feste Versionen, nichts vom Server eingebunden, keine riskanten Einstellungen, Ports ohne Überschneidung."""

from __future__ import annotations

import re
import sys

import pytest
import yaml
from docker_stack_fakes import EXT_DIR, docker_on  # noqa: F401 - Fixtures


@pytest.fixture(autouse=True)
def _ext_docker_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


FIXED_TAG = re.compile(r"^[0-9]+(\.[0-9]+){1,2}(-[a-z0-9.]+)?\Z")
SECRET_NAME = re.compile(r"PASS|TOKEN|SECRET|KEY|PWD", re.IGNORECASE)
RISKY_KEYS = {"privileged", "network_mode", "pid", "ipc", "cap_add", "devices", "security_opt", "userns_mode", "build", "provider"}


def _templates():
    from nodvard_deck_ext_docker import templates

    return templates.TEMPLATES


def test_every_template_is_safe_and_pinned():
    from nodvard_deck_ext_docker.editor import risky_settings
    from nodvard_deck_ext_docker.image_apply import PROJECT_RE
    from nodvard_deck_ext_docker.stacks import MAX_FILE_BYTES

    templates = _templates()
    assert len({t.id for t in templates}) == len(templates) >= 4
    for template in templates:
        assert PROJECT_RE.match(template.id) and template.name and template.summary and template.note
        assert len(template.compose.encode()) < MAX_FILE_BYTES
        config = yaml.safe_load(template.compose)
        published = []
        for name, service in config["services"].items():
            repo, _, tag = service["image"].rpartition(":")
            assert repo and FIXED_TAG.match(tag), f"{template.id}/{name}: feste Version statt {tag!r}"
            assert service["restart"] == "unless-stopped"
            assert not RISKY_KEYS & set(service), f"{template.id}/{name}"
            for volume in service.get("volumes", []):
                assert volume.split(":", 1)[0] in config.get("volumes", {}), f"{template.id}: nur benannte Volumes, nichts vom Server"
            published += [port.split(":", 1)[0] for port in service.get("ports", [])]
        assert published == list(template.ports)
        assert risky_settings(config) == []


def test_no_template_brings_a_password():
    for template in _templates():
        for name, service in yaml.safe_load(template.compose)["services"].items():
            environment = service.get("environment", {})
            assert isinstance(environment, dict), f"{template.id}/{name}: Umgebung als Zuordnung"
            secret = [key for key in environment if SECRET_NAME.search(key)]
            assert secret == [], f"{template.id}/{name}: ein fertiges Geheimnis in der Vorlage kennt jeder"


def test_notes_say_to_claim_the_first_account_right_away():
    for template in _templates():
        assert "gleich nach dem Start" in template.note, template.id


def test_all_templates_fit_on_one_server():
    ports = [port for template in _templates() for port in template.ports]
    assert len(ports) == len(set(ports)) and "22" not in ports


@pytest.mark.asyncio
async def test_the_catalog_through_the_core(client, docker_on):
    url = "/api/v1/ext/docker/stack-templates"
    answer = await client.get(url, headers=docker_on.owner)
    assert answer.status_code == 200
    first = answer.json()["templates"][0]
    assert set(first) == {"id", "name", "summary", "compose", "note", "ports"} and first["compose"].startswith("services:\n")
    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers=docker_on.viewer)).status_code == 403
