"""Zwei-Faktor abschalten (`DELETE /me/totp`) verlangt ausser dem Passwort einen Code: den aktuellen aus der App oder
einen Wiederherstellungs-Code. Sonst kaeme, wer nur das Passwort kennt, durch Abschalten an jeder Code-Pflicht vorbei.

Es gilt dieselbe Pruefung wie beim Anmelden: ein App-Code nur einmal, falsche App-Codes zaehlen auf die Grenze je
Konto, ein Wiederherstellungs-Code wird verbraucht. Im Protokoll steht nur das Ergebnis, nie der Code."""

from __future__ import annotations

import json
import time

import pyotp
import pytest
from nodvard_deck.core import login_limit
from nodvard_deck.models import AuditEntry, Notification, RecoveryCode
from nodvard_deck.services import auth as auth_service
from sqlalchemy import select
from totp_helpers import setup_confirm_code

PASSWORD = "correct-horse-battery"


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


def _wrong_codes(secret: str, clock, n: int) -> list[str]:
    """`n` sechsstellige Codes, die gerade sicher NICHT gelten (auch nicht einen Schritt davor oder danach)."""
    totp = pyotp.TOTP(secret)
    valid = {totp.at(clock[0] + d) for d in (-30, 0, 30)}
    out: list[str] = []
    i = 0
    while len(out) < n:
        candidate = f"{i:06d}"
        i += 1
        if candidate not in valid:
            out.append(candidate)
    return out


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


async def _disable(client, token, *, password=PASSWORD, code: str | None = None):
    body: dict = {"current_password": password}
    if code is not None:
        body["totp_code"] = code
    return await client.request("DELETE", "/api/v1/me/totp", json=body, headers=_h(token))


async def _enabled(client, token) -> bool:
    return (await client.get("/api/v1/me", headers=_h(token))).json()["totp_enabled"]


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
async def test_without_a_code_the_answer_is_403_totp_missing_and_it_does_not_count(client, db_session, clock):
    token = await _owner(client)
    secret, _codes = await _enable(client, token, clock)
    # Eine aeltere App schickt nur das Passwort (oder ein leeres Feld): klare Antwort mit eigenem Bezeichner.
    for code in (None, "", "   "):
        r = await _disable(client, token, code=code)
        assert r.status_code == 403, r.text
        assert r.json()["code"] == "totp_missing"
        assert "Zwei-Faktor-Code" in r.json()["detail"] and "Wiederherstellungs-Code" in r.json()["detail"]
    assert await _enabled(client, token) is True
    assert not await _audit(db_session, "auth.totp_check_failed"), "ein fehlender Code ist kein Fehlversuch"
    # ... und zaehlt auch nicht auf die Grenze je Konto: so viele Anfragen ohne Code wie die Grenze, danach geht es.
    for _ in range(login_limit.MAX_MFA_FAILURES_PER_ACCOUNT):
        assert (await _disable(client, token)).status_code == 403
    assert (await _disable(client, token, code=_code(secret, clock))).status_code == 204
    assert await _enabled(client, token) is False


@pytest.mark.asyncio
async def test_a_valid_app_code_switches_it_off_and_only_the_result_is_logged(client, db_session, clock):
    token = await _owner(client)
    secret, codes = await _enable(client, token, clock)
    code = _code(secret, clock)
    r = await _disable(client, token, code=code)
    assert r.status_code == 204, r.text
    assert await _enabled(client, token) is False
    assert (await db_session.execute(select(RecoveryCode))).first() is None, "die Wiederherstellungs-Codes sind weg"
    (done,) = await _audit(db_session, "auth.2fa_disabled")
    assert done.outcome == "success" and done.detail["via"] == "totp"
    _assert_no_code_in_audit(await _audit(db_session), [code, *codes])


