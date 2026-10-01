"""Baut einen `NextcloudConnector` frisch aus den aktuellen Einstellungen +
Vault-Secret -- wie `nodvard_deck_ext_proxmox.config.build_connector()`, KEIN einmalig in
`setup()` gebauter, dauerhaft gehaltener Connector.

**Unterschied zu proxmox:** gibt `None` zurueck statt zu werfen, wenn unkonfiguriert
-- `NextcloudFileSourceProvider.file_sources()` (siehe `capabilities.py`) will eine
unkonfigurierte Extension einfach STILL aus dem Dateimanager weglassen, nicht bei
jeder `GET /files/sources`-Anfrage eine Exception durchreichen."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .connector import NextcloudConnector

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

PASSWORD_LABEL = "nextcloud-app-password"


async def build_connector(ctx: "ExtensionContext") -> NextcloudConnector | None:
    settings = await ctx.settings.get()
    base_url = settings.get("base_url")
    username = settings.get("username")
    if not base_url or not username:
        return None
    if not await ctx.secrets.exists(PASSWORD_LABEL):
        return None

    handle = await ctx.secrets.get_handle(PASSWORD_LABEL)
    async with ctx.vault_use(handle) as password:
        return NextcloudConnector(
            ctx,
            base_url=str(base_url),
            username=str(username),
            password=password,
            tls_insecure_skip_verify=bool(settings.get("tls_insecure_skip_verify", False)),
        )
