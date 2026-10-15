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


@dataclass(frozen=True)
class NpmConfig:
    url: str
    identity: str
    insecure_tls: bool
    notify_expiring: bool


def normalize_url(raw: Any, *, strip_suffixes: tuple[str, ...] = ()) -> str | None:
    """`192.0.2.10` wird `http://192.0.2.10`, `http://npm.example/api/` wird `http://npm.example`.

    `None` bei leer, `ValueError` bei offensichtlich unbrauchbar."""
    text = str(raw or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(text)
    path = parts.path.rstrip("/")
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
        raise NotConfigured(f"Die Adresse des Nginx Proxy Managers „{exc}“ ist ungültig – bitte in den Einstellungen prüfen.") from exc
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
