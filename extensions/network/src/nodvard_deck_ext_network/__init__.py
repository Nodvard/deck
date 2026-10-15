"""Netzwerk: nur noch ein Wegweiser zu „Pi-hole“ und „Nginx Proxy Manager“. Die Kennung bleibt geladen, damit die Liste
der laufenden Module bei einem Update gleich bleibt und ein Rückweg die gespeicherte Zeile wiederfindet."""

from __future__ import annotations

from typing import Any

from nodvard_sdk import ExtensionContext, HealthReport, NodvardExtension, PageSpec


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        ctx.ui.register_page(
            PageSpec(
                id="network",
                path="/network",
                title="Netzwerk",
                icon="activity",
                nav_section="Infrastruktur",
                nav_order=40,
                permissions=["hosts.read"],
                component="NetworkPage",
            )
        )

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def on_stop(self, ctx: ExtensionContext) -> None:
        return None

    async def on_settings_changed(self, ctx: ExtensionContext, values: dict[str, Any]) -> None:
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        return HealthReport(healthy=True, message="Pi-hole und Nginx Proxy Manager sind eigene Module.")
