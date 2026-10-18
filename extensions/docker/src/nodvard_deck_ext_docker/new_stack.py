"""Neue Stacks anlegen: ein eigener Ordner unter dem Ordner für neue Stacks des Servers, darin `compose.yaml`, danach gilt
der Stack als übernommen. Angelegt wird nur über das Gate, und ein bestehender Ordner wird nie überschrieben."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import posixpath
import shlex
import stat as statmod
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Actor, Risk
from pydantic import BaseModel, ConfigDict, Field

from .adopt import AdoptedStacks
from .applier import RISK_ORDER
from .editor import (
    _SENSITIVE_DIRS,
    _SFTP_PERMISSION_DENIED,
    NOT_A_USER,
    Checked,
    StackEditor,
    _describe,
    _sensitive,
    parse_check,
    sha256,
)
from .image_apply import PROJECT_RE, _valid_path
from .stack_actions import IN_FOLDER, DockerStackExecutor, busy_text
from .stacks import (
    MAX_FILE_BYTES,
    DockerStacks,
    Stack,
    StackProblem,
    compose_argv,
    no_such_file,
)

log = logging.getLogger(__name__)

CREATE_ACTION = "docker.stack_create"
DEFAULT_ROOT = "/opt/stacks"
COMPOSE_NAME = "compose.yaml"
NEW_FILE_MODE = 0o600
NEW_DIR_MODE = 0o755
MAX_ROOTS = 200
RISK_TOO_LOW = "Die Datei enthält riskante Einstellungen; dafür braucht es eine Freigabe mit hohem Risiko. Bitte noch einmal anlegen."
# Dort kann jeder Nutzer des Servers Ordner anlegen und austauschen.
_SHARED_ROOTS = ("/tmp", "/var/tmp", "/dev/shm", "/run/user")


def create_action_spec() -> ActionSpec:
    return ActionSpec(
        action_type=CREATE_ACTION, label="Stack anlegen", icon="plus", default_risk=Risk.MEDIUM,
        description="Legt auf dem Server einen Ordner für einen neuen Stack an, schreibt seine Compose-Datei und übernimmt ihn.",
        permissions=["hosts.execute"],
        # host_bound=False: Name, Ordner und Inhalt legt immer die Docker-Seite fest.
        host_bound=False,
        confirm_text="Auf dem Server entsteht ein neuer Ordner mit der Compose-Datei des Stacks. Fortfahren?",
    )


def root_problem(path: Any) -> str | None:
    """Warum dieser Ordner nicht für neue Stacks taugt, oder `None`."""
    if not isinstance(path, str) or not path.startswith("/"):
        return "Bitte einen absoluten Ordner angeben, der mit / beginnt, etwa /opt/stacks."
    if path == "/":
        return "Der oberste Ordner / taugt nicht für neue Stacks."
    if not _valid_path(path) or posixpath.normpath(path) != path:
        return "Erlaubt sind Buchstaben ohne Umlaute, Ziffern, Leerzeichen und . _ + @ = -, ohne / am Ende und ohne .. im Pfad."
    # Strenger als bei den Warnungen im Editor: auch harmlose Geräte wie /dev/dri taugen nicht als Ordner.
    if _sensitive(path) or any(path == d or path.startswith(d + "/") for d in _SENSITIVE_DIRS):
        return f"{path} ist ein Systemordner oder liegt darin. Nimm etwa /opt/stacks oder einen Ordner unter /home."
    if any(path == shared or path.startswith(shared + "/") for shared in _SHARED_ROOTS):
        return f"In {path} kann jeder Nutzer des Servers Ordner anlegen und austauschen. Nimm einen eigenen Ordner, etwa /opt/stacks."
    return None


def valid_root(path: Any) -> bool:
    return root_problem(path) is None


def _setting(raw: Any) -> Any:
    # Ein / am Ende ist beim Eintippen leicht passiert und ändert nichts am Ordner.
    return raw.rstrip("/") if isinstance(raw, str) and len(raw) > 1 else raw


def root_setting(settings: dict[str, Any], host_id: str) -> tuple[str, str | None]:
    """Der Ordner für neue Stacks (je Server, sonst die Vorgabe des Moduls, sonst `/opt/stacks`) und, wenn eine
    Einstellung dabei übergangen wurde, warum."""
    roots = settings.get("stack_roots") if isinstance(settings.get("stack_roots"), dict) else {}
    skipped = None
    for raw, where in ((roots.get(host_id), "für diesen Server"), (settings.get("stack_root"), "in den Einstellungen des Moduls")):
        if raw in (None, ""):
            continue
        if (problem := root_problem(_setting(raw))) is None:
            root = _setting(raw)
            break
        skipped = skipped or (raw, where, problem)
    else:
        root = DEFAULT_ROOT
    if skipped is None:
        return root, None
    raw, where, problem = skipped
    return root, f"Der eingestellte Ordner {raw} {where} taugt nicht, deshalb gilt {root}. {problem}"


def stack_root(settings: dict[str, Any], host_id: str) -> str:
    return root_setting(settings, host_id)[0]


def new_stack(name: str, root: str) -> Stack:
    folder = posixpath.join(root, name)
    return Stack(name, [], folder, (posixpath.join(folder, COMPOSE_NAME),), (), "compose")


def cmd_new_check(stack: Stack, cwd: str) -> list[str]:
    """Prüft den Entwurf, bevor es den Ordner gibt: in `cwd` (dem Ordner darüber), mit dem künftigen Ordner als Projektordner
    und als `${PWD}`, damit Pfade in der Datei dasselbe ergeben wie später im Ordner."""
    argv = compose_argv(stack, "--profile", "*", "config", "--format", "json", stdin_index=0)
    return ["sh", "-c", IN_FOLDER, "sh", cwd, "env", f"PWD={stack.working_dir}", *argv]


async def _exists(sftp: Any, path: str) -> Any | None:
    """Die Angaben zu `path`, ohne Verweisen zu folgen; `None` nur, wenn es ihn sicher nicht gibt."""
    try:
        return await sftp.lstat(path)
    except Exception as exc:
        if no_such_file(exc):
            return None
        raise


async def _attrs_or_none(sftp: Any, path: str) -> Any | None:
    """Wie `_exists`, aber ohne eigenen Fehler: Wer schon einen Grund hat, soll ihn behalten."""
    try:
        return await _exists(sftp, path)
    except Exception:  # noqa: BLE001 - der erste Fehler zählt
        return None


def _is_dir(attrs: Any) -> bool:
    return attrs is not None and (attrs.permissions is None or statmod.S_ISDIR(attrs.permissions))


def _kind(attrs: Any) -> str:
    if attrs is None:
        return "missing"
    if _is_dir(attrs):
        return "dir"
    return "link" if statmod.S_ISLNK(attrs.permissions) else "file"


async def _root_refusal(sftp: Any, root: str, attrs: Any) -> str | None:
    """Warum in diesem vorhandenen Ordner keine Stacks entstehen, oder `None`."""
    kind = _kind(attrs)
    if kind == "link":
        target = None
        with contextlib.suppress(Exception):
            target = str(await sftp.realpath(root))
        where = f" Trag den echten Ordner ein: {target}." if target and target != root else ""
        return f"{root} ist ein Verweis auf einen anderen Ordner.{where}"
    if kind == "file":
        return f"{root} ist kein Ordner, sondern eine Datei. Wähle einen anderen Ordner für neue Stacks."
    if attrs is not None and attrs.permissions is not None and attrs.permissions & 0o002:
        return f"In {root} darf jeder Nutzer des Servers schreiben. Dort legt Nodvard Deck keine Stacks an; nimm einen eigenen Ordner."
    return None


class NewStacks:
    """Vorgaben und Prüfungen für neue Stacks; angelegt wird nur im Ausführer."""

    def __init__(self, ctx: Any, editor: StackEditor, stacks: DockerStacks, adopted: AdoptedStacks, settings_lock: asyncio.Lock) -> None:
        self._ctx = ctx
        self.editor = editor
        self._stacks = stacks
        self._adopted = adopted
        self._lock = settings_lock

    async def root(self, host_id: str) -> str:
        return stack_root(await self._ctx.settings.get(), host_id)

    async def set_root(self, host_id: str, path: str | None, actor: Any = None) -> str:
        root = await self._store_root(host_id, path)
        try:
            await self._ctx.audit.log(
                action="docker.stack_root", outcome="success", target_type="host", target_id=host_id,
                detail={"root": path}, actor=actor,
            )
        except Exception:  # gespeichert ist gespeichert, nur der Eintrag fehlt
            log.exception("docker_stack_root_audit_failed host=%s", host_id)
        return root

    async def _store_root(self, host_id: str, path: str | None) -> str:
        async with self._lock:
            settings = await self._ctx.settings.get()
            roots = dict(settings.get("stack_roots") or {}) if isinstance(settings.get("stack_roots"), dict) else {}
            if path is None:
                roots.pop(host_id, None)
            else:
                if host_id not in roots and len(roots) >= MAX_ROOTS:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Es sind schon {MAX_ROOTS} Server mit eigenem Ordner eingetragen.")
                roots[host_id] = path
            await self._ctx.settings.set({**settings, "stack_roots": roots})
            return stack_root({**settings, "stack_roots": roots}, host_id)

    async def place(self, host: Any) -> dict[str, Any]:
        """Wohin neue Stacks kommen, was dort gerade liegt und wo der Zugang zu Hause ist (für einen Vorschlag)."""
        settings = await self._ctx.settings.get()
        root, setting_problem = root_setting(settings, host.id)
        state, refusal, home = "unknown", None, None
        with contextlib.suppress(Exception):  # ohne Verbindung bleiben die Angaben offen
            async with self._ctx.exec.sftp(host) as sftp:
                with contextlib.suppress(Exception):
                    home = str(await sftp.realpath("."))
                attrs = await _exists(sftp, root)
                state = _kind(attrs)
                refusal = await _root_refusal(sftp, root, attrs)
        return {
            "root": root, "default": stack_root({**settings, "stack_roots": {}}, host.id), "root_state": state,
            "root_problem": refusal, "setting_problem": setting_problem,
            "home": home if home and valid_root(f"{home}/stacks") else None,
        }

    async def conflict(self, host: Any, name: str, root: str) -> str | None:
        """Warum es diesen Stack nicht neu geben kann, oder `None`."""
        if name in self._adopted.for_host(host.id):
            return f"Einen Stack „{name}“ hat Nodvard Deck auf diesem Server schon übernommen."
        try:
            if [s for s in await self._stacks.stacks(host, name) if s.project == name]:
                return f"Auf diesem Server gibt es schon einen Stack „{name}“. Nimm einen anderen Namen oder übernimm den bestehenden."
        except StackProblem as exc:
            return str(exc)
        folder = posixpath.join(root, name)
        try:
            async with self._ctx.exec.sftp(host) as sftp:
                path, parent = root, posixpath.dirname(root)
                try:
                    if (refusal := await _root_refusal(sftp, root, await _exists(sftp, root))) is not None:
                        return refusal
                    path, parent = folder, root
                    if await _exists(sftp, folder) is not None:
                        return f"Den Ordner {folder} gibt es schon. Nodvard Deck überschreibt nie einen bestehenden Ordner; nimm einen anderen Namen."
                except Exception as exc:  # noqa: BLE001 - als Grund zeigen
                    return await _unclear(sftp, exc, path, parent)
        except Exception as exc:  # noqa: BLE001 - als Grund zeigen
            return f"Server nicht erreichbar: {str(exc) or type(exc).__name__}"
        return None

    async def check(self, host: Any, stack: Stack, root: str, content: bytes) -> Checked:
        """Vorab-Prüfung; fehlt der Ordner für neue Stacks noch, läuft sie in `/`. Verbindlich prüft der Ausführer im neuen Ordner."""
        try:
            for cwd in (root, "/"):
                result = await self._ctx.exec.run_argv(host, cmd_new_check(stack, cwd), stdin=content, timeout_s=60)
                if result.exit_code != 97:
                    break
        except OSError as exc:
            if type(exc) is TimeoutError:
                return Checked(unverified="Compose hat nicht innerhalb von 60 Sekunden geantwortet.")
            return Checked(unverified=f"Server nicht erreichbar: {str(exc) or type(exc).__name__}")
        checked = parse_check(result.exit_code, result.stdout, result.stderr, COMPOSE_NAME)
        if checked.error and f"{stack.working_dir}/" in checked.error and "not found" in checked.error:
            note = (
                " Der Ordner des Stacks ist neu und leer; Dateien wie eine .env legst du nach dem Anlegen darin an. Bis dahin lass "
                "den Eintrag weg oder markiere ihn unter env_file mit „required: false“."
            )
            return dataclasses.replace(checked, error=_sentence(checked.error) + note)
        return checked


def _sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?", "“")) else f"{text}."


def _create_problem(exc: BaseException, path: str, parent: str, home: str | None) -> str:
    if getattr(exc, "code", None) == _SFTP_PERMISSION_DENIED:
        if _sensitive(parent) or parent.count("/") < 2:
            hint = " Lege ihn einmal als root an und gib ihn dem Zugang von Nodvard Deck (chown)"
        else:
            hint = f" Gib dem Zugang von Nodvard Deck Schreibrecht in {parent}"
        other = f", oder wähle einen Ordner wie {home}/stacks." if home and valid_root(f"{home}/stacks") else "."
        return f"Nodvard Deck darf {path} nicht anlegen.{hint}{other}"
    if no_such_file(exc):
        return f"{path} ließ sich nicht anlegen, weil es den Ordner {parent} darüber nicht gibt. Lege ihn zuerst an oder wähle einen anderen Ordner."
    return f"{path} ließ sich nicht anlegen: {str(exc) or type(exc).__name__}"


async def _unclear(sftp: Any, exc: BaseException, path: str, parent: str) -> str:
    """Warum sich nicht prüfen ließ, ob es `path` schon gibt, mit dem nächsten Schritt."""
    if getattr(exc, "code", None) != _SFTP_PERMISSION_DENIED:
        return f"Ob es den Ordner {path} schon gibt, ließ sich nicht prüfen ({str(exc) or type(exc).__name__}). Versuch es noch einmal."
    home = None
    with contextlib.suppress(Exception):
        home = str(await sftp.realpath("."))
    # lstat braucht nur das Recht, den Ordner darüber zu betreten.
    other = f", oder wähle einen Ordner wie {home}/stacks." if home and valid_root(f"{home}/stacks") else "."
    return (f"Nodvard Deck darf {parent} nicht betreten und kann {path} deshalb nicht prüfen. Gib dem Zugang von Nodvard Deck "
            f"Zugriff auf {parent}{other}")


async def _undo(sftp: Any, created: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Entfernt, was dieser Lauf angelegt hat, in umgekehrter Reihenfolge, einen Ordner nur, wenn er leer ist; gibt zurück,
    was stehen blieb."""
    left = []
    for kind, path in reversed(created):
        try:
            await (sftp.remove(path) if kind == "file" else sftp.rmdir(path))
        except Exception:  # noqa: BLE001 - was bleibt, nennt die Meldung
            left.append((kind, path))
    return left


