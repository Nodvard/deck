"""Gemeinsame FastAPI-Dependencies.

Getrennt von `db.session`/`config`, damit Endpunkte gegen stabile Typaliase
(`SessionDep`, `SettingsDep`, `CurrentUser`) programmieren, waehrend die tatsaechliche
Erzeugung austauschbar bleibt (z. B. fuer Tests, siehe tests/conftest.py).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..core import security
from ..db.session import get_session
from ..models import User
from ..services.auth import user_has_permission

# `scope="function"`: der abschliessende Commit in `get_session()` laeuft, BEVOR die
# Antwort rausgeht. Mit dem FastAPI-Default ("request", seit 0.118) kam er erst danach
# -- scheiterte er (live: volle Platte auf dem Pi), hatte der Browser schon "200 OK"
# und die Aenderung war trotzdem weg. So wird daraus ein ehrlicher Fehler (main.py:
# 507 bei voller Platte). Streaming-Antworten (Datei-Download, Container-Logs) lesen
# waehrend des Streams nichts aus dieser Session.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
SettingsDep = Annotated[Settings, Depends(get_settings)]

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> User:
    """Prueft `Authorization: Bearer <access_token>` -- identisch fuer Web und Android
    (docs/04-API.md §1: "der Kern der 'eine API'-Zusage"). Der Refresh-Token wird HIER
    NIE akzeptiert; `decode_jwt(..., expected_type="access")` weist ihn zurueck, selbst
    wenn er faelschlich als Bearer-Header ankaeme."""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Nicht authentifiziert.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    try:
        payload = security.decode_jwt(
            credentials.credentials,
            secret=settings.get_or_create_jwt_secret(),
            expected_type="access",
        )
    except security.TokenError as exc:
        raise unauthorized from exc

    user = await session.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_session_id(
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> str | None:
    """Kennung der Anmeldung (Refresh-Token-Zeile), zu der das Access-Token gehoert --
    der `sid`-Claim aus `services.auth._issue_tokens()`. `None` fuer aeltere Tokens
    ohne `sid`. Prueft NICHT selbst die Anmeldung, nur zusammen mit `CurrentUser`
    verwenden."""
    if credentials is None:
        return None
    try:
        payload = security.decode_jwt(
            credentials.credentials,
            secret=settings.get_or_create_jwt_secret(),
            expected_type="access",
        )
    except security.TokenError:
        return None
    sid = payload.get("sid")
    return sid if isinstance(sid, str) else None


CurrentSessionId = Annotated[str | None, Depends(get_current_session_id)]


def require_permission(permission: str):  # noqa: ANN201 - gibt eine FastAPI-Dependency zurueck
    """Wiederverwendbare RBAC-Dependency:

        @router.post("/hosts", dependencies=[Depends(require_permission("hosts.write"))])

    `is_owner` umgeht die Pruefung vollstaendig (docs/03-DATA-MODEL.md §1) -- das ist in
    `user_has_permission()` verankert, nicht hier noch einmal dupliziert.
    """

    async def _check(user: CurrentUser) -> User:
        if not user_has_permission(user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Berechtigung '{permission}' fehlt.",
            )
        return user

    return _check


async def require_owner(user: CurrentUser) -> User:
    """Nur der Owner (Inhaber der Installation), unabhaengig von Rollen. Fuer Aktionen, die
    mehr hergeben als jede Berechtigung: eine Sicherung enthaelt Master-Key und Keyring, also
    alle Zugangsdaten, und eine Wiederherstellung koennte einen Admin zum Owner machen. Die
    Rolle `admin` (`*`) reicht deshalb hier ausdruecklich NICHT. Kritische Aktionen verlangen
    zusaetzlich das aktuelle Passwort (`api/v1/auth.confirm_current_password`)."""
    if not user.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Das darf nur der Inhaber dieser Installation (Owner).",
        )
    return user


OwnerUser = Annotated[User, Depends(require_owner)]
