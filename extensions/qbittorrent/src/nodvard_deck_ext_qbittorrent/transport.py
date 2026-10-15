"""Schickt eine Anfrage an qBittorrent und macht aus jedem Fehler einen festen deutschen Satz."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from nodvard_sdk.errors import CertificateMismatch, NodvardError

DEFAULT_TIMEOUT_S = 10.0  # je Lese- und Verbindungsschritt (httpx)
# Gesamtfrist: ein Server, der Byte für Byte tropft, hielte sonst ewig.
TOTAL_TIMEOUT_S = 30.0
MAX_SMALL_BYTES = 1024 * 1024  # Version, `transfer/info` und die Anmeldung
MAX_LIST_BYTES = 4 * 1024 * 1024  # `torrents/info`
MAX_SYNC_BYTES = 8 * 1024 * 1024  # `sync/maindata`: enthält alle Torrents

_MIB = 1024 * 1024


class HttpLike(Protocol):
    def stream(
        self, method: str, url: str, *, insecure_tls: bool = False, **kwargs: Any
    ) -> AbstractAsyncContextManager[Any]: ...


class QbtError(Exception):
    """`state` ist der Maschinenwert für Seite und Kachel, `str(exc)` ein deutscher Satz."""

    state = "error"


class Unreachable(QbtError):
    """Zeitüberschreitung, Verbindung abgelehnt, Name nicht auflösbar."""

    state = "unreachable"


class AuthFailed(QbtError):
    """qBittorrent hat die Anmeldung abgelehnt oder die Adresse gesperrt."""

    state = "auth_failed"


class LoginRequired(QbtError):
    """qBittorrent verlangt eine Anmeldung, aber es ist kein Passwort eingetragen."""

    state = "not_configured"


class ResponseTooLarge(QbtError):
    """Die Antwort überschreitet die Grenze; gelesen wird sie nicht."""


@dataclass(frozen=True)
class Reply:
    status_code: int
    body: bytes
    cookies: tuple[str, ...] = ()  # die `Set-Cookie`-Zeilen der Antwort


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# Die Schritte, in denen httpx eine Weiterleitung zusammenbaut (auch wenn es ihr nicht folgt).
_REDIRECT_STEPS = frozenset(
    {"_build_redirect_request", "_redirect_url", "_redirect_method", "_redirect_headers", "_redirect_stream"}
)


def came_from_redirect(exc: BaseException) -> bool:
    """Ob `exc` beim Zusammenbauen einer Weiterleitung entstand."""
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_name in _REDIRECT_STEPS:
            return True
        tb = tb.tb_next
    return "location" in str(exc).lower()  # ersatzweise am Wort im Text, beides wählt nur zwischen festen Sätzen


def redirect_text(service: str, status_code: int | None = None) -> str:
    """Der Satz für jede Weiterleitung; die Zieladresse steht bewusst nirgends im Text."""
    code = f" (HTTP {status_code})" if status_code else ""
    return (
        f"{service} leitet auf eine andere Adresse um{code}. "
        "Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
        "Meist ist http:// statt https:// eingetragen (oder umgekehrt), oder ein Proxy davor leitet um. "
        "Trage in den Einstellungen die endgültige Adresse ein."
    )


def certificate_changed_text(service: str) -> str:
    return (
        f"{service} zeigt ein anderes Zertifikat als das gemerkte (Fingerabdruck). Es wurde nichts gesendet, auch "
        "nicht das Passwort. Hat sich das Zertifikat geändert, vergleiche den neuen Fingerabdruck und trage ihn "
        "in den Einstellungen ein; sonst ist die Verbindung nicht sicher."
    )


def _chain(exc: BaseException) -> list[BaseException]:
    out: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(out) < 6:
        seen.add(id(current))
        out.append(current)
        current = current.__cause__ or current.__context__
    return out


def certificate_changed(exc: BaseException) -> bool:
    """Ob der Server ein anderes Zertifikat zeigt als der gemerkte Fingerabdruck (meist eingepackt)."""
    return any(isinstance(link, CertificateMismatch) for link in _chain(exc))


def _own_url_ok(url: str) -> bool:
    try:
        _ = httpx.URL(url).host  # `.host` entschlüsselt Punycode und scheitert an einem kaputten Namen
    except (httpx.InvalidURL, ValueError):
        return False
    return True


def _unusable_exchange_text(exc: BaseException, url: str, service: str) -> str:
    """Fester Satz für kaputte Antworten und Adressen, nie der Text der Ausnahme (h11 zitiert kaputte Zeilen)."""
    if came_from_redirect(exc):
        return redirect_text(service)
    if isinstance(exc, (httpx.ProtocolError, httpx.DecodingError)):
        return (
            f"{service} hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt. "
            "Prüfe die Adresse in den Einstellungen (http:// oder https://, Port; qBittorrent nutzt meist 8080)."
        )
    if not _own_url_ok(url):
        return f"{service}: Die eingetragene Adresse ist ungültig. Bitte in den Einstellungen prüfen."
    return f"Die Anfrage an {service} ließ sich nicht senden. Prüfe die Einstellungen."


_DNS_MARKERS = ("name or service not known", "getaddrinfo", "nodename nor servname", "no address associated", "name resolution")
_REFUSED_MARKERS = ("connection refused", "errno 111", "[winerror 10061]")
_CERT_MARKERS = ("certificate_verify_failed", "certificate verify failed", "self-signed", "self signed")
_TLS_MISMATCH_MARKERS = ("wrong_version_number", "unknown protocol", "http request", "record layer failure")


def _timeout_text(service: str) -> str:
    return f"{service} antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port und ob qBittorrent läuft."


def _transport_error(exc: BaseException, service: str) -> QbtError:
    """Fester Satz zu einem Verbindungsfehler; der Text der Ausnahme wählt nur zwischen den Sätzen."""
    chain = _chain(exc)
    texts = [str(link).lower() for link in chain]
    if any(marker in text for text in texts for marker in _CERT_MARKERS):
        return QbtError(
            f"{service}: Das Zertifikat wurde nicht akzeptiert. Trage in den Einstellungen den Fingerabdruck des "
            "Zertifikats ein (oder, nur im eigenen Netz, schalte „Selbstsigniertes Zertifikat erlauben“ ein)."
        )
    if any(marker in text for text in texts for marker in _TLS_MISMATCH_MARKERS):
        return QbtError(
            f"{service}: Die Verschlüsselung passt nicht zum Port. Läuft qBittorrent ohne https, trage in den "
            "Einstellungen http:// ein (und umgekehrt)."
        )
    if any(isinstance(link, (httpx.TimeoutException, TimeoutError)) for link in chain):
        return Unreachable(_timeout_text(service))
    if any(marker in text for text in texts for marker in _DNS_MARKERS):
        return Unreachable(
            f"Der Name in der Adresse von {service} lässt sich nicht auflösen. Prüfe die Schreibweise und das Netzwerk."
        )
    if any(marker in text for text in texts for marker in _REFUSED_MARKERS):
        return Unreachable(
            f"{service} lehnt die Verbindung ab. Läuft qBittorrent, und stimmen Adresse und Port (meist 8080)?"
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


async def send(
    http: HttpLike,
    method: str,
    url: str,
    *,
    service: str,
    insecure_tls: bool = False,
    pin: str | None = None,
    headers: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    total_timeout_s: float = TOTAL_TIMEOUT_S,
    max_bytes: int = MAX_SMALL_BYTES,
) -> Reply:
    """Anfrage ohne Weiterleitung (3xx ist ein Fehler); mit `pin` gilt nur dieses Zertifikat, und es wirft nur `QbtError`."""
    # Nur über `ctx.http` (Zielprüfung), nie ein eigener httpx-Client; Weiterleitungen folgt es nicht.
    tls: dict[str, Any] = {"pinned_sha256": pin} if pin else {"insecure_tls": insecure_tls}
    body: dict[str, Any] = {} if form is None else {"data": form}
    problem: QbtError
    try:
        async with asyncio.timeout(total_timeout_s):
            async with http.stream(method, url, timeout=timeout_s, headers=headers or {}, **body, **tls) as response:
                status = response.status_code
                if 300 <= status < 400:
                    problem = QbtError(redirect_text(service, status))
                else:
                    cookies = tuple(response.headers.get_list("set-cookie"))
                    return Reply(status, await _read_limited(response, max_bytes), cookies)
    except _TooLarge:
        problem = ResponseTooLarge(
            f"{service} schickt eine ungewöhnlich große Antwort (mehr als {max_bytes // _MIB} MB). "
            "Sie wird nicht gelesen. Prüfe, ob die Adresse wirklich zu qBittorrent führt."
        )
    except (NodvardError, httpx.HTTPError, httpx.InvalidURL, OSError, ValueError) as exc:
        # Auch die Gesamtfrist (`asyncio.timeout` wirft `TimeoutError`, ein `OSError`).
        problem = _classify(exc, url, service)
    # Außerhalb der `except`-Blöcke, sonst hinge die httpx-Ausnahme (mit Text der fremden Antwort) als `__context__` dran.
    raise problem


def _classify(exc: BaseException, url: str, service: str) -> QbtError:
    if certificate_changed(exc):
        return QbtError(certificate_changed_text(service))
    if isinstance(exc, NodvardError):
        # z. B. PermissionDenied von ctx.http (Ziel oder TLS-Ausnahme nicht erlaubt): fester Text des Kerns.
        return QbtError(f"{service}: Verbindung vom Dashboard nicht erlaubt ({_short(str(exc))}).")
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return Unreachable(_timeout_text(service))
    if isinstance(exc, (httpx.ProtocolError, httpx.DecodingError, httpx.InvalidURL, UnicodeError)) or (
        isinstance(exc, ValueError) and not isinstance(exc, OSError)
    ):
        # Kaputte Antwort, unbrauchbare Weiterleitung oder unbrauchbare eigene Adresse.
        return QbtError(_unusable_exchange_text(exc, url, service))
    return _transport_error(exc, service)
