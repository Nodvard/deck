"""Stack löschen: herunterfahren (ohne Volumes), die Übernahme aufheben und nur auf ausdrücklichen Wunsch den Ordner des
Stacks löschen, wenn nichts dagegen spricht."""

from __future__ import annotations

import json
import logging
import posixpath
import re
import shlex
import stat as statmod
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Actor, Risk
from pydantic import BaseModel, ConfigDict

from .adopt import AdoptedStacks
from .applier import RISK_ORDER
from .editor import _SENSITIVE_DIRS, StackEditor
from .image_apply import PROJECT_RE
from .new_stack import DEFAULT_ROOT, NewStacks, root_problem
from .stack_actions import (
    NO_FOLDER_EXIT,
    TIMEOUT_S,
    DockerStackExecutor,
    _broken,
    _connection_error,
    _in_folder,
    _lost,
    busy_text,
    failure_line,
)
from .stacks import DockerStacks, Member, Stack, StackProblem, no_such_file

log = logging.getLogger(__name__)

DELETE_ACTION = "docker.stack_delete"
DOWN_ALL = ("--profile", "*", "down", "--remove-orphans")
"""Auch die Dienste mit Profil und Container, deren Dienst nicht mehr in der Datei steht; nie `-v`."""
RM_TIMEOUT_S = 600
CHECK_TIMEOUT_S = 60
MAX_CONTAINERS = 2000
MAX_TEXT_BYTES = 4 * 1024 * 1024
MOUNTINFO = "/proc/self/mountinfo"
PASSWD = "/etc/passwd"
MOUNTS_FORMAT = '{"name":{{json .Name}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},"mounts":{{json .Mounts}}}'
VOLUMES_STAY = "Die Volumes bleiben; unbenutzte löschst du im Reiter Volumes."
NOT_A_USER = "Einen Stack löscht nur ein Mensch über die Docker-Seite."
STAYS_ADOPTED = "Der Stack bleibt übernommen; ist der Ordner weg, hebe die Übernahme im Reiter Stacks auf."
_ID_RE = re.compile(r"^[0-9a-f]{64}\Z")
_OCTAL_RE = re.compile(r"\\([0-7]{3})")
_DENIED = ("Permission denied", "Operation not permitted")


def delete_action_spec() -> ActionSpec:
    return ActionSpec(
        action_type=DELETE_ACTION, label="Stack löschen", icon="trash", default_risk=Risk.HIGH,
        description="Fährt einen übernommenen Stack herunter (ohne Volumes), hebt die Übernahme auf und löscht auf Wunsch seinen Ordner.",
        permissions=["hosts.execute"],
        # host_bound=False: Stack und Ordner legt immer die Docker-Seite fest.
        host_bound=False,
        confirm_text="Container und Netze des Stacks werden gelöscht, und Nodvard Deck vergisst ihn. Wurde es angehakt, ist auch sein "
        "Ordner mit allen Dateien darin weg. Die Volumes bleiben. Fortfahren?",
    )


def _protected(folder: str) -> bool:
    """Ein Ordner, den Nodvard Deck nie löscht: zu weit oben, ein Systemordner, einer, der einen enthält, oder ein Ort, an
    dem jeder Nutzer schreiben darf."""
    return root_problem(folder) is not None or folder.count("/") < 2 or any(root.startswith(folder + "/") for root in _SENSITIVE_DIRS)


def _inside(path: str, folders: tuple[str, ...]) -> bool:
    return any(path == folder or path.startswith(folder + "/") for folder in folders)


def cmd_take_down(stack: Stack) -> list[str]:
    return _in_folder(stack, *DOWN_ALL)


def cmd_remove_folder(folder: str) -> list[str]:
    """Löscht den Ordner samt Inhalt; Verweisen folgt `rm` nicht, und in eingehängte Laufwerke darin geht es nicht hinein."""
    if _protected(folder):
        raise ValueError(f"Diesen Ordner löscht Nodvard Deck nicht: {folder!r}")
    return ["rm", "-rf", "--one-file-system", "--", folder]


def cmd_rm_probe() -> list[str]:
    """Ändert nichts: GNU `rm` zeigt nur seine Hilfe, ein `rm` ohne `--one-file-system` (BusyBox) bricht mit Fehler ab."""
    return ["rm", "--one-file-system", "--help"]


