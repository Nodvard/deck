"""Umzug von Portainer: Datei und Variablen eines Portainer-Stacks kommen in einen eigenen Ordner, dann wird der Stack unter
demselben Projektnamen neu ausgerollt. Die Volumes bleiben, die Container werden einmal neu erstellt."""

from __future__ import annotations

import asyncio
import json
import posixpath
import re
import time
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Actor, Risk

from .adopt import COMPOSE_V1, ITSELF, AdoptedStacks
from .applier import RISK_ORDER
from .capabilities import is_own_container
from .commands import VOLUME_NAME_RE
from .editor import StackEditor
from .image_apply import (
    LABEL_PROJECT,
    LABEL_WORKDIR,
    PROJECT_RE,
    _valid_path,
    is_self_image,
)
from .new_stack import (
    DockerStackCreateExecutor,
    NewStacks,
    _leftover,
    _sentence,
    _unclear,
    _undo,
    new_stack,
)
from .stack_actions import (
    FAILURE_LIMIT,
    _connection_error,
    _in_folder,
    _lost,
    busy_text,
    failure_line,
)
from .stack_backup import BACKUP_IMAGE
from .stack_delete import _ID_RE, MAX_CONTAINERS, cmd_container_ids
from .stacks import (
    MAX_ENV_BYTES,
    MAX_FILE_BYTES,
    DockerStacks,
    Stack,
    StackProblem,
    _assignment,
    _closing_quote,
    _line_end,
    no_such_file,
)

MOVE_ACTION = "docker.stack_from_portainer"
ENV_NAME = "stack.env"
READ_TIMEOUT_S = 300
UP_TIMEOUT_S = 2700
PORTAINER_REPOS = ("portainer/portainer-ce", "portainer/portainer-ee")
INSPECT_FORMAT = '{"name":{{json .Name}},"image":{{json .Config.Image}},"running":{{json .State.Running}},"mounts":{{json .Mounts}}}'
KEEP_IN_PORTAINER = (
    "Lösche den Stack danach nicht in Portainer und rolle ihn dort nicht neu aus: Er hat denselben Projektnamen, Portainer würde "
    "die neuen Container entfernen oder mit seiner alten Datei ersetzen. Lass den Eintrag stehen oder schalte Portainer ganz ab, "
    "wenn alle Stacks umgezogen sind."
)
NOT_A_FILE_EXIT = 4
MISSING_EXIT = 5
_READ_SCRIPT = (
    f'd=/data/compose/$1; f="$d/$2"; if [ -L /data/compose ] || [ -L "$d" ] || [ -L "$f" ]; then exit {NOT_A_FILE_EXIT}; fi; '
    f'if [ ! -e "$f" ]; then exit {MISSING_EXIT}; fi; if [ ! -f "$f" ]; then exit {NOT_A_FILE_EXIT}; fi; exec head -c "$3" "$f"'
)
"""Liest höchstens `$3` Bytes einer normalen Datei; Verweise, Ordner und Geräte im Speicher von Portainer bleiben ungelesen."""
WORKING_DIRS_FORMAT = '{{.Label "' + LABEL_WORKDIR + '"}}\t{{.State}}'
PORTAINER_DIR = "/data/compose/"
_STACK_DIR_RE = re.compile(r"^/data/compose/([0-9]{1,9})\Z")
_VERSIONED_DIR_RE = re.compile(r"^/data/compose/[0-9]{1,9}/.")
_DIGITS_RE = re.compile(r"^[0-9]{1,9}\Z")
_ENTRY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.ya?ml\Z")
# Englisch von Compose oder schon deutsch aus `failure_line`, die Meldungen zu einer .env vorher umschreibt.
_LINE_RE = re.compile(r"\b(?:line|Zeile) (\d+)")
# Nur Meldungen, in denen Compose die Datei zitiert; eigene Sätze wie „.env ließ sich nicht schreiben“ bleiben.
_ENV_FILE_RE = re.compile(r"(?:Compose kann|failed to (?:read|load)|error reading) (?:eine |\S*/)?(?:stack)?\.env\b")
_ESCAPE_RE = re.compile(r"\\(.)", re.DOTALL)
_ESCAPES = {"n": "\n", "r": "\r", "t": "\t"}
_COMMENT_RE = re.compile(r"\s#")


class PortainerProblem(Exception):
    """Portainer oder sein Speicher lässt sich so nicht eindeutig finden; der Text ist für Menschen."""


def move_action_spec() -> ActionSpec:
    return ActionSpec(
        action_type=MOVE_ACTION, label="Stack von Portainer übernehmen", icon="truck", default_risk=Risk.HIGH,
        description="Holt Datei und Variablen eines Portainer-Stacks in einen eigenen Ordner und rollt ihn dort neu aus; die "
        "Volumes bleiben.",
        permissions=["hosts.execute"],
        host_bound=False,
        confirm_text="Die Container des Stacks werden einmal neu erstellt und sind dabei kurz nicht erreichbar. Die Volumes bleiben. "
        "Fortfahren?",
    )


