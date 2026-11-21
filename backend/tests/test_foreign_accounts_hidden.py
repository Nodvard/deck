"""Wer andere Konten nicht sehen darf, liest ihre Namen und Rollen weder im Protokoll noch in Meldungen."""

from __future__ import annotations

import pytest
from nodvard_deck.api.v1 import overview as overview_api

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "new-correct-horse-battery"


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _role_ids(client, owner: str) -> dict[str, str]:
    return {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=_h(owner))).json()}


async def _create(client, owner: str, username: str, role_id: str) -> str:
    r = await client.post(
        "/api/v1/users",
        json={"username": username, "password": PASSWORD, "role_ids": [role_id], "current_password": PASSWORD},
        headers=_h(owner),
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _reset_password(client, owner: str, user_id: str) -> None:
    r = await client.patch(
        f"/api/v1/users/{user_id}", json={"password": NEW_PASSWORD, "current_password": PASSWORD}, headers=_h(owner)
    )
    assert r.status_code == 200, r.text


@pytest.fixture
async def accounts(client):
    """Owner legt einen Admin und ein weiteres Konto an, setzt dessen Passwort und das des Betrachters neu."""
    overview_api.reset_cache()
    await client.post(
        "/api/v1/auth/bootstrap", json={"username": "inhaberin", "password": PASSWORD, "setup_code": "TEST-CODE-2345"}
    )
    owner = await _login(client, "inhaberin", PASSWORD)
    roles = await _role_ids(client, owner)
    gast_id = await _create(client, owner, "gast", roles["viewer"])
    await _create(client, owner, "fremdadmin", roles["admin"])
    other_id = await _create(client, owner, "fremdkonto", roles["operator"])
    await _reset_password(client, owner, other_id)
    await _reset_password(client, owner, gast_id)
    viewer = await _login(client, "gast", NEW_PASSWORD)
    yield {"owner": owner, "viewer": viewer, "gast_id": gast_id, "other_id": other_id}
    overview_api.reset_cache()


FOREIGN = ("fremdadmin", "fremdkonto")


async def _texts(client, token: str) -> dict[str, str]:
    notes = (await client.get("/api/v1/notifications", params={"limit": 1000}, headers=_h(token))).json()
    single = [(await client.get(f"/api/v1/notifications/{n['id']}", headers=_h(token))).text for n in notes]
    return {
        "audit": (await client.get("/api/v1/audit", params={"limit": 1000}, headers=_h(token))).text,
        "export": (await client.get("/api/v1/audit/export", headers=_h(token))).text,
        "notifications": (await client.get("/api/v1/notifications", headers=_h(token))).text + "".join(single),
        "unread": (await client.get("/api/v1/notifications", params={"unread": True}, headers=_h(token))).text,
        "overview": (await client.get("/api/v1/overview", headers=_h(token))).text,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["audit", "export", "notifications", "unread", "overview"])
async def test_viewer_reads_no_foreign_names(client, accounts, where):
    seen = (await _texts(client, accounts["viewer"]))[where]
    for name in FOREIGN + ("inhaberin",):
        assert name not in seen


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["audit", "export", "notifications", "overview"])
async def test_owner_still_reads_everything(client, accounts, where):
    seen = (await _texts(client, accounts["owner"]))[where]
    for name in FOREIGN:
        assert name in seen


@pytest.mark.asyncio
async def test_admin_still_reads_everything(client, accounts):
    admin = await _login(client, "fremdadmin", PASSWORD)
    seen = await _texts(client, admin)
    assert "fremdkonto" in seen["audit"] and "fremdkonto" in seen["export"]
    assert "fremdkonto" in seen["notifications"]


@pytest.mark.asyncio
async def test_viewer_keeps_own_account_events(client, accounts):
    viewer, gast_id = accounts["viewer"], accounts["gast_id"]
    rows = (await client.get("/api/v1/audit", params={"limit": 1000}, headers=_h(viewer))).json()
    created = [e for e in rows if e["action"] == "user.created" and e["target_id"] == gast_id]
    assert created and created[0]["detail"]["username"] == "gast" and created[0]["detail"]["roles"] == ["viewer"]
    notes = (await client.get("/api/v1/notifications", headers=_h(viewer))).json()
    assert [n["title"] for n in notes if n["payload"].get("notification_type") == "account.changed"] == [
        "Zugang von „gast“ geändert"
    ]


