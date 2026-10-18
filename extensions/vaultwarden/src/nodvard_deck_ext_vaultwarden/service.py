"""Zustand von Vaultwarden für Seite, Kachel und Health; ein Lauf fragt den Server höchstens einmal je Aufruf."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .client import VaultwardenClient, VaultwardenError
from .config import InvalidSetting, NotConfigured, vaultwarden_config

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext


def _same_address(a: str, b: str) -> bool:
    return a.rstrip("/").lower() == b.rstrip("/").lower()


class VaultwardenService:
    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def status(self) -> dict[str, Any]:
        try:
            config = vaultwarden_config(await self._ctx.settings.get())
        except NotConfigured as exc:
            return {"state": "not_configured", "message": str(exc), "invalid": isinstance(exc, InvalidSetting)}
        base: dict[str, Any] = {"url": config.url, "https": config.https}
        try:
            info = await VaultwardenClient(self._ctx.http, config.url, insecure_tls=config.insecure_tls).info()
        except VaultwardenError as exc:
            return {"state": "unreachable", "message": str(exc), **base}
        domain = info["domain"]
        return {
            "state": "ok", "message": None, **base, **info,
            "domain_matches": None if domain is None else _same_address(domain, config.url),
        }
