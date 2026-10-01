"""„Verbindung testen“ für Erweiterungen (`POST /extensions/{id}/test`).

Ruft `health()` der Erweiterung auf (und bei Benachrichtigungskanälen `channel.test()`
bzw. sendet eine Testnachricht) und macht aus dem Ergebnis eine kurze, verständliche
deutsche Antwort. Technische Fehler werden übersetzt (Zeitüberschreitung, abgelehnte
Zugangsdaten, Zertifikat, Adresse nicht gefunden …). In den Text kommt nie ein
Geheimnis: bekannte Werte aus dem Tresor und typische Muster (Bearer-Token, Passwörter in
Adressen) werden vorher geschwärzt.

Alles hier ist generisch; der Kern kennt keine einzelne Erweiterung.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, quote_plus, urlsplit

from nodvard_sdk import Notification as SdkNotification, Severity
from nodvard_sdk.capabilities import NotificationChannel

TEST_TIMEOUT_S = 25.0
_MAX_TEXT = 300

_SECRET_KEYS = r"(?:token|password|passwort|passwd|secret|api[_-]?key|apikey|authorization|session(?:[_-]?id)?|cookie)"
_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(?<![A-Za-z0-9])(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}"), r"\1 ***"),
    (re.compile(r"(?i)(PVEAPIToken=)\S+"), r"\1***"),
    # `access_token=abc`, `"token": "abc"`, `{'password': 'abc'}`, `sessionid=abc`, `Authorization: abc`
    (
        re.compile(
            r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.-]*" + _SECRET_KEYS + r"[\"']?\s*[=:]\s*[\"']?)[^\s,;&\"'}\]]{3,}"
        ),
        r"\1***",
    ),
    (re.compile(r"(?<=://)[^/\s@]+@"), "***@"),
]


def scrub(text: str, secrets: list[str] | None = None) -> str:
    """Schwärzt bekannte Geheimnisse und typische Muster und kürzt den Text."""
    out = text or ""
    known = {s for s in (secrets or []) if s and len(s) >= 4}
    # Auch die URL-kodierten Formen: ein Geheimnis in einer Adresse steht dort kodiert.
    known |= {quote(s, safe="") for s in known} | {quote_plus(s) for s in known}
    for secret in sorted(known, key=len, reverse=True):
        out = out.replace(secret, "***")
    for pattern, repl in _REDACTIONS:
        out = pattern.sub(repl, out)
    out = " ".join(out.split())
    return out if len(out) <= _MAX_TEXT else out[: _MAX_TEXT - 1] + "…"


def describe_exception(exc: BaseException) -> str:
    """Text für die Erkennung: Typnamen und Meldungen der ganzen Ursachenkette (ein
    `ConnectError` trägt die eigentliche Ursache oft nur in `__cause__`) und ein
    HTTP-Status, falls die Ausnahme eine Antwort mitbringt."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(parts) < 5:
        seen.add(id(current))
        text = str(current)
        parts.append(f"{type(current).__name__}: {text}" if text else type(current).__name__)
        status_code = getattr(getattr(current, "response", None), "status_code", None)
        if isinstance(status_code, int):
            parts.append(f"HTTP {status_code}")
        current = current.__cause__ or current.__context__
    return " | ".join(parts)


@dataclass
class Translation:
    kind: str
    message: str


_HTTP_CODE = re.compile(r"HTTP[ /]*(\d{3})")
_TLS = re.compile(r"(?<![a-z])(ssl|tls)|certificate|zertifikat", re.I)
_DNS = re.compile(
    r"name or service not known|getaddrinfo|name resolution|nodename nor servname|no address associated|gaierror|namensaufl", re.I
)
_TIMEOUT = re.compile(r"timeout|timed out|zeit[üu]berschreitung", re.I)
_REFUSED = re.compile(
    r"connection refused|connecterror|all connection attempts failed|connect call failed|errno 111|no route to host|network is unreachable|errno 113|errno 101",
    re.I,
)
_SECRET_MISSING = re.compile(r"secretunavailable|secret '.*' existiert nicht|secret .* nicht gefunden", re.I)
_ALREADY_GERMAN = re.compile(r"konfiguriert|nicht eingerichtet|fehlt|fehlen", re.I)


