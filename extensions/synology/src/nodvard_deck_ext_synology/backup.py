"""Aufgaben von Hyper Backup: Ergebnis auf Deutsch, Ton und ob ein Lauf ausgeblieben ist."""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import BackupTask
from .parse import BackupStatus, TaskEntry, iso

# Ergebnis des letzten Laufs (`last_bkp_result`) -> (deutscher Text, Ton).
RESULTS = {
    "done": ("Erfolgreich", "good"),
    "success": ("Erfolgreich", "good"),
    "none": ("Noch nie gelaufen", "neutral"),
    "backingup": ("Läuft gerade", "neutral"),
    "resuming": ("Wird fortgesetzt", "neutral"),
    "version_deleting": ("Löscht alte Versionen", "neutral"),
    "partial": ("Teilweise gesichert", "warn"),
    "cancel": ("Abgebrochen", "warn"),
    "suspend": ("Angehalten", "warn"),
    "discard": ("Verworfen", "warn"),
    "version_delete_failed": ("Alte Versionen nicht gelöscht", "warn"),
    "failed": ("Fehlgeschlagen", "danger"),
    "cksum_failed": ("Prüfsumme falsch", "danger"),
    "dest_missing": ("Ziel nicht gefunden", "danger"),
    "failed_checking": ("Prüfung fehlgeschlagen", "danger"),
}
# Ergebnisse, die eine Meldung wert sind -> Satz; Abbrechen und Anhalten macht man meist selbst.
FAILED = {
    "partial": "Der letzte Lauf hat nicht alles gesichert.",
    "version_delete_failed": "Alte Versionen ließen sich nicht löschen, das Ziel kann volllaufen.",
    "failed": "Der letzte Lauf ist fehlgeschlagen.",
    "cksum_failed": "Beim letzten Lauf stimmte eine Prüfsumme nicht, die Sicherung ist womöglich beschädigt.",
    "dest_missing": "Das Ziel der Sicherung fehlt oder ist nicht erreichbar.",
    "failed_checking": "Die Prüfung der Sicherung ist fehlgeschlagen.",
}
# Davon nur eine Warnung, kein kritischer Fehler: Gesichert wurde trotzdem.
MILD_FAILURES = frozenset({"partial", "version_delete_failed"})

# Zustand der Aufgabe (`state`), in dem sie nicht mehr sichern kann -> Satz.
BROKEN_STATES = {
    "unauth": "Das Ziel verlangt eine neue Anmeldung.",
    "broken": "Die Sicherung am Ziel ist beschädigt.",
    "error_detect": "Hyper Backup hat am Ziel einen Fehler gefunden.",
    "relinkable": "Die Aufgabe muss in Hyper Backup neu mit dem Ziel verbunden werden.",
    "endofservice": "Der Dienst am Ziel wird nicht mehr angeboten.",
    "restore_only": "Die Aufgabe kann nur noch wiederherstellen, nicht mehr sichern.",
}

# Ein geplanter Lauf gilt als ausgeblieben, wenn sein Termin so lange vorbei ist.
MISSED_AFTER = timedelta(hours=6)
# Ohne die Uhr der Synology zählt die eigene; die Zeitzonen können so weit auseinanderliegen.
UNKNOWN_CLOCK_SLACK = timedelta(hours=14)
# Ohne Erfolg seit zwei Abständen zwischen den Läufen (plus diesem Puffer) ist eine Aufgabe überfällig.
STALE_GRACE = timedelta(hours=6)
MIN_INTERVAL, MAX_INTERVAL = timedelta(hours=1), timedelta(days=35)


def date_text(moment: datetime) -> str:
    return moment.strftime("%d.%m.%Y, %H:%M")


def is_running(status: BackupStatus) -> bool:
    return (status.status not in (None, "none")) or status.last_result == "backingup"


def state_problem(status: BackupStatus) -> str | None:
    return BROKEN_STATES.get((status.state or "").lower())


def failure_text(status: BackupStatus) -> str | None:
    """Satz zu einem gescheiterten letzten Lauf, mit dessen Zeit; `None`, wenn er nicht gescheitert ist oder läuft."""
    text = None if is_running(status) else FAILED.get((status.last_result or "").lower())
    if text is None or status.last_run is None:
        return text
    return f"{text} (Lauf vom {date_text(status.last_run)})"


def overdue_reason(status: BackupStatus, now: datetime, slack: timedelta = timedelta(0)) -> str | None:
    """Warum eine Aufgabe überfällig ist; `None`, wenn sie es nicht ist oder gerade läuft. `slack` verlängert die Fristen."""
    if is_running(status):
        return None
    if status.next_run is not None and now - status.next_run > MISSED_AFTER + slack:
        return f"Der geplante Lauf vom {date_text(status.next_run)} hat nicht stattgefunden."
    if status.next_run is None or status.last_run is None or status.last_success is None:
        return None
    interval = status.next_run - status.last_run
    if not MIN_INTERVAL <= interval <= MAX_INTERVAL:
        return None
    if now - status.last_success > 2 * interval + STALE_GRACE + slack:
        return f"Der letzte erfolgreiche Lauf war am {date_text(status.last_success)}."
    return None


def build_task(entry: TaskEntry, status: BackupStatus | None, now: datetime, slack: timedelta = timedelta(0)) -> BackupTask:
    if status is None:
        return BackupTask(
            id=entry.id, name=entry.name, state=None, running=False, status_read=False, last_result=None,
            result_label="Stand unbekannt",
            tone="neutral", last_run=None, last_success=None, next_run=None, overdue=False,
            problem="Der Stand dieser Aufgabe ließ sich nicht lesen.",
        )
    running = is_running(status)
    label, tone = RESULTS.get((status.last_result or "").lower(), (status.last_result or "Unbekannt", "neutral"))
    reason = overdue_reason(status, now, slack)
    problem = state_problem(status) or failure_text(status) or reason
    if state_problem(status) is not None:
        tone = "danger"
    elif reason is not None and tone in ("good", "neutral"):
        tone = "warn"
    if running:
        label, tone = "Läuft gerade", "neutral"
    return BackupTask(
        id=entry.id, name=entry.name, state=status.state, running=running, last_result=status.last_result,
        result_label=label, tone=tone, last_run=iso(status.last_run), last_success=iso(status.last_success),
        next_run=iso(status.next_run), overdue=reason is not None, problem=problem,
    )
