"""Stacks aus Git: Deck holt ein Repository selbst über HTTPS in einen eigenen Speicher und zeigt, was sich am Stack
ändern würde. Auf den Server kommt dabei nichts, und aus dem Repository läuft nie ein Programm."""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import ipaddress
import json
import logging
import os
import posixpath
import re
import shutil
import stat as statmod
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk.errors import SecretUnavailable
from nodvard_sdk.types import Actor
from pydantic import BaseModel, ConfigDict, Field

from .adopt import AdoptedStacks, StoreProblem
from .editor import EditProblem, _read_plain, _stack_key
from .git_fetch import (
    MAX_PROBLEMS,
    Fetched,
    GitProblem,
    Limits,
    _shown,
    clean_leftovers,
    name_ok,
)
from .image_apply import PROJECT_RE
from .image_updates import _write_atomic
from .registries import _ADDRESS_RE, _NAME_RE
from .stack_actions import DockerStackExecutor
from .stacks import FILE_SUFFIXES, MAX_FILE_BYTES, Stack, no_such_file

log = logging.getLogger(__name__)

STORE_FILE = "git_sources.json"
SECRET_PREFIX = "docker-git-token:"
WORKER = Path(__file__).with_name("git_fetch.py")
GRACE_S = 10
"""So lange darf der Prozess nach seiner eigenen Frist noch brauchen, dann beendet Deck ihn."""
PARALLEL = 2
"""Höchstens so viele Prozesse holen gleichzeitig, jeder darf bis zu `Limits.memory_bytes` belegen."""
REAP_S = 10
"""So lange wartet Deck nach dem Beenden darauf, dass der Prozess wirklich weg ist."""
_WORKER_ENV = ("PATH", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "SSL_CERT_FILE", "SSL_CERT_DIR")
_PROXY_ENV = ("https_proxy", "http_proxy", "all_proxy", "no_proxy")
LIMITS = Limits(compose_bytes=MAX_FILE_BYTES)

_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?\Z")
# Endet ein Name auf eine Zahl, lesen ihn Browser und Bibliotheken als IPv4-Adresse, auch 2130706433 oder 0x7f.1.
_NUMBER_LABEL_RE = re.compile(r"^(0x[0-9a-f]*|[0-9]+)\Z")
_NOT_A_SERVER = (ipaddress.ip_network("0.0.0.0/8"), ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("169.254.0.0/16"))
# Feste Namen für den eigenen Rechner, Docker trägt die ip6-Namen in jeden Container ein.
_LOCAL_NAMES = frozenset({"localhost", "localhost4", "localhost6", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})
_URL_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._~-]{1,200}\Z")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")

NOT_ADOPTED = "Der Stack ist nicht übernommen. Übernimm ihn zuerst, dann lässt sich eine Quelle eintragen."
NO_SOURCE = "Für diesen Stack ist noch kein Repository eingetragen."
CHANGED = "Das Repository dieses Stacks wurde inzwischen geändert oder vergessen. Lade die Ansicht neu."
UNREADABLE = "Die Antwort beim Holen war unlesbar."


def check_url(raw: str) -> str:
    """Die Adresse eines Repositorys in einheitlicher Form; nur `https://` ohne Zugangsdaten, Abfrage oder Anker."""
    url = raw.strip()
    if len(url) > 512:
        raise ValueError("Die Adresse ist länger als 512 Zeichen.")
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise ValueError("Nur Adressen mit https:// gehen, etwa https://github.com/name/repo.")
    if "@" in parts.netloc:
        raise ValueError("Zugangsdaten gehören nicht in die Adresse. Trag sie als Zugang in den Einstellungen ein.")
    if parts.query or parts.fragment or "?" in url or "#" in url:
        raise ValueError("Die Adresse darf kein ? und kein # enthalten.")
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        raise ValueError("Der Port in der Adresse ist ungültig.") from None
    if not _HOST_RE.match(host):
        raise ValueError("Der Name des Servers in der Adresse ist ungültig.")
    if problem := _host_problem(host):
        raise ValueError(problem)
    segments = parts.path.strip("/").split("/")
    if not parts.path.strip("/") or any(s in (".", "..") or not _URL_SEGMENT_RE.match(s) for s in segments):
        raise ValueError("Nach dem Server fehlt der Pfad des Repositorys, oder er enthält ungewöhnliche Zeichen.")
    netloc = host if port in (None, 443) else f"{host}:{port}"
    return f"https://{netloc}/{'/'.join(segments)}"


