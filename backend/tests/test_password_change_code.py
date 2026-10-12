"""Passwort aendern (`POST /me/password`) verlangt bei eingeschalteter Zwei-Faktor-Anmeldung ausser dem aktuellen
Passwort einen Code: den aktuellen aus der App oder einen Wiederherstellungs-Code. Sonst kaeme, wer nur das Passwort
kennt (etwa von einer offenen Sitzung aus), mit einem neuen Passwort an allem vorbei, was danach noch den Code
verlangt, und sperrte den Besitzer aus.

Es gilt dieselbe Pruefung wie beim Abschalten (`test_totp_disable_code.py`): erst das Passwort, dann der Code; ein
App-Code nur einmal, falsche Codes zaehlen, ein Wiederherstellungs-Code wird verbraucht und gemeldet. Ohne
Zwei-Faktor aendert sich nichts."""

from __future__ import annotations

import json
import time

import pyotp
import pytest
from nodvard_deck.core import login_limit, security
from nodvard_deck.models import AuditEntry, Notification, RecoveryCode
from nodvard_deck.services import auth as auth_service
from sqlalchemy import select
from totp_helpers import setup_confirm_code

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "ganz-neues-passwort-42"

APP_HINT = "Nutzt du die Handy-App und siehst kein Feld dafür, aktualisiere zuerst die App."


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def clock(monkeypatch):
    """Feste Uhr fuer die Zwei-Faktor-Pruefung: `clock[0] += 30` gibt den naechsten Code."""
    now = [time.time()]
    monkeypatch.setattr(auth_service, "_totp_now", lambda: now[0])
    return now


def _code(secret: str, clock) -> str:
    return pyotp.TOTP(secret).at(clock[0])


def _wrong_code(secret: str, clock) -> str:
    totp = pyotp.TOTP(secret)
    valid = {totp.at(clock[0] + d) for d in (-30, 0, 30)}
    return next(f"{i:06d}" for i in range(1_000_000) if f"{i:06d}" not in valid)


