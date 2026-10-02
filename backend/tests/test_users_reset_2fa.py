"""POST /users/{id}/reset-2fa: Admin schaltet die 2FA eines anderen Nutzers ab."""

from __future__ import annotations

import pyotp
import pytest
from nodvard_deck.models import AuditEntry, RecoveryCode, RefreshToken, User
from sqlalchemy import select

PASSWORD = "correct-horse-battery"


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _login(client, username):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _enable_2fa(client, token) -> tuple[str, list[str]]:
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=_h(token))).json()["secret"]
    r = await client.post("/api/v1/me/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=_h(token))
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


async def _world(client):
    """Owner 'chef', Admin 'admin1' (users.write), Viewer 'viewer1'; admin1 und chef haben 2FA."""
    await client.post(
        "/api/v1/auth/bootstrap", json={"username": "chef", "password": PASSWORD, "setup_code": "TEST-CODE-2345"}
    )
    owner = await _login(client, "chef")
    roles = {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=_h(owner))).json()}
    ids = {}
    for name, role in (("admin1", "admin"), ("viewer1", "viewer"), ("viewer2", "viewer")):
        r = await client.post(
            "/api/v1/users", json={"username": name, "password": PASSWORD, "role_ids": [roles[role]]}, headers=_h(owner)
        )
        ids[name] = r.json()["id"]
    return owner, ids


@pytest.mark.asyncio
async def test_admin_resets_2fa_of_another_user(client, db_session):
    _, ids = await _world(client)
    admin = await _login(client, "admin1")
    viewer_token = await _login(client, "viewer1")
    await _enable_2fa(client, viewer_token)
    listed = {u["username"]: u for u in (await client.get("/api/v1/users", headers=_h(admin))).json()}
    assert listed["viewer1"]["totp_enabled"] is True and listed["viewer2"]["totp_enabled"] is False

    r = await client.post(f"/api/v1/users/{ids['viewer1']}/reset-2fa", json={"current_password": PASSWORD}, headers=_h(admin))
    assert r.status_code == 204, r.text

    user = await db_session.get(User, ids["viewer1"])
    assert user.totp_secret_id is None and user.totp_confirmed_at is None
    assert (await db_session.execute(select(RecoveryCode).where(RecoveryCode.user_id == ids["viewer1"]))).first() is None
    # Anmeldung geht wieder mit Passwort allein.
    assert (await client.post("/api/v1/auth/login", json={"username": "viewer1", "password": PASSWORD})).status_code == 200

    entry = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "user.2fa_reset"))).scalar_one()
    assert entry.outcome == "success" and entry.target_id == ids["viewer1"]
    assert entry.actor_type == "user" and entry.actor_id != ids["viewer1"]


@pytest.mark.asyncio
async def test_reset_revokes_all_sessions_of_the_target(client, db_session):
    _, ids = await _world(client)
    admin = await _login(client, "admin1")
    viewer_token = await _login(client, "viewer1")
    await _enable_2fa(client, viewer_token)
    active_before = (
        await db_session.execute(
            select(RefreshToken).where(RefreshToken.user_id == ids["viewer1"], RefreshToken.revoked_at.is_(None))
        )
    ).scalars().all()
    assert active_before

    assert (await client.post(f"/api/v1/users/{ids['viewer1']}/reset-2fa", json={"current_password": PASSWORD}, headers=_h(admin))).status_code == 204

    # Bis hierher hat der Admin selbst keine Sitzung verloren, die des Ziels sind alle widerrufen.
    await db_session.refresh(active_before[0])
    assert active_before[0].revoked_at is not None
    admin_active = (
        await db_session.execute(
            select(RefreshToken).where(RefreshToken.user_id != ids["viewer1"], RefreshToken.revoked_at.is_(None))
        )
    ).scalars().all()
    assert admin_active