def cmd_portainer_inspect(ids: list[str]) -> list[str]:
    """Je Container eine JSON-Zeile mit Name, Image, Zustand und Mounts, um Portainer und seinen Speicher zu finden."""
    if not ids or len(ids) > MAX_CONTAINERS or not all(_ID_RE.match(i) for i in ids):
        raise ValueError("Ungültige Container-IDs")
    return ["docker", "container", "inspect", "--format", INSPECT_FORMAT, *ids]


def cmd_read(source: str, stack_id: str, name: str, limit: int) -> list[str]:
    """Liest eine Datei des Stacks aus Portainers Speicher, in einem kurzen Container ohne Netz, der ihn nur lesend sieht."""
    if not (VOLUME_NAME_RE.match(source) or (_valid_path(source) and ":" not in source)):
        raise ValueError(f"Ungültiger Speicher: {source!r}")
    if not _DIGITS_RE.match(stack_id) or not (_ENTRY_RE.match(name) or name == ENV_NAME) or not 0 < limit <= MAX_FILE_BYTES:
        raise ValueError(f"Ungültige Datei: {stack_id!r}/{name!r}")
    return [
        "docker", "run", "--rm", "--network", "none", "-v", f"{source}:/data:ro", BACKUP_IMAGE,
        "sh", "-c", _READ_SCRIPT, "sh", stack_id, name, str(limit + 1),
    ]


def cmd_working_dirs(project: str) -> list[str]:
    """Je Container des Projekts sein Ordner laut Compose und sein Zustand, auch für gemischte Stände nach einem Abbruch."""
    if not PROJECT_RE.match(project):
        raise ValueError(f"Ungültiger Projektname: {project!r}")
    return ["docker", "ps", "-a", "--filter", f"label={LABEL_PROJECT}={project}", "--format", WORKING_DIRS_FORMAT]


def parse_working_dirs(text: str) -> list[tuple[str, str]]:
    rows = []
    for line in text.splitlines():
        folder, _, state = line.partition("\t")
        if folder:
            rows.append((folder, state.strip().lower()))
    return rows


def cmd_move_up(stack: Stack) -> list[str]:
    # --force-recreate: sonst ließe Compose gleich beschriebene Container stehen, mit den Angaben von Portainer.
    return _in_folder(stack, "up", "-d", "--force-recreate")


def portainer_entry(stack: Stack) -> tuple[str, str] | str:
    """(Nummer des Stacks bei Portainer, Name der Datei) oder warum der Stack so nicht umziehen kann."""
    match = _STACK_DIR_RE.match(stack.working_dir)
    if stack.source == "portainer" and _VERSIONED_DIR_RE.match(stack.working_dir):
        return ("Portainer führt diesen Stack in mehreren Fassungen oder holt ihn aus Git. Das kann der Umzug noch nicht; kopiere die "
                "Datei aus Portainer und lege damit einen neuen Stack an.")
    if stack.source != "portainer" or match is None:
        return "Das ist kein Stack, den Portainer auf diesem Server angelegt hat."
    files = stack.portainer_files
    if len(files) != 1 or posixpath.dirname(files[0]) != stack.working_dir or not _ENTRY_RE.match(posixpath.basename(files[0])):
        return "Dieser Portainer-Stack besteht aus mehreren Dateien (etwa aus Git). Das kann der Umzug noch nicht."
    return match.group(1), posixpath.basename(files[0])


def _portainer_image(image: str) -> bool:
    name = image.split("@", 1)[0]
    if name.rfind(":") > name.rfind("/"):
        name = name[: name.rfind(":")]
    return any(name == repo or name.endswith("/" + repo) for repo in PORTAINER_REPOS)


def find_storage(text: str) -> str | None:
    """Der Speicher von Portainer (`/data`) aus der Ausgabe von `cmd_portainer_inspect`: Volume-Name oder Ordner. Ein laufender
    Portainer geht vor; gibt es mehrere Speicher, ist nicht klar, welcher den Stack hat (`PortainerProblem`)."""
    found: dict[str, bool] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not _portainer_image(str(row.get("image") or "")):
            continue
        for mount in row.get("mounts") or []:
            if not isinstance(mount, dict) or mount.get("Destination") != "/data":
                continue
            source = mount.get("Name") if mount.get("Type") == "volume" else mount.get("Source")
            if isinstance(source, str) and (VOLUME_NAME_RE.match(source) or (_valid_path(source) and ":" not in source)):
                found[source] = found.get(source, False) or row.get("running") is True
    candidates = sorted(s for s, running in found.items() if running) or sorted(found)
    if len(candidates) > 1:
        raise PortainerProblem(f"Auf diesem Server gibt es mehrere Portainer mit eigenem Speicher ({', '.join(candidates)}). Nodvard Deck "
                               "weiß nicht, welcher den Stack verwaltet; kopiere die Datei aus Portainer und lege damit einen neuen Stack an.")
    return candidates[0] if candidates else None


