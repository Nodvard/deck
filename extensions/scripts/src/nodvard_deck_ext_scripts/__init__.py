"""scripts-Extension: Git-versioniertes Skript-Repository, Ausführung über das Aktions-Gate, Zeitplan über den
Kern-Scheduler."""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk import (
    Actor,
    ActorType,
    ActionResult,
    DryRunReport,
    Event,
    ExtensionContext,
    GridSize,
    HealthReport,
    HostToolSpec,
    Host as SdkHost,
    NodvardExtension,
    ListItem,
    ListView,
    Notification,
    PageSpec,
    Refresh,
    Risk,
    Severity,
    StandingApproval,
    WidgetSpec,
    current_job_trigger,
)
from nodvard_sdk.actions import REQUEST_WAIT_S, ActionRequest, ActionSpec, ActionStatus, GateOutcome
from nodvard_sdk.errors import ActionBlocked, NodvardError
from pydantic import BaseModel

from .params import ParamError, substitute_params
from .promotion import MAX_COUNT, RecurringFixTracker, draft_content
from .repo import MAX_ID_LENGTH, Script, ScriptMeta, ScriptRepo
from .failnotice import FailureNotices
from .skipnotice import MAX_SEND_TRIES, SkipNotices
from .standing import (
    StandingApprovals,
    StandingRecord,
    evaluate,
    port_label,
    script_fingerprint,
    targets_fingerprint,
)

_log = logging.getLogger(__name__)

# Platzhalter-Cron für deaktivierte oder ungeplante Skripte. Der Scheduler prüft `job.enabled` vor dem Parsen, der Wert
# muss aber gültig sein, falls ein Skript später per PATCH /jobs/{id} ohne neues `register_job()` aktiviert wird.
_NEVER = "0 0 1 1 *"


# Ein Skript ist ein Job des Kerns (`ext_job_key="script-<id>"`); die Lauf-Historie liefern die Kern-Endpunkte unter /jobs.
def _job_id(script_id: str) -> str:
    return f"script-{script_id}"


def _repo_path(ctx: ExtensionContext) -> Path:
    return Path(str(ctx.data_dir)) / "repo"


def _skip_notices(ctx: ExtensionContext) -> SkipNotices:
    return SkipNotices(Path(str(ctx.data_dir)) / "skip-notices.json")


def _failure_notices(ctx: ExtensionContext) -> FailureNotices:
    return FailureNotices(Path(str(ctx.data_dir)) / "failure-notices.json")


def _standing_approvals(ctx: ExtensionContext) -> StandingApprovals:
    return StandingApprovals(Path(str(ctx.data_dir)) / "standing-approvals.json")


# Fehlertext, wenn die Dauerfreigabe direkt vor dem Befehl nicht mehr gilt.
STANDING_WITHDRAWN = "Dauerfreigabe wurde zurückgezogen – nichts ausgeführt."

# Berechtigung zum Erteilen und Zurückziehen (eingebaut nur Owner und Admin) und zugleich die der Erweiterung,
# Vorschläge mit Dauerfreigabe einzureichen.
STANDING_PERMISSION = "actions.standing_approval"


async def _expire_standing(
    ctx: ExtensionContext, script_id: str, script_name: str, reason: str, *,
    actor: Actor | None = None, notify: bool = True,
) -> bool:
    """Lässt die Dauerfreigabe erlöschen (Protokollzeile, Eintrag löschen, eine Push-Nachricht). True, wenn es eine gab."""
    # Fail closed: Ab `block` gilt sie nicht mehr, auch wenn das Protokoll oder das Löschen scheitert; der nächste Lauf
    # holt es nach, ohne doppelte Zeile oder Push. Gelöscht wird erst nach dem Protokoll, sonst ginge das Erlöschen ohne
    # Protokoll verloren.
    store = _standing_approvals(ctx)
    record = store.get_raw(script_id)
    if record is None:
        store.unblock(script_id)
        return False
    pending = store.block(script_id, record, reason)
    if not pending.logged:
        try:
            await ctx.audit.log(
                action="scripts.standing_approval.expired",
                outcome="success",
                target_type="script",
                target_id=script_id,
                reason=f"Dauerfreigabe für Skript „{script_name}“ erloschen: {pending.reason}",
                detail={
                    "granted_by": record.granted_by_user_id,
                    "granted_by_label": record.granted_by_label,
                    "granted_at": record.granted_at,
                },
                correlation_id=f"script:{script_id}",
                actor=actor,
            )
        except Exception:  # noqa: BLE001 -- der Lauf geht weiter, nur eben mit Klick
            _log.error(
                "Skript %s: Erlöschen der Dauerfreigabe konnte nicht protokolliert werden. Sie gilt "
                "trotzdem nicht mehr; der nächste Lauf versucht es erneut.",
                script_id, exc_info=True,
            )
            return True
        pending.logged = True
    try:
        store.remove(script_id)
    except OSError:
        _log.error(
            "Skript %s: Dauerfreigabe konnte nicht aus der Datei gelöscht werden. Sie gilt trotzdem "
            "nicht mehr; der nächste Lauf versucht es erneut.",
            script_id, exc_info=True,
        )
    else:
        store.unblock(script_id)
    if notify and not pending.notified:
        pending.notified = True
        try:
            await ctx.notify.send(
                Notification(
                    title=f"Dauerfreigabe für „{script_name}“ erloschen",
                    body=(
                        f"{pending.reason} Die geplanten Läufe von „{script_name}“ brauchen ab jetzt wieder "
                        "deine Freigabe unter „Aktionen“. Eine neue Dauerfreigabe kannst du auf der "
                        "Skripte-Seite erteilen."
                    ),
                    severity=Severity.INFO,
                    correlation_id=f"scripts-standing:{script_id}",
                    payload={"path": "/ext/scripts/scripts"},
                )
            )
        except Exception:  # noqa: BLE001 -- der Hinweis darf das Erlöschen nicht kippen
            _log.warning("Skript %s: Hinweis auf erloschene Dauerfreigabe nicht gesendet.", script_id, exc_info=True)
    elif not notify:
        pending.notified = True
    return True


