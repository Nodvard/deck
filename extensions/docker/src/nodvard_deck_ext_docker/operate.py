"""Modus „Nur bedienen“: Auf einem Server mit dieser Markierung ist der Zugang von Nodvard Deck nicht in der Gruppe docker
(das wäre praktisch root), sondern darf per sudo nur das Hilfsskript `docker-root`. Jeder Befehl des Moduls geht dort durch
`OperateExec`; nur was das Skript kennt, läuft, alles andere wird mit Grund abgelehnt."""

from __future__ import annotations

import shlex
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from nodvard_sdk import root_helper_path

from . import commands
from .image_apply import VERIFY_FORMAT

HELPER = root_helper_path("docker")
DEFAULT_TAG = "docker-bedienen"
REFUSED_EXIT = 126
# Exit 0, wenn der Zugang nur das Hilfsskript hat, sonst 1 (nie 127 = „nicht installiert“); `sh -c` für jede Login-Shell.
# Root prüft nur ROOT_CHECK; GROUP_CHECK fragt wie das Hilfsskript nur den Dienst unter /var/run/docker.sock.
ROOT_CHECK = r"""sh -c 'test "$(id -u)" != 0'"""
GROUP_CHECK = (
    r"""sh -c 'test "$(id -u)" = 0 && exit 0; id -nG "$(id -un)" | tr " " "\n" | grep -qx docker && exit 1; """
    r"""unset DOCKER_API_VERSION DOCKER_CERT_PATH DOCKER_TLS DOCKER_TLS_VERIFY; """
    r"""docker -H unix:///var/run/docker.sock ps -q >/dev/null 2>&1 && exit 1; exit 0'"""
)
FULL_SUDO_CHECK = (
    r"""sh -c 'test "$(id -u)" = 0 && exit 0; sudo -n -l 2>/dev/null | grep -E "^[[:space:]]*\((root|ALL)([[:space:]]*:[^)]*)?\)[[:space:]]*"""
    r"""([A-Z_]+:[[:space:]]*)*ALL[[:space:]]*\$" | grep -q "NOPASSWD:" && exit 1; exit 0'"""
)


def refused_text(full_tag: str = "docker") -> str:
    """Warum etwas auf einem Server im Modus „Nur bedienen“ nicht geht und was man für mehr tut."""
    return (
        "Auf diesem Server ist Docker im Modus „Nur bedienen“ eingerichtet: Nodvard Deck darf dort Container ansehen, ihre Logs "
        "lesen und sie starten, stoppen und neu starten, aber nicht mehr. Für alles andere gib dem Server unter Einstellungen, "
        f"Server & Zugänge die Markierung „{full_tag}“: Dann gilt er als voller Docker-Server, und die Einrichtung nimmt den "
        "Zugang in die Gruppe docker auf, die praktisch Root-Rechte gibt."
    )


REFUSED = refused_text()
MISSING = (
    "Auf diesem Server fehlt das Hilfsskript docker-root, oder sudo erlaubt es dem Zugang nicht ohne Passwort. Was fehlt, zeigt "
    "die Anforderung „Docker nur bedienen“ unter Einstellungen, Server & Zugänge."
)
_VERBS = ("start", "stop", "restart")
_SUDO_TROUBLE = ("sudo: a password is required", "sudo: a terminal is required", "is not allowed to execute", "may not run sudo",
                 "sudo: command not found", "sudo: not found")


def _logs(rest: list[str]) -> list[str] | None:
    """`logs --tail N --timestamps [--since S] [--follow] NAME` -> `logs N S|- 0|1 NAME`."""
    if rest[:2] != ["logs", "--tail"] or len(rest) < 5 or rest[3] != "--timestamps":
        return None
    tail, options, name = rest[2], rest[4:-1], rest[-1]
    since = "-"
    if options[:1] == ["--since"] and len(options) >= 2:
        since, options = options[1], options[2:]
    follow = options == ["--follow"]
    if options and not follow:
        return None
    return ["logs", tail, since, "1" if follow else "0", name]


def helper_args(command: str) -> list[str] | None:
    """Die Argumente für `docker-root` zu einem Befehl aus `commands.py` oder `after_check`, oder `None`, wenn das Skript
    ihn nicht kennt."""
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if words[:1] != ["docker"]:
        return None
    rest = words[1:]
    if rest == ["ps", "-a", "--format", commands.PS_FORMAT]:
        return ["ps"]
    if rest == ["stats", "--no-stream", "--format", commands.STATS_FORMAT]:
        return ["stats"]
    if rest[:5] == ["inspect", "--type", "container", "--format", commands.PORTS_FORMAT] and len(rest) > 5:
        return ["ports", *rest[5:]]
    if len(rest) == 4 and rest[:3] == ["inspect", "--type", "container"]:
        return ["inspect", rest[3]]
    if (len(rest) == 4 and rest[:3] == ["inspect", "-f", "{{.Id}}"]) or (
        len(rest) == 6 and rest[:5] == ["inspect", "--type", "container", "-f", "{{.Id}}"]
    ):
        return ["id", rest[-1]]
    if len(rest) == 6 and rest[:5] == ["inspect", "--type", "container", "-f", "{{.Id}}|{{.State.Status}}"]:
        return ["id-state", rest[5]]
    if rest[:4] == ["container", "inspect", "--format", VERIFY_FORMAT] and len(rest) > 4:
        return ["state", *rest[4:]]
    if len(rest) == 2 and rest[0] in _VERBS:
        return rest
    return _logs(rest)


