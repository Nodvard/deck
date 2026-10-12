"""Fernet-Verschlüsselung der Secrets mit versioniertem Keyring (docs/03-DATA-MODEL.md §3).

Kein Endpunkt gibt Klartext zurück: Er entsteht nur innerhalb von `vault_use()` und wird dort ohne Wert protokolliert.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db import utcnow
from ..models import Secret, SecretUsage
from . import audit

Keyring = dict[int, bytes]

USE_NOTE_INTERVAL = timedelta(hours=1)
"""Höchstens so oft je Secret und Verwender hält `vault_use()` die Nutzung fest (`last_used_at`, `secret_usages`, Audit
`secret.used`); Module fragen alle paar Sekunden ab, und eine Zeile je Zugriff flutete Protokoll und Datenbank."""

_now = utcnow
"""Die Uhr für die Drosselung, in Tests ersetzbar."""

_noted: dict[tuple[str, str, str], datetime] = {}
"""(Secret, Art, Verwender) -> wann die Nutzung zuletzt festgehalten wurde (nur dieser Prozess); spart die Abfrage
und hält zwei gleichzeitige Zugriffe vom doppelten Schreiben ab. Nach einem Neustart leer, dann gilt `secret_usages`."""

_deciding: set[tuple[str, str, str]] = set()
"""Kennungen, für die gerade ein Zugriff in `secret_usages` nachsieht; ein gleichzeitiger zweiter hält nichts fest."""


class VaultError(Exception):
    pass


def load_or_create_master_key(path) -> bytes:  # noqa: ANN001 - Path, siehe config.py
    """Lädt den Fernet-Schlüssel oder erzeugt ihn beim allerersten Start (Rechte 0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_bytes()

    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        # Unter Windows wirkungslos (NTFS-ACLs), auf den Linux-Zielsystemen wirksam.
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def encrypt_str(fernet: Fernet, plaintext: str) -> bytes:
    return fernet.encrypt(plaintext.encode("utf-8"))


def decrypt_str(fernet: Fernet, ciphertext: bytes) -> str:
    try:
        return fernet.decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise VaultError("Entschlüsselung fehlgeschlagen (falscher Master-Key?)") from exc


# Keyring: versionierte Schlüssel, damit eine Rotation nicht alle Zeilen auf einmal umschlüsseln muss.
def load_keyring(settings: Settings) -> Keyring:
    path = settings.vault_keyring_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {int(version): key.encode("ascii") for version, key in raw.items()}

    # Erster Zugriff überhaupt: Version 1 ist der bestehende Master-Key, damit ältere Secrets lesbar bleiben.
    key = load_or_create_master_key(settings.master_key_path)
    keyring: Keyring = {1: key}
    _save_keyring(path, keyring)
    return keyring


def _save_keyring(path, keyring: Keyring) -> None:  # noqa: ANN001 - Path
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = {str(version): key.decode("ascii") for version, key in keyring.items()}
    path.write_text(json.dumps(raw), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def current_version(keyring: Keyring) -> int:
    return max(keyring)


def fernet_for_version(keyring: Keyring, version: int) -> Fernet:
    try:
        return Fernet(keyring[version])
    except KeyError as exc:
        raise VaultError(f"Kein Schlüssel für key_version={version} im Keyring") from exc


def rotate_master_key(settings: Settings) -> Keyring:
    """Fügt eine neue, höhere Schlüsselversion hinzu; bestehende Zeilen folgen erst beim nächsten Zugriff."""
    keyring = load_keyring(settings)
    new_version = current_version(keyring) + 1
    keyring[new_version] = Fernet.generate_key()
    _save_keyring(settings.vault_keyring_path, keyring)
    return keyring


async def create_secret(
    session: AsyncSession,
    keyring: Keyring,
    *,
    label: str,
    kind: str,
    plaintext: str,
    owner_ext_id: str | None = None,
    created_by_user_id: str | None = None,
    description: str = "",
    expires_at: datetime | None = None,
) -> Secret:
    version = current_version(keyring)
    secret = Secret(
        label=label,
        kind=kind,
        ciphertext=encrypt_str(fernet_for_version(keyring, version), plaintext),
        key_version=version,
        owner_ext_id=owner_ext_id,
        created_by_user_id=created_by_user_id,
        description=description,
        expires_at=expires_at,
    )
    session.add(secret)
    await session.flush()
    return secret


async def replace_secret_value(
    session: AsyncSession, keyring: Keyring, secret_id: str, plaintext: str
) -> Secret:
    """Ersetzt den Wert eines Secrets (Schreiben ist erlaubt, Lesen nie), immer mit der aktuellen Schlüsselversion."""
    secret = await session.get(Secret, secret_id)
    if secret is None:
        raise VaultError(f"Secret '{secret_id}' nicht gefunden")
    version = current_version(keyring)
    secret.ciphertext = encrypt_str(fernet_for_version(keyring, version), plaintext)
    secret.key_version = version
    secret.rotated_at = utcnow()
    await session.flush()
    return secret


async def _decrypt_and_maybe_migrate(
    session: AsyncSession, keyring: Keyring, secret: Secret
) -> str:
    plaintext = decrypt_str(fernet_for_version(keyring, secret.key_version), secret.ciphertext)
    latest = current_version(keyring)
    if secret.key_version != latest:
        # Beim Zugriff umschlüsseln, nicht alle Zeilen auf einmal.
        secret.ciphertext = encrypt_str(fernet_for_version(keyring, latest), plaintext)
        secret.key_version = latest
        await session.flush()
    return plaintext


async def read_secret_plaintext(session: AsyncSession, keyring: Keyring, secret_id: str) -> str:
    """Klartext nur für serverseitige Prüfungen (z. B. TOTP); nie aus einem Endpunkt, der den Wert weitergibt."""
    # Ohne Audit-Eintrag (anders als `vault_use()`, der Weg für Extensions): Einer je TOTP-Prüfung wäre Rauschen.
    secret = await session.get(Secret, secret_id)
    if secret is None:
        raise VaultError(f"Secret '{secret_id}' nicht gefunden")
    return await _decrypt_and_maybe_migrate(session, keyring, secret)


async def delete_secret(session: AsyncSession, secret_id: str) -> None:
    secret = await session.get(Secret, secret_id)
    if secret is not None:
        # Die Fremdschlüssel-Kaskade räumt `secret_usages` auch ab; ausdrücklich, damit es nicht an
        # `PRAGMA foreign_keys` hängt.
        await session.execute(delete(SecretUsage).where(SecretUsage.secret_id == secret_id))
        await session.delete(secret)


@dataclass(frozen=True)
class SecretHandle:
    """Eine Referenz ohne Wert (wie `SecretHandleRef` im SDK); ohne `ciphertext`-Feld kann sie keinen Wert tragen."""

    id: str
    label: str
    kind: str


async def get_handle(session: AsyncSession, label: str) -> SecretHandle | None:
    result = await session.execute(select(Secret).where(Secret.label == label))
    secret = result.scalar_one_or_none()
    if secret is None:
        return None
    return SecretHandle(id=secret.id, label=secret.label, kind=secret.kind)


@asynccontextmanager
async def vault_use(
    session: AsyncSession,
    keyring: Keyring,
    handle: SecretHandle,
    *,
    actor_type: str,
    actor_id: str,
    correlation_id: str | None = None,
) -> AsyncIterator[str]:
    """Gibt den Klartext nur innerhalb des Blocks heraus und hält die Nutzung ohne Wert fest (Audit `secret.used`,
    `last_used_at`, `secret_usages`), je Secret und Verwender höchstens einmal je `USE_NOTE_INTERVAL`."""
    from ..db.session import get_sessionmaker

    secret = await session.get(Secret, handle.id)
    if secret is None:
        raise VaultError(f"Secret '{handle.id}' nicht gefunden")

    plaintext = decrypt_str(fernet_for_version(keyring, secret.key_version), secret.ciphertext)
    latest = current_version(keyring)
    label = secret.label
    now = _now()
    key = (handle.id, actor_type, actor_id)
    note = await _use_note_due(session, key, now)
    migrate = secret.key_version != latest

    # Geschrieben wird in einer eigenen, sofort committenden Session (zweite Verbindung): Dass ein Secret materialisiert
    # wurde, muss auch dann festgehalten bleiben, wenn der Aufrufer nach `yield` scheitert und zurückrollt. Ein
    # Zwischen-Commit auf `session` risse dessen offene, fremde Schreibvorgänge mit fest; `session` dient nur dem Lesen.
    # Test: `test_vault_use_audit_survives_caller_session_rollback` (echte Datei-DB, zwei Verbindungen).
    if note or migrate:
        if note:
            # Vor dem ersten `await` des Schreibens: ein gleichzeitiger Zugriff sieht den Eintrag schon.
            _noted[key] = now
        try:
            async with get_sessionmaker()() as durable_session:
                durable_secret = await durable_session.get(Secret, handle.id)
                if durable_secret is not None:
                    if durable_secret.key_version != latest:
                        durable_secret.ciphertext = encrypt_str(
                            fernet_for_version(keyring, latest), plaintext
                        )
                        durable_secret.key_version = latest
                    if note:
                        durable_secret.last_used_at = now
                        usage = await durable_session.get(SecretUsage, key)
                        if usage is None:
                            durable_session.add(SecretUsage(
                                secret_id=handle.id, consumer_type=actor_type, consumer_id=actor_id,
                                first_used_at=now, last_used_at=now,
                            ))
                        else:
                            usage.last_used_at = now
                        await audit.write_entry(
                            durable_session,
                            actor_type=actor_type,
                            actor_id=actor_id,
                            action="secret.used",
                            outcome="success",
                            target_type="secret",
                            target_id=handle.id,
                            detail={"label": label},
                            correlation_id=correlation_id,
                        )
                    await durable_session.commit()
        except BaseException:
            if note and _noted.get(key) == now:
                # Nichts festgehalten: der nächste Zugriff versucht es wieder, statt eine Stunde zu schweigen.
                del _noted[key]
            raise

    yield plaintext


async def _use_note_due(session: AsyncSession, key: tuple[str, str, str], now: datetime) -> bool:
    """Ob die Nutzung wieder festzuhalten ist (`USE_NOTE_INTERVAL`), nach Prozessstand, sonst nach `secret_usages`."""
    last = _noted.get(key)
    if last is None:
        if key in _deciding:
            return False
        # Zwischen dieser Antwort und dem Merken in `vault_use` liegt kein `await`; `_deciding` hält währenddessen
        # einen zweiten Zugriff fern (sonst schrieben beide).
        _deciding.add(key)
        try:
            last = await _stored_last_use(session, key)
        finally:
            _deciding.discard(key)
        if last is not None:
            _noted[key] = last
    # Geht die Uhr zurück (`now < last`), gilt die Nutzung als fällig.
    return last is None or now < last or now - last >= USE_NOTE_INTERVAL


async def _stored_last_use(session: AsyncSession, key: tuple[str, str, str]) -> datetime | None:
    """`last_used_at` aus `secret_usages`, frisch aus der Datenbank (nicht aus der Identity-Map des Aufrufers)."""
    secret_id, consumer_type, consumer_id = key
    result = await session.execute(
        select(SecretUsage.last_used_at).where(
            SecretUsage.secret_id == secret_id,
            SecretUsage.consumer_type == consumer_type,
            SecretUsage.consumer_id == consumer_id,
        )
    )
    return result.scalar_one_or_none()