async def _recheck_standing(
    ctx: ExtensionContext, repo: ScriptRepo, script_id: str, first: StandingRecord, host: SdkHost
) -> str | None:
    """Gilt die Dauerfreigabe vom Laufbeginn noch für `host`? None = ja, sonst der Grund (der Rest läuft dann mit
    Klick)."""
    # Vor jedem Server neu geprüft: Ein Lauf auf mehreren Servern dauert, Zurückziehen oder Änderungen müssen sofort
    # wirken.
    current = _standing_approvals(ctx).get(script_id)
    if current is None:
        return "Sie wurde während des Laufs zurückgezogen oder ist erloschen."
    same = (current.fingerprint, current.granted_at, current.granted_by_user_id) == (
        first.fingerprint, first.granted_at, first.granted_by_user_id,
    )
    if not same:
        return "Sie wurde während des Laufs neu erteilt."
    script = repo.get(script_id)
    fresh = await ctx.hosts.get(host.id)
    if script is None or fresh is None:
        return "Skript oder Server wurde während des Laufs gelöscht."
    evaluation = evaluate(current, script.meta, script.content, [fresh])
    if not evaluation.valid:
        problem = evaluation.problem or "Etwas Freigegebenes hat sich geändert."
        await _expire_standing(ctx, script_id, script.meta.name, problem)
        return problem
    if not evaluation.covered:
        return "Der Server gehört nicht mehr zur Dauerfreigabe."
    return None


async def _notify_skipped(
    ctx: ExtensionContext,
    script_id: str,
    script_name: str,
    skipped: dict[str, tuple[str, str]],
    new: set[str],
) -> bool:
    """Eine Meldung für die übersprungenen Server (`skipped` = {host_id: (Name, Grund)}, `new` = neu dazugekommene);
    False bei Sendefehler."""
    parts = ", ".join(f"{name} ({reason})" for name, reason in skipped.values())
    if len(new) < len(skipped):
        parts += ". Neu dazugekommen: " + ", ".join(skipped[hid][0] for hid in skipped if hid in new)
    try:
        # Der Kern schluckt Kanalfehler (z. B. ntfy nicht erreichbar); `raise_on_failure` macht sie sichtbar.
        await ctx.notify.send(
            Notification(
                title=f"Skript „{script_name}“ überspringt Server",
                body=(
                    f"Skript „{script_name}“ überspringt: {parts}. Dort läuft es nicht, "
                    "bis die Ursache behoben ist. Diese Meldung wird nicht wiederholt, bis sich die "
                    "Liste ändert (kommt sie nicht an, versucht das Dashboard es bis zu dreimal)."
                ),
                severity=Severity.INFO,
                correlation_id=f"scripts-skip:{script_id}",
                payload={"path": "/ext/scripts/scripts"},
            ),
            raise_on_failure=True,
        )
    except Exception:  # noqa: BLE001 -- der Lauf selbst ist wichtiger als der Hinweis
        _log.warning("Skript %s: Hinweis auf übersprungene Server nicht gesendet.", script_id, exc_info=True)
        return False
    return True


def _draft_id_for(fp: str) -> str:
    return f"auto-{hashlib.sha1(fp.encode('utf-8')).hexdigest()[:10]}"


# Skripte (Audits, Updates, Backups) laufen oft minutenlang; die 60 s Vorgabe von `ctx.exec.run()` ließ z. B. Lynis
# scheitern.
SCRIPT_TIMEOUT_S = 30 * 60

# Platzhalter für geheime Parameter im Befehl, der in Aktion, API und Ereignissen steht; der echte Wert kommt erst im
# Executor aus dem Tresor.
SECRET_MASK = "••••"

CHANGED_SINCE_PROPOSAL = "Skript wurde seit dem Vorschlag geändert – bitte neu ausführen."


def _secret_names(params_schema: dict[str, dict[str, Any]]) -> list[str]:
    return [name for name, spec in params_schema.items() if spec.get("type") == "secret"]


class _ScriptActionExecutor:
    """Führt `script.run` über denselben SSH-Weg wie `shell.exec` aus, nur mit eigener Aktionsart (siehe
    `promotion.py`)."""

    action_types = frozenset({"script.run"})

    def __init__(self, ctx: ExtensionContext, repo: ScriptRepo) -> None:
        self._ctx = ctx
        self._repo = repo

    async def execute(self, req: ActionRequest) -> ActionResult:
        if not req.host_ref:
            return ActionResult(success=False, error="ActionRequest.host_ref fehlt.")
        host = await self._ctx.hosts.get(req.host_ref)
        if host is None:
            return ActionResult(success=False, error=f"Host '{req.host_ref}' nicht gefunden.")
        command = req.payload.get("command")
        if not command:
            return ActionResult(success=False, error="payload.command fehlt (aufgelöster Skriptinhalt).")
        if "account" in req.payload and req.payload["account"] != host.credential_username:
            # Lief über eine Dauerfreigabe, die einem anderen Anmeldekonto galt.
            name = host.display_name or host.name
            return ActionResult(
                success=False,
                error=(
                    f"Auf „{name}“ meldet sich das Dashboard inzwischen unter einem anderen Konto an "
                    f"({req.payload['account'] or 'keins'} → {host.credential_username or 'keins'}). "
                    "Die Dauerfreigabe galt dem alten Konto, deshalb wurde nichts ausgeführt."
                ),
            )
        if "port" in req.payload and req.payload["port"] != host.credential_port:
            # Dasselbe für den SSH-Port: Ein anderer Port ist ein anderes Ziel.
            name = host.display_name or host.name
            return ActionResult(
                success=False,
                error=(
                    f"Auf „{name}“ meldet sich das Dashboard inzwischen über einen anderen SSH-Port an "
                    f"({port_label(req.payload['port'])} → {port_label(host.credential_port)}). "
                    "Die Dauerfreigabe galt dem alten Port, deshalb wurde nichts ausgeführt."
                ),
            )

        # Mit geheimen Parametern steht im Payload nur die maskierte Fassung; der echte Befehl entsteht erst hier,
        # damit der Klartext nie in Aktion, API oder Ereignissen landet.
        secret_params = req.payload.get("secret_params") or []
        audit_command: str | None = None
        if secret_params:
            try:
                command = await self._rebuild_command(req.payload, command, secret_params)
            except (ParamError, NodvardError) as exc:
                return ActionResult(success=False, error=str(exc))
            # Greift eine Sperrregel erst beim echten Befehl, darf die Audit-Zeile den Klartext nicht enthalten.
            audit_command = req.payload["command"]
        if self._standing_withdrawn(req):
            # Zwischen der Prüfung beim Vorschlagen und hier liegen Gate, Start und das Lesen aus dem Tresor; ein
            # Zurückziehen in dieser Zeit muss noch wirken.
            return ActionResult(success=False, error=STANDING_WITHDRAWN)
        try:
            if audit_command is None:
                result = await self._ctx.exec.run(host, command, timeout_s=SCRIPT_TIMEOUT_S)
            else:
                result = await self._ctx.exec.run(
                    host, command, timeout_s=SCRIPT_TIMEOUT_S, log_command=audit_command
                )
        except ActionBlocked as exc:
            # Die Meldung nennt nur Regel und Beschreibung, nie den Befehl.
            return ActionResult(success=False, error=str(exc))
        return ActionResult(
            success=result.exit_code == 0,
            exit_code=result.exit_code,
            output=result.stdout,
            error=result.stderr or None,
            duration_ms=result.duration_ms,
        )

    def _standing_withdrawn(self, req: ActionRequest) -> bool:
        """True, wenn die Aktion ohne Klick über eine Dauerfreigabe anlief, die so nicht mehr gilt."""
        # Sie gilt nicht mehr bei fehlendem Eintrag oder anderem Erteilungszeitpunkt (zurückgezogen und neu erteilt: die
        # neue Freigabe galt nicht für diesen Vorschlag). Ob ohne Klick, sagt das Gate (`req.standing_approval`).
        if req.standing_approval is None:
            return False  # Freigabe per Klick, auch wenn der Vorschlag noch Angaben der Dauerfreigabe trägt
        payload = req.payload
        script_id = payload.get("script_id")
        record = _standing_approvals(self._ctx).get(script_id) if isinstance(script_id, str) else None
        return record is None or record.granted_at != payload.get("standing_granted_at")

    async def _rebuild_command(
        self, payload: dict[str, Any], masked_command: str, secret_params: list[str]
    ) -> str:
        """Baut den echten Befehl aus dem aktuellen Skript und den Tresor-Werten; passt das Skript nicht mehr zum
        Vorschlag, wird nichts ausgeführt."""
        script_id = payload.get("script_id")
        script = self._repo.get(script_id) if isinstance(script_id, str) else None
        if script is None:
            raise NodvardError(CHANGED_SINCE_PROPOSAL)
        schema = script.meta.params_schema
        secrets = _secret_names(schema)
        if sorted(secrets) != sorted(secret_params):
            raise NodvardError(CHANGED_SINCE_PROPOSAL)
        values = {str(k): str(v) for k, v in (payload.get("params") or {}).items()}
        masked = substitute_params(script.content, schema, {**values, **dict.fromkeys(secrets, SECRET_MASK)})
        if masked != masked_command:
            raise NodvardError(CHANGED_SINCE_PROPOSAL)
        for name in secrets:
            handle = await self._ctx.secrets.get_handle(f"scripts-{script_id}-{name}")
            # Kurzer Block je Geheimnis: `vault_use` hält eine DB-Verbindung, solange er offen ist, und die darf nicht
            # den ganzen (bis zu 30 Minuten langen) Lauf offen bleiben.
            async with self._ctx.vault_use(handle) as value:
                values[name] = str(value)
        return substitute_params(script.content, schema, values)

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        command = req.payload.get("command", "")
        return DryRunReport(would_change=True, summary=f"Würde ausführen: {command[:200]}")


