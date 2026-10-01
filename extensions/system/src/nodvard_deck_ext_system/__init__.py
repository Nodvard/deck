"""system-Extension (Roadmap Punkt 3: "Linux-Hosts per SSH"): der Zustand eines
Linux-Servers auf einer Seite -- Betriebssystem, Auslastung, Platten, fehlgeschlagene
Dienste, ausstehende Updates, Neustart noetig. Fuer jeden Host mit SSH-Zugang, egal ob
Proxmox-Gast, Raspberry Pi oder gemieteter Server (herstellerneutral).

Lesend, bis auf EINE Aktion: "Dienst neu starten" (actions.py). Sie geht wie bei den
anderen Erweiterungen als Vorschlag ans Aktions-Gate (`ctx.actions.propose`), ausgefuehrt
erst nach Freigabe. Alles Lesende laeuft ueber `ctx.exec.run()` wie jedes andere
SSH-Kommando und braucht deshalb `hosts.execute` -- dieselbe Stufe wie Logs und Terminal.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, status
from nodvard_sdk import (
    Badge,
    ExtensionContext,
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
from .warnung import DEFAULT_DISK_PERCENT, DEFAULT_INTERVAL_MIN, DEFAULT_TEMP_C, run_warnungen

_WIDGET_CACHE_S = 300
_TONE_ORDER = {"danger": 0, "warn": 1, "good": 2}


def widget_row(host: Any, info: dict[str, Any] | None, error: str | None) -> dict[str, Any]:
    """Eine Zeile des Widgets "Server-Zustand": schlimmster Befund zuerst."""
    name = host.display_name or host.name
    if info is None:
        return {"name": name, "host_id": host.id, "summary": error or "nicht erreichbar", "badge": "nicht erreichbar", "tone": "danger"}
    # Stabil nach Schwere sortiert: der Text kam sonst vom ERSTEN Befund
    # (Platten stehen vor Diensten), der Ton aber vom schlimmsten.
    findings = sorted(info["findings"], key=lambda f: _TONE_ORDER.get(f["tone"], 2))
    tone = findings[0]["tone"] if findings else "good"
    return {
        "name": name,
        "host_id": host.id,
        "summary": findings[0]["text"] if findings else f"{info.get('os') or 'Linux'} -- alles in Ordnung",
        "badge": "OK" if not findings else f"{len(findings)} Befund{'e' if len(findings) > 1 else ''}",
        "tone": tone,
    }


class SshMetricsProvider:
    """Erfuellt `MetricsProvider` fuer Linux-Hosts OHNE eigenen Anbieter (z. B. den Pi).

    Eine Messung = ein SSH-Aufruf mit dem Live-Skript (live.py, ~1 s wegen der
    Raten). Das Ergebnis dient drei Lesern: der Live-Ansicht (alle paar Sekunden,
    solange jemand hinschaut), den Live-Ringen der Server-Seite und dem Verlaufs-
    Sammler des Kerns (alle 30 s). Kurz zwischengespeichert, damit sie sich eine
    Messung teilen statt den Host mehrfach parallel abzufragen."""

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
        async with lock:  # gleichzeitige Leser warten auf DIESELBE Messung
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


_NEVER = "0 0 31 2 *"  # 31. Februar -- der Job existiert, laeuft aber nie (Warnungen aus)


class _WarnJobSpec:
    """Platte fast voll / Temperatur zu hoch als Meldung (warnung.py). Zeitplan und
    Schalter kommen aus den Einstellungen; `register_job` ist ein Upsert ueber die ID."""

    id = "warnung"
    name = "Warnungen: Platte und Temperatur"
    params: dict[str, Any] = {}

    def __init__(self, ctx: ExtensionContext, settings: dict[str, Any]) -> None:
        self._ctx = ctx
        interval = max(5, min(59, int(settings.get("warn_interval_min") or DEFAULT_INTERVAL_MIN)))
        self.enabled = settings.get("warn_disk_enabled", True) is not False or settings.get("warn_temp_enabled", True) is not False
        self.schedule = f"*/{interval} * * * *" if self.enabled else _NEVER

    async def handler(self, **_: Any) -> dict[str, int]:
        return await run_warnungen(self._ctx)


def _thresholds(settings: dict[str, Any]) -> tuple[int, int]:
    """(Plattenschwelle %, Temperaturschwelle °C) fuer die Anzeige -- dieselben Werte wie die Warnungen."""
    return int(settings.get("warn_disk_percent") or DEFAULT_DISK_PERCENT), int(settings.get("warn_temp_c") or DEFAULT_TEMP_C)


class Extension(NodvardExtension):
    async def on_settings_changed(self, ctx: ExtensionContext, values: dict[str, Any]) -> None:
        await ctx.scheduler.register_job(_WarnJobSpec(ctx, values))

    async def setup(self, ctx: ExtensionContext) -> None:
        await ctx.scheduler.register_job(_WarnJobSpec(ctx, await ctx.settings.get()))
        metrics = SshMetricsProvider(ctx)
        ctx.capabilities.provide(metrics)
        ctx.capabilities.provide(SystemActionExecutor(ctx))
        for spec in ACTION_SPECS:
            ctx.actions.register(spec)
        router = APIRouter()
        self._widget_cache: tuple[float, list[dict[str, Any]]] | None = None

        async def _query(host: Any) -> dict[str, Any]:
            try:
                result = await asyncio.wait_for(ctx.exec.run(host, SCRIPT, timeout_s=20), timeout=25)
            except Exception as exc:  # noqa: BLE001 - ein Host darf die anderen nicht verstecken
                return widget_row(host, None, f"nicht erreichbar: {str(exc) or type(exc).__name__}")
            if "@@os" not in result.stdout:
                return widget_row(host, None, "Abfrage fehlgeschlagen")
            info = parse_system_info(result.stdout)
            apply_thresholds(info, *_thresholds(await ctx.settings.get()))
            info["findings"] = summary_findings(info)
            return widget_row(host, info, None)

        @router.get("/widgets/health")
        async def health_widget() -> dict:
            """Alle Linux-Hosts mit SSH-Zugang, schlimmster zuerst. 5 min zwischengespeichert:
            je Host ein SSH-Aufruf mit 1 s CPU-Messung -- nicht bei jedem Dashboard-Refresh."""
            now = time.monotonic()
            if self._widget_cache and now - self._widget_cache[0] < _WIDGET_CACHE_S:
                return {"data": self._widget_cache[1], "meta": {"cached": True}}
            hosts = [h for h in await ctx.hosts.list() if h.os_family == "linux" and h.has_credential]
            rows = list(await asyncio.gather(*(_query(h) for h in hosts)))
            rows.sort(key=lambda r: (_TONE_ORDER.get(r["tone"], 2), r["name"].lower()))
            self._widget_cache = (now, rows)
            return {"data": rows, "meta": {"cached": False}}

        @router.get("/hosts/{host_id}/live")
        async def host_live(host_id: str) -> dict:
            """Task-Manager-Ansicht: pro Kern, Takt, Temperaturen, RAM-Aufschluesselung,
            jede Platte/Netzwerkkarte, Dateisysteme, Top-Prozesse (live.py)."""
            host = await ctx.hosts.get(host_id)
            if host is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
            if host.os_family != "linux":
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Live-Werte gibt es nur für Linux-Hosts.")
            try:
                live = await metrics.live(host)
            except Exception as exc:  # noqa: BLE001 - dem Nutzer zeigen, nicht als 500 verstecken
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Nicht erreichbar: {str(exc) or type(exc).__name__}") from exc
            return {**live, "host": {"id": host.id, "name": host.display_name or host.name}}

        @router.get("/hosts/{host_id}/info")
        async def host_info(host_id: str) -> dict:
            host = await ctx.hosts.get(host_id)
            if host is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
            if host.os_family != "linux":
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Die System-Seite gibt es nur für Linux-Hosts.")
            try:
                result = await ctx.exec.run(host, SCRIPT, timeout_s=30)
            except Exception as exc:  # noqa: BLE001 - dem Nutzer zeigen, nicht als 500 verstecken
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Nicht erreichbar: {exc}") from exc
            if "@@os" not in result.stdout:
                detail = result.stderr.strip() or f"Exit-Code {result.exit_code}"
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Abfrage fehlgeschlagen: {detail}")
            info = parse_system_info(result.stdout)
            apply_thresholds(info, *_thresholds(await ctx.settings.get()))
            info["findings"] = summary_findings(info)
            units = list(dict.fromkeys([*info["failed_units"], *info["running_units"]]))
            # Docker/containerd nur anbieten, wo dort nicht das Dashboard laeuft -- wie die
            # Route, aber vor dem Knopf statt erst nach der Rueckfrage.
            # Die Abfrage nur, wenn so eine Einheit ueberhaupt in der Liste steht; "nicht
            # feststellbar" zaehlt als gesperrt.
            dashboard = await is_dashboard_host(ctx, host) if any(needs_dashboard_check(u) for u in units) else False
            info["restartable_units"] = [u for u in units if is_restartable(u, dashboard_host=dashboard)]
            info["host"] = {"id": host.id, "name": host.display_name or host.name, "address": host.address}
            return info

        router.include_router(build_action_router(ctx))
        ctx.api.include_router(router, permission="hosts.execute")

        ctx.ui.register_page(
            PageSpec(
                id="system",
                path="/system",
                title="System",
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
            id="system", title="System-Monitor", icon="cpu", category="monitoring",
            description="Live wie im Task-Manager: Kerne, Temperaturen, Platten, Netz, Prozesse -- dazu Dienste und Updates",
            path="/system?host={host_id}", os_families=["linux"], permissions=["hosts.execute"], order=50,
        ))
        # Dienste neu starten geht ueber `sudo -n` (actions.as_root) -- der Einrichtungsbefehl
        # bietet dafuer "Root-Rechte ohne Passwort" an, "Verbindung pruefen" testet es.
        ctx.ui.register_host_requirement(HostRequirementSpec(
            id="root", label="Root-Rechte (System)", needs_root=True, root_reason="Dienste neu starten", order=20,
        ))

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        return HealthReport(healthy=True)
