"""Cockpit-Uebersicht: EINE Abfrage fuer die Startseite -- wie
viele Dienste laufen, wie stehen die Backups, was wartet auf Freigabe, was ist an
Warnungen ungelesen.

Bleibt herstellerneutral wie der ganze Kern (docs/01 §1): Dienste und Backups kommen
ausschliesslich ueber die SDK-Capabilities `ServiceCatalog` und `BackupProvider` --
welche Extension sie liefert (heute service-matrix bzw. backups), weiss dieser Code
nicht. Eine Extension, die haengt oder wirft, verdeckt die anderen nicht: je Quelle
ein Zeitlimit, Fehler werden als `errors` gemeldet statt die ganze Seite zu leeren.

Dienste/Backups sind teuer (SSH `docker ps`, Proxmox-Task-Index) -- deshalb 30 s im
Prozess zwischengespeichert, mit Sperre gegen gleichzeitiges Neuladen. Die Zaehler aus
der eigenen Datenbank (Freigaben, Meldungen) sind billig und immer frisch.

**Apps ("+ App hinzufuegen"):** `apps` fuehrt die erkannten Dienste und die von Hand angelegten
Kacheln (`custom_apps`) in EINER Liste zusammen, jede mit `source` = `detected` oder `custom`. Die
eigenen stehen zuerst (nach ihrer Position), danach die erkannten in der Reihenfolge von `services`.
Rein additiv: `services` und `services_running` bleiben, wie sie waren -- nur erkannte Dienste, die
Dienst-Kennzahl zaehlt keine Links --, damit aeltere Clients nichts Neues falsch deuten. Die eigenen
Kacheln sind billig und kommen immer frisch aus der Datenbank, nicht aus dem Zwischenspeicher: wer eine
anlegt, sieht sie sofort. Der Server fragt ihre Adressen nie selbst ab.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from nodvard_sdk import BackupProvider, ServiceCatalog
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...ext.runtime import get_extension_runtime
from ...models import Action, Notification
from ...services import custom_apps as custom_apps_service
from ..deps import SessionDep, require_permission

router = APIRouter(prefix="/overview", tags=["overview"])

CACHE_TTL_S = 30.0
SOURCE_TIMEOUT_S = 12.0

_cache: dict[str, Any] = {"at": 0.0, "value": None}
_lock = asyncio.Lock()


class ServiceOut(BaseModel):
    id: str
    name: str
    host: str | None = None
    host_id: str | None = None
    state: str | None = None
    tone: str | None = None
    url: str | None = None
    image: str | None = None


class AppTileOut(BaseModel):
    """Eine Kachel im Bereich "Apps": ein erkannter Dienst (`source="detected"`) oder eine eigene App
    (`source="custom"`). Felder, die es fuer die andere Art nicht gibt, sind `null`."""

    id: str
    """Bei erkannten Diensten wie `ServiceOut.id`; bei eigenen die ID aus `/apps` (fuer Aendern/Loeschen)."""
    source: str
    name: str
    url: str | None = None
    host: str | None = None
    host_id: str | None = None
    state: str | None = None
    tone: str | None = None
    image: str | None = None
    icon: str | None = None
    color: str | None = None
    group: str | None = None
    open_in_new_tab: bool = True
    sort_order: int | None = None


class BackupSummary(BaseModel):
    total: int = 0
    ok: int = 0
    failed: int = 0
    running: int = 0
    unknown: int = 0
    failed_names: list[str] = []
    # Verbindungen, die nicht antworten (z. B. Proxmox-Knoten aus): zaehlen NICHT als Job,
    # sonst waeren sie ein "unbekannter" Lauf -- sie sind eine Warnung fuer sich.
    unreachable_names: list[str] = []


class AttentionItem(BaseModel):
    id: str
    ts: datetime
    severity: str
    title: str
    source_ext_id: str | None


class OverviewOut(BaseModel):
    services: list[ServiceOut]
    services_running: int
    apps: list[AppTileOut] = []
    """Eigene Apps (zuerst) und erkannte Dienste (danach), siehe Modul-Docstring."""
    backups: BackupSummary | None
    pending_actions: int
    unread_notifications: int
    attention: list[AttentionItem]
    errors: list[str]
    generated_at: float


async def _collect_services(errors: list[str]) -> list[ServiceOut]:
    services: list[ServiceOut] = []
    for provider in get_extension_runtime().capabilities.query(ServiceCatalog):
        try:
            rows = await asyncio.wait_for(provider.list_services(), timeout=SOURCE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - eine Quelle darf die anderen nicht verdecken
            errors.append(f"Dienste: {str(exc) or type(exc).__name__}")
            continue
        for row in rows:
            if not row.get("name"):
                continue  # Fehlerkacheln einer Extension ("Host nicht erreichbar") sind keine Dienste
            services.append(ServiceOut(
                id=str(row.get("id") or row["name"]), name=str(row["name"]), host=row.get("host"),
                host_id=row.get("host_id"), state=row.get("state"), tone=row.get("tone"),
                url=row.get("url"), image=row.get("image"),
            ))
    services.sort(key=lambda s: (s.state != "running", (s.name or "").lower()))
    return services


async def _app_tiles(session: AsyncSession, services: list[ServiceOut]) -> list[AppTileOut]:
    custom = await custom_apps_service.list_apps(session)
    names = await custom_apps_service.host_names(session, (a.host_id for a in custom))
    tiles = [
        AppTileOut(
            id=a.id, source="custom", name=a.name, url=custom_apps_service.stored_url(a.url),
            host=names.get(a.host_id) if a.host_id else None, host_id=a.host_id if a.host_id in names else None,
            icon=a.icon, color=a.color, group=a.group_name, open_in_new_tab=a.open_in_new_tab, sort_order=a.sort_order,
        )
        for a in custom
    ]
    tiles.extend(
        AppTileOut(
            id=s.id, source="detected", name=s.name, url=s.url, host=s.host, host_id=s.host_id, state=s.state,
            tone=s.tone, image=s.image,
        )
        for s in services
    )
    return tiles


async def _collect_backups(errors: list[str]) -> BackupSummary | None:
    providers = get_extension_runtime().capabilities.query(BackupProvider)
    if not providers:
        return None
    summary = BackupSummary()
    for provider in providers:
        try:
            jobs = await asyncio.wait_for(provider.list_jobs(), timeout=SOURCE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Backups: {str(exc) or type(exc).__name__}")
            continue
        for job in jobs:
            status = str(job.get("last_status") or "unknown")
            if status == "unreachable":
                # Platzhalterzeile einer toten Verbindung (BackupProvider.list_jobs()), kein Job.
                summary.unreachable_names.append(str(job.get("connection") or job.get("name") or "?"))
                continue
            summary.total += 1
            if status == "ok":
                summary.ok += 1
            elif status == "failed":
                summary.failed += 1
                summary.failed_names.append(str(job.get("name") or job.get("vmid") or "?"))
            elif status == "running":
                summary.running += 1
            else:
                summary.unknown += 1
    return summary


async def _expensive_part() -> dict[str, Any]:
    async with _lock:
        if _cache["value"] is not None and time.monotonic() - _cache["at"] < CACHE_TTL_S:
            return _cache["value"]
        errors: list[str] = []
        services, backups = await asyncio.gather(_collect_services(errors), _collect_backups(errors))
        value = {"services": services, "backups": backups, "errors": errors, "generated_at": time.time()}
        _cache.update(at=time.monotonic(), value=value)
        return value


def reset_cache() -> None:
    """Fuer Tests -- der Zwischenspeicher lebt im Prozess."""
    _cache.update(at=0.0, value=None)


@router.get("", dependencies=[Depends(require_permission("hosts.read"))])
async def overview(session: SessionDep) -> OverviewOut:
    expensive = await _expensive_part()
    pending = (await session.execute(select(func.count()).select_from(Action).where(Action.status == "proposed"))).scalar_one()
    unread_filter = Notification.read_at.is_(None)
    unread = (await session.execute(select(func.count()).select_from(Notification).where(unread_filter))).scalar_one()
    attention_rows = (await session.execute(
        select(Notification)
        .where(unread_filter, Notification.severity.in_(("warning", "critical")))
        .order_by(Notification.ts.desc())
        .limit(6)
    )).scalars().all()
    services: list[ServiceOut] = expensive["services"]
    return OverviewOut(
        services=services,
        services_running=sum(1 for s in services if s.state == "running"),
        apps=await _app_tiles(session, services),
        backups=expensive["backups"],
        pending_actions=int(pending),
        unread_notifications=int(unread),
        attention=[
            AttentionItem(id=n.id, ts=n.ts, severity=n.severity, title=n.title, source_ext_id=n.source_ext_id)
            for n in attention_rows
        ],
        errors=expensive["errors"],
        generated_at=expensive["generated_at"],
    )
