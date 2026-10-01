"""Ablage der automatischen Sicherungen: Zielordner, Schreiben, Pruefsummen-Datei, Liste,
Pruefen, Loeschen, Rotation.

**Zielordner nur aus einer Allowlist:** `<Datenordner>/backups` oder ein eingebundener
Ordner `/backups` (bzw. darunter). `/app` gehoert dem Dienstnutzer -- ein frei waehlbarer
Ordner waere ein Schreibweg in den Programmcode. Geprueft wird der angegebene Pfad Teil fuer
Teil: absolut, kein `..`, kein Symlink, `realpath` gleich dem Pfad, beschreibbar.

**Dateien:** `nodvard-deck-sicherung-*.ndbak` (0600) und daneben `<name>.json` mit sha256
und Groesse der ganzen Datei (Bitfaeule erkennen, ohne Passwort). Geschrieben wird erst nach
`.<name>.part` und danach umbenannt; eine halbe Datei traegt nie den echten Namen.
Rotation und Loeschen fassen nur Dateien mit unserem Namensmuster UND gueltigem Kopf an.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from . import format as fmt
from .errors import BackupError, DamagedBackup

EXTERNAL_ROOT = Path("/backups")
"""Eingebundener Ordner fuer Sicherungen ausserhalb des Datenordners (Compose: `./sicherungen:/backups`)."""

DIR_MODE = 0o700
FILE_MODE = 0o600
_READ = 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class TargetNotAllowed(BackupError):
    pass


def default_dir(data_dir: Path) -> Path:
    return Path(os.path.realpath(data_dir)) / "backups"


def _check_components(root: Path, target: Path, *, create: bool) -> None:
    """Jeder Teil von `root` bis `target` muss ein echter Ordner sein (kein Symlink)."""
    current = root
    parts = target.relative_to(root).parts
    for index in range(len(parts) + 1):
        if index:
            current = current / parts[index - 1]
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            if not create or current == EXTERNAL_ROOT:
                raise TargetNotAllowed(f"Der Ordner {current} existiert nicht (ist er eingebunden?).") from None
            os.mkdir(current, DIR_MODE)
            st = os.lstat(current)
        if stat.S_ISLNK(st.st_mode):
            raise TargetNotAllowed(f"{current} ist eine Verknüpfung (Symlink). Bitte einen echten Ordner angeben.")
        if not stat.S_ISDIR(st.st_mode):
            raise TargetNotAllowed(f"{current} ist kein Ordner.")


def resolve_target(raw: str | None, data_dir: Path, *, create: bool = True) -> Path:
    """Prueft einen Zielordner gegen die Allowlist und gibt ihn zurueck (legt
    `<Datenordner>/backups` bzw. Unterordner von `/backups` bei Bedarf mit 0700 an)."""
    default = default_dir(data_dir)
    if raw is None or raw == "":
        raw = str(default)
    if not isinstance(raw, str) or "\x00" in raw or not raw.startswith("/"):
        raise TargetNotAllowed("Bitte einen absoluten Ordnerpfad angeben.")
    pure = PurePosixPath(raw)
    if any(part in ("..", ".") for part in raw.split("/")):
        raise TargetNotAllowed("Der Pfad darf kein „..“ enthalten.")
    target = Path(str(pure))
    if target == default:
        root = default.parent
    elif target == EXTERNAL_ROOT or EXTERNAL_ROOT in target.parents:
        root = EXTERNAL_ROOT
    else:
        raise TargetNotAllowed(
            f"Sicherungen dürfen nur nach {default} oder in den eingebundenen Ordner {EXTERNAL_ROOT} geschrieben werden."
        )
    _check_components(root, target, create=create)
    if os.path.realpath(target) != str(target):
        raise TargetNotAllowed(f"{target} zeigt über eine Verknüpfung woandershin.")
    if not os.access(target, os.W_OK | os.X_OK):
        raise TargetNotAllowed(f"In {target} darf Nodvard Deck nicht schreiben.")
    if target == default:
        with contextlib.suppress(OSError):
            os.chmod(target, DIR_MODE)
    return target


def same_storage(a: Path, b: Path) -> bool:
    """Liegen beide auf demselben Dateisystem? Dann schuetzt die Sicherung vor
    Fehlbedienung, aber nicht vor einem Plattendefekt."""
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return True


def free_bytes(path: Path) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def make_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    with contextlib.suppress(OSError):
        os.chmod(path, DIR_MODE)
    return path


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------


class _HashingWriter:
    def __init__(self, fh: BinaryIO) -> None:
        self._fh = fh
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> int:
        self._fh.write(data)
        self.digest.update(data)
        self.size += len(data)
        return len(data)

    def flush(self) -> None:
        self._fh.flush()


def _fsync_dir(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_file(directory: Path, name: str, produce: Callable[[Any], None]) -> tuple[Path, str, int]:
    """Schreibt atomar `directory/name` (0600) ueber `.name.part` und liefert (Pfad, sha256,
    Groesse). Scheitert `produce`, bleibt nichts liegen."""
    final = directory / name
    part = directory / f".{name}.part"
    if os.path.lexists(final):
        raise BackupError(f"{name} gibt es schon. Bitte einen Moment warten und erneut versuchen.")
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, FILE_MODE)
    try:
        with os.fdopen(fd, "wb") as fh:
            writer = _HashingWriter(fh)
            produce(writer)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(part, final)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(part)
        raise
    _fsync_dir(directory)
    return final, writer.digest.hexdigest(), writer.size


def write_sidecar(path: Path, data: dict[str, Any]) -> None:
    sidecar = path.with_name(path.name + fmt.SIDECAR_SUFFIX)
    tmp = path.with_name(f".{sidecar.name}.part")
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, sort_keys=True, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, sidecar)


def read_sidecar(path: Path) -> dict[str, Any] | None:
    sidecar = path.with_name(path.name + fmt.SIDECAR_SUFFIX)
    try:
        fd = os.open(sidecar, os.O_RDONLY | _NOFOLLOW)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as fh:
            data = json.loads(fh.read(64 * 1024))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Lesen, Pruefen, Loeschen, Rotation
# ---------------------------------------------------------------------------


def backup_path(directory: Path, name: str) -> Path:
    """Pfad einer vorhandenen Sicherung; nur unser Namensmuster, nur normale Dateien."""
    if not fmt.is_backup_name(name):
        raise FileNotFoundError(name)
    path = directory / name
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise FileNotFoundError(name)
    return path


@contextlib.contextmanager
def open_backup(path: Path) -> Iterator[BinaryIO]:
    fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        yield fh


def read_header(path: Path) -> dict[str, Any]:
    with open_backup(path) as fh:
        return fmt.read_header(fh)


def _iter_ours(directory: Path) -> Iterator[tuple[str, Path]]:
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return
    for entry in entries:
        if fmt.is_backup_name(entry.name) and entry.is_file(follow_symlinks=False):
            yield entry.name, Path(entry.path)


def list_backups(directory: Path) -> list[dict[str, Any]]:
    """Neueste zuerst. `status`: `ok` (Groesse passt zur Pruefsummen-Datei), `ungeprueft`
    (keine Pruefsummen-Datei), `beschaedigt` (Kopf unlesbar oder Groesse falsch). Ob der
    Inhalt Bit fuer Bit stimmt, sagt erst `verify` (Ergebnis in `checked_at`/`check_ok`)."""
    out: list[dict[str, Any]] = []
    for name, path in _iter_ours(directory):
        item: dict[str, Any] = {"name": name, "size": os.lstat(path).st_size}
        try:
            header = read_header(path)
        except BackupError:
            item.update(status="beschaedigt", created_at=None, app_version=None, key_id=None, mode=None)
            out.append(item)
            continue
        item.update(
            created_at=header["created_at"], app_version=header["app_version"],
            key_id=header.get("key_id"), mode=header["mode"],
        )
        sidecar = read_sidecar(path)
        if sidecar is None:
            item["status"] = "ungeprueft"
        elif sidecar.get("size") != item["size"]:
            item["status"] = "beschaedigt"
        else:
            item["status"] = "ok"
        if sidecar:
            item["checked_at"] = sidecar.get("checked_at")
            item["check_ok"] = sidecar.get("check_ok")
            if sidecar.get("check_ok") is False:
                item["status"] = "beschaedigt"
        out.append(item)
    out.sort(key=lambda i: i["name"], reverse=True)
    return out


def verify(path: Path, *, now_iso: str) -> dict[str, Any]:
    """sha256 der ganzen Datei gegen die Pruefsummen-Datei (ohne Passwort)."""
    sidecar = read_sidecar(path)
    if sidecar is None or not isinstance(sidecar.get("sha256"), str):
        return {"ok": False, "detail": "Keine Prüfsummen-Datei gefunden, Prüfen ist nicht möglich."}
    digest = hashlib.sha256()
    size = 0
    with open_backup(path) as fh:
        while chunk := fh.read(_READ):
            digest.update(chunk)
            size += len(chunk)
    ok = digest.hexdigest() == sidecar["sha256"] and size == sidecar.get("size")
    sidecar.update(checked_at=now_iso, check_ok=ok)
    write_sidecar(path, sidecar)
    detail = "Die Datei ist unverändert." if ok else "Die Datei hat sich verändert (beschädigt)."
    return {"ok": ok, "detail": detail}


def delete(path: Path) -> None:
    """Nur unsere Dateien mit lesbarem Kopf (sonst koennte ein falscher Name etwas Fremdes
    treffen); die Pruefsummen-Datei verschwindet mit."""
    try:
        read_header(path)
    except DamagedBackup:
        # Kaputter Kopf: eine beschaedigte eigene Datei darf man trotzdem loswerden, solange
        # Name und Pruefsummen-Datei zu uns gehoeren.
        if read_sidecar(path) is None:
            raise
    os.unlink(path)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path.with_name(path.name + fmt.SIDECAR_SUFFIX))


def rotate(directory: Path, keep: int) -> list[str]:
    """Behaelt die `keep` neuesten eigenen Sicherungen, loescht aeltere. Fremde Dateien
    und Dateien mit unlesbarem Kopf bleiben unangetastet.

    "Neu" heisst: `created_at` aus dem Kopf (UTC). Der Dateiname traegt die Ortszeit und
    sortiert nach einem Wechsel der Zeitzone (oder in der doppelten Stunde beim Ende der
    Sommerzeit) nicht mehr zuverlaessig -- sonst koennte die Rotation die NEUESTE loeschen."""
    ours: list[tuple[str, str, Path]] = []
    for name, path in _iter_ours(directory):
        try:
            header = read_header(path)
        except BackupError:
            continue
        ours.append((header["created_at"], name, path))
    ours.sort(reverse=True)
    deleted: list[str] = []
    for _created, name, path in ours[max(keep, 1):]:
        os.unlink(path)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path.with_name(name + fmt.SIDECAR_SUFFIX))
        deleted.append(name)
    return deleted


_OUR_PART_RE = re.compile(
    r"^\.(?:nodvard-deck-sicherung-\d{8}-\d{6}\.ndbak(?:\.json)?|[0-9a-f]{32}\.ndbak)\.part$"
)


def sweep_parts(directory: Path) -> None:
    """Uebrig gebliebene eigene `.part`-Dateien (Absturz mitten im Schreiben) wegraeumen.
    Nur unser Muster: in `/backups` koennen auch fremde Programme halbe Dateien ablegen."""
    for entry in list(os.scandir(directory)) if directory.is_dir() else []:
        if _OUR_PART_RE.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
            with contextlib.suppress(OSError):
                os.unlink(entry.path)
