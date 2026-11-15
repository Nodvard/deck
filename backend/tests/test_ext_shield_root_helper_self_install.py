"""Als root angemeldet liest Shield auch ohne Hilfsskript auf dem Server und legt das mitgelieferte erst in einer
freigegebenen Aktion ab. Die Befehle laufen wirklich in `sh`, gegen Attrappen für `id`, `stat` und `install`."""

from __future__ import annotations

import base64
import contextlib
import gzip
import hashlib
import importlib
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("sh"), reason="braucht eine POSIX-Shell")

# Ausgabe eines echten Servers (Debian 13, Anmeldung als root, kein Hilfsskript), Rückgabecode 126.
REAL_MISSING = "@@noroot\n@@noroot-helper-missing\n"


@pytest.fixture
def shield(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "extensions/shield/src"))
    ssh = importlib.import_module("nodvard_deck_ext_shield.ssh_hardening")
    f2b = importlib.import_module("nodvard_deck_ext_shield.fail2ban")
    yield SimpleNamespace(ssh=ssh, f2b=f2b, av=ssh.av)
    for name in list(sys.modules):
        if name == "nodvard_deck_ext_shield" or name.startswith("nodvard_deck_ext_shield."):
            sys.modules.pop(name, None)


class Box:
    """Ein Server in `tmp_path`: Attrappen im PATH, Ordner für das Hilfsskript, das mitgelieferte Skript als Kopie."""

    def __init__(self, tmp_path, shield, monkeypatch, *, uid=0):
        from shield_helper_kit import helper_copy
        from test_host_setup_helpers import _helper_sandbox

        self.av = shield.av
        self.tmp = tmp_path
        self.bin, self.log, self.dir, _ = _helper_sandbox(tmp_path)
        self.helper = self.dir / "shield-root"
        self.stat = {}
        self.set_uid(uid)
        self._stat()
        self._stub("ssh-keygen", "exit 0\n")
        self._stub("install", f'''echo "install $*" >> "{self.log}"
if [ "$1" = -d ]; then for dest do :; done; mkdir -p "$dest"; exit 0; fi
for arg do src=$dest; dest=$arg; done
cp "$src" "$dest" && /bin/chmod 755 "$dest"
''')
        source_dir = tmp_path / "bundled"
        source_dir.mkdir()
        copy = helper_copy(source_dir, path=f"{self.bin}:/usr/bin:/bin", sdmark=tmp_path / "sd")
        self.text = copy.read_text(encoding="ascii").replace(f"SELF={copy}", f"SELF={self.helper}")
        self.bundled = source_dir / "shield-root.sh"
        self.bundled.write_text(self.text, encoding="ascii")
        copy.unlink()
        self.sha = hashlib.sha256(self.text.encode("ascii")).hexdigest()
        monkeypatch.setattr(self.av, "HELPER", str(self.helper))
        monkeypatch.setattr(self.av, "HELPER_FILE", self.bundled)
        monkeypatch.setattr(self.av, "HELPER_BUNDLED", (shield.ssh.VERSION, self.sha))
        self.calls = []

    def _stub(self, name, body):
        from test_host_setup_script import _stub

        _stub(self.bin / name, body)

    def set_uid(self, uid):
        self._stub("id", f'case "$1" in -u) echo {uid};; -gn) echo root;; *) exit 0;; esac\n')

    def _stat(self):
        cases = "".join(f'  "{fmt}|{path}") echo {value};;\n' for (fmt, path), value in self.stat.items())
        self._stub("stat", f'[ "$1" = -c ] || exit 2\ncase "$2|$3" in\n{cases}  %u\\|*) echo 0;;\n  %a\\|*) echo 755;;\n  *) exit 2;;\nesac\n')

    def set_stat(self, fmt, path, value):
        self.stat[(fmt, str(path))] = value
        self._stat()

    def sh(self, command):
        from test_host_setup_script import _run

        done = _run(command, self.bin)
        return SimpleNamespace(exit_code=done.returncode, stdout=done.stdout, stderr=done.stderr)

    async def run(self, command, **_kw):
        self.calls.append(command)
        return self.sh(command)

    def log_text(self):
        return self.log.read_text() if self.log.exists() else ""

    def ctx(self):
        async def run(host, command, **_kw):
            self.calls.append(command)
            if len(self.calls) == 1:
                return SimpleNamespace(exit_code=0, stdout="192.0.2.7\n", stderr="")
            return self.sh(command)

        return SimpleNamespace(exec=SimpleNamespace(run=run, read_access=lambda host: contextlib.nullcontext()))

    def session(self, username="root"):
        return SimpleNamespace(username=username, public_key=None, key_public_key=None, credential_id="c1",
                               key_credential_id=None, run=self.run)


