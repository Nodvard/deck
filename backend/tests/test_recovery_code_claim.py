"""Ein Wiederherstellungs-Code bei einer Bestaetigung (Zwei-Faktor abschalten, neue Codes): Er wird mit der Anfrage
verbraucht. Scheitert sie danach, ist er wieder frei; gleichzeitig zweimal eingegeben, gewinnt nur eine Anfrage; und die
lange Arbeit fuer neue Codes (Argon2) laeuft nicht, waehrend die Anfrage die Schreibsperre der Datenbank haelt -- auch
nicht bei den ersten Codes, die das Einschalten von Zwei-Faktor ausgibt.

Echte Datei-Datenbank (`file_db`): eine Session je Anfrage, Commit am Ende, Rollback bei einem Fehler."""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pyotp
import pytest
from nodvard_deck.core import security
from nodvard_deck.models import AuditEntry, RecoveryCode, User
from nodvard_deck.services import auth as auth_service
from sqlalchemy import select
from totp_helpers import setup_confirm_code

PASSWORD = "correct-horse-battery"


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _owner_in_setup(client) -> tuple[str, str]:
    """Owner mit gestarteter, noch nicht bestaetigter Zwei-Faktor-Einrichtung: (Token, Schluessel)."""
    r = await client.post(
        "/api/v1/auth/bootstrap", json={"username": "nico", "password": PASSWORD, "setup_code": "TEST-CODE-2345"}
    )
    assert r.status_code == 201, r.text
    token = (await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})).json()["access_token"]
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=_h(token))).json()[
        "secret"
    ]
    return token, secret


