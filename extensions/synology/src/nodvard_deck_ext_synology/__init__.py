"""Synology-Erweiterung: zeigt Modell, DSM-Version, Auslastung, Speicher, Hyper Backup und Updates von einer oder mehreren
Synology und meldet Probleme (nur lesend)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from nodvard_sdk import (
    ExtensionContext,
    HealthReport,
    NodvardExtension,
    PageSpec,
    WidgetSpec,
)

from .config import DeviceConfig, device_of
from .legacy import adopt_single_device, forget_legacy_password
from .models import DeviceList, Overview
from .service import NOT_CONFIGURED, SynologyService, UnknownDevice
from .suggestions import SynologyAppSuggestions
from .tiles import KINDS, tile_specs
from .transport import SERVICE, install_log_filter
from .watch import WatchJob
from .widgets import backup_rows, gone_rows, status_rows, volume_rows, widget_state

logger = logging.getLogger(__name__)


def _status_tile(data: Overview, many: bool) -> dict[str, Any]:
    meta: dict[str, Any] = {"state": widget_state(data)}
    if data.state == "ok":
        meta["problems"] = sum(1 for p in data.problems if p.severity != "info")
    if data.state == "ok" and data.system is not None:
        meta["model"] = data.system.model
        meta["dsm_version"] = data.system.dsm_version
    if data.state == "ok" and data.update is not None:
        meta["update_available"] = data.update.available
    return {"data": status_rows(data, many), "meta": meta}


def _volumes_tile(data: Overview, many: bool) -> dict[str, Any]:
    meta: dict[str, Any] = {"state": widget_state(data)}
    if data.volumes is not None:
        meta["volumes"] = len(data.volumes)
    return {"data": volume_rows(data, many), "meta": meta}


def _backups_tile(data: Overview, many: bool) -> dict[str, Any]:
    meta: dict[str, Any] = {"state": widget_state(data)}
    if data.backups is not None:
        meta["tasks"] = len(data.backups)
    return {"data": backup_rows(data, many), "meta": meta}


_TILES: dict[str, Callable[[Overview, bool], dict[str, Any]]] = {
    "status": _status_tile, "volumes": _volumes_tile, "backups": _backups_tile,
}


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._tiles: dict[str, WidgetSpec] = {}
        # DSM nimmt die Sitzungs-ID in der Adresse; httpx würde sie sonst bei INFO ins Protokoll schreiben.
        install_log_filter()
        self._service = SynologyService(ctx)
        service = self._service

        # Das Einzelgerät von früher wird das erste Gerät; scheitert das, bleibt das Modul trotzdem ladbar.
        try:
            await adopt_single_device(ctx)
        except Exception:  # setup() darf daran nicht scheitern
            logger.warning("Synology: das bisherige Gerät ließ sich nicht übernehmen.", exc_info=True)
        # Bei ausgeschaltetem Modul läuft on_settings_changed nicht; ein dabei entferntes Gerät räumt erst der Start auf.
        try:
            await forget_legacy_password(ctx, await ctx.settings.get())
        except Exception:  # setup() darf daran nicht scheitern
            logger.warning("Synology: der alte Passwort-Eintrag ließ sich nicht löschen.", exc_info=True)

        # Die eingetragenen Adressen als Vorschläge für App-Kacheln.
        ctx.capabilities.provide(SynologyAppSuggestions(service))

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
                                "username": {"type": "string"},
                                "tls_insecure_skip_verify": {"type": "boolean"},
                                "tls_fingerprint_sha256": {"type": "string"},
                            },
                        },
                    },
                    "notify": {"type": "boolean"},
                    "notify_update": {"type": "boolean"},
                    "disk_temp_warn_c": {"type": "integer"},
                },
            }
        )

        router = APIRouter()

        @router.get("/overview", response_model=Overview)
        async def overview(refresh: bool = False, device: str | None = None) -> Overview:
            # Ohne `device` gilt das erste Gerät. `refresh=true` überspringt den Zwischenspeicher, nicht die Pause
            # nach einer abgelehnten Anmeldung. Probleme stehen in `state` und in den Hinweisen je Teil, nicht im Statuscode.
            try:
                return await service.overview(device, refresh=refresh)
            except UnknownDevice:
                raise HTTPException(404, f"Ein Gerät mit dem Kurznamen „{(device or '')[:40]}“ ist nicht eingetragen.") from None

        @router.get("/devices", response_model=DeviceList)
        async def devices(refresh: bool = False) -> DeviceList:
            return DeviceList(devices=await service.overviews(refresh=refresh))

        def add_tile_routes(kind: str) -> None:
            build = _TILES[kind]

            async def first_device() -> dict[str, Any]:
                return build(await service.overview(), await service.many())

            async def named_device(device: str) -> dict[str, Any]:
                try:
                    data = await service.overview(device)
                except UnknownDevice:
                    # Eine Kachel, die nach dem Entfernen des Geräts noch offen ist, blendet sich damit selbst aus.
                    return {"data": gone_rows(device[:40]), "meta": {"state": "not_configured"}}
                return build(data, await service.many())

            # Der Name der Route bleibt der von früher, damit die Kennung in der OpenAPI-Beschreibung dieselbe bleibt.
            router.add_api_route(f"/widgets/{kind}", first_device, methods=["GET"], name=f"{kind}_widget")
            router.add_api_route(f"/widgets/{kind}/{{device}}", named_device, methods=["GET"], name=f"{kind}_widget_of_device")

        for kind, *_ in KINDS:
            add_tile_routes(kind)

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
        self._sync_tiles(await service.configs())

        # Alle fünf Minuten: meldet, wenn etwas kippt und wenn es wieder in Ordnung ist.
        await ctx.scheduler.register_job(WatchJob(ctx, service))

    def _sync_tiles(self, configs: list[DeviceConfig]) -> None:
        """Legt die Kacheln zu den eingetragenen Geräten an, ändert geänderte und nimmt die zu entfernten Geräten weg."""
        wanted = {spec.id: spec for spec in tile_specs(configs)}
        for gone in [spec_id for spec_id in self._tiles if spec_id not in wanted]:
            self._ctx.ui.unregister_widget(gone)
        for spec_id, spec in wanted.items():
            if self._tiles.get(spec_id) != spec:
                self._ctx.ui.register_widget(spec)
        self._tiles = wanted

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def on_stop(self, ctx: ExtensionContext) -> None:
        service = getattr(self, "_service", None)
        if service is not None:
            await service.shutdown()

    async def on_settings_changed(self, ctx: ExtensionContext, values: dict[str, Any]) -> None:
        service = getattr(self, "_service", None)
        if service is None:
            return
        try:
            await forget_legacy_password(ctx, values)
        except Exception:  # das Aufräumen darf das Übrige nicht aufhalten
            logger.warning("Synology: der alte Passwort-Eintrag ließ sich nicht löschen.", exc_info=True)
        await service.settings_changed()
        self._sync_tiles(await service.configs())

    async def on_secret_changed(self, ctx: ExtensionContext, label: str) -> None:
        # Sitzung und Pause von vorher gälten sonst weiter, bis jemand die Angaben des Geräts ändert.
        service = getattr(self, "_service", None)
        name = device_of(label)
        if service is not None and name is not None:
            await service.forget(name)

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        # „Verbindung testen“ meldet jedes Gerät neu an und leert den Zwischenspeicher samt Pausen.
        try:
            results = await self._service.test_connections()
        except Exception as exc:  # noqa: BLE001 - Health darf nie werfen
            return HealthReport(healthy=False, message=f"{SERVICE} ließ sich nicht abfragen ({type(exc).__name__}).")
        if not results:
            return HealthReport(healthy=False, message=NOT_CONFIGURED)
        message = "; ".join(f"{name}: {'in Ordnung' if r['healthy'] else r['error']}" for name, r in results.items())
        return HealthReport(healthy=all(r["healthy"] for r in results.values()), message=message, details=dict(results))
