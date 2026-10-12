"""Backup-Provider und Aktions-Ausführung gegen die Proxmox-API: Jobs, Läufe und Retry."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from nodvard_sdk import ActionResult, Actor, DryRunReport, Risk
from nodvard_sdk.actions import ActionRequest
from nodvard_sdk.errors import NodvardError

from .connector import ProxmoxBackupApiError, ProxmoxBackupConnector
from .failure_text import explain, relevant_lines
from .job_edit import (
    JobEditError,
    build_job_create,
    build_job_update,
    job_snapshot,
    retention_note,
    same_run_options,
    vzdump_options,
)
from .job_edit import describe as describe_job_diff
from .inventory import list_backup_files, retention_label, summarize

ConnectorFactory = Callable[[str], Awaitable[ProxmoxBackupConnector]]
ConnectorsFactory = Callable[[], Awaitable[dict[str, ProxmoxBackupConnector]]]


def _job_ref(connection: str, job_id: str, vmid: str) -> str:
    # Der Verbindungsname gehört dazu, weil dieselbe VMID auf mehreren Proxmox-Instanzen vorkommen kann.
    return f"{connection}--{job_id}--{vmid}"


def _parse_job_ref(job_ref: str) -> tuple[str, str, str]:
    connection, job_id, vmid = job_ref.split("--", 2)
    return connection, job_id, vmid


def _vzdump_tasks(tasks: list[dict[str, Any]], vmid: str, *, group_job: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Läufe für einen Gast, neueste zuerst. Ein Job für mehrere Gäste läuft in Proxmox als eine Aufgabe ohne
    Gast-ID; mit `group_job` zählt sie für jeden Gast des Jobs (nur auf dessen Knoten, falls einer festgelegt ist)."""

    def belongs(t: dict[str, Any]) -> bool:
        if t.get("type") != "vzdump":
            return False
        if str(t.get("id") or "") == str(vmid):
            return True
        if group_job is not None and not t.get("id"):
            node = group_job.get("node")
            return not node or t.get("node") == node
        return False

    matching = [t for t in tasks if belongs(t)]
    matching.sort(key=lambda t: t.get("starttime", 0), reverse=True)
    return matching


def job_vmids(job: dict[str, Any], resources: list[dict[str, Any]]) -> list[str]:
    """Welche Gäste ein Job sichert. Neben expliziten Listen (`vmid: "102"`) kennt Proxmox „alle Gäste“ (`all: 1`,
    optional `exclude` und `node`) und Pools (`pool`); dort fehlt `vmid` ganz."""
    explicit = [v.strip() for v in str(job.get("vmid") or "").split(",") if v.strip()]
    if explicit:
        return explicit
    ordered = sorted(resources, key=lambda r: int(r.get("vmid") or 0))
    if job.get("pool"):
        return [str(r["vmid"]) for r in ordered if r.get("pool") == job["pool"]]
    if str(job.get("all", "0")) in ("1", "true"):
        exclude = {v.strip() for v in str(job.get("exclude") or "").split(",") if v.strip()}
        return [
            str(r["vmid"])
            for r in ordered
            if str(r["vmid"]) not in exclude and (not job.get("node") or r.get("node") == job["node"])
        ]
    return []


def unreachable_row(connection: str, error: str) -> dict[str, Any]:
    """Platzhalterzeile für eine Verbindung, die nicht antwortet, mit den Feldern eines Jobs.

    Die Kern-Übersicht zählt sie nicht als Job; ohne `job_ref` blendet die Kachel „Erneut versuchen“ aus."""
    return {
        "job_ref": "", "vmid": "", "name": f"Verbindung {connection}", "host_id": None,
        "connection": connection, "node": None, "storage": error, "schedule": None,
        "enabled": False, "last_status": "unreachable", "last_run_at": None, "last_ok_at": None,
        "next_run_at": None, "retention": None, "error": error,
    }


