"""Einstellungen des Nginx Proxy Managers; das Passwort liegt nur im Tresor (`x-secrets`) und wird erst bei der
Anmeldung gelesen."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

NPM_SECRET = "network-npm-password"

SETTINGS_HINT = "Einstellungen → Erweiterungen → Nginx Proxy Manager"


class NotConfigured(Exception):
    """Dienst nicht (vollständig) eingetragen; `str(exc)` sagt auf Deutsch, was fehlt."""


class InvalidSetting(NotConfigured):
    """Etwas ist eingetragen, aber unbrauchbar (z. B. eine ungültige Adresse). Die Übersicht zählt das als
    eingerichtet, damit die Kachel den Fehler zeigt, statt zu verschwinden."""


@dataclass(frozen=True)
class NpmConfig:
    url: str
    identity: str
    insecure_tls: bool
    notify_expiring: bool


UI_PATHS = (
    "login", "users", "audit-log", "settings", "nginx/proxy", "nginx/redirection", "nginx/stream", "nginx/404",
    "nginx/certificates", "nginx/access", "certificates", "access", "logs",
)
"""Seiten der Weboberfläche des Nginx Proxy Managers (`/nginx/proxy`, `/certificates`, `/users`, …). Wer die Adresse aus dem
Browser kopiert, hat oft eine davon dabei; die Anfragen gehören aber an die Grundadresse. Ein Eintrag mit `/` gilt nur,
wenn beide Teile hintereinander stehen (ein Pfad `/nginx` davor bleibt)."""


def strip_ui_path(path: str) -> str:
    """Schneidet den Pfad ab der ersten Seite aus `UI_PATHS` ab, ohne Schrägstrich am Ende. Ein Pfad davor bleibt (z. B.
    `/npm` hinter einem Reverse Proxy), ein Teil wie `users-verwaltung` zählt nicht."""
    parts = path.split("/")
    names = [part.lower() for part in parts]
    for index in range(len(parts)):
        for page in UI_PATHS:
            wanted = page.split("/")
            if names[index : index + len(wanted)] == wanted:
                return "/".join(parts[:index]).rstrip("/")
    return "/".join(parts).rstrip("/")


def normalize_url(raw: Any, *, strip_suffixes: tuple[str, ...] = ()) -> str | None:
    """`192.0.2.10` wird `http://192.0.2.10`, `http://npm.example/nginx/proxy` wird `http://npm.example`.

    `None` bei leer, `ValueError` bei offensichtlich unbrauchbar."""
    text = str(raw or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(text)
    path = strip_ui_path(parts.path)
    changed = True
    while changed:
        changed = False
        for suffix in strip_suffixes:
            if path.lower().endswith(suffix):
                path = path[: -len(suffix)].rstrip("/")
                changed = True
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def npm_config(settings: dict[str, Any]) -> NpmConfig:
    """Ohne Passwortprüfung (die braucht den Tresor, siehe `service.py`)."""
    try:
        url = normalize_url(settings.get("url"), strip_suffixes=("/api",))
    except ValueError as exc:
        raise InvalidSetting(f"Die Adresse des Nginx Proxy Managers „{exc}“ ist ungültig – bitte in den Einstellungen prüfen.") from exc
    identity = str(settings.get("identity") or "").strip()
    if url is None:
        raise NotConfigured(
            "Nginx Proxy Manager ist noch nicht eingerichtet. Adresse, E-Mail-Adresse und Passwort unter "
            f"{SETTINGS_HINT} eintragen."
        )
    if not identity:
        raise NotConfigured(f"Für den Nginx Proxy Manager fehlt noch die E-Mail-Adresse zum Anmelden ({SETTINGS_HINT}).")
    return NpmConfig(
        url=url,
        identity=identity,
        insecure_tls=bool(settings.get("tls_insecure_skip_verify", False)),
        notify_expiring=settings.get("notify_expiring", True) is not False,
    )
