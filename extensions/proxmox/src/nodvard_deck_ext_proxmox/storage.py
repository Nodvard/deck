"""Speicher-Uebersicht: Belegung jedes Speicher-Pools je
Verbindung und Knoten, auf Wunsch mit dem Inhalt -- WELCHE Gast-Disks auf welchem Pool
liegen. Liegt eine Gast-Disk z. B. auf `local` (Verzeichnis-Speicher) statt auf
`local-lvm`, faellt das in der Proxmox-Oberflaeche kaum auf, weil man dafuer jeden Pool
einzeln aufklappen muss.

Rein lesend, keine Aktion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .config import build_connectors
from .connector import ProxmoxApiError

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

CONTENT_LABEL = {
    "images": "VM-Disks",
    "rootdir": "Container-Disks",
    "iso": "ISO-Images",
    "vztmpl": "Container-Vorlagen",
    "backup": "Backups",
    "snippets": "Snippets",
    "import": "Import",
}


# Speicherarten, die von Natur aus von aussen kommen. Proxmox setzt `shared` dort nicht
# immer selbst; ohne diese Liste stuenden sie je Knoten einzeln da.
_REMOTE_TYPES = {"nfs", "cifs", "glusterfs", "cephfs", "pbs"}


def is_shared(pool: dict[str, Any]) -> bool:
    """Geteilt: von Proxmox so markiert (`shared: 1`) oder eine Netzwerk-Speicherart."""
    if str(pool.get("shared") or "0").lower() in ("1", "true"):
        return True
    return str(pool.get("type") or "") in _REMOTE_TYPES


def shared_label(nodes: list[str]) -> str:
    """Kurzer Hinweis, wo ein geteilter Speicher verfuegbar ist."""
    if len(nodes) <= 3:
        return f"geteilt, verfügbar auf {', '.join(nodes)}"
    return f"geteilt, verfügbar auf {len(nodes)} Knoten"


def usage_tone(percent: float | None) -> str:
    if percent is None:
        return "neutral"
    if percent >= 90:
        return "danger"
    if percent >= 75:
        return "warn"
    return "good"


def _de_number(value: float) -> str:
    """1234.5 -> "1.234,5", 46.0 -> "46" (hoechstens eine Nachkommastelle)."""
    text = f"{value:,.1f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return text.removesuffix(",0")


def format_bytes(value: Any) -> str:
    """Wie der Widget-Filter `bytes` im Frontend (template.ts): 1024er-Stufen,
    deutsches Komma -- die Kachel bekommt den Text fertig, damit auch eine
    Fehlerzeile ohne Zahlen sauber aussieht."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "?"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    n, unit = abs(num), 0
    while n >= 1024 and unit < len(units) - 1:
        n /= 1024
        unit += 1
    sign = "-" if num < 0 else ""
    return f"{sign}{f'{n:g}' if unit == 0 else _de_number(n)} {units[unit]}"


def percent_badge(percent: float | None) -> str:
    return "unbekannt" if percent is None else f"{_de_number(percent)} %"


def _percent(pool: dict[str, Any]) -> float | None:
    total = pool.get("total") or 0
    if not total:
        return None
    return round(100.0 * float(pool.get("used") or 0) / float(total), 1)


async def _guest_names(ctx: "ExtensionContext") -> dict[tuple[str, str], str]:
    """(Verbindung, VMID) -> Anzeigename, aus den bereits entdeckten Hosts."""
    names: dict[tuple[str, str], str] = {}
    for host in await ctx.hosts.list(tag="proxmox"):
        meta = host.metadata or {}
        if meta.get("vmid") is not None:
            names[(str(meta.get("connection")), str(meta.get("vmid")))] = host.display_name or host.name
    return names


