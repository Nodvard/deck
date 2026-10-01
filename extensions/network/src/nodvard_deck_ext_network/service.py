"""Haelt je Dienst EINEN Client (und damit die Pi-hole-Sitzung bzw. das NPM-Token)
ueber alle Aufrufe hinweg -- die Clients werden erst beim ersten Bedarf gebaut und neu
gebaut, sobald sich Adresse/Konto in den Einstellungen aendern.

`pihole_status()`/`npm_status()` werfen nie: "nicht eingerichtet", "nicht
erreichbar" usw. sind Zustaende (`state`) mit einer deutschen Meldung, keine 500er.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .config import (
    NPM_SECRET,
    PIHOLE_SECRET,
    NotConfigured,
    NpmConfig,
    PiholeConfig,
    npm_config,
    pihole_config,
)
from .npm import NpmClient, NpmError
from .pihole import PiholeClient, PiholeError

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext


class NetworkService:
    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._pihole: tuple[PiholeConfig, PiholeClient] | None = None
        self._npm: tuple[NpmConfig, NpmClient] | None = None
        # Seite und Kacheln fragen gleichzeitig -- ohne Sperre baute jeder Aufruf
        # seinen eigenen Client, und die Pi-hole-Sitzung des verworfenen bliebe offen.
        self._pihole_lock = asyncio.Lock()
        self._npm_lock = asyncio.Lock()

    def _secret(self, label: str):
        async def _get() -> str | None:
            if not await self._ctx.secrets.exists(label):
                return None
            handle = await self._ctx.secrets.get_handle(label)
            async with self._ctx.vault_use(handle) as value:
                return value

        return _get

    async def _settings(self) -> dict[str, Any]:
        return await self._ctx.settings.get()

    # -- Clients ---------------------------------------------------------------

    async def pihole_client(self) -> PiholeClient:
        settings = await self._settings()
        async with self._pihole_lock:
            try:
                config = pihole_config(settings)
            except NotConfigured:
                await self._drop_pihole()
                raise
            if self._pihole is None or self._pihole[0] != config:
                await self._drop_pihole()
                client = PiholeClient(
                    self._ctx.http, base_url=config.url, password=self._secret(PIHOLE_SECRET), insecure_tls=config.insecure_tls,
                )
                self._pihole = (config, client)
            return self._pihole[1]

    async def npm_client(self) -> NpmClient:
        settings = await self._settings()
        has_password = await self._ctx.secrets.exists(NPM_SECRET)
        async with self._npm_lock:
            try:
                config = npm_config(settings)
            except NotConfigured:
                self._drop_npm()
                raise
            if not has_password:
                self._drop_npm()
                raise NotConfigured("Für den Nginx Proxy Manager fehlt noch das Passwort (Einstellungen → Erweiterungen → Netzwerk).")
            if self._npm is None or self._npm[0] != config:
                self._drop_npm()
                client = NpmClient(
                    self._ctx.http, base_url=config.url, identity=config.identity, password=self._secret(NPM_SECRET),
                    insecure_tls=config.insecure_tls,
                )
                self._npm = (config, client)
            return self._npm[1]

    async def npm_settings(self) -> NpmConfig | None:
        try:
            return npm_config(await self._settings())
        except NotConfigured:
            return None

    async def pihole_configured(self) -> bool:
        try:
            pihole_config(await self._settings())
        except NotConfigured:
            return False
        return True

    async def _drop_pihole(self) -> None:
        if self._pihole is not None:
            _, client = self._pihole
            self._pihole = None
            await client.logout()

    def _drop_npm(self) -> None:
        if self._npm is not None:
            self._npm[1].forget()
            self._npm = None

    async def refresh(self) -> None:
        """Nach dem Speichern der Einstellungen: alte Sitzungen sofort abmelden."""
        try:
            await self.pihole_client()
        except NotConfigured:
            pass
        try:
            await self.npm_client()
        except NotConfigured:
            pass

    async def close(self) -> None:
        async with self._pihole_lock:
            await self._drop_pihole()
        async with self._npm_lock:
            self._drop_npm()

    # -- Zustaende fuer Seite und Kacheln --------------------------------------

    async def pihole_status(self) -> dict[str, Any]:
        try:
            client = await self.pihole_client()
        except NotConfigured as exc:
            return {"state": "not_configured", "message": str(exc), "url": None, "summary": None, "blocking": None}
        result: dict[str, Any] = {"state": "ok", "message": None, "url": client.base_url, "summary": None, "blocking": None}
        try:
            result["summary"] = await client.summary()
            result["blocking"] = await client.blocking()
        except PiholeError as exc:
            result.update(state=exc.state, message=str(exc), summary=None, blocking=None)
        return result

    async def npm_status(self, *, now: datetime | None = None) -> dict[str, Any]:
        empty = {"hosts": [], "certificates": [], "summary": None}
        try:
            client = await self.npm_client()
        except NotConfigured as exc:
            return {"state": "not_configured", "message": str(exc), "url": None, **empty}
        try:
            certificates = await client.certificates(now=now or datetime.now(timezone.utc))
            hosts = await client.proxy_hosts(certificates)
        except NpmError as exc:
            return {"state": exc.state, "message": str(exc), "url": client.base_url, **empty}
        certificates.sort(key=lambda c: (c["days_left"] is None, c["days_left"] if c["days_left"] is not None else 0))
        hosts.sort(key=lambda h: (h["domains"][0].lower() if h["domains"] else ""))
        return {
            "state": "ok",
            "message": None,
            "url": client.base_url,
            "hosts": hosts,
            "certificates": certificates,
            "summary": {
                "hosts": len(hosts),
                "hosts_enabled": sum(1 for h in hosts if h["enabled"]),
                "certificates": len(certificates),
                "certificates_warn": sum(1 for c in certificates if c["status"] == "warn"),
                "certificates_expired": sum(1 for c in certificates if c["status"] == "expired"),
            },
        }
