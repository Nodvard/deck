"""Aufgabenverlauf: was auf den Knoten passiert ist, wer es
ausgeloest hat, ob es geklappt hat -- Proxmox' eigenes "Tasks"-Panel, ueber alle
Verbindungen zusammengefuehrt.

Beispiel: ein Knoten, der planmaessig neu startet, taucht hier als "stopall"/"startall"
auf (die Proxmox-Zeitstempel sind UTC, die Seite zeigt Ortszeit). Ohne Verlauf sieht
das in Nodvard Deck nur aus wie "VMs laufen"; hier wird es sichtbar.

Rein lesend. Die Protokollzeilen eines Tasks holt ein eigener Aufruf (`task_log`).
"""

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

CONSOLE_TYPES = frozenset({"vncproxy", "vncshell", "termproxy", "spiceproxy"})
"""Konsolen-Oeffnungen -- zahlreich (jede Konsole in Nodvard Deck erzeugt eine), fuer den Blick
auf "was ist passiert" meist Rauschen; die Seite blendet sie standardmaessig aus."""


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
                # Konsolen herausfiltern kostet Eintraege -- deshalb mehr holen, als
                # am Ende gezeigt werden.
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
                    "starttime": start,
                    "endtime": end,
                    "duration_s": (end - start) if isinstance(start, int) and isinstance(end, int) else None,
                })
    tasks.sort(key=lambda t: t["starttime"] or 0, reverse=True)
    return {"tasks": tasks[:limit], "errors": errors}
