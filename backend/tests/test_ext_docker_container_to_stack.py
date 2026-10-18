"""Einzelne Container in einen Stack umwandeln: Übersetzung aus `docker inspect`, Ablehnung mit Grund, Umschalten mit Rückweg."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from docker_stack_fakes import (  # noqa: F401 - Fixtures
    EXT_DIR,
    FIXTURES,
    FakeExec,
    docker_on,
    ps,
    result,
    row,
)


@pytest.fixture(autouse=True)
def _ext_docker_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


SAMPLES = json.loads((FIXTURES / "plain_containers.json").read_text(encoding="utf-8"))
IMAGE = SAMPLES["busybox_image_config"]
DAEMON = {"cgroup_v2": False, "log_driver": "json-file", "runtime": "runc"}
ROOT = "/opt/stacks"
PLAIN_COMPOSE = (
    'services:\n  plaintest:\n    image: "busybox:1.37"\n    container_name: plaintest\n    restart: unless-stopped\n    hostname: meinhost\n'
    '    command:\n    - sleep\n    - "3600"\n    env_file:\n    - container.env\n    labels:\n      meine.marke: "1"\n    ports:\n'
    '    - "127.0.0.1:8443:443"\n    - "8099:80"\n    volumes:\n    - "plaindaten:/data"\n    - "/srv/daten:/host:ro"\n'
    '    network_mode: bridge\nvolumes:\n  plaindaten:\n    external: true\n'
)


def _m():
    from nodvard_deck_ext_docker import (
        adopt,
        after_check,
        container_to_stack,
        editor,
        image_apply,
        new_stack,
        stack_actions,
        stacks,
    )

    return SimpleNamespace(adopt=adopt, after_check=after_check, c2s=container_to_stack, editor=editor, image_apply=image_apply,
                           new_stack=new_stack, stack_actions=stack_actions, stacks=stacks)


def _sample(name="plain", **host):
    data = copy.deepcopy(SAMPLES[name])
    data["HostConfig"].update(host)
    return data


def _convert(data, image=IMAGE, **daemon):
    return _m().c2s.convert(data, image, **{**DAEMON, **daemon})


def test_a_plain_container_becomes_this_file():
    out = _convert(_sample())
    assert (out.project, out.service, out.refused) == ("plaintest", "plaintest", [])
    assert out.compose == PLAIN_COMPOSE
    assert out.env == "FOO='bar'\nTZ='Europe/Berlin'\n" and out.env_names == ["FOO", "TZ"]


def test_more_settings_are_carried_over():
    out = _convert(_sample("plain2"))
    assert out.refused == [] and out.env is None
    service = yaml.safe_load(out.compose)["services"]["plain2"]
    assert service == {
        "image": "busybox:1.37", "container_name": "plain2", "user": "1000:1000", "working_dir": "/tmp", "entrypoint": ["sh"],
        "command": ["-c", "sleep 3600"], "stop_grace_period": "20s", "healthcheck": {"test": ["CMD-SHELL", "true"], "interval": "30s"},
        "tmpfs": ["/run:size=10m"], "networks": {"plainnetz": {"aliases": ["alias2"]}}, "init": True, "cap_add": ["CAP_NET_ADMIN"],
        "extra_hosts": ["meinhost:10.0.0.5"], "dns": ["9.9.9.9"], "mem_limit": 268435456, "cpus": 0.5,
    }
    assert yaml.safe_load(out.compose)["networks"] == {"plainnetz": {"external": True}}
    assert "memswap_limit" not in service, "das Doppelte von --memory setzt Docker selbst"


def test_dollar_signs_and_env_values_stay_literal():
    data = _sample()
    data["Config"]["Cmd"] = ["sh", "-c", 'echo "$FOO" > /x']
    data["Config"]["Labels"] = {"preis": "5$"}
    data["Config"]["Env"] = ["A=$x \"y\"", "B=", *IMAGE["Env"]]
    out = _convert(data)
    service = yaml.safe_load(out.compose)["services"]["plaintest"]
    assert service["command"] == ["sh", "-c", 'echo "$$FOO" > /x'] and service["labels"] == {"preis": "5$$"}
    assert out.env == "A='$x \"y\"'\nB=''\n"


@pytest.mark.parametrize(
    ("env", "why"),
    [
        ("MIT LEER=geheim", "Eine Variable hat einen Namen, den Compose in einer Env-Datei nicht annimmt"),
        ("a/b=geheim", "Eine Variable hat einen Namen, den Compose in einer Env-Datei nicht annimmt"),
        ("A:B=geheim", "Eine Variable hat einen Namen, den Compose in einer Env-Datei nicht annimmt"),
        ("1A=geheim", "Eine Variable hat einen Namen, den Compose in einer Env-Datei nicht annimmt"),
        ("A=it's", "Die Variable A hat einen Wert, den eine Env-Datei nicht sicher fasst"),
        ("A=zwei\nzeilen", "Die Variable A hat einen Wert, den eine Env-Datei nicht sicher fasst"),
        ("A=ende\\", "Die Variable A hat einen Wert, den eine Env-Datei nicht sicher fasst"),
        ("A=ende\\\\", "Die Variable A hat einen Wert, den eine Env-Datei nicht sicher fasst"),
    ],
)
def test_env_files_cannot_hold_everything(env, why):
    data = _sample()
    data["Config"]["Env"] = [env]
    out = _convert(data)
    assert out.refused[0].startswith(why) and "geheim" not in " ".join(out.refused)


def test_allowed_env_names_and_a_backslash_inside():
    data = _sample()
    data["Config"]["Env"] = ["a.b-c_1=x\\y", "_X="]
    out = _convert(data)
    assert out.refused == [] and out.env == "a.b-c_1='x\\y'\n_X=''\n"


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (lambda d: d["Config"]["Labels"].update({"com.docker.compose.project": "x"}), "Der Container gehört schon zu einem Compose-Projekt."),
        (lambda d: d["HostConfig"].update(AutoRemove=True), "Der Container löscht sich beim Stoppen selbst (--rm)."),
        (lambda d: d["HostConfig"].update(Links=["/a:/b/a"]), "Der Container nutzt alte Links (--link)."),
        (lambda d: d["HostConfig"].update(VolumesFrom=["daten"]), "Der Container übernimmt Volumes eines anderen (--volumes-from)."),
        (lambda d: d["HostConfig"].update(PublishAllPorts=True), "Alle Ports werden zufällig veröffentlicht (-P)."),
        (lambda d: d["HostConfig"].update(CpuQuota=50000), "Die Einstellung CpuQuota übersetzt Deck noch nicht."),
        (lambda d: d["HostConfig"].update(IpcMode="container:abc"), "Der Container teilt den Speicherbereich (IpcMode container:abc)."),
        (lambda d: d["HostConfig"].update(PidMode="container:abc"), "Die Einstellung PidMode (container:abc) übersetzt Deck noch nicht."),
        (lambda d: d["HostConfig"].update(NetworkMode="container:abc"), "Der Container teilt das Netz eines anderen Containers"),
        (lambda d: d["NetworkSettings"]["Networks"].update(weiteres={}), "Der Container hängt zusätzlich an weiteren Netzen (weiteres)."),
        (lambda d: d["Config"].update(Image="sha256:" + "a" * 64), "Der Container wurde über die ID seines Images gestartet"),
        (lambda d: d["HostConfig"].update(MaskedPaths=[]), "Der Schutz der Systempfade ist abgeschaltet"),
        (lambda d: d["HostConfig"].update(Mounts=[{"Target": "/data", "VolumeOptions": {"NoCopy": True}}]), "Der Mount /data hat eigene Optionen"),
        (lambda d: d["Mounts"][1].update(Propagation="rshared"), "Der Ordner unter /host wird mit „rshared“ eingebunden."),
        (lambda d: d["Mounts"][0].update(Driver="nfs"), "Das Volume unter /data lässt sich nicht übernehmen"),
        (lambda d: d["HostConfig"]["PortBindings"]["80/tcp"][0].update(HostIp="::1"), "Der Port 80/tcp ist an die IPv6-Adresse ::1 gebunden."),
        (lambda d: d["Config"].update(MacAddress="02:42:ac:11:00:09"), "Der Container hat eine feste MAC-Adresse."),
        (lambda d: d["HostConfig"].update(SecurityOpt=['seccomp={"defaultAction":"SCMP_ACT_ALLOW"}']), "Der Container hat ein eigenes Seccomp-Profil"),
        (lambda d: d["Mounts"][1].update(Source="/srv/mein:ordner"), "Der Ordner unter /host hat einen Doppelpunkt im Pfad."),
        (lambda d: d["NetworkSettings"]["Networks"].update(bridge={"IPAMConfig": {"LinkLocalIPs": ["169.254.1.1"]}}) or
            d["HostConfig"].update(NetworkMode="eigenes") or d["NetworkSettings"]["Networks"].update(eigenes=d["NetworkSettings"]["Networks"].pop("bridge")),
         "Am Netz eigenes hat der Container eigene Link-Local-Adressen."),
    ],
)
def test_what_deck_cannot_carry_over_is_named(change, why):
    data = _sample()
    change(data)
    refused = _convert(data).refused
    assert any(r.startswith(why) for r in refused), refused


@pytest.mark.parametrize(
    ("mode", "rw", "entry"),
    [("z", True, "plaindaten:/data"), ("nocopy", True, "plaindaten:/data:nocopy"), ("ro,nocopy", False, "plaindaten:/data:ro,nocopy"),
     ("ro", False, "plaindaten:/data:ro")],
)
def test_volume_modes_are_kept(mode, rw, entry):
    data = _sample()
    data["Mounts"][0].update(Mode=mode, RW=rw)
    out = _convert(data)
    assert out.refused == [] and yaml.safe_load(out.compose)["services"]["plaintest"]["volumes"][0] == entry


def test_a_volume_with_its_own_label_is_refused():
    data = _sample()
    data["Mounts"][0].update(Mode="Z")
    assert "Das Volume unter /data wird mit „Z“ eingebunden; das übernimmt Deck nicht." in _convert(data).refused


def test_a_port_keeps_its_address_without_a_fixed_port():
    data = _sample(PortBindings={"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}], "53/udp": [{"HostIp": "0.0.0.0", "HostPort": ""}]})
    assert yaml.safe_load(_convert(data).compose)["services"]["plaintest"]["ports"] == ["53/udp", "127.0.0.1::8080"]


def test_texts_that_look_like_numbers_stay_texts():
    data = _sample()
    data["Config"]["Labels"] = {"a": "1e3", "b": "0o17", "c": ".5", "d": "-1", "e": "0_8"}
    service = yaml.safe_load(_convert(data).compose)["services"]["plaintest"]
    assert service["labels"] == {"a": "1e3", "b": "0o17", "c": ".5", "d": "-1", "e": "0_8"}


def test_the_host_name_is_left_out_with_uts_host():
    service = yaml.safe_load(_convert(_sample(UTSMode="host")).compose)["services"]["plaintest"]
    assert service["uts"] == "host" and "hostname" not in service


def test_daemon_defaults_are_not_written():
    data = _sample(LogConfig={"Type": "local", "Config": {}}, ShmSize=128 * 1024 * 1024, CgroupnsMode="private", Runtime="crun",
                   MemorySwap=1024)
    service = yaml.safe_load(_convert(data).compose)["services"]["plaintest"]
    assert (service["logging"], service["shm_size"], service["cgroup"], service["runtime"], service["memswap_limit"]) == (
        {"driver": "local"}, 128 * 1024 * 1024, "private", "crun", 1024)
    plain = yaml.safe_load(_convert(_sample(CgroupnsMode="private"), cgroup_v2=True).compose)["services"]["plaintest"]
    assert not {"logging", "shm_size", "cgroup", "runtime", "memswap_limit"} & set(plain)


def test_names_for_stacks():
    c2s = _m().c2s
    assert c2s.stack_name("Mein_Container.1") == "mein_container-1"
    assert c2s.stack_name("---") == "stack" and len(c2s.stack_name("a" * 200)) == 63


def test_commands():
    c2s = _m().c2s
    cid = "a" * 64
    assert c2s.cmd_stop_old(cid, 20) == ["docker", "stop", "-t", "20", cid]
    assert c2s.cmd_rename_old(cid, "web-vor-stack") == ["docker", "rename", cid, "web-vor-stack"]
    assert c2s.cmd_remove_old(cid) == ["docker", "rm", cid] and c2s.cmd_start_old(cid) == ["docker", "start", cid]
    for bad in (lambda: c2s.cmd_stop_old("web", 1), lambda: c2s.cmd_rename_old(cid, "a b"), lambda: c2s.cmd_remove_old("web"),
                lambda: c2s.cmd_inspect("x;y"), lambda: c2s.cmd_image_config("$(id)")):
        with pytest.raises(ValueError):
            bad()


# --- Ausführer -------------------------------------------------------------------------------------------------------


async def _async(value):
    return value


class _Settings:
    async def get(self):
        return {}


def _setup(monkeypatch, tmp_path, *, verdict=True, fail=None):
    m = _m()
    fake = FakeExec()
    fake.rows = []
    fake.config = result(json.dumps({"name": "plaintest", "services": {"plaintest": {
        "image": "busybox:1.37", "environment": {"FOO": "bar", "TZ": "Europe/Berlin"}}}}))
    data = _sample()
    data["Id"] = "c" * 64
    data["State"] = {"Running": True}
    calls: list[list[str]] = []
    failing = dict(fail or {})
    original = fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[:3] == ["docker", "container", "inspect"]:
            return result(json.dumps([data]))
        if argv[:3] == ["docker", "image", "inspect"]:
            return result(json.dumps({"Id": data["Image"], "Config": IMAGE}))
        if argv[:2] == ["docker", "info"]:
            return result("1 json-file runc\n")
        if argv[:2] == ["docker"] + [argv[1]] and argv[1] in ("stop", "rename", "start", "rm"):
            calls.append(list(argv))
            if argv[1] in failing:
                return result(stderr=failing.pop(argv[1]), code=1)
            return result()
        answer = await original(host, argv, **kwargs)
        if argv[-2:] == ["up", "-d"]:
            calls.append(["up"])
            fake.rows = [row(project="plaintest", name="plaintest", service="plaintest", working_dir=f"{ROOT}/plaintest",
                             config_files=f"{ROOT}/plaintest/compose.yaml")]
        if argv[-1:] == ["down"]:
            calls.append(["down"])
            fake.rows = []
        return answer

    fake.run_argv = run_argv
    host = SimpleNamespace(id="h1", tags=["docker"])
    ctx = SimpleNamespace(exec=fake, hosts=SimpleNamespace(get=lambda host_id: _async(host if host_id == "h1" else None)), settings=_Settings())
    store = m.adopt.AdoptedStacks(Path(tmp_path))
    st = m.stacks.DockerStacks(ctx, store)
    actions = m.stack_actions.DockerStackExecutor(ctx, st, store)
    editor = m.editor.StackEditor(ctx, actions, Path(tmp_path))
    new = m.new_stack.NewStacks(ctx, editor, st, store, asyncio.Lock())
    create = m.new_stack.DockerStackCreateExecutor(ctx, new, store, actions)
    conv = m.c2s.ContainerToStack(ctx, new, store, st)

    async def check_running(ctx, host, refs, **_):
        return m.image_apply.Verdict(ok=verdict, text="Container läuft stabil." if verdict else "Container startet immer wieder neu."), None

    monkeypatch.setattr(m.after_check, "check_running", check_running)
    return SimpleNamespace(m=m, fake=fake, data=data, calls=calls, host=host, store=store, conv=conv, actions=actions, sftp=fake.sftp_server,
                           executor=m.c2s.DockerContainerToStackExecutor(ctx, conv, create, store, actions))


@pytest.fixture
def env(monkeypatch, tmp_path):
    return lambda **kw: _setup(monkeypatch, tmp_path, **kw)


def _request(plan, *, risk="high", actor="user", container="plaintest"):
    from nodvard_sdk.actions import ActionRequest
    from nodvard_sdk.types import Actor, Risk

    return ActionRequest(action_type="docker.container_to_stack", host_ref="h1", risk=Risk(risk), proposed_by=Actor(type=actor, id="u1"),
                         reason="Test", payload={"container": container, "id": plan["id"], "folder": plan["folder"],
                                                 "compose_sha256": hashlib.sha256((plan["compose"] or "").encode()).hexdigest()})


@pytest.mark.asyncio
async def test_the_plan_shows_no_values(env):
    e = env()
    plan = await e.conv.plan(e.host, "plaintest")
    assert (plan["project"], plan["folder"], plan["refused"], plan["warnings"]) == ("plaintest", f"{ROOT}/plaintest", [], [])
    assert plan["env_names"] == ["FOO", "TZ"] and "bar" not in json.dumps(plan) and plan["compose"] == PLAIN_COMPOSE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "why"),
    [
        (lambda d: d["Config"].update(Image="ghcr.io/nodvard/deck:0.9.0"), "Das ist Nodvard Deck selbst."),
        (lambda d: d["Config"].update(Image="ghcr.io/nodvard/deck-updater:1"), "Das ist Nodvard Deck selbst."),
        (lambda d: d["Config"]["Labels"].update({"org.opencontainers.image.title": "Nodvard Deck Update-Helfer"}),
         "Das ist der Update-Helfer von Nodvard Deck."),
    ],
)
async def test_deck_and_its_helper_are_never_converted(env, change, why):
    e = env()
    change(e.data)
    assert (await e.conv.plan(e.host, "plaintest"))["refused"][0] == why


@pytest.mark.asyncio
async def test_deck_itself_by_its_container_id(env, monkeypatch):
    e = env()
    monkeypatch.setattr(e.m.c2s, "is_own_container", lambda cid: cid == "c" * 64)
    assert (await e.conv.plan(e.host, "plaintest"))["refused"][0] == "Das ist Nodvard Deck selbst."


@pytest.mark.asyncio
async def test_an_image_that_moved_on_is_refused(env):
    e = env()
    original = e.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[:3] == ["docker", "image", "inspect"]:
            return result(json.dumps({"Id": "sha256:" + "9" * 64, "Config": IMAGE}))
        return await original(host, argv, **kwargs)

    e.fake.run_argv = run_argv
    refused = (await e.conv.plan(e.host, "plaintest"))["refused"]
    assert refused == [("Der Name busybox:1.37 zeigt inzwischen auf ein anderes Image als das, mit dem der Container läuft. "
                        "Wandle ihn um, wenn er mit dem aktuellen Image läuft.")]


@pytest.mark.asyncio
async def test_ids_and_long_names_are_refused(env):
    e = env()
    by_id = await e.conv.plan(e.host, "c" * 12)
    assert "Gib den Namen des Containers an, nicht seine ID." in by_id["refused"]
    e.data["Name"] = "/" + "x" * 250
    long = await e.conv.plan(e.host, "x" * 250)
    assert "Der Name des Containers ist zu lang, um ihn für den Rückweg umzubenennen." in long["refused"]


@pytest.mark.asyncio
async def test_folders_of_the_server_are_warned_about(env):
    e = env()
    e.data["Mounts"] += [
        {"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock", "RW": True},
        {"Type": "bind", "Source": "/etc", "Destination": "/host-etc", "RW": True},
    ]
    warnings = " ".join((await e.conv.plan(e.host, "plaintest"))["warnings"])
    assert "docker.sock" in warnings and "bindet /etc vom Server ein" in warnings


@pytest.mark.asyncio
async def test_env_values_compose_reads_differently_stop_everything(env):
    e = env()
    e.fake.config = result(json.dumps({"name": "plaintest", "services": {"plaintest": {"environment": {"FOO": "ba", "TZ": "Europe/Berlin"}}}}))
    done = await e.executor.execute(_request(await e.conv.plan(e.host, "plaintest")))
    assert done.error.startswith(e.m.c2s.ENV_CHANGED) and "bar" not in done.error and e.calls == []
    assert f"{ROOT}/plaintest" not in e.sftp.dirs


@pytest.mark.asyncio
@pytest.mark.parametrize(("stderr", "shown"), [
    ('unexpected character "/" in variable name "a/b=\'bar\'"\n', "Den Inhalt zeigt Nodvard Deck nicht"),
    ("invalid hostPort: bar\n", "Compose meldet einen Fehler, der Werte aus container.env enthält"),
])
async def test_a_compose_error_with_a_value_is_not_shown(env, stderr, shown):
    e = env()
    e.fake.config = result(stderr=stderr, code=1)
    done = await e.executor.execute(_request(await e.conv.plan(e.host, "plaintest")))
    assert "bar" not in done.error and shown in done.error and e.calls == []


@pytest.mark.asyncio
async def test_only_on_docker_hosts_and_only_the_planned_file(env):
    e = env()
    plan = await e.conv.plan(e.host, "plaintest")
    e.host.tags = []
    assert (await e.executor.execute(_request(plan))).error == "Kein Docker-Host."
    e.host.tags = ["docker"]
    other = {**plan, "compose": plan["compose"] + "# anders\n"}
    assert (await e.executor.execute(_request(other))).error.startswith("Der Container oder der Ordner hat sich inzwischen geändert.")
    assert e.calls == []


@pytest.mark.asyncio
async def test_the_approved_file_holds_for_the_second_read(env):
    e = env()
    plan = await e.conv.plan(e.host, "plaintest")
    original, reads = e.conv.facts, []

    async def facts(host, container):
        reads.append(container)
        if len(reads) == 2:
            e.data["Config"]["Cmd"] = ["sh", "-c", "anders"]
        return await original(host, container)

    e.conv.facts = facts
    done = await e.executor.execute(_request(plan))
    assert done.error == "Der Container oder der Ordner hat sich inzwischen geändert. Bitte noch einmal umwandeln." and e.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("gap", [" ", ""], ids=["eigenes-wort", "angehaengt"])
async def test_a_value_cut_off_in_the_reason_never_shows(env, gap):
    e = env()
    plan = await e.conv.plan(e.host, "plaintest")
    e.fake.verb = result(stderr=f"Error response from daemon: {'x' * 265}{gap}Europe/Berlin\n", code=1)
    done = await e.executor.execute(_request(plan))
    assert done.success is False and "Europe" not in done.error, done.error


@pytest.mark.asyncio
async def test_a_value_only_in_the_progress_does_not_hide_the_reason(env):
    e = env()
    plan = await e.conv.plan(e.host, "plaintest")
    e.fake.verb = result(stderr="Network bar  Created\nError response from daemon: port is already allocated\n", code=1)
    done = await e.executor.execute(_request(plan))
    assert "port is already allocated" in done.error and "bar " not in done.error, done.error


@pytest.mark.parametrize(
    ("text", "value", "shown"),
    [
        ("lesbar", "bar", False),
        ("Wert bar ist falsch", "bar", True),
        ("FOO=bar", "bar", True),
        ("xxEurope/Berlinxx", "Europe/Berlin", True),
        ("abc Europe/Be", "Europe/Berlin", True),
        ("abcEurope", "Europe/Berlin", False),
    ],
)
def test_when_a_value_counts_as_shown(text, value, shown):
    assert _m().c2s._shows(text, value) is shown


def test_a_long_value_is_checked_quickly():
    import time

    begin = time.monotonic()
    assert _m().c2s._shows("Error: " + "x" * 300, "y" * 50_000) is False
    assert time.monotonic() - begin < 2


def test_the_question_says_the_old_container_goes():
    spec = _m().c2s.convert_action_spec()
    assert "löscht Deck den alten Container; was nur in ihm lag und nicht in einem Volume oder Ordner, ist dann weg." in spec.confirm_text


@pytest.mark.asyncio
async def test_an_env_file_others_could_read_is_refused(env, monkeypatch):
    e = env()
    real_stat = e.sftp.stat

    async def stat(path):
        attrs = await real_stat(path)
        if path.endswith("container.env"):
            attrs.permissions = 0o100644
        return attrs

    monkeypatch.setattr(e.sftp, "stat", stat)
    done = await e.executor.execute(_request(await e.conv.plan(e.host, "plaintest")))
    assert done.error.startswith("container.env wäre für andere auf dem Server lesbar") and e.calls == []


@pytest.mark.asyncio
async def test_a_way_back_without_down_keeps_the_folder(env):
    e = env(verdict=False)
    original = e.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[-1:] == ["down"]:
            e.calls.append(["down"])
            return result(stderr="Error: daemon busy\n", code=1)
        return await original(host, argv, **kwargs)

    e.fake.run_argv = run_argv
    done = await e.executor.execute(_request(await e.conv.plan(e.host, "plaintest")))
    assert "Der Ordner /opt/stacks/plaintest bleibt, bis der neue Stack sicher herunter ist." in done.error
    assert f"{ROOT}/plaintest/compose.yaml" in e.sftp.files


def _lost():
    return pytest.importorskip("asyncssh").ConnectionLost("Connection lost")


@pytest.mark.asyncio
@pytest.mark.parametrize("at", ["stop", "up"])
async def test_a_lost_connection_brings_the_old_container_back(env, at):
    e = env()
    original = e.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if (at == "stop" and argv[1:2] == ["stop"]) or (at == "up" and argv[-2:] == ["up", "-d"]):
            e.calls.append([at, "abgebrochen"])
            raise _lost()
        return await original(host, argv, **kwargs)

    e.fake.run_argv = run_argv
    done = await e.executor.execute(_request(await e.conv.plan(e.host, "plaintest")))
    assert done.success is False and "die Verbindung zum Server ist abgebrochen. " in done.error and "Connection lost" not in done.error
    cid = "c" * 64
    assert e.calls[-1] == ["docker", "start", cid] and e.store.for_host("h1") == {}
    assert f"{ROOT}/plaintest" not in e.sftp.dirs


@pytest.mark.asyncio
async def test_a_lost_connection_while_removing_the_old_container_still_adopts(env):
    e = env()
    original = e.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[1:2] == ["rm"]:
            raise _lost()
        return await original(host, argv, **kwargs)

    e.fake.run_argv = run_argv
    done = await e.executor.execute(_request(await e.conv.plan(e.host, "plaintest")))
    assert done.success is True and "plaintest" in e.store.for_host("h1")
    assert "„plaintest-vor-stack“ ließ sich nicht löschen (die Verbindung zum Server ist abgebrochen)" in done.output


@pytest.mark.asyncio
async def test_a_lost_connection_while_reading_is_a_reason(env):
    e = env()
    original = e.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[:3] == ["docker", "container", "inspect"]:
            raise _lost()
        return await original(host, argv, **kwargs)

    e.fake.run_argv = run_argv
    plan = await e.conv.plan(e.host, "plaintest")
    assert any("die Verbindung zum Server ist abgebrochen" in r for r in plan["refused"])
    done = await e.executor.execute(_request({"id": "c" * 64, "folder": f"{ROOT}/plaintest", "compose": None}))
    assert done.error.endswith("die Verbindung zum Server ist abgebrochen. Geändert wurde nichts.")


@pytest.mark.asyncio
@pytest.mark.parametrize("at", ["stop", "up"])
async def test_a_cancel_brings_the_old_container_back(env, at):
    e = env()
    original = e.fake.run_argv
    hit = asyncio.Event()

    async def run_argv(host, argv, **kwargs):
        if (at == "stop" and argv[1:2] == ["stop"]) or (at == "up" and argv[-2:] == ["up", "-d"]):
            e.calls.append([at, "hängt"])
            hit.set()
            await asyncio.sleep(3600)
        return await original(host, argv, **kwargs)

    e.fake.run_argv = run_argv
    task = asyncio.create_task(e.executor.execute(_request(await e.conv.plan(e.host, "plaintest"))))
    await hit.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cid = "c" * 64
    tail = [["down"], ["docker", "rename", cid, "plaintest"], ["docker", "start", cid]]
    assert e.calls[-3:] == tail if at == "up" else e.calls[-2:] == tail[1:]
    assert f"{ROOT}/plaintest" not in e.sftp.dirs and e.store.for_host("h1") == {}


@pytest.mark.asyncio
async def test_the_switch(env):
    e = env()
    done = await e.executor.execute(_request(await e.conv.plan(e.host, "plaintest")))
    assert done.success is True, done.error
    assert done.output == f"„plaintest“ läuft jetzt als Stack „plaintest“ aus {ROOT}/plaintest. Container läuft stabil."
    cid = "c" * 64
    assert e.calls == [["docker", "stop", "-t", "10", cid], ["docker", "rename", cid, "plaintest-vor-stack"], ["up"], ["docker", "rm", cid]]
    assert e.sftp.files[f"{ROOT}/plaintest/compose.yaml"] == PLAIN_COMPOSE.encode()
    assert e.sftp.files[f"{ROOT}/plaintest/container.env"] == b"FOO='bar'\nTZ='Europe/Berlin'\n"
    assert e.sftp.modes[f"{ROOT}/plaintest/container.env"] & 0o777 == 0o600 and "plaintest" in e.store.for_host("h1")


@pytest.mark.asyncio
async def test_a_stack_that_does_not_run_brings_the_old_container_back(env):
    e = env(verdict=False)
    done = await e.executor.execute(_request(await e.conv.plan(e.host, "plaintest")))
    cid = "c" * 64
    assert done.success is False
    assert done.error == "Der Stack ist nicht sauber angelaufen: Container startet immer wieder neu. Der alte Container läuft wieder wie vorher."
    assert e.calls[2:] == [["up"], ["down"], ["docker", "rename", cid, "plaintest"], ["docker", "start", cid]]
    assert not [p for p in e.sftp.files if p.startswith(f"{ROOT}/plaintest")] and f"{ROOT}/plaintest" not in e.sftp.dirs
    assert e.store.for_host("h1") == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail", "error", "tail"),
    [
        ({"stop": "Error: cannot stop\n"}, "Der Container ließ sich nicht stoppen: Error: cannot stop. Er läuft weiter wie bisher.", ["start"]),
        ({"rename": "Error: name in use\n"}, "Der Container ließ sich nicht umbenennen: Error: name in use. Er läuft wieder wie bisher.", ["start"]),
    ],
)
async def test_early_failures_change_nothing(env, fail, error, tail):
    e = env(fail=fail)
    done = await e.executor.execute(_request(await e.conv.plan(e.host, "plaintest")))
    assert done.error == error and [c[1] for c in e.calls][-1:] == tail and ["up"] not in e.calls
    assert f"{ROOT}/plaintest" not in e.sftp.dirs


@pytest.mark.asyncio
async def test_a_start_that_fails_after_an_early_failure_is_said(env):
    e = env(fail={"stop": "Error: cannot stop\n", "start": "Error: no such container\n"})
    done = await e.executor.execute(_request(await e.conv.plan(e.host, "plaintest")))
    assert done.error == ("Der Container ließ sich nicht stoppen: Error: cannot stop. Ob er weiter wie bisher läuft, ließ sich nicht "
                          "feststellen (Error: no such container). Sieh im Reiter Container nach.")


@pytest.mark.asyncio
@pytest.mark.parametrize("renamed", [True, False])
async def test_a_rename_without_answer_gets_the_name_back(env, renamed):
    e = env(fail=None if renamed else {"rename": "Error response from daemon: Renaming a container with the same name as its current name\n"})
    original = e.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[1:2] == ["rename"] and argv[-1].endswith("-vor-stack"):
            raise _lost()
        return await original(host, argv, **kwargs)

    e.fake.run_argv = run_argv
    done = await e.executor.execute(_request(await e.conv.plan(e.host, "plaintest")))
    cid = "c" * 64
    assert done.error == "Der Container ließ sich nicht umbenennen: die Verbindung zum Server ist abgebrochen. Er läuft wieder wie bisher."
    assert e.calls[-2:] == [["docker", "rename", cid, "plaintest"], ["docker", "start", cid]] and ["up"] not in e.calls


@pytest.mark.asyncio
async def test_a_partial_way_back_says_so(env):
    e = env(verdict=False, fail={"start": "Error: no such container\n"})
    done = await e.executor.execute(_request(await e.conv.plan(e.host, "plaintest")))
    assert "Zurück auf den alten Container ging nicht ganz (Error: no such container)." in done.error
    assert "plaintest-vor-stack" in done.error


@pytest.mark.asyncio
async def test_a_leftover_old_container_is_named(env):
    e = env(fail={"rm": "Error: removal in progress\n"})
    done = await e.executor.execute(_request(await e.conv.plan(e.host, "plaintest")))
    assert done.success is True and done.output.endswith(
        "Der alte, gestoppte Container „plaintest-vor-stack“ ließ sich nicht löschen (Error: removal in progress); lösche ihn im Reiter Container.")


@pytest.mark.asyncio
async def test_refusals_and_changes_stop_everything(env):
    e = env()
    plan = await e.conv.plan(e.host, "plaintest")
    e.data["HostConfig"]["AutoRemove"] = True
    refused = await e.executor.execute(_request(plan))
    assert refused.error == "Der Container löscht sich beim Stoppen selbst (--rm). Geändert wurde nichts."
    e.data["HostConfig"]["AutoRemove"] = False
    e.data["Config"]["Labels"]["neu"] = "x"
    changed = await e.executor.execute(_request(plan))
    assert changed.error == "Der Container oder der Ordner hat sich inzwischen geändert. Bitte noch einmal umwandeln."
    e.data["State"]["Running"] = False
    stopped = await e.conv.plan(e.host, "plaintest")
    assert stopped["refused"] == ["Der Container läuft nicht. Starte ihn zuerst, damit sich danach prüfen lässt, ob er wieder läuft."]
    assert e.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"actor": "ai"}, "Einen Container wandelt nur ein Mensch über die Docker-Seite um."),
        ({"risk": "medium"}, "Das Umwandeln braucht eine Freigabe mit hohem Risiko."),
        ({"container": "a b"}, "Ungültiger Container: 'a b'"),
    ],
)
async def test_who_may_convert(env, change, error):
    e = env()
    plan = await e.conv.plan(e.host, "plaintest")
    assert (await e.executor.execute(_request(plan, **change))).error == error and e.calls == []


@pytest.mark.asyncio
async def test_one_change_at_a_time(env):
    e = env()
    plan = await e.conv.plan(e.host, "plaintest")
    with e.actions.locks.hold("h1", "plaintest", "Anlegen"):
        busy = await e.executor.execute(_request(plan))
    assert busy.error == "An diesem Stack läuft gerade schon etwas (Anlegen). Versuch es danach noch einmal." and e.calls == []


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_converting_through_the_gate(client, docker_on, monkeypatch):
    data = _sample()
    data["Id"], data["State"] = "c" * 64, {"Running": True}
    data["Config"]["Cmd"] = ["serve", "--token", "geheim-im-befehl"]
    original = docker_on.exec.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[:3] == ["docker", "container", "inspect"]:
            return result(json.dumps([data]))
        if argv[:3] == ["docker", "image", "inspect"]:
            return result(json.dumps({"Id": data["Image"], "Config": IMAGE}))
        if argv[:2] == ["docker", "info"]:
            return result("1 json-file runc\n")
        return await original(host, argv, **kwargs)

    monkeypatch.setattr(docker_on.handle, "run_argv", run_argv, raising=False)
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/containers/plaintest/to-stack"
    plan = await client.get(url, headers=docker_on.owner)
    assert plan.status_code == 200 and plan.json()["refused"] == [] and plan.json()["env_names"] == ["FOO", "TZ"]
    proposed = await client.post(url, headers=docker_on.owner)
    assert proposed.status_code == 202 and proposed.json()["risk"] == "high"
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert "Container „plaintest“ in den Stack „plaintest“ umwandeln" in action["reason"] and "bar" not in json.dumps(action)
    assert "geheim-im-befehl" not in json.dumps(action) and "compose" not in action["payload"]
    assert action["payload"]["compose_sha256"] == hashlib.sha256(plan.json()["compose"].encode()).hexdigest()
    data["HostConfig"]["Links"] = ["/a:/b"]
    refused = await client.post(url, headers=docker_on.owner)
    assert refused.status_code == 409 and refused.json()["detail"] == "Der Container nutzt alte Links (--link)."
    assert (await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/containers/a;b/to-stack", headers=docker_on.owner)).status_code == 400


@pytest.mark.asyncio
async def test_not_offered_on_the_server_page_and_only_with_hosts_execute(client, docker_on):
    refused = await client.post(f"/api/v1/hosts/{docker_on.host}/actions/docker.container_to_stack",
                                json={"payload": {"container": "plaintest"}, "reason": "Test"}, headers=docker_on.owner)
    assert refused.status_code == 404
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/containers/plaintest/to-stack"
    for method in ("get", "post"):
        assert (await getattr(client, method)(url)).status_code == 401
        assert (await getattr(client, method)(url, headers=docker_on.viewer)).status_code == 403


def test_the_env_check_knows_how_compose_writes_dollars():
    c2s = _m().c2s
    out = c2s.Converted(project="p", service="s", env="A='x$y'\n", env_values={"A": "x$y"})

    def seen(environment):
        return c2s._env_vet({"services": {"s": {"environment": environment}}}, out)

    assert seen({"A": "x$$y"}) is None and seen({"A": "x$y"}) is None and seen(["A=x$$y"]) is None
    assert seen({"A": "x"}) == seen({"A": "x$y", "B": "1"}) == seen(None) == c2s.ENV_CHANGED
