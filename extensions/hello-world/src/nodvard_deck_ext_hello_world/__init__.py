"""Referenz-Extension `hello-world` -- der Integrationstest der Schnittstelle
(docs/02-EXTENSION-API.md).

Registriert genau die fuenf Grundbausteine einer Extension (Seite, Widget, Job,
Einstellung, Capability) und demonstriert zusaetzlich `ctx.spawn`, `ctx.events`,
`ctx.audit` und `ctx.vault_use()` inklusive der Isolations-Garantie:
`POST /vault-use-and-fail` (nur mit `extensions.manage`) materialisiert ein Secret und
scheitert danach absichtlich, um zu beweisen, dass der `secret.used`-Audit-Eintrag
trotzdem stehen bleibt. Dazu kommen `POST /notify-test` (ebenfalls nur mit
`extensions.manage`; echtes `ctx.notify.send()`, sonst kein HTTP-Weg dafuer -- das
Notification-Center ist absichtlich nur lesend ueber die API) und ein Job-Handler, den
der Kern-Scheduler ausfuehrt.
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
    """Erfuellt `nodvard_sdk.capabilities.SearchProvider` strukturell (Protocol,
    `@runtime_checkable` prueft nur Attributnamen) -- kein Erben noetig."""

    async def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return [{"title": f"hello-world Treffer für '{query}'", "id": "1"}][:limit]


class _HelloJobSpec:
    """Erfuellt `nodvard_sdk.context.JobSpec` strukturell. Seit WP-6 fuehrt der
    Kern-Scheduler `handler` wirklich aus (bei Faelligkeit UND ueber `POST /jobs/{id}/
    run`) -- `register_job()` legt zusaetzlich die `jobs`-Zeile an (docs/03 §7)."""

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

        # Ohne `permission=` verlangt der Kern eine gueltige Anmeldung (Standard der
        # SDK-Methode `include_router`); fuer ein Demo-Widget reicht das.
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
            """WP-6: demonstriert `ctx.api.include_router(..., permission=...)` --
            im Gegensatz zu `/widgets/hello` (nur Anmeldung, der Standard) verlangt
            DIESE Route zusaetzlich `hosts.read`."""
            return {"ok": True}

        ctx.api.include_router(protected_router, permission="hosts.read")

        # Die beiden schreibenden Demo-Routen loesen echte Wirkungen aus (Push-Meldung,
        # Tresor-Eintrag, Fehler im Protokoll) -- deshalb nur fuer Verwalter von
        # Erweiterungen, nicht fuer jeden angemeldeten Nutzer.
        admin_router = APIRouter()

        @admin_router.post("/notify-test")
        async def notify_test(host_id: str | None = None) -> dict:
            """Reine Boot-Test-Demo (WP-6), wie `/vault-use-and-fail` fuer
            `vault_use()`: es gibt sonst keinen HTTP-Weg, `ctx.notify.send()` real
            auszuloesen (das Notification-Center ist absichtlich nur lesend ueber die
            API, siehe api/v1/notifications.py). `host_id`, falls gesetzt, macht die
            Benachrichtigung fuer die Wartungsfenster-Pruefung sichtbar
            (services.notifications.send()s `payload.get("host_id")`-Konvention)."""
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
                # ws_channel zusaetzlich zu interval_s (WP-7-Live-Demo-Vorgabe): der
                # Ticker unten sendet ueber genau diesen Kanal, das Widget muss also
                # nicht 30s auf den naechsten Poll warten, um den neuen Stand zu zeigen.
                refresh=Refresh(interval_s=30, ws_channel="ext.hello-world.tick"),
                data_endpoint="widgets/hello",
                view=ListView(item=ListItem(title="{{ title }}", subtitle="{{ subtitle }}")),
            )
        )

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
                # ctx.events.publish() faechert nur auf den EINEN geteilten "events"-
                # Kanal aus (core/events.py) -- ctx.ws.broadcast() zusaetzlich, damit
                # das Widget genau den in seinem Refresh.ws_channel genannten,
                # themenspezifischen Kanal abonnieren kann (docs/02 §4s Beispiel
                # "ext.shield.incidents" folgt demselben Muster).
                await ctx.ws.broadcast("tick", {"count": self._tick_count})
                await asyncio.sleep(2.0)

        ctx.spawn(_tick(), name="hello-ticker")
        await ctx.audit.log(action="hello.started", outcome="success")

    async def on_stop(self, ctx: ExtensionContext) -> None:
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        return HealthReport(healthy=True, details={"ticks": self._tick_count})
