"""Liest die Antworten der DSM-Web-API defensiv: jedes Feld darf fehlen, dann ist der Wert `None`.

Zurückgegeben wird `None`, wenn die Antwort nicht nach dem erwarteten Aufruf aussieht. Die Seriennummer, die DSM
mitschickt, wird nie gelesen."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .models import Disk, Load, Pool, SystemInfo, Update, Volume

MAX_TEXT = 80
MAX_ITEMS = 64  # Speicherpools, Volumes bzw. Festplatten, die wir übernehmen
MAX_BYTES = 1 << 63

_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,63}")
_SID = re.compile(r"[A-Za-z0-9_.~+/=-]{8,512}")
_DIGITS = re.compile(r"\s*[0-9]{1,20}\s*")
_NUMBER = re.compile(r"\s*-?[0-9]+(\.[0-9]+)?\s*")
_VOLUME_ID = re.compile(r"volume_([0-9]{1,4})")
_POOL_ID = re.compile(r"reuse_([0-9]{1,4})")
_RAID_LEVEL = re.compile(r"raid_([0-9]{1,2}|f1)")
_TASK_ID = re.compile(r"[0-9]{1,9}")
# Zeiten von Hyper Backup („2026/10/16 03:00“, auch mit Sekunden) und die Uhr aus `SYNO.DSM.Info` („Sat Oct 17 14:05:09
# 2026“), jeweils Ortszeit der Synology ohne Zone.
_LOCAL_TIME = re.compile(r"\s*([0-9]{4})[/-]([0-9]{1,2})[/-]([0-9]{1,2})[ T]([0-9]{1,2}):([0-9]{2})(?::([0-9]{2}))?\s*")
_CTIME = re.compile(r"\s*[A-Za-z]{3}\s+([A-Za-z]{3})\s+([0-9]{1,2})\s+([0-9]{1,2}):([0-9]{2}):([0-9]{2})\s+([0-9]{4})\s*")
_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1,
)}

# Zustände von Volumes und Festplatten in DSM -> (deutscher Text, Ton); Unbekanntes siehe `tone_for`.
_STATUS_LABELS = {
    "normal": "Normal",
    "attention": "Achtung",
    "warning": "Warnung",
    "degraded": "Eingeschränkt",
    "repairing": "Wird repariert",
    "background": "Hintergrundaufgabe",
    "expanding": "Wird erweitert",
    "crashed": "Ausgefallen",
    "failing": "Fällt aus",
    "abnormal": "Auffällig",
    "not_use": "Nicht verwendet",
    "initialized": "Initialisiert",
}
# Art des Verbunds in DSM (`device_type`) -> deutscher Name; RAID-Stufen ergänzt `raid_label`.
_RAID_LABELS = {
    "basic": "Basic",
    "jbod": "JBOD",
    "shr_without_disk_protect": "SHR ohne Ausfallschutz",
    "shr_with_1_disk_protect": "SHR",
    "shr_with_2_disk_protect": "SHR-2",
}
_GOOD = frozenset({"normal"})
_WARN = frozenset({"attention", "warning", "degraded", "repairing", "background", "expanding", "resyncing", "scrubbing"})
_DANGER = frozenset({"crashed", "failing", "abnormal"})
_BUSY = frozenset({"repairing", "background", "expanding", "resyncing", "scrubbing"})


@dataclass(frozen=True)
class ApiInfo:
    path: str
    min_version: int
    max_version: int


@dataclass(frozen=True)
class Storage:
    volumes: list[Volume]
    disks: list[Disk]
    pools: list[Pool] = field(default_factory=list)


@dataclass(frozen=True)
class TaskEntry:
    id: str
    name: str


@dataclass(frozen=True)
class BackupStatus:
    """Stand einer Aufgabe von Hyper Backup; Zeiten in Ortszeit der Synology."""

    state: str | None
    status: str | None
    last_result: str | None
    last_run: datetime | None
    last_success: datetime | None
    next_run: datetime | None


def clean_text(value: Any, limit: int = MAX_TEXT) -> str | None:
    """Kurzer Text ohne Steuerzeichen und Zeilenumbrüche; `None`, wenn es keiner ist oder er leer bleibt."""
    if not isinstance(value, str):
        return None
    text = "".join(" " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch for ch in value[: limit * 4])
    text = " ".join(text.split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str) and _NUMBER.fullmatch(value):
        return float(value)
    return None


def _whole(value: Any, *, maximum: int = MAX_BYTES) -> int | None:
    """Eine ganze Zahl von 0 bis `maximum`, auch aus einem Text aus Ziffern (DSM schickt Größen als Text)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            return None
        number = int(value)
    elif isinstance(value, str) and _DIGITS.fullmatch(value):
        number = int(value)
    else:
        return None
    return number if 0 <= number <= maximum else None


