"""Regelmaessige Erreichbarkeitspruefung (`services/reachability.py`, Kern-Job
`host-reachability`).

Geprueft wird unser Teil: Auswahl der Server, TCP-Pruefung gegen einen lokalen Server, Entprellung,
Meldungsregeln (erste Meldung, Wartungsfenster, Nachmeldung), Parallelitaet, Lock, Einstellungen
und Neuplanen. Kein Zugriff auf echte Netze: die Pruefung wird, wo noetig, durch eine Attrappe ersetzt."""

from __future__ import annotations

import asyncio
import socket
from datetime import timedelta
from pathlib import Path

import pytest
from nodvard_deck.core import demo_seed
from nodvard_deck.core.events import get_event_bus
from nodvard_deck.core.scheduler import get_scheduler_service
from nodvard_deck.db import utcnow
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import AuditEntry, Host, HostCredential, JobRun, Notification
from nodvard_deck.services import jobs as jobs_service
from nodvard_deck.services import reachability
from nodvard_deck.services import settings as settings_service
from nodvard_sdk import Notification as SdkNotification
from nodvard_sdk.capabilities import NotificationChannel
from sqlalchemy import select


@pytest.fixture(autouse=True)
def _clean():
    reset_extension_runtime()
    reachability.reset_state()
    yield
    reachability.reset_state()
    reset_extension_runtime()


class _Channel:
    channel_id = "test-kanal"
    label = "Test"

    def __init__(self) -> None:
        self.received: list[SdkNotification] = []

    async def send(self, notification: SdkNotification) -> None:
        self.received.append(notification)

    async def test(self):
        raise NotImplementedError


@pytest.fixture
def channel() -> _Channel:
    ch = _Channel()
    get_extension_runtime().capabilities.provide("chan-ext", NotificationChannel, ch)
    return ch


@pytest.fixture
def reach(monkeypatch) -> dict[str, bool]:
    """Adresse -> antwortet (Standard: ja). Ersetzt die echte TCP-Pruefung."""
    answers: dict[str, bool] = {}

    async def fake(address: str, port: int, timeout_s: float = 3.0) -> bool:
        return answers.get(address, True)

    monkeypatch.setattr(reachability, "probe_tcp", fake)
    return answers


async def _host(session, name="pi", address="10.0.0.5", **fields) -> Host:
    host = Host(name=name, display_name=fields.pop("display_name", name.capitalize()), address=address, **fields)
    session.add(host)
    await session.commit()
    return host


async def _credential(session, host: Host, *, kind="ssh_key", port=22) -> None:
    session.add(HostCredential(host_id=host.id, kind=kind, username="root", port=port, secret_id="s", is_default=True))
    await session.commit()


async def _state(session, host: Host) -> Host:
    await session.refresh(host)
    return host


async def _notes(session) -> list[Notification]:
    session.expire_all()
    return list((await session.execute(select(Notification).order_by(Notification.ts))).scalars().all())


# ---------------------------------------------------------------------------
# TCP-Pruefung
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_tcp_true_for_an_open_port():
    async def _handle(reader, writer):
        writer.close()

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        assert await reachability.probe_tcp("127.0.0.1", port) is True
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_probe_tcp_false_for_a_closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert await reachability.probe_tcp("127.0.0.1", port) is False


@pytest.mark.asyncio
async def test_probe_tcp_false_after_the_timeout(monkeypatch):
    async def _hang(*_a, **_k):
        await asyncio.sleep(30)

    monkeypatch.setattr(asyncio, "open_connection", _hang)
    started = asyncio.get_running_loop().time()
    assert await reachability.probe_tcp("10.255.255.1", 22, timeout_s=0.05) is False
    assert asyncio.get_running_loop().time() - started < 2


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["", "a" * 300, "nicht-aufloesbar.invalid"])
async def test_probe_tcp_false_for_unusable_addresses(address):
    assert await reachability.probe_tcp(address, 22, timeout_s=1.0) is False


