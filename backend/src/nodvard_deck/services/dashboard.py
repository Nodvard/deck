"""Dashboard-Layouts je Nutzer (Tabelle `dashboard_layouts`).

Die Zuordnung zum Nutzer (`user_id`) erzwingt schon die Abfrage, damit eine erratene `layout_id` keine fremde Zeile liefert.
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
    """Liefert das Standard-Layout und legt es an, falls der Nutzer noch keins hat (die Liste darf nie leer sein)."""
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
    session: AsyncSession, layout: DashboardLayout, *, name: str | None = None, items: list[dict[str, Any]] | None = None,
    groups: list[dict[str, Any]] | None = None,
) -> DashboardLayout:
    if name is not None:
        layout.name = name
    if items is not None:
        layout.items = items
    if groups is not None:
        layout.groups = groups
    await session.flush()
    return layout
