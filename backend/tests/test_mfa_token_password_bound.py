"""Ein Zwischentoken für den zweiten Faktor gilt nur für das Passwort, mit dem es ausgestellt wurde."""

from __future__ import annotations

import base64
import hashlib
import json
import time

import pyotp
import pytest
from argon2 import PasswordHasher
from nodvard_deck.core import login_limit, security, vault
from nodvard_deck.models import AuditEntry, RecoveryCode, User
from nodvard_deck.services import auth as auth_service
from sqlalchemy import func, select, update
from totp_helpers import setup_confirm_code

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "ganz-neues-passwort-42"


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(auth_service, "_totp_now", lambda: now[0])
    return now


async def _owner_with_2fa(client, clock) -> tuple[str, str, list[str]]:
    await client.post(
        "/api/v1/auth/bootstrap", json={"username": "nico", "password": PASSWORD, "setup_code": "TEST-CODE-2345"}
    )
    token = (await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})).json()[
        "access_token"
    ]
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=_h(token))).json()[
        "secret"
    ]
    r = await client.post("/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret, clock[0])}, headers=_h(token))
    assert r.status_code == 200, r.text
    return token, secret, r.json()["recovery_codes"]


async def _mfa_token(client, password: str = PASSWORD) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": "nico", "password": password})
    assert r.status_code == 202, r.text
    return r.json()["mfa_token"]


async def _mfa(client, mfa_token: str, code: str):
    return await client.post("/api/v1/auth/mfa", json={"mfa_token": mfa_token, "code": code})


async def _set_hash_elsewhere(db_session, new_password: str = NEW_PASSWORD) -> None:
    await db_session.execute(update(User).values(password_hash=security.hash_password(new_password)))
    await db_session.commit()


async def _unused_recovery_codes(db_session) -> int:
    db_session.expire_all()
    return (
        await db_session.execute(select(func.count()).select_from(RecoveryCode).where(RecoveryCode.used_at.is_(None)))
    ).scalar_one()


def _claims(jwt: str) -> dict:
    body = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


@pytest.mark.asyncio
async def test_unchanged_password_completes_the_login(client, clock):
    _, secret, _ = await _owner_with_2fa(client, clock)
    pending = await _mfa_token(client)
    clock[0] += 30
    r = await _mfa(client, pending, pyotp.TOTP(secret).at(clock[0]))
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]


@pytest.mark.asyncio
async def test_own_password_change_ends_a_pending_login(client, clock):
    token, secret, _ = await _owner_with_2fa(client, clock)
    pending = await _mfa_token(client)

    clock[0] += 30
    r = await client.post(
        "/api/v1/me/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD, "totp_code": pyotp.TOTP(secret).at(clock[0])},
        headers=_h(token),
    )
    assert r.status_code == 204, r.text

    clock[0] += 30
    r = await _mfa(client, pending, pyotp.TOTP(secret).at(clock[0]))
    assert r.status_code == 401
    assert "Passwort" in r.json()["detail"]
    assert "access_token" not in r.json()

    fresh = await _mfa_token(client, NEW_PASSWORD)
    assert (await _mfa(client, fresh, pyotp.TOTP(secret).at(clock[0]))).status_code == 200


@pytest.mark.asyncio
async def test_password_set_elsewhere_ends_a_pending_login(client, db_session, clock):
    _, secret, _ = await _owner_with_2fa(client, clock)
    pending = await _mfa_token(client)
    await _set_hash_elsewhere(db_session)

    clock[0] += 30
    r = await _mfa(client, pending, pyotp.TOTP(secret).at(clock[0]))
    assert r.status_code == 401
    assert "access_token" not in r.json()


@pytest.mark.asyncio
async def test_stale_token_leaves_the_recovery_code_and_the_counter_alone(client, db_session, clock):
    _, _, codes = await _owner_with_2fa(client, clock)
    pending = await _mfa_token(client)
    before = await _unused_recovery_codes(db_session)
    await _set_hash_elsewhere(db_session)

    r = await _mfa(client, pending, codes[0])
    assert r.status_code == 401
    assert await _unused_recovery_codes(db_session) == before
    failed = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "mfa.failed"))).scalars().all()
    assert failed == []


