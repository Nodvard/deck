"""TOTP-Grundgeruest: Setup -> Confirm -> aktiv -> Disable, plus der Login-Umweg ueber
/auth/mfa. Spielt exakt den Flow nach, der beim manuellen Boot-Test verifiziert wurde.
"""

from __future__ import annotations

import pyotp
import pytest


async def _bootstrap_and_login(client, username="nico", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_totp_setup_returns_secret_and_uri(client):
    token = await _bootstrap_and_login(client)
    r = await client.post("/api/v1/me/totp/setup", headers=_auth_header(token))
    assert r.status_code == 200
    body = r.json()
    assert len(body["secret"]) >= 16
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert "issuer=Nodvard%20Deck" in body["otpauth_uri"]


@pytest.mark.asyncio
async def test_totp_confirm_wrong_code_rejected(client):
    token = await _bootstrap_and_login(client)
    await client.post("/api/v1/me/totp/setup", headers=_auth_header(token))
    r = await client.post(
        "/api/v1/me/totp/confirm", json={"code": "000000"}, headers=_auth_header(token)
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_totp_full_lifecycle(client):
    """Setup -> Confirm -> totp_enabled=true -> Login verlangt jetzt MFA -> Disable ->
    Login geht wieder ohne MFA. Identisch zum manuell verifizierten Boot-Test-Ablauf."""
    token = await _bootstrap_and_login(client)

    setup = (await client.post("/api/v1/me/totp/setup", headers=_auth_header(token))).json()
    secret = setup["secret"]
    code = pyotp.TOTP(secret).now()

    confirm = await client.post(
        "/api/v1/me/totp/confirm", json={"code": code}, headers=_auth_header(token)
    )
    assert confirm.status_code == 200
    assert len(confirm.json()["recovery_codes"]) == 10

    me = await client.get("/api/v1/me", headers=_auth_header(token))
    assert me.json()["totp_enabled"] is True

    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "nico", "password": "correct-horse-battery"},
    )
    assert login.status_code == 202
    assert login.json()["mfa_required"] is True
    mfa_token = login.json()["mfa_token"]

    mfa_wrong = await client.post(
        "/api/v1/auth/mfa", json={"mfa_token": mfa_token, "code": "000000"}
    )
    assert mfa_wrong.status_code == 401

    fresh_code = pyotp.TOTP(secret).now()
    mfa_ok = await client.post(
        "/api/v1/auth/mfa", json={"mfa_token": mfa_token, "code": fresh_code}
    )
    assert mfa_ok.status_code == 200
    assert mfa_ok.json()["access_token"]
    new_token = mfa_ok.json()["access_token"]

    disable = await client.request(
        "DELETE", "/api/v1/me/totp", json={"current_password": "correct-horse-battery"}, headers=_auth_header(new_token)
    )
    assert disable.status_code == 204

    me2 = await client.get("/api/v1/me", headers=_auth_header(new_token))
    assert me2.json()["totp_enabled"] is False

    login_again = await client.post(
        "/api/v1/auth/login",
        json={"username": "nico", "password": "correct-horse-battery"},
    )
    assert login_again.status_code == 200, "nach Disable darf kein MFA mehr verlangt werden"


@pytest.mark.asyncio
async def test_mfa_token_cannot_be_used_as_access_token(client):
    """Der `typ`-Claim (docs/00 D-07-Praezisierung) verhindert, dass ein kurzlebiges
    MFA-Zwischentoken faelschlich als Bearer-Access-Token akzeptiert wird."""
    token = await _bootstrap_and_login(client)
    setup = (await client.post("/api/v1/me/totp/setup", headers=_auth_header(token))).json()
    code = pyotp.TOTP(setup["secret"]).now()
    await client.post("/api/v1/me/totp/confirm", json={"code": code}, headers=_auth_header(token))

    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "nico", "password": "correct-horse-battery"},
    )
    mfa_token = login.json()["mfa_token"]

    r = await client.get("/api/v1/me", headers=_auth_header(mfa_token))
    assert r.status_code == 401
