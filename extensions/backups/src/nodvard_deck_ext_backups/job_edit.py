"""Backup-Jobs bearbeiten (Zeitplan, aktiv, Speicher, Modus, Aufbewahrung) über das Gate, nur für feste Felder."""

from __future__ import annotations

import re
from typing import Any

KEEPS = ("keep-last", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly")
# Alle Regeln, die Proxmox kennt, in dessen Reihenfolge. keep-hourly und keep-all sind in Nodvard Deck nicht
# editierbar, dürfen beim Schreiben aber nicht verschwinden.
_ALL_KEEPS = ("keep-all", "keep-last", "keep-hourly", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly")
FIELDS = ("schedule", "enabled", "storage", "mode", *KEEPS)
LABEL = {
    "schedule": "Zeitplan", "enabled": "Aktiv", "storage": "Speicher", "mode": "Modus",
    "keep-last": "Letzte behalten", "keep-daily": "Tägliche", "keep-weekly": "Wöchentliche",
    "keep-monthly": "Monatliche", "keep-yearly": "Jährliche",
}
MODES = ("snapshot", "suspend", "stop")
# Zeitplan und Speichername gehen als Parameter an Proxmox: kurz und nur mit harmlosen Zeichen.
_SCHEDULE_RE = re.compile(r"^[A-Za-z0-9 ,:*/.\-]{1,64}$")
_STORAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")


class JobEditError(ValueError):
    """Ungültige Änderung; die Meldung geht so an den Nutzer."""


def parse_prune(value: Any) -> dict[str, int]:
    """`prune-backups` -> {"keep-last": 3, "keep-weekly": 2}; liest Objekt und String ("keep-last=3,keep-weekly=2")."""
    # Proxmox liefert je nach Version ein Objekt ({"keep-daily": "7"}, siehe inventory.retention_label) oder einen
    # String. Beides lesen, sonst sieht das Formular überall 0 und Speichern löscht die Regeln still.
    if isinstance(value, dict):
        items = [(str(k), str(v).strip()) for k, v in value.items()]
    else:
        items = [part.strip().partition("=")[::2] for part in str(value or "").split(",")]
    result: dict[str, int] = {}
    for key, number in items:
        key = key.strip()
        if key in _ALL_KEEPS and number.strip().isdigit():
            result[key] = int(number)
    return result


def current_values(job: dict[str, Any]) -> dict[str, Any]:
    prune = parse_prune(job.get("prune-backups"))
    values = {
        "schedule": job.get("schedule") or "",
        "enabled": str(job.get("enabled", 1)) != "0",
        "storage": job.get("storage") or "",
        "mode": job.get("mode") or "snapshot",
        **{k: prune.get(k, 0) for k in KEEPS},
    }
    if prune.get("keep-all"):
        values["keep-all"] = True  # nur Anzeige; das Formular sperrt dann die Aufbewahrung
    return values


def job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    """Der Stand eines Jobs, auf dem das Risiko einer Änderung berechnet wurde (editierbare Werte plus die
    Aufbewahrungsregeln, die Nodvard Deck nicht anzeigt). Steht beim Vorschlag in der Aktion und wird bei der
    Ausführung mit dem frisch gelesenen Job verglichen."""
    return {**{k: v for k, v in current_values(job).items() if k != "keep-all"}, "prune": parse_prune(job.get("prune-backups"))}


# Einstellungen des Jobs, die ein manueller Lauf übernimmt (nur einfache Werte, nie Nutzereingaben).
# `remove` gehört dazu: ein Job mit `remove=0` räumt nie auf, der Nachlauf darf es dann auch nicht (ohne den
# Schlüssel gilt in Proxmox `remove=1`). `tmpdir` (wie `dumpdir`/`script`) nimmt Proxmox nur von `root@pam` an,
# nie von einem API-Token; mitgeschickt würde jeder Nachlauf abgelehnt.
VZDUMP_JOB_KEYS = (
    "storage", "mode", "compress", "notes-template", "bwlimit", "ionice", "zstd", "pigz",
    "stopwait", "lockwait", "protected", "remove",
)
VZDUMP_RETENTION_KEYS = ("prune-backups", "maxfiles")


def run_retention(job: dict[str, Any] | None) -> dict[str, str]:
    """Aufbewahrung für einen manuellen Lauf: die des Jobs, ohne eigene (oder nicht sicher lesbare) Regel `keep-all=1`.

    Sonst räumt Proxmox nach der Regel des Speichers auf und löscht womöglich Sicherungen, die der Job behalten hätte."""
    job = job or {}
    prune = {k: v for k, v in parse_prune(job.get("prune-backups")).items() if v}
    if prune:
        return {"prune-backups": ",".join(f"{k}={prune[k]}" for k in _ALL_KEEPS if k in prune)}
    maxfiles = str(job.get("maxfiles") or "").strip()
    if maxfiles.isdigit() and int(maxfiles) > 0:
        return {"maxfiles": maxfiles}
    return {"prune-backups": "keep-all=1"}


def vzdump_options(job: dict[str, Any] | None) -> dict[str, Any]:
    """Parameter für `POST /nodes/{node}/vzdump` aus einem (frisch gelesenen) Job."""
    options: dict[str, Any] = {}
    for key in VZDUMP_JOB_KEYS:
        value = (job or {}).get(key)
        if isinstance(value, str) and value.strip() or isinstance(value, (int, float)):
            options[key] = value
    options.update(run_retention(job))
    return options


def same_run_options(approved: Any, current: dict[str, Any]) -> bool:
    """Sind die freigegebenen Lauf-Einstellungen (`vzdump_options` beim Vorschlag) noch die des Jobs?
    Verglichen wird als Text: Proxmox liefert Zahlen mal als Zahl, mal als Text."""
    if not isinstance(approved, dict):
        return False

    def normalized(options: dict[str, Any]) -> dict[str, str]:
        return {str(key): str(value).strip() for key, value in options.items()}

    return normalized(approved) == normalized(current)


def retention_note(options: dict[str, Any]) -> str:
    never_removes = str(options.get("remove", "1")).strip().lower() in ("0", "false", "no", "off")
    if never_removes or options.get("prune-backups") == "keep-all=1":
        return "Es wird nichts aufgeräumt."
    return (
        "Aufgeräumt wird nach der Aufbewahrung des Jobs: ältere Sicherungen dieses Gastes auf dem Speicher "
        "können dabei gelöscht werden, auch manuelle und die anderer Jobs."
    )


def build_job_update(job: dict[str, Any], changes: Any) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """-> (Parameter für PUT /cluster/backup/{id}, echte Änderungen, destruktiv?)."""
    if not isinstance(changes, dict) or not changes:
        raise JobEditError("Keine Änderung angegeben.")
    unknown = sorted(set(changes) - set(FIELDS))
    if unknown:
        raise JobEditError(f"Nicht änderbar über Nodvard Deck: {', '.join(unknown)}.")
    prune = parse_prune(job.get("prune-backups"))
    if prune.get("keep-all") and any(k in KEEPS for k in changes):
        raise JobEditError(
            "Dieser Job behält alle Sicherungen. Die Aufbewahrung bitte direkt in Proxmox ändern."
        )
    before = current_values(job)
    after = dict(before)
    for key, raw in changes.items():
        if key == "enabled":
            if not isinstance(raw, bool):
                raise JobEditError("Aktiv: an oder aus erwartet.")
            after[key] = raw
        elif key == "schedule":
            if not isinstance(raw, str) or not _SCHEDULE_RE.match(raw.strip()):
                raise JobEditError("Zeitplan: z. B. \"02:30\", \"sun 03:00\" oder \"*-*-* 22:30\".")
            after[key] = raw.strip()
        elif key == "storage":
            if not isinstance(raw, str) or not _STORAGE_RE.match(raw):
                raise JobEditError("Speicher: Name eines Proxmox-Speichers erwartet.")
            after[key] = raw
        elif key == "mode":
            if raw not in MODES:
                raise JobEditError(f"Modus: {', '.join(MODES)}.")
            after[key] = raw
        else:
            if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 1000:
                raise JobEditError(f"{LABEL[key]}: ganze Zahl von 0 bis 1000.")
            after[key] = raw

    diff = [{"key": k, "label": LABEL[k], "old": before[k], "new": after[k]} for k in FIELDS if after[k] != before[k]]
    if not diff:
        raise JobEditError("Keine Änderung -- die Werte sind bereits so gesetzt.")

    params: dict[str, Any] = {}
    deletes: list[str] = []
    for d in diff:
        key = d["key"]
        if key == "enabled":
            params["enabled"] = 1 if after[key] else 0
        elif key in KEEPS:
            continue
        else:
            params[key] = after[key]
    if any(d["key"] in KEEPS for d in diff):
        # Nicht editierbare Regeln (keep-hourly) unverändert mitschicken.
        merged = {**prune, **{k: after[k] for k in KEEPS}}
        keeps = ",".join(f"{k}={merged[k]}" for k in _ALL_KEEPS if merged.get(k))
        if keeps:
            params["prune-backups"] = keeps
        else:
            deletes.append("prune-backups")  # dann gilt die Aufbewahrung des Speichers
    if deletes:
        params["delete"] = ",".join(deletes)
    if job.get("digest"):
        # Der Digest des gelesenen Jobs verhindert, dass eine zwischenzeitliche Änderung überschrieben wird.
        params["digest"] = job["digest"]

    # Weniger aufbewahren oder abschalten ist hohes Risiko: Proxmox löscht beim nächsten Lauf alles über der
    # neuen Grenze, nicht rückgängig zu machen. "0" heißt keine eigene Regel mehr, dann greift die unbekannte
    # Speicher-Regel, ebenfalls destruktiv. Umgekehrt: ohne eigene Regel galt die des Speichers (oft "alle
    # behalten"), eine neue eigene Regel kann also weniger behalten.
    had_rule = any(prune.get(k) for k in _ALL_KEEPS)
    destructive = (
        (before["enabled"] and not after["enabled"])
        or any(after[k] < before[k] for k in KEEPS if before[k])
        or (not had_rule and any(after[k] for k in KEEPS))
    )
    return params, diff, destructive


def describe(diff: list[dict[str, Any]]) -> str:
    def fmt(key: str, value: Any) -> str:
        if key == "enabled":
            return "an" if value else "aus"
        if key in KEEPS:
            return str(value) if value else "–"
        return str(value) or "–"

    return "Geändert: " + ", ".join(f"{d['label']} {fmt(d['key'], d['old'])} → {fmt(d['key'], d['new'])}" for d in diff) + "."


def build_job_create(vmid: Any, values: Any) -> tuple[dict[str, Any], str]:
    """Neuer Job für einen Gast (aus "Ohne Backup-Job"); Zeitplan und Speicher sind Pflicht, der Rest wie beim
    Bearbeiten. -> (Parameter für POST /cluster/backup, Text)."""
    # Die VMID landet als Parameter bei Proxmox: nur eine kurze Zahl.
    if not re.fullmatch(r"\d{1,9}", str(vmid)):
        raise JobEditError("Ungültige VMID.")
    if not isinstance(values, dict) or not values.get("schedule") or not values.get("storage"):
        raise JobEditError("Zeitplan und Speicher angeben.")
    # Gegen einen leeren "Job" prüfen, damit exakt dieselben Regeln wie beim Bearbeiten gelten.
    params, _diff, _destructive = build_job_update({"schedule": "", "storage": "", "enabled": 1, "mode": "snapshot"}, values)
    params["vmid"] = str(vmid)
    params.pop("delete", None)
    keeps = parse_prune(params.get("prune-backups"))
    kept = f", behält {', '.join(f'{v}× {LABEL[k].lower()}' for k, v in keeps.items())}" if keeps else ""
    return params, f"Backup-Job für VMID {vmid} angelegt: {params['schedule']} auf {params['storage']}{kept}."