async def _owner(client, username="nico") -> str:
    await client.post(
        "/api/v1/auth/bootstrap", json={"username": username, "password": PASSWORD, "setup_code": "TEST-CODE-2345"}
    )
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _enable(client, token, clock) -> tuple[str, list[str]]:
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=_h(token))).json()[
        "secret"
    ]
    r = await client.post("/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret, clock[0])}, headers=_h(token))
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


async def _change(client, token, *, password=PASSWORD, new=NEW_PASSWORD, code: str | None = None):
    body: dict = {"current_password": password, "new_password": new}
    if code is not None:
        body["totp_code"] = code
    return await client.post("/api/v1/me/password", json=body, headers=_h(token))


async def _can_log_in_with(client, password: str, username="nico") -> bool:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return r.status_code in (200, 202)


async def _audit(db_session, action: str | None = None) -> list[AuditEntry]:
    db_session.expire_all()
    query = select(AuditEntry).order_by(AuditEntry.ts)
    if action is not None:
        query = query.where(AuditEntry.action == action)
    return list((await db_session.execute(query)).scalars().all())


def _assert_no_code_in_audit(entries: list[AuditEntry], codes: list[str]) -> None:
    for entry in entries:
        text = json.dumps(entry.detail or {}, ensure_ascii=False) + (entry.reason or "")
        assert NEW_PASSWORD not in text and PASSWORD not in text, entry.action
        for code in codes:
            for spelling in {code, code.replace("-", ""), code.lower()}:
                assert spelling not in text, (entry.action, spelling)


@pytest.mark.asyncio
async def test_without_a_code_the_answer_is_403_totp_missing_with_a_text_that_stands_for_itself(client, db_session, clock):
    token = await _owner(client)
    secret, _codes = await _enable(client, token, clock)
    # Eine aeltere App schickt nur die beiden Passwoerter (oder ein leeres Feld).
    for code in (None, "", "   "):
        r = await _change(client, token, code=code)
        assert r.status_code == 403, r.text
        body = r.json()
        assert body["code"] == "totp_missing"
        # Das `detail` zeigt die App an: Es muss ohne Zusatz verstaendlich sein und auf das Update der App hinweisen.
        assert body["detail"] == auth_service.PASSWORD_CHANGE_TOTP_REQUIRED_MESSAGE
        assert body["detail"].startswith("Zum Ändern des Passworts brauchst du jetzt deinen Zwei-Faktor-Code.")
        assert body["detail"].endswith(APP_HINT)
    # Das Passwort ist unveraendert, nichts im Protokoll ausser den Pruefungen, kein Fehlversuch.
    assert await _can_log_in_with(client, PASSWORD) and not await _can_log_in_with(client, NEW_PASSWORD)
    assert not await _audit(db_session, "auth.password_change")
    assert not await _audit(db_session, "auth.totp_check_failed"), "ein fehlender Code ist kein Fehlversuch"
    # ... und er zaehlt nicht auf die Grenze je Konto: so viele Anfragen ohne Code wie die Grenze, danach geht es.
    for _ in range(login_limit.MAX_MFA_FAILURES_PER_ACCOUNT):
        assert (await _change(client, token)).status_code == 403
    assert (await _change(client, token, code=_code(secret, clock))).status_code == 204


@pytest.mark.asyncio
async def test_the_other_actions_keep_their_own_text_for_a_missing_code(client, db_session, clock):
    """Nur das Passwort-Aendern bekommt den Satz mit dem Hinweis auf die App; Abschalten und neue Codes bleiben."""
    token = await _owner(client)
    await _enable(client, token, clock)
    off = await client.request("DELETE", "/api/v1/me/totp", json={"current_password": PASSWORD}, headers=_h(token))
    assert off.status_code == 403
    assert off.json() == {"detail": auth_service.TOTP_OR_RECOVERY_REQUIRED_MESSAGE, "code": "totp_missing"}
    renew = await client.post("/api/v1/me/recovery-codes", json={"current_password": PASSWORD}, headers=_h(token))
    assert renew.status_code == 403
    assert renew.json() == {"detail": auth_service.TOTP_OR_RECOVERY_REQUIRED_MESSAGE, "code": "totp_missing"}
    assert auth_service.PASSWORD_CHANGE_TOTP_REQUIRED_MESSAGE != auth_service.TOTP_OR_RECOVERY_REQUIRED_MESSAGE
    assert "Handy-App" not in auth_service.TOTP_OR_RECOVERY_REQUIRED_MESSAGE
    assert "Handy-App" not in auth_service.TOTP_REQUIRED_MESSAGE


@pytest.mark.asyncio
async def test_a_valid_app_code_changes_the_password_and_only_the_result_is_logged(client, db_session, clock):
    token = await _owner(client)
    secret, codes = await _enable(client, token, clock)
    # Eine andere Anmeldung (die App auf dem verlorenen Handy): sie endet, wie beim Passwortwechsel ueblich.
    login = await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD, "client_type": "android"})
    assert login.status_code == 202
    code = _code(secret, clock)
    mfa = await client.post("/api/v1/auth/mfa", json={"mfa_token": login.json()["mfa_token"], "code": code})
    assert mfa.status_code == 200, mfa.text
    phone_refresh = mfa.json()["refresh_token"]
    clock[0] += 30

    code = _code(secret, clock)
    r = await _change(client, token, code=code)
    assert r.status_code == 204, r.text
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": phone_refresh})).status_code == 401
    assert not await _can_log_in_with(client, PASSWORD)
    assert await _can_log_in_with(client, NEW_PASSWORD)
    (done,) = await _audit(db_session, "auth.password_change")
    assert done.outcome == "success" and done.detail["via"] == "totp" and "signed_out_sessions" in done.detail
    _assert_no_code_in_audit(await _audit(db_session), [code, *codes])


