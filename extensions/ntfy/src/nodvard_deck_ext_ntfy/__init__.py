"""ntfy-Extension -- der erste echte `NotificationChannel` (docs/02-EXTENSION-API.md
§3).

Hintergrund: das Vorgaengersystem postete an ein oeffentliches, unauthentifiziertes
`https://ntfy.sh`-Topic mit festem Namen -- wer den Namen kennt oder erraet, liest
saemtliche Lageberichte mit und kann dort selbst Nachrichten einstellen, die wie
echte Meldungen aussehen. Diese Extension hat deshalb WEDER
einen hartcodierten Server NOCH ein hartcodiertes Topic (beides Einstellungen, siehe
`settings.schema.json`) UND ein optionales Zugriffstoken, das ausschliesslich im
Vault liegt -- nie im Klartext in Einstellungen, Log oder DB.

Nutzt ntfys JSON-Publish-API (`POST {server}/`), nicht die Header-basierte einfache
API: Header sind auf ASCII/Latin-1 beschraenkt, ein deutscher Titel mit Umlauten
waere darueber nicht sicher uebertragbar.

Zwei Einstellungen steuern, was auf dem Handy ankommt: die Mindest-Stufe (`min_severity`:
was darunter liegt, geht nicht an ntfy, steht aber wie immer unter "Meldungen" im Dashboard)
und die ntfy-Prioritaet je Stufe (`priority_info`/`priority_warning`/`priority_critical`).
Dazu liefert die Extension den Link zum Abonnieren fuer den QR-Code der Einstellungsseite
(`GET /api/v1/ext/ntfy/subscribe-link`).

**Weitere Kanaele** (`channels.py`): Telegram, Discord, Gotify, Pushover, Matrix, Slack, Mattermost und eine eigene
Apprise-API, je mit eigener Mindest-Stufe. Die Kennung bleibt `ntfy` (Zustellprotokoll, Einstellungen), in der
Oberflaeche heisst das Modul "Push-Nachrichten". Es ist EIN Kanal fuer den Kern: der Kern meldet je Kanal-Objekt
einen Zustellstatus, und Kanaele lassen sich nur beim Start anmelden, die Liste in den Einstellungen aendert sich
aber jederzeit. Ein ausgefallener Teil-Kanal kippt deshalb die Zustellung nur, wenn gar nichts ankam (oder bei der
Testnachricht, die jeden Fehler nennen soll); sonst steht er im Log und im Zustand der Erweiterung.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Mapping
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, status
from nodvard_sdk import (
    ConnectorHealth,
    ExtensionContext,
    HealthReport,
    NodvardExtension,
    Notification,
    Severity,
)
from nodvard_sdk.errors import NotificationSkipped
from pydantic import BaseModel

from .channels import NAME_PATTERN as CHANNEL_NAME
from .channels import (
    CHANNEL_TIMEOUT_S,
    MAX_CHANNELS,
    ChannelError,
    Target,
    below_minimum,
    fingerprint,
    parse_channel_url,
    probe,
    secret_label,
    send_to,
)

_TOKEN_LABEL = "ntfy-token"

# Die Zuordnung, die gilt, solange nichts eingestellt ist (und fuer jeden unbekannten Wert).
_PRIORITY_BY_SEVERITY = {
    Severity.INFO: 3,
    Severity.WARNING: 4,
    Severity.CRITICAL: 5,
}

# ntfys Namen der Prioritaeten. Die JSON-API von ntfy will die Zahl 1 bis 5, die Einstellung speichert den Namen.
_PRIORITY_NUMBERS = {"min": 1, "low": 2, "default": 3, "high": 4, "max": 5}

_PRIORITY_SETTING = {
    Severity.INFO: "priority_info",
    Severity.WARNING: "priority_warning",
    Severity.CRITICAL: "priority_critical",
}

_SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}

# Emoji-Kuerzel von ntfy (werden in der App als Symbol vor dem Titel gezeigt).
_TAGS_BY_SEVERITY = {
    Severity.INFO: ["information_source"],
    Severity.WARNING: ["warning"],
    Severity.CRITICAL: ["rotating_light"],
}


class NtfyError(RuntimeError):
    """Fehler beim Senden oder Testen. Der Text ist ein fertiger deutscher Satz (`readable`) und
    enthaelt weder Adressen noch Antworttexte des Servers: eine Adresse aus der Antwort kaeme vom
    fremden Server, und die eingetragene kann Zugangsdaten tragen (`https://name:passwort@...`)."""

    readable = True


def redirect_text() -> str:
    return (
        "ntfy leitet auf eine andere Adresse um. Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
        "Meist ist http:// statt https:// eingetragen (oder umgekehrt), oder ein Proxy davor leitet um. "
        "Trage in den Einstellungen die endgültige Adresse des ntfy-Servers ein."
    )


def status_error(status_code: int) -> str | None:
    """Lesbarer Satz zu einer Antwort, die kein Erfolg (2xx) ist; `None` bei 2xx. Die Weiterleitung
    (3xx) nennt nie ihr Ziel: der `Location`-Header kommt vom fremden Server."""
    if 200 <= status_code < 300:
        return None
    if 300 <= status_code < 400:
        return redirect_text().replace("ntfy leitet auf eine andere Adresse um.", f"ntfy leitet auf eine andere Adresse um (HTTP {status_code}).", 1)
    if status_code in (401, 403):
        return f"ntfy hat den Zugang abgelehnt (HTTP {status_code}). Prüfe das Zugriffstoken in den Einstellungen und ob das Thema geschützt ist."
    if status_code == 404:
        return "Unter dieser Adresse antwortet kein ntfy-Server (HTTP 404). Prüfe die Adresse in den Einstellungen."
    if status_code == 429:
        return "ntfy nimmt gerade keine weiteren Nachrichten an (HTTP 429, zu viele Anfragen). Versuche es später erneut."
    if 400 <= status_code < 500:
        return f"ntfy hat die Nachricht nicht angenommen (HTTP {status_code}). Prüfe Adresse, Thema und Zugriffstoken."
    if status_code >= 500:
        return f"Der ntfy-Server meldet einen Fehler (HTTP {status_code}). Versuche es später erneut."
    return f"ntfy hat unerwartet geantwortet (HTTP {status_code})."


def _url_is_valid(url: str) -> bool:
    try:
        _ = httpx.URL(url).host  # `.host` entschluesselt Punycode und scheitert an einem kaputten Namen
    except (httpx.InvalidURL, ValueError):
        return False
    return True


# Die Schritte, in denen httpx eine Weiterleitung zusammenbaut (siehe `_came_from_redirect`).
_REDIRECT_STEPS = frozenset(
    {"_build_redirect_request", "_redirect_url", "_redirect_method", "_redirect_headers", "_redirect_stream"}
)


def _came_from_redirect(exc: BaseException) -> bool:
    """Ob `exc` beim Zusammenbauen einer Weiterleitung entstand (erkannt an der Stelle im Traceback,
    ersatzweise am Wort "location" im Text). Waehlt nur zwischen festen Saetzen."""
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_name in _REDIRECT_STEPS:
            return True
        tb = tb.tb_next
    return "location" in str(exc).lower()


def unusable_exchange_text(exc: BaseException, url: str) -> str:
    """Fester Satz fuer `httpx.ProtocolError`, `httpx.InvalidURL` und `ValueError` -- nie ihr Text. h11
    zitiert kaputte Zeilen der Antwort (auch einen kaputten `Location`-Header) und bei der eigenen
    Anfrage den kaputten Header samt Wert, also auch das Zugriffstoken (`Illegal header value b'Bearer ...'`);
    ein ungueltiger Punycode-Name in der Weiterleitung nennt Teile des fremden Namens."""
    not_sendable = (
        "Die Anfrage an ntfy ließ sich nicht senden. Prüfe die Adresse und das Zugriffstoken in den Einstellungen "
        "(zum Beispiel ein Zeilenumbruch oder Umlaut im Token)."
    )
    if isinstance(exc, httpx.ProtocolError):
        if _came_from_redirect(exc):
            return redirect_text()
        if isinstance(exc, httpx.LocalProtocolError):
            return not_sendable
        return (
            "Der ntfy-Server hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt. "
            "Prüfe die Adresse in den Einstellungen (http:// oder https://, Port)."
        )
    if not _url_is_valid(url):
        return "Die Adresse des ntfy-Servers ist ungültig. Prüfe sie in den Einstellungen."
    if _came_from_redirect(exc):
        return redirect_text()
    return not_sendable


def priorities_from_settings(settings: Mapping[str, Any]) -> dict[Severity, int]:
    """ntfy-Prioritaet (1 bis 5) je Stufe aus den Einstellungen. Ein fehlender oder unbekannter Wert
    (auch ein Wert, den eine spaetere Version nicht mehr kennt) gilt wie die feste Zuordnung von frueher
    (Info 3, Warnung 4, Kritisch 5): eine Meldung wird nie wegen einer kaputten Einstellung leiser."""
    out: dict[Severity, int] = {}
    for severity, key in _PRIORITY_SETTING.items():
        raw = settings.get(key)
        name = raw.strip().lower() if isinstance(raw, str) else ""
        out[severity] = _PRIORITY_NUMBERS.get(name, _PRIORITY_BY_SEVERITY[severity])
    return out


def min_severity_from_settings(settings: Mapping[str, Any]) -> Severity:
    """Die eingestellte Mindest-Stufe. Fehlt sie oder ist sie unbekannt, gilt `info` (alles geht raus):
    lieber eine Meldung zu viel als ein Alarm, der wegen eines Tippfehlers nie ankommt."""
    raw = settings.get("min_severity")
    try:
        return Severity(raw.strip().lower()) if isinstance(raw, str) else Severity.INFO
    except ValueError:
        return Severity.INFO


def is_below_minimum(notification: Notification, settings: Mapping[str, Any]) -> bool:
    """Ob die Meldung unter der Mindest-Stufe liegt und deshalb nicht an ntfy geht. Die Testnachricht der
    Einstellungsseite (`payload["test"]`) geht immer raus: Wer sie schickt, will sehen, dass es klappt."""
    if (notification.payload or {}).get("test") is True:
        return False
    return _SEVERITY_RANK.get(notification.severity, 0) < _SEVERITY_RANK[min_severity_from_settings(settings)]


_SUBSCRIBE_NAME = "Nodvard Deck"
"""Der Name, unter dem die ntfy-App das abonnierte Thema anzeigt (`display=` im Link)."""


def build_subscribe_link(server_url: Any, topic: Any) -> tuple[str | None, str | None]:
    """Der Link, mit dem die ntfy-App ein Thema abonniert: `ntfy://<host[:port][/pfad]>/<thema>`, bei einem
    `http://`-Server mit `?secure=false` (sonst nimmt die App https an), dazu `display=` fuer den Anzeigenamen
    (docs.ntfy.sh/subscribe/phone). Liefert `(Link, None)` oder `(None, Satz)`; der Satz ist fertiges Deutsch.

    Der Link enthaelt nur Adresse, Port, Pfad und Thema: Zugangsdaten in der eingetragenen Adresse
    (`https://name:passwort@...`), Abfrage und Fragment bleiben draussen, ebenso das Zugriffstoken -- der
    Link steht auf der Einstellungsseite und im QR-Code."""
    server = server_url.strip() if isinstance(server_url, str) else ""
    name = topic.strip() if isinstance(topic, str) else ""
    if not server and not name:
        return None, "Es fehlen noch die Adresse des ntfy-Servers und das Thema. Trage beides ein und speichere, dann erscheint hier der QR-Code."
    if not server:
        return None, "Es fehlt noch die Adresse des ntfy-Servers. Trage sie ein und speichere, dann erscheint hier der QR-Code."
    if not name:
        return None, "Es fehlt noch das Thema. Trage es ein und speichere, dann erscheint hier der QR-Code."
    invalid = "Die Adresse des ntfy-Servers ist ungültig. Prüfe sie in den Einstellungen."
    try:
        parts = urlsplit(server)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None, invalid
    if parts.scheme not in ("http", "https") or not host:
        return None, invalid
    authority = f"[{host}]" if ":" in host else host  # eine IPv6-Adresse braucht eckige Klammern
    if port is not None:
        authority += f":{port}"
    path = quote(parts.path.rstrip("/"), safe="/%")
    query = {"secure": "false"} if parts.scheme == "http" else {}
    query["display"] = _SUBSCRIBE_NAME
    return f"ntfy://{authority}{path}/{quote(name, safe='')}?{urlencode(query, quote_via=quote)}", None


def build_message(
    notification: Notification, topic: str, dashboard_url: str | None, priorities: Mapping[Severity, int] | None = None
) -> dict:
    """ntfy-JSON fuer eine Benachrichtigung. Konventionen im `payload` (alle optional):
    `path` -- Seite im Dashboard, die ein Tipp auf die Nachricht oeffnet;
    `actions` -- bis zu drei Knoepfe `[{label, path}]`; `tags` -- eigene ntfy-Symbole.
    `priorities`: ntfy-Prioritaet je Stufe (`priorities_from_settings`), ohne Angabe die feste Zuordnung."""
    payload = notification.payload or {}
    body: dict = {
        "topic": topic,
        "title": notification.title,
        "message": notification.body,
        "priority": (priorities or _PRIORITY_BY_SEVERITY).get(notification.severity, 3),
        "tags": [str(t) for t in (payload.get("tags") or _TAGS_BY_SEVERITY.get(notification.severity, []))][:5],
    }
    if dashboard_url:
        base = dashboard_url.rstrip("/")
        path = str(payload.get("path") or "/notifications")
        body["click"] = base + (path if path.startswith("/") else f"/{path}")
        actions = []
        for a in (payload.get("actions") or [])[:3]:
            if isinstance(a, dict) and a.get("label") and a.get("path"):
                p = str(a["path"])
                actions.append({"action": "view", "label": str(a["label"])[:40], "url": base + (p if p.startswith("/") else f"/{p}")})
        if actions:
            body["actions"] = actions
    return body


NOT_SET_UP_TEXT = (
    "Push-Nachrichten sind noch nicht eingerichtet: Trage einen ntfy-Server mit Thema ein oder einen weiteren Kanal "
    "(Einstellungen, Erweiterungen, Push-Nachrichten)."
)

_last_failures: dict[str, tuple[str, str]] = {}
"""Letzter Fehler je weiterem Kanal (Name -> (Abdruck der Adresse, fester Satz)), nur im Speicher: "Verbindung testen"
nennt ihn, auch wenn die Zustellung insgesamt klappte. Ein Erfolg loescht den Eintrag, eine neue Adresse macht ihn
ungueltig."""


class ChannelsFailed(NtfyError):
    """Fehler mehrerer Kanaele, ein fertiger Satz je Kanal. `verbatim`: der Kern zeigt ihn unveraendert (sonst nimmt
    seine Uebersetzung den ersten HTTP-Code im Text und nennt weder Kanal noch die anderen Fehler)."""

    verbatim = True


def ntfy_configured(settings: Mapping[str, Any]) -> bool:
    return bool(settings.get("server_url")) and bool(settings.get("topic"))


def channel_entries(settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Die eingeschalteten weiteren Kanaele aus den Einstellungen (Eintraege ohne gueltigen Namen zaehlen nicht)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in (settings.get("channels") or [])[:MAX_CHANNELS]:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        name = item.get("name")
        if not isinstance(name, str) or not CHANNEL_NAME.fullmatch(name) or name in seen:
            continue
        seen.add(name)
        out.append(item)
    return out


class _NtfyChannel:
    """Erfuellt `nodvard_sdk.capabilities.NotificationChannel`."""

    channel_id = "ntfy"
    label = "Push-Nachrichten"

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    @staticmethod
    def _config(settings: Mapping[str, Any]) -> tuple[str, str]:
        server_url = settings.get("server_url")
        topic = settings.get("topic")
        if not server_url or not topic:
            raise RuntimeError(NOT_SET_UP_TEXT)
        return str(server_url).rstrip("/"), str(topic)

    async def _target(self, name: str) -> tuple[Target, str]:
        """Die gelesene Adresse eines weiteren Kanals aus dem Tresor und ihr Abdruck; `ChannelError`, wenn sie fehlt
        oder nicht passt."""
        label = secret_label(name)
        if not await self._ctx.secrets.exists(label):
            raise ChannelError(f"Für „{name}“ fehlt noch die Adresse des Kanals (Einstellungen, unten unter „Zugangsdaten“).")
        handle = await self._ctx.secrets.get_handle(label)
        async with self._ctx.vault_use(handle) as url:
            return parse_channel_url(url), fingerprint(url)

    async def _send_extra(
        self, item: Mapping[str, Any], notification: Notification, dashboard_url: str | None
    ) -> tuple[str, str | None]:
        """Schickt an einen weiteren Kanal: `("sent", Teilfehler oder None)` oder `("skipped", None)`, sonst
        `ChannelError`. Merkt sich den Fehler fuer "Verbindung testen"."""
        if below_minimum(notification, item.get("min_severity")):
            return "skipped", None
        name = str(item["name"])
        target, mark = await self._target(name)
        try:
            partial = await send_to(self._ctx.http, target, notification, dashboard_url)
        except ChannelError as exc:
            _last_failures[name] = (mark, str(exc))
            raise
        if partial:
            _last_failures[name] = (mark, partial)
        else:
            _last_failures.pop(name, None)
        return "sent", partial

    @staticmethod
    def _dashboard_url(settings: Mapping[str, Any]) -> str | None:
        url = (settings.get("dashboard_url") or "").strip()
        return url if url.startswith(("http://", "https://")) else None

    async def _auth_header(self) -> dict[str, str]:
        if not await self._ctx.secrets.exists(_TOKEN_LABEL):
            return {}
        handle = await self._ctx.secrets.get_handle(_TOKEN_LABEL)
        async with self._ctx.vault_use(handle) as token:
            return {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Die Antwort, oder `NtfyError` mit festem Satz, wenn Antwort, Weiterleitung oder eigene Anfrage
        unbrauchbar sind (`unusable_exchange_text`). httpx baut die Weiterleitung auch dann, wenn es ihr
        nicht folgt, und scheitert dort an einem kaputten `Location`-Header. Der Fehler wird ausserhalb des
        `except`-Blocks geworfen: Sonst hinge die Ausnahme von httpx als `__context__` daran, und jeder
        Fehlertext, der die Ursachenkette abschreibt, truege ihren Text mit."""
        problem: str
        try:
            return await self._ctx.http.request(method, url, **kwargs)
        except (httpx.ProtocolError, httpx.InvalidURL, ValueError) as exc:
            problem = unusable_exchange_text(exc, url)
        raise NtfyError(problem)

    async def send(self, notification: Notification) -> None:
        """Schickt die Meldung an ntfy. Liegt sie unter der Mindest-Stufe (`min_severity`), geht keine Anfrage raus,
        und `NotificationSkipped` sagt dem Kern, dass das so eingestellt ist: Er verbucht die Zustellung als
        "uebersprungen" (weder zugestellt noch Fehler), `raise_on_failure` versucht nichts erneut, und eine Seite wie
        das Shield-Briefing behauptet nicht "zugestellt". Die Meldung steht trotzdem unter "Meldungen" im Dashboard.
        Ohne Einrichtung wirft sie auch dann, wenn sie sowieso uebersprungen wuerde: ein nicht
        eingerichteter Kanal soll nie wie ein gesunder aussehen.

        Mit weiteren Kanaelen laufen alle nebeneinander, jeder mit eigener Frist (`CHANNEL_TIMEOUT_S`). Fehler wirft
        sie nur, wenn gar nichts ankam oder bei der Testnachricht (dann mit einem Satz je Kanal); sonst stehen sie im
        Log und bei "Verbindung testen"."""
        settings = await self._ctx.settings.get()
        extras = channel_entries(settings)
        if not extras:
            # Nur ntfy: genau wie vor den weiteren Kanaelen.
            self._config(settings)
            if is_below_minimum(notification, settings):
                raise NotificationSkipped("Unter der eingestellten Mindest-Stufe.")
            await self._send_ntfy(settings, notification)
            return
        dashboard_url = self._dashboard_url(settings)
        jobs: list[Awaitable[tuple[str, str, str | None]]] = []
        if ntfy_configured(settings):
            jobs.append(self._guarded("ntfy", self._ntfy_job(settings, notification)))
        for item in extras:
            name = str(item["name"])
            jobs.append(self._guarded(f"„{name}“", self._send_extra(item, notification, dashboard_url)))
        results = await asyncio.gather(*jobs)
        sent = sum(1 for _, outcome, _ in results if outcome == "sent")
        failed = any(outcome == "failed" for _, outcome, _ in results)
        # Mit Teilfehlern ("2 von 3 Chats"): die Testnachricht soll jeden Fehler nennen.
        failures = [f"{who}: {problem}" for who, _, problem in results if problem]
        if failures and ((failed and sent == 0) or (notification.payload or {}).get("test") is True):
            raise ChannelsFailed(" ".join(failures))
        for failure in failures:
            self._ctx.logger.warning("Push-Nachrichten: ein Kanal hat die Meldung nicht (ganz) angenommen: %s", failure)
        if sent == 0:
            raise NotificationSkipped("Unter der eingestellten Mindest-Stufe.")

    async def _ntfy_job(self, settings: Mapping[str, Any], notification: Notification) -> tuple[str, str | None]:
        if is_below_minimum(notification, settings):
            return "skipped", None
        await self._send_ntfy(settings, notification)
        return "sent", None

    async def _guarded(self, who: str, job: Awaitable[tuple[str, str | None]]) -> tuple[str, str, str | None]:
        """`(wer, "sent"|"skipped"|"failed", Satz oder None)` -- wirft nie, und nie mit fremdem Text (er kann die
        Adresse samt Token tragen)."""
        try:
            async with asyncio.timeout(CHANNEL_TIMEOUT_S):
                outcome, partial = await job
        except TimeoutError:
            return who, "failed", "Keine Antwort (Zeitüberschreitung). Versuche es später erneut."
        except (ChannelError, NtfyError) as exc:
            return who, "failed", str(exc)
        except Exception as exc:  # noqa: BLE001
            self._ctx.logger.warning("Push-Nachrichten: %s, unerwarteter Fehler (%s)", who, type(exc).__name__)
            if who == "ntfy":
                return who, "failed", "Der ntfy-Server ist nicht erreichbar oder hat nicht geantwortet."
            return who, "failed", "Die Nachricht ließ sich nicht senden."
        return who, outcome, partial

    async def _send_ntfy(self, settings: Mapping[str, Any], notification: Notification) -> None:
        server_url, topic = self._config(settings)
        headers = await self._auth_header()
        body = build_message(notification, topic, self._dashboard_url(settings), priorities_from_settings(settings))
        response = await self._request("POST", server_url + "/", json=body, headers=headers)
        problem = status_error(response.status_code)
        if problem:
            raise NtfyError(problem)

    async def test(self) -> ConnectorHealth:
        """Fragt den ntfy-Server (ohne etwas zu senden). Ist nur ein weiterer Kanal eingerichtet, gibt es hier nichts zu
        fragen; dessen Adresse prueft `health()`."""
        settings = await self._ctx.settings.get()
        if not ntfy_configured(settings) and channel_entries(settings):
            return ConnectorHealth(ok=True, message="ntfy ist nicht eingerichtet, nur weitere Kanäle.")
        try:
            server_url, _ = self._config(settings)
        except RuntimeError as exc:
            return ConnectorHealth(ok=False, message=str(exc))
        try:
            response = await self._request("GET", server_url + "/v1/health")
        except Exception as exc:  # noqa: BLE001 - Health-Check darf nie werfen, nur melden
            return ConnectorHealth(ok=False, message=str(exc))
        problem = status_error(response.status_code)
        if problem:
            return ConnectorHealth(ok=False, message=problem)
        return ConnectorHealth(ok=True, message=f"HTTP {response.status_code}")


