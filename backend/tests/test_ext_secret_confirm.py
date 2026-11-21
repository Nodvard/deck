"""Gespeicherte Zugangsdaten werden nur mit Passwort gelöscht, ersetzt oder einer schwächeren Zertifikatsprüfung
überlassen, auch über die Verbindungs-Routen von Proxmox VE und Proxmox-Backups."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

import pytest
from nodvard_deck.models import ExtensionRecord, Secret
from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services.extension_setup import (
    missing_secrets,
    secret_slot_for,
    secrets_exposed_by_tls,
    secrets_to_clear,
    tls_weakened,
)
from sqlalchemy import select, update

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
PASSWORD = "correct-horse-battery"
PIN_A = ":".join(["AB"] * 32)
PIN_B = ":".join(["CD"] * 32)
CONN = {"name": "pve1", "base_url": "https://192.168.2.10:8006", "token_id": "root@pam!deck"}
EXTS = [("proxmox", "proxmox-token"), ("backups", "backups-token")]


async def _owner_with(client, db_session, test_settings, *ext_ids: str) -> dict:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post(
        "/api/v1/auth/bootstrap", json={"username": "owner1", "password": PASSWORD, "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": PASSWORD})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    for ext_id in ext_ids:
        assert (await client.post(f"/api/v1/extensions/{ext_id}/enable", headers=headers)).status_code == 200
    return headers


async def _has_secret(db_session, label: str) -> bool:
    return (await db_session.execute(select(Secret.id).where(Secret.label == label))).first() is not None


async def _connections(db_session, ext_id: str) -> list[dict]:
    record = await db_session.get(ExtensionRecord, ext_id)
    await db_session.refresh(record)
    return list((record.settings or {}).get("connections") or [])


async def _with_token(client, headers, ext_id: str, **extra) -> None:
    created = await client.post(f"/api/v1/ext/{ext_id}/connections", json={**CONN, **extra}, headers=headers)
    assert created.status_code == 201, created.text
    stored = await client.post(f"/api/v1/ext/{ext_id}/connections/pve1/token", json={"value": "tok"}, headers=headers)
    assert stored.status_code == 204, stored.text


TLS_CASES = [
    ({}, {"tls_insecure_skip_verify": True}, True),
    ({"tls_insecure_skip_verify": False}, {"tls_insecure_skip_verify": True}, True),
    ({"tls_insecure_skip_verify": True}, {"tls_insecure_skip_verify": False}, False),
    ({"tls_insecure_skip_verify": True}, {"tls_fingerprint_sha256": PIN_A}, False),
    ({}, {"tls_fingerprint_sha256": PIN_A}, True),
    ({"tls_fingerprint_sha256": PIN_A}, {"tls_fingerprint_sha256": PIN_B}, True),
    ({"tls_fingerprint_sha256": PIN_A}, {"tls_fingerprint_sha256": PIN_A.replace(":", "").lower()}, False),
    ({"tls_fingerprint_sha256": PIN_A}, {}, True),
    ({"tls_fingerprint_sha256": PIN_A, "tls_insecure_skip_verify": False}, {"tls_insecure_skip_verify": False}, True),
    ({}, {"tls_fingerprint_sha256": "kaputt", "tls_insecure_skip_verify": True}, False),
    ({"tls_insecure_skip_verify": True}, {}, False),
    ({"tls_fingerprint_sha256": "kaputt"}, {"tls_fingerprint_sha256": PIN_A}, True),
    ({}, {"base_url": "https://anders:8006"}, False),
    # Der Fingerabdruck gilt vor dem Schalter: so eine Verbindung prüft streng.
    ({"tls_insecure_skip_verify": True, "tls_fingerprint_sha256": PIN_A}, {"tls_fingerprint_sha256": PIN_B}, True),
    ({"tls_insecure_skip_verify": True, "tls_fingerprint_sha256": PIN_A},
     {"tls_insecure_skip_verify": True, "tls_fingerprint_sha256": PIN_B}, True),
    ({"tls_insecure_skip_verify": True, "tls_fingerprint_sha256": PIN_A}, {"tls_insecure_skip_verify": True}, True),
    ({"tls_insecure_skip_verify": True, "tls_fingerprint_sha256": PIN_A}, {"tls_fingerprint_sha256": PIN_A}, False),
    ({"tls_fingerprint_sha256": PIN_A}, {"tls_fingerprint_sha256": PIN_A, "tls_insecure_skip_verify": True}, False),
    ({"tls_fingerprint_sha256": "kaputt"}, {}, True),
    ({"tls_fingerprint_sha256": "kaputt"}, {"tls_fingerprint_sha256": "auch kaputt"}, False),
    ({}, {"tls_fingerprint_sha256": "kaputt"}, False),
]


@pytest.mark.parametrize(("before", "after", "weaker"), TLS_CASES)
def test_tls_weakened(before, after, weaker):
    assert tls_weakened(before, after) is weaker


@pytest.fixture
def module_tls_weakened(monkeypatch, request):
    ext_id = request.param
    package = f"nodvard_deck_ext_{ext_id}"
    monkeypatch.syspath_prepend(str(REPO_EXTENSIONS_DIR / ext_id / "src"))
    yield __import__(f"{package}.certificate", fromlist=["_"]).tls_weakened
    for name in [m for m in sys.modules if m == package or m.startswith(f"{package}.")]:
        sys.modules.pop(name, None)


@pytest.mark.parametrize("module_tls_weakened", ["proxmox", "backups"], indirect=True)
def test_modules_judge_the_check_like_the_core(module_tls_weakened):
    for before, after, weaker in TLS_CASES:
        assert module_tls_weakened(before, after) is weaker, (before, after)


def test_exposed_labels_follow_the_entry_and_skip_new_ones():
    schema = {"x-secrets": [{"label": "t:{name}", "per_item": "connections"}, {"label": "oben"}]}
    old = {"connections": [{"name": "a"}, {"name": "b"}]}
    new = {"connections": [{"name": "a"}, {"name": "b", "tls_insecure_skip_verify": True},
                           {"name": "c", "tls_insecure_skip_verify": True}]}
    assert secrets_exposed_by_tls(schema, old, new) == ["t:b"]
    assert secrets_exposed_by_tls(schema, {}, {"tls_insecure_skip_verify": True}) == ["oben"]
    assert secrets_exposed_by_tls(schema, old, old) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"tls_insecure_skip_verify": True, "tls_fingerprint_sha256": None},
        {"tls_fingerprint_sha256": PIN_B},
        {"tls_fingerprint_sha256": None},
    ],
    ids=["ohne-pruefung", "anderer-fingerabdruck", "fingerabdruck-weg"],
)
async def test_settings_need_the_password_before_a_token_meets_a_weaker_check(
    client, db_session, test_settings, change,
):
    headers = await _owner_with(client, db_session, test_settings, "proxmox")
    await _with_token(client, headers, "proxmox", tls_fingerprint_sha256=PIN_A)
    before = await _connections(db_session, "proxmox")
    url = "/api/v1/extensions/proxmox/settings"

    refused = await client.put(url, json={"values": {"connections": [{**before[0], **change}]}}, headers=headers)
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "confirm_required"
    assert await _connections(db_session, "proxmox") == before
    wrong = await client.put(
        url, json={"current_password": "falsch-falsch", "values": {"connections": [{**before[0], **change}]}},
        headers=headers,
    )
    assert wrong.status_code == 400, wrong.text
    assert await _connections(db_session, "proxmox") == before

    saved = await client.put(
        url, json={"current_password": PASSWORD, "values": {"connections": [{**before[0], **change}]}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["secrets_cleared"] == []
    assert await _has_secret(db_session, "proxmox-token:pve1")
    audit = await client.get("/api/v1/audit?action=extension.settings", headers=headers)
    rows = audit.json()["items"] if isinstance(audit.json(), dict) else audit.json()
    assert any(r["detail"].get("tls_weakened") == ["proxmox-token:pve1"] for r in rows)


@pytest.mark.asyncio
async def test_settings_without_a_token_or_with_a_stricter_check_need_no_password(client, db_session, test_settings):
    headers = await _owner_with(client, db_session, test_settings, "proxmox")
    url = "/api/v1/extensions/proxmox/settings"
    plain = await client.put(
        url, json={"values": {"connections": [{**CONN, "tls_insecure_skip_verify": True}]}}, headers=headers,
    )
    # Neuer Eintrag ohne Token: nichts im Tresor, also auch keine Rückfrage.
    assert plain.status_code == 200, plain.text
    put = await client.put(
        "/api/v1/extensions/proxmox/secrets",
        json={"current_password": PASSWORD, "label": "proxmox-token:pve1", "value": "tok"}, headers=headers,
    )
    assert put.status_code == 204, put.text
    stricter = await client.put(
        url, json={"values": {"connections": [{**CONN, "tls_fingerprint_sha256": PIN_A}]}}, headers=headers,
    )
    assert stricter.status_code == 200, stricter.text
    assert await _has_secret(db_session, "proxmox-token:pve1")


@pytest.mark.asyncio
async def test_settings_of_a_single_server_module_ask_before_turning_the_check_off(client, db_session, test_settings):
    headers = await _owner_with(client, db_session, test_settings, "pihole")
    url = "/api/v1/extensions/pihole/settings"
    assert (await client.put(url, json={"values": {"url": "https://pihole.test"}}, headers=headers)).status_code == 200
    put = await client.put(
        "/api/v1/extensions/pihole/secrets",
        json={"current_password": PASSWORD, "label": "network-pihole-password", "value": "pw"}, headers=headers,
    )
    assert put.status_code == 204, put.text
    refused = await client.put(url, json={"values": {"tls_insecure_skip_verify": True}}, headers=headers)
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "confirm_required"
    record = await db_session.get(ExtensionRecord, "pihole")
    await db_session.refresh(record)
    assert record.settings.get("tls_insecure_skip_verify") is not True


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext_id", "prefix"), EXTS)
@pytest.mark.parametrize(
    "change",
    [
        {"tls_insecure_skip_verify": True, "tls_fingerprint_sha256": None},
        {"tls_fingerprint_sha256": PIN_B},
        {"tls_fingerprint_sha256": None},
        {"base_url": "https://angreifer:8006"},
    ],
    ids=["ohne-pruefung", "anderer-fingerabdruck", "fingerabdruck-weg", "neue-adresse"],
)
async def test_connection_route_leaves_a_stored_token_to_the_core(
    client, db_session, test_settings, ext_id, prefix, change,
):
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    await _with_token(client, headers, ext_id, tls_fingerprint_sha256=PIN_A)
    before = await _connections(db_session, ext_id)

    refused = await client.put(f"/api/v1/ext/{ext_id}/connections/pve1", json=change, headers=headers)
    assert refused.status_code == 409, refused.text
    assert f"„{prefix}:pve1“" in refused.json()["detail"]
    assert await _connections(db_session, ext_id) == before
    assert await _has_secret(db_session, f"{prefix}:pve1")

    # Harmlose Änderungen gehen weiter ohne Passwort.
    toggled = await client.put(f"/api/v1/ext/{ext_id}/connections/pve1", json={"enabled": False}, headers=headers)
    assert toggled.status_code == 200, toggled.text
    assert toggled.json()["has_token"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext_id", "prefix"), EXTS)
async def test_connection_route_changes_freely_without_a_token(client, db_session, test_settings, ext_id, prefix):
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    created = await client.post(
        f"/api/v1/ext/{ext_id}/connections", json={**CONN, "tls_fingerprint_sha256": PIN_A}, headers=headers,
    )
    assert created.status_code == 201, created.text
    for change in ({"tls_fingerprint_sha256": PIN_B}, {"tls_insecure_skip_verify": True}, {"base_url": "https://neu:8006"}):
        r = await client.put(f"/api/v1/ext/{ext_id}/connections/pve1", json=change, headers=headers)
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext_id", "prefix"), EXTS)
async def test_connection_routes_never_delete_a_stored_token(client, db_session, test_settings, ext_id, prefix):
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    await _with_token(client, headers, ext_id)

    removed = await client.delete(f"/api/v1/ext/{ext_id}/connections/pve1", headers=headers)
    assert removed.status_code == 204, removed.text
    assert await _connections(db_session, ext_id) == []
    assert await _has_secret(db_session, f"{prefix}:pve1")

    # Eine neue Verbindung unter dem Namen erbt das Token nicht und löscht es auch nicht.
    again = await client.post(
        f"/api/v1/ext/{ext_id}/connections", json={**CONN, "base_url": "https://angreifer:8006"}, headers=headers,
    )
    assert again.status_code == 409, again.text
    assert f"„{prefix}:pve1“" in again.json()["detail"]
    assert "früheren Verbindung" in again.json()["detail"]
    assert await _connections(db_session, ext_id) == []
    assert await _has_secret(db_session, f"{prefix}:pve1")


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext_id", "prefix"), EXTS)
@pytest.mark.parametrize(
    "change",
    [{"tls_fingerprint_sha256": PIN_B}, {"tls_fingerprint_sha256": None}],
    ids=["anderer-fingerabdruck", "fingerabdruck-weg"],
)
async def test_a_pin_counts_before_an_old_switch(client, db_session, test_settings, ext_id, prefix, change):
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    await _with_token(client, headers, ext_id)
    record = await db_session.get(ExtensionRecord, ext_id)
    await db_session.refresh(record)
    record.settings = {
        **record.settings,
        "connections": [{**record.settings["connections"][0], "tls_insecure_skip_verify": True, "tls_fingerprint_sha256": PIN_A}],
    }
    await db_session.commit()
    before = await _connections(db_session, ext_id)

    refused = await client.put(f"/api/v1/ext/{ext_id}/connections/pve1", json=change, headers=headers)
    assert refused.status_code == 409, refused.text
    assert await _connections(db_session, ext_id) == before

    core = await client.put(
        f"/api/v1/extensions/{ext_id}/settings", json={"values": {"connections": [{**before[0], **change}]}},
        headers=headers,
    )
    assert core.status_code == 403, core.text
    assert core.json()["code"] == "confirm_required"
    assert await _connections(db_session, ext_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext_id", "prefix"), EXTS)
async def test_dropping_the_pin_of_a_stored_token_needs_the_password(client, db_session, test_settings, ext_id, prefix):
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    await _with_token(client, headers, ext_id, tls_fingerprint_sha256=PIN_A, tls_insecure_skip_verify=False)
    before = await _connections(db_session, ext_id)
    assert before[0]["tls_fingerprint_sha256"] == PIN_A and before[0]["tls_insecure_skip_verify"] is False

    route = await client.put(f"/api/v1/ext/{ext_id}/connections/pve1", json={"tls_fingerprint_sha256": None},
                             headers=headers)
    assert route.status_code == 409, route.text
    assert await _connections(db_session, ext_id) == before

    url = f"/api/v1/extensions/{ext_id}/settings"
    dropped = {k: v for k, v in before[0].items() if k != "tls_fingerprint_sha256"}
    for entry in (dropped, {**before[0], "tls_fingerprint_sha256": ""}):
        core = await client.put(url, json={"values": {"connections": [entry]}}, headers=headers)
        assert core.status_code == 403, core.text
        assert core.json()["code"] == "confirm_required"
        assert await _connections(db_session, ext_id) == before

    saved = await client.put(
        url, json={"current_password": PASSWORD, "values": {"connections": [dropped]}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert await _has_secret(db_session, f"{prefix}:pve1")


def test_a_pin_counts_only_where_the_schema_has_the_field():
    broken = {"tls_insecure_skip_verify": True, "tls_fingerprint_sha256": "kaputt"}
    item = {"type": "object", "properties": {"name": {}, "tls_insecure_skip_verify": {}}}
    without_pin = {
        "properties": {"connections": {"type": "array", "items": item}, "tls_insecure_skip_verify": {}},
        "x-secrets": [{"label": "k:{name}", "per_item": "connections"}, {"label": "oben"}],
    }
    old, new = {"connections": [{"name": "a"}]}, {"connections": [{"name": "a", **broken}], **broken}
    assert secrets_exposed_by_tls(without_pin, old, new) == ["k:a", "oben"]

    with_pin = {
        "properties": {
            "connections": {"type": "array", "items": {**item, "properties": {**item["properties"],
                                                                                "tls_fingerprint_sha256": {}}}},
            "tls_insecure_skip_verify": {}, "tls_fingerprint_sha256": {},
        },
        "x-secrets": without_pin["x-secrets"],
    }
    assert secrets_exposed_by_tls(with_pin, old, new) == []


# Module mit Schalter, aber ohne Fingerabdruck im Schema: (Kennung, Grundwerte, Geheimnis, Eintrag oder None).
SKIP_ONLY = [
    ("uptime-kuma", {"connections": [{"name": "kuma", "base_url": "https://kuma.test"}]}, "uptime-kuma-key:kuma",
     "connections"),
    ("nextcloud", {"base_url": "https://cloud.test", "username": "deck"}, "nextcloud-app-password", None),
    ("npm", {"url": "https://npm.test"}, "network-npm-password", None),
    ("pihole", {"url": "https://pihole.test"}, "network-pihole-password", None),
]


def test_skip_only_table_matches_the_schemas():
    import json

    found = set()
    for path in REPO_EXTENSIONS_DIR.glob("*/settings.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        for spec in schema.get("x-secrets") or []:
            per_item = spec.get("per_item")
            props = schema["properties"][per_item]["items"]["properties"] if per_item else schema["properties"]
            if "tls_insecure_skip_verify" in props and "tls_fingerprint_sha256" not in props:
                found.add(path.parent.name)
    assert found == {ext_id for ext_id, *_ in SKIP_ONLY}


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext_id", "base", "label", "per_item"), SKIP_ONLY, ids=[e[0] for e in SKIP_ONLY])
async def test_an_unknown_pin_field_does_not_skip_the_password(
    client, db_session, test_settings, ext_id, base, label, per_item,
):
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    url = f"/api/v1/extensions/{ext_id}/settings"
    assert (await client.put(url, json={"values": base}, headers=headers)).status_code == 200
    put = await client.put(
        f"/api/v1/extensions/{ext_id}/secrets", json={"current_password": PASSWORD, "label": label, "value": "geheim"},
        headers=headers,
    )
    assert put.status_code == 204, put.text
    record = await db_session.get(ExtensionRecord, ext_id)
    await db_session.refresh(record)
    before = dict(record.settings)

    broken = {"tls_insecure_skip_verify": True, "tls_fingerprint_sha256": "kaputt"}
    values = {per_item: [{**base[per_item][0], **broken}]} if per_item else {**base, **broken}
    refused = await client.put(url, json={"values": values}, headers=headers)
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "confirm_required"
    await db_session.refresh(record)
    assert record.settings == before
    assert await _has_secret(db_session, label)


PIN_URL = "/api/v1/extensions/{ext}/settings/fingerprint"


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext_id", "prefix"), EXTS)
async def test_pinning_asks_once_and_touches_only_that_fingerprint(client, db_session, test_settings, ext_id, prefix):
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    await _with_token(client, headers, ext_id, tls_fingerprint_sha256=PIN_A)
    record = await db_session.get(ExtensionRecord, ext_id)
    await db_session.refresh(record)
    other = {"proxmox": {"storage_warn_percent": 80}, "backups": {"backup_max_age_days": 5}}[ext_id]
    record.settings = {
        **record.settings, **other,
        "connections": [{**record.settings["connections"][0], "tls_insecure_skip_verify": True}],
    }
    await db_session.commit()
    before = await _connections(db_session, ext_id)
    url = PIN_URL.format(ext=ext_id)
    body = {"connection": "pve1", "sha256": PIN_B.replace(":", "").lower()}

    refused = await client.put(url, json=body, headers=headers)
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "confirm_required"
    wrong = await client.put(url, json={**body, "current_password": "falsch-falsch"}, headers=headers)
    assert wrong.status_code == 400, wrong.text
    assert await _connections(db_session, ext_id) == before

    saved = await client.put(url, json={**body, "current_password": PASSWORD}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["secrets_cleared"] == []
    after = await _connections(db_session, ext_id)
    assert after == [{**before[0], "tls_fingerprint_sha256": PIN_B, "tls_insecure_skip_verify": False}]
    await db_session.refresh(record)
    assert {k: record.settings.get(k) for k in other} == other
    assert await _has_secret(db_session, f"{prefix}:pve1")
    audit = await client.get("/api/v1/audit?action=extension.settings", headers=headers)
    rows = audit.json()["items"] if isinstance(audit.json(), dict) else audit.json()
    assert any(r["detail"].get("tls_weakened") == [f"{prefix}:pve1"] for r in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext_id", "prefix"), EXTS)
async def test_pinning_keeps_a_connection_added_in_the_meantime(client, db_session, test_settings, ext_id, prefix):
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    await _with_token(client, headers, ext_id, tls_fingerprint_sha256=PIN_A)
    seen_by_first_tab = (await client.get(f"/api/v1/extensions/{ext_id}/settings", headers=headers)).json()
    assert [c["name"] for c in seen_by_first_tab["values"]["connections"]] == ["pve1"]
    second = {**CONN, "name": "pve2", "base_url": "https://192.168.2.11:8006"}
    assert (await client.post(f"/api/v1/ext/{ext_id}/connections", json=second, headers=headers)).status_code == 201
    stored = await client.post(f"/api/v1/ext/{ext_id}/connections/pve2/token", json={"value": "tok2"}, headers=headers)
    assert stored.status_code == 204, stored.text
    other = (await _connections(db_session, ext_id))[1]

    saved = await client.put(
        PIN_URL.format(ext=ext_id), json={"connection": "pve1", "sha256": PIN_B, "current_password": PASSWORD},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    after = await _connections(db_session, ext_id)
    assert [c["name"] for c in after] == ["pve1", "pve2"]
    assert after[0]["tls_fingerprint_sha256"] == PIN_B
    assert after[1] == other
    assert await _has_secret(db_session, f"{prefix}:pve2")
    assert await _has_secret(db_session, f"{prefix}:pve1")


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext_id", "prefix"), EXTS)
async def test_pinning_works_for_a_name_with_a_space(client, db_session, test_settings, ext_id, prefix):
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    created = await client.post(
        f"/api/v1/ext/{ext_id}/connections", json={**CONN, "name": "pve 1", "tls_fingerprint_sha256": PIN_A},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    stored = await client.post(f"/api/v1/ext/{ext_id}/connections/pve%201/token", json={"value": "tok"}, headers=headers)
    assert stored.status_code == 204, stored.text

    saved = await client.put(
        PIN_URL.format(ext=ext_id), json={"connection": "pve 1", "sha256": PIN_B, "current_password": PASSWORD},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert (await _connections(db_session, ext_id))[0]["tls_fingerprint_sha256"] == PIN_B
    assert await _has_secret(db_session, f"{prefix}:pve 1")


@pytest.mark.asyncio
async def test_pinning_needs_no_password_without_a_token_or_for_the_same_pin(client, db_session, test_settings):
    headers = await _owner_with(client, db_session, test_settings, "proxmox")
    created = await client.post(
        "/api/v1/ext/proxmox/connections", json={**CONN, "tls_fingerprint_sha256": PIN_A}, headers=headers,
    )
    assert created.status_code == 201, created.text
    url = PIN_URL.format(ext="proxmox")
    plain = await client.put(url, json={"connection": "pve1", "sha256": PIN_B}, headers=headers)
    assert plain.status_code == 200, plain.text
    assert (await _connections(db_session, "proxmox"))[0]["tls_fingerprint_sha256"] == PIN_B

    stored = await client.post("/api/v1/ext/proxmox/connections/pve1/token", json={"value": "tok"}, headers=headers)
    assert stored.status_code == 204, stored.text
    same = await client.put(url, json={"connection": "pve1", "sha256": PIN_B}, headers=headers)
    assert same.status_code == 200, same.text


@pytest.mark.asyncio
async def test_pinning_rejects_unknown_targets_and_bad_values(client, db_session, test_settings):
    headers = await _owner_with(client, db_session, test_settings, "proxmox", "uptime-kuma")
    await _with_token(client, headers, "proxmox", tls_fingerprint_sha256=PIN_A)
    before = await _connections(db_session, "proxmox")
    url = PIN_URL.format(ext="proxmox")
    cases = [
        ({"connection": "gibt-es-nicht", "sha256": PIN_B}, 404),
        ({"connection": "pve1", "sha256": "kaputt"}, 400),
        ({"connection": "pve1", "sha256": ""}, 400),
    ]
    for body, code in cases:
        r = await client.put(url, json={**body, "current_password": PASSWORD}, headers=headers)
        assert r.status_code == code, (body, r.text)
    assert await _connections(db_session, "proxmox") == before

    kuma = await client.put(
        PIN_URL.format(ext="uptime-kuma"), json={"connection": "kuma", "sha256": PIN_B, "current_password": PASSWORD},
        headers=headers,
    )
    assert kuma.status_code == 409, kuma.text


@pytest.mark.asyncio
async def test_pinning_waits_for_the_settings_lock(client, db_session, test_settings):
    import asyncio

    from nodvard_deck.services import extension_settings

    headers = await _owner_with(client, db_session, test_settings, "proxmox")
    created = await client.post("/api/v1/ext/proxmox/connections", json=CONN, headers=headers)
    assert created.status_code == 201, created.text
    lock = extension_settings.lock_for("proxmox")
    await lock.acquire()
    try:
        task = asyncio.ensure_future(
            client.put(PIN_URL.format(ext="proxmox"), json={"connection": "pve1", "sha256": PIN_B}, headers=headers)
        )
        await asyncio.sleep(0.2)
        assert not task.done()
        assert "tls_fingerprint_sha256" not in (await _connections(db_session, "proxmox"))[0]
        # Schreibt an der geladenen Zeile vorbei, wie ein zweiter Prozess: nur ein frisches Lesen unter der Sperre sieht es.
        record = await db_session.get(ExtensionRecord, "proxmox")
        changed = {**record.settings, "connections": [*record.settings["connections"], {"name": "pve2"}]}
        await db_session.execute(
            update(ExtensionRecord).where(ExtensionRecord.id == "proxmox").values(settings=changed)
            .execution_options(synchronize_session=False)
        )
        await db_session.commit()
    finally:
        lock.release()
    assert (await task).status_code == 200
    after = await _connections(db_session, "proxmox")
    assert after[0]["tls_fingerprint_sha256"] == PIN_B
    assert after[1] == {"name": "pve2"}


async def _seed_connection(db_session, ext_id: str, entry: dict) -> None:
    record = await db_session.get(ExtensionRecord, ext_id)
    await db_session.refresh(record)
    record.settings = {**(record.settings or {}), "connections": [entry]}
    await db_session.commit()


async def _with_token_named(client, db_session, headers, ext_id: str, name: str, **extra) -> None:
    # Namen mit Leerzeichen am Rand nimmt das Anlegen nicht mehr an; so eine Verbindung gibt es nur von früher.
    await _seed_connection(db_session, ext_id, {**CONN, "name": name, **extra})
    stored = await client.post(
        f"/api/v1/ext/{ext_id}/connections/{quote(name)}/token", json={"value": "tok"}, headers=headers,
    )
    assert stored.status_code == 204, stored.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext_id", "prefix"), EXTS)
@pytest.mark.parametrize("name", ["pve1 ", " pve1", "  "])
async def test_pinning_a_name_with_outer_spaces_still_asks(client, db_session, test_settings, ext_id, prefix, name):
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    await _with_token_named(client, db_session, headers, ext_id, name, tls_fingerprint_sha256=PIN_A)
    assert await _has_secret(db_session, f"{prefix}:{name}")
    before = await _connections(db_session, ext_id)

    refused = await client.put(
        PIN_URL.format(ext=ext_id), json={"connection": name, "sha256": PIN_B}, headers=headers,
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "confirm_required"
    assert await _connections(db_session, ext_id) == before


def test_secret_labels_cover_the_stored_and_the_trimmed_name():
    schema = {
        "properties": {"connections": {"type": "array", "items": {"properties": {
            "name": {}, "base_url": {}, "tls_insecure_skip_verify": {}, "tls_fingerprint_sha256": {},
        }}}},
        "x-secrets": [{"label": "kuma-key:{name}", "per_item": "connections", "x-secret-bound-to": ["base_url"]}],
    }
    old = {"connections": [{"name": " kuma ", "base_url": "https://a"}]}
    weaker = {"connections": [{"name": " kuma ", "base_url": "https://a", "tls_insecure_skip_verify": True}]}
    assert secrets_exposed_by_tls(schema, old, weaker) == ["kuma-key: kuma ", "kuma-key:kuma"]
    moved = {"connections": [{"name": " kuma ", "base_url": "https://b"}]}
    assert secrets_to_clear(schema, old, moved) == ["kuma-key: kuma ", "kuma-key:kuma"]
    trimmed = {"connections": [{"name": "kuma", "base_url": "https://a"}]}
    assert secrets_to_clear(schema, old, trimmed) == ["kuma-key:kuma", "kuma-key: kuma "]
    both = {"connections": [{"name": "kuma", "base_url": "https://a"}, {"name": "kuma ", "base_url": "https://b"}]}
    assert "kuma-key:kuma" in secrets_to_clear(schema, both, both)


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext_id", "prefix"), EXTS)
@pytest.mark.parametrize("name", ["pve1 ", " pve1", "  "])
async def test_new_connection_with_outer_spaces_is_refused(client, db_session, test_settings, ext_id, prefix, name):
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    refused = await client.post(f"/api/v1/ext/{ext_id}/connections", json={**CONN, "name": name}, headers=headers)
    assert refused.status_code == 422, refused.text
    assert "Leerzeichen" in refused.text
    assert await _connections(db_session, ext_id) == []
    created = await client.post(f"/api/v1/ext/{ext_id}/connections", json=CONN, headers=headers)
    assert created.status_code == 201, created.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext_id", "prefix"), EXTS)
@pytest.mark.parametrize("name", ["pve1 ", " pve1", "  "])
async def test_old_connection_with_outer_spaces_can_be_removed(client, db_session, test_settings, ext_id, prefix, name):
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    await _with_token_named(client, db_session, headers, ext_id, name)
    label = f"{prefix}:{name}"
    url = f"/api/v1/extensions/{ext_id}/secrets?label={quote(label)}"

    refused = await client.request("DELETE", url, json={}, headers=headers)
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "confirm_required"
    assert await _has_secret(db_session, label)

    dropped = await client.request("DELETE", url, json={"current_password": PASSWORD}, headers=headers)
    assert dropped.status_code == 204, dropped.text
    assert not await _has_secret(db_session, label)
    removed = await client.delete(f"/api/v1/ext/{ext_id}/connections/{quote(name)}", headers=headers)
    assert removed.status_code == 204, removed.text
    assert await _connections(db_session, ext_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext_id", "prefix"), EXTS)
async def test_old_connection_with_outer_spaces_takes_a_token(client, db_session, test_settings, ext_id, prefix):
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    await _seed_connection(db_session, ext_id, {**CONN, "name": " pve1"})
    label = f"{prefix}: pve1"

    stored = await client.put(
        f"/api/v1/extensions/{ext_id}/secrets", json={"label": label, "value": "tok"}, headers=headers,
    )
    assert stored.status_code == 204, stored.text
    assert await _has_secret(db_session, label)
    shown = (await client.get(f"/api/v1/extensions/{ext_id}/settings", headers=headers)).json()["secrets"]
    assert [(s["item"], s["is_set"]) for s in shown] == [("pve1", True)]


@pytest.mark.asyncio
async def test_secret_labels_of_other_entries_stay_refused(client, db_session, test_settings):
    headers = await _owner_with(client, db_session, test_settings, "proxmox")
    await _seed_connection(db_session, "proxmox", {**CONN, "name": " pve1"})
    for label in ("proxmox-token:pve2", "proxmox-token:  pve1", "backups-token: pve1", "proxmox-token:{name}"):
        put = await client.put(
            "/api/v1/extensions/proxmox/secrets", json={"label": label, "value": "tok"}, headers=headers,
        )
        assert put.status_code == 422, (label, put.text)
        gone = await client.request(
            "DELETE", f"/api/v1/extensions/proxmox/secrets?label={quote(label)}", json={}, headers=headers,
        )
        assert gone.status_code == 422, (label, gone.text)


def test_token_under_the_stored_name_counts_as_present():
    schema = {"x-secrets": [{"label": "t:{name}", "per_item": "connections"}]}
    values = {"connections": [{"name": " a"}, {"name": "b"}]}
    assert missing_secrets(schema, values, {"t: a", "t:b"}) == []
    assert missing_secrets(schema, values, {"t:b"}) == ["Zugangsdaten fehlen: t:a (a)."]


@pytest.mark.asyncio
async def test_token_under_the_stored_name_is_scrubbed_from_test_errors(client, db_session, test_settings):
    from nodvard_deck.api.v1.extension_settings import _known_secret_values

    headers = await _owner_with(client, db_session, test_settings, "proxmox")
    await _with_token_named(client, db_session, headers, "proxmox", " pve1")
    values = {"connections": await _connections(db_session, "proxmox")}
    assert await _known_secret_values(db_session, test_settings, "proxmox", values) == ["tok"]


def test_secret_slot_for_takes_only_labels_of_the_schema():
    schema = {"x-secrets": [{"label": "oben"}, {"label": "t:{name}", "per_item": "connections"}]}
    values = {"connections": [{"name": " a"}]}
    assert secret_slot_for(schema, values, "oben")["item"] is None
    assert secret_slot_for(schema, values, "t: a")["item"] == "a"
    assert secret_slot_for(schema, values, "t:a")["item"] == "a"
    for label in ("unten", "t:b", "t:  a", "t:{name}", ""):
        assert secret_slot_for(schema, values, label) is None, label


async def _proxmox_token_of(name: str) -> str | None:
    from nodvard_deck.ext.runtime import get_extension_runtime

    loaded = get_extension_runtime().loaded["proxmox"]
    config = sys.modules[f"{type(loaded.instance).__module__.split('.')[0]}.config"]
    connector = (await config.build_connectors(loaded.ctx)).get(name)
    return None if connector is None else connector._auth_header["Authorization"].rsplit("=", 1)[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["pve1 ", " pve1"])
@pytest.mark.parametrize("with_token", [True, False])
async def test_settings_name_the_label_the_module_reads(client, db_session, test_settings, name, with_token):
    headers = await _owner_with(client, db_session, test_settings, "proxmox")
    if with_token:
        await _with_token_named(client, db_session, headers, "proxmox", name)
    else:
        await _seed_connection(db_session, "proxmox", {**CONN, "name": name})
    [slot] = (await client.get("/api/v1/extensions/proxmox/settings", headers=headers)).json()["secrets"]
    assert (slot["label"], slot["is_set"]) == (f"proxmox-token:{name}", with_token)

    stored = await client.put(
        "/api/v1/extensions/proxmox/secrets",
        json={"label": slot["label"], "value": "neu", "current_password": PASSWORD}, headers=headers,
    )
    assert stored.status_code == 204, stored.text
    assert await _proxmox_token_of(name) == "neu"
    assert not await _has_secret(db_session, "proxmox-token:pve1")

    url = f"/api/v1/extensions/proxmox/secrets?label={quote(slot['label'])}"
    dropped = await client.request("DELETE", url, json={"current_password": PASSWORD}, headers=headers)
    assert dropped.status_code == 204, dropped.text
    assert await _proxmox_token_of(name) is None
    [after] = (await client.get("/api/v1/extensions/proxmox/settings", headers=headers)).json()["secrets"]
    assert after["is_set"] is False


@pytest.mark.asyncio
async def test_settings_name_the_label_that_holds_the_token(client, db_session, test_settings):
    headers = await _owner_with(client, db_session, test_settings, "proxmox")
    await _seed_connection(db_session, "proxmox", {**CONN, "name": " pve1"})
    created = await client.put(
        "/api/v1/extensions/proxmox/secrets", json={"label": "proxmox-token:pve1", "value": "alt"}, headers=headers,
    )
    assert created.status_code == 204, created.text
    [slot] = (await client.get("/api/v1/extensions/proxmox/settings", headers=headers)).json()["secrets"]
    assert (slot["label"], slot["is_set"]) == ("proxmox-token:pve1", True)