# ---------------------------------------------------------------------------
# Welche Server geprueft werden
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_targets_takes_manual_hosts_with_the_credential_port(db_session):
    plain = await _host(db_session, "plain", "10.0.0.1")
    custom = await _host(db_session, "custom", "10.0.0.2")
    await _credential(db_session, custom, port=2222)

    targets = {t.host_id: t for t in await reachability.select_targets(db_session)}

    assert targets[plain.id].port == 22  # ohne Zugang gilt der Standard-SSH-Port
    assert (targets[custom.id].address, targets[custom.id].port) == ("10.0.0.2", 2222)


@pytest.mark.asyncio
async def test_select_targets_skips_module_maintained_hosts(db_session):
    manual = await _host(db_session, "manual", "10.0.0.1")
    await _host(db_session, "guest", "10.0.0.2", provider_ext_id="irgendein-modul", provider_ref="vm/100")

    assert [t.host_id for t in await reachability.select_targets(db_session)] == [manual.id]


@pytest.mark.asyncio
async def test_select_targets_skips_demo_hosts(db_session):
    await demo_seed.seed_demo_data(db_session)
    await db_session.commit()
    assert await reachability.select_targets(db_session) == []

    real = await _host(db_session, "echt", "10.0.0.9")
    assert [t.host_id for t in await reachability.select_targets(db_session)] == [real.id]


@pytest.mark.asyncio
async def test_a_demo_host_turned_into_a_real_one_is_checked(db_session):
    await demo_seed.seed_demo_data(db_session)
    await db_session.commit()
    demo = (await db_session.execute(select(Host).where(Host.name == "demo-nas"))).scalar_one()
    demo.address = "10.0.0.77"
    await db_session.commit()

    assert [t.address for t in await reachability.select_targets(db_session)] == ["10.0.0.77"]


@pytest.mark.asyncio
async def test_select_targets_skips_disabled_maintenance_blank_address_and_api_access(db_session):
    await _host(db_session, "aus", "10.0.0.1", enabled=False)
    await _host(db_session, "wartung", "10.0.0.2", status="maintenance")
    await _host(db_session, "leer", "   ")
    api_host = await _host(db_session, "api", "10.0.0.4")
    await _credential(db_session, api_host, kind="api_token", port=8006)
    ok = await _host(db_session, "ok", "10.0.0.5")

    assert [t.host_id for t in await reachability.select_targets(db_session)] == [ok.id]


# ---------------------------------------------------------------------------
# Zustand und Entprellung
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_down_needs_two_failures_in_a_row_and_up_is_immediate(db_session, reach, channel):
    host = await _host(db_session, status="up", last_seen_at=utcnow() - timedelta(hours=1))
    reach["10.0.0.5"] = False

    first = await reachability.run_once()
    assert (await _state(db_session, host)).status == "up"  # ein Aussetzer reicht nicht
    assert first["changed"] == 0 and channel.received == []

    second = await reachability.run_once()
    assert (await _state(db_session, host)).status == "down"
    assert second["changed"] == 1 and second["down"] == 1

    reach["10.0.0.5"] = True
    await reachability.run_once()
    host = await _state(db_session, host)
    assert host.status == "up"  # sofort
    assert host.last_seen_at is not None and utcnow() - host.last_seen_at < timedelta(minutes=1)


@pytest.mark.asyncio
async def test_a_single_failure_between_successes_never_flips_the_state(db_session, reach, channel):
    host = await _host(db_session, status="up")
    for answer in (False, True, False, True, False):
        reach["10.0.0.5"] = answer
        await reachability.run_once()
    assert (await _state(db_session, host)).status == "up"
    assert await _notes(db_session) == []


@pytest.mark.asyncio
async def test_unknown_becomes_up_on_the_first_success_without_a_message(db_session, reach, channel):
    host = await _host(db_session)
    assert host.status == "unknown"
    await reachability.run_once()
    host = await _state(db_session, host)
    assert host.status == "up" and host.last_seen_at is not None
    assert channel.received == []


