from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

SRC = Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"


@pytest.fixture(autouse=True)
def shield_path(monkeypatch):
    monkeypatch.syspath_prepend(str(SRC))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


@pytest_asyncio.fixture
async def ctx():
    from nodvard_deck_ext_shield.models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session():
        async with sessions() as db:
            yield db
            await db.commit()

    try:
        yield SimpleNamespace(
            db=SimpleNamespace(session=session), exec=SimpleNamespace(run=AsyncMock()),
            settings=SimpleNamespace(core=AsyncMock(return_value=None), get=AsyncMock(return_value={})),
            audit=SimpleNamespace(log=AsyncMock()), notify=SimpleNamespace(send=AsyncMock()),
            ws=SimpleNamespace(broadcast=AsyncMock()),
        )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("output", [
    "", "@@pm\napt\n@@list\n",
    "@@pm\napt\n@@list\nrc list 100\n@@end\n",
    "@@pm\ndnf\n@@list\nrc list 1\n@@end\n",
    "@@pm\napk\n@@list\nrc list 1\n@@end\n",
])
def test_failed_update_lists_are_errors(output):
    from nodvard_deck_ext_shield import updates

    status = updates.parse_status(output)
    assert status.error and not status.unsupported


@pytest.mark.parametrize(("manager", "code"), [("apt", 0), ("dnf", 0), ("dnf", 100), ("apk", 0)])
def test_successful_empty_update_lists(manager, code):
    from nodvard_deck_ext_shield import updates

    status = updates.parse_status(f"@@pm\n{manager}\n@@list\nrc list {code}\n@@end\n")
    assert status.error is None and status.packages == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("output", "code"), [
    ("@@pm\napt\n@@list\nrc list 100\n@@end\n", 0),
    ("@@pm\napt\n@@list\n", 0),
    ("@@pm\napt\n@@list\nrc list 0\n@@end\n", 1),
])
async def test_failed_update_check_keeps_successful_baseline(ctx, output, code):
    from nodvard_deck_ext_shield.patching import UpdateCenter, load_baseline, save_baseline

    old = {"manager": "apt", "count": 2, "packages": [{"name": "openssl"}, {"name": "curl"}], "checked_at": 1.0}
    await save_baseline(ctx, "h1", "updates", old)
    ctx.exec.run.return_value = SimpleNamespace(stdout=output, stderr="", exit_code=code)
    data = await UpdateCenter(ctx, None).check_host(SimpleNamespace(id="h1"), refresh=False)
    assert data["error"] and data["attempted_at"] > old["checked_at"]
    assert {key: data[key] for key in old} == old
    assert await load_baseline(ctx, "h1", "updates") == data


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine POSIX-Shell")
@pytest.mark.parametrize(("manager", "code", "error"), [("apt", 100, True), ("dnf", 1, True), ("dnf", 100, False), ("apk", 1, True)])
def test_update_command_reports_package_manager_exit(tmp_path, manager, code, error):
    from nodvard_deck_ext_shield import updates

    programs = {"apt-get": "exit 0", "apt": f"exit {code}"} if manager == "apt" else {manager: f"exit {code}"}
    for name, body in programs.items():
        program = tmp_path / name
        program.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        program.chmod(0o755)
    done = subprocess.run(["/bin/sh", "-c", updates.status_command(refresh=False)],
                          env={"PATH": str(tmp_path)}, capture_output=True, text=True, timeout=10)
    assert done.returncode == 0
    assert f"rc list {code}\n" in done.stdout
    assert bool(updates.parse_status(done.stdout).error) is error


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine POSIX-Shell")
@pytest.mark.parametrize("code", [0, 1])
def test_quarantine_delete_reports_rm_failure(code):
    from nodvard_deck_ext_shield import antivirus

    path = f"{antivirus.QUARANTINE_DIR}/test-datei"
    command = f"rm() {{ return {code}; }}; " + antivirus.delete_command(path)
    done = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True, timeout=10)
    assert done.returncode == code
    assert done.stdout.strip() == ("ok" if code == 0 else "")


@pytest.mark.parametrize("restore", [False, True])
def test_container_case_separates_pending_incidents_and_cooldowns(restore):
    from nodvard_deck_ext_shield.incidents import Incident, IncidentStore

    store = IncidentStore()
    incident = Incident(id="upper", host_id="h1", host_name="host", target="App", message="Absturz")
    if restore:
        store.restore([incident])
    else:
        store.enqueue(incident)
    assert store.is_in_cooldown("HOST", "App", cooldown_s=60)
    assert not store.is_in_cooldown("host", "app", cooldown_s=60)
    assert store.merge_pending("host", "app", is_crash=False, seen_at=time.time()) is None
    assert store.merge_pending("HOST", "App", is_crash=False, seen_at=time.time()) is incident


