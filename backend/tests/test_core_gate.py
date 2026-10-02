"""Das Aktions-Gate (docs/01-ARCHITECTURE.md §4) --
Sperrliste, Anti-Flapping, Autonomie-Entscheidung, Ausfuehrung, und dass Schritt 6
(Audit) nie uebersprungen wird, auch nicht bei einer Ablehnung.
"""

from __future__ import annotations

import pytest
from nodvard_sdk import Actor, ActionRequest, ActionResult, ActorType, DryRunReport, GateOutcome, Risk
from nodvard_sdk.capabilities import ActionExecutor
from sqlalchemy import select

from nodvard_deck.core import flap, gate
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import Action, AuditEntry
from nodvard_deck.services import settings as settings_service


@pytest.fixture(autouse=True)
def _reset_runtime():
    reset_extension_runtime()
    yield
    reset_extension_runtime()


class _FakeExecutor:
    action_types = frozenset({"shell.exec"})

    def __init__(self, *, succeed: bool = True, raise_error: bool = False) -> None:
        self.succeed = succeed
        self.raise_error = raise_error
        self.calls: list[ActionRequest] = []

    async def execute(self, req: ActionRequest) -> ActionResult:
        self.calls.append(req)
        if self.raise_error:
            raise RuntimeError("Verbindung mitten in der Ausfuehrung abgerissen")
        return ActionResult(success=self.succeed, exit_code=0 if self.succeed else 1, output="ok")

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        return None


def _register_executor(**kwargs) -> _FakeExecutor:
    executor = _FakeExecutor(**kwargs)
    get_extension_runtime().capabilities.provide("terminal", ActionExecutor, executor)
    return executor


def _request(*, action_type="shell.exec", command="uptime", risk=Risk.MEDIUM, host_ref="host-1") -> ActionRequest:
    return ActionRequest(
        action_type=action_type,
        payload={"command": command},
        host_ref=host_ref,
        risk=risk,
        proposed_by=Actor(type=ActorType.AI, id="test-model"),
        reason="Testvorschlag",
    )


async def _last_audit(session, action: str) -> AuditEntry:
    rows = (
        await session.execute(select(AuditEntry).where(AuditEntry.action == action).order_by(AuditEntry.ts.desc()))
    ).scalars().all()
    assert rows, f"keine Audit-Zeile mit action={action!r}"
    return rows[0]


@pytest.mark.asyncio
async def test_propose_denies_on_deny_pattern_and_still_writes_action_and_audit(db_session):
    decision = await gate.propose(
        db_session, ext_id="terminal", request=_request(command="rm -rf /"), command_field="command"
    )
    assert decision.outcome == GateOutcome.DENY
    assert decision.rule.startswith("deny_pattern:")

    row = await db_session.get(Action, decision.action_id)
    assert row.status == "denied"
    assert row.gate_decision["rule"] == decision.rule

    audit = await _last_audit(db_session, "action.denied")
    assert audit.outcome == "denied"
    assert audit.target_id == decision.action_id


@pytest.mark.asyncio
async def test_propose_without_command_field_skips_deny_pattern_check(db_session):
    """command_field=None (kein registriertes ActionSpec-Feld) -- nichts zu pruefen,
    kein falscher Alarm, kein Absturz."""
    decision = await gate.propose(
        db_session, ext_id="terminal", request=_request(command="rm -rf /"), command_field=None
    )
    assert decision.outcome == GateOutcome.REQUIRE_CONFIRMATION


@pytest.mark.asyncio
async def test_propose_denies_after_flap_limit_reached(db_session):
    for _ in range(flap.MAX_COUNT):
        await gate.propose(db_session, ext_id="terminal", request=_request(command="uptime"), command_field="command")

    decision = await gate.propose(
        db_session, ext_id="terminal", request=_request(command="uptime"), command_field="command"
    )
    assert decision.outcome == GateOutcome.DENY
    assert decision.rule == "flap_limit"


@pytest.mark.asyncio
async def test_propose_default_mode_requires_confirmation_and_sets_expiry(db_session):
    decision = await gate.propose(
        db_session, ext_id="terminal", request=_request(), command_field="command"
    )
    assert decision.outcome == GateOutcome.REQUIRE_CONFIRMATION
    assert decision.status.value == "proposed"
    assert decision.expires_at is not None

    row = await db_session.get(Action, decision.action_id)
    assert row.status == "proposed"
    assert row.expires_at is not None

    audit = await _last_audit(db_session, "action.proposed")
    assert audit.outcome == "proposed"


