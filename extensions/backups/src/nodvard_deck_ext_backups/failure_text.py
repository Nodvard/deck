"""Backup-Fehler in Klartext: aus dem Protokoll eines fehlgeschlagenen VZDump-Laufs wird eine feste Erklaerung
("Was heisst das?" und "Was kannst du tun?"), auch ohne KI.

Proxmox meldet am Ende eines fehlgeschlagenen Backup-Jobs nur "job errors"; der eigentliche Grund steht weiter oben im
Aufgabenprotokoll, z. B. `ERROR: Backup of VM 100 failed - vma_queue_write: write error - Broken pipe`. `explain()`
sucht die Fehlerzeilen des Gastes heraus und ordnet sie einer Zeile aus `RULES` zu (die erste passende gewinnt). Was
keine Regel kennt, heisst "unknown": dann steht nur die Zeile von Proxmox da.

Alle Saetze stehen in `TEXTS`, mit festen Schluesseln (`backup.failure.<art>`), nicht verstreut im Code -- eine
spaetere englische Oberflaeche uebersetzt nur diese Sammlung. Rein lesend, keine Netzzugriffe.
"""

from __future__ import annotations

import re
from typing import Any

TEXTS: dict[str, dict[str, str]] = {
    "backup.failure.no_space": {
        "what": "Auf dem Sicherungsziel ist kein Platz mehr frei.",
        "todo": "Alte Sicherungen auf dem Ziel von Hand löschen, dann „Erneut versuchen“. Die Aufbewahrung im Backup-Job "
                "räumt erst nach einem gelungenen Lauf auf; weniger aufbewahren hilft also erst beim nächsten Mal.",
    },
    "backup.failure.target_lost": {
        "what": "Die Verbindung zum Sicherungsziel ist mitten im Lauf abgerissen, meist bei einem Netzlaufwerk (NFS oder SMB).",
        "todo": "Prüfen, ob das Netzlaufwerk bzw. der NAS stabil läuft (Netzwerk, Energiesparmodus, Neustarts), dann „Erneut versuchen“.",
    },
    "backup.failure.storage_offline": {
        "what": "Das Sicherungsziel war beim Start nicht erreichbar oder nicht eingehängt.",
        "todo": "In Proxmox unter Rechenzentrum → Speicher nachsehen, ob das Ziel aktiv ist, und ob der NAS läuft.",
    },
    "backup.failure.locked": {
        "what": "Der Gast oder der Knoten war gesperrt, meist weil gerade schon eine Sicherung, ein Snapshot oder eine Migration lief.",
        "todo": "Warten, bis der andere Vorgang fertig ist. Hängt die Sperre fest, in Proxmox beim Gast nachsehen.",
    },
    "backup.failure.pbs_auth": {
        "what": "Der Proxmox Backup Server hat die Anmeldung oder das Schreiben abgelehnt (Zugang, Rechte oder Zertifikat).",
        "todo": "In Proxmox unter Rechenzentrum → Speicher beim Backup-Server Benutzer, Passwort und Fingerabdruck prüfen.",
    },
    "backup.failure.guest_agent": {
        "what": "Der Gast-Agent in der VM hat nicht geantwortet, als die Dateisysteme kurz angehalten werden sollten.",
        "todo": "Prüfen, ob der QEMU-Gast-Agent in der VM installiert ist und läuft. Ihn in den Optionen der VM auszuschalten "
                "geht auch, dann sind die Sicherungen aber nicht mehr ganz sauber (wie nach einem Stromausfall).",
    },
    "backup.failure.vm_timeout": {
        "what": "Die VM hat zu langsam geantwortet, Proxmox hat aufgegeben.",
        "todo": "Später erneut versuchen. Passiert es öfter, ist der Server zur Backup-Zeit vielleicht zu stark ausgelastet.",
    },
    "backup.failure.missing_right": {
        "what": "Dem Zugang, mit dem der Lauf gestartet wurde, fehlt in Proxmox ein Recht. Welches, steht in der Zeile unten.",
        "todo": "In Proxmox unter Rechenzentrum → Berechtigungen das genannte Recht ergänzen (die Liste steht in der Anleitung "
                "zum Proxmox-Zugang).",
    },
    "backup.failure.permission": {
        "what": "Proxmox durfte auf dem Sicherungsziel nicht schreiben oder eine Datei im Gast nicht lesen.",
        "todo": "Die Rechte auf dem Ziel prüfen (bei NFS darf root nicht eingeschränkt sein). Bei Containern ohne Root-Rechte hilft oft ein anderer Sicherungsmodus.",
    },
    "backup.failure.io_error": {
        "what": "Beim Lesen oder Schreiben ist ein Datenträgerfehler aufgetreten – am Sicherungsziel oder an einer Platte des Gastes.",
        "todo": "Den Zustand der Platten prüfen (Proxmox-Seite, Datenträger) und das Sicherungsziel kontrollieren.",
    },
    "backup.failure.guest_missing": {
        "what": "Den Gast gibt es auf diesem Server nicht mehr (gelöscht oder umgezogen).",
        "todo": "Den Backup-Job anpassen und den Gast dort entfernen oder neu auswählen.",
    },
    "backup.failure.aborted": {
        "what": "Der Lauf wurde abgebrochen – von Hand oder weil der Server neu gestartet ist.",
        "todo": "Einfach erneut versuchen. Hat niemand abgebrochen, nachsehen, ob der Server zu der Zeit neu gestartet ist.",
    },
    "backup.failure.unreachable": {
        "what": "Das Sicherungsziel hat nicht geantwortet (Netzwerk oder Gerät aus).",
        "todo": "Prüfen, ob das Gerät mit dem Sicherungsziel läuft und im Netz erreichbar ist.",
    },
    "backup.failure.other_guest": {
        "what": "Dieser Gast wurde in dem Lauf gesichert. Fehlgeschlagen ist ein anderer Gast im selben Backup-Job.",
        "todo": "Beim anderen Gast nachsehen (dort steht der Grund). Für diesen Gast ist nichts zu tun.",
    },
    "backup.failure.unknown": {
        "what": "Für diese Meldung gibt es noch keine Erklärung.",
        "todo": "Die Zeile von Proxmox unten hilft weiter. Den ganzen Lauf findest du in Proxmox unter dem Knoten → Aufgaben.",
    },
}

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("no_space", re.compile(r"no space left on device|ENOSPC|not enough (free )?space|quota exceeded", re.IGNORECASE)),
    ("missing_right", re.compile(r"permission check failed \(/", re.IGNORECASE)),
    ("pbs_auth", re.compile(
        r"authentication fail|invalid credentials|401 unauthori[sz]ed|permission check failed|"
        r"fingerprint|certificate (verify|validation)|ssl handshake", re.IGNORECASE)),
    ("target_lost", re.compile(
        r"broken pipe|connection reset|stale (nfs )?file handle|connection (was )?(lost|closed)|"
        r"transport endpoint is not connected|host is down|(write|read) error - (connection timed out|no route)", re.IGNORECASE)),
    ("storage_offline", re.compile(
        r"storage '[^']*' (is not (online|available)|does not exist|is disabled)|(could not|unable to) activate storage|"
        r"is not mounted|mount error|can't mount|does not exist or is unreachable", re.IGNORECASE)),
    ("locked", re.compile(
        r"can't (lock|acquire lock)|unable to acquire lock|\b(VM|CT) is locked|lock .*got timeout|lock request timeout",
        re.IGNORECASE)),
    ("guest_agent", re.compile(r"guest-fsfreeze|fs-?freeze|guest[- ]agent|qemu-guest-agent", re.IGNORECASE)),
    ("vm_timeout", re.compile(r"qmp command .*(got timeout|timed out)", re.IGNORECASE)),
    ("permission", re.compile(
        r"permission denied|operation not permitted|unable to create temporary directory|read-only file system", re.IGNORECASE)),
    ("io_error", re.compile(r"input/output error|\bI/O error|read error|write error", re.IGNORECASE)),
    ("guest_missing", re.compile(
        r"configuration file .* does not exist|\b(VM|CT|guest) \d+ (does not exist|not found)|no such (VM|guest|container)|"
        r"unable to find (VM|CT|guest) '?\d+", re.IGNORECASE)),
    ("aborted", re.compile(r"interrupted by signal|received interrupt|\baborted\b|unexpected status|\bkilled\b", re.IGNORECASE)),
    ("unreachable", re.compile(
        r"connection refused|no route to host|network is unreachable|timed out|got timeout|name or service not known|"
        r"could not resolve|can't connect|temporary failure in name resolution", re.IGNORECASE)),
)
"""Reihenfolge zaehlt: "write error - No space left" ist Platz, "write error - Broken pipe" oder "- Connection timed out" das
Netz (nicht die Platte), ein Sperren-Timeout ist eine Sperre und kein Netzproblem, ein fehlendes Recht in Proxmox
("permission check failed (/vms/100, VM.Backup)") kein Problem des Backup-Servers."""