def resolved_blockers(config: dict[str, Any], folder: str, portainer_dir: str) -> list[str]:
    """Was in der Datei, wie Compose sie im neuen Ordner auflöst, auf Dateien im neuen Ordner zeigt; unter Portainer lagen sie
    in dessen Ordner auf dem Server, nach dem Umzug fänden die Dienste dort nichts."""

    def inside(path: Any) -> str | None:
        if not isinstance(path, str) or not (path == folder or path.startswith(folder + "/")):
            return None
        return posixpath.normpath(posixpath.join(portainer_dir, posixpath.relpath(path, folder)))

    found = []
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        who = f"„{name}“"
        for volume in service.get("volumes") or []:
            if isinstance(volume, dict) and volume.get("type") == "bind" and (where := inside(volume.get("source"))):
                found.append(f"{who} bindet einen Ordner neben der Datei ein. Unter Portainer liegen die Daten auf dem Server unter {where}; "
                             "trage in Portainer diesen vollen Pfad ein, dann klappt der Umzug ohne Datenverlust.")
        for entry in service.get("env_file") or []:
            path = entry.get("path") if isinstance(entry, dict) else entry
            if inside(path) and posixpath.basename(str(path)) not in (".env", ENV_NAME):
                found.append(f"{who} liest {posixpath.basename(str(path))}, die Datei liegt bei Portainer und kommt nicht mit.")
        build = service.get("build")
        if isinstance(build, dict) and inside(build.get("context")):
            found.append(f"{who} wird aus Dateien bei Portainer gebaut (build).")
    for section in ("configs", "secrets"):
        entries = config.get(section) if isinstance(config.get(section), dict) else {}
        for name, entry in entries.items():
            if isinstance(entry, dict) and (where := inside(entry.get("file"))):
                found.append(f"„{name}“ ({section}) liest {where} aus dem Ordner bei Portainer.")
    return found


def secret_values(env: bytes | None) -> list[str]:
    """Die Werte aus `stack.env` roh, wie Compose sie liest und wie Docker sie zitiert, längste zuerst; deckt die bekannten Formen ab."""
    text = (env or b"").decode("utf-8", "replace").removeprefix("\ufeff")
    found: set[str] = set()
    pos = 0
    while pos < len(text):
        end = _line_end(text, pos)
        found.add(text[pos:end].partition("=")[2].strip().strip("\"'"))
        if (assignment := _assignment(text, pos, end)) is not None:
            start = assignment[1]
            while start < end and text[start] in " \t":
                start += 1
            if start < end and text[start] in "'\"":
                close = _closing_quote(text, start)
                raw = text[start + 1:close]
                found |= {raw, _ESCAPE_RE.sub(lambda m: _ESCAPES.get(m[1], m[1]), raw)}
                end = _line_end(text, close)
            else:
                found.add(_COMMENT_RE.split(text[start:end], maxsplit=1)[0].strip())
        pos = end + 1
    found |= {line.strip() for value in list(found) for line in value.splitlines()}
    # Compose macht aus `$$` ein `$`; Docker setzt in Anführungszeichen vor `\` und `"` einen Backslash.
    found |= {form for value in list(found) for plain in (value, value.replace("$$", "$"))
              for form in (plain, plain.replace("\\", "\\\\").replace('"', '\\"'))}
    return sorted((v for v in found if len(v) >= 4), key=len, reverse=True)


def scrub(text: str, values: list[str]) -> str:
    """Eine Meldung von Compose ohne die Werte der Variablen; zitiert sie die Env-Datei, bleibt nur die Zeilennummer."""
    for value in values:
        text = text.replace(value, "…")
    # Eine gekürzte Meldung kann mit dem Anfang eines Werts enden.
    for value in values:
        if cut := next((n for n in range(min(len(value) - 1, FAILURE_LIMIT), 3, -1) if text.endswith(value[:n])), 0):
            text = text[:-cut] + "…"
    if values and _ENV_FILE_RE.search(text):
        line = _LINE_RE.search(text)
        where = f" (Zeile {line.group(1)} in {ENV_NAME})" if line else ""
        return (f"Compose liest die Variablen aus Portainer nicht{where}; den Wert zeigt Nodvard Deck nicht. Korrigiere die Variable "
                "in Portainer und versuch es noch einmal.")
    return text


def _relative(value: Any) -> bool:
    return isinstance(value, str) and value.startswith((".", "~"))