@pytest.mark.asyncio
async def test_propose_full_autonomy_within_risk_threshold_executes_immediately(db_session):
    executor = _register_executor(succeed=True)
    await settings_service.set_global(db_session, "autonomy.mode", "full")
    await settings_service.set_global(db_session, "autonomy.max_risk", "medium")

    decision = await gate.propose(
        db_session, ext_id="terminal", request=_request(risk=Risk.MEDIUM), command_field="command"
    )
    assert decision.outcome == GateOutcome.ALLOW
    assert decision.status.value == "succeeded"
    assert len(executor.calls) == 1

    row = await db_session.get(Action, decision.action_id)
    assert row.status == "succeeded"
    assert row.approved_at is not None
    assert row.finished_at is not None
    assert row.expires_at is None

    proposed_audit = await _last_audit(db_session, "action.approved")
    assert proposed_audit.outcome == "proposed"
    executed_audit = await _last_audit(db_session, "action.executed")
    assert executed_audit.outcome == "success"


@pytest.mark.asyncio
async def test_propose_full_autonomy_above_risk_threshold_still_requires_confirmation(db_session):
    _register_executor(succeed=True)
    await settings_service.set_global(db_session, "autonomy.mode", "full")
    await settings_service.set_global(db_session, "autonomy.max_risk", "low")

    decision = await gate.propose(
        db_session, ext_id="terminal", request=_request(risk=Risk.HIGH), command_field="command"
    )
    assert decision.outcome == GateOutcome.REQUIRE_CONFIRMATION


@pytest.mark.asyncio
async def test_execute_action_records_failure_when_executor_raises(db_session):
    executor = _register_executor(raise_error=True)
    await settings_service.set_global(db_session, "autonomy.mode", "full")
    await settings_service.set_global(db_session, "autonomy.max_risk", "high")

    decision = await gate.propose(
        db_session, ext_id="terminal", request=_request(risk=Risk.LOW), command_field="command"
    )
    assert decision.status.value == "failed"
    assert len(executor.calls) == 1

    row = await db_session.get(Action, decision.action_id)
    assert row.result["success"] is False
    assert "abgerissen" in row.result["error"]

    executed_audit = await _last_audit(db_session, "action.executed")
    assert executed_audit.outcome == "failure"


@pytest.mark.asyncio
async def test_execute_action_records_failure_when_no_executor_registered(db_session):
    decision = await gate.propose(
        db_session, ext_id="terminal", request=_request(action_type="unknown.action", risk=Risk.LOW),
        command_field=None,
    )
    # Default autonomy.mode=propose -> braucht approve(), um execute_action() zu triggern.
    approved, moved = await gate.approve(db_session, decision.action_id, user_id="user-1")
    assert moved is True
    assert approved.status == "failed"
    assert "Kein ActionExecutor" in approved.result["error"]


@pytest.mark.asyncio
async def test_approve_executes_exactly_once_even_on_concurrent_double_call(db_session):
    executor = _register_executor(succeed=True)
    decision = await gate.propose(
        db_session, ext_id="terminal", request=_request(), command_field="command"
    )

    first, first_moved = await gate.approve(db_session, decision.action_id, user_id="user-1")
    second, second_moved = await gate.approve(db_session, decision.action_id, user_id="user-2")

    assert first_moved is True
    assert first.status == "succeeded"
    assert len(executor.calls) == 1
    assert second_moved is False  # DIESER Aufruf hat nichts ausgeloest -> 409 fuer den API-Aufrufer
    assert second.status == "succeeded"  # unveraendert, kein zweiter Versuch
    assert second.approved_by_user_id == "user-1"  # der zweite Aufruf hat NICHT gewonnen


@pytest.mark.asyncio
async def test_approve_unknown_action_returns_none(db_session):
    result, moved = await gate.approve(db_session, "does-not-exist", user_id="user-1")
    assert result is None
    assert moved is False


