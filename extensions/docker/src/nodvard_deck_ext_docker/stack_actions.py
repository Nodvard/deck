"""Stack-Aktionen für übernommene Stacks: Anwenden, Stoppen, Herunterfahren und Images holen, nur nach dem Gate und im
Ordner des Stacks, damit `${PWD}` in der Datei dasselbe ergibt wie beim Start von Hand."""

from __future__ import annotations

import re
import time
from typing import Any

from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.errors import HostUnreachable
from nodvard_sdk.types import Risk

from .adopt import AdoptedStacks
from .capabilities import is_own_container
from .compose_errors import scrub_compose_output
from .image_apply import PROJECT_RE, is_self_image
from .locks import StackLocks, busy_text
from .operate import still_full
from .stacks import DockerStacks, Stack, StackProblem, compose_argv

VERBS: dict[str, tuple[str, ...]] = {
    "docker.stack_up": ("up", "-d"),
    "docker.stack_stop": ("stop",),
    "docker.stack_down": ("down",),
    "docker.stack_pull": ("pull",),
}
"""`down` immer ohne `-v`: Die Volumes mit den Daten bleiben. `up` ohne `--profile`, davon geht die Prüfung danach aus."""
LABELS = {"docker.stack_up": "Anwenden", "docker.stack_stop": "Stoppen", "docker.stack_down": "Herunterfahren", "docker.stack_pull": "Images holen"}
DONE = {
    "docker.stack_up": "angewendet", "docker.stack_stop": "gestoppt", "docker.stack_down": "heruntergefahren",
    "docker.stack_pull": "hat seine Images neu geholt",
}
TIMEOUT_S = {"docker.stack_up": 2700, "docker.stack_stop": 900, "docker.stack_down": 900, "docker.stack_pull": 2700}
"""Anwenden und Holen laden auf einem Pi unter Umständen große Images, wie das Image-Update."""
NO_FOLDER_EXIT = 97
IN_FOLDER = f'cd -- "$1" 2>/dev/null || exit {NO_FOLDER_EXIT}; shift; exec "$@"'
"""Wechselt in den Ordner des Stacks und startet dann den Befehl; Ordner und Befehl kommen als eigene Wörter, nie als Text."""
SERVICES_TIMEOUT_S = 60
_PROJECT_SCHEMA = {"type": "object", "properties": {"project": {"type": "string", "pattern": PROJECT_RE.pattern.replace("\\Z", "$")}}, "required": ["project"]}
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_PROGRESS_RE = re.compile(r"^(?:Container|Image|Network|Volume)\s+\S+\s+[A-Z][a-z]+\b")
"""Fortschrittszeilen von Compose wie „Container app-db-1 Error …“; der Grund steht meist in der Zeile danach."""
_WARNING_RE = re.compile(r"^(?:WARN\b|time=\S+ level=warning\b)")
_SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def stack_action_specs(tag: str = "docker") -> list[ActionSpec]:
    common: dict[str, Any] = {"permissions": ["hosts.execute"], "host_bound": True, "host_tags": [tag], "params_schema": _PROJECT_SCHEMA}
    return [
        ActionSpec(
            action_type="docker.stack_up", label="Stack anwenden", icon="play", default_risk=Risk.MEDIUM,
            description="Startet einen übernommenen Stack nach seiner Compose-Datei (docker compose up -d).",
            confirm_text="Der Stack startet nach seiner Compose-Datei. Geänderte Dienste werden dabei neu erstellt und sind kurz "
            "nicht erreichbar. Fortfahren?", **common,
        ),
        ActionSpec(
            action_type="docker.stack_stop", label="Stack stoppen", icon="square", default_risk=Risk.MEDIUM,
            description="Stoppt alle Container eines übernommenen Stacks (docker compose stop).",
            confirm_text="Alle Container des Stacks werden gestoppt. Seine Dienste sind nicht erreichbar, bis du ihn wieder "
            "anwendest. Fortfahren?", **common,
        ),
        ActionSpec(
            action_type="docker.stack_down", label="Stack herunterfahren", icon="power", default_risk=Risk.HIGH,
            description="Entfernt Container und Netze eines übernommenen Stacks (docker compose down, ohne Volumes).",
            confirm_text="Container und Netze des Stacks werden gelöscht. Was nur im Container lag und nicht in einem Volume "
            "oder Ordner, ist danach weg. Die Volumes bleiben, und „Anwenden“ legt die Container neu an. Fortfahren?", **common,
        ),
        ActionSpec(
            action_type="docker.stack_pull", label="Images des Stacks holen", icon="download", default_risk=Risk.MEDIUM,
            description="Lädt die Images eines übernommenen Stacks neu herunter (docker compose pull), ohne Container neu zu starten.",
            **common,
        ),
    ]


