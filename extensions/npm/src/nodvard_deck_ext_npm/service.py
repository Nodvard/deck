"""Hält einen Client (NPM-Token) über alle Aufrufe hinweg.

`npm_status()` wirft nie: Fehler sind Zustände (`state`) mit deutscher Meldung, keine 500er.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from nodvard_sdk import ServiceCatalog

from .address_check import check_all, docker_hosts, problem_count
from .client import NpmClient, NpmError
from .config import (
    NPM_SECRET,
    SETTINGS_HINT,
    InvalidSetting,
    NotConfigured,
    NpmConfig,
    npm_config,
)

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

# So lange darf das Lesen der Container dauern (das Modul Docker fragt jeden Docker-Server per SSH).
CATALOG_TIMEOUT_S = 45.0
NO_CATALOG = (
    "Für den Adress-Check braucht es das Modul Docker, es kennt die Container. "
    "Es ist ausgeschaltet oder gerade nicht geladen."
)
NO_DOCKER_HOSTS = "Kein Server ist als Docker-Server markiert, deshalb lässt sich kein Ziel einem Container zuordnen."


class NpmService:
    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._npm: tuple[NpmConfig, NpmClient] | None = None
        # Seite und Kacheln fragen gleichzeitig; ohne Sperre baute jeder Aufruf einen eigenen Client.
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
                raise NotConfigured(f"Für den Nginx Proxy Manager fehlt noch das Passwort ({SETTINGS_HINT}).")
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

    def _drop_npm(self) -> None:
        if self._npm is not None:
            self._npm[1].forget()
            self._npm = None

    async def drop(self) -> None:
        """Verwirft das gespeicherte Token; der nächste Aufruf meldet sich mit dem aktuellen Passwort neu an."""
        async with self._npm_lock:
            self._drop_npm()

    async def refresh(self) -> None:
        """Nach dem Speichern der Einstellungen: einen alten Client sofort verwerfen."""
        try:
            await self.npm_client()
        except NotConfigured:
            pass

    async def close(self) -> None:
        async with self._npm_lock:
            self._drop_npm()

    # -- Zustände für Seite und Kacheln ----------------------------------------

    async def npm_status(self, *, now: datetime | None = None) -> dict[str, Any]:
        empty = {"hosts": [], "certificates": [], "summary": None}
        try:
            client = await self.npm_client()
        except NotConfigured as exc:
            return {"state": "not_configured", "message": str(exc), "invalid": isinstance(exc, InvalidSetting), "url": None, **empty}
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

    async def address_check(self) -> dict[str, Any]:
        """Je Proxy-Host: Zu welchem Container führt das Ziel, und läuft er? Wirft nie; Zustände wie bei
        `npm_status()`, dazu `no_catalog` (Modul Docker aus) und `docker_unreadable` (keine Container lesbar)."""
        empty: dict[str, Any] = {"checks": [], "problems": 0}
        try:
            client = await self.npm_client()
        except NotConfigured as exc:
            return {"state": "not_configured", "message": str(exc), **empty}
        providers = await self._ctx.capabilities.query(ServiceCatalog)
        if not providers:
            return {"state": "no_catalog", "message": NO_CATALOG, **empty}
        try:
            proxies = await client.proxy_hosts()
        except NpmError as exc:
            return {"state": exc.state, "message": str(exc), **empty}
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        for provider in providers:
            try:
                rows.extend(await asyncio.wait_for(provider.list_services(), timeout=CATALOG_TIMEOUT_S))
            except TimeoutError:
                errors.append(f"Zeitüberschreitung nach {CATALOG_TIMEOUT_S:.0f} s")
            except Exception as exc:  # noqa: BLE001 - ein Anbieter darf den Check nicht abbrechen
                errors.append(str(exc) or type(exc).__name__)
        if errors and not rows:
            return {"state": "docker_unreadable", "message": f"Die Docker-Container ließen sich nicht lesen: {'; '.join(errors)}", **empty}
        checks = check_all(proxies, rows, npm_url=client.base_url)
        return {
            "state": "ok",
            "message": None if docker_hosts(rows) else NO_DOCKER_HOSTS,
            "checks": checks,
            "problems": problem_count(checks),
        }
