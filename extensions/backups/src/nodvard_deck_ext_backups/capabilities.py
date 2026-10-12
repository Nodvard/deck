"""Erfuellt `nodvard_sdk.capabilities.BackupProvider`/`ActionExecutor` gegen die
Proxmox-Backup-API.

**Warum:** ein VZDump-Job, der strukturell fehlschlaegt (z. B. "Broken pipe" zu einem
langsamen NFS-Ziel), kann wochenlang unbemerkt bleiben, wenn das Ergebnis nur in einem
Freitext-Bericht steht, den niemand systematisch liest (so war es im Vorgaengersystem).
`list_jobs()` zeigt den Status PRO VM als strukturierten Datensatz (nicht als Prosa), `retry()`
macht einen fehlgeschlagenen Lauf mit einem Klick wiederholbar -- durchs Gate,
wie jede andere Aktion.

**Multi-Instanz-Nachtrag:** `job_ref` traegt jetzt den Verbindungsnamen mit
(`f"{connection}--{job_id}--{vmid}"`) -- ohne ihn waere ein Retry nach dem Wechsel
von einer auf mehrere Verbindungen nicht mehr eindeutig einer Proxmox-Instanz
zuordenbar (dieselbe VMID kann auf pve1 UND pve2 vorkommen, siehe
`nodvard_deck_ext_proxmox.capabilities`-Docstring fuer denselben Fall dort).
"""

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
    return f"{connection}--{job_id}--{vmid}"


def _parse_job_ref(job_ref: str) -> tuple[str, str, str]:
    connection, job_id, vmid = job_ref.split("--", 2)
    return connection, job_id, vmid


