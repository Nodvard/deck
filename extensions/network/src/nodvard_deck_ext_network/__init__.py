"""network-Extension ("Netzwerk") -- Pi-hole (v6) und Nginx Proxy Manager im Dashboard.

- Lesen (`hosts.read`): `GET /pihole`, `GET /npm`, die beiden Kachel-Endpunkte.
- Aendern (`hosts.execute`): Blockierung pausieren/fortsetzen, Proxy-Host ein/aus --
  immer als Vorschlag ans Aktions-Gate (`ctx.actions.propose`), ausgefuehrt erst nach
  Freigabe durch `NetworkActionExecutor` (actions.py). Die Seite gibt selbst frei,
  wenn der Nutzer `actions.approve:<risk>` hat -- wie Backups/Nodvard Shield.
- Beide Dienste sind optional; ohne Eintrag melden Seite, Kacheln und API sauber
  "nicht eingerichtet" statt eines Fehlers.

Passwoerter liegen nur im Vault (`network-pihole-password`, `network-npm-password`,
gesetzt ueber die Einstellungsseite des Kerns) und verlassen die Extension nie --
keine Route gibt sie zurueck, keine Meldung enthaelt sie.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from nodvard_sdk import (
    Actor,
    Badge,
    ExtensionContext,
    GridSize,
    HealthReport,
    ListItem,
    ListView,
    NodvardExtension,
    PageSpec,
    Refresh,
    Risk,
    WidgetSpec,
)
from nodvard_sdk.actions import REQUEST_WAIT_S, ActionRequest
from pydantic import BaseModel

from .actions import (
    ACTION_SPECS,
    MAX_HOST_ID,
    NPM_HOST_DISABLE,
    NPM_HOST_ENABLE,
    PAUSE_MINUTES,
    PIHOLE_PAUSE,
    PIHOLE_RESUME,
    NetworkActionExecutor,
    pause_text,
)
from .certwatch import CertWatchJob
from .config import NotConfigured
from .npm import NpmError
from .service import NetworkService
from .widgets import certificate_rows, pihole_rows

_WIDGET_ITEM = ListItem(title="{{ title }}", subtitle="{{ subtitle }}", badge=Badge(text="{{ label }}", tone="{{ tone }}"))


class _PauseIn(BaseModel):
    minutes: int


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._service = NetworkService(ctx)
        service = self._service

        ctx.capabilities.provide(NetworkActionExecutor(service))
        for spec in ACTION_SPECS:
            ctx.actions.register(spec)

        ctx.settings.declare(
            {
                "type": "object",
                "properties": {
                    "pihole": {"type": "object", "properties": {"url": {"type": "string"}, "tls_insecure_skip_verify": {"type": "boolean"}}},
                    "npm": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "identity": {"type": "string"},
                            "tls_insecure_skip_verify": {"type": "boolean"},
                            "notify_expiring": {"type": "boolean"},
                        },
                    },
                },
            }
        )

        read_router = APIRouter()

        @read_router.get("/pihole")
        async def pihole_overview() -> dict[str, Any]:
            return await service.pihole_status()

        @read_router.get("/npm")
        async def npm_overview() -> dict[str, Any]:
            return await service.npm_status()

        @read_router.get("/widgets/pihole")
        async def pihole_widget() -> dict[str, Any]:
            state = await service.pihole_status()
            return {"data": pihole_rows(state), "meta": {"state": state["state"]}}

        @read_router.get("/widgets/certificates")
        async def certificates_widget() -> dict[str, Any]:
            state = await service.npm_status()
            return {"data": certificate_rows(state), "meta": {"state": state["state"]}}

        async def _propose(action_type: str, payload: dict[str, Any], risk: Risk, reason: str, correlation_id: str, actor: Actor) -> dict[str, Any]:
            decision = await ctx.actions.propose(ActionRequest(
                action_type=action_type, payload=payload, risk=risk, proposed_by=actor, reason=reason,
                correlation_id=correlation_id,
            ), wait_s=REQUEST_WAIT_S)
            out: dict[str, Any] = {"action_id": decision.action_id, "status": decision.status.value, "risk": risk.value, "detail": decision.detail}
            if decision.status.value in ("succeeded", "failed"):
                row = await ctx.actions.result(decision.action_id)
                out["result"] = row.result if row is not None else None
            return out

        async def _require_pihole() -> None:
            if not await service.pihole_configured():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pi-hole ist nicht eingerichtet.")

        act_router = APIRouter()

        @act_router.post("/pihole/pause")
        async def pause_blocking(payload: _PauseIn, actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:  # noqa: B008
            if payload.minutes not in PAUSE_MINUTES:
                raise HTTPException(status_code=422, detail="Pausieren geht nur für 5, 15 oder 60 Minuten.")
            await _require_pihole()
            return await _propose(
                PIHOLE_PAUSE, {"minutes": payload.minutes}, Risk.LOW,
                f"Pi-hole-Blockierung über die Netzwerk-Seite für {pause_text(payload.minutes)} pausiert.",
                "network:pihole", actor,
            )

        @act_router.post("/pihole/resume")
        async def resume_blocking(actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:  # noqa: B008
            await _require_pihole()
            return await _propose(PIHOLE_RESUME, {}, Risk.LOW, "Pi-hole-Blockierung über die Netzwerk-Seite fortgesetzt.", "network:pihole", actor)

        async def _npm_host(host_id: int) -> dict[str, Any]:
            try:
                client = await service.npm_client()
                hosts = await client.proxy_hosts()
            except NotConfigured as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
            except NpmError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc) or type(exc).__name__) from exc
            host = next((h for h in hosts if h["id"] == host_id), None)
            if host is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Proxy-Host {host_id} gibt es nicht (mehr).")
            return host

        async def _set_host(host_id: int, enabled: bool, actor: Actor) -> dict[str, Any]:
            host = await _npm_host(host_id)
            name = ", ".join(host["domains"]) or f"Proxy-Host {host_id}"
            if host["enabled"] == enabled:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"„{name}“ ist schon {'eingeschaltet' if enabled else 'ausgeschaltet'}.",
                )
            verb = "eingeschaltet" if enabled else "ausgeschaltet"
            return await _propose(
                NPM_HOST_ENABLE if enabled else NPM_HOST_DISABLE, {"host_id": host_id}, Risk.MEDIUM,
                f"Proxy-Host „{name}“ über die Netzwerk-Seite {verb}.", f"network:npm-host:{host_id}", actor,
            )

        @act_router.post("/npm/hosts/{host_id}/enable")
        async def enable_host(host_id: int = Path(ge=1, le=MAX_HOST_ID), actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:  # noqa: B008
            return await _set_host(host_id, True, actor)

        @act_router.post("/npm/hosts/{host_id}/disable")
        async def disable_host(host_id: int = Path(ge=1, le=MAX_HOST_ID), actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:  # noqa: B008
            return await _set_host(host_id, False, actor)

        ctx.api.include_router(read_router, permission="hosts.read")
        ctx.api.include_router(act_router, permission="hosts.execute")

        ctx.ui.register_page(
            PageSpec(
                id="network",
                path="/network",
                title="Netzwerk",
                icon="activity",
                nav_section="Infrastruktur",
                nav_order=40,
                permissions=["hosts.read"],
                component="NetworkPage",
            )
        )
        ctx.ui.register_widget(
            WidgetSpec(
                id="pihole",
                title="Pi-hole",
                icon="shield-check",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=60),
                data_endpoint="widgets/pihole",
                permissions=["hosts.read"],
                view=ListView(item=_WIDGET_ITEM, empty_text="Keine Daten"),
            )
        )
        ctx.ui.register_widget(
            WidgetSpec(
                id="certificates",
                title="Zertifikate",
                icon="shield-alert",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=600),
                data_endpoint="widgets/certificates",
                permissions=["hosts.read"],
                view=ListView(item=_WIDGET_ITEM, empty_text="Keine Zertifikate"),
            )
        )

        await ctx.scheduler.register_job(CertWatchJob(ctx, service))

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def on_stop(self, ctx: ExtensionContext) -> None:
        # Pi-hole hat nur wenige API-Plaetze -- die eigene Sitzung wieder freigeben.
        await self._service.close()

    async def on_settings_changed(self, ctx: ExtensionContext, values: dict[str, Any]) -> None:
        await self._service.refresh()

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        try:
            states = [("Pi-hole", await self._service.pihole_status()), ("Nginx Proxy Manager", await self._service.npm_status())]
        except Exception as exc:  # noqa: BLE001 - Health darf nie werfen
            return HealthReport(healthy=False, message=str(exc))
        configured = [(name, s) for name, s in states if s["state"] != "not_configured"]
        if not configured:
            return HealthReport(healthy=False, message="Noch nichts eingerichtet – Pi-hole oder Nginx Proxy Manager in den Einstellungen eintragen.")
        details = {name: {"state": s["state"], "message": s["message"]} for name, s in configured}
        message = "; ".join(f"{name}: {'in Ordnung' if s['state'] == 'ok' else s['message']}" for name, s in configured)
        return HealthReport(healthy=all(s["state"] == "ok" for _, s in configured), message=message, details=details)
