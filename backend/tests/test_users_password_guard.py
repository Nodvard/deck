"""PATCH /users/{id}: Passwoerter setzen -- aber nie das eigene (-> /me/password mit Altpasswort) und
nie das des Inhabers durch einen anderen."""

from __future__ import annotations

import pytest
from nodvard_deck.core import security
from nodvard_deck.models import User
from sqlalchemy import select

PASSWORD = "correct-horse-battery"


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _login(client, username):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"], r.json()["user"]["id"]


async def _world(client):
    await client.post(
        "/api/v1/auth/bootstrap", json={"username": "chef", "password": PASSWORD, "setup_code": "TEST-CODE-2345"}
    )
    owner, owner_id = await _login(client, "chef")
    roles = {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=_h(owner))).json()}
    for name, role in (("admin1", "admin"), ("viewer1", "viewer")):
        await client.post(
            "/api/v1/users", json={"username": name, "password": PASSWORD, "role_ids": [roles[role]]}, headers=_h(owner)
        )
    return owner, owner_id


async def _password_ok(db_session, username, password) -> bool:
    user = (await db_session.execute(select(User).where(User.username == username))).scalar_one()
    return security.verify_password(password, user.password_hash)


@pytest.mark.asyncio
async def test_admin_cannot_set_own_password_via_patch(client, db_session):
    await _world(client)
    admin, admin_id = await _login(client, "admin1")
    r = await client.patch(f"/api/v1/users/{admin_id}", json={"password": "neues-passwort-99"}, headers=_h(admin))
    assert r.status_code == 409
    assert "Mein Konto" in r.json()["detail"]
    assert await _password_ok(db_session, "admin1", PASSWORD)


@pytest.mark.asyncio
async def test_admin_cannot_set_the_owners_password(client, db_session):
    _, owner_id = await _world(client)
    admin, _ = await _login(client, "admin1")
    r = await client.patch(f"/api/v1/users/{owner_id}", json={"password": "neues-passwort-99"}, headers=_h(admin))
    assert r.status_code == 403
    assert "Inhaber" in r.json()["detail"]
    assert await _password_ok(db_session, "chef", PASSWORD)


@pytest.mark.asyncio
async def test_other_fields_of_owner_and_self_can_still_be_patched(client):
    _, owner_id = await _world(client)
    admin, admin_id = await _login(client, "admin1")
    assert (await client.patch(f"/api/v1/users/{admin_id}", json={"display_name": "Ich"}, headers=_h(admin))).status_code == 200
    assert (await client.patch(f"/api/v1/users/{owner_id}", json={"display_name": "Chef"}, headers=_h(admin))).status_code == 200


@pytest.mark.asyncio
async def test_admin_can_still_set_passwords_of_other_normal_users(client, db_session):
    await _world(client)
    admin, _ = await _login(client, "admin1")
    viewer_id = (await _login(client, "viewer1"))[1]
    r = await client.patch(f"/api/v1/users/{viewer_id}", json={"password": "neues-passwort-99"}, headers=_h(admin))
    assert r.status_code == 200
    assert await _password_ok(db_session, "viewer1", "neues-passwort-99")


@pytest.mark.asyncio
async def test_no_way_around_the_current_password_check_for_2fa(client):
    """Die urspruengliche Umgehung: Passwort per PATCH setzen, dann 2FA mit diesem Passwort abschalten."""
    await _world(client)
    admin, admin_id = await _login(client, "admin1")
    await client.patch(f"/api/v1/users/{admin_id}", json={"password": "neues-passwort-99"}, headers=_h(admin))
    r = await client.request("DELETE", "/api/v1/me/totp", json={"current_password": "neues-passwort-99"}, headers=_h(admin))
    assert r.status_code == 400
