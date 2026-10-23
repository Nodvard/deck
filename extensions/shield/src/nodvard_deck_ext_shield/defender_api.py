"""Anbindung des Virenschutzes an den Kern: Zeitpläne, API-Routen und die Aktionen, die durchs Gate laufen."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from nodvard_sdk import REQUEST_WAIT_S, Actor, ActionRequest, ActionResult, ActionSpec, DryRunReport, GateDecision, Notification, Risk, Severity
from pydantic import BaseModel, Field

from . import antivirus as av
from . import updates as up
from .defender import DEFAULT_CRONS, Defender, _describe, max_filesize_mb, watch_interval_min
from . import intrusion as ix
from . import realtime as rt
from .guard import Guard, GuardUnavailable, guard_interval_min
from .ids import (
    ACTION_BAN,
    ACTION_DELETE,
    ACTION_INSTALL,
    ACTION_REALTIME,
    ACTION_REBOOT,
    ACTION_RESTORE,
    ACTION_UPGRADE,
    EXT_ID,
    LOGGER,
    SOC_PATH,
)
from .patching import DEFAULT_UPDATE_CRONS, UpdateCenter, load_baselines, save_baseline

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

_NEVER = "0 0 31 2 *"  # 31. Februar: der Job existiert, läuft aber nie


class _JobSpec:
    def __init__(self, job_id: str, name: str, schedule: str, enabled: bool, handler: Any, *, fallback: str | None = None) -> None:
        self.id = job_id
        self.name = name
        self.schedule = schedule if enabled else _NEVER
        self.enabled = enabled
        self.params: dict[str, Any] = {}
        self._handler = handler
        # Standard-Zeitplan, falls der aus den Einstellungen kaputt ist (`_register_job`); None: fester Zeitplan.
        self.fallback = fallback

    async def handler(self, **_: Any) -> Any:
        return await self._handler()


async def register_jobs(ctx: ExtensionContext, defender: Defender, updates: UpdateCenter, guard: Guard | None = None) -> None:
    """Registriert die Jobs bei jedem Start und nach jeder Einstellungsänderung (Upsert über die Job-ID)."""
    s = await ctx.settings.get()

    async def quick() -> dict:
        return {"scans": await defender.start_scans(await defender.target_hosts(), "quick", paths=None, trigger="schedule")}

    async def deep() -> dict:
        return {"scans": await defender.start_scans(await defender.target_hosts(), "deep", paths=None, trigger="schedule")}

    async def watch() -> dict:
        return {"scans": await defender.start_scans(await defender.target_hosts(), "watch", paths=None, trigger="schedule")}

    async def briefing() -> dict:
        return await defender.send_briefing(await updates.overview(), await guard.summary_line() if guard else None)

    async def guard_run() -> dict:
        return await guard.inspect_all() if guard else {}

    async def audit() -> dict:
        hosts = await defender.target_hosts()
        # Wartet, bis die Audits fertig sind (sie laufen entkoppelt auf den Servern, siehe `Defender.run_audits`).
        results = await defender.run_audits(hosts, trigger="schedule")
        return {"hosts": len(hosts), "finished": len(results)}

    async def updates_check() -> dict:
        return await updates.scheduled_check()

    async def updates_auto() -> dict:
        return await updates.auto_update()

    interval = watch_interval_min(s)
    guard_interval = guard_interval_min(s)
    jobs = [
        _JobSpec("defender-quick", "Virenschutz: Schnellscan", s.get("quick_scan_cron") or DEFAULT_CRONS["quick"], s.get("quick_scan_enabled", True), quick, fallback=DEFAULT_CRONS["quick"]),
        _JobSpec("defender-deep", "Virenschutz: Tiefenscan", s.get("deep_scan_cron") or DEFAULT_CRONS["deep"], s.get("deep_scan_enabled", True), deep, fallback=DEFAULT_CRONS["deep"]),
        _JobSpec("defender-watch", "Virenschutz: Echtzeit-Wächter", f"*/{interval} * * * *", s.get("realtime_enabled", True), watch),
        _JobSpec("defender-briefing", "Morgen-Briefing", s.get("briefing_cron") or DEFAULT_CRONS["briefing"], s.get("briefing_enabled", True), briefing, fallback=DEFAULT_CRONS["briefing"]),
        _JobSpec("defender-audit", "Virenschutz: Härtungs-Audit (Lynis)", s.get("audit_cron") or DEFAULT_CRONS["audit"], s.get("audit_enabled", True), audit, fallback=DEFAULT_CRONS["audit"]),
        _JobSpec("defender-updates-check", "Updates prüfen", s.get("updates_check_cron") or DEFAULT_UPDATE_CRONS["check"],
                 s.get("updates_check_enabled", True), updates_check, fallback=DEFAULT_UPDATE_CRONS["check"]),
        _JobSpec("defender-updates-auto", "Automatische Updates", s.get("auto_updates_cron") or DEFAULT_UPDATE_CRONS["auto"],
                 s.get("auto_updates_enabled", False), updates_auto, fallback=DEFAULT_UPDATE_CRONS["auto"]),
    ]
    if guard is not None:
        jobs.append(_JobSpec("defender-guard", "Einbruchschutz & Datei-Wächter", f"*/{guard_interval} * * * *",
                             s.get("guard_enabled", True), guard_run))
    for job in jobs:
        await _register_job(ctx, job)
    # Der Zeitplaner holt einen verpassten Lauf nicht nach; die Update-Prüfung holt ausgebliebene Server selbst.
    updates.schedule_catch_up()


async def _register_job(ctx: ExtensionContext, job: _JobSpec) -> None:
    """Registriert einen Job; ein kaputter Zeitplan fällt mit Warnung und Meldung auf den Standard zurück."""
    # Sonst bräche `register_job()` das `setup()` ab und die ganze Erweiterung stünde. Die Warnung bekommt bewusst
    # keinen Traceback: Die Deploy-Prüfung sucht im Container-Log danach.
    if job.enabled and job.fallback is not None:
        try:
            ctx.scheduler.validate_schedule(job.schedule)
        except ValueError as exc:
            logging.getLogger(LOGGER).warning(
                "shield_job_schedule_invalid job=%s schedule=%r error=%s", job.id, job.schedule, exc
            )
            bad, job.schedule = job.schedule, job.fallback
            try:
                await ctx.notify.send(Notification(
                    title=f"Zeitplan „{job.name}“ ist ungültig",
                    body=(
                        f"Der Zeitplan „{bad}“ für „{job.name}“ lässt sich nicht planen ({exc}). Bis du ihn in den "
                        f"Einstellungen von Nodvard Shield korrigierst, gilt der Standard „{job.fallback}“."
                    ),
                    severity=Severity.WARNING,
                    correlation_id=f"shield-schedule:{job.id}",
                    payload={"path": SOC_PATH, "tags": ["shield"]},
                ))
            except Exception:  # noqa: BLE001 - eine ausgefallene Meldung darf den Start nicht stoppen
                logging.getLogger(LOGGER).warning("shield_schedule_notice_failed job=%s", job.id)
    await ctx.scheduler.register_job(job)


OLD_VERSION_MESSAGE = "Vorschlag stammt von einer älteren Version – bitte neu auslösen."
STALE_MESSAGE = "Der Vorschlag passt nicht (mehr) zum erwarteten Befehl – bitte neu auslösen."

# Diese Felder schreiben die /defender-Routen in den Payload; nur daraus entsteht der Befehl.
# Fehlen sie, stammt der Vorschlag aus einer älteren Version.
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    ACTION_BAN: ("ip", "jail", "unban"),
    ACTION_INSTALL: ("package",),
    ACTION_UPGRADE: ("mode", "manager", "packages"),
    ACTION_REBOOT: (),
    ACTION_RESTORE: ("finding_id",),
    ACTION_DELETE: ("finding_id",),
    ACTION_REALTIME: ("enable", "watch", "exclude", "max_mb"),
}


class ActionCommands:
    """Baut den Befehl einer Shield-Aktion nur aus geprüften Feldern, nie aus einem frei eingetippten Befehl.

    Die Routen (für `payload.command`) und der Executor (baut vor dem Ausführen neu und vergleicht) teilen sich das."""

    def __init__(self, defender: Defender) -> None:
        self._defender = defender

    async def build(self, action_type: str, host: Any, payload: dict[str, Any]) -> str:
        """ValueError mit lesbarem deutschem Grund, wenn die Angaben nicht passen."""
        required = _REQUIRED_FIELDS.get(action_type)
        if required is None:
            raise ValueError(f"Unbekannte Aktion '{action_type}'.")
        if any(field not in payload for field in required):
            raise ValueError(OLD_VERSION_MESSAGE)
        if action_type == ACTION_BAN:
            ip, jail, unban = payload["ip"], payload["jail"], payload["unban"]
            if not isinstance(ip, str):
                raise ValueError("Ungültige IP-Adresse.")
            if not isinstance(jail, str):
                raise ValueError("Ungültiger Jail-Name.")
            if not isinstance(unban, bool):
                raise ValueError("Ungültige Angabe zum Entsperren.")
            return ix.ban_command(ip, jail, unban=unban)
        if action_type == ACTION_INSTALL:
            package = payload["package"]
            if not isinstance(package, str) or package not in av.INSTALL_COMMANDS:
                raise ValueError("Unbekanntes Paket.")
            return av.INSTALL_COMMANDS[package]
        if action_type == ACTION_REBOOT:
            return up.REBOOT_COMMAND
        if action_type == ACTION_REALTIME:
            enable, watch, exclude, max_mb = _realtime_fields(payload)
            if not enable:
                return rt.disable_command()
            return rt.enable_command(watch, exclude, max_filesize_mb=max_mb)
        if action_type == ACTION_UPGRADE:
            mode, manager, packages = payload["mode"], payload["manager"], payload["packages"]
            if mode not in ("all", "security", "cleanup"):
                raise ValueError("Unbekannte Update-Art.")
            if not isinstance(manager, str) or not isinstance(packages, list) or not all(isinstance(p, str) for p in packages):
                raise ValueError("Ungültige Update-Angaben.")
            # Ob dist-upgrade gilt, steht im Plan (Payload), nicht in der heutigen Einstellung, damit Vorschlag und
            # Ausführung denselben Befehl bauen. Fehlt die Angabe, gilt der normale Befehl.
            dist_upgrade = payload.get("dist_upgrade", False)
            if not isinstance(dist_upgrade, bool):
                raise ValueError("Ungültige Update-Angaben.")
            # upgrade_command prüft den Paketmanager und filtert und quotet die Paketnamen.
            return up.upgrade_command(manager, mode, packages, dist_upgrade=dist_upgrade)
        # restore / delete: alles Wesentliche kommt aus der Fund-Zeile, nicht aus dem Payload.
        finding_id = payload["finding_id"]
        row = await self._defender.finding_record(finding_id) if isinstance(finding_id, str) else None
        if row is None:
            raise ValueError("Unbekannter Fund.")
        if row.host_id != host.id:
            raise ValueError("Der Fund gehört zu einem anderen Server.")
        if row.status != "quarantined" or not row.quarantine_path:
            raise ValueError("Die Datei liegt nicht (mehr) in der Quarantäne.")
        if action_type == ACTION_RESTORE:
            return av.restore_command(row.quarantine_path, row.path, row.original_mode)
        return av.delete_command(row.quarantine_path)

    async def helper_args(self, action_type: str, host: Any, payload: dict[str, Any]) -> list[str] | None:
        """Derselbe Auftrag als Aufruf des Hilfsskripts, oder `None` (dann läuft der Befehl aus `build` wie bisher).
        Erst beim Ausführen gefragt: `payload.command` und damit freigegebene Aktionen bleiben unverändert."""
        if action_type == ACTION_BAN:
            return ix.ban_args(payload["ip"], payload["jail"], unban=payload["unban"])
        if action_type == ACTION_INSTALL:
            return ["install", payload["package"]]
        if action_type == ACTION_REALTIME:
            enable, watch, exclude, max_mb = _realtime_fields(payload)
            return rt.helper_args(enable, watch, exclude, max_filesize_mb=max_mb)
        if action_type in (ACTION_RESTORE, ACTION_DELETE):
            row = await self._defender.finding_record(payload["finding_id"])
            if row is None or not row.quarantine_path:
                return None
            if action_type == ACTION_RESTORE:
                return av.restore_args(row.quarantine_path, row.path, row.original_mode)
            return av.delete_args(row.quarantine_path)
        return None


def _realtime_fields(payload: dict[str, Any]) -> tuple[bool, list[str], list[str], int]:
    """Die Angaben zum Umstellen des Wächters; ValueError, wenn eine nicht stimmt (wie bei allen Feldern in `build`)."""
    enable, watch, exclude, max_mb = payload["enable"], payload["watch"], payload["exclude"], payload["max_mb"]
    if not isinstance(enable, bool):
        raise ValueError("Ungültige Angabe zum Einschalten.")  # noqa: TRY004 - Route und Executor fangen ValueError
    for folders in (watch, exclude):
        if not isinstance(folders, list) or not all(isinstance(f, str) for f in folders):
            raise ValueError("Ungültige Ordnerliste.")
    if isinstance(max_mb, bool) or not isinstance(max_mb, int) or not 1 <= max_mb <= rt.MAX_FILESIZE_MB:
        raise ValueError("Ungültige Dateigröße.")
    return enable, watch, exclude, max_mb


class DefenderExecutor:
    """Führt die Aktionen aus, die nicht automatisch laufen dürfen: durchs Gate mit Freigabe, Sperrliste und Audit."""

    action_types = frozenset({
        ACTION_RESTORE, ACTION_DELETE, ACTION_INSTALL, ACTION_UPGRADE, ACTION_REBOOT, ACTION_BAN, ACTION_REALTIME,
    })

    def __init__(self, ctx: ExtensionContext, defender: Defender, updates: UpdateCenter | None = None) -> None:
        self._ctx = ctx
        self._defender = defender
        self._updates = updates
        self._commands = ActionCommands(defender)

    async def execute(self, req: ActionRequest) -> ActionResult:
        host = await self._ctx.hosts.get(req.host_ref) if req.host_ref else None
        if host is None:
            return ActionResult(success=False, error="Server nicht gefunden.")
        # Der Befehl entsteht hier neu aus den Feldern; `payload.command` wird nur verglichen, denn Gate und
        # Freigebender haben genau ihn gesehen.
        try:
            command = await self._commands.build(req.action_type, host, req.payload)
        except ValueError as exc:
            return ActionResult(success=False, error=str(exc))
        if req.payload.get("command") != command:
            return ActionResult(success=False, error=STALE_MESSAGE)
        if req.action_type in (ACTION_UPGRADE, ACTION_REBOOT):
            if self._updates is None:
                return ActionResult(success=False, error="Die Update-Zentrale ist nicht aktiv.")
            mode = "reboot" if req.action_type == ACTION_REBOOT else str(req.payload["mode"])
            # `build` hat die Angaben schon geprüft. Der Plan ist derselbe Lauf für das Hilfsskript (`upgrade_args`).
            plan = None if req.action_type == ACTION_REBOOT else up.upgrade_args(
                req.payload["manager"], mode, req.payload["packages"], dist_upgrade=req.payload.get("dist_upgrade", False),
            )
            ok, summary, output, exit_code = await self._updates.execute(host, mode, command=command, trigger="manual", plan=plan)
            return ActionResult(success=ok, exit_code=exit_code, output=output[-20000:] or summary, error=None if ok else summary)
        timeout = {ACTION_INSTALL: 30 * 60, ACTION_REALTIME: 5 * 60}.get(req.action_type, 120)
        # `build` hat die Angaben schon geprüft; das Hilfsskript bekommt dieselben, sonst bleibt es beim alten Weg.
        args = await self._commands.helper_args(req.action_type, host, req.payload)
        version = {
            ACTION_REALTIME: av.HELPER_REALTIME_VERSION,
            ACTION_RESTORE: av.HELPER_QUARANTINE_VERSION,
            ACTION_DELETE: av.HELPER_QUARANTINE_VERSION,
        }.get(req.action_type, av.HELPER_ACTION_VERSION)
        wrapped = av.via_helper(args, command, version=version) if args else av.as_root(command)
        result = await self._ctx.exec.run(host, wrapped, timeout_s=timeout)
        stdout = av.helper_text(result.stdout) if result.stdout is not None else None
        ok = result.exit_code == 0
        if req.action_type == ACTION_INSTALL:
            # Sonst bote die Seite mit dem gemerkten Stand (bis 10 Minuten) das gerade Installierte erneut an.
            self._defender.forget_status(host.id)
        if req.action_type == ACTION_REALTIME:
            # Auch nach einem Fehlschlag: clamd kann schon laufen, die gemerkte Eignung stimmt dann nicht mehr.
            self._defender.forget_readiness(host.id)
        if av.NO_ROOT in (stdout or ""):
            return ActionResult(success=False, exit_code=result.exit_code, error=av.no_root_message(stdout))
        if req.action_type == ACTION_REALTIME:
            return await self._finish_realtime(host.id, req.payload["enable"], result, stdout or "")
        if req.action_type in (ACTION_RESTORE, ACTION_DELETE):
            action = "restore" if req.action_type == ACTION_RESTORE else "delete"
            await self._defender.apply_action_result(req.payload["finding_id"], action, ok, (stdout or "") + (result.stderr or ""))
        return ActionResult(
            success=ok, exit_code=result.exit_code, output=stdout, error=result.stderr or None, duration_ms=result.duration_ms,
        )

    async def _finish_realtime(self, host_id: str, enable: bool, result: Any, stdout: str) -> ActionResult:
        """Gemerkt wird der neue Stand nur, wenn der Server die Marke des Umstellens gemeldet hat. Startete clamonacc nicht,
        hat der Server schon wieder aufgeräumt (wie beim Ausschalten): dann gilt „aus“."""
        ok = result.exit_code == 0 and rt.switched(stdout, enable)
        undone = enable and not ok and av.REALTIME_NOT_STARTED in stdout
        if ok or undone:
            state = {"enabled": enable and ok, "changed_at": datetime.now(UTC).isoformat()}
            await save_baseline(self._ctx, host_id, rt.STATE_KIND, state)
        # Die Sätze des Befehls (etwa das fehlende Paket) stehen auf stdout, nicht auf stderr.
        error = None if ok else (result.stderr or stdout).strip()[-2000:] or "Das Umstellen ist fehlgeschlagen."
        return ActionResult(success=ok, exit_code=result.exit_code, output=stdout, error=error, duration_ms=result.duration_ms)

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        return DryRunReport(would_change=True, summary=req.reason or "Virenschutz-Aktion")


# host_bound=False: Den Befehl bauen immer die /defender-Routen. Sonst erschienen die Aktionen auf der Server-Seite
# als Formular mit freiem Feld "command" (läuft als root); POST /hosts/{id}/actions lehnt sie deshalb ab.
# `ctx.actions.propose` prüft host_bound nicht. command_field bleibt, damit die Sperrliste den Befehl prüft.
ACTION_SPECS = [
    ActionSpec(action_type=ACTION_RESTORE, label="Datei aus Quarantäne wiederherstellen", default_risk=Risk.MEDIUM,
               permissions=["hosts.execute"], host_bound=False, command_field="command"),
    ActionSpec(action_type=ACTION_DELETE, label="Datei in Quarantäne endgültig löschen", default_risk=Risk.MEDIUM,
               permissions=["hosts.execute"], host_bound=False, command_field="command"),
    ActionSpec(action_type=ACTION_INSTALL, label="Virenschutz-Werkzeuge installieren/aktualisieren", default_risk=Risk.MEDIUM,
               permissions=["hosts.execute"], host_bound=False, command_field="command"),
    ActionSpec(action_type=ACTION_UPGRADE, label="System-Updates einspielen", default_risk=Risk.MEDIUM,
               permissions=["hosts.execute"], host_bound=False, command_field="command"),
    ActionSpec(action_type=ACTION_REBOOT, label="Server neu starten", default_risk=Risk.HIGH,
               permissions=["hosts.execute"], host_bound=False, command_field="command"),
    ActionSpec(action_type=ACTION_BAN, label="IP-Adresse per Fail2ban sperren/entsperren", default_risk=Risk.LOW,
               permissions=["hosts.execute"], host_bound=False, command_field="command"),
    ActionSpec(action_type=ACTION_REALTIME, label="Viren-Wächter: Echtzeit ein- oder ausschalten", default_risk=Risk.MEDIUM,
               permissions=["hosts.execute"], host_bound=False, command_field="command"),
]


class _ScanIn(BaseModel):
    kind: Literal["quick", "deep", "custom"] = "quick"
    host_ids: list[str] | Literal["all"] = "all"
    paths: list[str] | None = Field(default=None, max_length=20)


class _AuditIn(BaseModel):
    host_ids: list[str] | Literal["all"] = "all"


class _InstallIn(BaseModel):
    package: Literal["clamav", "lynis", "signatures", "fail2ban", "unattended"]


class _BanIn(BaseModel):
    ip: str
    jail: str = "sshd"
    unban: bool = False


class _RealtimeIn(BaseModel):
    enable: bool


class _UpgradeIn(BaseModel):
    mode: Literal["all", "security", "cleanup", "reboot"]


class _ProposeAllIn(BaseModel):
    """Sammel-Vorschlag: nur Einspielen (alle oder nur Sicherheitsupdates); Aufräumen und Neustart bleiben je Server."""

    mode: Literal["all", "security"]
    host_ids: list[str] | None = Field(default=None, max_length=200)
    """Nur diese Server; ohne Angabe alle, bei denen für `mode` etwas offen ist."""


PROPOSE_ALL_WAIT_S = 0.0
"""Der Sammel-Vorschlag wartet nicht auf sofort freigegebene Aktionen (`wait_s` des Gates), anders als der Einzelweg.
Mit 20 s je Server dauerte die Anfrage bei zehn Servern drei Minuten und liefe in die Zeitgrenze eines Proxys;
die Aktionen laufen im Hintergrund trotzdem an."""

# Offen im Sinne des Sammel-Vorschlags: wartet auf Freigabe, ist freigegeben oder läuft.
_OPEN_UPGRADE_STATUS = ("proposed", "approved", "executing")
# So viele eigene Aktionen holt der Sammel-Vorschlag, um offene Vorschläge zu finden (Obergrenze von `ctx.actions.list`).
_OPEN_LOOKUP_LIMIT = 200


_INSTALL_LABEL = {
    "clamav": "ClamAV installieren", "lynis": "Lynis installieren", "signatures": "Virensignaturen aktualisieren (Auto-Update an)",
    "fail2ban": "Fail2ban installieren", "unattended": "Automatische Sicherheitsupdates (unattended-upgrades) einrichten",
}


def _nothing_to_do(row: dict[str, Any], mode: str) -> str | None:
    """Warum für diesen Server (eine Zeile von `UpdateCenter.overview`) in dieser Art nichts vorgeschlagen wird, sonst None.

    Gleiche Bedingungen wie bei den Einzel-Knöpfen der Seite, nach gespeichertem Stand; es wird kein Server geprüft."""
    st = row.get("status")
    if not st:
        return "Noch nicht geprüft."
    if st.get("unsupported"):
        return "Kein Paketmanager, den die Update-Zentrale kennt."
    if not st.get("manager"):
        return st.get("error") or "Update-Stand unbekannt."
    if mode == "security" and not st.get("security_count"):
        return "Keine Sicherheitsupdates offen."
    if mode == "all" and not st.get("count"):
        return "Keine Updates offen."
    return None


NOT_PROPOSED_MESSAGE = "Der Vorschlag konnte nicht angelegt werden (Einzelheiten im Protokoll)."
"""Was der Sammel-Vorschlag bei einer unerwarteten Ausnahme meldet. Der Ausnahmetext bleibt im Protokoll,
bei einem Datenbankfehler stünden SQL und Parameter darin."""


def _client_reason(exc: BaseException) -> str:
    """Grund für die Antwort des Sammel-Vorschlags bei einer unerwarteten Ausnahme.

    Im Wortlaut kommen nur lesbare Fehler (`readable`) und Zeitüberschreitungen durch, sonst `NOT_PROPOSED_MESSAGE`."""
    if (getattr(exc, "readable", False) and str(exc).strip()) or isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return _describe(exc)
    return NOT_PROPOSED_MESSAGE


def _already_open_reason(action: Any) -> str:
    """Satz dazu, dass für den Server schon ein Update-Vorschlag offen ist (`action`: Zeile aus `ctx.actions.list`)."""
    label = up.MODE_LABEL.get(str((action.payload or {}).get("mode")), "Updates")
    if action.status == "proposed":
        return f"Vorschlag „{label}“ wartet schon auf Freigabe."
    return f"„{label}“ ist schon freigegeben und läuft."


def _realtime_state(state: dict[str, Any], down: dict[str, Any], readiness: dict[str, Any]) -> dict[str, Any]:
    """Wie der Wächter auf einem Server läuft. `realtime_requested`: eingeschaltet. `realtime_on`: läuft auch wirklich in
    Echtzeit, also weder meldet der Server clamonacc als gestoppt, noch hat der Wächter-Lauf auf den Minuten-Takt
    zurückgeschaltet (`realtime_down_since`, Zeitpunkt des Rückfalls). Ein Rückfall zählt nur für das aktuelle Einschalten
    (`down`: Zeile `rt.DOWN_KIND`)."""
    requested = bool(state.get("enabled", False))
    since = down.get("since")
    current = down.get("changed_at") == state.get("changed_at")
    down_since = since if requested and current and isinstance(since, str) else None
    running = readiness.get("clamonacc_active")
    return {
        "realtime_on": requested and running is not False and down_since is None,
        "realtime_requested": requested,
        "realtime_down_since": down_since,
    }


def _threats(n: int) -> str:
    """"1 Bedrohung", "3 Bedrohungen" statt "Bedrohung(en)"."""
    return "1 Bedrohung" if n == 1 else f"{n} Bedrohungen"


def build_routers(
    ctx: ExtensionContext, defender: Defender, updates: UpdateCenter | None = None, guard: Guard | None = None,
) -> tuple[APIRouter, APIRouter]:
    read = APIRouter(prefix="/defender")
    manage = APIRouter(prefix="/defender")

    async def _hosts(host_ids: list[str] | str) -> list:
        targets = await defender.target_hosts()
        if host_ids == "all":
            return targets
        wanted = set(host_ids)
        chosen = [h for h in targets if h.id in wanted]
        if not chosen:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Keiner der gewählten Server ist für den Virenschutz verfügbar.")
        return chosen

    commands = ActionCommands(defender)

    async def _submit(host: Any, action_type: str, fields: dict, reason: str, actor: Actor, risk: Risk,
                      *, error_status: int = status.HTTP_400_BAD_REQUEST, wait_s: float | None = REQUEST_WAIT_S) -> GateDecision:
        """Der Befehl entsteht hier aus `fields`, wie beim Ausführen; `command` dient nur Anzeige und Sperrliste."""
        try:
            command = await commands.build(action_type, host, fields)
        except ValueError as exc:
            raise HTTPException(status_code=error_status, detail=str(exc)) from exc
        return await ctx.actions.propose(ActionRequest(
            action_type=action_type, host_ref=host.id, payload={**fields, "command": command}, risk=risk,
            proposed_by=actor, reason=reason,
        ), wait_s=wait_s)

    def _proposal(decision: GateDecision, risk: Risk) -> dict:
        # `risk`: Die Seite prüft damit, ob der Nutzer selbst freigeben darf (`actions.approve:<risiko>`); sie soll
        # die Stufe nicht raten (dist-upgrade ist "hoch").
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": risk.value}

    async def _propose(host: Any, action_type: str, fields: dict, reason: str, actor: Actor, risk: Risk,
                       *, error_status: int = status.HTTP_400_BAD_REQUEST) -> dict:
        return _proposal(await _submit(host, action_type, fields, reason, actor, risk, error_status=error_status), risk)

    async def _propose_upgrade(host: Any, mode: str, actor: Actor, *, wait_s: float | None) -> tuple[GateDecision, Risk]:
        """Plan, Grund, Risiko und Vorschlag für Einspielen und Aufräumen; Einzelweg und Sammel-Vorschlag teilen sich das.

        409 mit deutschem Grund, wenn der Plan nicht aufgeht (z. B. "Keine Sicherheitsupdates offen.")."""
        assert updates is not None
        name = host.display_name or host.name
        try:
            manager, packages = await updates.plan(host, mode)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        fields: dict[str, Any] = {"mode": mode, "manager": manager, "packages": packages}
        reason = f"{up.MODE_LABEL[mode]} auf {name}"
        risk = Risk.MEDIUM
        # Ob der Server Proxmox ist, entscheidet erst der Befehl auf dem Server, daher "falls".
        # dist-upgrade darf Pakete entfernen: hohes Risiko wie ein Neustart, damit auch bei voller Autonomie (bis
        # "mittel") niemand vorbeigeht, ohne den Befehl gesehen zu haben.
        if up.wants_dist_upgrade(await ctx.settings.get(), manager, mode):
            fields["dist_upgrade"] = True
            reason += " (dist-upgrade, falls der Server Proxmox ist)"
            risk = Risk.HIGH
        # Meldungen wie "Keine Sicherheitsupdates offen." kommen aus dem Befehlsbau: 409.
        decision = await _submit(host, ACTION_UPGRADE, fields, reason, actor, risk,
                                 error_status=status.HTTP_409_CONFLICT, wait_s=wait_s)
        return decision, risk

    @read.get("/overview")
    async def overview(refresh: bool = False) -> dict:
        return await defender.overview(refresh=refresh)

    @read.get("/realtime/readiness")
    async def realtime_readiness(refresh: bool = False) -> dict:
        """Je Server: Könnte der Viren-Wächter dort in Echtzeit laufen, und läuft er schon so? Nur eine Abfrage."""
        hosts = await defender.target_hosts()
        rows = await asyncio.gather(*(defender.realtime_readiness(h, refresh=refresh) for h in hosts))
        states, downs = await load_baselines(ctx, rt.STATE_KIND), await load_baselines(ctx, rt.DOWN_KIND)
        return {"hosts": [
            {
                "host_id": h.id, "host_name": h.display_name or h.name, **row,
                **_realtime_state(states.get(h.id) or {}, downs.get(h.id) or {}, row),
            }
            for h, row in zip(hosts, rows, strict=True)
        ]}

    @read.get("/widgets/protection")
    async def protection_widget() -> dict:
        """Kachel für die Übersicht: erste Zeile Gesamtlage, danach je Server."""
        o = await defender.overview()
        sm = o["summary"]
        if sm["hosts"] == 0:
            # Ohne prüfbaren Server wäre "Schutzwert 0 / 100, keine Bedrohung" irreführend. Entweder gibt es gar keinen
            # Server oder keinen prüfbaren (Linux, verwaltet, mit SSH-Zugang, ggf. mit der eingestellten Markierung).
            if sm.get("hosts_known", 0) > 0:
                title = "Noch kein Server prüfbar"
                subtitle = ("Nodvard Shield prüft Linux-Server mit SSH-Zugang (und, falls eingestellt, der passenden Markierung). "
                            "Einstellungen → Server & Zugänge → Zugang einrichten.")
            else:
                title = "Noch kein Server"
                subtitle = "Füge zuerst einen Server hinzu (Einstellungen → Server & Zugänge)."
            # Offene Funde aus älteren Scans bleiben auch ohne Server offen und stehen in der Kachel.
            threats = sm.get("open_threats", 0)
            label, tone = (_threats(threats), "danger") if threats else ("nichts zu bewerten", "neutral")
            return {"data": [{"title": title, "subtitle": subtitle, "label": label, "tone": tone}], "meta": {}}
        rows = [{
            "title": f"Schutzwert {sm['score']} / 100",
            "subtitle": f"{sm['protected']}/{sm['hosts']} Server geschützt · {sm['quarantined']} in Quarantäne",
            "label": _threats(sm["open_threats"]) if sm["open_threats"] else "keine Bedrohung",
            "tone": "danger" if sm["open_threats"] else ("good" if sm["score"] >= 80 else "warn"),
        }]
        for h in o["hosts"]:
            scan = h.get("last_scan") or {}
            status = scan.get("status")
            # Offene Funde und das Ergebnis des letzten Scans kommen aus derselben Quelle wie die Kopfzeile (`overview`):
            # Nach Quarantäne oder Löschen steht hier nicht mehr "Bedrohung", ein offener Fund des Wächters dagegen
            # auch bei sauberem Scan.
            open_findings = int(h.get("open_findings") or 0)
            outcome = h.get("last_scan_outcome")
            done = status == "infected" and outcome in ("quarantined", "accepted", "cleaned")
            if open_findings:
                label, tone = _threats(open_findings), "danger"
            elif not h.get("clamav_installed"):
                label, tone = "ohne Virenschutz", "warn"
            elif status == "infected" and not done:
                label, tone = "Bedrohung", "danger"
            elif (status == "clean" or done) and h.get("signature_stale") is True:
                label, tone = "Signaturen veraltet", "warn"
            elif done:
                label, tone = {"quarantined": ("in Quarantäne", "warn"), "accepted": ("als harmlos markiert", "neutral")}.get(
                    outcome, ("bereinigt", "good"))
            elif status == "clean":
                label, tone = "sauber", "good"
            elif status == "running" or h.get("scanning"):
                label, tone = "Scan läuft", "accent"
            else:
                label, tone = "noch nicht gescannt", "neutral"
            audit = h.get("last_ok_audit") or h.get("last_audit") or {}
            hardening = f" · Härtung {audit['hardening_index']}" if audit.get("hardening_index") is not None else ""
            rows.append({"title": h["host_name"], "subtitle": f"ClamAV {h.get('clamav_version') or '–'}{hardening}", "label": label, "tone": tone})
        return {"data": rows, "meta": {}}

    @read.get("/updates")
    async def updates_overview() -> dict:
        assert updates is not None
        return await updates.overview()

    @read.get("/guard")
    async def guard_overview() -> dict:
        assert guard is not None
        return await guard.overview()

    @read.get("/events")
    async def events(all: bool = False) -> list[dict]:  # noqa: A002 - Query-Parameter heißt so
        assert guard is not None
        return await guard.list_events(include_acknowledged=all)

    @read.get("/widgets/security")
    async def security_widget() -> dict:
        """Kachel "Sicherheitslage": Updates, Einbruchschutz und die offenen Ereignisse."""
        rows: list[dict[str, Any]] = []
        if updates is not None:
            us = (await updates.overview())["summary"]
            if us["checked"]:
                label = f"{us['security']} Sicherheit" if us["security"] else (f"{us['packages']} offen" if us["packages"] else "aktuell")
                tone = "danger" if us["security"] else ("warn" if us["packages"] or us["reboot"] else "good")
                rows.append({"title": "Updates", "label": label, "tone": tone,
                             "subtitle": f"{us['up_to_date']}/{us['hosts']} Server aktuell" + (f" · Neustart nötig: {us['reboot']}" if us["reboot"] else "")})
        if guard is not None:
            go = await guard.overview()
            gs = go["summary"]
            if any(h["view"] for h in go["hosts"]):
                rows.append({
                    "title": "Einbruchschutz", "label": f"{gs['open_events']} Ereignis(se)" if gs["open_events"] else "ruhig",
                    "tone": "danger" if gs["open_events"] else "good",
                    "subtitle": f"{gs['failed_24h']} SSH-Fehlversuche (24 Std.) · {gs['banned']} gesperrt · Fail2ban {gs['fail2ban_running']}/{gs['hosts']}"
                    + (f" ({gs['fail2ban_unreadable']} nicht lesbar)" if gs.get("fail2ban_unreadable") else ""),
                })
                tone_by = {"critical": "danger", "warning": "warn", "info": "accent"}
                for ev in (await guard.list_events(limit=4)):
                    rows.append({"title": ev["title"], "subtitle": ev["host_name"], "label": ev["kind_label"], "tone": tone_by.get(ev["severity"], "neutral")})
        return {"data": rows, "meta": {}}

    @read.get("/scans")
    async def scans(
        host: str | None = None, limit: int = Query(100, ge=1, le=1000), include_watch: bool = False
    ) -> list[dict]:
        return await defender.list_scans(host=host, limit=max(1, min(limit, 500)), include_watch=include_watch)

    @read.get("/findings")
    async def findings(status_filter: str | None = None) -> list[dict]:
        return await defender.list_findings(status=status_filter)

    @read.get("/audits")
    async def audits() -> list[dict]:
        return await defender.latest_audits()

    @manage.post("/scans")
    async def start_scan(payload: _ScanIn) -> dict:
        given = [p.strip() for p in payload.paths or [] if p.strip()]
        if payload.kind == "custom" and not given:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Für einen eigenen Scan bitte mindestens einen Ordner angeben.")
        relative = [p for p in given if not p.startswith("/")]
        if relative:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ordner bitte mit vollem Pfad ab „/“ angeben, zum Beispiel /var/www. Nicht so: {', '.join(relative[:3])}.",
            )
        paths = given or None
        ids = await defender.start_scans(await _hosts(payload.host_ids), payload.kind, paths=paths, trigger="manual")
        return {"scans": ids}

    @manage.post("/updates/check")
    async def updates_check(payload: _AuditIn) -> dict:
        assert updates is not None
        return {"hosts": updates.start_check(await _hosts(payload.host_ids))}

    @manage.post("/guard/check")
    async def guard_check(payload: _AuditIn) -> dict:
        assert guard is not None
        return {"hosts": guard.start(await _hosts(payload.host_ids))}

    @manage.post("/events/acknowledge-all")
    async def acknowledge_all(host: str | None = None, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
        assert guard is not None
        done, kept = await guard.acknowledge_all(actor, host_id=host)
        return {"acknowledged": done, "kept_open": kept}

    @manage.post("/events/{event_id}/acknowledge")
    async def acknowledge(event_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
        assert guard is not None
        try:
            done = await guard.acknowledge(event_id, actor)
        except GuardUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        if not done:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ereignis unbekannt oder schon erledigt.")
        return {"ok": True}

    @manage.post("/hosts/{host_id}/ban")
    async def ban(host_id: str, payload: _BanIn, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
        host = await ctx.hosts.get(host_id)
        if host is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Server.")
        ip = payload.ip.strip()
        verb = "entsperren" if payload.unban else "sperren"
        return await _propose(host, ACTION_BAN, {"ip": ip, "jail": payload.jail, "unban": payload.unban},
                              f"{ip} auf {host.display_name or host.name} {verb} (Fail2ban)", actor, Risk.LOW)

    @manage.post("/hosts/{host_id}/upgrade")
    async def upgrade(host_id: str, payload: _UpgradeIn, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
        assert updates is not None
        host = await ctx.hosts.get(host_id)
        if host is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Server.")
        if payload.mode == "reboot":
            return await _propose(host, ACTION_REBOOT, {}, f"Neustart von {host.display_name or host.name}", actor, Risk.HIGH)
        decision, risk = await _propose_upgrade(host, payload.mode, actor, wait_s=REQUEST_WAIT_S)
        return _proposal(decision, risk)

    propose_all_running = False

    async def _open_upgrades() -> dict[str, Any]:
        """Je Server die neueste offene Aktion `ACTION_UPGRADE` (wartet auf Freigabe, freigegeben oder läuft).

        Quelle: die jüngsten eigenen Aktionen (`ctx.actions.list`, auch mit alter Kennung); Abgelaufene zählen nicht."""
        now = datetime.now(UTC)
        found: dict[str, Any] = {}
        for row in await ctx.actions.list(limit=_OPEN_LOOKUP_LIMIT):  # neueste zuerst
            if row.action_type != ACTION_UPGRADE or not row.host_id or row.status not in _OPEN_UPGRADE_STATUS:
                continue
            until = row.expires_at
            if row.status == "proposed" and until is not None:
                if until.tzinfo is None:
                    until = until.replace(tzinfo=UTC)
                if until < now:
                    continue
            found.setdefault(row.host_id, row)
        return found

    @manage.post("/updates/propose-all")
    async def propose_all(payload: _ProposeAllIn, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
        """Legt für viele Server je einen Update-Vorschlag an; freigegeben wird wie sonst unter "Aktionen".

        Ohne SSH und ohne Warten (`PROPOSE_ALL_WAIT_S`); Server ohne gespeicherten Stand werden übersprungen."""
        nonlocal propose_all_running
        assert updates is not None
        mode = payload.mode
        if propose_all_running:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Es werden gerade schon Vorschläge angelegt – bitte einen Moment warten.")
        propose_all_running = True
        try:
            rows = {r["host_id"]: r for r in (await updates.overview())["hosts"]}
            if payload.host_ids is None:
                chosen: list[str] = [hid for hid, r in rows.items() if _nothing_to_do(r, mode) is None]
            else:
                chosen = list(dict.fromkeys(payload.host_ids))
            open_rows = await _open_upgrades() if chosen else {}
            results: list[dict[str, Any]] = []

            for host_id in chosen:
                row = rows.get(host_id)
                if row is None:
                    results.append({"host_id": host_id, "name": host_id, "result": "skipped",
                                    "reason": "Unbekannter Server oder nicht für die Update-Zentrale verfügbar."})
                    continue
                name = row["host_name"]
                out: dict[str, Any] = {"host_id": host_id, "name": name}
                results.append(out)
                skip = _nothing_to_do(row, mode)
                if skip is None and row.get("busy"):
                    skip = "Es läuft gerade ein Einspiel-Lauf."
                if skip is None and host_id in open_rows:
                    skip = _already_open_reason(open_rows[host_id])
                if skip is not None:
                    out.update(result="skipped", reason=skip)
                    continue
                try:
                    host = await ctx.hosts.get(host_id)
                    if host is None:
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server nicht mehr bekannt.")
                    decision, risk = await _propose_upgrade(host, mode, actor, wait_s=PROPOSE_ALL_WAIT_S)
                except HTTPException as exc:
                    out.update(result="failed", reason=str(exc.detail))
                    continue
                except Exception as exc:  # noqa: BLE001 - ein Server darf die Vorschläge der anderen nicht abbrechen
                    logging.getLogger(LOGGER).exception("shield_propose_all_failed host=%s", host_id)
                    out.update(result="failed", reason=_client_reason(exc))
                    continue
                if decision.status.value == "denied":
                    # Sperrliste oder Anti-Flapping des Gates: ein Eintrag steht trotzdem unter "Aktionen".
                    out.update(result="failed", reason=decision.detail or "Das Gate hat den Vorschlag abgelehnt.",
                               action_id=decision.action_id, status=decision.status.value, risk=risk.value)
                    continue
                out.update(result="proposed", **_proposal(decision, risk))

            counts = {kind: sum(1 for r in results if r["result"] == kind) for kind in ("proposed", "skipped", "failed")}
            counts["total"] = len(results)
            try:
                await ctx.audit.log(
                    action=f"{EXT_ID}.updates_proposed_all",
                    outcome="failure" if counts["failed"] and not counts["proposed"] else "success",
                    reason=f"{up.MODE_LABEL[mode]}: {counts['proposed']} vorgeschlagen, {counts['skipped']} übersprungen, "
                           f"{counts['failed']} fehlgeschlagen",
                    detail={"mode": mode, **counts, "host_ids": [r["host_id"] for r in results if r["result"] == "proposed"]},
                    actor=actor,
                )
            except Exception:  # noqa: BLE001 - Vorschläge stehen schon unter "Aktionen", das Protokoll darf nichts kippen
                logging.getLogger(LOGGER).exception("shield_propose_all_audit_failed")
            return {"mode": mode, "results": results, "counts": counts}
        finally:
            propose_all_running = False

    briefing_running = False

    @manage.post("/briefing")
    async def briefing_now() -> dict:
        """Lagebericht jetzt. Dauert bei einem nicht erreichbaren Server bis ~20 s;
        ein zweiter Klick (auch aus einem anderen Tab) legt kein zweites Briefing an."""
        nonlocal briefing_running
        if briefing_running:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Der Lagebericht wird gerade schon erstellt – bitte einen Moment warten.")
        briefing_running = True
        try:
            return await defender.send_briefing(
                await updates.overview() if updates is not None else None, await guard.summary_line() if guard is not None else None,
            )
        finally:
            briefing_running = False

    @manage.post("/audits")
    async def start_audit(payload: _AuditIn) -> dict:
        """Startet je Server ein Audit und antwortet sofort: `started` (neu), `running` (lief schon, es startet kein
        zweites) und `audits` (je Server seit wann, für "läuft seit ...")."""
        hosts = await _hosts(payload.host_ids)
        return {"hosts": len(hosts), **await defender.start_audits(hosts, trigger="manual")}

    @manage.post("/findings/{finding_id}/quarantine")
    async def quarantine(finding_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
        ok, message = await defender.quarantine(finding_id, actor=actor)
        if not ok:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
        return {"ok": True}

    @manage.post("/findings/{finding_id}/ignore")
    async def ignore(finding_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
        if not await defender.ignore(finding_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dieser Fund ist nicht (mehr) offen.")
        await ctx.audit.log(action=f"{EXT_ID}.finding_ignored", outcome="success", reason=finding_id, actor=actor)
        return {"ok": True}

    @manage.post("/findings/{finding_id}/reopen")
    async def reopen(finding_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
        ok, message = await defender.reopen(finding_id)
        if not ok:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
        await ctx.audit.log(action=f"{EXT_ID}.finding_reopened", outcome="success", reason=finding_id, actor=actor)
        return {"ok": True}

    @manage.post("/findings/{finding_id}/{action}")
    async def restore_or_delete(finding_id: str, action: Literal["restore", "delete"], actor: Actor = Depends(ctx.api.current_actor)) -> dict:
        f = await defender.finding(finding_id)
        if f is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Fund.")
        if f["status"] != "quarantined" or not f["quarantine_path"]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Die Datei liegt nicht in der Quarantäne.")
        host = await ctx.hosts.get(f["host_id"])
        if host is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server nicht mehr bekannt.")
        if action == "restore":
            return await _propose(host, ACTION_RESTORE, {"finding_id": finding_id},
                                  f"Wiederherstellen: {f['path']} ({f['signature']})", actor, Risk.MEDIUM,
                                  error_status=status.HTTP_409_CONFLICT)
        return await _propose(host, ACTION_DELETE, {"finding_id": finding_id},
                              f"Endgültig löschen: {f['path']} ({f['signature']})", actor, Risk.MEDIUM,
                              error_status=status.HTTP_409_CONFLICT)

    @manage.post("/realtime/{host_id}")
    async def realtime_switch(host_id: str, payload: _RealtimeIn, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
        """Schlägt vor, den Wächter auf dem Server in Echtzeit laufen zu lassen oder zurück auf den Minuten-Takt."""
        host = await ctx.hosts.get(host_id)
        if host is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Server.")
        name = host.display_name or host.name
        if payload.enable:
            ready = await defender.realtime_readiness(host, refresh=True)
            if not ready.get("possible"):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=" ".join(ready.get("reasons") or []))
            if not ready.get("clamd_installed"):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"{av.REALTIME_NO_PACKAGE} Bitte zuerst installieren; Nodvard Deck installiert es nicht selbst.",
                )
        settings = await ctx.settings.get()
        fields = {
            "enable": payload.enable,
            "watch": settings.get("watch_paths") or av.WATCH_PATHS,
            "exclude": settings.get("exclude_paths") or [],
            "max_mb": max_filesize_mb(settings),
        }
        reason = f"Viren-Wächter auf {name} in Echtzeit" if payload.enable else f"Viren-Wächter auf {name} zurück auf Intervall"
        return await _propose(host, ACTION_REALTIME, fields, reason, actor, Risk.MEDIUM)

    @manage.post("/hosts/{host_id}/install")
    async def install(host_id: str, payload: _InstallIn, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
        host = await ctx.hosts.get(host_id)
        if host is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Server.")
        risk = Risk.LOW if payload.package == "signatures" else Risk.MEDIUM
        return await _propose(host, ACTION_INSTALL, {"package": payload.package},
                              f"{_INSTALL_LABEL[payload.package]} auf {host.display_name or host.name}", actor, risk)

    return read, manage
