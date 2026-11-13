"""Konsole in einem Container (`docker exec -it`) über das Terminal des Kerns (`ScopedTerminalTarget`, scope `container`).
Hängt nur am Recht `hosts.execute`, auch wenn die Erweiterung "Terminal" aus ist."""

from __future__ import annotations

import re
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from . import commands
from .capabilities import is_own_container
from .commands import CONTAINER_NAME_RE
from .image_apply import is_self_image

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext


FULL_ID_RE = re.compile(r"^[0-9a-f]{64}\Z")


class ShellRefused(RuntimeError):
    """Fertiger Satz für die Fehlermeldung des Terminals (`readable`, siehe docs/02)."""

    readable = True


class _ShellSession:
    """Strukturell eine `TerminalSession` (wie in der Terminal-Erweiterung)."""

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
    """Konsole in Containern von Docker-Hosts (`ScopedTerminalTarget`); nur Server mit dem Docker-Tag."""

    scope = "container"

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def can_open_in(self, host: Any, ref: str) -> bool:
        if not host.has_credential or not CONTAINER_NAME_RE.match(ref):
            return False
        tag = (await self._ctx.settings.get()).get("docker_host_tag") or "docker"
        return tag in host.tags

    async def open_in(self, host: Any, ref: str, *, cols: int, rows: int) -> _ShellSession:
        # Nie in den Container von Nodvard Deck selbst: darin liegen Datenbank und Tresor-Schlüssel. Kann das nicht
        # geprüft werden, gibt es keine Konsole.
        inspected = await self._ctx.exec.run(host, commands.container_id_image(ref), timeout_s=15)
        lines = inspected.stdout.strip().splitlines()
        container_id, _, image = lines[0].partition("|") if len(lines) == 1 else ("", "", "")
        if inspected.exit_code != 0 or not FULL_ID_RE.match(container_id):
            raise ShellRefused(
                f"Ob „{ref}“ der Container von Nodvard Deck selbst ist, ließ sich nicht prüfen. "
                "Darum gibt es hier keine Konsole darin. Versuche es später noch einmal."
            )
        if is_own_container(container_id) or is_self_image(image):
            raise ShellRefused(
                f"„{ref}“ ist der Container von Nodvard Deck selbst. Eine Konsole darin gibt es hier nicht "
                "(Datenbank und Tresor-Schlüssel liegen darin) – nur direkt auf dem Server."
            )
        # Über die geprüfte ID, damit ein umbenannter Container nicht dazwischenkommt.
        command = commands.shell(container_id)
        stack = AsyncExitStack()
        try:
            process = await stack.enter_async_context(self._ctx.exec.open_shell(host, cols=cols, rows=rows, command=command))
        except BaseException:
            await stack.aclose()
            raise
        return _ShellSession(process, stack)
