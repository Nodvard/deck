"""Was an der Synology Aufmerksamkeit braucht; dieselbe Liste zeigt die Seite, und der Wächter meldet sie."""

from __future__ import annotations

from datetime import datetime, timedelta

from .backup import MILD_FAILURES, failure_text, overdue_reason, state_problem
from .config import AlertConfig
from .models import Disk, Pool, Problem, SystemInfo, Update, Volume
from .parse import BackupStatus, Storage, TaskEntry, is_problem_status, tone_for

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
STORAGE_MANAGER = "Sieh im Speicher-Manager von DSM nach, was los ist."
HYPER_BACKUP_LOG = "Den Grund nennt das Protokoll in Hyper Backup."


def _severity(tone: str) -> str:
    return "critical" if tone == "danger" else "warning"


def _space_text(name: str, status: str, label: str) -> str:
    key = status.lower()
    if key == "degraded":
        return (
            f"{name} meldet „{label}“: Der Ausfallschutz fehlt, meist weil eine Festplatte ausgefallen ist. Prüfe im "
            "Speicher-Manager von DSM, welche Platte betroffen ist, und ersetze sie bald."
        )
    if key == "crashed":
        return (
            f"{name} meldet „{label}“: Die Daten darauf sind womöglich nicht mehr lesbar. Schreib nichts mehr darauf "
            "und öffne den Speicher-Manager von DSM."
        )
    return f"{name} meldet „{label}“. {STORAGE_MANAGER}"


def pool_problems(device: str, pools: list[Pool]) -> list[Problem]:
    return [
        Problem(
            key=f"pool:{pool.id}", subject=pool.name, severity=_severity(pool.tone),
            title=f"{device}: {pool.name} – {pool.status_label}",
            text=_space_text(pool.name + (f" ({pool.raid})" if pool.raid else ""), pool.status, pool.status_label),
        )
        for pool in pools
        if is_problem_status(pool.status)
    ]


def pool_tones(pools: list[Pool]) -> dict[str, str]:
    return {pool.id: tone_for(pool.status) for pool in pools}


def covered_by_pool(volume: Volume, tones: dict[str, str]) -> bool:
    """Ob das Problem eines Volumes zu seinem Pool gehört, der nicht „Normal“ ist (eingeschränkt, in Reparatur, unbekannt).

    Ein ausgefallenes Volume meldet sich trotzdem selbst, außer sein Pool ist selbst ausgefallen."""
    tone = tones.get(volume.pool_id or "")
    if tone is None or tone == "good":
        return False
    return volume.tone != "danger" or tone == "danger"


def volume_problems(device: str, volumes: list[Volume], tones: dict[str, str]) -> list[Problem]:
    return [
        Problem(
            key=f"volume:{volume.id}", subject=volume.name, severity=_severity(volume.tone),
            title=f"{device}: {volume.name} – {volume.status_label}",
            text=_space_text(volume.name, volume.status, volume.status_label),
        )
        for volume in volumes
        if is_problem_status(volume.status) and not covered_by_pool(volume, tones)
    ]


def _disk_reasons(disk: Disk) -> list[str]:
    reasons = [disk.warning] if disk.warning else []
    if disk.smart_tone in ("warn", "danger") and disk.smart_label:
        reasons.append(f"SMART: {disk.smart_label}")
    if is_problem_status(disk.status):
        reasons.append(f"Zustand: {disk.status_label}")
    return reasons


