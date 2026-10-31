"""Nginx Proxy Manager: Proxy-Hosts mit Ziel, Zertifikate, Adress-Check und Ein- und Ausschalten über das Aktions-Gate.

Das Passwort liegt nur im Tresor; keine Route gibt es zurück, keine Meldung enthält es.
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

from .actions import (
    ACTION_SPECS,
    MAX_HOST_ID,
    NPM_HOST_DISABLE,
    NPM_HOST_ENABLE,
    NpmActionExecutor,
)
from .certwatch import CertWatchJob
from .client import NpmError
from .config import NotConfigured
from .service import NpmService
from .suggestions import NpmAppSuggestions
from .widgets import certificate_rows, widget_state

_WIDGET_ITEM = ListItem(title="{{ title }}", subtitle="{{ subtitle }}", badge=Badge(text="{{ label }}", tone="{{ tone }}"))


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._service = NpmService(ctx)
        service = self._service

        ctx.capabilities.provide(NpmActionExecutor(service))
        # Proxy-Hosts als Vorschläge für App-Kacheln, nur lesend.
        ctx.capabilities.provide(NpmAppSuggestions(service))
        for spec in ACTION_SPECS:
            ctx.actions.register(spec)

        ctx.settings.declare(
            {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "identity": {"type": "string"},
                    "tls_insecure_skip_verify": {"type": "boolean"},
                    "notify_expiring": {"type": "boolean"},
                },
            }
        )

        read_router = APIRouter()

        @read_router.get("/summary")
        async def npm_overview() -> dict[str, Any]:
            return await service.npm_status()

        @read_router.get("/address-check")
        async def npm_address_check() -> dict[str, Any]:
            return await service.address_check()

        @read_router.get("/widgets/certificates")
        async def certificates_widget() -> dict[str, Any]:
            state = await service.npm_status()
            return {"data": certificate_rows(state), "meta": {"state": widget_state(state)}}

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

        # Ändern nur als Vorschlag ans Aktions-Gate; ausgeführt wird erst nach der Freigabe.
        act_router = APIRouter()

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
                f"Proxy-Host „{name}“ über Nodvard Deck {verb}.", f"npm-host:{host_id}", actor,
            )

        @act_router.post("/hosts/{host_id}/enable")
        async def enable_host(host_id: int = Path(ge=1, le=MAX_HOST_ID), actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:  # noqa: B008
            return await _set_host(host_id, True, actor)

        @act_router.post("/hosts/{host_id}/disable")
        async def disable_host(host_id: int = Path(ge=1, le=MAX_HOST_ID), actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:  # noqa: B008
            return await _set_host(host_id, False, actor)

        ctx.api.include_router(read_router, permission="hosts.read")
        ctx.api.include_router(act_router, permission="hosts.execute")

        ctx.ui.register_page(
            PageSpec(
                id="npm",
                path="/npm",
                title="Nginx Proxy Manager",
                icon="globe",
                nav_section="Infrastruktur",
                nav_order=42,
                permissions=["hosts.read"],
                component="NpmPage",
            )
        )
        ctx.ui.register_widget(
            WidgetSpec(
                id="certificates",
                title="Zertifikate",
                group="security",
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
        await self._service.close()

    async def on_settings_changed(self, ctx: ExtensionContext, values: dict[str, Any]) -> None:
        await self._service.refresh()

    async def on_secret_changed(self, ctx: ExtensionContext, label: str) -> None:
        # Eine Anmeldung von vorher gälte sonst weiter, auch wenn im Tresor inzwischen ein falsches Passwort steht.
        await self._service.drop()

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        try:
            # „Verbindung testen“ prüft das Passwort, das jetzt im Tresor steht, nicht ein Token von vorher.
            await self._service.drop()
            state = await self._service.npm_status()
        except Exception as exc:  # noqa: BLE001 - Health darf nie werfen
            return HealthReport(healthy=False, message=str(exc))
        if state["state"] == "ok":
            return HealthReport(healthy=True, message="In Ordnung")
        return HealthReport(healthy=False, message=state["message"] or "Nginx Proxy Manager antwortet nicht.", details={"state": state["state"]})
