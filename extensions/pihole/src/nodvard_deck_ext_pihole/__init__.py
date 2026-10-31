"""Pi-hole (v6): Anfragen, Blockierung, aktivste Geräte und Sperrlisten; geändert wird nur über das Aktions-Gate.

Das Passwort liegt nur im Tresor; keine Route gibt es zurück, keine Meldung enthält es."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
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
    DOMAIN_ALLOW,
    DOMAIN_DENY,
    GRAVITY_UPDATE,
    PAUSE_MINUTES,
    PIHOLE_PAUSE,
    PIHOLE_RESUME,
    PiholeActionExecutor,
    pause_text,
)
from .config import normalize_domain
from .service import PiholeService
from .suggestions import PiholeAppSuggestions
from .widgets import status_rows, widget_state

_WIDGET_ITEM = ListItem(title="{{ title }}", subtitle="{{ subtitle }}", badge=Badge(text="{{ label }}", tone="{{ tone }}"))
_CORRELATION = "pihole"


class _PauseIn(BaseModel):
    minutes: int


class _DomainIn(BaseModel):
    list: Literal["allow", "deny"]
    domain: str


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._service = PiholeService(ctx)
        service = self._service

        ctx.capabilities.provide(PiholeActionExecutor(service))
        ctx.capabilities.provide(PiholeAppSuggestions(ctx))
        for spec in ACTION_SPECS:
            ctx.actions.register(spec)

        ctx.settings.declare(
            {"type": "object", "properties": {"url": {"type": "string"}, "tls_insecure_skip_verify": {"type": "boolean"}}}
        )

        read_router = APIRouter()

        @read_router.get("/summary")
        async def summary() -> dict[str, Any]:
            return await service.status()

        @read_router.get("/widgets/status")
        async def status_widget() -> dict[str, Any]:
            state = await service.status()
            return {"data": status_rows(state), "meta": {"state": widget_state(state)}}

        async def _propose(action_type: str, payload: dict[str, Any], risk: Risk, reason: str, actor: Actor) -> dict[str, Any]:
            decision = await ctx.actions.propose(ActionRequest(
                action_type=action_type, payload=payload, risk=risk, proposed_by=actor, reason=reason,
                correlation_id=_CORRELATION,
            ), wait_s=REQUEST_WAIT_S)
            out: dict[str, Any] = {"action_id": decision.action_id, "status": decision.status.value, "risk": risk.value, "detail": decision.detail}
            if decision.status.value in ("succeeded", "failed"):
                row = await ctx.actions.result(decision.action_id)
                out["result"] = row.result if row is not None else None
            return out

        async def _require_pihole() -> None:
            if not await service.configured():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pi-hole ist nicht eingerichtet.")

        # Ändern nur als Vorschlag ans Aktions-Gate; ausgeführt wird erst nach der Freigabe.
        act_router = APIRouter()

        @act_router.post("/pause")
        async def pause_blocking(payload: _PauseIn, actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:  # noqa: B008
            if payload.minutes not in PAUSE_MINUTES:
                raise HTTPException(status_code=422, detail="Pausieren geht nur für 5, 15 oder 60 Minuten.")
            await _require_pihole()
            return await _propose(
                PIHOLE_PAUSE, {"minutes": payload.minutes}, Risk.LOW,
                f"Pi-hole-Blockierung über Nodvard Deck für {pause_text(payload.minutes)} pausiert.", actor,
            )

        @act_router.post("/resume")
        async def resume_blocking(actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:  # noqa: B008
            await _require_pihole()
            return await _propose(PIHOLE_RESUME, {}, Risk.LOW, "Pi-hole-Blockierung über Nodvard Deck fortgesetzt.", actor)

        @act_router.post("/gravity")
        async def update_gravity(actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:  # noqa: B008
            await _require_pihole()
            return await _propose(GRAVITY_UPDATE, {}, Risk.LOW, "Pi-hole-Sperrlisten über Nodvard Deck neu geladen.", actor)

        @act_router.post("/domains")
        async def add_domain(payload: _DomainIn, actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:  # noqa: B008
            try:
                domain = normalize_domain(payload.domain)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            await _require_pihole()
            allow = payload.list == "allow"
            return await _propose(
                DOMAIN_ALLOW if allow else DOMAIN_DENY, {"domain": domain}, Risk.MEDIUM,
                f"„{domain}“ über Nodvard Deck in Pi-hole {'erlaubt' if allow else 'gesperrt'}.", actor,
            )

        ctx.api.include_router(read_router, permission="hosts.read")
        ctx.api.include_router(act_router, permission="hosts.execute")

        ctx.ui.register_page(
            PageSpec(
                id="pihole",
                path="/pihole",
                title="Pi-hole",
                icon="shield-check",
                nav_section="Infrastruktur",
                nav_order=41,
                permissions=["hosts.read"],
                component="PiholePage",
            )
        )
        ctx.ui.register_widget(
            WidgetSpec(
                id="status",
                title="Pi-hole",
                group="services",
                icon="shield-check",
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
        # Pi-hole hat nur wenige API-Plätze: die eigene Sitzung wieder freigeben.
        await self._service.close()

    async def on_settings_changed(self, ctx: ExtensionContext, values: dict[str, Any]) -> None:
        await self._service.refresh()

    async def on_secret_changed(self, ctx: ExtensionContext, label: str) -> None:
        # Eine Anmeldung von vorher gälte sonst weiter, auch wenn im Tresor inzwischen ein falsches Passwort steht.
        await self._service.drop()

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        try:
            # „Verbindung testen“ prüft das Passwort, das jetzt im Tresor steht, nicht eine Sitzung von vorher.
            await self._service.drop()
            state = await self._service.status()
        except Exception as exc:  # noqa: BLE001 - Health darf nie werfen
            return HealthReport(healthy=False, message=str(exc))
        if state["state"] == "ok":
            return HealthReport(healthy=True, message="In Ordnung")
        return HealthReport(healthy=False, message=state["message"] or "Pi-hole antwortet nicht.", details={"state": state["state"]})
