"""GET /widgets: jedes Widget trägt seine Gruppe, aus `WidgetSpec.group` oder der Kategorie der Erweiterung."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from nodvard_sdk import StatView, WidgetSpec


def _spec(widget_id: str, group=None) -> WidgetSpec:
    return WidgetSpec(id=widget_id, title=widget_id, data_endpoint="x", view=StatView(value="1"), group=group)


@pytest.mark.asyncio
async def test_widget_group_comes_from_the_spec_or_the_category(monkeypatch):
    from nodvard_deck.api.v1 import catalog

    widgets = [
        ("a", _spec("own", "apps")),
        ("b", _spec("by-category")),
        ("c", _spec("fallback")),
        ("d", _spec("no-category")),
    ]
    categories = {"a": "servers", "b": "security", "c": "tools", "d": None}
    runtime = SimpleNamespace(
        ui=SimpleNamespace(all_widgets=lambda: widgets),
        legacy_ids_of=lambda ext_id: [],
        category_of=categories.get,
    )
    monkeypatch.setattr(catalog, "get_extension_runtime", lambda: runtime)

    out = await catalog.list_widgets(SimpleNamespace())
    assert {w.id: w.group for w in out} == {
        "own": "apps", "by-category": "security", "fallback": "services", "no-category": "services",
    }


def test_an_unknown_group_is_refused_by_the_sdk():
    with pytest.raises(ValueError):
        _spec("x", "garage")
