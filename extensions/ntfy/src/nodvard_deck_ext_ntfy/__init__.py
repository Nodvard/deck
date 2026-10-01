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
            raise RuntimeError("ntfy ist nicht konfiguriert (server_url/topic fehlen).")
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

    async def send(self, notification: Notification) -> None:
        server_url, topic = await self._config()
        headers = await self._auth_header()
        body = build_message(notification, topic, await self._dashboard_url())
        response = await self._ctx.http.post(server_url + "/", json=body, headers=headers)
        response.raise_for_status()

    async def test(self) -> ConnectorHealth:
        try:
            server_url, _ = await self._config()
        except RuntimeError as exc:
            return ConnectorHealth(ok=False, message=str(exc))
        try:
            response = await self._ctx.http.get(server_url + "/v1/health")
            return ConnectorHealth(ok=response.status_code < 400, message=f"HTTP {response.status_code}")
        except Exception as exc:  # noqa: BLE001 - Health-Check darf nie werfen, nur melden
            return ConnectorHealth(ok=False, message=str(exc))


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
