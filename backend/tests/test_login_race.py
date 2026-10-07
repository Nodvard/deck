"""Die Passwort-Pruefung beim Anmelden dauert (Warteschlange, Argon2; auf einem Pi Sekunden).
Waehrenddessen darf die Anfrage keine Datenbankverbindung festhalten, und aus der Anmeldung darf
keine Sitzung werden, wenn das Konto in der Zwischenzeit deaktiviert, geloescht oder das Passwort
geaendert wurde (das Abmelden aller Geraete ist dann schon gelaufen).

Die Rennen werden nachgestellt: eine Hilfe fuehrt nach der echten Pruefung eine Aenderung ueber
eine zweite Datenbankverbindung (`file_db`) aus und gibt erst dann das Ergebnis zurueck."""

from __future__ import annotations

import asyncio
import threading

import pytest
from argon2 import PasswordHasher
from nodvard_deck.core import security
from nodvard_deck.models import AuditEntry, RecoveryCode, RefreshToken, User
from nodvard_deck.services import auth as auth_service
from sqlalchemy import select, update
from totp_helpers import setup_confirm_code

PASSWORD = "correct-horse-battery"
OTHER_PASSWORD = "ein-ganz-anderes-passwort"
# Schwaecher als die Voreinstellung: `security.needs_rehash` ist dafuer wahr.
_OLD_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _owner_token(client) -> str:
    r = await client.post(
        "/api/v1/auth/bootstrap", json={"username": "nico", "password": PASSWORD, "setup_code": "TEST-CODE-2345"}
    )
    assert r.status_code == 201, r.text
    login = await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _create_anna(client) -> None:
    r = await client.post(
        "/api/v1/users", json={"username": "anna", "password": PASSWORD}, headers=_h(await _owner_token(client))
    )
    assert r.status_code == 201, r.text


async def _change_user(file_db, username: str, **values) -> None:
    async with file_db() as session:
        await session.execute(update(User).where(User.username == username).values(**values))
        await session.commit()


async def _user_row(file_db, username: str) -> User:
    async with file_db() as session:
        return (await session.execute(select(User).where(User.username == username))).scalar_one()


async def _refresh_tokens(file_db, user_id: str) -> list[RefreshToken]:
    async with file_db() as session:
        return list((await session.execute(select(RefreshToken).where(RefreshToken.user_id == user_id))).scalars())


def _after_verifying(monkeypatch, action):
    """Fuehrt `action` einmal aus, sobald die Passwort-Pruefung fertig ist -- so, als haette sich
    das Konto waehrend der Pruefung geaendert."""
    real = security.verify_password_async
    done = False

    async def _wrapped(password, password_hash, *, signed_in=False):
        nonlocal done
        result = await real(password, password_hash, signed_in=signed_in)
        if not done:
            done = True
            await action()
        return result

    monkeypatch.setattr(security, "verify_password_async", _wrapped)


@pytest.mark.asyncio
async def test_login_does_not_hold_a_database_connection_while_waiting_for_the_hash(tmp_path, test_settings, monkeypatch):
    """Mit einem Pool aus nur einer Verbindung: waehrend die Pruefung laeuft, bekommt eine andere
    Anfrage die Verbindung. Mit festgehaltener Verbindung liefe sie in die Zeitgrenze des Pools."""
    from nodvard_deck.models import Base
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}", pool_size=1, max_overflow=0, pool_timeout=1
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        setup.add(User(username="anna", password_hash=security.hash_password(PASSWORD), is_active=True))
        await setup.commit()

    started = threading.Event()
    release = threading.Event()
    real = security.verify_password

    def _slow(password, password_hash):
        started.set()
        release.wait(10)
        return real(password, password_hash)

    monkeypatch.setattr(security, "verify_password", _slow)
    session = AsyncSession(engine, expire_on_commit=False)
    login = asyncio.create_task(auth_service.login(session, test_settings, username="anna", password=PASSWORD))
    try:
        async with asyncio.timeout(5):
            while not started.is_set():
                await asyncio.sleep(0.01)
        async with AsyncSession(engine) as other_request:
            await other_request.execute(text("select 1"))
    finally:
        release.set()
    tokens = await login
    assert tokens.user.username == "anna"
    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_account_deactivated_during_the_password_check_gets_no_session(client, file_db, monkeypatch):
    await _create_anna(client)
    anna = await _user_row(file_db, "anna")
    _after_verifying(monkeypatch, lambda: _change_user(file_db, "anna", is_active=False))

    r = await client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD})

    assert r.status_code == 401
    assert r.json()["detail"] == "Ungültiger Benutzername oder Passwort."
    assert await _refresh_tokens(file_db, anna.id) == []
    async with file_db() as session:
        failed = (
            await session.execute(
                select(AuditEntry).where(AuditEntry.action == "login.failed", AuditEntry.actor_id == anna.id)
            )
        ).scalars().all()
    assert [e.reason for e in failed] == ["Konto oder Passwort wurden während der Anmeldung geändert."]


