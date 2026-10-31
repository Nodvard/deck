"""Hinweis auf alte Umgebungsvariablen in der Übersicht: nur die Namen, nur für den Inhaber."""

from __future__ import annotations

import pytest
from nodvard_deck import config

OWNER = {"username": "nico", "password": "correct-horse-battery"}


async def _owner(client) -> dict:
    await client.post("/api/v1/auth/bootstrap", json={**OWNER, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json=OWNER)
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _account(client, db_session, username: str, role: str) -> dict:
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password("whatever-1234"), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": "whatever-1234"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_only_the_owner_sees_old_names_with_their_new_names(client, db_session, monkeypatch):
    monkeypatch.setattr(config, "_warned_legacy", {"LATTICE_JWT_SECRET", "LATTICE_DATA_DIR"})
    owner = await _owner(client)
    as_admin = await _account(client, db_session, "admin1", "admin")
    as_viewer = await _account(client, db_session, "viewer1", "viewer")

    body = (await client.get("/api/v1/overview", headers=owner)).json()
    assert body["legacy_env"] == [
        {"old": "LATTICE_DATA_DIR", "new": "NODVARD_DECK_DATA_DIR", "new_set": False},
        {"old": "LATTICE_JWT_SECRET", "new": "NODVARD_DECK_JWT_SECRET", "new_set": False},
    ]
    assert (await client.get("/api/v1/overview", headers=as_admin)).json()["legacy_env"] == []
    assert (await client.get("/api/v1/overview", headers=as_viewer)).json()["legacy_env"] == []


@pytest.mark.asyncio
async def test_without_old_names_there_is_no_hint(client, monkeypatch):
    monkeypatch.setattr(config, "_warned_legacy", set())
    owner = await _owner(client)
    assert (await client.get("/api/v1/overview", headers=owner)).json()["legacy_env"] == []


@pytest.mark.asyncio
async def test_an_old_name_next_to_its_new_one_says_it_can_go(client, monkeypatch):
    monkeypatch.setattr(config, "_warned_legacy", {"LATTICE_LOG_JSON", "LATTICE_ENV"})
    monkeypatch.setattr(config, "_shadowed_legacy", {"LATTICE_LOG_JSON"})
    owner = await _owner(client)
    assert (await client.get("/api/v1/overview", headers=owner)).json()["legacy_env"] == [
        {"old": "LATTICE_ENV", "new": "NODVARD_DECK_ENV", "new_set": False},
        {"old": "LATTICE_LOG_JSON", "new": "NODVARD_DECK_LOG_JSON", "new_set": True},
    ]
