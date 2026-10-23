"""Beispieldaten („Mit Beispieldaten ansehen“) und Demo-Modus (`NODVARD_DECK_DEMO_MODE`), nur mit generischen
Kern-Konzepten."""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from nodvard_sdk.actions import ActionStatus
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..models import (
    Action,
    CustomApp,
    DashboardLayout,
    Host,
    HostCredential,
    Notification,
    NotificationDelivery,
    Setting,
)
from . import audit as core_audit

# Die IDs aller angelegten Zeilen stehen in dieser globalen Einstellung (keine Migration, kein Zusatzfeld an den
# Tabellen); das Löschen entfernt genau diese IDs und sonst nichts.
DEMO_SETTING_KEY = "demo.seeded_ids"
DEMO_NETWORK = ipaddress.ip_network("192.0.2.0/24")
"""RFC 5737 TEST-NET-1: wird im Internet nicht geroutet."""

_GRID_COLS = 12

# Die Server sind ohne Zugang angelegt: Alles, was sich per SSH verbindet (Verbindungsprüfung, Sammler, Hintergrundjobs
# der Module), braucht einen hinterlegten Zugang und fasst Beispiel-Server daher nie an. Adressen nur aus dem
# Dokumentationsbereich.
_DEMO_HOSTS: list[dict[str, Any]] = [
    {"name": "demo-nas", "display_name": "Beispiel-NAS", "address": "192.0.2.10", "status": "up",
     "tags": ["demo", "speicher"]},
    {"name": "demo-webserver", "display_name": "Beispiel-Webserver", "address": "192.0.2.11", "status": "up",
     "tags": ["demo", "web"]},
    {"name": "demo-heimserver", "display_name": "Beispiel-Heimserver", "address": "192.0.2.12", "status": "up",
     "tags": ["demo", "zuhause"]},
    {"name": "demo-sicherung", "display_name": "Beispiel-Sicherungsziel", "address": "192.0.2.13", "status": "down",
     "tags": ["demo", "sicherung"]},
    {"name": "demo-testrechner", "display_name": "Beispiel-Testrechner", "address": "192.0.2.14", "status": "unknown",
     "tags": ["demo", "test"]},
]

# (Name, Adresse, Symbol, Gruppe, Beispiel-Server als Bezug oder None), die Reihenfolge ist die Position. Nur eigene
# Apps (von Hand angelegte Links, sie gehören dem Kern); erkannte Apps kommen von einem Modul. Das Dashboard ruft die
# Adressen nie ab.
_DEMO_APPS: list[tuple[str, str, str, str, str | None]] = [
    ("Beispiel-Router", "http://192.0.2.1", "router", "Netzwerk", None),
    ("Beispiel-NAS", "http://192.0.2.10:5000", "hard-drive", "Speicher", "demo-nas"),
    ("Beispiel-Pi-hole", "http://192.0.2.12/admin", "shield-check", "Netzwerk", "demo-heimserver"),
]

# (Schwere, Titel, Text, Ziel-Server oder None für die Startseite, Minuten seit jetzt, gelesen). Sie werden direkt als
# Zeilen angelegt, nicht über `services.notifications.deliver()`: sonst gingen sie als echte Push-Nachricht an die
# Kanäle.
_DEMO_NOTIFICATIONS: list[tuple[str, str, str, str | None, int, bool]] = [
    ("critical", "Beispiel: Sicherung fehlgeschlagen",
     "Die nächtliche Sicherung auf dem Beispiel-Sicherungsziel ist nicht angekommen. So sieht eine dringende Meldung aus.",
     "demo-sicherung", 12, False),
    ("warning", "Beispiel: Speicherplatz wird knapp",
     "Auf dem Beispiel-NAS sind nur noch 8 % frei. So sieht eine Warnung aus.",
     "demo-nas", 150, False),
    ("info", "Beispiel: Updates verfügbar",
     "Für den Beispiel-Webserver warten 14 Updates. So sieht ein Hinweis aus.",
     "demo-webserver", 60 * 26, False),
    ("info", "Beispiel: Willkommen bei den Beispieldaten",
     "Alles, was du hier siehst, ist ausgedacht. Über das Band oben löschst du die Beispieldaten mit einem Klick wieder.",
     None, 60 * 24 * 3, True),
]


