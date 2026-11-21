"""Ein nach der Gnadenfrist erneut benutzter Refresh-Token beendet die ganze Kette."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from nodvard_deck.core import security
from nodvard_deck.models import AuditEntry, RefreshToken
from nodvard_deck.services import auth as auth_service

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "neues-passwort-1"
COOKIE = "nodvard_deck_refresh"


async def _bootstrap(client) -> None:
    r = await client.post(
        "/api/v1/auth/bootstrap", json={"username": "nico", "password": PASSWORD, "setup_code": "TEST-CODE-2345"}
    )
    assert r.status_code == 201, r.text


async def _app_login(client) -> dict:
    r = await client.post(
        "/api/v1/auth/login", json={"username": "nico", "password": PASSWORD, "client_type": "android"}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _refresh(client, token: str):
    return await client.post("/api/v1/auth/refresh", json={"refresh_token": token})


async def _me(client, access: str) -> int:
    return (await client.get("/api/v1/me", headers={"Authorization": f"Bearer {access}"})).status_code


async def _age(db_session, raw: str, seconds: int = 31) -> None:
    row = (
        await db_session.execute(select(RefreshToken).where(RefreshToken.token_hash == security.hash_opaque_token(raw)))
    ).scalar_one()
    row.revoked_at = row.revoked_at - timedelta(seconds=seconds)
    await db_session.flush()


async def _reuse_entries(db_session) -> list[AuditEntry]:
    return list(
        (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "auth.refresh_reused"))).scalars()
    )


@pytest.mark.asyncio
async def test_reuse_after_grace_ends_the_continued_chain(client, db_session):
    await _bootstrap(client)
    first = await _app_login(client)
    second = (await _refresh(client, first["refresh_token"])).json()
    third = (await _refresh(client, second["refresh_token"])).json()
    await _age(db_session, first["refresh_token"])

    assert (await _refresh(client, first["refresh_token"])).status_code == 401

    assert (await _refresh(client, third["refresh_token"])).status_code == 401
    assert await _me(client, third["access_token"]) == 401
    [entry] = await _reuse_entries(db_session)
    assert entry.detail == {"signed_out_sessions": 1}


@pytest.mark.asyncio
async def test_reuse_within_grace_keeps_the_chain(client, db_session):
    await _bootstrap(client)
    first = await _app_login(client)
    second = (await _refresh(client, first["refresh_token"])).json()
    await _age(db_session, first["refresh_token"], seconds=5)

    assert (await _refresh(client, first["refresh_token"])).status_code == 200

    assert (await _refresh(client, second["refresh_token"])).status_code == 200
    assert await _reuse_entries(db_session) == []


@pytest.mark.asyncio
async def test_reuse_leaves_other_logins_alone(client, db_session):
    await _bootstrap(client)
    other = await _app_login(client)
    first = await _app_login(client)
    await _refresh(client, first["refresh_token"])
    await _age(db_session, first["refresh_token"])

    assert (await _refresh(client, first["refresh_token"])).status_code == 401

    assert (await _refresh(client, other["refresh_token"])).status_code == 200


@pytest.mark.asyncio
async def test_reuse_of_a_logged_out_token_writes_nothing(client, db_session):
    await _bootstrap(client)
    first = await _app_login(client)
    second = (await _refresh(client, first["refresh_token"])).json()
    assert (await client.post("/api/v1/auth/logout", json={"refresh_token": second["refresh_token"]})).status_code == 204
    await _age(db_session, first["refresh_token"])

    assert (await _refresh(client, first["refresh_token"])).status_code == 401
    assert await _reuse_entries(db_session) == []


@pytest.mark.asyncio
async def test_reuse_beyond_the_chain_limit_ends_every_login(client, db_session, monkeypatch):
    await _bootstrap(client)
    other = await _app_login(client)
    first = await _app_login(client)
    second = (await _refresh(client, first["refresh_token"])).json()
    third = (await _refresh(client, second["refresh_token"])).json()
    await _age(db_session, first["refresh_token"])
    monkeypatch.setattr(auth_service, "SESSION_CHAIN_LIMIT", 0)

    assert (await _refresh(client, first["refresh_token"])).status_code == 401

    assert (await _refresh(client, third["refresh_token"])).status_code == 401
    assert (await _refresh(client, other["refresh_token"])).status_code == 401


@pytest.mark.asyncio
async def test_reuse_while_the_end_renews_follows_to_the_new_end(client, db_session, test_settings, monkeypatch):
    await _bootstrap(client)
    first = await _app_login(client)
    second = (await _refresh(client, first["refresh_token"])).json()
    await _age(db_session, first["refresh_token"])
    real = auth_service.session_chain_end
    late: dict = {}

    async def renew_in_between(session, user_id, token_id, *, max_hops):
        end = await real(session, user_id, token_id, max_hops=max_hops)
        if not late:
            late["tokens"] = await auth_service._redeem_refresh_token(
                session, test_settings, raw_refresh_token=second["refresh_token"]
            )
        return end

    monkeypatch.setattr(auth_service, "session_chain_end", renew_in_between)

    assert (await _refresh(client, first["refresh_token"])).status_code == 401
    monkeypatch.setattr(auth_service, "session_chain_end", real)

    assert (await _refresh(client, late["tokens"].refresh_token)).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["password", "totp"])
async def test_copied_cookie_turned_further_ends_with_the_owners_change(client, db_session, route):
    await _bootstrap(client)
    login = await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})
    owner_access = login.json()["access_token"]
    owner_cookie = login.cookies[COOKIE]
    copied = await client.post("/api/v1/auth/refresh", json={})
    assert copied.status_code == 200
    thief_cookie = copied.cookies[COOKIE]
    thief_access = copied.json()["access_token"]
    await _age(db_session, owner_cookie)

    headers = {"Authorization": f"Bearer {owner_access}"}
    if route == "password":
        r = await client.post(
            "/api/v1/me/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}, headers=headers
        )
    else:
        r = await client.request("DELETE", "/api/v1/me/totp", json={"current_password": PASSWORD}, headers=headers)
    assert r.status_code == 204, r.text

    client.cookies.set(COOKIE, owner_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401

    client.cookies.set(COOKIE, thief_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401
    assert await _me(client, thief_access) == 401


@pytest.mark.asyncio
async def test_the_ended_chain_survives_the_refused_request(client, file_db):
    await _bootstrap(client)
    first = await _app_login(client)
    second = (await _refresh(client, first["refresh_token"])).json()
    async with file_db() as session:
        await _age(session, first["refresh_token"])
        await session.commit()

    assert (await _refresh(client, first["refresh_token"])).status_code == 401

    async with file_db() as session:
        live = (await session.execute(select(RefreshToken).where(RefreshToken.revoked_at.is_(None)))).scalars().all()
        entries = await _reuse_entries(session)
    assert live == []
    assert len(entries) == 1
    assert (await _refresh(client, second["refresh_token"])).status_code == 401


async def _expire(db_session, raw: str) -> None:
    row = (
        await db_session.execute(select(RefreshToken).where(RefreshToken.token_hash == security.hash_opaque_token(raw)))
    ).scalar_one()
    row.expires_at = row.revoked_at - timedelta(seconds=1)
    await db_session.flush()


@pytest.mark.asyncio
async def test_late_tab_within_grace_keeps_a_chain_turned_twice(client, db_session):
    await _bootstrap(client)
    first = await _app_login(client)
    second = (await _refresh(client, first["refresh_token"])).json()
    third = (await _refresh(client, second["refresh_token"])).json()
    await _age(db_session, first["refresh_token"], seconds=5)

    assert (await _refresh(client, first["refresh_token"])).status_code == 401

    assert await _me(client, third["access_token"]) == 200
    assert (await _refresh(client, third["refresh_token"])).status_code == 200
    assert await _reuse_entries(db_session) == []


@pytest.mark.asyncio
async def test_reuse_of_an_expired_rotated_token_ends_the_chain(client, db_session):
    await _bootstrap(client)
    first = await _app_login(client)
    second = (await _refresh(client, first["refresh_token"])).json()
    await _age(db_session, first["refresh_token"])
    await _expire(db_session, first["refresh_token"])

    assert (await _refresh(client, first["refresh_token"])).status_code == 401

    assert (await _refresh(client, second["refresh_token"])).status_code == 401
    assert await _me(client, second["access_token"]) == 401
    [entry] = await _reuse_entries(db_session)
    assert entry.detail == {"signed_out_sessions": 1}


@pytest.mark.asyncio
async def test_expired_token_within_grace_keeps_the_chain(client, db_session):
    await _bootstrap(client)
    first = await _app_login(client)
    second = (await _refresh(client, first["refresh_token"])).json()
    await _age(db_session, first["refresh_token"], seconds=5)
    await _expire(db_session, first["refresh_token"])

    assert (await _refresh(client, first["refresh_token"])).status_code == 401

    assert (await _refresh(client, second["refresh_token"])).status_code == 200
    assert await _reuse_entries(db_session) == []


@pytest.mark.asyncio
async def test_expired_token_never_renewed_writes_nothing(client, db_session):
    await _bootstrap(client)
    first = await _app_login(client)
    other = await _app_login(client)
    row = (
        await db_session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == security.hash_opaque_token(first["refresh_token"]))
        )
    ).scalar_one()
    row.expires_at = row.created_at - timedelta(seconds=1)
    await db_session.flush()

    assert (await _refresh(client, first["refresh_token"])).status_code == 401

    assert (await _refresh(client, other["refresh_token"])).status_code == 200
    assert await _reuse_entries(db_session) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["password", "totp"])
async def test_cookie_turned_twice_ends_with_the_owners_change_within_grace(client, db_session, route):
    await _bootstrap(client)
    login = await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})
    owner_access = login.json()["access_token"]
    owner_cookie = login.cookies[COOKIE]
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 200
    copied = await client.post("/api/v1/auth/refresh", json={})
    assert copied.status_code == 200
    thief_cookie = copied.cookies[COOKIE]
    thief_access = copied.json()["access_token"]

    headers = {"Authorization": f"Bearer {owner_access}"}
    if route == "password":
        r = await client.post(
            "/api/v1/me/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}, headers=headers
        )
    else:
        r = await client.request("DELETE", "/api/v1/me/totp", json={"current_password": PASSWORD}, headers=headers)
    assert r.status_code == 204, r.text

    client.cookies.set(COOKIE, owner_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401

    client.cookies.set(COOKIE, thief_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401
    assert await _me(client, thief_access) == 401
    [entry] = await _reuse_entries(db_session)
    assert entry.detail == {"signed_out_sessions": 1}


@pytest.mark.asyncio
async def test_password_change_leaves_the_grace_of_other_accounts(client, db_session):
    await _bootstrap(client)
    owner = await _app_login(client)
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    made = await client.post("/api/v1/users", json={"username": "anna", "password": PASSWORD}, headers=headers)
    assert made.status_code == 201, made.text
    anna = await client.post(
        "/api/v1/auth/login", json={"username": "anna", "password": PASSWORD, "client_type": "android"}
    )
    first = anna.json()["refresh_token"]
    second = (await _refresh(client, first)).json()

    r = await client.post(
        "/api/v1/me/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}, headers=headers
    )
    assert r.status_code == 204, r.text

    assert (await _refresh(client, first)).status_code == 200
    assert (await _refresh(client, second["refresh_token"])).status_code == 200
    assert await _reuse_entries(db_session) == []
