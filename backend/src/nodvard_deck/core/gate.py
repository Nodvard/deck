"""Das Aktions-Gate -- docs/01-ARCHITECTURE.md §4.

Ein Kern-Dienst, kein AI-SOC-Feature (docs/01 §4: "Das ist kein Feature des AI-SOC,
sondern ein Kern-Dienst, den auch das Script-Repository, das Gameserver-Modul und der
Dateimanager benutzen"). Zwei Aufrufer teilen sich diesen Code: `ext.context.
ActionsHandle.propose()` (eine Extension schlaegt vor) und `api.v1.hosts` (ein Nutzer
loest eine host-gebundene Aktion direkt aus dem UI aus). Beide pruefen Permission/RBAC
(Schritte 1+2 im docs/01-§4-Diagramm) VOR dem Aufruf hier -- welche Berechtigung noetig
ist, unterscheidet sich je Aufrufer (Extension-Grant vs. Nutzer-RBAC), das Gate selbst
kennt nur noch Schritt 3 an: Sperrliste, Anti-Flapping, Autonomie-Entscheidung, und
Schritt 6 (Audit), das NIE uebersprungen wird -- auch nicht bei einer Ablehnung.
"""

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
from . import deny_patterns
from .action_output import audit_result
from .events import get_event_bus
from .flap import check_and_record as _flap_check_and_record

logger = logging.getLogger("nodvard_deck.gate")

_RISK_ORDER = {Risk.LOW: 0, Risk.MEDIUM: 1, Risk.HIGH: 2, Risk.CRITICAL: 3}

DEFAULT_EXPIRES_IN_S = 24 * 60 * 60
"""docs/03-DATA-MODEL.md §5: "unbestaetigte Vorschlaege verfallen (Default 24 h)"."""


def _actor_from_row(action: Action) -> Actor:
    from nodvard_sdk import ActorType

    return Actor(type=ActorType(action.proposed_by_type), id=action.proposed_by_id)


def describe_error(exc: BaseException) -> str:
    """Lesbarer Grund fuer das Protokoll -- manche Ausnahmen (vor allem
    Zeitueberschreitungen) haben einen leeren Text, dann stand bei der Aktion nur
    "fehlgeschlagen" ohne jeden Hinweis."""
    text = str(exc).strip()
    if getattr(exc, "readable", False) and text:
        return text  # z. B. SshUnreachable: der Text sagt schon auf Deutsch, was los ist
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return f"Zeitüberschreitung{': ' + text if text else ''} – der Befehl hat zu lange gebraucht."
    if isinstance(exc, (ConnectionError, OSError)) and not text:
        return f"Server nicht erreichbar ({type(exc).__name__})."
    return text or type(exc).__name__


