"""Das Notification-Center -- docs/04-API.md.

Benachrichtigungs-Center: die Endpunkte existierten seit WP-6, aber KEINE Seite
im Frontend rief sie auf -- Meldungen (z. B. die Lageberichte von Nodvard Shield) landeten in der
Datenbank und niemand sah sie. Dazu `GET /unread-count` (fuer den Zaehler im Menue,
ohne die ganze Liste zu laden) und `POST /read-all`.

Nur Lesen + Als-gelesen-Markieren. Der Lesestatus gilt fuer alle Nutzer gemeinsam (eine Spalte
`read_at`), deshalb verlangt das Markieren `notifications.write` (Operator und Admin); wer nur
lesen darf, kann Meldungen anderer nicht ausblenden. Geschrieben wird ausschliesslich ueber
`services.notifications.send()` (derzeit aus `ext.context.NotifyHandle.send()`) --
kein Endpunkt hier legt selbst eine Zeile an, dasselbe Prinzip wie `api/v1/audit.py`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ...models import Notification
from ...services import notifications as notifications_service
from ..deps import SessionDep, require_permission

router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    dependencies=[Depends(require_permission("notifications.read"))],
)


class NotificationOut(BaseModel):
    id: str
    ts: datetime
    severity: str
    title: str
    body: str
    source_ext_id: str | None
    correlation_id: str | None
    read_at: datetime | None
    payload: dict[str, Any]

    @classmethod
    def from_model(cls, n: Notification) -> "NotificationOut":
        return cls(
            id=n.id, ts=n.ts, severity=n.severity, title=n.title, body=n.body,
            source_ext_id=n.source_ext_id, correlation_id=n.correlation_id,
            read_at=n.read_at, payload=n.payload,
        )


class MarkReadIn(BaseModel):
    ids: list[str] = Field(min_length=1)


@router.get("")
async def list_notifications(
    session: SessionDep, unread: bool | None = None, limit: int = 100, offset: int = 0
) -> list[NotificationOut]:
    rows = await notifications_service.list_notifications(session, unread=unread, limit=limit, offset=offset)
    return [NotificationOut.from_model(n) for n in rows]


@router.get("/unread-count")
async def get_unread_count(session: SessionDep) -> dict[str, int]:
    """Muss VOR `/{notification_id}` stehen -- sonst faengt jene Route "unread-count"
    als ID ab."""
    return {"unread": await notifications_service.unread_count(session)}


@router.post("/read-all", dependencies=[Depends(require_permission("notifications.write"))])
async def mark_all_read(session: SessionDep) -> dict[str, int]:
    return {"marked": await notifications_service.mark_all_read(session)}


@router.get("/{notification_id}")
async def get_notification(notification_id: str, session: SessionDep) -> NotificationOut:
    row = await notifications_service.get_notification(session, notification_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Benachrichtigung.")
    return NotificationOut.from_model(row)


@router.post("/read", dependencies=[Depends(require_permission("notifications.write"))])
async def mark_read(payload: MarkReadIn, session: SessionDep) -> dict[str, int]:
    count = await notifications_service.mark_read(session, payload.ids)
    return {"marked": count}