def host_of(value: str | None) -> str | None:
    """`host:port` aus einer Adresse (ohne Benutzerdaten), sonst `None`."""
    if not value:
        return None
    match = re.search(r"https?://[^\s'\"<>]+", value)
    if not match:
        return None
    try:
        parts = urlsplit(match.group(0))
        host = parts.hostname
        if not host:
            return None
        return f"{host}:{parts.port}" if parts.port else host
    except ValueError:
        return None


def _first_url_host(data: Any) -> str | None:
    if isinstance(data, dict):
        ordered = sorted(data.items(), key=lambda kv: 0 if "url" in kv[0].lower() else 1)
        for _, val in ordered:
            found = _first_url_host(val) if isinstance(val, (dict, list)) else host_of(val if isinstance(val, str) else None)
            if found:
                return found
    elif isinstance(data, list):
        for val in data:
            found = _first_url_host(val)
            if found:
                return found
    return None


def guess_host(settings: dict[str, Any], item_name: str | None = None) -> str | None:
    """Der Server, um den es geht: bei einem benannten Eintrag dessen Adresse, sonst die erste
    Adresse in den Einstellungen."""
    if item_name:
        for value in settings.values():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and str(item.get("name") or "") == item_name:
                        found = _first_url_host(item)
                        if found:
                            return found
    return _first_url_host(settings)


def tls_option_title(schema: dict[str, Any] | None) -> str | None:
    """Titel der Einstellung „Zertifikat nicht prüfen“, falls die Erweiterung eine hat."""
    def find(props: dict[str, Any]) -> str | None:
        for key, sub in props.items():
            if key == "tls_insecure_skip_verify":
                return str(sub.get("title") or key)
            inner = (sub.get("items") or {}).get("properties") if sub.get("type") == "array" else sub.get("properties")
            if inner:
                found = find(inner)
                if found:
                    return found
        return None

    return find((schema or {}).get("properties") or {})


def translate(text: str, *, host: str | None = None, tls_hint: str | None = None) -> Translation:
    """Macht aus einer technischen Fehlermeldung einen verständlichen deutschen Satz."""
    where = host or host_of(text)
    code_match = _HTTP_CODE.search(text)
    code = int(code_match.group(1)) if code_match else None

    if code in (401, 403):
        return Translation("auth", "Zugangsdaten abgelehnt – Benutzername, Token oder Passwort prüfen.")
    if _SECRET_MISSING.search(text):
        return Translation("secret_missing", "Zugangsdaten fehlen – bitte unter „Zugangsdaten“ eintragen.")
    if code == 404:
        return Translation("not_found", "Die Adresse antwortet, aber dort läuft nicht der erwartete Dienst (Seite nicht gefunden) – Adresse prüfen.")
    if code is not None and code >= 500:
        return Translation("server_error", f"Der Server meldet einen Fehler (HTTP {code}) – später erneut versuchen.")
    if code is not None and code >= 400:
        return Translation("http", f"Unerwartete Antwort vom Server (HTTP {code}) – Einstellungen prüfen.")
    if _TLS.search(text):
        hint = f" – ggf. „{tls_hint}“ einschalten" if tls_hint else ""
        return Translation("tls", f"Zertifikat wird nicht vertraut{hint}.")
    if _DNS.search(text):
        return Translation("dns", f"Adresse nicht gefunden{f' ({where})' if where else ''} – Schreibweise und Netzwerk prüfen.")
    if _TIMEOUT.search(text):
        return Translation("timeout", f"Keine Antwort von {where or 'dem Server'} – Adresse und Port prüfen.")
    if _REFUSED.search(text):
        return Translation(
            "refused", f"Verbindung zu {where or 'dem Server'} nicht möglich – läuft der Dienst, und stimmen Adresse und Port?"
        )
    if _ALREADY_GERMAN.search(text):
        return Translation("setup", text.rstrip(".") + ".")
    return Translation("unknown", f"Die Verbindung hat nicht geklappt. Technische Meldung: {text}" if text else "Die Verbindung hat nicht geklappt.")


@dataclass
class Detail:
    name: str
    ok: bool
    message: str


