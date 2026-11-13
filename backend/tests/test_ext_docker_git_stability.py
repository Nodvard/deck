from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from docker_stack_fakes import docker_on, result  # noqa: F401 - Fixture
from test_ext_docker_git_apply import (  # noqa: F401 - Fixture
    COMPOSE,
    COMPOSE_PATH,
    FOLDER,
    NEW,
    _apply,
    _approve,
    _ext_docker_on_path,
    _ga,
    _running_before,
    _source,
)


def _member(service="web", **changes):
    return {"name": f"/shop-{service}-1", "image_id": "sha256:" + "a" * 64,
            "status": "running", "exit_code": 0, "health": None, "restarts": 3,
            "started": "2026-11-02T08:00:00Z", **changes}


def _server(docker_on, monkeypatch, samples, *, ps=None, logs=None, command_s=0):
    ga = _ga()
    clock = SimpleNamespace(now=0.0, sleeps=[], timeouts=[])

    async def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(ga, "time", SimpleNamespace(**{**vars(ga.time), "monotonic": lambda: clock.now}))
    monkeypatch.setattr(ga, "asyncio", SimpleNamespace(**{**vars(asyncio), "sleep": sleep}))
    from nodvard_deck_ext_docker import after_check, image_apply

    async def running(*args, **kwargs):
        return image_apply.Verdict(True, "läuft stabil"), None

    monkeypatch.setattr(after_check, "check_running", running)
    run = docker_on.handle.run_argv
    queue = list(samples)
    calls = []

    async def answer(host, argv, **kwargs):
        calls.append(list(argv))
        if ("config" in argv or argv[-4:] == ["ps", "--all", "--format", "json"]
                or argv[:3] == ["docker", "container", "inspect"]):
            timeout = kwargs.get("timeout_s", 60)
            clock.now += min(command_s, timeout)
            if command_s > timeout:
                clock.timeouts.append(list(argv))
                raise TimeoutError
        if argv[-4:] == ["ps", "--all", "--format", "json"]:
            if isinstance(ps, Exception):
                raise ps
            return ps or result(json.dumps([{"Name": f"shop-{s}-1", "Service": s} for s in ("web", "worker")]))
        if argv[:3] == ["docker", "container", "inspect"]:
            sample = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(sample, Exception):
                raise sample
            if hasattr(sample, "exit_code"):
                return sample
            return result("\n".join(json.dumps(m) for m in sample if m["name"].lstrip("/") in argv[5:]))
        if "logs" in argv:
            if isinstance(logs, Exception):
                raise logs
            return logs or result("\n".join(f"web | Fehler {n}" for n in range(12)))
        return await run(host, argv, **kwargs)

    monkeypatch.setattr(docker_on.handle, "run_argv", answer)
    return clock, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("health", [None, "healthy"])
async def test_git_apply_observes_even_healthy_services_for_twenty_seconds(client, docker_on, tmp_path, monkeypatch, health):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    clock, calls = _server(docker_on, monkeypatch, [[_member(health=health), _member("worker")]])
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "succeeded", approved
    assert "Dienste laufen stabil." in approved["result"]["output"]
    assert approved["result"]["detail"]["check"]["ok"] is True
    assert clock.sleeps == [2] * 10
    assert len([c for c in calls if c[:3] == ["docker", "container", "inspect"]]) == 11
    assert not any("logs" in c for c in calls)
    history = await client.get(f"/api/v1/actions/{approved['id']}", headers=docker_on.owner)
    assert "Dienste laufen stabil." in history.text


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["apply", "restart"])
@pytest.mark.parametrize("oneoff", ["True", "true"])
async def test_stability_ignores_failed_oneoff_containers(client, docker_on, tmp_path, monkeypatch, operation, oneoff):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _running_before(docker_on, monkeypatch, {"web": ("shop-web-1",), "worker": ()})
    ps = result(json.dumps([
        {"Name": "shop-web-1", "Service": "web"},
        {"Name": "shop-web-run-old", "Service": "web"},
        {"Name": "shop-worker-1", "Service": "worker"},
    ]))
    clock, calls = _server(docker_on, monkeypatch, [[
        _member(health="healthy", oneoff="False"), _member("worker"),
        _member(name="/shop-web-run-old", status="exited", exit_code=1, oneoff=oneoff),
    ]], ps=ps)
    if operation == "apply":
        approved = await _apply(client, docker_on, url, commit)
    else:
        approved = await _approve(client, docker_on, await client.post(
            f"{url}/restart", headers=docker_on.owner, json={"services": ["web"]},
        ))
    assert approved["status"] == "succeeded", approved
    assert approved["result"]["detail"]["check"] == {"ok": True, "text": "Dienste laufen stabil."}
    assert clock.now == 20
    inspected = [c for c in calls if c[:3] == ["docker", "container", "inspect"]]
    assert inspected and all('index .Config.Labels "com.docker.compose.oneoff"' in c[4] for c in inspected)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["apply", "restart"])
