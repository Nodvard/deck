"""Stacks aus Git einspielen: Deck schreibt einen geholten Stand über das Gate in den Ordner des Stacks, prüft ihn mit
Compose, wendet ihn an und hält einen Schritt zurück bereit."""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import stat as statmod
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk import REQUEST_WAIT_S
from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Actor, Risk
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .commands import VOLUME_NAME_RE
from .compose_errors import scrub_compose_output
from .editor import (
    _SFTP_OP_UNSUPPORTED,
    Checked,
    _replace,
    _stack_key,
    _temp_path,
    _write_private,
    parse_check,
    risky_settings,
    sha256,
)
from .git_fetch import Fetched, GitProblem, load_compose
from .git_source import (
    CHANGED,
    ENV_NAME,
    NO_SOURCE,
    GitStacks,
    content_problems,
    env_file,
    layout_problem,
    server_path,
)
from .image_apply import PROJECT_RE, SERVICE_RE
from .stack_actions import (
    IN_FOLDER,
    SERVICES_TIMEOUT_S,
    DockerStackExecutor,
    busy_text,
    cmd_all_services,
)
from .stack_backup import _gb, restore_hint
from .stack_update import DockerStackUpdateExecutor, StackUpdate
from .stacks import Stack, StackProblem, compose_argv, no_such_file

log = logging.getLogger(__name__)

APPLY_ACTION = "docker.stack_git_apply"
ROLLBACK_ACTION = "docker.stack_git_rollback"
RESTART_ACTION = "docker.stack_git_restart"
RESTART_UNCHECKED = "Prüfung danach übersprungen: Docker zeigt zu diesen Diensten keinen Container."
RESTART_UNREADABLE = "Prüfung danach nicht möglich: Die Container ließen sich vorher nicht lesen."
RISK = Risk.HIGH
"""Der Inhalt kommt von außen, und ob er riskante Einstellungen bringt, zeigt Compose erst, wenn er auf dem Server liegt."""
CHECK_TIMEOUT_S = 60
DRAFT_TTL_S = 24 * 3600
MAX_DRAFTS = 20
_ID_RE = re.compile(r"^[0-9a-f]{32}\Z")
_DIR_MODE = 0o755
_PRIVATE_MODE = 0o600
ALLOWED_MODES = frozenset({0o600, 0o644, 0o755})
STEP_TIMEOUT_S = 60
NO_VOLUME_BACKUP = "Volumes werden vorher nicht gesichert."
NO_STACK_VOLUMES = ("Kein Container des Stacks nutzt ein benanntes Volume, deshalb gibt es nichts zu sichern; Daten in Ordnern auf dem Server "
                    "werden nicht mitgesichert.")
BACKUP_CHANGED = "Volumes oder Dienste des Stacks haben sich seit dem Vorschlag geändert. Eingespielt wurde nichts; bitte neu vorschlagen."
APPLY_FAILED = "Geschrieben, aber das Anwenden ist gescheitert"
SERVER_FILE_LIMIT = 10 * 1024 * 1024
"""Größer darf eine vorhandene Datei nicht sein, die Deck ersetzt oder entfernt."""
SERVER_TOTAL_LIMIT = 64 * 1024 * 1024
"""So viel liest Deck vom Server höchstens ein; die alten Fassungen hält es bis zum Schreiben im Speicher."""
RESTART_TIMEOUT_S = 900
NAMES_SHOWN = 10
NAME_CHARS = 80
"""So viele Zeichen eines Namens zeigt die Freigabe, vom Ende her; mehr verdrängt nur andere Hinweise."""
REASON_LIMIT = 4000

NOT_A_USER = "Einen Stand aus Git spielt nur ein Mensch über die Docker-Seite ein."
STALE = "Auf dem Server hat sich seit dem Vorschlag etwas geändert. Hol den Stand noch einmal und sieh ihn dir an."
NO_PREVIOUS = "Für diesen Stack gibt es keinen Stand vor dem letzten Einspielen."
UNSEEN_RISK = "Compose zeigt nach dem Schreiben riskante Einstellungen, die nicht in der Freigabe standen: {lines}"
STALE_SERVICES = "Diese Dienste lesen vielleicht noch den alten Stand: {names}."
MAX_SERVICES = 50


def action_specs() -> list[ActionSpec]:
    # host_bound=False: Dateien und Commit legt immer die Docker-Seite fest, nie ein Formular auf der Server-Seite.
    common: dict[str, Any] = {"permissions": ["hosts.execute"], "host_bound": False, "default_risk": RISK}
    return [
        ActionSpec(
            action_type=APPLY_ACTION, label="Stand aus Git einspielen", icon="download",
            description="Schreibt den geholten Stand eines Repositorys in den Ordner eines übernommenen Stacks, prüft ihn mit Compose "
            "und wendet ihn an.",
            confirm_text="Die Dateien aus dem Repository werden auf den Server geschrieben, Compose wendet den Stack an, und die in der "
            "Freigabe genannten Dienste starten neu. Ist eine Sicherung der Volumes gewählt, stehen die genannten Dienste dafür still. "
            "Der Stand davor bleibt für einen Schritt zurück. Fortfahren?",
            **common,
        ),
        ActionSpec(
            action_type=ROLLBACK_ACTION, label="Zurück zum Stand vor dem Einspielen", icon="rotate-ccw",
            description="Stellt die Dateien wieder her, die das letzte Einspielen aus Git ersetzt oder entfernt hat, und wendet den Stack an.",
            confirm_text="Die Dateien vor dem letzten Einspielen kommen zurück, Compose wendet den Stack an, und die in der Freigabe "
            "genannten Dienste starten neu. Fortfahren?",
            **common,
        ),
        ActionSpec(
            action_type=RESTART_ACTION, label="Dienste nach dem Einspielen neu starten", icon="rotate-cw",
            description="Startet Dienste eines Stacks neu, die nach dem Einspielen aus Git vielleicht noch den alten Stand lesen.",
            confirm_text="Die genannten Dienste starten neu. Fortfahren?",
            **{**common, "default_risk": Risk.MEDIUM},
        ),
    ]


def cmd_check_disk(stack: Stack) -> list[str]:
    """`docker compose config` im Ordner des Stacks, mit den Dateien so, wie sie jetzt auf dem Server liegen."""
    return ["sh", "-c", IN_FOLDER, "sh", stack.working_dir, *compose_argv(stack, "--profile", "*", "config", "--format", "json")]


def valid_service_names(raw: Any) -> bool:
    """Eine Liste gültiger, verschiedener Dienstnamen, wie sie für einen Neustart in Frage kommt."""
    return (isinstance(raw, list) and 0 < len(raw) <= MAX_SERVICES and len(set(map(str, raw))) == len(raw)
            and all(isinstance(name, str) and SERVICE_RE.match(name) for name in raw))


def cmd_restart_services(stack: Stack, services: list[str]) -> list[str]:
    """`docker compose restart` nur für diese Dienste; ein neu gestarteter Container sieht auch eine ersetzte Einzeldatei neu."""
    if not services or not all(SERVICE_RE.match(name) for name in services):
        raise ValueError("ungültige Dienstnamen")
    return ["sh", "-c", IN_FOLDER, "sh", stack.working_dir, *compose_argv(stack, "restart", "--", *services)]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Refused(Exception):
    """Am Server wird nichts geschrieben; der Text ist für Menschen."""


# --- Speicher in Deck ----------------------------------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _hashes(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)}


