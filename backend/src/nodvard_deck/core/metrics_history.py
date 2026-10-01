"""Metrik-Verlauf fuer Hosts, deren Anbieter selbst KEINE Historie hat (z. B. ein Pi
ueber SSH) -- genaue Server-Metriken wie in Grafana oder im Task-Manager.

**Abgrenzung zu D-02** (docs/00-DECISIONS.md, dort als D-02-Nachtrag festgehalten):
D-02 verbietet Zeitreihen in der RELATIONALEN Datenbank. Dieser Speicher ist bewusst
eine EIGENE SQLite-Datei (`<data_dir>/metrics.db`) mit eigener Verbindung und genau
einem Schreiber (dem Sammler unten) -- er konkurriert nicht mit der Schreibsperre von
`lattice.db`, taucht in keiner Alembic-Migration auf und darf jederzeit geloescht
werden (dann fehlt nur Verlauf, nichts Fachliches). Anbieter MIT eigener Historie
(Proxmox fuehrt RRD-Daten, `MetricsProvider.history()`) werden hier gar nicht erst
gesammelt.

Stufen: Rohwerte alle `interval_s` (Default 30 s) fuer `raw_retention_h` (48 h),
daraus 5-Minuten-Verdichtung (Mittel/Min/Max) fuer `rollup_retention_d` (35 Tage).
Groessenordnung fuer den Pi: ~18 Werte x 2 880 Messungen/Tag -- weit unter dem,
was SQLite im WAL-Modus nebenbei schreibt.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger("nodvard_deck.metrics")

ROLLUP_S = 300

RANGES: dict[str, int] = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
"""Erlaubte Zeitraeume (Sekunden) -- dieselben fuer jeden Anbieter."""

_MAX_POINTS = 720
"""Obergrenze je Kurve: mehr Punkte als Pixel bringen nichts, kosten nur JSON."""

LATEST_WINDOW_S = 600
"""Aelter als das ist ein "letzter Wert" nichts mehr wert (Cockpit-Auslastung): der Server fehlt dann ganz."""

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
    """Duenne, synchrone sqlite3-Schicht; alle Aufrufe laufen ueber `asyncio.to_thread`."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
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
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- schreiben ---------------------------------------------------------------

    def insert(self, host_id: str, ts: int, values: dict[str, float]) -> None:
        rows = [(host_id, k, ts, float(v)) for k, v in values.items() if isinstance(v, (int, float))]
        if not rows:
            return
        db = self._db()
        with db:
            db.executemany("INSERT OR REPLACE INTO raw VALUES (?, ?, ?, ?)", rows)

    def maintain(self, now: int, *, raw_retention_s: int, rollup_retention_s: int) -> None:
        """Abgeschlossene 5-Minuten-Fenster verdichten, dann Altes loeschen."""
        db = self._db()
        done = db.execute("SELECT value FROM state WHERE key = 'rolled_until'").fetchone()
        start = done[0] if done else 0
        until = now - now % ROLLUP_S
        with db:
            if until > start:
                db.execute(
                    f"""INSERT OR REPLACE INTO rollup
                        SELECT host_id, metric, ts - ts % {ROLLUP_S}, AVG(value), MIN(value), MAX(value), COUNT(*)
                        FROM raw WHERE ts >= ? AND ts < ?
                        GROUP BY host_id, metric, ts - ts % {ROLLUP_S}""",
                    (start - start % ROLLUP_S, until),
                )
                db.execute("INSERT OR REPLACE INTO state VALUES ('rolled_until', ?)", (until,))
            db.execute("DELETE FROM raw WHERE ts < ?", (now - raw_retention_s,))
            db.execute("DELETE FROM rollup WHERE ts < ?", (now - rollup_retention_s,))

    # -- lesen -------------------------------------------------------------------

    def latest(
        self, host_ids: Iterable[str], now: int, *, max_age_s: int, metrics: Iterable[str] = LATEST_METRICS,
    ) -> dict[str, dict[str, tuple[int, float]]]:
        """Letzter Rohwert je (Host, Metrik) innerhalb von `max_age_s`: `{host_id: {metrik: (ts, wert)}}`.
        Hosts ohne Wert fehlen. Je Paar ein Sprung auf den Primaerschluessel (rueckwaerts, ein Treffer) --
        ein Bereichsscan ueber `ts` wuerde die ganze Tabelle lesen, denn `ts` steht im Schluessel hinten."""
        db = self._db()
        since = now - max_age_s
        names = tuple(metrics)
        found: dict[str, dict[str, tuple[int, float]]] = {}
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


