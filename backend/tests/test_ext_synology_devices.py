"""Mehrere Synology-Geräte: getrennte Konten, Pausen, Kacheln und Meldungen, dazu die Übernahme der alten Einzel-Einrichtung."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from sqlalchemy import select
from test_ext_synology import (  # noqa: F401 - Fixture _synology_on_path
    API,
    DSM_711_APIS,
    DSM_741_APIS,
    EXT,
    INFO,
    OK_TASK,
    PASSWORD,
    POOLS_STORAGE,
    REPO_EXTENSIONS_DIR,
    FakeDsm,
    _entry,
    _login,
    _put_settings,
    _set_password,
    _synology_on_path,
    syn_config,
    syn_service,
    syn_watch,
)

from nodvard_deck.core import vault
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import ExtensionRecord, Secret
from nodvard_deck.services import extensions as extensions_service

DS920 = "https://ds920.test:5001"
DS415 = "https://ds415.test:5001"
PASSWORD_B = "Zweites&Passwort 2"
HEALTHY_UPDATE = {"update": {"available": False}}


class Network:
    """Ein Netz mit mehreren DSM: Jede Anfrage geht an das Gerät, dessen Rechnername in der Adresse steht."""

    def __init__(self, devices: dict[str, FakeDsm]) -> None:
        self.by_host = {urlsplit(url).hostname: fake for url, fake in devices.items()}

    async def handler(self, request: httpx.Request) -> Any:
        return await self.by_host[request.url.host].handler(request)

    async def insecure_handler(self, request: httpx.Request) -> Any:
        return await self.by_host[request.url.host].insecure_handler(request)


def _dsm(*, model: str, user: str, password: str, apis: dict[str, Any] | None = None, version: str = "DSM 7.2.1-69057 Update 5") -> FakeDsm:
    fake = FakeDsm()
    fake.username, fake.password = user, password
    fake.data["SYNO.DSM.Info"] = {**INFO, "model": model, "version_string": version}
    fake.data["SYNO.Core.Upgrade.Server"] = json.loads(json.dumps(HEALTHY_UPDATE))
    if apis is not None:
        fake.apis = json.loads(json.dumps(apis))
    return fake


def _two() -> dict[str, FakeDsm]:
    return {
        DS920: _dsm(model="DS920+", user="eins", password=PASSWORD, apis=DSM_741_APIS, version="DSM 7.4.1-90080 Update 1"),
        DS415: _dsm(model="DS415play", user="zwei", password=PASSWORD_B, apis=DSM_711_APIS, version="DSM 7.1.1-42962 Update 9"),
    }


def _wire(fakes: dict[str, FakeDsm]) -> None:
    network = Network(fakes)
    http = get_extension_runtime().loaded[EXT].ctx.http
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(network.handler))
    http._insecure_client = httpx.AsyncClient(transport=httpx.MockTransport(network.insecure_handler))


async def _discover(db_session, test_settings) -> None:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)


async def _enable_extension(client, headers) -> None:
    enabled = await client.post(f"/api/v1/extensions/{EXT}/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text


async def _setup_two(client, db_session, test_settings, fakes: dict[str, FakeDsm] | None = None) -> tuple[dict, dict[str, FakeDsm]]:
    """Beide Geräte eingerichtet: „ds920“ (Konto „eins“) und „keller“ (Konto „zwei“), je mit eigenem Passwort."""
    fakes = fakes or _two()
    await _discover(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)
    _wire(fakes)
    await _put_settings(client, headers, {"connections": [
        _entry("ds920", url=DS920, username="eins"), _entry("keller", url=DS415, username="zwei", tls_insecure_skip_verify=True),
    ]})
    await _set_password(client, headers, PASSWORD, "ds920")
    await _set_password(client, headers, PASSWORD_B, "keller")
    return headers, fakes


async def _devices(client, headers, query: str = "") -> list[dict[str, Any]]:
    res = await client.get(f"{API}/devices{query}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["devices"]


async def _catalog(client, headers) -> dict[str, dict[str, Any]]:
    widgets = (await client.get("/api/v1/widgets", headers=headers)).json()
    return {w["id"]: w for w in widgets if w["ext_id"] == EXT}


def _service():
    return get_extension_runtime().loaded[EXT].instance._service


async def _health():
    loaded = get_extension_runtime().loaded[EXT]
    return await loaded.instance.health(loaded.ctx)


# --- Mehrere Geräte nebeneinander


@pytest.mark.asyncio
async def test_each_device_logs_in_with_its_own_account_and_shows_its_own_state(client, db_session, test_settings):
    headers, fakes = await _setup_two(client, db_session, test_settings)
    a, b = fakes[DS920], fakes[DS415]

    devices = await _devices(client, headers)
    assert [(d["device"], d["state"], d["url"]) for d in devices] == [("ds920", "ok", DS920), ("keller", "ok", DS415)]
    assert [(d["system"]["model"], d["system"]["dsm_version"]) for d in devices] == [
        ("DS920+", "DSM 7.4.1-90080 Update 1"), ("DS415play", "DSM 7.1.1-42962 Update 9"),
    ]
    assert (a.logins, b.logins) == (1, 1)
    assert [dict(parse_qsl(r.content.decode()))["account"] for r in a.posts()] == ["eins"]
    assert [dict(parse_qsl(r.content.decode()))["account"] for r in b.posts()] == ["zwei"]
    assert PASSWORD_B not in "".join(r.content.decode() for r in a.requests), "das Passwort eines Geräts geht nie an ein anderes"
    assert PASSWORD not in "".join(r.content.decode() for r in b.requests)


@pytest.mark.asyncio
async def test_each_device_keeps_its_own_dsm_version_and_session(client, db_session, test_settings):
    headers, fakes = await _setup_two(client, db_session, test_settings)
    await _devices(client, headers)

    def versions(fake: FakeDsm) -> dict[str, str]:
        used: dict[str, str] = {}
        for request in fake.requests[1:]:
            params = FakeDsm.params(request)
            if "version" in params:
                used.setdefault(params["api"], params["version"])
        return used

    assert versions(fakes[DS920])["SYNO.Core.Upgrade.Server"] == "4"
    assert versions(fakes[DS415])["SYNO.DSM.Info"] == "2"
    assert (len(fakes[DS920].sessions), len(fakes[DS415].sessions)) == (1, 1), "jedes Gerät hat seine eigene Sitzung"
    before = {url: len(fake.requests) for url, fake in fakes.items()}
    await _devices(client, headers)
    assert {url: len(fake.requests) for url, fake in fakes.items()} == before, "binnen 20 Sekunden kommt es aus dem Zwischenspeicher"


@pytest.mark.asyncio
async def test_tls_options_are_per_device(client, db_session, test_settings):
    headers, fakes = await _setup_two(client, db_session, test_settings)
    await _devices(client, headers)
    assert fakes[DS920].insecure_requests == [], "ds920 prüft das Zertifikat"
    assert fakes[DS415].insecure_requests, "keller erlaubt ein selbstsigniertes Zertifikat"
    assert len(fakes[DS415].insecure_requests) == len(fakes[DS415].requests)


@pytest.mark.asyncio
async def test_overview_without_a_device_is_the_first_one_and_a_name_picks_another(client, db_session, test_settings):
    headers, _ = await _setup_two(client, db_session, test_settings)
    first = (await client.get(f"{API}/overview", headers=headers)).json()
    assert (first["device"], first["system"]["model"]) == ("ds920", "DS920+")
    second = (await client.get(f"{API}/overview?device=keller", headers=headers)).json()
    assert (second["device"], second["system"]["model"]) == ("keller", "DS415play")
    missing = await client.get(f"{API}/overview?device=gibt-es-nicht", headers=headers)
    assert missing.status_code == 404 and "gibt-es-nicht" in missing.json()["detail"]
    assert (await client.get(f"{API}/overview?device=..", headers=headers)).status_code == 404
    long = await client.get(f"{API}/overview?device={'x' * 300}", headers=headers)
    assert long.status_code == 404 and len(long.json()["detail"]) < 120


@pytest.mark.asyncio
async def test_without_any_device_both_routes_say_not_set_up(client, db_session, test_settings):
    await _discover(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)
    assert await _devices(client, headers) == []
    single = (await client.get(f"{API}/overview", headers=headers)).json()
    assert (single["device"], single["state"], single["url"]) == (None, "not_configured", None)
    assert "noch nicht eingerichtet" in single["message"]


# --- Pause nach abgelehnter Anmeldung, je Gerät


@pytest.mark.asyncio
async def test_a_rejected_login_pauses_only_that_device(client, db_session, test_settings):
    headers, fakes = await _setup_two(client, db_session, test_settings)
    a, b = fakes[DS920], fakes[DS415]
    await _set_password(client, headers, "falsch", "ds920")

    first = await _devices(client, headers)
    assert [d["state"] for d in first] == ["auth_failed", "ok"]
    assert (a.failed_logins, b.logins) == (1, 1)

    # „Aktualisieren“ fragt das pausierte Gerät nicht noch einmal, das andere schon.
    b_requests = len(b.requests)
    again = await _devices(client, headers, "?refresh=true")
    assert [d["state"] for d in again] == ["auth_failed", "ok"]
    assert a.failed_logins == 1
    assert len(b.requests) > b_requests, "das andere Gerät wird weiter abgefragt"


@pytest.mark.asyncio
async def test_the_pause_ends_after_thirty_minutes_for_that_device_only(client, db_session, test_settings):
    assert syn_service.AUTH_FAILED_TTL_S == 1800.0
    headers, fakes = await _setup_two(client, db_session, test_settings)
    a = fakes[DS920]
    await _set_password(client, headers, "falsch", "ds920")
    service = _service()
    now = [1000.0]
    service._clock = lambda: now[0]

    await _devices(client, headers)
    assert a.failed_logins == 1
    now[0] += syn_service.AUTH_FAILED_TTL_S - 1
    await _devices(client, headers, "?refresh=true")
    assert a.failed_logins == 1, "noch in der Pause"
    now[0] += 2
    await _devices(client, headers)
    assert a.failed_logins == 2, "die Pause ist vorbei, das Gerät wird wieder gefragt"


@pytest.mark.asyncio
async def test_saving_other_settings_or_changing_the_device_list_keeps_the_pause_of_an_unchanged_device(client, db_session, test_settings):
    headers, fakes = await _setup_two(client, db_session, test_settings)
    a = fakes[DS920]
    await _set_password(client, headers, "falsch", "ds920")
    await _devices(client, headers)
    assert a.failed_logins == 1

    for flag in (False, True):
        await _put_settings(client, headers, {"notify_update": flag})
        await _devices(client, headers, "?refresh=true")
    await _put_settings(client, headers, {"disk_temp_warn_c": 60})
    await _devices(client, headers)
    assert a.failed_logins == 1, "andere Einstellungen kosten keinen Fehlversuch"

    ds920 = _entry("ds920", url=DS920, username="eins")
    keller = _entry("keller", url=DS415, username="zwei", tls_insecure_skip_verify=True)
    for connections in ([ds920], [ds920, keller], [ds920, keller, _entry("drittes", url="https://ds3.test:5001", username="drei")]):
        await _put_settings(client, headers, {"connections": connections})
        devices = await _devices(client, headers, "?refresh=true")
        assert devices[0]["state"] == "auth_failed"
    assert a.failed_logins == 1, "weniger oder mehr Geräte lassen die Pause stehen"


@pytest.mark.asyncio
async def test_changing_the_details_of_the_paused_device_ends_only_its_pause(client, db_session, test_settings):
    headers, fakes = await _setup_two(client, db_session, test_settings)
    a, b = fakes[DS920], fakes[DS415]
    await _set_password(client, headers, "falsch", "ds920")
    await _devices(client, headers)
    assert (a.failed_logins, b.logins) == (1, 1)

    a.password = "falsch"
    ds920 = _entry("ds920", url=DS920, username="eins", tls_insecure_skip_verify=True)
    keller = _entry("keller", url=DS415, username="zwei", tls_insecure_skip_verify=True)
    await _put_settings(client, headers, {"connections": [ds920, keller]})
    assert [d["state"] for d in await _devices(client, headers)] == ["ok", "ok"]
    assert (a.logins, b.logins) == (1, 1), "keller behält seine Sitzung"


@pytest.mark.asyncio
async def test_a_new_password_ends_only_the_pause_and_session_of_its_device(client, db_session, test_settings):
    headers, fakes = await _setup_two(client, db_session, test_settings)
    a, b = fakes[DS920], fakes[DS415]
    await _set_password(client, headers, "falsch", "ds920")
    await _devices(client, headers)
    assert (a.failed_logins, b.logins) == (1, 1)

    await _set_password(client, headers, PASSWORD, "ds920")
    assert [d["state"] for d in await _devices(client, headers)] == ["ok", "ok"]
    assert (a.logins, b.logins) == (1, 1), "keller behält seine Sitzung"
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.instance.on_secret_changed(loaded.ctx, "synology-password")
    await _devices(client, headers, "?refresh=true")
    assert (a.logins, b.logins) == (1, 1), "der alte Eintrag ohne Kurznamen betrifft kein Gerät"


@pytest.mark.asyncio
async def test_the_pause_of_one_device_does_not_stop_the_tiles_of_the_other(client, db_session, test_settings):
    headers, fakes = await _setup_two(client, db_session, test_settings)
    await _set_password(client, headers, "falsch", "ds920")
    await _devices(client, headers)

    paused = (await client.get(f"{API}/widgets/status/ds920", headers=headers)).json()
    assert paused["meta"]["state"] == "error"
    assert paused["data"][0]["title"] == "ds920: Anmeldung fehlgeschlagen" and paused["data"][0]["tone"] == "danger"
    fine = (await client.get(f"{API}/widgets/status/keller", headers=headers)).json()
    assert fine["meta"]["state"] == "ok" and fine["data"][0]["title"] == "DS415play"
    assert fakes[DS920].failed_logins == 1


@pytest.mark.asyncio
async def test_testing_the_connection_lifts_every_pause_and_names_each_device(client, db_session, test_settings):
    headers, fakes = await _setup_two(client, db_session, test_settings)
    await _set_password(client, headers, "falsch", "ds920")
    await _devices(client, headers)
    assert fakes[DS920].failed_logins == 1

    health = await _health()
    assert health.healthy is False
    assert fakes[DS920].failed_logins == 2, "„Verbindung testen“ hebt die Pause auf"
    assert health.details["keller"] == {"healthy": True}
    assert health.details["ds920"]["healthy"] is False and health.details["ds920"]["verbatim"] is True
    assert health.message.startswith("ds920: Benutzername oder Passwort stimmt nicht.") and "; keller: in Ordnung" in health.message

    await _set_password(client, headers, PASSWORD, "ds920")
    health = await _health()
    assert health.healthy is True and health.message == "ds920: in Ordnung; keller: in Ordnung"


@pytest.mark.asyncio
async def test_a_slow_device_does_not_take_the_test_result_of_the_others_with_it(client, db_session, test_settings, monkeypatch):
    from nodvard_deck.services import extension_test

    headers, fakes = await _setup_two(client, db_session, test_settings)

    async def hang(request: httpx.Request) -> None:
        await asyncio.sleep(300)

    fakes[DS415].before = hang
    monkeypatch.setattr(extension_test, "TEST_TIMEOUT_S", 2.0)
    monkeypatch.setattr("nodvard_deck_ext_synology.service.TEST_DEVICE_TIMEOUT_S", 0.5)
    res = await client.post(f"/api/v1/extensions/{EXT}/test", json={"mode": "connection"}, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is False and body["details"], body
    by_name = {d["name"]: d for d in body["details"]}
    assert by_name["ds920"]["ok"] is True
    assert by_name["keller"]["ok"] is False and by_name["keller"]["message"].startswith("keller antwortet nicht rechtzeitig.")


@pytest.mark.asyncio
async def test_logging_out_before_the_test_is_kept_short(client, db_session, test_settings, monkeypatch):
    headers, fakes = await _setup_two(client, db_session, test_settings)
    await _devices(client, headers)

    async def hang_on_logout(request: httpx.Request) -> None:
        if FakeDsm.params(request).get("method") == "logout":
            await asyncio.sleep(300)

    for fake in fakes.values():
        fake.before = hang_on_logout
    monkeypatch.setattr("nodvard_deck_ext_synology.service.TEST_LOGOUT_TIMEOUT_S", 0.2)
    started = time.monotonic()
    health = await _health()
    assert health.healthy is True, health.message
    assert time.monotonic() - started < 2.0


@pytest.mark.asyncio
async def test_one_unreachable_device_leaves_the_other_alone(client, db_session, test_settings):
    headers, fakes = await _setup_two(client, db_session, test_settings)
    fakes[DS415].error = httpx.ConnectError("refused")
    devices = await _devices(client, headers)
    assert [(d["device"], d["state"]) for d in devices] == [("ds920", "ok"), ("keller", "unreachable")]
    assert devices[1]["message"] and devices[1]["system"] is None


# --- Zugangsdaten im Tresor

@pytest.mark.asyncio
async def test_each_device_has_its_own_password_slot_and_changing_one_address_clears_only_its_password(client, db_session, test_settings):
    headers, _ = await _setup_two(client, db_session, test_settings)
    settings = (await client.get(f"/api/v1/extensions/{EXT}/settings", headers=headers)).json()
    assert {s["label"]: (s["item"], s["is_set"]) for s in settings["secrets"]} == {
        "synology-password:ds920": ("ds920", True), "synology-password:keller": ("keller", True),
    }

    moved = [_entry("ds920", url=DS920, username="eins"), _entry("keller", url="https://angreifer.test:5001", username="zwei")]
    saved = await client.put(
        f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": moved}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["secrets_cleared"] == ["synology-password:keller"]
    assert {s["label"]: s["is_set"] for s in saved.json()["secrets"]} == {"synology-password:ds920": True, "synology-password:keller": False}


@pytest.mark.asyncio
async def test_removing_a_device_removes_its_password_and_keeps_the_other(client, db_session, test_settings):
    headers, _ = await _setup_two(client, db_session, test_settings)
    saved = await client.put(
        f"/api/v1/extensions/{EXT}/settings",
        json={"current_password": "correct-horse-battery", "values": {"connections": [_entry("keller", url=DS415, username="zwei")]}},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["secrets_cleared"] == ["synology-password:ds920"]
    labels = set((await db_session.execute(select(Secret.label))).scalars().all())
    assert "synology-password:ds920" not in labels and "synology-password:keller" in labels


@pytest.mark.asyncio
async def test_the_settings_form_refuses_bad_names_a_missing_account_and_too_many_devices(client, db_session, test_settings):
    await _discover(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)

    async def put(connections: list[dict[str, Any]]):
        return await client.put(
            f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": connections}}, headers=headers,
        )

    for name in ("mit leerzeichen", "..", "-x", "ä", "x" * 41):
        assert (await put([_entry(name)])).status_code == 422, name
    assert (await put([_entry("a", username=None)])).status_code == 422
    assert (await put([_entry(f"nas{i}") for i in range(11)])).status_code == 422
    assert (await put([_entry(f"nas{i}") for i in range(10)])).status_code == 200


# --- Kacheln je Gerät

@pytest.mark.asyncio
async def test_every_device_gets_three_tiles_named_after_it(client, db_session, test_settings):
    headers, _ = await _setup_two(client, db_session, test_settings)
    catalog = await _catalog(client, headers)
    assert sorted(catalog) == [
        "backups-ds920", "backups-keller", "status-ds920", "status-keller", "volumes-ds920", "volumes-keller",
    ]
    assert {i: (w["title"], w["data_endpoint"]) for i, w in catalog.items()} == {
        "status-ds920": ("Synology DSM · ds920", "widgets/status/ds920"),
        "volumes-ds920": ("Synology Speicher · ds920", "widgets/volumes/ds920"),
        "backups-ds920": ("Synology Hyper Backup · ds920", "widgets/backups/ds920"),
        "status-keller": ("Synology DSM · keller", "widgets/status/keller"),
        "volumes-keller": ("Synology Speicher · keller", "widgets/volumes/keller"),
        "backups-keller": ("Synology Hyper Backup · keller", "widgets/backups/keller"),
    }
    assert catalog["backups-keller"]["group"] == "backups" and catalog["status-keller"]["group"] == "servers"
    assert all(w["view"]["kind"] == "list" and w["permissions"] == ["hosts.read"] for w in catalog.values())


@pytest.mark.asyncio
async def test_each_tile_shows_the_data_of_its_own_device(client, db_session, test_settings):
    headers, fakes = await _setup_two(client, db_session, test_settings)
    fakes[DS415].data["SYNO.Storage.CGI.Storage"] = POOLS_STORAGE
    fakes[DS415].install_hyper_backup({"1": ("Nacht", OK_TASK)})
    await _service().clear()

    one = (await client.get(f"{API}/widgets/status/ds920", headers=headers)).json()
    two = (await client.get(f"{API}/widgets/status/keller", headers=headers)).json()
    assert (one["meta"]["model"], two["meta"]["model"]) == ("DS920+", "DS415play")
    assert one["meta"]["problems"] == 0 and two["meta"]["problems"] > 0
    volumes = (await client.get(f"{API}/widgets/volumes/keller", headers=headers)).json()
    assert [r["title"] for r in volumes["data"]][:1] == ["Speicherpool 1"], "ein Pool, der nicht in Ordnung ist, steht oben"
    backups = (await client.get(f"{API}/widgets/backups/keller", headers=headers)).json()
    assert [r["title"] for r in backups["data"]] == ["Nacht"]
    assert (await client.get(f"{API}/widgets/backups/ds920", headers=headers)).json()["data"][0]["title"] == "Keine Aufgaben"


@pytest.mark.asyncio
async def test_a_tile_of_a_device_that_is_gone_hides_itself(client, db_session, test_settings):
    headers, _ = await _setup_two(client, db_session, test_settings)
    for kind in ("status", "volumes", "backups"):
        res = await client.get(f"{API}/widgets/{kind}/entfernt", headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["meta"] == {"state": "not_configured"}
        assert body["data"] == [{
            "title": "entfernt nicht eingetragen", "subtitle": "Dieses Gerät steht nicht mehr in den Einstellungen.",
            "label": "nicht eingerichtet", "tone": "neutral",
        }]


@pytest.mark.asyncio
async def test_the_tiles_follow_the_settings_without_a_restart(client, db_session, test_settings):
    headers, _ = await _setup_two(client, db_session, test_settings)
    assert len(await _catalog(client, headers)) == 6

    await _put_settings(client, headers, {"connections": [
        _entry("ds920", url=DS920, username="eins"), _entry("keller", url=DS415, username="zwei"), _entry("buero", url="https://buero.test:5001", username="drei"),
    ]})
    catalog = await _catalog(client, headers)
    assert len(catalog) == 9 and catalog["status-buero"]["title"] == "Synology DSM · buero"

    await _put_settings(client, headers, {"connections": [_entry("keller", url=DS415, username="zwei")]})
    catalog = await _catalog(client, headers)
    assert sorted(catalog) == ["backups-keller", "status-keller", "volumes-keller"]
    assert catalog["status-keller"]["title"] == "Synology DSM", "mit einem Gerät steht sein Name nicht im Titel"

    await _put_settings(client, headers, {"connections": [_entry("keller", url=DS415, username="zwei"), _entry("ds920", url=DS920, username="eins")]})
    catalog = await _catalog(client, headers)
    assert catalog["status-keller"]["title"] == "Synology DSM · keller" and len(catalog) == 6, "der Titel folgt, nichts doppelt"


@pytest.mark.asyncio
async def test_without_a_device_the_three_tiles_stay_and_say_not_set_up(client, db_session, test_settings):
    await _discover(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)
    catalog = await _catalog(client, headers)
    assert sorted(catalog) == ["backups", "status", "volumes"]
    assert [catalog[i]["title"] for i in ("status", "volumes", "backups")] == ["Synology DSM", "Synology Speicher", "Synology Hyper Backup"]
    assert catalog["status"]["data_endpoint"] == "widgets/status"
    for kind in ("status", "volumes", "backups"):
        body = (await client.get(f"{API}/widgets/{kind}", headers=headers)).json()
        assert body["meta"]["state"] == "not_configured" and body["data"][0]["label"] == "nicht eingerichtet"


@pytest.mark.asyncio
async def test_a_device_with_an_unusable_or_double_name_is_an_error_row_without_a_tile_or_a_request(client, db_session, test_settings):
    headers, fakes = await _setup_two(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded[EXT]
    await loaded.ctx.settings.set({"connections": [
        _entry("ds920", url=DS920, username="eins"), _entry("../böse", url=DS415, username="zwei"), _entry("DS920", url=DS415, username="zwei"),
        _entry("keller", url=DS415, username="zwei", tls_insecure_skip_verify=True),
    ]})
    devices = await _devices(client, headers)
    assert [d["state"] for d in devices] == ["error", "error", "error", "ok"]
    assert "mehrfach" in devices[0]["message"] and "Kurzname" in devices[1]["message"] and "mehrfach" in devices[2]["message"]
    assert [d["config_problem"] for d in devices] == [True, True, True, False], "erneut abfragen hilft bei einem unbrauchbaren Eintrag nicht"
    assert sorted(await _catalog(client, headers)) == ["backups-keller", "status-keller", "volumes-keller"]
    assert fakes[DS920].requests == [], "für ein Gerät ohne brauchbaren Namen geht keine Anfrage raus"
    assert fakes[DS415].logins == 1, "nur „keller“ meldet sich an"
    health = await _health()
    assert health.healthy is False and set(health.details) == {"ds920", "../böse", "DS920", "keller"}


@pytest.mark.asyncio
async def test_a_name_twice_never_sends_the_stored_password_to_the_other_address(client, db_session, test_settings):
    evil_url = "https://evil.test:5001"
    fakes = {**_two(), evil_url: _dsm(model="DS-X", user="eins", password=PASSWORD)}
    headers, _ = await _setup_two(client, db_session, test_settings, fakes)
    evil = fakes[evil_url]
    swapped = [
        _entry("ds920", url=evil_url, username="eins"), _entry("ds920", url=DS920, username="eins"),
        _entry("keller", url=DS415, username="zwei", tls_insecure_skip_verify=True),
    ]

    refused = await client.put(f"/api/v1/extensions/{EXT}/settings", json={"values": {"connections": swapped}}, headers=headers)
    assert refused.status_code == 403 and refused.json()["code"] == "confirm_required", "das Löschen des Passworts verlangt die Bestätigung"

    saved = await client.put(
        f"/api/v1/extensions/{EXT}/settings", json={"current_password": "correct-horse-battery", "values": {"connections": swapped}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["secrets_cleared"] == ["synology-password:ds920"]
    assert [d["state"] for d in await _devices(client, headers)] == ["error", "error", "ok"]
    assert evil.requests == [] and fakes[DS920].requests == []


@pytest.mark.asyncio
async def test_the_module_alone_asks_neither_entry_of_a_double_name(client, db_session, test_settings):
    evil_url = "https://evil.test:5001"
    fakes = {**_two(), evil_url: _dsm(model="DS-X", user="eins", password=PASSWORD)}
    headers, _ = await _setup_two(client, db_session, test_settings, fakes)
    evil = fakes[evil_url]
    loaded = get_extension_runtime().loaded[EXT]
    # Am Kern vorbei gespeichert: das Passwort von „ds920“ liegt noch im Tresor.
    for order in ((evil_url, DS920), (DS920, evil_url)):
        await loaded.ctx.settings.set({"connections": [_entry("ds920", url=url, username="eins") for url in order]})
        assert [d["state"] for d in await _devices(client, headers, "?refresh=true")] == ["error", "error"]
    assert evil.requests == [] and fakes[DS920].requests == []
    assert "synology-password:ds920" in await _secret_labels(db_session)


@pytest.mark.asyncio
async def test_app_suggestions_name_each_device_once_there_are_several(client, db_session, test_settings):
    headers, _ = await _setup_two(client, db_session, test_settings)
    res = await client.get("/api/v1/apps/suggestions", headers=headers)
    assert res.status_code == 200, res.text
    assert [(s["name"], s["url"]) for s in res.json()["suggestions"]] == [("Synology DSM ds920", DS920), ("Synology DSM keller", DS415)]


# --- Meldungen je Gerät

def _healed() -> dict[str, Any]:
    healed = json.loads(json.dumps(POOLS_STORAGE))
    healed["storagePools"][0]["status"] = healed["volumes"][0]["status"] = "normal"
    healed["disks"][1].update(status="normal", smart_status="normal")
    return healed


async def _watch() -> dict[str, Any]:
    handler = get_extension_runtime().scheduler.get(EXT, "watch")
    assert handler is not None
    return await handler()


def _data_dir() -> Path:
    return Path(str(get_extension_runtime().loaded[EXT].ctx.data_dir))


async def _notification_rows(db_session) -> list[tuple[str, str, str | None, str | None]]:
    from nodvard_deck.models import Notification as NotificationRow

    rows = (await db_session.execute(
        select(NotificationRow).where(NotificationRow.source_ext_id == EXT).order_by(NotificationRow.ts, NotificationRow.title)
    )).scalars().all()
    return [(r.severity, r.title, r.correlation_id, (r.payload or {}).get("path")) for r in rows]


@pytest.mark.asyncio
async def test_each_device_reports_its_own_problems_with_its_name_in_the_title(client, db_session, test_settings):
    _, fakes = await _setup_two(client, db_session, test_settings)
    fakes[DS415].data["SYNO.Storage.CGI.Storage"] = POOLS_STORAGE

    result = await _watch()
    assert result["notified"] == 2 and "skipped" not in result
    rows = await _notification_rows(db_session)
    assert [(sev, title, cid, path) for sev, title, cid, path in rows if "Speicherpool" in title] == [
        ("warning", "keller: Speicherpool 1 – Eingeschränkt", "synology:keller:pool:reuse_1", "/ext/synology/synology?device=keller"),
    ]
    assert {title for _, title, _, _ in rows} == {"keller: Speicherpool 1 – Eingeschränkt", "keller: Laufwerk 2 – SMART: Fällt aus"}
    assert not any(title.startswith("ds920") for _, title, _, _ in rows), "ds920 ist in Ordnung und meldet nichts"
    assert (await _watch())["notified"] == 0, "nur Wechsel werden gemeldet"


@pytest.mark.asyncio
async def test_two_devices_with_the_same_problem_report_twice_and_recover_separately(client, db_session, test_settings):
    _, fakes = await _setup_two(client, db_session, test_settings)
    for fake in fakes.values():
        fake.data["SYNO.Storage.CGI.Storage"] = json.loads(json.dumps(POOLS_STORAGE))
        fake.data["SYNO.Storage.CGI.Storage"]["disks"][1].update(status="normal", smart_status="normal")
    assert (await _watch())["notified"] == 2
    titles = sorted(t for _, t, _, _ in await _notification_rows(db_session))
    assert titles == ["ds920: Speicherpool 1 – Eingeschränkt", "keller: Speicherpool 1 – Eingeschränkt"]

    # Nur ds920 wird gesund: Es kommt genau eine Entwarnung, keller bleibt offen und meldet nicht noch einmal.
    healed = _healed()
    fakes[DS920].data["SYNO.Storage.CGI.Storage"] = healed
    await _service().clear()
    assert (await _watch())["notified"] == 1
    rows = await _notification_rows(db_session)
    assert rows[-1][:2] == ("info", "ds920: Speicherpool 1 wieder in Ordnung")
    assert rows[-1][2] == "synology:ds920:pool:reuse_1", "die Entwarnung löst die offene Meldung desselben Geräts ab"
    assert "pool:reuse_1" in json.loads((_data_dir() / "alerts-keller.json").read_text(encoding="utf-8"))
    assert json.loads((_data_dir() / "alerts-ds920.json").read_text(encoding="utf-8")) == {}

    fakes[DS415].data["SYNO.Storage.CGI.Storage"] = healed
    await _service().clear()
    assert (await _watch())["notified"] == 1
    assert (await _notification_rows(db_session))[-1][1] == "keller: Speicherpool 1 wieder in Ordnung"


@pytest.mark.asyncio
async def test_an_unreachable_device_keeps_its_state_and_does_not_stop_the_other(client, db_session, test_settings):
    _, fakes = await _setup_two(client, db_session, test_settings)
    for fake in fakes.values():
        fake.data["SYNO.Storage.CGI.Storage"] = json.loads(json.dumps(POOLS_STORAGE))
        fake.data["SYNO.Storage.CGI.Storage"]["disks"][1].update(status="normal", smart_status="normal")
    assert (await _watch())["notified"] == 2

    fakes[DS415].error = httpx.ConnectError("refused")
    fakes[DS920].data["SYNO.Storage.CGI.Storage"] = _healed()
    await _service().clear()
    result = await _watch()
    assert result["notified"] == 1 and result["skipped"].startswith("keller: ")
    assert "ds920: Speicherpool 1 wieder in Ordnung" in [t for _, t, _, _ in await _notification_rows(db_session)]
    assert "pool:reuse_1" in json.loads((_data_dir() / "alerts-keller.json").read_text(encoding="utf-8")), "ohne Antwort bleibt der Stand"


@pytest.mark.asyncio
async def test_a_single_device_keeps_its_model_in_the_titles_and_the_plain_page_link(client, db_session, test_settings):
    await _discover(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)
    fake = _dsm(model="DS220+", user="eins", password=PASSWORD)
    fake.data["SYNO.Storage.CGI.Storage"] = POOLS_STORAGE
    _wire({DS920: fake})
    await _put_settings(client, headers, {"connections": [_entry("ds920", url=DS920, username="eins")]})
    await _set_password(client, headers, PASSWORD, "ds920")
    await _watch()
    rows = await _notification_rows(db_session)
    assert ("warning", "DS220+: Speicherpool 1 – Eingeschränkt", "synology:ds920:pool:reuse_1", "/ext/synology/synology") in rows


@pytest.mark.asyncio
async def test_the_state_of_a_removed_device_is_cleaned_up(client, db_session, test_settings):
    headers, fakes = await _setup_two(client, db_session, test_settings)
    for fake in fakes.values():
        fake.data["SYNO.Storage.CGI.Storage"] = json.loads(json.dumps(POOLS_STORAGE))
    await _watch()
    assert (_data_dir() / "alerts-ds920.json").exists() and (_data_dir() / "alerts-keller.json").exists()

    await _put_settings(client, headers, {"connections": [_entry("keller", url=DS415, username="zwei")]})
    await _watch()
    assert not (_data_dir() / "alerts-ds920.json").exists()
    assert (_data_dir() / "alerts-keller.json").exists()


def test_state_files_and_notification_ids_are_per_device_except_for_the_adopted_one():
    base = Path("/data")
    assert syn_watch.state_path(base, "synology") == base / "alerts.json"
    assert syn_watch.state_path(base, "keller") == base / "alerts-keller.json"
    assert syn_watch.correlation_id("synology", "pool:reuse_1") == "synology:pool:reuse_1"
    assert syn_watch.correlation_id("keller", "pool:reuse_1") == "synology:keller:pool:reuse_1"
    assert syn_watch.correlation_id("keller", "temperature") != syn_watch.correlation_id("ds920", "temperature")


# --- Übernahme der alten Einrichtung mit nur einem Gerät

LEGACY_SETTINGS = {
    "base_url": DS920, "username": "eins", "tls_insecure_skip_verify": True, "notify": True, "notify_update": False, "disk_temp_warn_c": 60,
}


async def _seed_legacy(db_session, test_settings, *, settings: dict[str, Any] | None = None, password: str | None = PASSWORD) -> None:
    """Der Zustand einer Installation von vorher: Einzelfelder in den Einstellungen und das Passwort unter dem alten Namen."""
    await _discover(db_session, test_settings)
    record = await db_session.get(ExtensionRecord, EXT)
    record.settings = dict(LEGACY_SETTINGS if settings is None else settings)
    if password is not None:
        keyring = vault.load_keyring(test_settings)
        await vault.create_secret(
            db_session, keyring, label="synology-password", kind="generic", plaintext=password, owner_ext_id=EXT, description="alt",
        )
    await db_session.commit()


async def _secret_labels(db_session) -> set[str]:
    return set((await db_session.execute(select(Secret.label))).scalars().all())


@pytest.mark.asyncio
async def test_the_old_single_setup_becomes_the_first_device_and_keeps_working(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    await _seed_legacy(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)
    fake = _dsm(model="DS920+", user="eins", password=PASSWORD)
    _wire({DS920: fake})

    stored = (await client.get(f"/api/v1/extensions/{EXT}/settings", headers=headers)).json()
    assert stored["values"]["connections"] == [
        {"name": "synology", "base_url": DS920, "username": "eins", "tls_insecure_skip_verify": True},
    ]
    assert stored["values"]["notify_update"] is False and stored["values"]["disk_temp_warn_c"] == 60, "die gemeinsamen Einstellungen bleiben"
    assert {s["label"]: s["is_set"] for s in stored["secrets"]} == {"synology-password:synology": True}

    # Das Passwort wurde mitgenommen: Die Anmeldung klappt ohne neue Eingabe.
    body = (await client.get(f"{API}/overview", headers=headers)).json()
    assert (body["state"], body["device"], body["system"]["model"]) == ("ok", "synology", "DS920+")
    assert fake.insecure_requests, "auch die TLS-Wahl ist übernommen"
    assert PASSWORD not in caplog.text and PASSWORD not in json.dumps(stored)


@pytest.mark.asyncio
async def test_the_adoption_leaves_the_old_fields_and_the_old_password_for_a_way_back(client, db_session, test_settings):
    await _seed_legacy(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)

    record = await db_session.get(ExtensionRecord, EXT)
    await db_session.refresh(record)
    for field, value in LEGACY_SETTINGS.items():
        assert record.settings[field] == value, field
    assert {"synology-password", "synology-password:synology"} <= await _secret_labels(db_session)

    # Beide Einträge tragen dasselbe Passwort; dem neuen sieht man die Herkunft an.
    new = (await db_session.execute(select(Secret).where(Secret.label == "synology-password:synology"))).scalar_one()
    assert new.owner_ext_id == EXT and "Übernommen" in new.description


@pytest.mark.asyncio
@pytest.mark.parametrize("then", [[], None, [_entry("ds920", url=DS920, username="eins")]], ids=["removed", "null", "renamed"])
async def test_the_old_password_entry_goes_with_the_adopted_device(client, db_session, test_settings, then):
    await _seed_legacy(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)
    assert {"synology-password", "synology-password:synology"} <= await _secret_labels(db_session)

    await _put_settings(client, headers, {"connections": then})
    assert await _secret_labels(db_session) & {"synology-password", "synology-password:synology"} == set()


@pytest.mark.asyncio
async def test_the_old_password_entry_goes_at_start_if_removed_while_off(client, db_session, test_settings):
    await _seed_legacy(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)
    disabled = await client.post(f"/api/v1/extensions/{EXT}/disable", headers=headers)
    assert disabled.status_code == 200, disabled.text

    await _put_settings(client, headers, {"connections": [_entry("keller", url=DS415, username="zwei")]})
    assert "synology-password" in await _secret_labels(db_session)
    await _enable_extension(client, headers)
    assert "synology-password" not in await _secret_labels(db_session)


@pytest.mark.asyncio
async def test_the_old_password_entry_stays_while_the_adopted_device_does(client, db_session, test_settings):
    await _seed_legacy(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)
    await _put_settings(client, headers, {"notify": False})
    stored = (await client.get(f"/api/v1/extensions/{EXT}/settings", headers=headers)).json()
    await _put_settings(client, headers, {"connections": [*stored["values"]["connections"], _entry("keller", url=DS415, username="zwei")]})
    assert {"synology-password", "synology-password:synology"} <= await _secret_labels(db_session)


@pytest.mark.asyncio
async def test_an_old_password_entry_without_an_adoption_is_left_alone(client, db_session, test_settings):
    await _seed_legacy(db_session, test_settings, settings={"username": "eins"})
    headers = await _login(client)
    await _enable_extension(client, headers)
    await _put_settings(client, headers, {"connections": [_entry("keller", url=DS415, username="zwei")]})
    assert "synology-password" in await _secret_labels(db_session)


async def _leftover_copy(db_session, test_settings, value: str) -> None:
    keyring = vault.load_keyring(test_settings)
    await vault.create_secret(
        db_session, keyring, label="synology-password:synology", kind="generic", plaintext=value, owner_ext_id=EXT, description="Rest",
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_a_leftover_copy_of_the_password_is_renewed_from_the_old_entry(client, db_session, test_settings):
    await _seed_legacy(db_session, test_settings)
    await _leftover_copy(db_session, test_settings, "Altes&Passwort 3")
    headers = await _login(client)
    await _enable_extension(client, headers)
    fake = _dsm(model="DS920+", user="eins", password=PASSWORD)
    _wire({DS920: fake})
    body = (await client.get(f"{API}/overview", headers=headers)).json()
    assert body["state"] == "ok" and fake.failed_logins == 0, "das Passwort kommt aus dem alten Eintrag, nicht aus der Kopie von früher"


@pytest.mark.asyncio
async def test_a_leftover_copy_without_an_old_password_is_removed(client, db_session, test_settings):
    await _seed_legacy(db_session, test_settings, password=None)
    await _leftover_copy(db_session, test_settings, "Altes&Passwort 3")
    headers = await _login(client)
    await _enable_extension(client, headers)
    assert "synology-password:synology" not in await _secret_labels(db_session)
    [device] = await _devices(client, headers)
    assert device["state"] == "not_configured" and "fehlt das Passwort" in device["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("removed", [[], None], ids=["empty list", "null"])
async def test_the_adoption_runs_only_once_and_never_brings_back_a_removed_device(client, db_session, test_settings, removed):
    await _seed_legacy(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)
    await _put_settings(client, headers, {"connections": removed})
    assert "synology-password:synology" not in await _secret_labels(db_session)

    for _ in range(2):
        assert (await client.post(f"/api/v1/extensions/{EXT}/disable", headers=headers)).status_code == 200
        await _enable_extension(client, headers)
    stored = (await client.get(f"/api/v1/extensions/{EXT}/settings", headers=headers)).json()
    assert not stored["values"].get("connections")
    assert await _devices(client, headers) == []
    assert "synology-password:synology" not in await _secret_labels(db_session)


@pytest.mark.asyncio
async def test_a_new_installation_has_nothing_to_adopt(client, db_session, test_settings):
    await _discover(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)
    record = await db_session.get(ExtensionRecord, EXT)
    await db_session.refresh(record)
    assert "connections" not in (record.settings or {})
    assert "synology-password:synology" not in await _secret_labels(db_session)


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", [{"username": "eins"}, {"base_url": "  ", "username": "eins"}, {"notify": False}])
async def test_without_an_old_address_nothing_is_adopted(client, db_session, test_settings, settings):
    await _seed_legacy(db_session, test_settings, settings=settings, password=None)
    headers = await _login(client)
    await _enable_extension(client, headers)
    record = await db_session.get(ExtensionRecord, EXT)
    await db_session.refresh(record)
    assert "connections" not in record.settings and record.settings == settings
    assert await _devices(client, headers) == []


@pytest.mark.asyncio
async def test_an_old_setup_without_a_password_still_becomes_a_device_that_asks_for_one(client, db_session, test_settings):
    await _seed_legacy(db_session, test_settings, password=None)
    headers = await _login(client)
    await _enable_extension(client, headers)
    [device] = await _devices(client, headers)
    assert (device["device"], device["state"]) == ("synology", "not_configured")
    assert "fehlt das Passwort" in device["message"]
    assert "synology-password:synology" not in await _secret_labels(db_session)


@pytest.mark.asyncio
async def test_an_old_setup_with_a_partial_or_odd_entry_is_adopted_as_it_is(client, db_session, test_settings):
    await _seed_legacy(db_session, test_settings, settings={"base_url": "https://nas.test/webman/index.cgi", "tls_fingerprint_sha256": "kaputt"})
    headers = await _login(client)
    await _enable_extension(client, headers)
    [device] = await _devices(client, headers)
    assert device["device"] == "synology" and device["state"] == "error" and "Fingerabdruck" in device["message"]


@pytest.mark.asyncio
async def test_a_failing_adoption_does_not_keep_the_module_from_loading_and_is_retried(client, db_session, test_settings, monkeypatch, caplog):
    from nodvard_deck.ext import context as ext_context

    await _seed_legacy(db_session, test_settings)

    async def boom(self, **kwargs: Any):
        raise RuntimeError(f"Tresor kaputt {PASSWORD}")

    monkeypatch.setattr(ext_context.SecretsHandle, "create", boom)
    headers = await _login(client)
    with caplog.at_level(logging.WARNING):
        await _enable_extension(client, headers)
    record = await db_session.get(ExtensionRecord, EXT)
    await db_session.refresh(record)
    assert record.state == "enabled"
    assert "connections" not in record.settings, "ohne das Passwort wird nichts halb übernommen"
    assert "ließ sich nicht übernehmen" in caplog.text

    monkeypatch.undo()
    assert (await client.post(f"/api/v1/extensions/{EXT}/disable", headers=headers)).status_code == 200
    await _enable_extension(client, headers)
    await db_session.refresh(record)
    assert [c["name"] for c in record.settings["connections"]] == ["synology"]
    assert "synology-password:synology" in await _secret_labels(db_session)


@pytest.mark.asyncio
async def test_the_adopted_device_keeps_the_old_tile_ids_and_titles(client, db_session, test_settings):
    await _seed_legacy(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)
    catalog = await _catalog(client, headers)
    assert sorted(catalog) == ["backups", "status", "volumes"], "so bleiben die Kacheln auf der Startseite an ihrem Platz"
    assert [catalog[i]["title"] for i in ("status", "volumes", "backups")] == ["Synology DSM", "Synology Speicher", "Synology Hyper Backup"]
    assert catalog["status"]["data_endpoint"] == "widgets/status/synology"
    assert (await client.get(f"{API}/widgets/status", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_with_a_second_device_the_adopted_one_keeps_its_ids_and_gains_its_name_in_the_title(client, db_session, test_settings):
    await _seed_legacy(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)
    fakes = _two()
    _wire(fakes)
    stored = (await client.get(f"/api/v1/extensions/{EXT}/settings", headers=headers)).json()["values"]["connections"]
    await _put_settings(client, headers, {"connections": [*stored, _entry("keller", url=DS415, username="zwei")]})
    await _set_password(client, headers, PASSWORD_B, "keller")

    catalog = await _catalog(client, headers)
    assert sorted(catalog) == ["backups", "backups-keller", "status", "status-keller", "volumes", "volumes-keller"]
    assert catalog["status"]["title"] == "Synology DSM · synology" and catalog["status-keller"]["title"] == "Synology DSM · keller"
    assert [d["state"] for d in await _devices(client, headers)] == ["ok", "ok"], "das übernommene Passwort gilt weiter"


@pytest.mark.asyncio
async def test_the_old_alert_state_and_ids_carry_on_for_the_adopted_device(client, db_session, test_settings):
    await _seed_legacy(db_session, test_settings, settings={**LEGACY_SETTINGS, "notify_update": True})
    headers = await _login(client)
    await _enable_extension(client, headers)
    fake = _dsm(model="DS220+", user="eins", password=PASSWORD)
    fake.data["SYNO.Storage.CGI.Storage"] = json.loads(json.dumps(POOLS_STORAGE))
    fake.data["SYNO.Storage.CGI.Storage"]["disks"][1].update(status="normal", smart_status="normal")
    _wire({DS920: fake})

    # Der Stand, wie ihn der Wächter vor der Umstellung geschrieben hat: die Pool-Meldung ging schon raus.
    (_data_dir() / "alerts.json").write_text(
        json.dumps({"pool:reuse_1": {"streak": 4, "alerted": True, "subject": "Speicherpool 1", "severity": "warning"}}), encoding="utf-8",
    )
    result = await _watch()
    assert result["notified"] == 0, "dieselbe Meldung kommt nach der Umstellung nicht noch einmal"
    assert await _notification_rows(db_session) == []

    fake.data["SYNO.Storage.CGI.Storage"] = _healed()
    await _service().clear()
    assert (await _watch())["notified"] == 1
    rows = await _notification_rows(db_session)
    assert rows == [("info", "DS220+: Speicherpool 1 wieder in Ordnung", "synology:pool:reuse_1", "/ext/synology/synology")]
    assert json.loads((_data_dir() / "alerts.json").read_text(encoding="utf-8")) == {}
    assert not (_data_dir() / "alerts-synology.json").exists()


@pytest.mark.asyncio
async def test_old_addresses_without_a_device_still_answer_for_the_first_one(client, db_session, test_settings):
    await _seed_legacy(db_session, test_settings)
    headers = await _login(client)
    await _enable_extension(client, headers)
    _wire({DS920: _dsm(model="DS920+", user="eins", password=PASSWORD)})
    old = (await client.get(f"{API}/overview", headers=headers)).json()
    assert old["state"] == "ok" and old["url"] == DS920
    for field in ("system", "load", "volumes", "disks", "update", "problems", "pools", "backups", "system_note", "backup_note"):
        assert field in old, field
    for kind in ("status", "volumes", "backups"):
        assert (await client.get(f"{API}/widgets/{kind}", headers=headers)).json()["meta"]["state"] == "ok"
    new = (await client.get(f"{API}/devices", headers=headers)).json()["devices"][0]
    assert {k: v for k, v in new.items() if k != "device"} == {k: v for k, v in old.items() if k != "device"}


def test_the_default_name_of_the_adopted_device_is_valid_and_documented():
    assert syn_config.LEGACY_NAME == "synology" and syn_config.is_valid_name(syn_config.LEGACY_NAME)
    assert syn_config.password_label(syn_config.LEGACY_NAME) == "synology-password:synology"
    assert syn_config.PASSWORD_LABEL == "synology-password"
