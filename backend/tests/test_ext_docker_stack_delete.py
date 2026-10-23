"""Stack löschen: herunterfahren ohne Volumes, vergessen und den Ordner nur auf Wunsch und nur, wenn nichts dagegen spricht."""

from __future__ import annotations

import asyncio
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


FOLDER = "/opt/stacks/shop"
RM = ["rm", "-rf", "--one-file-system", "--", FOLDER]
PROBE = ["rm", "--one-file-system", "--help"]
MOUNTINFO = (
    "22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n"
    "30 22 0:25 / /proc rw,nosuid - proc proc rw\n"
    "41 22 8:17 / /mnt/daten rw,relatime - ext4 /dev/sdb1 rw\n"
)
PASSWD = (
    "root:x:0:0:root:/root:/bin/bash\n"
    "pi:x:1000:1000:,,,:/home/pi:/bin/bash\n"
    "deck:x:1001:1001::/home/deck:/bin/bash\n"
    "nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n"
)
READING = (["docker", "ps", "-aq", "--no-trunc"], PROBE)


def _mods():
    from nodvard_deck_ext_docker import adopt, editor, new_stack, stack_actions, stack_delete, stacks

    return SimpleNamespace(adopt=adopt, editor=editor, new_stack=new_stack, stack_actions=stack_actions, stack_delete=stack_delete, stacks=stacks)


async def _async(value):
    return value


class _Settings:
    def __init__(self, data=None) -> None:
        self.data = data or {}

    async def get(self):
        return dict(self.data)

    async def set(self, value):
        self.data = dict(value)


def _stack(**changes):
    return _mods().stacks.parse_members(ps(row(**changes)))[0]


def _server_files(sftp, mountinfo=MOUNTINFO, passwd=PASSWD):
    sftp.files["/proc/self/mountinfo"] = mountinfo.encode()
    sftp.files["/etc/passwd"] = passwd.encode()


def _container(name, project="", sources=()):
    return {"id": f"{abs(hash(name)):064x}"[-64:], "name": f"/{name}", "project": project,
            "mounts": [{"Type": "bind", "Source": s, "Destination": "/x"} for s in sources]}


async def _setup(tmp_path, *, adopt=True):
    m = _mods()
    fake = FakeExec()
    hosts = SimpleNamespace(get=lambda host_id: _async(SimpleNamespace(id=host_id, tags=["docker"]) if host_id == "h1" else None))
    ctx = SimpleNamespace(exec=fake, hosts=hosts, settings=_Settings())
    store = m.adopt.AdoptedStacks(tmp_path)
    st = m.stacks.DockerStacks(ctx, store)
    actions = m.stack_actions.DockerStackExecutor(ctx, st, store)
    editor = m.editor.StackEditor(ctx, actions, tmp_path)
    new = m.new_stack.NewStacks(ctx, editor, st, store, asyncio.Lock())
    delete = m.stack_delete.StackDelete(ctx, st, store, new, actions)
    if adopt:
        await store.add("h1", _stack())
    sftp = fake.sftp_server
    _server_files(sftp)
    sftp.files[f"{FOLDER}/daten/notiz.txt"] = b"x"
    sftp.dirs.add(f"{FOLDER}/daten")
    fake.containers = [_container("shop-web-1", "shop", [f"{FOLDER}/daten"]), _container("andere", sources=["/srv/andere"])]
    env = SimpleNamespace(fake=fake, sftp=sftp, store=store, delete=delete, mod=m.stack_delete, ctx=ctx, rm=None,
                          executor=m.stack_delete.DockerStackDeleteExecutor(ctx, delete, store))
    original = fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[0] == "rm" and argv != PROBE and env.rm is not None:
            fake.argv_calls.append(list(argv))
            return env.rm
        return await original(host, argv, **kwargs)

    fake.run_argv = run_argv
    return env


def _changing(env):
    return _changing_calls(env.fake.argv_calls)


def _changing_calls(calls):
    return [call for call in calls if call not in READING and call[:3] != ["docker", "container", "inspect"]]


def _take_down():
    return _mods().stack_delete.cmd_take_down(_stack())


def _request(*, project="shop", remove_folder=False, folder=FOLDER, risk="high", actor="user", host="h1"):
    from nodvard_sdk.actions import ActionRequest
    from nodvard_sdk.types import Actor, Risk

    return ActionRequest(
        action_type="docker.stack_delete", host_ref=host, risk=Risk(risk), proposed_by=Actor(type=actor, id="u1"), reason="Test",
        payload={"project": project, "remove_folder": remove_folder, "folder": folder},
    )


def test_commands():
    m = _mods().stack_delete
    assert m.cmd_remove_folder(FOLDER) == RM
    assert m.cmd_remove_folder("/home/pi/stacks/app") == ["rm", "-rf", "--one-file-system", "--", "/home/pi/stacks/app"]
    for bad in ("/", "/opt", "/home", "/srv", "/var", "/etc/app", "/dev/dri/app", "/var/lib/docker/x", "/tmp/app", "/opt/stacks/../etc",
                "/opt/stacks/", "/opt/$x", "relativ"):
        with pytest.raises(ValueError):
            m.cmd_remove_folder(bad)
    assert _take_down()[-4:] == ["--profile", "*", "down", "--remove-orphans"] and "-v" not in _take_down()
    for bad in ([], ["abc"], ["A" * 64], ["0" * 64 + "; id"], ["0" * 64] * 2001):
        with pytest.raises(ValueError):
            m.cmd_container_mounts(bad)


