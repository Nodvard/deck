"""Anmeldung, Erneuern und Abmelden mit einem einzelnen Surrogat-Zeichen im Benutzernamen oder in einem Token."""

import pytest
from nodvard_deck.api.v1 import auth as auth_api
from nodvard_deck.config import Settings
from nodvard_deck.core import login_limit, rate_limit, security
from nodvard_deck.models import AuditEntry
from nodvard_deck.services import auth as auth_service
from sqlalchemy import select

JSON = {"content-type": "application/json"}
SURROGATE_LOGIN = b'{"username": "a\\ud800", "password": "falsch"}'
PLAIN_LOGIN = b'{"username": "niemand", "password": "falsch"}'
LOCAL = "127.0.0.1"
FOREIGN = "203.0.113.7"


async def _bootstrap(client):
    r = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "nico", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    assert r.status_code == 201, r.text


async def _statuses(client, body: bytes, tries: int) -> list[int]:
    codes = []
    for _ in range(tries):
        r = await client.post("/api/v1/auth/login", content=body, headers=JSON)
        codes.append(r.status_code)
    return codes


@pytest.fixture
def from_ip(monkeypatch):
    def use(ip: str) -> None:
        monkeypatch.setattr(auth_api, "_client_ip", lambda request: ip)

    return use


@pytest.mark.asyncio
@pytest.mark.parametrize("ip", [LOCAL, FOREIGN])
async def test_login_with_surrogate_name_is_rejected(client, from_ip, ip):
    from_ip(ip)
    await _bootstrap(client)
    r = await client.post("/api/v1/auth/login", content=SURROGATE_LOGIN, headers=JSON)
    assert r.status_code == 401, r.text
    assert r.json() == {"detail": "Ungültiger Benutzername oder Passwort."}


@pytest.mark.asyncio
@pytest.mark.parametrize("ip", [LOCAL, FOREIGN])
async def test_surrogate_name_is_throttled_like_any_unknown_name(client, from_ip, ip):
    from_ip(ip)
    await _bootstrap(client)
    tries = login_limit.MAX_FAILURES_PER_USER + 2
    plain = await _statuses(client, PLAIN_LOGIN, tries)
    login_limit.reset()
    surrogate = await _statuses(client, SURROGATE_LOGIN, tries)
    assert surrogate == plain
    assert surrogate[0] == 401
    assert surrogate[-1] == 429


@pytest.mark.asyncio
async def test_surrogate_name_is_logged_without_the_typed_text(client, db_session, from_ip):
    from_ip(LOCAL)
    await _bootstrap(client)
    await client.post("/api/v1/auth/login", content=SURROGATE_LOGIN, headers=JSON)
    rows = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "login.failed"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].reason == "Unbekannter Benutzername."
    assert rows[0].detail["username_length"] == 2


def test_unknown_login_audit_takes_a_surrogate(tmp_path):
    settings = Settings(data_dir=tmp_path)
    ref = auth_service.unknown_login_audit(settings, "a\ud800")
    assert ref["username_length"] == 2
    assert len(ref["username_ref"]) == 12
    assert ref["username_ref"] != auth_service.unknown_login_audit(settings, "a\udfff")["username_ref"]


def test_name_slot_takes_a_surrogate():
    slot = login_limit._name_slot("a\ud800")
    assert 0 <= slot < login_limit.NAME_SLOTS
    assert slot == login_limit._name_slot("a\ud800")
    assert login_limit._name_slot("demo") == login_limit._name_slot("demo")


def _brake(username: str, tries: int = 12) -> list[int]:
    login_limit.reset()
    waits = []
    for _ in range(tries):
        try:
            login_limit.failed(login_limit.begin(FOREIGN, username, brake_name=True, password_login=True))
            waits.append(0)
        except login_limit.Locked as exc:
            waits.append(exc.retry_after)
    return waits


