"""Host-Bindung über registrierte Router, Aktionsfreigaben und echte SSH-Handles."""

import time
from contextlib import nullcontext

import pytest
import test_ssh_api_access as api_fixtures
from fastapi import APIRouter, HTTPException
from nodvard_deck.core import gate
from nodvard_deck.db.session import session_scope
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import Action
from nodvard_sdk import ActionRequest, ActionResult, Actor, Risk
from nodvard_sdk.capabilities import ActionExecutor
from nodvard_sdk.errors import ActionBlocked

extension_imports = api_fixtures.extension_imports
ssh_api = api_fixtures.ssh_api
docker_api = api_fixtures.docker_api
shield_api = api_fixtures.shield_api

OPERATIONS = ("run", "run_argv", "stream", "stream_argv", "shell", "sftp")


async def _invoke(exec_handle, host, operation):
    if operation == "run":
        await exec_handle.run(host, "uname -r")
    elif operation == "run_argv":
        await exec_handle.run_argv(host, ["uname", "-r"])
    elif operation == "stream":
        async with exec_handle.stream(host, "uname -r") as chunks:
            assert b"".join([chunk async for chunk in chunks])
    elif operation == "stream_argv":
        async with exec_handle.stream_argv(host, ["uname", "-r"]) as chunks:
            assert b"".join([chunk async for chunk in chunks])
    elif operation == "shell":
        async with exec_handle.open_shell(host, cols=80, rows=24):
            pass
    else:
        async with exec_handle.sftp(host) as client:
            await client.stat("/")