def _percent(value: float) -> float:
    return round(min(100.0, max(0.0, value)), 1)


def parse_local_time(value: Any) -> datetime | None:
    """Ein Zeitpunkt aus DSM ohne Zone; `None` bei leerem Text, „N/A“ oder einem unbekannten Format."""
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        if match := _LOCAL_TIME.fullmatch(value):
            year, month, day, hour, minute, second = (int(part or 0) for part in match.groups())
            return datetime(year, month, day, hour, minute, second)
        if match := _CTIME.fullmatch(value):
            month_name, day, hour, minute, second, year = match.groups()
            month = _MONTHS.get(month_name.lower())
            return None if month is None else datetime(int(year), month, int(day), int(hour), int(minute), int(second))
    except ValueError:  # 31. Februar und Ähnliches
        return None
    return None


def iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat(timespec="minutes")


def _celsius(value: Any) -> int | None:
    """Temperatur in °C; 0 und weniger meldet DSM, wenn es keinen Fühler gibt."""
    number = _number(value)
    if number is None or not 0 < number <= 150:
        return None
    return int(number + 0.5)


# --- Schnittstellenliste und Anmeldung


def parse_discovery(data: Any) -> dict[str, ApiInfo] | None:
    """`SYNO.API.Info` `query`: Pfad und Versionen je Schnittstelle; Einträge ohne brauchbaren Pfad fehlen einfach."""
    if not isinstance(data, dict):
        return None
    found: dict[str, ApiInfo] = {}
    for name, entry in list(data.items())[:200]:
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        path = entry.get("path")
        low, high = _whole(entry.get("minVersion"), maximum=1000), _whole(entry.get("maxVersion"), maximum=1000)
        if not isinstance(path, str) or not _PATH.fullmatch(path) or ".." in path or "//" in path or low is None or high is None:
            continue
        found[name] = ApiInfo(path, low, high)
    return found


def parse_sid(data: Any) -> str | None:
    sid = data.get("sid") if isinstance(data, dict) else None
    return sid if isinstance(sid, str) and _SID.fullmatch(sid) else None


# --- Zustände


def tone_for(status: str | None) -> str:
    """good | warn | danger | neutral zu einem Zustand aus DSM; Unbekanntes mit „crash“/„fail“ im Namen ist rot."""
    if not status:
        return "neutral"
    key = status.lower()
    if key in _GOOD:
        return "good"
    if key in _DANGER:
        return "danger"
    if key in _WARN:
        return "warn"
    if "crash" in key or "fail" in key:
        return "danger"
    if any(part in key for part in ("degrad", "warn", "repair", "attention")):
        return "warn"
    return "neutral"


def _pretty(text: str) -> str:
    words = text.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def status_label(status: str | None) -> str:
    if not status:
        return "Unbekannt"
    return _STATUS_LABELS.get(status.lower()) or _pretty(status)


def volume_name(volume_id: str) -> str:
    match = _VOLUME_ID.fullmatch(volume_id)
    return f"Volume {int(match[1])}" if match else _pretty(volume_id)


def pool_name(pool_id: str, number: Any) -> str:
    whole = _whole(number, maximum=9999)
    if whole:
        return f"Speicherpool {whole}"
    match = _POOL_ID.fullmatch(pool_id)
    return f"Speicherpool {int(match[1])}" if match else _pretty(pool_id)


def raid_label(device_type: str | None) -> str | None:
    if not device_type:
        return None
    key = device_type.lower()
    if key in _RAID_LABELS:
        return _RAID_LABELS[key]
    match = _RAID_LEVEL.fullmatch(key)
    return f"RAID {match[1].upper()}" if match else _pretty(device_type)


def is_busy(status: str | None) -> bool:
    """Reparieren, Erweitern oder Prüfen läuft gerade; danach steht erst fest, ob wieder alles in Ordnung ist."""
    return bool(status) and status.lower() in _BUSY


def is_problem_status(status: str | None) -> bool:
    """Ein Zustand, über den man Bescheid wissen muss; Reparieren, Erweitern und Prüfen laufen von selbst und zählen nicht."""
    if not status:
        return False
    key = status.lower()
    if is_busy(key):
        return False
    return tone_for(status) in ("warn", "danger")


