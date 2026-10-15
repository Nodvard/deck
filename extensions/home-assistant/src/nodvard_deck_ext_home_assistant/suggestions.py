"""Vorschläge für App-Kacheln aus den eingetragenen Adressen (`nodvard_sdk.AppSuggestionProvider`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nodvard_sdk import AppSuggestion

if TYPE_CHECKING:
    from .service import HomeAssistantService

SOURCE = "Home Assistant"
NAME = "Home Assistant"


class HomeAssistantAppSuggestions:
    def __init__(self, service: HomeAssistantService) -> None:
        self._service = service

    async def app_suggestions(self) -> list[AppSuggestion]:
        # Nur Wissen aus den Einstellungen: keine Anfrage, das Token wird nicht gelesen.
        configs = [c for c in await self._service.configs() if c.base_url]
        many = len(configs) > 1
        return [
            AppSuggestion(name=f"{NAME} {c.name}" if many else NAME, url=c.base_url or "", source=SOURCE)
            for c in configs
        ]
