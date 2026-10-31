"""Tests für die Uptime-Kuma-Erweiterung; Uptime Kuma ist ein `httpx.MockTransport`."""

from __future__ import annotations

import asyncio
import base64
import importlib
import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import AuditEntry, ExtensionRecord
from nodvard_deck.services import extensions as extensions_service
from sqlalchemy import select

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


class _ExtModule:
    """Ein Modul der Erweiterung, geladen erst beim Zugriff im Test, wenn die Fixture den Pfad gesetzt hat."""

    def __init__(self, name: str) -> None:
        self._name = f"nodvard_deck_ext_uptime_kuma.{name}"

    def __getattr__(self, attr: str) -> Any:
        return getattr(importlib.import_module(self._name), attr)


metrics = _ExtModule("metrics")
statuspage = _ExtModule("statuspage")
transport = _ExtModule("transport")
kuma_config = _ExtModule("config")

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "uptime_kuma"
BASE = "http://kuma.test:3001"
KEY = "kuma-schluessel-9f3a7c21"
EXT = "uptime-kuma"
API = "/api/v1/ext/uptime-kuma"


@pytest.fixture(autouse=True)
def _uptime_kuma_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_EXTENSIONS_DIR / "uptime-kuma" / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _json(name: str) -> Any:
    return json.loads(_text(name))


def _basic(key: str = KEY) -> str:
    return "Basic " + base64.b64encode(f":{key}".encode()).decode()


def _metric_lines(monitors: list[tuple[str, int]]) -> str:
    """Minimale /metrics-Antwort: (Name, Status) je Monitor, dazu allgemeine Messwerte."""
    lines = ["# TYPE process_cpu_user_seconds_total counter", "process_cpu_user_seconds_total 1.5"]
    for name, status in monitors:
        lines.append(
            f'monitor_status{{monitor_name="{name}",monitor_type="http",monitor_url="https://{name.lower()}.example.com/",'
            f'monitor_hostname="null",monitor_port="null"}} {status}'
        )
    return "\n".join(lines) + "\n"


def test_parser_reads_a_realistic_metrics_answer():
    samples, total = metrics.parse_prometheus(_text("metrics_basic.txt"))
    assert total == 21, "alle Reihen gezaehlt, auch die allgemeinen Messwerte des Programms"
    found = {m.name: m for m in metrics.collect_monitors(samples)}
    assert list(found) == ["Nextcloud", "Wiki", "Intern", "Router", "NAS", 'Backup "Nacht" \\ Job']

    cloud = found["Nextcloud"]
    assert (cloud.type, cloud.url, cloud.status, cloud.response_ms) == ("http", "https://cloud.example.com/", 1, 123)
    assert (cloud.cert_days_left, cloud.cert_valid) == (62, True)
    assert found["Wiki"].cert_days_left == 9

    router = found["Router"]
    assert router.status == 0
    assert router.response_ms is None, "-1 heißt nicht gemessen"
    assert router.url == "198.51.100.1", "ohne Adresse: der Rechner"
    assert found["NAS"].url == "198.51.100.20:5000", "Rechner und Port"
    assert found["NAS"].response_ms is None, "NaN heißt nicht gemessen"
    assert found["NAS"].status == 2 and found['Backup "Nacht" \\ Job'].status == 3
    assert found['Backup "Nacht" \\ Job'].url is None, "alle Ziel-Labels sind null"

    intern = found["Intern"]
    assert intern.cert_days_left is None and intern.cert_valid is False
    assert intern.url == "https://intern.example.com:8443/status", "ohne Benutzer, Passwort und Suchteil"


def test_parser_merges_by_monitor_id_and_keeps_same_named_monitors_apart():
    samples, _ = metrics.parse_prometheus(_text("metrics_with_ids.txt"))
    monitors = metrics.collect_monitors(samples)
    assert [(m.id, m.name, m.status) for m in monitors] == [("1", "Nextcloud", 1), ("2", "Router", 0), ("3", "Router", 1)]
    assert monitors[1].url == "198.51.100.1" and monitors[2].url == "198.51.100.2"
    assert monitors[0].response_ms == 121


def test_parser_without_ids_keeps_two_monitors_with_the_same_name_apart_by_their_target():
    text = (
        'monitor_status{monitor_name="Web",monitor_type="http",monitor_url="https://a.example.com/",monitor_hostname="null",monitor_port="null"} 1\n'
        'monitor_status{monitor_name="Web",monitor_type="http",monitor_url="https://b.example.com/",monitor_hostname="null",monitor_port="null"} 0\n'
        'monitor_response_time{monitor_name="Web",monitor_type="http",monitor_url="https://b.example.com/",monitor_hostname="null",monitor_port="null"} 77\n'
    )
    samples, _ = metrics.parse_prometheus(text)
    monitors = metrics.collect_monitors(samples)
    assert [(m.url, m.status, m.response_ms) for m in monitors] == [
        ("https://a.example.com/", 1, None), ("https://b.example.com/", 0, 77),
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("NaN", None), ("+Inf", None), ("-Inf", None), ("Inf", None), ("1.5e+02", 150.0), ("-1", -1.0), ("0", 0.0),
    ],
)
def test_parser_values(raw, expected):
    sample = metrics.parse_line(f'monitor_status{{monitor_name="x"}} {raw}')
    assert sample is not None
    assert sample.value == expected


def test_parser_label_values_with_escapes_braces_and_commas():
    sample = metrics.parse_line(r'monitor_status{monitor_name="a \"b\" \\ c\nd } e, f=g",monitor_type="http",} 1')
    assert sample is not None
    assert sample.labels == {"monitor_name": 'a "b" \\ c\nd } e, f=g', "monitor_type": "http"}
    # Ein unbekannter Backslash-Ausdruck bleibt, wie er ist.
    odd = metrics.parse_line(r'm{a="x\qy"} 2')
    assert odd is not None and odd.labels == {"a": r"x\qy"}


def test_parser_accepts_a_timestamp_and_windows_line_endings():
    samples, total = metrics.parse_prometheus('monitor_status{monitor_name="x"} 1 1727000000000\r\n# Kommentar\r\n\r\n')
    assert total == 1 and samples[0].value == 1.0


