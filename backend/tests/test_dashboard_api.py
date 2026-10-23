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


async def _default_layout_id(client, token: str) -> str:
    return (await client.get("/api/v1/dashboard/layouts", headers=_auth_header(token))).json()[0]["id"]


@pytest.mark.asyncio
async def test_groups_are_empty_until_saved_and_then_kept_in_order(client):
    token = await _bootstrap_owner(client)
    layout_id = await _default_layout_id(client, token)
    assert (await client.get(f"/api/v1/dashboard/layouts/{layout_id}", headers=_auth_header(token))).json()["groups"] == []

    groups = [{"key": "backups", "title": " Sicherungen ", "collapsed": True}, {"key": "servers", "title": "", "collapsed": False}]
    put = await client.put(f"/api/v1/dashboard/layouts/{layout_id}", json={"groups": groups}, headers=_auth_header(token))
    assert put.status_code == 200, put.text
    assert put.json()["groups"] == [
        {"key": "backups", "title": "Sicherungen", "collapsed": True},
        {"key": "servers", "title": None, "collapsed": False},
    ]
    # Ein PUT nur mit Gruppen lässt die Widgets, wie sie sind, und umgekehrt.
    items = [{"widget_id": "hello", "ext_id": "hello-world", "x": 0, "y": 0, "w": 2, "h": 1, "config": {"group": "apps"}}]
    await client.put(f"/api/v1/dashboard/layouts/{layout_id}", json={"items": items}, headers=_auth_header(token))
    got = (await client.get(f"/api/v1/dashboard/layouts/{layout_id}", headers=_auth_header(token))).json()
    assert got["items"] == items and [g["key"] for g in got["groups"]] == ["backups", "servers"]


@pytest.mark.asyncio
async def test_a_put_of_only_groups_keeps_the_widgets_and_an_empty_list_resets_them(client):
    token = await _bootstrap_owner(client)
    layout_id = await _default_layout_id(client, token)
    url, headers = f"/api/v1/dashboard/layouts/{layout_id}", _auth_header(token)
    items = [{"widget_id": "hello", "ext_id": "hello-world", "x": 4, "y": 0, "w": 4, "h": 2, "config": {}}]
    await client.put(url, json={"items": items}, headers=headers)

    put = await client.put(url, json={"groups": [{"key": "apps", "collapsed": True}]}, headers=headers)
    assert put.json()["items"] == items
    assert (await client.put(url, json={"groups": None}, headers=headers)).json()["groups"][0]["key"] == "apps"
    assert (await client.put(url, json={"groups": []}, headers=headers)).json()["groups"] == []
    assert (await client.get(url, headers=headers)).json()["items"] == items


@pytest.mark.asyncio
@pytest.mark.parametrize("groups", [
    [{"key": "garage"}],
    [{"key": "apps"}, {"key": "apps"}],
    [{"key": "apps", "title": "x" * 61}],
])
async def test_invalid_groups_are_refused(client, groups):
    token = await _bootstrap_owner(client)
    layout_id = await _default_layout_id(client, token)
    put = await client.put(f"/api/v1/dashboard/layouts/{layout_id}", json={"groups": groups}, headers=_auth_header(token))
    assert put.status_code == 422


@pytest.mark.asyncio
async def test_a_group_saved_by_a_newer_version_is_left_out_but_kept(client, db_session):
    from nodvard_deck.models import DashboardLayout

    token = await _bootstrap_owner(client)
    layout_id = await _default_layout_id(client, token)
    row = await db_session.get(DashboardLayout, layout_id)
    row.groups = [{"key": "garage", "collapsed": True}, {"key": "apps", "collapsed": True}]
    await db_session.commit()

    got = (await client.get(f"/api/v1/dashboard/layouts/{layout_id}", headers=_auth_header(token))).json()
    assert got["groups"] == [{"key": "apps", "title": None, "collapsed": True}]
    await db_session.refresh(row)
    assert row.groups[0]["key"] == "garage"