def uses_stack_env(compose: str) -> bool:
    """Ob ein Dienst `env_file: stack.env` liest, wie es Portainer für alle Variablen auf einmal vorschlägt."""
    try:
        services = (yaml.safe_load(compose) or {}).get("services") or {}
    except (yaml.YAMLError, AttributeError):
        return False
    for service in services.values() if isinstance(services, dict) else ():
        entries = service.get("env_file") if isinstance(service, dict) else None
        for entry in entries if isinstance(entries, list) else [entries] if entries else []:
            path = entry.get("path") if isinstance(entry, dict) else entry
            if isinstance(path, str) and posixpath.basename(path) == ENV_NAME:
                return True
    return False


def move_blockers(compose: str, stack_dir: str) -> list[str]:
    """Was in der Datei nach dem Umzug woanders hinzeigen würde: relative Pfade, `build`, `extends`, `include`."""
    try:
        config = yaml.safe_load(compose)
    except yaml.YAMLError as exc:
        return [f"Die Datei lässt sich nicht lesen: {str(exc).splitlines()[0]}"]
    if not isinstance(config, dict) or not isinstance(config.get("services"), dict):
        return ["Die Datei beschreibt keine Dienste."]
    found = []
    if "include" in config:
        found.append("Die Datei bindet weitere Dateien ein (include).")
    for name, service in config["services"].items():
        if not isinstance(service, dict):
            continue
        who = f"„{name}“"
        if "build" in service or "extends" in service:
            found.append(f"{who} wird aus Dateien bei Portainer gebaut oder erweitert (build, extends).")
        for volume in service.get("volumes") or []:
            source = volume.get("source") if isinstance(volume, dict) else str(volume).split(":", 1)[0] if ":" in str(volume) else None
            if _relative(source):
                where = posixpath.normpath(posixpath.join(stack_dir, str(source)))
                found.append(f"{who} bindet {source} ein. Die Daten liegen auf dem Server unter {where}; trage in Portainer diesen "
                             "vollen Pfad ein, dann klappt der Umzug ohne Datenverlust.")
        env_files = service.get("env_file")
        for entry in env_files if isinstance(env_files, list) else [env_files] if env_files else []:
            path = entry.get("path") if isinstance(entry, dict) else entry
            if isinstance(path, str) and posixpath.basename(path) != ENV_NAME and not path.startswith("/"):
                found.append(f"{who} liest {path}, die Datei liegt bei Portainer und kommt nicht mit.")
    for section in ("configs", "secrets"):
        for name, entry in (config.get(section) or {}).items() if isinstance(config.get(section), dict) else ():
            if isinstance(entry, dict) and _relative(entry.get("file")):
                found.append(f"„{name}“ ({section}) liest {entry['file']} aus dem Ordner bei Portainer.")
    return found


def foreign_file(compose: str, stack: Stack) -> str | None:
    """Warum die Datei bei Portainer nicht zum laufenden Stack passt, oder `None`; jeder Dienst muss auf beiden Seiten stehen."""
    services = (yaml.safe_load(compose) or {}).get("services") or {}
    running = {m.service for m in stack.members if m.service}
    missing, extra = sorted(running - set(services)), sorted({name for name, s in services.items() if _starts(s)} - running)
    if missing and extra:
        return (f"Die Datei bei Portainer passt nicht zum laufenden Stack: Darin fehlt {_names(missing)}, dafür steht {_names(extra)} "
                "darin. Vielleicht gehört sie zu einem anderen Stack, etwa nach einer Neuinstallation von Portainer. Prüfe den Stack "
                "in Portainer.")
    if missing:
        return (f"Für {_names(missing)} gibt es noch einen Container, in der Datei bei Portainer steht der Dienst aber nicht. Ist das "
                "ein Rest eines entfernten Dienstes, lösche den Container im Reiter Container und versuch es noch einmal; sonst prüfe "
                "den Stack in Portainer.")
    if extra:
        return (f"Für {_names(extra)} steht ein Dienst in der Datei bei Portainer, es gibt aber keinen Container dazu. Rolle den Stack "
                "in Portainer einmal neu aus und versuch es dann noch einmal.")
    return None


def _starts(service: Any) -> bool:
    """Ob `up` ohne `--profile` den Dienst startet: nicht mit Profil und nicht auf null Container gesetzt."""
    if not isinstance(service, dict):
        return True
    deploy = service.get("deploy") if isinstance(service.get("deploy"), dict) else {}
    return not service.get("profiles") and service.get("scale") != 0 and deploy.get("replicas") != 0


def _names(services: list[str]) -> str:
    return ", ".join(f"„{s}“" for s in services)


