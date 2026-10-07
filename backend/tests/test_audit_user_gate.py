"""Konten anlegen, aendern, loeschen und die Einstellungen des Freigabe-Gates stehen im Protokoll.

Vorher hinterliess eine Admin-Sitzung, die ein Konto anlegte, dessen Passwort setzte, die Autonomie auf
"selbstaendig" stellte und das Konto wieder loeschte, keine einzige Zeile."""

from __future__ import annotations

import json

import pytest
from nodvard_deck.models import AuditEntry
from sqlalchemy import select

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "ganz-neues-Passwort-77"


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _owner(client) -> tuple[str, str]:
    await client.post("/api/v1/auth/bootstrap", json={"username": "chef", "password": PASSWORD, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "chef", "password": PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()["access_token"], login.json()["user"]["id"]


async def _role_ids(client, token) -> dict[str, str]:
    return {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=_h(token))).json()}


async def _entries(db_session, action: str) -> list[AuditEntry]:
    rows = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == action).order_by(AuditEntry.ts))).scalars().all()
    return list(rows)


def _whole_row(entry: AuditEntry) -> str:
    return json.dumps(
        [entry.actor_id, entry.target_id, entry.reason, entry.detail, entry.ip, entry.user_agent], default=str, ensure_ascii=False
    )


# --- Konten --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creating_a_user_is_audited_with_name_and_roles(client, db_session):
    token, owner_id = await _owner(client)
    roles = await _role_ids(client, token)
    created = await client.post(
        "/api/v1/users",
        json={"username": "hintertuer", "password": NEW_PASSWORD, "email": "geheim@example.org",
              "role_ids": [roles["admin"], roles["viewer"]]},
        headers=_h(token),
    )
    assert created.status_code == 201, created.text

    (entry,) = await _entries(db_session, "user.created")
    assert entry.outcome == "success"
    assert (entry.actor_type, entry.actor_id) == ("user", owner_id)
    assert (entry.target_type, entry.target_id) == ("user", created.json()["id"])
    assert entry.detail == {"username": "hintertuer", "roles": ["admin", "viewer"]}
    assert NEW_PASSWORD not in _whole_row(entry)
    assert "geheim@example.org" not in _whole_row(entry)


@pytest.mark.asyncio
async def test_a_refused_user_creation_leaves_no_audit_row(client, db_session):
    token, _ = await _owner(client)
    again = await client.post("/api/v1/users", json={"username": "chef", "password": NEW_PASSWORD}, headers=_h(token))
    assert again.status_code == 409
    assert await _entries(db_session, "user.created") == []


@pytest.mark.asyncio
async def test_updating_a_user_is_audited_with_the_changed_fields(client, db_session):
    token, owner_id = await _owner(client)
    roles = await _role_ids(client, token)
    user_id = (await client.post(
        "/api/v1/users", json={"username": "gast", "password": PASSWORD, "role_ids": [roles["viewer"]]}, headers=_h(token)
    )).json()["id"]

    r = await client.patch(
        f"/api/v1/users/{user_id}",
        json={"role_ids": [roles["admin"]], "is_active": False, "email": "neu@example.org", "display_name": "Neuer Name"},
        headers=_h(token),
    )
    assert r.status_code == 200, r.text

    (entry,) = await _entries(db_session, "user.updated")
    assert entry.outcome == "success"
    assert (entry.actor_type, entry.actor_id) == ("user", owner_id)
    assert (entry.target_type, entry.target_id) == ("user", user_id)
    assert entry.detail == {
        "username": "gast",
        "password_reset": False,
        "roles": {"old": ["viewer"], "new": ["admin"]},
        "is_active": {"old": True, "new": False},
        "email_changed": True,
        "display_name_changed": True,
    }
    # E-Mail-Adresse und Anzeigename stehen nie mit Wert im Protokoll.
    assert "neu@example.org" not in _whole_row(entry)
    assert "Neuer Name" not in _whole_row(entry)


@pytest.mark.asyncio
async def test_a_password_reset_is_audited_without_the_password(client, db_session):
    token, _ = await _owner(client)
    user_id = (await client.post("/api/v1/users", json={"username": "gast", "password": PASSWORD}, headers=_h(token))).json()["id"]

    r = await client.patch(f"/api/v1/users/{user_id}", json={"password": NEW_PASSWORD}, headers=_h(token))
    assert r.status_code == 200, r.text

    (entry,) = await _entries(db_session, "user.updated")
    assert entry.detail == {"username": "gast", "password_reset": True}
    assert NEW_PASSWORD not in _whole_row(entry)


@pytest.mark.asyncio
async def test_an_update_that_changes_nothing_writes_no_audit_row(client, db_session):
    token, _ = await _owner(client)
    roles = await _role_ids(client, token)
    user_id = (await client.post(
        "/api/v1/users", json={"username": "gast", "password": PASSWORD, "role_ids": [roles["viewer"]]}, headers=_h(token)
    )).json()["id"]

    r = await client.patch(f"/api/v1/users/{user_id}", json={"is_active": True, "role_ids": [roles["viewer"]]}, headers=_h(token))
    assert r.status_code == 200, r.text
    assert await _entries(db_session, "user.updated") == []


@pytest.mark.asyncio
async def test_a_failed_update_leaves_no_audit_row(client, db_session):
    token, _ = await _owner(client)
    user_id = (await client.post("/api/v1/users", json={"username": "gast", "password": PASSWORD}, headers=_h(token))).json()["id"]

    r = await client.patch(
        f"/api/v1/users/{user_id}", json={"is_active": False, "role_ids": ["gibt-es-nicht"]}, headers=_h(token)
    )
    assert r.status_code == 404
    assert await _entries(db_session, "user.updated") == []


