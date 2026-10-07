"""Sicherungen herunterladen verlangt, wenn Zwei-Faktor an ist, ausser dem Passwort den aktuellen Code aus der App.

Eine Sicherung enthaelt die Datenbank und die Schluessel des Tresors, also auch den Schluessel der Zwei-Faktor-Anmeldung.
Wer sie nur mit dem Passwort bekaeme (beim Download mit eigenem Einmal-Passwort sogar ohne das Sicherungspasswort),
koennte sich damit jederzeit gueltige Codes erzeugen und jede Code-Pflicht umgehen -- auch beim Abschalten."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from nodvard_deck.core import login_limit
from nodvard_deck.core.backup import crypto, store
from nodvard_deck.models import AuditEntry
from nodvard_deck.services import auth as auth_service
from nodvard_deck.services import backups as backups_service
from sqlalchemy import select
from totp_helpers import setup_confirm_code

OWNER_PW = "correct-horse-battery"
BACKUP_PW = "mein-sicherungs-passwort"
ONE_TIME_PW = "einmal-passwort-fuer-den-download"


@pytest.fixture
def env(client, file_db, test_settings, tmp_path, monkeypatch):
    """Wie in test_system_backups_api.py: Datei-DB, eigenes Sicherungsziel, schnelle KDF."""
    from nodvard_deck import config

    # Dieselbe Datei wie `file_db` (die Sicherung kopiert die Datenbank, auf die die Einstellungen zeigen).
    test_settings.database_url = file_db.kw["bind"].url.render_as_string(hide_password=False)
    monkeypatch.setattr(config, "_settings", test_settings)
    external = tmp_path / "extern"
    external.mkdir()
    monkeypatch.setattr(store, "EXTERNAL_ROOT", external)
    monkeypatch.setattr(crypto, "ARGON2_T", 1)
    monkeypatch.setattr(crypto, "ARGON2_M_KIB", 1024)
    monkeypatch.setattr(crypto, "SCRYPT_WRITE_LOG_N", 10)
    times = iter(datetime(2026, 10, 1, 3, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=i) for i in range(1000))

    async def _fake_now(session):
        return next(times)

    monkeypatch.setattr(backups_service, "_local_now", _fake_now)
    backups_service.reset_for_tests()
    yield {"settings": test_settings, "sessionmaker": file_db}
    backups_service.reset_for_tests()


@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(auth_service, "_totp_now", lambda: now[0])
    return now


async def _owner(client) -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": OWNER_PW, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": OWNER_PW})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _enable_totp(client, headers) -> tuple[str, list[str]]:
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": OWNER_PW}, headers=headers)).json()["secret"]
    r = await client.post("/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret, auth_service._totp_now())}, headers=headers)
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def _download(client, headers, **extra):
    body = {"current_password": OWNER_PW, "mode": "passwort", "password": ONE_TIME_PW, **extra}
    return client.post("/api/v1/system/backups/download", json=body, headers=headers)


async def _audit_text(sessionmaker) -> str:
    async with sessionmaker() as session:
        rows = (await session.execute(select(AuditEntry))).scalars().all()
    return json.dumps([[e.action, e.detail, e.reason] for e in rows], ensure_ascii=False)


@pytest.mark.asyncio
async def test_without_two_factor_the_password_is_enough_as_before(client, env):
    owner = await _owner(client)
    r = await _download(client, owner)
    assert r.status_code == 202, r.text


@pytest.mark.asyncio
async def test_a_fresh_download_needs_the_app_code(client, env, clock):
    owner = await _owner(client)
    secret, recovery = await _enable_totp(client, owner)

    missing = await _download(client, owner)
    assert missing.status_code == 403, missing.text
    assert missing.json()["code"] == "totp_missing" and "Zwei-Faktor-Code" in missing.json()["detail"]
    wrong = await _download(client, owner, totp_code="000000" if pyotp.TOTP(secret).at(clock[0]) != "000000" else "000001")
    assert wrong.status_code == 400 and wrong.json() == {"detail": auth_service.WRONG_TOTP_MESSAGE, "code": "totp_wrong"}
    # Wiederherstellungs-Codes gelten hier nicht (wie bei Update und Rueckweg): sie sind fuer den Notfall beim Anmelden.
    assert (await _download(client, owner, totp_code=recovery[0])).status_code == 400
    await backups_service.wait_for_tasks()
    downloads = store.default_dir(env["settings"].data_dir) / ".downloads"
    assert not downloads.exists() or list(downloads.iterdir()) == [], "ohne gueltigen Code entsteht keine Datei"

    code = pyotp.TOTP(secret).at(clock[0])
    ok = await _download(client, owner, totp_code=code)
    assert ok.status_code == 202, ok.text
    await backups_service.wait_for_tasks()
    again = await _download(client, owner, totp_code=code)
    assert again.status_code == 400
    assert again.json() == {"detail": auth_service.TOTP_CODE_USED_MESSAGE, "code": "totp_used"}
    text = await _audit_text(env["sessionmaker"])
    assert code not in text and recovery[0] not in text and recovery[0].replace("-", "") not in text


@pytest.mark.asyncio
async def test_a_stored_backup_needs_the_app_code_too(client, env, clock):
    owner = await _owner(client)
    r = await client.put("/api/v1/system/backups/key", json={"current_password": OWNER_PW, "password": BACKUP_PW}, headers=owner)
    assert r.status_code == 200, r.text
    assert (await client.post("/api/v1/system/backups/run", headers=owner)).status_code == 202
    await backups_service.wait_for_tasks()
    [item] = (await client.get("/api/v1/system/backups", headers=owner)).json()["backups"]
    secret, _recovery = await _enable_totp(client, owner)
    url = f"/api/v1/system/backups/{item['name']}/ticket"

    missing = await client.post(url, json={"current_password": OWNER_PW}, headers=owner)
    assert missing.status_code == 403 and missing.json()["code"] == "totp_missing"
    ok = await client.post(url, json={"current_password": OWNER_PW, "totp_code": pyotp.TOTP(secret).at(clock[0])}, headers=owner)
    assert ok.status_code == 200, ok.text
    assert (await client.get(ok.json()["url"])).status_code == 200


@pytest.mark.asyncio
async def test_wrong_codes_count_on_the_account_limit(client, env, clock):
    owner = await _owner(client)
    secret, _recovery = await _enable_totp(client, owner)
    valid = {pyotp.TOTP(secret).at(clock[0] + d) for d in (-30, 0, 30)}
    wrong = [c for c in (f"{i:06d}" for i in range(20)) if c not in valid][: login_limit.MAX_MFA_FAILURES_PER_ACCOUNT]
    for code in wrong:
        assert (await _download(client, owner, totp_code=code)).status_code == 400
    blocked = await _download(client, owner, totp_code=pyotp.TOTP(secret).at(clock[0]))
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers
    # Hier gelten keine Wiederherstellungs-Codes, also auch kein Hinweis darauf.
    assert auth_service.RECOVERY_STILL_WORKS_HINT not in blocked.json()["detail"]