@pytest.mark.asyncio
async def test_a_wrong_app_code_is_refused_and_counts_on_the_account_limit(client, db_session, clock):
    token = await _owner(client)
    secret, codes = await _enable(client, token, clock)
    wrong = _wrong_codes(secret, clock, login_limit.MAX_MFA_FAILURES_PER_ACCOUNT)
    for code in wrong:
        r = await _disable(client, token, code=code)
        assert r.status_code == 400, r.text
        assert r.json() == {"detail": auth_service.WRONG_TOTP_MESSAGE, "code": "totp_wrong"}
    assert await _enabled(client, token) is True
    failed = await _audit(db_session, "auth.totp_check_failed")
    assert len(failed) == len(wrong)
    assert all(e.detail["aktion"] == "2fa_abschalten" and "via" not in e.detail for e in failed)

    # Gesperrt -- auch mit dem richtigen Code, und die Sperre gilt fuer das Konto, also auch beim Anmelden.
    blocked = await _disable(client, token, code=_code(secret, clock))
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers
    # Wiederherstellungs-Codes sperrt diese Grenze nicht (wie beim Anmelden): Das steht in der Antwort.
    assert blocked.json()["detail"].endswith(auth_service.RECOVERY_STILL_WORKS_HINT), blocked.text
    assert await _enabled(client, token) is True
    login = await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})
    assert login.status_code == 202
    mfa = await client.post("/api/v1/auth/mfa", json={"mfa_token": login.json()["mfa_token"], "code": _code(secret, clock)})
    assert mfa.status_code == 429
    (locked,) = await _audit(db_session, "auth.password_check_locked")
    assert locked.detail["scopes"] == ["account"]
    db_session.expire_all()
    (note,) = (await db_session.execute(select(Notification))).scalars().all()
    assert note.title == "Zwei-Faktor-Code wird durchprobiert"
    _assert_no_code_in_audit(await _audit(db_session), [*wrong, *codes])


@pytest.mark.asyncio
async def test_a_recovery_code_switches_it_off_and_is_used_up(client, db_session, clock):
    token = await _owner(client)
    _secret, codes = await _enable(client, token, clock)
    # Schreibweise egal, wie beim Anmelden.
    r = await _disable(client, token, code=f"  {codes[0].lower()} ")
    assert r.status_code == 204, r.text
    assert await _enabled(client, token) is False
    (done,) = await _audit(db_session, "auth.2fa_disabled")
    assert done.detail["via"] == "recovery_code"

    # Wieder eingeschaltet (neuer Satz Codes): derselbe Code gilt nicht mehr.
    _secret, new_codes = await _enable(client, token, clock)
    assert codes[0] not in new_codes
    again = await _disable(client, token, code=codes[0])
    assert again.status_code == 400, again.text
    assert again.json() == {"detail": auth_service.WRONG_RECOVERY_MESSAGE, "code": "totp_wrong"}
    assert await _enabled(client, token) is True
    (failed,) = await _audit(db_session, "auth.totp_check_failed")
    assert failed.detail == {"aktion": "2fa_abschalten", "via": "recovery_code"}
    _assert_no_code_in_audit(await _audit(db_session), [*codes, *new_codes])


@pytest.mark.asyncio
async def test_switching_off_with_a_recovery_code_is_reported_like_when_signing_in(client, db_session, clock):
    """Ein benutzter Wiederherstellungs-Code soll nie unbemerkt bleiben (wie bei der Anmeldung): Meldung und
    `mfa.recovery_used`. Mit dem Code aus der App gibt es beides nicht."""
    token = await _owner(client)
    secret, codes = await _enable(client, token, clock)
    assert (await _disable(client, token, code=_code(secret, clock))).status_code == 204
    db_session.expire_all()
    assert (await db_session.execute(select(Notification))).first() is None
    assert not await _audit(db_session, "mfa.recovery_used")

    clock[0] += 30
    _secret, codes = await _enable(client, token, clock)
    assert (await _disable(client, token, code=codes[4])).status_code == 204
    (used,) = await _audit(db_session, "mfa.recovery_used")
    assert used.detail == {"remaining": 0, "aktion": "2fa_abschalten"}
    db_session.expire_all()
    (note,) = (await db_session.execute(select(Notification))).scalars().all()
    assert note.title == "Wiederherstellungs-Code benutzt" and note.severity == "warning"
    assert "beim Abschalten der Zwei-Faktor-Anmeldung" in note.body and "wieder ein" in note.body
    assert all(code not in note.body for code in codes)
    _assert_no_code_in_audit(await _audit(db_session), codes)


@pytest.mark.asyncio
async def test_a_recovery_code_used_for_signing_in_does_not_work_again(client, db_session, clock):
    token = await _owner(client)
    _secret, codes = await _enable(client, token, clock)
    login = await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})
    assert (await client.post("/api/v1/auth/mfa", json={"mfa_token": login.json()["mfa_token"], "code": codes[1]})).status_code == 200
    r = await _disable(client, token, code=codes[1])
    assert r.status_code == 400 and r.json()["detail"] == auth_service.WRONG_RECOVERY_MESSAGE
    assert await _enabled(client, token) is True
    # Mit einem anderen, noch freien Code geht es; danach ist er verbraucht wie alle anderen.
    assert (await _disable(client, token, code=codes[2])).status_code == 204


