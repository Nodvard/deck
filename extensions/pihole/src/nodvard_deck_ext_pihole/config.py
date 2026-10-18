"""Einstellungen von Pi-hole; das Passwort liegt nur im Tresor (`x-secrets`) und wird erst bei der Anmeldung gelesen."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

PIHOLE_SECRET = "network-pihole-password"
SETTINGS_PATH = "/settings/extensions/pihole"
SETTINGS_HINT = "Unter Einstellungen → Erweiterungen → Pi-hole eintragen."


class NotConfigured(Exception):
    """Pi-hole nicht (vollständig) eingetragen; `str(exc)` sagt auf Deutsch, was fehlt."""


class InvalidSetting(NotConfigured):
    """Etwas ist eingetragen, aber unbrauchbar (z. B. eine ungültige Adresse). Die Übersicht zählt das als
    eingerichtet, damit die Kachel den Fehler zeigt, statt zu verschwinden."""


@dataclass(frozen=True)
class PiholeConfig:
    url: str
    insecure_tls: bool


UI_PATHS = ("admin", "api/docs")
"""Seiten der Weboberfläche von Pi-hole (`/admin/queries`, `/admin/settings/dns`) und der API-Beschreibung. Wer die
Adresse aus dem Browser kopiert, hat oft eine davon dabei; die Anfragen gehören aber an die Grundadresse. Ein Eintrag
mit `/` gilt nur, wenn beide Teile hintereinander stehen."""


def strip_ui_path(path: str) -> str:
    """Schneidet den Pfad ab der ersten Seite aus `UI_PATHS` ab, ohne Schrägstrich am Ende. Ein Pfad davor bleibt (z. B.
    `/pihole` hinter einem Reverse Proxy), ein Teil wie `administration` zählt nicht."""
    parts = path.split("/")
    names = [part.lower() for part in parts]
    for index in range(len(parts)):
        for page in UI_PATHS:
            wanted = page.split("/")
            if names[index : index + len(wanted)] == wanted:
                return "/".join(parts[:index]).rstrip("/")
    return "/".join(parts).rstrip("/")


def normalize_url(raw: Any, *, strip_suffixes: tuple[str, ...] = ()) -> str | None:
    """`192.0.2.10` wird `http://192.0.2.10`, `http://pi.hole/admin/queries` wird `http://pi.hole`.

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


def pihole_config(settings: dict[str, Any]) -> PiholeConfig:
    try:
        url = normalize_url(settings.get("url"), strip_suffixes=("/api",))
    except ValueError as exc:
        raise InvalidSetting(f"Die Pi-hole-Adresse „{exc}“ ist ungültig – bitte in den Einstellungen prüfen.") from exc
    if url is None:
        raise NotConfigured("Pi-hole ist noch nicht eingerichtet. Adresse und Passwort unter Einstellungen → Erweiterungen → Pi-hole eintragen.")
    return PiholeConfig(url=url, insecure_tls=bool(settings.get("tls_insecure_skip_verify", False)))


_DOMAIN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
DOMAIN_HINT = "Bitte eine Domain wie werbung.example.com eintragen (ohne http:// und ohne Pfad)."


def normalize_domain(raw: Any) -> str:
    """Eine Domain für `allow`/`deny` (genau, kein Muster): klein, ohne Punkt am Ende, nur a-z, 0-9, `-` und Punkte,
    mindestens zwei Teile. `ValueError` mit deutschem Satz, sonst."""
    domain = (raw if isinstance(raw, str) else "").strip().lower().removesuffix(".")
    labels = domain.split(".")
    if not 3 <= len(domain) <= 253 or len(labels) < 2:
        raise ValueError(DOMAIN_HINT)
    for label in labels:
        if not 1 <= len(label) <= 63 or not set(label) <= _DOMAIN_CHARS or label[0] == "-" or label[-1] == "-":
            raise ValueError(DOMAIN_HINT)
    return domain
