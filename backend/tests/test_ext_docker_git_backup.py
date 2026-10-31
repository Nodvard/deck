from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from docker_stack_fakes import docker_on, result  # noqa: F401 - Fixture
from test_ext_docker_git_apply import (  # noqa: F401 - Fixtures
    COMPOSE_PATH,
    NEW,
    _approve,
    _ext_docker_on_path,
    _ga,
    _instance,
    _running,
    _source,
)

IMAGE_ID = "sha256:" + "a" * 64
GB = 1024**3


def _server(docker_on, monkeypatch, mounts=None, sizes=None, running=("web", "worker"), fail=None, in_file=("web", "worker")):
    """Der Server samt Volumes der Dienste, Platz, Archiven und Stoppen/Starten; `calls` hält die Reihenfolge fest. Wie bei
    Compose scheitert `start` ganz, wenn ein Dienst nicht in der Datei (`in_file`) steht."""
    mounts = {"web": ["shop_data"], "worker": []} if mounts is None else mounts
    sizes = {"shop_data": 2 * GB} if sizes is None else sizes
    fail = {} if fail is None else fail
    calls: list[str] = []
    run_argv = docker_on.handle.run_argv
    names = {"shop-web-1": "web", "shop-worker-1": "worker"}

    async def answer(host, argv, **kwargs):
        if argv[-4:] == ["--profile", "*", "config", "--services"]:
            listing = fail.get("listing")
            if listing == "raises":
                raise ConnectionResetError("Verbindung weg")
            if listing is not None:
                return result("") if listing == "empty" else result(stderr=listing, code=1)
            return result("".join(f"{name}\n" for name in in_file))
        if "start" in argv and (unknown := [s for s in argv[argv.index("start") + 1:] if s not in in_file]):
            calls.append(f"start {' '.join(argv[argv.index('start') + 1:])}")
            return result(stderr=f"no such service: {unknown[0]}\n", code=1)
        if argv[:2] == ["docker", "inspect"] and "--format" in argv:
            rows = [{"name": "/" + name, "image_id": IMAGE_ID, "ref": "busybox:1.37", "service": service, "running": service in running,
                     "mounts": [{"Type": "volume", "Name": v} for v in mounts.get(service, [])]}
                    for name, service in names.items() if name in argv]
            return result("".join(json.dumps(r) + "\n" for r in rows))
        if argv[:2] == ["df", "-Pk"]:
            return result(f"Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 100 1 {50 * 1024 * 1024} 1% /\n")
        if argv[:3] == ["docker", "image", "inspect"]:
            return result(f"{IMAGE_ID}\n")
        if argv[:2] == ["docker", "run"]:
            calls.append("archive")
            if "archive" in fail:
                return result(stderr=fail["archive"], code=1)
            return result()
        for verb in ("stop", "start", "up"):
            if verb in argv:
                calls.append(f"{verb} {' '.join(argv[argv.index(verb) + 1:])}".strip())
                if verb in fail:
                    return result(stderr=fail[verb], code=1)
        if argv[-3:] == ["config", "--format", "json"]:
            calls.append("check")
        return await run_argv(host, argv, **kwargs)

    monkeypatch.setattr(docker_on.handle, "run_argv", answer)
    known = [{"name": name, "size_bytes": size} for name, size in sizes.items()]
    monkeypatch.setattr(_instance(docker_on)._git_apply.update.volumes, "_resources",
                        SimpleNamespace(forget=lambda host_id: None, snapshot=lambda host: _done(SimpleNamespace(volumes=known))))
    return calls


async def _done(value):
    return value


async def _propose(client, docker_on, url, commit, backup=True):
    return await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit, "backup": backup})