@pytest.mark.asyncio
async def test_password_changed_during_the_password_check_gets_no_session(client, file_db, monkeypatch):
    await _create_anna(client)
    anna = await _user_row(file_db, "anna")
    new_hash = security.hash_password(OTHER_PASSWORD)
    _after_verifying(monkeypatch, lambda: _change_user(file_db, "anna", password_hash=new_hash))

    r = await client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD})

    assert r.status_code == 401
    assert await _refresh_tokens(file_db, anna.id) == []
    assert (await _user_row(file_db, "anna")).password_hash == new_hash


@pytest.mark.asyncio
async def test_rehash_does_not_overwrite_a_password_changed_in_the_meantime(client, file_db, monkeypatch):
    """Ist der gespeicherte Hash veraltet, wird er nach der Anmeldung neu berechnet. Aendert in der
    Zwischenzeit jemand das Passwort, bleibt das neue Passwort stehen."""
    await _create_anna(client)
    await _change_user(file_db, "anna", password_hash=_OLD_HASHER.hash(PASSWORD))
    assert security.needs_rehash((await _user_row(file_db, "anna")).password_hash)
    new_hash = security.hash_password(OTHER_PASSWORD)

    real = security.hash_password_async
    done = False

    async def _wrapped(password, *, signed_in=False):
        nonlocal done
        result = await real(password, signed_in=signed_in)
        if not done:
            done = True
            await _change_user(file_db, "anna", password_hash=new_hash)
        return result

    monkeypatch.setattr(security, "hash_password_async", _wrapped)

    r = await client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD})

    assert r.status_code == 401
    assert (await _user_row(file_db, "anna")).password_hash == new_hash


@pytest.mark.asyncio
async def test_login_still_replaces_an_outdated_hash(client, file_db):
    await _create_anna(client)
    await _change_user(file_db, "anna", password_hash=_OLD_HASHER.hash(PASSWORD))

    r = await client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD})

    assert r.status_code == 200, r.text
    stored = (await _user_row(file_db, "anna")).password_hash
    assert not security.needs_rehash(stored)
    assert security.verify_password(PASSWORD, stored)


@pytest.mark.asyncio
async def test_rehash_is_only_stored_for_the_hash_it_was_computed_from(client, file_db):
    await _create_anna(client)
    anna = await _user_row(file_db, "anna")
    async with file_db() as session:
        user = await session.get(User, anna.id)
        await auth_service._store_rehash(session, user, old_hash="nicht-der-gespeicherte-hash", new_hash="neu")
        await session.commit()
        assert user.password_hash == anna.password_hash
    assert (await _user_row(file_db, "anna")).password_hash == anna.password_hash

    async with file_db() as session:
        user = await session.get(User, anna.id)
        await auth_service._store_rehash(session, user, old_hash=anna.password_hash, new_hash="neu")
        assert user.password_hash == "neu"
        assert user not in session.dirty
        await session.commit()
    assert (await _user_row(file_db, "anna")).password_hash == "neu"


# ---------------------------------------------------------------------------
# Wiederherstellungs-Code: die Pruefung aller Codes dauert ebenfalls
# ---------------------------------------------------------------------------


async def _owner_with_recovery_codes(client) -> list[str]:
    token = await _owner_token(client)
    secret = (
        await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=_h(token))
    ).json()["secret"]
    confirm = await client.post("/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret)}, headers=_h(token))
    assert confirm.status_code == 200, confirm.text
    return confirm.json()["recovery_codes"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"is_active": False}, id="deaktiviert"),
        pytest.param({"password_hash": security.hash_password(OTHER_PASSWORD)}, id="passwort-geaendert"),
    ],
)
async def test_recovery_code_login_ends_when_the_account_changes_during_the_check(client, file_db, monkeypatch, change):
    """Ein Wiederherstellungs-Code, der waehrend der Pruefung an ein deaktiviertes oder neu
    gesetztes Konto gerichtet ist, ergibt keine Sitzung und wird nicht verbraucht."""
    codes = await _owner_with_recovery_codes(client)
    nico = await _user_row(file_db, "nico")
    first = await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})
    assert first.status_code == 202, first.text
    mfa_token = first.json()["mfa_token"]
    tokens_before = len(await _refresh_tokens(file_db, nico.id))

    real = security.run_hashing
    done = False

    async def _wrapped(func, *args, **kwargs):
        nonlocal done
        result = await real(func, *args, **kwargs)
        if func.__name__ == "_find" and not done:
            done = True
            await _change_user(file_db, "nico", **change)
        return result

    monkeypatch.setattr(security, "run_hashing", _wrapped)

    r = await client.post("/api/v1/auth/mfa", json={"mfa_token": mfa_token, "code": codes[0]})

    assert r.status_code == 401, r.text
    assert len(await _refresh_tokens(file_db, nico.id)) == tokens_before
    async with file_db() as session:
        used = (await session.execute(select(RecoveryCode).where(RecoveryCode.used_at.is_not(None)))).scalars().all()
    assert used == []
