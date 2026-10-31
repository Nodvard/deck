"""Zeitplan-/Lauf-DB-Operationen (docs/03-DATA-MODEL.md §7) -- reine
DB-Schicht, siehe test_core_scheduler.py fuer die Ausfuehrung."""

from __future__ import annotations

import os

import pytest

from nodvard_deck.models import JobRun
from nodvard_deck.services import jobs as jobs_service


@pytest.mark.asyncio
async def test_upsert_job_creates_then_updates_in_place(db_session):
    created = await jobs_service.upsert_job(
        db_session, ext_id="ext-a", ext_job_key="job-1", name="Erstname", kind="ext",
        schedule="*/5 * * * *", params={}, enabled=True,
    )
    updated = await jobs_service.upsert_job(
        db_session, ext_id="ext-a", ext_job_key="job-1", name="Neuer Name", kind="ext",
        schedule="0 * * * *", params={"x": 1}, enabled=False,
    )
    assert updated.id == created.id
    assert updated.name == "Neuer Name"
    assert updated.schedule == "0 * * * *"
    assert updated.enabled is False

    all_jobs = await jobs_service.list_jobs(db_session)
    assert len(all_jobs) == 1


@pytest.mark.asyncio
async def test_upsert_job_different_keys_create_separate_rows(db_session):
    await jobs_service.upsert_job(
        db_session, ext_id="ext-a", ext_job_key="job-1", name="A", kind="ext",
        schedule="* * * * *", params={}, enabled=True,
    )
    await jobs_service.upsert_job(
        db_session, ext_id="ext-a", ext_job_key="job-2", name="B", kind="ext",
        schedule="* * * * *", params={}, enabled=True,
    )
    assert len(await jobs_service.list_jobs(db_session, ext_id="ext-a")) == 2


@pytest.mark.asyncio
async def test_list_jobs_filters_by_ext_id_and_enabled(db_session):
    await jobs_service.upsert_job(
        db_session, ext_id="ext-a", ext_job_key="a", name="A", kind="ext",
        schedule="* * * * *", params={}, enabled=True,
    )
    await jobs_service.upsert_job(
        db_session, ext_id="ext-b", ext_job_key="b", name="B", kind="ext",
        schedule="* * * * *", params={}, enabled=False,
    )
    assert len(await jobs_service.list_jobs(db_session, ext_id="ext-a")) == 1
    assert len(await jobs_service.list_jobs(db_session, enabled=False)) == 1
    assert len(await jobs_service.list_jobs(db_session)) == 2


@pytest.mark.asyncio
async def test_get_job_by_key_roundtrip(db_session):
    job = await jobs_service.upsert_job(
        db_session, ext_id="ext-a", ext_job_key="job-1", name="A", kind="ext",
        schedule="* * * * *", params={}, enabled=True,
    )
    found = await jobs_service.get_job_by_key(db_session, ext_id="ext-a", ext_job_key="job-1")
    assert found is not None
    assert found.id == job.id
    assert await jobs_service.get_job_by_key(db_session, ext_id="ext-a", ext_job_key="does-not-exist") is None


@pytest.mark.asyncio
async def test_create_and_finish_run_roundtrip(db_session):
    job = await jobs_service.upsert_job(
        db_session, ext_id="ext-a", ext_job_key="job-1", name="A", kind="ext",
        schedule="* * * * *", params={}, enabled=True,
    )
    run = await jobs_service.create_run(db_session, job_id=job.id, ext_id="ext-a", trigger="manual")
    assert run.status == "running"
    assert run.finished_at is None

    await jobs_service.finish_run(db_session, run, status="succeeded", exit_code=0, output_ref="/data/runs/x.log")
    assert run.status == "succeeded"
    assert run.exit_code == 0
    assert run.finished_at is not None
    assert run.output_ref == "/data/runs/x.log"


@pytest.mark.asyncio
async def test_list_runs_filters_by_job_and_orders_newest_first(db_session):
    job = await jobs_service.upsert_job(
        db_session, ext_id="ext-a", ext_job_key="job-1", name="A", kind="ext",
        schedule="* * * * *", params={}, enabled=True,
    )
    other_job = await jobs_service.upsert_job(
        db_session, ext_id="ext-a", ext_job_key="job-2", name="B", kind="ext",
        schedule="* * * * *", params={}, enabled=True,
    )
    r1 = await jobs_service.create_run(db_session, job_id=job.id, ext_id="ext-a", trigger="manual")
    r2 = await jobs_service.create_run(db_session, job_id=job.id, ext_id="ext-a", trigger="schedule")
    await jobs_service.create_run(db_session, job_id=other_job.id, ext_id="ext-a", trigger="manual")

    runs = await jobs_service.list_runs(db_session, job_id=job.id)
    assert {r.id for r in runs} == {r1.id, r2.id}


@pytest.mark.asyncio
async def test_mark_interrupted_on_boot_only_touches_running_runs(db_session):
    job = await jobs_service.upsert_job(
        db_session, ext_id="ext-a", ext_job_key="job-1", name="A", kind="ext",
        schedule="* * * * *", params={}, enabled=True,
    )
    running = await jobs_service.create_run(db_session, job_id=job.id, ext_id="ext-a", trigger="schedule")
    already_done = await jobs_service.create_run(db_session, job_id=job.id, ext_id="ext-a", trigger="manual")
    await jobs_service.finish_run(db_session, already_done, status="succeeded", exit_code=0)

    count = await jobs_service.mark_interrupted_on_boot(db_session)
    assert count == 1

    db_session.expire_all()
    from sqlalchemy import select

    rows = {r.id: r for r in (await db_session.execute(select(JobRun))).scalars().all()}
    assert rows[running.id].status == "interrupted"
    assert rows[running.id].finished_at is not None
    assert rows[already_done.id].status == "succeeded"


@pytest.mark.parametrize(("ref", "inside"), [
    ("{runs}/x.log", True),
    ("{runs}/ordner/../x.log", True),
    ("{runs}/ordner/x.log", False),
    ("{base}/x.log", False),
    ("{runs}/..", False),
    ("{runs}/.", False),
    ("{runs}/", False),
    ("x.log", False),
    ("{runs}/a\x00b/x.log", False),
    ("", False),
    (None, False),
])
def test_run_file_accepts_only_names_directly_in_runs_dir(tmp_path, ref, inside):
    runs = tmp_path / "runs"
    (runs / "ordner").mkdir(parents=True)
    ref = ref.format(runs=runs, base=tmp_path) if ref else ref
    expected = os.path.join(os.path.realpath(runs), "x.log") if inside else None
    assert jobs_service.run_file(runs, ref) == expected


def test_run_file_follows_a_symlinked_runs_dir_but_never_the_name(tmp_path):
    real = tmp_path / "echt"
    real.mkdir()
    runs = tmp_path / "runs"
    try:
        runs.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks nicht möglich")
    (real / "link.log").symlink_to(tmp_path / "geheim.txt")
    assert jobs_service.run_file(runs, str(runs / "x.log")) == os.path.join(os.path.realpath(real), "x.log")
    assert jobs_service.run_file(runs, str(runs / "link.log")) == os.path.join(os.path.realpath(real), "link.log")


def test_run_file_refuses_a_name_that_is_the_folder_itself(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.chdir(runs)
    assert jobs_service.run_file(runs, ".") is None