class PortainerMove:
    """Was ein Umzug von Portainer bringen würde; ausgeführt wird nur im Ausführer."""

    def __init__(self, ctx: Any, stacks: DockerStacks, adopted: AdoptedStacks, new: NewStacks) -> None:
        self._ctx = ctx
        self.stacks = stacks
        self._adopted = adopted
        self.new = new

    async def running(self, host: Any, project: str) -> Stack | str:
        try:
            found = [s for s in await self.stacks.stacks(host, project) if s.project == project]
        except StackProblem as exc:
            return str(exc)
        if not found:
            return f"Auf diesem Server läuft kein Stack „{project}“."
        return found[0]

    async def storage(self, host: Any) -> str | None:
        """Wo Portainer auf diesem Server seine Daten hat, oder `None`, wenn hier kein Portainer läuft."""
        listed = await self._ctx.exec.run_argv(host, cmd_container_ids(), timeout_s=60)
        ids = listed.stdout.split()
        if listed.exit_code != 0 or not ids or len(ids) > MAX_CONTAINERS or not all(_ID_RE.match(i) for i in ids):
            return None
        inspected = await self._ctx.exec.run_argv(host, cmd_portainer_inspect(ids), timeout_s=60)
        # Verschwindet ein anderer Container zwischen den beiden Aufrufen, endet `inspect` mit Fehler; die übrigen Zeilen gelten.
        return find_storage(inspected.stdout)

    async def working_dirs(self, host: Any, project: str) -> list[tuple[str, str]] | None:
        """(Ordner, Zustand) je Container des Projekts, oder `None`, wenn sich das nicht lesen ließ."""
        try:
            result = await self._ctx.exec.run_argv(host, cmd_working_dirs(project), timeout_s=60)
        except Exception as exc:
            if not _connection_error(exc):
                raise
            return None
        return parse_working_dirs(result.stdout) if result.exit_code == 0 else None

    async def plan(self, host: Any, project: str) -> dict[str, Any]:
        """Wohin der Stack zöge und warum es nicht geht; die Datei selbst liest erst der Ausführer."""
        root = await self.new.root(host.id)
        out: dict[str, Any] = {"project": project, "folder": posixpath.join(root, project), "portainer_id": None, "services": [],
                               "stopped": False, "problem": None}
        stack = await self.running(host, project)
        if isinstance(stack, str):
            out["problem"] = stack
            return out
        out["services"] = sorted({m.service for m in stack.members if m.service})
        out["stopped"] = not any(m.state == "running" for m in stack.members)
        if any(is_own_container(m.id) or is_self_image(m.image) for m in stack.members):
            out["problem"] = ITSELF
            return out
        entry = portainer_entry(stack)
        if isinstance(entry, str):
            out["problem"] = entry
            return out
        out["portainer_id"] = entry[0]
        if stack.version.startswith("1."):
            out["problem"] = COMPOSE_V1
        elif project in self._adopted.for_host(host.id):
            out["problem"] = f"Einen Stack „{project}“ hat Nodvard Deck auf diesem Server schon übernommen."
        elif (problem := await self.folder_problem(host, out["folder"], root)) is not None:
            out["problem"] = problem
        else:
            try:
                found = await self.storage(host)
            except OSError as exc:
                found, out["problem"] = None, f"Server nicht erreichbar: {str(exc) or type(exc).__name__}"
            except PortainerProblem as exc:
                found, out["problem"] = None, str(exc)
            if found is None and out["problem"] is None:
                out["problem"] = ("Auf diesem Server läuft kein Portainer, dessen Speicher Nodvard Deck lesen kann. Kopiere die Datei "
                                  "aus Portainer und lege damit einen neuen Stack an.")
        return out

    async def folder_problem(self, host: Any, folder: str, root: str) -> str | None:
        try:
            async with self._ctx.exec.sftp(host) as sftp:
                try:
                    await sftp.lstat(folder)
                except Exception as exc:  # noqa: BLE001 - als Grund zeigen
                    return None if no_such_file(exc) else await _unclear(sftp, exc, folder, root)
        except Exception as exc:  # noqa: BLE001 - als Grund zeigen
            return f"Der Ordner {folder} ließ sich nicht prüfen ({str(exc) or type(exc).__name__})."
        return f"Den Ordner {folder} gibt es schon. Nodvard Deck überschreibt nie einen bestehenden Ordner."


