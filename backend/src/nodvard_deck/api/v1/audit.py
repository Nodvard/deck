"""Audit-Abfrage und -Export -- docs/04-API.md, docs/03-DATA-MODEL.md §4.

Nur Lesen. Geschrieben wird ausschliesslich serverseitig ueber `core.audit.write_entry`
(derzeit aus `services/auth.py` und `core/vault.py`) -- kein Endpunkt hier legt eine
Zeile an.

**Ehrlich offen gelassen:** `/export` deckelt auf die letzten 1000 Zeilen (derselbe
Deckel wie `services.audit.list_entries`) und streamt nicht -- fuer einen wirklich
vollstaendigen NDJSON-Export eines langlebigen Audit-Logs muesste das paginieren oder
streamen. Fuer diese Runde reicht die Deckelung, um die Grundfunktion (Filter -> NDJSON)
echt zu testen.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from ...services import audit as audit_service
from ..deps import SessionDep, require_permission

router = APIRouter(
    prefix="/audit",
    tags=["audit"],
    dependencies=[Depends(require_permission("audit.read"))],
)


class AuditEntryOut(BaseModel):
    id: str
    ts: datetime
    actor_type: str
    actor_id: str
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    reason: str | None
    detail: dict
    correlation_id: str | None
    ip: str | None
    user_agent: str | None

    @classmethod
    def from_model(cls, entry) -> "AuditEntryOut":  # noqa: ANN001 - AuditEntry-ORM-Objekt
        return cls(
            id=entry.id,
            ts=entry.ts,
            actor_type=entry.actor_type,
            actor_id=entry.actor_id,
            action=entry.action,
            target_type=entry.target_type,
            target_id=entry.target_id,
            outcome=entry.outcome,
            reason=entry.reason,
            detail=entry.detail,
            correlation_id=entry.correlation_id,
            ip=entry.ip,
            user_agent=entry.user_agent,
        )


@router.get("")
async def list_audit(
    session: SessionDep,
    actor_type: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    correlation_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    exclude_action: list[str] = Query(default=[]),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[AuditEntryOut]:
    entries = await audit_service.list_entries(
        session,
        actor_type=actor_type,
        action=action,
        outcome=outcome,
        target_type=target_type,
        target_id=target_id,
        correlation_id=correlation_id,
        since=since,
        until=until,
        exclude_actions=exclude_action,
        limit=limit,
        offset=offset,
    )
    return [AuditEntryOut.from_model(e) for e in entries]


@router.get("/export")
async def export_audit(
    session: SessionDep,
    actor_type: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Response:
    entries = await audit_service.list_entries(
        session,
        actor_type=actor_type,
        action=action,
        outcome=outcome,
        since=since,
        until=until,
        limit=1000,
    )
    body = audit_service.to_ndjson(entries)
    return Response(content=body, media_type="application/x-ndjson")
