"""Vaultwarden (Passwort-Manager): läuft es, welche Version, HTTPS und Registrierung. Nur lesen, ohne Anmeldung.

Die Bitwarden-Apps sprechen direkt mit Vaultwarden; Nodvard Deck sieht nie ein Master-Passwort und nie einen Eintrag."""

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

from .service import VaultwardenService
from .suggestions import VaultwardenAppSuggestions
from .widgets import status_rows, widget_state

_WIDGET_ITEM = ListItem(title="{{ title }}", subtitle="{{ subtitle }}", badge=Badge(text="{{ label }}", tone="{{ tone }}"))


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._service = VaultwardenService(ctx)
        service = self._service
        ctx.capabilities.provide(VaultwardenAppSuggestions(ctx))
        ctx.settings.declare(
            {"type": "object", "properties": {"url": {"type": "string"}, "tls_insecure_skip_verify": {"type": "boolean"}}}
        )

        read_router = APIRouter()

        @read_router.get("/summary")
        async def summary() -> dict[str, Any]:
            return await service.status()

        @read_router.get("/widgets/status")
        async def status_widget() -> dict[str, Any]:
            state = await service.status()
            return {"data": status_rows(state), "meta": {"state": widget_state(state)}}

        ctx.api.include_router(read_router, permission="hosts.read")
        ctx.ui.register_page(
            PageSpec(
                id="vaultwarden",
                path="/vaultwarden",
                title="Vaultwarden",
                icon="key-round",
                nav_section="Sicherheit",
                nav_order=30,
                permissions=["hosts.read"],
                component="VaultwardenPage",
            )
        )
        ctx.ui.register_widget(
            WidgetSpec(
                id="status",
                title="Vaultwarden",
                icon="key-round",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=300),
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
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        try:
            state = await self._service.status()
        except Exception as exc:  # noqa: BLE001 - Health darf nie werfen
            return HealthReport(healthy=False, message=str(exc))
        if state["state"] == "ok":
            return HealthReport(healthy=True, message="In Ordnung")
        message = state["message"] or "Vaultwarden antwortet nicht."
        return HealthReport(healthy=False, message=message, details={"state": state["state"]})
