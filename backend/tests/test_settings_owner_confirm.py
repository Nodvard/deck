"""Selbstständigkeit (`autonomy.mode`, `autonomy.max_risk`) und die Regel für neue Server-Schlüssel
(`ssh.confirm_new_host_keys`) ändert nur der Inhaber, mit seinem Passwort und, wenn Zwei-Faktor an ist, dem Code aus der
App. Eine gestohlene Sitzung allein reicht nicht, die eines Admins auch mit Passwort nicht. `PUT /settings/autonomy` setzt
Modus und Risikostufe mit einer Bestätigung (ein Code gilt nur einmal)."""

from __future__ import annotations

import time

import pyotp
import pytest
from nodvard_deck.core import security
from nodvard_deck.models import User
from nodvard_deck.services import auth as auth_service
from totp_helpers import setup_confirm_code

OWNER_PW = "correct-horse-battery"
ADMIN_PW = "admin-passwort-123"
PROTECTED = [("autonomy.mode", "full"), ("autonomy.max_risk", "high"), ("ssh.confirm_new_host_keys", False)]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(auth_service, "_totp_now", lambda: now[0])
    return now


@pytest.fixture
def host_keys_from_settings(test_settings):
    # Die Test-Einstellungen legen den Schalter sonst über die Umgebungsvariable fest (409).
    test_settings.ssh_confirm_new_host_keys = None


async def _owner(client) -> str:
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": OWNER_PW, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": OWNER_PW})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _admin(client, db_session) -> str:
    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username="admin2", password_hash=security.hash_password(ADMIN_PW), is_active=True)
    user.roles.append(roles["admin"])
    db_session.add(user)
    await db_session.flush()
    login = await client.post("/api/v1/auth/login", json={"username": "admin2", "password": ADMIN_PW})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _enable_totp(client, token: str) -> str:
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": OWNER_PW}, headers=_h(token))).json()["secret"]
    r = await client.post(
        "/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret, auth_service._totp_now())}, headers=_h(token)
    )
    assert r.status_code == 200, r.text
    return secret


async def _values(client, token: str) -> dict:
    return {row["key"]: row["value"] for row in (await client.get("/api/v1/settings", headers=_h(token))).json()}


@pytest.mark.asyncio
@pytest.mark.parametrize(("key", "value"), PROTECTED)
async def test_an_admin_cannot_change_them_even_with_the_password(client, db_session, host_keys_from_settings, key, value):
    owner = await _owner(client)
    admin = await _admin(client, db_session)
    before = await _values(client, owner)
    r = await client.put(f"/api/v1/settings/{key}", json={"value": value, "current_password": ADMIN_PW}, headers=_h(admin))
    assert r.status_code == 403, r.text
    assert "Inhaber" in r.json()["detail"]
    assert await _values(client, owner) == before
    denied = (await client.get("/api/v1/audit?action=system.settings.changed", headers=_h(owner))).json()
    assert [(e["target_id"], e["outcome"]) for e in denied] == [(key, "denied")]


@pytest.mark.asyncio
async def test_an_admin_cannot_use_the_combined_endpoint_either(client, db_session):
    owner = await _owner(client)
    admin = await _admin(client, db_session)
    body = {"mode": "full", "max_risk": "critical", "current_password": ADMIN_PW}
    r = await client.put("/api/v1/settings/autonomy", json=body, headers=_h(admin))
    assert r.status_code == 403, r.text
    values = await _values(client, owner)
    assert (values["autonomy.mode"], values["autonomy.max_risk"]) == ("propose", "low")


