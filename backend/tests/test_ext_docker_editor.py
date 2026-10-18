"""Compose-Dateien bearbeiten: prüfen über die Standardeingabe, Warnungen, Entwürfe, Verlauf und Speichern nur über das Gate."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from docker_stack_fakes import EXT_DIR, FakeExec, docker_on, ps, real_sftp, result, row  # noqa: F401 - Fixtures


@pytest.fixture(autouse=True)
def _ext_docker_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def _mods():
    from nodvard_deck_ext_docker import adopt, editor, stack_actions, stacks

    return adopt, editor, stack_actions, stacks


FOLDER = "/opt/stacks/shop"
PATH = f"{FOLDER}/compose.yaml"
OLD = b"services:\n  web:\n    image: busybox:1.37\n"
NEW = b"services:\n  web:\n    image: busybox:1.38\n"
IN_FOLDER = 'cd -- "$1" 2>/dev/null || exit 97; shift; exec "$@"'

# So gibt Compose 5.6 (Docker 29.8.2) mit `config --format json` eine Datei voller riskanter Einstellungen aus.
RISKY = {
    "name": "shop",
    "services": {
        "web": {
            "image": "busybox:1.37", "privileged": True, "network_mode": "host", "pid": "host", "uts": "host", "cgroup": "host",
            "cap_add": ["SYS_ADMIN", "CAP_NET_ADMIN", "CHOWN"], "security_opt": ["seccomp=unconfined"],
            "devices": [{"source": "/dev/sda", "target": "/dev/xvda", "permissions": "rwm"}, {"source": "/dev/dri", "target": "/dev/dri", "permissions": "rwm"}],
            "volumes": [
                {"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock", "bind": {}},
                {"type": "bind", "source": "/etc", "target": "/host-etc", "read_only": True, "bind": {}},
                {"type": "bind", "source": "//", "target": "/wurzel", "bind": {}},
                {"type": "bind", "source": "/proc/1/root", "target": "/p", "bind": {}},
                {"type": "bind", "source": "/opt/stacks/shop/daten", "target": "/d", "bind": {}},
                {"type": "bind", "source": "/dev/dri/renderD128", "target": "/dev/dri/renderD128", "bind": {}},
                {"type": "volume", "source": "wurzel", "target": "/w", "volume": {}},
                {"type": "volume", "source": "daten", "target": "/v", "volume": {}},
            ],
        },
        "ruhig": {"image": "busybox:1.37"},
    },
    "volumes": {"daten": {"name": "shop_daten"}, "wurzel": {"name": "shop_wurzel", "driver_opts": {"device": "/", "o": "bind", "type": "none"}}},
    "secrets": {"schatten": {"name": "shop_schatten", "file": "/etc/shadow"}},
    "configs": {"eigen": {"name": "shop_eigen", "file": "/opt/stacks/shop/eigen.conf"}},
}
RISKY_LINES = [
    "„web“ bekommt alle Rechte des Servers (privileged).",
    "„web“ teilt das Netz mit dem Server (network_mode: host).",
    "„web“ teilt die Prozesse mit dem Server (pid: host).",
    "„web“ teilt den Rechnernamen mit dem Server (uts: host).",
    "„web“ teilt die Steuergruppen mit dem Server (cgroup: host).",
    "„web“ bekommt besondere Rechte: NET_ADMIN, SYS_ADMIN (cap_add).",
    "„web“ schaltet einen Schutz ab (seccomp=unconfined).",
    "„web“ bekommt Zugriff auf docker.sock; wer den Container steuert, steuert damit den Server.",
    "„web“ bindet /etc vom Server ein (nur lesend).",
    "„web“ bindet / vom Server ein.",
    "„web“ bindet /proc/1/root vom Server ein.",
    "„web“ bekommt das Gerät /dev/sda des Servers (devices).",
    "Das Volume „wurzel“ bindet / vom Server ein.",
    "Das Geheimnis „schatten“ liest /etc/shadow vom Server.",
]


def _stack(**changes):
    _, _, _, stacks = _mods()
    return stacks.parse_members(ps(row(**changes)))[0]


def test_the_check_reads_the_draft_from_stdin_in_the_stack_folder():
    _, editor, _, _ = _mods()
    stack = _stack(config_files=f"{PATH},{FOLDER}/override.yaml")
    assert editor.cmd_check(stack, 1) == [
        "sh", "-c", IN_FOLDER, "sh", FOLDER, "docker", "compose", "--ansi", "never", "-p", "shop", "--project-directory", FOLDER,
        "-f", PATH, "-f", "-", "--profile", "*", "config", "--format", "json",
    ]
    with pytest.raises(ValueError):
        editor.cmd_check(stack, 2)


@pytest.mark.skipif(shutil.which("sh") is None or sys.platform == "win32", reason="braucht eine POSIX-Shell")
def test_the_draft_arrives_on_stdin(tmp_path):
    import shlex

    _, editor, _, _ = _mods()
    folder = tmp_path / "mein stack"
    folder.mkdir()
    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\nprintf "%s|%s|" "$PWD" "$*" >> "$LOG"; cat >> "$LOG"\n', encoding="utf-8")
    fake.chmod(0o755)
    argv = editor.cmd_check(_stack(working_dir=str(folder), config_files=f"{folder}/compose.yaml"), 0)
    env = {**os.environ, "PATH": f"{fake.parent}{os.pathsep}{os.environ.get('PATH', '')}", "LOG": str(tmp_path / "log")}
    done = subprocess.run(["sh", "-c", shlex.join(argv)], input=NEW, env=env, cwd="/", capture_output=True, check=False, timeout=10)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "log").read_bytes() == (
        f"{folder}|compose --ansi never -p shop --project-directory {folder} -f - --profile * config --format json|".encode() + NEW
    )


def test_risky_settings():
    _, editor, _, _ = _mods()
    assert editor.risky_settings(RISKY) == RISKY_LINES
    assert editor.risky_settings({"services": {"a": {"volumes": [{"type": "bind", "source": "/etc/../"}]}}}) == ["„a“ bindet / vom Server ein."]
    assert editor.risky_settings({"services": {"a": {"volumes": [{"type": "bind", "source": "/root/.ssh"}, {"type": "bind", "source": "/run/docker.sock"}]}}}) == [
        "„a“ bindet /root/.ssh vom Server ein.", "„a“ bekommt Zugriff auf docker.sock; wer den Container steuert, steuert damit den Server.",
    ]
    assert editor.risky_settings({"services": {"a": {"devices": ["/dev/net/tun:/dev/net/tun", "/dev/ttyUSB0:/dev/zigbee", "/dev/serial/by-id/x:/dev/x"]}}}) == []
    assert editor.risky_settings({"services": {"a": {"volumes": [
        {"type": "bind", "source": "/etc/localtime", "read_only": True}, {"type": "bind", "source": "/var"}, {"type": "bind", "source": "/var/lib/containers/x"},
    ]}}}) == ["„a“ bindet /var vom Server ein.", "„a“ bindet /var/lib/containers/x vom Server ein."]
    # Compose 5.6 nimmt `provider:` in die Ausgabe; `up -d` startet das Programm dann auf dem Server selbst.
    assert editor.risky_settings({"services": {"a": {"provider": {"type": "/bin/echo", "options": {}}}}}) == [
        "„a“ startet beim Anwenden ein Programm direkt auf dem Server, außerhalb jedes Containers (provider: /bin/echo).",
    ]
    assert editor.risky_settings({"services": "kaputt", "volumes": [], "secrets": {"x": None}}) == []
    assert editor.risky_settings({"services": {"a": None, "b": {"cap_add": [7], "volumes": ["/:/x"], "devices": [None]}}}) == []
    assert editor.risky_settings({"volumes": {"nfs": {"driver_opts": {"type": "nfs", "o": "addr=10.0.0.2", "device": ":/export"}}}}) == []
    assert editor.risky_settings({"services": {"a": {"volumes": [{"type": "bind", "source": "/etcetera"}, {"type": "bind", "source": "/srv/media"}]}}}) == []


@pytest.mark.parametrize(
    ("code", "stdout", "stderr", "error", "unverified"),
    [
        (1, "", "validating -: services.web.foo false schema\n", "validating compose.yaml: services.web.foo false schema", None),
        (1, "", 'time="x" level=warning msg="obsolete"\nfailed to parse -: go-yaml load error\n', "failed to parse compose.yaml: go-yaml load error", None),
        (1, "", 'time="x" level=info msg="Pulling oci://x" traceID=1\ninvalid containerPort: abc\n', "invalid containerPort: abc", None),
        (3, "", "", "Compose konnte die Datei nicht lesen.", None),
        (97, "", "", None, "Den Ordner des Stacks gibt es auf dem Server nicht mehr, oder er ist nicht lesbar."),
        (0, "kein json", "", None, "Compose hat keine lesbare Antwort geliefert."),
        (0, "[]", "", None, "Compose hat keine lesbare Antwort geliefert."),
    ],
)
def test_parse_check(code, stdout, stderr, error, unverified):
    _, editor, _, _ = _mods()
    checked = editor.parse_check(code, stdout, stderr, "compose.yaml")
    assert (checked.error, checked.unverified, checked.warnings) == (error, unverified, ())
    assert checked.problem == (f"Compose lehnt die Datei ab: {error}" if error else f"Die Datei ließ sich nicht prüfen: {unverified}")


def test_a_good_file():
    _, editor, _, _ = _mods()
    checked = editor.parse_check(0, json.dumps(RISKY), 'time="x" level=warning msg="-: the attribute `version` is obsolete"\n', "compose.yaml")
    assert (checked.problem, checked.warnings, checked.services) == (None, tuple(RISKY_LINES), ("ruhig", "web"))
    assert checked.as_response() == {"ok": True, "error": None, "unverified": None, "warnings": RISKY_LINES, "services": ["ruhig", "web"]}


def test_widening_a_known_mount_is_a_new_warning():
    _, editor, _, _ = _mods()

    def config(read_only):
        return {"services": {"a": {"image": "x", "volumes": [{"type": "bind", "source": "/etc", "read_only": read_only}]}}}

    before = editor.Checked(warnings=tuple(editor.risky_settings(config(True))))
    assert editor.new_warnings(editor.Checked(warnings=tuple(editor.risky_settings(config(False)))), before) == ["„a“ bindet /etc vom Server ein."]
    # Nur ein anderes Image für einen Dienst, der schon riskant war: keine neue Warnung, also keine hohe Stufe.
    newer = {"services": {"a": {**config(True)["services"]["a"], "image": "y"}}}
    assert editor.new_warnings(editor.Checked(warnings=tuple(editor.risky_settings(newer))), before) == []


def test_only_new_warnings_count():
    _, editor, _, _ = _mods()
    now = editor.Checked(warnings=("a", "b"))
    assert editor.new_warnings(now, editor.Checked(warnings=("a",))) == ["b"]
    assert editor.new_warnings(now, editor.Checked(warnings=("a", "b", "c"))) == []
    assert editor.new_warnings(now, editor.Checked(error="kaputt", warnings=("a",))) == ["a", "b"]
    assert editor.new_warnings(now, editor.Checked(unverified="weg")) == ["a", "b"]


def test_changed_lines():
    _, editor, _, _ = _mods()
    assert editor.changed_lines(OLD, NEW) == "+1 −1 Zeilen"
    assert editor.changed_lines(OLD, OLD + b"# neu\n# auch\n") == "+2 −0 Zeilen"


def test_drafts(tmp_path, monkeypatch):
    _, editor, _, _ = _mods()
    drafts = editor.Drafts(tmp_path / "drafts")
    draft = drafts.put("h1", "shop", PATH, NEW)
    meta, content = drafts.get(draft)
    assert (meta["host_id"], meta["project"], meta["path"], content) == ("h1", "shop", PATH, NEW)
    assert oct((tmp_path / "drafts" / f"{draft}.yaml").stat().st_mode & 0o777) == "0o600"
    (tmp_path / "drafts" / f"{draft}.yaml").write_bytes(b"untergeschoben")
    assert drafts.get(draft) is None
    assert drafts.get("../../etc/passwd") is None
    drafts.drop(draft)
    assert list((tmp_path / "drafts").iterdir()) == []
    monkeypatch.setattr(editor, "MAX_DRAFTS", 3)
    made = [drafts.put("h1", "shop", PATH, NEW) for _ in range(5)]
    assert {p.stem for p in (tmp_path / "drafts").glob("*.json")} <= set(made) and len(list((tmp_path / "drafts").glob("*.json"))) == 3


def test_old_and_orphaned_drafts_go(tmp_path):
    _, editor, _, _ = _mods()
    drafts = editor.Drafts(tmp_path)
    old = drafts.put("h1", "shop", PATH, NEW)
    for suffix in (".json", ".yaml"):
        os.utime(tmp_path / f"{old}{suffix}", (0, 0))
    orphan, writing = "a" * 32, "b" * 32
    (tmp_path / f"{orphan}.yaml").write_bytes(NEW)
    os.utime(tmp_path / f"{orphan}.yaml", (0, 0))
    (tmp_path / f"{writing}.yaml").write_bytes(NEW)
    fresh = drafts.put("h1", "shop", PATH, NEW)
    assert drafts.get(old) is None and drafts.get(fresh) is not None
    assert not (tmp_path / f"{orphan}.yaml").exists()
    assert (tmp_path / f"{writing}.yaml").exists(), "ein Entwurf, dessen Beschreibung noch geschrieben wird, bleibt"


def test_a_draft_that_cannot_be_written_leaves_nothing(tmp_path, monkeypatch):
    _, editor, _, _ = _mods()
    drafts = editor.Drafts(tmp_path)
    original = editor._write_private

    def full(path, data):
        if path.suffix == ".json":
            raise OSError(28, "No space left on device")
        original(path, data)

    monkeypatch.setattr(editor, "_write_private", full)
    with pytest.raises(OSError):
        drafts.put("h1", "shop", PATH, NEW)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("step", ["write", "replace"])
def test_a_full_disk_leaves_no_hidden_copy(tmp_path, monkeypatch, step):
    _, editor, _, _ = _mods()
    if step == "write":
        real = os.fdopen

        class Full:
            def __init__(self, fd):
                self._handle = real(fd, "wb")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._handle.close()

            def write(self, data):
                raise OSError(28, "No space left on device")

        monkeypatch.setattr(os, "fdopen", lambda fd, mode: Full(fd))
    else:
        def full(src, dst):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(os, "replace", full)
    with pytest.raises(OSError):
        editor.Drafts(tmp_path / "drafts").put("h1", "shop", PATH, NEW)
    with pytest.raises(OSError):
        editor.History(tmp_path / "history").add("h1", "shop", PATH, NEW)
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_tidying_up_survives_a_draft_that_is_already_gone(tmp_path, monkeypatch):
    import pathlib

    _, editor, _, _ = _mods()
    drafts = editor.Drafts(tmp_path)
    gone = drafts.put("h1", "shop", PATH, NEW)
    original = pathlib.Path.stat

    def stat(self, *args, **kwargs):
        if self.stem == gone:
            raise FileNotFoundError(2, "No such file or directory")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "stat", stat)
    assert drafts.get(drafts.put("h1", "shop", PATH, NEW)) is not None


def test_history_keeps_twenty_after_saving(tmp_path):
    _, editor, _, _ = _mods()
    history = editor.History(tmp_path)
    ids = [history.add("h1", "shop", PATH, f"fassung {n}\n".encode()) for n in range(22)]
    assert len(history.entries("h1", "shop")) == 22, "bis zum Aufräumen nach dem Speichern bleibt alles"
    history.prune("h1", "shop")
    entries = history.entries("h1", "shop")
    assert len(entries) == 20 and entries[0]["id"] == max(ids)
    assert history.entry("h1", "shop", entries[0]["id"])["content"] == "fassung 21\n"
    history.drop("h1", "shop", entries[0]["id"])
    assert history.entry("h1", "shop", entries[0]["id"]) is None
    assert history.entry("h1", "shop", "../x") is None
    assert history.entries("h1", "andere") == [] and history.entries("h2", "shop") == []


# --- Ausführer -------------------------------------------------------------------------------------------------------


async def _async(value):
    return value


def _setup(tmp_path, fake: FakeExec | None = None):
    adopt, editor, stack_actions, stacks = _mods()
    fake = fake or FakeExec()
    fake.files[PATH] = OLD
    fake.sftp_server.owners[PATH] = (1000, 1000)
    hosts = SimpleNamespace(get=lambda host_id: _async(SimpleNamespace(id=host_id) if host_id in ("h1", "h2") else None))
    ctx = SimpleNamespace(exec=fake, hosts=hosts)
    store = adopt.AdoptedStacks(tmp_path)
    actions = stack_actions.DockerStackExecutor(ctx, stacks.DockerStacks(ctx, store), store)
    ed = editor.StackEditor(ctx, actions, tmp_path)
    return SimpleNamespace(fake=fake, sftp=fake.sftp_server, store=store, editor=ed, save=editor.DockerStackSaveExecutor(ctx, ed, actions), mod=editor)


def _request(env, content=NEW, *, base=None, risk="medium", apply=False, actor="user", draft_for=("h1", "shop"), **payload):
    from nodvard_sdk.actions import ActionRequest
    from nodvard_sdk.types import Actor, Risk

    draft = env.editor.drafts.put(*draft_for, PATH, content)
    body = {"project": "shop", "path": PATH, "draft": draft, "sha256": env.mod.sha256(content),
            "base_sha256": base or env.mod.sha256(OLD), "apply": apply, **payload}
    return ActionRequest(
        action_type="docker.stack_save", payload=body, host_ref="h1", risk=Risk(risk), proposed_by=Actor(type=actor, id="u1"), reason="Test",
    )


def _leftovers(env):
    return [p for p in env.fake.files if "nodvard" in p]


@pytest.mark.asyncio
async def test_save_replaces_safely_and_keeps_owner_and_mode(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    env.sftp.modes[PATH] = 0o100640
    request = _request(env)
    done = await env.save.execute(request)
    assert (done.success, done.output) == (True, "„compose.yaml“ von Stack „shop“ gespeichert. Die alte Fassung steht im Verlauf.")
    assert env.fake.files[PATH] == NEW
    assert (env.sftp.modes[PATH], env.sftp.owners[PATH]) == (0o100640, (1000, 1000))
    written = [(path, mode) for path, mode in env.sftp.seen_modes if path != PATH]
    assert written and all(mode == 0o600 for _, mode in written), "die Temp-Datei ist beim Schreiben nur für den Zugang lesbar"
    assert _leftovers(env) == []
    assert env.fake.stdin == [NEW, OLD], "Entwurf und alte Fassung geprüft"
    entry = env.editor.history.entries("h1", "shop")[0]
    assert env.editor.history.entry("h1", "shop", entry["id"])["content"].encode() == OLD
    assert env.editor.drafts.get(request.payload["draft"]) is None
    assert not any(call[-2:] == ["up", "-d"] for call in env.fake.argv_calls)


@pytest.mark.asyncio
async def test_without_root_the_owner_stays_by_writing_in_place(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    env.sftp.chown_denied = True
    env.sftp.modes[PATH] = 0o100600
    assert (await env.save.execute(_request(env))).success is True
    assert (env.fake.files[PATH], env.sftp.owners[PATH], env.sftp.modes[PATH]) == (NEW, (1000, 1000), 0o100600)
    assert _leftovers(env) == []


@pytest.mark.asyncio
async def test_a_folder_without_write_access_writes_in_place(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    env.sftp.denied_paths.add(f"{FOLDER}/.compose.yaml.nodvard-")
    assert (await env.save.execute(_request(env))).success is True
    assert env.fake.files[PATH] == NEW and _leftovers(env) == []


@pytest.mark.asyncio
async def test_without_posix_rename(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    env.sftp.posix_rename_missing = True
    assert (await env.save.execute(_request(env))).success is True
    assert env.fake.files[PATH] == NEW and _leftovers(env) == []


@pytest.mark.asyncio
async def test_without_posix_rename_a_failed_rename_brings_the_file_back(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    env.sftp.posix_rename_missing = True
    env.sftp.failing_renames.add(PATH)
    original = env.sftp.rename

    async def rename(old, new):
        # Nur der Schritt Temp-Datei -> Ziel scheitert, das Zurückholen klappt.
        if new == PATH and ".nodvard-neu-" in old:
            raise OSError("rename to compose.yaml failed")
        env.sftp.failing_renames.discard(PATH)
        await original(old, new)

    env.sftp.rename = rename
    done = await env.save.execute(_request(env))
    assert done.success is False and done.error.startswith("Die Datei ließ sich nicht schreiben: rename to compose.yaml failed")
    assert env.fake.files[PATH] == OLD and _leftovers(env) == []
    assert env.editor.history.entries("h1", "shop") == []


@pytest.mark.asyncio
async def test_a_change_during_the_save_wins(tmp_path, monkeypatch):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    original = env.mod._take_over_owner

    async def meanwhile(handle, old):
        env.fake.files[PATH] = "services: {}  # von Hand geändert\n".encode()
        return await original(handle, old)

    monkeypatch.setattr(env.mod, "_take_over_owner", meanwhile)
    done = await env.save.execute(_request(env))
    assert done.error == env.mod.STALE
    assert env.fake.files[PATH] == "services: {}  # von Hand geändert\n".encode() and _leftovers(env) == []
    assert env.editor.history.entries("h1", "shop") == []


@pytest.mark.asyncio
async def test_save_and_apply(tmp_path, monkeypatch):
    from nodvard_deck_ext_docker import after_check, image_apply

    env = _setup(tmp_path)
    await env.store.add("h1", _stack())

    async def check_running(ctx, host, refs, **_):
        return image_apply.Verdict(ok=True, text="läuft stabil"), None

    monkeypatch.setattr(after_check, "check_running", check_running)
    done = await env.save.execute(_request(env, apply=True))
    assert done.success is True and done.output.endswith("Stack „shop“ angewendet.\nPrüfung danach: läuft stabil")
    assert done.detail["saved"] is True
    env.fake.verb = result(stderr="Error response from daemon: port is already allocated\n", code=1)
    env.fake.files[PATH] = OLD
    failed = await env.save.execute(_request(env, apply=True))
    assert failed.success is False and failed.error == "Gespeichert, aber das Anwenden ist gescheitert: Error response from daemon: port is already allocated"
    assert failed.detail["saved"] is True and env.fake.files[PATH] == NEW


@pytest.mark.asyncio
async def test_saved_is_said_even_when_applying_breaks(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())

    async def broken(*_args, **_kwargs):
        raise RuntimeError("kaputt")

    env.save._actions.run = broken
    done = await env.save.execute(_request(env, apply=True))
    assert done.success is False and done.error.startswith("Gespeichert, aber das Anwenden ist gescheitert:")
    assert done.detail == {"saved": True} and env.fake.files[PATH] == NEW


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "start"),
    [
        ({"base": "0" * 64}, "Die Datei wurde inzwischen auf dem Server geändert."),
        ({"actor": "ai"}, "Eine Compose-Datei speichert nur ein Mensch"),
        ({"actor": "scheduler"}, "Eine Compose-Datei speichert nur ein Mensch"),
        ({"actor": "system"}, "Eine Compose-Datei speichert nur ein Mensch"),
        ({"actor": "extension"}, "Eine Compose-Datei speichert nur ein Mensch"),
        ({"sha256": "f" * 64}, "Der Entwurf ist abgelaufen oder passt nicht."),
        ({"path": f"{FOLDER}/andere.yaml"}, "Der Entwurf ist abgelaufen oder passt nicht."),
        ({"draft_for": ("h2", "shop")}, "Der Entwurf ist abgelaufen oder passt nicht."),
        ({"draft_for": ("h1", "media")}, "Der Entwurf ist abgelaufen oder passt nicht."),
    ],
)
async def test_save_refuses(tmp_path, change, start):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    request = _request(env, **change)
    done = await env.save.execute(request)
    assert done.success is False and done.error.startswith(start), done.error
    assert env.fake.files[PATH] == OLD and env.editor.history.entries("h1", "shop") == []
    assert env.editor.drafts.get(request.payload["draft"]) is None, "ein abgelehnter Entwurf bleibt nicht liegen"


@pytest.mark.asyncio
async def test_new_risky_settings_need_high(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    calm = env.fake.config
    risky = result(json.dumps(RISKY))
    answers = iter([risky, calm, risky, calm])
    env.fake.run_argv = _answering(env.fake, answers)
    assert (await env.save.execute(_request(env))).error == env.mod.RISK_TOO_LOW and env.fake.files[PATH] == OLD
    assert (await env.save.execute(_request(env, risk="high"))).success is True


@pytest.mark.asyncio
async def test_risky_settings_already_on_the_server_stay_medium(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    env.fake.config = result(json.dumps(RISKY))
    assert (await env.save.execute(_request(env))).success is True


def _answering(fake, answers):
    original = fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[-3:] == ["config", "--format", "json"]:
            fake.stdin.append(kwargs.get("stdin"))
            return next(answers)
        return await original(host, argv, **kwargs)

    return run_argv


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (result(stderr="validating -: services.web.foo false schema\n", code=1), "Compose lehnt die Datei ab: validating compose.yaml: services.web.foo false schema"),
        (result(code=97), "Die Datei ließ sich nicht prüfen: Den Ordner des Stacks gibt es auf dem Server nicht mehr, oder er ist nicht lesbar."),
    ],
)
async def test_a_draft_that_fails_the_check_is_not_written(tmp_path, answer, error):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    env.fake.config = answer
    assert (await env.save.execute(_request(env))).error == error
    assert env.fake.files[PATH] == OLD


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "error"),
    [
        (OSError("Verbindung abgerissen"), "Die Datei ließ sich nicht prüfen: Server nicht erreichbar: Verbindung abgerissen"),
        (TimeoutError(), "Die Datei ließ sich nicht prüfen: Compose hat nicht innerhalb von 60 Sekunden geantwortet."),
    ],
)
async def test_an_unreachable_check_is_not_a_rejection(tmp_path, raised, error):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    original = env.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[-3:] == ["config", "--format", "json"]:
            raise raised
        return await original(host, argv, **kwargs)

    env.fake.run_argv = run_argv
    assert (await env.save.execute(_request(env))).error == error


@pytest.mark.asyncio
async def test_only_adopted_stacks_and_their_files(tmp_path):
    env = _setup(tmp_path)
    done = await env.save.execute(_request(env))
    assert done.error.startswith("Der Stack „shop“ ist nicht übernommen.")
    await env.store.add("h1", _stack())
    other = f"{FOLDER}/andere.yaml"
    env.fake.files[other] = OLD
    from nodvard_sdk.actions import ActionRequest
    from nodvard_sdk.types import Actor

    draft = env.editor.drafts.put("h1", "shop", other, NEW)
    request = ActionRequest(
        action_type="docker.stack_save", host_ref="h1", proposed_by=Actor(type="user", id="u1"), reason="Test",
        payload={"project": "shop", "path": other, "draft": draft, "sha256": env.mod.sha256(NEW), "base_sha256": env.mod.sha256(OLD)},
    )
    assert (await env.save.execute(request)).error == "Diese Datei gehört nicht mehr zum Stack."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setup", "start"),
    [
        (lambda env: env.sftp.modes.__setitem__(PATH, 0o120777), "Die Datei ist ein Verweis"),
        (lambda env: env.sftp.modes.__setitem__(PATH, 0o010644), "Das ist keine normale Datei."),
        (lambda env: env.fake.files.__setitem__(PATH, b"\xff\xfe kaputt"), "Die Datei ist nicht als UTF-8"),
        (lambda env: env.fake.files.__setitem__(PATH, b"x" * (256 * 1024 + 1)), "Die Datei ist größer als 256 KB"),
        (lambda env: env.fake.files.pop(PATH), "Die Datei gibt es auf dem Server nicht mehr."),
    ],
)
async def test_links_and_odd_files_are_not_edited(tmp_path, setup, start):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    setup(env)
    base = env.mod.sha256(env.fake.files.get(PATH, OLD))
    done = await env.save.execute(_request(env, base=base))
    assert done.success is False and done.error.startswith(start), done.error


@pytest.mark.asyncio
async def test_a_broken_channel_is_no_missing_file(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    lost = pytest.importorskip("asyncssh").SFTPConnectionLost("Kanal zu")

    async def broken(path):
        raise lost

    env.sftp.lstat = broken
    done = await env.save.execute(_request(env, base=env.mod.sha256(OLD)))
    assert done.success is False and done.error == "Die Datei ließ sich nicht lesen: Kanal zu. Gespeichert wurde nichts."
    assert env.fake.files[PATH] == OLD


@pytest.mark.asyncio
async def test_a_failed_write_leaves_file_history_and_folder_clean(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    original = env.sftp._writer

    async def failing(path, mode, attrs):
        handle = await original(path, mode, attrs)

        async def write(data):
            raise PermissionError("Schreiben verweigert")

        handle.write = write
        return handle

    env.sftp._writer = failing
    done = await env.save.execute(_request(env))
    assert done.error == "Die Datei ließ sich nicht schreiben: Schreiben verweigert"
    assert env.fake.files == {PATH: OLD}
    assert env.editor.history.entries("h1", "shop") == []


@pytest.mark.asyncio
async def test_a_denied_write_is_said_plainly(tmp_path):
    from docker_stack_fakes import SftpDenied

    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    env.sftp.denied_paths.add(FOLDER)
    done = await env.save.execute(_request(env))
    assert done.error == "Die Datei ließ sich nicht schreiben: Der Benutzer, mit dem Nodvard Deck sich anmeldet, darf diese Datei nicht schreiben."
    assert issubclass(SftpDenied, PermissionError) and env.fake.files == {PATH: OLD}


def _breaking_in_place(env, *, recovery_works=True):
    """Ohne root schreibt der Editor direkt in die Datei; hier bricht das nach dem Abschneiden ab."""
    env.sftp.chown_denied = True
    original = env.sftp._writer
    calls = []

    async def writer(path, mode, attrs):
        handle = await original(path, mode, attrs)
        if path == PATH:
            calls.append(path)
            if len(calls) == 1 or not recovery_works:
                async def write(data):
                    raise ConnectionResetError("Verbindung getrennt")

                handle.write = write
        return handle

    env.sftp._writer = writer


@pytest.mark.asyncio
async def test_a_broken_in_place_write_puts_the_old_file_back(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    _breaking_in_place(env)
    done = await env.save.execute(_request(env))
    assert done.error == "Die Datei ließ sich nicht schreiben: Verbindung getrennt Die alte Fassung steht wieder auf dem Server."
    assert env.fake.files[PATH] == OLD
    assert len(env.editor.history.entries("h1", "shop")) == 1, "die alte Fassung bleibt im Verlauf, bis klar ist, was auf dem Server steht"


@pytest.mark.asyncio
async def test_if_nothing_helps_the_old_file_stays_in_the_history(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    _breaking_in_place(env, recovery_works=False)
    done = await env.save.execute(_request(env))
    assert done.error.endswith("Die Datei auf dem Server ist jetzt vielleicht unvollständig. Die alte Fassung steht im Verlauf: Lade sie "
                               "dort in den Editor und speichere sie erneut.")
    entry = env.editor.history.entries("h1", "shop")[0]
    assert env.editor.history.entry("h1", "shop", entry["id"])["content"].encode() == OLD


@pytest.mark.asyncio
async def test_a_lost_answer_after_the_rename_still_counts_as_saved(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    original = env.sftp.posix_rename

    async def renamed_but_lost(old, new):
        await original(old, new)
        raise ConnectionResetError("Antwort verloren")

    env.sftp.posix_rename = renamed_but_lost
    done = await env.save.execute(_request(env))
    assert done.success is True and env.fake.files[PATH] == NEW
    assert len(env.editor.history.entries("h1", "shop")) == 1


@pytest.mark.asyncio
async def test_a_mode_that_does_not_stick_is_mentioned(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    env.sftp.modes[PATH] = 0o100644
    original = env.sftp._writer

    async def writer(path, mode, attrs):
        handle = await original(path, mode, attrs)

        async def chmod(mode):
            return None

        handle.chmod = chmod
        return handle

    env.sftp._writer = writer
    done = await env.save.execute(_request(env))
    assert done.success is True and done.output.endswith(
        "Die Rechte der alten Datei ließen sich nicht übernehmen; die neue kann nur der Zugang von Nodvard Deck lesen.")
    assert env.sftp.modes[PATH] & 0o777 == 0o600


@pytest.mark.asyncio
async def test_saving_trims_the_history_to_twenty(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    for n in range(20):
        env.editor.history.add("h1", "shop", PATH, f"alt {n}\n".encode())
    assert (await env.save.execute(_request(env))).success is True
    entries = env.editor.history.entries("h1", "shop")
    assert len(entries) == 20 and env.editor.history.entry("h1", "shop", entries[0]["id"])["content"].encode() == OLD


@pytest.mark.asyncio
async def test_long_names_keep_the_temp_file_short(tmp_path):
    env = _setup(tmp_path)
    long_path = f"{FOLDER}/{'x' * 200}.yaml"
    env.fake.files[long_path] = OLD
    env.fake.rows = [row(config_files=long_path), *[r for r in env.fake.rows if r["project"] != "shop"]]
    await env.store.add("h1", _stack(config_files=long_path))
    from nodvard_sdk.actions import ActionRequest
    from nodvard_sdk.types import Actor

    draft = env.editor.drafts.put("h1", "shop", long_path, NEW)
    request = ActionRequest(
        action_type="docker.stack_save", host_ref="h1", proposed_by=Actor(type="user", id="u1"), reason="Test",
        payload={"project": "shop", "path": long_path, "draft": draft, "sha256": env.mod.sha256(NEW), "base_sha256": env.mod.sha256(OLD)},
    )
    assert (await env.save.execute(request)).success is True
    temp = [path for path, _ in env.sftp.seen_modes if ".nodvard-" in path]
    assert temp and all(len(path.rsplit("/", 1)[1]) <= 1 + 100 + len(".nodvard-neu-") + 12 for path in temp)


@pytest.mark.asyncio
async def test_the_file_view_says_why_a_link_is_not_edited():
    _, _, _, stacks = _mods()
    from docker_stack_fakes import FakeSftp

    sftp = FakeSftp({PATH: OLD}, {PATH: 0o120777})
    shown = await stacks.read_file(sftp, PATH)
    assert shown["content"] == OLD.decode() and shown["edit_error"].startswith("Die Datei ist ein Verweis")
    sftp = FakeSftp({PATH: b"\xff kaputt"})
    assert (await stacks.read_file(sftp, PATH))["edit_error"].startswith("Die Datei ist nicht als UTF-8")
    sftp = FakeSftp({PATH: OLD}, {PATH: 0o010644})
    assert (await stacks.read_file(sftp, PATH))["error"] == "Das ist keine normale Datei."


@pytest.mark.asyncio
async def test_one_change_per_stack_at_a_time(tmp_path):
    env = _setup(tmp_path)
    await env.store.add("h1", _stack())
    with env.save._actions.locks.hold("h1", "shop", "Anwenden"):
        error = (await env.save.execute(_request(env))).error
    assert error == "An diesem Stack läuft gerade schon etwas (Anwenden). Versuch es danach noch einmal."


# --- Echtes SFTP -----------------------------------------------------------------------------------------------------


def _real(tmp_path, real_sftp):
    fake = FakeExec()
    fake.sftp = real_sftp.sftp
    env = _setup(tmp_path / "daten", fake)
    real_sftp.file.write_bytes(OLD)
    return env


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="braucht POSIX-Rechte")
async def test_real_sftp_keeps_mode_and_leaves_nothing(tmp_path, real_sftp):
    env = _real(tmp_path, real_sftp)
    await env.store.add("h1", _stack())
    real_sftp.file.chmod(0o640)
    done = await env.save.execute(_request(env))
    assert done.success is True, done.error
    assert real_sftp.file.read_bytes() == NEW and oct(real_sftp.file.stat().st_mode & 0o777) == "0o640"
    assert sorted(p.name for p in real_sftp.file.parent.iterdir()) == ["compose.yaml"]


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="braucht POSIX-Rechte")
async def test_real_sftp_temp_file_is_private_while_written(tmp_path, real_sftp, monkeypatch):
    env = _real(tmp_path, real_sftp)
    await env.store.add("h1", _stack())
    real_sftp.file.chmod(0o644)
    seen = []
    original = env.mod._take_over_owner

    async def look(handle, old):
        tmp = next(p for p in real_sftp.file.parent.iterdir() if ".nodvard-neu-" in p.name)
        seen.append(tmp.stat().st_mode & 0o777)
        return await original(handle, old)

    monkeypatch.setattr(env.mod, "_take_over_owner", look)
    assert (await env.save.execute(_request(env))).success is True
    assert seen == [0o600] and oct(real_sftp.file.stat().st_mode & 0o777) == "0o644"


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="braucht POSIX-Rechte")
async def test_real_sftp_a_broken_in_place_write_restores_the_file(tmp_path, real_sftp, monkeypatch):
    env = _real(tmp_path, real_sftp)
    await env.store.add("h1", _stack())

    async def no_owner(handle, old):
        return False

    monkeypatch.setattr(env.mod, "_take_over_owner", no_owner)
    original = env.mod._write_in_place

    async def breaking(sftp, path, content, base):
        await original(sftp, path, b"servi", base)
        raise ConnectionResetError("Verbindung getrennt")

    monkeypatch.setattr(env.mod, "_write_in_place", breaking)
    done = await env.save.execute(_request(env))
    assert done.error.endswith("Die alte Fassung steht wieder auf dem Server.")
    assert real_sftp.file.read_bytes() == OLD


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() != 0, reason="Besitzer übertragen geht nur als root")
async def test_real_sftp_keeps_the_owner(tmp_path, real_sftp):
    env = _real(tmp_path, real_sftp)
    await env.store.add("h1", _stack())
    os.chown(real_sftp.file, 1234, 1234)
    assert (await env.save.execute(_request(env))).success is True
    stat = real_sftp.file.stat()
    assert (stat.st_uid, stat.st_gid) == (1234, 1234)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="braucht Symlinks")
async def test_real_sftp_refuses_a_link_and_a_swapped_temp_file_hits_nothing(tmp_path, real_sftp, monkeypatch):
    env = _real(tmp_path, real_sftp)
    await env.store.add("h1", _stack())
    real = real_sftp.file.with_name("echt.yaml")
    real_sftp.file.rename(real)
    real_sftp.file.symlink_to("echt.yaml")
    assert (await env.save.execute(_request(env))).error.startswith("Die Datei ist ein Verweis")
    real_sftp.file.unlink()
    real.rename(real_sftp.file)

    victim = real_sftp.root / "geheim"
    victim.write_bytes(b"nur root\n")
    victim.chmod(0o600)
    original = env.mod._take_over_owner

    async def swap(handle, old):
        # Jemand mit Schreibrecht im Ordner tauscht die Temp-Datei gegen einen Verweis auf eine fremde Datei.
        tmp = next(p for p in real_sftp.file.parent.iterdir() if ".nodvard-neu-" in p.name)
        tmp.unlink()
        tmp.symlink_to(victim)
        return await original(handle, old)

    monkeypatch.setattr(env.mod, "_take_over_owner", swap)
    await env.save.execute(_request(env))
    assert victim.read_bytes() == b"nur root\n" and oct(victim.stat().st_mode & 0o777) == "0o600"


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


def _url(docker_on, tail):
    return f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/{tail}"


async def _adopt(client, docker_on):
    shown = {"working_dir": FOLDER, "config_files": [PATH]}
    assert (await client.post(_url(docker_on, "adopt"), json=shown, headers=docker_on.owner)).status_code == 200


def _body(docker_on, **change):
    import hashlib

    return {"path": PATH, "content": NEW.decode(), "base_sha256": hashlib.sha256(docker_on.exec.files[PATH]).hexdigest(), **change}


@pytest.mark.asyncio
async def test_check_through_the_core(client, docker_on):
    import hashlib

    await _adopt(client, docker_on)
    docker_on.exec.config = result(json.dumps(RISKY))
    checked = await client.post(_url(docker_on, "check"), json={"path": PATH, "content": NEW.decode()}, headers=docker_on.owner)
    assert checked.status_code == 200, checked.text
    assert checked.json() == {"ok": True, "error": None, "unverified": None, "warnings": RISKY_LINES, "services": ["ruhig", "web"]}
    assert docker_on.exec.stdin[-1] == NEW
    file = await client.get(_url(docker_on, "file"), headers=docker_on.owner)
    assert file.json()["files"][0]["sha256"] == hashlib.sha256(docker_on.exec.files[PATH]).hexdigest()
    assert file.json()["files"][0]["edit_error"] is None


@pytest.mark.asyncio
async def test_save_through_the_gate(client, docker_on):
    await _adopt(client, docker_on)
    old = docker_on.exec.files[PATH]
    proposed = await client.post(_url(docker_on, "save"), json=_body(docker_on), headers=docker_on.owner)
    assert proposed.status_code == 202, proposed.text
    assert (proposed.json()["status"], proposed.json()["risk"]) == ("proposed", "medium")
    assert docker_on.exec.files[PATH] == old, "vor der Freigabe ändert sich nichts"
    action = await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)
    assert "busybox:1.38" not in action.text and "+1 −1 Zeilen" in action.text
    approved = await client.post(f"/api/v1/actions/{proposed.json()['action_id']}/approve", headers=docker_on.owner)
    assert approved.json()["status"] == "succeeded", approved.json()
    assert docker_on.exec.files[PATH] == NEW
    entry = (await client.get(_url(docker_on, "history"), headers=docker_on.owner)).json()["entries"][0]
    assert entry["path"] == PATH
    loaded = await client.get(_url(docker_on, f"history/{entry['id']}"), headers=docker_on.owner)
    assert loaded.json()["content"].encode() == old
    assert (await client.get(_url(docker_on, "history/20990101T000000000000Z-00000000"), headers=docker_on.owner)).status_code == 404


@pytest.mark.asyncio
async def test_new_risky_settings_are_high_known_ones_are_not(client, docker_on, monkeypatch):
    await _adopt(client, docker_on)
    calm = docker_on.exec.config
    risky = result(json.dumps(RISKY))
    monkeypatch.setattr(docker_on.handle, "run_argv", _answering(docker_on.exec, iter([risky, calm, risky, risky])))
    proposed = await client.post(_url(docker_on, "save"), json=_body(docker_on, apply=True), headers=docker_on.owner)
    assert proposed.status_code == 202 and proposed.json()["risk"] == "high"
    action = await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)
    assert "Neu und riskant:" in action.text and "und den Stack anwenden" in action.text
    known = await client.post(_url(docker_on, "save"), json=_body(docker_on), headers=docker_on.owner)
    assert known.status_code == 202 and known.json()["risk"] == "medium"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "code", "detail"),
    [
        ({"base_sha256": "0" * 64}, 409, "Die Datei wurde inzwischen"),
        ({"content": None}, 409, "Die Datei ist unverändert"),
        ({"path": f"{FOLDER}/andere.yaml"}, 400, "Diese Datei gehört nicht zum Stack."),
        ({"path": f"{FOLDER}/compose.txt"}, 400, "Nur Compose-Dateien"),
        ({"extra": 1}, 422, None),
        ({"base_sha256": "kurz"}, 422, None),
    ],
)
async def test_save_errors(client, docker_on, change, code, detail):
    await _adopt(client, docker_on)
    body = _body(docker_on, **change)
    if change.get("content", "") is None:
        body["content"] = docker_on.exec.files[PATH].decode()
    refused = await client.post(_url(docker_on, "save"), json=body, headers=docker_on.owner)
    assert refused.status_code == code, refused.text
    if detail:
        assert refused.json()["detail"].startswith(detail)


@pytest.mark.asyncio
async def test_a_full_data_folder_is_a_503(client, docker_on, monkeypatch):
    await _adopt(client, docker_on)
    editor = sys.modules["nodvard_deck_ext_docker.editor"]

    def full(path, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(editor, "_write_private", full)
    refused = await client.post(_url(docker_on, "save"), json=_body(docker_on), headers=docker_on.owner)
    assert refused.status_code == 503
    assert refused.json()["detail"].startswith("Der Entwurf ließ sich nicht ablegen (No space left on device).")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "code", "detail"),
    [
        (result(stderr="validating -: services.web.foo false schema\n", code=1), 422, "Compose lehnt die Datei ab: validating compose.yaml"),
        (result(code=97), 502, "Die Datei ließ sich nicht prüfen: Den Ordner des Stacks"),
    ],
)
async def test_a_draft_that_fails_the_check_is_never_proposed(client, docker_on, answer, code, detail):
    await _adopt(client, docker_on)
    docker_on.exec.config = answer
    refused = await client.post(_url(docker_on, "save"), json=_body(docker_on), headers=docker_on.owner)
    assert refused.status_code == code and refused.json()["detail"].startswith(detail)


@pytest.mark.asyncio
async def test_reading_the_current_file(client, docker_on):
    await _adopt(client, docker_on)
    docker_on.exec.modes[PATH] = 0o120777
    linked = await client.post(_url(docker_on, "save"), json=_body(docker_on), headers=docker_on.owner)
    assert linked.status_code == 409 and linked.json()["detail"].startswith("Die Datei ist ein Verweis")
    docker_on.exec.modes[PATH] = 0o100644

    async def broken(path):
        raise RuntimeError("Kanal geschlossen")

    docker_on.exec.sftp_server._reader = broken
    lost = await client.post(_url(docker_on, "save"), json=_body(docker_on), headers=docker_on.owner)
    assert lost.status_code == 502 and lost.json()["detail"] == "Datei nicht lesbar: Kanal geschlossen"


@pytest.mark.asyncio
async def test_not_adopted_and_no_docker(client, docker_on):
    body = {"path": PATH, "content": NEW.decode()}
    refused = await client.post(_url(docker_on, "check"), json=body, headers=docker_on.owner)
    assert refused.status_code == 409 and refused.json()["detail"].startswith("Der Stack „shop“ ist nicht übernommen.")
    other = await client.post(f"/api/v1/ext/docker/hosts/{docker_on.other}/stacks/shop/check", json=body, headers=docker_on.owner)
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_the_save_action_is_not_offered_on_the_server_page(client, docker_on):
    refused = await client.post(
        f"/api/v1/hosts/{docker_on.host}/actions/docker.stack_save",
        json={"payload": {"project": "shop", "path": PATH, "draft": "0" * 32}, "reason": "Test"}, headers=docker_on.owner,
    )
    assert refused.status_code == 404, refused.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "tail", "body"),
    [("post", "check", {"path": PATH, "content": "x"}), ("post", "save", {"path": PATH, "content": "x", "base_sha256": "0" * 64}),
     ("get", "history", None), ("get", "history/20990101T000000000000Z-00000000", None)],
)
async def test_only_with_hosts_execute(client, docker_on, method, tail, body):
    kwargs = {"json": body} if body is not None else {}
    assert (await getattr(client, method)(_url(docker_on, tail), **kwargs)).status_code == 401
    assert (await getattr(client, method)(_url(docker_on, tail), headers=docker_on.viewer, **kwargs)).status_code == 403
