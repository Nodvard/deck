from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncssh
import pytest
from nodvard_deck.core import gate, ssh, vault
from nodvard_deck.ext import context
from nodvard_deck.models import Host, HostCredential, KnownHostKey, Setting
from sqlalchemy import event, select


@pytest.fixture(autouse=True)
def ssh_access():
    with gate.authorized_ssh("h1", operations=frozenset({"command"})):
        yield


def target(kind="ssh_key"):
    key = asyncssh.generate_private_key("ssh-ed25519").export_private_key().decode()
    return ssh.ConnectionTarget("h1", "10.0.0.1", 22, "deck", kind, key if kind == "ssh_key" else "secret")


@asynccontextmanager
async def session_scope():
    yield object()


async def test_session_keeps_connection_and_verifies_on_a_new_one(monkeypatch):
    monkeypatch.setattr(context, "session_scope", session_scope)
    old, fresh = Mock(), Mock()
    fresh.wait_closed = AsyncMock()
    connect = AsyncMock(return_value=fresh)
    run = AsyncMock(return_value=(0, "ok", ""))
    monkeypatch.setattr(ssh, "connect", connect)
    monkeypatch.setattr(ssh, "run", run)
    handle = SimpleNamespace(_reject_denied=AsyncMock(), _perm=Mock())
    access = target()
    session = context.SshSession(handle, old, access, 10)
    assert session.public_key.startswith("ssh-ed25519 ")
    assert "PRIVATE" not in session.public_key
    await session.run("before")
    await session.verify_key()
    await session.run("rollback")
    assert [c.args[0] for c in run.call_args_list] == [old, fresh, old]
    assert connect.call_args.args[1] is access
    assert connect.call_args.kwargs == {"connect_timeout_s": 10, "tofu": False, "key_only": True}
    fresh.close.assert_called_once()
    fresh.wait_closed.assert_awaited_once()
    old.close.assert_not_called()
    assert handle._reject_denied.await_count == 2


async def test_probe_closes_failed_connection(monkeypatch):
    monkeypatch.setattr(context, "session_scope", session_scope)
    fresh = Mock(wait_closed=AsyncMock())
    monkeypatch.setattr(ssh, "connect", AsyncMock(return_value=fresh))
    monkeypatch.setattr(ssh, "run", AsyncMock(return_value=(1, "", "")))
    with pytest.raises(ssh.SshAuthError):
        await context.SshSession(Mock(), Mock(), target(), 10).verify_key()
    fresh.close.assert_called_once()


async def test_key_only_connect_disables_password_and_keyboard_auth(monkeypatch):
    monkeypatch.setattr(ssh, "_known_fingerprints", AsyncMock(return_value={}))
    calls = []

    async def connect(**kwargs):
        calls.append(kwargs)
        kwargs["client_factory"]()
        return Mock()

    monkeypatch.setattr(asyncssh, "connect", connect)
    await ssh.connect(Mock(), target(), tofu=False, key_only=True)
    assert calls[0]["preferred_auth"] == "publickey"
    assert calls[0]["password_auth"] is False and calls[0]["kbdint_auth"] is False
    assert calls[0]["known_hosts"] == ([], [], [])
    assert len(calls[0]["client_keys"]) == 1
    with pytest.raises(ssh.SshAuthError):
        await ssh.connect(Mock(), target("ssh_password"), tofu=False, key_only=True)
    assert len(calls) == 1


async def test_session_requires_permission_before_resolving(monkeypatch):
    handle = context.ExecHandle(Mock(), SimpleNamespace(ssh_connect_timeout_s=10))
    handle._perm.require.side_effect = ValueError("denied")
    handle._resolve = AsyncMock()
    with pytest.raises(ValueError, match="denied"):
        async with handle.ssh_session(SimpleNamespace(id="h1")):
            pytest.fail("Zugang trotz fehlendem Recht")
    handle._resolve.assert_not_awaited()
    handle._perm.require.assert_called_once_with("hosts.execute")


async def test_session_resolves_once_and_uses_that_pool_connection(monkeypatch):
    monkeypatch.setattr(context, "session_scope", session_scope)
    pool = SimpleNamespace(get=AsyncMock(return_value=Mock()))
    monkeypatch.setattr(ssh, "get_ssh_pool", lambda: pool)
    handle = context.ExecHandle(Mock(), Mock())
    access = target("ssh_password")
    handle._resolve = AsyncMock(return_value=(access, "c1", SimpleNamespace(ssh_connect_timeout_s=10)))
    async with handle.ssh_session(SimpleNamespace(id="h1")) as session:
        assert session.public_key is None
        assert session.username == "deck"
        assert session._conn is pool.get.return_value
    handle._resolve.assert_awaited_once()
    assert pool.get.call_args.args[1] is access


