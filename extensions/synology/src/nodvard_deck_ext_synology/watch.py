"""Wächter: meldet über das Meldungs-Center, wenn an der Synology etwas kippt oder schlimmer wird und wenn es wieder in
Ordnung ist. Was die Seite unter „Braucht Aufmerksamkeit“ zeigt, ist dieselbe Liste (`problems.py`)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from nodvard_sdk import Notification, Severity

from .config import AlertConfig
from .models import Overview, Problem
from .parse import is_problem_status, is_undecided
from .problems import covered_by_pool, pool_tones

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

    from .service import SynologyService

_log = logging.getLogger(__name__)

SCHEDULE = "*/5 * * * *"
STATE_FILE = "alerts.json"
PAGE_PATH = "/ext/synology/synology"
# Die Temperatur schwankt: erst nach drei Prüfungen in Folge (10 bis 15 Minuten) melden, wieder normal erst so viele
# Grad unter der Grenze.
TEMP_STREAK = 3
TEMP_HYSTERESIS = 5
RECOVERED = "Wieder in Ordnung."
SEVERITIES = {"critical": Severity.CRITICAL, "warning": Severity.WARNING, "info": Severity.INFO}
RANK = {"info": 0, "warning": 1, "critical": 2}

# Erster Teil der Kennung -> Teil der Übersicht, der gelesen sein muss, damit „nicht mehr da“ wirklich „gut“ heißt.
_PARTS = {
    "pool": "storage", "volume": "storage", "disk": "storage", "disk-temp": "storage", "temperature": "system",
    "update": "update", "backup-state": "backups", "backup-failed": "backups", "backup-overdue": "backups",
}
_BACKUP_KINDS = ("backup-state", "backup-failed", "backup-overdue")


def _load(path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _save(path: Path, state: dict[str, dict[str, Any]]) -> None:
    """Schreibt erst eine Kopie und ersetzt dann die Datei, damit ein Absturz nie einen halben Stand hinterlässt."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".tmp")
        partial.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(partial, path)
    except OSError:
        _log.warning("Synology-Wächter: Stand nicht gespeichert, Wechsel könnten doppelt gemeldet werden.")


def _kind(key: str) -> str:
    return key.split(":", 1)[0]


def _readable(overview: Overview) -> dict[str, bool]:
    return {
        "storage": overview.disks is not None, "system": overview.system is not None, "update": overview.update is not None,
        "backups": overview.backups is not None,
    }


def _existing(overview: Overview) -> set[str]:
    """Kennungen der Dinge, die es gerade gibt; verschwindet eines, wird es still vergessen."""
    found = {f"pool:{p.id}" for p in overview.pools or []} | {f"volume:{v.id}" for v in overview.volumes or []}
    for disk in overview.disks or []:
        found |= {f"disk:{disk.id}", f"disk-temp:{disk.id}"}
    for task in overview.backups or []:
        found |= {f"{kind}:{task.id}" for kind in _BACKUP_KINDS}
    return found | {"temperature"}


def _undecided(overview: Overview) -> set[str]:
    """Kennungen, über die diese Prüfung nichts sagt; ihr gemerkter Stand bleibt, wie er ist.

    Das sind laufende oder unlesbare Sicherungen, Laufwerke ohne Temperatur, Pools und Volumes, die gerade repariert oder
    geprüft werden oder einen unbekannten Zustand haben, und Volumes, die ein Pool mitzieht, der nicht „Normal“ ist."""
    found: set[str] = set()
    for task in overview.backups or []:
        if task.running or not task.status_read:
            found |= {f"{kind}:{task.id}" for kind in _BACKUP_KINDS}
    found |= {f"disk-temp:{d.id}" for d in overview.disks or [] if d.temp is None}
    if overview.system is not None and overview.system.temperature_warn is None:
        found.add("temperature")
    found |= {f"pool:{p.id}" for p in overview.pools or [] if is_undecided(p.status)}
    tones = pool_tones(overview.pools or [])
    found |= {
        f"volume:{v.id}" for v in overview.volumes or []
        if is_undecided(v.status) or (is_problem_status(v.status) and covered_by_pool(v, tones))
    }
    return found


