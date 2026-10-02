"""Wiederherstellen einer Sicherung: Staging, Pruefen, Vormerken, Einspielen beim Start.

Ablauf (die Schritte 1 bis 3 laufen in der Anwendung, 4 und 5 beim naechsten Start in
`nodvard_deck.boot`, VOR Migration und Anwendung):

1. **Upload** der Datei nach `<Datenordner>/restore/<id>/upload.ndbak` (Service, nicht hier).
2. **Staging** (`stage_backup`): entschluesseln und in einen EIGENEN Ordner
   `restore/<id>/staging/` auspacken, dort alles pruefen (Namen, Pruefsummen, Datenbank,
   Migrationsstand). Bei jedem Fehler wird der Ordner geloescht. Erst nach erfolgreicher
   Rueckkehr darf irgendetwas davon weiterverwendet werden -- `read_backup` legt die Dateien
   schon vor dem Ende der Pruefung ab.
3. **Vormerken** (`write_pending`): `restore/pending.json`. Sie laeuft nach einer Stunde ab.
4. **Einspielen** (`apply_pending`, `boot.py`): Staging erneut pruefen, den aktuellen Stand
   nach `restore/replaced-<Zeit>/` VERSCHIEBEN (nur `rename`, kein Kopieren), das Staging an
   seinen Platz verschieben, Anmeldungen beenden, Einrichtungscode entfernen. Ein Journal
   (`restore/journal.json`) macht das Ganze auch nach einem Absturz mitten drin wieder
   rueckgaengig. **Endgueltig** ist die Wiederherstellung erst, wenn `boot.py` danach auch die
   Migration geschafft hat (`Applied.commit`); scheitert die, geht der alte Stand zurueck.
5. **Ergebnis** (`restore/result.json`): die Oberflaeche zeigt es, die Anwendung schreibt das
   Audit-Protokoll nach dem Start in die dann gueltige Datenbank.

**Nicht vertrauenswuerdig ist alles in der Datei.** Die Verschluesselung (age) prueft nur, dass
niemand die Datei veraendert hat -- nicht, WER sie gemacht hat; jeder, der den oeffentlichen
Schluessel oder das Einmal-Passwort kennt, kann eine gueltige Sicherung bauen. Deshalb:
Namen nur aus einer Erlaubnisliste, nur normale Dateien und Ordner, Obergrenzen fuer Eintraege,
Einzeldateien und die entpackte Menge (auch gegen den freien Platz), die SQLite-Datei nur
lesend und ohne vertrauten Schema-Code (`trusted_schema=OFF`, Autorisierer, keine Trigger,
Ansichten oder virtuellen Tabellen -- die legt Nodvard Deck nie an), `integrity_check`,
nur Alembic-Revisionen, die dieses Image kennt, und eine Zusammenfassung zur Ansicht vor dem
Einspielen.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy.engine import make_url

from . import container, snapshot, store
from . import format as fmt
from .errors import (
    BackupError,
    DamagedBackup,
    NotEnoughSpace,
    NotSqlite,
    UnusableBackup,
)

logger = logging.getLogger("nodvard_deck.restore")

RESTORE_DIR = "restore"
UPLOAD_NAME = "upload.ndbak"
STAGING_NAME = "staging"
META_NAME = "meta.json"
PENDING_NAME = "pending.json"
JOURNAL_NAME = "journal.json"
RESULT_NAME = "result.json"
REPLACED_PREFIX = "replaced-"

REPLACED_TTL_S = 30 * 24 * 3600
"""So lange bleibt der alte Stand unter `restore/replaced-...` liegen (Konten, Schluessel im Klartext!), dann loescht ihn
`sweep_replaced` von selbst. Die Oberflaeche zeigt, wann."""
_REPLACED_TIME_RE = re.compile(r"^replaced-(\d{8}T\d{6})x*$")

STAGING_TTL_S = 3600
"""Ein aufgegebener Upload bzw. ein aufgegebenes Staging wird nach einer Stunde geloescht."""
PENDING_TTL_S = 3600
"""So lange gilt eine Vormerkung. Danach wird sie beim Start NICHT mehr eingespielt (sonst
spielte ein Stromausfall Wochen spaeter ploetzlich einen alten Stand ein)."""

SPACE_RESERVE = 128 * 1024 * 1024
MIN_SPACE_BUDGET = 16 * 1024 * 1024
DEFAULT_MAX_ENTRIES = 200_000

ID_RE = re.compile(r"^[0-9a-f]{32}$")
SOURCES = ("owner", "bootstrap", "cli")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_TEXT = 200


class RestoreFailed(BackupError):
    """Das Einspielen ist gescheitert und der alte Stand ist wieder da."""


class RollbackFailed(BackupError):
    """Der Rueckweg selbst ist gescheitert -- der alte Stand liegt noch in `replaced-...`."""


def new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Wo liegt was
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    """Die Orte der Daten im laufenden Betrieb (aus den Einstellungen). Beim Einspielen wird
    genau das ersetzt, was `targets()` aufzaehlt -- und sonst nichts."""

    data_dir: Path
    db_path: Path
    master_key: Path
    keyring: Path
    jwt_secret: Path
    ext_dir: Path
    branding_dir: Path
    runs_dir: Path
    jwt_from_env: bool = False
    extensions_dir: Path | None = None
    repo_root: Path | None = None

    @property
    def restore_dir(self) -> Path:
        return self.data_dir / RESTORE_DIR

    @classmethod
    def from_settings(cls, settings: Any) -> Layout:
        url = make_url(settings.database_url)
        if not url.drivername.startswith("sqlite") or url.database in (None, "", ":memory:"):
            raise NotSqlite()
        absolute = lambda p: Path(os.path.abspath(p))
        return cls(
            data_dir=absolute(settings.data_dir),
            db_path=absolute(url.database),
            master_key=absolute(settings.master_key_path),
            keyring=absolute(settings.vault_keyring_path),
            jwt_secret=absolute(settings.jwt_secret_path),
            ext_dir=absolute(settings.ext_data_dir),
            branding_dir=absolute(Path(settings.data_dir) / "branding"),
            runs_dir=absolute(Path(settings.data_dir) / "runs"),
            jwt_from_env=bool(settings.jwt_secret),
            extensions_dir=absolute(settings.extensions_dir) if settings.extensions_dir else None,
        )


@dataclass(frozen=True)
class Target:
    name: str
    """Name im `replaced-...`-Ordner (flach)."""
    path: Path
    staged: str | None
    """Pfad im Staging; `None` = wird nur weggeraeumt (Nebendateien der alten Datenbank)."""


def targets(layout: Layout) -> list[Target]:
    db = layout.db_path
    out = [Target("lattice.db", db, "db/lattice.db")]
    out += [Target(f"lattice.db{suffix}", Path(str(db) + suffix), None) for suffix in ("-wal", "-shm", "-journal")]
    out += [
        Target("master.key", layout.master_key, "files/master.key"),
        Target("vault_keyring.json", layout.keyring, "files/vault_keyring.json"),
        Target("jwt_secret.key", layout.jwt_secret, "files/jwt_secret.key"),
        Target("ext", layout.ext_dir, "files/ext"),
        Target("branding", layout.branding_dir, "files/branding"),
        Target("runs", layout.runs_dir, "files/runs"),
    ]
    return out


# ---------------------------------------------------------------------------
# Kleine Helfer fuer Dateien
# ---------------------------------------------------------------------------


def make_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)
    return path


def _fsync_dir(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_json_atomic(path: Path, data: Any) -> None:
    """Schreibt `path` ganz oder gar nicht (0600): erst eine Nachbardatei, dann `rename`."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, sort_keys=True, indent=1, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _fsync_dir(path.parent)