@pytest.mark.asyncio
async def test_the_same_app_code_works_only_once(client, db_session, clock):
    token = await _owner(client)
    secret, _codes = await _enable(client, token, clock)
    code = _code(secret, clock)
    login = await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})
    assert (await client.post("/api/v1/auth/mfa", json={"mfa_token": login.json()["mfa_token"], "code": code})).status_code == 200
    r = await _disable(client, token, code=code)
    assert r.status_code == 400 and r.json() == {"detail": auth_service.TOTP_CODE_USED_MESSAGE, "code": "totp_used"}
    assert await _enabled(client, token) is True
    (failed,) = await _audit(db_session, "auth.totp_check_failed")
    assert failed.detail == {"aktion": "2fa_abschalten", "replayed": True}
    # Der naechste Code aus der App geht.
    clock[0] += 30
    assert (await _disable(client, token, code=_code(secret, clock))).status_code == 204


@pytest.mark.asyncio
async def test_the_code_that_confirmed_the_setup_does_not_work_again(client, db_session, clock):
    """Auch der Code, der die Einrichtung bestaetigt, gilt nur einmal: Sonst schaltete er die Zwei-Faktor-Anmeldung in
    den naechsten Sekunden gleich wieder ab (oder meldete jemanden an)."""
    token = await _owner(client)
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=_h(token))).json()[
        "secret"
    ]
    code = _code(secret, clock)
    assert (await client.post("/api/v1/me/totp/confirm", json={"code": code}, headers=_h(token))).status_code == 200
    r = await _disable(client, token, code=code)
    assert r.status_code == 400 and r.json()["detail"] == auth_service.TOTP_CODE_USED_MESSAGE
    assert await _enabled(client, token) is True
    login = await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})
    mfa = await client.post("/api/v1/auth/mfa", json={"mfa_token": login.json()["mfa_token"], "code": code})
    assert mfa.status_code == 401 and mfa.json()["detail"] == auth_service.TOTP_CODE_USED_MESSAGE
    # Der naechste Code aus der App geht.
    clock[0] += 30
    assert (await _disable(client, token, code=_code(secret, clock))).status_code == 204


@pytest.mark.asyncio
async def test_a_wrong_password_comes_first_and_leaves_the_code_untouched(client, db_session, clock):
    token = await _owner(client)
    secret, codes = await _enable(client, token, clock)
    code = _code(secret, clock)
    for given in (code, codes[0]):
        r = await _disable(client, token, password="falsch-falsch", code=given)
        assert r.status_code == 400 and r.json()["detail"] == "Das aktuelle Passwort stimmt nicht."
        assert "code" not in r.json()
    assert not await _audit(db_session, "auth.totp_check_failed")
    assert (await client.get("/api/v1/me", headers=_h(token))).json()["recovery_codes_remaining"] == 10
    # Weder der App-Code noch der Wiederherstellungs-Code ist dabei verbraucht worden.
    assert (await _disable(client, token, code=code)).status_code == 204
    secret, new_codes = await _enable(client, token, clock)
    clock[0] += 30
    r = await _disable(client, token, password="falsch-falsch", code=new_codes[0])
    assert r.status_code == 400
    assert (await _disable(client, token, code=new_codes[0])).status_code == 204


@pytest.mark.asyncio
async def test_without_two_factor_the_password_is_enough_as_before(client, db_session, clock):
    token = await _owner(client)
    assert (await _disable(client, token)).status_code == 204
    # Auch eine nur begonnene, nicht bestaetigte Einrichtung bricht das Passwort allein ab.
    assert (await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=_h(token))).status_code == 200
    assert (await _disable(client, token)).status_code == 204
    from nodvard_deck.models import User

    db_session.expire_all()
    user = (await db_session.execute(select(User))).scalar_one()
    assert user.totp_secret_id is None
    (*_, last) = await _audit(db_session, "auth.2fa_disabled")
    assert "via" not in last.detail


@pytest.mark.asyncio
async def test_recovery_codes_are_not_blocked_by_the_account_limit_like_when_signing_in(client, db_session, clock):
    """Wie beim Anmelden: Die Sperre je Konto (ausgeloest von jemandem, der das Passwort kennt und App-Codes raet)
    sperrt Wiederherstellungs-Codes nicht, und falsche Wiederherstellungs-Codes zaehlen nicht darauf."""
    token = await _owner(client)
    secret, codes = await _enable(client, token, clock)
    for code in _wrong_codes(secret, clock, login_limit.MAX_MFA_FAILURES_PER_ACCOUNT):
        await _disable(client, token, code=code)
    assert (await _disable(client, token, code=_code(secret, clock))).status_code == 429
    assert (await _disable(client, token, code=codes[0])).status_code == 204
    assert await _enabled(client, token) is False