def _host_problem(host: str) -> str | None:
    """Warum Deck von diesem Server nicht holt: er zeigt auf Deck selbst, in kein echtes Netz oder ist eine verkappte Zahl."""
    local = ("Die Adresse zeigt auf Deck selbst oder in kein echtes Netz (etwa 127.0.0.1 oder 169.254.x.x). Trag den Server so "
             "ein, wie andere Rechner im Netz ihn erreichen, etwa git.example.org oder 192.168.1.10.")
    if host in _LOCAL_NAMES or host.endswith(".localhost"):
        return local
    if not _NUMBER_LABEL_RE.match(host.rsplit(".", 1)[-1]):
        return None
    try:
        ip = ipaddress.IPv4Address(host)
    except ValueError:
        return "Eine Adresse aus Zahlen bitte als vier Zahlen mit Punkten schreiben, etwa 192.168.1.10."
    if any(ip in net for net in _NOT_A_SERVER) or ip.is_multicast or ip.is_reserved:
        return local
    return None


def check_ref(raw: str) -> str:
    """Name eines Branches oder Tags, so wie Git ihn zulässt, ohne `refs/` davor."""
    ref = raw.strip()
    if ref.startswith("refs/"):
        raise ValueError("Nur den Namen des Branches oder Tags, etwa main oder v1.2, ohne refs/ davor.")
    if (not _REF_RE.match(ref) or ".." in ref or "//" in ref or "/." in ref or ref.endswith(("/", ".", ".lock"))
            or "@{" in ref):
        raise ValueError("Der Name des Branches oder Tags ist ungültig.")
    return ref


def check_path(raw: str) -> str:
    """Pfad der Compose-Datei im Repository: relativ, ohne `..`, mit der Endung .yml oder .yaml."""
    path = raw.strip()
    if not path or len(path) > 512:
        raise ValueError("Der Pfad zur Compose-Datei fehlt oder ist länger als 512 Zeichen.")
    if path.startswith("/") or "\\" in path:
        raise ValueError("Der Pfad zählt ab dem Anfang des Repositorys, ohne / davor, etwa compose.yaml oder shop/compose.yaml.")
    segments = path.split("/")
    if len(segments) > LIMITS.depth or not all(name_ok(s) for s in segments):
        raise ValueError("Der Pfad enthält .., leere Teile oder ungewöhnliche Zeichen.")
    if not path.lower().endswith(FILE_SUFFIXES):
        raise ValueError("Die Compose-Datei endet auf .yml oder .yaml.")
    return path


def host_of(url: str) -> str:
    """`github.com` oder `git.example.org:3000`: wofür ein Zugang gilt."""
    parts = urlsplit(url)
    return parts.netloc.lower()


@dataclass(frozen=True)
class Access:
    name: str
    host: str
    username: str

    @property
    def secret_label(self) -> str:
        return f"{SECRET_PREFIX}{self.name}"


def accesses(settings: dict[str, Any]) -> dict[str, Access]:
    """Die gültigen Zugänge aus den Einstellungen; ein doppelter Name fällt mit allen seinen Einträgen weg."""
    found: dict[str, Access] = {}
    twice: set[str] = set()
    for raw in settings.get("git_accesses") or []:
        if not isinstance(raw, dict):
            continue
        entry = Access(str(raw.get("name") or ""), str(raw.get("host") or "").removesuffix(":443"), str(raw.get("username") or ""))
        # Port 80 hielte der Kern für dieselbe Adresse wie ohne Port; das Token bliebe dann gespeichert.
        if not (_NAME_RE.match(entry.name) and _ADDRESS_RE.match(entry.host) and not entry.host.endswith(":80")
                and re.match(r"^[^\s:]{1,255}\Z", entry.username)):
            continue
        if entry.name in found:
            twice.add(entry.name)
        found[entry.name] = entry
    return {name: entry for name, entry in found.items() if name not in twice}


@dataclass(frozen=True)
class Source:
    url: str
    ref: str
    path: str
    access: str | None
    set_at: str

    def to_json(self) -> dict[str, Any]:
        return {"url": self.url, "ref": self.ref, "path": self.path, "access": self.access, "set_at": self.set_at}


