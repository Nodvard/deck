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
    entries = r.json()
    assert len(entries) == 1
    # Der eingetippte Name steht nirgends im Protokoll (es koennte ein Passwort sein).
    assert "existiert-nicht" not in r.text
    assert entries[0]["actor_type"] == "anonymous"
    assert entries[0]["actor_id"] == "unbekannt"
    assert entries[0]["detail"]["username_length"] == len("existiert-nicht")
    assert len(entries[0]["detail"]["username_ref"]) == 12


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


TYPED = "Geheim-Sommer2026!"


async def _create_user_with_role(db_session, *, username, password, role):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password(password), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.flush()
    return user


async def _login(client, username, password) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.mark.asyncio
async def test_password_typed_as_username_is_nowhere_in_audit(client, db_session):

    owner = await _bootstrap_owner(client)
    await _create_user_with_role(db_session, username="gast", password="whatever123", role="viewer")
    for _ in range(12):  # genug fuer eine Sperre: auch `login.locked` darf nichts verraten
        await client.post("/api/v1/auth/login", json={"username": TYPED, "password": "x"})
    viewer = await _login(client, "gast", "whatever123")

    for token in (owner, viewer):
        listed = await client.get("/api/v1/audit", params={"limit": 1000}, headers=_auth_header(token))
        exported = await client.get("/api/v1/audit/export", headers=_auth_header(token))
        for body in (listed.text, exported.text):
            assert TYPED not in body
            assert TYPED.lower() not in body
    locked = (await client.get("/api/v1/audit", params={"action": "login.locked"}, headers=_auth_header(owner))).json()
    assert locked and all(e["actor_id"] == "unbekannt" for e in locked)
    assert all("username" not in e["detail"] and e["detail"]["username_length"] == len(TYPED) for e in locked)
    refs = {e["detail"]["username_ref"] for e in locked}
    failed = (await client.get("/api/v1/audit", params={"action": "login.failed"}, headers=_auth_header(owner))).json()
    # Gleiche Eingabe, gleiche Kennung: Haeufungen bleiben erkennbar.
    assert {e["detail"]["username_ref"] for e in failed} == refs and len(refs) == 1


@pytest.mark.asyncio
async def test_known_username_is_logged_as_account_id(client):
    owner = await _bootstrap_owner(client, username="nico")
    me = (await client.get("/api/v1/me", headers=_auth_header(owner))).json()
    for _ in range(11):
        await client.post("/api/v1/auth/login", json={"username": "Nico", "password": "FALSCH"})
    rows = (await client.get("/api/v1/audit", params={"limit": 1000}, headers=_auth_header(owner))).json()
    failed = [e for e in rows if e["action"] == "login.failed"]
    assert failed and all(e["actor_id"] == me["id"] and e["target_id"] == me["id"] for e in failed)
    assert all("username_ref" not in e["detail"] for e in failed)
    locked = [e for e in rows if e["action"] == "login.locked"]
    assert locked and all(e["actor_id"] == me["id"] for e in locked)


@pytest.mark.asyncio
async def test_nobody_sees_typed_names_of_old_entries_not_even_the_owner(client, db_session):
    """Eintraege aus der Zeit vor der Aenderung tragen den Text noch in der Datenbank, falls die
    Migration sie noch nicht bereinigt hat (sie tut es beim Start). Auch der Owner bekommt ihn nicht:
    es koennte das Passwort eines anderen Kontos sein, das jemand ins Namensfeld getippt hat."""
    from nodvard_deck.core import audit

    owner = await _bootstrap_owner(client)
    gast = await _create_user_with_role(db_session, username="gast", password="whatever123", role="viewer")
    await audit.write_entry(
        db_session, actor_type="user", actor_id="geheim-sommer2026!", action="login.failed",
        outcome="failure", reason="Unbekannter Benutzername.",
    )
    await audit.write_entry(
        db_session, actor_type="user", actor_id="geheim-sommer2026!", action="login.locked",
        outcome="failure", detail={"scopes": ["user"], "username": "geheim-sommer2026!"},
    )
    await audit.write_entry(
        db_session, actor_type="user", actor_id=gast.id, action="login.locked",
        outcome="failure", detail={"scopes": ["user"], "username": "gast"},
    )
    await db_session.commit()
    viewer = await _login(client, "gast", "whatever123")

    for token in (viewer, owner):
        for path in ("/api/v1/audit", "/api/v1/audit/export"):
            seen = (await client.get(path, params={"limit": 1000}, headers=_auth_header(token))).text
            assert "geheim-sommer2026" not in seen
            assert '"username"' not in seen
        rows = (await client.get("/api/v1/audit", params={"limit": 1000}, headers=_auth_header(token))).json()
        old = [e for e in rows if e["action"] in ("login.failed", "login.locked") and e["actor_id"] == "unbekannt"]
        assert len(old) == 2 and all(e["actor_type"] == "anonymous" for e in old)
        assert any(e["actor_id"] == gast.id for e in rows if e["action"] == "login.locked")


