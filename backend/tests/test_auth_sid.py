"""Ein Zugangs-Token gilt nur, solange die Anmeldung dahinter (`sid`) gilt."""

from __future__ import annotations

import contextlib
import itertools
from datetime import timedelta

import pytest
from sqlalchemy import event, select

from nodvard_deck.core import security
from nodvard_deck.db import new_id, utcnow
from nodvard_deck.models import AuditEntry, RefreshToken, User
from nodvard_deck.services import auth as auth_service
from nodvard_deck.services import session_guard

PASSWORD = "correct-horse-battery"


async def _bootstrap(client) -> str:
    r = await client.post(
        "/api/v1/auth/bootstrap", json={"username": "nico", "password": PASSWORD, "setup_code": "TEST-CODE-2345"}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _login(client, **extra) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD, **extra})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _me(client, token: str) -> int:
    return (await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})).status_code


def _browser_holds(client, value: str) -> None:
    for name in ("nodvard_deck_refresh", "lattice_refresh"):
        client.cookies.set(name, value)


def _token(settings, user_id: str, **claims) -> str:
    return security.create_jwt(
        subject=user_id,
        token_type="access",
        secret=settings.get_or_create_jwt_secret(),
        ttl_seconds=600,
        extra_claims=claims,
    )


def _sid(settings, token: str) -> str:
    return security.decode_jwt(token, secret=settings.get_or_create_jwt_secret(), expected_type="access")["sid"]