@pytest.mark.asyncio
async def test_other_settings_stay_open_for_admins(client, db_session):
    await _owner(client)
    admin = await _admin(client, db_session)
    r = await client.put("/api/v1/settings/audit.retention_days", json={"value": 30}, headers=_h(admin))
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("key", "value"), PROTECTED)
async def test_the_owner_needs_the_password(client, host_keys_from_settings, key, value):
    owner = await _owner(client)
    before = await _values(client, owner)

    missing = await client.put(f"/api/v1/settings/{key}", json={"value": value}, headers=_h(owner))
    assert missing.status_code == 403, missing.text
    assert missing.json()["code"] == "password_missing"
    wrong = await client.put(f"/api/v1/settings/{key}", json={"value": value, "current_password": "falsch-falsch"}, headers=_h(owner))
    assert wrong.status_code == 400, wrong.text
    assert await _values(client, owner) == before

    ok = await client.put(f"/api/v1/settings/{key}", json={"value": value, "current_password": OWNER_PW}, headers=_h(owner))
    assert ok.status_code == 200, ok.text
    assert (await _values(client, owner))[key] == value


@pytest.mark.asyncio
async def test_a_wrong_value_is_refused_before_the_password_is_asked(client):
    owner = await _owner(client)
    r = await client.put("/api/v1/settings/autonomy.mode", json={"value": "alles"}, headers=_h(owner))
    assert r.status_code == 422, r.text
    r = await client.put("/api/v1/settings/autonomy", json={"mode": "full", "max_risk": "riesig"}, headers=_h(owner))
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_with_two_factor_the_code_from_the_app_is_needed_too(client, clock, host_keys_from_settings):
    owner = await _owner(client)
    secret = await _enable_totp(client, owner)

    missing = await client.put(
        "/api/v1/settings/ssh.confirm_new_host_keys", json={"value": False, "current_password": OWNER_PW}, headers=_h(owner)
    )
    assert missing.status_code == 403, missing.text
    assert missing.json()["code"] == "totp_missing"
    assert (await _values(client, owner))["ssh.confirm_new_host_keys"] is True

    code = pyotp.TOTP(secret).at(clock[0])
    body = {"value": False, "current_password": OWNER_PW, "totp_code": code}
    ok = await client.put("/api/v1/settings/ssh.confirm_new_host_keys", json=body, headers=_h(owner))
    assert ok.status_code == 200, ok.text
    assert (await _values(client, owner))["ssh.confirm_new_host_keys"] is False

    again = await client.put("/api/v1/settings/autonomy.mode", json={**body, "value": "full"}, headers=_h(owner))
    assert again.status_code == 400, again.text
    assert again.json()["code"] == "totp_used"
    assert (await _values(client, owner))["autonomy.mode"] == "propose"


@pytest.mark.asyncio
async def test_the_combined_endpoint_sets_both_with_one_code(client, clock):
    owner = await _owner(client)
    secret = await _enable_totp(client, owner)
    body = {"mode": "full", "max_risk": "high", "current_password": OWNER_PW, "totp_code": pyotp.TOTP(secret).at(clock[0])}
    r = await client.put("/api/v1/settings/autonomy", json=body, headers=_h(owner))
    assert r.status_code == 200, r.text
    assert r.json() == {"mode": "full", "max_risk": "high"}
    values = await _values(client, owner)
    assert (values["autonomy.mode"], values["autonomy.max_risk"]) == ("full", "high")

    entries = (await client.get("/api/v1/audit?action=system.settings.changed", headers=_h(owner))).json()
    assert sorted((e["target_id"], e["detail"]["old"], e["detail"]["new"]) for e in entries) == [
        ("autonomy.max_risk", "low", "high"),
        ("autonomy.mode", "propose", "full"),
    ]


@pytest.mark.asyncio
async def test_the_combined_endpoint_logs_only_what_changed(client):
    owner = await _owner(client)
    r = await client.put(
        "/api/v1/settings/autonomy", json={"mode": "propose", "max_risk": "medium", "current_password": OWNER_PW}, headers=_h(owner)
    )
    assert r.status_code == 200, r.text
    entries = (await client.get("/api/v1/audit?action=system.settings.changed", headers=_h(owner))).json()
    assert [e["target_id"] for e in entries] == ["autonomy.max_risk"]