def is_undecided(status: str | None) -> bool:
    """Weder ein Problem noch „Normal“, etwa eine laufende Reparatur oder ein unbekannter Zustand: Es steht noch nichts fest."""
    return not is_problem_status(status) and tone_for(status) != "good"


# --- Teile


def parse_system_info(data: Any) -> SystemInfo | None:
    """`SYNO.DSM.Info` `getinfo`."""
    if not isinstance(data, dict):
        return None
    model = clean_text(data.get("model"), 40)
    version = clean_text(data.get("version_string"), 60)
    if model is None and version is None:
        return None
    warn = data.get("temperature_warn")
    return SystemInfo(
        model=model,
        dsm_version=version,
        uptime_seconds=_whole(data.get("uptime"), maximum=10**10),
        temperature=_celsius(data.get("temperature")),
        temperature_warn=warn if isinstance(warn, bool) else None,
        ram_mb=_whole(data.get("ram"), maximum=10**9),
        clock=iso(parse_local_time(data.get("time"))),
    )


def parse_utilization(data: Any) -> Load | None:
    """`SYNO.Core.System.Utilization` `get`: CPU = Benutzer + System + Sonstiges, RAM aus `real_usage`."""
    if not isinstance(data, dict):
        return None
    cpu: float | None = None
    cpu_data = data.get("cpu")
    if isinstance(cpu_data, dict):
        parts = [_number(cpu_data.get(key)) for key in ("user_load", "system_load", "other_load")]
        known = [p for p in parts if p is not None]
        if known:
            cpu = _percent(sum(known))
    ram: float | None = None
    memory = data.get("memory")
    if isinstance(memory, dict):
        usage = _number(memory.get("real_usage"))
        if usage is not None:
            ram = _percent(usage)
        else:
            total, free = _number(memory.get("total_real")), _number(memory.get("avail_real"))
            if total and total > 0 and free is not None and 0 <= free <= total:
                ram = _percent((total - free) * 100 / total)
    if cpu is None and ram is None:
        return None
    return Load(cpu_percent=cpu, ram_percent=ram)


