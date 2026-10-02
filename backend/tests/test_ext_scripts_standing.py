"""Dauerfreigabe fuer geplante Skripte (scripts-Extension, standing.py): ein Owner/Admin gibt
ein Skript einmal frei, danach laufen die Zeitplan-Laeufe ohne Klick, solange sich nichts
aendert -- und jeder Lauf steht weiter als Aktion im Protokoll."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import Action, AuditEntry
from nodvard_deck.services import hosts as hosts_service
from nodvard_sdk import ActionRequest, Actor, ActorType, Risk, StandingApproval
from nodvard_sdk.errors import PermissionDenied
from sqlalchemy import select
from test_ext_scripts import (  # noqa: F401 -- _cleanup_sys_path ist eine autouse-Fixture
    _PERMISSIONS,
    _auth_header,
    _capture_exec,
    _cleanup_sys_path,
    _enable_scripts,
    _host_with_credential,
    _scripts_extension_class,
    _setup_scripts_extension,
)


@pytest.fixture(autouse=True)
def _scripts_on_path(_cleanup_sys_path):  # noqa: F811 -- haengt bewusst an der Aufraeum-Fixture
    """Die Extension importierbar machen (auch fuer Tests ohne geladene Extension); die
    Aufraeum-Fixture nimmt den Pfad danach wieder heraus."""
    _scripts_extension_class()


@pytest.fixture(autouse=True)
def _global_runtime():
    """Das Gate sucht den Executor im globalen Runtime-Singleton (siehe test_ext_scripts.py,
    Ende-zu-Ende-Test) -- ohne den laeuft eine freigegebene Aktion nie bis zum Ende."""
    reset_extension_runtime()
    yield
    reset_extension_runtime()


async def _user(db_session, *, username: str, role: str, active: bool = True):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password("whatever123"), is_active=active)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()
    return user


async def _fleet(tmp_path, db_session, settings, *, hosts=("pve1", "bastel-pi")):
    """Gruppe mit zwei Servern (je mit Zugang als root), Skript mit Zeitplan auf die Gruppe."""
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings, runtime=get_extension_runtime())
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    group = await hosts_service.create_group(db_session, name="fleet")
    members = []
    for name in hosts:
        host = await _host_with_credential(db_session, settings, name)
        await hosts_service.add_group_member(db_session, group.id, host.id)
        members.append(host)
    await db_session.commit()
    meta = ScriptMeta(
        id="audit", name="Sicherheitsaudit", target={"kind": "group", "group_id": group.id},
        schedule="0 1 * * *", enabled=True,
    )
    ext._repo.save(meta, "echo audit\n", commit_message="audit angelegt")
    return ctx, ext, group, members


async def _grant(ctx, ext, user, script_id="audit", *, granted_at=None):
    """Freigabe so ablegen, wie es die Route tut (Ziele zum Zeitpunkt der Freigabe)."""
    from nodvard_deck_ext_scripts import _runnable_targets, _standing_approvals
    from nodvard_deck_ext_scripts.standing import StandingRecord, script_fingerprint

    script = ext._repo.get(script_id)
    targets, _ = await _runnable_targets(ctx, script.meta.target)
    record = StandingRecord(
        fingerprint=script_fingerprint(script.meta, script.content),
        granted_by_user_id=user.id, granted_by_label=user.username,
        granted_at=(granted_at or datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)).isoformat(),
        hosts={h.id: h.credential_username for h in targets},
        host_names={h.id: h.name for h in targets},
        addresses={h.id: h.address for h in targets},
        ports={h.id: h.credential_port for h in targets},
    )
    _standing_approvals(ctx).set(script_id, record)
    return record


async def _actions(db_session, script_id="audit"):
    stmt = select(Action).where(Action.correlation_id == f"script:{script_id}").order_by(Action.created_at)
    return (await db_session.execute(stmt.execution_options(populate_existing=True))).scalars().all()


async def _audit(db_session, action):
    stmt = select(AuditEntry).where(AuditEntry.action == action).execution_options(populate_existing=True)
    return (await db_session.execute(stmt)).scalars().all()


async def _scheduled(ctx, ext, script_id="audit"):
    from nodvard_deck_ext_scripts import run_script

    return await run_script(ctx, ext._repo, script_id, param_overrides={}, scheduled=True)


# --- Ablauf eines geplanten Laufs ------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduled_run_without_standing_approval_still_waits_for_a_click(
    tmp_path, db_session, test_settings, monkeypatch
):
    ctx, ext, _group, members = await _fleet(tmp_path, db_session, test_settings)
    commands = _capture_exec(monkeypatch, ctx)
    result = await _scheduled(ctx, ext)

    assert {r["status"] for r in result["results"]} == {"proposed"}
    assert commands == []
    rows = await _actions(db_session)
    assert len(rows) == len(members)
    assert {r.reason for r in rows} == {"Skript 'Sicherheitsaudit' (audit) ausführen"}
    assert all(r.approved_by_user_id is None and r.gate_decision["rule"] == "autonomy:propose" for r in rows)


@pytest.mark.asyncio
async def test_standing_approval_runs_all_target_hosts_without_a_click_and_logs_it(
    tmp_path, db_session, test_settings, monkeypatch
):
    ctx, ext, _group, members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    commands = _capture_exec(monkeypatch, ctx)

    result = await _scheduled(ctx, ext)

    assert {r["status"] for r in result["results"]} == {"succeeded"}
    assert commands == ["echo audit\n", "echo audit\n"]
    rows = await _actions(db_session)
    assert {r.host_id for r in rows} == {h.id for h in members}
    for row in rows:
        assert row.status == "succeeded"
        assert row.approved_by_user_id == admin.id
        assert row.gate_decision["rule"] == "standing_approval"
        assert row.gate_decision["standing_approval"]["granted_by"] == admin.id
        assert "ohne Klick, lief mit Dauerfreigabe vom 01.10.2026 durch chefin" in row.reason
    approved = [a for a in await _audit(db_session, "action.approved") if a.target_id in {r.id for r in rows}]
    assert len(approved) == 2
    assert all(a.detail["rule"] == "standing_approval" and a.actor_type == "extension" for a in approved)


@pytest.mark.asyncio
async def test_manual_runs_never_use_the_standing_approval(tmp_path, db_session, test_settings, monkeypatch):
    """"Jetzt ausführen" (Seite oder Jobs-Schnittstelle) bleibt ein Vorschlag -- die Freigabe
    gilt nur dem Zeitplan."""
    from nodvard_deck_ext_scripts import run_script

    ctx, ext, _group, _members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    commands = _capture_exec(monkeypatch, ctx)

    by_hand = await run_script(ctx, ext._repo, "audit", param_overrides={}, actor=Actor.user(admin.id, "chefin"))
    not_scheduled = await run_script(ctx, ext._repo, "audit", param_overrides={})

    assert {r["status"] for r in by_hand["results"] + not_scheduled["results"]} == {"proposed"}
    assert commands == []


@pytest.mark.asyncio
async def test_job_handler_uses_the_standing_approval_only_for_the_schedule_trigger(
    tmp_path, db_session, test_settings, monkeypatch
):
    from nodvard_sdk.context import _JOB_TRIGGER

    ctx, ext, _group, _members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    _capture_exec(monkeypatch, ctx)
    from nodvard_deck_ext_scripts import _ScriptJobSpec

    handler = _ScriptJobSpec(ctx, ext._repo, "audit", schedule="0 1 * * *", enabled=True).handler

    token = _JOB_TRIGGER.set("manual")
    try:
        manual = await handler()
    finally:
        _JOB_TRIGGER.reset(token)
    token = _JOB_TRIGGER.set("schedule")
    try:
        scheduled = await handler()
    finally:
        _JOB_TRIGGER.reset(token)

    assert {r["status"] for r in manual["results"]} == {"proposed"}
    assert {r["status"] for r in scheduled["results"]} == {"succeeded"}


@pytest.mark.asyncio
async def test_change_to_the_script_expires_the_approval_and_logs_and_notifies(
    tmp_path, db_session, test_settings, monkeypatch
):
    from nodvard_deck.models import Notification as NotificationRow
    from nodvard_deck_ext_scripts import _standing_approvals

    ctx, ext, _group, _members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    commands = _capture_exec(monkeypatch, ctx)
    script = ext._repo.get("audit")
    ext._repo.save(script.meta, "echo anders\n", commit_message="geaendert, an der Seite vorbei")

    result = await _scheduled(ctx, ext)

    assert {r["status"] for r in result["results"]} == {"proposed"}
    assert commands == []
    assert _standing_approvals(ctx).get("audit") is None
    expired = await _audit(db_session, "scripts.standing_approval.expired")
    assert len(expired) == 1 and "geändert" in expired[0].reason
    notes = (
        await db_session.execute(select(NotificationRow).where(NotificationRow.correlation_id == "scripts-standing:audit"))
    ).scalars().all()
    assert len(notes) == 1

    # Der naechste Lauf: normale Freigaben, keine zweite Meldung.
    await _scheduled(ctx, ext)
    assert len(await _audit(db_session, "scripts.standing_approval.expired")) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"content": "echo anders\n"},
        {"params_schema": {"tiefe": {"type": "string", "default": "voll"}}},
        {"target": {"kind": "all"}},
        {"schedule": "0 2 * * *"},
    ],
)
def test_fingerprint_covers_content_parameters_target_and_schedule(change):
    from nodvard_deck_ext_scripts.repo import ScriptMeta
    from nodvard_deck_ext_scripts.standing import script_fingerprint

    base = ScriptMeta(id="a", name="A", target={"kind": "group", "group_id": "g1"}, schedule="0 1 * * *")
    content = "echo audit\n"
    before = script_fingerprint(base, content)
    meta = ScriptMeta(**{**base.to_json(), **{k: v for k, v in change.items() if k != "content"}})
    assert script_fingerprint(meta, change.get("content", content)) != before


def test_fingerprint_ignores_name_description_and_enabled():
    from nodvard_deck_ext_scripts.repo import ScriptMeta
    from nodvard_deck_ext_scripts.standing import script_fingerprint

    base = ScriptMeta(id="a", name="A", target={"kind": "all"}, schedule="0 1 * * *")
    other = ScriptMeta(id="a", name="B", description="neu", target={"kind": "all"}, schedule="0 1 * * *", enabled=False)
    assert script_fingerprint(base, "x") == script_fingerprint(other, "x")


@pytest.mark.asyncio
async def test_new_host_in_the_group_gets_a_normal_approval_the_others_keep_running(
    tmp_path, db_session, test_settings, monkeypatch
):
    ctx, ext, group, members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    newcomer = await _host_with_credential(db_session, test_settings, "neu-pi")
    await hosts_service.add_group_member(db_session, group.id, newcomer.id)
    await db_session.commit()
    commands = _capture_exec(monkeypatch, ctx)

    result = await _scheduled(ctx, ext)

    status = {r["host_id"]: r["status"] for r in result["results"]}
    assert status == {members[0].id: "succeeded", members[1].id: "succeeded", newcomer.id: "proposed"}
    assert len(commands) == 2
    new_row = next(r for r in await _actions(db_session) if r.host_id == newcomer.id)
    assert "neuer Server, nicht in der Dauerfreigabe" in new_row.reason
    assert new_row.approved_by_user_id is None
    from nodvard_deck_ext_scripts import _standing_approvals

    assert _standing_approvals(ctx).get("audit") is not None, "die Freigabe der anderen bleibt"


@pytest.mark.asyncio
async def test_removed_host_does_not_cancel_the_approval_for_the_rest(tmp_path, db_session, test_settings, monkeypatch):
    ctx, ext, group, members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    await hosts_service.remove_group_member(db_session, group.id, members[1].id)
    await db_session.commit()
    _capture_exec(monkeypatch, ctx)

    result = await _scheduled(ctx, ext)

    assert [(r["host_id"], r["status"]) for r in result["results"]] == [(members[0].id, "succeeded")]


@pytest.mark.asyncio
async def test_other_login_account_on_an_approved_host_expires_the_whole_approval(
    tmp_path, db_session, test_settings, monkeypatch
):
    from nodvard_deck_ext_scripts import _standing_approvals

    ctx, ext, _group, members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    # Neuer Standard-Zugang unter einem anderen Konto (z. B. von root auf einen eigenen Benutzer).
    await hosts_service.add_credential(
        db_session, test_settings, host_id=members[1].id, kind="ssh_password", username="deck", port=22,
        secret_value="pw2",
    )
    await db_session.commit()
    commands = _capture_exec(monkeypatch, ctx)

    result = await _scheduled(ctx, ext)

    assert {r["status"] for r in result["results"]} == {"proposed"}
    assert commands == []
    assert _standing_approvals(ctx).get("audit") is None
    expired = await _audit(db_session, "scripts.standing_approval.expired")
    assert "anderen Konto" in expired[0].reason and "root → deck" in expired[0].reason


@pytest.mark.asyncio
async def test_demoted_admin_no_longer_counts_and_the_approval_expires(tmp_path, db_session, test_settings, monkeypatch):
    """Das Gate prueft die Person hinter der Freigabe bei jedem Lauf selbst."""
    from nodvard_deck.services import auth as auth_service
    from nodvard_deck_ext_scripts import _standing_approvals

    ctx, ext, _group, _members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    roles = await auth_service.ensure_builtin_roles(db_session)
    admin.roles = [roles["operator"]]
    await db_session.commit()
    commands = _capture_exec(monkeypatch, ctx)

    result = await _scheduled(ctx, ext)

    assert {r["status"] for r in result["results"]} == {"proposed"}
    assert commands == []
    assert _standing_approvals(ctx).get("audit") is None
    rows = await _actions(db_session)
    rejected = rows[0].gate_decision["standing_approval_rejected"]
    assert rejected["permission"] == "actions.standing_approval"
    # Die Aktion wartet auf einen Klick -- dann darf dort nicht "lief ohne Klick" stehen.
    for row in rows:
        assert "lief mit" not in row.reason and "ohne Klick" not in row.reason
        assert "gilt nicht" in row.reason and "Bitte selbst freigeben" in row.reason
        assert "actions." not in row.reason
    assert "kein Owner oder Admin mehr" in rows[0].reason
    proposed_log = [a for a in await _audit(db_session, "action.proposed") if a.target_id == rows[0].id]
    assert proposed_log[0].reason == rows[0].reason
    # Kein Fachbegriff in Protokoll und Push-Nachricht.
    from nodvard_deck.models import Notification as NotificationRow

    expired = await _audit(db_session, "scripts.standing_approval.expired")
    note = (
        await db_session.execute(select(NotificationRow).where(NotificationRow.correlation_id == "scripts-standing:audit"))
    ).scalars().one()
    assert "actions." not in expired[0].reason and "actions." not in note.body
    assert "kein Owner oder Admin mehr" in note.body


@pytest.mark.asyncio
async def test_secret_parameters_stay_out_of_the_action_and_the_approval_file(
    tmp_path, db_session, test_settings, monkeypatch
):
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, test_settings, runtime=get_extension_runtime())
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    host = await _host_with_credential(db_session, test_settings, "pve1")
    await ctx.secrets.create(label="scripts-geheim-api_key", kind="generic", value="s3cr3t-value")
    ext._repo.save(
        ScriptMeta(
            id="geheim", name="Geheim", params_schema={"api_key": {"type": "secret", "label": "Schlüssel"}},
            target={"kind": "host", "host_id": host.id}, schedule="0 1 * * *", enabled=True,
        ),
        "curl -H 'Authorization: Bearer $api_key' https://example.invalid\n",
        commit_message="geheim angelegt",
    )
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin, "geheim")
    commands = _capture_exec(monkeypatch, ctx)

    result = await _scheduled(ctx, ext, "geheim")

    assert result["results"][0]["status"] == "succeeded"
    assert commands == ["curl -H 'Authorization: Bearer s3cr3t-value' https://example.invalid\n"]
    row = (await _actions(db_session, "geheim"))[0]
    assert "s3cr3t-value" not in json.dumps(row.payload) + json.dumps(row.gate_decision) + row.reason
    assert "••••" in row.payload["command"]
    approvals = (tmp_path / "data" / "standing-approvals.json").read_text(encoding="utf-8")
    assert "s3cr3t-value" not in approvals


# --- Waehrend eines laufenden Laufs ----------------------------------------------------


def _after_first_propose(monkeypatch, ctx, action):
    """Fuehrt `action` aus, sobald der erste Server vorgeschlagen (und gelaufen) ist."""
    original = ctx.actions.propose
    calls = {"n": 0}

    async def _propose(request, **kwargs):
        decision = await original(request, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            await action()
        return decision

    monkeypatch.setattr(ctx.actions, "propose", _propose)


@pytest.mark.asyncio
async def test_revoking_during_a_run_makes_the_remaining_hosts_wait_for_a_click(
    tmp_path, db_session, test_settings, monkeypatch
):
    from nodvard_deck_ext_scripts import _standing_approvals

    ctx, ext, _group, _members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    commands = _capture_exec(monkeypatch, ctx)

    async def _revoke():
        _standing_approvals(ctx).remove("audit")

    _after_first_propose(monkeypatch, ctx, _revoke)
    result = await _scheduled(ctx, ext)

    assert [r["status"] for r in result["results"]] == ["succeeded", "proposed"]
    assert len(commands) == 1
    second = next(r for r in await _actions(db_session) if r.host_id == result["results"][1]["host_id"])
    assert second.approved_by_user_id is None and second.gate_decision["rule"] == "autonomy:propose"
    assert "Dauerfreigabe gilt nicht mehr" in second.reason and "zurückgezogen" in second.reason
    assert "ohne Klick" not in second.reason


@pytest.mark.asyncio
async def test_other_account_on_the_next_host_during_a_run_never_runs_without_a_click(
    tmp_path, db_session, test_settings, monkeypatch
):
    from nodvard_deck_ext_scripts import _standing_approvals

    ctx, ext, _group, members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    commands = _capture_exec(monkeypatch, ctx)
    order: list[str] = []

    async def _switch_account():
        first = order[0] if order else members[0].id
        other = next(h for h in members if h.id != first)
        await hosts_service.add_credential(
            db_session, test_settings, host_id=other.id, kind="ssh_password", username="deck", port=22,
            secret_value="pw2",
        )
        await db_session.commit()

    original = ctx.actions.propose

    async def _record(request, **kwargs):
        order.append(request.host_ref)
        return await original(request, **kwargs)

    monkeypatch.setattr(ctx.actions, "propose", _record)
    _after_first_propose(monkeypatch, ctx, _switch_account)
    result = await _scheduled(ctx, ext)

    assert [r["status"] for r in result["results"]] == ["succeeded", "proposed"]
    assert len(commands) == 1
    assert _standing_approvals(ctx).get("audit") is None
    expired = await _audit(db_session, "scripts.standing_approval.expired")
    assert len(expired) == 1 and "root → deck" in expired[0].reason


@pytest.mark.asyncio
async def test_executor_refuses_when_the_account_differs_from_the_approval(
    tmp_path, db_session, test_settings, monkeypatch
):
    """Zwischen Gate und Ausfuehrung kann sich das Konto aendern: der Executor vergleicht das
    freigegebene Konto aus dem Payload direkt vor dem Ausfuehren."""
    from nodvard_deck_ext_scripts import _ScriptActionExecutor

    ctx, ext, _group, members = await _fleet(tmp_path, db_session, test_settings, hosts=("pve1",))
    admin = await _user(db_session, username="chefin", role="admin")
    record = await _grant(ctx, ext, admin)
    host = members[0]
    await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password", username="deck", port=22, secret_value="pw2",
    )
    await db_session.commit()
    commands = _capture_exec(monkeypatch, ctx)
    executor = _ScriptActionExecutor(ctx, ext._repo)

    refused = await executor.execute(_standing_run(host, record, account="root"))
    assert refused.success is False and "root → deck" in (refused.error or "")
    assert commands == []
    assert (await executor.execute(_standing_run(host, record, account="deck"))).success is True
    assert commands == ["echo audit\n"]


def _standing_run(host, record, **payload):
    """Ein Vorschlag, wie `run_script` ihn bei einer Dauerfreigabe stellt (Konto, Port, Zeitpunkt)."""
    body = {
        "command": "echo audit\n", "script_id": "audit", "account": record.hosts.get(host.id),
        "port": record.ports.get(host.id), "standing_granted_at": record.granted_at,
    }
    return ActionRequest(
        action_type="script.run", host_ref=host.id, risk=Risk.HIGH, proposed_by=Actor.extension("scripts"),
        reason="Test", payload={**body, **payload},
        # So gibt das Gate eine Aktion weiter, die ueber die Dauerfreigabe ohne Klick anlief.
        standing_approval=StandingApproval(
            granted_by_user_id=record.granted_by_user_id, granted_at=datetime.fromisoformat(record.granted_at),
        ),
    )


@pytest.mark.asyncio
async def test_executor_refuses_when_the_approval_was_withdrawn_after_the_proposal(
    tmp_path, db_session, test_settings, monkeypatch
):
    """Zurueckziehen zwischen dem Vorschlag und dem Befehl: der Executor prueft direkt vor
    `ctx.exec.run` noch einmal, ob genau diese Dauerfreigabe gilt."""
    from nodvard_deck_ext_scripts import STANDING_WITHDRAWN, _ScriptActionExecutor, _standing_approvals

    ctx, ext, _group, members = await _fleet(tmp_path, db_session, test_settings, hosts=("pve1",))
    admin = await _user(db_session, username="chefin", role="admin")
    record = await _grant(ctx, ext, admin)
    commands = _capture_exec(monkeypatch, ctx)
    executor = _ScriptActionExecutor(ctx, ext._repo)

    assert (await executor.execute(_standing_run(members[0], record))).success is True
    assert commands == ["echo audit\n"]

    _standing_approvals(ctx).remove("audit")
    refused = await executor.execute(_standing_run(members[0], record))
    assert refused.success is False and refused.error == STANDING_WITHDRAWN
    assert commands == ["echo audit\n"]  # nichts Neues ausgefuehrt


@pytest.mark.asyncio
async def test_executor_refuses_an_approval_that_was_withdrawn_and_granted_again(
    tmp_path, db_session, test_settings, monkeypatch
):
    """Eine neu erteilte Freigabe deckt keinen Vorschlag, der unter der alten gestellt wurde."""
    from nodvard_deck_ext_scripts import STANDING_WITHDRAWN, _ScriptActionExecutor

    ctx, ext, _group, members = await _fleet(tmp_path, db_session, test_settings, hosts=("pve1",))
    admin = await _user(db_session, username="chefin", role="admin")
    old = await _grant(ctx, ext, admin)
    await _grant(ctx, ext, admin, granted_at=datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc))
    commands = _capture_exec(monkeypatch, ctx)

    refused = await _ScriptActionExecutor(ctx, ext._repo).execute(_standing_run(members[0], old))
    assert refused.success is False and refused.error == STANDING_WITHDRAWN
    assert commands == []


@pytest.mark.asyncio
async def test_executor_refuses_an_approval_that_expired_but_could_not_be_deleted(
    tmp_path, db_session, test_settings, monkeypatch
):
    """Erloschen, aber der Eintrag liess sich nicht loeschen (`block`): gilt auch fuer den Executor nicht."""
    from nodvard_deck_ext_scripts import STANDING_WITHDRAWN, _ScriptActionExecutor, _standing_approvals

    ctx, ext, _group, members = await _fleet(tmp_path, db_session, test_settings, hosts=("pve1",))
    admin = await _user(db_session, username="chefin", role="admin")
    record = await _grant(ctx, ext, admin)
    _standing_approvals(ctx).block("audit", record, "Test")
    commands = _capture_exec(monkeypatch, ctx)

    refused = await _ScriptActionExecutor(ctx, ext._repo).execute(_standing_run(members[0], record))
    assert refused.success is False and refused.error == STANDING_WITHDRAWN
    assert commands == []


@pytest.mark.asyncio
async def test_revoking_between_gate_and_executor_stops_the_command(tmp_path, db_session, test_settings, monkeypatch):
    """Ende zu Ende: Das Gate hat die Dauerfreigabe anerkannt, dann wird sie zurueckgezogen,
    bevor der Executor loslaeuft -- der Befehl laeuft nicht, die Aktion schlaegt fehl, der Rest
    des Laufs wartet auf einen Klick."""
    from nodvard_deck_ext_scripts import STANDING_WITHDRAWN, _ScriptActionExecutor, _standing_approvals

    ctx, ext, _group, _members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    commands = _capture_exec(monkeypatch, ctx)
    original = _ScriptActionExecutor.execute

    async def _revoke_first(self, req):
        _standing_approvals(ctx).remove("audit")
        return await original(self, req)

    monkeypatch.setattr(_ScriptActionExecutor, "execute", _revoke_first)
    result = await _scheduled(ctx, ext)

    assert [r["status"] for r in result["results"]] == ["failed", "proposed"]
    assert commands == []
    first = next(r for r in await _actions(db_session) if r.id == result["results"][0]["action_id"])
    assert first.status == "failed" and STANDING_WITHDRAWN in (first.result or {}).get("error", "")


@pytest.mark.asyncio
async def test_click_after_the_gate_refused_the_standing_approval_still_runs(
    tmp_path, db_session, test_settings, monkeypatch
):
    """Erkennt das Gate die Dauerfreigabe nicht an (Person hat keine Rechte mehr), wartet der Lauf
    auf einen Klick und die Freigabe erlischt. Der Vorschlag traegt noch ihre Angaben -- der Klick
    muss trotzdem wirken und darf nicht an "zurueckgezogen" scheitern."""
    from nodvard_deck.core import gate
    from nodvard_deck.services import auth as auth_service
    from nodvard_deck_ext_scripts import _standing_approvals

    ctx, ext, _group, _members = await _fleet(tmp_path, db_session, test_settings, hosts=("pve1",))
    admin = await _user(db_session, username="chefin", role="admin")
    other_admin = await _user(db_session, username="vertretung", role="admin")
    record = await _grant(ctx, ext, admin)
    roles = await auth_service.ensure_builtin_roles(db_session)
    admin.roles = [roles["operator"]]
    await db_session.commit()
    commands = _capture_exec(monkeypatch, ctx)

    result = await _scheduled(ctx, ext)
    assert [r["status"] for r in result["results"]] == ["proposed"]
    assert _standing_approvals(ctx).get("audit") is None
    (row,) = await _actions(db_session)
    assert row.payload["standing_granted_at"] == record.granted_at

    await gate.approve(db_session, row.id, user_id=other_admin.id)
    await db_session.commit()

    (row,) = await _actions(db_session)
    assert row.status == "succeeded", row.result
    assert commands == ["echo audit\n"]


@pytest.mark.asyncio
async def test_normal_approval_by_click_is_not_held_up_by_the_standing_check(
    tmp_path, db_session, test_settings, monkeypatch
):
    """Ohne Dauerfreigabe im Vorschlag prueft der Executor nichts davon."""
    from nodvard_deck_ext_scripts import _ScriptActionExecutor

    ctx, ext, _group, members = await _fleet(tmp_path, db_session, test_settings, hosts=("pve1",))
    commands = _capture_exec(monkeypatch, ctx)
    request = ActionRequest(
        action_type="script.run", host_ref=members[0].id, risk=Risk.HIGH, proposed_by=Actor.extension("scripts"),
        reason="Test", payload={"command": "echo audit\n", "script_id": "audit"},
    )

    assert (await _ScriptActionExecutor(ctx, ext._repo).execute(request)).success is True
    assert commands == ["echo audit\n"]


@pytest.mark.asyncio
async def test_new_address_of_an_approved_host_expires_the_approval(tmp_path, db_session, test_settings, monkeypatch):
    from nodvard_deck_ext_scripts import _standing_approvals

    ctx, ext, _group, members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    # Die Adresse direkt setzen: `update_host` loescht bei neuer Adresse auch das gespeicherte
    # SSH-Passwort, dann fiele der Server schon mangels Zugang heraus. Geprueft wird hier nur,
    # dass die Freigabe an der Adresse haengt.
    members[0].address = "192.168.2.50"
    await db_session.commit()
    commands = _capture_exec(monkeypatch, ctx)

    result = await _scheduled(ctx, ext)

    assert {r["status"] for r in result["results"]} == {"proposed"}
    assert commands == []
    assert _standing_approvals(ctx).get("audit") is None
    expired = await _audit(db_session, "scripts.standing_approval.expired")
    assert "neue Adresse" in expired[0].reason and "192.168.2.50" in expired[0].reason


@pytest.mark.asyncio
async def test_other_ssh_port_of_an_approved_host_expires_the_approval(tmp_path, db_session, test_settings, monkeypatch):
    """Gleiches Konto, gleiche Adresse, aber ein anderer SSH-Port: das ist ein anderes Ziel."""
    from nodvard_deck_ext_scripts import _standing_approvals

    ctx, ext, _group, members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    record = await _grant(ctx, ext, admin)
    assert record.ports == {h.id: 22 for h in members}
    await hosts_service.add_credential(
        db_session, test_settings, host_id=members[1].id, kind="ssh_password", username="root", port=2222,
        secret_value="pw2",
    )
    await db_session.commit()
    commands = _capture_exec(monkeypatch, ctx)

    result = await _scheduled(ctx, ext)

    assert {r["status"] for r in result["results"]} == {"proposed"}
    assert commands == []
    assert _standing_approvals(ctx).get("audit") is None
    expired = await _audit(db_session, "scripts.standing_approval.expired")
    assert "SSH-Port" in expired[0].reason and "22 → 2222" in expired[0].reason


@pytest.mark.asyncio
async def test_other_ssh_port_on_the_next_host_during_a_run_never_runs_without_a_click(
    tmp_path, db_session, test_settings, monkeypatch
):
    from nodvard_deck_ext_scripts import _standing_approvals

    ctx, ext, _group, members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    commands = _capture_exec(monkeypatch, ctx)
    order: list[str] = []
    original = ctx.actions.propose

    async def _record(request, **kwargs):
        order.append(request.host_ref)
        return await original(request, **kwargs)

    async def _switch_port():
        other = next(h for h in members if h.id != order[0])
        await hosts_service.add_credential(
            db_session, test_settings, host_id=other.id, kind="ssh_password", username="root", port=2222,
            secret_value="pw2",
        )
        await db_session.commit()

    monkeypatch.setattr(ctx.actions, "propose", _record)
    _after_first_propose(monkeypatch, ctx, _switch_port)
    result = await _scheduled(ctx, ext)

    assert [r["status"] for r in result["results"]] == ["succeeded", "proposed"]
    assert len(commands) == 1
    assert _standing_approvals(ctx).get("audit") is None
    expired = await _audit(db_session, "scripts.standing_approval.expired")
    assert len(expired) == 1 and "22 → 2222" in expired[0].reason


@pytest.mark.asyncio
async def test_executor_refuses_when_the_ssh_port_differs_from_the_approval(
    tmp_path, db_session, test_settings, monkeypatch
):
    """Zwischen Gate und Ausfuehrung kann sich der Port aendern: der Executor vergleicht den
    freigegebenen Port aus dem Payload direkt vor dem Ausfuehren."""
    from nodvard_deck_ext_scripts import _ScriptActionExecutor

    ctx, ext, _group, members = await _fleet(tmp_path, db_session, test_settings, hosts=("pve1",))
    admin = await _user(db_session, username="chefin", role="admin")
    record = await _grant(ctx, ext, admin)
    host = members[0]
    await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password", username="root", port=2222, secret_value="pw2",
    )
    await db_session.commit()
    commands = _capture_exec(monkeypatch, ctx)
    executor = _ScriptActionExecutor(ctx, ext._repo)

    refused = await executor.execute(_standing_run(host, record))
    assert refused.success is False and "22 → 2222" in (refused.error or "")
    assert commands == []
    assert (await executor.execute(_standing_run(host, record, port=2222))).success is True
    assert commands == ["echo audit\n"]


@pytest.mark.asyncio
async def test_run_hands_account_port_and_grant_time_to_the_executor(tmp_path, db_session, test_settings, monkeypatch):
    ctx, ext, _group, _members = await _fleet(tmp_path, db_session, test_settings, hosts=("pve1",))
    admin = await _user(db_session, username="chefin", role="admin")
    record = await _grant(ctx, ext, admin)
    _capture_exec(monkeypatch, ctx)

    result = await _scheduled(ctx, ext)

    row = next(r for r in await _actions(db_session) if r.id == result["results"][0]["action_id"])
    assert row.payload["account"] == "root" and row.payload["port"] == 22
    assert row.payload["standing_granted_at"] == record.granted_at


def test_targets_fingerprint_covers_the_ssh_port():
    from nodvard_deck_ext_scripts.standing import targets_fingerprint
    from nodvard_sdk import Host

    def _host(port):
        return Host(
            id="h1", name="pve1", display_name="pve1", address="192.168.2.10", credential_username="root",
            credential_port=port,
        )

    assert targets_fingerprint([_host(22)]) == targets_fingerprint([_host(22)])
    assert targets_fingerprint([_host(22)]) != targets_fingerprint([_host(2222)])


def test_record_without_recorded_ports_still_loads_and_skips_the_port_comparison():
    from nodvard_deck_ext_scripts.standing import StandingRecord

    base = {
        "fingerprint": "f", "granted_by_user_id": "u1", "granted_at": "2026-10-01T12:00:00+00:00",
        "hosts": {"h1": "root"},
    }
    assert StandingRecord.from_json(base).ports == {}
    loaded = StandingRecord.from_json({**base, "ports": {"h1": 2222, "h2": None}})
    assert loaded.ports == {"h1": 2222, "h2": None}
    assert StandingRecord.from_json({**base, "ports": {"h1": "kaputt"}}) is None  # fail closed


@pytest.mark.asyncio
async def test_date_in_the_log_uses_the_dashboard_time_zone(tmp_path, db_session, test_settings, monkeypatch):
    """22:30 UTC ist in Berlin schon der naechste Tag -- das Protokoll muss zur Oberflaeche passen."""
    from nodvard_deck.core import timezone as tz_service
    from nodvard_deck.services import settings as settings_service

    ctx, ext, _group, _members = await _fleet(tmp_path, db_session, test_settings)
    await settings_service.set_global(db_session, tz_service.SETTING_KEY, "Europe/Berlin")
    await db_session.commit()
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin, granted_at=datetime(2026, 10, 1, 22, 30, tzinfo=timezone.utc))
    _capture_exec(monkeypatch, ctx)

    await _scheduled(ctx, ext)

    rows = await _actions(db_session)
    assert rows and all("lief mit Dauerfreigabe vom 02.10.2026 durch chefin" in r.reason for r in rows)


# --- Erloeschen, auch wenn Datei oder Protokoll streiken ------------------------------


def _unwritable(monkeypatch):
    from nodvard_deck_ext_scripts.standing import StandingApprovals

    def _fail(self, state):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(StandingApprovals, "_write", _fail)


@pytest.mark.asyncio
async def test_expiry_holds_and_is_logged_once_when_the_file_cannot_be_written(
    tmp_path, db_session, test_settings, monkeypatch
):
    """Herabgestufte Person, Datei nicht beschreibbar: Das Erloeschen steht trotzdem im
    Protokoll und gilt -- ohne doppelte Zeile, und spaeter wird der Eintrag nachgeloescht."""
    from nodvard_deck.models import Notification as NotificationRow
    from nodvard_deck.services import auth as auth_service
    from nodvard_deck_ext_scripts import _standing_approvals
    from nodvard_deck_ext_scripts.standing import StandingApprovals

    ctx, ext, _group, _members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    roles = await auth_service.ensure_builtin_roles(db_session)
    admin.roles = [roles["operator"]]
    await db_session.commit()
    commands = _capture_exec(monkeypatch, ctx)
    write = StandingApprovals._write
    _unwritable(monkeypatch)

    for _ in range(2):
        result = await _scheduled(ctx, ext)
        assert {r["status"] for r in result["results"]} == {"proposed"}
    assert commands == []
    # Fail closed: Die Datei liess sich nicht neu schreiben, also ist sie ganz geloescht -- auch nach
    # einem Neustart (Sperre im Speicher weg) gilt die Freigabe nicht wieder.
    assert _standing_approvals(ctx).get_raw("audit") is None, "Datei ist weg"
    assert _standing_approvals(ctx).get("audit") is None, "gilt nicht mehr"
    assert len(await _audit(db_session, "scripts.standing_approval.expired")) == 1
    notes = (
        await db_session.execute(select(NotificationRow).where(NotificationRow.correlation_id == "scripts-standing:audit"))
    ).scalars().all()
    assert len(notes) == 1

    monkeypatch.setattr(StandingApprovals, "_write", write)
    await _scheduled(ctx, ext)
    assert _standing_approvals(ctx).get_raw("audit") is None
    assert len(await _audit(db_session, "scripts.standing_approval.expired")) == 1


@pytest.mark.asyncio
async def test_reverting_a_change_does_not_revive_an_approval_that_could_not_be_deleted(
    tmp_path, db_session, test_settings, monkeypatch
):
    ctx, ext, _group, _members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    commands = _capture_exec(monkeypatch, ctx)
    _unwritable(monkeypatch)
    script = ext._repo.get("audit")
    ext._repo.save(script.meta, "echo anders\n", commit_message="geaendert")
    await _scheduled(ctx, ext)
    ext._repo.save(script.meta, script.content, commit_message="zurueck")

    result = await _scheduled(ctx, ext)

    assert {r["status"] for r in result["results"]} == {"proposed"}
    assert commands == []


@pytest.mark.asyncio
async def test_failed_log_keeps_the_entry_and_the_next_run_catches_up(tmp_path, db_session, test_settings, monkeypatch):
    from nodvard_deck_ext_scripts import _standing_approvals

    ctx, ext, _group, _members = await _fleet(tmp_path, db_session, test_settings)
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    commands = _capture_exec(monkeypatch, ctx)
    script = ext._repo.get("audit")
    ext._repo.save(script.meta, "echo anders\n", commit_message="geaendert")
    original = ctx.audit.log
    failures = {"n": 0}

    async def _flaky(**kwargs):
        if kwargs.get("action") == "scripts.standing_approval.expired" and failures["n"] == 0:
            failures["n"] += 1
            raise RuntimeError("database is locked")
        return await original(**kwargs)

    monkeypatch.setattr(ctx.audit, "log", _flaky)

    first = await _scheduled(ctx, ext)
    assert {r["status"] for r in first["results"]} == {"proposed"}, "der Lauf bricht nicht ab"
    assert _standing_approvals(ctx).get_raw("audit") is not None
    assert await _audit(db_session, "scripts.standing_approval.expired") == []

    await _scheduled(ctx, ext)
    assert commands == []
    assert len(await _audit(db_session, "scripts.standing_approval.expired")) == 1
    assert _standing_approvals(ctx).get_raw("audit") is None


# --- Gate und Erweiterungs-Berechtigung -----------------------------------------------


def _standing_request(user_id: str, *, actor: Actor) -> ActionRequest:
    return ActionRequest(
        action_type="script.run", payload={"command": "true"}, host_ref=None, risk=Risk.HIGH,
        proposed_by=actor, reason="Test",
        standing_approval=StandingApproval(granted_by_user_id=user_id, granted_at=datetime.now(timezone.utc)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "active", "actor", "allowed"),
    [
        ("admin", True, Actor.extension("scripts"), True),
        ("admin", True, Actor(type=ActorType.AI, id="modell"), False),
        ("admin", False, Actor.extension("scripts"), False),
        ("operator", True, Actor.extension("scripts"), False),
        ("viewer", True, Actor.extension("scripts"), False),
    ],
)
async def test_gate_checks_the_person_behind_a_standing_approval(db_session, role, active, actor, allowed):
    from nodvard_deck.core import gate

    user = await _user(db_session, username=f"u-{role}-{active}", role=role, active=active)
    decision = await gate.propose(
        db_session, ext_id="scripts", request=_standing_request(user.id, actor=actor), command_field="command",
    )
    row = await db_session.get(Action, decision.action_id)
    if allowed:
        assert decision.rule == "standing_approval"
        assert row.approved_by_user_id == user.id
    else:
        assert decision.status.value == "proposed"
        assert row.gate_decision["rule"] == "autonomy:propose"
        assert decision.detail and "standing_approval_rejected" in row.gate_decision


@pytest.mark.asyncio
async def test_gate_explains_a_missing_approve_permission_in_plain_words(db_session, monkeypatch):
    from nodvard_deck.core import gate
    from nodvard_deck.services import auth as auth_service

    user = await _user(db_session, username="chefin", role="admin")
    real = auth_service.user_has_permission
    monkeypatch.setattr(
        auth_service, "user_has_permission", lambda u, p: p != "actions.approve:high" and real(u, p)
    )
    decision = await gate.propose(
        db_session, ext_id="scripts", request=_standing_request(user.id, actor=Actor.extension("scripts")),
        command_field="command",
    )
    row = await db_session.get(Action, decision.action_id)
    assert decision.detail == (
        "Die Person, die die Dauerfreigabe erteilt hat, hat keine Berechtigung mehr für Freigaben mit hohem Risiko."
    )
    assert row.gate_decision["standing_approval_rejected"]["permission"] == "actions.approve:high"
    assert "actions." not in row.reason


@pytest.mark.asyncio
async def test_rejected_standing_approval_under_full_autonomy_does_not_ask_for_a_click(db_session):
    """Laeuft die Aktion ueber die Automatik ohnehin an, steht kein "Bitte selbst freigeben" daran."""
    from nodvard_deck.core import gate
    from nodvard_deck.services import settings as settings_service

    operator = await _user(db_session, username="bediener", role="operator")
    await settings_service.set_global(db_session, "autonomy.mode", "full")
    await settings_service.set_global(db_session, "autonomy.max_risk", "high")
    await db_session.commit()

    decision = await gate.propose(
        db_session, ext_id="scripts", request=_standing_request(operator.id, actor=Actor.extension("scripts")),
        command_field="command",
    )
    row = await db_session.get(Action, decision.action_id)
    assert row.gate_decision["rule"] == "autonomy:full"
    assert "standing_approval_rejected" in row.gate_decision
    assert "gilt nicht" in row.reason and "Bitte selbst freigeben" not in row.reason


@pytest.mark.asyncio
async def test_unknown_user_in_a_standing_approval_is_rejected(db_session):
    from nodvard_deck.core import gate

    decision = await gate.propose(
        db_session, ext_id="scripts", request=_standing_request("gibt-es-nicht", actor=Actor.extension("scripts")),
        command_field="command",
    )
    assert decision.status.value == "proposed"


@pytest.mark.asyncio
async def test_extension_without_the_permission_cannot_send_a_standing_approval(tmp_path, db_session, test_settings):
    without = [p for p in _PERMISSIONS if p != "actions.standing_approval"]
    _runtime, ctx, _ext = await _setup_scripts_extension(tmp_path, test_settings, without)
    admin = await _user(db_session, username="chefin", role="admin")
    with pytest.raises(PermissionDenied):
        await ctx.actions.propose(_standing_request(admin.id, actor=Actor.extension("scripts")))


# --- Ueber die echte HTTP-API ---------------------------------------------------------


async def _login_as(client, db_session, username, role):
    await _user(db_session, username=username, role=role)
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": "whatever123"})
    assert r.status_code == 200, r.text
    return _auth_header(r.json()["access_token"])


def _grant_body(script_out):
    """Was die Seite beim Erteilen mitschickt: die beiden angezeigten Fingerabdruecke."""
    return {"fingerprint": script_out["fingerprint"], "targets_fingerprint": script_out.get("targets_fingerprint") or ""}


async def _http_script(client, headers, host_id, **extra):
    body = {
        "name": "Audit", "content": "echo audit\n", "schedule": "0 1 * * *", "enabled": True,
        "target": {"kind": "host", "host_id": host_id}, **extra,
    }
    saved = await client.put("/api/v1/ext/scripts/scripts/audit", json=body, headers=headers)
    assert saved.status_code == 200, saved.text
    return body, saved.json()


@pytest.mark.asyncio
async def test_owner_grants_and_revokes_over_http_and_everything_is_logged(client, db_session, test_settings):
    owner = _auth_header(await _enable_scripts(client, db_session, test_settings))
    host = await _host_with_credential(db_session, test_settings, "pve1")
    await db_session.commit()
    _body, saved = await _http_script(client, owner, host.id)
    assert saved["standing_approval"] is None
    url = "/api/v1/ext/scripts/scripts/audit/standing-approval"

    stale = await client.post(url, json={**_grant_body(saved), "fingerprint": "alt"}, headers=owner)
    assert stale.status_code == 409

    granted = await client.post(url, json=_grant_body(saved), headers=owner)
    assert granted.status_code == 200, granted.text
    standing = granted.json()["standing_approval"]
    assert standing["active"] is True
    assert standing["granted_by_label"] == "owner1"
    assert standing["hosts"] == [{"id": host.id, "name": "pve1", "account": "root", "address": "10.0.0.1", "port": 22}]
    listed = (await client.get("/api/v1/ext/scripts/scripts", headers=owner)).json()
    assert listed[0]["standing_approval"]["active"] is True

    assert (await client.delete(url, headers=owner)).status_code == 204
    assert (await client.delete(url, headers=owner)).status_code == 404
    assert (await client.get("/api/v1/ext/scripts/scripts/audit", headers=owner)).json()["standing_approval"] is None

    granted_log = await _audit(db_session, "scripts.standing_approval.granted")
    revoked_log = await _audit(db_session, "scripts.standing_approval.revoked")
    assert len(granted_log) == 1 and granted_log[0].actor_type == "user"
    assert granted_log[0].detail["hosts"][0]["account"] == "root"
    assert len(revoked_log) == 1 and revoked_log[0].actor_type == "user"


@pytest.mark.asyncio
async def test_grant_shows_the_targets_first_and_refuses_when_they_changed(client, db_session, test_settings):
    owner = _auth_header(await _enable_scripts(client, db_session, test_settings))
    host = await _host_with_credential(db_session, test_settings, "pve1")
    await db_session.commit()
    _body, saved = await _http_script(client, owner, host.id)
    assert saved["standing_preview"] == [{"id": host.id, "name": "pve1", "account": "root", "address": "10.0.0.1", "port": 22}]
    # Zwischen Laden der Seite und Klick: anderes Konto auf dem Server.
    await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password", username="deck", port=22, secret_value="pw2",
    )
    await db_session.commit()
    url = "/api/v1/ext/scripts/scripts/audit/standing-approval"

    stale = await client.post(url, json=_grant_body(saved), headers=owner)
    assert stale.status_code == 409 and "Zielserver" in stale.json()["detail"]
    fresh = (await client.get("/api/v1/ext/scripts/scripts/audit", headers=owner)).json()
    assert fresh["standing_preview"][0]["account"] == "deck"
    granted = await client.post(url, json=_grant_body(fresh), headers=owner)
    assert granted.status_code == 200, granted.text
    assert granted.json()["standing_approval"]["hosts"][0]["account"] == "deck"


@pytest.mark.asyncio
async def test_grant_refuses_when_the_ssh_port_changed_since_the_page_was_loaded(client, db_session, test_settings):
    owner = _auth_header(await _enable_scripts(client, db_session, test_settings))
    host = await _host_with_credential(db_session, test_settings, "pve1")
    await db_session.commit()
    _body, saved = await _http_script(client, owner, host.id)
    await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password", username="root", port=2222, secret_value="pw2",
    )
    await db_session.commit()
    url = "/api/v1/ext/scripts/scripts/audit/standing-approval"

    stale = await client.post(url, json=_grant_body(saved), headers=owner)
    assert stale.status_code == 409 and "Zielserver" in stale.json()["detail"]
    fresh = (await client.get("/api/v1/ext/scripts/scripts/audit", headers=owner)).json()
    assert fresh["standing_preview"][0]["port"] == 2222
    granted = await client.post(url, json=_grant_body(fresh), headers=owner)
    assert granted.status_code == 200, granted.text
    assert granted.json()["standing_approval"]["hosts"][0]["port"] == 2222
    granted_log = await _audit(db_session, "scripts.standing_approval.granted")
    assert granted_log[0].detail["hosts"][0]["port"] == 2222


@pytest.mark.asyncio
async def test_page_shows_the_approval_no_longer_holds_when_the_person_lost_the_rights(client, db_session, test_settings):
    from nodvard_deck.services import auth as auth_service

    owner = _auth_header(await _enable_scripts(client, db_session, test_settings))
    host = await _host_with_credential(db_session, test_settings, "pve1")
    await db_session.commit()
    _body, saved = await _http_script(client, owner, host.id)
    admin_headers = await _login_as(client, db_session, "chefin", "admin")
    url = "/api/v1/ext/scripts/scripts/audit"
    assert (await client.post(f"{url}/standing-approval", json=_grant_body(saved), headers=admin_headers)).status_code == 200
    assert (await client.get(url, headers=owner)).json()["standing_approval"]["active"] is True

    from nodvard_deck.models import User

    chefin = (await db_session.execute(select(User).where(User.username == "chefin"))).scalar_one()
    roles = await auth_service.ensure_builtin_roles(db_session)
    chefin.roles = [roles["operator"]]
    await db_session.commit()

    standing = (await client.get(url, headers=owner)).json()["standing_approval"]
    assert standing["active"] is False
    assert "kein Owner oder Admin mehr" in standing["problem"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["operator", "viewer"])
async def test_operator_and_viewer_cannot_grant_or_revoke(client, db_session, test_settings, role):
    owner = _auth_header(await _enable_scripts(client, db_session, test_settings))
    host = await _host_with_credential(db_session, test_settings, "pve1")
    await db_session.commit()
    _body, saved = await _http_script(client, owner, host.id)
    other = await _login_as(client, db_session, f"nutzer-{role}", role)
    url = "/api/v1/ext/scripts/scripts/audit/standing-approval"

    assert (await client.post(url, json=_grant_body(saved), headers=other)).status_code == 403
    assert (await client.post(url, json=_grant_body(saved), headers=owner)).status_code == 200
    assert (await client.delete(url, headers=other)).status_code == 403
    assert (await client.get("/api/v1/ext/scripts/scripts/audit", headers=owner)).json()["standing_approval"]["active"]


@pytest.mark.asyncio
async def test_saving_a_change_expires_the_approval_renaming_does_not(client, db_session, test_settings):
    owner = _auth_header(await _enable_scripts(client, db_session, test_settings))
    host = await _host_with_credential(db_session, test_settings, "pve1")
    await db_session.commit()
    body, saved = await _http_script(client, owner, host.id)
    url = "/api/v1/ext/scripts/scripts/audit"
    assert (await client.post(f"{url}/standing-approval", json=_grant_body(saved), headers=owner)).status_code == 200

    renamed = await client.put(url, json={**body, "name": "Neuer Name", "description": "x"}, headers=owner)
    assert renamed.json()["standing_approval"]["active"] is True

    operator = await _login_as(client, db_session, "bediener", "operator")
    changed = await client.put(url, json={**body, "schedule": "0 2 * * *"}, headers=operator)
    assert changed.status_code == 200, changed.text
    assert changed.json()["standing_approval"] is None
    expired = await _audit(db_session, "scripts.standing_approval.expired")
    assert len(expired) == 1
    assert expired[0].actor_type == "user" and "bediener" in expired[0].reason


@pytest.mark.asyncio
async def test_no_standing_approval_without_schedule_or_for_an_automatic_draft(client, db_session, test_settings):
    """Nur ein von Menschen angelegter Zeitplan kann eine Dauerfreigabe bekommen: ein
    automatisch angelegter Entwurf hat keinen Zeitplan und ist aus."""
    owner = _auth_header(await _enable_scripts(client, db_session, test_settings))
    host = await _host_with_credential(db_session, test_settings, "pve1")
    await db_session.commit()
    _body, saved = await _http_script(client, owner, host.id, schedule=None)
    refused = await client.post(
        "/api/v1/ext/scripts/scripts/audit/standing-approval", json=_grant_body(saved), headers=owner
    )
    assert refused.status_code == 409

    from nodvard_deck_ext_scripts.repo import ScriptMeta

    ext = get_extension_runtime().loaded["scripts"].instance
    ext._repo.save(
        ScriptMeta(id="auto-1234567890", name="Automatischer Entwurf: x", target={"kind": "host", "host_id": host.id},
                   schedule=None, enabled=False),
        "#!/bin/sh\nx\n",
        commit_message="scripts: automatischer Entwurf",
    )
    draft = (await client.get("/api/v1/ext/scripts/scripts/auto-1234567890", headers=owner)).json()
    refused = await client.post(
        "/api/v1/ext/scripts/scripts/auto-1234567890/standing-approval",
        json=_grant_body(draft), headers=owner,
    )
    assert refused.status_code == 409


def test_unwritable_file_on_removal_deletes_all_approvals_so_a_restart_does_not_revive_them(tmp_path, monkeypatch):
    """Zurueckziehen, Datei nicht beschreibbar, danach Neustart (Sperre im Speicher ist weg):
    die Freigabe darf nicht wieder gelten."""
    from nodvard_deck_ext_scripts import standing as standing_mod
    from nodvard_deck_ext_scripts.standing import StandingApprovals, StandingRecord

    path = tmp_path / "standing-approvals.json"
    store = StandingApprovals(path)
    path.write_text(json.dumps({"audit": {"x": 1}, "andere": {"y": 2}}), encoding="utf-8")
    monkeypatch.setattr(StandingRecord, "from_json", staticmethod(lambda raw: raw))
    _unwritable(monkeypatch)

    assert store.remove("audit") == {"x": 1}

    standing_mod._BLOCKED.clear()  # wie nach einem Neustart
    assert not path.exists()
    assert StandingApprovals(path).get_raw("audit") is None
    assert StandingApprovals(path).get_raw("andere") is None
