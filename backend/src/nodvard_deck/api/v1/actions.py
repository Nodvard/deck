"""Das Aktions-Gate-Journal -- docs/04-API.md §"Aktionen -- der Bestaetigungs-Workflow".

Nur die GENERISCHEN Endpunkte (Liste, Details, Entscheiden). Der host-gebundene
Katalog+Ausloeser (`GET/POST /hosts/{id}/actions...`) liegt in `api/v1/hosts.py`, weil
er zusaetzlich einen Host laedt -- beide rufen fuer die eigentliche Entscheidung
`core.gate` auf, nicht sich gegenseitig.

**Permission-Modell, nicht explizit in docs/03 §1 ausbuchstabiert (Gap-Fill-
Entscheidung):** Lesen (`GET`) braucht `hosts.read` -- ein Vorschlag ist immer
host-bezogen, wer Hosts sehen darf, darf auch die Vorschlaege dazu sehen. Entscheiden
(`approve`/`reject`/`dismiss`) braucht `actions.approve:<risk-der-Aktion>` (docs/03 §1
nennt genau dieses Schema als Beispiel) -- dynamisch pro Zeile geprueft, weil die
statische `require_permission()`-Dependency den Scope nicht kennen kann, bevor die
Zeile geladen ist. Reject und Dismiss teilen sich denselben Massstab wie Approve: alle
drei sind derselbe Bestaetigungs-Workflow (docs/04-API.md listet sie als Geschwister),
und wer eine `high`-Risiko-Aktion sehen darf, soll sie auch ablehnen duerfen, ohne
zwingend einen Admin zu brauchen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core import gate as gate_service
from ...models import Action, User
from ...services.actor_labels import ActorLabels, load_actor_labels, raw_actor
from ...services.auth import user_has_permission
from ..deps import CurrentUser, SessionDep, require_permission

router = APIRouter(
    prefix="/actions",
    tags=["actions"],
    dependencies=[Depends(require_permission("hosts.read"))],
)


class ActionOut(BaseModel):
    id: str
    ext_id: str
    action_type: str
    host_id: str | None
    payload: dict[str, Any]
    risk: str
    status: str
    proposed_by_type: str
    proposed_by_id: str
    proposed_by_label: str
    """Lesbarer Name zu proposed_by_type/_id (Nutzername, Name der Erweiterung, "KI" ...);
    nicht aufloesbar -> der rohe Wert "art/kennung"."""
    reason: str
    gate_decision: dict[str, Any]
    approved_by_user_id: str | None
    approved_by_label: str | None
    """Benutzername zu approved_by_user_id; None solange niemand entschieden hat."""
    approved_at: datetime | None
    executed_at: datetime | None
    finished_at: datetime | None
    result: dict[str, Any]
    correlation_id: str | None
    idempotency_key: str | None
    expires_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, a: Action, labels: ActorLabels | None = None) -> "ActionOut":
        """`labels` kommt aus `load_actor_labels()`; ohne sie stehen die rohen Werte da."""
        return cls(
            id=a.id, ext_id=a.ext_id, action_type=a.action_type, host_id=a.host_id,
            payload=a.payload, risk=a.risk, status=a.status,
            proposed_by_type=a.proposed_by_type, proposed_by_id=a.proposed_by_id,
            proposed_by_label=(
                labels.proposed_by(a) if labels else raw_actor(a.proposed_by_type, a.proposed_by_id)
            ),
            reason=a.reason, gate_decision=a.gate_decision,
            approved_by_user_id=a.approved_by_user_id,
            approved_by_label=labels.approved_by(a) if labels else a.approved_by_user_id,
            approved_at=a.approved_at,
            executed_at=a.executed_at, finished_at=a.finished_at, result=a.result,
            correlation_id=a.correlation_id, idempotency_key=a.idempotency_key,
            expires_at=a.expires_at, created_at=a.created_at,
        )


async def action_out(session: AsyncSession, action: Action, viewer: User) -> ActionOut:
    """Eine Aktion samt aufgeloesten Namen (Vorschlagender/Entscheider). Namen anderer
    Nutzer bekommt nur, wer sie sehen darf (`actor_labels.may_see_other_users`)."""
    return ActionOut.from_model(action, await load_actor_labels(session, [action], viewer=viewer))


async def action_outs(session: AsyncSession, actions: list[Action], viewer: User) -> list[ActionOut]:
    """Viele Aktionen, die Namen mit je EINER Abfrage fuer Nutzer und Erweiterungen."""
    labels = await load_actor_labels(session, actions, viewer=viewer)
    return [ActionOut.from_model(a, labels) for a in actions]


class RejectIn(BaseModel):
    reason: str = Field(min_length=1)


def _require_decision_permission(user, action: Action) -> None:
    if not user_has_permission(user, f"actions.approve:{action.risk}"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Berechtigung 'actions.approve:{action.risk}' fehlt.",
        )


@router.get("")
async def list_actions(
    session: SessionDep,
    user: CurrentUser,
    status_: str | None = None,
    action_type: str | None = None,
    host_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ActionOut]:
    stmt = select(Action).order_by(Action.created_at.desc())
    if status_ is not None:
        stmt = stmt.where(Action.status == status_)
    if action_type is not None:
        stmt = stmt.where(Action.action_type == action_type)
    if host_id is not None:
        stmt = stmt.where(Action.host_id == host_id)
    stmt = stmt.limit(min(limit, 1000)).offset(max(offset, 0))
    rows = (await session.execute(stmt)).scalars().all()
    return await action_outs(session, list(rows), user)


@router.get("/{action_id}")
async def get_action(action_id: str, session: SessionDep, user: CurrentUser) -> ActionOut:
    action = await session.get(Action, action_id)
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Aktion.")
    return await action_out(session, action, user)


RUNNING_STATUSES = frozenset({"approved", "executing"})


def mark_running(response: Response, action: Action) -> None:
    """Laeuft die Aktion nach der Wartezeit noch, antwortet die API mit 202
    statt 200 -- das Ergebnis gibt es danach unter GET /actions/{id}."""
    if action.status in RUNNING_STATUSES:
        response.status_code = status.HTTP_202_ACCEPTED
        response.headers["Location"] = f"/api/v1/actions/{action.id}"
        response.headers["Retry-After"] = "3"


@router.post("/{action_id}/approve")
async def approve_action(
    action_id: str,
    session: SessionDep,
    user: CurrentUser,
    response: Response,
    wait: float | None = Query(
        None, ge=0, le=gate_service.WAIT_S,
        description="Sekunden, die höchstens auf das Ergebnis gewartet wird (Standard 20, 0 = gar nicht).",
    ),
) -> ActionOut:
    """Bestaetigt und startet die Ausfuehrung im Hintergrund. Fertig
    innerhalb von `wait` Sekunden: 200 mit dem Ergebnis wie bisher. Sonst 202 mit
    Status 'executing' -- die Oberflaeche fragt dann GET /actions/{id} ab."""
    action = await session.get(Action, action_id)
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Aktion.")
    _require_decision_permission(user, action)

    wait_s = gate_service.WAIT_S if wait is None else min(wait, gate_service.WAIT_S)
    result, moved = await gate_service.approve(session, action_id, user_id=user.id, wait_s=wait_s)
    assert result is not None  # oben bereits auf Existenz geprueft
    if not moved:
        if result.status == "expired":
            # approve() hat den abgelaufenen Vorschlag gerade auf 'expired'
            # gesetzt. Ohne Commit rollt get_session() das beim 409 wieder zurueck, und
            # die Zeile stuende weiter als 'Wartet' in Liste und Zaehler.
            await session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Vorschlag ist nicht mehr im Zustand 'proposed' (jetzt: '{result.status}').",
        )
    mark_running(response, result)
    return await action_out(session, result, user)


@router.post("/{action_id}/reject")
async def reject_action(action_id: str, payload: RejectIn, session: SessionDep, user: CurrentUser) -> ActionOut:
    action = await session.get(Action, action_id)
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Aktion.")
    _require_decision_permission(user, action)

    result, moved = await gate_service.reject(session, action_id, user_id=user.id, reason=payload.reason)
    assert result is not None
    if not moved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Vorschlag ist nicht mehr im Zustand 'proposed' (jetzt: '{result.status}').",
        )
    return await action_out(session, result, user)


@router.post("/{action_id}/dismiss")
async def dismiss_action(action_id: str, session: SessionDep, user: CurrentUser) -> ActionOut:
    action = await session.get(Action, action_id)
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Aktion.")
    _require_decision_permission(user, action)

    result, moved = await gate_service.dismiss(session, action_id, user_id=user.id)
    assert result is not None
    if not moved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Vorschlag ist nicht mehr im Zustand 'proposed' (jetzt: '{result.status}').",
        )
    return await action_out(session, result, user)
