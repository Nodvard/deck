"""PUT/POST/DELETE /api/v1/auth/bootstrap/restore/... und POST /auth/bootstrap/restart:
Sicherung einspielen im Einrichtungs-Assistenten -- nur ohne Konto, nur mit Einrichtungscode."""

from __future__ import annotations

import json
import logging

import pytest
from nodvard_deck.core import restart as restart_service
from nodvard_deck.core.backup import restore
from restore_api_fixtures import (  # noqa: F401 - Fixtures
    OCTET,
    SETUP_CODE,
    Tripwire,
    backup_bytes,
    make_owner,
    rapi,
)
from restore_helpers import PASSWORD

BASE = "/api/v1/auth/bootstrap"


def code(value: str = SETUP_CODE, **extra) -> dict:
    return {"X-Setup-Code": value, **extra}


async def b_upload(client, data: bytes, **kw):
    return await client.put(f"{BASE}/restore/upload", content=data, headers={**OCTET, **code(**kw)})


async def b_inspect(client, tmp_path, secret=PASSWORD) -> tuple[str, dict]:
    r = await b_upload(client, backup_bytes(tmp_path))
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    i = await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": secret}, headers=code())
    assert i.status_code == 200, i.text
    return rid, i.json()


ENDPOINTS = [
    ("PUT", "/restore/upload", {"content": b"x", "headers": OCTET}),
    ("POST", f"/restore/{'a' * 32}/inspect", {"json": {"password": "x"}}),
    ("POST", f"/restore/{'a' * 32}/schedule", {"json": {}}),
    ("DELETE", "/restore/pending", {}),
    ("POST", "/restart", {}),
]


async def call(client, method, url, kw, **headers):
    kw = dict(kw)
    kw["headers"] = {**kw.get("headers", {}), **headers}
    return await client.request(method, BASE + url, **kw)


@pytest.mark.asyncio
async def test_every_call_needs_the_setup_code(client, rapi):
    for method, url, kw in ENDPOINTS:
        r = await call(client, method, url, kw)
        assert r.status_code == 403, (url, r.text)
        assert "Einrichtungscode" in r.json()["detail"]
        r = await call(client, method, url, kw, **code("FALSCH-FALSCH-FALS"))
        assert r.status_code == 403 and "stimmt nicht" in r.json()["detail"], url


@pytest.mark.asyncio
async def test_code_is_checked_before_the_body_is_read(client, rapi):
    trip = Tripwire()
    for headers in (OCTET, {**OCTET, **code("FALSCH-FALSCH-FALS")}):
        trip.consumed = False
        r = await client.put(f"{BASE}/restore/upload", content=trip, headers=headers)
        assert r.status_code == 403 and trip.consumed is False
    assert not restore.list_ids(rapi.layout)


@pytest.mark.asyncio
async def test_wrong_codes_are_throttled_like_bootstrap_itself(client, rapi):
    codes = []
    for _ in range(12):
        codes.append((await client.put(f"{BASE}/restore/upload", content=b"x", headers={**OCTET, **code("FALSCH-FALSCH-FALS")})).status_code)
    assert codes[0] == 403 and codes[-1] == 429
    # Auch das richtige Passwort kommt dann nicht durch -- und dasselbe gilt fuer POST /auth/bootstrap (ein Zaehler).
    assert (await b_upload(client, b"x")).status_code == 429
    r = await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": SETUP_CODE})
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_with_an_existing_account_every_call_is_409(client, rapi, tmp_path):
    await make_owner(client)
    for method, url, kw in ENDPOINTS:
        for headers in ({}, code(), code("FALSCH-FALSCH-FALS")):
            r = await call(client, method, url, kw, **headers)
            assert r.status_code == 409, (method, url, r.status_code)
    assert not restore.list_ids(rapi.layout)
    status = await client.get(BASE)
    assert status.json() == {"needed": False}


@pytest.mark.asyncio
async def test_full_flow_through_the_wizard_endpoints(client, rapi, tmp_path, monkeypatch):
    rid, view = await b_inspect(client, tmp_path)
    assert view["summary"]["owner_name"] == "anna" and view["state"] == "ready"
    # Eine Zwischenstand-ID des Assistenten gehoert nicht zum Owner-Bereich (und umgekehrt) -- Bereiche sind getrennt.
    assert restore.read_meta(rapi.layout, rid)["scope"] == "bootstrap"
    s = await client.post(f"{BASE}/restore/{rid}/schedule", json={}, headers=code())
    assert s.status_code == 200, s.text
    pending = restore.read_pending(rapi.layout)
    assert pending["source"] == "bootstrap" and pending["actor"]["label"] == "Einrichtung" and pending["sign_out_all"] is True
    assert (await b_upload(client, backup_bytes(tmp_path, name="zwei"))).status_code == 409
    fired = []
    real = restart_service.request_restart
    monkeypatch.setattr(restart_service, "request_restart", lambda app, **kw: real(app, terminate=lambda: fired.append(1)))
    r = await client.post(f"{BASE}/restart", headers=code())
    assert r.status_code == 202 and r.json() == {"restarting": True, "exit_code": 75}
    import asyncio

    await asyncio.sleep(restart_service.SIGNAL_DELAY_S + 0.3)
    assert fired == [1]