def read_json(path: Path, *, max_bytes: int = 64 * 1024 * 1024) -> Any | None:
    """Liest eine JSON-Datei von uns (ohne Links zu folgen); `None`, wenn es sie nicht gibt oder
    sie nicht lesbar ist."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return None
        return json.loads(raw)
    except (OSError, ValueError, RecursionError):
        return None


def remove_path(path: Path) -> None:
    """Loescht Datei, Link oder Ordner samt Inhalt (ohne Links zu folgen). Fehlt der Pfad: nichts."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def _move(src: Path, dst: Path) -> None:
    """Verschieben ist hier immer `rename` -- gleiches Dateisystem, atomar, kein Kopieren. Als
    eigene Funktion, damit Tests einen Fehler mitten im Verschieben einbauen koennen."""
    os.rename(src, dst)


def _text(value: object, limit: int = _MAX_TEXT) -> str | None:
    """Text aus einer fremden Datei fuer die Anzeige: nur Zeichen, begrenzt."""
    if not isinstance(value, str):
        return None
    cleaned = "".join(c for c in value if c.isprintable())
    return cleaned[:limit]


# ---------------------------------------------------------------------------
# Die fremde Datenbank
# ---------------------------------------------------------------------------

_SQLITE_MAGIC = b"SQLite format 3\x00"
_ALLOWED_PRAGMAS = frozenset({"integrity_check", "trusted_schema", "query_only", "cell_size_check"})
_READ_ACTIONS = tuple(
    getattr(sqlite3, name)
    for name in ("SQLITE_SELECT", "SQLITE_READ", "SQLITE_FUNCTION", "SQLITE_RECURSIVE")
    if hasattr(sqlite3, name)
)


def _authorizer(action: int, arg1: str | None, arg2: str | None, dbname: str | None, source: str | None) -> int:
    if action in _READ_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in _ALLOWED_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@contextlib.contextmanager
def open_untrusted(path: Path) -> Iterator[sqlite3.Connection]:
    """Oeffnet eine fremde SQLite-Datei so, dass sie nichts ausfuehren und nichts aendern kann:
    nur lesend und unveraenderlich (`mode=ro&immutable=1`), `trusted_schema=OFF` (Funktionen in
    Ansichten, Triggern, CHECK-Bedingungen und Indexausdruecken laufen nicht), und ein
    Autorisierer, der nur Lesen und die noetigen PRAGMAs zulaesst."""
    try:
        with open(path, "rb") as fh:
            if fh.read(16) != _SQLITE_MAGIC:
                raise DamagedBackup("Datenbank unlesbar")
    except OSError as exc:
        raise DamagedBackup("Datenbank fehlt") from exc
    uri = "file:" + urllib.parse.quote(os.fspath(path)) + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.execute("PRAGMA cell_size_check=ON")
        conn.execute("PRAGMA query_only=ON")
        row = conn.execute("PRAGMA trusted_schema").fetchone()
        if row is None or row[0] != 0:
            # Faellt die Sperre aus (sehr altes SQLite), lieber gar nichts pruefen als unsicher.
            raise UnusableBackup("Dieses SQLite kann fremde Datenbanken nicht sicher prüfen.")
        conn.set_authorizer(_authorizer)
        yield conn
    except sqlite3.DatabaseError as exc:
        raise DamagedBackup("Datenbank unlesbar") from exc
    finally:
        conn.close()


@dataclass
class DbFacts:
    versions: list[str]
    owner_name: str | None
    users: int | None
    hosts: int | None
    extensions: list[dict[str, str]] = field(default_factory=list)


def _scalar(conn: sqlite3.Connection, sql: str) -> Any:
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.DatabaseError:
        return None
    return row[0] if row else None


def _foreign_schema_object(kind: object, rootpage: object, sql: object) -> str | None:
    """Was an einem Eintrag in `sqlite_master` nicht von uns stammen kann (oder `None`).

    SQLite selbst richtet sich beim Laden des Schemas nach dem SQL-Text, NICHT nach der Spalte
    `type` -- mit `writable_schema` laesst sich ein Trigger als `type='TRIGGER'` (oder jede andere
    Schreibweise) eintragen, und er laeuft spaeter trotzdem. Darum: nur genau `table` und `index`,
    beide mit einer echten Seite (`rootpage > 0`; Ansichten, Trigger und virtuelle Tabellen haben
    keine), und zusaetzlich kein `CREATE VIRTUAL` im Text (auch mit Kommentar dazwischen)."""
    if not isinstance(kind, str) or kind not in ("table", "index"):
        lowered = kind.lower() if isinstance(kind, str) else ""
        return {"trigger": "Trigger", "view": "Ansicht"}.get(lowered, "Schema-Eintrag")
    if not isinstance(rootpage, int) or isinstance(rootpage, bool) or rootpage <= 0:
        return "virtuelle Tabelle" if kind == "table" else "Schema-Eintrag"
    if isinstance(sql, str) and re.match(r"(?:\s|/\*.*?\*/|--[^\n]*\n)*CREATE(?:\s|/\*.*?\*/|--[^\n]*\n)+VIRTUAL\b", sql, re.IGNORECASE | re.DOTALL):
        return "virtuelle Tabelle"
    return None