class DockerPortainerMoveExecutor:
    """Liest Datei und Variablen aus Portainers Speicher, legt den Ordner an, rollt den Stack dort neu aus und übernimmt ihn."""

    action_types = frozenset({MOVE_ACTION})

    def __init__(self, ctx: Any, move: PortainerMove, create: DockerStackCreateExecutor, adopted: AdoptedStacks, actions: Any) -> None:
        self._ctx = ctx
        self._move = move
        self._create = create
        self._adopted = adopted
        self._actions = actions

    async def execute(self, req: ActionRequest) -> ActionResult:
        payload = req.payload or {}
        project = str(payload.get("project") or "")
        if getattr(req.proposed_by.type, "value", req.proposed_by.type) != "user":
            return ActionResult(success=False, error="Einen Stack zieht nur ein Mensch über die Docker-Seite um.")
        if RISK_ORDER.index(req.risk) < RISK_ORDER.index(Risk.HIGH):
            return ActionResult(success=False, error="Der Umzug braucht eine Freigabe mit hohem Risiko.")
        if not PROJECT_RE.match(project):
            return ActionResult(success=False, error=f"Ungültiger Stack-Name: {project!r}")
        host = await self._ctx.hosts.get(req.host_ref) if req.host_ref else None
        tag = (await self._ctx.settings.get()).get("docker_host_tag") or "docker"
        if host is None or tag not in host.tags:
            return ActionResult(success=False, error="Kein Docker-Host.")
        with self._actions.locks.hold(host.id, project, "Umzug von Portainer") as busy:
            if busy is not None:
                return ActionResult(success=False, error=busy_text(busy))
            return await self._run(req, host, project, payload)

    async def _read(self, host: Any, storage: str, stack_id: str, name: str, limit: int, *, optional: bool = False) -> bytes | str | None:
        try:
            result = await self._ctx.exec.run_argv(host, cmd_read(storage, stack_id, name, limit), timeout_s=READ_TIMEOUT_S)
        except Exception as exc:
            if not _connection_error(exc):
                raise
            return f"{name} ließ sich bei Portainer nicht lesen: {_lost(exc)}."
        if result.exit_code == MISSING_EXIT:
            return None if optional else f"{name} fehlt in Portainers Speicher."
        if result.exit_code == NOT_A_FILE_EXIT:
            return f"{name} ist in Portainers Speicher keine normale Datei (etwa ein Verweis); Nodvard Deck liest sie nicht."
        if result.exit_code != 0:
            # Nie die Ausgabe: Sie könnte schon Inhalt der Datei sein.
            reason = failure_line(result.stderr) if (result.stderr or "").strip() else f"Exit {result.exit_code}"
            return f"{name} ließ sich bei Portainer nicht lesen: {reason}."
        if "\ufffd" in result.stdout:
            return f"{name} ist keine Textdatei in UTF-8."
        data = result.stdout.encode("utf-8")
        if len(data) > limit:
            return f"{name} ist größer als {limit // 1024} KB."
        return data

    async def _gather(self, host: Any, project: str, payload: dict[str, Any]) -> tuple[Stack, bytes, bytes | None] | str:
        """Stack, Datei und Variablen bei Portainer, oder warum es nicht weitergeht; ändert noch nichts."""
        plan = await self._move.plan(host, project)
        if plan["problem"] is not None:
            return f"{_sentence(plan['problem'])} Geändert wurde nichts."
        if payload.get("folder") != plan["folder"] or payload.get("portainer_id") != plan["portainer_id"]:
            return "Der Stack oder der Ordner hat sich inzwischen geändert. Bitte noch einmal umziehen."
        try:
            storage = await self._move.storage(host)
        except PortainerProblem as exc:
            return f"{exc} Geändert wurde nichts."
        stack = await self._move.running(host, project)
        if storage is None or isinstance(stack, str) or isinstance(entry := portainer_entry(stack), str):
            return "Portainer oder der Stack ist inzwischen nicht mehr zu finden. Geändert wurde nichts."
        stack_id, name = entry
        compose = await self._read(host, storage, stack_id, name, MAX_FILE_BYTES)
        env = await self._read(host, storage, stack_id, ENV_NAME, MAX_ENV_BYTES, optional=True)
        if isinstance(compose, str) or compose is None:
            return f"{compose} Geändert wurde nichts."
        if isinstance(env, str):
            return f"{env} Geändert wurde nichts."
        return stack, compose, env

    async def _run(self, req: ActionRequest, host: Any, project: str, payload: dict[str, Any]) -> ActionResult:
        started = time.monotonic()
        try:
            gathered = await self._gather(host, project, payload)
        except Exception as exc:
            if not _connection_error(exc):
                raise
            gathered = f"Der Stack ließ sich bei Portainer nicht lesen: {_lost(exc)}. Geändert wurde nichts."
        if isinstance(gathered, str):
            return ActionResult(success=False, error=gathered)
        stack, compose, env = gathered
        values = secret_values(env)
        text = compose.decode("utf-8")
        if blockers := move_blockers(text, stack.working_dir):
            return ActionResult(success=False, error=" ".join(_sentence(b) for b in blockers) + " Geändert wurde nichts.")
        if (foreign := foreign_file(text, stack)) is not None:
            return ActionResult(success=False, error=f"{foreign} Geändert wurde nichts.")
        root = await self._move.new.root(host.id)
        target = new_stack(project, root)
        # Die `.env` im Ordner liefert Compose die Werte für `${…}` in der Datei, so wie Portainer sie beim Ausrollen setzt.
        extra = ((f"{target.working_dir}/.env", env),) if env else ()
        if env and uses_stack_env(text):
            extra += ((f"{target.working_dir}/{ENV_NAME}", env),)

        def vet(config: dict[str, Any]) -> str | None:
            found = resolved_blockers(config, target.working_dir, stack.working_dir)
            return " ".join(_sentence(b) for b in found) if found else None

        created: list[tuple[str, str]] = []
        async with self._ctx.exec.sftp(host) as sftp:
            try:
                problem = await self._create.lay_out(req, sftp, host, target, root, compose, created, extra, vet=vet)
            except BaseException:
                await _shielded_undo(sftp, created)
                raise
            if problem is not None:
                left = await _shielded_undo(sftp, created)
                return ActionResult(success=False, error=_sentence(scrub(problem, values)) + _leftover(left) + " Am Stack hat sich nichts geändert.")
        before = sum(1 for m in stack.members if m.state == "running")
        return await self._roll_out(host, target, created, values, started, before)

    async def _roll_out(
        self, host: Any, target: Stack, created: list[tuple[str, str]], values: list[str], started: float, before: int,
    ) -> ActionResult:
        """Rollt im neuen Ordner aus und übernimmt erst, wenn dort Container laufen; sonst räumt es den Ordner wieder weg."""
        from . import after_check as ac  # spät: after_check braucht capabilities

        folder = target.working_dir
        cut, problem = None, None
        try:
            result = await self._ctx.exec.run_argv(host, cmd_move_up(target), timeout_s=UP_TIMEOUT_S)
            if result.exit_code != 0:
                problem = scrub(failure_line(scrub(result.stderr or result.stdout, values)), values)
        except BaseException as exc:
            if not isinstance(exc, Exception) or not _connection_error(exc):
                # Abbruch (Zeitgrenze der Freigabe, Neustart): Läuft schon etwas aus dem neuen Ordner, wenigstens merken.
                await asyncio.shield(self._adopt_if_moved(host, target))
                raise
            # Ohne Antwort ist offen, ob Compose auf dem Server schon läuft; es lebt auch nach einem Abriss der Verbindung weiter.
            cut = (f"Neu ausrollen hat nach {UP_TIMEOUT_S // 60} Minuten nicht geendet" if type(exc) is TimeoutError
                   else f"Beim Ausrollen brach die Verbindung ab ({_lost(exc)})")
        duration_ms = int((time.monotonic() - started) * 1000)
        rows = await self._move.working_dirs(host, target.project)
        moved = rows is not None and any(d == folder for d, _ in rows)
        if cut is not None:
            if moved:
                await self._adopt(host, target)
            return ActionResult(success=False, duration_ms=duration_ms, error=(
                f"{cut}; Compose kann auf dem Server noch weiterlaufen, und ein Teil der Container ist dann schon neu. Sieh in ein paar "
                f"Minuten im Reiter Stacks nach: Läuft der Stack aus {folder}, ist er übernommen oder lässt sich dort übernehmen; sonst "
                f"lösche den Ordner {folder} im Dateimanager und versuch es noch einmal. {KEEP_IN_PORTAINER}"
            ))
        if rows is None:
            # Ohne Blick auf die Container bleibt der Ordner: Laufen sie schon daraus, bräuchten sie ihn.
            failed = f"Neu ausrollen ist gescheitert: {_sentence(problem)} " if problem else ""
            return ActionResult(success=False, duration_ms=duration_ms, error=(
                f"{failed}Ob die Container jetzt aus {folder} laufen, ließ sich danach nicht lesen. Sieh im Reiter Stacks nach: Läuft "
                f"der Stack aus {folder}, übernimm ihn dort; sonst lösche den Ordner {folder} im Dateimanager und versuch es noch "
                f"einmal. {KEEP_IN_PORTAINER}"
            ))
        if problem is not None or not moved:
            reason = f"Neu ausrollen ist gescheitert: {_sentence(problem)}" if problem else "Die Container laufen nicht aus dem neuen Ordner."
            if moved:
                adopted = await self._adopt(host, target)
                # `up` ohne Änderung an der Datei lässt Container aus dem alten Ordner stehen; erst Herunterfahren holt alle um.
                if rest := sum(1 for d, _ in rows if d.startswith(PORTAINER_DIR)):
                    where = f"Ein Teil der Container kommt schon aus {folder}, {rest} noch aus dem Ordner bei Portainer"
                    fix = (f"Behebe den Grund, fahre ihn dann im Reiter Stacks herunter und wende ihn wieder an (die Volumes bleiben); "
                           f"dann kommt alles aus {folder}.")
                else:
                    where, fix = f"Die Container kommen schon aus {folder}", "Behebe den Grund und wende ihn im Reiter Stacks noch einmal an."
                then = f"der Stack ist übernommen. {fix}" if adopted is None else (
                    f"übernehmen ließ er sich nicht ({adopted}). Übernimm ihn im Reiter Stacks. {fix}")
                return ActionResult(success=False, duration_ms=duration_ms, error=f"{reason} {where}; {then} {KEEP_IN_PORTAINER}")
            try:
                async with self._ctx.exec.sftp(host) as sftp:
                    left = await _shielded_undo(sftp, created)
            except Exception as exc:
                if not _connection_error(exc):
                    raise
                left = list(created)
            running = sum(1 for d, state in rows if d.startswith(PORTAINER_DIR) and state == "running")
            if running >= before:
                now = "Die Container laufen weiter wie unter Portainer." if before else "Der Stack bleibt gestoppt wie vorher."
            elif running:
                now = (f"Von den alten Containern laufen nur noch {running} von {before}; rolle den Stack in Portainer noch einmal aus, "
                       "dann läuft er wie vorher.")
            else:
                now = "Die alten Container laufen nicht mehr; rolle den Stack in Portainer noch einmal aus, dann läuft er wie vorher."
            return ActionResult(success=False, duration_ms=duration_ms, error=(
                f"{reason}{_leftover(left) if left else ' Den neuen Ordner hat Nodvard Deck wieder entfernt.'} {now}"
            ))
        adopted = await self._adopt(host, target)
        after = await self._move.running(host, target.project)
        if adopted is not None:
            return ActionResult(success=False, duration_ms=duration_ms, error=(
                f"Der Stack läuft jetzt aus {folder}, Nodvard Deck konnte sich ihn aber nicht merken ({adopted}). Übernimm ihn im "
                f"Reiter Stacks. {KEEP_IN_PORTAINER}"
            ))
        moved_text = f"Stack „{target.project}“ liegt jetzt in {folder} und ist übernommen."
        if isinstance(after, str):
            return ActionResult(success=False, exit_code=0, output=moved_text, duration_ms=duration_ms, error=(
                f"Prüfung danach nicht möglich: {after} {KEEP_IN_PORTAINER}"
            ))
        verdict, _ = await ac.check_running(self._ctx, host, [m.id for m in after.members])
        line = ac.result_line(verdict)
        output = f"{moved_text} {line} {KEEP_IN_PORTAINER}"
        if verdict.ok:
            return ActionResult(success=True, exit_code=0, output=output, duration_ms=duration_ms)
        return ActionResult(success=False, exit_code=0, output=moved_text, duration_ms=duration_ms, error=f"{line} {KEEP_IN_PORTAINER}")

    async def _adopt(self, host: Any, target: Stack) -> str | None:
        """Merkt sich den Stack; `None` oder warum es nicht ging."""
        try:
            await self._adopted.add(host.id, target)
        except Exception as exc:  # noqa: BLE001 - die Dateien stehen, nur das Merken fehlt
            return str(exc) or type(exc).__name__
        return None

    async def _adopt_if_moved(self, host: Any, target: Stack) -> None:
        rows = await self._move.working_dirs(host, target.project)
        if rows is not None and any(d == target.working_dir for d, _ in rows):
            await self._adopt(host, target)

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        payload = req.payload or {}
        return DryRunReport(would_change=True, summary=(
            f"Würde den Portainer-Stack „{payload.get('project', '?')}“ nach {payload.get('folder', '?')} holen und dort neu ausrollen."
        ))


