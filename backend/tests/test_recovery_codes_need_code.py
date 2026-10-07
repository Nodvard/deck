"""Neue Wiederherstellungs-Codes (`POST /me/recovery-codes`) verlangen ausser dem Passwort einen Code: den aktuellen aus
der App oder einen der bisherigen Wiederherstellungs-Codes.

Sonst liesse sich die Code-Pflicht beim Abschalten der Zwei-Faktor-Anmeldung umgehen: Wer nur das Passwort kennt, holte
sich frische Codes und schaltete damit ab (oder meldete sich damit an). Geprueft wird wie beim Abschalten."""

from __future__ import annotations

import json
import time

import pyotp
import pytest
from nodvard_deck.core import login_limit
from nodvard_deck.models import AuditEntry, Notification
from nodvard_deck.services import auth as auth_service
from sqlalchemy import select
from totp_helpers import setup_confirm_code

PASSWORD = "correct-horse-battery"


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(auth_service, "_totp_now", lambda: now[0])
    return now


def _code(secret: str, clock) -> str:
    return pyotp.TOTP(secret).at(clock[0])


def _wrong_codes(secret: str, clock, n: int) -> list[str]:
    totp = pyotp.TOTP(secret)
    valid = {totp.at(clock[0] + d) for d in (-30, 0, 30)}
    return [c for c in (f"{i:06d}" for i in range(n + 3)) if c not in valid][:n]


async def _owner(client) -> str:
    await client.post("/api/v1/auth/bootstrap", json={"username": "nico", "password": PASSWORD, "setup_code": "TEST-CODE-2345"})
    return (await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})).json()["access_token"]


