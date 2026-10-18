"""Neue Stacks: Ordner je Server, Vorab-Prüfung, Anlegen nur über das Gate und nie über einen bestehenden Ordner."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from types import SimpleNamespace

import pytest
from docker_stack_fakes import EXT_DIR, FakeExec, SftpDenied, docker_on, real_sftp, result  # noqa: F401 - Fixtures


@pytest.fixture(autouse=True)
def _ext_docker_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def _mods():
    from nodvard_deck_ext_docker import adopt, editor, new_stack, stack_actions, stacks

    return adopt, editor, new_stack, stack_actions, stacks


ROOT = "/opt/stacks"
FOLDER = f"{ROOT}/neu"
PATH = f"{FOLDER}/compose.yaml"
CONTENT = b"services:\n  app:\n    image: busybox:1.37\n"
IN_FOLDER = 'cd -- "$1" 2>/dev/null || exit 97; shift; exec "$@"'
RISKY = {"services": {"app": {"image": "busybox:1.37", "privileged": True}}}


def test_valid_roots():
    _, _, new_stack, _, _ = _mods()
    assert new_stack.valid_root("/opt/stacks") and new_stack.valid_root("/home/pi/stacks") and new_stack.valid_root("/srv/mein stack")
    for bad in ("/", "/etc", "/etc/stacks", "/var", "relativ", "/opt/stacks/", "/opt/../etc", "/opt/$x", "/tmp/stacks", "/var/tmp", "/dev/shm/x", "/dev/dri/x", "/etc/localtime/x", None, 7):
        assert not new_stack.valid_root(bad), bad
    assert new_stack.root_problem("/srv/ä").startswith("Erlaubt sind Buchstaben ohne Umlaute")
    assert new_stack.root_problem("/tmp/x").startswith("In /tmp/x kann jeder Nutzer")


def test_the_root_per_server_then_the_setting_then_the_default():
    _, _, new_stack, _, _ = _mods()
    assert new_stack.stack_root({}, "h1") == "/opt/stacks"
    assert new_stack.stack_root({"stack_root": "/srv/stacks"}, "h1") == "/srv/stacks"
    assert new_stack.stack_root({"stack_root": "/srv/stacks", "stack_roots": {"h1": "/home/pi/stacks"}}, "h1") == "/home/pi/stacks"
    assert new_stack.stack_root({"stack_root": "/srv/stacks", "stack_roots": {"h1": "/home/pi/stacks"}}, "h2") == "/srv/stacks"
    assert new_stack.stack_root({"stack_root": "/etc", "stack_roots": {"h1": "../x"}}, "h1") == "/opt/stacks"
    assert new_stack.root_setting({"stack_root": "/srv/docker/"}, "h1") == ("/srv/docker", None)
    root, problem = new_stack.root_setting({"stack_root": "/srv/x", "stack_roots": {"h1": "/etc/x"}}, "h1")
    assert root == "/srv/x" and problem.startswith("Der eingestellte Ordner /etc/x für diesen Server taugt nicht, deshalb gilt /srv/x.")


def test_the_check_runs_above_the_new_folder():
    _, _, new_stack, _, _ = _mods()
    assert new_stack.cmd_new_check(new_stack.new_stack("neu", ROOT), ROOT) == [
        "sh", "-c", IN_FOLDER, "sh", ROOT, "env", f"PWD={FOLDER}", "docker", "compose", "--ansi", "never", "-p", "neu", "--project-directory", FOLDER,
        "-f", "-", "--profile", "*", "config", "--format", "json",
    ]


# --- Ausführer -------------------------------------------------------------------------------------------------------


async def _async(value):
    return value


class _Settings:
    def __init__(self, data=None) -> None:
        self.data = data or {}

    async def get(self):
        return dict(self.data)

    async def set(self, value):
        self.data = dict(value)


def _setup(tmp_path, settings=None):
    adopt, editor, new_stack, stack_actions, stacks = _mods()
    fake = FakeExec()
    hosts = SimpleNamespace(get=lambda host_id: _async(SimpleNamespace(id=host_id, tags=["docker"]) if host_id in ("h1", "nas") else None))
    ctx = SimpleNamespace(exec=fake, hosts=hosts, settings=_Settings(settings))
    store = adopt.AdoptedStacks(tmp_path)
    st = stacks.DockerStacks(ctx, store)
    actions = stack_actions.DockerStackExecutor(ctx, st, store)
    ed = editor.StackEditor(ctx, actions, tmp_path)
    new = new_stack.NewStacks(ctx, ed, st, store, asyncio.Lock())
    return SimpleNamespace(fake=fake, sftp=fake.sftp_server, store=store, editor=ed, new=new, mod=new_stack, ctx=ctx,
                           create=new_stack.DockerStackCreateExecutor(ctx, new, store, actions))


def _request(env, content=CONTENT, *, name="neu", folder=FOLDER, risk="medium", start=False, actor="user", host="h1", **payload):
    from nodvard_sdk.actions import ActionRequest
    from nodvard_sdk.types import Actor, Risk

    draft = env.editor.drafts.put("h1", name, f"{folder}/compose.yaml", content)
    body = {"project": name, "folder": folder, "draft": draft, "sha256": _sha(content), "start": start, **payload}
    return ActionRequest(action_type="docker.stack_create", payload=body, host_ref=host, risk=Risk(risk), proposed_by=Actor(type=actor, id="u1"), reason="Test")


def _sha(content):
    import hashlib

    return hashlib.sha256(content).hexdigest()


def _without_root(env):
    env.sftp.dirs -= {ROOT, f"{ROOT}/shop"}
    env.fake.files.pop(f"{ROOT}/shop/compose.yaml")


@pytest.mark.asyncio
async def test_create_a_stack(tmp_path):
    env = _setup(tmp_path)
    request = _request(env)
    done = await env.create.execute(request)
    assert (done.success, done.output, done.detail) == (True, "Stack „neu“ in /opt/stacks/neu angelegt und übernommen.", {"created": True})
    assert FOLDER in env.sftp.dirs and env.fake.files[PATH] == CONTENT and env.sftp.modes[PATH] & 0o777 == 0o600
    assert env.sftp.dir_modes[FOLDER] == 0o755
    entry = env.store.for_host("h1")["neu"]
    assert (entry.working_dir, entry.config_files) == (FOLDER, (PATH,))
    assert env.editor.drafts.get(request.payload["draft"]) is None
    assert env.fake.stdin == [CONTENT], "im neuen Ordner noch einmal geprüft"
    assert env.fake.argv_calls[-1][:6] == ["sh", "-c", IN_FOLDER, "sh", FOLDER, "docker"]


@pytest.mark.asyncio
async def test_create_and_start(tmp_path, monkeypatch):
    from nodvard_deck_ext_docker import after_check, image_apply

    env = _setup(tmp_path)

    async def check_running(ctx, host, refs, **_):
        return image_apply.Verdict(ok=True, text="läuft stabil"), None

    monkeypatch.setattr(after_check, "check_running", check_running)
    env.fake.services = result("app\n")
    env.fake.rows = [*env.fake.rows]
    done = await env.create.execute(_request(env, start=True))
    assert done.success is True and "Stack „neu“ angewendet." in done.output
    assert any(call[-2:] == ["up", "-d"] for call in env.fake.argv_calls)
    env.fake.verb = result(stderr="Error response from daemon: pull access denied\n", code=1)
    failed = await env.create.execute(_request(env, name="zwei", folder=f"{ROOT}/zwei", start=True))
    assert failed.error == "Angelegt, aber das Starten ist gescheitert: Error response from daemon: pull access denied"
    assert failed.detail["created"] is True and "zwei" in env.store.for_host("h1")


@pytest.mark.asyncio
async def test_starting_that_breaks_still_says_created(tmp_path, monkeypatch):
    env = _setup(tmp_path)

    async def broken(*args, **kwargs):
        raise RuntimeError("Verbindung weg")

    monkeypatch.setattr(env.create._actions, "run", broken)
    failed = await env.create.execute(_request(env, start=True))
    assert (failed.success, failed.output) == (False, "Stack „neu“ in /opt/stacks/neu angelegt und übernommen.")
    assert failed.error == "Angelegt, aber das Starten ist gescheitert: Verbindung weg" and failed.detail == {"created": True}
    assert "neu" in env.store.for_host("h1")


@pytest.mark.asyncio
async def test_a_missing_root_is_created_too(tmp_path):
    env = _setup(tmp_path)
    _without_root(env)
    assert (await env.create.execute(_request(env))).success is True
    assert {ROOT, FOLDER} <= env.sftp.dirs and env.sftp.dir_modes[ROOT] == env.sftp.dir_modes[FOLDER] == 0o755


@pytest.mark.asyncio
async def test_without_rights_for_the_root_nothing_stays(tmp_path):
    env = _setup(tmp_path)
    _without_root(env)
    env.sftp.denied_paths.add(ROOT)
    done = await env.create.execute(_request(env))
    assert done.error == ("Nodvard Deck darf /opt/stacks nicht anlegen. Lege ihn einmal als root an und gib ihn dem Zugang von Nodvard Deck (chown), "
                          "oder wähle einen Ordner wie /home/deck/stacks.")
    assert ROOT not in env.sftp.dirs and env.store.for_host("h1") == {}


@pytest.mark.asyncio
async def test_without_rights_in_the_root(tmp_path):
    env = _setup(tmp_path)
    env.sftp.denied_paths.add(FOLDER)
    done = await env.create.execute(_request(env))
    assert done.error == ("Nodvard Deck darf /opt/stacks/neu nicht anlegen. Gib dem Zugang von Nodvard Deck Schreibrecht in /opt/stacks, oder wähle "
                          "einen Ordner wie /home/deck/stacks.")


@pytest.mark.asyncio
async def test_a_missing_parent_is_named(tmp_path):
    env = _setup(tmp_path, {"stack_root": "/srv/docker/stacks"})
    done = await env.create.execute(_request(env, folder="/srv/docker/stacks/neu"))
    assert done.error == ("/srv/docker/stacks ließ sich nicht anlegen, weil es den Ordner /srv/docker darüber nicht gibt. Lege ihn zuerst an "
                          "oder wähle einen anderen Ordner.")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prepare", "start"),
    [
        (lambda env: env.sftp.dirs.add(FOLDER), "Den Ordner /opt/stacks/neu gibt es schon."),
        (lambda env: env.fake.files.__setitem__(FOLDER, b"keine Datei"), "Den Ordner /opt/stacks/neu gibt es schon."),
    ],
)
async def test_an_existing_folder_is_never_used(tmp_path, prepare, start):
    env = _setup(tmp_path)
    prepare(env)
    done = await env.create.execute(_request(env))
    assert done.error.startswith(start) and PATH not in env.fake.files


@pytest.mark.asyncio
async def test_names_that_exist_or_are_adopted(tmp_path):
    env = _setup(tmp_path)
    taken = await env.create.execute(_request(env, name="shop", folder=f"{ROOT}/shop"))
    assert taken.error.startswith("Auf diesem Server gibt es schon einen Stack „shop“.")
    await env.create.execute(_request(env))
    again = await env.create.execute(_request(env))
    assert again.error == "Einen Stack „neu“ hat Nodvard Deck auf diesem Server schon übernommen."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (result(stderr="validating -: services.app.foo false schema\n", code=1), "Compose lehnt die Datei ab: validating compose.yaml: services.app.foo false schema"),
        (result(json.dumps(RISKY)), "Die Datei enthält riskante Einstellungen; dafür braucht es eine Freigabe mit hohem Risiko. Bitte noch einmal anlegen."),
    ],
)
async def test_a_bad_file_leaves_nothing_behind(tmp_path, answer, error):
    env = _setup(tmp_path)
    _without_root(env)
    env.fake.config = answer
    assert (await env.create.execute(_request(env))).error == error
    assert ROOT not in env.sftp.dirs and FOLDER not in env.sftp.dirs and PATH not in env.fake.files


@pytest.mark.asyncio
async def test_risky_files_need_high(tmp_path):
    env = _setup(tmp_path)
    env.fake.config = result(json.dumps(RISKY))
    assert (await env.create.execute(_request(env, risk="high"))).success is True


@pytest.mark.asyncio
async def test_a_failed_write_removes_folder_and_file(tmp_path):
    env = _setup(tmp_path)
    original = env.sftp._writer

    async def failing(path, mode, attrs):
        handle = await original(path, mode, attrs)

        async def write(data):
            raise PermissionError("kein Platz")

        handle.write = write
        return handle

    env.sftp._writer = failing
    done = await env.create.execute(_request(env))
    assert done.error == "Die Compose-Datei ließ sich nicht schreiben: kein Platz"
    assert FOLDER not in env.sftp.dirs and PATH not in env.fake.files and env.store.for_host("h1") == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"actor": "ai"}, "Eine Compose-Datei speichert nur ein Mensch über die Docker-Seite."),
        ({"actor": "scheduler"}, "Eine Compose-Datei speichert nur ein Mensch über die Docker-Seite."),
        ({"name": "Neu;rm", "folder": f"{ROOT}/Neu;rm"}, "Ungültiger Stack-Name: 'Neu;rm'"),
        ({"host": "fremd"}, "Kein Docker-Host."),
        ({"folder": "/srv/neu"}, "Der Ordner für neue Stacks wurde inzwischen geändert. Bitte noch einmal anlegen."),
        ({"sha256": "f" * 64}, "Der Entwurf ist abgelaufen oder passt nicht. Bitte noch einmal anlegen."),
    ],
)
async def test_refusals(tmp_path, change, error):
    env = _setup(tmp_path)
    request = _request(env, **change)
    assert (await env.create.execute(request)).error == error
    assert FOLDER not in env.sftp.dirs
    assert env.editor.drafts.get(request.payload["draft"]) is None


@pytest.mark.asyncio
async def test_one_creation_per_name_at_a_time(tmp_path):
    env = _setup(tmp_path)
    with env.create._actions.locks.hold("h1", "neu", "Anlegen"):
        error = (await env.create.execute(_request(env))).error
    assert error == "An diesem Stack läuft gerade schon etwas (Anlegen). Versuch es danach noch einmal."


@pytest.mark.asyncio
async def test_the_place_and_changing_it(tmp_path):
    env = _setup(tmp_path, {"stack_root": "/srv/stacks"})
    host = SimpleNamespace(id="h1", tags=["docker"])
    place = {"root": "/srv/stacks", "default": "/srv/stacks", "root_state": "missing", "root_problem": None, "setting_problem": None, "home": "/home/deck"}
    assert await env.new.place(host) == place
    assert await env.new.set_root("h1", "/opt/stacks") == "/opt/stacks"
    assert await env.new.place(host) == {**place, "root": "/opt/stacks", "root_state": "dir"}
    assert await env.new.set_root("h1", None) == "/srv/stacks"
    env.ctx.settings.data["stack_root"] = "/tmp/stacks"
    assert (await env.new.place(host))["setting_problem"].startswith("Der eingestellte Ordner /tmp/stacks in den Einstellungen des Moduls taugt nicht")
    env.sftp.realpath = lambda path: _async("/root")
    assert (await env.new.place(host))["home"] is None, "/root/stacks wäre ein Systemordner"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prepare", "state", "error"),
    [
        (lambda env: (_without_root(env), env.fake.files.__setitem__(ROOT, b""), env.sftp.modes.__setitem__(ROOT, 0o120777)),
         "link", "/opt/stacks ist ein Verweis auf einen anderen Ordner."),
        (lambda env: (_without_root(env), env.fake.files.__setitem__(ROOT, b"datei")),
         "file", "/opt/stacks ist kein Ordner, sondern eine Datei. Wähle einen anderen Ordner für neue Stacks."),
        (lambda env: env.sftp.dir_modes.__setitem__(ROOT, 0o777),
         "dir", "In /opt/stacks darf jeder Nutzer des Servers schreiben. Dort legt Nodvard Deck keine Stacks an; nimm einen eigenen Ordner."),
    ],
)
async def test_a_root_that_is_no_plain_folder(tmp_path, prepare, state, error):
    env = _setup(tmp_path)
    prepare(env)
    place = await env.new.place(SimpleNamespace(id="h1", tags=["docker"]))
    assert (place["root_state"], place["root_problem"]) == (state, error)
    assert (await env.create.execute(_request(env))).error == error
    assert FOLDER not in env.sftp.dirs


@pytest.mark.asyncio
async def test_a_cancelled_run_cleans_up(tmp_path):

    env = _setup(tmp_path)
    _without_root(env)

    async def cancelled(*_a, **_k):
        raise asyncio.CancelledError

    env.editor.check = cancelled
    with pytest.raises(asyncio.CancelledError):
        await env.create.execute(_request(env))
    assert ROOT not in env.sftp.dirs and FOLDER not in env.sftp.dirs


async def _cancel_when(env, entered):
    task = asyncio.ensure_future(env.create.execute(_request(env)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.2)  # das geschützte Aufräumen läuft zu Ende


@pytest.mark.asyncio
async def test_a_cancel_while_checking_cleans_up(tmp_path):
    env = _setup(tmp_path)
    _without_root(env)
    entered = asyncio.Event()

    async def slow(*_a, **_k):
        entered.set()
        await asyncio.sleep(30)

    env.editor.check = slow
    await _cancel_when(env, entered)
    assert ROOT not in env.sftp.dirs and FOLDER not in env.sftp.dirs


@pytest.mark.asyncio
async def test_a_cancel_while_cleaning_up_still_cleans_up(tmp_path):
    env = _setup(tmp_path)
    _without_root(env)
    env.fake.config = result(stderr="validating -: services.app.foo false schema\n", code=1)
    entered = asyncio.Event()
    original = env.sftp.rmdir

    async def slow(path):
        entered.set()
        await asyncio.sleep(0.05)
        await original(path)

    env.sftp.rmdir = slow
    await _cancel_when(env, entered)
    assert ROOT not in env.sftp.dirs and FOLDER not in env.sftp.dirs


@pytest.mark.asyncio
async def test_a_cancel_while_creating_the_folder_cleans_up(tmp_path):
    env = _setup(tmp_path)
    _without_root(env)
    entered = asyncio.Event()
    original = env.sftp.mkdir

    async def mkdir(path, attrs=None):
        await original(path, attrs)
        if path == FOLDER:
            entered.set()
            await asyncio.sleep(30)  # angelegt, aber die Antwort kommt nicht mehr an

    env.sftp.mkdir = mkdir
    await _cancel_when(env, entered)
    assert ROOT not in env.sftp.dirs and FOLDER not in env.sftp.dirs


@pytest.mark.asyncio
async def test_leftovers_are_quoted(tmp_path):
    env = _setup(tmp_path, {"stack_root": "/opt/mein stack"})
    env.sftp.dirs.add("/opt/mein stack")
    env.fake.config = result(stderr="validating -: services.app.foo false schema\n", code=1)

    async def broken(path):
        raise OSError("Verbindung weg")

    env.sftp.rmdir = broken
    done = await env.create.execute(_request(env, folder="/opt/mein stack/neu"))
    assert done.error.endswith("lösche ihn im Terminal mit „rmdir -- '/opt/mein stack/neu'“.")


@pytest.mark.asyncio
async def test_what_stays_after_a_failed_cleanup_is_named(tmp_path):
    env = _setup(tmp_path)
    env.fake.config = result(stderr="validating -: services.app.foo false schema\n", code=1)

    async def broken(path):
        raise OSError("Verbindung weg")

    env.sftp.rmdir = broken
    done = await env.create.execute(_request(env))
    assert FOLDER in env.sftp.dirs
    assert done.error == (
        "Compose lehnt die Datei ab: validating compose.yaml: services.app.foo false schema. Beim Aufräumen blieb stehen: /opt/stacks/neu. "
        "Ist der Ordner leer, lösche ihn im Terminal mit „rmdir -- /opt/stacks/neu“."
    )


@pytest.mark.asyncio
async def test_a_folder_that_appears_after_the_conflict_check_is_left_alone(tmp_path):
    env = _setup(tmp_path)
    env.sftp.dirs.add(FOLDER)
    env.new.conflict = lambda *_a: _async(None)
    done = await env.create.execute(_request(env))
    assert done.error.startswith("Den Ordner /opt/stacks/neu gibt es inzwischen.")
    assert FOLDER in env.sftp.dirs and PATH not in env.fake.files and env.store.for_host("h1") == {}


@pytest.mark.asyncio
async def test_a_file_that_appears_before_writing_is_never_replaced(tmp_path):
    env = _setup(tmp_path)
    original = env.sftp.mkdir

    async def mkdir(path, attrs=None):
        await original(path, attrs)
        if path == FOLDER:
            env.fake.files[PATH] = b"fremd"

    env.sftp.mkdir = mkdir
    done = await env.create.execute(_request(env))
    assert done.error.startswith("Die Compose-Datei ließ sich nicht schreiben:") and "Beim Aufräumen blieb stehen: /opt/stacks/neu." in done.error
    assert env.fake.files[PATH] == b"fremd" and env.store.for_host("h1") == {}


def _lost():
    return pytest.importorskip("asyncssh").ChannelOpenError(2, "Kanal zu")


@pytest.mark.asyncio
async def test_a_broken_channel_is_no_missing_folder(tmp_path):
    env = _setup(tmp_path)
    folders = set(env.sftp.dirs)

    async def broken(path):
        raise _lost()

    env.sftp.lstat = broken
    assert (await env.new.place(SimpleNamespace(id="h1")))["root_state"] == "unknown"
    unclear = "Ob es den Ordner /opt/stacks schon gibt, ließ sich nicht prüfen (Kanal zu). Versuch es noch einmal."
    assert (await env.create.execute(_request(env))).error == unclear
    env.new.conflict = lambda *_a: _async(None)
    assert (await env.create.execute(_request(env))).error == unclear
    assert env.sftp.dirs == folders and env.store.for_host("h1") == {}


@pytest.mark.asyncio
async def test_without_sftp_the_server_is_unreachable_and_the_place_open(tmp_path):
    env = _setup(tmp_path)

    @contextlib.asynccontextmanager
    async def closed(host):
        raise _lost()
        yield

    env.fake.sftp = closed
    assert (await env.new.conflict(SimpleNamespace(id="h1"), "neu", ROOT)) == "Server nicht erreichbar: Kanal zu"
    place = await env.new.place(SimpleNamespace(id="h1"))
    assert (place["root_state"], place["home"]) == ("unknown", None)


@pytest.mark.asyncio
async def test_a_broken_channel_while_creating_the_folder_names_no_missing_parent(tmp_path):
    env = _setup(tmp_path)
    original = env.sftp.mkdir

    async def mkdir(path, attrs=None):
        if path == FOLDER:
            raise _lost()
        await original(path, attrs)

    env.sftp.mkdir = mkdir
    assert (await env.create.execute(_request(env))).error == "/opt/stacks/neu ließ sich nicht anlegen: Kanal zu"


@pytest.mark.asyncio
async def test_the_first_reason_stays_when_the_channel_breaks_afterwards(tmp_path):
    env = _setup(tmp_path)
    _without_root(env)
    env.new.conflict = lambda *_a: _async(None)
    env.sftp.denied_paths.add(ROOT)
    original, calls = env.sftp.lstat, []

    async def lstat(path):
        calls.append(path)
        if path == ROOT and len(calls) > 1:
            raise _lost()
        return await original(path)

    env.sftp.lstat = lstat
    done = await env.create.execute(_request(env))
    assert done.error.startswith("Nodvard Deck darf /opt/stacks nicht anlegen."), done.error


@pytest.mark.asyncio
async def test_the_first_reason_stays_for_the_stack_folder(tmp_path):
    env = _setup(tmp_path)
    env.new.conflict = lambda *_a: _async(None)
    env.sftp.denied_paths.add(FOLDER)
    original = env.sftp.lstat

    async def lstat(path):
        if path == FOLDER:
            raise _lost()
        return await original(path)

    env.sftp.lstat = lstat
    done = await env.create.execute(_request(env))
    assert done.error.startswith("Nodvard Deck darf /opt/stacks/neu nicht anlegen."), done.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("below", "state", "advice"),
    [
        (ROOT, "unknown", "Nodvard Deck darf /opt nicht betreten und kann /opt/stacks deshalb nicht prüfen. Gib dem Zugang von Nodvard Deck "
                          "Zugriff auf /opt"),
        (f"{ROOT}/", "dir", "Nodvard Deck darf /opt/stacks nicht betreten und kann /opt/stacks/neu deshalb nicht prüfen. Gib dem Zugang von "
                            "Nodvard Deck Zugriff auf /opt/stacks"),
    ],
    ids=["ordner-selbst", "darunter"],
)
async def test_missing_rights_while_checking_say_where(tmp_path, below, state, advice):
    env = _setup(tmp_path)
    original = env.sftp.lstat

    async def lstat(path):
        if path.startswith(below):
            raise SftpDenied(f"permission denied: {path}")
        return await original(path)

    env.sftp.lstat = lstat
    place = await env.new.place(SimpleNamespace(id="h1"))
    assert (place["root_state"], place["home"]) == (state, "/home/deck")
    problem = await env.new.conflict(SimpleNamespace(id="h1"), "neu", ROOT)
    assert problem.startswith(advice) and problem.endswith("oder wähle einen Ordner wie /home/deck/stacks."), problem


@pytest.mark.asyncio
async def test_two_runs_creating_the_root_at_once(tmp_path):
    env = _setup(tmp_path)
    _without_root(env)
    original = env.sftp.mkdir

    async def mkdir(path, attrs=None):
        if path == ROOT:
            await original(path, attrs)
            raise FileExistsError(path)
        await original(path, attrs)

    env.sftp.mkdir = mkdir
    assert (await env.create.execute(_request(env))).success is True


@pytest.mark.asyncio
async def test_adopting_fails_after_writing(tmp_path):
    env = _setup(tmp_path)

    async def full(*_a):
        raise OSError("Platte voll")

    env.store.add = full
    done = await env.create.execute(_request(env, start=True))
    assert done.error == (
        "Die Compose-Datei liegt in /opt/stacks/neu, Nodvard Deck konnte sich den Stack aber nicht merken (Platte voll). Gestartet ist er "
        "noch nicht. Starte ihn im Terminal in diesem Ordner mit „docker compose up -d“; danach lässt er sich im Reiter Stacks übernehmen."
    )
    assert env.fake.files[PATH] == CONTENT and not any(call[-2:] == ["up", "-d"] for call in env.fake.argv_calls)


@pytest.mark.asyncio
async def test_a_host_without_docker(tmp_path):
    env = _setup(tmp_path)
    env.ctx.hosts = SimpleNamespace(get=lambda host_id: _async(SimpleNamespace(id=host_id, tags=["web"])))
    assert (await env.create.execute(_request(env))).error == "Kein Docker-Host."


@pytest.mark.asyncio
async def test_the_precheck_falls_back_to_slash_without_the_root(tmp_path):
    env = _setup(tmp_path)
    calls = []
    original = env.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        calls.append(argv[4])
        if argv[4] == ROOT:
            return result(code=97)
        return await original(host, argv, **kwargs)

    env.fake.run_argv = run_argv
    checked = await env.new.check(SimpleNamespace(id="h1"), env.mod.new_stack("neu", ROOT), ROOT, CONTENT)
    assert checked.problem is None and calls == [ROOT, "/"]


@pytest.mark.asyncio
async def test_a_missing_env_file_gets_a_hint(tmp_path):
    env = _setup(tmp_path)
    env.fake.config = result(stderr=f"env file {FOLDER}/.env not found: stat {FOLDER}/.env: no such file or directory\n", code=1)
    checked = await env.new.check(SimpleNamespace(id="h1"), env.mod.new_stack("neu", ROOT), ROOT, CONTENT)
    assert checked.error.endswith("no such file or directory. Der Ordner des Stacks ist neu und leer; Dateien wie eine .env legst du nach dem Anlegen "
                                  "darin an. Bis dahin lass den Eintrag weg oder markiere ihn unter env_file mit „required: false“.")


# --- Echtes SFTP -----------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="braucht POSIX-Rechte")
async def test_real_sftp_creates_folder_and_private_file(tmp_path, real_sftp):
    env = _setup(tmp_path / "daten")
    env.fake.sftp = real_sftp.sftp
    assert (await env.create.execute(_request(env))).success is True
    created = real_sftp.root / "opt" / "stacks" / "neu" / "compose.yaml"
    assert created.read_bytes() == CONTENT and oct(created.stat().st_mode & 0o777) == "0o600"
    assert oct(created.parent.stat().st_mode & 0o777) == "0o755"
    again = await env.create.execute(_request(env, name="neu2", folder=f"{ROOT}/neu2", content=b"services:\n  x:\n    image: busybox\n"))
    assert again.success is True
    (real_sftp.root / "opt" / "stacks" / "neu3").mkdir()
    taken = await env.create.execute(_request(env, name="neu3", folder=f"{ROOT}/neu3"))
    assert taken.error.startswith("Den Ordner /opt/stacks/neu3 gibt es schon.")


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


def _url(docker_on, tail):
    return f"/api/v1/ext/docker/hosts/{docker_on.host}/{tail}"


@pytest.mark.asyncio
async def test_place_and_root_through_the_core(client, docker_on):
    place = await client.get(_url(docker_on, "new-stack"), headers=docker_on.owner)
    assert place.status_code == 200 and place.json() == {
        "root": "/opt/stacks", "default": "/opt/stacks", "root_state": "dir", "root_problem": None, "setting_problem": None, "home": "/home/deck",
    }
    changed = await client.put(_url(docker_on, "stack-root"), json={"path": "/home/pi/stacks"}, headers=docker_on.owner)
    assert changed.json() == {"root": "/home/pi/stacks"}
    for bad in ("/etc", "relativ", "/", "/tmp/x"):
        assert (await client.put(_url(docker_on, "stack-root"), json={"path": bad}, headers=docker_on.owner)).status_code == 400
    slash = await client.put(_url(docker_on, "stack-root"), json={"path": "/srv/docker/"}, headers=docker_on.owner)
    assert slash.json() == {"root": "/srv/docker"}
    assert (await client.put(_url(docker_on, "stack-root"), json={"path": None}, headers=docker_on.owner)).json() == {"root": "/opt/stacks"}
    logged = await client.get("/api/v1/audit", params={"action": "docker.stack_root"}, headers=docker_on.owner)
    roots = [entry["detail"]["root"] for entry in logged.json()]
    assert sorted(roots, key=str) == sorted(["/home/pi/stacks", "/srv/docker", None], key=str), "jede Änderung steht im Protokoll"


@pytest.mark.asyncio
async def test_a_full_data_folder_is_a_503(client, docker_on, monkeypatch):
    editor = sys.modules["nodvard_deck_ext_docker.editor"]

    def full(path, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(editor, "_write_private", full)
    refused = await client.post(_url(docker_on, "new-stack"), json={"name": "neu", "content": CONTENT.decode()}, headers=docker_on.owner)
    assert refused.status_code == 503
    assert refused.json()["detail"].startswith("Der Entwurf ließ sich nicht ablegen (No space left on device).")
    assert FOLDER not in docker_on.exec.sftp_server.dirs


@pytest.mark.asyncio
async def test_check_and_create_through_the_gate(client, docker_on):
    body = {"name": "neu", "content": CONTENT.decode()}
    checked = await client.post(_url(docker_on, "new-stack/check"), json=body, headers=docker_on.owner)
    assert checked.status_code == 200 and checked.json()["folder"] == FOLDER and checked.json()["conflict"] is None
    proposed = await client.post(_url(docker_on, "new-stack"), json=body, headers=docker_on.owner)
    assert proposed.status_code == 202, proposed.text
    assert (proposed.json()["status"], proposed.json()["risk"]) == ("proposed", "medium")
    assert FOLDER not in docker_on.exec.sftp_server.dirs, "vor der Freigabe entsteht nichts"
    action = await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)
    assert "busybox" not in action.text and "Neuen Stack „neu“ in /opt/stacks/neu anlegen (3 Zeilen, Dienste: web, worker)." in action.text
    approved = await client.post(f"/api/v1/actions/{proposed.json()['action_id']}/approve", headers=docker_on.owner)
    assert approved.json()["status"] == "succeeded", approved.json()
    assert docker_on.exec.files[PATH] == CONTENT
    listed = await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks", headers=docker_on.owner)
    assert [s for s in listed.json()["stacks"] if s["project"] == "neu"][0]["adopted"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "code", "detail"),
    [
        ({"name": "Neu", "content": "x"}, 400, "Der Name darf nur Kleinbuchstaben"),
        ({"name": "shop", "content": CONTENT.decode()}, 409, "Auf diesem Server gibt es schon einen Stack „shop“."),
        ({"name": "neu", "content": CONTENT.decode(), "extra": 1}, 422, None),
    ],
)
async def test_create_errors(client, docker_on, body, code, detail):
    refused = await client.post(_url(docker_on, "new-stack"), json=body, headers=docker_on.owner)
    assert refused.status_code == code, refused.text
    if detail:
        assert refused.json()["detail"].startswith(detail)


@pytest.mark.asyncio
async def test_rejected_and_risky_files_through_the_core(client, docker_on):
    body = {"name": "neu", "content": CONTENT.decode()}
    docker_on.exec.config = result(stderr="validating -: services.app.foo false schema\n", code=1)
    assert (await client.post(_url(docker_on, "new-stack"), json=body, headers=docker_on.owner)).status_code == 422
    docker_on.exec.config = result(json.dumps(RISKY))
    risky = await client.post(_url(docker_on, "new-stack"), json={**body, "start": True}, headers=docker_on.owner)
    assert risky.status_code == 202 and risky.json()["risk"] == "high"


@pytest.mark.asyncio
async def test_the_create_action_is_not_offered_on_the_server_page(client, docker_on):
    refused = await client.post(
        f"/api/v1/hosts/{docker_on.host}/actions/docker.stack_create",
        json={"payload": {"project": "neu", "folder": FOLDER, "draft": "0" * 32}, "reason": "Test"}, headers=docker_on.owner,
    )
    assert refused.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "tail", "body"),
    [("get", "new-stack", None), ("put", "stack-root", {"path": "/srv/x"}), ("post", "new-stack/check", {"name": "neu", "content": "x"}),
     ("post", "new-stack", {"name": "neu", "content": "x"})],
)
async def test_only_with_hosts_execute(client, docker_on, method, tail, body):
    kwargs = {"json": body} if body is not None else {}
    assert (await getattr(client, method)(_url(docker_on, tail), **kwargs)).status_code == 401
    assert (await getattr(client, method)(_url(docker_on, tail), headers=docker_on.viewer, **kwargs)).status_code == 403
    other = await getattr(client, method)(f"/api/v1/ext/docker/hosts/{docker_on.other}/{tail}", headers=docker_on.owner, **kwargs)
    assert other.status_code == 404
