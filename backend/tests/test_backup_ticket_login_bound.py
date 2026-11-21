"""Ein Download-Ticket der Sicherung gilt nur, solange die Anmeldung dahinter und das Passwort gleich bleiben."""

from __future__ import annotations

from pathlib import Path

import pytest
from nodvard_deck.core.backup import store
from nodvard_deck.core.backup.tickets import get_ticket_registry

from test_system_backups_api import ONE_TIME_PW, OWNER_PW, _overview, _prepare, _run, _set_key, env  # noqa: F401

NEW_PW = "ganz-neues-passwort-42"


async def _login(client, password: str = OWNER_PW) -> tuple[dict, str]:
    r = await client.post(
        "/api/v1/auth/login", json={"username": "owner1", "password": password, "client_type": "android"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["refresh_token"]


async def _owner_session(client) -> tuple[dict, str]:
    r = await client.post(
        "/api/v1/auth/bootstrap", json={"username": "owner1", "password": OWNER_PW, "setup_code": "TEST-CODE-2345"}
    )
    assert r.status_code in (200, 201), r.text
    return await _login(client)


async def _logout(client, refresh: str) -> None:
    assert (await client.post("/api/v1/auth/logout", json={"refresh_token": refresh})).status_code == 204


def _downloads(env) -> Path:  # noqa: F811
    return store.default_dir(env["settings"].data_dir) / ".downloads"


@pytest.mark.asyncio
async def test_logout_ends_the_ticket_and_deletes_the_file(client, env):  # noqa: F811
    headers, refresh = await _owner_session(client)
    ticket = await _prepare(client, headers, mode="passwort", password=ONE_TIME_PW)
    path = get_ticket_registry().get(ticket["ticket"]).path
    await _logout(client, refresh)
    assert (await client.get(ticket["url"])).status_code == 404
    assert not path.exists()
    assert get_ticket_registry().get(ticket["ticket"]) is None


@pytest.mark.asyncio
async def test_password_change_ends_the_ticket(client, env):  # noqa: F811
    headers, _ = await _owner_session(client)
    ticket = await _prepare(client, headers, mode="passwort", password=ONE_TIME_PW)
    r = await client.post(
        "/api/v1/me/password", json={"current_password": OWNER_PW, "new_password": NEW_PW}, headers=headers
    )
    assert r.status_code == 204, r.text
    assert (await client.get(ticket["url"])).status_code == 404


@pytest.mark.asyncio
async def test_password_reset_without_new_login_ends_the_ticket(client, env):  # noqa: F811
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from sqlalchemy import select

    headers, _ = await _owner_session(client)
    ticket = await _prepare(client, headers, mode="passwort", password=ONE_TIME_PW)
    async with env["sessionmaker"]() as session:
        user = (await session.execute(select(User).where(User.username == "owner1"))).scalar_one()
        user.password_hash = security.hash_password(NEW_PW)
        await session.commit()
    assert (await client.get(ticket["url"])).status_code == 404


async def _stored_ticket(client, headers) -> tuple[str, str]:
    await _set_key(client, headers)
    await _run(client, headers)
    [item] = (await _overview(client, headers))["backups"]
    r = await client.post(
        f"/api/v1/system/backups/{item['name']}/ticket", json={"current_password": OWNER_PW}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()["url"], item["name"]


@pytest.mark.asyncio
async def test_stored_backup_ticket_ends_with_the_login(client, env):  # noqa: F811
    headers, refresh = await _owner_session(client)
    other, _ = await _login(client)
    url, name = await _stored_ticket(client, headers)
    await _logout(client, refresh)
    assert (await client.get(url)).status_code == 404
    assert (Path((await _overview(client, other))["target"]["dir"]) / name).exists()


@pytest.mark.asyncio
async def test_stored_backup_ticket_ends_with_the_password(client, env):  # noqa: F811
    headers, _ = await _owner_session(client)
    url, _ = await _stored_ticket(client, headers)
    r = await client.post(
        "/api/v1/me/password", json={"current_password": OWNER_PW, "new_password": NEW_PW}, headers=headers
    )
    assert r.status_code == 204, r.text
    assert (await client.get(url)).status_code == 404


@pytest.mark.asyncio
async def test_other_login_ending_keeps_the_ticket(client, env):  # noqa: F811
    headers, _ = await _owner_session(client)
    _, other_refresh = await _login(client)
    ticket = await _prepare(client, headers, mode="passwort", password=ONE_TIME_PW)
    await _logout(client, other_refresh)
    assert (await client.get(ticket["url"])).status_code == 200


@pytest.mark.asyncio
async def test_renewed_login_keeps_the_ticket(client, env):  # noqa: F811
    headers, refresh = await _owner_session(client)
    ticket = await _prepare(client, headers, mode="passwort", password=ONE_TIME_PW)
    renewed = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert renewed.status_code == 200, renewed.text
    assert (await client.get(ticket["url"])).status_code == 200


@pytest.mark.asyncio
async def test_own_logout_ends_the_ticket_while_another_login_lives(client, env):  # noqa: F811
    headers, refresh = await _owner_session(client)
    await _login(client)
    ticket = await _prepare(client, headers, mode="passwort", password=ONE_TIME_PW)
    await _logout(client, refresh)
    assert (await client.get(ticket["url"])).status_code == 404


def _expire_after_first_check(monkeypatch) -> None:
    from nodvard_deck.api import deps
    from nodvard_deck.core import security

    real = security.decode_jwt
    calls = {"n": 0}

    def decode(token, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise security.TokenError("Signature has expired")
        return real(token, **kwargs)

    monkeypatch.setattr(deps.security, "decode_jwt", decode)


@pytest.mark.asyncio
async def test_token_expiring_during_the_request_keeps_the_ticket_bound(client, env, monkeypatch):  # noqa: F811
    headers, refresh = await _owner_session(client)
    await _login(client)
    _expire_after_first_check(monkeypatch)
    r = await client.post(
        "/api/v1/system/backups/download", json={"current_password": OWNER_PW, "mode": "passwort", "password": ONE_TIME_PW},
        headers=headers,
    )
    monkeypatch.undo()
    assert r.status_code == 202, r.text
    from nodvard_deck.services import backups as backups_service

    await backups_service.wait_for_tasks()
    assert get_ticket_registry().get(r.json()["ticket"]).login_id is not None
    await _logout(client, refresh)
    assert (await client.get(r.json()["url"])).status_code == 404


@pytest.mark.asyncio
async def test_token_expiring_during_the_request_keeps_the_stored_ticket_bound(client, env, monkeypatch):  # noqa: F811
    headers, refresh = await _owner_session(client)
    await _login(client)
    await _set_key(client, headers)
    await _run(client, headers)
    [item] = (await _overview(client, headers))["backups"]
    _expire_after_first_check(monkeypatch)
    r = await client.post(
        f"/api/v1/system/backups/{item['name']}/ticket", json={"current_password": OWNER_PW}, headers=headers
    )
    monkeypatch.undo()
    assert r.status_code == 200, r.text
    await _logout(client, refresh)
    assert (await client.get(r.json()["url"])).status_code == 404


@pytest.mark.asyncio
async def test_no_ticket_without_login_id(client, env):  # noqa: F811
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from sqlalchemy import select

    headers, _ = await _owner_session(client)
    await _login(client)
    await _set_key(client, headers)
    await _run(client, headers)
    [item] = (await _overview(client, headers))["backups"]
    async with env["sessionmaker"]() as session:
        owner_id = (await session.execute(select(User.id).where(User.username == "owner1"))).scalar_one()
    token = security.create_jwt(
        subject=owner_id, token_type="access", secret=env["settings"].get_or_create_jwt_secret(), ttl_seconds=300
    )
    legacy = {"Authorization": f"Bearer {token}"}
    before = len(get_ticket_registry()._tickets)
    r = await client.post(
        "/api/v1/system/backups/download", json={"current_password": OWNER_PW, "mode": "passwort", "password": ONE_TIME_PW},
        headers=legacy,
    )
    assert r.status_code == 401, r.text
    r = await client.post(
        f"/api/v1/system/backups/{item['name']}/ticket", json={"current_password": OWNER_PW}, headers=legacy
    )
    assert r.status_code == 401, r.text
    assert len(get_ticket_registry()._tickets) == before


@pytest.mark.asyncio
async def test_ticket_without_login_id_is_never_redeemed(client, env):  # noqa: F811
    from nodvard_deck.models import User
    from sqlalchemy import select

    headers, _ = await _owner_session(client)
    url, name = await _stored_ticket(client, headers)
    async with env["sessionmaker"]() as session:
        owner_id = (await session.execute(select(User.id).where(User.username == "owner1"))).scalar_one()
    found = get_ticket_registry().get(url.rsplit("/", 1)[-1])
    unbound = get_ticket_registry().create(
        user_id=owner_id, path=found.path, filename=name, delete_after=False, ready=True, stamp=found.stamp
    )
    assert (await client.get(url.replace(found.ticket, unbound.ticket))).status_code == 404
    assert (await client.get(url)).status_code == 200
