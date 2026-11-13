"""SSH-Freigaben über Anfrageende, Hintergrundprüfung und Wiederaufnahme hinweg."""

import asyncio
import json
import shlex
import socket
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import test_ssh_api_access as api_fixtures
from nodvard_deck.core import gate, ssh
from nodvard_deck.db.session import session_scope
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import Action
from nodvard_sdk.capabilities import FilePlaceProvider
from nodvard_sdk.errors import ActionBlocked
from sqlalchemy import select
from test_ssh_api_access import _login

docker_api = api_fixtures.docker_api
extension_imports = api_fixtures.extension_imports
shield_api = api_fixtures.shield_api
ssh_api = api_fixtures.ssh_api


async def _until(predicate):
    async with asyncio.timeout(10):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("kind", ["quick", "deep", "custom"])
async def test_manual_scan_finishes_after_response(shield_api, db_session, monkeypatch, fake_probe, kind):
    from nodvard_deck_ext_shield import antivirus as av
    from nodvard_deck_ext_shield.models import ScanRecord

    mark = "@@scan-rc-0123456789abcdef="
    command = "clamscan --stdout /tmp"
    monkeypatch.setattr(av, "new_rc_mark", lambda: mark)
    monkeypatch.setattr(av, "via_helper", lambda *args, **kwargs: command)
    fake_probe[command] = (0, f"Scanned files: 7\nInfected files: 0\n{mark}0\n", "")
    headers = await _login(shield_api.client, db_session, "scanner", permissions=("soc.manage",))
    response_done = asyncio.Event()
    original = ssh.run

    async def delayed(*args, **kwargs):
        await response_done.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(ssh, "run", delayed)
    response = await shield_api.client.post(
        "/api/v1/ext/shield/defender/scans", headers=headers,
        json={"host_ids": [shield_api.host.id], "kind": kind, "paths": ["/tmp"]},
    )
    response_done.set()
    assert response.status_code == 200, response.text
    defender = get_extension_runtime().loaded["shield"].instance._defender
    await _until(lambda: not defender.is_running(shield_api.host.id, kind))
    async with session_scope() as session:
        scan = await session.get(ScanRecord, response.json()["scans"][0])
        assert (scan.status, scan.files_scanned, scan.error) == ("clean", 7, None)


@pytest.mark.parametrize("kind", ["quick", "deep"])
async def test_cancelled_scan_releases_waiting_hosts(shield_api, monkeypatch, kind):
    from nodvard_deck.core.scheduler import get_scheduler_service
    from nodvard_deck_ext_shield import antivirus as av
    from nodvard_deck_ext_shield.models import ScanRecord

    loaded = get_extension_runtime().loaded["shield"]
    defender = loaded.instance._defender
    host = await loaded.ctx.hosts.get(shield_api.host.id)
    hosts = [host.model_copy(update={"id": f"scan-host-{i}"}) for i in range(4)]
    started = []
    slots_full = asyncio.Event()

    async def scan(host, *args):
        started.append(host.id)
        if len(started) == 3:
            slots_full.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(defender, "_scan_on_host", scan)
    monkeypatch.setattr(defender, "target_hosts", AsyncMock(return_value=hosts))
    handler = get_extension_runtime().scheduler.get("shield", f"defender-{kind}")
    scheduler = get_scheduler_service()
    task = asyncio.create_task(scheduler._await_handler("scan-job", "scan-run", handler, {}, "schedule", []))
    try:
        await asyncio.wait_for(slots_full.wait(), 10)
        assert len(defender._running) == 4
        assert scheduler.cancel_run("scan-run")
        status, _, _ = await asyncio.wait_for(task, 10)
    finally:
        if not task.done():
            scheduler.cancel_run("scan-run")
            await task
    assert status == "failed"
    assert not defender._running
    async with session_scope() as session:
        rows = (await session.execute(select(ScanRecord).where(ScanRecord.kind == kind))).scalars().all()
        assert len(rows) == 4
        assert all(row.status == "error" and row.finished_at and "Abgebrochen" in row.error for row in rows)

    monkeypatch.setattr(defender, "_scan_on_host", AsyncMock(return_value=(av.ScanResult(status="clean"), "", 0)))
    for next_kind in (kind, "watch"):
        ids = await defender.start_scans(hosts, next_kind, paths=["/tmp"], trigger="schedule", wait=True)
        assert len(ids) == 4
        async with session_scope() as session:
            rows = (await session.execute(select(ScanRecord).where(ScanRecord.id.in_(ids)))).scalars().all()
            assert all(row.status == "clean" and row.finished_at for row in rows)