class _ScriptJobSpec:
    """Job für den Kern-Scheduler; `handler` liest das Skript bei jedem Lauf frisch aus dem Repo, `register_job()` ist
    nicht nötig."""

    def __init__(
        self, ctx: ExtensionContext, repo: ScriptRepo, script_id: str, *, schedule: str, enabled: bool
    ) -> None:
        self._ctx = ctx
        self._repo = repo
        self._script_id = script_id
        self.id = _job_id(script_id)
        self.name = f"Skript: {script_id}"
        self.schedule = schedule
        self.params: dict[str, Any] = {}
        self.enabled = enabled

    async def handler(self, **_: Any) -> dict[str, Any]:
        # Nur ein echter Zeitplan-Lauf darf eine Dauerfreigabe nutzen; „Jetzt ausführen“ (manual) bleibt ein normaler
        # Vorschlag.
        result = await run_script(
            self._ctx, self._repo, self._script_id, param_overrides={},
            scheduled=current_job_trigger() == "schedule",
        )
        problem = _run_problem(result)
        if problem is None:
            _failure_notices(self._ctx).forget(self._script_id)
            return result
        if current_job_trigger() == "schedule" and not _only_skipped(result):
            # Wer von Hand startet, sieht das Ergebnis selbst; übersprungene Server meldet `_notify_skipped`.
            await self._notify_failure(problem)
        # Der Scheduler zählt einen Lauf nur als fehlgeschlagen, wenn der Handler wirft; sonst gälte ein Lauf ohne
        # Wirkung als erfolgreich.
        raise NodvardError(problem)

    async def _notify_failure(self, problem: str) -> None:
        """Eine Meldung je Fehler (`FailureNotices`); ein Sendefehler darf den Lauf nicht kippen."""
        if not _failure_notices(self._ctx).claim(self._script_id, problem):
            return
        script = self._repo.get(self._script_id)
        name = script.meta.name if script is not None else self._script_id
        try:
            await self._ctx.notify.send(
                Notification(
                    title=f"Skript „{name}“ fehlgeschlagen",
                    body=(
                        f"Der geplante Lauf von „{name}“ ist fehlgeschlagen. {problem} Diese Meldung kommt erst wieder, "
                        "wenn sich der Fehler ändert oder das Skript zwischendurch durchgelaufen ist."
                    ),
                    severity=Severity.WARNING,
                    correlation_id=f"scripts-failed:{self._script_id}",
                    payload={"path": "/ext/scripts/scripts"},
                )
            )
        except Exception:  # noqa: BLE001 -- der Lauf selbst ist wichtiger als die Meldung
            _log.warning("Skript %s: Meldung über den Fehlschlag nicht gesendet.", self._script_id, exc_info=True)


def _run_problem(result: dict[str, Any]) -> str | None:
    """Warum ein Lauf trotz normaler Rückgabe von `run_script()` als fehlgeschlagen gilt (Ziel mit Fehler oder kein
    Ziel); sonst None."""
    # Übersprungene Server eines Sammelziels sind für sich kein Fehler (dafür gibt es `_notify_skipped`), wohl aber,
    # wenn alle übersprungen wurden. Nur für den Job-Handler: Die Skripte-Seite zeigt die Ergebnisse je Server selbst.
    results: list[dict[str, Any]] = result.get("results") or []
    failed = [r for r in results if "error" in r]
    if failed:
        names = ", ".join(str(r.get("host_name") or r.get("host_id")) for r in failed)
        reasons = "; ".join(dict.fromkeys(str(r["error"]) for r in failed))
        return f"Nicht ausgeführt auf {names}: {reasons}"
    # Der Zeitplan wartet auf Aktionen ohne Klick (Dauerfreigabe); scheitert eine oder lehnt das Dashboard sie ab, ist
    # der Lauf fehlgeschlagen. Die Ausgabe steht nicht im Satz, sie kann Inhalte vom Server enthalten.
    problems = []
    for wanted, text in ((ActionStatus.FAILED.value, "Auf {} ist das Skript fehlgeschlagen; die Ausgabe steht unter „Aktionen“."),
                         (ActionStatus.DENIED.value, "Auf {} hat das Dashboard den Lauf abgelehnt (siehe „Aktionen“).")):
        hosts = [str(r.get("host_name") or r.get("host_id")) for r in results if r.get("status") == wanted]
        if hosts:
            problems.append(text.format(", ".join(hosts)))
    if problems:
        return " ".join(problems)
    if not result.get("targets"):
        skipped = [f"{r.get('host_name') or r.get('host_id')} ({r['skipped']})" for r in results if "skipped" in r]
        if skipped:
            return "Das Skript lief auf keinem Server, alle wurden übersprungen: " + ", ".join(skipped) + "."
        return "Das Skript lief auf keinem Server: Es gibt keinen passenden Zielserver."
    return None


