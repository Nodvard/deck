"""Aktions-Gate: Sperrliste, Anti-Flapping, Autonomie-Entscheidung und Audit für Vorschläge von Extensions und Nutzern."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from nodvard_sdk import Actor, ActionRequest, GateDecision, GateOutcome, Risk, StandingApproval
from nodvard_sdk.actions import REQUEST_WAIT_S, ActionStatus
from nodvard_sdk.capabilities import ActionExecutor
from nodvard_sdk.types import Event
from sqlalchemy import update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..db.session import session_scope
from ..models import Action
from . import deny_patterns, known_actors
from .action_output import audit_result
from .events import get_event_bus
from .flap import check_and_record as _flap_check_and_record

logger = logging.getLogger("nodvard_deck.gate")

_RISK_ORDER = {Risk.LOW: 0, Risk.MEDIUM: 1, Risk.HIGH: 2, Risk.CRITICAL: 3}

DEFAULT_EXPIRES_IN_S = 24 * 60 * 60
"""Unbestätigte Vorschläge verfallen nach 24 h."""


def _actor_from_row(action: Action) -> Actor:
    from nodvard_sdk import ActorType

    return Actor(type=ActorType(action.proposed_by_type), id=action.proposed_by_id)


def _via(ext_id: str, proposer: Actor) -> dict[str, str]:
    """Nennt im Protokoll das Modul, wenn der Vorschlag nicht unter dessen heutiger Kennung kommt."""
    # `core` tragen die Aktionen aus der Oberfläche (api/v1/hosts.py); dahinter steht kein Modul.
    own = ext_id == "core" or (proposer.type.value == "extension" and proposer.id == ext_id)
    return {} if own else {"via": ext_id}


def describe_error(exc: BaseException) -> str:
    """Lesbarer Grund für das Protokoll; manche Ausnahmen (vor allem Zeitüberschreitungen) haben einen leeren Text."""
    text = str(exc).strip()
    if getattr(exc, "readable", False) and text:
        return text  # z. B. SshUnreachable: der Text sagt schon auf Deutsch, was los ist
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return f"Zeitüberschreitung{': ' + text if text else ''} – der Befehl hat zu lange gebraucht."
    if isinstance(exc, (ConnectionError, OSError)) and not text:
        return f"Server nicht erreichbar ({type(exc).__name__})."
    return text or type(exc).__name__


def _audit_error_for(exc: BaseException) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "Zeitüberschreitung – der Befehl hat zu lange gebraucht."
    # Bewusst ohne den Text der Ausnahme: Ein Executor kann Inhalte vom Server hineinschreiben.
    return f"Die Ausführung ist mit einem Fehler beendet worden ({type(exc).__name__}). Einzelheiten stehen in der Aktion."


async def _extra_deny_patterns(session: AsyncSession) -> tuple[deny_patterns.DenyPattern, ...]:
    from ..services import settings as settings_service

    raw = await settings_service.get_global(session, "security.deny_patterns", [])
    out = []
    for i, pattern_text in enumerate(raw or []):
        try:
            compiled = re.compile(pattern_text, re.IGNORECASE)
        except re.error:
            logger.warning("gate_invalid_deny_pattern pattern=%r", pattern_text)
            continue
        out.append(deny_patterns.DenyPattern(f"custom-{i}", compiled, pattern_text, origin="added"))
    return tuple(out)


async def find_executor(action_type: str) -> ActionExecutor | None:
    """Sucht den ActionExecutor ohne Beschränkung auf eine Extension (Kern-Code, keine Extension)."""
    from ..ext.runtime import get_extension_runtime

    runtime = get_extension_runtime()
    for impl in runtime.capabilities.query(ActionExecutor, allowed_ext_ids=None):
        if action_type in impl.action_types:
            return impl
    return None


WAIT_S = REQUEST_WAIT_S
"""So lange wartet eine Anfrage höchstens auf das Ergebnis; längere Ausführungen laufen im Hintergrund weiter, weil
Browser und Proxys lange Anfragen abbrechen."""

CANCELLED_ERROR = (
    "Abgebrochen: das Dashboard wurde beendet – Ausgang unbekannt, bitte auf dem Server prüfen."
)
"""Der Befehl kann auf dem Server weitergelaufen oder fertig geworden sein; das Dashboard weiß es nicht mehr."""

EXECUTE_TIMEOUT_S = 60 * 60.0
"""Notbremse um jede Ausführung: Ein Executor ohne eigenes Timeout bliebe sonst bis zum nächsten Neustart auf
`executing`."""

EXECUTE_TIMEOUT_ERROR = (
    "Zeitüberschreitung: keine Rückmeldung nach 60 Minuten – Ausgang unbekannt, bitte auf dem Server prüfen."
)

_RECORD_RETRY_DELAYS_S: tuple[float, ...] = (1.0, 3.0, 10.0)
"""Wartezeiten zwischen den Versuchen, das Ergebnis zu speichern, falls SQLite gesperrt ist; niemand wartet mehr
auf die Anfrage."""

_running: dict[str, asyncio.Task[None]] = {}
"""Laufende Ausführungen je Aktion; die Event-Loop hält Tasks nur schwach, ohne diese Referenz könnte der GC einen
laufenden Task einsammeln."""


def _request_from_row(action: Action) -> ActionRequest:
    return ActionRequest(
        action_type=action.action_type,
        payload=action.payload,
        host_ref=action.host_id,
        risk=Risk(action.risk),
        proposed_by=_actor_from_row(action),
        reason=action.reason,
        correlation_id=action.correlation_id,
        idempotency_key=action.idempotency_key,
        standing_approval=_standing_from_row(action),
    )


def _standing_from_row(action: Action) -> StandingApproval | None:
    decision = action.gate_decision or {}
    info = decision.get("standing_approval")
    if decision.get("rule") != "standing_approval" or not isinstance(info, dict):
        return None
    try:
        granted_at = datetime.fromisoformat(str(info.get("granted_at")))
    except ValueError:
        # Schreibt nur das Gate selbst; im Zweifel trotzdem als Dauerfreigabe kennzeichnen.
        granted_at = action.approved_at or utcnow()
    return StandingApproval(
        granted_by_user_id=str(info.get("granted_by") or ""), granted_at=granted_at,
        label=None if info.get("label") is None else str(info["label"]),
    )


def start_execution(action_id: str) -> asyncio.Task[None]:
    """Startet eine committete `executing`-Aktion als Hintergrund-Task; läuft schon einer, kommt er zurück."""
    task = _running.get(action_id)
    if task is not None and not task.done():
        return task
    task = asyncio.create_task(_run_action(action_id), name=f"nodvard-deck-action-{action_id}")
    _running[action_id] = task

    def _forget(done: asyncio.Task[None]) -> None:
        if _running.get(action_id) is done:
            del _running[action_id]

    task.add_done_callback(_forget)
    return task


def is_running(action_id: str) -> bool:
    task = _running.get(action_id)
    return task is not None and not task.done()


async def wait_for_action(action_id: str, timeout: float | None) -> bool:
    """Wartet höchstens `timeout` Sekunden auf die Ausführung und gibt `True` zurück, wenn sie fertig ist oder hier
    nicht läuft."""
    task = _running.get(action_id)
    if task is None or task.done():
        return True
    # `asyncio.wait()` bricht die Task nie ab: Bei Zeitablauf oder Abbruch des Aufrufers läuft die Aktion weiter.
    done, _ = await asyncio.wait({task}, timeout=timeout)
    return task in done


async def shutdown_running(timeout: float = 5.0) -> None:
    """Bricht laufende Ausführungen ab, sie vermerken sich selbst als `failed`; den Rest fängt
    `fail_interrupted_on_boot()` auf."""
    tasks = [t for t in _running.values() if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
    _running.clear()


async def _run_action(action_id: str) -> None:
    outcome: tuple[ActionStatus, dict[str, Any], str | None] | None = None
    try:
        # Eigene kurze Sessions statt der Anfrage-Session: Ein Executor ist Extension-Code und öffnet evtl. selbst eine
        # `session_scope()` mit Schreibzugriff; SQLite (ein Schreiber zur Zeit) blockierte sie gegen eine offene
        # Transaktion. Darum ist während der Ausführung keine Transaktion offen.
        async with session_scope() as session:
            action = await session.get(Action, action_id)
            if action is None or action.status != ActionStatus.EXECUTING.value:
                return
            request = _request_from_row(action)
            people = [(action.proposed_by_type, action.proposed_by_id)]
            if action.approved_by_user_id:
                people.append(("user", action.approved_by_user_id))
            # So ist auch während einer langen Ausführung sichtbar, seit wann sie läuft.
            action.executed_at = utcnow()

        executor = await find_executor(request.action_type)
        if executor is None:
            error = f"Kein ActionExecutor für '{request.action_type}'."
            outcome = ActionStatus.FAILED, {"success": False, "error": error}, error
        else:
            deadline = asyncio.timeout(EXECUTE_TIMEOUT_S)
            try:
                async with deadline:
                    with known_actors.known_while(people):
                        res = await executor.execute(request)
                outcome = (
                    ActionStatus.SUCCEEDED if res.success else ActionStatus.FAILED,
                    res.model_dump(mode="json"),
                    None,
                )
            except Exception as exc:  # noqa: BLE001 - darf das Protokoll nie verschlucken
                logger.exception("gate_execute_raised action_id=%s action_type=%s", action_id, request.action_type)
                if deadline.expired():
                    outcome = ActionStatus.FAILED, {"success": False, "error": EXECUTE_TIMEOUT_ERROR}, EXECUTE_TIMEOUT_ERROR
                else:
                    outcome = ActionStatus.FAILED, {"success": False, "error": describe_error(exc)}, _audit_error_for(exc)
        await _record_outcome(action_id, outcome[0], outcome[1], gate_error=outcome[2])
    except asyncio.CancelledError:
        # Beim Beenden: Steht das Ergebnis schon fest (abgebrochen erst beim Speichern), zählt es, sonst ist der Ausgang
        # unbekannt.
        if outcome is not None:
            try:
                await _record_outcome(action_id, outcome[0], outcome[1], gate_error=outcome[2], publish=False)
            except Exception:  # noqa: BLE001 - dann bleibt nur der Abbruch-Vermerk
                logger.exception("gate_record_outcome_on_cancel_failed action_id=%s", action_id)
        await _record_outcome_safely(
            action_id, {"success": False, "error": CANCELLED_ERROR}, gate_error=CANCELLED_ERROR, publish=False
        )
        raise
    except Exception as exc:
        logger.exception("gate_background_failed action_id=%s", action_id)
        await _record_outcome_safely(
            action_id, {"success": False, "error": describe_error(exc)}, gate_error=_audit_error_for(exc)
        )


async def _record_outcome_safely(
    action_id: str, result: dict[str, Any], *, gate_error: str | None = None, publish: bool = True
) -> None:
    try:
        await _record_outcome(action_id, ActionStatus.FAILED, result, gate_error=gate_error, publish=publish)
    except Exception:  # letzte Rettung ist fail_interrupted_on_boot()
        logger.exception("gate_record_outcome_failed action_id=%s", action_id)


async def _record_outcome(
    action_id: str, status: ActionStatus, result: dict[str, Any], *, gate_error: str | None = None, publish: bool = True
) -> None:
    from ..services import audit as audit_service

    outcome = "success" if status == ActionStatus.SUCCEEDED else "failure"
    delays = list(_RECORD_RETRY_DELAYS_S)
    while True:
        try:
            async with session_scope() as session:
                # Bewacht auf `executing`: Hat jemand anderes die Aktion schon abgeschlossen, bleibt dessen Ergebnis
                # stehen.
                action, moved = await _atomic_transition(
                    session, action_id, from_status=ActionStatus.EXECUTING.value, to_status=status.value,
                    result=result, finished_at=utcnow(),
                )
                if not moved or action is None:
                    return
                await audit_service.log(
                    session,
                    actor_type="system",
                    actor_id="gate",
                    action="action.executed",
                    outcome=outcome,
                    target_type="action",
                    target_id=action.id,
                    reason=action.reason,
                    # Nur Erfolg, Exitcode und Längen: Die Ausgabe kann Inhalte vom Server enthalten und steht nur in
                    # der Aktion (Abruf mit `hosts.execute`); dazu der feste Grund des Gates (`gate_error`).
                    detail={
                        "action_type": action.action_type,
                        "result": audit_result(result, gate_error=gate_error),
                    },
                    correlation_id=action.correlation_id,
                )
                event_payload = {
                    "action_id": action.id,
                    "ext_id": action.ext_id,
                    "action_type": action.action_type,
                    "host_id": action.host_id,
                    "payload": action.payload,
                    "outcome": outcome,
                }
                correlation_id = action.correlation_id
            break
        except OperationalError as exc:
            if "locked" not in str(exc) or not delays:
                raise
            logger.warning("gate_record_outcome_locked action_id=%s retry_in=%ss", action_id, delays[0])
            await asyncio.sleep(delays.pop(0))

    if not publish:
        return
    # Erst nach dem Commit, damit ein Abonnent mit eigener Session die Schreibsperre frei findet und den Endstatus
    # liest. Der Bus entkoppelt die Extensions: Eine reagiert auf wiederholte Ausführungen, ohne die auslösende zu
    # kennen.
    await get_event_bus().publish(
        Event(name="action.executed", payload=event_payload, correlation_id=correlation_id)
    )


async def _start_and_wait(session: AsyncSession, action: Action, wait_s: float | None) -> Action:
    # Der Commit vorab ist Pflicht: Der Hintergrund-Task liest die Zeile über eine eigene Verbindung, die
    # Schreibsperre muss frei sein.
    await session.commit()
    start_execution(action.id)
    await wait_for_action(action.id, wait_s)
    await session.refresh(action)
    return action


async def _persist_denied(
    session: AsyncSession, *, ext_id: str, request: ActionRequest, rule: str, detail: str | None
) -> GateDecision:
    from ..services import audit as audit_service

    row = Action(
        ext_id=ext_id,
        action_type=request.action_type,
        host_id=request.host_ref,
        payload=request.payload,
        risk=request.risk.value,
        status=ActionStatus.DENIED.value,
        proposed_by_type=request.proposed_by.type.value,
        proposed_by_id=request.proposed_by.id,
        reason=request.reason,
        gate_decision={"rule": rule, "detail": detail},
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
    )
    session.add(row)
    await session.flush()
    await audit_service.log(
        session,
        actor_type=request.proposed_by.type.value,
        actor_id=request.proposed_by.id,
        action="action.denied",
        outcome="denied",
        target_type="action",
        target_id=row.id,
        reason=request.reason,
        detail={"rule": rule, "gate_detail": detail, **_via(ext_id, request.proposed_by)},
        correlation_id=request.correlation_id,
    )
    return GateDecision(
        action_id=row.id, outcome=GateOutcome.DENY, status=ActionStatus.DENIED, rule=rule, detail=detail
    )


STANDING_APPROVAL_PERMISSION = "actions.standing_approval"
"""Wer eine Dauerfreigabe erteilen darf (bei den eingebauten Rollen Owner und Admin), zugleich die Berechtigung
einer Extension dafür."""


_RISK_WORDS = {Risk.LOW: "niedrigem", Risk.MEDIUM: "mittlerem", Risk.HIGH: "hohem", Risk.CRITICAL: "kritischem"}


async def standing_approval_problem(
    session: AsyncSession, *, granted_by_user_id: str, risk: Risk, from_ai: bool = False
) -> tuple[str, str | None] | None:
    """`None`, wenn die Dauerfreigabe für Stufe `risk` gilt, sonst `(Grund, Berechtigung)`, der Grund nennt die
    Berechtigung bewusst nicht."""
    # Prüft, was nur der Kern weiß: Gibt es die Person noch, ist sie aktiv, darf sie heute noch Dauerfreigaben erteilen
    # und diese Stufe bestätigen? Wird sie herabgestuft oder gesperrt, läuft wieder alles über den Klick. Die Anzeige
    # nutzt dieselbe Prüfung, damit sie nie "gilt" zeigt, wo das Gate ablehnen würde.
    from ..models import User
    from ..services.auth import user_has_permission

    if from_ai:
        # Vorschläge einer KI nie ohne Klick, egal was mitgeschickt wird.
        return "Vorschläge der KI laufen nie über eine Dauerfreigabe.", None
    user = await session.get(User, granted_by_user_id)
    if user is None or not user.is_active:
        return "Die Person, die die Dauerfreigabe erteilt hat, gibt es nicht mehr oder sie ist gesperrt.", None
    if not user_has_permission(user, STANDING_APPROVAL_PERMISSION):
        return (
            (
                "Die Person, die die Dauerfreigabe erteilt hat, ist kein Owner oder Admin mehr und darf "
                "keine Dauerfreigaben mehr erteilen."
            ),
            STANDING_APPROVAL_PERMISSION,
        )
    permission = f"actions.approve:{risk.value}"
    if not user_has_permission(user, permission):
        return (
            (
                "Die Person, die die Dauerfreigabe erteilt hat, hat keine Berechtigung mehr für Freigaben "
                f"mit {_RISK_WORDS.get(risk, risk.value)} Risiko."
            ),
            permission,
        )
    return None


async def _standing_note(session: AsyncSession, granted_by_user_id: str, granted_at: Any) -> str:
    from zoneinfo import ZoneInfo

    from ..models import User
    from .timezone import get_timezone

    try:
        # Das Datum steht in der eingestellten Zeitzone, nicht in der des Prozesses.
        zone = ZoneInfo(await get_timezone(session))
    except Exception:  # noqa: BLE001 – ein Anzeigedatum darf den Vorschlag nie kippen
        zone = None
    when = granted_at if granted_at.tzinfo is not None else granted_at.replace(tzinfo=timezone.utc)
    date = when.astimezone(zone).strftime("%d.%m.%Y") if zone is not None else when.strftime("%d.%m.%Y")
    user = await session.get(User, granted_by_user_id)
    who = (user.username if user is not None else None) or granted_by_user_id
    return f"vom {date} durch {who}"


async def propose(
    session: AsyncSession, *, ext_id: str, request: ActionRequest, command_field: str | None,
    wait_s: float | None = None,
) -> GateDecision:
    """Prüft Sperrliste, Anti-Flapping, Autonomie und Audit für einen Vorschlag; die Berechtigungen prüft der
    Aufrufer vorher."""
    from ..services import settings as settings_service

    now = utcnow()

    command = request.payload.get(command_field) if command_field else None
    if isinstance(command, str) and command.strip():
        extra = await _extra_deny_patterns(session)
        matched = deny_patterns.match_deny_patterns(command, extra)
        if matched is not None:
            return await _persist_denied(
                session, ext_id=ext_id, request=request,
                rule=f"deny_pattern:{matched.id}", detail=matched.description,
            )

    fingerprint, blocked = await _flap_check_and_record(
        session, host_id=request.host_ref, action_type=request.action_type, payload=request.payload
    )
    if blocked:
        return await _persist_denied(
            session, ext_id=ext_id, request=request, rule="flap_limit",
            detail=f"Bereits mehrfach in kurzer Zeit versucht (Fingerprint {fingerprint[:12]}...).",
        )

    mode = await settings_service.get_global(session, "autonomy.mode", "propose")
    max_risk_raw = await settings_service.get_global(session, "autonomy.max_risk", "low")
    try:
        max_risk = Risk(max_risk_raw)
    except ValueError:
        max_risk = Risk.LOW
    auto_allow = mode == "full" and _RISK_ORDER[request.risk] <= _RISK_ORDER[max_risk]
    rule = "autonomy:full" if auto_allow else "autonomy:propose"
    gate_decision: dict[str, Any] = {}
    approved_by_user_id: str | None = None
    standing = request.standing_approval
    reason = request.reason
    if standing is not None:
        # Eine gültige Dauerfreigabe ersetzt den Klick, unabhängig von der Automatik-Einstellung;
        # Sperrliste und Anti-Flapping (oben) gelten trotzdem.
        from nodvard_sdk import ActorType

        found = await standing_approval_problem(
            session, granted_by_user_id=standing.granted_by_user_id, risk=request.risk,
            from_ai=request.proposed_by.type == ActorType.AI,
        )
        standing_info = {
            "granted_by": standing.granted_by_user_id,
            "granted_at": standing.granted_at.isoformat(),
            "label": standing.label,
        }
        # Den Hinweis erst nach der Prüfung setzen, damit "lief ohne Klick" nur an Aktionen steht, die wirklich ohne
        # Klick anliefen.
        note = await _standing_note(session, standing.granted_by_user_id, standing.granted_at)
        if found is None:
            auto_allow = True
            rule = "standing_approval"
            approved_by_user_id = standing.granted_by_user_id
            gate_decision["standing_approval"] = standing_info
            reason = f"{reason} – ohne Klick, lief mit Dauerfreigabe {note}"
        else:
            problem, permission = found
            rejected_info: dict[str, Any] = {**standing_info, "problem": problem}
            if permission is not None:
                rejected_info["permission"] = permission
            gate_decision["standing_approval_rejected"] = rejected_info
            reason = f"{reason} – Dauerfreigabe {note} gilt nicht: {problem}"
            if not auto_allow:
                # Läuft sie trotzdem an (Automatik "Selbstständig handeln"), wartet sie auf keinen Klick, also nicht
                # dazu auffordern.
                reason += " Bitte selbst freigeben."
    gate_decision = {"rule": rule, **gate_decision}

    row = Action(
        ext_id=ext_id,
        action_type=request.action_type,
        host_id=request.host_ref,
        payload=request.payload,
        risk=request.risk.value,
        status=ActionStatus.EXECUTING.value if auto_allow else ActionStatus.PROPOSED.value,
        proposed_by_type=request.proposed_by.type.value,
        proposed_by_id=request.proposed_by.id,
        reason=reason,
        gate_decision=gate_decision,
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
        expires_at=None if auto_allow else now + timedelta(seconds=request.expires_in_s or DEFAULT_EXPIRES_IN_S),
    )
    if auto_allow:
        row.approved_at = now
        row.approved_by_user_id = approved_by_user_id
    session.add(row)
    await session.flush()

    from ..services import audit as audit_service

    await audit_service.log(
        session,
        actor_type=request.proposed_by.type.value,
        actor_id=request.proposed_by.id,
        action="action.approved" if auto_allow else "action.proposed",
        outcome="proposed",
        target_type="action",
        target_id=row.id,
        reason=reason,
        detail={**gate_decision, **_via(ext_id, request.proposed_by)},
        correlation_id=request.correlation_id,
    )

    if auto_allow:
        await _start_and_wait(session, row, wait_s)
        return GateDecision(
            action_id=row.id, outcome=GateOutcome.ALLOW, status=ActionStatus(row.status),
            rule=rule, detail=None,
        )

    rejected = gate_decision.get("standing_approval_rejected")
    return GateDecision(
        action_id=row.id, outcome=GateOutcome.REQUIRE_CONFIRMATION, status=ActionStatus.PROPOSED,
        rule="autonomy:propose", detail=rejected["problem"] if rejected else None, expires_at=row.expires_at,
    )


async def _atomic_transition(
    session: AsyncSession, action_id: str, *, from_status: str, to_status: str, **extra_values: Any
) -> tuple[Action | None, bool]:
    action = await session.get(Action, action_id)
    if action is None:
        return None, False
    # Guardierter `UPDATE ... WHERE id=? AND status=?`: Der Status schützt bei Doppelklick oder Retry vor doppelter
    # Ausführung.
    result = await session.execute(
        update(Action)
        .where(Action.id == action_id, Action.status == from_status)
        .values(status=to_status, **extra_values)
    )
    await session.refresh(action)
    return action, result.rowcount == 1


async def approve(
    session: AsyncSession, action_id: str, *, user_id: str, wait_s: float | None = None
) -> tuple[Action | None, bool]:
    """Gibt `(Zeile, hat_dieser_Aufruf_die_Ausführung_ausgelöst)` zurück, damit ein Doppelklick 409 statt 200
    bekommt."""
    now = utcnow()
    action = await session.get(Action, action_id)
    if action is None:
        return None, False

    if action.status == ActionStatus.PROPOSED.value and action.expires_at is not None and action.expires_at < now:
        action, expired = await _atomic_transition(
            session, action_id, from_status=ActionStatus.PROPOSED.value, to_status=ActionStatus.EXPIRED.value
        )
        if expired and action is not None:
            await _audit_expired(session, action)
        return action, False

    action, moved = await _atomic_transition(
        session, action_id, from_status=ActionStatus.PROPOSED.value, to_status=ActionStatus.EXECUTING.value,
        approved_by_user_id=user_id, approved_at=now,
    )
    if not moved or action is None:
        return action, False

    from ..services import audit as audit_service

    await audit_service.log(
        session, actor_type="user", actor_id=user_id, action="action.approved", outcome="proposed",
        target_type="action", target_id=action.id, correlation_id=action.correlation_id,
    )
    await _start_and_wait(session, action, wait_s)
    return action, True


async def reject(session: AsyncSession, action_id: str, *, user_id: str, reason: str) -> tuple[Action | None, bool]:
    """Rückgabe wie `approve()`: `(Zeile, hat_dieser_Aufruf_abgelehnt)`."""
    action, moved = await _atomic_transition(
        session, action_id, from_status=ActionStatus.PROPOSED.value, to_status=ActionStatus.DENIED.value,
        approved_by_user_id=user_id, approved_at=utcnow(),
    )
    if not moved or action is None:
        return action, False

    action.gate_decision = {**action.gate_decision, "rule": "user:reject", "user_reason": reason}
    await session.flush()

    from ..services import audit as audit_service

    await audit_service.log(
        session, actor_type="user", actor_id=user_id, action="action.denied", outcome="denied",
        target_type="action", target_id=action.id, reason=reason, correlation_id=action.correlation_id,
    )
    return action, True


async def dismiss(session: AsyncSession, action_id: str, *, user_id: str) -> tuple[Action | None, bool]:
    """Rückgabe wie `approve()`: `(Zeile, hat_dieser_Aufruf_verworfen)`."""
    action, moved = await _atomic_transition(
        session, action_id, from_status=ActionStatus.PROPOSED.value, to_status=ActionStatus.DISMISSED.value,
        approved_by_user_id=user_id, approved_at=utcnow(),
    )
    if not moved or action is None:
        return action, False

    from ..services import audit as audit_service

    await audit_service.log(
        session, actor_type="user", actor_id=user_id, action="action.dismissed", outcome="denied",
        target_type="action", target_id=action.id, correlation_id=action.correlation_id,
    )
    return action, True


INTERRUPTED_ERROR = "Durch Neustart unterbrochen – Ausgang unbekannt, bitte auf dem Server prüfen."


async def fail_interrupted_on_boot(session: AsyncSession) -> int:
    """Setzt beim Start noch `executing` stehende Aktionen auf `failed`; muss vor dem Laden der Extensions laufen."""
    from sqlalchemy import select

    from ..services import audit as audit_service

    rows = (
        await session.execute(select(Action).where(Action.status == ActionStatus.EXECUTING.value))
    ).scalars().all()
    failed = 0
    for row in rows:
        result = {"success": False, "error": INTERRUPTED_ERROR}
        _, moved = await _atomic_transition(
            session, row.id, from_status=ActionStatus.EXECUTING.value, to_status=ActionStatus.FAILED.value,
            result=result, finished_at=utcnow(),
        )
        if not moved:
            continue
        failed += 1
        await audit_service.log(
            session, actor_type="system", actor_id="gate", action="action.executed", outcome="failure",
            target_type="action", target_id=row.id, reason=row.reason,
            detail={"action_type": row.action_type, "result": audit_result(result, gate_error=INTERRUPTED_ERROR)},
            correlation_id=row.correlation_id,
        )
    return failed


async def _audit_expired(session: AsyncSession, action: Action) -> None:
    from ..services import audit as audit_service

    await audit_service.log(
        session, actor_type="system", actor_id="gate", action="action.expired", outcome="denied",
        target_type="action", target_id=action.id, reason=action.reason, correlation_id=action.correlation_id,
    )


async def expire_stale(session: AsyncSession) -> int:
    """Markiert unbestätigte Vorschläge nach `expires_at` (Standard 24 h) als `expired`, je mit einer Audit-Zeile."""
    from sqlalchemy import select

    now = utcnow()
    rows = (
        await session.execute(
            select(Action).where(Action.status == ActionStatus.PROPOSED.value, Action.expires_at.is_not(None), Action.expires_at < now)
        )
    ).scalars().all()
    expired = 0
    for row in rows:
        _, moved = await _atomic_transition(
            session, row.id, from_status=ActionStatus.PROPOSED.value, to_status=ActionStatus.EXPIRED.value
        )
        if not moved:
            continue
        expired += 1
        await _audit_expired(session, row)
    return expired
