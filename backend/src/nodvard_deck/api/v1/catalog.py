"""Kataloge für Navigation (`/pages`) und Dashboard-Widgets (`/widgets`), gelesen aus `ExtensionRuntime.ui`.

Der Bestand liegt im Prozessspeicher: Aktivieren oder Deaktivieren einer Erweiterung wirkt sofort, ohne Neustart.
"""

from __future__ import annotations

from nodvard_sdk import PageSpec, WidgetGroup, WidgetSpec
from pydantic import Field

from ...ext.runtime import get_extension_runtime
from ...services.auth import user_has_permission
from ..deps import CurrentUser

from fastapi import APIRouter

router = APIRouter(tags=["catalog"])


def _visible(user, permissions: list[str]) -> bool:
    return all(user_has_permission(user, p) for p in permissions)


class PageOut(PageSpec):
    ext_id: str
    legacy_ext_ids: list[str] = Field(default_factory=list)
    """Alte Kennungen der Erweiterung (`legacy_ids` im Manifest), z. B. um gespeicherte Links mit
    `/ext/<alt>/...` auf `/ext/<ext_id>/...` umzuleiten. Ohne Umbenennung leer."""
    stage: str = "stable"
    """Reifegrad der Erweiterung (`stable` oder `beta`, siehe `ExtensionManifest.stage`): Menü und Seitenkopf
    zeigen bei `beta` ein Abzeichen. Fehlt die Angabe im Manifest, steht hier `stable`."""


class WidgetOut(WidgetSpec):
    ext_id: str
    legacy_ext_ids: list[str] = Field(default_factory=list)
    """Alte Kennungen der Erweiterung (`legacy_ids` im Manifest), z. B. um gespeicherte Dashboard-
    Einträge mit alter `ext_id` diesem Widget zuzuordnen. Ohne Umbenennung leer."""


# Ohne `WidgetSpec.group` entscheidet die Kategorie der Erweiterung; der Kern kennt nur die Gruppen, keine Module.
_GROUP_BY_CATEGORY: dict[str, WidgetGroup] = {"security": "security", "servers": "servers"}


def _group_of(spec: WidgetSpec, category: str | None) -> WidgetGroup:
    return spec.group or _GROUP_BY_CATEGORY.get(category or "", "services")


def _view_for(user, view: dict) -> dict:
    """Die Ansicht ohne Knöpfe, für die der Person ein Recht fehlt (ein Klick darauf endete mit 403)."""
    def allowed(actions: list[dict]) -> list[dict]:
        return [action for action in actions if _visible(user, action["permissions"])]

    view = {**view, **{key: allowed(view[key]) for key in ("actions", "row_actions") if key in view}}
    if isinstance(view.get("item"), dict):
        view["item"] = {**view["item"], "actions": allowed(view["item"]["actions"])}
    return view


@router.get("/pages")
async def list_pages(user: CurrentUser) -> list[PageOut]:
    runtime = get_extension_runtime()
    return [
        PageOut(ext_id=ext_id, legacy_ext_ids=runtime.legacy_ids_of(ext_id), stage=runtime.stage_of(ext_id), **spec.model_dump())
        for ext_id, spec in runtime.ui.all_pages()
        if spec.show_in_nav and _visible(user, spec.permissions)
    ]


@router.get("/widgets")
async def list_widgets(user: CurrentUser) -> list[WidgetOut]:
    runtime = get_extension_runtime()
    widgets = []
    for ext_id, spec in runtime.ui.all_widgets():
        if not _visible(user, spec.permissions):
            continue
        data = spec.model_dump()
        widgets.append(WidgetOut(
            ext_id=ext_id, legacy_ext_ids=runtime.legacy_ids_of(ext_id),
            **{**data, "group": _group_of(spec, runtime.category_of(ext_id)), "view": _view_for(user, data["view"])},
        ))
    return widgets
