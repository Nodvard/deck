"""Stacks übernehmen: Nodvard Deck merkt sich Ordner und Dateien eines vorhandenen Compose-Projekts, am Server ändert sich
dabei nichts und kein Container wird neu erstellt."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from .capabilities import is_own_container
from .image_apply import MAX_FILES, PROJECT_RE, _valid_path, is_self_image
from .image_updates import _write_atomic
from .stacks import NO_FILE, DockerStacks, Stack, StackProblem, compose_argv, first_reason, read_file

log = logging.getLogger(__name__)

STORE_FILE = "stacks.json"
VALIDATE_TIMEOUT_S = 30
NO_COMPOSE_V2 = "Auf dem Server fehlt Docker Compose v2, also der Befehl „docker compose“."
PORTAINER = (
    "Diesen Stack verwaltet Portainer: Seine Datei und seine Umgebungsvariablen liegen in Portainers eigenem Speicher. "
    "Bearbeite ihn weiter in Portainer."
)
COMPOSE_V1 = (
    "Angelegt mit dem alten docker-compose (Version 1). Compose v2 würde die Container beim nächsten Anwenden umbenennen und "
    "neu erstellen. Starte den Stack erst einmal selbst mit „docker compose up -d“ neu."
)
ITSELF = (
    "Das ist Nodvard Deck selbst. Seine Compose-Datei bearbeitest du besser direkt auf dem Server: Ein Fehler darin würde die "
    "Oberfläche abschalten, mit der man ihn beheben müsste."
)


@dataclass(frozen=True)
class Adopted:
    working_dir: str
    config_files: tuple[str, ...]
    env_files: tuple[str, ...]
    adopted_at: str

    def stack(self, project: str) -> Stack:
        return Stack(project, [], self.working_dir, self.config_files, self.env_files, "compose")

    def note(self, stack: Stack) -> str | None:
        """Warum die Übernahme nicht mehr zum laufenden Stack passt, oder `None`."""
        if not stack.members or (stack.working_dir, stack.files, stack.env_files) == (self.working_dir, self.config_files, self.env_files):
            return None
        return (
            f"Übernommen aus {self.working_dir}, der Stack läuft inzwischen aber mit anderen Dateien. Übernimm ihn neu, "
            "damit Nodvard Deck die richtigen kennt."
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "working_dir": self.working_dir, "config_files": list(self.config_files), "env_files": list(self.env_files),
            "adopted_at": self.adopted_at,
        }


def _entry(raw: Any) -> Adopted | None:
    """Ein Eintrag aus der Datei; was nicht passt (etwa von Hand verändert), fällt weg."""
    if not isinstance(raw, dict):
        return None
    working_dir, files, env_files = raw.get("working_dir"), raw.get("config_files"), raw.get("env_files", [])
    if not isinstance(files, list) or not isinstance(env_files, list) or not 1 <= len(files) <= MAX_FILES or len(env_files) > MAX_FILES:
        return None
    if not all(isinstance(p, str) and _valid_path(p) for p in (working_dir, *files, *env_files)):
        return None
    return Adopted(working_dir, tuple(files), tuple(env_files), str(raw.get("adopted_at") or ""))


class StoreProblem(Exception):
    """Die Merkliste ließ sich gerade nicht lesen oder schreiben; der Text ist für Menschen."""


class AdoptedStacks:
    """Die übernommenen Stacks je Server, als JSON im Datenordner des Moduls."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / STORE_FILE
        self._lock = asyncio.Lock()

    def _read(self, *, before_write: bool = False) -> dict[str, dict[str, Adopted]]:
        """Die Merkliste; eine kaputte Datei gilt als leer und wird vor dem nächsten Schreiben beiseitegelegt
        (`stacks.json.kaputt-<Zeit>`), eine gerade nicht lesbare bricht das Schreiben ab."""
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            log.warning("docker_stacks_store_unreadable path=%s error=%s", self._path, exc)
            if before_write:
                raise StoreProblem("Die Liste der übernommenen Stacks ließ sich gerade nicht lesen. Bitte später noch einmal.") from exc
            return {}
        try:
            raw = json.loads(text).get("adopted")
            if not isinstance(raw, dict):
                raise ValueError("adopted ist keine Zuordnung")
        except (ValueError, AttributeError):
            if before_write:
                self._set_aside()
            else:
                log.warning("docker_stacks_store_broken path=%s (wird ignoriert)", self._path)
            return {}
        out: dict[str, dict[str, Adopted]] = {}
        for host_id, projects in raw.items():
            if not isinstance(projects, dict):
                continue
            entries = {p: e for p, e in ((p, _entry(v)) for p, v in projects.items()) if isinstance(p, str) and PROJECT_RE.match(p) and e}
            if entries:
                out[str(host_id)] = entries
        return out

    def _set_aside(self) -> None:
        aside = self._path.with_name(f"{self._path.name}.kaputt-{datetime.now(timezone.utc):%Y%m%d%H%M%S}")
        try:
            os.replace(self._path, aside)
        except OSError as exc:
            raise StoreProblem("Die kaputte Liste der übernommenen Stacks ließ sich nicht beiseitelegen.") from exc
        log.warning("docker_stacks_store_broken path=%s beiseitegelegt=%s", self._path, aside.name)

    def _write(self, data: dict[str, dict[str, Adopted]]) -> None:
        plain = {h: {p: e.to_json() for p, e in projects.items()} for h, projects in data.items() if projects}
        try:
            _write_atomic(self._path, json.dumps({"adopted": plain}, indent=1, sort_keys=True))
        except OSError as exc:
            raise StoreProblem(
                f"Die Liste der übernommenen Stacks ließ sich nicht speichern ({exc.strerror or type(exc).__name__}). "
                "Ist der Datenordner von Nodvard Deck voll oder schreibgeschützt?"
            ) from exc

    def for_host(self, host_id: str) -> dict[str, Adopted]:
        return self._read().get(host_id, {})

    async def add(self, host_id: str, stack: Stack) -> Adopted:
        if stack.files is None:
            raise ValueError(f"Stack ohne lesbare Dateien: {stack.project!r}")
        entry = Adopted(stack.working_dir, stack.files, stack.env_files, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        async with self._lock:
            data = self._read(before_write=True)
            data.setdefault(host_id, {})[stack.project] = entry
            self._write(data)
        return entry

    async def remove(self, host_id: str, project: str) -> bool:
        async with self._lock:
            data = self._read(before_write=True)
            if data.get(host_id, {}).pop(project, None) is None:
                return False
            self._write(data)
        return True


def cmd_validate(stack: Stack) -> str:
    """`docker compose config --quiet` im Ordner des Stacks: liest Compose v2 die Datei ohne Fehler?"""
    return shlex.join([
        "sh", "-c", f"cd -- {shlex.quote(stack.working_dir)} && {shlex.join(compose_argv(stack, 'config', '--quiet'))} 2>&1",
    ])


async def problems(ctx: Any, stacks: DockerStacks, host: Any, stack: Stack) -> list[str]:
    """Gründe, warum sich der Stack nicht übernehmen lässt; leer heißt: es geht."""
    if any(is_own_container(m.id) or is_self_image(m.image) for m in stack.members):
        return [ITSELF]
    if stack.source == "portainer":
        return [PORTAINER]
    if stack.files is None:
        return [NO_FILE]
    if stack.version.startswith("1."):
        return [COMPOSE_V1]
    reasons = []
    try:
        async with ctx.exec.sftp(host) as sftp:
            files = [await read_file(sftp, path) for path in stack.files]
    except Exception as exc:  # noqa: BLE001 - als Grund zeigen, nicht als 500
        raise StackProblem(f"Dateien nicht lesbar: {str(exc) or type(exc).__name__}") from exc
    reasons += [f"{f['path']}: {f['error']}" for f in files if f["error"]]
    if reasons:
        return reasons
    result = await stacks._exec(host, cmd_validate(stack), VALIDATE_TIMEOUT_S)
    if result.exit_code != 0:
        output = result.stdout.splitlines()
        if any("is not a docker command" in line or "unknown command" in line for line in output):
            return [NO_COMPOSE_V2]
        return [f"Docker Compose kann die Datei nicht lesen: {first_reason(output)}"]
    return []


class _AdoptIn(BaseModel):
    """Die Pfade, die die Prüfung gezeigt hat; übernommen wird nur, wenn der Stack noch genau so läuft."""

    model_config = ConfigDict(extra="forbid")

    working_dir: str = Field(max_length=1024)
    config_files: list[Annotated[str, Field(max_length=1024)]] = Field(max_length=MAX_FILES)
    env_files: list[Annotated[str, Field(max_length=1024)]] = Field(default_factory=list, max_length=MAX_FILES)


def adopt_routes(
    ctx: Any, stacks: DockerStacks, adopted: AdoptedStacks, docker_host: Callable[[str], Awaitable[Any]],
) -> APIRouter:
    router = APIRouter()

    async def _stack(host: Any, project: str) -> Stack:
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        try:
            found = [s for s in await stacks.stacks(host, project) if s.project == project]
        except StackProblem as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        if not found:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Auf diesem Server läuft kein Stack „{project}“.")
        return found[0]

    async def _problems(host: Any, stack: Stack) -> list[str]:
        try:
            return await problems(ctx, stacks, host, stack)
        except StackProblem as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    @router.get("/hosts/{host_id}/stacks/{project}/adopt")
    async def adopt_check(host_id: str, project: str) -> dict:
        """Lässt sich der Stack übernehmen? Nur lesend: Gründe dagegen, Ordner und Dateien."""
        host = await docker_host(host_id)
        stack = await _stack(host, project)
        reasons = await _problems(host, stack)
        return {
            "project": project, "ok": not reasons, "reasons": reasons, "working_dir": stack.working_dir or None,
            "config_files": list(stack.files or ()), "env_files": list(stack.env_files),
            "adopted": project in adopted.for_host(host.id),
        }

    @router.post("/hosts/{host_id}/stacks/{project}/adopt")
    async def adopt(host_id: str, project: str, body: _AdoptIn, actor: Any = Depends(ctx.api.current_actor)) -> dict:
        """Übernimmt den Stack nach derselben Prüfung und nur mit den Pfaden, die sie gezeigt hat; am Server ändert sich nichts."""
        host = await docker_host(host_id)
        stack = await _stack(host, project)
        if (stack.working_dir, list(stack.files or ()), list(stack.env_files)) != (body.working_dir, body.config_files, body.env_files):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Der Stack läuft inzwischen mit anderen Dateien als bei der Prüfung. Bitte die Prüfung neu laden.",
            )
        reasons = await _problems(host, stack)
        if reasons:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=" ".join(reasons))
        try:
            entry = await adopted.add(host.id, stack)
        except StoreProblem as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        await _audit(ctx, "docker.stack_adopt", host.id, project, actor, {"working_dir": entry.working_dir, "config_files": list(entry.config_files)})
        return {"project": project, "adopted": True, **entry.to_json()}

    @router.delete("/hosts/{host_id}/stacks/{project}/adopt")
    async def release(host_id: str, project: str, actor: Any = Depends(ctx.api.current_actor)) -> dict:
        """Vergisst die Übernahme; Stack, Datei und Container bleiben, wie sie sind."""
        host = await docker_host(host_id)
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        try:
            removed = await adopted.remove(host.id, project)
        except StoreProblem as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        if not removed:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Der Stack „{project}“ ist nicht übernommen.")
        await _audit(ctx, "docker.stack_release", host.id, project, actor, {})
        return {"project": project, "adopted": False}

    return router


async def _audit(ctx: Any, action: str, host_id: str, project: str, actor: Any, detail: dict[str, Any]) -> None:
    try:
        await ctx.audit.log(
            action=action, outcome="success", target_type="stack", target_id=f"{host_id}:{project}", detail=detail, actor=actor,
        )
    except Exception:  # noqa: BLE001 - die Übernahme ist gespeichert, auch wenn das Protokoll scheitert
        log.exception("docker_stack_audit_failed action=%s", action)
