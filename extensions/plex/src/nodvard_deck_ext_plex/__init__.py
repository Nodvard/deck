"""Plex-Erweiterung: zeigt laufende Wiedergaben, Bibliotheken und neue Medien eines Plex-Servers (nur lesend)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from nodvard_sdk import (
    Badge,
    ExtensionContext,
    GridSize,
    HealthReport,
    ListItem,
    ListView,
    NodvardExtension,
    PageSpec,
    Refresh,
    WidgetSpec,
)

from .config import SETTINGS_HINT
from .models import Overview
from .service import PlexService
from .suggestions import PlexAppSuggestions
from .widgets import nothing_set_up, status_rows

_WIDGET_ITEM = ListItem(title="{{ title }}", subtitle="{{ subtitle }}", badge=Badge(text="{{ label }}", tone="{{ tone }}"))

NO_CONNECTION = f"Plex ist noch nicht eingerichtet. {SETTINGS_HINT}"


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._service = PlexService(ctx)
        service = self._service

        # Die eingetragenen Adressen als Vorschläge für App-Kacheln.
        ctx.capabilities.provide(PlexAppSuggestions(service))

        ctx.settings.declare(
            {
                "type": "object",
                "properties": {
                    "connections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "base_url": {"type": "string"},
                                "tls_fingerprint_sha256": {"type": "string"},
                                "tls_insecure_skip_verify": {"type": "boolean"},
                            },
                        },
                    },
                },
            }
        )

        router = APIRouter()

        @router.get("/overview", response_model=Overview)
        async def overview(refresh: bool = False) -> Overview:
            # `refresh=true` überspringt den Zwischenspeicher, nicht die Pause nach einem abgelehnten Token.
            # Probleme stehen je Verbindung in `state`, nicht im Statuscode.
            return await service.overview(refresh=refresh)

        @router.get("/widgets/status")
        async def status_widget() -> dict[str, Any]:
            data = await service.overview()
            meta: dict[str, Any] = {
                "connections": data.summary.connections,
                "playing": data.summary.playing,
                "transcoding": data.summary.transcoding,
            }
            if nothing_set_up(data):
                meta["state"] = "not_configured"
            return {"data": status_rows(data), "meta": meta}

        ctx.api.include_router(router, permission="hosts.read")

        ctx.ui.register_page(
            PageSpec(
                id="plex",
                path="/plex",
                title="Plex",
                icon="monitor",
                nav_section="Infrastruktur",
                nav_order=80,
                permissions=["hosts.read"],
                component="PlexPage",
            )
        )
        ctx.ui.register_widget(
            WidgetSpec(
                id="status",
                title="Plex",
                icon="monitor",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=60),
                data_endpoint="widgets/status",
                permissions=["hosts.read"],
                view=ListView(item=_WIDGET_ITEM, empty_text="Keine Daten"),
            )
        )

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def on_stop(self, ctx: ExtensionContext) -> None:
        return None

    async def on_settings_changed(self, ctx: ExtensionContext, values: dict[str, Any]) -> None:
        self._service.clear()

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        # „Verbindung testen“ prüft jede Verbindung mit `GET /` und leert den Zwischenspeicher samt Pause.
        try:
            results = await self._service.test_connections()
        except Exception as exc:  # noqa: BLE001 - Health darf nie werfen
            return HealthReport(healthy=False, message=f"Plex ließ sich nicht abfragen ({type(exc).__name__}).")
        if not results:
            return HealthReport(healthy=False, message=NO_CONNECTION)
        message = "; ".join(f"{name}: {'in Ordnung' if r['healthy'] else r['error']}" for name, r in results.items())
        return HealthReport(healthy=all(r["healthy"] for r in results.values()), message=message, details=dict(results))
