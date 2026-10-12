"""Offene Kopien: Wiederherstellungen, die Proxmox begonnen hat, die Nodvard Deck aber noch nicht fertig
eingerichtet hat (Netzwerkkarten trennen, Autostart und Name pruefen, Notiz).

Warum ein Vermerk auf der Platte: Proxmox stellt weiter her, auch wenn die Aktion vorher endet -- Zeitgrenze,
keine Antwort mehr, oder das Dashboard startet neu. Ohne Vermerk bliebe die Kopie dann mit verbundenen
Netzwerkkarten (und, je nach Proxmox-Version, mit dem Autostart des Originals) liegen. Der Vermerk entsteht VOR
dem Anlegen in `data_dir/restore-pending.json`; der Backup-Waechter richtet offene Kopien nach
(`restore.follow_up_open_copies`) und loescht den Vermerk, sobald es nichts mehr zu tun gibt.

Lesen und Schreiben sind synchron und ohne `await` dazwischen -- in der Event-Loop des Dashboards damit nicht
unterbrechbar. Geschrieben wird ueber eine Zwischendatei und `os.replace`, damit ein Absturz mitten im Schreiben
keinen halben Vermerk hinterlaesst.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

FILE_NAME = "restore-pending.json"

ACTIVE: set[str] = set()
"""Kopien, um die sich gerade eine laufende Aktion in diesem Prozess kuemmert -- der Waechter laesst sie aus."""

ANNOUNCED: dict[str, str] = {}
"""Kopien, deren Abschluss-Meldung in diesem Prozess schon raus ist (Schluessel -> Ausgang), solange ihr Vermerk noch
liegt. Auch das Vermerk-Feld `announced` sagt das, aber nur, wenn sich die Datei schreiben liess: Der Speicher haelt
den Stand auch dann, damit eine Meldung nicht in jeder Runde des Waechters noch einmal kommt, wenn der Datenordner
nicht beschreibbar ist."""


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
    """Den Vermerk wegraeumen. True, wenn er danach weg ist; False, wenn er noch liegt (Datei nicht beschreibbar)."""
    data = load(ctx)
    if data.pop(key, None) is None:
        return True
    try:
        _save(ctx, data)
    except OSError:
        return False
    return True
