"""Liest die Antworten der Home-Assistant-REST-API defensiv: jedes Feld darf fehlen, dann ist der Wert `None`."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# Feldnamen nach github.com/home-assistant/core: components/api, core_config.py, core.py, components/update.
MAX_TEXT = 80  # längster Name oder Zustand, den wir von Home Assistant übernehmen
MAX_VERSION = 40
MAX_UNIT = 20
MAX_COLLECTED = 2000  # so viele nicht verfügbare Entitäten und Updates merken wir uns, gezählt wird weiter

UNAVAILABLE = "unavailable"
UNKNOWN = "unknown"
UPDATE_ON = "on"  # bei `update.*` heißt das: ein Update steht bereit

_ENTITY_ID = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
_VERSION = re.compile(r"[0-9][0-9A-Za-z .+_-]{0,38}")


def clean_text(value: Any, limit: int = MAX_TEXT) -> str | None:
    """Kurzer Text ohne Steuerzeichen und Zeilenumbrüche; `None`, wenn es keiner ist oder er leer bleibt."""
    if not isinstance(value, str):
        return None
    text = "".join(" " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch for ch in value[: limit * 4])
    text = " ".join(text.split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _version(value: Any) -> str | None:
    """Versionsnummer der Installation (`2026.10.1`, `2026.11.0b3`); nur harmlose Zeichen, sonst `None`."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if _VERSION.fullmatch(text) else None


def _item_version(value: Any) -> str | None:
    """Version eines Updates: bei Add-ons und Geräten sehr verschieden, darum nur gekürzt."""
    return clean_text(value, MAX_VERSION)


@dataclass(frozen=True)
class HaConfig:
    version: str | None
    location_name: str | None


def parse_config(body: Any) -> HaConfig | None:
    """`GET /api/config` -> Version und Standortname; `None`, wenn es kein Objekt ist."""
    if not isinstance(body, dict):
        return None
    return HaConfig(version=_version(body.get("version")), location_name=clean_text(body.get("location_name")))


def is_status_ok(body: Any) -> bool:
    """Ob die Antwort von `GET /api/` ein Objekt mit einem Text unter `message` ist; der Wortlaut ist egal."""
    return isinstance(body, dict) and isinstance(body.get("message"), str)


@dataclass(frozen=True)
class UpdateEntry:
    entity_id: str
    name: str
    installed_version: str | None
    latest_version: str | None


@dataclass(frozen=True)
class UnavailableEntry:
    entity_id: str
    name: str


@dataclass(frozen=True)
class SelectedState:
    entity_id: str
    name: str
    status: str  # ok, unavailable oder unknown
    state: str | None
    unit: str | None


@dataclass
class ParsedStates:
    total: int = 0
    unavailable_count: int = 0
    unknown_count: int = 0
    update_count: int = 0
    updates: list[UpdateEntry] = field(default_factory=list)
    unavailable: list[UnavailableEntry] = field(default_factory=list)
    selected: dict[str, SelectedState] = field(default_factory=dict)


def parse_states(body: Any, wanted: tuple[str, ...] | frozenset[str] = ()) -> ParsedStates | None:
    """Macht aus `GET /api/states` Zähler, Updates, nicht verfügbare und die gewählten Entitäten (`wanted`).

    `None`, wenn es keine Liste ist; ein Eintrag ohne Kennung oder ohne Zustand als Text fällt weg."""
    if not isinstance(body, list):
        return None
    wanted_set = frozenset(wanted)
    result = ParsedStates()
    for entry in body:
        if not isinstance(entry, dict):
            continue
        entity_id = entry.get("entity_id")
        state = entry.get("state")
        if not isinstance(entity_id, str) or not isinstance(state, str) or not _ENTITY_ID.fullmatch(entity_id):
            continue
        attributes = entry.get("attributes")
        attrs: dict[str, Any] = attributes if isinstance(attributes, dict) else {}
        domain = entity_id.partition(".")[0]
        friendly = clean_text(attrs.get("friendly_name"))
        title = clean_text(attrs.get("title")) if domain == "update" else None
        name = friendly or title or entity_id
        result.total += 1
        if state == UNAVAILABLE:
            result.unavailable_count += 1
            if len(result.unavailable) < MAX_COLLECTED:
                result.unavailable.append(UnavailableEntry(entity_id, name))
        elif state == UNKNOWN:
            result.unknown_count += 1
        elif domain == "update" and state == UPDATE_ON:
            result.update_count += 1
            if len(result.updates) < MAX_COLLECTED:
                result.updates.append(
                    UpdateEntry(entity_id, name, _item_version(attrs.get("installed_version")), _item_version(attrs.get("latest_version")))
                )
        if entity_id in wanted_set:
            status = state if state in (UNAVAILABLE, UNKNOWN) else "ok"
            shown = clean_text(state) if status == "ok" else None
            result.selected[entity_id] = SelectedState(
                entity_id, name, status, shown, clean_text(attrs.get("unit_of_measurement"), MAX_UNIT)
            )
    result.updates.sort(key=lambda u: (u.name.casefold(), u.entity_id))
    result.unavailable.sort(key=lambda u: (u.name.casefold(), u.entity_id))
    return result
