"""Prueft, ob eine schon laufende Verbindung (Terminal-Shell, Live-Aktualisierung) noch erlaubt ist.

Anmeldung und Rechte werden bei normalen Anfragen jedes Mal neu gelesen, eine offene
Verbindung kennt sie aber nur vom Aufbau. Ohne diese Pruefung liefe eine Shell weiter,
nachdem das Konto deaktiviert, die Rolle entzogen, das Passwort geaendert oder die Person
abgemeldet wurde, und die Live-Aktualisierung lieferte weiter Ereignisse. Die Pruefung liest nur die Datenbank: sie greift deshalb auch bei
Aenderungen aus einem anderen Prozess (Notfall-Befehl auf dem Server)."""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..models import RefreshToken, User
from .auth import user_has_permission

_MAX_HOPS = 50
"""So viele Erneuerungen am Stueck verfolgt eine Pruefung hoechstens. Der verfolgte Stand wird
gemerkt, zwischen zwei Pruefungen (15 s) kommen also nur sehr wenige dazu."""

LOGGED_OUT_MESSAGE = "Du wurdest abgemeldet."


def credential_stamp(user: User) -> str:
    """Kurzer Fingerabdruck des Passworts: aendert es sich, ist er ein anderer. Nicht umkehrbar
    und nur im Speicher gehalten."""
    return hashlib.sha256(user.password_hash.encode("utf-8")).hexdigest()[:24]


class SessionWatch:
    """Was eine offene Verbindung beim Aufbau ueber ihre Anmeldung weiss, und die Pruefung dazu.

    `login_id` ist die Anmeldung (Refresh-Token-Zeile, `sid` im Zugangs-Token), aus der das
    Ticket geholt wurde. Die Verbindung haengt an GENAU dieser Anmeldung: wer sich dort abmeldet
    oder wessen Anmeldung widerrufen wird, verliert die Shell, auch wenn er auf einem anderen
    Geraet noch angemeldet ist. Beim Erneuern entsteht eine neue Zeile (`replaced_by_id`), der
    die Pruefung folgt und die sie sich merkt. Aeltere Zugangs-Tokens ohne `sid`: dann reicht
    irgendeine gueltige Anmeldung des Kontos."""

    def __init__(self, *, user_id: str, stamp: str, login_id: str | None) -> None:
        self.user_id = user_id
        self.stamp = stamp
        self.login_id = login_id

    async def ended_reason(self, session: AsyncSession, *, permission: str | None = None) -> str | None:
        """`None`, solange alles stimmt; sonst der Grund (Klartext fuer die Anzeige), warum die
        Verbindung enden muss: Konto weg oder deaktiviert, Berechtigung entzogen, Passwort
        geaendert, oder die Anmeldung gilt nicht mehr (abgemeldet, widerrufen, abgelaufen).

        Ohne `permission` wird keine bestimmte Berechtigung verlangt (die Live-Aktualisierung
        filtert ihre Ereignisse je Berechtigung selbst)."""
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
    """Die noch gueltige Zeile am Ende der Erneuerungskette ab `login_id`, sonst `None` (per
    Abmelden oder Passwortwechsel widerrufen, abgelaufen, fremd oder unbekannt)."""
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
