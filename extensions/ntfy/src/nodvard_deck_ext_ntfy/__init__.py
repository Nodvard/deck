"""ntfy-Erweiterung: Push-Nachrichten über ntfy und weitere Kanäle (`channels.py`).

Die Kennung bleibt `ntfy` (Zustellprotokoll, Einstellungen), in der Oberfläche heißt das Modul „Push-Nachrichten“.
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

# Server und Thema stehen nie im Code: Ein öffentliches Thema ohne Anmeldung kann jeder mitlesen oder mit falschen
# Meldungen füllen. Das Zugriffstoken liegt nur im Tresor, nie im Klartext in Einstellungen, Log oder DB.
_TOKEN_LABEL = "ntfy-token"

# Gilt, solange nichts eingestellt ist, und für jeden unbekannten Wert.
_PRIORITY_BY_SEVERITY = {
    Severity.INFO: 3,
    Severity.WARNING: 4,
    Severity.CRITICAL: 5,
}

# Die JSON-API von ntfy will die Zahl 1 bis 5, die Einstellung speichert den Namen.
_PRIORITY_NUMBERS = {"min": 1, "low": 2, "default": 3, "high": 4, "max": 5}

_PRIORITY_SETTING = {
    Severity.INFO: "priority_info",
    Severity.WARNING: "priority_warning",
    Severity.CRITICAL: "priority_critical",
}

_SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}

# Emoji-Kürzel von ntfy; die App zeigt sie als Symbol vor dem Titel.
_TAGS_BY_SEVERITY = {
    Severity.INFO: ["information_source"],
    Severity.WARNING: ["warning"],
    Severity.CRITICAL: ["rotating_light"],
}


class NtfyError(RuntimeError):
    """Fehler beim Senden oder Testen; der Text ist ein fertiger deutscher Satz."""

    # Weder Adresse noch Antworttext des Servers im Satz: Eine Adresse aus der Antwort käme vom fremden Server, und
    # die eingetragene kann Zugangsdaten tragen (`https://name:passwort@...`).
    readable = True


def redirect_text() -> str:
    return (
        "ntfy leitet auf eine andere Adresse um. Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
        "Meist ist http:// statt https:// eingetragen (oder umgekehrt), oder ein Proxy davor leitet um. "
        "Trage in den Einstellungen die endgültige Adresse des ntfy-Servers ein."
    )


def status_error(status_code: int) -> str | None:
    """Lesbarer Satz zu einer Antwort, die kein Erfolg (2xx) ist; `None` bei 2xx."""
    if 200 <= status_code < 300:
        return None
    if 300 <= status_code < 400:
        # Das Ziel der Weiterleitung wird nie genannt: Der `Location`-Header kommt vom fremden Server.
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
        _ = httpx.URL(url).host  # `.host` entschlüsselt Punycode und scheitert an einem kaputten Namen
    except (httpx.InvalidURL, ValueError):
        return False
    return True


# Schritte, in denen httpx eine Weiterleitung zusammenbaut.
_REDIRECT_STEPS = frozenset(
    {"_build_redirect_request", "_redirect_url", "_redirect_method", "_redirect_headers", "_redirect_stream"}
)


def _came_from_redirect(exc: BaseException) -> bool:
    """Ob `exc` beim Zusammenbauen einer Weiterleitung entstand (erkannt im Traceback, ersatzweise am Wort „location“)."""
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_name in _REDIRECT_STEPS:
            return True
        tb = tb.tb_next
    return "location" in str(exc).lower()


def unusable_exchange_text(exc: BaseException, url: str) -> str:
    """Fester Satz für Protokoll- und Adressfehler von httpx, nie deren Text."""
    # Der Text der Ausnahme bleibt draußen: h11 zitiert kaputte Zeilen der Antwort (auch einen kaputten `Location`-Header)
    # und bei der eigenen Anfrage den kaputten Header samt Wert, also auch das Zugriffstoken (`Illegal header value
    # b'Bearer ...'`); ein ungültiger Punycode-Name in der Weiterleitung nennt Teile des fremden Namens.
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
    """ntfy-Priorität (1 bis 5) je Stufe aus den Einstellungen."""
    out: dict[Severity, int] = {}
    # Ein fehlender oder unbekannter Wert gilt wie die feste Zuordnung (Info 3, Warnung 4, Kritisch 5): Eine Meldung
    # wird nie wegen einer kaputten Einstellung leiser.
    for severity, key in _PRIORITY_SETTING.items():
        raw = settings.get(key)
        name = raw.strip().lower() if isinstance(raw, str) else ""
        out[severity] = _PRIORITY_NUMBERS.get(name, _PRIORITY_BY_SEVERITY[severity])
    return out


def min_severity_from_settings(settings: Mapping[str, Any]) -> Severity:
    """Die eingestellte Mindest-Stufe; fehlt sie oder ist sie unbekannt, gilt `info`."""
    # Lieber eine Meldung zu viel als ein Alarm, der wegen eines Tippfehlers nie ankommt.
    raw = settings.get("min_severity")
    try:
        return Severity(raw.strip().lower()) if isinstance(raw, str) else Severity.INFO
    except ValueError:
        return Severity.INFO


def is_below_minimum(notification: Notification, settings: Mapping[str, Any]) -> bool:
    """Ob die Meldung unter der Mindest-Stufe liegt und deshalb nicht an ntfy geht."""
    # Die Testnachricht der Einstellungsseite geht immer raus: Wer sie schickt, will sehen, dass es klappt.
    if (notification.payload or {}).get("test") is True:
        return False
    return _SEVERITY_RANK.get(notification.severity, 0) < _SEVERITY_RANK[min_severity_from_settings(settings)]


# Name, unter dem die ntfy-App das abonnierte Thema anzeigt (`display=` im Link).
_SUBSCRIBE_NAME = "Nodvard Deck"


def build_subscribe_link(server_url: Any, topic: Any) -> tuple[str | None, str | None]:
    """Link, mit dem die ntfy-App ein Thema abonniert: `ntfy://<host[:port][/pfad]>/<thema>`.

    Liefert `(Link, None)` oder `(None, Satz)`; der Satz ist fertiges Deutsch."""
    # Der Link steht auf der Einstellungsseite und im QR-Code: Nur Adresse, Port, Pfad und Thema kommen hinein, nie
    # Zugangsdaten aus der eingetragenen Adresse (`https://name:passwort@...`), Abfrage, Fragment oder das Zugriffstoken.
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
    # Bei `http://` braucht die App `secure=false`, sonst nimmt sie https an (docs.ntfy.sh/subscribe/phone).
    query = {"secure": "false"} if parts.scheme == "http" else {}
    query["display"] = _SUBSCRIBE_NAME
    return f"ntfy://{authority}{path}/{quote(name, safe='')}?{urlencode(query, quote_via=quote)}", None


def build_message(
    notification: Notification, topic: str, dashboard_url: str | None, priorities: Mapping[Severity, int] | None = None
) -> dict:
    """ntfy-JSON für eine Benachrichtigung; `priorities` je Stufe, ohne Angabe die feste Zuordnung.

    Optional im `payload`: `path` (Seite, die ein Tipp öffnet), `actions` (bis zu drei Knöpfe `[{label, path}]`), `tags`."""
    # Die JSON-API statt der Header-API: Header sind auf ASCII/Latin-1 beschränkt, Umlaute im Titel kämen nicht sicher an.
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

# Letzter Fehler je weiterem Kanal (Name -> (Abdruck der Adresse, fester Satz)), nur im Speicher: „Verbindung testen“
# nennt ihn, auch wenn die Zustellung insgesamt klappte. Ein Erfolg löscht den Eintrag, eine neue Adresse macht ihn ungültig.
_last_failures: dict[str, tuple[str, str]] = {}


class ChannelsFailed(NtfyError):
    """Fehler mehrerer Kanäle, ein fertiger Satz je Kanal."""

    # Der Kern zeigt den Text unverändert; seine Übersetzung nähme sonst den ersten HTTP-Code darin und nennte weder
    # Kanal noch die anderen Fehler.
    verbatim = True


def ntfy_configured(settings: Mapping[str, Any]) -> bool:
    return bool(settings.get("server_url")) and bool(settings.get("topic"))


def channel_entries(settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Die eingeschalteten weiteren Kanäle aus den Einstellungen (Einträge ohne gültigen Namen zählen nicht)."""
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
    """Erfüllt `nodvard_sdk.capabilities.NotificationChannel`."""

    # Alle weiteren Kanäle laufen als EIN Kanal für den Kern: Er meldet den Zustellstatus je Kanal-Objekt, und Kanäle
    # lassen sich nur beim Start anmelden, die Liste in den Einstellungen ändert sich aber jederzeit.
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
        """Adresse eines weiteren Kanals aus dem Tresor samt Abdruck; `ChannelError`, wenn sie fehlt oder nicht passt."""
        label = secret_label(name)
        if not await self._ctx.secrets.exists(label):
            raise ChannelError(f"Für „{name}“ fehlt noch die Adresse des Kanals (Einstellungen, unten unter „Zugangsdaten“).")
        handle = await self._ctx.secrets.get_handle(label)
        async with self._ctx.vault_use(handle) as url:
            return parse_channel_url(url), fingerprint(url)

    async def _send_extra(
        self, item: Mapping[str, Any], notification: Notification, dashboard_url: str | None
    ) -> tuple[str, str | None]:
        """Schickt an einen weiteren Kanal: `("sent", Teilfehler oder None)` oder `("skipped", None)`,
        sonst `ChannelError`."""
        if below_minimum(notification, item.get("min_severity")):
            return "skipped", None
        name = str(item["name"])
        target, mark = await self._target(name)
        try:
            partial = await send_to(self._ctx.http, target, notification, dashboard_url)
        except ChannelError as exc:
            _last_failures[name] = (mark, str(exc))  # für „Verbindung testen“
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
        """Die Antwort oder `NtfyError` mit festem Satz, wenn Antwort, Weiterleitung oder eigene Anfrage unbrauchbar sind."""
        problem: str
        try:
            # httpx baut die Weiterleitung auch dann, wenn es ihr nicht folgt, und scheitert dort an einem kaputten
            # `Location`-Header.
            return await self._ctx.http.request(method, url, **kwargs)
        except (httpx.ProtocolError, httpx.InvalidURL, ValueError) as exc:
            problem = unusable_exchange_text(exc, url)
        # Außerhalb des `except`-Blocks: Sonst hinge die Ausnahme von httpx als `__context__` daran, und jeder Fehlertext,
        # der die Ursachenkette abschreibt, trüge ihren Text mit.
        raise NtfyError(problem)

    async def send(self, notification: Notification) -> None:
        """Schickt die Meldung an ntfy und alle weiteren Kanäle, jeden mit eigener Frist (`CHANNEL_TIMEOUT_S`)."""
        settings = await self._ctx.settings.get()
        extras = channel_entries(settings)
        if not extras:
            # Ohne Einrichtung wirft `_config` auch dann, wenn die Meldung sowieso übersprungen würde: Ein nicht
            # eingerichteter Kanal soll nie wie ein gesunder aussehen.
            self._config(settings)
            if is_below_minimum(notification, settings):
                # `NotificationSkipped` statt Fehler: Der Kern verbucht „übersprungen“, versucht nichts erneut, und keine
                # Seite behauptet „zugestellt“. Unter „Meldungen“ steht die Meldung trotzdem.
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
        # Ein ausgefallener Teilkanal kippt die Zustellung nur, wenn gar nichts ankam; die Testnachricht nennt jeden
        # Fehler, sonst stehen sie im Log und bei „Verbindung testen“.
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
        """`(wer, "sent"|"skipped"|"failed", Satz oder None)`; wirft nie und nennt nie fremden Text (er kann die Adresse samt
        Token tragen)."""
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
        """Fragt den ntfy-Server, ohne etwas zu senden; die Adressen weiterer Kanäle prüft `health()`."""
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
    """Antwort von `GET /ext/ntfy/subscribe-link`: `url` steckt im QR-Code, `text` sagt, was man damit tut, `error` ist
    ein fertiger Satz, wenn sich kein Link berechnen lässt (dann sind `url` und `text` leer)."""

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
    """Antwort von `GET /ext/ntfy/subscribe-link` zu den gespeicherten Einstellungen; wer nur weitere Kanäle nutzt, bekommt
    einen ruhigen Hinweis statt „es fehlt noch ...“."""
    if not settings.get("server_url") and not settings.get("topic") and channel_entries(settings):
        return SubscribeLinkOut(error=_ONLY_CHANNELS_HINT)
    url, error = build_subscribe_link(settings.get("server_url"), settings.get("topic"))
    if url is None:
        return SubscribeLinkOut(error=error)
    return SubscribeLinkOut(url=url, text=_SUBSCRIBE_HINT)


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        # Telegram-Token, Slack-Pfad und Discord-Token stehen in der Adresse der Anfrage, und httpx schreibt jede Anfrage
        # samt Adresse auf INFO: Auch bei tieferer Log-Einstellung sollen die Zugangsdaten nicht im Protokoll landen.
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
            """Legt das optionale Zugriffstoken im Tresor ab."""
            # `ctx.secrets` kennt keine Rotation: Ein gesetztes Token muss erst über `DELETE /api/v1/secrets/{id}` weg.
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
            """Link zum Abonnieren für den QR-Code der Einstellungsseite (`x-qr-url` im Schema).

            Rechnet aus den gespeicherten Einstellungen, ohne Token oder anderes Geheimnis; fehlt etwas, kommt `error`."""
            return subscribe_link_for(await ctx.settings.get())

        ctx.api.include_router(link_router, permission="extensions.manage")

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def on_stop(self, ctx: ExtensionContext) -> None:
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        """Ohne weitere Kanäle nur ntfy, sonst ein Eintrag je Kanal (`details`), alle nebeneinander."""
        # Geprüft wird ohne zu senden (`channels.probe`); wo der Dienst das nicht erlaubt (Slack, Gotify, Mattermost),
        # steht das in `note`, und ein Fehler beim letzten Senden zählt mit.
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
    """Ein weiterer Kanal für „Verbindung testen“."""
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
        # Bestätigt der Dienst Token und Empfänger, ist ein Fehler vom letzten Senden erledigt; lässt er sich nicht so
        # prüfen, zählt der Fehler weiter, solange die Adresse gleich ist.
        last = _last_failures.get(name)
        if note is None or (last is not None and last[0] != mark):
            _last_failures.pop(name, None)
        elif last is not None:
            problem = f"Beim letzten Senden: {last[1]}"
            verbatim = True
    if problem is not None:
        # `verbatim`: Der Kern zeigt die Sätze unverändert, sonst nähme seine Übersetzung nur den HTTP-Code darin (aus
        # „Bot nicht im Chat“ würde „dem Konto fehlt ein Recht“).
        return name, {"healthy": False, "error": problem, **({"verbatim": True} if verbatim else {})}
    entry: dict[str, Any] = {"healthy": True, "kind": label}
    if note:
        entry["note"] = note
    return name, entry