def _leftover(left: list[tuple[str, str]]) -> str:
    if not left:
        return ""
    folders = [path for kind, path in left if kind == "dir"]
    files = [path for kind, path in left if kind == "file"]
    text = f" Beim Aufräumen blieb stehen: {', '.join(path for _, path in left)}."
    if files:
        text += " Sieh im Dateimanager nach, ob die Datei von diesem Versuch stammt, und lösche sie dann."
    if folders:
        text += f" Ist der Ordner leer, lösche ihn im Terminal mit „rmdir -- {' '.join(shlex.quote(f) for f in folders)}“."
    return text


async def _make_dir(sftp: Any, path: str, created: list[tuple[str, str]], *, may_exist: bool = False) -> None:
    attrs = type(await sftp.lstat(posixpath.dirname(path)))(permissions=NEW_DIR_MODE)
    # Vorher eintragen: Bricht der Lauf mitten im Anlegen ab, versucht das Aufräumen ihn trotzdem (nur, wenn er leer ist).
    created.append(("dir", path))
    try:
        await sftp.mkdir(path, attrs)
    except Exception:
        created.pop()
        # Ein gleichzeitiger zweiter Lauf hat den Ordner für neue Stacks schon angelegt; er gehört dann nicht diesem Lauf.
        if may_exist and _kind(await _attrs_or_none(sftp, path)) == "dir":
            return
        raise


