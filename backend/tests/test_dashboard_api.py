"""GET /dashboard/layouts, GET/PUT /dashboard/layouts/{id} -- docs/04-API.md."""

from __future__ import annotations

import pytest


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(client):
    assert (await client.get("/api/v1/dashboard/layouts")).status_code == 401


@pytest.mark.asyncio
async def test_first_call_auto_creates_default_layout(client):
    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/dashboard/layouts", headers=_auth_header(token))
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["is_default"] is True
    assert body[0]["items"] == []


@pytest.mark.asyncio
async def test_get_and_put_layout_roundtrip(client):
    token = await _bootstrap_owner(client)
    listed = await client.get("/api/v1/dashboard/layouts", headers=_auth_header(token))
    layout_id = listed.json()[0]["id"]

    items = [{"widget_id": "hello", "ext_id": "hello-world", "x": 0, "y": 0, "w": 2, "h": 1, "config": {}}]
    put = await client.put(
        f"/api/v1/dashboard/layouts/{layout_id}",
        json={"name": "Mein Dashboard", "items": items},
        headers=_auth_header(token),
    )
    assert put.status_code == 200, put.text
    assert put.json()["name"] == "Mein Dashboard"
    assert put.json()["items"] == items

    got = await client.get(f"/api/v1/dashboard/layouts/{layout_id}", headers=_auth_header(token))
    assert got.json()["items"] == items


@pytest.mark.asyncio
async def test_layout_is_scoped_to_the_owning_user(client, db_session):
    from nodvard_deck.core import security
    from nodvard_deck.models import User

    owner_token = await _bootstrap_owner(client)
    listed = await client.get("/api/v1/dashboard/layouts", headers=_auth_header(owner_token))
    layout_id = listed.json()[0]["id"]

    db_session.add(User(username="other", password_hash=security.hash_password("whatever123"), is_active=True))
    await db_session.flush()
    other_login = await client.post("/api/v1/auth/login", json={"username": "other", "password": "whatever123"})
    other_token = other_login.json()["access_token"]

    forbidden = await client.get(f"/api/v1/dashboard/layouts/{layout_id}", headers=_auth_header(other_token))
    assert forbidden.status_code == 404

    forbidden_put = await client.put(
        f"/api/v1/dashboard/layouts/{layout_id}", json={"name": "Uebernommen"}, headers=_auth_header(other_token)
    )
    assert forbidden_put.status_code == 404


@pytest.mark.asyncio
async def test_get_unknown_layout_is_404(client):
    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/dashboard/layouts/does-not-exist", headers=_auth_header(token))
    assert r.status_code == 404