def _volume(entry: Any) -> Volume | None:
    if not isinstance(entry, dict):
        return None
    volume_id = clean_text(entry.get("id"), 40)
    if volume_id is None:
        return None
    status = clean_text(entry.get("status"), 40) or "unknown"
    size = entry.get("size")
    total = _whole(size.get("total")) if isinstance(size, dict) else None
    used = _whole(size.get("used")) if isinstance(size, dict) else None
    percent = None
    if total and used is not None:
        percent = min(100, (used * 200 + total) // (2 * total))
    return Volume(
        id=volume_id, name=volume_name(volume_id), status=status, status_label=status_label(status), tone=tone_for(status),
        fs_type=clean_text(entry.get("fs_type"), 20), used=used, total=total, percent=percent,
        pool_id=clean_text(entry.get("pool_path"), 40),
    )


def _disk_warning(entry: dict[str, Any]) -> str | None:
    problems = []
    if entry.get("exceed_bad_sector_thr") is True:
        problems.append("Zu viele defekte Sektoren")
    if entry.get("below_remain_life_thr") is True:
        problems.append("Restlebensdauer unter dem Grenzwert")
    return "; ".join(problems) or None


def _disk(entry: Any) -> Disk | None:
    if not isinstance(entry, dict):
        return None
    disk_id = clean_text(entry.get("id"), 40)
    name = clean_text(entry.get("name"), 40) or disk_id
    if disk_id is None or name is None:
        return None
    status = clean_text(entry.get("status"), 40) or "unknown"
    smart = clean_text(entry.get("smart_status"), 40)
    smart_tone = tone_for(smart) if smart else "neutral"
    warning = _disk_warning(entry)
    tone = tone_for(status)
    for extra in (smart_tone, "warn" if warning else "neutral"):
        if extra == "danger" or (extra == "warn" and tone in ("good", "neutral")):
            tone = extra
    vendor, model = clean_text(entry.get("vendor"), 30), clean_text(entry.get("model"), 50)
    if vendor and model and not model.lower().startswith(vendor.lower()):
        model = f"{vendor} {model}"
    return Disk(
        id=disk_id, name=name, model=model or vendor, kind=_disk_kind(entry), status=status, status_label=status_label(status),
        tone=tone, temp=_celsius(entry.get("temp")), smart_status=smart,
        smart_label=("In Ordnung" if smart.lower() == "normal" else status_label(smart)) if smart else None,
        smart_tone=smart_tone, warning=warning,
    )


def _disk_kind(entry: dict[str, Any]) -> str:
    """hdd | ssd | nvme, für die Grenze der Temperatur."""
    disk_type = entry.get("diskType")
    if isinstance(disk_type, str) and "nvme" in disk_type.lower():
        return "nvme"
    return "ssd" if entry.get("isSsd") is True else "hdd"


def _pool(entry: Any, volume_names: dict[str, str]) -> Pool | None:
    if not isinstance(entry, dict):
        return None
    pool_id = clean_text(entry.get("id"), 40)
    if pool_id is None:
        return None
    status = clean_text(entry.get("status"), 40) or "unknown"
    children = entry.get("pool_child")
    volume_ids = [clean_text(c.get("id"), 40) for c in children if isinstance(c, dict)] if isinstance(children, list) else []
    disks = entry.get("disks")
    size = entry.get("size")
    return Pool(
        id=pool_id, name=pool_name(pool_id, entry.get("num_id")), description=clean_text(entry.get("desc"), 60),
        raid=raid_label(clean_text(entry.get("device_type"), 40)), status=status, status_label=status_label(status),
        tone=tone_for(status), disks=len(disks) if isinstance(disks, list) else None,
        volumes=[volume_names.get(v, volume_name(v)) for v in volume_ids[:MAX_ITEMS] if v],
        total=_whole(size.get("total")) if isinstance(size, dict) else None,
    )


def _items(value: Any) -> list[Any]:
    return value[:MAX_ITEMS] if isinstance(value, list) else []


def parse_storage(data: Any) -> Storage | None:
    """`SYNO.Storage.CGI.Storage` `load_info`: Speicherpools, Volumes und Festplatten."""
    if not isinstance(data, dict):
        return None
    raw_volumes, raw_disks = data.get("volumes"), data.get("disks")
    if not isinstance(raw_volumes, list) and not isinstance(raw_disks, list):
        return None
    volumes = [v for v in (_volume(e) for e in _items(raw_volumes)) if v]
    disks = [d for d in (_disk(e) for e in _items(raw_disks)) if d]
    names = {v.id: v.name for v in volumes}
    pools = [p for p in (_pool(e, names) for e in _items(data.get("storagePools"))) if p]
    return Storage(volumes, disks, pools)


def parse_update(data: Any) -> Update | None:
    """`SYNO.Core.Upgrade.Server` `check`: `data.update`, sonst gilt `data` selbst als dieses Objekt."""
    if not isinstance(data, dict):
        return None
    update = data.get("update")
    if not isinstance(update, dict):
        update = data
    available = update.get("available")
    if not isinstance(available, bool):
        return None
    return Update(available=available, version=clean_text(update.get("version"), 60) if available else None)


# --- Hyper Backup


def parse_backup_tasks(data: Any) -> tuple[list[TaskEntry], int] | None:
    """`SYNO.Backup.Task` `list`: Kennung und Name je Aufgabe, dazu wie viele Aufgaben DSM insgesamt kennt."""
    if not isinstance(data, dict) or not isinstance(data.get("task_list"), list):
        return None
    raw = data["task_list"]
    total = data.get("total")
    # Nicht mitgeschickt (DSM kennt mehr) oder hier abgeschnitten; unbrauchbare Einträge zählen nicht mit.
    missing = max(0, total - len(raw)) if isinstance(total, int) and not isinstance(total, bool) else 0
    missing += max(0, len(raw) - MAX_ITEMS)
    tasks: list[TaskEntry] = []
    for entry in raw[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        raw_id = entry.get("task_id")
        task_id = str(raw_id) if isinstance(raw_id, int) and not isinstance(raw_id, bool) else raw_id
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            continue
        tasks.append(TaskEntry(id=task_id, name=clean_text(entry.get("name")) or f"Aufgabe {task_id}"))
    return tasks, len(tasks) + missing


def parse_backup_status(data: Any) -> BackupStatus | None:
    """`SYNO.Backup.Task` `status` mit den Zeiten des letzten und nächsten Laufs."""
    if not isinstance(data, dict):
        return None
    return BackupStatus(
        state=clean_text(data.get("state"), 40),
        status=clean_text(data.get("status"), 40),
        last_result=clean_text(data.get("last_bkp_result"), 40),
        last_run=parse_local_time(data.get("last_bkp_time")),
        last_success=parse_local_time(data.get("last_bkp_success_time")),
        next_run=parse_local_time(data.get("next_bkp_time")),
    )
