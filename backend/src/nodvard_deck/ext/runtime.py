"""Flüchtiger Zustand des Extension-Hosts: UI-Katalog, Capabilities, geladene Extensions, Router, Hintergrundtasks.

Die Registry-Tabelle sagt, was aktiv sein soll, dieser Zustand, was gerade läuft; der Lifespan bringt beide in Deckung.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, FastAPI
from nodvard_sdk import ExtensionManifest, HostRequirementSpec, HostToolSpec, PageSpec, WidgetSpec
from nodvard_sdk.actions import ActionSpec
from starlette.routing import BaseRoute

logger = logging.getLogger("nodvard_deck.ext")


@dataclass
class NavEntry:
    ext_id: str
    section: str | None
    order: int
    page: PageSpec


class UiRegistry:
    """Bestand an Seiten, Widgets und Host-Angaben; `clear_extension()` macht Deaktivieren ohne Neustart möglich."""

    def __init__(self) -> None:
        self._pages: dict[str, list[PageSpec]] = {}
        self._widgets: dict[str, list[WidgetSpec]] = {}
        self._host_tools: dict[str, list[HostToolSpec]] = {}
        self._host_requirements: dict[str, list[HostRequirementSpec]] = {}

    def register_page(self, ext_id: str, spec: PageSpec) -> None:
        self._pages.setdefault(ext_id, []).append(spec)

    def register_widget(self, ext_id: str, spec: WidgetSpec) -> None:
        self._widgets.setdefault(ext_id, []).append(spec)

    def register_host_tool(self, ext_id: str, spec: HostToolSpec) -> None:
        # Gleiche ID ersetzt: so meldet eine Extension ihr Werkzeug nach `on_settings_changed` mit neuen Bedingungen.
        tools = [t for t in self._host_tools.get(ext_id, []) if t.id != spec.id]
        tools.append(spec)
        self._host_tools[ext_id] = tools

    def register_host_requirement(self, ext_id: str, spec: HostRequirementSpec) -> None:
        # Wie bei den Werkzeugen ersetzt die gleiche ID.
        specs = [r for r in self._host_requirements.get(ext_id, []) if r.id != spec.id]
        specs.append(spec)
        self._host_requirements[ext_id] = specs

    def clear_extension(self, ext_id: str) -> None:
        self._pages.pop(ext_id, None)
        self._widgets.pop(ext_id, None)
        self._host_tools.pop(ext_id, None)
        # Eine deaktivierte Extension darf nichts mehr verlangen (Einrichtungsbefehl, Prüfung).
        self._host_requirements.pop(ext_id, None)

    def all_pages(self) -> list[tuple[str, PageSpec]]:
        return [(ext_id, p) for ext_id, specs in self._pages.items() for p in specs]

    def all_widgets(self) -> list[tuple[str, WidgetSpec]]:
        return [(ext_id, w) for ext_id, specs in self._widgets.items() for w in specs]

    def all_host_tools(self) -> list[tuple[str, HostToolSpec]]:
        return [(ext_id, t) for ext_id, specs in self._host_tools.items() for t in specs]

    def all_host_requirements(self) -> list[tuple[str, HostRequirementSpec]]:
        return [(ext_id, r) for ext_id, specs in self._host_requirements.items() for r in specs]


class CapabilityRegistry:
    def __init__(self) -> None:
        self._by_protocol: dict[type, list[tuple[str, Any]]] = {}

    def provide(self, ext_id: str, protocol: type, implementation: Any) -> None:
        self._by_protocol.setdefault(protocol, []).append((ext_id, implementation))

    def clear_extension(self, ext_id: str) -> None:
        for impls in self._by_protocol.values():
            impls[:] = [(eid, impl) for eid, impl in impls if eid != ext_id]

    def query(self, protocol: type, *, allowed_ext_ids: set[str] | None = None) -> list[Any]:
        impls = self._by_protocol.get(protocol, [])
        if allowed_ext_ids is None:
            return [impl for _, impl in impls]
        return [impl for ext_id, impl in impls if ext_id in allowed_ext_ids]

    def query_named(self, protocol: type) -> list[tuple[str, Any]]:
        """Wie `query()` ohne Filter, aber mit der Kennung der Erweiterung je Implementierung."""
        return list(self._by_protocol.get(protocol, []))

    def provided_by(self, protocol: type, ext_id: str) -> Any | None:
        """Die Implementierung einer bestimmten Extension für ein Protokoll, falls vorhanden."""
        for impl_ext_id, impl in self._by_protocol.get(protocol, []):
            if impl_ext_id == ext_id:
                return impl
        return None


class ActionRegistry:
    """Bestand der `ActionSpec`s für `GET /hosts/{id}/actions` und das Gate.

    Prozessweit statt je `ActionsHandle`: `propose()` braucht `command_field` und `permissions` später noch."""

    def __init__(self) -> None:
        self._specs: dict[str, tuple[str, ActionSpec]] = {}

    def register(self, ext_id: str, spec: ActionSpec) -> None:
        self._specs[spec.action_type] = (ext_id, spec)

    def clear_extension(self, ext_id: str) -> None:
        for action_type in [t for t, (eid, _) in self._specs.items() if eid == ext_id]:
            del self._specs[action_type]

    def get(self, action_type: str) -> tuple[str, ActionSpec] | None:
        return self._specs.get(action_type)

    def all_host_bound(self) -> list[ActionSpec]:
        return [spec for _, spec in self._specs.values() if spec.host_bound]


class SchedulerRegistry:
    """Bestand der Job-Handler für `core/scheduler.py`, gefunden über `(ext_id, ext_job_key)`.

    Ein Handler ist eine gebundene Methode (nicht speicherbar) und wird bei jedem Aktivieren neu eingetragen."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], Callable[..., Coroutine[Any, Any, Any]]] = {}

    def register(self, ext_id: str, job_key: str, handler: Callable[..., Coroutine[Any, Any, Any]]) -> None:
        self._handlers[(ext_id, job_key)] = handler

    def clear_extension(self, ext_id: str) -> None:
        for key in [k for k in self._handlers if k[0] == ext_id]:
            del self._handlers[key]

    def get(self, ext_id: str, job_key: str) -> Callable[..., Coroutine[Any, Any, Any]] | None:
        return self._handlers.get((ext_id, job_key))


