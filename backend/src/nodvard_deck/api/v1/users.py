"""Nutzerverwaltung -- docs/03-DATA-MODEL.md §1.

**Seit WP-1 offen (live im Test gefunden):** `POST /auth/
bootstrap` legt nur den allerersten Nutzer an (selbstlimitierend, schlaegt danach
immer fehl), `GET /me` liefert nur Selbstauskunft -- es gab bisher KEINEN Weg,
WEITERE Accounts anzulegen, Rollen zuzuweisen oder Accounts zu entfernen, ausser
direkt in der DB. Dieses Modul schliesst genau diese Luecke, sonst nichts --
kein Einladungs-/E-Mail-Flow (kein Mailversand im Projekt), Passwoerter werden
direkt beim Anlegen gesetzt (wie beim Bootstrap).

**Der Owner-Account bleibt ueber diese API unantastbar** -- `is_owner` wird hier
nirgends gesetzt oder geaendert (bleibt Bootstrap-only, siehe `User.is_owner`s
eigener Docstring: "Genau einer... kann sich das nicht selbst entziehen"), und
weder geloescht noch deaktiviert werden kann er auch nicht: eine unglueckliche
Aktion in dieser API darf die Installation nicht aussperren koennen, genau das
Szenario, vor dem `is_owner` schon schuetzt. Das eigene Konto kann sich niemand
ueber `DELETE /users/{id}` selbst entziehen (verhindert versehentliches
Selbst-Aussperren mitten in einer Session).

**Rollen-Zuweisung, D-12-sicher (docs/00-DECISIONS.md):** bei einem BRANDNEUEN
`User` wird `.roles` als normale Python-Liste gesetzt, BEVOR das Objekt der
Session uebergeben wird (unkritisch -- das ORM hat die Collection noch nicht
anfasst). Bei einem BEREITS PERSISTENTEN `User` (Update) wird stattdessen die
Assoziationstabelle `user_roles` direkt beschrieben (kein `.roles`-Zugriff zum
Schreiben), danach `refresh_relationships()` vor jedem Response-Aufbau -- exakt
das etablierte, bereits mehrfach bewiesene Muster (`ensure_builtin_roles()`,
`HostsHandle.upsert_discovered()`)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from ...core import security
from ...db.base import refresh_relationships
from ...models import Role, User, user_roles
from ...services import audit as audit_service
from ...services import auth as auth_service
from ...services import settings as settings_service
from ..deps import CurrentUser, SessionDep, require_permission
from .auth import confirm_current_password

router = APIRouter(tags=["users"])


class RoleOut(BaseModel):
    id: str
    name: str
    description: str
    is_builtin: bool


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str
    email: str | None
    is_active: bool
    is_owner: bool
    totp_enabled: bool
    locale: str
    last_login_at: datetime | None
    created_at: datetime
    roles: list[RoleOut]


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=255)
    display_name: str = ""
    email: str | None = None
    role_ids: list[str] = Field(default_factory=list)

    @field_validator("username")
    @classmethod
    def _lower(cls, value: str) -> str:
        return auth_service.normalize_username(value)


class ResetTwoFactorRequest(BaseModel):
    current_password: str
    """Das Passwort des handelnden Admins (nicht des Ziels)."""


class UserUpdate(BaseModel):
    display_name: str | None = None
    email: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=255)
    role_ids: list[str] | None = None
    """`None` = unveraendert lassen, `[]` = alle Rollen entziehen -- zwei
    unterscheidbare Faelle, deshalb kein Default von `[]`."""


def _role_out(role: Role) -> RoleOut:
    return RoleOut(id=role.id, name=role.name, description=role.description, is_builtin=role.is_builtin)


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id, username=user.username, display_name=user.display_name, email=user.email,
        is_active=user.is_active, is_owner=user.is_owner,
        totp_enabled=auth_service.two_factor_enabled(user), locale=user.locale,
        last_login_at=user.last_login_at, created_at=user.created_at,
        roles=[_role_out(r) for r in user.roles],
    )


async def _resolve_roles(session: SessionDep, role_ids: list[str]) -> list[Role]:
    if not role_ids:
        return []
    rows = (await session.execute(select(Role).where(Role.id.in_(role_ids)))).scalars().all()
    missing = set(role_ids) - {r.id for r in rows}
    if missing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unbekannte Rolle(n): {', '.join(sorted(missing))}.")
    return list(rows)


@router.get("/roles", dependencies=[Depends(require_permission("users.read"))])
async def list_roles(session: SessionDep) -> list[RoleOut]:
    rows = (await session.execute(select(Role).order_by(Role.name))).scalars().all()
    return [_role_out(r) for r in rows]


@router.get("/users", dependencies=[Depends(require_permission("users.read"))])
async def list_users(session: SessionDep) -> list[UserOut]:
    rows = (await session.execute(select(User).order_by(User.username))).scalars().all()
    return [_user_out(u) for u in rows]


@router.get("/users/{user_id}", dependencies=[Depends(require_permission("users.read"))])
async def get_user(user_id: str, session: SessionDep) -> UserOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nutzer nicht gefunden.")
    return _user_out(user)


@router.post("/users", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("users.write"))])
async def create_user(payload: UserCreate, session: SessionDep) -> UserOut:
    existing = (await session.execute(select(User).where(func.lower(User.username) == payload.username))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Benutzername '{payload.username}' existiert bereits.")
    roles = await _resolve_roles(session, payload.role_ids)

    user = User(
        username=payload.username, password_hash=security.hash_password(payload.password),
        display_name=payload.display_name, email=payload.email, is_active=True, is_owner=False,
    )
    # Brandneues, noch nicht der Session uebergebenes Objekt -- direkte
    # Listenzuweisung ist hier unkritisch (siehe Modul-Docstring).
    user.roles = roles
    session.add(user)
    await session.flush()
    return _user_out(user)


@router.patch("/users/{user_id}", dependencies=[Depends(require_permission("users.write"))])
async def update_user(
    user_id: str,
    payload: UserUpdate,
    session: SessionDep,
    current_user: CurrentUser,
) -> UserOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nutzer nicht gefunden.")
    if user.is_owner and payload.is_active is False:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Der Owner-Account kann nicht deaktiviert werden.")
    if payload.password is not None:
        # Das eigene Passwort nur ueber `POST /me/password` (verlangt das alte, ist gedrosselt) --
        # sonst liesse sich dieses Erfordernis mit `users.write` umgehen. Und das des Inhabers
        # darf kein anderer setzen: damit koennte ein Admin das Konto mit den meisten Rechten uebernehmen.
        if user.id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Das eigene Passwort lässt sich nur unter „Mein Konto“ ändern (dort wird das aktuelle Passwort abgefragt).",
            )
        if user.is_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Das Passwort des Inhabers kann nur der Inhaber selbst ändern (unter „Mein Konto“). Notfalls geht es auf dem Server mit dem Notfall-Befehl.",
            )

    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.email is not None:
        user.email = payload.email
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password is not None:
        user.password_hash = security.hash_password(payload.password)
        # Neues Passwort = alte Anmeldungen dieses Nutzers gelten nicht mehr.
        # (Das eigene Passwort geht hier nicht, siehe oben -- also immer ein anderer Nutzer.)
        await auth_service.revoke_refresh_tokens(session, user.id)

    if payload.role_ids is not None:
        roles = await _resolve_roles(session, payload.role_ids)
        # Bereits persistenter User -- KEIN `.roles`-Zugriff zum Schreiben
        # (D-12), stattdessen die Assoziationstabelle direkt beschreiben.
        await session.execute(user_roles.delete().where(user_roles.c.user_id == user.id))
        await session.flush()
        for role in roles:
            await session.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))

    await session.flush()
    await refresh_relationships(session, user, "roles")
    return _user_out(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_permission("users.write"))])
async def delete_user(user_id: str, current_user: CurrentUser, session: SessionDep) -> None:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nutzer nicht gefunden.")
    if user.is_owner:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Der Owner-Account kann nicht gelöscht werden.")
    if user.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Das eigene Konto kann nicht über diese API gelöscht werden.")
    await settings_service.delete_user_settings(session, user.id)
    await session.delete(user)


@router.post("/users/{user_id}/reset-2fa", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_permission("users.write"))])
async def reset_user_2fa(
    user_id: str, payload: ResetTwoFactorRequest, request: Request, current_user: CurrentUser, session: SessionDep
) -> None:
    """Schaltet die Zwei-Faktor-Anmeldung eines ANDEREN Nutzers ab (Handy und Wiederherstellungs-
    Codes verloren). Seine Wiederherstellungs-Codes werden geloescht und er wird ueberall abgemeldet,
    damit eine evtl. gestohlene Sitzung nicht weiterlaeuft. Jede Anwendung steht im Audit-Protokoll.

    Der Owner ist ausgenommen: seine 2FA kann nur er selbst abschalten (`DELETE /me/totp`) -- sonst
    koennte jeder Admin das Konto mit den meisten Rechten auf ein einfaches Passwort herabstufen.
    Das eigene Konto geht ebenfalls ueber `/me/totp`, nicht hier (dort ist es Teil von "Mein Konto")."""
    # Passwort des ADMINS zuerst (gedrosselt): eine gestohlene Admin-Sitzung allein soll keine
    # Zwei-Faktor-Anmeldung anderer abschalten koennen.
    await confirm_current_password(request, session, current_user, payload.current_password, "2fa_zuruecksetzen")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nutzer nicht gefunden.")
    if user.is_owner and user.id != current_user.id:
        await audit_service.log(
            session, actor_type="user", actor_id=current_user.id, action="user.2fa_reset", outcome="denied",
            target_type="user", target_id=user.id, reason="Die Zwei-Faktor-Anmeldung des Inhabers kann nur er selbst zurücksetzen.",
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Die Zwei-Faktor-Anmeldung des Inhabers kann nur der Inhaber selbst zurücksetzen.",
        )
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Die eigene Zwei-Faktor-Anmeldung lässt sich unter „Mein Konto“ abschalten.",
        )
    if not auth_service.two_factor_enabled(user) and user.totp_secret_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dieser Nutzer hat keine Zwei-Faktor-Anmeldung.")

    await auth_service.disable_totp(session, user)
    revoked = await auth_service.revoke_refresh_tokens(session, user.id)
    await audit_service.log(
        session, actor_type="user", actor_id=current_user.id, action="user.2fa_reset", outcome="success",
        target_type="user", target_id=user.id, detail={"signed_out_sessions": revoked},
    )
