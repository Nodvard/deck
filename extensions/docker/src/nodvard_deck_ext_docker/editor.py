"""Compose-Dateien übernommener Stacks bearbeiten: prüfen, ohne etwas zu speichern, und speichern nur über das Gate, mit
der alten Fassung im Verlauf."""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import hashlib
import json
import os
import posixpath
import re
import secrets
import stat as statmod
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk import REQUEST_WAIT_S
from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Actor, Risk
from pydantic import BaseModel, ConfigDict, Field

from .applier import RISK_ORDER
from .image_apply import PROJECT_RE
from .stack_actions import IN_FOLDER, NO_FOLDER_EXIT, DockerStackExecutor, busy_text
from .stacks import (
    FILE_SUFFIXES,
    MAX_FILE_BYTES,
    Stack,
    compose_argv,
    first_reason,
    no_such_file,
)

SAVE_ACTION = "docker.stack_save"
CHECK_TIMEOUT_S = 60
HISTORY_KEEP = 20
DRAFT_TTL_S = 24 * 3600
MAX_DRAFTS = 50
_ID_RE = re.compile(r"^[0-9a-f]{32}\Z")
_ENTRY_RE = re.compile(r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}\Z")
_SFTP_OP_UNSUPPORTED = 8
_RISKY_CAPS = frozenset({"ALL", "SYS_ADMIN", "SYS_MODULE", "SYS_RAWIO", "SYS_PTRACE", "SYS_BOOT", "DAC_READ_SEARCH", "NET_ADMIN", "BPF"})
_UNCONFINED = frozenset({"seccomp:unconfined", "apparmor:unconfined", "label:disable", "systempaths:unconfined"})
_SENSITIVE_DIRS = (
    "/etc", "/root", "/boot", "/dev", "/proc", "/sys", "/run", "/var/run", "/var/lib/docker", "/var/lib/containers", "/usr", "/bin",
    "/sbin", "/lib", "/lib64",
)
_HARMLESS = ("/dev/dri", "/dev/net/tun", "/etc/localtime", "/etc/timezone")
"""Üblich und ohne Zugriff auf Dateien oder Prozesse des Servers: Grafikkarte (Jellyfin), VPN-Gerät, Zeitzone."""
_HARMLESS_DEVICES = ("/dev/ttyUSB", "/dev/ttyACM", "/dev/serial/")
"""Funk- und Zigbee-Sticks, etwa für Home Assistant."""
_SOCKETS = ("docker.sock", "containerd.sock", "podman.sock")
_SHARED = {
    "network_mode": "das Netz", "pid": "die Prozesse", "ipc": "den gemeinsamen Speicher", "userns_mode": "die Benutzerkennungen",
    "uts": "den Rechnernamen", "cgroup": "die Steuergruppen",
}
_PRIVATE_MODE = 0o600
_SFTP_PERMISSION_DENIED = 3

STALE = "Die Datei wurde inzwischen auf dem Server geändert. Lade sie neu und trage deine Änderung noch einmal ein."
NOT_A_USER = "Eine Compose-Datei speichert nur ein Mensch über die Docker-Seite."
RISK_TOO_LOW = "Die Datei enthält riskante Einstellungen; dafür braucht es eine Freigabe mit hohem Risiko. Bitte neu speichern."


def save_action_spec() -> ActionSpec:
    return ActionSpec(
        action_type=SAVE_ACTION, label="Compose-Datei speichern", icon="save", default_risk=Risk.MEDIUM,
        description="Schreibt eine geprüfte Compose-Datei eines übernommenen Stacks auf den Server; die alte Fassung kommt in den Verlauf.",
        permissions=["hosts.execute"],
        # host_bound=False: Inhalt und Risikostufe legt immer die Docker-Seite fest, nie ein Formular auf der Server-Seite.
        host_bound=False,
        confirm_text="Die Compose-Datei auf dem Server wird ersetzt. Die alte Fassung bleibt im Verlauf. Fortfahren?",
    )


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cmd_check(stack: Stack, index: int) -> list[str]:
    """Im Ordner des Stacks `docker compose config` mit allen Profilen; die Datei Nummer `index` kommt über die
    Standardeingabe, damit Compose den Entwurf liest und nicht die Datei auf dem Server."""
    return ["sh", "-c", IN_FOLDER, "sh", stack.working_dir,
            *compose_argv(stack, "--profile", "*", "config", "--format", "json", stdin_index=index)]


def _host_path(raw: str) -> str:
    """Absoluter Pfad ohne `..` und doppelte Schrägstriche; `//` ist für Docker dasselbe wie `/`."""
    return "/" + posixpath.normpath(raw).lstrip("/")


