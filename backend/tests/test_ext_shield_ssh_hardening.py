import asyncio
import hashlib
import importlib
import re
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import httpx
import pytest
import test_ext_shield_root_helper_ssh as root_helper_ssh
from fastapi import FastAPI
from nodvard_sdk import ActionRequest, Actor, Host, Risk

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest"
helper_server = root_helper_ssh.server


class Session:
    username = "deck"
    public_key = KEY
    credential_id = "c1"
    key_credential_id = "c1"
    authorized = True
    managed = False
    root_keys = False
    two_factor = False

    def __init__(self):
        self.calls = []
        self.fail = None
        self.verify_key = AsyncMock(side_effect=self.verify)

    @property
    def key_public_key(self):
        return getattr(self, "selected_key", self.public_key)

    async def verify(self):
        self.calls.append("verify")
        if self.fail == "verify":
            raise ConnectionError("abgelehnt")

    async def run(self, cmd):
        self.calls.append(cmd)
        op = cmd.split()[1]
        if self.fail == op:
            return SimpleNamespace(exit_code=1, stdout="", stderr="fehlgeschlagen")
        out = {"status": f"@@authorized={'yes' if self.authorized else 'no'}\n@@hardened=no\n" + ("@@managed=yes\n" if self.managed else "")
                         + ("@@root_keys=yes\n" if self.root_keys else "") + ("@@two_factor=yes\n" if self.two_factor else ""),
               "apply": "@@pending=yes", "confirm": "@@hardened=yes", "rollback": "@@rollback=yes",
               "off": "@@hardened=no", "add-key": "@@authorized=yes"}[op]
        return SimpleNamespace(exit_code=0, stdout=out, stderr="")


@pytest.fixture
def ssh_module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "extensions/shield/src"))
    module = importlib.import_module("nodvard_deck_ext_shield.ssh_hardening")
    yield module
    for name in list(sys.modules):
        if name == "nodvard_deck_ext_shield" or name.startswith("nodvard_deck_ext_shield."):
            sys.modules.pop(name, None)


@pytest.fixture
def setup(monkeypatch, ssh_module):
    monkeypatch.setattr(ssh_module, "command", lambda op, user, *args: " ".join(["helper", op, user, *args]))
    session = Session()
    host = Host(id="h1", name="pi", display_name="Pi", address="10.0.0.1", os_family="linux", is_managed=True, has_credential=True)

    @asynccontextmanager
    async def connect(_host, **access):
        yield session

    ctx = SimpleNamespace(exec=SimpleNamespace(ssh_session=connect), hosts=SimpleNamespace(get=AsyncMock(return_value=host)),
                          audit=SimpleNamespace(log=AsyncMock()), api=SimpleNamespace(current_actor=lambda: Actor.user("u1", "nico")),
                          actions=SimpleNamespace(propose=AsyncMock(return_value=SimpleNamespace(action_id="a1", status=SimpleNamespace(value="proposed")))))
    return ssh_module.SshHardening(ctx), session, ctx


def request(session, mode="on"):
    ssh = sys.modules["nodvard_deck_ext_shield.ssh_hardening"]
    fields = {"mode": mode, "username": session.username, "public_key": session.public_key, "token": "a" * 32}
    fields["command"] = ssh.build(fields)
    return ActionRequest(action_type=ssh.ACTION, host_ref="h1", payload=fields, risk=Risk.HIGH,
                         proposed_by=Actor.user("u1", "nico"), reason="SSH härten")


async def test_hardening_checks_then_reloads_then_opens_new_connection(setup):
    service, session, ctx = setup
    result = await service.execute(request(session))
    assert result.success
    assert [c.split()[1] if c != "verify" else c for c in session.calls] == ["status", "apply", "verify", "confirm"]
    ctx.audit.log.assert_awaited_once()
    assert ctx.audit.log.call_args.kwargs["outcome"] == "success"