def _still_warm(key: str, overview: Overview, alerts: AlertConfig) -> bool:
    """Ein gemeldetes Laufwerk gilt erst `TEMP_HYSTERESIS` Grad unter der Grenze wieder als normal."""
    disk_id = key.split(":", 1)[1]
    disk = next((d for d in overview.disks or [] if d.id == disk_id), None)
    return disk is not None and disk.temp is not None and disk.temp > alerts.disk_limit(disk.kind) - TEMP_HYSTERESIS


def _wanted(key: str, alerts: AlertConfig) -> bool:
    return alerts.notify_update if _kind(key) == "update" else alerts.notify


async def _send(ctx: ExtensionContext, severity: Severity, title: str, body: str, key: str) -> bool:
    """Ob die Meldung angelegt wurde; scheitert sie, bleibt der Stand, und sie kommt bei der nächsten Prüfung."""
    try:
        await ctx.notify.send(Notification(
            title=title, body=body, severity=severity, correlation_id=f"synology:{key}", payload={"path": PAGE_PATH},
        ))
    except Exception:  # noqa: BLE001 - eine fehlgeschlagene Meldung darf die übrigen nicht verhindern
        _log.warning("Synology-Wächter: Meldung %s ließ sich nicht anlegen.", key, exc_info=True)
        return False
    return True


async def run_watch(ctx: ExtensionContext, service: SynologyService) -> dict[str, Any]:
    alerts = await service.alerts()
    if not alerts.notify and not alerts.notify_update:
        return {"checked": 0, "notified": 0, "skipped": "abgeschaltet"}
    overview = await service.overview()
    if overview.state != "ok":
        # Ohne Antwort bleibt der gemerkte Stand, wie er ist; sonst käme beim nächsten Mal eine falsche Entwarnung.
        return {"checked": 0, "notified": 0, "skipped": overview.message or overview.state}

    path = Path(str(ctx.data_dir)) / STATE_FILE
    state = _load(path)
    current: dict[str, Problem] = {p.key: p for p in overview.problems}
    readable, existing, undecided = _readable(overview), _existing(overview), _undecided(overview)
    device = (overview.system.model if overview.system is not None else None) or "Synology"
    sent = 0
    try:
        for key in sorted(set(current) | set(state)):
            if not readable.get(_PARTS.get(_kind(key), ""), False) or (key in undecided and key not in current):
                continue
            if _kind(key) == "update" and key not in current:
                # Eine gemeldete Version bleibt gemerkt, auch wenn die Suche kurz nichts findet; eine neuere löst sie ab.
                if any(_kind(k) == "update" for k in current):
                    state.pop(key, None)
                continue
            if key not in current and key not in existing:
                state.pop(key, None)
                continue
            entry = state.setdefault(key, {"streak": 0, "alerted": False})
            bad = key in current or (_kind(key) == "disk-temp" and entry["alerted"] and _still_warm(key, overview, alerts))
            if bad:
                entry["streak"] += 1
                problem = current.get(key)
                # Abgeschaltet bleibt eine Meldung offen und kommt, sobald jemand sie wieder einschaltet.
                if problem is None or not _wanted(key, alerts):
                    continue
                threshold = TEMP_STREAK if _kind(key) in ("disk-temp", "temperature") else 1
                first = not entry["alerted"] and entry["streak"] >= threshold
                worse = entry["alerted"] and RANK.get(problem.severity, 1) > RANK.get(entry.get("severity", "warning"), 1)
                severity = SEVERITIES.get(problem.severity, Severity.WARNING)
                if (first or worse) and await _send(ctx, severity, problem.title, problem.text, key):
                    entry.update(alerted=True, subject=problem.subject, severity=problem.severity)
                    sent += 1
                continue
            if entry["alerted"] and alerts.notify:
                title = f"{device}: {entry.get('subject') or 'Problem'} wieder in Ordnung"
                if not await _send(ctx, Severity.INFO, title, RECOVERED, key):
                    continue
                sent += 1
            state.pop(key, None)
    finally:
        _save(path, state)
    return {"checked": len(current), "notified": sent}


class WatchJob:
    """`JobSpec` für `ctx.scheduler.register_job()`."""

    id = "watch"
    name = "Synology-Wächter"
    schedule = SCHEDULE
    params: ClassVar[dict[str, Any]] = {}
    enabled = True

    def __init__(self, ctx: ExtensionContext, service: SynologyService) -> None:
        self._ctx = ctx
        self._service = service

    async def handler(self, **_: Any) -> dict[str, Any]:
        return await run_watch(self._ctx, self._service)
