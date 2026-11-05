"""Refresh-Cookie `nodvard_deck_refresh`: geschrieben, gelesen und gelöscht wird nur dieses, das alte `lattice_refresh`
gilt nicht mehr."""

from __future__ import annotations

from datetime import timedelta

import pytest
from nodvard_deck.core import security
from nodvard_deck.models import RefreshToken
from sqlalchemy import select

NEW = "nodvard_deck_refresh"
OLD = "lattice_refresh"
PASSWORD = "correct-horse-battery"
API = "/api/v1/auth"


async def _bootstrap(client, username="nico"):
    r = await client.post(
        f"{API}/bootstrap",
        json={"username": username, "password": PASSWORD, "setup_code": "TEST-CODE-2345"},
    )
    assert r.status_code == 201, r.text


def _set_cookies(response) -> dict[str, tuple[str, dict]]:
    """Alle `Set-Cookie`-Zeilen einer Antwort: Name -> (Wert, Attribute)."""
    found: dict[str, tuple[str, dict]] = {}
    for line in response.headers.get_list("set-cookie"):
        parts = [p.strip() for p in line.split(";")]
        name, _, value = parts[0].partition("=")
        attrs: dict = {}
        for part in parts[1:]:
            key, _, val = part.partition("=")
            attrs[key.strip().lower()] = val.strip().lower() or True
        assert name not in found, f"{name} kommt in der Antwort doppelt vor"
        found[name] = (value, attrs)
    return found


async def _post(client, path: str, *, cookies: dict[str, str] | None = None, json=None, base="http://test"):
    client.cookies.clear()
    header = {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())} if cookies else {}
    return await client.post(f"{base}{API}{path}", json={} if json is None else json, headers=header)


async def _login(client, base="http://test") -> str:
    r = await _post(client, "/login", json={"username": "nico", "password": PASSWORD}, base=base)
    assert r.status_code == 200, r.text
    cookies = _set_cookies(r)
    assert set(cookies) == {NEW}
    return cookies[NEW][0]


async def _row(db_session, raw: str) -> RefreshToken:
    query = (
        select(RefreshToken)
        .where(RefreshToken.token_hash == security.hash_opaque_token(raw))
        .execution_options(populate_existing=True)
    )
    return (await db_session.execute(query)).scalar_one()


@pytest.mark.asyncio
@pytest.mark.parametrize(("base", "secure"), [("http://test", False), ("https://test", True)])
async def test_login_writes_only_the_new_cookie(client, base, secure):
    await _bootstrap(client)
    r = await _post(client, "/login", json={"username": "nico", "password": PASSWORD}, base=base)

    cookies = _set_cookies(r)
    assert set(cookies) == {NEW}
    value, attrs = cookies[NEW]
    assert value
    assert attrs["path"] == API
    assert attrs["httponly"] is True
    assert attrs["samesite"] == "strict"
    assert ("secure" in attrs) is secure


@pytest.mark.asyncio
async def test_refresh_reads_and_writes_only_the_new_cookie(client):
    await _bootstrap(client)
    token = await _login(client)

    r = await _post(client, "/refresh", cookies={NEW: token})

    assert r.status_code == 200, r.text
    cookies = _set_cookies(r)
    assert set(cookies) == {NEW}
    assert cookies[NEW][0] not in ("", token)


@pytest.mark.asyncio
async def test_the_old_cookie_alone_is_no_login(client, db_session):
    await _bootstrap(client)
    token = await _login(client)

    r = await _post(client, "/refresh", cookies={OLD: token})

    assert r.status_code == 400
    assert _set_cookies(r) == {}
    assert (await _row(db_session, token)).revoked_at is None


@pytest.mark.asyncio
async def test_an_old_cookie_next_to_the_new_one_is_ignored(client, db_session):
    await _bootstrap(client)
    valid = await _login(client)
    revoked = await _login(client)
    await _post(client, "/logout", cookies={NEW: revoked})

    r = await _post(client, "/refresh", cookies={NEW: revoked, OLD: valid})

    assert r.status_code == 401, r.text
    assert (await _row(db_session, valid)).revoked_at is None


@pytest.mark.asyncio
async def test_android_gets_the_token_in_the_body_and_no_cookie(client):
    await _bootstrap(client)
    r = await _post(client, "/login", json={"username": "nico", "password": PASSWORD, "client_type": "android"})

    assert r.status_code == 200, r.text
    assert r.json()["refresh_token"]
    assert _set_cookies(r) == {}


@pytest.mark.asyncio
async def test_within_the_grace_period_no_cookie_is_set_or_deleted(client):
    await _bootstrap(client)
    token = await _login(client)
    first = await _post(client, "/refresh", cookies={NEW: token})
    assert first.status_code == 200

    again = await _post(client, "/refresh", cookies={NEW: token})

    assert again.status_code == 200, again.text
    assert again.json()["access_token"]
    assert _set_cookies(again) == {}


@pytest.mark.asyncio
async def test_after_the_grace_period_a_reused_token_is_rejected(client, db_session):
    await _bootstrap(client)
    token = await _login(client)
    assert (await _post(client, "/refresh", cookies={NEW: token})).status_code == 200
    row = await _row(db_session, token)
    row.revoked_at = row.revoked_at - timedelta(seconds=31)
    await db_session.flush()

    r = await _post(client, "/refresh", cookies={NEW: token})

    assert r.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(("base", "secure"), [("http://test", False), ("https://test", True)])
async def test_logout_revokes_the_token_and_deletes_only_the_new_cookie(client, db_session, base, secure):
    await _bootstrap(client)
    token = await _login(client, base=base)

    r = await _post(client, "/logout", cookies={NEW: token, OLD: token}, base=base)

    assert r.status_code == 204
    cookies = _set_cookies(r)
    assert set(cookies) == {NEW}
    value, attrs = cookies[NEW]
    assert value in ("", '""')
    assert attrs["path"] == API
    assert attrs["httponly"] is True and attrs["samesite"] == "strict"
    assert ("secure" in attrs) is secure
    assert (await _row(db_session, token)).revoked_at is not None


@pytest.mark.asyncio
async def test_logout_does_not_revoke_a_token_that_only_the_old_cookie_carries(client, db_session):
    await _bootstrap(client)
    token = await _login(client)

    r = await _post(client, "/logout", cookies={OLD: token})

    assert r.status_code == 204
    assert (await _row(db_session, token)).revoked_at is None


@pytest.mark.asyncio
async def test_a_token_in_the_body_wins_over_the_cookie(client, db_session):
    await _bootstrap(client)
    body_token = await _login(client)
    cookie_token = await _login(client)

    r = await _post(client, "/logout", cookies={NEW: cookie_token}, json={"refresh_token": body_token})

    assert r.status_code == 204
    assert (await _row(db_session, body_token)).revoked_at is not None
    assert (await _row(db_session, cookie_token)).revoked_at is None