@pytest.mark.asyncio
async def test_the_plan_names_volumes_sizes_and_the_services_that_stop(client, docker_on, tmp_path, monkeypatch):
    url, _ = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _server(docker_on, monkeypatch, mounts={"web": ["shop_data"], "worker": ["shop_cache"]}, sizes={"shop_data": 2 * GB, "shop_cache": GB},
            running=("web",))
    plan = (await client.get(f"{url}/volume-plan", headers=docker_on.owner)).json()
    assert plan["volumes"] == {"shop_cache": GB, "shop_data": 2 * GB} and plan["error"] is None
    assert plan["services"] == ["web"], "ein gestoppter Dienst muss nicht stillstehen"
    assert plan["folder"] == "/home/deck/nodvard-sicherungen/shop/"


@pytest.mark.asyncio
async def test_a_stack_without_volumes_has_no_backup(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch, mounts={})
    ga = _ga()
    assert (await client.get(f"{url}/volume-plan", headers=docker_on.owner)).json()["error"] == ga.NO_STACK_VOLUMES
    refused = await _propose(client, docker_on, url, commit)
    assert refused.status_code == 409 and refused.json()["detail"] == f"Sicherung der Volumes nicht möglich: {ga.NO_STACK_VOLUMES}"
    assert calls == [] and docker_on.exec.files[COMPOSE_PATH] != NEW


