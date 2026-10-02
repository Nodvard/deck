"""Das Notification-Center (docs/03-DATA-MODEL.md §8, docs/02-EXTENSION-API.md §3):
Dispatch an `NotificationChannel`-Capabilities, Zustellprotokoll, symmetrische
Wartungsfenster-Unterdrueckung."""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from nodvard_sdk import Notification as SdkNotification
from nodvard_sdk.capabilities import NotificationChannel
from nodvard_sdk.errors import NotificationNotDelivered
from sqlalchemy import select

from nodvard_deck.config import LOCAL_TIMEZONE
from nodvard_deck.db import utcnow
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import NotificationDelivery
from nodvard_deck.services import notifications as notifications_service
from nodvard_deck.services import settings as settings_service


@pytest.fixture(autouse=True)
def _reset_runtime():
    reset_extension_runtime()
    yield
    reset_extension_runtime()


class _RecordingChannel:
    def __init__(self, channel_id: str, *, fail: bool = False) -> None:
        self.channel_id = channel_id
        self.label = channel_id
        self.fail = fail
        self.received: list[SdkNotification] = []

    async def send(self, notification: SdkNotification) -> None:
        if self.fail:
            raise RuntimeError(f"{self.channel_id} ist nicht erreichbar")
        self.received.append(notification)

    async def test(self):  # noqa: ANN201 - ConnectorHealth, hier nicht gebraucht
        raise NotImplementedError


def _provide(channel: _RecordingChannel, *, ext_id: str = "chan-ext") -> None:
    get_extension_runtime().capabilities.provide(ext_id, NotificationChannel, channel)


@pytest.mark.asyncio
async def test_send_creates_row_and_delivers_to_all_channels(db_session):
    a = _RecordingChannel("chan-a")
    b = _RecordingChannel("chan-b")
    _provide(a, ext_id="ext-a")
    _provide(b, ext_id="ext-b")

    row = await notifications_service.send(db_session, title="T", body="B", severity="warning")

    assert row.title == "T"
    assert len(a.received) == 1
    assert len(b.received) == 1

    deliveries = (
        await db_session.execute(select(NotificationDelivery).where(NotificationDelivery.notification_id == row.id))
    ).scalars().all()
    assert {d.channel_id: d.status for d in deliveries} == {"chan-a": "sent", "chan-b": "sent"}


@pytest.mark.asyncio
async def test_a_failing_channel_does_not_block_others_or_the_notification_row(db_session):
    broken = _RecordingChannel("broken", fail=True)
    healthy = _RecordingChannel("healthy")
    _provide(broken, ext_id="ext-a")
    _provide(healthy, ext_id="ext-b")

    row = await notifications_service.send(db_session, title="T", body="B")

    assert len(healthy.received) == 1
    deliveries = {
        d.channel_id: d
        for d in (
            await db_session.execute(
                select(NotificationDelivery).where(NotificationDelivery.notification_id == row.id)
            )
        ).scalars().all()
    }
    assert deliveries["broken"].status == "failed"
    assert "nicht erreichbar" in deliveries["broken"].error
    assert deliveries["healthy"].status == "sent"


@pytest.mark.asyncio
async def test_raise_on_failure_makes_a_total_delivery_failure_visible_to_the_caller(db_session):
    """Ein Aufrufer, der bei ausgefallenem Kanal erneut versuchen
    will (scripts: Skip-Meldung), sieht den Fehler nur, wenn er ihn angefordert hat. Die
    Benachrichtigung und das Zustellprotokoll bleiben trotzdem gespeichert."""
    from nodvard_deck.models import Notification as NotificationRow

    _provide(_RecordingChannel("ntfy-dead", fail=True))

    with pytest.raises(NotificationNotDelivered) as info:
        await notifications_service.send(db_session, title="T", body="B", raise_on_failure=True)
    assert info.value.failures == [("ntfy-dead", "ntfy-dead ist nicht erreichbar")]
    assert "ntfy-dead ist nicht erreichbar" in str(info.value)

    [row] = (await db_session.execute(select(NotificationRow))).scalars().all()
    [delivery] = (await db_session.execute(select(NotificationDelivery))).scalars().all()
    assert (delivery.notification_id, delivery.channel_id, delivery.status) == (row.id, "ntfy-dead", "failed")


