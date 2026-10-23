"""Läuft nur mit `NODVARD_DECK_TEST_DROPBEAR=benutzer@host:port` und `NODVARD_DECK_TEST_DROPBEAR_KEY=<Schlüsseldatei>`
gegen einen echten Dropbear (POSIX-Login-Shell; Shield-Starts nur ohne root und sudo ohne Passwort). Trennt einmal
absichtlich die Verbindung („String too long“ im Server-Protokoll) und ändert vorübergehend ~/.ssh/authorized_keys."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import sys
from pathlib import Path

import asyncssh
import pytest
import pytest_asyncio
from nodvard_deck.core import ssh
from nodvard_deck.services import host_setup

TARGET = re.fullmatch(r"(?P<user>[^@\s]+)@(?P<host>\S+):(?P<port>[0-9]+)", os.environ.get("NODVARD_DECK_TEST_DROPBEAR", ""))
KEY = os.environ.get("NODVARD_DECK_TEST_DROPBEAR_KEY", "")
USER = TARGET["user"] if TARGET else ""
SHIELD_SRC = Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"
# MAX_CMD_LEN von Dropbear seit 2015.68: Ein längerer Befehlstext trennt die ganze Verbindung.
DROPBEAR_LIMIT = 9000

pytestmark = pytest.mark.skipif(
    TARGET is None or not KEY, reason="NODVARD_DECK_TEST_DROPBEAR und NODVARD_DECK_TEST_DROPBEAR_KEY sind nicht gesetzt",
)


@pytest_asyncio.fixture
async def conn():
    connection = await asyncssh.connect(
        TARGET["host"], port=int(TARGET["port"]), username=USER, client_keys=[KEY], config=[], agent_path=None,
        known_hosts=None,  # nur ein Testserver: welchen Schlüssel er zeigt, ist hier egal
    )
    try:
        yield connection
        assert await ssh.run(connection, "echo noch-da") == (0, "noch-da\n", ""), "die Verbindung steht noch"
    finally:
        connection.close()
        await connection.wait_closed()


@pytest.fixture
def shield(monkeypatch):
    monkeypatch.syspath_prepend(str(SHIELD_SRC))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [20_000, 200_000])
async def test_a_long_command_arrives_complete(conn, size):
    value = "ä" * (size // 2)
    command = f"A='{value}'\nprintf '%s\\n' \"$(printf '%s' \"$A\" | wc -c)\" \"als-$(id -un)\""
    assert len(command.encode()) > size
    assert await ssh.run(conn, command) == (0, f"{size}\nals-{USER}\n", "")


@pytest.mark.asyncio
async def test_a_parallel_short_channel_survives_a_long_command(conn):
    parallel = asyncio.ensure_future(ssh.run(conn, "sleep 1; echo parallel"))
    await asyncio.sleep(0.2)
    assert await ssh.run(conn, f": {'x' * 30_000}\necho lang-ok") == (0, "lang-ok\n", "")
    assert await parallel == (0, "parallel\n", "")


@pytest.mark.asyncio
async def test_an_incomplete_input_runs_nothing(conn):
    marker = f"/tmp/nodvard-dropbear-{secrets.token_hex(8)}"
    text, payload = ssh.stdin_command(f": {'x' * 9000}\ntouch {marker}\necho lief")
    try:
        for cut in (10, len(payload) // 2, len(payload) - 1):
            result = await conn.run(text, input=payload[:cut], check=False, encoding=None)
            assert result.exit_status == ssh.STDIN_INCOMPLETE_EXIT, cut
            assert (result.stdout, result.stderr.decode().splitlines()) == (b"", [ssh.STDIN_INCOMPLETE])
        assert await ssh.run(conn, f"test -e {marker}") == (1, "", "")
    finally:
        await ssh.run(conn, f"rm -f {marker}")


@pytest.mark.asyncio
async def test_the_setup_wrapper_carries_a_75_kb_script(conn):
    filler = "".join(f": füllzeile {i:04d} {'x' * 60}\n" for i in range(1000))
    script = f"{filler}A='{'ä' * 2000}'\nprintf '%s\\n' \"$(printf '%s' \"$A\" | wc -c)\" \"als-$(id -un)\""
    command, data = host_setup.remote_setup_command(script, as_root=True, sudo_with_password=False)
    assert len(command.encode()) <= ssh.INLINE_COMMAND_MAX and len(data) > 75_000
    result = await conn.run(command, input=data, check=False, encoding=None, timeout=60)
    assert (result.exit_status, result.stderr) == (0, b"")
    assert result.stdout.decode().splitlines() == [
        host_setup.SETUP_INPUT_READ, host_setup.SETUP_STARTED, "4000", f"als-{USER}",
    ]


@pytest_asyncio.fixture
async def without_root(conn):
    _code, out, _err = await ssh.run(conn, "id -u; sudo -n true 2>/dev/null && echo sudo")
    if out.split()[:1] == ["0"] or "sudo" in out.split():
        pytest.skip("der Benutzer hat root oder sudo ohne Passwort, der echte Lauf würde starten")


@pytest.mark.asyncio
async def test_real_shield_starts_run_through_and_stop_without_root(conn, shield, without_root):
    from nodvard_deck_ext_shield import antivirus as av
    from nodvard_deck_ext_shield import audit_job as aj
    from nodvard_deck_ext_shield import patching
    from nodvard_deck_ext_shield import updates as up

    packages = [f"libpaket{i:03d}-erweitert" for i in range(150)]
    starts = [
        aj.launch_as_root("audit_0123456789abcdef"),
        patching.launch_as_root("upd_0123456789abcdef", up.upgrade_command("apt", "all", []), up.upgrade_args("apt", "all")),
        patching.launch_as_root(
            "upd_0123456789abcdef", up.upgrade_command("apt", "security", packages),
            up.upgrade_args("apt", "security", packages),
        ),
    ]
    for start in starts:
        assert len(start.encode()) > DROPBEAR_LIMIT
        code, out, _err = await ssh.run(conn, start)
        assert (code, out.splitlines()[:1]) == (126, [av.NO_ROOT])


@pytest.mark.asyncio
async def test_the_intrusion_check_with_many_files_runs_through_completely(conn, shield):
    from nodvard_deck_ext_shield import guard
    from nodvard_deck_ext_shield import intrusion as ix

    command = guard.guard_as_root([f"/etc/nginx/sites-enabled/seite-{i:03d}.conf" for i in range(100)])
    assert len(command.encode()) > DROPBEAR_LIMIT
    code, out, _err = await ssh.run(conn, command, timeout_s=90)
    lines = out.splitlines()
    assert code == 0 and ix.guard_complete(out)
    assert lines[0] == "@@uid" and lines[1].isdigit()


@pytest.mark.asyncio
async def test_on_the_command_line_a_long_command_drops_the_whole_connection():
    connection = await asyncssh.connect(
        TARGET["host"], port=int(TARGET["port"]), username=USER, client_keys=[KEY], config=[], agent_path=None,
        known_hosts=None,
    )
    try:
        parallel = asyncio.ensure_future(ssh.run(connection, "sleep 1; echo parallel"))
        await asyncio.sleep(0.2)
        results = []
        for pending in (ssh.run(connection, f": {'x' * 20_000}\necho lang-ok", posix_shell=False), parallel):
            try:
                results.append(await pending)
            except (asyncssh.Error, OSError):
                results.append(None)
        assert all(result in (None, (-1, "", "")) for result in results), results
        await asyncio.wait_for(connection.wait_closed(), timeout=5)
    finally:
        connection.close()


@pytest_asyncio.fixture
async def keys_restored(conn):
    """Sichert ~/.ssh/authorized_keys des Testbenutzers samt Rechten und stellt sie danach Byte für Byte wieder her."""
    saved = await conn.run("cat ~/.ssh/authorized_keys", check=True, encoding=None)
    modes = (await conn.run("stat -c %a ~/.ssh ~/.ssh/authorized_keys", check=True)).stdout.split()
    assert saved.stdout and len(modes) == 2
    try:
        yield
    finally:
        await conn.run("cat > ~/.ssh/authorized_keys", input=saved.stdout, check=True, encoding=None)
        await conn.run(f"chmod {modes[0]} ~/.ssh && chmod {modes[1]} ~/.ssh/authorized_keys", check=True)
        assert (await conn.run("cat ~/.ssh/authorized_keys", check=True, encoding=None)).stdout == saved.stdout


def _setup_script(public_key: str, *, processes: str | None = None) -> str:
    """Das echte Einrichtungsskript für den Testbenutzer, `R` als direkter Aufruf (ohne root gibt es kein runuser)."""
    lines = host_setup.build_setup_script(USER, public_key)[0].split("\n")
    [switch] = [i for i, line in enumerate(lines) if line.startswith("R() {")]
    lines[switch] = 'R() { "$@"; }'
    script = "\n".join(lines)
    return script if processes is None else script.replace(host_setup._PROC_COMMS, processes)


async def _login_with_terminal(key: asyncssh.SSHKey) -> tuple[str, str]:
    async with asyncssh.connect(
        TARGET["host"], port=int(TARGET["port"]), username=USER, client_keys=[key], config=[], agent_path=None,
        known_hosts=None, preferred_auth="publickey",
    ) as connection:
        answer = await connection.run("echo angemeldet", check=True)
        process = await connection.create_process("tty", term_type="xterm")
        terminal = await process.stdout.read()
        await process.wait()
    return answer.stdout.strip(), terminal.strip()


def _new_key() -> tuple[asyncssh.SSHKey, str]:
    key = asyncssh.generate_private_key("ssh-ed25519")
    info = host_setup.derive_public_key(key.export_private_key().decode(), comment=host_setup.key_comment("dropbear-test"))
    return key, info.public_key


@pytest.mark.asyncio
async def test_a_new_setup_writes_a_line_dropbear_accepts_with_a_terminal(conn, keys_restored):
    key, public = _new_key()
    code, out, err = await ssh.run(conn, _setup_script(public))
    assert (code, err, host_setup.setup_messages(out)) == (0, "", [host_setup.KEY_ADDED])
    keys = (await conn.run("cat ~/.ssh/authorized_keys", check=True)).stdout
    assert keys.splitlines()[-1] == f"{host_setup.DROPBEAR_KEY_OPTIONS} {public}"
    answer, terminal = await _login_with_terminal(key)
    assert answer == "angemeldet" and terminal.startswith("/dev/")


@pytest.mark.asyncio
@pytest.mark.parametrize("old_comment", ["nodvard@dropbear-test", "lattice@alter-name"])
async def test_dropbear_drops_the_old_line_and_a_new_setup_repairs_it(conn, keys_restored, old_comment):
    key, public = _new_key()
    old = f"{public.rsplit(' ', 1)[0]} {old_comment}"
    before = (await conn.run("cat ~/.ssh/authorized_keys", check=True)).stdout
    # Ohne erkannten Dropbear schreibt das Skript die Zeile einer älteren Einrichtung.
    code, out, _err = await ssh.run(conn, _setup_script(old, processes="/nodvard-test-ohne-prozesse/[0-9]*/comm"))
    assert (code, host_setup.setup_messages(out)) == (0, [host_setup.KEY_ADDED])
    assert (await conn.run("cat ~/.ssh/authorized_keys", check=True)).stdout == f"{before}restrict,pty {old}\n"
    with pytest.raises(asyncssh.PermissionDenied):
        await _login_with_terminal(key)

    code, out, err = await ssh.run(conn, _setup_script(public))
    assert (code, err, host_setup.setup_messages(out)) == (0, "", [host_setup.KEY_FIXED])
    after = (await conn.run("cat ~/.ssh/authorized_keys", check=True)).stdout
    assert after == f"{before}{host_setup.DROPBEAR_KEY_OPTIONS} {old}\n"
    answer, terminal = await _login_with_terminal(key)
    assert answer == "angemeldet" and terminal.startswith("/dev/")