@pytest.mark.asyncio
async def test_viewer_sees_foreign_entries_without_names(client, accounts):
    rows = (await client.get("/api/v1/audit", params={"limit": 1000}, headers=_h(accounts["viewer"]))).json()
    foreign = [e for e in rows if e["action"].startswith("user.") and e["target_id"] == accounts["other_id"]]
    assert {e["action"] for e in foreign} == {"user.created", "user.updated"}
    assert all("username" not in e["detail"] and "roles" not in e["detail"] for e in foreign)
    updated = next(e for e in foreign if e["action"] == "user.updated")
    assert updated["detail"]["password_reset"] is True


@pytest.mark.asyncio
async def test_hidden_notifications_do_not_count_and_cannot_be_opened(client, accounts):
    owner, viewer = accounts["owner"], accounts["viewer"]
    owner_notes = (await client.get("/api/v1/notifications", headers=_h(owner))).json()
    foreign = [n for n in owner_notes if "fremd" in n["title"]]
    assert len(foreign) == 2
    for note in foreign:
        assert (await client.get(f"/api/v1/notifications/{note['id']}", headers=_h(viewer))).status_code == 404
        assert (await client.get(f"/api/v1/notifications/{note['id']}", headers=_h(owner))).status_code == 200
    own = (await client.get("/api/v1/notifications/unread-count", headers=_h(owner))).json()["unread"]
    seen = (await client.get("/api/v1/notifications/unread-count", headers=_h(viewer))).json()["unread"]
    assert seen == own - 2
    overview = (await client.get("/api/v1/overview", headers=_h(viewer))).json()
    assert overview["unread_notifications"] == seen
    assert [a["title"] for a in overview["attention"]] == ["Zugang von „gast“ geändert"]


@pytest.mark.asyncio
async def test_old_account_notice_without_account_is_hidden(client, accounts, db_session):
    from nodvard_deck.services import notifications as notifications_service

    await notifications_service.send(
        db_session, title="Zugang von „altkonto“ geändert", body="„inhaberin“ hat für „altkonto“ ein neues Passwort gesetzt.",
        severity="warning", payload={"notification_type": "account.changed", "path": "/settings/users"},
    )
    await db_session.commit()
    overview_api.reset_cache()
    assert "altkonto" not in "".join((await _texts(client, accounts["viewer"])).values())
    assert "altkonto" in (await _texts(client, accounts["owner"]))["notifications"]


@pytest.mark.asyncio
async def test_other_notifications_stay_visible(client, accounts, db_session):
    from nodvard_deck.services import notifications as notifications_service

    await notifications_service.send(db_session, title="Backup fertig", body="ok", payload={"notification_type": "backup"})
    await notifications_service.send(db_session, title="Ohne Art", body="ok")
    await db_session.commit()
    titles = [n["title"] for n in (await client.get("/api/v1/notifications", headers=_h(accounts["viewer"]))).json()]
    assert "Backup fertig" in titles and "Ohne Art" in titles


def _entry(action: str, target_id: str, detail: dict):
    from nodvard_deck.models import AuditEntry

    return AuditEntry(
        id=f"e-{action}-{target_id}", actor_type="user", actor_id="a1", action=action, outcome="success",
        target_type="user", target_id=target_id, detail=detail,
    )


def test_only_user_entries_lose_names_and_stored_entries_stay():
    from nodvard_deck.services.audit import hide_other_accounts

    foreign = _entry("user.deleted", "u2", {"username": "fremdkonto", "roles": ["viewer"], "x": 1})
    other = _entry("login.locked", "u2", {"username": "fremdkonto", "roles": ["viewer"]})
    own = _entry("user.updated", "u1", {"username": "gast", "roles": {"old": [], "new": ["viewer"]}})
    shown = hide_other_accounts([foreign, other, own], "u1")
    assert shown[0].detail == {"x": 1}
    assert shown[1] is other and shown[2] is own
    assert foreign.detail == {"username": "fremdkonto", "roles": ["viewer"], "x": 1}


