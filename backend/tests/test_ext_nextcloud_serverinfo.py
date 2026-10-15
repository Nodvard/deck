"""Tests für die Nextcloud-Kachel mit den Kennzahlen aus „Server-Info“; die Nextcloud ist ein `httpx.MockTransport`."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from nodvard_sdk.errors import PermissionDenied
from sqlalchemy import select

from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import AuditEntry
from nodvard_deck.services import extensions as extensions_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "nextcloud" / "src"))

from nodvard_deck_ext_nextcloud import serverinfo, widgets  # noqa: E402
from nodvard_deck_ext_nextcloud.config import (  # noqa: E402
    normalize_base_url,
    read_serverinfo_config,
)
from nodvard_deck_ext_nextcloud.models import ServerInfo  # noqa: E402
from nodvard_deck_ext_nextcloud.service import (  # noqa: E402
    AUTH_FAILED_TTL_S,
    CACHE_TTL_S,
    empty,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nextcloud"
BASE = "https://cloud.example.com"
USER = "alice"
PASSWORD = "app-passwort-4f9c1d7a"
TOKEN = "serverinfo-token-b81e52c0"
EXT = "nextcloud"
API = "/api/v1/ext/nextcloud"
INFO_URL = f"{BASE}/ocs/v2.php/apps/serverinfo/api/v1/info?format=json&skipApps=false&skipUpdate=false"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _data(name: str = "serverinfo_current.json") -> dict[str, Any]:
    return _fixture(name)["ocs"]["data"]


def _basic(user: str = USER, password: str = PASSWORD) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def _info(mutate: Callable[[dict[str, Any]], None] | None = None, name: str = "serverinfo_current.json") -> ServerInfo:
    data = copy.deepcopy(_data(name))
    if mutate is not None:
        mutate(data)
    return ServerInfo(state="ok", message=None, auth="token", **serverinfo.parse_info(data))


def _system(data: dict[str, Any]) -> dict[str, Any]:
    return data["nextcloud"]["system"]


def test_current_version_is_read_with_the_units_of_the_app():
    parsed = serverinfo.parse_info(_data())
    assert parsed["version"] == "31.0.7.1"
    assert parsed["updates"] == {
        "core_available": True,
        "core_version": "31.0.9.1",
        "core_checked_at": datetime(2026, 10, 12, 8, 0, tzinfo=UTC),
        "app_updates": 2,
    }
    assert parsed["storage"] == {"free_bytes": 48472944640, "files": 708860, "users": 7}
    assert parsed["active_users"] == {"last_5_minutes": 2, "last_hour": 4, "last_24_hours": 5}
    assert parsed["load"] == {
        "load_1": 0.85,
        "load_5": 1.04,
        "load_15": 1.17,
        "cpu_cores": 4,
        # mem_total/mem_free kommen in KiB, die Antwort ist in Bytes.
        "memory_total_bytes": 8183664 * 1024,
        "memory_available_bytes": 5877156 * 1024,
    }


def test_old_version_without_update_and_cpu_count_gives_unknown_values():
    parsed = serverinfo.parse_info(_data("serverinfo_old.json"))
    assert parsed["version"] == "26.0.13.1"
    assert parsed["updates"] == {"core_available": None, "core_version": None, "core_checked_at": None, "app_updates": 0}
    assert parsed["load"]["cpu_cores"] is None
    assert parsed["load"]["load_5"] == 0.31
    assert parsed["storage"] == {"free_bytes": 21474836480, "files": 1450, "users": 2}
    assert parsed["active_users"] == {"last_5_minutes": 0, "last_hour": 1, "last_24_hours": 2}


@pytest.mark.parametrize(
    ("mutate", "section", "expected"),
    [
        # freespace: Bytes oder null; negative Werte und Nicht-Zahlen sind unbekannt
        (lambda s: s.update(freespace=None), ("storage", "free_bytes"), None),
        (lambda s: s.update(freespace=-1), ("storage", "free_bytes"), None),
        (lambda s: s.update(freespace="5"), ("storage", "free_bytes"), None),
        (lambda s: s.update(freespace=True), ("storage", "free_bytes"), None),
        (lambda s: s.update(freespace=2048.0), ("storage", "free_bytes"), 2048),
        (lambda s: s.update(freespace=1.5), ("storage", "free_bytes"), None),
        (lambda s: s.update(freespace=10**30), ("storage", "free_bytes"), None),
        # cpuload: drei Zahlen, sonst "N/A" (bis Zweig 31) oder false (ab 32)
        (lambda s: s.update(cpuload="N/A"), ("load", "load_5"), None),
        (lambda s: s.update(cpuload=False), ("load", "load_5"), None),
        (lambda s: s.update(cpuload=[1, 2]), ("load", "load_5"), None),
        (lambda s: s.update(cpuload=[1, "x", 3]), ("load", "load_5"), None),
        (lambda s: s.update(cpuload=[-1.0, 0, 0]), ("load", "load_5"), None),
        (lambda s: s.update(cpuload={"a": 1}), ("load", "load_5"), None),
        (lambda s: s.update(cpuload=[1, 2.5, 3, 4]), ("load", "load_5"), 2.5),
        (lambda s: s.update(cpuload=[1, float("nan"), 3]), ("load", "load_5"), None),
        # cpunum: -1 heißt unbekannt
        (lambda s: s.update(cpunum=-1), ("load", "cpu_cores"), None),
        (lambda s: s.update(cpunum=0), ("load", "cpu_cores"), None),
        (lambda s: s.update(cpunum="4"), ("load", "cpu_cores"), None),
        (lambda s: s.update(cpunum=8.0), ("load", "cpu_cores"), 8),
        # Arbeitsspeicher: -1 (neuer) und -1024 (älter) heißen nicht ermittelbar
        (lambda s: s.update(mem_total=-1), ("load", "memory_total_bytes"), None),
        (lambda s: s.update(mem_total=-1024), ("load", "memory_total_bytes"), None),
        (lambda s: s.update(mem_total=0), ("load", "memory_total_bytes"), None),
        (lambda s: s.update(mem_total="x"), ("load", "memory_total_bytes"), None),
        (lambda s: s.update(mem_total=-1), ("load", "memory_available_bytes"), None),
        (lambda s: s.update(mem_free=-1024), ("load", "memory_available_bytes"), None),
        (lambda s: s.update(mem_free=-1024), ("load", "memory_total_bytes"), 8183664 * 1024),
        (lambda s: s.update(mem_free=9_000_000), ("load", "memory_available_bytes"), None),
        # Version: nur ein kurzer Text aus harmlosen Zeichen
        (lambda s: s.update(version="<script>alert(1)</script>"), ("version",), None),
        (lambda s: s.update(version=31), ("version",), None),
        (lambda s: s.update(version=""), ("version",), None),
        (lambda s: s.update(version="1" * 80), ("version",), None),
        (lambda s: s.update(version=" 31.0.7.1 "), ("version",), "31.0.7.1"),
        # update: gibt es erst ab Zweig 28
        (lambda s: s.pop("update"), ("updates", "core_available"), None),
        (lambda s: s.update(update="x"), ("updates", "core_available"), None),
        (lambda s: s["update"].update(available="yes"), ("updates", "core_available"), None),
        (lambda s: s["update"].update(available=1), ("updates", "core_available"), None),
        (lambda s: s["update"].update(available=False), ("updates", "core_available"), False),
        (lambda s: s["update"].update(available=False), ("updates", "core_version"), None),
        (lambda s: s["update"].pop("available_version"), ("updates", "core_version"), None),
        (lambda s: s["update"].update(available_version="<b>9</b>"), ("updates", "core_version"), None),
        (lambda s: s["update"].update(lastupdatedat=0), ("updates", "core_checked_at"), None),
        (lambda s: s["update"].update(lastupdatedat="gestern"), ("updates", "core_checked_at"), None),
        (lambda s: s["update"].update(lastupdatedat=10**15), ("updates", "core_checked_at"), None),
        # apps: nur mit skipApps=false
        (lambda s: s.pop("apps"), ("updates", "app_updates"), None),
        (lambda s: s["apps"].update(num_updates_available=-1), ("updates", "app_updates"), 2),  # Rückfall: die Liste
        (lambda s: s["apps"].update(num_updates_available=-1, app_updates="x"), ("updates", "app_updates"), None),
        (lambda s: s["apps"].pop("num_updates_available"), ("updates", "app_updates"), 2),
        (lambda s: s["apps"].update(app_updates=[]) or s["apps"].pop("num_updates_available"), ("updates", "app_updates"), None),
    ],
)
def test_broken_or_missing_system_values_become_none_and_never_raise(mutate, section, expected):
    parsed = serverinfo.parse_info(_mutated(lambda data: mutate(_system(data))))
    value: Any = parsed
    for key in section:
        value = value[key]
    assert value == expected


def _mutated(mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    data = copy.deepcopy(_data())
    mutate(data)
    return data


@pytest.mark.parametrize(
    ("mutate", "key", "expected"),
    [
        (lambda d: d["nextcloud"]["storage"].update(num_files=0), "files", None),  # der Hintergrundjob hat noch nicht gezählt
        (lambda d: d["nextcloud"]["storage"].update(num_files="7"), "files", None),
        (lambda d: d["nextcloud"]["storage"].update(num_users=-1), "users", None),
        (lambda d: d["nextcloud"].update(storage=[]), "users", None),
        (lambda d: d["nextcloud"].pop("storage"), "files", None),
    ],
)
def test_storage_counts(mutate, key, expected):
    assert serverinfo.parse_info(_mutated(mutate))["storage"][key] == expected


@pytest.mark.parametrize("active", [[], "x", 5, None, {"last5minutes": "2", "last1hour": None, "last24hours": -3}])
def test_broken_active_users_become_none(active):
    parsed = serverinfo.parse_info(_mutated(lambda d: d.update(activeUsers=active)))
    assert parsed["active_users"] == {"last_5_minutes": None, "last_hour": None, "last_24_hours": None}


@pytest.mark.parametrize(
    "data",
    [{}, {"nextcloud": []}, {"nextcloud": {"system": 5, "storage": "x"}, "activeUsers": 3}, {"nextcloud": {"system": {"update": [], "apps": 4}}}],
)
def test_garbage_gives_nothing_but_no_error(data):
    parsed = serverinfo.parse_info(data)
    info = ServerInfo(state="ok", message=None, auth=None, **parsed)
    assert info.version is None and info.storage.free_bytes is None and info.load.cpu_cores is None
    assert widgets.status_rows(info)[0]["tone"] == "neutral", "ohne Angaben bleibt es bei neutralen Zeilen"


def test_base_url_is_normalized_and_credentials_are_refused():
    assert normalize_base_url(" https://cloud.example.com/ ") == "https://cloud.example.com"
    assert normalize_base_url("http://cloud.example.com:8080/nextcloud/?x=1#y") == "http://cloud.example.com:8080/nextcloud"
    for bad in ("cloud.example.com", "ftp://cloud.example.com", "https://user:pw@cloud.example.com", "https://cloud.example.com:99999", "https://", ""):
        with pytest.raises(ValueError):
            normalize_base_url(bad)
    cfg = read_serverinfo_config({"base_url": "https://u:geheim@cloud.example.com", "username": "alice"})
    assert cfg.base_url is None and "ungültig" in (cfg.problem or "") and "geheim" not in (cfg.problem or "")
    assert read_serverinfo_config({}) == read_serverinfo_config({"base_url": "  ", "username": ""})
    assert read_serverinfo_config({"base_url": BASE, "username": "alice", "tls_insecure_skip_verify": "ja"}).insecure_tls is False


class _Http:
    def __init__(self, handler: Callable[[httpx.Request], Any]) -> None:
        self.requests: list[httpx.Request] = []

        def recording(request: httpx.Request) -> Any:
            self.requests.append(request)
            return handler(request)

        self._client = httpx.AsyncClient(transport=httpx.MockTransport(recording))
        self.insecure: list[bool] = []
        self.timeouts: list[Any] = []

    def stream(self, method: str, url: str, *, insecure_tls: bool = False, **kwargs: Any):
        self.insecure.append(insecure_tls)
        self.timeouts.append(kwargs.get("timeout"))
        return self._client.stream(method, url, **kwargs)


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=_fixture("serverinfo_current.json"))


@pytest.mark.asyncio
async def test_token_is_sent_as_nc_token_header_and_never_as_basic_login():
    http = _Http(_ok)
    data = await serverinfo.fetch(http, f"{BASE}/", token=TOKEN, auth=(USER, PASSWORD), insecure_tls=True)
    assert data["nextcloud"]["system"]["version"] == "31.0.7.1"
    (request,) = http.requests
    assert request.method == "GET" and str(request.url) == INFO_URL
    assert request.headers["NC-Token"] == TOKEN
    assert "Authorization" not in request.headers, "mit Token keine Basic-Anmeldung"
    assert request.headers["OCS-APIRequest"] == "true" and request.headers["Accept"] == "application/json"
    assert http.insecure == [True]


@pytest.mark.asyncio
async def test_password_is_sent_as_basic_login_and_without_token_header():
    http = _Http(_ok)
    await serverinfo.fetch(http, f"{BASE}/nextcloud", auth=(USER, PASSWORD))
    (request,) = http.requests
    assert request.url.path == "/nextcloud/ocs/v2.php/apps/serverinfo/api/v1/info"
    assert request.headers["Authorization"] == _basic()
    assert "NC-Token" not in request.headers
    assert http.insecure == [False]


@pytest.mark.asyncio
async def test_timeouts_allow_the_slow_answer_of_the_app():
    http = _Http(_ok)
    await serverinfo.fetch(http, BASE, token=TOKEN)
    timeout = http.timeouts[0]
    assert (timeout.connect, timeout.read) == (serverinfo.CONNECT_TIMEOUT_S, serverinfo.READ_TIMEOUT_S)
    assert serverinfo.READ_TIMEOUT_S >= 30 > serverinfo.CONNECT_TIMEOUT_S


async def _failure(handler: Callable[[httpx.Request], Any], **kwargs: Any) -> serverinfo.ServerInfoError:
    with pytest.raises(serverinfo.ServerInfoError) as caught:
        await serverinfo.fetch(_Http(handler), BASE, token=TOKEN, **kwargs)
    assert caught.value.__cause__ is None and caught.value.__context__ is None, "die Ursache haengt nicht an der Meldung"
    return caught.value


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_login_says_that_only_admins_get_the_numbers(status):
    error = await _failure(lambda r: httpx.Response(status, json=_fixture("serverinfo_unauthorized.json")))
    assert error.state == "auth_failed"
    assert str(error) == serverinfo.AUTH_TEXT
    assert "Admin-Konten" in str(error) and "Token für „Server-Info“" in str(error)


@pytest.mark.asyncio
async def test_app_switched_off_is_404_with_ocs_998_and_says_so():
    error = await _failure(lambda r: httpx.Response(404, json=_fixture("serverinfo_app_off.json")))
    assert error.state == "app_inactive"
    assert str(error).startswith("Die App „Server-Info“")
    assert "nicht aktiv" in str(error)
    assert "freedesktop" not in str(error) and "Invalid query" not in str(error), "kein Text des fremden Servers"
    # Dieselbe Antwort mit Status 200 (ein Proxy glättet den Statuscode): der OCS-Status zählt.
    error = await _failure(lambda r: httpx.Response(200, json=_fixture("serverinfo_app_off.json")))
    assert error.state == "app_inactive"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "state", "fragment"),
    [
        (429, "error", "bremst Anfragen"),
        (503, "error", "Wartungsmodus"),
        (500, "error", "(HTTP 500)"),
        (502, "error", "(HTTP 502)"),
        (400, "error", "abgelehnt (HTTP 400)"),
        (418, "error", "abgelehnt (HTTP 418)"),
    ],
)
async def test_other_status_codes_get_a_fixed_sentence_without_the_foreign_text(status, state, fragment):
    error = await _failure(lambda r: httpx.Response(status, text="Bitte überweise 5 Euro an Konto 123 <b>jetzt</b>"))
    assert error.state == state
    assert fragment in str(error)
    assert "überweise" not in str(error) and "<b>" not in str(error)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "state"), [(997, "auth_failed"), (401, "auth_failed"), (998, "app_inactive"), (404, "app_inactive"), (996, "error"), (999, "error"), (123, "error")]
)
async def test_ocs_status_in_an_answer_with_status_200(code, state):
    body = {"ocs": {"meta": {"status": "failure", "statuscode": code, "message": "Geheimer Fremdtext"}, "data": []}}
    error = await _failure(lambda r: httpx.Response(200, json=body))
    assert error.state == state
    assert "Geheimer Fremdtext" not in str(error)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [100, 200, None])
async def test_ocs_status_100_200_or_missing_is_accepted(code):
    body = copy.deepcopy(_fixture("serverinfo_current.json"))
    if code is None:
        del body["ocs"]["meta"]
    else:
        body["ocs"]["meta"]["statuscode"] = code
    data = await serverinfo.fetch(_Http(lambda r: httpx.Response(200, json=body)), BASE, token=TOKEN)
    assert data["nextcloud"]["system"]["version"] == "31.0.7.1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html><body>Bitte melde dich an</body></html>"),
        httpx.Response(200, content=b""),
        httpx.Response(200, content=b"\xff\xfe\x00 kaputt"),
        httpx.Response(200, content=b"[" * 100_000 + b"]" * 100_000),
        httpx.Response(200, json=[1, 2, 3]),
        httpx.Response(200, json={"ocs": "x"}),
        httpx.Response(200, json={"ocs": {"meta": {"statuscode": "200"}, "data": {"nextcloud": {}}}}),
        httpx.Response(200, json={"ocs": {"meta": {"statuscode": True}, "data": {"nextcloud": {}}}}),
        httpx.Response(200, json={"ocs": {"meta": {"statuscode": 200}, "data": []}}),
        httpx.Response(200, json={"ocs": {"meta": {"statuscode": 200}, "data": {"server": {}}}}),
        httpx.Response(200, json={"ocs": {"meta": {"statuscode": 200}, "data": {"nextcloud": "x"}}}),
    ],
)
async def test_an_answer_that_is_not_the_info_of_the_app_is_unexpected(response):
    error = await _failure(lambda r: response)
    assert error.state == "error"
    assert str(error).startswith("Unerwartete Antwort der Nextcloud:")
    assert "melde dich an" not in str(error)


@pytest.mark.asyncio
async def test_huge_answer_is_cut_off_early_and_called_unexpected():
    sent = 0

    async def body():
        nonlocal sent
        for _ in range(100):
            sent += 1
            yield b"x" * (1024 * 1024)

    error = await _failure(lambda r: httpx.Response(200, content=body()))
    assert str(error).startswith("Unerwartete Antwort der Nextcloud:")
    assert "mehr als 2 MB" in str(error)
    assert sent <= 4, "nach dem Limit wird nicht weitergelesen"


@pytest.mark.asyncio
async def test_giving_up_after_the_total_time_limit():
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    error = await _failure(slow, total_timeout_s=0.05)
    assert error.state == "unreachable"
    assert str(error) == "Nextcloud antwortet nicht (Zeitüberschreitung)."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        "http://angreifer.example/ocs",
        "/anderswo",
        # Ungültiger Punycode-Name: httpx wirft einen `ValueError`, dessen Text Teile des fremden Namens nennt.
        "http://xn--angreifer-ey9f.example/login",
    ],
)
@pytest.mark.parametrize("status", [301, 302, 307])
async def test_redirects_are_reported_never_followed_and_never_show_the_foreign_address(location, status):
    http = _Http(lambda r: httpx.Response(status, headers={"Location": location}))
    with pytest.raises(serverinfo.ServerInfoError) as caught:
        await serverinfo.fetch(http, BASE, token=TOKEN)
    text = str(caught.value)
    assert "leitet auf eine andere Adresse um" in text
    assert "Trage in den Einstellungen die endgültige Adresse ein." in text
    assert "angreifer" not in text.lower() and "anderswo" not in text
    assert len(http.requests) == 1, "der Weiterleitung wird nicht gefolgt"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "state", "fragment"),
    [
        (httpx.ReadTimeout("timed out"), "unreachable", "antwortet nicht (Zeitüberschreitung)"),
        (httpx.ConnectTimeout("timed out"), "unreachable", "antwortet nicht (Zeitüberschreitung)"),
        (httpx.ConnectError("[Errno 111] Connection refused"), "unreachable", "Nextcloud ist nicht erreichbar"),
        (httpx.ConnectError("[Errno -2] Name or service not known"), "unreachable", "Nextcloud ist nicht erreichbar"),
        (httpx.ReadError("Verbindung getrennt"), "unreachable", "Nextcloud ist nicht erreichbar"),
        (OSError("Network is unreachable"), "unreachable", "Nextcloud ist nicht erreichbar"),
        (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), "error", "Selbstsigniertes Zertifikat erlauben"),
        (httpx.RemoteProtocolError("illegal header line: bytearray(b'X-Geheim: abc')"), "error", "Verbindung abgebrochen oder eine fehlerhafte Antwort"),
        (httpx.LocalProtocolError("Illegal header value b'xyz'"), "error", "ließ sich nicht senden"),
        (httpx.InvalidURL("Invalid URL 'http://x y'"), "error", "ließ sich nicht senden"),
        (ValueError("Invalid IDNA hostname 'angreifer'"), "error", "ließ sich nicht senden"),
        (PermissionDenied(EXT, "net.outbound.insecure_tls"), "error", "nicht erlaubt"),
    ],
)
async def test_transport_errors_become_fixed_sentences(error, state, fragment):
    def boom(request: httpx.Request) -> httpx.Response:
        raise error

    result = await _failure(boom)
    assert result.state == state
    assert fragment in str(result)
    for raw in ("Connection refused", "Name or service", "X-Geheim", "xyz", "angreifer", "Verbindung getrennt", "Network is"):
        assert raw not in str(result), "der Text der Ausnahme wird nicht zitiert"


def test_tile_rows_of_a_current_nextcloud():
    assert widgets.status_rows(_info()) == [
        {"title": "Nextcloud 31.0.7.1", "subtitle": "Update auf Version 31.0.9.1 verfügbar", "label": "Update", "tone": "warn"},
        {"title": "Apps", "subtitle": "2 App-Updates verfügbar", "label": "2", "tone": "warn"},
        {"title": "Freier Speicher", "subtitle": "im Datenordner", "label": "45,1 GB", "tone": "good"},
        {"title": "Dateien und Konten", "subtitle": "708.860 Dateien · 7 Konten", "label": "7", "tone": "neutral"},
        {"title": "Aktive Nutzer", "subtitle": "2 in 5 Min. · 4 in 1 Std. · 5 in 24 Std.", "label": "2", "tone": "neutral"},
        {"title": "Prozessor-Last", "subtitle": "Last 0,85 / 1,04 / 1,17 (1 / 5 / 15 Min.) bei 4 Kernen", "label": "26 %", "tone": "good"},
        {"title": "Arbeitsspeicher", "subtitle": "2,2 GB von 7,8 GB belegt", "label": "28 %", "tone": "good"},
    ]


def test_tile_rows_of_an_old_nextcloud_say_what_they_do_not_know():
    rows = widgets.status_rows(_info(name="serverinfo_old.json"))
    assert rows[0] == {
        "title": "Nextcloud 26.0.13.1", "subtitle": "Ob ein Update bereitsteht, meldet diese Nextcloud nicht", "label": "–", "tone": "neutral",
    }
    assert rows[1] == {"title": "Apps", "subtitle": "Alle Apps sind aktuell", "label": "aktuell", "tone": "good"}
    assert rows[2]["label"] == "20,0 GB" and rows[2]["tone"] == "good"
    load = next(r for r in rows if r["title"] == "Prozessor-Last")
    assert load == {"title": "Prozessor-Last", "subtitle": "Last 0,20 / 0,31 / 0,40 (1 / 5 / 15 Min.)", "label": "0,31", "tone": "neutral"}


def test_tile_update_row():
    def with_update(**update: Any) -> list[dict[str, Any]]:
        return widgets.status_rows(_info(lambda d: _system(d).update(update=update)))

    assert with_update(available=False, lastupdatedat=1791792000)[0] == {
        "title": "Nextcloud 31.0.7.1", "subtitle": "Keine neue Version gemeldet", "label": "aktuell", "tone": "good",
    }
    assert with_update(available=False, lastupdatedat=0)[0]["tone"] == "neutral", "ohne Suche ist 'aktuell' nicht belegt"
    assert with_update(available=True, lastupdatedat=1)[0]["subtitle"] == "Update verfügbar"
    assert with_update(available=True, available_version="32.0.0.1", lastupdatedat=1)[0]["tone"] == "warn"


@pytest.mark.parametrize(
    ("mutate", "title", "label", "tone"),
    [
        (lambda s: s.update(cpuload=[0.1, 3.9, 3.9]), "Prozessor-Last", "98 %", "good"),
        (lambda s: s.update(cpuload=[0.1, 3.99, 3.9]), "Prozessor-Last", "100 %", "warn"),
        (lambda s: s.update(cpuload=[0.1, 4.0, 3.9]), "Prozessor-Last", "100 %", "warn"),
        (lambda s: s.update(cpuload=[9.0, 8.0, 6.0]), "Prozessor-Last", "200 %", "danger"),
        (lambda s: s.update(cpuload=[0.1, 0.4, 0.4]), "Prozessor-Last", "10 %", "good"),
        (lambda s: s.update(mem_free=800_000), "Arbeitsspeicher", "90 %", "warn"),
        (lambda s: s.update(mem_free=100_000), "Arbeitsspeicher", "99 %", "danger"),
        (lambda s: s.update(mem_free=7_000_000), "Arbeitsspeicher", "14 %", "good"),
        (lambda s: s.update(freespace=5 * 1024**3), "Freier Speicher", "5,0 GB", "warn"),
        (lambda s: s.update(freespace=1500 * 1024**2), "Freier Speicher", "1,5 GB", "danger"),
        (lambda s: s.update(freespace=2 * 1024**4), "Freier Speicher", "2,0 TB", "good"),
        (lambda s: s.update(freespace=300 * 1024**2), "Freier Speicher", "300 MB", "danger"),
    ],
)
def test_tile_tones(mutate, title, label, tone):
    rows = {r["title"]: r for r in widgets.status_rows(_info(lambda d: mutate(_system(d))))}
    assert (rows[title]["label"], rows[title]["tone"]) == (label, tone)


def test_tile_leaves_out_rows_without_numbers():
    def mutate(data: dict[str, Any]) -> None:
        system = _system(data)
        for key in ("freespace", "cpuload", "mem_total", "mem_free"):
            del system[key]
        del data["activeUsers"]
        data["nextcloud"]["storage"] = {}

    titles = [r["title"] for r in widgets.status_rows(_info(mutate))]
    assert titles == ["Nextcloud 31.0.7.1", "Apps"]


@pytest.mark.parametrize(
    ("value", "text"),
    [(0, "0 Bytes"), (1023, "1023 Bytes"), (1024, "1,0 KB"), (1536, "1,5 KB"), (123456789, "118 MB"), (5 * 1024**3, "5,0 GB"), (3 * 1024**5, "3072 TB")],
)
def test_sizes_and_numbers_are_written_the_german_way(value, text):
    assert widgets.size(value) == text
    assert widgets.number(1234567) == "1.234.567" and widgets.number(12) == "12"


def test_problem_rows():
    not_configured = empty("not_configured", "Hinweis")
    assert widgets.status_rows(not_configured) == [
        {"title": "Nextcloud nicht eingerichtet", "subtitle": "Hinweis", "label": "nicht eingerichtet", "tone": "neutral"}
    ]
    expected = {
        "auth_failed": ("keine Rechte", "warn"),
        "app_inactive": ("App aus", "warn"),
        "unreachable": ("nicht erreichbar", "danger"),
        "error": ("Fehler", "warn"),
        "etwas-neues": ("Fehler", "warn"),
    }
    for state, (label, tone) in expected.items():
        (row,) = widgets.status_rows(empty(state, f"Text {state}"))
        assert (row["label"], row["tone"], row["subtitle"]) == (label, tone, f"Text {state}")


class FakeNextcloud:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.insecure_requests: list[httpx.Request] = []
        self.body: Any = _fixture("serverinfo_current.json")
        self.override: Callable[[httpx.Request], httpx.Response] | None = None
        self.error: Exception | None = None
        self.token = TOKEN
        self.admin = (USER, PASSWORD)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.override is not None:
            return self.override(request)
        if request.url.path != "/ocs/v2.php/apps/serverinfo/api/v1/info":
            return httpx.Response(404, json=_fixture("serverinfo_app_off.json"))
        if request.headers.get("NC-Token") == self.token or request.headers.get("Authorization") == _basic(*self.admin):
            return httpx.Response(200, json=self.body)
        return httpx.Response(401, json=_fixture("serverinfo_unauthorized.json"))

    def insecure_handler(self, request: httpx.Request) -> httpx.Response:
        self.insecure_requests.append(request)
        return self.handler(request)


async def _login(client, username="owner1", password="correct-horse-battery") -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _enable(client, db_session, test_settings) -> tuple[dict, FakeNextcloud]:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    headers = await _login(client)
    enabled = await client.post(f"/api/v1/extensions/{EXT}/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text

    fake = FakeNextcloud()
    http = get_extension_runtime().loaded[EXT].ctx.http
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    http._insecure_client = httpx.AsyncClient(transport=httpx.MockTransport(fake.insecure_handler))
    return headers, fake


async def _configure(
    client, headers, *, password: str | None = PASSWORD, token: str | None = None, url: str = BASE, insecure: bool = False
) -> None:
    values: dict[str, Any] = {"base_url": url, "username": USER}
    if insecure:
        values["tls_insecure_skip_verify"] = True
    saved = await client.put(f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": values}, headers=headers)
    assert saved.status_code == 200, saved.text
    for label, value in (("nextcloud-app-password", password), ("nextcloud-serverinfo-token", token)):
        if value is not None:
            res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": label, "value": value}, headers=headers)
            assert res.status_code == 204, res.text


async def _serverinfo(client, headers, query: str = "") -> dict:
    res = await client.get(f"{API}/serverinfo{query}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def _widget(client, headers) -> dict:
    res = await client.get(f"{API}/widgets/status", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _service():
    return get_extension_runtime().loaded[EXT].instance._serverinfo


@pytest.mark.asyncio
async def test_extension_registers_the_tile_next_to_the_file_source(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    row = next(r for r in (await client.get("/api/v1/extensions", headers=headers)).json() if r["id"] == EXT)
    assert (row["name"], row["version"]) == ("Nextcloud", "0.1.0")
    assert "Server-Info" in row["description"]

    widgets_ = [w for w in (await client.get("/api/v1/widgets", headers=headers)).json() if w["ext_id"] == EXT]
    assert [w["id"] for w in widgets_] == ["status"]
    assert (widgets_[0]["title"], widgets_[0]["data_endpoint"]) == ("Nextcloud", "widgets/status")
    assert widgets_[0]["view"]["item"]["badge"] == {"text": "{{ label }}", "tone": "{{ tone }}"}
    assert widgets_[0]["permissions"] == ["hosts.read"]


@pytest.mark.asyncio
async def test_nothing_configured_is_reported_cleanly(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    body = await _serverinfo(client, headers)
    assert body["state"] == "not_configured" and body["auth"] is None
    assert "Einstellungen" in body["message"] and "App-Passwort" in body["message"]
    assert body["version"] is None and body["storage"] == {"free_bytes": None, "files": None, "users": None}

    widget = await _widget(client, headers)
    assert widget["data"] == [{
        "title": "Nextcloud nicht eingerichtet", "subtitle": body["message"], "label": "nicht eingerichtet", "tone": "neutral",
    }]
    assert widget["meta"] == {"state": "not_configured", "version": None}
    assert fake.requests == [], "ohne Einstellungen wird nichts angefragt"


@pytest.mark.asyncio
async def test_address_without_any_credentials_is_not_configured(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, password=None)
    assert (await _serverinfo(client, headers))["state"] == "not_configured"
    assert fake.requests == []

    # Ein nachgetragenes App-Passwort gilt sofort, auch innerhalb der 20 Sekunden des Zwischenspeichers.
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "nextcloud-app-password", "value": PASSWORD}, headers=headers)
    assert res.status_code == 204
    assert (await _serverinfo(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_token_wins_over_the_password_and_nothing_secret_leaks(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, token=TOKEN)

    res = await client.get(f"{API}/serverinfo", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body == {
        "state": "ok", "message": None, "auth": "token", "version": "31.0.7.1",
        "updates": {"core_available": True, "core_version": "31.0.9.1", "core_checked_at": "2026-10-12T08:00:00Z", "app_updates": 2},
        "storage": {"free_bytes": 48472944640, "files": 708860, "users": 7},
        "active_users": {"last_5_minutes": 2, "last_hour": 4, "last_24_hours": 5},
        "load": {
            "load_1": 0.85, "load_5": 1.04, "load_15": 1.17, "cpu_cores": 4,
            "memory_total_bytes": 8183664 * 1024, "memory_available_bytes": 5877156 * 1024,
        },
    }
    (request,) = fake.requests
    assert str(request.url) == INFO_URL and request.method == "GET"
    assert request.headers["NC-Token"] == TOKEN
    assert "Authorization" not in request.headers, "mit Token keine Basic-Anmeldung, auch wenn ein App-Passwort da ist"
    assert request.headers["OCS-APIRequest"] == "true"
    assert TOKEN not in str(request.url) and PASSWORD not in str(request.url)

    widget = await _widget(client, headers)
    assert [r["title"] for r in widget["data"]] == [
        "Nextcloud 31.0.7.1", "Apps", "Freier Speicher", "Dateien und Konten", "Aktive Nutzer", "Prozessor-Last", "Arbeitsspeicher",
    ]
    assert widget["meta"] == {"state": "ok", "version": "31.0.7.1"}
    assert len(fake.requests) == 1, "Seite und Kachel teilen sich die Abfrage"

    settings = (await client.get(f"/api/v1/extensions/{EXT}/settings", headers=headers)).json()
    slots = {s["label"]: s for s in settings["secrets"]}
    assert slots["nextcloud-serverinfo-token"]["is_set"] is True and slots["nextcloud-serverinfo-token"]["optional"] is True
    assert slots["nextcloud-app-password"]["is_set"] is True and slots["nextcloud-app-password"]["optional"] is False
    audit = (await db_session.execute(select(AuditEntry))).scalars().all()
    everything = res.text + json.dumps(widget) + json.dumps(settings) + caplog.text + json.dumps([a.detail or {} for a in audit], default=str)
    assert TOKEN not in everything and PASSWORD not in everything


@pytest.mark.asyncio
async def test_without_token_the_admin_login_is_used(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    body = await _serverinfo(client, headers)
    assert body["state"] == "ok" and body["auth"] == "password"
    (request,) = fake.requests
    assert request.headers["Authorization"] == _basic()
    assert "NC-Token" not in request.headers


@pytest.mark.asyncio
async def test_old_nextcloud_without_update_info_still_gives_a_tile(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.body = _fixture("serverinfo_old.json")
    await _configure(client, headers)
    body = await _serverinfo(client, headers)
    assert body["state"] == "ok" and body["version"] == "26.0.13.1"
    assert body["updates"] == {"core_available": None, "core_version": None, "core_checked_at": None, "app_updates": 0}
    assert body["load"]["cpu_cores"] is None
    rows = (await _widget(client, headers))["data"]
    assert rows[0]["label"] == "–" and rows[0]["tone"] == "neutral"


@pytest.mark.asyncio
async def test_wrong_credentials_say_what_to_do(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, password="falsch-falsch")
    body = await _serverinfo(client, headers)
    assert body["state"] == "auth_failed" and body["auth"] == "password"
    assert body["message"] == serverinfo.AUTH_TEXT
    assert body["version"] is None and body["load"]["cpu_cores"] is None
    assert (await _widget(client, headers))["data"] == [
        {"title": "Keine Rechte für die Kennzahlen", "subtitle": serverinfo.AUTH_TEXT, "label": "keine Rechte", "tone": "warn"}
    ]

    # Mit dem richtigen Token geht es sofort, obwohl die abgelehnte Anmeldung sonst gemerkt bleibt.
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "nextcloud-serverinfo-token", "value": TOKEN}, headers=headers)
    assert res.status_code == 204
    assert (await _serverinfo(client, headers))["state"] == "ok", "ein neu eingetragenes Token gilt sofort"


@pytest.mark.asyncio
async def test_wrong_token_is_rejected_the_same_way(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, password=None, token="falsches-token")
    body = await _serverinfo(client, headers)
    assert (body["state"], body["auth"]) == ("auth_failed", "token")
    assert "falsches-token" not in json.dumps(body)


@pytest.mark.asyncio
async def test_app_switched_off_is_a_state_with_a_hint(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.override = lambda request: httpx.Response(404, json=_fixture("serverinfo_app_off.json"))
    body = await _serverinfo(client, headers)
    assert body["state"] == "app_inactive" and body["message"] == serverinfo.APP_INACTIVE_TEXT
    (row,) = (await _widget(client, headers))["data"]
    assert (row["title"], row["label"], row["tone"]) == ("Die App „Server-Info“ ist nicht aktiv", "App aus", "warn")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "state", "label", "tone"),
    [
        (httpx.ReadTimeout("timed out"), "unreachable", "nicht erreichbar", "danger"),
        (httpx.ConnectError("[Errno 111] Connection refused"), "unreachable", "nicht erreichbar", "danger"),
        (httpx.RemoteProtocolError("kaputt"), "error", "Fehler", "warn"),
    ],
)
async def test_unreachable_server_is_a_clean_state_not_a_server_error(client, db_session, test_settings, error, state, label, tone):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.error = error
    body = await _serverinfo(client, headers)
    assert body["state"] == state and body["message"]
    (row,) = (await _widget(client, headers))["data"]
    assert (row["label"], row["tone"], row["subtitle"]) == (label, tone, body["message"])


@pytest.mark.asyncio
async def test_unexpected_answer_is_reported_as_such(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.override = lambda request: httpx.Response(200, text="<html>Anmeldung</html>")
    body = await _serverinfo(client, headers)
    assert body["state"] == "error" and body["message"].startswith("Unerwartete Antwort der Nextcloud:")
    (row,) = (await _widget(client, headers))["data"]
    assert row["label"] == "Fehler"


@pytest.mark.asyncio
async def test_redirect_is_reported_and_never_followed(client, db_session, test_settings, caplog):
    caplog.set_level(logging.INFO)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.override = lambda request: httpx.Response(302, headers={"Location": "http://angreifer.example/login"})
    res = await client.get(f"{API}/serverinfo", headers=headers)
    body = res.json()
    assert body["state"] == "error" and "leitet auf eine andere Adresse um (HTTP 302)" in body["message"]
    widget = await client.get(f"{API}/widgets/status", headers=headers)
    assert "angreifer" not in (res.text + widget.text + caplog.text).lower()
    assert all(r.url.host == "cloud.example.com" for r in fake.requests)


@pytest.mark.asyncio
async def test_tls_exception_is_used_only_when_asked_for(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    assert (await _serverinfo(client, headers))["state"] == "ok"
    assert fake.insecure_requests == [] and len(fake.requests) == 1

    await _configure(client, headers, insecure=True)
    assert (await _serverinfo(client, headers))["state"] == "ok"
    assert len(fake.insecure_requests) == 1, "mit der Einstellung geht die Abfrage über den Client ohne Zertifikatsprüfung"


@pytest.mark.asyncio
async def test_address_with_credentials_is_refused_before_anything_is_sent(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, token=TOKEN, url="https://alice:geheim@cloud.example.com")
    body = await _serverinfo(client, headers)
    assert body["state"] == "error" and "ungültig" in body["message"] and "geheim" not in json.dumps(body)
    assert fake.requests == []


@pytest.mark.asyncio
async def test_results_are_cached_for_twenty_seconds_and_refresh_skips_the_cache(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, token=TOKEN)
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

    first = await _serverinfo(client, headers)
    assert (await client.get(f"{API}/widgets/status", headers=headers)).status_code == 200
    assert await _serverinfo(client, headers) == first
    assert len(fake.requests) == 1, "Seite und Kachel fragen die Nextcloud nur einmal"
    assert reads == ["nextcloud-serverinfo-token"], "das Geheimnis wird nur für eine echte Abfrage geholt"

    await _serverinfo(client, headers, "?refresh=true")
    assert len(fake.requests) == 2, "der Knopf Aktualisieren umgeht den Zwischenspeicher"
    assert reads == ["nextcloud-serverinfo-token"] * 2

    now[0] += CACHE_TTL_S - 1
    await _serverinfo(client, headers)
    assert len(fake.requests) == 2
    now[0] += 2.0
    await _serverinfo(client, headers)
    assert len(fake.requests) == 3, "nach 20 Sekunden wird neu gefragt"

    # Geänderte Einstellungen gelten sofort.
    saved = await client.put(f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"username": "bob"}}, headers=headers)
    assert saved.status_code == 200, saved.text
    await _serverinfo(client, headers)
    assert len(fake.requests) == 4


@pytest.mark.asyncio
async def test_a_rejected_login_is_not_repeated_every_minute(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, password="falsch-falsch")
    service = _service()
    now = [5000.0]
    service._clock = lambda: now[0]

    assert (await _serverinfo(client, headers))["state"] == "auth_failed"
    for _ in range(4):
        now[0] += 60.0
        assert (await _serverinfo(client, headers))["state"] == "auth_failed"
        assert (await client.get(f"{API}/widgets/status", headers=headers)).status_code == 200
    assert len(fake.requests) == 1, "vier Minuten lang keine weitere Anmeldung"

    assert (await _serverinfo(client, headers, "?refresh=true"))["state"] == "auth_failed"
    assert len(fake.requests) == 2, "Aktualisieren fragt trotzdem"

    now[0] += AUTH_FAILED_TTL_S + 1
    await _serverinfo(client, headers)
    assert len(fake.requests) == 3


@pytest.mark.asyncio
async def test_testing_the_connection_makes_the_tile_ask_again(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, password="falsch-falsch")
    assert (await _serverinfo(client, headers))["state"] == "auth_failed"
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "nextcloud-app-password", "value": PASSWORD}, headers=headers)
    assert res.status_code == 204
    assert (await _serverinfo(client, headers))["state"] == "auth_failed", "noch aus dem Zwischenspeicher"

    loaded = get_extension_runtime().loaded[EXT]
    await loaded.instance.health(loaded.ctx)
    assert (await _serverinfo(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_changing_the_address_removes_both_secrets_so_they_never_go_to_a_new_target(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, token=TOKEN)
    assert (await _serverinfo(client, headers))["state"] == "ok"
    saved = await client.put(
        f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"base_url": "https://angreifer.example.com"}}, headers=headers
    )
    assert saved.status_code == 200, saved.text
    assert sorted(saved.json()["secrets_cleared"]) == ["nextcloud-app-password", "nextcloud-serverinfo-token"]
    assert (await _serverinfo(client, headers))["state"] == "not_configured"
    assert all(r.url.host != "angreifer.example.com" for r in fake.requests)


@pytest.mark.asyncio
async def test_unexpected_errors_do_not_become_a_500(client, db_session, test_settings, caplog):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    service = _service()

    async def broken(*args, **kwargs):
        raise RuntimeError("innerer Fehler mit " + PASSWORD)

    service._load = broken
    body = await _serverinfo(client, headers)
    assert body["state"] == "error" and "unerwarteter Fehler" in body["message"]
    assert PASSWORD not in json.dumps(body)
    assert (await client.get(f"{API}/widgets/status", headers=headers)).status_code == 200
    assert "innerer Fehler" in caplog.text, "im Protokoll steht die Ursache"


@pytest.mark.asyncio
async def test_unreadable_secret_is_a_hint_not_a_crash(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, token=TOKEN)
    loaded = get_extension_runtime().loaded[EXT]

    async def boom(label):
        raise RuntimeError("Tresor kaputt " + TOKEN)

    loaded.ctx.secrets.get_handle = boom
    body = await _serverinfo(client, headers)
    assert body["state"] == "error" and "Tresor" in body["message"] and TOKEN not in json.dumps(body)
    assert fake.requests == []


@pytest.mark.asyncio
async def test_routes_need_a_login_and_hosts_read(client, db_session, test_settings):
    from nodvard_deck.core import security
    from nodvard_deck.models import Role, RolePermission, User

    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, token=TOKEN)
    for path in ("/serverinfo", "/widgets/status"):
        assert (await client.get(f"{API}{path}")).status_code == 401, path

    roles = {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=headers)).json()}
    created = await client.post(
        "/api/v1/users", json={"username": "gast", "password": "gast-passwort-1", "role_ids": [roles["viewer"]]}, headers=headers,
    )
    assert created.status_code == 201, created.text
    login = await client.post("/api/v1/auth/login", json={"username": "gast", "password": "gast-passwort-1"})
    viewer = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get(f"{API}/serverinfo", headers=viewer)).status_code == 200
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
    for path in ("/serverinfo", "/widgets/status", "/serverinfo?refresh=true"):
        assert (await client.get(f"{API}{path}", headers=nobody)).status_code == 403, path
    assert len(fake.requests) == before

    # Auf diesen Adressen lässt sich nichts schreiben; das Token setzen bleibt `secrets.write`.
    for method in ("post", "put", "delete", "patch"):
        res = await getattr(client, method)(f"{API}/serverinfo", headers=headers)
        assert res.status_code in (404, 405), (method, res.status_code)
    assert (await client.post(f"{API}/token", json={"value": "x"}, headers=viewer)).status_code == 403


@pytest.mark.asyncio
async def test_settings_page_offers_the_optional_token(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    settings = (await client.get(f"/api/v1/extensions/{EXT}/settings", headers=headers)).json()
    slots = {s["label"]: s for s in settings["secrets"]}
    assert list(slots) == ["nextcloud-app-password", "nextcloud-serverinfo-token"]
    token = slots["nextcloud-serverinfo-token"]
    assert (token["title"], token["optional"], token["is_set"]) == ("Token für Server-Info (optional)", True, False)
    assert "occ config:app:set serverinfo token" in token["description"]
    # Ohne gespeicherte Adresse nimmt der Tresor das Token nicht an (es gehört zu einem Ziel).
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "nextcloud-serverinfo-token", "value": TOKEN}, headers=headers)
    assert res.status_code == 409
    # Und ein fremdes Geheimnis geht nicht.
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "nextcloud-etwas-anderes", "value": TOKEN}, headers=headers)
    assert res.status_code == 422


def test_openapi_describes_the_info_with_typed_fields():
    schema = ServerInfo.model_json_schema()
    assert set(schema["properties"]) == {"state", "message", "auth", "version", "updates", "storage", "active_users", "load"}
    assert set(schema["$defs"]["UpdateInfo"]["properties"]) == {"core_available", "core_version", "core_checked_at", "app_updates"}
    assert set(schema["$defs"]["StorageInfo"]["properties"]) == {"free_bytes", "files", "users"}
    assert set(schema["$defs"]["ActiveUsers"]["properties"]) == {"last_5_minutes", "last_hour", "last_24_hours"}
    assert set(schema["$defs"]["LoadInfo"]["properties"]) == {
        "load_1", "load_5", "load_15", "cpu_cores", "memory_total_bytes", "memory_available_bytes",
    }
