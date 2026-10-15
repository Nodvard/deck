"""Liest die Verbindungen aus den Einstellungen; ein unbrauchbarer Eintrag bekommt ein `problem`, die übrigen laufen weiter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from nodvard_sdk import sha256_fingerprint

# Das Zugangs-Token liegt je Verbindung im Tresor unter `home-assistant-token:<Kurzname>`.
TOKEN_LABEL = "home-assistant-token"
SETTINGS_HINT = "Unter Einstellungen → Erweiterungen → Home Assistant eintragen."

MAX_ENTITIES = 30  # ausgewählte Entitäten je Verbindung, wie im Schema
MAX_FINGERPRINT_INPUT = 200

_NAME = re.compile(r"[A-Za-z0-9_.-]+")
# So schreibt Home Assistant eine Kennung: `<Bereich>.<Name>`, z. B. `sensor.wohnzimmer_temperatur`.
ENTITY_ID = re.compile(r"[a-z_]+\.[a-z0-9_]+")


def token_label(name: str) -> str:
    return f"{TOKEN_LABEL}:{name}"


@dataclass(frozen=True)
class ConnectionConfig:
    name: str
    base_url: str | None
    entities: tuple[str, ...]
    insecure_tls: bool
    pin: str | None = None  # gemerkter Fingerabdruck (`AB:CD:...`), gilt statt des Schalters
    problem: str | None = None  # fester Hinweis; dann gibt es keine Anfrage


def normalize_base_url(raw: Any) -> str:
    """Entfernt den Schrägstrich am Ende; `ValueError`, wenn die Adresse unbrauchbar ist."""
    text = str(raw or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(text)
    # httpx würde Zugangsdaten in der Adresse als Basic-Anmeldung senden und das Token im Header überschreiben.
    if "@" in parts.netloc:
        raise ValueError(text)
    _ = parts.port  # wirft ValueError bei einem Port außerhalb von 0-65535
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def normalize_fingerprint(value: Any) -> str | None:
    """`None` für „nicht eingetragen“, sonst die Schreibweise `AB:CD:...`; `ValueError`, wenn es kein SHA-256-Fingerabdruck ist."""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_FINGERPRINT_INPUT:
        raise ValueError("kein Fingerabdruck")
    if not value.strip():
        return None
    return sha256_fingerprint(value)


def _entity_list(raw: Any, name: str) -> tuple[tuple[str, ...], str | None]:
    """(Kennungen ohne Doppelte in der Reihenfolge der Eingabe, Hinweis wenn etwas nicht stimmt)."""
    if raw is None:
        return (), None
    if not isinstance(raw, list):
        return (), f"Die Werte von {name} sind keine Liste von Kennungen."
    seen: list[str] = []
    for item in raw:
        text = item.strip() if isinstance(item, str) else ""
        if not text:
            continue
        if not ENTITY_ID.fullmatch(text):
            shown = text if len(text) <= 60 else text[:59] + "…"
            return (), (
                f"Die Kennung „{shown}“ bei {name} ist ungültig. So wie in Home Assistant: Kleinbuchstaben, Zahlen "
                "und Unterstriche, mit einem Punkt dazwischen, z. B. sensor.wohnzimmer_temperatur."
            )
        if text not in seen:
            seen.append(text)
    if len(seen) > MAX_ENTITIES:
        return (), f"Bei {name} sind {len(seen)} Werte eingetragen, erlaubt sind höchstens {MAX_ENTITIES}."
    return tuple(seen), None


def read_connections(settings: dict[str, Any]) -> list[ConnectionConfig]:
    raw = settings.get("connections")
    result: list[ConnectionConfig] = []
    seen: set[str] = set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        insecure = entry.get("tls_insecure_skip_verify") is True
        problem: str | None = None
        base_url: str | None = None
        pin: str | None = None
        entities: tuple[str, ...] = ()
        if name in seen:
            problem = f"Der Kurzname „{name}“ ist mehrfach vergeben. Jeder Eintrag braucht einen eigenen Kurznamen."
        elif not _NAME.fullmatch(name):
            problem = (
                f"Der Kurzname „{name[:40]}“ ist ungültig. Erlaubt sind Buchstaben, Zahlen, Punkt, Bindestrich "
                "und Unterstrich – keine Leerzeichen."
            )
        else:
            try:
                base_url = normalize_base_url(entry.get("base_url"))
            except ValueError:
                problem = (
                    f"Die Adresse von {name} ist ungültig. Sie muss mit http:// oder https:// beginnen und darf "
                    "keinen Benutzernamen und kein Passwort enthalten (das Token trägst du getrennt ein)."
                )
            if problem is None:
                # Ein ungültiger Fingerabdruck gilt nie still als „nicht prüfen“, die Verbindung bleibt ein Fehler.
                try:
                    pin = normalize_fingerprint(entry.get("tls_fingerprint_sha256"))
                except ValueError:
                    problem = (
                        f"Der Fingerabdruck des Zertifikats von {name} ist ungültig (64 Zeichen aus 0–9 und A–F, "
                        "gern mit Doppelpunkten). Bis er stimmt, verbindet sich Nodvard Deck nicht mit diesem Server."
                    )
            if problem is None and pin is not None and base_url is not None and not base_url.startswith("https://"):
                problem = (
                    f"Ein Fingerabdruck gilt nur für https://. Die Adresse von {name} beginnt mit http:// – "
                    "trage dort die https-Adresse ein oder lösche den Fingerabdruck."
                )
            if problem is None:
                entities, problem = _entity_list(entry.get("entities"), name)
        seen.add(name)
        result.append(ConnectionConfig(name, base_url, entities, insecure, pin, problem))
    return result
