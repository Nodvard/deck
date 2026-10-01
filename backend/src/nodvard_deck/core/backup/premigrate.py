"""Kopie der Datenbank vor jeder Migration -- und der Rueckweg auf sie.

`nodvard_deck.boot` legt VOR jeder Migration (`alembic upgrade`) eine Kopie der Datenbank an
(`<Datenordner>/backups/vor-update/<Zeit>_<von>_<nach>.db` plus `<Name>.json`) und migriert
nie ohne sie. Es bleiben die drei neuesten. Die Kopie entsteht mit der Online-Sicherung von SQLite
(`snapshot.online_copy`, danach `PRAGMA integrity_check`) in eine `.part`-Datei und wird erst nach
`fsync` umbenannt -- eine halbe Kopie sieht nie aus wie eine ganze.

**Platz:** vorher muss mindestens das 1,2-fache der Datenbank (samt WAL) frei sein: die Kopie selbst
und etwas Luft fuer die Migration. Sonst gibt es keine Migration (`NoSpaceForCopy`).

**Nicht Teil der Kopie:** Dateien ausserhalb der Datenbank (Erweiterungsdaten, Schluessel). Migrationen
fassen sie nicht an. Eine Kopie ist keine vollstaendige Sicherung (dafuer gibt es Einstellungen ->
System -> Sicherung).

**Rueckweg** (`restore_copy`): die Kopie ersetzt die Datenbank. Sie wird dazu UMBENANNT, nicht kopiert --
atomar, ohne zusaetzlichen Platz, und eine halb kopierte Datenbank kann es nicht geben. Die Kopie ist
danach verbraucht (sie IST jetzt die Datenbank). Vorher wird sie geprueft (`integrity_check`), und die
Nebendateien der Datenbank (`-wal`, `-shm`, `-journal`) werden weggeraeumt oder zusammen mit der alten
Datenbank beiseite gelegt: eine fremde WAL neben einer anderen Datenbank wuerde SQLite auf diese
anwenden und sie zerstoeren. Ein Journal in `state.json` (`revert`) macht auch einen Absturz mittendrin
heilbar (`finish_revert`, `nodvard_deck.boot` ruft es als Erstes auf). Auf Wunsch (`keep_discarded`) wandert
die verworfene Datenbank nach `restore/replaced-<Zeit>/`; dort raeumt sie die Oberflaeche und
`restore.sweep` nach 30 Tagen weg. Liegt die Datenbank auf einem anderen Laufwerk als der Datenordner, wird sie dafuer
kopiert (Platz wird vor dem Journal geprueft, `NoSpaceToSetAside`). Was dort schon liegt, wird nie ueberschrieben: ein
wiederholter Rueckweg loescht dann nur noch, was am Platz der Datenbank liegt.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import shutil
import sqlite3
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import bootstate
from . import restore, snapshot, store
from .errors import DamagedBackup, NotEnoughSpace, UnusableBackup

logger = logging.getLogger("nodvard_deck.premigrate")

DIR_PARTS = ("backups", "vor-update")
KEEP = 3
SPACE_FACTOR_NUM, SPACE_FACTOR_DEN = 12, 10
"""1,2 -- als Bruch, damit nichts mit Gleitkommazahlen gerundet wird."""
NAME_RE = bootstate.COPY_NAME_RE
SIDECAR_SUFFIX = ".json"
PART_SUFFIX = ".part"
_LABEL_BAD = re.compile(r"[^0-9A-Za-z.+-]+")
_SUFFIXES = ("", "-wal", "-shm", "-journal")
_MAX_ROWS = 64


class NoSpaceForCopy(NotEnoughSpace):
    def __init__(self, needed: int, free: int) -> None:
        self.needed = needed
        self.free = free
        super().__init__(
            f"Für die Kopie vor dem Update ist zu wenig Platz frei (nötig: {size_text(needed)}, frei: {size_text(free)}). "
            "Bitte Platz schaffen und es noch einmal versuchen. Es wurde nichts verändert."
        )


class NoSpaceToSetAside(NotEnoughSpace):
    """Die Datenbank liegt auf einem anderen Laufwerk als der Datenordner und wird zum Beiseitelegen kopiert -- dafuer
    fehlt Platz. Kommt VOR dem Journal: es wurde nichts veraendert, und kein Journal blockiert spaetere Starts."""

    def __init__(self, needed: int, free: int) -> None:
        self.needed = needed
        self.free = free
        super().__init__(
            f"Um die jetzige Datenbank beiseitezulegen, ist im Datenordner zu wenig Platz frei (nötig: {size_text(needed)}, frei: {size_text(free)}). "
            "Sie liegt auf einem anderen Laufwerk und wird dafür kopiert. Bitte Platz schaffen und es noch einmal versuchen. Es wurde nichts verändert."
        )


class RevertFailed(Exception):
    """Der Rueckweg auf die Kopie ist gescheitert. Das Journal in `state.json` bleibt stehen."""


def size_text(size: int) -> str:
    return f"{size / 1024 / 1024:.0f} MB" if size >= 1024 * 1024 else f"{size} Byte"


def copies_dir(data_dir: Path) -> Path:
    return Path(data_dir).joinpath(*DIR_PARTS)


def _file_size(path: str) -> int:
    try:
        return os.lstat(path).st_size
    except OSError:
        return 0


def required_bytes(db_path: Path) -> int:
    """Das 1,2-fache der Datenbank samt WAL, aufgerundet."""
    total = _file_size(os.fspath(db_path)) + _file_size(os.fspath(db_path) + "-wal")
    return -(-total * SPACE_FACTOR_NUM // SPACE_FACTOR_DEN)


def _label(version: str | None) -> str:
    cleaned = re.sub(r"\.{2,}", ".", _LABEL_BAD.sub("-", version or "")).strip(".-")[:32].strip(".-")
    return cleaned or "unbekannt"


def copy_name(now: float, from_version: str | None, to_version: str | None) -> str:
    stamp = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{_label(from_version)}_{_label(to_version)}.db"


@dataclass(frozen=True)
class CopyInfo:
    name: str
    path: Path
    created_at: str
    from_version: str | None
    to_version: str | None
    size: int


# ---------------------------------------------------------------------------
# Lesen: die laufende Datenbank und Kopien
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveDb:
    has_data: bool
    """Es gibt Tabellen, also etwas, das eine Kopie wert ist (eine leere oder neue Datei hat nichts zu schuetzen)."""
    heads: list[str]
    """Migrationsstaende aus `alembic_version` (sortiert; leer ohne die Tabelle)."""
    accounts: bool | None = None
    """Gibt es ein Konto (`users`)? `False` auch ohne die Tabelle, `None` wenn nicht feststellbar."""


def read_live(db_path: Path) -> LiveDb:
    """Liest die Datenbank der Anwendung (sie wird dabei nie veraendert oder angelegt).
    `DamagedBackup`, wenn sie sich nicht als SQLite-Datei oeffnen laesst."""
    try:
        st = os.lstat(db_path)
    except FileNotFoundError:
        return LiveDb(False, [])
    except OSError as exc:
        raise DamagedBackup("Datenbank nicht lesbar") from exc
    if st.st_size == 0:
        return LiveDb(False, [])
    try:
        conn = sqlite3.connect(os.fspath(db_path), timeout=30)
    except sqlite3.DatabaseError as exc:
        raise DamagedBackup("Datenbank nicht lesbar") from exc
    try:
        tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
        if not tables:
            return LiveDb(False, [])
        try:
            rows = conn.execute("SELECT version_num FROM alembic_version LIMIT ?", (_MAX_ROWS,)).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            rows = []
        try:
            accounts: bool | None = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
        except sqlite3.OperationalError as exc:
            accounts = False if "no such table" in str(exc) else None
        return LiveDb(True, sorted({str(r[0]) for r in rows}), accounts)
    except sqlite3.DatabaseError as exc:
        raise DamagedBackup("Datenbank nicht lesbar") from exc
    finally:
        conn.close()


def read_heads(path: Path) -> list[str]:
    """Migrationsstaende einer Kopie (nur lesend, ohne Schema-Code auszufuehren)."""
    with restore.open_untrusted(path) as conn:
        try:
            rows = conn.execute("SELECT version_num FROM alembic_version LIMIT ?", (_MAX_ROWS,)).fetchall()
        except sqlite3.DatabaseError:
            return []
    return sorted({str(r[0]) for r in rows})


def copy_path(data_dir: Path, name: object) -> Path | None:
    """Der Pfad einer unserer Kopien -- nur mit unserem Namensmuster und nur als normale Datei."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        return None
    path = copies_dir(data_dir) / name
    try:
        return path if stat.S_ISREG(os.lstat(path).st_mode) else None
    except OSError:
        return None


