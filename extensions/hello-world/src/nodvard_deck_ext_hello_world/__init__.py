"""Beispiel-Erweiterung `hello-world` (zugleich Integrationstest der Schnittstelle).

Zeigt Seite, Widget, Job, Einstellung und Capability sowie `ctx.spawn`, `ctx.events`, `ctx.audit`, `ctx.vault_use()` und `ctx.notify`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from nodvard_sdk import (
    Event,
    ExtensionContext,
    GridSize,
    HealthReport,
    ListItem,
    ListView,
    NodvardExtension,
    Notification,
    PageSpec,
    Refresh,
    Severity,
    WidgetSpec,
)


class _DummySearchProvider:
    """Erfüllt `nodvard_sdk.capabilities.SearchProvider` strukturell: Das Protocol prüft nur Attributnamen, Erben ist nicht nötig."""

    async def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return [{"title": f"hello-world Treffer für '{query}'", "id": "1"}][:limit]


class _HelloJobSpec:
    """Erfüllt `nodvard_sdk.context.JobSpec` strukturell; der Kern-Scheduler führt `handler` bei Fälligkeit und über
    `POST /jobs/{id}/run` aus."""

    id = "hello-ping"
    name = "Hello-Ping"
    schedule = "*/5 * * * *"
    params: dict[str, Any] = {}
    enabled = True

    async def handler(self, **kwargs: Any) -> dict[str, str]:
        return {"greeting": "hallo vom Scheduler"}


class Extension(NodvardExtension):
    def __init__(self) -> None:
        self._tick_count = 0

    async def setup(self, ctx: ExtensionContext) -> None:
        router = APIRouter()

        # Ohne `permission=` verlangt der Kern eine gültige Anmeldung (Standard von `include_router`); für ein Demo-Widget
        # reicht das.
        @router.get("/widgets/hello")
        async def hello_widget_data() -> dict:
            return {
                "data": [
                    {"title": "Hallo Welt", "subtitle": f"{self._tick_count} Ticks seit Start"}
                ],
                "meta": {},
            }

        protected_router = APIRouter()

        @protected_router.get("/protected-widget")
        async def protected_widget_data() -> dict:
            """Zeigt `ctx.api.include_router(..., permission=...)`: Anders als `/widgets/hello` (nur Anmeldung) verlangt diese
            Route zusätzlich `hosts.read`."""
            return {"ok": True}

        ctx.api.include_router(protected_router, permission="hosts.read")

        # Die beiden schreibenden Demo-Routen lösen echte Wirkungen aus (Push-Meldung, Tresor-Eintrag, Fehler im Protokoll),
        # deshalb nur für Verwalter von Erweiterungen, nicht für jeden angemeldeten Nutzer.
        admin_router = APIRouter()

        @admin_router.post("/notify-test")
        async def notify_test(host_id: str | None = None) -> dict:
            """Löst `ctx.notify.send()` aus; `host_id` macht die Meldung für die Wartungsfenster-Prüfung sichtbar."""
            # Es gibt sonst keinen HTTP-Weg dafür: Die Meldungen sind über die API absichtlich nur lesbar. Die Wartungsfenster
            # lesen die Konvention `payload["host_id"]`.
            await ctx.notify.send(
                Notification(
                    title="Testmeldung von hello-world",
                    body="Ausgelöst über POST /notify-test",
                    severity=Severity.WARNING,
                    payload={"host_id": host_id} if host_id else {},
                )
            )
            return {"sent": True}

        @admin_router.post("/vault-use-and-fail")
        async def vault_use_and_fail() -> dict:
            # Materialisiert ein Geheimnis und scheitert danach absichtlich: Der `secret.used`-Audit-Eintrag muss trotzdem
            # stehen bleiben.
            if not await ctx.secrets.exists("hello-world-demo"):
                await ctx.secrets.create(
                    label="hello-world-demo", kind="generic", value="demo-wert"
                )
            handle = await ctx.secrets.get_handle("hello-world-demo")
            async with ctx.vault_use(handle) as value:
                assert value == "demo-wert"
            raise RuntimeError("Absichtlicher Fehler NACH der Materialisierung (WP-3-Boot-Test)")

        ctx.api.include_router(router)
        ctx.api.include_router(admin_router, permission="extensions.manage")

        ctx.ui.register_page(
            PageSpec(
                id="hello",
                path="/hello",
                title="Hello World",
                icon="sparkles",
                nav_section="Beispiele",
                nav_order=999,
                component="HelloPage",
            )
        )

        ctx.ui.register_widget(
            WidgetSpec(
                id="hello",
                title="Hallo Welt",
                icon="sparkles",
                size=GridSize(w=2, h=1),
                # `ws_channel` zusätzlich zu `interval_s`: Der Ticker unten sendet über genau diesen Kanal, das Widget muss also
                # nicht 30 s auf den nächsten Poll warten.
                refresh=Refresh(interval_s=30, ws_channel="ext.hello-world.tick"),
                data_endpoint="widgets/hello",
                view=ListView(item=ListItem(title="{{ title }}", subtitle="{{ subtitle }}")),
            )
        )

        # Legt zusätzlich die `jobs`-Zeile an.
        await ctx.scheduler.register_job(_HelloJobSpec())

        ctx.settings.declare(
            {"type": "object", "properties": {"greeting": {"type": "string"}}}
        )

        ctx.capabilities.provide(_DummySearchProvider())

    async def on_start(self, ctx: ExtensionContext) -> None:
        async def _tick() -> None:
            while True:
                self._tick_count += 1
                await ctx.events.publish(Event(name="hello.tick", payload={"count": self._tick_count}))
                # `ctx.events.publish()` fächert nur auf den einen geteilten „events“-Kanal aus; `ctx.ws.broadcast()` zusätzlich,
                # damit das Widget den in seinem `Refresh.ws_channel` genannten, themenspezifischen Kanal abonnieren kann.
                await ctx.ws.broadcast("tick", {"count": self._tick_count})
                await asyncio.sleep(2.0)

        ctx.spawn(_tick(), name="hello-ticker")
        await ctx.audit.log(action="hello.started", outcome="success")

    async def on_stop(self, ctx: ExtensionContext) -> None:
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        return HealthReport(healthy=True, details={"ticks": self._tick_count})