@pytest.mark.parametrize("kind", ["updates", "guard"])
async def test_manual_shield_check_finishes_in_background(shield_api, db_session, fake_probe, kind):
    from nodvard_deck_ext_shield import intrusion as ix
    from nodvard_deck_ext_shield import updates as up
    from nodvard_deck_ext_shield.guard import guard_as_root
    from nodvard_deck_ext_shield.patching import load_baseline, own_container_probe

    extension = get_extension_runtime().loaded["shield"].instance
    host = await extension._ctx.hosts.get(shield_api.host.id)
    if kind == "updates":
        fake_probe[up.status_command(refresh=True)] = (
            0, "@@pm\napt\n@@list\ndocker-ce/stable 29.0 amd64 [upgradable from: 28.0]\nrc list 0\n@@end\n", "",
        )
        fake_probe[own_container_probe(socket.gethostname().strip())] = (0, "@@other\n", "")
    else:
        settings = await extension._ctx.settings.get()
        fake_probe[guard_as_root(settings.get("guard_watch_files") or ix.DEFAULT_WATCH_FILES)] = (
            0, "@@auth\n@@fail2ban\nactive\n@@ports\n@@files\n@@end\n", "",
        )
    headers = await _login(shield_api.client, db_session, "manager", permissions=("soc.manage",))
    response = await shield_api.client.post(
        f"/api/v1/ext/shield/defender/{kind}/check", headers=headers, json={"host_ids": [host.id]},
    )
    assert response.status_code == 200, response.text
    state = None
    async with asyncio.timeout(10):
        while state is None:
            state = await load_baseline(extension._ctx, host.id, kind)
            await asyncio.sleep(0.01)
    assert not state.get("error"), state
    if kind == "updates":
        assert state["deck_host"] is False


@pytest.mark.parametrize("trigger", ["manual", "schedule"])
async def test_cve_check_uses_real_ssh(shield_api, db_session, fake_probe, monkeypatch, trigger):
    from nodvard_deck.core.scheduler import get_scheduler_service
    from nodvard_deck_ext_shield import cve
    from nodvard_deck_ext_shield.patching import load_baseline

    loaded = get_extension_runtime().loaded["shield"]
    ctx, service = loaded.ctx, loaded.instance._checks
    host = await ctx.hosts.get(shield_api.host.id)
    settings = await ctx.settings.get()
    monkeypatch.setattr(ctx.settings, "get", AsyncMock(return_value={**settings, "external_checks_enabled": False}))
    monkeypatch.setattr(loaded.instance._defender, "target_hosts", AsyncMock(return_value=[host]))
    fake_probe[cve.inventory_command()] = (
        0, '@@os\nID=debian\nVERSION_ID=12\nVERSION_CODENAME=bookworm\n@@packages\n'
        'ii \tlibssl3\t3.0.11-1\topenssl\t3.0.11-1\tamd64\n@@end\n', "",
    )
    data = {"openssl": {"CVE-2026-1001": {"releases": {"bookworm": {"status": "open", "urgency": "high"}}}}}
    download = AsyncMock(return_value=SimpleNamespace(status_code=200, content=json.dumps(data).encode()))
    monkeypatch.setattr(ctx.http, "get", download)
    with pytest.raises(ActionBlocked):
        await ctx.exec.run(host, cve.inventory_command())

    if trigger == "manual":
        headers = await _login(shield_api.client, db_session, "cve-manager", permissions=("soc.manage",))
        response = await shield_api.client.post(
            f"/api/v1/ext/shield/defender/checks/{host.id}/run", headers=headers,
        )
        assert response.status_code == 202, response.text
        await _until(lambda: not service._tasks)
    else:
        handler = get_extension_runtime().scheduler.get("shield", "server-checks")
        status, _, _ = await get_scheduler_service()._await_handler(
            "cve-job", "cve-run", handler, {}, "schedule", [],
        )
        assert status == "succeeded"

    result = await load_baseline(ctx, host.id, "checks")
    assert result["cve"]["status"] == "ok", result
    assert result["cve"]["error"] is None
    assert result["cve"]["counts"] == {"high": 1, "critical": 0}
    download.assert_awaited_once_with(cve.DEBIAN_URL, timeout=60.0, follow_redirects=False)
    with pytest.raises(ActionBlocked):
        await ctx.exec.run(host, cve.inventory_command())