def test_reading_mounts_and_homes():
    m = _mods().stack_delete
    assert m.mount_points(MOUNTINFO + "50 22 0:40 / /mnt/mit\\040leer rw - nfs srv:/x rw\nkaputt\n") == ["/", "/proc", "/mnt/daten", "/mnt/mit leer"]
    assert m.home_dirs(PASSWD + "kaputt\nleer:x:1:1::/:/bin/sh\nrel:x:2:2::home/rel:/bin/sh\nzwei:x:3:3::/home/pi/:/bin/sh\n") == {
        "/root": "root", "/home/pi": "pi", "/home/deck": "deck", "/nonexistent": "nobody"}


@pytest.mark.asyncio
async def test_delete_keeps_the_folder_by_default(tmp_path):
    env = await _setup(tmp_path)
    done = await env.executor.execute(_request())
    assert done.success is True, done.error
    assert done.output == ("Stack „shop“ heruntergefahren und gelöscht. Der Ordner /opt/stacks/shop mit der Compose-Datei bleibt. "
                           "Die Volumes bleiben; unbenutzte löschst du im Reiter Volumes.")
    assert env.fake.argv_calls == [_take_down()]
    assert env.store.for_host("h1") == {} and FOLDER in env.sftp.dirs


@pytest.mark.asyncio
async def test_delete_with_the_folder(tmp_path):
    env = await _setup(tmp_path)
    done = await env.executor.execute(_request(remove_folder=True))
    assert done.success is True, done.error
    assert done.output == ("Stack „shop“ heruntergefahren und gelöscht. Der Ordner /opt/stacks/shop ist gelöscht. Die Volumes bleiben; "
                           "unbenutzte löschst du im Reiter Volumes.")
    assert _changing(env) == [_take_down(), RM]
    assert env.fake.argv_calls.count(PROBE) == 2, "vor dem Herunterfahren und noch einmal direkt vor rm"
    assert FOLDER not in env.sftp.dirs and env.store.for_host("h1") == {}
    assert "/opt/stacks" in env.sftp.dirs


@pytest.mark.asyncio
async def test_a_stack_without_containers_is_not_taken_down_again(tmp_path):
    env = await _setup(tmp_path)
    env.fake.rows = [r for r in env.fake.rows if r["project"] != "shop"]
    done = await env.executor.execute(_request(remove_folder=True))
    assert done.success is True, done.error
    assert done.output.startswith("Stack „shop“ gelöscht. Der Ordner /opt/stacks/shop ist gelöscht.")
    assert _changing(env) == [RM]


@pytest.mark.asyncio
async def test_containers_left_after_down_stop_everything(tmp_path):
    env = await _setup(tmp_path)
    env.fake.verb = result(stderr="Container shop-web-1  Removed\n")
    rows = list(env.fake.rows)
    original = env.fake.run_argv

    async def stubborn(host, argv, **kwargs):
        answer = await original(host, argv, **kwargs)
        env.fake.rows = rows
        return answer

    env.fake.run_argv = stubborn
    done = await env.executor.execute(_request(remove_folder=True))
    assert done.success is False
    assert done.error.startswith("Nach dem Herunterfahren sind noch Container des Stacks da: shop-web-1")
    assert done.error.endswith("Nodvard Deck hat den Stack nicht vergessen und den Ordner nicht gelöscht.")
    assert _changing(env) == [_take_down()] and "shop" in env.store.for_host("h1") and FOLDER in env.sftp.dirs


@pytest.mark.asyncio
async def test_unreadable_containers_stop_everything(tmp_path):
    env = await _setup(tmp_path)
    original = env.delete.stacks.stacks
    calls = []

    async def once(host, project=None):
        calls.append(project)
        if len(calls) > 1:
            raise _mods().stacks.StackProblem("Cannot connect to the Docker daemon")
        return await original(host, project)

    env.delete.stacks.stacks = once
    done = await env.executor.execute(_request())
    assert done.error == ("Die Container des Stacks ließen sich nicht lesen. Cannot connect to the Docker daemon Nodvard Deck hat den "
                          "Stack nicht vergessen.")
    assert env.fake.argv_calls == [] and "shop" in env.store.for_host("h1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "root"),
    [
        ("rm: cannot remove '/opt/stacks/shop/daten/db': Permission denied\n", True),
        ("rm: cannot remove '/opt/stacks/shop/daten': Device or resource busy\n", False),
    ],
)
async def test_a_partly_deleted_folder_keeps_the_stack(tmp_path, stderr, root):
    env = await _setup(tmp_path)
    env.rm = result(stderr=stderr, code=1)
    done = await env.executor.execute(_request(remove_folder=True))
    assert done.success is False and done.output == "Stack „shop“ heruntergefahren."
    hint = (" Übrige Dateien gehören meist root, weil ein Container sie angelegt hat; lösche den Rest im Terminal mit „sudo rm -rf "
            "--one-file-system -- /opt/stacks/shop“." if root else "")
    assert done.error == (f"Der Ordner /opt/stacks/shop ließ sich nicht ganz löschen: {stderr.strip()}.{hint} Der Stack bleibt übernommen; "
                          "ist der Ordner weg, hebe die Übernahme im Reiter Stacks auf.")
    assert "shop" in env.store.for_host("h1")


