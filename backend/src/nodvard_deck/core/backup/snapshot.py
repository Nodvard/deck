"""Inhalt einer Sicherung erzeugen und wieder auspacken (synchron, laeuft in Threads).

**Online-Kopie der Datenbank:** `sqlite3.Connection.backup()` in EINEM Schritt (`pages=-1`).
Die Datenbank laeuft im WAL-Modus (db/session.py): eine Lesetransaktion sieht einen festen
Stand und blockiert keine Schreiber. Schrittweise Kopien (`pages=1024` mit Pausen) beginnen
dagegen bei jedem Schreibzugriff einer anderen Verbindung von vorn -- bei einem
beschaeftigten Dashboard im ungluecklichen Fall endlos. Danach `PRAGMA integrity_check` auf
der Kopie; scheitert er, entsteht keine Sicherung.

**tar-Strom:** gzip (Stufe 6) um ein unkomprimiertes tar-Stream-Archiv. Jede Datei wird
beim Einpacken gehasht (genau die Bytes, die im Archiv landen); das Manifest kommt deshalb
als LETZTER Eintrag. Erweiterungsdateien sind nicht transaktional: wird eine Datei waehrend
des Einpackens kuerzer, scheitert die Sicherung ehrlich, statt still Muell zu sichern.
"""

from __future__ import annotations

import errno
import gzip
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import tarfile
import threading
import urllib.parse
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, TypeVar

from . import format as fmt
from .errors import BackupError, BackupTooLarge, DamagedBackup, NotEnoughSpace

T = TypeVar("T")

_READ_SIZE = 1024 * 1024


class IntegrityCheckFailed(BackupError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"Die Datenbank-Kopie ist nicht in Ordnung ({detail}). Es wurde keine Sicherung geschrieben.")


# ---------------------------------------------------------------------------
# Quellen
# ---------------------------------------------------------------------------


@dataclass
class Sources:
    db_path: Path
    single_files: list[tuple[str, Path]] = field(default_factory=list)
    """(Name im Archiv unter files/, Pfad) -- z. B. ("master.key", data/master.key)."""
    trees: list[tuple[str, Path]] = field(default_factory=list)
    """(Praefix im Archiv unter files/, Ordner) -- z. B. ("ext", data/ext)."""


@dataclass(frozen=True)
class Entry:
    arcname: str
    path: Path
    size: int


@dataclass
class Collected:
    entries: list[Entry]
    skipped_links: list[str] = field(default_factory=list)
    """Verknuepfungen (Symlinks), die bewusst NICHT mitgesichert werden (Pfade wie im Archiv,
    ohne `files/`, z. B. `ext/beispiel/link`)."""


def collect_all(sources: Sources) -> Collected:
    """Alle regulaeren Dateien, ohne Symlinks, ohne die Ausschlussliste. Uebersprungene
    Symlinks (Dateien wie Ordner, auch einzelne Quelldateien) werden mitgezaehlt, damit der
    Aufrufer sie melden kann, statt sie still zu verlieren."""
    entries: list[Entry] = []
    skipped: list[str] = []
    for name, path in sources.single_files:
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            continue
        if fmt.is_excluded(PurePosixPath(name)):
            continue
        if stat.S_ISREG(st.st_mode):
            entries.append(Entry(fmt.ARC_FILES + name, path, st.st_size))
        elif stat.S_ISLNK(st.st_mode):
            skipped.append(name)
    for prefix, root in sources.trees:
        try:
            if not stat.S_ISDIR(os.lstat(root).st_mode):
                continue
        except FileNotFoundError:
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            kept = []
            for dirname in sorted(dirnames):
                full = os.path.join(dirpath, dirname)
                if os.path.islink(full):
                    rel_dir = PurePosixPath(prefix) / PurePosixPath(Path(full).relative_to(root).as_posix())
                    if not fmt.is_excluded(rel_dir):
                        skipped.append(rel_dir.as_posix())
                else:
                    kept.append(dirname)
            dirnames[:] = kept
            for filename in sorted(filenames):
                path = Path(dirpath) / filename
                rel = PurePosixPath(prefix) / PurePosixPath(path.relative_to(root).as_posix())
                if fmt.is_excluded(rel):
                    continue
                try:
                    st = os.lstat(path)
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(st.st_mode):
                    skipped.append(rel.as_posix())
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue  # Sockets, FIFOs
                arcname = fmt.ARC_FILES + rel.as_posix()
                if fmt.is_safe_member(arcname):
                    entries.append(Entry(arcname, path, st.st_size))
    return Collected(entries, skipped)


