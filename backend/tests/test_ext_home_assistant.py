"""Tests für die Home-Assistant-Erweiterung; Home Assistant ist ein `httpx.MockTransport`."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from nodvard_sdk import CertificateMismatch
from nodvard_sdk.errors import PermissionDenied
from sqlalchemy import select

from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import AuditEntry, ExtensionRecord
from nodvard_deck.services import extensions as extensions_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "home-assistant" / "src"))

from nodvard_deck_ext_home_assistant import parse, transport
from nodvard_deck_ext_home_assistant.config import (
    normalize_base_url,
    normalize_fingerprint,
    read_connections,
)
from nodvard_deck_ext_home_assistant.service import AUTH_FAILED_TTL_S, CACHE_TTL_S
from nodvard_deck_ext_home_assistant.widgets import MAX_ROWS, value_text

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "home_assistant"
BASE = "http://ha.example.com:8123"
TOKEN = "eyJhbGciOiJIUzI1NiJ9." "eyJpc3MiOiJ0ZXN0In0.ha-test-sig-9f3a7c21"  # in zwei Teilen, sonst hält die Export-Prüfung den Testwert für ein echtes Token
PIN = ":".join(["AB"] * 32)
EXT = "home-assistant"
API = "/api/v1/ext/home-assistant"
ENTITIES = [
    "sensor.wohnzimmer_temperatur", "sensor.keller_luftfeuchtigkeit", "sensor.balkon_temperatur", "light.flur",
    "sensor.gibt_es_nicht",
]


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _state(entity_id: str, state: str, **attributes: Any) -> dict[str, Any]:
    return {"entity_id": entity_id, "state": state, "attributes": attributes}


def test_fixtures_hold_no_private_addresses():
    """Die Aufnahmen sind öffentlich: keine privaten Adressen, nur Beispiel-Namen."""
    text = "".join((FIXTURES / name).read_text(encoding="utf-8") for name in ("api_root.json", "config.json", "states.json"))
    for address in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
        assert address.startswith("203.0.113."), address
    assert not re.search(r"\b(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d+\.\d+", text)
    for host in re.findall(r"https?://([^/\s\"':]+)", text):
        assert host.endswith(("example.com", "home-assistant.io", "github.com")) or host == "203.0.113.10", host


def test_states_fixture_is_counted_and_listed():
    parsed = parse.parse_states(_json("states.json"), ENTITIES)
    assert parsed is not None
    assert (parsed.total, parsed.update_count, parsed.unavailable_count, parsed.unknown_count) == (20, 2, 4, 2)

    # Name aus friendly_name, nach Name sortiert; Versionen als Text.
    assert [(u.name, u.installed_version, u.latest_version) for u in parsed.updates] == [
        ("Home Assistant Core Update", "2026.10.1", "2026.10.2"),
        ("Mosquitto broker Update", "6.5.0", "6.5.1"),
    ]
    assert [u.entity_id for u in parsed.updates] == ["update.home_assistant_core_update", "update.mosquitto_broker_update"]
    # `off` (aktuell), ein übersprungenes Update (`off`) und ein `unavailable` zählen nicht als Update.
    assert [u.name for u in parsed.unavailable] == ["Balkon Temperatur", "Fernseher", "Garten Lampe", "WLED Regal Firmware"]

    temperature = parsed.selected["sensor.wohnzimmer_temperatur"]
    assert (temperature.name, temperature.status, temperature.state, temperature.unit) == ("Wohnzimmer Temperatur", "ok", "21.5", "°C")
    assert parsed.selected["sensor.keller_luftfeuchtigkeit"].unit == "%"
    gone = parsed.selected["sensor.balkon_temperatur"]
    assert (gone.status, gone.state, gone.unit) == ("unavailable", None, "°C")
    flur = parsed.selected["light.flur"]
    assert (flur.status, flur.state, flur.unit) == ("ok", "on", None)
    assert "sensor.gibt_es_nicht" not in parsed.selected


def test_config_fixture_gives_version_and_location():
    config = parse.parse_config(_json("config.json"))
    assert config == parse.HaConfig(version="2026.10.1", location_name="Zuhause")
    assert parse.is_status_ok(_json("api_root.json")) is True


def test_update_without_friendly_name_uses_the_title_and_then_the_id():
    parsed = parse.parse_states([
        _state("update.a", "on", title="Nur Titel", installed_version="1", latest_version="2"),
        _state("update.b", "on", installed_version="1", latest_version="2"),
        _state("sensor.c", "unavailable", title="Titel zaehlt nur bei Updates"),
    ])
    assert parsed is not None
    assert [(u.name, u.entity_id) for u in parsed.updates] == [("Nur Titel", "update.a"), ("update.b", "update.b")]
    assert [u.name for u in parsed.unavailable] == ["sensor.c"]


def test_broken_fields_are_skipped_or_empty_never_a_crash():
    long_name = "N" * 200
    body: list[Any] = [
        "kein Objekt", 7, None, [], {},
        {"entity_id": 5, "state": "on"},
        {"entity_id": "sensor.ohne_zustand"},
        {"entity_id": "sensor.zustand_keine_zeichenkette", "state": 21.5},
        {"entity_id": "Kein Gueltiger Name", "state": "on"},
        {"entity_id": "sensor.attribute_falsch", "state": "3", "attributes": "kaputt"},
        {"entity_id": "sensor.name_falsch", "state": "4", "attributes": {"friendly_name": 12, "unit_of_measurement": ["x"]}},
        {"entity_id": "sensor.lang", "state": "unavailable", "attributes": {"friendly_name": long_name}},
        {"entity_id": "update.version_falsch", "state": "on", "attributes": {"installed_version": 5, "latest_version": {"a": 1}}},
        {"entity_id": "update.version_lang", "state": "on", "attributes": {"installed_version": "v" * 300, "latest_version": "  "}},
        {"entity_id": "sensor.steuerzeichen", "state": "unavailable", "attributes": {"friendly_name": "Zeile 1\nZeile 2\x00\x07\u202e<b>x</b>"}},
    ]
    parsed = parse.parse_states(body, ("sensor.attribute_falsch", "sensor.name_falsch"))
    assert parsed is not None
    assert parsed.total == 6, "nur Einträge mit gültiger Kennung und Zustand als Text"
    assert parsed.selected["sensor.attribute_falsch"] == parse.SelectedState("sensor.attribute_falsch", "sensor.attribute_falsch", "ok", "3", None)
    assert parsed.selected["sensor.name_falsch"] == parse.SelectedState("sensor.name_falsch", "sensor.name_falsch", "ok", "4", None)

    names = {u.entity_id: u.name for u in parsed.unavailable}
    assert len(names["sensor.lang"]) == 80 and names["sensor.lang"].endswith("…")
    assert names["sensor.steuerzeichen"] == "Zeile 1 Zeile 2 <b>x</b>", "Zeilenumbrueche und Steuerzeichen werden Leerzeichen"
    versions = {u.entity_id: (u.installed_version, u.latest_version) for u in parsed.updates}
    assert versions["update.version_falsch"] == (None, None)
    installed, latest = versions["update.version_lang"]
    assert installed is not None and len(installed) == 40 and latest is None


@pytest.mark.parametrize("body", [{}, "text", 5, None, {"states": []}])
def test_states_that_are_not_a_list_are_refused(body):
    assert parse.parse_states(body) is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"version": "2026.11.0b3", "location_name": "Haus"}, parse.HaConfig("2026.11.0b3", "Haus")),
        ({"version": "2026.10.0.dev0"}, parse.HaConfig("2026.10.0.dev0", None)),
        ({"version": 2026.1, "location_name": 5}, parse.HaConfig(None, None)),
        ({"version": "<script>alert(1)</script>", "location_name": " "}, parse.HaConfig(None, None)),
        ({"version": "9" * 80}, parse.HaConfig(None, None)),
        ({}, parse.HaConfig(None, None)),
        ({"location_name": "L" * 200}, parse.HaConfig(None, "L" * 79 + "…")),
    ],
)
def test_config_fields_are_read_defensively(body, expected):
    assert parse.parse_config(body) == expected


@pytest.mark.parametrize("body", [[], "x", None, 3])
def test_config_that_is_not_an_object_is_refused(body):
    assert parse.parse_config(body) is None


@pytest.mark.parametrize(("body", "ok"), [({"message": "API running."}, True), ({"message": 5}, False), ({}, False), ([], False), (None, False)])
def test_status_answer(body, ok):
    assert parse.is_status_ok(body) is ok


def test_lists_are_collected_up_to_a_limit_but_counted_fully(monkeypatch):
    monkeypatch.setattr(parse, "MAX_COLLECTED", 3)
    body = [_state(f"sensor.s{i}", "unavailable") for i in range(10)] + [
        _state(f"update.u{i}", "on", installed_version="1", latest_version="2") for i in range(10)
    ]
    parsed = parse.parse_states(body)
    assert parsed is not None
    assert (parsed.unavailable_count, parsed.update_count) == (10, 10)
    assert (len(parsed.unavailable), len(parsed.updates)) == (3, 3)


@pytest.mark.parametrize(
    ("state", "unit", "expected"),
    [("21.5", "°C", "21,5 °C"), ("58", "%", "58 %"), ("on", None, "on"), ("-3.25", "K", "-3,25 K"), ("1.2.3", None, "1.2.3"), (None, "W", "–"), ("2026-10-13T05:51:12+00:00", None, "2026-10-13T05:51:12+00:00")],
)
def test_value_text(state, unit, expected):
    assert value_text(state, unit) == expected


def test_base_url_is_normalized_and_credentials_are_refused():
    assert normalize_base_url("http://ha.example.com:8123/") == "http://ha.example.com:8123"
    assert normalize_base_url(" https://ha.example.com/ha/ ") == "https://ha.example.com/ha"
    assert normalize_base_url("http://203.0.113.10:8123/?x=1#y") == "http://203.0.113.10:8123"
    for bad in ("ha.example.com:8123", "ftp://ha.example.com", "http://", "http://user:pw@ha.example.com", "http://@ha.example.com", "http://ha.example.com:999999", ""):
        with pytest.raises(ValueError):
            normalize_base_url(bad)


def test_fingerprint_is_normalized_or_refused():
    assert normalize_fingerprint(None) is None and normalize_fingerprint("  ") is None
    assert normalize_fingerprint("ab" * 32) == PIN
    assert normalize_fingerprint(PIN.replace(":", " ").lower()) == PIN
    for bad in ("kaputt", "AB:CD", 5, "A" * 300, ["x"]):
        with pytest.raises(ValueError):
            normalize_fingerprint(bad)


def test_read_connections_flags_unusable_entries_without_dropping_the_rest():
    configs = read_connections({"connections": [
        {"name": "a", "base_url": "http://a.example.com:8123/", "entities": ["sensor.x", " sensor.x ", "light.y", ""], "tls_insecure_skip_verify": True},
        {"name": "a", "base_url": "http://zweite.example.com"},
        {"name": "b", "base_url": "http://user:pw@b.example.com"},
        {"name": "c", "base_url": "http://c.example.com", "entities": ["Sensor.Gross"]},
        {"name": "d", "base_url": "http://d.example.com", "entities": [f"sensor.s{i}" for i in range(31)]},
        {"name": "e", "base_url": "https://e.example.com", "tls_fingerprint_sha256": "kaputt", "tls_insecure_skip_verify": True},
        {"name": "f", "base_url": "http://f.example.com", "tls_fingerprint_sha256": PIN},
        {"name": "g", "base_url": "https://g.example.com", "tls_fingerprint_sha256": "ab" * 32, "tls_insecure_skip_verify": True},
        {"name": "mit Leerzeichen", "base_url": "http://h.example.com"},
        {"name": "i", "base_url": "http://i.example.com", "entities": "sensor.x"},
        {"name": " ", "base_url": "http://leer.example.com"},
        "kaputt",
    ]})
    assert [c.name for c in configs] == ["a", "a", "b", "c", "d", "e", "f", "g", "mit Leerzeichen", "i"]
    first = configs[0]
    assert (first.base_url, first.entities, first.insecure_tls, first.pin, first.problem) == (
        "http://a.example.com:8123", ("sensor.x", "light.y"), True, None, None,
    ), "Doppelte fallen weg, die Reihenfolge bleibt"
    assert "mehrfach vergeben" in (configs[1].problem or "")
    assert "ungültig" in (configs[2].problem or "") and "pw" not in (configs[2].problem or "")
    assert "Sensor.Gross" in (configs[3].problem or "") and "ungültig" in (configs[3].problem or "")
    assert "höchstens 30" in (configs[4].problem or "")
    assert "Fingerabdruck" in (configs[5].problem or "") and "ungültig" in (configs[5].problem or "")
    assert "nur für https://" in (configs[6].problem or "")
    assert (configs[7].pin, configs[7].problem) == (PIN, None)
    assert "Kurzname" in (configs[8].problem or "")
    assert "keine Liste" in (configs[9].problem or "")
    assert read_connections({}) == [] and read_connections({"connections": "x"}) == []


class _Http:
    """Was `transport.get()` von `ctx.http` braucht."""

    def __init__(self, handler: Callable[[httpx.Request], Any]) -> None:
        self._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.calls: list[dict[str, Any]] = []

    def stream(self, method: str, url: str, *, insecure_tls: bool = False, pinned_sha256: str | None = None, **kwargs: Any):
        self.calls.append({"insecure_tls": insecure_tls, "pinned_sha256": pinned_sha256, **kwargs})
        return self._client.stream(method, url, **kwargs)


@pytest.mark.asyncio
async def test_transport_stops_reading_a_huge_answer_early():
    sent = 0

    async def body():
        nonlocal sent
        for _ in range(100):
            sent += 1
            yield b"x" * (1024 * 1024)

    http = _Http(lambda request: httpx.Response(200, content=body()))
    with pytest.raises(transport.HaError) as caught:
        await transport.get(http, "http://ha.example.com/api/states", service="Home Assistant „ha“", max_bytes=transport.MAX_STATES_BYTES)
    assert caught.value.state == "error"
    assert "ungewöhnlich große Antwort (mehr als 16 MB)" in str(caught.value)
    assert sent <= 18, "nach dem Limit wird nicht weitergelesen"
    assert transport.MAX_STATES_BYTES == 16 * 1024 * 1024 and transport.TOTAL_TIMEOUT_S == 30.0


@pytest.mark.asyncio
async def test_transport_gives_up_after_the_total_time_limit():
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    with pytest.raises(transport.Unreachable) as caught:
        await transport.get(_Http(slow), "http://ha.example.com/api/", service="Home Assistant", total_timeout_s=0.05)
    assert "antwortet nicht (Zeitüberschreitung)" in str(caught.value)


@pytest.mark.asyncio
async def test_transport_passes_the_tls_choice_and_returns_other_status_codes():
    http = _Http(lambda request: httpx.Response(503, content=b"nope"))
    reply = await transport.get(http, "https://ha.example.com/api/", service="x", insecure_tls=True)
    assert (reply.status_code, reply.body) == (503, b"nope")
    assert http.calls[0]["insecure_tls"] is True and http.calls[0]["pinned_sha256"] is None

    # Mit Fingerabdruck geht nur er hinaus, nie zusammen mit dem Schalter (ctx.http lehnt beides zusammen ab).
    pinned = _Http(lambda request: httpx.Response(200, content=b"{}"))
    await transport.get(pinned, "https://ha.example.com/api/", service="x", insecure_tls=True, pin=PIN)
    assert pinned.calls[0]["pinned_sha256"] == PIN and pinned.calls[0]["insecure_tls"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "state", "fragment"),
    [
        (httpx.ReadTimeout("timed out"), "unreachable", "antwortet nicht (Zeitüberschreitung)"),
        (httpx.ConnectTimeout("timed out"), "unreachable", "antwortet nicht (Zeitüberschreitung)"),
        (httpx.ConnectError("[Errno 111] Connection refused"), "unreachable", "lehnt die Verbindung ab"),
        (httpx.ConnectError("[Errno -2] Name or service not known"), "unreachable", "lässt sich nicht auflösen"),
        (httpx.ConnectError("[Errno 113] No route to host"), "unreachable", "ist nicht erreichbar. Prüfe Adresse und Port"),
        (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate"), "error", "Fingerabdruck des Zertifikats ein"),
        (httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER] wrong version number"), "error", "Verschlüsselung passt nicht zum Port"),
        (httpx.RemoteProtocolError("illegal header line: bytearray(b'X-Geheim: abc')"), "error", "Verbindung abgebrochen oder eine fehlerhafte Antwort"),
        (httpx.LocalProtocolError("Illegal header value b'Bearer xyz'"), "error", "fehlerhafte Antwort"),
        (httpx.DecodingError("Error -3 while decompressing data: Geheim"), "error", "fehlerhafte Antwort"),
        (ValueError("irgendwas mit Geheim"), "error", "ließ sich nicht senden"),
    ],
)
async def test_transport_error_sentences(error, state, fragment):
    def boom(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(transport.HaError) as caught:
        await transport.get(_Http(boom), "http://ha.example.com/api/", service="Home Assistant „ha“")
    assert caught.value.state == state
    assert fragment in str(caught.value)
    for secret in ("X-Geheim", "xyz", "Geheim", "self-signed"):
        assert secret not in str(caught.value), "der Text der Ausnahme wird nicht zitiert"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.asyncio
async def test_transport_says_when_the_core_refuses_the_target():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise PermissionDenied("home-assistant", "net.outbound")

    with pytest.raises(transport.HaError) as caught:
        await transport.get(_Http(refuse), "http://ha.example.com/api/", service="Home Assistant „ha“")
    assert "nicht erlaubt" in str(caught.value) and "net.outbound" in str(caught.value) and caught.value.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [True, False])
async def test_transport_knows_a_changed_certificate(wrapped):
    mismatch = CertificateMismatch(PIN, ":".join(["CD"] * 32))
    error: Exception = mismatch
    if wrapped:
        error = httpx.ConnectError("[SSL] handshake")
        error.__cause__ = mismatch

    def boom(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(transport.HaError) as caught:
        await transport.get(_Http(boom), "https://ha.example.com/api/", service="Home Assistant „ha“", pin=PIN)
    text = str(caught.value)
    assert "anderes Zertifikat als das gemerkte" in text and "nicht das Token" in text
    assert "CD:CD" not in text and "AB:AB" not in text, "keine Fingerabdruecke im Satz"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


class FakeHa:
    """Home Assistant mit `/api/`, `/api/config` und `/api/states`, Anmeldung per Bearer-Token."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.insecure_requests: list[httpx.Request] = []
        self.pinned_requests: list[httpx.Request] = []
        self.pinned_error: Exception | None = None
        self.token = TOKEN
        self.root = _json("api_root.json")
        self.config = _json("config.json")
        self.states = _json("states.json")
        self.overrides: dict[str, Callable[[httpx.Request], Any]] = {}
        self.error: Exception | None = None

    def handler(self, request: httpx.Request) -> Any:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        path = request.url.path
        if path in self.overrides:
            return self.overrides[path](request)
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, text="401: Unauthorized")
        if path == "/api/":
            return httpx.Response(200, json=self.root)
        if path == "/api/config":
            return httpx.Response(200, json=self.config)
        if path == "/api/states":
            return httpx.Response(200, json=self.states)
        return httpx.Response(404, text="404: Not Found")

    def insecure_handler(self, request: httpx.Request) -> Any:
        self.insecure_requests.append(request)
        return self.handler(request)

    def pinned_handler(self, request: httpx.Request) -> Any:
        self.pinned_requests.append(request)
        if self.pinned_error is not None:
            raise self.pinned_error
        return self.handler(request)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


