"""SSH-Zugriffe über die autorisierten System-, Terminal- und Datei-APIs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from nodvard_deck.core import security
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import Action, Role, RolePermission, User
from nodvard_deck.services import auth as auth_service
from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services import hosts as hosts_service
from nodvard_deck.services import settings as settings_service
from sqlalchemy import select

PASSWORD = "test-password-123"
EXTENSIONS = Path(__file__).resolve().parents[2] / "extensions"


@pytest.fixture
def extension_imports(monkeypatch):
    ext_ids = ("system", "terminal", "docker", "shield", "inventory", "npm", "gameserver")
    for ext_id in ext_ids:
        monkeypatch.syspath_prepend(str(EXTENSIONS / ext_id / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith(tuple(f"nodvard_deck_ext_{ext_id}" for ext_id in ext_ids)):
            del sys.modules[name]


async def _login(client, db_session, name, *, permissions=None):
    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=name, password_hash=security.hash_password(PASSWORD), is_active=True)
    if permissions is None:
        user.roles.append(roles["admin"])
    else:
        user.roles.append(Role(
            name=f"rolle-{name}", permissions=[RolePermission(permission=p) for p in permissions],
        ))
    db_session.add(user)
    await db_session.commit()
    response = await client.post("/api/v1/auth/login", json={"username": name, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(params=["propose", "full"])
async def ssh_api(request, running_app, db_session, test_settings, local_ssh_server, extension_imports):
    from nodvard_deck.main import app

    address, port, username, password, root = local_ssh_server
    host = await hosts_service.create_host(db_session, name="server", address=address, os_family="linux", tags=["docker"])
    await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username=username, port=port, secret_value=password,
    )
    await settings_service.set_global(db_session, "autonomy.mode", request.param)
    await db_session.commit()
    test_settings.extensions_dir = EXTENSIONS
    await extensions_service.discover_and_sync(db_session, test_settings)
    for ext_id in ("system", "terminal"):
        await extensions_service.enable_extension(app, db_session, test_settings, ext_id)
    await db_session.commit()

    http_base, ws_base = running_app
    async with AsyncClient(base_url=http_base) as client:
        headers = await _login(client, db_session, "admin1")
        yield SimpleNamespace(client=client, headers=headers, host=host, root=root, ws_base=ws_base)


async def test_system_info_and_health_use_read_contract(ssh_api, fake_probe, db_session):
    system = sys.modules["nodvard_deck_ext_system"]
    fake_probe[system.SCRIPT] = (0, "@@os\nDebian\n@@hostname\nserver\n@@end\n", "")
    response = await ssh_api.client.get(
        f"/api/v1/ext/system/hosts/{ssh_api.host.id}/info", headers=ssh_api.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["os"] == "Debian"
    widget = await ssh_api.client.get("/api/v1/ext/system/widgets/health", headers=ssh_api.headers)
    assert widget.status_code == 200, widget.text
    assert widget.json()["data"][0]["host_id"] == ssh_api.host.id
    assert "nicht erreichbar" not in widget.json()["data"][0]["summary"]
    assert (await db_session.execute(select(Action))).scalars().all() == []


async def test_terminal_ticket_opens_real_ssh_shell(ssh_api):
    import websockets

    response = await ssh_api.client.post(
        "/api/v1/terminal/sessions", json={"host_id": ssh_api.host.id}, headers=ssh_api.headers,
    )
    assert response.status_code == 200, response.text
    async with websockets.connect(ssh_api.ws_base + response.json()["ws_url"]) as ws:
        assert await asyncio.wait_for(ws.recv(), 3) == b"shell-ready\n"
        await ws.send(b"hallo\n")
        assert await asyncio.wait_for(ws.recv(), 3) == b"echo:hallo\n"
        await ws.send(b"exit\n")
        assert json.loads(await asyncio.wait_for(ws.recv(), 3))["type"] == "exit"


async def test_scoped_terminal_can_check_container_before_opening(ssh_api, fake_probe):
    import websockets
    from nodvard_deck_ext_docker import commands
    from nodvard_deck_ext_docker.shell import ContainerShell
    from nodvard_sdk.capabilities import ScopedTerminalTarget

    runtime = get_extension_runtime()
    runtime.capabilities.provide("terminal", ScopedTerminalTarget, ContainerShell(runtime.loaded["terminal"].ctx))
    fake_probe[commands.container_id_image("web")] = (0, "0" * 64 + "|nginx:latest\n", "")
    fake_probe[commands.shell("0" * 64)] = (0, "container-ready\n", "")
    response = await ssh_api.client.post(
        "/api/v1/terminal/sessions", headers=ssh_api.headers,
        json={"host_id": ssh_api.host.id, "scope": "container", "ref": "web"},
    )
    assert response.status_code == 200, response.text
    async with websockets.connect(ssh_api.ws_base + response.json()["ws_url"]) as ws:
        assert (await asyncio.wait_for(ws.recv(), 3)).strip() == b"container-ready"


@pytest.mark.parametrize("operation", ["list", "stat", "info", "download", "upload", "mkdir", "rename", "remove", "search"])
async def test_file_api_uses_real_sftp(ssh_api, operation):
    (ssh_api.root / "notes.txt").write_bytes(b"hallo\n")
    base = f"/api/v1/files/ssh-sftp:{ssh_api.host.id}"
    client, headers = ssh_api.client, ssh_api.headers
    if operation == "list":
        response = await client.get(f"{base}/list", params={"path": "/"}, headers=headers)
    elif operation == "stat":
        response = await client.get(f"{base}/stat", params={"path": "/notes.txt"}, headers=headers)
    elif operation == "info":
        response = await client.get(f"{base}/info", headers=headers)
    elif operation == "download":
        response = await client.get(f"{base}/download", params={"path": "/notes.txt"}, headers=headers)
    elif operation == "upload":
        response = await client.post(f"{base}/upload", params={"path": "/uploaded.txt"}, content=b"neu", headers=headers)
    elif operation == "mkdir":
        response = await client.post(f"{base}/mkdir", json={"path": "/folder"}, headers=headers)
    elif operation == "rename":
        response = await client.post(f"{base}/rename", json={"src": "/notes.txt", "dst": "/renamed.txt"}, headers=headers)
    elif operation == "remove":
        response = await client.post(f"{base}/remove", json={"path": "/notes.txt"}, headers=headers)
    else:
        response = await client.get("/api/v1/files/search", params={"q": "notes"}, headers=headers)
    assert response.status_code == 200, response.text
    if operation == "list":
        assert [item["name"] for item in response.json()["items"]] == ["notes.txt"]
    elif operation == "stat":
        assert response.json()["size"] == 6
    elif operation == "info":
        assert response.json()["healthy"] is True
    elif operation == "download":
        assert response.content == b"hallo\n"
    elif operation == "upload":
        assert (ssh_api.root / "uploaded.txt").read_bytes() == b"neu"
    elif operation == "mkdir":
        assert (ssh_api.root / "folder").is_dir()
    elif operation == "rename":
        assert (ssh_api.root / "renamed.txt").read_bytes() == b"hallo\n"
    elif operation == "remove":
        assert not (ssh_api.root / "notes.txt").exists()
    else:
        assert [hit["entry"]["name"] for hit in response.json()] == ["notes.txt"]


async def test_file_transfer_keeps_authorization_in_background(ssh_api, db_session):
    from nodvard_deck.services import jobs as jobs_service

    (ssh_api.root / "source.txt").write_bytes(b"kopie")
    source_id = f"ssh-sftp:{ssh_api.host.id}"
    response = await ssh_api.client.post(
        "/api/v1/files/transfer", headers=ssh_api.headers,
        json={"from": {"source": source_id, "path": "/source.txt"}, "to": {"source": source_id, "path": "/copy.txt"}},
    )
    assert response.status_code == 200, response.text
    for _ in range(100):
        run = await jobs_service.get_run(db_session, response.json()["run_id"])
        await db_session.refresh(run)
        if run.status in ("succeeded", "failed"):
            break
        await asyncio.sleep(0.02)
    assert run.status == "succeeded", run.error
    assert (ssh_api.root / "copy.txt").read_bytes() == b"kopie"


async def test_api_access_still_requires_server_permission(ssh_api, db_session, ssh_server_stats):
    headers = await _login(
        ssh_api.client, db_session, "files-only", permissions=("files.read", "files.write"),
    )
    base = f"/api/v1/files/ssh-sftp:{ssh_api.host.id}"
    responses = [
        await ssh_api.client.get(f"/api/v1/ext/system/hosts/{ssh_api.host.id}/info", headers=headers),
        await ssh_api.client.post("/api/v1/terminal/sessions", json={"host_id": ssh_api.host.id}, headers=headers),
        await ssh_api.client.get(f"{base}/list", headers=headers),
        await ssh_api.client.post(f"{base}/upload", params={"path": "/secret.txt"}, content=b"x", headers=headers),
    ]
    assert [response.status_code for response in responses] == [403] * 4
    assert ssh_server_stats["begin_auth"] == 0


async def test_api_authorization_does_not_escape_request(ssh_api):
    from nodvard_sdk.errors import ActionBlocked

    ctx = get_extension_runtime().loaded["terminal"].ctx
    host = await ctx.hosts.get(ssh_api.host.id)
    response = await ssh_api.client.get(f"/api/v1/files/ssh-sftp:{host.id}/list", headers=ssh_api.headers)
    assert response.status_code == 200, response.text
    with pytest.raises(ActionBlocked):
        async with ctx.exec.sftp(host):
            pass


@pytest.mark.parametrize("operation", ["run", "run_argv", "stream", "stream_argv"])
@pytest.mark.parametrize("permission,ssh_read", [("hosts.execute", False), ("soc.read", True)])
async def test_extension_read_contract_does_not_authorize_post(ssh_api, operation, permission, ssh_read):
    from fastapi import APIRouter, HTTPException
    from nodvard_deck.main import app
    from nodvard_sdk.errors import ActionBlocked

    loaded = get_extension_runtime().loaded["terminal"]
    ctx = loaded.ctx
    host = await ctx.hosts.get(ssh_api.host.id)
    router = APIRouter()

    @router.api_route("/read-contract", methods=["GET", "POST"])
    async def read_contract():
        try:
            if operation == "run":
                output = (await ctx.exec.run(host, "uname -r")).stdout
            elif operation == "run_argv":
                output = (await ctx.exec.run_argv(host, ["uname", "-r"])).stdout
            else:
                opening = ctx.exec.stream(host, "uname -r") if operation == "stream" else ctx.exec.stream_argv(host, ["uname", "-r"])
                async with opening as chunks:
                    output = b"".join([chunk async for chunk in chunks]).decode()
        except ActionBlocked as exc:
            raise HTTPException(status_code=409, detail=exc.rule) from exc
        return {"output": output}

    ctx.api.include_router(router, permission=permission, ssh_read=ssh_read)
    get_extension_runtime().mount_router(app, loaded.manifest.id, loaded.router)
    url = f"{loaded.manifest.api_prefix}/read-contract?host_id={host.id}"
    response = await ssh_api.client.get(url, headers=ssh_api.headers)
    assert response.status_code == 200, response.text
    assert response.json()["output"].replace("\r\n", "\n") == "ran:uname -r\n"
    blocked = await ssh_api.client.post(url, headers=ssh_api.headers)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"] == "action_gate"


async def test_metrics_collector_uses_real_ssh(ssh_api, fake_probe, tmp_path):
    from nodvard_deck.core.metrics_history import MetricsCollector, MetricsStore

    system = sys.modules["nodvard_deck_ext_system"]
    fake_probe[system.LIVE_SCRIPT] = (0, "@@mem\nMemTotal: 1000 kB\nMemAvailable: 400 kB\n@@end\n", "")
    store = MetricsStore(tmp_path / "metrics.db")
    collector = MetricsCollector(store, interval_s=30, raw_retention_s=3600, rollup_retention_s=86400)
    try:
        assert await collector.collect_once() == 1
        values = store.latest([ssh_api.host.id], int(time.time()), max_age_s=60)
        assert values[ssh_api.host.id]["mem_total_bytes"][1] == 1000 * 1024
    finally:
        await collector.stop()


async def test_scheduled_warnings_use_real_ssh(ssh_api, fake_probe):
    from nodvard_deck.core.scheduler import get_scheduler_service
    from nodvard_deck.db.session import session_scope
    from nodvard_deck.services import jobs as jobs_service
    from nodvard_deck_ext_system.warnung import WARN_SCRIPT

    fake_probe[WARN_SCRIPT] = (0, "@@disks\n/dev/sda1 ext4 1000 950 50 95% /\n@@thermal\n@@end\n", "")
    runtime = get_extension_runtime()
    handler = runtime.scheduler.get("system", "warnung")
    async with session_scope() as session:
        job = await jobs_service.get_job_by_key(session, ext_id="system", ext_job_key="warnung")
    lines = []
    status, _, error = await get_scheduler_service()._await_handler(job.id, "warn-test", handler, {}, "schedule", lines)
    assert (status, error) == ("succeeded", None)
    assert "'checked': 1, 'unreachable': 0, 'notified': 1" in lines[-1]


@pytest.fixture
async def docker_api(ssh_api, db_session, test_settings):
    from nodvard_deck.main import app

    await extensions_service.enable_extension(app, db_session, test_settings, "docker")
    await db_session.commit()
    return ssh_api


async def test_docker_reader_routes_use_real_ssh(docker_api, fake_docker, db_session):
    headers = await _login(docker_api.client, db_session, "reader", permissions=("hosts.read",))
    response = await docker_api.client.get("/api/v1/ext/docker/widgets/matrix", headers=headers)
    assert response.status_code == 200, response.text
    assert {item["container"] for item in response.json()["data"]} == {"web", "idle"}
    assert fake_docker["calls"]
    assert (await db_session.execute(select(Action))).scalars().all() == []


async def test_compose_file_route_uses_real_sftp(docker_api, fake_probe):
    from docker_stack_fakes import ps, row
    from nodvard_deck_ext_docker import stacks

    folder = docker_api.root / "opt" / "stacks" / "shop"
    folder.mkdir(parents=True)
    content = "services:\n  web:\n    image: nginx:1.27\n"
    (folder / "compose.yaml").write_bytes(content.encode())
    fake_probe[stacks.cmd_list("shop")] = (0, ps(row()), "")
    response = await docker_api.client.get(
        f"/api/v1/ext/docker/hosts/{docker_api.host.id}/stacks/shop/file", headers=docker_api.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["files"][0]["content"] == content
    assert response.json()["files"][0]["error"] is None


@pytest.mark.parametrize("opening", ["await", "context"])
async def test_sftp_read_file_handle_exposes_only_reads(ssh_api, opening):
    from nodvard_deck.core import gate

    (ssh_api.root / "notes.txt").write_bytes(b"hallo\n")
    ctx = get_extension_runtime().loaded["terminal"].ctx
    host = await ctx.hosts.get(ssh_api.host.id)

    async def check(handle):
        for method in ("chmod", "setstat", "chown", "utime", "truncate", "write", "write_parallel", "lock", "unlock"):
            assert not hasattr(handle, method), method
        assert (await handle.stat()).size == 6
        assert await handle.read(2) == b"ha"
        assert await handle.tell() == 2
        assert await handle.seek(0) == 0
        assert await handle.read() == b"hallo\n"
        with pytest.raises(AttributeError):
            await handle.chmod(0o600)
        with pytest.raises(AttributeError):
            await handle.setstat(None)

    with gate.authorized_ssh(host.id, operations=frozenset({"sftp_read"}), ext_id="terminal"):
        async with ctx.exec.sftp(host) as client:
            if opening == "await":
                handle = await client.open("/notes.txt", "rb")
                try:
                    await check(handle)
                finally:
                    await handle.close()
            else:
                async with client.open("/notes.txt", "rb") as handle:
                    await check(handle)
    assert (ssh_api.root / "notes.txt").read_bytes() == b"hallo\n"


async def test_npm_address_check_delegates_to_real_docker_catalog(docker_api, fake_docker, db_session, test_settings, monkeypatch):
    from nodvard_deck.main import app
    from nodvard_deck_ext_docker.capabilities import DockerServiceCatalog
    from nodvard_deck_ext_npm.service import NpmService
    from nodvard_sdk.capabilities import ServiceCatalog
    from nodvard_sdk.errors import ActionBlocked

    client = SimpleNamespace(
        base_url=f"http://{docker_api.host.address}:81",
        proxy_hosts=AsyncMock(return_value=[
            {"id": 1, "forward_host": docker_api.host.address, "forward_port": 8080, "enabled": True},
            {"id": 2, "forward_host": "idle", "forward_port": 80, "enabled": True},
        ]),
    )
    monkeypatch.setattr(NpmService, "npm_client", AsyncMock(return_value=client))
    await extensions_service.enable_extension(app, db_session, test_settings, "npm")
    await db_session.commit()
    runtime = get_extension_runtime()
    assert isinstance(runtime.capabilities.provided_by(ServiceCatalog, "docker"), DockerServiceCatalog)
    headers = await _login(docker_api.client, db_session, "npm-reader", permissions=("hosts.read",))
    response = await docker_api.client.get("/api/v1/ext/npm/address-check", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "ok"
    checks = {row["proxy_id"]: row for row in body["checks"]}
    assert (checks[1]["status"], checks[1]["container"], checks[1]["can_start"]) == ("running", "web", False)
    assert (checks[2]["status"], checks[2]["container"], checks[2]["can_start"]) == ("stopped", "idle", True)
    assert checks[1]["docker_host_id"] == checks[2]["docker_host_id"] == docker_api.host.id
    assert fake_docker["calls"]
    ctx = runtime.loaded["docker"].ctx
    with pytest.raises(ActionBlocked):
        await ctx.exec.run(await ctx.hosts.get(docker_api.host.id), "uname -r")
    assert (await db_session.execute(select(Action))).scalars().all() == []


@pytest.mark.parametrize("operation", ["check", "save"])
async def test_compose_editor_post_uses_read_preflight(docker_api, fake_probe, db_session, operation):
    from docker_stack_fakes import ps, row
    from nodvard_deck_ext_docker import editor, stacks
    from nodvard_deck_ext_docker.stack_actions import DockerStackExecutor
    from nodvard_sdk.capabilities import ActionExecutor

    stack = stacks.parse_members(ps(row()))[0]
    executor = next(
        impl for impl in get_extension_runtime().capabilities.query(ActionExecutor)
        if isinstance(impl, DockerStackExecutor)
    )
    await executor._adopted.add(docker_api.host.id, stack)
    folder = docker_api.root / "opt" / "stacks" / "shop"
    folder.mkdir(parents=True)
    old = b"services:\n  web:\n    image: nginx:1.26\n"
    (folder / "compose.yaml").write_bytes(old)
    await settings_service.set_global(db_session, "autonomy.max_risk", "low")
    await db_session.commit()
    fake_probe[stacks.cmd_list("shop")] = (0, ps(row()), "")
    fake_probe[shlex.join(editor.cmd_check(stack, 0))] = (0, json.dumps({"services": {"web": {"image": "nginx:1.27"}}}), "")
    body = {"path": "/opt/stacks/shop/compose.yaml", "content": "services:\n  web:\n    image: nginx:1.27\n"}
    if operation == "save":
        body["base_sha256"] = hashlib.sha256(old).hexdigest()
    response = await docker_api.client.post(
        f"/api/v1/ext/docker/hosts/{docker_api.host.id}/stacks/shop/{operation}", headers=docker_api.headers, json=body,
    )
    assert response.status_code == (200 if operation == "check" else 202), response.text
    if operation == "check":
        assert response.json()["ok"] is True, response.text
    else:
        action = (await db_session.execute(select(Action))).scalars().one()
        assert action.action_type == "docker.stack_save"
        assert action.status == "proposed"
    assert (folder / "compose.yaml").read_bytes() == old


async def test_image_update_post_reaches_gate_with_real_ssh(docker_api, fake_probe, db_session, monkeypatch):
    from nodvard_deck_ext_docker import image_apply as ia
    from nodvard_deck_ext_docker import image_updates as iu

    extension = get_extension_runtime().loaded["docker"].instance
    info = {
        "id": "a" * 64, "name": "/shop-web-1", "image": "nginx:1.27", "image_id": "sha256:" + "b" * 64,
        "state": "running", "labels": {
            "com.docker.compose.project": "shop", "com.docker.compose.service": "web",
            "com.docker.compose.project.working_dir": "/opt/stacks/shop",
            "com.docker.compose.project.config_files": "/opt/stacks/shop/compose.yaml",
            "com.docker.compose.config-hash": "c" * 64, "com.docker.compose.version": "2.29.1",
        },
    }
    target = ia.classify(info, own=False)
    monkeypatch.setattr(extension._images, "result_for", lambda host_id, container: {"status": iu.UPDATE, "image": "nginx:1.27"})
    await settings_service.set_global(db_session, "autonomy.max_risk", "low")
    await db_session.commit()
    fake_probe[ia.cmd_apply_inspect(["shop-web-1"])] = (0, json.dumps(info) + "\n", "")
    fake_probe[ia.cmd_probe(target)] = (0, "@@compose=2.29.1\n@@dir\n", "")
    fake_probe[ia.cmd_members(target)] = (0, "shop-web-1|running\n", "")
    fake_probe[ia.cmd_config(target)] = (0, json.dumps({"services": {"web": {"image": "nginx:1.27"}}}), "")
    fake_probe[ia.cmd_config_hash(target)] = (0, "web " + "c" * 64 + "\n", "")
    base = f"/api/v1/ext/docker/containers/{docker_api.host.id}/shop-web-1/image-update"
    plan = await docker_api.client.get(base + "/plan", headers=docker_api.headers)
    assert plan.status_code == 200, plan.text
    assert plan.json()["ok"] is True, plan.text
    response = await docker_api.client.post(base, headers=docker_api.headers, json={"plan_id": plan.json()["plan_id"]})
    assert response.status_code == 202, response.text
    action = (await db_session.execute(select(Action))).scalars().one()
    assert action.action_type == "container.image_update"
    assert action.status == "proposed"


def test_all_sdk_modules_import():
    import importlib
    import pkgutil
    import nodvard_sdk

    for module in pkgutil.walk_packages(nodvard_sdk.__path__, nodvard_sdk.__name__ + "."):
        importlib.import_module(module.name)


async def test_overview_reads_service_capability_with_real_ssh(docker_api, fake_docker, db_session):
    from nodvard_deck.api.v1 import overview

    overview._cache.update(at=0.0, value=None)
    headers = await _login(docker_api.client, db_session, "overview-reader", permissions=("hosts.read",))
    response = await docker_api.client.get("/api/v1/overview", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["services_unreachable_hosts"] == []
    assert {row["name"] for row in body["services"]} == {"web", "idle"}
    assert fake_docker["calls"]


@pytest.mark.parametrize("cache_age", [None, 11])
async def test_host_metrics_reads_capability_without_fresh_cache(ssh_api, fake_probe, db_session, cache_age):
    from nodvard_sdk.capabilities import MetricsProvider

    system = sys.modules["nodvard_deck_ext_system"]
    provider = get_extension_runtime().capabilities.provided_by(MetricsProvider, "system")
    if cache_age is not None:
        provider._cache[ssh_api.host.id] = (time.monotonic() - cache_age, {"mem_total_bytes": 1})
    fake_probe[system.LIVE_SCRIPT] = (0, "@@mem\nMemTotal: 1000 kB\nMemAvailable: 400 kB\n@@end\n", "")
    headers = await _login(ssh_api.client, db_session, "metrics-reader", permissions=("hosts.read",))
    response = await ssh_api.client.get(f"/api/v1/hosts/{ssh_api.host.id}/metrics", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["values"]["mem_total_bytes"] == 1000 * 1024


@pytest.fixture
async def shield_api(ssh_api, db_session, test_settings, monkeypatch):
    from nodvard_deck.main import app
    import nodvard_deck_ext_shield as extension

    ssh_api.shield_on_start = extension.Extension.on_start
    monkeypatch.setattr(extension.Extension, "on_start", AsyncMock())
    monkeypatch.setattr(extension.UpdateCenter, "schedule_catch_up", lambda self: None)
    conn = await db_session.connection()
    await conn.run_sync(extension.HistoryBase.metadata.create_all)
    await db_session.commit()
    await extensions_service.enable_extension(app, db_session, test_settings, "shield")
    await db_session.commit()
    return ssh_api


@pytest.mark.parametrize("operation", ["single", "all"])
async def test_file_change_acknowledgement_uses_real_ssh(shield_api, db_session, fake_probe, operation):
    from nodvard_deck.db import utcnow
    from nodvard_deck_ext_shield.guard import guard_as_root
    from nodvard_deck_ext_shield.models import SecurityEventRecord
    from nodvard_deck_ext_shield.patching import load_baseline, save_baseline
    from nodvard_sdk.errors import ActionBlocked

    loaded = get_extension_runtime().loaded["shield"]
    ctx, guard = loaded.ctx, loaded.instance._guard
    host = await ctx.hosts.get(shield_api.host.id)
    old = {"sha256": "a" * 64, "mode": "644", "owner": "root"}
    await save_baseline(ctx, host.id, "files", {"accepted": {"/etc/passwd": old}, "alerted": {"/etc/passwd": old}})
    await save_baseline(ctx, host.id, "guard", {"files_pending": ["/etc/passwd"]})
    db_session.add(SecurityEventRecord(
        id="changed-file", host_id=host.id, host_name=host.name, kind="file_changed", severity="warning",
        title="Datei geändert", detail={"changes": [{"path": "/etc/passwd"}]}, created_at=utcnow(),
    ))
    await db_session.commit()
    settings = await guard._settings()
    command = guard_as_root(settings.get("guard_watch_files") or None)
    fake_probe[command] = (0, f"@@files\n{'b' * 64} 644 root 2 11 - /etc/passwd\n@@end\n", "")
    headers = await _login(shield_api.client, db_session, "event-manager", permissions=("soc.manage",))
    route = "changed-file/acknowledge" if operation == "single" else "acknowledge-all"
    response = await shield_api.client.post(f"/api/v1/ext/shield/defender/events/{route}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == ({"ok": True} if operation == "single" else {"acknowledged": 1, "kept_open": 0})
    event = await db_session.get(SecurityEventRecord, "changed-file", populate_existing=True)
    assert event.acknowledged is True
    files = await load_baseline(ctx, host.id, "files")
    assert files["accepted"]["/etc/passwd"]["sha256"] == "b" * 64
    assert files["alerted"] == {}
    assert (await load_baseline(ctx, host.id, "guard"))["files_pending"] == []
    with pytest.raises(ActionBlocked):
        await ctx.exec.run(host, command)
    assert (await db_session.execute(select(Action))).scalars().all() == []


async def test_manual_briefing_keeps_real_host_status_in_cache(shield_api, db_session, fake_probe, monkeypatch):
    from nodvard_deck_ext_shield import antivirus as av
    from nodvard_sdk.errors import ActionBlocked

    loaded = get_extension_runtime().loaded["shield"]
    ctx, defender = loaded.ctx, loaded.instance._defender
    host = await ctx.hosts.get(shield_api.host.id)
    defender._status_cache[host.id] = (time.time(), {"reachable": False, "clamav_installed": False})
    fake_probe[av.STATUS_COMMAND] = (0, "@@clam\nClamAV 1.0.7\n@@fresh\nactive\n@@end\n", "")
    connection = AsyncMock(wraps=ctx.exec._connection)
    monkeypatch.setattr(ctx.exec, "_connection", connection)
    headers = await _login(shield_api.client, db_session, "briefing-manager", permissions=("soc.manage",))
    response = await shield_api.client.post("/api/v1/ext/shield/defender/briefing", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["body"]
    cached = await defender.host_status(host)
    assert cached["reachable"] is True
    assert cached["clamav_installed"] is True
    assert cached["clamav_version"] == "1.0.7"
    assert defender._status_cache[host.id][1] == cached
    connection.assert_awaited_once_with(host)
    with pytest.raises(ActionBlocked):
        await ctx.exec.run(host, av.STATUS_COMMAND)
    assert (await db_session.execute(select(Action))).scalars().all() == []


@pytest.mark.parametrize("kind", ["quick", "deep", "watch"])
async def test_scheduled_scan_keeps_read_access_until_result(shield_api, db_session, monkeypatch, fake_probe, kind):
    from nodvard_deck.core.scheduler import get_scheduler_service
    from nodvard_deck.db.session import session_scope
    from nodvard_deck.services import jobs as jobs_service
    from nodvard_deck_ext_shield import antivirus as av
    from nodvard_deck_ext_shield.models import ScanRecord

    mark = "@@scan-rc-0123456789abcdef="
    monkeypatch.setattr(av, "new_rc_mark", lambda: mark)
    command = "clamscan --stdout /tmp"
    monkeypatch.setattr(av, "via_helper", lambda *args, **kwargs: command)
    fake_probe[command] = (0, f"Scanned files: 7\nInfected files: 0\n{mark}0\n", "")
    runtime = get_extension_runtime()
    defender = runtime.loaded["shield"].instance._defender
    host = (await defender.target_hosts())[0]
    settings = await defender._settings()
    paths = settings.get(f"{kind}_scan_paths" if kind != "watch" else "watch_paths") or {
        "quick": av.QUICK_PATHS, "deep": av.DEEP_PATHS, "watch": av.WATCH_PATHS,
    }[kind]
    handler = runtime.scheduler.get("shield", f"defender-{kind}")
    async with session_scope() as session:
        job = await jobs_service.get_job_by_key(session, ext_id="shield", ext_job_key=f"defender-{kind}")
    status, _, error = await get_scheduler_service()._await_handler(job.id, "scan-test", handler, {}, "schedule", [])
    assert (status, error) == ("succeeded", None)
    async with session_scope() as session:
        scan = (await session.execute(select(ScanRecord).where(ScanRecord.kind == kind))).scalar_one()
        assert (scan.status, scan.files_scanned, scan.error) == ("clean", 7, None)
        assert scan.finished_at is not None
        assert scan.paths == list(paths)
    assert not defender.is_running(host.id, kind)


async def test_container_watcher_uses_own_read_access(shield_api, fake_probe):
    from nodvard_deck_ext_shield.watcher import DockerWatcher
    from nodvard_sdk.errors import ActionBlocked

    ctx = get_extension_runtime().loaded["shield"].ctx
    host = await ctx.hosts.get(shield_api.host.id)
    transition = AsyncMock()
    watcher = DockerWatcher(ctx, on_transition=transition)
    ps = "docker ps -a --format '{{.Names}}|{{.State}}|{{.Status}}'"
    inspect = "docker inspect --type container --format '{{.Name}}|{{.RestartCount}}|{{.Id}}' web"
    fake_probe[ps] = (0, "web|running|Up 1 hour\n", "")
    fake_probe[inspect] = (0, "/web|0|container-id\n", "")
    await watcher.tick()
    fake_probe[inspect] = (0, "/web|2|container-id\n", "")
    await watcher.tick()
    assert transition.await_count == 1
    assert transition.call_args.args[0].details["restarts_since_last_check"] == 2
    fake_probe[ps] = (0, "web|exited|Exited (1) 1 second ago\n", "")
    await watcher.tick()
    assert transition.await_count == 2
    with pytest.raises(ActionBlocked):
        await ctx.exec.run(host, ps)


@pytest.mark.parametrize("permission", [None, "soc.read"])
async def test_status_route_accepts_authorized_reader(shield_api, fake_probe, db_session, permission):
    from nodvard_deck_ext_shield import antivirus as av

    fake_probe[av.STATUS_COMMAND] = (0, "@@clam\nClamAV 1.0.7\n@@fresh\nactive\n@@end\n", "")
    headers = await _login(
        shield_api.client, db_session, "status-reader", permissions=None if permission is None else (permission,),
    )
    response = await shield_api.client.get("/api/v1/ext/shield/defender/overview?refresh=true", headers=headers)
    assert response.status_code == 200, response.text
    host = next(row for row in response.json()["hosts"] if row["host_id"] == shield_api.host.id)
    assert host["reachable"] is True
    assert host["clamav_installed"] is True


async def test_watch_templates_accepts_authorized_manager(shield_api, fake_probe, db_session):
    from nodvard_deck_ext_shield.watch_templates import detect_command

    fake_probe[detect_command()] = (0, "@@nextcloud\n/srv/cloud\n@@end\n", "")
    headers = await _login(shield_api.client, db_session, "template-manager", permissions=("soc.manage",))
    response = await shield_api.client.get("/api/v1/ext/shield/defender/watch-templates", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["errors"] == []
    assert [item["path"] for item in response.json()["suggestions"]] == ["/srv/cloud"]


async def test_inventory_post_uses_read_preflight(ssh_api, fake_probe, db_session, test_settings):
    from nodvard_deck.main import app
    from nodvard_deck_ext_inventory.hardware import SCAN_COMMAND

    await extensions_service.enable_extension(app, db_session, test_settings, "inventory")
    await db_session.commit()
    fake_probe[SCAN_COMMAND] = (0, "@@dmi\nsys_vendor=Example\nproduct_name=Server\n@@end\n", "")
    headers = await _login(ssh_api.client, db_session, "hardware-reader", permissions=("hosts.execute",))
    response = await ssh_api.client.post(f"/api/v1/ext/inventory/hosts/{ssh_api.host.id}/scan", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["host_id"] == ssh_api.host.id
    assert response.json()["suggestions"]
    assert (await db_session.execute(select(Action))).scalars().all() == []


@pytest.mark.parametrize("operation", ["check", "create"])
async def test_new_stack_post_reaches_gate(docker_api, fake_probe, db_session, operation):
    from nodvard_deck_ext_docker import new_stack, stacks

    root = docker_api.root / "opt" / "stacks"
    stack = new_stack.new_stack("newapp", "/opt/stacks")
    fake_probe[shlex.join(new_stack.cmd_new_check(stack, "/opt/stacks"))] = (97, "", "")
    fake_probe[shlex.join(new_stack.cmd_new_check(stack, "/"))] = (
        0, json.dumps({"services": {"web": {"image": "nginx:1.27"}}}), "",
    )
    fake_probe[stacks.cmd_list("newapp")] = (0, "", "")
    await settings_service.set_global(db_session, "autonomy.max_risk", "low")
    await db_session.commit()
    url = f"/api/v1/ext/docker/hosts/{docker_api.host.id}/new-stack"
    response = await docker_api.client.post(
        url + ("/check" if operation == "check" else ""), headers=docker_api.headers,
        json={"name": "newapp", "content": "services:\n  web:\n    image: nginx:1.27\n"},
    )
    assert response.status_code == (200 if operation == "check" else 202), response.text
    if operation == "check":
        assert response.json()["ok"] is True
        assert response.json()["conflict"] is None
    else:
        action = (await db_session.execute(select(Action))).scalars().one()
        assert (action.action_type, action.status) == ("docker.stack_create", "proposed")
    assert not (root / "newapp").exists()


@pytest.mark.parametrize("path,body", [
    ("stacks/shop/adopt", {"working_dir": "/opt/stacks/shop", "config_files": ["/opt/stacks/shop/compose.yaml"]}),
    ("stacks/shop/from-portainer", None),
    ("containers/web/to-stack", None),
    ("stacks/shop/update", {"services": ["web"]}),
    ("stacks/shop/backups/20261101-120000/delete", None),
    ("stacks/shop/delete", {"remove_folder": False}),
    ("stacks/shop/git/fetch", None),
    ("stacks/shop/git/apply", {"commit": "a" * 40}),
])
async def test_stack_post_preflights_reach_ssh_before_reporting_network_error(
    docker_api, db_session, monkeypatch, path, body,
):
    from docker_stack_fakes import ps, row
    from nodvard_deck.core import ssh
    from nodvard_deck_ext_docker import stacks
    from nodvard_deck_ext_docker.stack_actions import DockerStackExecutor
    from nodvard_sdk.capabilities import ActionExecutor

    stack = stacks.parse_members(ps(row()))[0]
    executor = next(
        impl for impl in get_extension_runtime().capabilities.query(ActionExecutor)
        if isinstance(impl, DockerStackExecutor)
    )
    await executor._adopted.add(docker_api.host.id, stack)
    base = f"/api/v1/ext/docker/hosts/{docker_api.host.id}/"
    if path.endswith("git/fetch"):
        response = await docker_api.client.put(
            base + "stacks/shop/git", headers=docker_api.headers,
            json={"url": "https://example.com/repo.git"},
        )
        assert response.status_code == 200, response.text
    run = AsyncMock(side_effect=OSError("Verbindung beim Lesen unterbrochen"))
    monkeypatch.setattr(ssh, "run", run)
    response = await docker_api.client.post(base + path, headers=docker_api.headers, json=body)
    assert response.status_code in (409, 502), response.text
    run.assert_awaited()
    assert (await db_session.execute(select(Action))).scalars().all() == []
