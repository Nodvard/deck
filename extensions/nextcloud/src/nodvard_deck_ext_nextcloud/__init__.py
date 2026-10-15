"""nextcloud-Extension -- die zweite echte Vendor-Extension nach proxmox,
liefert `FileSource`/`FileSourceProvider` fuer
den Kern-Dateimanager über WebDAV und eine Kachel mit Kennzahlen aus "Server-Info". Kein Host-Bezug (anders als proxmox/terminal) --
eine Nextcloud-Instanz ist keine `Host`-Entitaet, genauso wenig wie proxmoxs eigener
API-Endpunkt selbst ein Host ist (nur die von ihm entdeckten VMs sind welche).

**Die Nagelprobe fuer core_purity:** wie bei proxmox ist diese Datei (mit
`connector.py`/`capabilities.py`/`config.py`) die einzige Stelle im Repository
ausserhalb von `extensions/nextcloud/`, an der "Nextcloud"/"WebDAV" vorkommen darf.
Der Kern sieht nur `FileSource`/`FileSourceProvider` (`nodvard_sdk.capabilities`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from nodvard_sdk import (
    Badge,
    ExtensionContext,
    GridSize,
    HealthReport,
    ListItem,
    ListView,
    NodvardExtension,
    Refresh,
    WidgetSpec,
)
from pydantic import BaseModel

from .capabilities import NextcloudFileSourceProvider
from .config import PASSWORD_LABEL, build_connector
from .connector import WebDavError
from .models import ServerInfo
from .service import ServerInfoService
from .widgets import status_rows

_WIDGET_ITEM = ListItem(title="{{ title }}", subtitle="{{ subtitle }}", badge=Badge(text="{{ label }}", tone="{{ tone }}"))


class _PasswordIn(BaseModel):
    value: str


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._serverinfo = ServerInfoService(ctx)
        serverinfo = self._serverinfo

        ctx.capabilities.provide(NextcloudFileSourceProvider(ctx))

        ctx.settings.declare(
            {
                "type": "object",
                "properties": {
                    "base_url": {"type": "string"},
                    "username": {"type": "string"},
                },
                "required": ["base_url", "username"],
            }
        )

        router = APIRouter()

        @router.post("/token", status_code=status.HTTP_204_NO_CONTENT)
        async def set_password(payload: _PasswordIn) -> None:
            """Wie proxmoxs `/token` (WP-8): das App-Passwort liegt im Vault, nie in
            den (Klartext-)Einstellungen. Kein Rotationspfad in dieser Runde -- ein
            bestehendes Passwort muss erst ueber `DELETE /api/v1/secrets/{id}`
            entfernt werden."""
            if await ctx.secrets.exists(PASSWORD_LABEL):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Ein App-Passwort existiert bereits -- zuerst über DELETE /api/v1/secrets/{id} entfernen.",
                )
            await ctx.secrets.create(label=PASSWORD_LABEL, kind="generic", value=payload.value)

        ctx.api.include_router(router, permission="secrets.write")

        read_router = APIRouter()

        @read_router.get("/serverinfo", response_model=ServerInfo)
        async def get_serverinfo(refresh: bool = False) -> ServerInfo:
            # `refresh=true` überspringt den Zwischenspeicher; Probleme stehen in `state`, nicht im Statuscode.
            return await serverinfo.info(refresh=refresh)

        @read_router.get("/widgets/status")
        async def status_widget() -> dict[str, Any]:
            info = await serverinfo.info()
            return {"data": status_rows(info), "meta": {"state": info.state, "version": info.version}}

        ctx.api.include_router(read_router, permission="hosts.read")

        ctx.ui.register_widget(
            WidgetSpec(
                id="status",
                title="Nextcloud",
                icon="cloud",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=60),
                data_endpoint="widgets/status",
                permissions=["hosts.read"],
                view=ListView(item=_WIDGET_ITEM, empty_text="Keine Daten"),
            )
        )

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def on_stop(self, ctx: ExtensionContext) -> None:
        return None

    async def on_settings_changed(self, ctx: ExtensionContext, values: dict[str, Any]) -> None:
        self._serverinfo.clear()

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        # Nach "Verbindung testen" fragt die Kachel neu, auch nach einer abgelehnten Anmeldung.
        self._serverinfo.clear()
        connector = await build_connector(ctx)
        if connector is None:
            return HealthReport(healthy=False, message="Nicht konfiguriert (base_url/username/App-Passwort).")
        try:
            await connector.stat_one("/")
        except WebDavError as exc:
            return HealthReport(healthy=False, message=str(exc))
        return HealthReport(healthy=True)
