"""Update je Stack: die Images aller Dienste mit Update in einem Durchgang holen, die alten als Sicherung behalten, die
Dienste neu erstellen und prüfen. Startet einer nicht, geht es zur alten Version zurück, außer bei Datenspeichern."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
import logging
import re
import stat as statmod
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Actor, Risk
from pydantic import BaseModel, ConfigDict, Field

from .applier import RISK_ORDER
from .commands import CONTAINER_NAME_RE, IMAGE_ID_RE, VOLUME_NAME_RE
from .editor import StackEditor
from .image_apply import PROJECT_RE, SERVICE_RE, is_data_store
from .image_updates import ImageUpdateService, _write_atomic, is_valid_ref
from .stack_actions import (
    DockerStackExecutor,
    _connection_error,
    _in_folder,
    _lost,
    busy_text,
    failure_line,
)
from .stack_backup import (
    ARCHIVE_BUDGET_S,
    BACKUP_IMAGE,
    BACKUP_ROOT,
    CONTENTS_FILE,
    NO_VOLUMES,
    PART_SUFFIX,
    STOP_TIMEOUT_S,
    VolumeBackup,
    archive_name,
    backup_folder,
    cmd_archive,
    cmd_pull_backup_image,
    cmd_start_services,
    cmd_stop_archive,
    cmd_stop_services,
    contents,
    restore_hint,
)
from .stacks import DockerStacks, Stack, StackProblem, no_such_file

log = logging.getLogger(__name__)

UPDATE_ACTION = "docker.stack_update"
ROLLBACK_ACTION = "docker.stack_rollback"
BACKUPS_FILE = "stack-backups.json"
PULL_TIMEOUT_S = 2700
BACKUP_UNTIL_S = 2700
"""Spätestens so lange nach dem Start der Aktion endet die Sicherung; danach bleibt bis zur Grenze der Freigabe Zeit für den Rest."""
UP_TIMEOUT_S = 900
READ_TIMEOUT_S = 60
MAX_SERVICES = 40
IMAGES_FORMAT = (
    '{"name":{{json .Name}},"image_id":{{json .Image}},"ref":{{json .Config.Image}},"running":{{json .State.Running}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}},"mounts":{{json .Mounts}}}'
)
NO_AUTO_BACK = (
    "Weil ein Datenspeicher dabei ist, geht es nicht von selbst zurück: Eine alte Version kann mit Daten, die die neue schon "
    "umgebaut hat, Schaden anrichten. Prüfe den Stack; „Zurück zur vorigen Version“ steht im Reiter Stacks bereit."
)
STILL_RUNNING = (
    "Compose kann auf dem Server noch laufen, deshalb geht es nicht von selbst zurück. Prüfe den Stack; „Zurück zur vorigen "
    "Version“ steht im Reiter Stacks bereit."
)


def stack_actions_specs() -> list[ActionSpec]:
    common: dict[str, Any] = {"permissions": ["hosts.execute"], "host_bound": False}
    return [
        ActionSpec(
            action_type=UPDATE_ACTION, label="Stack aktualisieren", icon="download", default_risk=Risk.MEDIUM,
            description="Holt neue Images für die Dienste eines übernommenen Stacks, behält die alten als Sicherung und "
            "erstellt die Dienste neu; startet einer nicht, geht es zurück (außer bei Datenspeichern).",
            confirm_text="Die Dienste mit Update werden neu erstellt und sind dabei kurz nicht erreichbar. Fortfahren?", **common,
        ),
        ActionSpec(
            action_type=ROLLBACK_ACTION, label="Stack zurück zur vorigen Version", icon="rotate-ccw", default_risk=Risk.HIGH,
            description="Setzt die Dienste eines übernommenen Stacks auf die Images vor dem letzten Update zurück.",
            confirm_text="Die Dienste starten wieder mit ihren alten Images. Daten, die die neue Version schon umgebaut hat, bleiben "
            "so. Fortfahren?", **common,
        ),
    ]


def rollback_ref(project: str, service: str, tag: str = "previous") -> str:
    """`lattice-rollback/stack-<projekt>-<dienst>-<kurzer Hash>:previous`, die Sicherung eines Dienstes; `:pending` hält
    das laufende Image während des Holens fest."""
    if not PROJECT_RE.match(project) or not SERVICE_RE.match(service) or tag not in ("previous", "pending"):
        raise ValueError(f"Ungültiger Dienst: {project!r}/{service!r}")
    slug = re.sub(r"[^a-z0-9]+", "-", f"{project}-{service}".lower()).strip("-")[:60].strip("-")
    ref = f"lattice-rollback/stack-{slug}-{hashlib.sha256(f'{project}/{service}'.encode()).hexdigest()[:8]}:{tag}"
    if not is_valid_ref(ref):
        raise ValueError(f"Ungültiger Sicherungs-Name: {ref!r}")
    return ref


def _services(services: list[str]) -> list[str]:
    if not services or len(services) > MAX_SERVICES or not all(SERVICE_RE.match(s) for s in services):
        raise ValueError(f"Ungültige Dienste: {services!r}")
    return services


def cmd_container_images(names: list[str]) -> list[str]:
    """Je Container eine JSON-Zeile: Name, Dienst, Image-Name aus der Datei, ob er läuft und die ID seines Images."""
    if not names or not all(CONTAINER_NAME_RE.match(n) for n in names):
        raise ValueError(f"Ungültige Container: {names!r}")
    return ["docker", "inspect", "--type", "container", "--format", IMAGES_FORMAT, *names]


def cmd_image_id(ref: str) -> list[str]:
    """Die ID, auf die ein Image-Name gerade zeigt."""
    if not is_valid_ref(ref):
        raise ValueError(f"Ungültiger Image-Name: {ref!r}")
    return ["docker", "image", "inspect", "--format", "{{.Id}}", ref]


def cmd_stack_pull(stack: Stack, services: list[str]) -> list[str]:
    return _in_folder(stack, "pull", *_services(services))


def cmd_stack_up(stack: Stack, services: list[str]) -> list[str]:
    # --no-deps: nur diese Dienste; --no-build: nie bauen; --pull never: genau die Images, die schon da sind.
    return _in_folder(stack, "up", "-d", "--no-deps", "--no-build", "--pull", "never", *_services(services))


def cmd_untag(ref: str) -> list[str]:
    """Nimmt einen Sicherungs-Namen weg; das Image selbst bleibt unter seinen anderen Namen."""
    if not ref.startswith("lattice-rollback/stack-") or not ref.endswith((":pending", ":previous")) or not is_valid_ref(ref):
        raise ValueError(f"Kein Sicherungs-Name: {ref!r}")
    return ["docker", "image", "rm", ref]


def cmd_tag(source: str, target: str) -> list[str]:
    """Ein Image unter einem zweiten Namen; `source` ist eine Image-ID oder ein Name."""
    if not (IMAGE_ID_RE.match(source) or is_valid_ref(source)) or not is_valid_ref(target):
        raise ValueError(f"Ungültiges Taggen: {source!r} -> {target!r}")
    return ["docker", "tag", source, target]


@dataclass(frozen=True)
class Running:
    service: str
    container: str
    ref: str
    image_id: str
    containers: tuple[str, ...] = ()
    mixed: bool = False
    """Die Container des Dienstes laufen mit verschiedenen Images."""
    running: bool = True
    volumes: tuple[str, ...] = ()
    """Die benannten Volumes seiner Container."""
    up: tuple[str, ...] = ()
    """Die Container, die gerade laufen."""
    refs: tuple[str, ...] = ()
    """Die Image-Namen seiner Container, jeder einmal."""


def parse_container_images(text: str) -> dict[str, Running]:
    """Dienst -> seine Container mit Image-Name und Image-ID; Zeilen, die nicht der Form entsprechen, fallen weg."""
    rows: dict[str, list[tuple[str, str, str, bool, frozenset[str]]]] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").lstrip("/")
        service, ref, image_id = (str(row.get(k) or "") for k in ("service", "ref", "image_id"))
        mounts = row.get("mounts") if isinstance(row.get("mounts"), list) else []
        volumes = frozenset(
            str(m.get("Name")) for m in mounts
            if isinstance(m, dict) and m.get("Type") == "volume" and VOLUME_NAME_RE.match(str(m.get("Name") or ""))
        )
        if SERVICE_RE.match(service) and CONTAINER_NAME_RE.match(name) and is_valid_ref(ref) and IMAGE_ID_RE.match(image_id):
            rows.setdefault(service, []).append((name, ref, image_id, row.get("running") is True, volumes))
    out = {}
    for service, found in rows.items():
        name, ref, image_id, _, _ = found[0]
        mixed = len({r[1] for r in found}) > 1 or len({r[2] for r in found}) > 1
        volumes = tuple(sorted(set().union(*(r[4] for r in found))))
        out[service] = Running(service, name, ref, image_id, tuple(r[0] for r in found), mixed, any(r[3] for r in found), volumes,
                               tuple(r[0] for r in found if r[3]), tuple(dict.fromkeys(r[1] for r in found)))
    return out


def with_sharers(images: dict[str, str], services: list[str]) -> list[str]:
    """Die Dienste samt allen, die denselben Image-Namen nutzen: Ein Update verschiebt den Namen für alle zugleich."""
    refs = {images[s] for s in services if s in images}
    return list(services) + sorted(other for other, ref in images.items() if ref in refs and other not in services)


@dataclass(frozen=True)
class Backup:
    ref: str
    before: str
    """Die ID der Sicherung, also der Version vor dem Update."""
    after: str
    at: str


def fits(backup: Backup | None, entry: Running, kept: str | None) -> bool:
    """Passt die Sicherung noch zum laufenden Dienst: derselbe Image-Name, er läuft mit dem Stand nach dem Update, und
    `:previous` zeigt noch auf die Version davor."""
    return (
        backup is not None and kept == backup.before and entry.ref == backup.ref and not entry.mixed
        and entry.image_id == backup.after and "@" not in entry.ref
    )


class StackBackups:
    """Wovon `:previous` je Dienst die Sicherung ist, als JSON im Datenordner des Moduls."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / BACKUPS_FILE
        self._lock = asyncio.Lock()

    def _read(self, *, strict: bool = False) -> dict[str, Any]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except OSError:
            if strict:
                raise
            log.warning("docker_stack_backups_unreadable path=%s", self._path)
            return {}
        except ValueError:
            log.warning("docker_stack_backups_broken path=%s (wird ignoriert)", self._path)
            return {}
        backups = raw.get("backups") if isinstance(raw, dict) else None
        return backups if isinstance(backups, dict) else {}

    def get(self, host_id: str, project: str) -> dict[str, Backup]:
        projects = self._read().get(host_id)
        services = projects.get(project) if isinstance(projects, dict) else None
        out: dict[str, Backup] = {}
        for service, raw in (services.items() if isinstance(services, dict) else ()):
            if not isinstance(raw, dict) or not SERVICE_RE.match(str(service)):
                continue
            entry = Backup(*(str(raw.get(k) or "") for k in ("ref", "before", "after", "at")))
            if is_valid_ref(entry.ref) and IMAGE_ID_RE.match(entry.before) and IMAGE_ID_RE.match(entry.after):
                out[service] = entry
        return out

    async def _change(self, host_id: str, project: str, change: Callable[[dict[str, Any]], None]) -> None:
        async with self._lock:
            data = self._read(strict=True)
            hosts = {h: dict(p) for h, p in data.items() if isinstance(p, dict)}
            projects = hosts.setdefault(host_id, {})
            services = {s: dict(e) for s, e in (projects.get(project) or {}).items() if isinstance(e, dict)}
            change(services)
            if services:
                projects[project] = services
            else:
                projects.pop(project, None)
            if not projects:
                hosts.pop(host_id, None)
            _write_atomic(self._path, json.dumps({"backups": hosts}, indent=1, sort_keys=True))

    async def put(self, host_id: str, project: str, entries: dict[str, Backup]) -> None:
        def add(found: dict[str, Any]) -> None:
            found.update({s: vars(e) for s, e in entries.items()})

        await self._change(host_id, project, add)

    async def drop(self, host_id: str, project: str, services: Iterable[str] | None = None) -> None:
        names = None if services is None else set(services)

        def remove(found: dict[str, Any]) -> None:
            for service in [s for s in found if names is None or s in names]:
                del found[service]

        await self._change(host_id, project, remove)


