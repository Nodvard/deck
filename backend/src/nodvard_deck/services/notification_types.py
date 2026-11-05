"""Meldungsarten und Vorgaben für den Push-Versand."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

SETTING_KEY = "notifications.push_types"
Priority = Literal["min", "low", "default", "high", "urgent"]

TYPES = {
    "account.password_attempts": "Passwort wird durchprobiert",
    "account.totp_attempts": "Zwei-Faktor-Code wird durchprobiert",
    "account.recovery_used": "Wiederherstellungs-Code benutzt",
    "account.changed": "Kontozugang geändert",
    "deck.backup_failed": "Deck-Sicherung fehlgeschlagen",
    "deck.security_update": "Deck-Sicherheitsupdate verfügbar",
    "deck.update_result": "Deck-Update abgeschlossen oder abgebrochen",
    "host.down": "Server nicht erreichbar",
    "host.up": "Server wieder erreichbar",
    "secret.soon": "Zugang läuft in höchstens 14 Tagen ab",
    "secret.due": "Zugang läuft in höchstens 3 Tagen ab",
    "secret.expired": "Zugang abgelaufen",
}


class PushPreference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool = True
    priority: Priority | None = None


class NotificationTypeOut(BaseModel):
    id: str
    label: str


def catalog(extensions_dir: Path) -> dict[str, str]:
    types = dict(TYPES)
    for path in sorted(extensions_dir.glob("*/notification-types.json")):
        entries = json.loads(path.read_text(encoding="utf-8"))
        for key, label in entries.items():
            types.setdefault(key, label)
    return types


def defaults(types: dict[str, str] | None = None) -> dict[str, dict]:
    return {key: PushPreference().model_dump() for key in (types if types is not None else TYPES)}


def with_defaults(stored: dict | None, types: dict[str, str]) -> dict[str, dict]:
    return defaults(types) | (stored or {})
