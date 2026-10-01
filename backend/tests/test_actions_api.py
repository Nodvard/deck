"""GET/POST /actions -- das generische Gate-Journal (docs/04-API.md
"Aktionen -- der Bestaetigungs-Workflow")."""

from __future__ import annotations

import pytest
from nodvard_sdk import ActionRequest, ActionResult, DryRunReport
from nodvard_sdk.capabilities import ActionExecutor

from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import Action


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


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


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _make_action(
    db_session, *, risk="low", status_="proposed", action_type="shell.exec", proposed_by=("ai", "test-model")
) -> Action:
    row = Action(
        ext_id="terminal", action_type=action_type, host_id=None,
        payload={"command": "true"}, risk=risk, status=status_,
        proposed_by_type=proposed_by[0], proposed_by_id=proposed_by[1], reason="Testvorschlag",
        gate_decision={"rule": "autonomy:propose"},
    )
    db_session.add(row)
    await db_session.flush()
    return row


class _FakeExecutor:
    action_types = frozenset({"shell.exec"})

    async def execute(self, req: ActionRequest) -> ActionResult:
        return ActionResult(success=True, exit_code=0)

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        return None


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(client):
    assert (await client.get("/api/v1/actions")).status_code == 401


@pytest.mark.asyncio
async def test_user_without_hosts_read_cannot_list(client, db_session):
    await _create_user_with_role(db_session, username="noperm", password="whatever123", role="viewer")
    # viewer HAT hosts.read -- also einen Nutzer ganz ohne Rolle nehmen:
    from nodvard_deck.core import security
    from nodvard_deck.models import User

    db_session.add(User(username="norole", password_hash=security.hash_password("whatever123"), is_active=True))
    await db_session.flush()

    token = await _login(client, "norole", "whatever123")
    r = await client.get("/api/v1/actions", headers=_auth_header(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_and_get_actions(client, db_session):
    token = await _bootstrap_owner(client)
    a1 = await _make_action(db_session, risk="low")
    a2 = await _make_action(db_session, risk="high", status_="denied")

    listed = await client.get("/api/v1/actions", headers=_auth_header(token))
    assert listed.status_code == 200
    ids = {row["id"] for row in listed.json()}
    assert {a1.id, a2.id} <= ids

    filtered = await client.get("/api/v1/actions", params={"status_": "denied"}, headers=_auth_header(token))
    assert [row["id"] for row in filtered.json()] == [a2.id]

    got = await client.get(f"/api/v1/actions/{a1.id}", headers=_auth_header(token))
    assert got.status_code == 200
    assert got.json()["reason"] == "Testvorschlag"

    missing = await client.get("/api/v1/actions/does-not-exist", headers=_auth_header(token))
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_operator_can_approve_low_risk_but_not_high_risk(client, db_session):
    get_extension_runtime().capabilities.provide("terminal", ActionExecutor, _FakeExecutor())
    await _create_user_with_role(db_session, username="op1", password="whatever123", role="operator")
    token = await _login(client, "op1", "whatever123")

    low = await _make_action(db_session, risk="low")
    high = await _make_action(db_session, risk="high")

    approved = await client.post(f"/api/v1/actions/{low.id}/approve", headers=_auth_header(token))
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "succeeded"

    forbidden = await client.post(f"/api/v1/actions/{high.id}/approve", headers=_auth_header(token))
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_approve_unknown_action_is_404(client):
    token = await _bootstrap_owner(client)
    r = await client.post("/api/v1/actions/does-not-exist/approve", headers=_auth_header(token))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_double_approve_second_call_returns_409(client, db_session):
    get_extension_runtime().capabilities.provide("terminal", ActionExecutor, _FakeExecutor())
    token = await _bootstrap_owner(client)
    action = await _make_action(db_session, risk="low")

    first = await client.post(f"/api/v1/actions/{action.id}/approve", headers=_auth_header(token))
    assert first.status_code == 200

    second = await client.post(f"/api/v1/actions/{action.id}/approve", headers=_auth_header(token))
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_approve_expired_proposal_returns_409_and_keeps_it_expired(client, db_session):
    """gate.approve() setzt einen abgelaufenen Vorschlag auf 'expired', die
    Route antwortet 409 -- und die echte get_session() (session_scope) rollte bei
    dieser Ausnahme alles zurueck. Die Zeile blieb 'Wartet', samt Knoepfen und
    Zaehler, bis der stuendliche Aufraeum-Job kam. Die `client`-Fixture reicht die
    Session ohne Commit/Rollback durch und haette das nie gezeigt, deshalb hier
    dasselbe Verhalten wie session_scope()."""
    from datetime import timedelta

    from nodvard_deck.api.deps import get_session
    from nodvard_deck.db import utcnow
    from nodvard_deck.main import app

    async def _like_session_scope():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_session] = _like_session_scope

    get_extension_runtime().capabilities.provide("terminal", ActionExecutor, _FakeExecutor())
    token = await _bootstrap_owner(client)
    action = await _make_action(db_session, risk="low")
    action.expires_at = utcnow() - timedelta(minutes=1)
    await db_session.commit()

    r = await client.post(f"/api/v1/actions/{action.id}/approve", headers=_auth_header(token))
    assert r.status_code == 409
    assert "expired" in r.json()["detail"]

    await db_session.refresh(action)
    assert action.status == "expired"

    pending = await client.get("/api/v1/actions?status_=proposed", headers=_auth_header(token))
    assert pending.status_code == 200
    assert all(a["id"] != action.id for a in pending.json())


@pytest.mark.asyncio
async def test_reject_requires_reason_and_denies(client, db_session):
    token = await _bootstrap_owner(client)
    action = await _make_action(db_session, risk="medium")

    missing_reason = await client.post(f"/api/v1/actions/{action.id}/reject", json={}, headers=_auth_header(token))
    assert missing_reason.status_code == 422

    r = await client.post(
        f"/api/v1/actions/{action.id}/reject", json={"reason": "Falscher Host."}, headers=_auth_header(token)
    )
    assert r.status_code == 200
    assert r.json()["status"] == "denied"

    again = await client.post(
        f"/api/v1/actions/{action.id}/reject", json={"reason": "Zu spaet."}, headers=_auth_header(token)
    )
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_dismiss_action(client, db_session):
    token = await _bootstrap_owner(client)
    action = await _make_action(db_session, risk="low")

    r = await client.post(f"/api/v1/actions/{action.id}/dismiss", headers=_auth_header(token))
    assert r.status_code == 200
    assert r.json()["status"] == "dismissed"


@pytest.mark.asyncio
async def test_stale_proposals_expire_proactively_with_audit(db_session):
    """Vorher nur beim Klick geprueft -- jetzt markiert der stuendliche Kern-Job sie."""
    from datetime import timedelta

    from sqlalchemy import select

    from nodvard_deck.core.gate import expire_stale
    from nodvard_deck.db import utcnow
    from nodvard_deck.models import AuditEntry

    old = await _make_action(db_session)
    old.expires_at = utcnow() - timedelta(minutes=1)
    fresh = await _make_action(db_session)
    fresh.expires_at = utcnow() + timedelta(hours=1)
    done = await _make_action(db_session, status_="succeeded")
    done.expires_at = utcnow() - timedelta(days=1)
    await db_session.flush()

    assert await expire_stale(db_session) == 1
    await db_session.refresh(old)
    await db_session.refresh(fresh)
    await db_session.refresh(done)
    assert (old.status, fresh.status, done.status) == ("expired", "proposed", "succeeded")
    audit = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "action.expired"))).scalars().all()
    assert [a.target_id for a in audit] == [old.id]
    assert await expire_stale(db_session) == 0, "idempotent"