RULES_BY_KEY: dict[str, re.Pattern[str]] = dict(RULES)
_NEVER = re.compile(r"(?!)")

QUIET_SECOND_PASS = frozenset({"no_space", "permission", "target_lost", "io_error"})
"""Arten, die auch in einer Zeile OHNE "ERROR" zaehlen (tar meldet "Permission denied" als INFO, die Fehlerzeile sagt
dann nur "exit code 2"). Der Gast-Agent nicht: "skipping guest-agent 'fs-freeze'" steht auch bei Erfolg da."""

RAW_LIMIT = 300

_START_RE = re.compile(r"Starting Backup of VM (\d+)\b")
_FINISHED_RE = re.compile(r"Finished Backup of VM (\d+)\b")
_ERROR_RE = re.compile(r"(^|\s)(TASK )?ERROR:", re.IGNORECASE)
_JOB_ERRORS_RE = re.compile(r"\bjob errors\s*$", re.IGNORECASE)


def _section(lines: list[str], vmid: str) -> tuple[list[str], bool]:
    """Die Zeilen des Gastes in einem Sammel-Job: ab "Starting Backup of VM <vmid>" bis zum naechsten Gast. -> (Zeilen,
    Abschnitt gefunden). Gibt es keine Startzeile fuer ihn, aber fuer andere Gaeste (er wurde uebersprungen, oder das
    Protokoll ist abgeschnitten), nur der Kopf vor dem ersten Gast -- sonst bekaeme er die Fehler der Nachbarn. Ganz ohne
    Startzeile (der Job brach vor dem ersten Gast ab) das ganze Protokoll."""
    starts = [(i, m.group(1)) for i, line in enumerate(lines) if (m := _START_RE.search(line))]
    own = next((i for i, guest in starts if guest == vmid), None)
    if own is None:
        return (lines[: starts[0][0]] if starts else lines), False
    end = next((i for i, _guest in starts if i > own), len(lines))
    return lines[own:end], True


