"""Tests für die Synology-Erweiterung; DSM ist ein `httpx.MockTransport` mit Schnittstellenliste, Anmeldung und Sitzung."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import re
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

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
        self._name = f"nodvard_deck_ext_synology.{name}"

    def __getattr__(self, attr: str) -> Any:
        return getattr(importlib.import_module(self._name), attr)


syn_backup = _ExtModule("backup")
syn_client = _ExtModule("client")
syn_config = _ExtModule("config")
syn_problems = _ExtModule("problems")
syn_service = _ExtModule("service")
syn_watch = _ExtModule("watch")
models = _ExtModule("models")
parse = _ExtModule("parse")
transport = _ExtModule("transport")
widgets = _ExtModule("widgets")

BASE = "https://nas.test:5001"
DEVICE = "synology"  # so heißt das Gerät der Einzeltests, wie das aus der früheren Einrichtung übernommene
USER = "nodvard"
PASSWORD = "Beispiel&Pass wort=1+%"
EXT = "synology"
API = "/api/v1/ext/synology"
ENTRY = "/webapi/entry.cgi"
QUERY = "/webapi/query.cgi"
TIB = 1024**4
GIB = 1024**3

APIS = {
    "SYNO.API.Auth": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 7},
    "SYNO.DSM.Info": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 2},
    "SYNO.Core.System.Utilization": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 1},
    "SYNO.Storage.CGI.Storage": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 1},
    "SYNO.Core.Upgrade.Server": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 3},
}
INFO = {
    "model": "DS220+", "version_string": "DSM 7.2.1-69057 Update 5", "uptime": 3 * 86400 + 4 * 3600 + 120, "temperature": 42,
    "temperature_warn": False, "ram": 2048, "serial": "SERIAL-TEST-0001", "time": "Sat Oct 17 14:05:09 2026",
}
UTILIZATION = {
    "cpu": {"user_load": 7, "system_load": 3, "other_load": 2},
    "memory": {"real_usage": 41, "total_real": 2000000, "avail_real": 1180000},
}
STORAGE = {
    "volumes": [
        {"id": "volume_1", "status": "normal", "fs_type": "btrfs", "size": {"total": "3958241859994", "used": "442381631488"}},
        {"id": "volume_2", "status": "normal", "size": {"total": str(1000 * GIB), "used": str(950 * GIB)}},
    ],
    "disks": [
        {
            "id": "sata1", "name": "Laufwerk 1", "status": "normal", "temp": 36, "smart_status": "normal",
            "model": "WD40EFRX-68N32N0", "vendor": "WDC", "exceed_bad_sector_thr": False, "below_remain_life_thr": False,
        },
        {
            "id": "sata2", "name": "Laufwerk 2", "status": "normal", "temp": 39, "smart_status": "normal",
            "model": "ST4000VN006", "vendor": "Seagate", "exceed_bad_sector_thr": False, "below_remain_life_thr": False,
        },
    ],
}
UPDATE = {"update": {"available": True, "version": "DSM 7.2.2-72806"}}
DATA_CALLS = {
    "SYNO.DSM.Info": "getinfo", "SYNO.Core.System.Utilization": "get", "SYNO.Storage.CGI.Storage": "load_info",
    "SYNO.Core.Upgrade.Server": "check",
}


@pytest.fixture(autouse=True)
def _synology_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_EXTENSIONS_DIR / "synology" / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _storage(body: Any) -> Any:
    found = parse.parse_storage(body)
    assert found is not None
    return found


def _ok(data: Any) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "data": data})


def _fail(code: int) -> httpx.Response:
    return httpx.Response(200, json={"success": False, "error": {"code": code}})


# --- Konfiguration


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://nas.test:5001", "https://nas.test:5001"),
        ("  https://nas.test:5001/  ", "https://nas.test:5001"),
        ("http://203.0.113.10:5000/webman/index.cgi", "http://203.0.113.10:5000"),
        ("https://nas.test:5001/webman/", "https://nas.test:5001"),
        ("https://nas.test:5001/webapi/entry.cgi?api=SYNO.API.Info", "https://nas.test:5001"),
        ("https://nas.test:5001/WebMan/index.cgi#/desktop", "https://nas.test:5001"),
        ("https://nas.test:5001/?x=1#y", "https://nas.test:5001"),
        # Ein Unterpfad davor (DSM hinter einem Proxy) bleibt.
        ("https://proxy.test/nas/webman/index.cgi", "https://proxy.test/nas"),
        ("https://proxy.test/nas/", "https://proxy.test/nas"),
        # Nur ganze Pfadteile zählen.
        ("https://proxy.test/webmanager/x", "https://proxy.test/webmanager/x"),
    ],
)
def test_the_address_is_cut_at_the_ui_path_and_loses_query_and_fragment(raw, expected):
    assert syn_config.normalize_base_url(raw) == expected


def test_the_ui_paths_are_webman_and_webapi():
    assert syn_config.UI_PATHS == ("webman", "webapi")


@pytest.mark.parametrize(
    "raw",
    ["", "nas.test", "ftp://nas.test", "https://", "https://nutzer:" + "geheim@nas.test:5001", "https://nas.test:99999", "https://nas .test", None],
)
def test_unusable_addresses_are_refused(raw):
    with pytest.raises(ValueError):
        syn_config.normalize_base_url(raw)


def test_username_is_trimmed_or_empty():
    assert syn_config.normalize_username("  nodvard ") == "nodvard"
    assert syn_config.normalize_username(5) == "" and syn_config.normalize_username(None) == ""
    assert syn_config.normalize_username("a\nb") == "" and syn_config.normalize_username("x" * 200) == ""


def _read_one(**entry: Any):
    """Das eine Gerät, das aus diesem Eintrag gelesen wird."""
    found = syn_config.read_devices({"connections": [{"name": "nas", **entry}]})
    assert len(found) == 1
    return found[0]


def test_the_certificate_check_stays_on_unless_it_is_switched_off_on_purpose():
    assert _read_one(base_url=BASE).insecure_tls is False
    assert _read_one(base_url=BASE, tls_insecure_skip_verify=False).insecure_tls is False
    assert _read_one(base_url=BASE, tls_insecure_skip_verify="true").insecure_tls is False, "nur ein echtes „an“ zählt"
    assert _read_one(base_url=BASE, tls_insecure_skip_verify=True).insecure_tls is True


def test_a_device_without_an_address_is_not_a_problem_but_a_bad_address_is():
    assert _read_one().base_url is None and _read_one().problem is None
    assert _read_one(base_url="  ").problem is None
    bad = _read_one(base_url="https://nutzer:" + "geheim@nas.test")
    assert bad.base_url is None and bad.problem and "geheim" not in bad.problem and "nas" in bad.problem
    assert _read_one(base_url="https://nas.test/webman/x", username=" a ").username == "a"


def test_no_devices_when_the_list_is_missing_or_broken():
    for settings in ({}, {"connections": None}, {"connections": "nas"}, {"connections": [5, None, "x", {"name": ""}, {}]}):
        assert syn_config.read_devices(settings) == []
    assert syn_config.read_devices({"base_url": BASE, "username": USER}) == [], "die alten Einzelfelder lesen nur die Übernahme"


def test_devices_keep_their_order_and_their_own_settings():
    found = syn_config.read_devices({"connections": [
        {"name": "ds920", "base_url": "https://a.test:5001/webman/index.cgi", "username": "eins", "tls_insecure_skip_verify": True},
        {"name": "ds415", "base_url": "http://b.test:5000", "username": "zwei"},
    ]})
    assert [(d.name, d.base_url, d.username, d.insecure_tls, d.problem) for d in found] == [
        ("ds920", "https://a.test:5001", "eins", True, None), ("ds415", "http://b.test:5000", "zwei", False, None),
    ]


@pytest.mark.parametrize("name", ["..", ".versteckt", "-x", "mit leerzeichen", "a/b", "../etc", "ä", "x" * 41, "a\nb"])
def test_a_name_that_cannot_be_used_in_a_file_name_or_label_is_a_problem_without_any_request(name):
    [device] = syn_config.read_devices({"connections": [{"name": name, "base_url": BASE, "username": USER}]})
    assert device.named is False and device.problem and "Kurzname" in device.problem
    assert device.base_url is None


@pytest.mark.parametrize("name", ["a", "ds920", "DS415.play", "keller_2", "nas-1", "x" * 40])
def test_the_names_the_schema_allows_are_valid(name):
    [device] = syn_config.read_devices({"connections": [{"name": name, "base_url": BASE, "username": USER}]})
    assert device.named is True and device.problem is None
    assert syn_config.password_label(name) == f"synology-password:{name}"
    assert len(syn_config.password_label(name)) <= 128


def test_a_name_twice_makes_every_entry_with_it_unusable_regardless_of_case():
    found = syn_config.read_devices({"connections": [
        {"name": "Nas", "base_url": BASE, "username": USER}, {"name": "nas", "base_url": BASE, "username": USER},
        {"name": "Nas", "base_url": BASE, "username": USER}, {"name": "keller", "base_url": BASE, "username": USER},
    ]})
    assert [(d.name, d.named) for d in found] == [("Nas", False), ("nas", False), ("Nas", False), ("keller", True)]
    assert all("mehrfach" in (d.problem or "") and d.base_url is None for d in found[:3])


def test_the_schema_pattern_and_the_code_agree_on_names():
    import re

    pattern = json.loads((REPO_EXTENSIONS_DIR / EXT / "settings.schema.json").read_text(encoding="utf-8"))
    pattern = pattern["properties"]["connections"]["items"]["properties"]["name"]["pattern"]
    for name in ["a", "ds920", "..", "-x", "x" * 40, "x" * 41, "a b", "ä", ".a", "a.b", "a_b-c"]:
        assert bool(re.fullmatch(pattern, name)) is syn_config.is_valid_name(name), name


def test_the_old_single_fields_become_one_entry_for_the_first_device():
    entry = syn_config.legacy_entry({
        "base_url": BASE, "username": USER, "tls_insecure_skip_verify": True, "tls_fingerprint_sha256": "AB:CD", "notify": False,
    })
    assert entry == {
        "name": "synology", "base_url": BASE, "username": USER, "tls_insecure_skip_verify": True, "tls_fingerprint_sha256": "AB:CD",
    }
    assert syn_config.legacy_entry({"base_url": BASE}) == {"name": "synology", "base_url": BASE}
    for nothing in ({}, {"base_url": ""}, {"base_url": "  "}, {"base_url": None}, {"username": USER}, {"base_url": 5}):
        assert syn_config.legacy_entry(nothing) is None


# --- Antworten lesen


def test_discovery_keeps_valid_entries_and_refuses_odd_paths():
    found = parse.parse_discovery({
        **APIS,
        "SYNO.Evil": {"path": "../../etc/passwd", "minVersion": 1, "maxVersion": 1},
        "SYNO.Evil2": {"path": "/entry.cgi", "minVersion": 1, "maxVersion": 1},
        "SYNO.Evil3": {"path": "a//b", "minVersion": 1, "maxVersion": 1},
        "SYNO.NoVersions": {"path": "entry.cgi"},
        "SYNO.NotDict": 5,
    })
    assert found is not None and set(found) == set(APIS)
    assert found["SYNO.API.Auth"] == parse.ApiInfo("entry.cgi", 1, 7)
    assert parse.parse_discovery([]) is None and parse.parse_discovery(None) is None


def test_the_sid_must_look_like_a_sid():
    assert parse.parse_sid({"sid": "AbCdEf123456_-xyz"}) == "AbCdEf123456_-xyz"
    for bad in ({}, {"sid": ""}, {"sid": "kurz"}, {"sid": "mit leerzeichen!"}, {"sid": 5}, [], None):
        assert parse.parse_sid(bad) is None


def test_system_info_is_read_without_the_serial_number():
    info = parse.parse_system_info(INFO)
    assert info is not None
    assert info.model_dump() == {
        "model": "DS220+", "dsm_version": "DSM 7.2.1-69057 Update 5", "uptime_seconds": 3 * 86400 + 4 * 3600 + 120,
        "temperature": 42, "temperature_warn": False, "ram_mb": 2048, "clock": "2026-10-17T14:05",
    }
    assert "SERIAL" not in info.model_dump_json()


@pytest.mark.parametrize(
    ("over", "temperature", "warn"),
    [({"temperature": 0}, None, False), ({"temperature": None}, None, False), ({"temperature_warn": True}, 42, True),
     ({"temperature": "heiß"}, None, False), ({"temperature": 500}, None, False), ({"temperature_warn": "ja"}, 42, None)],
)
def test_temperature_is_optional_and_a_zero_means_no_sensor(over, temperature, warn):
    info = parse.parse_system_info({**INFO, **over})
    assert info is not None and info.temperature == temperature and info.temperature_warn is warn


@pytest.mark.parametrize("body", [{}, [], "text", None, 7, {"serial": "x"}, {"ram": 2048}])
def test_system_info_that_is_not_dsm_is_refused(body):
    assert parse.parse_system_info(body) is None


def test_utilization_adds_the_three_cpu_parts_and_reads_the_ram():
    load = parse.parse_utilization(UTILIZATION)
    assert load is not None and load.model_dump() == {"cpu_percent": 12.0, "ram_percent": 41.0}


def test_ram_is_computed_from_the_sizes_when_dsm_sends_no_percentage():
    load = parse.parse_utilization({"cpu": {"user_load": 1}, "memory": {"total_real": 2000000, "avail_real": 500000}})
    assert load is not None and load.model_dump() == {"cpu_percent": 1.0, "ram_percent": 75.0}
    only_ram = parse.parse_utilization({"memory": {"real_usage": "55"}})
    assert only_ram is not None and only_ram.model_dump() == {"cpu_percent": None, "ram_percent": 55.0}
    clamped = parse.parse_utilization({"cpu": {"user_load": 90, "system_load": 30, "other_load": 5}})
    assert clamped is not None and clamped.cpu_percent == 100.0


@pytest.mark.parametrize("body", [{}, [], None, {"cpu": {}, "memory": {}}, {"cpu": "viel"}])
def test_utilization_without_numbers_is_refused(body):
    assert parse.parse_utilization(body) is None


def test_volume_sizes_arrive_as_strings_and_become_a_percentage():
    volumes = _storage(STORAGE).volumes
    first, second = volumes
    assert (first.id, first.name, first.status, first.fs_type) == ("volume_1", "Volume 1", "normal", "btrfs")
    assert (first.used, first.total, first.percent) == (442381631488, 3958241859994, 11)
    assert (second.name, second.percent, second.fs_type) == ("Volume 2", 95, None)


@pytest.mark.parametrize(
    ("size", "percent"),
    [({"total": "0", "used": "0"}, None), ({"total": "100", "used": "200"}, 100), ({"total": "abc", "used": "1"}, None),
     ({"total": "100"}, None), ({"total": 200, "used": 100}, 50), (None, None), ({"total": "-5", "used": "1"}, None)],
)
def test_odd_sizes_never_break_the_percentage(size, percent):
    volumes = _storage({"volumes": [{"id": "volume_1", "status": "normal", "size": size}]}).volumes
    assert volumes[0].percent == percent


@pytest.mark.parametrize(
    ("status", "tone"),
    [("normal", "good"), ("attention", "warn"), ("warning", "warn"), ("degraded", "warn"), ("repairing", "warn"),
     ("background", "warn"), ("crashed", "danger"), ("failing", "danger"), ("abnormal", "danger"),
     ("sys_partition_crashed", "danger"), ("repair_failed", "danger"), ("not_use", "neutral"), ("etwas_neues", "neutral"),
     ("Normal", "good"), ("", "neutral"), (None, "neutral")],
)
def test_status_becomes_a_tone(status, tone):
    assert parse.tone_for(status) == tone


def test_status_gets_a_german_label_and_unknown_ones_stay_readable():
    assert parse.status_label("degraded") == "Eingeschränkt"
    assert parse.status_label("crashed") == "Ausgefallen"
    assert parse.status_label("sys_partition_normal") == "Sys partition normal"
    assert parse.status_label(None) == "Unbekannt"


def test_volume_names_come_from_the_id():
    assert parse.volume_name("volume_1") == "Volume 1"
    assert parse.volume_name("volume_12") == "Volume 12"
    assert parse.volume_name("reuse_1") == "Reuse 1"


def test_disks_with_a_limit_exceeded_get_a_warning_and_a_worse_tone():
    disks = _storage({"disks": [
        {"id": "a", "name": "Laufwerk 1", "status": "normal", "smart_status": "normal", "temp": 30},
        {"id": "b", "name": "Laufwerk 2", "status": "normal", "smart_status": "normal", "exceed_bad_sector_thr": True},
        {"id": "c", "name": "Laufwerk 3", "status": "normal", "smart_status": "normal", "below_remain_life_thr": True},
        {"id": "d", "name": "Laufwerk 4", "status": "normal", "smart_status": "failing"},
        {"id": "e", "name": "Laufwerk 5", "status": "crashed", "smart_status": "normal"},
        {"id": "f", "name": "Laufwerk 6", "status": "not_use"},
        {"id": "g", "status": "normal", "exceed_bad_sector_thr": True, "below_remain_life_thr": True},
    ]}).disks
    assert [(d.name, d.tone, d.warning, d.smart_tone) for d in disks] == [
        ("Laufwerk 1", "good", None, "good"),
        ("Laufwerk 2", "warn", "Zu viele defekte Sektoren", "good"),
        ("Laufwerk 3", "warn", "Restlebensdauer unter dem Grenzwert", "good"),
        ("Laufwerk 4", "danger", None, "danger"),
        ("Laufwerk 5", "danger", None, "good"),
        ("Laufwerk 6", "neutral", None, "neutral"),
        ("g", "warn", "Zu viele defekte Sektoren; Restlebensdauer unter dem Grenzwert", "neutral"),
    ]
    assert disks[0].smart_label == "In Ordnung" and disks[3].smart_label == "Fällt aus" and disks[5].smart_label is None


def test_the_disk_model_names_the_vendor_once():
    disks = _storage({"disks": [
        {"id": "a", "name": "A", "model": "WD40EFRX", "vendor": "WDC"},
        {"id": "b", "name": "B", "model": "WDC WD40EFRX", "vendor": "WDC"},
        {"id": "c", "name": "C", "vendor": "Seagate"},
        {"id": "d", "name": "D", "model": "ST4000"},
        {"id": "e", "name": "E", "temp": 0},
    ]}).disks
    assert [d.model for d in disks] == ["WDC WD40EFRX", "WDC WD40EFRX", "Seagate", "ST4000", None]
    assert disks[4].temp is None


@pytest.mark.parametrize("body", [{}, [], None, "x", {"volumes": "viele"}])
def test_storage_that_is_not_a_list_is_refused(body):
    assert parse.parse_storage(body) is None


def test_storage_without_disks_or_volumes_is_valid_when_the_other_is_a_list():
    assert parse.parse_storage({"volumes": []}) == parse.Storage([], [], [])
    found = _storage({"disks": [5, "x", {"id": "a", "name": "A"}], "volumes": [None, {"status": "normal"}]})
    volumes, disks = found.volumes, found.disks
    assert volumes == [] and [d.name for d in disks] == ["A"]


def test_update_is_read_from_update_or_from_data_itself():
    assert parse.parse_update(UPDATE) == parse.Update(available=True, version="DSM 7.2.2-72806")
    assert parse.parse_update({"available": True, "version": "DSM 7.3"}) == parse.Update(available=True, version="DSM 7.3")
    assert parse.parse_update({"update": {"available": False}}) == parse.Update(available=False, version=None)
    assert parse.parse_update({"update": {"available": True}}) == parse.Update(available=True, version=None)
    for bad in ({}, {"update": {}}, {"update": {"available": "ja"}}, [], None):
        assert parse.parse_update(bad) is None


def test_text_helpers_use_german_notation():
    assert [widgets.size_text(n) for n in (0, 512, 1536, 412 * GIB, int(3.6 * TIB), 500 * GIB)] == ["0 B", "512 B", "1,5 KB", "412 GB", "3,6 TB", "500 GB"]
    assert [widgets.duration_text(s) for s in (0, 59, 60, 3600, 4320, 86400, 90000, 3 * 86400 + 4 * 3600)] == [
        "< 1 Min.", "< 1 Min.", "1 Min.", "1 Std.", "1 Std. 12 Min.", "1 Tag", "1 Tag 1 Std.", "3 Tage 4 Std.",
    ]


# --- Senden


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
            yield b"x" * 100_000

    http = _Http(lambda request: httpx.Response(200, content=body()))
    with pytest.raises(transport.ResponseTooLarge) as caught:
        await transport.send(http, "GET", f"{BASE}/webapi/x", max_bytes=2 * 1024 * 1024)
    assert sent < 100 and "mehr als 2 MB" in str(caught.value) and "Sie wird nicht gelesen" in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.asyncio
async def test_transport_passes_the_tls_choice_and_the_form_and_never_sends_a_shared_cookie():
    http = _Http(lambda request: httpx.Response(200, text="{}"))
    await transport.send(http, "POST", f"{BASE}/webapi/entry.cgi", insecure_tls=True, form={"a": "b"})
    await transport.send(http, "GET", f"{BASE}/webapi/entry.cgi")
    assert http.calls[0]["insecure_tls"] is True and http.calls[0]["data"] == {"a": "b"} and http.calls[0]["pinned_sha256"] is None
    assert http.calls[1]["insecure_tls"] is False and "data" not in http.calls[1]
    assert all(c["headers"]["Cookie"] == "nodvard_deck=1" for c in http.calls)


@pytest.mark.asyncio
async def test_transport_gives_up_after_the_total_time_limit():
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    with pytest.raises(transport.Unreachable) as caught:
        await transport.send(_Http(slow), "GET", f"{BASE}/webapi/x", total_timeout_s=0.05)
    assert "Zeitüberschreitung" in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "state", "fragment"),
    [
        (httpx.ConnectError("[Errno 111] Connection refused"), "unreachable", "lehnt die Verbindung ab"),
        (httpx.ConnectError("[Errno -2] Name or service not known"), "unreachable", "lässt sich nicht auflösen"),
        (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate"), "error", "Selbstsigniertes Zertifikat erlauben"),
        (httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER] wrong version number"), "error", "Port 5001 für https"),
        (httpx.RemoteProtocolError("illegal status line: b'GET /geheim'"), "error", "abgebrochen oder eine fehlerhafte Antwort"),
        (httpx.ReadTimeout("timed out"), "unreachable", "Zeitüberschreitung"),
        (httpx.ConnectError("irgendwas"), "unreachable", "nicht erreichbar"),
    ],
)
async def test_transport_error_sentences(error, state, fragment):
    async def boom(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(transport.DsmError) as caught:
        await transport.send(_Http(boom), "GET", f"{BASE}/webapi/entry.cgi?_sid=GEHEIMESID")
    assert caught.value.state == state and fragment in str(caught.value)
    assert "geheim" not in str(caught.value).lower() and "GEHEIMESID" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.asyncio
async def test_transport_says_when_the_core_refuses_the_target():
    class Refusing:
        def stream(self, method, url, **kwargs):
            raise PermissionDenied("synology", "net.outbound:nas.test")

    with pytest.raises(transport.DsmError) as caught:
        await transport.send(Refusing(), "GET", f"{BASE}/webapi/x?_sid=GEHEIMESID")
    assert "nicht erlaubt" in str(caught.value) and "GEHEIMESID" not in str(caught.value)


@pytest.mark.asyncio
async def test_transport_knows_a_changed_certificate():
    async def boom(request: httpx.Request) -> httpx.Response:
        raise CertificateMismatch("AB:CD", "EF:01")

    with pytest.raises(transport.DsmError) as caught:
        await transport.send(_Http(boom), "GET", f"{BASE}/webapi/x")
    assert "anderes Zertifikat" in str(caught.value)


def test_the_log_filter_hides_the_session_in_http_logs():
    transport.install_log_filter()
    transport.install_log_filter()
    logger = logging.getLogger("httpx")
    assert len([f for f in logger.filters if getattr(f, "nodvard_synology", False)]) == 1, "nur einmal eingehängt"
    record = logging.LogRecord(
        "httpx", logging.INFO, __file__, 1, 'HTTP Request: %s %s "%s %d %s"',
        ("GET", httpx.URL(f"{BASE}/webapi/entry.cgi?api=SYNO.DSM.Info&_sid=GEHEIMESID&x=1"), "HTTP/1.1", 200, "OK"), None,
    )
    assert logger.filter(record)
    text = record.getMessage()
    assert "GEHEIMESID" not in text and "_sid=…&x=1" in text and "api=SYNO.DSM.Info" in text
    plain = logging.LogRecord("httpx", logging.INFO, __file__, 1, "ohne Sitzung %s", ("x",), None)
    assert logger.filter(plain) and plain.getMessage() == "ohne Sitzung x"
    logger.removeFilter(next(f for f in logger.filters if getattr(f, "nodvard_synology", False)))


# --- Das nachgebaute DSM


class FakeDsm:
    """DSM mit Schnittstellenliste, Anmeldung (POST, Formular), Sitzungs-ID in der Adresse und den vier Lese-Aufrufen."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.insecure_requests: list[httpx.Request] = []
        self.error: Exception | None = None
        self.overrides: dict[str, Callable[[httpx.Request], Any]] = {}  # nach Schnittstelle oder Pfad
        self.api_errors: dict[str, int] = {}  # Fehlercode je Schnittstelle bei den Lese-Aufrufen
        self.login_error: int | None = None
        self.username, self.password = USER, PASSWORD
        self.apis: dict[str, Any] = json.loads(json.dumps(APIS))
        self.data: dict[str, Any] = {
            "SYNO.DSM.Info": json.loads(json.dumps(INFO)), "SYNO.Core.System.Utilization": json.loads(json.dumps(UTILIZATION)),
            "SYNO.Storage.CGI.Storage": json.loads(json.dumps(STORAGE)), "SYNO.Core.Upgrade.Server": json.loads(json.dumps(UPDATE)),
        }
        self.prefix = ""  # Unterpfad, unter dem DSM hinter einem Proxy liegt
        self.sessions: set[str] = set()
        self.issued: list[str] = []
        self.logins = 0
        self.failed_logins = 0
        self.max_failures = 10
        self.backups: dict[str, dict[str, Any]] = {}  # Hyper Backup: Kennung -> {"name", "status"}
        self.backup_total: int | None = None  # `total` in `list`, wenn DSM mehr kennt, als es schickt
        self.before: Callable[[httpx.Request], Any] | None = None  # wartet vor der Antwort, etwa um Gleichzeitigkeit zu zählen

    def install_hyper_backup(self, tasks: dict[str, tuple[str, dict[str, Any]]]) -> None:
        """Hyper Backup mit diesen Aufgaben (Kennung -> (Name, Antwort von `status`))."""
        self.apis["SYNO.Backup.Task"] = {"path": "entry.cgi", "minVersion": 1, "maxVersion": 2}
        self.backups = {task_id: {"name": name, "status": status} for task_id, (name, status) in tasks.items()}

    async def handler(self, request: httpx.Request) -> Any:
        await asyncio.sleep(0)  # wie im Netz: andere Teile kommen dazwischen
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.before is not None:
            await self.before(request)
        return self.answer(request)

    async def insecure_handler(self, request: httpx.Request) -> Any:
        self.insecure_requests.append(request)
        return await self.handler(request)

    @staticmethod
    def params(request: httpx.Request) -> dict[str, str]:
        if request.method == "POST":
            return dict(parse_qsl(request.content.decode("utf-8")))
        return dict(request.url.params)

    def answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(self.prefix)
        params = self.params(request)
        api = params.get("api", "")
        for key in (api, path):
            if key in self.overrides:
                return self.overrides[key](request)
        if path == QUERY and api == "SYNO.API.Info":
            wanted = params.get("query", "").split(",")
            return _ok({name: self.apis[name] for name in wanted if name in self.apis})
        if path != ENTRY:
            return httpx.Response(404, text="Not Found")
        info = self.apis.get(api)
        version = params.get("version", "")
        if info is not None and not (version.isdigit() and info["minVersion"] <= int(version) <= info["maxVersion"]):
            return _fail(104)
        if api == "SYNO.API.Auth":
            if request.method == "POST" and params.get("method") == "login":
                return self._login(params)
            if request.method == "GET" and params.get("method") == "logout":
                self.sessions.discard(params.get("_sid", ""))
                return _ok({})
            return _fail(103)
        if request.method != "GET":
            return httpx.Response(405, text="Method Not Allowed")
        if api == "SYNO.Backup.Task" and info is not None:
            return self._backup(params)
        if api not in DATA_CALLS:
            return _fail(102)
        if params.get("method") != DATA_CALLS[api]:
            return _fail(103)
        if params.get("_sid") not in self.sessions:
            return _fail(119)
        if api in self.api_errors:
            return _fail(self.api_errors[api])
        return _ok(self.data[api])

    def _backup(self, params: dict[str, str]) -> httpx.Response:
        if params.get("_sid") not in self.sessions:
            return _fail(119)
        if "SYNO.Backup.Task" in self.api_errors:
            return _fail(self.api_errors["SYNO.Backup.Task"])
        if params.get("method") == "list":
            tasks = [{"task_id": int(i), "name": t["name"], "state": "backupable"} for i, t in self.backups.items()]
            return _ok({"task_list": tasks, "total": len(tasks) if self.backup_total is None else self.backup_total})
        if params.get("method") == "status" and params.get("task_id") in self.backups:
            assert "last_bkp_result" in params.get("additional", ""), "die Zeiten kommen nur auf Nachfrage"
            return _ok(self.backups[params["task_id"]]["status"])
        return _fail(4401)

    def _login(self, params: dict[str, str]) -> httpx.Response:
        if self.login_error is not None:
            self.failed_logins += 1
            return _fail(self.login_error)
        if params.get("account") == self.username and params.get("passwd") == self.password and params.get("format") == "sid":
            self.logins += 1
            sid = f"sid{len(self.issued) + 1:03d}" + "AbCdEfGhIjKlMnOpQrStUvWxYz-_01"
            self.issued.append(sid)
            self.sessions.add(sid)
            return _ok({"sid": sid})
        self.failed_logins += 1
        return _fail(400)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def calls(self) -> list[tuple[str, str]]:
        """(Schnittstelle, Methode) jeder Anfrage in der Reihenfolge des Eingangs."""
        return [(self.params(r).get("api", ""), self.params(r).get("method", "")) for r in self.requests]

    def posts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]

    def expire_sessions(self) -> None:
        self.sessions.clear()