# ---------------------------------------------------------------------------
# "Vorgeschlagen von" als Name statt "user/<uuid>"
# ---------------------------------------------------------------------------


async def _owner_id(client, token) -> str:
    return (await client.get("/api/v1/me", headers=_auth_header(token))).json()["id"]


@pytest.mark.asyncio
async def test_proposed_by_label_per_actor_type(client, db_session):
    from nodvard_deck.models import ExtensionRecord

    token = await _bootstrap_owner(client, username="owner1")
    owner_id = await _owner_id(client, token)
    db_session.add(ExtensionRecord(
        id="terminal", version="0.1.0", api_version="0.1", state="enabled",
        manifest={"name": "Terminal", "id": "terminal"},
    ))
    db_session.add(ExtensionRecord(id="stumm", version="0.1.0", api_version="0.1", state="enabled", manifest={}))
    await db_session.flush()

    cases = {
        "user": await _make_action(db_session, proposed_by=("user", owner_id)),
        "ext": await _make_action(db_session, proposed_by=("extension", "terminal")),
        "system": await _make_action(db_session, proposed_by=("system", "gate")),
        "scheduler": await _make_action(db_session, proposed_by=("scheduler", "job-7")),
        "ai": await _make_action(db_session, proposed_by=("ai", "qwen2.5:7b")),
        # Nicht aufloesbar -> der rohe Wert wie bisher:
        "gone": await _make_action(db_session, proposed_by=("user", "00000000-0000-0000-0000-000000000000")),
        "no_ext": await _make_action(db_session, proposed_by=("extension", "entfernt")),
        "no_name": await _make_action(db_session, proposed_by=("extension", "stumm")),
        "unknown_type": await _make_action(db_session, proposed_by=("event", "sonstwas")),
    }
    rows = {r["id"]: r for r in (await client.get("/api/v1/actions", headers=_auth_header(token))).json()}
    label = {name: rows[a.id]["proposed_by_label"] for name, a in cases.items()}
    assert label == {
        "user": "owner1",
        "ext": "Terminal",
        "system": "System",
        "scheduler": "Zeitplan",
        "ai": "KI",
        "gone": "user/00000000-0000-0000-0000-000000000000",
        "no_ext": "extension/entfernt",
        "no_name": "extension/stumm",
        "unknown_type": "event/sonstwas",
    }
    # Die rohen Felder bleiben unveraendert (die Oberflaeche zeigt sie als Tooltip).
    assert rows[cases["user"].id]["proposed_by_id"] == owner_id
    assert rows[cases["user"].id]["proposed_by_type"] == "user"
    assert rows[cases["user"].id]["approved_by_label"] is None
    assert rows[cases["user"].id]["approved_by_user_id"] is None

    single = (await client.get(f"/api/v1/actions/{cases['ext'].id}", headers=_auth_header(token))).json()
    assert single["proposed_by_label"] == "Terminal"


