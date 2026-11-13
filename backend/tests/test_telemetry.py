from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from nodvard_deck.db import utcnow
from nodvard_deck.models import ExtensionRecord, Host, User
from nodvard_deck.services import settings as store
from nodvard_deck.telemetry import service
from nodvard_deck.telemetry.codes import ErrorCode
from nodvard_deck.telemetry.schema import Choices, Report, VOCABULARY
from nodvard_deck.version import __version__


@pytest.fixture
def enabled(test_settings, monkeypatch):
    monkeypatch.delenv("NODVARD_TELEMETRIE", raising=False)
    monkeypatch.setattr(service.platform, "machine", lambda: "aarch64")
    test_settings.telemetry_url = "https://telemetrie.example.invalid"
    test_settings.telemetry_privacy_ready = True
    return test_settings


@pytest.fixture
def transport(monkeypatch, db_session):
    calls = []

    async def request(settings, method, identity, body=None):
        assert not db_session.in_transaction()
        calls.append((method, identity["id"], body))
        return True

    monkeypatch.setattr(service, "_request", request)
    return calls


async def test_default_off_never_contacts_receiver(db_session, enabled, transport):
    state = await service.status(db_session, enabled)
    assert state["choices"] == {"basis": False, "nutzung": False, "fehler": False}
    assert UUID(state["preview"]["id"]).version == 4
    assert set(state["preview"]) == {"id", "schema"}
    assert await service.send(db_session, enabled) == "disabled"
    assert transport == []


@pytest.mark.parametrize("mask", range(8))
async def test_each_stage_is_independent(db_session, enabled, mask):
    choices = Choices(basis=bool(mask & 1), nutzung=bool(mask & 2), fehler=bool(mask & 4))
    value = await service.status(db_session, enabled, choices=choices)
    assert set(value["preview"]) == {"id", "schema"} | {key for key, active in choices.model_dump().items() if active}
    assert not any(value["choices"].values())


async def test_basis_uses_product_and_fixed_values(db_session, enabled):
    enabled.edition = "dock"
    enabled.telemetry_installation = "docker"
    db_session.add(User(username="private-owner", password_hash="unused", is_owner=True, locale="en"))
    await db_session.commit()
    data = await service.status(db_session, enabled, choices=Choices(basis=True))
    assert data["preview"]["basis"] == {"version": __version__, "produkt": "dock", "installation": "docker", "arch": "arm64", "sprache": "en"}


async def test_usage_buckets_hide_names_and_foreign_ids(db_session, enabled):
    for i in range(27):
        db_session.add(Host(name=f"private-host-{i}", address="203.0.113.4"))
    for i in range(8):
        db_session.add(User(username=f"private-user-{i}", password_hash="unused", totp_confirmed_at=utcnow() if i == 0 else None))
    for ext_id in ("docker", "pihole", "private-module", "another-private-module"):
        db_session.add(ExtensionRecord(id=ext_id, version="1", api_version="1", state="enabled"))
    await store.set_global(db_session, "backup.config", {"enabled": True, "dir": "/private/backup"})
    await db_session.commit()
    data = (await service.status(db_session, enabled, choices=Choices(nutzung=True)))["preview"]
    assert data["nutzung"] == {"module": ["docker", "pihole"], "fremd": "2-5", "server": "21+", "benutzer": "6+", "zwei_faktor": True, "sicherungen_aktiv": True}
    assert "private" not in json.dumps(data)
    assert "203.0.113.4" not in json.dumps(data)


@pytest.mark.parametrize(("number", "expected"), [(0, "0"), (1, "1"), (2, "2-5"), (5, "2-5"), (6, "6-20"), (20, "6-20"), (21, "21+"), (10000, "21+")])
def test_server_class_boundaries(number, expected):
    assert service.count_class(number, (0, 1, 5, 20), ("0", "1", "2-5", "6-20", "21+")) == expected