def _under(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == root or path.startswith(root + "/") for root in roots)


def _sensitive(path: str) -> bool:
    """Ein Systemordner, etwas darin oder ein Ordner, der einen enthält (wie `/var` mit `/var/lib/docker`)."""
    if _under(path, _HARMLESS):
        return False
    return path == "/" or _under(path, _SENSITIVE_DIRS) or any(root.startswith(path + "/") for root in _SENSITIVE_DIRS)


def _path_warning(who: str, raw: Any, verb: str = "bindet", *, read_only: bool = False) -> str | None:
    """Ein Satz, wenn ein Pfad vom Server Docker selbst oder einen Systemordner in den Container bringt."""
    if not isinstance(raw, str) or not raw.startswith("/"):
        return None
    path = _host_path(raw)
    if path.endswith(_SOCKETS):
        return f"{who} bekommt Zugriff auf {posixpath.basename(path)}; wer den Container steuert, steuert damit den Server."
    if not _sensitive(path):
        return None
    if verb != "bindet":
        return f"{who} {verb} {path} vom Server."
    # Lesend oder schreibend steht im Satz, damit das Freigeben von Schreibrechten als neue Warnung zählt.
    return f"{who} bindet {path} vom Server ein{' (nur lesend)' if read_only else ''}."


def _device(raw: Any) -> str | None:
    source = raw.get("source") if isinstance(raw, dict) else raw.split(":", 1)[0] if isinstance(raw, str) else None
    return source if isinstance(source, str) and source else None


def risky_settings(config: dict[str, Any]) -> list[str]:
    """Einstellungen, mit denen ein Container den Server übernehmen oder Schutz abschalten kann, als Sätze für Menschen."""
    found: list[str] = []
    services = config.get("services")
    for name, svc in sorted(services.items() if isinstance(services, dict) else []):
        if not isinstance(svc, dict):
            continue
        who = f"„{name}“"
        if svc.get("privileged") is True:
            found.append(f"{who} bekommt alle Rechte des Servers (privileged).")
        for key, what in _SHARED.items():
            if svc.get(key) == "host":
                found.append(f"{who} teilt {what} mit dem Server ({key}: host).")
        caps = {str(c).upper().removeprefix("CAP_") for c in svc.get("cap_add") or [] if isinstance(c, str)}
        if caps & _RISKY_CAPS:
            found.append(f"{who} bekommt besondere Rechte: {', '.join(sorted(caps & _RISKY_CAPS))} (cap_add).")
        for opt in svc.get("security_opt") or []:
            if isinstance(opt, str) and opt.replace("=", ":", 1) in _UNCONFINED:
                found.append(f"{who} schaltet einen Schutz ab ({opt}).")
        for volume in svc.get("volumes") or []:
            if not isinstance(volume, dict) or volume.get("type") != "bind":
                continue
            if line := _path_warning(who, volume.get("source"), read_only=volume.get("read_only") is True):
                found.append(line)
        for device in svc.get("devices") or []:
            source = _device(device)
            if source and source.startswith("/"):
                path = _host_path(source)
                if not _under(path, _HARMLESS) and not path.startswith(_HARMLESS_DEVICES):
                    found.append(f"{who} bekommt das Gerät {path} des Servers (devices).")
        provider = svc.get("provider")
        if isinstance(provider, dict) and provider.get("type"):
            found.append(f"{who} startet beim Anwenden ein Programm direkt auf dem Server, außerhalb jedes Containers (provider: {provider['type']}).")
    # Ein benanntes Volume kann per `driver_opts` selbst ein Ordner des Servers sein, ebenso Geheimnisse und Konfigurationen.
    for section, label, verb in (("volumes", "Das Volume", "bindet"), ("secrets", "Das Geheimnis", "liest"), ("configs", "Die Konfiguration", "liest")):
        entries = config.get(section)
        for name, entry in sorted(entries.items() if isinstance(entries, dict) else []):
            if not isinstance(entry, dict):
                continue
            opts = entry.get("driver_opts") if isinstance(entry.get("driver_opts"), dict) else {}
            raw = opts.get("device") if section == "volumes" else entry.get("file")
            if section == "volumes" and "bind" not in str(opts.get("o", "")) and opts.get("type") != "none":
                continue
            if line := _path_warning(f"{label} „{name}“", raw, verb):
                found.append(line)
    return found