class _TokenIn(BaseModel):
    value: str


class SubscribeLinkOut(BaseModel):
    """Antwort von `GET /ext/ntfy/subscribe-link`: `url` steckt im QR-Code, `text` sagt, was man damit tut,
    `error` ist ein fertiger Satz, wenn sich kein Link berechnen laesst (dann sind `url` und `text` leer)."""

    url: str | None = None
    text: str | None = None
    error: str | None = None


_SUBSCRIBE_HINT = (
    "Halte die Kamera deines Handys auf den Code: Die ntfy-App öffnet sich und bietet an, dieses Thema zu abonnieren. "
    "Klappt das nicht, trage Server und Thema in der ntfy-App von Hand ein."
)


_ONLY_CHANNELS_HINT = (
    "Der QR-Code gehört zu ntfy, und ntfy ist nicht eingerichtet. Deine weiteren Kanäle brauchen ihn nicht."
)


def subscribe_link_for(settings: Mapping[str, Any]) -> SubscribeLinkOut:
    """Antwort von `GET /ext/ntfy/subscribe-link` zu den gespeicherten Einstellungen. Wer nur weitere Kanaele nutzt,
    bekommt einen ruhigen Hinweis statt "es fehlt noch ..."."""
    if not settings.get("server_url") and not settings.get("topic") and channel_entries(settings):
        return SubscribeLinkOut(error=_ONLY_CHANNELS_HINT)
    url, error = build_subscribe_link(settings.get("server_url"), settings.get("topic"))
    if url is None:
        return SubscribeLinkOut(error=error)
    return SubscribeLinkOut(url=url, text=_SUBSCRIBE_HINT)


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        # Sicherheitsnetz: Telegram-Token, Slack-Pfad und Discord-Token stehen in der Adresse der Anfrage, und httpx
        # schreibt jede Anfrage samt Adresse auf INFO. Heute zeigt das niemand (Wurzel-Logger auf WARNING); stellt eine
        # eigene Log-Einstellung tiefer, sollen die Zugangsdaten trotzdem nicht im Protokoll landen.
        logging.getLogger("httpx").setLevel(max(logging.WARNING, logging.getLogger("httpx").level))
        ctx.capabilities.provide(_NtfyChannel(ctx))
        ctx.settings.declare(
            {
                "type": "object",
                "properties": {
                    "server_url": {"type": "string"}, "topic": {"type": "string"}, "dashboard_url": {"type": "string"},
                    "min_severity": {"type": "string"}, "priority_info": {"type": "string"},
                    "priority_warning": {"type": "string"}, "priority_critical": {"type": "string"},
                    "channels": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"}, "min_severity": {"type": "string"},
                                "enabled": {"type": "boolean"},
                            },
                            "required": ["name"],
                        },
                    },
                },
            }
        )

        router = APIRouter()

        @router.post("/token", status_code=status.HTTP_204_NO_CONTENT)
        async def set_token(payload: _TokenIn) -> None:
            """Legt das optionale Zugriffstoken im Vault ab. Kein Update-Pfad in
            dieser Runde (`ctx.secrets` kennt keine Rotation) -- ein bereits
            gesetztes Token muss erst ueber das bestehende `DELETE /api/v1/secrets/
            {id}` (WP-2) entfernt werden. Absichtlich keine neue Mechanik fuer ein
            Problem, das schon eine Loesung hat."""
            if await ctx.secrets.exists(_TOKEN_LABEL):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Ein Token existiert bereits -- zuerst über DELETE /api/v1/secrets/{id} entfernen.",
                )
            await ctx.secrets.create(label=_TOKEN_LABEL, kind="generic", value=payload.value)

        ctx.api.include_router(router, permission="secrets.write")

        link_router = APIRouter()

        @link_router.get("/subscribe-link", response_model=SubscribeLinkOut)
        async def get_subscribe_link() -> SubscribeLinkOut:
            """Der Link zum Abonnieren fuer den QR-Code der Einstellungsseite (`x-qr-url` im Schema). Rechnet
            aus den GESPEICHERTEN Einstellungen, ohne Parameter; enthaelt weder Token noch sonst ein Geheimnis.
            Gleiches Recht wie das Lesen der Einstellungen. Fehlt etwas, kommt `error` mit einem Satz (kein
            Fehlerstatus)."""
            return subscribe_link_for(await ctx.settings.get())

        ctx.api.include_router(link_router, permission="extensions.manage")

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def on_stop(self, ctx: ExtensionContext) -> None:
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        """Ohne weitere Kanaele wie frueher (nur ntfy). Mit weiteren Kanaelen ein Eintrag je Kanal (`details`), alle
        nebeneinander: ntfy fragt seinen Server, die weiteren pruefen Token bzw. Schluessel beim Dienst, ohne eine
        Nachricht zu senden (`channels.probe`). Wo der Dienst das nicht erlaubt (Slack, Gotify, Mattermost), steht das
        im Hinweis (`note`), und ein Fehler beim letzten Senden zaehlt mit."""
        channel = _NtfyChannel(ctx)
        settings = await ctx.settings.get()
        extras = channel_entries(settings)
        if not extras:
            result = await channel.test()
            return HealthReport(healthy=result.ok, message=result.message)
        checks: list[Awaitable[tuple[str, dict[str, Any]]]] = []
        if ntfy_configured(settings):
            checks.append(_check_ntfy(channel))
        checks.extend(_check_extra(ctx, channel, str(item["name"])) for item in extras)
        details = dict(await asyncio.gather(*checks))
        summaries = [
            f"{name}: {entry.get('error') or entry.get('note') or 'ok'}" for name, entry in details.items()
        ]
        healthy = all(d["healthy"] for d in details.values())
        return HealthReport(healthy=healthy, message="; ".join(summaries), details=details)


