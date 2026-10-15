"""Vorschlag für eine App-Kachel: der Web-Tresor von Vaultwarden."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nodvard_sdk import AppSuggestion

from .config import NotConfigured, vaultwarden_config

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

SOURCE = "Vaultwarden"


class VaultwardenAppSuggestions:
    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def app_suggestions(self) -> list[AppSuggestion]:
        try:
            config = vaultwarden_config(await self._ctx.settings.get())
        except NotConfigured:
            return []
        return [AppSuggestion(name="Vaultwarden", url=f"{config.url}/", source=SOURCE)]