def _only_skipped(result: dict[str, Any]) -> bool:
    """Lief nirgends, weil alle Server übersprungen wurden; das meldet schon `_notify_skipped` (einmal je Liste)."""
    results: list[dict[str, Any]] = result.get("results") or []
    return not result.get("targets") and any("skipped" in r for r in results)


async def _resolve_targets(ctx: ExtensionContext, target: dict[str, Any]) -> list[SdkHost]:
    kind = target.get("kind")
    if kind == "host":
        host_id = target.get("host_id")
        if not host_id:
            return []
        host = await ctx.hosts.get(host_id)
        return [host] if host is not None else []
    if kind == "group":
        group_id = target.get("group_id")
        if not group_id:
            # `ctx.hosts.list(group=None)` filtert nicht, das Skript liefe sonst auf allen Servern.
            return []
        return await ctx.hosts.list(group=group_id)
    if kind == "all":
        return await ctx.hosts.list()
    return []


async def _runnable_targets(
    ctx: ExtensionContext, target: dict[str, Any]
) -> tuple[list[SdkHost], list[tuple[SdkHost, str]]]:
    """(Ziele, auf denen der Lauf klappen kann; übersprungene mit Grund). Ein gewählter Server ('host') bleibt immer
    Ziel."""
    targets = await _resolve_targets(ctx, target)
    if target.get("kind") not in ("all", "group"):
        return targets, []
    runnable: list[SdkHost] = []
    skipped: list[tuple[SdkHost, str]] = []
    for host in targets:
        reason = _skip_reason(host)
        if reason is None:
            runnable.append(host)
        else:
            skipped.append((host, reason))
    return runnable, skipped


def _skip_reason(host: SdkHost) -> str | None:
    """Warum ein Host bei 'Alle Server' oder 'Gruppe' nicht als Ziel taugt (wie `target_hosts()` in Nodvard Shield);
    sonst None."""
    # Ein Lauf dort scheiterte sicher und würde nur die Aktionen-Liste füllen.
    if not host.is_managed:
        return "nicht verwaltet"
    if host.os_family != "linux":
        return "Windows-Server" if host.os_family == "windows" else "kein Linux-Server"
    if not host.has_credential:
        return "keine SSH-Zugangsdaten"
    return None


