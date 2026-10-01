"""Aktionen und das Gate.

Der Mechanismus hinter der Grundregel: KI-Aktionen defaulten auf
"Vorschlag + Bestätigung", volle Autonomie nur per explizitem Schalter.

Die entscheidende Eigenschaft ist strukturell, nicht konfigurativ: *Vorschlagen* und
*Ausführen* sind getrennte Schritte mit einem persistenten Datensatz dazwischen. Im
Bestandssystem parst und führt eine einzige Funktion aus, weshalb ein Bestätigungsmodus
dort nicht sauber nachrüstbar wäre.

Siehe docs/01-ARCHITECTURE.md §4.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .types import Actor, Risk


class ActionStatus(str, enum.Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    EXPIRED = "expired"
    DISMISSED = "dismissed"


class GateOutcome(str, enum.Enum):
    ALLOW = "allow"
    REQUIRE_CONFIRMATION = "require_confirmation"
    DENY = "deny"


class ActionSpec(BaseModel):
    """Was eine Extension als ausführbare Aktion anmeldet.

    Der Kern bietet sie daraufhin generisch an — etwa auf der Host-Seite oder als
    Widget-Aktion — ohne zu wissen, was sie fachlich tut.
    """

    action_type: str            # "shell.exec", "vm.start", "script.run"
    label: str
    description: str | None = None
    icon: str | None = None
    default_risk: Risk = Risk.MEDIUM
    params_schema: dict[str, Any] | None = None   # JSON-Schema
    permissions: list[str] = Field(default_factory=list)
    host_bound: bool = True
    """False: erscheint nicht auf der Server-Seite und ist nicht per
    POST /hosts/{id}/actions/{typ} ausloesbar -- nur ueber ctx.actions.propose der
    Extension (z. B. wenn sie den Befehl selbst aus eigenen, geprueften Feldern baut)."""

    confirm_text: str | None = None
    host_tags: list[str] = Field(default_factory=list)
    """Nur fuer Hosts mit mindestens einem dieser Tags angeboten (leer = jeder Host).
    Ohne das stuende "Container neu starten" auch auf einem Proxmox-Knoten ohne Docker."""

    command_field: str | None = None
    """Welches Payload-Feld die Sperrlisten-Prüfung des Gates untersucht.

    Muss gesetzt sein, wenn der Payload einen auszuführenden Befehl enthält
    (z. B. "command"). Ohne diese Angabe kann das Gate nicht wissen, was es prüfen
    soll — und raten oder den ganzen Payload zu stringifizieren wäre beides falsch.
    """


class ActionRequest(BaseModel):
    """Ein Vorschlag an das Gate. Niemals direkt ausgeführt."""

    action_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    host_ref: str | None = None
    risk: Risk = Risk.MEDIUM
    proposed_by: Actor
    reason: str
    """Pflichtfeld — jede vorgeschlagene Aktion muss begründet sein.

    Im Bestandssystem heisst das Feld `BEGRUENDUNG:` und wird aus der Modellantwort
    geparst. Dass es hier im Datentyp Pflicht ist, macht ein Umgehen unmöglich statt
    unwahrscheinlich.
    """
    correlation_id: str | None = None
    idempotency_key: str | None = None
    expires_in_s: int | None = None

    @field_validator("reason")
    @classmethod
    def _reason_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "ActionRequest.reason ist Pflicht — jede ausgeführte UND jede "
                "abgelehnte Aktion muss begründet im Audit-Log stehen."
            )
        return v.strip()


class GateDecision(BaseModel):
    """Die Antwort des Kerns auf einen Vorschlag."""

    action_id: str
    outcome: GateOutcome
    status: ActionStatus
    rule: str | None = None
    """Welche Regel gegriffen hat — z. B. 'deny_pattern:rm-rf-root', 'flap_limit',
    'autonomy:propose', 'rbac:actions.approve:high'. Landet im Audit und in der UI."""
    detail: str | None = None
    expires_at: datetime | None = None


REQUEST_WAIT_S: float = 20.0
"""So lange wartet eine HTTP-Anfrage hoechstens auf das Ergebnis einer sofort
freigegebenen Aktion. Extension-Routen reichen den Wert als
`ctx.actions.propose(..., wait_s=REQUEST_WAIT_S)` durch; laeuft die Aktion danach noch,
meldet die Entscheidung `executing`, und die Seite verweist auf "Aktionen"."""


class ActionResult(BaseModel):
    success: bool
    exit_code: int | None = None
    output: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class DryRunReport(BaseModel):
    would_change: bool
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