def _mount(loaded, router, **kwargs):
    from nodvard_deck.main import app

    loaded.ctx.api.include_router(router, permission="hosts.execute", **kwargs)
    get_extension_runtime().mount_router(app, loaded.manifest.id, loaded.router)


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_mounted_router_precedes_catchall(ssh_api, tmp_path, method):
    from fastapi.staticfiles import StaticFiles
    from nodvard_deck.main import app

    loaded = get_extension_runtime().loaded["terminal"]
    router = APIRouter()
    app.mount("/", StaticFiles(directory=tmp_path))

    @router.api_route("/mount-check", methods=["GET", "POST"])
    async def check():
        return {"ok": True}

    _mount(loaded, router)
    response = await ssh_api.client.request(
        method, f"{loaded.manifest.api_prefix}/mount-check", headers=ssh_api.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("operation", ["run", "run_argv", "stream", "stream_argv", "sftp"])
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_query_host_limits_router_access(ssh_api, operation, method):
    loaded = get_extension_runtime().loaded["terminal"]
    host = await loaded.ctx.hosts.get(ssh_api.host.id)
    other = host.model_copy(update={"id": "other-host"})
    router = APIRouter()

    @router.api_route("/host-binding", methods=["GET", "POST"])
    async def check():
        await _invoke(loaded.ctx.exec, host, operation)
        with pytest.raises(ActionBlocked):
            await _invoke(loaded.ctx.exec, other, operation)
        with pytest.raises(ActionBlocked):
            await _invoke(get_extension_runtime().loaded["system"].ctx.exec, host, operation)
        return {"ok": True}

    _mount(loaded, router, ssh_preflight=True)
    response = await ssh_api.client.request(
        method, f"{loaded.manifest.api_prefix}/host-binding",
        params={"host_id": host.id}, headers=ssh_api.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    with pytest.raises(ActionBlocked):
        await _invoke(loaded.ctx.exec, host, operation)


@pytest.mark.parametrize("case", ["missing", "empty", "duplicate", "conflict", "path_conflict"])
async def test_router_without_unique_host_cannot_connect(ssh_api, ssh_server_stats, case):
    loaded = get_extension_runtime().loaded["terminal"]
    host = await loaded.ctx.hosts.get(ssh_api.host.id)
    router = APIRouter()

    @router.get("/unbound")
    @router.get("/unbound/{host_id}")
    async def check():
        try:
            await loaded.ctx.exec.run(host, "uname -r")
        except ActionBlocked as exc:
            raise HTTPException(status_code=409, detail=exc.rule) from exc
        return {"ok": True}

    _mount(loaded, router)
    params = {
        "missing": [], "empty": [("host_id", "")],
        "duplicate": [("host_id", host.id), ("host_id", host.id)],
        "conflict": [("host_id", host.id), ("host_id", "other-host")],
        "path_conflict": [("host_id", "other-host")],
    }[case]
    path = "/unbound" + (f"/{host.id}" if case == "path_conflict" else "")
    before = ssh_server_stats["begin_auth"]
    response = await ssh_api.client.get(loaded.manifest.api_prefix + path, params=params, headers=ssh_api.headers)
    assert response.status_code == (409 if case == "missing" else 400), response.text
    assert ssh_server_stats["begin_auth"] == before


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("with_read_access", [False, True])
async def test_hostless_action_never_authorizes_ssh(ssh_api, ssh_server_stats, operation, with_read_access):
    runtime = get_extension_runtime()
    loaded = runtime.loaded["terminal"]
    host = await loaded.ctx.hosts.get(ssh_api.host.id)

    class Executor:
        action_types = frozenset({"terminal.unbound"})

        async def execute(self, request):
            access = gate.authorized_ssh(
                host.id, operations=frozenset({"command", "shell", "sftp"}), ext_id="terminal",
            ) if with_read_access else nullcontext()
            with access:
                await _invoke(loaded.ctx.exec, host, operation)
            return ActionResult(success=True)

    runtime.capabilities.provide("terminal", ActionExecutor, Executor())
    router = APIRouter()

    @router.post("/unbound-action")
    async def propose():
        async with session_scope() as session:
            decision = await gate.propose(session, ext_id="terminal", command_field=None, request=ActionRequest(
                action_type="terminal.unbound", risk=Risk.LOW,
                proposed_by=Actor.extension("terminal"), reason="Host-Bindung prüfen",
            ))
        return {"action_id": decision.action_id}

    _mount(loaded, router)
    before = ssh_server_stats["begin_auth"]
    response = await ssh_api.client.post(loaded.manifest.api_prefix + "/unbound-action", headers=ssh_api.headers)
    assert response.status_code == 200, response.text
    action_id = response.json()["action_id"]
    async with session_scope() as session:
        action = await session.get(Action, action_id)
        proposed = action.status == "proposed"
    if proposed:
        response = await ssh_api.client.post(f"/api/v1/actions/{action_id}/approve", headers=ssh_api.headers)
        assert response.status_code == 200, response.text
    async with session_scope() as session:
        action = await session.get(Action, action_id)
        assert action.status == "failed"
        assert "Aktionsfreigabe" in action.result["error"]
    assert ssh_server_stats["begin_auth"] == before


@pytest.mark.parametrize("host_id", [None, "", " "])
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_hostless_access_does_not_connect(ssh_api, ssh_server_stats, host_id, operation):
    ctx = get_extension_runtime().loaded["terminal"].ctx
    host = await ctx.hosts.get(ssh_api.host.id)
    before = ssh_server_stats["begin_auth"]
    with gate.authorized_ssh(host_id, operations=frozenset({"command", "shell", "sftp"})), pytest.raises(ActionBlocked):
        await _invoke(ctx.exec, host, operation)
    assert ssh_server_stats["begin_auth"] == before


@pytest.mark.parametrize("restriction", ["host", "extension", "operation", "expired"])
async def test_active_action_respects_inner_access(ssh_api, ssh_server_stats, restriction):
    runtime = get_extension_runtime()
    ctx = runtime.loaded["terminal"].ctx
    host = await ctx.hosts.get(ssh_api.host.id)

    class Executor:
        action_types = frozenset({"terminal.scoped"})

        async def execute(self, request):
            with gate.authorized_ssh(
                "other-host" if restriction == "host" else host.id,
                operations=frozenset({"shell"} if restriction == "operation" else {"command"}),
                ext_id="system" if restriction == "extension" else "terminal",
                timeout_s=0 if restriction == "expired" else None,
            ), pytest.raises(ActionBlocked):
                await ctx.exec.run(host, "uname -r")
            return ActionResult(success=True)

    runtime.capabilities.provide("terminal", ActionExecutor, Executor())
    before = ssh_server_stats["begin_auth"]
    async with session_scope() as session:
        decision = await gate.propose(session, ext_id="terminal", command_field=None, request=ActionRequest(
            action_type="terminal.scoped", host_ref=host.id, risk=Risk.LOW, proposed_by=Actor.extension("terminal"),
            reason="Lesekontext prüfen",
        ))
    if decision.status.value == "proposed":
        response = await ssh_api.client.post(f"/api/v1/actions/{decision.action_id}/approve", headers=ssh_api.headers)
        assert response.status_code == 200, response.text
    async with session_scope() as session:
        assert (await session.get(Action, decision.action_id)).status == "succeeded"
    assert ssh_server_stats["begin_auth"] == before


async def test_preflight_context_does_not_replace_action_approval(ssh_api):
    runtime = get_extension_runtime()
    loaded = runtime.loaded["terminal"]
    host = await loaded.ctx.hosts.get(ssh_api.host.id)

    class Executor:
        action_types = frozenset({"terminal.mkdir"})

        async def execute(self, request):
            async with loaded.ctx.exec.sftp(host) as client:
                await client.mkdir("/approved")
            return ActionResult(success=True)

    runtime.capabilities.provide("terminal", ActionExecutor, Executor())
    router = APIRouter()

    @router.post("/hosts/{host_id}/mkdir")
    async def propose():
        async with loaded.ctx.exec.sftp(host) as client:
            assert not hasattr(client, "mkdir")
        async with session_scope() as session:
            decision = await gate.propose(session, ext_id="terminal", command_field=None, request=ActionRequest(
                action_type="terminal.mkdir", host_ref=host.id, risk=Risk.LOW, proposed_by=Actor.extension("terminal"),
                reason="Verzeichnis anlegen",
            ))
        return {"action_id": decision.action_id}

    _mount(loaded, router, ssh_preflight=True)
    response = await ssh_api.client.post(
        f"{loaded.manifest.api_prefix}/hosts/{host.id}/mkdir", headers=ssh_api.headers,
    )
    assert response.status_code == 200, response.text
    action_id = response.json()["action_id"]
    async with session_scope() as session:
        proposed = (await session.get(Action, action_id)).status == "proposed"
    if proposed:
        response = await ssh_api.client.post(f"/api/v1/actions/{action_id}/approve", headers=ssh_api.headers)
        assert response.status_code == 200, response.text
    async with session_scope() as session:
        assert (await session.get(Action, action_id)).status == "succeeded"
    assert (ssh_api.root / "approved").is_dir()


@pytest.mark.parametrize("ambiguous", [False, True])
@pytest.mark.parametrize("source", ["collector", "api"])
async def test_metrics_without_unique_owner_never_sample(ssh_api, db_session, fake_probe, tmp_path, ambiguous, source):
    from nodvard_deck.core.metrics_history import MetricsCollector, MetricsStore
    from nodvard_deck.services import hosts as hosts_service
    from nodvard_deck_ext_system import LIVE_SCRIPT
    from nodvard_sdk.capabilities import MetricsProvider

    runtime = get_extension_runtime()
    registry = runtime.capabilities
    healthy = registry.provided_by(MetricsProvider, "system")
    missing = await hosts_service.create_host(db_session, name="missing-provider", address="192.0.2.9", os_family="linux")
    await db_session.commit()
    sampled = []

    class Provider:
        async def supports(self, host):
            if host.id != missing.id:
                return False
            if not ambiguous:
                registry.clear_extension("missing")
            return True

        async def sample(self, host):
            sampled.append(host.id)
            await runtime.loaded["system"].ctx.exec.run(host, "uname -r")
            return {"cpu_percent": 1.0}

    provider = Provider()
    registry.clear_extension("system")
    registry.provide("missing", MetricsProvider, provider)
    if ambiguous:
        registry.provide("duplicate", MetricsProvider, provider)
    registry.provide("system", MetricsProvider, healthy)
    fake_probe[LIVE_SCRIPT] = (0, "@@mem\nMemTotal: 1000 kB\nMemAvailable: 400 kB\n@@end\n", "")
    if source == "api":
        response = await ssh_api.client.get(f"/api/v1/hosts/{missing.id}/metrics", headers=ssh_api.headers)
        assert response.status_code == 503, response.text
        assert response.json()["detail"] == "Kein eindeutiger MetricsProvider registriert."
    else:
        collector = MetricsCollector(
            MetricsStore(tmp_path / "metrics.db"), interval_s=30, raw_retention_s=3600, rollup_retention_s=86400,
        )
        try:
            assert await collector.collect_once() == 1
            values = collector.store.latest([missing.id, ssh_api.host.id], int(time.time()), max_age_s=60)
            assert set(values) == {ssh_api.host.id}
            assert values[ssh_api.host.id]["mem_total_bytes"][1] == 1000 * 1024
        finally:
            await collector.stop()
    assert sampled == []


async def test_fail2ban_save_uses_host_bound_preflight(shield_api, db_session, fake_probe):
    from nodvard_deck.services import settings as settings_service
    from nodvard_deck_ext_shield import fail2ban, intrusion
    from sqlalchemy import select
    from test_ext_shield_fail2ban_config import PEER, READ
    from test_ssh_api_access import _login

    await settings_service.set_global(db_session, "autonomy.max_risk", "medium")
    await db_session.commit()
    headers = await _login(
        shield_api.client, db_session, "fail2ban-manager", permissions=("soc.manage", "hosts.execute"),
    )
    body = {"bantime": 3600, "findtime": 600, "maxretry": 3, "ignoreip": ["192.168.0.0/16"], "jails": ["sshd"]}
    current = fail2ban.parse(READ, PEER)
    fields = fail2ban.settings({**body, **{key: current[key] for key in ("deck_address", "available_jails", "sshd_backend")}})
    command = fail2ban.command(fields)
    fake_probe[intrusion.DECK_ADDRESS_COMMAND] = (0, PEER + "\n", "")
    fake_probe[fail2ban.read_command()] = (0, READ, "")
    fake_probe[command] = (0, "gespeichert\n", "")
    response = await shield_api.client.post(
        f"/api/v1/ext/shield/defender/hosts/{shield_api.host.id}/fail2ban", json=body, headers=headers,
    )
    assert response.status_code == 200, response.text
    async with session_scope() as session:
        action = (await session.scalars(select(Action))).one()
        mode = await settings_service.get_global(session, "autonomy.mode")
        assert action.status == ("succeeded" if mode == "full" else "proposed")
        assert action.host_id == shield_api.host.id
        assert action.payload["command"] == command
        if mode == "full":
            assert action.result["output"] == "gespeichert\n"
    ctx = get_extension_runtime().loaded["shield"].ctx
    with pytest.raises(ActionBlocked):
        await ctx.exec.run(await ctx.hosts.get(shield_api.host.id), intrusion.DECK_ADDRESS_COMMAND)


@pytest.mark.parametrize("trigger", ["schedule", "manual"])
async def test_registered_job_has_no_implicit_ssh_access(ssh_api, trigger):
    from nodvard_deck.core.scheduler import get_scheduler_service

    runtime = get_extension_runtime()
    ctx = runtime.loaded["system"].ctx
    host = await ctx.hosts.get(ssh_api.host.id)

    class Job:
        id = "unbound-read"
        name = "Host-Bindung prüfen"
        schedule = "0 0 31 2 *"
        enabled = False
        def __init__(self):
            self.params = {}

        async def handler(self, **params):
            with pytest.raises(ActionBlocked):
                await ctx.exec.run(host, "uname -r")
            with ctx.exec.read_access(host):
                assert (await ctx.exec.run(host, "uname -r")).exit_code == 0
            with pytest.raises(ActionBlocked):
                await ctx.exec.run(host, "uname -r")

    await ctx.scheduler.register_job(Job())
    handler = runtime.scheduler.get("system", Job.id)
    status, _, error = await get_scheduler_service()._await_handler("unbound-job", "unbound-run", handler, {}, trigger, [])
    assert (status, error) == ("succeeded", None)


async def test_docker_stats_read_each_host_explicitly(docker_api, fake_probe):
    from nodvard_deck_ext_docker import commands
    from nodvard_deck_ext_docker.after_check import cmd_state

    fake_probe[commands.stats()] = (0, "web|1.50%|45.3MiB / 4GiB|1.10%\n", "")
    fake_probe[commands.restart_counts()] = (0, "/web|3\n", "")
    fake_probe[commands.info()] = (0, '{"NCPU":2,"MemTotal":4294967296,"ServerVersion":"29.8.2"}', "")
    fake_probe[commands.virtualization()] = (0, "lxc\n", "")
    fake_probe[cmd_state(["web"])] = (0, '{"name":"web","status":"running","started":"2026-11-07T10:00:00Z"}\n', "")
    response = await docker_api.client.get("/api/v1/ext/docker/stats", headers=docker_api.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    key = f"{docker_api.host.id}:web"
    assert body["data"][key]["cpu_percent"] == 1.5
    assert body["restarts"][key] == 3
    assert body["hosts"][docker_api.host.id]["virtualization"] == "lxc"
    assert body["started_at"][key] == "2026-11-07T10:00:00Z"


async def test_watch_templates_read_each_host_explicitly(shield_api, fake_probe):
    from nodvard_deck_ext_shield.watch_templates import detect_command

    fake_probe[detect_command()] = (0, "@@samba\n[uploads]\npath = /srv/uploads\n@@end\n", "")
    response = await shield_api.client.get("/api/v1/ext/shield/defender/watch-templates", headers=shield_api.headers)
    assert response.status_code == 200, response.text
    assert response.json()["errors"] == []
    assert response.json()["suggestions"][0]["path"] == "/srv/uploads"


async def test_realtime_watch_reads_and_confirms_with_own_access(shield_api, fake_probe):
    from nodvard_deck.core.scheduler import get_scheduler_service
    from nodvard_deck_ext_shield import antivirus as av
    from nodvard_deck_ext_shield import realtime as rt
    from nodvard_deck_ext_shield.models import ScanRecord
    from nodvard_deck_ext_shield.patching import load_baseline, save_baseline
    from sqlalchemy import select

    runtime = get_extension_runtime()
    ctx = runtime.loaded["shield"].ctx
    host_id = shield_api.host.id
    await save_baseline(ctx, host_id, rt.STATE_KIND, {"enabled": True})
    command = av.via_helper(["realtime-log", "0"], rt.log_command(0), version=av.HELPER_REALTIME_LOG_VERSION)
    confirm = av.via_helper(["realtime-confirm", "/tmp/eicar"], rt.confirm_command(["/tmp/eicar"]),
                            version=av.HELPER_REALTIME_LOG_VERSION)
    fake_probe[command] = (0, "@@active=yes\n@@lines=0-1\n/tmp/eicar: Eicar.Test FOUND\n@@end\n", "")
    fake_probe[confirm] = (0, "/tmp/eicar: OK\n@@rc=0\n", "")
    handler = runtime.scheduler.get("shield", "defender-watch")
    status, _, error = await get_scheduler_service()._await_handler("watch-job", "watch-run", handler, {}, "schedule", [])
    assert (status, error) == ("succeeded", None)
    async with session_scope() as session:
        record = (await session.scalars(select(ScanRecord).where(ScanRecord.kind == "watch"))).one()
        assert record.status == "clean", record.error
    assert (await load_baseline(ctx, host_id, rt.POSITION_KIND))["line"] == 1


async def test_gameserver_list_reads_status_and_join_code(ssh_api, db_session, test_settings, fake_powershell, fake_probe):
    from nodvard_deck.main import app
    from nodvard_deck.services import extensions as extensions_service

    ssh_api.host.os_family = "windows"
    await extensions_service.enable_extension(app, db_session, test_settings, "gameserver")
    await db_session.commit()
    ctx = get_extension_runtime().loaded["gameserver"].ctx
    await ctx.settings.set({"host_tag": "docker", "log_command": "cat /tmp/join-code"})
    fake_probe["cat /tmp/join-code"] = (0, "join code: ABC123\n", "")
    response = await ssh_api.client.get("/api/v1/ext/gameserver/servers", headers=ssh_api.headers)
    assert response.status_code == 200, response.text
    row = response.json()[0]
    assert row["host_id"] == ssh_api.host.id
    assert row["error"] is None
    assert row["running"] is True
    assert row["join_code"] == "ABC123"
    assert fake_powershell["scripts"]


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_hardening_routes_scope_session_access(shield_api, db_session, monkeypatch, method):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import nodvard_deck_ext_shield.ssh_hardening as hardening

    loaded = get_extension_runtime().loaded["shield"]
    inspected = []
    shield_api.host.is_managed = True
    await db_session.commit()

    @asynccontextmanager
    async def session(host, **kwargs):
        gate.require_execution(host.id, ext_id="shield")
        with pytest.raises(ActionBlocked):
            gate.require_execution("other-host", ext_id="shield")
        with pytest.raises(ActionBlocked):
            gate.require_execution(host.id, operation="shell", ext_id="shield")
        inspected.append(host.id)
        yield SimpleNamespace(key_public_key=None)

    monkeypatch.setattr(loaded.ctx.exec, "ssh_session", session)
    monkeypatch.setattr(hardening, "inspect", AsyncMock(return_value={"has_key": False}))
    response = await shield_api.client.request(
        method, f"{loaded.manifest.api_prefix}/defender/hosts/{shield_api.host.id}/ssh-hardening",
        headers=shield_api.headers, **({"json": {"mode": "on"}} if method == "POST" else {}),
    )
    assert response.status_code == (200 if method == "GET" else 400), response.text
    assert inspected == [shield_api.host.id]
    with pytest.raises(ActionBlocked):
        gate.require_execution(shield_api.host.id, ext_id="shield")
