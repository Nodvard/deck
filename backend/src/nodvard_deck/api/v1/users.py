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

**Protokoll:** Anlegen, Aendern und Loeschen eines Kontos stehen im Audit-Protokoll
(`user.created`, `user.updated`, `user.deleted`, dazu `user.2fa_reset`). Bei einer Aenderung
nennt `detail` nur, was sich geaendert hat; Passwort, E-Mail-Adresse und Anzeigename stehen
nie mit Wert darin.

**Fremde Zugaenge und Verwaltungsrechte nur mit Bestaetigung:** Das eigene Passwort des Admins
(`current_password`) und, wenn er Zwei-Faktor an hat, sein aktueller Code aus der App (`totp_code`) braucht es,
um
- das Passwort eines anderen Kontos zu setzen (`PATCH /users/{id}` mit `password`),
- dessen Zwei-Faktor-Anmeldung zurueckzusetzen (`POST /users/{id}/reset-2fa`),
- ein Konto mit Rollen anzulegen, die Konten verwalten duerfen (`POST /users`, `users.write` oder `*`), und
- einem Konto solche Rollen zu geben (`PATCH /users/{id}` mit `role_ids`).
Sonst kaeme eine gestohlene Admin-Sitzung allein an fremde Konten, auch auf dem Umweg ueber ein neues
Admin-Konto. Geprueft wird zuerst das Passwort (ohne verraet keine Antwort etwas ueber das Ziel), dann das
Ziel, zuletzt der Code (eine Ablehnung wegen des Ziels verbraucht ihn nicht). Jede dieser Aenderungen steht
danach unter „Meldungen“ (`_report_account_change`): wer sie dort lesen darf, und der Inhaber, sieht sie.
Loeschen bleibt ohne Bestaetigung: ein danach neu angelegtes Konto gleichen Namens ist ein neues Konto und
bekommt Verwaltungsrechte nur mit Bestaetigung.

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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from ...core import rbac, security
from ...db.base import refresh_relationships
from ...models import Role, User, user_roles
from ...services import audit as audit_service
from ...services import auth as auth_service
from ...services import settings as settings_service
from ..deps import CurrentUser, SessionDep, SettingsDep, require_permission
from ..errors import CodedHTTPException
from .auth import TotpMissing, confirm_current_password, confirm_current_totp

router = APIRouter(tags=["users"])


class RoleOut(BaseModel):
    id: str
    name: str
    description: str
    is_builtin: bool
    manages_users: bool = False
    """Die Rolle darf Konten verwalten (`users.write` oder `*`): Wer sie vergibt, bestaetigt das mit eigenem
    Passwort und Code (siehe Modulkopf)."""


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
    current_password: str = Field(default="", max_length=1024)
    """Nur bei Rollen, die Konten verwalten duerfen: das eigene Passwort des handelnden Admins (fehlt → 403)."""
    totp_code: str = Field(default="", max_length=64)
    """Wie `current_password`, dazu der Code aus der App, wenn der Admin Zwei-Faktor an hat (`totp_missing`)."""

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        return auth_service.validate_new_username(value)


class ResetTwoFactorRequest(BaseModel):
    current_password: str
    """Das Passwort des handelnden Admins (nicht des Ziels)."""
    totp_code: str = Field(default="", max_length=64)
    """Der aktuelle Code aus der App des handelnden Admins, Pflicht, wenn er Zwei-Faktor an hat (sonst 403 mit
    `code: "totp_missing"`). Im Schema optional: ohne Zwei-Faktor reicht das Passwort wie bisher."""


class UserUpdate(BaseModel):
    display_name: str | None = None
    email: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=255)
    current_password: str = Field(default="", max_length=1024)
    """Nur mit `password`: das eigene Passwort des handelnden Admins (nicht des Ziels). Fehlt es, 403."""
    totp_code: str = Field(default="", max_length=64)
    """Nur mit `password`: der aktuelle Code aus der App des handelnden Admins, Pflicht, wenn er Zwei-Faktor an hat
    (sonst 403 mit `code: "totp_missing"`)."""
    role_ids: list[str] | None = None
    """`None` = unveraendert lassen, `[]` = alle Rollen entziehen -- zwei
    unterscheidbare Faelle, deshalb kein Default von `[]`."""


def _manages_users(roles: list[Role]) -> bool:
    """Darf ein Konto mit diesen Rollen andere Konten verwalten (`users.write`, auch ueber `*`)?"""
    return rbac.has_permission([rp.permission for role in roles for rp in role.permissions], "users.write")