@pytest.mark.asyncio
async def test_after_a_partial_deletion_the_stack_can_go(tmp_path):
    env = await _setup(tmp_path)
    env.rm = result(stderr="rm: cannot remove '/opt/stacks/shop/daten/db': Permission denied\n", code=1)
    await env.executor.execute(_request(remove_folder=True))
    env.sftp.dirs.discard(FOLDER)
    env.sftp.files.pop(f"{FOLDER}/compose.yaml")
    done = await env.executor.execute(_request())
    assert done.success is True, done.error
    assert done.output.startswith("Stack „shop“ gelöscht.") and env.store.for_host("h1") == {}


@pytest.mark.asyncio
async def test_a_failed_down_deletes_nothing(tmp_path):
    env = await _setup(tmp_path)
    env.fake.verb = result(stderr="Error response from daemon: cannot stop container\n", code=1)
    done = await env.executor.execute(_request(remove_folder=True))
    assert done.error == ("Herunterfahren ist gescheitert: Error response from daemon: cannot stop container. Nodvard Deck hat den Stack "
                          "nicht vergessen und den Ordner nicht gelöscht.")
    assert "shop" in env.store.for_host("h1") and FOLDER in env.sftp.dirs
    assert _changing(env) == [_take_down()]


@pytest.mark.asyncio
async def test_a_missing_folder_stops_the_down(tmp_path):
    env = await _setup(tmp_path)
    env.fake.verb = result(code=97)
    done = await env.executor.execute(_request())
    assert done.error.startswith("Den Ordner /opt/stacks/shop gibt es auf dem Server nicht mehr, oder er ist nicht lesbar")
    assert "shop" in env.store.for_host("h1")


@pytest.mark.asyncio
@pytest.mark.parametrize("remove", [False, True])
async def test_forgetting_fails_says_what_happened(tmp_path, remove):
    env = await _setup(tmp_path)

    async def broken(*_a):
        raise OSError("Platte voll")

    env.store.remove = broken
    done = await env.executor.execute(_request(remove_folder=remove))
    rest = "Der Ordner /opt/stacks/shop ist gelöscht." if remove else "Der Ordner /opt/stacks/shop mit der Compose-Datei bleibt."
    assert done.success is False and done.output == "Stack „shop“ heruntergefahren."
    assert done.error.startswith(f"Nodvard Deck konnte den Stack „shop“ nicht vergessen (Platte voll). {rest}")
    assert done.error.endswith("Hebe die Übernahme im Reiter Stacks auf.")
    assert (FOLDER in env.sftp.dirs) is not remove


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"actor": "ai"}, "Einen Stack löscht nur ein Mensch über die Docker-Seite."),
        ({"risk": "medium"}, "Einen Stack zu löschen braucht eine Freigabe mit hohem Risiko."),
        ({"project": "media"}, "Der Stack „media“ ist nicht übernommen. Übernimm ihn zuerst im Reiter Stacks."),
        ({"host": "fremd"}, "Server nicht gefunden."),
        ({"remove_folder": True, "folder": "/opt/stacks/anders"}, "Der Ordner des Stacks hat sich inzwischen geändert. Bitte noch einmal löschen."),
    ],
)
async def test_refusals_change_nothing(tmp_path, change, error):
    env = await _setup(tmp_path)
    assert (await env.executor.execute(_request(**change))).error == error
    assert env.fake.argv_calls == [] and "shop" in env.store.for_host("h1")


def _realpath(env, mapping):
    async def realpath(path):
        return mapping.get(path, "/home/deck" if path == "." else path)

    env.sftp.realpath = realpath


def _unreadable(path, mode):
    raise PermissionError("Keine Berechtigung")


