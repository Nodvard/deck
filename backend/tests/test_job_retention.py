"""Alte Job-Laeufe und ihre Protokolldateien aufraeumen (`services/job_retention.py`,
Kern-Job `job-runs-retention`)."""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, insert, select

from nodvard_deck import config
from nodvard_deck.config import Settings
from nodvard_deck.core.scheduler import (
    CORE_SCHEDULER_EXT_ID, JOB_RUNS_RETENTION_KEY, get_scheduler_service, register_core_jobs,
)
from nodvard_deck.db import utcnow
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import Job, JobRun
from nodvard_deck.services import job_retention
from nodvard_deck.services import jobs as jobs_service
from nodvard_deck.services import settings as settings_service

DAY = timedelta(days=1)


async def _job(session, key="x") -> Job:
    return await jobs_service.upsert_job(
        session, ext_id=None, ext_job_key=key, name=key, kind="core", schedule="* * * * *", params={}, enabled=True,
    )


def _run(job_id, *, age_days: float, status="succeeded", output_ref=None, run_id=None) -> dict:
    return {
        "id": run_id or str(uuid.uuid4()), "job_id": job_id, "trigger": "schedule", "status": status,
        "started_at": utcnow() - timedelta(days=age_days), "output_ref": output_ref, "actor": {},
    }


async def _add(session, rows: list[dict]) -> None:
    await session.execute(insert(JobRun), rows)
    await session.commit()


async def _ids(session) -> set[str]:
    return set((await session.execute(select(JobRun.id))).scalars().all())


async def _purge(session, runs_dir: Path, **kwargs):
    kwargs.setdefault("retention_days", 30)
    kwargs.setdefault("pause", 0)
    return await job_retention.purge_old_runs(session, runs_dir=runs_dir, **kwargs)


def _log(runs_dir: Path, run_id: str, *, age_days: float = 0) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{run_id}.log"
    path.write_text("x", encoding="utf-8")
    if age_days:
        stamp = (utcnow() - timedelta(days=age_days)).timestamp()
        os.utime(path, (stamp, stamp))
    return path


# ---------------------------------------------------------------------------
# Zeilen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_old_runs_are_removed_and_young_ones_stay(db_session, tmp_path):
    job = await _job(db_session)
    old = [_run(job.id, age_days=40 + i) for i in range(25)]
    young = [_run(job.id, age_days=1 + i) for i in range(5)]
    await _add(db_session, old + young)

    result = await _purge(db_session, tmp_path / "runs", keep_per_job=0)

    assert result.runs_deleted == 25
    assert await _ids(db_session) == {r["id"] for r in young}


@pytest.mark.asyncio
async def test_the_newest_20_runs_of_every_job_stay_even_when_old(db_session, tmp_path):
    rare = await _job(db_session, "monatlich")
    busy = await _job(db_session, "alle-5-minuten")
    # Seltener Job: 30 Laeufe, alle aelter als 30 Tage -> genau die 20 neuesten bleiben.
    rare_runs = [_run(rare.id, age_days=60 + 30 * i) for i in range(30)]
    # Haeufiger Job: 100 alte Laeufe + 3 junge -> die 3 jungen und 17 der alten bleiben.
    busy_old = [_run(busy.id, age_days=31 + i * 0.01) for i in range(100)]
    busy_young = [_run(busy.id, age_days=i * 0.1) for i in range(3)]
    await _add(db_session, rare_runs + busy_old + busy_young)

    result = await _purge(db_session, tmp_path / "runs")

    kept_rare = sorted((r["id"] for r in rare_runs[:20]))
    newest_busy_old = [r["id"] for r in sorted(busy_old, key=lambda r: r["started_at"], reverse=True)[:17]]
    assert await _ids(db_session) == set(kept_rare) | set(newest_busy_old) | {r["id"] for r in busy_young}
    assert result.runs_deleted == 10 + 83


@pytest.mark.asyncio
async def test_kept_runs_are_counted_per_job_and_runs_without_a_job_go_by_age_only(db_session, tmp_path):
    a = await _job(db_session, "a")
    b = await _job(db_session, "b")
    rows = [_run(a.id, age_days=90 + i) for i in range(3)] + [_run(b.id, age_days=90 + i) for i in range(3)]
    orphan_rows = [_run(None, age_days=90 + i) for i in range(4)] + [_run(None, age_days=2)]
    await _add(db_session, rows + orphan_rows)

    result = await _purge(db_session, tmp_path / "runs")

    assert result.runs_deleted == 4  # nur die vier alten ohne Job; je Job bleiben 3 von hoechstens 20
    assert len(await _ids(db_session)) == 6 + 1


