"""Stacks lesen: die Compose-Projekte eines Servers aus den Labels ihrer Container, und ob `docker compose up` an ihnen
etwas ändern würde, weil die Prüfsumme eines Dienstes in der Datei nicht mehr zu seinem Container passt."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import secrets
import shlex
import stat as statmod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, status

from . import commands
from .capabilities import is_own_container, parse_stats_line
from .compose_errors import scrub_compose_output
from .image_apply import (
    LABEL_CONFIG_FILES,
    LABEL_ENV_FILE,
    LABEL_HASH,
    LABEL_ONEOFF,
    LABEL_PROJECT,
    LABEL_SERVICE,
    LABEL_VERSION,
    LABEL_WORKDIR,
    MAX_FILES,
    PORTAINER_PREFIX,
    PROJECT_RE,
    SERVICE_RE,
    _split_files,
    _valid_path,
)

_LABELS = {
    "project": LABEL_PROJECT, "service": LABEL_SERVICE, "hash": LABEL_HASH, "oneoff": LABEL_ONEOFF,
    "working_dir": LABEL_WORKDIR, "config_files": LABEL_CONFIG_FILES, "env_files": LABEL_ENV_FILE, "version": LABEL_VERSION,
}
LIST_FORMAT = (
    '{"id":{{json .ID}},"name":{{json .Names}},"state":{{json .State}},"status":{{json .Status}},"image":{{json .Image}}'
    + "".join(f',"{key}":{{{{json (.Label "{label}")}}}}' for key, label in _LABELS.items())
    + "}"
)
"""Eine JSON-Zeile je Container; Pfade und Namen dürfen so jedes Zeichen enthalten, ohne die Felder zu verschieben."""
HASH_RE = re.compile(r"^[0-9a-f]{64}\Z")
MAX_STACKS = 60
"""So viele Stacks prüft ein Abruf je Server auf Änderungen; die übrigen heißen „nicht geprüft“."""
MAX_FILE_BYTES = 256 * 1024
FILE_SUFFIXES = (".yml", ".yaml")
"""Nur Compose-Dateien werden gezeigt: Die Pfade stammen aus Labels, die jeder mit Zugriff auf Docker setzen kann."""
MAX_ENV_BYTES = 64 * 1024
MAX_ENV_NAMES = 200
_ENV_KEY_RE = re.compile(r"[^\W][\w.-]*")
"""Was Compose als Namen annimmt: Buchstaben (auch Umlaute), Ziffern, `_`, `.` und `-`."""
LIST_TIMEOUT_S = 15
HASH_TIMEOUT_S = 120
STATS_TIMEOUT_S = 20
_WARNING_RE = re.compile(r"^\s*(?:WARN\b|time=\S+ level=(?:warning|info|debug)\b)")
"""Warnungen von Compose bis 2.x (`WARN[0000] …`) und ab 5.x (`time="…" level=warning msg=…`)."""


class StackProblem(Exception):
    """Der Server ließ sich nicht lesen; der Text ist für Menschen."""


@dataclass
class Member:
    id: str
    name: str
    state: str
    status: str
    service: str
    hash: str
    image: str = ""


@dataclass
class Stack:
    project: str
    members: list[Member]
    working_dir: str
    files: tuple[str, ...] | None
    env_files: tuple[str, ...]
    source: str
    """`compose`: Dateien auf dem Server; `portainer`: in Portainers eigenem Speicher; `unknown`: ohne lesbare Angaben."""
    version: str = ""
    """Die Compose-Version, mit der die Container angelegt wurden."""
    portainer_files: tuple[str, ...] = ()
    """Bei `portainer`: die Dateien, wie Portainer sie in seinem eigenen Speicher nennt."""


def cmd_list(project: str | None = None) -> str:
    """Alle Container mit Compose-Projekt, auf Wunsch nur die eines Projekts; auch gestoppte."""
    if project is not None and not PROJECT_RE.match(project):
        raise ValueError(f"Ungültiger Projektname: {project!r}")
    selector = f"{LABEL_PROJECT}={project}" if project is not None else LABEL_PROJECT
    return shlex.join(["docker", "ps", "-a", "--filter", f"label={selector}", "--format", LIST_FORMAT])


def compose_argv(stack: Stack, *args: str, stdin_index: int | None = None) -> list[str]:
    """`docker compose` mit Projekt, Ordner und Dateien des Stacks; mit `stdin_index` liest Compose diese Datei aus der
    Standardeingabe statt vom Server."""
    if not PROJECT_RE.match(stack.project) or not stack.files or not _valid_path(stack.working_dir):
        raise ValueError(f"Stack ohne gültige Angaben: {stack.project!r}")
    if not all(_valid_path(p) for p in (*stack.files, *stack.env_files)):
        raise ValueError(f"Ungültiger Pfad im Stack {stack.project!r}")
    if stdin_index is not None and not 0 <= stdin_index < len(stack.files):
        raise ValueError(f"Keine Datei Nummer {stdin_index} im Stack {stack.project!r}")
    argv = ["docker", "compose", "--ansi", "never", "-p", stack.project, "--project-directory", stack.working_dir]
    for index, path in enumerate(stack.files):
        argv += ["-f", "-" if index == stdin_index else path]
    for path in stack.env_files:
        argv += ["--env-file", path]
    return argv + list(args)


def cmd_hashes(stacks: list[Stack], marker: str) -> str:
    """Ein Aufruf je Server, je Stack im eigenen Ordner wie beim Start (`${PWD}` in der Datei): die Prüfsummen aller
    Dienste, auch derer mit Profil, und die Dienste, die ohne Profil starten; `marker` macht die Trennzeilen unfälschbar."""
    if not re.fullmatch(r"[0-9a-f]{8,32}", marker):
        raise ValueError(f"Ungültige Trennmarke: {marker!r}")
    parts = []
    for stack in stacks:
        cd = f"cd -- {shlex.quote(stack.working_dir)}"
        for kind, args in (("hashes", ("--profile", "*", "config", "--hash", "*")), ("services", ("config", "--services"))):
            parts.append(
                f"printf '%s\\n' {shlex.quote(f'#{marker} {kind} {stack.project}')}; "
                f"({cd} && {shlex.join(compose_argv(stack, *args))}) 2>&1; printf '#{marker} rc %s\\n' \"$?\""
            )
    return shlex.join(["sh", "-c", "\n".join(parts)])


def parse_members(text: str) -> list[Stack]:
    """Die Zeilen von `cmd_list()` -> Stacks, ohne Einmal-Container (`docker compose run`)."""
    grouped: dict[str, list[dict[str, str]]] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("id", "name", "project")):
            continue
        fields = {k: v if isinstance(v, str) else "" for k, v in row.items()}
        if not PROJECT_RE.match(fields["project"]) or fields.get("oneoff", "").lower() == "true":
            continue
        grouped.setdefault(fields["project"], []).append(fields)
    return [_stack(project, rows) for project, rows in sorted(grouped.items())]


def _stack(project: str, rows: list[dict[str, str]]) -> Stack:
    members = sorted(
        (Member(id=r["id"], name=r["name"], state=r.get("state", "").lower(), status=r.get("status", ""),
                service=r.get("service", ""), hash=r.get("hash", ""), image=r.get("image", "")) for r in rows),
        key=lambda m: m.name,
    )
    # Alle Container eines Projekts tragen dieselben Angaben; der erste mit Ordner zählt.
    first = next((r for r in rows if r.get("working_dir")), rows[0])
    working_dir, raw_files, raw_env = first.get("working_dir", ""), first.get("config_files", ""), first.get("env_files", "")
    version = first.get("version", "")
    if working_dir.startswith(PORTAINER_PREFIX) or any(f.startswith(PORTAINER_PREFIX) for f in raw_files.split(",")):
        return Stack(project, members, working_dir, None, (), "portainer", version, tuple(f for f in raw_files.split(",") if f))
    files = _split_files(raw_files, working_dir) if raw_files and _valid_path(working_dir) else None
    env_files = _split_files(raw_env, working_dir) if raw_env else ()
    if files is None or env_files is None or not 1 <= len(files) <= MAX_FILES or len(env_files) > MAX_FILES:
        return Stack(project, members, working_dir, None, (), "unknown", version)
    return Stack(project, members, working_dir, files, env_files, "compose", version)


@dataclass
class Check:
    hashes: dict[str, str] | None = None
    """Dienst -> Prüfsumme in der Datei, über alle Profile."""
    defaults: set[str] | None = None
    """Die Dienste, die ohne Profil starten."""
    error: str | None = None


def parse_hashes(text: str, marker: str) -> dict[str, Check]:
    """Ausgabe von `cmd_hashes()` -> Projekt -> Prüfsummen und Dienste, oder die erste Zeile der Fehlermeldung."""
    out: dict[str, Check] = {}
    section: tuple[str, str] | None = None
    lines: list[str] = []
    for line in text.splitlines():
        words = line.split(" ")
        if words[0] != f"#{marker}":
            if section is not None:
                lines.append(line)
            continue
        if len(words) == 3 and words[1] in ("hashes", "services"):
            section, lines = (words[1], words[2]), []
            continue
        if len(words) != 3 or words[1] != "rc" or section is None:
            continue
        kind, project = section
        check = out.setdefault(project, Check())
        if words[2] != "0":
            check.error = check.error or first_reason(lines)
        elif kind == "hashes":
            check.hashes = {}
            for entry in lines:
                service, _, value = entry.strip().partition(" ")
                if SERVICE_RE.match(service) and HASH_RE.match(value.strip()):
                    check.hashes[service] = value.strip()
        else:
            check.defaults = {entry.strip() for entry in lines if SERVICE_RE.match(entry.strip())}
        section = None
    return out


def first_reason(lines: list[str]) -> str:
    """Die erste Zeile einer Fehlermeldung von Compose, ohne Warnungen davor und ohne Inhalt aus einer `.env`."""
    scrubbed = scrub_compose_output("\n".join(lines)).splitlines()
    text = next((line.strip() for line in scrubbed if line.strip() and not _WARNING_RE.match(line)), "")
    return text[:300] or "Compose konnte die Datei nicht lesen."


def pending_changes(stack: Stack, check: Check) -> dict[str, list[str]] | None:
    """Was `docker compose up` ändern würde: geänderte, neue und nicht mehr beschriebene Dienste; `None`, wenn unklar."""
    if check.error or check.hashes is None or check.defaults is None:
        return None
    hashes = check.hashes
    services = {m.service for m in stack.members if m.service}
    # Ein Container ohne Prüfsummen-Label (sehr altes Compose) lässt sich nicht vergleichen und zählt nicht als geändert.
    changed = sorted({m.service for m in stack.members if m.service in hashes and m.hash and m.hash != hashes[m.service]})
    return {"changed": changed, "added": sorted(check.defaults - services), "removed": sorted(services - set(hashes))}


def stack_state(members: list[Member]) -> str:
    running = sum(1 for m in members if m.state == "running")
    return "stopped" if running == 0 else "running" if running == len(members) else "partial"


def has_problem(member: Member) -> bool:
    return member.state in ("restarting", "dead") or "(unhealthy)" in member.status


def stack_out(
    stack: Stack, check: Check | None, *, unchecked: str | None = None, adopted: bool = False, adoption_note: str | None = None,
) -> dict[str, Any]:
    """`unchecked`: warum ein Stack mit lesbarer Datei nicht geprüft wurde; `adoption_note`: warum eine Übernahme nicht mehr passt."""
    # Ohne Container gibt es nichts zu vergleichen; „neu in der Datei“ wäre dann jeder Dienst.
    changes = pending_changes(stack, check) if check is not None and stack.members else None
    if not stack.members:
        check_error = None
    elif check is not None and check.error:
        check_error = f"Die Datei ließ sich nicht prüfen: {check.error}"
    elif stack.source == "compose" and changes is None:
        check_error = unchecked or "Nicht geprüft: Die Prüfung lieferte für diesen Stack keine Antwort."
    else:
        check_error = None
    return {
        "project": stack.project,
        "state": stack_state(stack.members),
        "running": sum(1 for m in stack.members if m.state == "running"),
        "problems": sum(1 for m in stack.members if has_problem(m)),
        "total": len(stack.members),
        "containers": [{"name": m.name, "service": m.service or None, "state": m.state, "status": m.status} for m in stack.members],
        "source": stack.source,
        "working_dir": stack.working_dir or None,
        "config_files": list(stack.files or ()),
        "is_self": any(is_own_container(m.id) for m in stack.members),
        "changes": changes,
        "changed": None if changes is None else any(changes.values()),
        "check_error": check_error,
        "adopted": adopted,
        "adoption_note": adoption_note,
    }


def sum_usage(stacks: list[Stack], stats_text: str) -> dict[str, dict[str, Any]]:
    """CPU und Arbeitsspeicher je Stack, aufsummiert über seine laufenden Container."""
    per_name = {}
    for line in stats_text.splitlines():
        parsed = parse_stats_line(line)
        if parsed is not None:
            per_name[parsed[0]] = parsed[1]
    usage: dict[str, dict[str, Any]] = {}
    for stack in stacks:
        values = [per_name[m.name] for m in stack.members if m.name in per_name]
        if not values:
            continue
        memory = [v["mem_used"] for v in values]
        cpu = [v["cpu_percent"] for v in values if v["cpu_percent"] is not None]
        usage[stack.project] = {
            "cpu_percent": round(sum(cpu), 2) if cpu else None,
            # Ohne Speicher-Abrechnung (Raspberry Pi) ist der RAM unbekannt, nicht 0.
            "mem_used": sum(memory) if all(m is not None for m in memory) else None,
        }
    return usage


class DockerStacks:
    """Liest die Stacks eines Servers; schreibt nichts auf den Server."""

    def __init__(self, ctx: Any, adopted: Any = None) -> None:
        self._ctx = ctx
        self._adopted = adopted

    async def _exec(self, host: Any, command: str, timeout_s: int) -> Any:
        try:
            return await self._ctx.exec.run(host, command, timeout_s=timeout_s)
        except TimeoutError as exc:
            raise StackProblem(f"Der Server hat nicht innerhalb von {timeout_s} Sekunden geantwortet.") from exc
        except Exception as exc:  # noqa: BLE001 - als Grund zeigen, nicht als 500
            raise StackProblem(f"Server nicht erreichbar: {str(exc) or type(exc).__name__}") from exc

    async def stacks(self, host: Any, project: str | None = None) -> list[Stack]:
        result = await self._exec(host, cmd_list(project), LIST_TIMEOUT_S)
        if result.exit_code != 0:
            raise StackProblem(_first_line(result.stderr) or f"docker ps endete mit {result.exit_code}.")
        return parse_members(result.stdout)

    async def overview(self, host: Any) -> dict[str, Any]:
        stacks = await self.stacks(host)
        adopted = self._adopted.for_host(host.id) if self._adopted is not None else {}
        running = {s.project for s in stacks}
        # Ein übernommener Stack ohne Container (nach `docker compose down`) bleibt mit seiner Datei in der Liste.
        stacks = sorted(stacks + [entry.stack(p) for p, entry in adopted.items() if p not in running], key=lambda s: s.project)
        readable = [s for s in stacks if s.source == "compose" and s.members]
        checks: dict[str, Check] = {}
        check_error = None
        if readable:
            marker = secrets.token_hex(8)
            try:
                result = await self._exec(host, cmd_hashes(readable[:MAX_STACKS], marker), HASH_TIMEOUT_S)
            except StackProblem as exc:
                check_error = f"Die Prüfung auf Änderungen ist abgebrochen. {exc}"
            else:
                checks = parse_hashes(result.stdout, marker)
                if not checks and result.exit_code != 0:
                    check_error = f"Die Prüfung auf Änderungen ist gescheitert: {_first_line(result.stderr) or result.exit_code}"
        reasons = {s.project: f"Nicht geprüft: Je Server werden höchstens {MAX_STACKS} Stacks geprüft." for s in readable[MAX_STACKS:]}
        if check_error:
            reasons.update({s.project: "Nicht geprüft." for s in readable[:MAX_STACKS]})
        out = []
        for stack in stacks:
            entry = adopted.get(stack.project)
            note = entry.note(stack) if entry is not None else None
            out.append(stack_out(
                stack, checks.get(stack.project), unchecked=reasons.get(stack.project),
                adopted=entry is not None and note is None, adoption_note=note,
            ))
        return {"stacks": out, "check_error": check_error}

    async def pending(self, host: Any, stack: Stack) -> dict[str, list[str]] | str:
        """Was `docker compose up` an diesem laufenden Stack ändern würde, oder warum sich das nicht prüfen ließ."""
        marker = secrets.token_hex(8)
        try:
            result = await self._exec(host, cmd_hashes([stack], marker), HASH_TIMEOUT_S)
        except StackProblem as exc:
            return f"Die Prüfung auf Änderungen in der Datei ist abgebrochen. {exc}"
        check = parse_hashes(result.stdout, marker).get(stack.project)
        changes = pending_changes(stack, check) if check is not None else None
        if changes is None:
            reason = check.error if check is not None and check.error else _first_line(result.stderr) or "keine Antwort"
            return f"Ob die Datei Änderungen hat, die noch nicht angewendet sind, ließ sich nicht prüfen: {reason}"
        return changes

    async def usage(self, host: Any) -> dict[str, dict[str, Any]]:
        stacks = await self.stacks(host)
        result = await self._exec(host, commands.stats(), STATS_TIMEOUT_S)
        if result.exit_code != 0:
            raise StackProblem(_first_line(result.stderr) or f"docker stats endete mit {result.exit_code}.")
        return sum_usage(stacks, result.stdout)

    async def files(self, host: Any, project: str) -> dict[str, Any]:
        """Die Compose-Dateien eines Stacks, so wie sie auf dem Server liegen; die Pfade kommen nur aus den Labels oder der Übernahme."""
        found = [s for s in await self.stacks(host, project) if s.project == project]
        adopted = self._adopted.for_host(host.id).get(project) if self._adopted is not None else None
        if not found and adopted is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Auf diesem Server gibt es keinen Stack „{project}“.")
        stack = found[0] if found else adopted.stack(project)
        if stack.source == "portainer":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Diesen Stack verwaltet Portainer. Seine Datei liegt in Portainers eigenem Speicher, nicht in einem Ordner auf dem Server.",
            )
        if stack.files is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NO_FILE)
        # Ohne eigene Angabe liest Compose die `.env` im Ordner des Stacks, wenn es sie gibt.
        env_paths = stack.env_files or (f"{stack.working_dir.rstrip('/')}/.env",)
        try:
            async with self._ctx.exec.sftp(host) as sftp:
                files = [await read_file(sftp, path) for path in stack.files]
                env = [await read_env(sftp, path, optional=not stack.env_files) for path in env_paths]
        except Exception as exc:  # noqa: BLE001 - als Grund zeigen, nicht als 500
            raise StackProblem(f"Dateien nicht lesbar: {str(exc) or type(exc).__name__}") from exc
        return {"project": project, "working_dir": stack.working_dir, "files": files, "env": [e for e in env if e is not None]}


TOO_BIG = f"Die Datei ist größer als {MAX_FILE_BYTES // 1024} KB und wird hier nicht gezeigt."
NO_FILE = (
    "Docker nennt für diesen Stack keine Compose-Datei, die Nodvard Deck lesen kann: Er wurde mit einem sehr alten Compose "
    "angelegt, oder im Pfad stehen ungewöhnliche Zeichen."
)


def no_such_file(exc: BaseException) -> bool:
    """Meldet SFTP „gibt es nicht“? Ein abgerissener Kanal (oft mit Code 2) oder fehlende Rechte zählen nicht dazu."""
    return isinstance(exc, FileNotFoundError) or type(exc).__name__ in ("SFTPNoSuchFile", "SFTPNoSuchPath")


def _unreadable(exc: BaseException) -> str:
    return "Die Datei gibt es nicht mehr." if no_such_file(exc) else f"Die Datei ließ sich nicht lesen: {str(exc) or type(exc).__name__}"


async def read_file(sftp: Any, path: str) -> dict[str, Any]:
    if not path.lower().endswith(FILE_SUFFIXES):
        return {"path": path, "content": None, "error": "Nodvard Deck liest nur Compose-Dateien mit der Endung .yml oder .yaml."}
    try:
        # SFTP folgt Verweisen: Die Endung zählt auch am Ziel, sonst ließe sich über einen Verweis jede Datei des Zugangs zeigen.
        real = str(await sftp.realpath(path))
        attrs = await sftp.stat(path)
    except Exception as exc:  # noqa: BLE001 - als Grund zeigen
        return {"path": path, "content": None, "error": _unreadable(exc)}
    if not real.lower().endswith(FILE_SUFFIXES):
        return {"path": path, "content": None, "error": "Die Datei verweist auf eine Datei ohne Endung .yml oder .yaml; Nodvard Deck liest sie nicht."}
    mode = getattr(attrs, "permissions", None)
    if mode is not None and not statmod.S_ISREG(mode):
        return {"path": path, "content": None, "error": "Das ist keine normale Datei."}
    if (getattr(attrs, "size", None) or 0) > MAX_FILE_BYTES:
        return {"path": path, "content": None, "error": TOO_BIG}
    try:
        async with sftp.open(path, "rb") as handle:
            data = await handle.read(MAX_FILE_BYTES + 1)
    except Exception as exc:  # noqa: BLE001
        return {"path": path, "content": None, "error": f"Die Datei ließ sich nicht lesen: {str(exc) or type(exc).__name__}"}
    if len(data) > MAX_FILE_BYTES:
        return {"path": path, "content": None, "error": TOO_BIG}
    return {
        "path": path, "content": data.decode("utf-8", errors="replace"), "error": None, "sha256": hashlib.sha256(data).hexdigest(),
        "edit_error": await _edit_error(sftp, path, data),
    }


async def _edit_error(sftp: Any, path: str, data: bytes) -> str | None:
    """Warum der Editor diese Datei nicht anbietet, oder `None`."""
    with contextlib.suppress(Exception):  # ohne lstat entscheidet das Speichern
        link = await sftp.lstat(path)
        if link.permissions is not None and statmod.S_ISLNK(link.permissions):
            return "Die Datei ist ein Verweis auf eine andere Datei. Bearbeite sie dort, wo sie wirklich liegt."
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "Die Datei ist nicht als UTF-8 gespeichert; Nodvard Deck bearbeitet sie nicht, damit keine Zeichen verloren gehen."
    return None


@dataclass
class EnvNames:
    names: list[str]
    more: bool = False
    """Es gibt mehr als `MAX_ENV_NAMES` Namen."""
    stopped_at: int | None = None
    """Die Zeile, ab der sich die Namen nicht mehr sicher lesen lassen; danach wird nichts mehr gelesen."""


def _assignment(text: str, pos: int, end: int) -> tuple[str, int] | None:
    """(Name, Beginn des Werts) einer Zeile `NAME=wert`, `export NAME=wert` oder `NAME: wert`, sonst `None`."""
    line = text[pos:end]
    body = line.lstrip(" \t")
    if body.startswith("export") and body[6:7] in (" ", "\t") and re.search(r"[=:]", body[7:]):
        body = body[7:].lstrip(" \t")
    cut = min((i for i in (body.find("="), body.find(":")) if i >= 0), default=-1)
    key = body[:cut].strip(" \t") if cut > 0 else ""
    if not _ENV_KEY_RE.fullmatch(key):
        return None
    return key, end - len(body) + cut + 1


def env_names(text: str) -> EnvNames:
    """Die Namen der Variablen einer `.env`, nie die Werte; Zeilen eines mehrzeiligen Werts in Anführungszeichen zählen
    nicht, und wo sich eine Zeile nicht sicher lesen lässt, endet das Lesen, damit kein Wert als Name erscheint."""
    text = text.removeprefix("\ufeff")
    found = EnvNames([])
    pos, line = 0, 1
    while pos < len(text):
        end = _line_end(text, pos)
        body = text[pos:end].strip(" \t\r")
        if body and not body.startswith("#"):
            assignment = _assignment(text, pos, end)
            if assignment is None:
                found.stopped_at = line
                break
            key, start = assignment
            if key not in found.names:
                if len(found.names) == MAX_ENV_NAMES:
                    found.more = True
                    break
                found.names.append(key)
            while start < end and text[start] in " \t":
                start += 1
            if start < end and text[start] in "'\"":
                close = _closing_quote(text, start)
                line += text.count("\n", end, close)
                end = _line_end(text, close)
                # Compose liest dahinter eine weitere Zuweisung; deren Wert kann über mehrere Zeilen gehen.
                rest = text[close + 1:end].strip(" \t\r")
                if rest and not rest.startswith("#"):
                    found.stopped_at = line
                    break
        pos, line = end + 1, line + 1
    return found


def _line_end(text: str, pos: int) -> int:
    end = text.find("\n", pos)
    return len(text) if end < 0 else end


def _closing_quote(text: str, start: int) -> int:
    # Wie Compose: Der Backslash maskiert in einfachen wie in doppelten Anführungszeichen das nächste Zeichen.
    quote = text[start]
    pos = start + 1
    while pos < len(text):
        if text[pos] == "\\":
            pos += 2
            continue
        if text[pos] == quote:
            return pos
        pos += 1
    return len(text)


def _env_file_name(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name == ".env" or name.endswith(".env") or name.startswith(".env.")


async def read_env(sftp: Any, path: str, *, optional: bool) -> dict[str, Any] | None:
    """Die Namen aus einer `.env`, nie die Werte; mit `optional` gibt es für eine fehlende Datei keinen Eintrag."""
    if not _env_file_name(path):
        return {"path": path, "names": [], "note": None, "error": "Nodvard Deck liest nur Dateien, die .env heißen oder auf .env enden."}
    try:
        # Wie bei read_file zählt der Name auch am Ziel eines Verweises.
        real = str(await sftp.realpath(path))
        attrs = await sftp.stat(path)
    except Exception as exc:  # noqa: BLE001 - wie bei read_file
        if optional and no_such_file(exc):
            return None
        return {"path": path, "names": [], "note": None, "error": _unreadable(exc)}
    if not _env_file_name(real):
        return {"path": path, "names": [], "note": None,
                "error": "Die Datei verweist auf eine Datei, die nicht .env heißt; Nodvard Deck liest sie nicht."}
    mode = getattr(attrs, "permissions", None)
    if mode is not None and not statmod.S_ISREG(mode):
        return {"path": path, "names": [], "note": None, "error": "Das ist keine normale Datei."}
    too_big = {"path": path, "names": [], "note": None, "error": f"Die Datei ist größer als {MAX_ENV_BYTES // 1024} KB und wird hier nicht gelesen."}
    if (getattr(attrs, "size", None) or 0) > MAX_ENV_BYTES:
        return too_big
    try:
        async with sftp.open(path, "rb") as handle:
            data = await handle.read(MAX_ENV_BYTES + 1)
    except Exception as exc:  # noqa: BLE001
        return {"path": path, "names": [], "note": None, "error": f"Die Datei ließ sich nicht lesen: {str(exc) or type(exc).__name__}"}
    if len(data) > MAX_ENV_BYTES:
        return too_big
    found = env_names(data.decode("utf-8", errors="replace"))
    note = None
    if found.stopped_at is not None:
        note = f"Ab Zeile {found.stopped_at} liest Nodvard Deck die Namen nicht mehr sicher; die Namen danach fehlen hier."
    elif found.more:
        note = f"Gezeigt sind die ersten {MAX_ENV_NAMES} Namen."
    return {"path": path, "names": found.names, "error": None, "note": note}


def _first_line(text: str) -> str:
    return next((line.strip() for line in scrub_compose_output(text or "").splitlines() if line.strip()), "")[:300]


def stack_routes(stacks: DockerStacks, docker_host: Callable[[str], Awaitable[Any]]) -> APIRouter:
    router = APIRouter()

    async def _guarded(host_id: str, read: Callable[[Any], Awaitable[Any]]) -> Any:
        host = await docker_host(host_id)
        try:
            return await read(host)
        except StackProblem as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    @router.get("/hosts/{host_id}/stacks")
    async def list_stacks(host_id: str) -> dict:
        """Alle Compose-Projekte des Servers mit Zustand und dem Hinweis, ob die Datei seit dem letzten Anwenden geändert wurde."""
        return {"host_id": host_id, **await _guarded(host_id, stacks.overview)}

    @router.get("/hosts/{host_id}/stacks/usage")
    async def stack_usage(host_id: str) -> dict:
        """CPU und Arbeitsspeicher je Stack; dauert ein paar Sekunden, deshalb getrennt von der Liste."""
        return {"host_id": host_id, "usage": await _guarded(host_id, stacks.usage)}

    @router.get("/hosts/{host_id}/stacks/{project}/file")
    async def stack_file(host_id: str, project: str) -> dict:
        """Die Compose-Dateien eines Stacks zum Ansehen, dazu die Namen aus seiner `.env` (ohne Werte)."""
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        return await _guarded(host_id, lambda host: stacks.files(host, project))

    return router