@pytest.mark.asyncio
async def test_owner_2fa_cannot_be_reset_by_an_admin(client, db_session):
    owner, _ = await _world(client)
    await _enable_2fa(client, owner)
    admin = await _login(client, "admin1")
    owner_row = (await db_session.execute(select(User).where(User.is_owner.is_(True)))).scalar_one()

    r = await client.post(f"/api/v1/users/{owner_row.id}/reset-2fa", json={"current_password": PASSWORD}, headers=_h(admin))
    assert r.status_code == 403
    assert "Inhaber" in r.json()["detail"]
    await db_session.refresh(owner_row)
    assert owner_row.totp_confirmed_at is not None

    denied = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "user.2fa_reset"))).scalars().all()
    assert [e.outcome for e in denied] == ["denied"]


@pytest.mark.asyncio
async def test_owner_can_reset_others_but_own_goes_through_me(client, db_session):
    owner, ids = await _world(client)
    await _enable_2fa(client, owner)
    viewer_token = await _login(client, "viewer1")
    await _enable_2fa(client, viewer_token)
    owner_row = (await db_session.execute(select(User).where(User.is_owner.is_(True)))).scalar_one()

    assert (await client.post(f"/api/v1/users/{ids['viewer1']}/reset-2fa", json={"current_password": PASSWORD}, headers=_h(owner))).status_code == 204
    own = await client.post(f"/api/v1/users/{owner_row.id}/reset-2fa", json={"current_password": PASSWORD}, headers=_h(owner))
    assert own.status_code == 409
    assert "Mein Konto" in own.json()["detail"]
    # Der Inhaber selbst schaltet seine eigene ueber /me/totp ab.
    assert (await client.request("DELETE", "/api/v1/me/totp", json={"current_password": PASSWORD}, headers=_h(owner))).status_code == 204


@pytest.mark.asyncio
async def test_reset_requires_users_write(client):
    _, ids = await _world(client)
    viewer = await _login(client, "viewer1")
    other = await _login(client, "viewer2")
    await _enable_2fa(client, other)
    assert (await client.post(f"/api/v1/users/{ids['viewer2']}/reset-2fa", json={"current_password": PASSWORD}, headers=_h(viewer))).status_code == 403
    assert (await client.post(f"/api/v1/users/{ids['viewer2']}/reset-2fa", json={"current_password": PASSWORD})).status_code == 401


@pytest.mark.asyncio
async def test_reset_unknown_user_and_user_without_2fa(client):
    owner, ids = await _world(client)
    assert (await client.post("/api/v1/users/gibt-es-nicht/reset-2fa", json={"current_password": PASSWORD}, headers=_h(owner))).status_code == 404
    r = await client.post(f"/api/v1/users/{ids['viewer2']}/reset-2fa", json={"current_password": PASSWORD}, headers=_h(owner))
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_reset_needs_the_admins_own_current_password(client, db_session):
    _, ids = await _world(client)
    admin = await _login(client, "admin1")
    viewer_token = await _login(client, "viewer1")
    await _enable_2fa(client, viewer_token)
    url = f"/api/v1/users/{ids['viewer1']}/reset-2fa"

    assert (await client.post(url, headers=_h(admin))).status_code == 422
    wrong = await client.post(url, json={"current_password": "falsch"}, headers=_h(admin))
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == "Das aktuelle Passwort stimmt nicht."
    assert (await db_session.get(User, ids["viewer1"])).totp_confirmed_at is not None

    failed = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "auth.password_check_failed"))).scalars().all()
    assert [e.detail["aktion"] for e in failed] == ["2fa_zuruecksetzen"]

    assert (await client.post(url, json={"current_password": PASSWORD}, headers=_h(admin))).status_code == 204


@pytest.mark.asyncio
async def test_reset_wrong_passwords_are_throttled(client):
    _, ids = await _world(client)
    admin = await _login(client, "admin1")
    url = f"/api/v1/users/{ids['viewer1']}/reset-2fa"
    from nodvard_deck.core import login_limit

    for _ in range(login_limit.MAX_FAILURES_PER_USER):
        assert (await client.post(url, json={"current_password": "falsch"}, headers=_h(admin))).status_code == 400
    assert (await client.post(url, json={"current_password": PASSWORD}, headers=_h(admin))).status_code == 429