@pytest.mark.parametrize(
    "line",
    [
        "", "   ", "# HELP monitor_status Text", "garbage line here", "<html>", 'monitor_status{monitor_name="x"',
        'monitor_status{monitor_name="x} 1', 'monitor_status{monitor_name=x} 1', "monitor_status", "monitor_status abc",
        "monitor_status 1 2 3", '{monitor_name="x"} 1',
    ],
)
def test_parser_skips_lines_it_does_not_understand(line):
    assert metrics.parse_line(line) is None


def test_parser_counts_nothing_in_a_login_page():
    samples, total = metrics.parse_prometheus("<!DOCTYPE html>\n<html><body>Bitte anmelden</body></html>\n")
    assert (samples, total) == ([], 0)


def test_label_value_treats_null_and_empty_as_missing():
    assert metrics.label_value({"a": "null"}, "a") is None
    assert metrics.label_value({"a": " "}, "a") is None
    assert metrics.label_value({}, "a") is None
    assert metrics.label_value({"a": " x "}, "a") == "x"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://admin:" + "geheim@intern.example.com:8443/status?token=abc#x", "https://intern.example.com:8443/status"),
        ("http://[2001:db8::1]:8080/x", "http://[2001:db8::1]:8080/x"),
        ("https://example.com/", "https://example.com/"),
        ("keine adresse", None), ("", None), (None, None), ("http://host:99999/x", None),
    ],
)
def test_safe_url_drops_credentials_and_query(url, expected):
    assert metrics.safe_url(url) == expected


def _page():
    monitors = statuspage.parse_monitors(_json("status_page.json"))
    assert monitors is not None
    page = statuspage.parse_heartbeats(_json("status_heartbeat.json"), monitors)
    assert page is not None
    return page


def test_status_page_uptime_last_beat_and_outages():
    page = _page()
    assert [(m.id, m.name, m.type) for m in page.monitors] == [
        ("1", "Nextcloud", "http"), ("2", "Wiki", "keyword"), ("3", "Router", "ping"), ("4", "NAS", "port"),
    ]
    assert page.uptime_by_id == {"1": 100.0, "2": 99.93, "3": 94.12, "4": 50.0}
    assert page.uptime_for("Router") == 94.12 and page.uptime_for("gibt es nicht") is None
    assert page.last_beat["3"].status == 0 and page.last_beat["1"].ping == 118
    assert page.last_beat["2"].time == datetime(2026, 10, 12, 8, 2, tzinfo=UTC)
    # Neueste zuerst; drei Fehlschläge des Routers hintereinander sind EIN Ausfall (der erste zählt).
    assert [(o.monitor, o.time, o.message) for o in page.outages] == [
        ("Wiki", datetime(2026, 10, 12, 8, 1, tzinfo=UTC), "Keyword nicht gefunden"),
        ("Router", datetime(2026, 10, 12, 7, 59, tzinfo=UTC), "Request timeout"),
    ]


def test_status_page_caps_outages_and_shortens_messages():
    monitors = [statuspage.PageMonitor("1", "Dienst")]
    beats = []
    for i in range(15):  # abwechselnd aus/an: 15 getrennte Ausfälle
        beats.append({"status": 0, "time": f"2026-10-12 08:{2 * i:02d}:00.000", "msg": f"Fehler {i}", "ping": None})
        beats.append({"status": 1, "time": f"2026-10-12 08:{2 * i + 1:02d}:00.000", "msg": "", "ping": 5})
    beats[0]["msg"] = "  lang\n" + "x" * 500
    page = statuspage.parse_heartbeats({"heartbeatList": {"1": beats}, "uptimeList": {}}, monitors)
    assert page is not None
    assert len(page.outages) == statuspage.MAX_OUTAGES == 10
    assert page.outages[0].message == "Fehler 14", "neueste zuerst"
    oldest_kept = page.outages[-1]
    assert oldest_kept.message == "Fehler 5"
    # Die Meldung des ersten Ausfalls wird gekürzt (der steht hier nicht mehr in den zehn neuesten).
    short = statuspage.parse_heartbeats({"heartbeatList": {"1": beats[:2]}, "uptimeList": {}}, monitors)
    assert short is not None and len(short.outages[0].message) == statuspage.MESSAGE_LIMIT
    assert short.outages[0].message.startswith("lang xxx")


def test_status_page_beats_are_sorted_even_if_the_server_sends_them_newest_first():
    monitors = [statuspage.PageMonitor("1", "Dienst")]
    beats = [
        {"status": 1, "time": "2026-10-12 08:02:00.000", "msg": "", "ping": 9},
        {"status": 0, "time": "2026-10-12 08:01:00.000", "msg": "weg", "ping": None},
        {"status": 1, "time": "2026-10-12 08:00:00.000", "msg": "", "ping": 8},
    ]
    page = statuspage.parse_heartbeats({"heartbeatList": {"1": beats}, "uptimeList": {}}, monitors)
    assert page is not None
    assert page.last_beat["1"].status == 1 and page.last_beat["1"].ping == 9
    assert [o.message for o in page.outages] == ["weg"]


def test_status_page_uptime_is_clamped_and_garbage_is_ignored():
    monitors = [statuspage.PageMonitor(str(i), f"M{i}") for i in range(1, 6)]
    uptimes = {"1_24": 1.5, "2_24": -0.2, "3_24": float("nan"), "4_24": "kaputt", "5_24": 0.123456}
    page = statuspage.parse_heartbeats({"heartbeatList": {}, "uptimeList": uptimes}, monitors)
    assert page is not None
    assert page.uptime_by_id == {"1": 100.0, "2": 0.0, "5": 12.35}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-12 08:01:00.123", datetime(2026, 10, 12, 8, 1, 0, 123000, tzinfo=UTC)),
        ("2026-10-12 08:01:00", datetime(2026, 10, 12, 8, 1, tzinfo=UTC)),
        ("2026-10-12T08:01:00Z", datetime(2026, 10, 12, 8, 1, tzinfo=UTC)),
        ("2026-10-12T10:01:00+02:00", datetime(2026, 10, 12, 8, 1, tzinfo=UTC)),
        ("gestern", None), ("", None), (None, None), (12345, None),
    ],
)
def test_kuma_time_is_read_as_utc(text, expected):
    assert statuspage.parse_kuma_time(text) == expected