@pytest.mark.asyncio
async def test_execute_action_publishes_action_executed_event(db_session):
    """Grundlage fuer die scripts-Extension: docs/02-EXTENSION-API.md §6 verlangt
    `ctx.events.subscribe("action.executed")`, damit eine Extension auf wiederholte,
    identische Ausfuehrungen reagieren kann, OHNE die Extension zu kennen, die sie
    vorgeschlagen hat -- "die beiden Extensions reden ueber den Event-Bus, nicht
    miteinander". Vor diesem Fix schrieb execute_action() nur den Audit-Eintrag,
    publizierte aber NICHTS auf den Event-Bus -- ein Abonnent haette nie etwas gesehen."""
    from nodvard_deck.core.events import get_event_bus
    from nodvard_sdk.types import Event

    received: list[Event] = []

    async def _handler(event: Event) -> None:
        received.append(event)

    get_event_bus().subscribe("action.*", _handler)
    try:
        executor = _register_executor(succeed=True)
        await settings_service.set_global(db_session, "autonomy.mode", "full")
        await settings_service.set_global(db_session, "autonomy.max_risk", "high")

        decision = await gate.propose(
            db_session, ext_id="terminal", request=_request(risk=Risk.LOW), command_field="command"
        )
        assert len(executor.calls) == 1

        executed = [e for e in received if e.name == "action.executed"]
        assert len(executed) == 1
        assert executed[0].payload["action_id"] == decision.action_id
        assert executed[0].payload["action_type"] == "shell.exec"
        assert executed[0].payload["outcome"] == "success"
    finally:
        get_event_bus().unsubscribe("action.*", _handler)


