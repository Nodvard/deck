"""Zwei gleichzeitige Ersteinrichtungen dürfen nur einen Inhaber anlegen und nie mit 500 enden."""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from nodvard_deck.core import security
from nodvard_deck.models import User
from nodvard_deck.services import auth as auth_service
from nodvard_deck.services import settings as settings_service
from sqlalchemy import select

PASSWORD = "correct-horse-battery"
SETUP_CODE = "TEST-CODE-2345"


def _both_hash_together(monkeypatch) -> None:
    """Lässt beide Anfragen erst nach dem Hashen weiterlaufen, wenn beide so weit sind (höchstens zwei Sekunden)."""
    real = security.hash_password_async
    barrier = asyncio.Barrier(2)

    async def _wrapped(password, *, signed_in=False):
        result = await real(password, signed_in=signed_in)
        with contextlib.suppress(TimeoutError, asyncio.BrokenBarrierError):
            await asyncio.wait_for(barrier.wait(), timeout=2)
        return result

    monkeypatch.setattr(security, "hash_password_async", _wrapped)


def _bootstrap(client, username: str):
    return client.post(
        "/api/v1/auth/bootstrap", json={"username": username, "password": PASSWORD, "setup_code": SETUP_CODE}
    )


async def _users(file_db) -> list[User]:
    async with file_db() as session:
        return list((await session.execute(select(User))).scalars())


@pytest.mark.asyncio
async def test_two_names_make_one_owner(client, file_db, monkeypatch):
    _both_hash_together(monkeypatch)
    first, second = await asyncio.gather(_bootstrap(client, "nico"), _bootstrap(client, "anna"))

    assert sorted([first.status_code, second.status_code]) == [201, 409], (first.text, second.text)
    users = await _users(file_db)
    assert len(users) == 1
    assert users[0].is_owner


@pytest.mark.asyncio
async def test_two_names_make_one_owner_without_a_unique_clash(client, file_db, monkeypatch):
    # Rollen und Einstellung gibt es schon, also stößt keine andere Zeile an eine Eindeutigkeitsregel.
    async with file_db() as session:
        await auth_service.ensure_builtin_roles(session)
        await settings_service.set_global(session, "ssh.confirm_new_host_keys", True)
        await session.commit()
    _both_hash_together(monkeypatch)
    first, second = await asyncio.gather(_bootstrap(client, "nico"), _bootstrap(client, "anna"))

    assert sorted([first.status_code, second.status_code]) == [201, 409], (first.text, second.text)
    assert [u.is_owner for u in await _users(file_db)] == [True]


@pytest.mark.asyncio
async def test_same_name_is_409_not_500(client, file_db, monkeypatch):
    _both_hash_together(monkeypatch)
    first, second = await asyncio.gather(_bootstrap(client, "nico"), _bootstrap(client, "nico"))

    assert sorted([first.status_code, second.status_code]) == [201, 409], (first.text, second.text)
    assert len(await _users(file_db)) == 1


@pytest.mark.asyncio
async def test_duplicate_from_another_process_is_409(client, file_db, monkeypatch):
    # Ein zweiter Prozess teilt die Sperre nicht: dann meldet erst die Datenbank den doppelten Namen.
    async with file_db() as session:
        session.add(User(username="nico", password_hash="x", is_owner=False, is_active=True))
        await session.commit()

    async def _no_user_yet(session):
        return True

    monkeypatch.setattr(auth_service, "needs_bootstrap", _no_user_yet)
    r = await _bootstrap(client, "nico")

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "Es existiert bereits mindestens ein Nutzer."
    assert len(await _users(file_db)) == 1


@pytest.mark.asyncio
async def test_single_bootstrap_unchanged(client, file_db):
    r = await _bootstrap(client, "Nico")

    assert r.status_code == 201, r.text
    assert r.json()["username"] == "nico"
    assert r.json()["is_owner"] is True
    users = await _users(file_db)
    assert [(u.username, u.is_owner) for u in users] == [("nico", True)]
    login = await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})
    assert login.status_code == 200, login.text