@pytest.mark.asyncio
async def test_a_failing_channel_stays_silent_by_default(db_session):
    """Rueckwaertskompatibel: ohne `raise_on_failure` schluckt der Kern Kanalfehler wie bisher."""
    _provide(_RecordingChannel("ntfy-dead", fail=True))
    row = await notifications_service.send(db_session, title="T", body="B")
    assert row.id is not None


@pytest.mark.asyncio
async def test_raise_on_failure_only_when_no_channel_delivered(db_session):
    # Ein Kanal hat zugestellt: kein Fehler, auch wenn ein anderer ausfiel.
    healthy = _RecordingChannel("healthy")
    _provide(_RecordingChannel("broken", fail=True), ext_id="ext-a")
    _provide(healthy, ext_id="ext-b")
    await notifications_service.send(db_session, title="T", body="B", raise_on_failure=True)
    assert len(healthy.received) == 1


@pytest.mark.asyncio
async def test_raise_on_failure_is_quiet_without_channels_and_in_maintenance_windows(db_session):
    # Kein Kanal eingerichtet: es gibt nichts, was ein erneuter Versuch bessern koennte.
    row = await notifications_service.send(db_session, title="T", body="B", raise_on_failure=True)
    assert row.id is not None

    # Wartungsfenster: absichtlich nicht zugestellt (suppressed) ist kein Fehler.
    _provide(_RecordingChannel("chan-a", fail=True))
    start = (utcnow() - timedelta(minutes=5)).astimezone(ZoneInfo(LOCAL_TIMEZONE))
    await settings_service.set_global(
        db_session, "maintenance.windows",
        [{"cron": f"{start.minute} {start.hour} * * *", "duration_minutes": 60, "host_ids": "all"}],
    )
    row = await notifications_service.send(
        db_session, title="Host wieder da", body="B", payload={"host_id": "host-1"}, raise_on_failure=True
    )
    assert row.id is not None


@pytest.mark.asyncio
async def test_notify_handle_passes_raise_on_failure_through(db_session):
    from nodvard_deck.ext.context import NotifyHandle, _PermissionChecker

    _provide(_RecordingChannel("ntfy-dead", fail=True))
    handle = NotifyHandle(_PermissionChecker("scripts", ["notify.send"]), "scripts")
    await handle.send(SdkNotification(title="T", body="B"))  # wie bisher: kein Fehler
    with pytest.raises(NotificationNotDelivered):
        await handle.send(SdkNotification(title="T", body="B"), raise_on_failure=True)


@pytest.mark.asyncio
async def test_notification_with_no_channels_still_creates_the_row(db_session):
    row = await notifications_service.send(db_session, title="T", body="B")
    assert row.id is not None


@pytest.mark.asyncio
async def test_maintenance_window_suppresses_delivery_but_keeps_the_row_visible(db_session):
    channel = _RecordingChannel("chan-a")
    _provide(channel)

    # Wartungsfenster meinen Ortszeit, darum Cron aus der Berliner Uhrzeit.
    start = (utcnow() - timedelta(minutes=5)).astimezone(ZoneInfo(LOCAL_TIMEZONE))
    await settings_service.set_global(
        db_session, "maintenance.windows",
        [{"cron": f"{start.minute} {start.hour} * * *", "duration_minutes": 60, "host_ids": "all"}],
    )

    row = await notifications_service.send(
        db_session, title="Host wieder da", body="B", payload={"host_id": "host-1"}
    )

    assert row.id is not None  # sichtbar im In-App-Verlauf
    assert channel.received == []  # aber NICHT extern zugestellt

    delivery = (
        await db_session.execute(select(NotificationDelivery).where(NotificationDelivery.notification_id == row.id))
    ).scalars().one()
    assert delivery.status == "suppressed"


