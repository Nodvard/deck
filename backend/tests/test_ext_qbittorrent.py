"""Tests für die qBittorrent-Erweiterung; qBittorrent ist ein `httpx.MockTransport` mit Anmeldung und Sitzungs-Cookie."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import AuditEntry, ExtensionRecord
from nodvard_deck.services import extensions as extensions_service
from nodvard_sdk import CertificateMismatch
from nodvard_sdk.errors import PermissionDenied
from sqlalchemy import select

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


class _ExtModule:
    """Ein Modul der Erweiterung, geladen erst beim Zugriff im Test, wenn die Fixture den Pfad gesetzt hat."""

    def __init__(self, name: str) -> None:
        self._name = f"nodvard_deck_ext_qbittorrent.{name}"

    def __getattr__(self, attr: str) -> Any:
        return getattr(importlib.import_module(self._name), attr)


qbt_client = _ExtModule("client")
qbt_config = _ExtModule("config")
qbt_service = _ExtModule("service")
qbt_widgets = _ExtModule("widgets")
parse = _ExtModule("parse")
transport = _ExtModule("transport")

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "qbittorrent"
FIXTURE_FILES = ("transfer_info.json", "torrents_active.json", "maindata.json", "maindata_v4.json")
BASE = "http://qbt.example.com:8080"
USER = "admin"
PASSWORD = "Beispiel&Pass wort=1+%"
PIN = ":".join(["AB"] * 32)
EXT = "qbittorrent"
API = "/api/v1/ext/qbittorrent"
LOGIN = "/api/v2/auth/login"
LOGOUT = "/api/v2/auth/logout"
BANNED_TEXT = "Your IP address has been banned after too many failed authentication attempts."


@pytest.fixture(autouse=True)
def _qbittorrent_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_EXTENSIONS_DIR / "qbittorrent" / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _torrent(name: str, state: str = "downloading", dl: int = 1000, up: int = 0, **over: Any) -> dict[str, Any]:
    return {"name": name, "state": state, "progress": 0.5, "size": 1000, "dlspeed": dl, "upspeed": up, "eta": 60, **over}


def test_fixtures_hold_no_private_addresses():
    text = "".join((FIXTURES / name).read_text(encoding="utf-8") for name in FIXTURE_FILES)
    for address in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
        assert address.startswith("203.0.113."), address
    for host in re.findall(r"https?://([^/\s\"':]+)", text):
        assert host == "example.com" or host.endswith(".example.com"), host


def test_transfer_fixture_gives_speed_connection_and_limits():
    transfer = parse.parse_transfer(_json("transfer_info.json"))
    assert transfer is not None
    assert transfer.model_dump() == {
        "download_speed": 4 * 1024 * 1024 + 100 * 1024, "upload_speed": 800 * 1024, "connection": "connected",
        "download_limit": None, "upload_limit": None, "limited": False, "alt_limits": None,
    }


@pytest.mark.parametrize(
    ("body", "limited", "limits"),
    [
        ({"dl_info_speed": 1, "dl_rate_limit": 1048576, "up_rate_limit": 0}, True, (1048576, None)),
        ({"up_info_speed": 1, "dl_rate_limit": 0, "up_rate_limit": 524288}, True, (None, 524288)),
        ({"dl_info_speed": 1, "dl_rate_limit": -1, "up_rate_limit": "viel"}, False, (None, None)),
        ({"connection_status": "firewalled"}, False, (None, None)),
    ],
)
def test_transfer_limits_are_read_defensively(body, limited, limits):
    transfer = parse.parse_transfer(body)
    assert transfer is not None
    assert (transfer.limited, transfer.download_limit, transfer.upload_limit) == (limited, *limits)


@pytest.mark.parametrize(
    ("status", "expected"),
    [("connected", "connected"), ("firewalled", "firewalled"), ("disconnected", "disconnected"), ("kaputt", "unknown"), (5, "unknown")],
)
def test_connection_status_is_one_of_the_known_values(status, expected):
    transfer = parse.parse_transfer({"dl_info_speed": 0, "connection_status": status})
    assert transfer is not None and transfer.connection == expected


@pytest.mark.parametrize("body", [{}, {"foo": 1}, [], "text", None, 7])
def test_transfer_that_is_not_qbittorrent_is_refused(body):
    assert parse.parse_transfer(body) is None


def test_transfer_values_that_are_not_numbers_become_zero():
    transfer = parse.parse_transfer({"dl_info_speed": "schnell", "up_info_speed": True, "connection_status": "connected"})
    assert transfer is not None and (transfer.download_speed, transfer.upload_speed) == (0, 0)


def test_active_fixture_lists_the_moving_torrents():
    torrents = parse.parse_torrents(_json("torrents_active.json"))
    assert torrents is not None
    assert [(t.name, t.state, t.progress_percent, t.download_speed, t.upload_speed, t.eta_seconds) for t in torrents] == [
        ("ubuntu-24.04.iso", "downloading", 42, 3145728, 262144, 1250),
        ("Beispiel-Hörbuch (2019)", "downloading", 7, 1048576, 0, 5400),
        ("debian-12.5.0-amd64-netinst.iso", "uploading", 100, 0, 524288, None),
        ("freebsd-14.0-amd64-disc1.iso", "uploading", 100, 0, 49152, None),
    ]
    assert torrents[0].size == 6 * 1024**3


@pytest.mark.parametrize(
    ("raw", "group"),
    [
        ("downloading", "downloading"), ("forcedDL", "downloading"), ("metaDL", "downloading"), ("forcedMetaDL", "downloading"),
        ("stalledDL", "stalled_downloading"), ("uploading", "uploading"), ("forcedUP", "uploading"),
        ("stalledUP", "stalled_uploading"),
        ("pausedDL", "paused"), ("pausedUP", "paused"), ("stoppedDL", "paused"), ("stoppedUP", "paused"),
        ("queuedDL", "queued"), ("queuedUP", "queued"),
        ("checkingDL", "checking"), ("checkingUP", "checking"), ("checkingResumeData", "checking"),
        ("moving", "moving"), ("error", "error"), ("missingFiles", "error"),
        ("unknown", "unknown"), ("etwasNeues", "unknown"), ("", "unknown"), (None, "unknown"), (5, "unknown"), (["error"], "unknown"),
    ],
)
def test_states_of_version_4_and_5_fall_into_the_same_groups(raw, group):
    assert parse.classify_state(raw) == group


def test_torrent_fields_are_read_defensively():
    long_name = "N" * 200
    body: list[Any] = [
        "kein Objekt", 7, None, [],
        {},
        _torrent(long_name),
        _torrent("Zeile 1\nZeile 2\x00\x07\u202e<b>x</b>"),
        {"name": 5, "state": ["x"], "progress": "halb", "size": -4, "dlspeed": float("nan"), "upspeed": None, "eta": "bald"},
        _torrent("Fortschritt", progress=1.7),
        _torrent("Negativ", progress=-0.3),
        _torrent("Unendlich", eta=8_640_000),
        _torrent("Darüber", eta=9_999_999),
        _torrent("Minus", eta=-1),
    ]
    torrents = parse.parse_torrents(body)
    assert torrents is not None and len(torrents) == 9
    by_name = {t.name: t for t in torrents}
    assert len(by_name["N" * 79 + "…"].name) == 80
    assert "Zeile 1 Zeile 2 <b>x</b>" in by_name
    empty = by_name[parse.UNKNOWN_NAME]
    assert (empty.state, empty.progress_percent, empty.size, empty.download_speed, empty.upload_speed, empty.eta_seconds) == (
        "unknown", 0, None, 0, 0, None,
    )
    assert (by_name["Fortschritt"].progress_percent, by_name["Negativ"].progress_percent) == (100, 0)
    assert [by_name[n].eta_seconds for n in ("Unendlich", "Darüber", "Minus")] == [None, None, None]


@pytest.mark.parametrize("body", [{}, "text", 5, None, {"torrents": []}])
def test_torrents_that_are_not_a_list_are_refused(body):
    assert parse.parse_torrents(body) is None


@pytest.mark.parametrize(
    ("fixture", "paused_states"), [("maindata.json", {"stoppedUP", "stoppedDL"}), ("maindata_v4.json", {"pausedUP", "pausedDL"})]
)
def test_maindata_fixtures_count_the_states_the_same_in_both_versions(fixture, paused_states):
    body = _json(fixture)
    assert {t["state"] for t in body["torrents"].values()} >= paused_states
    data = parse.parse_maindata(body)
    assert data is not None
    assert data.counts.model_dump() == {"total": 14, "downloading": 4, "seeding": 3, "paused": 2, "error": 2, "other": 3}
    assert (data.free_space, data.alt_limits) == (500 * 1024**3, False)


@pytest.mark.parametrize(("free", "expected"), [(-1, None), (0, 0), (123456, 123456), (None, None), ("viel", None), (True, None)])
def test_free_space_is_unknown_until_qbittorrent_has_measured_it(free, expected):
    data = parse.parse_maindata({"full_update": True, "server_state": {"free_space_on_disk": free}})
    assert data is not None and data.free_space == expected


def test_maindata_without_torrents_means_an_empty_library():
    data = parse.parse_maindata({"rid": 1, "full_update": True, "server_state": {"use_alt_speed_limits": True}})
    assert data is not None
    assert (data.counts.total, data.counts.downloading, data.alt_limits, data.free_space) == (0, 0, True, None)


@pytest.mark.parametrize(
    "body",
    [{}, {"rid": 3, "torrents": {}}, {"full_update": False, "torrents": {}}, {"full_update": True, "torrents": []}, [], "x", None],
)
def test_maindata_that_is_not_a_complete_answer_is_refused(body):
    assert parse.parse_maindata(body) is None


def test_odd_entries_in_maindata_count_as_other():
    data = parse.parse_maindata({"full_update": True, "torrents": {"a": {"state": "downloading"}, "b": "kaputt", "c": {"state": 5}, "d": None}})
    assert data is not None and (data.counts.total, data.counts.downloading, data.counts.other) == (4, 1, 3)


@pytest.mark.parametrize(
    ("body", "expected"),
    [(b"v5.1.4", "v5.1.4"), (b"v4.6.7\n", "v4.6.7"), (b"5.0.0", "5.0.0"), (b"v5.0.0beta1", "v5.0.0beta1"), (b"v5.1.0rc1", "v5.1.0rc1"),
     (b"<html>Anmelden</html>", None), (b"", None), (b"v" + b"9" * 80, None), (b"Fails.", None)],
)
def test_version_is_read_from_plain_text(body, expected):
    assert parse.parse_version(body) == expected


@pytest.mark.parametrize(("body", "expected"), [(b"2.11.4", "2.11.4"), (b"2.9", "2.9"), (b"v2", None), (b"<html>", None), (b"", None)])
def test_api_version_is_read_from_plain_text(body, expected):
    assert parse.parse_api_version(body) == expected


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (("SID=abcdEFGH1234+/=; HttpOnly; path=/; SameSite=Lax",), ("SID", "abcdEFGH1234+/=")),
        (("QBT_SID_8080=abcdEFGH1234; HttpOnly; path=/",), ("QBT_SID_8080", "abcdEFGH1234")),
        (("meine-sitzung=abcdEFGH1234; path=/",), ("meine-sitzung", "abcdEFGH1234")),
        (("SID=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/", "SID=abcdEFGH1234; path=/"), ("SID", "abcdEFGH1234")),
        (("SID=kurz; path=/",), None),
        (("SID=abc def ghi1234; path=/",), None),
        (("=abcdEFGH1234",), None),
        (("kein Cookie",), None),
        ((), None),
    ],
)
def test_session_cookie_is_found_under_any_name(lines, expected):
    assert parse.session_cookie(lines) == expected


def test_base_url_is_normalized_and_credentials_are_refused():
    assert qbt_config.normalize_base_url("http://qbt.example.com:8080/") == BASE
    assert qbt_config.normalize_base_url(" https://qbt.example.com/qbt/ ") == "https://qbt.example.com/qbt"
    assert qbt_config.normalize_base_url("http://203.0.113.20:8080/?x=1#y") == "http://203.0.113.20:8080"
    assert qbt_config.normalize_base_url("http://203.0.113.20:8080/index.html") == "http://203.0.113.20:8080"
    assert qbt_config.normalize_base_url("https://qbt.example.com/qbt/index.html") == "https://qbt.example.com/qbt"
    for bad in ("qbt.example.com:8080", "ftp://qbt.example.com", "http://", "http://user:" + "pw@qbt.example.com", "http://@qbt.example.com", "http://qbt.example.com:999999", ""):
        with pytest.raises(ValueError):
            qbt_config.normalize_base_url(bad)


def test_fingerprint_is_normalized_or_refused():
    assert qbt_config.normalize_fingerprint(None) is None and qbt_config.normalize_fingerprint("  ") is None
    assert qbt_config.normalize_fingerprint("ab" * 32) == PIN
    for bad in ("kaputt", "AB:CD", 5, "A" * 300, ["x"]):
        with pytest.raises(ValueError):
            qbt_config.normalize_fingerprint(bad)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("admin", "admin"), ("  admin ", "admin"), ("", ""), (None, ""), (5, ""), ("ad\nmin", ""), ("x" * 129, ""), ("Frau Müller", "Frau Müller")],
)
def test_username_is_trimmed_or_empty(value, expected):
    assert qbt_config.normalize_username(value) == expected


def test_read_connections_flags_unusable_entries_without_dropping_the_rest():
    configs = qbt_config.read_connections({"connections": [
        {"name": "a", "base_url": "http://a.example.com:8080/", "username": " admin ", "tls_insecure_skip_verify": True},
        {"name": "a", "base_url": "http://zweite.example.com"},
        {"name": "b", "base_url": "http://user:" + "pw@b.example.com"},
        {"name": "e", "base_url": "https://e.example.com", "tls_fingerprint_sha256": "kaputt", "tls_insecure_skip_verify": True},
        {"name": "f", "base_url": "http://f.example.com", "tls_fingerprint_sha256": PIN},
        {"name": "g", "base_url": "https://g.example.com", "tls_fingerprint_sha256": "ab" * 32, "tls_insecure_skip_verify": True},
        {"name": "mit Leerzeichen", "base_url": "http://h.example.com"},
        {"name": " ", "base_url": "http://leer.example.com"},
        "kaputt",
    ]})
    assert [c.name for c in configs] == ["a", "a", "b", "e", "f", "g", "mit Leerzeichen"]
    first = configs[0]
    assert (first.base_url, first.username, first.insecure_tls, first.pin, first.problem) == ("http://a.example.com:8080", "admin", True, None, None)
    assert "mehrfach vergeben" in (configs[1].problem or "")
    assert "ungültig" in (configs[2].problem or "") and "pw" not in (configs[2].problem or "") and configs[2].base_url is None
    assert "Fingerabdruck" in (configs[3].problem or "") and "ungültig" in (configs[3].problem or "")
    assert "nur für https://" in (configs[4].problem or "")
    assert (configs[5].pin, configs[5].problem) == (PIN, None)
    assert "Kurzname" in (configs[6].problem or "")
    assert qbt_config.read_connections({}) == [] and qbt_config.read_connections({"connections": "x"}) == []


def test_text_helpers_use_german_notation():
    assert [qbt_widgets.size_text(n) for n in (0, 512, 1024, 1536, 10 * 1024, 3 * 1024**2 + 200 * 1024, 500 * 1024**3, 5 * 1024**4)] == [
        "0 B", "512 B", "1,0 KB", "1,5 KB", "10 KB", "3,2 MB", "500 GB", "5,0 TB",
    ]
    assert qbt_widgets.speed_text(2 * 1024**2) == "2,0 MB/s"
    assert [qbt_widgets.duration_text(n) for n in (0, 59, 60, 3599, 3600, 4320, 86400, 90000, 3 * 86400 + 3600)] == [
        "< 1 Min.", "< 1 Min.", "1 Min.", "59 Min.", "1 Std.", "1 Std. 12 Min.", "1 Tag", "1 Tag 1 Std.", "3 Tage 1 Std.",
    ]


class _Http:
    """Was `transport.send()` von `ctx.http` braucht."""

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
    with pytest.raises(transport.ResponseTooLarge) as caught:
        await transport.send(http, "GET", f"{BASE}/x", service="qBittorrent „q“", max_bytes=transport.MAX_LIST_BYTES)
    assert caught.value.state == "error"
    assert "ungewöhnlich große Antwort (mehr als 4 MB)" in str(caught.value)
    assert sent <= 6, "nach dem Limit wird nicht weitergelesen"
    assert (transport.MAX_SMALL_BYTES, transport.MAX_LIST_BYTES, transport.MAX_SYNC_BYTES, transport.TOTAL_TIMEOUT_S) == (
        1024 * 1024, 4 * 1024 * 1024, 8 * 1024 * 1024, 30.0,
    )


@pytest.mark.asyncio
async def test_transport_gives_up_after_the_total_time_limit():
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    with pytest.raises(transport.Unreachable) as caught:
        await transport.send(_Http(slow), "GET", f"{BASE}/x", service="qBittorrent", total_timeout_s=0.05)
    assert "antwortet nicht (Zeitüberschreitung)" in str(caught.value)


@pytest.mark.asyncio
async def test_transport_passes_the_tls_choice_the_form_and_the_cookies():
    http = _Http(lambda request: httpx.Response(
        200, content=b"Ok.", headers=[("set-cookie", "SID=abcdEFGH1234; path=/"), ("set-cookie", "x=1")],
    ))
    reply = await transport.send(
        http, "POST", "https://qbt.example.com/api/v2/auth/login", service="x", insecure_tls=True, form={"username": "a b", "password": "p&w"},
    )
    assert (reply.status_code, reply.body, reply.cookies) == (200, b"Ok.", ("SID=abcdEFGH1234; path=/", "x=1"))
    assert http.calls[0]["insecure_tls"] is True and http.calls[0]["pinned_sha256"] is None
    assert http.calls[0]["data"] == {"username": "a b", "password": "p&w"}

    # Mit Fingerabdruck geht nur er hinaus, nie zusammen mit dem Schalter (ctx.http lehnt beides zusammen ab).
    pinned = _Http(lambda request: httpx.Response(200, content=b"{}"))
    await transport.send(pinned, "GET", "https://qbt.example.com/x", service="x", insecure_tls=True, pin=PIN)
    assert pinned.calls[0]["pinned_sha256"] == PIN and pinned.calls[0]["insecure_tls"] is False
    assert "data" not in pinned.calls[0]


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
        (httpx.LocalProtocolError("Illegal header value b'SID=xyz'"), "error", "fehlerhafte Antwort"),
        (httpx.DecodingError("Error -3 while decompressing data: Geheim"), "error", "fehlerhafte Antwort"),
        (ValueError("irgendwas mit Geheim"), "error", "ließ sich nicht senden"),
    ],
)
async def test_transport_error_sentences(error, state, fragment):
    def boom(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(transport.QbtError) as caught:
        await transport.send(_Http(boom), "GET", f"{BASE}/x", service="qBittorrent „q“")
    assert caught.value.state == state
    assert fragment in str(caught.value)
    for secret in ("X-Geheim", "xyz", "Geheim", "self-signed"):
        assert secret not in str(caught.value), "der Text der Ausnahme wird nicht zitiert"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.asyncio
async def test_transport_says_when_the_core_refuses_the_target():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise PermissionDenied("qbittorrent", "net.outbound")

    with pytest.raises(transport.QbtError) as caught:
        await transport.send(_Http(refuse), "GET", f"{BASE}/x", service="qBittorrent „q“")
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

    with pytest.raises(transport.QbtError) as caught:
        await transport.send(_Http(boom), "POST", "https://qbt.example.com/x", service="qBittorrent „q“", pin=PIN, form={"password": "p"})
    text = str(caught.value)
    assert "anderes Zertifikat als das gemerkte" in text and "nicht das Passwort" in text
    assert "CD:CD" not in text and "AB:AB" not in text, "keine Fingerabdrücke im Satz"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


class FakeQbt:
    """qBittorrent mit Anmeldung per Formular, Sitzungs-Cookie, CSRF-Prüfung, Sperre und den Aufrufen, die das Modul braucht."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.insecure_requests: list[httpx.Request] = []
        self.pinned_requests: list[httpx.Request] = []
        self.pinned_error: Exception | None = None
        self.error: Exception | None = None
        self.overrides: dict[str, Callable[[httpx.Request], Any]] = {}
        self.username, self.password = USER, PASSWORD
        self.flavor = "5.1"  # „5.2“: falsche Anmeldung = 401, Erfolg = 204 ohne Text, Cookie `QBT_SID_<Port>`
        self.cookie_name = "SID"
        self.prefix = ""  # Unterpfad, unter dem qBittorrent hinter einem Proxy liegt
        self.sessions: set[str] = set()
        self.issued: list[str] = []
        self.logins = 0
        self.failed_logins = 0
        self.max_failures = 5
        self.banned = False
        self.open_network = False  # „Authentifizierung für Clients im Subnetz überspringen“
        self.version = "v5.1.4"
        self.api_version = "2.11.4"
        self.transfer = _json("transfer_info.json")
        self.active = _json("torrents_active.json")
        self.maindata = _json("maindata.json")

    # --- Aufrufe des Moduls
    async def handler(self, request: httpx.Request) -> Any:
        await asyncio.sleep(0)  # wie im Netz: andere Abschnitte kommen dazwischen
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if request.url.path in self.overrides:
            result = self.overrides[request.url.path](request)
            return await result if inspect.isawaitable(result) else result
        return self.answer(request)

    async def insecure_handler(self, request: httpx.Request) -> Any:
        self.insecure_requests.append(request)
        return await self.handler(request)

    async def pinned_handler(self, request: httpx.Request) -> Any:
        self.pinned_requests.append(request)
        if self.pinned_error is not None:
            raise self.pinned_error
        return await self.handler(request)

    # --- die nachgebaute WebUI
    def _same_origin(self, request: httpx.Request) -> bool:
        # webapplication.cpp (isCrossSiteRequest): Origin, sonst Referer, muss zu Host und Port der Anfrage passen.
        value = request.headers.get("origin") or request.headers.get("referer")
        if not value:
            return True
        return urlsplit(value).netloc == request.url.netloc.decode()

    def _sid(self, request: httpx.Request) -> str | None:
        for pair in request.headers.get("cookie", "").split(";"):
            name, _, value = pair.strip().partition("=")
            if name == self.cookie_name:
                return value
        return None

    def _login(self, request: httpx.Request) -> httpx.Response:
        form = dict(parse_qsl(request.content.decode("utf-8")))
        if self.banned:
            return httpx.Response(403, text=BANNED_TEXT)
        if form.get("username") == self.username and form.get("password") == self.password:
            self.logins += 1
            sid = f"{len(self.issued) + 1:03d}" + "AbCdEfGhIjKlMnOpQrStUvWxYz01"
            self.issued.append(sid)
            self.sessions.add(sid)
            cookie = f"{self.cookie_name}={sid}; HttpOnly; path=/; SameSite=Lax"
            if self.flavor == "5.2":
                return httpx.Response(204, headers={"set-cookie": cookie})
            return httpx.Response(200, text="Ok.", headers={"set-cookie": cookie})
        self.failed_logins += 1
        if self.failed_logins >= self.max_failures:
            self.banned = True
        return httpx.Response(401, text="Unauthorized") if self.flavor == "5.2" else httpx.Response(200, text="Fails.")

    def answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(self.prefix)
        if not self._same_origin(request):
            return httpx.Response(401, text="Unauthorized")
        if path == LOGIN and request.method == "POST":
            return self._login(request)
        if path == LOGOUT and request.method == "POST":
            self.sessions.discard(self._sid(request) or "")
            return httpx.Response(200)
        if not self.open_network and self._sid(request) not in self.sessions:
            return httpx.Response(403, text="Forbidden")
        query = dict(request.url.params)
        if request.method != "GET":
            return httpx.Response(405, text="Method Not Allowed")
        if path == "/api/v2/app/version":
            return httpx.Response(200, text=self.version)
        if path == "/api/v2/app/webapiVersion":
            return httpx.Response(200, text=self.api_version)
        if path == "/api/v2/transfer/info":
            return httpx.Response(200, json=self.transfer)
        if path == "/api/v2/torrents/info":
            return httpx.Response(200, json=self.active[: int(query.get("limit", "0")) or None])
        if path == "/api/v2/sync/maindata" and query.get("rid") == "0":
            return httpx.Response(200, json=self.maindata)
        return httpx.Response(404, text="Not Found")

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def posts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]

    def expire_sessions(self) -> None:
        self.sessions.clear()