class StackUpdate:
    """Was ein Update eines Stacks bringen würde; ausgeführt wird nur in den Ausführern."""

    def __init__(
        self, ctx: Any, stacks: DockerStacks, images: ImageUpdateService, actions: DockerStackExecutor, backups: StackBackups,
        volumes: VolumeBackup,
    ) -> None:
        self._ctx = ctx
        self._stacks = stacks
        self._images = images
        self.actions = actions
        self.backups = backups
        self.volumes = volumes

    async def running(self, host: Any, stack: Stack) -> dict[str, Running]:
        found = [s for s in await self._stacks.stacks(host, stack.project) if s.project == stack.project]
        names = [m.name for s in found for m in s.members if CONTAINER_NAME_RE.match(m.name)]
        if not names:
            return {}
        result = await self._exec(host, cmd_container_images(names))
        if result.exit_code != 0:
            raise StackProblem(f"Docker auf dem Server ließ sich nicht abfragen ({failure_line(result.stderr or result.stdout)}).")
        return parse_container_images(result.stdout)

    async def image_id(self, host: Any, ref: str) -> str | None:
        result = await self._exec(host, cmd_image_id(ref))
        found = result.stdout.strip()
        return found if result.exit_code == 0 and IMAGE_ID_RE.match(found) else None

    async def _exec(self, host: Any, argv: list[str]) -> Any:
        """Ein lesender Aufruf; reißt die Verbindung ab, ist das ein `StackProblem` mit Grund."""
        try:
            return await self._ctx.exec.run_argv(host, argv, timeout_s=READ_TIMEOUT_S)
        except Exception as exc:
            if not _connection_error(exc):
                raise
            raise StackProblem(f"Docker auf dem Server ließ sich nicht abfragen ({_lost(exc)}).") from exc

    async def unapplied(self, host: Any, stack: Stack) -> list[str] | str:
        """Die Dienste, die in der Datei anders beschrieben sind als ihre Container, oder warum sich das nicht prüfen ließ."""
        found = [s for s in await self._stacks.stacks(host, stack.project) if s.project == stack.project]
        if not found:
            return []
        changes = await self._stacks.pending(host, found[0])
        return changes if isinstance(changes, str) else changes["changed"]

    async def fitting(self, host: Any, stack: Stack, running: dict[str, Running]) -> dict[str, Backup]:
        """Die Sicherungen, die noch zu ihrem laufenden Dienst passen."""
        out = {}
        for service, backup in self.backups.get(host.id, stack.project).items():
            if service in running and fits(backup, running[service], await self.image_id(host, rollback_ref(stack.project, service))):
                out[service] = backup
        return out

    async def plan(self, host: Any, stack: Stack) -> dict[str, Any]:
        """Je Dienst der Stand der Image-Prüfung, welche Dienste ein Update bekämen und wofür es eine passende Sicherung gibt."""
        running = await self.running(host, stack)
        backups = await self.fitting(host, stack, running)
        services = []
        for service, entry in sorted(running.items()):
            result = self._images.result_for(host.id, entry.container) or {}
            services.append({
                "service": service, "container": entry.container, "image": entry.ref, "status": result.get("status"),
                "reason": result.get("reason"), "data_store": is_data_store(entry.ref, service), "stopped": not entry.running,
                "mixed": entry.mixed, "backup": service in backups, "backup_at": backups[service].at if service in backups else None,
                "volumes": list(entry.volumes),
            })
        updates = [s["service"] for s in services if s["status"] == "update" and not s["stopped"] and not s["mixed"]]
        changes = await self.unapplied(host, stack) if updates or backups else []
        together = with_sharers({e.service: e.ref for e in running.values()}, updates)
        stored = any(s["data_store"] for s in services if s["service"] in together)
        volumes = sorted({v for s in services if s["service"] in together for v in s["volumes"]})
        return {
            "project": stack.project, "services": services, "updates": updates, "data_store": stored,
            "risk": (Risk.HIGH if stored else Risk.MEDIUM).value, "rollback": sorted(backups),
            "unapplied": changes if isinstance(changes, list) else [], "unapplied_error": changes if isinstance(changes, str) else None,
            "volume_backup": await self._volume_plan(host, stack, running, together, volumes) if updates else None,
        }

    async def _volume_plan(self, host: Any, stack: Stack, running: dict[str, Running], together: list[str], volumes: list[str]) -> dict[str, Any]:
        plan = await self.volumes.plan(host, stack.project, volumes)
        if plan["error"] is None and volumes:
            plan["error"] = backup_refusal(_uses(running), together)
        return plan