async def test_manual_realtime_preflight_reaches_gate(shield_api, db_session, fake_probe):
    from nodvard_deck_ext_shield import realtime as rt

    fake_probe[rt.READINESS_COMMAND] = (0, (
        "@@virt\nnone\n@@mem\nMemTotal: 8388608 kB\nMemAvailable: 4194304 kB\n@@fanotify\nyes\n"
        "@@clamd\n/usr/sbin/clamd\n@@clamonacc\n/usr/sbin/clamonacc\n@@clamd_state\ninactive\n"
        "@@clamonacc_state\ninactive\n@@root\nhelper\n@@apt\nyes\n@@end\n"
    ), "")
    headers = await _login(shield_api.client, db_session, "realtime-manager", permissions=("soc.manage",))
    response = await shield_api.client.post(
        f"/api/v1/ext/shield/defender/realtime/{shield_api.host.id}", headers=headers, json={"enable": True},
    )
    assert response.status_code == 200, response.text
    action = (await db_session.execute(select(Action))).scalars().one()
    assert action.status == "proposed"


@pytest.mark.parametrize(("seen", "unban", "refusal"), [
    ("10.0.0.9\n", False, "DECK_SELF_REFUSED"),
    ("\n", False, "DECK_ADDRESS_UNKNOWN"),
    ("10.0.0.8\n", False, None),
    ("10.0.0.9\n", True, None),
    ("\n", True, None),
])
async def test_ban_preflight_uses_scoped_deck_address(shield_api, db_session, fake_probe, seen, unban, refusal):
    from nodvard_deck.services import settings as settings_service
    from nodvard_deck_ext_shield import intrusion as ix
    from nodvard_deck_ext_shield.ids import ACTION_BAN
    from nodvard_deck_ext_shield.patching import save_baseline

    ctx = get_extension_runtime().loaded["shield"].ctx
    host = await ctx.hosts.get(shield_api.host.id)
    ip = "10.0.0.9"
    browser = "203.0.113.44"
    assert not ix.same_address(browser, ip)
    assert not ix.same_address("127.0.0.1", ip)
    await settings_service.set_global(db_session, "autonomy.mode", "propose")
    await db_session.commit()
    await save_baseline(ctx, host.id, "guard", {"fail2ban": "running", "jails": {"sshd": {"banned": []}}})
    fake_probe[ix.DECK_ADDRESS_COMMAND] = (0, seen, "")
    with pytest.raises(ActionBlocked):
        await ctx.exec.run(host, ix.DECK_ADDRESS_COMMAND)

    headers = await _login(shield_api.client, db_session, "ban-manager", permissions=("soc.manage",))
    response = await shield_api.client.post(
        f"/api/v1/ext/shield/defender/hosts/{host.id}/ban",
        headers={**headers, "X-Forwarded-For": browser}, json={"ip": ip, "jail": "sshd", "unban": unban},
    )
    actions = (await db_session.execute(select(Action))).scalars().all()
    if refusal:
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": getattr(ix, refusal)}
        assert actions == []
    else:
        assert response.status_code == 200, response.text
        assert len(actions) == 1
        action = actions[0]
        assert response.json() == {"action_id": action.id, "status": "proposed", "risk": "low"}
        assert (action.action_type, action.host_id, action.status) == (ACTION_BAN, host.id, "proposed")
        assert action.payload == {
            "ip": ip, "jail": "sshd", "unban": unban, "command": ix.ban_command(ip, "sshd", unban=unban),
        }
    with pytest.raises(ActionBlocked):
        await ctx.exec.run(host, ix.DECK_ADDRESS_COMMAND)


