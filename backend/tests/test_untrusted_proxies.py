"""Hinweis auf einen Proxy, der nicht eingetragen ist (`core.untrusted_proxies`, Anmeldung, Übersicht)."""

from __future__ import annotations

import ipaddress

import pytest
from httpx import ASGITransport, AsyncClient
from totp_helpers import setup_confirm_code

from nodvard_deck.core import trusted_proxies, untrusted_proxies

PROXY = "172.18.0.5"
NOW = 1_800_000_000.0
OWNER = {"username": "nico", "password": "correct-horse-battery"}
FORWARDED_FOR = {"X-Forwarded-For": "192.168.2.40"}


async def _owner(client) -> dict:
    await client.post("/api/v1/auth/bootstrap", json={**OWNER, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json=OWNER)
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _login_via(peer: str, headers: dict, password: str = OWNER["password"], username: str = OWNER["username"]) -> int:
    from nodvard_deck.main import app

    transport = ASGITransport(app=app, client=(peer, 4711), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as proxy:
        answer = await proxy.post("/api/v1/auth/login", json={"username": username, "password": password}, headers=headers)
    return answer.status_code


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


def _seen() -> list[str]:
    return [address for address, _ in untrusted_proxies.recent(trusted_proxies.current())]


@pytest.mark.asyncio
async def test_a_login_through_a_proxy_that_is_not_entered_is_remembered(client):
    await _owner(client)
    assert await _login_via(PROXY, FORWARDED_FOR) == 200
    assert _seen() == [PROXY]


@pytest.mark.asyncio
@pytest.mark.parametrize(("peer", "headers", "password"), [
    (PROXY, FORWARDED_FOR, "falsch-falsch-falsch"),
    (PROXY, {}, OWNER["password"]),
    (PROXY, {"X-Forwarded-Proto": "https"}, OWNER["password"]),
    # Nur `X-Forwarded-For` wertet Deck aus; ein Eintrag hülfe bei `Forwarded` allein nicht.
    (PROXY, {"Forwarded": "for=192.168.2.40;proto=https"}, OWNER["password"]),
    ("203.0.113.9", FORWARDED_FOR, OWNER["password"]),
    ("2001:db8::9", FORWARDED_FOR, OWNER["password"]),
], ids=["falsches-passwort", "ohne", "nur-proto", "nur-forwarded", "internet-v4", "internet-v6"])
async def test_nothing_is_remembered_without_a_login_through_a_home_proxy(client, peer, headers, password):
    await _owner(client)
    await _login_via(peer, headers, password)
    assert _seen() == []


@pytest.mark.asyncio
async def test_a_login_that_still_needs_the_second_factor_counts_too(client):
    headers = await _owner(client)
    setup = await client.post("/api/v1/me/totp/setup", json={"current_password": OWNER["password"]}, headers=headers)
    confirm = await client.post(
        "/api/v1/me/totp/confirm", json={"code": setup_confirm_code(setup.json()["secret"])}, headers=headers
    )
    assert confirm.status_code == 200
    assert await _login_via(PROXY, FORWARDED_FOR) == 202
    assert _seen() == [PROXY]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["viewer", "admin"])
async def test_logins_of_other_accounts_are_not_remembered(client, db_session, role):
    await _owner(client)
    await _account(client, db_session, f"{role}1", role)
    assert await _login_via("192.168.5.50", FORWARDED_FOR, "whatever-1234", f"{role}1") == 200
    assert _seen() == []


@pytest.mark.asyncio
async def test_an_entered_proxy_is_neither_remembered_nor_shown(client):
    await _owner(client)
    trusted_proxies.apply([PROXY])
    assert await _login_via(PROXY, FORWARDED_FOR) == 200
    trusted_proxies.reset()
    assert _seen() == []
    untrusted_proxies.note(ipaddress.ip_address(PROXY))
    trusted_proxies.apply(["172.18.0.0/28"])
    assert _seen() == []


@pytest.mark.parametrize(("address", "home"), [
    ("10.1.2.3", True), ("172.31.0.1", True), ("192.168.2.1", True), ("127.0.0.1", True), ("169.254.3.4", True),
    ("100.64.1.2", True), ("::1", True), ("fd12::1", True), ("fe80::1", True),
    ("100.128.0.1", False), ("172.32.0.1", False), ("8.8.8.8", False), ("2003::1", False),
])
def test_only_addresses_from_the_home_network_are_kept(address, home):
    untrusted_proxies.note(ipaddress.ip_address(address), NOW)
    assert (untrusted_proxies.recent(trusted_proxies.current(), NOW) != []) is home


def test_only_address_and_time_are_kept_newest_first_for_a_week():
    untrusted_proxies.note(ipaddress.ip_address("10.0.0.2"), NOW - 8 * 86400)
    untrusted_proxies.note(ipaddress.ip_address("10.0.0.3"), NOW - 60)
    untrusted_proxies.note(ipaddress.ip_address("fd00::4"), NOW)
    assert untrusted_proxies.recent(trusted_proxies.current(), NOW) == [
        ("fd00::4", NOW), ("10.0.0.3", NOW - 60),
    ]


def test_at_most_eight_addresses_the_longest_unseen_go_first():
    for i in range(8):
        untrusted_proxies.note(ipaddress.ip_address(f"192.168.5.{i}"), NOW + i)
    untrusted_proxies.note(ipaddress.ip_address("192.168.5.0"), NOW + 8)
    untrusted_proxies.note(ipaddress.ip_address("192.168.5.9"), NOW + 9)
    seen = [address for address, _ in untrusted_proxies.recent(trusted_proxies.current(), NOW + 9)]
    assert len(seen) == 8 and "192.168.5.0" in seen and "192.168.5.1" not in seen


@pytest.mark.asyncio
async def test_only_the_owner_sees_the_proxy(client, db_session):
    owner = await _owner(client)
    as_viewer = await _account(client, db_session, "viewer1", "viewer")
    as_admin = await _account(client, db_session, "admin1", "admin")

    assert await _login_via(PROXY, FORWARDED_FOR) == 200
    body = (await client.get("/api/v1/overview", headers=owner)).json()
    assert [p["address"] for p in body["untrusted_proxies"]] == [PROXY]
    connection = (await client.get("/api/v1/system/connection", headers=owner)).json()
    assert [p["address"] for p in connection["untrusted_proxies"]] == [PROXY]
    assert (await client.get("/api/v1/overview", headers=as_viewer)).json()["untrusted_proxies"] == []
    assert (await client.get("/api/v1/overview", headers=as_admin)).json()["untrusted_proxies"] == []
    admin_connection = await client.get("/api/v1/system/connection", headers=as_admin)
    assert admin_connection.status_code == 200 and admin_connection.json()["untrusted_proxies"] == []
