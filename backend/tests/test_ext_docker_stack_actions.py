"""Stack-Aktionen: Anwenden, Stoppen, Herunterfahren und Images holen, nur für übernommene Stacks und nur über das Gate."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from docker_stack_fakes import EXT_DIR, FakeExec, docker_on, ps, result, row  # noqa: F401 - Fixtures


@pytest.fixture(autouse=True)
def _ext_docker_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def _mods():
    from nodvard_deck_ext_docker import adopt, stack_actions, stacks

    return adopt, stack_actions, stacks


def _stack(**changes):
    _, _, stacks = _mods()
    return stacks.parse_members(ps(row(**changes)))[0]


FOLDER = "/opt/stacks/shop"
COMPOSE = ["docker", "compose", "--ansi", "never", "-p", "shop", "--project-directory", FOLDER, "-f", f"{FOLDER}/compose.yaml"]
IN_FOLDER = 'cd -- "$1" 2>/dev/null || exit 97; shift; exec "$@"'


@pytest.mark.parametrize(
    ("action", "verb"),
    [("docker.stack_up", ["up", "-d"]), ("docker.stack_stop", ["stop"]), ("docker.stack_down", ["down"]), ("docker.stack_pull", ["pull"])],
)
def test_the_words_run_in_the_stack_folder(action, verb):
    _, stack_actions, _ = _mods()
    assert stack_actions.cmd_verb(_stack(), action) == ["sh", "-c", IN_FOLDER, "sh", FOLDER, *COMPOSE, *verb]


def test_down_never_removes_volumes():
    _, stack_actions, _ = _mods()
    assert all("-v" not in verb and "--volumes" not in verb for verb in stack_actions.VERBS.values())


def test_the_specs():
    _, stack_actions, _ = _mods()
    from nodvard_sdk.types import Risk

    specs = {s.action_type: s for s in stack_actions.stack_action_specs("box")}
    assert set(specs) == stack_actions.DockerStackExecutor.action_types
    assert {t: s.default_risk for t, s in specs.items()} == {
        "docker.stack_up": Risk.MEDIUM, "docker.stack_stop": Risk.MEDIUM, "docker.stack_down": Risk.HIGH, "docker.stack_pull": Risk.MEDIUM,
    }
    assert {s.host_tags[0] for s in specs.values()} == {"box"}
    assert "Was nur im Container lag und nicht in einem Volume oder Ordner, ist danach weg." in specs["docker.stack_down"].confirm_text


def test_long_runs_get_time():
    _, stack_actions, _ = _mods()
    assert stack_actions.TIMEOUT_S == {
        "docker.stack_up": 2700, "docker.stack_stop": 900, "docker.stack_down": 900, "docker.stack_pull": 2700,
    }


VERB_LOG = """#!/bin/sh
printf '%s|%s\\n' "$PWD" "$*" >> "$LOG"
"""


@pytest.mark.skipif(shutil.which("sh") is None or sys.platform == "win32", reason="braucht eine POSIX-Shell")
def test_the_folder_and_odd_names_arrive_as_words(tmp_path):
    import shlex

    _, stack_actions, _ = _mods()
    folder = tmp_path / "my stacks" / "shop"
    folder.mkdir(parents=True)
    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir()
    fake.write_text(VERB_LOG, encoding="utf-8")
    fake.chmod(0o755)
    argv = stack_actions.cmd_verb(_stack(working_dir=str(folder), config_files=f"{folder}/compose.yaml"), "docker.stack_up")
    env = {**os.environ, "PATH": f"{fake.parent}{os.pathsep}{os.environ.get('PATH', '')}", "LOG": str(tmp_path / "log")}
    # So setzt der Kern die Wörter zusammen (`run_argv`, POSIX-Quoting).
    done = subprocess.run(["sh", "-c", shlex.join(argv)], env=env, cwd="/", capture_output=True, text=True, check=False, timeout=10)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "log").read_text(encoding="utf-8") == (
        f"{folder}|compose --ansi never -p shop --project-directory {folder} -f {folder}/compose.yaml up -d\n"
    )


def test_the_reason_is_the_last_error_line():
    _, stack_actions, _ = _mods()
    # So endet `docker compose pull` bei Docker 29.8.2, wenn Docker Hub die Zahl der Abrufe begrenzt.
    out = (
        " Image busybox:1.37 Pulling \n Image busybox:1.37 Error unknown: failed to resolve reference: 429 Too Many Requests\n"
        "Error response from daemon: unknown: failed to resolve reference \"docker.io/library/busybox:1.37\"\n"
    )
    assert stack_actions.failure_line(out) == 'Error response from daemon: unknown: failed to resolve reference "docker.io/library/busybox:1.37"'
    assert stack_actions.failure_line("\x1b[31mnur Fortschritt\x1b[0m\n") == "nur Fortschritt"
    # So endet `docker compose up -d` bei Docker 29.8.2, wenn eine Abhängigkeit „ungesund“ meldet.
    unhealthy = (
        " Container dep-db-1 Started \n Container dep-db-1 Waiting \n Container dep-db-1 Error dependency db failed to start\n"
        "dependency failed to start: container dep-db-1 is unhealthy\n"
    )
    assert stack_actions.failure_line(unhealthy) == "dependency failed to start: container dep-db-1 is unhealthy"
    warned = 'time="2026-10-16T01:00:00Z" level=warning msg="version is obsolete"\n Container a-1 Error boom\n'
    assert stack_actions.failure_line(warned) == "Container a-1 Error boom"
    assert stack_actions.failure_line("") == "Compose hat ohne Angabe eines Grundes abgebrochen."


# --- Ausführer -------------------------------------------------------------------------------------------------------


def _executor(tmp_path, fake: FakeExec | None = None):
    adopt, stack_actions, stacks = _mods()
    fake = fake or FakeExec()
    hosts = SimpleNamespace(get=lambda host_id: _async(SimpleNamespace(id=host_id) if host_id == "h1" else None))
    ctx = SimpleNamespace(exec=fake, hosts=hosts)
    store = adopt.AdoptedStacks(tmp_path)
    return stack_actions.DockerStackExecutor(ctx, stacks.DockerStacks(ctx, store), store), store, fake


async def _async(value):
    return value


def _request(action: str = "docker.stack_stop", project: str = "shop", host: str | None = "h1"):
    from nodvard_sdk.actions import ActionRequest
    from nodvard_sdk.types import Actor

    return ActionRequest(
        action_type=action, payload={"project": project}, host_ref=host, proposed_by=Actor(type="user", id="u1"), reason="Test",
    )


@pytest.mark.asyncio
async def test_only_adopted_stacks(tmp_path):
    executor, _, fake = _executor(tmp_path)
    done = await executor.execute(_request())
    assert done.success is False and done.error.startswith("Der Stack „shop“ ist nicht übernommen.")
    assert fake.argv_calls == []


@pytest.mark.asyncio
async def test_one_change_per_stack_at_a_time(tmp_path):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    with executor.locks.hold("h1", "shop", "Update") as busy:
        assert busy is None
        refused = await executor.execute(_request("docker.stack_stop"))
        with executor.locks.hold("h1", "shop", "Löschen") as second:
            assert second == "Update"
    assert refused.error == "An diesem Stack läuft gerade schon etwas (Update). Versuch es danach noch einmal." and fake.argv_calls == []
    assert (await executor.execute(_request("docker.stack_stop"))).success is True and executor.locks.held("h1", "shop") is None


@pytest.mark.asyncio
async def test_the_lock_is_free_again_after_an_error(tmp_path):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    inner = fake.run_argv

    async def broken(host, argv, **kwargs):
        if "stop" in argv:
            raise RuntimeError("kaputt")
        return await inner(host, argv, **kwargs)

    fake.run_argv = broken
    with pytest.raises(RuntimeError):
        await executor.execute(_request("docker.stack_stop"))
    assert executor.locks.held("h1", "shop") is None
    fake.run_argv = inner
    assert (await executor.execute(_request("docker.stack_stop"))).success is True


def test_every_action_has_a_name_for_the_lock():
    _, stack_actions, _ = _mods()
    assert stack_actions.LABELS.keys() == stack_actions.VERBS.keys() == stack_actions.DONE.keys() == stack_actions.TIMEOUT_S.keys()


def test_the_lock_is_per_server():
    from nodvard_deck_ext_docker.locks import StackLocks

    locks = StackLocks()
    with locks.hold("h1", "shop", "Update") as first, locks.hold("h2", "shop", "Anwenden") as second:
        assert first is None and second is None
        assert locks.held("h1", "shop") == "Update" and locks.held("h2", "shop") == "Anwenden" and locks.held("h1", "andere") is None


@pytest.mark.asyncio
async def test_the_module_shares_one_lock(client, docker_on):
    from nodvard_deck.ext.runtime import get_extension_runtime

    instance = get_extension_runtime().loaded["docker"].instance
    locks, applier, host = instance._locks, instance._applier, docker_on.host
    assert applier.stack_busy.__self__ is locks
    with locks.hold(host, "shop", "Anwenden") as busy:
        assert busy is None and applier.stack_busy(host, "shop") == "Anwenden"
    applier._busy[f"{host}:shop"] = "shop-web"
    try:
        with locks.hold(host, "shop", "Anwenden") as busy:
            assert busy == "Image-Update eines Containers"
    finally:
        applier._busy.clear()
    with locks.hold(host, "shop", "Update"):
        proposed = await client.post(f"/api/v1/hosts/{host}/actions/docker.stack_restart",
                                     json={"payload": {"project": "shop"}, "reason": "Test"}, headers=docker_on.owner)
        assert proposed.status_code in (200, 202), proposed.text
        action = proposed.json()
        if action["status"] == "proposed":
            action = (await client.post(f"/api/v1/actions/{action['id']}/approve", headers=docker_on.owner)).json()
    assert action["status"] == "failed" and "gerade schon etwas (Update)" in action["result"]["error"]
    assert not any("restart" in c for c in docker_on.exec.calls)


@pytest.mark.asyncio
async def test_stop_an_adopted_stack(tmp_path):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    done = await executor.execute(_request())
    assert (done.success, done.output) == (True, "Stack „shop“ gestoppt.")
    assert fake.argv_calls == [["sh", "-c", IN_FOLDER, "sh", FOLDER, *COMPOSE, "stop"]]


@pytest.mark.asyncio
async def test_a_failure_names_the_reason(tmp_path):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    fake.verb = result(stderr="Image busybox:1.37 Pulling\nError response from daemon: pull access denied\n", code=1)
    done = await executor.execute(_request("docker.stack_pull"))
    assert (done.success, done.exit_code, done.error) == (False, 1, "Error response from daemon: pull access denied")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "start"),
    [
        ({"image": "ghcr.io/nodvard/deck:0.9.0"}, "Das ist Nodvard Deck selbst."),
        ({"working_dir": "/srv/shop", "config_files": "/srv/shop/compose.yaml"}, "Übernommen aus /opt/stacks/shop"),
    ],
)
async def test_refusals_when_the_running_stack_differs(tmp_path, changes, start):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    fake.rows = [row(**changes), *[r for r in fake.rows if r["project"] != "shop"]]
    done = await executor.execute(_request("docker.stack_up"))
    assert done.success is False and done.error.startswith(start), done.error
    assert fake.argv_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("request_args", "reason"), [({"project": "Shop;id"}, "Ungültiger Stack-Name"), ({"host": "fremd"}, "Server nicht gefunden.")])
async def test_odd_requests(tmp_path, request_args, reason):
    executor, _, fake = _executor(tmp_path)
    done = await executor.execute(_request(**request_args))
    assert done.success is False and done.error.startswith(reason)
    assert fake.argv_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["docker.stack_down", "docker.stack_pull"])
async def test_down_and_pull(tmp_path, action):
    _, stack_actions, _ = _mods()
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    done = await executor.execute(_request(action))
    assert done.success is True and done.output == f"Stack „shop“ {stack_actions.DONE[action]}."
    assert fake.argv_calls == [["sh", "-c", IN_FOLDER, "sh", FOLDER, *COMPOSE, *stack_actions.VERBS[action]]]


@pytest.mark.asyncio
async def test_a_missing_folder_is_said_plainly(tmp_path):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    fake.verb = result(code=97)
    done = await executor.execute(_request("docker.stack_up"))
    assert done.success is False and done.error.startswith("Den Ordner „/opt/stacks/shop“ gibt es auf dem Server nicht mehr")


@pytest.mark.skipif(shutil.which("sh") is None or sys.platform == "win32", reason="braucht eine POSIX-Shell")
def test_the_wrapper_ends_with_97_without_the_folder(tmp_path):
    import shlex

    _, stack_actions, _ = _mods()
    argv = stack_actions.cmd_verb(_stack(working_dir=str(tmp_path / "weg"), config_files=f"{tmp_path}/weg/compose.yaml"), "docker.stack_up")
    done = subprocess.run(["sh", "-c", shlex.join(argv)], capture_output=True, text=True, check=False, timeout=10)
    assert (done.returncode, done.stderr) == (stack_actions.NO_FOLDER_EXIT, "")


@pytest.mark.asyncio
async def test_a_timeout_says_the_stack_may_be_half_done(tmp_path):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())

    async def slow(host, argv, **_):
        raise TimeoutError

    fake.run_argv = slow
    pulled = await executor.execute(_request("docker.stack_pull"))
    assert pulled.success is False and pulled.error.startswith("Nach 45 Minuten ohne Ende abgebrochen. Das Holen kann")
    stopped = await executor.execute(_request("docker.stack_stop"))
    assert stopped.error.startswith("Nach 15 Minuten ohne Ende abgebrochen.") and "nur zum Teil umgestellt" in stopped.error


class _SshUnreachable(OSError):
    readable = True


class _SshTimeout(_SshUnreachable, TimeoutError):
    pass


@pytest.mark.asyncio
async def test_a_connection_timeout_is_not_a_long_run(tmp_path):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())

    async def unreachable(host, argv, **_):
        raise _SshTimeout("Server antwortet nicht.")

    fake.run_argv = unreachable
    done = await executor.execute(_request("docker.stack_up"))
    assert done.success is False and done.error.startswith("Server antwortet nicht. Sieh im Reiter Stacks nach")


def _lost():
    asyncssh = pytest.importorskip("asyncssh")
    return asyncssh.ConnectionLost("Connection lost")


@pytest.mark.asyncio
async def test_a_lost_connection_is_said_in_german(tmp_path):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    error = _lost()
    assert not isinstance(error, OSError)

    async def lost(host, argv, **_):
        raise error

    fake.run_argv = lost
    done = await executor.execute(_request("docker.stack_up"))
    assert done.success is False
    assert done.error == ("Die Verbindung zum Server ist mitten im Lauf abgebrochen. Compose kann auf dem Server weitergelaufen sein. "
                          "Sieh im Reiter Stacks nach, ob sich am Stack schon etwas geändert hat.")


@pytest.mark.asyncio
async def test_a_reason_without_full_stop_gets_one(tmp_path):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())

    async def unreachable(host, argv, **_):
        raise _SshUnreachable("Server nicht erreichbar")

    fake.run_argv = unreachable
    done = await executor.execute(_request("docker.stack_stop"))
    assert done.error == "Server nicht erreichbar. Sieh im Reiter Stacks nach, ob sich am Stack schon etwas geändert hat."


@pytest.mark.asyncio
async def test_long_runs_get_their_time(tmp_path):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    seen: list[int] = []
    original = fake.run_argv

    async def run_argv(host, argv, **kwargs):
        seen.append(kwargs["timeout_s"])
        return await original(host, argv, **kwargs)

    fake.run_argv = run_argv
    await executor.execute(_request("docker.stack_pull"))
    await executor.execute(_request("docker.stack_stop"))
    assert seen == [2700, 900]


@pytest.mark.asyncio
async def test_an_older_core_without_run_argv(tmp_path):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    executor._ctx.exec = SimpleNamespace(run=fake.run)
    done = await executor.execute(_request())
    assert (done.success, done.error) == (False, "Stack-Aktionen brauchen eine neuere Version von Nodvard Deck.")


@pytest.mark.asyncio
async def test_a_moved_stack_is_not_stopped(tmp_path):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    fake.rows = [row(working_dir="/srv/shop", config_files="/srv/shop/compose.yaml"), *[r for r in fake.rows if r["project"] != "shop"]]
    done = await executor.execute(_request("docker.stack_stop"))
    assert done.success is False and done.error.startswith("Übernommen aus /opt/stacks/shop")
    assert fake.argv_calls == []


def _checking(monkeypatch, verdict_ok: bool = True):
    from nodvard_deck_ext_docker import after_check, image_apply

    checked: list[list[str]] = []

    async def check_running(ctx, host, refs, **_):
        checked.append(refs)
        return image_apply.Verdict(ok=verdict_ok, text="läuft stabil" if verdict_ok else "web: Container läuft nicht (beendet, Exit-Code 1)."), None

    monkeypatch.setattr(after_check, "check_running", check_running)
    return checked


@pytest.mark.asyncio
async def test_after_up_the_containers_of_the_file_are_checked(tmp_path, monkeypatch):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    checked = _checking(monkeypatch)
    done = await executor.execute(_request("docker.stack_up"))
    assert (done.success, done.output) == (True, "Stack „shop“ angewendet.\nPrüfung danach: läuft stabil")
    assert checked == [["8408834a78d1", "18d495ffdea5"]]
    assert fake.argv_calls[-1] == ["sh", "-c", IN_FOLDER, "sh", FOLDER, *COMPOSE, "config", "--services"]


@pytest.mark.asyncio
async def test_a_crashed_service_counts_and_others_do_not(tmp_path, monkeypatch):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    # Der Arbeiter ist gleich nach dem Start mit Exit 1 beendet; `debug` steht nur unter einem Profil, `alt` nicht mehr in der Datei.
    fake.rows = [
        row(), row(id="18d495ffdea5", name="shop-worker-1", service="worker", state="exited", status="Exited (1) 2 seconds ago"),
        row(id="aaaaaaaaaaaa", name="shop-debug-1", service="debug", state="exited", status="Exited (1) 3 days ago"),
        row(id="bbbbbbbbbbbb", name="shop-alt-1", service="alt", state="exited", status="Exited (2) 9 days ago"),
    ]
    checked = _checking(monkeypatch, verdict_ok=False)
    done = await executor.execute(_request("docker.stack_up"))
    assert checked == [["8408834a78d1", "18d495ffdea5"]]
    assert (done.success, done.error) == (False, "Prüfung danach: web: Container läuft nicht (beendet, Exit-Code 1).")


@pytest.mark.asyncio
async def test_replicas_and_a_stack_without_containers(tmp_path, monkeypatch):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    checked = _checking(monkeypatch)
    fake.rows = [row(), row(id="cccccccccccc", name="shop-web-2"), row(id="18d495ffdea5", name="shop-worker-1", service="worker")]
    await executor.execute(_request("docker.stack_up"))
    assert checked == [["8408834a78d1", "cccccccccccc", "18d495ffdea5"]]
    fake.rows = []
    done = await executor.execute(_request("docker.stack_up"))
    assert done.output.endswith("Prüfung danach übersprungen: Docker zeigt zu den Diensten der Datei keinen Container.")
    assert fake.argv_calls[-2][-2:] == ["up", "-d"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError(), _SshTimeout("weg"), OSError("Verbindung abgerissen"), "lost"])
async def test_up_when_the_services_cannot_be_read(tmp_path, monkeypatch, error):
    error = _lost() if error == "lost" else error
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    _checking(monkeypatch)
    original = fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[-2:] == ["config", "--services"]:
            raise error
        return await original(host, argv, **kwargs)

    fake.run_argv = run_argv
    done = await executor.execute(_request("docker.stack_up"))
    assert done.success is True and "Prüfung danach nicht möglich" in done.output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("services", "rows", "note"),
    [
        (result(""), None, "Prüfung danach übersprungen: Die Datei hat keinen Dienst, der ohne Profil startet."),
        (result(stderr="kaputt", code=1), None, "Prüfung danach nicht möglich: Die Dienste der Datei ließen sich nicht lesen."),
        (result("andere\n"), None, "Prüfung danach übersprungen: Docker zeigt zu den Diensten der Datei keinen Container."),
        (result("web\n"), result(stderr="Cannot connect", code=1), "Prüfung danach nicht möglich: Die Container ließen sich nicht lesen."),
    ],
)
async def test_up_without_a_check_says_so(tmp_path, monkeypatch, services, rows, note):
    executor, store, fake = _executor(tmp_path)
    await store.add("h1", _stack())
    checked = _checking(monkeypatch)
    fake.services = services
    original = executor._stacks.stacks
    calls = 0

    async def stacks(host, project=None):
        nonlocal calls
        calls += 1
        if rows is not None and calls > 1:
            fake.list = rows
        return await original(host, project)

    executor._stacks.stacks = stacks
    done = await executor.execute(_request("docker.stack_up"))
    assert (done.success, done.output) == (True, f"Stack „shop“ angewendet.\n{note}")
    assert checked == []


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_waits_for_approval_and_then_runs(client, docker_on):
    shown = {"working_dir": FOLDER, "config_files": [f"{FOLDER}/compose.yaml"]}
    assert (await client.post(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/adopt", json=shown, headers=docker_on.owner)).status_code == 200
    proposed = await client.post(
        f"/api/v1/hosts/{docker_on.host}/actions/docker.stack_stop",
        json={"payload": {"project": "shop"}, "reason": "Test"}, headers=docker_on.owner,
    )
    assert proposed.status_code == 202, proposed.text
    assert proposed.json()["status"] == "proposed" and proposed.json()["risk"] == "medium"
    assert docker_on.exec.argv_calls == [], "vor der Freigabe läuft nichts"
    approved = await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=docker_on.owner)
    assert approved.json()["status"] == "succeeded", approved.json()
    assert docker_on.exec.argv_calls[0][-1] == "stop"


@pytest.mark.asyncio
async def test_no_stack_actions_on_a_server_without_docker(client, docker_on):
    refused = await client.post(
        f"/api/v1/hosts/{docker_on.other}/actions/docker.stack_down",
        json={"payload": {"project": "shop"}, "reason": "Test"}, headers=docker_on.owner,
    )
    assert refused.status_code == 404
