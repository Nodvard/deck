"""Konsole IN einem Container (`docker exec -it`), ueber das Terminal des Kerns.

Erfuellt `nodvard_sdk.capabilities.ScopedTerminalTarget` mit `scope = "container"`: Die Seite oeffnet
`/terminal?host=<id>&scope=container&ref=<name>`, der Kern legt die Sitzung an wie fuer ein normales Terminal
(Berechtigung `hosts.execute`, Einmal-Ticket, Leerlauf-Grenze, Protokoll `terminal.open`/`terminal.close` mit
`scope` und `ref`) und fragt hier nach der Shell.

Der Name wandert in ein SSH-Kommando und muss deshalb durch `CONTAINER_NAME_RE` -- vor `can_open_in` UND noch
einmal vor dem Oeffnen. Nur Server mit dem Docker-Tag, wie bei allen Container-Aktionen. In welcher Shell man landet,
entscheidet der Container: bash, wenn es sie gibt, sonst sh. Gibt es keine (Images ohne Shell) oder laeuft der
Container nicht, zeigt das Terminal Dockers eigene Meldung und endet. Nie in den Container von Nodvard Deck selbst:
darin liegen Datenbank und Tresor-Schluessel (wie Stoppen und Loeschen nur direkt auf dem Host).

Gilt auch, wenn die Erweiterung "Terminal" aus ist: Diese Shell haengt nur am Recht `hosts.execute`.
"""

from __future__ import annotations

import shlex
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from .capabilities import CONTAINER_NAME_RE, is_own_container

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

PICK_SHELL = "if command -v bash >/dev/null 2>&1; then exec bash; else exec sh; fi"


def exec_command(container: str) -> str:
    """`docker exec -it <name> sh -c '<bash, sonst sh>'`. `ValueError` bei einem Namen, der nicht Dockers Regel folgt."""
    if not CONTAINER_NAME_RE.match(container):
        raise ValueError("Ungültiger Containername.")
    return f"docker exec -it {container} sh -c {shlex.quote(PICK_SHELL)}"


class ShellRefused(RuntimeError):
    """Fertiger Satz fuer die Fehlermeldung des Terminals (`readable`, siehe docs/02)."""

    readable = True


class _ShellSession:
    """Erfuellt `nodvard_sdk.capabilities.TerminalSession` strukturell (wie die terminal-Erweiterung)."""

    def __init__(self, process: Any, stack: AsyncExitStack) -> None:
        self._process = process
        self._stack = stack

    async def read(self):
        while True:
            data = await self._process.stdout.read(65536)
            if not data:
                return
            yield data

    async def write(self, data: bytes) -> None:
        self._process.stdin.write(data)
        await self._process.stdin.drain()

    async def resize(self, cols: int, rows: int) -> None:
        self._process.change_terminal_size(cols, rows)

    async def close(self) -> None:
        await self._stack.aclose()

    @property
    def exit_code(self) -> int | None:
        return self._process.exit_status


class ContainerShell:
    """Erfuellt `nodvard_sdk.capabilities.ScopedTerminalTarget` fuer Container auf Docker-Hosts."""

    scope = "container"

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def can_open_in(self, host: Any, ref: str) -> bool:
        if not host.has_credential or not CONTAINER_NAME_RE.match(ref):
            return False
        tag = (await self._ctx.settings.get()).get("docker_host_tag") or "docker"
        return tag in host.tags

    async def open_in(self, host: Any, ref: str, *, cols: int, rows: int) -> _ShellSession:
        command = exec_command(ref)
        inspected = await self._ctx.exec.run(host, f"docker inspect --type container -f '{{{{.Id}}}}' {ref}", timeout_s=15)
        if inspected.exit_code == 0 and is_own_container(inspected.stdout.strip()):
            raise ShellRefused(
                f"„{ref}“ ist der Container von Nodvard Deck selbst. Eine Konsole darin gibt es hier nicht "
                "(Datenbank und Tresor-Schlüssel liegen darin) – nur direkt auf dem Server."
            )
        stack = AsyncExitStack()
        try:
            process = await stack.enter_async_context(self._ctx.exec.open_shell(host, cols=cols, rows=rows, command=command))
        except BaseException:
            await stack.aclose()
            raise
        return _ShellSession(process, stack)
