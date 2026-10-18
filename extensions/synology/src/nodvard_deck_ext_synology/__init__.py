"""Synology-Erweiterung: zeigt Modell, DSM-Version, Auslastung, Speicher, Hyper Backup und Updates und meldet Probleme
(nur lesend)."""

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

from .models import Overview
from .service import SynologyService
from .suggestions import SynologyAppSuggestions
from .transport import SERVICE, install_log_filter
from .watch import WatchJob
from .widgets import backup_rows, status_rows, volume_rows, widget_state

_WIDGET_ITEM = ListItem(title="{{ title }}", subtitle="{{ subtitle }}", badge=Badge(text="{{ label }}", tone="{{ tone }}"))


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        # DSM nimmt die Sitzungs-ID in der Adresse; httpx würde sie sonst bei INFO ins Protokoll schreiben.
        install_log_filter()
        self._service = SynologyService(ctx)
        service = self._service

        # Die eingetragene Adresse als Vorschlag für eine App-Kachel.
        ctx.capabilities.provide(SynologyAppSuggestions(service))

        ctx.settings.declare(
            {
                "type": "object",
                "properties": {
                    "base_url": {"type": "string"},
                    "username": {"type": "string"},
                    "tls_insecure_skip_verify": {"type": "boolean"},
                    "tls_fingerprint_sha256": {"type": "string"},
                    "notify": {"type": "boolean"},
                    "notify_update": {"type": "boolean"},
                    "disk_temp_warn_c": {"type": "integer"},
                },
            }
        )

        router = APIRouter()

        @router.get("/overview", response_model=Overview)
        async def overview(refresh: bool = False) -> Overview:
            # `refresh=true` überspringt den Zwischenspeicher, nicht die Pause nach einer abgelehnten Anmeldung.
            # Probleme stehen in `state` und in den Hinweisen je Teil, nicht im Statuscode.
            return await service.overview(refresh=refresh)

        @router.get("/widgets/status")
        async def status_widget() -> dict[str, Any]:
            data = await service.overview()
            meta: dict[str, Any] = {"state": widget_state(data)}
            if data.state == "ok":
                meta["problems"] = sum(1 for p in data.problems if p.severity != "info")
            if data.state == "ok" and data.system is not None:
                meta["model"] = data.system.model
                meta["dsm_version"] = data.system.dsm_version
            if data.state == "ok" and data.update is not None:
                meta["update_available"] = data.update.available
            return {"data": status_rows(data), "meta": meta}

        @router.get("/widgets/volumes")
        async def volumes_widget() -> dict[str, Any]:
            data = await service.overview()
            meta: dict[str, Any] = {"state": widget_state(data)}
            if data.volumes is not None:
                meta["volumes"] = len(data.volumes)
            return {"data": volume_rows(data), "meta": meta}

        @router.get("/widgets/backups")
        async def backups_widget() -> dict[str, Any]:
            data = await service.overview()
            meta: dict[str, Any] = {"state": widget_state(data)}
            if data.backups is not None:
                meta["tasks"] = len(data.backups)
            return {"data": backup_rows(data), "meta": meta}

        ctx.api.include_router(router, permission="hosts.read")

        ctx.ui.register_page(
            PageSpec(
                id="synology",
                path="/synology",
                title=SERVICE,
                icon="server",
                nav_section="Infrastruktur",
                nav_order=100,
                permissions=["hosts.read"],
                component="SynologyPage",
            )
        )
        ctx.ui.register_widget(
            WidgetSpec(
                id="status",
                title=SERVICE,
                icon="server",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=60),
                data_endpoint="widgets/status",
                permissions=["hosts.read"],
                view=ListView(item=_WIDGET_ITEM, empty_text="Keine Daten"),
            )
        )
        ctx.ui.register_widget(
            WidgetSpec(
                id="volumes",
                title="Synology Speicher",
                icon="server",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=60),
                data_endpoint="widgets/volumes",
                permissions=["hosts.read"],
                view=ListView(item=_WIDGET_ITEM, empty_text="Keine Daten"),
            )
        )
        ctx.ui.register_widget(
            WidgetSpec(
                id="backups",
                title="Synology Hyper Backup",
                icon="database-backup",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=300),
                data_endpoint="widgets/backups",
                permissions=["hosts.read"],
                view=ListView(item=_WIDGET_ITEM, empty_text="Keine Daten"),
            )
        )

        # Alle fünf Minuten: meldet, wenn etwas kippt und wenn es wieder in Ordnung ist.
        await ctx.scheduler.register_job(WatchJob(ctx, service))

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def on_stop(self, ctx: ExtensionContext) -> None:
        service = getattr(self, "_service", None)
        if service is not None:
            await service.shutdown()

    async def on_settings_changed(self, ctx: ExtensionContext, values: dict[str, Any]) -> None:
        await self._service.clear()

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        # „Verbindung testen“ meldet neu an und leert den Zwischenspeicher samt Pause.
        try:
            result = await self._service.test_connection()
        except Exception as exc:  # noqa: BLE001 - Health darf nie werfen
            return HealthReport(healthy=False, message=f"{SERVICE} ließ sich nicht abfragen ({type(exc).__name__}).")
        if result["healthy"]:
            return HealthReport(healthy=True, message="in Ordnung", details={SERVICE: result})
        return HealthReport(healthy=False, message=str(result["error"]), details={SERVICE: result})
