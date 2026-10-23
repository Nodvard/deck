"""Schutzregel für die echten Erzeuger langer Befehle: Was Shield, Docker und das Einrichten mit Passwort an einen Server
schicken, hat höchstens `ssh.INLINE_COMMAND_MAX` Byte Befehlstext; der Rest geht über die Standardeingabe."""

from __future__ import annotations

import json
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import asyncssh
import pytest
from nodvard_deck.core import ssh
from nodvard_deck.services import host_setup, one_time_password
from nodvard_sdk import Host as SdkHost
from pydantic import SecretStr

EXTENSIONS = Path(__file__).resolve().parents[2] / "extensions"
WRAPPED_RE = re.compile(rb"(@@nodvard-ende-[0-9a-f]{32})(.*)\1", re.DOTALL)
HOST = SdkHost(id="h1", name="dietpi", display_name="DietPi", address="192.0.2.10", os_family="linux")
LAUNCHED = "@@launch\n@@method=setsid\n@@pid=4242\n"


@pytest.fixture(autouse=True)
def _extensions_on_path(monkeypatch):
    for ext_id in ("shield", "docker"):
        monkeypatch.syspath_prepend(str(EXTENSIONS / ext_id / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_shield", "nodvard_deck_ext_docker")):
            del sys.modules[name]


class _Server:
    """Attrappe der SSH-Verbindung: schneidet jeden Befehlstext mit und antwortet auf den ursprünglichen Befehl."""

    def __init__(self) -> None:
        self.reply = lambda _command: (0, "")
        self.sent: list[tuple[str, str]] = []

    async def run(self, command, **kwargs):
        wrapped = WRAPPED_RE.fullmatch(kwargs.get("input") or b"")
        original = wrapped.group(2).decode() if wrapped else command
        self.sent.append((command, original))
        code, out = self.reply(original)
        return SimpleNamespace(exit_status=code, stdout=out.encode(), stderr=b"")

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


def assert_short(server: _Server) -> None:
    """Mindestens ein Befehl war zu lang für die Befehlszeile, und kein Befehlstext ist es."""
    lengths = [(len(original.encode()), len(text.encode())) for text, original in server.sent]
    assert lengths and max(before for before, _ in lengths) > ssh.INLINE_COMMAND_MAX, lengths
    assert all(after <= ssh.INLINE_COMMAND_MAX for _, after in lengths), lengths


@pytest.fixture
def server(monkeypatch):
    from nodvard_deck.ext import context

    fake = _Server()

    async def connection(_self, _host):
        return fake

    monkeypatch.setattr(context.ExecHandle, "_connection", connection)
    return fake


class _Session:
    async def get(self, *_args):
        return None

    def add(self, _row) -> None:
        pass


def _ctx(settings: dict | None = None) -> SimpleNamespace:
    from nodvard_deck.ext import context

    @asynccontextmanager
    async def session():
        yield _Session()

    async def nothing(*_args, **_kwargs):
        return None

    async def get_settings():
        return dict(settings or {})

    async def get_host(host_id):
        return HOST if host_id == HOST.id else None

    return SimpleNamespace(
        exec=context.ExecHandle(context._PermissionChecker("shield", ["hosts.execute"]), settings=None),
        settings=SimpleNamespace(get=get_settings), hosts=SimpleNamespace(get=get_host),
        db=SimpleNamespace(session=session), audit=SimpleNamespace(log=nothing), ws=SimpleNamespace(broadcast=nothing),
        notify=SimpleNamespace(send=nothing),
    )


def _path(size: int) -> str:
    """Ein Pfad mit genau `size` Byte aus Ordnern mit erlaubter Namenslänge."""
    head, tail = "/home/nutzer", "/rechnung.pdf.exe"
    folders = []
    while len(head) + sum(len(f) + 1 for f in folders) + len(tail) + 101 <= size:
        folders.append("ordner-" + "x" * 93)
    fill = size - len(head) - sum(len(f) + 1 for f in folders) - len(tail) - 1
    path = "/".join([head, *folders, "f" * fill]) + tail
    assert len(path.encode()) == size
    return path


@pytest.mark.asyncio
async def test_shield_lynis_start(server):
    from nodvard_deck_ext_shield import antivirus as av
    from nodvard_deck_ext_shield import defender as df

    server.reply = lambda _command: (0, LAUNCHED)
    shield = df.Defender(_ctx())
    done = av.LynisResult(status="ok")

    async def wait(*_args, **_kwargs):
        return done

    shield._wait_audit = wait
    assert await shield._launch_audit(HOST, "audit_0123456789abcdef", 0.0) is done
    assert_short(server)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,packages,dist", [
    ("all", [], True),
    ("all", [], False),
    ("security", [f"libpaket{i:03d}-erweitert" for i in range(150)], False),
], ids=["alle-proxmox", "alle", "sicherheit-150"])
async def test_shield_updates(server, mode, packages, dist):
    from nodvard_deck_ext_shield import defender as df
    from nodvard_deck_ext_shield import defender_api as api
    from nodvard_deck_ext_shield import patching
    from nodvard_deck_ext_shield import updates as up
    from nodvard_deck_ext_shield.ids import ACTION_UPGRADE

    server.reply = lambda _command: (0, LAUNCHED)
    ctx = _ctx()
    shield = df.Defender(ctx)
    payload = {"mode": mode, "manager": "apt", "packages": packages, "dist_upgrade": dist}
    command = await api.ActionCommands(shield).build(ACTION_UPGRADE, HOST, payload)
    plan = up.upgrade_args("apt", mode, packages, dist_upgrade=dist)
    center = patching.UpdateCenter(ctx, shield)
    finished = (up.UpgradeResult(ok=True), "", 0)

    async def wait(*_args, **_kwargs):
        return finished

    center._wait_detached = wait
    assert await center._run_detached(HOST, "upd_0123456789abcdef", command, plan) == finished
    assert_short(server)


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", [{}, {"watch_use_clamdscan": True}], ids=["clamscan", "clamd"])
async def test_shield_virus_watch_every_few_minutes(server, settings):
    from nodvard_deck_ext_shield import antivirus as av
    from nodvard_deck_ext_shield import defender as df

    shield = df.Defender(_ctx(settings))

    async def minutes(*_args):
        return 17

    shield._watch_minutes = minutes
    await shield._scan_on_host(HOST, "scan_0123456789abcdef", "watch", list(av.WATCH_PATHS), settings)
    assert_short(server)


