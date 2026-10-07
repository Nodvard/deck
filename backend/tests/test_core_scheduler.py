"""Der Kern-Scheduler -- docs/00-DECISIONS.md D-08.

APScheduler selbst (Faelligkeitsberechnung) wird hier nicht neu getestet -- das ist
APSchedulers eigene, fremde Verantwortung. Getestet wird NUR unser Teil: Ausfuehrung,
Lauf-Protokoll (DB + Datei), WS-Events, Abbruch, Idempotenz."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from nodvard_deck.core.scheduler import SchedulerService
from nodvard_deck.core.ws_hub import WsHub
from nodvard_deck.models import Job, JobRun
from nodvard_deck.services import jobs as jobs_service


@pytest.fixture
async def service(tmp_path: Path):
    svc = SchedulerService()
    svc.configure(tmp_path / "runs")
    svc.start()
    yield svc
    await svc.shutdown()


async def _make_job(db_session, **overrides) -> Job:
    defaults = dict(
        ext_id="test-ext", ext_job_key="job-1", name="Job 1", kind="ext",
        schedule="*/5 * * * *", params={}, enabled=True,
    )
    defaults.update(overrides)
    return await jobs_service.upsert_job(db_session, **defaults)


@pytest.mark.asyncio
async def test_trigger_now_creates_run_writes_log_and_marks_succeeded(service, db_session, tmp_path):
    job = await _make_job(db_session)

    async def handler(**kwargs):
        return {"echo": kwargs}

    run_id = await service.trigger_now(job, handler, params={"x": 1})

    run = await jobs_service.get_run(db_session, run_id)
    assert run.status == "succeeded"
    assert run.exit_code == 0
    assert run.trigger == "manual"
    assert run.finished_at is not None

    log_path = Path(run.output_ref)
    assert log_path.is_file()
    content = log_path.read_text(encoding="utf-8")
    assert "gestartet" in content
    assert "Ergebnis" in content


@pytest.mark.asyncio
async def test_handler_sees_how_the_job_was_started(service, db_session):
    """`nodvard_sdk.current_job_trigger()`: "manual" bei "Jetzt ausfuehren", "schedule" beim
    Zeitplan, ausserhalb eines Laufs None (z. B. Dauerfreigaben der Skripte nur nach Zeitplan)."""
    from nodvard_sdk import current_job_trigger

    job = await _make_job(db_session)
    seen: list[str | None] = []

    async def handler(**kwargs):
        seen.append(current_job_trigger())

    await service.trigger_now(job, handler)
    await service._run_job(job_id=job.id, ext_id=job.ext_id, handler=handler, params={}, trigger_kind="schedule")

    assert seen == ["manual", "schedule"]
    assert current_job_trigger() is None


@pytest.mark.asyncio
async def test_trigger_now_records_failure_without_crashing(service, db_session):
    job = await _make_job(db_session)

    async def handler(**kwargs):
        raise RuntimeError("kaputt")

    run_id = await service.trigger_now(job, handler)
    run = await jobs_service.get_run(db_session, run_id)
    assert run.status == "failed"
    assert run.exit_code == 1
    assert "kaputt" in run.error


@pytest.mark.asyncio
async def test_an_expected_failure_is_logged_without_traceback(service, db_session, tmp_path, caplog):
    """Meldet ein Handler einen Fehlschlag selbst (`NodvardError`, z. B. "Skript lief auf keinem Server"), steht er
    als Warnung mit Klartext im Log, ohne Traceback: Die Pruefung nach einem Deploy sucht im Container-Log nach
    "Traceback" und rollt sonst zurueck. Ein echter Programmfehler behaelt seinen Traceback."""
    import logging

    from nodvard_sdk.errors import NodvardError

    logging.getLogger("nodvard_deck").disabled = False
    logging.getLogger("nodvard_deck.core.scheduler").disabled = False
    job = await _make_job(db_session)

    async def expected(**kwargs):
        raise NodvardError("Das Skript lief auf keinem Server.")

    with caplog.at_level(logging.WARNING, logger="nodvard_deck.core.scheduler"):
        run_id = await service.trigger_now(job, expected)
    run = await jobs_service.get_run(db_session, run_id)
    assert (run.status, run.exit_code, run.error) == ("failed", 1, "Das Skript lief auf keinem Server.")
    records = [r for r in caplog.records if "job_run_failed" in r.getMessage()]
    assert records and all(r.exc_info is None and r.levelno == logging.WARNING for r in records)
    assert "Das Skript lief auf keinem Server." in records[-1].getMessage()
    log_text = (tmp_path / "runs" / f"{run_id}.log").read_text(encoding="utf-8")
    assert "Traceback" not in log_text and "Das Skript lief auf keinem Server." in log_text

    caplog.clear()

    async def bug(**kwargs):
        raise RuntimeError("kaputt")

    with caplog.at_level(logging.WARNING, logger="nodvard_deck.core.scheduler"):
        await service.trigger_now(job, bug)
    assert any(r.exc_info is not None for r in caplog.records if "job_run_failed" in r.getMessage())


@pytest.mark.asyncio
async def test_publishes_ws_events_for_job_and_run_channels(service, db_session):
    hub = WsHub()

    class _Sock:
        def __init__(self):
            self.sent = []

        async def send_json(self, data):
            self.sent.append(data)

    sock = _Sock()
    conn = hub.connect(sock, user_id="u1", permissions=["jobs.read"])

    import nodvard_deck.core.ws_hub as ws_hub_module

    ws_hub_module._hub = hub  # denselben Hub wie das Test-Double benutzen

    job = await _make_job(db_session)
    hub.subscribe(conn, f"jobs.{job.id}")

    async def handler(**kwargs):
        return "ok"

    run_id = await service.trigger_now(job, handler)
    hub.subscribe(conn, f"runs.{run_id}")  # zu spaet fuer "running", aber irrelevant hier

    channels = {msg["channel"] for msg in sock.sent}
    assert f"jobs.{job.id}" in channels
    statuses = [msg["payload"].get("status") for msg in sock.sent if msg["channel"] == f"jobs.{job.id}"]
    assert "running" in statuses
    assert "succeeded" in statuses

    ws_hub_module._hub = None


@pytest.mark.asyncio
async def test_cancel_run_cancels_in_flight_task(service, db_session):
    job = await _make_job(db_session)
    started = asyncio.Event()

    async def handler(**kwargs):
        started.set()
        await asyncio.sleep(30)
        return "never"

    run_task = asyncio.ensure_future(service.trigger_now(job, handler))
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.sleep(0.05)  # dem Scheduler Zeit geben, den Task in _running_tasks einzutragen

    run_rows = (await db_session.execute(select(JobRun).where(JobRun.job_id == job.id))).scalars().all()
    assert len(run_rows) == 1
    run_id = run_rows[0].id

    cancelled = service.cancel_run(run_id)
    assert cancelled is True

    await asyncio.wait_for(run_task, timeout=2)
    db_session.expire_all()  # _run_job() aktualisiert ueber eine ANDERE Session (session_scope())
    run = await jobs_service.get_run(db_session, run_id)
    assert run.status == "failed"
    assert "Abgebrochen" in run.error


@pytest.mark.asyncio
async def test_cancel_run_unknown_or_finished_returns_false(service):
    assert service.cancel_run("does-not-exist") is False


@pytest.mark.asyncio
async def test_schedule_is_idempotent_replace_existing(service, db_session):
    job = await _make_job(db_session)

    async def handler(**kwargs):
        return None

    await service.schedule(job, handler)
    await service.schedule(job, handler)  # zweites Mal -- darf nicht doppelt eintragen/werfen

    jobs_in_scheduler = service._scheduler.get_jobs()
    assert len([j for j in jobs_in_scheduler if j.id == job.id]) == 1


@pytest.mark.asyncio
async def test_schedule_with_enabled_false_unschedules(service, db_session):
    job = await _make_job(db_session)

    async def handler(**kwargs):
        return None

    await service.schedule(job, handler)
    assert service._scheduler.get_job(job.id) is not None

    job.enabled = False
    await service.schedule(job, handler)
    assert service._scheduler.get_job(job.id) is None


@pytest.mark.asyncio
async def test_schedule_writes_next_run_at_even_before_the_scheduler_is_started(db_session, tmp_path):
    """Der konkrete Absturz, den der volle Testlauf vor dem Deploy gefangen hat:
    main.py's Lifespan registriert ALLE Jobs (jede Extension mit `setup()` ->
    `register_job()`), BEVOR `get_scheduler_service().start()` laeuft (siehe dortiger
    Kommentar -- `interrupted`-Markierung muss zuerst durch). Ein `AsyncIOScheduler`,
    der noch nicht gestartet ist, liefert aus `add_job()` ein `Job`-Objekt OHNE das
    Attribut `next_run_time` (kein `None` -- `AttributeError`) -- ein erster
    Implementierungsversuch griff trotzdem direkt darauf zu und haette JEDEN
    Extension-Boot mit einem registrierten Job zum Absturz gebracht. Bewusst OHNE
    die `service`-Fixture (die `.start()` bereits aufruft), um genau diesen
    Zustand nachzubilden."""
    svc = SchedulerService()
    svc.configure(tmp_path / "runs")
    # bewusst KEIN svc.start()

    job = await _make_job(db_session)
    job_id = job.id

    async def handler(**kwargs):
        return None

    await svc.schedule(job, handler)  # darf nicht werfen

    db_session.expire_all()
    refreshed = await jobs_service.get_job(db_session, job_id)
    assert refreshed.next_run_at is not None


@pytest.mark.asyncio
async def test_schedule_writes_next_run_at(service, db_session):
    """`Job.next_run_at`
    wurde bisher NIE beschrieben -- `GET /jobs` zeigte das Feld dauerhaft als
    `null`, obwohl APScheduler den Termin sofort nach `add_job()` kennt."""
    job = await _make_job(db_session)
    job_id = job.id
    assert job.next_run_at is None

    async def handler(**kwargs):
        return None

    await service.schedule(job, handler)

    db_session.expire_all()  # schedule() schreibt ueber eine ANDERE Session (session_scope())
    refreshed = await jobs_service.get_job(db_session, job_id)
    assert refreshed.next_run_at is not None
    assert refreshed.next_run_at == service._scheduler.get_job(job_id).next_run_time


@pytest.mark.asyncio
async def test_schedule_with_enabled_false_clears_next_run_at(service, db_session):
    job = await _make_job(db_session)
    job_id = job.id

    async def handler(**kwargs):
        return None

    await service.schedule(job, handler)
    db_session.expire_all()
    assert (await jobs_service.get_job(db_session, job_id)).next_run_at is not None

    job = await jobs_service.get_job(db_session, job_id)
    job.enabled = False
    await service.schedule(job, handler)
    db_session.expire_all()
    assert (await jobs_service.get_job(db_session, job_id)).next_run_at is None


@pytest.mark.asyncio
async def test_a_run_refreshes_next_run_at_afterwards(service, db_session):
    """APScheduler berechnet nach jedem Feuern intern den naechsten Termin neu --
    die gespiegelte Spalte darf danach nicht auf dem urspruenglichen Wert
    stehenbleiben."""
    job = await _make_job(db_session, schedule="* * * * *")
    job_id = job.id

    async def handler(**kwargs):
        return None

    await service.schedule(job, handler)
    db_session.expire_all()
    before = (await jobs_service.get_job(db_session, job_id)).next_run_at

    job = await jobs_service.get_job(db_session, job_id)
    await service.trigger_now(job, handler)

    db_session.expire_all()
    after = (await jobs_service.get_job(db_session, job_id)).next_run_at
    assert after is not None
    assert after == service._scheduler.get_job(job_id).next_run_time
    # Nicht zwingend ein ANDERER Wert (haengt vom Cron-Ausdruck/Timing ab), aber
    # muss mit APSchedulers tatsaechlichem, aktuellem Termin uebereinstimmen --
    # ohne den Fix bliebe `before` (der Wert von VOR dem Lauf) unveraendert stehen.
    assert before is not None


@pytest.mark.asyncio
async def test_unschedule_extension_removes_all_its_jobs(service, db_session):
    job_a = await _make_job(db_session, ext_job_key="a")
    job_b = await _make_job(db_session, ext_job_key="b")
    other_ext_job = await _make_job(db_session, ext_id="other-ext", ext_job_key="a")

    async def handler(**kwargs):
        return None

    await service.schedule(job_a, handler)
    await service.schedule(job_b, handler)
    await service.schedule(other_ext_job, handler)

    await service.unschedule_extension("test-ext")

    assert service._scheduler.get_job(job_a.id) is None
    assert service._scheduler.get_job(job_b.id) is None
    assert service._scheduler.get_job(other_ext_job.id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("schedule", ["30 3 * * 0", "30 3 * * 7"])
async def test_schedule_sunday_next_run_is_a_sunday(service, db_session, schedule):
    """Crontab-Wochentag: 0 und 7 = Sonntag (nicht APSchedulers 0 = Montag)."""
    from zoneinfo import ZoneInfo

    job = await _make_job(db_session, schedule=schedule)

    async def handler(**kwargs):
        return None

    job_id = job.id
    await service.schedule(job, handler)

    db_session.expire_all()
    next_run = (await jobs_service.get_job(db_session, job_id)).next_run_at
    assert next_run.astimezone(ZoneInfo("Europe/Berlin")).strftime("%A") == "Sunday"
    scheduled = service._scheduler.get_job(job_id).next_run_time
    assert scheduled.astimezone(ZoneInfo("Europe/Berlin")).strftime("%A") == "Sunday"