def _vzdump_tasks(tasks: list[dict[str, Any]], vmid: str, *, group_job: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Laeufe fuer einen Gast. Beobachtet: ein Container stand auf "nie gelaufen",
    obwohl nachts gesichert wurde -- ein Job fuer MEHRERE Gaeste laeuft in Proxmox als
    EINE Aufgabe ohne Gast-ID. Mit `group_job` zaehlt so eine Aufgabe fuer jeden Gast
    des Jobs (nur auf dessen Knoten, falls der Job einen festlegt)."""

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
    """Welche Gaeste ein Job sichert. Live nur explizite Listen (`vmid: "102"`), aber
    Proxmox kennt auch "alle Gaeste" (`all: 1`, optional `exclude`, optional auf
    `node` beschraenkt) und Pools (`pool`) -- dort fehlt `vmid` ganz. Vorher ergab so
    ein Job stillschweigend KEINE Zeile: ein Backup-Job, der alles sichert, war auf
    der Seite unsichtbar."""
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
    """Platzhalterzeile fuer eine Verbindung, die nicht antwortet -- dieselben Felder wie
    ein Job, damit jeder Leser von `list_jobs()` sie ohne Sonderweg verarbeiten kann
    (die Kern-Uebersicht zaehlt sie nicht als Job, sondern meldet "Verbindung nicht
    erreichbar"). Ohne `job_ref` blendet die Kachel "Erneut versuchen" aus."""
    return {
        "job_ref": "", "vmid": "", "name": f"Verbindung {connection}", "host_id": None,
        "connection": connection, "node": None, "storage": error, "schedule": None,
        "enabled": False, "last_status": "unreachable", "last_run_at": None, "last_ok_at": None,
        "next_run_at": None, "retention": None, "error": error,
    }


WARNINGS_RE = re.compile(r"WARNINGS: (\d+)")
"""So endet ein Lauf, der geklappt hat, aber Warnungen hatte (z. B. ein Gast-Agent, der nicht lief). Die Sicherung ist
da -- Proxmox zeigt ihn gelb, nicht rot. Vorher galt er hier als fehlgeschlagen und loeste "Backup fehlgeschlagen" aus."""
CAUSE_CACHE_SIZE = 64
"""So viele Protokolle fehlgeschlagener Laeufe bleiben im Speicher (nur die Zeilen, die `explain` braucht). Ein beendeter
Lauf aendert sein Protokoll nicht mehr; der Waechter und die Seite fragen dann nicht jedes Mal erneut."""


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
        """`ctx.hosts` kennt Proxmox nicht -- nur `Host.provider_ref` (`"<connection>/
        qemu|lxc/<node>/<vmid>"`, von der proxmox-Extension geschrieben, siehe WP-8
        und deren Multi-Instanz-Nachtrag). Ein Treffer ist reine Bonus-Anreicherung
        (Klarname statt nackter VMID); ohne proxmox-Extension, ohne vorherige
        Discovery, oder wenn `connection` dort anders heisst als hier, bleibt die
        VMID der Anzeigename. Filtert bewusst auch auf `connection`, weil dieselbe
        VMID auf mehreren Proxmox-Instanzen vorkommen kann (auf pve1 eine VM, auf pve2
        ein Container) -- und auf LXC genauso wie QEMU (Live gefunden: die alte Version
        pruefte nur "qemu/", Backups von LXC-Containern -- z. B. allem auf pve2 --
        zeigten deshalb nie einen Klarnamen)."""
        prefix_qemu = f"{connection}/qemu/"
        prefix_lxc = f"{connection}/lxc/"
        for host in await self._ctx.hosts.list():
            ref = host.provider_ref or ""
            if (ref.startswith(prefix_qemu) or ref.startswith(prefix_lxc)) and ref.rsplit("/", 1)[-1] == str(vmid):
                return host.display_name or host.name, host.id
        return f"VM {vmid}", None

    async def list_jobs(self) -> list[dict[str, Any]]:
        """Je Gast und Job eine Zeile. Eine nicht erreichbare Verbindung (pve2
        aus) versteckte vorher ALLE Jobs und legte den Waechter lahm -- jetzt steht sie
        als eigene Zeile `last_status="unreachable"` (ohne `job_ref`) in der Liste."""
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
                    # Letzter ERFOLGREICHER Lauf (der letzte kann fehlgeschlagen sein): Grundlage fuer die
                    # Meldung "Backup zu alt" im Waechter. Ende des Laufs, sonst sein Beginn.
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
                            # Beides steht schon in der Job-Definition -- kein Extra-Aufruf.
                            "next_run_at": job.get("next-run"),
                            "retention": retention_label(job.get("prune-backups")),
                        }
                    )
        return rows

    async def unprotected(self) -> dict[str, Any]:
        """Gaeste ohne jeden Backup-Job, je Verbindung. Ohne diese Liste zeigt die
        Backup-Seite nur, was gesichert WIRD, nie, was fehlt -- und das ist oft der
        groessere Teil der Gaeste. Eine nicht
        erreichbare Verbindung wird gemeldet, nicht verschwiegen."""
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
        """Vorhandene Sicherungen je Gast (inventory.py), ueber alle Verbindungen --
        Zuordnung ueber VMID + Typ gegen die entdeckten Gaeste, verwaiste Dateien extra."""
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
        """Gaeste, fuer die gerade eine Sicherung laeuft (ein VZDump-Task dieses Gastes ohne Ende), je Verbindung:
        `{"running": [{"connection", "vmid", "started_at"}], "errors": [{"connection", "error"}]}`. Fuer Gaeste ohne
        Job -- bei Gaesten mit Job steht der Lauf schon in den Zeilen von `list_jobs()`. Sammel-Laeufe ohne Gast-ID
        sind hier nicht dabei (sie gehoeren zu einem Job)."""
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
        """Die Laeufe eines Jobs fuer diesen Gast, neueste zuerst."""
        tasks = await connector.list_tasks()
        job = next((j for j in await connector.list_jobs() if str(j.get("id")) == job_id), None)
        # "Alle Gaeste"/Pool-Jobs haben keine VMID-Liste -- ohne Ressourcen
        # ergab job_vmids() [], und der Verlauf blieb leer (wie needs_resources in list_jobs).
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
        """Der Grund fuer den neuesten fehlgeschlagenen Lauf dieses Jobs fuer den Gast (oder fuer den Lauf `upid`), in
        Klartext (failure_text.py): `{"upid", "node", "started_at", "finished_at", "status", "log_readable", "key",
        "what", "todo", "detail"}`. `None`, wenn es keinen solchen fehlgeschlagenen Lauf gibt. Liest dazu das
        Aufgabenprotokoll; laesst es sich nicht lesen (fehlendes Recht `Sys.Audit`), zaehlt nur der Status."""
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
        """Gibt einen *Vorschlag* zurueck (docs/02 §3-Vertrag) -- der Aufrufer
        (`__init__.py`s `/jobs/{ref}/retry`-Route) reicht ihn an `ctx.actions.
        propose()` weiter, geht also durchs Gate wie jede andere Aktion."""
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
            # Job mit Knoten "-- Alle --" (PVE-Standard). Verlaesslich ist der
            # Knoten, auf dem der Gast liegt; sonst der des letzten Laufs -- bei Sammel-Jobs
            # hat der keine Gast-ID, zaehlt also nur mit `group_job` (wie list_jobs).
            resources = await connector.guest_resources()
            node = next((r.get("node") for r in resources if str(r.get("vmid")) == vmid and r.get("node")), None)
            if not node:
                group = job if len(job_vmids(job, resources)) > 1 else None
                latest = _vzdump_tasks(await connector.list_tasks(), vmid, group_job=group)
                node = latest[0].get("node") if latest else None
        if not node:
            raise NodvardError(f"Kein Proxmox-Knoten für VMID {vmid} bekannt -- Retry nicht möglich.")

        # Der Stand des Jobs, den der Freigebende zu sehen bekommt, wird festgehalten. Gelesen wird
        # wie beim Ausfuehren ueber `get_job` (nicht aus der Liste), damit beide Staende dieselbe
        # Form haben und der Vergleich nicht an Schreibweisen scheitert.
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
    """Erfuellt `nodvard_sdk.capabilities.ActionExecutor` fuer `backup.run`.

    **Ehrlich abgegrenzt** (wie proxmoxs `qemu_action()`): wartet NICHT auf den
    tatsaechlichen Abschluss des VZDump-Laufs -- `success=True` heisst "Proxmox hat
    den Auftrag angenommen (UPID erhalten)", nicht "das Backup ist fertig/fehlerfrei".
    Der naechste `list_jobs()`-Aufruf zeigt das echte Ergebnis, sobald der Task in
    `/cluster/tasks` erscheint."""

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
            # Gestartet wird nur mit den Einstellungen, die beim Vorschlag freigegeben wurden. Hat sich der
            # Job seitdem geaendert (oder ist er nicht lesbar), wird nichts gestartet: der Freigebende hat
            # dann einen anderen Stand gesehen, vor allem was aufgeraeumt wird. Ein Vorschlag ohne Job
            # (aus einer aelteren Version) laeuft mit `keep-all=1`, raeumt also nichts auf.
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
                # Die Aufbewahrung geht immer mit (auch `keep-all=1`), dafuer verlangt Proxmox
                # `Datastore.Allocate` auf dem Speicher -- ohne Hinweis sieht man nur "HTTP 403".
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
        """Liest den Job frisch (digest!), prueft erneut gegen die Whitelist, schreibt."""
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
