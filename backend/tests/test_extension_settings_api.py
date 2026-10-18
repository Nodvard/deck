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
    r = await client.put("/api/v1/extensions/demo/settings", json={"current_password": "correct-horse-battery", "values": values}, headers=headers)
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
        r = await client.put("/api/v1/extensions/demo/settings", json={"current_password": "correct-horse-battery", "values": values}, headers=headers)
        assert r.status_code == 422, (values, r.text)


@pytest.mark.asyncio
async def test_secret_set_and_replace_only_for_known_labels(client, db_session, demo_extension):
    db_session.add(ExtensionRecord(
        id="demo", version="1", api_version="0.1.0", state="disabled",
        settings={"connections": [{"name": "pve2", "base_url": "https://pve:8006"}]},
    ))
    await db_session.flush()
    headers = await _owner(client)

    foreign = await client.put("/api/v1/extensions/demo/secrets", json={"current_password": "correct-horse-battery", "label": "proxmox-token:pve2", "value": "x"}, headers=headers)
    assert foreign.status_code == 422
    for value in ("geheim-1", "geheim-2"):
        r = await client.put("/api/v1/extensions/demo/secrets", json={"current_password": "correct-horse-battery", "label": "demo-token:pve2", "value": value}, headers=headers)
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
        bad = await client.put("/api/v1/extensions/pat/settings", json={"current_password": "correct-horse-battery", "values": {"url": "ftp://x"}}, headers=headers)
        assert bad.status_code == 422
        assert bad.json()["detail"] == "„Einstellungen.url“: Die Adresse muss mit http:// oder https:// beginnen."
        generic = await client.put("/api/v1/extensions/pat/settings", json={"current_password": "correct-horse-battery", "values": {"code": "ABC"}}, headers=headers)
        assert generic.status_code == 422 and "Das Format stimmt nicht." in generic.json()["detail"]
        item = await client.put("/api/v1/extensions/pat/settings", json={"current_password": "correct-horse-battery", "values": {"words": ["ok", "NO"]}}, headers=headers)
        assert item.status_code == 422 and "[2]" in item.json()["detail"]
        good = await client.put(
            "/api/v1/extensions/pat/settings",
            json={"current_password": "correct-horse-battery", "values": {"url": "https://nas:8006", "code": "abc", "broken": "egal", "words": ["ok"]}}, headers=headers,
        )
        assert good.status_code == 200, good.text
        empty = await client.put("/api/v1/extensions/pat/settings", json={"current_password": "correct-horse-battery", "values": {"url": ""}}, headers=headers)
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
            return await client.put("/api/v1/extensions/nl/settings", json={"current_password": "correct-horse-battery", "values": values}, headers=headers)

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
        r = await client.put("/api/v1/extensions/bound/secrets", json={"current_password": "correct-horse-battery", "label": label, "value": "geheim-" + label}, headers=headers)
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
        json={"current_password": "correct-horse-battery", "values": {"topic": "neu", "servers": {}}},  # `servers` ist eine alte Kopie
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

    r = await client.put("/api/v1/extensions/bound/settings", json={"current_password": "correct-horse-battery", "values": {"topic": None}}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["values"] == {"server_url": "https://a.example"}

    # Pflichtfeld mit Standardwert: Entfernen ist erlaubt (der Standard gilt wieder).
    r = await client.put("/api/v1/extensions/bound/settings", json={"current_password": "correct-horse-battery", "values": {"server_url": None}}, headers=headers)
    assert r.status_code == 200, r.text
    assert "server_url" not in r.json()["values"]


@pytest.mark.asyncio
async def test_pattern_is_still_checked_for_changed_values(client, db_session, bound_extension):
    db_session.add(ExtensionRecord(id="bound", version="1", api_version="0.1.0", state="disabled", settings={}))
    await db_session.flush()
    headers = await _owner(client)
    r = await client.put("/api/v1/extensions/bound/settings", json={"current_password": "correct-horse-battery", "values": {"server_url": "ftp://x"}}, headers=headers)
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
        "/api/v1/extensions/bound/settings", json={"current_password": "correct-horse-battery", "values": {"server_url": "https://a.example/", "topic": "x"}}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["secrets_cleared"] == []
    assert all((await _secret_state(client, headers)).values())

    r = await client.put(
        "/api/v1/extensions/bound/settings",
        json={"current_password": "correct-horse-battery", "values": {
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
        json={"current_password": "correct-horse-battery", "values": {"pihole": {"url": "http://anderer"}, "connections": [{"name": "pve2", "base_url": "https://angreifer:8006"}]}},
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

    r = await client.put("/api/v1/extensions/bound/settings", json={"current_password": "correct-horse-battery", "values": {"server_url": "https://standard.example"}}, headers=headers)
    assert r.json()["secrets_cleared"] == []
    r = await client.put("/api/v1/extensions/bound/settings", json={"current_password": "correct-horse-battery", "values": {"server_url": "https://x.example"}}, headers=headers)
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
        return await client.put("/api/v1/extensions/bound/secrets", json={"current_password": "correct-horse-battery", "label": label, "value": "geheim"}, headers=headers)

    refused = await put_secret("bound-pw")
    assert refused.status_code == 409, refused.text
    assert "Adresse" in refused.json()["detail"]
    assert (await put_secret("bound-item:pve1")).status_code == 409  # Eintrag ohne Adresse
    assert (await put_secret("bound-item:pve2")).status_code == 204
    assert (await put_secret("bound-token")).status_code == 204  # Standardadresse aus dem Schema
    assert (await put_secret("free-token")).status_code == 204  # nicht gebunden
    assert (await _secret_state(client, headers))["bound-pw"] is False

    # Nach dem Speichern der Adresse geht es, ein Leerzeichen allein ist keine.
    r = await client.put("/api/v1/extensions/bound/settings", json={"current_password": "correct-horse-battery", "values": {"pihole": {"url": "  "}}}, headers=headers)
    assert r.status_code == 200, r.text
    assert (await put_secret("bound-pw")).status_code == 409
    r = await client.put("/api/v1/extensions/bound/settings", json={"current_password": "correct-horse-battery", "values": {"pihole": {"url": "http://pi.hole"}}}, headers=headers)
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

    r = await client.put("/api/v1/extensions/bound/settings", json={"current_password": "correct-horse-battery", "values": {"pihole": {"url": "http://fremd"}}}, headers=headers)
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
        assert (await client.put(f"{base}/secrets", json={"current_password": "correct-horse-battery", "label": "multi-key", "value": "k"}, headers=headers)).status_code == 204

        # Ein Feld, das nichts mit dem Ziel zu tun hat: der Schluessel bleibt.
        r = await client.put(f"{base}/settings", json={"current_password": "correct-horse-battery", "values": {"model": "llama"}}, headers=headers)
        assert r.json()["secrets_cleared"] == []

        # Der Ersatzserver wird neu eingetragen (leer -> Wert): der Schluessel ist weg.
        r = await client.put(f"{base}/settings", json={"current_password": "correct-horse-battery", "values": {"failover_url": "http://eigener-server:11434"}}, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["secrets_cleared"] == ["multi-key"]
        assert [s["is_set"] for s in r.json()["secrets"]] == [False]

        # Dasselbe, wenn nur der Hauptserver wechselt, und beim Leeren eines Felds.
        assert (await client.put(f"{base}/secrets", json={"current_password": "correct-horse-battery", "label": "multi-key", "value": "k"}, headers=headers)).status_code == 204
        r = await client.put(f"{base}/settings", json={"current_password": "correct-horse-battery", "values": {"failover_url": None}}, headers=headers)
        assert r.json()["secrets_cleared"] == ["multi-key"]
        assert (await client.put(f"{base}/secrets", json={"current_password": "correct-horse-battery", "label": "multi-key", "value": "k"}, headers=headers)).status_code == 204
        r = await client.put(f"{base}/settings", json={"current_password": "correct-horse-battery", "values": {"url": "http://anderer:11434"}}, headers=headers)
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
        r = await client.put("/api/v1/extensions/bound/settings", json={"current_password": "correct-horse-battery", "values": {"pihole": {"url": url}}}, headers=headers)
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


SCHEDULE_SCHEMA = {
    "type": "object",
    "properties": {
        "scan_cron": {"type": "string", "title": "Scan-Zeitplan", "default": "0 2 * * *", "x-widget": "schedule"},
        "other_cron": {"type": "string", "x-widget": "schedule"},
        "note": {"type": "string"},
    },
}


@pytest.fixture
def schedule_extension():
    runtime = get_extension_runtime()
    runtime.discovered["sched"] = SimpleNamespace(manifest=SimpleNamespace(settings_schema=SCHEDULE_SCHEMA), ok=True)
    yield
    runtime.discovered.pop("sched", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["@daily", "0 24 * * *", "60 * * * *", "0 3 * * 8", "* * * *", "abc * * * *", "   ", "*/0 * * * *"])
async def test_an_invalid_schedule_is_refused_and_nothing_is_stored(client, db_session, schedule_extension, bad):
    """Ein Zeitplan, den der Planer nicht annimmt, legte die Erweiterung beim naechsten Start lahm
    und wurde trotzdem als gespeichert gemeldet. Jetzt: 422 mit deutschem Text, nichts gespeichert."""
    db_session.add(ExtensionRecord(
        id="sched", version="1", api_version="0.1.0", state="disabled", settings={"scan_cron": "0 3 * * *", "note": "alt"},
    ))
    await db_session.flush()
    headers = await _owner(client)

    r = await client.put("/api/v1/extensions/sched/settings", json={"current_password": "correct-horse-battery", "values": {"scan_cron": bad, "note": "neu"}}, headers=headers)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail.startswith("„Scan-Zeitplan“: Der Zeitplan „") and "ist ungültig" in detail
    assert "Minute" in detail  # sagt, wie es richtig aussieht

    stored = (await client.get("/api/v1/extensions/sched/settings", headers=headers)).json()["values"]
    assert stored == {"scan_cron": "0 3 * * *", "note": "alt"}  # auch das gleichzeitig geschickte Feld bleibt


@pytest.mark.asyncio
async def test_a_schedule_field_without_title_names_the_field_in_the_message(client, db_session, schedule_extension):
    db_session.add(ExtensionRecord(id="sched", version="1", api_version="0.1.0", state="disabled", settings={}))
    await db_session.flush()
    headers = await _owner(client)
    r = await client.put("/api/v1/extensions/sched/settings", json={"current_password": "correct-horse-battery", "values": {"other_cron": "@weekly"}}, headers=headers)
    assert r.status_code == 422
    assert "other_cron" in r.json()["detail"]


@pytest.mark.asyncio
async def test_valid_schedules_empty_and_null_still_save(client, db_session, schedule_extension):
    db_session.add(ExtensionRecord(id="sched", version="1", api_version="0.1.0", state="disabled", settings={"scan_cron": "0 3 * * *"}))
    await db_session.flush()
    headers = await _owner(client)

    for good in ("30 4 * * *", "0 3 * * 0", "0 3 * * 7", "0 3 * * mon-fri", "*/15 * * * *", "0 6 1 * *"):
        r = await client.put("/api/v1/extensions/sched/settings", json={"current_password": "correct-horse-battery", "values": {"scan_cron": good}}, headers=headers)
        assert r.status_code == 200, (good, r.text)
        assert r.json()["values"]["scan_cron"] == good
    # leer = nicht gesetzt (die Erweiterung nimmt ihren Standard), null entfernt den Wert
    r = await client.put("/api/v1/extensions/sched/settings", json={"current_password": "correct-horse-battery", "values": {"scan_cron": ""}}, headers=headers)
    assert r.status_code == 200, r.text
    r = await client.put("/api/v1/extensions/sched/settings", json={"current_password": "correct-horse-battery", "values": {"scan_cron": None}}, headers=headers)
    assert r.status_code == 200, r.text
    assert "scan_cron" not in r.json()["values"]


@pytest.mark.asyncio
async def test_every_schedule_field_of_the_shipped_extensions_is_checked(client, db_session):
    """Gegen die echten Schemas: jede der sechs Shield-Einstellungen mit Zeitplan und die der
    Docker-Modul lehnt einen ungueltigen Wert ab."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "extensions"
    runtime = get_extension_runtime()
    headers = await _owner(client)
    found = 0
    try:
        for ext_id in ("shield", "docker"):
            schema = json.loads((root / ext_id / "settings.schema.json").read_text(encoding="utf-8"))
            runtime.discovered[ext_id] = SimpleNamespace(manifest=SimpleNamespace(settings_schema=schema), ok=True)
            db_session.add(ExtensionRecord(id=ext_id, version="1", api_version="0.1.0", state="disabled", settings={}))
            await db_session.flush()
            for key, spec in schema["properties"].items():
                if spec.get("x-widget") != "schedule":
                    continue
                found += 1
                bad = await client.put(f"/api/v1/extensions/{ext_id}/settings", json={"current_password": "correct-horse-battery", "values": {key: "@daily"}}, headers=headers)
                assert bad.status_code == 422, (ext_id, key, bad.text)
                good = await client.put(f"/api/v1/extensions/{ext_id}/settings", json={"current_password": "correct-horse-battery", "values": {key: "0 4 * * *"}}, headers=headers)
                assert good.status_code == 200, (ext_id, key, good.text)
    finally:
        runtime.discovered.pop("shield", None)
        runtime.discovered.pop("docker", None)
    assert found == 7  # sechs bei Shield, eine beim Docker-Modul


NUMBER_SCHEMA = {
    "type": "object",
    "properties": {
        "interval": {"type": "integer", "title": "Prüfabstand", "default": 10, "minimum": 2, "maximum": 59},
        "ratio": {"type": "number", "minimum": 0.5, "maximum": 10},
        "threshold": {"type": "integer", "title": "Schwelle", "minimum": 3},
        "free": {"type": "number"},
        "note": {"type": "string"},
        "limits": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 5}},
    },
}


@pytest.fixture
def number_extension():
    runtime = get_extension_runtime()
    runtime.discovered["zahl"] = SimpleNamespace(manifest=SimpleNamespace(settings_schema=NUMBER_SCHEMA), ok=True)
    yield
    runtime.discovered.pop("zahl", None)


async def _put_raw(client, headers, body: str):
    return await client.put(
        "/api/v1/extensions/zahl/settings", content=body, headers={**headers, "Content-Type": "application/json"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [-5, 0, 1, 60, 1000])
async def test_a_number_outside_its_bounds_is_refused_and_nothing_is_stored(client, db_session, number_extension, bad):
    """Bisher liess sich 0 oder -5 speichern; die Erweiterung rechnete dann damit (bis in Befehle auf dem Server)."""
    db_session.add(ExtensionRecord(
        id="zahl", version="1", api_version="0.1.0", state="disabled", settings={"interval": 10, "note": "alt"},
    ))
    await db_session.flush()
    headers = await _owner(client)

    r = await client.put("/api/v1/extensions/zahl/settings", json={"current_password": "correct-horse-battery", "values": {"interval": bad, "note": "neu"}}, headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == f"„Prüfabstand“: Erlaubt sind Werte von 2 bis 59. Eingetragen ist {bad}."
    stored = (await client.get("/api/v1/extensions/zahl/settings", headers=headers)).json()["values"]
    assert stored == {"interval": 10, "note": "alt"}


@pytest.mark.asyncio
async def test_number_bounds_are_inclusive_and_empty_still_means_default(client, db_session, number_extension):
    db_session.add(ExtensionRecord(id="zahl", version="1", api_version="0.1.0", state="disabled", settings={}))
    await db_session.flush()
    headers = await _owner(client)
    for values in ({"interval": 2}, {"interval": 59}, {"ratio": 0.5}, {"ratio": 10}, {"threshold": 3},
                   {"threshold": 10**6}, {"free": -12.5}, {"limits": [1, 5]}):
        r = await client.put("/api/v1/extensions/zahl/settings", json={"current_password": "correct-horse-battery", "values": values}, headers=headers)
        assert r.status_code == 200, (values, r.text)
    r = await client.put("/api/v1/extensions/zahl/settings", json={"current_password": "correct-horse-battery", "values": {"interval": None}}, headers=headers)
    assert r.status_code == 200, r.text
    assert "interval" not in r.json()["values"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("values", "detail"), [
    ({"ratio": 0.25}, "„Einstellungen.ratio“: Erlaubt sind Werte von 0,5 bis 10. Eingetragen ist 0,25."),
    ({"threshold": 2}, "„Schwelle“: Erlaubt sind Werte ab 3. Eingetragen ist 2."),
    ({"limits": [1, 6]}, "„Einstellungen.limits[2]“: Erlaubt sind Werte von 1 bis 5. Eingetragen ist 6."),
])
async def test_the_message_names_the_field_and_the_allowed_range(client, db_session, number_extension, values, detail):
    db_session.add(ExtensionRecord(id="zahl", version="1", api_version="0.1.0", state="disabled", settings={}))
    await db_session.flush()
    headers = await _owner(client)
    r = await client.put("/api/v1/extensions/zahl/settings", json={"current_password": "correct-horse-battery", "values": values}, headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == detail


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
async def test_nan_and_infinity_are_never_stored(client, db_session, number_extension, raw):
    """Pythons JSON-Leser nimmt `NaN` an. Gespeichert waere das kein gueltiges JSON mehr: Die Einstellungsseite der
    Erweiterung liesse sich danach nicht mehr laden. Gilt auch fuer Zahlenfelder ohne Grenzen."""
    db_session.add(ExtensionRecord(id="zahl", version="1", api_version="0.1.0", state="disabled", settings={"free": 1}))
    await db_session.flush()
    headers = await _owner(client)
    r = await _put_raw(client, headers, '{"values": {"free": ' + raw + '}}')
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "„Einstellungen.free“: Bitte eine Zahl eintragen."
    assert (await client.get("/api/v1/extensions/zahl/settings", headers=headers)).json()["values"] == {"free": 1}


@pytest.mark.asyncio
async def test_a_huge_integer_is_a_422_not_a_500(client, db_session, number_extension):
    """Der JSON-Leser macht aus `1000…0` (400 Stellen) eine Python-`int`; `math.isfinite` darauf warf `OverflowError`."""
    db_session.add(ExtensionRecord(id="zahl", version="1", api_version="0.1.0", state="disabled", settings={"interval": 10}))
    await db_session.flush()
    headers = await _owner(client)
    r = await _put_raw(client, headers, '{"values": {"interval": 1' + "0" * 400 + "}}")
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "„Prüfabstand“: Erlaubt sind Werte von 2 bis 59. Eingetragen ist 1,000 × 10^400."
    assert (await client.get("/api/v1/extensions/zahl/settings", headers=headers)).json()["values"] == {"interval": 10}


def test_every_number_field_of_the_shipped_extensions_has_bounds_around_its_default():
    """Jedes Zahlenfeld einer mitgelieferten Erweiterung hat `minimum` und `maximum`, und der Standard liegt dazwischen.
    Ein neues Feld ohne Grenzen faellt hier auf."""
    import json
    from pathlib import Path

    def numbers(schema, path):
        if schema.get("type") in ("integer", "number"):
            yield path, schema
        for key, sub in (schema.get("properties") or {}).items():
            yield from numbers(sub, f"{path}.{key}")
        if isinstance(schema.get("items"), dict):
            yield from numbers(schema["items"], f"{path}[]")

    root = Path(__file__).resolve().parents[2] / "extensions"
    found = []
    for path in sorted(root.glob("*/settings.schema.json")):
        for where, spec in numbers(json.loads(path.read_text(encoding="utf-8")), path.parent.name):
            found.append(where)
            assert isinstance(spec.get("minimum"), (int, float)) and isinstance(spec.get("maximum"), (int, float)), where
            assert spec["minimum"] <= spec.get("default", spec["minimum"]) <= spec["maximum"], where
    assert len(found) >= 9, found  # Shield und System


def test_every_minute_interval_of_the_shipped_extensions_offers_only_divisors_of_60():
    """Abstaende laufen als `*/N`: nur Teiler von 60 laufen gleichmaessig. Jede Auswahl hat lesbare Texte."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "extensions"
    found = []
    for path in sorted(root.glob("*/settings.schema.json")):
        for key, spec in json.loads(path.read_text(encoding="utf-8"))["properties"].items():
            if not key.endswith("_interval_min"):
                continue
            found.append(f"{path.parent.name}.{key}")
            assert spec["enum"] and all(60 % v == 0 for v in spec["enum"]), key
            assert spec["default"] in spec["enum"], key
            assert (spec["minimum"], spec["maximum"]) == (min(spec["enum"]), max(spec["enum"])), key
            assert set(spec["x-enum-labels"]) == {str(v) for v in spec["enum"]}, key
    assert sorted(found) == ["shield.guard_interval_min", "shield.watch_interval_min", "system.warn_interval_min"]


@pytest.mark.asyncio
async def test_an_enum_message_names_the_field_by_its_title(client, db_session):
    runtime = get_extension_runtime()
    schema = {"type": "object", "properties": {"step": {"type": "integer", "title": "Abstand", "enum": [5, 10]}}}
    runtime.discovered["wahl"] = SimpleNamespace(manifest=SimpleNamespace(settings_schema=schema), ok=True)
    try:
        db_session.add(ExtensionRecord(id="wahl", version="1", api_version="0.1.0", state="disabled", settings={}))
        await db_session.flush()
        headers = await _owner(client)
        bad = await client.put("/api/v1/extensions/wahl/settings", json={"current_password": "correct-horse-battery", "values": {"step": 7}}, headers=headers)
        assert bad.status_code == 422
        assert bad.json()["detail"] == "„Abstand“ muss einer dieser Werte sein: 5, 10."
        good = await client.put("/api/v1/extensions/wahl/settings", json={"current_password": "correct-horse-battery", "values": {"step": 10}}, headers=headers)
        assert good.status_code == 200, good.text
    finally:
        runtime.discovered.pop("wahl", None)


@pytest.mark.asyncio
async def test_the_additions_x_generate_and_x_qr_url_do_not_get_in_the_way_of_saving(client, db_session):
    """Reine Oberflaechen-Zusaetze wie `x-hidden`: das Backend reicht sie im Schema durch und prueft nur, was es kennt.
    Ein Wert, den der Knopf "Zufaellig waehlen" erzeugt, ist ein ganz normaler Text und muss zum `pattern` passen."""
    schema = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string", "title": "Thema", "pattern": "^[a-z0-9-]{1,64}$",
                "x-generate": {"label": "Zufällig wählen", "prefix": "nodvard-", "length": 20},
            },
        },
        "x-qr-url": "/ext/zufall/subscribe-link",
    }
    runtime = get_extension_runtime()
    runtime.discovered["zufall"] = SimpleNamespace(manifest=SimpleNamespace(settings_schema=schema), ok=True)
    try:
        db_session.add(ExtensionRecord(id="zufall", version="1", api_version="0.1.0", state="disabled", settings={}))
        await db_session.flush()
        headers = await _owner(client)
        read = await client.get("/api/v1/extensions/zufall/settings", headers=headers)
        assert read.json()["schema"]["x-qr-url"] == "/ext/zufall/subscribe-link"
        assert read.json()["schema"]["properties"]["topic"]["x-generate"]["prefix"] == "nodvard-"
        good = await client.put("/api/v1/extensions/zufall/settings", json={"current_password": "correct-horse-battery", "values": {"topic": "nodvard-k3j9x0a8s7d6f5g4h3j2"}}, headers=headers)
        assert good.status_code == 200, good.text
        bad = await client.put("/api/v1/extensions/zufall/settings", json={"current_password": "correct-horse-battery", "values": {"topic": "Nodvard Gross"}}, headers=headers)
        assert bad.status_code == 422
    finally:
        runtime.discovered.pop("zufall", None)


def test_every_generated_field_and_qr_address_of_the_shipped_extensions_is_usable():
    """`x-generate`: Prefix plus Zufallszeichen (a-z, 0-9) muss das `pattern` des Felds erfuellen und in `maxLength`
    passen, sonst bietet die Oberflaeche einen Knopf an, dessen Ergebnis sich nicht speichern laesst. `x-qr-url`
    ist ein Pfad unter /api/v1 in der eigenen Erweiterung."""
    import json
    from pathlib import Path

    from nodvard_deck.api.v1.extension_settings import _pattern_matches

    root = Path(__file__).resolve().parents[2] / "extensions"
    generated, qr = [], []

    def walk(ext_id, schema, path):
        spec = schema.get("x-generate")
        if spec is not None:
            generated.append(f"{ext_id}.{path}")
            assert schema.get("type") == "string" and not schema.get("enum"), path
            assert isinstance(spec.get("label"), str) and spec["label"], path
            prefix, length = spec.get("prefix", ""), spec["length"]
            assert isinstance(prefix, str) and isinstance(length, int) and 8 <= length <= 64, path
            limit = schema.get("maxLength") or 2000
            assert len(prefix) + length <= limit, path
            for filler in ("a", "m", "z", "0", "9"):
                value = prefix + filler * length
                assert _pattern_matches(schema.get("pattern", ""), value), (path, value)
        for key, sub in (schema.get("properties") or {}).items():
            walk(ext_id, sub, f"{path}.{key}" if path else key)
        if isinstance(schema.get("items"), dict):
            walk(ext_id, schema["items"], f"{path}[]")

    for file in sorted(root.glob("*/settings.schema.json")):
        schema = json.loads(file.read_text(encoding="utf-8"))
        ext_id = file.parent.name
        walk(ext_id, schema, "")
        if "x-qr-url" in schema:
            qr.append(ext_id)
            assert schema["x-qr-url"].startswith(f"/ext/{ext_id}/"), ext_id
    assert generated == ["ntfy.topic"] and qr == ["ntfy"]


async def _plain(db_session, test_settings, label: str) -> str | None:
    from sqlalchemy import select

    from nodvard_deck.core import vault
    from nodvard_deck.models import Secret

    await db_session.commit()
    row = (await db_session.execute(select(Secret).where(Secret.label == label))).scalar_one_or_none()
    return None if row is None else await vault.read_secret_plaintext(db_session, vault.load_keyring(test_settings), row.id)


@pytest.mark.asyncio
async def test_stored_secrets_are_replaced_or_removed_only_with_the_password(client, db_session, test_settings, bound_extension):
    """Dieselbe Hürde wie auf der Seite Tresor: Mit der Sitzung allein ersetzt oder löscht niemand ein gespeichertes
    Geheimnis über die Moduleinstellungen. Die Erstanlage bleibt ohne Passwort."""
    db_session.add(ExtensionRecord(
        id="bound", version="1", api_version="0.1.0", state="disabled", settings={"server_url": "https://a.example"},
    ))
    await db_session.flush()
    headers = await _owner(client)
    base = "/api/v1/extensions/bound"
    for label in ("free-token", "bound-token"):
        r = await client.put(f"{base}/secrets", json={"label": label, "value": "erst"}, headers=headers)
        assert r.status_code == 204, r.text

    for extra in ({}, {"current_password": ""}):
        r = await client.put(f"{base}/secrets", json={"label": "free-token", "value": "fremd", **extra}, headers=headers)
        assert r.status_code == 403, r.text
        assert r.json()["code"] == "confirm_required" and r.json()["two_factor"] is False
    r = await client.put(f"{base}/secrets", json={"label": "free-token", "value": "fremd", "current_password": "falsch"}, headers=headers)
    assert r.status_code == 400
    r = await client.delete(f"{base}/secrets", params={"label": "free-token"}, headers=headers)
    assert r.status_code == 403 and r.json()["code"] == "confirm_required"
    r = await client.request("DELETE", f"{base}/secrets", params={"label": "free-token"}, json={"current_password": "falsch"}, headers=headers)
    assert r.status_code == 400
    r = await client.put(f"{base}/settings", json={"values": {"server_url": "https://b.example"}}, headers=headers)
    assert r.status_code == 403 and r.json()["code"] == "confirm_required"
    assert (await client.get(f"{base}/settings", headers=headers)).json()["values"]["server_url"] == "https://a.example"
    assert await _plain(db_session, test_settings, "free-token") == "erst"
    assert await _plain(db_session, test_settings, "bound-token") == "erst"

    pw = {"current_password": "correct-horse-battery"}
    r = await client.put(f"{base}/secrets", json={"label": "free-token", "value": "neu", **pw}, headers=headers)
    assert r.status_code == 204, r.text
    assert await _plain(db_session, test_settings, "free-token") == "neu"
    r = await client.put(f"{base}/settings", json={"values": {"server_url": "https://b.example"}, **pw}, headers=headers)
    assert r.status_code == 200 and r.json()["secrets_cleared"] == ["bound-token"], r.text
    r = await client.request("DELETE", f"{base}/secrets", params={"label": "free-token"}, json=pw, headers=headers)
    assert r.status_code == 204, r.text
    assert await _plain(db_session, test_settings, "free-token") is None
    # Ohne gespeichertes Geheimnis braucht es nichts: ein neues Ziel ohne Token, ein leeres Löschen.
    r = await client.put(f"{base}/settings", json={"values": {"server_url": "https://c.example"}}, headers=headers)
    assert r.status_code == 200 and r.json()["secrets_cleared"] == []
    assert (await client.delete(f"{base}/secrets", params={"label": "free-token"}, headers=headers)).status_code == 204


@pytest.mark.asyncio
async def test_with_two_factor_the_module_settings_ask_for_the_code_too(client, db_session, test_settings, bound_extension):
    import pyotp
    from totp_helpers import setup_confirm_code

    db_session.add(ExtensionRecord(id="bound", version="1", api_version="0.1.0", state="disabled", settings={}))
    await db_session.flush()
    headers = await _owner(client)
    setup = await client.post("/api/v1/me/totp/setup", json={"current_password": "correct-horse-battery"}, headers=headers)
    secret = setup.json()["secret"]
    confirmed = await client.post("/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret)}, headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    base = "/api/v1/extensions/bound"
    assert (await client.put(f"{base}/secrets", json={"label": "free-token", "value": "erst"}, headers=headers)).status_code == 204

    r = await client.put(f"{base}/secrets", json={"label": "free-token", "value": "fremd"}, headers=headers)
    assert (r.status_code, r.json()["code"], r.json()["two_factor"]) == (403, "confirm_required", True)
    body = {"label": "free-token", "value": "fremd", "current_password": "correct-horse-battery"}
    r = await client.put(f"{base}/secrets", json=body, headers=headers)
    assert (r.status_code, r.json()["code"]) == (403, "totp_missing")
    assert await _plain(db_session, test_settings, "free-token") == "erst"
    r = await client.put(f"{base}/secrets", json={**body, "totp_code": pyotp.TOTP(secret).now()}, headers=headers)
    assert r.status_code == 204, r.text
    assert await _plain(db_session, test_settings, "free-token") == "fremd"