@pytest.fixture
def box(tmp_path, shield, monkeypatch):
    return Box(tmp_path, shield, monkeypatch)


def test_real_output_matches_the_command_on_a_root_server_without_helper(box, shield):
    done = box.sh(shield.ssh.command("status", "root"))
    assert (done.exit_code, done.stdout) == (126, REAL_MISSING)


async def test_root_without_helper_reads_ssh_state_and_puts_nothing_on_the_server(box, shield):
    state = await shield.ssh.inspect(box.session())
    assert state["root"] and state["hardened"] is False and state["managed"] is False
    assert state["helper_missing"] is True and state["warning"].endswith(shield.av.HELPER_LATER_NOTE)
    assert box.calls == [shield.ssh.command("status", "root"),
                         shield.av.helper_inline_command(("ssh-hardening", "status", "root"))]
    assert not box.dir.exists() and "install" not in box.log_text()


async def test_root_with_stale_helper_reads_ssh_state_and_keeps_the_file(box, shield):
    box.dir.mkdir(parents=True)
    box.helper.write_text("#!/bin/sh\nHELPER_VERSION=3\necho alt\n")
    box.helper.chmod(0o755)
    state = await shield.ssh.inspect(box.session())
    assert state["hardened"] is False and state["helper_missing"] is True
    assert box.helper.read_text() == "#!/bin/sh\nHELPER_VERSION=3\necho alt\n"


async def test_state_with_helper_in_place_has_no_note(box, shield):
    box.sh(shield.av.helper_install_command())
    state = await shield.ssh.inspect(box.session())
    assert state["helper_missing"] is False and state["warning"] == shield.ssh.ROOT_WARNING
    assert box.calls == [shield.ssh.command("status", "root")]


async def test_fail2ban_read_as_root_puts_nothing_on_the_server(box, shield):
    values = await shield.f2b.read(box.ctx(), SimpleNamespace(id="h1", credential_username="root"))
    assert values["bantime"] == 600 and values["deck_address"] == "192.0.2.7"
    assert box.calls[1:] == [shield.f2b.read_command(), shield.av.helper_inline_command(shield.f2b.READ_ARGS)]
    assert not box.dir.exists() and "install" not in box.log_text()


@pytest.mark.parametrize("username", ["deck", None])
async def test_fail2ban_read_as_other_user_sends_no_second_command(box, shield, username):
    box.set_uid(1000)
    box._stub("sudo", "exit 1\n")
    with pytest.raises(ValueError) as error:
        await shield.f2b.read(box.ctx(), SimpleNamespace(id="h1", credential_username=username))
    assert str(error.value) == shield.av.HELPER_MISSING_MESSAGE
    assert box.calls[1:] == [shield.f2b.read_command()]


async def test_other_user_without_helper_is_told_to_set_up_with_one_command(box, shield):
    box.set_uid(1000)
    box._stub("sudo", "exit 1\n")
    for use in (None, shield.av.HelperUse(root=False, install=True)):
        box.calls.clear()
        with pytest.raises(ValueError) as error:
            await shield.ssh.inspect(box.session("deck"), use)
        assert str(error.value) == shield.av.HELPER_MISSING_MESSAGE
        assert box.calls == [shield.ssh.command("status", "deck")]
    assert not box.helper.exists() and not box.dir.exists()