@pytest.mark.asyncio
async def test_label_does_not_leak_more_than_the_name(client, db_session):
    """Aufgeloest wird nur der Benutzername -- keine E-Mail, keine Rollen, kein Hash."""
    token = await _bootstrap_owner(client)
    user = await _create_user_with_role(db_session, username="kollegin", password="whatever123", role="viewer")
    user.email = "geheim@example.org"
    action = await _make_action(db_session, proposed_by=("user", user.id))
    await db_session.flush()

    body = (await client.get(f"/api/v1/actions/{action.id}", headers=_auth_header(token))).text
    assert "kollegin" in body
    assert "geheim@example.org" not in body
    assert user.password_hash not in body


@pytest.mark.asyncio
async def test_other_users_names_only_for_those_who_may_see_them(client, db_session):
    """Entscheidung zu den Namen: `GET /actions` verlangt nur `hosts.read`, `GET /users`
    aber `users.read`. Der Betrachter (viewer) sieht darum bei fremden Nutzern weiter die
    rohe Kennung; der Bediener (darf freigeben) und der Owner sehen den Namen."""
    from nodvard_deck.models import ExtensionRecord

    owner_token = await _bootstrap_owner(client, username="chef-admin")
    owner_id = await _owner_id(client, owner_token)
    viewer = await _create_user_with_role(db_session, username="gast", password="whatever123", role="viewer")
    await _create_user_with_role(db_session, username="op1", password="whatever123", role="operator")
    db_session.add(ExtensionRecord(
        id="terminal", version="0.1.0", api_version="0.1", state="enabled", manifest={"name": "Terminal"},
    ))
    theirs = await _make_action(db_session, proposed_by=("user", owner_id))
    theirs.approved_by_user_id = owner_id
    own = await _make_action(db_session, proposed_by=("user", viewer.id))
    ext = await _make_action(db_session, proposed_by=("extension", "terminal"))
    ai = await _make_action(db_session, proposed_by=("ai", "qwen"))
    await db_session.flush()

    async def labels(token):
        r = await client.get("/api/v1/actions", headers=_auth_header(token))
        assert r.status_code == 200, r.text
        return {row["id"]: row for row in r.json()}

    gast = await labels(await _login(client, "gast", "whatever123"))
    assert (await client.get("/api/v1/users", headers=_auth_header(await _login(client, "gast", "whatever123")))
            ).status_code == 403
    assert gast[theirs.id]["proposed_by_label"] == f"user/{owner_id}"
    assert gast[theirs.id]["approved_by_label"] == owner_id
    assert "chef-admin" not in str(gast), "der Name des Owners darf dem Betrachter nicht rausrutschen"
    # Den eigenen Namen, Erweiterungen und die festen Bezeichnungen sieht auch der Betrachter:
    assert gast[own.id]["proposed_by_label"] == "gast"
    assert gast[ext.id]["proposed_by_label"] == "Terminal"
    assert gast[ai.id]["proposed_by_label"] == "KI"
    # Auch die Einzelabfrage:
    single = await client.get(
        f"/api/v1/actions/{theirs.id}", headers=_auth_header(await _login(client, "gast", "whatever123"))
    )
    assert single.json()["proposed_by_label"] == f"user/{owner_id}"

    for token in (await _login(client, "op1", "whatever123"), owner_token):
        rows = await labels(token)
        assert rows[theirs.id]["proposed_by_label"] == "chef-admin"
        assert rows[theirs.id]["approved_by_label"] == "chef-admin"
        assert rows[own.id]["proposed_by_label"] == "gast"