def relevant_lines(lines: list[str]) -> list[str]:
    """Nur die Zeilen, die `explain` braucht (Start und Ende je Gast, Fehlerzeilen, Zeilen der Arten aus
    `QUIET_SECOND_PASS`) -- so viel wird zwischengespeichert, nicht das ganze Protokoll."""
    quiet = [RULES_BY_KEY[key] for key in QUIET_SECOND_PASS]
    return [
        line for line in lines
        if _START_RE.search(line) or _FINISHED_RE.search(line) or _ERROR_RE.search(line) or any(p.search(line) for p in quiet)
    ]


def _classify(lines: list[str], *, allowed: frozenset[str] | None = None) -> str | None:
    for key, pattern in RULES:
        if allowed is not None and key not in allowed:
            continue
        if any(pattern.search(line) for line in lines):
            return key
    return None


def _clean(line: str) -> str:
    line = re.sub(r"^\s*(TASK )?ERROR:\s*", "", line.strip())
    return line if len(line) <= RAW_LIMIT else line[: RAW_LIMIT - 1] + "…"


def explain(vmid: str, task_status: str | None, log_lines: list[str] | None) -> dict[str, Any]:
    """-> `{"key", "what", "todo", "detail"}`. `detail`: die Zeile von Proxmox, die den Fehler am besten beschreibt
    (gekuerzt, ohne "ERROR:"), sonst der Status der Aufgabe. `log_lines`: das Protokoll, `None`, wenn es sich nicht
    lesen liess -- dann zaehlt nur der Status."""
    status = (task_status or "").strip()
    section, found = _section(log_lines or [], str(vmid))
    errors = [line for line in section if _ERROR_RE.search(line)]
    if status and status.lower() not in ("job errors", "ok"):
        errors.append(status)
    own = [line for line in errors if re.search(rf"\b(VM|CT) {re.escape(str(vmid))}\b", line)]
    quiet = [line for line in section if line not in errors]
    finished = any((m := _FINISHED_RE.search(line)) and m.group(1) == str(vmid) for line in section)
    if found and finished and not [line for line in errors if not _JOB_ERRORS_RE.search(line)]:
        # Der eigene Abschnitt endet sauber, "job errors" kommt von einem anderen Gast im selben Lauf.
        text = TEXTS["backup.failure.other_guest"]
        return {"key": "other_guest", "what": text["what"], "todo": text["todo"], "detail": status or None}
    key = _classify(own) or _classify(errors) or _classify(quiet, allowed=QUIET_SECOND_PASS) or "unknown"
    best = next((line for line in own + errors + quiet if RULES_BY_KEY.get(key, _NEVER).search(line)), None)
    detail = best or (own[0] if own else errors[0] if errors else status) or None
    text = TEXTS[f"backup.failure.{key}"]
    return {"key": key, "what": text["what"], "todo": text["todo"], "detail": _clean(detail) if detail else None}