async def _foreign(env, *sources, name="dateimanager"):
    env.fake.containers.append(_container(name, "", sources))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prepare", "reason"),
    [
        (lambda env: env.store.add("h1", _stack(project="innen", working_dir=f"{FOLDER}/innen", config_files=f"{FOLDER}/innen/compose.yaml")),
         "Darin liegt noch ein anderer Stack."),
        (lambda env: env.ctx.settings.set({"stack_roots": {"h1": FOLDER}}), "Das ist der Ordner für neue Stacks."),
        (lambda env: _async(_realpath(env, {".": f"{FOLDER}/home"})), "Darin liegt der Heimatordner des Zugangs von Nodvard Deck."),
        (lambda env: _async(env.sftp.modes.__setitem__(FOLDER, 0o120777) or env.sftp.dirs.discard(FOLDER) or env.sftp.files.__setitem__(FOLDER, b"")),
         "Er ist ein Verweis auf einen anderen Ort oder kein Ordner."),
        (lambda env: _async(_server_files(env.sftp, passwd=PASSWD + f"pi2:x:1002:1002::{FOLDER}:/bin/sh\n")),
         f"Darin liegt der Heimatordner von „pi2“ ({FOLDER})."),
        (lambda env: _async(_server_files(env.sftp, passwd=PASSWD + f"svc:x:1003:1003::{FOLDER}/daten/svc:/bin/sh\n")),
         f"Darin liegt der Heimatordner von „svc“ ({FOLDER}/daten/svc)."),
        (lambda env: _async(_server_files(env.sftp, mountinfo=MOUNTINFO + f"60 22 0:50 / {FOLDER} rw - nfs nas:/stacks rw\n")),
         f"Unter {FOLDER} ist ein Laufwerk oder eine Freigabe eingehängt. Hänge es zuerst aus oder lösche den Ordner selbst."),
        (lambda env: _async(_server_files(env.sftp, mountinfo=MOUNTINFO + "60 22 0:50 / /opt/stacks/shop/mit\\040leer rw - nfs nas:/x rw\n")),
         f"Unter {FOLDER}/mit leer ist ein Laufwerk oder eine Freigabe eingehängt. Hänge es zuerst aus oder lösche den Ordner selbst."),
        (lambda env: _async(setattr(env.sftp, "open", _unreadable)), "Er ließ sich auf dem Server nicht prüfen (Keine Berechtigung)."),
        (lambda env: _async(_realpath(env, {FOLDER: "/etc/shop"})), "Er führt über einen Verweis nach /etc/shop, und dort löscht Nodvard Deck nichts."),
        (lambda env: _async(_realpath(env, {FOLDER: "/home/pi"})), "Darin liegt der Heimatordner von „pi“ (/home/pi)."),
        (lambda env: _foreign(env, f"{FOLDER}/daten"), f"Der Container „dateimanager“ gehört nicht zu diesem Stack und nutzt {FOLDER}/daten daraus."),
        (lambda env: _foreign(env, FOLDER, name="nodvard-deck"), f"Der Container „nodvard-deck“ gehört nicht zu diesem Stack und nutzt {FOLDER} daraus."),
        (lambda env: _async(setattr(env.fake, "rm_probe", result(stderr="rm: unrecognized option '--one-file-system'\n", code=1))),
         "rm auf dem Server kennt die Option --one-file-system nicht (etwa bei BusyBox), ohne sie könnte es in eingehängte Laufwerke "
         "hinein löschen. Lösche den Stack ohne Ordner und den Ordner danach selbst."),
    ],
)
async def test_the_folder_stays_when_something_speaks_against_it(tmp_path, prepare, reason):
    env = await _setup(tmp_path)
    await prepare(env)
    refusal = await env.delete.folder_refusal(SimpleNamespace(id="h1"), _stack())
    assert refusal == f"Den Ordner {FOLDER} löscht Nodvard Deck nicht: {reason}"
    done = await env.executor.execute(_request(remove_folder=True))
    assert done.error == f"{refusal} Gelöscht wurde nichts."
    assert _changing(env) == [] and "shop" in env.store.for_host("h1")


@pytest.mark.asyncio
async def test_a_symlinked_parent_is_checked_at_its_real_place(tmp_path):
    env = await _setup(tmp_path)
    _realpath(env, {FOLDER: "/mnt/daten/stacks/shop"})
    assert await env.delete.folder_refusal(SimpleNamespace(id="h1"), _stack()) is None
    await env.store.add("h1", _stack(project="echt", working_dir="/mnt/daten/stacks/shop/echt", config_files="/mnt/daten/stacks/shop/echt/c.yaml"))
    assert (await env.delete.folder_refusal(SimpleNamespace(id="h1"), _stack())).endswith("Darin liegt noch ein anderer Stack.")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "broken",
    [
        lambda env: setattr(env.fake, "containers", [*env.fake.containers, {"id": "kaputt", "name": "/x", "project": "", "mounts": []}]),
        lambda env: setattr(env.fake, "containers", [_container(f"c{i}") for i in range(2001)]),
    ],
)
async def test_unreadable_containers_keep_the_folder(tmp_path, broken):
    env = await _setup(tmp_path)
    broken(env)
    assert (await env.delete.folder_refusal(SimpleNamespace(id="h1"), _stack())).endswith(
        "Die Container des Servers ließen sich nicht prüfen.")


@pytest.mark.asyncio
async def test_the_folder_is_checked_again_right_before_rm(tmp_path):
    env = await _setup(tmp_path)
    original = env.fake.run_argv

    async def mounted_meanwhile(host, argv, **kwargs):
        answer = await original(host, argv, **kwargs)
        if argv[-1] == "--remove-orphans":
            await _foreign(env, f"{FOLDER}/daten")
        return answer

    env.fake.run_argv = mounted_meanwhile
    done = await env.executor.execute(_request(remove_folder=True))
    assert done.success is False and done.output == "Stack „shop“ heruntergefahren."
    assert done.error == (f"Den Ordner {FOLDER} löscht Nodvard Deck nicht: Der Container „dateimanager“ gehört nicht zu diesem Stack und "
                          f"nutzt {FOLDER}/daten daraus. Der Stack bleibt übernommen; ohne Ordner kannst du ihn noch einmal löschen.")
    assert _changing(env) == [_take_down()] and "shop" in env.store.for_host("h1") and FOLDER in env.sftp.dirs


