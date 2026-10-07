"""Eine Sicherung zum Einspielen vormerken verlangt, wenn Zwei-Faktor an ist, ausser dem Passwort den Code aus der App.

Eingespielt wird alles, auch die Konten. Eine selbst gebaute Sicherung (wie sie `restore_helpers.make_backup` hier fuer
die Tests baut) mit einem Inhaber ohne Zwei-Faktor waere sonst ein Weg, mit nur dem Passwort an jeder Code-Pflicht
vorbeizukommen."""

from __future__ import annotations

import json
import time

import pyotp
import pytest
from nodvard_deck.core.backup import restore
from nodvard_deck.services import auth as auth_service
from restore_api_fixtures import (  # noqa: F401 - Fixtures
    OCTET,
    OWNER_PW,
    backup_bytes,
    enc,
    make_owner,
    rapi,
)
from restore_helpers import PASSWORD
from totp_helpers import setup_confirm_code

BASE = "/api/v1/system"


@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(auth_service, "_totp_now", lambda: now[0])
    return now


async def _enable_totp(client, owner) -> tuple[str, list[str]]:
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": OWNER_PW}, headers=owner)).json()["secret"]
    r = await client.post("/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret, auth_service._totp_now())}, headers=owner)
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


async def _inspected(client, owner, tmp_path) -> str:
    headers = {**owner, **OCTET, "X-Confirm-Password": enc(OWNER_PW)}
    r = await client.put(f"{BASE}/restore/upload", content=backup_bytes(tmp_path), headers=headers)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    r = await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": PASSWORD}, headers=owner)
    assert r.status_code == 200, r.text
    return rid


@pytest.mark.asyncio
async def test_scheduling_a_restore_needs_the_app_code(client, rapi, tmp_path, clock):  # noqa: F811 - Fixture
    owner = await make_owner(client)
    secret, recovery = await _enable_totp(client, owner)
    rid = await _inspected(client, owner, tmp_path)
    url = f"{BASE}/restore/{rid}/schedule"

    missing = await client.post(url, json={"current_password": OWNER_PW}, headers=owner)
    assert missing.status_code == 403, missing.text
    assert missing.json()["code"] == "totp_missing" and "Zwei-Faktor-Code" in missing.json()["detail"]
    valid = {pyotp.TOTP(secret).at(clock[0] + d) for d in (-30, 0, 30)}
    wrong = next(c for c in ("000000", "000001", "000002", "000003") if c not in valid)
    r = await client.post(url, json={"current_password": OWNER_PW, "totp_code": wrong}, headers=owner)
    assert r.status_code == 400 and r.json()["detail"] == auth_service.WRONG_TOTP_MESSAGE
    r = await client.post(url, json={"current_password": OWNER_PW, "totp_code": recovery[0]}, headers=owner)
    assert r.status_code == 400, "Wiederherstellungs-Codes gelten hier nicht"
    assert restore.read_pending(rapi.layout) is None, "nichts vorgemerkt"
    # Erst das Passwort: falsch ist 400, ohne zu verraten, dass noch ein Code fehlt.
    r = await client.post(url, json={"current_password": "falsch-falsch"}, headers=owner)
    assert r.status_code == 400 and "code" not in r.json()

    code = pyotp.TOTP(secret).at(clock[0])
    ok = await client.post(url, json={"current_password": OWNER_PW, "totp_code": code}, headers=owner)
    assert ok.status_code == 200, ok.text
    assert restore.read_pending(rapi.layout)["id"] == rid
    audit = json.dumps((await client.get("/api/v1/audit?limit=200", headers=owner)).json())
    assert code not in audit and wrong not in audit and recovery[0] not in audit


@pytest.mark.asyncio
async def test_without_two_factor_the_password_is_enough_as_before(client, rapi, tmp_path):  # noqa: F811 - Fixture
    owner = await make_owner(client)
    rid = await _inspected(client, owner, tmp_path)
    r = await client.post(f"{BASE}/restore/{rid}/schedule", json={"current_password": OWNER_PW}, headers=owner)
    assert r.status_code == 200, r.text
