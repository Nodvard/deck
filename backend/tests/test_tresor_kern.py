"""Tresor-Kern (N8, T1): gedrosseltes `vault_use`, `secret_usages`, Ablaufdatum, `used_by` und `PATCH /secrets/{id}`.

Die Drosselung ist die Auflage aus der Pruefung des Plans: Module fragen regelmaessig ab, und jede Abfrage schrieb
`last_used_at` und eine Zeile `secret.used` ins Protokoll (wie bei #270). Jetzt hoechstens einmal je Stunde und
Verwender -- 100 Zugriffe in einer Stunde ergeben genau einen Eintrag."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from nodvard_deck.core import vault
from nodvard_deck.models import AuditEntry, Host, HostCredential, Secret, SecretUsage
from sqlalchemy import func, select

T0 = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)


def _keyring(*, versions: int = 1) -> vault.Keyring:
    from cryptography.fernet import Fernet

    return {v: Fernet.generate_key() for v in range(1, versions + 1)}


@pytest.fixture(autouse=True)
def _fresh_memory(monkeypatch):
    """Die Drosselung merkt sich je Prozess, wann sie zuletzt geschrieben hat -- jeder Test faengt leer an."""
    monkeypatch.setattr(vault, "_noted", {})


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock(T0)
    monkeypatch.setattr(vault, "_now", c)
    return c


async def _secret(db_session, keyring, label="proxmox-token", **kwargs) -> vault.SecretHandle:
    secret = await vault.create_secret(db_session, keyring, label=label, kind="api_token", plaintext="tok_geheim", **kwargs)
    await db_session.commit()
    return vault.SecretHandle(id=secret.id, label=secret.label, kind=secret.kind)


async def _use(db_session, keyring, handle, ext_id="proxmox") -> str:
    async with vault.vault_use(db_session, keyring, handle, actor_type="extension", actor_id=ext_id) as value:
        return value


async def _audit_count(db_session) -> int:
    return (await db_session.execute(
        select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "secret.used")
    )).scalar_one()


async def _usage(db_session, handle, ext_id="proxmox") -> SecretUsage | None:
    db_session.expire_all()
    return await db_session.get(SecretUsage, (handle.id, "extension", ext_id))


@pytest.mark.asyncio
async def test_hundred_uses_within_an_hour_leave_exactly_one_entry(db_session, clock):
    keyring = _keyring()
    handle = await _secret(db_session, keyring)

    for minute in range(100):
        clock.now = T0 + timedelta(seconds=35 * minute)  # 100 Zugriffe in knapp einer Stunde
        assert await _use(db_session, keyring, handle) == "tok_geheim"

    assert await _audit_count(db_session) == 1
    usage = await _usage(db_session, handle)
    assert usage is not None and usage.first_used_at == T0 and usage.last_used_at == T0
    assert (await db_session.get(Secret, handle.id)).last_used_at == T0
    assert (await db_session.execute(select(func.count()).select_from(SecretUsage))).scalar_one() == 1


@pytest.mark.asyncio
async def test_after_an_hour_the_next_use_is_noted_again(db_session, clock):
    keyring = _keyring()
    handle = await _secret(db_session, keyring)
    await _use(db_session, keyring, handle)

    clock.now = T0 + timedelta(minutes=59, seconds=59)
    await _use(db_session, keyring, handle)
    assert await _audit_count(db_session) == 1

    clock.now = T0 + timedelta(hours=1)
    await _use(db_session, keyring, handle)
    assert await _audit_count(db_session) == 2
    usage = await _usage(db_session, handle)
    assert usage.first_used_at == T0 and usage.last_used_at == T0 + timedelta(hours=1)


@pytest.mark.asyncio
async def test_each_consumer_is_noted_on_its_own(db_session, clock):
    keyring = _keyring()
    handle = await _secret(db_session, keyring)
    for _ in range(5):
        await _use(db_session, keyring, handle, "proxmox")
        await _use(db_session, keyring, handle, "backups")

    assert await _audit_count(db_session) == 2
    actors = {e.actor_id for e in (await db_session.execute(select(AuditEntry))).scalars().all()}
    assert actors == {"proxmox", "backups"}


@pytest.mark.asyncio
async def test_after_a_restart_the_stored_usage_decides(db_session, clock, monkeypatch):
    """Nach einem Neustart ist die Erinnerung des Prozesses leer; die Zeile in `secret_usages` verhindert, dass
    jeder Neustart wieder einen Eintrag schreibt."""
    keyring = _keyring()
    handle = await _secret(db_session, keyring)
    await _use(db_session, keyring, handle)

    monkeypatch.setattr(vault, "_noted", {})
    clock.now = T0 + timedelta(minutes=30)
    await _use(db_session, keyring, handle)
    assert await _audit_count(db_session) == 1


@pytest.mark.asyncio
async def test_simultaneous_first_uses_leave_one_entry(db_session, clock, monkeypatch):
    """Erster Zugriff nach dem Start, mehrfach gleichzeitig: waehrend einer in `secret_usages` nachsieht, schreiben
    die anderen nicht mit (sonst zwei Zeilen `secret.used`)."""
    import asyncio

    keyring = _keyring()
    handle = await _secret(db_session, keyring)
    real = vault._stored_last_use

    async def slow(session, key):
        for _ in range(5):
            await asyncio.sleep(0)  # die anderen Zugriffe kommen in dieser Zeit dran
        return await real(session, key)

    monkeypatch.setattr(vault, "_stored_last_use", slow)
    values = await asyncio.gather(*(_use(db_session, keyring, handle) for _ in range(5)))

    assert values == ["tok_geheim"] * 5
    assert await _audit_count(db_session) == 1
    assert vault._deciding == set()


@pytest.mark.asyncio
async def test_the_stored_usage_is_read_fresh_not_from_the_callers_identity_map(db_session, clock, monkeypatch):
    """Eine Aufrufer-Session mit einer alten Zeile im Speicher darf die Entscheidung nicht verfaelschen."""
    keyring = _keyring()
    handle = await _secret(db_session, keyring)
    await _use(db_session, keyring, handle)
    stale = await db_session.get(SecretUsage, (handle.id, "extension", "proxmox"))  # jetzt in der Identity-Map

    clock.now = T0 + timedelta(hours=2)
    await _use(db_session, keyring, handle)  # schreibt, Zeile in der Datenbank: T0 + 2 h
    assert stale is not None

    monkeypatch.setattr(vault, "_noted", {})  # "Neustart", die Session haelt noch den alten Stand
    clock.now = T0 + timedelta(hours=2, minutes=10)
    await _use(db_session, keyring, handle)
    assert await _audit_count(db_session) == 2


@pytest.mark.asyncio
async def test_a_clock_that_went_back_counts_as_due(db_session, clock):
    keyring = _keyring()
    handle = await _secret(db_session, keyring)
    await _use(db_session, keyring, handle)

    clock.now = T0 - timedelta(days=2)
    await _use(db_session, keyring, handle)
    assert await _audit_count(db_session) == 2


@pytest.mark.asyncio
async def test_key_rotation_still_migrates_while_the_note_is_throttled(db_session, clock):
    """Neu verschluesseln nach einer Rotation haengt nicht an der Drosselung."""
    from cryptography.fernet import Fernet

    keyring = _keyring()
    handle = await _secret(db_session, keyring)
    await _use(db_session, keyring, handle)

    keyring[2] = Fernet.generate_key()
    clock.now = T0 + timedelta(minutes=5)
    assert await _use(db_session, keyring, handle) == "tok_geheim"

    db_session.expire_all()
    assert (await db_session.get(Secret, handle.id)).key_version == 2
    assert await _audit_count(db_session) == 1


@pytest.mark.asyncio
async def test_a_failed_note_is_tried_again_on_the_next_use(db_session, clock, monkeypatch):
    """Scheitert das Festhalten, schweigt die Drosselung nicht eine Stunde lang."""
    keyring = _keyring()
    handle = await _secret(db_session, keyring)

    real_write = vault.audit.write_entry

    async def broken(*args, **kwargs):
        raise RuntimeError("Platte voll")

    monkeypatch.setattr(vault.audit, "write_entry", broken)
    with pytest.raises(RuntimeError):
        await _use(db_session, keyring, handle)
    assert vault._noted == {}
    assert await _usage(db_session, handle) is None  # nichts halb geschrieben
    assert (await db_session.get(Secret, handle.id)).last_used_at is None

    monkeypatch.setattr(vault.audit, "write_entry", real_write)
    clock.now = T0 + timedelta(minutes=1)
    await _use(db_session, keyring, handle)
    assert await _audit_count(db_session) == 1


@pytest.mark.asyncio
async def test_deleting_a_secret_removes_its_usages(db_session, clock):
    keyring = _keyring()
    handle = await _secret(db_session, keyring)
    await _use(db_session, keyring, handle)
    assert await _usage(db_session, handle) is not None

    await vault.delete_secret(db_session, handle.id)
    await db_session.commit()
    assert (await db_session.execute(select(func.count()).select_from(SecretUsage))).scalar_one() == 0


# ---------------------------------------------------------------------------
# API: expires_at, used_by, PATCH
# ---------------------------------------------------------------------------


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_create_with_expiry_and_list_shows_it(client):
    headers = await _bootstrap_owner(client)
    created = await client.post(
        "/api/v1/secrets",
        json={"label": "ha-token", "kind": "api_token", "value": "GEHEIM-1", "expires_at": "2027-01-31T12:00:00"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["expires_at"].startswith("2027-01-31T12:00:00")
    assert created.json()["used_by"] == []

    listed = (await client.get("/api/v1/secrets", headers=headers)).json()
    entry = next(s for s in listed if s["label"] == "ha-token")
    assert entry["expires_at"].startswith("2027-01-31T12:00:00")
    assert "GEHEIM-1" not in str(listed)


@pytest.mark.asyncio
async def test_used_by_names_module_server_and_observed_use(client, db_session, test_settings, clock):
    headers = await _bootstrap_owner(client)
    keyring = vault.load_keyring(test_settings)

    owned = await vault.create_secret(
        db_session, keyring, label="ntfy-token", kind="api_token", plaintext="x", owner_ext_id="ntfy"
    )
    ssh = await vault.create_secret(db_session, keyring, label="host-cred:h1:abc", kind="ssh_password", plaintext="pw")
    host = Host(name="docker", display_name="Docker-VM", address="198.51.100.5")
    db_session.add(host)
    await db_session.flush()
    db_session.add(HostCredential(host_id=host.id, kind="ssh_password", username="root", port=22, secret_id=ssh.id))
    await db_session.commit()

    # Ein anderes Modul liest das Token des ntfy-Moduls mit (Recht `secrets.read:ntfy-token`).
    await _use(db_session, keyring, vault.SecretHandle(id=owned.id, label=owned.label, kind=owned.kind), "documents")

    listed = {s["label"]: s for s in (await client.get("/api/v1/secrets", headers=headers)).json()}
    # Ohne geladene Module heisst ein Modul wie seine Kennung; sortiert nach Name.
    assert [(u["type"], u["id"]) for u in listed["ntfy-token"]["used_by"]] == [("extension", "documents"), ("extension", "ntfy")]
    by_id = {u["id"]: u for u in listed["ntfy-token"]["used_by"]}
    assert by_id["documents"]["last_used_at"].startswith("2026-10-12T08:00:00")
    assert by_id["ntfy"]["last_used_at"] is None
    assert listed["host-cred:h1:abc"]["used_by"] == [
        {"type": "host", "id": host.id, "name": "Docker-VM", "last_used_at": None}
    ]


@pytest.mark.asyncio
async def test_used_by_follows_a_renamed_module_and_sorts_server_before_module(client, db_session, test_settings, monkeypatch):
    from types import SimpleNamespace

    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck.models import SecretUsage as Usage

    runtime = get_extension_runtime()
    monkeypatch.setattr(runtime, "legacy_owner", {"altes-modul": "neues-modul"})
    monkeypatch.setitem(runtime.discovered, "neues-modul", SimpleNamespace(manifest=SimpleNamespace(name="Neues Modul")))

    headers = await _bootstrap_owner(client)
    keyring = vault.load_keyring(test_settings)
    secret = await vault.create_secret(
        db_session, keyring, label="umzug-key", kind="ssh_password", plaintext="pw", owner_ext_id="altes-modul"
    )
    host = Host(name="heimserver", display_name="", address="198.51.100.7")
    db_session.add(host)
    await db_session.flush()
    db_session.add(HostCredential(host_id=host.id, kind="ssh_password", username="root", port=22, secret_id=secret.id))
    # Ein Verwender einer anderen Art (heute schreibt nur `vault_use` mit Modulen): erscheint mit seiner Kennung.
    db_session.add(Usage(secret_id=secret.id, consumer_type="user", consumer_id="u-42", first_used_at=T0, last_used_at=T0))
    # Nutzung unter der alten Kennung landet beim heutigen Modul.
    db_session.add(Usage(secret_id=secret.id, consumer_type="extension", consumer_id="altes-modul", first_used_at=T0, last_used_at=T0))
    await db_session.commit()

    listed = {s["label"]: s for s in (await client.get("/api/v1/secrets", headers=headers)).json()}
    used_by = listed["umzug-key"]["used_by"]
    assert [(u["type"], u["id"], u["name"]) for u in used_by] == [
        ("host", host.id, "heimserver"),
        ("extension", "neues-modul", "Neues Modul"),
        ("user", "u-42", "u-42"),
    ]
    assert used_by[1]["last_used_at"].startswith("2026-10-12T08:00:00")


@pytest.mark.asyncio
async def test_used_by_names_the_account_of_a_two_factor_key(client, db_session):
    from nodvard_deck.models import User
    from totp_helpers import setup_confirm_code

    headers = await _bootstrap_owner(client)
    setup = await client.post("/api/v1/me/totp/setup", json={"current_password": "correct-horse-battery"}, headers=headers)
    await client.post("/api/v1/me/totp/confirm", json={"code": setup_confirm_code(setup.json()["secret"])}, headers=headers)
    owner = (await db_session.execute(select(User).where(User.username == "owner1"))).scalar_one()

    listed = {s["id"]: s for s in (await client.get("/api/v1/secrets", headers=headers)).json()}
    assert listed[owner.totp_secret_id]["used_by"] == [
        {"type": "login", "id": owner.id, "name": "owner1", "last_used_at": None}
    ]


@pytest.mark.asyncio
async def test_patch_changes_description_and_expiry_only(client, db_session, test_settings):
    headers = await _bootstrap_owner(client)
    created = (await client.post(
        "/api/v1/secrets",
        json={"label": "jellyfin", "kind": "api_token", "value": "WERT-BLEIBT", "description": "alt"},
        headers=headers,
    )).json()

    r = await client.patch(
        f"/api/v1/secrets/{created['id']}", json={"expires_at": "2027-03-01T00:00:00+01:00"}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "alt"
    assert r.json()["expires_at"].startswith("2027-02-28T23:00:00")  # in UTC

    r = await client.patch(f"/api/v1/secrets/{created['id']}", json={"description": "  neu  "}, headers=headers)
    assert r.json()["description"] == "neu"
    assert r.json()["expires_at"].startswith("2027-02-28T23:00:00")

    r = await client.patch(f"/api/v1/secrets/{created['id']}", json={"expires_at": None}, headers=headers)
    assert r.json()["expires_at"] is None
    assert "WERT-BLEIBT" not in r.text

    secret = await db_session.get(Secret, created["id"])
    await db_session.refresh(secret)
    assert await vault.read_secret_plaintext(db_session, vault.load_keyring(test_settings), secret.id) == "WERT-BLEIBT"


@pytest.mark.asyncio
async def test_patch_edge_cases(client):
    headers = await _bootstrap_owner(client)
    created = (await client.post(
        "/api/v1/secrets", json={"label": "plex", "kind": "api_token", "value": "x", "description": "alt"}, headers=headers,
    )).json()
    url = f"/api/v1/secrets/{created['id']}"

    r = await client.patch(url, json={"description": None}, headers=headers)
    assert r.status_code == 200 and r.json()["description"] == ""
    assert (await client.patch(url, json={"description": "x" * 256}, headers=headers)).status_code == 422
    for odd in ("0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00"):
        r = await client.patch(url, json={"expires_at": odd}, headers=headers)
        assert r.status_code == 422, (odd, r.text)
    r = await client.post(
        "/api/v1/secrets",
        json={"label": "rand", "kind": "generic", "value": "x", "expires_at": "9999-12-31T23:59:59-05:00"},
        headers=headers,
    )
    assert r.status_code == 422
    assert (await client.patch(url, json={}, headers=headers)).json()["description"] == ""


@pytest.mark.asyncio
async def test_patch_unknown_secret_is_404_and_needs_write_permission(client, db_session):
    headers = await _bootstrap_owner(client)
    assert (await client.patch("/api/v1/secrets/gibt-es-nicht", json={"description": "x"}, headers=headers)).status_code == 404

    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    viewer = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    viewer.roles.append(roles["viewer"])
    db_session.add(viewer)
    await db_session.commit()
    token = (await client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"})).json()["access_token"]
    r = await client.patch("/api/v1/secrets/gibt-es-nicht", json={"description": "x"}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_patch_refuses_the_two_factor_key_of_an_account(client, db_session):
    from nodvard_deck.models import User
    from totp_helpers import setup_confirm_code

    headers = await _bootstrap_owner(client)
    setup = await client.post("/api/v1/me/totp/setup", json={"current_password": "correct-horse-battery"}, headers=headers)
    await client.post("/api/v1/me/totp/confirm", json={"code": setup_confirm_code(setup.json()["secret"])}, headers=headers)
    owner = (await db_session.execute(select(User).where(User.username == "owner1"))).scalar_one()

    r = await client.patch(f"/api/v1/secrets/{owner.totp_secret_id}", json={"description": "x"}, headers=headers)
    assert r.status_code == 409
    assert "Zwei-Faktor" in r.json()["detail"]
