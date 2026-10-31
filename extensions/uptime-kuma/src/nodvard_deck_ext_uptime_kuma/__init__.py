"""Uptime-Kuma-Erweiterung: zeigt den Zustand der Monitore einer Uptime-Kuma-Installation (nur lesend)."""

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

from .config import connection_of
from .models import Overview
from .service import UptimeKumaService
from .widgets import nothing_set_up, status_rows

_WIDGET_ITEM = ListItem(title="{{ title }}", subtitle="{{ subtitle }}", badge=Badge(text="{{ label }}", tone="{{ tone }}"))

NO_CONNECTION = "Noch keine Verbindung – unter Einstellungen → Erweiterungen → Uptime Kuma eintragen."


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._service = UptimeKumaService(ctx)
        service = self._service

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
                                "status_page_slug": {"type": "string"},
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
            # `refresh=true` überspringt den Zwischenspeicher.
            return await service.overview(refresh=refresh)

        @router.get("/widgets/status")
        async def status_widget() -> dict[str, Any]:
            data = await service.overview()
            meta: dict[str, Any] = {"connections": len(data.connections), "total": data.summary.total, "down": data.summary.down}
            if nothing_set_up(data):
                meta["state"] = "not_configured"
            return {"data": status_rows(data), "meta": meta}

        ctx.api.include_router(router, permission="hosts.read")

        ctx.ui.register_page(
            PageSpec(
                id="uptime-kuma",
                path="/uptime-kuma",
                title="Uptime Kuma",
                icon="activity",
                nav_section="Infrastruktur",
                nav_order=50,
                permissions=["hosts.read"],
                component="UptimeKumaPage",
            )
        )
        ctx.ui.register_widget(
            WidgetSpec(
                id="status",
                title="Uptime Kuma",
                group="services",
                icon="activity",
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

    async def on_secret_changed(self, ctx: ExtensionContext, label: str) -> None:
        # Ein Ergebnis mit dem alten Schlüssel gälte sonst noch bis zu 20 Sekunden.
        name = connection_of(label)
        if name is not None:
            self._service.forget(name)

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        # „Verbindung testen“ fragt jetzt nach, nicht den Stand von vor ein paar Minuten.
        try:
            data = await self._service.overview(refresh=True)
        except Exception as exc:  # noqa: BLE001 - Health darf nie werfen
            return HealthReport(healthy=False, message=f"Uptime Kuma ließ sich nicht abfragen ({type(exc).__name__}).")
        if not data.connections:
            return HealthReport(healthy=False, message=NO_CONNECTION)
        details = {c.name: {"state": c.state, "message": c.message} for c in data.connections}
        message = "; ".join(f"{c.name}: {'in Ordnung' if c.state == 'ok' else c.message}" for c in data.connections)
        return HealthReport(healthy=all(c.state == "ok" for c in data.connections), message=message, details=details)