async def test_stability_reads_keep_their_timeout_at_the_end(client, docker_on, tmp_path, monkeypatch, operation):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _running_before(docker_on, monkeypatch, {"web": ("shop-web-1",), "worker": ()})
    clock, calls = _server(docker_on, monkeypatch, [[_member(), _member("worker")]], command_s=0.2)
    if operation == "apply":
        approved = await _apply(client, docker_on, url, commit)
    else:
        approved = await _approve(client, docker_on, await client.post(
            f"{url}/restart", headers=docker_on.owner, json={"services": ["web"]},
        ))
    assert approved["status"] == "succeeded", approved
    assert approved["result"]["detail"]["check"]["ok"] is True
    assert not clock.timeouts
    assert clock.now >= 20
    assert len([c for c in calls if c[:3] == ["docker", "container", "inspect"]]) > 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["apply", "restart"])
async def test_stability_lists_containers_in_the_stack_folder(client, docker_on, tmp_path, monkeypatch, operation):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _running_before(docker_on, monkeypatch, {"web": ("shop-web-1",), "worker": ()})
    _, calls = _server(docker_on, monkeypatch, [[_member(), _member("worker")]])
    if operation == "apply":
        approved = await _apply(client, docker_on, url, commit)
    else:
        approved = await _approve(client, docker_on, await client.post(
            f"{url}/restart", headers=docker_on.owner, json={"services": ["web"]},
        ))
    assert approved["status"] == "succeeded", approved
    ps = [c for c in calls if c[-4:] == ["ps", "--all", "--format", "json"]]
    assert ps and all(c[:5] == ["sh", "-c", _ga().IN_FOLDER, "sh", FOLDER] for c in ps)


@pytest.mark.asyncio
@pytest.mark.parametrize("setting,extra", [
    ({"scale": 0}, b"    scale: 0\n"),
    ({"deploy": {"replicas": 0}}, b"    deploy:\n      replicas: 0\n"),
], ids=["scale", "replicas"])
@pytest.mark.parametrize("web_running", [False, True])
async def test_git_apply_allows_services_scaled_to_zero(client, docker_on, tmp_path, monkeypatch, setting, extra, web_running):
    compose = NEW + (b"" if web_running else extra) + b"  worker:\n    image: busybox:1.37\n" + extra
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": compose})
    docker_on.exec.config = result(json.dumps({"services": {
        "web": {"image": "busybox:1.37", **({} if web_running else setting)},
        "worker": {"image": "busybox:1.37", **setting},
    }}))
    ps = result(json.dumps([{"Name": "shop-web-1", "Service": "web"}] if web_running else []))
    _, calls = _server(docker_on, monkeypatch, [[_member()]], ps=ps)
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "succeeded", approved
    text = "Dienste laufen stabil." if web_running else "Keine Dienste zum Starten vorgesehen."
    assert approved["result"]["detail"]["check"] == {"ok": True, "text": text}
    assert text in approved["result"]["output"]
    inspected = [c for c in calls if c[:3] == ["docker", "container", "inspect"]]
    assert bool(inspected) is web_running
    assert all(c[5:] == ["shop-web-1"] for c in inspected)


