"""Prüft, ob eine offene Verbindung (Terminal-Shell, Live-Aktualisierung) noch erlaubt ist, gelesen wird neu aus
der Datenbank."""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..models import RefreshToken, User
from .auth import user_has_permission

_MAX_HOPS = 50
"""So viele Erneuerungen verfolgt eine Prüfung höchstens; der Stand wird gemerkt, es kommen kaum neue dazu."""

LOGGED_OUT_MESSAGE = "Du wurdest abgemeldet."


def credential_stamp(user: User) -> str:
    """Kurzer Fingerabdruck des Passworts, ändert sich mit ihm; nicht umkehrbar, nur im Speicher gehalten."""
    return hashlib.sha256(user.password_hash.encode("utf-8")).hexdigest()[:24]


class SessionWatch:
    """Stand einer offenen Verbindung beim Aufbau samt Prüfung; `login_id` (`sid` im Zugangs-Token) bindet sie an
    genau eine Anmeldung."""

    def __init__(self, *, user_id: str, stamp: str, login_id: str | None) -> None:
        self.user_id = user_id
        self.stamp = stamp
        self.login_id = login_id

    async def ended_reason(self, session: AsyncSession, *, permission: str | None = None) -> str | None:
        """`None`, solange alles stimmt, sonst der Grund (Klartext für die Anzeige), warum die Verbindung enden
        muss."""
        user = await session.get(User, self.user_id)
        if user is None or not user.is_active:
            return "Das Konto ist nicht mehr aktiv."
        if permission is not None and not user_has_permission(user, permission):
            return "Dir wurde die Berechtigung dafür entzogen."
        if self.stamp and credential_stamp(user) != self.stamp:
            return "Das Passwort wurde geändert."
        if self.login_id is None:
            if not await _any_live_login(session, self.user_id):
                return LOGGED_OUT_MESSAGE
            return None
        head = await _live_login_head(session, self.user_id, self.login_id)
        if head is None:
            return LOGGED_OUT_MESSAGE
        # Eine Erneuerung legt eine neue Zeile an (`replaced_by_id`); ab jetzt dieser folgen.
        self.login_id = head
        return None


async def _any_live_login(session: AsyncSession, user_id: str) -> bool:
    live = (
        await session.execute(
            select(RefreshToken.id)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > utcnow(),
            )
            .limit(1)
        )
    ).first()
    return live is not None


async def _live_login_head(session: AsyncSession, user_id: str, login_id: str) -> str | None:
    now = utcnow()
    current = login_id
    for _ in range(_MAX_HOPS):
        row = await session.get(RefreshToken, current)
        if row is None or row.user_id != user_id:
            return None
        if row.revoked_at is None:
            return current if row.expires_at > now else None
        if row.replaced_by_id is None:
            return None
        current = row.replaced_by_id
    return None