def list_copies(data_dir: Path, *, limit: int = KEEP) -> list[dict[str, Any]]:
    """Die neuesten Kopien, neueste zuerst: Name, Zeit, Versionen, Groesse. Fuer die Oberflaeche."""
    directory = copies_dir(data_dir)
    try:
        names = sorted((e.name for e in os.scandir(directory) if NAME_RE.match(e.name)), reverse=True)
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for name in names:
        path = copy_path(data_dir, name)
        if path is None:
            continue
        side = restore.read_json(Path(str(path) + SIDECAR_SUFFIX), max_bytes=64 * 1024)
        side = side if isinstance(side, dict) else {}
        out.append({
            "name": name,
            "created_at": restore._text(side.get("created_at"), 32) or _time_from_name(name),
            "from_version": restore._text(side.get("from_version"), 40),
            "to_version": restore._text(side.get("to_version"), 40),
            "size": _file_size(os.fspath(path)),
        })
        if len(out) >= limit:
            break
    return out


def _time_from_name(name: str) -> str:
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z", name)
    return f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}Z" if m else ""


# ---------------------------------------------------------------------------
# Kopie anlegen
# ---------------------------------------------------------------------------


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def make_copy(
    layout: restore.Layout, *, from_version: str | None, to_version: str | None, from_heads: list[str], to_heads: list[str],
    now: float | None = None, keep: int = KEEP,
) -> CopyInfo:
    """Legt die Kopie an oder wirft -- ohne etwas zu hinterlassen und ohne eine aeltere Kopie anzufassen.
    `NoSpaceForCopy` bei zu wenig Platz, `snapshot.IntegrityCheckFailed` bei einer kaputten Kopie."""
    now = time.time() if now is None else now
    directory = store.make_private_dir(copies_dir(layout.data_dir))
    needed = required_bytes(layout.db_path)
    try:
        free = store.free_bytes(directory)
    except OSError:
        free = None  # Platz nicht feststellbar: nicht deshalb die Migration verbieten
    if free is not None and free < needed:
        raise NoSpaceForCopy(needed, free)

    name = copy_name(now, from_version, to_version)
    stamp = now
    while os.path.lexists(directory / name) or os.path.lexists(directory / (name + SIDECAR_SUFFIX)):
        stamp += 1  # zwei Kopien in derselben Sekunde
        name = copy_name(stamp, from_version, to_version)
    final = directory / name
    part = directory / (name + PART_SUFFIX)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(part)
    try:
        snapshot.online_copy(layout.db_path, part)
        os.chmod(part, 0o600)
        _fsync_file(part)
        os.replace(part, final)
        restore._fsync_dir(directory)
        size = final.stat().st_size
        restore.write_json_atomic(Path(str(final) + SIDECAR_SUFFIX), {
            "format": 1, "created_at": restore.now_iso(now), "from_version": from_version, "to_version": to_version,
            "from_heads": sorted(from_heads), "to_heads": sorted(to_heads), "db_bytes": size,
        })
    except BaseException:
        for leftover in (part, final, Path(str(final) + SIDECAR_SUFFIX)):
            with contextlib.suppress(OSError):
                os.unlink(leftover)
        raise
    prune(layout.data_dir, keep=keep)
    return CopyInfo(name=name, path=final, created_at=restore.now_iso(now), from_version=from_version, to_version=to_version, size=size)


