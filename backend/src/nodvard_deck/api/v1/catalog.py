"""Deklarative Kataloge fuer Navigation und Dashboard-Widgets --
docs/02-EXTENSION-API.md §4/§5, docs/04-API.md §3.

Beide Kataloge sind reine Sicht auf `ExtensionRuntime.ui` (Prozessspeicher, siehe
ext/runtime.py) -- Aktivieren/Deaktivieren einer Extension aendert sie sofort, ohne
Neustart, weil hier nichts anderes als dieser In-Memory-Bestand abgefragt wird
(die WP-3-Abnahme).

**Gap-Fill, hier dokumentiert:** docs/04-API.md listet `GET /widgets`, aber keinen
eigenen `GET /pages`-Endpunkt fuer die Navigation -- die Abnahme
verlangt jedoch ausdruecklich, dass sich "Navigation" beobachtbar aendert. `PageSpec`
traegt bereits alle dafuer noetigen Felder (`nav_section`, `nav_order`,
`show_in_nav`); ein eigener `/pages`-Katalog als Geschwister von `/widgets` ist die
naheliegende, minimal-invasive Erweiterung statt eines neu erfundenen `/nav`-Konzepts.
"""

from __future__ import annotations

from nodvard_sdk import PageSpec, WidgetSpec
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
    """Fruehere Kennungen der Erweiterung (`legacy_ids` im Manifest), z. B. um gespeicherte Links mit
    `/ext/<alt>/...` auf `/ext/<ext_id>/...` umzuleiten. Ohne Umbenennung leer."""


class WidgetOut(WidgetSpec):
    ext_id: str
    legacy_ext_ids: list[str] = Field(default_factory=list)
    """Fruehere Kennungen der Erweiterung (`legacy_ids` im Manifest), z. B. um gespeicherte Dashboard-
    Eintraege mit alter `ext_id` diesem Widget zuzuordnen. Ohne Umbenennung leer."""


@router.get("/pages")
async def list_pages(user: CurrentUser) -> list[PageOut]:
    runtime = get_extension_runtime()
    return [
        PageOut(ext_id=ext_id, legacy_ext_ids=runtime.legacy_ids_of(ext_id), **spec.model_dump())
        for ext_id, spec in runtime.ui.all_pages()
        if spec.show_in_nav and _visible(user, spec.permissions)
    ]


@router.get("/widgets")
async def list_widgets(user: CurrentUser) -> list[WidgetOut]:
    runtime = get_extension_runtime()
    return [
        WidgetOut(ext_id=ext_id, legacy_ext_ids=runtime.legacy_ids_of(ext_id), **spec.model_dump())
        for ext_id, spec in runtime.ui.all_widgets()
        if _visible(user, spec.permissions)
    ]
