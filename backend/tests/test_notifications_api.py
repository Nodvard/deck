"""GET /notifications, POST /notifications/read -- docs/04-API.md."""

from __future__ import annotations

import pytest

from nodvard_deck.services import notifications as notifications_service


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(client):
    assert (await client.get("/api/v1/notifications")).status_code == 401


@pytest.mark.asyncio
async def test_list_get_and_mark_read(client, db_session):
    token = await _bootstrap_owner(client)
    a = await notifications_service.send(db_session, title="A", body="x")
    b = await notifications_service.send(db_session, title="B", body="y", severity="critical")

    listed = await client.get("/api/v1/notifications", headers=_auth_header(token))
    assert listed.status_code == 200
    assert {n["id"] for n in listed.json()} == {a.id, b.id}

    unread_only = await client.get("/api/v1/notifications", params={"unread": True}, headers=_auth_header(token))
    assert {n["id"] for n in unread_only.json()} == {a.id, b.id}

    got = await client.get(f"/api/v1/notifications/{a.id}", headers=_auth_header(token))
    assert got.status_code == 200
    assert got.json()["severity"] == "info"

    missing = await client.get("/api/v1/notifications/does-not-exist", headers=_auth_header(token))
    assert missing.status_code == 404

    marked = await client.post("/api/v1/notifications/read", json={"ids": [a.id]}, headers=_auth_header(token))
    assert marked.status_code == 200
    assert marked.json() == {"marked": 1}

    still_unread = await client.get("/api/v1/notifications", params={"unread": True}, headers=_auth_header(token))
    assert {n["id"] for n in still_unread.json()} == {b.id}


@pytest.mark.asyncio
async def test_viewer_can_read_notifications(client, db_session):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles["viewer"])
    db_session.add(user)
    await db_session.flush()

    login = await client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"})
    token = login.json()["access_token"]

    r = await client.get("/api/v1/notifications", headers=_auth_header(token))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_unread_count_and_mark_all_read(client, db_session):
    """Benachrichtigungs-Center: Zaehler fuer das Menue und "Alle gelesen"."""
    token = await _bootstrap_owner(client)
    for i in range(3):
        await notifications_service.send(db_session, title=f"N{i}", body="x")

    count = await client.get("/api/v1/notifications/unread-count", headers=_auth_header(token))
    assert count.status_code == 200, count.text
    assert count.json() == {"unread": 3}

    marked = await client.post("/api/v1/notifications/read-all", headers=_auth_header(token))
    assert marked.json() == {"marked": 3}
    assert (await client.get("/api/v1/notifications/unread-count", headers=_auth_header(token))).json() == {"unread": 0}
    assert (await client.post("/api/v1/notifications/read-all", headers=_auth_header(token))).json() == {"marked": 0}
    assert (await client.get("/api/v1/notifications/unread-count")).status_code == 401
