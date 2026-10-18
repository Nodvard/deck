"""Vorschlaege fuer App-Kacheln: `GET /apps/suggestions` (api/v1/apps.py) aus `AppSuggestionProvider` und erkannten
Diensten (`ServiceCatalog`), dazu die Vorschlaege der Netzwerk-Erweiterung aus dem Nginx Proxy Manager."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from nodvard_deck.api.v1 import apps as apps_api
from nodvard_deck.api.v1 import overview as overview_api
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_sdk import AppSuggestion, AppSuggestionProvider, ServiceCatalog
from test_apps_api import _auth, _bootstrap_owner, _create, _user_token

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
FAKE_IDS = ("fake-proxy", "fake-services", "fake-broken", "fake-slow")


@pytest.fixture(autouse=True)
def _clean_runtime():
    overview_api.reset_cache()  # erkannte Dienste kommen aus dem Zwischenspeicher der Übersicht
    yield
    overview_api.reset_cache()
    runtime = get_extension_runtime()
    for ext_id in FAKE_IDS:
        runtime.capabilities.clear_extension(ext_id)


class _Proxy:
    def __init__(self, rows):
        self.rows = rows

    async def app_suggestions(self):
        return self.rows


class _Services:
    async def list_services(self):
        return [
            {"id": "a", "name": "nextcloud", "host": "docker", "state": "running", "url": "http://192.168.2.20:8080", "image": "nextcloud:29"},
            {"id": "b", "name": "gestoppt", "host": "docker", "state": "exited", "url": "http://192.168.2.20:8081", "image": "x"},
            {"id": "c", "name": "ohne-adresse", "host": "docker", "state": "running", "url": None, "image": "x"},
            {"id": "d", "name": "docker", "host": "docker", "unreachable": True, "url": "http://192.168.2.20:1"},
        ]


class _Broken:
    async def app_suggestions(self):
        raise RuntimeError("geheim: http://user:pw@intern")


class _Slow:
    async def app_suggestions(self):
        await asyncio.sleep(5)
        return []


class _BrokenServices:
    async def list_services(self):
        raise RuntimeError("geheim: http://user:pw@intern")


def _provide(ext_id, protocol, impl):
    get_extension_runtime().capabilities.provide(ext_id, protocol, impl)


@pytest.mark.asyncio
async def test_suggestions_are_checked_deduplicated_and_skip_existing_apps(client, db_session):
    token = await _bootstrap_owner(client)
    assert (await _create(client, token, name="Wiki", url="https://wiki.example.org/")).status_code == 201
    _provide("fake-proxy", AppSuggestionProvider, _Proxy([
        AppSuggestion(name="wiki.example.org", url="https://WIKI.example.org", source="Nginx Proxy Manager"),
        AppSuggestion(name="cloud.example.org", url="https://cloud.example.org", source="Nginx Proxy Manager"),
        AppSuggestion(name="doppelt", url="https://cloud.example.org/", source="Nginx Proxy Manager"),
        AppSuggestion(name="böse", url="javascript:alert(1)", source="Nginx Proxy Manager"),
        AppSuggestion(name="mit Zugang", url="http://user:pw@intern.example.org", source="Nginx Proxy Manager"),
        AppSuggestion(name="x" * 500, url="http://lang.example.org", source="Nginx Proxy Manager"),
        AppSuggestion(name="  ", url="http://leer.example.org", source="Nginx Proxy Manager"),
    ]))
    _provide("fake-services", ServiceCatalog, _Services())

    r = await client.get("/api/v1/apps/suggestions", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errors"] == []
    assert [(s["name"], s["url"], s["source"]) for s in body["suggestions"]] == [
        ("nextcloud", "http://192.168.2.20:8080", "fake-services"),
        ("cloud.example.org", "https://cloud.example.org", "Nginx Proxy Manager"),
        ("x" * 60, "http://lang.example.org", "Nginx Proxy Manager"),
    ]
    assert body["suggestions"][0]["host"] == "docker"
    assert [s["service_id"] for s in body["suggestions"]] == ["a", None, None]


class _CountingServices(_Services):
    def __init__(self):
        self.calls = 0

    async def list_services(self):
        self.calls += 1
        return await super().list_services()


@pytest.mark.asyncio
async def test_detected_services_come_from_the_same_read_as_the_overview_tiles(client, db_session):
    """Die erkannten Dienste tragen die Kennung ihrer Kachel und kommen aus demselben Abruf wie die Übersicht."""
    token = await _bootstrap_owner(client)
    services = _CountingServices()
    _provide("fake-services", ServiceCatalog, services)
    tiles = (await client.get("/api/v1/overview", headers=_auth(token))).json()["apps"]
    body = (await client.get("/api/v1/apps/suggestions", headers=_auth(token))).json()
    assert services.calls == 1, "kein zweites docker ps für die Vorschläge"
    detected = {t["id"] for t in tiles if t["source"] == "detected"}
    assert [s["service_id"] for s in body["suggestions"]] == ["a"] and "a" in detected


@pytest.mark.asyncio
async def test_a_module_address_that_is_also_a_detected_service_stays_at_the_service(client, db_session):
    token = await _bootstrap_owner(client)
    _provide("fake-services", ServiceCatalog, _Services())
    _provide("fake-proxy", AppSuggestionProvider, _Proxy([
        AppSuggestion(name="Nextcloud", url="http://192.168.2.20:8080/", source="Nextcloud"),
    ]))
    body = (await client.get("/api/v1/apps/suggestions", headers=_auth(token))).json()
    assert [(s["name"], s["service_id"]) for s in body["suggestions"]] == [("nextcloud", "a")]


@pytest.mark.asyncio
async def test_a_broken_or_slow_source_costs_only_its_own_suggestions(client, db_session, monkeypatch):
    monkeypatch.setattr(apps_api, "SUGGESTION_TIMEOUT_S", 0.2)
    token = await _bootstrap_owner(client)
    _provide("fake-broken", AppSuggestionProvider, _Broken())
    _provide("fake-slow", AppSuggestionProvider, _Slow())
    _provide("fake-proxy", AppSuggestionProvider, _Proxy([AppSuggestion(name="ok", url="https://ok.example.org", source="X")]))
    r = await client.get("/api/v1/apps/suggestions", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert [s["name"] for s in body["suggestions"]] == ["ok"]
    assert len(body["errors"]) == 2
    assert all("ließen sich gerade nicht lesen" in e for e in body["errors"])
    assert "geheim" not in " ".join(body["errors"]) and "pw@" not in " ".join(body["errors"])


@pytest.mark.asyncio
async def test_a_service_source_that_cannot_be_read_gets_a_sentence_and_the_others_stay(client, db_session):
    token = await _bootstrap_owner(client)
    _provide("fake-broken", ServiceCatalog, _BrokenServices())
    _provide("fake-services", ServiceCatalog, _Services())
    r = await client.get("/api/v1/apps/suggestions", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["name"] for s in body["suggestions"]] == ["nextcloud"]
    assert body["errors"] == ["Dienste von „fake-broken“ ließen sich gerade nicht lesen."]
    assert "geheim" not in r.text and "pw@" not in r.text


@pytest.mark.asyncio
async def test_a_failure_while_reading_the_services_is_a_sentence_not_a_server_error(
    client, db_session, monkeypatch, caplog
):
    async def explode():
        raise RuntimeError("geheim: http://user:pw@intern")

    monkeypatch.setattr(overview_api, "service_snapshot", explode)
    token = await _bootstrap_owner(client)
    _provide("fake-proxy", AppSuggestionProvider, _Proxy([AppSuggestion(name="ok", url="https://ok.example.org", source="X")]))
    with caplog.at_level("WARNING", logger="nodvard_deck.apps"):
        r = await client.get("/api/v1/apps/suggestions", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["name"] for s in body["suggestions"]] == ["ok"]
    assert body["errors"] == ["Die erkannten Dienste ließen sich gerade nicht lesen."]
    assert "geheim" not in r.text and "pw@" not in r.text
    assert "RuntimeError" in caplog.text and "geheim" not in caplog.text and "pw@" not in caplog.text


@pytest.mark.asyncio
async def test_only_people_who_may_create_apps_see_suggestions(client, db_session):
    await _bootstrap_owner(client)
    viewer = await _user_token(client, db_session, "betrachter", role="viewer")
    assert (await client.get("/api/v1/apps/suggestions", headers=_auth(viewer))).status_code == 403
    writer = await _user_token(client, db_session, "schreiber", permissions=("apps.write",))
    assert (await client.get("/api/v1/apps/suggestions", headers=_auth(writer))).status_code == 200


def test_npm_proxy_hosts_become_suggestions(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_EXTENSIONS_DIR / "npm" / "src"))
    from nodvard_deck_ext_npm.suggestions import suggestions_from_hosts

    rows = suggestions_from_hosts([
        {"enabled": True, "domains": ["Cloud.Example.org", "nc.example.org"], "certificate_id": 3},
        {"enabled": True, "domains": ["intern.example.org"], "certificate_id": None, "ssl_forced": False},
        {"enabled": False, "domains": ["aus.example.org"], "certificate_id": 1},
        {"enabled": True, "domains": ["*.example.org"], "certificate_id": 1},
        {"enabled": True, "domains": []},
        "kaputt",
    ])
    assert [(r.name, r.url, r.source) for r in rows] == [
        ("cloud.example.org", "https://cloud.example.org", "Nginx Proxy Manager"),
        ("intern.example.org", "http://intern.example.org", "Nginx Proxy Manager"),
    ]
    for name in [n for n in sys.modules if n.startswith("nodvard_deck_ext_npm")]:
        del sys.modules[name]


@pytest.mark.parametrize(
    ("a", "b"),
    [("https://Wiki.example.org/", "https://wiki.example.org"), ("http://x:8080/a/", "http://X:8080/a")],
)
def test_url_key_treats_spelling_variants_as_the_same(a, b):
    assert apps_api.url_key(a) == apps_api.url_key(b)
