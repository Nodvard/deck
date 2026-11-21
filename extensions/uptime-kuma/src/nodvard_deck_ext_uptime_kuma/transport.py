"""Schickt eine Anfrage an Uptime Kuma und macht aus jedem Fehler einen festen deutschen Satz."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from nodvard_sdk.errors import NodvardError

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0  # je Lese- und Verbindungsschritt (httpx)
# Gesamtfrist: ein Server, der Byte für Byte tropft, hielte sonst ewig.
TOTAL_TIMEOUT_S = 20.0
MAX_BYTES = 2 * 1024 * 1024  # Kuma-Metriken sind selbst bei hunderten Monitoren nur wenige hundert KB groß


class HttpLike(Protocol):
    def stream(
        self, method: str, url: str, *, insecure_tls: bool = False, **kwargs: Any
    ) -> AbstractAsyncContextManager[Any]: ...


class KumaError(Exception):
    """`state` ist der Maschinenwert für Seite und Kachel, `str(exc)` ein deutscher Satz."""

    state = "error"


class Unreachable(KumaError):
    """Zeitüberschreitung, Verbindung abgelehnt, Name nicht auflösbar."""

    state = "unreachable"


class AuthFailed(KumaError):
    state = "auth_failed"


@dataclass(frozen=True)
class Reply:
    status_code: int
    body: bytes


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# Die Schritte, in denen httpx eine Weiterleitung zusammenbaut (siehe `came_from_redirect`).
_REDIRECT_STEPS = frozenset(
    {"_build_redirect_request", "_redirect_url", "_redirect_method", "_redirect_headers", "_redirect_stream"}
)


def came_from_redirect(exc: BaseException) -> bool:
    """Ob `exc` beim Zusammenbauen einer Weiterleitung entstand (httpx baut sie auch, wenn es ihr nicht folgt)."""
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_name in _REDIRECT_STEPS:
            return True
        tb = tb.tb_next
    return "location" in str(exc).lower()  # ersatzweise am Wort im Text, beides wählt nur zwischen festen Sätzen


def redirect_text(service: str, status_code: int | None = None) -> str:
    """Der Satz für jede Weiterleitung; die Adresse aus dem `Location`-Header steht bewusst nirgends im Text."""
    code = f" (HTTP {status_code})" if status_code else ""
    return (
        f"{service} leitet auf eine andere Adresse um{code}. "
        "Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
        "Meist ist http:// statt https:// eingetragen (oder umgekehrt), oder ein Proxy davor leitet um. "
        "Trage in den Einstellungen die endgültige Adresse ein."
    )


def _own_url_ok(url: str) -> bool:
    try:
        _ = httpx.URL(url).host  # `.host` entschlüsselt Punycode und scheitert an einem kaputten Namen
    except (httpx.InvalidURL, ValueError):
        return False
    return True


def _unusable_exchange_text(exc: BaseException, url: str, service: str) -> str:
    """Fester Satz für `httpx.ProtocolError`, `httpx.InvalidURL` und `ValueError`, nie ihr Text (h11 zitiert kaputte Zeilen)."""
    if came_from_redirect(exc):
        return redirect_text(service)
    if isinstance(exc, httpx.ProtocolError):
        return (
            f"{service} hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt. "
            "Prüfe die Adresse in den Einstellungen (http:// oder https://, Port)."
        )
    if not _own_url_ok(url):
        return f"{service}: Die eingetragene Adresse ist ungültig. Bitte in den Einstellungen prüfen."
    return f"Die Anfrage an {service} ließ sich nicht senden. Prüfe die Einstellungen."


_DNS_MARKERS = ("name or service not known", "getaddrinfo", "nodename nor servname", "no address associated", "name resolution")
_REFUSED_MARKERS = ("connection refused", "errno 111", "[winerror 10061]")
_CERT_MARKERS = ("certificate_verify_failed", "certificate verify failed", "self-signed", "self signed")
_TLS_MARKERS = ("[ssl", "wrong_version_number", "unknown protocol", "record layer failure")


def _transport_error(exc: BaseException, service: str) -> KumaError:
    """Fester Satz je Fehlerart; der Text der Ausnahme wählt nur den Satz und steht nur im Protokoll."""
    # Nur die Art im Protokoll: der Text könnte Adresse oder Schlüssel zitieren.
    log.info("%s: Verbindungsfehler (%s)", service, type(exc).__name__)
    text = str(exc).lower()
    if any(marker in text for marker in _CERT_MARKERS):
        return KumaError(
            f"{service}: Das Zertifikat wurde nicht akzeptiert. Bei einem selbstsignierten Zertifikat in den "
            "Einstellungen „Selbstsigniertes Zertifikat erlauben“ einschalten."
        )
    if isinstance(exc, httpx.TimeoutException):
        return Unreachable(f"{service} antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port und ob der Server läuft.")
    if any(marker in text for marker in _DNS_MARKERS):
        return Unreachable(f"{service}: Der Name in der Adresse lässt sich nicht auflösen. Prüfe die Schreibweise und das Netzwerk.")
    if any(marker in text for marker in _REFUSED_MARKERS):
        return Unreachable(f"{service} lehnt die Verbindung ab. Läuft der Server, und stimmen Adresse und Port?")
    if any(marker in text for marker in _TLS_MARKERS):
        return KumaError(
            f"{service}: Die Verschlüsselung passt nicht zum Port. Läuft der Server ohne https, "
            "trage in den Einstellungen http:// ein (und umgekehrt)."
        )
    return Unreachable(f"{service} ist nicht erreichbar. Prüfe Adresse und Port und ob der Server läuft.")


class _TooLarge(Exception):
    pass


async def _read_limited(response: Any, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise _TooLarge
        chunks.append(chunk)
    return b"".join(chunks)


async def get(
    http: HttpLike,
    url: str,
    *,
    service: str,
    insecure_tls: bool = False,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    total_timeout_s: float = TOTAL_TIMEOUT_S,
    max_bytes: int = MAX_BYTES,
) -> Reply:
    """GET ohne Weiterleitung (eine 3xx-Antwort ist ein Fehler); wirft nur `KumaError`."""
    # Nur über `ctx.http` (Zielprüfung), nie ein eigener httpx-Client.
    problem: KumaError
    try:
        async with asyncio.timeout(total_timeout_s):
            async with http.stream(
                "GET", url, insecure_tls=insecure_tls, timeout=timeout_s, headers=headers or {}
            ) as response:
                status = response.status_code
                if 300 <= status < 400:
                    problem = KumaError(redirect_text(service, status))
                else:
                    return Reply(status, await _read_limited(response, max_bytes))
    except _TooLarge:
        problem = KumaError(
            f"{service} schickt eine ungewöhnlich große Antwort (mehr als {max_bytes // (1024 * 1024)} MB). "
            "Sie wird nicht gelesen. Prüfe, ob die Adresse wirklich zu Uptime Kuma führt."
        )
    except NodvardError as exc:
        # z. B. PermissionDenied von ctx.http (Ziel oder TLS-Ausnahme nicht erlaubt).
        problem = KumaError(f"{service}: Verbindung vom Dashboard nicht erlaubt ({_short(str(exc))}).")
    except TimeoutError:
        # Die Gesamtfrist (`asyncio.timeout`); die von httpx kommt unten als `httpx.TimeoutException`.
        problem = Unreachable(f"{service} antwortet nicht (Zeitüberschreitung).")
    except (httpx.ProtocolError, httpx.InvalidURL, ValueError) as exc:
        # Kaputte Antwort, unbrauchbare Weiterleitung oder unbrauchbare eigene Adresse.
        problem = KumaError(_unusable_exchange_text(exc, url, service))
    except (httpx.HTTPError, OSError) as exc:
        problem = _transport_error(exc, service)
    # Außerhalb der `except`-Blöcke, sonst hinge die httpx-Ausnahme (mit Text der fremden Antwort) als `__context__` dran.
    raise problem