def prune(data_dir: Path, keep: int = KEEP) -> list[str]:
    """Nur die `keep` neuesten Kopien bleiben (samt Begleitdatei); liegengebliebene `.part`-Dateien
    gehen auch weg. Gibt die entfernten Kopien zurueck."""
    directory = copies_dir(data_dir)
    removed: list[str] = []
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return removed
    names = sorted((e.name for e in entries if NAME_RE.match(e.name) and e.is_file(follow_symlinks=False)), reverse=True)
    for name in names[max(0, keep):]:
        for victim in (directory / name, directory / (name + SIDECAR_SUFFIX)):
            with contextlib.suppress(OSError):
                os.unlink(victim)
        removed.append(name)
    keep_names = set(names[:max(0, keep)])
    for entry in entries:
        orphan_sidecar = entry.name.endswith(SIDECAR_SUFFIX) and NAME_RE.match(entry.name[: -len(SIDECAR_SUFFIX)]) \
            and entry.name[: -len(SIDECAR_SUFFIX)] not in keep_names
        if entry.name.endswith(PART_SUFFIX) or orphan_sidecar:
            with contextlib.suppress(OSError):
                os.unlink(entry.path)
    return removed


# ---------------------------------------------------------------------------
# Rueckweg
# ---------------------------------------------------------------------------


def _move(src: Path, dst: Path) -> None:
    """Verschieben ist `rename` (gleiches Dateisystem, atomar). Eigene Funktion, damit Tests einen
    Absturz mitten im Rueckweg einbauen koennen."""
    os.rename(src, dst)


