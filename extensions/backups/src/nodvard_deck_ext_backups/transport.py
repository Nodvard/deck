"""Fehlertexte des Backup-Clients: feste Saetze statt Text aus der Antwort oder der Ausnahme.

Alles, was in einer Meldung landet (Kacheln, Seiten, Aktionsergebnis, Konsole, Protokoll), kommt von hier
oder aus dem eigenen Code. Der Text einer Ausnahme der HTTP-Schicht oder der Antwort des Servers wird nur
benutzt, um zwischen den Saetzen zu waehlen, und steht nie selbst in einer Meldung: h11 und httpx zitieren
kaputte Zeilen der Antwort (auch einen `Location`-Header), bei der eigenen Anfrage den Header samt Zugangs-
daten, und ein ungueltiger Punycode-Name nennt Teile des fremden Namens.

Einzige Ausnahme ist der Grund, den Proxmox selbst im JSON einer Fehlerantwort nennt (`message`, `errors`,
z. B. welches Recht fehlt): gekuerzt, ohne Steuerzeichen und nur, wenn er weder nach HTML noch nach einer
Adresse mit Schema (`https://...`) aussieht (`reason_from`).
"""

from __future__ import annotations

import unicodedata
from http import HTTPStatus
from typing import Any

import httpx
from nodvard_sdk.errors import PermissionDenied

SERVICE = "Proxmox"
MAX_REASON = 200

# Die Schritte, in denen httpx eine Weiterleitung zusammenbaut. httpx baut sie auch dann, wenn es ihr
# nicht folgt, und scheitert dabei an einem kaputten `Location`-Header (z. B. einem ungueltigen Punycode-Namen).
_REDIRECT_STEPS = frozenset(
    {"_build_redirect_request", "_redirect_url", "_redirect_method", "_redirect_headers", "_redirect_stream"}
)
# Quellen, deren Ausnahmetexte Teile der fremden Antwort oder der eigenen Anfrage zitieren.
_FOREIGN_TEXT_MODULES = frozenset({"httpx", "httpcore", "h11", "idna"})


def came_from_redirect(exc: BaseException) -> bool:
    """Ob `exc` beim Zusammenbauen einer Weiterleitung entstand. Erkannt an der Stelle im Traceback,
    ersatzweise am Wort "location" im Text -- beides waehlt nur zwischen festen Saetzen."""
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


def redirect_text(status_code: int | None = None) -> str:
    """Der Satz fuer jede Weiterleitung. Der `Location`-Header kommt vom fremden Server und steht darum
    nirgends im Text, auch nicht als Adressvorschlag."""
    code = f" (HTTP {status_code})" if status_code else ""
    return (
        f"Der Server hat die Verbindung auf eine andere Adresse umgeleitet{code}. "
        "Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
        "Prüfe die eingetragene Adresse des Servers."
    )


def unexpected_format_text(status_code: int) -> str:
    return f"Die Antwort von {SERVICE} hat nicht das erwartete Format (HTTP {status_code}). Stimmt die Adresse?"


def json_body(response: Any) -> Any:
    """Der JSON-Koerper, oder `None`, wenn er kaputt oder absichtlich tief verschachtelt ist."""
    try:
        return response.json()
    except (ValueError, RecursionError):
        return None


def clean_text(value: Any, limit: int = MAX_REASON) -> str | None:
    """Einzeiliger, gekuerzter Text ohne Steuer- und Formatzeichen; `None`, wenn nichts bleibt."""
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
    # koennte. Rechnernamen und `IP:Port` ohne Schema bleiben mit Absicht: echte Gruende von Proxmox nennen sie
    # (z. B. "Can't connect to 192.168.2.10:8006" im Cluster), und der Satz davor sagt, dass der Grund von Proxmox stammt.
    if "<" in text or ">" in text or "://" in text:
        return None
    return text


def _standard_phrase(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return ""


def reason_from(response: Any) -> str | None:
    """Der Grund, den Proxmox in einer Fehlerantwort nennt, sonst `None`: `message` und `errors` im JSON
    (`{"message": "...", "errors": {"vmid": "..."}}`), ersatzweise der Text in der Statuszeile
    (`HTTP/1.1 403 Permission check failed (...)`), wenn er nicht der Standardtext des Status ist.
    Roher Antworttext (HTML einer Proxy-Fehlerseite, Klartext) kommt nie zurueck, ebenso kein Grund mit HTML
    oder einer Adresse mit Schema."""
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
    """Fester Satz zu einer Ausnahme beim Senden einer Anfrage -- nie ihr Text."""
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
    # Ein Zertifikatsfehler (`ssl.SSLCertVerificationError`) ist zugleich ein `ValueError`. Er gehoert zur Pruefung
    # der Kette unten, nicht zu den Fehlern einer Adresse.
    if isinstance(exc, (httpx.InvalidURL, ValueError, UnicodeError)) and not isinstance(exc, OSError):
        if not own_url_ok(url):
            return "Die eingetragene Adresse ist ungültig. Bitte in den Einstellungen prüfen."
        if came_from_redirect(exc):
            return redirect_text()
        return f"Die Anfrage an {SERVICE} ließ sich nicht senden. Prüfe die Einstellungen."
    chain = _chain(exc)
    if _is_tls(chain):
        return (
            f"Das Zertifikat von {SERVICE} wird nicht akzeptiert. Bei einem selbstsignierten Zertifikat in den "
            "Einstellungen „Selbstsigniertes Zertifikat erlauben“ einschalten."
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
    """Ob der Text von `exc` aus der fremden Antwort oder der eigenen Anfrage stammen kann. Solche Ausnahmen
    bleiben nicht als Ursache an der Meldung haengen."""
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