def _source(raw: Any) -> Source | None:
    """Ein Eintrag aus der Datei; was nicht mehr passt (etwa von Hand verändert), fällt weg."""
    if not isinstance(raw, dict):
        return None
    access = raw.get("access")
    try:
        source = Source(check_url(str(raw.get("url") or "")), check_ref(str(raw.get("ref") or "")),
                        check_path(str(raw.get("path") or "")), access if isinstance(access, str) else None, str(raw.get("set_at") or ""))
    except ValueError:
        return None
    return None if source.access is not None and not _NAME_RE.match(source.access) else source


class GitSources:
    """Die eingetragenen Repositorys je Server und Stack, als JSON im Datenordner des Moduls."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / STORE_FILE
        self._lock = asyncio.Lock()

    def _read(self, *, before_write: bool = False) -> dict[str, dict[str, Source]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8")).get("sources")
            if not isinstance(raw, dict):
                raise TypeError("sources ist keine Zuordnung")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            if before_write:
                raise StoreProblem("Die Liste der Repositorys ließ sich gerade nicht lesen. Bitte später noch einmal.") from exc
            log.warning("docker_git_store_unreadable path=%s error=%s", self._path, exc)
            return {}
        except (ValueError, TypeError, AttributeError):
            # Eine kaputte Datei bleibt als Beleg liegen; das nächste Schreiben beginnt leer.
            log.warning("docker_git_store_broken path=%s (wird ignoriert)", self._path)
            if before_write:
                with contextlib.suppress(OSError):
                    os.replace(self._path, self._path.with_name(f"{self._path.name}.kaputt-{datetime.now(UTC):%Y%m%d%H%M%S}"))
            return {}
        out: dict[str, dict[str, Source]] = {}
        for host_id, projects in raw.items():
            if isinstance(projects, dict):
                entries = {p: s for p, s in ((p, _source(v)) for p, v in projects.items()) if isinstance(p, str) and PROJECT_RE.match(p) and s}
                if entries:
                    out[str(host_id)] = entries
        return out

    def _write(self, data: dict[str, dict[str, Source]]) -> None:
        plain = {h: {p: s.to_json() for p, s in projects.items()} for h, projects in data.items() if projects}
        try:
            _write_atomic(self._path, json.dumps({"sources": plain}, indent=1, sort_keys=True))
        except OSError as exc:
            raise StoreProblem(
                f"Die Liste der Repositorys ließ sich nicht speichern ({exc.strerror or type(exc).__name__}). "
                "Ist der Datenordner von Nodvard Deck voll oder schreibgeschützt?"
            ) from exc

    def get(self, host_id: str, project: str) -> Source | None:
        return self._read().get(host_id, {}).get(project)

    async def put(self, host_id: str, project: str, source: Source) -> None:
        async with self._lock:
            data = self._read(before_write=True)
            data.setdefault(host_id, {})[project] = source
            self._write(data)

    async def remove(self, host_id: str, project: str) -> bool:
        async with self._lock:
            data = self._read(before_write=True)
            if data.get(host_id, {}).pop(project, None) is None:
                return False
            self._write(data)
        return True


def layout_problem(stack: Stack) -> str | None:
    """Warum sich der Stack nicht mit einem Ordner aus Git abgleichen lässt, oder `None`."""
    files = stack.files or ()
    if len(files) != 1:
        return "Der Stack besteht aus mehreren Compose-Dateien. Aus Git lassen sich bisher nur Stacks mit einer Datei holen."
    if posixpath.dirname(files[0]) != posixpath.normpath(stack.working_dir):
        return "Die Compose-Datei des Stacks liegt nicht direkt in seinem Ordner. So lässt er sich nicht mit einem Ordner aus Git abgleichen."
    return None


ENV_NAME = ".env"
"""Umgebungsdateien halten meist Geheimnisse; Deck ersetzt sie auf dem Server nie, eine fehlende schreibt es mit 0600."""


def env_file(stack: Stack, path: str) -> bool:
    """Eine Umgebungsdatei des Stacks: `.env` in jedem Ordner und jede, die beim Übernehmen genannt war."""
    return posixpath.basename(path) == ENV_NAME or path in stack.env_files


def server_path(stack: Stack, fetched: Fetched, rel: str) -> str:
    """Wohin eine Datei aus dem Repository auf dem Server gehört; die Compose-Datei ersetzt die des Stacks."""
    return stack.files[0] if rel == fetched.compose else posixpath.join(stack.working_dir, rel)


def content_problems(stack: Stack, fetched: Fetched) -> list[str]:
    """Gründe aus dem Repository selbst, die gegen ein Einspielen sprechen."""
    problems = list(fetched.problems)
    target = posixpath.basename(stack.files[0])
    if fetched.compose in fetched.files:
        if target != fetched.compose and target in fetched.files:
            problems.append(
                f"Im Ordner aus dem Repository liegt neben „{fetched.compose}“ auch „{target}“, der Name der "
                "Compose-Datei des Stacks. Deck wüsste nicht, welche gilt.")
        if len(fetched.files[fetched.compose][1]) > MAX_FILE_BYTES:
            problems.append(f"Die Compose-Datei ist größer als {MAX_FILE_BYTES // 1024} KB.")
    return problems


async def _server_state(sftp: Any, path: str, data: bytes) -> str:
    """`new`, `same` oder `changed`; `other`, wenn dort ein Verweis oder etwas anderes als eine Datei liegt."""
    try:
        attrs = await sftp.lstat(path)
    except Exception as exc:
        if no_such_file(exc):
            return "new"
        raise
    if attrs.permissions is not None and not statmod.S_ISREG(attrs.permissions):
        return "other"
    if attrs.size is not None and attrs.size != len(data):
        return "changed"
    async with sftp.open(path, "rb") as handle:
        now = await handle.read(len(data) + 1)
    return "same" if bytes(now) == data else "changed"


def _worker_env() -> dict[str, str]:
    """Nur was der Prozess braucht: Pfad, Sprache, Zeitzone, Zertifikate und Proxy."""
    return {k: v for k, v in os.environ.items() if k.upper() in _WORKER_ENV or k.lower() in _PROXY_ENV or k.startswith("LC_")}


async def _exchange(proc: Any, request: bytes, cap: int) -> tuple[bytes, bytes]:
    """Schreibt den Auftrag und liest Antwort und Fehlerausgabe bis zum Ende, beides begrenzt."""
    too_much = False

    async def stdout() -> bytes:
        nonlocal too_much
        answer = bytearray()
        with contextlib.suppress(OSError):
            while chunk := await proc.stdout.read(64 * 1024):
                if too_much:
                    continue
                answer += chunk
                if len(answer) > cap:
                    too_much = True
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
        return bytes(answer)

    async def stderr() -> bytes:
        kept = bytearray()
        with contextlib.suppress(OSError):
            while chunk := await proc.stderr.read(64 * 1024):
                kept += chunk[: max(0, 2048 - len(kept))]
        return bytes(kept)

    # Stirbt der Prozess vor dem Lesen, steht der Grund in seiner Fehlerausgabe.
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        proc.stdin.write(request)
        await proc.stdin.drain()
    proc.stdin.close()
    answer, errors = await asyncio.gather(stdout(), stderr())
    await proc.wait()
    if too_much:
        raise GitProblem(UNREADABLE)
    return answer, errors


async def _reap(proc: Any) -> None:
    """Beendet den Prozess und liest seine Ausgabe leer; `wait()` kehrt erst zurück, wenn alle Rohre zu sind."""
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    try:
        async with asyncio.timeout(REAP_S):
            for stream in (proc.stdout, proc.stderr):
                with contextlib.suppress(OSError):
                    while await stream.read(64 * 1024):
                        pass
            await proc.wait()
    except TimeoutError:
        log.warning("docker_git_worker_stuck pid=%s", proc.pid)


async def run_worker(request: dict[str, Any], limits: Limits) -> dict[str, Any]:
    """Holt in einem eigenen Prozess mit festen Argumenten; nach der Frist beendet Deck ihn hart."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(WORKER), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=_worker_env(),
        )
    except (OSError, NotImplementedError) as exc:
        raise GitProblem(f"Deck konnte das Holen nicht starten ({type(exc).__name__}).") from None
    try:
        answer, errors = await asyncio.wait_for(_exchange(proc, json.dumps(request).encode(), 3 * limits.total_bytes + 1024 * 1024),
                                                limits.seconds + GRACE_S)
    except TimeoutError:
        raise GitProblem(f"Holen hat länger als {limits.seconds:g} Sekunden gedauert.") from None
    finally:
        if proc.returncode is None:
            await _reap(proc)
    try:
        out = json.loads(answer)
    except (ValueError, RecursionError):
        out = None
    if not isinstance(out, dict):
        log.warning("docker_git_worker_failed code=%s stderr=%r", proc.returncode, errors.decode("utf-8", "replace"))
        raise GitProblem("Holen ist abgebrochen, wohl weil Speicher oder Rechenzeit nicht reichten. Ist das Repository "
                         "ungewöhnlich groß?")
    return out