@pytest.mark.asyncio
async def test_shield_quarantine_of_a_long_path(server):
    from nodvard_deck_ext_shield import defender as df

    finding = SimpleNamespace(
        id="f_0123456789abcdef", host_id=HOST.id, path=_path(1000), scan_id="scan_1", signature="Win.Test.EICAR_HDB-1",
    )
    ok, message = await df.Defender(_ctx())._move_to_quarantine(finding, None)
    assert (ok, message) == (True, "In Quarantäne verschoben.")
    assert finding.path in server.sent[0][1]
    assert_short(server)


@pytest.mark.asyncio
@pytest.mark.parametrize("files", [None, [f"/etc/nginx/sites-enabled/seite-{i:03d}.conf" for i in range(100)]],
                         ids=["vorgabe", "100-dateien"])
async def test_shield_intrusion_check(server, files):
    from nodvard_deck_ext_shield import defender as df
    from nodvard_deck_ext_shield import guard

    ctx = _ctx({"guard_watch_files": files} if files else {})
    assert await guard.Guard(ctx, df.Defender(ctx)).inspect(HOST, notify=False) == {"error": guard.GUARD_INCOMPLETE}
    assert_short(server)


@pytest.mark.asyncio
async def test_docker_stack_list_with_sixty_stacks(server):
    from docker_stack_fakes import row
    from nodvard_deck_ext_docker import stacks as st

    rows = [
        row(project=f"stack{i:02d}", name=f"stack{i:02d}-web-1", working_dir=f"/opt/stacks/stack{i:02d}",
            config_files=f"/opt/stacks/stack{i:02d}/compose.yaml")
        for i in range(st.MAX_STACKS)
    ]
    listing = "".join(json.dumps(r) + "\n" for r in rows)
    server.reply = lambda command: (0, listing if command.startswith("docker ps") else "")
    overview = await st.DockerStacks(_ctx()).overview(HOST)
    assert len(overview["stacks"]) == 60 and overview["check_error"] is None
    assert_short(server)


@pytest.fixture
def setup_script():
    from test_bundled_root_helpers import _helpers

    key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    helpers = [helper for _ext_id, helper in sorted(_helpers().items())]
    script, _one_liner = host_setup.build_setup_script("nodvard", key, sudo=True, helpers=helpers, report_state=True)
    return script


@pytest.fixture
def one_login(monkeypatch):
    fake = _Server()

    async def connect(*_args, **_kwargs):
        return fake

    async def known(*_args):
        return {"ssh-ed25519": "SHA256:bekannt"}

    monkeypatch.setattr(ssh, "connect", connect)
    monkeypatch.setattr(ssh, "load_known_fingerprints", known)
    one_time_password.reset_state()
    yield fake
    one_time_password.reset_state()


@pytest.mark.asyncio
@pytest.mark.parametrize("as_root,sudo_with_password", [(True, False), (False, True), (False, False)],
                         ids=["root", "sudo-passwort", "sudo-ohne-passwort"])
async def test_setup_with_password_and_all_bundled_helpers(monkeypatch, one_login, setup_script, as_root, sudo_with_password):
    with monkeypatch.context() as unlimited:
        unlimited.setattr(ssh, "INLINE_COMMAND_MAX", 10**9)
        inline, _ = host_setup.remote_setup_command(setup_script, as_root=as_root, sudo_with_password=sudo_with_password)
    assert len(inline.encode()) > ssh.INLINE_COMMAND_MAX
    command, data = host_setup.remote_setup_command(setup_script, as_root=as_root, sudo_with_password=sudo_with_password)
    await one_time_password.run_once(
        None, host=SimpleNamespace(id="h1", address="192.0.2.10"), port=22, user_id="u1",
        login_user="root" if as_root else "admin", password=SecretStr("Einmal-Pw-7f3e9c!"), command=command,
        stdin_payload=data, password_on_stdin=sudo_with_password,
    )
    [(sent, _)] = one_login.sent
    assert len(sent.encode()) <= ssh.INLINE_COMMAND_MAX
    assert setup_script.encode() in data


@pytest.mark.asyncio
async def test_a_setup_command_that_is_still_too_long_is_refused_before_logging_in(one_login, setup_script):
    inline = f"sh -c '{setup_script}'"
    with pytest.raises(ssh.CommandTooLong):
        await one_time_password.run_once(
            None, host=SimpleNamespace(id="h1", address="192.0.2.10"), port=22, user_id="u1", login_user="root",
            password=SecretStr("Einmal-Pw-7f3e9c!"), command=inline,
        )
    assert one_login.sent == []