def collect(sources: Sources) -> list[Entry]:
    return collect_all(sources).entries


# ---------------------------------------------------------------------------
# Datenbank
# ---------------------------------------------------------------------------


def online_copy(src: Path, dst: Path) -> None:
    """Konsistente Kopie einer laufenden SQLite-Datenbank, danach Integritaetspruefung.
    Die Kopie steht im Journal-Modus DELETE (eine einzelne Datei, kein -wal daneben)."""
    source = sqlite3.connect(str(src), timeout=30)
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target, pages=-1)
        finally:
            target.close()
    finally:
        source.close()
    check = sqlite3.connect(str(dst))
    try:
        check.execute("PRAGMA journal_mode=DELETE")
        result = _integrity_result(check)
    finally:
        check.close()
    if result != ["ok"]:
        raise IntegrityCheckFailed("; ".join(str(r) for r in result[:3]))


def _integrity_result(conn: sqlite3.Connection) -> list[str]:
    return [str(row[0]) for row in conn.execute("PRAGMA integrity_check").fetchall()]


def integrity_check(path: Path) -> None:
    """`PRAGMA integrity_check` auf einer (fremden) Datei: nur lesend und unveraenderlich, und mit
    `trusted_schema=OFF` -- die Pruefung wertet CHECK-Bedingungen und Indexausdruecke aus, und die
    duerfen in einer fremden Datenbank keinen Funktionen vertrauen."""
    uri = "file:" + urllib.parse.quote(os.fspath(path)) + "?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.DatabaseError as exc:
        raise DamagedBackup("Datenbank unlesbar") from exc
    try:
        conn.execute("PRAGMA trusted_schema=OFF")
        result = _integrity_result(conn)
    except sqlite3.DatabaseError as exc:
        raise DamagedBackup("Datenbank unlesbar") from exc
    finally:
        conn.close()
    if result != ["ok"]:
        raise DamagedBackup("Datenbank nicht in Ordnung")


def db_facts(db_copy: Path, extensions_dir: Path | None) -> tuple[list[str], list[dict[str, Any]]]:
    """-> (alembic-Koepfe, [{id, version, alembic_heads}]) aus der KOPIE."""
    conn = sqlite3.connect(f"file:{db_copy}?mode=ro", uri=True)
    try:
        try:
            heads = sorted(r[0] for r in conn.execute("SELECT version_num FROM alembic_version"))
        except sqlite3.DatabaseError:
            heads = []
        try:
            rows = conn.execute("SELECT id, version FROM extensions ORDER BY id").fetchall()
        except sqlite3.DatabaseError:
            rows = []
    finally:
        conn.close()
    revisions = _extension_revisions(extensions_dir)
    extensions = [
        {"id": ext_id, "version": version, "alembic_heads": [h for h in heads if h in revisions.get(ext_id, set())]}
        for ext_id, version in rows
    ]
    return heads, extensions