@pytest.mark.asyncio
async def test_too_little_space_refuses_the_proposal(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _server(docker_on, monkeypatch, sizes={"shop_data": 100 * GB})
    refused = await _propose(client, docker_on, url, commit)
    assert refused.status_code == 409 and "Zu wenig Platz" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_with_backup_the_volumes_are_saved_before_anything_is_written(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch)
    proposed = await _propose(client, docker_on, url, commit)
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert "Vorher sichert Deck die Volumes shop_data (bis zu 2,0 GB); dafür stehen web still" in action["reason"]
    assert ga.NO_VOLUME_BACKUP not in action["reason"]
    assert action["payload"]["backup"] == {"volumes": ["shop_data"], "services": ["web"]}
    written: list[str] = []
    write = ga.SftpFiles.write

    async def noted(self, path, mode, data):
        written.append(path)
        calls.append("write")
        await write(self, path, mode, data)

    monkeypatch.setattr(ga.SftpFiles, "write", noted)
    approved = await _approve(client, docker_on, proposed)
    assert approved["status"] == "succeeded", approved
    assert calls.index("stop web") < calls.index("archive") < calls.index("write") < calls.index("check") < calls.index("up -d")
    assert calls.index("up -d") < calls.index("start web"), "was `up` nicht startet, startet Deck danach"
    assert docker_on.exec.files[COMPOSE_PATH] == NEW
    assert "Volumes gesichert in /home/deck/nodvard-sicherungen/shop/" in approved["result"]["output"]
    assert "Zurückspielen geht im Reiter Stacks" in approved["result"]["output"]


@pytest.mark.asyncio
async def test_without_backup_nothing_stops(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch)
    proposed = await _propose(client, docker_on, url, commit, backup=False)
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert ga.NO_VOLUME_BACKUP in action["reason"] and action["payload"]["backup"] is None
    assert (await _approve(client, docker_on, proposed))["status"] == "succeeded"
    assert not [c for c in calls if c.startswith(("stop", "start")) or c == "archive"]


@pytest.mark.asyncio
async def test_volumes_of_stopped_services_are_saved_without_stopping_anything(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch, running=())
    proposed = await _propose(client, docker_on, url, commit)
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert "Vorher sichert Deck die Volumes shop_data (bis zu 2,0 GB). " in action["reason"]
    docker_on.exec.config = result(stderr="kaputt\n", code=1)
    error = (await _approve(client, docker_on, proposed))["result"]["error"]
    assert "archive" in calls and not [c for c in calls if c.startswith(("stop", "start"))]
    assert "wieder gestartet" not in error and "Volumes gesichert in" in error


@pytest.mark.asyncio
async def test_a_failed_backup_of_stopped_services_starts_nothing(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch, running=(), fail={"archive": "tar: kaputt\n"})
    error = (await _approve(client, docker_on, await _propose(client, docker_on, url, commit)))["result"]["error"]
    assert "Eingespielt wurde nichts; die Dienste laufen weiter." in error and calls == ["archive"]


@pytest.mark.asyncio
async def test_a_failed_backup_writes_nothing_and_starts_the_services_again(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    old = docker_on.exec.files[COMPOSE_PATH]
    calls = _server(docker_on, monkeypatch, fail={"archive": "tar: write error: No space left on device\n"})
    approved = await _approve(client, docker_on, await _propose(client, docker_on, url, commit))
    error = approved["result"]["error"]
    assert approved["status"] == "failed" and "Sicherung von shop_data gescheitert" in error
    assert "No space left on device. Eingespielt wurde nichts; die Dienste laufen weiter." in error, error
    assert "start web" in calls and docker_on.exec.files[COMPOSE_PATH] == old


@pytest.mark.asyncio
async def test_a_failure_after_the_backup_starts_the_services_again(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    old = docker_on.exec.files[COMPOSE_PATH]
    calls = _server(docker_on, monkeypatch)
    proposed = await _propose(client, docker_on, url, commit)
    docker_on.exec.config = result(stderr="services.web Additional property nonsense is not allowed\n", code=1)
    approved = await _approve(client, docker_on, proposed)
    error = approved["result"]["error"]
    assert approved["status"] == "failed" and "alte Stand steht wieder" in error
    assert "Die Dienste sind wieder gestartet. Volumes gesichert in /home/deck/nodvard-sicherungen/shop/" in error, error
    assert calls.index("archive") < calls.index("start web") and "up -d" not in calls
    assert docker_on.exec.files[COMPOSE_PATH] == old


@pytest.mark.asyncio
async def test_a_failed_up_starts_the_stopped_services_again(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch, fail={"up": "Error response from daemon: port is already allocated\n"})
    approved = await _approve(client, docker_on, await _propose(client, docker_on, url, commit))
    error = approved["result"]["error"]
    assert error.startswith(ga.APPLY_FAILED) and error.endswith("Die Dienste sind wieder gestartet."), error
    assert calls.index("up -d") < calls.index("start web")
    assert "Volumes gesichert in" in approved["result"]["output"]


@pytest.mark.asyncio
async def test_a_starting_problem_is_named(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _server(docker_on, monkeypatch, fail={"start": "no such service\n"})
    proposed = await _propose(client, docker_on, url, commit)
    docker_on.exec.config = result(stderr="kaputt\n", code=1)
    error = (await _approve(client, docker_on, proposed))["result"]["error"]
    assert "Wieder starten gescheitert: no such service. Starte den Stack im Reiter Stacks." in error, error


@pytest.mark.asyncio
async def test_changed_volumes_since_the_proposal_stop_nothing(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    mounts = {"web": ["shop_data"], "worker": []}
    calls = _server(docker_on, monkeypatch, mounts=mounts)
    proposed = await _propose(client, docker_on, url, commit)
    mounts["worker"] = ["shop_cache"]
    approved = await _approve(client, docker_on, proposed)
    assert approved["result"]["error"] == ga.BACKUP_CHANGED and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["joins", "leaves"])
async def test_other_services_with_the_same_volumes_stop_nothing(client, docker_on, tmp_path, monkeypatch, change):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    mounts = {"web": ["shop_data"], "worker": [] if change == "joins" else ["shop_data"]}
    running = ["web", "worker"]
    calls = _server(docker_on, monkeypatch, mounts=mounts, running=running)
    proposed = await _propose(client, docker_on, url, commit)
    if change == "joins":
        mounts["worker"] = ["shop_data"]
    else:
        running.remove("worker")
    approved = await _approve(client, docker_on, proposed)
    assert approved["result"]["error"] == ga.BACKUP_CHANGED and calls == []


@pytest.mark.asyncio
async def test_a_service_gone_from_the_file_is_named_and_the_others_start(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch, mounts={"web": ["shop_data"], "worker": ["shop_data"]}, in_file=("web",))
    approved = await _approve(client, docker_on, await _propose(client, docker_on, url, commit))
    assert approved["status"] == "succeeded", approved
    assert calls[-1] == "start web" and "start web worker" not in calls
    assert approved["result"]["output"].endswith(
        "Der Dienst worker steht nicht mehr in der Compose-Datei; sein alter Container bleibt gestoppt, entfernen kannst du ihn im Reiter "
        "Container.")


@pytest.mark.asyncio
async def test_a_failed_up_still_starts_what_is_left_in_the_file(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch, mounts={"web": ["shop_data"], "worker": ["shop_data"]}, in_file=("web",),
                    fail={"up": "Error response from daemon: port is already allocated\n"})
    error = (await _approve(client, docker_on, await _propose(client, docker_on, url, commit)))["result"]["error"]
    assert error.startswith(ga.APPLY_FAILED) and "Der Dienst worker steht nicht mehr" in error, error
    assert error.endswith("Die übrigen Dienste sind wieder gestartet.") and calls[-1] == "start web"


@pytest.mark.asyncio
async def test_after_a_rejected_check_only_services_in_the_file_start(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch, mounts={"web": ["shop_data"], "worker": ["shop_data"]}, in_file=("worker",))
    proposed = await _propose(client, docker_on, url, commit)
    docker_on.exec.config = result(stderr="kaputt\n", code=1)
    error = (await _approve(client, docker_on, proposed))["result"]["error"]
    assert "alte Stand steht wieder" in error, error
    assert "Der Dienst web steht nicht mehr in der Compose-Datei; sein alter Container bleibt gestoppt, entfernen kannst du ihn im Reiter " \
           "Container. Die übrigen Dienste sind wieder gestartet. Volumes gesichert in" in error, error
    assert calls[-1] == "start worker"


@pytest.mark.asyncio
@pytest.mark.parametrize("up", ["ok", "failed"])
async def test_when_all_stopped_services_are_gone_nothing_starts(client, docker_on, tmp_path, monkeypatch, up):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    fail = {} if up == "ok" else {"up": "Error response from daemon: port is already allocated\n"}
    calls = _server(docker_on, monkeypatch, mounts={"web": ["shop_data"], "worker": ["shop_data"]}, in_file=("cache",), fail=fail)
    done = (await _approve(client, docker_on, await _propose(client, docker_on, url, commit)))["result"]
    text = done["output"] if up == "ok" else done["error"]
    assert text.endswith("Die Dienste web, worker stehen nicht mehr in der Compose-Datei; ihre alten Container bleiben gestoppt, entfernen "
                         "kannst du sie im Reiter Container."), text
    assert "wieder gestartet" not in text and not [c for c in calls if c.startswith("start")]


@pytest.mark.asyncio
async def test_a_failed_start_names_only_the_services_still_in_the_file(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch, mounts={"web": ["shop_data"], "worker": ["shop_data"]}, in_file=("web",),
                    fail={"start": "Error response from daemon: cannot start\n"})
    error = (await _approve(client, docker_on, await _propose(client, docker_on, url, commit)))["result"]["error"]
    assert "Für die Sicherung gestoppt waren: web; starte den Stack im Reiter Stacks. Der Dienst worker steht nicht mehr" in error, error
    assert calls[-1] == "start web"


@pytest.mark.asyncio
@pytest.mark.parametrize("listing", ["kaputt\n", "empty", "raises"])
async def test_a_service_list_that_cannot_be_read_starts_all_of_them(client, docker_on, tmp_path, monkeypatch, listing):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch, mounts={"web": ["shop_data"], "worker": ["shop_data"]}, fail={"listing": listing})
    approved = await _approve(client, docker_on, await _propose(client, docker_on, url, commit))
    assert approved["status"] == "succeeded" and calls[-1] == "start web worker"
    assert "steht nicht mehr" not in approved["result"]["output"]


@pytest.mark.asyncio
async def test_a_server_that_changed_since_the_proposal_stops_nothing(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch)
    proposed = await _propose(client, docker_on, url, commit)
    docker_on.exec.files[COMPOSE_PATH] = b"services:\n  web:\n    image: busybox:1.36\n"
    approved = await _approve(client, docker_on, proposed)
    assert approved["result"]["error"] == ga.STALE and calls == [], "geprüft wird vor dem Stoppen"


@pytest.mark.asyncio
async def test_a_cancel_after_stopping_starts_the_services_again(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch)
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit, "backup": True})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()

    async def ended(self, *args):
        raise asyncio.CancelledError

    monkeypatch.setattr(ga.DockerStackGitApplyExecutor, "_put", ended)
    executor = ga.DockerStackGitApplyExecutor(_instance(docker_on)._git_apply)
    person = SimpleNamespace(action_type=ga.APPLY_ACTION, host_ref=docker_on.host, payload=action["payload"],
                             proposed_by=SimpleNamespace(type="user"))
    with pytest.raises(asyncio.CancelledError):
        await executor.execute(person)
    assert calls[-1] == "start web"


@pytest.mark.asyncio
@pytest.mark.parametrize("backup", [{}, {"volumes": [], "services": []}, {"volumes": ["a b"], "services": []},
                                    {"volumes": ["data"], "services": ["-x"]}, {"volumes": "data", "services": []}, "ja"])
async def test_odd_backup_details_in_the_proposal_change_nothing(client, docker_on, tmp_path, monkeypatch, backup):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch)
    proposed = await _propose(client, docker_on, url, commit)
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    executor = ga.DockerStackGitApplyExecutor(_instance(docker_on)._git_apply)
    person = SimpleNamespace(action_type=ga.APPLY_ACTION, host_ref=docker_on.host, payload={**action["payload"], "backup": backup},
                             proposed_by=SimpleNamespace(type="user"))
    done = await executor.execute(person)
    assert not done.success and "Angaben zur Sicherung" in done.error and calls == []


