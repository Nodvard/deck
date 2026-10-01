"""Prozessspeicher-Zustand des Extension-Hosts: UI-Katalog, Capabilities, geladene
Extensions, Router-Montage, ueberwachte Hintergrundtasks.

Analog zu `core/events.py`: ein Singleton mit `get_extension_runtime()`/
`reset_extension_runtime()` fuers Testen. Die Registry-TABELLE (`ExtensionRecord`,
dauerhaft, in der DB) und dieser RUNTIME-Zustand (fluechtig, Prozessspeicher) sind
bewusst getrennt: die DB sagt, was ENABLED sein SOLL, die Runtime, was gerade
tatsaechlich laeuft. `main.py`s Lifespan bringt beide beim Boot in Deckung.
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


# ---------------------------------------------------------------------------
# UI-Katalog -- Seiten, Widgets, Navigation je Extension
# ---------------------------------------------------------------------------


@dataclass
class NavEntry:
    ext_id: str
    section: str | None
    order: int
    page: PageSpec


class UiRegistry:
    """Nur Bestand, keine Logik. `clear_extension()` ist der Grund, warum Deaktivieren
    ohne Neustart funktioniert: der Katalog ist reiner Prozessspeicher, kein
    FastAPI-Zustand, der erst beim naechsten Boot neu aufgebaut wuerde."""

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
        # Gleiche ID ersetzt -- so kann eine Extension ihr Werkzeug nach einer
        # Einstellungs-Aenderung (on_settings_changed) mit neuen Bedingungen melden.
        tools = [t for t in self._host_tools.get(ext_id, []) if t.id != spec.id]
        tools.append(spec)
        self._host_tools[ext_id] = tools

    def register_host_requirement(self, ext_id: str, spec: HostRequirementSpec) -> None:
        # Wie bei den Werkzeugen: gleiche ID ersetzt (Meldung nach Einstellungs-Aenderung).
        specs = [r for r in self._host_requirements.get(ext_id, []) if r.id != spec.id]
        specs.append(spec)
        self._host_requirements[ext_id] = specs

    def clear_extension(self, ext_id: str) -> None:
        self._pages.pop(ext_id, None)
        self._widgets.pop(ext_id, None)
        self._host_tools.pop(ext_id, None)
        # Eine deaktivierte Extension darf nichts mehr verlangen (Einrichtungsbefehl, Pruefung).
        self._host_requirements.pop(ext_id, None)

    def all_pages(self) -> list[tuple[str, PageSpec]]:
        return [(ext_id, p) for ext_id, specs in self._pages.items() for p in specs]

    def all_widgets(self) -> list[tuple[str, WidgetSpec]]:
        return [(ext_id, w) for ext_id, specs in self._widgets.items() for w in specs]

    def all_host_tools(self) -> list[tuple[str, HostToolSpec]]:
        return [(ext_id, t) for ext_id, specs in self._host_tools.items() for t in specs]

    def all_host_requirements(self) -> list[tuple[str, HostRequirementSpec]]:
        return [(ext_id, r) for ext_id, specs in self._host_requirements.items() for r in specs]


# ---------------------------------------------------------------------------
# Capabilities -- wer erfuellt welches Protokoll
# ---------------------------------------------------------------------------


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

    def provided_by(self, protocol: type, ext_id: str) -> Any | None:
        """Die Implementierung EINER bestimmten Extension fuer ein Protokoll, falls
        vorhanden -- gebraucht, um z. B. den `HostProvider` aufzuloesen, auf den
        `Host.provider_ext_id` verweist (docs/04-API.md: `GET /hosts/{id}/actions`),
        ohne die generische `query()` erst nach `allowed_ext_ids` filtern zu muessen."""
        for impl_ext_id, impl in self._by_protocol.get(protocol, []):
            if impl_ext_id == ext_id:
                return impl
        return None


# ---------------------------------------------------------------------------
# Aktions-Katalog -- welche ActionSpecs welche Extension angemeldet hat
# ---------------------------------------------------------------------------


class ActionRegistry:
    """Bestand fuer `GET /hosts/{id}/actions` (docs/04-API.md §3) und fuer das Gate
    (docs/00 D-05, WP-5): `command_field`/`permissions` einer `ActionSpec` werden bei
    `ctx.actions.propose()` gebraucht, lange nachdem die Extension, die sie registriert
    hat, die einzelne Anfrage laengst vergessen hat -- deshalb Prozess-weiter Bestand
    hier, analog zu `UiRegistry`, nicht ein privates Dict je `ActionsHandle`-Instanz."""

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


# ---------------------------------------------------------------------------
# Job-Handler -- welche Extension welchen Job-Schluessel wie ausfuehrt
# ---------------------------------------------------------------------------


class SchedulerRegistry:
    """Bestand fuer `core/scheduler.py`: eine `JobSpec.handler` ist eine gebundene
    Methode einer Extension-Instanz -- nicht in der DB speicherbar (`jobs` haelt nur
    Zeitplan-Metadaten, docs/03 §7) und nicht ueber einen Prozessneustart hinweg
    gueltig. Bei jedem `register_job()`-Aufruf (also bei jedem Extension-Enable) wird
    der Handler hier frisch eingetragen -- der Scheduler fasst beim Ausloesen eines
    faelligen Jobs nur ueber `(ext_id, ext_job_key)` dahin, niemals ueber eine
    gespeicherte Referenz."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], Callable[..., Coroutine[Any, Any, Any]]] = {}

    def register(self, ext_id: str, job_key: str, handler: Callable[..., Coroutine[Any, Any, Any]]) -> None:
        self._handlers[(ext_id, job_key)] = handler

    def clear_extension(self, ext_id: str) -> None:
        for key in [k for k in self._handlers if k[0] == ext_id]:
            del self._handlers[key]

    def get(self, ext_id: str, job_key: str) -> Callable[..., Coroutine[Any, Any, Any]] | None:
        return self._handlers.get((ext_id, job_key))