_REVISION_RE = re.compile(r"^revision\s*(?::\s*str)?\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)


def _extension_revisions(extensions_dir: Path | None) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    if extensions_dir is None or not extensions_dir.is_dir():
        return out
    for ext_dir in extensions_dir.iterdir():
        versions = ext_dir / "migrations" / "versions"
        if not versions.is_dir():
            continue
        found: set[str] = set()
        for script in versions.glob("*.py"):
            try:
                found.update(_REVISION_RE.findall(script.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
        out[ext_dir.name] = found
    return out


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while chunk := fh.read(_READ_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


# ---------------------------------------------------------------------------
# Archiv schreiben
# ---------------------------------------------------------------------------


class _HashingReader(io.RawIOBase):
    def __init__(self, fh: BinaryIO) -> None:
        self._fh = fh
        self.digest = hashlib.sha256()

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        data = self._fh.read(size)
        self.digest.update(data)
        return data


def _open_nofollow(path: Path) -> BinaryIO:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    return os.fdopen(fd, "rb")


def _add_file(tar: tarfile.TarFile, arcname: str, path: Path) -> dict[str, Any]:
    with _open_nofollow(path) as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise BackupError(f"{arcname} ist keine normale Datei mehr.")
        info = tarfile.TarInfo(arcname)
        info.size = st.st_size
        info.mtime = int(st.st_mtime)
        info.mode = stat.S_IMODE(st.st_mode) & 0o777
        reader = _HashingReader(fh)
        try:
            tar.addfile(info, reader)
        except OSError as exc:
            raise BackupError(f"{arcname} hat sich beim Sichern verändert, bitte erneut versuchen.") from exc
    return {"path": arcname, "sha256": reader.digest.hexdigest(), "size": info.size, "mode": info.mode}


def write_archive(
    out: BinaryIO, *, db_copy: Path, entries: list[Entry], manifest_for: Callable[[dict, list[dict]], dict]
) -> dict[str, Any]:
    """Schreibt das tar.gz nach `out`. `manifest_for(db_info, files)` baut das Manifest,
    das als letzter Eintrag folgt, und wird auch zurueckgegeben."""
    with (
        gzip.GzipFile(fileobj=out, mode="wb", compresslevel=6, mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w|", format=tarfile.PAX_FORMAT) as tar,
    ):
        db_entry = _add_file(tar, fmt.ARC_DB, db_copy)
        files = [_add_file(tar, entry.arcname, entry.path) for entry in entries]
        manifest = manifest_for(
            {"dialect": "sqlite", "sha256": db_entry["sha256"], "size": db_entry["size"]}, files
        )
        data = json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=1).encode("utf-8")
        info = tarfile.TarInfo(fmt.ARC_MANIFEST)
        info.size = len(data)
        info.mode = 0o600
        tar.addfile(info, io.BytesIO(data))
    return manifest


# ---------------------------------------------------------------------------
# Archiv lesen (Pruefen; Grundlage fuer das Wiederherstellen)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractLimits:
    """Grenzen beim Auspacken einer fremden Datei (Bomben: ein winziges gzip kann sich auf
    Terabyte entpacken, ein tar kann Millionen leerer Eintraege haben)."""

    max_entries: int
    max_total_bytes: int
    """Alles, was aus dem gzip herauskommt (Nutzdaten plus tar-Kopfzeilen und Auffuellung)."""
    max_file_bytes: int
    space_limited: bool = False
    """`max_total_bytes` stammt aus dem freien Platz (statt aus der festen Obergrenze): wird er
    ueberschritten, heisst die Meldung "zu wenig Speicher" statt "zu gross"."""


def _over_limit(limits: ExtractLimits) -> BackupError:
    megabytes = limits.max_total_bytes // (1024 * 1024)
    if limits.space_limited:
        return NotEnoughSpace(f"Zu wenig freier Speicher zum Entpacken der Sicherung (frei sind etwa {megabytes} MB).")
    return BackupTooLarge(f"Die Sicherung ist entpackt größer als erlaubt ({megabytes} MB).")


class _CountingReader(io.RawIOBase):
    """Zaehlt die entpackten Bytes und bricht an der Grenze ab -- schon waehrend gelesen wird
    (tarfile liest in Stuecken von ~10 KB), nicht erst danach."""

    def __init__(self, fh: BinaryIO, limits: ExtractLimits | None) -> None:
        self._fh = fh
        self._limits = limits
        self.total = 0
        self.header_start: int | None = 0
        """Gesetzt, solange tarfile den Kopf des naechsten Eintrags liest (siehe `MAX_HEADER_BYTES`)."""

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        data = self._fh.read(size)
        self.total += len(data)
        limits = self._limits
        if limits is not None and self.total > limits.max_total_bytes:
            raise _over_limit(limits)
        if self.header_start is not None and self.total - self.header_start > MAX_HEADER_BYTES:
            raise DamagedBackup("Kopf eines Eintrags zu gross")
        return data


MAX_HEADER_BYTES = 1024 * 1024
"""So viel darf tarfile zwischen zwei Eintraegen lesen (Kopf, pax-/GNU-Erweiterungen, Rest des
vorigen Eintrags). tarfile haelt einen pax- oder GNU-Langnamen-Kopf GANZ im Speicher: ein
Eintrag, der einen Namen von einem Gigabyte verspricht, ist als gzip nur ein Megabyte gross und
wuerde einen kleinen Rechner aus dem Speicher werfen. Unsere Namen sind hoechstens 1024 Zeichen."""


def _filter_member(member: tarfile.TarInfo, dest: Path) -> None:
    try:
        tarfile.data_filter(member, str(dest))
    except (tarfile.FilterError, OSError) as exc:  # OSError: `realpath` unter einer Datei ("files/a" und "files/a/b")
        raise DamagedBackup("unerwarteter Eintrag im Archiv") from exc


def extract_archive(
    stream: BinaryIO,
    dest: Path,
    header: dict[str, Any],
    *,
    limits: ExtractLimits | None = None,
    allow_file: Callable[[str], bool] | None = None,
    allow_dir: Callable[[str], bool] | None = None,
    skip_entry: Callable[[str, bool], bool] | None = None,
    skipped: list[str] | None = None,
) -> dict[str, Any]:
    """Packt nach `dest` aus und prueft alles: erlaubte Namen, nur normale Dateien (mit
    `allow_dir` auch Ordner), jede Datei genau einmal, sha256 und Groesse gegen das Manifest,
    Kopf gegen Manifest, `integrity_check` der Datenbank.

    Mit `limits` (fremde Dateien beim Wiederherstellen) zusaetzlich: Obergrenzen fuer Zahl der
    Eintraege, Einzeldatei und entpackte Gesamtmenge, gemessen am entpackten Strom. Mit
    `allow_file` eine strengere Namens-Erlaubnisliste als `is_safe_member`. Pro Eintrag laeuft
    ausserdem `tarfile.data_filter` (zweite Sicherung neben unserer eigenen Pruefung -- wir
    schreiben jede Datei selbst und nie ueber `tar.extract`). `skip_entry(name, ist_ordner)` nennt
    Eintraege, die nicht abgelehnt, sondern einfach nicht geschrieben werden (nur normale Dateien und
    Ordner; ihre Namen landen in `skipped`). Ihr Inhalt wird trotzdem gelesen und gegen das Manifest
    geprueft, damit die Pruefsummen-Pruefung dieselbe bleibt. Entpackt wird nur nach `dest`;
    scheitert etwas, liegen dort schon Dateien -- der Aufrufer raeumt `dest` dann weg."""
    seen: dict[str, tuple[str, int]] = {}
    manifest: Any = None
    entries = 0
    declared = 0
    gz = gzip.GzipFile(fileobj=stream, mode="rb")
    reader = _CountingReader(gz, limits)
    try:
        with tarfile.open(fileobj=reader, mode="r|") as tar:  # liest schon den ersten Kopf
            while True:
                reader.header_start = reader.total if reader.header_start is None else reader.header_start
                member = tar.next()
                reader.header_start = None
                if member is None:
                    break
                # Im Strom-Modus merkt sich tarfile jeden Eintrag (`members`) -- bei 200 000 Eintraegen
                # Hunderte MB. Wir brauchen die Liste nicht.
                tar.members.clear()
                name = member.name
                entries += 1
                if limits is not None and entries > limits.max_entries:
                    raise BackupTooLarge(f"Die Sicherung enthält mehr als {limits.max_entries} Einträge.")
                if manifest is not None:
                    raise DamagedBackup("unerwarteter Eintrag im Archiv")  # das Manifest ist der letzte Eintrag
                is_plain_file = member.type in (tarfile.REGTYPE, tarfile.AREGTYPE)
                skip_it = (
                    skip_entry is not None and (member.isdir() or is_plain_file) and skip_entry(name, member.isdir())
                )
                if skip_it and member.isdir():
                    if skipped is not None:
                        skipped.append(name)
                    continue
                if member.isdir():
                    if allow_dir is None or not allow_dir(name):
                        raise DamagedBackup("unerwarteter Eintrag im Archiv")
                    _filter_member(member, dest)
                    try:
                        (dest / name).mkdir(parents=True, exist_ok=True, mode=0o700)
                    except OSError as exc:
                        if exc.errno == errno.ENOSPC:
                            raise
                        raise DamagedBackup("unerwarteter Eintrag im Archiv") from exc  # z. B. Ordner nach gleichnamiger Datei
                    continue
                if skip_it:
                    allowed = fmt.is_safe_member(name)
                else:
                    allowed = allow_file(name) if allow_file is not None else fmt.is_safe_member(name)
                # Nur gewoehnliche Dateien: GNU-Sparse-Eintraege "entpacken" sich zu Nullen, die der Zaehler am
                # Strom nicht sieht (kleiner Eintrag, riesige Datei).
                if not allowed or not is_plain_file or name in seen:
                    raise DamagedBackup("unerwarteter Eintrag im Archiv")
                if limits is not None:
                    declared += member.size
                    if declared > limits.max_total_bytes:
                        raise _over_limit(limits)
                    if member.size > limits.max_file_bytes:
                        raise BackupTooLarge(f"Eine Datei in der Sicherung ist größer als erlaubt ({name}).")
                    _filter_member(member, dest)
                fh = tar.extractfile(member)
                assert fh is not None
                if skip_it:
                    digest = hashlib.sha256()
                    size = 0
                    while chunk := fh.read(_READ_SIZE):
                        digest.update(chunk)
                        size += len(chunk)
                    seen[name] = (digest.hexdigest(), size)
                    if skipped is not None:
                        skipped.append(name)
                    continue
                if name == fmt.ARC_MANIFEST:
                    if member.size > fmt.MAX_MANIFEST_BYTES:
                        raise DamagedBackup("Inhaltsverzeichnis zu gross")
                    manifest = json.loads(fh.read())
                    continue
                target = dest / name
                try:
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                except OSError as exc:
                    # "files/a" und "files/a/b" im selben Archiv, ein Name, den das Dateisystem nicht kann
                    # (zu lang, gleich bis auf Gross-/Kleinschreibung): das schreibt Nodvard Deck nie.
                    if exc.errno == errno.ENOSPC:
                        raise
                    raise DamagedBackup("unerwarteter Eintrag im Archiv") from exc
                digest = hashlib.sha256()
                size = 0
                with os.fdopen(fd, "wb") as out:
                    while chunk := fh.read(_READ_SIZE):
                        digest.update(chunk)
                        size += len(chunk)
                        out.write(chunk)
                seen[name] = (digest.hexdigest(), size)
    except (tarfile.TarError, EOFError, zlib.error, gzip.BadGzipFile, ValueError, RecursionError) as exc:
        # ValueError deckt kaputtes JSON/UTF-8 im Manifest ab, RecursionError ein tief
        # verschachteltes JSON.
        raise DamagedBackup("Archiv unlesbar") from exc
    finally:
        gz.close()
    if manifest is None:
        raise DamagedBackup("Inhaltsverzeichnis fehlt")
    manifest = fmt.check_manifest(manifest, header)
    expected: dict[str, tuple[str, int]] = {}
    for item in manifest["files"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise DamagedBackup("Inhaltsverzeichnis ungültig")
        expected[item["path"]] = (item.get("sha256"), item.get("size"))
    db = manifest["db"]
    expected[fmt.ARC_DB] = (db.get("sha256"), db.get("size"))
    if expected != seen:
        raise DamagedBackup("Prüfsummen stimmen nicht")
    integrity_check(dest / fmt.ARC_DB)
    return manifest


# ---------------------------------------------------------------------------
# Pipe zwischen zwei Threads (tar <-> Verschluesselung), ohne alles im Speicher
# ---------------------------------------------------------------------------


def run_pipeline(producer: Callable[[BinaryIO], None], consumer: Callable[[BinaryIO], T]) -> T:
    """`producer` schreibt in eine Pipe, `consumer` liest sie (bis zum Ende, Rest wird
    nachgelesen, damit der Erzeuger bis zum Schluss pruefen kann, z. B. den letzten
    age-Block). Ein Fehler des Erzeugers gewinnt gegen Folgefehler des Lesers."""
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb")
    writer = os.fdopen(write_fd, "wb")
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            producer(writer)
        except BaseException as exc:  # noqa: BLE001 - wird im aufrufenden Thread weitergereicht
            errors.append(exc)
        finally:
            try:
                writer.close()
            except OSError:
                pass

    thread = threading.Thread(target=_run, name="nodvard-backup-pipe", daemon=True)
    thread.start()
    try:
        result = consumer(reader)
        while reader.read(_READ_SIZE):
            pass
    except BaseException as exc:
        reader.close()
        thread.join()
        if errors and not isinstance(errors[0], BrokenPipeError):
            raise errors[0] from exc
        raise
    reader.close()
    thread.join()
    if errors:
        raise errors[0]
    return result
