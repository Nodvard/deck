"""Auswahlliste "KI-Modell": `ollama.list_models` (hinter `GET /ext/nexus-soc/ai/models`)."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[2] / "extensions" / "nexus-soc" / "src"


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
    from nodvard_deck_ext_nexus_soc.ollama import list_models

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
    from nodvard_deck_ext_nexus_soc.ollama import list_models

    calls: list = []
    settings = {"ollama_url": "http://ki:11434", "ollama_failover_url": "http://ersatz:11434"}
    await list_models(_ctx(_Resp(200, {"models": []}), settings=settings, calls=calls), "primary")
    await list_models(_ctx(_Resp(200, {"models": []}), settings=settings, calls=calls), "failover")
    assert [c[0] for c in calls] == ["http://ki:11434/api/tags", "http://ersatz:11434/api/tags"]


@pytest.mark.asyncio
async def test_key_is_sent_only_to_a_saved_address():
    """Schluessel-Leck (Sicherheits-Review): der Bearer-Schluessel darf nur an eine
    gespeicherte Adresse gehen, nie an etwas, das ein Aufrufer mitschickt."""
    from nodvard_deck_ext_nexus_soc.ollama import list_models

    calls: list = []
    await list_models(_ctx(_Resp(200, {"models": []}), saved_url="http://ki:11434", calls=calls, key="GEHEIM-KEY"), "primary")
    assert calls[0][0] == "http://ki:11434/api/tags"
    assert calls[0][1]["headers"] == {"Authorization": "Bearer GEHEIM-KEY"}

    calls.clear()
    out = await list_models(_ctx(_Resp(200, {"models": []}), settings={}, calls=calls, key="GEHEIM-KEY"), "primary")
    assert calls == [] and "gespeichert" in out["error"]  # ohne gespeicherte Adresse geht gar nichts raus


@pytest.mark.asyncio
async def test_unknown_which_is_not_a_way_to_pick_an_address():
    from nodvard_deck_ext_nexus_soc.ollama import list_models

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
    from nodvard_deck_ext_nexus_soc.ollama import list_models

    out = await list_models(_ctx(saved_url="http://ki:11434", **kwargs), "primary")
    assert out["options"] == [] and expected in out["error"]


@pytest.mark.asyncio
async def test_without_saved_address_no_request_is_made():
    from nodvard_deck_ext_nexus_soc.ollama import list_models

    calls: list = []
    out = await list_models(_ctx(_Resp(200, {}), settings={"ollama_url": "  ", "ollama_failover_url": "ftp://x"}, calls=calls), "primary")
    assert calls == [] and "Noch keine Adresse" in out["error"]
    out = await list_models(_ctx(_Resp(200, {}), settings={"ollama_failover_url": "ftp://x"}, calls=calls), "failover")
    assert calls == [] and "Noch keine Adresse" in out["error"]
