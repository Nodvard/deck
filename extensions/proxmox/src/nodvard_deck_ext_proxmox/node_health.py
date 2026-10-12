"""Knoten-Gesundheit: Prozessor, Last, RAM/Swap, Systemplatte
und die physischen Datentraeger mit SMART-Zustand, Restlebensdauer und Temperatur --
das, wofuer man in Proxmox "Uebersicht" und "Disks" des Knotens aufmacht.

Rein lesend (`/nodes/{node}/status`, `/disks/list`, `/disks/smart`, `/disks/zfs`).

Manche SSDs melden keine Abnutzung -- Proxmox zeigt dann "N/A". In kleinen Setups hat
ein Knoten oft nur eine Platte -- faellt sie aus, ist der Knoten weg; deshalb gehoert ihr
Zustand aufs Dashboard.
"""

from __future__ import annotations

import re
from typing import Any

from .connector import ProxmoxApiError

_NVME_TEMP_RE = re.compile(r"^Temperature:\s+(\d+)\s+Celsius", re.MULTILINE)
_NVME_HOURS_RE = re.compile(r"^Power On Hours:\s+([\d,.]+)", re.MULTILINE)
_LEADING_INT_RE = re.compile(r"^\s*(\d+)")

TYPE_LABEL = {"nvme": "NVMe", "ssd": "SSD", "hdd": "HDD", "usb": "USB"}


def _int(value: Any) -> int | None:
    match = _LEADING_INT_RE.match(str(value)) if value is not None else None
    return int(match.group(1)) if match else None


def smart_details(smart: dict[str, Any]) -> dict[str, int | None]:
    """Temperatur und Betriebsstunden aus der SMART-Antwort. NVMe liefert Freitext
    (`type: "text"`), SATA eine Attributliste (`type: "ata"`) -- live beide gesehen."""
    temperature: int | None = None
    hours: int | None = None
    text = smart.get("text") or ""
    if text:
        if match := _NVME_TEMP_RE.search(text):
            temperature = int(match.group(1))
        if match := _NVME_HOURS_RE.search(text):
            hours = int(re.sub(r"[,.]", "", match.group(1)))
    for attr in smart.get("attributes") or []:
        name = attr.get("name")
        if name in ("Temperature_Celsius", "Airflow_Temperature_Cel") and temperature is None:
            temperature = _int(attr.get("raw"))
        elif name == "Power_On_Hours" and hours is None:
            hours = _int(attr.get("raw"))
    if temperature is not None and not TEMP_PLAUSIBLE[0] <= temperature <= TEMP_PLAUSIBLE[1]:
        temperature = None  # ein Sensor ohne Wert meldet oft 0 oder Unsinn: kein Messwert
    return {"temperature_c": temperature, "power_on_hours": hours}


def disk_assessment(
    health: str | None, life_left: int | None, temperature: int | None, temp_limit: int = 70,
) -> tuple[str, str]:
    """-> (Badge-Text, Ton). SMART-Befund zuerst, dann Abnutzung, dann Temperatur (ab `temp_limit`, dieselbe Schwelle
    wie die Meldung, siehe `temperature_limit`)."""
    normalized = (health or "").upper()
    if normalized and normalized not in ("PASSED", "OK"):
        return "SMART: " + (health or "?"), "danger"
    if life_left is not None and life_left < 10:
        return f"{life_left} % Rest", "danger"
    if life_left is not None and life_left < 30:
        return f"{life_left} % Rest", "warn"
    if temperature is not None and temperature >= temp_limit:
        return f"{temperature} °C", "warn"
    if not normalized:
        return "unbekannt", "neutral"
    return "gesund", "good"


ZFS_CRITICAL = frozenset({"FAULTED", "UNAVAIL", "SUSPENDED", "REMOVED"})
"""Ein Pool in diesem Zustand liefert keine (oder keine verlaesslichen) Daten mehr."""
ZFS_STATE_TEXT = {
    "ONLINE": "in Ordnung",
    "DEGRADED": "eingeschränkt – eine Platte fehlt oder ist defekt, die Ausfallsicherheit ist verringert",
    "FAULTED": "ausgefallen",
    "OFFLINE": "abgeschaltet",
    "UNAVAIL": "nicht verfügbar",
    "REMOVED": "Platte entfernt",
    "SUSPENDED": "angehalten (Schreibfehler)",
}

TEMP_WARN_DEFAULT = {"hdd": 55, "ssd": 65, "nvme": 70}
"""Ab wie viel °C ein Datentraeger als zu heiss gilt, wenn nichts eingestellt ist: Festplatten vertragen weniger als
SSDs und NVMe (die oft 60-70 °C unter Last erreichen). Bei vielen SATA-SSDs ist 70 °C schon das Betriebsmaximum, darum
65. Unbekannte Arten (auch USB) wie eine HDD."""
TEMP_PLAUSIBLE = (1, 120)
"""Werte ausserhalb sind kein echter Messwert (fehlender oder defekter Sensor) und gelten als "keine Temperatur"."""
TEMP_SETTING_MIN, TEMP_SETTING_MAX = 40, 95
TEMP_HYSTERESIS = 5
"""So viele Grad unter der Schwelle gilt ein Datentraeger wieder als normal warm."""


def temperature_limit(disk_type: Any, setting: int | None) -> int:
    """Die Warnschwelle fuer einen Datentraeger: die Einstellung, sonst die Vorgabe seiner Art."""
    if setting:
        return setting
    return TEMP_WARN_DEFAULT.get(str(disk_type or "").lower(), TEMP_WARN_DEFAULT["hdd"])