@pytest.mark.asyncio
async def test_a_wrong_app_code_is_refused_counts_and_leaves_the_password_alone(client, db_session, clock):
    token = await _owner(client)
    secret, codes = await _enable(client, token, clock)
    wrong = _wrong_code(secret, clock)
    r = await _change(client, token, code=wrong)
    assert r.status_code == 400, r.text
    assert r.json() == {"detail": auth_service.WRONG_TOTP_MESSAGE, "code": "totp_wrong"}
    assert await _can_log_in_with(client, PASSWORD) and not await _can_log_in_with(client, NEW_PASSWORD)
    (failed,) = await _audit(db_session, "auth.totp_check_failed")
    assert failed.detail == {"aktion": "passwort_aendern"}
    assert not await _audit(db_session, "auth.password_change")

    # Es zaehlt auf die Grenze je Konto: danach ist auch der richtige Code gesperrt (429).
    for _ in range(login_limit.MAX_MFA_FAILURES_PER_ACCOUNT - 1):
        assert (await _change(client, token, code=wrong)).status_code == 400
    blocked = await _change(client, token, code=_code(secret, clock))
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers
    assert blocked.json()["detail"].endswith(auth_service.RECOVERY_STILL_WORKS_HINT)
    assert await _can_log_in_with(client, PASSWORD)
    _assert_no_code_in_audit(await _audit(db_session), [wrong, *codes])


@pytest.mark.asyncio
async def test_the_same_app_code_works_only_once(client, db_session, clock):
    token = await _owner(client)
    secret, _codes = await _enable(client, token, clock)
    code = _code(secret, clock)
    login = await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})
    assert (await client.post("/api/v1/auth/mfa", json={"mfa_token": login.json()["mfa_token"], "code": code})).status_code == 200
    r = await _change(client, token, code=code)
    assert r.status_code == 400 and r.json() == {"detail": auth_service.TOTP_CODE_USED_MESSAGE, "code": "totp_used"}
    assert await _can_log_in_with(client, PASSWORD)
    clock[0] += 30
    assert (await _change(client, token, code=_code(secret, clock))).status_code == 204


@pytest.mark.asyncio
async def test_a_recovery_code_changes_the_password_is_used_up_and_reported(client, db_session, clock):
    token = await _owner(client)
    _secret, codes = await _enable(client, token, clock)
    # Schreibweise egal, wie beim Anmelden und Abschalten.
    r = await _change(client, token, code=f"  {codes[0].lower()} ")
    assert r.status_code == 204, r.text
    assert await _can_log_in_with(client, NEW_PASSWORD) and not await _can_log_in_with(client, PASSWORD)
    (done,) = await _audit(db_session, "auth.password_change")
    assert done.detail["via"] == "recovery_code"
    (used,) = await _audit(db_session, "mfa.recovery_used")
    assert used.detail == {"remaining": 9, "aktion": "passwort_aendern"}
    db_session.expire_all()
    (note,) = (await db_session.execute(select(Notification))).scalars().all()
    assert note.title == "Wiederherstellungs-Code benutzt" and note.severity == "warning"
    assert "beim Ändern des Passworts" in note.body, "nicht „bei der Anmeldung“"
    assert "bei der Anmeldung" not in note.body
    assert "Es sind noch 9 Codes übrig." in note.body
    assert all(code not in note.body for code in codes)

    # Verbraucht: derselbe Code gilt nicht noch einmal.
    again = await _change(client, token, password=NEW_PASSWORD, new="noch-ein-neues-passwort", code=codes[0])
    assert again.status_code == 400, again.text
    assert again.json() == {"detail": auth_service.WRONG_RECOVERY_MESSAGE, "code": "totp_wrong"}
    assert await _can_log_in_with(client, NEW_PASSWORD)
    _assert_no_code_in_audit(await _audit(db_session), codes)


@pytest.mark.asyncio
async def test_an_app_code_is_not_reported_like_a_recovery_code(client, db_session, clock):
    token = await _owner(client)
    secret, _codes = await _enable(client, token, clock)
    assert (await _change(client, token, code=_code(secret, clock))).status_code == 204
    db_session.expire_all()
    assert (await db_session.execute(select(Notification))).first() is None
    assert not await _audit(db_session, "mfa.recovery_used")


