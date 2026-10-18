"""Update je Stack: holen, alte Images sichern, nur geänderte Dienste neu erstellen, prüfen und bei Bedarf zurück."""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import sys
from types import SimpleNamespace

import pytest
from docker_stack_fakes import EXT_DIR, HASHES, FakeExec, docker_on, result, row  # noqa: F401 - Fixtures


@pytest.fixture(autouse=True)
def _ext_docker_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


OLD, NEW, NEW2 = "sha256:" + "a" * 64, "sha256:" + "b" * 64, "sha256:" + "c" * 64
FOLDER = "/opt/stacks/shop"
APPLIED = re.sub(r"^web [0-9a-f]{64}$", f"web {row()['hash']}", HASHES, flags=re.M)
"""Die Prüfsummen, wenn die Datei zu den laufenden Containern passt."""


def _mods():
    from nodvard_deck_ext_docker import adopt, after_check, image_apply, stack_actions, stack_backup, stack_update, stacks

    return SimpleNamespace(adopt=adopt, after_check=after_check, image_apply=image_apply, stack_actions=stack_actions,
                           stack_backup=stack_backup, stack_update=stack_update, stacks=stacks)


async def _async(value):
    return value


class Docker:
    """Ein Server mit den Containern des Stacks „shop“: Image-Namen, Image-IDs und was ein Pull bringt. Ein Image ohne
    Namen kennt er nicht mehr, als wäre inzwischen aufgeräumt worden."""

    def __init__(self, fake: FakeExec) -> None:
        self.fake = fake
        fake.hashes = result(APPLIED)
        self.images = {"busybox:1.37": OLD, "redis:7": OLD}
        self.remote = {"busybox:1.37": NEW, "redis:7": NEW2}
        self.containers = {"web": ["shop-web-1", "busybox:1.37", OLD], "worker": ["shop-worker-1", "redis:7", OLD]}
        self.mounts = {"web": ["shop_data"], "worker": ["shop_cache"]}
        self.free_kb = 50 * 1024 * 1024
        self.stopped: set[str] = set()
        self.pull_always: set[str] = set()
        self.stuck: set[str] = set()
        """Dienste, deren Container `up` nicht neu erstellt."""
        self.fail: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.original = fake.run_argv
        fake.run_argv = self.run_argv

    async def run_argv(self, host, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[:2] == ["docker", "inspect"] and "--format" in argv:
            names = argv[argv.index("--format") + 2:]
            rows = [{"name": "/" + name, "image_id": iid, "ref": ref, "service": service, "running": service not in self.stopped,
                     "mounts": [{"Type": "volume", "Name": v} for v in self.mounts[service]] + [{"Type": "bind", "Source": "/etc"}]}
                    for service, (name, ref, iid) in self.containers.items() if name in names]
            return result("\n".join(json.dumps(r) for r in rows) + "\n")
        if argv[:3] == ["docker", "image", "inspect"]:
            found = self.images.get(argv[-1])
            return result(f"{found}\n") if found else result(stderr=f"Error: No such image: {argv[-1]}\n", code=1)
        if argv[:2] == ["docker", "tag"]:
            source = argv[2] if argv[2].startswith("sha256:") else self.images.get(argv[2])
            failing = self.fail.get("tag") if "tag" in self.fail and argv[3].endswith(self.fail.get("tag_target", "")) else None
            if failing or source not in self.images.values():
                return result(stderr=failing or f"Error response from daemon: No such image: {argv[2]}\n", code=1)
            self.images[argv[3]] = source
            return result()
        if argv[:3] == ["docker", "image", "rm"]:
            return result() if self.images.pop(argv[3], None) else result(stderr="Error: No such image\n", code=1)
        if argv[:2] == ["df", "-Pk"]:
            return result(f"Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 100 1 {self.free_kb} 1% /\n")
        if argv[:2] == ["docker", "run"]:
            if "archive_hangs" in self.fail:
                await asyncio.sleep(3600)
            if "archive" in self.fail:
                return result(stderr=self.fail["archive"], code=1)
            return result()
        if argv[:2] == ["docker", "pull"]:
            if "backup_pull" in self.fail:
                return result(stderr=self.fail["backup_pull"], code=1)
            self.images[argv[-1]] = NEW
            return result()
        for verb in ("stop", "start"):
            if verb in argv:
                return result(stderr=self.fail.get(verb, ""), code=1 if verb in self.fail else 0)
        if "pull" in argv:
            if "pull_hangs" in self.fail:
                await asyncio.sleep(3600)
            if "pull" in self.fail:
                return result(stderr=self.fail["pull"], code=1)
            for service in argv[argv.index("pull") + 1:]:
                ref = self.containers[service][1]
                self.images[ref] = self.remote[ref]
            return result()
        if "up" in argv:
            if "up_hangs" in self.fail:
                await asyncio.sleep(3600)
            if "up_timeout" in self.fail:
                self.fail.pop("up_timeout")
                raise TimeoutError
            if "up" in self.fail:
                return result(stderr=self.fail.pop("up"), code=1)
            never = "--pull" in argv and argv[argv.index("--pull") + 1] == "never"
            services = argv[argv.index("--pull") + 2:] if never else argv[argv.index("--no-build") + 1:]
            for service in services:
                ref = self.containers[service][1]
                if service in self.pull_always and not never:
                    self.images[ref] = self.remote[ref]
                if service not in self.stuck:
                    self.containers[service][2] = self.images[ref]
            return result()
        return await self.original(host, argv, **kwargs)

    def changing(self):
        return [c for c in self.calls if c[:2] in (["docker", "tag"], ["docker", "run"]) or c[:3] == ["docker", "image", "rm"]
                or any(verb in c for verb in ("pull", "up", "stop", "start"))]

    def pending(self):
        return [ref for ref in self.images if ref.endswith(":pending")]


class Images:
    def __init__(self, statuses=None) -> None:
        self.statuses = statuses if statuses is not None else {"shop-web-1": "update", "shop-worker-1": "current"}
        self.rechecked: list[str] = []

    def result_for(self, host_id, container):
        status = self.statuses.get(container)
        return {"status": status, "reason": None} if status else None

    async def recheck_container(self, host, name):
        self.rechecked.append(name)


async def _setup(tmp_path, monkeypatch, *, verdicts=(True,), statuses=None):
    m = _mods()
    fake = FakeExec()
    docker = Docker(fake)
    hosts = SimpleNamespace(get=lambda host_id: _async(SimpleNamespace(id=host_id, tags=["docker"]) if host_id == "h1" else None))
    ctx = SimpleNamespace(exec=fake, hosts=hosts)
    store = m.adopt.AdoptedStacks(tmp_path)
    st = m.stacks.DockerStacks(ctx, store)
    await store.add("h1", [s for s in m.stacks.parse_members(fake.list.stdout) if s.project == "shop"][0])
    actions = m.stack_actions.DockerStackExecutor(ctx, st, store)
    images = Images(statuses)
    backups = m.stack_update.StackBackups(tmp_path)
    resources = SimpleNamespace(
        forget=lambda host_id: None,
        snapshot=lambda host: _async(SimpleNamespace(volumes=[{"name": "shop_data", "size_bytes": 2 * 1024**3}, {"name": "shop_cache", "size_bytes": None}])),
    )
    update = m.stack_update.StackUpdate(ctx, st, images, actions, backups, m.stack_backup.VolumeBackup(ctx, resources))
    answers = list(verdicts)
    checked: list[list[str]] = []

    async def check_running(ctx, host, refs, **_):
        checked.append(list(refs))
        ok = answers.pop(0) if answers else True
        return m.image_apply.Verdict(ok=ok, text="läuft stabil." if ok else "Container startet immer wieder neu."), None

    monkeypatch.setattr(m.after_check, "check_running", check_running)
    return SimpleNamespace(m=m, docker=docker, images=images, update=update, checked=checked, store=store, backups=backups, fake=fake,
                           host=SimpleNamespace(id="h1"), stack=store.for_host("h1")["shop"].stack("shop"),
                           executor=m.stack_update.DockerStackUpdateExecutor(ctx, update, images))


def _request(action="docker.stack_update", services=("web",), risk="medium", actor="user", project="shop", backup=False):
    from nodvard_sdk.actions import ActionRequest
    from nodvard_sdk.types import Actor, Risk

    return ActionRequest(action_type=action, host_ref="h1", risk=Risk(risk), proposed_by=Actor(type=actor, id="u1"), reason="Test",
                         payload={"project": project, "services": list(services), "backup": backup})


def _previous(env, service="web"):
    return env.m.stack_update.rollback_ref("shop", service)


def test_rollback_names_and_commands():
    su = _mods().stack_update
    ref = su.rollback_ref("shop", "web")
    assert ref.startswith("lattice-rollback/stack-shop-web-") and ref.endswith(":previous")
    assert su.rollback_ref("shop", "web") != su.rollback_ref("shop-web", "x") != su.rollback_ref("sh", "op-web")
    assert su.cmd_tag(OLD, ref) == ["docker", "tag", OLD, ref]
    assert su.cmd_untag(ref) == ["docker", "image", "rm", ref]
    assert su.cmd_container_images(["shop-web-1"])[:4] == ["docker", "inspect", "--type", "container"]
    for bad in (("Shop", "web"), ("shop", "web;id"), ("shop", "")):
        with pytest.raises(ValueError):
            su.rollback_ref(*bad)
    for bad in (("$(id)", ref), (OLD, "a b")):
        with pytest.raises(ValueError):
            su.cmd_tag(*bad)
    for bad in ("busybox:1.37", "lattice-rollback/stack-x:latest", "lattice-rollback/andere:previous"):
        with pytest.raises(ValueError):
            su.cmd_untag(bad)
    with pytest.raises(ValueError):
        su.cmd_container_images(["a;b"])


def test_reading_the_containers():
    su = _mods().stack_update
    rows = [
        {"name": "/shop-web-1", "image_id": OLD, "ref": "busybox:1.37", "service": "web", "running": True},
        {"name": "/shop-web-2", "image_id": NEW, "ref": "busybox:1.37", "service": "web", "running": False},
        {"name": "/shop-db-1", "image_id": OLD, "ref": "redis:7", "service": "db", "running": False},
        {"name": "/kaputt", "image_id": "sha256:x", "ref": "redis:7", "service": "db", "running": True},
    ]
    found = su.parse_container_images("\n".join(json.dumps(r) for r in rows) + "\nkein json\n")
    assert found["web"].containers == ("shop-web-1", "shop-web-2") and found["web"].mixed and found["web"].running
    assert found["db"].containers == ("shop-db-1",) and not found["db"].mixed and not found["db"].running
    assert su.with_sharers({"web": "a:1", "worker": "a:1", "db": "b:1"}, ["web"]) == ["web", "worker"]
    assert su.with_sharers({"web": "a:1", "worker": "a:1"}, ["worker", "web"]) == ["worker", "web"]


@pytest.mark.asyncio
async def test_the_backup_notes(tmp_path):
    su = _mods().stack_update
    backups = su.StackBackups(tmp_path)
    assert backups.get("h1", "shop") == {}
    entry = su.Backup("busybox:1.37", OLD, NEW, "2026-10-16T04:00:00+00:00")
    await backups.put("h1", "shop", {"web": entry, "worker": su.Backup("redis:7", OLD, NEW2, "x")})
    await backups.put("h1", "media", {"app": entry})
    assert backups.get("h1", "shop")["web"] == entry
    await backups.drop("h1", "shop", ["worker"])
    assert list(backups.get("h1", "shop")) == ["web"]
    await backups.drop("h1", "shop")
    assert backups.get("h1", "shop") == {} and list(backups.get("h1", "media")) == ["app"]
    (tmp_path / su.BACKUPS_FILE).write_text("{kaputt", encoding="utf-8")
    assert backups.get("h1", "media") == {}
    (tmp_path / su.BACKUPS_FILE).write_text(json.dumps({"backups": {"h1": {"shop": {"web": {"ref": "x y", "before": OLD, "after": NEW},
                                                                                      "b;c": vars(entry)}}}}), encoding="utf-8")
    assert backups.get("h1", "shop") == {}


@pytest.mark.asyncio
async def test_the_plan(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    plan = await env.update.plan(env.host, env.stack)
    assert plan["updates"] == ["web"] and plan["rollback"] == [] and (plan["data_store"], plan["risk"]) == (False, "medium")
    assert (plan["unapplied"], plan["unapplied_error"]) == ([], None)
    assert [s["status"] for s in plan["services"]] == ["update", "current"]
    env.docker.images[_previous(env)] = NEW2
    assert (await env.update.plan(env.host, env.stack))["rollback"] == [], "eine Sicherung ohne Vermerk wird nie angeboten"
    env.images.statuses["shop-worker-1"] = "update"
    plan = await env.update.plan(env.host, env.stack)
    assert (plan["updates"], plan["data_store"], plan["risk"]) == (["web", "worker"], True, "high"), "redis ist ein Datenspeicher"
    env.docker.stopped.add("worker")
    plan = await env.update.plan(env.host, env.stack)
    assert plan["updates"] == ["web"] and plan["services"][1]["stopped"] is True


@pytest.mark.asyncio
async def test_update_keeps_the_old_image(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    done = await env.executor.execute(_request())
    assert done.success is True, done.error
    assert done.output == "Stack „shop“ aktualisiert: web. Prüfung danach: läuft stabil. Die alten Images bleiben als Sicherung."
    assert env.docker.images[_previous(env)] == OLD and env.docker.containers["web"][2] == NEW and env.docker.containers["worker"][2] == OLD
    up = [c for c in env.docker.calls if "up" in c][0]
    assert up[-7:] == ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "web"]
    assert up[:5] == ["sh", "-c", env.m.stack_actions.IN_FOLDER, "sh", FOLDER]
    assert env.checked == [["shop-web-1"]] and env.docker.pending() == [] and env.images.rechecked == ["shop-web-1"]
    tags = [c for c in env.docker.calls if c[:2] == ["docker", "tag"]]
    assert tags[0][2] == OLD and tags[0][3].endswith(":pending"), "festgehalten, bevor geholt wird"
    assert env.docker.calls.index(tags[0]) < next(i for i, c in enumerate(env.docker.calls) if "pull" in c)
    noted = env.backups.get("h1", "shop")["web"]
    assert (noted.ref, noted.before, noted.after) == ("busybox:1.37", OLD, NEW)
    plan = await env.update.plan(env.host, env.stack)
    assert plan["rollback"] == ["web"] and plan["services"][0]["backup_at"] == noted.at


@pytest.mark.asyncio
async def test_nothing_new_changes_nothing(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.remote["busybox:1.37"] = OLD
    done = await env.executor.execute(_request())
    assert (done.success, done.output) == (True, "Für „shop“ gab es nichts Neues; die Dienste laufen weiter wie bisher.")
    assert not any("up" in c for c in env.docker.calls) and env.docker.pending() == []
    assert _previous(env) not in env.docker.images and env.backups.get("h1", "shop") == {}


@pytest.mark.asyncio
async def test_only_changed_services_are_recreated(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch, statuses={"shop-web-1": "update", "shop-worker-1": "update"})
    env.docker.remote["redis:7"] = OLD
    done = await env.executor.execute(_request(services=("web", "worker"), risk="high"))
    assert done.success is True, done.error
    assert [c[-1] for c in env.docker.calls if "up" in c] == ["web"] and env.docker.pending() == []
    assert list(env.backups.get("h1", "shop")) == ["web"]


@pytest.mark.asyncio
async def test_a_failed_pull_changes_nothing(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fail["pull"] = "Error response from daemon: pull access denied\n"
    done = await env.executor.execute(_request())
    assert done.error == ("Holen der Images ist gescheitert: Error response from daemon: pull access denied Die Dienste laufen weiter wie "
                          "bisher; einzelne neue Images können schon geladen sein.")
    assert not any("up" in c for c in env.docker.calls) and env.docker.pending() == []


@pytest.mark.asyncio
async def test_a_cancelled_update_releases_what_it_held(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fail["pull_hangs"] = ""
    task = asyncio.create_task(env.executor.execute(_request()))
    while not any("pull" in c for c in env.docker.calls):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(20):
        await asyncio.sleep(0)
    assert env.docker.pending() == [] and env.update.actions.locks.held("h1", "shop") is None


@pytest.mark.asyncio
async def test_a_running_image_without_a_name_cannot_be_kept(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.containers["web"][2] = NEW2
    done = await env.executor.execute(_request())
    assert done.error.startswith("Sicherung ließ sich nicht anlegen: Error response from daemon: No such image")
    assert done.error.endswith("Läuft „web“ mit einem Image, das keinen Namen mehr hat, wende den Stack einmal an. Geändert wurde nichts.")
    assert not any("pull" in c or "up" in c for c in env.docker.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [":pending", ":previous"])
async def test_a_failed_backup_stops_before_anything_restarts(tmp_path, monkeypatch, target):
    env = await _setup(tmp_path, monkeypatch, statuses={"shop-web-1": "update", "shop-worker-1": "update"})
    env.docker.fail.update({"tag": "Error: no space left on device\n", "tag_target": target})
    if target == ":pending":
        env.docker.fail["tag_target"] = env.m.stack_update.rollback_ref("shop", "worker", "pending")
    done = await env.executor.execute(_request(services=("web", "worker"), risk="high"))
    assert done.error.startswith("Sicherung ließ sich nicht anlegen: Error: no space left on device")
    assert not any("up" in c for c in env.docker.calls) and env.docker.containers["web"][2] == OLD
    assert env.docker.pending() == [] and env.backups.get("h1", "shop") == {}


@pytest.mark.asyncio
async def test_a_crash_after_the_update_goes_back(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch, verdicts=(False, True))
    done = await env.executor.execute(_request())
    assert done.success is False
    assert done.error == ("Update von web nicht sauber gelaufen. Prüfung danach: Container startet immer wieder neu. "
                          "Zurück auf der vorigen Version, sie läuft wieder. läuft stabil.")
    assert env.docker.containers["web"][2] == OLD and env.docker.images["busybox:1.37"] == OLD
    assert env.images.rechecked == ["shop-web-1"]


@pytest.mark.asyncio
async def test_a_failed_up_goes_back_too(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fail["up"] = "Error response from daemon: port is already allocated\n"
    done = await env.executor.execute(_request())
    assert done.error.startswith("Update von web nicht sauber gelaufen. Neu erstellen ist gescheitert: Error response from daemon: port is already allocated")
    assert "Zurück auf der vorigen Version, sie läuft wieder." in done.error and env.docker.containers["web"][2] == OLD


@pytest.mark.asyncio
async def test_an_update_that_did_not_take_goes_back(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.stuck.add("web")
    done = await env.executor.execute(_request())
    assert done.error.startswith("Update von web nicht sauber gelaufen. web läuft nicht mit dem erwarteten Image.")
    assert env.checked == [["shop-web-1"]], "geprüft wird erst, wenn das richtige Image läuft, hier nur nach dem Rückweg"


@pytest.mark.asyncio
async def test_pull_policy_always_cannot_undo_the_way_back(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch, verdicts=(False, True))
    env.docker.pull_always.add("web")
    done = await env.executor.execute(_request())
    assert done.error.endswith("Zurück auf der vorigen Version, sie läuft wieder. läuft stabil.")
    assert env.docker.containers["web"][2] == OLD


@pytest.mark.asyncio
async def test_a_way_back_that_did_not_take_says_so(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch, verdicts=(False,))
    original = env.docker.run_argv

    async def stuck_on_the_way_back(host, argv, **kwargs):
        if "up" in argv and env.docker.images["busybox:1.37"] == OLD:
            env.docker.stuck.add("web")
        return await original(host, argv, **kwargs)

    env.fake.run_argv = stuck_on_the_way_back
    done = await env.executor.execute(_request())
    assert done.error.endswith("Der Rückweg hat nicht gegriffen: web läuft nicht mit dem erwarteten Image.")


@pytest.mark.asyncio
async def test_no_check_possible_counts_as_not_clean(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    calls = []
    original = env.update.running

    async def unreadable_after_up(host, stack):
        calls.append(1)
        found = await original(host, stack)
        if any("up" in c for c in env.docker.calls) and len(calls) == 3:
            raise OSError("weg")
        return found

    env.update.running = unreadable_after_up
    done = await env.executor.execute(_request())
    assert done.error.startswith("Update von web nicht sauber gelaufen. Prüfung danach: nicht möglich.")


@pytest.mark.asyncio
async def test_an_up_that_runs_out_of_time_does_not_go_back(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fail["up_timeout"] = ""
    done = await env.executor.execute(_request())
    assert done.error == ("Update von web nicht sauber gelaufen. Neu erstellen ist gescheitert: nach 15 Minuten ohne Ende abgebrochen. "
                          "Compose kann auf dem Server noch laufen, deshalb geht es nicht von selbst zurück. Prüfe den Stack; „Zurück "
                          "zur vorigen Version“ steht im Reiter Stacks bereit.")
    assert len([c for c in env.docker.calls if "up" in c]) == 1


@pytest.mark.asyncio
async def test_data_stores_need_high_and_never_go_back_by_themselves(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch, verdicts=(False,), statuses={"shop-worker-1": "update"})
    refused = await env.executor.execute(_request(services=("worker",)))
    assert refused.error.startswith("Dabei ist ein Datenspeicher; dafür braucht es eine Freigabe mit hohem Risiko.")
    assert env.docker.changing() == []
    done = await env.executor.execute(_request(services=("worker",), risk="high"))
    assert done.error.startswith("Update von worker nicht sauber gelaufen. Prüfung danach: Container startet immer wieder neu. Weil ein Datenspeicher dabei ist")
    assert env.docker.containers["worker"][2] == NEW2, "kein Rücksprung"


@pytest.mark.asyncio
async def test_going_back_by_hand(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    assert (await env.executor.execute(_request())).success is True
    medium = await env.executor.execute(_request("docker.stack_rollback"))
    assert medium.error == "Der Rückweg braucht eine Freigabe mit hohem Risiko."
    env.images.rechecked.clear()
    back = await env.executor.execute(_request("docker.stack_rollback", risk="high"))
    await asyncio.sleep(0)
    assert back.success is True and back.output.startswith("Stack „shop“: Zurück auf der vorigen Version, sie läuft wieder.")
    assert env.docker.containers["web"][2] == OLD and env.images.rechecked == ["shop-web-1"] and env.docker.pending() == []
    assert [c for c in env.docker.calls if "up" in c][-1][-3:] == ["--pull", "never", "web"]
    assert env.backups.get("h1", "shop") == {}
    again = await env.executor.execute(_request("docker.stack_rollback", risk="high"))
    assert again.error.startswith("Für „web“ passt die Sicherung nicht mehr zum laufenden Dienst")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "meanwhile",
    [
        lambda env: env.docker.containers["web"].__setitem__(1, "nginx:1.30"),
        lambda env: env.docker.containers["web"].__setitem__(2, NEW2),
        lambda env: env.docker.images.__setitem__(env.m.stack_update.rollback_ref("shop", "web"), NEW2),
        lambda env: env.docker.images.pop(env.m.stack_update.rollback_ref("shop", "web")),
    ],
    ids=["anderes Image in der Datei", "anders aktualisiert", "Sicherung umgehängt", "Sicherung weg"],
)
async def test_a_backup_that_no_longer_fits_is_never_used(tmp_path, monkeypatch, meanwhile):
    env = await _setup(tmp_path, monkeypatch)
    assert (await env.executor.execute(_request())).success is True
    env.docker.images["nginx:1.30"] = NEW
    meanwhile(env)
    assert (await env.update.plan(env.host, env.stack))["rollback"] == []
    tags_before = len([c for c in env.docker.calls if c[:2] == ["docker", "tag"]])
    refused = await env.executor.execute(_request("docker.stack_rollback", risk="high"))
    assert refused.error == ("Für „web“ passt die Sicherung nicht mehr zum laufenden Dienst, etwa weil er inzwischen anders aktualisiert "
                             "oder umgestellt wurde. Zurückgesetzt wurde nichts.")
    assert len([c for c in env.docker.calls if c[:2] == ["docker", "tag"]]) == tags_before


@pytest.mark.asyncio
async def test_a_failed_tag_on_the_way_back_puts_the_names_back(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.images.statuses["shop-worker-1"] = "update"
    assert (await env.executor.execute(_request(services=("web", "worker"), risk="high"))).success is True
    env.docker.fail.update({"tag": "Error: kaputt\n", "tag_target": "redis:7"})
    back = await env.executor.execute(_request("docker.stack_rollback", services=("web", "worker"), risk="high"))
    assert back.error == "Rückweg gescheitert: Error: kaputt Zurückgesetzt wurde nichts; die Sicherungen liegen bereit."
    assert env.docker.images["busybox:1.37"] == NEW and env.docker.pending() == []
    assert not any("up" in c for c in env.docker.calls[-6:])


@pytest.mark.asyncio
async def test_services_with_the_same_image_go_together(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch, statuses={"shop-web-1": "update", "shop-worker-1": "current"})
    env.docker.containers["worker"][1] = "busybox:1.37"
    done = await env.executor.execute(_request())
    assert done.success is True and done.output.startswith("Stack „shop“ aktualisiert: web, worker.")
    assert env.docker.containers["worker"][2] == NEW and sorted(env.backups.get("h1", "shop")) == ["web", "worker"]
    back = await env.executor.execute(_request("docker.stack_rollback", risk="high"))
    assert back.success is True and env.docker.containers["worker"][2] == OLD


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prepare", "error"),
    [
        (lambda env: env.docker.stopped.add("web"), "„web“ ist gestoppt und würde dabei gestartet. Starte ihn zuerst, oder er bekommt das Update beim nächsten Anwenden."),
        (lambda env: env.docker.containers["web"].__setitem__(1, "busybox@sha256:" + "d" * 64),
         "„web“ steht in der Datei mit einer festen Prüfsumme (…@sha256:…); dafür gibt es kein Update und keinen Rückweg."),
        (lambda env: setattr(env.fake, "hashes", result(HASHES)),
         "In der Datei steht für web etwas anderes, als gerade läuft. Ein Update würde diese Änderung mit anwenden. Wende den Stack zuerst an "
         "oder nimm die Änderung zurück, dann versuch es noch einmal."),
        (lambda env: setattr(env.fake, "hashes", result(stderr="Cannot connect\n", code=1)),
         "Ob die Datei Änderungen hat, die noch nicht angewendet sind, ließ sich nicht prüfen: Cannot connect"),
    ],
)
async def test_what_would_change_more_than_the_image_is_refused(tmp_path, monkeypatch, prepare, error):
    env = await _setup(tmp_path, monkeypatch)
    prepare(env)
    done = await env.executor.execute(_request())
    assert done.error == f"{error} Geändert wurde nichts."
    assert env.docker.changing() == []


@pytest.mark.asyncio
async def test_unapplied_changes_block_the_way_back_too(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    assert (await env.executor.execute(_request())).success is True
    env.fake.hashes = result(HASHES)
    plan = await env.update.plan(env.host, env.stack)
    assert plan["unapplied"] == ["web"]
    back = await env.executor.execute(_request("docker.stack_rollback", risk="high"))
    assert back.error.startswith("In der Datei steht für web etwas anderes, als gerade läuft. Der Rückweg würde diese Änderung mit anwenden.")


@pytest.mark.asyncio
async def test_unreadable_containers_say_so(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    calls = []
    original = env.update._stacks.stacks

    async def flaky(host, project=None):
        calls.append(project)
        if len(calls) > 1:
            raise env.m.stacks.StackProblem("Server nicht erreichbar: weg")
        return await original(host, project)

    env.update._stacks.stacks = flaky
    assert (await env.executor.execute(_request())).error == (
        "Die Container ließen sich nicht lesen. Server nicht erreichbar: weg Geändert wurde nichts."
    )
    assert env.update.actions.locks.held("h1", "shop") is None


def _connection_lost():
    return pytest.importorskip("asyncssh").ConnectionLost("Connection lost")


def _lose_at(env, matches, times=1):
    """Lässt die nächsten `times` Aufrufe, auf die `matches` passt, mit abgerissener SSH-Verbindung scheitern."""
    original = env.fake.run_argv
    left = [times]

    async def run_argv(host, argv, **kwargs):
        if left[0] and matches(argv):
            left[0] -= 1
            raise _connection_lost()
        return await original(host, argv, **kwargs)

    env.fake.run_argv = run_argv


@pytest.mark.asyncio
async def test_a_lost_connection_during_up_does_not_go_back(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    _lose_at(env, lambda argv: "up" in argv)
    done = await env.executor.execute(_request())
    assert done.success is False and done.error.startswith(
        "Update von web nicht sauber gelaufen. Neu erstellen ist gescheitert: die Verbindung zum Server ist abgebrochen. Compose kann "
        "auf dem Server noch laufen, deshalb geht es nicht von selbst zurück."
    )
    assert len([c for c in env.docker.calls if "up" in c]) == 0 and env.docker.pending() == []


@pytest.mark.asyncio
async def test_a_lost_connection_while_holding_the_old_image_is_no_nameless_image(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    _lose_at(env, lambda argv: argv[:2] == ["docker", "tag"])
    done = await env.executor.execute(_request())
    assert done.error == "Sicherung ließ sich nicht anlegen: die Verbindung zum Server ist abgebrochen. Geändert wurde nichts."
    assert not any("pull" in c or "up" in c for c in env.docker.calls)
    pending = env.m.stack_update.rollback_ref("shop", "web", "pending")
    assert ["docker", "image", "rm", pending] in env.docker.calls, "ein vielleicht schon gesetzter Name wird wieder entfernt"


@pytest.mark.asyncio
async def test_a_lost_connection_during_up_on_the_way_back_says_compose_may_still_run(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    assert (await env.executor.execute(_request())).success is True
    _lose_at(env, lambda argv: "up" in argv)
    back = await env.executor.execute(_request("docker.stack_rollback", risk="high"))
    assert back.error.endswith("Rückweg gescheitert: die Verbindung zum Server ist abgebrochen. Compose kann auf dem Server noch laufen; "
                               "sieh in ein paar Minuten im Reiter Stacks nach, mit welcher Version die Dienste laufen.")


@pytest.mark.asyncio
async def test_a_lost_connection_after_the_pull_updates_nothing(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    pulled = []
    _lose_at(env, lambda argv: argv[:3] == ["docker", "image", "inspect"] and bool(pulled))
    original = env.fake.run_argv

    async def note(host, argv, **kwargs):
        if "pull" in argv:
            pulled.append(argv)
        return await original(host, argv, **kwargs)

    env.fake.run_argv = note
    done = await env.executor.execute(_request())
    assert done.error == ("Docker auf dem Server ließ sich nicht abfragen (die Verbindung zum Server ist abgebrochen). Aktualisiert "
                          "wurde nichts; einzelne neue Images können schon geladen sein.")
    assert not any("up" in c for c in env.docker.calls) and env.docker.pending() == [] and env.update.actions.locks.held("h1", "shop") is None


@pytest.mark.asyncio
async def test_a_lost_connection_while_reading_changes_nothing(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    _lose_at(env, lambda argv: argv[:2] == ["docker", "inspect"])
    done = await env.executor.execute(_request())
    assert done.error == ("Die Container ließen sich nicht lesen. Docker auf dem Server ließ sich nicht abfragen (die Verbindung zum "
                          "Server ist abgebrochen). Geändert wurde nichts.")
    assert env.docker.changing() == []


@pytest.mark.asyncio
async def test_an_unreadable_image_after_the_pull_is_no_all_clear(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    pulled = []
    original = env.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if "pull" in argv:
            pulled.append(argv)
        if pulled and argv[:3] == ["docker", "image", "inspect"]:
            return result(stderr="Error response from daemon: kaputt\n", code=1)
        return await original(host, argv, **kwargs)

    env.fake.run_argv = run_argv
    done = await env.executor.execute(_request())
    assert done.success is False
    assert done.error.startswith("Die Image-ID von web ließ sich nach dem Holen nicht lesen. Aktualisiert wurde nichts;")
    assert not any("up" in c for c in env.docker.calls)


@pytest.mark.asyncio
async def test_a_failed_inspect_of_the_containers_is_said(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    original = env.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[:2] == ["docker", "inspect"]:
            return result(stderr="Error response from daemon: Docker läuft nicht\n", code=1)
        return await original(host, argv, **kwargs)

    env.fake.run_argv = run_argv
    done = await env.executor.execute(_request())
    assert done.error == ("Die Container ließen sich nicht lesen. Docker auf dem Server ließ sich nicht abfragen (Error response from "
                          "daemon: Docker läuft nicht). Geändert wurde nichts.")


@pytest.mark.asyncio
async def test_cleanup_that_fails_does_not_replace_the_result(tmp_path, monkeypatch, caplog):
    env = await _setup(tmp_path, monkeypatch)
    original = env.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[:3] == ["docker", "image", "rm"]:
            raise RuntimeError("kaputt")
        return await original(host, argv, **kwargs)

    env.fake.run_argv = run_argv
    done = await env.executor.execute(_request())
    assert done.success is True and "docker_stack_pending_not_released" in caplog.text
    await env.executor.drop_backups(env.host, "shop", ["web"])
    assert "docker_stack_backup_not_untagged project=shop service=web" in caplog.text
    assert env.backups.get("h1", "shop") == {}


@pytest.mark.asyncio
async def test_a_lost_connection_in_the_check_after_up_is_no_crash(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    ups = []
    original = env.fake.run_argv

    async def note(host, argv, **kwargs):
        if "up" in argv:
            ups.append(argv)
        return await original(host, argv, **kwargs)

    env.fake.run_argv = note
    _lose_at(env, lambda argv: argv[:2] == ["docker", "inspect"] and len(ups) == 1)
    done = await env.executor.execute(_request())
    assert done.success is False and "Mit welchem Image die Dienste jetzt laufen, ließ sich nicht prüfen." in done.error
    assert env.update.actions.locks.held("h1", "shop") is None


@pytest.mark.asyncio
async def test_a_deleted_stack_takes_its_backups_along(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    assert (await env.executor.execute(_request())).success is True
    env.docker.images[env.m.stack_update.rollback_ref("shop", "worker", "pending")] = OLD
    await env.executor.drop_backups(env.host, "shop", ["worker"])
    assert not [ref for ref in env.docker.images if ref.startswith("lattice-rollback/")]
    assert env.backups.get("h1", "shop") == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"actor": "ai"}, "Einen Stack aktualisiert nur ein Mensch über die Docker-Seite."),
        ({"project": "media"}, "Der Stack „media“ ist nicht übernommen. Übernimm ihn zuerst im Reiter Stacks."),
        ({"services": ("web;id",)}, "Ungültige Dienste."),
        ({"services": ()}, "Ungültige Dienste."),
        ({"services": ("fehlt",)}, "Zu fehlt gibt es gerade keinen Container. Wende den Stack an und versuch es dann noch einmal. Geändert wurde nichts."),
    ],
)
async def test_refusals_change_nothing(tmp_path, monkeypatch, change, error):
    env = await _setup(tmp_path, monkeypatch)
    assert (await env.executor.execute(_request(**change))).error == error
    assert env.docker.changing() == []


@pytest.mark.asyncio
async def test_one_run_per_stack_at_a_time(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    with env.update.actions.locks.hold("h1", "shop", "Löschen"):
        error = (await env.executor.execute(_request())).error
    assert error == "An diesem Stack läuft gerade schon etwas (Löschen). Versuch es danach noch einmal."
    env.update.actions.locks.outside = lambda host_id, project: "Image-Update eines Containers" if project == "shop" else None
    error = (await env.executor.execute(_request())).error
    assert error == "An diesem Stack läuft gerade schon etwas (Image-Update eines Containers). Versuch es danach noch einmal."
    assert env.docker.changing() == []


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


def _url(docker_on, tail="update"):
    return f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/{tail}"


async def _adopted(client, docker_on, monkeypatch):
    shown = {"working_dir": FOLDER, "config_files": [f"{FOLDER}/compose.yaml"]}
    assert (await client.post(_url(docker_on, "adopt"), json=shown, headers=docker_on.owner)).status_code == 200
    from nodvard_deck.ext.runtime import get_extension_runtime

    images = get_extension_runtime().loaded["docker"].instance._images
    monkeypatch.setattr(images, "result_for", lambda host_id, container: {"status": "update" if container == "shop-web-1" else "current"})
    docker = Docker(docker_on.exec)
    monkeypatch.setattr(docker_on.handle, "run_argv", docker.run_argv, raising=False)
    package = type(images).__module__.rsplit(".", 1)[0]
    after_check, image_apply = (importlib.import_module(f"{package}.{name}") for name in ("after_check", "image_apply"))

    async def check_running(ctx, host, refs, **_):
        return image_apply.Verdict(ok=True, text="läuft stabil."), None

    monkeypatch.setattr(after_check, "check_running", check_running)
    return docker


def _statuses(monkeypatch, statuses):
    from nodvard_deck.ext.runtime import get_extension_runtime

    images = get_extension_runtime().loaded["docker"].instance._images
    monkeypatch.setattr(images, "result_for", lambda host_id, container: {"status": statuses.get(container, "current")})


@pytest.mark.asyncio
async def test_the_route_checks_the_file_risk_and_shared_images(client, docker_on, monkeypatch):
    docker = await _adopted(client, docker_on, monkeypatch)
    docker_on.exec.hashes = result(HASHES)
    plan = await client.get(_url(docker_on), headers=docker_on.owner)
    assert plan.json()["unapplied"] == ["web"]
    refused = await client.post(_url(docker_on), json={"services": ["web"]}, headers=docker_on.owner)
    assert refused.status_code == 409 and refused.json()["detail"].startswith("In der Datei steht für web etwas anderes, als gerade läuft.")
    docker_on.exec.hashes = result(APPLIED)
    _statuses(monkeypatch, {"shop-worker-1": "update"})
    stored = await client.post(_url(docker_on), json={"services": ["worker"]}, headers=docker_on.owner)
    assert stored.status_code == 202 and stored.json()["risk"] == "high"
    docker.containers["worker"][1] = "busybox:1.37"
    _statuses(monkeypatch, {"shop-web-1": "update"})
    shared = await client.post(_url(docker_on), json={"services": ["web"]}, headers=docker_on.owner)
    assert shared.status_code == 202 and shared.json()["risk"] == "medium"
    action = (await client.get(f"/api/v1/actions/{shared.json()['action_id']}", headers=docker_on.owner)).json()
    assert action["payload"]["services"] == ["web", "worker"]
    assert "aktualisieren: web (mit worker, die dasselbe Image nutzen)." in action["reason"]


@pytest.mark.asyncio
async def test_a_backup_of_a_shared_volume_is_refused_before_approval(client, docker_on, monkeypatch):
    docker = await _adopted(client, docker_on, monkeypatch)
    docker.mounts["worker"] = ["shop_data"]
    _statuses(monkeypatch, {"shop-web-1": "update", "shop-worker-1": "update"})
    refused = await client.post(_url(docker_on), json={"services": ["web"], "backup": True}, headers=docker_on.owner)
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("Sicherung der Volumes nicht möglich: Das Volume shop_data nutzt auch der Dienst „worker“")
    actions = (await client.get("/api/v1/actions", headers=docker_on.owner)).json()
    assert not [a for a in (actions.get("items", actions) if isinstance(actions, dict) else actions) if a.get("action_type") == "docker.stack_update"]


@pytest.mark.asyncio
async def test_update_through_the_gate(client, docker_on, monkeypatch):
    docker = await _adopted(client, docker_on, monkeypatch)
    plan = await client.get(_url(docker_on), headers=docker_on.owner)
    assert plan.status_code == 200 and plan.json()["updates"] == ["web"]
    stale = await client.post(_url(docker_on), json={"services": ["worker"]}, headers=docker_on.owner)
    assert stale.status_code == 409 and "bitte neu laden" in stale.json()["detail"]
    proposed = await client.post(_url(docker_on), json={"services": ["web"]}, headers=docker_on.owner)
    assert proposed.status_code == 202, proposed.text
    assert (proposed.json()["status"], proposed.json()["risk"]) == ("proposed", "medium")
    assert docker.changing() == [], "vor der Freigabe läuft nichts"
    action = await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)
    assert "Stack „shop“ aktualisieren: web. Die alten Images bleiben als Sicherung." in action.text
    approved = await client.post(f"/api/v1/actions/{proposed.json()['action_id']}/approve", headers=docker_on.owner)
    assert approved.json()["status"] == "succeeded", approved.json()
    assert any("pull" in c for c in docker.calls)
    rollback = await client.post(_url(docker_on, "rollback"), json={"services": ["web"]}, headers=docker_on.owner)
    assert rollback.status_code == 202 and rollback.json()["risk"] == "high"


@pytest.mark.asyncio
async def test_update_routes_need_an_adopted_stack(client, docker_on):
    assert (await client.get(_url(docker_on), headers=docker_on.owner)).status_code == 409
    assert (await client.post(_url(docker_on), json={"services": []}, headers=docker_on.owner)).status_code == 422
    bad = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/Shop;id/update"
    assert (await client.get(bad, headers=docker_on.owner)).status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["docker.stack_update", "docker.stack_rollback"])
async def test_not_offered_on_the_server_page(client, docker_on, action):
    refused = await client.post(
        f"/api/v1/hosts/{docker_on.host}/actions/{action}", json={"payload": {"project": "shop", "services": ["web"]}, "reason": "Test"},
        headers=docker_on.owner,
    )
    assert refused.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "tail"), [("get", "update"), ("post", "update"), ("post", "rollback")])
async def test_only_with_hosts_execute(client, docker_on, method, tail):
    kwargs = {"json": {"services": ["web"]}} if method == "post" else {}
    assert (await getattr(client, method)(_url(docker_on, tail), **kwargs)).status_code == 401
    assert (await getattr(client, method)(_url(docker_on, tail), headers=docker_on.viewer, **kwargs)).status_code == 403


# --- Volumes vorher sichern ----------------------------------------------------------------------------------------


def test_backup_commands():
    sb = _mods().stack_backup
    folder = "/home/deck/nodvard-sicherungen/shop/20261016-041500"
    name = sb.archive_name("shop_data", folder)
    assert re.fullmatch(r"nodvard-sicherung-[0-9a-f]{20}", name) and name != sb.archive_name("shop_cache", folder)
    assert len(sb.archive_name("v" * 250, folder)) == len(name)
    assert sb.cmd_archive("shop_data", folder, "1000:1000") == [
        "docker", "run", "--rm", "--name", name, "--network", "none", "-v", "shop_data:/data:ro", "-v", f"{folder}:/backup",
        "busybox:1.37", "sh", "-c",
        'umask 077; tar czf "/backup/$1.part" -C /data . && chown "$2" "/backup/$1.part" && chmod 600 "/backup/$1.part" '
        '&& mv "/backup/$1.part" "/backup/$1" || { rc=$?; rm -f "/backup/$1.part"; exit "$rc"; }', "sh", "shop_data.tar.gz",
        "1000:1000",
    ]
    assert sb.cmd_stop_archive("shop_data", folder) == ["docker", "rm", "-f", name]
    with pytest.raises(ValueError):
        sb.cmd_stop_archive("a;b", folder)
    for bad in (("a;b", "/h", "1:1"), ("v", "/h:/x", "1:1"), ("v", "/h", "root"), ("v", "relativ", "1:1")):
        with pytest.raises(ValueError):
            sb.cmd_archive(*bad)
    assert sb.backup_folder("/home/deck", "shop", "20261016-041500") == "/home/deck/nodvard-sicherungen/shop/20261016-041500"
    with pytest.raises(ValueError):
        sb.backup_folder("/home/deck", "shop", "jetzt")
    assert sb.parse_free_space("Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 100 1 2048 1% /\n") == 2 * 1024 * 1024
    assert sb.parse_free_space("kaputt") is None
    assert "-v '/home/deck/mit leer/x:/backup' busybox:1.37" in sb.restore_hint("/home/deck/mit leer/x")


@pytest.mark.asyncio
async def test_the_plan_names_volumes_size_and_space(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    plan = await env.update.plan(env.host, env.stack)
    assert plan["services"][0]["volumes"] == ["shop_data"]
    assert plan["volume_backup"] == {"volumes": {"shop_data": 2 * 1024**3}, "free_bytes": 50 * 1024**3,
                                     "folder": "/home/deck/nodvard-sicherungen/shop/", "error": None}
    env.docker.free_kb = 1024 * 1024
    plan = await env.update.plan(env.host, env.stack)
    assert plan["volume_backup"]["error"] == "Zu wenig Platz: Die Volumes brauchen bis zu 2,0 GB, frei sind 1,0 GB."


@pytest.mark.asyncio
@pytest.mark.parametrize(("free_kb", "fits"), [(2306868, True), (2306867, False)])
async def test_the_space_check_keeps_a_tenth_extra(tmp_path, monkeypatch, free_kb, fits):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.free_kb = free_kb
    plan = await env.update.plan(env.host, env.stack)
    assert (plan["volume_backup"]["error"] is None) is fits


@pytest.mark.asyncio
async def test_an_unknown_volume_size_is_no_free_pass(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.mounts.update({"web": ["shop_data", "shop_cache"], "worker": []})
    plan = await env.update.plan(env.host, env.stack)
    assert plan["volume_backup"]["error"] == "Die Größe von shop_cache ist unbekannt, deshalb lässt sich der Platz nicht prüfen."
    done = await env.executor.execute(_request(backup=True))
    assert done.error.startswith("Sicherung der Volumes nicht möglich: Die Größe von shop_cache ist unbekannt")
    assert not any("stop" in c for c in env.docker.calls)


@pytest.mark.asyncio
async def test_update_with_backup_stops_saves_then_recreates(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    done = await env.executor.execute(_request(backup=True))
    assert done.success is True, done.error
    assert "Volumes gesichert in /home/deck/nodvard-sicherungen/shop/" in done.output and "Zurückspielen geht im Moment im Terminal" in done.output
    order = [next(k for k, test in (("hold", lambda c: c[:2] == ["docker", "tag"] and c[3].endswith(":pending")),
                                    ("pull", lambda c: "pull" in c), ("stop", lambda c: "stop" in c), ("archive", lambda c: c[:2] == ["docker", "run"]),
                                    ("keep", lambda c: c[:2] == ["docker", "tag"]), ("drop", lambda c: c[:3] == ["docker", "image", "rm"]),
                                    ("up", lambda c: "up" in c)) if test(c)) for c in env.docker.changing()]
    assert order == ["hold", "pull", "stop", "archive", "keep", "drop", "up"]
    archive = [c for c in env.docker.calls if c[:2] == ["docker", "run"]][0]
    assert "shop_data:/data:ro" in archive and archive[-1] == "1000:1000"
    sftp = env.docker.fake.sftp_server
    made = [d for d in sftp.dirs if d.startswith("/home/deck/nodvard-sicherungen")]
    assert len(made) == 3 and all(sftp.dir_modes[d] == 0o700 for d in made)
    assert not any(c[:2] == ["docker", "pull"] for c in env.docker.calls)


@pytest.mark.asyncio
async def test_the_archive_image_comes_before_anything_stops(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    for module in (env.m.stack_backup, env.m.stack_update):
        monkeypatch.setattr(module, "BACKUP_IMAGE", "busybox:1.36")
    assert (await env.executor.execute(_request(backup=True))).success is True
    verbs = [c[1] if c[1] == "pull" else next(v for v in ("stop", "run") if v in c) for c in env.docker.calls
             if c[:2] in (["docker", "pull"], ["docker", "run"]) or "stop" in c]
    assert verbs == ["pull", "stop", "run"]


@pytest.mark.asyncio
async def test_without_the_archive_image_nothing_stops(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    for module in (env.m.stack_backup, env.m.stack_update):
        monkeypatch.setattr(module, "BACKUP_IMAGE", "busybox:1.36")
    env.docker.fail["backup_pull"] = "Error: toomanyrequests\n"
    done = await env.executor.execute(_request(backup=True))
    assert done.error == ("busybox:1.36 für die Archive ließ sich nicht laden: Error: toomanyrequests Aktualisiert wurde nichts; die Dienste "
                          "laufen weiter mit der alten Version.")
    assert not any("stop" in c for c in env.docker.calls)


@pytest.mark.asyncio
async def test_the_reason_of_a_failed_backup_folder_stays(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fake.sftp_server.denied_paths.add("/home/deck/nodvard-sicherungen")
    done = await env.executor.execute(_request(backup=True))
    assert done.error.startswith("Der Ordner für die Sicherung ließ sich nicht anlegen: permission denied: /home/deck/nodvard-sicherungen")
    assert not any("stop" in c for c in env.docker.calls)


@pytest.mark.asyncio
async def test_a_broken_channel_after_a_failed_mkdir_is_named(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    sftp = env.docker.fake.sftp_server
    sftp.denied_paths.add("/home/deck/nodvard-sicherungen")
    lost = pytest.importorskip("asyncssh").SFTPConnectionLost("Kanal zu")

    async def broken(path):
        raise lost

    sftp.lstat = broken
    done = await env.executor.execute(_request(backup=True))
    assert done.error.startswith("Der Ordner für die Sicherung ließ sich nicht anlegen: Kanal zu"), done.error


@pytest.mark.asyncio
async def test_a_failed_archive_starts_the_services_again_and_updates_nothing(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fail["archive"] = "Error: no space left on device\n"
    done = await env.executor.execute(_request(backup=True))
    assert done.error == ("Sicherung von shop_data gescheitert: Error: no space left on device Aktualisiert wurde nichts; die Dienste laufen "
                          "weiter mit der alten Version.")
    assert any("start" in c for c in env.docker.calls) and not any("up" in c for c in env.docker.calls)
    assert env.docker.containers["web"][2] == OLD and env.docker.pending() == []
    assert _stamp_folders(env) == [], "ohne fertiges Archiv bleibt kein leerer Zeitordner"


def _stamp_folders(env):
    return sorted(d for d in env.docker.fake.sftp_server.dirs if d.startswith("/home/deck/nodvard-sicherungen/shop/"))


@pytest.mark.asyncio
async def test_a_backup_that_breaks_off_after_one_volume_names_what_is_left(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.mounts.update({"web": ["shop_data", "shop_more"], "worker": []})
    env.update.volumes._resources = SimpleNamespace(forget=lambda host_id: None, snapshot=lambda host: _async(SimpleNamespace(
        volumes=[{"name": "shop_data", "size_bytes": 1024}, {"name": "shop_more", "size_bytes": 1024}])))
    inner = env.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[:2] == ["docker", "run"] and "shop_more:/data:ro" in argv:
            env.docker.calls.append(list(argv))
            return result(stderr="Error: no space left on device\n", code=1)
        return await inner(host, argv, **kwargs)

    env.fake.run_argv = run_argv
    done = await env.executor.execute(_request(backup=True))
    [folder] = _stamp_folders(env)
    assert done.error.startswith("Sicherung von shop_more gescheitert: Error: no space left on device Schon gepackt: shop_data in "
                                 f"{folder}. Diese Sicherung ist unvollständig; brauchst du sie nicht, lösche den Ordner im Dateimanager.")
    assert any("start" in c for c in env.docker.calls) and not any("up" in c for c in env.docker.calls)


@pytest.mark.asyncio
async def test_a_failed_restart_after_a_failed_archive_is_not_hidden(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fail.update({"archive": "Error: no space left on device\n", "start": "Error: port is already allocated\n"})
    done = await env.executor.execute(_request(backup=True))
    assert done.error == ("Sicherung von shop_data gescheitert: Error: no space left on device Aktualisiert wurde nichts; die Dienste ließen "
                          "sich aber nicht wieder starten (Wieder starten gescheitert: Error: port is already allocated). Starte den Stack "
                          "im Reiter Stacks.")


@pytest.mark.asyncio
async def test_no_backup_without_space_or_when_stopping_fails(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.free_kb = 1024
    done = await env.executor.execute(_request(backup=True))
    assert done.error.startswith("Sicherung der Volumes nicht möglich: Zu wenig Platz")
    assert not any("stop" in c or "up" in c for c in env.docker.calls)
    env.docker.free_kb = 50 * 1024 * 1024
    env.docker.fail["stop"] = "Error: cannot stop\n"
    env.docker.calls.clear()
    stopped = await env.executor.execute(_request(backup=True))
    assert stopped.error.startswith("Stoppen vor der Sicherung gescheitert: Error: cannot stop")
    assert any("start" in c for c in env.docker.calls) and not any("up" in c for c in env.docker.calls)
    assert _stamp_folders(env) == []


@pytest.mark.asyncio
async def test_without_the_tick_nothing_is_saved(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    assert (await env.executor.execute(_request())).success is True
    assert not any(c[:2] == ["docker", "run"] or "stop" in c for c in env.docker.calls)


@pytest.mark.asyncio
async def test_services_stopped_for_the_backup_start_again_when_the_update_stops(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fail.update({"tag": "Error: no space left on device\n", "tag_target": ":previous"})
    done = await env.executor.execute(_request(backup=True))
    assert done.error.startswith("Sicherung ließ sich nicht anlegen: Error: no space left on device Aktualisiert wurde nichts; die neuen "
                                 "Images liegen schon bereit. Die Dienste sind wieder gestartet. Volumes gesichert in /home/deck/")
    assert [c for c in env.docker.changing() if "start" in c] and not any("up" in c for c in env.docker.calls)


@pytest.mark.asyncio
async def test_services_start_again_when_noting_the_backup_fails(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)

    async def broken(*_):
        raise OSError("Datenträger voll")

    monkeypatch.setattr(env.backups, "put", broken)
    done = await env.executor.execute(_request(backup=True))
    assert done.error.startswith("Die Sicherung ließ sich nicht vermerken (Datenträger voll). Aktualisiert wurde nichts; die neuen Images "
                                 "liegen schon bereit. Die Dienste sind wieder gestartet. Volumes gesichert in /home/deck/")
    assert [c for c in env.docker.changing() if "start" in c] and not any("up" in c for c in env.docker.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(("hang", "seen"), [("archive_hangs", lambda c: c[:2] == ["docker", "run"]), ("up_hangs", lambda c: "up" in c)])
async def test_a_cancel_after_stopping_starts_the_services_again(tmp_path, monkeypatch, hang, seen):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fail[hang] = ""
    task = asyncio.create_task(env.executor.execute(_request(backup=True)))
    while not any(seen(c) for c in env.docker.calls):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(20):
        await asyncio.sleep(0)
    assert any("start" in c for c in env.docker.calls) and env.docker.pending() == []


@pytest.mark.asyncio
async def test_a_volume_another_running_service_writes_to_is_not_backed_up(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.mounts["worker"] = ["shop_data"]
    plan = await env.update.plan(env.host, env.stack)
    assert plan["volume_backup"]["error"] == ("Das Volume shop_data nutzt auch der Dienst „worker“, der dabei weiterliefe; das Archiv "
                                              "wäre nicht verlässlich. Stoppe „worker“ vorher oder aktualisiere ohne Sicherung.")
    done = await env.executor.execute(_request(backup=True))
    assert done.error.startswith("Sicherung der Volumes nicht möglich: Das Volume shop_data nutzt auch der Dienst „worker“")
    assert done.error.endswith("Geändert wurde nichts; aktualisiere ohne Sicherung oder wähle die Dienste anders.")
    assert env.docker.changing() == [] and env.docker.pending() == []
    env.docker.stopped.add("worker")
    assert (await env.executor.execute(_request(backup=True))).success is True


@pytest.mark.asyncio
async def test_a_backup_without_named_volumes_is_not_skipped_silently(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.mounts["web"] = []
    done = await env.executor.execute(_request(backup=True))
    assert done.error == ("Sicherung der Volumes nicht möglich: Die Dienste mit Update haben keine benannten Volumes; ihre Daten liegen "
                          "in Ordnern auf dem Server. Geändert wurde nichts; aktualisiere ohne Sicherung oder wähle die Dienste anders.")
    assert env.docker.changing() == [] and env.docker.containers["web"][2] == OLD


def _archive_breaks(env, error):
    """Das Packen bricht ab, nachdem schon ein halbes Archiv auf dem Server liegt."""
    original = env.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[:2] == ["docker", "run"]:
            env.docker.calls.append(list(argv))
            folder = next(a for a in argv if a.endswith(":/backup"))[: -len(":/backup")]
            env.docker.fake.sftp_server.files[f"{folder}/shop_data.tar.gz.part"] = b"halb"
            raise error
        return await original(host, argv, **kwargs)

    env.fake.run_argv = run_argv


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TimeoutError(), "Minuten ohne Ende abgebrochen."),
        ("lost", "die Verbindung zum Server ist abgebrochen."),
    ],
)
async def test_an_archive_that_breaks_off_is_stopped_and_removed_before_the_restart(tmp_path, monkeypatch, error, reason):
    env = await _setup(tmp_path, monkeypatch)
    _archive_breaks(env, pytest.importorskip("asyncssh").ConnectionLost("Connection lost") if error == "lost" else error)
    done = await env.executor.execute(_request(backup=True))
    assert done.error.startswith("Sicherung von shop_data gescheitert: ")
    assert done.error.endswith(f"{reason} Aktualisiert wurde nichts; die Dienste laufen weiter mit der alten Version.")
    order = ["start" if "start" in c else c[1] for c in env.docker.calls if c[:2] in (["docker", "run"], ["docker", "rm"]) or "start" in c]
    assert order == ["run", "rm", "start"]
    archive = next(c for c in env.docker.calls if c[:2] == ["docker", "run"])
    assert next(c for c in env.docker.calls if c[:2] == ["docker", "rm"])[-1] == archive[archive.index("--name") + 1]
    assert not [p for p in env.docker.fake.sftp_server.files if ".tar.gz" in p]
    assert not any("up" in c for c in env.docker.calls)


@pytest.mark.asyncio
async def test_a_cancel_while_packing_stops_the_archive_before_the_restart(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fail["archive_hangs"] = ""
    task = asyncio.create_task(env.executor.execute(_request(backup=True)))
    while not any(c[:2] == ["docker", "run"] for c in env.docker.calls):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(20):
        await asyncio.sleep(0)
    after = [c for c in env.docker.calls if c[:2] == ["docker", "rm"] or "start" in c]
    assert after and after[0][:2] == ["docker", "rm"] and "start" in after[-1]


def _two_volumes(env):
    env.docker.mounts.update({"web": ["shop_data", "shop_more"], "worker": []})
    env.update.volumes._resources = SimpleNamespace(forget=lambda host_id: None, snapshot=lambda host: _async(SimpleNamespace(
        volumes=[{"name": "shop_data", "size_bytes": 1024}, {"name": "shop_more", "size_bytes": 1024}])))


def _archive_file(env, argv):
    folder = next(a for a in argv if a.endswith(":/backup"))[: -len(":/backup")]
    env.docker.fake.sftp_server.files[f"{folder}/{argv[-2]}"] = b"fertig"


async def _abort_while_packing(env, finished):
    """Bricht die Aktion ab, während nach `finished` fertigen Archiven das nächste gepackt wird."""
    _two_volumes(env)
    inner, archives = env.fake.run_argv, []

    async def run_argv(host, argv, **kwargs):
        if argv[:2] == ["docker", "run"] and any(a.endswith(":/data:ro") for a in argv):
            archives.append(argv)
            if len(archives) > finished:
                env.docker.calls.append(list(argv))
                await asyncio.sleep(3600)
            _archive_file(env, argv)
        return await inner(host, argv, **kwargs)

    env.fake.run_argv = run_argv
    task = asyncio.create_task(env.executor.execute(_request(backup=True)))
    while len(archives) <= finished and not task.done():
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_an_abort_while_packing_removes_the_empty_folder(tmp_path, monkeypatch, caplog):
    env = await _setup(tmp_path, monkeypatch)
    await _abort_while_packing(env, 0)
    assert _stamp_folders(env) == [] and any("start" in c for c in env.docker.calls)
    assert "after_abort" not in caplog.text


@pytest.mark.asyncio
async def test_an_abort_after_one_archive_keeps_it_and_logs_the_folder(tmp_path, monkeypatch, caplog):
    env = await _setup(tmp_path, monkeypatch)
    await _abort_while_packing(env, 1)
    [folder] = _stamp_folders(env)
    assert env.docker.fake.sftp_server.files[f"{folder}/shop_data.tar.gz"] == b"fertig"
    assert f"docker_stack_backup_left_after_abort folder={folder} packed=shop_data packing=shop_more" in caplog.text


@pytest.mark.asyncio
async def test_after_an_abort_a_failed_restart_is_logged(tmp_path, monkeypatch, caplog):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fail["start"] = "kaputt"
    await _abort_while_packing(env, 0)
    assert "docker_stack_services_not_started_after_abort project=shop reason=Wieder starten gescheitert: kaputt" in caplog.text
    assert _stamp_folders(env) == []


@pytest.mark.asyncio
async def test_an_abort_stays_an_abort_when_the_restart_raises(tmp_path, monkeypatch, caplog):
    env = await _setup(tmp_path, monkeypatch)
    inner = env.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if "start" in argv:
            raise RuntimeError("kaputt")
        return await inner(host, argv, **kwargs)

    env.fake.run_argv = run_argv
    await _abort_while_packing(env, 0)
    assert _stamp_folders(env) == [] and "docker_stack_clean_up_after_abort_failed" in caplog.text


@pytest.mark.asyncio
async def test_an_abort_while_cleaning_up_after_a_failure_still_logs_what_is_left(tmp_path, monkeypatch, caplog):
    env = await _setup(tmp_path, monkeypatch)
    _two_volumes(env)
    inner, starting = env.fake.run_argv, asyncio.Event()

    async def run_argv(host, argv, **kwargs):
        if argv[:2] == ["docker", "run"] and "shop_more:/data:ro" in argv:
            return result(stderr="no space left on device\n", code=1)
        if argv[:2] == ["docker", "run"]:
            _archive_file(env, argv)
        if "start" in argv:
            starting.set()
            await asyncio.sleep(0.05)
        return await inner(host, argv, **kwargs)

    env.fake.run_argv = run_argv
    task = asyncio.create_task(env.executor.execute(_request(backup=True)))
    await asyncio.wait_for(starting.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(asyncio.gather(*env.executor._cleanups), 5)
    [folder] = _stamp_folders(env)
    assert f"docker_stack_backup_left_after_abort folder={folder} packed=shop_data packing=shop_more" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "kept"), [
    (lambda: type("SFTPNoSuchFile", (Exception,), {})("No such file"), False),
    (lambda: PermissionError(13, "Permission denied"), True),
], ids=["schon-weg", "verweigert"])
async def test_a_folder_that_is_already_gone_counts_as_removed(tmp_path, monkeypatch, caplog, error, kept):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fail["archive"] = "kaputt"

    async def rmdir(path):
        raise error()

    env.docker.fake.sftp_server.rmdir = rmdir
    done = await env.executor.execute(_request(backup=True))
    assert done.error.startswith("Sicherung von shop_data gescheitert: kaputt")
    assert ("ließ sich nicht entfernen" in done.error) is kept and ("docker_stack_backup_folder_not_removed" in caplog.text) is kept


def _hang_in_mkdir(env, where):
    """`mkdir` von `where` antwortet nicht; beim Zeitordner erst, nachdem der Server ihn angelegt hat."""
    sftp, reached = env.docker.fake.sftp_server, asyncio.Event()
    mkdir = sftp.mkdir

    async def mkdir_without_answer(path, attrs=None):
        if path.startswith(where):
            if path.count("/") > 4:
                await mkdir(path, attrs)
            reached.set()
            await asyncio.sleep(3600)
        await mkdir(path, attrs)

    sftp.mkdir = mkdir_without_answer
    return reached


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["/home/deck/nodvard-sicherungen/shop/", "/home/deck/nodvard-sicherungen"], ids=["zeitordner", "oben"])
async def test_an_abort_while_creating_the_folder_leaves_none(tmp_path, monkeypatch, caplog, where):
    env = await _setup(tmp_path, monkeypatch)
    reached = _hang_in_mkdir(env, where)
    task = asyncio.create_task(env.executor.execute(_request(backup=True)))
    await asyncio.wait_for(reached.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _stamp_folders(env) == [] and not any("stop" in c for c in env.docker.calls)
    assert "docker_stack_backup_folder_not_removed" not in caplog.text


@pytest.mark.asyncio
async def test_a_second_abort_while_removing_the_new_folder_still_removes_it(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    reached = _hang_in_mkdir(env, "/home/deck/nodvard-sicherungen/shop/")
    sftp, entered, removed = env.docker.fake.sftp_server, asyncio.Event(), asyncio.Event()
    rmdir = sftp.rmdir

    async def slow_rmdir(path):
        entered.set()
        await asyncio.sleep(0.05)
        await rmdir(path)
        removed.set()

    sftp.rmdir = slow_rmdir
    task = asyncio.create_task(env.executor.execute(_request(backup=True)))
    await asyncio.wait_for(reached.wait(), 5)
    task.cancel()
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(removed.wait(), 5)
    assert _stamp_folders(env) == []


@pytest.mark.asyncio
async def test_an_abort_while_removing_the_folder_after_a_failure_still_removes_it(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fail["archive"] = "kaputt"
    sftp, entered, removed = env.docker.fake.sftp_server, asyncio.Event(), asyncio.Event()
    rmdir = sftp.rmdir

    async def slow_rmdir(path):
        entered.set()
        await asyncio.sleep(0.05)
        await rmdir(path)
        removed.set()

    sftp.rmdir = slow_rmdir
    task = asyncio.create_task(env.executor.execute(_request(backup=True)))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(removed.wait(), 5)
    assert _stamp_folders(env) == []


@pytest.mark.asyncio
async def test_services_start_again_even_when_stopping_the_archive_fails(tmp_path, monkeypatch, caplog):
    env = await _setup(tmp_path, monkeypatch)
    _archive_breaks(env, TimeoutError())
    original = env.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[:3] == ["docker", "rm", "-f"]:
            raise RuntimeError("kaputt")
        return await original(host, argv, **kwargs)

    env.fake.run_argv = run_argv
    done = await env.executor.execute(_request(backup=True))
    assert any("start" in c for c in env.docker.calls) and "docker_stack_archive_not_stopped" in caplog.text
    assert "packt vielleicht noch" in done.error and "docker rm -f nodvard-sicherung-" in done.error


@pytest.mark.asyncio
async def test_a_half_archive_that_cannot_be_removed_is_named(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    _archive_breaks(env, TimeoutError())

    async def refused(path):
        raise PermissionError(path)

    env.docker.fake.sftp_server.remove = refused
    done = await env.executor.execute(_request(backup=True))
    assert "kann die unfertige Datei shop_data.tar.gz.part liegen" in done.error
    assert any("start" in c for c in env.docker.calls)


@pytest.mark.asyncio
async def test_a_second_cancel_does_not_skip_the_restart(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fail["archive_hangs"] = ""
    original = env.fake.run_argv
    slow = asyncio.Event()

    async def run_argv(host, argv, **kwargs):
        if argv[:3] == ["docker", "rm", "-f"]:
            slow.set()
            await asyncio.sleep(0.05)
        return await original(host, argv, **kwargs)

    env.fake.run_argv = run_argv
    task = asyncio.create_task(env.executor.execute(_request(backup=True)))
    while not any(c[:2] == ["docker", "run"] for c in env.docker.calls):
        await asyncio.sleep(0)
    task.cancel()
    await slow.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.1)
    assert any("start" in c for c in env.docker.calls)


@pytest.mark.asyncio
async def test_a_failed_archive_also_stops_its_container(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    env.docker.fail["archive"] = "Error: no space left on device\n"
    await env.executor.execute(_request(backup=True))
    kinds = ["start" if "start" in c else c[1] for c in env.docker.calls if c[:2] in (["docker", "run"], ["docker", "rm"]) or "start" in c]
    assert kinds == ["run", "rm", "start"]


def test_the_check_before_approval_uses_the_chosen_services():
    from nodvard_deck_ext_docker.stack_update import _plan_refusal

    def row(service, volumes, stopped=False, image="busybox:1.37"):
        return {"service": service, "volumes": volumes, "stopped": stopped, "image": image}

    plan = {"services": [row("web", ["shop_data"]), row("worker", ["shop_data"], image="redis:7")], "updates": ["web", "worker"],
            "volume_backup": {"error": None}}
    assert _plan_refusal(plan, ["web", "worker"]) is None
    assert _plan_refusal(plan, ["web"]).startswith("Das Volume shop_data nutzt auch der Dienst „worker“")
    plan["services"][1]["stopped"] = True
    assert _plan_refusal(plan, ["web"]) is None
    plan["volume_backup"]["error"] = "Zu wenig Platz: …"
    assert _plan_refusal(plan, ["web"]) == "Zu wenig Platz: …"
    assert _plan_refusal({**plan, "services": [row("web", [])]}, ["web"]).startswith("Die Dienste mit Update haben keine benannten")


@pytest.mark.asyncio
async def test_a_lost_connection_while_reading_free_space(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    original = env.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[:2] == ["df", "-Pk"]:
            raise pytest.importorskip("asyncssh").ConnectionLost("Connection lost")
        return await original(host, argv, **kwargs)

    env.fake.run_argv = run_argv
    plan = await env.update.plan(env.host, env.stack)
    assert plan["volume_backup"]["error"] == "Der freie Platz ließ sich nicht lesen: die Verbindung zum Server ist abgebrochen."


@pytest.mark.asyncio
async def test_no_archive_starts_once_the_time_is_used_up(tmp_path, monkeypatch):
    env = await _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(env.m.stack_update, "BACKUP_UNTIL_S", 0)
    done = await env.executor.execute(_request(backup=True))
    assert done.error == ("Für die Sicherung reichte die Zeit nicht, shop_data ist nicht mehr dran gekommen. Aktualisiert wurde nichts; "
                          "die Dienste laufen weiter mit der alten Version.")
    assert not any(c[:2] == ["docker", "run"] for c in env.docker.calls) and any("start" in c for c in env.docker.calls)