def _in_folder(stack: Stack, *args: str) -> list[str]:
    return ["sh", "-c", IN_FOLDER, "sh", stack.working_dir, *compose_argv(stack, *args)]


def cmd_verb(stack: Stack, action_type: str) -> list[str]:
    """Die Wörter für `ctx.exec.run_argv`: im Ordner des Stacks `docker compose … <Verb>`."""
    return _in_folder(stack, *VERBS[action_type])


def cmd_services(stack: Stack) -> list[str]:
    """Die Dienste, die ohne Profil starten, gelesen im Ordner des Stacks (eine `.env` dort kann Profile setzen)."""
    return _in_folder(stack, "config", "--services")


def cmd_all_services(stack: Stack) -> list[str]:
    """Alle Dienste der Datei, auch die mit Profil, gelesen im Ordner des Stacks."""
    return _in_folder(stack, "--profile", "*", "config", "--services")


FAILURE_LIMIT = 300
"""So lang wird der Grund höchstens; ein Wert kann deshalb nur bis hierhin angeschnitten am Ende stehen."""


def failure_line(text: str, limit: int | None = FAILURE_LIMIT) -> str:
    """Der Grund aus der Ausgabe von Compose: die letzte eigene Zeile, bevorzugt eine mit „error“; Fortschritt und
    Warnungen zählen nur, wenn sonst nichts dasteht; nie mit Inhalt aus einer `.env`."""
    lines = [line.strip() for line in scrub_compose_output(_ANSI_RE.sub("", text or "")).splitlines() if line.strip()]
    own = [line for line in lines if not _PROGRESS_RE.match(line) and not _WARNING_RE.match(line)]
    errors = [line for line in own if "error" in line.lower()]
    loud = [line for line in lines if "error" in line.lower()]
    return ((errors or own or loud or lines or ["Compose hat ohne Angabe eines Grundes abgebrochen."])[-1])[:limit]


def _short(text: str, limit: int = 1500) -> str:
    clean = _ANSI_RE.sub("", text or "").strip()
    return clean if len(clean) <= limit else "…" + clean[-limit:]


_SSH_MODULES = ("asyncssh", "nodvard_deck.core.ssh")


def _connection_error(exc: BaseException) -> bool:
    """Ein Abbruch der Verbindung: `OSError`, `HostUnreachable` oder ein Fehler der SSH-Bibliothek bzw. der SSH-Schicht des
    Kerns (`ConnectionLost`, eine abgebrochene Anmeldung); die erkennt das Modul am Modul der Klasse."""
    if isinstance(exc, (OSError, HostUnreachable)):
        return True
    return any(cls.__module__ in _SSH_MODULES or cls.__module__.startswith("asyncssh.") for cls in type(exc).__mro__)


