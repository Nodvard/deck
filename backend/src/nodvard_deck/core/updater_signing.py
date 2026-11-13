"""Schlüssel der Installation für Anforderungen an den Update-Helfer."""

from __future__ import annotations

import contextlib
import os
import secrets
import stat
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

KEY_NAME = "updater-ed25519.key"


def load_key(data_dir: Path) -> Ed25519PrivateKey:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(Path(data_dir) / KEY_NAME, flags)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 32
                or (os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o077))):
            raise ValueError("Ungültiger Update-Schlüssel")
        return Ed25519PrivateKey.from_private_bytes(os.read(fd, 33))
    finally:
        os.close(fd)


def public_key(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def prepare_key(data_dir: Path) -> bytes:
    """Legt den Schlüssel einmalig an und gibt den öffentlichen Teil zurück; ein vorhandener bleibt unangetastet.

    Erst eine Temp-Datei schreiben, dann per `link` an den Namen hängen: Ein Abbruch hinterlässt keinen halben
    Schlüssel, und `link` überschreibt nie (anders als `rename`)."""
    path = Path(data_dir) / KEY_NAME
    if path.exists() or path.is_symlink():
        _drop_stale_temp_names(path)
        return public_key(load_key(data_dir))
    key = Ed25519PrivateKey.generate()
    tmp = path.with_name(f".{KEY_NAME}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            raw = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
            view = memoryview(raw)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        with contextlib.suppress(FileExistsError):  # gleichzeitig angelegt: der vorhandene gilt
            os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    _fsync_dir(path.parent)
    return public_key(load_key(data_dir))


def _drop_stale_temp_names(path: Path) -> None:
    """Stirbt der Prozess zwischen `link` und dem Löschen der Temp-Datei, hat der Schlüssel einen zweiten Namen
    und `load_key` lehnt ihn ab. Entfernt wird nur ein eigener Temp-Name, der auf genau diese Datei zeigt."""
    try:
        key = path.lstat()
    except OSError:
        return
    if not stat.S_ISREG(key.st_mode):
        return
    for tmp in path.parent.glob(f".{KEY_NAME}.*.tmp"):
        with contextlib.suppress(OSError):
            other = tmp.lstat()
            if stat.S_ISREG(other.st_mode) and (other.st_dev, other.st_ino) == (key.st_dev, key.st_ino):
                tmp.unlink()


def _fsync_dir(directory: Path) -> None:
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> None:
    from ..config import get_settings

    print(prepare_key(get_settings().data_dir).hex())


if __name__ == "__main__":
    main()
