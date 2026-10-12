"""Secrets-Metadaten: keine Antwort enthält `ciphertext` oder Klartext, auch nicht für den Owner (docs/04-API.md)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core import vault
from ...ext.runtime import get_extension_runtime
from ...models import Host, HostCredential, Secret, SecretUsage, User
from ..deps import SessionDep, SettingsDep, require_permission

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

    @classmethod
    def from_model(cls, secret: Secret, used_by: list[SecretUsageOut] | None = None) -> "SecretOut":
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
        )


class SecretCreate(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    kind: SecretKind
    value: str = Field(min_length=1)
    description: str = ""
    expires_at: datetime | None = None


class SecretValueUpdate(BaseModel):
    value: str = Field(min_length=1)


class SecretPatch(BaseModel):
    """Nur die angegebenen Felder ändern sich; `expires_at: null` entfernt das Ablaufdatum. Eine Zeit
    ohne Zeitzone gilt als UTC."""

    description: str | None = Field(default=None, max_length=255)
    expires_at: datetime | None = None


_USAGE_ORDER = {"login": 0, "host": 1, "extension": 2}


async def _used_by(session: AsyncSession, secret_ids: list[str]) -> dict[str, list[SecretUsageOut]]:
    """Secret-ID -> Verwender, sortiert (Konto, Server, Modul, Rest): feste Bindungen aus Besitzer-Modul,
    Server-Zugang und Zwei-Faktor, dazu die festgehaltenen Nutzungen aus `secret_usages`."""
    found: dict[str, dict[tuple[str, str], SecretUsageOut]] = {sid: {} for sid in secret_ids}
    if not secret_ids:
        return {}
    runtime = get_extension_runtime()

    def extension(ext_id: str) -> tuple[str, str]:
        """(heutige Kennung, Name), auch für eine alte Kennung oder ein Modul, das gerade nicht geladen ist."""
        current = runtime.canonical(ext_id)
        loaded = runtime.loaded.get(current)
        manifest = loaded.manifest if loaded is not None else getattr(runtime.discovered.get(current), "manifest", None)
        return current, (getattr(manifest, "name", None) or current)

    def put(secret_id: str, type_: str, id_: str, name: str, last_used_at: datetime | None = None) -> None:
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

    usages = await session.execute(select(SecretUsage).where(SecretUsage.secret_id.in_(secret_ids)))
    for usage in usages.scalars().all():
        if usage.consumer_type == "extension":
            put(usage.secret_id, "extension", *extension(usage.consumer_id), last_used_at=usage.last_used_at)
        else:
            put(usage.secret_id, usage.consumer_type, usage.consumer_id, usage.consumer_id, last_used_at=usage.last_used_at)

    return {
        sid: sorted(entries.values(), key=lambda u: (_USAGE_ORDER.get(u.type, 9), u.type, u.name.lower(), u.id))
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
    used_by = await _used_by(session, [s.id for s in secrets])
    return [SecretOut.from_model(s, used_by.get(s.id)) for s in secrets]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_secret(
    payload: SecretCreate,
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
    return SecretOut.from_model(secret)


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


@router.put(
    "/{secret_id}/value",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("secrets.write"))],
)
async def replace_secret_value(
    secret_id: str, payload: SecretValueUpdate, session: SessionDep, settings: SettingsDep
) -> None:
    await _refuse_login_secret(session, secret_id)
    keyring = vault.load_keyring(settings)
    try:
        await vault.replace_secret_value(session, keyring, secret_id, payload.value)
    except vault.VaultError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{secret_id}", dependencies=[Depends(require_permission("secrets.write"))])
async def update_secret(secret_id: str, payload: SecretPatch, session: SessionDep) -> SecretOut:
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
    used_by = await _used_by(session, [secret.id])
    return SecretOut.from_model(secret, used_by.get(secret.id))


@router.delete(
    "/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("secrets.write"))],
)
async def delete_secret(secret_id: str, session: SessionDep) -> None:
    """Idempotent: ein bereits gelöschtes oder unbekanntes Secret ist kein Fehler (wie `services.auth.logout()`)."""
    await _refuse_login_secret(session, secret_id)
    await vault.delete_secret(session, secret_id)
