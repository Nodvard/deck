"""Integrationstests fuer /api/v1/auth/* -- spielen exakt die Fluesse nach, die beim
Boot-Test manuell gegen einen echten uvicorn-Prozess verifiziert wurden.
"""

from __future__ import annotations

import pytest


async def _bootstrap(client, username="nico", password="correct-horse-battery"):
    r = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _age_revocations(db_session, seconds: int = 31):
    """Schiebt alle Widerrufszeitpunkte in die Vergangenheit (statt echt zu warten)."""
    from datetime import timedelta

    from nodvard_deck.models import RefreshToken
    from sqlalchemy import select

    rows = (await db_session.execute(select(RefreshToken))).scalars().all()
    for row in rows:
        if row.revoked_at is not None:
            row.revoked_at = row.revoked_at - timedelta(seconds=seconds)
    await db_session.flush()


def _browser_holds(client, value: str) -> None:
    """Der Browser haelt genau diesen Refresh-Token. Seit der Umbenennung (Teil B, PR 4) schreibt der
    Server ihn in zwei Cookies; mit nur einem `client.cookies.set` bliebe im Cookie-Speicher von httpx
    der aktuelle Wert im anderen Namen stehen und wuerde zuerst gelesen. Die Faelle mit
    unterschiedlichen Werten in beiden Namen (Rollback) stehen in test_auth_refresh_cookie.py."""
    for name in ("nodvard_deck_refresh", "lattice_refresh"):
        client.cookies.set(name, value)


async def _login_web(client):
    await _bootstrap(client)
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "nico", "password": "correct-horse-battery"},
    )
    assert login.status_code == 200, login.text
    return login


@pytest.mark.asyncio
async def test_bootstrap_creates_owner(client):
    body = await _bootstrap(client)
    assert body["is_owner"] is True
    assert body["username"] == "nico"


@pytest.mark.asyncio
async def test_bootstrap_status_flips_after_first_user(client):
    """Der Web-Erstinbetriebnahme-Assistent fragt das vor jedem Seitenaufbau
    ab, um zu entscheiden, ob er sich selbst oder /login zeigt."""
    before = await client.get("/api/v1/auth/bootstrap")
    assert before.status_code == 200
    assert before.json() == {"needed": True}

    await _bootstrap(client)

    after = await client.get("/api/v1/auth/bootstrap")
    assert after.json() == {"needed": False}


