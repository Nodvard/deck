from datetime import timedelta

import pytest
from nodvard_deck.core import rate_limit, security
from nodvard_deck.db import utcnow
from nodvard_deck.models import RefreshToken
from nodvard_deck.services import auth
from sqlalchemy import select


async def test_invalid_refresh_tokens_show_address_wait(client, monkeypatch):
    monkeypatch.setattr(auth._refresh_address_window, "limit", 3)
    monkeypatch.setattr(rate_limit, "_now", lambda: 1000.0)
    for index in range(3):
        response = await client.post("/api/v1/auth/refresh", json={"refresh_token": f"invalid-{index}"})
        assert response.status_code == 401
    blocked = await client.post("/api/v1/auth/refresh", json={"refresh_token": "invalid-next"})
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "60"
    assert "1 Minute" in blocked.json()["detail"]


async def test_refresh_limit_groups_ipv6_addresses(db_session, test_settings, monkeypatch):
    monkeypatch.setattr(auth._refresh_address_window, "limit", 3)
    for index in range(3):
        with pytest.raises(auth.InvalidCredentials):
            await auth.refresh_access_token(db_session, test_settings, raw_refresh_token="invalid", ip=f"2001:db8::{index}")
    with pytest.raises(auth.TooManyAttempts):
        await auth.refresh_access_token(db_session, test_settings, raw_refresh_token="invalid", ip="2001:db8::4")
    with pytest.raises(auth.InvalidCredentials):
        await auth.refresh_access_token(db_session, test_settings, raw_refresh_token="invalid", ip="2001:db8:0:1::4")


@pytest.mark.parametrize("use_grace", [False, True])
async def test_refresh_limit_follows_the_account_across_addresses(client, db_session, test_settings, monkeypatch, use_grace):
    await client.post("/api/v1/auth/bootstrap", json={
        "username": "owner", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345",
    })
    response = await client.post("/api/v1/auth/login", json={
        "username": "owner", "password": "correct-horse-battery", "client_type": "cli",
    })
    token = response.json()["refresh_token"]
    monkeypatch.setattr(auth._refresh_account_window, "limit", 3)
    monkeypatch.setattr(rate_limit, "_now", lambda: 1000.0)
    for index in range(3):
        tokens = await auth.refresh_access_token(db_session, test_settings, raw_refresh_token=token, ip=f"192.0.2.{index + 1}")
        if not use_grace:
            token = tokens.refresh_token
        await db_session.commit()
    with pytest.raises(auth.TooManyAttempts) as caught:
        await auth.refresh_access_token(db_session, test_settings, raw_refresh_token=token, ip="192.0.2.100")
    assert caught.value.retry_after > 0


@pytest.mark.parametrize("state", ["revoked", "expired", "grace_expired", "successor_revoked"])
async def test_invalid_known_refresh_tokens_do_not_block_another_session(db_session, test_settings, monkeypatch, state):
    await auth.bootstrap_owner(db_session, username="owner", password="correct-horse-battery")
    old = await auth.login(db_session, test_settings, username="owner", password="correct-horse-battery")
    if state in {"grace_expired", "successor_revoked"}:
        rotated = await auth.refresh_access_token(db_session, test_settings, raw_refresh_token=old.refresh_token)
        if state == "successor_revoked":
            await auth.logout(db_session, raw_refresh_token=rotated.refresh_token)
    elif state == "revoked":
        await auth.logout(db_session, raw_refresh_token=old.refresh_token)
    row = (await db_session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == security.hash_opaque_token(old.refresh_token))
    )).scalar_one()
    if state == "expired":
        row.expires_at = utcnow() - timedelta(seconds=1)
    elif state == "grace_expired":
        row.revoked_at = utcnow() - timedelta(seconds=auth.REFRESH_GRACE_SECONDS + 1)
    await db_session.commit()
    current = await auth.login(db_session, test_settings, username="owner", password="correct-horse-battery")
    monkeypatch.setattr(auth._refresh_account_window, "limit", 3)
    monkeypatch.setattr(rate_limit, "_now", lambda: 1000.0)
    auth._refresh_account_window.reset()

    for index in range(3):
        with pytest.raises(auth.InvalidCredentials):
            await auth.refresh_access_token(
                db_session, test_settings, raw_refresh_token=old.refresh_token, ip=f"192.0.2.{index + 1}",
            )
    tokens = await auth.refresh_access_token(
        db_session, test_settings, raw_refresh_token=current.refresh_token, ip="192.0.2.100",
    )
    assert tokens.refresh_token and tokens.refresh_token != current.refresh_token


async def test_valid_refreshes_behind_one_address_do_not_hit_the_address_limit(db_session, test_settings, monkeypatch):
    monkeypatch.setattr(auth._refresh_address_window, "limit", 3)
    monkeypatch.setattr(rate_limit, "_now", lambda: 1000.0)
    auth._refresh_address_window.reset()
    first = await auth.bootstrap_owner(db_session, username="owner", password="correct-horse-battery")
    await db_session.commit()
    tokens = [await auth.login(db_session, test_settings, username="owner", password="correct-horse-battery") for _ in range(6)]
    assert first is not None
    for issued in tokens:
        rotated = await auth.refresh_access_token(
            db_session, test_settings, raw_refresh_token=issued.refresh_token, ip="192.0.2.7",
        )
        assert rotated.refresh_token
        await db_session.commit()
    # Ungültige Tokens zählen weiter: nach der Grenze kommt die Wartezeit.
    for _ in range(3):
        with pytest.raises(auth.InvalidCredentials):
            await auth.refresh_access_token(db_session, test_settings, raw_refresh_token="invalid", ip="192.0.2.7")
    with pytest.raises(auth.TooManyAttempts):
        await auth.refresh_access_token(db_session, test_settings, raw_refresh_token="invalid", ip="192.0.2.7")