async def test_error_allowlist_and_capping(db_session, enabled):
    state = await service.load(db_session)
    state["errors"] = {"ssh.timeout": 1000, "backup.ziel_nicht_beschreibbar": -4, "private-error": 5}
    await service.save(db_session, state)
    body = await service.report(db_session, enabled, state, Choices(fehler=True))
    assert body["fehler"] == {"arten": {"ssh.timeout": 99, "backup.ziel_nicht_beschreibbar": 0}}
    assert len(json.dumps(body).encode()) < 4096


async def test_schema_rejects_personal_fields_at_every_level(db_session, enabled):
    body = (await service.status(db_session, enabled, choices=Choices(basis=True, nutzung=True, fehler=True)))["preview"]
    forbidden = {"name", "hostname", "host", "ip", "ip_address", "address", "email", "username", "path", "message", "error", "timestamp", "timezone", "uptime", "secret"}
    for target in (body, body["basis"], body["nutzung"], body["fehler"]):
        for key in forbidden:
            target[key] = "private-value"
            with pytest.raises(ValidationError):
                Report.model_validate(body)
            del target[key]

    def check_schema(node):
        if isinstance(node, dict):
            assert not forbidden & set(node.get("properties", {}))
            for child in node.values():
                check_schema(child)
        elif isinstance(node, list):
            for child in node:
                check_schema(child)

    check_schema(Report.model_json_schema())
    for block, key in (("basis", "version"), ("basis", "produkt"), ("basis", "installation"), ("basis", "arch"), ("basis", "sprache"), ("nutzung", "server"), ("nutzung", "benutzer"), ("nutzung", "fremd")):
        old = body[block][key]
        body[block][key] = "private-value"
        with pytest.raises(ValidationError):
            Report.model_validate(body)
        body[block][key] = old
    body["nutzung"]["module"] = ["private-module"]
    with pytest.raises(ValidationError):
        Report.model_validate(body)


def test_vocabulary_matches_shipped_modules():
    root = Path(__file__).resolve().parents[2]
    assert set(VOCABULARY["module"]) == {path.parent.name for path in (root / "extensions").glob("*/extension.toml")}


@pytest.mark.parametrize("success", [True, False])
async def test_24_hour_limit_survives_failure_and_id_rotation(db_session, enabled, monkeypatch, success):
    clock = utcnow()
    monkeypatch.setattr(service, "utcnow", lambda: clock)
    request = AsyncMock(return_value=success)
    monkeypatch.setattr(service, "_request", request)
    await service.configure(db_session, enabled, Choices(basis=True))
    preview = (await service.status(db_session, enabled))["preview"]
    assert await service.send(db_session, enabled) == ("sent" if success else "failed")
    assert request.call_args.args[3] == preview
    clock += timedelta(hours=24, microseconds=-1)
    assert await service.send(db_session, enabled) == "rate_limited"
    assert request.await_count == 1
    clock += timedelta(microseconds=1)
    assert await service.send(db_session, enabled) == ("sent" if success else "failed")
    await service.delete_data(db_session, enabled)
    await service.configure(db_session, enabled, Choices(basis=True))
    assert await service.send(db_session, enabled) == "rate_limited"


@pytest.mark.parametrize("mode", ["url", "privacy", "admin", "prefixed_admin", "insecure_url", "invalid_port"])
async def test_configuration_blocks_optin_and_network(db_session, enabled, transport, monkeypatch, mode):
    await service.configure(db_session, enabled, Choices(basis=True))
    if mode == "url":
        enabled.telemetry_url = ""
    elif mode == "privacy":
        enabled.telemetry_privacy_ready = False
    elif mode == "admin":
        monkeypatch.setenv("NODVARD_TELEMETRIE", "aus")
    elif mode == "prefixed_admin":
        enabled.telemetrie = "aus"
    elif mode == "insecure_url":
        enabled.telemetry_url = "http://telemetrie.example.invalid"
    else:
        enabled.telemetry_url = "https://telemetrie.example.invalid:invalid"
    with pytest.raises(ValueError):
        await service.configure(db_session, enabled, Choices(fehler=True))
    assert await service.send(db_session, enabled) == "disabled"
    await service.configure(db_session, enabled, Choices())
    assert transport == []