@pytest.mark.asyncio
async def test_execute_action_does_not_deadlock_when_executor_opens_nested_session(tmp_path):
    """Live gefunden beim Boot-Test der proxmox-Extension: `ProxmoxActionExecutor.
    execute()` ruft `ctx.vault_use()` auf, das (wie `ctx.audit.log()` etc., siehe
    `ext/context.py`) IMMER eine eigene, unabhaengige `session_scope()` oeffnet. Der
    echte Aufrufer (`api/v1/actions.py::approve_action()`) haelt seine Session ueber
    den GESAMTEN Request offen (`get_session()` committet erst am Ende) -- hier durch
    eine echte Datei-DB mit einer echten, offen gehaltenen Session nachgebildet. Die
    In-Memory-StaticPool-Fixture (`db_session`) teilt sich EINE physische Verbindung
    zwischen "unabhaengigen" Sessions und haette das Problem nie zeigen koennen (siehe
    dasselbe Argument in `test_core_vault.py::test_vault_use_audit_survives_caller_
    session_rollback`). Ohne den Fix in `core.gate.execute_action()` (Commit vor dem
    Aufruf in Extension-Code) blockiert SQLites Ein-Schreiber-Regel (D-02) die
    verschachtelte Session, bis `busy_timeout` (5s) ablaeuft und `execute()` mit
    "database is locked" fehlschlaegt -- ein echter Deadlock, kein Zufallstreffer."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck.config import Settings
    from nodvard_deck.db.session import create_engine_for, reset_engine_cache, session_scope, set_engine_for_testing
    from nodvard_deck.models import Base
    from nodvard_deck.services import audit as audit_service

    db_path = tmp_path / "gate-isolation.db"
    settings = Settings(database_url=f"sqlite+aiosqlite:///{db_path}")
    engine = create_engine_for(settings)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        set_engine_for_testing(engine)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        class _NestedSessionExecutor:
            action_types = frozenset({"vm.stop"})

            async def execute(self, req: ActionRequest) -> ActionResult:
                # Wie ctx.vault_use()/ctx.audit.log(): eine EIGENE, unabhaengige Session.
                async with session_scope() as inner_session:
                    await audit_service.log(
                        inner_session, actor_type="extension", actor_id="proxmox",
                        action="secret.used", outcome="success", detail={"label": "proxmox-token"},
                    )
                return ActionResult(success=True, output="ok")

            async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
                return None

        get_extension_runtime().capabilities.provide("proxmox", ActionExecutor, _NestedSessionExecutor())

        async with sessionmaker() as caller_session:
            decision = await gate.propose(
                caller_session, ext_id="core", request=_request(action_type="vm.stop", host_ref=None),
                command_field=None,
            )
            action, moved = await gate.approve(caller_session, decision.action_id, user_id="user-1")

        assert moved is True
        assert action.status == "succeeded", action.result

        async with sessionmaker() as verify_session:
            entries = (
                await verify_session.execute(select(AuditEntry).where(AuditEntry.action == "secret.used"))
            ).scalars().all()
        assert len(entries) == 1
    finally:
        await engine.dispose()
        reset_engine_cache()


@pytest.mark.asyncio
async def test_approve_expired_proposal_is_marked_expired_and_never_executed(db_session):
    executor = _register_executor(succeed=True)
    decision = await gate.propose(
        db_session, ext_id="terminal", request=_request(), command_field="command"
    )
    row = await db_session.get(Action, decision.action_id)
    from datetime import timedelta

    from nodvard_deck.db import utcnow

    row.expires_at = utcnow() - timedelta(seconds=1)
    await db_session.flush()

    result, moved = await gate.approve(db_session, decision.action_id, user_id="user-1")
    assert moved is False
    assert result.status == "expired"
    assert executor.calls == []


@pytest.mark.asyncio
async def test_reject_denies_with_reason_and_is_idempotent(db_session):
    decision = await gate.propose(
        db_session, ext_id="terminal", request=_request(), command_field="command"
    )
    result, moved = await gate.reject(db_session, decision.action_id, user_id="user-1", reason="Falscher Host.")
    assert moved is True
    assert result.status == "denied"
    assert result.gate_decision["user_reason"] == "Falscher Host."

    audit = await _last_audit(db_session, "action.denied")
    assert audit.reason == "Falscher Host."

    # Ein zweiter Reject-Versuch aendert nichts mehr (schon entschieden).
    second, second_moved = await gate.reject(db_session, decision.action_id, user_id="user-2", reason="Zu spaet.")
    assert second_moved is False
    assert second.status == "denied"
    assert second.approved_by_user_id == "user-1"


@pytest.mark.asyncio
async def test_dismiss_sets_dismissed_status(db_session):
    decision = await gate.propose(
        db_session, ext_id="terminal", request=_request(), command_field="command"
    )
    result, moved = await gate.dismiss(db_session, decision.action_id, user_id="user-1")
    assert moved is True
    assert result.status == "dismissed"


@pytest.mark.asyncio
async def test_find_executor_matches_by_action_type():
    assert await gate.find_executor("shell.exec") is None
    executor = _register_executor()
    found = await gate.find_executor("shell.exec")
    assert found is executor
    assert await gate.find_executor("something.else") is None


async def _file_engine(tmp_path, name: str):
    """Echte Datei-SQLite mit echten, unabhaengigen Verbindungen -- die
    StaticPool-Fixture haette Sperr-Probleme nie zeigen koennen (siehe
    test_execute_action_does_not_deadlock_when_executor_opens_nested_session)."""
    from nodvard_deck.config import Settings
    from nodvard_deck.db.session import create_engine_for, set_engine_for_testing
    from nodvard_deck.models import Base

    engine = create_engine_for(Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / name}"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    set_engine_for_testing(engine)
    return engine


@pytest.mark.asyncio
async def test_action_executed_subscriber_writing_via_own_session_is_not_blocked(tmp_path):
    """Ergebnis-Flush und Audit liefen in der noch offenen
    Schreibtransaktion des Aufrufers, der Event-Bus wurde DAVOR benachrichtigt. Ein
    Abonnent von 'action.executed', der ueber eine eigene Session schreibt (scripts:
    _on_action_executed -> ctx.notify.send), wartete busy_timeout (5 s) ab und
    scheiterte mit 'database is locked' -- die Meldung ging verloren. Ausserdem wurde
    `executed_at` nie gesetzt."""
    import time

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck.core.events import get_event_bus
    from nodvard_deck.db.session import reset_engine_cache, session_scope
    from nodvard_deck.services import audit as audit_service

    engine = await _file_engine(tmp_path, "gate-events.db")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    seen_executed_at: list = []

    class _Executor:
        action_types = frozenset({"vm.stop"})

        async def execute(self, req: ActionRequest) -> ActionResult:
            # Liest ueber eine EIGENE Verbindung: executed_at muss schon gespeichert sein.
            async with sessionmaker() as probe:
                row = (
                    await probe.execute(select(Action).where(Action.action_type == "vm.stop"))
                ).scalars().one()
                seen_executed_at.append(row.executed_at)
            return ActionResult(success=True, output="ok")

        async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
            return None

    async def _subscriber(event) -> None:
        async with session_scope() as own_session:
            await audit_service.log(
                own_session, actor_type="extension", actor_id="scripts",
                action="subscriber.wrote", outcome="success",
            )

    get_extension_runtime().capabilities.provide("proxmox", ActionExecutor, _Executor())
    get_event_bus().subscribe("action.executed", _subscriber)
    try:
        async with sessionmaker() as caller_session:
            decision = await gate.propose(
                caller_session, ext_id="core", request=_request(action_type="vm.stop", host_ref=None),
                command_field=None,
            )
            started = time.monotonic()
            _, moved = await gate.approve(caller_session, decision.action_id, user_id="user-1")
            elapsed = time.monotonic() - started
            # Der Aufrufer bricht danach ab (z. B. Fehler beim Serialisieren der
            # Antwort) -- das Ergebnis muss trotzdem gespeichert sein.
            await caller_session.rollback()

        assert moved is True
        assert elapsed < 3, f"approve hat {elapsed:.1f} s auf die Schreibsperre gewartet"
        assert seen_executed_at and seen_executed_at[0] is not None

        async with sessionmaker() as verify:
            stored = await verify.get(Action, decision.action_id)
            assert stored.status == "succeeded"
            assert stored.executed_at is not None
            assert stored.finished_at is not None and stored.finished_at >= stored.executed_at
            written = (
                await verify.execute(select(AuditEntry).where(AuditEntry.action == "subscriber.wrote"))
            ).scalars().all()
            assert len(written) == 1
            executed_audit = (
                await verify.execute(select(AuditEntry).where(AuditEntry.action == "action.executed"))
            ).scalars().one()
            assert executed_audit.outcome == "success"
    finally:
        get_event_bus().unsubscribe("action.executed", _subscriber)
        await engine.dispose()
        reset_engine_cache()


async def _add_action(session, *, status: str) -> Action:
    row = Action(
        ext_id="nexus-soc", action_type="nexus_soc.upgrade", host_id=None, payload={}, risk="medium",
        status=status, proposed_by_type="user", proposed_by_id="user-1", reason="Test",
        gate_decision={"rule": "autonomy:propose"}, correlation_id=f"corr-{status}",
    )
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio
async def test_fail_interrupted_on_boot_marks_only_executing_actions_failed(db_session):
    """Startet der Container waehrend einer langen Aktion neu (Update bis
    45 min, Deploy mehrmals taeglich), blieb die Aktion fuer immer auf 'Laeuft'."""
    executing = await _add_action(db_session, status="executing")
    proposed = await _add_action(db_session, status="proposed")
    succeeded = await _add_action(db_session, status="succeeded")

    count = await gate.fail_interrupted_on_boot(db_session)

    assert count == 1
    await db_session.refresh(executing)
    assert executing.status == "failed"
    assert executing.result == {"success": False, "error": gate.INTERRUPTED_ERROR}
    assert executing.finished_at is not None
    await db_session.refresh(proposed)
    await db_session.refresh(succeeded)
    assert proposed.status == "proposed"
    assert succeeded.status == "succeeded"

    audit = await _last_audit(db_session, "action.executed")
    assert audit.target_id == executing.id
    assert audit.outcome == "failure"
    # Das Protokoll nennt den festen Grund des Gates (nicht als Text vom Server).
    assert audit.detail["result"] == {"success": False, "gate_error": gate.INTERRUPTED_ERROR}

    # Ein zweiter Lauf findet nichts mehr.
    assert await gate.fail_interrupted_on_boot(db_session) == 0


@pytest.mark.asyncio
async def test_lifespan_fails_actions_interrupted_by_a_restart(tmp_path, monkeypatch):
    """Der echte Start (main.py-Lifespan) muss die Aufraeumfunktion auch aufrufen."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck import config
    from nodvard_deck.core.events import reset_event_bus
    from nodvard_deck.core.metrics_history import reset_metrics_collector
    from nodvard_deck.core.scheduler import reset_scheduler_service
    from nodvard_deck.db.session import reset_engine_cache
    from nodvard_deck.main import app, lifespan

    settings = config.Settings(
        env="dev",
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'boot.db'}",
        master_key_path=tmp_path / "master.key",
        vault_keyring_path=tmp_path / "vault_keyring.json",
        jwt_secret_path=tmp_path / "jwt_secret.key",
        extensions_dir=tmp_path / "extensions",
        ext_data_dir=tmp_path / "ext-data",
        metrics_interval_s=0,
    )
    monkeypatch.setattr(config, "_settings", settings)
    engine = await _file_engine(tmp_path, "boot.db")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as setup:
        row = await _add_action(setup, status="executing")
        await setup.commit()

    original_routes = list(app.router.routes)
    reset_metrics_collector()
    try:
        async with lifespan(app):
            pass
        async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
            stored = await verify.get(Action, row.id)
            assert stored.status == "failed"
            assert stored.result["error"] == gate.INTERRUPTED_ERROR
    finally:
        app.router.routes[:] = original_routes
        await engine.dispose()
        reset_engine_cache()
        reset_scheduler_service()
        reset_metrics_collector()
        reset_event_bus()


