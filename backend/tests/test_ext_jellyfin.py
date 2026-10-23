"""Tests für die Jellyfin-Erweiterung; Jellyfin ist ein `httpx.MockTransport`."""

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
sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "jellyfin" / "src"))

from nodvard_deck_ext_jellyfin import parse, transport
from nodvard_deck_ext_jellyfin.client import LATEST_QUERY
from nodvard_deck_ext_jellyfin.config import (
    normalize_base_url,
    normalize_fingerprint,
    read_connections,
)
from nodvard_deck_ext_jellyfin.models import Media
from nodvard_deck_ext_jellyfin.service import AUTH_FAILED_TTL_S, CACHE_TTL_S
from nodvard_deck_ext_jellyfin.widgets import (
    MAX_ROWS,
    episode_code,
    media_title,
    number_text,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jellyfin"
FIXTURE_FILES = ("system_info.json", "sessions.json", "item_counts.json", "latest_items.json")
BASE = "http://jellyfin.example.com:8096"
KEY = "7c1f93e0a2b84d5c9e6f10a3b5d7c2e4"
PIN = ":".join(["AB"] * 32)
EXT = "jellyfin"
API = "/api/v1/ext/jellyfin"
AUTH_HEADER = f'MediaBrowser Token="{KEY}"'


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


def _session(user: str, title: str, **playing: Any) -> dict[str, Any]:
    return {
        "UserName": user, "Client": "Jellyfin Web", "DeviceName": f"Gerät {user}",
        "NowPlayingItem": {"Name": title, "Type": "Movie", "RunTimeTicks": 1000},
        "PlayState": {"PositionTicks": 500, "IsPaused": False, "PlayMethod": "DirectPlay", **playing},
    }


def test_fixtures_hold_no_private_addresses():
    text = "".join((FIXTURES / name).read_text(encoding="utf-8") for name in FIXTURE_FILES)
    for address in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
        assert address.startswith("203.0.113."), address
    for host in re.findall(r"https?://([^/\s\"':]+)", text):
        assert host in ("203.0.113.20", "example.com") or host.endswith(".example.com"), host
    assert KEY not in text


def test_system_info_fixture_gives_name_and_version():
    assert parse.parse_system_info(_json("system_info.json")) == parse.ServerInfo(name="Heimkino", version="10.10.7")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"ServerName": "Keller", "Version": "10.11.0-rc1"}, parse.ServerInfo("Keller", "10.11.0-rc1")),
        ({"Id": "abc"}, parse.ServerInfo(None, None)),
        ({"ServerName": 5, "Version": ["10"], "Id": "abc"}, parse.ServerInfo(None, None)),
        ({"Version": "<script>alert(1)</script>"}, parse.ServerInfo(None, None)),
        ({"ServerName": "N" * 200}, parse.ServerInfo("N" * 79 + "…", None)),
        ({"Version": "9" * 80}, parse.ServerInfo(None, None)),
        ({"foo": "bar"}, None),
        ({}, None),
        ([], None),
        ("Jellyfin", None),
        (None, None),
    ],
)
def test_system_info_is_read_defensively(body, expected):
    assert parse.parse_system_info(body) == expected


def test_sessions_fixture_lists_only_running_playbacks_with_who_what_and_how():
    playbacks = parse.parse_sessions(_json("sessions.json"))
    assert playbacks is not None
    assert [p.model_dump() for p in playbacks] == [
        {
            "title": "Der Beispielfilm", "kind": "movie", "series": None, "season": None, "episode": None, "artist": None,
            "year": 2019, "user": "anna", "device": "Firefox", "client": "Jellyfin Web", "play_method": "direct_play",
            "transcoding": False, "progress_percent": 25, "paused": False,
        },
        {
            "title": "Der Besuch", "kind": "episode", "series": "Die Beispielserie", "season": 2, "episode": 5, "artist": None,
            "year": None, "user": "ben", "device": "Wohnzimmer-TV", "client": "Jellyfin Android TV", "play_method": "transcode",
            "transcoding": True, "progress_percent": 33, "paused": True,
        },
    ]


@pytest.mark.parametrize(
    ("play_state", "info", "expected"),
    [
        ({"PlayMethod": "Transcode"}, None, "transcode"),
        ({"PlayMethod": "DirectStream"}, None, "direct_stream"),
        ({"PlayMethod": "DirectPlay"}, None, "direct_play"),
        ({"PlayMethod": "DirectPlay"}, {"IsVideoDirect": False, "IsAudioDirect": False}, "direct_play"),
        ({"PlayMethod": "DirectStream"}, {"IsVideoDirect": True, "IsAudioDirect": False}, "transcode"),
        ({"PlayMethod": "DirectStream"}, {"IsVideoDirect": True, "IsAudioDirect": True}, "direct_stream"),
        ({}, {"IsVideoDirect": False, "IsAudioDirect": True}, "transcode"),
        ({}, {"IsVideoDirect": True, "IsAudioDirect": True}, "direct_stream"),
        ({}, {}, "transcode"),
        ({}, None, None),
        ({"PlayMethod": "Unbekannt"}, None, None),
        ({"PlayMethod": 0}, "kein Objekt", None),
    ],
)
def test_transcoding_is_decided_from_the_play_method_and_the_transcoding_info(play_state, info, expected):
    session = {"UserName": "u", "NowPlayingItem": {"Name": "T"}, "PlayState": play_state}
    if info is not None:
        session["TranscodingInfo"] = info
    playbacks = parse.parse_sessions([session])
    assert playbacks is not None
    assert (playbacks[0].play_method, playbacks[0].transcoding) == (expected, expected == "transcode")


@pytest.mark.parametrize(
    ("position", "total", "expected"),
    [(500, 1000, 50), (0, 1000, 0), (1000, 1000, 100), (1500, 1000, 100), (999, 1000, 99), (5.0, 10.0, 50), (-1, 1000, None),
     (500, 0, None), (500, None, None), (None, 1000, None), (True, 1000, None), ("500", 1000, None), (float("nan"), 10, None)],
)
def test_progress_is_a_percentage_or_empty(position, total, expected):
    body = [{"NowPlayingItem": {"Name": "T", "RunTimeTicks": total}, "PlayState": {"PositionTicks": position}}]
    playbacks = parse.parse_sessions(body)
    assert playbacks is not None and playbacks[0].progress_percent == expected


def test_playbacks_are_sorted_and_music_shows_the_artist():
    body = [
        _session("zoe", "b-Titel"),
        _session("Anna", "Z-Titel"),
        _session("anna", "a-Titel"),
        {"UserName": "dj", "NowPlayingItem": {"Name": "Lied", "Type": "Audio", "Artists": ["Band A", "Band B"], "AlbumArtist": "Sampler", "IndexNumber": 7, "ParentIndexNumber": 1}},
        {"UserName": "dj2", "NowPlayingItem": {"Name": "Lied 2", "Type": "Audio", "Artists": [], "AlbumArtist": "Sampler"}},
        {"UserName": "ohne"},
        {"UserName": "ohne2", "NowPlayingItem": None},
        {"UserName": "ohne3", "NowPlayingItem": "kaputt"},
    ]
    playbacks = parse.parse_sessions(body)
    assert playbacks is not None
    assert [(p.user, p.title) for p in playbacks] == [
        ("anna", "a-Titel"), ("Anna", "Z-Titel"), ("dj", "Lied"), ("dj2", "Lied 2"), ("zoe", "b-Titel"),
    ]
    dj, dj2 = playbacks[2], playbacks[3]
    assert (dj.artist, dj.season, dj.episode) == ("Band A", None, None), "bei Musik sind Staffel und Folge keine Spurnummer"
    assert dj2.artist == "Sampler"