@pytest.mark.asyncio
async def test_bootstrap_second_time_is_rejected(client):
    await _bootstrap(client)
    r = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "anderer", "password": "whatever123", "setup_code": "TEST-CODE-2345"},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_login_web_sets_cookie_not_body(client):
    await _bootstrap(client)
    r = await client.post(
        "/api/v1/auth/login",
        json={"username": "nico", "password": "correct-horse-battery"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "refresh_token" not in body
    assert body["access_token"]
    assert body["user"]["is_owner"] is True
    assert body["user"]["permissions"] == ["*"]
    assert "lattice_refresh" in r.cookies
    assert "nodvard_deck_refresh" in r.cookies, "Uebergangszeit: beide Cookies"
    # docs/00-DECISIONS.md D-07 fordert Secure fuer den Produktivbetrieb -- ueber
    # Klartext-HTTP (base_url http://test) MUSS es fehlen, sonst wuerde ein echter
    # Browser den Cookie nie speichern (siehe Tests unten).
    set_cookie_header = r.headers.get("set-cookie", "")
    assert "secure" not in set_cookie_header.lower()
    assert "httponly" in set_cookie_header.lower()
    assert "samesite=strict" in set_cookie_header.lower()


def _is_secure(set_cookie_header: str) -> bool:
    return any(part.strip().lower() == "secure" for part in set_cookie_header.split(";"))


async def _login(client, url="/api/v1/auth/login"):
    r = await client.post(url, json={"username": "nico", "password": "correct-horse-battery"})
    assert r.status_code == 200, r.text
    return r


@pytest.mark.asyncio
async def test_refresh_cookie_not_secure_over_http_even_in_prod(client, test_settings, monkeypatch):
    """Eine Installation laeuft mit LATTICE_ENV=prod, aber ueber reines HTTP
    (z. B. http://192.168.2.45:8080). Ein Secure-Cookie verwirft der Browser dort still ->
    Abmeldung bei jedem Neuladen. Secure haengt deshalb am echten Schema der Anfrage,
    nicht an `env`."""
    from nodvard_deck import config

    test_settings.env = "prod"
    # Auch der Prozess-Singleton, nicht nur die DI-Aufloesung, soll 'prod' sehen.
    monkeypatch.setattr(config, "_settings", test_settings)
    await _bootstrap(client)

    login = await _login(client, "http://test/api/v1/auth/login")
    assert not _is_secure(login.headers["set-cookie"])

    refreshed = await client.post("http://test/api/v1/auth/refresh", json={})
    assert refreshed.status_code == 200
    assert not _is_secure(refreshed.headers["set-cookie"])


@pytest.mark.asyncio
async def test_refresh_cookie_secure_over_https(client):
    await _bootstrap(client)

    login = await _login(client, "https://test/api/v1/auth/login")
    assert _is_secure(login.headers["set-cookie"])

    refreshed = await client.post("https://test/api/v1/auth/refresh", json={})
    assert refreshed.status_code == 200
    assert _is_secure(refreshed.headers["set-cookie"])

    logout = await client.post("https://test/api/v1/auth/logout", json={})
    assert logout.status_code == 204
    assert "lattice_refresh" in logout.headers["set-cookie"]
    assert "nodvard_deck_refresh" in logout.headers["set-cookie"]
    assert _is_secure(logout.headers["set-cookie"])


@pytest.mark.asyncio
async def test_cookie_secure_setting_overrides_the_scheme(client, test_settings):
    """`LATTICE_COOKIE_SECURE` erzwingt es in beide Richtungen -- z. B. hinter einem
    TLS-Proxy, der das Schema nicht weiterreicht."""
    await _bootstrap(client)

    test_settings.cookie_secure = True
    assert _is_secure((await _login(client, "http://test/api/v1/auth/login")).headers["set-cookie"])
    logout = await client.post("http://test/api/v1/auth/logout", json={})
    assert _is_secure(logout.headers["set-cookie"])

    test_settings.cookie_secure = False
    assert not _is_secure((await _login(client, "https://test/api/v1/auth/login")).headers["set-cookie"])


@pytest.mark.asyncio
async def test_login_android_returns_refresh_token_in_body_no_cookie(client):
    await _bootstrap(client)
    r = await client.post(
        "/api/v1/auth/login",
        json={
            "username": "nico",
            "password": "correct-horse-battery",
            "client_type": "android",
        },
    )
    assert r.status_code == 200
    assert r.json()["refresh_token"]
    assert "lattice_refresh" not in r.cookies
    assert "nodvard_deck_refresh" not in r.cookies


@pytest.mark.asyncio
async def test_login_wrong_password_rejected(client):
    await _bootstrap(client)
    r = await client.post(
        "/api/v1/auth/login", json={"username": "nico", "password": "falsch"}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_user_same_error_as_wrong_password(client):
    """Kein Username-Enumeration-Leck ueber unterschiedliche Fehlermeldungen."""
    await _bootstrap(client)
    r1 = await client.post(
        "/api/v1/auth/login", json={"username": "gibtsnicht", "password": "x"}
    )
    r2 = await client.post(
        "/api/v1/auth/login", json={"username": "nico", "password": "falsch"}
    )
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["detail"] == r2.json()["detail"]


@pytest.mark.asyncio
async def test_get_me_requires_bearer_token(client):
    r = await client.get("/api/v1/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_me_rejects_garbage_token(client):
    r = await client.get("/api/v1/me", headers={"Authorization": "Bearer garbage.not.jwt"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_me_with_valid_token(client):
    await _bootstrap(client)
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "nico", "password": "correct-horse-battery"},
    )
    token = login.json()["access_token"]

    r = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "nico"
    assert r.json()["totp_enabled"] is False


@pytest.mark.asyncio
async def test_refresh_rotation_and_reuse_detection(client, db_session):
    """Der Kernfall, der beim manuellen Boot-Test zuerst mit MissingGreenlet und dann
    mit einem SQLite-Timezone-Bug crashte, bevor beide Bugs gefixt wurden."""
    await _bootstrap(client)
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "nico", "password": "correct-horse-battery"},
    )
    old_cookie = login.cookies["lattice_refresh"]

    r1 = await client.post("/api/v1/auth/refresh", json={})
    assert r1.status_code == 200
    new_cookie = r1.cookies["lattice_refresh"]
    assert new_cookie != old_cookie

    # Alten (jetzt widerrufenen) Cookie manuell zurueckspielen und wiederverwenden --
    # httpx' eigener Cookie-Jar haette ihn schon durch den neuen ersetzt. Erst nach der
    # Gnadenfrist ist das ein Fehlschlag (siehe `test_refresh_grace_*` unten).
    await _age_revocations(db_session)
    _browser_holds(client, old_cookie)
    r2 = await client.post("/api/v1/auth/refresh", json={})
    assert r2.status_code == 401

    _browser_holds(client, new_cookie)
    r3 = await client.post("/api/v1/auth/refresh", json={})
    assert r3.status_code == 200


@pytest.mark.asyncio
async def test_refresh_without_token_is_rejected(client):
    r = await client.post("/api/v1/auth/refresh", json={})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_logout_revokes_refresh_token(client):
    await _bootstrap(client)
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "nico", "password": "correct-horse-battery"},
    )
    assert (await client.post("/api/v1/auth/logout", json={})).status_code == 204

    _browser_holds(client, login.cookies["lattice_refresh"])
    r = await client.post("/api/v1/auth/refresh", json={})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_logout_is_idempotent(client):
    """Logout ohne (oder mit bereits ungueltigem) Token ist trotzdem 204 -- der
    Zielzustand 'abgemeldet' ist danach so oder so erreicht."""
    r = await client.post("/api/v1/auth/logout", json={})
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_refresh_grace_parallel_tab_gets_access_token_without_new_refresh(
    client, db_session, test_settings
):
    """Zwei Tabs aktualisieren fast gleichzeitig: der zweite kommt mit dem gerade
    rotierten Token an und bleibt angemeldet -- ohne zweite Sitzungskette."""
    login = await _login_web(client)
    old_cookie = login.cookies["lattice_refresh"]

    first = await client.post("/api/v1/auth/refresh", json={})
    assert first.status_code == 200
    new_cookie = first.cookies["lattice_refresh"]

    _browser_holds(client, old_cookie)
    second = await client.post("/api/v1/auth/refresh", json={})
    assert second.status_code == 200
    assert "set-cookie" not in second.headers
    assert "refresh_token" not in second.json()

    # Der Access-Token ist echt nutzbar und gehoert zur Nachfolger-Sitzung.
    me = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {second.json()['access_token']}"},
    )
    assert me.status_code == 200
    from nodvard_deck.core import security
    from nodvard_deck.models import RefreshToken
    from sqlalchemy import select

    claims = security.decode_jwt(
        second.json()["access_token"],
        secret=test_settings.get_or_create_jwt_secret(),
        expected_type="access",
    )
    successor = (
        await db_session.execute(select(RefreshToken).where(RefreshToken.revoked_at.is_(None)))
    ).scalars().one()
    assert claims["sid"] == successor.id

    # Der neue Refresh-Token aus dem ersten Aufruf bleibt der einzig gueltige.
    _browser_holds(client, new_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 200


@pytest.mark.asyncio
async def test_refresh_grace_body_client_gets_no_new_refresh_token(client):
    await _bootstrap(client)
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "username": "nico",
            "password": "correct-horse-battery",
            "client_type": "android",
        },
    )
    assert login.status_code == 200, login.text
    old = login.json()["refresh_token"]
    first = await client.post("/api/v1/auth/refresh", json={"refresh_token": old})
    assert first.status_code == 200
    second = await client.post("/api/v1/auth/refresh", json={"refresh_token": old})
    assert second.status_code == 200
    assert "refresh_token" not in second.json()


