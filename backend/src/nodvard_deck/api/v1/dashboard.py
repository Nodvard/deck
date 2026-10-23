"""Dashboard-Layouts: `GET /dashboard/layouts` und `GET/PUT /dashboard/layouts/{id}`.

Das leere Standard-Layout entsteht beim ersten Abruf der Liste; einen Anlege-Endpunkt für weitere Layouts gibt es nicht.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from nodvard_sdk import WidgetGroup
from pydantic import BaseModel, Field, field_validator

from ...models import DashboardLayout
from ...services import dashboard as dashboard_service
from ..deps import CurrentUser, SessionDep

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardGroup(BaseModel):
    """Eine Gruppe der Startseite: Schlüssel wie `WidgetSpec.group`, eigener Name (leer heißt Vorgabe), zugeklappt."""

    key: WidgetGroup
    title: str | None = Field(default=None, max_length=60)
    collapsed: bool = False

    @field_validator("title")
    @classmethod
    def _blank_is_default(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


class LayoutOut(BaseModel):
    id: str
    name: str
    is_default: bool
    items: list[dict[str, Any]]
    groups: list[DashboardGroup] = Field(default_factory=list)
    """In ihrer Reihenfolge; fehlt eine Gruppe, steht sie in der Vorgabe-Reihenfolge dahinter."""
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, layout: DashboardLayout) -> "LayoutOut":
        return cls(
            id=layout.id, name=layout.name, is_default=layout.is_default, items=layout.items,
            groups=_known_groups(layout.groups), created_at=layout.created_at, updated_at=layout.updated_at,
        )


def _known_groups(raw: list[dict[str, Any]] | None) -> list[DashboardGroup]:
    # Gespeichertes von einer neueren Version mit weiteren Schlüsseln bleibt in der Datenbank, wird hier aber ausgelassen.
    groups: list[DashboardGroup] = []
    for entry in raw or []:
        try:
            groups.append(DashboardGroup.model_validate(entry))
        except ValueError:
            continue
    return groups


class LayoutPatch(BaseModel):
    name: str | None = None
    items: list[dict[str, Any]] | None = None
    groups: list[DashboardGroup] | None = None

    @field_validator("groups")
    @classmethod
    def _each_group_once(cls, value: list[DashboardGroup] | None) -> list[DashboardGroup] | None:
        if value is not None and len({g.key for g in value}) != len(value):
            raise ValueError("Jede Gruppe darf nur einmal vorkommen.")
        return value


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
    groups = None if payload.groups is None else [g.model_dump() for g in payload.groups]
    layout = await dashboard_service.update_layout(session, layout, name=payload.name, items=payload.items, groups=groups)
    return LayoutOut.from_model(layout)
