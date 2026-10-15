"""Secrets-Metadaten: keine Antwort enthält `ciphertext` oder Klartext, auch nicht für den Owner (docs/04-API.md)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core import vault
from ...ext.runtime import get_extension_runtime
from ...models import Host, HostCredential, Secret, SecretUsage, User
from ...services import audit as audit_service
from ..deps import SessionDep, SettingsDep, require_permission
from .auth import confirm_stored_secret_change

router = APIRouter(prefix="/secrets", tags=["secrets"])

SecretKind = Literal["password", "ssh_private_key", "api_token", "totp", "generic"]


class SecretUsageOut(BaseModel):
    """Ein Verwender des Secrets. `type`: `extension` (Modul), `host` (Server-Zugang), `login`
    (Zwei-Faktor eines Kontos); weitere Arten können dazukommen. `last_used_at` nur, wenn eine
    Nutzung festgehalten wurde (höchstens einmal je Stunde, siehe `core.vault.USE_NOTE_INTERVAL`)."""

    type: str
    id: str
    name: str
    last_used_at: datetime | None = None


# Die erlaubten Felder stehen einzeln da, das ORM-Objekt wird nie ungefiltert serialisiert.
class SecretOut(BaseModel):
    id: str
    label: str
    kind: str
    key_version: int
    owner_ext_id: str | None
    description: str
    created_by_user_id: str | None
    created_at: datetime
    rotated_at: datetime | None
    last_used_at: datetime | None
    expires_at: datetime | None = None
    used_by: list[SecretUsageOut] = []
    bound: list[SecretUsageOut] = []
    """Nur die festen Bindungen aus `used_by` (Besitzer-Modul, Server-Zugang, Zwei-Faktor). Solange hier etwas steht,
    lehnt `DELETE` mit 409 ab; eine nur festgehaltene Nutzung steht allein in `used_by` und sperrt nicht."""

    @classmethod
    def from_model(
        cls, secret: Secret, used_by: list[SecretUsageOut] | None = None, bound: list[SecretUsageOut] | None = None,
    ) -> "SecretOut":
        return cls(
            id=secret.id,
            label=secret.label,
            kind=secret.kind,
            key_version=secret.key_version,
            owner_ext_id=secret.owner_ext_id,
            description=secret.description,
            created_by_user_id=secret.created_by_user_id,
            created_at=secret.created_at,
            rotated_at=secret.rotated_at,
            last_used_at=secret.last_used_at,
            expires_at=secret.expires_at,
            used_by=used_by or [],
            bound=bound or [],
        )


class SecretCreate(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    kind: SecretKind
    value: str = Field(min_length=1)
    description: str = ""
    expires_at: datetime | None = None


class SecretConfirm(BaseModel):
    """Bestätigung für Ersetzen und Löschen: das eigene Passwort und, wenn Zwei-Faktor an ist, der Code aus der App."""

    current_password: str = Field(default="", max_length=1024)
    totp_code: str = Field(default="", max_length=64)


class SecretValueUpdate(SecretConfirm):
    value: str = Field(min_length=1)


class SecretPatch(BaseModel):
    """Nur die angegebenen Felder ändern sich; `expires_at: null` entfernt das Ablaufdatum. Eine Zeit
    ohne Zeitzone gilt als UTC."""

    description: str | None = Field(default=None, max_length=255)
    expires_at: datetime | None = None


_USAGE_ORDER = {"login": 0, "host": 1, "extension": 2}


def _in_order(entries: Iterable[SecretUsageOut]) -> list[SecretUsageOut]:
    """Konto, Server, Modul, Rest; darin nach Name."""
    return sorted(entries, key=lambda u: (_USAGE_ORDER.get(u.type, 9), u.type, u.name.lower(), u.id))


async def _used_by(
    session: AsyncSession, secret_ids: list[str], *, bound_only: bool = False,
) -> dict[str, tuple[list[SecretUsageOut], list[SecretUsageOut]]]:
    """Secret-ID -> (alle Verwender, davon die festen Bindungen), je sortiert. Fest gebunden sind Besitzer-Modul,
    Server-Zugang und Zwei-Faktor; in der ersten Liste stehen dazu die festgehaltenen Nutzungen aus `secret_usages`
    (nicht mit `bound_only`). Eine Nutzung ist nur ein Hinweis, sie sperrt das Löschen nicht."""
    found: dict[str, dict[tuple[str, str], SecretUsageOut]] = {sid: {} for sid in secret_ids}
    fixed: dict[str, set[tuple[str, str]]] = {sid: set() for sid in secret_ids}
    if not secret_ids:
        return {}
    runtime = get_extension_runtime()

    def extension(ext_id: str) -> tuple[str, str]:
        """(heutige Kennung, Name), auch für eine alte Kennung oder ein Modul, das gerade nicht geladen ist."""
        current = runtime.canonical(ext_id)
        loaded = runtime.loaded.get(current)
        manifest = loaded.manifest if loaded is not None else getattr(runtime.discovered.get(current), "manifest", None)
        return current, (getattr(manifest, "name", None) or current)

    def put(
        secret_id: str, type_: str, id_: str, name: str, last_used_at: datetime | None = None, *, bound: bool = True,
    ) -> None:
        if bound:
            fixed[secret_id].add((type_, id_))
        entry = found[secret_id].get((type_, id_))
        if entry is None:
            found[secret_id][(type_, id_)] = SecretUsageOut(type=type_, id=id_, name=name, last_used_at=last_used_at)
        elif last_used_at is not None and (entry.last_used_at is None or last_used_at > entry.last_used_at):
            entry.last_used_at = last_used_at

    owners = await session.execute(
        select(Secret.id, Secret.owner_ext_id).where(Secret.id.in_(secret_ids), Secret.owner_ext_id.is_not(None))
    )
    for secret_id, owner in owners.all():
        put(secret_id, "extension", *extension(owner))

    hosts = await session.execute(
        select(HostCredential.secret_id, Host.id, Host.name, Host.display_name)
        .join(Host, Host.id == HostCredential.host_id)
        .where(HostCredential.secret_id.in_(secret_ids))
    )
    for secret_id, host_id, name, display_name in hosts.all():
        put(secret_id, "host", host_id, display_name or name)

    logins = await session.execute(
        select(User.totp_secret_id, User.id, User.username).where(User.totp_secret_id.in_(secret_ids))
    )
    for secret_id, user_id, username in logins.all():
        put(secret_id, "login", user_id, username)

    if not bound_only:
        usages = await session.execute(select(SecretUsage).where(SecretUsage.secret_id.in_(secret_ids)))
        for usage in usages.scalars().all():
            if usage.consumer_type == "extension":
                put(
                    usage.secret_id, "extension", *extension(usage.consumer_id), last_used_at=usage.last_used_at,
                    bound=False,
                )
            else:
                put(
                    usage.secret_id, usage.consumer_type, usage.consumer_id, usage.consumer_id,
                    last_used_at=usage.last_used_at, bound=False,
                )

    return {
        sid: (_in_order(entries.values()), _in_order(entries[key] for key in fixed[sid]))
        for sid, entries in found.items()
    }


def _as_utc(value: datetime | None) -> datetime | None:
    """In UTC; ohne Zeitzone gilt die Zeit als UTC. Ein Datum am Rand des Kalenders (Jahr 1 oder 9999 mit
    Versatz) lässt sich nicht umrechnen: 422 statt eines Absturzes."""
    if value is None:
        return None
    try:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail="Dieses Ablaufdatum lässt sich nicht verwenden."
        ) from exc


@router.get("", dependencies=[Depends(require_permission("secrets.read"))])
async def list_secrets(session: SessionDep) -> list[SecretOut]:
    result = await session.execute(select(Secret).order_by(Secret.label))
    secrets = list(result.scalars().all())
    users = await _used_by(session, [s.id for s in secrets])
    return [SecretOut.from_model(s, *users.get(s.id, ([], []))) for s in secrets]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_secret(
    payload: SecretCreate,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: Annotated[User, Depends(require_permission("secrets.write"))],
) -> SecretOut:
    existing = await session.execute(select(Secret.id).where(Secret.label == payload.label))
    if existing.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Label '{payload.label}' bereits vergeben.",
        )

    keyring = vault.load_keyring(settings)
    secret = await vault.create_secret(
        session,
        keyring,
        label=payload.label,
        kind=payload.kind,
        plaintext=payload.value,
        description=payload.description,
        created_by_user_id=user.id,
        expires_at=_as_utc(payload.expires_at),
    )
    await _audit(session, request, user, "secret.created", secret, kind=secret.kind)
    return SecretOut.from_model(secret)


async def _audit(session: AsyncSession, request: Request, user: User, action: str, secret: Secret, **detail) -> None:
    """Protokoll-Eintrag zu einem Eintrag des Tresors, mit Label und nie mit dem Wert."""
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action=action, outcome="success", target_type="secret",
        target_id=secret.id, detail={"label": secret.label, **detail},
        ip=request.client.host if request.client else None, user_agent=request.headers.get("user-agent"),
    )


LOGIN_SECRET_MESSAGE = (
    "Dieser Eintrag gehört zur Zwei-Faktor-Anmeldung eines Kontos und lässt sich hier weder "
    "ändern noch löschen. Abschalten geht unter „Mein Konto“ bzw. bei anderen Nutzern unter "
    "„Benutzer“ mit „Zwei-Faktor zurücksetzen“."
)


async def _refuse_login_secret(session: SessionDep, secret_id: str) -> None:
    """Der Zwei-Faktor-Schlüssel eines Kontos ist hier tabu (409)."""
    # Ersetzt hätte jeder mit `secrets.write` einen eigenen Authenticator für ein fremdes Konto; gelöscht käme der
    # Besitzer nicht mehr hinein.
    owner_of = await session.execute(select(User.id).where(User.totp_secret_id == secret_id).limit(1))
    if owner_of.first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LOGIN_SECRET_MESSAGE)


@router.put("/{secret_id}/value", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def replace_secret_value(
    secret_id: str,
    payload: SecretValueUpdate,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: Annotated[User, Depends(require_permission("secrets.write"))],
) -> JSONResponse | None:
    """Ersetzt den Wert; verlangt das eigene Passwort und, wenn Zwei-Faktor an ist, den Code aus der App."""
    await _refuse_login_secret(session, secret_id)
    secret = await session.get(Secret, secret_id)
    if secret is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diesen Eintrag gibt es nicht (mehr).")
    refused = await confirm_stored_secret_change(
        request, session, settings, user, payload.current_password, payload.totp_code, "tresor_ersetzen",
        "Zum Ersetzen bitte das aktuelle Passwort eingeben.",
    )
    if refused is not None:
        return refused
    await vault.replace_secret_value(session, vault.load_keyring(settings), secret_id, payload.value)
    await _audit(session, request, user, "secret.replaced", secret)
    return None


@router.patch("/{secret_id}")
async def update_secret(
    secret_id: str,
    payload: SecretPatch,
    request: Request,
    session: SessionDep,
    user: Annotated[User, Depends(require_permission("secrets.write"))],
) -> SecretOut:
    """Beschreibung und Ablaufdatum ändern; der Wert bleibt (dafür `PUT /{secret_id}/value`)."""
    await _refuse_login_secret(session, secret_id)
    secret = await session.get(Secret, secret_id)
    if secret is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diesen Eintrag gibt es nicht (mehr).")
    if "description" in payload.model_fields_set:
        secret.description = (payload.description or "").strip()
    if "expires_at" in payload.model_fields_set:
        secret.expires_at = _as_utc(payload.expires_at)
    await session.flush()
    await _audit(session, request, user, "secret.updated", secret, fields=sorted(payload.model_fields_set))
    users = await _used_by(session, [secret.id])
    return SecretOut.from_model(secret, *users.get(secret.id, ([], [])))


IN_USE_MESSAGE = (
    "Dieser Eintrag wird noch benutzt. Erst dort austauschen oder entfernen (beim Modul in dessen Einstellungen, beim "
    "Server unter Zugang), dann lässt er sich hier löschen."
)


@router.delete("/{secret_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_secret(
    secret_id: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: Annotated[User, Depends(require_permission("secrets.write"))],
    payload: Annotated[SecretConfirm | None, Body()] = None,
) -> JSONResponse | None:
    """Löscht einen Eintrag, an den nichts mehr fest gebunden ist (Besitzer-Modul, Server-Zugang; sonst 409 mit diesen
    Bindungen in `used_by`), mit Passwort und gegebenenfalls Code. Eine nur festgehaltene Nutzung sperrt nicht.

    Ein schon gelöschter oder unbekannter Eintrag ist kein Fehler."""
    await _refuse_login_secret(session, secret_id)
    # Gesperrt nur durch feste Bindungen: Ein von Hand angelegter Eintrag, den ein Modul einmal gelesen hat, bliebe sonst
    # für immer liegen, denn die festgehaltene Nutzung verschwindet nie von selbst.
    _, used = (await _used_by(session, [secret_id], bound_only=True))[secret_id]
    if used:
        return JSONResponse(
            {"detail": IN_USE_MESSAGE, "code": "secret_in_use", "used_by": [u.model_dump(mode="json") for u in used]},
            status_code=status.HTTP_409_CONFLICT,
        )
    confirm = payload or SecretConfirm()
    refused = await confirm_stored_secret_change(
        request, session, settings, user, confirm.current_password, confirm.totp_code, "tresor_loeschen",
        "Zum Löschen bitte das aktuelle Passwort eingeben.",
    )
    if refused is not None:
        return refused
    secret = await session.get(Secret, secret_id)
    if secret is not None:
        await _audit(session, request, user, "secret.deleted", secret)
        await vault.delete_secret(session, secret_id)
    return None