WARNINGS_RE = re.compile(r"WARNINGS: (\d+)")
"""So endet ein Lauf, der geklappt hat, aber Warnungen hatte (z. B. Gast-Agent lief nicht). Die Sicherung ist da,
Proxmox zeigt ihn gelb, nicht rot."""
CAUSE_CACHE_SIZE = 64
"""So viele Protokolle fehlgeschlagener Läufe bleiben im Speicher (nur die Zeilen für `explain`); ein beendeter Lauf
ändert sein Protokoll nicht mehr."""


def _task_status(task: dict[str, Any] | None) -> str:
    if task is None:
        return "unknown"
    if "endtime" not in task:
        return "running"
    status = str(task.get("status") or "")
    return "ok" if status == "OK" or WARNINGS_RE.fullmatch(status) else "failed"


def _task_warnings(task: dict[str, Any]) -> int | None:
    match = WARNINGS_RE.fullmatch(str(task.get("status") or ""))
    return int(match.group(1)) if match else None


class ProxmoxBackupProvider:
    def __init__(self, ctx: Any, connectors_factory: ConnectorsFactory) -> None:
        self._ctx = ctx
        self._connectors_factory = connectors_factory
        self._logs: dict[tuple[str, str], list[str]] = {}

    async def _vm_name_and_host(self, connection: str, vmid: str) -> tuple[str, str | None]:
        """Klarname und Host-ID aus `Host.provider_ref` (`<connection>/qemu|lxc/<node>/<vmid>`), sonst "VM <vmid>"."""
        # Die Proxmox-Erweiterung schreibt den Verweis; ohne sie oder bei abweichendem Verbindungsnamen bleibt die VMID.
        # Der Treffer muss zur Verbindung passen (gleiche VMID auf mehreren Instanzen) und gilt für LXC wie QEMU.
        prefix_qemu = f"{connection}/qemu/"
        prefix_lxc = f"{connection}/lxc/"
        for host in await self._ctx.hosts.list():
            ref = host.provider_ref or ""
            if (ref.startswith(prefix_qemu) or ref.startswith(prefix_lxc)) and ref.rsplit("/", 1)[-1] == str(vmid):
                return host.display_name or host.name, host.id
        return f"VM {vmid}", None

    async def list_jobs(self) -> list[dict[str, Any]]:
        """Je Gast und Job eine Zeile; eine nicht erreichbare Verbindung steht als eigene Zeile
        `last_status="unreachable"` (ohne `job_ref`), damit sie weder Jobs versteckt noch den Wächter lahmlegt."""
        rows: list[dict[str, Any]] = []
        for connection, connector in (await self._connectors_factory()).items():
            try:
                jobs = await connector.list_jobs()
                tasks = await connector.list_tasks()
                needs_resources = any(not str(job.get("vmid") or "").strip() for job in jobs)
                resources = await connector.guest_resources() if needs_resources else []
            except ProxmoxBackupApiError as exc:
                rows.append(unreachable_row(connection, str(exc)))
                continue

            for job in jobs:
                vmids = job_vmids(job, resources)
                group = job if len(vmids) > 1 else None
                for vmid in vmids:
                    name, host_id = await self._vm_name_and_host(connection, vmid)
                    latest = _vzdump_tasks(tasks, vmid, group_job=group)
                    latest_task = latest[0] if latest else None
                    # Letzter erfolgreicher Lauf (der letzte kann fehlgeschlagen sein), Grundlage für „Backup zu alt“
                    # im Wächter; als Zeitpunkt zählt das Ende des Laufs, sonst sein Beginn.
                    latest_ok = next((t for t in latest if _task_status(t) == "ok"), None)
                    rows.append(
                        {
                            "job_ref": _job_ref(connection, str(job.get("id")), vmid),
                            "vmid": vmid,
                            "name": name,
                            "host_id": host_id,
                            "connection": connection,
                            "node": job.get("node") or (latest_task.get("node") if latest_task else None),
                            "storage": job.get("storage"),
                            "schedule": job.get("schedule"),
                            "enabled": bool(int(job.get("enabled", 1))),
                            "last_status": _task_status(latest_task),
                            "last_run_at": latest_task.get("starttime") if latest_task else None,
                            "last_ok_at": (latest_ok.get("endtime") or latest_ok.get("starttime")) if latest_ok else None,
                            # Beides steht schon in der Job-Definition, kein Extra-Aufruf.
                            "next_run_at": job.get("next-run"),
                            "retention": retention_label(job.get("prune-backups")),
                        }
                    )
        return rows

    async def unprotected(self) -> dict[str, Any]:
        """Gäste ohne Backup-Job, je Verbindung; eine nicht erreichbare Verbindung wird gemeldet, nicht verschwiegen."""
        # Ohne diese Liste zeigt die Seite nur, was gesichert wird, nie, was fehlt.
        guests: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        acknowledged = {
            (str(a.get("connection")), str(a.get("vmid"))): str(a.get("note") or "")
            for a in (await self._ctx.settings.get()).get("acknowledged_unprotected") or []
        }
        for connection, connector in (await self._connectors_factory()).items():
            try:
                rows = await connector.not_backed_up()
            except ProxmoxBackupApiError as exc:
                errors.append({"connection": connection, "error": str(exc)})
                continue
            for row in rows:
                vmid = str(row.get("vmid"))
                name, host_id = await self._vm_name_and_host(connection, vmid)
                guests.append({
                    "connection": connection,
                    "vmid": vmid,
                    # Anzeigename in Nodvard Deck, wenn der Gast entdeckt ist, sonst Proxmox' Name.
                    "name": name if host_id is not None else (row.get("name") or name),
                    "host_id": host_id,
                    "kind": "Container" if row.get("type") == "lxc" else "VM",
                    # "Bewusst ohne Backup" (vom Betreiber so markiert): bleibt sichtbar,
                    # warnt aber nicht mehr im Widget.
                    "acknowledged": (connection, vmid) in acknowledged,
                    "note": acknowledged.get((connection, vmid)),
                })
        guests.sort(key=lambda g: (g["connection"], int(g["vmid"]) if g["vmid"].isdigit() else 0))
        return {"guests": guests, "errors": errors}

    async def inventory(self) -> dict[str, Any]:
        """Vorhandene Sicherungen je Gast (inventory.py) über alle Verbindungen.

        Zuordnung über VMID und Typ gegen die entdeckten Gäste; verwaiste Dateien stehen extra."""
        known: dict[tuple[str, str], list[str]] = {}
        for host in await self._ctx.hosts.list():
            parts = (host.provider_ref or "").split("/")
            if len(parts) == 4 and parts[1] in ("qemu", "lxc"):
                known.setdefault((parts[1], parts[3]), []).append(parts[0])
        files_by_connection: dict[str, list[dict[str, Any]]] = {}
        errors: list[dict[str, str]] = []
        for connection, connector in (await self._connectors_factory()).items():
            try:
                files, problems = await list_backup_files(connector)
            except ProxmoxBackupApiError as exc:
                errors.append({"connection": connection, "error": str(exc)})
                continue
            errors.extend({"connection": connection, "error": p} for p in problems)
            files_by_connection[connection] = files
        guests, orphans = summarize(files_by_connection, known)
        return {"guests": guests, "orphans": orphans, "errors": errors}

    async def running_backups(self) -> dict[str, Any]:
        """Gäste, für die gerade eine Sicherung läuft (VZDump-Task ohne Ende), je Verbindung.

        Für Gäste ohne Job; bei Gästen mit Job steht der Lauf in `list_jobs()`, Sammelläufe ohne Gast-ID fehlen hier."""
        running: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for connection, connector in (await self._connectors_factory()).items():
            try:
                tasks = await connector.list_tasks()
            except ProxmoxBackupApiError as exc:
                errors.append({"connection": connection, "error": str(exc)})
                continue
            running.extend(
                {"connection": connection, "vmid": str(task["id"]), "started_at": task.get("starttime")}
                for task in tasks
                if task.get("type") == "vzdump" and task.get("id") and _task_status(task) == "running"
            )
        return {"running": running, "errors": errors}

    async def _connector_for(self, connection: str) -> ProxmoxBackupConnector:
        connector = (await self._connectors_factory()).get(connection)
        if connector is None:
            raise NodvardError(f"Backup-Verbindung '{connection}' ist nicht (mehr) konfiguriert.")
        return connector

    async def _job_tasks(self, connector: ProxmoxBackupConnector, job_id: str, vmid: str) -> list[dict[str, Any]]:
        """Die Läufe eines Jobs für diesen Gast, neueste zuerst."""
        tasks = await connector.list_tasks()
        job = next((j for j in await connector.list_jobs() if str(j.get("id")) == job_id), None)
        # „Alle Gäste“- und Pool-Jobs haben keine VMID-Liste; ohne Ressourcen bliebe der Verlauf leer (wie in list_jobs).
        needs_resources = job is not None and not str(job.get("vmid") or "").strip()
        resources = await connector.guest_resources() if needs_resources else []
        group = job if job is not None and len(job_vmids(job, resources)) > 1 else None
        return _vzdump_tasks(tasks, vmid, group_job=group)

    async def job_history(self, job_ref: str) -> list[dict[str, Any]]:
        connection, job_id, vmid = _parse_job_ref(job_ref)
        connector = await self._connector_for(connection)
        return [
            {
                "upid": t.get("upid"),
                "node": t.get("node"),
                "status": _task_status(t),
                "started_at": t.get("starttime"),
                "finished_at": t.get("endtime"),
                # Geklappt, aber mit Warnungen (Anzahl), sonst nicht gesetzt.
                **({"warnings": warnings} if (warnings := _task_warnings(t)) is not None else {}),
            }
            for t in await self._job_tasks(connector, job_id, vmid)
        ]

    async def failure_cause(self, job_ref: str, upid: str | None = None) -> dict[str, Any] | None:
        """Grund für den neuesten fehlgeschlagenen Lauf des Jobs für den Gast (oder den Lauf `upid`), in Klartext.

        `None` ohne solchen Lauf; ist das Protokoll nicht lesbar (Recht `Sys.Audit` fehlt), zählt nur der Status."""
        connection, job_id, vmid = _parse_job_ref(job_ref)
        connector = await self._connector_for(connection)
        task = next(
            (t for t in await self._job_tasks(connector, job_id, vmid)
             if _task_status(t) == "failed" and (upid is None or t.get("upid") == upid)),
            None,
        )
        if task is None:
            return None
        task_upid, node = str(task.get("upid") or ""), str(task.get("node") or "")
        lines = self._logs.get((connection, task_upid))
        if lines is None and task_upid and node:
            try:
                lines = relevant_lines(await connector.task_log(node, task_upid))
            except ProxmoxBackupApiError:
                lines = None
            else:
                if len(self._logs) >= CAUSE_CACHE_SIZE:
                    self._logs.pop(next(iter(self._logs)))
                self._logs[(connection, task_upid)] = lines
        return {
            "upid": task_upid, "node": node, "started_at": task.get("starttime"), "finished_at": task.get("endtime"),
            "status": task.get("status"), "log_readable": lines is not None,
            **explain(vmid, task.get("status"), lines),
        }

    async def retry(self, job_ref: str) -> ActionRequest:
        """Gibt einen Vorschlag zurück; der Aufrufer reicht ihn an `ctx.actions.propose()` weiter (durchs Gate)."""
        connection, job_id, vmid = _parse_job_ref(job_ref)
        connectors = await self._connectors_factory()
        connector = connectors.get(connection)
        if connector is None:
            raise NodvardError(f"Backup-Verbindung '{connection}' ist nicht (mehr) konfiguriert.")
        jobs = await connector.list_jobs()
        job = next((j for j in jobs if str(j.get("id")) == job_id), None)
        if job is None:
            raise NodvardError(f"Backup-Job '{job_id}' existiert nicht (mehr).")

        node = job.get("node")
        if not node:
            # Job mit Knoten „-- Alle --“ (PVE-Standard): verlässlich ist der Knoten, auf dem der Gast liegt,
            # sonst der des letzten Laufs. Bei Sammel-Jobs hat der keine Gast-ID und zählt nur mit `group_job`.
            resources = await connector.guest_resources()
            node = next((r.get("node") for r in resources if str(r.get("vmid")) == vmid and r.get("node")), None)
            if not node:
                group = job if len(job_vmids(job, resources)) > 1 else None
                latest = _vzdump_tasks(await connector.list_tasks(), vmid, group_job=group)
                node = latest[0].get("node") if latest else None
        if not node:
            raise NodvardError(f"Kein Proxmox-Knoten für VMID {vmid} bekannt -- Retry nicht möglich.")

        # Festgehalten wird der Stand, den der Freigebende sieht. Gelesen wird wie beim Ausführen über
        # `get_job` (nicht aus der Liste), damit beide Stände dieselbe Form haben.
        current = await connector.get_job(job_id)
        if not current:
            raise NodvardError(f"Backup-Job '{job_id}' existiert nicht (mehr).")
        options = vzdump_options(current)

        name, host_id = await self._vm_name_and_host(connection, vmid)
        return ActionRequest(
            action_type="backup.run",
            host_ref=host_id,
            payload={
                "connection": connection, "node": node, "vmid": vmid, "storage": job.get("storage"),
                "job_id": job_id, "options": options,
            },
            risk=Risk.MEDIUM,
            proposed_by=Actor.extension("backups"),
            reason=f"Manueller Backup-Retry für VMID {vmid} ({name}) mit den Einstellungen des Jobs. {retention_note(options)}",
            correlation_id=job_ref,
        )