def backup_refusal(uses: dict[str, tuple[tuple[str, ...], bool]], services: Iterable[str]) -> str | None:
    """Warum es für diese Auswahl keine Sicherung geben kann, je Dienst (Volumes, läuft): keine benannten Volumes, oder ein
    laufender Dienst außerhalb der Auswahl schreibt in eines davon."""
    chosen = set(services)
    volumes = {v for s in chosen if s in uses for v in uses[s][0]}
    if not volumes:
        return NO_VOLUMES
    for service, (theirs, running) in sorted(uses.items()):
        shared = sorted(volumes & set(theirs))
        if service not in chosen and running and shared:
            return (f"Das Volume {shared[0]} nutzt auch der Dienst „{service}“, der dabei weiterliefe; das Archiv wäre nicht "
                    f"verlässlich. Stoppe „{service}“ vorher oder aktualisiere ohne Sicherung.")
    return None


def _plan_refusal(plan: dict[str, Any], chosen: list[str]) -> str | None:
    """Vor der Freigabe dieselbe Prüfung wie im Ausführer, für die gewählten Dienste statt für alle mit Update."""
    uses = {row["service"]: (tuple(row["volumes"]), not row["stopped"]) for row in plan["services"]}
    if (refusal := backup_refusal(uses, chosen)) is not None:
        return refusal
    error = (plan["volume_backup"] or {}).get("error")
    together = with_sharers({row["service"]: row["image"] for row in plan["services"]}, plan["updates"])
    # Der Plan prüft gemeinsam genutzte Volumes für alle Dienste mit Update; für die Auswahl zählt die Prüfung oben.
    return None if error == backup_refusal(uses, together) else error


def _leftovers(folder: str, volume: str) -> str:
    name = archive_name(volume, folder)
    return (f" Der Archiv-Container {name} packt vielleicht noch, und im Ordner {folder} kann die unfertige Datei "
            f"{volume}{PART_SUFFIX} liegen. Beende ihn mit „docker rm -f {name}“ und lösche die Datei.")


def _uses(current: dict[str, Running]) -> dict[str, tuple[tuple[str, ...], bool]]:
    return {service: (entry.volumes, entry.running) for service, entry in current.items()}


async def _write_contents(sftp: Any, folder: str, volumes: list[str], kind: Any) -> None:
    """Legt die Inhaltsliste neu an, nur für den Zugang lesbar; `kind` ist die Attribut-Klasse des SFTP-Clients."""
    async with sftp.open(f"{folder}/{CONTENTS_FILE}", "xb", kind(permissions=0o600)) as handle:
        # Nicht jeder SFTP-Server übernimmt die Rechte beim Anlegen; vor dem Schreiben noch einmal setzen.
        with contextlib.suppress(Exception):
            await handle.chmod(0o600)
        await handle.write(contents(volumes))


class TimedOut(str):
    """Ein Grund, nach dem Compose auf dem Server noch laufen kann."""


