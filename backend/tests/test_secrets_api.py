"""GET/POST/PUT/DELETE /secrets -- docs/04-API.md §3.

Zentrale Zusicherung ueber alle Tests hinweg: KEIN Response-Body enthaelt jemals den
Klartextwert oder `ciphertext` (docs/03-DATA-MODEL.md §3, Invariante 1).
"""

from __future__ import annotations

import pytest


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
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
        json={"value": "NEUER-GEHEIMER-WERT"},
        headers=_auth_header(token),
    )
    assert replaced.status_code == 204
    assert "NEUER-GEHEIMER-WERT" not in replaced.text

    deleted = await client.delete(f"/api/v1/secrets/{body['id']}", headers=_auth_header(token))
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
        json={"value": "x"},
        headers=_auth_header(token),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_unknown_secret_is_idempotent(client):
    token = await _bootstrap_owner(client)
    r = await client.delete("/api/v1/secrets/does-not-exist", headers=_auth_header(token))
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