@pytest.mark.asyncio
async def test_files_outside_the_folder_and_a_running_stack_inside(tmp_path):
    env = await _setup(tmp_path, adopt=False)
    outside = _stack(config_files="/srv/compose/shop.yaml")
    assert (await env.delete.folder_refusal(SimpleNamespace(id="h1"), outside)).endswith(
        "Die Dateien des Stacks liegen nicht alle darin, er gehört also nicht nur zu diesem Stack.")
    env.fake.rows.append(row(project="nachbar", name="nachbar-app-1", working_dir=f"{FOLDER}/nachbar", config_files=f"{FOLDER}/nachbar/compose.yaml"))
    assert (await env.delete.folder_refusal(SimpleNamespace(id="h1"), _stack())).endswith("Darin liegt noch ein anderer Stack.")
    up = _stack(working_dir="/opt", config_files="/opt/compose.yaml")
    assert (await env.delete.folder_refusal(SimpleNamespace(id="h1"), up)).endswith("Er liegt zu weit oben oder an einem Ort, an dem Nodvard Deck nichts löscht.")


@pytest.mark.asyncio
async def test_more_reasons_to_keep_the_folder(tmp_path):
    env = await _setup(tmp_path, adopt=False)
    host = SimpleNamespace(id="h1")
    env_outside = _stack(env_files="/srv/geheim/.env")
    assert (await env.delete.folder_refusal(host, env_outside)).endswith("Die Dateien des Stacks liegen nicht alle darin, er gehört also nicht nur zu diesem Stack.")
    default_root = _stack(working_dir="/opt/stacks", config_files="/opt/stacks/compose.yaml")
    assert (await env.delete.folder_refusal(host, default_root)).endswith("Das ist der Ordner für neue Stacks.")
    await env.store.add("h1", _stack(project="zwilling"))
    assert (await env.delete.folder_refusal(host, _stack())).endswith("Darin liegt noch ein anderer Stack."), "zweiter Stack im selben Ordner"
    await env.store.remove("h1", "zwilling")
    _realpath(env, {".": FOLDER})
    assert (await env.delete.folder_refusal(host, _stack())).endswith("Darin liegt der Heimatordner des Zugangs von Nodvard Deck.")
    _realpath(env, {})
    original = env.sftp.lstat

    async def unknown_kind(path):
        attrs = await original(path)
        return SimpleNamespace(**{**vars(attrs), "permissions": None}) if path == FOLDER else attrs

    env.sftp.lstat = unknown_kind
    assert (await env.delete.folder_refusal(host, _stack())).endswith("Er ist ein Verweis auf einen anderen Ort oder kein Ordner.")


class _Readable(OSError):
    readable = True


def _connection_lost():
    return pytest.importorskip("asyncssh").ConnectionLost("Connection lost")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("where", "error"),
    [
        (PROBE, "Er ließ sich auf dem Server nicht prüfen (die Verbindung zum Server ist abgebrochen)."),
        (READING[0], "Die Container des Servers ließen sich nicht prüfen (die Verbindung zum Server ist abgebrochen)."),
        (RM, "ließ sich nicht ganz löschen: die Verbindung zum Server ist abgebrochen, rm kann auf dem Server noch weiterlaufen."),
        ("down", "Herunterfahren ist abgebrochen: Die Verbindung zum Server ist mitten im Lauf abgebrochen."),
    ],
)
async def test_a_lost_ssh_connection_is_a_reason_not_a_crash(tmp_path, where, error):
    env = await _setup(tmp_path)
    original = env.fake.run_argv
    lost = _connection_lost()
    assert not isinstance(lost, OSError)

    async def breaks(host, argv, **kwargs):
        if argv == where or (where == "down" and argv == _take_down()):
            raise lost
        return await original(host, argv, **kwargs)

    env.fake.run_argv = breaks
    done = await env.executor.execute(_request(remove_folder=True))
    assert done.success is False and error in done.error and "Connection lost" not in done.error
    assert "shop" in env.store.for_host("h1") and env.delete.actions.locks.held("h1", "shop") is None


@pytest.mark.asyncio
async def test_a_failing_cleanup_of_backups_keeps_the_deletion(tmp_path, caplog):
    env = await _setup(tmp_path)

    async def broken(host, project, services):
        raise RuntimeError("kaputt")

    executor = env.mod.DockerStackDeleteExecutor(env.ctx, env.delete, env.store, drop_backups=broken)
    done = await executor.execute(_request())
    assert done.success is True and "shop" not in env.store.for_host("h1")
    assert "docker_stack_backups_not_dropped project=shop" in caplog.text


@pytest.mark.asyncio
async def test_a_failed_login_of_the_core_is_a_reason_too(tmp_path):
    from nodvard_deck.core.ssh import SshError

    env = await _setup(tmp_path)
    original = env.fake.run_argv

    async def breaks(host, argv, **kwargs):
        if argv == _take_down():
            raise SshError("Verbindung zu 192.0.2.1:22 fehlgeschlagen: Connection lost")
        return await original(host, argv, **kwargs)

    env.fake.run_argv = breaks
    done = await env.executor.execute(_request())
    assert done.success is False
    assert done.error.startswith("Herunterfahren ist abgebrochen: Verbindung zu 192.0.2.1:22 fehlgeschlagen: Connection lost.")
    assert "shop" in env.store.for_host("h1")