def cmd_container_ids() -> list[str]:
    return ["docker", "ps", "-aq", "--no-trunc"]


def cmd_container_mounts(ids: list[str]) -> list[str]:
    """Je Container eine JSON-Zeile mit Name, Compose-Projekt und Mounts."""
    if not ids or len(ids) > MAX_CONTAINERS or not all(_ID_RE.match(i) for i in ids):
        raise ValueError("Ungültige Container-IDs")
    return ["docker", "container", "inspect", "--format", MOUNTS_FORMAT, *ids]


def mount_points(text: str) -> list[str]:
    """Die Einhängepunkte aus `/proc/self/mountinfo`; Leerzeichen und Ähnliches stehen dort als `\\040`."""
    points = []
    for line in text.splitlines():
        fields = line.split(" ")
        if len(fields) > 4:
            points.append(_OCTAL_RE.sub(lambda m: chr(int(m.group(1), 8)), fields[4]))
    return points


def home_dirs(text: str) -> dict[str, str]:
    """Heimatordner -> Nutzer aus `/etc/passwd`."""
    homes: dict[str, str] = {}
    for line in text.splitlines():
        fields = line.split(":")
        if len(fields) >= 7 and fields[5].startswith("/") and fields[5] != "/":
            homes.setdefault(posixpath.normpath(fields[5]), fields[0])
    return homes


async def _lstat(sftp: Any, path: str) -> Any | None:
    """Die Angaben zu `path` ohne Verweisen zu folgen, `None`, wenn es ihn nicht gibt."""
    try:
        return await sftp.lstat(path)
    except Exception as exc:
        if no_such_file(exc):
            return None
        raise


async def _read_text(sftp: Any, path: str) -> str:
    async with sftp.open(path, "rb") as handle:
        data = await handle.read(MAX_TEXT_BYTES + 1)
    if len(data) > MAX_TEXT_BYTES:
        raise OSError(f"{path} ist zu groß")
    return data.decode("utf-8", errors="replace")