def _move_aside(src: Path, dst: Path) -> None:
    """Beiseitelegen: umbenennen. Liegt die Datenbank auf einem anderen Dateisystem als der Datenordner (eigener
    Datenbank-Pfad), geht das nicht (EXDEV) -- dann kopieren und die Quelle erst loeschen, wenn die Kopie vollstaendig
    auf der Platte ist. Wiederholbar: Ein Abbruch mittendrin laesst die Quelle stehen, der naechste Lauf kopiert neu."""
    try:
        _move(src, dst)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    part = dst.with_name(dst.name + PART_SUFFIX)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(part)
    try:
        shutil.copyfile(src, part)
        os.chmod(part, 0o600)
        _fsync_file(part)
        os.replace(part, dst)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(part)
        raise
    restore._fsync_dir(dst.parent)
    os.unlink(src)
    restore._fsync_dir(src.parent)


def check_room_to_set_aside(layout: restore.Layout) -> None:
    """Muss die Datenbank zum Beiseitelegen kopiert werden (anderes Dateisystem), braucht es dafuer Platz im Datenordner.
    Das wird VOR dem Journal geprueft: sonst bliebe ein Rueckweg stehen, der bei jedem Start an genau diesem Platz scheitert."""
    if store.same_storage(layout.db_path.parent, layout.data_dir):
        return
    needed = sum(_file_size(os.fspath(layout.db_path) + suffix) for suffix in _SUFFIXES)
    try:
        free = store.free_bytes(layout.data_dir)
    except OSError:
        return
    if free < needed:
        raise NoSpaceToSetAside(needed, free)


def _clear_revert(data_dir: Path, journal: dict[str, Any]) -> None:
    """Der Rueckweg ist fertig: Journal weg und -- im SELBEN Schreibvorgang -- die Migration, zu der die Kopie gehoerte,
    als zurueckgesetzt vermerken. Getrennt geschrieben stuende sie nach einem Absturz genau dazwischen noch auf
    `running` mit einer Kopie, die es nicht mehr gibt (sie IST jetzt die Datenbank): jeder weitere Start endete auf der
    Notseite. Dass die Datenbank danach wieder der Stand vor dem Update ist, weiss nur das Journal."""
    state = bootstate.read_state(data_dir)
    state.pop("revert", None)
    migration = state.get("last_migration")
    if migration and migration.get("copy") == journal.get("copy"):
        migration["state"] = "reverted"
        state["started_ok"] = True  # der Stand vor dem Update ist ja schon gelaufen
    bootstate.write_state(data_dir, state)


def _replaced_name(layout: restore.Layout, now: float) -> str:
    base = f"{restore.REPLACED_PREFIX}{datetime.fromtimestamp(now, tz=timezone.utc):%Y%m%dT%H%M%S}"
    name = base
    while os.path.lexists(layout.restore_dir / name):
        name += "x"
    return name


def _discard_live(layout: restore.Layout, replaced: str | None) -> None:
    """Die jetzige Datenbank samt Nebendateien weg: beiseite gelegt (`replaced`) oder geloescht. Die Nebendateien zuerst:
    bricht es mittendrin ab, liegt nie eine fremde WAL neben einer fehlenden Datenbank (SQLite wendete sie auf die
    naechste an)."""
    db = os.fspath(layout.db_path)
    if not replaced:
        for suffix in _SUFFIXES:
            restore.remove_path(Path(db + suffix))
        return
    directory = restore.make_private_dir(layout.restore_dir / replaced)
    # Ein Ziel, das schon da ist, wird nie ueberschrieben. Der Ordner ist je Journal neu, und ein Ziel entsteht nur ganz
    # (umbenannt, oder eine gefsyncte `.part` umbenannt): Ein frueherer Lauf hat diese Datei also schon beiseitegelegt und
    # brach nur vor dem Loeschen der Quelle ab. Liegt die DATENBANK schon dort (sie kommt als Letzte), ist das Original
    # ganz beiseite; was jetzt am Platz liegt, ist die schon eingesetzte Kopie (anderes Laufwerk: `_install` brach vor dem
    # Loeschen der Kopie ab) oder derselbe Inhalt noch einmal. Das wird geloescht, nie darueber gelegt.
    if os.path.lexists(directory / layout.db_path.name):
        for suffix in (*_SUFFIXES[1:], _SUFFIXES[0]):
            restore.remove_path(Path(db + suffix))
        return
    for suffix in (*_SUFFIXES[1:], _SUFFIXES[0]):
        live, target = Path(db + suffix), directory / (layout.db_path.name + suffix)
        if not os.path.lexists(live):
            continue
        if os.path.lexists(target):
            restore.remove_path(live)
            continue
        _move_aside(live, target)