@pytest.mark.asyncio
async def test_a_broken_channel_is_not_taken_for_a_missing_folder(tmp_path):
    class ChannelOpenError(Exception):
        code = 2

    env = await _setup(tmp_path)

    async def broken(path):
        raise ChannelOpenError("Kanal zu")

    env.sftp.lstat = broken
    done = await env.executor.execute(_request(remove_folder=True))
    assert done.success is False and "ließ sich auf dem Server nicht prüfen (Kanal zu)" in done.error
    assert "shop" in env.store.for_host("h1")


@pytest.mark.asyncio
async def test_a_lost_sftp_connection_is_said_in_german(tmp_path):
    env = await _setup(tmp_path)

    async def lost(path):
        raise _connection_lost()

    env.sftp.lstat = lost
    done = await env.executor.execute(_request(remove_folder=True))
    assert "ließ sich auf dem Server nicht prüfen (die Verbindung zum Server ist abgebrochen)" in done.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("broken", "error"),
    [
        (TimeoutError(), "Der Ordner /opt/stacks/shop ließ sich nicht ganz löschen: nach 10 Minuten ohne Ende abgebrochen, rm kann "
         "auf dem Server noch weiterlaufen."),
        (OSError("Verbindung weg"), "Der Ordner /opt/stacks/shop ließ sich nicht ganz löschen: die Verbindung zum Server ist "
         "abgebrochen, rm kann auf dem Server noch weiterlaufen."),
        (_Readable("Server antwortet nicht."), "Der Ordner /opt/stacks/shop ließ sich nicht ganz löschen: Server antwortet nicht, rm "
         "kann auf dem Server noch weiterlaufen."),
    ],
)
async def test_rm_that_breaks_off_keeps_the_stack(tmp_path, broken, error):
    env = await _setup(tmp_path)
    original = env.fake.run_argv

    async def breaks(host, argv, **kwargs):
        if argv == RM:
            raise broken
        return await original(host, argv, **kwargs)

    env.fake.run_argv = breaks
    done = await env.executor.execute(_request(remove_folder=True))
    assert done.success is False and done.error.startswith(error) and "root" not in done.error
    assert "shop" in env.store.for_host("h1") and env.delete.actions.locks.held("h1", "shop") is None


@pytest.mark.asyncio
async def test_an_unclear_result_keeps_the_stack(tmp_path):
    env = await _setup(tmp_path)
    env.rm = result(stderr="rm: Fehler\n", code=1)
    sftp = env.fake.sftp

    calls = []

    def flaky(host):
        calls.append(host)
        if len(calls) > 2:
            raise OSError("SFTP weg")
        return sftp(host)

    env.fake.sftp = flaky
    done = await env.executor.execute(_request(remove_folder=True))
    assert done.error == (f"Ob der Ordner {FOLDER} ganz gelöscht ist, ließ sich nicht prüfen (rm: Fehler). Sieh im Dateimanager nach. Der "
                          "Stack bleibt übernommen; ist der Ordner weg, hebe die Übernahme im Reiter Stacks auf.")
    assert "shop" in env.store.for_host("h1")


@pytest.mark.asyncio
async def test_the_backups_of_a_deleted_stack_go_too(tmp_path):
    env = await _setup(tmp_path)
    dropped = []

    async def drop(host, project, services):
        dropped.append((host.id, project, sorted(services)))

    executor = env.mod.DockerStackDeleteExecutor(env.ctx, env.delete, env.store, drop)
    env.store.remove, original = None, env.store.remove

    async def broken(*_a):
        raise OSError("Platte voll")

    env.store.remove = broken
    assert (await executor.execute(_request())).success is False and dropped == [], "nur wenn der Stack wirklich vergessen ist"
    env.store.remove = original
    assert (await executor.execute(_request())).success is True
    assert dropped == [("h1", "shop", [])], "die Container sind beim ersten Versuch schon heruntergefahren"


@pytest.mark.asyncio
async def test_one_deletion_per_stack_at_a_time(tmp_path):
    env = await _setup(tmp_path)
    locks = env.delete.actions.locks
    with locks.hold("h1", "shop", "Update"):
        error = (await env.executor.execute(_request())).error
    assert error == "An diesem Stack läuft gerade schon etwas (Update). Versuch es danach noch einmal."
    assert (await env.executor.execute(_request())).success is True and locks.held("h1", "shop") is None


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


def _url(docker_on, tail="stacks/shop/delete"):
    return f"/api/v1/ext/docker/hosts/{docker_on.host}/{tail}"


async def _adopt(client, docker_on):
    _server_files(docker_on.exec.sftp_server)
    shown = {"working_dir": FOLDER, "config_files": [f"{FOLDER}/compose.yaml"]}
    assert (await client.post(_url(docker_on, "stacks/shop/adopt"), json=shown, headers=docker_on.owner)).status_code == 200