@pytest.mark.asyncio
async def test_refresh_grace_expires_after_30_seconds(client, db_session):
    login = await _login_web(client)
    old_cookie = login.cookies["lattice_refresh"]
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 200

    await _age_revocations(db_session, seconds=31)
    _browser_holds(client, old_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401


@pytest.mark.asyncio
async def test_refresh_grace_does_not_apply_to_logout(client):
    """Abmelden widerruft ohne Nachfolger -> auch innerhalb der 30 s nie mehr gueltig."""
    login = await _login_web(client)
    old_cookie = login.cookies["lattice_refresh"]
    assert (await client.post("/api/v1/auth/logout", json={})).status_code == 204

    _browser_holds(client, old_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401


@pytest.mark.asyncio
async def test_refresh_grace_rejected_when_successor_was_logged_out(client):
    """Token rotiert, dann meldet sich die Nachfolger-Sitzung ab: der alte Token darf
    die abgemeldete Sitzung nicht wieder beleben."""
    login = await _login_web(client)
    old_cookie = login.cookies["lattice_refresh"]
    first = await client.post("/api/v1/auth/refresh", json={})
    assert first.status_code == 200
    assert (await client.post("/api/v1/auth/logout", json={})).status_code == 204

    _browser_holds(client, old_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401


@pytest.mark.asyncio
async def test_refresh_grace_does_not_apply_to_password_change(client, db_session, test_settings):
    """Passwortwechsel widerruft alle anderen Sitzungen -- auch ein zuvor rotierter
    Token, dessen Nachfolger dabei abgemeldet wird, bleibt ungueltig."""
    from nodvard_deck.services import auth as auth_service

    login = await _login_web(client)
    old_cookie = login.cookies["lattice_refresh"]
    first = await client.post("/api/v1/auth/refresh", json={})
    assert first.status_code == 200
    user_id = login.json()["user"]["id"]

    revoked = await auth_service.revoke_refresh_tokens(db_session, user_id)
    assert revoked == 1

    _browser_holds(client, old_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401
    _browser_holds(client, first.cookies["lattice_refresh"])
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401


@pytest.mark.asyncio
async def test_refresh_grace_rejected_for_deactivated_user(client, db_session):
    from nodvard_deck.models import User
    from sqlalchemy import select

    login = await _login_web(client)
    old_cookie = login.cookies["lattice_refresh"]
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 200

    user = (await db_session.execute(select(User))).scalars().one()
    user.is_active = False
    await db_session.flush()

    _browser_holds(client, old_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotation_is_atomic_loser_falls_into_grace(client, db_session, test_settings):
    """Ueberlappende Aufrufe: der zweite hat den Token noch als 'nicht widerrufen'
    gelesen, der erste hat aber schon rotiert. Der zweite darf keinen zweiten
    Nachfolger erzeugen, sondern faellt in die Gnadenfrist."""
    from nodvard_deck.models import RefreshToken
    from nodvard_deck.services import auth as auth_service
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import set_committed_value

    login = await _login_web(client)
    old_cookie = login.cookies["lattice_refresh"]
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 200

    from nodvard_deck.core import security

    row = (
        await db_session.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == security.hash_opaque_token(old_cookie)
            )
        )
    ).scalar_one()
    assert row.revoked_at is not None
    # Veralteter Lesestand, wie ihn ein ueberlappender Aufruf haette.
    set_committed_value(row, "revoked_at", None)

    result = await auth_service.refresh_access_token(
        db_session, test_settings, raw_refresh_token=old_cookie
    )
    assert result.refresh_token is None
    total = (await db_session.execute(select(RefreshToken))).scalars().all()
    assert len(total) == 2  # keine dritte Zeile
    assert len([t for t in total if t.revoked_at is None]) == 1


@pytest.mark.asyncio
async def test_refresh_grace_does_not_apply_after_own_password_change(client):
    """Eigener Passwortwechsel: die aktuelle Sitzung bleibt, aber ein davor rotierter
    Token, dessen Nachfolger sie ist, bekommt keine Gnadenfrist mehr."""
    login = await _login_web(client)
    old_cookie = login.cookies["lattice_refresh"]
    first = await client.post("/api/v1/auth/refresh", json={})
    assert first.status_code == 200
    headers = {"Authorization": f"Bearer {first.json()['access_token']}"}

    ok = await client.post(
        "/api/v1/me/password",
        json={"current_password": "correct-horse-battery", "new_password": "neues-passwort-1"},
        headers=headers,
    )
    assert ok.status_code == 204, ok.text

    _browser_holds(client, old_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401
    _browser_holds(client, first.cookies["lattice_refresh"])
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 200


@pytest.mark.asyncio
async def test_password_change_with_stale_session_keeps_the_current_browser_logged_in(client):
    """Tab B erneuert (S1 -> S2), Tab A hat noch einen Zugangs-Token
    mit der `sid` von S1. Ein Passwortwechsel in Tab A darf S2 -- die tatsaechlich
    aktuelle Sitzung dieses Browsers -- nicht mit abmelden."""
    login = await _login_web(client)
    stale_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    # Tab B erneuert: der Cookie zeigt danach auf S2, S1 ist durch Rotation widerrufen.
    rotated = await client.post("/api/v1/auth/refresh", json={})
    assert rotated.status_code == 200
    current_cookie = rotated.cookies["lattice_refresh"]

    ok = await client.post(
        "/api/v1/me/password",
        json={"current_password": "correct-horse-battery", "new_password": "neues-passwort-1"},
        headers=stale_headers,
    )
    assert ok.status_code == 204, ok.text

    _browser_holds(client, current_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 200


@pytest.mark.asyncio
async def test_password_change_with_stale_session_still_signs_out_other_devices(client):
    """Gegenprobe zu Nr. 3: Auch mit veralteter `sid` werden fremde Anmeldungen
    abgemeldet, und der Vorgaenger der behaltenen Sitzung bekommt keine Gnadenfrist."""
    await client.post("/api/v1/auth/bootstrap", json={"username": "nico", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    phone = await client.post(
        "/api/v1/auth/login",
        json={"username": "nico", "password": "correct-horse-battery", "client_type": "android"},
    )
    phone_refresh = phone.json()["refresh_token"]
    web = await client.post("/api/v1/auth/login", json={"username": "nico", "password": "correct-horse-battery"})
    old_cookie = web.cookies["lattice_refresh"]
    stale_headers = {"Authorization": f"Bearer {web.json()['access_token']}"}
    rotated = await client.post("/api/v1/auth/refresh", json={})
    current_cookie = rotated.cookies["lattice_refresh"]

    ok = await client.post(
        "/api/v1/me/password",
        json={"current_password": "correct-horse-battery", "new_password": "neues-passwort-1"},
        headers=stale_headers,
    )
    assert ok.status_code == 204, ok.text

    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": phone_refresh})).status_code == 401
    _browser_holds(client, old_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401
    _browser_holds(client, current_cookie)
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 200


@pytest.mark.asyncio
async def test_password_change_after_fresh_login_ends_a_rotated_foreign_chain(client):
    """Grenze zu Nr. 3 (siehe `_live_session_head`): Wer eine Uebernahme vermutet und sich vor
    dem Passwortwechsel neu anmeldet, beendet auch eine weitergedrehte fremde Kette."""
    await client.post("/api/v1/auth/bootstrap", json={"username": "nico", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    stolen = await client.post(
        "/api/v1/auth/login",
        json={"username": "nico", "password": "correct-horse-battery", "client_type": "android"},
    )
    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen.json()["refresh_token"]})
    assert rotated.status_code == 200
    foreign_refresh = rotated.json()["refresh_token"]

    fresh = await client.post("/api/v1/auth/login", json={"username": "nico", "password": "correct-horse-battery"})
    ok = await client.post(
        "/api/v1/me/password",
        json={"current_password": "correct-horse-battery", "new_password": "neues-passwort-1"},
        headers={"Authorization": f"Bearer {fresh.json()['access_token']}"},
    )
    assert ok.status_code == 204, ok.text

    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": foreign_refresh})).status_code == 401
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 200  # die frische bleibt