@pytest.mark.asyncio
async def test_last_seen_is_not_set_on_failure(db_session, reach):
    host = await _host(db_session)
    reach["10.0.0.5"] = False
    await reachability.run_once()
    await reachability.run_once()
    host = await _state(db_session, host)
    assert host.status == "down" and host.last_seen_at is None


@pytest.mark.asyncio
async def test_changing_the_address_restarts_the_failure_count(db_session, reach):
    host = await _host(db_session, status="up")
    reach["10.0.0.5"] = False
    reach["10.0.0.6"] = False
    await reachability.run_once()
    host.address = "10.0.0.6"
    await db_session.commit()
    await reachability.run_once()  # erster Fehlschlag fuer die NEUE Adresse
    assert (await _state(db_session, host)).status == "up"
    await reachability.run_once()
    assert (await _state(db_session, host)).status == "down"


# ---------------------------------------------------------------------------
# Meldungen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outage_and_recovery_are_reported_once_each(db_session, reach, channel):
    host = await _host(db_session, "pi", "10.0.0.5", display_name="Bastel-Pi", status="up", last_seen_at=utcnow())
    host_id = host.id
    reach["10.0.0.5"] = False
    for _ in range(4):  # der Zustand bleibt "down", gemeldet wird nur der Wechsel
        await reachability.run_once()

    notes = await _notes(db_session)
    assert [n.title for n in notes] == ["Server Bastel-Pi ist nicht erreichbar"]
    assert notes[0].severity == "critical" and notes[0].source_ext_id is None
    assert notes[0].payload["path"] == f"/hosts/{host_id}" and notes[0].payload["host_id"] == host_id
    assert [n.title for n in channel.received] == ["Server Bastel-Pi ist nicht erreichbar"]

    reach["10.0.0.5"] = True
    await reachability.run_once()
    await reachability.run_once()

    notes = await _notes(db_session)
    assert [n.title for n in notes] == ["Server Bastel-Pi ist nicht erreichbar", "Server Bastel-Pi ist wieder erreichbar"]
    assert notes[1].severity == "info" and notes[1].payload["path"] == f"/hosts/{host_id}"
    assert len(channel.received) == 2


@pytest.mark.asyncio
async def test_a_never_seen_server_gets_the_state_but_no_message(db_session, reach, channel):
    host = await _host(db_session, "tippfehler", "10.9.9.9")  # unknown, last_seen_at leer
    reach["10.9.9.9"] = False
    await reachability.run_once()
    await reachability.run_once()
    assert (await _state(db_session, host)).status == "down"
    assert await _notes(db_session) == [] and channel.received == []

    # Wird er spaeter erreichbar, gibt es auch keine "wieder erreichbar"-Meldung (kein gemeldeter Ausfall).
    reach["10.9.9.9"] = True
    await reachability.run_once()
    assert (await _state(db_session, host)).status == "up"
    assert await _notes(db_session) == []


@pytest.mark.asyncio
async def test_unknown_with_an_earlier_sighting_is_reported_when_it_goes_down(db_session, reach, channel):
    await _host(db_session, "alt", "10.0.0.8", last_seen_at=utcnow() - timedelta(days=1))  # unknown, aber schon mal gesehen
    reach["10.0.0.8"] = False
    await reachability.run_once()
    await reachability.run_once()
    assert len(await _notes(db_session)) == 1


@pytest.mark.asyncio
async def test_a_down_state_set_elsewhere_is_not_announced_as_a_recovery(db_session, reach, channel):
    # z. B. durch "Verbindung pruefen" auf "down" gesetzt: wir haben keinen Ausfall gemeldet.
    host = await _host(db_session, status="down", last_seen_at=utcnow() - timedelta(hours=2))
    await reachability.run_once()
    assert (await _state(db_session, host)).status == "up"
    assert await _notes(db_session) == []


