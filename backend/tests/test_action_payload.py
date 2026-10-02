"""Befehl und Skript-Inhalt einer Aktion (`payload`): vollstaendig nur fuer Nutzer, die sie
ausfuehren oder bestaetigen duerfen -- in der API (`/actions`) und im WebSocket-Kanal `events`
(core/action_output.py, api/v1/actions.py, core/events.py, core/ws_hub.py)."""

from __future__ import annotations

import json

import pytest
from nodvard_sdk import Event

from nodvard_deck.core.action_output import redact_action_event, visible_payload
from nodvard_deck.core.events import get_event_bus
from nodvard_deck.core.ws_hub import WsHub, reset_ws_hub
from nodvard_deck.models import Action

API = "/api/v1"
PASSWORD = "whatever123"
SECRET_COMMAND = "mysql -u root -pGEHEIM123 -e 'select 1'"


async def _user_headers(client, db_session, role: str, permissions: list[str] | None = None) -> dict:
    from nodvard_deck.core import security
    from nodvard_deck.models import Role, RolePermission, User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=f"u-{role}", password_hash=security.hash_password(PASSWORD), is_active=True)
    if permissions is not None:
        custom = Role(name=f"custom-{role}", description="Test")
        db_session.add(custom)
        await db_session.flush()
        for perm in permissions:
            db_session.add(RolePermission(role_id=custom.id, permission=perm))
        await db_session.flush()
        await db_session.refresh(custom, ["permissions"])
        user.roles.append(custom)
    else:
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


async def _seed(db_session, *, risk: str = "high") -> Action:
    action = Action(
        ext_id="scripts", action_type="script.run", host_id="h1",
        payload={
            "command": SECRET_COMMAND, "script_id": "s1", "params": {"pw": "GEHEIM123"},
            "secret_params": ["token"],
        },
        risk=risk, status="proposed", proposed_by_type="user", proposed_by_id="x", reason="r",
    )
    db_session.add(action)
    await db_session.commit()
    return action


@pytest.mark.asyncio
async def test_viewer_gets_no_command_in_list_and_detail(client, db_session):
    action = await _seed(db_session)
    headers = await _user_headers(client, db_session, "viewer")

    for url in (f"{API}/actions", f"{API}/actions?action_type=script.run", f"{API}/actions/{action.id}"):
        r = await client.get(url, headers=headers)
        assert r.status_code == 200, r.text
        assert "GEHEIM123" not in r.text
    row = (await client.get(f"{API}/actions/{action.id}", headers=headers)).json()
    # `payload` bleibt im Schema, nur mit harmlosen Kennungen.
    assert row["payload"] == {"script_id": "s1"}
    assert row["payload_hidden"] is True


@pytest.mark.asyncio
async def test_operator_and_owner_see_the_command(client, db_session):
    action = await _seed(db_session, risk="medium")
    owner = await _owner_headers(client)
    operator = await _user_headers(client, db_session, "operator")

    for headers in (operator, owner):
        row = (await client.get(f"{API}/actions/{action.id}", headers=headers)).json()
        assert row["payload"]["command"] == SECRET_COMMAND
        assert row["payload"]["params"] == {"pw": "GEHEIM123"}
        assert row["payload_hidden"] is False


@pytest.mark.asyncio
async def test_whoever_may_decide_sees_the_command(client, db_session):
    """Eine Freigabe ohne Kenntnis des Befehls waere keine: auch ohne `hosts.execute` sieht
    ihn, wer `actions.approve:<risiko>` hat."""
    action = await _seed(db_session, risk="high")
    approver = await _user_headers(client, db_session, "approver", ["hosts.read", "actions.approve:high"])
    other = await _user_headers(client, db_session, "reader", ["hosts.read", "actions.approve:low"])

    row = (await client.get(f"{API}/actions/{action.id}", headers=approver)).json()
    assert row["payload"]["command"] == SECRET_COMMAND
    row = (await client.get(f"{API}/actions/{action.id}", headers=other)).json()
    assert "command" not in row["payload"] and row["payload_hidden"] is True