# ---------------------------------------------------------------------------
# Ausfuehrung im Hintergrund statt in der HTTP-Anfrage
# ---------------------------------------------------------------------------


class _BlockingExecutor:
    """Laeuft, bis der Test `release` setzt -- wie ein langes Update oder Skript."""

    action_types = frozenset({"vm.stop"})

    def __init__(self, *, fail: bool = False) -> None:
        import asyncio

        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = fail

    async def execute(self, req: ActionRequest) -> ActionResult:
        self.started.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("SSH-Verbindung abgerissen")
        return ActionResult(success=True, output="fertig")

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        return None


async def _stored(sessionmaker, action_id: str) -> Action:
    async with sessionmaker() as verify:
        return await verify.get(Action, action_id)


async def _audit_result(sessionmaker, action_id: str) -> dict:
    """Das Ergebnis, wie es in der Audit-Zeile 'action.executed' der Aktion steht."""
    async with sessionmaker() as verify:
        entry = (
            await verify.execute(
                select(AuditEntry).where(AuditEntry.action == "action.executed", AuditEntry.target_id == action_id)
            )
        ).scalars().one()
        return entry.detail["result"]


@pytest.mark.asyncio
async def test_approve_with_wait_zero_runs_in_background_without_holding_a_lock(tmp_path):
    """Die Freigabe kommt sofort mit 'executing' zurueck. Waehrend die Aktion laeuft,
    kann eine andere Verbindung schreiben (keine offene Transaktion), und wer
    'action.executed' abonniert, liest schon den gespeicherten Endstatus."""
    import asyncio
    import time

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck.core.events import get_event_bus
    from nodvard_deck.db.session import reset_engine_cache, session_scope
    from nodvard_deck.services import audit as audit_service

    engine = await _file_engine(tmp_path, "gate-background.db")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    executor = _BlockingExecutor()
    seen_in_subscriber: list[str] = []

    async def _subscriber(event) -> None:
        seen_in_subscriber.append((await _stored(sessionmaker, event.payload["action_id"])).status)

    get_extension_runtime().capabilities.provide("proxmox", ActionExecutor, executor)
    get_event_bus().subscribe("action.executed", _subscriber)
    try:
        async with sessionmaker() as caller_session:
            decision = await gate.propose(
                caller_session, ext_id="core", request=_request(action_type="vm.stop", host_ref=None),
                command_field=None,
            )
            action, moved = await gate.approve(caller_session, decision.action_id, user_id="user-1", wait_s=0)
        assert moved is True
        assert action.status == "executing"

        await asyncio.wait_for(executor.started.wait(), timeout=5)
        assert gate.is_running(decision.action_id)
        stored = await _stored(sessionmaker, decision.action_id)
        assert stored.status == "executing"
        assert stored.executed_at is not None

        started = time.monotonic()
        async with session_scope() as other:
            await audit_service.log(other, actor_type="user", actor_id="user-2", action="other.write", outcome="success")
        assert time.monotonic() - started < 1, "eine laufende Aktion haelt die Schreibsperre"

        executor.release.set()
        assert await gate.wait_for_action(decision.action_id, 5) is True

        stored = await _stored(sessionmaker, decision.action_id)
        assert stored.status == "succeeded"
        assert stored.result["output"] == "fertig"
        assert stored.finished_at is not None and stored.finished_at >= stored.executed_at
        assert seen_in_subscriber == ["succeeded"]
        async with sessionmaker() as verify:
            executed = (
                await verify.execute(select(AuditEntry).where(AuditEntry.action == "action.executed"))
            ).scalars().one()
        assert executed.outcome == "success"
        assert not gate.is_running(decision.action_id)
    finally:
        get_event_bus().unsubscribe("action.executed", _subscriber)
        await gate.shutdown_running()
        await engine.dispose()
        reset_engine_cache()


