"""GET /widgets: Knöpfe, für die der Person ein Recht fehlt, fehlen in der Antwort."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from nodvard_sdk import (
    ActionsView,
    Column,
    ListItem,
    ListView,
    StatView,
    TableView,
    WidgetAction,
    WidgetSpec,
)


def _action(action_id: str, *permissions: str) -> WidgetAction:
    return WidgetAction(id=action_id, label=action_id, endpoint=f"x/{action_id}", permissions=list(permissions))


ACTIONS = [_action("free"), _action("manage", "soc.manage"), _action("both", "soc.read", "soc.manage")]


def _widgets() -> list[tuple[str, WidgetSpec]]:
    views = {
        "list": ListView(item=ListItem(title="{{ t }}", actions=ACTIONS)),
        "table": TableView(columns=[Column(field="t", label="T")], row_actions=ACTIONS),
        "actions": ActionsView(actions=ACTIONS),
        "stat": StatView(value="1"),
    }
    return [
        ("ext", WidgetSpec(id=kind, title=kind, data_endpoint="x", view=view, permissions=["soc.read"]))
        for kind, view in views.items()
    ]


@pytest.fixture
def catalog(monkeypatch):
    from nodvard_deck.api.v1 import catalog

    runtime = SimpleNamespace(
        ui=SimpleNamespace(all_widgets=_widgets), legacy_ids_of=lambda ext_id: [], category_of=lambda ext_id: None,
    )
    monkeypatch.setattr(catalog, "get_extension_runtime", lambda: runtime)
    monkeypatch.setattr(catalog, "user_has_permission", lambda user, p: p in user.perms)
    return catalog


def _action_ids(widget) -> list[str]:
    view = widget.view
    actions = {"list": lambda: view.item.actions, "table": lambda: view.row_actions, "actions": lambda: view.actions}
    return [a.id for a in actions[view.kind]()]


@pytest.mark.asyncio
async def test_without_the_right_of_a_button_it_is_left_out(catalog):
    out = {w.id: w for w in await catalog.list_widgets(SimpleNamespace(perms={"soc.read"}))}
    assert set(out) == {"list", "table", "actions", "stat"}
    for kind in ("list", "table", "actions"):
        assert _action_ids(out[kind]) == ["free"], kind


@pytest.mark.asyncio
async def test_with_every_right_all_buttons_stay(catalog):
    out = {w.id: w for w in await catalog.list_widgets(SimpleNamespace(perms={"soc.read", "soc.manage"}))}
    for kind in ("list", "table", "actions"):
        assert _action_ids(out[kind]) == ["free", "manage", "both"], kind


@pytest.mark.asyncio
async def test_a_widget_without_its_own_right_stays_hidden(catalog):
    assert await catalog.list_widgets(SimpleNamespace(perms={"soc.manage"})) == []

