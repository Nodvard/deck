"""service-matrix-Extension -- entdeckt laufende Docker-Container und zeigt sie als
klickbare Kacheln.

Ersetzt die von Hand gepflegten Container-/URL-Listen des Vorgaengersystems: Container
werden entdeckt, nicht von Hand gepflegt. Eine Handliste sammelt tote oder falsch
zugeordnete Eintraege (Container, die es nicht mehr gibt, Dienste am falschen Host),
weil sie beim naechsten Infrastruktur-Umbau nie nachgezogen wird. Diese Extension hat
keine Liste, die veralten koennte.

**Container-Verwaltung:** von reiner Anzeige zu echter Verwaltung --
Start/Stop/Neustart je Container (`container.*`-Aktionen, `DockerContainerExecutor`,
IMMER ueber das Aktions-Gate) und Live-Logs (`GET .../containers/{host}/{name}/logs`,
gestreamt ueber `ctx.exec.stream()`). Damit braucht es fuer den Alltag weder
Portainer noch eine SSH-Sitzung auf dem Docker-Host.

**Image-Updates** (`image_updates.py`): rein lesende Pruefung, ob es fuer laufende
Container neuere Images gibt (`GET/POST .../image-updates`), optional als Tagesjob mit
einer Meldung. Einspielen (Compose-Dienste: `docker compose pull` + `up -d`) geht nur ueber
das Aktions-Gate -- `applier.py` liefert die Uebersicht vorab (`.../image-update/plan`).
"""

from __future__ import annotations

import json

from typing import Any

from fastapi import Depends
from fastapi.responses import StreamingResponse
from nodvard_sdk import (
    Actor,
    ExtensionContext,
    GridSize,
    HealthReport,
    HostRequirementSpec,
    HostToolSpec,
    NodvardExtension,
    PageSpec,
    Refresh,
    StatusGridView,
    WidgetSpec,
)
from pydantic import BaseModel, Field

from .capabilities import (
    CONTAINER_NAME_RE,
    DockerContainerExecutor,
    DockerMaintenanceExecutor,
    DockerServiceCatalog,
    container_action_specs,
)
from .applier import IMAGE_UPDATE_SPEC, ImageApplier, ImageUpdateExecutor
from .docker_details import parse_images, parse_system_df, summarize_inspect
from .image_apply import NotUpdatable
from .image_updates import ImageUpdateService, register_job

_MAX_TAIL = 5000


class _ApplyIn(BaseModel):
    plan_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    """Die Kennung der Uebersicht, die der Nutzer gesehen hat (`GET .../image-update/plan`)."""


def _apply_docker_tag(ctx: ExtensionContext, tag: str) -> None:
    """Kachel und Aktionen folgen dem eingestellten Docker-Tag (gleiche ID/Typ ersetzt)."""
    _register_host_tool(ctx, tag)
    _register_docker_requirement(ctx, tag)
    for spec in container_action_specs(tag):
        ctx.actions.register(spec)


def _register_host_tool(ctx: ExtensionContext, tag: str) -> None:
    """Kachel "Container" auf der Server-Seite jedes Docker-Hosts. Der Tag ist
    einstellbar (`docker_host_tag`) -- deshalb bei jeder Aenderung neu gemeldet."""
    ctx.ui.register_host_tool(HostToolSpec(
        id="containers", title="Container", icon="layout-grid", category="services",
        description="Container starten, stoppen, Logs, Details -- Docker-Speicher aufräumen", path="/matrix?host={host_id}",
        tags=[tag],
    ))


