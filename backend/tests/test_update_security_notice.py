"""Sicherheitsupdates: rote Meldung einmal je Version und der Schalter `system.update_check.auto_security`."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from nodvard_deck.core import rate_limit, updates
from nodvard_deck.db.session import get_engine, session_scope
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models.platform import Notification, NotificationDelivery
from nodvard_deck.services import notifications as notifications_service
from nodvard_deck.services import settings as settings_service
from nodvard_deck.services import update_check
from nodvard_deck.services.update_check import SECURITY_TITLE
from nodvard_deck.version import __version__
from nodvard_sdk import Notification as SdkNotification
from nodvard_sdk.capabilities import NotificationChannel
from sqlalchemy import select

OWNER_PW = "correct-horse-battery"


def _bump(version: str) -> str:
    major, minor, _patch = version.split("-")[0].split(".")
    return f"{major}.{int(minor) + 1}.0"


NEWER = _bump(__version__)


@pytest.fixture
def notification_channel(client):
    class Channel:
        channel_id = "security-test"
        label = "Sicherheitsmeldungen"
        fail = False

        def __init__(self):
            self.received: list[SdkNotification] = []

        async def send(self, notification: SdkNotification) -> None:
            if self.fail:
                raise RuntimeError("Meldungskanal nicht erreichbar")
            self.received.append(notification)

        async def test(self):
            raise NotImplementedError

    channel = Channel()
    get_extension_runtime().capabilities.provide("security-test", NotificationChannel, channel)
    return channel


@pytest.fixture
def registry(monkeypatch, notification_channel):
    state: dict = {"tags": [__version__, NEWER], "kinds": "sicherheit,behoben", "fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectError("offline", request=request)
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "t"})
        if request.url.path.endswith("/tags/list"):
            return httpx.Response(200, json={"tags": state["tags"]})
        index = {"schemaVersion": 2, "manifests": [], "annotations": {updates.KINDS_ANNOTATION: state["kinds"]}}
        return httpx.Response(200, content=json.dumps(index).encode())

    real = updates._make_client
    monkeypatch.setattr(updates, "_make_client", lambda transport=None: real(httpx.MockTransport(handler)))
    return state


async def _owner(client) -> dict:
    body = {"username": "owner1", "password": OWNER_PW, "setup_code": "TEST-CODE-2345"}
    await client.post("/api/v1/auth/bootstrap", json=body)
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": OWNER_PW})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _check(client, headers) -> dict:
    rate_limit.reset_all()
    r = await client.post("/api/v1/system/updates/check", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _security_notices(client, headers) -> list[dict]:
    rows = (await client.get("/api/v1/notifications", headers=headers)).json()
    return [n for n in rows if n["title"] == SECURITY_TITLE]


@pytest.mark.asyncio
async def test_a_security_update_is_reported_once_in_red(client, registry):
    owner = await _owner(client)
    assert (await _check(client, owner))["kinds"] == ["sicherheit", "behoben"]
    notices = await _security_notices(client, owner)
    assert len(notices) == 1
    assert notices[0]["severity"] == "critical" and NEWER in notices[0]["body"]
    assert notices[0]["payload"]["path"] == "/settings/system"
    await _check(client, owner)
    assert len(await _security_notices(client, owner)) == 1


@pytest.mark.asyncio
async def test_update_check_releases_connection_before_search(file_db, registry, test_settings, monkeypatch):
    async with file_db() as session:
        await settings_service.set_global(session, update_check.KEY_ENABLED, False)
        await settings_service.set_global(session, update_check.KEY_AUTO_SECURITY, True)
        await settings_service.set_global(session, update_check.KEY_CHANNEL, "beta")
        await session.commit()

    held = []
    check = updates.check
    notify = update_check.notify_security

    async def search(*args, **kwargs):
        held.append(get_engine().pool.checkedout())
        return await check(*args, **kwargs)

    async def notify_security(result):
        held.append(get_engine().pool.checkedout())
        await notify(result)

    monkeypatch.setattr(updates, "check", search)
    monkeypatch.setattr(update_check, "notify_security", notify_security)
    async with file_db() as session:
        result = await update_check.check_now(session, test_settings)
        assert held == [0, 0]
        assert not session.in_transaction()
    assert result["enabled"] is False
    assert result["auto_security"] is True
    assert result["channel"] == "beta"
    assert result["source"] == "live"
    async with file_db() as session:
        notice = await session.scalar(select(Notification).where(Notification.title == SECURITY_TITLE))
        assert notice is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["none", "failed"])
async def test_in_app_notice_counts_as_delivered(client, registry, notification_channel, channel):
    capabilities = get_extension_runtime().capabilities
    if channel == "none":
        capabilities.clear_extension("security-test")
        assert capabilities.query(NotificationChannel) == []
    else:
        notification_channel.fail = True
    owner = await _owner(client)

    await _check(client, owner)
    await _check(client, owner)

    notices = await _security_notices(client, owner)
    assert len(notices) == 1
    assert notices[0]["severity"] == "critical" and NEWER in notices[0]["body"]
    assert notification_channel.received == []
    async with session_scope() as session:
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
        assert [d.status for d in deliveries] == ([] if channel == "none" else ["failed"])
        assert await settings_service.get_global(session, update_check.KEY_SECURITY_NOTIFIED) == [NEWER]


@pytest.mark.asyncio
@pytest.mark.parametrize("kinds", ["behoben,neu", ""], ids=["ohne-sicherheit", "unbekannt"])
async def test_no_notice_without_a_known_security_fix(client, registry, kinds):
    registry["kinds"] = kinds
    owner = await _owner(client)
    await _check(client, owner)
    assert await _security_notices(client, owner) == []


@pytest.mark.asyncio
async def test_no_notice_when_offline_or_already_current(client, registry):
    owner = await _owner(client)
    registry["fail"] = True
    await _check(client, owner)
    registry["fail"] = False
    registry["tags"] = [__version__]
    await _check(client, owner)
    assert await _security_notices(client, owner) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["cache", "offline"])
async def test_only_a_live_search_reports(client, registry, source):
    owner = await _owner(client)
    await update_check.notify_security({"source": source, "available": True, "latest": NEWER, "kinds": ["sicherheit"]})
    assert await _security_notices(client, owner) == []


@pytest.mark.asyncio
async def test_two_searches_at_once_report_once(client, registry, monkeypatch):
    owner = await _owner(client)
    real = notifications_service.send

    async def slow_send(*args, **kwargs):
        await asyncio.sleep(0.05)
        return await real(*args, **kwargs)

    monkeypatch.setattr(notifications_service, "send", slow_send)
    found = {"source": "live", "available": True, "latest": NEWER, "kinds": ["sicherheit"]}
    await asyncio.gather(update_check.notify_security(found), update_check.notify_security(found))
    assert len(await _security_notices(client, owner)) == 1


@pytest.mark.asyncio
async def test_a_failed_notice_is_logged_and_tried_again(db_session, monkeypatch, caplog):
    real = notifications_service.send

    async def broken(*args, **kwargs):
        raise RuntimeError("Datenbank weg")

    found = {"source": "live", "available": True, "latest": NEWER, "kinds": ["sicherheit"]}
    monkeypatch.setattr(notifications_service, "send", broken)
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.update_check"):
        await update_check.notify_security(found)
    assert "security_notice_failed" in caplog.text
    monkeypatch.setattr(notifications_service, "send", real)
    await update_check.notify_security(found)
    async with session_scope() as session:
        rows = (await session.execute(select(Notification).where(Notification.title == SECURITY_TITLE))).scalars()
        assert len(rows.all()) == 1


@pytest.mark.asyncio
async def test_a_notice_without_in_app_or_channel_is_retried_after_recovery(client, registry, notification_channel, monkeypatch):
    owner = await _owner(client)
    send = notification_channel.send

    async def remove_notice_and_send(notification):
        async with session_scope() as session:
            ids = (await session.execute(select(Notification.id).where(Notification.title == SECURITY_TITLE))).scalars()
            await notifications_service.delete_notifications(session, list(ids))
        await send(notification)

    monkeypatch.setattr(notification_channel, "send", remove_notice_and_send)
    notification_channel.fail = True
    await _check(client, owner)
    assert notification_channel.received == []
    assert await _security_notices(client, owner) == []
    async with session_scope() as session:
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
        assert deliveries == []
        assert await settings_service.get_global(session, update_check.KEY_SECURITY_NOTIFIED) is None

    notification_channel.fail = False
    monkeypatch.setattr(notification_channel, "send", send)
    await _check(client, owner)
    assert len(notification_channel.received) == 1
    assert notification_channel.received[0].title == SECURITY_TITLE
    async with session_scope() as session:
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
        assert [(d.channel_id, d.status) for d in deliveries] == [("security-test", "sent")]
        assert await settings_service.get_global(session, update_check.KEY_SECURITY_NOTIFIED) == [NEWER]

    await _check(client, owner)
    assert len(notification_channel.received) == 1


@pytest.mark.asyncio
async def test_a_newer_security_update_is_reported_again(client, registry):
    owner = await _owner(client)
    await _check(client, owner)
    registry["tags"] = [__version__, NEWER, _bump(NEWER)]
    await _check(client, owner)
    notices = await _security_notices(client, owner)
    assert len(notices) == 2 and _bump(NEWER) in notices[0]["body"] + notices[1]["body"]


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_marker", [False, True])
async def test_channel_switch_reports_each_security_version_once(client, registry, notification_channel, legacy_marker):
    owner = await _owner(client)
    beta = f"{_bump(NEWER)}-rc1"
    registry["tags"] = [__version__, NEWER, beta]
    assert (await _check(client, owner))["latest"] == NEWER
    if legacy_marker:
        async with session_scope() as session:
            await settings_service.set_global(session, update_check.KEY_SECURITY_NOTIFIED, NEWER)
            await session.commit()

    for channel, latest in [("beta", beta), ("stable", NEWER)]:
        response = await client.put(
            "/api/v1/settings/system.update_check.channel", json={"value": channel}, headers=owner,
        )
        assert response.status_code == 200, response.text
        result = await _check(client, owner)
        assert result["channel"] == channel and result["latest"] == latest

    notices = await _security_notices(client, owner)
    assert len(notices) == 2
    assert all(notice["severity"] == "critical" for notice in notices)
    assert len(notification_channel.received) == 2
    for version in (NEWER, beta):
        assert sum(f"Version {version} " in notice["body"] for notice in notices) == 1
        assert sum(f"Version {version} " in notice.body for notice in notification_channel.received) == 1
    async with session_scope() as session:
        assert await settings_service.get_global(session, update_check.KEY_SECURITY_NOTIFIED) == [NEWER, beta]


@pytest.mark.asyncio
async def test_single_security_marker_is_migrated_without_another_notice(client, registry, notification_channel):
    owner = await _owner(client)
    async with session_scope() as session:
        await settings_service.set_global(session, update_check.KEY_SECURITY_NOTIFIED, NEWER)
        await session.commit()

    await _check(client, owner)

    assert await _security_notices(client, owner) == []
    assert notification_channel.received == []
    async with session_scope() as session:
        assert await settings_service.get_global(session, update_check.KEY_SECURITY_NOTIFIED) == [NEWER]


@pytest.mark.asyncio
async def test_the_switch_needs_the_owner_password_and_shows_in_the_status(client, registry):
    owner = await _owner(client)
    assert (await client.get("/api/v1/system/updates", headers=owner)).json()["auto_security"] is False
    key = "/api/v1/settings/system.update_check.auto_security"
    missing = await client.put(key, json={"value": True}, headers=owner)
    assert missing.status_code == 403 and missing.json()["code"] == "password_missing"
    ok = await client.put(key, json={"value": True, "current_password": OWNER_PW}, headers=owner)
    assert ok.status_code == 200, ok.text
    assert (await client.get("/api/v1/system/updates", headers=owner)).json()["auto_security"] is True
    assert (await _check(client, owner))["auto_security"] is True
    audit = (await client.get("/api/v1/audit?action=system.settings.changed", headers=owner)).json()
    assert any(row.get("target_id") == "system.update_check.auto_security" for row in audit)