def inspect_db(path: Path) -> DbFacts:
    """Prueft die fremde Datenbank (siehe `open_untrusted`) und liest die Fakten fuer die
    Zusammenfassung. Wirft `DamagedBackup` bzw. `UnusableBackup` mit verstaendlichem Text."""
    with open_untrusted(path) as conn:
        try:
            objects = conn.execute("SELECT type, name, rootpage, sql FROM sqlite_master").fetchall()
        except sqlite3.DatabaseError as exc:
            raise DamagedBackup("Datenbank unlesbar") from exc
        for kind, name, rootpage, sql in objects:
            what = _foreign_schema_object(kind, rootpage, sql)
            if what is not None:
                raise UnusableBackup(
                    f"Die Datenbank in der Sicherung enthält etwas, das Nodvard Deck nie anlegt "
                    f"({what} „{_text(name, 60)}“). Sie wird aus Sicherheitsgründen nicht eingespielt."
                )
        result = [str(r[0]) for r in conn.execute("PRAGMA integrity_check").fetchall()]
        if result != ["ok"]:
            raise DamagedBackup("Datenbank nicht in Ordnung")
        try:
            versions = sorted(str(r[0]) for r in conn.execute("SELECT version_num FROM alembic_version LIMIT 200").fetchall())
        except sqlite3.DatabaseError:
            versions = []
        owner = _scalar(conn, "SELECT username FROM users WHERE is_owner = 1 ORDER BY created_at LIMIT 1")
        users = _scalar(conn, "SELECT count(*) FROM users")
        hosts = _scalar(conn, "SELECT count(*) FROM hosts")
        extensions: list[dict[str, str]] = []
        try:
            for ext_id, version, state in conn.execute("SELECT id, version, state FROM extensions ORDER BY id LIMIT 500"):
                extensions.append({"id": _text(ext_id, 64) or "", "version": _text(version, 32) or "", "state": _text(state, 16) or ""})
        except sqlite3.DatabaseError:
            extensions = []
    return DbFacts(versions=versions, owner_name=_text(owner, 64), users=users if isinstance(users, int) else None,
                   hosts=hosts if isinstance(hosts, int) else None, extensions=extensions)


def installed_extension_ids(extensions_dir: Path | None) -> set[str] | None:
    """Ordnernamen der hier installierten Erweiterungen; `None`, wenn nicht feststellbar."""
    if extensions_dir is None or not extensions_dir.is_dir():
        return None
    return {p.name for p in extensions_dir.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))}


def check_compatible(
    facts: DbFacts, manifest: dict[str, Any], *, known: set[str] | None, installed: set[str] | None, current_version: str
) -> None:
    """Passt der Migrationsstand der Datenbank zu diesem Image? Sonst `UnusableBackup` mit
    Klartext (neuer als dieses Image, oder eine Erweiterung fehlt). Aeltere Staende sind in
    Ordnung -- `boot.py` bringt sie nach dem Einspielen auf den neuen Stand."""
    if not facts.versions:
        raise UnusableBackup("In der Datenbank der Sicherung steht kein Migrationsstand. Sie kann nicht eingespielt werden.")
    declared = manifest.get("db", {}).get("alembic_heads") if isinstance(manifest.get("db"), dict) else None
    if not isinstance(declared, list) or sorted(str(x) for x in declared) != facts.versions:
        raise DamagedBackup("Inhaltsverzeichnis passt nicht zur Datenbank")
    if known is None:
        return
    unknown = [v for v in facts.versions if v not in known]
    if not unknown:
        return
    by_revision: dict[str, str] = {}
    for ext in manifest.get("extensions") or []:
        if isinstance(ext, dict) and isinstance(ext.get("id"), str):
            for head in ext.get("alembic_heads") or []:
                if isinstance(head, str):
                    by_revision[head] = ext["id"]
    missing = sorted({by_revision[v] for v in unknown if v in by_revision and installed is not None and by_revision[v] not in installed})
    if missing:
        names = ", ".join(f"„{_text(m, 64)}“" for m in missing)
        raise UnusableBackup(
            f"Diese Sicherung braucht die Erweiterung {names}, die in dieser Installation fehlt. "
            "Bitte zuerst die Erweiterung installieren (oder die Sicherung in einer Installation einspielen, die sie hat)."
        )
    newer_ext = sorted({by_revision[v] for v in unknown if v in by_revision})
    if newer_ext:
        names = ", ".join(f"„{_text(m, 64)}“" for m in newer_ext)
        raise UnusableBackup(
            f"Die Daten der Erweiterung {names} in dieser Sicherung stammen aus einer neueren Version, als hier installiert ist. "
            "Bitte zuerst Nodvard Deck aktualisieren."
        )
    version = _text(manifest.get("app_version"), 32) or "unbekannt"
    raise UnusableBackup(
        f"Die Sicherung stammt aus einer neueren (oder fremden) Version von Nodvard Deck (Version {version}); "
        f"installiert ist {current_version}. Bitte zuerst Nodvard Deck aktualisieren."
    )


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


def extract_limits(layout: Layout, *, max_unpacked: int, max_entries: int, reserve: int = SPACE_RESERVE) -> snapshot.ExtractLimits:
    """Obergrenzen fuer das Entpacken: die feste Grenze, aber nie mehr als der freie Platz im
    Datenordner (abzueglich einer Reserve, damit die Anwendung weiterlaufen kann)."""
    free = store.free_bytes(layout.restore_dir)
    budget = free - reserve
    if budget < MIN_SPACE_BUDGET:
        raise NotEnoughSpace(
            f"Zu wenig freier Speicher zum Prüfen der Sicherung ({free // (1024 * 1024)} MB frei). Bitte zuerst Platz schaffen."
        )
    if budget < max_unpacked:
        return snapshot.ExtractLimits(max_entries, budget, budget, space_limited=True)
    return snapshot.ExtractLimits(max_entries, max_unpacked, max_unpacked)


