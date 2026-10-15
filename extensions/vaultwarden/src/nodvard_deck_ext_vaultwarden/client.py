"""Liest Vaultwarden über seine öffentlichen Adressen `/alive`, `/api/config` und `/api/version`.

Ohne Anmeldung: Deck schickt Vaultwarden kein Passwort und kein Token und folgt keiner Weiterleitung. Meldungen sind feste
deutsche Sätze; Text aus der fremden Antwort steht nur in geprüften Feldern (Version, Name, Adresse)."""

from __future__ import annotations

import re
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from nodvard_sdk.errors import NodvardError

SERVICE = "Vaultwarden"
TIMEOUT_S = 10.0

_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,39}$")
_NAME_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z .+_-]{0,59}$")


class HttpLike(Protocol):
    async def request(self, method: str, url: str, *, insecure_tls: bool = False, **kwargs: Any) -> Any: ...


class VaultwardenError(Exception):
    """`str(exc)` ist ein fester deutscher Satz."""


def _text(value: Any, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.match(value) else None


def _address(value: Any) -> str | None:
    """Die Adresse aus `DOMAIN` von Vaultwarden, nur wenn sie wie eine aussieht."""
    if not isinstance(value, str) or len(value) > 200 or any(ch.isspace() for ch in value):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    return value.rstrip("/") if parts.scheme in ("http", "https") and parts.hostname else None


def _json(response: Any) -> Any:
    try:
        return response.json()
    except (ValueError, RecursionError):
        return None


class VaultwardenClient:
    def __init__(self, http: HttpLike, url: str, *, insecure_tls: bool) -> None:
        self._http = http
        self._url = url
        self._insecure_tls = insecure_tls

    async def _get(self, path: str) -> Any:
        problem: str
        try:
            # `ctx.http` prüft das Ziel (`net.outbound`) und erlaubt die TLS-Ausnahme nur mit `net.outbound.insecure_tls`.
            response = await self._http.request(
                "GET", self._url + path, insecure_tls=self._insecure_tls, timeout=TIMEOUT_S,
                headers={"Accept": "application/json"},
            )
        except NodvardError:
            problem = f"{SERVICE}: Verbindung vom Dashboard nicht erlaubt."
        except httpx.TimeoutException:
            problem = f"{SERVICE} antwortet nicht (Zeitüberschreitung)."
        except (httpx.HTTPError, httpx.InvalidURL, OSError, ValueError) as exc:
            text = str(exc).lower()
            if "certificate verify failed" in text or "certificate_verify_failed" in text:
                problem = (
                    f"{SERVICE}: Das Zertifikat wurde nicht akzeptiert. Bei einem selbstsignierten Zertifikat in den "
                    "Einstellungen „Selbstsigniertes Zertifikat erlauben“ einschalten."
                )
            else:
                problem = (
                    f"{SERVICE} ist nicht erreichbar. Prüfe die Adresse in den Einstellungen (http:// oder https://, Port)."
                )
        else:
            if 300 <= response.status_code < 400:
                raise VaultwardenError(
                    f"{SERVICE} leitet auf eine andere Adresse um (HTTP {response.status_code}). Solchen Umleitungen "
                    "folgt Nodvard Deck nicht. Trage in den Einstellungen die endgültige Adresse ein (meist https:// "
                    "statt http://)."
                )
            return response
        # Außerhalb des `except`: sonst hinge die Ausnahme mit Teilen der fremden Antwort an der Meldung.
        raise VaultwardenError(problem)

    async def info(self) -> dict[str, Any]:
        alive = await self._get("/alive")
        if alive.status_code != 200:
            raise VaultwardenError(f"Unter der Adresse antwortet kein Vaultwarden (HTTP {alive.status_code} auf /alive).")
        config = await self._get("/api/config")
        body = _json(config) if config.status_code == 200 else None
        if not isinstance(body, dict) or "version" not in body:
            raise VaultwardenError("Unter der Adresse antwortet kein Vaultwarden (/api/config fehlt oder ist unlesbar).")
        version = await self._get("/api/version")
        server = body.get("server") if isinstance(body.get("server"), dict) else {}
        settings = body.get("settings") if isinstance(body.get("settings"), dict) else {}
        environment = body.get("environment") if isinstance(body.get("environment"), dict) else {}
        closed = settings.get("disableUserRegistration")
        return {
            "version": _text(_json(version), _VERSION_RE) if version.status_code == 200 else None,
            "api_version": _text(body.get("version"), _VERSION_RE),
            "server_name": _text(server.get("name"), _NAME_RE),
            # Ältere Versionen sagen das nicht: dann unbekannt.
            "signups_open": (not closed) if isinstance(closed, bool) else None,
            "domain": _address(environment.get("vault")),
        }
