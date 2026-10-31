"""Cockpit-Übersicht: eine Abfrage für die Startseite (Dienste, Backups, offene Freigaben, ungelesene Warnungen).

Dienste und Backups kommen über `ServiceCatalog` und `BackupProvider`, je Quelle mit Zeitlimit; Fehler stehen in `errors`.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from nodvard_sdk import BackupProvider, ServiceCatalog
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import (
    ENV_PREFIX,
    LEGACY_ENV_PREFIX,
    legacy_env_names,
    legacy_env_shadowed,
)
from ...core import trusted_proxies, untrusted_proxies
from ...ext.runtime import get_extension_runtime
from ...models import Action, Notification
from ...services import custom_apps as custom_apps_service
from ...services import helper_hints as helper_hints_service
from ..deps import CurrentUser, SessionDep, require_permission
from .host_access import root_helpers_for, root_needs_helpers_only

router = APIRouter(prefix="/overview", tags=["overview"])

# Dienste und Backups sind teuer (Abfragen per SSH oder API): 30 s im Prozess zwischengespeichert, mit Sperre gegen
# gleichzeitiges Neuladen. Zähler aus der eigenen Datenbank sind billig und immer frisch.
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
    unreachable: bool = False
    """`true` bei der Platzhalterkachel eines Servers, dessen Dienste gerade nicht gelesen werden konnten. Sie
    ist kein Dienst und zählt nicht als "läuft nicht"."""


class AppTileOut(BaseModel):
    """Eine Kachel im Bereich "Apps": ein erkannter Dienst (`source="detected"`) oder eine eigene App
    (`source="custom"`). Felder, die es für die andere Art nicht gibt, sind `null`."""

    id: str
    """Bei erkannten Diensten wie `ServiceOut.id`; bei eigenen die ID aus `/apps` (für Ändern/Löschen)."""
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
    # Verbindungen ohne Antwort (z. B. ein ausgeschalteter Server) zählen nicht als Job, sonst wären sie ein
    # "unbekannter" Lauf; sie sind eine Warnung für sich.
    unreachable_names: list[str] = []


class AttentionItem(BaseModel):
    id: str
    ts: datetime
    severity: str
    title: str
    source_ext_id: str | None


class HelperHintOut(BaseModel):
    """Ein Server, auf dem sudo Hilfsskripte ausführen lässt und welche fehlen oder veraltet sind
    (`services.helper_hints`)."""

    host_id: str
    host_name: str
    modules: list[str]
    """Anzeigenamen der Module, deren Skript fehlt oder veraltet ist."""
    checked_at: datetime
    without_root: bool = False
    """`true`: Dort gibt es keine volle sudo-Regel, diese Module haben gerade gar kein root. Sonst laufen sie über die
    volle Regel, außer denen in `no_fallback`."""
    still_used: list[str] = []
    """Die Module aus `modules`, die ihr veraltetes Skript weiter aufrufen: Diese Module haben dort root, nur mit der alten
    Fassung, auch wenn `without_root` gilt."""
    no_fallback: list[str] = []
    """Die Module aus `modules`, die ohne ihr Skript auch mit der vollen Regel kein root haben (außer `still_used`)."""
    full_rule_removable: bool = False
    """`true`: Dort gibt es die volle sudo-Regel, nötig ist sie aber nicht, die Hilfsskripte reichen. `modules` ist dann
    leer, wenn kein Skript fehlt oder veraltet ist."""


class UntrustedProxyOut(BaseModel):
    """Eine Adresse aus dem Heimnetz, über die Anmeldungen kamen, die aber nicht als vertrauenswürdiger Proxy
    eingetragen ist (`core.untrusted_proxies`)."""

    address: str
    last_seen: datetime


def untrusted_proxy_hints() -> list[UntrustedProxyOut]:
    return [
        UntrustedProxyOut(address=address, last_seen=datetime.fromtimestamp(at, UTC))
        for address, at in untrusted_proxies.recent(trusted_proxies.current())
    ]


class LegacyEnvOut(BaseModel):
    """Eine alte Umgebungsvariable, die noch gilt, und der Name, unter dem sie künftig stehen muss."""

    old: str
    new: str
    new_set: bool = False
    """Der neue Name ist schon gesetzt und gewinnt; der alte kann einfach weg."""


def legacy_env_hints() -> list[LegacyEnvOut]:
    shadowed = legacy_env_shadowed()
    return [
        LegacyEnvOut(old=name, new=ENV_PREFIX + name.removeprefix(LEGACY_ENV_PREFIX), new_set=name in shadowed)
        for name in legacy_env_names()
    ]


class OverviewOut(BaseModel):
    services: list[ServiceOut]
    services_running: int
    services_unreachable_hosts: list[str] = []
    """Namen der Server, deren Dienste nicht gelesen werden konnten (Kacheln mit `unreachable`). Leer, wenn alle
    antworten. Die Kennzahl "x von y laufen" gilt dann nur für die übrigen Server."""
    apps: list[AppTileOut] = []
    """Eigene Apps (zuerst, nach Position) und erkannte Dienste (danach). `services` und `services_running` zählen
    weiter nur erkannte Dienste, damit ältere Clients nichts Neues falsch deuten."""
    backups: BackupSummary | None
    pending_actions: int
    unread_notifications: int
    attention: list[AttentionItem]
    helper_hints: list[HelperHintOut] = []
    """Server, die neu eingerichtet werden sollten, damit die Module mit engen Rechten laufen. Nur aus dem gemerkten
    Stand der letzten Prüfung, ohne SSH beim Abruf; weggeklickte fehlen."""
    untrusted_proxies: list[UntrustedProxyOut] = []
    """Proxys, die nicht eingetragen sind: Hinter ihnen sieht Nodvard Deck die echten Adressen nicht. Nur für den
    Inhaber, der die Liste als Einziger ändern darf, sonst leer."""
    legacy_env: list[LegacyEnvOut] = []
    """Alte Umgebungsvariablen, mit denen Nodvard Deck gestartet wurde. Sie gelten in dieser Version noch, in der
    nächsten nicht mehr. Nur für den Inhaber, sonst leer."""
    errors: list[str]
    generated_at: float


async def _collect_services(
    errors: list[str], sources: dict[str, str] | None = None, failed: list[str] | None = None,
) -> list[ServiceOut]:
    """Alle erkannten Dienste; `sources` bekommt je Dienst die Kennung der Erweiterung, die ihn gemeldet hat, `failed`
    die Kennung jeder Erweiterung, deren Dienste sich nicht lesen ließen."""
    services: list[ServiceOut] = []
    for ext_id, provider in get_extension_runtime().capabilities.query_named(ServiceCatalog):
        try:
            rows = await asyncio.wait_for(provider.list_services(), timeout=SOURCE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - eine Quelle darf die anderen nicht verdecken
            errors.append(f"Dienste: {str(exc) or type(exc).__name__}")
            if failed is not None and ext_id not in failed:
                failed.append(ext_id)
            continue
        for row in rows:
            if not row.get("name"):
                continue  # Fehlerkacheln einer Erweiterung ("Host nicht erreichbar") sind keine Dienste
            url = row.get("url")
            services.append(ServiceOut(
                id=str(row.get("id") or row["name"]), name=str(row["name"]), host=row.get("host"),
                host_id=row.get("host_id"), state=row.get("state"), tone=row.get("tone"),
                # Gleiche Regel wie bei eigenen Apps: nur http(s), sonst keine Adresse (kein `javascript:`-Link).
                url=custom_apps_service.stored_url(
                    url if isinstance(url, str) else None, custom_apps_service.SERVICE_URL_MAX,
                ),
                image=row.get("image"), unreachable=row.get("unreachable") is True,
            ))
            if sources is not None:
                sources[services[-1].id] = ext_id
    services.sort(key=lambda s: (s.state != "running", (s.name or "").lower()))
    return services


def _unreachable_hosts(services: list[ServiceOut]) -> list[str]:
    hosts: list[str] = []
    for s in services:
        name = s.host or s.name
        if s.unreachable and name not in hosts:
            hosts.append(name)
    return hosts


async def _app_tiles(session: AsyncSession, services: list[ServiceOut]) -> list[AppTileOut]:
    # Eigene Kacheln kommen immer frisch aus der Datenbank, nicht aus dem Zwischenspeicher: Wer eine anlegt, sieht sie
    # sofort. Der Server fragt ihre Adressen nie selbst ab.
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
        sources: dict[str, str] = {}
        failed: list[str] = []
        services, backups = await asyncio.gather(_collect_services(errors, sources, failed), _collect_backups(errors))
        value = {
            "services": services, "sources": sources, "failed": failed, "backups": backups, "errors": errors,
            "generated_at": time.time(),
        }
        _cache.update(at=time.monotonic(), value=value)
        return value


async def service_snapshot() -> tuple[list[ServiceOut], dict[str, str], list[str]]:
    """Die erkannten Dienste aus demselben Zwischenspeicher wie `GET /overview`, je Dienst die Erweiterung und die
    Erweiterungen, deren Dienste sich nicht lesen ließen."""
    value = await _expensive_part()
    return value["services"], value.get("sources") or {}, value.get("failed") or []


def reset_cache() -> None:
    """Für Tests: Der Zwischenspeicher lebt im Prozess."""
    _cache.update(at=0.0, value=None)


@router.get("", dependencies=[Depends(require_permission("hosts.read"))])
async def overview(session: SessionDep, user: CurrentUser) -> OverviewOut:
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
        services_unreachable_hosts=_unreachable_hosts(services),
        apps=await _app_tiles(session, services),
        backups=expensive["backups"],
        pending_actions=int(pending),
        unread_notifications=int(unread),
        attention=[
            AttentionItem(id=n.id, ts=n.ts, severity=n.severity, title=n.title, source_ext_id=n.source_ext_id)
            for n in attention_rows
        ],
        helper_hints=[
            HelperHintOut(
                host_id=h.host_id, host_name=h.host_name, modules=h.modules, checked_at=h.checked_at,
                without_root=h.without_root, still_used=h.still_used, no_fallback=h.no_fallback,
                full_rule_removable=h.full_rule_removable,
            )
            for h in await helper_hints_service.current_hints(session, root_helpers_for, root_needs_helpers_only)
        ],
        untrusted_proxies=untrusted_proxy_hints() if user.is_owner else [],
        legacy_env=legacy_env_hints() if user.is_owner else [],
        errors=expensive["errors"],
        generated_at=expensive["generated_at"],
    )