@dataclass
class Staged:
    summary: dict[str, Any]
    files: dict[str, list]
    """Pfad im Staging -> [sha256, Groesse]; genau das, was beim Einspielen erneut geprueft wird."""
    unpacked_bytes: int
    compat: dict[str, Any] = field(default_factory=dict)
    """Angaben fuer die Verstaendlichkeit der Fehlermeldung beim erneuten Pruefen (welche
    Erweiterung zu welcher Revision gehoert)."""


def read_upload_header(path: Path) -> dict[str, Any]:
    """Kopf einer hochgeladenen Datei (Klartext, nicht authentifiziert); wirft `DamagedBackup`/
    `NewerBackup`, wenn es keine Sicherung von Nodvard Deck ist."""
    with open(path, "rb") as fh:
        return fmt.read_header(fh)


def _version_key(version: str) -> tuple[int, ...] | None:
    parts = re.findall(r"\d+", version.split("+")[0])[:4]
    return tuple(int(p) for p in parts) if parts else None


def build_summary(
    manifest: dict[str, Any], facts: DbFacts, files: dict[str, list], *, layout: Layout,
    current_version: str, current_instance_id: str | None, has_accounts: bool | None, installed: set[str] | None,
    skipped_files: int = 0,
) -> dict[str, Any]:
    header = manifest.get("header") if isinstance(manifest.get("header"), dict) else {}
    created_at = _text(manifest.get("created_at"), 40)
    app_version = _text(manifest.get("app_version"), 32)
    instance_id = _text(manifest.get("instance_id"), 64)
    warnings: list[str] = []
    if instance_id and current_instance_id and instance_id != current_instance_id and has_accounts:
        warnings.append(
            "Die Sicherung stammt von einer anderen Installation als dieser. Nur einspielen, wenn die Sicherung selbst erstellt wurde."
        )
    if app_version and _version_key(app_version) and _version_key(current_version):
        if _version_key(app_version) < _version_key(current_version):  # type: ignore[operator]
            warnings.append(
                f"Die Sicherung stammt aus Version {app_version}, installiert ist {current_version}. "
                "Beim Start werden die Daten auf den neuen Stand gebracht."
            )
        elif _version_key(app_version) > _version_key(current_version):  # type: ignore[operator]
            warnings.append(
                f"Die Sicherung stammt aus Version {app_version}, installiert ist {current_version}. "
                "Der Datenbankstand passt, trotzdem sollte Nodvard Deck bald aktualisiert werden."
            )
    if installed is not None:
        absent = sorted(e["id"] for e in facts.extensions if e["id"] and e["id"] not in installed)
        if absent:
            warnings.append(
                f"Diese Erweiterungen aus der Sicherung gibt es hier nicht: {', '.join(absent)}. Die Daten dazu werden trotzdem eingespielt, die Erweiterungen sind aber nicht nutzbar."
            )
    if manifest.get("jwt_from_env") is True and not layout.jwt_from_env:
        warnings.append(
            "Die Sicherung stammt von einer Installation, die das Anmelde-Geheimnis über die Umgebung bekommt. "
            "Hier wird ein neues angelegt; alle melden sich neu an."
        )
    if facts.users == 0:
        warnings.append("In der Sicherung gibt es noch kein Konto. Nach dem Einspielen beginnt die Einrichtung von vorn.")
    elif facts.owner_name is None:
        warnings.append("In der Sicherung ist kein Owner-Konto zu finden.")
    if skipped_files == 1:
        warnings.append("1 Git-Einstellung aus der Sicherung wurde nicht übernommen, der Verlauf bleibt.")
    elif skipped_files > 1:
        warnings.append(
            f"{skipped_files} Git-Einstellungen aus der Sicherung wurden nicht übernommen, der Verlauf bleibt."
        )
    return {
        "created_at": created_at,
        "app_version": app_version,
        "instance_id": instance_id,
        "mode": header.get("mode") if header.get("mode") in fmt.MODES else None,
        "owner_name": facts.owner_name,
        "users": facts.users,
        "hosts": facts.hosts,
        "extensions": [{"id": e["id"], "version": e["version"]} for e in facts.extensions],
        "includes": {
            "runs": any(p.startswith("files/runs/") for p in files),
            "branding": any(p.startswith("files/branding/") for p in files),
            "jwt_secret": "files/jwt_secret.key" in files,
        },
        "warnings": warnings,
    }


def stage_backup(
    upload: Path, secret: str, staging: Path, *, layout: Layout, limits: snapshot.ExtractLimits,
    known: set[str] | None, current_version: str, current_instance_id: str | None, has_accounts: bool | None,
) -> Staged:
    """Entschluesselt `upload` nach `staging` (ein neuer, eigener Ordner) und prueft alles. Gibt
    es einen Fehler -- falsches Passwort, beschaedigte Datei, zu gross, nicht einspielbar --
    ist `staging` danach GELOESCHT. Nur nach erfolgreicher Rueckkehr darf es benutzt werden."""
    make_private_dir(staging.parent)
    staging.mkdir(mode=0o700)
    try:
        skipped: list[str] = []
        with open(upload, "rb") as fh:
            manifest = container.read_backup(
                fh, secret, staging, limits=limits, allow_file=fmt.is_restore_file, allow_dir=fmt.is_restore_dir,
                skip_entry=fmt.is_unwanted_restore_entry, skipped=skipped,
            )
        if skipped:
            logger.warning(
                "restore_skipped_vcs_entries count=%d examples=%s", len(skipped), ", ".join(sorted(skipped)[:5]),
            )
        facts = inspect_db(staging / fmt.ARC_DB)
        installed = installed_extension_ids(layout.extensions_dir)
        check_compatible(facts, manifest, known=known, installed=installed, current_version=current_version)
        files: dict[str, list] = {}
        total = 0
        skipped_names = set(skipped)
        skipped_files = 0
        for item in manifest["files"]:
            if item["path"] in skipped_names:
                # Nicht eingespielt, also auch nicht Teil dessen, was spaeter erneut geprueft wird; die Zahl
                # landet in der Zusammenfassung (Oberflaeche und Kommandozeile), nicht nur im Protokoll.
                skipped_files += 1
                continue
            files[item["path"]] = [item["sha256"], item["size"]]
            total += item["size"]
        db = manifest["db"]
        files[fmt.ARC_DB] = [db["sha256"], db["size"]]
        total += db["size"]
        summary = build_summary(
            manifest, facts, files, layout=layout, current_version=current_version,
            current_instance_id=current_instance_id, has_accounts=has_accounts, installed=installed,
            skipped_files=skipped_files,
        )
        compat = {
            "app_version": _text(manifest.get("app_version"), 32),
            "extensions": [
                {"id": e["id"], "alembic_heads": [h for h in e.get("alembic_heads", []) if isinstance(h, str)][:50]}
                for e in (manifest.get("extensions") or [])[:500]
                if isinstance(e, dict) and isinstance(e.get("id"), str)
            ],
        }
        return Staged(summary=summary, files=files, unpacked_bytes=total, compat=compat)
    except BaseException as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:  # ENOSPC
            raise NotEnoughSpace("Der Speicher ist beim Entpacken der Sicherung vollgelaufen.") from exc
        raise