def test_broken_session_fields_never_crash():
    long_name = "N" * 200
    body: list[Any] = [
        "kein Objekt", 7, None, [], {},
        {"NowPlayingItem": {}, "PlayState": "kaputt", "TranscodingInfo": 5},
        {"NowPlayingItem": {"Name": long_name, "Type": ["Episode"], "RunTimeTicks": "viel"}, "UserName": 5, "DeviceName": {"x": 1}, "Client": None},
        {"NowPlayingItem": {"Name": "Zeile 1\nZeile 2\x00\x07\u202e<b>x</b>", "Type": "Episode", "SeriesName": 7, "ParentIndexNumber": "2", "IndexNumber": -4}},
        {"NowPlayingItem": {"Name": "Jahr", "Type": "Movie", "ProductionYear": 99999999}},
    ]
    playbacks = parse.parse_sessions(body)
    assert playbacks is not None and len(playbacks) == 4
    by_title = {p.title: p for p in playbacks}
    assert parse.UNKNOWN_TITLE in by_title
    empty = by_title[parse.UNKNOWN_TITLE]
    assert (empty.user, empty.device, empty.kind, empty.play_method, empty.progress_percent, empty.paused) == (None, None, "other", None, None, False)
    assert len(by_title["N" * 79 + "…"].title) == 80
    steuer = by_title["Zeile 1 Zeile 2 <b>x</b>"]
    assert (steuer.kind, steuer.series, steuer.season, steuer.episode) == ("episode", None, None, None)
    assert by_title["Jahr"].year is None


@pytest.mark.parametrize("body", [{}, "text", 5, None, {"Sessions": []}])
def test_sessions_that_are_not_a_list_are_refused(body):
    assert parse.parse_sessions(body) is None


def test_counts_fixture_and_odd_values():
    counts = parse.parse_counts(_json("item_counts.json"))
    assert counts is not None
    assert counts.model_dump() == {
        "movies": 482, "series": 37, "episodes": 1296, "albums": 318, "songs": 4210, "music_videos": 0, "box_sets": 12, "books": 0,
    }
    odd = parse.parse_counts({"MovieCount": "viele", "SeriesCount": -1, "EpisodeCount": True, "AlbumCount": 3.0, "SongCount": None})
    assert odd is not None
    assert (odd.movies, odd.series, odd.episodes, odd.albums, odd.songs) == (None, None, None, 3, None)
    assert parse.parse_counts({}) is not None
    for body in ([], "x", None, 3):
        assert parse.parse_counts(body) is None


def test_latest_fixture_keeps_the_order_and_reads_dates():
    latest = parse.parse_latest(_json("latest_items.json"))
    assert latest is not None
    assert [(i.kind, i.series, i.season, i.episode, i.title, i.year) for i in latest] == [
        ("movie", None, None, None, "Der zweite Beispielfilm", 2024),
        ("episode", "Die Beispielserie", 2, 5, "Der Besuch", None),
        ("episode", "Die Beispielserie", 2, 4, "Die Abreise", None),
        ("episode", "Eine andere Serie", 1, 1, "Pilot", None),
        ("movie", None, None, None, "Ein alter Beispielfilm", 1987),
    ]
    assert latest[0].added_at is not None and latest[0].added_at.isoformat() == "2026-10-11T18:22:31+00:00"


def test_latest_dates_and_broken_entries():
    latest = parse.parse_latest({"Items": [
        {"Name": "A", "DateCreated": "kein Datum"}, {"Name": "B", "DateCreated": 5}, {"Name": "C", "DateCreated": "2026-10-01T10:00:00"},
        {"Name": "D", "DateCreated": "2026-10-01T10:00:00.1234567+02:00"}, "kaputt", None, 3, {},
    ]})
    assert latest is not None
    assert [i.title for i in latest] == ["A", "B", "C", "D", parse.UNKNOWN_TITLE]
    assert [i.added_at is not None for i in latest] == [False, False, True, True, False]
    assert latest[2].added_at is not None and latest[2].added_at.utcoffset() is not None, "ohne Zone gilt UTC"


@pytest.mark.parametrize("body", [{}, {"Items": "x"}, {"Items": None}, [], "x", None])
def test_latest_without_an_items_list_is_refused(body):
    assert parse.parse_latest(body) is None


def test_titles_for_the_tile():
    def media(**over: Any) -> Media:
        base: dict[str, Any] = {"title": "T", "kind": "movie", "series": None, "season": None, "episode": None, "artist": None, "year": None}
        return Media(**{**base, **over})

    assert media_title(media()) == "T"
    assert media_title(media(year=2019)) == "T (2019)"
    assert media_title(media(kind="episode", series="Serie", season=1, episode=3)) == "Serie S01E03 · T"
    assert media_title(media(kind="episode", series="Serie")) == "Serie · T"
    assert media_title(media(kind="episode", season=2)) == "S02 · T"
    assert media_title(media(kind="episode", episode=9)) == "E09 · T"
    assert media_title(media(kind="audio", artist="Band")) == "Band – T"
    assert media_title(media(kind="audio")) == "T"
    assert (episode_code(None, None), episode_code(12, 104)) == ("", "S12E104")
    assert [number_text(n) for n in (0, 999, 1000, 1296, 1234567)] == ["0", "999", "1.000", "1.296", "1.234.567"]


def test_base_url_is_normalized_and_credentials_are_refused():
    assert normalize_base_url("http://jellyfin.example.com:8096/") == BASE
    assert normalize_base_url(" https://jellyfin.example.com/jellyfin/ ") == "https://jellyfin.example.com/jellyfin"
    assert normalize_base_url("http://203.0.113.20:8096/?x=1#y") == "http://203.0.113.20:8096"
    assert normalize_base_url("http://203.0.113.20:8096/web/index.html#!/home.html") == "http://203.0.113.20:8096"
    assert normalize_base_url("https://jellyfin.example.com/jellyfin/web/") == "https://jellyfin.example.com/jellyfin"
    assert normalize_base_url("http://jellyfin.test/jellyfin") == "http://jellyfin.test/jellyfin"
    for bad in ("jellyfin.example.com:8096", "ftp://jellyfin.example.com", "http://", "http://user:" + "pw@jellyfin.example.com", "http://@jellyfin.example.com", "http://jellyfin.example.com:999999", ""):
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
        {"name": "a", "base_url": "http://a.example.com:8096/", "tls_insecure_skip_verify": True},
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
    assert (first.base_url, first.insecure_tls, first.pin, first.problem) == ("http://a.example.com:8096", True, None, None)
    assert "mehrfach vergeben" in (configs[1].problem or "")
    assert "ungültig" in (configs[2].problem or "") and "pw" not in (configs[2].problem or "") and configs[2].base_url is None
    assert "Fingerabdruck" in (configs[3].problem or "") and "ungültig" in (configs[3].problem or "")
    assert "nur für https://" in (configs[4].problem or "")
    assert (configs[5].pin, configs[5].problem) == (PIN, None)
    assert "Kurzname" in (configs[6].problem or "")
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
    with pytest.raises(transport.JellyfinError) as caught:
        await transport.get(http, f"{BASE}/Sessions", service="Jellyfin „jf“", max_bytes=transport.MAX_LIST_BYTES)
    assert caught.value.state == "error"
    assert "ungewöhnlich große Antwort (mehr als 4 MB)" in str(caught.value)
    assert sent <= 6, "nach dem Limit wird nicht weitergelesen"
    assert (transport.MAX_LIST_BYTES, transport.MAX_SMALL_BYTES, transport.TOTAL_TIMEOUT_S) == (4 * 1024 * 1024, 1024 * 1024, 30.0)