async def run_script(
    ctx: ExtensionContext, repo: ScriptRepo, script_id: str, *, param_overrides: dict[str, str], actor: Actor | None = None,
    wait_s: float | None = None, scheduled: bool = False,
) -> dict[str, Any]:
    """Löst Ziele und Parameter auf und schlägt je Zielserver eine `script.run`-Aktion vor (Zeitplan und manueller
    Lauf)."""
    # `wait_s` (nur die HTTP-Route) ist ein Zeitbudget für alle Server zusammen, sonst wartete ein Lauf auf fünf Servern
    # bis zu 5 x 20 s; None wartet jede Aktion ab (geplanter Lauf). `scheduled` (nur der Zeitplan): Mit gültiger
    # Dauerfreigabe laufen die freigegebenen Ziele ohne Klick, neue Ziele bekommen die normale Freigabe, eine ungültige
    # Dauerfreigabe erlischt hier.
    deadline = None if wait_s is None else time.monotonic() + wait_s
    script = repo.get(script_id)
    if script is None:
        raise NodvardError(f"Skript '{script_id}' existiert nicht (mehr) im Repository.")
    meta = script.meta
    if not meta.enabled:
        # `enabled=False` ist ein Kill-Switch, kein reiner Zeitplan-Schalter: Ein automatisch angelegter Entwurf (siehe
        # `_on_action_executed()`) bleibt deshalb deaktiviert, bis ihn jemand geprüft hat, auch für Läufe von Hand.
        raise NodvardError(f"Skript '{script_id}' ist deaktiviert.")

    # Geheimnisse kommen nur aus dem Tresor und stehen nie in der Aktion: Der Vorschlag trägt den Befehl mit
    # Platzhalter, der Executor setzt den echten Wert erst beim Ausführen ein. Ein per Aufruf mitgegebener Wert landete
    # im Payload.
    secrets = _secret_names(meta.params_schema)
    given = sorted(name for name in secrets if name in param_overrides)
    if given:
        raise NodvardError(
            f"Geheime Parameter ({', '.join(given)}) werden nur aus dem Tresor gelesen "
            "und können nicht beim Ausführen mitgegeben werden."
        )
    targets, skipped_hosts = await _runnable_targets(ctx, meta.target)
    results: list[dict[str, Any]] = []
    if meta.target.get("kind") in ("all", "group"):
        skipped: dict[str, tuple[str, str]] = {}
        for host, reason in skipped_hosts:
            results.append({"host_id": host.id, "host_name": host.name, "skipped": reason})
            skipped[host.id] = (host.name, reason)
        # Übersprungene Server meldet der Zeitplan nur einmal und dann erst wieder bei einer Änderung, nicht jede Nacht.
        # Bei Läufen von Hand (`actor`) bleibt der Stand unberührt, sonst bliebe die erste Zeitplan-Meldung aus, sobald
        # jemand das Skript einmal getestet hat.
        if actor is None:
            notices = _skip_notices(ctx)
            new, previous = notices.update(script_id, {hid: reason for hid, (_n, reason) in skipped.items()})
            if not new or await _notify_skipped(ctx, script_id, meta.name, skipped, set(new)):
                notices.send_done(script_id)
            elif not notices.send_failed(script_id, previous):
                # Senden ging schief (z. B. ntfy nicht erreichbar): beim nächsten Lauf erneut, aber nicht endlos.
                _log.warning("Skript %s: Hinweis auf übersprungene Server nach %d Versuchen aufgegeben.", script_id, MAX_SEND_TRIES)
    for name in secrets if targets else []:
        # Fehlt das Geheimnis im Tresor, soll schon der Vorschlag scheitern.
        await ctx.secrets.get_handle(f"scripts-{script_id}-{name}")
    values = dict(param_overrides)
    param_error = ""
    try:
        command = substitute_params(
            script.content, meta.params_schema, {**values, **dict.fromkeys(secrets, SECRET_MASK)}
        )
    except ParamError as exc:
        command = None
        param_error = str(exc)
    standing: StandingApproval | None = None
    standing_record: StandingRecord | None = None
    covered: set[str] = set()
    lost: str | None = None  # warum die Dauerfreigabe für den Rest dieses Laufs nicht mehr gilt
    if scheduled and actor is None and not param_overrides and targets and command is not None:
        store = _standing_approvals(ctx)
        pending = store.pending_expiry(script_id)
        if pending is not None:
            # Ein Erlöschen aus einem vorigen Lauf ließ sich nicht protokollieren oder löschen.
            await _expire_standing(ctx, script_id, meta.name, pending.reason)
        standing_record = store.get(script_id)
        if standing_record is not None:
            evaluation = evaluate(standing_record, meta, script.content, targets)
            if not evaluation.valid:
                await _expire_standing(ctx, script_id, meta.name, evaluation.problem or "")
                standing_record = None
            else:
                covered = {h.id for h in evaluation.covered}
                standing = StandingApproval(
                    granted_by_user_id=standing_record.granted_by_user_id,
                    granted_at=datetime.fromisoformat(standing_record.granted_at),
                    label=f"Skript „{meta.name}“ ({script_id})",
                )
    for host in targets:
        if command is None:
            results.append({"host_id": host.id, "host_name": host.name, "error": param_error})
            continue
        reason = f"Skript '{meta.name}' ({script_id}) ausführen"
        use_standing = standing is not None and host.id in covered
        if use_standing and standing_record is not None:
            problem = await _recheck_standing(ctx, repo, script_id, standing_record, host)
            if problem is not None:
                lost, standing, use_standing = problem, None, False
        payload: dict[str, Any] = {
            "command": command,
            "script_id": script_id,
            "params": values,
            "secret_params": secrets,
        }
        if use_standing and standing_record is not None:
            # Konto und Port prüft der Executor direkt vor dem Ausführen noch einmal, ebenso ob genau diese
            # Dauerfreigabe (Erteilungszeitpunkt) dann noch gilt; zwischen Gate und Ausführung kann sich beides ändern.
            payload["account"] = standing_record.hosts.get(host.id)
            if host.id in standing_record.ports:
                payload["port"] = standing_record.ports[host.id]
            payload["standing_granted_at"] = standing_record.granted_at
        elif standing_record is not None and host.id not in covered:
            reason += " – neuer Server, nicht in der Dauerfreigabe: bitte einzeln freigeben"
        elif lost is not None:
            reason += f" – die Dauerfreigabe gilt nicht mehr: {lost} Bitte selbst freigeben."
        decision = await ctx.actions.propose(
            ActionRequest(
                action_type="script.run",
                host_ref=host.id,
                payload=payload,
                risk=Risk.HIGH,
                proposed_by=actor or Actor.extension("scripts"),
                reason=reason,
                correlation_id=f"script:{script_id}",
                standing_approval=standing if use_standing else None,
            ),
            wait_s=None if deadline is None else max(0.0, deadline - time.monotonic()),
        )
        if use_standing and decision.outcome == GateOutcome.REQUIRE_CONFIRMATION:
            # Das Gate hat die Freigabe nicht anerkannt (z. B. Person gesperrt oder kein Admin mehr): Dieser Lauf wartet
            # auf einen Klick, und die Freigabe erlischt.
            lost = decision.detail or "Das Dashboard hat die Freigabe nicht anerkannt."
            await _expire_standing(ctx, script_id, meta.name, lost)
            standing = None
        results.append(
            {
                "host_id": host.id,
                "host_name": host.name,
                "action_id": decision.action_id,
                "status": decision.status.value,
            }
        )
    return {"script_id": script_id, "targets": len(targets), "results": results}


class _ScriptIn(BaseModel):
    name: str
    description: str = ""
    content: str
    params_schema: dict[str, dict[str, Any]] = {}
    target: dict[str, Any] = {"kind": "host", "host_id": None}
    schedule: str | None = None
    enabled: bool = True


class _RunIn(BaseModel):
    param_overrides: dict[str, str] = {}


class _StandingIn(BaseModel):
    # Was die Seite angezeigt hat (`_ScriptOut.fingerprint`, `_ScriptOut.targets_fingerprint`). Hat sich das Skript oder
    # ein Zielserver seitdem geändert, wird nicht freigegeben: Es gilt nur, was die Person gesehen hat.
    fingerprint: str
    targets_fingerprint: str


class _StandingHostOut(BaseModel):
    id: str
    name: str
    account: str | None
    address: str | None = None
    port: int | None = None


class _StandingOut(BaseModel):
    active: bool  # False: gilt nicht mehr (`problem`) und erlischt beim nächsten Lauf
    problem: str | None = None
    granted_by_label: str
    granted_at: str
    hosts: list[_StandingHostOut]  # Server, für die die Freigabe gilt
    new_hosts: list[str] = []  # Namen neuer Zielserver, die eine normale Freigabe brauchen


class _ScriptOut(BaseModel):
    id: str
    name: str
    description: str
    content: str
    params_schema: dict[str, dict[str, Any]]
    target: dict[str, Any]
    schedule: str | None
    enabled: bool
    job_id: str
    fingerprint: str = ""  # Inhalt, Parameter, Ziel, Zeitplan (zum Erteilen einer Dauerfreigabe, siehe `_StandingIn`)
    standing_approval: _StandingOut | None = None
    # Bei aktiven Skripten mit Zeitplan: die Server, die eine Dauerfreigabe jetzt decken würde (zeigt der Dialog).
    standing_preview: list[_StandingHostOut] | None = None
    targets_fingerprint: str | None = None  # Fingerabdruck von `standing_preview`

    @classmethod
    def from_script(cls, script: Script) -> "_ScriptOut":
        return cls(
            id=script.meta.id,
            name=script.meta.name,
            description=script.meta.description,
            content=script.content,
            params_schema=script.meta.params_schema,
            target=script.meta.target,
            schedule=script.meta.schedule,
            enabled=script.meta.enabled,
            job_id=_job_id(script.meta.id),
            fingerprint=script_fingerprint(script.meta, script.content),
        )


_SLUG_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def _valid_id(script_id: str) -> bool:
    return (
        bool(script_id)
        and len(script_id) <= MAX_ID_LENGTH
        and set(script_id) <= _SLUG_ALLOWED
        and not script_id.startswith("-")
    )