async def test_approved_action_installs_the_helper_for_root(box, shield):
    use = shield.av.HelperUse(root=True, install=True)
    state = await shield.ssh.inspect(box.session(), use)
    assert state["hardened"] is False and state["helper_missing"] is False
    assert state["warning"] == shield.ssh.ROOT_WARNING
    assert use.missing and use.installed
    assert box.helper.read_text(encoding="ascii") == box.text
    assert box.calls == [shield.ssh.command("status", "root"), shield.av.helper_install_command(),
                         shield.ssh.command("status", "root")]


def test_inline_command_runs_the_bundled_script_with_exactly_these_arguments(box, shield):
    done = box.sh(shield.av.helper_inline_command(("ssh-hardening", "status", "root")))
    assert done.exit_code == 0 and "@@hardened=no" in done.stdout.splitlines()
    wrong = box.sh(shield.av.helper_inline_command(("ssh-hardening", "status", "-x")))
    assert wrong.exit_code != 0 and "invalid user" in wrong.stderr


def test_inline_command_passes_arguments_as_text(box, shield, tmp_path):
    done = box.sh(shield.av.helper_inline_command(("ssh-hardening", "status", f"$(touch {tmp_path}/x)")))
    assert done.exit_code != 0 and "invalid user" in done.stderr
    assert not (tmp_path / "x").exists()


def test_inline_command_refuses_other_users(box, shield):
    box.set_uid(1000)
    done = box.sh(shield.av.helper_inline_command(("ssh-hardening", "status", "root")))
    assert (done.exit_code, done.stdout) == (126, "")


def test_inline_command_checks_the_checksum(box, shield, monkeypatch):
    monkeypatch.setattr(shield.av, "HELPER_BUNDLED", (shield.ssh.VERSION, "0" * 64))
    done = box.sh(shield.av.helper_inline_command(("ssh-hardening", "status", "root")))
    assert (done.exit_code, done.stdout) == (1, "") and "nicht entpacken" in done.stderr


@pytest.mark.parametrize("args", [
    (), ("ssh-hardening",), ("ssh-hardening", "apply", "root", "a" * 32), ("ssh-hardening", "add-key", "root", "ssh-ed25519", "AAAA"),
    ("ssh-hardening", "off", "root"), ("fail2ban-config-write", "192.0.2.7", "[sshd]"), ("status",), ("version",),
])
def test_inline_command_only_for_reads(shield, args):
    with pytest.raises(ValueError):
        shield.av.helper_inline_command(args)


def test_real_script_fits_into_one_argument(shield):
    assert shield.av.HELPER_FILE.stat().st_size < 100_000


def test_install_command_refuses_other_users(box, shield):
    box.set_uid(1000)
    done = box.sh(shield.av.helper_install_command())
    assert done.exit_code == 126 and done.stdout == ""
    assert not box.helper.exists() and not box.dir.exists()


def test_install_command_puts_exactly_the_bundled_script(box, shield):
    done = box.sh(shield.av.helper_install_command())
    assert done.exit_code == 0, done.stderr
    assert done.stdout.splitlines() == [shield.av.HELPER_INSTALLED]
    assert box.helper.read_text(encoding="ascii") == box.text
    installs = [line for line in box.log.read_text().splitlines() if line.startswith("install ")]
    assert installs[0] == f"install -d -m 0755 -o root -g root {box.dir}"
    assert installs[1].startswith("install -m 0755 -o root -g root ") and installs[1].endswith(f" {box.helper}")
    assert len(installs) == 2


def test_install_command_checks_the_checksum(box, shield, monkeypatch):
    monkeypatch.setattr(shield.av, "HELPER_BUNDLED", (shield.ssh.VERSION, "0" * 64))
    done = box.sh(shield.av.helper_install_command())
    assert done.exit_code == 1 and done.stdout.splitlines() == [shield.av.HELPER_NOT_INSTALLED]
    assert not box.helper.exists()


def test_install_command_carries_the_script_packed(box, shield):
    command = shield.av.helper_install_command()
    blob = base64.b64encode(gzip.compress(box.text.encode("ascii"), 9, mtime=0)).decode("ascii")
    assert blob in command
    assert len(command) < 64 * 1024


