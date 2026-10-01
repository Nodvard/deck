"""Audit-Log-Kern: Zeilen schreiben, NDJSON-Zeilen serialisieren
(docs/03-DATA-MODEL.md §4).

Reine Bausteine ohne Filter-/Retentionslogik -- das liegt in `services/audit.py`,
analog zum security/vault-Schnitt aus WP-1: hier nur das, was JEDER Aufrufer (auch
`core.vault.vault_use`, das denselben Layer nicht hoeher importieren soll) direkt
braucht.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditEntry

VALID_OUTCOMES = frozenset({"success", "failure", "denied", "proposed"})


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
    """Die EINZIGE Stelle, die eine `AuditEntry`-Zeile anlegt -- append-only, kein
    UPDATE/DELETE hier (docs/03 §4). `services.audit.purge_expired()` ist die
    dokumentierte Ausnahme fuer DELETE (der Retention-Job)."""
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"Ungültiger outcome '{outcome}' (erlaubt: {sorted(VALID_OUTCOMES)})")

    entry = AuditEntry(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        outcome=outcome,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        detail=detail or {},
        correlation_id=correlation_id,
        ip=ip,
        user_agent=user_agent,
    )
    session.add(entry)
    await session.flush()
    return entry


def to_ndjson_line(entry: AuditEntry) -> str:
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
        "detail": entry.detail,
        "correlation_id": entry.correlation_id,
        "ip": entry.ip,
        "user_agent": entry.user_agent,
    }
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))
