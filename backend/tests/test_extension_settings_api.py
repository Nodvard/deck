"""Einstellungen einer Extension ueber die Oberflaeche: Schema, Werte, Geheimnisse."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import ExtensionRecord

SCHEMA = {
    "type": "object",
    "properties": {
        "connections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "base_url": {"type": "string"}, "verify": {"type": "boolean"}},
                "required": ["name", "base_url"],
            },
        },
        "interval_s": {"type": "integer"},
        "mode": {"type": "string", "enum": ["a", "b"]},
    },
    "x-secrets": [{"label": "demo-token:{name}", "title": "API-Token", "per_item": "connections"}],
}


@pytest.fixture
def demo_extension():
    runtime = get_extension_runtime()
    runtime.discovered["demo"] = SimpleNamespace(manifest=SimpleNamespace(settings_schema=SCHEMA), ok=True)
    yield
    runtime.discovered.pop("demo", None)


async def _owner(client):
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    r = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_settings_roundtrip_keeps_internal_keys(client, db_session, demo_extension):
    db_session.add(ExtensionRecord(id="demo", version="1", api_version="0.1.0", state="disabled", settings={"internal": [1]}))
    await db_session.flush()
    headers = await _owner(client)

    r = await client.get("/api/v1/extensions/demo/settings", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["schema"]["properties"]["mode"]["enum"] == ["a", "b"]
    assert r.json()["secrets"] == []

    values = {"connections": [{"name": "pve2", "base_url": "https://pve:8006", "verify": False}], "interval_s": 30, "unknown": 1}
    r = await client.put("/api/v1/extensions/demo/settings", json={"values": values}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["values"] == {"internal": [1], "connections": values["connections"], "interval_s": 30}
    assert body["secrets"] == [{"label": "demo-token:pve2", "title": "API-Token", "description": None, "item": "pve2", "is_set": False, "optional": False}]


@pytest.mark.asyncio
async def test_settings_are_type_checked(client, db_session, demo_extension):
    db_session.add(ExtensionRecord(id="demo", version="1", api_version="0.1.0", state="disabled"))
    await db_session.flush()
    headers = await _owner(client)
    bad = [
        {"interval_s": "30"},
        {"mode": "c"},
        {"connections": [{"name": "x"}]},
        {"interval_s": True},
    ]
    for values in bad:
        r = await client.put("/api/v1/extensions/demo/settings", json={"values": values}, headers=headers)
        assert r.status_code == 422, (values, r.text)


@pytest.mark.asyncio
async def test_secret_set_and_replace_only_for_known_labels(client, db_session, demo_extension):
    db_session.add(ExtensionRecord(
        id="demo", version="1", api_version="0.1.0", state="disabled",
        settings={"connections": [{"name": "pve2", "base_url": "https://pve:8006"}]},
    ))
    await db_session.flush()
    headers = await _owner(client)

    foreign = await client.put("/api/v1/extensions/demo/secrets", json={"label": "proxmox-token:pve2", "value": "x"}, headers=headers)
    assert foreign.status_code == 422
    for value in ("geheim-1", "geheim-2"):
        r = await client.put("/api/v1/extensions/demo/secrets", json={"label": "demo-token:pve2", "value": value}, headers=headers)
        assert r.status_code == 204, r.text
    r = await client.get("/api/v1/extensions/demo/settings", headers=headers)
    assert r.json()["secrets"][0]["is_set"] is True
    assert "geheim" not in r.text


@pytest.mark.asyncio
async def test_settings_need_extensions_manage(client, db_session, demo_extension):
    db_session.add(ExtensionRecord(id="demo", version="1", api_version="0.1.0", state="disabled"))
    await db_session.flush()
    assert (await client.get("/api/v1/extensions/demo/settings")).status_code == 401


@pytest.mark.asyncio
async def test_pattern_is_checked_with_german_message(client, db_session):
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "title": "Adresse", "pattern": "^https?://\\S+$", "x-pattern-message": "Die Adresse muss mit http:// oder https:// beginnen."},
            "code": {"type": "string", "pattern": "^[a-z]+$"},
            "broken": {"type": "string", "pattern": "([unclosed"},
            "words": {"type": "array", "items": {"type": "string", "pattern": "^[a-z]+$"}},
        },
    }
    runtime = get_extension_runtime()
    runtime.discovered["pat"] = SimpleNamespace(manifest=SimpleNamespace(settings_schema=schema), ok=True)
    try:
        db_session.add(ExtensionRecord(id="pat", version="1", api_version="0.1.0", state="disabled"))
        await db_session.flush()
        headers = await _owner(client)
        bad = await client.put("/api/v1/extensions/pat/settings", json={"values": {"url": "ftp://x"}}, headers=headers)
        assert bad.status_code == 422
        assert bad.json()["detail"] == "„Einstellungen.url“: Die Adresse muss mit http:// oder https:// beginnen."
        generic = await client.put("/api/v1/extensions/pat/settings", json={"values": {"code": "ABC"}}, headers=headers)
        assert generic.status_code == 422 and "Das Format stimmt nicht." in generic.json()["detail"]
        item = await client.put("/api/v1/extensions/pat/settings", json={"values": {"words": ["ok", "NO"]}}, headers=headers)
        assert item.status_code == 422 and "[2]" in item.json()["detail"]
        good = await client.put(
            "/api/v1/extensions/pat/settings",
            json={"values": {"url": "https://nas:8006", "code": "abc", "broken": "egal", "words": ["ok"]}}, headers=headers,
        )
        assert good.status_code == 200, good.text
        empty = await client.put("/api/v1/extensions/pat/settings", json={"values": {"url": ""}}, headers=headers)
        assert empty.status_code == 200  # leer = nicht gesetzt, kein Musterfehler
    finally:
        runtime.discovered.pop("pat", None)


@pytest.mark.asyncio
async def test_pattern_behaves_like_javascript_for_trailing_newline_and_length(client, db_session):
    """Sicherheits-Review: Python-`$` laesst ein `\\n` am Ende durch (JavaScript nicht); und
    sehr lange Werte werden vor der (synchronen) Musterpruefung abgelehnt."""
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "pattern": "^https?://\\S+$"},
            "either": {"type": "string", "pattern": "^a$|^b$"},
            "cls": {"type": "string", "pattern": "^[a-z$]+$"},
            "long": {"type": "string", "pattern": "^[a-z]+$", "maxLength": 5000},
        },
    }
    runtime = get_extension_runtime()
    runtime.discovered["nl"] = SimpleNamespace(manifest=SimpleNamespace(settings_schema=schema), ok=True)
    try:
        db_session.add(ExtensionRecord(id="nl", version="1", api_version="0.1.0", state="disabled"))
        await db_session.flush()
        headers = await _owner(client)

        async def put(values):
            return await client.put("/api/v1/extensions/nl/settings", json={"values": values}, headers=headers)

        assert (await put({"url": "https://x\n"})).status_code == 422
        assert (await put({"either": "a\n"})).status_code == 422
        assert (await put({"either": "b"})).status_code == 200
        assert (await put({"cls": "a$"})).status_code == 200  # `$` in einer Zeichenklasse bleibt ein Zeichen
        assert (await put({"url": "https://x"})).status_code == 200

        too_long = await put({"url": "https://" + "a" * 2000})
        assert too_long.status_code == 422 and "zu lang" in too_long.json()["detail"]
        assert (await put({"long": "a" * 4000})).status_code == 200  # das Schema erlaubt mehr
        assert (await put({"long": "a" * 5001})).status_code == 422
    finally:
        runtime.discovered.pop("nl", None)
