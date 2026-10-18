"""`ctx.exec.run_argv()` / `stream_argv()`: Die Extension gibt eine Wortliste, der Kern setzt die Kommandozeile
zusammen (docs/02-EXTENSION-API.md §2).

Drei Ebenen: Das Quoting übersteht echte POSIX-Shells Wort für Wort, ungültige Listen scheitern vor jeder
Verbindung (und nach der Rechte-Prüfung), und über einen echten SSH-Server kommen Kommandozeile und
Standardeingabe genau so an, mit EOF auch bei leeren Daten.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import shutil
import subprocess

import asyncssh
import pytest
from nodvard_sdk.errors import ActionBlocked, PermissionDenied
from sqlalchemy import select
from test_ext_context import _build

from nodvard_deck.ext.context import _join_argv
from nodvard_deck.models import AuditEntry
from nodvard_deck.services import hosts as hosts_service
from nodvard_deck.services.hosts import host_to_sdk

# Wörter, an denen selbst gebautes Quoting typischerweise scheitert.
TRICKY = [
    "plain", "two words", "it's", '"double"', "$HOME", "$(id)", "`id`", "; rm -rf /tmp/x", "a\nb", "tab\there",
    "*", "?", "[ab]", "~", "!", "#kommentar", "x=y", "-n", "--", "\\", "back\\slash", "ümlaut ß", "",
    "'", "''", "a'b'c", "{a,b}", "&&", "|", ">out", "<in",
]


def _shells() -> list[str]:
    return [path for name in ("sh", "dash", "bash") if (path := shutil.which(name))]


@pytest.mark.parametrize("shell", _shells())
def test_joined_command_splits_back_into_exactly_the_same_words_in_a_real_shell(shell):
    argv = ["printf", "%s\\0", *TRICKY]
    result = subprocess.run([shell, "-c", _join_argv(argv)], capture_output=True, check=True, timeout=10)
    assert result.stdout.decode("utf-8").split("\0")[:-1] == TRICKY


def test_join_is_shlex_join_so_extensions_can_predict_the_command_line():
    assert _join_argv(["docker", "compose", "-f", "/opt/stacks/a b/compose.yml", "config"]) == (
        "docker compose -f '/opt/stacks/a b/compose.yml' config"
    )
    assert _join_argv(("ls", "-la")) == "ls -la"  # ein Tupel ist auch eine Liste von Wörtern
    # Kein Name davor: ein Pfad mit `=` bleibt ein Programm, ebenso `=` in späteren Wörtern.
    assert _join_argv(["/opt/a=b/prog", "x=y"]) == "/opt/a=b/prog x=y"


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        ("ls -la", TypeError),  # Zeichenkette: wäre Zeichen für Zeichen eine Sequence[str]
        (b"ls", TypeError),
        ((w for w in ["ls"]), TypeError),  # keine Sequence
        ([], ValueError),
        ([""], ValueError),
        (["ls", 1], TypeError),
        (["ls", None], TypeError),
        (["ls", b"x"], TypeError),
        (["ls", "a\x00b"], ValueError),
        (["x=y", "echo"], ValueError),  # die Shell setzte x und führte nur echo aus
        (["x+=y", "echo"], ValueError),
        (["LD_PRELOAD=/tmp/a.so", "prog"], ValueError),
    ],
)
async def test_invalid_argv_fails_before_any_connection(tmp_path, db_session, argv, error):
    """`host=None`: Würde das Kommando trotzdem verschickt, käme ein anderer Fehler beim Auflösen des Hosts."""
    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"])
    with pytest.raises(error):
        await ctx.exec.run_argv(host=None, argv=argv)
    with pytest.raises(error):
        ctx.exec.stream_argv(host=None, argv=argv)


async def test_stdin_of_another_type_is_refused_before_any_connection(tmp_path, db_session):
    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"])
    with pytest.raises(TypeError):
        await ctx.exec.run_argv(host=None, argv=["cat"], stdin=123)


async def test_permission_is_checked_before_the_argv(tmp_path, db_session):
    _, _, ctx = _build(tmp_path, permissions=[])
    with pytest.raises(PermissionDenied):
        await ctx.exec.run_argv(host=None, argv="kein argv")
    with pytest.raises(PermissionDenied):
        ctx.exec.stream_argv(host=None, argv=["true"])


async def test_deny_patterns_apply_to_the_joined_command_and_the_audit_row_takes_log_command(tmp_path, db_session):
    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"])
    with pytest.raises(ActionBlocked):
        await ctx.exec.run_argv(host=None, argv=["rm", "-rf", "/"], log_command="rm -rf <ziel>")
    with pytest.raises(ActionBlocked):
        async with ctx.exec.stream_argv(host=None, argv=["rm", "-rf", "/"]):
            pass

    rows = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "exec.denied"))).scalars().all()
    assert [row.detail["command"] for row in rows] == ["rm -rf <ziel>", "rm -rf /"]


class _Server(asyncssh.SSHServer):
    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    async def validate_password(self, username: str, password: str) -> bool:
        return (username, password) == ("argv", "argv-pass")


async def _handle(process) -> None:
    """`read-stdin …` liest die Standardeingabe bis EOF (fehlt EOF, läuft die Zeitgrenze des Aufrufers ab), sonst nur
    die Kommandozeile zurück, ohne die Standardeingabe anzufassen."""
    command = process.command or ""
    data = await process.stdin.read() if command.startswith("read-stdin") else None
    reply = {"command": command, "stdin": None if data is None else data.decode("utf-8")}
    process.stdout.write(json.dumps(reply).encode() + b"\n")
    await process.stdout.drain()
    process.exit(0)


@pytest.fixture
async def argv_host(db_session, test_settings):
    server = await asyncssh.listen(
        "127.0.0.1", 0, server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        server_factory=_Server, process_factory=_handle, encoding=None,
    )
    host = await hosts_service.create_host(db_session, name="argv-host", address="127.0.0.1")
    await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username="argv", port=server.sockets[0].getsockname()[1], secret_value="argv-pass",
    )
    await db_session.commit()
    try:
        yield host_to_sdk(host)
    finally:
        from nodvard_deck.core import ssh

        await ssh.reset_ssh_pool()
        server.close()
        try:
            await asyncio.wait_for(server.wait_closed(), timeout=5.0)
        except TimeoutError:
            pass


async def test_run_argv_sends_the_joined_command_line_over_ssh(tmp_path, test_settings, argv_host):
    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"], settings=test_settings)
    argv = ["echo", "a b", "$(id)", "it's"]
    result = await ctx.exec.run_argv(argv_host, argv, timeout_s=10)
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"command": shlex.join(argv), "stdin": None}


@pytest.mark.parametrize(
    ("stdin", "expected"),
    [
        ("services:\n  web:\n    image: nginx\n", "services:\n  web:\n    image: nginx\n"),
        ("Grüße\n".encode(), "Grüße\n"),
        ("", ""),  # leer: trotzdem EOF, sonst hinge der Prozess bis zur Zeitgrenze
        (b"", ""),
    ],
)
async def test_run_argv_feeds_stdin_and_closes_it(tmp_path, test_settings, argv_host, stdin, expected):
    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"], settings=test_settings)
    result = await ctx.exec.run_argv(argv_host, ["read-stdin", "--", "-"], stdin=stdin, timeout_s=10)
    reply = json.loads(result.stdout)
    assert reply == {"command": "read-stdin -- -", "stdin": expected}
    # Die Daten stehen nicht auf der Kommandozeile.
    assert expected == "" or expected.strip() not in reply["command"]


async def test_stream_argv_runs_the_joined_command_line(tmp_path, test_settings, argv_host):
    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"], settings=test_settings)
    argv = ["tail", "-f", "/var/log/a b.log"]
    chunks = b""
    async with ctx.exec.stream_argv(argv_host, argv) as stream:
        async for chunk in stream:
            chunks += chunk
    assert json.loads(chunks.decode().strip())["command"] == shlex.join(argv)
