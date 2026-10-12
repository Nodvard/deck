"""Beispieldaten: `GET /demo` (braucht `hosts.read`), `POST /demo/seed` und `DELETE /demo` (Logik in `core/demo_seed.py`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ...core import demo_seed
from ...ext.runtime import get_extension_runtime
from ...services.auth import user_has_permission
from ...services.hosts import HostBusyError, HostServiceError
from ..deps import CurrentUser, SessionDep, require_permission

router = APIRouter(prefix="/demo", tags=["demo"])

# Beispieldaten legen Server an (`hosts.write`), schreiben Meldungen für alle Nutzer und ändern das Dashboard
# der ganzen Installation (`settings.write`).
_WriteGuards = [Depends(require_permission("hosts.write")), Depends(require_permission("settings.write"))]


class DemoStatusOut(BaseModel):
    active: bool
    """Es gibt Beispieldaten (Server oder Meldungen)."""
    hosts: int
    notifications: int
    apps: int = 0
    """Beispiel-Apps (eigene Kacheln im Cockpit)."""
    layout_replaced: bool
    """Das Dashboard-Layout wurde durch ein Beispiel-Layout ersetzt und wird beim Löschen zurückgesetzt."""
    hosts_with_access: list[str] = Field(default_factory=list)
    """Beispiel-Server, an denen inzwischen ein Zugang hängt; sie werden beim Löschen mit entfernt."""


class DemoSeedOut(DemoStatusOut):
    created: bool
    """`false`: es gab schon Beispieldaten, nichts wurde verändert."""


class DemoRemoveOut(BaseModel):
    removed_hosts: int
    kept_hosts: int
    """Beispiel-Server, die inzwischen zu echten Servern umgebaut wurden und deshalb stehen bleiben."""
    removed_notifications: int
    layout_restored: bool
    removed_apps: int = 0
    kept_apps: int = 0
    """Beispiel-Apps, die inzwischen auf eine echte Adresse zeigen und deshalb stehen bleiben."""


def _status_out(value: demo_seed.DemoStatus) -> dict:
    return {
        "active": value.active, "hosts": value.hosts, "notifications": value.notifications, "apps": value.apps,
        "layout_replaced": value.layout_replaced, "hosts_with_access": value.hosts_with_access,
    }


@router.get("", dependencies=[Depends(require_permission("hosts.read"))])
async def get_demo(session: SessionDep) -> DemoStatusOut:
    return DemoStatusOut(**_status_out(await demo_seed.demo_status(session)))


@router.post("/seed", dependencies=_WriteGuards)
async def seed_demo(session: SessionDep, user: CurrentUser) -> DemoSeedOut:
    # Dieselbe Auswahl wie `GET /widgets`.
    widgets = [
        (ext_id, spec)
        for ext_id, spec in get_extension_runtime().ui.all_widgets()
        if all(user_has_permission(user, p) for p in spec.permissions)
    ]
    try:
        current, created = await demo_seed.seed_demo_data(
            session, user_id=user.id, widgets=widgets, actor_type="user", actor_id=user.id,
        )
    except demo_seed.DemoBlockedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except HostServiceError as exc:  # zwei Aufrufe gleichzeitig: Name "demo-..." schon vergeben
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return DemoSeedOut(**_status_out(current), created=created)


@router.delete("", dependencies=_WriteGuards)
async def remove_demo(session: SessionDep, user: CurrentUser) -> DemoRemoveOut:
    try:
        removal = await demo_seed.remove_demo_data(session, actor_type="user", actor_id=user.id)
    except HostBusyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return DemoRemoveOut(
        removed_hosts=removal.removed_hosts, kept_hosts=removal.kept_hosts,
        removed_notifications=removal.removed_notifications, layout_restored=removal.layout_restored,
        removed_apps=removal.removed_apps, kept_apps=removal.kept_apps,
    )
