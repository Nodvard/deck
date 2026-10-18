"""Vorschlag für eine App-Kachel aus der eingetragenen Adresse (`nodvard_sdk.AppSuggestionProvider`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nodvard_sdk import AppSuggestion

if TYPE_CHECKING:
    from .service import SynologyService

NAME = "Synology DSM"


class SynologyAppSuggestions:
    def __init__(self, service: SynologyService) -> None:
        self._service = service

    async def app_suggestions(self) -> list[AppSuggestion]:
        # Nur Wissen aus den Einstellungen: keine Anfrage, das Passwort wird nicht gelesen.
        config = await self._service.config()
        if not config.base_url:
            return []
        return [AppSuggestion(name=NAME, url=config.base_url, source=NAME)]