class FileDrafts:
    """Dateisätze zwischen Vorschlag und Freigabe; der Inhalt steht so nie in der Aktion und nie im Protokoll."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def put(self, host_id: str, project: str, commit: str, files: dict[str, tuple[int, bytes]]) -> str:
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.prune()
        draft_id = secrets.token_hex(16)
        folder = self._root / draft_id
        folder.mkdir(mode=0o700)
        try:
            entries = {}
            for number, (path, (mode, data)) in enumerate(sorted(files.items())):
                _write_private(folder / f"{number}.bin", data)
                entries[path] = {"n": number, "mode": mode, "sha256": sha256(data)}
            meta = {"host_id": host_id, "project": project, "commit": commit, "files": entries, "created": time.time()}
            _write_private(folder / "meta.json", json.dumps(meta).encode())
        except OSError:
            self.drop(draft_id)
            raise
        return draft_id

    def get(self, draft_id: str) -> tuple[dict[str, Any], dict[str, tuple[int, bytes]]] | None:
        self.prune()
        if not _ID_RE.match(draft_id):
            return None
        folder = self._root / draft_id
        meta = _read_json(folder / "meta.json")
        if meta is None or not isinstance(meta.get("files"), dict):
            return None
        files: dict[str, tuple[int, bytes]] = {}
        try:
            for path, entry in meta["files"].items():
                data = (folder / f"{int(entry['n'])}.bin").read_bytes()
                if sha256(data) != entry.get("sha256") or entry.get("mode") not in (0o644, 0o755):
                    return None
                files[path] = (entry["mode"], data)
        except (OSError, KeyError, TypeError, ValueError):
            return None
        return meta, files

    def drop(self, draft_id: str) -> None:
        if _ID_RE.match(draft_id):
            shutil.rmtree(self._root / draft_id, ignore_errors=True)

    def _folders(self) -> list[Path]:
        try:
            return sorted((p for p in self._root.iterdir() if p.is_dir() and _ID_RE.match(p.name)), key=_age)
        except OSError:
            return []

    def prune(self) -> None:
        """Abgelaufene Entwürfe weg; ein abgelehnter oder nie freigegebener Vorschlag lässt seinen Inhalt sonst liegen."""
        folders = self._folders()
        cutoff = time.time() - DRAFT_TTL_S
        for index, folder in enumerate(folders):
            if _age(folder) < cutoff or len(folders) - index >= MAX_DRAFTS:
                self.drop(folder.name)

    def drop_for(self, host_id: str, project: str) -> None:
        for folder in self._folders():
            meta = _read_json(folder / "meta.json") or {}
            if (meta.get("host_id"), meta.get("project")) == (host_id, project):
                self.drop(folder.name)


def _age(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


@dataclass
class Previous:
    """Der Stand vor dem letzten Einspielen, für genau einen Schritt zurück."""

    id: str
    commit: str
    commit_before: str | None
    at: str
    written: dict[str, str]
    """Was das Einspielen geschrieben hat (Pfad → Prüfsumme); nur das wird zurückgenommen."""
    removed: list[str]
    restore: dict[str, dict[str, Any]]
    """Alte Fassungen der ersetzten oder entfernten Dateien: Pfad → Nummer, Rechte, Prüfsumme."""
    manifest_before: dict[str, str]
    at_before: str | None = None
    dirs: list[str] = field(default_factory=list)
    """Ordner, die das Einspielen angelegt hat; der Rückweg nimmt sie weg, wenn sie leer sind."""

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "commit": self.commit, "commit_before": self.commit_before, "at": self.at, "written": self.written,
                "removed": self.removed, "restore": self.restore, "manifest_before": self.manifest_before, "at_before": self.at_before,
                "dirs": self.dirs}


class AppliedState:
    """Je Stack, was Deck aus Git geschrieben hat, und der Stand davor; im Datenordner von Deck, nie auf dem Server."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _dir(self, host_id: str, project: str) -> Path:
        return self._root / _stack_key(host_id, project)

    def written(self, host_id: str, project: str) -> tuple[str | None, str | None, dict[str, str]]:
        """Commit, Zeitpunkt und Dateien (Pfad → Prüfsumme) des letzten Einspielens; leer, wenn es keines gab."""
        data = _read_json(self._dir(host_id, project) / "written.json") or {}
        commit = data.get("commit")
        return (commit if isinstance(commit, str) else None), (str(data.get("at") or "") or None), _hashes(data.get("files"))

    def put_written(self, host_id: str, project: str, commit: str | None, files: dict[str, str], at: str | None = None) -> None:
        folder = self._dir(host_id, project)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_private(folder / "written.json", json.dumps({"commit": commit, "at": at or _now(), "files": files}).encode())

    def previous(self, host_id: str, project: str) -> Previous | None:
        data = _read_json(self._dir(host_id, project) / "previous" / "meta.json")
        if data is None or not isinstance(data.get("id"), str) or not isinstance(data.get("restore"), dict):
            return None
        removed, dirs = data.get("removed"), data.get("dirs")
        return Previous(
            id=data["id"], commit=str(data.get("commit") or ""), commit_before=data.get("commit_before") if isinstance(data.get("commit_before"), str) else None,
            at=str(data.get("at") or ""), written=_hashes(data.get("written")),
            removed=[str(p) for p in removed] if isinstance(removed, list) else [],
            restore={str(k): v for k, v in data["restore"].items() if isinstance(v, dict)}, manifest_before=_hashes(data.get("manifest_before")),
            at_before=data.get("at_before") if isinstance(data.get("at_before"), str) else None,
            dirs=[str(p) for p in dirs] if isinstance(dirs, list) else [],
        )

    def previous_content(self, host_id: str, project: str, entry: dict[str, Any]) -> bytes | None:
        try:
            data = (self._dir(host_id, project) / "previous" / f"{int(entry['n'])}.bin").read_bytes()
        except (OSError, KeyError, TypeError, ValueError):
            return None
        return data if sha256(data) == entry.get("sha256") else None

    def stage_previous(self, host_id: str, project: str, previous: Previous, contents: dict[str, bytes]) -> Path:
        """Legt den Stand davor neben den alten; erst `keep_previous` ersetzt ihn, sonst bleibt der alte Schritt zurück."""
        folder = self._dir(host_id, project)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        for leftover in folder.glob("previous.*-*"):
            # Reste eines abgebrochenen Laufs; unter der Sperre des Stacks läuft sonst keiner.
            shutil.rmtree(leftover, ignore_errors=True)
        staged = folder / f"previous.neu-{secrets.token_hex(4)}"
        staged.mkdir(mode=0o700)
        try:
            for path, entry in previous.restore.items():
                _write_private(staged / f"{entry['n']}.bin", contents[path])
            _write_private(staged / "meta.json", json.dumps(previous.to_json()).encode())
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        return staged

    def keep_previous(self, host_id: str, project: str, staged: Path, previous: Previous) -> None:
        _write_private(staged / "meta.json", json.dumps(previous.to_json()).encode())
        target = self._dir(host_id, project) / "previous"
        old = target.with_name(f"previous.alt-{secrets.token_hex(4)}")
        with contextlib.suppress(FileNotFoundError):
            os.replace(target, old)
        os.replace(staged, target)
        shutil.rmtree(old, ignore_errors=True)

    def drop_previous(self, host_id: str, project: str) -> None:
        shutil.rmtree(self._dir(host_id, project) / "previous", ignore_errors=True)

    def forget(self, host_id: str, project: str) -> None:
        shutil.rmtree(self._dir(host_id, project), ignore_errors=True)


# --- Auf dem Server ------------------------------------------------------------------------------------------------------


@dataclass
class Target:
    """Wie eine Datei vor dem Schreiben auf dem Server liegt."""

    path: str
    sha256: str | None = None
    data: bytes | None = None
    attrs: Any = None


@dataclass
class Plan:
    commit: str
    files: dict[str, tuple[int, bytes]]
    """Der ganze neue Stand: Pfad auf dem Server → Rechte und Inhalt."""
    writes: list[str] = field(default_factory=list)
    removes: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    """Von Deck geschrieben, im neuen Stand nicht mehr da, aber auf dem Server geändert: bleibt stehen."""
    env_kept: list[str] = field(default_factory=list)
    """Umgebungsdateien aus dem Repository, die es auf dem Server schon gibt: bleiben, wie sie sind."""
    owner_changes: list[str] = field(default_factory=list)
    """Vorhandene Dateien eines anderen Besitzers, die danach dem Besitzer des Stack-Ordners gehören."""
    env_left: list[str] = field(default_factory=list)
    """Umgebungsdateien, die Deck geschrieben hat und die im Repository jetzt fehlen: bleiben auf dem Server."""
    mode_changes: list[tuple[str, int, int]] = field(default_factory=list)
    """Vorhandene Dateien, deren Rechte strenger werden: Pfad, alte und neue Rechte."""
    targets: dict[str, Target] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    io: Any = None
    """Womit Deck liest und schreibt: mit dem Zugang selbst oder mit den Rechten des Besitzers des Stack-Ordners."""
    owner_uid: int | None = None

    def bases(self) -> dict[str, str | None]:
        return {path: self.targets[path].sha256 for path in (*self.writes, *self.removes)}

    def counts(self) -> tuple[int, int, int]:
        new = sum(1 for p in self.writes if self.targets[p].sha256 is None)
        return new, len(self.writes) - new, len(self.removes)

    def manifest(self, written: dict[str, str], before: dict[str, str]) -> dict[str, str]:
        """Was danach als von Deck geschrieben gilt: das eben Geschriebene und zuvor Geschriebenes, das seitdem gleich blieb."""
        kept = {path: digest for path, digest in before.items()
                if path in self.files and path not in written and path in self.targets and self.targets[path].sha256 == digest}
        return {**kept, **written}


def _is(attrs: Any, check: Callable[[int], bool]) -> bool:
    return attrs.permissions is not None and check(attrs.permissions)


async def _lstat(sftp: Any, path: str) -> Any | None:
    try:
        return await sftp.lstat(path)
    except Exception as exc:
        if no_such_file(exc):
            return None
        raise


async def folder_owner(sftp: Any, folder: str) -> Any:
    """Die Attribute des Stack-Ordners; ein Verweis oder etwas anderes als ein Ordner wird abgelehnt."""
    attrs = await _lstat(sftp, folder)
    if attrs is None:
        raise Refused("Den Ordner des Stacks gibt es auf dem Server nicht mehr.")
    if _is(attrs, statmod.S_ISLNK):
        raise Refused("Der Ordner des Stacks ist ein Verweis auf einen anderen Ordner. Deck schreibt dort nichts aus Git hinein.")
    if not _is(attrs, statmod.S_ISDIR):
        raise Refused("Der Ordner des Stacks ist kein Ordner.")
    return attrs


async def parents_problem(sftp: Any, folder: str, path: str, owner_uid: int | None, seen: dict[str, str | None]) -> str | None:
    """Ein Grund, wenn zwischen Stack-Ordner und Datei ein Verweis, etwas anderes als ein Ordner oder ein Ordner eines anderen
    Besitzers liegt; fehlende Ordner legt Deck später selbst an."""
    rel = posixpath.relpath(posixpath.dirname(path), folder)
    if rel == "." or rel == ".." or rel.startswith("../"):
        return None if rel == "." else f"„{path}“ liegt nicht im Ordner des Stacks."
    current = folder
    for part in rel.split("/"):
        current = posixpath.join(current, part)
        if current not in seen:
            attrs = await _lstat(sftp, current)
            if attrs is None:
                seen[current] = None
                return None
            if _is(attrs, statmod.S_ISLNK):
                seen[current] = f"„{current}“ ist auf dem Server ein Verweis; Deck schreibt nicht hindurch."
            elif not _is(attrs, statmod.S_ISDIR):
                seen[current] = f"„{current}“ ist auf dem Server kein Ordner."
            elif owner_uid is not None and getattr(attrs, "uid", None) not in (None, owner_uid):
                seen[current] = f"„{current}“ gehört auf dem Server einem anderen Benutzer als der Ordner des Stacks; Deck schreibt dort nicht hinein."
            else:
                seen[current] = ""
        if seen[current] is None:
            return None
        if seen[current]:
            return seen[current]
    return None


class SftpFiles:
    """Lesen und Schreiben mit dem Zugang selbst; nur, wenn Deck sich als Besitzer des Stack-Ordners anmeldet."""

    def __init__(self, sftp: Any, attrs_type: Any) -> None:
        self._sftp = sftp
        self._attrs = attrs_type

    async def read(self, path: str, limit: int) -> bytes:
        async with self._sftp.open(path, "rb") as handle:
            return bytes(await handle.read(limit + 1))

    async def mkdir(self, path: str) -> None:
        await self._sftp.mkdir(path, self._attrs(permissions=_DIR_MODE))

    async def write(self, path: str, mode: int, data: bytes) -> None:
        """Daneben schreiben, nur für den Zugang lesbar, Rechte über den Griff, dann umbenennen; so liest Compose nie eine
        halbe Datei, und ein harter Verweis an der Stelle wird ersetzt statt beschrieben."""
        if mode not in ALLOWED_MODES:
            raise ValueError(f"Rechte {mode:o} sind nicht erlaubt")
        tmp = _temp_path(path, "git")
        handle = await self._sftp.open(tmp, "xb", self._attrs(permissions=_PRIVATE_MODE))
        try:
            async with handle:
                await handle.write(data)
                await handle.chmod(mode)
            await self._put(tmp, path)
        except BaseException:
            with contextlib.suppress(Exception):
                await self._sftp.remove(tmp)
            raise

    async def _put(self, tmp: str, path: str) -> None:
        """Eine neue Datei geht auch ohne `posix-rename`, eine vorhandene über den Weg des Editors."""
        if await _lstat(self._sftp, path) is not None:
            await _replace(self._sftp, tmp, path)
            return
        try:
            await self._sftp.posix_rename(tmp, path)
        except Exception as exc:  # nur „nicht unterstützt“ hat einen zweiten Weg
            if getattr(exc, "code", None) != _SFTP_OP_UNSUPPORTED:
                raise
            await self._sftp.rename(tmp, path)

    async def remove(self, path: str) -> None:
        await self._sftp.remove(path)

    async def rmdir(self, path: str) -> None:
        await self._sftp.rmdir(path)