SpawnTarget = Coroutine[Any, Any, Any] | Callable[[], Coroutine[Any, Any, Any]]


@dataclass
class SupervisedTask:
    """Hintergrundtask mit Absturzzähler; Neustart nur mit einer Fabrik (`Callable[[], Coroutine]`) als Ziel.

    Eine fertige Coroutine lässt sich nicht erneut abwarten; ihr Absturz wird nur geloggt und gezählt."""

    ext_id: str
    name: str
    target: SpawnTarget
    restart: bool
    restart_delay_s: float
    task: asyncio.Task | None = None
    crash_count: int = 0
    can_restart: bool = field(init=False)

    def __post_init__(self) -> None:
        self.can_restart = callable(self.target) and not asyncio.iscoroutine(self.target)

    def _new_coro(self) -> Coroutine[Any, Any, Any]:
        if asyncio.iscoroutine(self.target):
            return self.target
        return self.target()  # type: ignore[operator]

    async def _run(self) -> None:
        while True:
            try:
                await self._new_coro()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                self.crash_count += 1
                logger.exception(
                    "ext_task_crashed ext_id=%s name=%s crash_count=%d",
                    self.ext_id, self.name, self.crash_count,
                )
                if not self.restart or not self.can_restart:
                    return
                await asyncio.sleep(self.restart_delay_s)
                continue

    def start(self) -> None:
        self.task = asyncio.ensure_future(self._run())

    async def cancel(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - Aufräumen, nicht neu werfen
                pass


@dataclass
class LoadedExtension:
    manifest: ExtensionManifest
    instance: Any
    ctx: Any
    granted_permissions: list[str]
    router: APIRouter | None = None
    mounted_routes: list[BaseRoute] = field(default_factory=list)
    tasks: list[SupervisedTask] = field(default_factory=list)
    settings_schema: dict[str, Any] | None = None
    store_id: str | None = None
    """`id` der Registry-Zeile für Einstellungen und Zeitpläne; nach einer Umbenennung ggf. die alte Kennung."""

    def __post_init__(self) -> None:
        if not self.store_id:
            self.store_id = self.manifest.id


class ExtensionRuntime:
    def __init__(self) -> None:
        self.loaded: dict[str, LoadedExtension] = {}
        self.ui = UiRegistry()
        self.capabilities = CapabilityRegistry()
        self.actions = ActionRegistry()
        self.scheduler = SchedulerRegistry()
        self.discovered: dict[str, Any] = {}  # ext_id -> DiscoveredExtension, vom letzten Scan
        self.legacy_owner: dict[str, str] = {}
        """Alte Kennung -> Kennung der Erweiterung, die sie in `legacy_ids` nennt."""
        self.store_ids: dict[str, str] = {}
        """Kennung -> Speicher-Kennung (`id` der Registry-Zeile); ohne alte Kennungen dieselbe."""
        self.refused: dict[str, str] = {}
        """Kennung -> Grund, warum die Erweiterung wegen einer alten Kennung nicht geladen wird."""
        self.refused_old: dict[str, str] = {}
        """Alte Kennung -> Grund, warum ihre neue Version nicht geladen wird."""

    def canonical(self, ext_id: str) -> str:
        """Die heutige Kennung zu `ext_id` (eine alte wird aufgelöst, alles andere bleibt)."""
        return self.legacy_owner.get(ext_id, ext_id)

    def store_id(self, ext_id: str) -> str:
        """Die `id` der Registry-Zeile, die für `ext_id` (alte oder heutige Kennung) gilt."""
        canonical = self.canonical(ext_id)
        return self.store_ids.get(canonical, canonical)

    def is_stale_twin(self, row_id: str) -> bool:
        """Eine Zeile mit alter Kennung, während die Erweiterung eine andere nutzt (nach Rückweg und erneutem Update).

        Sie wird nie angefasst und nie geladen."""
        owner = self.legacy_owner.get(row_id)
        return owner is not None and self.store_ids.get(owner, owner) != row_id

    def legacy_ids_of(self, ext_id: str) -> list[str]:
        """Die alten Kennungen von `ext_id` in Manifest-Reihenfolge, die ihr laut letztem Scan auch gehören."""
        manifest = getattr(self.discovered.get(ext_id), "manifest", None)
        return [old for old in getattr(manifest, "legacy_ids", None) or [] if self.legacy_owner.get(old) == ext_id]

    def stage_of(self, ext_id: str) -> str:
        """Der Reifegrad (`stable` oder `beta`) von `ext_id`; ohne Angabe oder ohne Fund `stable`."""
        loaded = self.loaded.get(ext_id)
        manifest = loaded.manifest if loaded is not None else getattr(self.discovered.get(ext_id), "manifest", None)
        return getattr(manifest, "stage", None) or "stable"

    def mount_router(
        self, app: FastAPI, ext_id: str, router: APIRouter, *, deprecated: bool = False
    ) -> list[BaseRoute]:
        """Montiert den Router unter /api/v1/ext/<id>/... und gibt die neuen Routen für `unmount_router` zurück.

        `deprecated=True` markiert sie im OpenAPI-Schema als veraltet (alte Adressen einer umbenannten Erweiterung)."""
        # Ein Scratch-Router erzeugt die präfixierten Routen, ohne sie ans Ende von `app.router.routes` zu hängen.
        # FastAPI backt `dependency_overrides_provider` beim Einbinden in jede Route ein; ohne die Zuweisung
        # ignorierten Extension-Routen `app.dependency_overrides` und liefen in Tests gegen die echte DB.
        scratch = APIRouter()
        scratch.dependency_overrides_provider = app
        scratch.include_router(router, prefix=f"/api/v1/ext/{ext_id}", deprecated=deprecated)
        new_routes = list(scratch.routes)
        # Vor den StaticFiles-Catch-all auf "/", den main.py später anhängt, sonst fängt er alle Extension-Anfragen ab.
        index = getattr(app.state, "ext_mount_index", len(app.router.routes))
        app.router.routes[index:index] = new_routes
        return new_routes

    def unmount_router(self, app: FastAPI, routes: list[BaseRoute]) -> None:
        for route in routes:
            try:
                app.router.routes.remove(route)
            except ValueError:
                pass

    async def cancel_tasks(self, loaded: LoadedExtension) -> None:
        for task in loaded.tasks:
            await task.cancel()


_runtime: ExtensionRuntime | None = None


def get_extension_runtime() -> ExtensionRuntime:
    global _runtime
    if _runtime is None:
        _runtime = ExtensionRuntime()
    return _runtime


def reset_extension_runtime() -> None:
    """Nur für Tests."""
    global _runtime
    _runtime = None