@pytest.mark.asyncio
async def test_background_executor_error_ends_in_failed(tmp_path):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck.db.session import reset_engine_cache

    engine = await _file_engine(tmp_path, "gate-background-fail.db")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    executor = _BlockingExecutor(fail=True)
    get_extension_runtime().capabilities.provide("proxmox", ActionExecutor, executor)
    try:
        async with sessionmaker() as caller_session:
            decision = await gate.propose(
                caller_session, ext_id="core", request=_request(action_type="vm.stop", host_ref=None),
                command_field=None,
            )
            action, _ = await gate.approve(caller_session, decision.action_id, user_id="user-1", wait_s=0)
        assert action.status == "executing"
        await asyncio.wait_for(executor.started.wait(), timeout=5)
        executor.release.set()
        assert await gate.wait_for_action(decision.action_id, 5) is True

        stored = await _stored(sessionmaker, decision.action_id)
        assert stored.status == "failed"
        assert "abgerissen" in stored.result["error"]
        assert stored.finished_at is not None
    finally:
        await gate.shutdown_running()
        await engine.dispose()
        reset_engine_cache()


@pytest.mark.asyncio
async def test_background_error_outside_the_executor_still_ends_in_failed(db_session, monkeypatch):
    """Auch ein Fehler ausserhalb des Executors (hier: beim Suchen des Executors)
    darf die Aktion nie auf 'executing' haengen lassen."""

    async def _broken(action_type: str):
        raise RuntimeError("Laufzeit kaputt")

    monkeypatch.setattr(gate, "find_executor", _broken)
    decision = await gate.propose(db_session, ext_id="terminal", request=_request(), command_field="command")
    action, moved = await gate.approve(db_session, decision.action_id, user_id="user-1")
    assert moved is True
    assert action.status == "failed"
    assert action.result["error"] == "Laufzeit kaputt"
    executed = await _last_audit(db_session, "action.executed")
    assert executed.outcome == "failure"


