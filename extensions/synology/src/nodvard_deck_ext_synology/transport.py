"""Schickt eine Anfrage an DSM und macht aus jedem Fehler einen festen deutschen Satz."""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from nodvard_sdk.errors import CertificateMismatch, NodvardError

SERVICE = "Synology DSM"

DEFAULT_TIMEOUT_S = 10.0  # je Lese- und Verbindungsschritt (httpx)
# Gesamtfrist: ein Server, der Byte für Byte tropft, hielte sonst ewig.
TOTAL_TIMEOUT_S = 30.0
MAX_SMALL_BYTES = 1024 * 1024  # Schnittstellenliste, Anmeldung, Geräteinfo, Auslastung, Update
MAX_STORAGE_BYTES = 4 * 1024 * 1024  # `load_info`: Volumes, Pools und Festplatten eines großen Geräts

_MIB = 1024 * 1024

# Ein eigener Cookie statt keinem, sonst hängt httpx einen aus dem gemeinsamen Speicher an.
_NO_SESSION = "nodvard_deck=1"


class HttpLike(Protocol):
    def stream(
        self, method: str, url: str, *, insecure_tls: bool = False, **kwargs: Any
    ) -> AbstractAsyncContextManager[Any]: ...


class DsmError(Exception):
    """`state` ist der Maschinenwert für Seite und Kachel, `kind` die Art eines Teils (Info, Auslastung, Speicher,
    Update), `str(exc)` ein deutscher Satz."""

    state = "error"
    kind = "error"


class Unreachable(DsmError):
    """Zeitüberschreitung, Verbindung abgelehnt, Name nicht auflösbar."""

    state = "unreachable"


class AuthFailed(DsmError):
    """DSM hat die Anmeldung abgelehnt oder die Adresse gesperrt."""

    state = "auth_failed"


class NoRights(DsmError):
    """Das Konto darf diesen Teil nicht lesen (Fehler 105)."""

    kind = "no_rights"


class Unavailable(DsmError):
    """Dieses DSM kennt die Schnittstelle, Methode oder Version nicht (Fehler 102 bis 104)."""

    kind = "unavailable"


class ResponseTooLarge(DsmError):
    """Die Antwort überschreitet die Grenze; gelesen wird sie nicht."""


@dataclass(frozen=True)
class Reply:
    status_code: int
    body: bytes


# --- Protokoll: die Sitzungs-ID steht bei DSM in der Adresse (`_sid=`), darum nie in einem Log.
_SID = re.compile(r"(_sid=)[^&\s\"']+")
REDACTED = "…"


def redact_sid(text: str) -> str:
    return _SID.sub(rf"\1{REDACTED}", text)