def _register_docker_requirement(ctx: ExtensionContext, tag: str) -> None:
    """Der Einrichtungsbefehl fuer einen Server (Einstellungen, Server & Zugaenge) bietet auf
    Docker-Hosts die Gruppe "docker" an, und "Verbindung pruefen" testet sie -- die Extension
    arbeitet mit dem SSH-Benutzer ohne sudo. Wie die Kachel folgt auch das dem Tag."""
    ctx.ui.register_host_requirement(HostRequirementSpec(
        id="docker-group", label="Docker ohne sudo (Service-Matrix)",
        check_command="docker ps -q", ok_text="Docker lässt sich ohne sudo benutzen.",
        fail_hint="Benutzer zur Gruppe docker hinzufügen: sudo usermod -aG docker {user}",
        unix_group="docker", tags=[tag], order=30,
    ))


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._catalog = DockerServiceCatalog(ctx)
        self._images = ImageUpdateService(ctx)
        self._applier = ImageApplier(ctx, self._images)
        self._images.apply_hints = self._applier.hints
        ctx.capabilities.provide(self._catalog)
        ctx.capabilities.provide(DockerContainerExecutor(ctx))
        ctx.capabilities.provide(DockerMaintenanceExecutor(ctx))
        ctx.capabilities.provide(ImageUpdateExecutor(self._applier))
        # Nicht in `container_action_specs` (die haengen am Docker-Tag): Den Befehl baut immer
        # die Extension, die Aktion kommt nur ueber die Route unten -- nie ueber die Server-Seite.
        ctx.actions.register(IMAGE_UPDATE_SPEC)
        # Mit dem Standard-Tag; on_start meldet sie mit dem eingestellten Tag neu an.
        for spec in container_action_specs():
            ctx.actions.register(spec)

        ctx.settings.declare(
            {
                "type": "object",
                "properties": {
                    "docker_host_tag": {"type": "string"},
                    "url_scheme": {"type": "string", "enum": ["http", "https"]},
                    "image_updates_enabled": {"type": "boolean"},
                    "image_updates_cron": {"type": "string"},
                },
            }
        )

        from fastapi import APIRouter

        router = APIRouter()

        @router.get("/widgets/matrix")
        async def matrix_widget_data() -> dict:
            return {"data": await self._catalog.list_services(), "meta": {}}

        @router.get("/stats")
        async def container_stats() -> dict:
            """CPU/RAM je laufendem Container, getrennt von der Liste (dauert ein paar
            Sekunden, siehe `DockerServiceCatalog.container_stats()`)."""
            return {"data": await self._catalog.container_stats()}

        @router.get("/image-updates")
        async def image_updates() -> dict:
            """Letzter Stand der Image-Pruefung je Host und Container (nur Speicher, kein
            Zugriff auf die Hosts -- die Pruefung selbst loest der POST darunter aus). Dazu
            `applying`/`applied`: laufende und zuletzt beendete Updates."""
            await self._images.docker_hosts()  # nur die Host-Liste aus der Datenbank; raeumt entfernte Hosts aus dem Stand
            return self._snapshot()

        # Sicherheits-Nachtrag: dieser
        # Router hing OHNE Berechtigung (`include_router(router)`) -- laut ApiHandle-
        # Docstring heisst das: oeffentlich, ohne Login erreichbar. Lesen braucht jetzt
        # `hosts.read`, alles Ausloesende `hosts.execute`.
        ctx.api.include_router(router, permission="hosts.read")

        from contextlib import AsyncExitStack

        from fastapi import HTTPException, Query, status

        logs_router = APIRouter()

        async def _docker_host(host_id: str):  # noqa: ANN202 - nodvard_sdk.Host
            """Nur Hosts mit dem Docker-Tag -- sonst liefe `docker ...` per SSH auf
            beliebigen Hosts, nur weil jemand eine ID kennt."""
            host = await ctx.hosts.get(host_id)
            tag = (await ctx.settings.get()).get("docker_host_tag") or "docker"
            if host is None or tag not in host.tags:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kein Docker-Host.")
            return host

        @logs_router.post("/image-updates/check", status_code=status.HTTP_202_ACCEPTED)
        async def image_updates_check(host_id: str | None = None, force: bool = False) -> dict:
            """Startet die Pruefung im Hintergrund (alle Docker-Hosts oder nur `host_id`) und
            antwortet sofort mit dem Stand (`hosts[..].checking`). Lesend -- aber sie
            oeffnet SSH-Verbindungen und fragt Registries, deshalb `hosts.execute` wie
            bei den Logs. `force`: Antworten der Registry nicht aus dem 6-Stunden-Speicher
            nehmen."""
            hosts = [await _docker_host(host_id)] if host_id else await self._images.docker_hosts()
            self._images.start(hosts, force=force)
            return self._snapshot()

        @logs_router.get("/containers/{host_id}/{container}/image-update/plan")
        async def image_update_plan(host_id: str, container: str) -> dict:
            """Die Uebersicht vor dem Einspielen -- nur lesend: was passiert, welcher Befehl
            laeuft, was ist betroffen, welche Warnungen gibt es. `ok: false` mit Grund, wenn
            sich der Container so nicht aktualisieren laesst."""
            if not CONTAINER_NAME_RE.match(container):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Containername.")
            host = await _docker_host(host_id)
            try:
                plan = await self._applier.plan(host, container)
            except Exception as exc:  # noqa: BLE001 - dem Nutzer zeigen, nicht als 500 verstecken
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Host nicht erreichbar: {str(exc) or type(exc).__name__}"
                ) from exc
            if isinstance(plan, NotUpdatable):
                return {"ok": False, "kind": plan.kind, "reason": plan.why}
            return plan.as_response()

        @logs_router.post("/containers/{host_id}/{container}/image-update", status_code=status.HTTP_202_ACCEPTED)
        async def image_update_apply(host_id: str, container: str, body: _ApplyIn, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
            """Schlaegt das Einspielen vor (Gate-Aktion `container.image_update`) -- fuer genau die
            Uebersicht, die der Nutzer gesehen hat (`plan_id`). Hat sich der Stand seitdem
            geaendert, 409 mit Aufforderung, neu zu laden."""
            if not CONTAINER_NAME_RE.match(container):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Containername.")
            host = await _docker_host(host_id)
            try:
                plan = await self._applier.plan(host, container)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Host nicht erreichbar: {str(exc) or type(exc).__name__}"
                ) from exc
            if isinstance(plan, NotUpdatable):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.why)
            if plan.plan_id != body.plan_id:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Der Stand hat sich geändert – bitte den Plan neu laden.")
            return await self._applier.propose(host, plan, actor)

        @logs_router.get("/containers/{host_id}/{container}/inspect")
        async def container_inspect(host_id: str, container: str) -> dict:
            """Details wie in Portainer: Image, Neustart-Regel, Ports, Mounts, Netze,
            Compose-Projekt, Namen der Umgebungsvariablen (nie ihre Werte)."""
            if not CONTAINER_NAME_RE.match(container):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Containername.")
            host = await _docker_host(host_id)
            result = await ctx.exec.run(host, f"docker inspect --type container {container}", timeout_s=20)
            if result.exit_code != 0:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=result.stderr.strip() or "docker inspect fehlgeschlagen")
            try:
                raw = json.loads(result.stdout)[0]
            except (ValueError, IndexError, TypeError) as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unerwartete Antwort von docker inspect.") from exc
            return summarize_inspect(raw)

        @logs_router.get("/hosts/{host_id}/docker")
        async def docker_storage(host_id: str) -> dict:
            """Belegung (Images, Container, Volumes, Build-Cache) und die Images des
            Hosts -- was man sonst per `docker system df` auf der Konsole nachsieht."""
            host = await _docker_host(host_id)
            df = await ctx.exec.run(host, "docker system df --format '{{json .}}'", timeout_s=60)
            images = await ctx.exec.run(host, "docker images --format '{{json .}}'", timeout_s=30)
            if df.exit_code != 0:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=df.stderr.strip() or "docker system df fehlgeschlagen")
            return {
                "disk": parse_system_df(df.stdout),
                "images": parse_images(images.stdout) if images.exit_code == 0 else [],
            }

        @logs_router.get("/containers/{host_id}/{container}/logs")
        async def container_logs(
            host_id: str,
            container: str,
            tail: int = Query(default=200, ge=0, le=_MAX_TAIL),
            follow: bool = True,
        ) -> StreamingResponse:
            """Live-Logs eines Containers als laufender Text-Strom (`docker logs -f`).
            Lesend, aber nicht harmlos (Logs enthalten gern Tokens/Adressen) -- deshalb
            dieselbe Berechtigung wie das Terminal (`hosts.execute`, siehe
            `include_router` unten), nicht nur `hosts.read`.

            Der Strom wird VOR der Antwort geoeffnet: ein nicht erreichbarer Host wird
            so ein ehrlicher 502, statt eines 200 mit leerem Koerper. Endet die
            Verbindung zum Browser, beendet das Verlassen von `ctx.exec.stream()` den
            entfernten Prozess (PTY-Hangup)."""
            if not CONTAINER_NAME_RE.match(container):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Containername.")
            host = await ctx.hosts.get(host_id)
            if host is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")

            command = f"docker logs --tail {tail} --timestamps {'--follow ' if follow else ''}{container}"
            stack = AsyncExitStack()
            try:
                chunks = await stack.enter_async_context(ctx.exec.stream(host, command))
            except Exception as exc:  # noqa: BLE001 - dem Nutzer zeigen, nicht als 500 verstecken
                await stack.aclose()
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Logs nicht abrufbar: {exc}"
                ) from exc

            async def _body():  # noqa: ANN202
                try:
                    async for chunk in chunks:
                        # PTY liefert `\r\n` -- fuer die Log-Ansicht reine Zeilenumbrueche.
                        yield chunk.replace(b"\r", b"")
                finally:
                    await stack.aclose()

            return StreamingResponse(
                _body(),
                media_type="text/plain; charset=utf-8",
                # Kein Zwischenpuffern durch einen vorgeschalteten Proxy (nginx/NPM),
                # sonst kommen "Live"-Logs in Klumpen an.
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        ctx.api.include_router(logs_router, permission="hosts.execute")

        ctx.ui.register_page(
            PageSpec(
                id="matrix",
                path="/matrix",
                title="Service-Matrix",
                icon="layout-grid",
                nav_section="Infrastruktur",
                nav_order=30,
                component="ServiceMatrixPage",
            )
        )

        ctx.ui.register_widget(
            WidgetSpec(
                id="matrix",
                title="Service-Matrix",
                icon="layout-grid",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=30),
                data_endpoint="widgets/matrix",
                view=StatusGridView(
                    tile_title="{{ name }}",
                    tile_subtitle="{{ host }}",
                    tile_tone="{{ tone }}",
                    tile_link="{{ url }}",
                ),
            )
        )

    def _snapshot(self) -> dict[str, Any]:
        """Stand der Pruefung (`data`, `hosts`) plus laufende/beendete Updates (`applying`, `applied`)."""
        return {**self._images.snapshot(), **self._applier.snapshot()}

    async def on_start(self, ctx: ExtensionContext) -> None:
        # Nicht in setup(): das liest die Einstellungen (I/O, docs/02 "setup = nur
        # registrieren"). Die Server-Seite fragt die Werkzeuge erst zur Anfragezeit ab.
        settings = await ctx.settings.get()
        _apply_docker_tag(ctx, settings.get("docker_host_tag") or "docker")
        await register_job(ctx, self._images, settings)
        # Ein Update, das beim letzten Beenden noch auf dem Host lief, wird hier weiterverfolgt und gemeldet.
        # Kein Neustart der Aufgabe: sie wartet bis zur Frist des Laufs und meldet dann selbst.
        ctx.spawn(self._applier.resume_interrupted(), name="service-matrix-image-update-resume", restart=False)

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        return HealthReport(healthy=True)

    async def on_settings_changed(self, ctx: ExtensionContext, values: dict[str, Any]) -> None:
        _apply_docker_tag(ctx, values.get("docker_host_tag") or "docker")
        await register_job(ctx, self._images, await ctx.settings.get())