def operate_command(command: str) -> str | None:
    """Derselbe Befehl über `sudo -n docker-root`, oder `None`, wenn er im Modus „Nur bedienen“ nicht geht."""
    args = helper_args(command)
    return shlex.join(["sudo", "-n", HELPER, *args]) if args else None


def refused(text: str = REFUSED) -> Any:
    return SimpleNamespace(stdout="", stderr=text, exit_code=REFUSED_EXIT, duration_ms=0)


def _explained(result: Any) -> Any:
    """Scheitert schon sudo oder fehlt das Skript, ein Satz dazu statt der rohen Meldung von sudo."""
    stderr = str(getattr(result, "stderr", "") or "")
    missing = HELPER in stderr and ("command not found" in stderr or "No such file" in stderr)
    if result.exit_code == 0 or not (missing or any(text in stderr for text in _SUDO_TROUBLE)):
        return result
    return SimpleNamespace(stdout=result.stdout, stderr=MISSING, exit_code=result.exit_code, duration_ms=getattr(result, "duration_ms", 0))


def tags(settings: dict[str, Any]) -> tuple[str, str]:
    """(Markierung für volle Docker-Server, Markierung für „Nur bedienen“)."""
    return settings.get("docker_host_tag") or "docker", settings.get("docker_operate_tag") or DEFAULT_TAG


def is_operate_host(host: Any, settings: dict[str, Any]) -> bool:
    full, operate = tags(settings)
    host_tags = getattr(host, "tags", None) or []
    return operate in host_tags and full not in host_tags


def is_docker_host(host: Any, settings: dict[str, Any]) -> bool:
    host_tags = getattr(host, "tags", None) or []
    return any(tag in host_tags for tag in tags(settings))


def not_full_host(host: Any, settings: dict[str, Any], missing: str = "Kein Docker-Host.") -> str | None:
    """Warum `host` für Stacks, Registries und Image-Updates nicht taugt: der Satz zum Modus „Nur bedienen“, sonst `missing`.
    `None`, wenn er ein voller Docker-Server ist."""
    full = tags(settings)[0]
    if full in (getattr(host, "tags", None) or []):
        return None
    return refused_text(full) if is_operate_host(host, settings) else missing


async def docker_hosts(ctx: Any) -> list[tuple[Any, bool]]:
    """Alle Docker-Server als (Server, nur bedienen); wer beide Markierungen hat, gilt als voller Docker-Server."""
    full_tag, operate_tag = tags(await ctx.settings.get())
    full = list(await ctx.hosts.list(tag=full_tag))
    known = {host.id for host in full}
    return [(host, False) for host in full] + [(host, True) for host in await ctx.hosts.list(tag=operate_tag) if host.id not in known]


class OperateExec:
    """`ctx.exec` des Moduls: Auf Servern im Modus „Nur bedienen“ laufen nur Befehle, die `docker-root` kennt, über sudo.
    Bewusst ohne Durchreichen: Eine neue Funktion des Kerns gibt es hier erst, wenn sie entschieden ist."""

    def __init__(self, inner: Any, operate: Callable[[Any], Awaitable[bool]], full_tag: Callable[[], Awaitable[str]] | None = None) -> None:
        self._inner = inner
        self._operate = operate
        self._full_tag = full_tag

    async def _why(self) -> str:
        return refused_text(await self._full_tag()) if self._full_tag else REFUSED

    async def run(self, host: Any, command: str, **kwargs: Any) -> Any:
        if not await self._operate(host):
            return await self._inner.run(host, command, **kwargs)
        mapped = operate_command(command)
        if mapped is None:
            return refused(await self._why())
        return _explained(await self._inner.run(host, mapped, **kwargs))

    async def run_argv(self, host: Any, argv: Any, **kwargs: Any) -> Any:
        if await self._operate(host):
            return refused(await self._why())
        return await self._inner.run_argv(host, argv, **kwargs)

    @asynccontextmanager
    async def stream(self, host: Any, command: str) -> AsyncIterator[Any]:
        if await self._operate(host):
            mapped = operate_command(command)
            if mapped is None:
                raise PermissionError(await self._why())
            command = mapped
        async with self._inner.stream(host, command) as chunks:
            yield chunks

    @asynccontextmanager
    async def stream_argv(self, host: Any, argv: Any) -> AsyncIterator[Any]:
        if await self._operate(host):
            raise PermissionError(await self._why())
        async with self._inner.stream_argv(host, argv) as chunks:
            yield chunks

    @asynccontextmanager
    async def sftp(self, host: Any) -> AsyncIterator[Any]:
        if await self._operate(host):
            raise PermissionError(await self._why())
        async with self._inner.sftp(host) as client:
            yield client

    @asynccontextmanager
    async def open_shell(self, host: Any, **kwargs: Any) -> AsyncIterator[Any]:
        if await self._operate(host):
            raise PermissionError(await self._why())
        async with self._inner.open_shell(host, **kwargs) as process:
            yield process


class OperateContext:
    """Der Kontext des Moduls mit `OperateExec` statt `exec`; alles andere kommt unverändert vom Kern."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

        async def operate(host: Any) -> bool:
            return is_operate_host(host, await ctx.settings.get())

        async def full_tag() -> str:
            return tags(await ctx.settings.get())[0]

        self.exec = OperateExec(ctx.exec, operate, full_tag)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)
