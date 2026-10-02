"""Zugangsdaten gehören zu ihrem Ziel: Wer eine Adresse ändert oder eine Verbindung entfernt,
lässt das Geheimnis nicht an der neuen Adresse zurück. Gegen die echten Erweiterungen."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from nodvard_deck.models import ExtensionRecord, Secret
from nodvard_deck.services import extensions as extensions_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
_TOKEN_ID = "root@pam!deck"


async def _owner_with(client, db_session, test_settings, *ext_ids: str) -> dict:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    for ext_id in ext_ids:
        enabled = await client.post(f"/api/v1/extensions/{ext_id}/enable", headers=headers)
        assert enabled.status_code == 200, enabled.text
    return headers


async def _has_secret(db_session, label: str) -> bool:
    return (await db_session.execute(select(Secret.id).where(Secret.label == label))).first() is not None


@pytest.mark.asyncio
async def test_ntfy_token_is_removed_when_server_url_changes(client, db_session, test_settings):
    headers = await _owner_with(client, db_session, test_settings, "ntfy")
    r = await client.put(
        "/api/v1/extensions/ntfy/settings", json={"values": {"server_url": "https://ntfy.example", "topic": "deck"}}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert (await client.put("/api/v1/extensions/ntfy/secrets", json={"label": "ntfy-token", "value": "tk_geheim"}, headers=headers)).status_code == 204
    assert await _has_secret(db_session, "ntfy-token")

    # Anderes Feld: Token bleibt.
    r = await client.put("/api/v1/extensions/ntfy/settings", json={"values": {"topic": "anders"}}, headers=headers)
    assert r.status_code == 200, r.text
    assert await _has_secret(db_session, "ntfy-token")

    r = await client.put("/api/v1/extensions/ntfy/settings", json={"values": {"server_url": "http://angreifer:8000"}}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["secrets_cleared"] == ["ntfy-token"]
    assert [s["is_set"] for s in r.json()["secrets"]] == [False]
    assert not await _has_secret(db_session, "ntfy-token")

    audit = await client.get("/api/v1/audit?action=extension.settings", headers=headers)
    rows = audit.json()["items"] if isinstance(audit.json(), dict) else audit.json()
    assert any(x["detail"].get("secrets_cleared") == ["ntfy-token"] for x in rows)
    assert "tk_geheim" not in audit.text

    # Ohne Token meldet eine weitere neue Adresse nichts als „gelöscht“.
    r = await client.put("/api/v1/extensions/ntfy/settings", json={"values": {"server_url": "https://ntfy2.example"}}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["secrets_cleared"] == []


@pytest.mark.asyncio
async def test_other_extensions_bind_their_secrets_to_the_address(test_settings):
    """Die Schemata der Erweiterungen benennen die Felder, an die ihr Geheimnis gebunden ist."""
    import json

    expected = {
        "nextcloud": {"nextcloud-app-password": ["base_url"]},
        "network": {"network-pihole-password": ["pihole.url"], "network-npm-password": ["npm.url"]},
        "nexus-soc": {"nexus-soc-ollama-key": ["ollama_url", "ollama_failover_url"]},
        "backups": {"backups-token:{name}": ["base_url"]},
        "proxmox": {"proxmox-token:{name}": ["base_url"]},
        "ntfy": {"ntfy-token": ["server_url"]},
    }
    for ext_id, labels in expected.items():
        schema = json.loads((REPO_EXTENSIONS_DIR / ext_id / "settings.schema.json").read_text(encoding="utf-8"))
        got = {s["label"]: s.get("x-secret-bound-to") for s in schema["x-secrets"]}
        assert got == labels, ext_id


@pytest.mark.asyncio
@pytest.mark.parametrize("ext_id,label", [("proxmox", "proxmox-token"), ("backups", "backups-token")])
async def test_connection_token_is_removed_on_new_address_and_on_removal(client, db_session, test_settings, ext_id, label):
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    base = f"/api/v1/ext/{ext_id}/connections"
    created = await client.post(
        base, json={"name": "pve1", "base_url": "https://192.168.2.10:8006", "token_id": _TOKEN_ID}, headers=headers,
    )
    assert created.status_code == 201, created.text
    assert (await client.post(f"{base}/pve1/token", json={"value": "tok-geheim"}, headers=headers)).status_code == 204
    assert await _has_secret(db_session, f"{label}:pve1")

    # Andere Felder und dieselbe Adresse (nur Schluss-Schrägstrich): das Token bleibt.
    r = await client.put(f"{base}/pve1", json={"tls_insecure_skip_verify": True, "base_url": "https://192.168.2.10:8006/"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["has_token"] is True
    # Andere Schreibweise derselben Adresse (Gross-/Kleinschreibung, Standardport): ebenfalls dasselbe Ziel.
    r = await client.put(f"{base}/pve1", json={"base_url": "HTTPS://192.168.2.10:8006//"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["has_token"] is True
    r = await client.put(f"{base}/pve1", json={"base_url": "https://192.168.2.10:8006/api2"}, headers=headers)
    assert r.json()["has_token"] is False  # anderer Pfad: im Zweifel ein neues Ziel
    assert (await client.post(f"{base}/pve1/token", json={"value": "tok-geheim"}, headers=headers)).status_code == 204

    r = await client.put(f"{base}/pve1", json={"base_url": "https://angreifer:8006"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["has_token"] is False
    assert not await _has_secret(db_session, f"{label}:pve1")

    # Entfernen: auch ein neues Token verschwindet, eine neue Verbindung gleichen Namens beginnt leer.
    assert (await client.post(f"{base}/pve1/token", json={"value": "tok-zwei"}, headers=headers)).status_code == 204
    assert (await client.delete(f"{base}/pve1", headers=headers)).status_code == 204
    assert not await _has_secret(db_session, f"{label}:pve1")
    again = await client.post(
        base, json={"name": "pve1", "base_url": "https://192.168.2.11:8006", "token_id": _TOKEN_ID}, headers=headers,
    )
    assert again.json()["has_token"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("ext_id,label", [("proxmox", "proxmox-token"), ("backups", "backups-token")])
async def test_new_connection_does_not_inherit_a_leftover_token(client, db_session, test_settings, ext_id, label):
    """Ein Token, das noch unter dem Namen liegt (von einer Verbindung, die vor diesem Stand
    entfernt wurde), geht nicht an eine neu angelegte Verbindung mit anderer Adresse."""
    headers = await _owner_with(client, db_session, test_settings, ext_id)
    base = f"/api/v1/ext/{ext_id}/connections"
    assert (await client.post(f"{base}/pve7/token", json={"value": "alt"}, headers=headers)).status_code == 204
    assert await _has_secret(db_session, f"{label}:pve7")
    created = await client.post(
        base, json={"name": "pve7", "base_url": "https://angreifer:8006", "token_id": _TOKEN_ID}, headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["has_token"] is False
    assert not await _has_secret(db_session, f"{label}:pve7")

    # Dasselbe über das Einstellungs-Formular.
    assert (await client.post(f"{base}/pve8/token", json={"value": "alt"}, headers=headers)).status_code == 204
    current = (await client.get(f"/api/v1/extensions/{ext_id}/settings", headers=headers)).json()["values"]["connections"]
    r = await client.put(
        f"/api/v1/extensions/{ext_id}/settings",
        json={"values": {"connections": [*current, {"name": "pve8", "base_url": "https://angreifer:8006", "token_id": _TOKEN_ID}]}},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["secrets_cleared"] == [f"{label}:pve8"]
    assert not await _has_secret(db_session, f"{label}:pve8")


@pytest.mark.asyncio
async def test_removing_a_connection_in_the_settings_form_removes_its_token(client, db_session, test_settings):
    headers = await _owner_with(client, db_session, test_settings, "proxmox")
    r = await client.put(
        "/api/v1/extensions/proxmox/settings",
        json={"values": {"connections": [{"name": "pve1", "base_url": "https://192.168.2.10:8006", "token_id": _TOKEN_ID}]}},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    await client.put("/api/v1/extensions/proxmox/secrets", json={"label": "proxmox-token:pve1", "value": "tok"}, headers=headers)
    assert await _has_secret(db_session, "proxmox-token:pve1")
    r = await client.put("/api/v1/extensions/proxmox/settings", json={"values": {"connections": []}}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["secrets_cleared"] == ["proxmox-token:pve1"]
    assert not await _has_secret(db_session, "proxmox-token:pve1")


@pytest.mark.asyncio
async def test_gameserver_profiles_survive_the_settings_page(client, db_session, test_settings):
    headers = await _owner_with(client, db_session, test_settings, "gameserver")
    record = await db_session.get(ExtensionRecord, "gameserver")
    record.settings = {"host_tag": "gameserver", "servers": {"h1": {"profile": "valheim"}}}
    await db_session.flush()

    # Alte Kopie der Profile plus eine harmlose Änderung -- wie ein veralteter zweiter Tab.
    r = await client.put(
        "/api/v1/extensions/gameserver/settings",
        json={"values": {"host_tag": "spiele", "servers": {"h1": {"profile": "x", "service_name": "a'b"}}}},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["values"]["host_tag"] == "spiele"
    assert r.json()["values"]["servers"] == {"h1": {"profile": "valheim"}}


@pytest.mark.asyncio
async def test_nexus_soc_key_goes_with_both_ai_servers(client, db_session, test_settings):
    """Der Schluessel gilt fuer Haupt- UND Ersatzserver der Nodvard KI. Wer den Ersatzserver neu
    einrichtet, darf den Schluessel nicht an einen eigenen Server schicken lassen; vorher gibt es
    keinen Schluessel ohne gespeicherte Adresse."""
    headers = await _owner_with(client, db_session, test_settings)
    label = "nexus-soc-ollama-key"
    base = "/api/v1/extensions/nexus-soc"

    early = await client.put(f"{base}/secrets", json={"label": label, "value": "key-geheim"}, headers=headers)
    assert early.status_code == 409, early.text

    saved = await client.put(f"{base}/settings", json={"values": {"ollama_url": "http://192.168.2.42:11434"}}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert (await client.put(f"{base}/secrets", json={"label": label, "value": "key-geheim"}, headers=headers)).status_code == 204
    assert await _has_secret(db_session, label)

    failover = await client.put(f"{base}/settings", json={"values": {"ollama_failover_url": "http://eigener-server:11434"}}, headers=headers)
    assert failover.status_code == 200, failover.text
    assert failover.json()["secrets_cleared"] == [label]
    assert not await _has_secret(db_session, label)