def test_name_brake_counts_a_surrogate_name(monkeypatch):
    monkeypatch.setattr(login_limit, "_now", lambda: 1000.0)
    waits = _brake("a\ud800")
    assert waits == _brake("demo")
    assert waits[0] == 0
    assert waits[-1] > 0


def test_mfa_slot_takes_a_surrogate_token():
    attempt = login_limit.begin(LOCAL, "demo", mfa_token="a\ud800")
    assert attempt.mfa_key == login_limit._mfa_key("a\ud800")
    assert attempt.mfa_key != login_limit._mfa_key("a\udfff")


def test_decode_jwt_rejects_a_surrogate_token():
    with pytest.raises(security.TokenError):
        security.decode_jwt("a\ud800", secret="s" * 32, expected_type="mfa")


@pytest.mark.asyncio
async def test_mfa_with_surrogate_token_is_rejected(client, from_ip):
    from_ip(LOCAL)
    await _bootstrap(client)
    r = await client.post(
        "/api/v1/auth/mfa", content=b'{"mfa_token": "a\\ud800", "code": "123456"}', headers=JSON
    )
    assert r.status_code == 401, r.text
    assert "\\ud800" not in r.text


SURROGATE_REFRESH = b'{"refresh_token": "x\\ud800"}'


@pytest.fixture
def hashed_tokens(monkeypatch):
    seen = []
    original = security.hash_opaque_token

    def spy(raw_token: str) -> str:
        seen.append(raw_token)
        return original(raw_token)

    monkeypatch.setattr(security, "hash_opaque_token", spy)
    return seen


@pytest.mark.asyncio
async def test_refresh_with_surrogate_token_is_rejected(client, from_ip, hashed_tokens):
    from_ip(LOCAL)
    await _bootstrap(client)
    hashed_tokens.clear()
    r = await client.post("/api/v1/auth/refresh", content=SURROGATE_REFRESH, headers=JSON)
    assert r.status_code == 401, r.text
    assert r.json() == {"detail": "Refresh-Token ungültig, widerrufen oder abgelaufen."}
    assert hashed_tokens == []


@pytest.mark.asyncio
async def test_surrogate_refresh_token_counts_like_any_invalid_token(client, from_ip, monkeypatch):
    from_ip(FOREIGN)
    monkeypatch.setattr(auth_service._refresh_address_window, "limit", 3)
    monkeypatch.setattr(rate_limit, "_now", lambda: 1000.0)
    codes = []
    for _ in range(4):
        r = await client.post("/api/v1/auth/refresh", content=SURROGATE_REFRESH, headers=JSON)
        codes.append(r.status_code)
    assert codes == [401, 401, 401, 429]


@pytest.mark.asyncio
async def test_logout_with_surrogate_token_succeeds_without_lookup(client, from_ip, hashed_tokens):
    from_ip(LOCAL)
    await _bootstrap(client)
    hashed_tokens.clear()
    r = await client.post("/api/v1/auth/logout", content=SURROGATE_REFRESH, headers=JSON)
    assert r.status_code == 204, r.text
    assert hashed_tokens == []


@pytest.mark.asyncio
async def test_logout_with_surrogate_token_keeps_other_sessions(client, from_ip):
    from_ip(LOCAL)
    await _bootstrap(client)
    r = await client.post(
        "/api/v1/auth/login", json={"username": "nico", "password": "correct-horse-battery", "client_type": "cli"}
    )
    token = r.json()["refresh_token"]
    await client.post("/api/v1/auth/logout", content=SURROGATE_REFRESH, headers=JSON)
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
    assert r.status_code == 200, r.text


def test_hash_opaque_token_stays_strict():
    with pytest.raises(UnicodeEncodeError):
        security.hash_opaque_token("x\ud800")


def test_different_surrogate_names_get_their_own_slot():
    names = [f"a{chr(code)}" for code in range(0xD800, 0xD810)]
    assert len({login_limit._name_slot(name) for name in names}) > 1