def _install(layout: restore.Layout, src: Path) -> None:
    """Die Kopie an den Platz der Datenbank. Bevorzugt umbenennen; auf einem anderen Dateisystem
    (z. B. eigener Datenbank-Pfad) ueber eine Nachbardatei."""
    db = layout.db_path
    try:
        os.replace(src, db)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        tmp = db.with_name(f".{db.name}.reverting")
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        try:
            shutil.copyfile(src, tmp)
            os.chmod(tmp, 0o600)
            _fsync_file(tmp)
            snapshot.integrity_check(tmp)
            os.replace(tmp, db)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        with contextlib.suppress(OSError):
            os.unlink(src)
        restore._fsync_dir(src.parent)  # das Loeschen der Kopie gleich auf die Platte, nicht erst mit state.json
    with contextlib.suppress(OSError):
        os.chmod(db, 0o600)
    restore._fsync_dir(db.parent)
    with contextlib.suppress(OSError):
        os.unlink(Path(str(src) + SIDECAR_SUFFIX))


def set_aside(layout: restore.Layout, *, now: float | None = None) -> str:
    """Die jetzige Datenbank samt Nebendateien nach `restore/replaced-<Zeit>/` legen -- nie loeschen. Fuer die halbe
    Datenbank einer ersten Migration, die nicht fertig wurde (`boot.decide`). Gibt den Ordnernamen zurueck."""
    now = time.time() if now is None else now
    restore.make_private_dir(layout.restore_dir)
    check_room_to_set_aside(layout)
    name = _replaced_name(layout, now)
    _discard_live(layout, name)
    return name


def restore_copy(layout: restore.Layout, name: str, *, keep_discarded: bool, reason: str, now: float | None = None) -> None:
    """Ersetzt die Datenbank durch die Kopie `name`. Prueft die Kopie, BEVOR etwas angefasst wird
    (`UnusableBackup`: es gibt sie nicht; `DamagedBackup`: kaputt). Danach schreibt sie das Journal und
    fuehrt den Rueckweg aus (`finish_revert`); scheitert der, bleibt das Journal stehen und der naechste
    Start macht weiter."""
    now = time.time() if now is None else now
    src = copy_path(layout.data_dir, name)
    if src is None:
        raise UnusableBackup("Die Kopie vor dem Update gibt es nicht (mehr).")
    snapshot.integrity_check(src)
    heads = read_heads(src)
    replaced = _replaced_name(layout, now) if keep_discarded else None
    if replaced:
        check_room_to_set_aside(layout)
    bootstate.update_state(layout.data_dir, revert={
        "copy": name, "heads": heads, "replaced": replaced, "keep": keep_discarded, "reason": reason, "at": restore.now_iso(now),
    })
    finish_revert(layout)


def finish_revert(layout: restore.Layout) -> bool:
    """Fuehrt einen im Journal (`state.json`, `revert`) stehenden Rueckweg zu Ende -- idempotent, jeder
    Schritt darf wiederholt werden. `False`: kein Journal. `RevertFailed`: geht nicht (das Journal bleibt)."""
    data_dir = layout.data_dir
    journal = bootstate.read_state(data_dir).get("revert")
    if not journal:
        return False
    name = journal["copy"]
    src = copy_path(data_dir, name)
    if src is None:
        # Die Kopie ist weg: entweder war der Rueckweg schon fertig (Absturz VOR dem Aufraeumen des Journals) ...
        expected = journal.get("heads") or []
        try:
            live = read_live(layout.db_path)
        except DamagedBackup as exc:
            raise RevertFailed("Die Kopie für den Rückweg fehlt und die Datenbank ist nicht lesbar.") from exc
        stale = any(os.path.lexists(os.fspath(layout.db_path) + s) for s in _SUFFIXES[1:])
        if live.has_data and expected and live.heads == sorted(expected) and not stale:
            _clear_revert(data_dir, journal)
            return True
        # ... oder sie fehlt wirklich.
        raise RevertFailed("Die Kopie für den Rückweg fehlt, und die Datenbank hat nicht den erwarteten Stand.")
    try:
        snapshot.integrity_check(src)
    except DamagedBackup as exc:
        raise RevertFailed("Die Kopie für den Rückweg ist beschädigt.") from exc
    _discard_live(layout, journal.get("replaced"))
    _install(layout, src)
    _clear_revert(data_dir, journal)
    return True