def _role_out(role: Role) -> RoleOut:
    return RoleOut(
        id=role.id, name=role.name, description=role.description, is_builtin=role.is_builtin,
        manages_users=_manages_users([role]),
    )


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


ADMIN_PASSWORD_REQUIRED = "Zur Bestätigung brauchst du dein eigenes Passwort (nicht das der anderen Person)."
ADMIN_PASSWORD_MISSING_CODE = "password_missing"
"""`code` der 403-Antwort ohne `current_password`: die Oberflaeche zeigt daran das Feld fuers eigene Passwort."""


async def _confirm_admin_password(request: Request, session: SessionDep, admin: User, password: str, action: str) -> None:
    """Erster Teil der Bestaetigung: das eigene Passwort des handelnden Admins. Fehlt es 403, falsch 400, zu oft 429."""
    if not password:
        raise CodedHTTPException(status.HTTP_403_FORBIDDEN, ADMIN_PASSWORD_REQUIRED, ADMIN_PASSWORD_MISSING_CODE)
    await confirm_current_password(request, session, admin, password, action)


async def _confirm_admin_code(
    request: Request, session: SessionDep, settings: Any, admin: User, code: str, action: str
) -> Any:
    """Zweiter Teil, erst wenn feststeht, dass die Aenderung wirklich geschieht (sonst verbraucht eine Ablehnung wegen
    des Ziels einen Code): wenn der Admin Zwei-Faktor an hat, sein Code aus der App -- wie bei Sicherung und Update kein
    Wiederherstellungs-Code. Fehlt er, kommt 403 `totp_missing` als Antwort zurueck; falsch 400. `None` heisst
    bestaetigt."""
    try:
        await confirm_current_totp(request, session, settings, admin, code, action)
    except TotpMissing as exc:
        return exc.response()
    return None


_CHANGE_TEXT = {
    "password": (
        "Zugang von „{target}“ geändert",
        (
            "„{admin}“ hat für „{target}“ ein neues Passwort gesetzt. Die Anmeldungen dieses Kontos laufen damit aus "
            "(spätestens nach 15 Minuten)."
        ),
    ),
    "2fa": (
        "Zugang von „{target}“ geändert",
        (
            "„{admin}“ hat die Zwei-Faktor-Anmeldung von „{target}“ zurückgesetzt. Die Anmeldungen dieses Kontos laufen "
            "damit aus (spätestens nach 15 Minuten)."
        ),
    ),
    "admin_created": (
        "Neues Konto mit Verwaltungsrechten: „{target}“",
        "„{admin}“ hat das Konto „{target}“ angelegt. Es darf andere Konten verwalten.",
    ),
    "admin_granted": (
        "Verwaltungsrechte für „{target}“",
        "„{admin}“ hat „{target}“ Rollen gegeben, mit denen das Konto andere Konten verwalten darf.",
    ),
}


async def _report_account_change(session: SessionDep, admin: User, target: User, kind: str) -> None:
    """Meldung unter „Meldungen“ (und als Push), dass ein Admin den Zugang eines anderen Kontos geaendert oder
    Verwaltungsrechte vergeben hat. Wer Meldungen lesen darf, und der Inhaber, sieht so, was geschah und wer es war.
    Scheitert die Zustellung, bleibt die Aenderung (sie steht im Protokoll)."""
    from ...services import notifications as notifications_service

    title, text = _CHANGE_TEXT[kind]
    try:
        await notifications_service.send(
            session,
            title=title.format(target=target.username),
            body=(
                f"{text.format(admin=admin.username, target=target.username)} "
                "Hast du das nicht erwartet, sprich mit der Person, die es getan hat, oder dem Inhaber."
            ),
            severity="warning",
            payload={"path": "/settings/users", "tags": ["warning", "key"]},
        )
    except Exception:
        import logging

        logging.getLogger(__name__).warning("Meldung zur Kontoaenderung nicht gesendet", exc_info=True)


