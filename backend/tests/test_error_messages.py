import logging
import re
import sys
from contextlib import nullcontext
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from nodvard_sdk.errors import error_message
from sqlalchemy.exc import OperationalError

from nodvard_deck.core.error_text import describe_connection_error


@pytest.fixture(autouse=True)
def extension_imports():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_"):
            del sys.modules[name]


def reference(text):
    match = re.search(r"Fehlerkennung: ([0-9a-f]{32})\.", text)
    assert match, text
    return match[1]


def test_error_message_logs_location_without_exception_values(caplog):
    try:
        raise RuntimeError("Passwort: vertraulich-a; /srv/private/key; SELECT secret")
    except RuntimeError as exc:
        detail = error_message(exc)
    ident = reference(detail)
    assert ident in caplog.text
    assert "RuntimeError" in caplog.text and "test_error_messages.py:" in caplog.text
    assert "vertraulich-a" not in caplog.text + detail
    assert "/srv/private/key" not in detail
    assert "SELECT secret" not in detail
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("error", [
    RuntimeError("vertraulich-b /srv/data/db.sqlite 10.0.0.7 PostgreSQL 18"),
    OperationalError("SELECT secret FROM credentials", {"password": "vertraulich-b"}, RuntimeError("internal")),
])
async def test_unhandled_error_returns_reference_without_reraising(client, caplog, error):
    from nodvard_deck.main import app

    async def fail():
        raise error

    app.router.routes.insert(0, APIRoute("/api/v1/test-error", fail, methods=["GET"]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as request:
        response = await request.get("/api/v1/test-error")
    assert response.status_code == 500
    ident = reference(response.json()["detail"])
    assert ident in caplog.text
    for hidden in ("vertraulich-b", "/srv/data", "10.0.0.7", "PostgreSQL", "SELECT secret"):
        assert hidden not in response.text
    assert "vertraulich-b" not in caplog.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("kind", [RuntimeError, ConnectionResetError, OSError, PermissionError])
def test_connection_error_does_not_repeat_foreign_text(caplog, kind):
    detail = describe_connection_error(kind("vertraulich-c /etc/private.conf"))
    assert reference(detail) in caplog.text
    assert "vertraulich-c" not in caplog.text + detail
    assert "/etc/private.conf" not in detail


@pytest.mark.parametrize("kind", [RuntimeError, TimeoutError, OSError])
def test_action_error_does_not_repeat_foreign_text(caplog, kind):
    from nodvard_deck.core.gate import describe_error

    detail = describe_error(kind("vertraulich-action /srv/private/action"))
    assert reference(detail) in caplog.text
    assert "vertraulich-action" not in caplog.text + detail
    assert "/srv/private" not in detail


async def test_file_source_errors_hide_values_in_response_and_logs(client, caplog):
    from test_api_files import _FakeFileSource, _auth_header, _bootstrap_owner, _register

    class Broken(_FakeFileSource):
        async def stat(self, path):
            raise RuntimeError("vertraulich-d /srv/private/file")

    _register(Broken())
    token = await _bootstrap_owner(client)
    with caplog.at_level(logging.WARNING):
        response = await client.get("/api/v1/files/fake/stat?path=/x", headers=_auth_header(token))
    assert response.status_code == 502
    assert reference(response.json()["detail"]) in caplog.text
    assert "vertraulich-d" not in response.text + caplog.text
    assert "/srv/private/file" not in response.text


@pytest.mark.parametrize("phase", ["import", "setup", "on_start"])
async def test_extension_load_errors_do_not_store_exception_values(client, db_session, test_settings, monkeypatch, caplog, phase):
    from test_extensions_api import _auth_header, _bootstrap_owner, _discover_real_hello_world
    from nodvard_deck.services import extensions

    token = await _bootstrap_owner(client)
    await _discover_real_hello_world(db_session, test_settings)
    original = extensions.importlib.import_module

    def load(name, *args, **kwargs):
        module = original(name, *args, **kwargs)
        if name != "nodvard_deck_ext_hello_world":
            return module
        if phase == "import":
            raise RuntimeError("vertraulich-e /srv/private/module")

        async def fail(self, ctx):
            raise RuntimeError("vertraulich-e /srv/private/module")

        monkeypatch.setattr(module.Extension, phase, fail)
        return module

    monkeypatch.setattr(extensions.importlib, "import_module", load)
    response = await client.post("/api/v1/extensions/hello-world/enable", headers=_auth_header(token))
    assert response.status_code == 200
    assert response.json()["state"] == "error"
    ident = reference(response.json()["last_error"])
    assert ident in caplog.text
    assert "vertraulich-e" not in response.text + caplog.text
    stored = await extensions.get_record(db_session, "hello-world")
    assert stored.last_error == response.json()["last_error"]


@pytest.mark.parametrize("route", ["status", "metrics/history"])
async def test_host_provider_errors_hide_exception_values(client, db_session, caplog, route):
    from nodvard_sdk.capabilities import HostProvider, MetricsProvider
    from nodvard_deck.ext.runtime import get_extension_runtime
    from test_verbindungsfehler import _host_with_metrics_provider

    token, host_id = await _host_with_metrics_provider(client, db_session)
    fail = AsyncMock(side_effect=RuntimeError("vertraulich-h /srv/private/metrics 10.0.0.9"))
    provider = SimpleNamespace(history=fail, host_status=fail)
    capability = HostProvider if route == "status" else MetricsProvider
    get_extension_runtime().capabilities.provide("fake-metrics", capability, provider)
    response = await client.get(f"/api/v1/hosts/{host_id}/{route}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == (200 if route == "status" else 502)
    assert reference(response.json()["detail"]) in caplog.text
    assert "vertraulich-h" not in response.text + caplog.text
    assert "/srv/private" not in response.text and "10.0.0.9" not in response.text


async def test_missing_file_does_not_echo_the_internal_path():
    from fastapi import HTTPException
    from nodvard_deck.api.v1.files import _call

    with pytest.raises(HTTPException) as caught:
        await _call(AsyncMock(side_effect=FileNotFoundError("/srv/private/secret"))())
    assert caught.value.status_code == 404
    assert caught.value.detail == "Datei nicht gefunden."


@pytest.mark.parametrize("operation", ["provider", "identity"])
async def test_file_source_background_errors_hide_values(client, caplog, operation):
    from nodvard_sdk.capabilities import FileSourceProvider
    from nodvard_deck.api.v1.files import _all_sources, _identity
    from nodvard_deck.ext.runtime import get_extension_runtime

    fail = AsyncMock(side_effect=RuntimeError("vertraulich-i /srv/private/source"))
    if operation == "provider":
        provider = SimpleNamespace(file_sources=fail, password="vertraulich-i")
        get_extension_runtime().capabilities.provide("fake", FileSourceProvider, provider)
        assert await _all_sources() == []
    else:
        source = SimpleNamespace(source_id="fake", file_identity=fail)
        assert await _identity(source, PurePosixPath("/file")) is None
    assert "request_failed id=" in caplog.text
    assert "vertraulich-i" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("failed_result", [False, True])
async def test_gameserver_status_hides_remote_errors(monkeypatch, caplog, failed_result):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "extensions/gameserver/src"))
    from nodvard_deck_ext_gameserver import GameServerStatus

    failure = "vertraulich-j /srv/private/game"
    run = AsyncMock(return_value=SimpleNamespace(exit_code=1, stderr=failure)) if failed_result else AsyncMock(side_effect=RuntimeError(failure))
    read_access = MagicMock(return_value=nullcontext())
    ctx = SimpleNamespace(settings=SimpleNamespace(get=AsyncMock(return_value={})), exec=SimpleNamespace(run=run, read_access=read_access))
    host = SimpleNamespace(id="host", status="up")
    result = await GameServerStatus(ctx).get(host)
    read_access.assert_called_once_with(host, timeout_s=45)
    run.assert_awaited_once()
    assert result["running"] is False
    assert reference(result["error"]) in caplog.text
    assert "vertraulich-j" not in str(result) + caplog.text


@pytest.mark.parametrize("failed_result", [False, True])
async def test_system_info_hides_remote_errors(client, db_session, test_settings, monkeypatch, caplog, failed_result):
    from test_extensions_api import _bootstrap_owner, _discover_real_hello_world
    from nodvard_deck.ext.runtime import get_extension_runtime

    token = await _bootstrap_owner(client)
    await _discover_real_hello_world(db_session, test_settings)
    headers = {"Authorization": f"Bearer {token}"}
    enabled = await client.post("/api/v1/extensions/system/enable", headers=headers)
    assert enabled.json()["state"] == "enabled"
    ctx = get_extension_runtime().loaded["system"].ctx
    host = SimpleNamespace(id="host", os_family="linux", has_credential=True, name="host", display_name="host")
    monkeypatch.setattr(ctx.hosts, "get", AsyncMock(return_value=host))
    failure = "vertraulich-k /etc/private/system"
    run = AsyncMock(return_value=SimpleNamespace(stdout="", stderr=failure, exit_code=1)) if failed_result else AsyncMock(side_effect=RuntimeError(failure))
    monkeypatch.setattr(ctx.exec, "run", run)
    response = await client.get("/api/v1/ext/system/hosts/host/info", headers=headers)
    assert response.status_code == 502
    assert reference(response.json()["detail"]) in caplog.text
    assert "vertraulich-k" not in response.text + caplog.text
    assert "/etc/private" not in response.text