@pytest.mark.asyncio
async def test_running_runs_always_stay(db_session, tmp_path):
    job = await _job(db_session)
    running = [_run(job.id, age_days=400, status="running") for _ in range(3)]
    finished = [_run(job.id, age_days=300 + i) for i in range(30)]
    ownerless_running = _run(None, age_days=500, status="running")
    await _add(db_session, running + finished + [ownerless_running])

    await _purge(db_session, tmp_path / "runs", keep_per_job=0)

    assert await _ids(db_session) == {r["id"] for r in running} | {ownerless_running["id"]}


@pytest.mark.asyncio
async def test_a_running_run_does_not_use_up_one_of_the_20_kept_places(db_session, tmp_path):
    job = await _job(db_session)
    running = _run(job.id, age_days=1, status="running")
    finished = [_run(job.id, age_days=60 + i) for i in range(25)]
    await _add(db_session, [running] + finished)

    await _purge(db_session, tmp_path / "runs")

    left = await _ids(db_session)
    assert running["id"] in left
    assert len(left) == 21  # 20 beendete + der laufende


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "interrupted", "skipped"])
async def test_every_finished_status_is_removed(db_session, tmp_path, status):
    await _add(db_session, [_run(None, age_days=60, status=status)])
    assert (await _purge(db_session, tmp_path / "runs")).runs_deleted == 1


@pytest.mark.asyncio
async def test_rows_go_in_chunks_with_a_commit_in_between(db_session, tmp_path, monkeypatch):
    job = await _job(db_session)
    await _add(db_session, [_run(job.id, age_days=40 + i / 1000) for i in range(12_000)])
    commits = 0
    real_commit = db_session.commit

    async def counting_commit():
        nonlocal commits
        commits += 1
        await real_commit()

    monkeypatch.setattr(db_session, "commit", counting_commit)

    result = await _purge(db_session, tmp_path / "runs")  # Vorgabe: 5000 je Durchgang

    assert result.runs_deleted == 12_000 - job_retention.KEEP_PER_JOB
    assert result.chunks == 3  # 5000 + 5000 + 1980
    assert commits >= result.chunks
    assert (await db_session.execute(select(func.count()).select_from(JobRun))).scalar_one() == job_retention.KEEP_PER_JOB


@pytest.mark.asyncio
async def test_chunk_size_is_respected_and_an_exact_multiple_ends_cleanly(db_session, tmp_path):
    await _add(db_session, [_run(None, age_days=40 + i) for i in range(10)])
    result = await _purge(db_session, tmp_path / "runs", chunk_size=5)
    assert (result.runs_deleted, result.chunks) == (10, 2)


@pytest.mark.asyncio
async def test_a_second_pass_changes_nothing(db_session, tmp_path):
    job = await _job(db_session)
    await _add(db_session, [_run(job.id, age_days=40 + i) for i in range(30)])
    first = await _purge(db_session, tmp_path / "runs")
    second = await _purge(db_session, tmp_path / "runs")
    assert first.runs_deleted == 10 and second.as_dict() == {
        "runs_deleted": 0, "files_deleted": 0, "orphans_deleted": 0, "chunks": 0,
    }


# ---------------------------------------------------------------------------
# Dateien
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_files_of_deleted_runs_are_removed_and_the_others_stay(db_session, tmp_path):
    runs = tmp_path / "runs"
    job = await _job(db_session)
    old = _run(job.id, age_days=60)
    young = _run(job.id, age_days=1)
    running = _run(job.id, age_days=90, status="running")
    paths = {}
    for row in (old, young, running):
        paths[row["id"]] = _log(runs, row["id"])
        row["output_ref"] = str(paths[row["id"]])
    await _add(db_session, [old, young, running])

    result = await _purge(db_session, runs, keep_per_job=0)

    assert not paths[old["id"]].exists()
    assert paths[young["id"]].exists() and paths[running["id"]].exists()
    assert (result.runs_deleted, result.files_deleted) == (1, 1)


@pytest.mark.asyncio
async def test_the_log_file_of_a_kept_run_stays(db_session, tmp_path):
    runs = tmp_path / "runs"
    job = await _job(db_session)
    row = _run(job.id, age_days=200)
    path = _log(runs, row["id"], age_days=200)
    row["output_ref"] = str(path)
    await _add(db_session, [row])

    await _purge(db_session, runs)  # einziger Lauf des Jobs: bleibt (letzte 20)

    assert path.exists() and await _ids(db_session) == {row["id"]}


