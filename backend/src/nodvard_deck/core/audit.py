"""Audit-Log-Kern: Zeilen schreiben und als NDJSON serialisieren.

Filter und Aufbewahrung liegen in `services/audit.py`; hier steht nur, was jeder Aufrufer braucht (auch `core.vault`)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditEntry
from .action_output import hide_audit_detail

VALID_OUTCOMES = frozenset({"success", "failure", "denied", "proposed"})

# Längen der Textspalten in `models.security.AuditEntry`. SQLite prüft sie nicht; ohne Kürzung ließe sich das Protokoll
# über lange Eingaben (z. B. ein Benutzername beim Login ohne Anmeldung) aufblähen. `reason` fehlt bewusst: Die Spalte
# hat keine Grenze, Begründungen bleiben vollständig (beim Login nur feste Sätze).
_MAX_LEN = {
    "actor_type": 16,
    "actor_id": 128,
    "action": 128,
    "target_type": 64,
    "target_id": 128,
    "ip": 64,
    "user_agent": 255,
}


def _clip(field: str, value: str | None) -> str | None:
    limit = _MAX_LEN[field]
    if value is None or len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


async def write_entry(
    session: AsyncSession,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    outcome: str,
    target_type: str | None = None,
    target_id: str | None = None,
    reason: str | None = None,
    detail: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuditEntry:
    """Die einzige Stelle, die eine `AuditEntry`-Zeile anlegt: append-only, kein UPDATE oder DELETE
    (Ausnahme ist die Aufbewahrung in `services.audit.purge_expired()`)."""
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"Ungültiger outcome '{outcome}' (erlaubt: {sorted(VALID_OUTCOMES)})")

    entry = AuditEntry(
        actor_type=_clip("actor_type", actor_type),
        actor_id=_clip("actor_id", actor_id),
        action=_clip("action", action),
        outcome=outcome,
        target_type=_clip("target_type", target_type),
        target_id=_clip("target_id", target_id),
        reason=reason,
        detail=detail or {},
        correlation_id=correlation_id,
        ip=_clip("ip", ip),
        user_agent=_clip("user_agent", user_agent),
    )
    session.add(entry)
    await session.flush()
    return entry


def to_ndjson_line(entry: AuditEntry, *, hide_output: bool = False) -> str:
    """`hide_output=True` lässt Server-Ausgabe in den Details weg (siehe `action_output`)."""
    detail = hide_audit_detail(entry.action, entry.detail)[0] if hide_output else entry.detail
    row = {
        "id": entry.id,
        "ts": entry.ts.isoformat(),
        "actor_type": entry.actor_type,
        "actor_id": entry.actor_id,
        "action": entry.action,
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "outcome": entry.outcome,
        "reason": entry.reason,
        "detail": detail,
        "correlation_id": entry.correlation_id,
        "ip": entry.ip,
        "user_agent": entry.user_agent,
    }
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))