@pytest.mark.asyncio
async def test_maintenance_window_mutes_the_message_but_updates_the_state(db_session, reach, channel):
    from zoneinfo import ZoneInfo

    from nodvard_deck.config import LOCAL_TIMEZONE

    start = (utcnow() - timedelta(minutes=10)).astimezone(ZoneInfo(LOCAL_TIMEZONE))
    await settings_service.set_global(
        db_session, "maintenance.windows",
        [{"cron": f"{start.minute} {start.hour} * * *", "duration_minutes": 120, "host_ids": "all"}],
    )
    host = await _host(db_session, status="up", last_seen_at=utcnow())
    host_id = host.id
    reach["10.0.0.5"] = False
    await reachability.run_once()
    await reachability.run_once()

    assert (await _state(db_session, host)).status == "down"  # Zustand stimmt trotzdem
    assert channel.received == []  # kein Push im Fenster ...
    assert len(await _notes(db_session)) == 1  # ... aber der Verlaufseintrag bleibt
    stored = await settings_service.get_global(db_session, reachability.KEY_STATE)
    assert stored == {host_id: {"muted": True}}


@pytest.mark.asyncio
async def test_a_muted_outage_is_announced_once_after_the_window(db_session, reach, channel):
    host = await _host(db_session, "pi", "10.0.0.5", display_name="Pi", status="down", last_seen_at=utcnow())
    await settings_service.set_global(db_session, reachability.KEY_STATE, {host.id: {"muted": True}})
    await db_session.commit()
    reach["10.0.0.5"] = False
    await reachability.run_once()  # kein Fenster mehr (nie eingestellt): Nachmeldung

    assert [n.title for n in channel.received] == ["Server Pi ist nicht erreichbar" + reachability.DOWN_AFTER_WINDOW]
    await reachability.run_once()
    assert len(channel.received) == 1  # nur einmal
    assert await settings_service.get_global(db_session, reachability.KEY_STATE) == {host.id: {"muted": False}}


@pytest.mark.asyncio
async def test_a_muted_outage_stays_quiet_while_the_window_runs(db_session, reach, channel):
    from zoneinfo import ZoneInfo

    from nodvard_deck.config import LOCAL_TIMEZONE

    start = (utcnow() - timedelta(minutes=10)).astimezone(ZoneInfo(LOCAL_TIMEZONE))
    await settings_service.set_global(
        db_session, "maintenance.windows",
        [{"cron": f"{start.minute} {start.hour} * * *", "duration_minutes": 120, "host_ids": "all"}],
    )
    host = await _host(db_session, status="down", last_seen_at=utcnow())
    await settings_service.set_global(db_session, reachability.KEY_STATE, {host.id: {"muted": True}})
    await db_session.commit()
    reach["10.0.0.5"] = False
    await reachability.run_once()
    assert channel.received == [] and await _notes(db_session) == []


@pytest.mark.asyncio
async def test_state_of_deleted_hosts_is_cleaned_up(db_session, reach):
    await _host(db_session)
    await settings_service.set_global(db_session, reachability.KEY_STATE, {"weg": {"muted": False}})
    await db_session.commit()
    await reachability.run_once()
    assert await settings_service.get_global(db_session, reachability.KEY_STATE) == {}


@pytest.mark.asyncio
async def test_status_changes_are_published_on_the_event_bus(db_session, reach):
    seen = []

    async def _collect(event):
        seen.append(event)

    bus = get_event_bus()
    bus.subscribe("host.*", _collect)
    try:
        host = await _host(db_session)
        await reachability.run_once()  # unknown -> up
        await reachability.run_once()  # keine Aenderung, kein Ereignis
    finally:
        bus.unsubscribe("host.*", _collect)
    assert [(e.name, e.payload) for e in seen] == [
        ("host.status_changed", {"host_id": host.id, "status": "up", "previous": "unknown"})
    ]


