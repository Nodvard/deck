"""Liest die öffentliche Status-Seite von Uptime Kuma (ohne Anmeldung)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# GET /api/status-page/<kurzname> liefert die Monitore, GET /api/status-page/heartbeat/<kurzname> die Herzschläge.
MAX_OUTAGES = 10
MESSAGE_LIMIT = 200


@dataclass
class PageMonitor:
    id: str
    name: str
    type: str | None = None


@dataclass
class Beat:
    status: int | None
    time: datetime | None
    message: str
    ping: int | None


@dataclass
class Outage:
    monitor: str
    time: datetime | None
    message: str


@dataclass
class StatusPage:
    monitors: list[PageMonitor] = field(default_factory=list)
    uptime_by_id: dict[str, float] = field(default_factory=dict)  # letzte 24 Stunden in Prozent, 2 Nachkommastellen
    last_beat: dict[str, Beat] = field(default_factory=dict)
    outages: list[Outage] = field(default_factory=list)

    def uptime_for(self, name: str) -> float | None:
        """Per Name, weil `/metrics` älterer Versionen keine Nummer liefert; bei doppelten Namen gilt der erste."""
        for monitor in self.monitors:
            if monitor.name == name:
                return self.uptime_by_id.get(monitor.id)
        return None


def parse_kuma_time(value: Any) -> datetime | None:
    """Wandelt die UTC-Zeit `2026-10-01 12:30:00.123` (auch mit `T`/`Z`) in ein datetime mit Zeitzone."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace(" ", "T", 1))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(value)


def parse_monitors(body: Any) -> list[PageMonitor] | None:
    """Die Monitore der Status-Seite; `None`, wenn die Antwort keine Status-Seite ist."""
    if not isinstance(body, dict) or not isinstance(body.get("publicGroupList"), list):
        return None
    monitors: list[PageMonitor] = []
    seen: set[str] = set()
    for group in body["publicGroupList"]:
        entries = group.get("monitorList") if isinstance(group, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            monitor_id = entry.get("id")
            name = entry.get("name")
            if isinstance(monitor_id, bool) or not isinstance(monitor_id, (int, str)) or not isinstance(name, str):
                continue
            key = str(monitor_id)
            if key in seen:
                continue
            seen.add(key)
            kind = entry.get("type")
            monitors.append(PageMonitor(key, name, kind if isinstance(kind, str) and kind else None))
    return monitors


def _beats(raw: Any) -> list[Beat]:
    beats: list[Beat] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        ping = _as_int(item.get("ping"))
        message = item.get("msg")
        beats.append(
            Beat(
                status=_as_int(item.get("status")),
                time=parse_kuma_time(item.get("time")),
                message=" ".join(message.split())[:MESSAGE_LIMIT] if isinstance(message, str) else "",
                ping=ping if ping is not None and ping >= 0 else None,
            )
        )
    # Der neueste Herzschlag steht zuletzt; solche ohne lesbare Zeit behalten ihren Platz (stabile Sortierung).
    beats.sort(key=lambda b: b.time or datetime.min.replace(tzinfo=UTC))
    return beats


def _outages_of(monitor: PageMonitor, beats: list[Beat]) -> list[Outage]:
    """Ein Ausfall je zusammenhängender Folge von Herzschlägen mit Status 0; Zeit und Meldung stammen vom ersten."""
    found: list[Outage] = []
    previous_down = False
    for beat in beats:
        down = beat.status == 0
        if down and not previous_down:
            found.append(Outage(monitor.name, beat.time, beat.message))
        previous_down = down
    return found


def parse_heartbeats(body: Any, monitors: list[PageMonitor]) -> StatusPage | None:
    """`None`, wenn die Antwort keine Herzschlag-Liste ist."""
    if not isinstance(body, dict) or not isinstance(body.get("heartbeatList"), dict):
        return None
    heartbeats: dict[str, Any] = body["heartbeatList"]
    uptimes = body.get("uptimeList") if isinstance(body.get("uptimeList"), dict) else {}
    page = StatusPage(monitors=list(monitors))
    for monitor in monitors:
        beats = _beats(heartbeats.get(monitor.id))
        if beats:
            page.last_beat[monitor.id] = beats[-1]
        page.outages.extend(_outages_of(monitor, beats))
        ratio = uptimes.get(f"{monitor.id}_24")  # Anteil der letzten 24 Stunden von 0 bis 1
        if not isinstance(ratio, bool) and isinstance(ratio, (int, float)) and math.isfinite(ratio):
            page.uptime_by_id[monitor.id] = round(min(max(ratio, 0.0), 1.0) * 100, 2)
    epoch = datetime.min.replace(tzinfo=UTC)
    page.outages.sort(key=lambda o: o.time or epoch, reverse=True)
    del page.outages[MAX_OUTAGES:]
    return page
