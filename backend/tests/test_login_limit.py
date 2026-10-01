"""Drosselung fehlgeschlagener Anmeldungen (core/login_limit.py)."""

from __future__ import annotations

import time

import pyotp
import pytest
from httpx import ASGITransport, AsyncClient
from nodvard_deck.core import login_limit
from nodvard_deck.models import AuditEntry
from sqlalchemy import select

PASSWORD = "correct-horse-battery"


async def _bootstrap(client, username="nico"):
    r = await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": PASSWORD, "setup_code": "TEST-CODE-2345"})
    assert r.status_code == 201


async def _login(client, username="nico", password=PASSWORD):
    return await client.post("/api/v1/auth/login", json={"username": username, "password": password})


def _other_ip_client(ip: str) -> AsyncClient:
    from nodvard_deck.main import app

    transport = ASGITransport(app=app, client=(ip, 4711), raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(login_limit, "_now", lambda: now[0])
    return now


async def _audit(db_session, action: str) -> list[AuditEntry]:
    result = await db_session.execute(select(AuditEntry).where(AuditEntry.action == action))
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_eleventh_attempt_is_429_even_with_correct_password(client, db_session, monkeypatch):
    await _bootstrap(client)
    for _ in range(login_limit.MAX_FAILURES_PER_USER):
        assert (await _login(client, password="falsch")).status_code == 401
    assert len(await _audit(db_session, "login.locked")) == 1

    from nodvard_deck.core import security

    calls = []
    real_verify = security.verify_password
    monkeypatch.setattr(
        security, "verify_password", lambda *a, **k: calls.append(1) or real_verify(*a, **k)
    )
    r = await _login(client)
    assert r.status_code == 429
    assert r.json()["detail"].startswith("Zu viele Fehlversuche. Bitte in ")
    assert r.json()["detail"].endswith("erneut versuchen.")
    assert 0 < int(r.headers["Retry-After"]) <= login_limit.WINDOW_SECONDS
    assert calls == []  # gesperrt, bevor Argon2 ueberhaupt laeuft

    assert (await _login(client, password="falsch")).status_code == 429
    # Genau ein Audit-Eintrag je Sperre, weitere 429er schreiben keinen neuen.
    assert len(await _audit(db_session, "login.locked")) == 1
    assert len(await _audit(db_session, "login.failed")) == login_limit.MAX_FAILURES_PER_USER


@pytest.mark.asyncio
async def test_lock_ends_after_the_window(client, clock):
    await _bootstrap(client)
    for _ in range(login_limit.MAX_FAILURES_PER_USER):
        assert (await _login(client, password="falsch")).status_code == 401
        clock[0] += 1
    r = await _login(client)
    assert r.status_code == 429
    # Aeltester Fehlversuch bei t=1000, jetzt t=1010 -> noch 290 s.
    assert r.headers["Retry-After"] == str(login_limit.WINDOW_SECONDS - 10)
    assert "in 5 Minuten" in r.json()["detail"]

    clock[0] += login_limit.WINDOW_SECONDS - 10
    assert (await _login(client)).status_code == 200


@pytest.mark.asyncio
async def test_successful_login_resets_the_counter(client):
    await _bootstrap(client)
    for _ in range(login_limit.MAX_FAILURES_PER_USER - 1):
        assert (await _login(client, password="falsch")).status_code == 401
    assert (await _login(client)).status_code == 200
    for _ in range(login_limit.MAX_FAILURES_PER_USER - 1):
        assert (await _login(client, password="falsch")).status_code == 401
    assert (await _login(client)).status_code == 200


@pytest.mark.asyncio
async def test_other_username_and_other_ip_are_not_locked(client):
    await _bootstrap(client)
    for _ in range(login_limit.MAX_FAILURES_PER_USER):
        await _login(client, password="falsch")
    assert (await _login(client)).status_code == 429
    # Anderer Name von derselben IP: normale Pruefung (401), keine Sperre.
    assert (await _login(client, username="anna", password="x")).status_code == 401
    # Derselbe Name von einem anderen Geraet: klappt.
    async with _other_ip_client("192.168.1.50") as other:
        assert (await _login(other)).status_code == 200


@pytest.mark.asyncio
async def test_many_usernames_from_one_ip_are_throttled(client, db_session):
    await _bootstrap(client)
    for i in range(login_limit.MAX_FAILURES_PER_IP):
        assert (await _login(client, username=f"user{i}", password="x")).status_code == 401
    r = await _login(client)
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    locked = await _audit(db_session, "login.locked")
    assert [e.detail["scopes"] for e in locked] == [["ip"]]
    async with _other_ip_client("192.168.1.50") as other:
        assert (await _login(other)).status_code == 200


def test_lockout_message_singular():
    assert login_limit.lockout_message(30) == "Zu viele Fehlversuche. Bitte in 1 Minute erneut versuchen."
    assert login_limit.lockout_message(61) == "Zu viele Fehlversuche. Bitte in 2 Minuten erneut versuchen."


# ---------------------------------------------------------------------------
# Zweiter Faktor
# ---------------------------------------------------------------------------


async def _enable_totp(client) -> str:
    await _bootstrap(client)
    token = (await _login(client)).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    secret = (await client.post("/api/v1/me/totp/setup", headers=headers)).json()["secret"]
    confirm = await client.post(
        "/api/v1/me/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=headers
    )
    assert confirm.status_code == 200
    return secret


def _wrong_code(secret: str) -> str:
    totp = pyotp.TOTP(secret)
    now = time.time()
    valid = {totp.at(now + offset) for offset in (-30, 0, 30)}
    return next(c for c in (f"{n:06d}" for n in range(1000000)) if c not in valid)


async def _mfa_token(client) -> str:
    r = await _login(client)
    assert r.status_code == 202
    return r.json()["mfa_token"]


async def _mfa(client, mfa_token: str, code: str):
    return await client.post("/api/v1/auth/mfa", json={"mfa_token": mfa_token, "code": code})


@pytest.mark.asyncio
async def test_mfa_token_is_dead_after_five_wrong_codes(client):
    secret = await _enable_totp(client)
    wrong = _wrong_code(secret)
    mfa_token = await _mfa_token(client)

    for _ in range(login_limit.MAX_MFA_FAILURES - 1):
        r = await _mfa(client, mfa_token, wrong)
        assert r.status_code == 401
        assert r.json()["detail"] == "Ungültiger 2FA-Code."
    r = await _mfa(client, mfa_token, wrong)
    assert r.status_code == 429
    assert "erneut an" in r.json()["detail"]

    # Auch der richtige Code hilft mit diesem Token nicht mehr.
    r = await _mfa(client, mfa_token, pyotp.TOTP(secret).now())
    assert r.status_code == 429
    assert "erneut an" in r.json()["detail"]

    # Neu anmelden -> neues Token -> klappt.
    fresh = await _mfa_token(client)
    assert (await _mfa(client, fresh, pyotp.TOTP(secret).now())).status_code == 200


@pytest.mark.asyncio
async def test_wrong_mfa_codes_count_towards_the_login_lock(client):
    """Sonst liesse sich mit dem richtigen Passwort ueber immer neue Tokens doch
    beliebig oft raten."""
    secret = await _enable_totp(client)
    wrong = _wrong_code(secret)
    per_token = login_limit.MAX_MFA_FAILURES
    for _ in range(login_limit.MAX_FAILURES_PER_USER // per_token):
        mfa_token = await _mfa_token(client)
        for _ in range(per_token):
            await _mfa(client, mfa_token, wrong)

    r = await _login(client)
    assert r.status_code == 429
    assert r.json()["detail"].startswith("Zu viele Fehlversuche.")