def test_session_public_keys_discard_imported_comments():
    login_key = asyncssh.generate_private_key("ssh-ed25519", comment="nico@desktop importiert")
    install_key = asyncssh.generate_private_key("ssh-ed25519", comment="anderer Kommentar")
    login = replace(target(), secret_value=login_key.export_private_key().decode())
    install = replace(target(), secret_value=install_key.export_private_key().decode())
    login_session = context.SshSession(Mock(), Mock(), login, 10)
    assert login_session.public_key == " ".join(login_key.export_public_key().decode().split()[:2])
    session = context.SshSession(Mock(), Mock(), login, 10, key_target=install)
    assert session.public_key == " ".join(login_key.export_public_key().decode().split()[:2])
    assert session.key_public_key == " ".join(install_key.export_public_key().decode().split()[:2])
    assert len(session.public_key.split()) == len(session.key_public_key.split()) == 2


@pytest.mark.parametrize("operation", ["session", "verify"])
async def test_session_releases_database_before_real_ssh(monkeypatch, db_session, local_ssh_key_server, operation):
    address, port, username, authorized = local_ssh_key_server
    key = asyncssh.generate_private_key("ssh-ed25519")
    authorized.append(key.get_fingerprint())
    access = ssh.ConnectionTarget("h1", address, port, username, "ssh_key", key.export_private_key().decode())
    report = await ssh.inspect_host_key(address, port, {})
    db_session.add_all([
        Host(id="h1", name="server", address=address),
        KnownHostKey(host_id="h1", key_type=report.key_type, fingerprint=report.fingerprint),
    ])
    await db_session.commit()
    checked_out = 0
    network_calls = 0

    def checkout(*_):
        nonlocal checked_out
        checked_out += 1

    def checkin(*_):
        nonlocal checked_out
        checked_out -= 1

    engine = db_session.bind.sync_engine
    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)
    original_connect = asyncssh.connect

    async def connect_without_database(**kwargs):
        nonlocal network_calls
        network_calls += 1
        assert checked_out == 0, "Die DB-Verbindung ist während SSH noch ausgeliehen."
        return await original_connect(**kwargs)

    monkeypatch.setattr(asyncssh, "connect", connect_without_database)
    pool = ssh.SshPool()
    monkeypatch.setattr(ssh, "get_ssh_pool", lambda: pool)
    try:
        if operation == "session":
            handle = context.ExecHandle(Mock(), Mock())
            handle._resolve = AsyncMock(return_value=(access, "c1", SimpleNamespace(ssh_connect_timeout_s=10)))
            async with handle.ssh_session(SimpleNamespace(id="h1")) as session:
                assert session.credential_id == session.key_credential_id == "c1"
                assert checked_out == 0
        else:
            await context.SshSession(Mock(), Mock(), access, 10).verify_key()
        assert network_calls == 1
        assert checked_out == 0
    finally:
        await pool.close_all()
        event.remove(engine, "checkout", checkout)
        event.remove(engine, "checkin", checkin)


async def test_detached_connect_saves_tofu_in_a_new_session(monkeypatch, db_session, local_ssh_server):
    address, port, username, password, _ = local_ssh_server
    db_session.add(Host(id="h1", name="server", address=address))
    await db_session.commit()
    scopes = []
    original_scope = ssh.session_scope

    @asynccontextmanager
    async def tracked_scope():
        async with original_scope() as session:
            scopes.append(session)
            yield session

    monkeypatch.setattr(ssh, "session_scope", tracked_scope)
    access = ssh.ConnectionTarget("h1", address, port, username, "ssh_password", password)
    conn = await ssh.connect(None, access)
    try:
        assert len(scopes) == 2 and scopes[0] is not scopes[1]
        rows = (await db_session.execute(select(KnownHostKey))).scalars().all()
        assert len(rows) == 1 and rows[0].host_id == "h1"
    finally:
        await ssh._close_quietly(conn)


