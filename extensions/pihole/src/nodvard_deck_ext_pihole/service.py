"""Hält einen Pi-hole-Client (Sitzung) über alle Aufrufe hinweg. `status()` wirft nie: Fehler sind Zustände (`state`)
mit deutscher Meldung, keine 500er."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from .client import PiholeClient, PiholeError
from .config import (
    PIHOLE_SECRET,
    InvalidSetting,
    NotConfigured,
    PiholeConfig,
    pihole_config,
)

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext


class PiholeService:
    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._pihole: tuple[PiholeConfig, PiholeClient] | None = None
        # Seite und Kachel fragen gleichzeitig; ohne Sperre baute jeder Aufruf einen eigenen Client, und die Sitzung des
        # verworfenen bliebe offen (Pi-hole hat nur wenige API-Plätze).
        self._lock = asyncio.Lock()

    async def _password(self) -> str | None:
        if not await self._ctx.secrets.exists(PIHOLE_SECRET):
            return None
        handle = await self._ctx.secrets.get_handle(PIHOLE_SECRET)
        async with self._ctx.vault_use(handle) as value:
            return value

    async def client(self) -> PiholeClient:
        settings = await self._ctx.settings.get()
        async with self._lock:
            try:
                config = pihole_config(settings)
            except NotConfigured:
                await self._drop()
                raise
            if self._pihole is None or self._pihole[0] != config:
                await self._drop()
                client = PiholeClient(self._ctx.http, base_url=config.url, password=self._password, insecure_tls=config.insecure_tls)
                self._pihole = (config, client)
            return self._pihole[1]

    async def configured(self) -> bool:
        try:
            pihole_config(await self._ctx.settings.get())
        except NotConfigured:
            return False
        return True

    async def _drop(self) -> None:
        if self._pihole is not None:
            _, client = self._pihole
            self._pihole = None
            await client.logout()

    async def drop(self) -> None:
        """Meldet die gespeicherte Sitzung ab; der nächste Aufruf meldet sich mit dem aktuellen Passwort neu an."""
        # Der Client bleibt: Eine Seite, die ihn gerade benutzt, meldet sich an ihm neu an, statt an einem verworfenen
        # Client eine zweite Sitzung zu öffnen, die niemand mehr abmeldet.
        async with self._lock:
            if self._pihole is not None:
                await self._pihole[1].logout()

    async def refresh(self) -> None:
        """Nach dem Speichern der Einstellungen: eine alte Sitzung sofort abmelden."""
        try:
            await self.client()
        except NotConfigured:
            pass

    async def close(self) -> None:
        async with self._lock:
            await self._drop()

    async def status(self) -> dict[str, Any]:
        """Für Seite und Kachel: `state`, `message`, `url`, `summary`, `blocking`, `top_clients`."""
        try:
            client = await self.client()
        except NotConfigured as exc:
            return {
                "state": "not_configured", "message": str(exc), "invalid": isinstance(exc, InvalidSetting), "url": None,
                "summary": None, "blocking": None, "top_clients": None,
            }
        result: dict[str, Any] = {
            "state": "ok", "message": None, "url": client.base_url, "summary": None, "blocking": None, "top_clients": None,
        }
        try:
            result["summary"] = await client.summary()
            result["blocking"] = await client.blocking()
            result["top_clients"] = await client.top_clients()
        except PiholeError as exc:
            result.update(state=exc.state, message=str(exc), summary=None, blocking=None, top_clients=None)
        return result
