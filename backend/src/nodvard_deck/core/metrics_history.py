"""Metrik-Verlauf für Hosts ohne eigene Anbieter-Historie in einer eigenen SQLite-Datei (`metrics.db`), die
jederzeit gelöscht werden darf."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger("nodvard_deck.metrics")

# Stufen: Rohwerte alle `interval_s` für `raw_retention_h`, daraus 5-Minuten-Verdichtung (Mittel/Min/Max) für
# `rollup_retention_d`.
ROLLUP_S = 300

RANGES: dict[str, int] = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
"""Erlaubte Zeiträume (Sekunden), für jeden Anbieter dieselben."""

_MAX_POINTS = 720
"""Obergrenze je Kurve: mehr Punkte als Pixel bringen nichts, kosten nur JSON."""

LATEST_WINDOW_S = 600
"""Ältere Werte zählen nicht als "letzter Wert" (Cockpit-Auslastung); der Server fehlt dann ganz."""

STALE_AFTER_S = 120
"""Ab diesem Alter gilt ein letzter Wert als veraltet (der Sammler misst alle 30 s, 4 Messungen fehlen)."""

LATEST_METRICS = ("cpu_percent", "mem_used_bytes", "mem_total_bytes", "root_used_bytes", "root_total_bytes", "uptime_s")
"""Was das Cockpit aus dem Verlauf braucht: CPU, RAM, Platte (Wurzel-Dateisystem), Betriebszeit."""


def _step_for(range_s: int, native_s: int) -> int:
    step = native_s
    while range_s / step > _MAX_POINTS:
        step *= 2
    return step


class MetricsStore:
    """Dünne, synchrone sqlite3-Schicht; die eine Verbindung verträgt keine parallelen Threads, darum läuft jeder
    Zugriff unter `_lock`."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def _db(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(self._path, check_same_thread=False, timeout=10)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS raw (
                        host_id TEXT NOT NULL, metric TEXT NOT NULL, ts INTEGER NOT NULL, value REAL NOT NULL,
                        PRIMARY KEY (host_id, metric, ts)
                    ) WITHOUT ROWID;
                    CREATE TABLE IF NOT EXISTS rollup (
                        host_id TEXT NOT NULL, metric TEXT NOT NULL, ts INTEGER NOT NULL,
                        avg REAL NOT NULL, min REAL NOT NULL, max REAL NOT NULL, n INTEGER NOT NULL,
                        PRIMARY KEY (host_id, metric, ts)
                    ) WITHOUT ROWID;
                    CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                    """
                )
                self._conn = conn
            return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def insert(self, host_id: str, ts: int, values: dict[str, float]) -> None:
        rows = [(host_id, k, ts, float(v)) for k, v in values.items() if isinstance(v, (int, float))]
        if not rows:
            return
        with self._lock:
            db = self._db()
            with db:
                db.executemany("INSERT OR REPLACE INTO raw VALUES (?, ?, ?, ?)", rows)

    def maintain(self, now: int, *, raw_retention_s: int, rollup_retention_s: int) -> None:
        """Verdichtet abgeschlossene 5-Minuten-Fenster und löscht Altes."""
        with self._lock:
            db = self._db()
            done = db.execute("SELECT value FROM state WHERE key = 'rolled_until'").fetchone()
            start = done[0] if done else 0
            until = now - now % ROLLUP_S
            with db:
                if until > start:
                    db.execute(
                        """INSERT OR REPLACE INTO rollup
                            SELECT host_id, metric, ts - ts % :step, AVG(value), MIN(value), MAX(value), COUNT(*)
                            FROM raw WHERE ts >= :start AND ts < :until
                            GROUP BY host_id, metric, ts - ts % :step""",
                        {"step": ROLLUP_S, "start": start - start % ROLLUP_S, "until": until},
                    )
                    db.execute("INSERT OR REPLACE INTO state VALUES ('rolled_until', ?)", (until,))
                db.execute("DELETE FROM raw WHERE ts < ?", (now - raw_retention_s,))
                db.execute("DELETE FROM rollup WHERE ts < ?", (now - rollup_retention_s,))

    def latest(
        self, host_ids: Iterable[str], now: int, *, max_age_s: int, metrics: Iterable[str] = LATEST_METRICS,
    ) -> dict[str, dict[str, tuple[int, float]]]:
        """Letzter Rohwert je (Host, Metrik) innerhalb von `max_age_s` als `{host_id: {metrik: (ts, wert)}}`, Hosts
        ohne Wert fehlen."""
        since = now - max_age_s
        names = tuple(metrics)
        found: dict[str, dict[str, tuple[int, float]]] = {}
        with self._lock:
            db = self._db()
            # Je Paar ein Sprung auf den Primärschlüssel: Ein Bereichsscan über `ts` läse die ganze Tabelle, denn
            # `ts` steht im Schlüssel hinten.
            for host_id in host_ids:
                for metric in names:
                    row = db.execute(
                        "SELECT ts, value FROM raw WHERE host_id = ? AND metric = ? AND ts >= ? ORDER BY ts DESC LIMIT 1",
                        (host_id, metric, since),
                    ).fetchone()
                    if row is not None:
                        found.setdefault(host_id, {})[metric] = (int(row[0]), float(row[1]))
        return found

    def query(self, host_id: str, range_name: str, now: int, *, interval_s: int, raw_retention_s: int) -> dict[str, Any]:
        range_s = RANGES[range_name]
        since = now - range_s
        use_raw = range_s <= raw_retention_s
        step = _step_for(range_s, interval_s if use_raw else ROLLUP_S)
        with self._lock:
            db = self._db()
            if use_raw:
                rows = db.execute(
                    "SELECT metric, ts - ts % ?, AVG(value), MAX(value) FROM raw WHERE host_id = ? AND ts >= ? "
                    "GROUP BY metric, ts - ts % ?",
                    (step, host_id, since, step),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT metric, ts - ts % ?, SUM(avg * n) / SUM(n), MAX(max) FROM rollup WHERE host_id = ? AND ts >= ? "
                    "GROUP BY metric, ts - ts % ?",
                    (step, host_id, since, step),
                ).fetchall()
        return columnar(((m, ts, avg, mx) for m, ts, avg, mx in rows), step=step, since=since, now=now)

    def query_hosts(
        self, host_id: str, aliases: Iterable[str], range_name: str, now: int, *, interval_s: int, raw_retention_s: int,
    ) -> dict[str, Any]:
        result = self.query(host_id, range_name, now, interval_s=interval_s, raw_retention_s=raw_retention_s)
        for alias in aliases:
            data = self.query(alias, range_name, now, interval_s=interval_s, raw_retention_s=raw_retention_s)
            for metric, values in data["series"].items():
                series = result["series"].setdefault(metric, [None] * len(values))
                peaks = result["max"].setdefault(metric, [None] * len(values))
                for index, value in enumerate(values):
                    if series[index] is None:
                        series[index] = value
                        peaks[index] = data["max"][metric][index]
        return result


def columnar(rows: Iterable[tuple[str, int, float, float | None]], *, step: int, since: int, now: int) -> dict[str, Any]:
    """Macht aus `(metric, bucket_ts, avg, max)` eine gemeinsame Zeitachse mit einer Werteliste je Metrik; Lücken
    bleiben `None`, damit die Kurve sie als Lücke zeigt."""
    by_metric: dict[str, dict[int, tuple[float, float | None]]] = {}
    for metric, ts, avg, mx in rows:
        if avg is None:
            continue
        by_metric.setdefault(metric, {})[int(ts)] = (float(avg), float(mx) if mx is not None else None)
    first = since - since % step + step
    timestamps = list(range(first, now + 1, step))
    series = {m: [points.get(t, (None,))[0] for t in timestamps] for m, points in by_metric.items()}
    peaks = {m: [points.get(t, (None, None))[1] for t in timestamps] for m, points in by_metric.items()}
    return {"step_s": step, "timestamps": timestamps, "series": series, "max": peaks}


class MetricsCollector:
    """Der einzige Schreiber in `metrics.db` (Hosts ohne eigene Historie, alle `interval_s`); von Hosts mit eigener
    Anbieter-Historie hält er nur den aktuellen Wert im Speicher."""

    def __init__(self, store: MetricsStore, *, interval_s: int, raw_retention_s: int, rollup_retention_s: int) -> None:
        self.store = store
        self.interval_s = interval_s
        self.raw_retention_s = raw_retention_s
        self.rollup_retention_s = rollup_retention_s
        self.sample_timeout_s: float = max(10, interval_s - 2)
        self._task: asyncio.Task[None] | None = None
        self._last_maintain = 0.0
        self._current: dict[str, tuple[int, dict[str, float]]] = {}

    def start(self) -> None:
        if self.interval_s > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="metrics-collector")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await asyncio.to_thread(self.store.close)

    async def _loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await self.collect_once()
            except Exception:  # noqa: BLE001 - der Sammler darf nie sterben
                logger.exception("metrics_collect_failed")
            await asyncio.sleep(max(1.0, self.interval_s - (time.monotonic() - started)))

    async def collect_once(self) -> int:
        from sqlalchemy import select

        from ..db.session import session_scope
        from ..models import Host
        from ..services import hosts as hosts_service
        from . import gate

        async with session_scope() as session:
            hosts = (await session.execute(select(Host).where(Host.enabled.is_(True)))).scalars().all()
            targets = [(h.id, hosts_service.host_to_sdk(h), h.provider_ext_id) for h in hosts]

        semaphore = asyncio.Semaphore(4)
        now = int(time.time())
        providers = await asyncio.gather(
            *(resolve_metrics_provider(sdk_host, ext_id) for _, sdk_host, ext_id in targets),
        )
        own_history: dict[int, tuple[Any, list[Any]]] = {}
        sampled: list[tuple[str, Any, Any]] = []
        for (host_id, sdk_host, _), provider in zip(targets, providers, strict=True):
            if provider is None:
                continue
            if getattr(provider, "history", None) is not None:
                own_history.setdefault(id(provider), (provider, []))[1].append(sdk_host)
            else:
                sampled.append((host_id, sdk_host, provider))

        async def _one(host_id: str, sdk_host: Any, provider: Any) -> bool:
            async with semaphore:
                try:
                    owner = metrics_provider_owner(provider)
                    if owner is None:
                        raise ValueError("Kein eindeutiger MetricsProvider registriert.")
                    with gate.authorized_ssh(host_id, operations=frozenset({"command"}), ext_id=owner):
                        values = await asyncio.wait_for(provider.sample(sdk_host), timeout=self.sample_timeout_s)
                except Exception as exc:  # noqa: BLE001 - Lücke statt Absturz
                    logger.info("metrics_sample_failed host=%s error=%s", host_id, str(exc) or type(exc).__name__)
                    return False
            await asyncio.to_thread(self.store.insert, host_id, now, values)
            return True

        # Die Anbieter mit eigener Historie laufen neben den Einzelmessungen, damit langsame Server sie nicht aufhalten.
        results = await asyncio.gather(
            *(_one(*entry) for entry in sampled),
            *(self._sample_current(provider, hosts, semaphore) for provider, hosts in own_history.values()),
        )
        stored = sum(1 for ok in results[: len(sampled)] if ok)
        known = {host_id for host_id, _, _ in targets}
        self._current = {host_id: entry for host_id, entry in self._current.items() if host_id in known}
        if time.monotonic() - self._last_maintain > ROLLUP_S:
            self._last_maintain = time.monotonic()
            await asyncio.to_thread(
                self.store.maintain, now,
                raw_retention_s=self.raw_retention_s, rollup_retention_s=self.rollup_retention_s,
            )
        return stored

    async def _sample_current(self, provider: Any, hosts: list[Any], semaphore: asyncio.Semaphore) -> None:
        if getattr(provider, "sample_many", None) is not None:
            await self._sample_bundled(provider, hosts)
        else:
            await asyncio.gather(*(self._sample_each(provider, host, semaphore) for host in hosts))

    async def _sample_bundled(self, provider: Any, hosts: list[Any]) -> None:
        try:
            found = await asyncio.wait_for(provider.sample_many(hosts), timeout=self.sample_timeout_s)
        except Exception as exc:  # noqa: BLE001 - Lücke statt Absturz
            reason = str(exc) or type(exc).__name__
            logger.info("metrics_sample_many_failed provider=%s error=%s", type(provider).__name__, reason)
            return
        now = int(time.time())
        for host in hosts:
            if host.id in found:
                self._remember(host.id, now, found[host.id])

    async def _sample_each(self, provider: Any, host: Any, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            try:
                values = await asyncio.wait_for(provider.sample(host), timeout=self.sample_timeout_s)
            except Exception as exc:  # noqa: BLE001 - Lücke statt Absturz
                logger.info("metrics_sample_failed host=%s error=%s", host.id, str(exc) or type(exc).__name__)
                return
        self._remember(host.id, int(time.time()), values)

    def _remember(self, host_id: str, now: int, values: dict[str, float]) -> None:
        # Ein leeres Ergebnis heißt „gerade kein Wert“ (Gast gestoppt): Der alte Wert darf dann nicht stehen bleiben.
        if values:
            self._current[host_id] = (now, values)
        else:
            self._current.pop(host_id, None)

    def current(
        self, host_ids: Iterable[str], now: int, *, max_age_s: int, metrics: Iterable[str] = LATEST_METRICS,
    ) -> dict[str, dict[str, tuple[int, float]]]:
        """Wie `MetricsStore.latest()`, für die Hosts mit eigener Anbieter-Historie."""
        names = tuple(metrics)
        found: dict[str, dict[str, tuple[int, float]]] = {}
        for host_id in host_ids:
            entry = self._current.get(host_id)
            if entry is None or entry[0] < now - max_age_s:
                continue
            ts, values = entry
            picked = {m: (ts, float(values[m])) for m in names if isinstance(values.get(m), (int, float))}
            if picked:
                found[host_id] = picked
        return found


def metrics_provider_owner(provider: Any) -> str | None:
    from nodvard_sdk.capabilities import MetricsProvider

    from ..ext.runtime import get_extension_runtime

    owners = {
        ext_id for ext_id, impl in get_extension_runtime().capabilities.query_named(MetricsProvider)
        if impl is provider
    }
    return owners.pop() if len(owners) == 1 else None


async def resolve_metrics_provider(sdk_host: Any, provider_ext_id: str | None) -> Any | None:
    """Eigener Anbieter des Hosts (`provider_ext_id`), sonst ein allgemeiner, der ihn messen kann (`supports`)."""
    from nodvard_sdk.capabilities import MetricsProvider

    from ..ext.runtime import get_extension_runtime

    runtime = get_extension_runtime()
    if sdk_host.metadata.get("manual_entry"):
        for candidate in runtime.capabilities.query(MetricsProvider):
            supports = getattr(candidate, "supports", None)
            if supports is not None and await supports(sdk_host):
                return candidate
    if provider_ext_id:
        provider = runtime.capabilities.provided_by(MetricsProvider, provider_ext_id)
        if provider is not None:
            return provider
    for candidate in runtime.capabilities.query(MetricsProvider):
        supports = getattr(candidate, "supports", None)
        if supports is not None and await supports(sdk_host):
            return candidate
    return None


_collector: MetricsCollector | None = None


def get_metrics_collector() -> MetricsCollector:
    global _collector
    if _collector is None:
        from ..config import get_settings

        settings = get_settings()
        _collector = MetricsCollector(
            MetricsStore(settings.data_dir / "metrics.db"),
            interval_s=settings.metrics_interval_s,
            raw_retention_s=settings.metrics_raw_retention_h * 3600,
            rollup_retention_s=settings.metrics_rollup_retention_d * 86400,
        )
    return _collector


def reset_metrics_collector() -> None:
    """Nur für Tests."""
    global _collector
    _collector = None
