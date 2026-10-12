"""Wichtige Orte eines Proxmox-Knotens für den Dateimanager: der Ordner mit den Sicherungen."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nodvard_sdk import FilePlace

from .capabilities import _parse_ref
from .config import build_connector

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

# Speicherarten, die Proxmox selbst unter `/mnt/pve/<Speicher>` einhängt.
_MOUNTED_TYPES = frozenset({"nfs", "cifs", "cephfs", "glusterfs"})


# Den Ordner der Sicherungen (`vzdump`, Unterordner `dump`) nennt Proxmox über die API nicht. Er ergibt sich nur bei zwei
# Arten sicher: `local` (Art `dir`) liegt unter /var/lib/vz, Speicher an einer Netzfreigabe unter /mnt/pve/<Speicher>.
# Jedes andere Verzeichnis-Laufwerk hat einen frei gewählten Ordner: dafür gibt es keinen Ort, statt einen zu raten.
def backup_folder(pool: dict[str, Any]) -> str | None:
    """Ordner der Sicherungen eines Speichers, oder `None`, wenn er sich nicht sicher nennen lässt."""
    storage = str(pool.get("storage") or "")
    kind = str(pool.get("type") or "")
    if not storage or "/" in storage:
        return None
    if kind == "dir" and storage == "local":
        return "/var/lib/vz/dump"
    if kind in _MOUNTED_TYPES:
        return f"/mnt/pve/{storage}/dump"
    return None


def _is_on(value: Any) -> bool:
    return str(value if value is not None else 1).lower() not in ("0", "false")


def backup_places(pools: list[dict[str, Any]]) -> list[FilePlace]:
    """Je Speicher mit Inhaltsart `backup`, der benutzbar ist (aktiv, nicht abgeschaltet) und dessen Ordner bekannt ist."""
    places: list[FilePlace] = []
    for pool in pools:
        if "backup" not in str(pool.get("content") or "").split(","):
            continue
        if not _is_on(pool.get("active")) or not _is_on(pool.get("enabled")):
            continue
        folder = backup_folder(pool)
        if folder is None:
            continue
        storage = str(pool["storage"])
        places.append(FilePlace(
            label=f"Sicherungen ({storage})", path=folder, icon="database-backup",
            hint=f"Proxmox-Sicherungen auf dem Speicher „{storage}“",
        ))
    return places


class ProxmoxFilePlaces:
    """Erfüllt `nodvard_sdk.capabilities.FilePlaceProvider` für Proxmox-Knoten, die diese Erweiterung eingelesen hat.
    Gäste (VM, Container) und Server anderer Herkunft bekommen keine Orte von hier."""

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx

    async def places_for_host(self, host_id: str) -> list[FilePlace]:
        host = await self._ctx.hosts.get(host_id)
        if host is None or host.provider_ext_id != self._ctx.ext_id or not host.provider_ref:
            return []
        try:
            connection, kind, node, _ = _parse_ref(host.provider_ref)
        except IndexError:
            return []
        if kind != "node":
            return []
        connector = await build_connector(self._ctx, connection)
        return backup_places(await connector.node_storage(node))