def _fail(error: str, started: float, output: str | None = None) -> ActionResult:
    return ActionResult(success=False, error=error, output=output, duration_ms=int((time.monotonic() - started) * 1000))


def _report_after_abort(
    project: str, folder: str, packing: str | None, packed: list[str], cleanup: asyncio.Task[tuple[str, str | None]],
) -> None:
    """Nach einem Abbruch gibt es keine Meldung mehr; was liegen bleibt oder nicht wieder startet, steht dann im Log."""
    if cleanup.cancelled():
        return
    if (exc := cleanup.exception()) is not None:
        log.warning("docker_stack_clean_up_after_abort_failed folder=%s", folder, exc_info=exc)
        return
    left, not_started = cleanup.result()
    if left:
        log.warning("docker_stack_backup_left_after_abort folder=%s packed=%s packing=%s", folder, ",".join(packed) or "-", packing or "-")
    if not_started:
        log.warning("docker_stack_services_not_started_after_abort project=%s reason=%s", project, not_started)


class DockerStackUpdateExecutor:
    """Aktualisiert die Dienste eines übernommenen Stacks und setzt sie auf Wunsch zurück."""

    action_types = frozenset({UPDATE_ACTION, ROLLBACK_ACTION})

    def __init__(self, ctx: Any, update: StackUpdate, images: ImageUpdateService) -> None:
        self._ctx = ctx
        self._update = update
        self._images = images
        self._rechecks: set[asyncio.Task[None]] = set()
        self._cleanups: set[asyncio.Task[tuple[str, str | None]]] = set()

    async def execute(self, req: ActionRequest) -> ActionResult:
        payload = req.payload or {}
        if getattr(req.proposed_by.type, "value", req.proposed_by.type) != "user":
            return ActionResult(success=False, error="Einen Stack aktualisiert nur ein Mensch über die Docker-Seite.")
        target = await self._update.actions.target(req.host_ref, str(payload.get("project") or ""))
        if isinstance(target, str):
            return ActionResult(success=False, error=target)
        host, stack = target
        services = payload.get("services")
        if not isinstance(services, list) or not services or len(services) > MAX_SERVICES or not all(isinstance(s, str) and SERVICE_RE.match(s) for s in services):
            return ActionResult(success=False, error="Ungültige Dienste.")
        what = "Update" if req.action_type == UPDATE_ACTION else "Rückweg"
        with self._update.actions.locks.hold(host.id, stack.project, what) as busy:
            if busy is not None:
                return ActionResult(success=False, error=busy_text(busy))
            try:
                if req.action_type == UPDATE_ACTION:
                    return await self._update_stack(req, host, stack, services, payload.get("backup") is True)
                return await self._roll_back(req, host, stack, services)
            except StackProblem as exc:
                return ActionResult(success=False, error=str(exc))

    async def _exec(self, host: Any, argv: list[str], timeout_s: int, what: str) -> str | None:
        """Ein Schritt auf dem Server; bei einem Fehler der Grund für Menschen. Läuft die Zeit ab oder reißt die Verbindung ab,
        ist offen, was auf dem Server noch läuft: dann als `TimedOut`."""
        try:
            result = await self._ctx.exec.run_argv(host, argv, timeout_s=timeout_s)
        except Exception as exc:
            if not _connection_error(exc):
                raise
            if type(exc) is TimeoutError:
                return TimedOut(f"{what}: nach {timeout_s // 60} Minuten ohne Ende abgebrochen.")
            return TimedOut(f"{what}: {_lost(exc)}.")
        if result.exit_code != 0:
            return f"{what}: {failure_line(result.stderr or result.stdout)}"
        return None

    async def _prepare(self, host: Any, stack: Stack, services: list[str], doing: str) -> tuple[dict[str, Running], list[str]] | str:
        """Die laufenden Dienste und die Auswahl samt Diensten mit demselben Image, oder warum es so nicht geht."""
        try:
            current = await self._update.running(host, stack)
        except StackProblem as exc:
            return f"Die Container ließen sich nicht lesen. {exc}"
        missing = [s for s in services if s not in current]
        if missing:
            return f"Zu {', '.join(missing)} gibt es gerade keinen Container. Wende den Stack an und versuch es dann noch einmal."
        services = with_sharers({s: e.ref for s, e in current.items()}, services)
        for service in services:
            entry = current[service]
            if "@" in entry.ref:
                return f"„{service}“ steht in der Datei mit einer festen Prüfsumme (…@sha256:…); dafür gibt es kein Update und keinen Rückweg."
            if entry.mixed:
                return f"Die Container von „{service}“ laufen mit verschiedenen Images. Wende den Stack einmal an und versuch es dann noch einmal."
            if not entry.running:
                return f"„{service}“ ist gestoppt und würde dabei gestartet. Starte ihn zuerst, oder er bekommt das Update beim nächsten Anwenden."
        changes = await self._update.unapplied(host, stack)
        if isinstance(changes, str):
            return changes
        hit = [s for s in services if s in changes]
        if hit:
            return (f"In der Datei steht für {', '.join(hit)} etwas anderes, als gerade läuft. {doing} würde diese Änderung mit anwenden. "
                    "Wende den Stack zuerst an oder nimm die Änderung zurück, dann versuch es noch einmal.")
        return current, services

    async def _update_stack(self, req: ActionRequest, host: Any, stack: Stack, services: list[str], backup: bool) -> ActionResult:
        started = time.monotonic()
        prepared = await self._prepare(host, stack, services, "Ein Update")
        if isinstance(prepared, str):
            return _fail(f"{prepared} Geändert wurde nichts.", started)
        current, services = prepared
        stored = any(is_data_store(current[s].ref, s) for s in services)
        if stored and RISK_ORDER.index(req.risk) < RISK_ORDER.index(Risk.HIGH):
            return _fail("Dabei ist ein Datenspeicher; dafür braucht es eine Freigabe mit hohem Risiko. Bitte noch einmal aktualisieren.", started)
        held: set[str] = set()
        try:
            return await self._update_held(host, stack, services, current, stored, held, started, backup)
        finally:
            # Auch bei einem Abbruch (Neustart, Zeitgrenze der Freigabe) die Zwischenstände wieder weg.
            await asyncio.shield(self._release(host, held))

    async def _update_held(
        self, host: Any, stack: Stack, services: list[str], current: dict[str, Running], stored: bool, held: set[str], started: float,
        backup: bool,
    ) -> ActionResult:
        # Vor dem Holen festhalten: Ohne Namen bliebe das alte Image nur als namenloses liegen, und ein Aufräumen
        # (`docker image prune`) dazwischen nähme es mit.
        if backup and (refusal := backup_refusal(_uses(current), services)) is not None:
            return _fail(f"Sicherung der Volumes nicht möglich: {refusal} Geändert wurde nichts; aktualisiere ohne Sicherung oder "
                         "wähle die Dienste anders.", started)
        pending = {s: rollback_ref(stack.project, s, "pending") for s in services}
        for service in services:
            if (problem := await self._exec(host, cmd_tag(current[service].image_id, pending[service]), READ_TIMEOUT_S, "Sicherung ließ sich nicht anlegen")) is not None:
                if isinstance(problem, TimedOut):
                    # Ob der Name schon steht, ist offen; das Aufräumen nimmt ihn mit, falls doch.
                    held.add(pending[service])
                    return _fail(f"{problem} Geändert wurde nichts.", started)
                return _fail(f"{problem} Läuft „{service}“ mit einem Image, das keinen Namen mehr hat, wende den Stack einmal an. Geändert wurde nichts.", started)
            held.add(pending[service])
        if (problem := await self._exec(host, cmd_stack_pull(stack, services), PULL_TIMEOUT_S, "Holen der Images ist gescheitert")) is not None:
            return _fail(f"{problem} Die Dienste laufen weiter wie bisher; einzelne neue Images können schon geladen sein.", started)
        try:
            new = {s: await self._update.image_id(host, current[s].ref) for s in services}
        except StackProblem as exc:
            return _fail(f"{exc} Aktualisiert wurde nichts; einzelne neue Images können schon geladen sein.", started)
        if unknown := [s for s in services if new[s] is None]:
            return _fail(f"Die Image-ID von {', '.join(unknown)} ließ sich nach dem Holen nicht lesen. Aktualisiert wurde nichts; "
                         "einzelne neue Images können schon geladen sein. Versuch es gleich noch einmal.", started)
        changed = [s for s in services if new[s] != current[s].image_id]
        if not changed:
            return ActionResult(success=True, exit_code=0, duration_ms=int((time.monotonic() - started) * 1000),
                                output=f"Für „{stack.project}“ gab es nichts Neues; die Dienste laufen weiter wie bisher.", detail={"updated": []})
        saved = ""
        volumes = sorted({v for s in changed for v in current[s].volumes})
        # Nur ein Teil der Auswahl hat ein neues Image; für diesen Teil gilt dieselbe Prüfung noch einmal.
        if backup and (refusal := backup_refusal(_uses(current), changed)) is not None:
            return _fail(f"Sicherung der Volumes nicht möglich: {refusal} Aktualisiert wurde nichts; die Dienste laufen weiter mit der "
                         "alten Version. Aktualisiere ohne Sicherung.", started)
        if backup:
            folder, problem, not_started = await self.save_volumes(host, stack, changed, volumes, started)
            if problem is not None:
                after = ("die Dienste laufen weiter mit der alten Version." if not_started is None else
                         f"die Dienste ließen sich aber nicht wieder starten ({not_started}). Starte den Stack im Reiter Stacks.")
                return _fail(f"{problem} Aktualisiert wurde nichts; {after}", started)
            saved = f" Volumes gesichert in {folder}. {restore_hint()}"
        try:
            return await self._finish(host, stack, changed, current, new, pending, stored, held, started, saved)
        except BaseException:
            # Gestoppt für die Sicherung: Auch bei einem Abbruch (Zeitgrenze der Freigabe, Neustart) laufen sie wieder an.
            if saved:
                await asyncio.shield(self.start_again(host, stack, changed))
            raise

    async def _finish(
        self, host: Any, stack: Stack, changed: list[str], current: dict[str, Running], new: dict[str, str | None],
        pending: dict[str, str], stored: bool, held: set[str], started: float, saved: str,
    ) -> ActionResult:
        for service in changed:
            problem = await self._exec(host, cmd_tag(pending[service], rollback_ref(stack.project, service)), READ_TIMEOUT_S, "Sicherung ließ sich nicht anlegen")
            if problem is not None:
                return _fail(f"{problem} Aktualisiert wurde nichts; die neuen Images liegen schon bereit.{await self._restart(host, stack, changed, saved)}", started)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            await self._update.backups.put(host.id, stack.project, {s: Backup(current[s].ref, current[s].image_id, str(new[s]), now) for s in changed})
        except OSError as exc:
            return _fail(f"Die Sicherung ließ sich nicht vermerken ({exc}). Aktualisiert wurde nichts; die neuen Images liegen schon "
                         f"bereit.{await self._restart(host, stack, changed, saved)}", started)
        await self._release(host, held)
        names = ", ".join(changed)
        problem = await self._exec(host, cmd_stack_up(stack, changed), UP_TIMEOUT_S, "Neu erstellen ist gescheitert")
        timed_out = isinstance(problem, TimedOut)
        problem = problem or await self._verify(host, stack, {s: str(new[s]) for s in changed})
        verdict = None if problem else await self._check(host, stack, changed)
        if problem is None and verdict is not None and verdict.ok:
            self._recheck(host, [current[s].container for s in changed])
            return ActionResult(
                success=True, exit_code=0, duration_ms=int((time.monotonic() - started) * 1000),
                output=f"Stack „{stack.project}“ aktualisiert: {names}. Prüfung danach: {verdict.text} Die alten Images bleiben als Sicherung.{saved}",
                detail={"updated": changed, "check": {"ok": True, "text": verdict.text}},
            )
        reason = problem or f"Prüfung danach: {verdict.text if verdict else 'nicht möglich.'}"
        if timed_out:
            return _fail(f"Update von {names} nicht sauber gelaufen. {reason} {STILL_RUNNING}{saved}", started)
        if stored:
            return _fail(f"Update von {names} nicht sauber gelaufen. {reason} {NO_AUTO_BACK}{saved}", started)
        _, back = await self._back(host, stack, {s: (current[s].ref, current[s].image_id, str(new[s])) for s in changed})
        self._recheck(host, [current[s].container for s in changed])
        return _fail(f"Update von {names} nicht sauber gelaufen. {reason} {back}{saved}", started)

    async def _restart(self, host: Any, stack: Stack, services: list[str], saved: str) -> str:
        """Nach einer Sicherung sind die Dienste gestoppt; bricht das Update danach ab, laufen sie wieder an."""
        if not saved:
            return ""
        problem = await self.start_again(host, stack, services)
        return f" Die Dienste sind wieder gestartet.{saved}" if problem is None else f" {problem} Starte den Stack im Reiter Stacks.{saved}"

    async def start_again(self, host: Any, stack: Stack, services: list[str]) -> str | None:
        """Startet Dienste, die für eine Sicherung gestoppt waren; `None`, wenn das geklappt hat, sonst der Grund."""
        if not services:
            return None
        return await self._exec(host, cmd_start_services(stack, services), STOP_TIMEOUT_S, "Wieder starten gescheitert")

    async def save_volumes(
        self, host: Any, stack: Stack, services: list[str], volumes: list[str], started: float,
    ) -> tuple[str, str | None, str | None]:
        """Stoppt die Dienste und packt die Volumes, auch für das Einspielen aus Git: (Ordner, Grund, falls es nicht ging, Grund,
        falls das Wiederstarten danach scheiterte)."""
        plan = await self._update.volumes.plan(host, stack.project, volumes)
        found = await self._update.volumes.home(host)
        if plan["error"] or found is None:
            return "", f"Sicherung der Volumes nicht möglich: {plan['error'] or 'Der Heimatordner ließ sich nicht lesen.'}", None
        if await self._update.image_id(host, BACKUP_IMAGE) is None and (
            problem := await self._exec(host, cmd_pull_backup_image(), PULL_TIMEOUT_S, f"{BACKUP_IMAGE} für die Archive ließ sich nicht laden")
        ) is not None:
            return "", problem, None
        home, owner = found
        folder = backup_folder(home, stack.project, time.strftime("%Y%m%d-%H%M%S", time.gmtime()))
        step = "Der Ordner für die Sicherung ließ sich nicht anlegen"
        reached = False
        try:
            async with self._ctx.exec.sftp(host) as sftp:
                kind = type(await sftp.stat(home))
                path = home
                for depth, part in enumerate((BACKUP_ROOT, stack.project, folder.rsplit("/", 1)[1])):
                    path = f"{path}/{part}"
                    reached = depth == 2
                    try:
                        await sftp.mkdir(path, kind(permissions=0o700))
                    except Exception as first:  # ein vorhandener Ordner ist recht
                        try:
                            attrs = await sftp.lstat(path)
                        except Exception as second:  # noqa: BLE001 - gibt es den Ordner nicht, zählt der Grund des mkdir
                            raise (first if no_such_file(second) else second) from None
                        if not statmod.S_ISDIR(attrs.permissions or 0):
                            raise
                step = "Die Inhaltsliste der Sicherung ließ sich nicht anlegen"
                await _write_contents(sftp, folder, volumes, kind)
        except Exception as exc:  # noqa: BLE001 - als Grund melden
            # Ging beim Zeitordner nur die Antwort verloren, steht er womöglich schon.
            removed = not reached or await asyncio.shield(self._remove_folder(host, folder))
            left = "" if removed else (
                f" Der Ordner {folder} kann noch leer auf dem Server liegen; lösche ihn im Reiter Stacks unter „Sicherungen“.")
            reason = (str(exc) or type(exc).__name__).rstrip(".")
            return "", f"{step}: {reason}.{left}", None
        except BaseException:
            # Der Server kann den Ordner schon angelegt haben; auch ein zweiter Abbruch soll ihn nicht liegen lassen.
            await asyncio.shield(self._remove_folder(host, folder))
            raise
        packing: str | None = None
        packed: list[str] = []
        try:
            problem = (await self._exec(host, cmd_stop_services(stack, services), STOP_TIMEOUT_S, "Stoppen vor der Sicherung gescheitert")
                       if services else None)
            deadline = min(time.monotonic() + ARCHIVE_BUDGET_S, started + BACKUP_UNTIL_S)
            for volume in volumes if problem is None else ():
                left = int(deadline - time.monotonic())
                if left <= 0:
                    problem = f"Für die Sicherung reichte die Zeit nicht, {volume} ist nicht mehr dran gekommen."
                    break
                packing = volume
                if (problem := await self._exec(host, cmd_archive(volume, folder, owner), left, f"Sicherung von {volume} gescheitert")) is not None:
                    break
                packing = None
                packed.append(volume)
        except BaseException:
            # Als eigene Aufgabe: Auch ein zweiter Abbruch überspringt weder das Wiederstarten noch das Aufräumen.
            cleanup = self._clean_up_task(host, stack, services, folder, packing, packed)
            cleanup.add_done_callback(functools.partial(_report_after_abort, stack.project, folder, packing, list(packed)))
            await asyncio.wait([cleanup])
            raise
        if problem is not None:
            cleanup = self._clean_up_task(host, stack, services, folder, packing, packed)
            try:
                left, not_started = await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cleanup.add_done_callback(functools.partial(_report_after_abort, stack.project, folder, packing, list(packed)))
                raise
            return "", f"{problem}{left}", not_started
        return folder, None, None

    def _clean_up_task(
        self, host: Any, stack: Stack, services: list[str], folder: str, packing: str | None, packed: list[str],
    ) -> asyncio.Task[tuple[str, str | None]]:
        """Das Aufräumen als eigene Aufgabe, die ein Abbruch der Aktion nicht mitreißt; gehalten, bis sie fertig ist."""
        task = asyncio.get_running_loop().create_task(self._clean_up(host, stack, services, folder, packing, packed))
        self._cleanups.add(task)
        task.add_done_callback(self._cleanups.discard)
        return task

    async def _clean_up(
        self, host: Any, stack: Stack, services: list[str], folder: str, packing: str | None, packed: list[str],
    ) -> tuple[str, str | None]:
        """Nach einem Fehlschlag oder Abbruch: erst ein laufendes Archiv beenden und seine halbe Datei löschen, dann die Dienste starten
        und einen leeren Zeitordner entfernen (Hinweis auf Reste, Grund, falls das Starten scheiterte)."""
        hint = ""
        if packing is not None:
            try:
                hint = await self._stop_packing(host, folder, packing)
            except Exception:
                log.warning("docker_stack_archive_not_stopped folder=%s volume=%s", folder, packing, exc_info=True)
                hint = _leftovers(folder, packing)
        try:
            not_started = await self.start_again(host, stack, services)
        finally:  # auch wenn das Starten wirft
            left = hint + await self._unfinished(host, folder, packed, hint)
        return left, not_started

    async def _unfinished(self, host: Any, folder: str, packed: list[str], hint: str) -> str:
        """Ist nichts fertig gepackt, geht der Zeitordner weg; sonst nennt die Meldung, was dort liegt."""
        if packed:
            return (f" Schon gepackt: {', '.join(packed)} in {folder}. Diese Sicherung ist unvollständig; brauchst du sie nicht, lösche "
                    "sie im Reiter Stacks unter „Sicherungen“.")
        if hint or await self._remove_folder(host, folder):
            return ""
        return f" Der Ordner {folder} ließ sich nicht entfernen; sieh nach, ob darin schon ein fertiges Archiv liegt."

    async def _remove_folder(self, host: Any, folder: str) -> bool:
        """Entfernt den Zeitordner, wenn darin höchstens die Inhaltsliste liegt; False, wenn er danach noch da sein kann."""
        try:
            async with self._ctx.exec.sftp(host) as sftp:
                if {str(n) for n in await sftp.listdir(folder)} - {".", ".."} == {CONTENTS_FILE}:
                    try:
                        await sftp.remove(f"{folder}/{CONTENTS_FILE}")
                    except Exception as exc:
                        if not no_such_file(exc):
                            raise
                await sftp.rmdir(folder)
        except Exception as exc:
            if no_such_file(exc):
                return True
            log.warning("docker_stack_backup_folder_not_removed folder=%s", folder, exc_info=True)
            return False
        return True

    async def _stop_packing(self, host: Any, folder: str, volume: str) -> str:
        """Beendet den Archiv-Container und löscht die unfertige Datei; ein Hinweis, wenn davon etwas nicht klappte."""
        stopped = await self._exec(host, cmd_stop_archive(volume, folder), READ_TIMEOUT_S, "Archiv-Container")
        if stopped is not None and "no such container" not in stopped.lower():
            return _leftovers(folder, volume)
        try:
            async with self._ctx.exec.sftp(host) as sftp:
                await sftp.remove(f"{folder}/{volume}{PART_SUFFIX}")
        except Exception as exc:
            if no_such_file(exc):
                return ""
            log.warning("docker_stack_archive_not_removed folder=%s volume=%s", folder, volume, exc_info=True)
            return _leftovers(folder, volume)
        return ""

    async def _release(self, host: Any, held: set[str]) -> None:
        """Die Zwischenstände wieder weg; scheitert das, bleibt nur ein überzähliger Name stehen."""
        for ref in sorted(held):
            try:
                await self._exec(host, cmd_untag(ref), READ_TIMEOUT_S, "Zwischenstand")
            except Exception:  # Steht im finally; das Ergebnis des Laufs darf es nicht ersetzen.
                log.warning("docker_stack_pending_not_released ref=%s", ref, exc_info=True)
            held.discard(ref)

    async def _verify(self, host: Any, stack: Stack, expected: dict[str, str]) -> str | None:
        """Laufen die Dienste jetzt wirklich mit diesen Images?"""
        try:
            running = await self._update.running(host, stack)
        except (OSError, StackProblem):
            return "Mit welchem Image die Dienste jetzt laufen, ließ sich nicht prüfen."
        wrong = [s for s, image_id in expected.items() if s not in running or running[s].mixed or running[s].image_id != image_id]
        return f"{', '.join(wrong)} läuft nicht mit dem erwarteten Image." if wrong else None

    async def _back(self, host: Any, stack: Stack, targets: dict[str, tuple[str, str, str]]) -> tuple[bool, str]:
        """Setzt die Dienste auf ihre Sicherung zurück, je Dienst (Image-Name, ID davor, ID danach): (läuft wieder, ein Satz)."""
        held: set[str] = set()
        try:
            return await self._back_held(host, stack, targets, held)
        finally:
            await asyncio.shield(self._release(host, held))

    async def _back_held(self, host: Any, stack: Stack, targets: dict[str, tuple[str, str, str]], held: set[str]) -> tuple[bool, str]:
        # Die neue Version erst festhalten, damit sich jeder umgesetzte Name wieder zurückstellen lässt.
        pending = {s: rollback_ref(stack.project, s, "pending") for s in targets}
        for service, (_, _, after) in targets.items():
            if (problem := await self._exec(host, cmd_tag(after, pending[service]), READ_TIMEOUT_S, "Rückweg gescheitert")) is not None:
                return False, f"{problem} Zurückgesetzt wurde nichts; die Sicherungen liegen bereit."
            held.add(pending[service])
        moved: list[str] = []
        for service, (ref, before, _) in targets.items():
            if (problem := await self._exec(host, cmd_tag(before, ref), READ_TIMEOUT_S, "Rückweg gescheitert")) is not None:
                undone = [await self._exec(host, cmd_tag(pending[s], targets[s][0]), READ_TIMEOUT_S, "Zurückstellen") for s in moved]
                if any(undone):
                    return False, f"{problem} Einzelne Image-Namen zeigen schon auf die alte Version, die Dienste laufen aber noch mit der neuen."
                return False, f"{problem} Zurückgesetzt wurde nichts; die Sicherungen liegen bereit."
            moved.append(service)
        if (problem := await self._exec(host, cmd_stack_up(stack, list(targets)), UP_TIMEOUT_S, "Rückweg gescheitert")) is not None:
            if isinstance(problem, TimedOut):
                return False, (f"{problem} Compose kann auf dem Server noch laufen; sieh in ein paar Minuten im Reiter Stacks nach, mit "
                               "welcher Version die Dienste laufen.")
            return False, problem
        if (wrong := await self._verify(host, stack, {s: before for s, (_, before, _) in targets.items()})) is not None:
            return False, f"Der Rückweg hat nicht gegriffen: {wrong}"
        verdict = await self._check(host, stack, list(targets))
        if verdict is not None and verdict.ok:
            return True, f"Zurück auf der vorigen Version, sie läuft wieder. {verdict.text}"
        return False, f"Zurück auf der vorigen Version, aber auch sie läuft nicht sauber: {verdict.text if verdict else 'Prüfung nicht möglich.'}"

    async def _roll_back(self, req: ActionRequest, host: Any, stack: Stack, services: list[str]) -> ActionResult:
        started = time.monotonic()
        prepared = await self._prepare(host, stack, services, "Der Rückweg")
        if isinstance(prepared, str):
            return _fail(f"{prepared} Zurückgesetzt wurde nichts.", started)
        current, services = prepared
        if RISK_ORDER.index(req.risk) < RISK_ORDER.index(Risk.HIGH):
            return _fail("Der Rückweg braucht eine Freigabe mit hohem Risiko.", started)
        backups = await self._update.fitting(host, stack, current)
        for service in services:
            if service not in backups:
                return _fail(f"Für „{service}“ passt die Sicherung nicht mehr zum laufenden Dienst, etwa weil er inzwischen anders "
                             "aktualisiert oder umgestellt wurde. Zurückgesetzt wurde nichts.", started)
        ok, text = await self._back(host, stack, {s: (backups[s].ref, backups[s].before, backups[s].after) for s in services})
        self._recheck(host, [current[s].container for s in services])
        duration_ms = int((time.monotonic() - started) * 1000)
        if ok:
            try:
                await self._update.backups.drop(host.id, stack.project, services)
            except OSError:
                log.warning("docker_stack_backups_not_dropped project=%s", stack.project)
            return ActionResult(success=True, exit_code=0, duration_ms=duration_ms, output=f"Stack „{stack.project}“: {text}")
        return ActionResult(success=False, duration_ms=duration_ms, error=text)

    async def drop_backups(self, host: Any, project: str, services: Iterable[str]) -> None:
        """Nach dem Löschen eines Stacks: seine Sicherungs-Namen und Vermerke weg; was scheitert, bleibt nur als Name stehen."""
        known = {s for s in (*services, *self._update.backups.get(host.id, project)) if SERVICE_RE.match(s)}
        for service in sorted(known):
            for tag in ("previous", "pending"):
                try:
                    await self._exec(host, cmd_untag(rollback_ref(project, service, tag)), READ_TIMEOUT_S, "Sicherung")
                except Exception:  # Der Stack ist schon gelöscht; übrig bleibt höchstens ein Name.
                    log.warning("docker_stack_backup_not_untagged project=%s service=%s", project, service, exc_info=True)
        try:
            await self._update.backups.drop(host.id, project)
        except OSError:
            log.warning("docker_stack_backups_not_dropped project=%s", project)

    async def _check(self, host: Any, stack: Stack, services: list[str]) -> Any:
        from . import after_check as ac  # spät: after_check braucht capabilities

        try:
            running = await self._update.running(host, stack)
        except (OSError, StackProblem):
            return None
        refs = [name for s in services if s in running for name in running[s].containers or (running[s].container,)]
        if not refs:
            return None
        verdict, _ = await ac.check_running(self._ctx, host, refs)
        return verdict

    def _recheck(self, host: Any, containers: list[str]) -> None:
        """Die Image-Prüfung für diese Container im Hintergrund neu, damit „Update verfügbar“ gleich stimmt."""

        async def again() -> None:
            for name in containers:
                try:
                    await self._images.recheck_container(host, name)
                except Exception:  # noqa: BLE001 - betrifft nur die Anzeige der Image-Prüfung
                    continue

        task = asyncio.get_running_loop().create_task(again())
        self._rechecks.add(task)
        task.add_done_callback(self._rechecks.discard)

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        payload = req.payload or {}
        services = ", ".join(str(s) for s in payload.get("services") or []) or "?"
        what = "aktualisieren" if req.action_type == UPDATE_ACTION else "auf die vorige Version zurücksetzen"
        return DryRunReport(would_change=True, summary=f"Würde im Stack „{payload.get('project', '?')}“ {services} {what}.")


