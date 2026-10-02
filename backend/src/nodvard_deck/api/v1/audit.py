"""Audit-Abfrage und -Export -- docs/04-API.md, docs/03-DATA-MODEL.md §4.

Server-Ausgabe in den Details (Ergebnisse ausgefuehrter Aktionen, auch aus alten
Eintraegen) sieht nur, wer `hosts.execute` hat (`core/action_output.py`).

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

from ...core.action_output import OUTPUT_PERMISSION, hide_audit_detail
from ...services import audit as audit_service
from ...services.auth import user_has_permission
from ..deps import CurrentUser, SessionDep, require_permission

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
    output_hidden: bool = False
    """`true`, wenn Ausgabe oder ein Befehl aus `detail` weggelassen wurde (der Abrufende hat kein
    `hosts.execute`)."""
    correlation_id: str | None
    ip: str | None
    user_agent: str | None

    @classmethod
    def from_model(cls, entry, *, show_output: bool) -> "AuditEntryOut":  # noqa: ANN001 - AuditEntry-ORM-Objekt
        """Ohne `show_output` fehlen Server-Ausgabe und Befehle in `detail` (auch in alten Eintraegen)."""
        detail, output_hidden = (entry.detail, False) if show_output else hide_audit_detail(entry.action, entry.detail)
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
            detail=detail,
            output_hidden=output_hidden,
            correlation_id=entry.correlation_id,
            ip=entry.ip,
            user_agent=entry.user_agent,
        )


async def _for_viewer(session, user, entries):
    """Eingetippte Anmeldenamen sieht niemand: bei neuen Eintraegen stehen sie gar nicht erst
    im Protokoll, bei alten hat sie die Migration entfernt (und die Antwort schwaerzt sie zur
    Sicherheit trotzdem). Ihre Kennungen und Laengen sieht nur der Owner; fuer alle anderen,
    auch Admins, werden sie geschwaerzt. Sonst kaeme ein Admin so an das Owner-Konto, das
    ihm sonst verschlossen bleibt."""
    return await audit_service.hide_typed_names(session, entries, keep_traces=user.is_owner)


@router.get("")
async def list_audit(
    session: SessionDep,
    user: CurrentUser,
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
    entries = await _for_viewer(session, user, entries)
    show_output = user_has_permission(user, OUTPUT_PERMISSION)
    return [AuditEntryOut.from_model(e, show_output=show_output) for e in entries]


@router.get("/export")
async def export_audit(
    session: SessionDep,
    user: CurrentUser,
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
    body = audit_service.to_ndjson(
        await _for_viewer(session, user, entries), hide_output=not user_has_permission(user, OUTPUT_PERMISSION)
    )
    return Response(content=body, media_type="application/x-ndjson")