async def collect_storage(
    ctx: "ExtensionContext", *, details: bool, merge_connections: bool = False
) -> dict[str, Any]:
    """`details=False` (Widget, alle 30s): nur Belegung, eine Abfrage je Knoten.
    `details=True` (Seite): zusaetzlich der Inhalt je Pool -- Gast-Disks einzeln,
    Backups/ISOs/Vorlagen als Anzahl + Groesse.

    Ein geteilter Speicher (NFS ...) steht in der Antwort jedes Knotens. Er kommt nur
    einmal vor, mit `nodes` (wo er verfuegbar ist); Belegung und Groesse sind auf allen
    Knoten gleich und werden nicht addiert. Mit `merge_connections` (Widget, ohne
    Verbindungs-Gruppierung) auch ueber Verbindungen hinweg, wenn Name, Art und Groesse
    gleich sind -- zwei eigenstaendige Proxmox-Server, die dieselbe Freigabe einbinden."""
    pools: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    names = await _guest_names(ctx) if details else {}
    shared_rows: dict[tuple[Any, ...], dict[str, Any]] = {}

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
                raw = await connector.node_storage(node_name)
            except ProxmoxApiError as exc:
                errors.append({"connection": conn_name, "node": node_name, "error": str(exc)})
                continue
            for pool in raw:
                storage = str(pool.get("storage"))
                shared = is_shared(pool)
                shared_keys: list[tuple[Any, ...]] = []
                if shared:
                    shared_keys = [("conn", conn_name, storage)]
                    if merge_connections and pool.get("total"):
                        shared_keys.append(("all", storage, pool.get("type"), pool.get("total")))
                    known = next((shared_rows[k] for k in shared_keys if k in shared_rows), None)
                    if known is not None:
                        # Auch die Schluessel dieses Funds merken: der naechste Knoten
                        # derselben Verbindung findet die Zeile sonst nicht, wenn er
                        # (z. B. Speicher gerade inaktiv, Groesse 0) den Verbindungs-
                        # uebergreifenden Schluessel nicht bildet.
                        for k in shared_keys:
                            shared_rows.setdefault(k, known)
                        if node_name not in known["nodes"]:
                            known["nodes"].append(node_name)
                        if conn_name not in known["connections"]:
                            known["connections"].append(conn_name)
                        continue
                content = [c for c in str(pool.get("content") or "").split(",") if c]
                percent = _percent(pool)
                row: dict[str, Any] = {
                    "id": f"{conn_name}/{node_name}/{storage}",
                    "connection": conn_name,
                    "node": node_name,
                    "storage": storage,
                    "type": pool.get("type"),
                    "content": content,
                    "content_labels": [CONTENT_LABEL.get(c, c) for c in content],
                    "nodes": [node_name],
                    "connections": [conn_name],
                    "shared": shared,
                    "active": bool(pool.get("active", 1)),
                    "total": pool.get("total"),
                    "used": pool.get("used"),
                    "avail": pool.get("avail"),
                    "used_percent": percent,
                    "tone": usage_tone(percent) if pool.get("active", 1) else "danger",
                }
                if details:
                    row.update(await _pool_content(connector, conn_name, node_name, storage, names))
                for k in shared_keys:
                    shared_rows[k] = row
                pools.append(row)

    pools.sort(key=lambda p: (p["connection"], p["node"], -(p["used_percent"] or 0)))
    return {"pools": pools, "errors": errors}


async def _pool_content(
    connector: Any, conn_name: str, node: str, storage: str, names: dict[tuple[str, str], str]
) -> dict[str, Any]:
    try:
        items = await connector.storage_content(node, storage)
    except ProxmoxApiError as exc:
        return {"volumes": [], "other": {}, "content_error": str(exc)}
    volumes: list[dict[str, Any]] = []
    other: dict[str, dict[str, Any]] = {}
    for item in items:
        kind = item.get("content")
        vmid = item.get("vmid")
        if kind in ("images", "rootdir") and vmid is not None:
            volumes.append({
                "volid": item.get("volid"),
                "vmid": str(vmid),
                "name": names.get((conn_name, str(vmid)), f"ID {vmid}"),
                "content": kind,
                "size": item.get("size"),
            })
        else:
            bucket = other.setdefault(str(kind), {"label": CONTENT_LABEL.get(str(kind), str(kind)), "count": 0, "size": 0})
            bucket["count"] += 1
            bucket["size"] += int(item.get("size") or 0)
    volumes.sort(key=lambda v: (v["vmid"], v["volid"] or ""))
    return {"volumes": volumes, "other": other}