async def _write_new(sftp: Any, path: str, content: bytes, like: Any, created: list[tuple[str, str]]) -> None:
    """Die Compose-Datei neu anlegen (`O_EXCL`), nur für den Zugang lesbar: Sie kann Passwörter enthalten."""
    created.append(("file", path))
    try:
        handle = await sftp.open(path, "xb", type(like)(permissions=NEW_FILE_MODE))
    except Exception:
        created.pop()
        raise
    async with handle:
        # Nicht jeder SFTP-Server übernimmt die Rechte beim Anlegen; vor dem Schreiben noch einmal setzen.
        with contextlib.suppress(Exception):
            await handle.chmod(NEW_FILE_MODE)
        await handle.write(content)


async def _private(sftp: Any, path: str) -> str | None:
    """Die Datei nur für den Zugang lesbar: Nicht jeder SFTP-Server übernimmt die Rechte beim Anlegen."""
    name = posixpath.basename(path)
    with contextlib.suppress(Exception):
        await sftp.chmod(path, 0o600)
    try:
        mode = (await sftp.stat(path)).permissions
    except Exception as exc:  # noqa: BLE001 - als Grund melden
        return f"{name} ließ sich nicht prüfen: {str(exc) or type(exc).__name__}"
    if mode is None or mode & 0o077:
        return f"{name} wäre für andere auf dem Server lesbar; der Server übernimmt die Rechte 600 nicht."
    return None