@dataclass(frozen=True)
class Checked:
    error: str | None = None
    """Compose lehnt die Datei ab."""
    unverified: str | None = None
    """Die Prüfung lief nicht (Server, Zeitlimit, Ordner); über die Datei sagt das nichts."""
    warnings: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    config: dict[str, Any] | None = field(default=None, compare=False, repr=False)
    """Die Datei, wie Compose sie aufgelöst hat (Pfade absolut, Variablen eingesetzt); nur zum Prüfen, nie für die Antwort."""

    @property
    def problem(self) -> str | None:
        if self.error is not None:
            return f"Compose lehnt die Datei ab: {self.error}"
        if self.unverified is not None:
            return f"Die Datei ließ sich nicht prüfen: {self.unverified}"
        return None

    def as_response(self) -> dict[str, Any]:
        return {"ok": self.problem is None, "error": self.error, "unverified": self.unverified, "warnings": list(self.warnings),
                "services": list(self.services)}


def parse_check(exit_code: int, stdout: str, stderr: str, name: str) -> Checked:
    """Antwort von `cmd_check`; in Fehlermeldungen steht statt „-“ der Name der Datei."""
    if exit_code == NO_FOLDER_EXIT:
        return Checked(unverified="Den Ordner des Stacks gibt es auf dem Server nicht mehr, oder er ist nicht lesbar.")
    if exit_code != 0:
        reason = first_reason((stderr or stdout or "").splitlines())
        return Checked(re.sub(r"(^|\s)-:", rf"\g<1>{name}:", reason))
    try:
        config = json.loads(stdout)
    except ValueError:
        config = None
    if not isinstance(config, dict):
        return Checked(unverified="Compose hat keine lesbare Antwort geliefert.")
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    return Checked(warnings=tuple(risky_settings(config)), services=tuple(sorted(services)), config=config)


def new_warnings(checked: Checked, before: Checked) -> list[str]:
    """Die Warnungen, die die Datei auf dem Server noch nicht hatte; ließ sie sich nicht prüfen, zählen alle."""
    if before.problem is not None:
        return list(checked.warnings)
    return [line for line in checked.warnings if line not in set(before.warnings)]