def _broken(exc: BaseException, action_type: str, timeout_s: int) -> str:
    """Der Grund, wenn der Aufruf selbst abbricht: das eigene Zeitlimit (reines `TimeoutError`), ein Fehler mit fertigem
    Satz des Kerns oder eine abgerissene Verbindung."""
    look = "Sieh im Reiter Stacks nach, ob sich am Stack schon etwas geändert hat."
    if type(exc) is TimeoutError:
        after = ("Das Holen kann auf dem Server weiterlaufen." if action_type == "docker.stack_pull" else
                 "Compose kann auf dem Server weiterlaufen, der Stack ist dann vielleicht nur zum Teil umgestellt.")
        return f"Nach {timeout_s // 60} Minuten ohne Ende abgebrochen. {after} Sieh im Reiter Stacks nach, bevor du es noch einmal versuchst."
    text = str(exc).strip()
    if not getattr(exc, "readable", False) or not text:
        return f"Die Verbindung zum Server ist mitten im Lauf abgebrochen. Compose kann auf dem Server weitergelaufen sein. {look}"
    return f"{text}{'' if text.endswith(('.', '!', '?')) else '.'} {look}"


def _lost(exc: BaseException) -> str:
    """Kurzer Grund, wenn ein Aufruf an den Server selbst abbricht, klein geschrieben für Sätze mit Doppelpunkt oder Klammer."""
    text = str(exc).strip().rstrip(".")
    if type(exc) is TimeoutError:
        return "der Server hat nicht rechtzeitig geantwortet"
    if getattr(exc, "readable", False) and text:
        return text
    return "die Verbindung zum Server ist abgebrochen"