@pytest.mark.asyncio
async def test_module_maintained_and_demo_hosts_keep_their_state(db_session, reach, channel):
    guest = await _host(db_session, "guest", "10.0.0.2", status="up", provider_ext_id="irgendein-modul", provider_ref="vm/1")
    reach["10.0.0.2"] = False
    for _ in range(3):
        await reachability.run_once()
    assert (await _state(db_session, guest)).status == "up"
    assert channel.received == []


@pytest.mark.asyncio
async def test_demo_hosts_keep_the_state_they_were_created_with(db_session, monkeypatch, channel):
    async def never(*_a, **_k):
        raise AssertionError("Beispiel-Server duerfen nicht geprueft werden")

    monkeypatch.setattr(reachability, "probe_tcp", never)
    await demo_seed.seed_demo_data(db_session)
    await db_session.commit()
    before = {h.name: h.status for h in (await db_session.execute(select(Host))).scalars()}
    for _ in range(3):
        result = await reachability.run_once()
    assert result["of"] == 0
    db_session.expire_all()
    assert {h.name: h.status for h in (await db_session.execute(select(Host))).scalars()} == before


# ---------------------------------------------------------------------------
# Parallelitaet, Lock, Zeitgrenze
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_at_most_ten_probes_run_at_the_same_time(db_session, monkeypatch):
    for i in range(25):
        db_session.add(Host(name=f"h{i:02d}", display_name=f"H{i}", address=f"10.1.0.{i + 1}"))
    await db_session.commit()
    running = peak = 0

    async def slow(address: str, port: int, timeout_s: float = 3.0) -> bool:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return True

    monkeypatch.setattr(reachability, "probe_tcp", slow)
    result = await reachability.run_once()
    assert result["checked"] == 25
    assert 1 < peak <= reachability.MAX_PARALLEL


@pytest.mark.asyncio
async def test_a_second_run_is_skipped_while_one_is_running(db_session, monkeypatch):
    await _host(db_session)
    gate = asyncio.Event()
    started = asyncio.Event()

    async def blocked(address: str, port: int, timeout_s: float = 3.0) -> bool:
        started.set()
        await gate.wait()
        return True

    monkeypatch.setattr(reachability, "probe_tcp", blocked)
    first = asyncio.create_task(reachability.run_once())
    await started.wait()

    assert await reachability.run_once() == {"skipped": True}

    gate.set()
    assert (await first)["checked"] == 1
    assert (await reachability.run_once()).get("skipped") is None  # danach laeuft es wieder


@pytest.mark.asyncio
async def test_probing_never_waits_longer_than_the_budget():
    async def forever(*_a, **_k):
        await asyncio.sleep(30)

    original = reachability.probe_tcp
    reachability.probe_tcp = forever  # type: ignore[assignment]
    try:
        targets = [reachability.Target("h1", "H1", "10.0.0.1", 22)]
        started = asyncio.get_running_loop().time()
        assert await reachability._probe_all(targets, budget_s=0.05) == {}
        assert asyncio.get_running_loop().time() - started < 2
    finally:
        reachability.probe_tcp = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_an_unanswered_probe_counts_as_neither_up_nor_down(db_session, monkeypatch):
    host = await _host(db_session, status="up")

    async def forever(*_a, **_k):
        await asyncio.sleep(30)

    async def quick_budget(targets, budget_s):
        return await original(targets, 0.05)

    original = reachability._probe_all
    monkeypatch.setattr(reachability, "probe_tcp", forever)
    monkeypatch.setattr(reachability, "_probe_all", quick_budget)
    for _ in range(3):
        result = await reachability.run_once()
    assert result["checked"] == 0
    assert (await _state(db_session, host)).status == "up"


# ---------------------------------------------------------------------------
# Kern-Job, Einstellungen
# ---------------------------------------------------------------------------


