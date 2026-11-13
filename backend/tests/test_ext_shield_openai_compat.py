"""HTTP-Vertrag und Geheimnisschutz des zusätzlichen KI-Anbieters."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, quote_plus

import httpx
import pytest

SRC = Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"


@pytest.fixture(autouse=True)
def extension_path(monkeypatch):
    monkeypatch.syspath_prepend(str(SRC))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


def _token():
    return "test" + "-token"


@asynccontextmanager
async def _context(handler, settings=None, key=None):
    values = settings if settings is not None else {
        "ai_provider": "openai_compat", "openai_compat_url": "https://primary.test",
        "openai_compat_model": "hauptmodell",
    }

    async def get_settings():
        return values

    async def exists(label):
        return label == "shield-openai-compat-key" and key is not None

    async def get_handle(label):
        assert label == "shield-openai-compat-key"
        return object()

    @asynccontextmanager
    async def vault_use(handle):
        yield key

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        yield SimpleNamespace(http=client, settings=SimpleNamespace(get=get_settings),
                              secrets=SimpleNamespace(exists=exists, get_handle=get_handle), vault_use=vault_use)


def _answer(text="Alles gut."):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class _ResponseStream(httpx.AsyncByteStream):
    def __init__(self, body, *, delay=0):
        self.body = body
        self.delay = delay
        self.parts = 0
        self.bytes_sent = 0
        self.closed = False

    async def __aiter__(self):
        yield self.body
        for _ in range(32):
            if self.delay:
                await asyncio.sleep(self.delay)
            chunk = b" " if self.delay else b" " * (64 * 1024)
            self.parts += 1
            self.bytes_sent += len(chunk)
            yield chunk

    async def aclose(self):
        self.closed = True


def _short_deadline(monkeypatch):
    timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda seconds: timeout(min(seconds, 0.05)))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["complete", "health", "cached_health", "models", "test"])
@pytest.mark.parametrize("failure", ["large", "slow", "headers"])
async def test_response_limits_stop_reading_and_close_stream(operation, failure, monkeypatch):
    from nodvard_deck_ext_shield.openai_compat import (
        UNAVAILABLE_TEXT,
        OpenAICompatProvider,
        list_models,
        test_connection,
    )

    if failure != "large":
        _short_deadline(monkeypatch)
    streams = []

    async def answer(request):
        if failure == "headers":
            await asyncio.sleep(0.2)
        body = {"data": [{"id": "hauptmodell"}]} if request.method == "GET" else _answer()
        stream = _ResponseStream(json.dumps(body).encode(), delay=0.01 if failure == "slow" else 0)
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    async with _context(answer) as ctx:
        provider = OpenAICompatProvider(ctx)
        if operation == "complete":
            assert await provider.complete("Frage") == UNAVAILABLE_TEXT
        elif operation == "health":
            assert (await provider.health()).ok is False
        elif operation == "cached_health":
            from nodvard_deck_ext_shield.ai import ConfiguredAIProvider

            assert (await ConfiguredAIProvider(ctx).cached_health()).ok is False
        elif operation == "models":
            result = await list_models(ctx)
            assert result["options"] == [] and result["error"]
        else:
            result = await test_connection(ctx)
            assert result["ok"] is False
            assert result["error_code"] == ("invalid_response" if failure == "large" else "timeout")
    if failure == "headers":
        assert streams == []
        return
    assert len(streams) == 1 and streams[0].closed
    assert streams[0].parts < 32
    assert streams[0].bytes_sent <= 1024 * 1024 + 64 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["complete", "models", "test", "test_models"])
@pytest.mark.parametrize("failure", ["large", "slow", "headers"])
@pytest.mark.parametrize("which", ["primary", "failover"])
async def test_ollama_response_limits_stop_reading_and_close_stream(operation, failure, which, monkeypatch):
    from nodvard_deck_ext_shield.ai import test_connection
    from nodvard_deck_ext_shield.ollama import OllamaProvider, list_models

    if failure != "large":
        _short_deadline(monkeypatch)
    streams = []

    async def answer(request):
        if operation == "test_models" and request.method == "POST":
            return httpx.Response(200, json={"response": "OK"})
        if failure == "headers":
            await asyncio.sleep(0.2)
        body = {"models": [{"name": "hauptmodell"}]} if request.method == "GET" else {"response": "OK"}
        stream = _ResponseStream(json.dumps(body).encode(), delay=0.01 if failure == "slow" else 0)
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    prefix = "ollama_failover" if which == "failover" else "ollama"
    settings = {f"{prefix}_url": "https://ollama.test", f"{prefix}_model": "hauptmodell"}
    async with _context(answer, settings) as ctx:
        if operation == "complete":
            assert "nicht erreichbar" in await OllamaProvider(ctx).complete("Frage")
        elif operation == "models":
            result = await list_models(ctx, which)
            assert result["options"] == [] and result["error"]
        else:
            result = await test_connection(ctx, which)
            if operation == "test_models":
                assert result["ok"] is True and result["models"] == [] and result["models_error"]
            else:
                assert result["ok"] is False
                if failure != "large":
                    assert result["error_code"] == "timeout"
    if failure == "headers":
        assert streams == []
        return
    assert len(streams) == 1 and streams[0].closed
    assert streams[0].parts < 32
    assert streams[0].bytes_sent <= 1024 * 1024 + 64 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["complete", "models"])
async def test_ollama_compressed_response_is_rejected_before_decoding(operation):
    from nodvard_deck_ext_shield.ollama import OllamaProvider, list_models

    body = {"models": [{"name": "hauptmodell"}]} if operation == "models" else {"response": "OK"}
    stream = _ResponseStream(gzip.compress(json.dumps(body).encode() + b" " * (2 * 1024 * 1024)))
    calls = []

    def answer(request):
        calls.append(request)
        return httpx.Response(200, stream=stream, headers={"content-encoding": "gzip"})

    async with _context(answer, {"ollama_url": "https://ollama.test"}) as ctx:
        if operation == "complete":
            assert "nicht erreichbar" in await OllamaProvider(ctx).complete("Frage")
        else:
            result = await list_models(ctx)
            assert result["options"] == [] and result["error"]
    assert stream.closed and stream.parts == 0
    assert calls[0].headers["accept-encoding"] == "identity"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["complete", "health"])
@pytest.mark.parametrize("failure", ["large", "slow"])
async def test_response_limit_allows_fallback(operation, failure, monkeypatch):
    from nodvard_deck_ext_shield.openai_compat import OpenAICompatProvider

    if failure == "slow":
        _short_deadline(monkeypatch)
    stream = _ResponseStream(json.dumps(_answer("Hauptantwort")).encode(), delay=0.01 if failure == "slow" else 0)
    calls = []

    def answer(request):
        calls.append(request.url.host)
        if request.url.host == "primary.test":
            return httpx.Response(200, stream=stream)
        assert stream.closed
        return httpx.Response(200, json=_answer("Ersatzantwort"))

    settings = {"openai_compat_url": "https://primary.test", "openai_compat_model": "haupt",
                "openai_compat_failover_url": "https://fallback.test", "openai_compat_failover_model": "ersatz"}
    async with _context(answer, settings) as ctx:
        provider = OpenAICompatProvider(ctx)
        if operation == "complete":
            assert await provider.complete("Frage") == "[Failover-Modell] Ersatzantwort"
        else:
            health = await provider.health()
            assert health.ok is True and "Ersatzserver" in health.message
    assert calls == ["primary.test", "fallback.test"]
    assert stream.closed and stream.parts < 32


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["complete", "models"])
async def test_compressed_response_is_rejected_before_decoding(operation):
    from nodvard_deck_ext_shield.openai_compat import (
        UNAVAILABLE_TEXT,
        OpenAICompatProvider,
        list_models,
    )

    body = {"data": [{"id": "hauptmodell"}]} if operation == "models" else _answer()
    stream = _ResponseStream(gzip.compress(json.dumps(body).encode() + b" " * (2 * 1024 * 1024)))
    calls = []

    def answer(request):
        calls.append(request)
        return httpx.Response(200, stream=stream, headers={"content-encoding": "gzip"})

    async with _context(answer) as ctx:
        if operation == "complete":
            assert await OpenAICompatProvider(ctx).complete("Frage") == UNAVAILABLE_TEXT
        else:
            result = await list_models(ctx)
            assert result["options"] == [] and result["error"]
    assert stream.closed and stream.parts == 0
    assert calls[0].headers["accept-encoding"] == "identity"


@pytest.mark.asyncio
@pytest.mark.parametrize("method,status", [("POST", 401), ("POST", 302), ("GET", 404), ("GET", 500)])
async def test_error_status_closes_response_without_reading_body(method, status):
    from nodvard_deck_ext_shield.openai_compat import list_models, test_connection

    stream = _ResponseStream(b"")
    async with _context(lambda request: httpx.Response(status, stream=stream)) as ctx:
        if method == "POST":
            result = await test_connection(ctx)
            assert result["ok"] is False and result["error_code"] == ("auth" if status == 401 else "redirect")
        else:
            result = await list_models(ctx)
            assert result["options"] == [] and result["error"]
    assert stream.closed and stream.parts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("method,status", [("POST", 401), ("POST", 302), ("GET", 404), ("GET", 500)])
async def test_ollama_error_status_closes_response_without_reading_body(method, status):
    from nodvard_deck_ext_shield.ai import test_connection
    from nodvard_deck_ext_shield.ollama import list_models

    stream = _ResponseStream(b"")
    async with _context(lambda request: httpx.Response(status, stream=stream),
                        {"ollama_url": "https://ollama.test"}) as ctx:
        if method == "POST":
            result = await test_connection(ctx, "primary")
            assert result["ok"] is False and result["error_code"] == ("auth" if status == 401 else "unreachable")
        else:
            result = await list_models(ctx)
            assert result["options"] == [] and result["error"]
    assert stream.closed and stream.parts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/", "/v1", "/v1/", "/v1/v1/", "/prefix/v1"])
async def test_completion_uses_json_messages_and_one_version_prefix(suffix):
    from nodvard_deck_ext_shield.openai_compat import OpenAICompatProvider

    calls = []

    def answer(request):
        calls.append(request)
        return httpx.Response(200, json=_answer())

    settings = {"openai_compat_url": f"https://primary.test{suffix}", "openai_compat_model": "hauptmodell"}
    async with _context(answer, settings, _token()) as ctx:
        assert await OpenAICompatProvider(ctx).complete("Frage", system="Systemtext") == "Alles gut."
    request = calls[0]
    prefix = "/prefix" if suffix.startswith("/prefix") else ""
    assert str(request.url) == f"https://primary.test{prefix}/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {_token()}"
    assert json.loads(request.content) == {
        "model": "hauptmodell", "messages": [{"role": "system", "content": "Systemtext"}, {"role": "user", "content": "Frage"}],
        "stream": False,
    }
    assert request.extensions["timeout"]["read"] == 45.0


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "code", "message"), [(401, "auth", "abgelehnt"), (403, "auth", "abgelehnt"),
    (302, "redirect", "um"), (500, "http", "HTTP 500")])
async def test_test_connection_uses_fixed_sanitized_errors(status, code, message, caplog):
    from nodvard_deck_ext_shield.openai_compat import test_connection

    key = _token()
    caplog.set_level(logging.DEBUG)

    def answer(request):
        return httpx.Response(status, json={"error": key}, headers={"location": f"https://other.test/{key}"})

    async with _context(answer, key=key) as ctx:
        result = await test_connection(ctx)
    assert result["ok"] is False and result["error_code"] == code
    assert message in result["message"] and result["latency_ms"] >= 0
    assert key not in json.dumps(result) + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_type", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError])
async def test_network_error_does_not_echo_foreign_text(exc_type, caplog):
    from nodvard_deck_ext_shield.openai_compat import test_connection

    caplog.set_level(logging.DEBUG)

    def answer(request):
        raise exc_type(f"fremder Text {_token()}", request=request)

    async with _context(answer, key=_token()) as ctx:
        result = await test_connection(ctx)
    assert result["error_code"] == ("timeout" if exc_type is httpx.ReadTimeout else "unreachable")
    assert _token() not in json.dumps(result) + caplog.text and "fremder" not in result["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, [], {"choices": []}, {"choices": [{"message": {"content": 3}}]},
                                  {"choices": [{"message": {"content": ""}}]}])
async def test_malformed_completion_is_a_meaningful_error(body):
    from nodvard_deck_ext_shield.openai_compat import test_connection

    async with _context(lambda request: httpx.Response(200, json=body)) as ctx:
        result = await test_connection(ctx)
    assert result["ok"] is False and result["error_code"] == "invalid_response"


@pytest.mark.asyncio
async def test_non_json_completion_is_rejected():
    from nodvard_deck_ext_shield.openai_compat import test_connection

    async with _context(lambda request: httpx.Response(200, text="kein JSON")) as ctx:
        result = await test_connection(ctx)
    assert result["error_code"] == "invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["complete", "stream", "models", "test"])
async def test_plain_and_encoded_token_echoes_never_leave_provider(operation, caplog):
    from nodvard_deck_ext_shield.openai_compat import OpenAICompatProvider, list_models, test_connection

    key = _token() + "/+ wert"
    forms = [key, quote(key, safe=""), quote_plus(key)]
    caplog.set_level(logging.DEBUG)

    def answer(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": f"modell-{form}"} for form in forms] + [{"id": "sauber"}]})
        return httpx.Response(200, json=_answer(" ".join(forms)))

    async with _context(answer, key=key) as ctx:
        provider = OpenAICompatProvider(ctx)
        if operation == "complete":
            result = await provider.complete("Frage")
        elif operation == "stream":
            result = [part async for part in provider.stream("Frage")]
        elif operation == "models":
            result = await list_models(ctx)
            assert result["options"] == [{"value": "sauber", "label": "sauber"}]
        else:
            result = await test_connection(ctx)
            assert result["ok"] is True and result["models"] == [{"value": "sauber", "label": "sauber"}]
    assert all(form not in json.dumps(result) + caplog.text for form in forms)


@pytest.mark.asyncio
async def test_model_metadata_is_sanitized_before_audit_use():
    from nodvard_deck_ext_shield.openai_compat import OpenAICompatProvider, test_connection

    settings = {"openai_compat_url": "https://primary.test", "openai_compat_model": _token()}
    async with _context(lambda request: httpx.Response(200, json=_answer()), settings, _token()) as ctx:
        provider = OpenAICompatProvider(ctx)
        await provider.complete("Frage")
        result = await test_connection(ctx)
        assert provider.model == "[ausgeblendet]" and result["model"] == "[ausgeblendet]"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 405, 501])
async def test_optional_model_list_does_not_break_a_working_connection(status):
    from nodvard_deck_ext_shield.openai_compat import OpenAICompatProvider, test_connection

    calls = []

    def answer(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(status) if request.method == "GET" else httpx.Response(200, json=_answer("OK"))

    async with _context(answer) as ctx:
        result = await test_connection(ctx)
        assert result["ok"] is True and result["models"] == [] and "keine Modellliste" in result["models_error"]
        calls.clear()
        health = await OpenAICompatProvider(ctx).health()
    assert health.ok is True and calls == [("POST", "/v1/chat/completions")]


@pytest.mark.asyncio
async def test_model_list_is_sorted_and_deduplicated():
    from nodvard_deck_ext_shield.openai_compat import list_models

    body = {"data": [{"id": " z "}, {"id": "a"}, {"id": "a"}, 1, {}, {"id": ""}]}
    async with _context(lambda request: httpx.Response(200, json=body)) as ctx:
        result = await list_models(ctx)
    assert result == {"options": [{"value": "a", "label": "a"}, {"value": "z", "label": "z"}], "error": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "ftp://primary.test", "https://@primary.test", "https://primary.test?q=x",
                                   "https://primary.test/#x", "https://primary.test:invalid", "https://primary.test bad"])
async def test_invalid_address_never_receives_a_request(value):
    from nodvard_deck_ext_shield.openai_compat import test_connection

    calls = []
    async with _context(lambda request: calls.append(request), {"openai_compat_url": value, "openai_compat_model": "m"}) as ctx:
        result = await test_connection(ctx)
    assert result["ok"] is False and result["error_code"] == "configuration" and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("encoded", [False, True])
async def test_secret_in_address_is_rejected_before_network(encoded):
    from nodvard_deck_ext_shield.openai_compat import list_models, test_connection

    key = _token() + "/+ wert"
    part = quote(key, safe="") if encoded else key
    calls = []
    settings = {"openai_compat_url": f"https://primary.test/{part}", "openai_compat_model": "m"}
    async with _context(lambda request: calls.append(request), settings, key) as ctx:
        result = await test_connection(ctx)
        models = await list_models(ctx)
    assert result["ok"] is False and models["options"] == [] and calls == []
    assert key not in json.dumps(result) + json.dumps(models) and quote(key, safe="") not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("primary", [None, 500])
async def test_fallback_uses_its_own_model_and_base(primary):
    from nodvard_deck_ext_shield.openai_compat import OpenAICompatProvider

    calls = []

    def answer(request):
        calls.append((request.url.host, json.loads(request.content)["model"]))
        return httpx.Response(500) if request.url.host == "primary.test" else httpx.Response(200, json=_answer("Ersatzantwort"))

    settings = {"openai_compat_failover_url": "https://fallback.test/v1", "openai_compat_failover_model": "ersatz"}
    if primary:
        settings.update(openai_compat_url="https://primary.test", openai_compat_model="haupt")
    async with _context(answer, settings, _token()) as ctx:
        provider = OpenAICompatProvider(ctx)
        assert await provider.complete("Frage") == "[Failover-Modell] Ersatzantwort"
        assert provider.model == "ersatz"
    assert calls[-1] == ("fallback.test", "ersatz")
    assert len(calls) == (2 if primary else 1)


@pytest.mark.asyncio
async def test_absent_config_and_failed_fallback_are_safe():
    from nodvard_deck_ext_shield.openai_compat import NO_AI_TEXT, UNAVAILABLE_TEXT, OpenAICompatProvider

    async with _context(lambda request: httpx.Response(500), {}) as ctx:
        assert await OpenAICompatProvider(ctx).complete("Frage") == NO_AI_TEXT
    async with _context(lambda request: httpx.Response(500)) as ctx:
        assert await OpenAICompatProvider(ctx).complete("Frage") == UNAVAILABLE_TEXT


@pytest.mark.asyncio
async def test_selected_provider_updates_without_restart_and_defaults_to_ollama():
    from nodvard_deck_ext_shield.ai import ConfiguredAIProvider, is_configured

    settings = {"ollama_url": "https://ollama.test", "openai_compat_url": "https://primary.test", "openai_compat_model": "neu"}
    calls = []

    def answer(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"response": "Alt"} if request.url.path == "/api/generate" else _answer("Neu"))

    async with _context(answer, settings) as ctx:
        provider = ConfiguredAIProvider(ctx)
        assert await provider.complete("Frage") == "Alt"
        settings["ai_provider"] = "openai_compat"
        assert await provider.complete("Frage") == "Neu" and provider.model == "neu"
        assert is_configured(settings) is True
        settings.pop("openai_compat_url")
        assert is_configured(settings) is False
    assert calls == ["/api/generate", "/v1/chat/completions"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["complete", "health", "models", "test"])
async def test_current_vault_key_is_used_on_every_call(file_db, tmp_path, test_settings, operation):
    from nodvard_deck.db.session import get_engine
    from nodvard_deck.ext.context import build_context
    from nodvard_deck.ext.runtime import ExtensionRuntime, LoadedExtension
    from nodvard_deck.models import ExtensionRecord
    from nodvard_deck_ext_shield.openai_compat import OpenAICompatProvider, TOKEN_LABEL, list_models, test_connection
    from nodvard_sdk import ExtensionManifest

    permissions = ["net.outbound", f"secrets.read:{TOKEN_LABEL}"]
    manifest = ExtensionManifest(id="shield", name="Shield", version="0.1.0", api_version="0.1.0", entrypoint="x:Extension", permissions=permissions)
    loaded = LoadedExtension(manifest=manifest, instance=None, ctx=None, granted_permissions=permissions)
    ctx = build_context(ExtensionRuntime(), loaded, manifest, permissions, tmp_path, test_settings)
    async with file_db() as session:
        session.add(ExtensionRecord(id="shield", version="0.1.0", api_version="0.1.0", state="enabled",
                                    settings={"openai_compat_url": "https://primary.test", "openai_compat_model": "haupt"}))
        await session.commit()
    expected = _token()
    seen = []
    held = []

    def answer(request):
        held.append(get_engine().pool.checkedout())
        seen.append(request.headers.get("authorization"))
        if request.headers.get("authorization") != f"Bearer {expected}":
            return httpx.Response(401, text=expected)
        return httpx.Response(200, json={"data": [{"id": "haupt"}]} if request.method == "GET" else _answer("Antwort"))

    provider = OpenAICompatProvider(ctx)

    async def call():
        if operation == "health":
            return (await provider.health()).ok
        if operation == "models":
            return (await list_models(ctx))["error"] is None
        if operation == "test":
            return (await test_connection(ctx))["ok"]
        return await provider.complete("Frage") == "Antwort"

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        ctx.http._client = http
        await ctx.secrets.create(label=TOKEN_LABEL, kind="generic", value=expected)
        assert await call() is True
        await ctx.secrets.replace(label=TOKEN_LABEL, value="falsch")
        assert await call() is False
        expected = _token() + "-neu"
        await ctx.secrets.replace(label=TOKEN_LABEL, value=expected)
        assert await call() is True
        await ctx.secrets.delete(TOKEN_LABEL)
        assert await call() is False
    assert seen[-1] is None
    assert held and set(held) == {0}


def test_schema_and_manifest_bind_the_new_secret():
    import tomllib

    directory = SRC.parent
    schema = json.loads((directory / "settings.schema.json").read_text(encoding="utf-8"))
    manifest = tomllib.loads((directory / "extension.toml").read_text(encoding="utf-8"))
    assert schema["properties"]["ai_provider"]["default"] == "ollama"
    secret = next(item for item in schema["x-secrets"] if item["label"] == "shield-openai-compat-key")
    assert secret["x-secret-bound-to"] == ["openai_compat_url", "openai_compat_failover_url"]
    assert "secrets.read:shield-openai-compat-key" in manifest["extension"]["permissions"]


@pytest.mark.asyncio
async def test_transport_logs_cannot_echo_authenticated_response_metadata(caplog):
    from nodvard_deck_ext_shield.openai_compat import test_connection

    key = _token()
    caplog.set_level(logging.DEBUG)
    # Die ntfy-Erweiterung hebt "httpx" beim Laden auf WARNING; ohne eigenen Pegel hinge der Test von der Reihenfolge ab.
    caplog.set_level(logging.DEBUG, logger="httpx")

    def answer(request):
        logging.getLogger("httpcore.http11").debug("receive_response_headers.complete %s", {"echo": key})
        body = {"data": [{"id": "haupt"}]} if request.method == "GET" else _answer("OK")
        return httpx.Response(200, json=body, extensions={"reason_phrase": key.encode()})

    async with _context(answer, key=key) as ctx:
        result = await test_connection(ctx)
    assert result["ok"] is True and key not in caplog.text
    logging.getLogger("httpx").info("Transportdiagnose nach dem Verbindungstest")
    assert "Transportdiagnose nach dem Verbindungstest" in caplog.text


@pytest.mark.asyncio
async def test_transport_log_guard_keeps_concurrent_requests_private(caplog):
    import asyncio

    from nodvard_deck_ext_shield.openai_compat import _credentials

    key = _token()
    ready = asyncio.Event()
    first_done = asyncio.Event()
    caplog.set_level(logging.DEBUG)

    async with _context(lambda request: httpx.Response(200), key=key) as ctx:
        async def first():
            async with _credentials(ctx):
                await ready.wait()
            first_done.set()

        async def second():
            async with _credentials(ctx):
                ready.set()
                await first_done.wait()
                logging.getLogger("httpcore.http11").debug("Serverecho %s", key)

        await asyncio.gather(first(), second())
    assert key not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 404, 405, 501])
async def test_health_requires_a_working_completion(status):
    from nodvard_deck_ext_shield.openai_compat import OpenAICompatProvider

    def answer(request):
        if request.method == "GET":
            return httpx.Response(status, json={"data": [{"id": "hauptmodell"}]})
        return httpx.Response(404)

    async with _context(answer) as ctx:
        assert (await OpenAICompatProvider(ctx).health()).ok is False


@pytest.mark.asyncio
@pytest.mark.parametrize("primary_status", [401, 404, 500])
async def test_health_checks_fallback_completion(primary_status):
    from nodvard_deck_ext_shield.openai_compat import OpenAICompatProvider

    calls = []

    def answer(request):
        calls.append((request.url.host, request.method))
        if request.url.host == "primary.test":
            return httpx.Response(primary_status)
        assert json.loads(request.content)["model"] == "ersatz"
        return httpx.Response(200, json=_answer("OK"))

    settings = {"openai_compat_url": "https://primary.test", "openai_compat_model": "haupt",
                "openai_compat_failover_url": "https://fallback.test", "openai_compat_failover_model": "ersatz"}
    async with _context(answer, settings) as ctx:
        health = await OpenAICompatProvider(ctx).health()
    assert health.ok is True and "Ersatzserver" in health.message
    assert calls == [("primary.test", "POST"), ("fallback.test", "POST")]


@pytest.mark.asyncio
async def test_ollama_test_fails_when_model_metadata_overflows():
    from nodvard_deck_ext_shield.ai import test_connection

    def answer(request):
        body = {"response": "OK"} if request.method == "POST" else {
            "models": [{"name": "haupt", "size": 10 ** 400}]}
        return httpx.Response(200, json=body)

    settings = {"ollama_url": "https://ollama.test", "ollama_model": "haupt"}
    async with _context(answer, settings) as ctx:
        result = await test_connection(ctx, "primary")
    assert result["ok"] is False and result["error_code"] == "unreachable"
    assert "Tresor" in result["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["complete", "health"])
async def test_inflight_request_keeps_credentials_bound_to_original_targets(operation):
    from nodvard_deck_ext_shield.openai_compat import OpenAICompatProvider

    settings = {"openai_compat_url": "https://primary.test", "openai_compat_model": "haupt",
                "openai_compat_failover_url": "https://fallback.test", "openai_compat_failover_model": "ersatz"}
    calls = []

    async def answer(request):
        calls.append((request.url.host, request.headers.get("authorization")))
        if request.url.host == "primary.test":
            settings["openai_compat_failover_url"] = "https://new.test"
            return httpx.Response(500)
        return httpx.Response(200, json=_answer("OK"))

    async with _context(answer, settings, _token()) as ctx:
        provider = OpenAICompatProvider(ctx)
        if operation == "complete":
            assert await provider.complete("Frage") == "[Failover-Modell] OK"
        else:
            assert (await provider.health()).ok is True
    assert calls == [("primary.test", f"Bearer {_token()}"), ("fallback.test", f"Bearer {_token()}")]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["complete", "health", "models", "test"])
async def test_settings_change_while_loading_credentials_stops_request(operation):
    from nodvard_deck_ext_shield.openai_compat import (
        OpenAICompatProvider,
        list_models,
        test_connection,
    )

    calls = []
    settings = {"openai_compat_url": "https://primary.test", "openai_compat_model": "haupt"}
    async with _context(lambda request: calls.append(request), settings, _token()) as ctx:
        @asynccontextmanager
        async def vault_use(handle):
            settings["openai_compat_url"] = "https://new.test"
            yield _token()

        ctx.vault_use = vault_use
        provider = OpenAICompatProvider(ctx)
        if operation == "complete":
            await provider.complete("Frage")
        elif operation == "health":
            assert (await provider.health()).ok is False
        elif operation == "models":
            assert (await list_models(ctx))["options"] == []
        else:
            assert (await test_connection(ctx))["ok"] is False
    assert calls == []


@pytest.mark.asyncio
async def test_cached_health_shares_checks_and_expires(monkeypatch):
    import asyncio

    from nodvard_deck_ext_shield import ai

    now = [0.0]
    monkeypatch.setattr(ai, "monotonic", lambda: now[0])
    calls = []
    status = [200]
    settings = {"ai_provider": "openai_compat", "openai_compat_url": "https://primary.test",
                "openai_compat_model": "haupt"}

    async def answer(request):
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(status[0], json=_answer("OK"))

    async with _context(answer, settings) as ctx:
        provider = ai.ConfiguredAIProvider(ctx)
        results = await asyncio.gather(*(provider.cached_health() for _ in range(10)))
        assert all(result.ok for result in results) and len(calls) == 1
        now[0] = 299
        assert (await provider.cached_health()).ok is True and len(calls) == 1
        now[0] = 300
        status[0] = 500
        assert (await provider.cached_health()).ok is False and len(calls) == 2
        assert (await provider.cached_health()).ok is False and len(calls) == 2
        settings["openai_compat_url"] = "https://new.test"
        status[0] = 200
        assert (await provider.cached_health()).ok is True and len(calls) == 3
        assert calls[-1].url.host == "new.test"


@pytest.mark.asyncio
async def test_health_handles_config_removed_before_credentials():
    from nodvard_deck_ext_shield.openai_compat import OpenAICompatProvider

    calls = []
    reads = [0]
    async with _context(lambda request: calls.append(request)) as ctx:
        original_get = ctx.settings.get

        async def get_settings():
            reads[0] += 1
            return await original_get() if reads[0] == 1 else {}

        ctx.settings.get = get_settings
        assert (await OpenAICompatProvider(ctx).health()).ok is False
    assert calls == []
