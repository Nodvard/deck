"""Secrets-Metadaten -- docs/04-API.md §3, docs/03-DATA-MODEL.md §3.

Strukturelle Umsetzung von Invariante 1: kein Response-Modell hier enthaelt
`ciphertext` oder Klartext -- auch nicht fuer den Owner. `SecretOut` listet explizit
nur die erlaubten Felder, statt das ORM-Objekt ungefiltert zu serialisieren (genau DAS
waere der Fehler, den diese Datei strukturell verhindern soll). Lesen liefert nur
Metadaten, Schreiben/Ersetzen ja -- Invariante 1 woertlich.

Bewusst NICHT Teil dieser Runde: `secret_grants`-Verwaltung (wer darf ein Secret lesen)
-- relevant wird das erst, wenn Extensions (WP-3+) eigene Secrets anfordern.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...core import vault
from ...models import Secret, User
from ..deps import SessionDep, SettingsDep, require_permission

router = APIRouter(prefix="/secrets", tags=["secrets"])

SecretKind = Literal["password", "ssh_private_key", "api_token", "totp", "generic"]


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

    @classmethod
    def from_model(cls, secret: Secret) -> "SecretOut":
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
        )


class SecretCreate(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    kind: SecretKind
    value: str = Field(min_length=1)
    description: str = ""


class SecretValueUpdate(BaseModel):
    value: str = Field(min_length=1)


@router.get("", dependencies=[Depends(require_permission("secrets.read"))])
async def list_secrets(session: SessionDep) -> list[SecretOut]:
    result = await session.execute(select(Secret).order_by(Secret.label))
    return [SecretOut.from_model(s) for s in result.scalars().all()]


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
    )
    return SecretOut.from_model(secret)


LOGIN_SECRET_MESSAGE = (
    "Dieser Eintrag gehört zur Zwei-Faktor-Anmeldung eines Kontos und lässt sich hier weder "
    "ändern noch löschen. Abschalten geht unter „Mein Konto“ bzw. bei anderen Nutzern unter "
    "„Benutzer“ mit „Zwei-Faktor zurücksetzen“."
)


async def _refuse_login_secret(session: SessionDep, secret_id: str) -> None:
    """Der Schluessel der Zwei-Faktor-Anmeldung liegt im Tresor wie jedes andere Secret. Ueber
    diese Endpunkte ersetzt, haette jeder mit `secrets.write` einen eigenen Authenticator fuer
    ein fremdes Konto (auch das des Inhabers), ohne Passwortabfrage und am Protokoll von
    `user.2fa_reset` vorbei; geloescht, kaeme der Besitzer nicht mehr hinein."""
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


@router.delete(
    "/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("secrets.write"))],
)
async def delete_secret(secret_id: str, session: SessionDep) -> None:
    """Idempotent -- ein bereits geloeschtes/unbekanntes Secret ist kein Fehler,
    gleiche Haltung wie `services.auth.logout()` gegenueber einem unbekannten
    Refresh-Token."""
    await _refuse_login_secret(session, secret_id)
    await vault.delete_secret(session, secret_id)
