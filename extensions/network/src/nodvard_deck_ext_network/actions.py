"""Aktionen der Netzwerk-Extension -- alle laufen durch das Aktions-Gate des Kerns:
die Seite schlaegt vor (`ctx.actions.propose`), ein berechtigter Nutzer gibt frei, erst
dann ruft der Kern `NetworkActionExecutor.execute()` auf.

Der Payload steht zwischen Vorschlag und Ausfuehrung in der Datenbank und wird hier
deshalb NOCHMAL vollstaendig geprueft (nur erlaubte Felder, echte ganze Zahlen, feste
Pausenlaengen) -- nie blind weitergereicht.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nodvard_sdk import ActionResult, DryRunReport, Risk
from nodvard_sdk.actions import ActionRequest, ActionSpec

from .config import NotConfigured
from .npm import NpmError
from .pihole import PiholeError

if TYPE_CHECKING:
    from .service import NetworkService

PIHOLE_PAUSE = "network.pihole_pause"
PIHOLE_RESUME = "network.pihole_resume"
NPM_HOST_ENABLE = "network.npm_host_enable"
NPM_HOST_DISABLE = "network.npm_host_disable"

PAUSE_MINUTES = (5, 15, 60)
MAX_HOST_ID = 2**31 - 1

ACTION_SPECS = [
    ActionSpec(
        action_type=PIHOLE_PAUSE,
        label="Pi-hole-Blockierung pausieren",
        description="Schaltet die Werbe- und Tracker-Blockierung für 5, 15 oder 60 Minuten aus. Danach blockiert Pi-hole von selbst wieder.",
        icon="activity",
        default_risk=Risk.LOW,
        permissions=["hosts.execute"],
        host_bound=False,
        confirm_text="Pi-hole blockiert in dieser Zeit keine Werbung und keine Tracker. Fortfahren?",
    ),
    ActionSpec(
        action_type=PIHOLE_RESUME,
        label="Pi-hole-Blockierung fortsetzen",
        description="Schaltet die Blockierung sofort wieder ein.",
        icon="activity",
        default_risk=Risk.LOW,
        permissions=["hosts.execute"],
        host_bound=False,
    ),
    ActionSpec(
        action_type=NPM_HOST_ENABLE,
        label="Proxy-Host einschalten",
        description="Schaltet einen Proxy-Host im Nginx Proxy Manager ein – die Seite ist danach wieder erreichbar.",
        icon="activity",
        default_risk=Risk.MEDIUM,
        permissions=["hosts.execute"],
        host_bound=False,
    ),
    ActionSpec(
        action_type=NPM_HOST_DISABLE,
        label="Proxy-Host ausschalten",
        description="Schaltet einen Proxy-Host im Nginx Proxy Manager aus.",
        icon="activity",
        default_risk=Risk.MEDIUM,
        permissions=["hosts.execute"],
        host_bound=False,
        confirm_text="Die Seite ist danach nicht mehr erreichbar, bis der Proxy-Host wieder eingeschaltet wird. Fortfahren?",
    ),
]


class PayloadError(ValueError):
    pass


def _only(payload: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PayloadError("Ungültige Aktion: Daten fehlen.")
    extra = sorted(set(payload) - allowed)
    if extra:
        raise PayloadError(f"Ungültige Aktion: unerwartete Angaben ({', '.join(extra)}).")
    return payload


def _strict_int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PayloadError(f"Ungültige Aktion: {what} muss eine ganze Zahl sein.")
    return value


def validate_pause(payload: Any) -> int:
    minutes = _strict_int(_only(payload, {"minutes"}).get("minutes"), "die Pausenlänge")
    if minutes not in PAUSE_MINUTES:
        raise PayloadError("Ungültige Aktion: pausieren geht nur für 5, 15 oder 60 Minuten.")
    return minutes


def validate_resume(payload: Any) -> None:
    _only(payload, set())


def validate_host(payload: Any) -> int:
    host_id = _strict_int(_only(payload, {"host_id"}).get("host_id"), "die Nummer des Proxy-Hosts")
    if not 1 <= host_id <= MAX_HOST_ID:
        raise PayloadError("Ungültige Aktion: unbekannte Nummer des Proxy-Hosts.")
    return host_id


def pause_text(minutes: int) -> str:
    return f"{minutes} Minute{'n' if minutes != 1 else ''}"


class NetworkActionExecutor:
    """Erfuellt `nodvard_sdk.capabilities.ActionExecutor`."""

    action_types = frozenset({PIHOLE_PAUSE, PIHOLE_RESUME, NPM_HOST_ENABLE, NPM_HOST_DISABLE})

    def __init__(self, service: NetworkService) -> None:
        self._service = service

    async def execute(self, req: ActionRequest) -> ActionResult:
        try:
            if req.action_type == PIHOLE_PAUSE:
                return await self._pause(validate_pause(req.payload))
            if req.action_type == PIHOLE_RESUME:
                validate_resume(req.payload)
                return await self._resume()
            if req.action_type in (NPM_HOST_ENABLE, NPM_HOST_DISABLE):
                return await self._set_host(validate_host(req.payload), enabled=req.action_type == NPM_HOST_ENABLE)
        except (PayloadError, NotConfigured, PiholeError, NpmError) as exc:
            return ActionResult(success=False, error=str(exc))
        return ActionResult(success=False, error=f"Unbekannte Aktion '{req.action_type}'.")

    async def _pause(self, minutes: int) -> ActionResult:
        client = await self._service.pihole_client()
        state = await client.set_blocking(False, timer_s=minutes * 60)
        if state["enabled"] is not False:
            return ActionResult(success=False, error="Pi-hole hat die Pause nicht übernommen – die Blockierung ist weiter aktiv.", detail={"blocking": state})
        output = f"Pi-hole-Blockierung für {pause_text(minutes)} pausiert."
        if state["timer_s"] is None:
            output += " Achtung: Pi-hole meldet keine Restzeit – falls die Blockierung nicht von selbst zurückkommt, bitte „Fortsetzen“ drücken."
        return ActionResult(success=True, output=output, detail={"blocking": state})

    async def _resume(self) -> ActionResult:
        client = await self._service.pihole_client()
        state = await client.set_blocking(True, timer_s=None)
        if state["enabled"] is not True:
            return ActionResult(success=False, error="Pi-hole hat die Blockierung nicht wieder eingeschaltet.", detail={"blocking": state})
        return ActionResult(success=True, output="Pi-hole blockiert wieder.", detail={"blocking": state})

    async def _set_host(self, host_id: int, *, enabled: bool) -> ActionResult:
        client = await self._service.npm_client()
        changed = await client.set_host_enabled(host_id, enabled)
        word = "eingeschaltet" if enabled else "ausgeschaltet"
        name = f"Proxy-Host {host_id}"
        try:  # nur fuer eine lesbare Meldung -- die Aenderung selbst ist schon passiert
            match = next((h for h in await client.proxy_hosts() if h["id"] == host_id), None)
            if match and match["domains"]:
                name = f"„{', '.join(match['domains'])}“"
        except NpmError:
            pass
        if not changed:
            return ActionResult(success=True, output=f"{name} war schon {word}.", detail={"host_id": host_id, "changed": False})
        return ActionResult(success=True, output=f"{name} ist jetzt {word}.", detail={"host_id": host_id, "changed": True})

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        try:
            if req.action_type == PIHOLE_PAUSE:
                return DryRunReport(would_change=True, summary=f"Würde die Pi-hole-Blockierung für {pause_text(validate_pause(req.payload))} pausieren.")
            if req.action_type == PIHOLE_RESUME:
                validate_resume(req.payload)
                return DryRunReport(would_change=True, summary="Würde die Pi-hole-Blockierung wieder einschalten.")
            if req.action_type in (NPM_HOST_ENABLE, NPM_HOST_DISABLE):
                word = "einschalten" if req.action_type == NPM_HOST_ENABLE else "ausschalten"
                return DryRunReport(would_change=True, summary=f"Würde Proxy-Host {validate_host(req.payload)} {word}.")
        except PayloadError as exc:
            return DryRunReport(would_change=False, summary=str(exc))
        return None