async def _login(client, username="owner1", password="correct-horse-battery") -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _enable(client, db_session, test_settings) -> tuple[dict, FakeDsm]:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    headers = await _login(client)
    enabled = await client.post(f"/api/v1/extensions/{EXT}/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text

    fake = FakeDsm()
    http = get_extension_runtime().loaded[EXT].ctx.http
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    http._insecure_client = httpx.AsyncClient(transport=httpx.MockTransport(fake.insecure_handler))
    return headers, fake


def _entry(name: str = DEVICE, *, url: str | None = BASE, username: str | None = USER, **more: Any) -> dict[str, Any]:
    """Ein Gerät für `connections`; `None` lässt das Feld weg."""
    entry = {"name": name, "base_url": url, "username": username, **more}
    return {key: value for key, value in entry.items() if value is not None}


_DEVICE_FIELDS = ("tls_insecure_skip_verify", "tls_fingerprint_sha256")


async def _put_settings(client, headers, values: dict[str, Any]) -> None:
    saved = await client.put(f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": values}, headers=headers)
    assert saved.status_code == 200, saved.text


async def _configure(
    client, headers, *, url: str | None = BASE, username: str | None = USER, password: str | None = PASSWORD, name: str = DEVICE,
    **more: Any,
) -> None:
    """Richtet das eine Gerät der Einzeltests ein; `tls_*` gehört zum Gerät, alles andere zu den gemeinsamen Einstellungen."""
    entry = _entry(name, url=url, username=username, **{k: v for k, v in more.items() if k in _DEVICE_FIELDS})
    values = {"connections": [entry], **{k: v for k, v in more.items() if k not in _DEVICE_FIELDS}}
    if username is None:
        # Die Oberfläche lässt den Benutzernamen nicht weg; so bleibt der Zustand „Benutzername fehlt“ erreichbar.
        ctx = get_extension_runtime().loaded[EXT].ctx
        await ctx.settings.set({**await ctx.settings.get(), **values})
    else:
        await _put_settings(client, headers, values)
    if password is not None:
        await _set_password(client, headers, password, name)


async def _set_password(client, headers, password: str, name: str = DEVICE) -> None:
    res = await client.put(
        f"/api/v1/extensions/{EXT}/secrets",
        json={"current_password": "correct-horse-battery", "label": f"synology-password:{name}", "value": password}, headers=headers,
    )
    assert res.status_code == 204, res.text


async def _overview(client, headers, query: str = "") -> dict:
    res = await client.get(f"{API}/overview{query}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def _widget(client, headers, name: str = "status") -> dict:
    res = await client.get(f"{API}/widgets/{name}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _service():
    return get_extension_runtime().loaded[EXT].instance._service


async def _health():
    loaded = get_extension_runtime().loaded[EXT]
    return await loaded.instance.health(loaded.ctx)


def _no_secret_in(fake: FakeDsm, *texts: str) -> None:
    everything = "\n".join(texts)
    for secret in (PASSWORD, *fake.issued):
        assert secret not in everything
    assert "Beispiel&Pass" not in everything and "Beispiel%26Pass" not in everything


# --- Einbindung


@pytest.mark.asyncio
async def test_extension_is_discovered_disabled_beta_and_registers_page_and_three_widgets(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    record = await db_session.get(ExtensionRecord, EXT)
    assert record is not None and record.state == "disabled"

    headers, _ = await _enable(client, db_session, test_settings)
    assert record.state == "enabled", record.last_error
    row = next(r for r in (await client.get("/api/v1/extensions", headers=headers)).json() if r["id"] == EXT)
    assert (row["name"], row["stage"], row["category"]) == ("Synology DSM", "beta", "servers")

    pages = (await client.get("/api/v1/pages", headers=headers)).json()
    page = next(p for p in pages if p["ext_id"] == EXT)
    assert (page["path"], page["component"], page["title"], page["stage"]) == ("/synology", "SynologyPage", "Synology DSM", "beta")
    widgets = [w for w in (await client.get("/api/v1/widgets", headers=headers)).json() if w["ext_id"] == EXT]
    assert sorted(w["id"] for w in widgets) == ["backups", "status", "volumes"]
    assert {w["data_endpoint"] for w in widgets} == {"widgets/backups", "widgets/status", "widgets/volumes"}
    assert all(w["view"]["item"]["badge"] == {"text": "{{ label }}", "tone": "{{ tone }}"} for w in widgets)

    bundle = await client.get(f"/api/v1/extensions/{EXT}/frontend/index.js")
    assert bundle.status_code == 200
    assert "SynologyPage" in bundle.text


@pytest.mark.asyncio
async def test_manifest_asks_for_reading_only(client, db_session, test_settings):
    import tomllib

    manifest = tomllib.loads((REPO_EXTENSIONS_DIR / EXT / "extension.toml").read_text(encoding="utf-8"))["extension"]
    assert manifest["stage"] == "beta"
    assert manifest["permissions"] == [
        "net.outbound", "net.outbound.insecure_tls", "secrets.read:synology-*", "schedule.register", "notify.send",
    ]
    assert manifest["description"].endswith("Nur lesend.")
    await _enable(client, db_session, test_settings)
    registered = get_extension_runtime().actions._specs
    assert [t for t, (ext_id, _spec) in registered.items() if ext_id == EXT] == [], "keine Aktion, nichts zum Freigeben"


@pytest.mark.asyncio
async def test_the_schema_has_its_settings_and_a_vault_password_bound_to_the_address():
    schema = json.loads((REPO_EXTENSIONS_DIR / EXT / "settings.schema.json").read_text(encoding="utf-8"))
    assert set(schema["properties"]) == {"connections", "notify", "notify_update", "disk_temp_warn_c"}
    device = schema["properties"]["connections"]["items"]
    assert set(device["properties"]) == {"name", "base_url", "username", "tls_insecure_skip_verify", "tls_fingerprint_sha256"}
    assert device["required"] == ["name", "base_url", "username"]
    assert schema["properties"]["connections"]["maxItems"] == 10
    assert device["properties"]["tls_insecure_skip_verify"]["default"] is False
    assert device["properties"]["tls_fingerprint_sha256"]["x-advanced"] is True
    assert schema["properties"]["notify"]["default"] is True and schema["properties"]["notify_update"]["default"] is True
    assert schema["properties"]["disk_temp_warn_c"]["x-advanced"] is True
    assert [(s["label"], s["x-secret-bound-to"], s["per_item"]) for s in schema["x-secrets"]] == [
        ("synology-password:{name}", ["base_url"], "connections"),
    ]
    assert "administrators" in device["properties"]["username"]["description"]
    assert "Zwei-Faktor" in device["properties"]["username"]["description"]
    assert "eigenes" in device["properties"]["tls_insecure_skip_verify"]["description"]


# --- Nicht eingerichtet


@pytest.mark.asyncio
async def test_nothing_configured_is_reported_cleanly(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    body = await _overview(client, headers)
    assert body["state"] == "not_configured" and body["url"] is None
    assert body["message"] == "Synology DSM ist noch nicht eingerichtet. Unter Einstellungen → Erweiterungen → Synology DSM eintragen."
    assert all(body[k] is None for k in ("system", "load", "volumes", "disks", "update", "system_note", "load_note", "storage_note", "update_note"))

    widget = await _widget(client, headers)
    assert widget["meta"]["state"] == "not_configured"
    assert widget["data"] == [{
        "title": "Synology DSM nicht eingerichtet", "subtitle": body["message"], "label": "nicht eingerichtet", "tone": "neutral",
    }]
    volumes = await _widget(client, headers, "volumes")
    assert volumes["meta"]["state"] == "not_configured" and volumes["data"][0]["label"] == "nicht eingerichtet"
    health = await _health()
    assert health.healthy is False and "noch nicht eingerichtet" in health.message
    assert fake.requests == [], "ohne Einstellungen wird nichts angefragt"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("username", "password", "fragment"),
    [(None, PASSWORD, "fehlt der Benutzername"), (USER, None, "fehlt das Passwort"), (None, None, "fehlen Benutzername und Passwort")],
)
async def test_an_address_without_credentials_is_not_set_up_and_sends_nothing(client, db_session, test_settings, username, password, fragment):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, username=username, password=password)
    body = await _overview(client, headers)
    assert body["state"] == "not_configured" and fragment in body["message"] and body["url"] == BASE
    assert (await _widget(client, headers))["meta"]["state"] == "not_configured"
    health = await _health()
    assert health.healthy is False and fragment in health.message
    assert fake.requests == []

    # Das Passwort nachtragen gilt sofort, auch innerhalb der 20 Sekunden des Zwischenspeichers.
    if username is not None:
        await _set_password(client, headers, PASSWORD)
        assert (await _overview(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_a_bad_address_is_an_error_not_a_missing_setup(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.ctx.settings.set({"connections": [_entry(url="https://nutzer:" + "geheim@nas.test:5001")]})
    body = await _overview(client, headers)
    assert body["state"] == "error" and "ungültig" in body["message"] and "geheim" not in json.dumps(body)
    assert body["url"] is None
    widget = await _widget(client, headers)
    assert widget["meta"]["state"] == "error" and widget["data"][0]["label"] == "Fehler" and widget["data"][0]["tone"] == "warn"
    assert (await _health()).healthy is False
    assert fake.requests == []


# --- Der volle Ablauf


@pytest.mark.asyncio
async def test_full_overview_shows_model_version_load_volumes_disks_and_update(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)

    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["state"], body["message"], body["url"]) == ("ok", None, BASE)
    assert body["system"] == {
        "model": "DS220+", "dsm_version": "DSM 7.2.1-69057 Update 5", "uptime_seconds": 3 * 86400 + 4 * 3600 + 120,
        "temperature": 42, "temperature_warn": False, "ram_mb": 2048, "clock": "2026-10-17T14:05",
    }
    assert body["load"] == {"cpu_percent": 12.0, "ram_percent": 41.0}
    assert [(v["id"], v["name"], v["status"], v["tone"], v["used"], v["total"], v["percent"]) for v in body["volumes"]] == [
        ("volume_1", "Volume 1", "normal", "good", 442381631488, 3958241859994, 11),
        ("volume_2", "Volume 2", "normal", "good", 950 * GIB, 1000 * GIB, 95),
    ]
    assert [(d["name"], d["model"], d["status"], d["temp"], d["tone"], d["smart_status"], d["warning"]) for d in body["disks"]] == [
        ("Laufwerk 1", "WDC WD40EFRX-68N32N0", "normal", 36, "good", "normal", None),
        ("Laufwerk 2", "Seagate ST4000VN006", "normal", 39, "good", "normal", None),
    ]
    assert body["update"] == {"available": True, "version": "DSM 7.2.2-72806"}
    assert all(body[k] is None for k in ("system_note", "load_note", "storage_note", "update_note"))
    assert "SERIAL" not in res.text, "die Seriennummer verlässt DSM nicht"

    widget = await _widget(client, headers)
    assert widget["data"] == [
        {"title": "DS220+", "subtitle": "DSM 7.2.1-69057 Update 5 · Laufzeit 3 Tage 4 Std.", "label": "online", "tone": "good"},
        {"title": "Auslastung", "subtitle": "CPU 12 % · RAM 41 %", "label": "normal", "tone": "good"},
        {"title": "Temperatur", "subtitle": "42 °C", "label": "in Ordnung", "tone": "good"},
        {"title": "DSM-Update verfügbar", "subtitle": "DSM 7.2.2-72806", "label": "Update", "tone": "warn"},
    ]
    assert widget["meta"] == {
        "state": "ok", "problems": 0, "model": "DS220+", "dsm_version": "DSM 7.2.1-69057 Update 5", "update_available": True,
    }
    volumes = await _widget(client, headers, "volumes")
    assert volumes["data"] == [
        {"title": "Volume 1", "subtitle": "412 GB von 3,6 TB belegt (11 %)", "label": "Normal", "tone": "good"},
        {"title": "Volume 2", "subtitle": "950 GB von 1000 GB belegt (95 %)", "label": "fast voll", "tone": "warn"},
    ]
    assert volumes["meta"] == {"state": "ok", "volumes": 2}

    # Das Passwort und die Sitzung stehen nirgends: weder in Antworten noch in Einstellungen, Protokoll oder Audit.
    settings = (await client.get(f"/api/v1/extensions/{EXT}/settings", headers=headers)).json()
    assert {s["label"]: s["is_set"] for s in settings["secrets"]} == {"synology-password:synology": True}
    audit = (await db_session.execute(select(AuditEntry))).scalars().all()
    _no_secret_in(fake, res.text, json.dumps(widget), json.dumps(settings), caplog.text, json.dumps([a.detail or {} for a in audit], default=str))
    assert "SERIAL" not in caplog.text


@pytest.mark.asyncio
async def test_the_first_requests_find_the_paths_log_in_by_post_and_read_with_the_session(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.apis["SYNO.API.Auth"] = {"path": "auth.cgi", "minVersion": 1, "maxVersion": 7}
    fake.apis["SYNO.DSM.Info"] = {"path": "info.cgi", "minVersion": 1, "maxVersion": 1}
    # Eigene Pfade: dieses DSM antwortet an anderen Dateien.
    fake.overrides["/webapi/auth.cgi"] = lambda request: FakeDsm._login(fake, FakeDsm.params(request)) if request.method == "POST" else _ok({})
    fake.overrides["/webapi/info.cgi"] = lambda request: _ok(INFO) if FakeDsm.params(request).get("_sid") in fake.sessions else _fail(119)
    await _configure(client, headers)
    assert (await _overview(client, headers))["state"] == "ok"

    first = fake.requests[0]
    assert (first.method, first.url.path) == ("GET", QUERY)
    assert dict(first.url.params) == {
        "api": "SYNO.API.Info", "version": "1", "method": "query",
        "query": (
            "SYNO.API.Auth,SYNO.DSM.Info,SYNO.Core.System.Utilization,SYNO.Storage.CGI.Storage,SYNO.Core.Upgrade.Server,"
            "SYNO.Backup.Task"
        ),
    }
    assert str(first.url).endswith("SYNO.Core.Upgrade.Server,SYNO.Backup.Task")

    login = fake.requests[1]
    assert (login.method, login.url.path) == ("POST", "/webapi/auth.cgi")
    assert login.url.query == b"", "weder Passwort noch Benutzername in der Adresse"
    assert FakeDsm.params(login) == {
        "api": "SYNO.API.Auth", "version": "6", "method": "login", "account": USER, "passwd": PASSWORD,
        "session": "NodvardDeck", "format": "sid",
    }
    assert PASSWORD not in str(login.url) and USER not in str(login.url)
    assert login.headers["content-type"].startswith("application/x-www-form-urlencoded")

    reads = {FakeDsm.params(r)["api"]: r for r in fake.requests[2:]}
    sid = fake.issued[0]
    assert {api: (r.method, r.url.path) for api, r in reads.items()} == {
        "SYNO.DSM.Info": ("GET", "/webapi/info.cgi"), "SYNO.Core.System.Utilization": ("GET", ENTRY),
        "SYNO.Storage.CGI.Storage": ("GET", ENTRY), "SYNO.Core.Upgrade.Server": ("GET", ENTRY),
    }
    wanted = {"SYNO.DSM.Info": "1", "SYNO.Core.System.Utilization": "1", "SYNO.Storage.CGI.Storage": "1", "SYNO.Core.Upgrade.Server": "3"}
    for api, request in reads.items():
        params = FakeDsm.params(request)
        # Version = kleinere aus der von DSM gemeldeten und unserer Höchstversion.
        assert params == {"api": api, "version": wanted[api], "method": DATA_CALLS[api], "_sid": sid}, api
        assert not request.content and PASSWORD not in str(request.url)
    assert PASSWORD not in "".join(str(r.url) for r in fake.requests)


@pytest.mark.asyncio
async def test_versions_follow_the_smaller_of_the_reported_and_the_wanted_maximum(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.apis["SYNO.API.Auth"]["maxVersion"] = 3
    fake.apis["SYNO.DSM.Info"]["maxVersion"] = 5
    await _configure(client, headers)
    await _overview(client, headers)
    versions = {FakeDsm.params(r)["api"]: FakeDsm.params(r)["version"] for r in fake.requests[1:]}
    assert versions == {
        "SYNO.API.Auth": "3", "SYNO.DSM.Info": "2", "SYNO.Core.System.Utilization": "1", "SYNO.Storage.CGI.Storage": "1",
        "SYNO.Core.Upgrade.Server": "3",
    }


@pytest.mark.asyncio
async def test_the_session_is_reused_and_a_refresh_does_not_log_in_again(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    await _overview(client, headers)
    assert fake.logins == 1
    before = len(fake.requests)
    await _overview(client, headers)
    assert len(fake.requests) == before, "20 Sekunden lang aus dem Zwischenspeicher"
    await _overview(client, headers, "?refresh=true")
    await _widget(client, headers)
    await _widget(client, headers, "volumes")
    assert fake.logins == 1 and len(fake.posts()) == 1
    assert fake.calls().count(("SYNO.API.Info", "query")) == 1, "die Schnittstellenliste wird nur einmal geholt"
    assert len({FakeDsm.params(r).get("_sid") for r in fake.requests if r.method == "GET"} - {None}) == 1
    assert not any(r.url.path.endswith("logout") or FakeDsm.params(r).get("method") == "logout" for r in fake.requests), "kein Abmelden nach jeder Abfrage"


@pytest.mark.asyncio
async def test_the_widgets_and_the_page_share_one_set_of_requests(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    await asyncio.gather(_widget(client, headers), _widget(client, headers, "volumes"), _overview(client, headers))
    assert fake.logins == 1
    assert sorted(fake.calls()).count(("SYNO.Storage.CGI.Storage", "load_info")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [106, 107, 119])
async def test_an_expired_session_logs_in_exactly_once_more_and_repeats_the_call(client, db_session, test_settings, code):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    assert (await _overview(client, headers))["state"] == "ok"
    fake.expire_sessions()
    if code != 119:
        fake.overrides["SYNO.DSM.Info"] = lambda request: (
            _fail(code) if FakeDsm.params(request).get("_sid") == fake.issued[0] else _ok(INFO)
        )
    body = await _overview(client, headers, "?refresh=true")
    assert body["state"] == "ok" and body["system"]["model"] == "DS220+" and body["volumes"]
    assert fake.logins == 2 and len(fake.issued) == 2
    assert all(body[k] is None for k in ("system_note", "load_note", "storage_note", "update_note"))


@pytest.mark.asyncio
async def test_parallel_parts_with_an_expired_session_share_one_new_login(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    await _overview(client, headers)
    fake.expire_sessions()
    assert (await _overview(client, headers, "?refresh=true"))["state"] == "ok"
    assert fake.logins == 2, "vier Teile, ein Login"


@pytest.mark.asyncio
async def test_a_session_that_is_refused_even_after_a_new_login_is_not_retried_again(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.overrides["SYNO.Core.Upgrade.Server"] = lambda request: _fail(119)
    body = await _overview(client, headers)
    assert body["state"] == "ok" and body["update"] is None and body["system"] is not None
    assert body["update_note"]["kind"] == "error" and "nimmt die Sitzung nicht an" in body["update_note"]["message"]
    assert fake.calls().count(("SYNO.Core.Upgrade.Server", "check")) == 2, "einmal wiederholt, nicht öfter"
    assert fake.logins == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "fragment"),
    [
        (400, "Benutzername oder Passwort stimmt nicht."),
        (401, "Das Konto ist deaktiviert."),
        (402, "Das Konto darf sich nicht anmelden."),
        (403, "Dieses Konto verlangt Zwei-Faktor; bitte ein eigenes Konto ohne Zwei-Faktor für Nodvard Deck anlegen"),
        (404, "Zwei-Faktor-Code abgelehnt."),
        (406, "Dieses Konto verlangt Zwei-Faktor; bitte ein eigenes Konto ohne Zwei-Faktor für Nodvard Deck anlegen"),
        (407, "Die Adresse von Nodvard Deck ist in DSM gesperrt (automatische Sperre)."),
        (408, "Das Passwort ist abgelaufen oder muss geändert werden."),
        (409, "Das Passwort ist abgelaufen oder muss geändert werden."),
        (410, "Das Passwort ist abgelaufen oder muss geändert werden."),
    ],
)
async def test_every_login_error_code_has_a_german_sentence_and_pauses(client, db_session, test_settings, code, fragment):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.login_error = code
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "auth_failed" and body["message"].startswith(fragment)
    assert body["system"] is None and body["volumes"] is None
    assert fake.failed_logins == 1 and fake.calls() == [("SYNO.API.Info", "query"), ("SYNO.API.Auth", "login")]
    assert str(code) not in body["message"], "kein Fehlercode im Satz, sondern was zu tun ist"

    # Die Pause: auch „Aktualisieren“ und die Kacheln fragen nicht noch einmal (jeder Fehlversuch zählt in DSM).
    await _overview(client, headers, "?refresh=true")
    widget = await _widget(client, headers)
    assert fake.failed_logins == 1
    assert widget["meta"]["state"] == "error"
    assert widget["data"] == [{"title": "Synology DSM: Anmeldung fehlgeschlagen", "subtitle": body["message"], "label": "Anmeldung fehlgeschlagen", "tone": "danger"}]
    health = await _health()
    assert health.healthy is False and health.message.startswith(f"{DEVICE}: {fragment}")
    assert fake.failed_logins == 2, "„Verbindung testen“ hebt die Pause auf"


@pytest.mark.asyncio
async def test_a_wrong_password_pauses_for_thirty_minutes_and_stays_below_the_dsm_block_limit(client, db_session, test_settings):
    assert syn_service.AUTH_FAILED_TTL_S == 1800.0
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, password="falsch")
    service = _service()
    now = [5000.0]
    service._clock = lambda: now[0]

    assert (await _overview(client, headers))["state"] == "auth_failed"
    for _ in range(29):
        now[0] += 60.0
        assert (await _overview(client, headers, "?refresh=true"))["state"] == "auth_failed"
        assert (await client.get(f"{API}/widgets/status", headers=headers)).status_code == 200
    assert fake.failed_logins == 1, "29 Minuten lang kein weiterer Fehlversuch"
    now[0] += 61.0
    await _overview(client, headers)
    assert fake.failed_logins == 2, "nach 30 Minuten wird wieder gefragt"

    now[0] += syn_service.AUTH_FAILED_TTL_S + 1
    await _set_password(client, headers, PASSWORD)
    assert (await _overview(client, headers))["state"] == "ok"


@pytest.mark.asyncio
async def test_changed_details_of_the_device_end_its_pause(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, password="falsch")
    assert (await _overview(client, headers))["state"] == "auth_failed"
    fake.password = "falsch"
    saved = await client.put(
        f"/api/v1/extensions/{EXT}/settings",
        json={"current_password": "correct-horse-battery", "values": {"connections": [_entry(DEVICE, tls_insecure_skip_verify=True)]}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert not saved.json().get("secrets_cleared"), "dieselbe Adresse: das Passwort bleibt"
    assert (await _overview(client, headers))["state"] == "ok"
    assert fake.logins == 1


@pytest.mark.asyncio
async def test_a_new_password_in_the_vault_ends_the_pause_at_once(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, password="falsch")
    assert (await _overview(client, headers))["state"] == "auth_failed"
    await _set_password(client, headers, PASSWORD)
    assert (await _overview(client, headers))["state"] == "ok"
    assert (fake.failed_logins, fake.logins) == (1, 1)


@pytest.mark.asyncio
async def test_a_new_password_in_the_vault_logs_the_old_session_out(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    assert (await _overview(client, headers))["state"] == "ok"
    assert len(fake.sessions) == 1
    await _set_password(client, headers, "falsch")
    assert fake.sessions == set(), "die alte Sitzung ist abgemeldet"
    assert (await _overview(client, headers))["state"] == "auth_failed", "nicht weiter mit der Sitzung von vorher"


@pytest.mark.asyncio
async def test_an_answer_with_the_old_password_is_not_kept(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    service, loaded = _service(), get_extension_runtime().loaded[EXT]
    load, entered, release, loads = service._load_bounded, asyncio.Event(), asyncio.Event(), []

    async def slow(cfg, *args):
        loads.append(cfg.name)
        if len(loads) == 1:
            entered.set()
            await release.wait()
        return await load(cfg, *args)

    service._load_bounded = slow
    first = asyncio.create_task(_overview(client, headers))
    await entered.wait()
    await loaded.instance.on_secret_changed(loaded.ctx, f"synology-password:{DEVICE}")
    release.set()
    await first
    await _overview(client, headers)
    assert loads == [DEVICE, DEVICE], "was die Abfrage mit dem alten Passwort einträgt, gilt nicht mehr"


@pytest.mark.asyncio
async def test_the_ban_of_the_address_pauses_too(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.login_error = 407
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "auth_failed" and "gesperrt" in body["message"] and "30 Minuten" in body["message"]
    await _overview(client, headers, "?refresh=true")
    assert fake.failed_logins == 1


@pytest.mark.asyncio
async def test_an_unknown_login_error_also_pauses_further_attempts(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.login_error = 411
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "auth_failed"
    assert body["message"].startswith("Synology DSM hat die Anmeldung mit Fehler 411 abgelehnt. Danach „Verbindung testen“")
    await _overview(client, headers, "?refresh=true")
    assert fake.failed_logins == 1, "jede Ablehnung kann bei DSM als Fehlversuch zählen"


@pytest.mark.asyncio
async def test_an_unknown_login_method_stays_a_plain_error(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.login_error = 103
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "error" and "kennt die Anmeldung in dieser Form nicht (Fehler 103)" in body["message"]


# --- Teile scheitern einzeln


@pytest.mark.asyncio
async def test_missing_rights_for_the_storage_keep_the_rest_and_say_what_to_do(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.api_errors["SYNO.Storage.CGI.Storage"] = 105
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "ok"
    assert body["system"]["model"] == "DS220+" and body["load"]["cpu_percent"] == 12.0 and body["update"]["available"] is True
    assert body["volumes"] is None and body["disks"] is None
    assert body["storage_note"] == {
        "kind": "no_rights",
        "message": "Diesem Konto fehlen Rechte. Für Speicher braucht das Konto Administratorrechte (Mitglied der Gruppe „administrators“).",
    }
    assert body["system_note"] is None and body["load_note"] is None and body["update_note"] is None

    rows = (await _widget(client, headers, "volumes"))["data"]
    assert rows == [{"title": "Speicher", "subtitle": body["storage_note"]["message"], "label": "Rechte fehlen", "tone": "warn"}]
    assert (await _widget(client, headers))["meta"]["state"] == "ok", "die Kachel „Status“ bleibt nutzbar"


@pytest.mark.asyncio
async def test_missing_rights_for_the_update_check_keep_the_rest(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.api_errors["SYNO.Core.Upgrade.Server"] = 105
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["update"] is None and body["volumes"] and body["system"]
    assert body["update_note"]["kind"] == "no_rights" and "Für Updates braucht das Konto Administratorrechte" in body["update_note"]["message"]
    rows = (await _widget(client, headers))["data"]
    assert rows[-1] == {"title": "Updates", "subtitle": body["update_note"]["message"], "label": "Rechte fehlen", "tone": "warn"}


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [102, 103, 104])
async def test_an_unknown_api_method_or_version_means_not_available_on_this_dsm(client, db_session, test_settings, code):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.api_errors["SYNO.Core.Upgrade.Server"] = code
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "ok" and body["update"] is None and body["storage_note"] is None
    assert body["update_note"] == {"kind": "unavailable", "message": "Auf diesem DSM nicht verfügbar."}
    assert (await _widget(client, headers))["data"][-1]["label"] == "nicht verfügbar"


@pytest.mark.asyncio
async def test_an_api_the_list_does_not_name_is_not_available_and_is_not_asked(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    del fake.apis["SYNO.Core.Upgrade.Server"]
    fake.apis["SYNO.API.Auth"] = {"path": "entry.cgi", "minVersion": 1, "maxVersion": 7}
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "ok" and body["system"] and body["load"] and body["volumes"]
    assert body["update_note"]["kind"] == "unavailable"
    assert ("SYNO.Core.Upgrade.Server", "check") not in fake.calls()


@pytest.mark.asyncio
async def test_a_newer_dsm_without_our_version_is_asked_with_its_oldest(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.apis["SYNO.Storage.CGI.Storage"] = {"path": "entry.cgi", "minVersion": 2, "maxVersion": 3}
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["storage_note"] is None and [v["name"] for v in body["volumes"]] == ["Volume 1", "Volume 2"]
    storage = next(FakeDsm.params(r) for r in fake.requests if FakeDsm.params(r).get("api") == "SYNO.Storage.CGI.Storage")
    assert storage["version"] == "2"


def test_versions_dsm_only_knows_older_than_ours_are_not_used():
    info = parse.ApiInfo("entry.cgi", 1, 1)
    assert syn_client.pick_version("SYNO.API.Auth", info) is None, "Anmelden mit format=sid geht erst ab Version 2"
    assert syn_client.pick_version("SYNO.Core.Upgrade.Server", parse.ApiInfo("entry.cgi", 1, 9)) == 4
    assert syn_client.pick_version("SYNO.Core.Upgrade.Server", parse.ApiInfo("entry.cgi", 6, 9)) == 6


@pytest.mark.asyncio
async def test_a_part_that_fails_alone_does_not_hide_the_others(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.overrides["SYNO.Core.System.Utilization"] = lambda request: httpx.Response(500, text="Text des Servers: bitte nach evil.example.org gehen")
    fake.overrides["SYNO.DSM.Info"] = lambda request: _ok({"unerwartet": 1})
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "ok" and body["system"] is None and body["load"] is None
    assert body["volumes"] and body["update"]
    assert "(HTTP 500)" in body["load_note"]["message"] and body["load_note"]["kind"] == "error"
    assert "nicht das erwartete Format" in body["system_note"]["message"]
    assert "evil.example.org" not in json.dumps(body), "Text des fremden Servers steht nie in der Meldung"
    rows = (await _widget(client, headers))["data"]
    assert [r["title"] for r in rows] == ["Synology DSM", "Auslastung", "DSM-Update verfügbar"]
    assert rows[0] == {"title": "Synology DSM", "subtitle": body["system_note"]["message"], "label": "Fehler", "tone": "warn"}


@pytest.mark.asyncio
async def test_when_every_part_fails_the_whole_thing_is_an_error(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    for api in DATA_CALLS:
        fake.api_errors[api] = 105
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "error" and "Administratorrechte" in body["message"]
    assert body["system"] is None and body["system_note"] is None
    assert (await _widget(client, headers))["meta"]["state"] == "error"


@pytest.mark.asyncio
async def test_an_account_that_may_only_read_the_device_info_still_gets_a_page(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    for api in ("SYNO.Core.System.Utilization", "SYNO.Storage.CGI.Storage", "SYNO.Core.Upgrade.Server"):
        fake.api_errors[api] = 105
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "ok" and body["system"]["model"] == "DS220+"
    assert {k: body[k]["kind"] for k in ("load_note", "storage_note", "update_note")} == {k: "no_rights" for k in ("load_note", "storage_note", "update_note")}
    assert (await _health()).healthy is True


# --- Fehlerbilder der Verbindung


@pytest.mark.asyncio
async def test_not_dsm_at_this_address(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.overrides[QUERY] = lambda request: httpx.Response(200, text="<html><body>Willkommen bei Meine Seite evil.example.org</body></html>")
    body = await _overview(client, headers)
    assert body["state"] == "error" and "nicht das erwartete Format" in body["message"] and "5000" in body["message"]
    assert "evil.example.org" not in body["message"]

    await _service().clear()
    fake.overrides[QUERY] = lambda request: httpx.Response(404, text="nicht gefunden")
    body = await _overview(client, headers)
    assert body["state"] == "error" and "läuft kein Synology DSM" in body["message"]

    await _service().clear()
    fake.overrides[QUERY] = lambda request: _ok({"SYNO.DSM.Info": APIS["SYNO.DSM.Info"]})
    body = await _overview(client, headers)
    assert body["state"] == "error" and "Anmelde-Schnittstelle fehlt" in body["message"]
    assert fake.posts() == [], "ohne Anmelde-Schnittstelle wird nichts gesendet"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "fragment"),
    [(429, "bremst Anfragen"), (502, "gerade nicht bereit"), (503, "gerade nicht bereit"), (500, "meldet einen Fehler (HTTP 500)"), (418, "abgelehnt (HTTP 418)")],
)
@pytest.mark.parametrize("where", [QUERY, ENTRY])
async def test_other_status_codes_have_fixed_sentences(client, db_session, test_settings, status, fragment, where):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.overrides[where] = lambda request: httpx.Response(status, text="Text des Servers: bitte nach evil.example.org gehen")
    body = await _overview(client, headers)
    assert body["state"] == "error" and fragment in body["message"]
    assert "evil.example.org" not in body["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("where", [QUERY, ENTRY])
@pytest.mark.parametrize(
    ("location", "sentence"),
    [
        ("https://angreifer.example/login", "leitet auf eine andere Adresse um (HTTP 302)."),
        # Ungültiger Punycode-Name: httpx wirft einen `ValueError`, dessen Text Teile des fremden Namens nennt.
        ("http://xn--angreifer-ey9f.example/login", "leitet auf eine andere Adresse um."),
    ],
)
async def test_redirects_are_reported_never_followed_and_never_show_the_foreign_address(client, db_session, test_settings, caplog, location, sentence, where):
    caplog.set_level(logging.INFO)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.overrides[where] = lambda request: httpx.Response(302, headers={"Location": location})

    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "error"
    assert body["message"].startswith(f"Synology DSM {sentence}")
    assert "Port 5000 für http und 5001 für https" in body["message"]
    assert "angreifer" not in (res.text + caplog.text).lower()
    assert all(r.url.host == "nas.test" for r in fake.requests), "der Weiterleitung wird nicht gefolgt"
    _no_secret_in(fake, res.text, caplog.text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "state", "fragment"),
    [
        (httpx.ReadTimeout("timed out"), "unreachable", "antwortet nicht (Zeitüberschreitung)"),
        (httpx.ConnectTimeout("timed out"), "unreachable", "antwortet nicht (Zeitüberschreitung)"),
        (httpx.ConnectError("[Errno 111] Connection refused"), "unreachable", "lehnt die Verbindung ab"),
        (httpx.ConnectError("[Errno -2] Name or service not known"), "unreachable", "lässt sich nicht auflösen"),
        (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), "error", "Schalte in den Einstellungen „Selbstsigniertes Zertifikat erlauben“ ein"),
        (httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER] wrong version number"), "error", "Verschlüsselung passt nicht zum Port"),
    ],
)
async def test_network_and_tls_errors_are_clean_states(client, db_session, test_settings, error, state, fragment):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.error = error
    body = await _overview(client, headers)
    assert body["state"] == state and fragment in body["message"] and "Synology DSM" in body["message"]
    assert body["system"] is None
    assert fake.paths() == [QUERY], "ist der Server nicht erreichbar, hilft keine zweite Anfrage"

    widget = await _widget(client, headers)
    assert widget["meta"]["state"] == "error"
    assert widget["data"][0]["subtitle"] == body["message"] and widget["data"][0]["tone"] in ("danger", "warn")
    assert (await _widget(client, headers, "volumes"))["data"][0]["subtitle"] == body["message"]
    health = await _health()
    assert health.healthy is False and body["message"] in health.message


@pytest.mark.asyncio
async def test_the_server_vanishing_after_the_login_is_unreachable(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)

    def gone(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 111] Connection refused")

    fake.overrides.update({api: gone for api in DATA_CALLS})
    body = await _overview(client, headers)
    assert body["state"] == "unreachable" and "lehnt die Verbindung ab" in body["message"]


@pytest.mark.asyncio
async def test_a_text_that_quotes_the_password_or_the_session_never_reaches_the_user(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.error = httpx.ConnectError(f"boom {PASSWORD} sid001AbCdEfGhIjKlMnOpQrStUvWxYz-_01 boom")
    res = await client.get(f"{API}/overview", headers=headers)
    assert res.json()["state"] == "unreachable"
    health = await _health()
    assert PASSWORD not in res.text + caplog.text + json.dumps(health.model_dump(), default=str)
    assert "sid001AbCdEf" not in res.text + caplog.text

    fake.error = None
    await _service().clear()
    assert (await _overview(client, headers))["state"] == "ok"
    fake.error = httpx.ConnectError(f"boom {fake.issued[-1]}")
    res = await client.get(f"{API}/overview?refresh=true", headers=headers)
    _no_secret_in(fake, res.text, caplog.text, json.dumps((await _health()).model_dump(), default=str))


@pytest.mark.asyncio
async def test_a_part_that_quotes_the_password_in_its_answer_is_scrubbed(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.overrides["SYNO.Core.Upgrade.Server"] = lambda request: httpx.Response(200, json={"success": False, "error": {"code": 100, "errors": PASSWORD}})
    body = await _overview(client, headers)
    assert body["update_note"]["message"] == "Synology DSM hat die Anfrage abgelehnt (Fehler 100)."
    _no_secret_in(fake, json.dumps(body))


@pytest.mark.asyncio
async def test_answers_that_are_not_dsm_are_refused_without_a_traceback(client, db_session, test_settings, caplog):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    for api in DATA_CALLS:
        fake.overrides[api] = lambda request: httpx.Response(200, text="[[[[" * 100_000)
    body = await _overview(client, headers)
    assert body["state"] == "error" and "nicht das erwartete Format" in body["message"]
    assert "Traceback" not in caplog.text

    await _service().clear()
    for api in DATA_CALLS:
        fake.overrides[api] = lambda request: httpx.Response(200, json={"success": "ja", "data": {}})
    assert (await _overview(client, headers))["state"] == "error"


@pytest.mark.asyncio
async def test_a_login_without_a_usable_sid_is_explained(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.overrides["SYNO.API.Auth"] = lambda request: _ok({"sid": "x"})
    body = await _overview(client, headers)
    assert body["state"] == "error" and "nicht das erwartete Format" in body["message"]


@pytest.mark.asyncio
async def test_oversized_answers_are_refused_while_streaming(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)

    async def endless():
        for _ in range(100):
            yield b"x" * 100_000

    fake.overrides["SYNO.Core.System.Utilization"] = lambda request: httpx.Response(200, content=endless())
    body = await _overview(client, headers)
    assert body["state"] == "ok" and body["load"] is None and "ungewöhnlich große Antwort" in body["load_note"]["message"]
    assert body["system"] and body["volumes"]


# --- Adresse, TLS, Geheimnis


@pytest.mark.asyncio
async def test_the_address_from_the_browser_works_and_a_proxy_path_stays(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, url="https://nas.test:5001/webman/index.cgi?launchApp=x#/desktop")
    body = await _overview(client, headers)
    assert body["state"] == "ok" and body["url"] == BASE
    assert {r.url.path for r in fake.requests} == {QUERY, ENTRY} and {r.url.port for r in fake.requests} == {5001}

    await _service().clear()
    assert fake.calls()[-1] == ("SYNO.API.Auth", "logout") and not fake.sessions, "die alte Sitzung wird abgemeldet"
    fake.requests.clear()
    fake.prefix = "/nas"
    await _configure(client, headers, url="https://proxy.test/nas/webman/", password=None)
    await _set_password(client, headers, PASSWORD)
    body = await _overview(client, headers)
    assert body["state"] == "ok" and body["url"] == "https://proxy.test/nas"
    assert {r.url.path for r in fake.requests} == {f"/nas{QUERY}", f"/nas{ENTRY}"}


@pytest.mark.asyncio
async def test_tls_exception_is_used_only_when_the_check_is_switched_off(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    await _overview(client, headers)
    assert fake.requests and fake.insecure_requests == [], "Standard: Zertifikat wird geprüft"

    await _service().clear()
    fake.requests.clear()
    await _configure(client, headers, tls_insecure_skip_verify=True, password=None)
    assert (await _overview(client, headers))["state"] == "ok"
    assert fake.insecure_requests and len(fake.insecure_requests) == len(fake.requests), "mit „aus“ geht jede Anfrage ohne Prüfung"

    await _service().clear()
    fake.requests.clear()
    fake.insecure_requests.clear()
    await _configure(client, headers, tls_insecure_skip_verify=False, password=None)
    await _overview(client, headers)
    assert fake.requests and fake.insecure_requests == []


@pytest.mark.asyncio
async def test_changing_the_address_removes_the_password_so_it_never_goes_to_a_new_target(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    assert (await _overview(client, headers))["state"] == "ok"
    saved = await client.put(
        f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": [_entry(url="https://angreifer.test:5001")]}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["secrets_cleared"] == ["synology-password:synology"]
    fake.requests.clear()
    body = await _overview(client, headers)
    assert body["state"] == "not_configured" and "fehlt das Passwort" in body["message"]
    assert fake.requests == []


@pytest.mark.asyncio
async def test_an_unreadable_or_empty_password_is_a_hint_not_a_crash(client, db_session, test_settings, monkeypatch):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    service = _service()

    async def broken(label: str):
        raise RuntimeError(f"Tresor kaputt: {PASSWORD}")

    monkeypatch.setattr(service._ctx.secrets, "get_handle", broken)
    body = await _overview(client, headers)
    assert body["state"] == "error" and "ließ sich nicht aus dem Tresor lesen" in body["message"] and PASSWORD not in json.dumps(body)
    assert fake.requests == []


@pytest.mark.asyncio
async def test_unexpected_errors_do_not_become_a_500(client, db_session, test_settings, caplog, monkeypatch):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers)

    async def boom(*args: Any, **kwargs: Any):
        raise RuntimeError(f"unerwartet {PASSWORD}")

    monkeypatch.setattr(_service(), "_load_bounded", boom)
    res = await client.get(f"{API}/overview", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "error" and body["message"] == "Bei der Abfrage von synology ist ein unerwarteter Fehler aufgetreten."
    assert PASSWORD not in res.text


# --- Kacheln

def _overview_model(**over: Any):
    base: dict[str, Any] = {
        "state": "ok", "message": None, "url": BASE, "system": None, "load": None, "volumes": None, "disks": None, "update": None,
        "system_note": None, "load_note": None, "storage_note": None, "update_note": None,
    }
    return models.Overview(**{**base, **over})


def test_volume_widget_warns_above_ninety_percent_and_keeps_a_bad_status():
    def vol(name: str, status: str, percent: int | None, used: int = 90 * GIB, total: int = 100 * GIB):
        return parse.Volume(
            id=name, name=name, status=status, status_label=parse.status_label(status), tone=parse.tone_for(status), fs_type=None,
            used=used, total=total, percent=percent,
        )

    rows = widgets.volume_rows(_overview_model(volumes=[
        vol("A", "normal", 90), vol("B", "normal", 91), vol("C", "crashed", 20), vol("D", "degraded", 99),
        vol("E", "normal", None, used=0, total=0),
    ]))
    assert [(r["title"], r["label"], r["tone"]) for r in rows] == [
        ("A", "Normal", "good"), ("B", "fast voll", "warn"), ("C", "Ausgefallen", "danger"), ("D", "Eingeschränkt", "warn"),
        ("E", "Normal", "good"),
    ]
    assert rows[0]["subtitle"] == "90 GB von 100 GB belegt (90 %)" and rows[4]["subtitle"] == "Belegung unbekannt"


def test_volume_widget_names_a_failing_disk_and_says_when_there_are_no_volumes():
    disks = _storage(STORAGE).disks
    bad = disks[1].model_copy(update={"tone": "danger", "status": "crashed", "status_label": "Ausgefallen", "warning": None})
    rows = widgets.volume_rows(_overview_model(volumes=[], disks=[disks[0], bad]))
    assert rows == [
        {"title": "Keine Volumes", "subtitle": "DSM meldet keinen Speicherplatz.", "label": "–", "tone": "neutral"},
        {"title": "Festplatte Laufwerk 2", "subtitle": "Seagate ST4000VN006 · Ausgefallen", "label": "Ausgefallen", "tone": "danger"},
    ]


def test_volume_widget_never_shows_more_than_ten_rows():
    volumes = [
        parse.Volume(id=f"volume_{i}", name=f"Volume {i}", status="normal", status_label="Normal", tone="good", fs_type=None, used=1, total=2, percent=50)
        for i in range(1, 15)
    ]
    rows = widgets.volume_rows(_overview_model(volumes=volumes))
    assert len(rows) == widgets.MAX_ROWS and rows[-1]["title"] == "… und 5 weitere Zeilen"


def test_status_widget_marks_a_hot_device_and_a_busy_one():
    system = parse.parse_system_info({**INFO, "temperature": 71, "temperature_warn": True})
    load = parse.parse_utilization({"cpu": {"user_load": 95}, "memory": {"real_usage": 40}})
    rows = widgets.status_rows(_overview_model(system=system, load=load, update=parse.Update(available=False, version=None)))
    assert [(r["title"], r["label"], r["tone"]) for r in rows] == [
        ("DS220+", "online", "good"), ("Auslastung", "hoch", "warn"), ("Temperatur", "zu warm", "danger"), ("DSM ist aktuell", "aktuell", "good"),
    ]
    assert rows[1]["subtitle"] == "CPU 95 % · RAM 40 %"


@pytest.mark.asyncio
async def test_the_widget_meta_state_is_not_configured_error_or_ok(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    assert (await _widget(client, headers))["meta"]["state"] == "not_configured"
    await _configure(client, headers)
    fake.error = httpx.ConnectError("[Errno 111] Connection refused")
    assert (await _widget(client, headers))["meta"]["state"] == "error", "eingetragen, aber kaputt: bleibt sichtbar"
    fake.error = None
    await _service().clear()
    assert (await _widget(client, headers))["meta"]["state"] == "ok"


# --- Rechte, Beschreibung

@pytest.mark.asyncio
async def test_routes_need_a_login_and_hosts_read_and_there_is_nothing_to_write(client, db_session, test_settings):
    from nodvard_deck.core import security
    from nodvard_deck.models import Role, RolePermission, User

    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    for path in ("/overview", "/widgets/status", "/widgets/volumes"):
        assert (await client.get(f"{API}{path}")).status_code == 401, path
    assert fake.requests == []

    roles = {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=headers)).json()}
    created = await client.post("/api/v1/users", json={"username": "gast", "password": "gast-passwort-1", "role_ids": [roles["viewer"]]}, headers=headers)
    assert created.status_code == 201, created.text
    login = await client.post("/api/v1/auth/login", json={"username": "gast", "password": "gast-passwort-1"})
    viewer = {"Authorization": f"Bearer {login.json()['access_token']}"}
    for path in ("/overview", "/widgets/status", "/widgets/volumes"):
        assert (await client.get(f"{API}{path}", headers=viewer)).status_code == 200, path

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
    for path in ("/overview", "/widgets/status", "/widgets/volumes", "/overview?refresh=true"):
        assert (await client.get(f"{API}{path}", headers=nobody)).status_code == 403, path
    assert len(fake.requests) == before

    for method in ("post", "put", "delete", "patch"):
        res = await getattr(client, method)(f"{API}/overview", headers=headers)
        assert res.status_code in (404, 405), (method, res.status_code)


def test_openapi_describes_the_overview_with_typed_fields():
    schema = models.Overview.model_json_schema()
    assert set(schema["properties"]) == {
        "device", "state", "message", "url", "system", "load", "volumes", "disks", "pools", "update", "backups",
        "system_note", "load_note", "storage_note", "update_note", "backup_note", "problems", "config_problem",
    }
    assert set(schema["$defs"]) >= {"Note", "SystemInfo", "Load", "Volume", "Disk", "Pool", "Update", "BackupTask", "Problem"}
    assert set(schema["$defs"]["SystemInfo"]["properties"]) == {
        "model", "dsm_version", "uptime_seconds", "temperature", "temperature_warn", "ram_mb", "clock",
    }
    assert "serial" not in json.dumps(schema).lower()
    assert set(schema["$defs"]["Load"]["properties"]) == {"cpu_percent", "ram_percent"}
    assert set(schema["$defs"]["Volume"]["properties"]) == {
        "id", "name", "status", "status_label", "tone", "fs_type", "used", "total", "percent", "pool_id",
    }
    assert set(schema["$defs"]["Disk"]["properties"]) == {
        "id", "name", "model", "kind", "status", "status_label", "tone", "temp", "temp_warn", "smart_status", "smart_label",
        "smart_tone", "warning",
    }
    assert set(schema["$defs"]["Pool"]["properties"]) == {
        "id", "name", "description", "raid", "status", "status_label", "tone", "disks", "volumes", "total",
    }
    assert set(schema["$defs"]["BackupTask"]["properties"]) == {
        "id", "name", "state", "running", "status_read", "last_result", "result_label", "tone", "last_run", "last_success",
        "next_run", "overdue", "problem",
    }
    assert set(schema["$defs"]["Problem"]["properties"]) == {"key", "subject", "severity", "title", "text"}
    assert set(schema["$defs"]["Update"]["properties"]) == {"available", "version"}
    assert set(schema["$defs"]["Note"]["properties"]) == {"kind", "message"}


@pytest.mark.asyncio
async def test_openapi_of_the_running_app_lists_the_routes_with_their_model(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    paths = (await client.get("/api/v1/system/openapi.json", headers=headers)).json()["paths"]
    path = paths[f"{API}/overview"]["get"]
    assert path["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/Overview")
    assert [p["name"] for p in path["parameters"]] == ["refresh", "device"]
    assert set(paths[f"{API}/overview"]) == {"get"}
    assert set(paths[f"{API}/widgets/status"]) == {"get"} and set(paths[f"{API}/widgets/volumes"]) == {"get"}
    devices = paths[f"{API}/devices"]["get"]
    assert devices["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/DeviceList")
    assert [p["name"] for p in devices["parameters"]] == ["refresh"]
    for kind in ("status", "volumes", "backups"):
        named = paths[f"{API}/widgets/{kind}/{{device}}"]["get"]
        assert [p["name"] for p in named["parameters"]] == ["device"], kind


# --- Verbindung testen


@pytest.mark.asyncio
async def test_testing_the_connection_asks_the_paths_logs_in_and_reads_the_device_info_only(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    health = await _health()
    assert health.healthy is True and health.message == f"{DEVICE}: in Ordnung"
    assert health.details == {DEVICE: {"healthy": True}}
    assert fake.calls() == [("SYNO.API.Info", "query"), ("SYNO.API.Auth", "login"), ("SYNO.DSM.Info", "getinfo")]


@pytest.mark.asyncio
async def test_testing_the_connection_names_each_problem_with_a_fixed_sentence(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    health = await _health()
    assert health.healthy is False and health.message == "Synology DSM ist noch nicht eingerichtet. Unter Einstellungen → Erweiterungen → Synology DSM eintragen."
    assert health.details is None or health.details == {}

    await _configure(client, headers, password="falsch")
    health = await _health()
    assert health.healthy is False and health.message.startswith(f"{DEVICE}: Benutzername oder Passwort stimmt nicht.")
    error = health.details[DEVICE]["error"]
    assert health.details[DEVICE] == {"healthy": False, "error": error, "verbatim": True} and error.startswith("Benutzername oder Passwort")

    await _set_password(client, headers, PASSWORD)
    fake.error = httpx.ReadTimeout("timed out")
    health = await _health()
    assert health.healthy is False and "Zeitüberschreitung" in health.message


@pytest.mark.asyncio
async def test_testing_the_connection_treats_missing_rights_as_a_working_connection(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.api_errors["SYNO.DSM.Info"] = 105
    assert (await _health()).healthy is True
    fake.api_errors["SYNO.DSM.Info"] = 100
    assert (await _health()).healthy is False


@pytest.mark.asyncio
async def test_testing_the_connection_survives_surprises(client, db_session, test_settings, monkeypatch):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers)

    async def boom(*args: Any, **kwargs: Any):
        raise RuntimeError(f"unerwartet {PASSWORD}")

    monkeypatch.setattr(_service(), "client", boom)
    health = await _health()
    assert health.healthy is False and health.message == f"{DEVICE}: Beim Testen von {DEVICE} ist ein unerwarteter Fehler aufgetreten."
    assert PASSWORD not in json.dumps(health.model_dump(), default=str)

    monkeypatch.setattr(_service(), "test_connections", boom)
    health = await _health()
    assert health.healthy is False and "RuntimeError" in health.message and PASSWORD not in health.message


# --- App-Vorschlag


@pytest.mark.asyncio
async def test_the_address_is_suggested_as_an_app_without_any_request(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    assert (await client.get("/api/v1/apps/suggestions", headers=headers)).json()["suggestions"] == []

    await _configure(client, headers, url="http://203.0.113.20:5000/webman/index.cgi", password=None)
    res = await client.get("/api/v1/apps/suggestions", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json() == {
        "suggestions": [{
            "name": "Synology DSM", "url": "http://203.0.113.20:5000", "source": "Synology DSM", "host": None, "service_id": None,
        }],
        "errors": [],
    }
    assert fake.requests == [], "ein Vorschlag liest weder das Passwort noch ruft er DSM auf"


@pytest.mark.asyncio
async def test_an_unusable_address_is_not_suggested(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.ctx.settings.set({"base_url": "https://nutzer:" + "passwort@nas.test:5001"})
    suggestions = (await client.get("/api/v1/apps/suggestions", headers=headers)).json()["suggestions"]
    assert suggestions == [] and "passwort" not in json.dumps(suggestions)


# --- Beenden und nur lesen


@pytest.mark.asyncio
async def test_stopping_the_extension_logs_the_session_out_once_and_never_fails(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.instance.on_stop(loaded.ctx)
    assert fake.requests == [], "ohne Sitzung gibt es nichts abzumelden"

    await _configure(client, headers)
    await _overview(client, headers)
    sid = fake.issued[0]
    assert fake.sessions == {sid}
    fake.requests.clear()
    await loaded.instance.on_stop(loaded.ctx)
    assert [(r.method, r.url.path) for r in fake.requests] == [("GET", ENTRY)]
    assert FakeDsm.params(fake.requests[0]) == {"api": "SYNO.API.Auth", "version": "6", "method": "logout", "session": "NodvardDeck", "_sid": sid}
    assert fake.sessions == set()
    fake.requests.clear()
    await loaded.instance.on_stop(loaded.ctx)
    assert fake.requests == [], "die Sitzung wird nur einmal abgemeldet"

    await _overview(client, headers, "?refresh=true")
    fake.error = httpx.ConnectError("[Errno 111] Connection refused")
    await loaded.instance.on_stop(loaded.ctx)


@pytest.mark.asyncio
async def test_logging_out_gives_up_quickly_and_sends_nothing_without_a_session():
    box = syn_client.SessionBox()
    seen: list[httpx.Request] = []

    async def hang(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        await asyncio.sleep(60)
        return httpx.Response(200)

    await syn_client.logout(_Http(hang), box, timeout_s=0.05)
    assert seen == []
    box.sid = "abcdEFGH1234"
    box.target = syn_client.Target(BASE, False, parse.ApiInfo("entry.cgi", 1, 7))
    await asyncio.wait_for(syn_client.logout(_Http(hang), box, timeout_s=0.05), 5)
    assert [r.method for r in seen] == ["GET"] and "_sid=abcdEFGH1234" in str(seen[0].url)
    assert box.sid is None


def test_only_get_requests_and_the_one_login_post_exist():
    source = REPO_EXTENSIONS_DIR / "synology" / "src" / "nodvard_deck_ext_synology"
    text = "".join(p.read_text(encoding="utf-8") for p in source.glob("*.py"))
    # `secrets.delete(` räumt eine Passwort-Kopie im Tresor auf und ist kein HTTP-Aufruf.
    assert not re.search(r"(?<!secrets)\.(post|put|patch|delete|request)\(", text)
    assert not re.search(r"""["'](PUT|PATCH|DELETE)["']""", text)
    posts = [line for line in text.splitlines() if '"POST"' in line]
    assert len(posts) == 2, posts  # die Prüfung in `_send` und die Anmeldung
    assert '"POST"' not in (source / "transport.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), [("POST", "/webapi/entry.cgi"), ("PUT", "/webapi/entry.cgi"), ("DELETE", "/webapi/entry.cgi"), ("GET", "/api/v1/x"), ("POST ", "/webapi/x")])
async def test_the_client_refuses_to_send_anything_else(method, path):
    http = _Http(lambda request: httpx.Response(200))
    client = syn_client.SynologyClient(http, base_url=BASE, username=USER, password=PASSWORD, box=syn_client.SessionBox())
    with pytest.raises(ValueError):
        await client._send(method, path)
    assert http.calls == []


READ_CALLS = {
    ("SYNO.API.Info", "query"), ("SYNO.API.Auth", "login"), ("SYNO.API.Auth", "logout"), ("SYNO.DSM.Info", "getinfo"),
    ("SYNO.Core.System.Utilization", "get"), ("SYNO.Storage.CGI.Storage", "load_info"), ("SYNO.Core.Upgrade.Server", "check"),
    ("SYNO.Backup.Task", "list"), ("SYNO.Backup.Task", "status"),
}


@pytest.mark.asyncio
async def test_everything_that_was_sent_was_a_read_or_the_one_login(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.install_hyper_backup({"1": ("Nacht", OK_TASK), "2": ("Woche", OK_TASK)})
    await _configure(client, headers)
    await _overview(client, headers)
    await _widget(client, headers)
    await _widget(client, headers, "volumes")
    await _health()
    fake.expire_sessions()
    await _overview(client, headers, "?refresh=true")
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.instance.on_stop(loaded.ctx)
    sent = {(r.method, r.url.path) for r in fake.requests}
    assert sent == {("GET", QUERY), ("GET", ENTRY), ("POST", ENTRY)}
    assert set(fake.calls()) <= READ_CALLS
    assert {("SYNO.Backup.Task", "list"), ("SYNO.Backup.Task", "status")} <= set(fake.calls())
    assert all(not r.content for r in fake.requests if r.method == "GET")
    assert all(r.url.path.startswith("/webapi/") for r in fake.requests)
    assert all(r.headers["cookie"] == "nodvard_deck=1" for r in fake.requests)


@pytest.mark.asyncio
async def test_no_password_and_no_session_in_any_log_line_even_at_debug_level(client, db_session, test_settings, caplog, monkeypatch):
    # Frühere Tests können den Logger von httpx abgeschaltet (Alembic, `fileConfig`) oder auf WARNING gestellt haben.
    monkeypatch.setattr(logging.getLogger("httpx"), "disabled", False)
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="httpx")
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    await _overview(client, headers)
    fake.expire_sessions()
    await _overview(client, headers, "?refresh=true")
    await _health()
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.instance.on_stop(loaded.ctx)
    assert any("HTTP Request" in r.getMessage() for r in caplog.records), "httpx hat die Anfragen protokolliert"
    assert "_sid=" in caplog.text and "_sid=…" in caplog.text
    _no_secret_in(fake, caplog.text)
    assert len(fake.issued) >= 2


def test_the_cache_keeps_an_answer_for_twenty_seconds():
    assert syn_service.CACHE_TTL_S == 20.0


# --- DSM 7.1.1 und 7.4.1

# Schnittstellenliste wie bei DSM 7.1 und 7.2 (so meldet sie auch die Bibliothek hinter Home Assistant).
DSM_711_APIS = {
    "SYNO.API.Auth": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 7},
    "SYNO.DSM.Info": {"path": "entry.cgi", "minVersion": 2, "maxVersion": 2},
    "SYNO.Core.System.Utilization": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 1},
    "SYNO.Storage.CGI.Storage": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 1},
    "SYNO.Core.Upgrade.Server": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 4},
    "SYNO.Backup.Task": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 2},
}
# Für 7.4.1 angenommen: höhere Höchstversionen, und ältere Versionen fallen weg.
DSM_741_APIS = {
    "SYNO.API.Auth": {"path": "entry.cgi", "minVersion": 3, "maxVersion": 7},
    "SYNO.DSM.Info": {"path": "entry.cgi", "minVersion": 2, "maxVersion": 3},
    "SYNO.Core.System.Utilization": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 2},
    "SYNO.Storage.CGI.Storage": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 2},
    "SYNO.Core.Upgrade.Server": {"path": "entry.cgi", "minVersion": 2, "maxVersion": 5},
    "SYNO.Backup.Task": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 3},
}
# Und ein DSM, das nur noch die neueste Version jeder Schnittstelle kennt.
DSM_NEWEST_ONLY = {name: {**info, "minVersion": info["maxVersion"]} for name, info in DSM_741_APIS.items()}

OK_TASK = {
    "state": "backupable", "status": "none", "last_bkp_result": "done", "last_bkp_time": "2026/10/17 03:00",
    "last_bkp_end_time": "2026/10/17 03:40", "last_bkp_success_time": "2026/10/17 03:40", "next_bkp_time": "2026/10/18 03:00",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("apis", "version_string", "model", "expected"),
    [
        (DSM_711_APIS, "DSM 7.1.1-42962 Update 9", "Älteres Gerät", {
            "SYNO.API.Auth": "6", "SYNO.DSM.Info": "2", "SYNO.Core.System.Utilization": "1", "SYNO.Storage.CGI.Storage": "1",
            "SYNO.Core.Upgrade.Server": "4", "SYNO.Backup.Task": "1",
        }),
        (DSM_741_APIS, "DSM 7.4.1-90080 Update 1", "Neueres Gerät", {
            "SYNO.API.Auth": "6", "SYNO.DSM.Info": "2", "SYNO.Core.System.Utilization": "1", "SYNO.Storage.CGI.Storage": "1",
            "SYNO.Core.Upgrade.Server": "4", "SYNO.Backup.Task": "1",
        }),
        (DSM_NEWEST_ONLY, "DSM 7.4.1-90080 Update 1", "Neueres Gerät", {
            "SYNO.API.Auth": "7", "SYNO.DSM.Info": "3", "SYNO.Core.System.Utilization": "2", "SYNO.Storage.CGI.Storage": "2",
            "SYNO.Core.Upgrade.Server": "5", "SYNO.Backup.Task": "3",
        }),
    ],
    ids=["dsm-7.1.1", "dsm-7.4.1", "nur-neueste"],
)
async def test_dsm_711_and_741_are_read_completely(client, db_session, test_settings, apis, version_string, model, expected):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.apis = json.loads(json.dumps(apis))
    fake.install_hyper_backup({"1": ("Nachtsicherung", OK_TASK)})
    fake.apis["SYNO.Backup.Task"] = dict(apis["SYNO.Backup.Task"])
    fake.data["SYNO.DSM.Info"] = {**INFO, "version_string": version_string, "model": model}
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "ok"
    assert [body[k] for k in ("system_note", "load_note", "storage_note", "update_note", "backup_note")] == [None] * 5
    assert (body["system"]["model"], body["system"]["dsm_version"]) == (model, version_string)
    assert [t["name"] for t in body["backups"]] == ["Nachtsicherung"]
    used = {}
    for request in fake.requests[1:]:
        params = FakeDsm.params(request)
        used.setdefault(params["api"], params["version"])
    assert used == expected


# --- Speicherpools


POOLS_STORAGE = {
    "volumes": [
        {"id": "volume_1", "status": "degraded", "pool_path": "reuse_1", "size": {"total": "4000", "used": "1000"}},
        {"id": "volume_2", "status": "normal", "pool_path": "reuse_2", "size": {"total": "4000", "used": "1000"}},
    ],
    "disks": [
        {"id": "sata1", "name": "Laufwerk 1", "status": "normal", "smart_status": "normal", "temp": 34, "isSsd": False},
        {"id": "sata2", "name": "Laufwerk 2", "status": "crashed", "smart_status": "failing", "temp": 36},
    ],
    "storagePools": [
        {
            "id": "reuse_1", "num_id": 1, "desc": "", "device_type": "shr_with_1_disk_protect", "status": "degraded",
            "disks": ["sata1", "sata2"], "pool_child": [{"id": "volume_1"}], "size": {"total": "8000", "used": "8000"},
        },
        {
            "id": "reuse_2", "num_id": 2, "desc": "Fotos", "device_type": "raid_5", "status": "normal", "disks": ["sata3"],
            "pool_child": [{"id": "volume_2"}], "size": {"total": "4000"},
        },
    ],
}


def test_pools_are_read_with_their_kind_disks_and_volumes():
    pools = _storage(POOLS_STORAGE).pools
    assert [(p.id, p.name, p.description, p.raid, p.status_label, p.tone, p.disks, p.volumes, p.total) for p in pools] == [
        ("reuse_1", "Speicherpool 1", None, "SHR", "Eingeschränkt", "warn", 2, ["Volume 1"], 8000),
        ("reuse_2", "Speicherpool 2", "Fotos", "RAID 5", "Normal", "good", 1, ["Volume 2"], 4000),
    ]
    assert _storage(POOLS_STORAGE).volumes[0].pool_id == "reuse_1"
    assert _storage({"volumes": []}).pools == [], "DSM ohne Pools (sehr alt) ist kein Fehler"


@pytest.mark.parametrize(
    ("device_type", "label"),
    [("shr_without_disk_protect", "SHR ohne Ausfallschutz"), ("shr_with_2_disk_protect", "SHR-2"), ("raid_1", "RAID 1"),
     ("raid_10", "RAID 10"), ("raid_f1", "RAID F1"), ("basic", "Basic"), ("jbod", "JBOD"), ("etwas_neues", "Etwas neues"),
     (None, None)],
)
def test_the_kind_of_pool_reads_like_dsm(device_type, label):
    assert parse.raid_label(device_type) == label


@pytest.mark.parametrize("entry", [None, 5, {}, {"id": ""}, {"id": "reuse_9", "pool_child": "x", "disks": "y", "size": 5}])
def test_odd_pools_never_break_the_storage(entry):
    pools = _storage({"volumes": [], "storagePools": [entry]}).pools
    assert pools == [] or (pools[0].name == "Speicherpool 9" and pools[0].volumes == [] and pools[0].disks is None)


def test_disk_kind_decides_the_temperature_limit():
    disks = _storage({"disks": [
        {"id": "a", "name": "A", "isSsd": False}, {"id": "b", "name": "B", "isSsd": True},
        {"id": "c", "name": "C", "diskType": "NVMe", "isSsd": True},
    ]}).disks
    assert [d.kind for d in disks] == ["hdd", "ssd", "nvme"]
    alerts = syn_config.read_alerts({})
    assert [alerts.disk_limit(d.kind) for d in disks] == [55, 65, 70]
    assert syn_config.read_alerts({"disk_temp_warn_c": 50}).disk_limit("nvme") == 50
    for odd in (20, 120, "50", True, 50.5):
        assert syn_config.read_alerts({"disk_temp_warn_c": odd}).disk_temp_warn_c is None


# --- Zeiten


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2026/10/16 03:00", datetime(2026, 10, 16, 3, 0)), ("2026/10/16 03:00:59", datetime(2026, 10, 16, 3, 0, 59)),
     ("2026-10-16 03:00:00", datetime(2026, 10, 16, 3, 0)), ("Sat Oct 17 14:05:09 2026", datetime(2026, 10, 17, 14, 5, 9)),
     ("Thu Apr  2 07:01:00 2026", datetime(2026, 4, 2, 7, 1)), ("", None), ("N/A", None), ("2026/02/31 03:00", None),
     ("Sat Okt 17 14:05:09 2026", None), (None, None), (17, None), ("2026/10/16 03:00" + " " * 40, None)],
)
def test_dsm_times_are_read_without_a_zone(raw, expected):
    assert parse.parse_local_time(raw) == expected


# --- Hyper Backup


NOW = datetime(2026, 10, 17, 14, 5)


def _status(**over: Any) -> Any:
    fields = {
        "state": "backupable", "status": "none", "last_result": "done", "last_run": datetime(2026, 10, 17, 3, 0),
        "last_success": datetime(2026, 10, 17, 3, 40), "next_run": datetime(2026, 10, 18, 3, 0),
    }
    return parse.BackupStatus(**{**fields, **over})


def test_a_task_that_ran_last_night_is_fine():
    task = syn_backup.build_task(parse.TaskEntry("1", "Nacht"), _status(), NOW)
    assert (task.result_label, task.tone, task.overdue, task.problem, task.running) == ("Erfolgreich", "good", False, None, False)
    assert (task.last_run, task.next_run, task.last_success) == ("2026-10-17T03:00", "2026-10-18T03:00", "2026-10-17T03:40")


def test_a_missed_run_or_a_long_time_without_success_is_overdue():
    missed = _status(next_run=datetime(2026, 10, 17, 3, 0), last_run=datetime(2026, 10, 16, 3, 0))
    assert syn_backup.overdue_reason(missed, NOW) == "Der geplante Lauf vom 17.10.2026, 03:00 hat nicht stattgefunden."
    assert syn_backup.overdue_reason(missed, datetime(2026, 10, 17, 8, 0)) is None, "fünf Stunden sind noch kein Ausfall"
    # Wöchentlich gesichert, der letzte Erfolg liegt drei Wochen zurück (die Läufe dazwischen sind fehlgeschlagen).
    stale = _status(
        last_result="failed", last_run=datetime(2026, 10, 12, 3, 0), next_run=datetime(2026, 10, 19, 3, 0),
        last_success=datetime(2026, 9, 26, 3, 0),
    )
    assert syn_backup.overdue_reason(stale, NOW) == "Der letzte erfolgreiche Lauf war am 26.09.2026, 03:00."
    weekly_ok = _status(last_run=datetime(2026, 10, 12, 3, 0), next_run=datetime(2026, 10, 19, 3, 0), last_success=datetime(2026, 10, 12, 4, 0))
    assert syn_backup.overdue_reason(weekly_ok, NOW) is None
    assert syn_backup.overdue_reason(_status(status="backup", next_run=datetime(2026, 10, 1, 3, 0)), NOW) is None, "läuft gerade"
    checking = _status(state="error_detect", next_run=datetime(2026, 10, 1, 3, 0))
    assert syn_backup.overdue_reason(checking, NOW) is None, "Integritätsprüfung läuft"
    assert syn_backup.overdue_reason(_status(next_run=None), NOW) is None, "ohne Zeitplan nie überfällig"


@pytest.mark.parametrize(
    ("over", "label", "tone", "problem"),
    [
        ({"last_result": "failed"}, "Fehlgeschlagen", "danger", "Der letzte Lauf ist fehlgeschlagen. (Lauf vom 17.10.2026, 03:00)"),
        ({"last_result": "partial"}, "Teilweise gesichert", "warn", "Der letzte Lauf hat nicht alles gesichert. (Lauf vom 17.10.2026, 03:00)"),
        ({"last_result": "dest_missing", "last_run": None}, "Ziel nicht gefunden", "danger", "Das Ziel der Sicherung fehlt oder ist nicht erreichbar."),
        ({"state": "unauth"}, "Erfolgreich", "danger", "Das Ziel verlangt eine neue Anmeldung."),
        ({"last_result": "cancel"}, "Abgebrochen", "warn", None),
        ({"last_result": "none", "last_run": None, "last_success": None}, "Noch nie gelaufen", "neutral", None),
        ({"status": "backup", "last_result": "failed"}, "Läuft gerade", "neutral", None),
        ({"state": "error_detect"}, "Integritätsprüfung läuft", "neutral", None),
        (
            {"state": "error_detect", "last_result": "failed"}, "Fehlgeschlagen", "danger",
            "Der letzte Lauf ist fehlgeschlagen. (Lauf vom 17.10.2026, 03:00)",
        ),
        ({"state": "broken"}, "Erfolgreich", "danger", "Die Sicherung am Ziel ist beschädigt."),
        ({"last_result": "neu_in_dsm_8"}, "neu_in_dsm_8", "neutral", None),
    ],
)
def test_results_and_states_read_in_german(over, label, tone, problem):
    task = syn_backup.build_task(parse.TaskEntry("1", "Nacht"), _status(**over), NOW)
    assert (task.result_label, task.tone, task.problem) == (label, tone, problem)


def test_a_task_whose_status_could_not_be_read_says_so():
    task = syn_backup.build_task(parse.TaskEntry("4", "Cloud"), None, NOW)
    assert (task.result_label, task.tone, task.problem) == ("Stand unbekannt", "neutral", "Der Stand dieser Aufgabe ließ sich nicht lesen.")


@pytest.mark.parametrize("body", [None, [], {}, {"task_list": "x"}])
def test_a_task_list_that_is_none_is_refused(body):
    assert parse.parse_backup_tasks(body) is None


def test_tasks_need_a_numeric_id_and_get_a_name():
    tasks, total = parse.parse_backup_tasks({"task_list": [
        {"task_id": 3, "name": "Nacht"}, {"task_id": "7"}, {"task_id": True}, {"task_id": "../1"}, {"name": "ohne"}, "x",
    ]})
    assert tasks == [parse.TaskEntry("3", "Nacht"), parse.TaskEntry("7", "Aufgabe 7")] and total == 2


@pytest.mark.asyncio
async def test_hyper_backup_shows_each_task_with_last_and_next_run(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.install_hyper_backup({
        "1": ("Nachtsicherung", OK_TASK),
        "2": ("Cloud", {**OK_TASK, "last_bkp_result": "failed", "last_bkp_success_time": "2026/10/16 03:40"}),
        "3": ("Alt", {**OK_TASK, "next_bkp_time": "2026/10/16 03:00"}),
    })
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert [(t["id"], t["name"], t["result_label"], t["tone"], t["overdue"]) for t in body["backups"]] == [
        ("1", "Nachtsicherung", "Erfolgreich", "good", False),
        ("2", "Cloud", "Fehlgeschlagen", "danger", False),
        ("3", "Alt", "Erfolgreich", "warn", True),
    ]
    status_calls = [FakeDsm.params(r) for r in fake.requests if FakeDsm.params(r).get("method") == "status"]
    assert sorted(c["task_id"] for c in status_calls) == ["1", "2", "3"]
    assert all(c["blOnline"] == "false" for c in status_calls)
    rows = (await _widget(client, headers, "backups"))["data"]
    assert [(r["title"], r["label"], r["tone"]) for r in rows] == [
        ("Cloud", "Fehlgeschlagen", "danger"), ("Alt", "Erfolgreich", "warn"), ("Nachtsicherung", "Erfolgreich", "good"),
    ]
    assert rows[2]["subtitle"] == "Zuletzt 17.10.2026, 03:00 · nächster Lauf 18.10.2026, 03:00"


@pytest.mark.asyncio
async def test_without_hyper_backup_the_rest_stays_and_a_quiet_note_explains(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "ok" and body["backups"] is None and body["volumes"]
    assert body["backup_note"] == {"kind": "unavailable", "message": syn_service.NO_HYPER_BACKUP}
    assert ("SYNO.Backup.Task", "list") not in fake.calls()
    assert (await _widget(client, headers, "backups"))["data"] == [
        {"title": "Hyper Backup", "subtitle": syn_service.NO_HYPER_BACKUP, "label": "nicht verfügbar", "tone": "neutral"},
    ]


@pytest.mark.asyncio
async def test_hyper_backup_without_rights_names_the_group(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.install_hyper_backup({"1": ("Nacht", OK_TASK)})
    fake.api_errors["SYNO.Backup.Task"] = 105
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["backups"] is None and body["backup_note"]["kind"] == "no_rights"
    assert "Hyper Backup" in body["backup_note"]["message"] and "administrators" in body["backup_note"]["message"]


@pytest.mark.asyncio
async def test_one_unreadable_task_does_not_hide_the_others(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.install_hyper_backup({"1": ("Nacht", OK_TASK), "2": ("Kaputt", OK_TASK)})
    original = fake._backup
    fake._backup = lambda params: _fail(4401) if params.get("task_id") == "2" else original(params)  # type: ignore[method-assign]
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert [(t["name"], t["result_label"]) for t in body["backups"]] == [("Nacht", "Erfolgreich"), ("Kaputt", "Stand unbekannt")]


@pytest.mark.asyncio
async def test_every_task_is_asked_four_at_a_time(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.install_hyper_backup({str(i): (f"Aufgabe {i}", OK_TASK) for i in range(1, 26)})
    active, peak, asked = 0, 0, 0

    async def slow_status(request: httpx.Request) -> None:
        nonlocal active, peak, asked
        if FakeDsm.params(request).get("method") != "status":
            return
        active, asked = active + 1, asked + 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    fake.before = slow_status
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert len(body["backups"]) == 25 and asked == 25
    assert 1 < peak <= 4
    assert body["backup_note"] is None


def _slow_after(fake: FakeDsm, fast: int) -> None:
    """Antwortet auf die Stände der ersten `fast` Aufgaben sofort, auf alle anderen erst nach Minuten."""

    async def slow_status(request: httpx.Request) -> None:
        params = FakeDsm.params(request)
        if params.get("method") == "status" and int(params["task_id"]) > fast:
            await asyncio.sleep(300)

    fake.before = slow_status


@pytest.mark.asyncio
async def test_slow_tasks_hit_their_own_time_limit_and_the_rest_of_the_page_stays(client, db_session, test_settings, monkeypatch):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.install_hyper_backup({str(i): (f"Aufgabe {i}", OK_TASK) for i in range(1, 13)})
    _slow_after(fake, 4)
    monkeypatch.setattr("nodvard_deck_ext_synology.client.BACKUP_TIMEOUT_S", 0.5)
    monkeypatch.setattr("nodvard_deck_ext_synology.service.CONNECTION_TIMEOUT_S", 3.0)
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "ok", body["message"]
    assert body["volumes"] and body["system"] and body["update"] and body["storage_note"] is None
    assert [t["result_label"] for t in body["backups"]] == ["Erfolgreich"] * 4 + ["Stand unbekannt"] * 8
    assert all(t["problem"] for t in body["backups"][4:]) and body["backup_note"] is None


@pytest.mark.asyncio
async def test_a_slow_task_list_uses_up_the_same_time_limit_and_the_rest_of_the_page_stays(
    client, db_session, test_settings, monkeypatch,
):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.install_hyper_backup({"1": ("Nacht", OK_TASK)})

    async def slow_list(request: httpx.Request) -> None:
        if FakeDsm.params(request).get("method") == "list":
            await asyncio.sleep(300)

    fake.before = slow_list
    monkeypatch.setattr("nodvard_deck_ext_synology.client.BACKUP_TIMEOUT_S", 0.5)
    monkeypatch.setattr("nodvard_deck_ext_synology.service.CONNECTION_TIMEOUT_S", 3.0)
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "ok", body["message"]
    assert body["volumes"] and body["system"] and body["update"] and body["storage_note"] is None
    assert body["backups"] is None
    assert body["backup_note"]["message"].startswith("Hyper Backup hat nicht rechtzeitig geantwortet.")


@pytest.mark.asyncio
async def test_the_time_limit_for_hyper_backup_already_runs_while_connecting(client, db_session, test_settings, monkeypatch):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.install_hyper_backup({"1": ("Nacht", OK_TASK)})

    async def slow_connect_and_list(request: httpx.Request) -> None:
        params = FakeDsm.params(request)
        if params.get("api") == "SYNO.API.Info":
            await asyncio.sleep(0.8)
        elif params.get("method") == "list":
            await asyncio.sleep(300)

    fake.before = slow_connect_and_list
    monkeypatch.setattr("nodvard_deck_ext_synology.client.BACKUP_TIMEOUT_S", 0.8)
    monkeypatch.setattr("nodvard_deck_ext_synology.service.CONNECTION_TIMEOUT_S", 1.4)
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert body["state"] == "ok", body["message"]
    assert body["volumes"] and body["system"] and body["update"]
    assert body["backup_note"]["message"].startswith("Hyper Backup hat nicht rechtzeitig geantwortet.")


@pytest.mark.asyncio
@pytest.mark.parametrize(("installed", "total", "shown"), [(70, 70, 64), (3, 9, 3)], ids=["mehr-als-64", "dsm-blaettert"])
async def test_more_tasks_than_shown_are_said_and_not_hidden(client, db_session, test_settings, installed, total, shown):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.install_hyper_backup({str(i): (f"Aufgabe {i}", OK_TASK) for i in range(1, installed + 1)})
    fake.backup_total = total
    await _configure(client, headers)
    body = await _overview(client, headers)
    assert len(body["backups"]) == shown
    assert body["backup_note"]["kind"] == "partial"
    assert body["backup_note"]["message"].startswith(f"Hyper Backup hat {total} Aufgaben, Nodvard Deck zeigt und prüft davon die ersten {shown}.")


# --- Probleme


def _alerts(**over: Any) -> Any:
    return syn_config.read_alerts(over)


def test_a_degraded_pool_is_one_problem_and_its_volume_is_not_a_second():
    found = syn_problems.collect(
        system=parse.parse_system_info(INFO), storage=_storage(POOLS_STORAGE), update=None, tasks=None, alerts=_alerts(), now=NOW,
    )
    assert [(p.key, p.severity, p.subject) for p in found] == [
        ("disk:sata2", "critical", "Laufwerk 2"),
        ("pool:reuse_1", "warning", "Speicherpool 1"),
    ]
    pool = found[1]
    assert pool.title == "DS220+: Speicherpool 1 – Eingeschränkt"
    assert "Ausfallschutz fehlt" in pool.text and "(SHR)" in pool.text
    assert found[0].text == (
        "Laufwerk 2 meldet: SMART: Fällt aus; Zustand: Ausgefallen. Sichere wichtige Daten und plane den Tausch der Festplatte."
    )


def test_busy_pools_and_volumes_are_no_problem():
    busy = {
        "volumes": [{"id": "volume_1", "status": "repairing"}],
        "storagePools": [{"id": "reuse_1", "num_id": 1, "status": "expanding"}, {"id": "reuse_2", "num_id": 2, "status": "crashed"}],
    }
    found = syn_problems.collect(system=None, storage=_storage(busy), update=None, tasks=None, alerts=_alerts(), now=NOW)
    assert [(p.key, p.severity, p.title) for p in found] == [("pool:reuse_2", "critical", "Synology: Speicherpool 2 – Ausgefallen")]


@pytest.mark.parametrize("state", ["repairing", None, "migrating"], ids=["repairing", "unknown", "migrating"])
def test_a_volume_has_no_problem_of_its_own_while_its_pool_is_not_normal(state):
    storage = json.loads(json.dumps(POOLS_STORAGE))
    storage["disks"][1].update(status="normal", smart_status="normal")
    if state is None:
        del storage["storagePools"][0]["status"]
    else:
        storage["storagePools"][0]["status"] = state
    found = syn_problems.collect(system=None, storage=_storage(storage), update=None, tasks=None, alerts=_alerts(), now=NOW)
    assert found == []


def test_heat_and_updates():
    hot = parse.parse_system_info({**INFO, "temperature": 71, "temperature_warn": True})
    storage = _storage({"disks": [
        {"id": "a", "name": "Laufwerk 1", "temp": 55}, {"id": "b", "name": "Laufwerk 2", "temp": 54},
        {"id": "c", "name": "SSD", "temp": 60, "isSsd": True},
    ]})
    update = parse.parse_update(UPDATE)
    found = syn_problems.collect(system=hot, storage=storage, update=update, tasks=None, alerts=_alerts(), now=NOW)
    assert [(p.key, p.severity) for p in found] == [("temperature", "warning"), ("disk-temp:a", "warning"), ("update:DSM 7.2.2-72806", "info")]
    assert found[0].title == "DS220+: zu warm (71 °C)"
    assert found[1].text.startswith("Laufwerk 1 ist 55 °C warm, die Grenze liegt bei 55 °C.")
    assert found[2].text == "DSM 7.2.2-72806 steht bereit. Einspielen kannst du es in der Systemsteuerung von DSM."
    assert [p.key for p in syn_problems.collect(
        system=hot, storage=storage, update=None, tasks=None, alerts=_alerts(disk_temp_warn_c=60), now=NOW,
    )] == ["temperature", "disk-temp:c"]


def test_backup_problems_have_one_key_each():
    tasks = [
        (parse.TaskEntry("1", "Nacht"), _status(last_result="failed")),
        (parse.TaskEntry("2", "Teil"), _status(last_result="partial")),
        (parse.TaskEntry("3", "Alt"), _status(next_run=datetime(2026, 10, 16, 3, 0))),
        (parse.TaskEntry("4", "Ziel"), _status(state="broken")),
        (parse.TaskEntry("5", "Unbekannt"), None),
    ]
    found = syn_problems.collect(system=None, storage=None, update=None, tasks=tasks, alerts=_alerts(), now=NOW)
    assert [(p.key, p.severity, p.title) for p in found] == [
        ("backup-failed:1", "critical", "Synology: Hyper Backup „Nacht“ fehlgeschlagen"),
        ("backup-failed:2", "warning", "Synology: Hyper Backup „Teil“ unvollständig"),
        ("backup-overdue:3", "warning", "Synology: Hyper Backup „Alt“ überfällig"),
        ("backup-state:4", "warning", "Synology: Hyper Backup „Ziel“ gestört"),
    ]
    assert found[0].text == (
        "Hyper Backup „Nacht“: Der letzte Lauf ist fehlgeschlagen. (Lauf vom 17.10.2026, 03:00) Den Grund nennt das "
        "Protokoll in Hyper Backup."
    )
    failed_and_late = [(parse.TaskEntry("6", "Beides"), _status(last_result="failed", next_run=datetime(2026, 10, 16, 3, 0)))]
    assert [p.key for p in syn_problems.collect(
        system=None, storage=None, update=None, tasks=failed_and_late, alerts=_alerts(), now=NOW,
    )] == ["backup-failed:6"], "eine Ursache, eine Meldung"


def test_the_integrity_check_of_hyper_backup_is_no_problem():
    tasks = [(parse.TaskEntry("1", "Nacht"), _status(state="error_detect", next_run=datetime(2026, 10, 16, 3, 0)))]
    assert syn_problems.collect(system=None, storage=None, update=None, tasks=tasks, alerts=_alerts(), now=NOW) == []


@pytest.mark.asyncio
async def test_overdue_counts_by_the_clock_of_the_synology(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.install_hyper_backup({"1": ("Nacht", {**OK_TASK, "next_bkp_time": "2026/10/17 03:00"})})
    # Hier ist es noch früh am Morgen, auf der Synology schon Nachmittag.
    _service()._wall = lambda: datetime(2026, 10, 17, 4, 0)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    await _configure(client, headers)
    assert [p["key"] for p in (await _overview(client, headers))["problems"]] == ["backup-overdue:1"]
    fake.data["SYNO.DSM.Info"] = {k: v for k, v in INFO.items() if k != "time"}
    assert (await _overview(client, headers, "?refresh=true"))["problems"] == [], "ohne Uhr der Synology zählt die eigene"
    # Mit einem Puffer für eine andere Zeitzone: neun Stunden sind dann noch kein Ausfall, Tage schon.
    _service()._wall = lambda: datetime(2026, 10, 17, 12, 0)
    assert (await _overview(client, headers, "?refresh=true"))["problems"] == []
    _service()._wall = lambda: datetime(2026, 10, 19, 4, 0)
    assert [p["key"] for p in (await _overview(client, headers, "?refresh=true"))["problems"]] == ["backup-overdue:1"]


@pytest.mark.asyncio
async def test_the_status_widget_puts_problems_first(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Storage.CGI.Storage"] = POOLS_STORAGE
    await _configure(client, headers)
    widget = await _widget(client, headers)
    assert widget["meta"]["problems"] == 2
    assert [(r["title"], r["label"], r["tone"]) for r in widget["data"][:2]] == [
        ("Laufwerk 2", "kritisch", "danger"), ("Speicherpool 1", "Warnung", "warn"),
    ]
    volumes = (await _widget(client, headers, "volumes"))["data"]
    assert (volumes[0]["title"], volumes[0]["subtitle"], volumes[0]["label"]) == ("Speicherpool 1", "SHR · 1 Volumes", "Eingeschränkt")


# --- Wächter


async def _notes(db_session) -> list[tuple[str, str]]:
    from nodvard_deck.models import Notification as NotificationRow

    rows = (await db_session.execute(
        select(NotificationRow).where(NotificationRow.source_ext_id == EXT).order_by(NotificationRow.ts)
    )).scalars().all()
    return [(r.severity, r.title) for r in rows]


async def _watch() -> dict[str, Any]:
    handler = get_extension_runtime().scheduler.get(EXT, "watch")
    assert handler is not None
    return await handler()


@pytest.mark.asyncio
async def test_the_watch_reports_each_change_once_and_the_cockpit_lists_it(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Storage.CGI.Storage"] = POOLS_STORAGE
    fake.install_hyper_backup({"1": ("Nacht", {**OK_TASK, "last_bkp_result": "failed"})})
    await _configure(client, headers)

    assert (await _watch())["notified"] == 4
    assert sorted(await _notes(db_session)) == [
        ("critical", "DS220+: Hyper Backup „Nacht“ fehlgeschlagen"),
        ("critical", "DS220+: Laufwerk 2 – SMART: Fällt aus"),
        ("info", "DS220+: DSM-Update verfügbar"),
        ("warning", "DS220+: Speicherpool 1 – Eingeschränkt"),
    ]
    assert (await _watch())["notified"] == 0, "nur Wechsel werden gemeldet"
    cockpit = (await client.get("/api/v1/overview", headers=headers)).json()
    titles = {a["title"] for a in cockpit["attention"] if a["source_ext_id"] == EXT}
    assert titles == {
        "DS220+: Hyper Backup „Nacht“ fehlgeschlagen", "DS220+: Laufwerk 2 – SMART: Fällt aus", "DS220+: Speicherpool 1 – Eingeschränkt",
    }, "das Update steht nicht unter „Braucht Aufmerksamkeit“"

    healed = json.loads(json.dumps(POOLS_STORAGE))
    healed["storagePools"][0]["status"] = healed["volumes"][0]["status"] = "normal"
    healed["disks"][1].update(status="normal", smart_status="normal")
    fake.data["SYNO.Storage.CGI.Storage"] = healed
    fake.backups["1"]["status"] = OK_TASK
    await _service().clear()
    assert (await _watch())["notified"] == 3
    recovered = [t for sev, t in await _notes(db_session) if sev == "info" and "wieder in Ordnung" in t]
    assert sorted(recovered) == [
        "DS220+: Hyper Backup „Nacht“ wieder in Ordnung", "DS220+: Laufwerk 2 wieder in Ordnung", "DS220+: Speicherpool 1 wieder in Ordnung",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", ["repairing", "resyncing", "scrubbing"])
async def test_a_reported_pool_under_repair_is_not_cleared_until_it_is_normal(client, db_session, test_settings, busy):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    storage = json.loads(json.dumps(POOLS_STORAGE))
    storage["disks"][1].update(status="normal", smart_status="normal")
    fake.data["SYNO.Storage.CGI.Storage"] = storage
    await _configure(client, headers)
    assert (await _watch())["notified"] == 1

    async def round_with(status: str) -> list[str]:
        storage["storagePools"][0]["status"] = storage["volumes"][0]["status"] = status
        await _service().clear()
        await _watch()
        return [t for _sev, t in await _notes(db_session)]

    assert await round_with(busy) == ["DS220+: Speicherpool 1 – Eingeschränkt"], "mitten in der Reparatur keine Entwarnung"
    assert await round_with("normal") == ["DS220+: Speicherpool 1 – Eingeschränkt", "DS220+: Speicherpool 1 wieder in Ordnung"]


@pytest.mark.asyncio
async def test_a_reported_volume_under_repair_is_not_cleared_until_it_is_normal(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    storage = json.loads(json.dumps(POOLS_STORAGE))
    storage["disks"][1].update(status="normal", smart_status="normal")
    storage["storagePools"][0]["status"] = "normal"
    fake.data["SYNO.Storage.CGI.Storage"] = storage
    await _configure(client, headers)
    assert (await _watch())["notified"] == 1
    storage["volumes"][0]["status"] = "repairing"
    await _service().clear()
    assert (await _watch())["notified"] == 0
    storage["volumes"][0]["status"] = "normal"
    await _service().clear()
    assert (await _watch())["notified"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [None, "rebuilding", "migrating"], ids=["unknown", "rebuilding", "migrating"])
async def test_a_reported_pool_in_a_state_we_cannot_judge_is_not_cleared_until_it_is_normal(client, db_session, test_settings, state):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    storage = json.loads(json.dumps(POOLS_STORAGE))
    storage["disks"][1].update(status="normal", smart_status="normal")
    fake.data["SYNO.Storage.CGI.Storage"] = storage
    await _configure(client, headers)
    assert (await _watch())["notified"] == 1

    async def round_with(status: str | None) -> int:
        for entry in (storage["storagePools"][0], storage["volumes"][0]):
            if status is None:
                entry.pop("status", None)
            else:
                entry["status"] = status
        await _service().clear()
        return (await _watch())["notified"]

    assert await round_with(state) == 0
    assert [t for _sev, t in await _notes(db_session)] == ["DS220+: Speicherpool 1 – Eingeschränkt"]
    assert await round_with("normal") == 1
    assert [t for _sev, t in await _notes(db_session)][-1] == "DS220+: Speicherpool 1 wieder in Ordnung"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [None, "rebuilding", "migrating"], ids=["unknown", "rebuilding", "migrating"])
async def test_a_reported_volume_in_a_state_we_cannot_judge_is_not_cleared_until_it_is_normal(client, db_session, test_settings, state):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    storage = json.loads(json.dumps(POOLS_STORAGE))
    storage["disks"][1].update(status="normal", smart_status="normal")
    storage["storagePools"][0]["status"] = "normal"
    fake.data["SYNO.Storage.CGI.Storage"] = storage
    await _configure(client, headers)
    assert (await _watch())["notified"] == 1

    async def round_with(status: str | None) -> int:
        if status is None:
            storage["volumes"][0].pop("status", None)
        else:
            storage["volumes"][0]["status"] = status
        await _service().clear()
        return (await _watch())["notified"]

    assert await round_with(state) == 0
    assert [t for _sev, t in await _notes(db_session)] == ["DS220+: Volume 1 – Eingeschränkt"]
    assert await round_with("normal") == 1
    assert [t for _sev, t in await _notes(db_session)][-1] == "DS220+: Volume 1 wieder in Ordnung"


@pytest.mark.asyncio
async def test_a_volume_that_stays_degraded_while_its_pool_is_repaired_is_no_second_message(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    storage = json.loads(json.dumps(POOLS_STORAGE))
    storage["disks"][1].update(status="normal", smart_status="normal")
    fake.data["SYNO.Storage.CGI.Storage"] = storage
    await _configure(client, headers)
    assert (await _watch())["notified"] == 1

    storage["storagePools"][0]["status"] = "repairing"
    await _service().clear()
    assert (await _watch())["notified"] == 0
    assert [t for _sev, t in await _notes(db_session)] == ["DS220+: Speicherpool 1 – Eingeschränkt"]

    storage["storagePools"][0]["status"] = storage["volumes"][0]["status"] = "normal"
    await _service().clear()
    assert (await _watch())["notified"] == 1
    assert [t for _sev, t in await _notes(db_session)][-1] == "DS220+: Speicherpool 1 wieder in Ordnung"


@pytest.mark.asyncio
async def test_heat_is_reported_after_three_rounds_and_clears_five_degrees_below(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    disks = json.loads(json.dumps(STORAGE))
    fake.data["SYNO.Storage.CGI.Storage"] = disks
    await _configure(client, headers)

    async def round_with(temp: int) -> int:
        disks["disks"][0]["temp"] = temp
        await _service().clear()
        return (await _watch())["notified"]

    assert [await round_with(t) for t in (57, 58, 56)] == [0, 0, 1]
    assert await round_with(52) == 0, "noch nicht fünf Grad unter der Grenze"
    assert await round_with(50) == 1
    assert [t for _sev, t in await _notes(db_session)] == [
        "DS220+: Laufwerk 1 zu warm (56 °C)", "DS220+: Temperatur von Laufwerk 1 wieder in Ordnung",
    ]


@pytest.mark.asyncio
async def test_without_an_answer_the_watch_keeps_what_it_knows(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Storage.CGI.Storage"] = POOLS_STORAGE
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    await _configure(client, headers)
    assert (await _watch())["notified"] == 2

    fake.error = httpx.ConnectError("[Errno 111] Connection refused")
    await _service().clear()
    assert (await _watch())["notified"] == 0
    fake.error = None
    fake.api_errors["SYNO.Storage.CGI.Storage"] = 105
    await _service().clear()
    assert (await _watch())["notified"] == 0, "ein Teil ohne Antwort ist keine Entwarnung"
    assert len(await _notes(db_session)) == 2


@pytest.mark.asyncio
async def test_switches_turn_problems_and_updates_off_separately(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Storage.CGI.Storage"] = POOLS_STORAGE
    await _configure(client, headers, notify=False)
    assert (await _watch())["notified"] == 1
    assert await _notes(db_session) == [("info", "DS220+: DSM-Update verfügbar")]

    await _configure(client, headers, notify=True, notify_update=False, password=None)
    assert (await _watch())["notified"] == 2, "eingeschaltet: was noch offen ist, kommt jetzt"
    await _configure(client, headers, notify=False, notify_update=False, password=None)
    assert await _watch() == {"checked": 0, "notified": 0, "skipped": "abgeschaltet"}


@pytest.mark.asyncio
async def test_an_update_is_reported_once_per_version(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)

    async def round_with(update: dict[str, Any]) -> int:
        fake.data["SYNO.Core.Upgrade.Server"] = {"update": update}
        await _service().clear()
        return (await _watch())["notified"]

    assert await round_with(UPDATE["update"]) == 1
    assert await round_with({"available": False}) == 0, "keine Entwarnung"
    assert await round_with(UPDATE["update"]) == 0, "dieselbe Version nach kurzer Pause nicht noch einmal"
    assert await round_with({"available": True, "version": "DSM 7.3-80000"}) == 1
    assert await round_with(UPDATE["update"]) == 1, "eine abgelöste Version ist vergessen"
    assert [t for _s, t in await _notes(db_session)] == ["DS220+: DSM-Update verfügbar"] * 3


@pytest.mark.asyncio
async def test_a_deleted_task_is_forgotten_without_a_message(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    fake.install_hyper_backup({"1": ("Nacht", {**OK_TASK, "last_bkp_result": "failed"})})
    await _configure(client, headers)
    assert (await _watch())["notified"] == 1
    fake.backups = {}
    await _service().clear()
    assert (await _watch())["notified"] == 0
    state = json.loads((Path(str(get_extension_runtime().loaded[EXT].ctx.data_dir)) / "alerts.json").read_text(encoding="utf-8"))
    assert state == {}


@pytest.mark.asyncio
async def test_the_watch_is_quiet_when_nothing_is_set_up(client, db_session, test_settings):
    await _enable(client, db_session, test_settings)
    result = await _watch()
    assert result["notified"] == 0 and "noch nicht eingerichtet" in result["skipped"]
    assert syn_watch.SCHEDULE == "*/5 * * * *"


# --- Sitzung


@pytest.mark.asyncio
async def test_new_device_details_and_testing_the_connection_log_the_old_session_out(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    await _overview(client, headers)
    assert len(fake.sessions) == 1
    await _configure(client, headers, notify=False, password=None)
    assert len(fake.sessions) == 1, "gemeinsame Einstellungen lassen die Sitzung des Geräts stehen"
    await _configure(client, headers, tls_insecure_skip_verify=True, password=None)
    assert not fake.sessions and fake.calls()[-1] == ("SYNO.API.Auth", "logout")
    await _overview(client, headers)
    assert (await _health()).healthy is True
    assert len(fake.sessions) == 1, "„Verbindung testen“ meldet die alte Sitzung ab, bevor es neu anmeldet"


def test_the_session_id_is_not_in_the_repr():
    box = syn_client.SessionBox(sid="GEHEIMESID12345")
    assert "GEHEIMESID12345" not in repr(box)


# --- Fingerabdruck des Zertifikats


PIN = ":".join(["AB"] * 32)


def test_the_fingerprint_is_normalized_or_refused():
    assert syn_config.normalize_fingerprint(None) is None and syn_config.normalize_fingerprint("  ") is None
    assert syn_config.normalize_fingerprint("ab" * 32) == PIN
    for bad in ("kaputt", "ab" * 31, 5, "ab" * 200):
        with pytest.raises(ValueError):
            syn_config.normalize_fingerprint(bad)


def test_a_pin_needs_https_and_a_broken_one_is_never_ignored():
    pinned = _read_one(base_url=BASE, tls_fingerprint_sha256="ab" * 32, tls_insecure_skip_verify=True)
    assert (pinned.pin, pinned.problem) == (PIN, None)
    broken = _read_one(base_url=BASE, tls_fingerprint_sha256="kaputt")
    assert broken.base_url is None and "Fingerabdruck" in broken.problem and "ungültig" in broken.problem
    plain = _read_one(base_url="http://nas.test:5000", tls_fingerprint_sha256=PIN)
    assert plain.base_url is None and "nur für https://" in plain.problem


@pytest.mark.asyncio
async def test_the_transport_sends_the_pin_instead_of_the_switch():
    http = _Http(lambda request: httpx.Response(200, content=b"{}"))
    await transport.send(http, "GET", f"{BASE}/webapi/x", insecure_tls=True, pin=PIN)
    assert http.calls[0]["pinned_sha256"] == PIN and http.calls[0]["insecure_tls"] is False


@pytest.mark.asyncio
async def test_with_a_pin_every_request_goes_to_exactly_that_certificate(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    pinned: list[httpx.Request] = []

    async def pinned_handler(request: httpx.Request) -> Any:
        pinned.append(request)
        return await fake.handler(request)

    http = get_extension_runtime().loaded[EXT].ctx.http
    http._pinned_clients[bytes.fromhex(PIN.replace(":", ""))] = httpx.AsyncClient(transport=httpx.MockTransport(pinned_handler))
    await _configure(client, headers, tls_fingerprint_sha256=PIN, tls_insecure_skip_verify=True)
    body = await _overview(client, headers)
    assert body["state"] == "ok", body["message"]
    assert pinned and len(pinned) == len(fake.requests) and not fake.insecure_requests
    await _service().clear()
    assert pinned[-1].url.params["method"] == "logout", "auch das Abmelden geht an das gepinnte Zertifikat"


@pytest.mark.asyncio
async def test_a_running_or_unreadable_task_is_no_all_clear(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    fake.install_hyper_backup({"1": ("Nacht", {**OK_TASK, "last_bkp_result": "failed"})})
    await _configure(client, headers)
    assert (await _watch())["notified"] == 1

    fake.backups["1"]["status"] = {**OK_TASK, "last_bkp_result": "failed", "status": "backup"}
    await _service().clear()
    assert (await _watch())["notified"] == 0, "läuft gerade: noch kein Ergebnis"
    original = fake._backup
    fake._backup = lambda params: _fail(4401) if params.get("method") == "status" else original(params)  # type: ignore[method-assign]
    await _service().clear()
    assert (await _watch())["notified"] == 0, "Stand unbekannt: keine Entwarnung"
    fake._backup = original  # type: ignore[method-assign]
    fake.backups["1"]["status"] = {**OK_TASK, "last_bkp_result": "failed"}
    await _service().clear()
    assert (await _watch())["notified"] == 0, "wieder fehlgeschlagen ist keine neue Meldung"
    assert [t for _s, t in await _notes(db_session)] == ["DS220+: Hyper Backup „Nacht“ fehlgeschlagen"]


@pytest.mark.asyncio
async def test_the_integrity_check_of_hyper_backup_is_no_alarm_and_no_all_clear(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    fake.install_hyper_backup({"1": ("Nacht", OK_TASK)})
    await _configure(client, headers)
    late = {**OK_TASK, "next_bkp_time": "2026/10/16 03:00"}
    checking = {**late, "state": "error_detect"}

    async def watch(status: dict[str, Any]) -> int:
        fake.backups["1"]["status"] = status
        await _service().clear()
        return (await _watch())["notified"]

    # `status` bleibt während der Prüfung `none`; ein Termin, der dabei verstreicht, gilt nicht als ausgeblieben.
    assert [await watch(OK_TASK), await watch(checking)] == [0, 0]
    task = (await _overview(client, headers))["backups"][0]
    assert (task["result_label"], task["tone"], task["overdue"], task["problem"], task["running"]) == (
        "Integritätsprüfung läuft", "neutral", False, None, True,
    )
    assert await watch(OK_TASK) == 0
    assert await _notes(db_session) == []

    # Eine offene Meldung bleibt während der Prüfung offen, die Entwarnung kommt erst danach.
    assert [await watch(late), await watch(checking), await watch(OK_TASK)] == [1, 0, 1]
    # Ein fehlgeschlagener Lauf bleibt während der Prüfung sichtbar; findet sie etwas, kommt „gestört“ dazu.
    failed = {**OK_TASK, "last_bkp_result": "failed"}
    assert [await watch(failed), await watch({**failed, "state": "error_detect"})] == [1, 0]
    task = (await _overview(client, headers))["backups"][0]
    assert (task["result_label"], task["tone"]) == ("Fehlgeschlagen", "danger")
    assert await watch({**failed, "state": "broken"}) == 1
    assert await _notes(db_session) == [
        ("warning", "DS220+: Hyper Backup „Nacht“ überfällig"),
        ("info", "DS220+: Hyper Backup „Nacht“ wieder in Ordnung"),
        ("critical", "DS220+: Hyper Backup „Nacht“ fehlgeschlagen"),
        ("warning", "DS220+: Hyper Backup „Nacht“ gestört"),
    ]


@pytest.mark.asyncio
async def test_a_task_cut_off_by_the_time_limit_is_no_all_clear(client, db_session, test_settings, monkeypatch):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    fake.install_hyper_backup({"1": ("Nacht", {**OK_TASK, "last_bkp_result": "failed"})})
    await _configure(client, headers)
    assert (await _watch())["notified"] == 1

    _slow_after(fake, 0)
    monkeypatch.setattr("nodvard_deck_ext_synology.client.BACKUP_TIMEOUT_S", 0.2)
    monkeypatch.setattr("nodvard_deck_ext_synology.service.CONNECTION_TIMEOUT_S", 3.0)
    fake.backups["1"]["status"] = OK_TASK
    await _service().clear()
    assert await _watch() == {"checked": 0, "notified": 0}, "Stand nicht rechtzeitig da: keine Entwarnung"
    fake.before = None
    await _service().clear()
    assert (await _watch())["notified"] == 1
    assert [t for _s, t in await _notes(db_session)] == [
        "DS220+: Hyper Backup „Nacht“ fehlgeschlagen", "DS220+: Hyper Backup „Nacht“ wieder in Ordnung",
    ]


@pytest.mark.asyncio
async def test_a_problem_that_gets_worse_is_reported_again(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    storage = json.loads(json.dumps(POOLS_STORAGE))
    storage["disks"][1].update(status="normal", smart_status="normal")
    fake.data["SYNO.Storage.CGI.Storage"] = storage
    await _configure(client, headers)
    assert (await _watch())["notified"] == 1
    storage["storagePools"][0]["status"] = "crashed"
    await _service().clear()
    assert (await _watch())["notified"] == 1
    assert (await _watch())["notified"] == 0
    assert await _notes(db_session) == [
        ("warning", "DS220+: Speicherpool 1 – Eingeschränkt"), ("critical", "DS220+: Speicherpool 1 – Ausgefallen"),
    ]


@pytest.mark.asyncio
async def test_an_unreadable_temperature_is_no_all_clear(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    disks = json.loads(json.dumps(STORAGE))
    fake.data["SYNO.Storage.CGI.Storage"] = disks
    await _configure(client, headers)
    for temp in (60, 60, 60):
        disks["disks"][0]["temp"] = temp
        await _service().clear()
        await _watch()
    disks["disks"][0]["temp"] = 0  # DSM meldet 0, wenn es keinen Fühler gibt
    await _service().clear()
    assert (await _watch())["notified"] == 0
    assert [t for _s, t in await _notes(db_session)] == ["DS220+: Laufwerk 1 zu warm (60 °C)"]


@pytest.mark.asyncio
async def test_a_volume_pulled_down_by_its_pool_is_no_all_clear(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    storage = json.loads(json.dumps(POOLS_STORAGE))
    storage["disks"][1].update(status="normal", smart_status="normal")
    storage["storagePools"][0]["status"] = "normal"
    fake.data["SYNO.Storage.CGI.Storage"] = storage
    await _configure(client, headers)
    assert (await _watch())["notified"] == 1, "erst nur das Volume"
    storage["storagePools"][0]["status"] = "degraded"
    await _service().clear()
    assert (await _watch())["notified"] == 1
    assert [t for _s, t in await _notes(db_session)] == [
        "DS220+: Volume 1 – Eingeschränkt", "DS220+: Speicherpool 1 – Eingeschränkt",
    ], "keine Entwarnung für das Volume, solange der Pool es mitzieht"


@pytest.mark.asyncio
async def test_a_failed_message_is_tried_again_and_the_state_is_written_whole(client, db_session, test_settings, monkeypatch):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    fake.data["SYNO.Storage.CGI.Storage"] = POOLS_STORAGE
    await _configure(client, headers)
    notify = get_extension_runtime().loaded[EXT].ctx.notify
    original, calls = notify.send, 0

    async def flaky(notification, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Datenbank gesperrt")
        return await original(notification, **kwargs)

    monkeypatch.setattr(notify, "send", flaky)
    assert (await _watch())["notified"] == 1
    assert (await _watch())["notified"] == 1, "die fehlgeschlagene Meldung kommt bei der nächsten Prüfung"
    assert (await _watch())["notified"] == 0
    data_dir = Path(str(get_extension_runtime().loaded[EXT].ctx.data_dir))
    assert sorted(json.loads((data_dir / "alerts.json").read_text(encoding="utf-8"))) == ["disk:sata2", "pool:reuse_1"]
    assert not (data_dir / "alerts.json.tmp").exists()


@pytest.mark.parametrize("state", ["repairing", None, "migrating"], ids=["repairing", "unknown", "migrating"])
def test_a_crashed_volume_reports_itself_under_a_pool_without_its_own_message(state):
    storage = json.loads(json.dumps(POOLS_STORAGE))
    storage["disks"][1].update(status="normal", smart_status="normal")
    storage["volumes"][0]["status"] = "crashed"
    if state is None:
        del storage["storagePools"][0]["status"]
    else:
        storage["storagePools"][0]["status"] = state
    found = syn_problems.collect(system=None, storage=_storage(storage), update=None, tasks=None, alerts=_alerts(), now=NOW)
    assert ("volume:volume_1", "critical") in [(p.key, p.severity) for p in found]


def test_a_crashed_volume_in_a_degraded_pool_is_one_urgent_message():
    storage = json.loads(json.dumps(POOLS_STORAGE))
    storage["disks"][1].update(status="normal", smart_status="normal")
    storage["volumes"][0]["status"] = "crashed"
    found = syn_problems.collect(system=None, storage=_storage(storage), update=None, tasks=None, alerts=_alerts(), now=NOW)
    assert [(p.key, p.severity) for p in found] == [("pool:reuse_1", "critical")]
    assert found[0].title.endswith(": Speicherpool 1 – Eingeschränkt, Volume 1 ausgefallen")
    assert "Außerdem ist Volume 1 ausgefallen" in found[0].text


@pytest.mark.asyncio
async def test_a_volume_crashing_in_a_degraded_pool_raises_the_pool_message_once(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    fake.data["SYNO.Core.Upgrade.Server"] = {"update": {"available": False}}
    storage = json.loads(json.dumps(POOLS_STORAGE))
    storage["disks"][1].update(status="normal", smart_status="normal")
    storage["volumes"][0]["status"] = "normal"
    fake.data["SYNO.Storage.CGI.Storage"] = storage
    await _configure(client, headers)
    assert (await _watch())["notified"] == 1
    storage["volumes"][0]["status"] = "crashed"
    await _service().clear()
    assert (await _watch())["notified"] == 1
    assert (await _watch())["notified"] == 0
    assert await _notes(db_session) == [
        ("warning", "DS220+: Speicherpool 1 – Eingeschränkt"),
        ("critical", "DS220+: Speicherpool 1 – Eingeschränkt, Volume 1 ausgefallen"),
    ]


def test_a_crashed_volume_of_a_crashed_pool_is_one_message():
    storage = json.loads(json.dumps(POOLS_STORAGE))
    storage["disks"][1].update(status="normal", smart_status="normal")
    storage["volumes"][0]["status"] = "crashed"
    storage["storagePools"][0]["status"] = "crashed"
    found = syn_problems.collect(system=None, storage=_storage(storage), update=None, tasks=None, alerts=_alerts(), now=NOW)
    assert [p.key for p in found] == ["pool:reuse_1"]
