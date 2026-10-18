"""Zeilen für die Kacheln „Synology DSM“, „Synology Speicher“ und „Synology Hyper Backup“: fertige deutsche Texte, der
Ton ein fester Wert (good/warn/danger/neutral)."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from .backup import date_text
from .config import SETTINGS_HINT
from .models import BackupTask, Note, Overview, Volume
from .transport import SERVICE

MAX_ROWS = 10  # mehr passt nicht in eine Kachel, der Rest steht in einer Schlusszeile
FULL_PERCENT = 90  # über dieser Belegung warnt die Kachel

_PROBLEM = {
    "auth_failed": ("Anmeldung fehlgeschlagen", "danger"),
    "unreachable": ("nicht erreichbar", "danger"),
    "error": ("Fehler", "warn"),
}
_NOTE_LABEL = {
    "no_rights": ("Rechte fehlen", "warn"),
    "unavailable": ("nicht verfügbar", "neutral"),
    "error": ("Fehler", "warn"),
}
_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
_SEVERITY = {"critical": ("kritisch", "danger"), "warning": ("Warnung", "warn")}
MAX_PROBLEM_ROWS = 3


def _row(title: str, subtitle: str, label: str, tone: str) -> dict[str, Any]:
    return {"title": title, "subtitle": subtitle, "label": label, "tone": tone}


def size_text(count: int) -> str:
    """„1,5 GB“: 1024er-Schritte, unter 10 eine Nachkommastelle (mit Komma), Byte ohne."""
    value, unit = float(count), 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024
        unit += 1
    digits = 1 if unit > 0 and value < 10 else 0
    rounded = math.floor(value * 10**digits + 0.5) / 10**digits  # kaufmännisch, wie die Seite
    return f"{rounded:.{digits}f}".replace(".", ",") + f" {_UNITS[unit]}"


def duration_text(seconds: int) -> str:
    """„5 Min.“, „1 Std. 12 Min.“ oder „2 Tage 3 Std.“; unter einer Minute „< 1 Min.“."""
    minutes = seconds // 60
    if minutes < 1:
        return "< 1 Min."
    if minutes < 60:
        return f"{minutes} Min."
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} Std. {minutes} Min." if minutes else f"{hours} Std."
    days, hours = divmod(hours, 24)
    day_text = "1 Tag" if days == 1 else f"{days} Tage"
    return f"{day_text} {hours} Std." if hours else day_text


def percent_text(value: float) -> str:
    return f"{math.floor(value + 0.5)} %"


def usage_text(volume: Volume) -> str:
    """„412 GB von 3,6 TB belegt (11 %)“; ohne Größen „Belegung unbekannt“."""
    if volume.used is None or not volume.total:
        return "Belegung unbekannt"
    percent = f" ({volume.percent} %)" if volume.percent is not None else ""
    return f"{size_text(volume.used)} von {size_text(volume.total)} belegt{percent}"


def widget_state(overview: Overview) -> str:
    """`meta.state` der Kachel: `not_configured` blendet sie auf der Übersicht aus; eine eingetragene, aber kaputte
    Verbindung ist `error` und bleibt sichtbar."""
    if overview.state == "ok":
        return "ok"
    return "not_configured" if overview.state == "not_configured" else "error"


def _problem_row(overview: Overview) -> dict[str, Any]:
    if overview.state == "not_configured":
        return _row(f"{SERVICE} nicht eingerichtet", overview.message or SETTINGS_HINT, "nicht eingerichtet", "neutral")
    label, tone = _PROBLEM.get(overview.state, _PROBLEM["error"])
    return _row(f"{SERVICE}: {label}", overview.message or "", label, tone)


def _note_row(title: str, note: Note) -> dict[str, Any]:
    label, tone = _NOTE_LABEL.get(note.kind, _NOTE_LABEL["error"])
    return _row(title, note.message, label, tone)


def _trimmed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) <= MAX_ROWS:
        return rows
    rest = len(rows) - (MAX_ROWS - 1)
    return [*rows[: MAX_ROWS - 1], _row(f"… und {rest} weitere Zeilen", "Alles steht auf der Seite „Synology DSM“.", "–", "neutral")]


def attention_rows(overview: Overview) -> list[dict[str, Any]]:
    """Die wichtigsten Probleme zuerst; ein Update allein ist keins, es steht weiter unten."""
    serious = [p for p in overview.problems if p.severity in _SEVERITY]
    rows = [_row(p.subject, p.text, *_SEVERITY[p.severity]) for p in serious[:MAX_PROBLEM_ROWS]]
    if len(serious) > MAX_PROBLEM_ROWS:
        rest = len(serious) - MAX_PROBLEM_ROWS
        rows.append(_row(f"… und {rest} weitere Hinweise", "Alles steht auf der Seite „Synology DSM“.", "–", "neutral"))
    return rows


def status_rows(overview: Overview) -> list[dict[str, Any]]:
    if overview.state != "ok":
        return [_problem_row(overview)]
    rows = attention_rows(overview)
    system, load, update = overview.system, overview.load, overview.update
    if system is not None:
        facts = [system.dsm_version, f"Laufzeit {duration_text(system.uptime_seconds)}" if system.uptime_seconds is not None else None]
        rows.append(_row(system.model or "Synology", " · ".join(f for f in facts if f), "online", "good"))
    elif overview.system_note is not None:
        rows.append(_note_row(SERVICE, overview.system_note))
    if load is not None:
        cpu = percent_text(load.cpu_percent) if load.cpu_percent is not None else "–"
        ram = percent_text(load.ram_percent) if load.ram_percent is not None else "–"
        busy = max(load.cpu_percent or 0, load.ram_percent or 0) >= FULL_PERCENT
        rows.append(_row("Auslastung", f"CPU {cpu} · RAM {ram}", "hoch" if busy else "normal", "warn" if busy else "good"))
    elif overview.load_note is not None:
        rows.append(_note_row("Auslastung", overview.load_note))
    if system is not None and system.temperature is not None:
        hot = system.temperature_warn is True
        rows.append(_row("Temperatur", f"{system.temperature} °C", "zu warm" if hot else "in Ordnung", "danger" if hot else "good"))
    if update is not None:
        if update.available:
            rows.append(_row("DSM-Update verfügbar", update.version or "Die neue Version steht in DSM bereit.", "Update", "warn"))
        else:
            rows.append(_row("DSM ist aktuell", "Kein Update verfügbar.", "aktuell", "good"))
    elif overview.update_note is not None:
        rows.append(_note_row("Updates", overview.update_note))
    return _trimmed(rows)


def volume_rows(overview: Overview) -> list[dict[str, Any]]:
    if overview.state != "ok":
        return [_problem_row(overview)]
    if overview.volumes is None:
        return [_note_row("Speicher", overview.storage_note)] if overview.storage_note is not None else []
    rows: list[dict[str, Any]] = []
    # Ein Pool, der nicht in Ordnung ist, steht über seinen Volumes.
    for pool in overview.pools or []:
        if pool.tone in ("warn", "danger"):
            facts = (pool.raid, f"{len(pool.volumes)} Volumes" if pool.volumes else None)
            rows.append(_row(pool.name, " · ".join(f for f in facts if f), pool.status_label, pool.tone))
    for volume in overview.volumes:
        tone, label = volume.tone, volume.status_label
        if tone in ("good", "neutral") and volume.percent is not None and volume.percent > FULL_PERCENT:
            tone, label = "warn", "fast voll"
        rows.append(_row(volume.name, usage_text(volume), label, tone))
    if not overview.volumes:
        rows.append(_row("Keine Volumes", "DSM meldet keinen Speicherplatz.", "–", "neutral"))
    # Eine Festplatte mit Warnung gehört in dieselbe Kachel, sie ist wichtiger als die Belegung.
    for disk in overview.disks or []:
        if disk.tone in ("warn", "danger"):
            detail = " · ".join(p for p in (disk.model, disk.warning or disk.status_label) if p)
            rows.append(_row(f"Festplatte {disk.name}", detail, disk.status_label, disk.tone))
    return _trimmed(rows)


def _when(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return date_text(datetime.fromisoformat(value))
    except ValueError:
        return None


def task_subtitle(task: BackupTask) -> str:
    """„Zuletzt 16.10.2026, 03:00 · nächster Lauf 17.10.2026, 03:00“, bei einem Problem dessen Satz."""
    if task.problem:
        return task.problem
    last, upcoming = _when(task.last_run), _when(task.next_run)
    parts = [f"Zuletzt {last}" if last else "Noch kein Lauf", f"nächster Lauf {upcoming}" if upcoming else "ohne Zeitplan"]
    return " · ".join(parts)


def backup_rows(overview: Overview) -> list[dict[str, Any]]:
    if overview.state != "ok":
        return [_problem_row(overview)]
    if overview.backups is None:
        return [_note_row("Hyper Backup", overview.backup_note)] if overview.backup_note is not None else []
    if not overview.backups:
        return [_row("Keine Aufgaben", "In Hyper Backup ist noch keine Sicherung eingerichtet.", "–", "neutral")]
    # Was nicht stimmt, steht oben.
    order = {"danger": 0, "warn": 1}
    tasks = sorted(overview.backups, key=lambda t: order.get(t.tone, 2))
    return _trimmed([_row(t.name, task_subtitle(t), t.result_label, t.tone) for t in tasks])