@pytest.mark.parametrize("late", [False, True])
async def test_manual_quarantine_moves_finding(shield_api, db_session, monkeypatch, fake_probe, late):
    from nodvard_deck_ext_shield import antivirus as av
    from nodvard_deck_ext_shield import defender as df
    from nodvard_deck_ext_shield.models import FindingRecord

    db_session.add(FindingRecord(
        id="finding1", host_id=shield_api.host.id, host_name="Server", path="/tmp/eicar",
        signature="Eicar-Test-Signature", status="detected", detected_at=datetime.now(UTC),
    ))
    await db_session.commit()
    monkeypatch.setattr(av, "via_helper", lambda args, *a, **kw: args[0] + "-test")
    monkeypatch.setattr(df, "QUARANTINE_FOLLOW_INTERVAL_S", 0.01)
    monkeypatch.setattr(df, "QUARANTINE_FOLLOW_S", 1)
    fake_probe["quarantine-test"] = (0, "@@mode=644\n", "")
    fake_probe["quarantine-state-test"] = (0, "@@state=quarantined\n@@mode=644\n", "")
    original = ssh.run

    async def run(conn, command, **kwargs):
        if command == "quarantine-test" and late:
            raise TimeoutError
        return await original(conn, command, **kwargs)

    monkeypatch.setattr(ssh, "run", run)
    headers = await _login(shield_api.client, db_session, "quarantine-manager", permissions=("soc.manage",))
    response = await shield_api.client.post(
        "/api/v1/ext/shield/defender/findings/finding1/quarantine", headers=headers,
    )
    assert response.status_code == (409 if late else 200), response.text
    defender = get_extension_runtime().loaded["shield"].instance._defender
    await _until(lambda: not defender._quarantine_follow)
    async with session_scope() as session:
        assert (await session.get(FindingRecord, "finding1")).status == "quarantined"


async def test_image_check_keeps_access_after_http_response(docker_api, monkeypatch, fake_probe):
    from nodvard_deck_ext_docker import image_updates as iu
    from nodvard_deck_ext_docker.operate import OperateExec

    extension = get_extension_runtime().loaded["docker"].instance
    assert isinstance(extension._images._ctx.exec, OperateExec)

    ids = ["a" * 64]
    fake_probe[iu.CMD_LIST_IDS] = (0, ids[0] + "\n", "")
    fake_probe[iu.cmd_inspect_containers(ids)] = (0, "", "")
    response_done = asyncio.Event()
    commands = []
    original = ssh.run

    async def delayed(conn, command, **kwargs):
        result = await original(conn, command, **kwargs)
        commands.append(command)
        if command == iu.CMD_LIST_IDS:
            await response_done.wait()
        return result

    monkeypatch.setattr(ssh, "run", delayed)
    response = await docker_api.client.post(
        "/api/v1/ext/docker/image-updates/check", params={"host_id": docker_api.host.id}, headers=docker_api.headers,
    )
    response_done.set()
    assert response.status_code == 202, response.text
    images = get_extension_runtime().loaded["docker"].instance._images
    await _until(lambda: not images._tasks)
    assert images.snapshot()["hosts"][docker_api.host.id]["error"] is None
    assert commands == [iu.CMD_LIST_IDS, iu.cmd_inspect_containers(ids)]