class _UpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    services: list[str] = Field(min_length=1, max_length=MAX_SERVICES)
    backup: bool = False


def update_routes(update: StackUpdate, editor: StackEditor, docker_host: Any, current_actor: Any) -> APIRouter:
    router = APIRouter()

    async def _target(host_id: str, project: str) -> tuple[Any, Stack]:
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        host = await docker_host(host_id)
        target = await update.actions.target(host.id, project)
        if isinstance(target, str):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=target)
        return target

    async def _plan(host: Any, stack: Stack) -> dict[str, Any]:
        try:
            return await update.plan(host, stack)
        except (OSError, StackProblem) as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Server nicht erreichbar: {str(exc) or type(exc).__name__}") from exc

    @router.get("/hosts/{host_id}/stacks/{project}/update")
    async def update_plan(host_id: str, project: str) -> dict:
        """Je Dienst der Stand der Image-Prüfung, welche ein Update bekämen und wofür es eine Sicherung gibt."""
        return await _plan(*await _target(host_id, project))

    async def _propose(host_id: str, project: str, body: _UpdateIn, actor: Actor, action_type: str) -> dict:
        host, stack = await _target(host_id, project)
        plan = await _plan(host, stack)
        wanted = plan["updates"] if action_type == UPDATE_ACTION else plan["rollback"]
        unknown = [s for s in body.services if s not in wanted]
        if unknown:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Der Stand hat sich geändert ({', '.join(unknown)}); bitte neu laden.")
        chosen = with_sharers({s["service"]: s["image"] for s in plan["services"]}, list(body.services))
        if plan["unapplied_error"]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan["unapplied_error"])
        if hit := [s for s in chosen if s in plan["unapplied"]]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=(
                f"In der Datei steht für {', '.join(hit)} etwas anderes, als gerade läuft. Wende den Stack zuerst an oder nimm die "
                "Änderung zurück, dann versuch es noch einmal."
            ))
        if action_type == ROLLBACK_ACTION and (missing := [s for s in chosen if s not in plan["rollback"]]):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=(
                f"{', '.join(missing)} nutzt dasselbe Image, hat aber keine passende Sicherung; so ginge nur ein Teil zurück."
            ))
        if action_type == UPDATE_ACTION and body.backup and (refusal := _plan_refusal(plan, chosen)) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Sicherung der Volumes nicht möglich: {refusal}")
        stored = [s["service"] for s in plan["services"] if s["service"] in chosen and s["data_store"]]
        risk = Risk.HIGH if action_type == ROLLBACK_ACTION or stored else Risk.MEDIUM
        extra = [s for s in chosen if s not in body.services]
        names = ", ".join(body.services) + (f" (mit {', '.join(extra)}, die dasselbe Image nutzen)" if extra else "")
        if action_type == UPDATE_ACTION:
            reason = f"Stack „{project}“ aktualisieren: {names}. Die alten Images bleiben als Sicherung."
            if stored:
                reason += f" Datenspeicher dabei ({', '.join(stored)}): kein automatischer Rückweg."
            if body.backup:
                reason += " Vorher werden ihre Volumes gesichert; die Dienste sind dabei gestoppt."
        else:
            reason = f"Stack „{project}“ zurück zur vorigen Version: {names}."
        decision = await editor.propose(ActionRequest(
            action_type=action_type, host_ref=host.id, risk=risk, proposed_by=actor, reason=reason,
            payload={"project": project, "services": chosen, "backup": body.backup and action_type == UPDATE_ACTION},
        ))
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": risk.value, "detail": decision.detail}

    @router.post("/hosts/{host_id}/stacks/{project}/update", status_code=status.HTTP_202_ACCEPTED)
    async def update_stack(host_id: str, project: str, body: _UpdateIn, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt das Update vor (Gate-Aktion `docker.stack_update`), hohes Risiko bei Datenspeichern."""
        return await _propose(host_id, project, body, actor, UPDATE_ACTION)

    @router.post("/hosts/{host_id}/stacks/{project}/rollback", status_code=status.HTTP_202_ACCEPTED)
    async def roll_back(host_id: str, project: str, body: _UpdateIn, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt den Rückweg auf die Sicherung vor (Gate-Aktion `docker.stack_rollback`, immer hohes Risiko)."""
        return await _propose(host_id, project, body, actor, ROLLBACK_ACTION)

    return router