class DockerStackCreateExecutor:
    """Legt den Ordner an, prüft die Datei dort noch einmal mit Compose, schreibt sie und übernimmt den Stack."""

    action_types = frozenset({CREATE_ACTION})

    def __init__(self, ctx: Any, new: NewStacks, adopted: AdoptedStacks, actions: DockerStackExecutor) -> None:
        self._ctx = ctx
        self._new = new
        self._adopted = adopted
        self._actions = actions

    async def execute(self, req: ActionRequest) -> ActionResult:
        draft_id = str((req.payload or {}).get("draft") or "")
        try:
            return await self._execute(req, draft_id)
        finally:
            self._new.editor.drafts.drop(draft_id)

    async def _execute(self, req: ActionRequest, draft_id: str) -> ActionResult:
        payload = req.payload or {}
        name = str(payload.get("project") or "")
        if getattr(req.proposed_by.type, "value", req.proposed_by.type) != "user":
            return ActionResult(success=False, error=NOT_A_USER)
        if not PROJECT_RE.match(name):
            return ActionResult(success=False, error=f"Ungültiger Stack-Name: {name!r}")
        host = await self._ctx.hosts.get(req.host_ref) if req.host_ref else None
        tag = (await self._ctx.settings.get()).get("docker_host_tag") or "docker"
        if host is None or tag not in host.tags:
            return ActionResult(success=False, error="Kein Docker-Host.")
        root = await self._new.root(host.id)
        stack = new_stack(name, root)
        if payload.get("folder") != stack.working_dir:
            return ActionResult(success=False, error="Der Ordner für neue Stacks wurde inzwischen geändert. Bitte noch einmal anlegen.")
        draft = self._new.editor.drafts.get(draft_id)
        if draft is None or (draft[0].get("host_id"), draft[0].get("project"), draft[0].get("path"), draft[0].get("sha256")) != (
            host.id, name, stack.files[0], payload.get("sha256"),
        ):
            return ActionResult(success=False, error="Der Entwurf ist abgelaufen oder passt nicht. Bitte noch einmal anlegen.")
        with self._actions.locks.hold(host.id, name, "Anlegen") as busy:
            if busy is not None:
                return ActionResult(success=False, error=busy_text(busy))
            return await self._create(req, host, stack, root, draft[1], bool(payload.get("start")))

    async def _create(self, req: ActionRequest, host: Any, stack: Stack, root: str, content: bytes, start: bool) -> ActionResult:
        started = time.monotonic()
        conflict = await self._new.conflict(host, stack.project, root)
        if conflict is not None:
            return ActionResult(success=False, error=conflict)
        created: list[tuple[str, str]] = []
        async with self._ctx.exec.sftp(host) as sftp:
            try:
                problem = await self.lay_out(req, sftp, host, stack, root, content, created)
            except BaseException:
                # Auch bei einem Abbruch (Neustart von Deck, Zeitgrenze der Freigabe) nichts Halbes liegen lassen.
                await asyncio.shield(_undo(sftp, created))
                raise
            if problem is not None:
                left = await asyncio.shield(_undo(sftp, created))
                return ActionResult(success=False, error=_sentence(problem) + _leftover(left) if left else problem)
        folder = stack.working_dir
        try:
            await self._adopted.add(host.id, stack)
        except Exception as exc:  # noqa: BLE001 - die Datei steht, nur das Merken fehlt
            return ActionResult(success=False, error=(
                f"Die Compose-Datei liegt in {folder}, Nodvard Deck konnte sich den Stack aber nicht merken ({exc}). "
                "Gestartet ist er noch nicht. Starte ihn im Terminal in diesem Ordner mit „docker compose up -d“; danach lässt er "
                "sich im Reiter Stacks übernehmen."
            ))
        done = f"Stack „{stack.project}“ in {folder} angelegt und übernommen."
        duration_ms = int((time.monotonic() - started) * 1000)
        if not start:
            return ActionResult(success=True, exit_code=0, output=done, duration_ms=duration_ms, detail={"created": True})
        try:
            applied = await self._actions.run(host, stack, "docker.stack_up")
        except Exception as exc:  # noqa: BLE001 - der Stack ist schon angelegt, das muss die Oberfläche erfahren
            return ActionResult(success=False, output=done, duration_ms=duration_ms, detail={"created": True},
                                error=f"Angelegt, aber das Starten ist gescheitert: {_describe(exc)}")
        output = "\n".join(part for part in (done, applied.output) if part)
        detail = {**(applied.detail or {}), "created": True}
        if applied.success:
            return ActionResult(success=True, exit_code=0, output=output, duration_ms=duration_ms + (applied.duration_ms or 0), detail=detail)
        return ActionResult(
            success=False, exit_code=applied.exit_code, output=output, duration_ms=duration_ms + (applied.duration_ms or 0),
            error=f"Angelegt, aber das Starten ist gescheitert: {applied.error}", detail=detail,
        )

    async def lay_out(
        self, req: ActionRequest, sftp: Any, host: Any, stack: Stack, root: str, content: bytes, created: list[tuple[str, str]],
        extra: tuple[tuple[str, bytes], ...] = (), vet: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> str | None:
        """Ordner anlegen, `extra` (etwa eine `.env`) hineinschreiben, im Ordner prüfen, Datei schreiben; gibt den Grund
        zurück, wenn es nicht ging. `vet` prüft zusätzlich die Datei, wie Compose sie im neuen Ordner auflöst."""
        folder, path = stack.working_dir, stack.files[0]
        home = None
        with contextlib.suppress(Exception):
            home = str(await sftp.realpath("."))
        try:
            root_attrs = await _exists(sftp, root)
        except Exception as exc:  # noqa: BLE001 - als Grund melden
            return await _unclear(sftp, exc, root, posixpath.dirname(root))
        if root_attrs is not None and (refusal := await _root_refusal(sftp, root, root_attrs)) is not None:
            return refusal
        target, parent = root, posixpath.dirname(root)
        try:
            if root_attrs is None:
                await _make_dir(sftp, root, created, may_exist=True)
                if (refusal := await _root_refusal(sftp, root, await _exists(sftp, root))) is not None:
                    return refusal
            target, parent = folder, root
            await _make_dir(sftp, folder, created)
        except Exception as exc:  # noqa: BLE001 - als Grund melden
            if target == folder and await _attrs_or_none(sftp, folder) is not None:
                return f"Den Ordner {folder} gibt es inzwischen. Nodvard Deck überschreibt nie einen bestehenden Ordner; nimm einen anderen Namen."
            return _create_problem(exc, target, parent, home)
        for extra_path, extra_content in extra:
            try:
                await _write_new(sftp, extra_path, extra_content, await sftp.lstat(folder), created)
            except Exception as exc:  # noqa: BLE001 - als Grund melden
                return f"{posixpath.basename(extra_path)} ließ sich nicht schreiben: {str(exc) or type(exc).__name__}"
            if (problem := await _private(sftp, extra_path)) is not None:
                return problem
        checked = await self._new.editor.check(host, stack, 0, content)
        if checked.problem is not None:
            return checked.problem
        if checked.warnings and RISK_ORDER.index(req.risk) < RISK_ORDER.index(Risk.HIGH):
            return RISK_TOO_LOW
        if vet is not None and (refusal := vet(checked.config or {})) is not None:
            return refusal
        try:
            await _write_new(sftp, path, content, await sftp.lstat(folder), created)
        except Exception as exc:  # noqa: BLE001 - als Grund melden
            return f"Die Compose-Datei ließ sich nicht schreiben: {str(exc) or type(exc).__name__}"
        # Mit Variablen aus einem anderen Speicher kann auch die Datei Geheimnisse enthalten.
        return await _private(sftp, path) if extra else None

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        payload = req.payload or {}
        then = " und ihn starten" if payload.get("start") else ""
        return DryRunReport(would_change=True, summary=f"Würde den Stack „{payload.get('project', '?')}“ in {payload.get('folder', '?')} anlegen{then}.")


class _NewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=64)
    content: str = Field(max_length=MAX_FILE_BYTES)