def _target_problem(target: dict[str, Any]) -> str | None:
    """Meldet 'Einer Gruppe' oder 'Einem Server' ohne Auswahl, statt sie still zu speichern."""
    # Eine Gruppe ohne Kennung würde sonst auf allen Servern laufen (siehe `_resolve_targets`).
    kind = target.get("kind")
    if kind == "group" and not target.get("group_id"):
        return "Bitte eine Gruppe wählen, auf der das Skript laufen soll."
    if kind == "host" and not target.get("host_id"):
        return "Bitte einen Server wählen, auf dem das Skript laufen soll."
    if kind not in ("host", "group", "all"):
        return "Unbekanntes Ziel – bitte „Einem Server“, „Einer Gruppe“ oder „Allen Servern“ wählen."
    return None


class Extension(NodvardExtension):
    async def _register_script_job(self, ctx: ExtensionContext, script: Script) -> None:
        """Meldet das Skript beim Start an; bei ungültigem Zeitplan ohne Zeitplan, mit Warnung und Meldung."""
        # Sonst brächte `register_job()` die ganze Erweiterung zu Fall. Die Warnung ohne Traceback: Die Deploy-Prüfung
        # sucht im Container-Log danach.
        meta = script.meta
        schedule = meta.schedule or _NEVER
        enabled = bool(meta.enabled and meta.schedule)
        if meta.schedule:
            try:
                ctx.scheduler.validate_schedule(meta.schedule)
            except ValueError as exc:
                _log.warning("script_schedule_invalid script=%s schedule=%r error=%s", meta.id, meta.schedule, exc)
                if enabled:
                    await self._notify_invalid_schedule(ctx, meta, str(exc))
                schedule, enabled = _NEVER, False
        await ctx.scheduler.register_job(
            _ScriptJobSpec(ctx, self._repo, meta.id, schedule=schedule, enabled=enabled)
        )

    async def _notify_invalid_schedule(self, ctx: ExtensionContext, meta: ScriptMeta, why: str) -> None:
        try:
            await ctx.notify.send(
                Notification(
                    title=f"Skript „{meta.name}“: Zeitplan ungültig",
                    body=(
                        f"Der Zeitplan „{meta.schedule}“ von „{meta.name}“ lässt sich nicht planen ({why}). "
                        "Das Skript läuft vorerst nicht nach Zeitplan. Bitte korrigiere den Zeitplan auf der "
                        "Skripte-Seite."
                    ),
                    severity=Severity.WARNING,
                    correlation_id=f"scripts-schedule:{meta.id}",
                    payload={"path": "/ext/scripts/scripts"},
                )
            )
        except Exception:  # noqa: BLE001 - eine ausgefallene Meldung darf den Start nicht stoppen
            _log.warning("script_schedule_notice_failed script=%s", meta.id)

    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._repo = ScriptRepo(_repo_path(ctx))
        self._tracker = RecurringFixTracker()
        # Bewusst kein vorab angelegtes Skript: Es käme nach jedem Löschen beim nächsten Start zurück, liefe ohne sudo und
        # doppelte das Härtungs-Audit von Nodvard Shield.

        ctx.capabilities.provide(_ScriptActionExecutor(ctx, self._repo))
        ctx.actions.register(
            ActionSpec(
                action_type="script.run",
                label="Skript ausführen",
                description="Führt ein aufgelöstes Skript aus dem Repository per SSH auf dem Host aus.",
                default_risk=Risk.HIGH,
                permissions=["hosts.execute"],
                # Nicht host-gebunden: Sonst böte die Server-Seite `script.run` als Formular mit freiem Feld "command"
                # an. Gestartet wird über die Skripte-Seite; das Gate prüft `host_bound` nicht.
                host_bound=False,
                command_field="command",
            )
        )

        for script in self._repo.list_all():
            await self._register_script_job(ctx, script)

        router = APIRouter()

        async def _out(script: Script) -> _ScriptOut:
            out = _ScriptOut.from_script(script)
            meta = script.meta
            record = _standing_approvals(ctx).get(meta.id)
            grantable = bool(meta.schedule and meta.enabled)
            if record is None and not grantable:
                return out
            targets, _skipped = await _runnable_targets(ctx, meta.target)
            if grantable:
                out.standing_preview = [
                    _StandingHostOut(
                        id=h.id, name=h.display_name or h.name, account=h.credential_username,
                        address=h.address, port=h.credential_port,
                    )
                    for h in targets
                ]
                out.targets_fingerprint = targets_fingerprint(targets)
            if record is None:
                return out
            evaluation = evaluate(record, meta, script.content, targets)
            problem = evaluation.problem
            if evaluation.valid:
                # Dieselbe Prüfung der Person wie im Gate, sonst zeigte die Seite „gilt“, obwohl der nächste Lauf auf
                # einen Klick wartet.
                problem = await ctx.actions.check_standing_approval(record.granted_by_user_id, risk=Risk.HIGH)
            out.standing_approval = _StandingOut(
                active=problem is None,
                problem=problem,
                granted_by_label=record.granted_by_label,
                granted_at=record.granted_at,
                hosts=[
                    _StandingHostOut(
                        id=hid, name=record.host_names.get(hid, hid), account=account,
                        address=record.addresses.get(hid), port=record.ports.get(hid),
                    )
                    for hid, account in record.hosts.items()
                ],
                new_hosts=[h.display_name or h.name for h in evaluation.uncovered],
            )
            return out

        @router.get("/scripts")
        async def list_scripts() -> list[_ScriptOut]:
            return [await _out(s) for s in self._repo.list_all()]

        @router.get("/widgets/overview")
        async def overview_widget_data() -> dict[str, Any]:
            """Widget-Daten im Format `{"data": ..., "meta": {}}`; `GET /scripts` liefert dagegen die rohe Liste für die
            Seite."""
            return {
                "data": [
                    {"name": s.meta.name, "schedule": s.meta.schedule or "manuell"}
                    for s in self._repo.list_all()
                ],
                "meta": {},
            }

        @router.get("/scripts/{script_id}")
        async def get_script(script_id: str) -> _ScriptOut:
            script = self._repo.get(script_id)
            if script is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes Skript.")
            return await _out(script)

        @router.get("/scripts/{script_id}/history")
        async def script_history(script_id: str, limit: int = 20) -> list[dict[str, Any]]:
            if self._repo.get(script_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes Skript.")
            return self._repo.history(script_id, limit=limit)

        @router.get("/scripts/{script_id}/runs")
        async def script_runs(script_id: str, limit: int = 20) -> list[dict[str, Any]]:
            """Die letzten Läufe mit Ausgabe, Exit-Code, Zielserver und Auslöser."""
            if self._repo.get(script_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes Skript.")
            rows = await ctx.actions.list(correlation_id=f"script:{script_id}", limit=limit)
            proposers = await ctx.actions.proposer_labels(rows)
            names = {h.id: (h.display_name or h.name) for h in await ctx.hosts.list()}
            out = []
            for row in rows:
                result = row.result or {}
                out.append({
                    "action_id": row.id,
                    "status": row.status,
                    # Lief ohne Klick über die Dauerfreigabe; `reason` nennt, wer und wann.
                    "standing_approval": (row.gate_decision or {}).get("rule") == "standing_approval",
                    "reason": row.reason,
                    "host_id": row.host_id,
                    "host_name": names.get(row.host_id or "", row.host_id),
                    "proposed_by": f"{row.proposed_by_type}/{row.proposed_by_id}",
                    "proposed_by_label": proposers.get(row.id),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                    "exit_code": result.get("exit_code"),
                    "output": (result.get("output") or "")[-20000:],
                    "error": (result.get("error") or "")[-5000:],
                    "duration_ms": result.get("duration_ms"),
                })
            return out

        @router.put("/scripts/{script_id}")
        async def save_script(
            script_id: str, payload: _ScriptIn, create: bool = False, actor: Actor = Depends(ctx.api.current_actor)
        ) -> _ScriptOut:
            """Speichert das Skript; `create=true` ("Neues Skript") antwortet bei vergebener Kennung mit 409 statt zu
            überschreiben."""
            if not _valid_id(script_id):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Skript-ID darf nur a-z, 0-9 und '-' enthalten (höchstens {MAX_ID_LENGTH} Zeichen).",
                )
            problem = _target_problem(payload.target)
            if problem:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem)
            if payload.schedule:
                # Vor dem Speichern, sonst landete ein kaputter Zeitplan im Repo (siehe `_register_script_job`).
                try:
                    ctx.scheduler.validate_schedule(payload.schedule)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Der Zeitplan „{payload.schedule}“ ist ungültig: {exc}",
                    ) from exc
            is_new = self._repo.get(script_id) is None
            if create and not is_new:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Ein Skript mit der Kennung „{script_id}“ gibt es schon – bitte eine andere Kennung wählen.",
                )
            meta = ScriptMeta(
                id=script_id,
                name=payload.name,
                description=payload.description,
                params_schema=payload.params_schema,
                target=payload.target,
                schedule=payload.schedule,
                enabled=payload.enabled,
            )
            self._repo.save(
                meta,
                payload.content,
                commit_message=f"scripts: '{script_id}' {'angelegt' if is_new else 'aktualisiert'}",
            )
            record = _standing_approvals(ctx).get_raw(script_id)
            if record is not None and is_new:
                # Ein neu angelegtes Skript erbt nie eine alte Freigabe derselben Kennung.
                await _expire_standing(
                    ctx, script_id, meta.name, "Das Skript wurde neu angelegt.", actor=actor, notify=False
                )
            elif record is not None and record.fingerprint != script_fingerprint(meta, payload.content):
                # Inhalt, Parameter, Ziel oder Zeitplan geändert: Die Dauerfreigabe galt dem alten Stand (siehe
                # `standing.py`).
                who = actor.label or actor.id
                await _expire_standing(
                    ctx, script_id, meta.name, f"Das Skript wurde von {who} geändert.", actor=actor
                )
            if payload.target.get("kind") not in ("all", "group"):
                # Kein Sammelziel mehr: Ein alter Stand unterdrückte sonst bei einem Rückwechsel eine erwartete Meldung.
                _skip_notices(ctx).forget(script_id)
            await ctx.scheduler.register_job(
                _ScriptJobSpec(
                    ctx, self._repo, script_id, schedule=meta.schedule or _NEVER,
                    enabled=bool(meta.enabled and meta.schedule),
                )
            )
            script = self._repo.get(script_id)
            assert script is not None
            return await _out(script)

        @router.delete("/scripts/{script_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def delete_script(script_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> None:
            script = self._repo.get(script_id)
            if script is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes Skript.")
            # Erst den Job abmelden, dann aus dem Repo löschen, damit zwischen beiden Schritten kein Lauf ein
            # gelöschtes Skript lesen will.
            await ctx.scheduler.register_job(
                _ScriptJobSpec(ctx, self._repo, script_id, schedule=_NEVER, enabled=False)
            )
            self._repo.delete(script_id)
            _skip_notices(ctx).forget(script_id)
            _failure_notices(ctx).forget(script_id)
            await _expire_standing(
                ctx, script_id, script.meta.name, "Das Skript wurde gelöscht.", actor=actor, notify=False
            )

        @router.post("/scripts/{script_id}/run")
        async def run_now(script_id: str, payload: _RunIn, actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:
            """Führt das Skript wie ein geplanter Lauf aus (`run_script()`), aber mit überschriebenen Parametern."""
            # Neben `POST /api/v1/jobs/{id}/run` des Kerns, das keine Parameter-Überschreibungen entgegennimmt.
            if self._repo.get(script_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes Skript.")
            try:
                return await run_script(
                    ctx, self._repo, script_id, param_overrides=payload.param_overrides, actor=actor,
                    wait_s=REQUEST_WAIT_S,
                )
            except NodvardError as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

        ctx.api.include_router(router, permission="hosts.execute")

        # Eigener Router mit eigener Berechtigung: Dauerfreigaben erteilen und zurückziehen dürfen nur Owner und Admin,
        # alle anderen bekommen serverseitig 403.
        standing_router = APIRouter()

        @standing_router.post("/scripts/{script_id}/standing-approval")
        async def grant_standing(
            script_id: str, payload: _StandingIn, actor: Actor = Depends(ctx.api.current_actor)
        ) -> _ScriptOut:
            """Erteilt die Dauerfreigabe: Geplante Läufe auf den jetzigen Zielservern laufen ohne Klick, solange sich
            nichts ändert."""
            script = self._repo.get(script_id)
            if script is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes Skript.")
            meta = script.meta
            if actor.type != ActorType.USER:
                # Nur ein Mensch; `ctx.api.current_actor` liefert ohnehin immer den Nutzer.
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nur ein Mensch kann freigeben.")
            if not meta.schedule or not meta.enabled:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Eine Dauerfreigabe gibt es nur für aktive Skripte mit Zeitplan.",
                )
            fingerprint = script_fingerprint(meta, script.content)
            if payload.fingerprint != fingerprint:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Das Skript wurde inzwischen geändert – lade die Seite neu und prüfe es noch einmal.",
                )
            targets, _skipped = await _runnable_targets(ctx, meta.target)
            if not targets:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Das Skript hat gerade keinen Server, auf dem es laufen kann.",
                )
            if payload.targets_fingerprint != targets_fingerprint(targets):
                # Seit dem Laden der Seite hat sich die Serverliste, ein Konto oder eine Adresse geändert: Es gilt nur,
                # was die Person gesehen hat.
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Die Zielserver haben sich inzwischen geändert – lade die Seite neu und prüfe die Liste noch einmal.",
                )
            record = StandingRecord(
                fingerprint=fingerprint,
                granted_by_user_id=actor.id,
                granted_by_label=actor.label or actor.id,
                granted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                hosts={h.id: h.credential_username for h in targets},
                host_names={h.id: h.display_name or h.name for h in targets},
                addresses={h.id: h.address for h in targets},
                ports={h.id: h.credential_port for h in targets},
            )
            store = _standing_approvals(ctx)
            try:
                store.set(script_id, record)
            except OSError as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Die Dauerfreigabe konnte nicht gespeichert werden.",
                ) from exc
            try:
                await ctx.audit.log(
                    action="scripts.standing_approval.granted",
                    outcome="success",
                    target_type="script",
                    target_id=script_id,
                    reason=f"Dauerfreigabe für Skript „{meta.name}“ erteilt",
                    detail={
                        "fingerprint": fingerprint,
                        "schedule": meta.schedule,
                        "hosts": [
                            {
                                "id": h.id, "name": h.display_name or h.name, "account": h.credential_username,
                                "address": h.address, "port": h.credential_port,
                            }
                            for h in targets
                        ],
                    },
                    correlation_id=f"script:{script_id}",
                    actor=actor,
                )
            except Exception:
                # Ohne Protokollzeile keine Freigabe.
                store.remove(script_id)
                raise
            return await _out(script)

        @standing_router.delete("/scripts/{script_id}/standing-approval", status_code=status.HTTP_204_NO_CONTENT)
        async def revoke_standing(script_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> None:
            """Zieht die Freigabe zurück; wirkt sofort, auch auf die restlichen Server eines laufenden Laufs."""
            script = self._repo.get(script_id)
            if script is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes Skript.")
            store = _standing_approvals(ctx)
            record = store.get(script_id)
            if record is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Für dieses Skript gibt es keine Dauerfreigabe.")
            # Zuerst im Speicher sperren: So gilt sie sofort nicht mehr, auch wenn die Datei nicht beschreibbar ist.
            pending = store.block(script_id, record, "Sie wurde zurückgezogen.")
            stuck = False
            try:
                store.remove(script_id)
            except OSError:
                stuck = True
                _log.error(
                    "Skript %s: zurückgezogene Dauerfreigabe konnte nicht aus der Datei gelöscht werden.",
                    script_id, exc_info=True,
                )
            else:
                store.unblock(script_id)
            await ctx.audit.log(
                action="scripts.standing_approval.revoked",
                outcome="success",
                target_type="script",
                target_id=script_id,
                reason=f"Dauerfreigabe für Skript „{script.meta.name}“ zurückgezogen",
                detail={
                    "granted_by": record.granted_by_user_id,
                    "granted_by_label": record.granted_by_label,
                    "granted_at": record.granted_at,
                    **({"entry_removed": False} if stuck else {}),
                },
                correlation_id=f"script:{script_id}",
                actor=actor,
            )
            # Das Zurückziehen ist protokolliert; ein späteres Nachholen muss nur noch löschen.
            pending.logged = pending.notified = True
            if stuck:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Die Freigabe gilt ab sofort nicht mehr, ließ sich aber nicht dauerhaft löschen "
                        "(Speicher voll oder keine Schreibrechte?). Nach einem Neustart des Dashboards "
                        "bitte noch einmal zurückziehen."
                    ),
                )

        ctx.api.include_router(standing_router, permission=STANDING_PERMISSION)

        ctx.ui.register_host_tool(HostToolSpec(
            id="scripts", title="Skripte", icon="terminal-square", category="control",
            description="Gespeicherte Skripte für diesen Host ansehen und ausführen", path="/scripts?host={host_id}",
            permissions=["hosts.execute"],
        ))

        ctx.ui.register_page(
            PageSpec(
                id="scripts",
                path="/scripts",
                title="Skripte",
                icon="terminal-square",
                nav_section="Automatisierung",
                nav_order=20,
                component="ScriptsPage",
            )
        )

        ctx.ui.register_widget(
            WidgetSpec(
                id="scripts-overview",
                title="Skript-Repository",
                icon="terminal-square",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=60),
                data_endpoint="widgets/overview",
                view=ListView(
                    item=ListItem(title="{{ name }}", subtitle="{{ schedule }}"),
                    empty_text="Keine Skripte angelegt",
                ),
            )
        )

        async def _on_action_executed(event: Event) -> None:
            payload = event.payload
            fp = self._tracker.observe(
                action_type=payload.get("action_type", ""),
                host_id=payload.get("host_id"),
                outcome=payload.get("outcome", ""),
                payload=payload.get("payload") or {},
            )
            if fp is None:
                return
            command = (payload.get("payload") or {}).get("command", "")
            draft_id = _draft_id_for(fp)
            if self._repo.get(draft_id) is not None:
                return
            self._repo.save(
                ScriptMeta(
                    id=draft_id,
                    name=f"Automatischer Entwurf: {command[:60]}",
                    description=(
                        f"Automatisch angelegt, weil dieser Befehl {MAX_COUNT}x erfolgreich "
                        f"vorgeschlagen/ausgeführt wurde. Ziel/Zeitplan prüfen, bevor dieses "
                        f"Skript aktiviert wird."
                    ),
                    target={"kind": "host", "host_id": payload.get("host_id")},
                    schedule=None,
                    enabled=False,
                ),
                draft_content(command),
                commit_message=f"scripts: automatischer Entwurf nach {MAX_COUNT}x Wiederholung",
            )
            # Der Befehl steht nur im Entwurf (Skripte-Seite, `hosts.execute`): Meldungen liest auch, wer nur ansehen
            # darf, und Befehle können Zugangsdaten enthalten.
            await ctx.notify.send(
                Notification(
                    title="Wiederkehrende Reparatur erkannt",
                    body=(
                        f"Derselbe Befehl wurde {MAX_COUNT}x erfolgreich ausgeführt -- Entwurf "
                        f"'{draft_id}' im Skript-Repository angelegt (deaktiviert)."
                    ),
                    severity=Severity.INFO,
                    payload={"path": "/ext/scripts/scripts"},
                )
            )

        ctx.events.subscribe("action.executed", _on_action_executed)

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def on_stop(self, ctx: ExtensionContext) -> None:
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        return HealthReport(healthy=True, details={"scripts": len(self._repo.list_ids())})