VZDUMP_PERMISSION_HINT = (
    "Proxmox hat den Backup-Lauf abgelehnt (keine Berechtigung), es wurde nichts gestartet. "
    "Der Token braucht dafür VM.Backup und auf dem Backup-Speicher Datastore.Allocate "
    "(dieselbe Zusatzrolle wie zum Anlegen von Jobs); hat der Job eine Bandbreitengrenze oder "
    "ionice, auch Sys.Modify auf /."
)


class BackupActionExecutor:
    """Führt `backup.run`, `backup.job_update` und `backup.job_create` aus.

    Bei `backup.run` heißt `success=True` nur „Auftrag angenommen“ (UPID); den Ausgang zeigt später `list_jobs()`."""

    action_types = frozenset({"backup.run", "backup.job_update", "backup.job_create"})

    def __init__(self, connectors_factory: ConnectorsFactory) -> None:
        self._connectors_factory = connectors_factory

    async def execute(self, req: ActionRequest) -> ActionResult:
        if req.action_type == "backup.job_update":
            return await self._job_update(req)
        if req.action_type == "backup.job_create":
            return await self._job_create(req)
        connection = req.payload.get("connection")
        node = req.payload.get("node")
        vmid = req.payload.get("vmid")
        if not connection or not node or not vmid:
            return ActionResult(success=False, error="payload.connection/node/vmid fehlt.")
        try:
            connectors = await self._connectors_factory()
            connector = connectors.get(connection)
            if connector is None:
                return ActionResult(success=False, error=f"Backup-Verbindung '{connection}' ist nicht (mehr) konfiguriert.")
            # Gestartet wird nur mit den freigegebenen Einstellungen. Hat sich der Job seitdem geändert (oder
            # ist er nicht lesbar), geschieht nichts: der Freigebende hat sonst einen anderen Stand gesehen,
            # vor allem bei dem, was aufgeräumt wird. Ein Vorschlag ohne Job läuft mit `keep-all=1`.
            options: dict[str, Any] | None = None
            job_id = str(req.payload.get("job_id") or "")
            if job_id:
                try:
                    current = await connector.get_job(job_id)
                except ProxmoxBackupApiError:
                    current = {}
                if not current:
                    return ActionResult(
                        success=False,
                        error=f"Der Backup-Job '{job_id}' ist nicht lesbar (gelöscht oder Proxmox antwortet nicht). Es wurde nichts gestartet – bitte neu vorschlagen.",
                    )
                if not same_run_options(req.payload.get("options"), vzdump_options(current)):
                    return ActionResult(
                        success=False,
                        error="Der Backup-Job wurde seit dem Vorschlag geändert. Es wurde nichts gestartet – bitte neu vorschlagen.",
                    )
                options = req.payload["options"]
            try:
                upid = await connector.run_vzdump(node, vmid, storage=req.payload.get("storage"), options=options)
            except ProxmoxBackupApiError as exc:
                if exc.status_code != 403:
                    raise
                # Die Aufbewahrung geht immer mit (auch `keep-all=1`); dafür verlangt Proxmox `Datastore.Allocate`
                # auf dem Speicher, ohne Hinweis sähe man nur „HTTP 403“.
                return ActionResult(success=False, error=f"{VZDUMP_PERMISSION_HINT} ({exc})")
        except ProxmoxBackupApiError as exc:
            return ActionResult(success=False, error=str(exc))
        return ActionResult(success=True, output=f"Backup gestartet: {upid}")

    async def _job_create(self, req: ActionRequest) -> ActionResult:
        connection = str(req.payload.get("connection") or "")
        try:
            connector = (await self._connectors_factory()).get(connection)
            if connector is None:
                return ActionResult(success=False, error=f"Backup-Verbindung '{connection}' ist nicht (mehr) konfiguriert.")
            params, text = build_job_create(req.payload.get("vmid"), req.payload.get("values"))
            await connector.create_job(params)
        except JobEditError as exc:
            return ActionResult(success=False, error=str(exc))
        except ProxmoxBackupApiError as exc:
            return ActionResult(success=False, error=str(exc))
        return ActionResult(success=True, output=text)

    async def _job_update(self, req: ActionRequest) -> ActionResult:
        """Liest den Job frisch (Digest), prüft erneut gegen die Erlaubnisliste und schreibt."""
        connection = str(req.payload.get("connection") or "")
        job_id = str(req.payload.get("job_id") or "")
        try:
            connector = (await self._connectors_factory()).get(connection)
            if connector is None:
                return ActionResult(success=False, error=f"Backup-Verbindung '{connection}' ist nicht (mehr) konfiguriert.")
            job = await connector.get_job(job_id)
            expected = req.payload.get("expected")
            if isinstance(expected, dict) and job_snapshot(job) != expected:
                return ActionResult(
                    success=False,
                    error="Der Backup-Job wurde seit dem Vorschlag geändert. Es wurde nichts geschrieben – bitte die Änderung neu vorschlagen.",
                )
            params, diff, destructive = build_job_update(job, req.payload.get("changes"))
            if destructive and req.risk not in (Risk.HIGH, Risk.CRITICAL):
                # Nur mit hoher Freigabe: die Änderung würde Sicherungen kosten.
                return ActionResult(
                    success=False,
                    error="Diese Änderung würde Sicherungen löschen, wurde aber nicht mit hohem Risiko freigegeben. Es wurde nichts geschrieben – bitte neu vorschlagen.",
                )
            await connector.update_job(job_id, params)
        except JobEditError as exc:
            return ActionResult(success=False, error=str(exc))
        except ProxmoxBackupApiError as exc:
            return ActionResult(success=False, error=str(exc))
        return ActionResult(success=True, output=describe_job_diff(diff), detail={"changes": diff})

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        return DryRunReport(
            would_change=True,
            summary=f"Würde VZDump für VMID {req.payload.get('vmid')} auf Knoten {req.payload.get('node')} starten.",
        )
