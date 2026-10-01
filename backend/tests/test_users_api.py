"""GET/POST/PATCH/DELETE /users, GET /roles (`POST /auth/bootstrap` deckte
anfangs nur den allerersten Nutzer ab, es gab
keinen Weg, weitere Accounts anzulegen/zu verwalten). Siehe
backend/src/nodvard_deck/api/v1/users.py fuer die volle Begruendung, insbesondere den
Owner-Schutz (weder loeschbar noch deaktivierbar noch kann er sich selbst
entziehen) und die D-12-sichere Rollen-Zuweisung."""

from __future__ import annotations

import pytest


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"], login.json()["user"]["id"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(client):
    assert (await client.get("/api/v1/users")).status_code == 401


@pytest.mark.asyncio
async def test_bootstrap_already_creates_the_builtin_roles(client):
    """`bootstrap_owner()` ruft intern `ensure_builtin_roles()` -- `GET /roles` muss
    admin/operator/viewer deshalb schon nach dem allerersten Login zeigen, ohne
    dass irgendwer sie manuell anlegen musste."""
    token, _ = await _bootstrap_owner(client)
    roles = await client.get("/api/v1/roles", headers=_auth_header(token))
    assert roles.status_code == 200, roles.text
    names = {r["name"] for r in roles.json()}
    assert names == {"admin", "operator", "viewer"}
    assert all(r["is_builtin"] for r in roles.json())


@pytest.mark.asyncio
async def test_owner_sees_itself_in_the_user_list(client):
    token, owner_id = await _bootstrap_owner(client)
    listed = await client.get("/api/v1/users", headers=_auth_header(token))
    assert listed.status_code == 200
    assert [u["id"] for u in listed.json()] == [owner_id]
    assert listed.json()[0]["is_owner"] is True
    assert listed.json()[0]["roles"] == []


