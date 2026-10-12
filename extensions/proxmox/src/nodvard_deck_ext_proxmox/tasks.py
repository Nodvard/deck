"""Aufgabenverlauf: was auf den Knoten passiert ist, wer es ausgelöst hat und ob es geklappt hat, über alle Verbindungen
zusammengeführt (Proxmox' "Tasks"-Panel). Rein lesend; die Protokollzeilen eines Tasks holt `task_log`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .config import build_connectors
from .connector import ProxmoxApiError

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

TYPE_LABEL = {
    "qmstart": "VM gestartet",
    "qmstop": "VM hart gestoppt",
    "qmshutdown": "VM heruntergefahren",
    "qmreboot": "VM neu gestartet",
    "qmreset": "VM zurückgesetzt",
    "qmsuspend": "VM angehalten",
    "qmresume": "VM fortgesetzt",
    "qmsnapshot": "Snapshot erstellt",
    "qmrollback": "Snapshot zurückgerollt",
    "qmdelsnapshot": "Snapshot gelöscht",
    "qmclone": "VM geklont",
    "qmcreate": "VM angelegt",
    "qmdestroy": "VM gelöscht",
    "qmigrate": "VM migriert",
    "qmmove": "Disk verschoben",
    "vzstart": "Container gestartet",
    "vzstop": "Container hart gestoppt",
    "vzshutdown": "Container heruntergefahren",
    "vzreboot": "Container neu gestartet",
    "vzsnapshot": "Snapshot erstellt",
    "vzrollback": "Snapshot zurückgerollt",
    "vzdelsnapshot": "Snapshot gelöscht",
    "vzcreate": "Container angelegt",
    "vzdestroy": "Container gelöscht",
    "vzdump": "Backup",
    "startall": "Alle Gäste gestartet (Knotenstart)",
    "stopall": "Alle Gäste gestoppt (Knoten fährt herunter)",
    "aptupdate": "Paketlisten aktualisiert",
    "vncproxy": "Konsole geöffnet",
    "vncshell": "Knoten-Shell geöffnet",
    "termproxy": "Terminal geöffnet",
    "spiceproxy": "SPICE-Konsole geöffnet",
    "srvreload": "Dienst neu geladen",
    "srvrestart": "Dienst neu gestartet",
    "imgcopy": "Image kopiert",
    "download": "Download",
}

# Konsolen-Öffnungen sind zahlreich (jede Konsole in Nodvard Deck erzeugt eine) und für den Blick auf "was ist passiert"
# meist Rauschen; die Seite blendet sie standardmäßig aus.
CONSOLE_TYPES = frozenset({"vncproxy", "vncshell", "termproxy", "spiceproxy"})


def _guest_names(hosts: list[Any]) -> dict[tuple[str, str], str]:
    names: dict[tuple[str, str], str] = {}
    for host in hosts:
        meta = host.metadata or {}
        if meta.get("vmid") is not None:
            names[(str(meta.get("connection")), str(meta.get("vmid")))] = host.display_name or host.name
    return names


async def collect_tasks(ctx: "ExtensionContext", *, limit: int, include_console: bool) -> dict[str, Any]:
    names = _guest_names(await ctx.hosts.list(tag="proxmox"))
    tasks: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for conn_name, connector in (await build_connectors(ctx)).items():
        try:
            nodes = await connector.list_nodes()
        except ProxmoxApiError as exc:
            errors.append({"connection": conn_name, "error": str(exc)})
            continue
        for node in nodes:
            node_name = node.get("node")
            if not node_name or node.get("status") not in (None, "online"):
                continue
            try:
                # Konsolen herausfiltern kostet Einträge, darum mehr holen, als am Ende gezeigt wird.
                rows = await connector.node_tasks(node_name, limit=limit if include_console else limit * 3)
            except ProxmoxApiError as exc:
                errors.append({"connection": conn_name, "node": node_name, "error": str(exc)})
                continue
            for row in rows:
                kind = str(row.get("type") or "")
                if not include_console and kind in CONSOLE_TYPES:
                    continue
                guest_id = str(row.get("id") or "")
                start, end = row.get("starttime"), row.get("endtime")
                status = row.get("status")
                tasks.append({
                    "connection": conn_name,
                    "node": node_name,
                    "upid": row.get("upid"),
                    "type": kind,
                    "type_label": TYPE_LABEL.get(kind, kind),
                    "guest_id": guest_id or None,
                    "guest_name": names.get((conn_name, guest_id)) if guest_id else None,
                    "user": row.get("user"),
                    "status": status,
                    "running": end is None,
                    "ok": status == "OK" if end is not None else None,
                    # Zeitstempel von Proxmox sind UTC, die Seite zeigt Ortszeit.
                    "starttime": start,
                    "endtime": end,
                    "duration_s": (end - start) if isinstance(start, int) and isinstance(end, int) else None,
                })
    tasks.sort(key=lambda t: t["starttime"] or 0, reverse=True)
    return {"tasks": tasks[:limit], "errors": errors}