@dataclass
class CheckOutcome:
    ok: bool
    message: str
    kind: str = "ok"
    details: list[Detail] = field(default_factory=list)


def _detail_items(report_details: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Das Muster „ein Eintrag je Verbindung“ in `HealthReport.details`:
    `{"pve1": {"healthy": True, ...}, ...}`."""
    return [(k, v) for k, v in report_details.items() if isinstance(v, dict) and isinstance(v.get("healthy"), bool)]


async def run_connection_test(
    loaded: Any, *, settings: dict[str, Any], schema: dict[str, Any] | None, secrets: list[str], channel: Any | None
) -> CheckOutcome:
    tls_hint = tls_option_title(schema)

    def tr(text: str, item: str | None = None) -> Translation:
        clean = scrub(text, secrets)
        return translate(clean, host=guess_host(settings, item), tls_hint=tls_hint)

    try:
        report = await asyncio.wait_for(loaded.instance.health(loaded.ctx), TEST_TIMEOUT_S)
    except asyncio.TimeoutError:
        t = tr("Timeout: keine Antwort der Erweiterung")
        return CheckOutcome(False, t.message, t.kind)
    except Exception as exc:  # noqa: BLE001 - Testlauf darf nie als 500 enden
        t = tr(describe_exception(exc))
        return CheckOutcome(False, t.message, t.kind)

    items = _detail_items(report.details or {})
    if items:
        details: list[Detail] = []
        for name, entry in items:
            if entry["healthy"]:
                details.append(Detail(name, True, "Verbindung funktioniert."))
            else:
                t = tr(str(entry.get("error") or entry.get("message") or ""), name)
                details.append(Detail(name, False, t.message))
        bad = [d for d in details if not d.ok]
        if not bad:
            message = "Verbindung funktioniert." if len(details) == 1 else f"Alle {len(details)} Verbindungen funktionieren."
        elif len(details) == 1:
            message = bad[0].message
        else:
            message = f"{len(bad)} von {len(details)} Verbindungen funktionieren nicht – Einzelheiten unten."
        if bad:
            return CheckOutcome(False, message, "partial", details)
        outcome = CheckOutcome(True, message, "ok", details)
    elif not report.healthy:
        t = tr(report.message or "")
        return CheckOutcome(False, t.message, t.kind)
    else:
        outcome = CheckOutcome(True, "Verbindung funktioniert.")

    if channel is not None:
        try:
            result = await asyncio.wait_for(channel.test(), TEST_TIMEOUT_S)
        except asyncio.TimeoutError:
            t = tr("Timeout: keine Antwort des Servers")
            return CheckOutcome(False, t.message, t.kind)
        except Exception as exc:  # noqa: BLE001
            t = tr(describe_exception(exc))
            return CheckOutcome(False, t.message, t.kind)
        if not result.ok:
            t = tr(result.message or "")
            return CheckOutcome(False, t.message, t.kind)
    return outcome


async def send_test_message(*, channel: Any, settings: dict[str, Any], schema: dict[str, Any] | None, secrets: list[str], product_name: str) -> CheckOutcome:
    tls_hint = tls_option_title(schema)
    notification = SdkNotification(
        title="Testnachricht",
        body=f"Das ist eine Testnachricht von {product_name}. Wenn du sie siehst, funktioniert die Benachrichtigung.",
        severity=Severity.INFO,
        payload={"test": True},
    )
    try:
        await asyncio.wait_for(channel.send(notification), TEST_TIMEOUT_S)
    except asyncio.TimeoutError:
        t = translate("Timeout: keine Antwort des Servers", host=guess_host(settings), tls_hint=tls_hint)
        return CheckOutcome(False, t.message, t.kind)
    except Exception as exc:  # noqa: BLE001
        t = translate(scrub(describe_exception(exc), secrets), host=guess_host(settings), tls_hint=tls_hint)
        return CheckOutcome(False, t.message, t.kind)
    return CheckOutcome(True, "Testnachricht gesendet – sie sollte gleich auf dem Gerät ankommen.", "ok")


def find_channel(runtime: Any, ext_id: str) -> Any | None:
    return runtime.capabilities.provided_by(NotificationChannel, ext_id)
