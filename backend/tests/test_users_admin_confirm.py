"""Fremde Zugaenge nur mit Bestaetigung: Ein Admin setzt das Passwort eines anderen Kontos oder dessen Zwei-Faktor-
Anmeldung zurueck nur mit seinem eigenen Passwort und, wenn er selbst Zwei-Faktor an hat, seinem eigenen Code aus der App.
Danach steht eine Meldung unter „Meldungen“, und alles steht im Protokoll."""

from __future__ import annotations

import time

import pyotp
import pytest
from nodvard_deck.core import security
from nodvard_deck.models import AuditEntry, Notification, User
from sqlalchemy import select
from totp_helpers import setup_confirm_code

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "ganz-neues-passwort-55"


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _login(client, username: str, password: str = PASSWORD) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _enable_2fa(client, token: str) -> tuple[str, list[str]]:
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=_h(token))).json()["secret"]
    r = await client.post("/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret)}, headers=_h(token))
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


async def _world(client) -> dict[str, str]:
    """Inhaber 'chef', Admin 'admin1', Viewer 'viewer1' und 'viewer2'."""
    await client.post("/api/v1/auth/bootstrap", json={"username": "chef", "password": PASSWORD, "setup_code": "TEST-CODE-2345"})
    owner = await _login(client, "chef")
    roles = {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=_h(owner))).json()}
    ids = {}
    for name, role in (("admin1", "admin"), ("viewer1", "viewer"), ("viewer2", "viewer")):
        r = await client.post("/api/v1/users", json={"username": name, "password": PASSWORD, "role_ids": [roles[role]], "current_password": PASSWORD}, headers=_h(owner))
        ids[name] = r.json()["id"]
    return ids


async def _password_is(db_session, user_id: str, password: str) -> bool:
    user = await db_session.get(User, user_id)
    await db_session.refresh(user)
    return security.verify_password(password, user.password_hash)


async def _notices(db_session) -> list[Notification]:
    return (await db_session.execute(select(Notification).where(Notification.title.like("Zugang von%")))).scalars().all()


def _next_code(secret: str) -> str:
    """Der Code des naechsten Zeitschritts: gilt auch noch, wenn der aktuelle in diesem Test schon verbraucht ist."""
    return pyotp.TOTP(secret).at(time.time() + 30)


@pytest.mark.asyncio
async def test_setting_another_password_needs_the_own_password(client, db_session):
    ids = await _world(client)
    admin = await _login(client, "admin1")

    missing = await client.patch(f"/api/v1/users/{ids['viewer1']}", json={"password": NEW_PASSWORD}, headers=_h(admin))
    assert missing.status_code == 403
    assert "dein eigenes Passwort" in missing.json()["detail"]
    wrong = await client.patch(
        f"/api/v1/users/{ids['viewer1']}", json={"password": NEW_PASSWORD, "current_password": "falsch-falsch-1"}, headers=_h(admin)
    )
    assert wrong.status_code == 400
    assert await _password_is(db_session, ids["viewer1"], PASSWORD)
    assert await _notices(db_session) == []

    ok = await client.patch(
        f"/api/v1/users/{ids['viewer1']}", json={"password": NEW_PASSWORD, "current_password": PASSWORD}, headers=_h(admin)
    )
    assert ok.status_code == 200, ok.text
    assert await _password_is(db_session, ids["viewer1"], NEW_PASSWORD)
    notices = await _notices(db_session)
    assert len(notices) == 1
    assert notices[0].title == "Zugang von „viewer1“ geändert"
    assert "„admin1“ hat für „viewer1“ ein neues Passwort gesetzt." in notices[0].body
    assert NEW_PASSWORD not in notices[0].body and PASSWORD not in notices[0].body
    entry = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "user.updated"))).scalars().all()[-1]
    assert entry.detail["password_reset"] is True and entry.actor_id == ids["admin1"]
    assert NEW_PASSWORD not in str(entry.detail) and PASSWORD not in str(entry.detail)


@pytest.mark.asyncio
async def test_other_changes_need_no_confirmation(client, db_session):
    ids = await _world(client)
    admin = await _login(client, "admin1")
    r = await client.patch(f"/api/v1/users/{ids['viewer1']}", json={"display_name": "Viktor"}, headers=_h(admin))
    assert r.status_code == 200, r.text
    assert await _notices(db_session) == []