@pytest.mark.parametrize("fmt,where,value", [
    ("%u", "dir", "1000"),
    ("%a", "dir", "775"),
    ("%a", "dir", "757"),
    ("%u", "parent", "1000"),
    ("%a", "parent", "2775"),
    ("%u", "grandparent", "1000"),
    ("%a", "/", "777"),
])
def test_install_command_refuses_unsafe_folders(box, shield, fmt, where, value):
    path = {"dir": box.dir, "parent": box.dir.parent, "grandparent": box.dir.parent.parent, "/": "/"}[where]
    box.set_stat(fmt, path, value)
    done = box.sh(shield.av.helper_install_command())
    assert done.exit_code == 1 and done.stdout.splitlines() == [shield.av.HELPER_NOT_INSTALLED]
    assert not box.helper.exists()


@pytest.mark.parametrize("where", ["dir", "parent"])
def test_install_command_refuses_links(box, shield, tmp_path, where):
    target = tmp_path / "woanders"
    target.mkdir()
    link = box.dir if where == "dir" else box.dir.parent
    if where == "dir":
        box.dir.parent.mkdir(parents=True)
    link.symlink_to(target, target_is_directory=True)
    done = box.sh(shield.av.helper_install_command())
    assert done.exit_code == 1 and done.stdout.splitlines() == [shield.av.HELPER_NOT_INSTALLED]
    assert not list(target.iterdir())
    assert "install " not in (box.log.read_text() if box.log.exists() else "")


def test_install_command_refuses_a_script_others_can_write(box, shield):
    box.set_stat("%a", box.helper, "777")
    done = box.sh(shield.av.helper_install_command())
    assert done.exit_code == 1 and done.stdout.splitlines() == [shield.av.HELPER_NOT_INSTALLED]


def test_install_command_checks_what_landed_on_the_server(box, shield):
    box._stub("install", 'if [ "$1" = -d ]; then for dest do :; done; mkdir -p "$dest"; exit 0; fi\n'
                         'for arg do src=$dest; dest=$arg; done\n'
                         '{ cat "$src"; echo "echo fremd"; } > "$dest" && /bin/chmod 755 "$dest"\n')
    done = box.sh(shield.av.helper_install_command())
    assert done.exit_code == 1 and done.stdout.splitlines() == [shield.av.HELPER_NOT_INSTALLED]


async def test_failed_install_keeps_a_message_for_root(box, shield):
    box.set_stat("%a", box.dir.parent, "2775")
    with pytest.raises(ValueError) as error:
        await shield.ssh.inspect(box.session(), shield.av.HelperUse(root=True, install=True))
    assert str(error.value) == shield.av.HELPER_ROOT_MESSAGE
    assert "Einrichtungsbefehl" not in str(error.value) and len(str(error.value)) <= 200
    assert len(box.calls) == 2
    assert not box.helper.exists()


def answers(shield, **by_command):
    calls = []

    async def run(command):
        calls.append(command)
        kind = {shield.av.helper_install_command(): "install", shield.av.helper_inline_command(READ): "inline"}.get(command, "first")
        return by_command[kind]

    return run, calls


READ = ("fail2ban-config-read",)
MISSING = SimpleNamespace(exit_code=126, stdout=REAL_MISSING, stderr="")


async def test_helper_is_installed_at_most_once_per_call(shield):
    installed = SimpleNamespace(exit_code=0, stdout=shield.av.HELPER_INSTALLED + "\n", stderr="")
    run, calls = answers(shield, first=MISSING, install=installed)
    use = shield.av.HelperUse(root=True, install=True)
    assert await use.call(run, "befehl", READ) is MISSING
    assert calls == ["befehl", shield.av.helper_install_command(), "befehl"]
    assert use.missing and use.installed


@pytest.mark.parametrize("answer", [
    SimpleNamespace(exit_code=0, stdout="", stderr=""),
    SimpleNamespace(exit_code=1, stdout="@@helper-installed\n", stderr=""),
    SimpleNamespace(exit_code=0, stdout="x@@helper-installed\n", stderr=""),
])
async def test_no_retry_without_a_confirmed_install(shield, answer):
    run, calls = answers(shield, first=MISSING, install=answer)
    use = shield.av.HelperUse(root=True, install=True)
    assert await use.call(run, "befehl") is MISSING
    assert len(calls) == 2 and use.missing and not use.installed


