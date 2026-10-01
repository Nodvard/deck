"""Audit-Abfragen, Retention-Job, NDJSON-Export.

`core.audit.write_entry` bleibt der einzige Schreibpfad; hier nur Lesen/Filtern/
Loeschen jenseits der Retention -- letzteres die eine dokumentierte Ausnahme vom
Append-only-Prinzip (docs/03-DATA-MODEL.md §4).

Die periodische Ausfuehrung von `purge_expired()` uebernimmt der Kern-Scheduler
(`core/scheduler.py`); hier existiert nur die pure, getestete Funktion.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import audit as core_audit
from ..core.audit import write_entry
from ..db import utcnow
from ..models import AuditEntry

__all__ = [
    "RETENTION_KEY", "RETENTION_MAX_DAYS", "RETENTION_MIN_DAYS",
    "effective_retention_days", "log", "list_entries", "purge_expired", "to_ndjson", "write_entry",
]

RETENTION_KEY = "audit.retention_days"
RETENTION_MIN_DAYS = 7
RETENTION_MAX_DAYS = 3650


async def log(session: AsyncSession, **kwargs) -> AuditEntry:  # noqa: ANN003
    """Duenner Alias auf `core.audit.write_entry` -- eigener Name in `services/`, damit
    Aufrufer (z. B. `services/auth.py`) gegen die Orchestrierungsschicht programmieren,
    nicht direkt gegen `core/`."""
    return await core_audit.write_entry(session, **kwargs)


async def list_entries(
    session: AsyncSession,
    *,
    actor_type: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    correlation_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    exclude_actions: list[str] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditEntry]:
    stmt = select(AuditEntry).order_by(AuditEntry.ts.desc(), AuditEntry.id.desc())
    if actor_type is not None:
        stmt = stmt.where(AuditEntry.actor_type == actor_type)
    if action is not None:
        stmt = stmt.where(AuditEntry.action == action)
    if exclude_actions:
        stmt = stmt.where(AuditEntry.action.not_in(exclude_actions))
    if outcome is not None:
        stmt = stmt.where(AuditEntry.outcome == outcome)
    if target_type is not None:
        stmt = stmt.where(AuditEntry.target_type == target_type)
    if target_id is not None:
        stmt = stmt.where(AuditEntry.target_id == target_id)
    if correlation_id is not None:
        stmt = stmt.where(AuditEntry.correlation_id == correlation_id)
    if since is not None:
        stmt = stmt.where(AuditEntry.ts >= since)
    if until is not None:
        stmt = stmt.where(AuditEntry.ts <= until)
    stmt = stmt.limit(min(limit, 1000)).offset(max(offset, 0))

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def effective_retention_days(session: AsyncSession, *, fallback: int) -> int:
    """Aufbewahrungsdauer des Protokolls in Tagen: die Einstellung `audit.retention_days`
    (Einstellungen, System), sonst `fallback` (die Umgebungsvariable). Ein gespeicherter Wert
    ausserhalb von 7 bis 3650 (etwa von Hand in die Tabelle geschrieben) zaehlt als nicht
    gesetzt: lieber die Umgebung nehmen als das Protokoll fast leer zu raeumen."""
    from . import settings as settings_service

    stored = await settings_service.get_global(session, RETENTION_KEY)
    if isinstance(stored, int) and not isinstance(stored, bool) and RETENTION_MIN_DAYS <= stored <= RETENTION_MAX_DAYS:
        return stored
    return fallback


async def purge_expired(session: AsyncSession, *, retention_days: int) -> int:
    """Der Retention-Job (docs/03 §4: "nur der Retention-Job loescht jenseits von
    audit.retention_days"). Gibt die Anzahl geloeschter Zeilen zurueck."""
    cutoff = utcnow() - timedelta(days=retention_days)
    result = await session.execute(delete(AuditEntry).where(AuditEntry.ts < cutoff))
    await session.flush()
    return result.rowcount or 0


def to_ndjson(entries: list[AuditEntry]) -> str:
    if not entries:
        return ""
    return "\n".join(core_audit.to_ndjson_line(e) for e in entries) + "\n"
