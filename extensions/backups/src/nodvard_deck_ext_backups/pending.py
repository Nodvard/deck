"""Offene Kopien: Wiederherstellungen, die Proxmox begonnen hat, die Nodvard Deck aber noch nicht fertig eingerichtet hat."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

# Warum ein Vermerk auf der Platte: Proxmox stellt weiter her, auch wenn die Aktion vorher endet (Zeitgrenze, keine
# Antwort, Neustart des Dashboards). Ohne Vermerk bliebe die Kopie mit verbundenen Netzwerkkarten (und je nach
# Proxmox-Version dem Autostart des Originals) liegen. Er entsteht vor dem Anlegen; der Backup-Wächter richtet offene
# Kopien nach (`restore.follow_up_open_copies`) und löscht ihn, sobald nichts mehr zu tun ist.
FILE_NAME = "restore-pending.json"

ACTIVE: set[str] = set()
"""Kopien, um die sich gerade eine laufende Aktion in diesem Prozess kümmert; der Wächter lässt sie aus."""

ANNOUNCED: dict[str, str] = {}
"""Kopien, deren Abschluss-Meldung in diesem Prozess schon raus ist (Schlüssel -> Ausgang), solange ihr Vermerk noch
liegt. Das Vermerk-Feld `announced` sagt das auch, aber nur, wenn sich die Datei schreiben ließ; dieser Speicher
verhindert die Wiederholung in jeder Wächter-Runde bei nicht beschreibbarem Datenordner."""


def _path(ctx: ExtensionContext) -> Path:
    return Path(str(ctx.data_dir)) / FILE_NAME


def load(ctx: ExtensionContext) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(_path(ctx).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}


def _save(ctx: ExtensionContext, data: dict[str, dict[str, Any]]) -> None:
    # Synchron und ohne `await`, in der Event-Loop also nicht unterbrechbar. Über Zwischendatei und `os.replace`,
    # damit ein Absturz mitten im Schreiben keinen halben Vermerk hinterlässt.
    path = _path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{FILE_NAME}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def remember(ctx: ExtensionContext, key: str, record: dict[str, Any]) -> None:
    """Wirft `OSError`, wenn der Vermerk nicht geschrieben werden kann -- dann wird nichts angelegt."""
    data = load(ctx)
    data[key] = record
    _save(ctx, data)


def forget(ctx: ExtensionContext, key: str) -> bool:
    """Räumt den Vermerk weg. True, wenn er danach weg ist; False, wenn er noch liegt (Datei nicht beschreibbar)."""
    data = load(ctx)
    if data.pop(key, None) is None:
        return True
    try:
        _save(ctx, data)
    except OSError:
        return False
    return True