@pytest.mark.asyncio
async def test_create_user_with_a_role_round_trip(client):
    token, _ = await _bootstrap_owner(client)
    roles = (await client.get("/api/v1/roles", headers=_auth_header(token))).json()
    operator_id = next(r["id"] for r in roles if r["name"] == "operator")

    created = await client.post(
        "/api/v1/users",
        json={"username": "nico", "password": "correct-horse-battery", "display_name": "Nico", "role_ids": [operator_id]},
        headers=_auth_header(token),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["username"] == "nico"
    assert body["display_name"] == "Nico"
    assert body["is_owner"] is False
    assert body["is_active"] is True
    assert [r["name"] for r in body["roles"]] == ["operator"]

    fetched = await client.get(f"/api/v1/users/{body['id']}", headers=_auth_header(token))
    assert fetched.status_code == 200
    assert fetched.json()["username"] == "nico"

    # Das neue Passwort funktioniert wirklich -- kein Blindflug, dass der Hash
    # tatsaechlich passt.
    login = await client.post("/api/v1/auth/login", json={"username": "nico", "password": "correct-horse-battery"})
    assert login.status_code == 200, login.text


@pytest.mark.asyncio
async def test_duplicate_username_is_rejected(client):
    token, _ = await _bootstrap_owner(client)
    first = await client.post(
        "/api/v1/users", json={"username": "dup", "password": "correct-horse-battery"}, headers=_auth_header(token)
    )
    assert first.status_code == 201
    second = await client.post(
        "/api/v1/users", json={"username": "dup", "password": "correct-horse-battery"}, headers=_auth_header(token)
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_create_user_with_unknown_role_returns_404(client):
    token, _ = await _bootstrap_owner(client)
    res = await client.post(
        "/api/v1/users",
        json={"username": "usr-x", "password": "correct-horse-battery", "role_ids": ["does-not-exist"]},
        headers=_auth_header(token),
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_update_user_display_name_email_active_and_roles(client):
    token, _ = await _bootstrap_owner(client)
    roles = (await client.get("/api/v1/roles", headers=_auth_header(token))).json()
    viewer_id = next(r["id"] for r in roles if r["name"] == "viewer")
    operator_id = next(r["id"] for r in roles if r["name"] == "operator")

    created = await client.post(
        "/api/v1/users",
        json={"username": "usr1", "password": "correct-horse-battery", "role_ids": [viewer_id]},
        headers=_auth_header(token),
    )
    user_id = created.json()["id"]

    updated = await client.patch(
        f"/api/v1/users/{user_id}",
        json={"display_name": "Neuer Name", "email": "u1@example.com", "is_active": False, "role_ids": [operator_id]},
        headers=_auth_header(token),
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["display_name"] == "Neuer Name"
    assert body["email"] == "u1@example.com"
    assert body["is_active"] is False
    assert [r["name"] for r in body["roles"]] == ["operator"]


@pytest.mark.asyncio
async def test_update_role_ids_empty_list_clears_all_roles_but_omitting_it_leaves_roles_unchanged(client):
    token, _ = await _bootstrap_owner(client)
    roles = (await client.get("/api/v1/roles", headers=_auth_header(token))).json()
    viewer_id = next(r["id"] for r in roles if r["name"] == "viewer")

    created = await client.post(
        "/api/v1/users",
        json={"username": "usr2", "password": "correct-horse-battery", "role_ids": [viewer_id]},
        headers=_auth_header(token),
    )
    user_id = created.json()["id"]

    unchanged = await client.patch(
        f"/api/v1/users/{user_id}", json={"display_name": "still has viewer"}, headers=_auth_header(token)
    )
    assert [r["name"] for r in unchanged.json()["roles"]] == ["viewer"]

    cleared = await client.patch(f"/api/v1/users/{user_id}", json={"role_ids": []}, headers=_auth_header(token))
    assert cleared.json()["roles"] == []


@pytest.mark.asyncio
async def test_update_user_password_actually_changes_login(client):
    token, _ = await _bootstrap_owner(client)
    created = await client.post(
        "/api/v1/users", json={"username": "usr3", "password": "correct-horse-battery"}, headers=_auth_header(token)
    )
    user_id = created.json()["id"]

    await client.patch(f"/api/v1/users/{user_id}", json={"password": "new-correct-horse-battery"}, headers=_auth_header(token))

    old_login = await client.post("/api/v1/auth/login", json={"username": "usr3", "password": "correct-horse-battery"})
    assert old_login.status_code == 401
    new_login = await client.post("/api/v1/auth/login", json={"username": "usr3", "password": "new-correct-horse-battery"})
    assert new_login.status_code == 200


@pytest.mark.asyncio
async def test_delete_user_round_trip(client):
    token, _ = await _bootstrap_owner(client)
    created = await client.post(
        "/api/v1/users", json={"username": "temp", "password": "correct-horse-battery"}, headers=_auth_header(token)
    )
    user_id = created.json()["id"]

    deleted = await client.delete(f"/api/v1/users/{user_id}", headers=_auth_header(token))
    assert deleted.status_code == 204

    missing = await client.get(f"/api/v1/users/{user_id}", headers=_auth_header(token))
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_owner_account_cannot_be_deleted_or_deactivated(client):
    token, owner_id = await _bootstrap_owner(client)

    deleted = await client.delete(f"/api/v1/users/{owner_id}", headers=_auth_header(token))
    assert deleted.status_code == 409

    deactivated = await client.patch(f"/api/v1/users/{owner_id}", json={"is_active": False}, headers=_auth_header(token))
    assert deactivated.status_code == 409


@pytest.mark.asyncio
async def test_admin_cannot_delete_their_own_account(client):
    """Verhindert versehentliches Selbst-Aussperren mitten in einer Session --
    unabhaengig vom Owner-Schutz, der nur `is_owner=True` betrifft."""
    owner_token, _ = await _bootstrap_owner(client)
    roles = (await client.get("/api/v1/roles", headers=_auth_header(owner_token))).json()
    admin_id = next(r["id"] for r in roles if r["name"] == "admin")

    created = await client.post(
        "/api/v1/users",
        json={"username": "admin2", "password": "correct-horse-battery", "role_ids": [admin_id]},
        headers=_auth_header(owner_token),
    )
    admin2_id = created.json()["id"]

    login = await client.post("/api/v1/auth/login", json={"username": "admin2", "password": "correct-horse-battery"})
    admin2_token = login.json()["access_token"]

    self_delete = await client.delete(f"/api/v1/users/{admin2_id}", headers=_auth_header(admin2_token))
    assert self_delete.status_code == 409

    # Ein ANDERER Admin darf admin2 aber sehr wohl loeschen.
    other_delete = await client.delete(f"/api/v1/users/{admin2_id}", headers=_auth_header(owner_token))
    assert other_delete.status_code == 204


@pytest.mark.asyncio
async def test_reads_and_writes_require_their_respective_permission(client, db_session):
    """Derselbe read_router/write_router-Anspruch wie bei den Extensions -- hier
    ueber zwei separate `require_permission()`-Dependencies auf einzelnen Routen
    (Kern-Router kennt keine Ein-Permission-pro-Router-Einschraenkung, die gilt
    nur fuer `ctx.api.include_router()`)."""
    from nodvard_deck.core import security
    from nodvard_deck.db.base import refresh_relationships
    from nodvard_deck.models import Role, RolePermission, User

    owner_token, _ = await _bootstrap_owner(client)

    role = Role(name="users-reader-test", is_builtin=False, description="Testrolle")
    db_session.add(role)
    await db_session.flush()
    db_session.add(RolePermission(role_id=role.id, permission="users.read"))
    await db_session.flush()
    await refresh_relationships(db_session, role, "permissions")

    reader = User(username="reader", password_hash=security.hash_password("correct-horse-battery"), is_active=True)
    reader.roles.append(role)
    db_session.add(reader)
    await db_session.flush()
    await db_session.commit()

    login = await client.post("/api/v1/auth/login", json={"username": "reader", "password": "correct-horse-battery"})
    assert login.status_code == 200, login.text
    reader_headers = _auth_header(login.json()["access_token"])

    read_ok = await client.get("/api/v1/users", headers=reader_headers)
    assert read_ok.status_code == 200, read_ok.text

    write_forbidden = await client.post(
        "/api/v1/users", json={"username": "usr-y", "password": "correct-horse-battery"}, headers=reader_headers
    )
    assert write_forbidden.status_code == 403


@pytest.mark.asyncio
async def test_admin_password_reset_signs_out_all_sessions_of_that_user(client):
    """Setzt ein Admin ein neues Passwort, gelten die alten Anmeldungen
    dieses Nutzers nicht mehr weiter (Refresh-Tokens widerrufen)."""
    token, _ = await _bootstrap_owner(client)
    user_id = (
        await client.post(
            "/api/v1/users", json={"username": "usr4", "password": "correct-horse-battery"}, headers=_auth_header(token)
        )
    ).json()["id"]
    session_a = await client.post(
        "/api/v1/auth/login",
        json={"username": "usr4", "password": "correct-horse-battery", "client_type": "android"},
    )
    refresh_a = session_a.json()["refresh_token"]

    r = await client.patch(
        f"/api/v1/users/{user_id}", json={"password": "new-correct-horse-battery"}, headers=_auth_header(token)
    )
    assert r.status_code == 200, r.text
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_a})).status_code == 401


@pytest.mark.asyncio
async def test_own_password_cannot_be_set_via_patch(client):
    """Das eigene Passwort gibt es nur ueber `POST /me/password` (mit dem alten Passwort,
    gedrosselt) -- sonst waere das Altpasswort-Erfordernis dort mit users.write umgehbar."""
    token, owner_id = await _bootstrap_owner(client)
    r = await client.patch(
        f"/api/v1/users/{owner_id}", json={"password": "new-correct-horse-battery"}, headers=_auth_header(token)
    )
    assert r.status_code == 409, r.text
    assert "Mein Konto" in r.json()["detail"]
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    assert login.status_code == 200