@pytest.mark.parametrize("failure", ["apply", "verify", "confirm"])
async def test_failure_uses_old_session_for_rollback(setup, failure):
    service, session, ctx = setup
    session.fail = failure
    result = await service.execute(request(session))
    assert not result.success
    assert "zurückgenommen" in result.error
    assert session.calls[-1] == "helper rollback deck " + "a" * 32
    assert ctx.audit.log.call_args.kwargs["outcome"] == "failure"


async def test_failed_rollback_is_not_reported_as_success(setup):
    service, session, _ = setup
    session.verify_key = AsyncMock(side_effect=ConnectionError())
    session.fail = "rollback"
    result = await service.execute(request(session))
    assert not result.success
    assert "nicht bestätigt" in result.error


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("sh"), reason="braucht eine POSIX-Shell")
async def test_confirmation_timeout_does_not_claim_rollback(setup, helper_server, monkeypatch):
    service, session, ctx = setup
    run, conf, _, _ = helper_server
    assert run("add-key", *KEY.split()).returncode == 0

    async def run_helper(cmd):
        session.calls.append(cmd)
        parts = cmd.split()
        done = run(parts[1], *parts[3:])
        if parts[1] == "confirm":
            assert done.returncode == 0, done.stderr
            raise TimeoutError("Antwort fehlt")
        return SimpleNamespace(exit_code=done.returncode, stdout=done.stdout, stderr=done.stderr)

    monkeypatch.setattr(session, "run", run_helper)
    result = await service.execute(request(session))
    assert not result.success
    assert conf.exists()
    assert session.calls[-1] == "helper rollback deck " + "a" * 32
    assert "nicht bestätigt" in result.error
    assert "SSH-Zustand prüfen" in result.error
    assert "zurückgenommen" not in result.error
    assert "60 Sekunden" not in result.error
    assert ctx.audit.log.call_args.kwargs["outcome"] == "failure"


@pytest.mark.parametrize("mode,marker", [("off", "off"), ("add-key", "add-key")])
async def test_separate_actions(setup, mode, marker):
    service, session, _ = setup
    assert (await service.execute(request(session, mode))).success
    assert len(session.calls) == 1 and session.calls[0].split()[1] == marker
    session.verify_key.assert_not_awaited()


@pytest.mark.parametrize("change", ["key", "user", "command", "authorized", "managed"])
async def test_stale_or_unprepared_access_never_applies(setup, change):
    service, session, _ = setup
    req = request(session)
    if change == "key": session.public_key = None
    if change == "user": session.username = "root"
    if change == "command": req.payload["command"] = "anything"
    if change == "authorized": session.authorized = False
    if change == "managed": session.managed = True
    assert not (await service.execute(req)).success
    assert not any(" apply " in cmd for cmd in session.calls)


@pytest.mark.parametrize("mode", ["on", "add-key"])
async def test_password_access_cannot_be_proposed(setup, mode):
    service, session, ctx = setup
    session.public_key = None
    app = FastAPI()
    app.include_router(service.routers()[1])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/defender/hosts/h1/ssh-hardening", json={"mode": mode})
    assert response.status_code == 400
    assert "SSH-Schlüssel" in response.json()["detail"]
    ctx.actions.propose.assert_not_awaited()
    assert session.calls == []


async def test_root_warning_and_all_changes_use_gate(setup, ssh_module):
    service, session, ctx = setup
    session.username = "root"
    app = FastAPI()
    app.include_router(service.routers()[1])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for mode in ("on", "add-key", "off"):
            response = await client.post("/defender/hosts/h1/ssh-hardening", json={"mode": mode})
            assert response.status_code == 200
            req = ctx.actions.propose.call_args.args[0]
            assert req.risk == Risk.HIGH and req.action_type == ssh_module.ACTION
            if mode == "on": assert "prohibit-password" in req.reason
    assert all(" status " in cmd for cmd in session.calls)