async def test_blocked_installation_still_allows_reducing_consent(db_session, enabled, transport):
    await service.configure(db_session, enabled, Choices(basis=True, nutzung=True, fehler=True))
    enabled.telemetrie = "aus"
    value = await service.configure(db_session, enabled, Choices(basis=True, nutzung=True))
    assert value["choices"] == {"basis": True, "nutzung": True, "fehler": False}
    assert await service.send(db_session, enabled) == "disabled"
    assert transport == []


async def test_delete_rotates_disables_and_retries_failed_request(db_session, enabled, transport, monkeypatch):
    await service.configure(db_session, enabled, Choices(basis=True, fehler=True))
    await service.send(db_session, enabled)
    old = await service.load(db_session)
    request = AsyncMock(return_value=False)
    monkeypatch.setattr(service, "_request", request)
    assert await service.delete_data(db_session, enabled) == "delete_pending"
    new = await service.load(db_session)
    assert new["id"] != old["id"] and new["secret"] != old["secret"]
    assert not any(new["choices"].values()) and new["errors"] == {}
    assert new["pending_deletions"][0]["id"] == old["id"]
    request.return_value = True
    assert await service.delete_data(db_session, enabled) == "deleted"
    assert any(call.args[2]["id"] == old["id"] for call in request.call_args_list[1:])
    assert (await service.load(db_session))["pending_deletions"] == []


@pytest.mark.parametrize("send_result", [None, False, True])
async def test_repeated_deletes_only_queue_attempted_ids(db_session, enabled, monkeypatch, send_result):
    request = AsyncMock(return_value=send_result)
    monkeypatch.setattr(service, "_request", request)
    await service.configure(db_session, enabled, Choices(basis=True))
    if send_result is not None:
        assert await service.send(db_session, enabled) == ("sent" if send_result else "failed")
    old = await service.load(db_session)
    pending = [{"id": old["id"], "secret": old["secret"], "url": enabled.telemetry_url}] if send_result is not None else []
    request.reset_mock()
    request.return_value = False
    for _ in range(3):
        assert await service.delete_data(db_session, enabled) == ("delete_pending" if pending else "deleted")
        assert (await service.load(db_session))["pending_deletions"] == pending
    assert request.await_count == (3 if pending else 0)
    assert all(call.args[1:] == ("DELETE", pending[0]) for call in request.call_args_list)


@pytest.mark.parametrize("status_code", [200, 500])
async def test_delete_uses_report_recipient_after_url_change(db_session, enabled, monkeypatch, status_code):
    requests = []
    original = httpx.AsyncClient
    recipient = enabled.telemetry_url

    async def handler(request):
        assert not db_session.in_transaction()
        saved = await store.get_global(db_session, service.KEY)
        await db_session.commit()
        if request.method == "POST":
            assert saved["recipients"] == [recipient]
        else:
            assert saved["pending_deletions"][0]["url"] == recipient
        requests.append(request)
        return httpx.Response(status_code if request.method == "POST" else 200)

    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))
    await service.configure(db_session, enabled, Choices(basis=True))
    assert await service.send(db_session, enabled) == ("sent" if status_code == 200 else "failed")
    old = await service.load(db_session)
    enabled.telemetry_url = "https://new.example.invalid"
    assert await service.delete_data(db_session, enabled) == "deleted"
    assert [(request.method, str(request.url)) for request in requests] == [
        ("POST", recipient + "/v1/bericht"), ("DELETE", recipient + f"/v1/id/{old['id']}"),
    ]
    assert requests[0].headers["X-Nodvard-Delete-Key"] == requests[1].headers["X-Nodvard-Delete-Key"]
    state = await service.load(db_session)
    assert state["pending_deletions"] == [] and state["recipients"] == []
    assert state["id"] != old["id"] and state["secret"] != old["secret"]


