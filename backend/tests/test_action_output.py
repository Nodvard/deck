"""Ausgabe und Fehlertexte von Aktionen: nur mit `hosts.execute` sichtbar, im Protokoll
nur als Kurzfassung plus feste Gate-Texte (core/action_output.py, api/v1/actions.py,
api/v1/audit.py)."""

from __future__ import annotations

import json

import pytest
from nodvard_sdk import Actor, ActionRequest, ActionResult, ActorType, DryRunReport, Risk
from nodvard_sdk.capabilities import ActionExecutor
from sqlalchemy import select

from nodvard_deck.core import gate
from nodvard_deck.core.action_output import audit_result, hide_audit_detail, hide_result
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import Action, AuditEntry
from nodvard_deck.services import audit as audit_service
from nodvard_deck.services import settings as settings_service

SECRET = "root:GEHEIM:19000::"
API = "/api/v1"
PASSWORD = "whatever123"


@pytest.fixture(autouse=True)
def _reset_runtime():
    reset_extension_runtime()
    yield
    reset_extension_runtime()


async def _user_token(client, db_session, role: str) -> dict:
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=f"u-{role}", password_hash=security.hash_password(PASSWORD), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.commit()
    login = await client.post(f"{API}/auth/login", json={"username": user.username, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _owner_headers(client) -> dict:
    await client.post(
        f"{API}/auth/bootstrap",
        json={"username": "chef", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post(f"{API}/auth/login", json={"username": "chef", "password": "correct-horse-battery"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _seed(db_session) -> Action:
    action = Action(
        ext_id="terminal", action_type="shell.exec", payload={"command": "cat /etc/shadow"},
        risk="high", status="failed", proposed_by_type="user", proposed_by_id="x", reason="r",
        result={
            "success": False, "exit_code": 2, "output": SECRET, "error": f"stderr: {SECRET}",
            "duration_ms": 12, "detail": {"stdout": SECRET},
        },
    )
    db_session.add(action)
    # So sahen Protokolleintraege vor der Aenderung aus (volles Ergebnis).
    await audit_service.log(
        db_session, actor_type="system", actor_id="gate", action="action.executed", outcome="failure",
        target_type="action", target_id="a1",
        detail={"action_type": "shell.exec", "result": dict(action.result)},
    )
    await db_session.commit()
    return action


@pytest.mark.asyncio
async def test_viewer_gets_no_output_in_action_list_and_detail(client, db_session):
    action = await _seed(db_session)
    headers = await _user_token(client, db_session, "viewer")

    for url in (f"{API}/actions", f"{API}/actions/{action.id}"):
        r = await client.get(url, headers=headers)
        assert r.status_code == 200, r.text
        assert "GEHEIM" not in r.text
    row = (await client.get(f"{API}/actions/{action.id}", headers=headers)).json()
    assert row["output_hidden"] is True
    assert row["status"] == "failed"
    assert row["result"]["success"] is False and row["result"]["exit_code"] == 2 and row["result"]["duration_ms"] == 12
    # Felder bleiben vorhanden, nur leer: alte Aufrufer lesen result.output weiter ohne Absturz.
    assert row["result"]["output"] is None and row["result"]["error"] is None and row["result"]["detail"] == {}


@pytest.mark.asyncio
async def test_operator_admin_and_owner_still_see_the_output(client, db_session):
    """Owner umgeht die Rechtepruefung; Admin geht ueber `*`, Operator ueber `hosts.execute`."""
    action = await _seed(db_session)
    owner = await _owner_headers(client)
    operator = await _user_token(client, db_session, "operator")
    admin = await _user_token(client, db_session, "admin")

    for headers in (operator, admin, owner):
        row = (await client.get(f"{API}/actions/{action.id}", headers=headers)).json()
        assert row["result"]["output"] == SECRET
        assert row["result"]["error"] == f"stderr: {SECRET}"
        assert row["output_hidden"] is False


@pytest.mark.asyncio
async def test_viewer_sees_status_and_participants_but_not_output(client, db_session):
    action = await _seed(db_session)
    headers = await _user_token(client, db_session, "viewer")
    row = (await client.get(f"{API}/actions/{action.id}", headers=headers)).json()
    assert row["action_type"] == "shell.exec" and row["risk"] == "high"
    assert row["proposed_by_type"] == "user" and "approved_by_label" in row


@pytest.mark.asyncio
async def test_action_without_output_is_not_marked_hidden(client, db_session):
    db_session.add(Action(
        ext_id="terminal", action_type="shell.exec", payload={}, risk="low", status="proposed",
        proposed_by_type="ai", proposed_by_id="m", reason="r",
    ))
    await db_session.commit()
    headers = await _user_token(client, db_session, "viewer")
    rows = (await client.get(f"{API}/actions", headers=headers)).json()
    assert rows[0]["output_hidden"] is False


@pytest.mark.asyncio
async def test_viewer_gets_no_output_in_old_audit_entries(client, db_session):
    await _seed(db_session)
    owner = await _owner_headers(client)
    admin = await _user_token(client, db_session, "admin")
    viewer = await _user_token(client, db_session, "viewer")

    r = await client.get(f"{API}/audit", headers=viewer)
    assert r.status_code == 200
    assert "GEHEIM" not in r.text
    entry = next(e for e in r.json() if e["action"] == "action.executed")
    assert entry["output_hidden"] is True
    assert entry["detail"]["action_type"] == "shell.exec"
    # Die Felder bleiben vorhanden, nur leer -- wie bei der Aktion selbst (die App liest sie so).
    assert entry["detail"]["result"] == {
        "success": False, "exit_code": 2, "duration_ms": 12, "output": None, "error": None, "detail": {},
    }
    exported = (await client.get(f"{API}/audit/export", headers=viewer)).text
    assert "GEHEIM" not in exported
    exported_entry = next(r for r in map(json.loads, exported.splitlines()) if r["action"] == "action.executed")
    assert exported_entry["detail"]["result"]["output"] is None and exported_entry["detail"]["result"]["detail"] == {}

    for headers in (owner, admin):
        full = next(e for e in (await client.get(f"{API}/audit", headers=headers)).json() if e["action"] == "action.executed")
        assert full["detail"]["result"]["output"] == SECRET and full["output_hidden"] is False
        assert SECRET in (await client.get(f"{API}/audit/export", headers=headers)).text


@pytest.mark.asyncio
async def test_viewer_audit_hides_output_fields_of_other_entries(client, db_session):
    await audit_service.log(
        db_session, actor_type="extension", actor_id="x", action="x.did_something", outcome="success",
        detail={"stdout": SECRET, "nested": [{"output": SECRET, "ok": 1}], "host": "h"},
    )
    await db_session.commit()
    viewer = await _user_token(client, db_session, "viewer")
    entry = next(e for e in (await client.get(f"{API}/audit", headers=viewer)).json() if e["action"] == "x.did_something")
    # Die Felder bleiben, nur leer.
    assert entry["detail"] == {"stdout": None, "nested": [{"output": None, "ok": 1}], "host": "h"}
    assert entry["output_hidden"] is True


@pytest.mark.asyncio
async def test_viewer_audit_hides_blocked_commands(client, db_session):
    """Ein von der Sperrliste abgefangener Befehl steht im Protokoll (`exec.denied`); er kann wie
    der Befehl einer Aktion Zugangsdaten enthalten und fehlt deshalb ohne `hosts.execute`."""
    await audit_service.log(
        db_session, actor_type="extension", actor_id="scripts", action="exec.denied", outcome="denied",
        reason="Sperrmuster getroffen", detail={"command": f"echo {SECRET}; rm -rf /", "rule": "deny_pattern:x"},
    )
    await db_session.commit()
    owner = await _owner_headers(client)
    viewer = await _user_token(client, db_session, "viewer")

    r = await client.get(f"{API}/audit", headers=viewer)
    assert SECRET not in r.text
    entry = next(e for e in r.json() if e["action"] == "exec.denied")
    assert entry["detail"] == {"rule": "deny_pattern:x", "command": None} and entry["output_hidden"] is True
    assert SECRET not in (await client.get(f"{API}/audit/export", headers=viewer)).text
    full = next(e for e in (await client.get(f"{API}/audit", headers=owner)).json() if e["action"] == "exec.denied")
    assert SECRET in full["detail"]["command"]


class _Executor:
    action_types = frozenset({"shell.exec"})

    async def execute(self, req: ActionRequest) -> ActionResult:
        return ActionResult(success=True, exit_code=0, output=SECRET, duration_ms=5, detail={"stdout": SECRET})

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        return None


@pytest.mark.asyncio
async def test_new_audit_entries_carry_no_output(db_session):
    get_extension_runtime().capabilities.provide("terminal", ActionExecutor, _Executor())
    await settings_service.set_global(db_session, "autonomy.mode", "full")
    await settings_service.set_global(db_session, "autonomy.max_risk", "high")
    decision = await gate.propose(
        db_session,
        ext_id="terminal",
        request=ActionRequest(
            action_type="shell.exec", payload={"command": "cat /etc/shadow"}, host_ref="h", risk=Risk.LOW,
            proposed_by=Actor(type=ActorType.AI, id="m"), reason="r",
        ),
        command_field=None,
    )
    assert decision.status.value == "succeeded"

    # Die Aktion selbst hat die Ausgabe weiter ...
    row = await db_session.get(Action, decision.action_id)
    assert row.result["output"] == SECRET
    # ... das Protokoll nur Erfolg, Exitcode, Dauer und Laenge.
    entry = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "action.executed"))).scalars().one()
    assert SECRET not in json.dumps(entry.detail)
    assert entry.detail["result"] == {"success": True, "exit_code": 0, "duration_ms": 5, "output_length": len(SECRET)}


def test_helpers_keep_only_safe_fields():
    assert audit_result({"success": True, "output": "abc", "error": None, "detail": {"a": 1}}) == {
        "success": True, "output_length": 3,
    }
    # Der feste Gate-Text steht im Protokoll; ist er der Fehlertext selbst, braucht es keine Laenge.
    assert audit_result({"success": False, "error": "Zeitüberschreitung"}, gate_error="Zeitüberschreitung") == {
        "success": False, "gate_error": "Zeitüberschreitung",
    }
    assert audit_result({"success": False, "error": f"boom {SECRET}"}, gate_error="Fehler (RuntimeError)") == {
        "success": False, "error_length": len(f"boom {SECRET}"), "gate_error": "Fehler (RuntimeError)",
    }
    assert hide_audit_detail(
        "action.executed", {"result": {"success": False, "gate_error": "abgebrochen", "error_length": 4}}
    ) == ({"result": {
        "success": False, "gate_error": "abgebrochen", "error_length": 4, "output": None, "error": None, "detail": {},
    }}, False)
    assert hide_result({"success": True, "output": "", "error": None, "detail": {}}) == (
        {"success": True, "output": None, "error": None, "detail": {}}, False,
    )
    assert hide_result({"success": False, "error": "x"}) == ({"success": False, "error": None}, True)
    assert hide_audit_detail("auth.login", {"a": 1}) == ({"a": 1}, False)


def _gate_request() -> ActionRequest:
    return ActionRequest(
        action_type="shell.exec", payload={"command": "uptime"}, host_ref="h", risk=Risk.LOW,
        proposed_by=Actor(type=ActorType.AI, id="m"), reason="r",
    )


async def _executed_audit(db_session, action_id: str) -> AuditEntry:
    return (
        await db_session.execute(
            select(AuditEntry).where(AuditEntry.action == "action.executed", AuditEntry.target_id == action_id)
        )
    ).scalars().one()


class _Raising:
    action_types = frozenset({"shell.exec"})

    async def execute(self, req: ActionRequest) -> ActionResult:
        raise RuntimeError(f"stderr: {SECRET}")

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        return None


@pytest.mark.asyncio
async def test_gate_reasons_stay_in_the_audit_but_not_text_from_the_server(client, db_session):
    """Ohne Executor und bei einer Ausnahme im Executor: das Protokoll nennt die Ursache mit einem
    festen Text. Der Text der Ausnahme (kann Inhalte vom Server tragen) steht nur in der Aktion."""
    # Kein Executor fuer den Typ
    first = await gate.propose(db_session, ext_id="terminal", request=_gate_request(), command_field=None)
    missing, _ = await gate.approve(db_session, first.action_id, user_id="u1")
    assert missing.status == "failed"
    no_executor = (await _executed_audit(db_session, first.action_id)).detail["result"]
    assert no_executor == {"success": False, "gate_error": "Kein ActionExecutor für 'shell.exec'."}

    # Der Executor wirft mit Inhalt vom Server im Text
    get_extension_runtime().capabilities.provide("terminal", ActionExecutor, _Raising())
    second = await gate.propose(db_session, ext_id="terminal", request=_gate_request(), command_field=None)
    raised, _ = await gate.approve(db_session, second.action_id, user_id="u1")
    assert SECRET in raised.result["error"], "die Aktion behaelt den vollen Text"
    in_audit = (await _executed_audit(db_session, second.action_id)).detail["result"]
    assert SECRET not in json.dumps(in_audit)
    assert in_audit["gate_error"] == (
        "Die Ausführung ist mit einem Fehler beendet worden (RuntimeError). Einzelheiten stehen in der Aktion."
    )

    # Alle Rollen sehen den festen Grund, niemand den Text vom Server.
    owner = await _owner_headers(client)
    viewer = await _user_token(client, db_session, "viewer")
    for headers in (owner, viewer):
        rows = {e["target_id"]: e for e in (await client.get(f"{API}/audit", headers=headers)).json() if e["action"] == "action.executed"}
        assert rows[first.action_id]["detail"]["result"]["gate_error"] == "Kein ActionExecutor für 'shell.exec'."
        assert rows[second.action_id]["detail"]["result"]["gate_error"] == in_audit["gate_error"]
        assert SECRET not in json.dumps(rows)
    seen = {e["target_id"]: e for e in (await client.get(f"{API}/audit", headers=viewer)).json() if e["action"] == "action.executed"}
    assert seen[second.action_id]["detail"]["result"]["error"] is None  # das Feld bleibt, leer


def test_show_output_must_be_given_explicitly():
    """Ein neuer Aufrufer ohne `show_output` soll scheitern, nicht allen die Ausgabe zeigen."""
    from nodvard_deck.api.v1.actions import ActionOut
    from nodvard_deck.api.v1.audit import AuditEntryOut

    with pytest.raises(TypeError):
        ActionOut.from_model(Action())  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        AuditEntryOut.from_model(AuditEntry())  # type: ignore[call-arg]