# Feste Befehle; Pfade und Rechte kommen nur als eigene Wörter dazu, nie in den Text.
_READ = 'test -f "$1" && test ! -L "$1" || exit 3; test -r "$1" || exit 4; head -c "$2" "$1" | base64'
_WRITE = 'set -C; umask 077; test ! -d "$3" || exit 5; cat > "$1" && chmod "$2" "$1" && mv -f "$1" "$3"'
_NOT_A_FILE = 3


@dataclass(frozen=True)
class Owner:
    """Besitzer des Stack-Ordners; `sudo`, wenn Deck sich nicht als root anmeldet."""

    uid: int
    gid: int
    sudo: bool


def _as(owner: Owner, *argv: str) -> list[str]:
    if type(owner.uid) is not int or type(owner.gid) is not int or owner.uid < 0 or owner.gid < 0:
        raise ValueError("Besitzer ohne gültige Nummer")
    prefix = ["setpriv", f"--reuid={owner.uid}", f"--regid={owner.gid}", "--clear-groups", "--"]
    return ["sudo", "-n", *prefix, *argv] if owner.sudo else [*prefix, *argv]


def _absolute(path: str) -> str:
    if not path.startswith("/") or "\0" in path:
        raise ValueError(f"kein absoluter Pfad: {path!r}")
    return path


def cmd_login_uid() -> list[str]:
    return ["id", "-u"]


def cmd_owner_probe(owner: Owner) -> list[str]:
    return _as(owner, "true")


def cmd_owner_read(owner: Owner, path: str, limit: int) -> list[str]:
    return _as(owner, "sh", "-c", _READ, "sh", _absolute(path), str(int(limit) + 1))


def cmd_owner_write(owner: Owner, tmp: str, mode: int, path: str) -> list[str]:
    if mode not in ALLOWED_MODES:
        raise ValueError(f"Rechte {mode:o} sind nicht erlaubt")
    return _as(owner, "sh", "-c", _WRITE, "sh", _absolute(tmp), f"{mode:o}", _absolute(path))


def cmd_owner_mkdir(owner: Owner, path: str) -> list[str]:
    return _as(owner, "mkdir", "-m", f"{_DIR_MODE:o}", "--", _absolute(path))


def cmd_owner_remove(owner: Owner, path: str) -> list[str]:
    return _as(owner, "rm", "-f", "--", _absolute(path))


def cmd_owner_rmdir(owner: Owner, path: str) -> list[str]:
    return _as(owner, "rmdir", "--", _absolute(path))


class CommandFailed(OSError):
    """Ein fester Befehl endete mit einem Code ungleich 0; der Text ist die erste Zeile seiner Fehlerausgabe."""

    def __init__(self, exit_code: int, text: str) -> None:
        super().__init__(text)
        self.exit_code = exit_code


class OwnerFiles:
    """Lesen und Schreiben über feste Befehle mit den Rechten des Besitzers (`setpriv`, ohne weitere Gruppen): Tauscht er
    zwischen Prüfen und Schreiben einen Ordner gegen einen Verweis, landet Deck nur dort, wo er selbst schreiben darf."""

    def __init__(self, ctx: Any, host: Any, owner: Owner) -> None:
        self._ctx = ctx
        self._host = host
        self.owner = owner

    async def _call(self, host: Any, argv: list[str], stdin: bytes | None = None) -> str:
        result = await self._ctx.exec.run_argv(host, argv, stdin=stdin, timeout_s=STEP_TIMEOUT_S)
        if result.exit_code != 0:
            raise CommandFailed(result.exit_code, _first_line(result.stderr) or f"Befehl mit Code {result.exit_code} beendet")
        return result.stdout

    async def read(self, path: str, limit: int) -> bytes:
        try:
            out = await self._call(self._host, cmd_owner_read(self.owner, path, limit))
        except CommandFailed as exc:
            if exc.exit_code == _NOT_A_FILE:
                raise Refused(f"Auf dem Server ist „{path}“ ein Verweis oder keine normale Datei; Deck ersetzt das nicht.") from None
            raise Refused(f"„{path}“ ließ sich auf dem Server nicht lesen: {exc}") from None
        try:
            return base64.b64decode("".join(out.split()), validate=True)
        except (binascii.Error, ValueError):
            raise Refused(f"„{path}“ ließ sich auf dem Server nicht lesbar auslesen.") from None

    async def mkdir(self, path: str) -> None:
        await self._call(self._host, cmd_owner_mkdir(self.owner, path))

    async def write(self, path: str, mode: int, data: bytes) -> None:
        tmp = _temp_path(path, "git")
        try:
            await self._call(self._host, cmd_owner_write(self.owner, tmp, mode, path), stdin=data)
        except BaseException:
            with contextlib.suppress(Exception):
                await self._call(self._host, cmd_owner_remove(self.owner, tmp))
            raise

    async def remove(self, path: str) -> None:
        await self._call(self._host, cmd_owner_remove(self.owner, path))

    async def rmdir(self, path: str) -> None:
        await self._call(self._host, cmd_owner_rmdir(self.owner, path))


def _first_line(text: str | None) -> str:
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), "")[:200]


async def _login_uid(ctx: Any, host: Any) -> int:
    try:
        result = await ctx.exec.run_argv(host, cmd_login_uid(), timeout_s=STEP_TIMEOUT_S)
        return int(result.stdout.strip())
    except (ValueError, AttributeError):
        raise Refused("Deck konnte nicht feststellen, als welcher Benutzer es auf dem Server angemeldet ist.") from None


async def owner_files(ctx: Any, host: Any, sftp: Any, folder_attrs: Any) -> Any:
    """Schreibt Deck als jemand anderes als der Besitzer des Stack-Ordners, dann nur mit dessen Rechten; geht das nicht, ein Grund."""
    uid, gid = getattr(folder_attrs, "uid", None), getattr(folder_attrs, "gid", None)
    if uid is None or gid is None:
        raise Refused("Der Server nennt den Besitzer des Stack-Ordners nicht. Ohne ihn schreibt Deck dort nichts aus Git hinein.")
    me = await _login_uid(ctx, host)
    if me == uid:
        return SftpFiles(sftp, type(folder_attrs))
    owner = Owner(uid, gid, sudo=me != 0)
    probe = await ctx.exec.run_argv(host, cmd_owner_probe(owner), timeout_s=STEP_TIMEOUT_S)
    if probe.exit_code == 0:
        return OwnerFiles(ctx, host, owner)
    if me != 0:
        raise Refused(f"Der Ordner des Stacks gehört dem Benutzer mit der Nummer {uid}, Deck meldet sich aber als {me} an. Mit den Rechten "
                      "des Besitzers zu schreiben braucht root: Melde Deck als root oder als Besitzer an, oder erlaube ihm "
                      "„sudo -n setpriv“ (das kommt root-Rechten gleich).")
    raise Refused(f"Der Ordner des Stacks gehört dem Benutzer mit der Nummer {uid}. Deck schreibt dort nur mit dessen Rechten und braucht "
                  f"dafür setpriv (Paket util-linux) auf dem Server ({_first_line(probe.stderr) or probe.exit_code}).")


async def read_target(sftp: Any, io: Any, path: str, limit: int) -> Target:
    """Die Datei, wie sie gerade liegt; ein Verweis oder etwas anderes als eine normale Datei ist ein Grund."""
    attrs = await _lstat(sftp, path)
    if attrs is None:
        return Target(path)
    if not _is(attrs, statmod.S_ISREG):
        raise Refused(f"Auf dem Server ist „{path}“ ein Verweis oder keine normale Datei; Deck ersetzt das nicht.")
    if attrs.size is not None and attrs.size > limit:
        raise Refused(f"Auf dem Server ist „{path}“ größer als erwartet; Deck ersetzt die Datei nicht.")
    data = await io.read(path, limit)
    if len(data) > limit:
        raise Refused(f"Auf dem Server ist „{path}“ größer als erwartet; Deck ersetzt die Datei nicht.")
    if attrs.size is not None and len(data) != attrs.size:
        raise Refused(f"„{path}“ ließ sich auf dem Server nicht vollständig lesen; Deck ersetzt die Datei nicht.")
    return Target(path, sha256(data), data, attrs)


async def make_dirs(sftp: Any, io: Any, folder: str, path: str, made: list[str]) -> None:
    """Legt fehlende Ordner zwischen Stack-Ordner und Datei an; `made` sammelt sie für den Rückbau."""
    current = folder
    for part in posixpath.relpath(posixpath.dirname(path), folder).split("/"):
        if part == ".":
            continue
        current = posixpath.join(current, part)
        if await _lstat(sftp, current) is not None:
            continue
        await io.mkdir(current)
        made.append(current)


async def still_as_planned(sftp: Any, folder: str, plan: Plan, path: str) -> None:
    """Direkt vor dem Schreiben noch einmal: kein Verweis auf dem Weg, und die Datei liegt noch so da wie beim Planen."""
    if problem := await parents_problem(sftp, folder, path, plan.owner_uid, {}):
        raise Refused(problem)
    if (await read_target(sftp, plan.io, path, SERVER_FILE_LIMIT)).sha256 != plan.targets[path].sha256:
        raise Refused(f"„{path}“ hat sich auf dem Server gerade geändert.")


def _mode_of(target: Target) -> int:
    return statmod.S_IMODE(target.attrs.permissions) if target.attrs is not None and target.attrs.permissions is not None else 0o644


def _private_name(path: str) -> bool:
    name = posixpath.basename(path)
    return name == ENV_NAME or name.startswith(ENV_NAME + ".") or name.endswith(ENV_NAME)


def wanted_mode(repo_mode: int, target: Target, path: str, private: bool = False) -> int:
    """Nur 0644, 0755 oder 0600: Umgebungsdateien und Dateien, die bisher nur Besitzer und Gruppe lesen durften, bleiben privat."""
    if private or _private_name(path) or (target.attrs is not None and not _mode_of(target) & 0o004):
        return _PRIVATE_MODE
    return repo_mode