@pytest.mark.asyncio
async def test_delete_through_the_gate(client, docker_on):
    await _adopt(client, docker_on)
    check = await client.get(_url(docker_on), headers=docker_on.owner)
    assert check.json() == {"folder": FOLDER, "folder_refusal": None}
    proposed = await client.post(_url(docker_on), json={"remove_folder": False}, headers=docker_on.owner)
    assert proposed.status_code == 202, proposed.text
    assert (proposed.json()["status"], proposed.json()["risk"]) == ("proposed", "high")
    assert _changing_calls(docker_on.exec.argv_calls) == [], "vor der Freigabe läuft nichts"
    action = await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)
    assert "Stack „shop“ löschen: herunterfahren, Container und Netze weg, Volumes bleiben." in action.text
    approved = await client.post(f"/api/v1/actions/{proposed.json()['action_id']}/approve", headers=docker_on.owner)
    assert approved.json()["status"] == "succeeded", approved.json()
    assert any(call[-2:] == ["down", "--remove-orphans"] for call in docker_on.exec.argv_calls)


@pytest.mark.asyncio
async def test_delete_with_the_folder_through_the_gate(client, docker_on):
    await _adopt(client, docker_on)
    proposed = await client.post(_url(docker_on), json={"remove_folder": True}, headers=docker_on.owner)
    assert proposed.status_code == 202, proposed.text
    action = await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)
    assert f"und den Ordner {FOLDER} mit allen Dateien darin löschen" in action.text
    assert action.json()["payload"] == {"project": "shop", "remove_folder": True, "folder": FOLDER}
    approved = await client.post(f"/api/v1/actions/{proposed.json()['action_id']}/approve", headers=docker_on.owner)
    assert approved.json()["status"] == "succeeded", approved.json()["result"]
    assert RM in docker_on.exec.argv_calls


@pytest.mark.asyncio
async def test_delete_errors_through_the_core(client, docker_on):
    assert (await client.get(_url(docker_on), headers=docker_on.owner)).status_code == 409, "nicht übernommen"
    await _adopt(client, docker_on)
    docker_on.exec.sftp_server.dirs.discard(FOLDER)
    docker_on.exec.sftp_server.files.pop(f"{FOLDER}/compose.yaml")
    check = await client.get(_url(docker_on), headers=docker_on.owner)
    assert check.json()["folder_refusal"] == f"Den Ordner {FOLDER} gibt es nicht mehr."
    refused = await client.post(_url(docker_on), json={"remove_folder": True}, headers=docker_on.owner)
    assert refused.status_code == 409 and refused.json()["detail"] == f"Den Ordner {FOLDER} gibt es nicht mehr."
    assert (await client.post(_url(docker_on, "stacks/Shop;id/delete"), json={}, headers=docker_on.owner)).status_code == 400
    assert (await client.post(_url(docker_on), json={"extra": 1}, headers=docker_on.owner)).status_code == 422


@pytest.mark.asyncio
async def test_the_delete_action_is_not_offered_on_the_server_page(client, docker_on):
    refused = await client.post(
        f"/api/v1/hosts/{docker_on.host}/actions/docker.stack_delete",
        json={"payload": {"project": "shop", "remove_folder": True, "folder": FOLDER}, "reason": "Test"}, headers=docker_on.owner,
    )
    assert refused.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get", "post"])
async def test_only_with_hosts_execute(client, docker_on, method):
    kwargs = {"json": {}} if method == "post" else {}
    assert (await getattr(client, method)(_url(docker_on), **kwargs)).status_code == 401
    assert (await getattr(client, method)(_url(docker_on), headers=docker_on.viewer, **kwargs)).status_code == 403
    other = await getattr(client, method)(f"/api/v1/ext/docker/hosts/{docker_on.other}/stacks/shop/delete", headers=docker_on.owner, **kwargs)
    assert other.status_code == 404


# --- Fehler und Grenzfälle beim Löschen ------------------------------------------------------------------------------


def _fails_with(env, error, when):
    """Lässt jeden Aufruf, auf den `when(argv)` passt, mit `error` scheitern."""
    original = env.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if when(argv):
            raise error
        return await original(host, argv, **kwargs)

    env.fake.run_argv = run_argv


def _inspect_says(env, change):
    """Ersetzt die Antwort von `docker container inspect` durch `change(antwort)`."""
    original = env.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        answer = await original(host, argv, **kwargs)
        return change(answer) if argv[:3] == ["docker", "container", "inspect"] else answer

    env.fake.run_argv = run_argv


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/proc/self/mountinfo", "/etc/passwd"])
async def test_a_system_file_that_is_too_big_keeps_the_folder(tmp_path, path):
    env = await _setup(tmp_path)
    env.sftp.files[path] = b"x" * (env.mod.MAX_TEXT_BYTES + 1)
    done = await env.executor.execute(_request(remove_folder=True))
    assert done.error == (f"Den Ordner {FOLDER} löscht Nodvard Deck nicht: Er ließ sich auf dem Server nicht prüfen "
                          f"({path} ist zu groß). Gelöscht wurde nichts.")
    assert _changing(env) == [] and "shop" in env.store.for_host("h1")


@pytest.mark.asyncio
async def test_a_system_file_at_the_size_limit_is_still_read(tmp_path):
    env = await _setup(tmp_path)
    env.sftp.files["/proc/self/mountinfo"] = MOUNTINFO.encode().ljust(env.mod.MAX_TEXT_BYTES, b"x")
    assert await env.delete.folder_refusal(SimpleNamespace(id="h1"), _stack()) is None


