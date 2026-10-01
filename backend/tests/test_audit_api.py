"""GET /audit, GET /audit/export -- docs/04-API.md, docs/03-DATA-MODEL.md §4.

Die wichtigste Zusicherung hier: ein `login.failed`-Eintrag ueberlebt tatsaechlich in
der Datenbank, obwohl die Anfrage selbst mit 401 endet (siehe
`services.auth._commit_before_raising` fuer den Bug, den dieser Test als Regression
festhaelt: `session_scope()` rollt bei JEDER durchgereichten Exception zurueck --
auch bei einer erwarteten 401 -- und haette ohne Zwischen-Commit genau den
Audit-Eintrag mit geloescht, der eine gescheiterte Login-Attacke dokumentiert).
"""

from __future__ import annotations

import json

import pytest


async def _bootstrap_owner(client, username="nico", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(client):
    r = await client.get("/api/v1/audit")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_user_without_any_role_gets_403(client, db_session):
    from nodvard_deck.core import security
    from nodvard_deck.models import User

    user = User(username="norole", password_hash=security.hash_password("whatever123"), is_active=True)
    db_session.add(user)
    await db_session.flush()

    login = await client.post(
        "/api/v1/auth/login", json={"username": "norole", "password": "whatever123"}
    )
    token = login.json()["access_token"]

    r = await client.get("/api/v1/audit", headers=_auth_header(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_failed_login_produces_audit_entry_despite_401_response(client):
    """Regression fuer den Rollback-Bug -- siehe Modul-Docstring."""
    token = await _bootstrap_owner(client)

    failed = await client.post(
        "/api/v1/auth/login", json={"username": "nico", "password": "FALSCH"}
    )
    assert failed.status_code == 401

    r = await client.get(
        "/api/v1/audit", params={"action": "login.failed"}, headers=_auth_header(token)
    )
    assert r.status_code == 200
    entries = r.json()
    assert len(entries) == 1
    assert entries[0]["outcome"] == "failure"
    # actor_id ist die User-ID, nicht der Benutzername -- der Nutzer existiert ja
    # (falsches Passwort, nicht unbekannter Benutzername, siehe naechster Test).
    assert entries[0]["actor_id"]


@pytest.mark.asyncio
async def test_unknown_username_login_also_produces_audit_entry(client):
    token = await _bootstrap_owner(client)

    failed = await client.post(
        "/api/v1/auth/login", json={"username": "existiert-nicht", "password": "egal12345"}
    )
    assert failed.status_code == 401

    r = await client.get(
        "/api/v1/audit", params={"action": "login.failed"}, headers=_auth_header(token)
    )
    actor_ids = [e["actor_id"] for e in r.json()]
    assert "existiert-nicht" in actor_ids


@pytest.mark.asyncio
async def test_bootstrap_and_login_produce_login_succeeded_entry(client):
    token = await _bootstrap_owner(client)

    r = await client.get(
        "/api/v1/audit", params={"action": "login.succeeded"}, headers=_auth_header(token)
    )
    entries = r.json()
    assert len(entries) == 1
    assert entries[0]["outcome"] == "success"


@pytest.mark.asyncio
async def test_logout_produces_audit_entry(client):
    token = await _bootstrap_owner(client)

    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "nico", "password": "correct-horse-battery", "client_type": "android"},
    )
    refresh_token = login.json()["refresh_token"]

    logout = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})
    assert logout.status_code == 204

    r = await client.get(
        "/api/v1/audit", params={"action": "logout.succeeded"}, headers=_auth_header(token)
    )
    assert len(r.json()) == 1


@pytest.mark.asyncio
async def test_export_returns_ndjson_with_one_valid_json_line_per_entry(client):
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/auth/login", json={"username": "nico", "password": "FALSCH"})

    listed = await client.get("/api/v1/audit", headers=_auth_header(token))
    exported = await client.get("/api/v1/audit/export", headers=_auth_header(token))

    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/x-ndjson")

    lines = [line for line in exported.text.splitlines() if line]
    assert len(lines) == len(listed.json())
    for line in lines:
        json.loads(line)
