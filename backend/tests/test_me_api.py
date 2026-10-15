"""Eigenes Konto: Profil aendern und Passwort wechseln."""

from __future__ import annotations

import pytest


async def _login(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_update_own_profile(client):
    headers = await _login(client)
    r = await client.patch("/api/v1/me", json={"display_name": " Nico ", "email": "nico@example.org"}, headers=headers)
    assert r.status_code == 200, r.text
    assert (r.json()["display_name"], r.json()["email"]) == ("Nico", "nico@example.org")
    assert (await client.patch("/api/v1/me", json={"email": ""}, headers=headers)).json()["email"] is None
    assert (await client.patch("/api/v1/me", json={"locale": "Deutsch"}, headers=headers)).status_code == 422


@pytest.mark.asyncio
async def test_change_password_needs_the_current_one(client):
    headers = await _login(client)
    wrong = await client.post("/api/v1/me/password", json={"current_password": "falsch", "new_password": "neues-passwort-1"}, headers=headers)
    assert wrong.status_code == 400
    short = await client.post("/api/v1/me/password", json={"current_password": "correct-horse-battery", "new_password": "kurz"}, headers=headers)
    assert short.status_code == 422
    ok = await client.post("/api/v1/me/password", json={"current_password": "correct-horse-battery", "new_password": "neues-passwort-1"}, headers=headers)
    assert ok.status_code == 204, ok.text
    old = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    new = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "neues-passwort-1"})
    assert (old.status_code, new.status_code) == (401, 200)