async def test_delete_retries_only_failed_recipients(db_session, enabled, monkeypatch):
    clock = utcnow()
    monkeypatch.setattr(service, "utcnow", lambda: clock)
    request = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "_request", request)
    first = enabled.telemetry_url
    second = "https://second.example.invalid/telemetry"
    await service.configure(db_session, enabled, Choices(basis=True))
    for url in (first, second, first + "/"):
        enabled.telemetry_url = url
        assert await service.send(db_session, enabled) == "sent"
        clock += timedelta(days=1)
    old = await service.load(db_session)
    assert old["recipients"] == [first, second]
    enabled.telemetry_url = "https://third.example.invalid"
    request.reset_mock()
    request.side_effect = [False, True]
    assert await service.delete_data(db_session, enabled) == "delete_pending"
    assert [call.args[0].telemetry_url for call in request.call_args_list] == [first, second]
    assert all(call.args[1:] == ("DELETE", {"id": old["id"], "secret": old["secret"], "url": url})
               for call, url in zip(request.call_args_list, (first, second)))
    pending = (await service.load(db_session))["pending_deletions"]
    assert pending == [{"id": old["id"], "secret": old["secret"], "url": first}]
    request.reset_mock()
    request.side_effect = None
    assert await service.delete_data(db_session, enabled) == "deleted"
    request.assert_awaited_once()
    assert request.call_args.args[0].telemetry_url == first
    assert request.call_args.args[1:] == ("DELETE", pending[0])


async def test_cancelled_send_keeps_recipient(db_session, enabled, monkeypatch):
    request = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(service, "_request", request)
    recipient = enabled.telemetry_url
    await service.configure(db_session, enabled, Choices(basis=True))
    with pytest.raises(asyncio.CancelledError):
        await service.send(db_session, enabled)
    enabled.telemetry_url = "https://new.example.invalid"
    request.reset_mock()
    request.side_effect = None
    request.return_value = True
    assert await service.delete_data(db_session, enabled) == "deleted"
    request.assert_awaited_once()
    assert request.call_args.args[0].telemetry_url == recipient


@pytest.mark.parametrize("mode", ["attempted", "pending", "send_again"])
async def test_legacy_deletions_without_recipient_stay_pending(db_session, enabled, monkeypatch, mode):
    state = await service.load(db_session)
    old = {"id": state["id"], "secret": state["secret"]}
    state["attempted"] = mode != "pending"
    state["pending_deletions"] = [old] if mode == "pending" else []
    await service.save(db_session, state)
    request = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "_request", request)
    if mode == "send_again":
        await service.configure(db_session, enabled, Choices(basis=True))
        assert await service.send(db_session, enabled) == "sent"
    request.reset_mock()
    for _ in range(2):
        assert await service.delete_data(db_session, enabled) == "delete_pending"
        pending = (await service.load(db_session))["pending_deletions"]
        assert len(pending) == 1
        assert pending[0]["id"] == old["id"] and pending[0]["secret"] == old["secret"]
        assert not pending[0].get("url")
    assert request.await_count == (1 if mode == "send_again" else 0)


@pytest.mark.parametrize("mode", ["admin", "privacy", "invalid_url"])
async def test_pending_recipient_respects_network_blocks(db_session, enabled, monkeypatch, mode):
    request = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "_request", request)
    await service.configure(db_session, enabled, Choices(basis=True))
    await service.send(db_session, enabled)
    if mode == "admin":
        monkeypatch.setenv("NODVARD_TELEMETRIE", "aus")
    elif mode == "privacy":
        enabled.telemetry_privacy_ready = False
    else:
        state = await service.load(db_session)
        state["recipients"] = ["http://insecure.example.invalid"]
        await service.save(db_session, state)
    request.reset_mock()
    assert await service.delete_data(db_session, enabled) == "delete_pending"
    request.assert_not_awaited()


async def test_daily_schedule_and_bounded_history(db_session, enabled, transport, monkeypatch):
    clock = utcnow()
    monkeypatch.setattr(service, "utcnow", lambda: clock)
    monkeypatch.setattr(service.secrets, "randbelow", lambda upper: 3600)
    await service.configure(db_session, enabled, Choices(nutzung=True))
    assert await service.send(db_session, enabled, scheduled=True) == "not_due"
    for _ in range(34):
        clock += timedelta(hours=25)
        assert await service.send(db_session, enabled, scheduled=True) == "sent"
    state = await service.load(db_session)
    assert len(state["history"]) == 30
    assert all(entry["outcome"] == "sent" for entry in state["history"])