@pytest.mark.asyncio
async def test_shutdown_running_marks_running_actions_as_cancelled(tmp_path):
    """Beim Beenden des Dashboards: laufende Aktionen vermerken sich als abgebrochen,
    statt bis zum naechsten Start auf 'executing' zu stehen."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck.db.session import reset_engine_cache

    engine = await _file_engine(tmp_path, "gate-shutdown.db")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    executor = _BlockingExecutor()
    get_extension_runtime().capabilities.provide("proxmox", ActionExecutor, executor)
    try:
        async with sessionmaker() as caller_session:
            decision = await gate.propose(
                caller_session, ext_id="core", request=_request(action_type="vm.stop", host_ref=None),
                command_field=None,
            )
            await gate.approve(caller_session, decision.action_id, user_id="user-1", wait_s=0)
        await asyncio.wait_for(executor.started.wait(), timeout=5)

        await gate.shutdown_running()

        stored = await _stored(sessionmaker, decision.action_id)
        assert stored.status == "failed"
        assert stored.result["error"] == gate.CANCELLED_ERROR
        assert (await _audit_result(sessionmaker, decision.action_id)) == {"success": False, "gate_error": gate.CANCELLED_ERROR}
        assert not gate.is_running(decision.action_id)
    finally:
        await gate.shutdown_running()
        await engine.dispose()
        reset_engine_cache()


@pytest.mark.asyncio
async def test_record_outcome_retries_when_the_database_is_locked(db_session, monkeypatch):
    """Niemand wartet mehr in der Anfrage -- ist SQLite beim Speichern gerade
    gesperrt, versucht es der Hintergrund-Task selbst noch einmal."""
    from sqlalchemy.exc import OperationalError

    from nodvard_deck.services import audit as audit_service

    _register_executor(succeed=True)
    real_log = audit_service.log
    failures = {"left": 1}

    async def _flaky_log(session, **kwargs):
        if kwargs.get("action") == "action.executed" and failures["left"]:
            failures["left"] -= 1
            raise OperationalError("INSERT", {}, Exception("database is locked"))
        return await real_log(session, **kwargs)

    monkeypatch.setattr(audit_service, "log", _flaky_log)
    monkeypatch.setattr(gate, "_RECORD_RETRY_DELAYS_S", (0.01,))

    decision = await gate.propose(db_session, ext_id="terminal", request=_request(), command_field="command")
    action, _ = await gate.approve(db_session, decision.action_id, user_id="user-1")
    assert failures["left"] == 0
    assert action.status == "succeeded"
    executed = await _last_audit(db_session, "action.executed")
    assert executed.outcome == "success"


@pytest.mark.asyncio
async def test_propose_full_autonomy_with_wait_zero_returns_executing(tmp_path):
    """Vollautonomie (POST /hosts/{id}/actions/{type}): die sofort freigegebene Aktion
    laeuft ebenfalls im Hintergrund, die Entscheidung meldet 'executing'."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck.db.session import reset_engine_cache

    engine = await _file_engine(tmp_path, "gate-autonomy.db")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    executor = _BlockingExecutor()
    get_extension_runtime().capabilities.provide("proxmox", ActionExecutor, executor)
    try:
        async with sessionmaker() as caller_session:
            await settings_service.set_global(caller_session, "autonomy.mode", "full")
            await settings_service.set_global(caller_session, "autonomy.max_risk", "medium")
            decision = await gate.propose(
                caller_session, ext_id="core", request=_request(action_type="vm.stop", host_ref=None),
                command_field=None, wait_s=0,
            )
        assert decision.outcome == GateOutcome.ALLOW
        assert decision.status.value == "executing"

        await asyncio.wait_for(executor.started.wait(), timeout=5)
        executor.release.set()
        assert await gate.wait_for_action(decision.action_id, 5) is True
        assert (await _stored(sessionmaker, decision.action_id)).status == "succeeded"
    finally:
        await gate.shutdown_running()
        await engine.dispose()
        reset_engine_cache()