async def _audit_user(
    session: SessionDep, actor: User, action: str, target: User, detail: dict[str, Any]
) -> None:
    """Eine Zeile im Protokoll zu einem Konto (`user.created`, `user.updated`, `user.deleted`).
    Wer handelt, ist `actor`. Passwoerter und E-Mail-Adressen stehen nie in `detail`."""
    await audit_service.log(
        session, actor_type="user", actor_id=actor.id, action=action, outcome="success",
        target_type="user", target_id=target.id, detail=detail,
    )


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
async def create_user(
    payload: UserCreate, request: Request, session: SessionDep, settings: SettingsDep, current_user: CurrentUser
) -> UserOut:
    roles = await _resolve_roles(session, payload.role_ids)
    # Verwaltungsrechte nur mit Bestaetigung (siehe Modulkopf): erst das Passwort, dann das Ziel, dann der Code.
    grants_admin = _manages_users(roles)
    if grants_admin:
        await _confirm_admin_password(request, session, current_user, payload.current_password, "verwalter_anlegen")
    existing = (await session.execute(select(User).where(func.lower(User.username) == payload.username))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Benutzername '{payload.username}' existiert bereits.")
    if grants_admin:
        refused = await _confirm_admin_code(request, session, settings, current_user, payload.totp_code, "verwalter_anlegen")
        if refused is not None:
            return refused

    user = User(
        username=payload.username, password_hash=await security.hash_password_async(payload.password, signed_in=True),
        display_name=payload.display_name, email=payload.email, is_active=True, is_owner=False,
    )
    # Brandneues, noch nicht der Session uebergebenes Objekt -- direkte
    # Listenzuweisung ist hier unkritisch (siehe Modul-Docstring).
    user.roles = roles
    session.add(user)
    await session.flush()
    await _audit_user(
        session, current_user, "user.created", user,
        {"username": user.username, "roles": sorted(r.name for r in roles)},
    )
    if grants_admin:
        await _report_account_change(session, current_user, user, "admin_created")
    return _user_out(user)


@router.patch("/users/{user_id}", dependencies=[Depends(require_permission("users.write"))])
async def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    current_user: CurrentUser,
) -> UserOut:
    # Rollen zuerst aufloesen (sie sind ueber GET /roles ohnehin sichtbar): ob die Aenderung Verwaltungsrechte
    # vergibt, entscheidet, ob es eine Bestaetigung braucht. Eine unbekannte Rolle (404) verbraucht so keinen Code.
    roles = await _resolve_roles(session, payload.role_ids) if payload.role_ids is not None else None
    grants_admin = roles is not None and _manages_users(roles)
    needs_confirmation = payload.password is not None or grants_admin
    if payload.password is not None and user_id == current_user.id:
        # Das eigene Passwort nur ueber `POST /me/password` (verlangt das alte, ist gedrosselt) -- sonst liesse sich
        # dieses Erfordernis mit `users.write` umgehen. Vor der Bestaetigung: das verraet nichts ueber andere Konten.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Das eigene Passwort lässt sich nur unter „Mein Konto“ ändern (dort wird das aktuelle Passwort abgefragt).",
        )
    if needs_confirmation:
        # Passwort des Admins vor allem, was etwas ueber das Ziel verraet (404, 409, 403).
        await _confirm_admin_password(
            request, session, current_user, payload.current_password,
            "passwort_fremd" if payload.password is not None else "verwalter_rechte",
        )
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nutzer nicht gefunden.")
    if user.is_owner and payload.is_active is False:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Der Owner-Account kann nicht deaktiviert werden.")
    newly_admin = grants_admin and not _manages_users(list(user.roles))
    # Das Passwort des Inhabers darf kein anderer setzen: damit koennte ein Admin das Konto mit den meisten Rechten
    # uebernehmen.
    if payload.password is not None and user.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Das Passwort des Inhabers kann nur der Inhaber selbst ändern (unter „Mein Konto“). Notfalls geht es auf dem Server mit dem Notfall-Befehl.",
        )
    if needs_confirmation:
        # Der Code erst jetzt, wenn feststeht, dass die Aenderung geschieht (siehe Modulkopf). Ohne `password` und
        # ohne Verwaltungsrollen bleibt alles wie bisher (Name, E-Mail, Sperren brauchen keine Bestaetigung).
        refused = await _confirm_admin_code(
            request, session, settings, current_user, payload.totp_code,
            "passwort_fremd" if payload.password is not None else "verwalter_rechte",
        )
        if refused is not None:
            return refused

    # Was sich wirklich aendert, steht danach im Protokoll (E-Mail und Anzeigename nur als
    # "geaendert", nie mit Wert; das Passwort nur als Merker `password_reset`).
    changes: dict[str, Any] = {}
    old_role_names = sorted(r.name for r in user.roles)
    if payload.display_name is not None:
        if payload.display_name != user.display_name:
            changes["display_name_changed"] = True
        user.display_name = payload.display_name
    if payload.email is not None:
        if payload.email != user.email:
            changes["email_changed"] = True
        user.email = payload.email
    if payload.is_active is not None:
        if payload.is_active != user.is_active:
            changes["is_active"] = {"old": user.is_active, "new": payload.is_active}
        user.is_active = payload.is_active
    if payload.is_active is False:
        # Alle Anmeldungen enden mit dem Deaktivieren. Sonst wuerden die alten Geraete (Cookie,
        # App, Kommandozeile) nach dem Wieder-Aktivieren ohne neue Anmeldung weiterlaufen --
        # auch dann, wenn das Konto wegen einer verlorenen oder gestohlenen Anmeldung gesperrt wurde.
        await auth_service.revoke_refresh_tokens(session, user.id)
    if payload.password is not None:
        user.password_hash = await security.hash_password_async(payload.password, signed_in=True)
        # Neues Passwort = alte Anmeldungen dieses Nutzers gelten nicht mehr.
        # (Das eigene Passwort geht hier nicht, siehe oben -- also immer ein anderer Nutzer.)
        await auth_service.revoke_refresh_tokens(session, user.id)

    if roles is not None:
        new_role_names = sorted(r.name for r in roles)
        if old_role_names != new_role_names:
            changes["roles"] = {"old": old_role_names, "new": new_role_names}
        # Bereits persistenter User -- KEIN `.roles`-Zugriff zum Schreiben
        # (D-12), stattdessen die Assoziationstabelle direkt beschreiben.
        await session.execute(user_roles.delete().where(user_roles.c.user_id == user.id))
        await session.flush()
        for role in roles:
            await session.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))

    await session.flush()
    await refresh_relationships(session, user, "roles")
    if changes or payload.password is not None:
        await _audit_user(
            session, current_user, "user.updated", user,
            {"username": user.username, "password_reset": payload.password is not None, **changes},
        )
    if payload.password is not None:
        await _report_account_change(session, current_user, user, "password")
    if newly_admin:
        await _report_account_change(session, current_user, user, "admin_granted")
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
    await _audit_user(
        session, current_user, "user.deleted", user,
        {"username": user.username, "roles": sorted(r.name for r in user.roles)},
    )
    await settings_service.delete_user_settings(session, user.id)
    await session.delete(user)


