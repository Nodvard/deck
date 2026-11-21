"""Protokoll ohne `hosts.execute`: Texte in `reason` und `detail` nur nach Erlaubnisliste."""

from __future__ import annotations

import json

import pytest

from nodvard_deck.core.action_output import (
    FIXED_REASON_PREFIXES,
    hide_audit_detail,
    hide_audit_reason,
)
from nodvard_deck.ext.context import CORE_AUDIT_PREFIXES
from nodvard_deck.services import audit as audit_service

SECRET = "root:GEHEIM:19000::"
API = "/api/v1"
PASSWORD = "whatever123"


async def _token(client, db_session, role: str) -> dict:
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


async def _entry(client, headers, action: str) -> dict:
    rows = (await client.get(f"{API}/audit", headers=headers)).json()
    return next(e for e in rows if e["action"] == action)


async def _exported(client, headers, action: str) -> dict:
    text = (await client.get(f"{API}/audit/export", headers=headers)).text
    return next(r for r in map(json.loads, text.splitlines()) if r["action"] == action)


@pytest.mark.asyncio
async def test_viewer_gets_no_server_text_in_reason(client, db_session):
    await audit_service.log(
        db_session, actor_type="extension", actor_id="shield", action="shield.scan", outcome="failure",
        reason=f"ssh: {SECRET}",
    )
    await db_session.commit()
    viewer = await _token(client, db_session, "viewer")
    operator = await _token(client, db_session, "operator")

    entry = await _entry(client, viewer, "shield.scan")
    assert entry["reason"] is None and entry["output_hidden"] is True
    assert SECRET not in (await client.get(f"{API}/audit/export", headers=viewer)).text
    assert (await _exported(client, viewer, "shield.scan"))["reason"] is None

    assert (await _entry(client, operator, "shield.scan"))["reason"] == f"ssh: {SECRET}"
    assert (await _exported(client, operator, "shield.scan"))["reason"] == f"ssh: {SECRET}"


@pytest.mark.asyncio
async def test_viewer_keeps_fixed_reasons_of_the_core(client, db_session):
    await audit_service.log(
        db_session, actor_type="user", actor_id="x", action="auth.password_changed", outcome="denied",
        reason="Falsches Passwort.",
    )
    await db_session.commit()
    viewer = await _token(client, db_session, "viewer")

    entry = await _entry(client, viewer, "auth.password_changed")
    assert entry["reason"] == "Falsches Passwort." and entry["output_hidden"] is False
    assert (await _exported(client, viewer, "auth.password_changed"))["reason"] == "Falsches Passwort."


@pytest.mark.asyncio
async def test_viewer_gets_no_server_text_in_unlisted_detail_keys(client, db_session):
    await audit_service.log(
        db_session, actor_type="extension", actor_id="x", action="x.failed", outcome="failure",
        detail={
            "error": SECRET, "message": f"sudo: {SECRET}", "nested": {"note": SECRET, "host_id": "h1"},
            "lines": [SECRET, 2], "host": {"error": SECRET}, "host_id": "h1", "count": 3, "ok": True,
        },
    )
    await db_session.commit()
    viewer = await _token(client, db_session, "viewer")
    operator = await _token(client, db_session, "operator")

    expected = {
        "error": None, "message": None, "nested": {"note": None, "host_id": "h1"},
        "lines": [None, 2], "host": {"error": None}, "host_id": "h1", "count": 3, "ok": True,
    }
    entry = await _entry(client, viewer, "x.failed")
    assert entry["detail"] == expected and entry["output_hidden"] is True
    assert (await _exported(client, viewer, "x.failed"))["detail"] == expected
    assert SECRET not in (await client.get(f"{API}/audit/export", headers=viewer)).text

    assert (await _entry(client, operator, "x.failed"))["detail"]["error"] == SECRET


def test_long_text_under_a_listed_key_is_hidden():
    assert hide_audit_detail("x.y", {"host_id": "h" * 200}) == ({"host_id": "h" * 200}, False)
    assert hide_audit_detail("x.y", {"host_id": "h" * 201}) == ({"host_id": None}, True)