async def _confirm_setup(client, token: str, secret: str):
    # Ohne feste Uhr: die echte Uhr des Servers. Die Hilfe wartet dann eine nahe Schrittgrenze ab, sonst waere der Code
    # beim Server schon zwei Schritte alt.
    return await client.post("/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret)}, headers=_h(token))


async def _owner_with_codes(client) -> tuple[str, list[str]]:
    token, secret = await _owner_in_setup(client)
    r = await _confirm_setup(client, token, secret)
    assert r.status_code == 200, r.text
    return token, r.json()["recovery_codes"]


async def _disable(client, token, code: str):
    return await client.request(
        "DELETE", "/api/v1/me/totp", json={"current_password": PASSWORD, "totp_code": code}, headers=_h(token)
    )


async def _renew(client, token, code: str):
    return await client.post(
        "/api/v1/me/recovery-codes", json={"current_password": PASSWORD, "totp_code": code}, headers=_h(token)
    )


async def _codes(file_db) -> list[RecoveryCode]:
    async with file_db() as session:
        return list((await session.execute(select(RecoveryCode))).scalars().all())


async def _two_factor_on(file_db) -> bool:
    async with file_db() as session:
        user = (await session.execute(select(User).where(User.username == "nico"))).scalar_one()
        return auth_service.two_factor_enabled(user)


async def _actions(file_db, action: str) -> list[AuditEntry]:
    async with file_db() as session:
        return list((await session.execute(select(AuditEntry).where(AuditEntry.action == action))).scalars().all())


def _watch_write_lock_while_hashing(monkeypatch, tmp_path) -> list[str]:
    """Bei jedem Argon2-Hash versucht eine zweite Verbindung, eine Schreibtransaktion zu beginnen. Die Liste sammelt je
    Hash "frei" oder den Fehler ("database is locked")."""
    db_file = tmp_path / "lattice-file.db"
    seen: list[str] = []
    real = security.hash_password

    def _hash_and_look(password: str) -> str:
        other = sqlite3.connect(db_file, timeout=0.2)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("ROLLBACK")
            seen.append("frei")
        except sqlite3.OperationalError as exc:
            seen.append(str(exc))
        finally:
            other.close()
        return real(password)

    monkeypatch.setattr(security, "hash_password", _hash_and_look)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("way", ["abschalten", "codes_erneuern"])
async def test_a_recovery_code_is_free_again_when_the_action_fails_afterwards(client, file_db, monkeypatch, way):
    token, codes = await _owner_with_codes(client)

    async def _breaks(*_args, **_kwargs):
        raise RuntimeError("Platte voll")

    if way == "abschalten":
        monkeypatch.setattr(auth_service, "disable_totp", _breaks)
        r = await _disable(client, token, codes[0])
    else:
        monkeypatch.setattr(auth_service, "issue_recovery_codes", _breaks)
        r = await _renew(client, token, codes[0])
    assert r.status_code == 500, r.text

    # Nichts davon ist gespeichert: der Code ist unbenutzt, Zwei-Faktor an, keine Meldung ueber einen benutzten Code.
    stored = await _codes(file_db)
    assert len(stored) == len(codes) and all(c.used_at is None for c in stored)
    assert await _two_factor_on(file_db)
    assert await _actions(file_db, "mfa.recovery_used") == []

    monkeypatch.undo()
    if way == "abschalten":
        assert (await _disable(client, token, codes[0])).status_code == 204
        assert not await _two_factor_on(file_db)
    else:
        assert (await _renew(client, token, codes[0])).status_code == 200
    assert len(await _actions(file_db, "mfa.recovery_used")) == 1


@pytest.mark.asyncio
async def test_the_same_recovery_code_twice_at_the_same_time_only_one_wins(client, file_db, monkeypatch):
    """Beide Anfragen pruefen den Code zu Ende, bevor eine ihn verbraucht: Nur eine bekommt neue Codes."""
    token, codes = await _owner_with_codes(client)
    real = security.run_hashing
    checked = 0
    both_checked = asyncio.Event()

    async def _together(func, *args, **kwargs):
        nonlocal checked
        result = await real(func, *args, **kwargs)
        if getattr(func, "__name__", "") == "_find":
            checked += 1
            if checked == 2:
                both_checked.set()
            async with asyncio.timeout(10):
                await both_checked.wait()
        return result

    monkeypatch.setattr(security, "run_hashing", _together)
    first, second = await asyncio.gather(_renew(client, token, codes[0]), _renew(client, token, codes[0]))

    assert checked == 2
    assert sorted([first.status_code, second.status_code]) == [200, 400], (first.text, second.text)
    winner, loser = (first, second) if first.status_code == 200 else (second, first)
    assert loser.json()["detail"] == auth_service.WRONG_RECOVERY_MESSAGE
    # Genau ein neuer Satz, ganz unbenutzt; ein Eintrag fuer den benutzten Code.
    stored = await _codes(file_db)
    assert len(stored) == len(codes) and all(c.used_at is None for c in stored)
    assert len(winner.json()["recovery_codes"]) == len(codes)
    assert len(await _actions(file_db, "mfa.recovery_used")) == 1
    (generated,) = [e for e in await _actions(file_db, "auth.recovery_codes_generated") if e.detail.get("reason") == "renewed"]
    assert generated.detail["via"] == "recovery_code"


@pytest.mark.asyncio
async def test_new_codes_are_hashed_before_the_recovery_code_is_used_up(client, file_db, monkeypatch, tmp_path):
    """Neue Codes mit einem Wiederherstellungs-Code bestaetigt: Ab dessen Verbrauch haelt die Anfrage die Schreibsperre
    der Datenbank bis zum Ende. Die zehn Argon2-Hashes (auf einem Pi Sekunden) laufen darum vorher; sonst wartet jeder
    andere Schreibzugriff so lange (oder scheitert mit "database is locked")."""
    token, codes = await _owner_with_codes(client)
    seen = _watch_write_lock_while_hashing(monkeypatch, tmp_path)
    r = await _renew(client, token, codes[0])
    assert r.status_code == 200, r.text
    assert seen == ["frei"] * len(codes)
    # Gespeichert sind die Codes, die vorher gerechnet wurden.
    monkeypatch.undo()
    new = r.json()["recovery_codes"]
    assert (await _renew(client, token, new[0])).status_code == 200


@pytest.mark.asyncio
async def test_first_codes_are_hashed_before_two_factor_is_switched_on(client, file_db, monkeypatch, tmp_path):
    """Das Einschalten (`POST /me/totp/confirm`) schreibt den Zeitpunkt der Bestaetigung und haelt ab da die
    Schreibsperre bis zum Ende der Anfrage. Die zehn Hashes der ersten Codes laufen darum vorher."""
    token, secret = await _owner_in_setup(client)
    seen = _watch_write_lock_while_hashing(monkeypatch, tmp_path)
    r = await _confirm_setup(client, token, secret)
    assert r.status_code == 200, r.text
    first = r.json()["recovery_codes"]
    assert seen == ["frei"] * len(first)
    assert await _two_factor_on(file_db)
    # Gespeichert sind genau die Codes, die vorher gerechnet und ausgegeben wurden.
    monkeypatch.undo()
    assert len(await _codes(file_db)) == len(first)
    assert (await _renew(client, token, first[0])).status_code == 200


@pytest.mark.asyncio
async def test_a_wrong_setup_code_computes_no_new_codes(client, file_db, monkeypatch, tmp_path):
    """Die Hashes laufen erst nach der Pruefung des Codes: Ein falscher Code kostet keine zehn Argon2-Rechnungen."""
    token, secret = await _owner_in_setup(client)
    valid = {pyotp.TOTP(secret).at(time.time() + shift) for shift in (-60, -30, 0, 30, 60)}
    wrong = next(c for c in ("000000", "111111", "222222", "333333", "444444", "555555") if c not in valid)
    seen = _watch_write_lock_while_hashing(monkeypatch, tmp_path)
    r = await client.post("/api/v1/me/totp/confirm", json={"code": wrong}, headers=_h(token))
    assert r.status_code == 400, r.text
    assert seen == []
    assert not await _two_factor_on(file_db)
