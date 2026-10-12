"""Das Notification-Center -- docs/04-API.md.

Benachrichtigungs-Center: die Endpunkte existierten seit WP-6, aber KEINE Seite
im Frontend rief sie auf -- Meldungen (z. B. die Lageberichte von Nodvard Shield) landeten in der
Datenbank und niemand sah sie. Dazu `GET /unread-count` (fuer den Zaehler im Menue,
ohne die ganze Liste zu laden) und `POST /read-all`.

Lesen, Als-gelesen-Markieren und Loeschen. Der Lesestatus gilt fuer alle Nutzer gemeinsam (eine Spalte
`read_at`), deshalb verlangt das Markieren `notifications.write` (Operator und Admin); wer nur
lesen darf, kann Meldungen anderer nicht ausblenden. Dasselbe gilt fuers Loeschen (`DELETE /{id}`,
`POST /delete-read`): es trifft alle Nutzer und steht mit `notifications.deleted` im Protokoll (einzeln mit
Kennung und Titel, gesammelt mit der Anzahl; scheitert das Sammel-Loeschen mittendrin, steht der bisherige Stand
als `failure` mit `interrupted` dort). Zusaetzlich loescht der Kern-Job `notifications-retention` jede
Nacht alte Meldungen (`services.notification_retention`, Einstellung `notifications.retention_days`).
Geschrieben wird ausschliesslich ueber
`services.notifications.send()` (derzeit aus `ext.context.NotifyHandle.send()`) --
kein Endpunkt hier legt selbst eine Zeile an, dasselbe Prinzip wie `api/v1/audit.py`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Notification, User
from ...services import audit as audit_service
from ...services import notifications as notifications_service
from ..deps import CurrentUser, SessionDep, require_permission

log = logging.getLogger("nodvard_deck.notifications")

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


async def _audit_deleted(session: AsyncSession, user: User, *, target_id: str | None, detail: dict[str, Any]) -> None:
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="notifications.deleted", outcome="success",
        target_type="notification", target_id=target_id, detail=detail,
    )


async def _audit_interrupted_delete(session: AsyncSession, actor_id: str, deleted: int) -> None:
    """Das Sammel-Loeschen ist nach `deleted` geloeschten Meldungen gescheitert: auch das steht im Protokoll (der
    eigentliche Fehler geht danach weiter). Ein Fehler hier ueberdeckt ihn nie. Die Kennung kommt als Text herein:
    nach dem `rollback()` waeren die Felder eines geladenen Nutzers verfallen und nur noch per Nachladen lesbar."""
    try:
        await session.rollback()  # das gescheiterte Haeppchen verwerfen, die geloeschten sind schon committet
        await audit_service.log(
            session, actor_type="user", actor_id=actor_id, action="notifications.deleted", outcome="failure",
            target_type="notification", target_id=None, detail={"count": deleted, "interrupted": True},
        )
        await session.commit()
    except Exception:  # nie den urspruenglichen Fehler ueberdecken
        log.exception("Protokolleintrag fuer das unterbrochene Loeschen gelesener Meldungen ging nicht")


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


@router.post("/delete-read", dependencies=[Depends(require_permission("notifications.write"))])
async def delete_read_notifications(session: SessionDep, user: CurrentUser) -> dict[str, int]:
    """Loescht alle gelesenen Meldungen (ungelesene bleiben) und meldet, wie viele es waren. Geloescht wird in
    Haeppchen mit Commit dazwischen; scheitert ein spaeteres, steht der Stand bis dahin trotzdem im Protokoll
    (`outcome` `failure`, `{"count": n, "interrupted": true}`) und der Fehler geht weiter."""
    progress = notifications_service.DeleteProgress()
    try:
        deleted = await notifications_service.delete_read(session, progress=progress)
    except (Exception, asyncio.CancelledError):
        if progress.deleted:
            await _audit_interrupted_delete(session, user.id, progress.deleted)
        raise
    if deleted:
        await _audit_deleted(session, user, target_id=None, detail={"count": deleted})
    return {"deleted": deleted}


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


@router.delete(
    "/{notification_id}", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("notifications.write"))],
)
async def delete_notification(notification_id: str, session: SessionDep, user: CurrentUser) -> Response:
    title = await notifications_service.delete_notification(session, notification_id)
    if title is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Benachrichtigung.")
    await _audit_deleted(session, user, target_id=notification_id, detail={"title": title})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