async def _running_window(db_session, host_ids) -> None:
    start = (utcnow() - timedelta(minutes=5)).astimezone(ZoneInfo(LOCAL_TIMEZONE))
    await settings_service.set_global(
        db_session, "maintenance.windows",
        [{"cron": f"{start.minute} {start.hour} * * *", "duration_minutes": 60, "host_ids": host_ids}],
    )


@pytest.mark.asyncio
async def test_collective_notification_is_suppressed_only_if_all_its_hosts_are_in_the_window(db_session):
    """Sammelmeldung (`payload.host_ids`): Fenster fuer beide Server -> still, fehlt einer
    -> geht raus. Der Verlaufseintrag bleibt in beiden Faellen."""
    channel = _RecordingChannel("chan-a")
    _provide(channel)

    await _running_window(db_session, ["host-1", "host-2"])
    quiet = await notifications_service.send(
        db_session, title="Update-Bericht", body="B", payload={"host_ids": ["host-1", "host-2"]}
    )
    assert channel.received == []

    await _running_window(db_session, ["host-1"])
    loud = await notifications_service.send(
        db_session, title="Update-Bericht", body="B", payload={"host_ids": ["host-1", "host-2"]}
    )
    assert [n.title for n in channel.received] == ["Update-Bericht"]

    statuses = {
        d.notification_id: d.status
        for d in (await db_session.execute(select(NotificationDelivery))).scalars().all()
    }
    assert statuses == {quiet.id: "suppressed", loud.id: "sent"}


@pytest.mark.asyncio
@pytest.mark.parametrize("host_ids", [[], "host-1", ["host-1", 7], ["host-1", ""], {"host-1": True}])
async def test_unusable_host_list_is_never_suppressed(db_session, host_ids):
    """Eine leere oder kaputte Liste (auch mit nur einem schlechten Eintrag) macht die
    Meldung hoerbar, statt sie wegen der brauchbaren Eintraege still zu schalten."""
    channel = _RecordingChannel("chan-a")
    _provide(channel)
    await _running_window(db_session, "all")

    await notifications_service.send(db_session, title="T", body="B", payload={"host_ids": host_ids})
    assert [n.title for n in channel.received] == ["T"]


async def _notification_count(db_session) -> int:
    from nodvard_deck.models import Notification

    return len((await db_session.execute(select(Notification))).scalars().all())


@pytest.mark.asyncio
async def test_would_suppress_follows_the_send_rule_and_creates_nothing(db_session):
    """Nachholen nach dem Wartungsfenster: ein Waechter fragt bei jeder Pruefung, ob das
    Fenster noch laeuft -- das darf keinen (stummen) Verlaufseintrag erzeugen."""
    assert await notifications_service.would_suppress(db_session, {"host_id": "host-1"}) is False

    await _running_window(db_session, ["host-1"])
    cases = {
        "ein Server im Fenster": ({"host_id": "host-1"}, True),
        "anderer Server": ({"host_id": "host-2"}, False),
        "ohne Host-Bezug": ({}, False),
        "kein Payload": (None, False),
        "Sammelmeldung, alle im Fenster": ({"host_ids": ["host-1"]}, True),
        "Sammelmeldung, einer draussen": ({"host_ids": ["host-1", "host-2"]}, False),
        "Sammelmeldung als Tupel": ({"host_ids": ("host-1",)}, True),
        "kaputte Liste": ({"host_ids": ["host-1", 7]}, False),
        "String statt Liste": ({"host_ids": "host-1"}, False),
    }
    for label, (payload, expected) in cases.items():
        assert await notifications_service.would_suppress(db_session, payload) is expected, label
    assert await _notification_count(db_session) == 0


