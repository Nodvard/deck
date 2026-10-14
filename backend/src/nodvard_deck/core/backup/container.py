"""Eine Sicherungsdatei als Ganzes schreiben und lesen: Kopf + age(tar.gz), als Strom ohne die ganze Datei im Speicher."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from . import crypto, snapshot
from . import format as fmt
from .errors import WrongSecret

Encryptor = Callable[[BinaryIO, BinaryIO], None]


def encryptor_for_recipient(recipient: str) -> Encryptor:
    return lambda reader, writer: crypto.encrypt_to_recipient(reader, writer, recipient)


def encryptor_for_password(password: str, *, log_n: int | None = None) -> Encryptor:
    return lambda reader, writer: crypto.encrypt_with_passphrase(reader, writer, password, log_n=log_n)


def write_backup(
    out: BinaryIO,
    *,
    header: dict[str, Any],
    sources: snapshot.Sources,
    tmp_dir: Path,
    encrypt: Encryptor,
    build: str | None,
    instance_id: str,
    jwt_from_env: bool,
    extensions_dir: Path | None,
    on_skipped_links: Callable[[list[str]], None] | None = None,
) -> dict[str, Any]:
    """Schreibt die komplette Datei nach `out` und gibt das Manifest zurück; scheitert etwas, muss der Aufrufer `out`
    verwerfen. `on_skipped_links` bekommt die Pfade der Verknüpfungen, die nicht mitgesichert wurden."""
    # Der einzige Klartext auf der Platte ist diese kurzlebige Datenbank-Kopie im Temp-Ordner unter dem Datenordner (0700),
    # nie im Zielordner.
    db_copy = tmp_dir / "lattice.db"
    snapshot.online_copy(sources.db_path, db_copy)
    heads, extensions = snapshot.db_facts(db_copy, extensions_dir)
    collected = snapshot.collect_all(sources)
    entries = collected.entries
    if on_skipped_links is not None:
        on_skipped_links(collected.skipped_links)

    def manifest_for(db_info: dict, files: list[dict]) -> dict:
        return fmt.build_manifest(
            header=header, build=build, instance_id=instance_id, jwt_from_env=jwt_from_env,
            db={**db_info, "alembic_heads": heads}, extensions=extensions, files=files,
        )

    out.write(fmt.encode_header(header))
    result: dict[str, Any] = {}

    def produce(pipe: BinaryIO) -> None:
        result["manifest"] = snapshot.write_archive(pipe, db_copy=db_copy, entries=entries, manifest_for=manifest_for)

    snapshot.run_pipeline(produce, lambda pipe: encrypt(pipe, out))
    return result["manifest"]


def decryptor_for(header: dict[str, Any], secret: str) -> Encryptor:
    """Wählt zum Modus passend Einmal-Passwort, Sicherungspasswort (KDF aus dem Kopf, mit Deckeln) oder
    Wiederherstellungsschlüssel."""
    if header["mode"] == "passwort":
        return lambda reader, writer: crypto.decrypt_with_passphrase(reader, writer, secret)
    if crypto.is_recovery_key(secret):
        identity = secret.strip().upper()
    else:
        key = crypto.derive_key(secret, crypto.KdfParams.from_dict(header.get("kdf")))
        # Eine andere Kennung heißt: falsches Passwort. So kommt die klare Meldung, bevor der Strom angefasst wird.
        if key.key_id != header.get("key_id"):
            raise WrongSecret()
        identity = key.identity
    return lambda reader, writer: crypto.decrypt_with_identity(reader, writer, identity)


def read_backup(reader: BinaryIO, secret: str, dest: Path, **extract: Any) -> dict[str, Any]:
    """Liest, entschlüsselt und prüft eine Sicherung vollständig nach `dest`; `extract` geht an `snapshot.extract_archive`.

    Schon vor dem Ende der Prüfung liegen Dateien in `dest`: nur nach erfolgreicher Rückkehr verwenden, sonst wegräumen."""
    header = fmt.read_header(reader)
    decrypt = decryptor_for(header, secret)
    return snapshot.run_pipeline(
        lambda pipe: decrypt(reader, pipe),
        lambda pipe: snapshot.extract_archive(pipe, dest, header, **extract),
    )