@pytest.mark.asyncio
async def test_action_without_command_is_not_marked_hidden(client, db_session):
    db_session.add(Action(
        ext_id="proxmox", action_type="vm.start", host_id="h1", payload={"vmid": 100, "node": "pve1"},
        risk="low", status="proposed", proposed_by_type="user", proposed_by_id="x", reason="r",
    ))
    await db_session.commit()
    headers = await _user_headers(client, db_session, "viewer")
    row = (await client.get(f"{API}/actions", headers=headers)).json()[0]
    assert row["payload"] == {"vmid": 100, "node": "pve1"}
    assert row["payload_hidden"] is False


def test_visible_payload_is_an_allowlist():
    assert visible_payload({"command": "x", "vmid": 7, "changes": {"cipassword": "x"}, "unit": "nginx"}) == (
        {"vmid": 7, "unit": "nginx"}, True,
    )
    # Auch ein erlaubter Name zeigt nie Listen, Unterobjekte oder sehr lange Texte.
    assert visible_payload({"unit": ["a"], "node": "n" * 500}) == ({}, True)
    assert visible_payload({"command": ""}) == ({}, False)
    assert visible_payload(None) == ({}, False)


def test_redact_action_event_keeps_everything_but_the_command():
    data = {"action_id": "a1", "action_type": "shell.exec", "outcome": "success", "payload": {"command": "x", "host_id": "h"}}
    assert redact_action_event(data) == {
        "action_id": "a1", "action_type": "shell.exec", "outcome": "success",
        "payload": {"host_id": "h"}, "payload_hidden": True,
    }
    assert redact_action_event({"name": "x"}) == {"name": "x"}


class _Socket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_hub_sends_the_reduced_payload_without_full_permission():
    hub = WsHub()
    full, reduced = _Socket(), _Socket()
    conn_full = hub.connect(full, user_id="a", permissions=["hosts.read", "hosts.execute"])
    conn_reduced = hub.connect(reduced, user_id="b", permissions=["hosts.read"])
    for conn in (conn_full, conn_reduced):
        hub.subscribe(conn, "events")

    await hub.publish(
        "events", {"d": "voll"}, required_permission="hosts.read",
        reduced_payload={"d": "gekuerzt"}, full_permission="hosts.execute",
    )
    assert full.sent[0]["payload"] == {"d": "voll"}
    assert reduced.sent[0]["payload"] == {"d": "gekuerzt"}


@pytest.mark.asyncio
async def test_event_bus_gives_viewers_no_command_but_handlers_and_admins_get_it():
    reset_ws_hub()
    from nodvard_deck.core.ws_hub import get_ws_hub

    hub = get_ws_hub()
    viewer, operator = _Socket(), _Socket()
    for sock, perms in ((viewer, ["hosts.read"]), (operator, ["hosts.read", "hosts.execute"])):
        hub.subscribe(hub.connect(sock, user_id="u", permissions=perms), "events")

    seen: list[dict] = []

    async def handler(event: Event) -> None:
        seen.append(event.payload)

    bus = get_event_bus()
    bus.subscribe("action.*", handler)
    payload = {"action_id": "a1", "action_type": "shell.exec", "host_id": "h1", "payload": {"command": SECRET_COMMAND}, "outcome": "success"}
    await bus.publish(Event(name="action.executed", payload=payload))

    # Handler im Prozess (z. B. die Skripte-Erweiterung) bekommen den vollen Payload.
    assert seen[0]["payload"]["command"] == SECRET_COMMAND
    assert SECRET_COMMAND not in json.dumps(viewer.sent)
    assert viewer.sent[0]["payload"]["data"]["payload_hidden"] is True
    assert viewer.sent[0]["payload"]["data"]["action_id"] == "a1"
    assert operator.sent[0]["payload"]["data"]["payload"]["command"] == SECRET_COMMAND
    bus.unsubscribe("action.*", handler)
    reset_ws_hub()


def test_show_payload_must_be_given_explicitly():
    from nodvard_deck.api.v1.actions import ActionOut

    with pytest.raises(TypeError):
        ActionOut.from_model(Action(), None, show_output=True)  # type: ignore[call-arg]