@pytest.mark.parametrize("status_code", [200, 302, 500])
async def test_transport_timeout_redirects_and_delete_key(enabled, monkeypatch, status_code):
    requests = []
    original = httpx.AsyncClient

    def factory(**kwargs):
        assert kwargs == {"timeout": 10, "follow_redirects": False, "trust_env": False}
        return original(**kwargs, transport=httpx.MockTransport(handler))

    def handler(request):
        requests.append(request)
        return httpx.Response(status_code, headers={"Location": "https://other.example.invalid"})

    monkeypatch.setattr(service.httpx, "AsyncClient", factory)
    identity = service._identity()
    body = {"schema": 1, "id": identity["id"]}
    assert await service._request(enabled, "POST", identity, body) is (status_code == 200)
    assert len(requests) == 1
    assert json.loads(requests[0].content) == body
    assert requests[0].headers["X-Nodvard-Delete-Key"] == hmac.new(bytes.fromhex(identity["secret"]), identity["id"].encode(), hashlib.sha256).hexdigest()
    assert await service._request(enabled, "DELETE", identity) is (status_code == 200)
    assert requests[-1].url.path == f"/v1/id/{identity['id']}"


@pytest.mark.parametrize("edition", ["deck", "dock"])
async def test_api_owner_only_preview_and_real_send(client, db_session, enabled, transport, edition):
    from nodvard_deck.api.deps import get_current_user
    from nodvard_deck.main import app

    enabled.edition = edition
    assert (await client.get("/api/v1/telemetry")).status_code == 401
    user = User(id="owner", username="private", password_hash="unused", is_owner=False)
    app.dependency_overrides[get_current_user] = lambda: user
    for method, path, payload in [("GET", "", None), ("PUT", "", {}), ("POST", "/preview", {}), ("POST", "/send", None), ("DELETE", "", None)]:
        response = await client.request(method, "/api/v1/telemetry" + path, json=payload)
        assert response.status_code == 403
    user.is_owner = True
    choices = {"basis": True, "nutzung": True, "fehler": False}
    response = await client.post("/api/v1/telemetry/preview", json=choices)
    assert response.status_code == 200
    preview = response.json()["preview"]
    assert not any(response.json()["choices"].values())
    assert transport == []
    assert (await client.put("/api/v1/telemetry", json=choices)).status_code == 200
    assert (await client.post("/api/v1/telemetry/send")).json() == {"result": "sent"}
    assert transport[0][2] == preview
    assert (await client.post("/api/v1/telemetry/send")).json() == {"result": "rate_limited"}
    assert (await client.delete("/api/v1/telemetry")).json() == {"result": "deleted"}


@pytest.mark.parametrize("error_type", ["permission", "target"])
async def test_backup_error_instrumentation(db_session, monkeypatch, error_type):
    from nodvard_deck.services import backups, notifications

    record = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(service, "record_error", record)
    monkeypatch.setattr(notifications, "send", notify)
    assert await store.get_global(db_session, backups.SETTING_LAST_RUN) is None
    await db_session.commit()
    error = PermissionError("private path") if error_type == "permission" else backups.store.TargetNotWritable("private path")
    await backups._record_failure(error, trigger="manual", user_id=None)
    record.assert_awaited_once_with(ErrorCode.BACKUP_UNWRITABLE)
    last_run = await store.get_global(db_session, backups.SETTING_LAST_RUN)
    assert last_run["ok"] is False and last_run["trigger"] == "manual"
    entries = await backups.audit_service.list_entries(db_session, action="system.backup.failed")
    assert len(entries) == 1 and entries[0].outcome == "failure"
    notify.assert_awaited_once()


async def test_ssh_timeout_instrumentation(db_session, monkeypatch):
    from nodvard_deck.core import ssh

    record = AsyncMock()
    monkeypatch.setattr(service, "record_error", record)
    monkeypatch.setattr(ssh.asyncssh, "connect", AsyncMock(side_effect=TimeoutError("private address")))
    target = ssh.ConnectionTarget(host_id="unknown", address="203.0.113.5", port=22, username="private", kind="ssh_password", secret_value="unused")
    with pytest.raises(ssh.SshTimeout):
        await ssh.connect(db_session, target)
    record.assert_awaited_once_with(ErrorCode.SSH_TIMEOUT)