def temperature_setting(raw: Any) -> int | None:
    """`disk_temp_warn_c` aus den Einstellungen: eine ganze Zahl von 40 bis 95, sonst `None` (dann gilt die Vorgabe
    je Art). Die Oberflaeche laesst nur solche Werte zu; ein anders gespeicherter Wert legt nichts lahm."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        number = int(raw)
    except (OverflowError, ValueError):
        return None
    return number if number == raw and TEMP_SETTING_MIN <= number <= TEMP_SETTING_MAX else None


def zfs_assessment(health: Any) -> tuple[str, str, str]:
    """-> (Badge-Text, Ton, Satz) fuer einen ZFS-Pool."""
    state = str(health or "").upper()
    text = ZFS_STATE_TEXT.get(state, state.lower() or "unbekannt")
    if state == "ONLINE":
        return "ONLINE", "good", text
    if not state:
        return "unbekannt", "neutral", text
    return state, "danger" if state in ZFS_CRITICAL else "warn", text


async def collect_zfs(connector: Any, node: str) -> list[dict[str, Any]]:
    pools: list[dict[str, Any]] = []
    for row in await connector.node_zfs(node):
        name = str(row.get("name") or "")
        if not name:
            continue
        badge, tone, text = zfs_assessment(row.get("health"))
        parts = [text, f"{_format_size(row.get('alloc'))} von {_format_size(row.get('size'))} belegt"]
        if isinstance(row.get("frag"), (int, float)):
            parts.append(f"{row['frag']} % fragmentiert")
        pools.append({
            "name": name, "health": str(row.get("health") or "").upper() or None, "size": row.get("size"),
            "alloc": row.get("alloc"), "free": row.get("free"), "badge": badge, "tone": tone, "summary": " · ".join(parts),
        })
    return pools


def _format_size(size: Any) -> str:
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.0f} {unit}" if value >= 10 else f"{value:.1f} {unit}"
        value /= 1000
    return "?"


async def collect_disks(
    connector: Any, conn_name: str, node: str, temp_setting: int | None = None,
) -> list[dict[str, Any]]:
    """`temp_setting`: die Einstellung `disk_temp_warn_c` (`temperature_setting`), damit Seite und Meldung dieselbe
    Schwelle nehmen."""
    disks: list[dict[str, Any]] = []
    for row in await connector.node_disks(node):
        devpath = row.get("devpath")
        life_left = row.get("wearout") if isinstance(row.get("wearout"), int) else None
        try:
            smart = await connector.disk_smart(node, devpath) if devpath else {}
        except ProxmoxApiError:
            smart = {}
        details = smart_details(smart)
        badge, tone = disk_assessment(
            row.get("health"), life_left, details["temperature_c"], temperature_limit(row.get("type"), temp_setting),
        )
        parts = [TYPE_LABEL.get(str(row.get("type")), str(row.get("type") or "?")), _format_size(row.get("size"))]
        if life_left is not None:
            parts.append(f"{life_left} % Restlebensdauer")
        if details["temperature_c"] is not None:
            parts.append(f"{details['temperature_c']} °C")
        disks.append({
            "connection": conn_name,
            "node": node,
            "devpath": devpath,
            "model": (row.get("model") or "").replace("_", " ") or devpath,
            "type": row.get("type"),
            "size": row.get("size"),
            "health": row.get("health"),
            "life_left_percent": life_left,
            **details,
            "summary": " · ".join(parts),
            "badge": badge,
            "tone": tone,
        })
    return disks


async def collect_node_health(connector: Any, conn_name: str, node: str, temp_setting: int | None = None) -> dict[str, Any]:
    status = await connector.node_status(node)
    cpu = status.get("cpuinfo") or {}
    memory = status.get("memory") or {}
    swap = status.get("swap") or {}
    rootfs = status.get("rootfs") or {}
    manager = str(status.get("pveversion") or "")
    try:
        disks = await collect_disks(connector, conn_name, node, temp_setting)
        disks_error = None
    except ProxmoxApiError as exc:
        disks, disks_error = [], str(exc)
    try:
        zfs = await collect_zfs(connector, node)
        zfs_error = None
    except ProxmoxApiError as exc:
        zfs, zfs_error = [], str(exc)
    return {
        "connection": conn_name,
        "node": node,
        "cpu_model": cpu.get("model"),
        "cpu_cores": cpu.get("cores"),
        "cpu_threads": cpu.get("cpus"),
        "cpu_sockets": cpu.get("sockets"),
        "loadavg": [float(x) for x in status.get("loadavg") or []],
        "io_wait_percent": round(float(status.get("wait") or 0) * 100, 1),
        # "available" statt "free": Linux-Seitencache zaehlt sonst als belegt.
        "mem_total": memory.get("total"),
        "mem_available": memory.get("available", memory.get("free")),
        "swap_total": swap.get("total"),
        "swap_used": swap.get("used"),
        "rootfs_total": rootfs.get("total"),
        "rootfs_used": rootfs.get("used"),
        "ksm_shared": (status.get("ksm") or {}).get("shared"),
        "uptime_s": status.get("uptime"),
        "pve_version": manager.split("/")[1] if "/" in manager else manager or None,
        "kernel": (status.get("current-kernel") or {}).get("release"),
        "boot_mode": ((status.get("boot-info") or {}).get("mode") or "").upper() or None,
        "disks": disks,
        "disks_error": disks_error,
        "zfs": zfs,
        "zfs_error": zfs_error,
    }