def _fake_user(*permissions: str, owner: bool = False):
    from nodvard_deck.models import Role, RolePermission, User

    role = Role(name="test", permissions=[RolePermission(permission=p) for p in permissions])
    return User(username="x", password_hash="x", is_owner=owner, roles=[role])


@pytest.mark.parametrize(
    ("permissions", "owner", "expected"),
    [
        (("hosts.read",), False, False),                       # Betrachter
        (("hosts.read", "hosts.execute"), False, False),       # darf ausloesen, nicht entscheiden
        (("hosts.read", "actions.approve:low"), False, True),  # Bediener: entscheidet
        (("hosts.read", "actions.approve:high"), False, True),
        (("hosts.read", "users.read"), False, True),           # kennt die Namen aus der Nutzerverwaltung
        (("*",), False, True),                                 # Admin
        ((), True, True),                                      # Owner
        (("hosts.read", "actions.approval:x"), False, False),  # nur echte Berechtigung zaehlt
    ],
)
def test_may_see_other_users(permissions, owner, expected):
    from nodvard_deck.services.actor_labels import may_see_other_users

    assert may_see_other_users(_fake_user(*permissions, owner=owner)) is expected


@pytest.mark.asyncio
async def test_viewer_list_skips_the_user_lookup(client, db_session):
    """Wer fremde Namen nicht sehen darf, loest sie auch nicht auf -- ohne Abfrage."""
    from sqlalchemy import event

    await _bootstrap_owner(client)
    viewer = await _create_user_with_role(db_session, username="gast", password="whatever123", role="viewer")
    other = await _create_user_with_role(db_session, username="andere", password="whatever123", role="viewer")
    await _make_action(db_session, proposed_by=("user", other.id))
    await _make_action(db_session, proposed_by=("user", viewer.id))
    token = await _login(client, "gast", "whatever123")

    statements: list[str] = []
    engine = db_session.bind.sync_engine

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        r = await client.get("/api/v1/actions", headers=_auth_header(token))
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    assert r.status_code == 200
    assert {row["proposed_by_label"] for row in r.json()} == {"gast", f"user/{other.id}"}
    assert not [s for s in statements if "users.username" in s and " IN " in s], statements


