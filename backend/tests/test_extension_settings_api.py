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


BOUND_SCHEMA = {
    "type": "object",
    "required": ["server_url"],
    "properties": {
        "server_url": {"type": "string", "default": "https://standard.example", "pattern": "^https?://\\S+$"},
        "topic": {"type": "string"},
        "pihole": {"type": "object", "properties": {"url": {"type": "string"}}},
        "connections": {
            "type": "array",
            "items": {"type": "object", "properties": {"name": {"type": "string"}, "base_url": {"type": "string"}}},
        },
        "servers": {"type": "object", "x-hidden": True},
    },
    "x-secrets": [
        {"label": "bound-token", "title": "Token", "x-secret-bound-to": ["server_url"]},
        {"label": "bound-pw", "title": "Passwort", "x-secret-bound-to": ["pihole.url"]},
        {"label": "bound-item:{name}", "title": "Pro Eintrag", "per_item": "connections", "x-secret-bound-to": ["base_url"]},
        {"label": "free-token", "title": "Ohne Bindung"},
    ],
}


@pytest.fixture
def bound_extension():
    runtime = get_extension_runtime()
    runtime.discovered["bound"] = SimpleNamespace(manifest=SimpleNamespace(settings_schema=BOUND_SCHEMA), ok=True)
    yield
    runtime.discovered.pop("bound", None)


async def _set_secrets(client, headers, *labels):
    for label in labels:
        r = await client.put("/api/v1/extensions/bound/secrets", json={"label": label, "value": "geheim-" + label}, headers=headers)
        assert r.status_code == 204, r.text


async def _secret_state(client, headers) -> dict[str, bool]:
    body = (await client.get("/api/v1/extensions/bound/settings", headers=headers)).json()
    return {s["label"]: s["is_set"] for s in body["secrets"]}


