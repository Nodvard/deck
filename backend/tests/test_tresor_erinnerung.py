"""Tests für `services.secret_expiry`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from nodvard_deck import config
from nodvard_deck.config import Settings
from nodvard_deck.core import vault
from nodvard_deck.core.scheduler import (
    CORE_SCHEDULER_EXT_ID,
    get_scheduler_service,
    register_core_jobs,
)
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import Host, HostCredential, Notification, Secret
from nodvard_deck.services import jobs as jobs_service
from nodvard_deck.services import notifications as notifications_service
from nodvard_deck.services import secret_expiry
from nodvard_deck.services import settings as settings_service

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 12, 6, 7, tzinfo=UTC)  # 08:07 in Berlin, wann der Job läuft


@pytest.fixture(autouse=True)
async def _berlin(db_session):
    await settings_service.set_global(db_session, "system.timezone", "Europe/Berlin")
    await db_session.commit()


def _keyring() -> vault.Keyring:
    from cryptography.fernet import Fernet

    return {1: Fernet.generate_key()}


async def _secret(db_session, label="proxmox-token", *, expires_at, owner=None, description="") -> Secret:
    secret = await vault.create_secret(
        db_session, _keyring(), label=label, kind="api_token", plaintext="tok_geheim",
        description=description, owner_ext_id=owner, expires_at=expires_at,
    )
    await db_session.commit()
    return secret


async def _notes(db_session) -> list[Notification]:
    db_session.expire_all()
    return list((await db_session.execute(select(Notification).order_by(Notification.ts))).scalars().all())


def _day(n: int, hour: int = 12) -> datetime:
    """n Tage nach heute (Berliner Kalender) zur Berliner Uhrzeit `hour`, in UTC."""
    local = NOW.astimezone(BERLIN)
    return (datetime(local.year, local.month, local.day, hour, tzinfo=BERLIN) + timedelta(days=n)).astimezone(UTC)


def test_stages_count_calendar_days_in_the_dashboard_zone():
    assert secret_expiry.stage_for(_day(15), NOW, BERLIN) is None
    assert secret_expiry.stage_for(_day(14, hour=23), NOW, BERLIN) is secret_expiry.SOON
    assert secret_expiry.stage_for(_day(4), NOW, BERLIN) is secret_expiry.SOON
    assert secret_expiry.stage_for(_day(3, hour=0), NOW, BERLIN) is secret_expiry.DUE
    assert secret_expiry.stage_for(_day(0, hour=23), NOW, BERLIN) is secret_expiry.DUE  # heute, noch nicht vorbei
    assert secret_expiry.stage_for(NOW, NOW, BERLIN) is secret_expiry.EXPIRED
    assert secret_expiry.stage_for(NOW - timedelta(days=40), NOW, BERLIN) is secret_expiry.EXPIRED
    # 00:30 Berliner Zeit am 15. Tag ist in UTC noch der 14.: es zählt der Berliner Kalender.
    late = _day(15, hour=0) + timedelta(minutes=30)
    assert late.astimezone(UTC).date() < late.astimezone(BERLIN).date()
    assert secret_expiry.stage_for(late, NOW, BERLIN) is None


def test_message_names_the_day_and_the_date_in_the_zone():
    secret = Secret(label="proxmox-token", description="Token für Proxmox", expires_at=_day(1, hour=0))
    title, body = secret_expiry.message(secret, secret_expiry.DUE, NOW, BERLIN, "Gehört zum Modul „Proxmox“.")
    assert title == "Zugang läuft bald ab: proxmox-token"
    assert body == (
        "„proxmox-token“ (Token für Proxmox) läuft morgen ab (am 13.10.2026). "
        "Bitte vorher einen neuen Zugang erzeugen und im Dashboard ersetzen. Gehört zum Modul „Proxmox“."
    )

    secret.expires_at = _day(14)
    assert "läuft in 14 Tagen ab (am 26.10.2026)" in secret_expiry.message(secret, secret_expiry.SOON, NOW, BERLIN)[1]
    secret.expires_at = _day(0, hour=23)
    assert "läuft heute ab" in secret_expiry.message(secret, secret_expiry.DUE, NOW, BERLIN)[1]

    secret.expires_at, secret.description = _day(-2), ""
    title, body = secret_expiry.message(secret, secret_expiry.EXPIRED, NOW, BERLIN)
    assert title == "Zugang abgelaufen: proxmox-token"
    assert body == "„proxmox-token“ ist seit dem 10.10.2026 abgelaufen. Bitte einen neuen Zugang erzeugen und im Dashboard ersetzen."
    secret.expires_at = NOW - timedelta(minutes=5)
    assert secret_expiry.message(secret, secret_expiry.EXPIRED, NOW, BERLIN)[1].startswith("„proxmox-token“ ist heute abgelaufen.")


@pytest.mark.asyncio
async def test_each_stage_comes_once_and_far_or_missing_dates_stay_quiet(db_session):
    secret = await _secret(db_session, expires_at=_day(20))
    await _secret(db_session, "ohne-datum", expires_at=None)
    await _secret(db_session, "weit-weg", expires_at=_day(200))

    assert await secret_expiry.run_check(now=NOW) == {"checked": 0, "sent": 0, "failed": 0}
    assert await _notes(db_session) == []

    for day, expected in ((5, 0), (6, 1), (7, 1), (16, 1), (17, 2), (20, 2), (21, 3), (60, 3)):
        await secret_expiry.run_check(now=NOW + timedelta(days=day))
        await secret_expiry.run_check(now=NOW + timedelta(days=day, hours=2))  # zweiter Lauf am selben Tag
        assert len(await _notes(db_session)) == expected, day

    notes = await _notes(db_session)
    assert [(n.severity, n.title) for n in notes] == [
        ("info", "Zugang läuft bald ab: proxmox-token"),
        ("warning", "Zugang läuft bald ab: proxmox-token"),
        ("warning", "Zugang abgelaufen: proxmox-token"),
    ]
    assert "in 14 Tagen" in notes[0].body and "in 3 Tagen" in notes[1].body
    assert [n.payload["tags"] for n in notes] == [["key"], ["warning", "key"], ["warning", "key"]]
    assert all(n.source_ext_id is None for n in notes)

    await db_session.refresh(secret)
    assert secret.secret_metadata["expiry_notice"] == {"for": secret.expires_at.isoformat(), "stage": "expired"}


@pytest.mark.asyncio
async def test_a_missed_window_sends_only_the_most_urgent_stage(db_session):
    await _secret(db_session, expires_at=_day(2))

    assert (await secret_expiry.run_check(now=NOW))["sent"] == 1
    assert [n.severity for n in await _notes(db_session)] == ["warning"]  # keine nachgeholte 14-Tage-Meldung

    await secret_expiry.run_check(now=NOW + timedelta(days=3))
    assert [n.title for n in await _notes(db_session)][-1] == "Zugang abgelaufen: proxmox-token"
    assert len(await _notes(db_session)) == 2


@pytest.mark.asyncio
async def test_a_new_expiry_date_starts_the_reminders_again(db_session):
    secret = await _secret(db_session, expires_at=_day(-1))
    await secret_expiry.run_check(now=NOW)
    assert len(await _notes(db_session)) == 1

    # Token erneuert, neues Datum eingetragen: erst still, dann wieder 14 Tage vorher.
    await db_session.refresh(secret)
    secret.expires_at = _day(30)
    await db_session.commit()
    await secret_expiry.run_check(now=NOW)
    assert len(await _notes(db_session)) == 1
    await secret_expiry.run_check(now=NOW + timedelta(days=16))
    notes = await _notes(db_session)
    assert len(notes) == 2 and notes[-1].severity == "info" and "in 14 Tagen" in notes[-1].body

    await db_session.refresh(secret)
    assert secret.secret_metadata["expiry_notice"]["stage"] == "soon"


@pytest.mark.asyncio
async def test_other_metadata_of_the_secret_stays(db_session):
    secret = await _secret(db_session, expires_at=_day(1))
    secret.secret_metadata = {"fingerprint": "SHA256:abc"}
    await db_session.commit()

    await secret_expiry.run_check(now=NOW)

    await db_session.refresh(secret)
    assert secret.secret_metadata["fingerprint"] == "SHA256:abc"
    assert secret.secret_metadata["expiry_notice"]["stage"] == "due"


@pytest.mark.asyncio
async def test_the_link_and_the_text_name_the_module_or_the_servers(db_session, monkeypatch):
    from types import SimpleNamespace

    runtime = get_extension_runtime()
    monkeypatch.setitem(runtime.discovered, "proxmox", SimpleNamespace(manifest=SimpleNamespace(name="Proxmox")))
    await _secret(db_session, "modul-token", expires_at=_day(1), owner="proxmox")
    await _secret(db_session, "altes-modul-token", expires_at=_day(1, hour=12), owner="gibt-es-nicht")
    one = await _secret(db_session, "server-schluessel", expires_at=_day(1, hour=13))
    shared = await _secret(db_session, "geteilter-schluessel", expires_at=_day(1, hour=14))
    await _secret(db_session, "frei", expires_at=_day(1, hour=15))
    hosts = [
        Host(name="zeta", display_name="", address="203.0.113.10"),
        Host(name="b-intern", display_name="Alpha", address="203.0.113.11"),
    ]
    db_session.add_all(hosts)
    await db_session.flush()
    zeta, alpha = hosts[0].id, hosts[1].id
    db_session.add_all([
        HostCredential(host_id=zeta, kind="ssh_key", username="admin", secret_id=one.id),
        HostCredential(host_id=zeta, kind="ssh_key", username="admin", secret_id=shared.id),
        HostCredential(host_id=alpha, kind="ssh_key", username="admin", secret_id=shared.id),
    ])
    await db_session.commit()

    await secret_expiry.run_check(now=NOW)

    notes = {n.title.split(": ", 1)[1]: n for n in await _notes(db_session)}
    assert {label: n.payload.get("path") for label, n in notes.items()} == {
        "modul-token": "/settings/extensions/proxmox",
        "altes-modul-token": None,  # das Modul gibt es nicht (mehr): kein toter Link
        "server-schluessel": f"/settings/hosts/{zeta}",
        "geteilter-schluessel": f"/settings/hosts/{alpha}",  # der erste nach Namen
        "frei": None,
    }
    assert notes["modul-token"].body.endswith("Gehört zum Modul „Proxmox“.")
    assert notes["altes-modul-token"].body.endswith("Gehört zum Modul „gibt-es-nicht“.")
    assert notes["server-schluessel"].body.endswith("Zugang für den Server „zeta“.")
    assert notes["geteilter-schluessel"].body.endswith("Zugang für die Server „Alpha“ und „zeta“.")
    assert notes["frei"].body.endswith("im Dashboard ersetzen.")


@pytest.mark.asyncio
async def test_many_servers_are_cut_short(db_session):
    shared = await _secret(db_session, "viele", expires_at=_day(1))
    hosts = [Host(name=f"server-{i}", address=f"203.0.113.{20 + i}") for i in range(5)]
    db_session.add_all(hosts)
    await db_session.flush()
    db_session.add_all(HostCredential(host_id=h.id, kind="ssh_key", username="admin", secret_id=shared.id) for h in hosts)
    await db_session.commit()

    await secret_expiry.run_check(now=NOW)

    (note,) = await _notes(db_session)
    assert note.body.endswith("Zugang für die Server „server-0“, „server-1“, „server-2“ und 2 weitere.")


@pytest.mark.asyncio
async def test_a_failure_after_the_notice_was_saved_never_sends_it_twice(db_session, monkeypatch):
    # `deliver` speichert die Meldung vor den Kanälen; der Merker muss dabei schon mitgespeichert sein.
    secret = await _secret(db_session, expires_at=_day(1))

    async def broken_log(*_args, **_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(notifications_service, "_log_deliveries", broken_log)
    assert await secret_expiry.run_check(now=NOW) == {"checked": 1, "sent": 0, "failed": 1}
    monkeypatch.undo()

    await db_session.refresh(secret)
    assert secret.secret_metadata["expiry_notice"]["stage"] == "due"
    assert (await secret_expiry.run_check(now=NOW + timedelta(hours=1)))["sent"] == 0
    assert len(await _notes(db_session)) == 1


@pytest.mark.asyncio
async def test_the_last_hour_of_the_fourteenth_day_is_within_the_window(db_session):
    # Die Vorauswahl läuft in UTC (`expires_at <= now + 15 Tage`), ein Eintrag spät am 14. Tag Ortszeit muss drin sein.
    await _secret(db_session, expires_at=_day(14, hour=23) + timedelta(minutes=30))
    await _secret(db_session, "einen-tag-spaeter", expires_at=_day(15, hour=0) + timedelta(minutes=30))

    assert (await secret_expiry.run_check(now=NOW))["sent"] == 1
    (note,) = await _notes(db_session)
    assert note.title == "Zugang läuft bald ab: proxmox-token" and "in 14 Tagen" in note.body


@pytest.mark.asyncio
async def test_an_unusable_zone_counts_in_utc_and_says_so(db_session, monkeypatch, caplog):
    async def broken_zone(_session):
        return "Keine/Zone"

    monkeypatch.setattr(secret_expiry, "get_timezone", broken_zone)
    await _secret(db_session, expires_at=_day(1))

    with caplog.at_level("WARNING", logger="nodvard_deck.secret_expiry"):
        assert (await secret_expiry.run_check(now=NOW))["sent"] == 1
    assert "secret_expiry_timezone_unusable" in caplog.text


@pytest.mark.asyncio
async def test_a_failing_entry_does_not_stop_the_others_and_is_tried_again(db_session, monkeypatch, caplog):
    broken = await _secret(db_session, "kaputt", expires_at=_day(1))
    await _secret(db_session, "heil", expires_at=_day(1, hour=13))

    real = notifications_service.deliver

    async def deliver(session, **kwargs):
        if "kaputt" in kwargs["title"]:
            raise RuntimeError("Datenbank gesperrt")
        return await real(session, **kwargs)

    monkeypatch.setattr(notifications_service, "deliver", deliver)
    with caplog.at_level("ERROR", logger="nodvard_deck.secret_expiry"):
        assert await secret_expiry.run_check(now=NOW) == {"checked": 2, "sent": 1, "failed": 1}
    # Im Protokoll steht nur die Kennung des Eintrags, nie der Inhalt.
    assert f"secret_expiry_reminder_failed secret_id={broken.id}" in caplog.text
    assert "tok_geheim" not in caplog.text
    await db_session.refresh(broken)
    assert "expiry_notice" not in broken.secret_metadata

    monkeypatch.setattr(notifications_service, "deliver", real)
    assert await secret_expiry.run_check(now=NOW + timedelta(hours=1)) == {"checked": 2, "sent": 1, "failed": 0}
    assert sorted(n.title for n in await _notes(db_session)) == [
        "Zugang läuft bald ab: heil", "Zugang läuft bald ab: kaputt",
    ]


@pytest.mark.asyncio
async def test_an_expired_entry_shows_on_the_start_page(client, db_session):
    from nodvard_deck.api.v1 import overview as overview_api

    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    await _secret(db_session, expires_at=_day(-1))
    await secret_expiry.run_check(now=NOW)

    overview_api.reset_cache()
    try:
        response = await client.get("/api/v1/overview", headers=headers)
    finally:
        overview_api.reset_cache()
    assert response.status_code == 200, response.text
    assert [a["title"] for a in response.json()["attention"]] == ["Zugang abgelaufen: proxmox-token"]


@pytest.mark.asyncio
async def test_the_core_job_is_registered_daily_in_the_morning(db_session, tmp_path, monkeypatch):
    reset_extension_runtime()
    monkeypatch.setattr(config, "_settings", Settings(data_dir=tmp_path))
    try:
        await register_core_jobs(tmp_path / "runs")

        job = await jobs_service.get_job_by_key(db_session, ext_id=None, ext_job_key=secret_expiry.JOB_KEY)
        assert job is not None and job.kind == "core" and job.enabled
        assert job.schedule == secret_expiry.SCHEDULE == "7 8 * * *"
        assert job.timezone == "Europe/Berlin"
        assert get_scheduler_service()._scheduler.get_job(job.id) is not None
        handler = get_extension_runtime().scheduler.get(CORE_SCHEDULER_EXT_ID, secret_expiry.JOB_KEY)
        assert handler is not None

        await _secret(db_session, expires_at=datetime.now(UTC) - timedelta(days=1))
        assert (await handler())["sent"] == 1
    finally:
        reset_extension_runtime()
