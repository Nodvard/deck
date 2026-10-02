"""Audit-Abfragen, Retention-Job, NDJSON-Export.

`core.audit.write_entry` bleibt der einzige Schreibpfad; hier nur Lesen/Filtern/
Loeschen jenseits der Retention -- letzteres die eine dokumentierte Ausnahme vom
Append-only-Prinzip (docs/03-DATA-MODEL.md §4).

Die periodische Ausfuehrung von `purge_expired()` uebernimmt der Kern-Scheduler
(`core/scheduler.py`); hier existiert nur die pure, getestete Funktion.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import audit as core_audit
from ..core.audit import write_entry
from ..db import utcnow
from ..models import AuditEntry, User

__all__ = [
    "RETENTION_KEY", "RETENTION_MAX_DAYS", "RETENTION_MIN_DAYS",
    "effective_retention_days", "hide_typed_names", "log", "list_entries", "purge_expired", "to_ndjson", "write_entry",
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


LEGACY_UNKNOWN_NAME_REASONS = ("Unbekannter Benutzername.", "Eingabe zu lang.")
"""Gruende, mit denen aeltere `login.failed`-Eintraege den eingetippten Text als Akteur trugen."""


TYPED_NAME_TRACES = ("username_ref", "username_length")
"""Was neue Eintraege ueber einen unbekannten Namen festhalten (`services.auth.unknown_login_audit`)."""


async def hide_typed_names(
    session: AsyncSession, entries: list[AuditEntry], *, keep_traces: bool = False
) -> list[AuditEntry]:
    """Schwaerzt in Antworten die Namen, die jemand bei einer Anmeldung eingetippt hat.

    Neue Eintraege enthalten den Text gar nicht mehr (`services.auth.unknown_login_audit`),
    nur Kennung und Laenge. Die sieht nur, wer `keep_traces` setzt (der Owner): mit der Kennung
    liesse sich eine Vermutung pruefen (selbst als Namen eintippen, Kennungen vergleichen), die
    Laenge verkleinert das Raten. Aeltere Eintraege trugen den Text noch: bei `login.failed` mit
    unbekanntem Namen als `actor_id`, bei `login.locked` als `detail.username`. Die Migration
    `f3a9c6d18e24` entfernt ihn aus der Datenbank; hier bleibt die Schwaerzung als zweite
    Sicherung, und zwar fuer alle, auch den Owner. Dort steht „unbekannt“; die Zeilen in der
    Datenbank bleiben dabei unveraendert (es sind Kopien, nichts wird gespeichert)."""
    candidates = [
        e for e in entries
        if e.action in ("login.failed", "login.locked") and (
            (e.actor_type == "user" and (
                (e.action == "login.failed" and e.reason in LEGACY_UNKNOWN_NAME_REASONS)
                or (e.action == "login.locked" and "username" in (e.detail or {}))
            ))
            or (not keep_traces and any(k in (e.detail or {}) for k in TYPED_NAME_TRACES))
        )
    ]
    if not candidates:
        return entries
    ids = {e.actor_id for e in candidates if e.actor_type == "user"}
    known = set((await session.execute(select(User.id).where(User.id.in_(ids)))).scalars().all()) if ids else set()
    hidden: dict[str, SimpleNamespace] = {}
    for e in candidates:
        copy = SimpleNamespace(**{c.name: getattr(e, c.name) for c in AuditEntry.__table__.columns})
        copy.detail = {
            k: v for k, v in (e.detail or {}).items()
            if k != "username" and (keep_traces or k not in TYPED_NAME_TRACES)
        }
        if e.actor_type == "user" and e.actor_id not in known:
            copy.actor_type, copy.actor_id = "anonymous", "unbekannt"
        hidden[e.id] = copy
    return [hidden.get(e.id, e) for e in entries]  # type: ignore[misc]


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


def to_ndjson(entries: list[AuditEntry], *, hide_output: bool = False) -> str:
    if not entries:
        return ""
    return "\n".join(core_audit.to_ndjson_line(e, hide_output=hide_output) for e in entries) + "\n"
