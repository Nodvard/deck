from __future__ import annotations

import sys

import pytest
from docker_stack_fakes import EXT_DIR, docker_on  # noqa: F401 - Fixture

FOLDER = "/opt/stacks/shop"
ADOPT = {"working_dir": FOLDER, "config_files": [f"{FOLDER}/compose.yaml"]}


@pytest.fixture(autouse=True, scope="module")
def _ext_docker_on_path():
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(EXT_DIR / "src"))
        yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


async def _tags(db_session, host_id, tags):
    from nodvard_deck.services import hosts as hosts_service

    await hosts_service.update_host(db_session, host_id, tags=tags)
    await db_session.commit()


async def _propose(client, docker_on, action, payload):
    proposed = await client.post(
        f"/api/v1/hosts/{docker_on.host}/actions/{action}", json={"payload": payload, "reason": "Test"}, headers=docker_on.owner,
    )
    assert proposed.status_code == 202 and proposed.json()["status"] == "proposed", proposed.text
    return proposed.json()["id"]


async def _approve(client, docker_on, action_id):
    docker_on.exec.calls.clear()
    docker_on.exec.argv_calls.clear()
    return (await client.post(f"/api/v1/actions/{action_id}/approve", headers=docker_on.owner)).json()


FULL_ONLY = [
    ("docker.stack_stop", {"project": "shop"}),
    ("docker.stack_restart", {"project": "shop"}),
    ("docker.prune_images", {}),
    ("container.remove", {"container": "shop-web-1"}),
    ("docker.image_remove", {"image": "sha256:" + "a" * 64}),
    ("docker.volume_remove", {"volume": "daten"}),
    ("docker.network_remove", {"network": "b" * 64}),
]
ANY_DOCKER = [
    ("container.restart", {"container": "shop-web-1"}),
    ("docker.stack_stop", {"project": "shop"}),
    ("docker.stack_restart", {"project": "shop"}),
    ("docker.prune_images", {}),
    ("docker.image_remove", {"image": "sha256:" + "a" * 64}),
    ("container.remove", {"container": "shop-web-1"}),
    ("docker.volume_remove", {"volume": "daten"}),
]


def _ids(cases):
    return [action for action, _ in cases]


STAMP = "20261024-120000"
REGISTRY = {"name": "ghcr", "address": "ghcr.io", "username": "nico"}
# Jede Aktionsart des Moduls mit einem Vorschlag, der ohne die Prüfung bis zum ersten Befehl käme.
EVERY_ACTION = {
    "container.start": {"container": "shop-web-1"},
    "container.stop": {"container": "shop-web-1"},
    "container.restart": {"container": "shop-web-1"},
    "container.remove": {"container": "shop-web-1"},
    "container.image_update": {"container": "shop-web-1"},
    "docker.container_to_stack": {"container": "solo"},
    "docker.prune_images": {},
    "docker.prune_unused_images": {},
    "docker.prune_build_cache": {},
    "docker.image_remove": {"image": "sha256:" + "a" * 64},
    "docker.volume_remove": {"volume": "daten"},
    "docker.network_remove": {"network": "b" * 64},
    "docker.registry_login": {"registry": "ghcr", "address": "ghcr.io", "username": "nico"},
    "docker.registry_logout": {"registry": "ghcr", "address": "ghcr.io", "username": "nico"},
    "docker.stack_up": {"project": "shop"},
    "docker.stack_stop": {"project": "shop"},
    "docker.stack_down": {"project": "shop"},
    "docker.stack_pull": {"project": "shop"},
    "docker.stack_restart": {"project": "shop"},
    "docker.stack_save": {"project": "shop", "path": f"{FOLDER}/compose.yaml"},
    "docker.stack_create": {"project": "neu"},
    "docker.stack_from_portainer": {"project": "shop"},
    "docker.stack_delete": {"project": "shop"},
    "docker.stack_update": {"project": "shop", "services": ["web"]},
    "docker.stack_rollback": {"project": "shop", "services": ["web"]},
    "docker.stack_volume_restore": {"project": "shop", "stamp": STAMP, "volumes": ["daten"]},
    "docker.stack_backup_delete": {"project": "shop", "stamp": STAMP},
    "docker.stack_git_apply": {"project": "shop"},
    "docker.stack_git_rollback": {"project": "shop"},
    "docker.stack_git_restart": {"project": "shop", "services": ["web"]},
}
HELPER_VERBS = {"container.start", "container.stop", "container.restart"}
MODES = {"nur-bedienen": ["docker-bedienen"], "ohne-markierung": ["nas"]}
CASES = [(action, mode) for action in sorted(EVERY_ACTION) for mode in MODES if not (mode == "nur-bedienen" and action in HELPER_VERBS)]


def _executors():
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_sdk.capabilities import ActionExecutor

    found = get_extension_runtime().capabilities.query_named(ActionExecutor)
    return {kind: impl for ext_id, impl in found if ext_id == "docker" for kind in impl.action_types}


