"""Wiederherstellungs-Codes fuer die Zwei-Faktor-Anmeldung: erzeugen beim Einrichten, Anmeldung
damit (einmalig), neu erzeugen mit Passwort, Zaehler, Audit, Drosselung."""

from __future__ import annotations

import re

import pyotp
import pytest
from nodvard_deck.models import AuditEntry, Notification, RecoveryCode
from sqlalchemy import select

PASSWORD = "correct-horse-battery"
CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{5}-[A-HJ-NP-Z2-9]{5}$")


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _setup(client, username="nico"):
    """Owner anlegen, anmelden, 2FA einrichten. -> (token, totp_secret, recovery_codes)"""
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": username, "password": PASSWORD, "setup_code": "TEST-CODE-2345"},
    )
    token = (await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})).json()[
        "access_token"
    ]
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=_h(token))).json()["secret"]
    confirm = await client.post(
        "/api/v1/me/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=_h(token)
    )
    assert confirm.status_code == 200, confirm.text
    return token, secret, confirm.json()["recovery_codes"]


async def _mfa_token(client, username="nico") -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 202, r.text
    return r.json()["mfa_token"]


async def _mfa(client, code, username="nico"):
    return await client.post("/api/v1/auth/mfa", json={"mfa_token": await _mfa_token(client, username), "code": code})


@pytest.mark.asyncio
async def test_confirming_2fa_returns_ten_distinct_codes_and_stores_only_hashes(client, db_session):
    _, _, codes = await _setup(client)
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(CODE_RE.match(c) for c in codes)

    rows = (await db_session.execute(select(RecoveryCode))).scalars().all()
    assert len(rows) == 10
    plain = {c.replace("-", "") for c in codes}
    for row in rows:
        assert row.code_hash.startswith("$argon2id$")
        assert row.code_hash not in plain and row.used_at is None
    # Die Klartext-Codes stehen nirgends in der Datenbank (auch nicht im Audit).
    audit = (await db_session.execute(select(AuditEntry))).scalars().all()
    assert not any(c in str(a.detail) + str(a.reason) for a in audit for c in codes)


@pytest.mark.asyncio
async def test_me_shows_remaining_count(client):
    token, _, _ = await _setup(client)
    me = (await client.get("/api/v1/me", headers=_h(token))).json()
    assert me["totp_enabled"] is True and me["recovery_codes_remaining"] == 10


@pytest.mark.asyncio
async def test_login_with_recovery_code_works_once(client, db_session):
    _, _, codes = await _setup(client)
    ok = await _mfa(client, codes[0])
    assert ok.status_code == 200, ok.text
    assert ok.json()["access_token"]

    # Zweiter Versuch mit demselben Code: verbraucht.
    again = await _mfa(client, codes[0])
    assert again.status_code == 401
    assert "Wiederherstellungs-Code" in again.json()["detail"]

    me = (await client.get("/api/v1/me", headers=_h(ok.json()["access_token"]))).json()
    assert me["recovery_codes_remaining"] == 9

    used = (await db_session.execute(select(RecoveryCode).where(RecoveryCode.used_at.is_not(None)))).scalars().all()
    assert len(used) == 1


@pytest.mark.asyncio
async def test_recovery_code_spelling_does_not_matter(client):
    _, _, codes = await _setup(client)
    sloppy = " " + codes[3].lower().replace("-", " ") + " "
    assert (await _mfa(client, sloppy)).status_code == 200


@pytest.mark.asyncio
async def test_wrong_recovery_code_is_rejected_and_audited(client, db_session):
    await _setup(client)
    bad = await _mfa(client, "AAAAA-BBBBB")
    assert bad.status_code == 401
    failed = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "mfa.failed"))).scalars().all()
    assert len(failed) == 1 and failed[0].detail == {"via": "recovery_code"}


@pytest.mark.asyncio
async def test_recovery_use_writes_audit_and_notification(client, db_session):
    _, _, codes = await _setup(client)
    assert (await _mfa(client, codes[1])).status_code == 200
    entries = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "mfa.recovery_used"))).scalars().all()
    assert len(entries) == 1
    assert entries[0].detail == {"remaining": 9} and entries[0].outcome == "success"
    succeeded = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "login.succeeded"))).scalars().all()
    assert any(e.detail.get("via") == "recovery_code" for e in succeeded)

    notes = (await db_session.execute(select(Notification))).scalars().all()
    assert len(notes) == 1
    assert notes[0].severity == "warning"
    assert "Wiederherstellungs-Code" in notes[0].title and "noch 9" in notes[0].body
    assert codes[1] not in notes[0].body