@pytest.mark.asyncio
async def test_an_admin_with_two_factor_also_needs_the_own_code_for_a_password(client, db_session):
    ids = await _world(client)
    admin = await _login(client, "admin1")
    secret, recovery = await _enable_2fa(client, admin)
    body = {"password": NEW_PASSWORD, "current_password": PASSWORD}

    no_code = await client.patch(f"/api/v1/users/{ids['viewer1']}", json=body, headers=_h(admin))
    assert no_code.status_code == 403
    assert no_code.json()["code"] == "totp_missing"
    wrong = await client.patch(f"/api/v1/users/{ids['viewer1']}", json=body | {"totp_code": "000000"}, headers=_h(admin))
    assert wrong.status_code == 400
    assert wrong.json()["code"] == "totp_wrong"
    # Ein Wiederherstellungs-Code reicht hier nicht (wie bei Sicherung und Update): er ist fuer den Notfall beim Anmelden.
    recovery_try = await client.patch(f"/api/v1/users/{ids['viewer1']}", json=body | {"totp_code": recovery[0]}, headers=_h(admin))
    assert recovery_try.status_code == 400
    assert await _password_is(db_session, ids["viewer1"], PASSWORD)
    assert await _notices(db_session) == []

    ok = await client.patch(
        f"/api/v1/users/{ids['viewer1']}", json=body | {"totp_code": pyotp.TOTP(secret).now()}, headers=_h(admin)
    )
    assert ok.status_code == 200, ok.text
    assert await _password_is(db_session, ids["viewer1"], NEW_PASSWORD)
    assert len(await _notices(db_session)) == 1


@pytest.mark.asyncio
async def test_resetting_two_factor_of_another_needs_the_own_code(client, db_session):
    ids = await _world(client)
    admin = await _login(client, "admin1")
    secret, _ = await _enable_2fa(client, admin)
    await _enable_2fa(client, await _login(client, "viewer1"))

    no_code = await client.post(f"/api/v1/users/{ids['viewer1']}/reset-2fa", json={"current_password": PASSWORD}, headers=_h(admin))
    assert no_code.status_code == 403
    assert no_code.json()["code"] == "totp_missing"
    viewer = await db_session.get(User, ids["viewer1"])
    await db_session.refresh(viewer)
    assert viewer.totp_confirmed_at is not None

    ok = await client.post(
        f"/api/v1/users/{ids['viewer1']}/reset-2fa",
        json={"current_password": PASSWORD, "totp_code": pyotp.TOTP(secret).now()}, headers=_h(admin),
    )
    assert ok.status_code == 204, ok.text
    await db_session.refresh(viewer)
    assert viewer.totp_confirmed_at is None
    notices = await _notices(db_session)
    assert len(notices) == 1
    assert "„admin1“ hat die Zwei-Faktor-Anmeldung von „viewer1“ zurückgesetzt." in notices[0].body
    entries = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "user.2fa_reset"))).scalars().all()
    assert [e.outcome for e in entries] == ["success"]


@pytest.mark.asyncio
async def test_a_refused_target_does_not_use_up_the_code(client, db_session):
    """Erst wenn feststeht, dass die Aenderung geschieht, wird der Code geprueft: Eine Ablehnung wegen des Ziels (hier:
    viewer2 hat keine Zwei-Faktor-Anmeldung) verbraucht ihn nicht, er gilt danach noch fuer die richtige Aktion."""
    ids = await _world(client)
    admin = await _login(client, "admin1")
    secret, _ = await _enable_2fa(client, admin)
    await _enable_2fa(client, await _login(client, "viewer1"))
    code = pyotp.TOTP(secret).now()

    refused = await client.post(
        f"/api/v1/users/{ids['viewer2']}/reset-2fa", json={"current_password": PASSWORD, "totp_code": code}, headers=_h(admin)
    )
    assert refused.status_code == 409
    ok = await client.post(
        f"/api/v1/users/{ids['viewer1']}/reset-2fa", json={"current_password": PASSWORD, "totp_code": code}, headers=_h(admin)
    )
    assert ok.status_code == 204, ok.text


@pytest.mark.asyncio
async def test_the_password_comes_before_anything_about_the_target(client, db_session):
    """Ohne richtiges eigenes Passwort erfaehrt man nichts ueber das Ziel (kein 404/409 als Hinweis)."""
    await _world(client)
    admin = await _login(client, "admin1")
    r = await client.post("/api/v1/users/gibt-es-nicht/reset-2fa", json={"current_password": "falsch-falsch-1"}, headers=_h(admin))
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_a_used_code_is_not_accepted_a_second_time(client, db_session):
    ids = await _world(client)
    admin = await _login(client, "admin1")
    secret, _ = await _enable_2fa(client, admin)
    code = _next_code(secret)
    body = {"current_password": PASSWORD, "totp_code": code}
    assert (await client.patch(f"/api/v1/users/{ids['viewer1']}", json=body | {"password": NEW_PASSWORD}, headers=_h(admin))).status_code == 200
    again = await client.patch(f"/api/v1/users/{ids['viewer2']}", json=body | {"password": NEW_PASSWORD}, headers=_h(admin))
    assert again.status_code == 400
    assert again.json()["code"] == "totp_used"
    assert await _password_is(db_session, ids["viewer2"], PASSWORD)