@pytest.mark.asyncio
async def test_password_change_during_the_code_check_ends_the_login(client, db_session, clock, monkeypatch):
    _, secret, _ = await _owner_with_2fa(client, clock)
    pending = await _mfa_token(client)
    original = vault.read_secret_plaintext

    async def change_meanwhile(session, keyring, secret_id):
        await session.execute(update(User).values(password_hash=security.hash_password(NEW_PASSWORD)))
        return await original(session, keyring, secret_id)

    monkeypatch.setattr(vault, "read_secret_plaintext", change_meanwhile)
    clock[0] += 30
    code = pyotp.TOTP(secret).at(clock[0])
    r = await _mfa(client, pending, code)
    assert r.status_code == 401
    assert "access_token" not in r.json()

    monkeypatch.setattr(vault, "read_secret_plaintext", original)
    fresh = await _mfa_token(client, NEW_PASSWORD)
    r = await _mfa(client, fresh, code)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_password_change_during_the_code_check_does_not_count_as_a_failure(
    client, db_session, clock, monkeypatch
):
    _, secret, _ = await _owner_with_2fa(client, clock)
    pending = await _mfa_token(client)
    original = vault.read_secret_plaintext

    async def change_meanwhile(session, keyring, secret_id):
        await session.execute(update(User).values(password_hash=security.hash_password(NEW_PASSWORD)))
        return await original(session, keyring, secret_id)

    monkeypatch.setattr(vault, "read_secret_plaintext", change_meanwhile)
    clock[0] += 30
    assert (await _mfa(client, pending, pyotp.TOTP(secret).at(clock[0]))).status_code == 401
    assert not any(login_limit._by_account.values())
    assert not any(login_limit._by_ip.values())
    assert not any(login_limit._by_user.values())


@pytest.mark.asyncio
async def test_token_without_password_stamp_is_refused(client, db_session, test_settings, clock):
    _, secret, _ = await _owner_with_2fa(client, clock)
    user_id = (await db_session.execute(select(User.id))).scalar_one()
    bare = security.create_jwt(
        subject=user_id,
        token_type="mfa",
        secret=test_settings.get_or_create_jwt_secret(),
        ttl_seconds=300,
        extra_claims={"client_type": "web", "device_name": None, "user_agent": None, "ip": None},
    )
    clock[0] += 30
    assert (await _mfa(client, bare, pyotp.TOTP(secret).at(clock[0]))).status_code == 401


@pytest.mark.asyncio
async def test_token_does_not_carry_the_password_hash(client, db_session, clock):
    await _owner_with_2fa(client, clock)
    pending = await _mfa_token(client)
    stored = (await db_session.execute(select(User.password_hash))).scalar_one()
    text = json.dumps(_claims(pending))
    assert stored not in text
    assert hashlib.sha256(stored.encode()).hexdigest()[:16] not in text


def test_stamp_needs_the_server_secret():
    stored = security.hash_password(PASSWORD)
    assert auth_service._password_stamp("eins", stored) != auth_service._password_stamp("zwei", stored)


@pytest.mark.asyncio
async def test_token_stamp_is_keyed_with_the_server_secret(client, db_session, test_settings, clock):
    await _owner_with_2fa(client, clock)
    pending = await _mfa_token(client)
    stored = (await db_session.execute(select(User.password_hash))).scalar_one()
    stamp = _claims(pending)["pws"]
    assert stamp == auth_service._password_stamp(test_settings.get_or_create_jwt_secret(), stored)
    assert stamp != auth_service._password_stamp("", stored)
    assert stamp != hashlib.sha256(stored.encode()).hexdigest()[: len(stamp)]


@pytest.mark.asyncio
async def test_rehash_at_login_keeps_the_pending_login_valid(client, db_session, clock):
    _, secret, _ = await _owner_with_2fa(client, clock)
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    await db_session.execute(update(User).values(password_hash=weak))
    await db_session.commit()
    assert security.needs_rehash(weak)

    pending = await _mfa_token(client)
    db_session.expire_all()
    stored = (await db_session.execute(select(User.password_hash))).scalar_one()
    assert stored != weak

    clock[0] += 30
    r = await _mfa(client, pending, pyotp.TOTP(secret).at(clock[0]))
    assert r.status_code == 200, r.text