def test_cron_for_the_interval():
    assert reachability.cron_for(1) == "*/1 * * * *"
    assert reachability.cron_for(2) == "*/2 * * * *"
    assert reachability.cron_for(45) == "*/45 * * * *"
    assert reachability.cron_for(60) == "0 * * * *"


@pytest.mark.asyncio
async def test_sync_job_creates_the_core_job_with_defaults(db_session):
    await reachability.sync_job()
    job = await jobs_service.get_job_by_key(db_session, ext_id=None, ext_job_key=reachability.JOB_KEY)
    assert job is not None and job.kind == "core"
    assert job.enabled is True and job.schedule == "*/2 * * * *"
    assert get_scheduler_service()._scheduler.get_job(job.id) is not None
    assert get_extension_runtime().scheduler.get("__core__", reachability.JOB_KEY) is not None


@pytest.mark.asyncio
async def test_unusable_stored_values_fall_back_to_the_defaults(db_session):
    await settings_service.set_global(db_session, reachability.KEY_ENABLED, "ja")
    await settings_service.set_global(db_session, reachability.KEY_INTERVAL, 999)
    config = await reachability.load_config(db_session)
    assert (config.enabled, config.interval_minutes) == (True, 2)


async def _owner_token(client) -> dict:
    await client.post(
        "/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"}
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_settings_list_shows_the_defaults(client):
    headers = await _owner_token(client)
    rows = {r["key"]: r["value"] for r in (await client.get("/api/v1/settings", headers=headers)).json()}
    assert rows["hosts.reachability.enabled"] is True
    assert rows["hosts.reachability.interval_minutes"] == 2
    assert "hosts.reachability.state" not in rows  # interner Merker


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, -1, 61, 1000, 2.5, "5", None, True, [2]])
async def test_interval_rejects_invalid_values_with_422(client, value):
    headers = await _owner_token(client)
    r = await client.put("/api/v1/settings/hosts.reachability.interval_minutes", json={"value": value}, headers=headers)
    assert r.status_code == 422
    rows = {x["key"]: x["value"] for x in (await client.get("/api/v1/settings", headers=headers)).json()}
    assert rows["hosts.reachability.interval_minutes"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["true", 1, 0, None, "an"])
async def test_enabled_rejects_non_booleans_with_422(client, value):
    headers = await _owner_token(client)
    r = await client.put("/api/v1/settings/hosts.reachability.enabled", json={"value": value}, headers=headers)
    assert r.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1, 2, 30, 60])
async def test_interval_accepts_the_whole_range(client, value):
    headers = await _owner_token(client)
    r = await client.put("/api/v1/settings/hosts.reachability.interval_minutes", json={"value": value}, headers=headers)
    assert r.status_code == 200 and r.json()["value"] == value


@pytest.mark.asyncio
async def test_changing_the_interval_reschedules_the_job(client, db_session):
    headers = await _owner_token(client)
    get_scheduler_service().start()  # laeuft der Scheduler nicht, merkt er sich "ersetzen" erst beim Start
    await reachability.sync_job()
    job = await jobs_service.get_job_by_key(db_session, ext_id=None, ext_job_key=reachability.JOB_KEY)
    scheduler = get_scheduler_service()._scheduler
    before = str(scheduler.get_job(job.id).trigger)

    r = await client.put("/api/v1/settings/hosts.reachability.interval_minutes", json={"value": 7}, headers=headers)
    assert r.status_code == 200

    await db_session.refresh(job)
    assert job.schedule == "*/7 * * * *" and job.enabled is True
    assert job.next_run_at is not None
    assert str(scheduler.get_job(job.id).trigger) != before
    assert "7" in str(scheduler.get_job(job.id).trigger)