@pytest.mark.asyncio
async def test_deleting_a_user_is_audited_with_the_name(client, db_session):
    token, owner_id = await _owner(client)
    roles = await _role_ids(client, token)
    user_id = (await client.post(
        "/api/v1/users", json={"username": "hintertuer", "password": PASSWORD, "role_ids": [roles["admin"]]}, headers=_h(token)
    )).json()["id"]

    r = await client.delete(f"/api/v1/users/{user_id}", headers=_h(token))
    assert r.status_code == 204, r.text

    (entry,) = await _entries(db_session, "user.deleted")
    assert entry.outcome == "success"
    assert (entry.actor_type, entry.actor_id) == ("user", owner_id)
    assert (entry.target_type, entry.target_id) == ("user", user_id)
    assert entry.detail == {"username": "hintertuer", "roles": ["admin"]}


@pytest.mark.asyncio
async def test_the_whole_backdoor_story_is_visible_in_the_audit_log(client):
    """Konto anlegen, Passwort setzen, Gate abschalten, Konto loeschen: alles steht im Protokoll."""
    token, _ = await _owner(client)
    roles = await _role_ids(client, token)
    user_id = (await client.post(
        "/api/v1/users", json={"username": "hintertuer", "password": PASSWORD, "role_ids": [roles["admin"]]}, headers=_h(token)
    )).json()["id"]
    await client.patch(f"/api/v1/users/{user_id}", json={"password": NEW_PASSWORD}, headers=_h(token))
    await client.put("/api/v1/settings/autonomy.mode", json={"value": "full"}, headers=_h(token))
    await client.put("/api/v1/settings/autonomy.max_risk", json={"value": "critical"}, headers=_h(token))
    await client.delete(f"/api/v1/users/{user_id}", headers=_h(token))

    actions = [e["action"] for e in (await client.get("/api/v1/audit?limit=100", headers=_h(token))).json()]
    for expected in ("user.created", "user.updated", "system.settings.changed", "user.deleted"):
        assert expected in actions, actions
    assert actions.count("system.settings.changed") == 2


# --- Einstellungen des Gates ---------------------------------------------------------------

_WINDOW = {"cron": "0 3 * * 0", "duration_minutes": 90, "host_ids": "all"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "old", "new"),
    [
        ("autonomy.mode", "propose", "full"),
        ("autonomy.max_risk", "low", "critical"),
        ("security.deny_patterns", [], ["kill\\s+-9\\s+1\\b"]),
        ("maintenance.windows", [], [_WINDOW]),
    ],
)
async def test_gate_settings_land_in_the_audit_log_with_old_and_new_value(client, key, old, new):
    token, owner_id = await _owner(client)
    r = await client.put(f"/api/v1/settings/{key}", json={"value": new}, headers=_h(token))
    assert r.status_code == 200, r.text

    entries = (await client.get("/api/v1/audit?action=system.settings.changed", headers=_h(token))).json()
    (entry,) = [e for e in entries if e["target_id"] == key]
    assert entry["outcome"] == "success"
    assert (entry["actor_type"], entry["actor_id"]) == ("user", owner_id)
    assert entry["target_type"] == "setting"
    assert entry["detail"] == {"key": key, "old": old, "new": new}


@pytest.mark.asyncio
async def test_a_second_change_records_the_previous_stored_value(client):
    token, _ = await _owner(client)
    await client.put("/api/v1/settings/autonomy.max_risk", json={"value": "high"}, headers=_h(token))
    await client.put("/api/v1/settings/autonomy.max_risk", json={"value": "low"}, headers=_h(token))
    entries = (await client.get("/api/v1/audit?action=system.settings.changed", headers=_h(token))).json()
    assert sorted((e["detail"]["old"], e["detail"]["new"]) for e in entries) == [("high", "low"), ("low", "high")]


@pytest.mark.asyncio
async def test_a_rejected_gate_setting_leaves_no_audit_row(client):
    token, _ = await _owner(client)
    r = await client.put("/api/v1/settings/autonomy.mode", json={"value": "alles-erlaubt"}, headers=_h(token))
    assert r.status_code == 422
    entries = (await client.get("/api/v1/audit?action=system.settings.changed", headers=_h(token))).json()
    assert entries == []


@pytest.mark.asyncio
async def test_a_very_long_pattern_list_is_logged_by_count_only(client):
    """Sperrmuster haben keine Obergrenze; eine einzelne Protokollzeile soll davon nicht beliebig wachsen."""
    token, _ = await _owner(client)
    patterns = [f"muster-{i:04d}-" + "x" * 40 for i in range(500)]
    r = await client.put("/api/v1/settings/security.deny_patterns", json={"value": patterns}, headers=_h(token))
    assert r.status_code == 200, r.text

    entries = (await client.get("/api/v1/audit?action=system.settings.changed", headers=_h(token))).json()
    (entry,) = [e for e in entries if e["target_id"] == "security.deny_patterns"]
    assert entry["detail"] == {"key": "security.deny_patterns", "old": [], "new": {"truncated": True, "count": 500}}
    # Gespeichert ist die ganze Liste, nur das Protokoll kuerzt.
    stored = {s["key"]: s["value"] for s in (await client.get("/api/v1/settings", headers=_h(token))).json()}
    assert stored["security.deny_patterns"] == patterns
