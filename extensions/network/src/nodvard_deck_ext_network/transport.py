"""Gemeinsame HTTP-Helfer fuer die beiden Clients (Pi-hole, Nginx Proxy Manager).

Nur ueber `ctx.http` (Zielpruefung `net.outbound`, TLS-Ausnahme nur mit
`net.outbound.insecure_tls`) -- nie ein eigener `httpx`-Client. Jeder Netzwerkfehler
wird hier in eine verstaendliche deutsche Meldung uebersetzt; Passwoerter, Sitzungen
und Tokens landen nie in einer Meldung (die stehen nur in Headern/Bodies, nie in der
URL).
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from nodvard_sdk.errors import NodvardError

DEFAULT_TIMEOUT_S = 10.0


class HttpLike(Protocol):
    async def request(self, method: str, url: str, *, insecure_tls: bool = False, **kwargs: Any) -> Any: ...


class ServiceError(Exception):
    """Basis beider Clients. `state` ist der Maschinenwert fuer Seite/Kacheln:
    `unreachable` | `auth_failed` | `unsupported` | `error`."""

    state = "unreachable"


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe_transport_error(exc: BaseException, service: str) -> str:
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text.lower():
        return (
            f"{service}: Das Zertifikat wurde nicht akzeptiert. Bei einem selbstsignierten Zertifikat in den "
            "Einstellungen „Selbstsigniertes Zertifikat erlauben“ einschalten."
        )
    if isinstance(exc, httpx.TimeoutException):
        return f"{service} antwortet nicht (Zeitüberschreitung)."
    detail = _short(text) if text.strip() else type(exc).__name__
    return f"{service} ist nicht erreichbar ({detail})."


async def send(
    http: HttpLike,
    method: str,
    url: str,
    *,
    service: str,
    error_cls: type[ServiceError],
    insecure_tls: bool = False,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    **kwargs: Any,
) -> Any:
    try:
        return await http.request(method, url, insecure_tls=insecure_tls, timeout=timeout_s, **kwargs)
    except NodvardError as exc:
        # z. B. PermissionDenied von ctx.http (Ziel oder TLS-Ausnahme nicht erlaubt).
        raise error_cls(f"{service}: Verbindung vom Dashboard nicht erlaubt ({_short(str(exc))}).") from exc
    except (httpx.HTTPError, OSError) as exc:
        raise error_cls(describe_transport_error(exc, service)) from exc


def json_body(response: Any) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def error_message(response: Any) -> str | None:
    """`{"error": {"message": "..."}}` -- so melden Pi-hole v6 UND Nginx Proxy
    Manager Fehler."""
    body = json_body(response)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return _short(str(err["message"]), 200)
        if isinstance(err, str) and err:
            return _short(err, 200)
    return None


def redirect_message(response: Any, service: str) -> str | None:
    """3xx auf die Anmeldung: meist http statt https (oder umgekehrt) eingetragen.
    `ctx.http` folgt Weiterleitungen bewusst nicht -- sonst ginge das Passwort an ein
    Ziel, das niemand eingetragen hat. Stattdessen sagen, welche Adresse gemeint ist."""
    if not 300 <= response.status_code < 400:
        return None
    headers = getattr(response, "headers", None) or {}
    target = urlsplit(str(headers.get("location") or ""))
    if target.scheme in ("http", "https") and target.netloc:
        return f"{service} leitet auf {target.scheme}://{target.netloc} weiter – bitte diese Adresse in den Einstellungen eintragen."
    return f"{service} leitet auf eine andere Adresse weiter (HTTP {response.status_code}) – bitte die Adresse in den Einstellungen prüfen."


def as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def as_bool(value: Any) -> bool:
    """Aeltere NPM-Versionen liefern 0/1 statt true/false."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)