@pytest.mark.asyncio
async def test_switching_it_off_and_on_unschedules_and_reschedules(client, db_session):
    headers = await _owner_token(client)
    get_scheduler_service().start()
    await reachability.sync_job()
    job = await jobs_service.get_job_by_key(db_session, ext_id=None, ext_job_key=reachability.JOB_KEY)
    scheduler = get_scheduler_service()._scheduler

    assert (await client.put("/api/v1/settings/hosts.reachability.enabled", json={"value": False}, headers=headers)).status_code == 200
    await db_session.refresh(job)
    assert job.enabled is False and job.next_run_at is None
    assert scheduler.get_job(job.id) is None

    assert (await client.put("/api/v1/settings/hosts.reachability.enabled", json={"value": True}, headers=headers)).status_code == 200
    await db_session.refresh(job)
    assert job.enabled is True and scheduler.get_job(job.id) is not None


@pytest.mark.asyncio
async def test_setting_changes_are_audited(client, db_session):
    headers = await _owner_token(client)
    await client.put("/api/v1/settings/hosts.reachability.interval_minutes", json={"value": 10}, headers=headers)
    await client.put("/api/v1/settings/hosts.reachability.enabled", json={"value": False}, headers=headers)

    db_session.expire_all()
    rows = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "system.settings.changed"))).scalars().all()
    by_key = {r.target_id: r.detail for r in rows}
    assert by_key["hosts.reachability.interval_minutes"]["old"] == 2 and by_key["hosts.reachability.interval_minutes"]["new"] == 10
    assert by_key["hosts.reachability.enabled"]["old"] is True and by_key["hosts.reachability.enabled"]["new"] is False


@pytest.mark.asyncio
async def test_settings_need_the_settings_permission(client):
    r = await client.put("/api/v1/settings/hosts.reachability.enabled", json={"value": False})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_the_jobs_api_leaves_the_switch_to_the_setting(client, db_session):
    headers = await _owner_token(client)
    await reachability.sync_job()
    job = await jobs_service.get_job_by_key(db_session, ext_id=None, ext_job_key=reachability.JOB_KEY)
    for call in (
        client.patch(f"/api/v1/jobs/{job.id}", json={"enabled": False}, headers=headers),
        client.delete(f"/api/v1/jobs/{job.id}", headers=headers),
    ):
        r = await call
        assert r.status_code == 409 and "Server & Zugänge" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Protokoll aufraeumen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prune_runs_removes_only_old_finished_runs(db_session):
    job = await jobs_service.upsert_job(
        db_session, ext_id=None, ext_job_key="x", name="X", kind="core", schedule="* * * * *", params={}, enabled=True,
    )
    old = utcnow() - timedelta(days=3)
    db_session.add_all([
        JobRun(job_id=job.id, trigger="schedule", status="succeeded", started_at=old, output_ref="/a/alt.log"),
        JobRun(job_id=job.id, trigger="schedule", status="running", started_at=old),
        JobRun(job_id=job.id, trigger="schedule", status="succeeded", started_at=utcnow()),
    ])
    await db_session.commit()

    refs = await jobs_service.prune_runs(db_session, job_id=job.id, older_than=utcnow() - timedelta(days=1))
    await db_session.commit()

    assert refs == ["/a/alt.log"]
    left = (await db_session.execute(select(JobRun.status))).scalars().all()
    assert sorted(left) == ["running", "succeeded"]


@pytest.mark.asyncio
async def test_the_job_handler_prunes_its_own_old_runs_and_logs(db_session, tmp_path, reach):
    runs_dir = tmp_path / "runs"
    get_scheduler_service().configure(runs_dir)
    await reachability.sync_job()
    job = await jobs_service.get_job_by_key(db_session, ext_id=None, ext_job_key=reachability.JOB_KEY)
    log = Path(runs_dir / "alt.log")
    log.write_text("alt", encoding="utf-8")
    db_session.add(JobRun(
        job_id=job.id, trigger="schedule", status="succeeded", started_at=utcnow() - timedelta(days=2), output_ref=str(log),
    ))
    await db_session.commit()

    await reachability._job_handler()

    assert not log.exists()
    assert (await db_session.execute(select(JobRun))).scalars().all() == []