@pytest.mark.asyncio
async def test_unreadable_other_stacks_keep_the_folder(tmp_path):
    env = await _setup(tmp_path)
    original = env.delete.stacks.stacks

    async def stacks(host, project=None):
        if project is None:
            raise _mods().stacks.StackProblem("Cannot connect to the Docker daemon")
        return await original(host, project)

    env.delete.stacks.stacks = stacks
    done = await env.executor.execute(_request(remove_folder=True))
    assert done.error == (f"Den Ordner {FOLDER} löscht Nodvard Deck nicht: "
                          "Die anderen Stacks des Servers ließen sich nicht prüfen. Cannot connect to the Docker daemon "
                          "Gelöscht wurde nichts.")
    assert env.fake.argv_calls == [] and "shop" in env.store.for_host("h1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("when", "down_ran"),
    [
        (lambda argv: argv == READING[0], False),
        (lambda argv: argv[:3] == ["docker", "container", "inspect"], False),
        (lambda argv: argv == PROBE, False),
        (lambda argv: argv[-2:] == ["down", "--remove-orphans"], False),
        (lambda argv: argv == RM, True),
    ],
    ids=["container-liste", "container-details", "rm-probe", "herunterfahren", "rm"],
)
async def test_an_unexpected_error_is_not_turned_into_a_reason(tmp_path, when, down_ran):
    env = await _setup(tmp_path)
    _fails_with(env, RuntimeError("Docker kaputt"), when)
    with pytest.raises(RuntimeError, match="Docker kaputt"):
        await env.executor.execute(_request(remove_folder=True))
    assert _changing(env) == ([_take_down()] if down_ran else [])
    assert "shop" in env.store.for_host("h1") and FOLDER in env.sftp.dirs
    assert env.delete.actions.locks.held("h1", "shop") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        lambda answer: result("kein json\n" + answer.stdout.split("\n", 1)[1]),
        lambda answer: result(answer.stdout.split("\n", 1)[0] + "\n"),
        lambda answer: result('[1]\n"text"\n'),
        lambda answer: result(answer.stdout, "Error: No such container: abc\n", 1),
    ],
    ids=["kaputte-zeile", "zu-wenige-zeilen", "keine-objekte", "exit-code"],
)
async def test_unreadable_container_details_keep_the_folder(tmp_path, change):
    env = await _setup(tmp_path)
    _inspect_says(env, change)
    done = await env.executor.execute(_request(remove_folder=True))
    assert done.error == (f"Den Ordner {FOLDER} löscht Nodvard Deck nicht: "
                          "Die Container des Servers ließen sich nicht prüfen. Gelöscht wurde nichts.")
    assert _changing(env) == [] and "shop" in env.store.for_host("h1")


@pytest.mark.asyncio
@pytest.mark.parametrize("remove", [False, True])
async def test_unreadable_containers_after_the_down_keep_the_stack(tmp_path, remove):
    env = await _setup(tmp_path)
    original = env.delete.stacks.stacks

    async def stacks(host, project=None):
        if _take_down() in env.fake.argv_calls:
            raise _mods().stacks.StackProblem("Cannot connect to the Docker daemon")
        return await original(host, project)

    env.delete.stacks.stacks = stacks
    done = await env.executor.execute(_request(remove_folder=remove))
    nothing = "Nodvard Deck hat den Stack nicht vergessen" + (" und den Ordner nicht gelöscht." if remove else ".")
    assert done.error == ("Ob der Stack ganz heruntergefahren ist, ließ sich nicht prüfen. "
                          "Die Container des Stacks ließen sich nicht lesen. "
                          f"Cannot connect to the Docker daemon {nothing}")
    assert _changing(env) == [_take_down()] and "shop" in env.store.for_host("h1") and FOLDER in env.sftp.dirs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "summary"),
    [
        ({"project": "shop", "remove_folder": False, "folder": FOLDER}, "Würde den Stack „shop“ herunterfahren (ohne Volumes)."),
        ({"project": "shop", "remove_folder": True, "folder": FOLDER},
         f"Würde den Stack „shop“ herunterfahren (ohne Volumes) und den Ordner {FOLDER} löschen."),
        ({"project": "shop", "remove_folder": "ja", "folder": FOLDER}, "Würde den Stack „shop“ herunterfahren (ohne Volumes)."),
        ({"remove_folder": True}, "Würde den Stack „?“ herunterfahren (ohne Volumes) und den Ordner ? löschen."),
        ({}, "Würde den Stack „?“ herunterfahren (ohne Volumes)."),
    ],
    ids=["ohne-ordner", "mit-ordner", "nur-ein-echtes-true", "ohne-angaben-mit-ordner", "ohne-angaben"],
)
async def test_the_preview_names_what_would_happen(tmp_path, payload, summary):
    env = await _setup(tmp_path)
    report = await env.executor.dry_run(_request().model_copy(update={"payload": payload}))
    assert report.would_change is True and report.summary == summary
    assert env.fake.argv_calls == [] and "shop" in env.store.for_host("h1")
