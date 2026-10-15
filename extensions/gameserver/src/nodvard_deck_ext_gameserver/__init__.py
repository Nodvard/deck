"""gameserver-Erweiterung: Gameserver steuern und beobachten (Status, Spieler, Join-Code, Welt sichern, Start/Stop/Neustart).

Alles Spiel- und Betriebssystem-Spezifische steckt in `profiles/`, pro Server wählbar (`settings.servers.<host_id>`).
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk import (
    REQUEST_WAIT_S,
    HostToolSpec,
    Actor,
    ActionRequest,
    ActionResult,
    Badge,
    DryRunReport,
    ExtensionContext,
    GridSize,
    HealthReport,
    NodvardExtension,
    ListItem,
    ListView,
    PageSpec,
    Refresh,
    Risk,
    WidgetAction,
    WidgetSpec,
)
from pydantic import BaseModel

from .joincode import DEFAULT_JOIN_CODE_PATTERN, extract_join_code
from .profiles import PROFILES, config_for, get_profile
from .watch import LIVE_HOST_STATUSES, run_gameserver_watch

# Deutsche Anzeige für den Host-Status (GameServerPage.tsx hat dieselben Wörter).
_STATUS_LABEL = {"up": "läuft", "down": "gestoppt", "unknown": "unbekannt", "maintenance": "Wartung"}

_SERVICE_LABEL = {"running": "läuft", "stopped": "gestoppt", "startpending": "startet", "stoppending": "stoppt", "missing": "kein Dienst"}

# Eigene Aktionstypen `gameserver.*`: Sie steuern per SSH (wie die Statusabfrage) den Spiel-Dienst, nicht die ganze VM.
ACTIONS: dict[str, tuple[str, Risk]] = {
    "start": ("gameserver.start", Risk.LOW),
    "stop": ("gameserver.stop", Risk.HIGH),
    "restart": ("gameserver.restart", Risk.MEDIUM),
    "backup": ("gameserver.backup", Risk.LOW),
}
_VERB = {"start": "gestartet", "stop": "gestoppt", "restart": "neu gestartet", "backup": "Welt gesichert"}

STATUS_TTL_S = 20.0
UNREACHABLE_TTL_S = 60.0


class GameServerStatus:
    """Status je Host mit kurzem Zwischenspeicher: Liste, Widget, Detailseite und Wächter teilen sich eine SSH-Abfrage."""

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def invalidate(self, host_id: str) -> None:
        self._cache.pop(host_id, None)

    async def get(self, host: Any, *, fresh: bool = False) -> dict[str, Any]:
        cached = self._cache.get(host.id)
        if cached and not fresh:
            ttl = STATUS_TTL_S if cached[1].get("error") is None else UNREACHABLE_TTL_S
            if time.monotonic() - cached[0] < ttl:
                return cached[1]
        settings = await self._ctx.settings.get()
        config = config_for(settings, host.id)
        profile = get_profile(config.profile)
        if getattr(host.status, "value", host.status) == "down":
            # Server aus: Eine SSH-Verbindung liefe in den Timeout, und die Seite hinge so lange auf „Lade …“.
            data = {"profile": profile.id, "running": False, "service_state": "stopped", "error": "Server ist ausgeschaltet."}
            self._cache[host.id] = (time.monotonic(), data)
            return data
        try:
            result = await self._ctx.exec.run(host, profile.status_command(config), timeout_s=45)
            if result.exit_code != 0:
                raise RuntimeError(result.stderr.strip() or f"Exit-Code {result.exit_code}")
            data = profile.parse_status(result.stdout)
            data["error"] = None
        except Exception as exc:  # noqa: BLE001 - ein nicht erreichbarer Server ist ein Zustand, kein Absturz
            data = {"profile": profile.id, "running": False, "service_state": "unknown", "error": str(exc) or type(exc).__name__}
        # Ausweg für Setups ohne passendes Profil: eigener Join-Code-Befehl (`log_command`, `join_code_pattern`).
        if settings.get("log_command") and data.get("error") is None:
            data["join_code"] = await _legacy_join_code(self._ctx, host, settings)
        self._cache[host.id] = (time.monotonic(), data)
        return data


async def _legacy_join_code(ctx: ExtensionContext, host: Any, settings: dict[str, Any]) -> str | None:
    try:
        result = await ctx.exec.run(host, settings["log_command"], timeout_s=15)
    except Exception:  # noqa: BLE001
        return None
    if result.exit_code != 0:
        return None
    return extract_join_code(result.stdout, pattern=settings.get("join_code_pattern") or DEFAULT_JOIN_CODE_PATTERN)


class _GameServerActionExecutor:
    """`gameserver.start/stop/restart/backup`: Der Befehl kommt aus dem Profil des Servers; gesetzte alte
    `start_command`/`stop_command` haben Vorrang."""

    action_types = frozenset(action_type for action_type, _ in ACTIONS.values())

    def __init__(self, ctx: ExtensionContext, statuses: GameServerStatus) -> None:
        self._ctx = ctx
        self._statuses = statuses

    async def _command_for(self, action_type: str, host_id: str) -> str:
        settings = await self._ctx.settings.get()
        action = action_type.split(".", 1)[1]
        legacy = settings.get(f"{action}_command") if action in ("start", "stop") else None
        if legacy:
            return str(legacy).format(service_name=settings.get("service_name") or "valheim")
        config = config_for(settings, host_id)
        return get_profile(config.profile).action_command(action, config)

    async def execute(self, req: ActionRequest) -> ActionResult:
        if not req.host_ref:
            return ActionResult(success=False, error="ActionRequest.host_ref fehlt.")
        host = await self._ctx.hosts.get(req.host_ref)
        if host is None:
            return ActionResult(success=False, error=f"Host '{req.host_ref}' nicht gefunden.")
        command = await self._command_for(req.action_type, host.id)
        result = await self._ctx.exec.run(host, command, timeout_s=120)
        self._statuses.invalidate(host.id)
        return ActionResult(
            success=result.exit_code == 0,
            exit_code=result.exit_code,
            output=result.stdout,
            error=result.stderr or None,
            duration_ms=result.duration_ms,
        )

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        action = req.action_type.split(".", 1)[1]
        return DryRunReport(would_change=True, summary=f"Gameserver wird {_VERB.get(action, action)}.")


class _WatchJobSpec:
    """Join-Code-Wechsel und unerwartet gestoppte Server als Meldung (watch.py)."""

    id = "watch"
    name = "Gameserver-Wächter"
    schedule = "*/5 * * * *"
    params: dict[str, Any] = {}
    enabled = True

    def __init__(self, ctx: ExtensionContext, statuses: GameServerStatus) -> None:
        self._ctx = ctx
        self._statuses = statuses

    async def handler(self, **_: Any) -> dict[str, int]:
        return await run_gameserver_watch(self._ctx, self._statuses)


class _ConfigIn(BaseModel):
    profile: str
    values: dict[str, str] = {}


def _players_display(data: dict[str, Any]) -> str:
    players = data.get("players_online")
    if players is None:
        return "Spielerzahl unbekannt"
    return "1 Spieler online" if players == 1 else f"{players} Spieler online"


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        statuses = GameServerStatus(ctx)
        self._statuses = statuses
        ctx.capabilities.provide(_GameServerActionExecutor(ctx, statuses))

        ctx.settings.declare(
            {
                "type": "object",
                "properties": {
                    "host_tag": {"type": "string"},
                    "servers": {"type": "object", "additionalProperties": {"type": "object"}},
                    "log_command": {"type": "string"},
                    "join_code_pattern": {"type": "string"},
                    "service_name": {"type": "string"},
                    "start_command": {"type": "string"},
                    "stop_command": {"type": "string"},
                },
            }
        )

        router = APIRouter()

        async def _hosts() -> list[Any]:
            settings = await ctx.settings.get()
            return await ctx.hosts.list(tag=settings.get("host_tag") or "gameserver")

        async def _host_or_404(host_id: str) -> Any:
            """Nur getaggte Hosts sind Gameserver: Sonst löste ein Betrachter das Statusskript per SSH auf jedem Host aus."""
            host = await ctx.hosts.get(host_id)
            settings = await ctx.settings.get()
            if host is None or (settings.get("host_tag") or "gameserver") not in (host.tags or []):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Gameserver.")
            return host

        async def _summary(host: Any) -> dict[str, Any]:
            host_status = host.status.value
            data = await statuses.get(host) if host_status in LIVE_HOST_STATUSES else {"running": False, "service_state": "unknown"}
            running = bool(data.get("running"))
            join_code = data.get("join_code") if running or data.get("service_state") == "unknown" else None
            return {
                "host_id": host.id,
                "name": host.display_name or host.name,
                "status": host_status,
                "status_label": _STATUS_LABEL.get(host_status, host_status),
                "service_state": data.get("service_state"),
                "service_label": _SERVICE_LABEL.get(str(data.get("service_state")), "unbekannt"),
                "running": running,
                "tone": "good" if running else ("danger" if host_status in LIVE_HOST_STATUSES and data.get("service_state") == "stopped" else "neutral"),
                "players_online": data.get("players_online"),
                "players_display": _players_display(data),
                "version": data.get("version"),
                "profile": data.get("profile"),
                "error": data.get("error"),
                # Nur sinnvolle Knöpfe (kein „Starten“ neben einem laufenden Server); unbekannter Zustand: beide.
                "can_start": not running,
                "can_stop": running or data.get("service_state") == "unknown",
                "join_code": join_code,
                "join_code_display": f"Join-Code: {join_code}" if join_code else "Kein Join-Code gefunden",
            }

        @router.get("/servers")
        async def list_servers() -> list[dict[str, Any]]:
            return [await _summary(host) for host in await _hosts()]

        @router.get("/widgets/servers")
        async def servers_widget_data() -> dict[str, Any]:
            return {"data": [await _summary(host) for host in await _hosts()], "meta": {}}

        @router.get("/profiles")
        async def list_profiles() -> list[dict[str, Any]]:
            return [
                {"id": p.id, "label": p.label, "fields": [f.__dict__ for f in p.fields]}
                for p in PROFILES.values()
            ]

        async def _details(host_id: str, *, fresh: bool, with_log: bool) -> dict[str, Any]:
            """Detailansicht: Zusammenfassung, Profil-Status (Spieler, Welt, Sicherungen, Log) und Konfiguration."""
            host = await _host_or_404(host_id)
            if fresh:
                statuses.invalidate(host.id)
            summary = await _summary(host)
            data = await statuses.get(host) if host.status.value in LIVE_HOST_STATUSES else {}
            if not with_log:
                data = {k: v for k, v in data.items() if k != "log_tail"}
            config = config_for(await ctx.settings.get(), host_id)
            return {**summary, "details": data, "config": {"profile": config.profile, "values": config.values}}

        @router.get("/servers/{host_id}")
        async def server_details(host_id: str) -> dict[str, Any]:
            """Details für Leser (`hosts.read`): ohne Server-Log, immer aus dem Zwischenspeicher."""
            # Log (Spielernamen, Verbindungsdaten) und `fresh` nur mit `hosts.execute`, wie die Container-Logs im Modul Docker.
            return await _details(host_id, fresh=False, with_log=False)

        config_router = APIRouter()

        @config_router.put("/servers/{host_id}/config")
        async def set_config(host_id: str, payload: _ConfigIn) -> dict[str, Any]:
            """Profil und Werte je Server (`settings.write`); leer heißt „automatisch erkennen“ (wird nicht gespeichert)."""
            await _host_or_404(host_id)
            if payload.profile not in PROFILES:
                raise HTTPException(status_code=422, detail=f"Unbekanntes Profil '{payload.profile}'.")
            allowed = {f.key for f in PROFILES[payload.profile].fields}
            values: dict[str, str] = {}
            for key, value in payload.values.items():
                if key not in allowed:
                    raise HTTPException(status_code=422, detail=f"Unbekanntes Feld '{key}'.")
                value = value.strip()
                # Die Werte landen in einem einfach-quotierten PowerShell-Literal (`profiles.valheim._ps_literal`).
                if any(ch in value for ch in "\r\n\x00'"):
                    raise HTTPException(status_code=422, detail=f"Ungültiges Zeichen in '{key}'.")
                if value:
                    values[key] = value
            settings = await ctx.settings.get()
            servers = dict(settings.get("servers") or {})
            servers[host_id] = {"profile": payload.profile, **values}
            settings["servers"] = servers
            await ctx.settings.set(settings)
            statuses.invalidate(host_id)
            return {"profile": payload.profile, "values": values}

        async def _propose_action(host_id: str, action: str, actor: Actor) -> dict[str, Any]:
            await _host_or_404(host_id)
            action_type, risk = ACTIONS[action]
            decision = await ctx.actions.propose(
                ActionRequest(
                    action_type=action_type, host_ref=host_id, payload={},
                    risk=risk, proposed_by=actor,
                    reason=f"Gameserver über die Gameserver-Seite {_VERB[action]}.",
                ),
                wait_s=REQUEST_WAIT_S,
            )
            return {"action_id": decision.action_id, "status": decision.status.value, "risk": risk.value}

        action_router = APIRouter()

        @action_router.get("/servers/{host_id}/details")
        async def server_details_full(host_id: str, fresh: bool = False) -> dict[str, Any]:
            """Wie `/servers/{host_id}`, aber mit Server-Log und `fresh` (am Cache vorbei, etwa nach einer Aktion)."""
            return await _details(host_id, fresh=fresh, with_log=True)

        @action_router.post("/servers/{host_id}/start")
        async def start_server(host_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:
            return await _propose_action(host_id, "start", actor)

        @action_router.post("/servers/{host_id}/stop")
        async def stop_server(host_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:
            return await _propose_action(host_id, "stop", actor)

        @action_router.post("/servers/{host_id}/restart")
        async def restart_server(host_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:
            return await _propose_action(host_id, "restart", actor)

        @action_router.post("/servers/{host_id}/backup")
        async def backup_world(host_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> dict[str, Any]:
            return await _propose_action(host_id, "backup", actor)

        # Lesen braucht `hosts.read`, alles Auslösende `hosts.execute`, die Konfiguration `settings.write`.
        ctx.api.include_router(router, permission="hosts.read")
        ctx.api.include_router(action_router, permission="hosts.execute")
        ctx.api.include_router(config_router, permission="settings.write")

        ctx.ui.register_page(
            PageSpec(
                id="servers",
                path="/gameservers",
                title="Gameserver",
                icon="gamepad-2",
                nav_section="Infrastruktur",
                nav_order=40,
                component="GameServerPage",
            )
        )

        ctx.ui.register_widget(
            WidgetSpec(
                id="servers",
                title="Gameserver",
                icon="gamepad-2",
                size=GridSize(w=3, h=2),
                refresh=Refresh(interval_s=60),
                data_endpoint="widgets/servers",
                permissions=["hosts.execute"],
                view=ListView(
                    item=ListItem(
                        title="{{ name }}",
                        subtitle="{{ join_code_display }} · {{ players_display }}",
                        # Deutsche Wörter statt „running“/„stopped“; die Farbe entscheidet das Backend.
                        badge=Badge(text="{{ service_label }}", tone="{{ tone }}"),
                        actions=[
                            WidgetAction(id="start", label="Starten", endpoint="servers/{{ host_id }}/start", method="POST", style="primary", permissions=["hosts.execute"], show_if="{{ can_start }}"),
                            WidgetAction(id="stop", label="Stoppen", endpoint="servers/{{ host_id }}/stop", method="POST", style="danger", confirm=True, confirm_text="Gameserver stoppen?", permissions=["hosts.execute"], show_if="{{ can_stop }}"),
                        ],
                    ),
                    empty_text="Keine Gameserver getaggt",
                ),
            )
        )

        ctx.ui.register_host_tool(HostToolSpec(
            id="gameserver", title="Gameserver", icon="gamepad-2", category="services",
            description="Join-Code, Spieler, Welt-Sicherung, Neustart", path="/gameservers?host={host_id}",
            tags=["gameserver"],
        ))

        await ctx.scheduler.register_job(_WatchJobSpec(ctx, statuses))

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        return HealthReport(healthy=True)