@pytest.mark.asyncio
async def test_would_suppress_and_send_treat_a_tuple_of_hosts_alike(db_session):
    """`would_suppress(host_ids=(...))` und `send()` mit demselben Tupel im Payload
    folgen derselben Regel -- sonst sagte die Frage "still", die Meldung kaeme aber laut."""
    from nodvard_deck.ext.context import NotifyHandle, _PermissionChecker

    channel = _RecordingChannel("chan-a")
    _provide(channel)
    await _running_window(db_session, ["host-1", "host-2"])
    handle = NotifyHandle(_PermissionChecker("backups", ["notify.send"]), "backups")

    assert await handle.would_suppress(host_ids=("host-1", "host-2")) is True
    result = await handle.send(SdkNotification(title="T", body="B", payload={"host_ids": ("host-1", "host-2")}))
    assert result.suppressed is True
    assert channel.received == []


@pytest.mark.asyncio
async def test_deliver_reports_whether_the_window_suppressed_the_push(db_session):
    channel = _RecordingChannel("chan-a")
    _provide(channel)
    await _running_window(db_session, ["host-1"])

    quiet, quiet_suppressed = await notifications_service.deliver(
        db_session, title="Still", body="B", payload={"host_id": "host-1"}
    )
    loud, loud_suppressed = await notifications_service.deliver(
        db_session, title="Laut", body="B", payload={"host_id": "host-2"}
    )
    assert (quiet_suppressed, loud_suppressed) == (True, False)
    assert quiet.id and loud.id
    assert [n.title for n in channel.received] == ["Laut"]


@pytest.mark.asyncio
async def test_notify_handle_returns_the_result_and_answers_would_suppress(db_session):
    """SDK-Erweiterung (docs/02 §2): `send()` sagt, ob ein Fenster den Push unterdrueckt
    hat; `would_suppress()` fragt dieselbe Regel ab, ohne etwas anzulegen."""
    from nodvard_sdk import NotifyResult
    from nodvard_sdk.errors import PermissionDenied

    from nodvard_deck.ext.context import NotifyHandle, _PermissionChecker
    from nodvard_deck.models import Notification

    channel = _RecordingChannel("chan-a")
    _provide(channel)
    handle = NotifyHandle(_PermissionChecker("proxmox", ["notify.send"]), "proxmox")

    assert await handle.would_suppress(host_id="host-1") is False
    loud = await handle.send(SdkNotification(title="Laut", body="B", payload={"host_id": "host-1"}))
    assert isinstance(loud, NotifyResult) and loud.suppressed is False

    await _running_window(db_session, ["host-1"])
    before = await _notification_count(db_session)
    assert await handle.would_suppress(host_id="host-1") is True
    assert await handle.would_suppress(host_id="host-2") is False
    assert await handle.would_suppress(host_ids=["host-1"]) is True
    assert await handle.would_suppress(host_ids=("host-1", "host-2")) is False
    assert await handle.would_suppress(host_ids="host-1") is False, "ein String ist keine Liste"
    assert await handle.would_suppress() is False
    assert await _notification_count(db_session) == before, "nur gefragt, nichts angelegt"

    quiet = await handle.send(SdkNotification(title="Still", body="B", payload={"host_id": "host-1"}))
    assert quiet.suppressed is True
    row = await db_session.get(Notification, quiet.notification_id)
    assert (row.title, row.source_ext_id) == ("Still", "proxmox")
    assert [n.title for n in channel.received] == ["Laut"]

    no_perm = NotifyHandle(_PermissionChecker("proxmox", []), "proxmox")
    with pytest.raises(PermissionDenied):
        await no_perm.would_suppress(host_id="host-1")


