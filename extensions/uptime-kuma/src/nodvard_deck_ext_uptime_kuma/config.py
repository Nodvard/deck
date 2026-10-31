"""Liest die Verbindungen aus den Einstellungen; ein unbrauchbarer Eintrag bekommt ein `problem`, die übrigen laufen weiter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# Der API-Schlüssel liegt je Verbindung im Tresor unter `uptime-kuma-key:<Kurzname>`.
KEY_LABEL = "uptime-kuma-key"
SETTINGS_HINT = "Unter Einstellungen → Erweiterungen → Uptime Kuma eintragen."

_SLUG = re.compile(r"[a-z0-9-]+")


def key_label(name: str) -> str:
    return f"{KEY_LABEL}:{name}"


def connection_of(label: str) -> str | None:
    """Der Kurzname zu einem Label aus `key_label`, sonst `None`."""
    prefix = f"{KEY_LABEL}:"
    return label.removeprefix(prefix) if label.startswith(prefix) else None


@dataclass(frozen=True)
class ConnectionConfig:
    name: str
    base_url: str | None
    status_page_slug: str
    insecure_tls: bool
    problem: str | None = None  # fester Hinweis; dann gibt es keine Anfrage


UI_PATHS = (
    "dashboard", "manage-status-page", "add-status-page", "status", "status-page", "settings", "maintenance",
    "add-maintenance", "add", "edit", "clone", "list", "setup", "setup-database", "metrics",
)
"""Erste Pfadteile von Seiten der Weboberfläche von Uptime Kuma. Wer die Adresse aus dem Browser kopiert, hat oft eine
davon dabei (`/dashboard`, `/status/<seite>` oder `/settings/…`); die Anfragen des Moduls gehören aber an die
Grundadresse."""


def strip_ui_path(path: str) -> str:
    """Schneidet den Pfad ab dem ersten Teil aus `UI_PATHS` ab, ohne Schrägstrich am Ende. Ein Pfad davor bleibt (z. B.
    `/uptime-kuma` hinter einem Reverse Proxy)."""
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
    # httpx würde Zugangsdaten in der Adresse als Anmeldung senden und den API-Schlüssel überschreiben.
    if "@" in parts.netloc:
        raise ValueError(text)
    # `parts.port` wirft ValueError bei einem Port außerhalb von 0-65535.
    _ = parts.port
    return urlunsplit((parts.scheme, parts.netloc, strip_ui_path(parts.path), "", ""))


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
        slug = str(entry.get("status_page_slug") or "").strip()
        insecure = entry.get("tls_insecure_skip_verify") is True
        problem: str | None = None
        base_url: str | None = None
        if name in seen:
            problem = f"Der Kurzname „{name}“ ist mehrfach vergeben. Jeder Eintrag braucht einen eigenen Kurznamen."
        else:
            try:
                base_url = normalize_base_url(entry.get("base_url"))
            except ValueError:
                problem = (
                    f"Die Adresse von {name} ist ungültig. Sie muss mit http:// oder https:// beginnen und darf "
                    "keinen Benutzernamen und kein Passwort enthalten (den API-Schlüssel trägst du getrennt ein)."
                )
            if problem is None and slug and not _SLUG.fullmatch(slug):
                problem = (
                    f"Der Kurzname der Status-Seite von {name} ist ungültig. Erlaubt sind Kleinbuchstaben, "
                    "Zahlen und Bindestriche, so wie in der Adresse der Status-Seite nach /status/."
                )
        seen.add(name)
        result.append(ConnectionConfig(name, base_url, slug, insecure, problem))
    return result