@pytest.mark.asyncio
async def test_a_missing_file_is_not_an_error(db_session, tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    await _add(db_session, [
        _run(None, age_days=60, output_ref=str(runs / "gibt-es-nicht.log")),
        _run(None, age_days=60, output_ref=str(tmp_path / "kein-ordner" / "x.log")),
        _run(None, age_days=60, output_ref=""),
    ])
    result = await _purge(db_session, runs)
    assert (result.runs_deleted, result.files_deleted) == (3, 0)


@pytest.mark.asyncio
async def test_a_missing_runs_dir_is_not_an_error(db_session, tmp_path):
    await _add(db_session, [_run(None, age_days=60)])
    result = await _purge(db_session, tmp_path / "gibt-es-nicht")
    assert result.runs_deleted == 1 and result.orphans_deleted == 0


@pytest.mark.asyncio
async def test_files_outside_runs_dir_are_never_deleted(db_session, tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    outside = tmp_path / "wichtig.txt"
    outside.write_text("nicht loeschen", encoding="utf-8")
    sibling = tmp_path / "runs-nebenan"
    sibling.mkdir()
    near = sibling / "x.log"
    near.write_text("nicht loeschen", encoding="utf-8")
    await _add(db_session, [
        _run(None, age_days=60, output_ref=str(outside)),
        _run(None, age_days=60, output_ref=str(near)),  # Praefix-Falle: "runs-nebenan" beginnt mit "runs"
        _run(None, age_days=60, output_ref=str(runs / ".." / "wichtig.txt")),  # Ausbruch per ..
        _run(None, age_days=60, output_ref=str(runs)),  # der Ordner selbst
    ])

    result = await _purge(db_session, runs)

    assert result.runs_deleted == 4 and result.files_deleted == 0
    assert outside.read_text(encoding="utf-8") == "nicht loeschen" and near.exists() and runs.is_dir()


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks nicht moeglich (Windows ohne Rechte)")


@pytest.mark.asyncio
async def test_a_symlink_to_the_outside_is_not_followed(db_session, tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    secret = tmp_path / "geheim.txt"
    secret.write_text("bleibt", encoding="utf-8")
    link = runs / "link.log"
    _symlink_or_skip(link, secret)
    await _add(db_session, [_run(None, age_days=60, output_ref=str(link))])

    result = await _purge(db_session, runs)

    assert secret.read_text(encoding="utf-8") == "bleibt"  # das Ziel ist unberuehrt
    assert not link.exists() and not link.is_symlink()  # nur der Link selbst ist weg
    assert result.runs_deleted == 1


@pytest.mark.asyncio
async def test_a_symlinked_directory_inside_runs_dir_does_not_lead_outside(db_session, tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    outside_dir = tmp_path / "draussen"
    outside_dir.mkdir()
    victim = outside_dir / "x.log"
    victim.write_text("bleibt", encoding="utf-8")
    _symlink_or_skip(runs / "unter", outside_dir, directory=True)
    await _add(db_session, [_run(None, age_days=60, output_ref=str(runs / "unter" / "x.log"))])

    await _purge(db_session, runs)

    assert victim.read_text(encoding="utf-8") == "bleibt"


@pytest.mark.asyncio
async def test_runs_dir_itself_may_be_a_symlink(db_session, tmp_path):
    real = tmp_path / "echt"
    real.mkdir()
    runs = tmp_path / "runs"
    _symlink_or_skip(runs, real, directory=True)
    row = _run(None, age_days=60)
    path = _log(runs, row["id"])
    row["output_ref"] = str(path)
    await _add(db_session, [row])

    result = await _purge(db_session, runs)

    assert result.files_deleted == 1 and not (real / path.name).exists()


# ---------------------------------------------------------------------------
# Verwaiste Dateien
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orphan_files_older_than_the_retention_are_removed(db_session, tmp_path):
    runs = tmp_path / "runs"
    job = await _job(db_session)
    known = _run(job.id, age_days=1)
    known_path = _log(runs, known["id"], age_days=100)  # alt, aber die Zeile gibt es
    # Zeile mit abweichendem Dateinamen: ueber `output_ref` bekannt
    other = runs / "umbenannt.log"
    other.write_text("x", encoding="utf-8")
    os.utime(other, (1, 1))
    renamed = _run(job.id, age_days=2, output_ref=str(other))
    known["output_ref"] = str(known_path)
    await _add(db_session, [known, renamed])
    orphan_old = _log(runs, str(uuid.uuid4()), age_days=45)
    orphan_young = _log(runs, str(uuid.uuid4()), age_days=2)
    foreign = runs / "notizen.txt"  # keine .log: nie anfassen
    foreign.write_text("x", encoding="utf-8")
    os.utime(foreign, (1, 1))
    subdir = runs / "ordner.log"
    subdir.mkdir()

    result = await _purge(db_session, runs)

    assert result.orphans_deleted == 1 and result.runs_deleted == 0
    assert not orphan_old.exists()
    assert orphan_young.exists() and known_path.exists() and other.exists() and foreign.exists() and subdir.is_dir()


@pytest.mark.asyncio
async def test_orphan_symlinks_are_left_alone(db_session, tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    target = tmp_path / "ziel.log"
    target.write_text("bleibt", encoding="utf-8")
    link = runs / "alt.log"
    _symlink_or_skip(link, target)
    os.utime(link, (1, 1), follow_symlinks=False)

    await _purge(db_session, runs)

    assert link.is_symlink() and target.exists()


@pytest.mark.asyncio
async def test_orphan_check_works_in_batches(db_session, tmp_path):
    runs = tmp_path / "runs"
    job = await _job(db_session)
    kept_rows = [_run(job.id, age_days=1) for _ in range(500)]
    await _add(db_session, kept_rows)
    for row in kept_rows:
        _log(runs, row["id"], age_days=90)
    orphans = [_log(runs, str(uuid.uuid4()), age_days=90) for _ in range(450)]

    result = await _purge(db_session, runs)

    assert result.orphans_deleted == 450
    assert not any(p.exists() for p in orphans)
    assert len(list(runs.glob("*.log"))) == 500


# ---------------------------------------------------------------------------
# Einstellung und Job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_effective_retention_reads_the_setting_and_ignores_broken_values(db_session):
    assert await job_retention.effective_retention_days(db_session) == 30
    await settings_service.set_global(db_session, job_retention.RETENTION_KEY, 7)
    assert await job_retention.effective_retention_days(db_session) == 7
    for broken in (0, -1, 3651, "7", True, 7.5, None):
        await settings_service.set_global(db_session, job_retention.RETENTION_KEY, broken)
        assert await job_retention.effective_retention_days(db_session) == 30


@pytest.mark.asyncio
async def test_the_core_job_is_registered_nightly_and_does_the_cleanup(db_session, tmp_path, monkeypatch):
    reset_extension_runtime()
    monkeypatch.setattr(config, "_settings", Settings(data_dir=tmp_path))
    await register_core_jobs(tmp_path / "runs")

    job = await jobs_service.get_job_by_key(db_session, ext_id=None, ext_job_key=JOB_RUNS_RETENTION_KEY)
    assert job is not None and job.kind == "core" and job.enabled
    fields = job.schedule.split()
    assert fields[2:] == ["*", "*", "*"] and fields[1] == "3"  # einmal pro Nacht
    assert get_scheduler_service()._scheduler.get_job(job.id) is not None
    handler = get_extension_runtime().scheduler.get(CORE_SCHEDULER_EXT_ID, JOB_RUNS_RETENTION_KEY)
    assert handler is not None

    runs = tmp_path / "runs"
    other = await _job(db_session, "anderer")
    rows = [_run(other.id, age_days=50 + i) for i in range(22)]  # alle ueber 30 Tage alt
    oldest = rows[-1]
    oldest["output_ref"] = str(_log(runs, oldest["id"]))
    await _add(db_session, rows)

    outcome = await handler()  # ohne Einstellung: 30 Tage; 20 bleiben, die zwei aeltesten gehen

    assert outcome["retention_days"] == 30 and outcome["runs_deleted"] == 2 and outcome["files_deleted"] == 1
    assert not Path(oldest["output_ref"]).exists()
    assert len(await _ids(db_session)) == 20

    third = await _job(db_session, "dritter")
    await _add(db_session, [_run(third.id, age_days=100 + i) for i in range(25)])
    await settings_service.set_global(db_session, job_retention.RETENTION_KEY, 3650)
    await db_session.commit()
    assert (await handler())["runs_deleted"] == 0  # weit unter der Aufbewahrung
    await settings_service.set_global(db_session, job_retention.RETENTION_KEY, 45)
    await db_session.commit()
    outcome = await handler()
    assert outcome["retention_days"] == 45 and outcome["runs_deleted"] == 5


@pytest.mark.asyncio
async def test_the_job_leaves_its_own_running_run_alone(db_session, tmp_path, monkeypatch):
    """Der Lauf, der gerade aufraeumt, steht selbst als `running` in `job_runs`."""
    reset_extension_runtime()
    monkeypatch.setattr(config, "_settings", Settings(data_dir=tmp_path))
    await register_core_jobs(tmp_path / "runs")
    job = await jobs_service.get_job_by_key(db_session, ext_id=None, ext_job_key=JOB_RUNS_RETENTION_KEY)
    own = _run(job.id, age_days=100, status="running")
    await _add(db_session, [own])
    handler = get_extension_runtime().scheduler.get(CORE_SCHEDULER_EXT_ID, JOB_RUNS_RETENTION_KEY)

    await handler()

    assert await _ids(db_session) == {own["id"]}