def test_status_page_rejects_answers_of_the_wrong_shape():
    assert statuspage.parse_monitors("kein objekt") is None
    assert statuspage.parse_monitors({"config": {}}) is None
    assert statuspage.parse_monitors({"publicGroupList": [{"monitorList": [{"id": True, "name": "x"}, "x", {"id": 3}]}, 5]}) == []
    assert statuspage.parse_heartbeats({"heartbeatList": []}, []) is None
    assert statuspage.parse_heartbeats(None, []) is None


def test_base_url_is_normalized_and_credentials_are_refused():
    assert kuma_config.normalize_base_url("http://kuma.test:3001/") == "http://kuma.test:3001"
    assert kuma_config.normalize_base_url(" https://kuma.example.com/kuma/ ") == "https://kuma.example.com/kuma"
    assert kuma_config.normalize_base_url("http://kuma.test:3001/?x=1#y") == "http://kuma.test:3001"
    # Aus dem Browser kopiert: Seiten der Oberfläche fallen weg, ein Präfix hinter einem Proxy bleibt.
    assert kuma_config.normalize_base_url("http://203.0.113.10:3001/dashboard") == "http://203.0.113.10:3001"
    assert kuma_config.normalize_base_url("http://kuma.test/kuma") == "http://kuma.test/kuma"
    assert kuma_config.normalize_base_url("http://kuma.test:3001/dashboard/12") == "http://kuma.test:3001"
    assert kuma_config.normalize_base_url("http://kuma.test:3001/manage-status-page") == "http://kuma.test:3001"
    assert kuma_config.normalize_base_url("http://kuma.test:3001/settings/notifications") == "http://kuma.test:3001"
    assert kuma_config.normalize_base_url("http://kuma.test:3001/status/heim") == "http://kuma.test:3001"
    assert kuma_config.normalize_base_url("http://kuma.test:3001/status-page/heim") == "http://kuma.test:3001"
    assert kuma_config.normalize_base_url("http://kuma.test:3001/metrics") == "http://kuma.test:3001"
    assert kuma_config.normalize_base_url("https://kuma.example.com/kuma/Dashboard/") == "https://kuma.example.com/kuma"
    for bad in ("kuma.test:3001", "ftp://kuma.test", "http://", "http://user:" + "pw@kuma.test", "http://@kuma.test", "http://kuma.test:999999", ""):
        with pytest.raises(ValueError):
            kuma_config.normalize_base_url(bad)


def test_read_connections_flags_unusable_entries_without_dropping_the_rest():
    configs = kuma_config.read_connections({"connections": [
        {"name": "a", "base_url": "http://a.test:3001/", "status_page_slug": "heim", "tls_insecure_skip_verify": True},
        {"name": "a", "base_url": "http://zweite.test"},
        {"name": "b", "base_url": "http://user:" + "pw@b.test"},
        {"name": "c", "base_url": "http://c.test", "status_page_slug": "Gross Geschrieben"},
        {"name": " ", "base_url": "http://leer.test"},
        "kaputt",
    ]})
    assert [c.name for c in configs] == ["a", "a", "b", "c"]
    assert (configs[0].base_url, configs[0].status_page_slug, configs[0].insecure_tls, configs[0].problem) == (
        "http://a.test:3001", "heim", True, None,
    )
    assert "mehrfach vergeben" in (configs[1].problem or "")
    assert "ungültig" in (configs[2].problem or "") and "pw" not in (configs[2].problem or "")
    assert "Status-Seite" in (configs[3].problem or "")
    assert kuma_config.read_connections({}) == [] and kuma_config.read_connections({"connections": "x"}) == []


class _Http:
    """Was `transport.get()` von `ctx.http` braucht."""

    def __init__(self, handler: Callable[[httpx.Request], Any]) -> None:
        self._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.insecure: list[bool] = []

    def stream(self, method: str, url: str, *, insecure_tls: bool = False, **kwargs: Any):
        self.insecure.append(insecure_tls)
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
    with pytest.raises(transport.KumaError) as caught:
        await transport.get(http, "http://kuma.test/metrics", service="Uptime Kuma „kuma“")
    assert caught.value.state == "error"
    assert "ungewöhnlich große Antwort (mehr als 2 MB)" in str(caught.value)
    assert sent <= 4, "nach dem Limit wird nicht weitergelesen"


@pytest.mark.asyncio
async def test_transport_gives_up_after_the_total_time_limit():
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    http = _Http(slow)
    with pytest.raises(transport.Unreachable) as caught:
        await transport.get(http, "http://kuma.test/metrics", service="Uptime Kuma", total_timeout_s=0.05)
    assert "antwortet nicht (Zeitüberschreitung)" in str(caught.value)


