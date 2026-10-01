"""Generalisiertes Anti-Flapping -- docs/03-DATA-MODEL.md §5.

1:1 aus dem Vorgaengersystem uebernommen: `RESTART_HISTORY`/`MAX_RESTARTS`(=3)/
`FLAP_WINDOW_SECONDS`(=900) -- dort nur fuer Docker-Restarts und nur im synchronen
`execute_remote`-Pfad, den `check_and_restart()` nie tatsaechlich benutzt hat (die
autonome KI-Remediation konnte das Limit also stillschweigend umgehen). Hier gilt es
fuer JEDEN Aktionstyp und JEDEN Aufrufer, weil es im Kern-Gate sitzt -- also auch fuer
die, die es bisher umgangen haben.

Fingerprint statt Rohstring-Schluessel (der Bestand nutzte `host + "::" + cmd[:80]`):
ein SHA-256 ueber (host, action_type, normalisierter Payload) erfasst auch
strukturierte Payloads, nicht nur wortgleiche Shell-Kommandos.
"""

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
    """Zaehlt bisherige, NICHT geblockte Versuche desselben Fingerprints im
    Zeitfenster; ist das Limit erreicht, wird DIESER Versuch als `blocked=True`
    aufgezeichnet (er zaehlt fuer sich selbst nicht als weiterer "erlaubter"
    Versuch) und `True` zurueckgegeben. Gibt `(fingerprint, blocked)` zurueck."""
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