# ---------------------------------------------------------------------------
# Ueberwachte Hintergrundtasks (ctx.spawn)
# ---------------------------------------------------------------------------


SpawnTarget = Coroutine[Any, Any, Any] | Callable[[], Coroutine[Any, Any, Any]]


@dataclass
class SupervisedTask:
    """**Dokumentierte Abweichung vom SDK-Protokoll:** `ExtensionContext.spawn()`
    nimmt laut Vertrag eine `Coroutine` entgegen -- ein bereits erzeugtes
    Coroutine-Objekt ist aber nach einem Absturz nicht wiederverwendbar (Python wirft
    `RuntimeError: cannot reuse already awaited coroutine`). Ein echter Neustart
    braucht eine FABRIK (`Callable[[], Coroutine]`), keine fertige Coroutine.
    `@runtime_checkable` prueft nur Attributnamen, keine Signaturen, deshalb ist es
    protokollkonform, hier zusaetzlich eine Fabrik zu akzeptieren. Wird eine blosse
    Coroutine uebergeben (der dokumentierte Fall), laeuft sie einmal ueberacht; ein
    Absturz wird geloggt und gezaehlt, ein echter Neustart ist dann NICHT moeglich --
    das ist eine Grenze des SDK-Vertrags, nicht dieser Implementierung (siehe
    Abnahmebericht, Abschnitt "ehrlich offen gelassen")."""

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
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - Aufraeumen, nicht neu werfen
                pass


# ---------------------------------------------------------------------------
# Eine geladene Extension
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Der Runtime-Zustand insgesamt
# ---------------------------------------------------------------------------


class ExtensionRuntime:
    def __init__(self) -> None:
        self.loaded: dict[str, LoadedExtension] = {}
        self.ui = UiRegistry()
        self.capabilities = CapabilityRegistry()
        self.actions = ActionRegistry()
        self.scheduler = SchedulerRegistry()
        self.discovered: dict[str, Any] = {}  # ext_id -> DiscoveredExtension, vom letzten Scan

    def mount_router(self, app: FastAPI, ext_id: str, router: APIRouter) -> list[BaseRoute]:
        """Montiert unter /api/v1/ext/<id>/... und merkt sich die HINZUGEFUEGTEN
        Route-Objekte, damit `unmount_router` exakt sie wieder entfernen kann --
        `app.include_router()` selbst kennt kein Gegenstueck zum Entfernen.

        Fuegt bewusst an `app.state.ext_mount_index` EIN, nicht ans Ende von
        `app.router.routes`: main.py mountet dort spaeter (nur wenn `frontend/dist`
        existiert) einen StaticFiles-Catch-all auf "/" -- staende der davor, wuerde er
        JEDE `/api/v1/ext/<id>/...`-Anfrage abfangen, bevor eine erst danach
        hinzugefuegte Extension-Route je geprueft wird. Ein Scratch-Router (nie an
        `app` gehaengt) erzeugt die praefixierten Route-Objekte, ohne sie selbst ans
        Ende von `app.router.routes` zu haengen.

        **Live gefunden beim Bau von WP-6s `permission=`-Parameter, nicht vorher
        bedacht:** ein frisch konstruierter `APIRouter()` hat
        `dependency_overrides_provider=None`. FastAPI backt diesen Wert beim
        `include_router()`-Aufruf UNVERAENDERLICH in jede daraus entstehende Route ein
        (`_RouterIncludeContext.for_include`) -- ohne das Setzen unten haette JEDE
        ueber eine Extension gemountete Route, die `Depends(get_settings)`/
        `Depends(get_session)` (direkt oder ueber `CurrentUser`/`require_permission`)
        nutzt, `app.dependency_overrides` STILLSCHWEIGEND ignoriert und wäre in Tests
        (und ueberall sonst, wo Overrides greifen sollen) gegen die echten,
        ungecachten Projekt-Settings/-DB gelaufen -- exakt derselbe Fallstrick-Familie
        wie der wiederholt gefundene `get_settings()`-Singleton-Bug, nur eine Ebene
        tiefer und nie zuvor bemerkt, weil keine Extension-Route vor WP-6 je
        `Depends()` fuer etwas Ueberschreibbares benutzt hat."""
        scratch = APIRouter()
        scratch.dependency_overrides_provider = app
        scratch.include_router(router, prefix=f"/api/v1/ext/{ext_id}")
        new_routes = list(scratch.routes)
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
    """Nur fuer Tests."""
    global _runtime
    _runtime = None