@pytest.mark.parametrize("home", ["/", "/home/nodvard/"])
async def test_existing_volume_backup_can_be_listed(docker_api, fake_probe, monkeypatch, home):
    from docker_stack_fakes import ps, row
    from nodvard_deck_ext_docker import stack_update, stacks
    from nodvard_deck_ext_docker.stack_actions import DockerStackExecutor
    from nodvard_deck_ext_docker.stack_backup import VolumeBackup
    from nodvard_sdk.capabilities import ActionExecutor

    base = f"{home.rstrip('/')}/nodvard-sicherungen/shop"
    folder = docker_api.root / base.lstrip("/") / "20261101-120000"
    folder.mkdir(parents=True)
    (folder / "shop_data.tar.gz").write_bytes(b"archive")
    fake_probe[stacks.cmd_list("shop")] = (0, ps(row()), "")
    fake_probe[shlex.join(stack_update.cmd_container_images(["shop-web-1"]))] = (0, json.dumps({
        "name": "/shop-web-1", "service": "web", "ref": "busybox:1.37", "image_id": "sha256:" + "a" * 64,
        "running": True, "mounts": [{"Type": "volume", "Name": "shop_data"}],
    }) + "\n", "")
    executor = next(
        impl for impl in get_extension_runtime().capabilities.query(ActionExecutor)
        if isinstance(impl, DockerStackExecutor)
    )
    await executor._adopted.add(docker_api.host.id, stacks.parse_members(ps(row()))[0])
    monkeypatch.setattr(VolumeBackup, "home", AsyncMock(return_value=(home, "1000:1000")))
    response = await docker_api.client.get(
        f"/api/v1/ext/docker/hosts/{docker_api.host.id}/stacks/shop/backups", headers=docker_api.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["error"] is None
    assert body["folder"] == f"{base}/"
    assert body["backups"][0]["folder"] == f"{base}/20261101-120000"
    assert body["backups"][0]["stamp"] == "20261101-120000"
    assert body["backups"][0]["volumes"][0]["name"] == "shop_data"


@pytest.mark.parametrize("unit", ["docker.service", "containerd.service"])
@pytest.mark.parametrize("answer", ["@@other", "@@own", "@@unknown"])
async def test_service_restart_preflight_preserves_host_protection(ssh_api, fake_probe, db_session, unit, answer):
    from nodvard_deck_ext_system.actions import own_container_probe

    fake_probe[own_container_probe(socket.gethostname().strip())] = (0, answer + "\n", "")
    response = await ssh_api.client.post(
        f"/api/v1/ext/system/hosts/{ssh_api.host.id}/services/restart", headers=ssh_api.headers, json={"unit": unit},
    )
    assert response.status_code == (200 if answer == "@@other" else 409), response.text
    actions = (await db_session.execute(select(Action))).scalars().all()
    assert len(actions) == (1 if answer == "@@other" else 0)
    if actions:
        assert actions[0].status == "proposed"


@pytest.mark.parametrize("cached", [False, True])
async def test_file_places_refreshes_docker_projects(docker_api, fake_probe, monkeypatch, cached):
    from nodvard_deck_ext_docker import commands

    provider = get_extension_runtime().capabilities.provided_by(FilePlaceProvider, "docker")
    ctx = get_extension_runtime().loaded["docker"].ctx
    host = await ctx.hosts.get(docker_api.host.id)
    system_ctx = get_extension_runtime().loaded["system"].ctx
    original = provider.places_for_host

    async def scoped(host_id):
        with pytest.raises(ActionBlocked):
            await ctx.exec.run(host.model_copy(update={"id": "other-host"}), commands.ps_all())
        with pytest.raises(ActionBlocked):
            await system_ctx.exec.run(host, commands.ps_all())
        with pytest.raises(ActionBlocked):
            async with ctx.exec.sftp(host):
                pass
        return await original(host_id)

    monkeypatch.setattr(provider, "places_for_host", scoped)
    if cached:
        provider._catalog._compose_dirs[docker_api.host.id] = (0.0, {"old": "/opt/old"})
    fake_probe[commands.ps_all()] = (0, "aabbccddeeff|web|running|Up 1 hour|nginx|shop||/opt/stacks/shop\n", "")
    response = await docker_api.client.get(
        "/api/v1/files/places", params={"host_id": docker_api.host.id}, headers=docker_api.headers,
    )
    assert response.status_code == 200, response.text
    assert any(place["path"] == "/opt/stacks/shop" for place in response.json())
    assert all(place["path"] != "/opt/old" for place in response.json())
    with pytest.raises(ActionBlocked):
        await ctx.exec.run(host, commands.ps_all())


async def test_watcher_batch_reads_crash_facts_and_logs(shield_api, fake_probe, monkeypatch):
    from nodvard_deck_ext_shield.watcher import inspect_exit_command

    extension = get_extension_runtime().loaded["shield"].instance
    watcher = extension._watcher
    ps = "docker ps -a --format '{{.Names}}|{{.State}}|{{.Status}}'"
    inspect = "docker inspect --type container --format '{{.Name}}|{{.RestartCount}}|{{.Id}}' web"
    fake_probe[ps] = (0, "web|running|Up 1 hour\n", "")
    fake_probe[inspect] = (0, "/web|0|container-id\n", "")
    await watcher.tick()
    fake_probe[ps] = (0, "web|exited|Exited (137) 1 second ago\n", "")
    await watcher.tick()
    fake_probe[inspect_exit_command(["web"])] = (
        0, "/web|true|137|false|2026-11-01T10:00:00Z|0|nginx:1.27|\n", "",
    )
    fake_probe["docker logs --tail 25 web 2>&1"] = (0, "worker allocation failed\n", "")
    complete = AsyncMock(return_value="Keine Aktion erforderlich.")
    monkeypatch.setattr(extension._ai, "complete", complete)
    batch = extension._store.take_batch()
    assert len(batch) == 1
    await extension._process_batch(extension._ctx, batch)
    prompt = complete.call_args.args[0]
    assert "OOMKilled=true" in prompt and "ExitCode=137" in prompt
    assert "worker allocation failed" in prompt
    assert not gate._ssh_access.get()


@pytest.mark.parametrize("outcome", ["pull_failed", "healthy", "crashed"])
async def test_docker_start_resumes_saved_update(docker_api, fake_probe, monkeypatch, outcome):
    from nodvard_deck_ext_docker import applier as ap
    from nodvard_deck_ext_docker import detached as dt
    from nodvard_deck_ext_docker import image_apply as ia
    from nodvard_deck_ext_docker.operate import OperateExec

    loaded = get_extension_runtime().loaded["docker"]
    extension = loaded.instance
    assert isinstance(extension._applier._ctx.exec, OperateExec)
    host_id = docker_api.host.id
    run_id = "imgupd_0123456789abcdef"
    target = ia.classify({
        "id": "a" * 64, "name": "/shop-web-1", "image": "nginx:1.27", "image_id": "sha256:" + "b" * 64,
        "state": "running", "labels": {
            "com.docker.compose.project": "shop", "com.docker.compose.service": "web",
            "com.docker.compose.project.working_dir": "/opt/stacks/shop",
            "com.docker.compose.project.config_files": "/opt/stacks/shop/compose.yaml",
            "com.docker.compose.config-hash": "c" * 64, "com.docker.compose.version": "2.29.1",
        },
    }, own=False)
    extension._applier._store_run({
        "run_id": run_id, "host_id": host_id, "container": target.container,
        "started_at": datetime.now(UTC).isoformat(), "target": ap._target_to_json(target),
    })
    monkeypatch.setattr(ap, "RESUME_DELAY_S", 0)
    monkeypatch.setattr(ap, "RUN_DEADLINE_S", 0.1)
    monkeypatch.setattr(ap, "POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(ap, "ROLLBACK_DEADLINE_S", 0.1)
    monkeypatch.setattr(extension._images, "recheck_container", AsyncMock())
    fake_probe[dt.poll_command(run_id)] = (
        0, f"@@rc={ia.RC_PULL if outcome == 'pull_failed' else 0}\n@@tail\nmanifest unknown\n", "",
    )
    back_id = ap.rollback_run_id(run_id)
    fake_probe[dt.poll_command(back_id)] = (0, "@@unknown\n@@tail\n", "")
    new_image = "sha256:" + "d" * 64
    verify_command = ia.cmd_verify(target, ia.rollback_ref(target.container))

    def state(image_id, *, crashed=False):
        member = {
            "name": target.container, "image_id": image_id, "status": "exited" if crashed else "running",
            "exit_code": 1 if crashed else 0, "health": "healthy", "restarts": 0,
        }
        return json.dumps(member) + f"\n@@image={image_id}\n@@rollback={target.image_id}\n"

    fake_probe[verify_command] = (0, state(new_image, crashed=outcome == "crashed"), "")
    launch = dt.launch_command(back_id, ia.rollback_script(target, ia.rollback_ref(target.container)), target.project)
    fake_probe[launch] = (0, "@@launch\n@@method=systemd\n@@pid=42\n", "")
    original = ssh.run
    commands = []

    async def run(conn, command, **kwargs):
        commands.append(command)
        if command == launch:
            fake_probe[dt.poll_command(back_id)] = (0, "@@rc=0\n@@tail\n", "")
            fake_probe[verify_command] = (0, state(target.image_id), "")
        return await original(conn, command, **kwargs)

    monkeypatch.setattr(ssh, "run", run)
    await extension.on_start(loaded.ctx)
    await asyncio.wait_for(loaded.tasks[-1].task, timeout=10)
    key = f"{host_id}:{target.container}"
    result = extension._applier.snapshot()["applied"][key]
    assert result["ok"] is (outcome == "healthy")
    if outcome == "pull_failed":
        assert result["summary"] == ia.failure_headline(target, ia.describe_pull_failure("manifest unknown", public=True)).splitlines()[0]
    elif outcome == "crashed":
        assert launch in commands
        assert "Automatisch zurück" in result["summary"]
    else:
        assert verify_command in commands
    assert extension._applier._read_runs() == {}


@pytest.mark.parametrize("resumed", [False, True, "queued", "limit"])
async def test_shield_audit_finishes_with_own_access(shield_api, db_session, monkeypatch, fake_probe, resumed):
    from nodvard_deck_ext_shield import audit_job as aj
    from nodvard_deck_ext_shield import defender as df
    from nodvard_deck_ext_shield.models import AuditRecord
    from nodvard_deck_ext_shield.patching import save_baseline

    loaded = get_extension_runtime().loaded["shield"]
    run_id = "audit_0123456789abcdef"
    now = time.time()
    monkeypatch.setattr(df, "_new_id", lambda prefix: run_id)
    monkeypatch.setattr(df, "AUDIT_RESUME_DELAY_S", 0)
    monkeypatch.setattr(df, "AUDIT_REACH_GRACE_S", 0)
    monkeypatch.setattr(df, "AUDIT_LIMIT_S", 1)
    monkeypatch.setattr(df, "AUDIT_STOP_GRACE_S", 0)
    fake_probe[aj.launch_as_root(run_id, limit_s=1)] = (0, "@@launch\n@@method=systemd\n@@pid=42\n", "")
    fake_probe[aj.poll_as_root(run_id)] = (
        0, "@@rc=0\n@@tail\n@@lynis-rc=0\n@@report\nhardening_index=68\nfinish=true\n", "",
    )
    if resumed == "limit":
        fake_probe[aj.poll_as_root(run_id)] = (0, "@@running\n@@tail\n", "")
        fake_probe[aj.stop_as_root(run_id)] = (0, "@@stopped\n", "")
    if resumed:
        await save_baseline(loaded.ctx, shield_api.host.id, df.AUDIT_STATE_KIND, {
            "run_id": run_id, "requested_at": now,
            "started_at": None if resumed == "queued" else now - (10 if resumed == "limit" else 0),
            "trigger": "manual",
        })
        await shield_api.shield_on_start(loaded.instance, loaded.ctx)
    else:
        headers = await _login(shield_api.client, db_session, "auditor", permissions=("soc.manage",))
        response = await shield_api.client.post(
            "/api/v1/ext/shield/defender/audits", headers=headers, json={"host_ids": [shield_api.host.id]},
        )
        assert response.status_code == 200, response.text
    async with asyncio.timeout(10):
        record = None
        while record is None:
            async with session_scope() as session:
                record = await session.get(AuditRecord, run_id)
            await asyncio.sleep(0.01)
    if resumed == "limit":
        assert (record.status, record.error) == ("error", aj.too_long_message(1))
    else:
        assert (record.status, record.hardening_index, record.error) == ("ok", 68, None)


async def test_shield_start_resumes_package_update(shield_api, db_session, monkeypatch, fake_probe):
    from nodvard_deck_ext_shield import patching as patch
    from nodvard_deck_ext_shield import updates as up
    from nodvard_deck_ext_shield.models import UpdateRunRecord

    loaded = get_extension_runtime().loaded["shield"]
    run_id = "upd_0123456789abcdef"
    db_session.add(UpdateRunRecord(
        id=run_id, host_id=shield_api.host.id, host_name="Server", mode="all", trigger="manual",
        status="running", started_at=datetime.now(UTC), remote_id=run_id,
    ))
    await db_session.commit()
    monkeypatch.setattr(patch, "RESUME_DELAY_S", 0)
    monkeypatch.setattr(patch, "UPGRADE_TIMEOUT", 0)
    monkeypatch.setattr(patch, "RESUME_MIN_WAIT_S", 0)
    fake_probe[patch.poll_as_root(run_id)] = (0, f"@@rc=0\n@@tail\n{up.UPGRADE_DONE}\n", "")
    fake_probe[up.status_command(refresh=False)] = (0, "@@pm\napt\n@@list\nrc list 0\n@@end\n", "")
    await shield_api.shield_on_start(loaded.instance, loaded.ctx)
    async with asyncio.timeout(10):
        status = "running"
        while status == "running":
            async with session_scope() as session:
                record = await session.get(UpdateRunRecord, run_id)
                status, summary = record.status, record.summary
            await asyncio.sleep(0.01)
    assert status == "ok", summary