async def _login(client, username="owner1", password="correct-horse-battery") -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _enable(client, db_session, test_settings) -> tuple[dict, FakeHa]:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    headers = await _login(client)
    enabled = await client.post(f"/api/v1/extensions/{EXT}/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text

    fake = FakeHa()
    http = get_extension_runtime().loaded[EXT].ctx.http
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    http._insecure_client = httpx.AsyncClient(transport=httpx.MockTransport(fake.insecure_handler))
    # Ein Client mit gemerktem Fingerabdruck wird je Fingerabdruck gebaut; hier steht der Nachbau schon bereit.
    http._pinned_clients[bytes.fromhex(PIN.replace(":", ""))] = httpx.AsyncClient(transport=httpx.MockTransport(fake.pinned_handler))
    return headers, fake


def _connection(name="ha", *, entities: list[str] | None = None, insecure=False, url=BASE, pin: str | None = None) -> dict:
    entry: dict[str, Any] = {"name": name, "base_url": url, "entities": ENTITIES if entities is None else entities}
    if insecure:
        entry["tls_insecure_skip_verify"] = True
    if pin:
        entry["tls_fingerprint_sha256"] = pin
    return entry


async def _configure(client, headers, *connections: dict, token: str | None = TOKEN) -> None:
    saved = await client.put(f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": list(connections)}}, headers=headers)
    assert saved.status_code == 200, saved.text
    if token is not None:
        for connection in connections:
            res = await client.put(
                f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": f"home-assistant-token:{connection['name']}", "value": token}, headers=headers,
            )
            assert res.status_code == 204, res.text


async def _overview(client, headers, query: str = "") -> dict:
    res = await client.get(f"{API}/overview{query}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _only(body: dict) -> dict:
    assert len(body["connections"]) == 1
    return body["connections"][0]


async def _widget(client, headers) -> dict:
    res = await client.get(f"{API}/widgets/status", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _service():
    return get_extension_runtime().loaded[EXT].instance._service


async def _health():
    loaded = get_extension_runtime().loaded[EXT]
    return await loaded.instance.health(loaded.ctx)


@pytest.mark.asyncio
async def test_extension_is_discovered_disabled_beta_and_registers_page_and_widget(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    record = await db_session.get(ExtensionRecord, EXT)
    assert record is not None and record.state == "disabled"

    headers, _ = await _enable(client, db_session, test_settings)
    assert record.state == "enabled", record.last_error
    row = next(r for r in (await client.get("/api/v1/extensions", headers=headers)).json() if r["id"] == EXT)
    assert (row["name"], row["stage"], row["category"]) == ("Home Assistant", "beta", "servers")

    pages = (await client.get("/api/v1/pages", headers=headers)).json()
    page = next(p for p in pages if p["ext_id"] == EXT)
    assert (page["path"], page["component"], page["title"], page["stage"]) == ("/home-assistant", "HomeAssistantPage", "Home Assistant", "beta")
    widgets = [w for w in (await client.get("/api/v1/widgets", headers=headers)).json() if w["ext_id"] == EXT]
    assert [w["id"] for w in widgets] == ["status"]
    assert widgets[0]["view"]["item"]["badge"] == {"text": "{{ label }}", "tone": "{{ tone }}"}
    assert widgets[0]["data_endpoint"] == "widgets/status"

    bundle = await client.get(f"/api/v1/extensions/{EXT}/frontend/index.js")
    assert bundle.status_code == 200
    assert "HomeAssistantPage" in bundle.text


@pytest.mark.asyncio
async def test_manifest_asks_for_reading_only(client, db_session, test_settings):
    import tomllib

    manifest = tomllib.loads((REPO_EXTENSIONS_DIR / EXT / "extension.toml").read_text(encoding="utf-8"))
    assert manifest["extension"]["stage"] == "beta"
    assert manifest["extension"]["permissions"] == ["net.outbound", "net.outbound.insecure_tls", "secrets.read:home-assistant-*"]
    await _enable(client, db_session, test_settings)
    registered = get_extension_runtime().actions._specs
    assert [t for t, (ext_id, _spec) in registered.items() if ext_id == EXT] == [], "keine Aktion, nichts zum Freigeben"


@pytest.mark.asyncio
async def test_nothing_configured_is_reported_cleanly(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    body = await _overview(client, headers)
    assert body == {"connections": [], "summary": {"connections": 0, "updates": 0, "unavailable": 0, "unknown": 0}}

    widget = await _widget(client, headers)
    assert widget["data"] == [{
        "title": "Home Assistant nicht eingerichtet", "subtitle": "Unter Einstellungen → Erweiterungen → Home Assistant eintragen.",
        "label": "nicht eingerichtet", "tone": "neutral",
    }]
    health = await _health()
    assert health.healthy is False and "noch nicht eingerichtet" in health.message
    assert fake.requests == [], "ohne Einstellungen wird nichts angefragt"


@pytest.mark.asyncio
async def test_connection_without_token_is_not_configured(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), token=None)

    connection = _only(await _overview(client, headers))
    assert connection["state"] == "not_configured"
    assert connection["message"] == "Für ha fehlt das Zugangs-Token. Unter Einstellungen → Erweiterungen → Home Assistant eintragen."
    assert (connection["counts"], connection["updates"], connection["unavailable"], connection["entities"]) == (None, [], [], [])
    rows = (await _widget(client, headers))["data"]
    assert rows == [{"title": "ha: nicht eingerichtet", "subtitle": connection["message"], "label": "nicht eingerichtet", "tone": "neutral"}]
    assert fake.requests == []

    # Das Token nachtragen: gilt sofort, auch innerhalb der 20 Sekunden des Zwischenspeichers.
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "home-assistant-token:ha", "value": TOKEN}, headers=headers)
    assert res.status_code == 204
    assert _only(await _overview(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_full_overview_shows_version_updates_unavailable_and_chosen_entities(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())

    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    connection = _only(body)
    assert (connection["name"], connection["url"], connection["state"], connection["message"]) == ("ha", BASE, "ok", None)
    assert (connection["version"], connection["location_name"]) == ("2026.10.1", "Zuhause")
    assert connection["counts"] == {"total": 20, "updates": 2, "unavailable": 4, "unknown": 2}
    assert connection["updates"] == [
        {"entity_id": "update.home_assistant_core_update", "name": "Home Assistant Core Update", "installed_version": "2026.10.1", "latest_version": "2026.10.2"},
        {"entity_id": "update.mosquitto_broker_update", "name": "Mosquitto broker Update", "installed_version": "6.5.0", "latest_version": "6.5.1"},
    ]
    assert connection["unavailable"] == [
        {"entity_id": "sensor.balkon_temperatur", "name": "Balkon Temperatur"},
        {"entity_id": "media_player.fernseher", "name": "Fernseher"},
        {"entity_id": "light.garten_lampe", "name": "Garten Lampe"},
        {"entity_id": "update.wled_firmware", "name": "WLED Regal Firmware"},
    ]
    assert connection["entities"] == [
        {"entity_id": "sensor.wohnzimmer_temperatur", "name": "Wohnzimmer Temperatur", "status": "ok", "state": "21.5", "unit": "°C"},
        {"entity_id": "sensor.keller_luftfeuchtigkeit", "name": "Keller Luftfeuchtigkeit", "status": "ok", "state": "58", "unit": "%"},
        {"entity_id": "sensor.balkon_temperatur", "name": "Balkon Temperatur", "status": "unavailable", "state": None, "unit": "°C"},
        {"entity_id": "light.flur", "name": "Flur", "status": "ok", "state": "on", "unit": None},
        {"entity_id": "sensor.gibt_es_nicht", "name": "sensor.gibt_es_nicht", "status": "missing", "state": None, "unit": None},
    ]
    assert body["summary"] == {"connections": 1, "updates": 2, "unavailable": 4, "unknown": 2}

    # Nur lesen, mit dem Token als Bearer, ohne Umweg über `/api/`; `/api/config` zuerst (wird das Token abgelehnt, bleibt es dabei).
    assert fake.paths() == ["/api/config", "/api/states"]
    assert {r.method for r in fake.requests} == {"GET"}
    assert all(r.headers["Authorization"] == f"Bearer {TOKEN}" for r in fake.requests)
    assert all(TOKEN not in str(r.url) and not r.content for r in fake.requests)

    widget = await _widget(client, headers)
    assert widget["data"] == [
        {"title": "Home Assistant 2026.10.1", "subtitle": "ha · Zuhause · 2 Updates: Home Assistant Core Update, Mosquitto broker Update", "label": "2 Updates", "tone": "warn"},
        {"title": "Nicht verfügbar", "subtitle": "ha · Balkon Temperatur, Fernseher und 2 weitere · 2 mit Zustand „unbekannt“", "label": "4", "tone": "warn"},
        {"title": "Wohnzimmer Temperatur", "subtitle": "ha · sensor.wohnzimmer_temperatur", "label": "21,5 °C", "tone": "neutral"},
        {"title": "Keller Luftfeuchtigkeit", "subtitle": "ha · sensor.keller_luftfeuchtigkeit", "label": "58 %", "tone": "neutral"},
        {"title": "Balkon Temperatur", "subtitle": "ha · sensor.balkon_temperatur", "label": "nicht verfügbar", "tone": "warn"},
        {"title": "Flur", "subtitle": "ha · light.flur", "label": "on", "tone": "neutral"},
        {"title": "sensor.gibt_es_nicht", "subtitle": "ha · gibt es in Home Assistant nicht", "label": "nicht gefunden", "tone": "warn"},
    ]
    assert widget["meta"] == {"connections": 1, "updates": 2, "unavailable": 4}

    # Das Token steht nirgends: weder in Antworten noch in Einstellungen, Protokoll oder Audit.
    settings = (await client.get(f"/api/v1/extensions/{EXT}/settings", headers=headers)).json()
    assert {s["label"]: s["is_set"] for s in settings["secrets"]} == {"home-assistant-token:ha": True}
    audit = (await db_session.execute(select(AuditEntry))).scalars().all()
    everything = res.text + json.dumps(widget) + json.dumps(settings) + caplog.text + json.dumps([a.detail or {} for a in audit], default=str)
    assert TOKEN not in everything and TOKEN.split(".")[1] not in everything


@pytest.mark.asyncio
async def test_widget_when_everything_is_calm(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(entities=[]))
    fake.states = [_state("sensor.a", "1"), _state("update.core", "off", installed_version="1", latest_version="1")]
    fake.config = {"version": "2026.10.1", "location_name": "Ha"}
    rows = (await _widget(client, headers))["data"]
    assert rows == [
        {"title": "Home Assistant 2026.10.1", "subtitle": "ha · alles aktuell", "label": "aktuell", "tone": "good"},
        {"title": "Nicht verfügbar", "subtitle": "ha · keine", "label": "keine", "tone": "good"},
    ], "der Standortname gleicht dem Kurznamen: er steht nicht doppelt da"

    fake.states = [_state("sensor.a", "unknown"), _state("update.core", "on", installed_version="1", latest_version="2", title="Core")]
    _service().clear()
    rows = (await _widget(client, headers))["data"]
    assert rows[0] == {"title": "Home Assistant 2026.10.1", "subtitle": "ha · 1 Update: Core", "label": "1 Update", "tone": "warn"}
    assert rows[1] == {"title": "Nicht verfügbar", "subtitle": "ha · keine · 1 mit Zustand „unbekannt“", "label": "keine", "tone": "good"}

    fake.config = {"foo": "bar"}
    _service().clear()
    assert (await _widget(client, headers))["data"][0]["title"] == "Home Assistant", "ohne Version nur der Name"


@pytest.mark.asyncio
async def test_widget_shows_at_most_ten_rows_and_says_how_many_values_are_missing(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    ids = [f"sensor.s{i:02d}" for i in range(12)]
    fake.states = [_state(i, str(n), unit_of_measurement="W", friendly_name=f"Sensor {n}") for n, i in enumerate(ids)]
    await _configure(client, headers, _connection(entities=ids))
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == MAX_ROWS
    assert [r["title"] for r in rows[2:9]] == [f"Sensor {n}" for n in range(7)]
    assert rows[-1] == {"title": "… und 5 weitere Werte", "subtitle": "Alle stehen auf der Seite „Home Assistant“.", "label": "–", "tone": "neutral"}
    # Die Seite bekommt sie alle.
    assert len(_only(await _overview(client, headers))["entities"]) == 12

    # Passen alle genau, gibt es keine Schlusszeile.
    await _configure(client, headers, _connection(entities=ids[:8]))
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == MAX_ROWS and rows[-1]["title"] == "Sensor 7"


@pytest.mark.asyncio
async def test_widget_with_many_connections_stays_within_ten_rows(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    names = [f"ha{i}" for i in range(7)]
    await _configure(client, headers, *[_connection(n, entities=["sensor.wohnzimmer_temperatur"]) for n in names])
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == MAX_ROWS
    assert rows[-1]["title"] == "… und 5 weitere Zeilen"
    assert len({r["subtitle"] for r in rows[:4]}) == 4, "je Verbindung eigene Zeilen"


@pytest.mark.asyncio
async def test_entity_rows_for_unknown_and_other_states(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(entities=["sensor.nas_cpu_auslastung", "update.wled_firmware", "person.max_mustermann"]))
    rows = (await _widget(client, headers))["data"]
    assert rows[2:] == [
        {"title": "NAS CPU Auslastung", "subtitle": "ha · sensor.nas_cpu_auslastung", "label": "unbekannt", "tone": "neutral"},
        {"title": "WLED Regal Firmware", "subtitle": "ha · update.wled_firmware", "label": "nicht verfügbar", "tone": "warn"},
        {"title": "Max Mustermann", "subtitle": "ha · person.max_mustermann", "label": "home", "tone": "neutral"},
    ]


@pytest.mark.asyncio
async def test_long_lists_are_cut_but_the_totals_stay(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(entities=[]))
    fake.states = (
        [_state(f"sensor.weg{i:03d}", "unavailable", friendly_name=f"Weg {i:03d}") for i in range(120)]
        + [_state(f"update.u{i:03d}", "on", friendly_name=f"Update {i:03d}", installed_version="1", latest_version="2") for i in range(70)]
    )
    connection = _only(await _overview(client, headers))
    assert connection["counts"] == {"total": 190, "updates": 70, "unavailable": 120, "unknown": 0}
    assert len(connection["unavailable"]) == 50 and connection["unavailable"][0]["name"] == "Weg 000"
    assert len(connection["updates"]) == 50 and connection["updates"][-1]["name"] == "Update 049"
    subtitle = (await _widget(client, headers))["data"][0]["subtitle"]
    assert subtitle == "ha · Zuhause · 70 Updates: Update 000, Update 001 und 68 weitere"


@pytest.mark.asyncio
async def test_broken_fields_in_the_answer_do_not_break_the_page(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(entities=["sensor.a", "sensor.b"]))
    fake.config = {"version": ["2026"], "location_name": {"x": 1}, "components": "kaputt"}
    fake.states = [
        None, "x", {"entity_id": "sensor.a", "state": ["on"], "attributes": None},
        {"entity_id": "sensor.b", "state": "7", "attributes": {"unit_of_measurement": 5, "friendly_name": None}},
        {"entity_id": "update.x", "state": "on", "attributes": [1, 2]},
    ]
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok"
    assert (connection["version"], connection["location_name"]) == (None, None)
    assert connection["counts"] == {"total": 2, "updates": 1, "unavailable": 0, "unknown": 0}
    assert connection["updates"] == [{"entity_id": "update.x", "name": "update.x", "installed_version": None, "latest_version": None}]
    assert connection["entities"] == [
        {"entity_id": "sensor.a", "name": "sensor.a", "status": "missing", "state": None, "unit": None},
        {"entity_id": "sensor.b", "name": "sensor.b", "status": "ok", "state": "7", "unit": None},
    ]
    assert (await client.get(f"{API}/widgets/status", headers=headers)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_token_says_what_to_do_and_asks_only_once(client, db_session, test_settings, status):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/api/config"] = lambda request: httpx.Response(status, text="no")
    fake.overrides["/api/"] = lambda request: httpx.Response(status, text="no")

    connection = _only(await _overview(client, headers))
    assert connection["state"] == "auth_failed"
    assert connection["message"].startswith("Home Assistant „ha“: Das Zugangs-Token wurde abgelehnt")
    assert f"(HTTP {status})" in connection["message"]
    assert "Profil → Sicherheit → „Langlebige Zugangs-Token“" in connection["message"] and "Verbindung testen" in connection["message"]
    assert (connection["counts"], connection["updates"], connection["entities"]) == (None, [], [])
    assert fake.paths() == ["/api/config"], "wird das Token abgelehnt, zählt Home Assistant jeden Versuch: kein zweiter Aufruf"

    rows = (await _widget(client, headers))["data"]
    assert rows == [{"title": "ha: Anmeldung fehlgeschlagen", "subtitle": connection["message"], "label": "Anmeldung fehlgeschlagen", "tone": "danger"}]
    health = await _health()
    assert health.healthy is False and "Zugangs-Token wurde abgelehnt" in health.message


@pytest.mark.asyncio
async def test_wrong_token_is_rejected_by_the_server_and_shown_as_auth_failed(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), token="falsches-token-0000")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "auth_failed"
    assert fake.requests[0].headers["Authorization"] == "Bearer falsches-token-0000"
    assert "falsches-token-0000" not in json.dumps(connection)


@pytest.mark.asyncio
async def test_a_rejected_token_is_not_asked_again_for_half_an_hour(client, db_session, test_settings):
    assert AUTH_FAILED_TTL_S == 1800.0
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), token="falsch-falsch-0000")
    service = _service()
    now = [5000.0]
    service._clock = lambda: now[0]

    assert _only(await _overview(client, headers))["state"] == "auth_failed"
    for _ in range(10):
        now[0] += 60.0
        assert _only(await _overview(client, headers))["state"] == "auth_failed"
        assert (await client.get(f"{API}/widgets/status", headers=headers)).status_code == 200
    assert len(fake.requests) == 1, "zehn Minuten lang keine weitere Anmeldung"

    assert _only(await _overview(client, headers, "?refresh=true"))["state"] == "auth_failed"
    assert len(fake.requests) == 1, "auch „Aktualisieren“ umgeht die Pause nicht"

    now[0] += AUTH_FAILED_TTL_S
    await _overview(client, headers)
    assert len(fake.requests) == 2, "nach 30 Minuten wird wieder gefragt"


@pytest.mark.asyncio
async def test_changed_settings_end_the_pause(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), token="falsch-falsch-0000")
    assert _only(await _overview(client, headers))["state"] == "auth_failed"
    assert len(fake.requests) == 1
    # Neues Token und geänderte Einstellungen (hier: eine Entität mehr).
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "home-assistant-token:ha", "value": TOKEN}, headers=headers)
    assert res.status_code == 204
    saved = await client.put(f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": [_connection(entities=["light.flur"])]}}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert not saved.json().get("secrets_cleared"), "dieselbe Adresse: das Token bleibt"
    assert _only(await _overview(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_testing_the_connection_ends_the_pause_and_asks_again(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), token="falsch-falsch-0000")
    assert _only(await _overview(client, headers))["state"] == "auth_failed"
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "home-assistant-token:ha", "value": TOKEN}, headers=headers)
    assert res.status_code == 204
    assert _only(await _overview(client, headers))["state"] == "auth_failed", "noch aus dem Zwischenspeicher"

    health = await _health()
    assert health.healthy is True
    assert _only(await _overview(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_not_home_assistant_at_this_address(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/api/config"] = lambda request: httpx.Response(404, text="404: Not Found")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error"
    assert connection["message"].startswith("Home Assistant „ha“: Unter dieser Adresse läuft kein Home Assistant, oder die API ist ausgeschaltet.")
    widget = (await _widget(client, headers))["data"]
    assert widget[0]["label"] == "Fehler" and widget[0]["tone"] == "warn"
    assert fake.paths() == ["/api/config"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        (429, "bremst Anfragen gerade (HTTP 429)"),
        (503, "gerade nicht bereit (HTTP 503)"),
        (502, "gerade nicht bereit (HTTP 502)"),
        (500, "meldet einen Fehler (HTTP 500)"),
        (418, "hat die Anfrage abgelehnt (HTTP 418)"),
    ],
)
async def test_other_status_codes_have_fixed_sentences(client, db_session, test_settings, status, fragment):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/api/config"] = lambda request: httpx.Response(status, text="Text des Servers: bitte nach evil.example.org gehen")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and fragment in connection["message"]
    assert "evil.example.org" not in connection["message"], "Text des fremden Servers steht nie in der Meldung"


@pytest.mark.asyncio
async def test_states_failing_keeps_version_and_says_why(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/api/states"] = lambda request: httpx.Response(500, text="kaputt")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and "(HTTP 500)" in connection["message"]
    assert (connection["version"], connection["location_name"]) == ("2026.10.1", "Zuhause"), "was zu lesen war, bleibt"
    assert connection["counts"] is None and connection["updates"] == [] and connection["entities"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location", "sentence"),
    [
        ("https://angreifer.example/login", "leitet auf eine andere Adresse um (HTTP 302)."),
        # Ungültiger Punycode-Name: httpx wirft einen `ValueError`, dessen Text Teile des fremden Namens nennt.
        ("http://xn--angreifer-ey9f.example/login", "leitet auf eine andere Adresse um."),
    ],
)
async def test_redirects_are_reported_never_followed_and_never_show_the_foreign_address(
    client, db_session, test_settings, caplog, location, sentence
):
    caplog.set_level(logging.INFO)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/api/config"] = lambda request: httpx.Response(302, headers={"Location": location})

    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200, res.text
    connection = _only(res.json())
    assert connection["state"] == "error"
    assert connection["message"].startswith(f"Home Assistant „ha“ {sentence}")
    assert "Trage in den Einstellungen die endgültige Adresse ein." in connection["message"]
    widget = await client.get(f"{API}/widgets/status", headers=headers)
    assert widget.status_code == 200 and "leitet auf eine andere Adresse um" in widget.text
    assert "angreifer" not in (res.text + widget.text + caplog.text).lower()
    assert "Traceback" not in caplog.text
    assert all(r.url.host == "ha.example.com" for r in fake.requests), "der Weiterleitung wird nicht gefolgt"
    assert fake.paths() == ["/api/config"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (httpx.ReadTimeout("timed out"), "antwortet nicht (Zeitüberschreitung)"),
        (httpx.ConnectTimeout("timed out"), "antwortet nicht (Zeitüberschreitung)"),
        (httpx.ConnectError("[Errno 111] Connection refused"), "lehnt die Verbindung ab"),
        (httpx.ConnectError("[Errno -2] Name or service not known"), "lässt sich nicht auflösen"),
    ],
)
async def test_unreachable_server_is_a_clean_state(client, db_session, test_settings, error, fragment):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.error = error
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "unreachable"
    assert fragment in connection["message"] and "Home Assistant „ha“" in connection["message"]
    assert connection["counts"] is None
    assert fake.paths() == ["/api/config"], "ist der Server nicht erreichbar, hilft die zweite Anfrage nicht"

    rows = (await _widget(client, headers))["data"]
    assert rows == [{"title": "ha: nicht erreichbar", "subtitle": connection["message"], "label": "nicht erreichbar", "tone": "danger"}]
    health = await _health()
    assert health.healthy is False and connection["message"] in health.message


@pytest.mark.asyncio
async def test_a_text_that_quotes_the_token_never_reaches_the_user(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.error = httpx.ConnectError(f"boom {TOKEN} boom")
    res = await client.get(f"{API}/overview", headers=headers)
    assert _only(res.json())["state"] == "unreachable"
    health = await _health()
    assert TOKEN not in res.text and TOKEN not in caplog.text and TOKEN not in json.dumps(health.model_dump(), default=str)


@pytest.mark.asyncio
async def test_answers_that_are_not_home_assistant(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    service = _service()

    for body in ("<html><body>Anmelden</body></html>", "[1, 2]", '"text"', "", "null"):
        service.clear()
        fake.overrides["/api/config"] = lambda request, body=body: httpx.Response(200, text=body)
        connection = _only(await _overview(client, headers))
        assert connection["state"] == "error" and "nicht das erwartete Format" in connection["message"], body
        assert "Führt die Adresse wirklich zu Home Assistant?" in connection["message"]

    del fake.overrides["/api/config"]
    for body in ('{"states": []}', "<html>", "7", "null"):
        service.clear()
        fake.overrides["/api/states"] = lambda request, body=body: httpx.Response(200, text=body)
        connection = _only(await _overview(client, headers))
        assert connection["state"] == "error" and "nicht das erwartete Format" in connection["message"], body
        assert connection["version"] == "2026.10.1"

    service.clear()
    fake.overrides["/api/states"] = lambda request: httpx.Response(200, content=b"[" * 100000)
    assert _only(await _overview(client, headers))["state"] == "error", "absichtlich tief verschachteltes JSON"


@pytest.mark.asyncio
async def test_oversized_answers_are_refused_while_streaming(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    service = _service()
    sent = 0

    async def endless():
        nonlocal sent
        for _ in range(100):
            sent += 1
            yield b" " * (1024 * 1024)

    fake.overrides["/api/states"] = lambda request: httpx.Response(200, content=endless())
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and "ungewöhnlich große Antwort (mehr als 16 MB)" in connection["message"]
    assert sent <= 18
    assert connection["counts"] is None and connection["version"] == "2026.10.1"

    service.clear()
    fake.overrides["/api/config"] = lambda request: httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and "(mehr als 1 MB)" in connection["message"]


@pytest.mark.asyncio
async def test_a_big_but_allowed_states_list_is_read(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(entities=["sensor.s00001"]))
    big = [_state(f"sensor.s{i:05d}", "unavailable" if i % 10 == 0 else "5", friendly_name=f"Sensor {i}", note="x" * 200) for i in range(30000)]
    fake.states = big
    assert len(json.dumps(big)) > 6 * 1024 * 1024
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok"
    assert connection["counts"]["total"] == 30000 and connection["counts"]["unavailable"] == 3000
    assert len(connection["unavailable"]) == 50
    assert connection["entities"][0]["state"] == "5"


@pytest.mark.asyncio
async def test_tls_exception_is_used_only_when_the_connection_asks_for_it(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("streng", entities=[]), _connection("lax", entities=[], insecure=True, url="https://lax.example.com"))
    body = await _overview(client, headers)
    assert [c["state"] for c in body["connections"]] == ["ok", "ok"]
    assert {r.url.host for r in fake.insecure_requests} == {"lax.example.com"}
    assert {r.url.host for r in fake.requests} == {"ha.example.com", "lax.example.com"}
    assert [r.url.host for r in fake.requests if r not in fake.insecure_requests] == ["ha.example.com"] * 2


@pytest.mark.asyncio
async def test_a_fingerprint_goes_into_the_call_instead_of_the_switch(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    url = "https://ha.example.com"
    await _configure(client, headers, _connection(entities=[], url=url, pin="ab" * 32, insecure=True))
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok", connection["message"]
    assert [r.url.path for r in fake.pinned_requests] == ["/api/config", "/api/states"]
    assert fake.insecure_requests == [], "mit Fingerabdruck wird nie ungeprueft verbunden, auch wenn der Schalter an ist"
    health = await _health()
    assert health.healthy is True
    assert [r.url.path for r in fake.pinned_requests][-1] == "/api/"


@pytest.mark.asyncio
async def test_a_changed_certificate_stops_the_connection_with_its_own_sentence(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(entities=[], url="https://ha.example.com", pin=PIN))
    mismatch = CertificateMismatch(PIN, ":".join(["CD"] * 32))
    wrapped = httpx.ConnectError("[SSL] handshake")
    wrapped.__cause__ = mismatch
    fake.pinned_error = wrapped

    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error"
    assert "anderes Zertifikat als das gemerkte" in connection["message"]
    assert "nichts gesendet" in connection["message"]
    assert "CD:CD" not in connection["message"] + caplog.text
    health = await _health()
    assert health.healthy is False and "anderes Zertifikat als das gemerkte" in health.details["ha"]["error"]


@pytest.mark.asyncio
async def test_an_unusable_fingerprint_blocks_the_connection_instead_of_skipping_the_check(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.ctx.settings.set({"connections": [
        {"name": "kaputt", "base_url": "https://ha.example.com", "tls_fingerprint_sha256": "kein-fingerabdruck", "tls_insecure_skip_verify": True},
        {"name": "http", "base_url": "http://ha.example.com:8123", "tls_fingerprint_sha256": PIN},
    ]})
    for name in ("kaputt", "http"):
        await loaded.ctx.secrets.create(label=f"home-assistant-token:{name}", kind="generic", value=TOKEN)
    body = await _overview(client, headers)
    assert [(c["name"], c["state"]) for c in body["connections"]] == [("kaputt", "error"), ("http", "error")]
    assert "Fingerabdruck" in body["connections"][0]["message"] and "nur für https://" in body["connections"][1]["message"]
    assert fake.requests == [] and fake.insecure_requests == []
    health = await _health()
    assert health.healthy is False and fake.requests == []


@pytest.mark.asyncio
async def test_results_are_cached_for_a_short_time_and_the_token_is_read_only_when_needed(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    loaded = get_extension_runtime().loaded[EXT]
    service = _service()
    now = [1000.0]
    service._clock = lambda: now[0]
    reads: list[str] = []
    real_get_handle = loaded.ctx.secrets.get_handle

    async def counting_get_handle(label):
        reads.append(label)
        return await real_get_handle(label)

    loaded.ctx.secrets.get_handle = counting_get_handle

    first = await _overview(client, headers)
    assert (await client.get(f"{API}/widgets/status", headers=headers)).status_code == 200
    assert await _overview(client, headers) == first
    assert len(fake.requests) == 2, "Seite und Kachel fragen Home Assistant nur einmal (config und states)"
    assert reads == ["home-assistant-token:ha"]

    await _overview(client, headers, "?refresh=true")
    assert len(fake.requests) == 4, "der Knopf Aktualisieren umgeht den Zwischenspeicher"
    assert reads == ["home-assistant-token:ha"] * 2

    await _overview(client, headers)
    assert len(fake.requests) == 4
    now[0] += CACHE_TTL_S - 1
    await _overview(client, headers)
    assert len(fake.requests) == 4
    now[0] += 2.0
    await _overview(client, headers)
    assert len(fake.requests) == 6, "nach 20 Sekunden wird neu gefragt"

    saved = await client.put(
        f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": [_connection(entities=["light.flur"])]}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert not saved.json().get("secrets_cleared"), "dieselbe Adresse: das Token bleibt"
    assert len(_only(await _overview(client, headers))["entities"]) == 1
    assert len(fake.requests) == 8, "nach geaenderten Einstellungen wird sofort neu gefragt"


@pytest.mark.asyncio
async def test_changing_the_address_removes_the_token_so_it_never_goes_to_a_new_target(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    assert _only(await _overview(client, headers))["state"] == "ok"
    saved = await client.put(
        f"/api/v1/extensions/{EXT}/settings",
        json={"current_password": "correct-horse-battery", "values": {"connections": [_connection(url="http://angreifer.example.com:8123")]}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["secrets_cleared"] == ["home-assistant-token:ha"]
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "not_configured"
    assert all(r.url.host != "angreifer.example.com" for r in fake.requests)


@pytest.mark.asyncio
async def test_two_connections_are_independent_and_asked_at_the_same_time(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("eins", entities=[]), _connection("zwei", entities=[], url="http://zwei.example.com:8123"))
    barrier = asyncio.Barrier(2)

    async def meet(request: httpx.Request) -> httpx.Response:
        # Beide Verbindungen müssen gleichzeitig hier sein, sonst wartet die erste vergeblich auf die zweite.
        await asyncio.wait_for(barrier.wait(), 5)
        return httpx.Response(200, json=fake.config)

    fake.overrides["/api/config"] = meet
    body = await _overview(client, headers)
    assert [(c["name"], c["state"]) for c in body["connections"]] == [("eins", "ok"), ("zwei", "ok")]
    assert body["summary"] == {"connections": 2, "updates": 4, "unavailable": 8, "unknown": 4}
    rows = (await _widget(client, headers))["data"]
    assert [r["title"] for r in rows] == ["Home Assistant 2026.10.1", "Nicht verfügbar"] * 2


@pytest.mark.asyncio
async def test_a_broken_connection_does_not_hold_up_or_break_the_others(client, db_session, test_settings, monkeypatch):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(
        client, headers, _connection("gut", entities=[]), _connection("haengt", entities=[], url="http://haengt.example.com:8123"),
        _connection("abgelehnt", entities=[], url="http://abgelehnt.example.com:8123"),
    )

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(60)
        return httpx.Response(200)

    def by_host(request: httpx.Request) -> httpx.Response:
        if request.url.host == "abgelehnt.example.com":
            return httpx.Response(401, text="no")
        if request.url.host == "haengt.example.com":
            return hang(request)  # type: ignore[return-value]
        return httpx.Response(200, json=fake.config)

    fake.overrides["/api/config"] = by_host
    # Das Modul der geladenen Erweiterung (nicht das beim Import dieser Datei, das ein Test davor aufgeräumt hat).
    monkeypatch.setattr(sys.modules[type(_service()).__module__], "CONNECTION_TIMEOUT_S", 0.3)
    body = await asyncio.wait_for(_overview(client, headers), 10)
    states = {c["name"]: c["state"] for c in body["connections"]}
    assert states == {"gut": "ok", "haengt": "unreachable", "abgelehnt": "auth_failed"}
    assert body["summary"]["connections"] == 3
    rows = (await _widget(client, headers))["data"]
    assert [r["title"] for r in rows] == ["Home Assistant 2026.10.1", "Nicht verfügbar", "haengt: nicht erreichbar", "abgelehnt: Anmeldung fehlgeschlagen"]


@pytest.mark.asyncio
async def test_unusable_entries_become_error_cards_without_a_request(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.ctx.settings.set({"connections": [
        {"name": "ok", "base_url": BASE},
        {"name": "ok", "base_url": BASE},
        {"name": "pw", "base_url": "http://nutzer:passwort@ha.example.com:8123"},
        {"name": "gross", "base_url": BASE, "entities": ["Sensor.X"]},
    ]})
    await loaded.ctx.secrets.create(label="home-assistant-token:ok", kind="generic", value=TOKEN)
    body = await _overview(client, headers)
    states = [(c["name"], c["state"]) for c in body["connections"]]
    assert states == [("ok", "ok"), ("ok", "error"), ("pw", "error"), ("gross", "error")]
    assert "mehrfach vergeben" in body["connections"][1]["message"]
    assert "passwort" not in json.dumps(body)
    assert body["connections"][2]["url"] is None
    assert len(fake.requests) == 2 and {r.url.host for r in fake.requests} == {"ha.example.com"}, "nur der brauchbare Eintrag fragt an"


@pytest.mark.asyncio
async def test_unexpected_errors_do_not_become_a_500(client, db_session, test_settings, caplog):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    service = _service()

    async def broken(cfg, has_token):
        raise RuntimeError("innerer Fehler mit Details")

    service._load = broken
    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200, res.text
    connection = _only(res.json())
    assert connection["state"] == "error" and connection["message"] == "Bei der Abfrage von ha ist ein unerwarteter Fehler aufgetreten."
    assert "Details" not in res.text
    assert "innerer Fehler" in caplog.text, "im Protokoll steht die Ursache"
    assert (await client.get(f"{API}/widgets/status", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_unreadable_or_odd_tokens_are_a_hint_not_a_crash(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    loaded = get_extension_runtime().loaded[EXT]
    real = loaded.ctx.secrets.get_handle

    async def boom(label):
        raise RuntimeError("Tresor kaputt " + TOKEN)

    loaded.ctx.secrets.get_handle = boom
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and "aus dem Tresor lesen" in connection["message"] and TOKEN not in json.dumps(connection)
    assert fake.requests == []
    loaded.ctx.secrets.get_handle = real

    # Ein Token mit Leerzeichen oder Zeilenumbruch wird nie gesendet (httpx zitiert den Header in seiner Fehlermeldung).
    for odd in ("abc def", "abc\r\nX-Evil: 1", "tökén"):
        _service().clear()
        await _configure(client, headers, _connection(), token=odd)
        connection = _only(await _overview(client, headers))
        assert connection["state"] == "error" and "Zeichen, die in einem Token nicht vorkommen" in connection["message"], odd
    assert fake.requests == []


@pytest.mark.asyncio
async def test_routes_need_a_login_and_hosts_read_and_there_is_nothing_to_write(client, db_session, test_settings):
    from nodvard_deck.core import security
    from nodvard_deck.models import Role, RolePermission, User

    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    for path in ("/overview", "/widgets/status"):
        assert (await client.get(f"{API}{path}")).status_code == 401, path
    assert fake.requests == []

    roles = {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=headers)).json()}
    created = await client.post(
        "/api/v1/users", json={"username": "gast", "password": "gast-passwort-1", "role_ids": [roles["viewer"]]}, headers=headers,
    )
    assert created.status_code == 201, created.text
    login = await client.post("/api/v1/auth/login", json={"username": "gast", "password": "gast-passwort-1"})
    viewer = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get(f"{API}/overview", headers=viewer)).status_code == 200
    assert (await client.get(f"{API}/widgets/status", headers=viewer)).status_code == 200

    # Ein Konto ohne `hosts.read` bekommt 403 und löst keine Abfrage aus.
    custom = Role(name="rolle-ohne-hosts", permissions=[RolePermission(permission="notifications.read")])
    db_session.add(custom)
    user = User(username="ohnehosts", password_hash=security.hash_password("ohne-hosts-1"), is_active=True)
    user.roles.append(custom)
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": "ohnehosts", "password": "ohne-hosts-1"})
    assert login.status_code == 200, login.text
    nobody = {"Authorization": f"Bearer {login.json()['access_token']}"}
    before = len(fake.requests)
    for path in ("/overview", "/widgets/status", "/overview?refresh=true"):
        assert (await client.get(f"{API}{path}", headers=nobody)).status_code == 403, path
    assert len(fake.requests) == before

    # Nur lesend: es gibt nichts, was man aufrufen oder vorschlagen könnte.
    for method in ("post", "put", "delete", "patch"):
        res = await getattr(client, method)(f"{API}/overview", headers=headers)
        assert res.status_code in (404, 405), (method, res.status_code)


def test_openapi_describes_the_overview_with_typed_fields():
    from nodvard_deck_ext_home_assistant.models import Overview

    schema = Overview.model_json_schema()
    assert set(schema["properties"]) == {"connections", "summary"}
    assert set(schema["$defs"]) >= {"Connection", "Counts", "EntityValue", "Summary", "UnavailableItem", "UpdateItem"}
    assert set(schema["$defs"]["Connection"]["properties"]) == {
        "name", "url", "state", "message", "version", "location_name", "counts", "updates", "unavailable", "entities",
    }
    assert set(schema["$defs"]["Counts"]["properties"]) == {"total", "updates", "unavailable", "unknown"}
    assert set(schema["$defs"]["EntityValue"]["properties"]) == {"entity_id", "name", "status", "state", "unit"}
    assert set(schema["$defs"]["UpdateItem"]["properties"]) == {"entity_id", "name", "installed_version", "latest_version"}
    assert set(schema["$defs"]["UnavailableItem"]["properties"]) == {"entity_id", "name"}
    assert set(schema["$defs"]["Summary"]["properties"]) == {"connections", "updates", "unavailable", "unknown"}


@pytest.mark.asyncio
async def test_openapi_of_the_running_app_lists_the_route_with_its_model(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    # Außerhalb des Entwicklungsmodus gibt es /openapi.json nicht, die Beschreibung liefert der Kern angemeldet.
    paths = (await client.get("/api/v1/system/openapi.json", headers=headers)).json()["paths"]
    path = paths[f"{API}/overview"]["get"]
    assert path["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/Overview")
    assert [p["name"] for p in path["parameters"]] == ["refresh"]
    assert set(paths[f"{API}/overview"]) == {"get"} and set(paths[f"{API}/widgets/status"]) == {"get"}


@pytest.mark.asyncio
async def test_testing_the_connection_checks_every_connection_with_the_api_root(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("eins"), _connection("zwei", url="http://zwei.example.com:8123"))
    health = await _health()
    assert health.healthy is True and health.message == "eins: in Ordnung; zwei: in Ordnung"
    assert sorted(fake.paths()) == ["/api/", "/api/"], "nur die Erreichbarkeit und das Token, keine Listen"
    assert {r.headers["Authorization"] for r in fake.requests} == {f"Bearer {TOKEN}"}
    assert health.details == {"eins": {"healthy": True}, "zwei": {"healthy": True}}

    # Durch die echte Route „Verbindung testen“.
    tested = await client.post(f"/api/v1/extensions/{EXT}/test", headers=headers)
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True and tested.json()["message"] == "Alle 2 Verbindungen funktionieren."
    assert TOKEN not in tested.text


@pytest.mark.asyncio
async def test_testing_the_connection_names_each_problem_with_a_fixed_sentence(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(
        client, headers, _connection("gut"), _connection("alt", url="http://alt.example.com:8123"),
        _connection("kein-token", url="http://k.example.com:8123"), token=None,
    )
    for name in ("gut", "alt"):
        res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": f"home-assistant-token:{name}", "value": TOKEN}, headers=headers)
        assert res.status_code == 204
    fake.overrides["/api/"] = lambda request: (
        httpx.Response(401, text="no") if request.url.host == "alt.example.com" else httpx.Response(200, json=fake.root)
    )
    health = await _health()
    assert health.healthy is False
    assert health.details["gut"] == {"healthy": True}
    assert health.details["alt"]["healthy"] is False and "Zugangs-Token wurde abgelehnt" in health.details["alt"]["error"]
    assert health.details["kein-token"]["error"].startswith("Für kein-token fehlt das Zugangs-Token.")
    assert all(d["verbatim"] is True for d in health.details.values() if not d["healthy"])
    assert [r.url.host for r in fake.requests if r.url.host == "k.example.com"] == [], "ohne Token keine Anfrage"

    tested = await client.post(f"/api/v1/extensions/{EXT}/test", headers=headers)
    body = tested.json()
    assert body["ok"] is False and body["message"] == "2 von 3 Verbindungen funktionieren nicht – Einzelheiten unten."
    by_name = {d["name"]: d for d in body["details"]}
    assert by_name["gut"]["ok"] is True
    assert by_name["alt"]["message"].startswith("Home Assistant „alt“: Das Zugangs-Token wurde abgelehnt (HTTP 401).")
    assert TOKEN not in tested.text


@pytest.mark.asyncio
async def test_testing_the_connection_survives_surprises(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/api/"] = lambda request: httpx.Response(200, text="<html>Anmelden</html>")
    health = await _health()
    assert health.healthy is False and "nicht das erwartete Format" in health.details["ha"]["error"]

    service = _service()

    async def broken(cfg):
        raise RuntimeError("innerer Fehler " + TOKEN)

    service._test_one = broken
    health = await _health()
    assert health.healthy is False
    # `gather` gibt die Ausnahme weiter; health() fängt sie und nennt nur den Typ.
    assert TOKEN not in health.message


@pytest.mark.asyncio
async def test_each_connection_is_suggested_as_an_app_without_any_request(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    assert (await client.get("/api/v1/apps/suggestions", headers=headers)).json()["suggestions"] == []

    await _configure(client, headers, _connection(url="http://203.0.113.10:8123"), token=None)
    res = await client.get("/api/v1/apps/suggestions", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json() == {
        "suggestions": [{"name": "Home Assistant", "url": "http://203.0.113.10:8123", "source": "Home Assistant", "host": None}],
        "errors": [],
    }

    await _configure(client, headers, _connection("haus", url="http://203.0.113.10:8123"), _connection("ferien", url="https://ferien.example.com"), token=None)
    suggestions = (await client.get("/api/v1/apps/suggestions", headers=headers)).json()["suggestions"]
    assert [(s["name"], s["url"]) for s in suggestions] == [
        ("Home Assistant ferien", "https://ferien.example.com"), ("Home Assistant haus", "http://203.0.113.10:8123"),
    ]
    assert fake.requests == [], "ein Vorschlag liest weder das Token noch ruft er Home Assistant auf"


@pytest.mark.asyncio
async def test_unusable_entries_are_not_suggested(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.ctx.settings.set({"connections": [
        {"name": "ok", "base_url": "http://ha.example.com:8123"},
        {"name": "pw", "base_url": "http://nutzer:passwort@ha.example.com:8123"},
        {"name": "leer", "base_url": ""},
    ]})
    suggestions = (await client.get("/api/v1/apps/suggestions", headers=headers)).json()["suggestions"]
    assert [(s["name"], s["url"]) for s in suggestions] == [("Home Assistant", "http://ha.example.com:8123")]
    assert "passwort" not in json.dumps(suggestions)


def test_only_get_requests_exist_in_the_client():
    """Der Client kennt nur `GET`."""
    source = (REPO_EXTENSIONS_DIR / "home-assistant" / "src" / "nodvard_deck_ext_home_assistant")
    text = "".join(p.read_text(encoding="utf-8") for p in source.glob("*.py"))
    assert not re.search(r"\.(post|put|patch|delete|request)\(", text)
    assert not re.search(r"""["'](POST|PUT|PATCH|DELETE)["']""", text)
    assert text.count('"GET"') == 1, "genau eine Stelle schickt eine Anfrage"