@pytest.mark.asyncio
async def test_a_wrong_password_comes_first_and_leaves_the_code_untouched(client, db_session, clock):
    token = await _owner(client)
    secret, codes = await _enable(client, token, clock)
    code = _code(secret, clock)
    for given in (code, codes[0]):
        r = await _change(client, token, password="falsch-falsch", code=given)
        assert r.status_code == 400 and r.json()["detail"] == "Das aktuelle Passwort stimmt nicht."
        assert "code" not in r.json()
    # Auch ohne Code: das falsche Passwort wird zuerst gemeldet, nicht „Code fehlt“.
    r = await _change(client, token, password="falsch-falsch")
    assert r.status_code == 400 and "code" not in r.json()
    assert not await _audit(db_session, "auth.totp_check_failed")
    assert (await client.get("/api/v1/me", headers=_h(token))).json()["recovery_codes_remaining"] == 10
    # Weder der App-Code noch der Wiederherstellungs-Code ist dabei verbraucht worden.
    assert (await _change(client, token, code=codes[0])).status_code == 204
    assert (await client.get("/api/v1/me", headers=_h(token))).json()["recovery_codes_remaining"] == 9


@pytest.mark.asyncio
async def test_the_same_new_password_is_refused_without_using_up_the_code(client, db_session, clock):
    """„Neues Passwort gleich dem alten“ braucht keinen Code und verbraucht auch keinen."""
    token = await _owner(client)
    _secret, codes = await _enable(client, token, clock)
    r = await _change(client, token, new=PASSWORD, code=codes[0])
    assert r.status_code == 422, r.text
    assert (await client.get("/api/v1/me", headers=_h(token))).json()["recovery_codes_remaining"] == 10
    assert not await _audit(db_session, "mfa.recovery_used")
    assert (await _change(client, token, code=codes[0])).status_code == 204


@pytest.mark.asyncio
async def test_the_new_password_is_hashed_before_a_recovery_code_is_used_up(client, db_session, clock, monkeypatch):
    """Der Hash des neuen Passworts dauert (auf einem Pi Sekunden). Mit einem Wiederherstellungs-Code haelt die Anfrage
    ab dem Verbrauch des Codes die Schreibsperre der Datenbank: gerechnet wird davor, wie bei neuen Codes."""
    token = await _owner(client)
    _secret, codes = await _enable(client, token, clock)
    events: list[str] = []
    real_hash = security.hash_password_async
    real_reload = auth_service._reload_unchanged

    async def hash_spy(password, **kwargs):
        events.append("hash")
        return await real_hash(password, **kwargs)

    async def reload_spy(*args, **kwargs):
        events.append("claim")  # unmittelbar vor dem Markieren des Codes als benutzt
        return await real_reload(*args, **kwargs)

    monkeypatch.setattr(security, "hash_password_async", hash_spy)
    monkeypatch.setattr(auth_service, "_reload_unchanged", reload_spy)
    assert (await _change(client, token, code=codes[0])).status_code == 204
    assert events == ["hash", "claim"]
    assert await _can_log_in_with(client, NEW_PASSWORD)


@pytest.mark.asyncio
async def test_without_two_factor_the_password_is_enough_as_before(client, db_session, clock):
    token = await _owner(client)
    assert (await _change(client, token)).status_code == 204
    assert await _can_log_in_with(client, NEW_PASSWORD)
    (done,) = await _audit(db_session, "auth.password_change")
    assert "via" not in done.detail
    # Ein mitgeschickter Code wird ohne Zwei-Faktor weder geprueft noch gezaehlt.
    assert (await _change(client, token, password=NEW_PASSWORD, new="und-noch-eins-1234", code="000000")).status_code == 204
    assert not await _audit(db_session, "auth.totp_check_failed")
    db_session.expire_all()
    assert (await db_session.execute(select(RecoveryCode))).first() is None