@pytest.mark.asyncio
async def test_wrong_recovery_codes_are_limited_like_when_signing_in(client, db_session, clock):
    """Wie beim Anmelden (dort je Adresse und Name): nach 10 falschen Wiederherstellungs-Codes in 5 Minuten ist Schluss,
    auch wenn das Passwort davor jedes Mal stimmt. Den Zaehler der Passwortabfrage setzt das richtige Passwort zurueck,
    deshalb zaehlen falsche Wiederherstellungs-Codes auf einen eigenen."""
    token = await _owner(client)
    secret, codes = await _enable(client, token, clock)
    wrong = [f"AAAAA-AAAA{i}" for i in range(login_limit.MAX_FAILURES_PER_USER)]
    for code in wrong:
        r = await _disable(client, token, code=code)
        assert r.status_code == 400 and r.json()["detail"] == auth_service.WRONG_RECOVERY_MESSAGE
    blocked = await _disable(client, token, code=codes[0])
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers
    assert await _enabled(client, token) is True
    (locked,) = await _audit(db_session, "auth.password_check_locked")
    assert locked.detail["scopes"] == ["user"]
    assert f"{login_limit.MAX_FAILURES_PER_USER} Fehlversuche in 5 Minuten" in locked.reason, locked.reason
    # Der Wiederherstellungs-Code ist dabei nicht verbraucht worden, und die Sperre trifft nur diese Codes: Mit dem
    # Code aus der App (eigene Grenze je Konto) geht es weiter.
    assert (await client.get("/api/v1/me", headers=_h(token))).json()["recovery_codes_remaining"] == len(codes)
    assert (await _disable(client, token, code=_code(secret, clock))).status_code == 204
    _assert_no_code_in_audit(await _audit(db_session), [*wrong, *codes])


@pytest.mark.asyncio
async def test_wrong_recovery_codes_are_throttled_although_the_password_is_right(client, db_session, clock, monkeypatch):
    """Falsche Wiederherstellungs-Codes zaehlen auch auf den Zaehler aller Sicherheitsabfragen des Kontos, den kein
    richtiges Passwort zuruecksetzt. Dessen Grenze ist hier klein, damit der Test schnell bleibt."""
    monkeypatch.setattr(login_limit, "MAX_FAILURES_PER_IP", 3)
    token = await _owner(client)
    _secret, codes = await _enable(client, token, clock)
    wrong = ["AAAAA-AAAAA", "BBBBB-BBBBB", "CCCCC-CCCCC"]
    for code in wrong:
        r = await _disable(client, token, code=code)
        assert r.status_code == 400 and r.json()["detail"] == auth_service.WRONG_RECOVERY_MESSAGE
    blocked = await _disable(client, token, code=codes[0])
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers
    assert await _enabled(client, token) is True
    (locked,) = await _audit(db_session, "auth.password_check_locked")
    assert locked.detail["scopes"] == ["ip"]
    assert "3 Fehlversuche in 5 Minuten" in locked.reason, locked.reason
    assert "konto:" not in (locked.ip or "")
    # Falsche Wiederherstellungs-Codes zaehlen nicht auf die Grenze je Konto: kein Hinweis auf geratene App-Codes.
    db_session.expire_all()
    assert (await db_session.execute(select(Notification))).first() is None
    _assert_no_code_in_audit(await _audit(db_session), [*wrong, *codes])


@pytest.mark.asyncio
async def test_setting_up_again_while_it_is_on_cannot_replace_the_key(client, db_session, clock):
    """Kein Umweg ueber eine neue Einrichtung: Solange Zwei-Faktor an ist, lehnt `POST /me/totp/setup` ab (409), der
    Schluessel bleibt derselbe und es gibt keine neuen Codes. Abschalten geht nur ueber `DELETE /me/totp` mit Code."""
    from nodvard_deck.models import User

    token = await _owner(client)
    secret, codes = await _enable(client, token, clock)
    db_session.expire_all()
    before = (await db_session.execute(select(User))).scalar_one().totp_secret_id
    r = await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=_h(token))
    assert r.status_code == 409 and "secret" not in r.json()
    assert (await client.post("/api/v1/me/totp/confirm", json={"code": _code(secret, clock)}, headers=_h(token))).status_code == 409
    db_session.expire_all()
    assert (await db_session.execute(select(User))).scalar_one().totp_secret_id == before
    assert (await client.get("/api/v1/me", headers=_h(token))).json()["recovery_codes_remaining"] == len(codes)
    assert (await _disable(client, token, code=_code(secret, clock))).status_code == 204