@pytest.mark.asyncio
async def test_action_keeps_running_when_the_waiting_caller_is_cancelled(tmp_path):
    """ctx.actions.propose() wartet bis zum Ende. Wird der Aufrufer abgebrochen (z. B.
    ein abgebrochener Lauf), laeuft die Aktion trotzdem zu Ende und wird gespeichert."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck.db.session import reset_engine_cache

    engine = await _file_engine(tmp_path, "gate-caller-cancel.db")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    executor = _BlockingExecutor()
    get_extension_runtime().capabilities.provide("proxmox", ActionExecutor, executor)
    try:
        async with sessionmaker() as setup:
            decision = await gate.propose(
                setup, ext_id="core", request=_request(action_type="vm.stop", host_ref=None), command_field=None,
            )
            await setup.commit()

        async def _caller() -> None:
            async with sessionmaker() as caller_session:
                await gate.approve(caller_session, decision.action_id, user_id="user-1")

        caller = asyncio.create_task(_caller())
        await asyncio.wait_for(executor.started.wait(), timeout=5)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        executor.release.set()
        assert await gate.wait_for_action(decision.action_id, 5) is True
        assert (await _stored(sessionmaker, decision.action_id)).status == "succeeded"
    finally:
        await gate.shutdown_running()
        await engine.dispose()
        reset_engine_cache()


@pytest.mark.asyncio
async def test_hanging_executor_ends_in_failed_after_the_emergency_timeout(tmp_path, monkeypatch):
    """Ein Executor ohne eigenes Timeout (tote SSH-Verbindung) darf die Aktion nicht
    bis zum naechsten Neustart auf 'executing' lassen."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck.db.session import reset_engine_cache

    monkeypatch.setattr(gate, "EXECUTE_TIMEOUT_S", 0.05)
    engine = await _file_engine(tmp_path, "gate-timeout.db")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    executor = _BlockingExecutor()  # wird nie freigegeben
    get_extension_runtime().capabilities.provide("proxmox", ActionExecutor, executor)
    try:
        async with sessionmaker() as caller_session:
            decision = await gate.propose(
                caller_session, ext_id="core", request=_request(action_type="vm.stop", host_ref=None),
                command_field=None,
            )
            await gate.approve(caller_session, decision.action_id, user_id="user-1", wait_s=0)
        assert await gate.wait_for_action(decision.action_id, 5) is True

        stored = await _stored(sessionmaker, decision.action_id)
        assert stored.status == "failed"
        assert stored.result["error"] == gate.EXECUTE_TIMEOUT_ERROR
        assert (await _audit_result(sessionmaker, decision.action_id)) == {"success": False, "gate_error": gate.EXECUTE_TIMEOUT_ERROR}
        assert executor.started.is_set() and not executor.release.is_set()
    finally:
        await gate.shutdown_running()
        await engine.dispose()
        reset_engine_cache()


@pytest.mark.asyncio
async def test_shutdown_while_saving_keeps_the_known_result(tmp_path, monkeypatch):
    """Wird der Task erst beim Speichern abgebrochen, ist der Befehl schon gelaufen --
    dann zaehlt sein Ergebnis, nicht 'Abgebrochen'."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck.db.session import reset_engine_cache

    engine = await _file_engine(tmp_path, "gate-cancel-save.db")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    executor = _BlockingExecutor()
    get_extension_runtime().capabilities.provide("proxmox", ActionExecutor, executor)
    real_record = gate._record_outcome
    saving = asyncio.Event()
    calls = {"n": 0}

    async def _slow_first_record(action_id, status, result, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            saving.set()
            await asyncio.sleep(3600)
        return await real_record(action_id, status, result, **kwargs)

    monkeypatch.setattr(gate, "_record_outcome", _slow_first_record)
    try:
        async with sessionmaker() as caller_session:
            decision = await gate.propose(
                caller_session, ext_id="core", request=_request(action_type="vm.stop", host_ref=None),
                command_field=None,
            )
            await gate.approve(caller_session, decision.action_id, user_id="user-1", wait_s=0)
        executor.release.set()
        await asyncio.wait_for(saving.wait(), timeout=5)

        await gate.shutdown_running()

        stored = await _stored(sessionmaker, decision.action_id)
        assert stored.status == "succeeded"
        assert stored.result["output"] == "fertig"
    finally:
        await gate.shutdown_running()
        await engine.dispose()
        reset_engine_cache()
