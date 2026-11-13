from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker_stack_fakes import EXT_DIR, FakeSettings, result
from docker_stack_fakes import docker_on as docker_on  # noqa: PLC0414 - Fixture

NAMED = "sha256:" + "a" * 64
DANGLING = "sha256:" + "b" * 64
LAYER = "sha256:" + "c" * 64
IDS = "docker image ls -a -q --no-trunc"
PRUNE = "docker image prune -f"


@pytest.fixture(autouse=True, scope="module")
def _ext_docker_on_path():
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(EXT_DIR / "src"))
        yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def request():
    from nodvard_sdk import ActionRequest, Actor

    return ActionRequest(action_type="docker.prune_images", host_ref="h1", proposed_by=Actor.user("u1", "owner1"), reason="Test")


async def execute(listed, pruned, resources=None):
    from nodvard_deck_ext_docker.capabilities import DockerMaintenanceExecutor

    calls = []
    host = SimpleNamespace(id="h1", tags=["docker"])

    async def get(host_id):
        return host

    async def run(host, command, timeout_s=60):
        calls.append(command)
        answer = {IDS: listed, PRUNE: pruned}[command]
        if isinstance(answer, Exception):
            raise answer
        return answer

    resources = resources or SimpleNamespace(forget=Mock())
    ctx = SimpleNamespace(hosts=SimpleNamespace(get=get), settings=FakeSettings(), exec=SimpleNamespace(run=run))
    outcome = await DockerMaintenanceExecutor(ctx, resources=resources).execute(request())
    return outcome, calls, resources


@pytest.mark.parametrize("deleted, expected", [
    ([DANGLING, LAYER, DANGLING], "1 Image entfernt. Freigegeben: 1.2GB."),
    ([], "0 Images entfernt. Freigegeben: 0B."),
    ([NAMED, DANGLING, LAYER], "2 Images entfernt. Freigegeben: 1.2GB."),
])
async def test_prune_counts_images_without_layers(deleted, expected):
    space = "1.2GB" if deleted else "0B"
    output = "Deleted Images:\n" + "\n".join(f"deleted: {image}" for image in deleted) + f"\nTotal reclaimed space: {space}\n"
    outcome, calls, resources = await execute(result(f"{NAMED}\n{NAMED}\n{DANGLING}\n"), result(output))
    assert outcome.success is True
    assert outcome.output == expected
    assert calls == [IDS, PRUNE]
    resources.forget.assert_called_once_with("h1")


@pytest.mark.parametrize("listed", [result(stderr="permission denied", code=1), result("kaputt\n")])
async def test_unreadable_inventory_prevents_prune(listed):
    outcome, calls, resources = await execute(listed, result())
    assert outcome.success is False
    assert "Nichts aufgeräumt" in outcome.error
    assert calls == [IDS]
    resources.forget.assert_not_called()


async def test_prune_failure_keeps_the_error():
    outcome, _, resources = await execute(result(DANGLING + "\n"), result(stderr="daemon unavailable", code=1))
    assert outcome.success is False
    assert outcome.exit_code == 1
    assert outcome.error == "daemon unavailable"
    resources.forget.assert_called_once_with("h1")


async def test_prune_disconnect_invalidates_the_cache():
    resources = SimpleNamespace(forget=Mock())
    with pytest.raises(ConnectionError, match="Verbindung abgebrochen"):
        await execute(result(DANGLING + "\n"), ConnectionError("Verbindung abgebrochen"), resources)
    resources.forget.assert_called_once_with("h1")


async def test_prune_without_space_reports_unknown():
    outcome, _, _ = await execute(result(DANGLING + "\n"), result(f"Deleted Images:\ndeleted: {DANGLING}\n"))
    assert outcome.success is True
    assert outcome.output == "1 Image entfernt. Freigegebener Platz unbekannt."


async def test_prune_requires_approval_and_refreshes_images(client, docker_on, monkeypatch):
    images = [
        {"ID": NAMED, "Repository": "nodvard-deck", "Tag": "previous", "Size": "10MB", "Containers": "0"},
        {"ID": DANGLING, "Repository": "<none>", "Tag": "<none>", "Size": "2MB", "Containers": "0"},
    ]
    calls = []

    async def run(host, command, timeout_s=60):
        calls.append(command)
        if command.startswith("docker system df"):
            return result(json.dumps({"Images": images}))
        if command.startswith("docker network ls"):
            return result()
        if command == IDS:
            return result("\n".join(image["ID"] for image in images) + "\n")
        assert command == PRUNE
        images.pop()
        return result(f"Deleted Images:\ndeleted: {DANGLING}\ndeleted: {LAYER}\n\nTotal reclaimed space: 2MB\n")

    monkeypatch.setattr(docker_on.handle, "run", run)
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/images"
    assert len((await client.get(base, headers=docker_on.owner)).json()["images"]) == 2
    url = f"/api/v1/hosts/{docker_on.host}/actions/docker.prune_images"
    assert (await client.post(url, headers=docker_on.viewer, json={"reason": "Test"})).status_code == 403
    proposed = await client.post(url, headers=docker_on.owner, json={"reason": "Test", "payload": {}})
    assert proposed.status_code == 202
    assert PRUNE not in calls and IDS not in calls
    approved = await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=docker_on.owner)
    assert approved.json()["status"] == "succeeded", approved.text
    assert approved.json()["result"]["output"] == "1 Image entfernt. Freigegeben: 2MB."
    remaining = (await client.get(base, headers=docker_on.owner)).json()["images"]
    assert [image["id"] for image in remaining] == [NAMED]