@router.post("/users/{user_id}/reset-2fa", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_permission("users.write"))])
async def reset_user_2fa(
    user_id: str, payload: ResetTwoFactorRequest, request: Request, current_user: CurrentUser, session: SessionDep,
    settings: SettingsDep,
):
    """Schaltet die Zwei-Faktor-Anmeldung eines ANDEREN Nutzers ab (Handy und Wiederherstellungs-
    Codes verloren). Seine Wiederherstellungs-Codes werden geloescht und er wird ueberall abgemeldet,
    damit eine evtl. gestohlene Sitzung nicht weiterlaeuft. Jede Anwendung steht im Audit-Protokoll.

    Der Owner ist ausgenommen: seine 2FA kann nur er selbst abschalten (`DELETE /me/totp`) -- sonst
    koennte jeder Admin das Konto mit den meisten Rechten auf ein einfaches Passwort herabstufen.
    Das eigene Konto geht ebenfalls ueber `/me/totp`, nicht hier (dort ist es Teil von "Mein Konto")."""
    # Passwort und Code des ADMINS zuerst (gedrosselt): eine gestohlene Admin-Sitzung allein soll keine
    # Zwei-Faktor-Anmeldung anderer abschalten koennen -- auch nicht mit dem Passwort, wenn er selbst Zwei-Faktor an hat.
    await _confirm_admin_password(request, session, current_user, payload.current_password, "2fa_zuruecksetzen")
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
    refused = await _confirm_admin_code(request, session, settings, current_user, payload.totp_code, "2fa_zuruecksetzen")
    if refused is not None:
        return refused

    await auth_service.disable_totp(session, user)
    revoked = await auth_service.revoke_refresh_tokens(session, user.id)
    await audit_service.log(
        session, actor_type="user", actor_id=current_user.id, action="user.2fa_reset", outcome="success",
        target_type="user", target_id=user.id, detail={"signed_out_sessions": revoked},
    )
    await _report_account_change(session, current_user, user, "2fa")
    return None
