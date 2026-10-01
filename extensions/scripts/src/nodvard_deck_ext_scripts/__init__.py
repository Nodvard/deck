"""scripts-Extension -- Git-versioniertes Skript-Repository, Ausfuehrung ueber das
Aktions-Gate, fleet-weiter Zeitplan ueber den Kern-Scheduler
(docs/02-EXTENSION-API.md Paragraph 6).

Setzt die urspruengliche Script-Repository-Vision um: Versionierung (Git, siehe
`repo.py`), Metadaten/Parameter (JSON-Dateien im selben Repo, siehe `repo.py`s
Docstring fuer die bewusste Abweichung von "eigene Tabellen"), das Sicherheits-Gate
(dasselbe Kern-Gate wie ueberall sonst -- kein zweites Bestaetigungs-UI), und die
Verbindung zum AI-SOC ueber den Event-Bus ("wiederkehrende Reparatur als Skript
befoerdern", siehe `promotion.py`).

**Run-Historie bewusst NICHT hier dupliziert:** `api/v1/jobs.py`s eigener Docstring
sagt es woertlich -- "GET/PATCH/DELETE /jobs/{id} und POST /jobs/{id}/run funktionieren
bereits fuer jeden Job, den eine Extension registriert hat". Ein Skript IST ein Job
(`ext_job_key="script-<id>"`); die Web-UI ruft fuer die Lauf-Historie direkt die
bestehenden Kern-Endpunkte auf (per `ext_id=scripts` gefiltert und ueber `ext_job_key`
mit der Skript-ID verknuepft), statt dass diese Extension einen zweiten, redundanten
Satz Endpunkte dafuer baut. `POST /scripts/{id}/run` existiert hier trotzdem zusaetzlich
-- NICHT als zweiter Ausfuehrungsweg (er ruft `run_script()` auf, denselben Code, den
auch der geplante Lauf nutzt), sondern weil der Kern-Endpunkt `POST /jobs/{id}/run`
keine Parameter-Ueberschreibungen im Request-Body entgegennimmt (nur die gespeicherten
`job.params`), die UI aber "einmalig mit anderen Werten ausprobieren" anbieten soll.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk import (
    Actor,
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
    WidgetSpec,
)
from nodvard_sdk.actions import REQUEST_WAIT_S, ActionRequest, ActionSpec
from nodvard_sdk.errors import ActionBlocked, NodvardError
from pydantic import BaseModel

from .params import ParamError, substitute_params
from .promotion import MAX_COUNT, RecurringFixTracker
from .repo import Script, ScriptMeta, ScriptRepo
from .skipnotice import MAX_SEND_TRIES, SkipNotices

_log = logging.getLogger(__name__)

_NEVER = "0 0 1 1 *"
"""Platzhalter-Cron fuer deaktivierte/ungeplante Skripte. Wird nie ausgewertet --
`SchedulerService.schedule()` prueft `job.enabled` VOR dem Parsen des Cron-Strings
(core/scheduler.py) -- muss aber trotzdem SYNTAKTISCH gueltig sein, falls ein Skript
spaeter per PATCH /jobs/{id} (Kern-Endpunkt) ohne neuen `register_job()`-Aufruf wieder
aktiviert wird."""


def _job_id(script_id: str) -> str:
    return f"script-{script_id}"


def _repo_path(ctx: ExtensionContext) -> Path:
    return Path(str(ctx.data_dir)) / "repo"


def _skip_notices(ctx: ExtensionContext) -> SkipNotices:
    return SkipNotices(Path(str(ctx.data_dir)) / "skip-notices.json")


async def _notify_skipped(
    ctx: ExtensionContext,
    script_id: str,
    script_name: str,
    skipped: dict[str, tuple[str, str]],
    new: set[str],
) -> bool:
    """EINE Meldung fuer die uebersprungenen Server eines Zeitplan-Laufs
    (`skipped` = {host_id: (Name, Grund)}, `new` = die Kennungen, die neu dazugekommen
    sind). Genannt wird immer die ganze Liste, Neues wird hervorgehoben. Ein Fehler beim
    Senden darf den Lauf nicht kippen; er liefert False (dann bleibt es beim alten Stand).
    Der Kern schluckt Kanalfehler (ntfy nicht erreichbar) -- `raise_on_failure` macht sie
    hier sichtbar."""
    parts = ", ".join(f"{name} ({reason})" for name, reason in skipped.values())
    if len(new) < len(skipped):
        parts += ". Neu dazugekommen: " + ", ".join(skipped[hid][0] for hid in skipped if hid in new)
    try:
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


SCRIPT_TIMEOUT_S = 30 * 60

SECRET_MASK = "••••"
"""Platzhalter fuer geheime Parameter im Befehl, der in der Aktion (Payload, API,
Ereignisse) steht. Der echte Wert wird erst im Executor aus dem Tresor geholt."""

CHANGED_SINCE_PROPOSAL = "Skript wurde seit dem Vorschlag geändert – bitte neu ausführen."


def _secret_names(params_schema: dict[str, dict[str, Any]]) -> list[str]:
    return [name for name, spec in params_schema.items() if spec.get("type") == "secret"]


class _ScriptActionExecutor:
    """Erfuellt `nodvard_sdk.capabilities.ActionExecutor` fuer `script.run`.

    Derselbe SSH-Layer wie die terminal-Extension (`ctx.exec.run`, docs/00 D-05: kein
    zweiter Ausfuehrungsweg) -- der Unterschied zu `shell.exec` ist nur die eigene
    `ActionSpec`-Identitaet, damit `promotion.py` `script.run`-Ausfuehrungen NIE erneut
    als "wiederkehrenden Fix" befoerdert (siehe dort)."""

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

        # Ohne geheime Parameter steht der fertige Befehl im Payload (wie bisher). Mit
        # geheimen steht dort nur die maskierte Fassung -- der echte Befehl entsteht erst
        # hier, damit der Klartext nie in Aktion, API oder Ereignissen landet.
        secret_params = req.payload.get("secret_params") or []
        audit_command: str | None = None
        if secret_params:
            try:
                command = await self._rebuild_command(req.payload, command, secret_params)
            except (ParamError, NodvardError) as exc:
                return ActionResult(success=False, error=str(exc))
            # Greift eine Sperrregel erst beim echten Befehl, darf die Audit-Zeile den
            # Klartext nicht enthalten: dort steht dann die maskierte Fassung.
            audit_command = req.payload["command"]
        try:
            # Skripte (Audits, Updates, Backups) laufen oft minutenlang -- die 60 s
            # Vorgabe von ctx.exec.run() liess z. B. Lynis regelmaessig scheitern.
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

    async def _rebuild_command(
        self, payload: dict[str, Any], masked_command: str, secret_params: list[str]
    ) -> str:
        """Baut den echten Befehl aus dem aktuellen Skript und den Tresor-Werten. Passt die
        maskierte Fassung des heutigen Skripts nicht zu der, die vorgeschlagen (und
        freigegeben) wurde, wird nichts ausgefuehrt."""
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
            # Kurzer Block je Geheimnis: vault_use haelt eine DB-Verbindung, solange er
            # offen ist -- waehrend des (bis zu 30 Minuten langen) Laufs darf keine offen sein.
            async with self._ctx.vault_use(handle) as value:
                values[name] = str(value)
        return substitute_params(script.content, schema, values)

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        command = req.payload.get("command", "")
        return DryRunReport(would_change=True, summary=f"Würde ausführen: {command[:200]}")


class _ScriptJobSpec:
    """Erfuellt `nodvard_sdk.context.JobSpec` strukturell (wie proxmoxs
    `_DiscoveryJobSpec`). `handler` liest das Skript bei JEDEM Lauf frisch aus dem Repo
    -- eine Zeitplanaenderung oder ein neuer Skriptinhalt braucht dadurch kein erneutes
    `register_job()`, nur einen neuen Commit (siehe `repo.py`)."""

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
        return await run_script(self._ctx, self._repo, self._script_id, param_overrides={})


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
            # `ctx.hosts.list(group=None)` filtert nicht -- ohne Gruppe liefe das
            # Skript sonst auf ALLEN Servern.
            return []
        return await ctx.hosts.list(group=group_id)
    if kind == "all":
        return await ctx.hosts.list()
    return []


def _skip_reason(host: SdkHost) -> str | None:
    """Warum ein Host bei 'Alle Server'/'Gruppe' nicht als Ziel taugt
    -- dieselben Kriterien wie nexus-socs `target_hosts()`. Ein Lauf dort wuerde
    garantiert scheitern und nur die Aktionen-Liste zumuellen."""
    if not host.is_managed:
        return "nicht verwaltet"
    if host.os_family != "linux":
        return "Windows-Server" if host.os_family == "windows" else "kein Linux-Server"
    if not host.has_credential:
        return "keine SSH-Zugangsdaten"
    return None


async def run_script(
    ctx: ExtensionContext, repo: ScriptRepo, script_id: str, *, param_overrides: dict[str, str], actor: Actor | None = None,
    wait_s: float | None = None,
) -> dict[str, Any]:
    """Loest Ziel-Hosts UND Parameter (inkl. `type="secret"` ueber `ctx.vault_use()`)
    auf und schlaegt EINE `script.run`-Aktion PRO Ziel-Host vor. Wird sowohl vom
    Kern-Scheduler (geplanter Lauf, ueber `_ScriptJobSpec.handler`) als auch von
    `POST /scripts/{id}/run` (manueller Lauf mit optionalen Ueberschreibungen)
    aufgerufen -- derselbe Pfad, unabhaengig vom Ausloeser, exakt wie nexus-socs
    `_propose_from_response()` fuer Chat UND Vorfall-Batches denselben Pfad nutzt.

    `wait_s` (nur von der HTTP-Route) ist EIN Zeitbudget fuer alle Ziel-Hosts
    zusammen -- sonst wartete ein Lauf auf fuenf Servern bis zu 5 x 20 s. None = jede
    Aktion bis zum Ende abwarten (geplanter Lauf)."""
    deadline = None if wait_s is None else time.monotonic() + wait_s
    script = repo.get(script_id)
    if script is None:
        raise NodvardError(f"Skript '{script_id}' existiert nicht (mehr) im Repository.")
    meta = script.meta
    if not meta.enabled:
        # `enabled=False` ist ein Kill-Switch, kein reiner Zeitplan-Schalter -- ein
        # automatisch angelegter Entwurf (siehe `_on_action_executed()` unten) wird
        # GENAU DESHALB deaktiviert gespeichert: er soll erst nach manueller Pruefung
        # ueberhaupt ausfuehrbar sein, weder geplant NOCH per Handauslösung.
        raise NodvardError(f"Skript '{script_id}' ist deaktiviert.")

    # Geheimnisse kommen NUR aus dem Tresor und stehen nie in der Aktion.
    # Der Vorschlag traegt den Befehl mit Platzhalter, der Executor setzt den echten Wert
    # erst beim Ausfuehren ein. Ein Wert fuer einen geheimen Parameter per Aufruf wuerde
    # sonst doch im Payload landen -- deshalb abgelehnt.
    secrets = _secret_names(meta.params_schema)
    given = sorted(name for name in secrets if name in param_overrides)
    if given:
        raise NodvardError(
            f"Geheime Parameter ({', '.join(given)}) werden nur aus dem Tresor gelesen "
            "und können nicht beim Ausführen mitgegeben werden."
        )
    targets = await _resolve_targets(ctx, meta.target)
    results: list[dict[str, Any]] = []
    if meta.target.get("kind") in ("all", "group"):
        # Ein ausdruecklich gewaehlter Server ('host') bleibt unveraendert Ziel; bei
        # Sammelzielen nur Hosts, auf denen der Lauf ueberhaupt klappen kann.
        runnable = []
        skipped: dict[str, tuple[str, str]] = {}
        for host in targets:
            reason = _skip_reason(host)
            if reason is None:
                runnable.append(host)
            else:
                results.append({"host_id": host.id, "host_name": host.name, "skipped": reason})
                skipped[host.id] = (host.name, reason)
        targets = runnable
        # Den Zeitplan nicht jede Nacht dieselben Server melden lassen --
        # einmal, dann erst wieder bei Aenderung. Bei Handausloesung (`actor`) zeigt die
        # Oberflaeche die Liste ohnehin; dort bleibt der Stand unberuehrt, sonst bliebe die
        # erste Zeitplan-Meldung aus, sobald jemand das Skript einmal von Hand getestet hat.
        if actor is None:
            notices = _skip_notices(ctx)
            new, previous = notices.update(script_id, {hid: reason for hid, (_n, reason) in skipped.items()})
            if not new or await _notify_skipped(ctx, script_id, meta.name, skipped, set(new)):
                notices.send_done(script_id)
            elif not notices.send_failed(script_id, previous):
                # Senden ging schief (z. B. ntfy nicht erreichbar): beim naechsten Lauf erneut
                # versuchen -- aber nicht endlos, jeder Versuch ist ein Eintrag im Dashboard.
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
    for host in targets:
        if command is None:
            results.append({"host_id": host.id, "host_name": host.name, "error": param_error})
            continue
        decision = await ctx.actions.propose(
            ActionRequest(
                action_type="script.run",
                host_ref=host.id,
                payload={
                    "command": command,
                    "script_id": script_id,
                    "params": values,
                    "secret_params": secrets,
                },
                risk=Risk.HIGH,
                proposed_by=actor or Actor.extension("scripts"),
                reason=f"Skript '{meta.name}' ({script_id}) ausführen",
                correlation_id=f"script:{script_id}",
            ),
            wait_s=None if deadline is None else max(0.0, deadline - time.monotonic()),
        )
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
        )


_SLUG_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def _valid_id(script_id: str) -> bool:
    return bool(script_id) and set(script_id) <= _SLUG_ALLOWED and not script_id.startswith("-")


def _target_problem(target: dict[str, Any]) -> str | None:
    """'Einer Gruppe'/'Einem Server' ohne Auswahl nicht still speichern --
    eine Gruppe ohne Kennung lief frueher auf allen Servern."""
    kind = target.get("kind")
    if kind == "group" and not target.get("group_id"):
        return "Bitte eine Gruppe wählen, auf der das Skript laufen soll."
    if kind == "host" and not target.get("host_id"):
        return "Bitte einen Server wählen, auf dem das Skript laufen soll."
    if kind not in ("host", "group", "all"):
        return "Unbekanntes Ziel – bitte „Einem Server“, „Einer Gruppe“ oder „Allen Servern“ wählen."
    return None


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._repo = ScriptRepo(_repo_path(ctx))
        self._tracker = RecurringFixTracker()
        # Bewusst KEIN vorab angelegtes Skript mehr: das fruehere
        # Lynis-Seed kam nach jedem Loeschen beim naechsten Start zurueck, lief ohne
        # sudo und doppelte nexus-socs eigenes Haertungs-Audit.

        ctx.capabilities.provide(_ScriptActionExecutor(ctx, self._repo))
        ctx.actions.register(
            ActionSpec(
                action_type="script.run",
                label="Skript ausführen",
                description="Führt ein aufgelöstes Skript aus dem Repository per SSH auf dem Host aus.",
                default_risk=Risk.HIGH,
                permissions=["hosts.execute"],
                # Nicht host-gebunden (wie backups' backup.run): sonst bietet die
                # Server-Seite script.run als Formular mit freiem Feld "command" an.
                # Gestartet wird ueber die Skript-Seite; das Gate prueft
                # host_bound nicht.
                host_bound=False,
                command_field="command",
            )
        )

        for script in self._repo.list_all():
            await ctx.scheduler.register_job(
                _ScriptJobSpec(
                    ctx,
                    self._repo,
                    script.meta.id,
                    schedule=script.meta.schedule or _NEVER,
                    enabled=bool(script.meta.enabled and script.meta.schedule),
                )
            )

        router = APIRouter()

        @router.get("/scripts")
        async def list_scripts() -> list[_ScriptOut]:
            return [_ScriptOut.from_script(s) for s in self._repo.list_all()]

        @router.get("/widgets/overview")
        async def overview_widget_data() -> dict[str, Any]:
            """Eigener Endpunkt statt `GET /scripts` direkt als `data_endpoint` --
            der generische Widget-Vertrag verlangt `{"data": ..., "meta": {}}`
            (siehe `WidgetCard.tsx`/proxmoxs `overview_widget_data()`), waehrend
            `GET /scripts` bewusst die rohe Liste liefert, die `ScriptsPage.tsx`
            direkt erwartet. Live gefunden: ohne diese Trennung zeigte das Dashboard
            den Fehler 'Antwort hat keine "data"-Eigenschaft.'"""
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
            return _ScriptOut.from_script(script)

        @router.get("/scripts/{script_id}/history")
        async def script_history(script_id: str, limit: int = 20) -> list[dict[str, Any]]:
            if self._repo.get(script_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes Skript.")
            return self._repo.history(script_id, limit=limit)

        @router.get("/scripts/{script_id}/runs")
        async def script_runs(script_id: str, limit: int = 20) -> list[dict[str, Any]]:
            """Die letzten Laeufe mit Ausgabe, Exit-Code, Ziel-Host und wer ausgeloest hat
            -- vorher sah man nach "Ausfuehren" nur "vorgeschlagen"/"erfolgreich", nie,
            was das Skript tatsaechlich ausgegeben hat."""
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
        async def save_script(script_id: str, payload: _ScriptIn, create: bool = False) -> _ScriptOut:
            """`create=true` kommt von "Neues Skript": ist die Kennung schon
            vergeben, 409 statt das bestehende Skript still zu ueberschreiben."""
            if not _valid_id(script_id):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Skript-ID darf nur a-z, 0-9 und '-' enthalten.",
                )
            problem = _target_problem(payload.target)
            if problem:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem)
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
            if payload.target.get("kind") not in ("all", "group"):
                # Kein Sammelziel mehr: ein alter Stand dürfte sonst bei einem Rückwechsel
                # eine erwartete Meldung unterdrücken.
                _skip_notices(ctx).forget(script_id)
            await ctx.scheduler.register_job(
                _ScriptJobSpec(
                    ctx, self._repo, script_id, schedule=meta.schedule or _NEVER,
                    enabled=bool(meta.enabled and meta.schedule),
                )
            )
            script = self._repo.get(script_id)
            assert script is not None
            return _ScriptOut.from_script(script)

        @router.delete("/scripts/{script_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def delete_script(script_id: str) -> None:
            script = self._repo.get(script_id)
            if script is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes Skript.")
            # Job zuerst abmelden (unschedule), DANACH aus dem Repo entfernen -- sonst
            # koennte ein zwischen beiden Schritten faelliger Lauf ein bereits
            # geloeschtes Skript lesen wollen. `run_script()` wirft in dem Fall zwar nur
            # einen sauberen NodvardError statt abzustuerzen, aber "abmelden vor
            # loeschen" vermeidet das Fenster von vornherein.
            await ctx.scheduler.register_job(
                _ScriptJobSpec(ctx, self._repo, script_id, schedule=_NEVER, enabled=False)
            )
            self._repo.delete(script_id)
            _skip_notices(ctx).forget(script_id)

        @router.post("/scripts/{script_id}/run")
        async def run_now(script_id: str, payload: _RunIn, actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:
            """Kein zweiter Ausfuehrungsweg -- ruft `run_script()` auf, denselben Code
            wie ein geplanter Lauf. Existiert zusaetzlich zu `POST /api/v1/jobs/{id}/
            run` (Kern-Endpunkt, siehe Modul-Docstring), weil letzterer keine
            Parameter-Ueberschreibungen entgegennimmt."""
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
                f"#!/bin/sh\n{command}\n",
                commit_message=f"scripts: automatischer Entwurf nach {MAX_COUNT}x Wiederholung",
            )
            await ctx.notify.send(
                Notification(
                    title="Wiederkehrende Reparatur erkannt",
                    body=(
                        f"'{command}' wurde mehrfach erfolgreich ausgeführt -- Entwurf "
                        f"'{draft_id}' im Skript-Repository angelegt (deaktiviert)."
                    ),
                    severity=Severity.INFO,
                )
            )

        ctx.events.subscribe("action.executed", _on_action_executed)

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def on_stop(self, ctx: ExtensionContext) -> None:
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        return HealthReport(healthy=True, details={"scripts": len(self._repo.list_ids())})