def test_reason_without_entry_is_not_marked_hidden():
    assert hide_audit_reason("x.y", None) == (None, False)
    assert hide_audit_reason("x.y", "") == ("", False)
    assert hide_audit_reason("x.y", "Text") == (None, True)
    assert hide_audit_reason("login.failed", "Text") == ("Text", False)


def test_fixed_reasons_only_from_names_extensions_cannot_write():
    assert FIXED_REASON_PREFIXES
    assert all(prefix.startswith(CORE_AUDIT_PREFIXES) for prefix in FIXED_REASON_PREFIXES)


@pytest.mark.asyncio
async def test_viewer_keeps_fixed_codes_of_core_entries(client, db_session):
    await audit_service.log(
        db_session, actor_type="user", actor_id="x", action="host.setup_with_password", outcome="success",
        detail={"result": "ok", "reason": "renewed", "source": "bootstrap", "aktion": "password_change"},
    )
    await db_session.commit()
    viewer = await _token(client, db_session, "viewer")

    entry = await _entry(client, viewer, "host.setup_with_password")
    assert entry["detail"] == {"result": "ok", "reason": "renewed", "source": "bootstrap", "aktion": "password_change"}
    assert entry["output_hidden"] is False


def test_free_text_under_a_code_key_is_hidden():
    for text in ("Permission denied", f"sudo: {SECRET}", "x" * 65, "ok\n"):
        assert hide_audit_detail("x.y", {"reason": text, "result": [text]}) == ({"reason": None, "result": [None]}, True)


@pytest.mark.asyncio
async def test_viewer_sees_which_setting_changed_but_not_the_values(client, db_session):
    await audit_service.log(
        db_session, actor_type="user", actor_id="x", action="system.settings.changed", outcome="success",
        detail={"key": "autonomy.mode", "old": "manual", "new": f"x {SECRET}"},
    )
    await db_session.commit()
    viewer = await _token(client, db_session, "viewer")

    entry = await _entry(client, viewer, "system.settings.changed")
    assert entry["detail"] == {"key": "autonomy.mode", "old": None, "new": None}


def test_reasons_of_system_entries_stay_hidden():
    assert hide_audit_reason("system.restore.inspect_failed", f"Archiv: {SECRET}") == (None, True)


def test_task_status_keeps_only_fixed_states():
    for state in ("OK", "ok", "running", "failed", "timeout", "lost", "finished", "unfinished", "rejected", "given_up"):
        assert hide_audit_detail("x.y", {"task_status": state}) == ({"task_status": state}, False)
    for text in ("command 'vma extract' failed: exit code 133", "WARNINGS: 2", "unbekannter Ausgang", "OK "):
        assert hide_audit_detail("x.y", {"task_status": text}) == ({"task_status": None}, True)
    assert hide_audit_detail("x.y", {"task_status": None}) == ({"task_status": None}, False)
    assert hide_audit_detail("x.y", {"task_status": [SECRET]}) == ({"task_status": None}, True)


@pytest.mark.asyncio
async def test_viewer_keeps_the_title_only_of_a_deleted_notification(client, db_session):
    await audit_service.log(
        db_session, actor_type="user", actor_id="x", action="notifications.deleted", outcome="success",
        detail={"title": "Backup fertig"},
    )
    await audit_service.log(
        db_session, actor_type="extension", actor_id="x", action="x.notified", outcome="success",
        detail={"title": f"Server meldet {SECRET}"},
    )
    await db_session.commit()
    viewer = await _token(client, db_session, "viewer")

    assert (await _entry(client, viewer, "notifications.deleted"))["detail"] == {"title": "Backup fertig"}
    entry = await _entry(client, viewer, "x.notified")
    assert entry["detail"] == {"title": None} and entry["output_hidden"] is True
    assert (await _exported(client, viewer, "x.notified"))["detail"] == {"title": None}


def test_title_stays_hidden_when_nested_or_too_long():
    assert hide_audit_detail("notifications.deleted", {"x": {"title": "a"}}) == ({"x": {"title": None}}, True)
    assert hide_audit_detail("notifications.deleted", {"x": [{"title": "a"}]}) == ({"x": [{"title": None}]}, True)
    assert hide_audit_detail("notifications.deleted", {"title": "t" * 201}) == ({"title": None}, True)