@pytest.mark.asyncio
async def test_container_case_separates_history_and_recovery(ctx):
    from nodvard_deck_ext_shield.history import IncidentRepository
    from nodvard_deck_ext_shield.incidents import Incident

    history = IncidentRepository(ctx)
    upper = Incident(id="upper", host_id="h1", host_name="host", target="App", message="Absturz", action_id="a1")
    lower = Incident(id="lower", host_id="h1", host_name="host", target="app", message="Absturz", action_id="a2")
    await history.save(upper)
    assert await history.merge_into_active(host_name="host", target="app", is_crash=False,
                                          seen_at=time.time(), window_s=60, action_waiting=AsyncMock(return_value=True)) is None
    assert await history.recent_action_ids(host_name="host", target="app") == []
    await history.resolve_recovered(host_name="host", target="App", note="Läuft wieder")
    assert await history.reopen_auto_resolved(host_name="host", target="app", is_crash=False,
                                             seen_at=time.time(), window_s=60, action_waiting=AsyncMock(return_value=True)) is None
    assert await history.reopen_auto_resolved(host_name="host", target="App", is_crash=False,
                                             seen_at=time.time(), window_s=60, action_waiting=AsyncMock(return_value=True)) is not None
    await history.save(lower)
    resolved = await history.resolve_recovered(host_name="HOST", target="App", note="Läuft wieder")
    assert [item["id"] for item in resolved] == ["upper"]
    assert (await history.get("lower"))["status"] == "open"
    assert not await history.recently_auto_resolved(host_name="host", target="app", since=time.time() - 60)


@pytest.mark.asyncio
async def test_restart_in_shared_batch_keeps_other_containers_cooldown(ctx):
    from nodvard_deck_ext_shield import Extension
    from nodvard_deck_ext_shield.history import IncidentRepository
    from nodvard_deck_ext_shield.incidents import Incident

    extension = Extension()
    extension._ctx = ctx
    extension._history = IncidentRepository(ctx)
    extension._queue = SimpleNamespace(add=AsyncMock(), mark_processed=AsyncMock())
    extension._batch_ready = asyncio.Event()
    host = SimpleNamespace(id="h1", name="host")
    action = SimpleNamespace(
        host_id=host.id, status="proposed", payload={"command": "docker restart App"}, finished_at=None,
    )
    ctx.actions = SimpleNamespace(result=AsyncMock(return_value=action))
    now = time.time()
    for target in ("App", "app"):
        extension._store.enqueue(Incident(
            id=target, host_id=host.id, host_name=host.name, target=target,
            message="Absturz", details={"is_crash": True}, created_at=now - 300,
        ))
    await extension._finish_group(
        ctx, extension._store.take_batch(), ai_summary="Container prüfen",
        action_id="a1", outcome="success", proposed=True,
    )
    action.status = "succeeded"
    action.finished_at = datetime.fromtimestamp(now - 120, UTC)
    for target in ("App", "app"):
        assert await extension._history.recent_action_ids(host_name=host.name, target=target) == [("a1", False)]
        assert extension._store.is_in_cooldown(host.name, target, cooldown_s=1800)

    await extension._on_transition(SimpleNamespace(
        host=host, target="app", message="Absturz", details={"is_crash": True},
    ))
    assert not extension._store.has_pending()
    extension._queue.add.assert_not_awaited()

    await extension._on_transition(SimpleNamespace(
        host=host, target="App", message="Absturz", details={"is_crash": True},
    ))
    batch = extension._store.take_batch()
    assert [(incident.target, incident.details.get("after_restart")) for incident in batch] == [
        ("App", action.finished_at.timestamp()),
    ]


@pytest.mark.asyncio
async def test_recovery_during_processing_only_resolves_exact_container(ctx):
    from nodvard_deck_ext_shield import Extension
    from nodvard_deck_ext_shield.history import IncidentRepository
    from nodvard_deck_ext_shield.incidents import Incident

    extension = Extension()
    extension._ctx = ctx
    extension._history = IncidentRepository(ctx)
    extension._queue = SimpleNamespace(mark_processed=AsyncMock())
    await extension._on_recovery(SimpleNamespace(id="h1", name="HOST"), "App", time.time() - 300)
    batch = [Incident(id=name, host_id="h1", host_name="host", target=name, message="Absturz") for name in ("App", "app")]
    await extension._finish_group(ctx, batch, ai_summary="Container prüfen", action_id=None, outcome="success", proposed=False)
    assert [incident.status for incident in batch] == ["resolved", "reviewed"]


@pytest.mark.asyncio
async def test_transition_keeps_other_containers_recovery(ctx):
    from nodvard_deck_ext_shield import Extension
    from nodvard_deck_ext_shield.incidents import Incident

    extension = Extension()
    extension._ctx = ctx
    extension._queue = SimpleNamespace(add=AsyncMock())
    extension._recovered = {("host", "App"): 1.0, ("host", "app"): 2.0}
    extension._store.enqueue(Incident(id="upper", host_id="h1", host_name="host", target="App", message="Absturz"))
    await extension._on_transition(SimpleNamespace(host=SimpleNamespace(name="HOST"), target="App", details={}))
    assert extension._recovered == {("host", "app"): 2.0}
