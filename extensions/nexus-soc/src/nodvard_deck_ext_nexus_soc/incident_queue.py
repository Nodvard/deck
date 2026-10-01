"""Dauerhafte Warteschlange fuer Vorfaelle des laufenden Sammelfensters
(`ext_nexus_soc_incident_queue`, models.py).

Der Speicher-Batch aus `incidents.py` bleibt die Arbeitsliste; diese Tabelle ist seine
Absicherung gegen einen Neustart: ein Vorfall wird beim Erkennen sofort als "offen"
geschrieben, nach der Verarbeitung des Batches als "verarbeitet" markiert, und
`load_open()` holt beim Start alles zurueck, was noch nicht gemeldet wurde.

Scheitert die Verarbeitung, zaehlt `attempts` hoch; nach `MAX_ATTEMPTS` Versuchen (oder
wenn ein offener Eintrag aelter als `MAX_AGE_S` ist) wird der Eintrag "fehlgeschlagen"
und nicht mehr mitgeschleppt. `action_id`/`ai_summary` halten einen schon angelegten
Aktionsvorschlag fest, damit ein erneuter Versuch keinen zweiten anlegt.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, or_, select, update

from .incidents import Incident
from .models import IncidentQueueRecord

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

OPEN = "offen"
PROCESSED = "verarbeitet"
FAILED = "fehlgeschlagen"
MAX_ATTEMPTS = 3
MAX_AGE_S = 24 * 3600
KEEP_DAYS = 7


class IncidentQueueRepository:
    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx

    async def add(self, incident: Incident) -> None:
        async with self._ctx.db.session() as session:
            row = await session.get(IncidentQueueRecord, incident.id)
            if row is None:
                row = IncidentQueueRecord(id=incident.id, created_at=datetime.fromtimestamp(incident.created_at, UTC))
                session.add(row)
            row.host_id = incident.host_id
            row.host_name = incident.host_name
            row.target = incident.target
            row.message = incident.message
            row.details = dict(incident.details)
            row.status = OPEN
            row.processed_at = None
            row.attempts = incident.attempts

    async def load_open(self) -> list[Incident]:
        async with self._ctx.db.session() as session:
            rows = (
                await session.execute(
                    select(IncidentQueueRecord)
                    .where(IncidentQueueRecord.status == OPEN)
                    .order_by(IncidentQueueRecord.created_at, IncidentQueueRecord.id)
                )
            ).scalars().all()
            return [
                Incident(
                    id=r.id,
                    host_id=r.host_id or "",
                    host_name=r.host_name,
                    target=r.target,
                    message=r.message,
                    details=dict(r.details or {}),
                    created_at=(r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=UTC)).timestamp(),
                    ai_summary=r.ai_summary,
                    action_id=r.action_id,
                    attempts=r.attempts or 0,
                    proposal_started=bool(r.proposal_started),
                    resumed=True,
                )
                for r in rows
            ]

    async def mark_processed(self, incident_ids: list[str]) -> None:
        await self._finish(incident_ids, PROCESSED)

    async def mark_failed(self, incident_ids: list[str]) -> None:
        await self._finish(incident_ids, FAILED)

    async def _finish(self, incident_ids: list[str], status: str) -> None:
        if not incident_ids:
            return
        async with self._ctx.db.session() as session:
            await session.execute(
                update(IncidentQueueRecord)
                .where(IncidentQueueRecord.id.in_(incident_ids))
                .values(status=status, processed_at=datetime.now(UTC))
            )

    async def save_outcome(self, incident_ids: list[str], *, action_id: str | None, ai_summary: str) -> None:
        """Vermerkt den angelegten Aktionsvorschlag samt KI-Text an den Eintraegen."""
        if not incident_ids:
            return
        async with self._ctx.db.session() as session:
            await session.execute(
                update(IncidentQueueRecord)
                .where(IncidentQueueRecord.id.in_(incident_ids))
                .values(action_id=action_id, ai_summary=ai_summary)
            )

    async def mark_proposal_started(self, incident_ids: list[str]) -> None:
        """Vermerkt VOR dem Anlegen eines Aktionsvorschlags, dass er begonnen wird."""
        if not incident_ids:
            return
        async with self._ctx.db.session() as session:
            await session.execute(
                update(IncidentQueueRecord).where(IncidentQueueRecord.id.in_(incident_ids)).values(proposal_started=True)
            )

    async def bump_attempts(self, incident_ids: list[str]) -> dict[str, int]:
        """Zaehlt einen fehlgeschlagenen Versuch fuer die noch OFFENEN Eintraege hoch und
        gibt deren neuen Stand zurueck (bereits verarbeitete fehlen im Ergebnis)."""
        if not incident_ids:
            return {}
        async with self._ctx.db.session() as session:
            rows = (
                await session.execute(
                    select(IncidentQueueRecord).where(
                        IncidentQueueRecord.id.in_(incident_ids), IncidentQueueRecord.status == OPEN
                    )
                )
            ).scalars().all()
            for row in rows:
                row.attempts = (row.attempts or 0) + 1
            return {row.id: row.attempts for row in rows}

    async def expire_stale(self, *, max_attempts: int = MAX_ATTEMPTS, max_age_s: float = MAX_AGE_S) -> int:
        """Beim Start: offene Eintraege, die zu alt sind oder ihre Versuche verbraucht
        haben, als "fehlgeschlagen" markieren, statt sie ewig mitzuschleppen."""
        cutoff = datetime.now(UTC) - timedelta(seconds=max_age_s)
        async with self._ctx.db.session() as session:
            result = await session.execute(
                update(IncidentQueueRecord)
                .where(
                    IncidentQueueRecord.status == OPEN,
                    or_(IncidentQueueRecord.created_at < cutoff, IncidentQueueRecord.attempts >= max_attempts),
                )
                .values(status=FAILED, processed_at=datetime.now(UTC))
            )
            return int(result.rowcount or 0)

    async def purge_processed(self, *, keep_days: int = KEEP_DAYS) -> None:
        cutoff = datetime.now(UTC) - timedelta(days=keep_days)
        async with self._ctx.db.session() as session:
            await session.execute(
                delete(IncidentQueueRecord).where(
                    IncidentQueueRecord.status.in_((PROCESSED, FAILED)), IncidentQueueRecord.processed_at < cutoff
                )
            )
