"""Kopf, Manifest, Namens- und Pfadregeln einer Sicherung. Reine Funktionen ohne E/A
(bis auf das Lesen des Kopfes aus einem offenen Strom).

Der Kopf (Zeile 2) ist Klartext und NICHT authentifiziert -- er sagt nur, wie die Datei zu
oeffnen ist. Damit ein veraenderter Kopf auffaellt, steht eine Kopie davon im Manifest
(im verschluesselten Teil); `check_manifest` vergleicht beide.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, BinaryIO

from .errors import DamagedBackup, NewerBackup

MAGIC_PREFIX = b"NODVARD-DECK-BACKUP/"
MAGIC = MAGIC_PREFIX + b"1\n"
FORMAT_VERSION = 1
MAX_HEADER_BYTES = 4096
MODES = ("passwort", "schluessel")

FILE_PREFIX = "nodvard-deck-sicherung-"
FILE_SUFFIX = ".ndbak"
SIDECAR_SUFFIX = ".json"
NAME_RE = re.compile(r"^nodvard-deck-sicherung-\d{8}-\d{6}\.ndbak$")

ARC_MANIFEST = "manifest.json"
ARC_DB = "db/lattice.db"
ARC_FILES = "files/"
MAX_MANIFEST_BYTES = 32 * 1024 * 1024

EXCLUDED_TOP_LEVEL = frozenset({"backups", "restore", ".boot"})
EXCLUDED_NAMES = frozenset({"setup_code.txt"})
EXCLUDED_SUFFIXES = ("-wal", "-shm", "-journal", ".tmp", ".part", ".swp", "~")


def backup_name(local_time: datetime) -> str:
    return f"{FILE_PREFIX}{local_time:%Y%m%d-%H%M%S}{FILE_SUFFIX}"


def is_backup_name(name: str) -> bool:
    return bool(NAME_RE.fullmatch(name))


def build_header(
    *, created_at: datetime, app_version: str, mode: str, key_id: str | None = None, kdf: dict | None = None
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"unbekannter Modus {mode!r}")
    header: dict[str, Any] = {
        "format": FORMAT_VERSION,
        "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app_version": app_version,
        "mode": mode,
    }
    if mode == "schluessel":
        if not key_id or kdf is None:
            raise ValueError("Schlüssel-Modus braucht key_id und kdf")
        header["key_id"] = key_id
        header["kdf"] = kdf
    return header


def encode_header(header: dict[str, Any]) -> bytes:
    line = json.dumps(header, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
    if len(line) > MAX_HEADER_BYTES:
        raise ValueError("Kopf zu gross")
    return MAGIC + line


def read_header(reader: BinaryIO) -> dict[str, Any]:
    """Liest Zeile 1 und 2 und laesst den Strom direkt am Anfang der age-Datei stehen."""
    first = reader.readline(64)
    if not first.startswith(MAGIC_PREFIX) or not first.endswith(b"\n"):
        raise DamagedBackup("keine Sicherung von Nodvard Deck")
    if first != MAGIC:
        version = first[len(MAGIC_PREFIX):-1]
        if version.isdigit() and int(version) > FORMAT_VERSION:
            raise NewerBackup(int(version), FORMAT_VERSION)
        raise DamagedBackup("Kopf ungültig")
    line = reader.readline(MAX_HEADER_BYTES + 1)
    if not line.endswith(b"\n") or len(line) > MAX_HEADER_BYTES:
        raise DamagedBackup("Kopf ungültig")
    try:
        header = json.loads(line)
    except ValueError as exc:
        raise DamagedBackup("Kopf ungültig") from exc
    return validate_header(header)


def validate_header(header: object) -> dict[str, Any]:
    if not isinstance(header, dict):
        raise DamagedBackup("Kopf ungültig")
    fmt = header.get("format")
    if not isinstance(fmt, int) or isinstance(fmt, bool):
        raise DamagedBackup("Kopf ungültig")
    if fmt > FORMAT_VERSION:
        raise NewerBackup(fmt, FORMAT_VERSION)
    if fmt != FORMAT_VERSION:
        raise DamagedBackup("Kopf ungültig")
    for field in ("created_at", "app_version", "mode"):
        if not isinstance(header.get(field), str):
            raise DamagedBackup("Kopf ungültig")
    if header["mode"] not in MODES:
        raise DamagedBackup("Kopf ungültig")
    if header["mode"] == "schluessel":
        if not isinstance(header.get("key_id"), str) or not re.fullmatch(r"[0-9a-f]{16}", header["key_id"]):
            raise DamagedBackup("Kopf ungültig")
        if not isinstance(header.get("kdf"), dict):
            raise DamagedBackup("Kopf ungültig")
    return header


def build_manifest(
    *,
    header: dict[str, Any],
    build: str | None,
    instance_id: str,
    jwt_from_env: bool,
    db: dict[str, Any],
    extensions: list[dict[str, Any]],
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "format": header["format"],
        "app_version": header["app_version"],
        "build": build,
        "created_at": header["created_at"],
        "instance_id": instance_id,
        "jwt_from_env": jwt_from_env,
        "header": header,
        "db": db,
        "extensions": extensions,
        "files": files,
    }


def check_manifest(manifest: object, header: dict[str, Any]) -> dict[str, Any]:
    """Manifest muss zum Kopf passen -- sonst wurde der Kopf veraendert oder die Datei
    zusammengestueckelt."""
    if not isinstance(manifest, dict):
        raise DamagedBackup("Inhaltsverzeichnis ungültig")
    if manifest.get("header") != header:
        raise DamagedBackup("Kopf passt nicht zum Inhalt")
    for field in ("format", "app_version", "created_at"):
        if manifest.get(field) != header.get(field):
            raise DamagedBackup("Kopf passt nicht zum Inhalt")
    if not isinstance(manifest.get("files"), list) or not isinstance(manifest.get("db"), dict):
        raise DamagedBackup("Inhaltsverzeichnis ungültig")
    return manifest


def is_safe_member(name: str) -> bool:
    """Nur `manifest.json`, `db/lattice.db` und Dateien unter `files/`: kein absoluter
    Pfad, kein `..`, keine Rueckwaertsstriche, keine leeren Teile."""
    if not name or len(name) > 1024 or "\\" in name or "\x00" in name:
        return False
    if name in (ARC_MANIFEST, ARC_DB):
        return True
    if not name.startswith(ARC_FILES):
        return False
    parts = name.split("/")
    return len(parts) >= 2 and all(p not in ("", ".", "..") for p in parts)


RESTORE_SINGLE_FILES = frozenset({"master.key", "vault_keyring.json", "jwt_secret.key"})
RESTORE_TREES = ("ext", "branding", "runs")
"""Was beim Wiederherstellen ueberhaupt angenommen wird -- genau das, was `services/backups.py`
(`sources_for`) schreibt: drei Schluesseldateien und die Ordner `ext`, `branding`, `runs` unter
`files/`, dazu `db/lattice.db` und `manifest.json`. Alles andere ist fremd."""

MAX_MEMBER_DEPTH = 40

_VCS_SETTING_NAMES = frozenset({".gitattributes", ".gitconfig", ".gitmodules"})
_NTFS_SHORT_NAME_RE = re.compile(r"(git|gitatt|gitcon|gitmod)~[0-9]+")
_NTFS_SHORT_NAMES = {"git": ".git", "gitatt": ".gitattributes", "gitcon": ".gitconfig", "gitmod": ".gitmodules"}
"""Kurznamen, unter denen NTFS die Git-Namen ebenfalls erreichbar macht (`GIT~1/config` landet in
`.git/config`). Gemeint sind die ersten sechs Zeichen ohne den Punkt plus `~` und eine Zahl."""
_NTFS_HASH_NAME_RE = re.compile(r"gi[0-9a-f]{4}~[0-9]+")
"""Ersatzform, die Windows ab dem fuenften gleichen Kurznamen bildet: die ersten zwei Zeichen, vier
Hex-Ziffern aus dem Namen und `~` mit Zahl (`.gitmodules` wird so zu `GI7EBA~1`). Alle vier Git-Namen
beginnen mit `gi`; solche Teile zaehlen wie `.git`: als Datei uebersprungen, als Ordner nur Verlauf."""
_HEX2_RE = re.compile(r"[0-9a-f]{2}")
_OBJECT_RE = re.compile(r"[0-9a-f]{38,62}")
_PACK_RE = re.compile(r"pack-[0-9a-f]{40,64}\.(pack|idx|rev)")


def _git_inner_ok(rest: Sequence[str], *, is_dir: bool) -> bool:
    """Was unterhalb eines `.git`-Ordners uebernommen wird: nur die Daten des Verlaufs (Objekte,
    Verweise, `HEAD`, Index). Alles, wonach Git Programme starten oder Einstellungen lesen
    koennte (`config`, `hooks/`, `info/`, `commondir`, `worktrees/` ...), nicht."""
    if not rest:
        return is_dir
    top = rest[0]
    if top == "refs":
        return is_dir or len(rest) >= 2
    if top in ("HEAD", "index", "packed-refs"):
        return len(rest) == 1 and not is_dir
    if top != "objects":
        return False
    if len(rest) == 1:
        return is_dir
    sub = rest[1]
    if sub == "pack":
        if len(rest) == 2:
            return is_dir
        return len(rest) == 3 and not is_dir and bool(_PACK_RE.fullmatch(rest[2]))
    if _HEX2_RE.fullmatch(sub):
        if len(rest) == 2:
            return is_dir
        return len(rest) == 3 and not is_dir and bool(_OBJECT_RE.fullmatch(rest[2]))
    return False


def is_unwanted_vcs_entry(parts: Sequence[str], *, is_dir: bool = False) -> bool:
    """Gehoert der Pfad (Teile unterhalb von `ext/`, `branding/`, `runs/`) zu den Git-Einstellungen
    eines Ordners? Dann wird er weder gesichert noch eingespielt: eine fremde Sicherung koennte dort
    ein Repository mit Filtern, Hooks oder Attributen unterbringen, die Git beim naechsten Speichern
    ausfuehrt. Die Daten des Verlaufs (Objekte, Verweise) bleiben erlaubt; die Einstellungen legt
    die Erweiterung beim Oeffnen selbst frisch an. `.gitignore` fuehrt nichts aus und bleibt erlaubt."""
    for index, part in enumerate(parts):
        # Windows liest `.git.`, `.git ` und `.git::$INDEX_ALLOCATION` als `.git`, NTFS dazu `GIT~1`.
        low = part.split(":", 1)[0].rstrip(" .").casefold()
        short = _NTFS_SHORT_NAME_RE.fullmatch(low)
        if short:
            low = _NTFS_SHORT_NAMES[short.group(1)]
        elif _NTFS_HASH_NAME_RE.fullmatch(low):
            low = ".git"
        if low in _VCS_SETTING_NAMES:
            return True
        if low == ".git":
            return not _git_inner_ok(parts[index + 1:], is_dir=is_dir)
    return False


def is_unwanted_restore_entry(name: str, is_dir: bool) -> bool:
    """Ein sonst erlaubter Eintrag einer Sicherung, der beim Einspielen still uebersprungen wird
    (siehe `is_unwanted_vcs_entry`). Fuer alles, was ohnehin abgelehnt wird, `False`."""
    parts = _clean_parts(name)
    if parts is None or len(parts) < 3 or parts[0] != "files" or parts[1] not in RESTORE_TREES:
        return False
    return is_unwanted_vcs_entry(parts[2:], is_dir=is_dir)


def _clean_parts(name: str) -> list[str] | None:
    """Pfadteile eines Archivnamens oder `None`, wenn der Name nicht sauber ist (leer,
    absolut, `..`, Rueckwaertsstrich, Steuerzeichen, zu lang oder zu tief)."""
    if not name or len(name) > 1024 or "\\" in name:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        return None
    try:
        name.encode("utf-8")  # kein gueltiges UTF-8 im Archiv (tarfile liefert dann Ersatzzeichen)
    except UnicodeEncodeError:
        return None
    parts = name.split("/")
    if len(parts) > MAX_MEMBER_DEPTH or any(p in ("", ".", "..") for p in parts):
        return None
    return parts


def is_restore_file(name: str) -> bool:
    """Strenge Erlaubnisliste fuer Dateien beim Wiederherstellen (siehe `RESTORE_*`)."""
    parts = _clean_parts(name)
    if parts is None:
        return False
    if name in (ARC_MANIFEST, ARC_DB):
        return True
    if parts[0] != "files" or len(parts) < 2:
        return False
    if len(parts) == 2:
        return parts[1] in RESTORE_SINGLE_FILES
    return parts[1] in RESTORE_TREES and not is_unwanted_vcs_entry(parts[2:])


def is_restore_dir(name: str) -> bool:
    """Ordner-Eintraege (tar kennt sie; Nodvard Deck selbst schreibt keine): nur die Ordner, die
    eine erlaubte Datei enthalten duerfte."""
    parts = _clean_parts(name)
    if parts is None:
        return False
    if parts == ["db"] or parts == ["files"]:
        return True
    if len(parts) >= 2 and parts[0] == "files" and parts[1] in RESTORE_TREES:
        return not is_unwanted_vcs_entry(parts[2:], is_dir=True)
    return False


def is_excluded(relative: PurePosixPath) -> bool:
    """Regeln fuer Dateien aus dem Datenordner (Pfad relativ dazu)."""
    if not relative.parts:
        return True
    if relative.parts[0] in EXCLUDED_TOP_LEVEL:
        return True
    name = relative.name
    if name in EXCLUDED_NAMES or name.startswith(".nodvard-tmp"):
        return True
    if is_unwanted_vcs_entry(relative.parts[1:]):
        return True
    return name.endswith(EXCLUDED_SUFFIXES)
