"""Aktionen des Nginx Proxy Managers, alle über das Aktions-Gate des Kerns."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nodvard_sdk import ActionResult, DryRunReport, Risk
from nodvard_sdk.actions import ActionRequest, ActionSpec

from .client import NpmError
from .config import NotConfigured

if TYPE_CHECKING:
    from .service import NpmService

# Die Namen aus „Netzwerk“ bleiben, damit gespeicherte Aktionen und offene Vorschläge weiterlaufen.
NPM_HOST_ENABLE = "network.npm_host_enable"
NPM_HOST_DISABLE = "network.npm_host_disable"

MAX_HOST_ID = 2**31 - 1

ACTION_SPECS = [
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
    # Der Payload liegt zwischen Vorschlag und Ausführung in der Datenbank, darum wird er hier
    # noch einmal streng geprüft (nur erlaubte Felder, echte ganze Zahlen).
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


def validate_host(payload: Any) -> int:
    host_id = _strict_int(_only(payload, {"host_id"}).get("host_id"), "die Nummer des Proxy-Hosts")
    if not 1 <= host_id <= MAX_HOST_ID:
        raise PayloadError("Ungültige Aktion: unbekannte Nummer des Proxy-Hosts.")
    return host_id


class NpmActionExecutor:
    """Führt freigegebene Aktionen am Nginx Proxy Manager aus (`ActionExecutor` des SDK)."""

    action_types = frozenset({NPM_HOST_ENABLE, NPM_HOST_DISABLE})

    def __init__(self, service: NpmService) -> None:
        self._service = service

    async def execute(self, req: ActionRequest) -> ActionResult:
        try:
            if req.action_type in (NPM_HOST_ENABLE, NPM_HOST_DISABLE):
                return await self._set_host(validate_host(req.payload), enabled=req.action_type == NPM_HOST_ENABLE)
        except (PayloadError, NotConfigured, NpmError) as exc:
            return ActionResult(success=False, error=str(exc))
        return ActionResult(success=False, error=f"Unbekannte Aktion '{req.action_type}'.")

    async def _set_host(self, host_id: int, *, enabled: bool) -> ActionResult:
        client = await self._service.npm_client()
        changed = await client.set_host_enabled(host_id, enabled)
        word = "eingeschaltet" if enabled else "ausgeschaltet"
        name = f"Proxy-Host {host_id}"
        try:  # nur für eine lesbare Meldung, die Änderung selbst ist schon passiert
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
            if req.action_type in (NPM_HOST_ENABLE, NPM_HOST_DISABLE):
                word = "einschalten" if req.action_type == NPM_HOST_ENABLE else "ausschalten"
                return DryRunReport(would_change=True, summary=f"Würde Proxy-Host {validate_host(req.payload)} {word}.")
        except PayloadError as exc:
            return DryRunReport(would_change=False, summary=str(exc))
        return None