def test_the_backup_sentence_stays_short():
    ga = _ga()
    many = [f"{'v' * 200}{i}" for i in range(30)]
    text = ga.backup_sentence(many, many, dict.fromkeys(many, GB))
    assert "und 20 weitere" in text and "v" * 81 not in text and "bis zu 30,0 GB" in text
    assert ga.backup_sentence(["data"], [], {"data": None}) == "Vorher sichert Deck die Volumes data (bis zu 0,0 GB)."


@pytest.mark.asyncio
async def test_only_true_or_false_turns_the_backup_on_and_viewers_see_no_plan(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch)
    for odd in ("ja", "true", 1):
        assert (await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit, "backup": odd})).status_code == 422
    assert (await client.get(f"{url}/volume-plan", headers=docker_on.viewer)).status_code == 403
    assert calls == []


@pytest.mark.asyncio
async def test_a_failed_start_after_applying_is_named(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch, fail={"start": "Error response from daemon: cannot start\n"})
    approved = await _approve(client, docker_on, await _propose(client, docker_on, url, commit))
    error = approved["result"]["error"]
    assert approved["status"] == "failed" and error.startswith("Geschrieben und angewendet. Wieder starten gescheitert: "), error
    assert "Für die Sicherung gestoppt waren: web; starte den Stack im Reiter Stacks." in error
    assert calls.index("up -d") < calls.index("start web")