# ---------------------------------------------------------------------------
# Zustand im Ordner restore/
# ---------------------------------------------------------------------------


def id_dir(layout: Layout, restore_id: str) -> Path:
    if not ID_RE.fullmatch(restore_id):
        raise ValueError("ungueltige ID")
    return layout.restore_dir / restore_id


def read_meta(layout: Layout, restore_id: str) -> dict[str, Any] | None:
    meta = read_json(id_dir(layout, restore_id) / META_NAME, max_bytes=256 * 1024)
    return meta if isinstance(meta, dict) else None


def write_meta(layout: Layout, restore_id: str, meta: dict[str, Any]) -> None:
    write_json_atomic(id_dir(layout, restore_id) / META_NAME, meta)


def list_ids(layout: Layout) -> list[str]:
    try:
        return sorted(e.name for e in os.scandir(layout.restore_dir) if ID_RE.fullmatch(e.name) and e.is_dir(follow_symlinks=False))
    except FileNotFoundError:
        return []


def validate_pending(raw: Any) -> dict[str, Any] | None:
    """Prueft eine gelesene Vormerkung streng; `None`, wenn irgendetwas nicht stimmt."""
    if not isinstance(raw, dict) or raw.get("version") != 1:
        return None
    rid, source, at = raw.get("id"), raw.get("source"), raw.get("scheduled_at")
    if not isinstance(rid, str) or not ID_RE.fullmatch(rid) or source not in SOURCES:
        return None
    if not isinstance(at, (int, float)) or isinstance(at, bool) or not isinstance(raw.get("sign_out_all"), bool):
        return None
    files = raw.get("files")
    if not isinstance(files, dict) or fmt.ARC_DB not in files:
        return None
    for name, value in files.items():
        if not isinstance(name, str) or not fmt.is_restore_file(name) or name == fmt.ARC_MANIFEST:
            return None
        if not (isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and _SHA_RE.fullmatch(value[0])
                and isinstance(value[1], int) and not isinstance(value[1], bool) and value[1] >= 0):
            return None
    if not isinstance(raw.get("actor"), dict) or not isinstance(raw.get("backup"), dict):
        return None
    return raw


def pending_fresh(pending: dict[str, Any], now: float) -> bool:
    """Gilt die Vormerkung noch? Hoechstens `PENDING_TTL_S` alt -- und auch nicht mehr als das
    "aus der Zukunft" (falsch gestellte Uhr beim Vormerken): sonst liefe sie nie ab."""
    return abs(now - pending["scheduled_at"]) <= PENDING_TTL_S


def read_pending(layout: Layout) -> dict[str, Any] | None:
    return validate_pending(read_json(layout.restore_dir / PENDING_NAME))


def pending_exists(layout: Layout) -> bool:
    return os.path.lexists(layout.restore_dir / PENDING_NAME)


def write_pending(
    layout: Layout, *, restore_id: str, source: str, actor: dict[str, Any], sign_out_all: bool, staged: Staged,
    now: float | None = None,
) -> dict[str, Any]:
    pending = {
        "version": 1, "id": restore_id, "source": source, "actor": actor, "sign_out_all": sign_out_all,
        "scheduled_at": time.time() if now is None else now, "files": staged.files,
        "backup": {k: staged.summary.get(k) for k in ("created_at", "app_version", "instance_id", "owner_name", "users", "hosts")},
        "compat": staged.compat,
    }
    if validate_pending(pending) is None:
        raise ValueError("Vormerkung ungueltig")
    write_json_atomic(layout.restore_dir / PENDING_NAME, pending)
    return pending


def clear_pending(layout: Layout) -> None:
    with contextlib.suppress(OSError):
        os.unlink(layout.restore_dir / PENDING_NAME)


def read_result(layout: Layout) -> dict[str, Any] | None:
    result = read_json(layout.restore_dir / RESULT_NAME, max_bytes=256 * 1024)
    return result if isinstance(result, dict) else None


def write_result(layout: Layout, result: dict[str, Any]) -> None:
    make_private_dir(layout.restore_dir)
    write_json_atomic(layout.restore_dir / RESULT_NAME, {**result, "audit_logged": bool(result.get("audit_logged", False))})