@pytest.mark.asyncio
@pytest.mark.parametrize("change,reason", [
    ({"status": "exited", "exit_code": 1}, "beendet"),
    ({"status": "exited", "exit_code": None}, "beendet"),
    ({"status": "dead", "exit_code": 0}, "defekt"),
    ({"status": "restarting"}, "startet neu"),
    ({"restarts": 4}, "erneut gestartet"),
    ({"status": "exited", "exit_code": 0, "restarts": 4}, "erneut gestartet"),
    ({"started": "2026-11-02T08:00:02Z"}, "erneut gestartet"),
    ({"status": "exited", "exit_code": 0, "started": "2026-11-02T08:00:02Z"}, "erneut gestartet"),
    ({"health": "unhealthy"}, "ungesund"),
    ({"health": "starting"}, "startet"),
])
async def test_git_apply_warns_with_service_and_logs_button_without_rollback(
    client, docker_on, tmp_path, monkeypatch, change, reason,
):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _, calls = _server(docker_on, monkeypatch, [[_member(), _member("worker")], [_member(**change), _member("worker")]])
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "failed", approved
    error = approved["result"]["error"]
    assert "Warnung: web:" in error and reason in error and "worker:" not in error
    assert "Logs über den Knopf „Logs“." in error
    assert not any("logs" in c for c in calls)
    assert docker_on.exec.files[COMPOSE_PATH] == NEW
    state = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()
    assert state["applied"]["commit"] == commit and state["previous"] is not None
    assert approved["result"]["detail"]["written"] is True
    assert approved["result"]["detail"]["check"]["ok"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("already_done", [False, True])
@pytest.mark.parametrize("all_done", [False, True])
async def test_git_apply_accepts_successfully_completed_services(
    client, docker_on, tmp_path, monkeypatch, already_done, all_done,
):
    compose = COMPOSE + b'    restart: "no"\n'
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": compose})
    done = [_member(status="exited", health="unhealthy"), _member("worker", status="exited" if all_done else "running")]
    samples = [done] if already_done else [[_member(), _member("worker")], done]
    clock, calls = _server(docker_on, monkeypatch, samples)
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "succeeded", approved
    check = approved["result"]["detail"]["check"]
    assert check["ok"] is True
    assert "Ohne Fehler beendet (Exit-Code 0): web" in check["text"]
    assert ("worker" in check["text"]) is all_done
    assert ("laufen stabil" in check["text"]) is not all_done
    assert check["text"] in approved["result"]["output"]
    assert approved["result"]["error"] is None
    assert clock.now == 20 and docker_on.exec.files[COMPOSE_PATH] == compose
    assert not any("logs" in c for c in calls)
    history = await client.get(f"/api/v1/actions/{approved['id']}", headers=docker_on.owner)
    assert history.json()["result"]["detail"]["check"] == check


@pytest.mark.asyncio
async def test_completed_service_starting_again_is_a_warning(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    clock, _ = _server(docker_on, monkeypatch, [
        [_member(status="exited"), _member("worker")],
        [_member(started="2026-11-02T08:00:02Z"), _member("worker")],
    ])
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "failed", approved
    assert "Warnung: web: erneut gestartet" in approved["result"]["error"]
    assert clock.now == 2


@pytest.mark.asyncio
async def test_git_apply_allows_healthcheck_to_finish_but_still_observes_restarts(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    clock, _ = _server(docker_on, monkeypatch, [
        [_member(health="starting"), _member("worker")],
        [_member(health="unhealthy"), _member("worker")],
        [_member(health="healthy"), _member("worker")],
    ])
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "succeeded" and "Dienste laufen stabil." in approved["result"]["output"]
    assert clock.now == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "partial", "invalid", "unreadable", "inspect"])
async def test_git_apply_reports_missing_or_unreadable_state(client, docker_on, tmp_path, monkeypatch, failure):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    ps = {"missing": result("[]"), "invalid": result("kein JSON"), "unreadable": OSError("weg")}.get(failure)
    samples = [[_member(), _member("worker")]]
    if failure == "partial":
        samples = [[_member("worker")]]
    if failure == "inspect":
        samples = [result(stderr="Docker antwortet nicht", code=1)]
    clock, _ = _server(docker_on, monkeypatch, samples, ps=ps)
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "failed" and "Warnung:" in approved["result"]["error"], approved
    assert "Dienste laufen stabil" not in approved["result"]["output"]
    assert clock.now == 20 and docker_on.exec.files[COMPOSE_PATH] == NEW


@pytest.mark.asyncio
async def test_git_apply_retries_an_inspect_outage(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _server(docker_on, monkeypatch, [OSError("weg"), [_member(), _member("worker")]])
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "succeeded" and "Dienste laufen stabil." in approved["result"]["output"]


@pytest.mark.asyncio
@pytest.mark.parametrize("completed", [False, True])
async def test_git_apply_does_not_claim_stability_from_only_one_late_read(client, docker_on, tmp_path, monkeypatch, completed):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _server(docker_on, monkeypatch, [OSError("weg")] * 10 + [[_member(status="exited" if completed else "running"), _member("worker")]])
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "failed" and "noch nicht lange genug beobachtet" in approved["result"]["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("profiled", [False, True])
async def test_the_automatic_restart_is_followed_by_the_strict_check(client, docker_on, tmp_path, monkeypatch, profiled):
    compose = NEW + b"    volumes: ['./conf:/etc/app']\n"
    docker_on.exec.files[COMPOSE_PATH] = compose
    docker_on.exec.config = result(json.dumps({"services": {
        "web": {"image": "busybox:1.37", "volumes": [{"type": "bind", "source": "/opt/stacks/shop/conf"}]},
    }}))
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": compose, "conf": {"app.ini": b"neu\n"}})
    if profiled:
        docker_on.exec.services = result("worker\n")
    _, calls = _server(docker_on, monkeypatch, [[_member(), _member("worker")], [_member(restarts=4), _member("worker")]])
    approved = await _apply(client, docker_on, url, commit)
    restart = next(i for i, c in enumerate(calls) if "restart" in c)
    up = next(i for i, c in enumerate(calls) if "up" in c)
    inspect = next(i for i, c in enumerate(calls) if c[:3] == ["docker", "container", "inspect"])
    assert restart < up < inspect
    assert "Warnung: web: erneut gestartet" in approved["result"]["error"]
    assert docker_on.exec.files[COMPOSE_PATH] == compose
    assert docker_on.exec.files["/opt/stacks/shop/conf/app.ini"] == b"neu\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("healthy", [False, True])
async def test_explicit_restart_checks_only_selected_services_and_warns_on_the_first_restart(
    client, docker_on, tmp_path, monkeypatch, healthy,
):
    url, _ = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _running_before(docker_on, monkeypatch, {"web": ("shop-web-1",), "worker": ()})
    health = "healthy" if healthy else None
    _, calls = _server(docker_on, monkeypatch, [
        [_member(health=health), _member("worker", status="exited")],
        [_member(health=health, restarts=4), _member("worker", status="exited")],
    ])
    approved = await _approve(client, docker_on, await client.post(
        f"{url}/restart", headers=docker_on.owner, json={"services": ["web"]},
    ))
    assert approved["status"] == "failed"
    error = approved["result"]["error"]
    assert "Warnung: web: erneut gestartet" in error and "worker:" not in error and "Logs über den Knopf „Logs“." in error
    inspected = [c for c in calls if c[:3] == ["docker", "container", "inspect"]]
    assert len(inspected) == 2 and all(c[-1:] == ["shop-web-1"] for c in inspected)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [1, 2])
async def test_explicit_restart_reports_a_replica_missing_from_the_first_read(client, docker_on, tmp_path, monkeypatch, missing):
    url, _ = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _running_before(docker_on, monkeypatch, {"web": ("shop-web-1", "shop-web-2"), "worker": ("shop-worker-1",)})
    remaining = f"shop-web-{3 - missing}"
    ps = result(json.dumps([{"Name": remaining, "Service": "web"}]))
    clock, calls = _server(docker_on, monkeypatch, [[_member(name=f"/{remaining}")]], ps=ps)
    approved = await _approve(client, docker_on, await client.post(
        f"{url}/restart", headers=docker_on.owner, json={"services": ["web"]},
    ))
    assert approved["status"] == "failed", approved
    assert "Warnung: web: Container erneut erstellt oder entfernt." in approved["result"]["error"]
    assert approved["result"]["detail"]["check"]["ok"] is False
    assert clock.now == 0
    inspected = [c for c in calls if c[:3] == ["docker", "container", "inspect"]]
    assert len(inspected) == 1 and inspected[0][5:] == [remaining]


@pytest.mark.asyncio
async def test_explicit_restart_accepts_a_successfully_completed_service(client, docker_on, tmp_path, monkeypatch):
    url, _ = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _running_before(docker_on, monkeypatch, {"web": ("shop-web-1",), "worker": ()})
    clock, calls = _server(docker_on, monkeypatch, [[_member(status="exited"), _member("worker")]])
    approved = await _approve(client, docker_on, await client.post(
        f"{url}/restart", headers=docker_on.owner, json={"services": ["web"]},
    ))
    assert approved["status"] == "succeeded", approved
    assert approved["result"]["detail"]["check"] == {"ok": True, "text": "Ohne Fehler beendet (Exit-Code 0): web."}
    assert clock.now == 20
    inspected = [c for c in calls if c[:3] == ["docker", "container", "inspect"]]
    assert len(inspected) == 11 and all(c[-1:] == ["shop-web-1"] for c in inspected)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["apply", "restart"])
@pytest.mark.parametrize("log_state", ["available", "empty", "failed", "unreachable"])
async def test_stability_warning_does_not_read_or_store_container_logs(
    client, docker_on, tmp_path, monkeypatch, operation, log_state,
):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    secret = ("password=log-only-password token=log-only-token Authorization: Bearer log-only-bearer "
              + "postgres" + "://user:log-only-url-password@db.example.org/app")
    logs = {"available": result(secret), "empty": result(), "failed": result(stderr=secret, code=1),
            "unreachable": OSError(secret)}[log_state]
    _running_before(docker_on, monkeypatch, {"web": ("shop-web-1",), "worker": ()})
    _, calls = _server(docker_on, monkeypatch, [[_member(status="exited", exit_code=1), _member("worker")]], logs=logs)
    if operation == "apply":
        approved = await _apply(client, docker_on, url, commit)
    else:
        approved = await _approve(client, docker_on, await client.post(
            f"{url}/restart", headers=docker_on.owner, json={"services": ["web"]},
        ))
    assert approved["status"] == "failed", approved
    assert "Warnung: web:" in approved["result"]["error"]
    assert "Logs über den Knopf „Logs“." in approved["result"]["error"]
    assert approved["result"]["detail"]["check"]["text"] in approved["result"]["error"]
    assert not any("logs" in c for c in calls)
    history = await client.get(f"/api/v1/actions/{approved['id']}", headers=docker_on.owner)
    assert history.status_code == 200
    for saved in (json.dumps(approved), history.text):
        assert "log-only-" not in saved and "Log-Auszug" not in saved


@pytest.mark.asyncio
async def test_failed_compose_up_does_not_claim_stability(client, docker_on, tmp_path, monkeypatch):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _, calls = _server(docker_on, monkeypatch, [[_member(), _member("worker")]])
    docker_on.exec.verb = result(stderr="Start fehlgeschlagen", code=1)
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "failed" and "Start fehlgeschlagen" in approved["result"]["error"]
    assert not any("ps" in c or c[:3] == ["docker", "container", "inspect"] for c in calls)
