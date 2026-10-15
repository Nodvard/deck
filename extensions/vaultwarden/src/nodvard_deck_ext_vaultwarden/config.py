"""Einstellungen von Vaultwarden: nur die Adresse und die TLS-Ausnahme, kein Passwort und kein Token."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SETTINGS_PATH = "/settings/extensions/vaultwarden"
SETTINGS_HINT = "Unter Einstellungen → Erweiterungen → Vaultwarden die Adresse eintragen."


class NotConfigured(Exception):
    """Vaultwarden nicht (gültig) eingetragen; `str(exc)` sagt auf Deutsch, was fehlt."""


class CredentialsInUrl(ValueError):
    """Benutzername oder Passwort stehen in der Adresse (`name:passwort@`)."""


@dataclass(frozen=True)
class VaultwardenConfig:
    url: str
    insecure_tls: bool

    @property
    def https(self) -> bool:
        return self.url.startswith("https://")


def normalize_url(raw: Any) -> str | None:
    """`192.0.2.20:8222` wird `http://192.0.2.20:8222`, `https://vault.example.org/#/login` wird
    `https://vault.example.org`. `None` bei leer, `CredentialsInUrl` bei `name:passwort@`, sonst `ValueError` bei
    offensichtlich unbrauchbar. Die Ausnahmen tragen den Eingabetext nicht, er könnte ein Passwort enthalten."""
    text = str(raw or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "http://" + text
    try:
        parts = urlsplit(text)
        hostname = parts.hostname
    except ValueError:
        raise ValueError("unbrauchbare Adresse") from None
    # Die Adresse landet in der Übersicht, der Kachel und dem Vorschlag auf der Apps-Seite; ein Passwort darin
    # sähe jeder mit Leserecht. Vaultwarden braucht hier ohnehin keine Anmeldung.
    if "@" in parts.netloc:
        raise CredentialsInUrl("Benutzername oder Passwort in der Adresse")
    if parts.scheme not in ("http", "https") or not hostname:
        raise ValueError("unbrauchbare Adresse")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def vaultwarden_config(settings: dict[str, Any]) -> VaultwardenConfig:
    try:
        url = normalize_url(settings.get("url"))
    except CredentialsInUrl:
        raise NotConfigured(
            "Die Adresse enthält einen Benutzernamen oder ein Passwort. Bitte nur die Adresse eintragen, "
            "ohne „name:passwort@“."
        ) from None
    except ValueError:
        raise NotConfigured(
            "Die Adresse ist ungültig (erwartet http:// oder https:// mit Rechnername) – "
            "bitte in den Einstellungen prüfen."
        ) from None
    if url is None:
        raise NotConfigured(f"Vaultwarden ist noch nicht eingerichtet. {SETTINGS_HINT}")
    return VaultwardenConfig(url=url, insecure_tls=bool(settings.get("tls_insecure_skip_verify", False)))
