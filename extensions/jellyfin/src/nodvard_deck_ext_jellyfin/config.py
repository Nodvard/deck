"""Liest die Verbindungen aus den Einstellungen; ein unbrauchbarer Eintrag bekommt ein `problem`, die übrigen laufen weiter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from nodvard_sdk import sha256_fingerprint

# Der API-Schlüssel liegt je Verbindung im Tresor unter `jellyfin-key:<Kurzname>`.
KEY_LABEL = "jellyfin-key"
SETTINGS_HINT = "Unter Einstellungen → Erweiterungen → Jellyfin eintragen."

MAX_FINGERPRINT_INPUT = 200

_NAME = re.compile(r"[A-Za-z0-9_.-]+")


def key_label(name: str) -> str:
    return f"{KEY_LABEL}:{name}"


@dataclass(frozen=True)
class ConnectionConfig:
    name: str
    base_url: str | None
    insecure_tls: bool
    pin: str | None = None  # gemerkter Fingerabdruck (`AB:CD:...`), gilt statt des Schalters
    problem: str | None = None  # fester Hinweis; dann gibt es keine Anfrage


def normalize_base_url(raw: Any) -> str:
    """Entfernt den Schrägstrich am Ende; `ValueError`, wenn die Adresse unbrauchbar ist."""
    text = str(raw or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(text)
    # httpx würde Zugangsdaten in der Adresse als Basic-Anmeldung senden und den Header überschreiben.
    if "@" in parts.netloc:
        raise ValueError(text)
    _ = parts.port  # wirft ValueError bei einem Port außerhalb von 0-65535
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def normalize_fingerprint(value: Any) -> str | None:
    """`None` für „nicht eingetragen“, sonst `AB:CD:...`; `ValueError`, wenn es kein SHA-256-Fingerabdruck ist."""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_FINGERPRINT_INPUT:
        raise ValueError("kein Fingerabdruck")
    if not value.strip():
        return None
    return sha256_fingerprint(value)


def _problem_for(name: str, entry: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """(Adresse, Fingerabdruck, Hinweis) eines Eintrags mit gültigem, noch nicht vergebenem Kurznamen."""
    try:
        base_url = normalize_base_url(entry.get("base_url"))
    except ValueError:
        return None, None, (
            f"Die Adresse von {name} ist ungültig. Sie muss mit http:// oder https:// beginnen und darf "
            "keinen Benutzernamen und kein Passwort enthalten (den API-Schlüssel trägst du getrennt ein)."
        )
    # Ein ungültiger Fingerabdruck gilt nie still als „nicht prüfen“.
    try:
        pin = normalize_fingerprint(entry.get("tls_fingerprint_sha256"))
    except ValueError:
        return base_url, None, (
            f"Der Fingerabdruck des Zertifikats von {name} ist ungültig (64 Zeichen aus 0–9 und A–F, gern mit "
            "Doppelpunkten). Bis er stimmt, verbindet sich Nodvard Deck nicht mit diesem Server."
        )
    if pin is not None and not base_url.startswith("https://"):
        return base_url, pin, (
            f"Ein Fingerabdruck gilt nur für https://. Die Adresse von {name} beginnt mit http:// – "
            "trage dort die https-Adresse ein oder lösche den Fingerabdruck."
        )
    return base_url, pin, None


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
        base_url: str | None = None
        pin: str | None = None
        if name in seen:
            problem: str | None = f"Der Kurzname „{name}“ ist mehrfach vergeben. Jeder Eintrag braucht einen eigenen Kurznamen."
        elif not _NAME.fullmatch(name):
            problem = (
                f"Der Kurzname „{name[:40]}“ ist ungültig. Erlaubt sind Buchstaben, Zahlen, Punkt, Bindestrich "
                "und Unterstrich – keine Leerzeichen."
            )
        else:
            base_url, pin, problem = _problem_for(name, entry)
        seen.add(name)
        result.append(ConnectionConfig(name, base_url, insecure, pin, problem))
    return result