async def test_refused_install_is_reported_as_root(shield):
    refused = SimpleNamespace(exit_code=1, stdout=shield.av.HELPER_NOT_INSTALLED + "\n", stderr="")
    run, calls = answers(shield, first=MISSING, install=refused)
    use = shield.av.HelperUse(root=True, install=True)
    result = await use.call(run, "befehl")
    assert (result.exit_code, result.stdout) == (126, REAL_MISSING + shield.av.NO_ROOT_AS_ROOT + "\n")
    assert shield.av.no_root_message(result.stdout) == shield.av.HELPER_ROOT_MESSAGE
    assert len(calls) == 2 and not use.installed


CASES = [
    ("@@noroot\n@@noroot-helper-missing\n", True),
    ("@@noroot\n@@noroot-helper 3\n", True),
    ("@@noroot\n@@noroot-helper \n", True),
    ("@@noroot\n", False),
    ("@@noroot\n@@noroot-helper-unreadable\n", False),
    ("@@noroot-helper-missing\n", False),
    ("Datei @@noroot gefunden\n@@noroot-helper-missing\n", False),
    ("@@noroot\nDatei @@noroot-helper-missing gefunden\n", False),
    ("@@hardened=no\n", False),
]


@pytest.mark.parametrize("stdout,second", CASES)
async def test_install_only_for_a_missing_or_foreign_helper(shield, stdout, second):
    first = SimpleNamespace(exit_code=126, stdout=stdout, stderr="")
    run, calls = answers(shield, first=first, install=SimpleNamespace(exit_code=126, stdout="", stderr=""))
    use = shield.av.HelperUse(root=True, install=True)
    assert await use.call(run, "befehl", READ) is first
    assert calls == (["befehl", shield.av.helper_install_command()] if second else ["befehl"])
    assert use.missing is second


@pytest.mark.parametrize("stdout,second", CASES)
async def test_inline_read_only_for_a_missing_or_foreign_helper(shield, stdout, second):
    first = SimpleNamespace(exit_code=126, stdout=stdout, stderr="")
    inline = SimpleNamespace(exit_code=0, stdout="@@hardened=no\n", stderr="")
    run, calls = answers(shield, first=first, inline=inline)
    use = shield.av.HelperUse(root=True)
    assert await use.call(run, "befehl", READ) is (inline if second else first)
    assert calls == (["befehl", shield.av.helper_inline_command(READ)] if second else ["befehl"])
    assert use.missing is second and not use.installed


@pytest.mark.parametrize("install", [False, True])
async def test_other_users_never_get_a_second_command(shield, install):
    run, calls = answers(shield, first=MISSING)
    use = shield.av.HelperUse(root=False, install=install)
    assert await use.call(run, "befehl", READ) is MISSING
    assert calls == ["befehl"] and not use.missing


async def test_reading_never_installs_and_changes_never_run_inline(shield):
    run, calls = answers(shield, first=MISSING, inline=MISSING)
    assert await shield.av.HelperUse(root=True).call(run, "befehl") is MISSING
    assert calls == ["befehl"]
    await shield.av.HelperUse(root=True).call(run, "befehl", READ)
    assert shield.av.helper_install_command() not in calls
    assert shield.av.HelperUse().root is False and shield.av.HelperUse().install is False


def test_install_command_passes_the_command_filter(box, shield):
    from nodvard_deck.core import deny_patterns

    assert deny_patterns.match_deny_patterns(shield.av.helper_install_command()) is None
    assert deny_patterns.match_deny_patterns(shield.ssh.command("status", "root")) is None
    assert deny_patterns.match_deny_patterns(shield.av.helper_inline_command(("ssh-hardening", "status", "root"))) is None


def test_no_install_command_without_bundled_script(shield, monkeypatch):
    monkeypatch.setattr(shield.av, "HELPER_BUNDLED", None)
    assert shield.av.helper_install_command() is None