class SidRedactFilter(logging.Filter):
    """Kürzt `_sid=...` in Zeilen von httpx (`HTTP Request: GET <Adresse> ...` steht schon bei INFO im Protokoll)."""

    nodvard_synology = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str) and "_sid=" in record.msg:
                record.msg = redact_sid(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(
                    redact_sid(str(arg)) if isinstance(arg, (str, httpx.URL)) and "_sid=" in str(arg) else arg
                    for arg in record.args
                )
        except Exception:  # noqa: BLE001 - ein Filter darf nie das Protokollieren verhindern
            return True
        return True


def install_log_filter() -> None:
    """Idempotent; der Filter hängt am Logger von httpx und gilt für jeden Handler."""
    logger = logging.getLogger("httpx")
    if not any(getattr(f, "nodvard_synology", False) for f in logger.filters):
        logger.addFilter(SidRedactFilter())


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


def redirect_text(status_code: int | None = None) -> str:
    """Der Satz für jede Weiterleitung; die Zieladresse steht bewusst nirgends im Text."""
    code = f" (HTTP {status_code})" if status_code else ""
    return (
        f"{SERVICE} leitet auf eine andere Adresse um{code}. "
        "Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
        "Meist ist http:// statt https:// eingetragen (oder umgekehrt): DSM nutzt Port 5000 für http und 5001 für "
        "https, und leitet oft von der einen auf die andere Adresse um. Trage in den Einstellungen die endgültige "
        "Adresse ein."
    )


def certificate_changed_text() -> str:
    return (
        f"{SERVICE} zeigt ein anderes Zertifikat als erwartet. Es wurde nichts gesendet, auch nicht das Passwort. "
        "Prüfe die Adresse in den Einstellungen."
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
    return any(isinstance(link, CertificateMismatch) for link in _chain(exc))


def _own_url_ok(url: str) -> bool:
    try:
        _ = httpx.URL(url).host  # `.host` entschlüsselt Punycode und scheitert an einem kaputten Namen
    except (httpx.InvalidURL, ValueError):
        return False
    return True


def _unusable_exchange_text(exc: BaseException, url: str) -> str:
    """Fester Satz für kaputte Antworten und Adressen, nie der Text der Ausnahme (h11 zitiert kaputte Zeilen, und die
    Adresse enthält die Sitzung)."""
    if came_from_redirect(exc):
        return redirect_text()
    if isinstance(exc, (httpx.ProtocolError, httpx.DecodingError)):
        return (
            f"{SERVICE} hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt. "
            "Prüfe die Adresse in den Einstellungen (http:// oder https://, Port; DSM nutzt meist 5000 bzw. 5001)."
        )
    if not _own_url_ok(url):
        return f"{SERVICE}: Die eingetragene Adresse ist ungültig. Bitte in den Einstellungen prüfen."
    return f"Die Anfrage an {SERVICE} ließ sich nicht senden. Prüfe die Einstellungen."


_DNS_MARKERS = ("name or service not known", "getaddrinfo", "nodename nor servname", "no address associated", "name resolution")
_REFUSED_MARKERS = ("connection refused", "errno 111", "[winerror 10061]")
_CERT_MARKERS = ("certificate_verify_failed", "certificate verify failed", "self-signed", "self signed", "hostname mismatch")
_TLS_MISMATCH_MARKERS = ("wrong_version_number", "unknown protocol", "http request", "record layer failure")


def timeout_text() -> str:
    return f"{SERVICE} antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port und ob die Synology läuft."


def _transport_error(exc: BaseException) -> DsmError:
    """Fester Satz zu einem Verbindungsfehler; der Text der Ausnahme wählt nur zwischen den Sätzen."""
    chain = _chain(exc)
    texts = [str(link).lower() for link in chain]
    if any(marker in text for text in texts for marker in _CERT_MARKERS):
        return DsmError(
            f"{SERVICE}: Das Zertifikat wurde nicht akzeptiert. DSM hat oft ein eigenes, selbst erstelltes Zertifikat. "
            "Schalte in den Einstellungen „Selbstsigniertes Zertifikat erlauben“ ein (am besten nur im eigenen Netz)."
        )
    if any(marker in text for text in texts for marker in _TLS_MISMATCH_MARKERS):
        return DsmError(
            f"{SERVICE}: Die Verschlüsselung passt nicht zum Port. DSM nutzt Port 5001 für https und 5000 für http; "
            "trage in den Einstellungen die passende Adresse ein."
        )
    if any(isinstance(link, (httpx.TimeoutException, TimeoutError)) for link in chain):
        return Unreachable(timeout_text())
    if any(marker in text for text in texts for marker in _DNS_MARKERS):
        return Unreachable(
            f"Der Name in der Adresse von {SERVICE} lässt sich nicht auflösen. Prüfe die Schreibweise und das Netzwerk."
        )
    if any(marker in text for text in texts for marker in _REFUSED_MARKERS):
        return Unreachable(
            f"{SERVICE} lehnt die Verbindung ab. Läuft die Synology, und stimmen Adresse und Port (meist 5000 oder 5001)?"
        )
    return Unreachable(f"{SERVICE} ist nicht erreichbar. Prüfe Adresse und Port und ob die Synology läuft.")


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
    insecure_tls: bool = False,
    pin: str | None = None,
    form: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    total_timeout_s: float = TOTAL_TIMEOUT_S,
    max_bytes: int = MAX_SMALL_BYTES,
) -> Reply:
    """Anfrage ohne Weiterleitung (3xx ist ein Fehler); mit `pin` gilt nur dieses Zertifikat. Wirft nur `DsmError`;
    die Adresse kann die Sitzung enthalten und kommt darum in keine Meldung."""
    # Nur über `ctx.http` (Zielprüfung), nie ein eigener httpx-Client; Weiterleitungen folgt es nicht.
    headers = {"Accept": "application/json", "Cookie": _NO_SESSION}
    body: dict[str, Any] = {} if form is None else {"data": form}
    tls: dict[str, Any] = {"pinned_sha256": pin} if pin else {"insecure_tls": insecure_tls}
    problem: DsmError
    try:
        async with asyncio.timeout(total_timeout_s):
            async with http.stream(method, url, timeout=timeout_s, headers=headers, **tls, **body) as response:
                status = response.status_code
                if 300 <= status < 400:
                    problem = DsmError(redirect_text(status))
                else:
                    return Reply(status, await _read_limited(response, max_bytes))
    except _TooLarge:
        problem = ResponseTooLarge(
            f"{SERVICE} schickt eine ungewöhnlich große Antwort (mehr als {max_bytes // _MIB} MB). "
            "Sie wird nicht gelesen. Prüfe, ob die Adresse wirklich zu DSM führt."
        )
    except (NodvardError, httpx.HTTPError, httpx.InvalidURL, OSError, ValueError) as exc:
        # Auch die Gesamtfrist (`asyncio.timeout` wirft `TimeoutError`, ein `OSError`).
        problem = _classify(exc, url)
    # Außerhalb der `except`-Blöcke, sonst hinge die httpx-Ausnahme (mit Text der fremden Antwort) als `__context__` dran.
    raise problem


def _classify(exc: BaseException, url: str) -> DsmError:
    if certificate_changed(exc):
        return DsmError(certificate_changed_text())
    if isinstance(exc, NodvardError):
        # z. B. PermissionDenied von ctx.http (Ziel oder TLS-Ausnahme nicht erlaubt): fester Text des Kerns.
        return DsmError(f"{SERVICE}: Verbindung vom Dashboard nicht erlaubt ({_short(str(exc))}).")
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return Unreachable(timeout_text())
    if isinstance(exc, (httpx.ProtocolError, httpx.DecodingError, httpx.InvalidURL, UnicodeError)) or (
        isinstance(exc, ValueError) and not isinstance(exc, OSError)
    ):
        # Kaputte Antwort, unbrauchbare Weiterleitung oder unbrauchbare eigene Adresse.
        return DsmError(_unusable_exchange_text(exc, url))
    return _transport_error(exc)
