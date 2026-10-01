"""Empfindliche Konto-Aktionen (Passwort aendern, 2FA abschalten, neue Wiederherstellungs-Codes)
verlangen das aktuelle Passwort -- falsche Eingaben werden gedrosselt und protokolliert."""

from __future__ import annotations

import pyotp
import pytest
from nodvard_deck.core import login_limit
from nodvard_deck.models import AuditEntry, User
from sqlalchemy import select

PASSWORD = "correct-horse-battery"


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _setup(client, username="nico"):
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": username, "password": PASSWORD, "setup_code": "TEST-CODE-2345"},
    )
    token = (await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})).json()["access_token"]
    secret = (await client.post("/api/v1/me/totp/setup", headers=_h(token))).json()["secret"]
    r = await client.post("/api/v1/me/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=_h(token))
    assert r.status_code == 200
    return token


async def _disable(client, token, password):
    return await client.request("DELETE", "/api/v1/me/totp", json={"current_password": password}, headers=_h(token))


async def _change(client, token, password):
    return await client.post(
        "/api/v1/me/password", json={"current_password": password, "new_password": "ganz-neues-passwort"}, headers=_h(token)
    )


@pytest.mark.asyncio
async def test_disable_2fa_needs_the_current_password(client, db_session):
    token = await _setup(client)

    assert (await client.request("DELETE", "/api/v1/me/totp", headers=_h(token))).status_code == 422

    wrong = await _disable(client, token, "falsch")
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == "Das aktuelle Passwort stimmt nicht."
    assert (await client.get("/api/v1/me", headers=_h(token))).json()["totp_enabled"] is True

    assert (await _disable(client, token, PASSWORD)).status_code == 204
    assert (await client.get("/api/v1/me", headers=_h(token))).json()["totp_enabled"] is False


@pytest.mark.asyncio
async def test_wrong_password_is_audited_without_leaking_it(client, db_session):
    token = await _setup(client)
    await _disable(client, token, "geheim-xyz")
    await _change(client, token, "anderes-xyz")
    entries = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "auth.password_check_failed"))).scalars().all()
    assert sorted(e.detail["aktion"] for e in entries) == ["2fa_abschalten", "passwort_aendern"]
    assert all(e.outcome == "failure" and e.target_type == "user" for e in entries)
    assert not any(w in str(e.detail) + str(e.reason) for e in entries for w in ("geheim-xyz", "anderes-xyz"))
    user = (await db_session.execute(select(User))).scalar_one()
    assert all(e.target_id == user.id for e in entries)


@pytest.mark.asyncio
async def test_repeated_wrong_passwords_are_throttled_across_all_sensitive_endpoints(client, db_session):
    token = await _setup(client)
    # Gemischt ueber die drei Endpunkte: alle zaehlen auf dasselbe Konto-Konto.
    for i in range(login_limit.MAX_FAILURES_PER_USER):
        if i % 3 == 0:
            r = await _disable(client, token, "falsch")
        elif i % 3 == 1:
            r = await _change(client, token, "falsch")
        else:
            r = await client.post("/api/v1/me/recovery-codes", json={"current_password": "falsch"}, headers=_h(token))
        assert r.status_code == 400, (i, r.text)

    for blocked in (
        await _disable(client, token, PASSWORD),  # auch das richtige Passwort kommt waehrend der Sperre nicht durch
        await _change(client, token, PASSWORD),
        await client.post("/api/v1/me/recovery-codes", json={"current_password": PASSWORD}, headers=_h(token)),
    ):
        assert blocked.status_code == 429
        assert "Retry-After" in blocked.headers
        assert "Fehlversuche" in blocked.json()["detail"]
    assert (await client.get("/api/v1/me", headers=_h(token))).json()["totp_enabled"] is True
    # Die Sperre hat einen eigenen, passend formulierten Eintrag (nicht den der Anmeldung) mit der echten IP.
    assert (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "login.locked"))).first() is None
    (locked,) = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "auth.password_check_locked"))).scalars().all()
    assert locked.outcome == "failure"
    assert locked.ip == "127.0.0.1"
    assert "konto:" not in (locked.ip or "") and "Benutzernamen" not in (locked.reason or "")
    assert "Sicherheitsabfrage" in locked.reason and "10 Fehlversuche" in locked.reason
    assert locked.target_type == "user" and locked.actor_type == "user"


@pytest.mark.asyncio
async def test_throttle_is_per_user_and_does_not_block_login(client):
    token = await _setup(client)
    for _ in range(login_limit.MAX_FAILURES_PER_USER):
        await _disable(client, token, "falsch")
    assert (await _disable(client, token, PASSWORD)).status_code == 429
    # Andere Nutzer (und der normale Login) sind nicht betroffen.
    await client.post("/api/v1/users", json={"username": "anna", "password": PASSWORD}, headers=_h(token))
    anna = (await client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD})).json()["access_token"]
    assert (await _change(client, anna, "falsch")).status_code == 400
    assert (await client.post("/api/v1/auth/login", json={"username": "nico", "password": "falsch"})).status_code == 401