async def _login(client, username="owner1", password="correct-horse-battery") -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _enable(client, db_session, test_settings) -> tuple[dict, FakeQbt]:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    headers = await _login(client)
    enabled = await client.post(f"/api/v1/extensions/{EXT}/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text

    fake = FakeQbt()
    http = get_extension_runtime().loaded[EXT].ctx.http
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    http._insecure_client = httpx.AsyncClient(transport=httpx.MockTransport(fake.insecure_handler))
    # Ein Client mit gemerktem Fingerabdruck wird je Fingerabdruck gebaut; hier steht der Nachbau schon bereit.
    http._pinned_clients[bytes.fromhex(PIN.replace(":", ""))] = httpx.AsyncClient(transport=httpx.MockTransport(fake.pinned_handler))
    return headers, fake


def _connection(name="qb", *, insecure=False, url=BASE, pin: str | None = None, username: str | None = USER) -> dict:
    entry: dict[str, Any] = {"name": name, "base_url": url}
    if username is not None:
        entry["username"] = username
    if insecure:
        entry["tls_insecure_skip_verify"] = True
    if pin:
        entry["tls_fingerprint_sha256"] = pin
    return entry


async def _configure(client, headers, *connections: dict, password: str | None = PASSWORD) -> None:
    saved = await client.put(f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": list(connections)}}, headers=headers)
    assert saved.status_code == 200, saved.text
    if password is not None:
        for connection in connections:
            res = await client.put(
                f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": f"qbittorrent-password:{connection['name']}", "value": password}, headers=headers,
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


def _no_secret_in(fake: FakeQbt, *texts: str) -> None:
    everything = "\n".join(texts)
    for secret in (PASSWORD, *fake.issued):
        assert secret not in everything
    assert "Beispiel&Pass" not in everything and "Beispiel%26Pass" not in everything


@pytest.mark.asyncio
async def test_extension_is_discovered_disabled_beta_and_registers_page_and_widget(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    record = await db_session.get(ExtensionRecord, EXT)
    assert record is not None and record.state == "disabled"

    headers, _ = await _enable(client, db_session, test_settings)
    assert record.state == "enabled", record.last_error
    row = next(r for r in (await client.get("/api/v1/extensions", headers=headers)).json() if r["id"] == EXT)
    assert (row["name"], row["stage"], row["category"]) == ("qBittorrent", "beta", "servers")

    pages = (await client.get("/api/v1/pages", headers=headers)).json()
    page = next(p for p in pages if p["ext_id"] == EXT)
    assert (page["path"], page["component"], page["title"], page["stage"]) == ("/qbittorrent", "QbittorrentPage", "qBittorrent", "beta")
    widgets = [w for w in (await client.get("/api/v1/widgets", headers=headers)).json() if w["ext_id"] == EXT]
    assert [w["id"] for w in widgets] == ["status"]
    assert widgets[0]["view"]["item"]["badge"] == {"text": "{{ label }}", "tone": "{{ tone }}"}
    assert widgets[0]["data_endpoint"] == "widgets/status"

    bundle = await client.get(f"/api/v1/extensions/{EXT}/frontend/index.js")
    assert bundle.status_code == 200
    assert "QbittorrentPage" in bundle.text


@pytest.mark.asyncio
async def test_manifest_asks_for_reading_only(client, db_session, test_settings):
    import tomllib

    manifest = tomllib.loads((REPO_EXTENSIONS_DIR / EXT / "extension.toml").read_text(encoding="utf-8"))
    assert manifest["extension"]["stage"] == "beta"
    assert manifest["extension"]["permissions"] == ["net.outbound", "net.outbound.insecure_tls", "secrets.read:qbittorrent-*"]
    await _enable(client, db_session, test_settings)
    registered = get_extension_runtime().actions._specs
    assert [t for t, (ext_id, _spec) in registered.items() if ext_id == EXT] == [], "keine Aktion, nichts zum Freigeben"


@pytest.mark.asyncio
async def test_nothing_configured_is_reported_cleanly(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    assert await _overview(client, headers) == {
        "connections": [], "summary": {"connections": 0, "download_speed": 0, "upload_speed": 0, "active": 0},
    }
    widget = await _widget(client, headers)
    assert widget["data"] == [{
        "title": "qBittorrent nicht eingerichtet", "subtitle": "Unter Einstellungen → Erweiterungen → qBittorrent eintragen.",
        "label": "nicht eingerichtet", "tone": "neutral",
    }]
    health = await _health()
    assert health.healthy is False and "noch nicht eingerichtet" in health.message
    assert fake.requests == [], "ohne Einstellungen wird nichts angefragt"


@pytest.mark.asyncio
async def test_widget_meta_says_not_configured_only_without_any_set_up_connection(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    # Nichts eingetragen: die Übersicht darf die Kachel ausblenden; die übrigen `meta`-Felder bleiben.
    assert (await _widget(client, headers))["meta"] == {
        "connections": 0, "download_speed": 0, "upload_speed": 0, "active": 0, "state": "not_configured",
    }

    # Eine Verbindung ohne Passwort, bei der qBittorrent eine Anmeldung verlangt, ist nicht eingerichtet;
    # die Textzeile bleibt für ältere Oberflächen.
    await _configure(client, headers, _connection(), password=None)
    widget = await _widget(client, headers)
    assert widget["meta"]["state"] == "not_configured" and widget["meta"]["connections"] == 1
    assert widget["data"][0]["label"] == "nicht eingerichtet"

    # Mit Passwort gilt die Verbindung als eingerichtet, auch wenn der Server nicht antwortet: Fehler bleiben sichtbar.
    await _configure(client, headers, _connection())
    fake.error = httpx.ConnectError("refused")
    widget = await _widget(client, headers)
    assert "state" not in widget["meta"] and widget["meta"]["connections"] == 1
    assert _only(await _overview(client, headers))["state"] == "unreachable"

    # Eine eingerichtete und eine nicht eingerichtete Verbindung: das Modul ist eingerichtet.
    fake.error = None
    await _configure(client, headers, _connection(), _connection("zwei"), password=None)
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "qbittorrent-password:qb", "value": PASSWORD}, headers=headers)
    assert res.status_code == 204
    assert [c["state"] for c in (await _overview(client, headers))["connections"]] == ["ok", "not_configured"]
    widget = await _widget(client, headers)
    assert "state" not in widget["meta"] and widget["meta"]["connections"] == 2

@pytest.mark.asyncio
async def test_full_overview_shows_version_speed_active_torrents_counts_and_free_space(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())

    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    connection = _only(body)
    assert (connection["name"], connection["url"], connection["state"], connection["message"]) == ("qb", BASE, "ok", None)
    assert (connection["version"], connection["api_version"], connection["problems"], connection["notes"]) == ("v5.1.4", "2.11.4", [], [])
    assert connection["transfer"] == {
        "download_speed": 4 * 1024 * 1024 + 100 * 1024, "upload_speed": 800 * 1024, "connection": "connected",
        "download_limit": None, "upload_limit": None, "limited": False, "alt_limits": False,
    }
    assert [(t["name"], t["state"], t["progress_percent"]) for t in connection["active"]] == [
        ("ubuntu-24.04.iso", "downloading", 42), ("Beispiel-Hörbuch (2019)", "downloading", 7),
        ("debian-12.5.0-amd64-netinst.iso", "uploading", 100), ("freebsd-14.0-amd64-disc1.iso", "uploading", 100),
    ]
    assert connection["active_more"] is False
    assert connection["counts"] == {"total": 14, "downloading": 4, "seeding": 3, "paused": 2, "error": 2, "other": 3}
    assert connection["free_space"] == 500 * 1024**3
    assert body["summary"] == {"connections": 1, "download_speed": 4 * 1024 * 1024 + 100 * 1024, "upload_speed": 800 * 1024, "active": 4}

    widget = await _widget(client, headers)
    assert widget["data"][0] == {
        "title": "qBittorrent v5.1.4",
        "subtitle": "qb · ↓ 4,1 MB/s · ↑ 800 KB/s · 4 laden, 3 verteilen, 2 pausiert, 2 mit Fehler, 3 warten oder prüfen · 500 GB frei",
        "label": "2 Fehler", "tone": "warn",
    }
    assert widget["data"][1] == {
        "title": "ubuntu-24.04.iso", "subtitle": "42 % · ↓ 3,0 MB/s · ↑ 256 KB/s · noch 20 Min. · 6,0 GB", "label": "lädt", "tone": "good",
    }
    assert widget["meta"] == {"connections": 1, "download_speed": 4 * 1024 * 1024 + 100 * 1024, "upload_speed": 800 * 1024, "active": 4}

    # Das Passwort und die Sitzung stehen nirgends: weder in Antworten noch in Einstellungen, Protokoll oder Audit.
    settings = (await client.get(f"/api/v1/extensions/{EXT}/settings", headers=headers)).json()
    assert {s["label"]: s["is_set"] for s in settings["secrets"]} == {"qbittorrent-password:qb": True}
    audit = (await db_session.execute(select(AuditEntry))).scalars().all()
    _no_secret_in(fake, res.text, json.dumps(widget), json.dumps(settings), caplog.text, json.dumps([a.detail or {} for a in audit], default=str))


@pytest.mark.asyncio
async def test_login_sends_the_form_with_referer_and_origin_and_then_every_read_carries_the_session(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    assert _only(await _overview(client, headers))["state"] == "ok"

    login = fake.requests[0]
    assert (login.method, login.url.path) == ("POST", LOGIN)
    assert login.headers["content-type"] == "application/x-www-form-urlencoded"
    assert dict(parse_qsl(login.content.decode())) == {"username": USER, "password": PASSWORD}
    assert "Beispiel&Pass" not in login.content.decode(), "Sonderzeichen werden im Formular kodiert"
    assert (login.headers["referer"], login.headers["origin"]) == (BASE, BASE)
    assert "SID" not in login.headers["cookie"], "bei der Anmeldung geht keine alte Sitzung mit"

    reads = fake.requests[1:]
    assert fake.paths()[1] == "/api/v2/app/version", "zuerst allein"
    assert sorted(r.url.path for r in reads) == sorted(
        ["/api/v2/app/version", "/api/v2/app/webapiVersion", "/api/v2/transfer/info", "/api/v2/torrents/info", "/api/v2/sync/maindata"]
    )
    assert {r.method for r in reads} == {"GET"}
    assert all(r.headers["cookie"] == f"SID={fake.issued[0]}" for r in reads)
    assert all((r.headers["referer"], r.headers["origin"]) == (BASE, BASE) for r in reads)
    assert all(PASSWORD not in str(r.url) and not r.content for r in reads)
    info = next(r for r in reads if r.url.path == "/api/v2/torrents/info")
    assert dict(info.url.params) == {"filter": "active", "sort": "dlspeed", "reverse": "true", "limit": "51"}
    assert dict(next(r for r in reads if r.url.path == "/api/v2/sync/maindata").url.params) == {"rid": "0"}


@pytest.mark.asyncio
async def test_the_session_is_reused_and_a_refresh_does_not_log_in_again(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    service = _service()
    now = [1000.0]
    service._clock = lambda: now[0]

    await _overview(client, headers)
    await _overview(client, headers, "?refresh=true")
    now[0] += qbt_service.CACHE_TTL_S + 1
    await _overview(client, headers)
    assert fake.logins == 1 and len(fake.posts()) == 1
    assert len(fake.requests) == 1 + 3 * 5, "ein Anmelden, dreimal fünf Lesezugriffe"


@pytest.mark.asyncio
@pytest.mark.parametrize(("flavor", "cookie_name"), [("5.1", "SID"), ("5.1", "meine-sitzung"), ("5.2", "QBT_SID_8080")])
async def test_the_cookie_name_is_taken_from_the_answer(client, db_session, test_settings, flavor, cookie_name):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.flavor, fake.cookie_name = flavor, cookie_name
    await _configure(client, headers, _connection())
    assert _only(await _overview(client, headers))["state"] == "ok"
    assert all(r.headers["cookie"] == f"{cookie_name}={fake.issued[0]}" for r in fake.requests[1:])


@pytest.mark.asyncio
async def test_an_expired_session_logs_in_exactly_once_more(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    await _overview(client, headers)
    assert fake.logins == 1
    fake.expire_sessions()
    fake.requests.clear()

    connection = _only(await _overview(client, headers, "?refresh=true"))
    assert connection["state"] == "ok" and connection["problems"] == []
    assert fake.logins == 2
    assert fake.paths()[:3] == ["/api/v2/app/version", LOGIN, "/api/v2/app/version"], "403, neu anmelden, dasselbe noch einmal"
    assert len(fake.posts()) == 1


@pytest.mark.asyncio
async def test_parallel_requests_with_an_expired_session_share_one_new_login(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    await _overview(client, headers)
    fake.requests.clear()

    def version_then_expire(request: httpx.Request) -> httpx.Response:
        reply = fake.answer(request)
        fake.expire_sessions()
        return reply

    fake.overrides["/api/v2/app/version"] = version_then_expire
    fake.expire_sessions()
    connection = _only(await _overview(client, headers, "?refresh=true"))
    assert connection["state"] == "ok" and connection["problems"] == [], "alle vier Abschnitte laufen weiter"
    assert fake.logins == 3, "erste Anmeldung, nach der Version eine für alle vier gleichzeitigen Abschnitte"
    assert len(fake.posts()) == 2


@pytest.mark.asyncio
async def test_a_session_that_is_refused_even_after_a_new_login_is_not_retried_again(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/api/v2/transfer/info"] = lambda request: httpx.Response(403, text="Forbidden")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["transfer"] is None
    assert connection["problems"] == [
        (
            "Geschwindigkeit: qBittorrent „qb“ nimmt die Sitzung nicht an (HTTP 403), obwohl die Anmeldung geklappt hat. "
            "Entfernt ein Proxy davor Cookies?"
        )
    ]
    assert fake.logins == 2 and len(fake.posts()) == 2
    assert sum(1 for r in fake.requests if r.url.path == "/api/v2/transfer/info") == 2, "einmal wiederholt, nicht öfter"


@pytest.mark.asyncio
@pytest.mark.parametrize("flavor", ["5.1", "5.2"])
async def test_rejected_login_says_what_to_do_and_asks_only_once(client, db_session, test_settings, flavor, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    fake.flavor = flavor
    await _configure(client, headers, _connection(), password="falsches-passwort")

    res = await client.get(f"{API}/overview", headers=headers)
    connection = _only(res.json())
    assert connection["state"] == "auth_failed"
    assert connection["message"].startswith("qBittorrent „qb“: Benutzername oder Passwort wurden abgelehnt")
    assert ("(HTTP 401)" in connection["message"]) == (flavor == "5.2")
    assert ("Host-Header" in connection["message"]) == (flavor == "5.2")
    assert "Verbindung testen" in connection["message"] and "in 30 Minuten wieder" in connection["message"]
    assert (connection["transfer"], connection["active"], connection["counts"], connection["free_space"]) == (None, None, None, None)
    assert fake.paths() == [LOGIN], "nach einer abgelehnten Anmeldung kommt keine zweite Anfrage"
    assert fake.failed_logins == 1

    rows = (await _widget(client, headers))["data"]
    assert rows == [{"title": "qb: Anmeldung fehlgeschlagen", "subtitle": connection["message"], "label": "Anmeldung fehlgeschlagen", "tone": "danger"}]
    assert "falsches-passwort" not in res.text + caplog.text
    health = await _health()
    assert health.healthy is False and "wurden abgelehnt" in health.message


@pytest.mark.asyncio
async def test_a_rejected_login_pauses_for_thirty_minutes_even_on_refresh(client, db_session, test_settings):
    assert qbt_service.AUTH_FAILED_TTL_S == 1800.0
    headers, fake = await _enable(client, db_session, test_settings)
    fake.max_failures = 100
    await _configure(client, headers, _connection(), password="falsch")
    service = _service()
    now = [5000.0]
    service._clock = lambda: now[0]

    assert _only(await _overview(client, headers))["state"] == "auth_failed"
    for _ in range(29):
        now[0] += 60.0
        assert _only(await _overview(client, headers, "?refresh=true"))["state"] == "auth_failed"
        assert (await client.get(f"{API}/widgets/status", headers=headers)).status_code == 200
    assert fake.failed_logins == 1, "29 Minuten lang kein weiterer Fehlversuch, auch nicht auf „Aktualisieren“"

    now[0] += 61.0
    await _overview(client, headers)
    assert fake.failed_logins == 2, "nach 30 Minuten wird wieder gefragt"


@pytest.mark.asyncio
async def test_the_pause_keeps_the_dashboard_below_the_ban_limit_of_qbittorrent(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), password="falsch")
    service = _service()
    now = [0.0]
    service._clock = lambda: now[0]
    for _ in range(40):
        now[0] += qbt_service.CACHE_TTL_S + 1
        await _overview(client, headers)
    assert fake.failed_logins == 1 and fake.banned is False


@pytest.mark.asyncio
async def test_changed_settings_end_the_pause(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), password="falsch")
    assert _only(await _overview(client, headers))["state"] == "auth_failed"
    fake.password = "falsch"
    saved = await client.put(f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": [_connection(insecure=True)]}}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert not saved.json().get("secrets_cleared"), "dieselbe Adresse: das Passwort bleibt"
    assert _only(await _overview(client, headers))["state"] == "ok"
    assert fake.logins == 1


@pytest.mark.asyncio
async def test_testing_the_connection_ends_the_pause_and_logs_in_again(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), password="falsch")
    assert _only(await _overview(client, headers))["state"] == "auth_failed"
    fake.password = "falsch"
    assert _only(await _overview(client, headers))["state"] == "auth_failed", "noch aus dem Zwischenspeicher"

    health = await _health()
    assert health.healthy is True
    assert _only(await _overview(client, headers))["state"] == "ok"


async def _set_password(client, headers, value: str, name: str = "qb") -> None:
    res = await client.put(
        f"/api/v1/extensions/{EXT}/secrets",
        json={"current_password": "correct-horse-battery", "label": f"qbittorrent-password:{name}", "value": value}, headers=headers,
    )
    assert res.status_code == 204, res.text


@pytest.mark.asyncio
async def test_a_new_password_in_the_vault_ends_the_pause_at_once(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), password="falsch")
    assert _only(await _overview(client, headers))["state"] == "auth_failed"
    await _set_password(client, headers, PASSWORD)
    assert _only(await _overview(client, headers))["state"] == "ok"
    assert fake.failed_logins == 1 and fake.logins == 1


@pytest.mark.asyncio
async def test_a_new_password_in_the_vault_drops_the_old_session(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    assert _only(await _overview(client, headers))["state"] == "ok"
    assert len(fake.sessions) == 1
    await _set_password(client, headers, "falsch")
    assert fake.sessions == set(), "die alte Sitzung ist bei qBittorrent abgemeldet"
    assert _only(await _overview(client, headers))["state"] == "auth_failed", "nicht weiter mit der Sitzung von vorher"
    assert fake.failed_logins == 1


@pytest.mark.asyncio
async def test_a_changed_secret_touches_only_its_own_connection(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), _connection(name="qb2"))
    await _overview(client, headers)
    service, loaded = _service(), get_extension_runtime().loaded[EXT]
    assert set(service._cache) == {"qb", "qb2"}
    await loaded.instance.on_secret_changed(loaded.ctx, "qbittorrent-password:qb2")
    assert set(service._cache) == {"qb"} and set(service._boxes) == {"qb"}
    await loaded.instance.on_secret_changed(loaded.ctx, "qbittorrent-sonstwas")
    assert set(service._cache) == {"qb"}


@pytest.mark.asyncio
async def test_an_answer_with_the_old_password_is_not_kept(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    service, loaded = _service(), get_extension_runtime().loaded[EXT]
    load, entered, release, loads = service._load_bounded, asyncio.Event(), asyncio.Event(), []

    async def slow(cfg, has_password):
        loads.append(cfg.name)
        if len(loads) == 1:
            entered.set()
            await release.wait()
        return await load(cfg, has_password)

    service._load_bounded = slow
    first = asyncio.create_task(_overview(client, headers))
    await entered.wait()
    await loaded.instance.on_secret_changed(loaded.ctx, "qbittorrent-password:qb")
    release.set()
    await first
    await _overview(client, headers)
    assert loads == ["qb", "qb"], "was die Abfrage mit dem alten Passwort einträgt, gilt nicht mehr"


@pytest.mark.asyncio
async def test_a_ban_has_its_own_sentence_and_also_pauses(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.banned = True
    await _configure(client, headers, _connection())
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "auth_failed"
    assert connection["message"].startswith("qBittorrent „qb“: qBittorrent hat die Adresse des Dashboards gesperrt")
    assert "zu viele falsche Anmeldungen" in connection["message"] and "Vorgabe: eine Stunde" in connection["message"]
    assert "wurden abgelehnt" not in connection["message"]
    assert fake.paths() == [LOGIN]
    await _overview(client, headers, "?refresh=true")
    assert fake.paths() == [LOGIN], "auch auf „Aktualisieren“ keine neue Anmeldung"


@pytest.mark.asyncio
async def test_a_forbidden_login_without_the_ban_text_is_a_rejection(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides[LOGIN] = lambda request: httpx.Response(403, text="Forbidden: <b>evil.example.org</b>")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "auth_failed" and "(HTTP 403)" in connection["message"]
    assert "evil.example.org" not in connection["message"]


@pytest.mark.asyncio
async def test_a_session_that_expires_while_the_password_changed_is_a_rejected_login(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    assert _only(await _overview(client, headers))["state"] == "ok"
    fake.expire_sessions()
    fake.password = "in-qbittorrent-geaendert"
    connection = _only(await _overview(client, headers, "?refresh=true"))
    assert connection["state"] == "auth_failed" and fake.failed_logins == 1
    await _overview(client, headers, "?refresh=true")
    assert fake.failed_logins == 1


@pytest.mark.asyncio
async def test_without_a_password_nothing_is_sent_to_log_in_and_the_reads_go_straight_out(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.open_network = True
    await _configure(client, headers, _connection(username=None), password=None)
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["version"] == "v5.1.4" and connection["counts"]["total"] == 14
    assert fake.posts() == [] and fake.logins == 0
    assert {r.headers["cookie"] for r in fake.requests} == {"nodvard_deck=1"}


@pytest.mark.asyncio
async def test_without_a_password_where_a_login_is_needed_says_so_without_asking_to_log_in(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), password=None)
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "not_configured"
    assert connection["message"].startswith("Für qb fehlt das Passwort, und qBittorrent verlangt eine Anmeldung.")
    assert "Authentifizierung für Clients im Subnetz überspringen" in connection["message"]
    assert fake.paths() == ["/api/v2/app/version"] and fake.posts() == []
    rows = (await _widget(client, headers))["data"]
    assert rows == [{"title": "qb: nicht eingerichtet", "subtitle": connection["message"], "label": "nicht eingerichtet", "tone": "neutral"}]
    health = await _health()
    assert health.healthy is False and "fehlt das Passwort" in health.message
    assert fake.posts() == []

    # Das Passwort nachtragen gilt sofort, auch innerhalb der 20 Sekunden des Zwischenspeichers.
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "qbittorrent-password:qb", "value": PASSWORD}, headers=headers)
    assert res.status_code == 204
    assert _only(await _overview(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_a_password_without_a_user_name_is_a_hint_and_sends_nothing(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(username=None))
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "not_configured" and connection["message"].startswith("Für qb fehlt der Benutzername.")
    assert fake.requests == []


@pytest.mark.asyncio
async def test_the_shared_cookie_store_of_the_http_handle_is_never_used(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    # Zweite Verbindung zum selben Rechner ohne Passwort: Ihre Anfragen dürfen die Sitzung der ersten nicht erben.
    await _configure(client, headers, _connection("mit"), _connection("ohne", username=None), password=None)
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "qbittorrent-password:mit", "value": PASSWORD}, headers=headers)
    assert res.status_code == 204
    http = get_extension_runtime().loaded[EXT].ctx.http
    body = await _overview(client, headers)
    assert [(c["name"], c["state"]) for c in body["connections"]] == [("mit", "ok"), ("ohne", "not_configured")]
    assert any(c.name == "SID" for c in http._client.cookies.jar), "der gemeinsame Speicher kennt die Sitzung durchaus"
    ohne = [r for r in fake.requests if r.method == "GET" and r.headers["cookie"] == "nodvard_deck=1"]
    assert ohne, "die Verbindung ohne Passwort sendet ihren eigenen Platzhalter"
    assert all(fake.issued[0] not in r.headers["cookie"] for r in ohne)
    assert fake.logins == 1


@pytest.mark.asyncio
async def test_the_csrf_check_of_qbittorrent_passes_with_a_port_and_a_base_path(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.prefix = "/qbt"
    await _configure(client, headers, _connection(url="http://qbt.example.com:9090/qbt/"))
    assert _only(await _overview(client, headers))["state"] == "ok"
    assert fake.requests and all(r.url.path.startswith("/qbt/api/v2/") for r in fake.requests)
    assert {(r.headers["referer"], r.headers["origin"]) for r in fake.requests} == {("http://qbt.example.com:9090/qbt", "http://qbt.example.com:9090")}


@pytest.mark.asyncio
async def test_a_request_the_csrf_check_refuses_is_explained_without_counting_as_a_wrong_password(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/api/v2/app/version"] = lambda request: httpx.Response(401, text="Unauthorized")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error"
    assert "weist die Anfrage ab (HTTP 401)" in connection["message"] and "Host-Header-Prüfung" in connection["message"]
    assert fake.failed_logins == 0


@pytest.mark.asyncio
async def test_not_qbittorrent_at_this_address(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides[LOGIN] = lambda request: httpx.Response(404, text="404: Not Found")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error"
    assert connection["message"].startswith("qBittorrent „qb“: Unter dieser Adresse läuft kein qBittorrent, oder der Pfad stimmt nicht.")
    widget = (await _widget(client, headers))["data"]
    assert widget[0]["label"] == "Fehler" and widget[0]["tone"] == "warn"
    assert fake.paths() == [LOGIN]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        (429, "bremst Anfragen gerade (HTTP 429)"),
        (503, "gerade nicht bereit (HTTP 503)"),
        (502, "gerade nicht bereit (HTTP 502)"),
        (500, "meldet einen Fehler (HTTP 500)"),
        (405, "hat die Anfrage abgelehnt (HTTP 405)"),
    ],
)
@pytest.mark.parametrize("where", [LOGIN, "/api/v2/app/version"])
async def test_other_status_codes_have_fixed_sentences(client, db_session, test_settings, status, fragment, where):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides[where] = lambda request: httpx.Response(status, text="Text des Servers: bitte nach evil.example.org gehen")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and fragment in connection["message"]
    assert "evil.example.org" not in connection["message"], "Text des fremden Servers steht nie in der Meldung"


@pytest.mark.asyncio
@pytest.mark.parametrize("where", [LOGIN, "/api/v2/app/version"])
@pytest.mark.parametrize(
    ("location", "sentence"),
    [
        ("https://angreifer.example/login", "leitet auf eine andere Adresse um (HTTP 302)."),
        # Ungültiger Punycode-Name: httpx wirft einen `ValueError`, dessen Text Teile des fremden Namens nennt.
        ("http://xn--angreifer-ey9f.example/login", "leitet auf eine andere Adresse um."),
    ],
)
async def test_redirects_are_reported_never_followed_and_never_show_the_foreign_address(
    client, db_session, test_settings, caplog, location, sentence, where
):
    caplog.set_level(logging.INFO)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides[where] = lambda request: httpx.Response(302, headers={"Location": location})

    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200, res.text
    connection = _only(res.json())
    assert connection["state"] == "error"
    assert connection["message"].startswith(f"qBittorrent „qb“ {sentence}")
    assert "Trage in den Einstellungen die endgültige Adresse ein." in connection["message"]
    widget = await client.get(f"{API}/widgets/status", headers=headers)
    assert widget.status_code == 200 and "leitet auf eine andere Adresse um" in widget.text
    assert "angreifer" not in (res.text + widget.text + caplog.text).lower()
    assert "Traceback" not in caplog.text
    assert all(r.url.host == "qbt.example.com" for r in fake.requests), "der Weiterleitung wird nicht gefolgt"
    _no_secret_in(fake, res.text, caplog.text)


@pytest.mark.asyncio
async def test_a_redirect_in_one_part_is_a_problem_of_that_part_only(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/api/v2/torrents/info"] = lambda request: httpx.Response(307, headers={"Location": "https://angreifer.example/"})
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["active"] is None and connection["transfer"] is not None
    assert "Aktive Torrents: qBittorrent „qb“ leitet auf eine andere Adresse um (HTTP 307)." in connection["problems"][0]
    assert "angreifer" not in json.dumps(connection)
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == 1 and rows[0]["label"] == "unvollständig" and rows[0]["tone"] == "warn"


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
    assert fragment in connection["message"] and "qBittorrent „qb“" in connection["message"]
    assert connection["counts"] is None
    assert fake.paths() == [LOGIN], "ist der Server nicht erreichbar, hilft keine zweite Anfrage"

    rows = (await _widget(client, headers))["data"]
    assert rows == [{"title": "qb: nicht erreichbar", "subtitle": connection["message"], "label": "nicht erreichbar", "tone": "danger"}]
    health = await _health()
    assert health.healthy is False and connection["message"] in health.message


@pytest.mark.asyncio
async def test_the_server_vanishing_after_the_login_is_unreachable(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())

    def gone(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 111] Connection refused")

    fake.overrides.update({p: gone for p in ("/api/v2/app/webapiVersion", "/api/v2/transfer/info", "/api/v2/torrents/info", "/api/v2/sync/maindata")})
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "unreachable" and connection["version"] == "v5.1.4"


@pytest.mark.asyncio
async def test_a_text_that_quotes_the_password_or_the_session_never_reaches_the_user(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.error = httpx.ConnectError(f"boom {PASSWORD} 001AbCdEfGhIjKlMnOpQrStUvWxYz01 boom")
    res = await client.get(f"{API}/overview", headers=headers)
    assert _only(res.json())["state"] == "unreachable"
    health = await _health()
    assert PASSWORD not in res.text + caplog.text + json.dumps(health.model_dump(), default=str)
    assert "001AbCdEf" not in res.text + caplog.text

    fake.error = None
    _service().clear()
    assert _only(await _overview(client, headers))["state"] == "ok"
    fake.error = httpx.ConnectError(f"boom {fake.issued[-1]}")
    res = await client.get(f"{API}/overview?refresh=true", headers=headers)
    _no_secret_in(fake, res.text, caplog.text, json.dumps((await _health()).model_dump(), default=str))


@pytest.mark.asyncio
async def test_answers_that_are_not_qbittorrent(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    service = _service()

    for body in ("<html><body>Anmelden</body></html>", "{}", '{"foo": "bar"}', "Ok. Aber nicht ganz"):
        service.clear()
        fake.overrides[LOGIN] = lambda request, body=body: httpx.Response(200, text=body, headers={"set-cookie": "SID=abcdEFGH1234; path=/"})
        connection = _only(await _overview(client, headers))
        assert connection["state"] == "error" and "nicht das erwartete Format" in connection["message"], body
        assert "Führt die Adresse wirklich zu qBittorrent?" in connection["message"]

    del fake.overrides[LOGIN]
    for body in ("<html>Anmelden</html>", "[1, 2]", '"text"', "", "null"):
        service.clear()
        fake.overrides["/api/v2/app/version"] = lambda request, body=body: httpx.Response(200, text=body)
        connection = _only(await _overview(client, headers))
        assert connection["state"] == "error" and "nicht das erwartete Format" in connection["message"], body
        assert "/api/v2/app/version" in connection["message"]

    del fake.overrides["/api/v2/app/version"]
    service.clear()
    fake.overrides["/api/v2/torrents/info"] = lambda request: httpx.Response(200, content=b"[" * 100000)
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["active"] is None, "absichtlich tief verschachteltes JSON"
    assert "nicht das erwartete Format" in connection["problems"][0] and "/api/v2/torrents/info" in connection["problems"][0]


@pytest.mark.asyncio
async def test_a_login_that_sets_no_session_is_explained(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides[LOGIN] = lambda request: httpx.Response(200, text="Ok.")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and "keine Sitzung gesetzt" in connection["message"]
    assert fake.paths() == [LOGIN]


@pytest.mark.asyncio
async def test_oversized_answers_are_refused_while_streaming(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    sent = 0

    async def endless():
        nonlocal sent
        for _ in range(100):
            sent += 1
            yield b" " * (1024 * 1024)

    fake.overrides["/api/v2/torrents/info"] = lambda request: httpx.Response(200, content=endless())
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["active"] is None
    assert "ungewöhnlich große Antwort (mehr als 4 MB)" in connection["problems"][0]
    assert sent <= 6
    assert connection["counts"] is not None and connection["version"] == "v5.1.4"

    _service().clear()
    fake.overrides["/api/v2/transfer/info"] = lambda request: httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["transfer"] is None and "(mehr als 1 MB)" in connection["problems"][0]

    _service().clear()
    fake.overrides[LOGIN] = lambda request: httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and "(mehr als 1 MB)" in connection["message"]


@pytest.mark.asyncio
async def test_a_library_too_big_for_one_request_leaves_counts_and_free_space_unknown_without_an_error(client, db_session, test_settings):
    assert transport.MAX_SYNC_BYTES == 8 * 1024 * 1024 and qbt_service.TOO_LARGE_PAUSE_S == 600.0
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    service = _service()
    now = [100.0]
    service._clock = lambda: now[0]
    sent = 0

    async def endless():
        nonlocal sent
        for _ in range(100):
            sent += 1
            yield b" " * (1024 * 1024)

    fake.overrides["/api/v2/sync/maindata"] = lambda request: httpx.Response(200, content=endless())
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["problems"] == []
    assert (connection["counts"], connection["free_space"]) == (None, None)
    assert connection["notes"] == [
        "Anzahl je Zustand und freier Platz sind unbekannt: Die Liste aller Torrents ist größer als 8 MB und wird nicht gelesen."
    ]
    assert sent <= 10, "nach dem Limit wird nicht weitergelesen"
    assert connection["transfer"] is not None and len(connection["active"]) == 4
    rows = (await _widget(client, headers))["data"]
    assert rows[0]["label"] == "aktiv" and "frei" not in rows[0]["subtitle"] and "laden" not in rows[0]["subtitle"]

    # Die schwere Abfrage wird danach nicht bei jeder Aktualisierung wiederholt.
    asked = sum(1 for r in fake.requests if r.url.path == "/api/v2/sync/maindata")
    now[0] += qbt_service.CACHE_TTL_S + 1
    again = _only(await _overview(client, headers, "?refresh=true"))
    assert again["notes"] == connection["notes"]
    assert sum(1 for r in fake.requests if r.url.path == "/api/v2/sync/maindata") == asked
    now[0] += qbt_service.TOO_LARGE_PAUSE_S
    fake.overrides.pop("/api/v2/sync/maindata")
    recovered = _only(await _overview(client, headers))
    assert recovered["counts"]["total"] == 14 and recovered["notes"] == []


@pytest.mark.asyncio
async def test_a_big_but_allowed_library_is_read(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    template = next(iter(fake.maindata["torrents"].values()))
    fake.maindata = {**fake.maindata, "torrents": {f"{i:040x}": {**template, "state": "uploading", "name": "x" * 40} for i in range(4000)}}
    size = len(json.dumps(fake.maindata))
    assert 4 * 1024 * 1024 < size < 8 * 1024 * 1024, size
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["notes"] == []
    assert connection["counts"]["total"] == 4000 and connection["counts"]["seeding"] == 4000


@pytest.mark.asyncio
async def test_one_failing_part_does_not_hide_the_others(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/api/v2/sync/maindata"] = lambda request: httpx.Response(500, text="kaputt")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["message"] is None
    assert (connection["counts"], connection["free_space"]) == (None, None)
    assert connection["transfer"]["download_speed"] > 0 and len(connection["active"]) == 4
    assert connection["problems"] == [
        "Anzahl und freier Platz: qBittorrent „qb“ meldet einen Fehler (HTTP 500). Schau in das Protokoll von qBittorrent."
    ]
    rows = (await _widget(client, headers))["data"]
    assert rows[0]["label"] == "unvollständig" and rows[0]["tone"] == "warn"
    assert "meldet einen Fehler (HTTP 500)" in rows[0]["subtitle"]

    fake.overrides["/api/v2/transfer/info"] = lambda request: httpx.Response(200, text="<html>")
    fake.overrides["/api/v2/torrents/info"] = lambda request: httpx.Response(200, json={"x": 1})
    _service().clear()
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error", "wenn nichts zu lesen war, ist es ein Fehler"
    assert connection["version"] == "v5.1.4", "was zu lesen war, bleibt"
    assert (connection["transfer"], connection["active"], connection["counts"], connection["problems"]) == (None, None, None, [])


@pytest.mark.asyncio
async def test_the_web_api_version_is_optional(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/api/v2/app/webapiVersion"] = lambda request: httpx.Response(404, text="Not Found")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["api_version"] is None and connection["problems"] == []


@pytest.mark.asyncio
async def test_a_login_that_stops_working_during_the_queries_is_an_auth_failure(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    original = fake.answer

    def change_password_after_version(request: httpx.Request) -> httpx.Response:
        reply = original(request)
        if request.url.path == "/api/v2/app/version":
            fake.password = "geaendert"
            fake.expire_sessions()
        return reply

    fake.overrides["/api/v2/app/version"] = change_password_after_version
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "auth_failed" and "wurden abgelehnt" in connection["message"]
    assert connection["version"] == "v5.1.4" and connection["transfer"] is None
    assert fake.failed_logins == 1, "vier gleichzeitige Abschnitte zählen nur als ein Fehlversuch"


@pytest.mark.asyncio
async def test_the_active_list_is_cut_to_fifty_and_says_there_are_more(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.active = [_torrent(f"Torrent {i:03d}", dl=1000 - i) for i in range(80)]
    connection = _only(await _overview(client, headers))
    assert len(connection["active"]) == 50 and connection["active_more"] is True
    assert connection["active"][0]["name"] == "Torrent 000" and connection["active"][-1]["name"] == "Torrent 049"

    fake.active = [_torrent(f"Torrent {i:03d}", dl=1000 - i) for i in range(50)]
    _service().clear()
    connection = _only(await _overview(client, headers))
    assert len(connection["active"]) == 50 and connection["active_more"] is False


@pytest.mark.asyncio
async def test_the_active_list_is_ordered_by_speed_and_names_are_cut(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.active = [
        _torrent("langsam", dl=10), _torrent("nur hoch", state="uploading", dl=0, up=5000), _torrent("schnell", dl=4000, up=100),
        _torrent("N" * 200), _torrent("a\nb\x00<img src=x>"),
    ]
    names = [t["name"] for t in _only(await _overview(client, headers))["active"]]
    assert names == ["nur hoch", "schnell", "a b <img src=x>", "N" * 79 + "…", "langsam"]


@pytest.mark.asyncio
async def test_a_limited_connection_and_a_closed_one_show_up_in_the_tile(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.transfer = {**fake.transfer, "dl_rate_limit": 1048576, "connection_status": "firewalled", "dl_info_speed": 0, "up_info_speed": 0}
    fake.active = []
    fake.maindata = {**fake.maindata, "torrents": {}, "server_state": {**fake.maindata["server_state"], "use_alt_speed_limits": True}}
    connection = _only(await _overview(client, headers))
    assert connection["transfer"]["limited"] is True and connection["transfer"]["alt_limits"] is True
    assert connection["transfer"]["download_limit"] == 1048576 and connection["transfer"]["upload_limit"] is None
    assert connection["counts"]["total"] == 0 and connection["active"] == []
    rows = (await _widget(client, headers))["data"]
    assert rows == [{
        "title": "qBittorrent v5.1.4",
        "subtitle": "qb · ↓ 0 B/s · ↑ 0 B/s · keine Torrents · 500 GB frei · Firewall · Tempolimit", "label": "ruhig", "tone": "neutral",
    }]

    fake.transfer = {**fake.transfer, "connection_status": "disconnected"}
    _service().clear()
    rows = (await _widget(client, headers))["data"]
    assert rows[0]["label"] == "getrennt" and rows[0]["tone"] == "danger" and "nicht verbunden" in rows[0]["subtitle"]


@pytest.mark.asyncio
async def test_results_are_cached_for_a_short_time_and_the_password_is_read_only_when_needed(client, db_session, test_settings):
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
    assert len(fake.requests) == 6, "Seite und Kachel fragen qBittorrent nur einmal (Anmeldung und fünf Lesezugriffe)"
    assert reads == ["qbittorrent-password:qb"]

    await _overview(client, headers, "?refresh=true")
    assert len(fake.requests) == 11, "der Knopf Aktualisieren umgeht den Zwischenspeicher, die Sitzung bleibt"
    assert reads == ["qbittorrent-password:qb"] * 2

    now[0] += qbt_service.CACHE_TTL_S - 1
    await _overview(client, headers)
    assert len(fake.requests) == 11
    now[0] += 2.0
    await _overview(client, headers)
    assert len(fake.requests) == 16, "nach 20 Sekunden wird neu gefragt"
    assert fake.logins == 1

    saved = await client.put(
        f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": [_connection(insecure=True)]}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert not saved.json().get("secrets_cleared"), "dieselbe Adresse: das Passwort bleibt"
    assert _only(await _overview(client, headers))["state"] == "ok"
    assert len(fake.requests) == 22, "nach geänderten Einstellungen wird sofort neu gefragt, mit neuer Anmeldung"
    assert fake.logins == 2


@pytest.mark.asyncio
async def test_changing_the_address_removes_the_password_so_it_never_goes_to_a_new_target(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    assert _only(await _overview(client, headers))["state"] == "ok"
    saved = await client.put(
        f"/api/v1/extensions/{EXT}/settings",
        json={"current_password": "correct-horse-battery", "values": {"connections": [_connection(url="http://angreifer.example.com:8080")]}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["secrets_cleared"] == ["qbittorrent-password:qb"]
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "not_configured"
    to_new_target = [r for r in fake.requests if r.url.host == "angreifer.example.com"]
    assert to_new_target and all(r.method == "GET" and not r.content for r in to_new_target), "kein Passwort an das neue Ziel"


@pytest.mark.asyncio
async def test_tls_exception_is_used_only_when_the_connection_asks_for_it(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("streng"), _connection("lax", insecure=True, url="https://lax.example.com"))
    body = await _overview(client, headers)
    assert [c["state"] for c in body["connections"]] == ["ok", "ok"]
    assert {r.url.host for r in fake.insecure_requests} == {"lax.example.com"}
    assert {r.url.host for r in fake.requests} == {"qbt.example.com", "lax.example.com"}
    assert {r.url.host for r in fake.requests if r not in fake.insecure_requests} == {"qbt.example.com"}
    assert any(r.method == "POST" for r in fake.insecure_requests), "auch die Anmeldung geht durch den Zugang mit der Ausnahme"


@pytest.mark.asyncio
async def test_a_fingerprint_goes_into_the_call_instead_of_the_switch(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(url="https://qbt.example.com", pin="ab" * 32, insecure=True))
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok", connection["message"]
    assert fake.pinned_requests[0].url.path == LOGIN and len(fake.pinned_requests) == 6
    assert fake.insecure_requests == [], "mit Fingerabdruck wird nie ungeprüft verbunden, auch wenn der Schalter an ist"
    health = await _health()
    assert health.healthy is True


@pytest.mark.asyncio
async def test_a_changed_certificate_stops_the_connection_before_the_password_is_sent(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(url="https://qbt.example.com", pin=PIN))
    mismatch = CertificateMismatch(PIN, ":".join(["CD"] * 32))
    wrapped = httpx.ConnectError("[SSL] handshake")
    wrapped.__cause__ = mismatch
    fake.pinned_error = wrapped

    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error"
    assert "anderes Zertifikat als das gemerkte" in connection["message"] and "nichts gesendet" in connection["message"]
    assert "CD:CD" not in connection["message"] + caplog.text
    health = await _health()
    assert health.healthy is False and "anderes Zertifikat als das gemerkte" in health.details["qb"]["error"]
    assert fake.requests == [] or all(PASSWORD not in r.content.decode() for r in fake.requests)


@pytest.mark.asyncio
async def test_an_unusable_fingerprint_blocks_the_connection_instead_of_skipping_the_check(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.ctx.settings.set({"connections": [
        {"name": "kaputt", "base_url": "https://qbt.example.com", "username": USER, "tls_fingerprint_sha256": "kein-fingerabdruck", "tls_insecure_skip_verify": True},
        {"name": "http", "base_url": BASE, "username": USER, "tls_fingerprint_sha256": PIN},
    ]})
    for name in ("kaputt", "http"):
        await loaded.ctx.secrets.create(label=f"qbittorrent-password:{name}", kind="generic", value=PASSWORD)
    body = await _overview(client, headers)
    assert [(c["name"], c["state"]) for c in body["connections"]] == [("kaputt", "error"), ("http", "error")]
    assert "Fingerabdruck" in body["connections"][0]["message"] and "nur für https://" in body["connections"][1]["message"]
    assert fake.requests == [] and fake.insecure_requests == []
    health = await _health()
    assert health.healthy is False and fake.requests == []


@pytest.mark.asyncio
async def test_two_connections_are_independent_and_asked_at_the_same_time(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("eins"), _connection("zwei", url="http://zwei.example.com:8080"))
    barrier = asyncio.Barrier(2)
    original = fake.answer

    async def meet(request: httpx.Request) -> httpx.Response:
        # Beide Verbindungen müssen gleichzeitig hier sein, sonst wartet die erste vergeblich auf die zweite.
        await asyncio.wait_for(barrier.wait(), 5)
        return original(request)

    fake.overrides["/api/v2/app/version"] = meet
    body = await _overview(client, headers)
    assert [(c["name"], c["state"]) for c in body["connections"]] == [("eins", "ok"), ("zwei", "ok")]
    assert body["summary"]["active"] == 8 and body["summary"]["connections"] == 2
    assert fake.logins == 2
    # Jede Verbindung hat ihre eigene Sitzung.
    cookies = {r.url.host: r.headers["cookie"] for r in fake.requests if r.url.path == "/api/v2/transfer/info"}
    assert cookies["qbt.example.com"] != cookies["zwei.example.com"]


@pytest.mark.asyncio
async def test_the_four_parts_of_one_connection_are_asked_at_the_same_time(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    barrier = asyncio.Barrier(4)
    original = fake.answer

    async def meet(request: httpx.Request) -> httpx.Response:
        await asyncio.wait_for(barrier.wait(), 5)
        return original(request)

    fake.overrides.update({p: meet for p in ("/api/v2/app/webapiVersion", "/api/v2/transfer/info", "/api/v2/torrents/info", "/api/v2/sync/maindata")})
    assert _only(await _overview(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_a_broken_connection_does_not_hold_up_or_break_the_others(client, db_session, test_settings, monkeypatch):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(
        client, headers, _connection("gut"), _connection("haengt", url="http://haengt.example.com:8080"),
        _connection("abgelehnt", url="http://abgelehnt.example.com:8080"),
    )
    original = fake.answer

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(60)
        return httpx.Response(200)

    def by_host(request: httpx.Request) -> Any:
        if request.url.host == "abgelehnt.example.com":
            return httpx.Response(200, text="Fails.")
        if request.url.host == "haengt.example.com":
            return hang(request)
        return original(request)

    fake.overrides[LOGIN] = by_host
    # Das Modul der geladenen Erweiterung (nicht das beim Import dieser Datei, das ein Test davor aufgeräumt hat).
    monkeypatch.setattr(sys.modules[type(_service()).__module__], "CONNECTION_TIMEOUT_S", 0.3)
    body = await asyncio.wait_for(_overview(client, headers), 10)
    states = {c["name"]: c["state"] for c in body["connections"]}
    assert states == {"gut": "ok", "haengt": "unreachable", "abgelehnt": "auth_failed"}
    assert body["summary"]["connections"] == 3
    rows = (await _widget(client, headers))["data"]
    assert [r["title"] for r in rows[:3]] == ["qBittorrent v5.1.4", "haengt: nicht erreichbar", "abgelehnt: Anmeldung fehlgeschlagen"]


def test_time_limits_are_thirty_seconds_per_request_and_fifty_per_connection():
    from nodvard_deck_ext_qbittorrent import service

    assert (transport.TOTAL_TIMEOUT_S, service.CONNECTION_TIMEOUT_S) == (30.0, 50.0)


@pytest.mark.asyncio
async def test_unusable_entries_become_error_cards_without_a_request(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.ctx.settings.set({"connections": [
        {"name": "ok", "base_url": BASE, "username": USER},
        {"name": "ok", "base_url": BASE, "username": USER},
        {"name": "pw", "base_url": "http://nutzer:" + "passwort@qbt.example.com:8080"},
    ]})
    await loaded.ctx.secrets.create(label="qbittorrent-password:ok", kind="generic", value=PASSWORD)
    body = await _overview(client, headers)
    assert [(c["name"], c["state"]) for c in body["connections"]] == [("ok", "ok"), ("ok", "error"), ("pw", "error")]
    assert "mehrfach vergeben" in body["connections"][1]["message"]
    assert "passwort" not in json.dumps(body)
    assert body["connections"][2]["url"] is None
    assert len(fake.requests) == 6 and {r.url.host for r in fake.requests} == {"qbt.example.com"}, "nur der brauchbare Eintrag fragt an"


@pytest.mark.asyncio
async def test_unexpected_errors_do_not_become_a_500(client, db_session, test_settings, caplog):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    service = _service()

    async def broken(cfg, has_password):
        raise RuntimeError("innerer Fehler mit Details")

    service._load = broken
    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200, res.text
    connection = _only(res.json())
    assert connection["state"] == "error" and connection["message"] == "Bei der Abfrage von qb ist ein unerwarteter Fehler aufgetreten."
    assert "Details" not in res.text
    assert "innerer Fehler" in caplog.text, "im Protokoll steht die Ursache"
    assert (await client.get(f"{API}/widgets/status", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_an_unreadable_or_empty_password_is_a_hint_not_a_crash(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    loaded = get_extension_runtime().loaded[EXT]
    real = loaded.ctx.secrets.get_handle

    async def boom(label):
        raise RuntimeError("Tresor kaputt " + PASSWORD)

    loaded.ctx.secrets.get_handle = boom
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and "aus dem Tresor lesen" in connection["message"] and PASSWORD not in json.dumps(connection)
    assert fake.requests == []
    loaded.ctx.secrets.get_handle = real

    # Ein Zeilenumbruch vom Einfügen gehört nicht zum Passwort, Leerzeichen können dazugehören.
    for stored in (PASSWORD + "\r\n", PASSWORD + "\n"):
        _service().clear()
        await _configure(client, headers, _connection(), password=stored)
        assert _only(await _overview(client, headers))["state"] == "ok", repr(stored)
    fake.password = " mit Rand "
    _service().clear()
    await _configure(client, headers, _connection(), password=" mit Rand ")
    assert _only(await _overview(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_widget_shows_at_most_ten_rows_and_says_how_many_torrents_are_missing(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.active = [_torrent(f"Torrent {i:02d}", dl=1000 - i) for i in range(30)]
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == qbt_widgets.MAX_ROWS and rows[0]["title"] == "qBittorrent v5.1.4"
    assert [r["title"] for r in rows[1:9]] == [f"Torrent {i:02d}" for i in range(8)]
    assert rows[9] == {"title": "… und 22 weitere aktive Torrents", "subtitle": "Alle stehen auf der Seite „qBittorrent“.", "label": "–", "tone": "neutral"}

    # Gibt es mehr, als die Abfrage liefert, ist die Zahl nur eine Untergrenze.
    fake.active = [_torrent(f"Torrent {i:02d}", dl=1000 - i) for i in range(80)]
    _service().clear()
    rows = (await _widget(client, headers))["data"]
    assert rows[9]["title"] == "… und mehr als 42 weitere aktive Torrents"

    # Passen alle genau, gibt es keine Schlusszeile.
    fake.active = [_torrent(f"Torrent {i:02d}") for i in range(9)]
    _service().clear()
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == qbt_widgets.MAX_ROWS and rows[9]["title"] == "Torrent 08"


@pytest.mark.asyncio
async def test_widget_with_several_connections_names_each_torrent_and_stays_within_ten_rows(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("eins"), _connection("zwei"))
    fake.active = [_torrent("Film", dl=2048, up=1024, eta=7300)]
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == 4
    assert rows[0]["title"] == "qBittorrent v5.1.4" and rows[0]["subtitle"].startswith("eins · ")
    assert rows[1]["subtitle"].startswith("zwei · ")
    assert rows[2] == {"title": "Film", "subtitle": "50 % · ↓ 2,0 KB/s · ↑ 1,0 KB/s · noch 2 Std. 1 Min. · 1000 B · eins", "label": "lädt", "tone": "good"}
    assert rows[3]["subtitle"].endswith("· zwei")

    await _configure(client, headers, *[_connection(f"qb{i}") for i in range(7)])
    fake.active = [_torrent(f"T{i}") for i in range(4)]
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == qbt_widgets.MAX_ROWS
    assert [r["subtitle"].split(" · ")[0] for r in rows[:7]] == [f"qb{i}" for i in range(7)]
    assert rows[9]["title"] == "… und 26 weitere aktive Torrents"

    await _configure(client, headers, *[_connection(f"qb{i}") for i in range(11)])
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == qbt_widgets.MAX_ROWS and rows[-1]["title"] == "… und 2 weitere Zeilen"
    assert not any(r["label"] == "lädt" for r in rows), "bei so vielen gibt es nur die Zusammenfassungen"


@pytest.mark.asyncio
async def test_states_are_labelled_in_german_with_a_fixed_tone(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    states = ["stalledDL", "stalledUP", "stoppedDL", "pausedUP", "queuedDL", "checkingUP", "moving", "error"]
    fake.active = [_torrent(f"T{i}", state=state, dl=100 - i) for i, state in enumerate(states)]
    rows = (await _widget(client, headers))["data"]
    assert [(r["label"], r["tone"]) for r in rows[1:]] == [
        ("wartet auf Quellen", "neutral"), ("verteilt", "neutral"), ("pausiert", "neutral"), ("pausiert", "neutral"),
        ("wartet", "neutral"), ("prüft", "neutral"), ("verschiebt", "neutral"), ("Fehler", "danger"),
    ]


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
    from nodvard_deck_ext_qbittorrent.models import Overview

    schema = Overview.model_json_schema()
    assert set(schema["properties"]) == {"connections", "summary"}
    assert set(schema["$defs"]) >= {"Connection", "Torrent", "TorrentCounts", "Transfer", "Summary"}
    assert set(schema["$defs"]["Connection"]["properties"]) == {
        "name", "url", "state", "message", "version", "api_version", "transfer", "active", "active_more", "counts", "free_space",
        "notes", "problems",
    }
    assert set(schema["$defs"]["Transfer"]["properties"]) == {
        "download_speed", "upload_speed", "connection", "download_limit", "upload_limit", "limited", "alt_limits",
    }
    assert set(schema["$defs"]["Torrent"]["properties"]) == {
        "name", "state", "progress_percent", "size", "download_speed", "upload_speed", "eta_seconds",
    }
    assert set(schema["$defs"]["TorrentCounts"]["properties"]) == {"total", "downloading", "seeding", "paused", "error", "other"}
    assert set(schema["$defs"]["Summary"]["properties"]) == {"connections", "download_speed", "upload_speed", "active"}


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
async def test_testing_the_connection_logs_in_and_asks_for_the_version_on_every_connection(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("eins"), _connection("zwei", url="http://zwei.example.com:8080"))
    health = await _health()
    assert health.healthy is True and health.message == "eins: in Ordnung; zwei: in Ordnung"
    assert sorted(fake.paths()) == sorted([LOGIN, LOGIN, "/api/v2/app/version", "/api/v2/app/version"]), "keine Listen, nur Anmeldung und Version"
    assert health.details == {"eins": {"healthy": True}, "zwei": {"healthy": True}}

    # Durch die echte Route „Verbindung testen“.
    tested = await client.post(f"/api/v1/extensions/{EXT}/test", headers=headers)
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True and tested.json()["message"] == "Alle 2 Verbindungen funktionieren."
    _no_secret_in(fake, tested.text)


@pytest.mark.asyncio
async def test_testing_the_connection_names_each_problem_with_a_fixed_sentence(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(
        client, headers, _connection("gut"), _connection("alt", url="http://alt.example.com:8080"),
        _connection("kein-pw", url="http://k.example.com:8080"), _connection("ohne-name", url="http://n.example.com:8080", username=None),
        password=None,
    )
    for name in ("gut", "alt", "ohne-name"):
        res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": f"qbittorrent-password:{name}", "value": PASSWORD}, headers=headers)
        assert res.status_code == 204
    original = fake.answer
    fake.overrides[LOGIN] = lambda request: httpx.Response(200, text="Fails.") if request.url.host == "alt.example.com" else original(request)
    health = await _health()
    assert health.healthy is False
    assert health.details["gut"] == {"healthy": True}
    assert health.details["alt"]["healthy"] is False and "wurden abgelehnt" in health.details["alt"]["error"]
    assert health.details["kein-pw"]["error"].startswith("Für kein-pw fehlt das Passwort, und qBittorrent verlangt eine Anmeldung.")
    assert health.details["ohne-name"]["error"].startswith("Für ohne-name fehlt der Benutzername.")
    assert all(d["verbatim"] is True for d in health.details.values() if not d["healthy"])
    assert [r for r in fake.requests if r.url.host == "n.example.com"] == [], "ohne Benutzernamen keine Anfrage"

    tested = await client.post(f"/api/v1/extensions/{EXT}/test", headers=headers)
    body = tested.json()
    assert body["ok"] is False and body["message"] == "3 von 4 Verbindungen funktionieren nicht – Einzelheiten unten."
    by_name = {d["name"]: d for d in body["details"]}
    assert by_name["gut"]["ok"] is True
    assert by_name["alt"]["message"].startswith("qBittorrent „alt“: Benutzername oder Passwort wurden abgelehnt.")
    _no_secret_in(fake, tested.text)


@pytest.mark.asyncio
async def test_testing_the_connection_survives_surprises(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/api/v2/app/version"] = lambda request: httpx.Response(200, text="<html>Anmelden</html>")
    health = await _health()
    assert health.healthy is False and "nicht das erwartete Format" in health.details["qb"]["error"]

    service = _service()

    async def broken(cfg):
        raise RuntimeError("innerer Fehler " + PASSWORD)

    service._test_one = broken
    health = await _health()
    assert health.healthy is False
    assert PASSWORD not in health.message


@pytest.mark.asyncio
async def test_each_connection_is_suggested_as_an_app_without_any_request(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    assert (await client.get("/api/v1/apps/suggestions", headers=headers)).json()["suggestions"] == []

    await _configure(client, headers, _connection(url="http://203.0.113.20:8080"), password=None)
    res = await client.get("/api/v1/apps/suggestions", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json() == {
        "suggestions": [{"name": "qBittorrent", "url": "http://203.0.113.20:8080", "source": "qBittorrent", "host": None, "service_id": None}],
        "errors": [],
    }

    await _configure(client, headers, _connection("nas", url="http://203.0.113.20:8080"), _connection("seedbox", url="https://seedbox.example.com"), password=None)
    suggestions = (await client.get("/api/v1/apps/suggestions", headers=headers)).json()["suggestions"]
    assert [(s["name"], s["url"]) for s in suggestions] == [
        ("qBittorrent nas", "http://203.0.113.20:8080"), ("qBittorrent seedbox", "https://seedbox.example.com"),
    ]
    assert fake.requests == [], "ein Vorschlag liest weder das Passwort noch ruft er qBittorrent auf"


@pytest.mark.asyncio
async def test_unusable_entries_are_not_suggested(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.ctx.settings.set({"connections": [
        {"name": "ok", "base_url": BASE},
        {"name": "pw", "base_url": "http://nutzer:" + "passwort@qbt.example.com:8080"},
        {"name": "leer", "base_url": ""},
    ]})
    suggestions = (await client.get("/api/v1/apps/suggestions", headers=headers)).json()["suggestions"]
    assert [(s["name"], s["url"]) for s in suggestions] == [("qBittorrent", BASE)]
    assert "passwort" not in json.dumps(suggestions)


@pytest.mark.asyncio
async def test_stopping_the_extension_logs_every_session_out_once_and_never_fails(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.instance.on_stop(loaded.ctx)
    assert fake.requests == [], "ohne Sitzung gibt es nichts abzumelden"

    await _configure(client, headers, _connection("eins"), _connection("zwei", url="http://zwei.example.com:8080"))
    await _overview(client, headers)
    sessions = list(fake.sessions)
    assert len(sessions) == 2
    fake.requests.clear()
    await loaded.instance.on_stop(loaded.ctx)
    assert sorted((r.method, r.url.path) for r in fake.requests) == [("POST", LOGOUT), ("POST", LOGOUT)]
    assert {r.headers["cookie"] for r in fake.requests} == {f"SID={sid}" for sid in sessions}
    assert fake.sessions == set()
    fake.requests.clear()
    await loaded.instance.on_stop(loaded.ctx)
    assert fake.requests == [], "jede Sitzung wird nur einmal abgemeldet"

    await _overview(client, headers, "?refresh=true")
    fake.error = httpx.ConnectError("[Errno 111] Connection refused")
    await loaded.instance.on_stop(loaded.ctx)


@pytest.mark.asyncio
async def test_logging_out_gives_up_quickly_and_sends_nothing_without_a_session():
    box = qbt_client.SessionBox()
    seen: list[httpx.Request] = []

    async def hang(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        await asyncio.sleep(60)
        return httpx.Response(200)

    await qbt_client.logout(_Http(hang), box, timeout_s=0.05)
    assert seen == []
    box.cookie, box.target = ("SID", "abcdEFGH1234"), qbt_client.Target("qBittorrent „q“", BASE, False, None)
    await asyncio.wait_for(qbt_client.logout(_Http(hang), box, timeout_s=0.05), 5)
    assert [(r.method, r.url.path, r.headers["cookie"]) for r in seen] == [("POST", LOGOUT, "SID=abcdEFGH1234")]
    assert box.cookie is None


def test_only_get_requests_and_the_two_fixed_login_paths_exist():
    source = REPO_EXTENSIONS_DIR / "qbittorrent" / "src" / "nodvard_deck_ext_qbittorrent"
    text = "".join(p.read_text(encoding="utf-8") for p in source.glob("*.py"))
    assert not re.search(r"\.(post|put|patch|delete|request)\(", text)
    assert not re.search(r"""["'](PUT|PATCH|DELETE)["']""", text)
    assert "POST" not in (source / "transport.py").read_text(encoding="utf-8") and '"GET"' not in (source / "transport.py").read_text(encoding="utf-8")
    posts = [line for line in text.splitlines() if '"POST"' in line]
    assert len(posts) == 3 and all(("POST_PATHS" in line or "LOGIN_PATH" in line or "LOGOUT_PATH" in line) for line in posts), posts
    assert qbt_client.POST_PATHS == {"/api/v2/auth/login", "/api/v2/auth/logout"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), [("POST", "/api/v2/torrents/stop"), ("POST", "/api/v2/torrents/delete"), ("PUT", LOGIN), ("DELETE", LOGIN), ("GET ", LOGIN)])
async def test_the_client_refuses_to_send_anything_else(method, path):
    http = _Http(lambda request: httpx.Response(200))
    client = qbt_client.QbittorrentClient(http, name="q", base_url=BASE, username=USER, password=PASSWORD, box=qbt_client.SessionBox())
    with pytest.raises(ValueError):
        await client._send(method, path, cookie=None, max_bytes=10)
    assert http.calls == []


@pytest.mark.asyncio
async def test_everything_that_was_sent_was_a_read_or_one_of_the_fixed_login_paths(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("eins"), _connection("zwei", url="http://zwei.example.com:8080"))
    await _overview(client, headers)
    await _widget(client, headers)
    await _health()
    fake.expire_sessions()
    await _overview(client, headers, "?refresh=true")
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.instance.on_stop(loaded.ctx)
    sent = {(r.method, r.url.path) for r in fake.requests}
    assert {m for m, _ in sent} == {"GET", "POST"}
    assert {p for m, p in sent if m == "POST"} == {LOGIN, LOGOUT}
    assert all(r.url.path.startswith("/api/v2/") for r in fake.requests)
    assert all(not r.content for r in fake.requests if r.method == "GET")
