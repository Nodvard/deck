"""Auswahlliste "KI-Modell": `ollama.list_models` (hinter `GET /ext/shield/ai/models`)."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

SRC = Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"


@pytest.fixture(autouse=True)
def _soc_on_path():
    before = list(sys.path)
    sys.path.insert(0, str(SRC))
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _ctx(resp=None, exc=None, saved_url=None, calls=None, settings=None, key=None):
    async def get(url, **kwargs):  # noqa: ANN003
        if calls is not None:
            calls.append((url, kwargs))
        if exc:
            raise exc
        return resp

    async def settings_get():
        if settings is not None:
            return settings
        return {"ollama_url": saved_url} if saved_url else {}

    async def exists(label):  # noqa: ANN001
        return key is not None

    async def get_handle(label):  # noqa: ANN001
        return object()

    @asynccontextmanager
    async def vault_use(handle):  # noqa: ANN001
        yield key

    return SimpleNamespace(
        http=SimpleNamespace(get=get), settings=SimpleNamespace(get=settings_get),
        secrets=SimpleNamespace(exists=exists, get_handle=get_handle), vault_use=vault_use,
    )


@pytest.mark.asyncio
async def test_models_are_listed_sorted_with_size():
    from nodvard_deck_ext_shield.ollama import list_models

    calls: list = []
    body = {"models": [{"name": "qwen2.5:7b", "size": 4_683_087_332}, {"name": "llama3:8b", "size": 0}, {"name": ""}]}
    out = await list_models(_ctx(_Resp(200, body), calls=calls, saved_url="http://10.0.0.5:11434/"), "primary")
    assert out == {
        "options": [{"value": "llama3:8b", "label": "llama3:8b"}, {"value": "qwen2.5:7b", "label": "qwen2.5:7b (4.7 GB)"}],
        "error": None,
    }
    assert calls[0][0] == "http://10.0.0.5:11434/api/tags"


@pytest.mark.asyncio
async def test_primary_and_failover_use_their_saved_addresses():
    from nodvard_deck_ext_shield.ollama import list_models

    calls: list = []
    settings = {"ollama_url": "http://ki:11434", "ollama_failover_url": "http://ersatz:11434"}
    await list_models(_ctx(_Resp(200, {"models": []}), settings=settings, calls=calls), "primary")
    await list_models(_ctx(_Resp(200, {"models": []}), settings=settings, calls=calls), "failover")
    assert [c[0] for c in calls] == ["http://ki:11434/api/tags", "http://ersatz:11434/api/tags"]


@pytest.mark.asyncio
async def test_key_is_sent_only_to_a_saved_address():
    """Schluessel-Leck (Sicherheits-Review): der Bearer-Schluessel darf nur an eine
    gespeicherte Adresse gehen, nie an etwas, das ein Aufrufer mitschickt."""
    from nodvard_deck_ext_shield.ollama import list_models

    calls: list = []
    await list_models(_ctx(_Resp(200, {"models": []}), saved_url="http://ki:11434", calls=calls, key="GEHEIM-KEY"), "primary")
    assert calls[0][0] == "http://ki:11434/api/tags"
    assert calls[0][1]["headers"] == {"Authorization": "Bearer GEHEIM-KEY"}

    calls.clear()
    out = await list_models(_ctx(_Resp(200, {"models": []}), settings={}, calls=calls, key="GEHEIM-KEY"), "primary")
    assert calls == [] and "gespeichert" in out["error"]  # ohne gespeicherte Adresse geht gar nichts raus


@pytest.mark.asyncio
async def test_unknown_which_is_not_a_way_to_pick_an_address():
    from nodvard_deck_ext_shield.ollama import list_models

    calls: list = []
    out = await list_models(_ctx(_Resp(200, {"models": []}), saved_url="http://ki:11434", calls=calls), "http://evil:1")
    assert calls == [] and out["options"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"exc": TimeoutError("timed out")}, "Keine Antwort vom KI-Server"),
        ({"exc": OSError("[Errno -2] Name or service not known")}, "nicht gefunden"),
        ({"exc": OSError("Connection refused")}, "nicht erreichbar"),
        ({"resp": _Resp(401)}, "abgelehnt"),
        ({"resp": _Resp(500)}, "HTTP 500"),
        ({"resp": _Resp(200, ValueError("kein json"))}, "Unerwartete Antwort"),
        ({"resp": _Resp(200, {"models": []})}, "noch kein Modell"),
    ],
)
async def test_problems_become_hints_not_errors(kwargs, expected):
    from nodvard_deck_ext_shield.ollama import list_models

    out = await list_models(_ctx(saved_url="http://ki:11434", **kwargs), "primary")
    assert out["options"] == [] and expected in out["error"]


@pytest.mark.asyncio
async def test_without_saved_address_no_request_is_made():
    from nodvard_deck_ext_shield.ollama import list_models

    calls: list = []
    out = await list_models(_ctx(_Resp(200, {}), settings={"ollama_url": "  ", "ollama_failover_url": "ftp://x"}, calls=calls), "primary")
    assert calls == [] and "Noch keine Adresse" in out["error"]
    out = await list_models(_ctx(_Resp(200, {}), settings={"ollama_failover_url": "ftp://x"}, calls=calls), "failover")
    assert calls == [] and "Noch keine Adresse" in out["error"]


# --- Weiterleitungen und kaputte Antworten (Liste, Gesundheit, Anfrage) ---------------------------------------------

DEEP_JSON = "[" * 100_000 + "]" * 100_000  # so tief verschachtelt, dass `json.loads` mit RecursionError aufgibt
REDIRECT_TO = "http://anderswo.example:8080/falle"


class _JsonResp(_Resp):
    """Antwort wie von httpx: `json()` parst den Text wirklich; eine Weiterleitung traegt ihr Ziel im Kopf `location`."""

    def __init__(self, status=200, text="", headers=None):
        super().__init__(status)
        self.text, self.headers = text, headers or {}

    def json(self):
        import json

        return json.loads(self.text)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_a_redirect_is_named_but_its_target_never(status):
    from nodvard_deck_ext_shield.ollama import list_models

    resp = _JsonResp(status, "", {"location": REDIRECT_TO})
    out = await list_models(_ctx(resp, saved_url="http://ki:11434"), "primary")
    assert out["options"] == [] and f"umgeleitet (HTTP {status})" in out["error"]
    assert "anderswo" not in out["error"] and "http" not in out["error"].replace(f"HTTP {status}", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [DEEP_JSON, "[1, 2]", "42", '"text"', "null", '{"models": "viele"}', '{"models": 7}'])
async def test_an_answer_that_is_no_object_or_too_deep_becomes_a_hint(text):
    from nodvard_deck_ext_shield.ollama import list_models

    out = await list_models(_ctx(_JsonResp(200, text), saved_url="http://ki:11434"), "primary")
    assert out == {"options": [], "error": "Unerwartete Antwort vom KI-Server."}


@pytest.mark.asyncio
async def test_entries_that_are_no_objects_are_skipped():
    from nodvard_deck_ext_shield.ollama import list_models

    text = '{"models": [1, "qwen", null, ["x"], {"name": {"x": 1}}, {"name": " llama3:8b ", "size": 4.7e9}]}'
    out = await list_models(_ctx(_JsonResp(200, text), saved_url="http://ki:11434"), "primary")
    assert out == {"options": [{"value": "llama3:8b", "label": "llama3:8b (4.7 GB)"}], "error": None}


def _provider(resp=None, exc=None, *, url="http://ki:11434", failover=None):
    from nodvard_deck_ext_shield.ollama import OllamaProvider

    async def request(url, **kwargs):  # noqa: ANN003
        if exc:
            raise exc
        return resp

    ctx = _ctx(resp, exc, settings={"ollama_url": url, **({"ollama_failover_url": failover} if failover else {})})
    ctx.http = SimpleNamespace(get=request, post=request)
    return OllamaProvider(ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "ok"), [(200, True), (204, True), (301, False), (302, False), (308, False),
                                            (404, False), (500, False)])
async def test_health_is_ok_only_for_a_2xx_answer(status, ok):
    health = await _provider(_JsonResp(status, "{}", {"location": REDIRECT_TO})).health()
    assert health.ok is ok
    if 300 <= status < 400:
        assert f"umgeleitet (HTTP {status})" in health.message and "anderswo" not in health.message
    else:
        assert health.message == f"HTTP {status}"


_FOREIGN_ERRORS = [
    # So zitieren httpx und h11 Teile einer fremden Antwort im Text der Ausnahme.
    httpx.RemoteProtocolError("illegal header line: bytearray(b'Location: https://angreifer.example/x\\x00')"),
    httpx.RemoteProtocolError("Invalid URL in location header: http://angreifer.example:ANGREIFER-TEXT/"),
    ValueError("Codepoint U+2980 not allowed at position 10 in 'angreifer\u2980'"),
    httpx.ConnectError("[Errno 111] Connection refused angreifer"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", _FOREIGN_ERRORS)
async def test_the_health_check_never_quotes_the_text_of_a_network_error(exc):
    health = await _provider(exc=exc).health()
    assert health.ok is False
    assert health.message == "KI-Server nicht erreichbar – Adresse und Port prüfen."


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", _FOREIGN_ERRORS)
async def test_an_unreachable_ai_server_never_carries_the_text_of_the_network_error(exc):
    from nodvard_deck_ext_shield.ollama import OllamaUnavailable

    with pytest.raises(OllamaUnavailable) as caught:
        await _provider(exc=exc)._generate("http://ki:11434", "m", "p", None, {}, 5.0)
    assert "angreifer" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(("exc", "text"), [
    (httpx.ReadTimeout("timed out"), "Keine Antwort vom KI-Server"),
    (httpx.ConnectError("[Errno -2] Name or service not known"), "nicht gefunden"),
])
async def test_the_health_check_still_tells_timeout_and_unknown_name_apart(exc, text):
    health = await _provider(exc=exc).health()
    assert text in health.message


@pytest.mark.asyncio
@pytest.mark.parametrize(("resp", "reason"), [
    (_JsonResp(302, "", {"location": REDIRECT_TO}), "umgeleitet (HTTP 302)"),
    (_JsonResp(200, "kein json"), "Unerwartete Antwort"),
    (_JsonResp(200, DEEP_JSON), "Unerwartete Antwort"),
    (_JsonResp(200, '["response"]'), "Unerwartete Antwort"),
    (_JsonResp(200, "7"), "Unerwartete Antwort"),
    (_JsonResp(200, '{"response": {"text": "x"}}'), "Unerwartete Antwort"),
    (_JsonResp(200, '{"done": true}'), "Leere Antwort"),
])
async def test_a_broken_answer_is_unavailable_with_a_reason_and_never_crashes(resp, reason):
    from nodvard_deck_ext_shield.ollama import OllamaUnavailable

    provider = _provider(resp)
    with pytest.raises(OllamaUnavailable) as caught:
        await provider._generate("http://ki:11434", "m", "p", None, {}, 5.0)
    assert reason in str(caught.value) and "anderswo" not in str(caught.value)
    # Nach aussen bleibt es der Platzhalter, nie eine Ausnahme.
    assert "nicht erreichbar" in await provider.complete("Frage")


@pytest.mark.asyncio
async def test_a_good_answer_still_comes_through():
    assert await _provider(_JsonResp(200, '{"response": "Alles gut."}')).complete("Frage") == "Alles gut."