@pytest.mark.asyncio
async def test_success_clears_the_counter(client):
    token = await _setup(client)
    for _ in range(login_limit.MAX_FAILURES_PER_USER - 1):
        assert (await _change(client, token, "falsch")).status_code == 400
    assert (await client.post("/api/v1/me/recovery-codes", json={"current_password": PASSWORD}, headers=_h(token))).status_code == 200
    # Zaehler wurde zurueckgesetzt: wieder 9 Fehlversuche frei.
    for _ in range(login_limit.MAX_FAILURES_PER_USER - 1):
        assert (await _change(client, token, "falsch")).status_code == 400


@pytest.mark.asyncio
async def test_password_change_still_works_with_the_right_password(client):
    token = await _setup(client)
    assert (await _change(client, token, PASSWORD)).status_code == 204


async def _start_totp(client, username="nico"):
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": username, "password": PASSWORD, "setup_code": "TEST-CODE-2345"},
    )
    token = (await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})).json()["access_token"]
    secret = (await client.post("/api/v1/me/totp/setup", headers=_h(token))).json()["secret"]
    return token, secret


@pytest.mark.asyncio
async def test_confirm_on_already_active_2fa_is_refused(client, db_session):
    from nodvard_deck.models import RecoveryCode

    token, secret = await _start_totp(client)
    first = await client.post("/api/v1/me/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=_h(token))
    assert first.status_code == 200
    before = {r.id for r in (await db_session.execute(select(RecoveryCode))).scalars().all()}

    # Mit gueltigem Code, aber ohne Passwort: kein neuer Satz Codes.
    second = await client.post("/api/v1/me/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=_h(token))
    assert second.status_code == 409
    assert second.json()["detail"] == "Zwei-Faktor ist schon aktiv."
    after = {r.id for r in (await db_session.execute(select(RecoveryCode))).scalars().all()}
    assert after == before


@pytest.mark.asyncio
async def test_wrong_confirm_codes_are_throttled_and_audited(client, db_session):
    token, secret = await _start_totp(client)
    valid = {pyotp.TOTP(secret).at(__import__("time").time() + o) for o in (-30, 0, 30)}
    wrong = next(c for c in (f"{n:06d}" for n in range(1000000)) if c not in valid)
    for _ in range(login_limit.MAX_FAILURES_PER_USER):
        r = await client.post("/api/v1/me/totp/confirm", json={"code": wrong}, headers=_h(token))
        assert r.status_code == 400
    blocked = await client.post("/api/v1/me/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=_h(token))
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers
    failed = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "auth.totp_confirm_failed"))).scalars().all()
    assert len(failed) == login_limit.MAX_FAILURES_PER_USER
    assert (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "auth.password_check_locked"))).scalars().all()


@pytest.mark.asyncio
async def test_right_confirm_code_still_works_after_a_few_wrong_ones(client):
    token, secret = await _start_totp(client)
    for _ in range(3):
        assert (await client.post("/api/v1/me/totp/confirm", json={"code": "000000"}, headers=_h(token))).status_code in (400,)
    ok = await client.post("/api/v1/me/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=_h(token))
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_disabling_2fa_signs_out_other_sessions_but_keeps_the_current_one(client, db_session):
    from nodvard_deck.models import RefreshToken

    token = await _setup(client)
    other = await client.post(
        "/api/v1/auth/login", json={"username": "nico", "password": PASSWORD, "client_type": "android"}
    )
    # 2FA ist an -> Login liefert 202; fuer die zweite Sitzung kurz per Datenbank eine Zeile anlegen.
    assert other.status_code == 202
    user = (await db_session.execute(select(User))).scalar_one()
    from datetime import timedelta

    from nodvard_deck.db import utcnow

    db_session.add(RefreshToken(user_id=user.id, token_hash="x" * 64, client_type="android", expires_at=utcnow() + timedelta(days=1)))
    await db_session.flush()

    assert (await _disable(client, token, PASSWORD)).status_code == 204
    rows = (await db_session.execute(select(RefreshToken))).scalars().all()
    by_hash = {r.token_hash: r for r in rows}
    assert by_hash["x" * 64].revoked_at is not None
    current = [r for r in rows if r.token_hash != "x" * 64]
    assert current and all(r.revoked_at is None for r in current)
    # Die aktuelle Sitzung (Access-Token) bleibt nutzbar.
    assert (await client.get("/api/v1/me", headers=_h(token))).status_code == 200