def parse_answer(answer: dict[str, Any], limits: Limits) -> Fetched:
    """Die Antwort des Prozesses, noch einmal geprüft: Pfade, Rechte und Größen wie beim Lesen aus dem Repository."""
    if answer.get("ok") is not True:
        if answer.get("error"):
            log.warning("docker_git_fetch_error error=%r", answer["error"])
        raise GitProblem(_shown(str(answer.get("problem") or UNREADABLE), 500))
    try:
        files: dict[str, tuple[int, bytes]] = {}
        for entry in answer["files"]:
            rel, mode = str(entry["path"]), {"644": 0o644, "755": 0o755}[entry["mode"]]
            parts = rel.split("/")
            if len(parts) > limits.depth or not all(name_ok(part) for part in parts):
                raise ValueError(rel)
            files[rel] = (mode, base64.b64decode(entry["data"], validate=True))
        if len(files) > limits.files or sum(len(data) for _, data in files.values()) > limits.total_bytes:
            raise ValueError("zu groß")
        texts = {key: str(answer[key]) for key in ("commit", "ref_kind", "author", "when", "message", "compose")}
        lines = [[_shown(str(line), 600) for line in answer[key]][:MAX_PROBLEMS * 2] for key in ("problems", "warnings")]
        if any(len(texts[key]) > 300 for key in texts) or not name_ok(texts["compose"]):
            raise ValueError("zu lang")
        texts.update(author=_shown(texts["author"], 300), message=_shown(texts["message"], 300))
    except (KeyError, TypeError, ValueError, binascii.Error):
        raise GitProblem(UNREADABLE) from None
    if not re.match(r"^[0-9a-f]{40}\Z", texts["commit"]) or texts["ref_kind"] not in ("branch", "tag"):
        raise GitProblem(UNREADABLE)
    return Fetched(files=files, problems=lines[0], warnings=lines[1], **texts)