@pytest.mark.asyncio
async def test_put_keeps_hidden_and_unsent_values(client, db_session, bound_extension):
    """Eine alte Kopie aus der Oberfläche darf versteckte Felder (Profile der Gameserver) nicht
    überschreiben, und was nicht mitgeschickt wird, bleibt stehen."""
    db_session.add(ExtensionRecord(
        id="bound", version="1", api_version="0.1.0", state="disabled",
        settings={"server_url": "https://a.example", "topic": "t", "servers": {"h1": {"profile": "neu"}}},
    ))
    await db_session.flush()
    headers = await _owner(client)

    r = await client.put(
        "/api/v1/extensions/bound/settings",
        json={"values": {"topic": "neu", "servers": {}}},  # `servers` ist eine alte Kopie
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["values"] == {
        "server_url": "https://a.example", "topic": "neu", "servers": {"h1": {"profile": "neu"}},
    }


@pytest.mark.asyncio
async def test_put_null_removes_a_value_and_required_is_checked_after_merging(client, db_session, bound_extension):
    db_session.add(ExtensionRecord(
        id="bound", version="1", api_version="0.1.0", state="disabled", settings={"server_url": "https://a.example", "topic": "t"},
    ))
    await db_session.flush()
    headers = await _owner(client)

    r = await client.put("/api/v1/extensions/bound/settings", json={"values": {"topic": None}}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["values"] == {"server_url": "https://a.example"}

    # Pflichtfeld mit Standardwert: Entfernen ist erlaubt (der Standard gilt wieder).
    r = await client.put("/api/v1/extensions/bound/settings", json={"values": {"server_url": None}}, headers=headers)
    assert r.status_code == 200, r.text
    assert "server_url" not in r.json()["values"]


@pytest.mark.asyncio
async def test_pattern_is_still_checked_for_changed_values(client, db_session, bound_extension):
    db_session.add(ExtensionRecord(id="bound", version="1", api_version="0.1.0", state="disabled", settings={}))
    await db_session.flush()
    headers = await _owner(client)
    r = await client.put("/api/v1/extensions/bound/settings", json={"values": {"server_url": "ftp://x"}}, headers=headers)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_changing_a_bound_target_clears_its_secret_and_audits_it(client, db_session, bound_extension):
    db_session.add(ExtensionRecord(
        id="bound", version="1", api_version="0.1.0", state="disabled",
        settings={
            "server_url": "https://a.example", "pihole": {"url": "http://pi.hole"},
            "connections": [{"name": "pve1", "base_url": "https://pve1:8006"}, {"name": "pve2", "base_url": "https://pve2:8006"}],
        },
    ))
    await db_session.flush()
    headers = await _owner(client)
    await _set_secrets(client, headers, "bound-token", "bound-pw", "bound-item:pve1", "bound-item:pve2", "free-token")

    # Nur ein Schluss-Schrägstrich dazu: dasselbe Ziel, nichts wird gelöscht.
    r = await client.put(
        "/api/v1/extensions/bound/settings", json={"values": {"server_url": "https://a.example/", "topic": "x"}}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["secrets_cleared"] == []
    assert all((await _secret_state(client, headers)).values())

    r = await client.put(
        "/api/v1/extensions/bound/settings",
        json={"values": {
            "server_url": "https://angreifer.example",
            "connections": [{"name": "pve1", "base_url": "https://pve1:8006"}, {"name": "pve2", "base_url": "https://angreifer:8006"}],
        }},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert sorted(r.json()["secrets_cleared"]) == ["bound-item:pve2", "bound-token"]
    state = await _secret_state(client, headers)
    assert state == {
        "bound-token": False, "bound-pw": True, "bound-item:pve1": True, "bound-item:pve2": False, "free-token": True,
    }

    # Zweiter Pfad (`pihole.url`) und ein entfernter Eintrag.
    r = await client.put(
        "/api/v1/extensions/bound/settings",
        json={"values": {"pihole": {"url": "http://anderer"}, "connections": [{"name": "pve2", "base_url": "https://angreifer:8006"}]}},
        headers=headers,
    )
    assert sorted(r.json()["secrets_cleared"]) == ["bound-item:pve1", "bound-pw"]

    audit = await client.get("/api/v1/audit?action=extension.settings", headers=headers)
    rows = audit.json()["items"] if isinstance(audit.json(), dict) else audit.json()
    cleared = [x["detail"].get("secrets_cleared") for x in rows if x["detail"].get("secrets_cleared")]
    assert sorted(map(sorted, cleared)) == [["bound-item:pve1", "bound-pw"], ["bound-item:pve2", "bound-token"]]
    assert "geheim-" not in audit.text


@pytest.mark.asyncio
async def test_default_address_counts_as_the_bound_target(client, db_session, bound_extension):
    """Ohne eingetragene Adresse gilt der Standard aus dem Schema; ihn ausdrücklich einzutragen
    ändert das Ziel nicht, eine andere Adresse schon."""
    db_session.add(ExtensionRecord(id="bound", version="1", api_version="0.1.0", state="disabled", settings={}))
    await db_session.flush()
    headers = await _owner(client)
    await _set_secrets(client, headers, "bound-token")

    r = await client.put("/api/v1/extensions/bound/settings", json={"values": {"server_url": "https://standard.example"}}, headers=headers)
    assert r.json()["secrets_cleared"] == []
    r = await client.put("/api/v1/extensions/bound/settings", json={"values": {"server_url": "https://x.example"}}, headers=headers)
    assert r.json()["secrets_cleared"] == ["bound-token"]


@pytest.mark.asyncio
async def test_secret_needs_a_saved_target_first(client, db_session, bound_extension):
    """Ohne gespeichertes Ziel gehoert ein Geheimnis zu keinem Server: erst die Adresse speichern
    (409), dann das Geheimnis ablegen. Der Standardwert aus dem Schema zaehlt als Ziel."""
    db_session.add(ExtensionRecord(
        id="bound", version="1", api_version="0.1.0", state="disabled",
        settings={"connections": [{"name": "pve1"}, {"name": "pve2", "base_url": "https://pve2:8006"}]},
    ))
    await db_session.flush()
    headers = await _owner(client)

    async def put_secret(label: str):
        return await client.put("/api/v1/extensions/bound/secrets", json={"label": label, "value": "geheim"}, headers=headers)

    refused = await put_secret("bound-pw")
    assert refused.status_code == 409, refused.text
    assert "Adresse" in refused.json()["detail"]
    assert (await put_secret("bound-item:pve1")).status_code == 409  # Eintrag ohne Adresse
    assert (await put_secret("bound-item:pve2")).status_code == 204
    assert (await put_secret("bound-token")).status_code == 204  # Standardadresse aus dem Schema
    assert (await put_secret("free-token")).status_code == 204  # nicht gebunden
    assert (await _secret_state(client, headers))["bound-pw"] is False

    # Nach dem Speichern der Adresse geht es, ein Leerzeichen allein ist keine.
    r = await client.put("/api/v1/extensions/bound/settings", json={"values": {"pihole": {"url": "  "}}}, headers=headers)
    assert r.status_code == 200, r.text
    assert (await put_secret("bound-pw")).status_code == 409
    r = await client.put("/api/v1/extensions/bound/settings", json={"values": {"pihole": {"url": "http://pi.hole"}}}, headers=headers)
    assert r.json()["secrets_cleared"] == []
    assert (await put_secret("bound-pw")).status_code == 204
    assert (await _secret_state(client, headers))["bound-pw"] is True


@pytest.mark.asyncio
async def test_first_address_removes_a_secret_that_lies_in_the_vault_without_a_target(client, db_session, test_settings, bound_extension):
    """Ein Geheimnis ohne Ziel (aus einer Zeit vor dieser Regel) gehoert zu keinem Server und bleibt
    nicht an der ersten Adresse haengen, die jemand eintraegt."""
    from nodvard_deck.core import vault

    db_session.add(ExtensionRecord(id="bound", version="1", api_version="0.1.0", state="disabled", settings={}))
    await db_session.flush()
    await vault.create_secret(
        db_session, vault.load_keyring(test_settings), label="bound-pw", kind="generic", plaintext="alt", owner_ext_id="bound",
    )
    headers = await _owner(client)
    assert (await _secret_state(client, headers))["bound-pw"] is True

    r = await client.put("/api/v1/extensions/bound/settings", json={"values": {"pihole": {"url": "http://fremd"}}}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["secrets_cleared"] == ["bound-pw"]
    assert (await _secret_state(client, headers))["bound-pw"] is False


MULTI_SCHEMA = {
    "type": "object",
    "properties": {"url": {"type": "string"}, "failover_url": {"type": "string"}, "model": {"type": "string"}},
    "x-secrets": [{"label": "multi-key", "title": "Schlüssel", "optional": True, "x-secret-bound-to": ["url", "failover_url"]}],
}


@pytest.mark.asyncio
async def test_secret_bound_to_several_fields_is_removed_when_any_of_them_changes(client, db_session):
    """Ein Schluessel fuer zwei Server (Hauptserver und Ersatzserver): wird der zweite Server neu
    eingetragen, auch wenn dort vorher nichts stand, geht der Schluessel nicht mehr dorthin."""
    runtime = get_extension_runtime()
    runtime.discovered["multi"] = SimpleNamespace(manifest=SimpleNamespace(settings_schema=MULTI_SCHEMA), ok=True)
    try:
        db_session.add(ExtensionRecord(id="multi", version="1", api_version="0.1.0", state="disabled", settings={"url": "http://haupt:11434"}))
        await db_session.flush()
        headers = await _owner(client)
        base = "/api/v1/extensions/multi"
        assert (await client.put(f"{base}/secrets", json={"label": "multi-key", "value": "k"}, headers=headers)).status_code == 204

        # Ein Feld, das nichts mit dem Ziel zu tun hat: der Schluessel bleibt.
        r = await client.put(f"{base}/settings", json={"values": {"model": "llama"}}, headers=headers)
        assert r.json()["secrets_cleared"] == []

        # Der Ersatzserver wird neu eingetragen (leer -> Wert): der Schluessel ist weg.
        r = await client.put(f"{base}/settings", json={"values": {"failover_url": "http://eigener-server:11434"}}, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["secrets_cleared"] == ["multi-key"]
        assert [s["is_set"] for s in r.json()["secrets"]] == [False]

        # Dasselbe, wenn nur der Hauptserver wechselt, und beim Leeren eines Felds.
        assert (await client.put(f"{base}/secrets", json={"label": "multi-key", "value": "k"}, headers=headers)).status_code == 204
        r = await client.put(f"{base}/settings", json={"values": {"failover_url": None}}, headers=headers)
        assert r.json()["secrets_cleared"] == ["multi-key"]
        assert (await client.put(f"{base}/secrets", json={"label": "multi-key", "value": "k"}, headers=headers)).status_code == 204
        r = await client.put(f"{base}/settings", json={"values": {"url": "http://anderer:11434"}}, headers=headers)
        assert r.json()["secrets_cleared"] == ["multi-key"]
    finally:
        runtime.discovered.pop("multi", None)


@pytest.mark.asyncio
async def test_other_spellings_of_the_same_address_keep_the_secret(client, db_session, bound_extension):
    """Gross-/Kleinschreibung, Standardport, fehlendes Schema und Schlussstriche aendern das Ziel nicht;
    ein anderer Pfad, Port oder Rechner schon (im Zweifel gilt eine Adresse als neu)."""
    db_session.add(ExtensionRecord(
        id="bound", version="1", api_version="0.1.0", state="disabled", settings={"pihole": {"url": "http://pi.hole"}},
    ))
    await db_session.flush()
    headers = await _owner(client)

    async def put_url(url: str):
        r = await client.put("/api/v1/extensions/bound/settings", json={"values": {"pihole": {"url": url}}}, headers=headers)
        assert r.status_code == 200, r.text
        return r.json()["secrets_cleared"]

    async def fresh_secret():
        await _set_secrets(client, headers, "bound-pw")

    await fresh_secret()
    for same in ("pi.hole", "http://Pi.Hole", "HTTP://PI.HOLE/", "http://pi.hole:80/", " http://pi.hole// "):
        assert await put_url(same) == [], same
    assert (await _secret_state(client, headers))["bound-pw"] is True

    assert await put_url("https://pi.hole") == ["bound-pw"]  # anderes Schema
    for different, previous in (("https://pi.hole:8443", "https://pi.hole"), ("https://pi.hole:8443/admin", "https://pi.hole:8443"),
                                ("https://anderer.hole:8443/admin", "https://pi.hole:8443/admin")):
        await fresh_secret()
        assert await put_url(different) == ["bound-pw"], (previous, different)
    await fresh_secret()
    assert await put_url("https://anderer.hole:8443/admin/") == []  # nur ein Schlussstrich
    assert (await _secret_state(client, headers))["bound-pw"] is True