@pytest.mark.asyncio
async def test_usernames_are_always_lowercase(client):
    headers = await _login(client, username=" Admin ")
    me = await client.get("/api/v1/me", headers=headers)
    assert me.json()["username"] == "admin"
    assert (await client.post("/api/v1/auth/login", json={"username": "ADMIN", "password": "correct-horse-battery"})).status_code == 200

    created = await client.post("/api/v1/users", json={"username": "Frisch", "password": "correct-horse-battery"}, headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["username"] == "frisch"
    dup = await client.post("/api/v1/users", json={"username": "FRISCH", "password": "correct-horse-battery"}, headers=headers)
    assert dup.status_code == 409


@pytest.mark.asyncio
async def test_change_password_signs_out_other_sessions_but_keeps_the_current_one(client):
    """Nach dem Passwortwechsel (z. B. weil ein Handy verloren ging) duerfen
    andere Anmeldungen keine neuen Access-Tokens mehr bekommen. Die Sitzung, in der
    das Passwort geaendert wurde, bleibt angemeldet."""
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    # Andere Anmeldung, z. B. die App auf dem verlorenen Handy (Refresh-Token im Body).
    phone = await client.post(
        "/api/v1/auth/login",
        json={"username": "owner1", "password": "correct-horse-battery", "client_type": "android"},
    )
    phone_refresh = phone.json()["refresh_token"]
    # Aktuelle Web-Sitzung (Refresh-Token als Cookie im Client).
    web = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {web.json()['access_token']}"}

    ok = await client.post(
        "/api/v1/me/password",
        json={"current_password": "correct-horse-battery", "new_password": "neues-passwort-1"},
        headers=headers,
    )
    assert ok.status_code == 204, ok.text

    stolen = await client.post("/api/v1/auth/refresh", json={"refresh_token": phone_refresh})
    assert stolen.status_code == 401
    current = await client.post("/api/v1/auth/refresh", json={})
    assert current.status_code == 200, current.text


@pytest.mark.asyncio
async def test_me_reports_the_dashboard_timezone_to_every_user(client):
    headers = await _login(client)
    assert (await client.get("/api/v1/me", headers=headers)).json()["timezone"] == "Europe/Berlin"
    put = await client.put("/api/v1/settings/system.timezone", json={"value": "Asia/Tokyo"}, headers=headers)
    assert put.status_code == 200, put.text
    assert (await client.get("/api/v1/me", headers=headers)).json()["timezone"] == "Asia/Tokyo"


@pytest.mark.asyncio
async def test_preferences_are_per_user_and_validated(client):
    """„Erste Schritte“ ausblenden: eine Einstellung je Benutzer, nicht je Browser."""
    owner = await _login(client)
    assert (await client.get("/api/v1/me/preferences", headers=owner)).json() == {"first_steps_dismissed": False, "theme": "simple", "design_notice_dismissed": True}

    r = await client.patch("/api/v1/me/preferences", json={"first_steps_dismissed": True}, headers=owner)
    assert r.status_code == 200, r.text
    assert r.json() == {"first_steps_dismissed": True, "theme": "simple", "design_notice_dismissed": True}
    assert (await client.get("/api/v1/me/preferences", headers=owner)).json() == {"first_steps_dismissed": True, "theme": "simple", "design_notice_dismissed": True}

    # Ein anderer Benutzer sieht seine eigene, noch unberuehrte Einstellung.
    await client.post("/api/v1/users", json={"username": "zweiter", "password": "correct-horse-battery"}, headers=owner)
    login = await client.post("/api/v1/auth/login", json={"username": "zweiter", "password": "correct-horse-battery"})
    other = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/api/v1/me/preferences", headers=other)).json() == {"first_steps_dismissed": False, "theme": "simple", "design_notice_dismissed": True}

    # Wieder einblenden; ein leerer Aufruf aendert nichts; falsche Werte werden abgelehnt.
    assert (await client.patch("/api/v1/me/preferences", json={}, headers=owner)).json() == {"first_steps_dismissed": True, "theme": "simple", "design_notice_dismissed": True}
    assert (await client.patch("/api/v1/me/preferences", json={"first_steps_dismissed": False}, headers=owner)).json() == {"first_steps_dismissed": False, "theme": "simple", "design_notice_dismissed": True}
    assert (await client.patch("/api/v1/me/preferences", json={"first_steps_dismissed": "ja bitte"}, headers=owner)).status_code == 422
    assert (await client.get("/api/v1/me/preferences")).status_code == 401


@pytest.mark.asyncio
async def test_theme_is_per_user(client, db_session):
    from nodvard_deck.services import settings as settings_service

    owner = await _login(client)
    r = await client.patch("/api/v1/me/preferences", json={"theme": "classic"}, headers=owner)
    assert r.json() == {"first_steps_dismissed": False, "theme": "classic", "design_notice_dismissed": True}
    assert (await client.patch("/api/v1/me/preferences", json={"first_steps_dismissed": True}, headers=owner)).json()["theme"] == "classic"
    assert (await client.patch("/api/v1/me/preferences", json={"theme": "neon"}, headers=owner)).status_code == 422

    await client.post("/api/v1/users", json={"username": "zweiter", "password": "correct-horse-battery"}, headers=owner)
    login = await client.post("/api/v1/auth/login", json={"username": "zweiter", "password": "correct-horse-battery"})
    other = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/api/v1/me/preferences", headers=other)).json()["theme"] == "simple"

    # Ein gespeicherter Wert, den es nicht mehr gibt, liefert den Standard.
    me = (await client.get("/api/v1/me", headers=owner)).json()
    await settings_service.set_user(db_session, me["id"], "ui.theme", "alt")
    await db_session.commit()
    assert (await client.get("/api/v1/me/preferences", headers=owner)).json()["theme"] == "simple"


@pytest.mark.asyncio
async def test_original_theme_and_design_notice(client, db_session):
    from sqlalchemy import delete

    from nodvard_deck.models import Setting

    owner = await _login(client)
    # Das Theme „Bisher“ ist wählbar.
    r = await client.patch("/api/v1/me/preferences", json={"theme": "original"}, headers=owner)
    assert r.json() == {"first_steps_dismissed": False, "theme": "original", "design_notice_dismissed": True}

    # Neue Konten kennen das alte Aussehen nicht und bekommen den Hinweis nie, auch nicht das erste.
    await client.post("/api/v1/users", json={"username": "zweiter", "password": "correct-horse-battery"}, headers=owner)
    login = await client.post("/api/v1/auth/login", json={"username": "zweiter", "password": "correct-horse-battery"})
    other = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/api/v1/me/preferences", headers=other)).json()["design_notice_dismissed"] is True

    # Ein Konto von vor dem Update hat den Wert nicht: Hinweis offen, bis er weggeklickt wird, gemerkt beim Benutzer.
    me = (await client.get("/api/v1/me", headers=owner)).json()
    await db_session.execute(delete(Setting).where(Setting.key == "ui.design_notice_dismissed", Setting.user_id == me["id"]))
    await db_session.commit()
    assert (await client.get("/api/v1/me/preferences", headers=owner)).json()["design_notice_dismissed"] is False
    r = await client.patch("/api/v1/me/preferences", json={"design_notice_dismissed": True}, headers=owner)
    assert r.json() == {"first_steps_dismissed": False, "theme": "original", "design_notice_dismissed": True}
    assert (await client.get("/api/v1/me/preferences", headers=owner)).json()["design_notice_dismissed"] is True
    assert (await client.patch("/api/v1/me/preferences", json={"design_notice_dismissed": "ja"}, headers=owner)).status_code == 422
    # Das andere Konto bleibt davon unberührt.
    assert (await client.get("/api/v1/me/preferences", headers=other)).json()["theme"] == "simple"


@pytest.mark.asyncio
async def test_preferences_disappear_with_the_user(client, db_session):
    from sqlalchemy import select

    from nodvard_deck.models import Setting

    owner = await _login(client)
    created = await client.post("/api/v1/users", json={"username": "kurz", "password": "correct-horse-battery"}, headers=owner)
    login = await client.post("/api/v1/auth/login", json={"username": "kurz", "password": "correct-horse-battery"})
    other = {"Authorization": f"Bearer {login.json()['access_token']}"}
    await client.patch("/api/v1/me/preferences", json={"first_steps_dismissed": True}, headers=other)
    own_rows = select(Setting).where(Setting.scope == "user", Setting.user_id == created.json()["id"])
    assert (await db_session.execute(own_rows)).scalars().all()

    assert (await client.delete(f"/api/v1/users/{created.json()['id']}", headers=owner)).status_code == 204
    assert (await db_session.execute(own_rows)).scalars().all() == []
