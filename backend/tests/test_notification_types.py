"""Push-Regeln je Meldungsart, API und Kennungen der mitgelieferten Absender."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import NotificationDelivery
from nodvard_deck.services import notifications, settings, update_check
from nodvard_deck.services.notification_types import SETTING_KEY, catalog, defaults
from nodvard_sdk.capabilities import NotificationChannel
from sqlalchemy import select
from test_ext_ntfy import (
    _auth_header,
    _bootstrap_owner,
    _setup_ntfy_extension,
)
from test_ext_ntfy import (
    _cleanup_sys_path as _cleanup_sys_path,  # noqa: PLC0414
)
from test_ext_ntfy import (
    fake_ntfy_server as fake_ntfy_server,  # noqa: PLC0414
)

EXTENSIONS = Path(__file__).resolve().parents[2] / "extensions"
TYPES = catalog(EXTENSIONS)


@pytest.fixture(autouse=True)
def _catalog_directory(test_settings):
    test_settings.extensions_dir = EXTENSIONS


@pytest.fixture(autouse=True)
def _reset_runtime():
    reset_extension_runtime()
    yield
    reset_extension_runtime()


async def _channel(db_session, tmp_path, test_settings, server_url):
    runtime, ctx, _ = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url=server_url)
    channel = runtime.capabilities.query(NotificationChannel)[0]
    get_extension_runtime().capabilities.provide("ntfy", NotificationChannel, channel)
    return ctx


@pytest.mark.parametrize("kind", TYPES)
async def test_disabled_type_keeps_history_without_sending(kind, fake_ntfy_server, db_session, tmp_path, test_settings):
    url, received, _ = fake_ntfy_server
    await _channel(db_session, tmp_path, test_settings, url)
    await settings.set_global(db_session, SETTING_KEY, {kind: {"enabled": False, "priority": "urgent"}})
    row = await notifications.send(
        db_session, title="Meldung", body="Inhalt", payload={"notification_type": kind}, raise_on_failure=True,
    )
    assert received == []
    assert row in await notifications.list_notifications(db_session)
    delivery = (await db_session.execute(
        select(NotificationDelivery).where(NotificationDelivery.notification_id == row.id)
    )).scalar_one()
    assert (delivery.status, delivery.error) == ("skipped", None)


@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "priority"])
async def test_security_update_respects_push_preferences(
    enabled, fake_ntfy_server, db_session, tmp_path, test_settings,
):
    url, received, _ = fake_ntfy_server
    await _channel(db_session, tmp_path, test_settings, url)
    await settings.set_global(db_session, SETTING_KEY, {
        "deck.security_update": {"enabled": enabled, "priority": "low"},
    })
    await db_session.commit()
    found = {"source": "live", "available": True, "latest": "99.0.0", "kinds": ["sicherheit"]}

    await update_check.notify_security(found)
    await update_check.notify_security(found)

    if enabled:
        assert len(received) == 1
        assert received[0]["priority"] == "2"
        assert received[0]["body"]["priority"] == 2
    else:
        assert received == []
    notices = await notifications.list_notifications(db_session)
    assert len(notices) == 1
    assert notices[0].severity == "critical"
    assert notices[0].payload == {
        "path": "/settings/system", "tags": ["lock"], "notification_type": "deck.security_update",
    }
    delivery = (await db_session.execute(
        select(NotificationDelivery).where(NotificationDelivery.notification_id == notices[0].id)
    )).scalar_one()
    assert (delivery.status, delivery.error) == ("sent" if enabled else "skipped", None)


@pytest.mark.parametrize(("priority", "number"), [("min", 1), ("low", 2), ("default", 3), ("high", 4), ("urgent", 5)])
async def test_type_priority_reaches_header_and_json(priority, number, fake_ntfy_server, db_session, tmp_path, test_settings):
    url, received, _ = fake_ntfy_server
    await _channel(db_session, tmp_path, test_settings, url)
    await settings.set_global(db_session, SETTING_KEY, {"host.down": {"enabled": True, "priority": priority}})
    await notifications.send(
        db_session, title="Server nicht erreichbar", body="Inhalt", severity="critical",
        payload={"notification_type": "host.down"},
    )
    assert received[0]["priority"] == str(number)
    assert received[0]["body"]["priority"] == number


@pytest.mark.parametrize("stored", [None, {}, {"host.down": {"enabled": True, "priority": None}}])
async def test_defaults_preserve_existing_severity_priorities(stored, fake_ntfy_server, db_session, tmp_path, test_settings):
    url, received, _ = fake_ntfy_server
    ctx = await _channel(db_session, tmp_path, test_settings, url)
    if stored is not None:
        await settings.set_global(db_session, SETTING_KEY, stored)
    for severity in ("info", "warning", "critical"):
        await notifications.send(db_session, title="Meldung", body="Inhalt", severity=severity,
                                 payload={"notification_type": "host.down"})
    assert [r["priority"] for r in received] == ["3", "4", "5"]
    await ctx.settings.set({"server_url": url, "topic": "test-topic", "priority_warning": "low"})
    await notifications.send(db_session, title="Meldung", body="Inhalt", severity="warning",
                             payload={"notification_type": "host.down"})
    assert received[-1]["priority"] == "2"


async def test_unknown_types_and_test_messages_still_send(fake_ntfy_server, db_session, tmp_path, test_settings):
    url, received, _ = fake_ntfy_server
    await _channel(db_session, tmp_path, test_settings, url)
    await settings.set_global(db_session, SETTING_KEY, {"host.down": {"enabled": False, "priority": "urgent"}})
    for payload in ({}, {"notification_type": "external.custom"}, {"notification_type": "host.down", "test": True}):
        await notifications.send(db_session, title="Meldung", body="Inhalt", payload=payload)
    assert [r["priority"] for r in received] == ["3", "3", "3"]


async def test_explicit_priority_still_respects_the_minimum(fake_ntfy_server, db_session, tmp_path, test_settings):
    url, received, _ = fake_ntfy_server
    ctx = await _channel(db_session, tmp_path, test_settings, url)
    await ctx.settings.set({"server_url": url, "topic": "test-topic", "min_severity": "critical"})
    await settings.set_global(db_session, SETTING_KEY, {"host.up": {"enabled": True, "priority": "urgent"}})
    row = await notifications.send(db_session, title="Server wieder erreichbar", body="Inhalt",
                                   payload={"notification_type": "host.up"})
    assert received == []
    delivery = (await db_session.execute(
        select(NotificationDelivery).where(NotificationDelivery.notification_id == row.id)
    )).scalar_one()
    assert delivery.status == "skipped"


async def test_api_lists_defaults_and_persists_each_type(client):
    headers = _auth_header(await _bootstrap_owner(client))
    types = await client.get("/api/v1/settings/notification-types", headers=headers)
    assert types.status_code == 200
    assert {"id": "deck.security_update", "label": "Deck-Sicherheitsupdate verfügbar"} in types.json()
    assert types.json() == [{"id": key, "label": label} for key, label in TYPES.items()]
    response = await client.get("/api/v1/settings", headers=headers)
    assert next(row["value"] for row in response.json() if row["key"] == SETTING_KEY) == defaults(TYPES)
    assert all(value == {"enabled": True, "priority": None} for value in defaults(TYPES).values())
    value = {kind: {"enabled": False, "priority": "urgent"} for kind in TYPES}
    saved = await client.put(f"/api/v1/settings/{SETTING_KEY}", json={"value": value}, headers=headers)
    assert saved.status_code == 200
    assert saved.json()["value"] == value
    response = await client.get("/api/v1/settings", headers=headers)
    assert next(row["value"] for row in response.json() if row["key"] == SETTING_KEY) == value


@pytest.mark.parametrize("value", [
    [], {"invented": {}}, {"host.down": False}, {"host.down": {"enabled": "false"}},
    {"host.down": {"enabled": 0}}, {"host.down": {"priority": "max"}},
    {"host.down": {"priority": 5}}, {"host.down": {"other": True}},
])
async def test_invalid_preferences_are_rejected_without_overwriting(client, value):
    headers = _auth_header(await _bootstrap_owner(client))
    url = f"/api/v1/settings/{SETTING_KEY}"
    good = {"host.down": {"enabled": False, "priority": "low"}}
    saved = (await client.put(url, json={"value": good}, headers=headers)).json()["value"]
    assert (await client.put(url, json={"value": value}, headers=headers)).status_code == 422
    rows = (await client.get("/api/v1/settings", headers=headers)).json()
    assert next(row["value"] for row in rows if row["key"] == SETTING_KEY) == saved


async def test_type_catalog_requires_settings_permission(client, db_session):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth

    assert (await client.get("/api/v1/settings/notification-types")).status_code == 401
    await _bootstrap_owner(client)
    roles = await auth.ensure_builtin_roles(db_session)
    viewer = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    viewer.roles.append(roles["viewer"])
    db_session.add(viewer)
    await db_session.flush()
    login = await client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"})
    headers = _auth_header(login.json()["access_token"])
    assert (await client.get("/api/v1/settings/notification-types", headers=headers)).status_code == 403
    assert (await client.put(f"/api/v1/settings/{SETTING_KEY}", json={"value": {}}, headers=headers)).status_code == 403


async def test_push_rule_does_not_change_other_types_or_channels(fake_ntfy_server, db_session, tmp_path, test_settings):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    url, received, _ = fake_ntfy_server
    await _channel(db_session, tmp_path, test_settings, url)
    other = SimpleNamespace(channel_id="other", label="Weiterer Kanal", send=AsyncMock(), test=AsyncMock())
    get_extension_runtime().capabilities.provide("other", NotificationChannel, other)
    await settings.set_global(db_session, SETTING_KEY, {"host.down": {"enabled": False, "priority": "urgent"}})
    for kind in ("host.down", "host.up"):
        await notifications.send(db_session, title=kind, body="Inhalt", payload={"notification_type": kind})
    assert [r["body"]["title"] for r in received] == ["host.up"]
    assert other.send.await_count == 2
    assert all("_push_priority" not in call.args[0].payload for call in other.send.await_args_list)


@pytest.mark.parametrize(("module", "key", "kind"), [
    ("proxmox", key, f"proxmox.{key}") for key in ("conn", "node", "disk", "disktemp", "storage", "zfs", "zfsgone")
] + [
    ("synology", key, f"synology.{key}") for key in
    ("pool", "volume", "disk", "disk-temp", "temperature", "update", "backup-state", "backup-failed", "backup-overdue")
])
async def test_watchers_send_registered_types(module, key, kind):
    import importlib
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from nodvard_sdk import Severity

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "extensions" / module / "src"))
    watcher = importlib.import_module(f"nodvard_deck_ext_{module}.watch")
    ctx = SimpleNamespace(notify=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(suppressed=False))))
    if module == "proxmox":
        await watcher._send(ctx, Severity.WARNING, "Titel", "Inhalt", f"{key}:target")
    else:
        await watcher._send(ctx, Severity.WARNING, "Titel", "Inhalt", "nas", f"{key}:target", "/nas")
    message = ctx.notify.send.await_args.args[0]
    assert message.payload["notification_type"] == kind
    assert kind in TYPES


def test_shipped_notification_builders_identify_their_types():
    root = Path(__file__).resolve().parents[2]
    seen = set()
    for path in (root / "extensions").glob("*/src/**/*.py"):
        for call in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "Notification":
                continue
            payload = next((kw.value for kw in call.keywords if kw.arg == "payload"), None)
            assert isinstance(payload, ast.Dict), str(path)
            kind = next((v for k, v in zip(payload.keys, payload.values)
                         if isinstance(k, ast.Constant) and k.value == "notification_type"), None)
            assert kind is not None, str(path)
            if isinstance(kind, ast.Constant):
                assert kind.value in TYPES, (str(path), kind.value)
                seen.add(kind.value)
    assert {key for key in TYPES if key.split(".")[0] not in ("deck", "account", "host", "secret", "proxmox", "synology")
            and key not in ("shield.security_updates", "shield.updates_available", "shield.reboot_required",
                            "shield.update_check_failed")} <= seen


@pytest.mark.parametrize("source", [
    "api/v1/users.py", "services/auth.py", "services/backups.py", "services/update_helper.py", "services/reachability.py",
    "services/update_check.py",
])
def test_core_senders_identify_their_types(source):
    root = Path(__file__).resolve().parents[1] / "src" / "nodvard_deck"
    calls = [call for call in ast.walk(ast.parse((root / source).read_text(encoding="utf-8")))
             if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
             and isinstance(call.func.value, ast.Name) and call.func.value.id == "notifications_service"
             and call.func.attr in ("send", "deliver")]
    assert calls
    for call in calls:
        payload = next((kw.value for kw in call.keywords if kw.arg == "payload"), None)
        assert isinstance(payload, ast.Dict)
        kind = next((v for k, v in zip(payload.keys, payload.values)
                     if isinstance(k, ast.Constant) and k.value == "notification_type"), None)
        assert kind is not None
        values = [kind.body, kind.orelse] if isinstance(kind, ast.IfExp) else [kind]
        assert all(isinstance(value, ast.Constant) and value.value in TYPES for value in values)


def test_catalog_combines_core_types_and_installed_extension_metadata(tmp_path):
    import json

    from nodvard_deck.services.notification_types import TYPES as core_types

    assert catalog(tmp_path) == core_types
    extension = tmp_path / "example"
    extension.mkdir()
    (extension / "notification-types.json").write_text(json.dumps({
        "example.finished": "Vorgang abgeschlossen", "host.down": "Überschrieben",
    }), encoding="utf-8")
    assert catalog(tmp_path) == core_types | {"example.finished": "Vorgang abgeschlossen"}
    assert defaults(catalog(tmp_path))["example.finished"] == {"enabled": True, "priority": None}