async def _app_login(client) -> dict:
    r = await client.post(
        "/api/v1/auth/login", json={"username": "nico", "password": PASSWORD, "client_type": "android"}
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_logout_ends_the_access_token_at_once(client):
    await _bootstrap(client)
    token = await _login(client)
    assert await _me(client, token) == 200

    assert (await client.post("/api/v1/auth/logout", json={})).status_code == 204
    assert await _me(client, token) == 401


@pytest.mark.asyncio
async def test_rotation_in_another_tab_keeps_the_older_access_token(client):
    await _bootstrap(client)
    older = await _login(client)
    rotated = await client.post("/api/v1/auth/refresh", json={})
    assert rotated.status_code == 200

    assert await _me(client, older) == 200
    assert await _me(client, rotated.json()["access_token"]) == 200
    # Abmelden am Ende der Kette beendet auch das ältere Token.
    assert (await client.post("/api/v1/auth/logout", json={})).status_code == 204
    assert await _me(client, older) == 401


@pytest.mark.asyncio
async def test_logout_with_an_already_renewed_token_ends_the_whole_chain(client):
    await _bootstrap(client)
    first = (await client.post(
        "/api/v1/auth/login", json={"username": "nico", "password": PASSWORD, "client_type": "android"}
    )).json()
    # Die Antwort der Erneuerung kommt beim Gerät nicht an, es meldet sich mit dem alten Token ab.
    renewed = (await client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})).json()

    r = await client.post("/api/v1/auth/logout", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 204
    assert await _me(client, first["access_token"]) == 401
    assert await _me(client, renewed["access_token"]) == 401
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": renewed["refresh_token"]})).status_code == 401


@pytest.mark.asyncio
async def test_logout_two_renewals_back_still_ends_the_session(client):
    await _bootstrap(client)
    first = (await client.post(
        "/api/v1/auth/login", json={"username": "nico", "password": PASSWORD, "client_type": "android"}
    )).json()
    second = (await client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})).json()
    third = (await client.post("/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})).json()

    assert (await client.post("/api/v1/auth/logout", json={"refresh_token": first["refresh_token"]})).status_code == 204
    assert await _me(client, third["access_token"]) == 401
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": third["refresh_token"]})).status_code == 401


@pytest.mark.asyncio
async def test_logout_while_another_tab_renews_still_ends_the_session(client, file_db, test_settings, monkeypatch):
    await _bootstrap(client)
    first = await _app_login(client)
    find_end = auth_service.session_chain_end

    async def renew_in_between(session, user_id, token_id, **kwargs):
        end = await find_end(session, user_id, token_id, **kwargs)
        monkeypatch.setattr(auth_service, "session_chain_end", find_end)
        async with file_db() as other_tab:
            await auth_service.refresh_access_token(other_tab, test_settings, raw_refresh_token=first["refresh_token"])
            await other_tab.commit()
        return end

    monkeypatch.setattr(auth_service, "session_chain_end", renew_in_between)
    async with file_db() as session:
        await auth_service.logout(session, raw_refresh_token=first["refresh_token"])
        await session.commit()

    async with file_db() as session:
        live = (await session.execute(select(RefreshToken).where(RefreshToken.revoked_at.is_(None)))).scalars().all()
        logouts = (
            await session.execute(select(AuditEntry).where(AuditEntry.action == "logout.succeeded"))
        ).scalars().all()
    assert live == []
    assert len(logouts) == 1


@pytest.mark.asyncio
async def test_logging_out_twice_is_logged_once(client, db_session):
    await _bootstrap(client)
    first = await _app_login(client)
    for _ in range(2):
        r = await client.post("/api/v1/auth/logout", json={"refresh_token": first["refresh_token"]})
        assert r.status_code == 204

    logouts = (
        await db_session.execute(select(AuditEntry).where(AuditEntry.action == "logout.succeeded"))
    ).scalars().all()
    assert len(logouts) == 1


@pytest.mark.asyncio
async def test_long_chains_are_followed(client):
    await _bootstrap(client)
    first = latest = await _app_login(client)
    for _ in range(30):
        latest = (await client.post("/api/v1/auth/refresh", json={"refresh_token": latest["refresh_token"]})).json()

    assert await _me(client, first["access_token"]) == 200
    assert (await client.post("/api/v1/auth/logout", json={"refresh_token": first["refresh_token"]})).status_code == 204
    assert await _me(client, latest["access_token"]) == 401


@pytest.mark.asyncio
async def test_logout_reaches_the_end_of_a_chain_turned_far_beyond_the_check_limit(client):
    await _bootstrap(client)
    first = latest = await _app_login(client)
    for _ in range(auth_service.SESSION_CHAIN_MAX_HOPS + 10):
        latest = (await client.post("/api/v1/auth/refresh", json={"refresh_token": latest["refresh_token"]})).json()

    assert (await client.post("/api/v1/auth/logout", json={"refresh_token": first["refresh_token"]})).status_code == 204
    assert await _me(client, latest["access_token"]) == 401
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": latest["refresh_token"]})).status_code == 401


@pytest.mark.asyncio
async def test_logout_never_ends_a_login_of_another_account(client, db_session, test_settings):
    user_id = await _bootstrap(client)
    first = (await client.post(
        "/api/v1/auth/login", json={"username": "nico", "password": PASSWORD, "client_type": "android"}
    )).json()
    await client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    other = User(username="andere", password_hash="x")
    db_session.add(other)
    await db_session.flush()
    foreign = RefreshToken(
        user_id=other.id, token_hash=security.hash_opaque_token("andere"), expires_at=utcnow() + timedelta(days=1)
    )
    db_session.add(foreign)
    await db_session.flush()
    # Kaputte Daten: Die Kette zeigt auf die Anmeldung eines anderen Kontos.
    own = await db_session.get(RefreshToken, _sid(test_settings, first["access_token"]))
    assert own.user_id == user_id
    own.replaced_by_id = foreign.id
    await db_session.flush()

    assert (await client.post("/api/v1/auth/logout", json={"refresh_token": first["refresh_token"]})).status_code == 204
    await db_session.refresh(foreign)
    assert foreign.revoked_at is None


@pytest.mark.asyncio
async def test_password_change_from_an_older_tab_keeps_both_tabs(client):
    await _bootstrap(client)
    login = await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})
    older = login.json()["access_token"]
    old_cookie = login.cookies["nodvard_deck_refresh"]
    rotated = await client.post("/api/v1/auth/refresh", json={})
    current_cookie = rotated.cookies["nodvard_deck_refresh"]

    r = await client.post(
        "/api/v1/me/password",
        json={"current_password": PASSWORD, "new_password": "neues-passwort-1"},
        headers={"Authorization": f"Bearer {older}"},
    )
    assert r.status_code == 204, r.text
    assert await _me(client, older) == 200
    assert await _me(client, rotated.json()["access_token"]) == 200
    # Die Gnadenfrist des alten Refresh-Tokens ist trotzdem vorbei.
    _browser_holds(client, old_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401
    _browser_holds(client, current_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 200


@pytest.mark.asyncio
async def test_password_change_ends_other_access_tokens_at_once(client):
    await _bootstrap(client)
    phone = await _login(client, client_type="android")
    web = await _login(client)

    r = await client.post(
        "/api/v1/me/password",
        json={"current_password": PASSWORD, "new_password": "neues-passwort-1"},
        headers={"Authorization": f"Bearer {web}"},
    )
    assert r.status_code == 204, r.text
    assert await _me(client, phone) == 401
    assert await _me(client, web) == 200


@pytest.mark.asyncio
async def test_password_reset_by_admin_ends_the_users_access_tokens(client):
    await _bootstrap(client)
    owner = await _login(client)
    created = await client.post(
        "/api/v1/users",
        json={"username": "gast", "password": "gast-passwort-1"},
        headers={"Authorization": f"Bearer {owner}"},
    )
    assert created.status_code == 201, created.text
    guest = (await client.post("/api/v1/auth/login", json={"username": "gast", "password": "gast-passwort-1"})).json()
    assert await _me(client, guest["access_token"]) == 200

    r = await client.patch(
        f"/api/v1/users/{created.json()['id']}",
        json={"password": "anderes-passwort-1", "current_password": PASSWORD},
        headers={"Authorization": f"Bearer {owner}"},
    )
    assert r.status_code == 200, r.text
    assert await _me(client, guest["access_token"]) == 401
    assert await _me(client, owner) == 200


@pytest.mark.asyncio
async def test_reactivated_account_needs_a_new_login(client):
    await _bootstrap(client)
    owner = await _login(client)
    created = await client.post(
        "/api/v1/users",
        json={"username": "gast", "password": "gast-passwort-1"},
        headers={"Authorization": f"Bearer {owner}"},
    )
    guest = (await client.post("/api/v1/auth/login", json={"username": "gast", "password": "gast-passwort-1"})).json()
    for active in (False, True):
        r = await client.patch(
            f"/api/v1/users/{created.json()['id']}",
            json={"is_active": active},
            headers={"Authorization": f"Bearer {owner}"},
        )
        assert r.status_code == 200, r.text

    assert await _me(client, guest["access_token"]) == 401


@pytest.mark.asyncio
async def test_access_token_without_sid_needs_a_live_login(client, test_settings):
    user_id = await _bootstrap(client)
    await _login(client)
    old_style = _token(test_settings, user_id)
    assert await _me(client, old_style) == 200

    assert (await client.post("/api/v1/auth/logout", json={})).status_code == 204
    assert await _me(client, old_style) == 401


@pytest.mark.asyncio
async def test_access_token_with_a_foreign_unknown_or_odd_sid_is_refused(client, db_session, test_settings):
    user_id = await _bootstrap(client)
    await _login(client)
    other = User(username="andere", password_hash="x")
    db_session.add(other)
    await db_session.flush()
    foreign = RefreshToken(
        user_id=other.id, token_hash=security.hash_opaque_token("andere"), expires_at=utcnow() + timedelta(days=1)
    )
    db_session.add(foreign)
    await db_session.flush()

    assert await _me(client, _token(test_settings, user_id, sid=foreign.id)) == 401
    assert await _me(client, _token(test_settings, user_id, sid="unbekannt")) == 401
    assert await _me(client, _token(test_settings, user_id, sid=7)) == 401


@pytest.mark.asyncio
async def test_access_token_ends_with_its_expired_login(client, db_session, test_settings):
    await _bootstrap(client)
    token = await _login(client)
    login = await db_session.get(RefreshToken, _sid(test_settings, token))
    login.expires_at = utcnow() - timedelta(seconds=1)
    await db_session.flush()

    assert await _me(client, token) == 401


def _chain(user_id: str, renewals: int, name: str) -> list[RefreshToken]:
    now = utcnow()
    rows = [
        RefreshToken(
            id=new_id(),
            user_id=user_id,
            token_hash=security.hash_opaque_token(f"{name}-{i}"),
            expires_at=now + timedelta(days=1),
        )
        for i in range(renewals + 1)
    ]
    for row, successor in itertools.pairwise(rows):
        row.revoked_at = now - timedelta(hours=1)
        row.replaced_by_id = successor.id
    return rows


async def _store(db_session, *chains: list[RefreshToken]) -> None:
    db_session.add_all([row for chain in chains for row in chain])
    await db_session.flush()
    # Sonst läse eine Suche Schritt für Schritt aus der Session statt aus der Datenbank, und die Zählung sähe sie nicht.
    db_session.expunge_all()


@contextlib.contextmanager
def _statements(db_session):
    seen: list[str] = []

    def count(_conn, _cursor, statement, *_args) -> None:
        seen.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", count)


async def _live(db_session) -> set[str]:
    return set((await db_session.execute(select(RefreshToken.id).where(RefreshToken.revoked_at.is_(None)))).scalars())


@pytest.mark.asyncio
async def test_chain_length_adds_no_queries(client, db_session):
    user_id = await _bootstrap(client)
    counts = []
    for name, renewals in (("kurz", 2), ("lang", 150)):
        chain = _chain(user_id, renewals, name)
        await _store(db_session, chain, _chain(user_id, 0, f"{name}-handy"))
        with _statements(db_session) as others:
            assert await auth_service.revoke_refresh_tokens(db_session, user_id, except_token_id=chain[0].id) == 1
        with _statements(db_session) as logout:
            await auth_service.logout(db_session, raw_refresh_token=f"{name}-0")
            await db_session.flush()
        assert await _live(db_session) == set()
        counts.append((len(others), len(logout)))

    assert counts[0] == counts[1]
    assert max(counts[0]) <= 5


@pytest.mark.asyncio
async def test_access_token_check_follows_up_to_the_limit_in_one_query(client, db_session):
    user_id = await _bootstrap(client)
    within = _chain(user_id, auth_service.SESSION_CHAIN_MAX_HOPS, "innen")
    beyond = _chain(user_id, auth_service.SESSION_CHAIN_MAX_HOPS + 1, "aussen")
    await _store(db_session, within, beyond)

    with _statements(db_session) as checks:
        assert await session_guard.login_is_live(db_session, user_id, within[0].id)
        assert not await session_guard.login_is_live(db_session, user_id, beyond[0].id)
    assert len(checks) == 2


@pytest.mark.asyncio
async def test_the_chain_query_is_built_once(client, db_session, monkeypatch):
    user_id = await _bootstrap(client)
    chain = _chain(user_id, 3, "einmal")
    await _store(db_session, chain)

    def no_new_statement(*_args, **_kwargs):
        raise AssertionError("Die Abfrage der Kette entsteht bei jedem Aufruf neu")

    for name in ("select", "aliased", "bindparam", "literal_column"):
        monkeypatch.setattr(auth_service, name, no_new_statement)
    end = await auth_service.session_chain_end(db_session, user_id, chain[0].id, max_hops=10)
    assert end is not None and end.id == chain[-1].id


@pytest.mark.asyncio
async def test_logout_beyond_the_limit_ends_every_login_of_the_account(client, db_session, monkeypatch):
    user_id = await _bootstrap(client)
    monkeypatch.setattr(auth_service, "SESSION_CHAIN_LIMIT", 5)
    other = User(username="andere", password_hash="x")
    db_session.add(other)
    await db_session.flush()
    at_limit, too_long = _chain(user_id, 5, "genau"), _chain(user_id, 6, "lang")
    phone, foreign = _chain(user_id, 0, "handy"), _chain(other.id, 0, "fremd")
    await _store(db_session, at_limit, too_long, phone, foreign)

    await auth_service.logout(db_session, raw_refresh_token="genau-0")
    assert await _live(db_session) == {too_long[-1].id, phone[0].id, foreign[0].id}
    await auth_service.logout(db_session, raw_refresh_token="lang-0")
    assert await _live(db_session) == {foreign[0].id}
    with _statements(db_session) as again:
        await auth_service.logout(db_session, raw_refresh_token="lang-0")
    assert len(again) == 2

    logouts = (
        await db_session.execute(select(AuditEntry.detail).where(AuditEntry.action == "logout.succeeded"))
    ).scalars().all()
    assert sorted(logouts, key=len) == [{}, {"signed_out_sessions": 2}]


@pytest.mark.asyncio
async def test_signing_out_others_beyond_the_limit_ends_the_own_login_too(client, db_session, monkeypatch):
    user_id = await _bootstrap(client)
    monkeypatch.setattr(auth_service, "SESSION_CHAIN_LIMIT", 5)
    at_limit = _chain(user_id, 5, "genau")
    await _store(db_session, at_limit, _chain(user_id, 0, "handy"))
    assert await auth_service.revoke_refresh_tokens(db_session, user_id, except_token_id=at_limit[0].id) == 1
    assert await _live(db_session) == {at_limit[-1].id}

    too_long = _chain(user_id, 6, "lang")
    await _store(db_session, too_long)
    assert await auth_service.revoke_refresh_tokens(db_session, user_id, except_token_id=too_long[0].id) == 2
    assert await _live(db_session) == set()


@pytest.mark.asyncio
async def test_a_chain_running_in_circles_ends_safely(client, db_session, monkeypatch):
    user_id = await _bootstrap(client)
    monkeypatch.setattr(auth_service, "SESSION_CHAIN_LIMIT", 5)
    loop, phone = _chain(user_id, 1, "kreis"), _chain(user_id, 0, "handy")
    loop[-1].revoked_at = loop[0].revoked_at
    loop[-1].replaced_by_id = loop[0].id
    await _store(db_session, loop, phone)

    assert not await session_guard.login_is_live(db_session, user_id, loop[0].id)
    await auth_service.logout(db_session, raw_refresh_token="kreis-0")
    assert await _live(db_session) == set()


@pytest.mark.asyncio
async def test_a_chain_pointing_into_another_account_ends_at_the_account_border(client, db_session):
    user_id = await _bootstrap(client)
    other = User(username="andere", password_hash="x")
    db_session.add(other)
    await db_session.flush()
    own, phone, foreign = _chain(user_id, 0, "eigen"), _chain(user_id, 0, "handy"), _chain(other.id, 0, "fremd")
    own[0].revoked_at = utcnow() - timedelta(hours=1)
    own[0].replaced_by_id = foreign[0].id
    await _store(db_session, own, phone, foreign)

    assert not await session_guard.login_is_live(db_session, user_id, own[0].id)
    assert await auth_service.revoke_refresh_tokens(db_session, user_id, except_token_id=own[0].id) == 1
    assert await _live(db_session) == {foreign[0].id}


@pytest.mark.asyncio
async def test_the_chain_only_continues_after_a_renewal(client, db_session):
    user_id = await _bootstrap(client)
    first, second = _chain(user_id, 0, "eins"), _chain(user_id, 0, "zwei")
    first[0].replaced_by_id = second[0].id
    await _store(db_session, first, second)

    await auth_service.logout(db_session, raw_refresh_token="eins-0")
    assert await _live(db_session) == {second[0].id}
