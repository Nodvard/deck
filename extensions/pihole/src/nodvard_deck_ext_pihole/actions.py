"""Aktionen von Pi-hole, alle über das Aktions-Gate des Kerns."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nodvard_sdk import ActionResult, DryRunReport, Risk
from nodvard_sdk.actions import ActionRequest, ActionSpec

from .client import PiholeError
from .config import NotConfigured, normalize_domain

if TYPE_CHECKING:
    from .service import PiholeService

# Die Namen aus „Netzwerk“ bleiben, damit gespeicherte Aktionen und offene Vorschläge weiterlaufen.
PIHOLE_PAUSE = "network.pihole_pause"
PIHOLE_RESUME = "network.pihole_resume"
GRAVITY_UPDATE = "pihole.gravity_update"
DOMAIN_ALLOW = "pihole.domain_allow"
DOMAIN_DENY = "pihole.domain_deny"

PAUSE_MINUTES = (5, 15, 60)
DOMAIN_ACTIONS = {DOMAIN_ALLOW: "allow", DOMAIN_DENY: "deny"}
DOMAIN_COMMENT = "Über Nodvard Deck eingetragen"

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
        action_type=GRAVITY_UPDATE,
        label="Pi-hole-Sperrlisten aktualisieren",
        description="Lädt alle Sperrlisten neu herunter. Das dauert ein paar Minuten; Pi-hole blockiert dabei weiter.",
        icon="refresh-cw",
        default_risk=Risk.LOW,
        permissions=["hosts.execute"],
        host_bound=False,
    ),
    ActionSpec(
        action_type=DOMAIN_ALLOW,
        label="Domain in Pi-hole erlauben",
        description="Trägt eine Domain in die Erlaubt-Liste ein. Pi-hole blockiert sie dann nicht mehr, auch wenn eine Sperrliste sie nennt.",
        icon="check",
        default_risk=Risk.MEDIUM,
        permissions=["hosts.execute"],
        host_bound=False,
    ),
    ActionSpec(
        action_type=DOMAIN_DENY,
        label="Domain in Pi-hole sperren",
        description="Trägt eine Domain in die Sperr-Liste ein. Geräte im Netz erreichen sie dann nicht mehr.",
        icon="ban",
        default_risk=Risk.MEDIUM,
        permissions=["hosts.execute"],
        host_bound=False,
        confirm_text="Geräte im Netz erreichen diese Domain danach nicht mehr. Fortfahren?",
    ),
]


class PayloadError(ValueError):
    pass


def _only(payload: Any, allowed: set[str]) -> dict[str, Any]:
    # Der Payload liegt zwischen Vorschlag und Ausführung in der Datenbank, darum wird er hier noch einmal streng geprüft
    # (nur erlaubte Felder, echte ganze Zahlen, feste Pausenlängen, gültige Domain).
    if not isinstance(payload, dict):
        raise PayloadError("Ungültige Aktion: Daten fehlen.")
    extra = sorted(set(payload) - allowed)
    if extra:
        raise PayloadError(f"Ungültige Aktion: unerwartete Angaben ({', '.join(extra)}).")
    return payload


def validate_pause(payload: Any) -> int:
    minutes = _only(payload, {"minutes"}).get("minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise PayloadError("Ungültige Aktion: die Pausenlänge muss eine ganze Zahl sein.")
    if minutes not in PAUSE_MINUTES:
        raise PayloadError("Ungültige Aktion: pausieren geht nur für 5, 15 oder 60 Minuten.")
    return minutes


def validate_empty(payload: Any) -> None:
    _only(payload, set())


def validate_domain(payload: Any) -> str:
    domain = _only(payload, {"domain"}).get("domain")
    try:
        normalized = normalize_domain(domain)
    except ValueError as exc:
        raise PayloadError(f"Ungültige Aktion: {exc}") from exc
    if normalized != domain:
        raise PayloadError("Ungültige Aktion: die Domain ist nicht in der gespeicherten Form.")
    return normalized


def pause_text(minutes: int) -> str:
    return f"{minutes} Minute{'n' if minutes != 1 else ''}"


class PiholeActionExecutor:
    """Führt freigegebene Pi-hole-Aktionen aus (`ActionExecutor` des SDK)."""

    action_types = frozenset({PIHOLE_PAUSE, PIHOLE_RESUME, GRAVITY_UPDATE, DOMAIN_ALLOW, DOMAIN_DENY})

    def __init__(self, service: PiholeService) -> None:
        self._service = service

    async def execute(self, req: ActionRequest) -> ActionResult:
        try:
            if req.action_type == PIHOLE_PAUSE:
                return await self._pause(validate_pause(req.payload))
            if req.action_type == PIHOLE_RESUME:
                validate_empty(req.payload)
                return await self._resume()
            if req.action_type == GRAVITY_UPDATE:
                validate_empty(req.payload)
                return await self._gravity()
            if req.action_type in DOMAIN_ACTIONS:
                return await self._domain(DOMAIN_ACTIONS[req.action_type], validate_domain(req.payload))
        except (PayloadError, NotConfigured, PiholeError) as exc:
            return ActionResult(success=False, error=str(exc))
        return ActionResult(success=False, error=f"Unbekannte Aktion '{req.action_type}'.")

    async def _pause(self, minutes: int) -> ActionResult:
        client = await self._service.client()
        state = await client.set_blocking(False, timer_s=minutes * 60)
        if state["enabled"] is not False:
            return ActionResult(success=False, error="Pi-hole hat die Pause nicht übernommen – die Blockierung ist weiter aktiv.", detail={"blocking": state})
        output = f"Pi-hole-Blockierung für {pause_text(minutes)} pausiert."
        if state["timer_s"] is None:
            output += " Achtung: Pi-hole meldet keine Restzeit – falls die Blockierung nicht von selbst zurückkommt, bitte „Fortsetzen“ drücken."
        return ActionResult(success=True, output=output, detail={"blocking": state})

    async def _resume(self) -> ActionResult:
        client = await self._service.client()
        state = await client.set_blocking(True, timer_s=None)
        if state["enabled"] is not True:
            return ActionResult(success=False, error="Pi-hole hat die Blockierung nicht wieder eingeschaltet.", detail={"blocking": state})
        return ActionResult(success=True, output="Pi-hole blockiert wieder.", detail={"blocking": state})

    async def _gravity(self) -> ActionResult:
        client = await self._service.client()
        tail = await client.update_gravity()
        output = "Pi-hole hat die Sperrlisten neu geladen."
        return ActionResult(success=True, output=f"{output}\n\n{tail}" if tail else output)

    async def _domain(self, list_type: str, domain: str) -> ActionResult:
        client = await self._service.client()
        errors = await client.add_domain(list_type, domain, comment=DOMAIN_COMMENT)
        word = "erlaubt" if list_type == "allow" else "gesperrt"
        if errors:
            if all("unique" in e.lower() for e in errors):
                return ActionResult(success=True, output=f"„{domain}“ stand schon auf der Liste ({word}).", detail={"domain": domain, "changed": False})
            return ActionResult(success=False, error=f"Pi-hole hat „{domain}“ nicht übernommen: {'; '.join(errors)}", detail={"domain": domain})
        return ActionResult(success=True, output=f"„{domain}“ ist jetzt in Pi-hole {word}.", detail={"domain": domain, "changed": True})

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        try:
            if req.action_type == PIHOLE_PAUSE:
                return DryRunReport(would_change=True, summary=f"Würde die Pi-hole-Blockierung für {pause_text(validate_pause(req.payload))} pausieren.")
            if req.action_type == PIHOLE_RESUME:
                validate_empty(req.payload)
                return DryRunReport(would_change=True, summary="Würde die Pi-hole-Blockierung wieder einschalten.")
            if req.action_type == GRAVITY_UPDATE:
                validate_empty(req.payload)
                return DryRunReport(would_change=True, summary="Würde die Pi-hole-Sperrlisten neu laden.")
            if req.action_type in DOMAIN_ACTIONS:
                word = "erlauben" if DOMAIN_ACTIONS[req.action_type] == "allow" else "sperren"
                return DryRunReport(would_change=True, summary=f"Würde „{validate_domain(req.payload)}“ in Pi-hole {word}.")
        except PayloadError as exc:
            return DryRunReport(would_change=False, summary=str(exc))
        return None