def _audit_error_for(exc: BaseException) -> str:
    """Grund fuers Protokoll, wenn eine Ausnahme die Ausfuehrung beendet hat. Bewusst ohne den
    Text der Ausnahme: ein Executor kann Inhalte vom Server hineinschreiben, im Protokoll steht
    nur die Art des Fehlers. Der volle Text steht in der Aktion (`describe_error`)."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "Zeitüberschreitung – der Befehl hat zu lange gebraucht."
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
    """Unrestricted Capability-Query -- das ist Kern-Code, keine Extension (docs/02 §2
    beschraenkt 'requires' nur auf Extension-zu-Extension-Sicht), derselbe Modus, den
    schon die WP-4-Terminal-WS-Bruecke fuer `TerminalTarget` nutzt."""
    from ..ext.runtime import get_extension_runtime

    runtime = get_extension_runtime()
    for impl in runtime.capabilities.query(ActionExecutor, allowed_ext_ids=None):
        if action_type in impl.action_types:
            return impl
    return None


WAIT_S = REQUEST_WAIT_S
"""So lange wartet eine Anfrage (Bestaetigen, Ausloesen) hoechstens auf das
Ergebnis. Eine genehmigte Aktion lief frueher komplett innerhalb der HTTP-Anfrage --
ein Update bis zu 45 min, ein Skript bis zu 30 min. Firefox brach nach 300 s mit
'NetworkError' ab, ein spaeteres nginx (proxy_read_timeout 60 s) haette 'HTTP 504'
gemeldet, obwohl die Aktion weiterlief oder sogar geklappt hatte. Seitdem laeuft jede
Ausfuehrung als eigener Hintergrund-Task; was bis hierher nicht fertig ist, meldet die
API als 202 'executing', das Ergebnis holt sich die Oberflaeche ueber GET /actions/{id}."""

CANCELLED_ERROR = (
    "Abgebrochen: das Dashboard wurde beendet – Ausgang unbekannt, bitte auf dem Server prüfen."
)
"""Der Befehl kann auf dem Server weitergelaufen oder sogar fertig geworden sein --
das Dashboard weiss es nur nicht mehr."""

EXECUTE_TIMEOUT_S = 60 * 60.0
"""Notbremse um jede Ausfuehrung: Haengt ein Executor ohne eigenes Timeout (z. B. eine
tote SSH-Verbindung ohne Keepalive), stuende die Aktion sonst bis zum naechsten
Neustart auf 'executing'. Grosszuegig ueber den bekannten Grenzen der Extensions
(Update 45 min, Skript und Lynis 30 min), damit sie nie zuerst greift."""

EXECUTE_TIMEOUT_ERROR = (
    "Zeitüberschreitung: keine Rückmeldung nach 60 Minuten – Ausgang unbekannt, bitte auf dem Server prüfen."
)

_RECORD_RETRY_DELAYS_S: tuple[float, ...] = (1.0, 3.0, 10.0)
"""Wartezeiten zwischen den Versuchen, das Ergebnis zu speichern, falls SQLite gerade
gesperrt ist ('database is locked', busy_timeout 5 s). Frueher wartete die Anfrage
selbst und der Nutzer sah den Fehler -- jetzt wartet niemand mehr, also muss der
Hintergrund-Task es selbst noch einmal versuchen."""

_running: dict[str, asyncio.Task[None]] = {}
"""Laufende Ausfuehrungen je Aktion. Die Event-Loop haelt Tasks nur schwach -- ohne
diese starke Referenz koennte der Garbage Collector einen noch laufenden Task
einsammeln, und die Aktion stuende fuer immer auf 'executing'."""


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
    """Die Dauerfreigabe, ueber die die Aktion ohne Klick anlief -- None, wenn ein Mensch sie per
    Klick freigegeben hat (auch dann, wenn der Vorschlag sich auf eine Dauerfreigabe berief, das
    Gate sie aber nicht anerkannt hat). So kann der Executor direkt vor dem Befehl pruefen, ob sie
    noch gilt, ohne einen Klick auszubremsen."""
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
    """Startet die Ausfuehrung einer Aktion (Status davor: `executing`, bereits
    committet) als Hintergrund-Task. Idempotent: laeuft schon einer, kommt er zurueck."""
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
    """Wartet hoechstens `timeout` Sekunden (None = bis zum Ende) auf die Ausfuehrung.
    `True`, wenn sie fertig ist (oder in diesem Prozess gar nicht laeuft). Bei
    Zeitablauf oder wenn der Aufrufer selbst abgebrochen wird, laeuft die Aktion weiter
    -- `asyncio.wait()` bricht die erwarteten Tasks nie ab."""
    task = _running.get(action_id)
    if task is None or task.done():
        return True
    done, _ = await asyncio.wait({task}, timeout=timeout)
    return task in done


async def shutdown_running(timeout: float = 5.0) -> None:
    """Beim Beenden: laufende Ausfuehrungen abbrechen. Sie vermerken sich selbst als
    'failed' mit CANCELLED_ERROR (siehe `_run_action()`); wer das nicht mehr schafft,
    faengt beim naechsten Start `fail_interrupted_on_boot()` auf."""
    tasks = [t for t in _running.values() if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
    _running.clear()


async def _run_action(action_id: str) -> None:
    """Der Hintergrund-Task: fuehrt aus und schreibt IMMER Ergebnis + Audit-Zeile --
    auch wenn der ActionExecutor selbst wirft (z. B. eine mitten in der Ausfuehrung
    abgerissene SSH-Verbindung) oder das Laden scheitert. Eine genehmigte, aber
    nirgendwo protokollierte Ausfuehrung ist genau der Fehlermodus, den docs/01 §4
    strukturell ausschliessen soll.

    Eigene, kurze Sessions statt der Session der Anfrage: Ein ActionExecutor ist
    Extension-Code und darf beliebiges eigenes I/O machen -- oeffnet er dabei (wie
    ctx.vault_use()/ctx.audit.log()) eine eigene session_scope() fuer einen
    Schreibzugriff, blockierte SQLite (ein Schreiber zur Zeit, D-02) sie gegen eine
    noch offene Transaktion (Live-Fund WP-8, proxmox). Waehrend der Ausfuehrung ist
    deshalb keine Transaktion offen."""
    # Status, Ergebnis der Aktion und (falls das Gate den Grund selbst nennt) der feste Text fuers Protokoll.
    outcome: tuple[ActionStatus, dict[str, Any], str | None] | None = None
    try:
        async with session_scope() as session:
            action = await session.get(Action, action_id)
            if action is None or action.status != ActionStatus.EXECUTING.value:
                return
            request = _request_from_row(action)
            # So ist auch waehrend einer langen Ausfuehrung sichtbar, seit
            # wann sie laeuft. session_scope() committet beim Verlassen.
            action.executed_at = utcnow()

        executor = await find_executor(request.action_type)
        if executor is None:
            error = f"Kein ActionExecutor für '{request.action_type}'."
            outcome = ActionStatus.FAILED, {"success": False, "error": error}, error
        else:
            deadline = asyncio.timeout(EXECUTE_TIMEOUT_S)
            try:
                async with deadline:
                    res = await executor.execute(request)
                outcome = (
                    ActionStatus.SUCCEEDED if res.success else ActionStatus.FAILED,
                    res.model_dump(mode="json"),
                    None,
                )
            except Exception as exc:  # noqa: BLE001 - siehe Docstring: darf das Protokoll nie verschlucken
                logger.exception("gate_execute_raised action_id=%s action_type=%s", action_id, request.action_type)
                if deadline.expired():
                    outcome = ActionStatus.FAILED, {"success": False, "error": EXECUTE_TIMEOUT_ERROR}, EXECUTE_TIMEOUT_ERROR
                else:
                    outcome = ActionStatus.FAILED, {"success": False, "error": describe_error(exc)}, _audit_error_for(exc)
        await _record_outcome(action_id, outcome[0], outcome[1], gate_error=outcome[2])
    except asyncio.CancelledError:
        # Beim Beenden: Stand das Ergebnis schon fest (abgebrochen erst beim
        # Speichern), zaehlt es -- sonst ist der Ausgang unbekannt.
        if outcome is not None:
            try:
                await _record_outcome(action_id, outcome[0], outcome[1], gate_error=outcome[2], publish=False)
            except Exception:  # noqa: BLE001 - dann bleibt nur der Abbruch-Vermerk
                logger.exception("gate_record_outcome_on_cancel_failed action_id=%s", action_id)
        await _record_outcome_safely(
            action_id, {"success": False, "error": CANCELLED_ERROR}, gate_error=CANCELLED_ERROR, publish=False
        )
        raise
    except Exception as exc:  # z. B. Datenbankfehler beim Laden/Speichern
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
    """Ergebnis und Audit-Zeile in EINER eigenen Transaktion festschreiben, erst danach
    Abonnenten benachrichtigen: ein Abonnent, der ueber eine eigene
    Session schreibt (scripts: _on_action_executed -> ctx.notify.send), findet die
    Schreibsperre frei und liest schon den Endstatus. Bewacht auf `executing` -- hat
    jemand anderes die Aktion schon abgeschlossen, bleibt sein Ergebnis stehen."""
    from ..services import audit as audit_service

    outcome = "success" if status == ActionStatus.SUCCEEDED else "failure"
    delays = list(_RECORD_RETRY_DELAYS_S)
    while True:
        try:
            async with session_scope() as session:
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
                    # Nur Erfolg, Exitcode und Laengen: die Ausgabe kann Inhalte vom Server
                    # enthalten und steht nur in der Aktion (Abruf mit `hosts.execute`). Der feste
                    # Grund des Gates (`gate_error`) steht dazu im Protokoll.
                    detail={
                        "action_type": action.action_type,
                        "result": audit_result(result, gate_error=gate_error),
                    },
                    correlation_id=action.correlation_id,
                )
                event_payload = {
                    "action_id": action.id,
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
    # docs/02-EXTENSION-API.md §6 ("KI befoerdert wiederkehrenden Fix"): eine
    # Extension (z. B. scripts) soll auf wiederholte, identische Ausfuehrungen
    # reagieren koennen, OHNE die auslösende Extension (z. B. nexus-soc) zu kennen --
    # "die beiden Extensions reden ueber den Event-Bus, nicht miteinander". Kern-Code
    # meldet Kern-Zustandsänderungen, unabhängig davon, ob heute schon ein Abonnent
    # existiert.
    await get_event_bus().publish(
        Event(name="action.executed", payload=event_payload, correlation_id=correlation_id)
    )


async def _start_and_wait(session: AsyncSession, action: Action, wait_s: float | None) -> Action:
    """Statusuebergang nach `executing` samt Audit-Zeile festschreiben, Ausfuehrung im
    Hintergrund starten und hoechstens `wait_s` Sekunden (None = bis zum Ende) darauf
    warten. Danach traegt `action` den aktuellen Stand -- fertig oder noch
    `executing`. Der Commit vorab ist Pflicht: der Hintergrund-Task liest die Zeile
    ueber eine eigene Verbindung, und die Schreibsperre muss frei sein (D-14-Muster)."""
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
        detail={"rule": rule, "gate_detail": detail},
        correlation_id=request.correlation_id,
    )
    return GateDecision(
        action_id=row.id, outcome=GateOutcome.DENY, status=ActionStatus.DENIED, rule=rule, detail=detail
    )


STANDING_APPROVAL_PERMISSION = "actions.standing_approval"
"""Wer eine Dauerfreigabe erteilen darf (bei den eingebauten Rollen nur Owner und Admin).
Dieselbe Zeichenkette ist die Erweiterungs-Berechtigung, ueberhaupt Vorschlaege mit einer
Dauerfreigabe einzureichen (`ext.context.ActionsHandle.propose`)."""


_RISK_WORDS = {Risk.LOW: "niedrigem", Risk.MEDIUM: "mittlerem", Risk.HIGH: "hohem", Risk.CRITICAL: "kritischem"}


async def standing_approval_problem(
    session: AsyncSession, *, granted_by_user_id: str, risk: Risk, from_ai: bool = False
) -> tuple[str, str | None] | None:
    """None, wenn eine Dauerfreigabe von `granted_by_user_id` fuer Aktionen der Stufe `risk`
    gilt; sonst (Grund in einfachen Worten, fehlende Berechtigung oder None).

    Die Extension hat schon geprueft, ob die Freigabe zum Vorschlag passt (bei Skripten:
    unveraendert seit der Freigabe). Hier prueft der Kern, was nur er weiss: Gibt es den
    Menschen hinter der Freigabe noch, ist er aktiv, und darf er HEUTE noch Dauerfreigaben
    erteilen und Aktionen dieser Risikostufe bestaetigen? Wird ein Admin herabgestuft oder
    gesperrt, laeuft ab dem naechsten Vorschlag wieder alles ueber den Klick. Dieselbe
    Pruefung nutzt die Anzeige (`ext.context.ActionsHandle.check_standing_approval`), damit
    eine Seite nie "gilt" zeigt, wo das Gate ablehnen wuerde.

    Der Grund landet in Aktion, Protokoll und Push-Nachricht, deshalb ohne Namen von
    Berechtigungen; die Berechtigung steht getrennt daneben (fuer `gate_decision`)."""
    from ..models import User
    from ..services.auth import user_has_permission

    if from_ai:
        # Vorschlaege einer KI nie ohne Klick, egal was mitgeschickt wird.
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
    """Der Text "vom <Datum> durch <Benutzer>" -- das Datum in der eingestellten Zeitzone des
    Dashboards (wie Zeitplaene und Wartungsfenster), nicht in der des Prozesses."""
    from zoneinfo import ZoneInfo

    from ..models import User
    from .timezone import get_timezone

    try:
        zone = ZoneInfo(await get_timezone(session))
    except Exception:  # noqa: BLE001 -- ein Anzeigedatum darf den Vorschlag nie kippen
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
    """Schritte 3-6 aus docs/01 §4. `command_field` kommt vom `ActionSpec`, das der
    Aufrufer fuer `request.action_type` registriert hat (SDK-Vertrag: nur gesetzt,
    wenn der Payload einen auszufuehrenden Befehl enthaelt) -- `None` ueberspringt die
    Sperrlisten-Pruefung mangels eines zu pruefenden Feldes.

    Bei Vollautonomie laeuft die Aktion sofort im Hintergrund an;
    `wait_s` begrenzt, wie lange auf das Ergebnis gewartet wird (None = bis zum Ende,
    so bleibt `ctx.actions.propose()` fuer Extensions unveraendert). Der Status der
    Entscheidung ist dann `executing`, falls sie noch laeuft."""
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
        # Eine gueltige Dauerfreigabe ersetzt den Klick -- unabhaengig von der Automatik-
        # Einstellung. Sperrliste und Anti-Flapping (oben) gelten trotzdem.
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
        # Den Hinweis setzt das Gate erst NACH der Pruefung -- so steht "lief ohne Klick"
        # nur an Aktionen, die wirklich ohne Klick angelaufen sind.
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
                # Laeuft sie trotzdem an (Automatik "Selbststaendig handeln"), wartet sie auf
                # keinen Klick -- dann auch nicht dazu auffordern.
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
        # Bei einer Dauerfreigabe steht ihr Urheber als "bestaetigt von" an der Aktion.
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
        detail=gate_decision,
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
    """Guardierter `UPDATE ... WHERE id=? AND status=?`: schuetzt vor doppelter
    Ausfuehrung bei einem doppelten Klick/Retry (docs/04-API.md: "fuehrt aus
    (Idempotency-Key!)") ohne eine echte Idempotency-Key-Tabelle -- der Status selbst
    ist hier der Schutz. Gibt (Zeile, hat_diesen_Uebergang_gemacht) zurueck."""
    action = await session.get(Action, action_id)
    if action is None:
        return None, False
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
    """Gibt `(Zeile, hat_DIESER_Aufruf_die_Ausfuehrung_ausgeloest)` zurueck.

    Die Ausfuehrung laeuft im Hintergrund, `wait_s` begrenzt das Warten
    auf das Ergebnis (None = bis zum Ende). Laeuft sie danach noch, steht die Zeile
    auf `executing`.

    Das zweite Element ist bewusst NICHT aus dem End-`status` der Zeile ableitbar:
    ein bereits `succeeded`er Vorschlag aus einem FRUEHEREN Aufruf sieht in `status`
    identisch aus wie einer, den GENAU DIESER Aufruf gerade zum Erfolg gefuehrt hat --
    ohne das explizite Flag wuerde ein zweiter `approve()`-Versuch (Doppelklick, Retry)
    faelschlich wieder 200 statt 409 melden. Live beim Schreiben des API-Tests
    gefunden, nicht vorher bedacht."""
    now = utcnow()
    action = await session.get(Action, action_id)
    if action is None:
        return None, False

    if action.status == ActionStatus.PROPOSED.value and action.expires_at is not None and action.expires_at < now:
        action, _ = await _atomic_transition(
            session, action_id, from_status=ActionStatus.PROPOSED.value, to_status=ActionStatus.EXPIRED.value
        )
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
    """Rueckgabe wie `approve()`: `(Zeile, hat_DIESER_Aufruf_abgelehnt)`."""
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
    """Rueckgabe wie `approve()`: `(Zeile, hat_DIESER_Aufruf_verworfen)`."""
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
    """Eine Aktion, die beim Start noch als `executing` gefuehrt wird, hat
    ein Neustart mitten in der Ausfuehrung abgebrochen (z. B. ein Deploy waehrend
    `nexus_soc.upgrade`). Ohne diesen Schritt stuende sie fuer immer auf 'Laeuft' --
    `expire_stale()` kennt nur `proposed`, `services.jobs.mark_interrupted_on_boot()`
    nur JobRuns. Muss beim Start laufen, BEVOR Extensions geladen werden, damit keine
    gerade frisch gestartete Ausfuehrung mit erwischt wird (main.py-Lifespan). Je
    Aktion eine 'action.executed'-Audit-Zeile, wie sie _record_outcome() geschrieben
    haette."""
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


async def expire_stale(session: AsyncSession) -> int:
    """Unbestaetigte Vorschlaege nach Ablauf (`expires_at`, Default 24 h) als `expired`
    markieren -- bisher geschah das nur beim Klick auf "Bestaetigen"
    (es gab keine proaktive Ablauf-Ueberwachung), die
    Aktionen-Seite zeigte Tage alte Vorschlaege weiter als "Wartet". Je Vorschlag eine
    Audit-Zeile, wie bei jeder anderen Statusaenderung."""
    from sqlalchemy import select

    from ..services import audit as audit_service

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
        await audit_service.log(
            session, actor_type="system", actor_id="gate", action="action.expired", outcome="denied",
            target_type="action", target_id=row.id, reason=row.reason, correlation_id=row.correlation_id,
        )
    return expired