@pytest.mark.asyncio
async def test_migration_cleans_old_entries_so_the_owner_sees_nothing_in_the_data_itself(client, db_session):
    """Die Schwaerzung in der Antwort ist nur die zweite Sicherung: nach der Migration steht der Text
    gar nicht mehr in der Tabelle, auch nicht fuer jemanden mit Zugriff auf die Datenbankdatei."""
    import importlib.util
    from pathlib import Path

    from nodvard_deck.core import audit
    from nodvard_deck.models import AuditEntry
    from sqlalchemy import select

    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "f3a9c6d18e24_login_protokoll_namen.py"
    spec = importlib.util.spec_from_file_location("audit_names_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    owner = await _bootstrap_owner(client)
    await audit.write_entry(
        db_session, actor_type="user", actor_id="geheim-sommer2026!", action="login.failed",
        outcome="failure", reason="Unbekannter Benutzername.",
    )
    await audit.write_entry(
        db_session, actor_type="user", actor_id="geheim-sommer2026!", action="login.locked",
        outcome="failure", detail={"scopes": ["user"], "username": "geheim-sommer2026!"},
    )
    await db_session.commit()

    changed = await db_session.run_sync(lambda sync_session: migration.anonymize_typed_login_names(sync_session.connection()))
    await db_session.commit()
    db_session.expire_all()
    assert changed == 2

    stored = (await db_session.execute(select(AuditEntry))).scalars().all()
    assert not any("geheim-sommer2026" in f"{e.actor_id}{e.reason}{e.detail}" for e in stored)
    seen = (await client.get("/api/v1/audit", params={"limit": 1000}, headers=_auth_header(owner))).text
    assert "geheim-sommer2026" not in seen


@pytest.mark.asyncio
async def test_viewer_cannot_check_a_guess_against_the_reference(client, db_session):
    """Mit Kennung und Laenge liesse sich eine Vermutung pruefen: selbst als Namen eintippen und
    die Kennungen vergleichen. Wer nicht Owner ist, bekommt beides deshalb nicht."""
    owner = await _bootstrap_owner(client)
    await _create_user_with_role(db_session, username="gast", password="whatever123", role="viewer")
    await db_session.commit()
    await client.post("/api/v1/auth/login", json={"username": TYPED, "password": "x"})
    viewer = await _login(client, "gast", "whatever123")
    await client.post("/api/v1/auth/login", json={"username": TYPED, "password": "y"})  # die Vermutung

    for path in ("/api/v1/audit", "/api/v1/audit/export"):
        seen = (await client.get(path, params={"limit": 1000}, headers=_auth_header(viewer))).text
        assert "username_ref" not in seen and "username_length" not in seen
    rows = (await client.get("/api/v1/audit", params={"action": "login.failed"}, headers=_auth_header(viewer))).json()
    assert len(rows) == 2 and all(e["actor_id"] == "unbekannt" for e in rows)
    full = (await client.get("/api/v1/audit", params={"action": "login.failed"}, headers=_auth_header(owner))).json()
    assert len({e["detail"]["username_ref"] for e in full}) == 1


@pytest.mark.asyncio
async def test_admin_does_not_see_typed_names_of_old_entries(client, db_session):
    """Ein Admin darf vieles, aber nicht alles, was der Owner darf. Ein altes Protokoll mit dem
    Owner-Passwort im Namensfeld darf ihm den Weg dorthin nicht oeffnen."""
    from nodvard_deck.core import audit

    await _bootstrap_owner(client)
    await _create_user_with_role(db_session, username="chef", password="whatever123", role="admin")
    await audit.write_entry(
        db_session, actor_type="user", actor_id="geheim-sommer2026!", action="login.failed",
        outcome="failure", reason="Unbekannter Benutzername.",
    )
    await audit.write_entry(
        db_session, actor_type="user", actor_id="geheim-sommer2026!", action="login.locked",
        outcome="failure", detail={"scopes": ["user"], "username": "geheim-sommer2026!"},
    )
    await db_session.commit()
    admin = await _login(client, "chef", "whatever123")
    for path in ("/api/v1/audit", "/api/v1/audit/export"):
        seen = (await client.get(path, params={"limit": 1000}, headers=_auth_header(admin))).text
        assert "geheim-sommer2026" not in seen