class DemoError(Exception):
    """Basis der Fehler dieses Moduls; die Meldung ist für Nutzer gedacht."""


class DemoBlockedError(DemoError):
    """Es gibt schon echte Server: Beispieldaten hätten dort nichts verloren."""


@dataclass
class DemoStatus:
    active: bool = False
    hosts: int = 0
    notifications: int = 0
    apps: int = 0
    layout_replaced: bool = False
    hosts_with_access: list[str] = field(default_factory=list)
    """Anzeigenamen der Beispiel-Server, an denen inzwischen ein Zugang hängt; beim Löschen werden sie trotzdem
    entfernt, die Oberfläche warnt."""


@dataclass
class DemoRemoval:
    removed_hosts: int = 0
    kept_hosts: int = 0
    removed_notifications: int = 0
    layout_restored: bool = False
    removed_apps: int = 0
    kept_apps: int = 0
    """Beispiel-Apps, die inzwischen auf eine echte Adresse zeigen und deshalb stehen bleiben."""

    @property
    def anything(self) -> bool:
        return bool(
            self.removed_hosts or self.kept_hosts or self.removed_notifications or self.layout_restored
            or self.removed_apps or self.kept_apps
        )


async def _record(session: AsyncSession) -> dict[str, Any] | None:
    from ..services import settings as settings_service

    value = await settings_service.get_global(session, DEMO_SETTING_KEY)
    return value if isinstance(value, dict) else None


def _ids(record: dict[str, Any] | None, key: str) -> list[str]:
    raw = (record or {}).get(key)
    return [i for i in raw if isinstance(i, str)] if isinstance(raw, list) else []


def _app_is_still_demo(app: CustomApp) -> bool:
    try:
        return ipaddress.ip_address((urlsplit(app.url).hostname or "").strip()) in DEMO_NETWORK
    except ValueError:
        return False


def _is_still_demo(host: Host) -> bool:
    if host.provider_ext_id:
        return False
    try:
        return ipaddress.ip_address(host.address.strip()) in DEMO_NETWORK
    except ValueError:
        return False


async def still_demo_host_ids(session: AsyncSession) -> set[str]:
    """IDs der Server, die noch erkennbar Beispiele sind, damit Hintergrundaufträge sich nie mit ihnen verbinden."""
    host_ids = _ids(await _record(session), "host_ids")
    if not host_ids:
        return set()
    rows = (await session.execute(select(Host).where(Host.id.in_(host_ids)))).scalars().unique()
    return {h.id for h in rows if _is_still_demo(h)}


async def demo_status(session: AsyncSession) -> DemoStatus:
    record = await _record(session)
    host_ids = _ids(record, "host_ids")
    notification_ids = _ids(record, "notification_ids")
    app_ids = _ids(record, "app_ids")
    hosts = (
        [h for h in (await session.execute(select(Host).where(Host.id.in_(host_ids)))).scalars().unique() if _is_still_demo(h)]
        if host_ids else []
    )
    notification_count = (
        len((await session.execute(select(Notification.id).where(Notification.id.in_(notification_ids)))).all())
        if notification_ids else 0
    )
    app_count = (
        len([a for a in (await session.execute(select(CustomApp).where(CustomApp.id.in_(app_ids)))).scalars() if _app_is_still_demo(a)])
        if app_ids else 0
    )
    with_access: list[str] = []
    if hosts:
        has_credential = set(
            (await session.execute(
                select(HostCredential.host_id).where(HostCredential.host_id.in_([h.id for h in hosts]))
            )).scalars()
        )
        with_access = [h.display_name or h.name for h in sorted(hosts, key=lambda h: h.name) if h.id in has_credential]
    # Aktiv heißt: Server oder Meldungen sind noch da. Übrig gebliebene Beispiel-Apps allein halten das Band nicht
    # am Leben (sie lassen sich wie jede App löschen; ein neues Anlegen räumt sie ohnehin zuerst weg).
    active = bool(hosts or notification_count)
    layout = (record or {}).get("layout")
    return DemoStatus(
        active=active,
        hosts=len(hosts),
        notifications=notification_count,
        apps=app_count,
        layout_replaced=active and isinstance(layout, dict),
        hosts_with_access=with_access,
    )