OTHER_ACCOUNT_TYPES = ("account.password_attempts", "account.totp_attempts", "account.recovery_used")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", OTHER_ACCOUNT_TYPES)
async def test_other_account_notices_follow_the_same_rule(client, accounts, db_session, kind):
    from nodvard_deck.services import notifications as notifications_service

    for name, account in (("fremdkonto", accounts["other_id"]), ("gast", accounts["gast_id"]), ("altkonto", None)):
        payload = {"notification_type": kind, "path": "/settings/account"}
        if account is not None:
            payload["account_id"] = account
        await notifications_service.send(
            db_session, title=f"Achtung {name}", body=f"Für „{name}“ gab es etwas.", severity="warning", payload=payload
        )
    await db_session.commit()
    overview_api.reset_cache()
    seen = "".join((await _texts(client, accounts["viewer"])).values())
    assert "fremdkonto" not in seen and "altkonto" not in seen
    assert "Achtung gast" in seen
    owner = (await _texts(client, accounts["owner"]))["notifications"]
    assert "Achtung fremdkonto" in owner and "Achtung altkonto" in owner


@pytest.mark.parametrize("source", ["api/v1/users.py", "services/auth.py"])
def test_account_notices_name_their_account(source):
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "nodvard_deck"
    kinds = []
    for node in ast.walk(ast.parse((root / source).read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}
        kind = keys.get("notification_type")
        if isinstance(kind, ast.Constant) and str(kind.value).startswith("account."):
            assert "account_id" in keys, (source, kind.value)
            kinds.append(kind.value)
    assert kinds


async def _guessed_password(db_session, user):
    from contextlib import asynccontextmanager

    from nodvard_deck.services import auth as auth_service

    @asynccontextmanager
    async def _scope():
        yield db_session

    original = auth_service.session_scope
    auth_service.session_scope = _scope
    try:
        await auth_service._report_password_guessing(user.id)
    finally:
        auth_service.session_scope = original


async def _guessed_code(db_session, user):
    from nodvard_deck.services import auth as auth_service

    await auth_service._report_mfa_guessing(db_session, user)


async def _used_recovery(db_session, user):
    from nodvard_deck.services import auth as auth_service

    await auth_service.report_recovery_use(db_session, user, ip=None, user_agent=None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "report"),
    [
        ("account.password_attempts", _guessed_password),
        ("account.totp_attempts", _guessed_code),
        ("account.recovery_used", _used_recovery),
    ],
)
async def test_account_alerts_only_for_their_account(client, accounts, db_session, kind, report):
    from nodvard_deck.models import User

    owner, viewer = accounts["owner"], accounts["viewer"]
    before = (await client.get("/api/v1/notifications/unread-count", headers=_h(viewer))).json()["unread"]
    await report(db_session, await db_session.get(User, accounts["other_id"]))
    await report(db_session, await db_session.get(User, accounts["gast_id"]))
    await db_session.commit()
    overview_api.reset_cache()

    notes = (await client.get("/api/v1/notifications", headers=_h(owner))).json()
    of_kind = [n for n in notes if n["payload"].get("notification_type") == kind]
    assert [n["payload"].get("account_id") for n in of_kind] == [accounts["gast_id"], accounts["other_id"]]
    foreign = of_kind[1]
    assert "fremdkonto" in foreign["body"]
    assert (await client.get(f"/api/v1/notifications/{foreign['id']}", headers=_h(viewer))).status_code == 404
    seen = await _texts(client, viewer)
    for where in ("notifications", "unread", "overview"):
        assert "fremdkonto" not in seen[where]
    assert [n["id"] for n in (await client.get("/api/v1/notifications", headers=_h(viewer))).json()
            if n["payload"].get("notification_type") == kind] == [of_kind[0]["id"]]
    after = (await client.get("/api/v1/notifications/unread-count", headers=_h(viewer))).json()["unread"]
    assert after == before + 1
    overview = (await client.get("/api/v1/overview", headers=_h(viewer))).json()
    assert overview["unread_notifications"] == after


