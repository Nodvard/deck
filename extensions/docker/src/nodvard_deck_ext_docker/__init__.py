"""Docker: entdeckt Container per SSH und zeigt sie als Kacheln, mit Container-Verwaltung und Image-Updates; alles
Ändernde (Container-Aktionen, Einspielen von Image-Updates) läuft über das Aktions-Gate."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
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
from pydantic import BaseModel, Field, field_validator

from . import commands
from .capabilities import (
    CONTAINER_NAME_RE,
    MAX_URL_LENGTH,
    MAX_URL_OVERRIDES,
    DockerContainerExecutor,
    DockerMaintenanceExecutor,
    DockerServiceCatalog,
    clean_tile_url,
    container_action_specs,
    url_origin,
    url_overrides,
)
from .applier import IMAGE_UPDATE_SPEC, ImageApplier, ImageUpdateExecutor
from .docker_details import parse_images, parse_system_df, summarize_inspect
from .image_apply import NotUpdatable
from .image_updates import ImageUpdateService, register_job
from .places import DockerFilePlaces
from .resources import DockerResourceExecutor, DockerResources, ResourceProblem, resource_action_specs
from .shell import ContainerShell
from .adopt import AdoptedStacks, adopt_routes
from .editor import DockerStackSaveExecutor, StackEditor, editor_routes, save_action_spec
from .new_stack import DockerStackCreateExecutor, NewStacks, create_action_spec, new_routes
from .stack_delete import DockerStackDeleteExecutor, StackDelete, delete_action_spec, delete_routes
from .stack_backup import VolumeBackup
from .stack_restore import DockerStackRestoreExecutor, StackRestore, restore_routes, restore_specs
from .stack_update import DockerStackUpdateExecutor, StackBackups, StackUpdate, stack_actions_specs, update_routes
from .locks import StackLocks
from .stack_actions import DockerStackExecutor, stack_action_specs
from .stacks import DockerStacks, stack_routes
from .container_to_stack import ContainerToStack, DockerContainerToStackExecutor, convert_action_spec, convert_routes
from .registries import DockerRegistryExecutor, Registries, login_spec, registry_routes
from .portainer_move import DockerPortainerMoveExecutor, PortainerMove, move_action_spec, move_routes
from .templates import template_routes
from .operate import DEFAULT_TAG as OPERATE_DEFAULT_TAG
from .operate import FULL_SUDO_CHECK, GROUP_CHECK, ROOT_CHECK
from .operate import HELPER as OPERATE_HELPER
from .operate import OperateContext, is_docker_host, is_operate_host
from .operate import docker_hosts as operate_hosts
from .operate import refused_text as operate_refused
from .operate import tags as operate_tags

log = logging.getLogger("nodvard_deck.ext.docker")


class _TileUrlIn(BaseModel):
    url: str | None = Field(..., max_length=MAX_URL_LENGTH)
    """Eigene Adresse der Kachel; leer oder `null` nimmt wieder die erkannte. Pflichtfeld: `{}` ist kein Zurücksetzen."""

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return None if value is None or not value.strip() else clean_tile_url(value)


class _ApplyIn(BaseModel):
    plan_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    """Die Kennung der Übersicht, die der Nutzer gesehen hat (`GET .../image-update/plan`)."""


def _apply_docker_tag(ctx: ExtensionContext, settings: dict[str, Any]) -> None:
    """Kachel, Anforderungen und Aktionen folgen den eingestellten Markierungen (gleiche ID/Typ ersetzt)."""
    tag, operate_tag = operate_tags(settings)
    _register_host_tool(ctx, [tag, operate_tag])
    _register_docker_requirement(ctx, tag)
    _register_operate_requirement(ctx, operate_tag)
    _register_operate_limits(ctx, operate_tag, tag)
    for spec in container_action_specs(tag, operate_tag) + resource_action_specs(tag) + stack_action_specs(tag):
        ctx.actions.register(spec)


def _register_host_tool(ctx: ExtensionContext, tags: list[str]) -> None:
    """Kachel "Container" auf der Server-Seite jedes Docker-Hosts. Der Tag ist einstellbar (`docker_host_tag`), deshalb
    wird sie bei jeder Änderung neu gemeldet."""
    ctx.ui.register_host_tool(HostToolSpec(
        id="containers", title="Container", icon="layout-grid", category="services",
        description="Container ansehen, starten, stoppen, neu starten, Logs lesen", path="/matrix?host={host_id}",
        tags=tags,
    ))


def _register_operate_requirement(ctx: ExtensionContext, tag: str) -> None:
    """Server im Modus „Nur bedienen“: statt der Gruppe docker das Hilfsskript `docker-root`, das die Einrichtung root-eigen
    ablegt und per sudo nur dieses Skript erlaubt."""
    ctx.ui.register_host_requirement(HostRequirementSpec(
        id="docker-operate", label="Docker nur bedienen (ohne Gruppe docker)",
        check_command=f"sudo -n {OPERATE_HELPER} version", ok_text="Das Hilfsskript für Docker antwortet.",
        fail_hint="Den Zugang neu einrichten (Einstellungen, Server & Zugänge): Die Einrichtung legt das Hilfsskript ab und erlaubt "
        "es per sudo.",
        needs_root=True, root_reason="Container ansehen, starten, stoppen und Logs lesen, ohne Gruppe docker",
        tags=[tag], order=31,
    ))


def _register_operate_limits(ctx: ExtensionContext, tag: str, full_tag: str) -> None:
    """Ein Zugang als root, mit Gruppe docker oder mit voller sudo-Regel darf auch im Modus „Nur bedienen“ alles."""
    both = f"Hat der Server auch die Markierung „{full_tag}“, ist er ein voller Docker-Server und das so gewollt. "
    ctx.ui.register_host_requirement(HostRequirementSpec(
        id="docker-operate-root", label="Nur bedienen: Zugang ist nicht root",
        check_command=ROOT_CHECK, ok_text="Der Zugang meldet sich nicht als root an.",
        fail_hint=both + "Sonst: Der Zugang meldet sich als root an und darf hier ohnehin alles, „Nur bedienen“ schränkt ihn "
        "also nicht ein. Richte den Zugang mit einem eigenen Benutzer statt root neu ein (Einstellungen, Server & Zugänge).",
        tags=[tag], order=32,
    ))
    ctx.ui.register_host_requirement(HostRequirementSpec(
        id="docker-operate-group", label="Nur bedienen: Zugang nicht in der Gruppe docker",
        check_command=GROUP_CHECK,
        ok_text="Der Zugang kommt ohne sudo nicht an Docker heran, oder er ist root (dann gilt „Zugang ist nicht root“).",
        fail_hint=both + "Sonst: Der Zugang kann Docker noch ohne sudo benutzen, meist über die Gruppe docker, und ist damit "
        "auf diesem Server praktisch root. Nimm ihn aus der Gruppe: sudo gpasswd -d {user} docker (gilt ab der nächsten "
        "Anmeldung, „Verbindung prüfen“ meldet sich dafür neu an).",
        tags=[tag], order=33,
    ))
    ctx.ui.register_host_requirement(HostRequirementSpec(
        id="docker-operate-sudo", label="Nur bedienen: keine volle sudo-Regel",
        check_command=FULL_SUDO_CHECK,
        ok_text="sudo erlaubt dem Zugang nicht alles, oder er ist root (dann gilt „Zugang ist nicht root“).",
        fail_hint=both + "Sonst: sudo erlaubt dem Zugang hier noch alles ohne Passwort, und die Einrichtung nimmt so eine Regel "
        "nie zurück. Braucht kein anderes Modul hier volle Rechte, entferne sie, sobald das Hilfsskript antwortet und der "
        "Zugang nicht mehr in der Gruppe docker ist (Erst-Einrichtung, Abschnitt 6). Welche Datei die Regel enthält, zeigt: "
        "sudo grep -rl NOPASSWD /etc/sudoers /etc/sudoers.d",
        tags=[tag], order=34,
    ))


def _register_docker_requirement(ctx: ExtensionContext, tag: str) -> None:
    """Der Einrichtungsbefehl für einen Server (Einstellungen, Server & Zugänge) bietet auf Docker-Hosts die Gruppe
    "docker" an, und "Verbindung prüfen" testet sie: die Extension arbeitet mit dem SSH-Benutzer ohne sudo. Wie die
    Kachel folgt auch das dem Tag."""
    ctx.ui.register_host_requirement(HostRequirementSpec(
        id="docker-group", label="Docker ohne sudo (Container-Verwaltung)",
        check_command="docker ps -q", ok_text="Docker lässt sich ohne sudo benutzen.",
        fail_hint="Benutzer zur Gruppe docker hinzufügen: sudo usermod -aG docker {user}",
        unix_group="docker", tags=[tag], order=30,
    ))


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        # Jeder Befehl des Moduls geht durch `OperateExec`, auch auf Servern im Modus „Nur bedienen“.
        ctx = OperateContext(ctx)  # type: ignore[assignment]
        self._ctx = ctx
        self._catalog = DockerServiceCatalog(ctx)
        self._images = ImageUpdateService(ctx)
        self._applier = ImageApplier(ctx, self._images)
        self._images.apply_hints = self._applier.hints
        ctx.capabilities.provide(self._catalog)
        ctx.capabilities.provide(DockerFilePlaces(ctx, self._catalog))
        ctx.capabilities.provide(DockerContainerExecutor(ctx))
        # Eine Sperre je Stack für alle, die ein Compose-Projekt ändern, auch für den Neustart.
        self._locks = StackLocks()
        ctx.capabilities.provide(DockerMaintenanceExecutor(ctx, self._locks))
        ctx.capabilities.provide(ImageUpdateExecutor(self._applier))
        ctx.capabilities.provide(ContainerShell(ctx))
        self._resources = DockerResources(ctx)
        ctx.capabilities.provide(DockerResourceExecutor(ctx, self._resources))
        # Nicht in `container_action_specs` (die hängen am Docker-Tag): den Befehl baut immer die Extension, die Aktion
        # kommt nur über die Route unten, nie über die Server-Seite.
        ctx.actions.register(IMAGE_UPDATE_SPEC)
        ctx.actions.register(save_action_spec())
        ctx.actions.register(create_action_spec())
        ctx.actions.register(delete_action_spec())
        ctx.actions.register(move_action_spec())
        ctx.actions.register(convert_action_spec())
        for spec in login_spec():
            ctx.actions.register(spec)
        for spec in restore_specs():
            ctx.actions.register(spec)
        for spec in stack_actions_specs():
            ctx.actions.register(spec)
        # Mit dem Standard-Tag; on_start meldet sie mit dem eingestellten Tag neu an.
        for spec in container_action_specs("docker", OPERATE_DEFAULT_TAG) + resource_action_specs() + stack_action_specs():
            ctx.actions.register(spec)

        ctx.settings.declare(
            {
                "type": "object",
                "properties": {
                    "docker_host_tag": {"type": "string"},
                    "docker_operate_tag": {"type": "string"},
                    "url_scheme": {"type": "string", "enum": ["http", "https"]},
                    "image_updates_enabled": {"type": "boolean"},
                    "image_updates_cron": {"type": "string"},
                    "image_update_auto_rollback": {"type": "boolean"},
                    "url_overrides": {"type": "object", "additionalProperties": {"type": "string"}},
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
            """CPU/RAM je laufendem Container, Neustarts je Container und je Host CPU-Zahl und Arbeitsspeicher, getrennt von
            der Liste (dauert ein paar Sekunden, siehe `DockerServiceCatalog.measure()`)."""
            return await self._catalog.measure()

        @router.get("/hosts")
        async def docker_hosts() -> dict:
            """Die Server mit Docker-Markierung, auch ohne Container; nur aus der Datenbank. `mode`: `operate` für „Nur
            bedienen“, sonst `full`."""
            await self._images.docker_hosts()  # räumt entfernte Hosts aus dem Stand der Image-Prüfung
            return {"hosts": [{"id": h.id, "name": h.name, "mode": "operate" if operate else "full"} for h, operate in await operate_hosts(ctx)]}

        @router.get("/image-updates")
        async def image_updates() -> dict:
            """Letzter Stand der Image-Prüfung je Host und Container (nur Speicher, kein Zugriff auf die Hosts; die Prüfung
            löst der POST darunter aus). Dazu `applying`/`applied`: laufende und zuletzt beendete Updates."""
            await self._images.docker_hosts()  # nur die Host-Liste aus der Datenbank; räumt entfernte Hosts aus dem Stand
            return self._snapshot()

        # Ein Router ohne `permission` wäre öffentlich (ohne Login erreichbar, siehe ApiHandle): Lesen braucht
        # `hosts.read`, alles Auslösende `hosts.execute`.
        ctx.api.include_router(router, permission="hosts.read")

        from contextlib import AsyncExitStack

        from fastapi import HTTPException, Query, status

        logs_router = APIRouter()

        async def _docker_host(host_id: str):  # noqa: ANN202 - nodvard_sdk.Host
            """Nur Hosts mit Docker-Tag, sonst liefe `docker ...` per SSH auf jedem Host, dessen ID jemand kennt."""
            host = await ctx.hosts.get(host_id)
            if host is None or not is_docker_host(host, await ctx.settings.get()):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kein Docker-Host.")
            return host

        async def _full_docker_host(host_id: str):  # noqa: ANN202 - nodvard_sdk.Host
            """Wie `_docker_host`, aber 409 mit Grund für Server im Modus „Nur bedienen“, statt dort abgelehnter Befehle."""
            host = await _docker_host(host_id)
            settings = await ctx.settings.get()
            if is_operate_host(host, settings):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=operate_refused(operate_tags(settings)[0]))
            return host

        @logs_router.post("/image-updates/check", status_code=status.HTTP_202_ACCEPTED)
        async def image_updates_check(host_id: str | None = None, force: bool = False) -> dict:
            """Startet die Prüfung im Hintergrund (alle Docker-Hosts oder nur `host_id`) und antwortet sofort mit dem Stand
            (`hosts[..].checking`). Lesend, aber sie öffnet SSH-Verbindungen und fragt Registries, deshalb `hosts.execute`
            wie bei den Logs. `force`: Antworten der Registry nicht aus dem 6-Stunden-Speicher nehmen."""
            hosts = [await _full_docker_host(host_id)] if host_id else await self._images.docker_hosts()
            self._images.start(hosts, force=force)
            return self._snapshot()

        @logs_router.get("/containers/{host_id}/{container}/image-update/plan")
        async def image_update_plan(host_id: str, container: str) -> dict:
            """Die Übersicht vor dem Einspielen, nur lesend: was passiert, welcher Befehl läuft, was ist betroffen, welche
            Warnungen es gibt. `ok: false` mit Grund, wenn sich der Container so nicht aktualisieren lässt."""
            if not CONTAINER_NAME_RE.match(container):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Containername.")
            host = await _full_docker_host(host_id)
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
            """Schlägt das Einspielen vor (Gate-Aktion `container.image_update`), und zwar für genau die Übersicht, die der
            Nutzer gesehen hat (`plan_id`). Hat sich der Stand seitdem geändert, 409 mit der Aufforderung, neu zu laden."""
            if not CONTAINER_NAME_RE.match(container):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Containername.")
            host = await _full_docker_host(host_id)
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
            result = await ctx.exec.run(host, commands.inspect_container(container), timeout_s=20)
            if result.exit_code != 0:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=result.stderr.strip() or "docker inspect fehlgeschlagen")
            # Auch eine Antwort in anderer Form, etwa ein Objekt statt einer Liste, ist ein Fehler auf dem Server; das Log zeigt, woran es lag.
            try:
                return summarize_inspect(json.loads(result.stdout)[0])
            except (ValueError, LookupError, TypeError, AttributeError) as exc:
                log.warning("docker_inspect_unreadable host=%s container=%s", host.id, container, exc_info=True)
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unerwartete Antwort von docker inspect.") from exc

        @logs_router.get("/hosts/{host_id}/docker")
        async def docker_storage(host_id: str) -> dict:
            """Belegung (Images, Container, Volumes, Build-Cache) und Images des Hosts, wie `docker system df` sie zeigt."""
            host = await _full_docker_host(host_id)
            df = await ctx.exec.run(host, commands.system_df(), timeout_s=60)
            images = await ctx.exec.run(host, commands.images(), timeout_s=30)
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
            tail: int = Query(default=200, ge=0, le=commands.MAX_LOG_TAIL),
            follow: bool = True,
            since: str | None = None,
        ) -> StreamingResponse:
            """Live-Logs eines Containers als laufender Text-Strom (`docker logs -f`), auf Wunsch nur die der letzten Zeit
            (`since`, etwa `10m` oder `24h`). Lesend, aber nicht harmlos (Logs enthalten gern Tokens und Adressen):
            dieselbe Berechtigung wie das Terminal (`hosts.execute`)."""
            if not CONTAINER_NAME_RE.match(container):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Containername.")
            if since is not None and not commands.LOG_SINCE_RE.match(since):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Zeitraum.")
            host = await _docker_host(host_id)

            command = commands.logs(container, tail, follow=follow, since=since)
            # Strom vor der Antwort öffnen: ein unerreichbarer Host wird so ein ehrlicher 502 statt 200 mit leerem Körper.
            stack = AsyncExitStack()
            try:
                chunks = await stack.enter_async_context(ctx.exec.stream(host, command))
            except Exception as exc:  # noqa: BLE001 - dem Nutzer zeigen, nicht als 500 verstecken
                await stack.aclose()
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Logs nicht abrufbar: {exc}"
                ) from exc

            async def _body():  # noqa: ANN202
                # Bricht der Browser ab, beendet das Verlassen von `ctx.exec.stream()` den entfernten Prozess (PTY-Hangup).
                try:
                    async for chunk in chunks:
                        # PTY liefert `\r\n`, für die Log-Ansicht reichen Zeilenumbrüche.
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

        resources_router = APIRouter()

        async def _resources(host_id: str):  # noqa: ANN202 - resources.Snapshot
            host = await _full_docker_host(host_id)
            try:
                return await self._resources.snapshot(host)
            except ResourceProblem as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

        @resources_router.get("/hosts/{host_id}/images")
        async def list_images(host_id: str) -> dict:
            """Images des Servers mit Größe, Namen und den Containern, die sie nutzen."""
            return {"host_id": host_id, "images": (await _resources(host_id)).images}

        @resources_router.get("/hosts/{host_id}/volumes")
        async def list_volumes(host_id: str) -> dict:
            """Volumes des Servers mit Größe und den Containern, die sie nutzen; Werte nie, nur Namen."""
            return {"host_id": host_id, "volumes": (await _resources(host_id)).volumes}

        @resources_router.get("/hosts/{host_id}/networks")
        async def list_networks(host_id: str) -> dict:
            """Netze des Servers mit den Containern daran; `error`, wenn nur diese Liste nicht lesbar war."""
            snapshot = await _resources(host_id)
            return {"host_id": host_id, "networks": snapshot.networks, "error": snapshot.network_error}

        # Lesend, aber jeder Abruf öffnet SSH und lässt Docker den Speicher zählen, deshalb wie `/hosts/{id}/docker`.
        ctx.api.include_router(resources_router, permission="hosts.execute")
        # Die Dateien liest SFTP mit den Zugangsdaten des Servers, wie im Dateimanager des Kerns.
        adopted = AdoptedStacks(Path(str(ctx.data_dir)))
        stacks = DockerStacks(ctx, adopted)
        stack_executor = DockerStackExecutor(ctx, stacks, adopted, self._locks)
        stack_executor.locks.outside = self._applier.project_busy
        self._applier.stack_busy = stack_executor.locks.held
        ctx.capabilities.provide(stack_executor)
        editor = StackEditor(ctx, stack_executor, Path(str(ctx.data_dir)))
        ctx.capabilities.provide(DockerStackSaveExecutor(ctx, editor, stack_executor))
        ctx.api.include_router(stack_routes(stacks, _full_docker_host), permission="hosts.execute")
        ctx.api.include_router(adopt_routes(ctx, stacks, adopted, _full_docker_host), permission="hosts.execute")
        ctx.api.include_router(editor_routes(editor, _full_docker_host, ctx.api.current_actor), permission="hosts.execute")
        # Ordner für neue Stacks und eigene Kachel-Adressen schreiben beide das ganze Einstellungsdokument.
        settings_lock = asyncio.Lock()
        new = NewStacks(ctx, editor, stacks, adopted, settings_lock)
        create_executor = DockerStackCreateExecutor(ctx, new, adopted, stack_executor)
        ctx.capabilities.provide(create_executor)
        move = PortainerMove(ctx, stacks, adopted, new)
        ctx.capabilities.provide(DockerPortainerMoveExecutor(ctx, move, create_executor, adopted, stack_executor))
        ctx.api.include_router(move_routes(move, editor, _full_docker_host, ctx.api.current_actor), permission="hosts.execute")
        convert = ContainerToStack(ctx, new, adopted, stacks)
        ctx.capabilities.provide(DockerContainerToStackExecutor(ctx, convert, create_executor, adopted, stack_executor))
        ctx.api.include_router(convert_routes(convert, editor, _full_docker_host, ctx.api.current_actor), permission="hosts.execute")
        known_registries = Registries(ctx)
        ctx.capabilities.provide(DockerRegistryExecutor(ctx, known_registries))
        ctx.api.include_router(registry_routes(known_registries, editor, _full_docker_host, ctx.api.current_actor), permission="hosts.execute")
        ctx.api.include_router(new_routes(new, _full_docker_host, ctx.api.current_actor), permission="hosts.execute")
        ctx.api.include_router(template_routes(), permission="hosts.execute")
        update = StackUpdate(ctx, stacks, self._images, stack_executor, StackBackups(Path(str(ctx.data_dir))), VolumeBackup(ctx, self._resources))
        update_executor = DockerStackUpdateExecutor(ctx, update, self._images)
        ctx.capabilities.provide(update_executor)
        ctx.api.include_router(update_routes(update, editor, _full_docker_host, ctx.api.current_actor), permission="hosts.execute")
        restore = StackRestore(ctx, update, self._resources)
        ctx.capabilities.provide(DockerStackRestoreExecutor(ctx, restore))
        ctx.api.include_router(restore_routes(restore, editor, _full_docker_host, ctx.api.current_actor), permission="hosts.execute")
        delete = StackDelete(ctx, stacks, adopted, new, stack_executor)
        ctx.capabilities.provide(DockerStackDeleteExecutor(ctx, delete, adopted, update_executor.drop_backups))
        ctx.api.include_router(delete_routes(delete, editor, _full_docker_host, ctx.api.current_actor), permission="hosts.execute")

        apps_router = APIRouter()

        @apps_router.put("/containers/{host_id}/{container}/url")
        async def set_tile_url(host_id: str, container: str, body: _TileUrlIn, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
            """Eigene Adresse für die Kachel eines Containers (z. B. wenn die erkannte nicht stimmt). Bleibt gespeichert,
            auch wenn der Container neu erstellt wird (gleicher Name); `url: null` nimmt wieder die erkannte. Wie das
            Bearbeiten der Apps-Seite nur mit `apps.write`."""
            if not CONTAINER_NAME_RE.match(container):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Containername.")
            host = await _docker_host(host_id)
            key = f"{host.id}:{container}"
            async with settings_lock:
                settings = await ctx.settings.get()
                overrides = url_overrides(settings)
                if body.url is None:
                    overrides.pop(key, None)
                else:
                    if key not in overrides and len(overrides) >= MAX_URL_OVERRIDES:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=f"Es sind schon {MAX_URL_OVERRIDES} eigene Adressen gespeichert. Bitte erst alte zurücksetzen.",
                        )
                    overrides[key] = body.url
                await ctx.settings.set({**settings, "url_overrides": overrides})
            try:
                # Der Protokoll-Name bleibt aus der Zeit vor dem Umzug, gespeicherte Einträge und Filter hängen daran.
                await ctx.audit.log(
                    action="service_matrix.tile_url", outcome="success", target_type="container", target_id=key,
                    detail={"custom": body.url is not None, "url": url_origin(body.url) if body.url else None}, actor=actor,
                )
            except Exception:  # noqa: BLE001 - gespeichert ist gespeichert
                pass
            return {"id": key, "url": body.url}

        ctx.api.include_router(apps_router, permission="apps.write")

        ctx.ui.register_page(
            PageSpec(
                id="matrix",
                path="/matrix",
                title="Docker",
                icon="layout-grid",
                nav_section="Infrastruktur",
                nav_order=30,
                component="ContainersPage",
            )
        )

        ctx.ui.register_widget(
            WidgetSpec(
                id="matrix",
                title="Container",
                group="apps",
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
        """Stand der Prüfung (`data`, `hosts`) plus laufende/beendete Updates (`applying`, `applied`)."""
        return {**self._images.snapshot(), **self._applier.snapshot()}

    async def on_start(self, ctx: ExtensionContext) -> None:
        # Nicht in setup(): das liest die Einstellungen (I/O), setup soll nur registrieren. Die Server-Seite fragt die
        # Werkzeuge erst zur Anfragezeit ab.
        settings = await ctx.settings.get()
        _apply_docker_tag(ctx, settings)
        await register_job(ctx, self._images, settings)
        # Ein Update, das beim letzten Beenden noch auf dem Host lief, wird hier weiterverfolgt und gemeldet.
        # Kein Neustart der Aufgabe: sie wartet bis zur Frist des Laufs und meldet dann selbst.
        ctx.spawn(self._applier.resume_interrupted(), name="docker-image-update-resume", restart=False)

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        return HealthReport(healthy=True)

    async def on_settings_changed(self, ctx: ExtensionContext, values: dict[str, Any]) -> None:
        _apply_docker_tag(ctx, values)
        await register_job(ctx, self._images, await ctx.settings.get())