async def test_concurrent_sends_share_one_limit(db_session, enabled, transport):
    await service.configure(db_session, enabled, Choices(nutzung=True))
    results = await asyncio.gather(service.send(db_session, enabled), service.send(db_session, enabled))
    assert sorted(results) == ["rate_limited", "sent"]
    assert len(transport) == 1


async def test_error_collection_requires_consent_and_caps(db_session, enabled, monkeypatch):
    from nodvard_deck import config

    monkeypatch.setattr(config, "_settings", enabled)
    await service.status(db_session, enabled)
    await db_session.commit()
    await service.record_error(ErrorCode.SSH_TIMEOUT)
    assert (await service.load(db_session))["errors"] == {}
    await service.configure(db_session, enabled, Choices(fehler=True))
    await service.record_error(ErrorCode.SSH_TIMEOUT)
    assert (await service.load(db_session))["errors"] == {"ssh.timeout": 1}
    await service.record_error(ErrorCode.SSH_TIMEOUT)
    assert (await service.load(db_session))["errors"] == {"ssh.timeout": 2}
    await service.record_error("unknown")
    assert (await service.load(db_session))["errors"] == {"ssh.timeout": 2}
    state = await service.load(db_session)
    state["errors"] = {"ssh.timeout": 99}
    await service.save(db_session, state)
    await service.record_error(ErrorCode.SSH_TIMEOUT)
    await service.record_error("unknown")
    assert (await service.load(db_session))["errors"] == {"ssh.timeout": 99}
    await service.configure(db_session, enabled, Choices())
    assert (await service.load(db_session))["errors"] == {}


async def test_unknown_architecture_does_not_invent_a_value(db_session, enabled, monkeypatch, transport):
    monkeypatch.setattr(service.platform, "machine", lambda: "unknown")
    value = await service.status(db_session, enabled, choices=Choices(basis=True))
    assert value["preview"] is None and value["preview_error"] == "unsupported_arch"
    with pytest.raises(ValueError):
        await service.configure(db_session, enabled, Choices(basis=True))
    assert await service.send(db_session, enabled) == "disabled"
    assert transport == []


def test_choices_reject_free_values():
    for payload in ({"basis": "yes"}, {"basis": 1}, {"level": 1}):
        with pytest.raises(ValidationError):
            Choices.model_validate(payload)


async def test_background_task_respects_saved_deadline(db_session, enabled, transport, monkeypatch):
    from nodvard_deck import config

    clock = utcnow()
    monkeypatch.setattr(config, "_settings", enabled)
    monkeypatch.setattr(service, "utcnow", lambda: clock)
    monkeypatch.setattr(service.secrets, "randbelow", lambda upper: 3600)
    await service.configure(db_session, enabled, Choices(nutzung=True))
    await db_session.commit()
    monkeypatch.setattr(service.asyncio, "sleep", AsyncMock(side_effect=[None, asyncio.CancelledError]))
    with pytest.raises(asyncio.CancelledError):
        await service.run_daily()
    assert transport == []
    clock += timedelta(hours=1)
    monkeypatch.setattr(service.asyncio, "sleep", AsyncMock(side_effect=[None, asyncio.CancelledError]))
    with pytest.raises(asyncio.CancelledError):
        await service.run_daily()
    assert len(transport) == 1


async def test_network_errors_do_not_log_private_details(enabled, monkeypatch, caplog):
    original = httpx.AsyncClient

    def handler(request):
        raise httpx.ConnectError("private-hostname.example", request=request)

    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))
    with caplog.at_level("INFO", logger="nodvard_deck.telemetry"):
        assert not await service._request(enabled, "POST", service._identity(), {"schema": 1})
    assert "Empfänger nicht erreichbar" in caplog.text
    assert "private-hostname" not in caplog.text
