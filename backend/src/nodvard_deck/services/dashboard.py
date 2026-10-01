"""Dashboard-Layouts -- docs/03-DATA-MODEL.md §9 (`dashboard_layouts`).

Layouts sind PERSOENLICH (`user_id`) -- kein RBAC-Permission-Modell noetig ausser
"eingeloggt": jeder Nutzer sieht/aendert nur seine EIGENEN Layouts, erzwungen auf der
Query-Ebene hier (nicht nur als UI-Konvention), damit ein erratener/geteilter
`layout_id`-Wert keinem anderen Nutzer je eine fremde Zeile zeigt.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DashboardLayout

DEFAULT_LAYOUT_NAME = "Standard"


async def list_layouts(session: AsyncSession, *, user_id: str) -> list[DashboardLayout]:
    result = await session.execute(
        select(DashboardLayout).where(DashboardLayout.user_id == user_id).order_by(DashboardLayout.created_at)
    )
    return list(result.scalars().all())


async def get_or_create_default(session: AsyncSession, *, user_id: str) -> DashboardLayout:
    """`GET /dashboard/layouts` darf nie leer sein -- ein frisch angelegter Nutzer
    braucht sofort EIN bearbeitbares Layout, statt erst einen Anlege-Schritt zu
    verlangen, den docs/04 gar nicht als eigenen Endpunkt vorsieht."""
    result = await session.execute(
        select(DashboardLayout).where(DashboardLayout.user_id == user_id, DashboardLayout.is_default.is_(True))
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = DashboardLayout(user_id=user_id, name=DEFAULT_LAYOUT_NAME, is_default=True, items=[])
    session.add(row)
    await session.flush()
    return row


async def get_layout(session: AsyncSession, layout_id: str, *, user_id: str) -> DashboardLayout | None:
    row = await session.get(DashboardLayout, layout_id)
    if row is None or row.user_id != user_id:
        return None
    return row


async def update_layout(
    session: AsyncSession, layout: DashboardLayout, *, name: str | None = None, items: list[dict[str, Any]] | None = None
) -> DashboardLayout:
    if name is not None:
        layout.name = name
    if items is not None:
        layout.items = items
    await session.flush()
    return layout