class DockerStackExecutor:
    """Führt die Stack-Aktionen aus, nur für übernommene Stacks und nie für Nodvard Deck selbst."""

    action_types = frozenset(VERBS)

    def __init__(self, ctx: Any, stacks: DockerStacks, adopted: AdoptedStacks, locks: StackLocks | None = None) -> None:
        self._ctx = ctx
        self._stacks = stacks
        self._adopted = adopted
        self.locks = locks or StackLocks()

    async def _target(self, req: ActionRequest) -> tuple[Any, Stack] | str:
        if req.action_type not in VERBS:
            return f"Unbekannte Aktion '{req.action_type}'."
        return await self.target(req.host_ref, str(req.payload.get("project") or ""))

    async def target(self, host_ref: str | None, project: str) -> tuple[Any, Stack] | str:
        """(Host, Stack) eines übernommenen Stacks oder der Grund, warum an ihm nichts geändert wird."""
        if not PROJECT_RE.match(project):
            return f"Ungültiger Stack-Name: {project!r}"
        if not hasattr(self._ctx.exec, "run_argv"):
            return "Stack-Aktionen brauchen eine neuere Version von Nodvard Deck."
        host = await self._ctx.hosts.get(host_ref) if host_ref else None
        if host is None:
            return "Server nicht gefunden."
        if why := await still_full(self._ctx, host):
            return why
        entry = self._adopted.for_host(host.id).get(project)
        if entry is None:
            return f"Der Stack „{project}“ ist nicht übernommen. Übernimm ihn zuerst im Reiter Stacks."
        try:
            found = [s for s in await self._stacks.stacks(host, project) if s.project == project]
        except StackProblem as exc:
            return str(exc)
        running = found[0] if found else None
        if running is not None:
            if any(is_own_container(m.id) or is_self_image(m.image) for m in running.members):
                return "Das ist Nodvard Deck selbst. Diesen Stack bedienst du besser direkt auf dem Server."
            note = entry.note(running)
            if note:
                return note
        return host, entry.stack(project)

    async def execute(self, req: ActionRequest) -> ActionResult:
        target = await self._target(req)
        if isinstance(target, str):
            return ActionResult(success=False, error=target)
        host, stack = target
        with self.locks.hold(host.id, stack.project, LABELS[req.action_type]) as busy:
            if busy is not None:
                return ActionResult(success=False, error=busy_text(busy))
            return await self.run(host, stack, req.action_type)

    async def run(self, host: Any, stack: Stack, action_type: str) -> ActionResult:
        """Führt ein Verb im Ordner des Stacks aus; nach `up` mit der Prüfung, ob die Dienste laufen."""
        timeout_s = TIMEOUT_S[action_type]
        started = time.monotonic()
        try:
            result = await self._ctx.exec.run_argv(host, cmd_verb(stack, action_type), timeout_s=timeout_s)
        except Exception as exc:
            if not _connection_error(exc):
                raise
            return ActionResult(success=False, duration_ms=int((time.monotonic() - started) * 1000), error=_broken(exc, action_type, timeout_s))
        duration_ms = int((time.monotonic() - started) * 1000)
        done = f"Stack „{stack.project}“ {DONE[action_type]}."
        if result.exit_code == NO_FOLDER_EXIT:
            return ActionResult(
                success=False, exit_code=result.exit_code, duration_ms=duration_ms,
                error=f"Den Ordner „{stack.working_dir}“ gibt es auf dem Server nicht mehr, oder er ist nicht lesbar. Liegt der "
                "Stack jetzt woanders, hebe die Übernahme auf und übernimm ihn neu.",
            )
        if result.exit_code != 0:
            stderr = scrub_compose_output(result.stderr)
            reason = failure_line(result.stderr or result.stdout)
            return ActionResult(
                success=False, exit_code=result.exit_code, error=reason, output=_short(stderr) or None, duration_ms=duration_ms,
            )
        if action_type == "docker.stack_up":
            return await self._checked(host, stack, done, duration_ms)
        return ActionResult(success=True, exit_code=0, output=done, duration_ms=duration_ms)

    async def _members(self, host: Any, stack: Stack) -> list[str] | str:
        """Die Container der Dienste, die `up` startet, auch beendete; sonst der Grund, warum die Prüfung ausfällt."""
        try:
            listed = await self._ctx.exec.run_argv(host, cmd_services(stack), timeout_s=SERVICES_TIMEOUT_S)
        except Exception as exc:
            if not _connection_error(exc):
                raise
            if type(exc) is TimeoutError:
                return "Prüfung danach nicht möglich: Der Server hat beim Lesen der Dienste nicht rechtzeitig geantwortet."
            return "Prüfung danach nicht möglich: Der Server war nicht mehr erreichbar."
        services = {line.strip() for line in listed.stdout.splitlines() if _SERVICE_RE.match(line.strip())}
        if listed.exit_code != 0:
            return "Prüfung danach nicht möglich: Die Dienste der Datei ließen sich nicht lesen."
        if not services:
            return "Prüfung danach übersprungen: Die Datei hat keinen Dienst, der ohne Profil startet."
        try:
            found = [s for s in await self._stacks.stacks(host, stack.project) if s.project == stack.project]
        except StackProblem:
            return "Prüfung danach nicht möglich: Die Container ließen sich nicht lesen."
        refs = [m.id for s in found for m in s.members if m.service in services]
        return refs or "Prüfung danach übersprungen: Docker zeigt zu den Diensten der Datei keinen Container."

    async def _checked(self, host: Any, stack: Stack, done: str, duration_ms: int) -> ActionResult:
        """Nach dem Anwenden: Laufen die Container des Stacks und bleiben sie laufen?"""
        from . import after_check as ac  # spät: after_check braucht capabilities

        refs = await self._members(host, stack)
        if isinstance(refs, str):
            return ActionResult(success=True, exit_code=0, output=f"{done}\n{refs}", duration_ms=duration_ms)
        verdict, _ = await ac.check_running(self._ctx, host, refs)
        line = ac.result_line(verdict)
        detail = {"check": {"ok": verdict.ok, "text": verdict.text}}
        if verdict.ok:
            return ActionResult(success=True, exit_code=0, output=f"{done}\n{line}", duration_ms=duration_ms, detail=detail)
        return ActionResult(success=False, exit_code=0, output=done, error=line, duration_ms=duration_ms, detail=detail)

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        project = req.payload.get("project", "?")
        verb = " ".join(VERBS.get(req.action_type, ("?",)))
        extra = " (ohne Volumes)" if req.action_type == "docker.stack_down" else ""
        return DryRunReport(would_change=True, summary=f"Würde für den Stack „{project}“ in seinem Ordner „docker compose {verb}“{extra} ausführen.")
