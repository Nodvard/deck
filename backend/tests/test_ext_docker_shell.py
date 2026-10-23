"""Konsole im Container (Docker-Modul `shell.py`, `ScopedTerminalTarget` mit `scope = "container"`)."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


@pytest.fixture(autouse=True)
def _ext_path():
    before = list(sys.path)
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "docker" / "src"))
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


class _Host:
    def __init__(self, *, tags=("docker",), has_credential=True) -> None:
        self.id = "h1"
        self.tags = list(tags)
        self.has_credential = has_credential


class _Stdout:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


class _Stdin:
    def __init__(self) -> None:
        self.data: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.data.append(data)

    async def drain(self) -> None:
        return None


class _Process:
    def __init__(self) -> None:
        self.stdout = _Stdout([b"root@web:/# "])
        self.stdin = _Stdin()
        self.sizes: list[tuple[int, int]] = []
        self.exit_status = None

    def change_terminal_size(self, cols: int, rows: int) -> None:
        self.sizes.append((cols, rows))


class _Result:
    def __init__(self, exit_code: int, stdout: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = ""


class _Exec:
    def __init__(self, *, fail: BaseException | None = None, container_id: str = "0123456789ab" * 5 + "0011") -> None:
        self.opened: list[dict] = []
        self.ran: list[str] = []
        self.closed = 0
        self.fail = fail
        self.process = _Process()
        self.container_id = container_id

    async def run(self, host, command: str, *, timeout_s: int = 60):
        self.ran.append(command)
        return _Result(0, self.container_id + "\n")

    def open_shell(self, host, *, cols: int, rows: int, command: str | None = None):
        @asynccontextmanager
        async def _cm():
            self.opened.append({"host": host.id, "cols": cols, "rows": rows, "command": command})
            if self.fail is not None:
                raise self.fail
            try:
                yield self.process
            finally:
                self.closed += 1

        return _cm()


class _Settings:
    def __init__(self, values: dict) -> None:
        self.values = values

    async def get(self) -> dict:
        return self.values


class _Ctx:
    def __init__(self, exec_handle: _Exec, settings: dict | None = None) -> None:
        self.exec = exec_handle
        self.settings = _Settings(settings or {})


def test_command_is_fixed_and_the_name_is_checked():
    from nodvard_deck_ext_docker.commands import shell

    assert shell("nginx-1") == (
        "docker exec -it nginx-1 sh -c 'if command -v bash >/dev/null 2>&1; then exec bash; else exec sh; fi'"
    )
    for bad in ("x; rm -rf /", "$(reboot)", "-u0", "a b", "", "web\n"):
        with pytest.raises(ValueError):
            shell(bad)


def test_it_is_a_scoped_terminal_target_and_nothing_else():
    from nodvard_deck_ext_docker.shell import ContainerShell
    from nodvard_sdk import ALL_CAPABILITIES
    from nodvard_sdk.capabilities import ScopedTerminalTarget

    shell = ContainerShell(_Ctx(_Exec()))
    assert [p for p in ALL_CAPABILITIES if isinstance(shell, p)] == [ScopedTerminalTarget]
    assert shell.scope == "container"


@pytest.mark.asyncio
async def test_only_docker_hosts_with_access_and_valid_names():
    from nodvard_deck_ext_docker.shell import ContainerShell

    shell = ContainerShell(_Ctx(_Exec(), {"docker_host_tag": "container-host"}))
    assert await shell.can_open_in(_Host(tags=["container-host"]), "web") is True
    assert await shell.can_open_in(_Host(tags=["docker"]), "web") is False  # eingestellter Tag zählt
    assert await shell.can_open_in(_Host(tags=["container-host"], has_credential=False), "web") is False
    assert await shell.can_open_in(_Host(tags=["container-host"]), "web;id") is False


@pytest.mark.asyncio
async def test_open_runs_docker_exec_with_a_pty_and_relays_the_session():
    from nodvard_deck_ext_docker.shell import ContainerShell

    exec_handle = _Exec()
    session = await ContainerShell(_Ctx(exec_handle)).open_in(_Host(), "web", cols=120, rows=40)
    assert exec_handle.opened == [{
        "host": "h1", "cols": 120, "rows": 40,
        "command": "docker exec -it web sh -c 'if command -v bash >/dev/null 2>&1; then exec bash; else exec sh; fi'",
    }]
    assert [chunk async for chunk in session.read()] == [b"root@web:/# "]
    await session.write(b"ls\n")
    await session.resize(100, 30)
    assert exec_handle.process.stdin.data == [b"ls\n"] and exec_handle.process.sizes == [(100, 30)]
    await session.close()
    assert exec_handle.closed == 1


@pytest.mark.asyncio
async def test_never_a_shell_in_the_dashboards_own_container(monkeypatch):
    import nodvard_deck_ext_docker.capabilities as caps
    from nodvard_deck_ext_docker.shell import ContainerShell, ShellRefused

    monkeypatch.setattr(caps, "_own_container_id", lambda: "3f2a9c1b7d4e")
    exec_handle = _Exec(container_id="3f2a9c1b7d4e" * 5 + "0011")
    with pytest.raises(ShellRefused, match="Nodvard Deck selbst") as caught:
        await ContainerShell(_Ctx(exec_handle)).open_in(_Host(), "nodvard-deck-1", cols=80, rows=24)
    assert caught.value.readable is True
    assert exec_handle.opened == [] and exec_handle.ran == ["docker inspect --type container -f '{{.Id}}' nodvard-deck-1"]


@pytest.mark.asyncio
async def test_a_bad_name_or_a_failing_host_opens_nothing():
    from nodvard_deck_ext_docker.shell import ContainerShell

    exec_handle = _Exec()
    with pytest.raises(ValueError):
        await ContainerShell(_Ctx(exec_handle)).open_in(_Host(), "web && id", cols=80, rows=24)
    assert exec_handle.opened == []

    failing = _Exec(fail=ConnectionRefusedError("weg"))
    with pytest.raises(ConnectionRefusedError):
        await ContainerShell(_Ctx(failing)).open_in(_Host(), "web", cols=80, rows=24)
    assert failing.closed == 0  # nichts geöffnet, nichts hängt