class GitStacks:
    """Quelle eintragen, holen und vergleichen; am Server ändert sich dabei nichts."""

    def __init__(self, ctx: Any, actions: DockerStackExecutor, adopted: AdoptedStacks, data_dir: Path) -> None:
        self.ctx = ctx
        self.actions = actions
        self._adopted = adopted
        self.sources = GitSources(data_dir)
        self._root = data_dir / "git"
        self._locks: dict[str, asyncio.Lock] = {}
        self.limits = LIMITS
        self._slots = asyncio.Semaphore(PARALLEL)
        self.run_fetch: Callable[[dict[str, Any], Limits], Awaitable[dict[str, Any]]] = run_worker
        """Test-Einhänger: führt den Auftrag statt im eigenen Prozess anders aus."""
        self.on_forget: Callable[[str, str], Awaitable[None]] | None = None
        """Wird nach dem Vergessen aufgerufen, damit auch Eingespieltes und der Rückweg wegfallen."""

    def _lock(self, host_id: str, project: str) -> asyncio.Lock:
        """Holen und Vergessen eines Stacks nacheinander, nie gleichzeitig im selben Speicher."""
        return self._locks.setdefault(_stack_key(host_id, project), asyncio.Lock())

    def repo_dir(self, host_id: str, project: str) -> Path:
        return self._root / _stack_key(host_id, project)

    def adopted_stack(self, host_id: str, project: str) -> Stack:
        entry = self._adopted.for_host(host_id).get(project)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NOT_ADOPTED)
        return entry.stack(project)

    async def accesses(self) -> dict[str, Access]:
        return accesses(await self.ctx.settings.get())

    async def forget(self, host_id: str, project: str) -> None:
        """Nach dem Aufheben der Übernahme oder dem Löschen des Stacks: Quelle und Geholtes fallen weg."""
        try:
            await self.sources.remove(host_id, project)
        finally:
            async with self._lock(host_id, project):
                store = self.repo_dir(host_id, project)
                await asyncio.to_thread(shutil.rmtree, store, ignore_errors=True)
                await asyncio.to_thread(clean_leftovers, store)
            if self.on_forget is not None:
                await self.on_forget(host_id, project)

    async def fetch(self, host_id: str, project: str, source: Source) -> Fetched:
        async with contextlib.AsyncExitStack() as stack:
            credentials = None
            if source.access is not None:
                access = (await self.accesses()).get(source.access)
                if access is None:
                    raise GitProblem(f"Den Zugang „{source.access}“ gibt es nicht (mehr) in den Einstellungen.")
                if access.host != host_of(source.url):
                    raise GitProblem(f"Der Zugang „{access.name}“ gilt für {access.host}, das Repository liegt aber auf {host_of(source.url)}.")
                try:
                    handle = await self.ctx.secrets.get_handle(access.secret_label)
                    token = str(await stack.enter_async_context(self.ctx.vault_use(handle)) or "")
                except (LookupError, SecretUnavailable):
                    token = ""
                except Exception as exc:  # noqa: BLE001 - nie den Text der Ausnahme weitergeben, er könnte das Geheimnis betreffen
                    raise GitProblem(f"Das Token für „{access.name}“ ließ sich nicht aus dem Tresor lesen ({type(exc).__name__}).") from None
                if not token:
                    raise GitProblem(f"Für „{access.name}“ ist kein Token im Tresor. Trag es unter Einstellungen → Docker ein.")
                credentials = (access.username, token)
            async with self._lock(host_id, project):
                # Zwischen Lesen der Quelle und der Sperre kann jemand sie geändert oder vergessen haben.
                if self.sources.get(host_id, project) != source:
                    raise GitProblem(CHANGED)
                self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
                request = {"url": source.url, "ref": source.ref, "path": source.path, "store": str(self.repo_dir(host_id, project)),
                           "credentials": list(credentials) if credentials else None, "limits": asdict(self.limits)}
                async with self._slots:
                    return parse_answer(await self.run_fetch(request, self.limits), self.limits)

    async def preview(self, host: Any, stack: Stack, source: Source) -> dict[str, Any]:
        fetched = await self.fetch(host.id, stack.project, source)
        out: dict[str, Any] = {
            "source": source.to_json(),
            "commit": {"id": fetched.commit, "short": fetched.commit[:7], "ref_kind": fetched.ref_kind, "author": fetched.author,
                       "date": fetched.when, "message": fetched.message},
            "problems": content_problems(stack, fetched), "warnings": [], "files": [], "compose": None,
        }
        target = stack.files[0]
        if fetched.compose in fetched.files:
            out["warnings"] = list(fetched.warnings)
        try:
            async with self.ctx.exec.sftp(host) as sftp:
                for rel, (mode, data) in sorted(fetched.files.items()):
                    path = server_path(stack, fetched, rel)
                    state = await _server_state(sftp, path, data)
                    if state == "other":
                        out["problems"].append(f"Auf dem Server ist „{path}“ ein Verweis oder keine normale Datei; Deck ersetzt das nicht.")
                    out["files"].append({"path": rel, "server_path": path, "state": state, "size": len(data), "mode": f"{mode:o}",
                                         "env": env_file(stack, path)})
                plain = all(f["state"] != "other" for f in out["files"] if f["server_path"] == target)
                if plain and fetched.compose in fetched.files and len(fetched.files[fetched.compose][1]) <= MAX_FILE_BYTES:
                    new = fetched.files[fetched.compose][1]
                    try:
                        old = (await _read_plain(sftp, target))[1].decode("utf-8")
                    except EditProblem as exc:
                        out["problems"].append(str(exc))
                        old = None
                    out["compose"] = {"path": target, "old": old, "new": new.decode("utf-8", "replace"),
                                      "same": old is not None and old.encode() == new}
        except Exception as exc:  # als Grund zeigen, nicht als 500
            raise GitProblem(f"Die Dateien auf dem Server ließen sich nicht lesen: {str(exc)[:200] or type(exc).__name__}") from exc
        out["problems"] = list(dict.fromkeys(out["problems"]))[:MAX_PROBLEMS]
        out["ok"] = not out["problems"]
        # Eine vorhandene Umgebungsdatei ersetzt Deck nie; weicht nur sie ab, gibt es nichts einzuspielen.
        out["unchanged"] = out["ok"] and bool(out["files"]) and all(f["state"] == "same" or (f["env"] and f["state"] == "changed")
                                                                     for f in out["files"])
        return out


