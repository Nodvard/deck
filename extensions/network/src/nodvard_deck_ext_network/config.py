"""Einstellungen der Netzwerk-Extension lesen -- Pi-hole und Nginx Proxy Manager sind
beide optional. Nichts eingetragen heisst "nicht eingerichtet", nie ein Fehler.

Die Passwoerter liegen ausschliesslich im Vault (Labels unten, siehe `x-secrets` in
settings.schema.json) -- gesetzt ueber die Einstellungsseite des Kerns
(`PUT /extensions/network/secrets`), gelesen nur im Moment der Anmeldung.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

PIHOLE_SECRET = "network-pihole-password"
NPM_SECRET = "network-npm-password"

SETTINGS_PATH = "/settings/extensions/network"


class NotConfigured(Exception):
    """Dienst nicht (vollstaendig) eingetragen -- `str(exc)` ist ein freundlicher
    deutscher Hinweis, was fehlt."""


@dataclass(frozen=True)
class PiholeConfig:
    url: str
    insecure_tls: bool


@dataclass(frozen=True)
class NpmConfig:
    url: str
    identity: str
    insecure_tls: bool
    notify_expiring: bool


def normalize_url(raw: Any, *, strip_suffixes: tuple[str, ...] = ()) -> str | None:
    """`192.168.1.2` -> `http://192.168.1.2`; `http://pi.hole/admin/` ->
    `http://pi.hole`. `None` bei leer; `ValueError` bei offensichtlich unbrauchbar."""
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


def _section(settings: dict[str, Any], key: str) -> dict[str, Any]:
    value = settings.get(key)
    return value if isinstance(value, dict) else {}


def pihole_config(settings: dict[str, Any]) -> PiholeConfig:
    section = _section(settings, "pihole")
    try:
        url = normalize_url(section.get("url"), strip_suffixes=("/admin", "/api"))
    except ValueError as exc:
        raise NotConfigured(f"Die Pi-hole-Adresse „{exc}“ ist ungültig – bitte in den Einstellungen prüfen.") from exc
    if url is None:
        raise NotConfigured("Pi-hole ist noch nicht eingerichtet. Adresse und Passwort unter Einstellungen → Erweiterungen → Netzwerk eintragen.")
    return PiholeConfig(url=url, insecure_tls=bool(section.get("tls_insecure_skip_verify", False)))


def npm_config(settings: dict[str, Any]) -> NpmConfig:
    """Ohne Passwort-Pruefung (das braucht den Vault, siehe `service.py`)."""
    section = _section(settings, "npm")
    try:
        url = normalize_url(section.get("url"), strip_suffixes=("/api",))
    except ValueError as exc:
        raise NotConfigured(f"Die Adresse des Nginx Proxy Managers „{exc}“ ist ungültig – bitte in den Einstellungen prüfen.") from exc
    identity = str(section.get("identity") or "").strip()
    if url is None:
        raise NotConfigured(
            "Nginx Proxy Manager ist noch nicht eingerichtet. Adresse, E-Mail-Adresse und Passwort unter "
            "Einstellungen → Erweiterungen → Netzwerk eintragen."
        )
    if not identity:
        raise NotConfigured("Für den Nginx Proxy Manager fehlt noch die E-Mail-Adresse zum Anmelden (Einstellungen → Erweiterungen → Netzwerk).")
    return NpmConfig(
        url=url,
        identity=identity,
        insecure_tls=bool(section.get("tls_insecure_skip_verify", False)),
        notify_expiring=section.get("notify_expiring", True) is not False,
    )