async def _titles(db_session) -> list[str]:
    return [n.title for n in (await db_session.execute(select(Notification))).scalars().all()]


@pytest.mark.asyncio
async def test_roles_say_whether_they_manage_accounts(client):
    await _world(client)
    owner = await _login(client, "chef")
    roles = {r["name"]: r for r in (await client.get("/api/v1/roles", headers=_h(owner))).json()}
    assert roles["admin"]["manages_users"] is True
    assert roles["viewer"]["manages_users"] is False


@pytest.mark.asyncio
async def test_a_new_admin_account_needs_the_own_password_and_code(client, db_session):
    """Der Umweg ueber ein neues Admin-Konto: mit nur einer Admin-Sitzung (ohne Passwort und Code) geht er nicht."""
    await _world(client)
    admin = await _login(client, "admin1")
    secret, _ = await _enable_2fa(client, admin)
    roles = {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=_h(admin))).json()}
    body = {"username": "hintertuer", "password": NEW_PASSWORD, "role_ids": [roles["admin"]]}

    no_password = await client.post("/api/v1/users", json=body, headers=_h(admin))
    assert no_password.status_code == 403
    assert no_password.json()["code"] == "password_missing"
    no_code = await client.post("/api/v1/users", json=body | {"current_password": PASSWORD}, headers=_h(admin))
    assert no_code.status_code == 403
    assert no_code.json()["code"] == "totp_missing"
    assert (await db_session.execute(select(User).where(User.username == "hintertuer"))).scalar_one_or_none() is None

    ok = await client.post(
        "/api/v1/users", json=body | {"current_password": PASSWORD, "totp_code": pyotp.TOTP(secret).now()}, headers=_h(admin)
    )
    assert ok.status_code == 201, ok.text
    assert "Neues Konto mit Verwaltungsrechten: „hintertuer“" in await _titles(db_session)


@pytest.mark.asyncio
async def test_a_normal_account_needs_no_confirmation(client, db_session):
    await _world(client)
    admin = await _login(client, "admin1")
    roles = {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=_h(admin))).json()}
    before = await _titles(db_session)  # admin1 selbst wurde als Admin angelegt
    r = await client.post("/api/v1/users", json={"username": "gast", "password": NEW_PASSWORD, "role_ids": [roles["viewer"]]}, headers=_h(admin))
    assert r.status_code == 201, r.text
    assert await _titles(db_session) == before


@pytest.mark.asyncio
async def test_giving_admin_roles_needs_the_own_password(client, db_session):
    ids = await _world(client)
    admin = await _login(client, "admin1")
    roles = {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=_h(admin))).json()}

    refused = await client.patch(f"/api/v1/users/{ids['viewer1']}", json={"role_ids": [roles["admin"]]}, headers=_h(admin))
    assert refused.status_code == 403
    assert refused.json()["code"] == "password_missing"
    viewer = await db_session.get(User, ids["viewer1"])
    await db_session.refresh(viewer, ["roles"])
    assert [r.name for r in viewer.roles] == ["viewer"]

    ok = await client.patch(
        f"/api/v1/users/{ids['viewer1']}", json={"role_ids": [roles["admin"]], "current_password": PASSWORD}, headers=_h(admin)
    )
    assert ok.status_code == 200, ok.text
    assert "Verwaltungsrechte für „viewer1“" in await _titles(db_session)
    # Andere Rollen ohne Verwaltungsrechte brauchen nichts.
    assert (await client.patch(
        f"/api/v1/users/{ids['viewer2']}", json={"role_ids": [roles["operator"]]}, headers=_h(admin)
    )).status_code == 200


@pytest.mark.asyncio
async def test_patch_checks_the_password_before_it_says_anything_about_the_target(client):
    await _world(client)
    admin = await _login(client, "admin1")
    r = await client.patch("/api/v1/users/gibt-es-nicht", json={"password": NEW_PASSWORD}, headers=_h(admin))
    assert r.status_code == 403
    assert r.json()["code"] == "password_missing"
    r = await client.patch(
        "/api/v1/users/gibt-es-nicht", json={"password": NEW_PASSWORD, "current_password": "falsch-falsch-1"}, headers=_h(admin)
    )
    assert r.status_code == 400
