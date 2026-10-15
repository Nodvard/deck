"""GET/POST/PUT/DELETE /secrets -- docs/04-API.md §3.

Zentrale Zusicherung ueber alle Tests hinweg: KEIN Response-Body enthaelt jemals den
Klartextwert oder `ciphertext` (docs/03-DATA-MODEL.md §3, Invariante 1).
"""

from __future__ import annotations

import pytest
from totp_helpers import setup_confirm_code


OWNER_PW = "correct-horse-battery"


async def _bootstrap_owner(client, username="owner1", password=OWNER_PW):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _create_user_with_role(db_session, *, username, password, role):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password(password), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.flush()
    return user


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(client):
    r = await client.get("/api/v1/secrets")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_viewer_without_secrets_permission_gets_403(client, db_session):
    await _create_user_with_role(
        db_session, username="viewer1", password="whatever123", role="viewer"
    )
    login = await client.post(
        "/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"}
    )
    token = login.json()["access_token"]

    r = await client.post(
        "/api/v1/secrets",
        json={"label": "x", "kind": "generic", "value": "geheim"},
        headers=_auth_header(token),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_owner_full_lifecycle_never_exposes_value(client):
    token = await _bootstrap_owner(client)

    created = await client.post(
        "/api/v1/secrets",
        json={
            "label": "ssh-fleet",
            "kind": "ssh_private_key",
            "value": "SUPER-GEHEIMER-WERT",
            "description": "Test-Secret",
        },
        headers=_auth_header(token),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["label"] == "ssh-fleet"
    assert body["kind"] == "ssh_private_key"
    assert body["key_version"] == 1
    assert "value" not in body
    assert "ciphertext" not in body
    assert "SUPER-GEHEIMER-WERT" not in created.text

    listed = await client.get("/api/v1/secrets", headers=_auth_header(token))
    assert listed.status_code == 200
    assert "SUPER-GEHEIMER-WERT" not in listed.text
    assert any(s["label"] == "ssh-fleet" for s in listed.json())

    replaced = await client.put(
        f"/api/v1/secrets/{body['id']}/value",
        json={"value": "NEUER-GEHEIMER-WERT", "current_password": OWNER_PW},
        headers=_auth_header(token),
    )
    assert replaced.status_code == 204
    assert "NEUER-GEHEIMER-WERT" not in replaced.text

    deleted = await client.request(
        "DELETE", f"/api/v1/secrets/{body['id']}", json={"current_password": OWNER_PW}, headers=_auth_header(token),
    )
    assert deleted.status_code == 204

    listed_after = await client.get("/api/v1/secrets", headers=_auth_header(token))
    assert all(s["label"] != "ssh-fleet" for s in listed_after.json())


@pytest.mark.asyncio
async def test_duplicate_label_is_rejected_with_409(client):
    token = await _bootstrap_owner(client)
    payload = {"label": "dup-label", "kind": "generic", "value": "a"}

    first = await client.post("/api/v1/secrets", json=payload, headers=_auth_header(token))
    assert first.status_code == 201

    second = await client.post("/api/v1/secrets", json=payload, headers=_auth_header(token))
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_replace_value_for_unknown_secret_returns_404(client):
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/secrets/does-not-exist/value",
        json={"value": "x", "current_password": OWNER_PW},
        headers=_auth_header(token),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_unknown_secret_is_idempotent(client):
    token = await _bootstrap_owner(client)
    r = await client.request(
        "DELETE", "/api/v1/secrets/does-not-exist", json={"current_password": OWNER_PW}, headers=_auth_header(token),
    )
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_invalid_kind_is_rejected_by_schema(client):
    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/secrets",
        json={"label": "x", "kind": "erfunden", "value": "a"},
        headers=_auth_header(token),
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_two_factor_key_of_an_account_cannot_be_replaced_or_deleted(client, db_session, test_settings):
    """Mit `secrets.write` liess sich der Zwei-Faktor-Schluessel eines fremden Kontos (auch des
    Inhabers) durch einen eigenen ersetzen oder loeschen -- ohne Passwort und an `reset-2fa`
    vorbei."""
    import pyotp
    from sqlalchemy import select

    from nodvard_deck.models import User

    owner_token = await _bootstrap_owner(client)
    setup = await client.post(
        "/api/v1/me/totp/setup", json={"current_password": "correct-horse-battery"}, headers=_auth_header(owner_token)
    )
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    confirmed = await client.post(
        "/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret)}, headers=_auth_header(owner_token)
    )
    assert confirmed.status_code == 200, confirmed.text
    owner = (await db_session.execute(select(User).where(User.username == "owner1"))).scalar_one()
    totp_secret_id = owner.totp_secret_id

    await _create_user_with_role(db_session, username="admin1", password="whatever123", role="admin")
    admin_token = (
        await client.post("/api/v1/auth/login", json={"username": "admin1", "password": "whatever123"})
    ).json()["access_token"]
    listed = (await client.get("/api/v1/secrets", headers=_auth_header(admin_token))).json()
    assert totp_secret_id in {s["id"] for s in listed}

    replaced = await client.put(
        f"/api/v1/secrets/{totp_secret_id}/value",
        json={"value": pyotp.random_base32()},
        headers=_auth_header(admin_token),
    )
    assert replaced.status_code == 409
    assert "Zwei-Faktor" in replaced.json()["detail"]
    deleted = await client.delete(f"/api/v1/secrets/{totp_secret_id}", headers=_auth_header(admin_token))
    assert deleted.status_code == 409

    # Der Schluessel ist unveraendert: der alte Authenticator gilt weiter.
    from nodvard_deck.core import vault

    await db_session.refresh(owner)
    assert owner.totp_secret_id == totp_secret_id
    assert await vault.read_secret_plaintext(db_session, vault.load_keyring(test_settings), totp_secret_id) == secret


async def _secret(client, token, label="pve-token", value="WERT-123") -> str:
    created = await client.post(
        "/api/v1/secrets", json={"label": label, "kind": "api_token", "value": value}, headers=_auth_header(token),
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _audit(db_session, action: str) -> list:
    from sqlalchemy import select

    from nodvard_deck.models import AuditEntry

    return (await db_session.execute(select(AuditEntry).where(AuditEntry.action == action))).scalars().all()


@pytest.mark.asyncio
async def test_replace_and_delete_need_the_own_password(client, db_session, test_settings):
    from nodvard_deck.core import vault

    token = await _bootstrap_owner(client)
    secret_id = await _secret(client, token)
    url = f"/api/v1/secrets/{secret_id}"

    for body in ({"value": "NEU"}, {"value": "NEU", "current_password": ""}):
        r = await client.put(f"{url}/value", json=body, headers=_auth_header(token))
        assert r.status_code == 403 and "Passwort" in r.json()["detail"]
    r = await client.put(f"{url}/value", json={"value": "NEU", "current_password": "falsch"}, headers=_auth_header(token))
    assert r.status_code == 400
    assert (await client.delete(url, headers=_auth_header(token))).status_code == 403
    r = await client.request("DELETE", url, json={"current_password": "falsch"}, headers=_auth_header(token))
    assert r.status_code == 400
    assert await vault.read_secret_plaintext(db_session, vault.load_keyring(test_settings), secret_id) == "WERT-123"

    r = await client.put(f"{url}/value", json={"value": "NEU", "current_password": OWNER_PW}, headers=_auth_header(token))
    assert r.status_code == 204
    assert await vault.read_secret_plaintext(db_session, vault.load_keyring(test_settings), secret_id) == "NEU"
    r = await client.request("DELETE", url, json={"current_password": OWNER_PW}, headers=_auth_header(token))
    assert r.status_code == 204

    for action in ("secret.created", "secret.replaced", "secret.deleted"):
        entries = await _audit(db_session, action)
        assert len(entries) == 1, action
        assert entries[0].detail["label"] == "pve-token"
        assert "WERT-123" not in str(entries[0].detail) and "NEU" not in str(entries[0].detail)


@pytest.mark.asyncio
async def test_with_two_factor_the_code_from_the_app_is_needed_too(client, db_session, test_settings):
    import pyotp

    token = await _bootstrap_owner(client)
    setup = await client.post("/api/v1/me/totp/setup", json={"current_password": OWNER_PW}, headers=_auth_header(token))
    secret = setup.json()["secret"]
    confirmed = await client.post(
        "/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret)}, headers=_auth_header(token),
    )
    assert confirmed.status_code == 200, confirmed.text
    secret_id = await _secret(client, token)
    url = f"/api/v1/secrets/{secret_id}"

    r = await client.put(f"{url}/value", json={"value": "NEU", "current_password": OWNER_PW}, headers=_auth_header(token))
    assert (r.status_code, r.json()["code"]) == (403, "totp_missing")
    r = await client.put(
        f"{url}/value", json={"value": "NEU", "current_password": OWNER_PW, "totp_code": "000000"}, headers=_auth_header(token),
    )
    assert r.status_code == 400 and r.json()["code"] == "totp_wrong"
    r = await client.request("DELETE", url, json={"current_password": OWNER_PW}, headers=_auth_header(token))
    assert (r.status_code, r.json()["code"]) == (403, "totp_missing")
    assert await _audit(db_session, "secret.replaced") == [] and await _audit(db_session, "secret.deleted") == []

    code = pyotp.TOTP(secret).now()
    r = await client.put(
        f"{url}/value", json={"value": "NEU", "current_password": OWNER_PW, "totp_code": code}, headers=_auth_header(token),
    )
    assert r.status_code == 204, r.text
    r = await client.request(
        "DELETE", url, json={"current_password": OWNER_PW, "totp_code": code}, headers=_auth_header(token),
    )
    assert r.status_code == 400 and r.json()["code"] == "totp_used", "jeder Code gilt nur einmal"


@pytest.mark.asyncio
async def test_a_secret_in_use_is_not_deleted_and_names_its_users(client, db_session, test_settings):
    from nodvard_deck.core import vault
    from nodvard_deck.models import Host, HostCredential

    token = await _bootstrap_owner(client)
    keyring = vault.load_keyring(test_settings)
    owned = await vault.create_secret(db_session, keyring, label="modul-pw", kind="generic", plaintext="x", owner_ext_id="ntfy")
    host = Host(name="pve1", address="192.0.2.10")
    db_session.add(host)
    await db_session.flush()
    for_host = await vault.create_secret(db_session, keyring, label="pve1-ssh", kind="ssh_private_key", plaintext="k")
    db_session.add(HostCredential(host_id=host.id, secret_id=for_host.id, kind="ssh_key", username="root"))
    await db_session.commit()

    for secret, kind, name in ((owned, "extension", "ntfy"), (for_host, "host", "pve1")):
        r = await client.request("DELETE", f"/api/v1/secrets/{secret.id}", json={"current_password": OWNER_PW}, headers=_auth_header(token))
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["code"] == "secret_in_use" and "Erst dort" in body["detail"]
        assert [(u["type"], u["name"]) for u in body["used_by"]] == [(kind, name)]
        # Ohne Passwort derselbe Hinweis: Die Liste kommt vor der Rückfrage.
        r = await client.delete(f"/api/v1/secrets/{secret.id}", headers=_auth_header(token))
        assert r.status_code == 409
    assert await _audit(db_session, "secret.deleted") == []


@pytest.mark.asyncio
async def test_a_recorded_use_alone_does_not_block_deleting(client, db_session):
    """Ein von Hand angelegter Eintrag, den ein Modul einmal gelesen hat, ließ sich nie mehr löschen: Die festgehaltene
    Nutzung (`secret_usages`) zählte wie eine feste Bindung und verschwindet nie von selbst."""
    from nodvard_deck.models import SecretUsage

    token = await _bootstrap_owner(client)
    secret_id = await _secret(client, token)
    db_session.add(SecretUsage(secret_id=secret_id, consumer_type="extension", consumer_id="proxmox"))
    await db_session.commit()

    # Angezeigt wird die Nutzung weiter, als Hinweis, aber nicht als feste Bindung (danach sperrt die Oberfläche).
    listed = next(s for s in (await client.get("/api/v1/secrets", headers=_auth_header(token))).json() if s["id"] == secret_id)
    assert [(u["type"], u["id"]) for u in listed["used_by"]] == [("extension", "proxmox")]
    assert listed["bound"] == []

    r = await client.request("DELETE", f"/api/v1/secrets/{secret_id}", json={"current_password": OWNER_PW}, headers=_auth_header(token))
    assert r.status_code == 204, r.text
    assert len(await _audit(db_session, "secret.deleted")) == 1


@pytest.mark.asyncio
async def test_bound_lists_only_the_fixed_bindings(client, db_session, test_settings):
    """`bound` nennt nur, was das Löschen sperrt (Besitzer-Modul, Server-Zugang); eine festgehaltene Nutzung steht nur in
    `used_by`. Liest das Besitzer-Modul seinen Eintrag, bleibt es eine Bindung (mit `last_used_at`)."""
    from nodvard_deck.core import vault
    from nodvard_deck.models import Host, HostCredential, SecretUsage

    token = await _bootstrap_owner(client)
    keyring = vault.load_keyring(test_settings)
    owned = await vault.create_secret(db_session, keyring, label="modul-pw", kind="generic", plaintext="x", owner_ext_id="ntfy")
    host = Host(name="pve1", address="192.0.2.10")
    db_session.add(host)
    await db_session.flush()
    for_host = await vault.create_secret(db_session, keyring, label="pve1-ssh", kind="ssh_private_key", plaintext="k")
    db_session.add(HostCredential(host_id=host.id, secret_id=for_host.id, kind="ssh_key", username="root"))
    db_session.add(SecretUsage(secret_id=owned.id, consumer_type="extension", consumer_id="ntfy"))
    db_session.add(SecretUsage(secret_id=for_host.id, consumer_type="extension", consumer_id="proxmox"))
    await db_session.commit()

    listed = {s["label"]: s for s in (await client.get("/api/v1/secrets", headers=_auth_header(token))).json()}
    modul, server = listed["modul-pw"], listed["pve1-ssh"]
    assert [(u["type"], u["id"]) for u in modul["bound"]] == [("extension", "ntfy")]
    assert modul["bound"][0]["last_used_at"] is not None
    assert [(u["type"], u["id"]) for u in modul["used_by"]] == [("extension", "ntfy")]
    assert [(u["type"], u["name"]) for u in server["bound"]] == [("host", "pve1")]
    assert [(u["type"], u["id"]) for u in server["used_by"]] == [("host", host.id), ("extension", "proxmox")]

    # Dieselbe Antwort nach PATCH, und genau diese Bindungen nennt das abgelehnte Löschen.
    r = await client.patch(f"/api/v1/secrets/{for_host.id}", json={"description": "neu"}, headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert r.json()["bound"] == server["bound"]
    r = await client.request("DELETE", f"/api/v1/secrets/{for_host.id}", json={"current_password": OWNER_PW}, headers=_auth_header(token))
    assert r.status_code == 409
    assert [(u["type"], u["id"]) for u in r.json()["used_by"]] == [(u["type"], u["id"]) for u in server["bound"]]


@pytest.mark.asyncio
async def test_description_and_expiry_changes_are_logged_without_password(client, db_session):
    token = await _bootstrap_owner(client)
    secret_id = await _secret(client, token)
    r = await client.patch(
        f"/api/v1/secrets/{secret_id}", json={"description": "Proxmox", "expires_at": "2027-01-01T00:00:00Z"},
        headers=_auth_header(token),
    )
    assert r.status_code == 200, r.text
    (entry,) = await _audit(db_session, "secret.updated")
    assert entry.detail == {"label": "pve-token", "fields": ["description", "expires_at"]}


_WRITE_CALLS = (
    ("PUT", "/value", {"value": "NEU", "current_password": OWNER_PW}),
    ("DELETE", "", {"current_password": OWNER_PW}),
    ("PATCH", "", {"description": "neu"}),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "suffix", "body"), _WRITE_CALLS)
async def test_changing_an_entry_needs_a_login(client, method, suffix, body):
    token = await _bootstrap_owner(client)
    secret_id = await _secret(client, token)
    r = await client.request(method, f"/api/v1/secrets/{secret_id}{suffix}", json=body)
    assert r.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "suffix", "body"), _WRITE_CALLS)
async def test_a_viewer_cannot_change_an_entry_even_with_the_own_password(client, db_session, test_settings, method, suffix, body):
    from nodvard_deck.core import vault

    token = await _bootstrap_owner(client)
    secret_id = await _secret(client, token)
    await _create_user_with_role(db_session, username="anna", password="whatever123", role="viewer")
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": "anna", "password": "whatever123"})
    viewer = login.json()["access_token"]
    own = {**body, "current_password": "whatever123"} if "current_password" in body else body
    r = await client.request(method, f"/api/v1/secrets/{secret_id}{suffix}", json=own, headers=_auth_header(viewer))
    assert r.status_code == 403
    assert await vault.read_secret_plaintext(db_session, vault.load_keyring(test_settings), secret_id) == "WERT-123"
    listed = (await client.get("/api/v1/secrets", headers=_auth_header(token))).json()
    assert [(e["id"], e["description"]) for e in listed] == [(secret_id, "")]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "suffix"), [("PUT", "/value"), ("DELETE", "")])
