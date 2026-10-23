"""Lange Befehle über die Standardeingabe (`core/ssh.py`): wann sie gelten, was der Server sieht und wie die Hülle in
echten Shells läuft."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from nodvard_deck.core import ssh
from nodvard_sdk import Host as SdkHost

WRAPPED_RE = re.compile(rb"(@@nodvard-ende-[0-9a-f]{32})(.*)\1", re.DOTALL)
LOGIN_SHELLS = ("sh", "dash", "bash", "busybox")


def login_shell(name: str, tmp_path: Path, path: str | None = None) -> tuple[list[str], str]:
    """`argv` und PATH für eine Login-Shell wie auf dem Server; busybox bekommt dazu sein eigenes `cat`."""
    if sys.platform == "win32":
        pytest.skip("braucht eine echte POSIX-Shell")
    path = os.environ.get("PATH", "") if path is None else path
    if name != "busybox":
        found = shutil.which(name)
        if found is None:
            pytest.skip(f"{name} ist nicht installiert")
        return [found], path
    busybox = shutil.which("busybox")
    if busybox is None:
        pytest.skip("busybox ist nicht installiert")
    applets = tmp_path / "busybox-bin"
    applets.mkdir(exist_ok=True)
    if not (applets / "cat").exists():
        (applets / "cat").symlink_to(busybox)
    return [busybox, "sh"], f"{applets}{os.pathsep}{path}"


class _Conn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return SimpleNamespace(exit_status=0, stdout=b"", stderr=b"")


async def _sent(command: str, **kwargs) -> tuple[str, dict]:
    conn = _Conn()
    await ssh.run(conn, command, **kwargs)
    [(sent, options)] = conn.calls
    return sent, options


# ---------------------------------------------------------------------------
# Wann ein Befehl über die Standardeingabe geht
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("command,inline", [
    ("a" * 8000, True),
    ("a" * 8001, False),
    ("ä" * 4000, True),
    ("ä" * 4001, False),
])
async def test_the_limit_counts_utf8_bytes_of_the_original_command(command, inline):
    sent, options = await _sent(command)
    if inline:
        assert (sent, options) == (command, {"check": False, "encoding": None})
    else:
        wrapped = WRAPPED_RE.fullmatch(options["input"])
        assert sent != command and wrapped is not None and wrapped.group(2) == command.encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("stdin", [None, b""])
async def test_a_long_command_without_own_input_reaches_the_server_only_as_input(stdin):
    command = "echo geheim-7f3e; " + "#" * 9000
    sent, options = await _sent(command, stdin=stdin)
    assert len(sent.encode()) <= 600
    assert "geheim" not in sent and "###" not in sent
    wrapped = WRAPPED_RE.fullmatch(options["input"])
    assert wrapped is not None and wrapped.group(1).decode() in sent
    assert wrapped.group(2) == command.encode()


@pytest.mark.asyncio
async def test_a_long_command_with_nul_is_refused_before_anything_is_sent():
    conn = _Conn()
    with pytest.raises(ValueError, match="NUL"):
        await ssh.run(conn, "rm\x00 -rf /tmp/x; " + "#" * 9000)
    assert conn.calls == []


@pytest.mark.asyncio
async def test_a_short_command_with_nul_still_goes_out_unchanged():
    sent, _ = await _sent("echo a\x00b")
    assert sent == "echo a\x00b"


@pytest.mark.asyncio
async def test_without_a_posix_shell_a_long_command_stays_on_the_command_line():
    command = "Write-Output x; " + "#" * 9000
    sent, options = await _sent(command, posix_shell=False)
    assert sent == command and "input" not in options


def test_every_end_mark_is_new_and_only_hex():
    marks = {ssh.end_mark() for _ in range(100)}
    assert len(marks) == 100
    assert all(re.fullmatch(r"@@nodvard-ende-[0-9a-f]{32}", mark) for mark in marks)


@pytest.mark.asyncio
@pytest.mark.parametrize("os_family,posix", [("linux", True), ("windows", False)])
async def test_exec_handle_tells_the_ssh_layer_whether_the_server_has_a_posix_shell(monkeypatch, os_family, posix):
    from nodvard_deck.ext import context

    seen: dict = {}

    async def connection(_self, _host):
        return "verbindung"

    async def run(conn, command, **kwargs):
        seen.update(kwargs, conn=conn, command=command)
        return 0, "", ""

    monkeypatch.setattr(context.ExecHandle, "_connection", connection)
    monkeypatch.setattr(ssh, "run", run)
    handle = context.ExecHandle(context._PermissionChecker("test-ext", ["hosts.execute"]), settings=None)
    host = SdkHost(id="h1", name="h1", display_name="h1", address="192.0.2.1", os_family=os_family)
    await handle.run(host, "true")
    assert (seen["conn"], seen["command"], seen["posix_shell"]) == ("verbindung", "true", posix)


# ---------------------------------------------------------------------------
# Die Hülle in echten Shells
# ---------------------------------------------------------------------------

PAD = "# " + "x" * 9000 + "\n"
CASES = {
    "exit_code": "echo hi; exit 7",
    "set_e": "set -e\nfalse\necho nicht",
    "reads_stdin": "if read x; then echo gelesen:$x; else echo eof; fi",
    "heredoc_at_the_end": "cat <<'EOF'\nabc $HOME\nEOF",
    "trailing_newlines": "echo a\n\n\n",
    "trap": "trap 'echo tschuess' EXIT\necho hi",
    "arguments": 'echo "n=$# 1=${1-leer}"',
    "own_variable_is_gone": 'echo "[${__nd-weg}]"',
    "umlauts_and_quotes": "echo 'grüße' \"ä\\$x\" '\"'",
    "function_return": "f() { return 3; }\nf\necho rc=$?",
    "exec_replaces": "exec echo ersetzt",
    "background_job": "sleep 0.1 & wait; echo fertig",
    "syntax_error_late": "echo vor\nif then\necho nach",
    "foreign_end_mark": "echo @@nodvard-ende-00000000000000000000000000000000; echo ok",
    "trailing_backslash": "echo a\\",
    "subshell": "sh -c 'echo innen' >/dev/null; echo aussen",
}


def _run(argv: list[str], path: str, command: str, stdin: bytes, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [*argv, "-c", command], input=stdin, capture_output=True, timeout=60, check=False, cwd=cwd,
        env={**os.environ, "PATH": path},
    )


@pytest.mark.parametrize("shell", LOGIN_SHELLS)
@pytest.mark.parametrize("case", sorted(CASES))
def test_the_wrapper_behaves_like_the_command_line(shell, case, tmp_path):
    argv, path = login_shell(shell, tmp_path)
    script = PAD + CASES[case]
    direct = _run(argv, path, script, b"", tmp_path)
    text, payload = ssh.stdin_command(script)
    wrapped = _run(argv, path, text, payload, tmp_path)
    # Nur Fehlermeldungen der Shell nennen `eval` statt `-c`.
    assert (wrapped.returncode, wrapped.stdout) == (direct.returncode, direct.stdout)
    assert bool(wrapped.stderr) == bool(direct.stderr)


@pytest.mark.parametrize("shell", LOGIN_SHELLS)
def test_an_incomplete_transfer_runs_nothing(shell, tmp_path):
    argv, path = login_shell(shell, tmp_path)
    marker = tmp_path / "lief"
    text, payload = ssh.stdin_command(f"{PAD}touch {marker}\necho lief")
    for cut in (10, len(payload) // 2, len(payload) - 1):
        done = _run(argv, path, text, payload[:cut], tmp_path)
        assert done.returncode == ssh.STDIN_INCOMPLETE_EXIT, (cut, done.stderr)
        assert done.stdout == b""
        assert done.stderr.decode().splitlines() == [ssh.STDIN_INCOMPLETE]
        assert not marker.exists(), cut
    done = _run(argv, path, text, payload, tmp_path)
    assert (done.returncode, done.stdout, marker.exists()) == (0, b"lief\n", True)


@pytest.mark.parametrize("shell", LOGIN_SHELLS)
def test_input_read_before_the_wrapper_runs_nothing(shell, tmp_path):
    argv, path = login_shell(shell, tmp_path)
    marker = tmp_path / "lief"
    text, payload = ssh.stdin_command(f"echo erste-zeile\n{PAD}touch {marker}\necho lief")
    # Wie eine ~/.bashrc, die eine Zeile von der Standardeingabe liest, bevor der Befehl dran ist.
    done = _run(argv, path, f"IFS= read -r _vorher; {text}", payload, tmp_path)
    assert done.returncode == ssh.STDIN_INCOMPLETE_EXIT, done.stderr
    assert (done.stdout, done.stderr.decode().splitlines()) == (b"", [ssh.STDIN_INCOMPLETE])
    assert not marker.exists()


@pytest.mark.parametrize("shell", LOGIN_SHELLS)
def test_a_script_of_one_mebibyte_runs_through(shell, tmp_path):
    argv, path = login_shell(shell, tmp_path)
    line = "echo " + "y" * 48 + " >/dev/null"
    script = "\n".join([line] * 20_000) + "\necho ende"
    assert len(script.encode()) > 1_048_576
    text, payload = ssh.stdin_command(script)
    done = _run(argv, path, text, payload, tmp_path)
    assert (done.returncode, done.stdout, done.stderr) == (0, b"ende\n", b"")
