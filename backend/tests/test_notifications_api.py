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


async def _role_header(client, db_session, role: str) -> dict:
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=f"u-{role}", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": user.username, "password": "whatever123"})
    assert login.status_code == 200, login.text
    return _auth_header(login.json()["access_token"])


@pytest.mark.asyncio
async def test_viewer_can_read_but_not_mark_as_read(client, db_session):
    """Der Lesestatus gilt fuer alle: wer nur lesen darf, blendet keine Meldung fuer andere aus."""
    owner = await _bootstrap_owner(client)
    note = await notifications_service.send(db_session, title="Neue Login-IP", body="x", severity="critical")
    viewer = await _role_header(client, db_session, "viewer")

    assert (await client.get("/api/v1/notifications", headers=viewer)).status_code == 200
    assert (await client.get("/api/v1/notifications/unread-count", headers=viewer)).json() == {"unread": 1}

    all_ = await client.post("/api/v1/notifications/read-all", headers=viewer)
    assert all_.status_code == 403
    assert "notifications.write" in all_.json()["detail"]
    one = await client.post("/api/v1/notifications/read", json={"ids": [note.id]}, headers=viewer)
    assert one.status_code == 403

    # Beim Owner bleibt die Meldung ungelesen.
    assert (await client.get("/api/v1/notifications/unread-count", headers=_auth_header(owner))).json() == {"unread": 1}


@pytest.mark.asyncio
async def test_operator_can_mark_as_read(client, db_session):
    note = await notifications_service.send(db_session, title="A", body="x")
    operator = await _role_header(client, db_session, "operator")

    assert (await client.post("/api/v1/notifications/read", json={"ids": [note.id]}, headers=operator)).json() == {"marked": 1}
    assert (await client.post("/api/v1/notifications/read-all", headers=operator)).json() == {"marked": 0}