async def _shielded_undo(sftp: Any, created: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return await asyncio.shield(_undo(sftp, created))


def move_routes(move: PortainerMove, editor: StackEditor, docker_host: Any, current_actor: Any) -> APIRouter:
    router = APIRouter()

    async def _plan(host_id: str, project: str) -> tuple[Any, dict[str, Any]]:
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        host = await docker_host(host_id)
        return host, await move.plan(host, project)

    @router.get("/hosts/{host_id}/stacks/{project}/from-portainer")
    async def move_plan(host_id: str, project: str) -> dict:
        """Wohin ein Portainer-Stack zöge und warum es nicht geht."""
        return (await _plan(host_id, project))[1]

    @router.post("/hosts/{host_id}/stacks/{project}/from-portainer", status_code=status.HTTP_202_ACCEPTED)
    async def move_stack(host_id: str, project: str, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt den Umzug vor (Gate-Aktion `docker.stack_from_portainer`, immer mit hohem Risiko)."""
        host, plan = await _plan(host_id, project)
        if plan["problem"] is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan["problem"])
        reason = (f"Portainer-Stack „{project}“ nach {plan['folder']} holen: Datei und Variablen aus Portainer, dann neu ausrollen. "
                  "Die Container werden einmal neu erstellt, die Volumes bleiben.")
        decision = await editor.propose(ActionRequest(
            action_type=MOVE_ACTION, host_ref=host.id, risk=Risk.HIGH, proposed_by=actor, reason=reason,
            payload={"project": project, "folder": plan["folder"], "portainer_id": plan["portainer_id"]},
        ))
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": Risk.HIGH.value, "detail": decision.detail}

    return router
