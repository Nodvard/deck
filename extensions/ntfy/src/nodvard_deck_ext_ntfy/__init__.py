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
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, status
from nodvard_sdk import ConnectorHealth, ExtensionContext, HealthReport, NodvardExtension, Notification, Severity
from pydantic import BaseModel

_TOKEN_LABEL = "ntfy-token"

_PRIORITY_BY_SEVERITY = {
    Severity.INFO: 3,
    Severity.WARNING: 4,
    Severity.CRITICAL: 5,
}

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


def build_message(notification: Notification, topic: str, dashboard_url: str | None) -> dict:
    """ntfy-JSON fuer eine Benachrichtigung. Konventionen im `payload` (alle optional):
    `path` -- Seite im Dashboard, die ein Tipp auf die Nachricht oeffnet;
    `actions` -- bis zu drei Knoepfe `[{label, path}]`; `tags` -- eigene ntfy-Symbole."""
    payload = notification.payload or {}
    body: dict = {
        "topic": topic,
        "title": notification.title,
        "message": notification.body,
        "priority": _PRIORITY_BY_SEVERITY.get(notification.severity, 3),
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


class _NtfyChannel:
    """Erfuellt `nodvard_sdk.capabilities.NotificationChannel`."""

    channel_id = "ntfy"
    label = "ntfy"

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def _config(self) -> tuple[str, str]:
        settings = await self._ctx.settings.get()
        server_url = settings.get("server_url")
        topic = settings.get("topic")
        if not server_url or not topic:
            raise RuntimeError("ntfy ist noch nicht eingerichtet: Es fehlen die Adresse des ntfy-Servers und das Thema (Einstellungen, Erweiterungen, ntfy).")
        return str(server_url).rstrip("/"), str(topic)

    async def _dashboard_url(self) -> str | None:
        url = ((await self._ctx.settings.get()).get("dashboard_url") or "").strip()
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
        server_url, topic = await self._config()
        headers = await self._auth_header()
        body = build_message(notification, topic, await self._dashboard_url())
        response = await self._request("POST", server_url + "/", json=body, headers=headers)
        problem = status_error(response.status_code)
        if problem:
            raise NtfyError(problem)

    async def test(self) -> ConnectorHealth:
        try:
            server_url, _ = await self._config()
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


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        ctx.capabilities.provide(_NtfyChannel(ctx))
        ctx.settings.declare(
            {
                "type": "object",
                "properties": {"server_url": {"type": "string"}, "topic": {"type": "string"}, "dashboard_url": {"type": "string"}},
                "required": ["server_url", "topic"],
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

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def on_stop(self, ctx: ExtensionContext) -> None:
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        channel = _NtfyChannel(ctx)
        result = await channel.test()
        return HealthReport(healthy=result.ok, message=result.message)