@pytest.mark.asyncio
async def test_approved_by_label_names_the_deciding_user(client, db_session):
    get_extension_runtime().capabilities.provide("terminal", ActionExecutor, _FakeExecutor())
    token = await _bootstrap_owner(client, username="owner1")
    approved = await _make_action(db_session, risk="low")
    rejected = await _make_action(db_session, risk="low")
    orphan = await _make_action(db_session, risk="low", status_="denied")
    orphan.approved_by_user_id = "11111111-1111-1111-1111-111111111111"
    await db_session.flush()

    r = await client.post(f"/api/v1/actions/{approved.id}/approve", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert r.json()["approved_by_label"] == "owner1"
    assert r.json()["proposed_by_label"] == "KI"

    r = await client.post(
        f"/api/v1/actions/{rejected.id}/reject", json={"reason": "Nein."}, headers=_auth_header(token)
    )
    assert r.json()["approved_by_label"] == "owner1"

    rows = {x["id"]: x for x in (await client.get("/api/v1/actions", headers=_auth_header(token))).json()}
    assert rows[approved.id]["approved_by_label"] == "owner1"
    # Gelöschter Entscheider -> die rohe Kennung statt eines Namens.
    assert rows[orphan.id]["approved_by_label"] == "11111111-1111-1111-1111-111111111111"


@pytest.mark.asyncio
async def test_labels_for_many_actions_cost_one_query_per_kind(client, db_session):
    """Kein N+1: 30 Aktionen von 3 Nutzern und 2 Erweiterungen -> je EINE Abfrage."""
    from nodvard_deck.models import ExtensionRecord
    from sqlalchemy import event

    token = await _bootstrap_owner(client)
    users = [
        await _create_user_with_role(db_session, username=f"nutzer{i}", password="whatever123", role="viewer")
        for i in range(3)
    ]
    for ext_id in ("proxmox", "backups"):
        db_session.add(ExtensionRecord(
            id=ext_id, version="0.1.0", api_version="0.1", state="enabled", manifest={"name": ext_id.title()},
        ))
    await db_session.flush()
    for i in range(30):
        who = ("user", users[i % 3].id) if i % 2 else ("extension", ("proxmox", "backups")[i % 4 // 2])
        await _make_action(db_session, proposed_by=who)

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db_session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        r = await client.get("/api/v1/actions", headers=_auth_header(token))
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 30
    assert {row["proposed_by_label"] for row in body} == {"nutzer0", "nutzer1", "nutzer2", "Proxmox", "Backups"}

    user_lookups = [s for s in statements if "users.username" in s and " IN " in s]
    ext_lookups = [s for s in statements if "extensions.manifest" in s and " IN " in s]
    assert len(user_lookups) == 1, statements
    assert len(ext_lookups) == 1, statements


@pytest.mark.asyncio
async def test_list_without_named_actors_skips_the_lookups(client, db_session):
    """Nur KI/System-Vorschlaege -> es gibt nichts nachzuschlagen, also auch keine Abfrage."""
    from sqlalchemy import event

    token = await _bootstrap_owner(client)
    await _make_action(db_session, proposed_by=("ai", "m"))
    await _make_action(db_session, proposed_by=("system", "gate"))

    statements: list[str] = []
    engine = db_session.bind.sync_engine

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        assert (await client.get("/api/v1/actions", headers=_auth_header(token))).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    assert not [s for s in statements if "users.username" in s and " IN " in s]
    assert not [s for s in statements if "extensions.manifest" in s]


# ---------------------------------------------------------------------------
# Lange Aktionen laufen im Hintergrund, die Anfrage wartet begrenzt
# ---------------------------------------------------------------------------


class _SlowExecutor:
    """Laeuft, bis der Test `release` setzt -- wie ein Update, das 45 min dauert."""

    action_types = frozenset({"shell.exec"})

    def __init__(self, *, fail: bool = False) -> None:
        import asyncio

        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = fail

    async def execute(self, req: ActionRequest) -> ActionResult:
        self.started.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("Paketquelle nicht erreichbar")
        return ActionResult(success=True, exit_code=0, output="fertig")

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        return None


async def _file_action(sessionmaker, **kwargs) -> Action:
    async with sessionmaker() as session:
        row = await _make_action(session, **kwargs)
        await session.commit()
        return row


@pytest.mark.asyncio
async def test_slow_approve_returns_202_and_finishes_in_background(client, file_db, monkeypatch):
    import asyncio

    from sqlalchemy import select

    from nodvard_deck.core import gate
    from nodvard_deck.models import AuditEntry

    monkeypatch.setattr(gate, "WAIT_S", 0.2)
    executor = _SlowExecutor()
    get_extension_runtime().capabilities.provide("terminal", ActionExecutor, executor)
    token = await _bootstrap_owner(client)
    action = await _file_action(file_db, risk="low")

    r = await client.post(f"/api/v1/actions/{action.id}/approve", headers=_auth_header(token))
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "executing"
    assert r.json()["approved_by_user_id"] is not None
    assert r.headers["location"] == f"/api/v1/actions/{action.id}"

    await asyncio.wait_for(executor.started.wait(), timeout=5)
    polled = await client.get(f"/api/v1/actions/{action.id}", headers=_auth_header(token))
    assert polled.json()["status"] == "executing"
    assert polled.json()["executed_at"] is not None

    # Ein zweiter Klick waehrend der Ausfuehrung fuehrt nicht noch einmal aus.
    again = await client.post(f"/api/v1/actions/{action.id}/approve", headers=_auth_header(token))
    assert again.status_code == 409

    executor.release.set()
    assert await gate.wait_for_action(action.id, 5) is True
    done = await client.get(f"/api/v1/actions/{action.id}", headers=_auth_header(token))
    assert done.json()["status"] == "succeeded"
    assert done.json()["result"]["output"] == "fertig"
    async with file_db() as verify:
        stored = await verify.get(Action, action.id)
        assert stored.status == "succeeded"
        assert stored.finished_at is not None
        executed = (
            await verify.execute(select(AuditEntry).where(AuditEntry.action == "action.executed"))
        ).scalars().one()
        assert executed.outcome == "success"


@pytest.mark.asyncio
async def test_slow_approve_failure_ends_in_failed_with_reason(client, file_db, monkeypatch):
    import asyncio

    from nodvard_deck.core import gate

    monkeypatch.setattr(gate, "WAIT_S", 0.2)
    executor = _SlowExecutor(fail=True)
    get_extension_runtime().capabilities.provide("terminal", ActionExecutor, executor)
    token = await _bootstrap_owner(client)
    action = await _file_action(file_db, risk="low")

    r = await client.post(f"/api/v1/actions/{action.id}/approve", headers=_auth_header(token))
    assert r.status_code == 202, r.text

    await asyncio.wait_for(executor.started.wait(), timeout=5)
    executor.release.set()
    assert await gate.wait_for_action(action.id, 5) is True
    done = (await client.get(f"/api/v1/actions/{action.id}", headers=_auth_header(token))).json()
    assert done["status"] == "failed"
    assert done["result"]["error"] == "Paketquelle nicht erreichbar"


@pytest.mark.asyncio
async def test_approve_with_wait_zero_does_not_wait(client, file_db):
    """Die Sammel-Freigabe will nicht je Aktion bis zu 20 s warten."""
    import time

    from nodvard_deck.core import gate

    executor = _SlowExecutor()
    get_extension_runtime().capabilities.provide("terminal", ActionExecutor, executor)
    token = await _bootstrap_owner(client)
    action = await _file_action(file_db, risk="low")

    started = time.monotonic()
    r = await client.post(f"/api/v1/actions/{action.id}/approve?wait=0", headers=_auth_header(token))
    assert time.monotonic() - started < 2
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "executing"

    executor.release.set()
    assert await gate.wait_for_action(action.id, 5) is True
    done = (await client.get(f"/api/v1/actions/{action.id}", headers=_auth_header(token))).json()
    assert done["status"] == "succeeded"


@pytest.mark.asyncio
async def test_fast_approve_still_returns_200_with_the_result(client, file_db):
    get_extension_runtime().capabilities.provide("terminal", ActionExecutor, _FakeExecutor())
    token = await _bootstrap_owner(client)
    action = await _file_action(file_db, risk="low")

    r = await client.post(f"/api/v1/actions/{action.id}/approve", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded"
    assert "location" not in r.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("wait", ["-1", "21", "abc"])
async def test_approve_rejects_an_invalid_wait(client, db_session, wait):
    token = await _bootstrap_owner(client)
    action = await _make_action(db_session, risk="low")
    r = await client.post(f"/api/v1/actions/{action.id}/approve?wait={wait}", headers=_auth_header(token))
    assert r.status_code == 422
    await db_session.refresh(action)
    assert action.status == "proposed"