async def _check_ntfy(channel: _NtfyChannel) -> tuple[str, dict[str, Any]]:
    try:
        async with asyncio.timeout(CHANNEL_TIMEOUT_S):
            result = await channel.test()
    except TimeoutError:
        return "ntfy", {"healthy": False, "error": "Der ntfy-Server antwortet nicht (Zeitüberschreitung)."}
    return "ntfy", ({"healthy": True} if result.ok else {"healthy": False, "error": result.message})


async def _check_extra(ctx: ExtensionContext, channel: _NtfyChannel, name: str) -> tuple[str, dict[str, Any]]:
    """Ein weiterer Kanal fuer "Verbindung testen". Bestaetigt der Dienst Token und Empfaenger, ist ein Fehler vom
    letzten Senden erledigt; laesst er sich nicht so pruefen, zaehlt der Fehler weiter, solange die Adresse gleich ist."""
    problem: str | None = None
    note: str | None = None
    label: str | None = None
    verbatim = False
    try:
        async with asyncio.timeout(CHANNEL_TIMEOUT_S):
            target, mark = await channel._target(name)
            label = target.label
            note = await probe(ctx.http, target)
    except TimeoutError:
        problem = "Keine Antwort (Zeitüberschreitung). Prüfe die Adresse des Kanals und das Netz."
    except ChannelError as exc:
        problem = str(exc)
        verbatim = True
    except Exception as exc:  # noqa: BLE001 - nie mit fremdem Text (er kann die Adresse samt Token tragen)
        ctx.logger.warning("Push-Nachrichten: Kanal %s nicht prüfbar (%s)", name, type(exc).__name__)
        problem = "Die Adresse des Kanals ließ sich nicht lesen oder prüfen."
    else:
        last = _last_failures.get(name)
        if note is None or (last is not None and last[0] != mark):
            _last_failures.pop(name, None)
        elif last is not None:
            problem = f"Beim letzten Senden: {last[1]}"
            verbatim = True
    if problem is not None:
        # `verbatim`: fertige Saetze dieser Erweiterung; der Kern zeigt sie unveraendert (sonst nimmt seine
        # Uebersetzung nur den HTTP-Code darin, und aus „Bot nicht im Chat“ wird „dem Konto fehlt ein Recht“).
        return name, {"healthy": False, "error": problem, **({"verbatim": True} if verbatim else {})}
    entry: dict[str, Any] = {"healthy": True, "kind": label}
    if note:
        entry["note"] = note
    return name, entry
