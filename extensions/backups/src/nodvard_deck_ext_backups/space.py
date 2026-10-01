"""Passt ein Backup ueberhaupt auf den Speicher? -- VOR dem Anlegen eines Jobs bzw.
dem Start eines Laufs.

**Warum:** ein grosser Gast kann den Backup-Speicher volllaufen lassen. Der Lauf
bricht ab, die halbe `.dat` bleibt liegen -- und liegt auf derselben Platte auch
Nodvard Deck selbst, scheitert danach JEDER Schreibzugriff im Dashboard ("HTTP 500"
beim Bestaetigen). Beide Zahlen (Gastgroesse, freier Platz) sind in Proxmox abrufbar,
also wird vorher gewarnt.

Die Gastgroesse ist bewusst eine OBERGRENZE: Summe der Platten, die VZDump sichert
(`backup=0` und CD-Laufwerke zaehlen nicht, bei Containern nur `rootfs` und
Mountpoints mit `backup=1`). Komprimierung und leere Bloecke machen das echte
Backup meist kleiner -- deshalb eine Warnung mit "trotzdem", kein hartes Verbot.
Fehlt eine der Zahlen (API-Fehler, fehlende Rechte), wird NICHT gewarnt: lieber
keine Warnung als eine geratene.
"""

from __future__ import annotations

import re
from typing import Any

from .connector import ProxmoxBackupApiError, ProxmoxBackupConnector

_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
_SIZE_RE = re.compile(r"(?:^|,)size=(\d+(?:\.\d+)?)([KMGT]?)(?:,|$)")
_QEMU_DISK_RE = re.compile(r"^(scsi|virtio|sata|ide|efidisk|tpmstate)\d+$")
_LXC_MP_RE = re.compile(r"^mp\d+$")


def _option(value: str, key: str) -> str | None:
    for part in value.split(","):
        k, _, v = part.partition("=")
        if k == key:
            return v
    return None


def _size_bytes(value: str) -> int | None:
    match = _SIZE_RE.search(value)
    if match is None:
        return None
    return int(float(match.group(1)) * _UNITS[match.group(2)])


def backed_up_disk_bytes(guest_type: str, config: dict[str, Any]) -> int | None:
    """Summe der Platten, die VZDump fuer diesen Gast sichert (Obergrenze)."""
    total = 0
    found = False
    for key, raw in config.items():
        value = str(raw)
        if guest_type == "lxc":
            if key == "rootfs":
                included = True
            elif _LXC_MP_RE.match(key):
                included = _option(value, "backup") == "1"
            else:
                continue
        else:
            if not _QEMU_DISK_RE.match(key) or _option(value, "media") == "cdrom":
                continue
            included = _option(value, "backup") != "0"
        size = _size_bytes(value)
        if size is None:
            continue
        found = True
        if included:
            total += size
    return total if found else None


def format_gb(value: int) -> str:
    return f"{value / 1024**3:.0f} GB"


async def check_backup_space(connector: ProxmoxBackupConnector, vmids: str | list[str], storage: str | None) -> dict[str, Any] | None:
    """-> Warnung (dict mit `message` und den Zahlen), wenn der/die Gast/Gaeste zusammen
    groesser sind als der freie Platz auf `storage`; sonst None -- auch dann, wenn eine
    Zahl nicht zu ermitteln war. Mehrere Gaeste (Job bearbeiten) werden addiert: ein
    Job-Lauf legt alle Sicherungen nebeneinander ab."""
    if not storage:
        return None
    wanted = [str(v) for v in ([vmids] if isinstance(vmids, str) else vmids)]
    if not wanted:
        return None
    try:
        resources = await connector.guest_resources()
        guests = []
        for vmid in wanted:
            guest = next((r for r in resources if str(r.get("vmid")) == vmid), None)
            if guest is None or not guest.get("node"):
                return None
            size = await _guest_bytes(connector, guest, vmid)
            if size is None:
                return None
            guests.append((guest, size))
        storages = await connector.backup_storages(str(guests[0][0]["node"]))
    except ProxmoxBackupApiError:
        return None
    target = next((s for s in storages if s.get("storage") == storage), None)
    avail = target.get("avail") if target else None
    guest_bytes = sum(size for _, size in guests)
    if not isinstance(avail, (int, float)) or guest_bytes <= avail:
        return None
    if len(guests) == 1:
        who = f"'{guests[0][0].get('name') or f'VMID {wanted[0]}'}' hat"
    else:
        who = f"Die {len(guests)} Gäste des Jobs haben zusammen"
    return {
        "vmid": ",".join(wanted),
        "storage": storage,
        "guest_bytes": guest_bytes,
        "avail_bytes": int(avail),
        "total_bytes": int(target["total"]) if isinstance(target.get("total"), (int, float)) else None,
        "message": (
            f"{who} bis zu {format_gb(guest_bytes)} zu sichern, auf Speicher '{storage}' sind "
            f"aber nur noch {format_gb(int(avail))} frei. Das Backup kann den Speicher komplett füllen "
            f"-- liegt Nodvard Deck auf demselben Gerät, funktioniert danach auch das Dashboard nicht mehr."
        ),
    }


async def _guest_bytes(connector: ProxmoxBackupConnector, guest: dict[str, Any], vmid: str) -> int | None:
    guest_type = str(guest.get("type") or "qemu")
    try:
        config = await connector.guest_config(str(guest["node"]), guest_type, vmid)
        size = backed_up_disk_bytes(guest_type, config)
    except ProxmoxBackupApiError:
        size = None
    if size is None:
        maxdisk = guest.get("maxdisk")
        size = int(maxdisk) if isinstance(maxdisk, (int, float)) and maxdisk > 0 else None
    return size