@pytest.mark.asyncio
async def test_a_cancel_while_starting_again_still_starts_them(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch)
    proposed = await _propose(client, docker_on, url, commit)
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    saver = _instance(docker_on)._git_apply.saver
    start_again, tries = saver.start_again, []

    async def cancelled_once(host, stack, services):
        tries.append(services)
        if len(tries) == 1:
            raise asyncio.CancelledError
        return await start_again(host, stack, services)

    monkeypatch.setattr(saver, "start_again", cancelled_once)
    executor = ga.DockerStackGitApplyExecutor(_instance(docker_on)._git_apply)
    person = SimpleNamespace(action_type=ga.APPLY_ACTION, host_ref=docker_on.host, payload=action["payload"],
                             proposed_by=SimpleNamespace(type="user"))
    with pytest.raises(asyncio.CancelledError):
        await executor.execute(person)
    assert tries == [["web"], ["web"]] and calls[-1] == "start web"


@pytest.mark.asyncio
async def test_a_second_cancel_does_not_stop_starting_again(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch)
    proposed = await _propose(client, docker_on, url, commit)
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    saver = _instance(docker_on)._git_apply.saver
    start_again, entered, release, finished = saver.start_again, asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def ended(self, *args):
        raise asyncio.CancelledError

    async def held(host, stack, services):
        entered.set()
        await release.wait()
        await start_again(host, stack, services)
        finished.set()

    monkeypatch.setattr(ga.DockerStackGitApplyExecutor, "_put", ended)
    monkeypatch.setattr(saver, "start_again", held)
    executor = ga.DockerStackGitApplyExecutor(_instance(docker_on)._git_apply)
    person = SimpleNamespace(action_type=ga.APPLY_ACTION, host_ref=docker_on.host, payload=action["payload"],
                             proposed_by=SimpleNamespace(type="user"))
    task = asyncio.create_task(executor.execute(person))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await asyncio.wait_for(finished.wait(), 10)
    assert calls[-1] == "start web"


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["backup_choice", "save_volumes"])
async def test_docker_out_of_reach_before_stopping_changes_nothing(client, docker_on, tmp_path, monkeypatch, where):
    from nodvard_deck_ext_docker.stacks import StackProblem

    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    calls = _server(docker_on, monkeypatch)
    proposed = await _propose(client, docker_on, url, commit)
    apply = _instance(docker_on)._git_apply

    async def unreachable(*args):
        raise StackProblem("Docker auf dem Server ließ sich nicht abfragen (Verbindung weg)")

    monkeypatch.setattr(apply if where == "backup_choice" else apply.saver, where, unreachable)
    error = (await _approve(client, docker_on, proposed))["result"]["error"]
    assert error == "Docker auf dem Server ließ sich nicht abfragen (Verbindung weg). Eingespielt wurde nichts." and calls == []


