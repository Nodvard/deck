"""Verschluesselungskern des Secrets-Vaults -- Fernet + Key-Versionierung (D-06).

WP-1 baute hier nur einen einzelnen Master-Key fuer genau einen Zweck (TOTP-Secrets,
siehe der urspruengliche Modul-Docstring dieser Datei). WP-2 erweitert das um ein
Trio: Key-Rotation ueber Versionen (`Keyring`),
die `SecretHandle`/`vault_use()`-Abstraktion fuer (kuenftige) Extensions, und die
Audit-Pflicht bei jeder Materialisierung (docs/03-DATA-MODEL.md §3, Invarianten 2+3).

**Bewusste Design-Entscheidung, hier dokumentiert statt stillschweigend getroffen:**
`get_fernet()` und das alte `store_secret(session, fernet, ...)` aus WP-1 sind ENTFERNT,
nicht als Kompatibilitaets-Shim daneben liegen gelassen. Grund: ein einzelner,
versionsloser Fernet-Schluessel kann `Secret.key_version` nicht respektieren -- nach
einer Rotation haette `get_fernet()` weiterhin nur den NEUESTEN Schluessel geliefert,
und jedes vor der Rotation verschluesselte Secret (z. B. ein TOTP-Secret aus WP-1) waere
beim naechsten Login-Versuch nicht mehr entschluesselbar gewesen -- ein latenter Bug,
der erst beim ERSTEN Rotationsaufruf sichtbar geworden waere. Die neuen Funktionen
`create_secret`/`read_secret_plaintext`/`vault_use` sind deshalb alle Keyring- (also
versions-) bewusst; `services/auth.py` (TOTP-Flow) wurde entsprechend umgestellt.
`encrypt_str`/`decrypt_str`/`load_or_create_master_key` bleiben unveraendert -- reine
Krypto-Bausteine ohne Versionsbezug, die die neuen Funktionen intern weiterverwenden.

**Migration von WP-1-Daten:** `load_keyring()` uebernimmt einen bereits existierenden
`master.key` unveraendert als Version 1 des Keyrings.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db import utcnow
from ..models import Secret
from . import audit

Keyring = dict[int, bytes]


class VaultError(Exception):
    pass


def load_or_create_master_key(path) -> bytes:  # noqa: ANN001 - Path, siehe config.py
    """Laedt den Fernet-Schluessel oder erzeugt ihn beim allerersten Start.

    `0600`-Rechte werden per `os.chmod` gesetzt -- unter Windows (dieser
    Entwicklungsrechner) ist das wirkungslos, da NTFS-ACLs eigene Rechteverwaltung
    haben; der Aufruf ist trotzdem harmlos und wird auf echten Linux-Zielsystemen
    (Raspberry Pi bzw. kleiner x86-Rechner, docs/00 D-10) tatsaechlich wirksam.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_bytes()

    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
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


# ---------------------------------------------------------------------------
# Keyring: versionierte Schluessel fuer Rotation ohne Big-Bang (docs/00 D-06)
# ---------------------------------------------------------------------------


def load_keyring(settings: Settings) -> Keyring:
    path = settings.vault_keyring_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {int(version): key.encode("ascii") for version, key in raw.items()}

    # Erster Zugriff ueberhaupt (oder Umstieg von WP-1): Version 1 uebernimmt den
    # bestehenden Master-Key unveraendert, damit vor WP-2 verschluesselte Secrets ohne
    # Re-Encryption lesbar bleiben.
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
    """Fuegt eine neue, hoehere Schluesselversion hinzu. Reine Funktion (Rotation als
    Funktion, UI spaeter) -- schluesselt KEINE bestehende Secret-Zeile
    um; das passiert lazy beim naechsten Zugriff (siehe `_decrypt_and_maybe_migrate`,
    docs/03-DATA-MODEL.md §3: "neue Secrets mit v2, alte werden beim naechsten Zugriff
    migriert")."""
    keyring = load_keyring(settings)
    new_version = current_version(keyring) + 1
    keyring[new_version] = Fernet.generate_key()
    _save_keyring(settings.vault_keyring_path, keyring)
    return keyring


# ---------------------------------------------------------------------------
# Secrets: anlegen, lesen, ersetzen, loeschen
# ---------------------------------------------------------------------------


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
    )
    session.add(secret)
    await session.flush()
    return secret


async def replace_secret_value(
    session: AsyncSession, keyring: Keyring, secret_id: str, plaintext: str
) -> Secret:
    """Ersetzt den Wert eines bestehenden Secrets -- Invariante 1 erlaubt genau das
    ('Schreiben und Ersetzen ja, Lesen nein', docs/03 §3). Verschluesselt immer mit der
    aktuellen Schluesselversion, unabhaengig davon, mit welcher Version das Secret zuvor
    lag."""
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
        # Lazy-Migration bei Zugriff, kein Big-Bang-Umschluesseln aller Zeilen.
        secret.ciphertext = encrypt_str(fernet_for_version(keyring, latest), plaintext)
        secret.key_version = latest
        await session.flush()
    return plaintext