def changed_lines(old: bytes, new: bytes) -> str:
    """„+3 −1 Zeilen“: was der Freigebende über den Umfang erfährt, ohne dass Inhalt im Protokoll steht."""
    added = removed = 0
    for line in difflib.unified_diff(old.decode("utf-8", "replace").splitlines(), new.decode("utf-8", "replace").splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return f"+{added} −{removed} Zeilen"


def _stack_key(host_id: str, project: str) -> str:
    return hashlib.sha256(f"{host_id}\0{project}".encode()).hexdigest()[:32]


def _write_private(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        # Die halbe Kopie trägt den Inhalt und fiele sonst durch jedes Aufräumen.
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _mtime(path: Path) -> float | None:
    """Änderungszeit oder `None`, wenn die Datei inzwischen weg ist (zwei Speichern räumen gleichzeitig auf)."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


class Drafts:
    """Entwürfe zwischen Vorschlag und Freigabe; der Inhalt steht so nie in der Aktion und nie im Protokoll."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def put(self, host_id: str, project: str, path: str, content: bytes) -> str:
        self._root.mkdir(parents=True, exist_ok=True)
        self._prune()
        draft_id = secrets.token_hex(16)
        meta = {"host_id": host_id, "project": project, "path": path, "sha256": sha256(content), "created": time.time()}
        try:
            _write_private(self._root / f"{draft_id}.yaml", content)
            _write_private(self._root / f"{draft_id}.json", json.dumps(meta).encode())
        except OSError:
            self.drop(draft_id)
            raise
        return draft_id

    def get(self, draft_id: str) -> tuple[dict[str, Any], bytes] | None:
        if not _ID_RE.match(draft_id):
            return None
        try:
            meta = json.loads((self._root / f"{draft_id}.json").read_text(encoding="utf-8"))
            content = (self._root / f"{draft_id}.yaml").read_bytes()
        except (OSError, ValueError):
            return None
        if not isinstance(meta, dict) or meta.get("sha256") != sha256(content):
            return None
        return meta, content

    def drop(self, draft_id: str) -> None:
        if _ID_RE.match(draft_id):
            for suffix in (".json", ".yaml"):
                with contextlib.suppress(OSError):
                    (self._root / f"{draft_id}{suffix}").unlink()

    def _prune(self) -> None:
        metas = sorted(((meta, mtime) for meta in self._root.glob("*.json") if (mtime := _mtime(meta)) is not None), key=lambda m: m[1])
        cutoff = time.time() - DRAFT_TTL_S
        for index, (meta, mtime) in enumerate(metas):
            if mtime < cutoff or len(metas) - index >= MAX_DRAFTS:
                self.drop(meta.stem)
        # Erst nach einer Minute: Sonst träfe es einen Entwurf, dessen Beschreibung gerade noch geschrieben wird.
        for content in self._root.glob("*.yaml"):
            mtime = _mtime(content)
            if mtime is not None and mtime < time.time() - 60 and not content.with_suffix(".json").exists():
                self.drop(content.stem)


class History:
    """Die letzten Fassungen der Compose-Dateien eines Stacks, je Stack höchstens `HISTORY_KEEP`."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def add(self, host_id: str, project: str, path: str, content: bytes) -> str:
        folder = self._root / _stack_key(host_id, project)
        folder.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        # Mit Mikrosekunden, damit die Namen auch bei mehreren Fassungen in einer Sekunde nach Zeit sortieren.
        entry_id = f"{now:%Y%m%dT%H%M%S%f}Z-{secrets.token_hex(4)}"
        record = {"path": path, "saved_at": now.isoformat(), "sha256": sha256(content),
                  "content": content.decode("utf-8", "replace")}
        _write_private(folder / f"{entry_id}.json", json.dumps(record).encode())
        return entry_id

    def drop(self, host_id: str, project: str, entry_id: str) -> None:
        if _ENTRY_RE.match(entry_id):
            with contextlib.suppress(OSError):
                (self._root / _stack_key(host_id, project) / f"{entry_id}.json").unlink()

    def prune(self, host_id: str, project: str) -> None:
        """Erst nach einem gelungenen Speichern: Fehlversuche verdrängen so keine echten alten Fassungen."""
        for old in sorted((self._root / _stack_key(host_id, project)).glob("*.json"))[:-HISTORY_KEEP]:
            with contextlib.suppress(OSError):
                old.unlink()

    def entries(self, host_id: str, project: str) -> list[dict[str, Any]]:
        out = []
        for file in sorted((self._root / _stack_key(host_id, project)).glob("*.json"), reverse=True):
            record = self._load(file)
            if record is not None:
                out.append({"id": file.stem, "path": record["path"], "saved_at": record["saved_at"], "size": len(record["content"].encode())})
        return out

    def entry(self, host_id: str, project: str, entry_id: str) -> dict[str, Any] | None:
        if not _ENTRY_RE.match(entry_id):
            return None
        record = self._load(self._root / _stack_key(host_id, project) / f"{entry_id}.json")
        return None if record is None else {"id": entry_id, **record}

    @staticmethod
    def _load(file: Path) -> dict[str, Any] | None:
        try:
            record = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or not all(isinstance(record.get(k), str) for k in ("path", "saved_at", "content")):
            return None
        return record


class StackEditor:
    """Prüfen, Lesen und Vorschlagen für die Routen; geschrieben wird nur im Ausführer."""

    def __init__(self, ctx: Any, actions: DockerStackExecutor, data_dir: Path) -> None:
        self._ctx = ctx
        self._actions = actions
        self.drafts = Drafts(data_dir / "editor" / "drafts")
        self.history = History(data_dir / "editor" / "history")

    async def target(self, host_id: str, project: str, path: str) -> tuple[Any, Stack, int]:
        """Host, Stack und Nummer der Datei; nur für übernommene Stacks und ihre eigenen Dateien."""
        found = await self._actions.target(host_id, project)
        if isinstance(found, str):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=found)
        host, stack = found
        if path not in (stack.files or ()):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Diese Datei gehört nicht zum Stack.")
        return host, stack, stack.files.index(path)

    async def check(self, host: Any, stack: Stack, index: int, content: bytes) -> Checked:
        try:
            result = await self._ctx.exec.run_argv(host, cmd_check(stack, index), stdin=content, timeout_s=CHECK_TIMEOUT_S)
        except OSError as exc:
            if type(exc) is TimeoutError:
                return Checked(unverified=f"Compose hat nicht innerhalb von {CHECK_TIMEOUT_S} Sekunden geantwortet.")
            return Checked(unverified=f"Server nicht erreichbar: {str(exc) or type(exc).__name__}")
        except Exception as exc:  # noqa: BLE001 - nicht geprüft, nicht abgelehnt
            return Checked(unverified=f"Die Prüfung ist abgebrochen: {str(exc) or type(exc).__name__}")
        return parse_check(result.exit_code, result.stdout, result.stderr, posixpath.basename(stack.files[index]))

    async def propose(self, request: ActionRequest) -> Any:
        return await self._ctx.actions.propose(request, wait_s=REQUEST_WAIT_S)

    async def current(self, host: Any, path: str) -> bytes:
        """Die Datei, wie sie gerade auf dem Server liegt; ein Verweis (Symlink) wird nicht bearbeitet."""
        async with self._ctx.exec.sftp(host) as sftp:
            return (await _read_plain(sftp, path))[1]


class EditProblem(Exception):
    """Die Datei lässt sich nicht bearbeiten; der Text ist für Menschen."""


class Stale(EditProblem):
    def __init__(self) -> None:
        super().__init__(STALE)


async def _read_plain(sftp: Any, path: str) -> tuple[Any, bytes]:
    """Attribute und Inhalt einer normalen Datei; Verweise, Pipes, Geräte, zu große und nicht UTF-8-Dateien nicht."""
    try:
        attrs = await sftp.lstat(path)
    except Exception as exc:
        if no_such_file(exc):
            raise EditProblem("Die Datei gibt es auf dem Server nicht mehr.") from exc
        raise
    if attrs.permissions is not None and statmod.S_ISLNK(attrs.permissions):
        raise EditProblem("Die Datei ist ein Verweis auf eine andere Datei. Bearbeite sie dort, wo sie wirklich liegt.")
    if attrs.permissions is not None and not statmod.S_ISREG(attrs.permissions):
        raise EditProblem("Das ist keine normale Datei.")
    if (attrs.size or 0) > MAX_FILE_BYTES:
        raise EditProblem(f"Die Datei ist größer als {MAX_FILE_BYTES // 1024} KB.")
    async with sftp.open(path, "rb") as handle:
        data = await handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise EditProblem(f"Die Datei ist größer als {MAX_FILE_BYTES // 1024} KB.")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EditProblem("Die Datei ist nicht als UTF-8 gespeichert; Nodvard Deck bearbeitet sie nicht, damit keine Zeichen verloren gehen.") from exc
    return attrs, data


async def _unchanged(sftp: Any, path: str, base: str) -> None:
    """Kurz vor dem Ersetzen noch einmal: Hat jemand die Datei inzwischen geändert, gewinnt seine Fassung."""
    if sha256((await _read_plain(sftp, path))[1]) != base:
        raise Stale()


def _temp_path(path: str, kind: str) -> str:
    folder, name = posixpath.split(path)
    return posixpath.join(folder, f".{name[:100]}.nodvard-{kind}-{secrets.token_hex(6)}")


async def _take_over_owner(handle: Any, old: Any) -> bool:
    """Besitzer und Gruppe der alten Datei auf die neue übertragen, nur über den offenen Griff (Pfade folgen Links);
    `True`, wenn die neue Datei sie danach wirklich trägt."""
    if old.uid is None or old.gid is None:
        return True
    with contextlib.suppress(Exception):  # ohne root nicht erlaubt, das Ergebnis zählt
        await handle.chown(old.uid, old.gid)
    try:
        now = await handle.stat()
    except Exception:  # noqa: BLE001 - nicht prüfbar heißt nicht übertragen
        return False
    return (getattr(now, "uid", None), getattr(now, "gid", None)) == (old.uid, old.gid)


async def _replace(sftp: Any, tmp: str, path: str) -> None:
    """Ersetzt in einem Schritt; kennt der Server das nicht, wird die alte Datei beiseitegelegt und bei einem Fehler zurückgeholt."""
    try:
        await sftp.posix_rename(tmp, path)
        return
    except Exception as exc:  # nur „nicht unterstützt“ hat einen zweiten Weg
        if getattr(exc, "code", None) != _SFTP_OP_UNSUPPORTED:
            raise
    backup = _temp_path(path, "alt")
    await sftp.rename(path, backup)
    try:
        await sftp.rename(tmp, path)
    except BaseException:
        with contextlib.suppress(Exception):
            await sftp.rename(backup, path)
        raise
    with contextlib.suppress(Exception):
        await sftp.remove(backup)


async def _write_in_place(sftp: Any, path: str, content: bytes, base: str) -> None:
    """Direkt in die Datei, wenn sich Besitzer oder Ordner nicht anders halten lassen; der Inhalt liegt schon ganz vor."""
    await _unchanged(sftp, path, base)
    async with sftp.open(path, "wb") as handle:
        await handle.write(content)


async def _copy_mode(handle: Any, old: Any) -> str | None:
    """Die Rechte der alten Datei über den Griff; klappt das nicht, bleibt die neue bei 0600, und der Satz sagt es."""
    if old.permissions is None:
        return None
    wanted = statmod.S_IMODE(old.permissions)
    with contextlib.suppress(Exception):
        await handle.chmod(wanted)
        if statmod.S_IMODE((await handle.stat()).permissions) == wanted:
            return None
    return "Die Rechte der alten Datei ließen sich nicht übernehmen; die neue kann nur der Zugang von Nodvard Deck lesen."


async def _write(sftp: Any, path: str, old: Any, content: bytes, base: str) -> str | None:
    """Schreibt eine Datei daneben, die bis zum Ersetzen nur der Zugang lesen kann, übernimmt Besitzer und Rechte über den
    Griff und ersetzt dann in einem Schritt; so liest Compose nie eine halbe Datei. Liefert einen Hinweis oder `None`."""
    tmp = _temp_path(path, "neu")
    try:
        handle = await sftp.open(tmp, "xb", type(old)(permissions=_PRIVATE_MODE))
    except Exception as exc:
        # Datei beschreibbar, Ordner nicht: dann direkt hinein.
        if getattr(exc, "code", None) != _SFTP_PERMISSION_DENIED:
            raise
        await _write_in_place(sftp, path, content, base)
        return None
    keeps_owner, note = True, None
    try:
        async with handle:
            keeps_owner = await _take_over_owner(handle, old)
            if keeps_owner:
                await handle.write(content)
                note = await _copy_mode(handle, old)
        if keeps_owner:
            await _unchanged(sftp, path, base)
            await _replace(sftp, tmp, path)
    except BaseException:
        with contextlib.suppress(Exception):
            await sftp.remove(tmp)
        raise
    if not keeps_owner:
        # Ohne root ginge die Datei beim Ersetzen an den Zugang über; wer sie vorher schreiben durfte, verlöre das Recht.
        with contextlib.suppress(Exception):
            await sftp.remove(tmp)
        await _write_in_place(sftp, path, content, base)
    return note


async def _read_back(sftp: Any, path: str) -> bytes:
    async with sftp.open(path, "rb") as handle:
        return bytes(await handle.read(MAX_FILE_BYTES + 1))


AFTER_FAILURE = {
    "untouched": "",
    "restored": " Die alte Fassung steht wieder auf dem Server.",
    "unknown": " Die Datei auf dem Server ist jetzt vielleicht unvollständig. Die alte Fassung steht im Verlauf: Lade sie dort in "
    "den Editor und speichere sie erneut.",
}


async def _after_failure(sftp: Any, path: str, old: bytes, content: bytes) -> str:
    """Was nach einem abgebrochenen Schreiben auf dem Server steht: unverändert, schon gespeichert, wiederhergestellt oder
    unklar; eine halbe Datei bekommt dabei die alte Fassung zurück."""
    try:
        now = await _read_back(sftp, path)
    except Exception:  # noqa: BLE001 - Verbindung weg: Ob etwas geschrieben wurde, ist unklar
        return "unknown"
    if now == old:
        return "untouched"
    if now == content:
        return "saved"
    with contextlib.suppress(Exception):
        async with sftp.open(path, "wb") as handle:
            await handle.write(old)
        if await _read_back(sftp, path) == old:
            return "restored"
    return "unknown"


def _describe(exc: BaseException) -> str:
    if getattr(exc, "code", None) == _SFTP_PERMISSION_DENIED:
        return "Der Benutzer, mit dem Nodvard Deck sich anmeldet, darf diese Datei nicht schreiben."
    return str(exc) or type(exc).__name__


class DockerStackSaveExecutor:
    """Speichert einen Entwurf, wenn die Datei auf dem Server noch die ist, die der Nutzer bearbeitet hat."""

    action_types = frozenset({SAVE_ACTION})

    def __init__(self, ctx: Any, editor: StackEditor, actions: DockerStackExecutor) -> None:
        self._ctx = ctx
        self._editor = editor
        self._actions = actions

    async def execute(self, req: ActionRequest) -> ActionResult:
        payload = req.payload or {}
        draft_id = str(payload.get("draft") or "")
        try:
            return await self._execute(req, draft_id)
        finally:
            # Ein Vorschlag läuft nur einmal; sonst bliebe der Inhalt bis zum nächsten Aufräumen im Datenordner liegen.
            self._editor.drafts.drop(draft_id)

    async def _execute(self, req: ActionRequest, draft_id: str) -> ActionResult:
        payload = req.payload or {}
        project, path = str(payload.get("project") or ""), str(payload.get("path") or "")
        if getattr(req.proposed_by.type, "value", req.proposed_by.type) != "user":
            return ActionResult(success=False, error=NOT_A_USER)
        found = await self._actions.target(req.host_ref, project)
        if isinstance(found, str):
            return ActionResult(success=False, error=found)
        host, stack = found
        draft = self._editor.drafts.get(draft_id)
        if draft is None or (draft[0].get("host_id"), draft[0].get("project"), draft[0].get("path"), draft[0].get("sha256")) != (
            host.id, project, path, payload.get("sha256"),
        ):
            return ActionResult(success=False, error="Der Entwurf ist abgelaufen oder passt nicht. Bitte noch einmal speichern.")
        if path not in (stack.files or ()):
            return ActionResult(success=False, error="Diese Datei gehört nicht mehr zum Stack.")
        with self._actions.locks.hold(host.id, project, "Speichern") as busy:
            if busy is not None:
                return ActionResult(success=False, error=busy_text(busy))
            return await self._save(req, host, stack, path, draft[1], bool(payload.get("apply")))

    async def _save(self, req: ActionRequest, host: Any, stack: Stack, path: str, content: bytes, apply: bool) -> ActionResult:
        started = time.monotonic()
        index, base = stack.files.index(path), str((req.payload or {}).get("base_sha256") or "")
        checked = await self._editor.check(host, stack, index, content)
        if checked.problem is not None:
            return ActionResult(success=False, error=checked.problem)
        history = self._editor.history
        try:
            async with self._ctx.exec.sftp(host) as sftp:
                try:
                    old_attrs, old = await _read_plain(sftp, path)
                except EditProblem:
                    raise
                except Exception as exc:  # noqa: BLE001 - gelesen, nicht geschrieben
                    return ActionResult(success=False, error=f"Die Datei ließ sich nicht lesen: {str(exc) or type(exc).__name__}. Gespeichert wurde nichts.")
                if sha256(old) != base:
                    raise Stale()
                before = await self._editor.check(host, stack, index, old)
                if new_warnings(checked, before) and RISK_ORDER.index(req.risk) < RISK_ORDER.index(Risk.HIGH):
                    return ActionResult(success=False, error=RISK_TOO_LOW)
                try:
                    entry = await asyncio.to_thread(history.add, host.id, stack.project, path, old)
                except OSError:
                    return ActionResult(success=False, error="Die alte Fassung ließ sich nicht sichern, deshalb wurde nichts geändert.")
                try:
                    note = await _write(sftp, path, old_attrs, content, base)
                except Stale:
                    history.drop(host.id, stack.project, entry)
                    raise
                except Exception as exc:  # noqa: BLE001 - erst nachsehen, was auf dem Server steht
                    state = await _after_failure(sftp, path, old, content)
                    if state != "saved":
                        if state == "untouched":
                            history.drop(host.id, stack.project, entry)
                        return ActionResult(success=False, error=f"Die Datei ließ sich nicht schreiben: {_describe(exc)}{AFTER_FAILURE[state]}")
                    note = None
        except EditProblem as exc:
            return ActionResult(success=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - als Grund melden
            return ActionResult(success=False, error=f"Die Datei ließ sich nicht schreiben: {_describe(exc)}")
        history.prune(host.id, stack.project)
        saved = f"„{posixpath.basename(path)}“ von Stack „{stack.project}“ gespeichert. Die alte Fassung steht im Verlauf." + (f" {note}" if note else "")
        duration_ms = int((time.monotonic() - started) * 1000)
        # `saved` sagt der Oberfläche, dass die Datei auf dem Server steht, auch wenn das Anwenden danach scheitert.
        if not apply:
            return ActionResult(success=True, exit_code=0, output=saved, duration_ms=duration_ms, detail={"saved": True})
        try:
            applied = await self._actions.run(host, stack, "docker.stack_up")
        except Exception as exc:  # noqa: BLE001 - die Datei steht schon auf dem Server, das muss die Oberfläche erfahren
            return ActionResult(
                success=False, output=saved, duration_ms=duration_ms, detail={"saved": True},
                error=f"Gespeichert, aber das Anwenden ist gescheitert: {_describe(exc)}",
            )
        output = "\n".join(part for part in (saved, applied.output) if part)
        detail = {**(applied.detail or {}), "saved": True}
        if applied.success:
            return ActionResult(success=True, exit_code=0, output=output, duration_ms=duration_ms + (applied.duration_ms or 0), detail=detail)
        return ActionResult(
            success=False, exit_code=applied.exit_code, output=output, duration_ms=duration_ms + (applied.duration_ms or 0),
            error=f"Gespeichert, aber das Anwenden ist gescheitert: {applied.error}", detail=detail,
        )

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        payload = req.payload or {}
        then = " und den Stack danach anwenden" if payload.get("apply") else ""
        return DryRunReport(
            would_change=True,
            summary=f"Würde „{posixpath.basename(str(payload.get('path') or '?'))}“ von Stack „{payload.get('project', '?')}“ ersetzen "
            f"({payload.get('changes', '?')}){then}.",
        )


class _CheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=1024)
    content: str = Field(max_length=MAX_FILE_BYTES)


class _SaveIn(_CheckIn):
    base_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    apply: bool = False


def editor_routes(editor: StackEditor, docker_host: Any, current_actor: Any) -> APIRouter:
    router = APIRouter()

    async def _target(host_id: str, project: str, path: str) -> tuple[Any, Stack, int]:
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        if not path.lower().endswith(FILE_SUFFIXES):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nur Compose-Dateien mit der Endung .yml oder .yaml.")
        await docker_host(host_id)
        return await editor.target(host_id, project, path)

    def _bytes(content: str) -> bytes:
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"Die Datei ist größer als {MAX_FILE_BYTES // 1024} KB.")
        return data

    @router.post("/hosts/{host_id}/stacks/{project}/check")
    async def check_file(host_id: str, project: str, body: _CheckIn) -> dict:
        """Prüft einen Entwurf mit Compose selbst, ohne etwas zu speichern; dazu riskante Einstellungen."""
        host, stack, index = await _target(host_id, project, body.path)
        return (await editor.check(host, stack, index, _bytes(body.content))).as_response()

    @router.post("/hosts/{host_id}/stacks/{project}/save", status_code=status.HTTP_202_ACCEPTED)
    async def save_file(host_id: str, project: str, body: _SaveIn, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt das Speichern vor (Gate-Aktion `docker.stack_save`), mit hohem Risiko bei riskanten Einstellungen."""
        host, stack, index = await _target(host_id, project, body.path)
        content = _bytes(body.content)
        checked = await editor.check(host, stack, index, content)
        if checked.problem is not None:
            code = status.HTTP_422_UNPROCESSABLE_ENTITY if checked.error is not None else status.HTTP_502_BAD_GATEWAY
            raise HTTPException(status_code=code, detail=checked.problem)
        try:
            old = await editor.current(host, body.path)
        except EditProblem as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except Exception as exc:  # als Grund zeigen, nicht als 500
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Datei nicht lesbar: {str(exc) or type(exc).__name__}") from exc
        if sha256(old) != body.base_sha256:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=STALE)
        if old == content:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Die Datei ist unverändert, es gibt nichts zu speichern.")
        # Hohes Risiko nur für riskante Einstellungen, die die Datei auf dem Server noch nicht hat.
        fresh = new_warnings(checked, await editor.check(host, stack, index, old))
        risk = Risk.HIGH if fresh else Risk.MEDIUM
        try:
            draft = await asyncio.to_thread(editor.drafts.put, host.id, project, body.path, content)
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Der Entwurf ließ sich nicht ablegen ({exc.strerror or type(exc).__name__}). Ist der Datenordner von Nodvard Deck "
                "voll oder schreibgeschützt?",
            ) from exc
        name, changes = posixpath.basename(body.path), changed_lines(old, content)
        reason = f"Compose-Datei „{name}“ von Stack „{project}“ speichern ({changes})" + (" und den Stack anwenden" if body.apply else "") + "."
        if fresh:
            reason += " Neu und riskant: " + " ".join(fresh)
        decision = await editor.propose(
            ActionRequest(
                action_type=SAVE_ACTION, host_ref=host.id, risk=risk, proposed_by=actor, reason=reason,
                payload={
                    "project": project, "path": body.path, "draft": draft, "sha256": sha256(content), "base_sha256": body.base_sha256,
                    "apply": body.apply, "changes": changes, "warnings": fresh,
                },
            ),
        )
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": risk.value, "detail": decision.detail}

    @router.get("/hosts/{host_id}/stacks/{project}/history")
    async def history(host_id: str, project: str) -> dict:
        """Die gespeicherten alten Fassungen, neueste zuerst, ohne Inhalt."""
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        host = await docker_host(host_id)
        return {"entries": editor.history.entries(host.id, project)}

    @router.get("/hosts/{host_id}/stacks/{project}/history/{entry_id}")
    async def history_entry(host_id: str, project: str, entry_id: str) -> dict:
        """Eine alte Fassung mit Inhalt, etwa um sie in den Editor zu laden."""
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        host = await docker_host(host_id)
        entry = editor.history.entry(host.id, project, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diese Fassung gibt es nicht (mehr).")
        return entry

    return router