class StackDelete:
    """Prüft, ob sich der Ordner eines Stacks löschen lässt; gelöscht wird nur im Ausführer."""

    def __init__(self, ctx: Any, stacks: DockerStacks, adopted: AdoptedStacks, new: NewStacks, actions: DockerStackExecutor) -> None:
        self._ctx = ctx
        self.stacks = stacks
        self._adopted = adopted
        self._new = new
        self.actions = actions

    async def folder_refusal(self, host: Any, stack: Stack) -> str | None:
        """Warum der Ordner dieses Stacks bleibt, oder `None`."""
        folder = stack.working_dir
        keep = f"Den Ordner {folder} löscht Nodvard Deck nicht"
        if _protected(folder):
            return f"{keep}: Er liegt zu weit oben oder an einem Ort, an dem Nodvard Deck nichts löscht."
        if not stack.files or not all(path.startswith(folder + "/") for path in (*stack.files, *stack.env_files)):
            return f"{keep}: Die Dateien des Stacks liegen nicht alle darin, er gehört also nicht nur zu diesem Stack."
        if folder in (await self._new.root(host.id), DEFAULT_ROOT):
            return f"{keep}: Das ist der Ordner für neue Stacks."
        try:
            running = await self.stacks.stacks(host)
        except StackProblem as exc:
            return f"{keep}: Die anderen Stacks des Servers ließen sich nicht prüfen. {exc}"
        try:
            async with self._ctx.exec.sftp(host) as sftp:
                attrs = await _lstat(sftp, folder)
                if attrs is None:
                    return f"Den Ordner {folder} gibt es nicht mehr."
                real = str(await sftp.realpath(folder))
                home = str(await sftp.realpath("."))
                points = mount_points(await _read_text(sftp, MOUNTINFO))
                homes = home_dirs(await _read_text(sftp, PASSWD))
        except Exception as exc:  # noqa: BLE001 - was sich nicht prüfen lässt, wird nicht gelöscht
            # Ein Rechtefehler bringt einen eigenen Satz mit; die SSH-Bibliothek nur englische Kurzformen.
            why = _lost(exc) if _connection_error(exc) and not isinstance(exc, OSError) else str(exc) or type(exc).__name__
            return f"{keep}: Er ließ sich auf dem Server nicht prüfen ({why})."
        if attrs.permissions is None or not statmod.S_ISDIR(attrs.permissions):
            return f"{keep}: Er ist ein Verweis auf einen anderen Ort oder kein Ordner."
        if real != folder and _protected(real):
            return f"{keep}: Er führt über einen Verweis nach {real}, und dort löscht Nodvard Deck nichts."
        paths = (folder, real) if real != folder else (folder,)
        others = [entry.working_dir for project, entry in self._adopted.for_host(host.id).items() if project != stack.project]
        others += [s.working_dir for s in running if s.project != stack.project and s.working_dir]
        if any(_inside(other, paths) for other in others):
            return f"{keep}: Darin liegt noch ein anderer Stack."
        if _inside(home, paths):
            return f"{keep}: Darin liegt der Heimatordner des Zugangs von Nodvard Deck."
        for user_home, user in homes.items():
            if _inside(user_home, paths):
                return f"{keep}: Darin liegt der Heimatordner von „{user}“ ({user_home})."
        for point in points:
            if _inside(point, paths):
                return f"{keep}: Unter {point} ist ein Laufwerk oder eine Freigabe eingehängt. Hänge es zuerst aus oder lösche den Ordner selbst."
        if (used := await self._used_elsewhere(host, stack.project, paths)) is not None:
            return f"{keep}: {used}"
        try:
            probe = await self._ctx.exec.run_argv(host, cmd_rm_probe(), timeout_s=CHECK_TIMEOUT_S)
        except Exception as exc:
            if not _connection_error(exc):
                raise
            return f"{keep}: Er ließ sich auf dem Server nicht prüfen ({_lost(exc)})."
        if probe.exit_code != 0:
            return (f"{keep}: rm auf dem Server kennt die Option --one-file-system nicht (etwa bei BusyBox), ohne sie könnte es in "
                    "eingehängte Laufwerke hinein löschen. Lösche den Stack ohne Ordner und den Ordner danach selbst.")
        return None

    async def _used_elsewhere(self, host: Any, project: str, paths: tuple[str, ...]) -> str | None:
        """Ob ein Container außerhalb des Stacks etwas aus dem Ordner einbindet, auch Nodvard Deck selbst."""
        unreadable = "Die Container des Servers ließen sich nicht prüfen."
        try:
            listed = await self._ctx.exec.run_argv(host, cmd_container_ids(), timeout_s=CHECK_TIMEOUT_S)
            ids = listed.stdout.split()
            if listed.exit_code != 0 or len(ids) > MAX_CONTAINERS or not all(_ID_RE.match(i) for i in ids):
                return unreadable
            if not ids:
                return None
            inspected = await self._ctx.exec.run_argv(host, cmd_container_mounts(ids), timeout_s=CHECK_TIMEOUT_S)
        except Exception as exc:
            if not _connection_error(exc):
                raise
            return f"Die Container des Servers ließen sich nicht prüfen ({_lost(exc)})."
        rows = []
        for line in inspected.stdout.splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        if inspected.exit_code != 0 or len(rows) != len(ids) or not all(isinstance(r, dict) for r in rows):
            return unreadable
        for row in rows:
            if row.get("project") == project:
                continue
            for mount in row.get("mounts") or []:
                source = mount.get("Source") if isinstance(mount, dict) else None
                if isinstance(source, str) and _inside(source, paths):
                    name = str(row.get("name") or "?").lstrip("/")
                    return f"Der Container „{name}“ gehört nicht zu diesem Stack und nutzt {source} daraus."
        return None


async def _gone(ctx: Any, host: Any, folder: str) -> bool | None:
    """Ist der Ordner weg? `None`, wenn sich das nicht feststellen ließ."""
    try:
        async with ctx.exec.sftp(host) as sftp:
            return await _lstat(sftp, folder) is None
    except Exception:  # noqa: BLE001 - dann bleibt es offen
        return None


