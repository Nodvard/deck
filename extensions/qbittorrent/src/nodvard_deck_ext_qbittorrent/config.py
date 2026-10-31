"""Liest die Verbindungen aus den Einstellungen; ein unbrauchbarer Eintrag bekommt ein `problem`, die übrigen laufen weiter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from nodvard_sdk import sha256_fingerprint

# Das Passwort liegt je Verbindung im Tresor unter `qbittorrent-password:<Kurzname>`.
PASSWORD_LABEL = "qbittorrent-password"
SETTINGS_HINT = "Unter Einstellungen → Erweiterungen → qBittorrent eintragen."

MAX_FINGERPRINT_INPUT = 200
MAX_USERNAME = 128

_NAME = re.compile(r"[A-Za-z0-9_.-]+")


def password_label(name: str) -> str:
    return f"{PASSWORD_LABEL}:{name}"


def connection_of(label: str) -> str | None:
    """Der Kurzname zu einem Passwort-Label, sonst `None`."""
    prefix = f"{PASSWORD_LABEL}:"
    return label.removeprefix(prefix) if label.startswith(prefix) else None


@dataclass(frozen=True)
class ConnectionConfig:
    name: str
    base_url: str | None
    username: str
    insecure_tls: bool
    pin: str | None = None  # gemerkter Fingerabdruck (`AB:CD:...`), gilt statt des Schalters
    problem: str | None = None  # fester Hinweis; dann gibt es keine Anfrage


UI_PATHS = ("index.html",)
"""Erste Pfadteile von Seiten der Weboberfläche von qBittorrent. Wer die Adresse aus dem Browser kopiert, hat oft
eine davon dabei (`/index.html`); die Anfragen des Moduls gehören aber an die Grundadresse."""


def strip_ui_path(path: str) -> str:
    """Schneidet den Pfad ab dem ersten Teil aus `UI_PATHS` ab, ohne Schrägstrich am Ende. Ein Pfad davor bleibt (z. B.
    `/qbittorrent` hinter einem Reverse Proxy)."""
    parts = path.split("/")
    for index, part in enumerate(parts):
        if part.lower() in UI_PATHS:
            parts = parts[:index]
            break
    return "/".join(parts).rstrip("/")


def normalize_base_url(raw: Any) -> str:
    """Entfernt den Schrägstrich am Ende und eine Unterseite der Weboberfläche (`strip_ui_path`); `ValueError`, wenn
    die Adresse unbrauchbar ist."""
    text = str(raw or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(text)
    # httpx würde Zugangsdaten in der Adresse als Basic-Anmeldung senden.
    if "@" in parts.netloc:
        raise ValueError(text)
    _ = parts.port  # wirft ValueError bei einem Port außerhalb von 0-65535
    return urlunsplit((parts.scheme, parts.netloc, strip_ui_path(parts.path), "", ""))


def normalize_fingerprint(value: Any) -> str | None:
    """`None` für „nicht eingetragen“, sonst `AB:CD:...`; `ValueError`, wenn es kein SHA-256-Fingerabdruck ist."""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_FINGERPRINT_INPUT:
        raise ValueError("kein Fingerabdruck")
    if not value.strip():
        return None
    return sha256_fingerprint(value)


def normalize_username(value: Any) -> str:
    """Der Benutzername ohne Leerraum am Rand; leer, wenn er fehlt oder unbrauchbar ist."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) > MAX_USERNAME or any(ord(ch) < 32 for ch in text):
        return ""
    return text


def _problem_for(name: str, entry: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """(Adresse, Fingerabdruck, Hinweis) eines Eintrags mit gültigem, noch nicht vergebenem Kurznamen."""
    try:
        base_url = normalize_base_url(entry.get("base_url"))
    except ValueError:
        return None, None, (
            f"Die Adresse von {name} ist ungültig. Sie muss mit http:// oder https:// beginnen und darf "
            "keinen Benutzernamen und kein Passwort enthalten (beides trägst du getrennt ein)."
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
        result.append(ConnectionConfig(name, base_url, normalize_username(entry.get("username")), insecure, pin, problem))
    return result