def disk_problems(device: str, disks: list[Disk], alerts: AlertConfig) -> list[Problem]:
    found: list[Problem] = []
    for disk in disks:
        if reasons := _disk_reasons(disk):
            model = f" ({disk.model})" if disk.model else ""
            found.append(Problem(
                key=f"disk:{disk.id}", subject=disk.name, severity=_severity(disk.tone),
                title=f"{device}: {disk.name} – {reasons[0]}",
                text=f"{disk.name}{model} meldet: {'; '.join(reasons)}. Sichere wichtige Daten und plane den Tausch der Festplatte.",
            ))
        limit = alerts.disk_limit(disk.kind)
        if disk.temp is not None and disk.temp >= limit:
            found.append(Problem(
                key=f"disk-temp:{disk.id}", subject=f"Temperatur von {disk.name}", severity="warning",
                title=f"{device}: {disk.name} zu warm ({disk.temp} °C)",
                text=(
                    f"{disk.name} ist {disk.temp} °C warm, die Grenze liegt bei {limit} °C. Prüfe Lüfter, Staub und ob das "
                    "Gerät genug Luft hat."
                ),
            ))
    return found


def system_problems(device: str, system: SystemInfo | None) -> list[Problem]:
    if system is None or system.temperature_warn is not True:
        return []
    degrees = f" ({system.temperature} °C)" if system.temperature is not None else ""
    return [Problem(
        key="temperature", subject="Temperatur", severity="warning", title=f"{device}: zu warm{degrees}",
        text=f"DSM meldet die Temperatur des Geräts als zu hoch{degrees}. Prüfe Lüfter, Staub und ob das Gerät genug Luft hat.",
    )]


def update_problems(device: str, update: Update | None) -> list[Problem]:
    if update is None or not update.available:
        return []
    version = update.version or "Eine neue Version von DSM"
    return [Problem(
        key=f"update:{update.version or 'neu'}", subject="DSM-Update", severity="info", title=f"{device}: DSM-Update verfügbar",
        text=f"{version} steht bereit. Einspielen kannst du es in der Systemsteuerung von DSM.",
    )]


def backup_problems(
    device: str, tasks: list[tuple[TaskEntry, BackupStatus | None]], now: datetime, slack: timedelta = timedelta(0),
) -> list[Problem]:
    found: list[Problem] = []
    for entry, status in tasks:
        if status is None:
            continue
        name = f"Hyper Backup „{entry.name}“"
        if broken := state_problem(status):
            found.append(Problem(
                key=f"backup-state:{entry.id}", subject=name, severity="warning", title=f"{device}: {name} gestört",
                text=f"{name}: {broken} Öffne Hyper Backup in DSM und sieh dir die Aufgabe an.",
            ))
        if failed := failure_text(status):
            mild = (status.last_result or "").lower() in MILD_FAILURES
            found.append(Problem(
                key=f"backup-failed:{entry.id}", subject=name, severity="warning" if mild else "critical",
                title=f"{device}: {name} {'unvollständig' if mild else 'fehlgeschlagen'}", text=f"{name}: {failed} {HYPER_BACKUP_LOG}",
            ))
        # Ein fehlgeschlagener Lauf ist schon gemeldet; überfällig wäre dieselbe Ursache ein zweites Mal.
        elif reason := overdue_reason(status, now, slack):
            found.append(Problem(
                key=f"backup-overdue:{entry.id}", subject=name, severity="warning", title=f"{device}: {name} überfällig",
                text=f"{name}: {reason} Prüfe in Hyper Backup, ob der Zeitplan an ist und das Ziel erreichbar ist.",
            ))
    return found


def collect(
    *, system: SystemInfo | None, storage: Storage | None, update: Update | None,
    tasks: list[tuple[TaskEntry, BackupStatus | None]] | None, alerts: AlertConfig, now: datetime,
    slack: timedelta = timedelta(0),
) -> list[Problem]:
    """Alle Probleme, das Wichtigste zuerst; ein Teil, der sich nicht lesen ließ, trägt nichts bei."""
    device = (system.model if system is not None else None) or "Synology"
    found = system_problems(device, system) + update_problems(device, update)
    if storage is not None:
        found += pool_problems(device, storage.pools)
        found += volume_problems(device, storage.volumes, pool_tones(storage.pools)) + disk_problems(device, storage.disks, alerts)
    if tasks is not None:
        found += backup_problems(device, tasks, now, slack)
    return sorted(found, key=lambda p: SEVERITY_ORDER.get(p.severity, 3))
