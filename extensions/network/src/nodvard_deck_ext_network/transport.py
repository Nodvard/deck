"""Gemeinsame HTTP-Helfer fuer die beiden Clients (Pi-hole, Nginx Proxy Manager).

Nur ueber `ctx.http` (Zielpruefung `net.outbound`, TLS-Ausnahme nur mit
`net.outbound.insecure_tls`) -- nie ein eigener `httpx`-Client. Jeder Netzwerkfehler
wird hier in eine verstaendliche deutsche Meldung uebersetzt; Passwoerter, Sitzungen
und Tokens landen nie in einer Meldung (die stehen nur in Headern/Bodies, nie in der
URL).
"""

from __future__ import annotations

from typing import Any, Protocol

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


# Die Schritte, in denen httpx eine Weiterleitung zusammenbaut (siehe `came_from_redirect`).
_REDIRECT_STEPS = frozenset(
    {"_build_redirect_request", "_redirect_url", "_redirect_method", "_redirect_headers", "_redirect_stream"}
)


def came_from_redirect(exc: BaseException) -> bool:
    """Ob `exc` beim Zusammenbauen einer Weiterleitung entstand. httpx baut die Weiterleitung auch
    dann, wenn es ihr nicht folgt, und scheitert dabei an einem kaputten `Location`-Header (z. B. an
    einem ungueltigen Punycode-Namen). Erkannt an der Stelle im Traceback, ersatzweise am Wort
    "location" im Text. Beides waehlt nur zwischen festen Saetzen: der Text der Ausnahme selbst kommt
    nie in eine Meldung."""
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_name in _REDIRECT_STEPS:
            return True
        tb = tb.tb_next
    return "location" in str(exc).lower()


def own_url_ok(url: str) -> bool:
    try:
        _ = httpx.URL(url).host  # `.host` entschluesselt Punycode und scheitert an einem kaputten Namen
    except (httpx.InvalidURL, ValueError):
        return False
    return True


def _protocol_text(exc: BaseException, service: str) -> str:
    if came_from_redirect(exc):
        return redirect_text(service)
    if isinstance(exc, httpx.LocalProtocolError):
        return f"Die Anfrage an {service} ließ sich nicht senden. Prüfe die Einstellungen."
    return (
        f"{service} hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt. "
        "Prüfe die Adresse in den Einstellungen (http:// oder https://, Port)."
    )


def unusable_exchange_text(exc: BaseException, url: str, service: str) -> str:
    """Fester Satz fuer `httpx.ProtocolError`, `httpx.InvalidURL` und `ValueError` -- nie ihr Text.
    h11 zitiert kaputte Zeilen der Antwort (`illegal header line: bytearray(b'Location: ...')`) und
    bei der eigenen Anfrage den kaputten Header samt Wert; ein ungueltiger Punycode-Name in der
    Weiterleitung nennt Teile des fremden Namens."""
    if isinstance(exc, httpx.ProtocolError):
        return _protocol_text(exc, service)
    if not own_url_ok(url):
        return f"{service}: Die eingetragene Adresse ist ungültig. Bitte in den Einstellungen prüfen."
    if came_from_redirect(exc):
        return redirect_text(service)
    return f"Die Anfrage an {service} ließ sich nicht senden. Prüfe die Einstellungen."


def describe_transport_error(exc: BaseException, service: str) -> str:
    text = str(exc)
    if isinstance(exc, httpx.ProtocolError):
        return _protocol_text(exc, service)
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
    problem: str
    try:
        return await http.request(method, url, insecure_tls=insecure_tls, timeout=timeout_s, **kwargs)
    except NodvardError as exc:
        # z. B. PermissionDenied von ctx.http (Ziel oder TLS-Ausnahme nicht erlaubt).
        raise error_cls(f"{service}: Verbindung vom Dashboard nicht erlaubt ({_short(str(exc))}).") from exc
    except (httpx.ProtocolError, httpx.InvalidURL, ValueError) as exc:
        # Kaputte Antwort, unbrauchbare Weiterleitung oder unbrauchbare eigene Adresse. `InvalidURL`
        # und die Punycode-Fehler (`ValueError`) wirft httpx ausserhalb von `HTTPError`.
        problem = unusable_exchange_text(exc, url, service)
    except (httpx.HTTPError, OSError) as exc:
        raise error_cls(describe_transport_error(exc, service)) from exc
    # Ausserhalb des `except`-Blocks: sonst hinge die Ausnahme von httpx (mit Teilen der fremden
    # Antwort im Text) als `__context__` an der Meldung, und wer die Ursachenkette abschreibt, truege sie mit.
    raise error_cls(problem)


def json_body(response: Any) -> Any:
    try:
        return response.json()
    except (ValueError, RecursionError):
        # Kaputter oder absichtlich tief verschachtelter Koerper: wie "kein JSON" behandeln.
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


def redirect_text(service: str, status_code: int | None = None) -> str:
    """Der Satz fuer jede Weiterleitung. Der `Location`-Header kommt vom fremden Server und
    steht darum nirgends im Text, auch nicht als Adressvorschlag: ein Angreifer koennte sich
    sonst eine Adresse aussuchen, die der Nutzer dann in die Einstellungen uebernimmt."""
    code = f" (HTTP {status_code})" if status_code else ""
    return (
        f"{service} leitet auf eine andere Adresse um{code}. "
        "Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
        "Meist ist http:// statt https:// eingetragen (oder umgekehrt), oder ein Proxy davor leitet um. "
        "Trage in den Einstellungen die endgültige Adresse ein."
    )


def redirect_message(response: Any, service: str) -> str | None:
    """Satz fuer eine 3xx-Antwort, sonst `None`. Eine Weiterleitung ist nie ein Erfolg:
    `ctx.http` folgt ihr bewusst nicht -- sonst ginge das Passwort an ein Ziel, das niemand
    eingetragen hat."""
    if not 300 <= response.status_code < 400:
        return None
    return redirect_text(service, response.status_code)


def raise_if_redirect(response: Any, service: str, error_cls: type[ServiceError]) -> None:
    message = redirect_message(response, service)
    if message:
        raise error_cls(message)


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
