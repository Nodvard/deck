"""UI-Katalog, Capabilities, Router-Montage, ueberwachte Hintergrundtasks."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import APIRouter, FastAPI
from nodvard_sdk import GridSize, ListItem, ListView, PageSpec, Refresh, WidgetSpec
from nodvard_sdk.actions import ActionSpec

from nodvard_deck.ext.runtime import ActionRegistry, CapabilityRegistry, ExtensionRuntime, SupervisedTask, UiRegistry


def _page(id_: str) -> PageSpec:
    return PageSpec(id=id_, path=f"/{id_}", title=id_, component="X")


def _widget(id_: str) -> WidgetSpec:
    return WidgetSpec(
        id=id_, title=id_, data_endpoint="w", view=ListView(item=ListItem(title="{{ t }}"))
    )


def test_ui_registry_tracks_per_extension_and_clears():
    ui = UiRegistry()
    ui.register_page("ext-a", _page("a1"))
    ui.register_widget("ext-a", _widget("w1"))
    ui.register_page("ext-b", _page("b1"))

    assert {p.id for _, p in ui.all_pages()} == {"a1", "b1"}
    assert {w.id for _, w in ui.all_widgets()} == {"w1"}

    ui.clear_extension("ext-a")
    assert {p.id for _, p in ui.all_pages()} == {"b1"}
    assert ui.all_widgets() == []


class _ProtoA:
    pass


class _ProtoB:
    pass


def test_capability_registry_query_and_clear():
    reg = CapabilityRegistry()
    impl_a = object()
    impl_b = object()
    reg.provide("ext-a", _ProtoA, impl_a)
    reg.provide("ext-b", _ProtoA, impl_b)

    all_impls = reg.query(_ProtoA)
    assert set(all_impls) == {impl_a, impl_b}
    assert reg.query(_ProtoB) == []

    reg.clear_extension("ext-a")
    remaining = reg.query(_ProtoA)
    assert remaining == [impl_b]


def test_capability_registry_query_restricted_to_allowed_ext_ids():
    reg = CapabilityRegistry()
    impl_a = object()
    impl_b = object()
    reg.provide("ext-a", _ProtoA, impl_a)
    reg.provide("ext-b", _ProtoA, impl_b)

    assert reg.query(_ProtoA, allowed_ext_ids={"ext-a"}) == [impl_a]
    assert reg.query(_ProtoA, allowed_ext_ids=set()) == []


def test_capability_registry_provided_by_finds_specific_extension():
    reg = CapabilityRegistry()
    impl_a = object()
    impl_b = object()
    reg.provide("ext-a", _ProtoA, impl_a)
    reg.provide("ext-b", _ProtoA, impl_b)

    assert reg.provided_by(_ProtoA, "ext-b") is impl_b
    assert reg.provided_by(_ProtoA, "unknown-ext") is None
    assert reg.provided_by(_ProtoB, "ext-a") is None


def _spec(action_type: str, *, host_bound: bool = True) -> ActionSpec:
    return ActionSpec(action_type=action_type, label=action_type, host_bound=host_bound)


def test_action_registry_register_list_and_clear():
    reg = ActionRegistry()
    reg.register("ext-a", _spec("shell.exec"))
    reg.register("ext-a", _spec("host.only", host_bound=False))
    reg.register("ext-b", _spec("vm.start"))

    assert {s.action_type for s in reg.all_host_bound()} == {"shell.exec", "vm.start"}

    found = reg.get("shell.exec")
    assert found is not None
    assert found[0] == "ext-a"
    assert found[1].action_type == "shell.exec"

    reg.clear_extension("ext-a")
    assert reg.get("shell.exec") is None
    assert {s.action_type for s in reg.all_host_bound()} == {"vm.start"}


def test_action_registry_re_register_same_action_type_overwrites():
    reg = ActionRegistry()
    reg.register("ext-a", _spec("shell.exec"))
    reg.register("ext-a", _spec("shell.exec"))
    assert len(reg.all_host_bound()) == 1


@pytest.mark.asyncio
async def test_supervised_task_runs_plain_coroutine_once():
    ran = []

    async def _work() -> None:
        ran.append(1)

    task = SupervisedTask(ext_id="e", name="t", target=_work(), restart=True, restart_delay_s=0.01)
    task.start()
    await task.task
    assert ran == [1]


@pytest.mark.asyncio
async def test_supervised_task_with_factory_restarts_after_crash():
    attempts = []

    def factory():
        async def _run():
            attempts.append(len(attempts))
            if len(attempts) < 2:
                raise RuntimeError("erster Versuch scheitert absichtlich")
        return _run()

    task = SupervisedTask(ext_id="e", name="t", target=factory, restart=True, restart_delay_s=0.01)
    assert task.can_restart is True
    task.start()
    await asyncio.wait_for(task.task, timeout=2)
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_supervised_task_plain_coroutine_cannot_restart_after_crash():
    """Dokumentierte Grenze des SDK-Vertrags (siehe SupervisedTask-Docstring): eine
    blosse Coroutine ist nach einem Absturz nicht wiederverwendbar."""

    async def _fails() -> None:
        raise RuntimeError("stuerzt ab")

    task = SupervisedTask(ext_id="e", name="t", target=_fails(), restart=True, restart_delay_s=0.01)
    assert task.can_restart is False
    task.start()
    await task.task
    assert task.crash_count == 1


@pytest.mark.asyncio
async def test_supervised_task_cancel_stops_a_running_loop():
    ticks = []

    async def factory():
        while True:
            ticks.append(1)
            await asyncio.sleep(0.01)

    task = SupervisedTask(ext_id="e", name="t", target=lambda: factory(), restart=True, restart_delay_s=0.01)
    task.start()
    await asyncio.sleep(0.03)
    await task.cancel()
    count_after_cancel = len(ticks)
    await asyncio.sleep(0.03)
    assert len(ticks) == count_after_cancel, "nach cancel() darf der Task nicht weiterlaufen"


def test_mount_router_inserts_before_catch_all_and_unmount_removes_exactly_those_routes():
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)

    # Simuliert main.py: der StaticFiles-Catch-all wird NACH dem Merkpunkt montiert.
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    catch_all = Route("/{path:path}", lambda r: PlainTextResponse("catch-all"))
    app.router.routes.append(catch_all)

    runtime = ExtensionRuntime()
    ext_router = APIRouter()

    @ext_router.get("/ping")
    async def ping():
        return {"ok": True}

    added = runtime.mount_router(app, "hello-world", ext_router)
    assert len(added) == 1

    index_of_added = app.router.routes.index(added[0])
    index_of_catch_all = app.router.routes.index(catch_all)
    assert index_of_added < index_of_catch_all, (
        "Extension-Routen muessen vor dem Catch-all stehen, sonst verschluckt er sie"
    )

    runtime.unmount_router(app, added)
    assert added[0] not in app.router.routes
    assert catch_all in app.router.routes