class DockerStackDeleteExecutor:
    """Fährt herunter, löscht, wenn angehakt, den Ordner und vergisst den Stack erst, wenn das geklappt hat."""

    action_types = frozenset({DELETE_ACTION})

    def __init__(
        self, ctx: Any, delete: StackDelete, adopted: AdoptedStacks,
        drop_backups: Callable[[Any, str, Iterable[str]], Awaitable[None]] | None = None,
    ) -> None:
        self._ctx = ctx
        self._delete = delete
        self._adopted = adopted
        self._drop_backups = drop_backups

    async def execute(self, req: ActionRequest) -> ActionResult:
        payload = req.payload or {}
        if getattr(req.proposed_by.type, "value", req.proposed_by.type) != "user":
            return ActionResult(success=False, error=NOT_A_USER)
        if RISK_ORDER.index(req.risk) < RISK_ORDER.index(Risk.HIGH):
            return ActionResult(success=False, error="Einen Stack zu löschen braucht eine Freigabe mit hohem Risiko.")
        target = await self._delete.actions.target(req.host_ref, str(payload.get("project") or ""))
        if isinstance(target, str):
            return ActionResult(success=False, error=target)
        host, stack = target
        remove = payload.get("remove_folder") is True
        if remove:
            if payload.get("folder") != stack.working_dir:
                return ActionResult(success=False, error="Der Ordner des Stacks hat sich inzwischen geändert. Bitte noch einmal löschen.")
            if (refusal := await self._delete.folder_refusal(host, stack)) is not None:
                return ActionResult(success=False, error=f"{refusal} Gelöscht wurde nichts.")
        with self._delete.actions.locks.hold(host.id, stack.project, "Löschen") as busy:
            if busy is not None:
                return ActionResult(success=False, error=busy_text(busy))
            return await self._run(host, stack, remove)

    async def _left(self, host: Any, project: str) -> list[Member] | str:
        """Die Container des Projekts, auch gestoppte, oder warum sie sich nicht lesen ließen."""
        try:
            found = await self._delete.stacks.stacks(host, project)
        except StackProblem as exc:
            return f"Die Container des Stacks ließen sich nicht lesen. {exc}"
        return [m for s in found if s.project == project for m in s.members]

    async def _take_down(self, host: Any, stack: Stack) -> str | None:
        """Fährt den Stack mit allen Profilen herunter; der Grund, wenn das scheitert."""
        timeout_s = TIMEOUT_S["docker.stack_down"]
        try:
            result = await self._ctx.exec.run_argv(host, cmd_take_down(stack), timeout_s=timeout_s)
        except Exception as exc:
            if not _connection_error(exc):
                raise
            return f"Herunterfahren ist abgebrochen: {_broken(exc, 'docker.stack_down', timeout_s)}"
        if result.exit_code == NO_FOLDER_EXIT:
            return (f"Den Ordner {stack.working_dir} gibt es auf dem Server nicht mehr, oder er ist nicht lesbar, deshalb ließ sich der "
                    "Stack nicht herunterfahren.")
        if result.exit_code != 0:
            line = failure_line(result.stderr or result.stdout)
            return f"Herunterfahren ist gescheitert: {line}{'' if line.endswith('.') else '.'}"
        return None

    async def _run(self, host: Any, stack: Stack, remove: bool) -> ActionResult:
        started = time.monotonic()
        folder = stack.working_dir
        nothing = "Nodvard Deck hat den Stack nicht vergessen" + (" und den Ordner nicht gelöscht." if remove else ".")

        def failed(error: str, output: str | None = None) -> ActionResult:
            return ActionResult(success=False, exit_code=0, output=output, error=error, duration_ms=int((time.monotonic() - started) * 1000))

        async def forget(rest: str) -> ActionResult:
            try:
                await self._adopted.remove(host.id, stack.project)
            except Exception as exc:  # noqa: BLE001 - was schon gelöscht ist, bleibt gelöscht
                return failed(f"Nodvard Deck konnte den Stack „{stack.project}“ nicht vergessen ({exc}). {rest} Hebe die Übernahme "
                              "im Reiter Stacks auf.", output=down)
            if self._drop_backups is not None:
                try:
                    await self._drop_backups(host, stack.project, services)
                except Exception:  # Der Stack ist gelöscht; übrig bleiben höchstens Namen.
                    log.warning("docker_stack_backups_not_dropped project=%s", stack.project, exc_info=True)
            return ActionResult(success=True, exit_code=0, output=f"{done} {rest}", duration_ms=int((time.monotonic() - started) * 1000))

        left = await self._left(host, stack.project)
        if isinstance(left, str):
            return failed(f"{left} {nothing}")
        took_down = bool(left)
        services = {m.service for m in left if m.service}
        if took_down:
            if (problem := await self._take_down(host, stack)) is not None:
                return failed(f"{problem} {nothing}")
            left = await self._left(host, stack.project)
            if isinstance(left, str):
                return failed(f"Ob der Stack ganz heruntergefahren ist, ließ sich nicht prüfen. {left} {nothing}")
            if left:
                return failed(f"Nach dem Herunterfahren sind noch Container des Stacks da: {', '.join(m.name for m in left[:5])}. {nothing}")
        down = f"Stack „{stack.project}“ heruntergefahren." if took_down else None
        done = f"Stack „{stack.project}“ {'heruntergefahren und ' if took_down else ''}gelöscht."
        if not remove:
            return await forget(f"Der Ordner {folder} mit der Compose-Datei bleibt. {VOLUMES_STAY}")
        if (refusal := await self._delete.folder_refusal(host, stack)) is not None:
            return failed(f"{refusal} Der Stack bleibt übernommen; ohne Ordner kannst du ihn noch einmal löschen.", output=down)
        denied = False
        try:
            result = await self._ctx.exec.run_argv(host, cmd_remove_folder(folder), timeout_s=RM_TIMEOUT_S)
            reason = failure_line(result.stderr or result.stdout) if result.exit_code != 0 else None
            denied = result.exit_code != 0 and any(word in (result.stderr or "") for word in _DENIED)
        except Exception as exc:
            if not _connection_error(exc):
                raise
            reason = (f"nach {RM_TIMEOUT_S // 60} Minuten ohne Ende abgebrochen" if type(exc) is TimeoutError else _lost(exc))
            reason += ", rm kann auf dem Server noch weiterlaufen"
        gone = await _gone(self._ctx, host, folder)
        if gone is True:
            return await forget(f"Der Ordner {folder} ist gelöscht. {VOLUMES_STAY}")
        if gone is None:
            return failed(f"Ob der Ordner {folder} ganz gelöscht ist, ließ sich nicht prüfen{f' ({reason})' if reason else ''}. "
                          f"Sieh im Dateimanager nach. {STAYS_ADOPTED}", output=down)
        root = (f" Übrige Dateien gehören meist root, weil ein Container sie angelegt hat; lösche den Rest im Terminal mit „sudo rm -rf "
                f"--one-file-system -- {shlex.quote(folder)}“." if denied else "")
        return failed(f"Der Ordner {folder} ließ sich nicht ganz löschen{f': {reason}' if reason else ''}.{root} {STAYS_ADOPTED}", output=down)

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        payload = req.payload or {}
        folder = f" und den Ordner {payload.get('folder', '?')} löschen" if payload.get("remove_folder") is True else ""
        return DryRunReport(would_change=True, summary=f"Würde den Stack „{payload.get('project', '?')}“ herunterfahren (ohne Volumes){folder}.")