async def test_too_many_wrong_passwords_lock_replace_and_delete(client, db_session, test_settings, method, suffix):
    from nodvard_deck.core import login_limit, vault

    token = await _bootstrap_owner(client)
    secret_id = await _secret(client, token)
    url = f"/api/v1/secrets/{secret_id}{suffix}"
    for _ in range(login_limit.MAX_FAILURES_PER_USER):
        r = await client.request(method, url, json={"value": "NEU", "current_password": "falsch"}, headers=_auth_header(token))
        assert r.status_code == 400, r.text
    r = await client.request(method, url, json={"value": "NEU", "current_password": OWNER_PW}, headers=_auth_header(token))
    assert r.status_code == 429 and "Retry-After" in r.headers, "auch das richtige Passwort hilft erst nach der Pause"
    assert await vault.read_secret_plaintext(db_session, vault.load_keyring(test_settings), secret_id) == "WERT-123"


@pytest.mark.asyncio
async def test_patch_needs_no_password_but_the_write_permission(client):
    """Beschreibung und Ablauf sind keine Zugangsdaten: dafür reicht `secrets.write`, wie bisher."""
    token = await _bootstrap_owner(client)
    secret_id = await _secret(client, token)
    r = await client.patch(f"/api/v1/secrets/{secret_id}", json={"description": "Proxmox"}, headers=_auth_header(token))
    assert r.status_code == 200 and r.json()["description"] == "Proxmox"
