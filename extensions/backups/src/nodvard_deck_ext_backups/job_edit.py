"""Backup-Job bearbeiten (Roadmap Punkt 1: "Backup-Jobs"): Zeitplan, aktiv, Speicher,
Modus, Aufbewahrung -- ueber das Gate.

WHITELIST wie beim Gast-Editor der proxmox-Extension. Geschrieben wird mit dem `digest`
des gerade gelesenen Jobs. Weniger Aufbewahrung oder Abschalten ist HOHES Risiko:
Proxmox loescht beim naechsten Lauf alles ueber der neuen Grenze -- das laesst sich
nicht zuruecknehmen.
"""

from __future__ import annotations

import re
from typing import Any

KEEPS = ("keep-last", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly")
# Alle Regeln, die Proxmox kennt, in dessen Reihenfolge. keep-hourly und keep-all sind in
# Nodvard Deck nicht editierbar -- sie duerfen beim Schreiben aber auch nicht verschwinden.
_ALL_KEEPS = ("keep-all", "keep-last", "keep-hourly", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly")
FIELDS = ("schedule", "enabled", "storage", "mode", *KEEPS)
LABEL = {
    "schedule": "Zeitplan", "enabled": "Aktiv", "storage": "Speicher", "mode": "Modus",
    "keep-last": "Letzte behalten", "keep-daily": "Tägliche", "keep-weekly": "Wöchentliche",
    "keep-monthly": "Monatliche", "keep-yearly": "Jährliche",
}
MODES = ("snapshot", "suspend", "stop")
_SCHEDULE_RE = re.compile(r"^[A-Za-z0-9 ,:*/.\-]{1,64}$")
_STORAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")


class JobEditError(ValueError):
    """Ungueltige Aenderung -- die Meldung geht so an den Nutzer."""


def parse_prune(value: Any) -> dict[str, int]:
    """`prune-backups` -> {"keep-last": 3, "keep-weekly": 2}. Proxmox liefert ein Objekt
    ({"keep-daily": "7"}, live gesehen, siehe inventory.retention_label), aeltere Wege
    einen String ("keep-last=3,keep-weekly=2") -- beides lesen, sonst sieht das
    Formular ueberall 0 und das Speichern loescht die Regeln still."""
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
        values["keep-all"] = True  # nur Anzeige -- das Formular sperrt dann die Aufbewahrung
    return values


def build_job_update(job: dict[str, Any], changes: Any) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """-> (Parameter fuer PUT /cluster/backup/{id}, echte Aenderungen, destruktiv?)."""
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
        # Nicht editierbare Regeln (keep-hourly) unveraendert mitschicken.
        merged = {**prune, **{k: after[k] for k in KEEPS}}
        keeps = ",".join(f"{k}={merged[k]}" for k in _ALL_KEEPS if merged.get(k))
        if keeps:
            params["prune-backups"] = keeps
        else:
            deletes.append("prune-backups")  # dann gilt die Aufbewahrung des Speichers
    if deletes:
        params["delete"] = ",".join(deletes)
    if job.get("digest"):
        params["digest"] = job["digest"]

    # Weniger aufbewahren loescht beim naechsten Lauf; "0" = keine eigene Regel mehr,
    # dann greift die (unbekannte) Speicher-Regel -- ebenfalls als destruktiv werten.
    # Umgekehrt genauso: ohne eigene Regel galt die des Speichers (oft "alle behalten"),
    # eine neue eigene Regel kann also weniger behalten.
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
    """Neuer Job fuer EINEN Gast (aus "Ohne Backup-Job"). Zeitplan und Speicher sind
    Pflicht, der Rest wie beim Bearbeiten. -> (Parameter fuer POST /cluster/backup, Text)."""
    if not re.fullmatch(r"\d{1,9}", str(vmid)):
        raise JobEditError("Ungültige VMID.")
    if not isinstance(values, dict) or not values.get("schedule") or not values.get("storage"):
        raise JobEditError("Zeitplan und Speicher angeben.")
    # Gegen einen leeren "Job" pruefen -- so gelten exakt dieselben Regeln wie beim Bearbeiten.
    params, _diff, _destructive = build_job_update({"schedule": "", "storage": "", "enabled": 1, "mode": "snapshot"}, values)
    params["vmid"] = str(vmid)
    params.pop("delete", None)
    keeps = parse_prune(params.get("prune-backups"))
    kept = f", behält {', '.join(f'{v}× {LABEL[k].lower()}' for k, v in keeps.items())}" if keeps else ""
    return params, f"Backup-Job für VMID {vmid} angelegt: {params['schedule']} auf {params['storage']}{kept}."