@pytest.mark.asyncio
async def test_deleted_foreign_notice_keeps_its_title_from_viewers(client, accounts):
    owner, viewer = accounts["owner"], accounts["viewer"]
    notes = (await client.get("/api/v1/notifications", headers=_h(owner))).json()
    changed = [n for n in notes if n["payload"].get("notification_type") == "account.changed"]
    foreign = next(n for n in changed if "fremdkonto" in n["title"])
    own = next(n for n in changed if "gast" in n["title"])
    for note in (foreign, own):
        assert (await client.delete(f"/api/v1/notifications/{note['id']}", headers=_h(owner))).status_code == 204

    def _deleted(rows):
        return {e["target_id"]: e["detail"] for e in rows if e["action"] == "notifications.deleted"}

    seen = _deleted((await client.get("/api/v1/audit", params={"limit": 1000}, headers=_h(viewer))).json())
    assert "title" not in seen[foreign["id"]]
    assert seen[own["id"]]["title"] == own["title"]
    texts = await _texts(client, viewer)
    assert "fremdkonto" not in texts["audit"] and "fremdkonto" not in texts["export"]
    full = _deleted((await client.get("/api/v1/audit", params={"limit": 1000}, headers=_h(owner))).json())
    assert full[foreign["id"]]["title"] == foreign["title"]
    assert "fremdkonto" in (await _texts(client, owner))["export"]


def _deleted_entry(target_id: str, detail: dict):
    from nodvard_deck.models import AuditEntry

    return AuditEntry(
        id=f"d-{target_id}", actor_type="user", actor_id="a1", action="notifications.deleted", outcome="success",
        target_type="notification", target_id=target_id, detail=detail,
    )


def test_deleted_notice_titles_of_other_accounts_are_hidden():
    from nodvard_deck.services.audit import hide_other_accounts

    old = [
        _deleted_entry(f"o{i}", {"title": title})
        for i, title in enumerate(
            ("Zugang von „a“ geändert", "Neues Konto mit Verwaltungsrechten: „b“", "Verwaltungsrechte für „c“")
        )
    ]
    foreign = _deleted_entry("n2", {"title": "x", "notification_type": "account.changed", "account_id": "u2"})
    unknown = _deleted_entry("n3", {"title": "Zugang", "notification_type": "account.changed", "account_id": None})
    own = _deleted_entry("n4", {"title": "y", "notification_type": "account.changed", "account_id": "u1"})
    other = _deleted_entry("n5", {"title": "Backup fertig"})
    bulk = _deleted_entry("n6", {"count": 3})
    shown = hide_other_accounts([*old, foreign, unknown, own, other, bulk], "u1")
    assert all("title" not in e.detail for e in shown[:5])
    assert shown[5:] == [own, other, bulk]
    assert old[0].detail["title"] == "Zugang von „a“ geändert"


def test_every_account_change_title_counts_as_account_title():
    from nodvard_deck.api.v1.users import _CHANGE_TEXT
    from nodvard_deck.services.notifications import ACCOUNT_TITLE_PREFIXES

    for title, _ in _CHANGE_TEXT.values():
        assert title.format(target="x").startswith(ACCOUNT_TITLE_PREFIXES)


@pytest.mark.asyncio
async def test_own_notice_names_no_other_account(client, accounts):
    owner, viewer = accounts["owner"], accounts["viewer"]
    own = [n for n in (await client.get("/api/v1/notifications", headers=_h(viewer))).json()
           if n["payload"].get("notification_type") == "account.changed"]
    assert len(own) == 1
    note = own[0]
    single = (await client.get(f"/api/v1/notifications/{note['id']}", headers=_h(viewer))).json()
    for shown in (note, single):
        assert "inhaberin" not in shown["body"] and "inhaberin" not in str(shown["payload"])
        assert "neues Passwort" in shown["body"] and "„gast“" in shown["body"]
    full = (await client.get(f"/api/v1/notifications/{note['id']}", headers=_h(owner))).json()
    assert "„inhaberin“ hat für „gast“ ein neues Passwort gesetzt." in full["body"]


@pytest.mark.asyncio
async def test_own_notice_hides_a_foreign_admin_too(client, accounts):
    owner = accounts["owner"]
    admin = await _login(client, "fremdadmin", PASSWORD)
    r = await client.patch(
        f"/api/v1/users/{accounts['gast_id']}",
        json={"password": PASSWORD, "current_password": PASSWORD}, headers=_h(admin),
    )
    assert r.status_code == 200, r.text
    viewer = await _login(client, "gast", PASSWORD)
    seen = await _texts(client, viewer)
    assert "fremdadmin" not in seen["notifications"]
    assert "„fremdadmin“ hat für „gast“" in (await _texts(client, owner))["notifications"]