async def read_secret_plaintext(session: AsyncSession, keyring: Keyring, secret_id: str) -> str:
    """NUR fuer serverseitige Verifikation (z. B. TOTP-Codepruefung) -- schreibt KEINEN
    Audit-Eintrag, im Unterschied zu `vault_use()` (siehe dortiger Docstring fuer die
    Begruendung der Trennung). Niemals aus einem API-Endpunkt heraus aufrufen, der den
    Rueckgabewert weiterreicht (docs/03-DATA-MODEL.md §3, Invariante 1)."""
    secret = await session.get(Secret, secret_id)
    if secret is None:
        raise VaultError(f"Secret '{secret_id}' nicht gefunden")
    return await _decrypt_and_maybe_migrate(session, keyring, secret)


async def delete_secret(session: AsyncSession, secret_id: str) -> None:
    secret = await session.get(Secret, secret_id)
    if secret is not None:
        await session.delete(secret)


# ---------------------------------------------------------------------------
# SecretHandle + vault_use -- der auditierte, extension-taugliche Weg
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecretHandle:
    """Eine Referenz, kein Wert -- entspricht `SecretHandleRef` im SDK-Vertrag
    (sdk/python/nodvard_sdk/context.py). Absichtlich ohne `ciphertext`-Feld: ein Handle
    kann strukturell gar keinen Wert transportieren."""

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
    """Materialisiert den Klartext NUR innerhalb des Blocks (docs/03 §3, Invariante 2)
    -- entspricht `ExtensionContext.vault_use()` im SDK-Vertrag. Jeder Aufruf schreibt
    eine `secret.used`-Audit-Zeile mit Label und Verwender, nie mit dem Wert
    (Invariante 3).

    Anders als `read_secret_plaintext()` (interner Helfer ohne Audit-Pflicht) ist DIES
    der Weg, der auditiert -- die Trennung ist Absicht: einen TOTP-Codecheck bei jedem
    Login-Versuch zu auditieren waere Rauschen ohne Sicherheitswert; eine Extension, die
    ein Secret an sich zieht, ist der Fall, den docs/03 §3 tatsaechlich meint.

    **Isolations-Fix (WP-3, eingeloest gegen den ersten echten Aufrufer -- den
    Extension-Host, Auflage aus Commit 1ee1dbe):** Dass ein Secret materialisiert wurde,
    ist eine Tatsache, die unabhaengig davon bestehen bleiben muss, was der
    Aufrufer-Code NACH `yield` tut. Eine Extension, die nach dem Auslesen eines Secrets
    an einer voellig anderen Stelle scheitert, darf den `secret.used`-Nachweis nicht mit
    in ihren eigenen Rollback reissen -- sonst waere ausgerechnet die Verwendung eines
    Secrets der einzige Vorgang im System, dessen Audit-Spur vom Erfolg des Aufrufers
    abhaengt. Deshalb laufen alle schreibenden Nebenwirkungen (Lazy-Key-Migration,
    `last_used_at`, die Audit-Zeile) NICHT in der von der Aufrufer-Session `session`
    geteilten Transaktion, sondern in einer eigenen, sofort committenden Session ueber
    `db.session.get_sessionmaker()` -- bewusst NICHT dasselbe Mittel wie
    `services.auth._commit_before_raising()`: dort war ein Zwischen-Commit auf der
    GETEILTEN Session sicher, weil an den drei betroffenen Stellen nachweislich nichts
    anderes im Transaktionspuffer stand. `vault_use()` ist dagegen eine generische
    Primitive; beliebiger Aufrufer-Code kann zwischen `yield` und einem eigenen Fehler
    eigene, voneinander unabhaengige Schreibvorgaenge machen, die ein Commit auf der
    GETEILTEN Session faelschlich mit festschreiben wuerde. Nur eine echte zweite
    Verbindung vermeidet das. `session` selbst dient nur noch dem lesenden Zugriff auf
    die Ciphertext-Spalte.

    Getestet gegen eine echte Datei-SQLite-DB mit zwei echten Verbindungen (nicht nur
    In-Memory/StaticPool, wo beide Sessions dieselbe physische Verbindung teilen wuerden
    und der Test nichts bewiesen haette): siehe
    test_vault_use_audit_survives_caller_session_rollback in test_core_vault.py, und
    live gegen den echten Extension-Host in extensions/hello-world (Boot-Test)."""
    from ..db.session import get_sessionmaker

    secret = await session.get(Secret, handle.id)
    if secret is None:
        raise VaultError(f"Secret '{handle.id}' nicht gefunden")

    plaintext = decrypt_str(fernet_for_version(keyring, secret.key_version), secret.ciphertext)
    latest = current_version(keyring)
    label = secret.label

    async with get_sessionmaker()() as durable_session:
        durable_secret = await durable_session.get(Secret, handle.id)
        if durable_secret is not None:
            if durable_secret.key_version != latest:
                durable_secret.ciphertext = encrypt_str(
                    fernet_for_version(keyring, latest), plaintext
                )
                durable_secret.key_version = latest
            durable_secret.last_used_at = utcnow()
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

    yield plaintext
