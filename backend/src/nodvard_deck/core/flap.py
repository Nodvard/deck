"""Anti-Flapping im Gate: Dieselbe Aktion (Fingerprint) darf im Zeitfenster nur begrenzt oft laufen, für jeden Aufrufer.
Der Fingerprint ist ein SHA-256 über (Host, Aktionstyp, normalisierte Payload) und erfasst auch strukturierte Payloads,
nicht nur wortgleiche Befehle."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..models import ActionFlapHistory

MAX_COUNT = 3
WINDOW_SECONDS = 900.0


def fingerprint(host_id: str | None, action_type: str, payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    raw = f"{host_id or ''}::{action_type}::{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def check_and_record(
    session: AsyncSession, *, host_id: str | None, action_type: str, payload: dict[str, Any]
) -> tuple[str, bool]:
    """Gibt `(fingerprint, blocked)` zurück. Gezählt werden nicht geblockte Versuche im Zeitfenster; ist das Limit
    erreicht, wird dieser Versuch als `blocked` aufgezeichnet und zählt selbst nicht als erlaubter Versuch."""
    fp = fingerprint(host_id, action_type, payload)
    since = utcnow() - timedelta(seconds=WINDOW_SECONDS)
    result = await session.execute(
        select(ActionFlapHistory).where(
            ActionFlapHistory.fingerprint == fp,
            ActionFlapHistory.ts >= since,
            ActionFlapHistory.blocked.is_(False),
        )
    )
    count = len(result.scalars().all())
    blocked = count >= MAX_COUNT
    session.add(ActionFlapHistory(host_id=host_id, fingerprint=fp, blocked=blocked))
    await session.flush()
    return fp, blocked