@pytest.mark.asyncio
async def test_docker_out_of_reach_gives_a_reason_not_a_crash(client, docker_on, tmp_path, monkeypatch):
    from nodvard_deck_ext_docker.stacks import StackProblem

    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _server(docker_on, monkeypatch)

    async def unreachable(*args):
        raise StackProblem("Docker auf dem Server ließ sich nicht abfragen (Verbindung weg).")

    monkeypatch.setattr(_instance(docker_on)._git_apply, "backup_choice", unreachable)
    for answer in (await client.get(f"{url}/volume-plan", headers=docker_on.owner), await _propose(client, docker_on, url, commit)):
        assert answer.status_code == 502 and "Verbindung weg" in answer.json()["detail"]


@pytest.mark.asyncio
async def test_the_dry_run_names_the_backup(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _server(docker_on, monkeypatch)
    proposed = await _propose(client, docker_on, url, commit)
    payload = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()["payload"]
    executor = ga.DockerStackGitApplyExecutor(_instance(docker_on)._git_apply)
    with_backup = await executor.dry_run(SimpleNamespace(action_type=ga.APPLY_ACTION, payload=payload))
    without = await executor.dry_run(SimpleNamespace(action_type=ga.APPLY_ACTION, payload={**payload, "backup": None}))
    assert with_backup.summary.startswith("Würde vorher die Volumes shop_data sichern, dann 1 Datei(en) schreiben")
    assert without.summary.startswith("Würde 1 Datei(en) schreiben")