class _SourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=512)
    ref: str = Field(default="main", max_length=200)
    path: str = Field(default="compose.yaml", max_length=512)
    access: str | None = Field(default=None, max_length=64)


async def _audit(ctx: Any, action: str, host_id: str, project: str, actor: Any, detail: dict[str, Any]) -> None:
    try:
        await ctx.audit.log(action=action, outcome="success", target_type="stack", target_id=f"{host_id}:{project}", detail=detail, actor=actor)
    except Exception:  # gespeichert ist gespeichert
        log.exception("docker_git_audit_failed action=%s", action)


def git_routes(git: GitStacks, docker_host: Callable[[str], Awaitable[Any]], current_actor: Any) -> APIRouter:
    router = APIRouter()

    def _project(project: str) -> None:
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")

    @router.get("/hosts/{host_id}/stacks/{project}/git")
    async def get_source(host_id: str, project: str) -> dict:
        """Das eingetragene Repository und die Zugänge zur Auswahl; nur aus dem Speicher von Deck."""
        _project(project)
        host = await docker_host(host_id)
        stack = git.adopted_stack(host.id, project)
        source = git.sources.get(host.id, project)
        return {
            "source": source.to_json() if source else None,
            "accesses": [{"name": a.name, "host": a.host} for a in (await git.accesses()).values()],
            "layout_problem": layout_problem(stack),
        }

    @router.put("/hosts/{host_id}/stacks/{project}/git")
    async def put_source(host_id: str, project: str, body: _SourceIn, actor: Actor = Depends(current_actor)) -> dict:
        """Trägt das Repository ein, aus dem der Stack kommen soll; geholt wird erst über „Holen und ansehen“."""
        _project(project)
        host = await docker_host(host_id)
        stack = git.adopted_stack(host.id, project)
        if problem := layout_problem(stack):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=problem)
        try:
            url, ref, path = check_url(body.url), check_ref(body.ref), check_path(body.path)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from None
        if body.access is not None:
            access = (await git.accesses()).get(body.access)
            if access is None:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Den Zugang „{body.access}“ gibt es nicht in den Einstellungen.")
            if access.host != host_of(url):
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    detail=f"Der Zugang „{access.name}“ gilt für {access.host}, das Repository liegt aber auf {host_of(url)}.")
        source = Source(url, ref, path, body.access, datetime.now(UTC).isoformat(timespec="seconds"))
        try:
            await git.sources.put(host.id, project, source)
        except StoreProblem as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        await _audit(git.ctx, "docker.stack_git_source", host.id, project, actor, {"url": url, "ref": ref, "path": path, "access": body.access})
        return {"source": source.to_json()}

    @router.delete("/hosts/{host_id}/stacks/{project}/git")
    async def delete_source(host_id: str, project: str, actor: Actor = Depends(current_actor)) -> dict:
        """Vergisst das Repository und alles Geholte; am Stack und auf dem Server ändert sich nichts."""
        _project(project)
        host = await docker_host(host_id)
        if git.sources.get(host.id, project) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_SOURCE)
        try:
            await git.forget(host.id, project)
        except StoreProblem as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        await _audit(git.ctx, "docker.stack_git_source_removed", host.id, project, actor, {})
        return {"source": None}

    @router.post("/hosts/{host_id}/stacks/{project}/git/fetch")
    async def fetch_source(host_id: str, project: str, actor: Actor = Depends(current_actor)) -> dict:
        """Holt den Stand aus dem Repository und vergleicht ihn mit den Dateien auf dem Server, nur lesend."""
        _project(project)
        host = await docker_host(host_id)
        git.adopted_stack(host.id, project)
        source = git.sources.get(host.id, project)
        if source is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_SOURCE)
        found = await git.actions.target(host.id, project)
        if isinstance(found, str):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=found)
        stack = found[1]
        if problem := layout_problem(stack):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=problem)
        try:
            out = await git.preview(host, stack, source)
        except GitProblem as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None
        await _audit(git.ctx, "docker.stack_git_fetch", host.id, project, actor, {"url": source.url, "ref": source.ref, "commit": out["commit"]["id"]})
        return out

    return router
