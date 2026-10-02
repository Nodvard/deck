"""Image-Updates einspielen (service-matrix) -- alles, was Hosts anspricht.

Die reinen Teile (erkennen, Befehle bauen, Ergebnis beurteilen) stehen in `image_apply.py`,
der entkoppelte Lauf auf dem Host in `detached.py`. Hier:

- `ImageApplier.hints()`: ein lesender Aufruf je Host beim Pruefen -- welche Container mit
  Update sich ueberhaupt einspielen lassen (Feld `apply` im Ergebnis).
- `ImageApplier.plan()`: die Uebersicht VOR dem Klick (nur lesend): was wird gepullt, welcher
  Befehl laeuft, was ist betroffen, welche Warnungen gibt es -- oder warum es nicht geht.
- `ImageApplier.apply()` / `ImageUpdateExecutor`: die Gate-Aktion `container.image_update`. Der
  Executor baut den Befehl aus den geprueften Feldern NEU und vergleicht ihn mit dem, was der
  Freigebende gesehen hat (`payload.command`). Der Lauf startet entkoppelt auf dem Host
  (`detached.py`, ueberlebt einen SSH-Abriss), das Dashboard fragt alle paar Sekunden nach,
  prueft danach den Start, bewertet den Container neu, schreibt Audit und meldet das Ergebnis.
- `ImageApplier.resume_interrupted()`: startet das Dashboard waehrenddessen neu, macht es danach
  weiter. Dazu merkt sich `image-apply-runs.json` (Datenordner) jeden Lauf VOR dem Start und
  vergisst ihn nach dem Ende -- nur bei einem Abbruch (`CancelledError`) bleibt er stehen.

Kein sudo: service-matrix ruft ueberall schlicht `docker` auf, der SSH-Benutzer ist also in der
Gruppe `docker`. Compose-Dateien, die er nicht lesen kann, blockieren das Update mit Hinweis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nodvard_sdk import REQUEST_WAIT_S, Notification, Severity
from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Actor, ActorType, Risk

from . import detached as dt
from . import image_apply as ia
from .capabilities import CONTAINER_NAME_RE, is_own_container
from .image_apply import ComposeTarget, NotUpdatable
from .image_updates import UPDATE, ImageUpdateService, _write_atomic

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

log = logging.getLogger("nodvard_deck.ext.service-matrix")

INSPECT_TIMEOUT_S = 20
PROBE_TIMEOUT_S = 20
CONFIG_TIMEOUT_S = 30
LAUNCH_TIMEOUT_S = 30
POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 20
UNKNOWN_GRACE_S = 30
"""So lange darf ein gerade gestarteter Lauf noch "unbekannt" sein."""
REACH_GRACE_S = 3 * 60
"""Nach einem gescheiterten Start: meldet sich der Host so lange nicht, gilt er als nicht erreichbar."""
RUN_DEADLINE_S = 40 * 60
"""Unter der Notbremse des Gates (60 Minuten). Danach laeuft der Lauf auf dem Host weiter."""
VERIFY_TIMEOUT_S = 180
VERIFY_INTERVAL_S = 3
VERIFY_STABLE_S = 15
"""Ohne Healthcheck gilt der Start erst nach zwei Lesungen mindestens so weit auseinander, ohne
dass sich `StartedAt`/`RestartCount` geaendert haben (Absturz-Kreislauf)."""
VERIFY_CALL_TIMEOUT_S = 20
NOTIFY_SUCCESS_AFTER_S = 60
"""Ein Erfolg wird nur gemeldet, wenn er laenger dauerte (der Nutzer hat ja zugesehen); ein Fehler immer."""
APPLIED_KEEP_S = 6 * 3600
"""So lange merkt sich die Seite das Ergebnis des letzten Updates (nur im Speicher)."""
RUNS_FILE = "image-apply-runs.json"
"""Laufende Updates (im Datenordner): ueberlebt einen Neustart des Dashboards, damit es danach weitermachen kann."""
RESUME_DELAY_S = 5
"""Nach dem Start kurz warten, bis Netz und SSH wieder da sind."""
RESUME_MIN_WAIT_S = 5 * 60
"""Nach einem Neustart mindestens so lange nachfragen, auch wenn die Frist des Laufs fast um ist."""
RESUME_MAX_AGE_S = 60 * 60
"""Ein Eintrag, der aelter ist als `RUN_DEADLINE_S` + so lange, wird verworfen (die Protokolle des Hosts sind dann
womoeglich schon aufgeraeumt, ein Ergebnis waere Unsinn)."""
FUTURE_TOLERANCE_S = 5 * 60
"""So weit darf `started_at` in der Zukunft liegen (Uhr nach dem Start noch nicht gestellt), sonst gilt es als unbekannt."""
RESTARTED_LINE = "Das Dashboard wurde währenddessen neu gestartet."

ACTION_TYPE = "container.image_update"
STALE_MESSAGE = "Der Container wurde inzwischen verändert – bitte neu prüfen und neu auslösen."
BUSY_MESSAGE = ia.BUSY_TEXT
RISK_ORDER = (Risk.LOW, Risk.MEDIUM, Risk.HIGH, Risk.CRITICAL)
NOT_A_USER_MESSAGE = "Ein Image-Update kann nur ein Mensch über die Service-Matrix vorschlagen."
RISK_TOO_LOW_MESSAGE = "Der Vorschlag hat eine niedrigere Risikostufe, als dieses Update jetzt verlangt – bitte neu prüfen und neu auslösen."
NOT_STARTED = "Update-Lauf wurde auf dem Host nicht gefunden – er ist wohl nicht gestartet."
PHASE_OF_STEP = {"tag": "pull", "pull": "pull", "up": "up", "done": "verify"}

IMAGE_UPDATE_SPEC = ActionSpec(
    action_type=ACTION_TYPE, label="Image-Update einspielen", icon="download",
    description="Lädt das neue Image eines Compose-Dienstes und erstellt den Container damit neu (docker compose pull + up -d).",
    default_risk=Risk.MEDIUM, permissions=["hosts.execute"],
    # host_bound=False: den Befehl baut immer die Extension (Uebersicht + Executor), nie ein
    # Formular auf der Server-Seite. command_field: die Sperrliste des Gates prueft den Befehl.
    host_bound=False, command_field="command",
    confirm_text="Der Container wird neu erstellt und ist kurz nicht erreichbar. Fortfahren?",
)


def _describe(exc: BaseException) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "Zeitüberschreitung – der Vorgang hat zu lange gebraucht."
    return str(exc).strip() or type(exc).__name__


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_ACTIVE_RUNS: set[str] = set()
"""Lauf-IDs, die IN DIESEM PROZESS gerade von einem `apply()` oder einer Fortsetzung betreut werden. Schaltet jemand die
Extension aus und wieder ein, laeuft das alte `apply()` in der Gate-Aufgabe weiter, waehrend `on_start` schon
`resume_interrupted` ruft -- diese Laeufe darf die Fortsetzung nicht ein zweites Mal aufgreifen (doppeltes Audit)."""


def _target_to_json(t: ComposeTarget) -> dict[str, Any]:
    return {**asdict(t), "config_files": list(t.config_files), "env_files": list(t.env_files)}


def _target_from_json(data: Any) -> ComposeTarget:
    """Baut das Ziel aus dem gemerkten Eintrag; jeder Fehler (auch ein unpassender Wert) wird zu ValueError."""
    if not isinstance(data, dict):
        raise ValueError("kein Ziel")
    if not isinstance(data.get("config_files"), list) or not isinstance(data.get("env_files") or [], list):
        raise ValueError("Ziel: Dateilisten sind keine Listen")
    try:
        t = ComposeTarget(
            container=data["container"], container_id=data["container_id"], image=data["image"], image_id=data["image_id"],
            project=data["project"], service=data["service"], working_dir=data["working_dir"],
            config_files=tuple(data["config_files"]), env_files=tuple(data.get("env_files") or ()), config_hash=data.get("config_hash"),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("Ziel unvollständig") from exc
    ia.check_target(t)
    return t


def _actor_to_json(actor: Any) -> dict[str, Any]:
    return {"type": getattr(actor.type, "value", actor.type), "id": actor.id, "label": actor.label}


def _actor_from_json(data: Any) -> Actor | None:
    """Der Freigebende aus dem Eintrag, streng geprueft; bei jedem Zweifel `None` (das Audit gibt es trotzdem)."""
    if not isinstance(data, dict):
        return None
    kind, ident, label = data.get("type"), data.get("id"), data.get("label")
    if kind not in {a.value for a in ActorType} or not isinstance(ident, str) or not 0 < len(ident) <= 200:
        return None
    if label is not None and (not isinstance(label, str) or len(label) > 200):
        return None
    return Actor(type=ActorType(kind), id=ident, label=label)


def _parse_time(value: Any) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


@dataclass
class _Outcome:
    state: str  # done | not_started | lost | unknown | unreachable | timeout
    rc: int | None = None
    log: str = ""
    step: str | None = None
    message: str = ""
    public_message: str = ""
    """Wie `message`, aber ohne Zeilen vom Host (fuer Protokoll, Meldung und `applied`).
    Leer: `message` selbst enthaelt keinen Text vom Host."""

    @property
    def safe_message(self) -> str:
        return self.public_message or self.message


@dataclass(frozen=True)
class Plan:
    target: ComposeTarget
    host_id: str
    host_name: str
    affected: tuple[str, ...]
    stopped: tuple[str, ...]
    command: str
    rollback_ref: str
    rollback: str
    risk: Risk
    warnings: tuple[str, ...]
    remote_digest: str | None
    registry_at: str | None
    stale: bool
    plan_id: str

    def reason_text(self) -> str:
        t = self.target
        return ia.reason_text(self.host_name, t.container, t.image, t.project, t.service, self.risk)

    def as_response(self) -> dict[str, Any]:
        t = self.target
        return {
            "ok": True,
            "plan_id": self.plan_id,
            "host_id": self.host_id,
            "container": t.container,
            "image": t.image,
            "current_image_id": t.image_id,
            "current_short": ia.short_id(t.image_id),
            "remote_digest": self.remote_digest,
            "remote_short": ia.short_id(self.remote_digest),
            "registry_at": self.registry_at,
            "stale": self.stale,
            "project": t.project,
            "service": t.service,
            "affected": list(self.affected),
            "command": self.command,
            "rollback": self.rollback,
            "risk": self.risk.value,
            "warnings": list(self.warnings),
        }


class ImageApplier:
    def __init__(self, ctx: ExtensionContext, images: ImageUpdateService) -> None:
        self._ctx = ctx
        self._images = images
        self._busy: dict[str, str] = {}
        """`host_id:projekt` -> Container, fuer den dort gerade ein Update laeuft."""
        self._applying: dict[str, dict[str, Any]] = {}
        """`host_id:container` -> Stand des laufenden Updates (Schritt, Start, Lauf-ID)."""
        self._applied: dict[str, dict[str, Any]] = {}
        """`host_id:container` -> Ergebnis des letzten Updates (nur im Speicher)."""
        self._applied_at: dict[str, float] = {}
        self._now = time.monotonic  # Test-Einhaenger (Dauer eines Laufs)

    def snapshot(self) -> dict[str, Any]:
        cutoff = time.time() - APPLIED_KEEP_S
        for key in [k for k, at in self._applied_at.items() if at < cutoff]:
            self._applied.pop(key, None)
            self._applied_at.pop(key, None)
        return {"applying": {k: dict(v) for k, v in self._applying.items()}, "applied": {k: dict(v) for k, v in self._applied.items()}}

    # --- Hinweise beim Pruefen -------------------------------------------------------------

    async def hints(self, host: Any, names: list[str]) -> dict[str, dict[str, Any]]:
        """`{name: {"mode": "compose", project, service} | {"mode": "none", kind, why}}` -- nur
        aus Etiketten (ein lesender Aufruf, keine Umgebungsvariablen). Die endgueltige Pruefung
        macht `plan()` beim Klick."""
        wanted = [n for n in names if CONTAINER_NAME_RE.match(n)]
        if not wanted:
            return {}
        res = await self._ctx.exec.run(host, ia.cmd_apply_inspect(wanted), timeout_s=INSPECT_TIMEOUT_S)
        hints: dict[str, dict[str, Any]] = {}
        for info in ia.parse_apply_inspect(res.stdout):
            name = str(info.get("name") or "").lstrip("/")
            if name not in wanted:
                continue
            verdict = ia.classify(info, own=is_own_container(str(info.get("id") or "")))
            if isinstance(verdict, NotUpdatable):
                hints[name] = {"mode": "none", "kind": verdict.kind, "why": verdict.why}
            else:
                hints[name] = {"mode": "compose", "project": verdict.project, "service": verdict.service}
        return hints

    # --- Die Uebersicht vor dem Klick -------------------------------------------------------

    async def plan(self, host: Any, container: str) -> Plan | NotUpdatable:
        """Nur lesend. Ein SSH-Fehler kommt als Ausnahme heraus (die Route macht daraus 502,
        der Executor eine Fehlermeldung)."""
        if not CONTAINER_NAME_RE.match(container or ""):
            return NotUpdatable("name", "Ungültiger Containername.")
        run = self._ctx.exec.run

        res = await run(host, ia.cmd_apply_inspect([container]), timeout_s=INSPECT_TIMEOUT_S)
        rows = [r for r in ia.parse_apply_inspect(res.stdout) if str(r.get("name") or "").lstrip("/") == container]
        if not rows:
            return NotUpdatable("gone", "Container nicht gefunden.")
        info = rows[0]
        found = ia.classify(info, own=is_own_container(str(info.get("id") or "")))
        if isinstance(found, NotUpdatable):
            return found
        t = found
        if str(info.get("state") or "") != "running":
            return NotUpdatable("not_running", "Container läuft nicht.")

        result = self._images.result_for(host.id, container)
        if result is None or result.get("status") != UPDATE or result.get("image") != t.image:
            return NotUpdatable("no_update", "Laut letzter Prüfung gibt es für diesen Container kein neueres Image. Erst „Image-Updates prüfen“.")
        if f"{host.id}:{t.project}" in self._busy:
            return NotUpdatable("busy", "Für dieses Compose-Projekt läuft gerade schon ein Update.")

        probe = await run(host, ia.cmd_probe(t), timeout_s=PROBE_TIMEOUT_S)
        blocked = ia.parse_probe(probe.stdout, t)
        if blocked is not None:
            return blocked

        members_res = await run(host, ia.cmd_members(t), timeout_s=INSPECT_TIMEOUT_S)
        members = ia.parse_members(members_res.stdout) if members_res.exit_code == 0 else []
        affected = tuple(n for n, _ in members) or (t.container,)
        stopped = tuple(n for n, state in members if state != "running")

        # Die Ausgabe enthaelt aufgeloeste Umgebungswerte: nur auswerten, nie weitergeben.
        config = await run(host, ia.cmd_config(t), timeout_s=CONFIG_TIMEOUT_S)
        blocked = ia.check_config(config.stdout, config.stderr, config.exit_code, t)
        if blocked is not None:
            return blocked

        drift = False
        if t.config_hash:
            try:
                hashed = await run(host, ia.cmd_config_hash(t), timeout_s=CONFIG_TIMEOUT_S)
                current = ia.parse_config_hash(hashed.stdout, t.service) if hashed.exit_code == 0 else None
                drift = current is not None and current != t.config_hash
            except Exception:  # noqa: BLE001 - nur ein Hinweis, kein Grund abzubrechen
                drift = False

        command = ia.display_command(t)
        risk = ia.risk_for(t.image, t.service)
        rollback = ia.rollback_ref(t.container)
        remote_digest = result.get("remote_digest")
        stale = bool(result.get("stale"))
        return Plan(
            target=t, host_id=host.id, host_name=getattr(host, "display_name", None) or host.name,
            affected=affected, stopped=stopped, command=command, rollback_ref=rollback, rollback=ia.rollback_commands(t, rollback),
            risk=risk,
            warnings=tuple(ia.warnings_for(
                image=t.image, service=t.service, affected=list(affected), stopped=list(stopped), drift=drift, stale=stale,
                registry_at=result.get("registry_at"),
            )),
            remote_digest=remote_digest, registry_at=result.get("registry_at"), stale=stale,
            plan_id=ia.plan_id(command, t.image_id, remote_digest),
        )

    # --- Vorschlagen (POST-Route) ------------------------------------------------------------

    async def propose(self, host: Any, plan: Plan, actor: Any) -> dict[str, Any]:
        """Legt den Vorschlag im Gate an. Die Begruendung ist das Einzige, was die Aktionen-Seite
        zeigt -- sie traegt deshalb das Wesentliche (`Plan.reason_text`)."""
        t = plan.target
        decision = await self._ctx.actions.propose(
            ActionRequest(
                action_type=ACTION_TYPE, host_ref=host.id, risk=plan.risk, proposed_by=actor, reason=plan.reason_text(),
                correlation_id="imgupd_" + secrets.token_hex(8),
                payload={
                    "container": t.container, "project": t.project, "service": t.service, "image": t.image,
                    "old_image_id": t.image_id, "remote_digest": plan.remote_digest, "command": plan.command,
                },
            ),
            wait_s=REQUEST_WAIT_S,
        )
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": plan.risk.value, "detail": decision.detail}

    # --- Ausfuehren (Gate-Aktion) ------------------------------------------------------------

    async def apply(self, req: ActionRequest) -> ActionResult:
        payload = req.payload or {}
        host = await self._ctx.hosts.get(req.host_ref) if req.host_ref else None
        if host is None:
            return ActionResult(success=False, error="Host nicht gefunden.")
        tag = (await self._ctx.settings.get()).get("docker_host_tag") or "docker"
        if tag not in host.tags:
            return ActionResult(success=False, error="Kein Docker-Host.")
        container = str(payload.get("container") or "")
        if not CONTAINER_NAME_RE.match(container):
            return ActionResult(success=False, error=f"Ungültiger Containername: {container!r}")

        # Der Befehl entsteht hier NEU aus den geprueften Feldern; `payload.command` wird nie
        # ausgefuehrt, nur verglichen: das Gate hat genau ihn geprueft, der Freigebende genau ihn gesehen.
        try:
            plan = await self.plan(host, container)
        except Exception as exc:  # noqa: BLE001 - als Ergebnis melden
            return ActionResult(success=False, error=f"Host nicht erreichbar: {_describe(exc)}")
        if isinstance(plan, NotUpdatable):
            return ActionResult(success=False, error=plan.why)
        t = plan.target
        if payload.get("command") != plan.command or payload.get("old_image_id") != t.image_id:
            return ActionResult(success=False, error=STALE_MESSAGE)
        # Nur ein Mensch schlaegt das vor, und nie mit einer kleineren Risikostufe, als die neue
        # Uebersicht verlangt (z. B. weil aus dem Container inzwischen eine Datenbank wurde).
        if getattr(req.proposed_by.type, "value", req.proposed_by.type) != "user":
            return ActionResult(success=False, error=NOT_A_USER_MESSAGE)
        if RISK_ORDER.index(req.risk) < RISK_ORDER.index(plan.risk):
            return ActionResult(success=False, error=RISK_TOO_LOW_MESSAGE)

        busy_key, app_key = f"{host.id}:{t.project}", f"{host.id}:{container}"
        if busy_key in self._busy:  # zwischen Uebersicht und hier kann ein anderer Lauf begonnen haben
            return ActionResult(success=False, error=BUSY_MESSAGE)
        run_id = req.correlation_id if isinstance(req.correlation_id, str) and dt.RUN_ID_RE.match(req.correlation_id) else "imgupd_" + secrets.token_hex(8)
        self._busy[busy_key] = container
        started_iso = _iso_now()
        self._applying[app_key] = {"phase": "start", "started_at": started_iso, "run_id": run_id}
        started = self._now()
        keep_entry = False
        # VOR dem Start vermerken: bricht das Dashboard jetzt ab, macht es nach dem Neustart weiter.
        self._store_run({
            "run_id": run_id, "host_id": host.id, "container": container, "project": t.project, "service": t.service,
            "image": t.image, "old_image_id": t.image_id, "started_at": started_iso,
            "target": _target_to_json(t), "proposed_by": _actor_to_json(req.proposed_by),
        })
        _ACTIVE_RUNS.add(run_id)
        try:
            script = ia.apply_script(t, old_image_id=t.image_id, rollback=plan.rollback_ref)
            outcome = await self._launch_and_wait(host, run_id, script, app_key, t.project)
            return await self._conclude(host, t, plan.rollback_ref, run_id, app_key, started, outcome, actor=req.proposed_by)
        except asyncio.CancelledError:
            # Das Dashboard faehrt herunter: der Lauf auf dem Host geht weiter (entkoppelt). Hier
            # wird nichts als "fertig" gemeldet -- das Gate markiert die Aktion als unterbrochen.
            # Der Eintrag bleibt: nach dem Neustart macht `resume_interrupted` weiter und meldet.
            # Achtung: CancelledError kommt nicht nur vom Herunterfahren, sondern auch von der Notbremse
            # des Gates (`asyncio.timeout`, 60 Minuten). Ein dann stehengebliebener Eintrag wird beim
            # naechsten Start als zu alt verworfen (`RESUME_MAX_AGE_S`), nicht ewig weiterverfolgt.
            keep_entry = True
            log.warning("image_update_interrupted host=%s container=%s run=%s (laeuft auf dem Host weiter)", host.id, container, run_id)
            raise
        finally:
            _ACTIVE_RUNS.discard(run_id)
            if not keep_entry:
                self._drop_run(run_id)
            self._busy.pop(busy_key, None)
            self._applying.pop(app_key, None)

    # --- Merkzettel fuer den Neustart des Dashboards ------------------------------------------

    def _runs_path(self) -> Path:
        return Path(str(self._ctx.data_dir)) / RUNS_FILE

    def _read_runs(self) -> dict[str, Any]:
        """`{run_id: Eintrag}`. Fehlt die Datei: leer. Ist sie kaputt: leer plus Logzeile (nie ein Absturz)."""
        path = self._runs_path()
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError:
            log.exception("image_update_runs_unreadable path=%s", path)
            return {}
        try:
            runs = json.loads(text).get("runs", {})
            if not isinstance(runs, dict):
                raise ValueError("runs ist keine Zuordnung")
        except (ValueError, AttributeError):
            log.warning("image_update_runs_broken path=%s (wird ignoriert)", path)
            return {}
        return runs

    def _write_runs(self, runs: dict[str, Any]) -> None:
        _write_atomic(self._runs_path(), json.dumps({"runs": runs}, indent=1, sort_keys=True))

    def _store_run(self, entry: dict[str, Any]) -> None:
        try:
            runs = self._read_runs()
            runs[entry["run_id"]] = entry
            self._write_runs(runs)
        except Exception:  # noqa: BLE001 - das Update selbst geht vor; ohne Eintrag fehlt nur das Fortsetzen
            log.exception("image_update_runs_write_failed run=%s", entry.get("run_id"))

    def _drop_run(self, run_id: str) -> None:
        try:
            runs = self._read_runs()
            if runs.pop(run_id, None) is not None:
                self._write_runs(runs)
        except Exception:  # noqa: BLE001
            log.exception("image_update_runs_write_failed run=%s", run_id)

    # --- Nach einem Neustart weitermachen ------------------------------------------------------

    async def resume_interrupted(self) -> int:
        """Beim Start (Hintergrundaufgabe): Updates, die beim Beenden des Dashboards noch liefen,
        weiter abfragen und abschliessen wie nach dem normalen Weg -- mit Meldung, denn der
        Ausloeser hat die Antwort nie bekommen.

        Container und Projekt werden sofort als belegt eingetragen (ein zweites Update kann in
        der Wartezeit nicht starten); nur das Abfragen wartet kurz (`RESUME_DELAY_S`)."""
        claimed: list[tuple[dict[str, Any], Any, ComposeTarget, float]] = []
        for run_id, entry in list(self._read_runs().items()):
            if run_id in _ACTIVE_RUNS:  # betreut dieser Prozess schon (z. B. Extension aus- und eingeschaltet)
                log.info("image_update_resume_skipped run=%s (wird in diesem Prozess schon betreut)", run_id)
                continue
            try:
                if not isinstance(entry, dict) or entry.get("run_id") != run_id or not dt.RUN_ID_RE.match(run_id):
                    raise ValueError("Eintrag passt nicht zur Lauf-ID")
                t = _target_from_json(entry.get("target"))
                if entry.get("container") != t.container:
                    raise ValueError("Eintrag unvollständig")
                started_ts = _parse_time(entry.get("started_at"))
                if started_ts is None:
                    raise ValueError("Startzeit nicht lesbar")
                now = time.time()
                if started_ts > now + FUTURE_TOLERANCE_S:  # falsch gestellte Uhr: Alter unbekannt, ab jetzt rechnen
                    started_ts = now
                if now - started_ts > RUN_DEADLINE_S + RESUME_MAX_AGE_S:
                    raise ValueError("Eintrag zu alt")
            except Exception as exc:  # noqa: BLE001 - ein kaputter Eintrag darf den Rest nicht aufhalten
                log.warning("image_update_resume_dropped run=%s (%s)", run_id, _describe(exc))
                self._drop_run(run_id)
                continue
            try:
                host = await self._ctx.hosts.get(str(entry.get("host_id") or ""))
            except Exception:  # noqa: BLE001 - voruebergehender Fehler (z. B. Datenbank gesperrt): Eintrag behalten
                log.exception("image_update_resume_host_lookup_failed run=%s (Eintrag bleibt)", run_id)
                continue
            if host is None:
                log.warning("image_update_resume_dropped run=%s (Host %s existiert nicht mehr)", run_id, entry.get("host_id"))
                self._drop_run(run_id)
                continue
            busy_key, app_key = f"{host.id}:{t.project}", f"{host.id}:{t.container}"
            if busy_key in self._busy:  # der laufende Lauf meldet sich selbst, auf dem Host schuetzt die Sperre
                log.warning("image_update_resume_dropped run=%s (Projekt ist belegt)", run_id)
                self._drop_run(run_id)
                continue
            self._busy[busy_key] = t.container
            _ACTIVE_RUNS.add(run_id)
            started_iso = datetime.fromtimestamp(started_ts, timezone.utc).isoformat(timespec="seconds")
            self._applying[app_key] = {"phase": "start", "started_at": started_iso, "run_id": run_id}
            claimed.append((entry, host, t, started_ts))
        if not claimed:
            return 0
        try:
            await asyncio.sleep(RESUME_DELAY_S)
        except asyncio.CancelledError:  # schon wieder beendet: Eintraege bleiben fuer den naechsten Start
            for entry, host, t, _ts in claimed:
                self._release(host, t, entry["run_id"])
            raise
        await asyncio.gather(*(self._resume_one(entry, host, t, ts) for entry, host, t, ts in claimed))
        return len(claimed)

    def _release(self, host: Any, t: ComposeTarget, run_id: str) -> None:
        _ACTIVE_RUNS.discard(run_id)
        self._busy.pop(f"{host.id}:{t.project}", None)
        self._applying.pop(f"{host.id}:{t.container}", None)

    async def _resume_one(self, entry: dict[str, Any], host: Any, t: ComposeTarget, started_ts: float) -> None:
        run_id, app_key = entry["run_id"], f"{host.id}:{t.container}"
        keep_entry = False
        try:
            # Nie laenger als ein ganzer Lauf, nie kuerzer als 5 Minuten ab jetzt (der Host war ja evtl. weg).
            left = started_ts + RUN_DEADLINE_S - time.time()
            outcome = await self._wait(
                host, run_id, app_key, unknown_message=NOT_STARTED, reach_grace_s=None,
                deadline_s=min(max(left, RESUME_MIN_WAIT_S), RUN_DEADLINE_S),
            )
            # Das Sicherungs-Image wird neu abgeleitet, nie aus der Datei uebernommen.
            await self._conclude(
                host, t, ia.rollback_ref(t.container), run_id, app_key, self._now(), outcome,
                actor=_actor_from_json(entry.get("proposed_by")), resumed=True,
            )
        except asyncio.CancelledError:
            keep_entry = True
            log.warning("image_update_resume_interrupted host=%s container=%s run=%s", host.id, t.container, run_id)
            raise
        except Exception:  # noqa: BLE001 - ein Fehler hier darf nicht bei jedem Start wiederkehren
            log.exception("image_update_resume_failed host=%s container=%s run=%s", host.id, t.container, run_id)
        finally:
            if not keep_entry:
                self._drop_run(run_id)
            self._release(host, t, run_id)

    def _set_phase(self, app_key: str, phase: str) -> None:
        if app_key in self._applying:
            self._applying[app_key]["phase"] = phase

    async def _conclude(
        self, host: Any, t: ComposeTarget, rollback_ref: str, run_id: str, app_key: str, started: float, outcome: _Outcome,
        *, actor: Actor | None, resumed: bool = False,
    ) -> ActionResult:
        """Alles nach dem Lauf: pruefen, Container neu bewerten, Audit, Meldung. Fuer den Weg ueber
        das Gate und fuer das Fortsetzen nach einem Neustart (`resumed`); `actor` ist der Freigebende (falls bekannt)."""
        log_text = outcome.log
        verdict: ia.Verdict | None = None
        snap: ia.Snapshot | None = None
        show_rollback = False
        success = False
        pull_failed = outcome.state == "done" and outcome.rc == ia.RC_PULL
        public_message: str | None = None  # gesetzt, wenn `message` eine Zeile vom Host enthaelt

        if outcome.state == "done" and outcome.rc == 0:
            self._set_phase(app_key, "verify")
            verdict, snap = await self._verify(host, t, rollback_ref)
            success = verdict.ok
            message = verdict.text
            show_rollback = not success
        elif pull_failed:
            message = ia.describe_pull_failure(log_text)  # nichts veraendert, kein Zurueck noetig
            public_message = ia.describe_pull_failure(log_text, public=True)
        else:
            if outcome.state == "done":
                message = (
                    "Neu erstellen fehlgeschlagen – der Dienst läuft womöglich nicht."
                    if outcome.rc == ia.RC_UP else f"Update-Lauf mit Rückgabecode {outcome.rc} beendet."
                )
            else:
                message = outcome.message
                public_message = outcome.safe_message
            if outcome.state in ("done", "lost", "timeout"):  # dort kann schon etwas veraendert sein
                snap = await self._snapshot_once(host, t, rollback_ref)
            show_rollback = outcome.rc == ia.RC_UP or outcome.step in ("up", "done")

        rollback = rollback_ref if (snap is None or snap.rollback_image_id == t.image_id) else None
        if "@@warn=rollback-tag" in log_text:  # das Markieren scheiterte: kein Rueckweg versprechen
            rollback = None
        if success:
            assert verdict is not None
            output = ia.success_output(t, verdict, old_image_id=t.image_id, rollback=rollback, log=log_text)
        else:
            output = ia.failure_output(
                t, message, rollback=rollback, log=log_text, show_rollback=show_rollback,
                state=ia.describe_members(snap) if snap is not None and not pull_failed else None,
            )

        try:  # der Stand der Seite: aus "Update verfuegbar" wird "aktuell" (oder es bleibt, wie es war)
            await self._images.recheck_container(host, t.container)
        except Exception:  # noqa: BLE001 - das Ergebnis des Updates steht trotzdem fest
            log.exception("image_update_recheck_failed host=%s container=%s", host.id, t.container)

        duration_s = self._now() - started
        new_id = verdict.new_image_id if verdict else None
        # Was auch ohne Server-Recht sichtbar ist (Uebersicht der Seite, Protokoll, Meldung): bei einem
        # Fehler nur feste Texte. Die Zeile vom Host steht nur in der Ausgabe der Aktion.
        public_output = output if success else ia.failure_headline(t, public_message or message)
        summary = public_output.splitlines()[0]
        self._applied[app_key] = {"ok": success, "summary": summary, "finished_at": _iso_now()}
        self._applied_at[app_key] = time.time()
        detail = {
            "container": t.container, "project": t.project, "service": t.service, "image": t.image, "old_image_id": t.image_id,
            "new_image_id": new_id, "rollback_ref": rollback_ref if show_rollback or success else None, "run_id": run_id,
            "log_path": dt.log_path(run_id), "recreated": bool(verdict and verdict.changed) if success else None,
            "health": verdict.health if verdict else None,
        }
        if resumed:
            detail["resumed"] = True
        await self._audit(actor, host, t, success, summary, detail, run_id)
        await self._notify(host, t, success, public_output, duration_s, show_rollback and not success, run_id, rollback, resumed=resumed)
        return ActionResult(
            success=success, exit_code=outcome.rc, output=output[-20000:], error=None if success else message,
            duration_ms=int(duration_s * 1000), detail=detail,
        )

    async def _launch_and_wait(self, host: Any, run_id: str, script: str, app_key: str, project: str) -> _Outcome:
        unknown = NOT_STARTED
        reach_grace_s: float | None = None
        try:
            res = await self._ctx.exec.run(host, dt.launch_command(run_id, script, project), timeout_s=LAUNCH_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            # Der Start kann trotzdem geklappt haben (Verbindung erst danach weg) -- nachsehen statt
            # einen laufenden Lauf als gescheitert zu melden. Meldet sich der Host gar nicht mehr,
            # nicht 40 Minuten lang "laeuft" zeigen.
            unknown = f"Start fehlgeschlagen: {_describe(exc)}"
            reach_grace_s = REACH_GRACE_S
        else:
            out = (res.stdout or "") + (res.stderr or "")
            info = dt.parse_launch(out)
            if info.busy:  # auf dem Host laeuft schon ein Update dieses Projekts (z. B. von einem anderen Dashboard)
                return _Outcome("not_started", message=BUSY_MESSAGE)
            if not info.started and info.method is None:  # schon der Ordner scheiterte: nichts gestartet
                lines = [ln for ln in out.strip().splitlines() if not ln.startswith("@@")]
                reason = lines[-1][:200] if lines else "keine Rückmeldung"
                return _Outcome(
                    "not_started", message=f"Update-Lauf konnte nicht gestartet werden: {reason}",
                    public_message=f"Update-Lauf konnte nicht gestartet werden – {ia.DETAILS_HINT}",
                )
        return await self._wait(host, run_id, app_key, unknown_message=unknown, reach_grace_s=reach_grace_s)

    async def _wait(
        self, host: Any, run_id: str, app_key: str, *, unknown_message: str, reach_grace_s: float | None, deadline_s: float | None = None,
    ) -> _Outcome:
        """Fragt alle `POLL_INTERVAL_S` Sekunden kurz nach. Verbindungsfehler zwischendurch (z. B.
        wenn Docker neu startet) und Antworten ohne Auskunft werden ausgesessen. Der Lauf auf dem
        Host wird nie beendet."""
        command = dt.poll_command(run_id)
        deadline = time.monotonic() + (RUN_DEADLINE_S if deadline_s is None else deadline_s)
        unknown_until = time.monotonic() + UNKNOWN_GRACE_S
        reach_until = None if reach_grace_s is None else time.monotonic() + reach_grace_s
        reached = False
        lost = 0
        last_error: str | None = None
        log_text = ""
        step: str | None = None
        while True:
            try:
                res = await self._ctx.exec.run(host, command, timeout_s=POLL_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - Verbindung weg: weiter nachfragen
                last_error = _describe(exc)
            else:
                poll = dt.parse_poll((res.stdout or "") + (res.stderr or ""))
                if poll.state == "noreply":
                    last_error = f"keine Antwort vom Host (Rückgabecode {res.exit_code})"
                else:
                    last_error = None
                    reached = True
                    log_text = poll.output or log_text
                    if poll.step:
                        step = poll.step
                        self._set_phase(app_key, PHASE_OF_STEP.get(poll.step, "pull"))
                    if poll.state == "done":
                        return _Outcome("done", rc=poll.rc, log=poll.output, step=step)
                    lost = lost + 1 if poll.state == "lost" else 0
                    if lost >= 2:  # zweimal hintereinander: kein Zufall beim Nachsehen
                        return _Outcome(
                            "lost", log=log_text, step=step,
                            message=f"Der Update-Lauf wurde auf dem Host unerwartet beendet (Protokoll: {dt.log_path(run_id)}).",
                        )
                    if poll.state == "unknown" and time.monotonic() >= unknown_until:
                        return _Outcome("unknown", message=unknown_message)
            if reach_until is not None and not reached and time.monotonic() >= reach_until:
                return _Outcome("unreachable", message=unknown_message)
            if time.monotonic() >= deadline:
                message = f"Keine Rückmeldung – der Lauf kann auf dem Host weiterlaufen (Protokoll: {dt.log_path(run_id)})."
                if last_error:
                    message += f" Letzter Fehler: {last_error}"
                return _Outcome("timeout", log=log_text, step=step, message=message)
            await asyncio.sleep(POLL_INTERVAL_S)

    async def _read_state(self, host: Any, t: ComposeTarget, rollback_ref: str) -> ia.Snapshot:
        res = await self._ctx.exec.run(host, ia.cmd_verify(t, rollback_ref), timeout_s=VERIFY_CALL_TIMEOUT_S)
        return ia.parse_verify((res.stdout or "") + (res.stderr or ""))

    async def _verify(self, host: Any, t: ComposeTarget, rollback_ref: str) -> tuple[ia.Verdict, ia.Snapshot | None]:
        """Wartet, bis der Dienst wieder laeuft (oder klar nicht). Ohne Healthcheck reicht ein
        einzelnes "running" nicht: erst zwei Lesungen `VERIFY_STABLE_S` auseinander ohne neuen Start
        (`StartedAt`, `RestartCount`) gelten als Erfolg; ein neuer Start dazwischen ist ein Absturz-Kreislauf."""
        deadline = time.monotonic() + VERIFY_TIMEOUT_S
        snap: ia.Snapshot | None = None
        first_ok: tuple[ia.Snapshot, float] | None = None
        while True:
            try:
                snap = await self._read_state(host, t, rollback_ref)
            except Exception:  # noqa: BLE001 - kurzer Aussetzer: weiter versuchen
                snap = None
            now = time.monotonic()
            final = now >= deadline
            if snap is not None:
                if first_ok is not None and ia.restarted_since(first_ok[0], snap):
                    return ia.Verdict(False, ia.CRASH_LOOP_TEXT), snap
                verdict = ia.judge_verify(snap, old_image_id=t.image_id, final=final)
                if verdict is not None:
                    if not (verdict.ok and ia.needs_stability_check(snap)):
                        return verdict, snap
                    if first_ok is None:
                        first_ok = (snap, now)  # erste ruhige Lesung: mindestens eine zweite muss folgen
                        if final:
                            return verdict, snap
                    elif now - first_ok[1] >= VERIFY_STABLE_S or final:
                        return verdict, snap
                else:
                    first_ok = None  # zwischendurch nicht mehr "ok": die Beobachtung beginnt neu
            elif final:
                return ia.Verdict(False, "Zustand des Containers nicht prüfbar (Host antwortet nicht)."), None
            await asyncio.sleep(VERIFY_INTERVAL_S)

    async def _snapshot_once(self, host: Any, t: ComposeTarget, rollback_ref: str) -> ia.Snapshot | None:
        try:
            return await self._read_state(host, t, rollback_ref)
        except Exception:  # noqa: BLE001 - nur zur Auskunft
            return None

    async def _audit(self, actor: Any, host: Any, t: ComposeTarget, success: bool, summary: str, detail: dict[str, Any], run_id: str) -> None:
        try:
            await self._ctx.audit.log(
                action="service_matrix.image_update", outcome="success" if success else "failure", target_type="container",
                target_id=f"{host.id}:{t.container}", reason=summary, detail=detail, correlation_id=run_id, actor=actor,
            )
        except Exception:  # noqa: BLE001 - das Journal des Gates hat die Aktion ohnehin
            log.exception("image_update_audit_failed host=%s container=%s", host.id, t.container)

    async def _notify(
        self, host: Any, t: ComposeTarget, success: bool, public_output: str, duration_s: float, with_rollback: bool, run_id: str,
        rollback: str | None, *, resumed: bool = False,
    ) -> None:
        """Fehler immer, Erfolg nur nach mehr als `NOTIFY_SUCCESS_AFTER_S` (nach einem Neustart immer). Bewusst OHNE `host_id`
        im Payload: ein Ergebnis, auf das der Nutzer wartet, soll ein Wartungsfenster nicht schlucken.
        `public_output`: die Ausgabe ohne Zeilen vom Host (siehe `_conclude`); die Meldung zeigt nur die ersten Zeilen."""
        if success and duration_s <= NOTIFY_SUCCESS_AFTER_S and not resumed:
            return
        host_name = getattr(host, "display_name", None) or host.name
        lines = public_output.splitlines()
        body = "\n".join(lines[:2] if success else lines[:1])
        if with_rollback and rollback:
            body += "\nZurück (auf dem Host):\n" + ia.rollback_commands(t, rollback)
        if resumed:  # der Ausloeser hat die Antwort nie bekommen: immer melden, und sagen warum
            body += "\n" + RESTARTED_LINE
        try:
            await self._ctx.notify.send(Notification(
                title=f"Image-Update „{t.container}“ auf {host_name}: {'fertig' if success else 'fehlgeschlagen'}",
                body=body, severity=Severity.INFO if success else Severity.WARNING, correlation_id=run_id,
                payload={"path": f"/ext/service-matrix/matrix?host={host.id}", "tags": ["package"] if success else ["warning"]},
            ))
        except Exception:  # noqa: BLE001
            log.exception("image_update_notify_failed host=%s container=%s", host.id, t.container)


class ImageUpdateExecutor:
    """Erfuellt `nodvard_sdk.capabilities.ActionExecutor` fuer `container.image_update`."""

    action_types = frozenset({ACTION_TYPE})

    def __init__(self, applier: ImageApplier) -> None:
        self._applier = applier

    async def execute(self, req: ActionRequest) -> ActionResult:
        return await self._applier.apply(req)

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        return DryRunReport(would_change=True, summary=req.reason or "Image-Update einspielen")
