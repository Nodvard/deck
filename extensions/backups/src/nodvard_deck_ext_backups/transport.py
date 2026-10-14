"""Fehlertexte des Backup-Clients: feste Sätze statt Text aus der Antwort oder der Ausnahme."""

from __future__ import annotations

import unicodedata
from http import HTTPStatus
from typing import Any

import httpx
from nodvard_sdk.errors import CertificateMismatch, PermissionDenied

# Meldungen bestehen nur aus festen Sätzen: Der Text einer HTTP-Ausnahme oder Serverantwort wählt nur den Satz aus, denn
# h11 und httpx zitieren kaputte Antwortzeilen und die eigene Anfrage samt Zugangsdaten. Einzige Ausnahme ist der Grund,
# den Proxmox selbst im JSON einer Fehlerantwort nennt (`message`, `errors`, siehe `reason_from`).
SERVICE = "Proxmox"
MAX_REASON = 200

# Die Schritte, in denen httpx eine Weiterleitung zusammenbaut. httpx baut sie auch dann, wenn es ihr nicht folgt,
# und scheitert dabei an einem kaputten `Location`-Header (z. B. einem ungültigen Punycode-Namen).
_REDIRECT_STEPS = frozenset(
    {"_build_redirect_request", "_redirect_url", "_redirect_method", "_redirect_headers", "_redirect_stream"}
)
# Quellen, deren Ausnahmetexte Teile der fremden Antwort oder der eigenen Anfrage zitieren.
_FOREIGN_TEXT_MODULES = frozenset({"httpx", "httpcore", "h11", "idna"})


def came_from_redirect(exc: BaseException) -> bool:
    """Ob `exc` beim Zusammenbauen einer Weiterleitung entstand, erkannt an der Stelle im Traceback, ersatzweise am
    Wort "location" im Text. Beides wählt nur zwischen festen Sätzen."""
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_name in _REDIRECT_STEPS:
            return True
        tb = tb.tb_next
    return "location" in str(exc).lower()


def own_url_ok(url: str) -> bool:
    try:
        _ = httpx.URL(url).host  # `.host` entschlüsselt Punycode und scheitert an einem kaputten Namen
    except (httpx.InvalidURL, ValueError):
        return False
    return True


def redirect_text(status_code: int | None = None) -> str:
    """Der Satz für jede Weiterleitung. Der `Location`-Header kommt vom fremden Server und steht darum nirgends im
    Text, auch nicht als Adressvorschlag."""
    code = f" (HTTP {status_code})" if status_code else ""
    return (
        f"Der Server hat die Verbindung auf eine andere Adresse umgeleitet{code}. "
        "Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
        "Prüfe die eingetragene Adresse des Servers."
    )


def unexpected_format_text(status_code: int) -> str:
    return f"Die Antwort von {SERVICE} hat nicht das erwartete Format (HTTP {status_code}). Stimmt die Adresse?"


def json_body(response: Any) -> Any:
    """Der JSON-Körper, oder `None`, wenn er kaputt oder absichtlich tief verschachtelt ist."""
    try:
        return response.json()
    except (ValueError, RecursionError):
        return None