def columnar(rows: Iterable[tuple[str, int, float, float | None]], *, step: int, since: int, now: int) -> dict[str, Any]:
    """(metric, bucket_ts, avg, max) -> gemeinsame Zeitachse, eine Werteliste je Metrik.
    Luecken (Host nicht erreichbar) bleiben `None` -- die Kurve zeigt sie als Luecke,
    statt sie wegzuinterpolieren."""
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
    """Genau EIN Schreiber in `metrics.db`: fragt alle `interval_s` jeden Host ab, dessen
    Anbieter keine eigene Historie hat, und legt die Werte ab. Ein nicht erreichbarer
    Host wird uebersprungen (Luecke im Verlauf), nie zum Fehler fuer die anderen."""

    def __init__(self, store: MetricsStore, *, interval_s: int, raw_retention_s: int, rollup_retention_s: int) -> None:
        self.store = store
        self.interval_s = interval_s
        self.raw_retention_s = raw_retention_s
        self.rollup_retention_s = rollup_retention_s
        self._task: asyncio.Task[None] | None = None
        self._last_maintain = 0.0

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
        from ..db.session import session_scope
        from ..models import Host
        from ..services import hosts as hosts_service
        from sqlalchemy import select

        async with session_scope() as session:
            hosts = (await session.execute(select(Host).where(Host.enabled.is_(True)))).scalars().all()
            targets = [(h.id, hosts_service.host_to_sdk(h), h.provider_ext_id) for h in hosts]

        semaphore = asyncio.Semaphore(4)
        now = int(time.time())

        async def _one(host_id: str, sdk_host: Any, provider_ext_id: str | None) -> bool:
            provider = await resolve_metrics_provider(sdk_host, provider_ext_id)
            if provider is None or getattr(provider, "history", None) is not None:
                return False
            async with semaphore:
                try:
                    values = await asyncio.wait_for(provider.sample(sdk_host), timeout=max(10, self.interval_s - 2))
                except Exception as exc:  # noqa: BLE001 - Luecke statt Absturz
                    logger.info("metrics_sample_failed host=%s error=%s", host_id, str(exc) or type(exc).__name__)
                    return False
            await asyncio.to_thread(self.store.insert, host_id, now, values)
            return True

        stored = sum(await asyncio.gather(*(_one(*t) for t in targets)))
        if time.monotonic() - self._last_maintain > ROLLUP_S:
            self._last_maintain = time.monotonic()
            await asyncio.to_thread(
                self.store.maintain, now,
                raw_retention_s=self.raw_retention_s, rollup_retention_s=self.rollup_retention_s,
            )
        return stored


async def resolve_metrics_provider(sdk_host: Any, provider_ext_id: str | None) -> Any | None:
    """Eigener Anbieter des Hosts (provider_ext_id), sonst ein allgemeiner, der den Host
    nach eigener Auskunft (`supports`) messen kann -- etwa per SSH. Gemeinsam fuer die
    Live-Werte (api/v1/hosts.py), den Verlauf und den Sammler."""
    from nodvard_sdk.capabilities import MetricsProvider

    from ..ext.runtime import get_extension_runtime

    runtime = get_extension_runtime()
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
    """Nur fuer Tests."""
    global _collector
    _collector = None