class _DeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    remove_folder: bool = False


def delete_routes(delete: StackDelete, editor: StackEditor, docker_host: Any, current_actor: Any) -> APIRouter:
    router = APIRouter()

    async def _target(host_id: str, project: str) -> tuple[Any, Stack]:
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        host = await docker_host(host_id)
        target = await delete.actions.target(host.id, project)
        if isinstance(target, str):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=target)
        return target

    @router.get("/hosts/{host_id}/stacks/{project}/delete")
    async def delete_check(host_id: str, project: str) -> dict:
        """Ob sich mit dem Stack auch sein Ordner löschen ließe, und wenn nicht, warum."""
        host, stack = await _target(host_id, project)
        return {"folder": stack.working_dir, "folder_refusal": await delete.folder_refusal(host, stack)}

    @router.post("/hosts/{host_id}/stacks/{project}/delete", status_code=status.HTTP_202_ACCEPTED)
    async def delete_stack(host_id: str, project: str, body: _DeleteIn, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt das Löschen vor (Gate-Aktion `docker.stack_delete`, immer mit hohem Risiko)."""
        host, stack = await _target(host_id, project)
        if body.remove_folder and (refusal := await delete.folder_refusal(host, stack)) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal)
        folder = f" und den Ordner {stack.working_dir} mit allen Dateien darin löschen" if body.remove_folder else ""
        reason = f"Stack „{project}“ löschen: herunterfahren, Container und Netze weg, Volumes bleiben{folder}."
        decision = await editor.propose(ActionRequest(
            action_type=DELETE_ACTION, host_ref=host.id, risk=Risk.HIGH, proposed_by=actor, reason=reason,
            payload={"project": project, "remove_folder": body.remove_folder, "folder": stack.working_dir},
        ))
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": Risk.HIGH.value, "detail": decision.detail}

    return router
