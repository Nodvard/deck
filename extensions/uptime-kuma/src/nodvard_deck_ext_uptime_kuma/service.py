"""Holt den Zustand jeder Uptime-Kuma-Verbindung und führt /metrics und Status-Seite zusammen; `overview()` wirft nie."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .client import KumaClient
from .config import ConnectionConfig, key_label, read_connections
from .metrics import STATUS_NAMES, MonitorData
from .models import Connection, Monitor, Outage, Overview, Summary
from .statuspage import StatusPage
from .transport import KumaError, Unreachable

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

logger = logging.getLogger(__name__)

# Seite und Kachel (fragt jede Minute) sollen Uptime Kuma nicht doppelt belasten.
CACHE_TTL_S = 20.0

# Ausgefallenes zuerst, Ruhiges zuletzt.
_SEVERITY = {"down": 0, "pending": 1, "unknown": 2, "maintenance": 3, "up": 4}


def _clean(problem: KumaError, key: str) -> str:
    """Der Satz einer Meldung, ohne den API-Schlüssel, falls eine Ausnahme ihn zitiert."""
    text = str(problem)
    return text.replace(key, "…") if key else text


def summarize(monitors: list[Monitor]) -> Summary:
    counts = {name: 0 for name in _SEVERITY}
    for monitor in monitors:
        counts[monitor.status if monitor.status in counts else "unknown"] += 1
    return Summary(
        total=len(monitors), up=counts["up"], down=counts["down"], pending=counts["pending"], maintenance=counts["maintenance"]
    )


def _from_metrics(data: MonitorData, page: StatusPage | None) -> Monitor:
    uptime: float | None = None
    if page is not None:
        uptime = page.uptime_by_id.get(data.id) if data.id is not None else None
        if uptime is None:
            uptime = page.uptime_for(data.name)
    return Monitor(
        name=data.name,
        type=data.type,
        url=data.url,
        status=STATUS_NAMES.get(data.status, "unknown") if data.status is not None else "unknown",
        response_ms=data.response_ms,
        uptime_24h=uptime,
        cert_days_left=data.cert_days_left,
        cert_valid=data.cert_valid,
    )


def _from_page(page: StatusPage) -> list[Monitor]:
    """Ohne /metrics kommt der Zustand eines Monitors aus seinem letzten Herzschlag."""
    monitors: list[Monitor] = []
    for entry in page.monitors:
        beat = page.last_beat.get(entry.id)
        status = STATUS_NAMES.get(beat.status, "unknown") if beat is not None and beat.status is not None else "unknown"
        monitors.append(
            Monitor(
                name=entry.name,
                type=entry.type,
                url=None,
                status=status,
                response_ms=beat.ping if beat is not None else None,
                uptime_24h=page.uptime_by_id.get(entry.id),
                cert_days_left=None,
                cert_valid=None,
            )
        )
    return monitors


def merge(metrics: list[MonitorData] | None, page: StatusPage | None) -> list[Monitor]:
    if metrics is not None:
        monitors = [_from_metrics(m, page) for m in metrics]
    elif page is not None:
        monitors = _from_page(page)
    else:
        monitors = []
    monitors.sort(key=lambda m: (_SEVERITY.get(m.status, 2), m.name.casefold()))
    return monitors


@dataclass
class _Entry:
    fingerprint: Any
    expires: float
    connection: Connection


class UptimeKumaService:
    def __init__(self, ctx: ExtensionContext, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._ctx = ctx
        self._clock = clock
        self._cache: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def clear(self) -> None:
        """Verwirft alle Zwischenergebnisse."""
        self._cache.clear()

    async def _read_key(self, name: str) -> str:
        label = key_label(name)
        try:
            handle = await self._ctx.secrets.get_handle(label)
            async with self._ctx.vault_use(handle) as value:
                key = str(value or "").strip()
        except Exception as exc:  # noqa: BLE001 - nie den Text der Ausnahme weitergeben
            logger.warning("Uptime Kuma %s: API-Schluessel nicht lesbar (%s)", name, type(exc).__name__)
            raise KumaError(
                f"Der API-Schlüssel für {name} ließ sich nicht aus dem Tresor lesen. Bitte unter Einstellungen → "
                "Erweiterungen → Uptime Kuma neu eintragen."
            ) from None
        if not key:
            raise KumaError(f"Der API-Schlüssel für {name} ist leer. Bitte unter Einstellungen → Erweiterungen → Uptime Kuma neu eintragen.")
        return key

    @staticmethod
    def _failed(cfg: ConnectionConfig, state: str, message: str) -> Connection:
        return Connection(
            name=cfg.name, url=cfg.base_url, state=state, message=message, monitors=[], summary=None, outages=[],
            status_page=bool(cfg.status_page_slug),
        )

    async def _load(self, cfg: ConnectionConfig, has_key: bool) -> Connection:
        if cfg.problem or cfg.base_url is None:
            return self._failed(cfg, "error", cfg.problem or f"Die Adresse von {cfg.name} fehlt.")
        # Zwei Quellen, beide optional, mindestens eine nötig: `/metrics` (API-Schlüssel) und die öffentliche Status-Seite.
        slug = cfg.status_page_slug
        if not has_key and not slug:
            return self._failed(cfg, "not_configured", f"Für {cfg.name} fehlt der API-Schlüssel oder eine Status-Seite.")
        client = KumaClient(self._ctx.http, name=cfg.name, base_url=cfg.base_url, insecure_tls=cfg.insecure_tls)
        problems: list[KumaError] = []
        metrics: list[MonitorData] | None = None
        page: StatusPage | None = None
        key = ""
        if has_key:
            try:
                key = await self._read_key(cfg.name)
                metrics = await client.metrics(key)
            except KumaError as exc:
                problems.append(exc)
        # Ist der Server nicht erreichbar, hilft die zweite Anfrage an dieselbe Adresse nicht (und kostet das Zeitlimit).
        if slug and not (problems and isinstance(problems[0], Unreachable)):
            try:
                page = await client.status_page(slug)
            except KumaError as exc:
                problems.append(exc)
        monitors = merge(metrics, page)
        have_data = metrics is not None or page is not None
        return Connection(
            name=cfg.name,
            url=cfg.base_url,
            state=problems[0].state if problems else "ok",
            message=" ".join(dict.fromkeys(_clean(p, key) for p in problems)) if problems else None,
            monitors=monitors,
            summary=summarize(monitors) if have_data else None,
            outages=[Outage(monitor=o.monitor, time=o.time, message=o.message) for o in page.outages] if page is not None else [],
            status_page=bool(slug),
        )

    async def _connection(self, cfg: ConnectionConfig, *, refresh: bool) -> Connection:
        try:
            # Nur `exists`: den Schlüssel selbst liest erst `_load`, nie für eine Antwort aus dem Zwischenspeicher.
            has_key = False if cfg.problem else await self._ctx.secrets.exists(key_label(cfg.name))
            lock = self._locks.setdefault(cfg.name, asyncio.Lock())
            async with lock:
                now = self._clock()
                fingerprint = (cfg, has_key)
                entry = self._cache.get(cfg.name)
                if not refresh and entry is not None and entry.fingerprint == fingerprint and entry.expires > now:
                    return entry.connection
                connection = await self._load(cfg, has_key)
                self._cache[cfg.name] = _Entry(fingerprint, self._clock() + CACHE_TTL_S, connection)
                return connection
        except Exception:  # eine Verbindung darf nie die ganze Antwort kippen
            logger.exception("Uptime Kuma %s: unerwarteter Fehler", cfg.name)
            return self._failed(cfg, "error", f"Bei der Abfrage von {cfg.name} ist ein unerwarteter Fehler aufgetreten.")

    async def overview(self, *, refresh: bool = False) -> Overview:
        configs = read_connections(await self._ctx.settings.get())
        names = {c.name for c in configs}
        for gone in [n for n in self._cache if n not in names]:
            del self._cache[gone]
        connections = list(await asyncio.gather(*(self._connection(c, refresh=refresh) for c in configs)))
        total = Summary(total=0, up=0, down=0, pending=0, maintenance=0)
        for connection in connections:
            if connection.summary is not None:
                total = Summary(
                    total=total.total + connection.summary.total,
                    up=total.up + connection.summary.up,
                    down=total.down + connection.summary.down,
                    pending=total.pending + connection.summary.pending,
                    maintenance=total.maintenance + connection.summary.maintenance,
                )
        return Overview(connections=connections, summary=total)