@pytest.mark.asyncio
async def test_the_table_names_every_action_of_the_module(docker_on):
    assert set(_executors()) == set(EVERY_ACTION), "neue Aktionsart: in EVERY_ACTION aufnehmen"


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "mode"), CASES, ids=[f"{action}-{mode}" for action, mode in CASES])
async def test_every_executor_checks_the_mode_before_its_first_command(client, db_session, docker_on, monkeypatch, action, mode):
    from types import SimpleNamespace

    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck_ext_docker import operate
    from nodvard_sdk.types import Risk

    assert (await client.post(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/adopt", json=ADOPT, headers=docker_on.owner)).status_code == 200
    await get_extension_runtime().loaded["docker"].ctx.settings.set({"registries": [REGISTRY]})
    await _tags(db_session, docker_on.host, MODES[mode])
    opened = []

    def sftp(host):
        opened.append(host)
        return docker_on.exec.sftp(host)

    monkeypatch.setattr(docker_on.handle, "sftp", sftp)
    docker_on.exec.calls.clear()
    docker_on.exec.argv_calls.clear()
    req = SimpleNamespace(action_type=action, host_ref=docker_on.host, payload=dict(EVERY_ACTION[action]), risk=Risk.HIGH,
                          proposed_by=SimpleNamespace(type="user"), reason="Test")
    done = await _executors()[action].execute(req)
    expected = operate.REFUSED if mode == "nur-bedienen" else operate.NOT_DOCKER
    assert (done.success, done.error) == (False, expected)
    assert (docker_on.exec.calls, docker_on.exec.argv_calls, opened) == ([], [], []), "kein Befehl, auch kein lesender"


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "payload"), FULL_ONLY, ids=_ids(FULL_ONLY))
async def test_a_server_switched_to_operate_only_after_the_proposal_runs_nothing(client, db_session, docker_on, action, payload):
    from nodvard_deck_ext_docker import operate

    assert (await client.post(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/adopt", json=ADOPT, headers=docker_on.owner)).status_code == 200
    action_id = await _propose(client, docker_on, action, payload)
    await _tags(db_session, docker_on.host, ["docker-bedienen"])
    done = await _approve(client, docker_on, action_id)
    assert done["status"] == "failed" and done["result"]["error"] == operate.REFUSED
    assert docker_on.exec.calls == [] and docker_on.exec.argv_calls == [], "kein Befehl, auch kein lesender"


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "payload"), ANY_DOCKER, ids=_ids(ANY_DOCKER))
async def test_a_server_that_is_no_docker_server_anymore_runs_nothing(client, db_session, docker_on, action, payload):
    from nodvard_deck_ext_docker import operate

    assert (await client.post(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/adopt", json=ADOPT, headers=docker_on.owner)).status_code == 200
    action_id = await _propose(client, docker_on, action, payload)
    await _tags(db_session, docker_on.host, ["nas"])
    done = await _approve(client, docker_on, action_id)
    assert done["status"] == "failed" and done["result"]["error"] == operate.NOT_DOCKER
    assert docker_on.exec.calls == [] and docker_on.exec.argv_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["start", "stop", "restart"])
async def test_starting_and_stopping_still_go_through_the_helper(client, db_session, docker_on, verb):
    action_id = await _propose(client, docker_on, f"container.{verb}", {"container": "shop-web-1"})
    await _tags(db_session, docker_on.host, ["docker-bedienen"])
    await _approve(client, docker_on, action_id)
    calls = docker_on.exec.calls
    assert calls and all(call.startswith("sudo -n ") and call.endswith(" shop-web-1") for call in calls), calls


@pytest.mark.asyncio
async def test_a_server_with_both_tags_counts_as_full(client, db_session, docker_on):
    action_id = await _propose(client, docker_on, "container.remove", {"container": "shop-web-1"})
    await _tags(db_session, docker_on.host, ["docker-bedienen", "docker"])
    await _approve(client, docker_on, action_id)
    assert docker_on.exec.calls or docker_on.exec.argv_calls, "voller Docker-Server: das Löschen läuft an"


@pytest.mark.asyncio
async def test_own_tags_are_named_in_the_reason(client, db_session, docker_on):
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck_ext_docker import operate

    settings = get_extension_runtime().loaded["docker"].ctx.settings
    await settings.set({"docker_host_tag": "voll", "docker_operate_tag": "lesen"})
    await _tags(db_session, docker_on.host, ["voll"])
    action_id = await _propose(client, docker_on, "container.remove", {"container": "shop-web-1"})
    await _tags(db_session, docker_on.host, ["lesen"])
    done = await _approve(client, docker_on, action_id)
    assert done["status"] == "failed" and done["result"]["error"] == operate.refused_text("voll")
    action_id = await _propose(client, docker_on, "container.restart", {"container": "shop-web-1"})
    await _tags(db_session, docker_on.host, ["docker"])
    done = await _approve(client, docker_on, action_id)
    assert done["status"] == "failed" and done["result"]["error"] == operate.not_docker_text("voll")
    assert "„voll“" in done["result"]["error"] and docker_on.exec.calls == [] and docker_on.exec.argv_calls == []
