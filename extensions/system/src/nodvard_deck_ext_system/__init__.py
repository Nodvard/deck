"""system-Extension: Zustand eines Linux-Servers mit SSH-Zugang auf einer Seite (Auslastung, Platten, Dienste, Updates)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk import (
    Actor,
    Badge,
    ExtensionContext,
    FilePlace,
    GridSize,
    HealthReport,
    HostRequirementSpec,
    HostToolSpec,
    ListItem,
    ListView,
    NodvardExtension,
    PageSpec,
    Refresh,
    WidgetSpec,
)
from pydantic import BaseModel, ConfigDict, Field

from .actions import (
    ACTION_SPECS,
    SystemActionExecutor,
    build_action_router,
    is_dashboard_host,
    is_restartable,
    needs_dashboard_check,
)
from .live import LIVE_SCRIPT, metrics_from_live, parse_live
from .sysinfo import SCRIPT, apply_thresholds, parse_system_info, summary_findings
from .warnung import (
    DEFAULT_DISK_PERCENT,
    DEFAULT_INTERVAL_MIN,
    TEMP_LIMIT_MAX,
    TEMP_LIMIT_MIN,
    global_temp_limit,
    own_temp_limit,
    own_temp_limits,
    run_warnungen,
    temp_limit,
)

# Das Lesende läuft über `ctx.exec.run()` und braucht deshalb `hosts.execute`; schreibend ist nur „Dienst neu starten“
# (`actions.py`), als Vorschlag ans Aktions-Gate.
_WIDGET_CACHE_S = 300
_TONE_ORDER = {"danger": 0, "warn": 1, "good": 2}


class WidgetCache:
    """Zwischenspeicher des Widgets „Server-Zustand“; gilt nur, solange `key` (die Serverliste) gleich bleibt."""
    # Ein neuer Server oder Zugang soll nicht bis zu fünf Minuten fehlen.

    def __init__(self, ttl_s: float) -> None:
        self._ttl_s = ttl_s
        self._entry: tuple[float, Any, list[dict[str, Any]]] | None = None

    def get(self, key: Any, now: float) -> list[dict[str, Any]] | None:
        if self._entry is None:
            return None
        stored_at, stored_key, rows = self._entry
        return rows if stored_key == key and now - stored_at < self._ttl_s else None

    def put(self, key: Any, now: float, rows: list[dict[str, Any]]) -> None:
        self._entry = (now, key, rows)


def hosts_key(hosts: list[Any]) -> tuple[tuple[str, str, str], ...]:
    """Woran das Widget erkennt, dass sich die Serverliste geändert hat: Kennung, Adresse und Name."""
    return tuple(sorted((str(h.id), str(h.address), str(h.display_name or h.name)) for h in hosts))


def error_text(exc: BaseException) -> str:
    """Warum ein Server nicht antwortet, in einem Satz; Ausnahmen wie `TimeoutError` haben oft keinen Text."""
    if isinstance(exc, TimeoutError):  # auch asyncio.TimeoutError (seit Python 3.11 derselbe Typ)
        return "keine Antwort innerhalb der Zeitgrenze – ist der Server an?"
    if isinstance(exc, ConnectionRefusedError):
        return "Verbindung abgelehnt – läuft der SSH-Dienst?"
    return str(exc).strip() or "Verbindung fehlgeschlagen"


def widget_row(host: Any, info: dict[str, Any] | None, error: str | None) -> dict[str, Any]:
    """Eine Zeile des Widgets "Server-Zustand": schlimmster Befund zuerst."""
    name = host.display_name or host.name
    if info is None:
        return {"name": name, "host_id": host.id, "summary": error or "nicht erreichbar", "badge": "nicht erreichbar", "tone": "danger"}
    # Nach Schwere sortiert, damit Text und Ton vom selben (schlimmsten) Eintrag kommen.
    findings = sorted(info["findings"], key=lambda f: _TONE_ORDER.get(f["tone"], 2))
    tone = findings[0]["tone"] if findings else "good"
    mem_total, mem_available = info.get("mem_total"), info.get("mem_available")
    return {
        "name": name,
        "host_id": host.id,
        "summary": findings[0]["text"] if findings else f"{info.get('os') or 'Linux'} – alles in Ordnung",
        "badge": "OK" if not findings else f"{len(findings)} Befund{'e' if len(findings) > 1 else ''}",
        "tone": tone,
        # Auslastung zur Zeit der Abfrage (für die Server-Kacheln); None, wenn unbekannt.
        "cpu_percent": info.get("cpu_percent"),
        "mem_percent": (
            round(100 * (mem_total - mem_available) / mem_total)
            if isinstance(mem_total, int | float) and isinstance(mem_available, int | float) and mem_total > 0
            else None
        ),
    }


class SshMetricsProvider:
    """Messwerte für Linux-Server ohne eigenen Anbieter: ein SSH-Aufruf mit dem Live-Skript (etwa 1 s wegen der Raten)."""
    # Live-Ansicht, Live-Ringe der Server-Seite und der Verlaufs-Sammler des Kerns teilen sich eine kurz
    # zwischengespeicherte Messung, statt den Server mehrfach parallel abzufragen.

    _LIVE_CACHE_S = 2.5
    _SAMPLE_CACHE_S = 10

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def supports(self, host: Any) -> bool:
        return host.os_family == "linux" and bool(host.has_credential)

    async def metric_names(self) -> list[str]:
        return [
            "cpu_percent", "cpu_iowait_percent", "load_1", "load_5", "load_15",
            "mem_used_bytes", "mem_total_bytes", "swap_used_bytes", "swap_total_bytes",
            "net_in_bps", "net_out_bps", "disk_read_bps", "disk_write_bps", "disk_busy_percent",
            "temp_c", "root_used_bytes", "root_total_bytes", "uptime_s",
        ]

    async def live(self, host: Any, *, max_age_s: float | None = None) -> dict[str, Any]:
        max_age = self._LIVE_CACHE_S if max_age_s is None else max_age_s
        lock = self._locks.setdefault(host.id, asyncio.Lock())
        async with lock:  # gleichzeitige Leser warten auf dieselbe Messung
            cached = self._cache.get(host.id)
            if cached and time.monotonic() - cached[0] < max_age:
                return cached[1]
            result = await self._ctx.exec.run(host, LIVE_SCRIPT, timeout_s=20)
            if "@@end" not in result.stdout:
                detail = result.stderr.strip() or f"Exit-Code {result.exit_code}"
                raise RuntimeError(f"Messung unvollständig: {detail}")
            live = parse_live(result.stdout)
            self._cache[host.id] = (time.monotonic(), live)
            return live

    async def sample(self, host: Any) -> dict[str, float]:
        return metrics_from_live(await self.live(host, max_age_s=self._SAMPLE_CACHE_S))


class SystemFilePlaces:
    """Bietet bei einem Linux-Server mit SSH-Zugang `/var/log` an; das gibt es überall, es braucht keine Abfrage."""

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def places_for_host(self, host_id: str) -> list[FilePlace]:
        host = await self._ctx.hosts.get(host_id)
        if host is None or host.os_family != "linux" or not host.has_credential:
            return []
        return [FilePlace(label="Protokolle", path="/var/log", icon="file-text", hint="Systemprotokolle dieses Servers")]


_NEVER = "0 0 31 2 *"  # 31. Februar: der Job existiert, läuft aber nie (Warnungen aus)

# Nur Teiler von 60 laufen als `*/N` gleichmäßig (`*/7` liefe um :56 und gleich wieder um :00); ein anderer Wert wird
# auf den nächstkleineren gesetzt.
_MINUTE_STEPS = (5, 6, 10, 12, 15, 20, 30)


def warn_interval_min(settings: dict[str, Any]) -> int:
    value = int(settings.get("warn_interval_min") or DEFAULT_INTERVAL_MIN)
    return max(step for step in _MINUTE_STEPS if step <= max(value, _MINUTE_STEPS[0]))


class _WarnJobSpec:
    """Job für die Warnungen (Platte fast voll, Temperatur zu hoch); Zeitplan und Schalter kommen aus den Einstellungen."""

    id = "warnung"
    name = "Warnungen: Platte und Temperatur"
    params: dict[str, Any] = {}

    def __init__(self, ctx: ExtensionContext, settings: dict[str, Any]) -> None:
        self._ctx = ctx
        interval = warn_interval_min(settings)
        self.enabled = settings.get("warn_disk_enabled", True) is not False or settings.get("warn_temp_enabled", True) is not False
        self.schedule = f"*/{interval} * * * *" if self.enabled else _NEVER

    async def handler(self, **_: Any) -> dict[str, int]:
        return await run_warnungen(self._ctx)


def _thresholds(settings: dict[str, Any], host_id: str) -> tuple[int, int]:
    """(Plattenschwelle %, Temperaturschwelle °C dieses Servers), dieselben Werte wie bei den Warnungen."""
    return int(settings.get("warn_disk_percent") or DEFAULT_DISK_PERCENT), temp_limit(settings, host_id)


def _temp_limit_out(settings: dict[str, Any], host_id: str) -> dict[str, Any]:
    return {
        "host_id": host_id, "own_c": own_temp_limit(settings, host_id),
        "global_c": global_temp_limit(settings), "effective_c": temp_limit(settings, host_id),
    }


class _TempLimitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Pflichtfeld: nur ein ausdrückliches `null` nimmt die eigene Grenze heraus (dann gilt `warn_temp_c`).
    own_c: int | None = Field(..., ge=TEMP_LIMIT_MIN, le=TEMP_LIMIT_MAX, strict=True)


class Extension(NodvardExtension):
    async def on_settings_changed(self, ctx: ExtensionContext, values: dict[str, Any]) -> None:
        await ctx.scheduler.register_job(_WarnJobSpec(ctx, values))

    async def setup(self, ctx: ExtensionContext) -> None:
        await ctx.scheduler.register_job(_WarnJobSpec(ctx, await ctx.settings.get()))
        metrics = SshMetricsProvider(ctx)
        ctx.capabilities.provide(metrics)
        ctx.capabilities.provide(SystemFilePlaces(ctx))
        ctx.capabilities.provide(SystemActionExecutor(ctx))
        for spec in ACTION_SPECS:
            ctx.actions.register(spec)
        router = APIRouter()
        widget_cache = WidgetCache(_WIDGET_CACHE_S)

        async def _query(host: Any) -> dict[str, Any]:
            try:
                # Das äußere Zeitlimit (25 s) liegt über dem des SSH-Aufrufs (20 s), damit auch ein hängender
                # Verbindungsaufbau endet.
                result = await asyncio.wait_for(ctx.exec.run(host, SCRIPT, timeout_s=20), timeout=25)
            except Exception as exc:  # noqa: BLE001 - ein Host darf die anderen nicht verstecken
                return widget_row(host, None, f"nicht erreichbar: {error_text(exc)}")
            if "@@os" not in result.stdout:
                return widget_row(host, None, "Abfrage fehlgeschlagen")
            info = parse_system_info(result.stdout)
            apply_thresholds(info, *_thresholds(await ctx.settings.get(), host.id))
            info["findings"] = summary_findings(info)
            return widget_row(host, info, None)

        @router.get("/widgets/health")
        async def health_widget() -> dict:
            """Alle Linux-Server mit SSH-Zugang, schlimmster zuerst; fünf Minuten zwischengespeichert."""
            # Je Server ein SSH-Aufruf mit 1 s CPU-Messung, das soll nicht bei jedem Refresh laufen.
            now = time.monotonic()
            hosts = [h for h in await ctx.hosts.list() if h.os_family == "linux" and h.has_credential]
            key = hosts_key(hosts)
            cached = widget_cache.get(key, now)
            if cached is not None:
                return {"data": cached, "meta": {"cached": True}}
            rows = list(await asyncio.gather(*(_query(h) for h in hosts)))
            rows.sort(key=lambda r: (_TONE_ORDER.get(r["tone"], 2), r["name"].lower()))
            widget_cache.put(key, now, rows)
            return {"data": rows, "meta": {"cached": False}}

        @router.get("/hosts/{host_id}/live")
        async def host_live(host_id: str) -> dict:
            """Task-Manager-Ansicht: Kerne, Takt, Temperaturen, RAM, Platten, Netzwerkkarten, Dateisysteme, Top-Prozesse."""
            host = await ctx.hosts.get(host_id)
            if host is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
            if host.os_family != "linux":
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Live-Werte gibt es nur für Linux-Server.")
            try:
                live = await metrics.live(host)
            except Exception as exc:  # noqa: BLE001 - dem Nutzer zeigen, nicht als 500 verstecken
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Nicht erreichbar: {error_text(exc)}") from exc
            return {**live, "host": {"id": host.id, "name": host.display_name or host.name}}

        @router.get("/hosts/{host_id}/info")
        async def host_info(host_id: str) -> dict:
            host = await ctx.hosts.get(host_id)
            if host is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
            if host.os_family != "linux":
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Die Seite „Server-Zustand“ gibt es nur für Linux-Server.")
            try:
                result = await ctx.exec.run(host, SCRIPT, timeout_s=30)
            except Exception as exc:  # noqa: BLE001 - dem Nutzer zeigen, nicht als 500 verstecken
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Nicht erreichbar: {error_text(exc)}") from exc
            if "@@os" not in result.stdout:
                detail = result.stderr.strip() or f"Exit-Code {result.exit_code}"
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Abfrage fehlgeschlagen: {detail}")
            info = parse_system_info(result.stdout)
            settings = await ctx.settings.get()
            apply_thresholds(info, *_thresholds(settings, host.id))
            info["temp_warn_own_c"] = own_temp_limit(settings, host.id)
            info["findings"] = summary_findings(info)
            units = list(dict.fromkeys([*info["failed_units"], *info["running_units"]]))
            # Docker und containerd nur anbieten, wo dort nicht das Dashboard läuft (wie die Route, aber schon vor dem
            # Knopf). Gefragt wird nur, wenn so eine Einheit in der Liste steht; „nicht feststellbar“ gilt als gesperrt.
            dashboard = await is_dashboard_host(ctx, host) if any(needs_dashboard_check(u) for u in units) else False
            info["restartable_units"] = [u for u in units if is_restartable(u, dashboard_host=dashboard)]
            info["host"] = {"id": host.id, "name": host.display_name or host.name, "address": host.address}
            return info

        router.include_router(build_action_router(ctx))
        ctx.api.include_router(router, permission="hosts.execute")

        # Eigene Temperaturgrenze je Server (`warn_temp_by_host`, im Formular versteckt): lesen mit `hosts.read`,
        # ändern wie die übrigen Einstellungen mit `settings.write`.
        limit_read = APIRouter()
        limit_write = APIRouter()

        async def _known_host(host_id: str) -> Any:
            host = await ctx.hosts.get(host_id)
            if host is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
            return host

        @limit_read.get("/hosts/{host_id}/temp-limit")
        async def get_temp_limit(host_id: str) -> dict[str, Any]:
            """Ab welcher Temperatur dieser Server warnt: eigene Grenze (`own_c`, sonst null), allgemeine, gültige."""
            host = await _known_host(host_id)
            return _temp_limit_out(await ctx.settings.get(), host.id)

        @limit_write.put("/hosts/{host_id}/temp-limit")
        async def set_temp_limit(
            host_id: str, payload: _TempLimitIn, actor: Actor = Depends(ctx.api.current_actor),  # noqa: B008
        ) -> dict[str, Any]:
            """Setzt die eigene Grenze dieses Servers; `own_c: null` nimmt sie heraus (dann gilt die allgemeine)."""
            host = await _known_host(host_id)
            settings = await ctx.settings.get()
            before = own_temp_limit(settings, host.id)
            known = {h.id for h in await ctx.hosts.list()}
            # Grenzen gelöschter Server fallen beim nächsten Speichern mit heraus.
            by_host = {k: v for k, v in own_temp_limits(settings).items() if k in known and k != host.id}
            if payload.own_c is not None:
                by_host[host.id] = payload.own_c
            await ctx.settings.set({**settings, "warn_temp_by_host": by_host})
            out = _temp_limit_out(await ctx.settings.get(), host.id)
            if out["own_c"] != before:
                await _audit_temp_limit(host.id, before, out, actor)
            return out

        async def _audit_temp_limit(host_id: str, before: int | None, out: dict[str, Any], actor: Actor) -> None:
            try:
                await ctx.audit.log(
                    action="ext_system.temp_limit_changed", outcome="success", target_type="host", target_id=host_id,
                    detail={"own_c_before": before, "own_c": out["own_c"], "effective_c": out["effective_c"]}, actor=actor,
                )
            except Exception:  # noqa: BLE001 - die gespeicherte Grenze gilt auch ohne Protokollzeile
                ctx.logger.warning("Protokolleintrag zur Temperaturgrenze von %s fehlgeschlagen", host_id, exc_info=True)

        ctx.api.include_router(limit_read, permission="hosts.read")
        ctx.api.include_router(limit_write, permission="settings.write")

        ctx.ui.register_page(
            PageSpec(
                id="system",
                path="/system",
                title="Server-Zustand",
                icon="cpu",
                nav_section="Infrastruktur",
                nav_order=40,
                component="SystemPage",
            )
        )
        ctx.ui.register_widget(
            WidgetSpec(
                id="health",
                title="Server-Zustand",
                icon="cpu",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=300),
                data_endpoint="widgets/health",
                permissions=["hosts.execute"],
                view=ListView(
                    item=ListItem(
                        title="{{ name }}",
                        subtitle="{{ summary }}",
                        badge=Badge(text="{{ badge }}", tone="{{ tone }}"),
                    ),
                    empty_text="Keine Linux-Server mit SSH-Zugang",
                ),
            )
        )
        ctx.ui.register_host_tool(HostToolSpec(
            id="system", title="Server-Zustand", icon="cpu", category="monitoring",
            description="Live wie im Task-Manager: Kerne, Temperaturen, Platten, Netz, Prozesse – dazu Dienste und Updates",
            path="/system?host={host_id}", os_families=["linux"], permissions=["hosts.execute"], order=50,
        ))
        # Dienste neu starten geht über `sudo -n` (`actions.as_root`); der Einrichtungsbefehl bietet dafür „Root-Rechte
        # ohne Passwort“ an, „Verbindung prüfen“ testet es.
        ctx.ui.register_host_requirement(HostRequirementSpec(
            id="root", label="Root-Rechte (Server-Zustand)", needs_root=True, root_reason="Dienste neu starten", order=20,
        ))

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        return HealthReport(healthy=True)