@pytest.mark.parametrize("change", ["forgotten", "target"])
async def test_detached_tofu_rechecks_trust_before_saving(monkeypatch, db_session, local_ssh_server, change):
    address, port, username, password, _ = local_ssh_server
    db_session.add(Host(id="h1", name="server", address=address))
    await db_session.commit()
    pool = ssh.SshPool()
    monkeypatch.setattr(ssh, "get_ssh_pool", lambda: pool)
    original_connect = asyncssh.connect
    connections = []

    async def change_during_connect(**kwargs):
        if change == "forgotten":
            async with ssh.session_scope() as session:
                session.add(Setting(
                    key=ssh.HOST_KEY_CONFIRM_REQUIRED, scope=ssh.HOST_KEY_CONFIRM_SCOPE,
                    user_id="h1", value={"value": True},
                ))
        else:
            await pool.drop_host("h1")
        conn = await original_connect(**kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(asyncssh, "connect", change_during_connect)
    access = ssh.ConnectionTarget("h1", address, port, username, "ssh_password", password, generation=0)
    error = ssh.HostKeyUnknown if change == "forgotten" else ssh.SshTargetChanged
    with pytest.raises(error):
        await ssh.connect(None, access)
    assert connections[0].is_closed()
    assert (await db_session.execute(select(KnownHostKey))).scalars().all() == []


async def test_password_login_can_prepare_a_different_key(monkeypatch):
    pool = SimpleNamespace(get=AsyncMock(return_value=Mock()))
    monkeypatch.setattr(ssh, "get_ssh_pool", lambda: pool)
    password, install = target("ssh_password"), target()
    settings = SimpleNamespace(ssh_connect_timeout_s=10)
    handle = context.ExecHandle(Mock(), settings)
    handle._resolve = AsyncMock(side_effect=[(password, "password", settings), (install, "key", settings)])
    fresh = Mock(wait_closed=AsyncMock())
    connect = AsyncMock(return_value=fresh)
    monkeypatch.setattr(ssh, "connect", connect)
    monkeypatch.setattr(ssh, "run", AsyncMock(return_value=(0, "", "")))
    async with handle.ssh_session(SimpleNamespace(id="h1"), credential_id="password", key_credential_id="key") as session:
        assert session.public_key is None
        assert session.key_public_key.startswith("ssh-ed25519 ")
        assert session.credential_id == "password" and session.key_credential_id == "key"
        assert pool.get.call_args.args == (None, password)
        await session.verify_key()
    assert connect.call_args.args == (None, install)
    assert connect.call_args.kwargs["key_only"] is True
    assert [call.kwargs for call in handle._resolve.await_args_list] == [
        {"credential_id": "password"}, {"credential_id": "key"},
    ]


async def test_selected_password_works_when_default_key_is_not_authorized(db_session, test_settings, local_ssh_server):
    address, port, username, password, _ = local_ssh_server
    key = asyncssh.generate_private_key("ssh-ed25519", comment="importierter Schlüssel")
    keyring = vault.load_keyring(test_settings)
    key_secret = await vault.create_secret(
        db_session, keyring, label="server-key", kind="ssh_private_key", plaintext=key.export_private_key().decode(),
    )
    password_secret = await vault.create_secret(
        db_session, keyring, label="server-password", kind="ssh_password", plaintext=password,
    )
    db_session.add_all([
        Host(id="h1", name="server", address=address),
        HostCredential(
            id="key", host_id="h1", kind="ssh_key", username=username, port=port,
            secret_id=key_secret.id, is_default=True,
        ),
        HostCredential(
            id="password", host_id="h1", kind="ssh_password", username=username, port=port,
            secret_id=password_secret.id, is_default=False,
        ),
    ])
    await db_session.commit()
    host = SimpleNamespace(id="h1", name="server", display_name="server")
    handle = context.ExecHandle(context._PermissionChecker("test", ["hosts.execute"]), test_settings)
    try:
        async with handle.ssh_session(host, credential_id="password", key_credential_id="key") as session:
            assert session.public_key is None
            assert session.key_public_key == " ".join(key.export_public_key().decode().split()[:2])
            assert session.credential_id == "password" and session.key_credential_id == "key"
            assert (await session.run("echo ready")).exit_code == 0
            with pytest.raises(ssh.SshAuthError):
                await session.verify_key()
            assert (await session.run("echo rollback")).exit_code == 0
    finally:
        await ssh.reset_ssh_pool()


@pytest.mark.parametrize("credential_id", ["missing", "foreign", "token"])
async def test_selected_login_must_belong_to_host(db_session, test_settings, credential_id):
    secret = await vault.create_secret(
        db_session, vault.load_keyring(test_settings), label="unused", kind="generic", plaintext="unused",
    )
    db_session.add_all([
        Host(id="h1", name="server", address="127.0.0.1"),
        Host(id="h2", name="other", address="127.0.0.2"),
        HostCredential(id="foreign", host_id="h2", kind="ssh_key", secret_id=secret.id),
        HostCredential(id="token", host_id="h1", kind="api_token", secret_id=secret.id),
    ])
    await db_session.commit()
    handle = context.ExecHandle(context._PermissionChecker("test", ["hosts.execute"]), test_settings)
    with pytest.raises(context.HostUnreachable, match="gewählte SSH-Zugang"):
        async with handle.ssh_session(SimpleNamespace(id="h1"), credential_id=credential_id):
            pytest.fail("Fremder oder ungültiger Zugang wurde angenommen.")


@pytest.mark.parametrize("change", [{"username": "other"}, {"port": 2222}, {"kind": "ssh_password"}])
async def test_incompatible_install_key_is_rejected_before_connect(monkeypatch, change):
    pool = SimpleNamespace(get=AsyncMock())
    monkeypatch.setattr(ssh, "get_ssh_pool", lambda: pool)
    settings = SimpleNamespace(ssh_connect_timeout_s=10)
    handle = context.ExecHandle(Mock(), settings)
    handle._resolve = AsyncMock(side_effect=[
        (target("ssh_password"), "password", settings), (replace(target(), **change), "key", settings),
    ])
    with pytest.raises(ValueError):
        async with handle.ssh_session(SimpleNamespace(id="h1"), credential_id="password", key_credential_id="key"):
            pytest.fail("Unpassender Zielschlüssel wurde angenommen.")
    pool.get.assert_not_awaited()