@pytest.mark.asyncio
async def test_list_and_mark_read(db_session):
    a = await notifications_service.send(db_session, title="A", body="x")
    b = await notifications_service.send(db_session, title="B", body="y")

    unread = await notifications_service.list_notifications(db_session, unread=True)
    assert {n.id for n in unread} == {a.id, b.id}

    count = await notifications_service.mark_read(db_session, [a.id])
    assert count == 1

    still_unread = await notifications_service.list_notifications(db_session, unread=True)
    assert {n.id for n in still_unread} == {b.id}

    # Ein zweites Mal denselben markieren zaehlt nicht erneut.
    assert await notifications_service.mark_read(db_session, [a.id]) == 0


@pytest.mark.asyncio
async def test_channel_writing_via_own_session_is_not_blocked_by_the_notification_row(tmp_path):
    """Der ntfy-Kanal schreibt bei gesetztem Token ueber ctx.vault_use() mit
    einer EIGENEN Verbindung ('secret.used'-Audit). Hielt send() dabei noch die
    Schreibsperre der Notification-Zeile, wartete der Kanal busy_timeout (5 s) ab und
    scheiterte mit 'database is locked'. Gegen eine echte Datei-DB geprueft, weil die
    StaticPool-Fixture alle Sessions ueber EINE Verbindung laufen laesst."""
    import time

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck.config import Settings
    from nodvard_deck.core import audit
    from nodvard_deck.db.session import (
        create_engine_for,
        reset_engine_cache,
        session_scope,
        set_engine_for_testing,
    )
    from nodvard_deck.models import AuditEntry, Base

    class _AuditingChannel(_RecordingChannel):
        async def send(self, notification: SdkNotification) -> None:
            async with session_scope() as own_session:
                await audit.write_entry(
                    own_session, actor_type="extension", actor_id="chan-ext",
                    action="secret.used", outcome="success",
                )
            self.received.append(notification)

    engine = create_engine_for(Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'notify.db'}"))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        set_engine_for_testing(engine)

        channel = _AuditingChannel("chan-a")
        _provide(channel)

        started = time.monotonic()
        async with session_scope() as session:
            row = await notifications_service.send(session, title="T", body="B")
        elapsed = time.monotonic() - started

        assert elapsed < 3, f"Kanal hat {elapsed:.1f} s auf die Schreibsperre gewartet"
        assert len(channel.received) == 1

        async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
            delivery = (
                await verify.execute(
                    select(NotificationDelivery).where(NotificationDelivery.notification_id == row.id)
                )
            ).scalars().one()
            assert delivery.status == "sent", delivery.error
            audits = (
                await verify.execute(select(AuditEntry).where(AuditEntry.action == "secret.used"))
            ).scalars().all()
            assert len(audits) == 1
    finally:
        await engine.dispose()
        reset_engine_cache()


@pytest.mark.asyncio
async def test_notify_result_says_whether_a_channel_really_delivered(db_session):
    """`NotifyResult.delivered`: wahr nur, wenn mindestens ein Kanal zugestellt hat. Ohne Kanal,
    mit nur ausgefallenen Kanälen und im Wartungsfenster ist es falsch -- damit eine Seite nicht
    "gesendet" meldet, obwohl nichts aufs Handy geht."""
    from nodvard_deck.ext.context import NotifyHandle, _PermissionChecker

    handle = NotifyHandle(_PermissionChecker("nexus-soc", ["notify.send"]), "nexus-soc")
    note = SdkNotification(title="T", body="B")

    assert (await handle.send(note)).delivered is False, "kein Kanal vorhanden"

    broken = _RecordingChannel("chan-broken", fail=True)
    _provide(broken, ext_id="ext-broken")
    result = await handle.send(note)
    assert (result.delivered, result.suppressed) == (False, False), "der einzige Kanal ist ausgefallen"

    good = _RecordingChannel("chan-good")
    _provide(good, ext_id="ext-good")
    assert (await handle.send(note)).delivered is True, "ein Kanal reicht"

    await _running_window(db_session, ["host-1"])
    quiet = await handle.send(SdkNotification(title="T", body="B", payload={"host_id": "host-1"}))
    assert (quiet.suppressed, quiet.delivered) == (True, False)
