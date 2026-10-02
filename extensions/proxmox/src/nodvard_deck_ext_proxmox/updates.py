"""Paket-Updates je Knoten: was wartet, ob ein neuer Kernel dabei ist, ob ein Neustart
aussteht, wann Proxmox zuletzt nach Updates gesehen hat.

Rein lesend -- `GET /nodes/{node}/apt/update` liest nur die Liste, die Proxmox' eigener
naechtlicher Check (`aptupdate`-Task, `pve-daily-update.timer`) ohnehin aktualisiert.
Installiert wird hier nichts; das bleibt Sache des Administrators.

Typischer Fall: unter den wartenden Paketen ist ein neuer Kernel. Nach der Installation
laeuft bis zum Neustart weiter der alte Kernel -- deshalb meldet diese Ansicht einen
ausstehenden Neustart gesondert.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from .config import build_connectors
from .connector import ProxmoxApiError

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

KERNEL_PACKAGE_RE = re.compile(r"^(proxmox|pve)-kernel-")
# Konkretes, installierbares Kernel-Image ("proxmox-kernel-6.8.12-4-pve-signed",
# aelter: "pve-kernel-5.15.108-1-pve") -- im Gegensatz zu Meta-Paketen wie
# "proxmox-kernel-6.8", die nur auf das neueste Image zeigen.
KERNEL_IMAGE_RE = re.compile(r"^(?:proxmox|pve)-kernel-(\d+\.\d+\.\d+-\d+)-pve(?:-signed)?$")


# Proxmox startet den naechtlichen Check (`pve-daily-update.timer`) mit einer zufaelligen
# Verzoegerung von mehreren Stunden; zwei gesunde Laeufe liegen deshalb bis zu rund 29 Stunden
# auseinander. Erst deutlich darueber gilt der Check als ausgeblieben.
CHECK_STALE_AFTER_S = 36 * 3600


def check_age(last_check: Any, now: float | None = None) -> tuple[int | None, bool]:
    """-> (Alter des letzten Prueflaufs in Sekunden, ob er laenger als `CHECK_STALE_AFTER_S` her ist).
    Ohne (lesbare) Zeitangabe: (None, False) -- "kein Lauf gefunden" ist etwas anderes als "alt"."""
    if isinstance(last_check, bool) or not isinstance(last_check, int | float):
        return None, False
    age = max(0, int((time.time() if now is None else now) - last_check))
    return age, age > CHECK_STALE_AFTER_S


def version_key(version: str) -> tuple[int, ...]:
    """"6.8.12-4" -> (6, 8, 12, 4). Reicht fuer Kernel-Versionen; nicht-numerische
    Teile werden ignoriert statt zu raten."""
    return tuple(int(part) for part in re.split(r"[.-]", version) if part.isdigit())


def newest_installed_kernel(versions: list[dict[str, Any]]) -> str | None:
    installed = [
        match.group(1)
        for row in versions
        if row.get("CurrentState") == "Installed" and (match := KERNEL_IMAGE_RE.match(str(row.get("Package") or "")))
    ]
    return max(installed, key=version_key) if installed else None


def summarize(count: int, kernel_update: bool, reboot_pending: bool, newest_kernel: str | None) -> tuple[str, str, str]:
    """-> (Zusammenfassung, Badge-Text, Ton). Ein ausstehender Neustart wiegt schwerer
    als wartende Pakete: der laufende Kernel ist dann schon veraltet."""
    parts: list[str] = []
    if reboot_pending:
        parts.append(f"Neustart ausstehend: Kernel {newest_kernel} installiert, läuft noch nicht")
    if count:
        text = f"{count} Update{'s' if count != 1 else ''} verfügbar"
        if kernel_update:
            text += ", darunter ein neuer Kernel (danach Neustart nötig)"
        parts.append(text)
    if not parts:
        return "Auf dem neuesten Stand", "aktuell", "good"
    badge = "Neustart" if reboot_pending else f"{count} Update{'s' if count != 1 else ''}"
    return " · ".join(parts), badge, "warn"


async def _node_updates(connector: Any, conn_name: str, node_name: str, *, details: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"connection": conn_name, "node": node_name, "error": None}
    try:
        packages = await connector.apt_updates(node_name)
        status = await connector.node_status(node_name)
        versions = await connector.apt_versions(node_name)
        last = await connector.node_tasks(node_name, limit=1, typefilter="aptupdate")
    except ProxmoxApiError as exc:
        summary = f"Nicht abrufbar: {exc}"
        return {**result, "error": str(exc), "count": None, "summary": summary, "badge": "?", "tone": "neutral", "packages": []}

    running = str((status.get("current-kernel") or {}).get("release") or "") or None
    newest = newest_installed_kernel(versions)
    running_version = re.sub(r"-pve$", "", running) if running else None
    reboot_pending = bool(newest and running_version and version_key(newest) > version_key(running_version))
    kernel_update = any(KERNEL_PACKAGE_RE.match(str(p.get("Package") or "")) for p in packages)
    summary, badge, tone = summarize(len(packages), kernel_update, reboot_pending, newest)
    manager = str(status.get("pveversion") or "")
    last_start = last[0].get("starttime") if last else None
    last_age, last_stale = check_age(last_start)
    # Ergebnis des letzten Laufs im Klartext ("OK", "WARNINGS: 1", Fehlertext); leer, solange er noch laeuft.
    last_status = str(last[0].get("status")) if last and last[0].get("endtime") and last[0].get("status") else None
    result.update({
        "pve_version": manager.split("/")[1] if manager.count("/") >= 1 else manager or None,
        "running_kernel": running,
        "newest_kernel": newest,
        "reboot_pending": reboot_pending,
        "kernel_update": kernel_update,
        "count": len(packages),
        "last_check": last_start,
        "last_check_ok": (last[0].get("status") == "OK") if last and last[0].get("endtime") else None,
        "last_check_status": last_status,
        "last_check_age_s": last_age,
        "last_check_stale": last_stale,
        "summary": summary,
        "badge": badge,
        "tone": tone,
    })
    if details:
        result["packages"] = sorted(
            (
                {
                    "package": p.get("Package"),
                    "title": p.get("Title"),
                    "old_version": p.get("OldVersion"),
                    "version": p.get("Version"),
                    "origin": p.get("Origin"),
                    "new_package": p.get("OldVersion") is None,
                }
                for p in packages
            ),
            key=lambda p: str(p["package"]),
        )
    return result


async def collect_updates(ctx: "ExtensionContext", *, details: bool) -> dict[str, Any]:
    """`details=False` (Widget): nur Zusammenfassung je Knoten, ohne Paketliste."""
    nodes: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for conn_name, connector in (await build_connectors(ctx)).items():
        try:
            listed = await connector.list_nodes()
        except ProxmoxApiError as exc:
            errors.append({"connection": conn_name, "error": str(exc)})
            continue
        for node in listed:
            node_name = node.get("node")
            if not node_name:
                continue
            if node.get("status") not in (None, "online"):
                nodes.append({
                    "connection": conn_name, "node": node_name, "error": "Knoten offline", "count": None,
                    "summary": "Knoten offline", "badge": "offline", "tone": "danger", "packages": [],
                })
                continue
            nodes.append(await _node_updates(connector, conn_name, node_name, details=details))
    nodes.sort(key=lambda n: (n["connection"], n["node"]))
    return {"nodes": nodes, "errors": errors}