@pytest.mark.asyncio
async def test_transport_passes_the_tls_choice_and_returns_other_status_codes():
    http = _Http(lambda request: httpx.Response(503, content=b"nope"))
    reply = await transport.get(http, "http://kuma.test/metrics", service="x", insecure_tls=True)
    assert (reply.status_code, reply.body) == (503, b"nope")
    assert http.insecure == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "state", "fragment"),
    [
        (httpx.ReadTimeout("timed out"), "unreachable", "antwortet nicht (Zeitüberschreitung)"),
        (httpx.ConnectError("[Errno 111] Connection refused"), "unreachable", "ist nicht erreichbar ([Errno 111] Connection refused)"),
        (httpx.ConnectError("[Errno -2] Name or service not known"), "unreachable", "Name or service not known"),
        (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), "error", "Selbstsigniertes Zertifikat erlauben"),
        (httpx.RemoteProtocolError("illegal header line: bytearray(b'X-Geheim: abc')"), "error", "Verbindung abgebrochen oder eine fehlerhafte Antwort"),
        (httpx.LocalProtocolError("Illegal header value b'Basic xyz'"), "error", "fehlerhafte Antwort"),
    ],
)
async def test_transport_error_sentences(error, state, fragment):
    def boom(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(transport.KumaError) as caught:
        await transport.get(_Http(boom), "http://kuma.test/metrics", service="Uptime Kuma „kuma“")
    assert caught.value.state == state
    assert fragment in str(caught.value)
    assert "X-Geheim" not in str(caught.value) and "xyz" not in str(caught.value), "der Text der Ausnahme wird nicht zitiert"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


class FakeKuma:
    """Uptime Kuma mit /metrics (Anmeldung per API-Schlüssel) und der Status-Seite `heim`."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.insecure_requests: list[httpx.Request] = []
        self.metrics_text = _text("metrics_basic.txt")
        self.page = _json("status_page.json")
        self.heartbeats = _json("status_heartbeat.json")
        self.overrides: dict[str, Callable[[httpx.Request], httpx.Response]] = {}
        self.error: Exception | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        path = request.url.path
        if path in self.overrides:
            return self.overrides[path](request)
        if path == "/metrics":
            if request.headers.get("Authorization") != _basic():
                return httpx.Response(401, text="Unauthorized")
            return httpx.Response(200, text=self.metrics_text, headers={"Content-Type": "text/plain; version=0.0.4"})
        if path == "/api/status-page/heim":
            return httpx.Response(200, json=self.page)
        if path == "/api/status-page/heartbeat/heim":
            return httpx.Response(200, json=self.heartbeats)
        return httpx.Response(404, json={"msg": "Not Found"})

    def insecure_handler(self, request: httpx.Request) -> httpx.Response:
        self.insecure_requests.append(request)
        return self.handler(request)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


async def _login(client, username="owner1", password="correct-horse-battery") -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _enable(client, db_session, test_settings) -> tuple[dict, FakeKuma]:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    headers = await _login(client)
    enabled = await client.post(f"/api/v1/extensions/{EXT}/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text

    fake = FakeKuma()
    http = get_extension_runtime().loaded[EXT].ctx.http
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    http._insecure_client = httpx.AsyncClient(transport=httpx.MockTransport(fake.insecure_handler))
    return headers, fake


def _connection(name="kuma", *, slug="heim", insecure=False, url=BASE) -> dict:
    entry: dict[str, Any] = {"name": name, "base_url": url}
    if slug:
        entry["status_page_slug"] = slug
    if insecure:
        entry["tls_insecure_skip_verify"] = True
    return entry


async def _configure(client, headers, *connections: dict, key: str | None = KEY) -> None:
    saved = await client.put(f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": list(connections)}}, headers=headers)
    assert saved.status_code == 200, saved.text
    if key is not None:
        for connection in connections:
            res = await client.put(
                f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": f"uptime-kuma-key:{connection['name']}", "value": key}, headers=headers,
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


@pytest.mark.asyncio
async def test_extension_is_discovered_disabled_beta_and_registers_page_and_widget(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    record = await db_session.get(ExtensionRecord, EXT)
    assert record is not None and record.state == "disabled"

    headers, _ = await _enable(client, db_session, test_settings)
    assert record.state == "enabled", record.last_error
    row = next(r for r in (await client.get("/api/v1/extensions", headers=headers)).json() if r["id"] == EXT)
    assert (row["name"], row["stage"], row["category"]) == ("Uptime Kuma", "beta", "servers")

    pages = (await client.get("/api/v1/pages", headers=headers)).json()
    page = next(p for p in pages if p["ext_id"] == EXT)
    assert (page["path"], page["component"], page["title"], page["stage"]) == ("/uptime-kuma", "UptimeKumaPage", "Uptime Kuma", "beta")
    widgets = [w for w in (await client.get("/api/v1/widgets", headers=headers)).json() if w["ext_id"] == EXT]
    assert [w["id"] for w in widgets] == ["status"]
    assert widgets[0]["view"]["item"]["badge"] == {"text": "{{ label }}", "tone": "{{ tone }}"}
    assert widgets[0]["data_endpoint"] == "widgets/status"

    bundle = await client.get(f"/api/v1/extensions/{EXT}/frontend/index.js")
    assert bundle.status_code == 200
    assert "UptimeKumaPage" in bundle.text


@pytest.mark.asyncio
async def test_nothing_configured_is_reported_cleanly(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    body = await _overview(client, headers)
    assert body == {"connections": [], "summary": {"total": 0, "up": 0, "down": 0, "pending": 0, "maintenance": 0}}

    widget = await _widget(client, headers)
    assert widget["data"] == [{
        "title": "Uptime Kuma nicht eingerichtet", "subtitle": "Unter Einstellungen → Erweiterungen → Uptime Kuma eintragen.",
        "label": "nicht eingerichtet", "tone": "neutral",
    }]
    loaded = get_extension_runtime().loaded[EXT]
    health = await loaded.instance.health(loaded.ctx)
    assert health.healthy is False and "Noch keine Verbindung" in health.message
    assert fake.requests == [], "ohne Einstellungen wird nichts angefragt"


@pytest.mark.asyncio
async def test_connection_without_key_and_status_page_is_not_configured(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(slug=""), key=None)

    connection = _only(await _overview(client, headers))
    assert connection == {
        "name": "kuma", "url": BASE, "state": "not_configured", "message": "Für kuma fehlt der API-Schlüssel oder eine Status-Seite.",
        "monitors": [], "summary": None, "outages": [], "status_page": False,
    }
    widget = (await _widget(client, headers))["data"]
    assert widget == [{
        "title": "kuma: nicht eingerichtet", "subtitle": "Für kuma fehlt der API-Schlüssel oder eine Status-Seite.",
        "label": "nicht eingerichtet", "tone": "neutral",
    }]
    assert fake.requests == []

    # Ein nachgetragener Schlüssel gilt sofort, auch innerhalb der 20 Sekunden des Zwischenspeichers.
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "uptime-kuma-key:kuma", "value": KEY}, headers=headers)
    assert res.status_code == 204
    assert _only(await _overview(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_widget_meta_says_not_configured_only_without_any_set_up_connection(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    # Nichts eingetragen: die Übersicht darf die Kachel ausblenden; die übrigen `meta`-Felder bleiben.
    assert (await _widget(client, headers))["meta"] == {"connections": 0, "total": 0, "down": 0, "state": "not_configured"}

    # Ohne Schlüssel und ohne Status-Seite ist die Verbindung nicht eingerichtet; die Textzeile bleibt für ältere Oberflächen.
    await _configure(client, headers, _connection(slug=""), key=None)
    widget = await _widget(client, headers)
    assert widget["meta"]["state"] == "not_configured" and widget["meta"]["connections"] == 1
    assert widget["data"][0]["label"] == "nicht eingerichtet"

    # Mit Schlüssel (und Status-Seite) gilt die Verbindung als eingerichtet, auch wenn der Server nicht antwortet.
    await _configure(client, headers, _connection())
    fake.error = httpx.ConnectError("refused")
    widget = await _widget(client, headers)
    assert "state" not in widget["meta"] and widget["meta"]["connections"] == 1
    assert _only(await _overview(client, headers))["state"] == "unreachable"

    # Eine eingerichtete und eine nicht eingerichtete Verbindung: das Modul ist eingerichtet.
    await _configure(client, headers, _connection(), _connection("zwei", slug=""), key=None)
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "uptime-kuma-key:kuma", "value": KEY}, headers=headers)
    assert res.status_code == 204
    assert [c["state"] for c in (await _overview(client, headers))["connections"]] == ["unreachable", "not_configured"]
    widget = await _widget(client, headers)
    assert "state" not in widget["meta"] and widget["meta"]["connections"] == 2
    assert [row["label"] for row in widget["data"]] == ["nicht erreichbar", "nicht eingerichtet"]

@pytest.mark.asyncio
async def test_full_overview_merges_metrics_and_status_page_without_leaking_the_key(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())

    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    connection = _only(body)
    assert (connection["name"], connection["url"], connection["state"], connection["message"], connection["status_page"]) == (
        "kuma", BASE, "ok", None, True,
    )
    assert connection["summary"] == body["summary"] == {"total": 6, "up": 3, "down": 1, "pending": 1, "maintenance": 1}

    # Ausgefallenes zuerst, dann nach Name.
    assert [(m["name"], m["status"]) for m in connection["monitors"]] == [
        ("Router", "down"), ("NAS", "pending"), ('Backup "Nacht" \\ Job', "maintenance"),
        ("Intern", "up"), ("Nextcloud", "up"), ("Wiki", "up"),
    ]
    by_name = {m["name"]: m for m in connection["monitors"]}
    assert by_name["Nextcloud"] == {
        "name": "Nextcloud", "type": "http", "url": "https://cloud.example.com/", "status": "up", "response_ms": 123,
        "uptime_24h": 100.0, "cert_days_left": 62, "cert_valid": True,
    }
    assert by_name["Router"]["uptime_24h"] == 94.12 and by_name["Router"]["response_ms"] is None
    assert by_name["Wiki"]["uptime_24h"] == 99.93 and by_name["Wiki"]["cert_days_left"] == 9
    assert by_name["Intern"]["uptime_24h"] is None, "nicht auf der Status-Seite"
    assert by_name["Intern"]["url"] == "https://intern.example.com:8443/status"
    assert connection["outages"] == [
        {"monitor": "Wiki", "time": "2026-10-12T08:01:00Z", "message": "Keyword nicht gefunden"},
        {"monitor": "Router", "time": "2026-10-12T07:59:00Z", "message": "Request timeout"},
    ]

    assert fake.paths() == ["/metrics", "/api/status-page/heim", "/api/status-page/heartbeat/heim"]
    metrics_request = fake.requests[0]
    assert metrics_request.method == "GET"
    assert metrics_request.headers["Authorization"] == _basic()
    assert base64.b64decode(metrics_request.headers["Authorization"].split()[1]).decode() == f":{KEY}", "Benutzername leer"
    assert "Authorization" not in fake.requests[1].headers, "die Status-Seite braucht keine Anmeldung"
    assert all(KEY not in str(r.url) for r in fake.requests)

    widget = await _widget(client, headers)
    assert widget["data"] == [{"title": "Router", "subtitle": "kuma · 198.51.100.1", "label": "ausgefallen", "tone": "danger"}]
    assert widget["meta"] == {"connections": 1, "total": 6, "down": 1}

    settings = (await client.get(f"/api/v1/extensions/{EXT}/settings", headers=headers)).json()
    assert {s["label"]: s["is_set"] for s in settings["secrets"]} == {"uptime-kuma-key:kuma": True}
    audit = (await db_session.execute(select(AuditEntry))).scalars().all()
    everything = res.text + json.dumps(widget) + json.dumps(settings) + caplog.text + json.dumps([a.detail or {} for a in audit], default=str)
    assert KEY not in everything


@pytest.mark.asyncio
async def test_key_only_has_no_availability_and_no_outages(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(slug=""))
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["status_page"] is False
    assert connection["outages"] == []
    assert all(m["uptime_24h"] is None for m in connection["monitors"])
    assert len(connection["monitors"]) == 6
    assert fake.paths() == ["/metrics"]


@pytest.mark.asyncio
async def test_status_page_only_takes_the_state_from_the_last_heartbeat(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), key=None)
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["status_page"] is True
    assert [(m["name"], m["status"], m["response_ms"], m["uptime_24h"], m["url"]) for m in connection["monitors"]] == [
        ("Router", "down", None, 94.12, None), ("NAS", "pending", None, 50.0, None),
        ("Nextcloud", "up", 118, 100.0, None), ("Wiki", "up", 44, 99.93, None),
    ]
    assert connection["summary"] == {"total": 4, "up": 2, "down": 1, "pending": 1, "maintenance": 0}
    assert len(connection["outages"]) == 2
    assert "/metrics" not in fake.paths(), "ohne Schlüssel keine Anfrage an /metrics"
    assert all("Authorization" not in r.headers for r in fake.requests)


@pytest.mark.asyncio
async def test_widget_when_everything_runs_and_for_unusual_states(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(slug=""), _connection("zwei", slug=""))
    fake.metrics_text = _metric_lines([("A", 1), ("B", 1), ("C", 1)])
    rows = (await _widget(client, headers))["data"]
    assert rows == [
        {"title": "Alle 3 Monitore laufen", "subtitle": "kuma", "label": "in Ordnung", "tone": "good"},
        {"title": "Alle 3 Monitore laufen", "subtitle": "zwei", "label": "in Ordnung", "tone": "good"},
    ]

    fake.metrics_text = _metric_lines([("Solo", 1)])
    get_extension_runtime().loaded[EXT].instance._service.clear()
    assert (await _widget(client, headers))["data"][0]["title"] == "Der Monitor läuft"

    fake.metrics_text = _metric_lines([("A", 1), ("B", 2), ("C", 3), ("D", 1)])
    get_extension_runtime().loaded[EXT].instance._service.clear()
    assert (await _widget(client, headers))["data"][0] == {
        "title": "2 von 4 Monitoren laufen", "subtitle": "kuma: 1 ausstehend, 1 in Wartung", "label": "2/4", "tone": "warn",
    }

    fake.metrics_text = _metric_lines([("A", 1), ("B", 3)])
    get_extension_runtime().loaded[EXT].instance._service.clear()
    assert (await _widget(client, headers))["data"][0]["tone"] == "neutral", "Wartung allein ist kein Grund zur Sorge"

    fake.metrics_text = "# TYPE process_cpu_user_seconds_total counter\nprocess_cpu_user_seconds_total 1\n"
    get_extension_runtime().loaded[EXT].instance._service.clear()
    body = await _overview(client, headers)
    assert _only({"connections": body["connections"][:1]})["summary"] == {"total": 0, "up": 0, "down": 0, "pending": 0, "maintenance": 0}
    assert (await _widget(client, headers))["data"][0]["title"] == "Keine Monitore"


@pytest.mark.asyncio
async def test_many_down_monitors_are_capped_in_the_widget(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(slug=""))
    fake.metrics_text = _metric_lines([(f"M{i:02d}", 0) for i in range(35)])
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == 31
    assert rows[-1]["title"] == "… und 5 weitere ausgefallen"
    assert {r["tone"] for r in rows} == {"danger"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_key_keeps_the_status_page_data_and_says_what_to_do(client, db_session, test_settings, status):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/metrics"] = lambda request: httpx.Response(status, text="no")

    connection = _only(await _overview(client, headers))
    assert connection["state"] == "auth_failed"
    assert connection["message"].startswith("Uptime Kuma „kuma“ hat den API-Schlüssel nicht akzeptiert.")
    assert "Einstellungen → API-Schlüssel" in connection["message"]
    assert [m["name"] for m in connection["monitors"]] == ["Router", "NAS", "Nextcloud", "Wiki"], "Stand laut Status-Seite"
    assert connection["summary"]["down"] == 1
    rows = (await _widget(client, headers))["data"]
    assert rows[0]["title"] == "Router" and rows[0]["tone"] == "danger"
    assert rows[1] == {
        "title": "kuma: Anmeldung fehlgeschlagen", "subtitle": connection["message"], "label": "Anmeldung fehlgeschlagen", "tone": "danger",
    }
    loaded = get_extension_runtime().loaded[EXT]
    assert (await loaded.instance.health(loaded.ctx)).healthy is False


@pytest.mark.asyncio
async def test_wrong_key_is_rejected_by_the_server_and_shown_as_auth_failed(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(slug=""), key="falscher-schluessel-0000")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "auth_failed"
    assert fake.requests[0].headers["Authorization"] == _basic("falscher-schluessel-0000")
    assert "falscher-schluessel-0000" not in json.dumps(connection)
    assert connection["monitors"] == [] and connection["summary"] is None


async def _set_key(client, headers, value: str, name: str = "kuma") -> None:
    res = await client.put(
        f"/api/v1/extensions/{EXT}/secrets",
        json={"current_password": "correct-horse-battery", "label": f"uptime-kuma-key:{name}", "value": value}, headers=headers,
    )
    assert res.status_code == 204, res.text


@pytest.mark.asyncio
async def test_a_new_key_in_the_vault_is_used_at_once(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(slug=""), key="falscher-schluessel-0000")
    assert _only(await _overview(client, headers))["state"] == "auth_failed"
    await _set_key(client, headers, KEY)
    assert _only(await _overview(client, headers))["state"] == "ok", "ohne „Aktualisieren“ und vor Ablauf der 20 Sekunden"
    assert fake.requests[-1].headers["Authorization"] == _basic()


@pytest.mark.asyncio
async def test_a_changed_secret_touches_only_its_own_connection(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), _connection(name="kuma2"))
    await _overview(client, headers)
    loaded = get_extension_runtime().loaded[EXT]
    service = loaded.instance._service
    assert set(service._cache) == {"kuma", "kuma2"}
    await loaded.instance.on_secret_changed(loaded.ctx, "uptime-kuma-key:kuma2")
    assert set(service._cache) == {"kuma"}
    await loaded.instance.on_secret_changed(loaded.ctx, "uptime-kuma-sonstwas")
    assert set(service._cache) == {"kuma"}


@pytest.mark.asyncio
async def test_an_answer_with_the_old_key_is_not_kept(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    loaded = get_extension_runtime().loaded[EXT]
    service = loaded.instance._service
    load, entered, release, loads = service._load, asyncio.Event(), asyncio.Event(), []

    async def slow(cfg, has_key):
        loads.append(cfg.name)
        if len(loads) == 1:
            entered.set()
            await release.wait()
        return await load(cfg, has_key)

    service._load = slow
    first = asyncio.create_task(_overview(client, headers))
    await entered.wait()
    await loaded.instance.on_secret_changed(loaded.ctx, "uptime-kuma-key:kuma")
    release.set()
    await first
    await _overview(client, headers)
    assert loads == ["kuma", "kuma"], "was die Abfrage mit dem alten Schlüssel einträgt, gilt nicht mehr"


@pytest.mark.asyncio
async def test_metrics_404_says_there_is_no_kuma_or_no_metrics(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(slug=""))
    fake.overrides["/metrics"] = lambda request: httpx.Response(404, text="Not Found")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error"
    assert connection["message"] == "Unter dieser Adresse antwortet kein Uptime Kuma oder /metrics ist abgeschaltet."
    widget = (await _widget(client, headers))["data"]
    assert widget[0]["label"] == "Fehler" and widget[0]["tone"] == "warn"


@pytest.mark.asyncio
async def test_unknown_status_page_is_an_error_but_the_metrics_still_show(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(slug="gibts-nicht"))
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error"
    assert connection["message"].startswith("Die Status-Seite „gibts-nicht“ gibt es nicht.")
    assert len(connection["monitors"]) == 6, "die Messwerte aus /metrics bleiben"
    assert connection["outages"] == []
    assert fake.paths() == ["/metrics", "/api/status-page/gibts-nicht"]


@pytest.mark.asyncio
async def test_status_page_with_broken_answers(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), key=None)
    service = get_extension_runtime().loaded[EXT].instance._service

    fake.overrides["/api/status-page/heim"] = lambda request: httpx.Response(200, text="<html>kein JSON</html>")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and "kein gültiges JSON" in connection["message"]

    service.clear()
    fake.overrides["/api/status-page/heim"] = lambda request: httpx.Response(200, json={"irgendwas": 1})
    assert "nicht das erwartete Format" in _only(await _overview(client, headers))["message"]

    service.clear()
    del fake.overrides["/api/status-page/heim"]
    fake.overrides["/api/status-page/heartbeat/heim"] = lambda request: httpx.Response(200, json=[1, 2])
    assert "Verlauf der Status-Seite" in _only(await _overview(client, headers))["message"]

    service.clear()
    fake.overrides["/api/status-page/heartbeat/heim"] = lambda request: httpx.Response(500, text="kaputt")
    assert "meldet einen Fehler (HTTP 500)" in _only(await _overview(client, headers))["message"]

    service.clear()
    fake.overrides["/api/status-page/heartbeat/heim"] = lambda request: httpx.Response(403, text="nein")
    assert "verlangt eine Anmeldung" in _only(await _overview(client, headers))["message"]

    service.clear()
    fake.overrides["/api/status-page/heartbeat/heim"] = lambda request: httpx.Response(200, content=b"[" * 100000)
    assert _only(await _overview(client, headers))["state"] == "error", "absichtlich tief verschachteltes JSON"


@pytest.mark.asyncio
async def test_metrics_answers_that_are_not_metrics(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(slug=""))
    service = get_extension_runtime().loaded[EXT].instance._service

    fake.overrides["/metrics"] = lambda request: httpx.Response(200, text="<html><body>Anmelden</body></html>")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and "liefert unter /metrics keine Messwerte" in connection["message"]

    service.clear()
    fake.overrides["/metrics"] = lambda request: httpx.Response(502, text="Bad Gateway")
    assert "meldet einen Fehler (HTTP 502)" in _only(await _overview(client, headers))["message"]

    service.clear()
    fake.overrides["/metrics"] = lambda request: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and "ungewöhnlich große Antwort" in connection["message"]
    assert connection["monitors"] == []


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
    fake.overrides["/metrics"] = lambda request: httpx.Response(302, headers={"Location": location})
    fake.overrides["/api/status-page/heim"] = lambda request: httpx.Response(302, headers={"Location": location})

    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200, res.text
    connection = _only(res.json())
    assert connection["state"] == "error"
    assert connection["message"].startswith(f"Uptime Kuma „kuma“ {sentence}")
    assert "Trage in den Einstellungen die endgültige Adresse ein." in connection["message"]
    widget = await client.get(f"{API}/widgets/status", headers=headers)
    assert widget.status_code == 200 and "leitet auf eine andere Adresse um" in widget.text
    assert "angreifer" not in (res.text + widget.text + caplog.text).lower()
    assert "Traceback" not in caplog.text
    assert all(r.url.host == "kuma.test" for r in fake.requests), "der Weiterleitung wird nicht gefolgt"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (httpx.ReadTimeout("timed out"), "antwortet nicht (Zeitüberschreitung)"),
        (httpx.ConnectTimeout("timed out"), "antwortet nicht (Zeitüberschreitung)"),
        (httpx.ConnectError("[Errno 111] Connection refused"), "Connection refused"),
        (httpx.ConnectError("[Errno -2] Name or service not known"), "Name or service not known"),
    ],
)
async def test_unreachable_server_is_a_clean_state_and_the_second_request_is_skipped(client, db_session, test_settings, error, fragment):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.error = error
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "unreachable"
    assert fragment in connection["message"] and connection["message"].startswith("Uptime Kuma „kuma“")
    assert connection["monitors"] == [] and connection["summary"] is None
    assert fake.paths() == ["/metrics"], "ist der Server nicht erreichbar, hilft die Status-Seite nicht"

    rows = (await _widget(client, headers))["data"]
    assert rows == [{"title": "kuma: nicht erreichbar", "subtitle": connection["message"], "label": "nicht erreichbar", "tone": "danger"}]
    loaded = get_extension_runtime().loaded[EXT]
    health = await loaded.instance.health(loaded.ctx)
    assert health.healthy is False and health.message == f"kuma: {connection['message']}"


@pytest.mark.asyncio
async def test_a_text_that_quotes_the_key_never_reaches_the_user(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(slug=""))
    fake.error = httpx.ConnectError(f"boom {KEY} boom")
    res = await client.get(f"{API}/overview", headers=headers)
    assert _only(res.json())["state"] == "unreachable"
    assert KEY not in res.text and KEY not in caplog.text


@pytest.mark.asyncio
async def test_tls_exception_is_used_only_when_the_connection_asks_for_it(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("streng", slug=""), _connection("lax", slug="", insecure=True, url="https://lax.test"))
    body = await _overview(client, headers)
    assert [c["state"] for c in body["connections"]] == ["ok", "ok"]
    assert {r.url.host for r in fake.insecure_requests} == {"lax.test"}
    assert {r.url.host for r in fake.requests} == {"kuma.test", "lax.test"}
    normal_hosts = [r.url.host for r in fake.requests if r not in fake.insecure_requests]
    assert normal_hosts == ["kuma.test"]


@pytest.mark.asyncio
async def test_results_are_cached_for_a_short_time_and_the_key_is_read_only_when_needed(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    loaded = get_extension_runtime().loaded[EXT]
    service = loaded.instance._service
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
    assert len(fake.requests) == 3, "Seite und Kachel fragen Uptime Kuma nur einmal"
    assert reads == ["uptime-kuma-key:kuma"]

    await _overview(client, headers, "?refresh=true")
    assert len(fake.requests) == 6, "der Knopf Aktualisieren umgeht den Zwischenspeicher"

    await _overview(client, headers)
    assert len(fake.requests) == 6
    now[0] += 19.0
    await _overview(client, headers)
    assert len(fake.requests) == 6
    now[0] += 2.0
    await _overview(client, headers)
    assert len(fake.requests) == 9, "nach 20 Sekunden wird neu gefragt"

    await _overview(client, headers)
    saved = await client.put(
        f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": [_connection(slug="")]}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert not saved.json().get("secrets_cleared"), "dieselbe Adresse: der Schlüssel bleibt"
    await _overview(client, headers)
    assert len(fake.requests) == 10, "nach geaenderten Einstellungen wird sofort neu gefragt (nur /metrics, keine Status-Seite mehr)"


@pytest.mark.asyncio
async def test_changing_the_address_removes_the_key_so_it_never_goes_to_a_new_target(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(slug=""))
    assert _only(await _overview(client, headers))["state"] == "ok"
    saved = await client.put(
        f"/api/v1/extensions/{EXT}/settings",
        json={"current_password": "correct-horse-battery", "values": {"connections": [_connection(slug="", url="http://angreifer.test:3001")]}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["secrets_cleared"] == ["uptime-kuma-key:kuma"]
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "not_configured"
    assert all(r.url.host != "angreifer.test" for r in fake.requests)


@pytest.mark.asyncio
async def test_two_connections_are_independent(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("eins", slug=""), _connection("zwei", slug="", url="http://zwei.test:3001"))
    fake.overrides["/metrics"] = lambda request: (
        httpx.Response(200, text=_metric_lines([("A", 1)])) if request.url.host == "kuma.test" else httpx.Response(404)
    )
    body = await _overview(client, headers)
    assert [(c["name"], c["state"]) for c in body["connections"]] == [("eins", "ok"), ("zwei", "error")]
    assert body["summary"] == {"total": 1, "up": 1, "down": 0, "pending": 0, "maintenance": 0}
    rows = (await _widget(client, headers))["data"]
    assert [r["title"] for r in rows] == ["Der Monitor läuft", "zwei: Fehler"]
    loaded = get_extension_runtime().loaded[EXT]
    health = await loaded.instance.health(loaded.ctx)
    assert health.healthy is False
    assert health.details == {"eins": {"state": "ok", "message": None}, "zwei": {"state": "error", "message": body["connections"][1]["message"]}}
    assert health.message.startswith("eins: in Ordnung; zwei: Unter dieser Adresse")


@pytest.mark.asyncio
async def test_health_is_fine_when_every_connection_answers(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    loaded = get_extension_runtime().loaded[EXT]
    health = await loaded.instance.health(loaded.ctx)
    assert health.healthy is True and health.message == "kuma: in Ordnung"


@pytest.mark.asyncio
async def test_health_asks_again_instead_of_using_the_cache(client, db_session, test_settings):
    """„Verbindung testen“ zeigt den Stand von jetzt: fällt Kuma nach dem letzten Abruf aus, meldet der Test das."""
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    assert _only(await _overview(client, headers))["state"] == "ok"
    asked = len(fake.requests)
    fake.error = httpx.ConnectError("Verbindung abgelehnt")
    loaded = get_extension_runtime().loaded[EXT]
    health = await loaded.instance.health(loaded.ctx)
    assert len(fake.requests) > asked, "der Test fragt Uptime Kuma neu"
    assert health.healthy is False


@pytest.mark.asyncio
async def test_unusable_entries_become_error_cards_without_a_request(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.ctx.settings.set({"connections": [
        {"name": "ok", "base_url": BASE, "status_page_slug": "heim"},
        {"name": "ok", "base_url": BASE},
        {"name": "pw", "base_url": "http://nutzer:" + "passwort@kuma.test:3001"},
        {"name": "gross", "base_url": BASE, "status_page_slug": "Heim"},
    ]})
    body = await _overview(client, headers)
    states = [(c["name"], c["state"]) for c in body["connections"]]
    assert states == [("ok", "ok"), ("ok", "error"), ("pw", "error"), ("gross", "error")]
    assert "mehrfach vergeben" in body["connections"][1]["message"]
    assert "passwort" not in json.dumps(body)
    assert body["connections"][2]["url"] is None
    assert {r.url.host for r in fake.requests} == {"kuma.test"} and len(fake.requests) == 2, "nur der brauchbare Eintrag fragt an (nur Status-Seite)"


@pytest.mark.asyncio
async def test_unexpected_errors_do_not_become_a_500(client, db_session, test_settings, caplog):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    service = get_extension_runtime().loaded[EXT].instance._service

    async def broken(cfg, has_key):
        raise RuntimeError("innerer Fehler mit Details")

    service._load = broken
    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200, res.text
    connection = _only(res.json())
    assert connection["state"] == "error" and connection["message"] == "Bei der Abfrage von kuma ist ein unerwarteter Fehler aufgetreten."
    assert "Details" not in res.text
    assert "innerer Fehler" in caplog.text, "im Protokoll steht die Ursache"


@pytest.mark.asyncio
async def test_routes_reject_anonymous_and_let_viewers_read(client, db_session, test_settings):
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
    # Nur lesend: es gibt nichts, was man aufrufen oder vorschlagen könnte.
    for method in ("post", "put", "delete", "patch"):
        res = await getattr(client, method)(f"{API}/overview", headers=headers)
        assert res.status_code in (404, 405), (method, res.status_code)


def test_openapi_describes_the_overview_with_typed_fields():
    from nodvard_deck_ext_uptime_kuma.models import Overview

    schema = Overview.model_json_schema()
    names = set(schema["$defs"])
    assert {"Connection", "Monitor", "Outage", "Summary"} <= names
    assert set(schema["$defs"]["Monitor"]["properties"]) == {
        "name", "type", "url", "status", "response_ms", "uptime_24h", "cert_days_left", "cert_valid",
    }
    assert set(schema["$defs"]["Connection"]["properties"]) == {
        "name", "url", "state", "message", "monitors", "summary", "outages", "status_page",
    }
    assert set(schema["$defs"]["Summary"]["properties"]) == {"total", "up", "down", "pending", "maintenance"}
    assert set(schema["$defs"]["Outage"]["properties"]) == {"monitor", "time", "message"}