def foreign_owner(target: Target, owner_uid: int | None, changes: list[str]) -> str | None:
    """Eine vorhandene Datei eines anderen Besitzers gehört danach dem des Stack-Ordners; durfte nur ihr Besitzer sie lesen, ein Grund."""
    uid = getattr(target.attrs, "uid", None)
    if target.attrs is None or owner_uid is None or uid is None or uid == owner_uid:
        return None
    if not _mode_of(target) & 0o004:
        return (f"„{target.path}“ gehört auf dem Server dem Benutzer mit der Nummer {uid} und ist nur für ihn lesbar. Deck würde sie als "
                "Besitzer des Stack-Ordners ersetzen, und der Dienst könnte sie danach nicht mehr lesen. Passe Besitzer oder Rechte selbst an "
                "oder nimm die Datei aus dem Repository.")
    changes.append(target.path)
    return None


@dataclass
class Done:
    """Was beim Schreiben schon passiert ist, damit es sich zurücknehmen lässt."""

    written: dict[str, str] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)
    pending: tuple[str, str | None] | None = None
    """Der Schritt, der gerade läuft: Pfad und Prüfsumme danach, `None` beim Entfernen."""
    unclear: dict[str, str | None] = field(default_factory=dict)
    """Unterbrochene Schritte, bei denen auf dem Server weder der alte noch der neue Stand zu sehen war: nur der Rückweg fasst sie an."""


async def settle(sftp: Any, plan: Plan, done: Done) -> bool:
    """Der unterbrochene Schritt zählt nur als geschehen, wenn auf dem Server genau sein Ergebnis liegt; `False`, wenn es unklar bleibt."""
    if done.pending is None:
        return True
    (path, expected), done.pending = done.pending, None
    try:
        now = (await read_target(sftp, plan.io, path, SERVER_FILE_LIMIT)).sha256
    except Exception:  # offen lassen, nicht raten
        log.warning("docker_git_settle_unclear path=%s", path, exc_info=True)
        now = ""
    if now == expected:
        if expected is None:
            done.removed.append(path)
        else:
            done.written[path] = expected
    elif now != plan.targets[path].sha256:
        done.unclear[path] = expected
        return False
    return True


def _restore_mode(target: Target, path: str) -> int:
    """Die alte Fassung kommt mit 0600, 0644 oder 0755 zurück, nie mit Sonderrechten."""
    current = _mode_of(target)
    if _private_name(path) or not current & 0o004:
        return _PRIVATE_MODE
    return 0o755 if current & 0o100 else 0o644


async def undo(sftp: Any, folder: str, plan: Plan, done: Done) -> bool:
    """Stellt den Stand vor dem Schreiben wieder her, nie durch einen Verweis hindurch; `True`, wenn das ganz gelungen ist."""
    ok, io = await settle(sftp, plan, done), plan.io
    for path in [*done.written, *done.removed]:
        target = plan.targets[path]
        try:
            if await parents_problem(sftp, folder, path, plan.owner_uid, {}):
                ok = False
                continue
            if target.data is None:
                await io.remove(path)
            else:
                await io.write(path, _restore_mode(target, path), target.data)
        except Exception:
            log.warning("docker_git_undo_failed path=%s", path, exc_info=True)
            ok = False
    for made in reversed(done.dirs):
        with contextlib.suppress(Exception):
            await io.rmdir(made)
    return ok


def _long_volume(raw: Any, folder: str) -> Any:
    """Kurze Schreibweise `quelle:ziel[:ro]` wie Compose sie auflöst; relative Quellen liegen im Ordner des Stacks."""
    if isinstance(raw, dict):
        source = raw.get("source")
        if raw.get("type") == "bind" and isinstance(source, str) and source.startswith("."):
            return {**raw, "source": posixpath.normpath(posixpath.join(folder, source))}
        return raw
    if not isinstance(raw, str) or ":" not in raw:
        return raw
    source, _, rest = raw.partition(":")
    options = rest.split(":")[1].split(",") if ":" in rest else []
    if not source.startswith(("/", ".", "~")):
        return {"type": "volume", "source": source}
    if source.startswith("."):
        source = posixpath.normpath(posixpath.join(folder, source))
    return {"type": "bind", "source": source, "read_only": "ro" in options}


def _relative_file(entry: Any, folder: str) -> Any:
    if isinstance(entry, dict) and isinstance(entry.get("file"), str) and not entry["file"].startswith("/"):
        return {**entry, "file": posixpath.normpath(posixpath.join(folder, entry["file"]))}
    return entry


_LISTS = ("volumes", "cap_add", "security_opt", "devices", "configs", "secrets")


def _service(svc: dict[str, Any], folder: str) -> dict[str, Any]:
    # Wo Compose eine Liste verlangt, zählt nur eine Liste; alles andere lehnt Compose nach der Freigabe ohnehin ab.
    kept = {key: value for key, value in svc.items() if key not in _LISTS or isinstance(value, list)}
    return {**kept, "volumes": [_long_volume(v, folder) for v in kept.get("volumes", [])]}


def local_config(compose: bytes, folder: str) -> dict[str, Any] | None:
    """Die Datei selbst, Einbindungen in der langen Form mit absoluten Pfaden; für das, was Compose vorab nicht prüfen kann."""
    try:
        config = load_compose(compose)
    except Exception:  # noqa: BLE001 - unlesbar heißt hier nur: nichts vorab erkennbar
        return None
    if not isinstance(config, dict) or not isinstance(config.get("services"), dict):
        return None
    services = {str(name): _service(svc, folder) for name, svc in config["services"].items() if isinstance(svc, dict)}
    sections = {kind: {str(name): _relative_file(entry, folder) for name, entry in config[kind].items()}
                for kind in ("volumes", "configs", "secrets") if isinstance(config.get(kind), dict)}
    return {**config, **sections, "services": services}


def static_view(compose: bytes, folder: str, changed: list[str]) -> tuple[list[str], list[str]]:
    """Riskante Einstellungen und Dienste, die eine geänderte Datei einlesen, nur aus der Datei und ohne Compose; was sich so nicht
    lesen lässt, prüft Compose nach der Freigabe."""
    config = local_config(compose, folder)
    try:
        return ([] if config is None else risky_settings(config)), readers(config, changed)
    except Exception:  # eine ungewöhnliche Datei darf den Vorschlag nicht verhindern
        log.warning("docker_git_static_view_failed folder=%s", folder, exc_info=True)
        return [], []


def readers(config: dict[str, Any] | None, changed: list[str]) -> list[str]:
    """Dienste, die eine der geänderten Dateien einbinden; `up -d` startet sie nicht neu, wenn sich nur der Inhalt geändert hat."""
    if not isinstance(config, dict) or not isinstance(config.get("services"), dict):
        return []

    def hit(source: Any) -> bool:
        if not isinstance(source, str) or not source.startswith("/"):
            return False
        source = posixpath.normpath(source)
        inside = source.rstrip("/") + "/"
        return any(path == source or path.startswith(inside) for path in changed)

    files = {kind: {name: entry.get("file") for name, entry in config[kind].items() if isinstance(entry, dict)}
             for kind in ("configs", "secrets") if isinstance(config.get(kind), dict)}
    found = []
    for name, service in config["services"].items():
        if not isinstance(service, dict) or not SERVICE_RE.match(str(name)):
            continue
        sources = [v.get("source") for v in service.get("volumes") or [] if isinstance(v, dict) and v.get("type") == "bind"]
        for kind, by_name in files.items():
            refs = service.get(kind) if isinstance(service.get(kind), list) else []
            names = [ref.get("source") if isinstance(ref, dict) else ref for ref in refs]
            sources += [by_name.get(name) for name in names if isinstance(name, str)]
        if any(hit(source) for source in sources):
            found.append(str(name))
    return sorted(found)


# --- Ablauf --------------------------------------------------------------------------------------------------------------