@pytest.mark.parametrize("root_keys", [False, True])
async def test_root_keys_are_reported_and_named_in_approval(setup, ssh_module, root_keys):
    service, session, ctx = setup
    session.root_keys = root_keys
    assert (await ssh_module.inspect(session))["root_keys"] is root_keys
    app = FastAPI()
    app.include_router(service.routers()[1])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/defender/hosts/h1/ssh-hardening", json={"mode": "on"})).status_code == 200
    assert (ssh_module.ROOT_KEYS_NOTE in ctx.actions.propose.call_args.args[0].reason) is root_keys


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("sh"), reason="braucht eine POSIX-Shell")
@pytest.mark.parametrize("sudo", [False, True])
async def test_root_setup_installs_helper_for_status(ssh_module, tmp_path, monkeypatch, test_settings, sudo):
    from nodvard_deck.api.v1 import host_access
    from nodvard_deck.ext.root_helpers import RootHelperFile
    from nodvard_deck.services import host_setup
    from shield_helper_kit import helper_copy
    from test_host_setup_helpers import _helper_sandbox
    from test_host_setup_script import _run, _stub

    bin_dir, log, helper_dir, captured = _helper_sandbox(tmp_path)
    _stub(bin_dir / "id", 'case "$1" in -u) echo 0;; -gn) echo root;; *) exit 0;; esac\n')
    _stub(bin_dir / "ssh-keygen", "exit 0\n")
    _stub(bin_dir / "install", f'''echo "install $*" >> "{log}"
if [ "$1" = -d ]; then for dest do :; done; mkdir -p "$dest"; exit 0; fi
for arg do src=$dest; dest=$arg; done
case "$dest" in
  "{helper_dir}"/*) cp "$src" "$dest"; /bin/chmod 755 "$dest";;
  *) exit 1;;
esac
''')
    source = helper_copy(tmp_path, path=f"{bin_dir}:/usr/bin:/bin", sdmark=tmp_path / "sd")
    text = source.read_text(encoding="ascii")
    helper = RootHelperFile(
        ext_id="shield", ext_name="Nodvard Shield", version=ssh_module.VERSION,
        install_path=str(helper_dir / "shield-root"), text=text,
        sha256=hashlib.sha256(text.encode("ascii")).hexdigest(), sudo_fallback=False,
    )
    source.unlink()
    build_script = host_setup.build_setup_script
    monkeypatch.setattr(host_setup, "build_setup_script", lambda *args, **kw: build_script(
        *args, **kw, helper_dir=str(helper_dir),
    ))
    monkeypatch.setattr(host_access, "root_helpers_for", lambda host: [helper])
    monkeypatch.setattr(host_access, "matching_requirements", lambda host: [])
    host = SimpleNamespace(id="h1", os_family="linux", name="pi")
    credential = SimpleNamespace(host_id="h1", username="root", kind="ssh_key", secret_id="s1")
    monkeypatch.setattr(host_access, "_load_host", AsyncMock(return_value=host))
    monkeypatch.setattr(host_access.vault, "load_keyring", lambda settings: None)
    key = asyncssh.generate_private_key("ssh-ed25519")
    monkeypatch.setattr(host_access.vault, "read_secret_plaintext", AsyncMock(
        return_value=key.export_private_key().decode(),
    ))
    prepared = await host_access.credential_setup(
        "h1", "c1", SimpleNamespace(get=AsyncMock(return_value=credential)), test_settings, None,
        sudo=sudo, groups=[],
    )
    assert [item.ext_id for item in prepared.helpers] == ["shield"]
    assert not prepared.sudo and not prepared.full_sudo
    done = _run(prepared.one_liner, bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (helper_dir / "shield-root").read_text(encoding="ascii") == text
    report = host_setup.setup_report(done.stdout, user="root")
    assert report["helpers"] == ["shield"] and report["plain_rules"] == []
    assert not list(captured.iterdir())
    assert "visudo" not in log.read_text() and "/etc/sudoers.d/" not in log.read_text()
    monkeypatch.setattr(ssh_module.av, "HELPER", str(helper_dir / "shield-root"))
    monkeypatch.setattr(ssh_module.av, "HELPER_BUNDLED", (helper.version, helper.sha256))
    session = Session()
    session.username = "root"
    session.public_key = prepared.public_key.rsplit(" ", 1)[0]

    async def run(cmd):
        result = _run(cmd, bin_dir)
        return SimpleNamespace(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)

    session.run = run
    state = await ssh_module.inspect(session)
    assert state["root"] and state["authorized"] and state["has_key"]
    assert state["hardened"] is False and state["managed"] is False


@pytest.mark.parametrize("action_type", ["test.access", "shield.ssh_hardening"])
async def test_fingerprint_fields_come_from_action_spec(ssh_module, tmp_path, db_session, action_type):
    from test_ext_context import _build

    assert ssh_module.SPEC.fingerprint_exclude_fields == ["token", "command"]
    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"])
    ctx.actions.register(ssh_module.SPEC.model_copy(update={"action_type": action_type}))
    statuses = []
    for number in range(4):
        payload = {"mode": "on", "username": "root", "token": f"{number:032x}", "command": f"true {number}"}
        req = ActionRequest(
            action_type=action_type, payload=payload, proposed_by=Actor.extension("test-ext"), reason="SSH härten",
        )
        decision = await ctx.actions.propose(req)
        statuses.append(decision.status.value)
        assert req.payload == payload
    assert statuses == ["proposed"] * 3 + ["denied"]
    assert decision.rule == "flap_limit"
    for field, value in (("mode", "off"), ("username", "deck")):
        decision = await ctx.actions.propose(ActionRequest(
            action_type=action_type, payload={**payload, field: value},
            proposed_by=Actor.extension("test-ext"), reason="SSH ändern",
        ))
        assert decision.status.value == "proposed"
    ctx.actions.register(ssh_module.SPEC.model_copy(update={"action_type": "test.full", "fingerprint_exclude_fields": []}))
    for number in range(4):
        decision = await ctx.actions.propose(ActionRequest(
            action_type="test.full", payload={**payload, "token": str(number)},
            proposed_by=Actor.extension("test-ext"), reason="Zugang prüfen",
        ))
        assert decision.status.value == "proposed"


@pytest.mark.parametrize("mode", ["on", "off", "add-key"])
async def test_repeated_api_changes_hit_flap_limit(setup, ssh_module, db_session, monkeypatch, mode):
    from nodvard_deck.core import gate
    from nodvard_deck.services import settings as settings_service

    service, session, ctx = setup
    await settings_service.set_global(db_session, "autonomy.mode", "full")
    await settings_service.set_global(db_session, "autonomy.max_risk", "high")
    monkeypatch.setattr(gate, "find_executor", AsyncMock(return_value=service))
    tokens = iter(f"{n:032x}" for n in range(4))
    monkeypatch.setattr(ssh_module.secrets, "token_hex", lambda _: next(tokens))

    async def propose(req):
        return await gate.propose(
            db_session, ext_id="shield", request=req, command_field=ssh_module.SPEC.command_field,
            fingerprint_exclude_fields=ssh_module.SPEC.fingerprint_exclude_fields,
        )

    ctx.actions.propose.side_effect = propose
    app = FastAPI()
    app.include_router(service.routers()[1])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        statuses = []
        for _ in range(4):
            response = await client.post("/defender/hosts/h1/ssh-hardening", json={"mode": mode})
            assert response.status_code == 200, response.text
            statuses.append(response.json()["status"])
    assert statuses == ["succeeded"] * 3 + ["denied"]
    operation = "apply" if mode == "on" else mode
    assert sum(cmd.startswith(f"helper {operation} ") for cmd in session.calls) == 3


async def test_status_reports_password_and_root_without_mutation(setup, ssh_module):
    _, session, _ = setup
    session.public_key = None
    session.username = "root"
    state = await ssh_module.inspect(session)
    assert not state["has_key"] and not state["hardened"] and state["root"]
    assert "prohibit-password" in state["warning"]
    assert session.calls == ["helper status root"]


def test_helper_only_and_version(ssh_module):
    cmd = ssh_module.command("apply", "deck", "a" * 32)
    assert "ssh-hardening apply deck" in cmd
    assert f"-ge {ssh_module.VERSION}" in cmd and ssh_module.VERSION == 26
    assert "sudo -n sh -c" not in cmd
    assert not ssh_module.SPEC.host_bound and ssh_module.SPEC.command_field == "command"


@pytest.mark.parametrize("filename,pattern", [
    ("18-SSH-HAERTUNG.md", r"ab Version (\d+)"),
    ("en/18-SSH-HARDENING.md", r"version (\d+) or later"),
])
def test_documented_helper_version_matches_command(ssh_module, filename, pattern):
    text = (Path(__file__).resolve().parents[2] / "docs" / filename).read_text(encoding="utf-8")
    version = re.search(pattern, text)
    assert version is not None
    assert int(version[1]) == ssh_module.VERSION


async def test_password_hint_remains_available_when_helper_is_missing(setup):
    service, session, _ = setup
    session.public_key = None
    session.fail = "status"
    app = FastAPI()
    app.include_router(service.routers()[0])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/defender/hosts/h1/ssh-hardening")
    assert response.status_code == 200
    assert response.json()["has_key"] is False
    assert response.json()["hardened"] is None
    assert response.json()["error"]


async def test_cancellation_also_rolls_back(setup):
    service, session, _ = setup
    session.verify_key = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await service.execute(request(session))
    assert session.calls[-1] == "helper rollback deck " + "a" * 32


@pytest.mark.parametrize("key_is_default", [False, True])
async def test_add_key_uses_existing_login_with_real_ssh(
    ssh_module, monkeypatch, db_session, test_settings, key_is_default,
):
    from nodvard_deck.api.deps import get_current_user
    from nodvard_deck.core import gate, ssh as core_ssh
    from nodvard_deck.ext.context import ApiHandle, ExecHandle, _PermissionChecker
    from nodvard_deck.services import hosts as hosts_service

    authorized = set()
    logins = []
    commands = []
    key = asyncssh.generate_private_key("ssh-ed25519")

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username):
            return True

        def password_auth_supported(self):
            return True

        def validate_password(self, username, password):
            logins.append("password")
            return username == "deck" and password == "test-password"

        def public_key_auth_supported(self):
            return True

        def validate_public_key(self, username, offered):
            logins.append("key")
            return username == "deck" and offered.get_fingerprint() in authorized

    async def process(command):
        parts = command.command.split()
        commands.append(parts)
        out = ""
        if parts[:2] == ["helper", "status"]:
            present = bool(parts[3:]) and asyncssh.import_public_key(" ".join(parts[3:])).get_fingerprint() in authorized
            out = f"@@authorized={'yes' if present else 'no'}\n@@hardened=no\n"
        elif parts[:2] == ["helper", "add-key"]:
            authorized.add(asyncssh.import_public_key(" ".join(parts[3:])).get_fingerprint())
            out = "@@authorized=yes\n"
        command.stdout.write(out.encode())
        command.exit(0)

    monkeypatch.setattr(ssh_module, "command", lambda op, user, *args: " ".join(["helper", op, user, *args]))
    server = await asyncssh.listen(
        "127.0.0.1", 0, server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        server_factory=Server, process_factory=process, encoding=None,
    )
    try:
        host = await hosts_service.create_host(db_session, name="ssh-host", address="127.0.0.1")
        host.is_managed = True
        port = server.sockets[0].getsockname()[1]
        password = await hosts_service.add_credential(
            db_session, test_settings, host_id=host.id, kind="ssh_password", username="deck", port=port,
            secret_value="test-password",
        )
        selected = await hosts_service.add_credential(
            db_session, test_settings, host_id=host.id, kind="ssh_key", username="deck", port=port,
            secret_value=key.export_private_key().decode(), is_default=key_is_default,
        )
        await db_session.commit()
        sdk_host = hosts_service.host_to_sdk(host)
        handle = ExecHandle(_PermissionChecker("shield", ["hosts.read", "hosts.execute"]), test_settings)
        if key_is_default:
            with handle.read_access(sdk_host), pytest.raises(core_ssh.SshAuthError):
                async with handle.ssh_session(sdk_host):
                    pytest.fail("Der neue Schlüssel ist noch nicht autorisiert.")
        loaded = SimpleNamespace(manifest=SimpleNamespace(id="shield"), router=None)
        ctx = SimpleNamespace(
            exec=handle, hosts=SimpleNamespace(get=AsyncMock(return_value=sdk_host)),
            audit=SimpleNamespace(log=AsyncMock()), api=ApiHandle(loaded, _PermissionChecker("shield", ["hosts.read", "hosts.execute"])),
            actions=SimpleNamespace(propose=AsyncMock()),
        )

        async def propose(req):
            return await gate.propose(
                db_session, ext_id="shield", request=req, command_field=ssh_module.SPEC.command_field,
                fingerprint_exclude_fields=ssh_module.SPEC.fingerprint_exclude_fields,
            )

        ctx.actions.propose.side_effect = propose
        service = ssh_module.SshHardening(ctx)
        monkeypatch.setattr(gate, "find_executor", AsyncMock(return_value=service))
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1", username="nico", is_owner=True)
        read_router, write_router = service.routers()
        ctx.api.include_router(read_router, permission="hosts.read")
        ctx.api.include_router(write_router, permission="hosts.execute", ssh_preflight=True)
        app.include_router(loaded.router)
        path = f"/defender/hosts/{host.id}/ssh-hardening"
        selection = {"credential_id": password.id, "key_credential_id": selected.id}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            state = await client.get(path, params=selection)
            assert state.status_code == 200
            assert state.json()["has_key"] and not state.json()["authorized"]
            response = await client.post(path, json={"mode": "add-key", **selection})
        assert response.status_code == 200
        assert response.json()["status"] == "proposed"
        req = ctx.actions.propose.call_args.args[0]
        assert req.payload["credential_id"] == password.id
        assert req.payload["key_credential_id"] == selected.id
        assert req.payload["login_public_key"] is None
        assert req.payload["public_key"] == key.export_public_key().decode().strip()
        assert not authorized
        if not key_is_default:
            await hosts_service.make_default_credential(db_session, host.id, selected.id)
            await db_session.commit()
        action, moved = await gate.approve(db_session, response.json()["action_id"], user_id="u1")
        assert moved and action.status == "succeeded"
        assert authorized == {key.get_fingerprint()}
        with handle.read_access(sdk_host):
            async with handle.ssh_session(sdk_host, credential_id=selected.id) as session:
                await session.verify_key()
        assert "password" in logins and "key" in logins
        assert [parts[1] for parts in commands if parts[0] == "helper"] == ["status", "add-key"]
    finally:
        await core_ssh.reset_ssh_pool()
        server.close()
        await asyncio.wait_for(server.wait_closed(), timeout=5)


