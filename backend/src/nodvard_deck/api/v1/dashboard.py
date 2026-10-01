"""Dashboard-Layouts -- docs/04-API.md (`GET /dashboard/layouts`,
`GET/PUT /dashboard/layouts/{id}`).

**Bewusst NICHT Teil dieser Runde:** ein `POST /dashboard/layouts` fuer zusaetzliche,
benannte Layouts -- docs/04 zeigt keinen eigenen Anlege-Endpunkt, nur Lesen/Aendern
eines (impliziten) Standard-Layouts pro Nutzer. `GET /dashboard/layouts` legt beim
allerersten Aufruf automatisch ein leeres Standard-Layout an (`services.dashboard.
get_or_create_default()`), damit ein frischer Nutzer sofort etwas Bearbeitbares hat.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from ...models import DashboardLayout
from ...services import dashboard as dashboard_service
from ..deps import CurrentUser, SessionDep

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class LayoutOut(BaseModel):
    id: str
    name: str
    is_default: bool
    items: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, layout: DashboardLayout) -> "LayoutOut":
        return cls(
            id=layout.id, name=layout.name, is_default=layout.is_default, items=layout.items,
            created_at=layout.created_at, updated_at=layout.updated_at,
        )


class LayoutPatch(BaseModel):
    name: str | None = None
    items: list[dict[str, Any]] | None = None


@router.get("/layouts")
async def list_layouts(session: SessionDep, user: CurrentUser) -> list[LayoutOut]:
    rows = await dashboard_service.list_layouts(session, user_id=user.id)
    if not rows:
        rows = [await dashboard_service.get_or_create_default(session, user_id=user.id)]
    return [LayoutOut.from_model(r) for r in rows]


@router.get("/layouts/{layout_id}")
async def get_layout(layout_id: str, session: SessionDep, user: CurrentUser) -> LayoutOut:
    layout = await dashboard_service.get_layout(session, layout_id, user_id=user.id)
    if layout is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes Layout.")
    return LayoutOut.from_model(layout)


@router.put("/layouts/{layout_id}")
async def put_layout(layout_id: str, payload: LayoutPatch, session: SessionDep, user: CurrentUser) -> LayoutOut:
    layout = await dashboard_service.get_layout(session, layout_id, user_id=user.id)
    if layout is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekanntes Layout.")
    layout = await dashboard_service.update_layout(session, layout, name=payload.name, items=payload.items)
    return LayoutOut.from_model(layout)