class GitApply:
    """Plant, schlägt vor und führt aus; geschrieben wird nur im Ausführer und nur unter der Sperre des Stacks."""

    def __init__(self, ctx: Any, git: GitStacks, actions: DockerStackExecutor, data_dir: Path, update: StackUpdate,
                 saver: DockerStackUpdateExecutor) -> None:
        self.ctx = ctx
        self.git = git
        self.actions = actions
        self.update = update
        self.saver = saver
        self.state = AppliedState(data_dir / "git-apply")
        self.drafts = FileDrafts(data_dir / "git-apply" / "drafts")

    async def backup_choice(self, host: Any, stack: Stack) -> tuple[list[str], list[str]]:
        """Die benannten Volumes des Stacks und die laufenden Dienste, die sie nutzen und für die Sicherung stillstehen."""
        running = await self.update.running(host, stack)
        volumes = sorted({volume for entry in running.values() for volume in entry.volumes})
        return volumes, sorted(name for name, entry in running.items() if entry.running and entry.volumes)

    async def volume_plan(self, host: Any, stack: Stack) -> dict[str, Any]:
        """Wie beim Stack-Update Größe je Volume, freier Platz und Ziel, dazu die Dienste, die dafür stillstehen."""
        volumes, services = await self.backup_choice(host, stack)
        plan = await self.update.volumes.plan(host, stack.project, volumes)
        if not volumes:
            plan["error"] = NO_STACK_VOLUMES
        return {**plan, "services": services}

    async def forget(self, host_id: str, project: str) -> None:
        await asyncio.to_thread(self.state.forget, host_id, project)
        await asyncio.to_thread(self.drafts.drop_for, host_id, project)

    def new_state(self, stack: Stack, fetched: Fetched) -> dict[str, tuple[int, bytes]]:
        return {server_path(stack, fetched, rel): entry for rel, entry in fetched.files.items()}

    async def plan(self, host: Any, sftp: Any, stack: Stack, commit: str, files: dict[str, tuple[int, bytes]]) -> Plan:
        """Was ein Einspielen genau dieses Stands schreiben und entfernen würde, mit den Gründen dagegen."""
        plan = Plan(commit, files)
        folder_attrs = await folder_owner(sftp, stack.working_dir)
        plan.io = await owner_files(self.ctx, host, sftp, folder_attrs)
        plan.owner_uid = owner_uid = getattr(folder_attrs, "uid", None)
        seen: dict[str, str | None] = {}
        total = 0
        _, _, written = self.state.written(host.id, stack.project)
        for path in sorted({*files, *written}):
            if problem := await parents_problem(sftp, stack.working_dir, path, owner_uid, seen):
                plan.problems.append(problem)
                continue
            try:
                target = plan.targets[path] = await read_target(sftp, plan.io, path, SERVER_FILE_LIMIT)
            except Refused as exc:
                plan.problems.append(str(exc))
                continue
            total += len(target.data or b"")
            if total > SERVER_TOTAL_LIMIT:
                plan.problems.append(f"Die betroffenen Dateien auf dem Server sind zusammen größer als {SERVER_TOTAL_LIMIT // (1024 * 1024)} MB; "
                                     "Deck spielt so viel nicht auf einmal ein.")
                break
            if path in files:
                repo_mode, data = files[path]
                if env_file(stack, path) and target.sha256 is not None:
                    plan.env_kept.append(path)
                elif target.sha256 != sha256(data) or (target.attrs is not None and bool(repo_mode & 0o100) != bool(_mode_of(target) & 0o100)):
                    plan.writes.append(path)
                    if problem := foreign_owner(target, owner_uid, plan.owner_changes):
                        plan.problems.append(problem)
                    mode = wanted_mode(repo_mode, target, path, env_file(stack, path))
                    if target.attrs is not None and _mode_of(target) & ~mode & 0o7777:
                        plan.mode_changes.append((path, _mode_of(target), mode))
            elif target.sha256 is None:
                continue
            elif env_file(stack, path):
                plan.env_left.append(path)
            elif target.sha256 == written[path]:
                plan.removes.append(path)
            else:
                plan.kept.append(path)
        return plan

    async def check(self, host: Any, stack: Stack) -> Checked:
        """Compose prüft die Dateien des Stacks so, wie sie auf dem Server liegen."""
        try:
            result = await self.ctx.exec.run_argv(host, cmd_check_disk(stack), timeout_s=CHECK_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - ungeprüft heißt nicht übernommen
            if type(exc) is TimeoutError:
                return Checked(unverified=f"Compose hat nicht innerhalb von {CHECK_TIMEOUT_S} Sekunden geantwortet.")
            return Checked(unverified=f"Die Prüfung ist abgebrochen: {str(exc) or type(exc).__name__}")
        return parse_check(result.exit_code, result.stdout, result.stderr, posixpath.basename(stack.files[0]))

    @staticmethod
    def preview(stack: Stack, compose: bytes, changed: list[str]) -> tuple[list[str], list[str]]:
        """Riskante Einstellungen und Dienste, die neu starten, nur aus der Datei: Compose bekommt Inhalt aus dem Repository erst nach
        der Freigabe zu sehen, denn schon `config` kann Einbindungen aus dem Netz holen."""
        return static_view(compose, stack.working_dir, changed)

    async def propose(self, request: ActionRequest) -> Any:
        return await self.ctx.actions.propose(request, wait_s=REQUEST_WAIT_S)


def _fail(error: str, started: float | None = None) -> ActionResult:
    duration = None if started is None else int((time.monotonic() - started) * 1000)
    return ActionResult(success=False, error=error, duration_ms=duration)


def _describe(exc: BaseException) -> str:
    return str(exc)[:200] or type(exc).__name__


def _sentence(text: str) -> str:
    text = text.rstrip()
    return text if not text or text.endswith((".", "!", "?", "“")) else f"{text}."


class DockerStackGitApplyExecutor:
    """Spielt einen vorgeschlagenen Stand ein oder geht einen Schritt zurück, wenn der Server noch so aussieht wie beim Vorschlag."""

    action_types = frozenset({APPLY_ACTION, ROLLBACK_ACTION, RESTART_ACTION})

    def __init__(self, apply: GitApply) -> None:
        self._apply = apply
        self._actions = apply.actions

    async def execute(self, req: ActionRequest) -> ActionResult:
        payload = req.payload or {}
        if getattr(req.proposed_by.type, "value", req.proposed_by.type) != "user":
            return _fail(NOT_A_USER)
        found = await self._apply.actions.target(req.host_ref, str(payload.get("project") or ""))
        if isinstance(found, str):
            return _fail(found)
        host, stack = found
        if problem := layout_problem(stack):
            return _fail(problem)
        if req.action_type == ROLLBACK_ACTION:
            with self._apply.actions.locks.hold(host.id, stack.project, "Zurück zum Stand davor") as busy:
                if busy is not None:
                    return _fail(busy_text(busy))
                return await self._rollback(host, stack, str(payload.get("previous") or ""), list(payload.get("restarts") or []))
        if req.action_type == RESTART_ACTION:
            services = payload.get("services")
            if not valid_service_names(services):
                return _fail("Ungültige Dienstnamen.")
            with self._apply.actions.locks.hold(host.id, stack.project, "Neustart") as busy:
                if busy is not None:
                    return _fail(busy_text(busy))
                started = time.monotonic()
                containers = await self._containers_of(host, stack, services)
                if problem := await self._restart(host, stack, services):
                    return _fail(f"Der Neustart von {', '.join(services)} ist gescheitert: {problem}", started)
                return await self._restart_checked(host, services, containers, started)
        draft_id = str(payload.get("draft") or "")
        try:
            draft = self._apply.drafts.get(draft_id)
            if draft is None or not _draft_fits(draft[0], host.id, stack.project, payload):
                return _fail("Der Vorschlag ist abgelaufen oder passt nicht mehr. Bitte noch einmal einspielen.")
            source = self._apply.git.sources.get(host.id, stack.project)
            if source is None or {"url": source.url, "ref": source.ref, "path": source.path} != payload.get("source"):
                return _fail(CHANGED)
            with self._apply.actions.locks.hold(host.id, stack.project, "Einspielen") as busy:
                if busy is not None:
                    return _fail(busy_text(busy))
                return await self._run(host, stack, payload, draft[1])
        finally:
            # Ein Vorschlag läuft nur einmal; sonst bliebe der Inhalt bis zum nächsten Aufräumen im Datenordner liegen.
            self._apply.drafts.drop(draft_id)

    async def _run(self, host: Any, stack: Stack, payload: dict[str, Any], files: dict[str, tuple[int, bytes]]) -> ActionResult:
        started = time.monotonic()
        approved = list(payload.get("restarts") or [])
        if payload.get("backup") is None:
            put = await self._put(host, stack, payload, files)
            return _fail(put, started) if isinstance(put, str) else await self._up(host, stack, put[0], started, put[1], approved)
        chosen = _chosen(payload.get("backup"))
        if chosen is None:
            return _fail("Die Angaben zur Sicherung im Vorschlag sind ungültig. Eingespielt wurde nichts; bitte neu vorschlagen.", started)
        # Erst prüfen, dann stoppen: Passt der Vorschlag nicht mehr, soll dafür kein Dienst umsonst stillstehen.
        if (problem := await self._stale(host, stack, payload, files)) is not None:
            return _fail(problem, started)
        volumes, services = chosen
        try:
            if await self._apply.backup_choice(host, stack) != chosen:
                return _fail(BACKUP_CHANGED, started)
            folder, problem, not_started = await self._apply.saver.save_volumes(host, stack, services, volumes, started)
        except StackProblem as exc:
            return _fail(f"{_sentence(str(exc))} Eingespielt wurde nichts.", started)
        if problem is not None:
            after = ("die Dienste laufen weiter." if not_started is None else
                     f"die Dienste ließen sich aber nicht wieder starten ({not_started}). Starte den Stack im Reiter Stacks.")
            return _fail(f"{_sentence(problem)} Eingespielt wurde nichts; {after}", started)
        saved = f" Volumes gesichert in {folder}. {restore_hint()}"
        try:
            put = await self._put(host, stack, payload, files)
            if isinstance(put, str):
                return _fail(f"{_sentence(put)}{await self._start_again(host, stack, services)}{saved}", started)
            result = await self._up(host, stack, put[0] + saved, started, put[1], approved)
            # `up -d` startet keinen Dienst mit Profil und nach einem Fehler womöglich gar keinen.
            restart_problem, gone = await self._restart_stopped(host, stack, services)
        except BaseException:
            # Gestoppt für die Sicherung: Auch bei einem Abbruch, etwa an der Zeitgrenze der Freigabe, laufen sie wieder an.
            await asyncio.shield(self._restart_stopped(host, stack, services))
            raise
        return _with_restart(result, services, restart_problem, gone)

    async def _start_again(self, host: Any, stack: Stack, services: list[str]) -> str:
        """Startet die für die Sicherung gestoppten Dienste wieder; der Satz dazu für die Meldung."""
        if not services:
            return ""
        problem, gone = await self._restart_stopped(host, stack, services)
        if problem is not None:
            return f" {_sentence(problem)} Starte den Stack im Reiter Stacks.{_gone_note(gone)}"
        return f"{_gone_note(gone)}{_started_sentence(services, gone)}"

    async def _restart_stopped(self, host: Any, stack: Stack, services: list[str]) -> tuple[str | None, list[str]]:
        """Startet die gestoppten Dienste wieder: (Grund, falls das scheiterte; die Dienste, die nicht mehr in der Datei stehen)."""
        # Nur die, die noch in der Compose-Datei stehen; einen unbekannten Dienst lehnt Compose samt allen anderen ab.
        listed = await self._listed_services(host, stack) if services else None
        gone = [name for name in services if listed is not None and name not in listed]
        return await self._apply.saver.start_again(host, stack, [name for name in services if name not in gone]), gone

    async def _listed_services(self, host: Any, stack: Stack) -> set[str] | None:
        """Die Dienste der Datei, die jetzt auf dem Server liegt, oder `None`, wenn sie sich nicht lesen ließen."""
        try:
            listed = await self._apply.ctx.exec.run_argv(host, cmd_all_services(stack), timeout_s=SERVICES_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - dann versucht Deck, alle zu starten
            return None
        found = {line.strip() for line in listed.stdout.splitlines() if SERVICE_RE.match(line.strip())}
        return found if listed.exit_code == 0 and found else None

    async def _stale(self, host: Any, stack: Stack, payload: dict[str, Any], files: dict[str, tuple[int, bytes]]) -> str | None:
        """Warum der Server nicht mehr zum Vorschlag passt, oder `None`."""
        try:
            async with self._apply.ctx.exec.sftp(host) as sftp:
                plan = await self._apply.plan(host, sftp, stack, str(payload.get("commit") or ""), files)
        except Refused as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001 - als Grund melden
            return f"Die Dateien auf dem Server ließen sich nicht lesen: {_describe(exc)}"
        if plan.problems:
            return plan.problems[0]
        return None if _as_proposed(plan, payload) else STALE

    async def _put(self, host: Any, stack: Stack, payload: dict[str, Any], files: dict[str, tuple[int, bytes]]) -> str | tuple[str, list[str]]:
        """Schreibt den Stand und prüft ihn mit Compose: die Meldung und die Dienste, die dafür neu starten, oder der Grund dagegen."""
        apply, commit = self._apply, str(payload.get("commit") or "")
        commit_before, at_before, manifest_before = apply.state.written(host.id, stack.project)
        try:
            async with apply.ctx.exec.sftp(host) as sftp:
                plan = await apply.plan(host, sftp, stack, commit, files)
                if plan.problems:
                    return plan.problems[0]
                if not _as_proposed(plan, payload):
                    return STALE
                previous = Previous(
                    id=secrets.token_hex(16), commit=commit, commit_before=commit_before, at=_now(), written={}, removed=[],
                    restore={p: _restore_entry(n, plan.targets[p]) for n, p in enumerate(p for p in (*plan.writes, *plan.removes)
                                                                                           if plan.targets[p].data is not None)},
                    manifest_before=manifest_before, at_before=at_before,
                )
                try:
                    staged = await asyncio.to_thread(
                        apply.state.stage_previous, host.id, stack.project, previous, {p: plan.targets[p].data for p in previous.restore},
                    )
                except OSError:
                    return "Der Stand davor ließ sich nicht sichern, deshalb wurde nichts geändert."
                restarts = await self._write(sftp, host, stack, plan, previous, staged, list(payload.get("risky") or []))
                if isinstance(restarts, str):
                    return restarts
        except Refused as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001 - als Grund melden
            return f"Die Dateien ließen sich nicht schreiben: {_describe(exc)}"
        await self._forgotten_meanwhile(host, stack)
        new, changed, removed = plan.counts()
        done = f"Stand {commit[:7]} in Stack „{stack.project}“ eingespielt: {new} neu, {changed} geändert, {removed} entfernt."
        kept = apply.state.previous(host.id, stack.project)
        if kept is not None and kept.id == previous.id:
            done += " Der Stand davor bleibt für einen Schritt zurück."
        else:
            done += " Der Stand davor ließ sich in Deck nicht ablegen; einen Schritt zurück gibt es diesmal nicht."
        if plan.env_kept:
            done += " Die Umgebungsdateien auf dem Server blieben, wie sie waren."
        if plan.kept:
            done += f" {len(plan.kept)} Datei(en) fehlen im Repository, sind auf dem Server aber geändert und bleiben deshalb stehen."
        return done, restarts

    async def _write(self, sftp: Any, host: Any, stack: Stack, plan: Plan, previous: Previous, staged: Path, risky: list[str]) -> str | list[str]:
        """Schreibt, prüft mit Compose und merkt sich alles; die Dienste, die dafür neu starten, oder der Grund dagegen."""
        apply, done, io = self._apply, Done(), plan.io
        step = "Die Dateien ließen sich nicht schreiben"
        try:
            for path in plan.writes:
                await still_as_planned(sftp, stack.working_dir, plan, path)
                await make_dirs(sftp, io, stack.working_dir, path, done.dirs)
                target, (mode, data) = plan.targets[path], plan.files[path]
                done.pending = (path, sha256(data))
                await io.write(path, wanted_mode(mode, target, path, env_file(stack, path)), data)
                done.written[path], done.pending = sha256(data), None
            for path in plan.removes:
                await still_as_planned(sftp, stack.working_dir, plan, path)
                done.pending = (path, None)
                await io.remove(path)
                done.removed.append(path)
                done.pending = None
            step = "Die Prüfung mit Compose ließ sich nicht auswerten"
            checked = await apply.check(host, stack)
        except Exception as exc:  # noqa: BLE001 - erst zurücknehmen, dann melden
            return await self._back_out(sftp, host, stack, plan, previous, staged, done, f"{step}: {_describe(exc)}")
        except BaseException:
            # Abgebrochen, etwa weil Deck beendet wird: so weit wie möglich zurück, sonst bleibt wenigstens der Rückweg.
            with contextlib.suppress(Exception):
                await self._back_out(sftp, host, stack, plan, previous, staged, done, "")
            raise
        problem = checked.problem
        if problem is None and (unseen := [line for line in checked.warnings if line not in set(risky)]):
            problem = UNSEEN_RISK.format(lines=" ".join(unseen))
        if problem is not None:
            return await self._back_out(sftp, host, stack, plan, previous, staged, done, problem)
        self._keep(host, stack, staged, previous, done)
        try:
            apply.state.put_written(host.id, stack.project, plan.commit, plan.manifest(done.written, previous.manifest_before))
        except OSError:
            # Ohne Liste entfernt Deck beim nächsten Mal nur weniger, nie mehr.
            log.exception("docker_git_written_not_kept host=%s project=%s", host.id, stack.project)
        return readers(checked.config, [*done.written, *done.removed])

    async def _back_out(self, sftp: Any, host: Any, stack: Stack, plan: Plan, previous: Previous, staged: Path, done: Done, reason: str) -> str:
        if await undo(sftp, stack.working_dir, plan, done):
            shutil.rmtree(staged, ignore_errors=True)
            return f"{_sentence(reason)} Der alte Stand steht wieder auf dem Server."
        self._keep(host, stack, staged, previous, done)
        if done.unclear:
            return (f"{_sentence(reason)} Ob der letzte Schritt gewirkt hat, ließ sich nicht sicher feststellen; {_names(stack, list(done.unclear))} "
                    "hat Deck deshalb nicht angefasst. „Zurück zum Stand davor“ prüft das noch einmal und stellt zurück, was noch so daliegt "
                    "wie eingespielt.")
        return f"{_sentence(reason)} Der alte Stand ließ sich nicht ganz zurückschreiben; „Zurück zum Stand davor“ versucht es noch einmal."

    def _keep(self, host: Any, stack: Stack, staged: Path, previous: Previous, done: Done) -> None:
        # Unklare Schritte gehören mit in den Rückweg; der fasst eine Datei nur an, wenn auf dem Server genau das Eingespielte liegt.
        previous.written = {**done.written, **{path: sha for path, sha in done.unclear.items() if sha is not None}}
        previous.removed = [*done.removed, *(path for path, sha in done.unclear.items() if sha is None)]
        previous.dirs = list(done.dirs)
        try:
            self._apply.state.keep_previous(host.id, stack.project, staged, previous)
        except OSError:
            log.exception("docker_git_previous_not_kept host=%s project=%s", host.id, stack.project)

    async def _forgotten_meanwhile(self, host: Any, stack: Stack) -> None:
        """Wurde das Repository während des Schreibens vergessen, behält Deck auch das Eingespielte nicht."""
        if self._apply.git.sources.get(host.id, stack.project) is None:
            await self._apply.forget(host.id, stack.project)

    async def _restart(self, host: Any, stack: Stack, services: list[str]) -> str | None:
        """Startet die Dienste neu, die geänderte Dateien einlesen; `None`, wenn das geklappt hat, sonst der Grund."""
        try:
            result = await self._apply.ctx.exec.run_argv(host, cmd_restart_services(stack, services), timeout_s=RESTART_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - als Grund melden, angewendet wird trotzdem
            return _describe(exc)
        if result.exit_code != 0:
            return _first_line(scrub_compose_output(result.stderr or "")) or f"Code {result.exit_code}"
        return None

    async def _containers_of(self, host: Any, stack: Stack, services: list[str]) -> list[str] | None:
        """Die Container dieser Dienste, `None`, wenn sie sich nicht lesen ließen. Alle zählen, denn `compose restart` startet
        auch gestoppte."""
        try:
            found = await self._apply.update.running(host, stack)
        except (OSError, StackProblem):
            return None
        return [name for service in services if service in found for name in found[service].containers]

    async def _restart_checked(self, host: Any, services: list[str], containers: list[str] | None, started: float) -> ActionResult:
        """Nach dem Neustart: Laufen die Container dieser Dienste und bleiben sie laufen?"""
        from . import after_check as ac  # spät: after_check braucht capabilities

        done = f"Neu gestartet: {', '.join(services)}."
        if not containers:
            skipped = RESTART_UNCHECKED if containers is not None else RESTART_UNREADABLE
            return ActionResult(success=True, exit_code=0, output=f"{done}\n{skipped}",
                                duration_ms=int((time.monotonic() - started) * 1000))
        verdict, _ = await ac.check_running(self._apply.ctx, host, containers)
        line = ac.result_line(verdict)
        detail = {"check": {"ok": verdict.ok, "text": verdict.text}}
        duration_ms = int((time.monotonic() - started) * 1000)
        if verdict.ok:
            return ActionResult(success=True, exit_code=0, output=f"{done}\n{line}", duration_ms=duration_ms, detail=detail)
        return ActionResult(success=False, exit_code=0, output=done, error=line, duration_ms=duration_ms, detail=detail)

    async def _up(self, host: Any, stack: Stack, done: str, started: float, readers_now: list[str], approved: list[str]) -> ActionResult:
        """Die freigegebenen Dienste mit geänderten Dateien neu starten, dann `up -d` samt Prüfung, ob alles läuft; wer nur auf dem
        Server als betroffen erscheint, wird genannt, aber nicht neu gestartet."""
        restarts = [name for name in readers_now if name in approved]
        stale = [name for name in readers_now if name not in approved]
        restart_problem = await self._restart(host, stack, restarts) if restarts else None
        if restarts and restart_problem is None:
            done += f" Neu gestartet, weil sie geänderte Dateien einlesen: {', '.join(restarts)}."
        if stale:
            done += " " + STALE_SERVICES.format(names=", ".join(stale))
        duration_ms = int((time.monotonic() - started) * 1000)
        try:
            applied = await self._actions.run(host, stack, "docker.stack_up")
        except Exception as exc:  # noqa: BLE001 - die Dateien stehen schon auf dem Server, das muss die Oberfläche erfahren
            return ActionResult(success=False, output=done, duration_ms=duration_ms, detail={"written": True, **({"stale": stale} if stale else {})},
                                error=f"{APPLY_FAILED}: {_describe(exc)}")
        output = "\n".join(part for part in (done, applied.output) if part)
        detail = {**(applied.detail or {}), "written": True, **({"stale": stale} if stale else {})}
        total = duration_ms + (applied.duration_ms or 0)
        if not applied.success:
            return ActionResult(success=False, exit_code=applied.exit_code, output=output, duration_ms=total, detail=detail,
                                error=f"{APPLY_FAILED}: {applied.error}")
        if restart_problem is not None:
            return ActionResult(success=False, exit_code=applied.exit_code, output=output, duration_ms=total, detail=detail,
                                error=f"Geschrieben und angewendet, aber der Neustart von {', '.join(restarts)} ist gescheitert: "
                                f"{restart_problem}. Diese Dienste lesen vielleicht noch die alten Dateien.")
        return ActionResult(success=True, exit_code=0, output=output, duration_ms=total, detail=detail)

    async def _rollback(self, host: Any, stack: Stack, previous_id: str, approved: list[str]) -> ActionResult:
        started = time.monotonic()
        apply = self._apply
        previous = apply.state.previous(host.id, stack.project)
        if previous is None or previous.id != previous_id:
            return _fail("Der Stand davor hat sich seit dem Vorschlag geändert oder ist weg. Bitte neu auslösen.", started)
        contents: dict[str, bytes] = {}
        for path, entry in previous.restore.items():
            data = apply.state.previous_content(host.id, stack.project, entry)
            if data is None:
                return _fail("Die gesicherte Fassung ist beschädigt; zurückgestellt wurde nichts.", started)
            contents[path] = data
        skipped: list[str] = []
        unsafe: list[str] = []
        changed: list[str] = []

        def failed(reason: str) -> ActionResult:
            if changed:
                reason += f" {len(changed)} Datei(en) stehen schon wieder auf dem alten Stand; ein zweiter Versuch macht beim Rest weiter."
            return _fail(reason, started)

        try:
            async with apply.ctx.exec.sftp(host) as sftp:
                folder_attrs = await folder_owner(sftp, stack.working_dir)
                io = await owner_files(apply.ctx, host, sftp, folder_attrs)
                owner_uid = getattr(folder_attrs, "uid", None)
                for path in sorted({*previous.written, *previous.removed}):
                    if await parents_problem(sftp, stack.working_dir, path, owner_uid, {}):
                        unsafe.append(path)
                        continue
                    now = await read_target(sftp, io, path, SERVER_FILE_LIMIT)
                    entry = previous.restore.get(path)
                    old_mode = entry["mode"] if entry is not None and entry.get("mode") in ALLOWED_MODES else _PRIVATE_MODE
                    # Auch nur geänderte Rechte zählen: Hat das Einspielen bloß 0755 gesetzt, kommt die alte Fassung mit ihren Rechten zurück.
                    if now.sha256 == (entry or {}).get("sha256") and (entry is None or _mode_of(now) == old_mode):
                        continue
                    if now.sha256 != previous.written.get(path):
                        skipped.append(path)
                        continue
                    if entry is None:
                        await io.remove(path)
                    else:
                        await make_dirs(sftp, io, stack.working_dir, path, [])
                        await io.write(path, old_mode, contents[path])
                    changed.append(path)
                for folder in reversed(previous.dirs):
                    # Nur leere Ordner gehen weg; was jemand hineingelegt hat, bleibt mit seinem Ordner stehen.
                    with contextlib.suppress(Exception):
                        await io.rmdir(folder)
        except Refused as exc:
            return failed(str(exc))
        except Exception as exc:  # noqa: BLE001 - als Grund melden
            return failed(f"Die Dateien ließen sich nicht zurückschreiben: {_describe(exc)}")
        try:
            apply.state.put_written(host.id, stack.project, previous.commit_before, previous.manifest_before, previous.at_before)
            apply.state.drop_previous(host.id, stack.project)
        except OSError:
            log.exception("docker_git_rollback_not_kept host=%s project=%s", host.id, stack.project)
        await self._forgotten_meanwhile(host, stack)
        checked = await apply.check(host, stack)
        back = [path for path in sorted({*previous.written, *previous.removed}) if path not in skipped and path not in unsafe]
        restarts = readers(checked.config, back) if checked.problem is None else []
        done = f"Stack „{stack.project}“ steht wieder auf dem Stand vor {previous.commit[:7]}."
        if unsafe:
            done += (f" {len(unsafe)} Datei(en) liegen hinter einem Verweis oder in einem Ordner eines anderen Benutzers; Deck hat sie nicht "
                     "angefasst.")
        if skipped:
            done += f" {len(skipped)} Datei(en) wurden seit dem Einspielen geändert und bleiben, wie sie sind."
        return await self._up(host, stack, done, started, restarts, approved)

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        payload = req.payload or {}
        project = payload.get("project", "?")
        if req.action_type == ROLLBACK_ACTION:
            return DryRunReport(would_change=True, summary=f"Würde in Stack „{project}“ den Stand vor dem letzten Einspielen zurückholen "
                                f"und den Stack anwenden.")
        if req.action_type == RESTART_ACTION:
            return DryRunReport(would_change=True, summary=f"Würde in Stack „{project}“ neu starten: {', '.join(payload.get('services') or [])}.")
        writes, removes = payload.get("writes") or [], payload.get("removes") or []
        chosen = _chosen(payload.get("backup"))
        first = f"Würde vorher die Volumes {_listed(chosen[0])} sichern, dann " if chosen else "Würde "
        return DryRunReport(would_change=True, summary=f"{first}{len(writes)} Datei(en) schreiben und {len(removes)} entfernen "
                            f"(Stand {str(payload.get('commit') or '?')[:7]}) und Stack „{project}“ anwenden.")


def _with_restart(result: ActionResult, services: list[str], problem: str | None, gone: list[str]) -> ActionResult:
    """Das Ergebnis samt dem Wiederstarten der Dienste, die für die Sicherung gestoppt waren."""
    if not services:
        return result
    note = _gone_note(gone)
    if problem is None:
        if result.success:
            return result.model_copy(update={"output": f"{result.output or ''}{note}"}) if note else result
        return result.model_copy(update={"error": f"{_sentence(result.error or '')}{note}{_started_sentence(services, gone)}"})
    tried = [name for name in services if name not in gone]
    hint = f"{_sentence(problem)} Für die Sicherung gestoppt waren: {_listed(tried)}; starte den Stack im Reiter Stacks.{note}"
    if result.success:
        return result.model_copy(update={"success": False, "error": f"Geschrieben und angewendet. {hint}"})
    return result.model_copy(update={"error": f"{_sentence(result.error or '')} {hint}"})


def _started_sentence(services: list[str], gone: list[str]) -> str:
    if not gone:
        return " Die Dienste sind wieder gestartet."
    return " Die übrigen Dienste sind wieder gestartet." if len(gone) < len(services) else ""


def _gone_note(gone: list[str]) -> str:
    """Dienste, die für die Sicherung gestoppt waren und nicht mehr in der Compose-Datei stehen."""
    if not gone:
        return ""
    if len(gone) == 1:
        return (f" Der Dienst {_listed(gone)} steht nicht mehr in der Compose-Datei; sein alter Container bleibt gestoppt, entfernen kannst "
                "du ihn im Reiter Container.")
    return (f" Die Dienste {_listed(gone)} stehen nicht mehr in der Compose-Datei; ihre alten Container bleiben gestoppt, entfernen "
            "kannst du sie im Reiter Container.")


def _restore_entry(number: int, target: Target) -> dict[str, Any]:
    return {"n": number, "sha256": target.sha256, "mode": _restore_mode(target, target.path)}


def _as_proposed(plan: Plan, payload: dict[str, Any]) -> bool:
    return (plan.writes, plan.removes, plan.bases(), plan.env_kept) == (
        payload.get("writes"), payload.get("removes"), payload.get("bases"), payload.get("env_kept"))


def _chosen(raw: Any) -> tuple[list[str], list[str]] | None:
    """Volumes und Dienste der Sicherung aus dem Vorschlag, nur in gültiger Form."""
    if not isinstance(raw, dict):
        return None
    volumes, services = raw.get("volumes"), raw.get("services")
    if not isinstance(volumes, list) or not volumes or not all(isinstance(v, str) and VOLUME_NAME_RE.match(v) for v in volumes):
        return None
    if not isinstance(services, list) or not all(isinstance(name, str) and SERVICE_RE.match(name) for name in services):
        return None
    return volumes, services


def _draft_fits(meta: dict[str, Any], host_id: str, project: str, payload: dict[str, Any]) -> bool:
    files = meta.get("files") if isinstance(meta.get("files"), dict) else {}
    hashes = {path: entry.get("sha256") for path, entry in files.items()}
    return (meta.get("host_id"), meta.get("project"), meta.get("commit")) == (host_id, project, payload.get("commit")) and hashes == payload.get("files")


# --- Routen --------------------------------------------------------------------------------------------------------------


class _ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    backup: StrictBool = False


class _RollbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previous: str = Field(pattern=r"^[0-9a-f]{32}$")


class _RestartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    services: list[str] = Field(min_length=1, max_length=MAX_SERVICES)


def _short(name: str) -> str:
    return name if len(name) <= NAME_CHARS else "…" + name[-(NAME_CHARS - 1):]


def _names(stack: Stack, paths: list[str]) -> str:
    return _listed([posixpath.relpath(path, stack.working_dir) for path in paths])


def _cut(text: str, room: int) -> str:
    return text if len(text) <= room else text[: max(room - 1, 0)].rstrip() + "…"


def _fit(head: list[str], parts: list[str], limit: int, first: str = NO_VOLUME_BACKUP) -> str:
    """Der Freigabetext in `limit` Zeichen: Anfang und der Satz zu den Volumes bleiben immer, danach folgen die Teile in ihrer
    Reihenfolge, bis kein Platz mehr ist."""
    must = f"Hinweise: {first}"
    text = f"{_cut(' '.join(head), limit - len(must) - 1)} {must}"
    for part in parts:
        room = limit - len(text) - 1
        if len(part) > room:
            return f"{text} {_cut(part, room)}" if room > 20 else text
        text = f"{text} {part}"
    return text


def _listed(names: list[str]) -> str:
    shown = [_short(name) for name in names[:NAMES_SHOWN]]
    more = len(names) - len(shown)
    return ", ".join(shown) + (f" und {more} weitere" if more > 0 else "")


def backup_sentence(volumes: list[str], services: list[str], sizes: dict[str, int | None]) -> str:
    """Was vor dem Einspielen gesichert wird und welche Dienste dafür stillstehen."""
    text = f"Vorher sichert Deck die Volumes {_listed(volumes)} (bis zu {_gb(sum(size or 0 for size in sizes.values()))})"
    return f"{text}; dafür stehen {_listed(services)} still, bis Compose den Stack anwendet." if services else f"{text}."


def apply_reason(source: Any, stack: Stack, commit: str, plan: Plan, risky: list[str], restarts: list[str], notes: list[str],
                 backup: str = NO_VOLUME_BACKUP) -> str:
    """Der Text der Freigabe: riskante Einstellungen zuerst, dann was geschieht, die Warnungen aus dem Repository, die Hinweise und
    zuletzt die Namen der Dateien."""
    new, changed, removed = plan.counts()
    head = [f"Riskant: {' '.join(risky)}"] if risky else []
    head.append(f"Stand {commit[:7]} aus {source.url} ({source.ref}) in Stack „{stack.project}“ einspielen: {new} neu, {changed} geändert, "
                f"{removed} entfernt; danach wendet Compose den Stack an.")
    head.append(f"Neu starten, weil sie geänderte Dateien einlesen: {', '.join(restarts)}." if restarts else
                "Kein Dienst liest eine geänderte Datei ein; eigens neu gestartet wird keiner.")
    hints = []
    if plan.env_kept:
        hints.append(f"Diese Umgebungsdateien bleiben auf dem Server, wie sie sind; die aus dem Repository werden nicht übernommen: "
                     f"{_names(stack, plan.env_kept)}.")
    if plan.env_left:
        hints.append(f"Umgebungsdateien fehlen jetzt im Repository und bleiben auf dem Server: {_names(stack, plan.env_left)}.")
    if plan.owner_changes:
        hints.append(f"Gehört danach dem Besitzer des Stack-Ordners statt einem anderen Benutzer: {_names(stack, plan.owner_changes)}.")
    if plan.mode_changes:
        shown = ", ".join(f"{_short(posixpath.relpath(path, stack.working_dir))} {old:o} → {new_mode:o}"
                          for path, old, new_mode in plan.mode_changes[:NAMES_SHOWN])
        hints.append(f"Rechte werden strenger, erlaubt sind nur 0600, 0644 und 0755: {shown}.")
    if plan.kept:
        hints.append(f"{len(plan.kept)} Datei(en) fehlen im Repository, sind auf dem Server aber geändert und bleiben stehen.")
    added = [path for path in plan.writes if plan.targets[path].sha256 is None]
    replaced = [path for path in plan.writes if plan.targets[path].sha256 is not None]
    lists = [f"{label}: {_names(stack, paths)}." for label, paths in (("Neu", added), ("Geändert", replaced), ("Entfernt", plan.removes)) if paths]
    return _fit(head, [*notes, *hints, *lists], REASON_LIMIT, backup)


def apply_routes(apply: GitApply, docker_host: Callable[[str], Awaitable[Any]], current_actor: Any) -> APIRouter:
    router = APIRouter()

    async def _stack(host_id: str, project: str) -> tuple[Any, Stack]:
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        host = await docker_host(host_id)
        apply.git.adopted_stack(host.id, project)
        found = await apply.actions.target(host.id, project)
        if isinstance(found, str):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=found)
        if problem := layout_problem(found[1]):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=problem)
        return found

    @router.get("/hosts/{host_id}/stacks/{project}/git/applied")
    async def applied(host_id: str, project: str) -> dict:
        """Was zuletzt aus Git eingespielt wurde und ob es einen Schritt zurück gibt; nur aus dem Speicher von Deck."""
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        host = await docker_host(host_id)
        await asyncio.to_thread(apply.drafts.prune)
        commit, at, files = apply.state.written(host.id, project)
        previous = apply.state.previous(host.id, project)
        return {
            "applied": {"commit": commit, "at": at, "files": len(files)} if commit else None,
            "previous": {"id": previous.id, "commit": previous.commit, "commit_before": previous.commit_before, "at": previous.at}
            if previous else None,
        }

    @router.get("/hosts/{host_id}/stacks/{project}/git/volume-plan")
    async def volume_plan(host_id: str, project: str) -> dict:
        """Was eine Sicherung vor dem Einspielen umfasst: Volumes mit Größe, freier Platz, Ziel und die Dienste, die dafür stillstehen."""
        host, stack = await _stack(host_id, project)
        try:
            return await apply.volume_plan(host, stack)
        except StackProblem as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None

    @router.post("/hosts/{host_id}/stacks/{project}/git/apply", status_code=status.HTTP_202_ACCEPTED)
    async def apply_commit(host_id: str, project: str, body: _ApplyIn, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt das Einspielen genau des angesehenen Stands vor (Gate-Aktion `docker.stack_git_apply`)."""
        host, stack = await _stack(host_id, project)
        source = apply.git.sources.get(host.id, project)
        if source is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_SOURCE)
        try:
            fetched = await apply.git.fetch(host.id, project, source)
        except GitProblem as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None
        if fetched.commit != body.commit:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Im Repository gibt es inzwischen einen anderen Stand. Hol ihn dir zuerst in die Vorschau.")
        if problems := content_problems(stack, fetched):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=problems[0])
        files = apply.new_state(stack, fetched)
        try:
            async with apply.ctx.exec.sftp(host) as sftp:
                plan = await apply.plan(host, sftp, stack, fetched.commit, files)
        except Refused as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
        except Exception as exc:  # als Grund zeigen, nicht als 500
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                                detail=f"Die Dateien auf dem Server ließen sich nicht lesen: {_describe(exc)}") from exc
        if plan.problems:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.problems[0])
        if not plan.writes and not plan.removes:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Auf dem Server steht schon genau dieser Stand.")
        risky, restarts = apply.preview(stack, files[stack.files[0]][1], [*plan.writes, *plan.removes])
        backup, sentence = None, NO_VOLUME_BACKUP
        if body.backup:
            try:
                planned = await apply.volume_plan(host, stack)
            except StackProblem as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None
            if planned["error"]:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Sicherung der Volumes nicht möglich: {planned['error']}")
            backup = {"volumes": sorted(planned["volumes"]), "services": planned["services"]}
            sentence = backup_sentence(backup["volumes"], backup["services"], planned["volumes"])
        try:
            draft = await asyncio.to_thread(apply.drafts.put, host.id, project, fetched.commit, files)
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Der Vorschlag ließ sich nicht ablegen ({exc.strerror or type(exc).__name__}). Ist der Datenordner von Nodvard Deck "
                "voll oder schreibgeschützt?",
            ) from exc
        decision = await apply.propose(ActionRequest(
            action_type=APPLY_ACTION, host_ref=host.id, risk=RISK, proposed_by=actor,
            reason=apply_reason(source, stack, fetched.commit, plan, risky, restarts, list(fetched.warnings), sentence),
            payload={
                "project": project, "commit": fetched.commit, "draft": draft,
                "source": {"url": source.url, "ref": source.ref, "path": source.path},
                "files": {path: sha256(data) for path, (_, data) in files.items()},
                "writes": plan.writes, "removes": plan.removes, "bases": plan.bases(), "kept": plan.kept, "env_kept": plan.env_kept,
                "risky": risky, "restarts": restarts, "backup": backup,
            },
        ))
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": RISK.value, "detail": decision.detail}

    @router.post("/hosts/{host_id}/stacks/{project}/git/rollback", status_code=status.HTTP_202_ACCEPTED)
    async def rollback(host_id: str, project: str, body: _RollbackIn, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt vor, den Stand vor dem letzten Einspielen zurückzuholen (Gate-Aktion `docker.stack_git_rollback`)."""
        host, stack = await _stack(host_id, project)
        previous = apply.state.previous(host.id, project)
        if previous is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_PREVIOUS)
        if previous.id != body.previous:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Der Stand davor hat sich inzwischen geändert. Lade die Ansicht neu.")
        back_to = previous.commit_before[:7] if previous.commit_before else "vor dem ersten Einspielen"
        compose = await _compose_after(host, stack, previous)
        restarts = static_view(compose, stack.working_dir, sorted({*previous.written, *previous.removed}))[1]
        named = (f" Neu starten, weil sie zurückgestellte Dateien einlesen: {', '.join(restarts)}." if restarts else
                 " Welche Dienste zurückgestellte Dateien einlesen, ließ sich vorab nicht lesen; das Ergebnis nennt sie." if not compose else
                 " Kein Dienst liest eine zurückgestellte Datei ein; eigens neu gestartet wird keiner.")
        decision = await apply.propose(ActionRequest(
            action_type=ROLLBACK_ACTION, host_ref=host.id, risk=RISK, proposed_by=actor,
            reason=f"Stack „{stack.project}“ vom Stand {previous.commit[:7]} zurück auf {back_to}; danach wendet Compose den Stack an."
            f"{named} {NO_VOLUME_BACKUP}",
            payload={"project": project, "previous": previous.id, "commit": previous.commit, "restarts": restarts},
        ))
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": RISK.value, "detail": decision.detail}

    async def _compose_after(host: Any, stack: Stack, previous: Previous) -> bytes:
        """Die Compose-Datei, wie sie nach dem Rückweg aussähe: die gesicherte Fassung oder die auf dem Server; sonst leer."""
        entry = previous.restore.get(stack.files[0])
        if entry is not None:
            return apply.state.previous_content(host.id, stack.project, entry) or b""
        try:
            async with apply.ctx.exec.sftp(host) as sftp:
                attrs = await _lstat(sftp, stack.files[0])
                if attrs is None or not _is(attrs, statmod.S_ISREG) or (attrs.size or 0) > SERVER_FILE_LIMIT:
                    return b""
                async with sftp.open(stack.files[0], "rb") as handle:
                    return bytes(await handle.read(SERVER_FILE_LIMIT))
        except Exception:  # noqa: BLE001 - ohne lesbare Datei nennt die Freigabe nur keine Dienste
            return b""

    @router.post("/hosts/{host_id}/stacks/{project}/git/restart", status_code=status.HTTP_202_ACCEPTED)
    async def restart(host_id: str, project: str, body: _RestartIn, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt vor, Dienste neu zu starten, die nach dem Einspielen vielleicht noch den alten Stand lesen (`docker.stack_git_restart`)."""
        host, stack = await _stack(host_id, project)
        if not valid_service_names(body.services):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültige Dienstnamen.")
        decision = await apply.propose(ActionRequest(
            action_type=RESTART_ACTION, host_ref=host.id, risk=Risk.MEDIUM, proposed_by=actor,
            reason=f"In Stack „{stack.project}“ neu starten, damit sie den neuen Stand der Dateien lesen: {', '.join(body.services)}.",
            payload={"project": project, "services": body.services},
        ))
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": Risk.MEDIUM.value, "detail": decision.detail}

    return router
