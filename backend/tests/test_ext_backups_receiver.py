"""Proxmox-Backups nimmt eine Verbindung an, die "Proxmox VE" beim Verbinden mit Passwort angelegt hat
(`nodvard_sdk.ConnectionReceiver`, receiver.py). Die echte Erweiterung in der echten App, ohne Proxmox."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from nodvard_sdk import ConnectionReceiver
from nodvard_sdk.errors import SecretNotOwned
from pydantic import SecretStr
from sqlalchemy import select

from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import ExtensionRecord
from nodvard_deck.models.security import AuditEntry
from nodvard_deck.services import extensions as extensions_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
URL = "https://192.168.2.10:8006"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


async def _receiver(client, db_session, test_settings, connections: list[dict]):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post(
        "/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    token = login.json()["access_token"]
    enabled = await client.post("/api/v1/extensions/backups/enable", headers={"Authorization": f"Bearer {token}"})
    assert enabled.status_code == 200, enabled.text
    record = await db_session.get(ExtensionRecord, "backups")
    record.settings = {"connections": connections}
    await db_session.flush()
    (receiver,) = get_extension_runtime().capabilities.query(ConnectionReceiver)
    return receiver, get_extension_runtime().loaded["backups"].ctx


async def _connections(db_session) -> list[dict]:
    record = await db_session.get(ExtensionRecord, "backups")
    await db_session.refresh(record)
    return list((record.settings or {}).get("connections") or [])


async def _audit(db_session) -> list[AuditEntry]:
    return list((await db_session.execute(
        select(AuditEntry).where(AuditEntry.action == "backups.connection_received")
    )).scalars())


async def _secret(ctx, label: str) -> str | None:
    if not await ctx.secrets.exists(label):
        return None
    async with ctx.vault_use(await ctx.secrets.get_handle(label)) as value:
        return value


@pytest.mark.asyncio
async def test_the_receiver_checks_names_and_addresses_without_changing_anything(client, db_session, test_settings):
    existing = {"name": "pve1", "base_url": URL, "token_id": "a@pve!x"}
    receiver, _ctx = await _receiver(client, db_session, test_settings, [existing])
    assert receiver.connection_kind == "proxmox"
    assert await receiver.connection_conflict(name="neu", base_url="https://192.168.2.11:8006") is None
    # Dieselbe Adresse (andere Schreibweise) unter anderem Namen: die vorhandene Verbindung bekommt den Token.
    assert await receiver.connection_conflict(name="anders", base_url="https://192.168.2.10:8006/") is None
    taken = await receiver.connection_conflict(name="pve1", base_url="https://192.168.2.11:8006")
    assert taken is not None and "anderen Server" in taken
    assert await receiver.connection_conflict(name="mit leerzeichen", base_url="https://192.168.2.11:8006") is not None
    assert await _connections(db_session) == [existing]


@pytest.mark.asyncio
async def test_a_new_connection_is_stored_like_the_settings_form_does(client, db_session, test_settings):
    receiver, ctx = await _receiver(client, db_session, test_settings, [])
    result = await receiver.receive_connection(
        name="pve1", base_url=URL, token_id="nodvard@pve!deck-backup", token_secret=SecretStr("geheim-1"),
        tls_insecure_skip_verify=True,
    )
    assert result == {
        "name": "pve1", "replaced_token_id": None, "tls_insecure_skip_verify": True, "enabled": True,
        "tls_fingerprint_sha256": None, "replaced_fingerprint": None,
    }
    assert await _connections(db_session) == [{
        "name": "pve1", "base_url": URL, "token_id": "nodvard@pve!deck-backup", "tls_insecure_skip_verify": True,
        "enabled": True,
    }]
    assert await _secret(ctx, "backups-token:pve1") == "geheim-1"
    assert await receiver.received_token_ids() == ["nodvard@pve!deck-backup"]
    (row,) = await _audit(db_session)
    assert row.target_id == "backups"
    assert row.detail == {
        "connection": "pve1", "address": URL, "token_id": "nodvard@pve!deck-backup", "replaced_token_id": None,
        "from": "proxmox", "fingerprint": None, "previous_fingerprint": None,
    }


@pytest.mark.asyncio
async def test_an_existing_connection_to_the_same_server_keeps_its_name_and_gets_the_new_token(client, db_session, test_settings):
    receiver, ctx = await _receiver(
        client, db_session, test_settings,
        [{"name": "alt", "base_url": URL, "token_id": "nodvard@pve!handarbeit", "tls_insecure_skip_verify": True, "enabled": False}],
    )
    first = await ctx.secrets.create(label="backups-token:alt", kind="generic", value="alter-wert")
    result = await receiver.receive_connection(
        name="pve1", base_url="https://192.168.2.10:8006/", token_id="nodvard@pve!deck-backup",
        token_secret=SecretStr("neuer-wert"), tls_insecure_skip_verify=False,
    )
    # Strenger werden darf sie; die ausgeschaltete bleibt aus.
    assert result == {
        "name": "alt", "replaced_token_id": "nodvard@pve!handarbeit", "tls_insecure_skip_verify": False, "enabled": False,
        "tls_fingerprint_sha256": None, "replaced_fingerprint": None,
    }
    assert await _connections(db_session) == [{
        "name": "alt", "base_url": URL, "token_id": "nodvard@pve!deck-backup", "tls_insecure_skip_verify": False,
        "enabled": False,
    }]
    assert [r.detail["replaced_token_id"] for r in await _audit(db_session)] == ["nodvard@pve!handarbeit"]
    # In einem Schritt ersetzt: dieselbe Zeile im Tresor, neuer Wert.
    assert (await ctx.secrets.get_handle("backups-token:alt")).id == first.id
    assert await _secret(ctx, "backups-token:alt") == "neuer-wert"


@pytest.mark.asyncio
async def test_if_storing_fails_the_old_state_stays(client, db_session, test_settings, monkeypatch):
    old = {"name": "pve1", "base_url": URL, "token_id": "nodvard@pve!deck-backup"}
    receiver, ctx = await _receiver(client, db_session, test_settings, [old])
    await ctx.secrets.create(label="backups-token:pve1", kind="generic", value="alter-wert")

    async def broken(**_kwargs):
        raise RuntimeError("Tresor kaputt")

    monkeypatch.setattr(ctx.secrets, "replace", broken)
    with pytest.raises(RuntimeError):
        await receiver.receive_connection(
            name="pve1", base_url=URL, token_id="nodvard@pve!deck-backup-2", token_secret=SecretStr("neu"),
            tls_insecure_skip_verify=True,
        )
    assert await _connections(db_session) == [old]
    assert await _secret(ctx, "backups-token:pve1") == "alter-wert"


@pytest.mark.asyncio
async def test_a_name_of_another_server_is_refused_when_receiving(client, db_session, test_settings):
    other = {"name": "pve1", "base_url": "https://192.168.2.99:8006", "token_id": "a@pve!b"}
    receiver, ctx = await _receiver(client, db_session, test_settings, [other])
    with pytest.raises(Exception, match="anderen Server"):
        await receiver.receive_connection(
            name="pve1", base_url=URL, token_id="nodvard@pve!deck-backup", token_secret=SecretStr("x"),
            tls_insecure_skip_verify=True,
        )
    assert await _connections(db_session) == [other]
    assert await _secret(ctx, "backups-token:pve1") is None


@pytest.mark.asyncio
async def test_a_strict_connection_is_never_switched_to_allow_self_signed_certificates(client, db_session, test_settings):
    strict = {"name": "pve1", "base_url": URL, "token_id": "a@pve!x", "tls_insecure_skip_verify": False}
    receiver, ctx = await _receiver(client, db_session, test_settings, [strict])
    await ctx.secrets.create(label="backups-token:pve1", kind="generic", value="alt")
    result = await receiver.receive_connection(
        name="pve1", base_url=URL, token_id="nodvard-backup@pve!deck", token_secret=SecretStr("neu"),
        tls_insecure_skip_verify=True,
    )
    assert (result["tls_insecure_skip_verify"], result["enabled"]) == (False, True)
    assert await _connections(db_session) == [{**strict, "token_id": "nodvard-backup@pve!deck", "enabled": True}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("base_url", "token_id"),
    [
        ("http://192.168.2.10:8006", "nodvard-backup@pve!deck"),
        ("https://", "nodvard-backup@pve!deck"),
        ("https://nutzer:pw@192.168.2.10:8006", "nodvard-backup@pve!deck"),
        ("https://192.168.2.10:8006/api2/json", "nodvard-backup@pve!deck"),
        ("https://192.168.2.10:8006?x=1", "nodvard-backup@pve!deck"),
        ("https://192.168.2.10:8006\n", "nodvard-backup@pve!deck"),
        ("https://192.168.2.10:99999", "nodvard-backup@pve!deck"),
        (URL, "deck"),
        (URL, "nodvard-backup@pve"),
        (URL, "nodvard backup@pve!deck"),
        (URL, "nodvard-backup@pve!deck!zwei"),
    ],
)
async def test_only_what_a_hand_made_connection_could_be_is_received(client, db_session, test_settings, base_url, token_id):
    receiver, ctx = await _receiver(client, db_session, test_settings, [])
    with pytest.raises(Exception, match="Für Proxmox-Backups ist"):
        await receiver.receive_connection(
            name="pve1", base_url=base_url, token_id=token_id, token_secret=SecretStr("x"),
            tls_insecure_skip_verify=True,
        )
    assert await _connections(db_session) == []
    assert await _secret(ctx, "backups-token:pve1") is None
    assert await _audit(db_session) == []


async def _foreign_secret(client, label: str) -> None:
    """Wie von Hand ueber die Tresor-Schnittstelle angelegt: ohne Besitzer-Erweiterung."""
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    r = await client.post(
        "/api/v1/secrets", json={"label": label, "kind": "api_token", "value": "von-hand"},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_a_foreign_secret_is_never_touched_and_nothing_changes(client, db_session, test_settings):
    old = {"name": "pve2", "base_url": "https://192.168.2.12:8006", "token_id": "a@pve!x"}
    receiver, ctx = await _receiver(client, db_session, test_settings, [old])
    await _foreign_secret(client, "backups-token:pve1")
    await _foreign_secret(client, "backups-token:pve2")
    # Neue Verbindung: faellt vor jeder Aenderung auf.
    with pytest.raises(SecretNotOwned):
        await receiver.receive_connection(
            name="pve1", base_url=URL, token_id="nodvard-backup@pve!deck", token_secret=SecretStr("neu"),
            tls_insecure_skip_verify=True,
        )
    # Vorhandene Verbindung: faellt beim Ersetzen auf, die Einstellungen kommen zurueck.
    with pytest.raises(SecretNotOwned):
        await receiver.receive_connection(
            name="pve2", base_url=old["base_url"], token_id="nodvard-backup@pve!deck", token_secret=SecretStr("neu"),
            tls_insecure_skip_verify=True,
        )
    assert await _connections(db_session) == [old]
    assert await _audit(db_session) == []
    for label in ("backups-token:pve1", "backups-token:pve2"):
        assert await ctx.secrets.exists(label)


PIN_A = "AB" * 32
PIN_A_COLONS = ":".join(["AB"] * 32)
PIN_B = ":".join(["CD"] * 32)


@pytest.mark.asyncio
async def test_a_confirmed_fingerprint_is_stored_instead_of_skipping_the_check(client, db_session, test_settings):
    """Mit `pinned_sha256` merkt sich der Empfaenger den Fingerabdruck (in der Schreibweise von Proxmox) und schaltet
    "Selbstsigniertes Zertifikat erlauben" aus, auch wenn der Anbieter es mitschickt."""
    receiver, _ctx = await _receiver(client, db_session, test_settings, [])
    result = await receiver.receive_connection(
        name="pve1", base_url=URL, token_id="nodvard-backup@pve!deck", token_secret=SecretStr("geheim"),
        tls_insecure_skip_verify=True, pinned_sha256=PIN_A.lower(),
    )
    assert result["tls_fingerprint_sha256"] == PIN_A_COLONS
    assert result["tls_insecure_skip_verify"] is False
    assert await _connections(db_session) == [{
        "name": "pve1", "base_url": URL, "token_id": "nodvard-backup@pve!deck", "tls_insecure_skip_verify": False,
        "enabled": True, "tls_fingerprint_sha256": PIN_A_COLONS,
    }]
    (row,) = await _audit(db_session)
    assert row.detail["fingerprint"] == PIN_A_COLONS


@pytest.mark.asyncio
async def test_a_new_confirmed_fingerprint_replaces_the_old_one(client, db_session, test_settings):
    receiver, ctx = await _receiver(
        client, db_session, test_settings,
        [{"name": "pve1", "base_url": URL, "token_id": "a@pve!x", "tls_insecure_skip_verify": True, "tls_fingerprint_sha256": PIN_B}],
    )
    await ctx.secrets.create(label="backups-token:pve1", kind="generic", value="alt")
    result = await receiver.receive_connection(
        name="pve1", base_url=URL, token_id="nodvard-backup@pve!deck", token_secret=SecretStr("neu"),
        tls_insecure_skip_verify=False, pinned_sha256=PIN_A,
    )
    assert result["tls_fingerprint_sha256"] == PIN_A_COLONS
    assert result["replaced_fingerprint"] == PIN_B
    (conn,) = await _connections(db_session)
    assert (conn["tls_fingerprint_sha256"], conn["tls_insecure_skip_verify"]) == (PIN_A_COLONS, False)
    (row,) = await _audit(db_session)
    assert (row.detail["fingerprint"], row.detail["previous_fingerprint"]) == (PIN_A_COLONS, PIN_B)


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [PIN_B, "kaputt"])
async def test_a_remembered_fingerprint_is_never_weakened_without_a_new_one(client, db_session, test_settings, stored):
    """Ohne neuen Fingerabdruck bleibt ein gemerkter stehen (auch ein ungueltiger, der die Verbindung sperrt), und
    "nicht pruefen" kommt nie dazu -- auch wenn der Anbieter es verlangt."""
    receiver, ctx = await _receiver(
        client, db_session, test_settings,
        [{"name": "pve1", "base_url": URL, "token_id": "a@pve!x", "tls_insecure_skip_verify": False, "tls_fingerprint_sha256": stored}],
    )
    await ctx.secrets.create(label="backups-token:pve1", kind="generic", value="alt")
    result = await receiver.receive_connection(
        name="pve1", base_url=URL, token_id="nodvard-backup@pve!deck", token_secret=SecretStr("neu"),
        tls_insecure_skip_verify=True,
    )
    (conn,) = await _connections(db_session)
    assert conn["tls_fingerprint_sha256"] == stored
    assert conn["tls_insecure_skip_verify"] is False
    assert result["tls_insecure_skip_verify"] is False
    assert result["tls_fingerprint_sha256"] == (PIN_B if stored == PIN_B else None)


@pytest.mark.asyncio
async def test_an_invalid_fingerprint_is_refused_without_changes(client, db_session, test_settings):
    receiver, ctx = await _receiver(client, db_session, test_settings, [])
    with pytest.raises(Exception, match="Fingerabdruck") as refused:
        await receiver.receive_connection(
            name="pve1", base_url=URL, token_id="nodvard-backup@pve!deck", token_secret=SecretStr("geheim"),
            tls_insecure_skip_verify=False, pinned_sha256="nicht-hex",
        )
    assert type(refused.value).__name__ == "ConnectionRefused"
    assert await _connections(db_session) == []
    assert not await ctx.secrets.exists("backups-token:pve1")
