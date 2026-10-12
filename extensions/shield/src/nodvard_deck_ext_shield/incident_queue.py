"""Dauerhafte Warteschlange (`ext_nexus_soc_incident_queue`), die den Batch aus `incidents.py` über einen Neustart
rettet: Ein Vorfall steht ab dem Erkennen als "offen" darin, `load_open()` holt beim Start den Rest."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, or_, select, update

from .incidents import Incident
from .models import IncidentQueueRecord

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

# Scheitert die Verarbeitung, zählt `attempts` hoch. Nach MAX_ATTEMPTS Versuchen oder wenn der Eintrag älter als
# MAX_AGE_S ist, wird er "fehlgeschlagen" und nicht mehr mitgeschleppt.
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
        """Hält den angelegten Aktionsvorschlag samt KI-Text fest, damit ein erneuter Versuch keinen zweiten anlegt."""
        if not incident_ids:
            return
        async with self._ctx.db.session() as session:
            await session.execute(
                update(IncidentQueueRecord)
                .where(IncidentQueueRecord.id.in_(incident_ids))
                .values(action_id=action_id, ai_summary=ai_summary)
            )

    async def mark_proposal_started(self, incident_ids: list[str]) -> None:
        """Vermerkt vor dem Anlegen eines Aktionsvorschlags, dass er begonnen wird."""
        if not incident_ids:
            return
        async with self._ctx.db.session() as session:
            await session.execute(
                update(IncidentQueueRecord).where(IncidentQueueRecord.id.in_(incident_ids)).values(proposal_started=True)
            )

    async def bump_attempts(self, incident_ids: list[str]) -> dict[str, int]:
        """Zählt einen fehlgeschlagenen Versuch für die noch offenen Einträge hoch und gibt deren neuen Stand
        zurück (bereits verarbeitete fehlen im Ergebnis)."""
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
        """Beim Start: offene Einträge, die zu alt sind oder ihre Versuche verbraucht haben, als "fehlgeschlagen"
        markieren."""
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