def now_iso(now: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def latest_replaced(layout: Layout) -> Path | None:
    try:
        names = sorted(e.name for e in os.scandir(layout.restore_dir) if e.name.startswith(REPLACED_PREFIX) and e.is_dir(follow_symlinks=False))
    except FileNotFoundError:
        return None
    return layout.restore_dir / names[-1] if names else None


def replaced_created(path: Path) -> float | None:
    """Wann der `replaced-...`-Ordner entstanden ist: aus dem Namen (UTC), sonst seine Aenderungszeit."""
    match = _REPLACED_TIME_RE.match(path.name)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return None
    try:
        return os.lstat(path).st_mtime
    except OSError:
        return None


def replaced_info(layout: Layout) -> dict[str, Any] | None:
    path = latest_replaced(layout)
    if path is None:
        return None
    size = 0
    for root, _dirs, names in os.walk(path, followlinks=False):
        for name in names:
            with contextlib.suppress(OSError):
                size += os.lstat(os.path.join(root, name)).st_size
    created = replaced_created(path)
    return {
        "name": path.name, "size": size, "at": _text(path.name.removeprefix(REPLACED_PREFIX), 32),
        "expires_at": now_iso(created + REPLACED_TTL_S) if created is not None else None,
    }


def sweep_replaced(layout: Layout, *, now: float | None = None) -> list[str]:
    """Loescht `replaced-...`-Ordner, die aelter als `REPLACED_TTL_S` (30 Tage) sind. Nie einen, den ein unfertiges
    Einspielen noch braucht (Journal), nie einen Link, nie einen mit unbekanntem oder zukuenftigem Alter."""
    now = time.time() if now is None else now
    protected: set[str] = set()
    journal = read_json(layout.restore_dir / JOURNAL_NAME)
    if isinstance(journal, dict) and isinstance(journal.get("replaced"), str):
        protected.add(Path(journal["replaced"]).name)
    removed: list[str] = []
    try:
        entries = [e for e in os.scandir(layout.restore_dir) if e.name.startswith(REPLACED_PREFIX) and e.is_dir(follow_symlinks=False)]
    except FileNotFoundError:
        return removed
    for entry in entries:
        if entry.name in protected:
            continue
        created = replaced_created(Path(entry.path))
        if created is not None and now - created > REPLACED_TTL_S:
            shutil.rmtree(entry.path, ignore_errors=True)
            removed.append(entry.name)
    return removed


def remove_replaced(layout: Layout) -> int:
    """Loescht alle `replaced-...`-Ordner (der alte Stand mit Klartext-Schluesseln)."""
    removed = 0
    try:
        entries = [e for e in os.scandir(layout.restore_dir) if e.name.startswith(REPLACED_PREFIX) and e.is_dir(follow_symlinks=False)]
    except FileNotFoundError:
        return 0
    for entry in entries:
        shutil.rmtree(entry.path, ignore_errors=True)
        removed += 1
    return removed


def cleanup_restore_dir(layout: Layout, *, keep: set[str] = frozenset()) -> None:
    """Loescht alle ID-Ordner (Upload und Staging) ausser den genannten."""
    for rid in list_ids(layout):
        if rid not in keep:
            shutil.rmtree(layout.restore_dir / rid, ignore_errors=True)


def sweep(layout: Layout, *, now: float | None = None, keep: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Raeumt Aufgegebenes weg: ID-Ordner, die aelter als `STAGING_TTL_S` sind, ausser sie
    gehoeren zu einer noch gueltigen Vormerkung, einem laufenden Einspielen oder sind in `keep`
    (gerade in Arbeit, z. B. eine laufende Pruefung). Auch eine abgelaufene Vormerkung selbst, und der alte
    Stand nach 30 Tagen (`sweep_replaced`)."""
    now = time.time() if now is None else now
    removed: list[str] = []
    pending = read_pending(layout)
    journal = read_json(layout.restore_dir / JOURNAL_NAME)
    protected: set[str] = set(keep)
    if pending is not None and pending_fresh(pending, now):
        protected.add(pending["id"])
    elif pending_exists(layout):
        clear_pending(layout)
        removed.append(PENDING_NAME)
    if isinstance(journal, dict) and isinstance(journal.get("id"), str):
        protected.add(journal["id"])
    for rid in list_ids(layout):
        if rid in protected:
            continue
        directory = layout.restore_dir / rid
        meta = read_meta(layout, rid) or {}
        created = meta.get("created_at")
        if not isinstance(created, (int, float)):
            with contextlib.suppress(OSError):
                created = os.lstat(directory).st_mtime
        if not isinstance(created, (int, float)) or now - created > STAGING_TTL_S:
            shutil.rmtree(directory, ignore_errors=True)
            removed.append(rid)
    with contextlib.suppress(OSError):
        for entry in os.scandir(layout.restore_dir):
            is_leftover = entry.name.endswith(".tmp") and entry.name.startswith(".") and entry.is_file(follow_symlinks=False)
            if is_leftover and now - entry.stat(follow_symlinks=False).st_mtime > STAGING_TTL_S:
                os.unlink(entry.path)
    removed += sweep_replaced(layout, now=now)
    return removed


# ---------------------------------------------------------------------------
# Einspielen (laeuft in boot.py, vor Migration und Anwendung)
# ---------------------------------------------------------------------------


def hash_tree(root: Path) -> dict[str, list]:
    """Alle Dateien unter `root` mit sha256 und Groesse. Alles ausser normalen Dateien und
    Ordnern (Links, Geraete, ...) oder ein nicht erlaubter Name ist ein Fehler."""
    found: dict[str, list] = {}

    def walk(directory: Path, rel: PurePosixPath) -> None:
        with os.scandir(directory) as it:
            entries = sorted(it, key=lambda e: e.name)
        for entry in entries:
            child = rel / entry.name
            st = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                if not fmt.is_restore_dir(child.as_posix()):
                    raise DamagedBackup("unerwarteter Ordner im Staging")
                walk(Path(entry.path), child)
            elif stat.S_ISREG(st.st_mode):
                if not fmt.is_restore_file(child.as_posix()) or child.as_posix() == fmt.ARC_MANIFEST:
                    raise DamagedBackup("unerwartete Datei im Staging")
                digest, size = snapshot.sha256_file(Path(entry.path))
                found[child.as_posix()] = [digest, size]
            else:
                raise DamagedBackup("unerwarteter Eintrag im Staging")

    walk(root, PurePosixPath())
    return found


@dataclass
class Applied:
    """Ein eingespieltes, aber noch nicht endgueltiges Einspielen (siehe Modul-Docstring)."""

    layout: Layout
    pending: dict[str, Any]
    plan: list[dict[str, Any]]
    replaced: Path
    now: float
    has_users: bool = False
    """Die eingespielte Datenbank hat Konten (dann braucht es keinen Einrichtungscode mehr)."""

    def commit(self) -> None:
        """Endgueltig: Journal auf `done`, Ergebnis schreiben, Aufraeumen. Der Einrichtungscode wird
        erst jetzt entfernt -- beim Zurueckrollen bleibt er, wie er war."""
        journal = read_json(self.layout.restore_dir / JOURNAL_NAME) or {}
        journal["state"] = "done"
        write_json_atomic(self.layout.restore_dir / JOURNAL_NAME, journal)
        if self.has_users:
            from ..setup_code import clear

            clear(self.layout.data_dir)
        finish_ok(self.layout, self.pending, self.replaced.name, now=self.now)

    def rollback(self, reason: str) -> None:
        """Alles zurueck auf den alten Stand und das Scheitern festhalten."""
        rollback_plan(self.layout, self.plan)
        finish_failed(self.layout, self.pending, reason, now=self.now)


def finish_ok(layout: Layout, pending: dict[str, Any], replaced_name: str, *, now: float) -> None:
    write_result(layout, {
        "ok": True, "at": now_iso(), "id": pending["id"], "source": pending["source"], "actor": pending["actor"],
        "message": "Die Sicherung wurde eingespielt.", "backup": pending["backup"], "replaced": replaced_name,
        "sign_out_all": pending["sign_out_all"],
    })
    shutil.rmtree(layout.restore_dir / pending["id"], ignore_errors=True)
    clear_pending(layout)
    with contextlib.suppress(OSError):
        os.unlink(layout.restore_dir / JOURNAL_NAME)
    for entry in _replaced_entries(layout):
        if entry != replaced_name:
            shutil.rmtree(layout.restore_dir / entry, ignore_errors=True)


def finish_failed(layout: Layout, pending: dict[str, Any] | None, reason: str, *, now: float | None = None) -> None:
    write_result(layout, {
        "ok": False, "at": now_iso(), "id": pending["id"] if pending else None, "source": pending["source"] if pending else None,
        "actor": pending["actor"] if pending else {}, "message": reason, "rolled_back": True,
        "backup": pending["backup"] if pending else {},
    })
    if pending:
        shutil.rmtree(layout.restore_dir / pending["id"], ignore_errors=True)
    clear_pending(layout)
    with contextlib.suppress(OSError):
        os.unlink(layout.restore_dir / JOURNAL_NAME)


def _replaced_entries(layout: Layout) -> list[str]:
    try:
        return sorted(e.name for e in os.scandir(layout.restore_dir) if e.name.startswith(REPLACED_PREFIX) and e.is_dir(follow_symlinks=False))
    except FileNotFoundError:
        return []


def rollback_plan(layout: Layout, plan: list[dict[str, Any]]) -> None:
    """Stellt den alten Stand her -- anhand dessen, was auf der Platte liegt, nicht anhand
    dessen, was gemeldet wurde (ein Absturz kann zwischen Schritt und Eintrag liegen).
    Angefasst werden nur Ziele, die zum aktuellen Aufbau gehoeren, und Ablagen unter `restore/`."""
    allowed = {str(t.path) for t in targets(layout)}
    restore_root = os.path.realpath(layout.restore_dir)
    errors: list[str] = []
    for item in reversed(plan):
        try:
            target, saved = Path(item["target"]), Path(item["saved"])
            if str(target) not in allowed or not os.path.realpath(saved).startswith(restore_root + os.sep):
                raise RollbackFailed("Journal passt nicht zum aktuellen Aufbau")
            if item["had_old"]:
                if os.path.lexists(saved):
                    remove_path(target)
                    _move(saved, target)
            else:
                remove_path(target)
        except Exception as exc:
            logger.exception("restore_rollback_step_failed target=%s", item.get("target"))
            errors.append(f"{item.get('name')}: {type(exc).__name__}")
    if errors:
        raise RollbackFailed(
            "Der alte Stand konnte nicht vollständig zurückgespielt werden (" + ", ".join(errors) + "). "
            f"Er liegt noch unter {layout.restore_dir}/{REPLACED_PREFIX}…"
        )


def _post_restore(layout: Layout, pending: dict[str, Any]) -> bool:
    """Nach dem Verschieben, in der eingespielten Datenbank: Anmeldungen beenden. Gibt zurueck,
    ob es Konten gibt (dann entfernt `Applied.commit` den Einrichtungscode)."""
    conn = sqlite3.connect(os.fspath(layout.db_path), timeout=30)
    try:
        conn.execute("PRAGMA trusted_schema=OFF")
        if pending["sign_out_all"]:
            try:
                conn.execute("DELETE FROM refresh_tokens")
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise
        conn.commit()
        users = _scalar(conn, "SELECT count(*) FROM users")
    finally:
        conn.close()
    for suffix in ("-wal", "-shm", "-journal"):
        with contextlib.suppress(OSError):
            os.unlink(str(layout.db_path) + suffix)
    return isinstance(users, int) and users > 0


def _has_users(db_path: Path) -> bool | None:
    """Gibt es im aktuellen Stand schon ein Konto? (`None`: nicht feststellbar -- der Aufrufer
    behandelt das wie "ja", denn eine Wiederherstellung aus dem Assistenten darf nie ein
    bestehendes Konto ersetzen.) Nur "keine Datei" und "keine Tabelle users" gelten als leer."""
    if not os.path.lexists(db_path):
        return False
    try:
        conn = sqlite3.connect(f"file:{urllib.parse.quote(os.fspath(db_path))}?mode=ro", uri=True, timeout=5)
    except sqlite3.DatabaseError:
        return None
    try:
        row = conn.execute("SELECT count(*) FROM users").fetchone()
        return bool(row and row[0] > 0)
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return False  # noch keine Tabellen: frische Installation
        return None  # gesperrt, kaputt, nicht lesbar: lieber ablehnen
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()


def apply_pending(
    layout: Layout, *, known: set[str] | None, current_version: str, now: float | None = None,
) -> Applied | None:
    """Spielt eine vorgemerkte Wiederherstellung ein. `None`: nichts vorgemerkt. Sonst ein
    `Applied`, das der Aufrufer nach der Migration mit `commit()` endgueltig macht oder mit
    `rollback()` zuruecknimmt. Scheitert etwas, ist der alte Stand zurueck, `result.json`
    erklaert es, und es wird `RestoreFailed` geworfen (der Start geht normal weiter).
    Scheitert der Rueckweg, wird `RollbackFailed` geworfen (der Start darf dann nicht weitergehen)."""
    now = time.time() if now is None else now
    if not layout.restore_dir.is_dir() or not pending_exists(layout):
        return None
    raw = read_json(layout.restore_dir / PENDING_NAME)
    pending = validate_pending(raw)
    if pending is None:
        clear_pending(layout)
        finish_failed(layout, None, "Die Vormerkung war beschädigt und wurde verworfen.")
        raise RestoreFailed("Die Vormerkung war beschädigt und wurde verworfen.")
    try:
        return _apply(layout, pending, known=known, current_version=current_version, now=now)
    except (RollbackFailed, RestoreFailed):
        raise
    except Exception as exc:
        logger.exception("restore_apply_failed")
        text = f"Das Einspielen ist nicht gelungen ({type(exc).__name__}). Der alte Stand wurde beibehalten."
        finish_failed(layout, pending, text)
        raise RestoreFailed(text) from exc


def _apply(layout: Layout, pending: dict[str, Any], *, known: set[str] | None, current_version: str, now: float) -> Applied:
    def fail(reason: str) -> RestoreFailed:
        finish_failed(layout, pending, reason)
        return RestoreFailed(reason)

    if not pending_fresh(pending, now):
        raise fail("Die Vormerkung war abgelaufen (älter als eine Stunde) und wurde nicht eingespielt. Bitte noch einmal vormerken.")
    staging = layout.restore_dir / pending["id"] / STAGING_NAME
    try:
        if not stat.S_ISDIR(os.lstat(staging).st_mode):
            raise FileNotFoundError
        actual = hash_tree(staging)
    except (OSError, DamagedBackup):
        raise fail("Die vorbereiteten Daten fehlen oder wurden verändert. Bitte die Sicherung noch einmal hochladen.") from None
    if actual != pending["files"]:
        raise fail("Die vorbereiteten Daten wurden verändert. Bitte die Sicherung noch einmal hochladen.")
    if pending["source"] == "bootstrap":
        accounts = _has_users(layout.db_path)
        if accounts is None:
            raise fail("Ob es inzwischen ein Konto gibt, ließ sich nicht feststellen. Eine Wiederherstellung aus der Einrichtung wird dann nicht eingespielt.")
        if accounts:
            raise fail("Inzwischen wurde ein Konto angelegt. Eine Wiederherstellung aus der Einrichtung ist dann nicht mehr erlaubt.")
    try:
        facts = inspect_db(staging / fmt.ARC_DB)
        compat = pending.get("compat") if isinstance(pending.get("compat"), dict) else {}
        check_compatible(
            facts,
            {"db": {"alembic_heads": facts.versions}, "extensions": compat.get("extensions") or [], "app_version": compat.get("app_version")},
            known=known, installed=installed_extension_ids(layout.extensions_dir), current_version=current_version,
        )
    except BackupError as exc:
        raise fail(str(exc)) from exc

    replaced = layout.restore_dir / f"{REPLACED_PREFIX}{datetime.fromtimestamp(now, tz=timezone.utc):%Y%m%dT%H%M%S}"
    while os.path.lexists(replaced):
        replaced = replaced.with_name(replaced.name + "x")
    plan: list[dict[str, Any]] = []
    for target in targets(layout):
        staged = staging / target.staged if target.staged else None
        staged_exists = staged is not None and os.path.lexists(staged)
        had_old = os.path.lexists(target.path)
        # Auch was es vorher NICHT gab, steht im Plan: der Rueckweg raeumt es dann weg. Wichtig fuer
        # `-wal`/`-journal`: stirbt die Migration der eingespielten Datenbank mittendrin, bleiben deren
        # Nebendateien liegen -- neben der zurueckgeholten alten Datenbank wuerde SQLite sie auf DIESE
        # anwenden (eine fremde WAL bzw. ein "heisses" Journal) und sie damit zerstoeren.
        plan.append({
            "name": target.name, "target": str(target.path), "saved": str(replaced / target.name),
            "staged": str(staged) if staged_exists else None, "had_old": had_old,
        })
    light = {**pending, "files": {fmt.ARC_DB: pending["files"][fmt.ARC_DB]}}  # das Journal braucht die Pruefsummen nicht
    journal = {"version": 1, "id": pending["id"], "state": "applying", "replaced": str(replaced), "plan": plan, "pending": light}
    make_private_dir(replaced)
    write_json_atomic(layout.restore_dir / JOURNAL_NAME, journal)
    clear_pending(layout)  # ab jetzt zaehlt das Journal; eine abgebrochene Vormerkung wird nie zweimal versucht

    try:
        for item in plan:
            target = Path(item["target"])
            if item["had_old"]:
                _move(target, Path(item["saved"]))
            if item["staged"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                _move(Path(item["staged"]), target)
        has_users = _post_restore(layout, pending)
        for directory in (layout.ext_dir, layout.runs_dir):
            directory.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.exception("restore_apply_failed")
        try:
            rollback_plan(layout, plan)
        except RollbackFailed:
            write_result(layout, {
                "ok": False, "at": now_iso(), "id": pending["id"], "source": pending["source"], "actor": pending["actor"],
                "message": "Das Einspielen ist gescheitert und der alte Stand konnte nicht vollständig zurückgespielt werden. "
                          f"Er liegt unter restore/{replaced.name}.", "rolled_back": False, "backup": pending["backup"],
            })
            raise
        reason = (
            "Das Einspielen ist nicht gelungen (" + (str(exc) if isinstance(exc, OSError) else type(exc).__name__) + "). "
            "Alles ist wie vorher. Liegen Teile des Datenordners auf einem eigenen Laufwerk, geht das Verschieben nicht."
        )
        raise fail(reason) from exc
    return Applied(layout=layout, pending=pending, plan=plan, replaced=replaced, now=now, has_users=has_users)


def recover_interrupted(layout: Layout) -> dict[str, Any] | None:
    """Beim Start zuerst aufrufen: Gab es ein Einspielen, das mitten drin abbrach (Strom weg,
    Absturz), wird es zurueckgenommen -- ausser es war schon fertig. Gibt das Ergebnis zurueck."""
    path = layout.restore_dir / JOURNAL_NAME
    if not os.path.lexists(path):
        return None
    journal = read_json(path)
    pending = validate_pending(journal.get("pending")) if isinstance(journal, dict) else None
    if not isinstance(journal, dict) or pending is None or not isinstance(journal.get("plan"), list):
        # Journal unlesbar: nichts anfassen, nichts raten.
        raise RollbackFailed(f"Das Journal der Wiederherstellung ({path}) ist unlesbar. Bitte {layout.restore_dir} ansehen.")
    if journal.get("state") == "done":
        result = read_result(layout)
        if result is None or result.get("id") != pending["id"]:
            finish_ok(layout, pending, Path(str(journal.get("replaced", ""))).name, now=time.time())
        else:
            shutil.rmtree(layout.restore_dir / pending["id"], ignore_errors=True)
            clear_pending(layout)
            with contextlib.suppress(OSError):
                os.unlink(path)
        return read_result(layout)
    rollback_plan(layout, journal["plan"])
    finish_failed(layout, pending, "Das Einspielen wurde mittendrin unterbrochen (Neustart oder Absturz). Der alte Stand ist wieder da.")
    return read_result(layout)
