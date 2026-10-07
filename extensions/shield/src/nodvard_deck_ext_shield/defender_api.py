"""Anbindung des Virenschutzes an den Kern: Zeitplaene, API-Routen und die
Aktionen, die durch das Gate laufen (Wiederherstellen, Loeschen, Installieren)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk import REQUEST_WAIT_S, Actor, ActionRequest, ActionResult, ActionSpec, DryRunReport, GateDecision, Notification, Risk, Severity
from pydantic import BaseModel, Field

from . import antivirus as av
from . import updates as up
from .defender import DEFAULT_CRONS, Defender, _describe
from . import intrusion as ix
from .guard import Guard
from .ids import ACTION_BAN, ACTION_DELETE, ACTION_INSTALL, ACTION_REBOOT, ACTION_RESTORE, ACTION_UPGRADE, EXT_ID, LOGGER, SOC_PATH
from .patching import DEFAULT_UPDATE_CRONS, UpdateCenter

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

_NEVER = "0 0 31 2 *"  # 31. Februar -- Job existiert, laeuft aber nie


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
    """Bei jedem Start UND nach jeder Einstellungsaenderung -- `register_job` ist ein
    Upsert ueber die Job-ID, alte Zeitplaene werden dabei ersetzt."""
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

    interval = max(2, min(59, int(s.get("watch_interval_min") or 10)))
    guard_interval = max(5, min(59, int(s.get("guard_interval_min") or 15)))
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
    # Der Zeitplaner holt einen verpassten Lauf (Dashboard war zur Zeit aus) nicht nach: die
    # Update-Pruefung holt sich ausgebliebene Server selbst.
    updates.schedule_catch_up()


async def _register_job(ctx: ExtensionContext, job: _JobSpec) -> None:
    """Ein kaputter Zeitplan aus den Einstellungen (gespeichert von einer Version, die ihn noch nicht pruefte)
    legte frueher die ganze Erweiterung lahm: `register_job()` warf, `setup()` brach ab -- keine Scans, kein
    Waechter, kein Executor. Jetzt laeuft der Job vorerst mit seinem Standard-Zeitplan, mit Warnung (ohne
    Traceback: die Deploy-Pruefung sucht im Container-Log danach) und Meldung."""
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

# Diese Felder schreiben die /defender-Routen in den Payload; daraus (und nur daraus)
# entsteht der Befehl. Fehlen sie, stammt der Vorschlag aus einer aelteren Version.
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    ACTION_BAN: ("ip", "jail", "unban"),
    ACTION_INSTALL: ("package",),
    ACTION_UPGRADE: ("mode", "manager", "packages"),
    ACTION_REBOOT: (),
    ACTION_RESTORE: ("finding_id",),
    ACTION_DELETE: ("finding_id",),
}


class ActionCommands:
    """Baut den Befehl einer Nodvard-Shield-Aktion NUR aus strukturierten, geprueften Feldern.

    Dieselbe Funktion nutzen die Routen (fuer `payload.command`, das Anzeige und
    Sperrliste des Gates sehen) und der Executor (der den Befehl vor dem Ausfuehren neu
    baut und mit `payload.command` vergleicht). Ein frei eingetippter Befehl kommt so
    nie mehr als root auf einen Server."""

    def __init__(self, defender: Defender) -> None:
        self._defender = defender

    async def build(self, action_type: str, host: Any, payload: dict[str, Any]) -> str:
        """ValueError mit lesbarem (deutschem) Grund, wenn die Angaben nicht passen."""
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
        if action_type == ACTION_UPGRADE:
            mode, manager, packages = payload["mode"], payload["manager"], payload["packages"]
            if mode not in ("all", "security", "cleanup"):
                raise ValueError("Unbekannte Update-Art.")
            if not isinstance(manager, str) or not isinstance(packages, list) or not all(isinstance(p, str) for p in packages):
                raise ValueError("Ungültige Update-Angaben.")
            # Ob "Alle Updates" dist-upgrade nimmt, steht im Plan (Payload), nicht in
            # der Einstellung von heute -- Vorschlag und Ausfuehrung bauen dasselbe. Fehlt die
            # Angabe (aeltere Vorschlaege), gilt der bisherige Befehl.
            dist_upgrade = payload.get("dist_upgrade", False)
            if not isinstance(dist_upgrade, bool):
                raise ValueError("Ungültige Update-Angaben.")
            # upgrade_command prueft den Paketmanager und filtert/quotet die Paketnamen.
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


class DefenderExecutor:
    """Die Aktionen, die NICHT automatisch passieren duerfen: sie laufen durchs Gate
    und damit durch Freigabe, Sperrliste und Audit."""

    action_types = frozenset({
        ACTION_RESTORE, ACTION_DELETE, ACTION_INSTALL, ACTION_UPGRADE, ACTION_REBOOT, ACTION_BAN,
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
        # Der Befehl entsteht hier neu aus den Feldern -- `payload.command`
        # wird nie ausgefuehrt, nur verglichen: Das Gate hat genau ihn gegen die
        # Sperrliste geprueft, und der Freigebende hat genau ihn gesehen.
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
            ok, summary, output, exit_code = await self._updates.execute(host, mode, command=command, trigger="manual")
            return ActionResult(success=ok, exit_code=exit_code, output=output[-20000:] or summary, error=None if ok else summary)
        timeout = 30 * 60 if req.action_type == ACTION_INSTALL else 120
        result = await self._ctx.exec.run(host, av.as_root(command), timeout_s=timeout)
        ok = result.exit_code == 0
        if req.action_type == ACTION_INSTALL:
            # Was installiert ist, steht im gemerkten Stand des Servers: sonst bot die Seite bis zu
            # 10 Minuten lang an, das gerade Installierte noch einmal zu installieren.
            self._defender.forget_status(host.id)
        if av.NO_ROOT in (result.stdout or ""):
            return ActionResult(success=False, exit_code=result.exit_code, error=av.NO_ROOT_MESSAGE)
        if req.action_type in (ACTION_RESTORE, ACTION_DELETE):
            action = "restore" if req.action_type == ACTION_RESTORE else "delete"
            await self._defender.apply_action_result(req.payload["finding_id"], action, ok, (result.stdout or "") + (result.stderr or ""))
        return ActionResult(
            success=ok, exit_code=result.exit_code, output=result.stdout, error=result.stderr or None, duration_ms=result.duration_ms,
        )

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        return DryRunReport(would_change=True, summary=req.reason or "Virenschutz-Aktion")


# host_bound=False: Den Befehl bauen immer die /defender-Routen (ActionCommands). Auf
# der Server-Seite (GET /hosts/{id}/actions) erschienen diese Aktionen sonst als
# Formular mit freiem Feld "command", das als root laeuft; POST /hosts/{id}/actions
# lehnt sie deshalb ab. ctx.actions.propose prueft host_bound nicht.
# command_field bleibt, damit die Sperrliste des Gates den gebauten Befehl prueft.
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


class _UpgradeIn(BaseModel):
    mode: Literal["all", "security", "cleanup", "reboot"]


class _ProposeAllIn(BaseModel):
    """Sammel-Vorschlag: nur Einspielen (alle Updates oder nur Sicherheitsupdates), kein Aufraeumen und kein
    Neustart -- beides bleibt eine bewusste Entscheidung je Server."""

    mode: Literal["all", "security"]
    host_ids: list[str] | None = Field(default=None, max_length=200)
    """Nur diese Server. Ohne Angabe: alle Server, bei denen fuer `mode` etwas offen ist."""


PROPOSE_ALL_WAIT_S = 0.0
"""Beim Sammel-Vorschlag wartet kein Vorschlag auf das Ergebnis einer sofort freigegebenen Aktion (`wait_s` des Gates).
Das Warten gibt es nur bei "Selbststaendig handeln": Der Einzelweg zeigt dem Klickenden das Ergebnis (bis
`REQUEST_WAIT_S`), der Sammelweg verweist ohnehin auf "Aktionen". Mit 20 s je Server dauerte die Anfrage bei zehn
Servern drei Minuten und liefe in die Zeitgrenze eines Proxys; die Aktionen laufen im Hintergrund trotzdem an."""

# Offen im Sinne des Sammel-Vorschlags: wartet auf Freigabe, ist freigegeben oder laeuft.
_OPEN_UPGRADE_STATUS = ("proposed", "approved", "executing")
# So viele eigene Aktionen holt der Sammel-Vorschlag, um offene Vorschlaege zu finden (Obergrenze von `ctx.actions.list`).
_OPEN_LOOKUP_LIMIT = 200


_INSTALL_LABEL = {
    "clamav": "ClamAV installieren", "lynis": "Lynis installieren", "signatures": "Virensignaturen aktualisieren (Auto-Update an)",
    "fail2ban": "Fail2ban installieren", "unattended": "Automatische Sicherheitsupdates (unattended-upgrades) einrichten",
}


def _nothing_to_do(row: dict[str, Any], mode: str) -> str | None:
    """Warum fuer diesen Server der Update-Uebersicht (eine Zeile von `UpdateCenter.overview`) in dieser Art nichts
    vorgeschlagen wird -- None, wenn etwas offen ist. Dieselben Bedingungen wie bei den Einzel-Knoepfen der Seite
    ("Sicherheitsupdates einspielen" bei `security_count`, "Alle einspielen" bei `count`). Es geht nur um den
    gespeicherten Stand: Der Sammel-Vorschlag prueft keinen Server selbst."""
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
"""Was der Sammel-Vorschlag bei einer unerwarteten Ausnahme an den Client schreibt. Der Ausnahmetext selbst bleibt im
Protokoll des Dashboards: bei einem Datenbankfehler stuenden SQL-Text und Parameter darin."""


def _client_reason(exc: BaseException) -> str:
    """Grund fuer die Antwort des Sammel-Vorschlags zu einer unerwarteten Ausnahme. Im Wortlaut kommt nur durch, was fuer
    Nutzer gedacht ist: Fehler, die selbst als lesbar gelten (`readable`, z. B. der SSH-Verbindung: ein fertiger deutscher
    Satz), und die festen Saetze bei einer Zeitueberschreitung. Alles andere gilt `NOT_PROPOSED_MESSAGE`."""
    if (getattr(exc, "readable", False) and str(exc).strip()) or isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return _describe(exc)
    return NOT_PROPOSED_MESSAGE


def _already_open_reason(action: Any) -> str:
    """Satz dazu, dass fuer den Server schon ein Update-Vorschlag offen ist (`action`: Zeile aus `ctx.actions.list`)."""
    label = up.MODE_LABEL.get(str((action.payload or {}).get("mode")), "Updates")
    if action.status == "proposed":
        return f"Vorschlag „{label}“ wartet schon auf Freigabe."
    return f"„{label}“ ist schon freigegeben und läuft."


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
        """Der Befehl entsteht hier aus `fields` -- derselbe Weg wie beim Ausfuehren.
        `command` im Payload dient nur der Anzeige und der Sperrliste."""
        try:
            command = await commands.build(action_type, host, fields)
        except ValueError as exc:
            raise HTTPException(status_code=error_status, detail=str(exc)) from exc
        return await ctx.actions.propose(ActionRequest(
            action_type=action_type, host_ref=host.id, payload={**fields, "command": command}, risk=risk,
            proposed_by=actor, reason=reason,
        ), wait_s=wait_s)

    def _proposal(decision: GateDecision, risk: Risk) -> dict:
        # `risk`: die Seite prueft damit, ob der Nutzer den Vorschlag selbst freigeben darf
        # (`actions.approve:<risiko>`) -- sie soll die Stufe nicht raten (dist-upgrade ist "hoch").
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": risk.value}

    async def _propose(host: Any, action_type: str, fields: dict, reason: str, actor: Actor, risk: Risk,
                       *, error_status: int = status.HTTP_400_BAD_REQUEST) -> dict:
        return _proposal(await _submit(host, action_type, fields, reason, actor, risk, error_status=error_status), risk)

    async def _propose_upgrade(host: Any, mode: str, actor: Actor, *, wait_s: float | None) -> tuple[GateDecision, Risk]:
        """Plan, Grund, Risiko und Vorschlag fuer Einspielen/Aufraeumen (nicht Neustart). Der Einzelweg und der
        Sammel-Vorschlag gehen beide hierdurch -- gleiche Felder, gleicher Befehl, gleiches Risiko.
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
        # Nur wenn eingeschaltet, kommt die Angabe in den Plan (sonst bleibt der
        # Payload wie bisher); der Freigebende sieht sie im Grund und im Befehl. Ob der Server
        # Proxmox ist, entscheidet erst der Befehl auf dem Server -- daher "falls".
        # dist-upgrade darf Pakete entfernen: hohes Risiko wie ein Neustart, damit auch bei
        # "volle Autonomie" bis "mittel" niemand vorbeigeht, ohne den Befehl gesehen zu haben.
        if up.wants_dist_upgrade(await ctx.settings.get(), manager, mode):
            fields["dist_upgrade"] = True
            reason += " (dist-upgrade, falls der Server Proxmox ist)"
            risk = Risk.HIGH
        # "Keine Sicherheitsupdates offen." u. ae. kommen aus dem Befehlsbau -> 409 wie bisher.
        decision = await _submit(host, ACTION_UPGRADE, fields, reason, actor, risk,
                                 error_status=status.HTTP_409_CONFLICT, wait_s=wait_s)
        return decision, risk

    @read.get("/overview")
    async def overview(refresh: bool = False) -> dict:
        return await defender.overview(refresh=refresh)

    @read.get("/widgets/protection")
    async def protection_widget() -> dict:
        """Kachel fuer die Uebersicht: erste Zeile Gesamtlage, danach je Server."""
        o = await defender.overview()
        sm = o["summary"]
        if sm["hosts"] == 0:
            # Ohne pruefbaren Server gibt es nichts zu bewerten -- "Schutzwert 0 / 100, keine Bedrohung" waere irrefuehrend.
            # Zwei Faelle: gar kein Server angelegt, oder Server da, aber keiner pruefbar (Linux, verwaltet, mit SSH-Zugang,
            # ggf. mit der eingestellten Markierung).
            if sm.get("hosts_known", 0) > 0:
                title = "Noch kein Server prüfbar"
                subtitle = ("Nodvard Shield prüft Linux-Server mit SSH-Zugang (und, falls eingestellt, der passenden Markierung). "
                            "Einstellungen → Server & Zugänge → Zugang einrichten.")
            else:
                title = "Noch kein Server"
                subtitle = "Füge zuerst einen Server hinzu (Einstellungen → Server & Zugänge)."
            # Offene Funde aus frueheren Scans bleiben offen, auch wenn der Server inzwischen fehlt -- die Kachel zeigt sie.
            threats = sm.get("open_threats", 0)
            label, tone = (f"{threats} Bedrohung(en)", "danger") if threats else ("nichts zu bewerten", "neutral")
            return {"data": [{"title": title, "subtitle": subtitle, "label": label, "tone": tone}], "meta": {}}
        rows = [{
            "title": f"Schutzwert {sm['score']} / 100",
            "subtitle": f"{sm['protected']}/{sm['hosts']} Server geschützt · {sm['quarantined']} in Quarantäne",
            "label": f"{sm['open_threats']} Bedrohung(en)" if sm["open_threats"] else "keine Bedrohung",
            "tone": "danger" if sm["open_threats"] else ("good" if sm["score"] >= 80 else "warn"),
        }]
        for h in o["hosts"]:
            scan = h.get("last_scan") or {}
            status = scan.get("status")
            if not h.get("clamav_installed"):
                label, tone = "ohne Virenschutz", "warn"
            elif status == "infected":
                label, tone = "Bedrohung", "danger"
            elif status == "clean" and h.get("signature_stale") is True:
                label, tone = "Signaturen veraltet", "warn"
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
    async def events(all: bool = False) -> list[dict]:  # noqa: A002 - Query-Parameter heisst so
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
    async def scans(host: str | None = None, limit: int = 100, include_watch: bool = False) -> list[dict]:
        return await defender.list_scans(host=host, limit=max(1, min(limit, 500)), include_watch=include_watch)

    @read.get("/findings")
    async def findings(status_filter: str | None = None) -> list[dict]:
        return await defender.list_findings(status=status_filter)

    @read.get("/audits")
    async def audits() -> list[dict]:
        return await defender.latest_audits()

    @manage.post("/scans")
    async def start_scan(payload: _ScanIn) -> dict:
        if payload.kind == "custom" and not payload.paths:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Für einen eigenen Scan bitte mindestens einen Ordner angeben.")
        paths = [p.strip() for p in payload.paths or [] if p.strip().startswith("/")] or None
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
    async def acknowledge_all(actor: Actor = Depends(ctx.api.current_actor)) -> dict:
        assert guard is not None
        return {"acknowledged": await guard.acknowledge_all(actor)}

    @manage.post("/events/{event_id}/acknowledge")
    async def acknowledge(event_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
        assert guard is not None
        if not await guard.acknowledge(event_id, actor):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ereignis unbekannt oder schon bestätigt.")
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
        """Je Server die neueste offene Aktion `ACTION_UPGRADE` (wartet auf Freigabe, freigegeben, laeuft).
        Quelle ist `ctx.actions.list`: die juengsten eigenen Aktionen (hoechstens 200, alle Arten, auch unter einer
        frueheren Kennung der Erweiterung), nach Art, Status und Server gefiltert. Ein Vorschlag, dessen Frist
        abgelaufen ist, aber noch nicht als `expired` markiert wurde, zaehlt nicht mehr."""
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
        """Legt fuer viele Server auf einmal je einen Update-Vorschlag an (nur Vorschlaege: freigegeben wird wie
        sonst unter "Aktionen", auch dort gesammelt). Jeder Vorschlag geht ueber `_propose_upgrade`, also genau
        wie der Einzelweg; ein Server mit Fehler haelt die anderen nicht auf.

        Laufzeit: je Server ein Schritt durch Datenbank und Gate -- keine SSH-Verbindung (der Plan kommt aus dem
        gespeicherten Stand der Update-Zentrale; Server ohne Stand werden uebersprungen, nicht geprueft) und
        ohne Warten auf freigegebene Aktionen (`PROPOSE_ALL_WAIT_S`). Die Vorschlaege entstehen nacheinander."""
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
                except Exception as exc:  # noqa: BLE001 - ein Server darf die Vorschlaege der anderen nicht abbrechen
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
            except Exception:  # noqa: BLE001 - die Vorschlaege stehen schon unter "Aktionen": die Antwort darf nicht an der Protokoll-Zeile scheitern
                logging.getLogger(LOGGER).exception("shield_propose_all_audit_failed")
            return {"mode": mode, "results": results, "counts": counts}
        finally:
            propose_all_running = False

    briefing_running = False

    @manage.post("/briefing")
    async def briefing_now() -> dict:
        """Lagebericht jetzt. Das Erstellen kann bei einem nicht erreichbaren Server ~20 s dauern:
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
        """Startet je Server ein Audit und antwortet sofort mit dem Stand: `started` (neu), `running` (lief schon, es
        startet kein zweites) und `audits` (je Server seit wann, fuer "laeuft seit ...")."""
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

    @manage.post("/hosts/{host_id}/install")
    async def install(host_id: str, payload: _InstallIn, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
        host = await ctx.hosts.get(host_id)
        if host is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Server.")
        risk = Risk.LOW if payload.package == "signatures" else Risk.MEDIUM
        return await _propose(host, ACTION_INSTALL, {"package": payload.package},
                              f"{_INSTALL_LABEL[payload.package]} auf {host.display_name or host.name}", actor, risk)

    return read, manage