@pytest.mark.asyncio
async def test_failed_notification_never_blocks_login(client, monkeypatch):
    from nodvard_deck.services import notifications as notifications_service

    async def boom(*_a, **_k):
        raise RuntimeError("Kanal kaputt")

    monkeypatch.setattr(notifications_service, "send", boom)
    _, _, codes = await _setup(client)
    assert (await _mfa(client, codes[0])).status_code == 200


@pytest.mark.asyncio
async def test_totp_still_works_and_code_type_is_not_confused(client):
    _, secret, _ = await _setup(client)
    assert (await _mfa(client, pyotp.TOTP(secret).now())).status_code == 200
    # Sechs Ziffern werden nie als Wiederherstellungs-Code geprueft.
    assert (await _mfa(client, "000000")).status_code == 401


@pytest.mark.asyncio
async def test_recovery_attempts_count_against_the_mfa_token_limit(client):
    await _setup(client)
    mfa_token = await _mfa_token(client)
    for _ in range(5):
        r = await client.post("/api/v1/auth/mfa", json={"mfa_token": mfa_token, "code": "AAAAA-BBBBB"})
        assert r.status_code in (401, 429)
    r = await client.post("/api/v1/auth/mfa", json={"mfa_token": mfa_token, "code": "AAAAA-BBBBB"})
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_regenerate_needs_password_and_invalidates_old_codes(client, db_session):
    token, _, old = await _setup(client)

    wrong = await client.post("/api/v1/me/recovery-codes", json={"current_password": "falsch"}, headers=_h(token))
    assert wrong.status_code == 400
    assert (await client.get("/api/v1/me", headers=_h(token))).json()["recovery_codes_remaining"] == 10

    r = await client.post("/api/v1/me/recovery-codes", json={"current_password": PASSWORD}, headers=_h(token))
    assert r.status_code == 200, r.text
    new = r.json()["recovery_codes"]
    assert len(new) == 10 and not set(new) & set(old)

    rows = (await db_session.execute(select(RecoveryCode))).scalars().all()
    assert len(rows) == 10  # alte Zeilen sind weg, nicht nur markiert

    assert (await _mfa(client, old[0])).status_code == 401
    assert (await _mfa(client, new[0])).status_code == 200


@pytest.mark.asyncio
async def test_regenerate_resets_the_remaining_counter(client):
    token, _, codes = await _setup(client)
    assert (await _mfa(client, codes[0])).status_code == 200
    assert (await client.get("/api/v1/me", headers=_h(token))).json()["recovery_codes_remaining"] == 9
    await client.post("/api/v1/me/recovery-codes", json={"current_password": PASSWORD}, headers=_h(token))
    assert (await client.get("/api/v1/me", headers=_h(token))).json()["recovery_codes_remaining"] == 10


@pytest.mark.asyncio
async def test_regenerate_requires_active_2fa(client):
    await client.post(
        "/api/v1/auth/bootstrap", json={"username": "nico", "password": PASSWORD, "setup_code": "TEST-CODE-2345"}
    )
    token = (await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})).json()["access_token"]
    r = await client.post("/api/v1/me/recovery-codes", json={"current_password": PASSWORD}, headers=_h(token))
    assert r.status_code == 409
    me = (await client.get("/api/v1/me", headers=_h(token))).json()
    assert me["recovery_codes_remaining"] == 0


@pytest.mark.asyncio
async def test_regenerate_requires_login(client):
    assert (await client.post("/api/v1/me/recovery-codes", json={"current_password": "x"})).status_code == 401


@pytest.mark.asyncio
async def test_disabling_2fa_deletes_the_codes(client, db_session):
    token, _, _ = await _setup(client)
    assert (await client.request("DELETE", "/api/v1/me/totp", json={"current_password": PASSWORD}, headers=_h(token))).status_code == 204
    assert (await db_session.execute(select(RecoveryCode))).first() is None
    assert (await client.get("/api/v1/me", headers=_h(token))).json()["recovery_codes_remaining"] == 0
    # Login geht wieder ohne zweiten Faktor -- der alte Code ist nirgends mehr gueltig.
    assert (await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})).status_code == 200


@pytest.mark.asyncio
async def test_codes_of_one_user_do_not_work_for_another(client, db_session):
    token, _, codes = await _setup(client)
    # zweiter Nutzer mit eigener 2FA
    await client.post(
        "/api/v1/users", json={"username": "anna", "password": PASSWORD}, headers=_h(token)
    )
    anna_token = (await client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD})).json()["access_token"]
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=_h(anna_token))).json()["secret"]
    await client.post("/api/v1/me/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=_h(anna_token))
    assert (await _mfa(client, codes[0], username="anna")).status_code == 401
    # Nicos Code ist dadurch nicht verbraucht.
    assert (await _mfa(client, codes[0], username="nico")).status_code == 200
