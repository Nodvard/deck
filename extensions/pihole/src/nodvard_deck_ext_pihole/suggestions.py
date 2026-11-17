"""Vorschlag für eine App-Kachel: die Verwaltung von Pi-hole (`<adresse>/admin`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nodvard_sdk import AppSuggestion

from .config import NotConfigured, pihole_config, shown_url

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

SOURCE = "Pi-hole"


class PiholeAppSuggestions:
    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def app_suggestions(self) -> list[AppSuggestion]:
        try:
            settings = await self._ctx.settings.get()
            config = pihole_config(settings)
        except NotConfigured:
            return []
        url = shown_url(settings.get("url"), config.url)
        return [] if url is None else [AppSuggestion(name="Pi-hole", url=f"{url}/admin/", source=SOURCE)]