@pytest.mark.asyncio
async def test_restart_needs_something_pending(client, rapi):
    r = await client.post(f"{BASE}/restart", headers=code())
    assert r.status_code == 409 and "vorgemerkt" in r.json()["detail"]


@pytest.mark.asyncio
async def test_cancel_discards_everything(client, rapi, tmp_path):
    rid, _ = await b_inspect(client, tmp_path)
    await client.post(f"{BASE}/restore/{rid}/schedule", json={}, headers=code())
    assert (await client.delete(f"{BASE}/restore/pending", headers=code())).status_code == 204
    assert not restore.pending_exists(rapi.layout) and restore.list_ids(rapi.layout) == []


@pytest.mark.asyncio
async def test_wizard_status_reports_a_failed_restore_only_without_accounts(client, rapi):
    restore.make_private_dir(rapi.layout.restore_dir)
    restore.write_result(rapi.layout, {"ok": False, "at": "2026-10-01T03:00:00Z", "message": "Migration kaputt", "source": "bootstrap",
                                       "actor": {"ip": "10.1.2.3"}, "backup": {}})
    body = (await client.get(BASE)).json()
    assert body == {"needed": True, "restore": {"ok": False, "message": "Migration kaputt", "at": "2026-10-01T03:00:00Z"}}
    # Ergebnisse anderer Herkunft oder erfolgreiche zeigt der Assistent nicht.
    restore.write_result(rapi.layout, {"ok": True, "at": "x", "message": "ok", "source": "bootstrap", "actor": {}, "backup": {}})
    assert (await client.get(BASE)).json() == {"needed": True}
    restore.write_result(rapi.layout, {"ok": False, "at": "x", "message": "x", "source": "owner", "actor": {}, "backup": {}})
    assert (await client.get(BASE)).json() == {"needed": True}


@pytest.mark.asyncio
async def test_wizard_status_hides_file_paths_in_the_error(client, rapi):
    restore.make_private_dir(rapi.layout.restore_dir)
    msg = "Einspielen gescheitert: [Errno 13] Permission denied: '/app/data/restore/staging-abc/db/lattice.db'"
    restore.write_result(rapi.layout, {"ok": False, "at": "x", "message": msg, "source": "bootstrap", "actor": {}, "backup": {}})
    shown = (await client.get(BASE)).json()["restore"]["message"]
    assert "/app/data" not in shown and "staging-abc" not in shown
    assert shown.startswith("Einspielen gescheitert: [Errno 13] Permission denied")


@pytest.mark.asyncio
async def test_setup_code_in_the_header_is_not_logged_or_audited(client, rapi, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    rid, _ = await b_inspect(client, tmp_path)
    await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": "geheimes-passwort-xyz"}, headers=code())
    await client.put(f"{BASE}/restore/upload", content=b"x", headers={**OCTET, **code("FALSCH-FALSCH-FALS")})
    logs = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (SETUP_CODE, "FALSCH-FALSCH-FALS", PASSWORD, "geheimes-passwort-xyz"):
        assert secret not in logs


@pytest.mark.asyncio
async def test_audit_of_the_wizard_steps_has_no_secrets(client, rapi, tmp_path, db_session):
    from nodvard_deck.models import AuditEntry
    from sqlalchemy import select

    rid, _ = await b_inspect(client, tmp_path)
    await client.post(f"{BASE}/restore/{rid}/schedule", json={}, headers=code())
    await client.put(f"{BASE}/restore/upload", content=b"x", headers={**OCTET, **code("FALSCH-FALSCH-FALS")})
    rows = (await db_session.execute(select(AuditEntry))).scalars().all()
    actions = {r.action for r in rows}
    assert {"system.restore.uploaded", "system.restore.inspected", "system.restore.scheduled", "setup.failed"} <= actions
    text = json.dumps([[r.action, r.actor_type, r.actor_id, r.reason, r.detail] for r in rows], default=str)
    for secret in (SETUP_CODE, "FALSCH-FALSCH-FALS", PASSWORD):
        assert secret not in text
    assert all(r.actor_type == "anonymous" for r in rows if r.action.startswith("system.restore"))