async def test_password_login_never_hardens_even_with_a_key_on_file(setup):
    service, session, _ = setup
    req = request(session)
    session.selected_key = KEY + "x"
    assert not (await service.execute(req)).success
    assert not any(" apply " in cmd for cmd in session.calls)


async def test_two_factor_server_is_not_hardened_and_not_proposed(setup):
    service, session, ctx = setup
    session.two_factor = True
    result = await service.execute(request(session))
    assert not result.success and "zweiten Faktor" in result.error
    assert not any(" apply " in cmd for cmd in session.calls)
    app = FastAPI()
    app.include_router(service.routers()[1])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/defender/hosts/h1/ssh-hardening", json={"mode": "on"})
    assert response.status_code == 400 and "zweiten Faktor" in response.json()["detail"]
    ctx.actions.propose.assert_not_awaited()


async def test_password_default_cannot_be_proposed(setup):
    service, session, ctx = setup
    session.selected_key = KEY + "x"
    app = FastAPI()
    app.include_router(service.routers()[1])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/defender/hosts/h1/ssh-hardening", json={"mode": "on"})
    assert response.status_code == 400 and "Standard setzen" in response.json()["detail"]
    ctx.actions.propose.assert_not_awaited()


async def test_proposal_reason_warns_about_the_lockout(setup):
    service, _, ctx = setup
    app = FastAPI()
    app.include_router(service.routers()[1])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/defender/hosts/h1/ssh-hardening", json={"mode": "on"})).status_code == 200
    reason = ctx.actions.propose.call_args.args[0].reason
    assert "nur noch mit einem SSH-Schlüssel" in reason and "Konsolenzugang" in reason