def clean_text(value: Any, limit: int = MAX_REASON) -> str | None:
    """Einzeiliger, gekürzter Text ohne Steuer- und Formatzeichen; `None`, wenn nichts bleibt."""
    if not isinstance(value, str):
        return None
    text = "".join(" " if ch in "\r\n\t" else ch for ch in value)
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("C"))
    text = " ".join(text.split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _reason_text(value: Any, limit: int) -> str | None:
    text = clean_text(value, limit)
    if text is None:
        return None
    # Nie HTML und nie eine Adresse mit Schema (`https://...`), die sich dem Nutzer als neue Adresse anbieten
    # könnte. Rechnernamen und `IP:Port` ohne Schema bleiben mit Absicht: echte Gründe von Proxmox nennen sie
    # (z. B. "Can't connect to 192.0.2.10:8006" im Cluster), und der Satz davor sagt, dass der Grund von Proxmox stammt.
    if "<" in text or ">" in text or "://" in text:
        return None
    return text


def proxmox_reason(value: Any, limit: int = MAX_REASON) -> str | None:
    """Ein Text, den Proxmox selbst nennt (etwa der Ausgang einer Aufgabe), für eine Meldung: derselbe Filter wie bei
    den Gründen in Fehlerantworten (gekürzt, einzeilig, kein HTML, keine Adresse mit Schema), sonst `None`."""
    return _reason_text(value, limit)


def _standard_phrase(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return ""


def reason_from(response: Any) -> str | None:
    """Der Grund, den Proxmox in einer Fehlerantwort nennt (`message`, `errors`), sonst `None`. Roher Antworttext
    (HTML einer Proxy-Fehlerseite) kommt nie zurück."""
    # Formen: `{"message": "...", "errors": {"vmid": "..."}}` und, ersatzweise, die Statuszeile
    # (`HTTP/1.1 403 Permission check failed (...)`), wenn ihr Text nicht der Standardtext des Status ist.
    status_code = getattr(response, "status_code", 0)
    phrase = getattr(response, "reason_phrase", None)
    from_status_line = (
        _reason_text(phrase, MAX_REASON)
        if isinstance(phrase, str) and phrase.strip().lower() != _standard_phrase(status_code).lower()
        else None
    )
    text = getattr(response, "text", "")
    body = json_body(response) if not (isinstance(text, str) and len(text) > 65536) else None
    if not isinstance(body, dict):
        return from_status_line
    parts: list[str] = []
    message = _reason_text(body.get("message"), MAX_REASON)
    if message:
        parts.append(message)
    errors = body.get("errors")
    if isinstance(errors, dict):
        for key, value in list(errors.items())[:3]:
            item = _reason_text(f"{key}: {value}" if isinstance(value, str) else None, 80)
            if item:
                parts.append(item)
    if not parts:
        return from_status_line
    return clean_text(" ".join(parts), MAX_REASON)


def status_text(status_code: int, reason: str | None = None) -> str:
    """Fester Satz je Statusbereich, mit Proxmox' eigenem Grund dahinter, falls es einen gibt."""
    if status_code == 401:
        sentence = f"{SERVICE} hat den Zugang abgelehnt. Prüfe Token-ID und Geheimnis in den Einstellungen."
    elif status_code == 403:
        sentence = "Dem Token fehlt ein Recht für diese Abfrage."
    elif status_code == 404:
        sentence = f"Das gibt es dort nicht (mehr), oder die Adresse gehört nicht zu {SERVICE}."
    elif status_code == 400:
        sentence = f"{SERVICE} hat die Angaben nicht angenommen."
    elif status_code < 500:
        sentence = f"{SERVICE} hat die Anfrage abgelehnt."
    elif status_code == 500:
        sentence = f"{SERVICE} meldet einen Fehler."
    else:
        sentence = f"{SERVICE} oder ein Proxy davor meldet einen Fehler."
    if reason:
        return f"{sentence} Grund laut {SERVICE}: {reason.rstrip('.')}."
    return sentence


def http_error_text(method: str, path: str, response: Any) -> str:
    code = int(response.status_code)
    return f"{method} {path} -> HTTP {code}: {status_text(code, reason_from(response))}"


def _class_names(exc: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(exc).__mro__}


CERTIFICATE_CHANGED_TEXT = (
    "Der Server zeigt ein anderes Zertifikat als das gemerkte. Vergleiche den neuen Fingerabdruck in Proxmox "
    "(Knoten → System → Zertifikate) und bestätige ihn unter „Verbindungen verwalten“."
)


def certificate_changed(exc: BaseException) -> bool:
    """Ob `exc` daher kommt, dass der Server ein anderes Zertifikat zeigt als der gemerkte Fingerabdruck
    (`CertificateMismatch`, meist in eine Ausnahme der HTTP-Schicht eingepackt)."""
    return any(isinstance(link, CertificateMismatch) for link in _chain(exc))


def _chain(exc: BaseException) -> list[BaseException]:
    out: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(out) < 6:
        seen.add(id(current))
        out.append(current)
        current = current.__cause__ or current.__context__
    return out


def _status_of(exc: BaseException) -> int | None:
    for candidate in (getattr(exc, "status_code", None), getattr(getattr(exc, "response", None), "status_code", None)):
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


def _is_tls(chain: list[BaseException]) -> bool:
    for link in chain:
        if "SSLError" in _class_names(link) or "SSLCertVerificationError" in _class_names(link):
            return True
        text = str(link).lower()
        if "certificate verify failed" in text or "certificate_verify_failed" in text or "[ssl" in text:
            return True
    return False


def _is_dns(chain: list[BaseException]) -> bool:
    for link in chain:
        if "gaierror" in _class_names(link):
            return True
        text = str(link).lower()
        if any(
            word in text
            for word in ("name or service not known", "getaddrinfo", "name resolution", "nodename nor servname", "no address associated")
        ):
            return True
    return False


def _is_timeout(chain: list[BaseException]) -> bool:
    return any(isinstance(link, (httpx.TimeoutException, TimeoutError)) or "TimeoutError" in _class_names(link) for link in chain)


def transport_text(exc: BaseException, url: str) -> str:
    """Fester Satz zu einer Ausnahme beim Senden einer Anfrage, nie ihr Text."""
    if isinstance(exc, PermissionDenied):
        return str(exc)  # eigener Text des Kerns (welche Berechtigung fehlt), nichts aus der Antwort
    code = _status_of(exc)
    if code is not None and 300 <= code < 400:
        return redirect_text(code)
    if code is not None and code >= 400:
        return f"HTTP {code}: {status_text(code)}"
    if code is not None and 200 <= code < 300:
        return unexpected_format_text(code)
    if isinstance(exc, httpx.LocalProtocolError):
        return f"Die Anfrage an {SERVICE} ließ sich nicht senden. Prüfe die Einstellungen (Adresse, Token-ID, Geheimnis)."
    if isinstance(exc, (httpx.ProtocolError, httpx.DecodingError)):
        if came_from_redirect(exc):
            return redirect_text()
        return (
            f"{SERVICE} hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt. "
            "Prüfe die Adresse in den Einstellungen (https:// und Port 8006)."
        )
    # Ein Zertifikatsfehler (`ssl.SSLCertVerificationError`) ist zugleich ein `ValueError`. Er gehört zur Prüfung
    # der Kette unten, nicht zu den Fehlern einer Adresse.
    if isinstance(exc, (httpx.InvalidURL, ValueError, UnicodeError)) and not isinstance(exc, OSError):
        if not own_url_ok(url):
            return "Die eingetragene Adresse ist ungültig. Bitte in den Einstellungen prüfen."
        if came_from_redirect(exc):
            return redirect_text()
        return f"Die Anfrage an {SERVICE} ließ sich nicht senden. Prüfe die Einstellungen."
    chain = _chain(exc)
    if certificate_changed(exc):
        return CERTIFICATE_CHANGED_TEXT
    if _is_tls(chain):
        return (
            f"Das Zertifikat von {SERVICE} wird nicht akzeptiert. Bei einem selbstsignierten Zertifikat vergleiche "
            "seinen Fingerabdruck und merk ihn dir unter „Verbindungen verwalten“ (Knopf „Zertifikat“)."
        )
    if _is_dns(chain):
        return "Die Adresse ließ sich nicht auflösen (Namensauflösung fehlgeschlagen). Prüfe die Schreibweise der Adresse."
    if _is_timeout(chain):
        return f"{SERVICE} antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port."
    if isinstance(exc, (httpx.NetworkError, OSError)) or any(isinstance(link, OSError) for link in chain):
        return (
            f"{SERVICE} ist nicht erreichbar (Verbindung abgelehnt oder kein Weg dorthin). "
            "Läuft der Server, und stimmen Adresse und Port?"
        )
    return f"Die Verbindung zu {SERVICE} ist fehlgeschlagen ({type(exc).__name__})."


def carries_foreign_text(exc: BaseException) -> bool:
    """Ob der Text von `exc` aus der fremden Antwort oder der eigenen Anfrage stammen kann.

    Solche Ausnahmen bleiben nicht als Ursache an der Meldung hängen."""
    if isinstance(exc, (httpx.ProtocolError, httpx.InvalidURL, httpx.DecodingError)):
        return True
    tb = exc.__traceback__
    while tb is not None:
        if str(tb.tb_frame.f_globals.get("__name__", "")).split(".")[0] in _FOREIGN_TEXT_MODULES and isinstance(
            exc, (ValueError, UnicodeError)
        ):
            return True
        tb = tb.tb_next
    return False
