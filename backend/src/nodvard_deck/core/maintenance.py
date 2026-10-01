"""Wartungsfenster -- docs/03-DATA-MODEL.md §9.

Verallgemeinert das frueher hartcodierte 3:45-4:45-Fenster zu einer Liste aus
(Cron-Ausdruck fuer den Fensterstart, Dauer in Minuten, betroffene Hosts) --
docs/03 §9 woertlich. **Symmetrisch** angewandt heisst: `is_in_window()` ist die
EINZIGE Pruefung, die `services.notifications.send()` fuer JEDE host-bezogene
Benachrichtigung gleich aufruft, unabhaengig vom Inhalt ("wieder da" vs. "nicht
erreichbar") -- der dokumentierte Bestandsfehler war genau, dass nur eine Richtung
unterdrueckt wurde, weil zwei unterschiedliche Codepfade existierten. Hier gibt es nur
einen.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import LOCAL_TIMEZONE
from ..db import utcnow
from .cron import cron_trigger
from .timezone import get_timezone


async def is_in_window(
    session: AsyncSession, *, host_id: str | None, host_ids: Sequence[str] | None = None
) -> bool:
    """`maintenance.windows` (docs/03 §9) ist eine Liste von
    `{"cron": "<5-Feld-Crontab fuer den Fensterstart>", "duration_minutes": int,
    "host_ids": ["<id>", ...] | "all"}`. Nutzt APSchedulers `CronTrigger`
    (ohnehin schon Abhaengigkeit, D-08) statt einer eigenen Cron-Bibliothek: der
    juengste Fensterstart im Rueckblick-Fenster `[now - duration, now]` laesst sich
    darueber exakt bestimmen, ohne selbst Kalenderarithmetik nachzubauen.

    `host_id`: die Meldung betrifft genau diesen Server. `host_ids`: Sammelmeldung ueber
    mehrere Server -- still nur, wenn alle in einem laufenden Fenster liegen (verschiedene
    Fenster duerfen sich ergaenzen). `host_id` hat Vorrang."""
    from ..services import settings as settings_service

    windows = await settings_service.get_global(session, "maintenance.windows", [])
    if not windows:
        return False

    targets = [host_id] if host_id else list(dict.fromkeys(host_ids or []))
    if not targets:
        # Keine Host-Zuordnung -> strukturell kein Wartungsfenster zustaendig
        # (services.notifications.send()'s Docstring: "eine Benachrichtigung ohne
        # host_id im Payload wird nie unterdrueckt").
        return False

    now = utcnow()
    zone = await get_timezone(session)
    running: list[object] = []  # `host_ids` der Fenster, die gerade laufen
    for window in windows:
        cron = window.get("cron")
        duration = window.get("duration_minutes", 0)
        if not cron or not isinstance(duration, (int, float)) or duration <= 0:
            continue

        if _window_covers_now(cron, duration, now, zone):
            running.append(window.get("host_ids", "all"))

    return bool(running) and all(
        any(hosts == "all" or (isinstance(hosts, list) and target in hosts) for hosts in running)
        for target in targets
    )


def _window_covers_now(cron: str, duration_minutes: float, now, zone: str = LOCAL_TIMEZONE) -> bool:  # noqa: ANN001 - datetime
    # Der Cron meint die eingestellte Ortszeit (Einstellung `system.timezone`) wie der
    # Zeitplan-Waehler und die Jobs, nicht UTC -- sonst liegt "03:00" im Sommer bei 05:00
    # deutscher Zeit.
    try:
        trigger = cron_trigger(cron, timezone=zone)
    except Exception:  # noqa: BLE001 - ein kaputtes Muster blockt nichts, statt den Aufrufer abzuschiessen
        return False

    lookback_start = now - timedelta(minutes=duration_minutes)
    last_start = trigger.get_next_fire_time(None, lookback_start)
    return last_start is not None and last_start <= now