class _CreateIn(_NewIn):
    start: bool = False


class _RootIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = Field(default=None, max_length=1024)


def new_routes(new: NewStacks, docker_host: Any, current_actor: Any) -> APIRouter:
    router = APIRouter()

    def _content(text: str) -> bytes:
        data = text.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"Die Datei ist größer als {MAX_FILE_BYTES // 1024} KB.")
        return data

    def _name(name: str) -> str:
        if not PROJECT_RE.match(name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Der Name darf nur Kleinbuchstaben, Ziffern, „-“ und „_“ enthalten und muss mit einem Buchstaben oder einer Ziffer beginnen.",
            )
        return name

    @router.get("/hosts/{host_id}/new-stack")
    async def new_place(host_id: str) -> dict:
        """Wohin neue Stacks auf diesem Server kommen."""
        return await new.place(await docker_host(host_id))

    @router.put("/hosts/{host_id}/stack-root")
    async def set_root(host_id: str, body: _RootIn, actor: Actor = Depends(current_actor)) -> dict:
        """Ordner für neue Stacks auf diesem Server; `null` nimmt wieder die Vorgabe."""
        host = await docker_host(host_id)
        path = _setting(body.path)
        if path is not None and (problem := root_problem(path)) is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)
        return {"root": await new.set_root(host.id, path, actor)}

    async def _prepare(host_id: str, body: _NewIn) -> tuple[Any, Stack, str, bytes, Any, str | None]:
        host = await docker_host(host_id)
        name, content = _name(body.name), _content(body.content)
        root = await new.root(host.id)
        stack = new_stack(name, root)
        return host, stack, root, content, await new.check(host, stack, root, content), await new.conflict(host, name, root)

    @router.post("/hosts/{host_id}/new-stack/check")
    async def check_new(host_id: str, body: _NewIn) -> dict:
        """Prüft Name und Datei eines neuen Stacks, ohne etwas anzulegen."""
        _, stack, _, _, checked, conflict = await _prepare(host_id, body)
        return {**checked.as_response(), "folder": stack.working_dir, "conflict": conflict}

    @router.post("/hosts/{host_id}/new-stack", status_code=status.HTTP_202_ACCEPTED)
    async def create(host_id: str, body: _CreateIn, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt das Anlegen vor (Gate-Aktion `docker.stack_create`), mit hohem Risiko bei riskanten Einstellungen."""
        host, stack, _, content, checked, conflict = await _prepare(host_id, body)
        if conflict is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=conflict)
        if checked.problem is not None:
            code = status.HTTP_422_UNPROCESSABLE_ENTITY if checked.error is not None else status.HTTP_502_BAD_GATEWAY
            raise HTTPException(status_code=code, detail=checked.problem)
        risk = Risk.HIGH if checked.warnings else Risk.MEDIUM
        try:
            draft = await asyncio.to_thread(new.editor.drafts.put, host.id, stack.project, stack.files[0], content)
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Der Entwurf ließ sich nicht ablegen ({exc.strerror or type(exc).__name__}). Ist der Datenordner von Nodvard Deck "
                "voll oder schreibgeschützt?",
            ) from exc
        lines = len(content.decode("utf-8", "replace").splitlines())
        shown = ", ".join(checked.services[:8]) + (f" und {len(checked.services) - 8} weitere" if len(checked.services) > 8 else "")
        services = f", Dienste: {shown}" if checked.services else ""
        reason = f"Neuen Stack „{stack.project}“ in {stack.working_dir} anlegen ({lines} Zeilen{services})" + (" und starten" if body.start else "") + "."
        if checked.warnings:
            reason += " Riskant: " + " ".join(checked.warnings)
        decision = await new.editor.propose(
            ActionRequest(
                action_type=CREATE_ACTION, host_ref=host.id, risk=risk, proposed_by=actor, reason=reason,
                payload={
                    "project": stack.project, "folder": stack.working_dir, "draft": draft, "sha256": sha256(content),
                    "start": body.start, "lines": lines, "warnings": list(checked.warnings),
                },
            ),
        )
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": risk.value, "detail": decision.detail}

    return router
