"""Vorschlag für eine App-Kachel: die Verwaltung von Pi-hole (`<adresse>/admin`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nodvard_sdk import AppSuggestion

from .config import NotConfigured, pihole_config

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

SOURCE = "Pi-hole"


class PiholeAppSuggestions:
    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def app_suggestions(self) -> list[AppSuggestion]:
        try:
            config = pihole_config(await self._ctx.settings.get())
        except NotConfigured:
            return []
        return [AppSuggestion(name="Pi-hole", url=f"{config.url}/admin/", source=SOURCE)]
