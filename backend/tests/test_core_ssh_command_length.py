"""Schutzregel: Befehle gehen nur über `core/ssh.py` und das Einmal-Passwort an einen Server, keiner mit mehr als
`ssh.INLINE_COMMAND_MAX` Byte Befehlstext, und was nicht über die Standardeingabe gehen kann, scheitert vorher mit einem
lesbaren Satz."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from nodvard_deck.core import gate, ssh
from nodvard_deck.core.error_text import (
    describe_connection_error,
    is_expected_connection_error,
)
from nodvard_sdk import Host as SdkHost

WRAPPED_RE = re.compile(rb"(@@nodvard-ende-[0-9a-f]{32})(.*)\1", re.DOTALL)
LENGTHS = (0, 1, 4000, 7999, 8000, 8001, 8999, 9000, 9001, 16_384, 65_536, 131_072, 262_144, 1 << 20)
REPO = Path(__file__).resolve().parents[2]
SENDERS = {"backend/src/nodvard_deck/core/ssh.py", "backend/src/nodvard_deck/services/one_time_password.py"}
CHANNEL_METHODS = {
    "run", "create_process", "create_subprocess", "create_session", "open_session",
    "connect_ssh", "create_ssh_connection",
}
CONNECTORS = {"connect", "connect_reverse", "create_connection", "run_client"}
NOT_SSH = {
    "ssh": "core/ssh.py selbst, der vorgesehene Weg (nur wenn die Datei ihn von dort holt)",
    "asyncio": "startet die Ereignisschleife der Kommandozeile",
    "ctx.exec": "Befehle der Erweiterungen, laufen über core/ssh.py",
    "self._ctx.exec": "Befehle der Erweiterungen, laufen über core/ssh.py",
    "self": "eigene Methode run einer Klasse, ihr Inhalt wird selbst geprüft",
    "self._actions": "DockerStackExecutor.run, eigene Methode des Docker-Moduls",
    "self._inner": "OperateExec des Docker-Moduls (Nur bedienen), leitet an ctx.exec weiter",
    "_Scanner()": "Prüfung des eigenen CSS",
}
"""Empfänger der `CHANNEL_METHODS`, die keine SSH-Verbindung sind. Ein neuer Empfänger macht den Wächter rot und kommt
nur mit Grund hierher, einer, den es im Code nicht mehr gibt, ebenso."""


def _refusal(size: int) -> str:
    return (
        f"Der Befehl ist mit {size} Byte zu lang für eine SSH-Befehlszeile (höchstens 8000), deshalb wurde er nicht "
        "gesendet. Kleine SSH-Server wie Dropbear würden sonst die Verbindung trennen."
    )


class _Conn:
    def __init__(self) -> None:
        self.runs: list[tuple[str, dict]] = []
        self.processes: list[tuple[tuple, dict]] = []

    async def run(self, command, **kwargs):
        self.runs.append((command, kwargs))
        return SimpleNamespace(exit_status=0, stdout=b"", stderr=b"")

    async def create_process(self, *args, **kwargs):
        self.processes.append((args, kwargs))
        return SimpleNamespace(close=lambda: None)


def _command(size: int, char: str) -> str:
    """Ein Befehl mit genau `size` Byte UTF-8, so weit es geht aus `char`."""
    width = len(char.encode())
    command = char * (size // width) + "x" * (size % width)
    assert len(command.encode()) == size
    return command


def _stream(conn, command, **kwargs):
    return ssh.open_command_stream(conn, command, **kwargs)


def _shell(conn, command, **kwargs):
    return ssh.open_shell(conn, command=command, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("char", ["x", "ä", "€"], ids=["ascii", "umlaut", "drei-byte"])
@pytest.mark.parametrize("size", LENGTHS)
async def test_no_command_line_is_longer_than_the_limit(size, char):
    command = _command(size, char)
    conn = _Conn()
    await ssh.run(conn, command)
    [(sent, options)] = conn.runs
    assert len(sent.encode()) <= ssh.INLINE_COMMAND_MAX
    if size <= ssh.INLINE_COMMAND_MAX:
        assert sent == command and "input" not in options
    else:
        assert WRAPPED_RE.fullmatch(options["input"]).group(2) == command.encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("opener", [_stream, _shell], ids=["stream", "shell"])
@pytest.mark.parametrize("command", ["x" * 8001, "ä" * 4001, "x" * (1 << 20)], ids=["8001", "umlaute", "1-mib"])
async def test_stream_and_shell_refuse_a_long_command_before_sending_it(opener, command):
    conn = _Conn()
    with pytest.raises(ssh.CommandTooLong) as raised:
        async with opener(conn, command):
            pass
    assert (conn.processes, conn.runs) == ([], [])
    assert str(raised.value) == _refusal(len(command.encode()))


@pytest.mark.asyncio
@pytest.mark.parametrize("opener", [_stream, _shell], ids=["stream", "shell"])
@pytest.mark.parametrize("command,posix", [("ä" * 4000, True), ("x" * 20_000, False)], ids=["grenze", "windows"])
async def test_stream_and_shell_send_up_to_the_limit_and_leave_windows_alone(opener, command, posix):
    conn = _Conn()
    async with opener(conn, command, posix_shell=posix):
        pass
    assert [args for args, _ in conn.processes] == [(command,)]


@pytest.mark.asyncio
async def test_a_shell_without_command_is_the_login_shell():
    conn = _Conn()
    async with ssh.open_shell(conn):
        pass
    assert [args for args, _ in conn.processes] == [()]


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["x" * 8001, "ä" * 4001, "x" * (1 << 20)], ids=["8001", "umlaute", "1-mib"])
async def test_own_input_and_a_long_command_are_refused_before_sending(command):
    conn = _Conn()
    with pytest.raises(ssh.CommandTooLong) as raised:
        await ssh.run(conn, command, stdin=b"daten")
    assert conn.runs == []
    assert str(raised.value) == _refusal(len(command.encode()))


@pytest.mark.asyncio
@pytest.mark.parametrize("command,posix", [("ä" * 4000, True), ("x" * 20_000, False)], ids=["grenze", "windows"])
async def test_own_input_goes_out_with_a_command_up_to_the_limit_and_to_windows(command, posix):
    conn = _Conn()
    await ssh.run(conn, command, stdin=b"daten", posix_shell=posix)
    assert [(sent, options["input"]) for sent, options in conn.runs] == [(command, b"daten")]


@pytest.fixture
def handle(monkeypatch):
    from nodvard_deck.ext import context

    conn = _Conn()

    async def connection(_self, _host):
        return conn

    monkeypatch.setattr(context.ExecHandle, "_connection", connection)
    exec_handle = context.ExecHandle(context._PermissionChecker("test-ext", ["hosts.execute"]), settings=None)
    return exec_handle, conn


def _host(os_family: str = "linux") -> SdkHost:
    return SdkHost(id="h1", name="h1", display_name="h1", address="192.0.2.1", os_family=os_family)


@pytest.mark.asyncio
async def test_extensions_get_the_refusal_as_a_plain_sentence(handle):
    exec_handle, conn = handle
    long_word = "x" * 9000
    attempts = [
        lambda: exec_handle.run_argv(_host(), ["cat", long_word], stdin="daten"),
        lambda: exec_handle.stream(_host(), f"tail -f {long_word}").__aenter__(),
        lambda: exec_handle.stream_argv(_host(), ["tail", "-f", long_word]).__aenter__(),
        lambda: exec_handle.open_shell(_host(), cols=80, rows=24, command=f"sh {long_word}").__aenter__(),
    ]
    for attempt in attempts:
        with pytest.raises(ssh.CommandTooLong) as raised:
            await attempt()
        text = str(raised.value)
        assert text.startswith("Der Befehl ist mit 90") and "Dropbear" in text
        assert describe_connection_error(raised.value) == text
        assert gate.describe_error(raised.value) == text
        assert is_expected_connection_error(raised.value)
    assert (conn.runs, conn.processes) == ([], [])


@pytest.mark.asyncio
async def test_a_windows_server_still_gets_long_command_lines(handle):
    exec_handle, conn = handle
    command = "Write-Output " + "x" * 9000
    await exec_handle.run_argv(_host("windows"), ["cmd", command], stdin="daten")
    async with exec_handle.stream(_host("windows"), command):
        pass
    async with exec_handle.open_shell(_host("windows"), cols=80, rows=24, command=command):
        pass
    assert len(conn.runs) == 1 and len(conn.runs[0][0]) > 9000
    assert [args for args, _ in conn.processes] == [(command,), (command,)]


def _receiver(node: ast.expr) -> str:
    return f"{ast.unparse(node.func)}()" if isinstance(node, ast.Call) else ast.unparse(node)


def _calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            yield node, node.func.attr, _receiver(node.func.value)


def _ssh_is_core(tree: ast.AST) -> bool:
    """Der Name `ssh` steht nur für `core/ssh.py`, wenn die Datei ihn von dort holt und sonst nirgends vergibt."""
    bindings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            from_core = isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "core"
            bindings += [
                from_core and alias.name == "ssh"
                for alias in node.names if (alias.asname or alias.name.split(".")[0]) == "ssh"
            ]
        elif (isinstance(node, ast.Name) and node.id == "ssh" and not isinstance(node.ctx, ast.Load)) or (
            isinstance(node, ast.arg) and node.arg == "ssh"
        ):
            bindings.append(False)
    return bool(bindings) and all(bindings)


def _ssh_sends(source: str) -> list[int]:
    """Zeilen, die an `core/ssh.py` vorbei eine SSH-Verbindung öffnen oder darauf einen Befehl starten könnten."""
    tree = ast.parse(source)
    found = []
    modules = {"asyncssh"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.asname for alias in node.names if alias.name == "asyncssh" and alias.asname}
        if isinstance(node, ast.ImportFrom) and node.module == "asyncssh" and {a.name for a in node.names} & CONNECTORS:
            found.append(node.lineno)
    allowed = set(NOT_SSH) if _ssh_is_core(tree) else set(NOT_SSH) - {"ssh"}
    for node, method, receiver in _calls(tree):
        if (method in CONNECTORS and receiver in modules) or (method in CHANNEL_METHODS and receiver not in allowed):
            found.append(node.lineno)
    return sorted(found)


def _sources() -> dict[str, str]:
    roots = [REPO / "backend" / "src" / "nodvard_deck", REPO / "sdk" / "python" / "nodvard_sdk"]
    roots += sorted((REPO / "extensions").glob("*/src"))
    return {
        path.relative_to(REPO).as_posix(): path.read_text(encoding="utf-8")
        for root in roots for path in sorted(root.rglob("*.py"))
    }


@pytest.mark.parametrize("line", [
    "await conn.run(command)", "await self._conn.run(command)", "await connection.create_process(command)",
    "await c.create_session(factory)", "await asyncssh.connect(host)", "from asyncssh import connect",
    "await c.run(cmd)", "await session.run(cmd)", "await self.client.run(cmd)", "await open_link().run(cmd)",
    "await asyncssh.SSHClientConnection.run(c, cmd)", "await ctx.exec.conn.open_session()",
    "await c.create_subprocess(F, cmd)", "await conn.connect_ssh(host)", "await conn.create_ssh_connection(F, host)",
    "await asyncssh.create_connection(F, host)", "import asyncssh as a; await a.connect(host)",
    "ssh = conn; await ssh.run(cmd)",
])
def test_the_guard_finds_a_way_around_core_ssh(line):
    assert _ssh_sends(f"async def f():\n    {line}\n") == [2]


@pytest.mark.parametrize("receiver", sorted(NOT_SSH))
def test_the_guard_leaves_known_receivers_alone(receiver):
    assert _ssh_sends(f"from ..core import ssh\n\n\nasync def f():\n    await {receiver}.run(x)\n") == []


@pytest.mark.parametrize("body", [
    "async def f():\n    await ssh.run(x)\n",
    "from ..core import ssh\n\n\nasync def f(conn):\n    ssh = conn\n    await ssh.run(x)\n",
    "from ..core import ssh\n\n\nasync def f(ssh):\n    await ssh.run(x)\n",
    "from ..core import ssh\nfrom .tunnel import link as ssh\n\n\nasync def f():\n    await ssh.run(x)\n",
])
def test_a_name_ssh_that_is_not_core_ssh_counts(body):
    assert len(_ssh_sends(body)) == 1


def test_every_known_receiver_still_occurs():
    used = {
        receiver for source in _sources().values()
        for _, method, receiver in _calls(ast.parse(source)) if method in CHANNEL_METHODS
    }
    assert sorted(set(NOT_SSH) - used) == []


def test_the_guard_leaves_other_methods_alone():
    assert _ssh_sends("async def f():\n    await c.execute(cmd)\n    c.runs.append(1)\n") == []


def test_commands_reach_a_server_only_through_core_ssh():
    sources = _sources()
    assert len(sources) > 100
    found = {rel: lines for rel, source in sources.items() if rel not in SENDERS and (lines := _ssh_sends(source))}
    assert found == {}
    assert all(_ssh_sends(sources[sender]) for sender in SENDERS)
