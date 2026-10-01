"""Vorhandene Sicherungen je Gast: was tatsaechlich auf den Backup-Speichern liegt --
nicht nur, ob der letzte Task "OK" meldete. Ein Job kann gruen sein, waehrend die
Aufbewahrung alles Aeltere loescht; ein Gast ohne Job kann trotzdem eine alte, von Hand
erstellte Sicherung haben.

**Zuordnung ueber VMID UND Typ, verbindungsuebergreifend:** Backup-Speicher koennen
geteilt sein (z. B. ein gemeinsames NFS-Ziel `backup-nas`), und VMIDs sind nur je
Proxmox-Instanz eindeutig -- dieselbe VMID kann auf pve1 eine VM und auf pve2 ein
Container sein. Die Sicherung eines Containers auf pve2 taucht dann auch beim Lesen ueber
die pve1-Verbindung auf, wo es gar keinen Gast mit dieser VMID gibt. Eine Datei wird
deshalb dem Gast mit derselben VMID und demselben Typ zugeordnet, bevorzugt auf der lesenden
Verbindung, sonst auf der einzigen anderen, die ihn kennt. Passt kein bekannter Gast,
ist die Datei "verwaist" (Gast geloescht/umgezogen) -- sie belegt nur Platz.

Rein lesend: `GET /nodes/{node}/storage?content=backup` und
`GET /nodes/{node}/storage/{storage}/content?content=backup`.
"""

from __future__ import annotations

from typing import Any

from .connector import ProxmoxBackupApiError, ProxmoxBackupConnector

KEEP_LABEL = {
    "keep-last": "letzte {n}",
    "keep-hourly": "{n} stündl.",
    "keep-daily": "{n} tägl.",
    "keep-weekly": "{n} wöchentl.",
    "keep-monthly": "{n} monatl.",
    "keep-yearly": "{n} jährl.",
}


def retention_label(prune: Any) -> str | None:
    """`prune-backups` -> Anzeige. Proxmox liefert je nach Version ein Objekt
    (`{"keep-last": "3"}`, live gesehen) oder einen String ("keep-last=3,keep-daily=7")."""
    if not prune:
        return None
    if isinstance(prune, str):
        prune = dict(part.split("=", 1) for part in prune.split(",") if "=" in part)
    if str(prune.get("keep-all", "0")) == "1":
        return "alle behalten"
    parts = [KEEP_LABEL[key].format(n=prune[key]) for key in KEEP_LABEL if prune.get(key) not in (None, "", "0", 0)]
    return ", ".join(parts) or None


def file_kind(row: dict[str, Any]) -> str | None:
    """"qemu"/"lxc" aus `subtype` (live vorhanden) oder dem Dateinamen als Rueckfall."""
    subtype = row.get("subtype")
    if subtype in ("qemu", "lxc"):
        return subtype
    volid = str(row.get("volid") or "")
    for kind in ("qemu", "lxc"):
        if f"vzdump-{kind}-" in volid:
            return kind
    return None


async def list_backup_files(connector: ProxmoxBackupConnector) -> tuple[list[dict[str, Any]], list[str]]:
    """Alle Backup-Dateien, die eine Verbindung sieht. Gemeinsame Speicher (`shared`)
    werden nur einmal gelesen, auch wenn jeder Knoten sie meldet; inaktive gar nicht."""
    files: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_storages: set[tuple[str, str]] = set()
    for node in await connector.list_nodes():
        node_name = node.get("node")
        if not node_name or node.get("status") not in (None, "online"):
            continue
        try:
            storages = await connector.backup_storages(node_name)
        except ProxmoxBackupApiError as exc:
            errors.append(f"{node_name}: {exc}")
            continue
        for storage in storages:
            name = storage.get("storage")
            if not name or not storage.get("active", 1):
                continue
            key = ("*" if storage.get("shared") else node_name, name)
            if key in seen_storages:
                continue
            seen_storages.add(key)
            try:
                rows = await connector.backup_files(node_name, name)
            except ProxmoxBackupApiError as exc:
                errors.append(f"{name}: {exc}")
                continue
            files.extend({**row, "storage": name} for row in rows if row.get("volid") and row.get("vmid") is not None)
    return files, errors


def summarize(
    files_by_connection: dict[str, list[dict[str, Any]]],
    known_guests: dict[tuple[str, str], list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """-> (je Gast, verwaiste Dateien je lesender Verbindung + VMID + Typ).
    `known_guests`: (Typ, VMID) -> Verbindungen, auf denen es diesen Gast gibt."""
    seen: set[tuple[Any, ...]] = set()
    groups: dict[tuple[str, str, str | None, bool], dict[str, Any]] = {}
    for scanner, files in files_by_connection.items():
        for row in files:
            # Derselbe NFS-Speicher kann ueber zwei Verbindungen sichtbar sein.
            identity = (row["volid"], row.get("ctime"), row.get("size"))
            if identity in seen:
                continue
            seen.add(identity)
            vmid, kind = str(row["vmid"]), file_kind(row)
            owners = known_guests.get((kind or "", vmid), [])
            if scanner in owners:
                target, orphan = scanner, False
            elif len(owners) == 1:
                target, orphan = owners[0], False
            else:
                target, orphan = scanner, True
            entry = groups.setdefault((target, vmid, kind if orphan else None, orphan), {
                "connection": target, "vmid": vmid, "count": 0, "total_size": 0,
                "newest_at": None, "oldest_at": None, "storages": [],
                **({"kind": kind} if orphan else {}),
            })
            ctime = row.get("ctime")
            entry["count"] += 1
            entry["total_size"] += int(row.get("size") or 0)
            if isinstance(ctime, int):
                entry["newest_at"] = max(entry["newest_at"] or ctime, ctime)
                entry["oldest_at"] = min(entry["oldest_at"] or ctime, ctime)
            if row["storage"] not in entry["storages"]:
                entry["storages"].append(row["storage"])
    guests: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []
    for (_target, _vmid, _kind, orphan), entry in groups.items():
        entry["storages"].sort()
        (orphans if orphan else guests).append(entry)
    order = lambda e: (e["connection"], int(e["vmid"]) if e["vmid"].isdigit() else 0)  # noqa: E731
    return sorted(guests, key=order), sorted(orphans, key=order)
