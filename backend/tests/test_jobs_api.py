"""GET/PATCH/DELETE /jobs, /jobs/{id}/run, /jobs/{id}/runs, /runs/{id},
/runs/{id}/output, /runs/{id}/cancel -- docs/04-API.md."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from nodvard_deck.core.scheduler import get_scheduler_service
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.services import jobs as jobs_service


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _create_user_with_role(db_session, *, username, password, role):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password(password), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.flush()
    return user


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_job(db_session, *, ext_id="test-ext", key="job-1", handler=None, enabled=True):
    async def _default(**kwargs):
        return "ok"

    job = await jobs_service.upsert_job(
        db_session, ext_id=ext_id, ext_job_key=key, name="Testjob", kind="ext",
        schedule="*/5 * * * *", params={}, enabled=enabled,
    )
    get_extension_runtime().scheduler.register(ext_id, key, handler or _default)
    return job


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(client):
    assert (await client.get("/api/v1/jobs")).status_code == 401


@pytest.mark.asyncio
async def test_list_and_get_job(client, db_session):
    token = await _bootstrap_owner(client)
    job = await _register_job(db_session)

    listed = await client.get("/api/v1/jobs", headers=_auth_header(token))
    assert listed.status_code == 200
    assert any(j["id"] == job.id for j in listed.json())

    got = await client.get(f"/api/v1/jobs/{job.id}", headers=_auth_header(token))
    assert got.status_code == 200
    assert got.json()["ext_job_key"] == "job-1"

    missing = await client.get("/api/v1/jobs/does-not-exist", headers=_auth_header(token))
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_operator_has_jobs_read_and_run_but_not_write(client, db_session):
    await _create_user_with_role(db_session, username="op1", password="whatever123", role="operator")
    job = await _register_job(db_session)

    login = await client.post("/api/v1/auth/login", json={"username": "op1", "password": "whatever123"})
    token = login.json()["access_token"]

    assert (await client.get("/api/v1/jobs", headers=_auth_header(token))).status_code == 200
    assert (
        await client.post(f"/api/v1/jobs/{job.id}/run", headers=_auth_header(token))
    ).status_code == 200
    assert (
        await client.patch(f"/api/v1/jobs/{job.id}", json={"enabled": False}, headers=_auth_header(token))
    ).status_code == 403


@pytest.mark.asyncio
async def test_a_switched_off_job_cannot_be_run_by_hand(client, db_session):
    owner = await _bootstrap_owner(client)
    await _create_user_with_role(db_session, username="op2", password="whatever123", role="operator")
    calls: list[dict] = []

    async def _updates(**kwargs):
        calls.append(kwargs)

    job = await _register_job(db_session, key="updates-auto", handler=_updates, enabled=False)
    login = await client.post("/api/v1/auth/login", json={"username": "op2", "password": "whatever123"})
    for token in (login.json()["access_token"], owner):
        r = await client.post(f"/api/v1/jobs/{job.id}/run", headers=_auth_header(token))
        assert r.status_code == 409 and "ausgeschaltet" in r.json()["detail"], r.text
    assert calls == []
    assert (await client.get(f"/api/v1/jobs/{job.id}/runs", headers=_auth_header(owner))).json() == []


@pytest.mark.asyncio
async def test_run_job_now_executes_and_returns_run(client, db_session):
    token = await _bootstrap_owner(client)
    job = await _register_job(db_session)

    r = await client.post(f"/api/v1/jobs/{job.id}/run", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "succeeded"
    assert body["trigger"] == "manual"


@pytest.mark.asyncio
async def test_run_job_without_loaded_extension_is_conflict(client, db_session):
    token = await _bootstrap_owner(client)
    job = await jobs_service.upsert_job(
        db_session, ext_id="ghost-ext", ext_job_key="job-1", name="Geisterjob", kind="ext",
        schedule="* * * * *", params={}, enabled=True,
    )
    # KEIN get_extension_runtime().scheduler.register() -- Extension "geladen", aber
    # kein Handler bekannt (z. B. nach einem Absturz).
    r = await client.post(f"/api/v1/jobs/{job.id}/run", headers=_auth_header(token))
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_run_core_job_via_api_is_rejected(client, db_session):
    token = await _bootstrap_owner(client)
    job = await jobs_service.upsert_job(
        db_session, ext_id=None, ext_job_key="core-job", name="Kernjob", kind="core",
        schedule="0 3 * * *", params={}, enabled=True,
    )
    r = await client.post(f"/api/v1/jobs/{job.id}/run", headers=_auth_header(token))
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_list_runs_get_run_and_output(client, db_session):
    token = await _bootstrap_owner(client)
    job = await _register_job(db_session)

    triggered = await client.post(f"/api/v1/jobs/{job.id}/run", headers=_auth_header(token))
    run_id = triggered.json()["id"]

    runs = await client.get(f"/api/v1/jobs/{job.id}/runs", headers=_auth_header(token))
    assert runs.status_code == 200
    assert any(r["id"] == run_id for r in runs.json())

    got_run = await client.get(f"/api/v1/runs/{run_id}", headers=_auth_header(token))
    assert got_run.status_code == 200
    assert got_run.json()["status"] == "succeeded"

    output = await client.get(f"/api/v1/runs/{run_id}/output", headers=_auth_header(token))
    assert output.status_code == 200
    assert "gestartet" in output.json()["output"]

    missing_run = await client.get("/api/v1/runs/does-not-exist", headers=_auth_header(token))
    assert missing_run.status_code == 404


@pytest.mark.asyncio
async def test_cancel_run_stops_a_long_running_job(client, db_session):
    token = await _bootstrap_owner(client)
    started = asyncio.Event()

    async def _slow(**kwargs):
        started.set()
        await asyncio.sleep(30)

    job = await _register_job(db_session, handler=_slow)

    run_task = asyncio.ensure_future(
        client.post(f"/api/v1/jobs/{job.id}/run", headers=_auth_header(token))
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.sleep(0.05)

    runs = await client.get(f"/api/v1/jobs/{job.id}/runs", headers=_auth_header(token))
    run_id = runs.json()[0]["id"]

    cancelled = await client.post(f"/api/v1/runs/{run_id}/cancel", headers=_auth_header(token))
    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is True

    await run_task  # den ausgeloesten (jetzt abgebrochenen) Trigger-Request sauber beenden

    again = await client.post(f"/api/v1/runs/{run_id}/cancel", headers=_auth_header(token))
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_patch_job_disable_then_enable_reschedules(client, db_session):
    token = await _bootstrap_owner(client)
    job = await _register_job(db_session)

    disabled = await client.patch(f"/api/v1/jobs/{job.id}", json={"enabled": False}, headers=_auth_header(token))
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert get_scheduler_service()._scheduler.get_job(job.id) is None

    enabled = await client.patch(f"/api/v1/jobs/{job.id}", json={"enabled": True}, headers=_auth_header(token))
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert get_scheduler_service()._scheduler.get_job(job.id) is not None


@pytest.mark.asyncio
async def test_delete_job_removes_it_and_unschedules(client, db_session):
    token = await _bootstrap_owner(client)
    job = await _register_job(db_session)

    deleted = await client.delete(f"/api/v1/jobs/{job.id}", headers=_auth_header(token))
    assert deleted.status_code == 204
    assert get_scheduler_service()._scheduler.get_job(job.id) is None

    missing = await client.get(f"/api/v1/jobs/{job.id}", headers=_auth_header(token))
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_patch_job_enable_on_file_db_does_not_block_itself(client, tmp_path):
    """PATCH enabled=true hielt mit dem Flush die Schreibsperre der
    Anfrage, schedule() schreibt next_run_at aber ueber eine EIGENE Session -- die
    wartete busy_timeout (5 s) ab, dann HTTP 500, enabled wurde zurueckgerollt,
    APScheduler hatte den Job trotzdem schon eingeplant. Gegen eine echte Datei-DB
    mit der echten get_session(), weil die StaticPool-Fixture das nie zeigt."""
    import time

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck.api.deps import get_session
    from nodvard_deck.config import Settings
    from nodvard_deck.db.session import (
        create_engine_for,
        reset_engine_cache,
        session_scope,
        set_engine_for_testing,
    )
    from nodvard_deck.main import app
    from nodvard_deck.models import Base, Job

    engine = create_engine_for(Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"))
    job_id = None
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        set_engine_for_testing(engine)
        app.dependency_overrides.pop(get_session, None)  # echte session_scope()-Semantik

        token = await _bootstrap_owner(client)
        async with session_scope() as session:
            job = await jobs_service.upsert_job(
                session, ext_id="test-ext", ext_job_key="job-1", name="Testjob", kind="ext",
                schedule="*/5 * * * *", params={}, enabled=False,
            )
            job_id = job.id
            orphan = await jobs_service.upsert_job(
                session, ext_id="test-ext", ext_job_key="ohne-handler", name="Ohne Handler", kind="ext",
                schedule="*/5 * * * *", params={}, enabled=False,
            )

        async def _handler(**kwargs):
            return "ok"

        get_extension_runtime().scheduler.register("test-ext", "job-1", _handler)

        started = time.monotonic()
        r = await client.patch(f"/api/v1/jobs/{job.id}", json={"enabled": True}, headers=_auth_header(token))
        elapsed = time.monotonic() - started
        assert r.status_code == 200, r.text
        assert elapsed < 3, f"PATCH hat {elapsed:.1f} s auf die eigene Schreibsperre gewartet"
        assert r.json()["enabled"] is True
        assert r.json()["next_run_at"] is not None

        # Ohne geladenen Handler: 409, und enabled bleibt in der DB aus.
        conflict = await client.patch(
            f"/api/v1/jobs/{orphan.id}", json={"enabled": True}, headers=_auth_header(token)
        )
        assert conflict.status_code == 409

        async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
            stored = await verify.get(Job, job.id)
            assert stored.enabled is True
            assert stored.next_run_at is not None
            stored_orphan = await verify.get(Job, orphan.id)
            assert stored_orphan.enabled is False
    finally:
        if job_id is not None:
            get_scheduler_service().unschedule(job_id)
        await engine.dispose()
        reset_engine_cache()


@pytest.fixture
def run_files(tmp_path):
    runs = tmp_path / "runs"
    (runs / "unter").mkdir(parents=True)
    (runs / "a.log").write_text("eigene Ausgabe", encoding="utf-8")
    (runs / "unter" / "b.log").write_text("weiter unten", encoding="utf-8")
    (tmp_path / "geheim.txt").write_text("geheim", encoding="utf-8")
    (tmp_path / "runs-nebenan").mkdir()
    (tmp_path / "runs-nebenan" / "x.log").write_text("geheim", encoding="utf-8")
    return runs


@pytest.mark.parametrize("ref,expected", [
    ("a.log", "eigene Ausgabe"),
    ("unter/b.log", ""),
    ("unter", ""),
    ("..", ""),
    ("../geheim.txt", ""),
    ("../runs-nebenan/x.log", ""),
    (".", ""),
    ("a\x00.log", ""),
])
def test_run_output_is_read_only_from_files_directly_in_the_runs_folder(run_files, ref, expected):
    assert jobs_service.read_run_output(run_files, f"{run_files}/{ref}") == expected


def test_run_output_keeps_no_file_open_and_replaces_broken_text(run_files):
    (run_files / "kaputt.log").write_bytes(b"vor\xffnach")
    assert jobs_service.read_run_output(run_files, str(run_files / "kaputt.log")) == "vor\ufffdnach"
    if not os.path.isdir("/proc/self/fd"):
        pytest.skip("braucht /proc")
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(20):
        assert jobs_service.read_run_output(run_files, f"{run_files}/..") == ""
    assert len(os.listdir("/proc/self/fd")) == before


def test_run_output_ignores_absolute_paths_links_and_fifos(run_files, tmp_path):
    assert jobs_service.read_run_output(run_files, str(tmp_path / "geheim.txt")) == ""
    assert jobs_service.read_run_output(run_files, None) == ""
    assert jobs_service.read_run_output(run_files, "") == ""
    if sys.platform == "win32":
        pytest.skip("braucht Symlinks und FIFOs")
    (run_files / "link.log").symlink_to(tmp_path / "geheim.txt")
    os.mkfifo(run_files / "fifo.log")
    assert jobs_service.read_run_output(run_files, str(run_files / "link.log")) == ""
    assert jobs_service.read_run_output(run_files, str(run_files / "fifo.log")) == ""


@pytest.mark.asyncio
async def test_a_run_from_a_changed_backup_cannot_read_other_files(client, db_session, tmp_path):
    from sqlalchemy import insert

    from nodvard_deck.db import utcnow
    from nodvard_deck.models import JobRun

    token = await _bootstrap_owner(client)
    secret = tmp_path / "geheim.txt"
    secret.write_text("root:x:0:0", encoding="utf-8")
    await db_session.execute(insert(JobRun), [{
        "id": "aus-sicherung", "job_id": None, "trigger": "schedule", "status": "succeeded", "started_at": utcnow(),
        "output_ref": str(secret), "actor": {},
    }])
    await db_session.commit()
    r = await client.get("/api/v1/runs/aus-sicherung/output", headers=_auth_header(token))
    assert r.status_code == 200 and r.json() == {"output": ""}