@pytest.mark.asyncio
async def test_transport_gives_up_after_the_total_time_limit():
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    with pytest.raises(transport.Unreachable) as caught:
        await transport.get(_Http(slow), f"{BASE}/System/Info", service="Jellyfin", total_timeout_s=0.05)
    assert "antwortet nicht (Zeitüberschreitung)" in str(caught.value)


@pytest.mark.asyncio
async def test_transport_passes_the_tls_choice_and_returns_other_status_codes():
    http = _Http(lambda request: httpx.Response(503, content=b"nope"))
    reply = await transport.get(http, "https://jellyfin.example.com/System/Info", service="x", insecure_tls=True)
    assert (reply.status_code, reply.body) == (503, b"nope")
    assert http.calls[0]["insecure_tls"] is True and http.calls[0]["pinned_sha256"] is None

    # Mit Fingerabdruck geht nur er hinaus, nie zusammen mit dem Schalter (ctx.http lehnt beides zusammen ab).
    pinned = _Http(lambda request: httpx.Response(200, content=b"{}"))
    await transport.get(pinned, "https://jellyfin.example.com/System/Info", service="x", insecure_tls=True, pin=PIN)
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
        (httpx.LocalProtocolError("Illegal header value b'MediaBrowser Token=xyz'"), "error", "fehlerhafte Antwort"),
        (httpx.DecodingError("Error -3 while decompressing data: Geheim"), "error", "fehlerhafte Antwort"),
        (ValueError("irgendwas mit Geheim"), "error", "ließ sich nicht senden"),
    ],
)
async def test_transport_error_sentences(error, state, fragment):
    def boom(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(transport.JellyfinError) as caught:
        await transport.get(_Http(boom), f"{BASE}/System/Info", service="Jellyfin „jf“")
    assert caught.value.state == state
    assert fragment in str(caught.value)
    for secret in ("X-Geheim", "xyz", "Geheim", "self-signed"):
        assert secret not in str(caught.value), "der Text der Ausnahme wird nicht zitiert"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.asyncio
async def test_transport_says_when_the_core_refuses_the_target():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise PermissionDenied("jellyfin", "net.outbound")

    with pytest.raises(transport.JellyfinError) as caught:
        await transport.get(_Http(refuse), f"{BASE}/System/Info", service="Jellyfin „jf“")
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

    with pytest.raises(transport.JellyfinError) as caught:
        await transport.get(_Http(boom), "https://jellyfin.example.com/System/Info", service="Jellyfin „jf“", pin=PIN)
    text = str(caught.value)
    assert "anderes Zertifikat als das gemerkte" in text and "nicht der API-Schlüssel" in text
    assert "CD:CD" not in text and "AB:AB" not in text, "keine Fingerabdrücke im Satz"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


class FakeJellyfin:
    """Jellyfin mit den vier Aufrufen, die das Modul braucht; Anmeldung per `MediaBrowser Token="..."`."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.insecure_requests: list[httpx.Request] = []
        self.pinned_requests: list[httpx.Request] = []
        self.pinned_error: Exception | None = None
        self.key = KEY
        self.info = _json("system_info.json")
        self.sessions = _json("sessions.json")
        self.counts = _json("item_counts.json")
        self.latest = _json("latest_items.json")
        self.overrides: dict[str, Callable[[httpx.Request], Any]] = {}
        self.error: Exception | None = None

    def handler(self, request: httpx.Request) -> Any:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        path = request.url.path
        if path in self.overrides:
            return self.overrides[path](request)
        if request.headers.get("Authorization") != f'MediaBrowser Token="{self.key}"':
            return httpx.Response(401, text="Unauthorized")
        answers = {"/System/Info": self.info, "/Sessions": self.sessions, "/Items/Counts": self.counts, "/Items": self.latest}
        if path in answers:
            return httpx.Response(200, json=answers[path])
        return httpx.Response(404, text="Not Found")

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


async def _enable(client, db_session, test_settings) -> tuple[dict, FakeJellyfin]:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    headers = await _login(client)
    enabled = await client.post(f"/api/v1/extensions/{EXT}/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text

    fake = FakeJellyfin()
    http = get_extension_runtime().loaded[EXT].ctx.http
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    http._insecure_client = httpx.AsyncClient(transport=httpx.MockTransport(fake.insecure_handler))
    # Ein Client mit gemerktem Fingerabdruck wird je Fingerabdruck gebaut; hier steht der Nachbau schon bereit.
    http._pinned_clients[bytes.fromhex(PIN.replace(":", ""))] = httpx.AsyncClient(transport=httpx.MockTransport(fake.pinned_handler))
    return headers, fake


def _connection(name="jf", *, insecure=False, url=BASE, pin: str | None = None) -> dict:
    entry: dict[str, Any] = {"name": name, "base_url": url}
    if insecure:
        entry["tls_insecure_skip_verify"] = True
    if pin:
        entry["tls_fingerprint_sha256"] = pin
    return entry


async def _configure(client, headers, *connections: dict, key: str | None = KEY) -> None:
    saved = await client.put(f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": list(connections)}}, headers=headers)
    assert saved.status_code == 200, saved.text
    if key is not None:
        for connection in connections:
            res = await client.put(
                f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": f"jellyfin-key:{connection['name']}", "value": key}, headers=headers,
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
    assert (row["name"], row["stage"], row["category"]) == ("Jellyfin", "beta", "servers")

    pages = (await client.get("/api/v1/pages", headers=headers)).json()
    page = next(p for p in pages if p["ext_id"] == EXT)
    assert (page["path"], page["component"], page["title"], page["stage"]) == ("/jellyfin", "JellyfinPage", "Jellyfin", "beta")
    widgets = [w for w in (await client.get("/api/v1/widgets", headers=headers)).json() if w["ext_id"] == EXT]
    assert [w["id"] for w in widgets] == ["status"]
    assert widgets[0]["view"]["item"]["badge"] == {"text": "{{ label }}", "tone": "{{ tone }}"}
    assert widgets[0]["data_endpoint"] == "widgets/status"

    bundle = await client.get(f"/api/v1/extensions/{EXT}/frontend/index.js")
    assert bundle.status_code == 200
    assert "JellyfinPage" in bundle.text


@pytest.mark.asyncio
async def test_manifest_asks_for_reading_only(client, db_session, test_settings):
    import tomllib

    manifest = tomllib.loads((REPO_EXTENSIONS_DIR / EXT / "extension.toml").read_text(encoding="utf-8"))
    assert manifest["extension"]["stage"] == "beta"
    assert manifest["extension"]["permissions"] == ["net.outbound", "net.outbound.insecure_tls", "secrets.read:jellyfin-*"]
    await _enable(client, db_session, test_settings)
    registered = get_extension_runtime().actions._specs
    assert [t for t, (ext_id, _spec) in registered.items() if ext_id == EXT] == [], "keine Aktion, nichts zum Freigeben"


@pytest.mark.asyncio
async def test_nothing_configured_is_reported_cleanly(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    assert await _overview(client, headers) == {"connections": [], "summary": {"connections": 0, "playing": 0, "transcoding": 0}}
    widget = await _widget(client, headers)
    assert widget["data"] == [{
        "title": "Jellyfin nicht eingerichtet", "subtitle": "Unter Einstellungen → Erweiterungen → Jellyfin eintragen.",
        "label": "nicht eingerichtet", "tone": "neutral",
    }]
    health = await _health()
    assert health.healthy is False and "noch nicht eingerichtet" in health.message
    assert fake.requests == [], "ohne Einstellungen wird nichts angefragt"


@pytest.mark.asyncio
async def test_connection_without_key_is_not_configured(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), key=None)

    connection = _only(await _overview(client, headers))
    assert connection["state"] == "not_configured"
    assert connection["message"] == "Für jf fehlt der API-Schlüssel. Unter Einstellungen → Erweiterungen → Jellyfin eintragen."
    assert (connection["playbacks"], connection["playback_count"], connection["counts"], connection["latest"]) == (None, None, None, None)
    rows = (await _widget(client, headers))["data"]
    assert rows == [{"title": "jf: nicht eingerichtet", "subtitle": connection["message"], "label": "nicht eingerichtet", "tone": "neutral"}]
    assert fake.requests == []

    # Den Schlüssel nachtragen: gilt sofort, auch innerhalb der 20 Sekunden des Zwischenspeichers.
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "jellyfin-key:jf", "value": KEY}, headers=headers)
    assert res.status_code == 204
    assert _only(await _overview(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_widget_meta_says_not_configured_only_without_any_set_up_connection(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    # Nichts eingetragen: die Übersicht darf die Kachel ausblenden; die übrigen `meta`-Felder bleiben.
    assert (await _widget(client, headers))["meta"] == {"connections": 0, "playing": 0, "transcoding": 0, "state": "not_configured"}

    # Eine Verbindung ohne Schlüssel ist ebenfalls nicht eingerichtet; die Textzeile bleibt für ältere Oberflächen.
    await _configure(client, headers, _connection(), key=None)
    widget = await _widget(client, headers)
    assert widget["meta"]["state"] == "not_configured" and widget["meta"]["connections"] == 1
    assert widget["data"][0]["label"] == "nicht eingerichtet"

    # Mit Schlüssel gilt die Verbindung als eingerichtet, auch wenn der Server nicht antwortet: Fehler bleiben sichtbar.
    await _configure(client, headers, _connection())
    fake.error = httpx.ConnectError("refused")
    widget = await _widget(client, headers)
    assert "state" not in widget["meta"] and widget["meta"]["connections"] == 1
    assert _only(await _overview(client, headers))["state"] == "unreachable"

    # Eine eingerichtete und eine nicht eingerichtete Verbindung: das Modul ist eingerichtet.
    await _configure(client, headers, _connection(), _connection("zwei"), key=None)
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "jellyfin-key:jf", "value": KEY}, headers=headers)
    assert res.status_code == 204
    body = await _overview(client, headers)
    assert [c["state"] for c in body["connections"]] == ["unreachable", "not_configured"]
    widget = await _widget(client, headers)
    assert "state" not in widget["meta"] and widget["meta"]["connections"] == 2
    assert [row["label"] for row in widget["data"]] == ["nicht erreichbar", "nicht eingerichtet"]

@pytest.mark.asyncio
async def test_full_overview_shows_server_playbacks_libraries_and_new_media(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())

    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    connection = _only(body)
    assert (connection["name"], connection["url"], connection["state"], connection["message"]) == ("jf", BASE, "ok", None)
    assert (connection["server_name"], connection["version"], connection["problems"]) == ("Heimkino", "10.10.7", [])
    assert connection["playback_count"] == 2
    assert [(p["user"], p["title"], p["transcoding"], p["paused"], p["progress_percent"]) for p in connection["playbacks"]] == [
        ("anna", "Der Beispielfilm", False, False, 25), ("ben", "Der Besuch", True, True, 33),
    ]
    assert connection["playbacks"][1]["series"] == "Die Beispielserie" and (connection["playbacks"][1]["season"], connection["playbacks"][1]["episode"]) == (2, 5)
    assert connection["counts"] == {"movies": 482, "series": 37, "episodes": 1296, "albums": 318, "songs": 4210, "music_videos": 0, "box_sets": 12, "books": 0}
    assert [i["title"] for i in connection["latest"]] == ["Der zweite Beispielfilm", "Der Besuch", "Die Abreise", "Pilot", "Ein alter Beispielfilm"]
    assert connection["latest"][0]["added_at"] == "2026-10-11T18:22:31Z"
    assert body["summary"] == {"connections": 1, "playing": 2, "transcoding": 1}

    # Nur lesen, mit dem Schlüssel im Header; `/System/Info` zuerst (wird der Schlüssel abgelehnt, bleibt es dabei).
    assert fake.paths()[0] == "/System/Info" and sorted(fake.paths()) == ["/Items", "/Items/Counts", "/Sessions", "/System/Info"]
    assert {r.method for r in fake.requests} == {"GET"}
    assert all(r.headers["Authorization"] == AUTH_HEADER and r.headers["Accept"] == "application/json" for r in fake.requests)
    assert not any(name.lower().startswith(("x-emby", "x-mediabrowser")) for r in fake.requests for name in r.headers)
    assert all(KEY not in str(r.url) and not r.content for r in fake.requests)

    widget = await _widget(client, headers)
    assert widget["data"] == [
        {"title": "Der Beispielfilm (2019)", "subtitle": "anna · Firefox · 25 %", "label": "läuft", "tone": "good"},
        {
            "title": "Die Beispielserie S02E05 · Der Besuch", "subtitle": "ben · Wohnzimmer-TV · 33 % · transkodiert",
            "label": "pausiert", "tone": "neutral",
        },
        {
            "title": "Jellyfin 10.10.7",
            "subtitle": "jf · Heimkino · 2 Wiedergaben (1 transkodiert) · 482 Filme, 37 Serien, 1.296 Folgen, 318 Alben",
            "label": "2 laufen", "tone": "good",
        },
    ]
    assert widget["meta"] == {"connections": 1, "playing": 2, "transcoding": 1}

    # Der Schlüssel steht nirgends: weder in Antworten noch in Einstellungen, Protokoll oder Audit.
    settings = (await client.get(f"/api/v1/extensions/{EXT}/settings", headers=headers)).json()
    assert {s["label"]: s["is_set"] for s in settings["secrets"]} == {"jellyfin-key:jf": True}
    audit = (await db_session.execute(select(AuditEntry))).scalars().all()
    everything = res.text + json.dumps(widget) + json.dumps(settings) + caplog.text + json.dumps([a.detail or {} for a in audit], default=str)
    assert KEY not in everything


@pytest.mark.asyncio
async def test_latest_media_are_asked_without_a_user_and_without_virtual_items(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    await _overview(client, headers)
    request = next(r for r in fake.requests if r.url.path == "/Items")
    assert dict(request.url.params) == {
        "SortBy": "DateCreated", "SortOrder": "Descending", "Recursive": "true", "IncludeItemTypes": "Movie,Episode",
        "ExcludeLocationTypes": "Virtual", "Fields": "DateCreated", "EnableImages": "false", "EnableUserData": "false",
        "EnableTotalRecordCount": "false", "Limit": "10",
    }
    assert LATEST_QUERY.startswith("?SortBy=DateCreated&SortOrder=Descending")
    assert not any("UserId" in r.url.params or "userId" in r.url.params for r in fake.requests)
    assert not any(r.url.path == "/Items/Latest" for r in fake.requests)


@pytest.mark.asyncio
async def test_a_base_path_in_the_address_is_kept(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(url=f"{BASE}/jellyfin/"))
    fake.overrides.update({
        "/jellyfin/System/Info": lambda r: httpx.Response(200, json=fake.info),
        "/jellyfin/Sessions": lambda r: httpx.Response(200, json=[]),
        "/jellyfin/Items/Counts": lambda r: httpx.Response(200, json=fake.counts),
        "/jellyfin/Items": lambda r: httpx.Response(200, json={"Items": []}),
    })
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["playbacks"] == [] and connection["latest"] == []
    assert sorted(fake.paths()) == ["/jellyfin/Items", "/jellyfin/Items/Counts", "/jellyfin/Sessions", "/jellyfin/System/Info"]


@pytest.mark.asyncio
async def test_widget_when_nothing_is_playing_and_the_server_has_no_version(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("heimkino"))
    fake.sessions = [{"UserName": "anna", "NowPlayingItem": None}]
    fake.info = {"ServerName": "heimkino"}
    fake.counts = {"MovieCount": 1, "SeriesCount": 0, "EpisodeCount": 0, "AlbumCount": 0}
    rows = (await _widget(client, headers))["data"]
    assert rows == [{"title": "Jellyfin", "subtitle": "heimkino · nichts läuft · 1 Film", "label": "nichts läuft", "tone": "neutral"}], (
        "der Servername gleicht dem Kurznamen: er steht nicht doppelt da"
    )


@pytest.mark.asyncio
async def test_widget_marks_a_transcoding_stream_and_names_the_connection_when_there_are_several(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("eins"), _connection("zwei", url="http://zwei.example.com:8096"))
    fake.sessions = [_session("anna", "Film", PlayMethod="Transcode", PositionTicks=None)]
    fake.counts = {}
    rows = (await _widget(client, headers))["data"]
    assert rows[0] == {"title": "Film", "subtitle": "anna · Gerät anna · eins", "label": "transkodiert", "tone": "warn"}
    assert rows[1]["subtitle"] == "anna · Gerät anna · zwei"
    assert rows[2]["subtitle"].startswith("eins · Heimkino · 1 Wiedergabe (1 transkodiert)")
    assert len(rows) == 4


@pytest.mark.asyncio
async def test_widget_shows_at_most_ten_rows_and_says_how_many_playbacks_are_missing(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.sessions = [_session(f"u{i:03d}", f"Film {i}") for i in range(120)]
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == MAX_ROWS
    assert [r["title"] for r in rows[:8]] == [f"Film {i}" for i in range(8)]
    assert rows[8] == {"title": "… und 112 weitere Wiedergaben", "subtitle": "Alle stehen auf der Seite „Jellyfin“.", "label": "–", "tone": "neutral"}
    assert rows[9]["title"] == "Jellyfin 10.10.7" and rows[9]["label"] == "120 laufen"

    # Die Seite bekommt die ersten 50 und die Gesamtzahl.
    connection = _only(await _overview(client, headers))
    assert connection["playback_count"] == 120 and len(connection["playbacks"]) == 50

    # Passen alle genau, gibt es keine Schlusszeile.
    fake.sessions = [_session(f"u{i}", f"Film {i}") for i in range(9)]
    _service().clear()
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == MAX_ROWS and rows[8]["title"] == "Film 8"


@pytest.mark.asyncio
async def test_widget_with_many_connections_stays_within_ten_rows(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, *[_connection(f"jf{i}") for i in range(7)])
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == MAX_ROWS
    assert [r["title"] for r in rows[:2]] == ["Der Beispielfilm (2019)", "Die Beispielserie S02E05 · Der Besuch"]
    assert rows[2]["title"] == "… und 12 weitere Wiedergaben"
    assert [r["subtitle"].split(" · ")[0] for r in rows[3:]] == [f"jf{i}" for i in range(7)]

    await _configure(client, headers, *[_connection(f"jf{i}") for i in range(11)])
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == MAX_ROWS and rows[-1]["title"] == "… und 2 weitere Zeilen"
    assert not any(r["tone"] == "good" and r["label"] == "läuft" for r in rows), "bei so vielen gibt es nur die Zusammenfassungen"


@pytest.mark.asyncio
async def test_new_media_are_cut_to_ten(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.latest = {"Items": [{"Name": f"Film {i}", "Type": "Movie"} for i in range(25)]}
    connection = _only(await _overview(client, headers))
    assert [i["title"] for i in connection["latest"]] == [f"Film {i}" for i in range(10)]


@pytest.mark.asyncio
async def test_one_failing_part_does_not_hide_the_others(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/Sessions"] = lambda request: httpx.Response(500, text="kaputt")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["message"] is None
    assert (connection["playbacks"], connection["playback_count"]) == (None, None)
    assert connection["counts"]["movies"] == 482 and len(connection["latest"]) == 5
    assert connection["problems"] == ["Laufende Wiedergaben: Jellyfin „jf“ meldet einen Fehler (HTTP 500). Schau in das Protokoll von Jellyfin."]
    rows = (await _widget(client, headers))["data"]
    assert len(rows) == 1 and rows[0]["label"] == "unvollständig" and rows[0]["tone"] == "warn"
    assert "Wiedergaben nicht lesbar" in rows[0]["subtitle"] and "meldet einen Fehler (HTTP 500)" in rows[0]["subtitle"]

    fake.overrides["/Items/Counts"] = lambda request: httpx.Response(200, text="<html>")
    fake.overrides["/Items"] = lambda request: httpx.Response(200, json={"Items": "x"})
    _service().clear()
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error", "wenn nichts zu lesen war, ist es ein Fehler"
    assert connection["message"].startswith("Jellyfin „jf“ meldet einen Fehler (HTTP 500)")
    assert (connection["server_name"], connection["version"]) == ("Heimkino", "10.10.7"), "was zu lesen war, bleibt"
    assert (connection["playbacks"], connection["counts"], connection["latest"], connection["problems"]) == (None, None, None, [])


@pytest.mark.asyncio
async def test_a_revoked_key_during_the_queries_is_an_auth_failure(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/Sessions"] = lambda request: httpx.Response(401, text="no")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "auth_failed" and "API-Schlüssel wurde abgelehnt" in connection["message"]
    assert connection["version"] == "10.10.7" and connection["playbacks"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_key_says_what_to_do_and_asks_only_once(client, db_session, test_settings, status):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/System/Info"] = lambda request: httpx.Response(status, text="no")

    connection = _only(await _overview(client, headers))
    assert connection["state"] == "auth_failed"
    assert connection["message"].startswith("Jellyfin „jf“: Der API-Schlüssel wurde abgelehnt")
    assert f"(HTTP {status})" in connection["message"]
    assert "Dashboard → API-Schlüssel" in connection["message"] and "Verbindung testen" in connection["message"]
    assert (connection["playbacks"], connection["counts"], connection["latest"]) == (None, None, None)
    assert fake.paths() == ["/System/Info"], "ein abgelehnter Schlüssel braucht keine zweite Anfrage"

    rows = (await _widget(client, headers))["data"]
    assert rows == [{"title": "jf: Anmeldung fehlgeschlagen", "subtitle": connection["message"], "label": "Anmeldung fehlgeschlagen", "tone": "danger"}]
    health = await _health()
    assert health.healthy is False and "API-Schlüssel wurde abgelehnt" in health.message


@pytest.mark.asyncio
async def test_wrong_key_is_rejected_by_the_server_and_shown_as_auth_failed(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), key="falscherschluessel0000")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "auth_failed"
    assert fake.requests[0].headers["Authorization"] == 'MediaBrowser Token="falscherschluessel0000"'
    assert "falscherschluessel0000" not in json.dumps(connection)


@pytest.mark.asyncio
async def test_a_rejected_key_is_not_asked_again_for_five_minutes(client, db_session, test_settings):
    assert AUTH_FAILED_TTL_S == 300.0
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), key="falsch0000")
    service = _service()
    now = [5000.0]
    service._clock = lambda: now[0]

    assert _only(await _overview(client, headers))["state"] == "auth_failed"
    for _ in range(4):
        now[0] += 60.0
        assert _only(await _overview(client, headers))["state"] == "auth_failed"
        assert (await client.get(f"{API}/widgets/status", headers=headers)).status_code == 200
    assert len(fake.requests) == 1, "vier Minuten lang keine weitere Anmeldung"

    assert _only(await _overview(client, headers, "?refresh=true"))["state"] == "auth_failed"
    assert len(fake.requests) == 1, "auch „Aktualisieren“ umgeht die Pause nicht"
    now[0] += AUTH_FAILED_TTL_S
    await _overview(client, headers)
    assert len(fake.requests) == 2, "nach fünf Minuten wird wieder gefragt"


@pytest.mark.asyncio
async def test_changed_settings_end_the_pause(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), key="falsch0000")
    assert _only(await _overview(client, headers))["state"] == "auth_failed"
    assert len(fake.requests) == 1
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "jellyfin-key:jf", "value": KEY}, headers=headers)
    assert res.status_code == 204
    saved = await client.put(f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": [_connection(insecure=True)]}}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert not saved.json().get("secrets_cleared"), "dieselbe Adresse: der Schlüssel bleibt"
    assert _only(await _overview(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_testing_the_connection_ends_the_pause_and_asks_again(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(), key="falsch0000")
    assert _only(await _overview(client, headers))["state"] == "auth_failed"
    res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": "jellyfin-key:jf", "value": KEY}, headers=headers)
    assert res.status_code == 204
    assert _only(await _overview(client, headers))["state"] == "auth_failed", "noch aus dem Zwischenspeicher"

    health = await _health()
    assert health.healthy is True
    assert _only(await _overview(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_not_jellyfin_at_this_address(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/System/Info"] = lambda request: httpx.Response(404, text="404: Not Found")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error"
    assert connection["message"].startswith("Jellyfin „jf“: Unter dieser Adresse läuft kein Jellyfin, oder der Pfad stimmt nicht.")
    widget = (await _widget(client, headers))["data"]
    assert widget[0]["label"] == "Fehler" and widget[0]["tone"] == "warn"
    assert fake.paths() == ["/System/Info"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        (429, "bremst Anfragen gerade (HTTP 429)"),
        (503, "gerade nicht bereit (HTTP 503)"),
        (502, "gerade nicht bereit (HTTP 502)"),
        (500, "meldet einen Fehler (HTTP 500)"),
        (400, "hat die Anfrage abgelehnt (HTTP 400)"),
    ],
)
async def test_other_status_codes_have_fixed_sentences(client, db_session, test_settings, status, fragment):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/System/Info"] = lambda request: httpx.Response(status, text="Text des Servers: bitte nach evil.example.org gehen")
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and fragment in connection["message"]
    assert "evil.example.org" not in connection["message"], "Text des fremden Servers steht nie in der Meldung"


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
    fake.overrides["/System/Info"] = lambda request: httpx.Response(302, headers={"Location": location})

    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200, res.text
    connection = _only(res.json())
    assert connection["state"] == "error"
    assert connection["message"].startswith(f"Jellyfin „jf“ {sentence}")
    assert "Trage in den Einstellungen die endgültige Adresse ein." in connection["message"]
    widget = await client.get(f"{API}/widgets/status", headers=headers)
    assert widget.status_code == 200 and "leitet auf eine andere Adresse um" in widget.text
    assert "angreifer" not in (res.text + widget.text + caplog.text).lower()
    assert "Traceback" not in caplog.text
    assert all(r.url.host == "jellyfin.example.com" for r in fake.requests), "der Weiterleitung wird nicht gefolgt"
    assert fake.paths() == ["/System/Info"]


@pytest.mark.asyncio
async def test_a_redirect_in_one_part_is_a_problem_of_that_part_only(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/Items"] = lambda request: httpx.Response(307, headers={"Location": "https://angreifer.example/"})
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["latest"] is None
    assert "Neu hinzugefügt: Jellyfin „jf“ leitet auf eine andere Adresse um (HTTP 307)." in connection["problems"][0]
    assert "angreifer" not in json.dumps(connection)


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
    assert fragment in connection["message"] and "Jellyfin „jf“" in connection["message"]
    assert connection["counts"] is None
    assert fake.paths() == ["/System/Info"], "ist der Server nicht erreichbar, hilft die zweite Anfrage nicht"

    rows = (await _widget(client, headers))["data"]
    assert rows == [{"title": "jf: nicht erreichbar", "subtitle": connection["message"], "label": "nicht erreichbar", "tone": "danger"}]
    health = await _health()
    assert health.healthy is False and connection["message"] in health.message


@pytest.mark.asyncio
async def test_the_server_vanishing_after_the_first_answer_is_unreachable(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())

    def gone(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 111] Connection refused")

    fake.overrides.update({"/Sessions": gone, "/Items/Counts": gone, "/Items": gone})
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "unreachable" and connection["version"] == "10.10.7"


@pytest.mark.asyncio
async def test_a_text_that_quotes_the_key_never_reaches_the_user(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.error = httpx.ConnectError(f"boom {KEY} boom")
    res = await client.get(f"{API}/overview", headers=headers)
    assert _only(res.json())["state"] == "unreachable"
    health = await _health()
    assert KEY not in res.text and KEY not in caplog.text and KEY not in json.dumps(health.model_dump(), default=str)


@pytest.mark.asyncio
async def test_answers_that_are_not_jellyfin(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    service = _service()

    for body in ("<html><body>Anmelden</body></html>", "[1, 2]", '"text"', "", "null", "{}", '{"foo": "bar"}'):
        service.clear()
        fake.overrides["/System/Info"] = lambda request, body=body: httpx.Response(200, text=body)
        connection = _only(await _overview(client, headers))
        assert connection["state"] == "error" and "nicht das erwartete Format" in connection["message"], body
        assert "Führt die Adresse wirklich zu Jellyfin?" in connection["message"]
        assert "/System/Info" in connection["message"]

    del fake.overrides["/System/Info"]
    service.clear()
    fake.overrides["/Sessions"] = lambda request: httpx.Response(200, content=b"[" * 100000)
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["playbacks"] is None, "absichtlich tief verschachteltes JSON"
    assert "nicht das erwartete Format" in connection["problems"][0] and "/Sessions" in connection["problems"][0]


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

    fake.overrides["/Sessions"] = lambda request: httpx.Response(200, content=endless())
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["playbacks"] is None
    assert "ungewöhnlich große Antwort (mehr als 4 MB)" in connection["problems"][0]
    assert sent <= 6
    assert connection["counts"] is not None and connection["version"] == "10.10.7"

    _service().clear()
    fake.overrides["/System/Info"] = lambda request: httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and "(mehr als 1 MB)" in connection["message"]


@pytest.mark.asyncio
async def test_a_big_but_allowed_session_list_is_read(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    big = [{**_session(f"u{i:04d}", f"Film {i}"), "NowPlayingQueue": [{"Id": "x" * 32, "PlaylistItemId": "y" * 200}] * 4} for i in range(3000)]
    fake.sessions = big
    assert 2 * 1024 * 1024 < len(json.dumps(big)) < 4 * 1024 * 1024
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok" and connection["problems"] == []
    assert connection["playback_count"] == 3000 and len(connection["playbacks"]) == 50


@pytest.mark.asyncio
async def test_tls_exception_is_used_only_when_the_connection_asks_for_it(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("streng"), _connection("lax", insecure=True, url="https://lax.example.com"))
    body = await _overview(client, headers)
    assert [c["state"] for c in body["connections"]] == ["ok", "ok"]
    assert {r.url.host for r in fake.insecure_requests} == {"lax.example.com"}
    assert {r.url.host for r in fake.requests} == {"jellyfin.example.com", "lax.example.com"}
    assert {r.url.host for r in fake.requests if r not in fake.insecure_requests} == {"jellyfin.example.com"}


@pytest.mark.asyncio
async def test_a_fingerprint_goes_into_the_call_instead_of_the_switch(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(url="https://jellyfin.example.com", pin="ab" * 32, insecure=True))
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "ok", connection["message"]
    assert sorted(r.url.path for r in fake.pinned_requests) == ["/Items", "/Items/Counts", "/Sessions", "/System/Info"]
    assert fake.insecure_requests == [], "mit Fingerabdruck wird nie ungeprüft verbunden, auch wenn der Schalter an ist"
    health = await _health()
    assert health.healthy is True
    assert [r.url.path for r in fake.pinned_requests][-1] == "/System/Info"


@pytest.mark.asyncio
async def test_a_changed_certificate_stops_the_connection_with_its_own_sentence(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection(url="https://jellyfin.example.com", pin=PIN))
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
    assert health.healthy is False and "anderes Zertifikat als das gemerkte" in health.details["jf"]["error"]


@pytest.mark.asyncio
async def test_an_unusable_fingerprint_blocks_the_connection_instead_of_skipping_the_check(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.ctx.settings.set({"connections": [
        {"name": "kaputt", "base_url": "https://jellyfin.example.com", "tls_fingerprint_sha256": "kein-fingerabdruck", "tls_insecure_skip_verify": True},
        {"name": "http", "base_url": BASE, "tls_fingerprint_sha256": PIN},
    ]})
    for name in ("kaputt", "http"):
        await loaded.ctx.secrets.create(label=f"jellyfin-key:{name}", kind="generic", value=KEY)
    body = await _overview(client, headers)
    assert [(c["name"], c["state"]) for c in body["connections"]] == [("kaputt", "error"), ("http", "error")]
    assert "Fingerabdruck" in body["connections"][0]["message"] and "nur für https://" in body["connections"][1]["message"]
    assert fake.requests == [] and fake.insecure_requests == []
    health = await _health()
    assert health.healthy is False and fake.requests == []


@pytest.mark.asyncio
async def test_results_are_cached_for_a_short_time_and_the_key_is_read_only_when_needed(client, db_session, test_settings):
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
    assert len(fake.requests) == 4, "Seite und Kachel fragen Jellyfin nur einmal (vier Aufrufe)"
    assert reads == ["jellyfin-key:jf"]

    await _overview(client, headers, "?refresh=true")
    assert len(fake.requests) == 8, "der Knopf Aktualisieren umgeht den Zwischenspeicher"
    assert reads == ["jellyfin-key:jf"] * 2

    await _overview(client, headers)
    assert len(fake.requests) == 8
    now[0] += CACHE_TTL_S - 1
    await _overview(client, headers)
    assert len(fake.requests) == 8
    now[0] += 2.0
    await _overview(client, headers)
    assert len(fake.requests) == 12, "nach 20 Sekunden wird neu gefragt"

    saved = await client.put(
        f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": [_connection(insecure=True)]}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert not saved.json().get("secrets_cleared"), "dieselbe Adresse: der Schlüssel bleibt"
    assert _only(await _overview(client, headers))["state"] == "ok"
    assert len(fake.requests) == 16, "nach geänderten Einstellungen wird sofort neu gefragt"


@pytest.mark.asyncio
async def test_changing_the_address_removes_the_key_so_it_never_goes_to_a_new_target(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    assert _only(await _overview(client, headers))["state"] == "ok"
    saved = await client.put(
        f"/api/v1/extensions/{EXT}/settings",
        json={"current_password": "correct-horse-battery", "values": {"connections": [_connection(url="http://angreifer.example.com:8096")]}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["secrets_cleared"] == ["jellyfin-key:jf"]
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "not_configured"
    assert all(r.url.host != "angreifer.example.com" for r in fake.requests)


@pytest.mark.asyncio
async def test_two_connections_are_independent_and_asked_at_the_same_time(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("eins"), _connection("zwei", url="http://zwei.example.com:8096"))
    barrier = asyncio.Barrier(2)

    async def meet(request: httpx.Request) -> httpx.Response:
        # Beide Verbindungen müssen gleichzeitig hier sein, sonst wartet die erste vergeblich auf die zweite.
        await asyncio.wait_for(barrier.wait(), 5)
        return httpx.Response(200, json=fake.info)

    fake.overrides["/System/Info"] = meet
    body = await _overview(client, headers)
    assert [(c["name"], c["state"]) for c in body["connections"]] == [("eins", "ok"), ("zwei", "ok")]
    assert body["summary"] == {"connections": 2, "playing": 4, "transcoding": 2}


@pytest.mark.asyncio
async def test_the_three_parts_of_one_connection_are_asked_at_the_same_time(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    barrier = asyncio.Barrier(3)

    def meeting(body: Any) -> Callable[[httpx.Request], Any]:
        async def meet(request: httpx.Request) -> httpx.Response:
            await asyncio.wait_for(barrier.wait(), 5)
            return httpx.Response(200, json=body)

        return meet

    fake.overrides.update({
        "/Sessions": meeting(fake.sessions), "/Items/Counts": meeting(fake.counts), "/Items": meeting(fake.latest),
    })
    assert _only(await _overview(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_a_broken_connection_does_not_hold_up_or_break_the_others(client, db_session, test_settings, monkeypatch):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(
        client, headers, _connection("gut"), _connection("haengt", url="http://haengt.example.com:8096"),
        _connection("abgelehnt", url="http://abgelehnt.example.com:8096"),
    )

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(60)
        return httpx.Response(200)

    def by_host(request: httpx.Request) -> Any:
        if request.url.host == "abgelehnt.example.com":
            return httpx.Response(401, text="no")
        if request.url.host == "haengt.example.com":
            return hang(request)
        return httpx.Response(200, json=fake.info)

    fake.overrides["/System/Info"] = by_host
    # Das Modul der geladenen Erweiterung (nicht das beim Import dieser Datei, das ein Test davor aufgeräumt hat).
    monkeypatch.setattr(sys.modules[type(_service()).__module__], "CONNECTION_TIMEOUT_S", 0.3)
    body = await asyncio.wait_for(_overview(client, headers), 10)
    states = {c["name"]: c["state"] for c in body["connections"]}
    assert states == {"gut": "ok", "haengt": "unreachable", "abgelehnt": "auth_failed"}
    assert body["summary"]["connections"] == 3
    rows = (await _widget(client, headers))["data"]
    assert [r["title"] for r in rows[-3:]] == ["Jellyfin 10.10.7", "haengt: nicht erreichbar", "abgelehnt: Anmeldung fehlgeschlagen"]


def test_time_limits_are_thirty_seconds_per_request_and_fifty_per_connection():
    from nodvard_deck_ext_jellyfin import service

    assert (transport.TOTAL_TIMEOUT_S, service.CONNECTION_TIMEOUT_S) == (30.0, 50.0)


@pytest.mark.asyncio
async def test_unusable_entries_become_error_cards_without_a_request(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.ctx.settings.set({"connections": [
        {"name": "ok", "base_url": BASE},
        {"name": "ok", "base_url": BASE},
        {"name": "pw", "base_url": "http://nutzer:" + "passwort@jellyfin.example.com:8096"},
    ]})
    await loaded.ctx.secrets.create(label="jellyfin-key:ok", kind="generic", value=KEY)
    body = await _overview(client, headers)
    assert [(c["name"], c["state"]) for c in body["connections"]] == [("ok", "ok"), ("ok", "error"), ("pw", "error")]
    assert "mehrfach vergeben" in body["connections"][1]["message"]
    assert "passwort" not in json.dumps(body)
    assert body["connections"][2]["url"] is None
    assert len(fake.requests) == 4 and {r.url.host for r in fake.requests} == {"jellyfin.example.com"}, "nur der brauchbare Eintrag fragt an"


@pytest.mark.asyncio
async def test_unexpected_errors_do_not_become_a_500(client, db_session, test_settings, caplog):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    service = _service()

    async def broken(cfg, has_key):
        raise RuntimeError("innerer Fehler mit Details")

    service._load = broken
    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200, res.text
    connection = _only(res.json())
    assert connection["state"] == "error" and connection["message"] == "Bei der Abfrage von jf ist ein unerwarteter Fehler aufgetreten."
    assert "Details" not in res.text
    assert "innerer Fehler" in caplog.text, "im Protokoll steht die Ursache"
    assert (await client.get(f"{API}/widgets/status", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_unreadable_or_odd_keys_are_a_hint_not_a_crash(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    loaded = get_extension_runtime().loaded[EXT]
    real = loaded.ctx.secrets.get_handle

    async def boom(label):
        raise RuntimeError("Tresor kaputt " + KEY)

    loaded.ctx.secrets.get_handle = boom
    connection = _only(await _overview(client, headers))
    assert connection["state"] == "error" and "aus dem Tresor lesen" in connection["message"] and KEY not in json.dumps(connection)
    assert fake.requests == []
    loaded.ctx.secrets.get_handle = real

    # Ein Schlüssel mit Leerzeichen, Anführungszeichen oder Zeilenumbruch wird nie gesendet: Er käme in den Header.
    for odd in ("abc def", 'abc",Client="x', "abc\r\nX-Evil: 1", "schlüssel", "a%2Cb", "x" * 129):
        _service().clear()
        await _configure(client, headers, _connection(), key=odd)
        connection = _only(await _overview(client, headers))
        assert connection["state"] == "error" and "Zeichen, die in einem Schlüssel nicht vorkommen" in connection["message"], odd
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
    from nodvard_deck_ext_jellyfin.models import Overview

    schema = Overview.model_json_schema()
    assert set(schema["properties"]) == {"connections", "summary"}
    assert set(schema["$defs"]) >= {"Connection", "LatestItem", "LibraryCounts", "Playback", "Summary"}
    assert set(schema["$defs"]["Connection"]["properties"]) == {
        "name", "url", "state", "message", "server_name", "version", "playback_count", "playbacks", "counts", "latest", "problems",
    }
    media = {"title", "kind", "series", "season", "episode", "artist", "year"}
    assert set(schema["$defs"]["Playback"]["properties"]) == media | {"user", "device", "client", "play_method", "transcoding", "progress_percent", "paused"}
    assert set(schema["$defs"]["LatestItem"]["properties"]) == media | {"added_at"}
    assert set(schema["$defs"]["LibraryCounts"]["properties"]) == {
        "movies", "series", "episodes", "albums", "songs", "music_videos", "box_sets", "books",
    }
    assert set(schema["$defs"]["Summary"]["properties"]) == {"connections", "playing", "transcoding"}


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
async def test_testing_the_connection_checks_every_connection_with_the_system_info(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection("eins"), _connection("zwei", url="http://zwei.example.com:8096"))
    health = await _health()
    assert health.healthy is True and health.message == "eins: in Ordnung; zwei: in Ordnung"
    assert sorted(fake.paths()) == ["/System/Info", "/System/Info"], "nur die Erreichbarkeit und der Schlüssel, keine Listen"
    assert {r.headers["Authorization"] for r in fake.requests} == {AUTH_HEADER}
    assert health.details == {"eins": {"healthy": True}, "zwei": {"healthy": True}}

    # Durch die echte Route „Verbindung testen“.
    tested = await client.post(f"/api/v1/extensions/{EXT}/test", headers=headers)
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True and tested.json()["message"] == "Alle 2 Verbindungen funktionieren."
    assert KEY not in tested.text


@pytest.mark.asyncio
async def test_testing_the_connection_names_each_problem_with_a_fixed_sentence(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(
        client, headers, _connection("gut"), _connection("alt", url="http://alt.example.com:8096"),
        _connection("kein-key", url="http://k.example.com:8096"), key=None,
    )
    for name in ("gut", "alt"):
        res = await client.put(f"/api/v1/extensions/{EXT}/secrets", json={"current_password": "correct-horse-battery", "label": f"jellyfin-key:{name}", "value": KEY}, headers=headers)
        assert res.status_code == 204
    fake.overrides["/System/Info"] = lambda request: (
        httpx.Response(401, text="no") if request.url.host == "alt.example.com" else httpx.Response(200, json=fake.info)
    )
    health = await _health()
    assert health.healthy is False
    assert health.details["gut"] == {"healthy": True}
    assert health.details["alt"]["healthy"] is False and "API-Schlüssel wurde abgelehnt" in health.details["alt"]["error"]
    assert health.details["kein-key"]["error"].startswith("Für kein-key fehlt der API-Schlüssel.")
    assert all(d["verbatim"] is True for d in health.details.values() if not d["healthy"])
    assert [r.url.host for r in fake.requests if r.url.host == "k.example.com"] == [], "ohne Schlüssel keine Anfrage"

    tested = await client.post(f"/api/v1/extensions/{EXT}/test", headers=headers)
    body = tested.json()
    assert body["ok"] is False and body["message"] == "2 von 3 Verbindungen funktionieren nicht – Einzelheiten unten."
    by_name = {d["name"]: d for d in body["details"]}
    assert by_name["gut"]["ok"] is True
    assert by_name["alt"]["message"].startswith("Jellyfin „alt“: Der API-Schlüssel wurde abgelehnt (HTTP 401).")
    assert KEY not in tested.text


@pytest.mark.asyncio
async def test_testing_the_connection_survives_surprises(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, _connection())
    fake.overrides["/System/Info"] = lambda request: httpx.Response(200, text="<html>Anmelden</html>")
    health = await _health()
    assert health.healthy is False and "nicht das erwartete Format" in health.details["jf"]["error"]

    service = _service()

    async def broken(cfg):
        raise RuntimeError("innerer Fehler " + KEY)

    service._test_one = broken
    health = await _health()
    assert health.healthy is False
    assert KEY not in health.message


@pytest.mark.asyncio
async def test_each_connection_is_suggested_as_an_app_without_any_request(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    assert (await client.get("/api/v1/apps/suggestions", headers=headers)).json()["suggestions"] == []

    await _configure(client, headers, _connection(url="http://203.0.113.20:8096"), key=None)
    res = await client.get("/api/v1/apps/suggestions", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json() == {
        "suggestions": [{"name": "Jellyfin", "url": "http://203.0.113.20:8096", "source": "Jellyfin", "host": None, "service_id": None}],
        "errors": [],
    }

    await _configure(client, headers, _connection("haus", url="http://203.0.113.20:8096"), _connection("ferien", url="https://ferien.example.com"), key=None)
    suggestions = (await client.get("/api/v1/apps/suggestions", headers=headers)).json()["suggestions"]
    assert [(s["name"], s["url"]) for s in suggestions] == [
        ("Jellyfin ferien", "https://ferien.example.com"), ("Jellyfin haus", "http://203.0.113.20:8096"),
    ]
    assert fake.requests == [], "ein Vorschlag liest weder den Schlüssel noch ruft er Jellyfin auf"


@pytest.mark.asyncio
async def test_unusable_entries_are_not_suggested(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.ctx.settings.set({"connections": [
        {"name": "ok", "base_url": BASE},
        {"name": "pw", "base_url": "http://nutzer:" + "passwort@jellyfin.example.com:8096"},
        {"name": "leer", "base_url": ""},
    ]})
    suggestions = (await client.get("/api/v1/apps/suggestions", headers=headers)).json()["suggestions"]
    assert [(s["name"], s["url"]) for s in suggestions] == [("Jellyfin", BASE)]
    assert "passwort" not in json.dumps(suggestions)


def test_only_get_requests_exist_in_the_client():
    source = REPO_EXTENSIONS_DIR / "jellyfin" / "src" / "nodvard_deck_ext_jellyfin"
    text = "".join(p.read_text(encoding="utf-8") for p in source.glob("*.py"))
    assert not re.search(r"\.(post|put|patch|delete|request)\(", text)
    assert not re.search(r"""["'](POST|PUT|PATCH|DELETE)["']""", text)
    assert text.count('"GET"') == 1, "genau eine Stelle schickt eine Anfrage"
