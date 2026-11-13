"""SSH-Handles prüfen Aktionsfreigaben und begrenzte API-Zugriffe vor jeder Verbindung."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncssh
import pytest
from exec_gate_helpers import execute_approved
from nodvard_deck.core import gate, ssh
from nodvard_deck.ext.context import ApiHandle, CapabilitiesHandle, ExecHandle, _PermissionChecker
from nodvard_deck.ext.runtime import ExtensionRuntime
from nodvard_deck.services import settings as settings_service
from nodvard_sdk import ActionRequest, ActionResult, Actor, GateOutcome, Host, Risk
from nodvard_sdk.errors import ActionBlocked, InvalidRegistration, PermissionDenied

OPERATIONS = ("run", "run_argv", "stream", "stream_argv", "shell", "shell_command", "sftp")
HOST = Host(id="host-1", name="server", display_name="Server", address="192.0.2.1")


async def _invoke(handle, operation, host=HOST):
    if operation == "run":
        return await handle.run(host, "touch /tmp/freigabe")
    if operation == "run_argv":
        return await handle.run_argv(host, ["touch", "/tmp/freigabe"], stdin=b"daten")
    if operation == "stream":
        cm = handle.stream(host, "touch /tmp/freigabe")
    elif operation == "stream_argv":
        cm = handle.stream_argv(host, ["touch", "/tmp/freigabe"])
    elif operation == "sftp":
        cm = handle.sftp(host)
    else:
        command = "touch /tmp/freigabe" if operation == "shell_command" else None
        cm = handle.open_shell(host, cols=80, rows=24, command=command)
    async with cm:
        pass


@pytest.fixture
def handle(monkeypatch):
    handle = ExecHandle(_PermissionChecker("test-ext", ["hosts.execute"]), settings=None)
    handle._connection = AsyncMock(return_value=object())
    monkeypatch.setattr(ssh, "run", AsyncMock(return_value=(0, "fertig", "")))

    @asynccontextmanager
    async def process(*args, **kwargs):
        yield SimpleNamespace()

    monkeypatch.setattr(ssh, "open_shell", process)
    monkeypatch.setattr(ssh, "open_command_stream", process)
    monkeypatch.setattr(ssh, "start_sftp", AsyncMock(return_value=SimpleNamespace(
        exit=Mock(), wait_closed=AsyncMock(),
        remove=AsyncMock(), rename=AsyncMock(), mkdir=AsyncMock(),
        chmod=AsyncMock(), put=AsyncMock(), open56=AsyncMock(),
    )))
    return handle


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_direct_ssh_calls_require_gate_approval_before_connecting(handle, operation):
    with pytest.raises(ActionBlocked) as raised:
        await _invoke(handle, operation)
    assert raised.value.rule == "action_gate"
    handle._connection.assert_not_awaited()


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_manifest_permission_is_still_required(handle, operation):
    handle._perm = _PermissionChecker("test-ext", [])
    with pytest.raises(PermissionDenied):
        await _invoke(handle, operation)
    handle._connection.assert_not_awaited()


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_approved_action_can_use_ssh_and_approval_ends_with_execution(db_session, handle, operation):
    await execute_approved(db_session, lambda: _invoke(handle, operation), host_id=HOST.id)
    handle._connection.assert_awaited_once_with(HOST)
    handle._connection.reset_mock()
    with pytest.raises(ActionBlocked):
        await _invoke(handle, operation)
    handle._connection.assert_not_awaited()


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_approval_for_another_host_does_not_allow_ssh(db_session, handle, operation):
    async def execute():
        with pytest.raises(ActionBlocked) as raised:
            await _invoke(handle, operation)
        assert raised.value.rule == "action_gate"

    await execute_approved(db_session, execute, host_id="another-host")
    handle._connection.assert_not_awaited()


@pytest.mark.parametrize("operation", ("stream", "stream_argv", "shell", "sftp"))
async def test_deferred_context_checks_approval_when_entered(db_session, handle, operation):
    async def prepare():
        if operation == "stream":
            return handle.stream(HOST, "touch /tmp/freigabe")
        if operation == "stream_argv":
            return handle.stream_argv(HOST, ["touch", "/tmp/freigabe"])
        if operation == "shell":
            return handle.open_shell(HOST, cols=80, rows=24)
        return handle.sftp(HOST)

    cm = await execute_approved(db_session, prepare, host_id=HOST.id)
    with pytest.raises(ActionBlocked) as raised:
        async with cm:
            pass
    assert raised.value.rule == "action_gate"
    handle._connection.assert_not_awaited()


@pytest.mark.parametrize("mode,risk,command,outcome", [
    ("propose", Risk.LOW, "touch /tmp/freigabe", GateOutcome.REQUIRE_CONFIRMATION),
    ("full", Risk.HIGH, "touch /tmp/freigabe", GateOutcome.REQUIRE_CONFIRMATION),
    ("full", Risk.LOW, "rm -rf /", GateOutcome.DENY),
    ("full", Risk.LOW, "touch /tmp/freigabe", GateOutcome.ALLOW),
])
async def test_gate_decision_controls_execution(db_session, monkeypatch, handle, mode, risk, command, outcome):
    await settings_service.set_global(db_session, "autonomy.mode", mode)
    await settings_service.set_global(db_session, "autonomy.max_risk", "low")

    class Executor:
        async def execute(self, request):
            await handle.run(HOST, request.payload["command"])
            return ActionResult(success=True)

    monkeypatch.setattr(gate, "find_executor", AsyncMock(return_value=Executor()))
    decision = await gate.propose(
        db_session, ext_id="test-ext", command_field="command",
        request=ActionRequest(
            action_type="test.exec", host_ref=HOST.id, payload={"command": command}, risk=risk,
            proposed_by=Actor.extension("test-ext"), reason="Freigabe prüfen",
        ),
    )
    assert decision.outcome == outcome
    if outcome == GateOutcome.ALLOW:
        assert decision.status.value == "succeeded"
        handle._connection.assert_awaited_once_with(HOST)
    else:
        handle._connection.assert_not_awaited()
        with pytest.raises(ActionBlocked) as raised:
            await handle.run(HOST, "touch /tmp/anderer-aufruf")
        assert raised.value.rule == "action_gate"


@pytest.mark.parametrize("fail", [False, True])
async def test_inherited_approval_expires_even_when_executor_fails(db_session, handle, fail):
    resume = asyncio.Event()
    tasks = []

    async def delayed():
        await resume.wait()
        await _invoke(handle, "sftp")

    async def execute():
        tasks.append(asyncio.create_task(delayed()))
        if fail:
            raise ValueError("Ausführung fehlgeschlagen")

    try:
        if fail:
            with pytest.raises(ValueError, match="Ausführung fehlgeschlagen"):
                await execute_approved(db_session, execute, host_id=HOST.id)
        else:
            await execute_approved(db_session, execute, host_id=HOST.id)
    finally:
        resume.set()
        if tasks:
            with pytest.raises(ActionBlocked):
                await tasks[0]
    handle._connection.assert_not_awaited()


@pytest.mark.parametrize("operation,access", [
    ("run", "command"), ("run_argv", "command"), ("stream", "command"),
    ("stream_argv", "command"), ("shell", "shell"), ("shell_command", "shell"), ("sftp", "sftp"),
])
async def test_api_authorization_allows_only_its_operation_and_host(handle, operation, access):
    with gate.authorized_ssh(HOST.id, operations=frozenset({access}), ext_id="test-ext"):
        await _invoke(handle, operation)
        handle._connection.assert_awaited_once_with(HOST)
        handle._connection.reset_mock()
        with pytest.raises(ActionBlocked):
            await _invoke(handle, operation, HOST.model_copy(update={"id": "another-host"}))
        handle._connection.assert_not_awaited()
    with pytest.raises(ActionBlocked):
        await _invoke(handle, operation)


@pytest.mark.parametrize("operation", ("shell", "sftp"))
async def test_read_access_does_not_authorize_shell_or_sftp(handle, operation):
    with (
        gate.authorized_ssh(HOST.id, operations=frozenset({"command"}), ext_id="test-ext"),
        pytest.raises(ActionBlocked),
    ):
        await _invoke(handle, operation)
    handle._connection.assert_not_awaited()


async def test_read_access_is_bound_to_extension(handle):
    with (
        gate.authorized_ssh(HOST.id, operations=frozenset({"command"}), ext_id="other-ext"),
        pytest.raises(ActionBlocked),
    ):
        await _invoke(handle, "run")
    handle._connection.assert_not_awaited()


def _service_catalog(callback, *, requires=("test-ext",)):
    from nodvard_sdk.capabilities import ServiceCatalog

    class Catalog:
        async def list_services(self):
            return await callback()

    runtime = ExtensionRuntime()
    runtime.capabilities.provide("test-ext", ServiceCatalog, Catalog())
    return CapabilitiesHandle(runtime, "consumer", list(requires))


async def test_service_catalog_preserves_provider_equality():
    from nodvard_sdk.capabilities import ServiceCatalog

    consumer = _service_catalog(AsyncMock(return_value=[]))
    original = consumer._runtime.capabilities.query(ServiceCatalog)[0]
    catalogs = await consumer.query(ServiceCatalog)
    again = await consumer.query(ServiceCatalog)

    assert catalogs == [original]
    assert [original] == catalogs
    assert catalogs == again
    assert catalogs != [object()]
    assert catalogs != await _service_catalog(AsyncMock(return_value=[])).query(ServiceCatalog)
    assert hash(catalogs[0]) == hash(original)
    assert {catalogs[0], again[0], original} == {original}
    assert isinstance(catalogs[0], ServiceCatalog)


async def test_service_catalog_delegates_only_provider_commands(handle):
    from nodvard_sdk.capabilities import ServiceCatalog

    other = ExecHandle(_PermissionChecker("other-ext", ["hosts.execute"]), settings=None)
    other._connection = AsyncMock()

    async def list_services():
        result = await handle.run(HOST, "uname -r")
        with pytest.raises(ActionBlocked):
            await other.run(HOST, "uname -r")
        with pytest.raises(ActionBlocked):
            await handle.run(HOST.model_copy(update={"id": "other-host"}), "uname -r")
        for operation in ("shell", "sftp"):
            with pytest.raises(ActionBlocked):
                await _invoke(handle, operation)
        return [{"output": result.stdout}]

    consumer = _service_catalog(list_services)
    providers = await consumer.query(ServiceCatalog)
    with gate.authorized_ssh(HOST.id, operations=frozenset({"command", "sftp_read"}), ext_id="consumer"):
        assert await providers[0].list_services() == [{"output": "fertig"}]
        with pytest.raises(ActionBlocked):
            await handle.run(HOST, "uname -r")
    with pytest.raises(ActionBlocked):
        await providers[0].list_services()
    handle._connection.assert_awaited_once_with(HOST)
    other._connection.assert_not_awaited()


@pytest.mark.parametrize("source,operations", [
    ("other-consumer", {"command"}), ("consumer", {"sftp_read"}), ("consumer", {"shell"}),
])
async def test_service_catalog_does_not_delegate_unrelated_access(handle, source, operations):
    from nodvard_sdk.capabilities import ServiceCatalog

    providers = await _service_catalog(lambda: handle.run(HOST, "uname -r")).query(ServiceCatalog)
    with gate.authorized_ssh(HOST.id, operations=frozenset(operations), ext_id=source):
        with pytest.raises(ActionBlocked):
            await providers[0].list_services()
    handle._connection.assert_not_awaited()


async def test_service_catalog_still_requires_declared_provider(handle):
    from nodvard_sdk.capabilities import ServiceCatalog

    consumer = _service_catalog(lambda: handle.run(HOST, "uname -r"), requires=())
    with gate.authorized_ssh(HOST.id, operations=frozenset({"command"}), ext_id="consumer"):
        assert await consumer.query(ServiceCatalog) == []
    handle._connection.assert_not_awaited()


@pytest.mark.parametrize("revocation", ["parent_closed", "expired", "call_finished"])
async def test_service_catalog_delegation_cannot_outlive_read_access(handle, monkeypatch, revocation):
    from nodvard_sdk.capabilities import ServiceCatalog

    now = [100.0]
    monkeypatch.setattr(gate, "time", SimpleNamespace(monotonic=lambda: now[0]))
    entered, resume = asyncio.Event(), asyncio.Event()
    tasks = []

    async def delayed():
        entered.set()
        await resume.wait()
        await handle.run(HOST, "uname -r")

    async def list_services():
        if revocation == "call_finished":
            tasks.append(asyncio.create_task(delayed()))
        else:
            await delayed()
        return []

    providers = await _service_catalog(list_services).query(ServiceCatalog)
    with gate.authorized_ssh(HOST.id, operations=frozenset({"command"}), ext_id="consumer", timeout_s=10):
        task = asyncio.create_task(providers[0].list_services())
        await entered.wait()
        if revocation == "call_finished":
            assert await task == []
        elif revocation == "expired":
            now[0] = 110.0
            resume.set()
            with pytest.raises(ActionBlocked):
                await task
    resume.set()
    if revocation != "expired":
        with pytest.raises(ActionBlocked):
            await (tasks[0] if tasks else task)
    handle._connection.assert_not_awaited()


async def test_service_catalog_cannot_override_active_action_host(db_session, handle):
    from nodvard_sdk.capabilities import ServiceCatalog

    providers = await _service_catalog(lambda: handle.run(HOST, "uname -r")).query(ServiceCatalog)

    async def execute():
        with gate.authorized_ssh(HOST.id, operations=frozenset({"command"}), ext_id="consumer"):
            with pytest.raises(ActionBlocked):
                await providers[0].list_services()

    await execute_approved(db_session, execute, host_id="other-host")
    handle._connection.assert_not_awaited()


async def test_inherited_api_authorization_expires(handle):
    resume = asyncio.Event()

    async def delayed():
        await resume.wait()
        await _invoke(handle, "sftp")

    with gate.authorized_ssh(HOST.id, operations=frozenset({"sftp"})):
        task = asyncio.create_task(delayed())
    resume.set()
    with pytest.raises(ActionBlocked):
        await task
    handle._connection.assert_not_awaited()


@pytest.mark.parametrize("fail", [False, True])
async def test_inherited_action_allows_new_read_access_after_completion(db_session, handle, fail):
    handle._perm = _PermissionChecker("test-ext", ["hosts.read", "hosts.execute"])
    resume = asyncio.Event()
    tasks = []

    async def delayed():
        await resume.wait()
        with pytest.raises(ActionBlocked):
            await handle.run(HOST, "uname -r")
        with handle.read_access(HOST):
            await handle.run(HOST, "uname -r")
            with pytest.raises(ActionBlocked):
                await handle.run(HOST.model_copy(update={"id": "other-host"}), "uname -r")
            with pytest.raises(ActionBlocked):
                await _invoke(handle, "sftp")
        with pytest.raises(ActionBlocked):
            await handle.run(HOST, "uname -r")

    async def execute():
        tasks.append(asyncio.create_task(delayed()))
        if fail:
            raise ValueError("Ausführung fehlgeschlagen")

    try:
        if fail:
            with pytest.raises(ValueError, match="Ausführung fehlgeschlagen"):
                await execute_approved(db_session, execute, host_id="other-host")
        else:
            await execute_approved(db_session, execute, host_id="other-host")
    finally:
        resume.set()
        if tasks:
            await tasks[0]
    handle._connection.assert_awaited_once_with(HOST)


async def test_api_authorization_cannot_override_action_host(db_session, handle):
    async def execute():
        with gate.authorized_ssh(HOST.id, operations=frozenset({"sftp"})), pytest.raises(ActionBlocked):
            await _invoke(handle, "sftp")

    await execute_approved(db_session, execute, host_id="another-host")
    handle._connection.assert_not_awaited()


async def test_read_access_cannot_override_active_action_host(db_session, handle):
    handle._perm = _PermissionChecker("test-ext", ["hosts.read", "hosts.execute"])

    async def execute():
        with handle.read_access(HOST), pytest.raises(ActionBlocked):
            await handle.run(HOST, "uname -r")

    await execute_approved(db_session, execute, host_id="another-host")
    handle._connection.assert_not_awaited()


async def test_file_stream_authorizes_reads_and_close_without_leaking(handle):
    from nodvard_deck.api.v1.files import _authorized_source

    closed = []

    async def open_read():
        try:
            await _invoke(handle, "sftp")
            yield b"daten"
        finally:
            await _invoke(handle, "sftp")
            closed.append(True)

    source = _authorized_source(SimpleNamespace(
        host_id=HOST.id, required_permission="hosts.execute", open_read=open_read,
    ))
    stream = source.open_read()
    assert await anext(stream) == b"daten"
    with pytest.raises(ActionBlocked):
        await _invoke(handle, "sftp")
    await stream.aclose()
    assert closed == [True]


async def test_source_without_server_permission_does_not_get_ssh_access(handle):
    from nodvard_deck.api.v1.files import _authorized_source

    source = SimpleNamespace(
        host_id=HOST.id, required_permission="files.read", read=lambda: _invoke(handle, "sftp"),
    )
    with pytest.raises(ActionBlocked):
        await _authorized_source(source).read()
    handle._connection.assert_not_awaited()


@pytest.mark.parametrize("mode", ["w", "wb", "a", "r+", "x", asyncssh.FXF_WRITE])
async def test_sftp_read_authorization_rejects_write_modes(handle, mode):
    with gate.authorized_ssh(HOST.id, operations=frozenset({"sftp_read"}), ext_id="test-ext"):
        async with handle.sftp(HOST) as client:
            with pytest.raises(ActionBlocked) as raised:
                client.open("/compose.yaml", mode)
            assert raised.value.rule == "action_gate"


@pytest.mark.parametrize("operation", ["remove", "rename", "mkdir", "chmod", "put", "open56"])
async def test_sftp_read_authorization_exposes_no_write_methods(handle, operation):
    assert callable(getattr(ssh.start_sftp.return_value, operation))
    with gate.authorized_ssh(HOST.id, operations=frozenset({"sftp_read"}), ext_id="test-ext"):
        async with handle.sftp(HOST) as client:
            assert not hasattr(client, operation)


@pytest.mark.parametrize("cause", ["normal", "timeout", "cancelled"])
async def test_sftp_close_is_bounded_and_preserves_cancellation(handle, monkeypatch, cause):
    from nodvard_deck.ext import context as context_module

    monkeypatch.setattr(context_module, "_SFTP_CLOSE_TIMEOUT_S", 0.01, raising=False)
    client = ssh.start_sftp.return_value
    client.wait_closed.side_effect = asyncio.Event().wait
    entered = asyncio.Event()

    async def use_sftp():
        with gate.authorized_ssh(HOST.id, operations=frozenset({"sftp"}), ext_id="test-ext"):
            async with asyncio.timeout(0 if cause == "timeout" else None):
                async with handle.sftp(HOST):
                    entered.set()
                    if cause != "normal":
                        await asyncio.Event().wait()

    task = asyncio.create_task(use_sftp())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if cause == "cancelled":
            task.cancel()
        done, _ = await asyncio.wait({task}, timeout=1)
        assert task in done
        if cause == "normal":
            await task
        else:
            with pytest.raises(TimeoutError if cause == "timeout" else asyncio.CancelledError):
                await task
        client.exit.assert_called_once_with()
        client.wait_closed.assert_awaited_once_with()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("permission,public", [(None, False), ("hosts.read", False), ("hosts.execute", True)])
def test_ssh_preflight_requires_protected_execute_routes(permission, public):
    from fastapi import APIRouter

    api = ApiHandle(SimpleNamespace(), _PermissionChecker("test-ext", []))
    with pytest.raises(InvalidRegistration, match="hosts.execute"):
        api.include_router(APIRouter(), permission=permission, public=public, ssh_preflight=True)


@pytest.mark.parametrize("permission,public", [(None, False), ("soc.read", True)])
def test_explicit_ssh_reads_require_protected_routes(permission, public):
    from fastapi import APIRouter

    api = ApiHandle(SimpleNamespace(), _PermissionChecker("test-ext", []))
    with pytest.raises(InvalidRegistration, match="Berechtigung"):
        api.include_router(APIRouter(), permission=permission, public=public, ssh_read=True)


async def test_background_read_access_expires_and_is_host_bound(handle, monkeypatch):
    handle._perm = _PermissionChecker("test-ext", ["hosts.read", "hosts.execute"])
    now = [100.0]
    monkeypatch.setattr(gate, "time", SimpleNamespace(monotonic=lambda: now[0]))
    with handle.read_access(HOST, timeout_s=15):
        await handle.run(HOST, "uname -r")
        with pytest.raises(ActionBlocked):
            await handle.run(HOST.model_copy(update={"id": "another-host"}), "uname -r")
        for operation in ("shell", "sftp"):
            with pytest.raises(ActionBlocked):
                await _invoke(handle, operation)
        now[0] = 115.0
        with pytest.raises(ActionBlocked):
            await handle.run(HOST, "uname -r")
    handle._connection.assert_awaited_once_with(HOST)
    with pytest.raises(ActionBlocked):
        await handle.run(HOST, "uname -r")


async def test_background_read_access_revokes_inherited_context(handle):
    handle._perm = _PermissionChecker("test-ext", ["hosts.read", "hosts.execute"])
    resume = asyncio.Event()

    async def delayed():
        await resume.wait()
        await handle.run(HOST, "uname -r")

    with handle.read_access(HOST):
        task = asyncio.create_task(delayed())
    resume.set()
    with pytest.raises(ActionBlocked):
        await task
    handle._connection.assert_not_awaited()


@pytest.mark.parametrize("permissions", [("hosts.read",), ("hosts.execute",), ()])
def test_background_read_access_requires_manifest_permissions(handle, permissions):
    handle._perm = _PermissionChecker("test-ext", list(permissions))
    with pytest.raises(PermissionDenied), handle.read_access(HOST):
        pass


@pytest.mark.parametrize("timeout_s", [0, -1, 61, float("inf"), float("nan")])
def test_background_read_access_has_bounded_duration(handle, timeout_s):
    handle._perm = _PermissionChecker("test-ext", ["hosts.read", "hosts.execute"])
    with pytest.raises(ValueError), handle.read_access(HOST, timeout_s=timeout_s):
        pass


@pytest.fixture
def session_handle(handle, monkeypatch):
    target = ssh.ConnectionTarget(HOST.id, HOST.address, 22, "deck", "ssh_password", "secret")
    handle._resolve = AsyncMock(return_value=(target, "c1", SimpleNamespace(ssh_connect_timeout_s=10)))
    pool = SimpleNamespace(get=AsyncMock(return_value=object()))
    monkeypatch.setattr(ssh, "get_ssh_pool", lambda: pool)
    fresh = Mock(wait_closed=AsyncMock())
    monkeypatch.setattr(ssh, "connect", AsyncMock(return_value=fresh))
    return handle, pool


async def test_ssh_session_requires_approval_before_resolving(session_handle):
    handle, pool = session_handle
    with pytest.raises(ActionBlocked):
        async with handle.ssh_session(HOST):
            pytest.fail("Sitzung ohne Freigabe")
    handle._resolve.assert_not_awaited()
    pool.get.assert_not_awaited()


async def test_approved_action_can_run_and_verify_session(db_session, session_handle):
    handle, pool = session_handle

    async def execute():
        async with handle.ssh_session(HOST, credential_id="c1") as session:
            assert (await session.run("true")).exit_code == 0
            await session.verify_key()

    await execute_approved(db_session, execute, host_id=HOST.id)
    handle._resolve.assert_awaited_once_with(HOST, credential_id="c1")
    pool.get.assert_awaited_once()
    ssh.connect.assert_awaited_once()


@pytest.mark.parametrize("operation", ["run", "verify_key"])
@pytest.mark.parametrize("revocation", ["closed", "expired", "host", "extension"])
async def test_session_rechecks_access_before_network(session_handle, monkeypatch, operation, revocation):
    handle, _ = session_handle
    now = [100.0]
    monkeypatch.setattr(gate, "time", SimpleNamespace(monotonic=lambda: now[0]))
    with gate.authorized_ssh(HOST.id, operations=frozenset({"command"}), ext_id="test-ext", timeout_s=10):
        async with handle.ssh_session(HOST) as session:
            assert (await session.run("true")).exit_code == 0
    ssh.run.reset_mock()
    host_id = "other-host" if revocation == "host" else HOST.id
    ext_id = "other-ext" if revocation == "extension" else "test-ext"
    with gate.authorized_ssh(host_id, operations=frozenset({"command"}), ext_id=ext_id, timeout_s=10):
        if revocation == "expired":
            now[0] += 10
        if revocation != "closed":
            with pytest.raises(ActionBlocked):
                await getattr(session, operation)(*(["true"] if operation == "run" else []))
    if revocation == "closed":
        with pytest.raises(ActionBlocked):
            await getattr(session, operation)(*(["true"] if operation == "run" else []))
    ssh.run.assert_not_awaited()
    ssh.connect.assert_not_awaited()


@pytest.mark.parametrize("host_id,ext_id", [("other-host", "test-ext"), (HOST.id, "other-ext")])
async def test_session_opening_checks_host_and_extension(session_handle, host_id, ext_id):
    handle, pool = session_handle
    with gate.authorized_ssh(host_id, operations=frozenset({"command"}), ext_id=ext_id):
        with pytest.raises(ActionBlocked):
            async with handle.ssh_session(HOST):
                pytest.fail("Sitzung für fremden Kontext")
    handle._resolve.assert_not_awaited()
    pool.get.assert_not_awaited()