async def _enable(client, token) -> tuple[str, list[str]]:
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=_h(token))).json()[
        "secret"
    ]
    r = await client.post("/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret, auth_service._totp_now())}, headers=_h(token))
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


async def _renew(client, token, *, password=PASSWORD, code: str | None = None):
    body: dict = {"current_password": password}
    if code is not None:
        body["totp_code"] = code
    return await client.post("/api/v1/me/recovery-codes", json=body, headers=_h(token))


async def _disable(client, token, code: str):
    return await client.request(
        "DELETE", "/api/v1/me/totp", json={"current_password": PASSWORD, "totp_code": code}, headers=_h(token)
    )


async def _remaining(client, token) -> int:
    return (await client.get("/api/v1/me", headers=_h(token))).json()["recovery_codes_remaining"]


async def _audit(db_session, action: str | None = None) -> list[AuditEntry]:
    db_session.expire_all()
    query = select(AuditEntry).order_by(AuditEntry.ts)
    if action is not None:
        query = query.where(AuditEntry.action == action)
    return list((await db_session.execute(query)).scalars().all())


def _assert_no_code_in_audit(entries: list[AuditEntry], codes: list[str]) -> None:
    for entry in entries:
        text = json.dumps(entry.detail or {}, ensure_ascii=False) + (entry.reason or "")
        for code in codes:
            for spelling in {code, code.replace("-", ""), code.lower()}:
                assert spelling not in text, (entry.action, spelling)


@pytest.mark.asyncio
async def test_the_password_alone_gets_no_fresh_codes_and_so_no_way_to_switch_off(client, db_session, clock):
    """Der Umweg, den die Code-Pflicht beim Abschalten sonst offen liesse: frische Codes nur mit dem Passwort holen."""
    token = await _owner(client)
    _secret, codes = await _enable(client, token)
    for code in (None, ""):
        r = await _renew(client, token, code=code)
        assert r.status_code == 403, r.text
        assert r.json()["code"] == "totp_missing" and "recovery_codes" not in r.json()
        assert "Wiederherstellungs-Code" in r.json()["detail"]
    assert await _remaining(client, token) == 10, "die alten Codes gelten weiter"
    assert not await _audit(db_session, "auth.totp_check_failed"), "ein fehlender Code ist kein Fehlversuch"
    assert len(await _audit(db_session, "auth.recovery_codes_generated")) == 1, "nur der Satz beim Einschalten"
    # Die alten Codes funktionieren noch (hier zum Abschalten).
    assert (await _disable(client, token, codes[0])).status_code == 204


@pytest.mark.asyncio
async def test_a_valid_app_code_gives_new_codes_and_only_the_result_is_logged(client, db_session, clock):
    token = await _owner(client)
    secret, old = await _enable(client, token)
    code = _code(secret, clock)
    r = await _renew(client, token, code=code)
    assert r.status_code == 200, r.text
    new = r.json()["recovery_codes"]
    assert len(new) == 10 and not set(new) & set(old)
    (_first, renewed) = await _audit(db_session, "auth.recovery_codes_generated")
    assert renewed.detail == {"reason": "renewed", "via": "totp"}
    # Die alten Codes gelten nicht mehr, und derselbe App-Code gilt auch fuer eine andere Bestaetigung nicht nochmal.
    r = await _disable(client, token, old[0])
    assert r.status_code == 400 and r.json()["detail"] == auth_service.WRONG_RECOVERY_MESSAGE
    r = await _disable(client, token, code)
    assert r.status_code == 400 and r.json()["detail"] == auth_service.TOTP_CODE_USED_MESSAGE
    _assert_no_code_in_audit(await _audit(db_session), [code, *old, *new])


@pytest.mark.asyncio
async def test_an_old_recovery_code_gives_new_codes_and_is_used_up(client, db_session, clock):
    token = await _owner(client)
    _secret, old = await _enable(client, token)
    r = await _renew(client, token, code=old[3].lower())
    assert r.status_code == 200, r.text
    new = r.json()["recovery_codes"]
    assert await _remaining(client, token) == 10
    (_first, renewed) = await _audit(db_session, "auth.recovery_codes_generated")
    assert renewed.detail == {"reason": "renewed", "via": "recovery_code"}
    again = await _renew(client, token, code=old[3])
    assert again.status_code == 400 and again.json()["detail"] == auth_service.WRONG_RECOVERY_MESSAGE
    assert await _remaining(client, token) == 10
    _assert_no_code_in_audit(await _audit(db_session), [*old, *new])


@pytest.mark.asyncio
async def test_new_codes_with_a_recovery_code_are_reported_like_when_signing_in(client, db_session, clock):
    """Wie bei der Anmeldung soll ein benutzter Wiederherstellungs-Code nie unbemerkt bleiben: Wer Passwort und einen
    Code hat, tauschte sonst still den ganzen Satz aus, und die restlichen Codes des Besitzers waeren ungueltig."""
    token = await _owner(client)
    secret, _old = await _enable(client, token)
    clock[0] += 30
    # Mit dem Code aus der App: keine Meldung, kein `mfa.recovery_used`.
    r = await _renew(client, token, code=_code(secret, clock))
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert (await db_session.execute(select(Notification))).first() is None
    assert not await _audit(db_session, "mfa.recovery_used")

    codes = r.json()["recovery_codes"]
    r = await _renew(client, token, code=codes[0])
    assert r.status_code == 200, r.text
    (used,) = await _audit(db_session, "mfa.recovery_used")
    assert used.detail == {"remaining": 10, "aktion": "codes_erneuern"}
    db_session.expire_all()
    (note,) = (await db_session.execute(select(Notification))).scalars().all()
    assert note.title == "Wiederherstellungs-Code benutzt" and note.severity == "warning"
    assert "beim Erzeugen neuer Wiederherstellungs-Codes" in note.body and "neuen Satz" in note.body
    for code in (*codes, *r.json()["recovery_codes"]):
        assert code not in note.body
    _assert_no_code_in_audit(await _audit(db_session), [*codes, *r.json()["recovery_codes"]])


@pytest.mark.asyncio
async def test_wrong_codes_count_on_the_account_limit(client, db_session, clock):
    token = await _owner(client)
    secret, _codes = await _enable(client, token)
    for code in _wrong_codes(secret, clock, login_limit.MAX_MFA_FAILURES_PER_ACCOUNT):
        r = await _renew(client, token, code=code)
        assert r.status_code == 400 and r.json()["detail"] == auth_service.WRONG_TOTP_MESSAGE
    blocked = await _renew(client, token, code=_code(secret, clock))
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers
    # Dieselbe Grenze je Konto wie beim Anmelden und beim Abschalten.
    assert (await _disable(client, token, _code(secret, clock))).status_code == 429
    failed = await _audit(db_session, "auth.totp_check_failed")
    assert len(failed) == login_limit.MAX_MFA_FAILURES_PER_ACCOUNT
    assert all(e.detail == {"aktion": "codes_erneuern"} for e in failed)


@pytest.mark.asyncio
async def test_a_wrong_password_comes_first_and_leaves_the_code_untouched(client, db_session, clock):
    token = await _owner(client)
    secret, old = await _enable(client, token)
    code = _code(secret, clock)
    for given in (code, old[0]):
        r = await _renew(client, token, password="falsch-falsch", code=given)
        assert r.status_code == 400 and r.json()["detail"] == "Das aktuelle Passwort stimmt nicht."
    assert await _remaining(client, token) == 10
    assert (await _renew(client, token, code=code)).status_code == 200
