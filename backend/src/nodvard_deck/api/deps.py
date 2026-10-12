"""Gemeinsame FastAPI-Dependencies mit stabilen Typaliasen (`SessionDep`, `SettingsDep`, `CurrentUser`).

Die Erzeugung bleibt austauschbar, z. B. für Tests (siehe tests/conftest.py).
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

# `scope="function"`: Der Commit in `get_session()` läuft, bevor die Antwort rausgeht. Mit dem Default ("request")
# käme er erst danach, und bei voller Platte sähe der Browser "200 OK", obwohl die Änderung fehlt; so wird daraus
# ein ehrlicher Fehler (507 in main.py). Streaming-Antworten (Download, Logs) lesen dabei nichts aus der Session.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
SettingsDep = Annotated[Settings, Depends(get_settings)]

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> User:
    """Prüft `Authorization: Bearer <access_token>`, für Web und Android gleich (docs/04-API.md §1).

    Ein Refresh-Token wird hier nie akzeptiert: `decode_jwt(..., expected_type="access")` weist ihn zurück."""
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
    """Kennung der Anmeldung (Refresh-Token-Zeile) aus dem `sid`-Claim des Access-Tokens, `None` bei älteren Tokens.

    Prüft die Anmeldung nicht selbst: nur zusammen mit `CurrentUser` verwenden."""
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


def require_permission(permission: str):  # noqa: ANN201 - gibt eine FastAPI-Dependency zurück
    """Wiederverwendbare RBAC-Dependency, z. B. `dependencies=[Depends(require_permission("hosts.write"))]`.

    `is_owner` umgeht die Prüfung vollständig (docs/03-DATA-MODEL.md §1); das steckt in `user_has_permission()`.
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
    """Nur der Owner, nicht einmal die Rolle `admin` (`*`) reicht: Eine Sicherung enthält Master-Key und Keyring,
    eine Wiederherstellung könnte einen Admin zum Owner machen. Kritische Aktionen verlangen zusätzlich das
    aktuelle Passwort (`api/v1/auth.confirm_current_password`)."""
    if not user.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Das darf nur der Inhaber dieser Installation (Owner).",
        )
    return user


OwnerUser = Annotated[User, Depends(require_owner)]