def _arrange(widgets: Sequence[tuple[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    x = y = row_h = 0
    for ext_id, spec in widgets:
        w = max(1, min(_GRID_COLS, spec.size.w))
        h = max(1, spec.size.h)
        if x + w > _GRID_COLS:
            x, y, row_h = 0, y + row_h, 0
        items.append({"widget_id": spec.id, "ext_id": ext_id, "x": x, "y": y, "w": w, "h": h, "config": {}})
        x += w
        row_h = max(row_h, h)
    return items


async def seed_demo_data(
    session: AsyncSession,
    *,
    user_id: str | None = None,
    widgets: Sequence[tuple[str, Any]] = (),
    actor_type: str = "system",
    actor_id: str = "demo-mode",
) -> tuple[DemoStatus, bool]:
    """Legt die Beispieldaten an und gibt `(Status, neu angelegt)` zurück, bei echten Servern wirft es
    `DemoBlockedError`."""
    from ..services import custom_apps as custom_apps_service
    from ..services import dashboard as dashboard_service
    from ..services import hosts as hosts_service
    from ..services import settings as settings_service

    current = await demo_status(session)
    if current.active:
        return current, False

    if (await session.execute(select(Host.id).limit(1))).first() is not None:
        raise DemoBlockedError(
            "Beispieldaten gibt es nur auf einer frischen Installation ohne Server – "
            "sonst würden sie sich mit deinen echten Daten vermischen."
        )

    # Ein Rest der letzten Beispieldaten (alles von Hand gelöscht)? Dann erst aufräumen, auch das gesicherte Layout
    # kommt so zurück, bevor ein neues gesichert wird.
    await remove_demo_data(session, audit=False)

    host_ids: dict[str, str] = {}
    now = utcnow()
    for spec in _DEMO_HOSTS:
        host = await hosts_service.create_host(
            session, name=spec["name"], display_name=spec["display_name"], address=spec["address"],
            tags=list(spec["tags"]),
        )
        host.status = spec["status"]
        if spec["status"] == "up":
            host.last_seen_at = now
        host_ids[spec["name"]] = host.id
    await session.flush()

    notification_ids: list[str] = []
    for severity, title, body, target, minutes_ago, read in _DEMO_NOTIFICATIONS:
        ts = now - timedelta(minutes=minutes_ago)
        row = Notification(
            ts=ts, severity=severity, title=title, body=body,
            payload={"path": f"/hosts/{host_ids[target]}" if target else "/", "demo": True},
            read_at=ts if read else None,
        )
        session.add(row)
        await session.flush()
        notification_ids.append(row.id)

    # Eigene Apps kann man ohne Server anlegen, und eine auf eine echte Adresse umgestellte Beispiel-App bleibt beim
    # Aufräumen stehen: die neuen Beispiel-Apps kommen hinter alle vorhandenen (wie jede neue App) und überschreiten
    # die Obergrenze nicht (sonst scheitert danach das Verschieben der Apps).
    existing_apps = (await session.execute(select(func.count()).select_from(CustomApp))).scalar_one()
    highest_order = (await session.execute(select(func.max(CustomApp.sort_order)))).scalar_one()
    first_order = 0 if highest_order is None else highest_order + 1
    app_ids: list[str] = []
    for position, (name, url, icon, group, host_name) in enumerate(_DEMO_APPS):
        if existing_apps + len(app_ids) >= custom_apps_service.MAX_APPS:
            break
        app = CustomApp(
            name=name, url=url, icon=icon, group_name=group, sort_order=first_order + position, open_in_new_tab=True,
            host_id=host_ids[host_name] if host_name else None, created_by_user_id=user_id,
        )
        session.add(app)
        await session.flush()
        app_ids.append(app.id)

    # Beispiel-Layout aus den Widgets, die es gerade gibt (der Kern bringt selbst keine mit, sie kommen von Modulen);
    # gibt es keine, bleibt das Layout unberührt. Das bisherige wird vorher gesichert.
    layout_backup: dict[str, Any] | None = None
    if user_id is not None and widgets:
        layout = await dashboard_service.get_or_create_default(session, user_id=user_id)
        layout_backup = {"layout_id": layout.id, "items": list(layout.items or [])}
        await dashboard_service.update_layout(session, layout, items=_arrange(widgets))

    await settings_service.set_global(
        session, DEMO_SETTING_KEY,
        {
            "host_ids": list(host_ids.values()), "notification_ids": notification_ids, "app_ids": app_ids,
            "layout": layout_backup, "created_at": now.isoformat(),
        },
        updated_by_user_id=user_id,
    )
    await core_audit.write_entry(
        session, actor_type=actor_type, actor_id=actor_id, action="system.demo.seeded", outcome="success",
        target_type="system", target_id="demo",
        detail={
            "hosts": len(host_ids), "notifications": len(notification_ids), "apps": len(app_ids),
            "layout_replaced": layout_backup is not None,
        },
    )
    return await demo_status(session), True


async def remove_demo_data(
    session: AsyncSession,
    *,
    actor_type: str = "system",
    actor_id: str = "demo-mode",
    audit: bool = True,
) -> DemoRemoval:
    """Entfernt genau die Zeilen aus `demo.seeded_ids` und stellt das Layout wieder her, bei laufender Aktion
    `HostBusyError`."""
    from ..services import hosts as hosts_service

    record = await _record(session)
    if record is None:
        return DemoRemoval()
    result = DemoRemoval()

    # Erst prüfen, dann löschen: So bleibt bei einer laufenden Aktion alles unverändert, auch ohne Rollback des
    # Aufrufers.
    host_ids = _ids(record, "host_ids")
    if host_ids:
        busy = await session.execute(
            select(Action.id).where(Action.host_id.in_(host_ids), Action.status == ActionStatus.EXECUTING.value).limit(1)
        )
        if busy.first() is not None:
            raise hosts_service.HostBusyError(
                "Auf einem Beispiel-Server läuft gerade eine Aktion. Bitte warten, bis sie fertig ist."
            )

    # Erst die Beispiel-Apps (eine, die auf eine echte Adresse umgestellt wurde, bleibt, ohne Server-Bezug,
    # denn der Beispiel-Server geht gleich weg).
    for app_id in _ids(record, "app_ids"):
        app = await session.get(CustomApp, app_id)
        if app is None:
            continue
        if _app_is_still_demo(app):
            await session.delete(app)
            result.removed_apps += 1
        else:
            result.kept_apps += 1
    await session.flush()

    for host_id in host_ids:
        host = await session.get(Host, host_id)
        if host is None:
            continue
        if not _is_still_demo(host):
            result.kept_hosts += 1
            continue
        if await hosts_service.delete_host(session, host_id):
            result.removed_hosts += 1

    notification_ids = _ids(record, "notification_ids")
    if notification_ids:
        await session.execute(
            delete(NotificationDelivery).where(NotificationDelivery.notification_id.in_(notification_ids))
        )
        removed = await session.execute(delete(Notification).where(Notification.id.in_(notification_ids)))
        result.removed_notifications = removed.rowcount or 0

    backup = record.get("layout")
    if isinstance(backup, dict) and isinstance(backup.get("layout_id"), str):
        layout = await session.get(DashboardLayout, backup["layout_id"])
        if layout is not None:
            layout.items = list(backup.get("items") or [])
            result.layout_restored = True

    await session.execute(
        delete(Setting).where(Setting.key == DEMO_SETTING_KEY, Setting.scope == "global", Setting.user_id == "")
    )
    await session.flush()
    if audit and result.anything:
        await core_audit.write_entry(
            session, actor_type=actor_type, actor_id=actor_id, action="system.demo.removed", outcome="success",
            target_type="system", target_id="demo",
            detail={
                "hosts": result.removed_hosts, "hosts_kept": result.kept_hosts,
                "notifications": result.removed_notifications, "layout_restored": result.layout_restored,
                "apps": result.removed_apps, "apps_kept": result.kept_apps,
            },
        )
    return result
