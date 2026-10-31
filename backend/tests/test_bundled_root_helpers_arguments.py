"""Böse Argumente für die mitgelieferten Hilfsskripte: Jeder Unterbefehl lehnt sie ab, bevor ein Programm mit
Nebenwirkung startet. Läuft eine Kopie jedes Skripts, deren `PATH` auf Attrappen zeigt; jede Attrappe schreibt ins
Protokoll."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Vor der Fixture: Sie legt Symlinks an, die unter Windows meist ein Recht brauchen.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="braucht eine POSIX-Shell")

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
SCRIPT_PATH = "PATH=/usr/sbin:/usr/bin:/sbin:/bin"
USAGE_EXIT = 64
REFUSED_EXIT = 77

# Reine Textwerkzeuge laufen echt; alles mit Nebenwirkung ist eine Attrappe, alles andere fehlt.
PURE = ("tr", "cut", "sed", "grep", "head", "tail", "wc", "expr", "basename", "dirname", "printf", "cat")
SIDE_EFFECTS = (
    "systemctl", "docker", "fail2ban-client", "apt-get", "dnf", "apk", "shutdown", "mv", "cp", "rm", "chmod", "chown",
    "install", "mkdir", "touch", "ln", "kill", "pkill", "flock", "timeout", "setsid", "nohup", "clamscan", "clamdscan",
    "freshclam", "lynis", "find", "stat", "sha256sum", "mktemp", "tee", "sleep", "journalctl", "ss", "getent", "id",
    "readlink", "env", "nice", "ionice", "date", "ls", "sync", "unattended-upgrade", "dpkg", "awk",
)

LONG = "a" * 256
MARK = "@@scan-rc-0123456789abcdef="
RID_UPGRADE = "upd_0123456789abcdef"
RID_AUDIT = "audit_0123456789abcdef"

CASES: list[tuple[str, tuple[str, ...]]] = [
    *[(ext_id, args) for ext_id in ("system", "docker", "shield") for args in ((), ("x",), ("-h",), ("",))],
    *[(ext_id, ("version", "x")) for ext_id in ("system", "docker", "shield")],
    ("system", ("service-restart",)),
    ("system", ("service-restart", "a.service", "b.service")),
    ("system", ("service-restart", "-x.service")),
    ("system", ("service-restart", "--now")),
    ("system", ("service-restart", "a;b.service")),
    ("system", ("service-restart", "a b.service")),
    ("system", ("service-restart", "$(id).service")),
    ("system", ("service-restart", "`id`.service")),
    ("system", ("service-restart", "../a.service")),
    ("system", ("service-restart", "a/b.service")),
    ("system", ("service-restart", "a\n.service")),
    ("system", ("service-restart", "sshd")),
    ("system", ("service-restart", "a.mount")),
    ("system", ("service-restart", ".service")),
    ("system", ("service-restart", "a" * 129 + ".service")),
    ("system", ("service-restart", "")),
    ("docker", ("ps", "-a")),
    ("docker", ("stats", "x")),
    *[("docker", (verb, name)) for verb in ("start", "stop", "restart", "inspect", "id", "id-state")
      for name in ("-f", "--all", "*", "a*", "", LONG, "a;b", "a b", "$(id)", "../a", "a/b", ".a")],
    ("docker", ("start", "a", "b")),
    ("docker", ("start",)),
    ("docker", ("state",)),
    ("docker", ("state", *["a"] * 201)),
    ("docker", ("ports", "0123456789a")),
    ("docker", ("ports", "0" * 65)),
    ("docker", ("ports", "0123456789AB")),
    ("docker", ("ports", *["0123456789ab"] * 201)),
    ("docker", ("logs", "-1", "-", "0", "a")),
    ("docker", ("logs", "99999", "-", "0", "a")),
    ("docker", ("logs", "", "-", "0", "a")),
    ("docker", ("logs", "10", "--foo", "0", "a")),
    ("docker", ("logs", "10", "0m", "0", "a")),
    ("docker", ("logs", "10", "5d", "0", "a")),
    ("docker", ("logs", "10", "5mm", "0", "a")),
    ("docker", ("logs", "10", "-", "2", "a")),
    ("docker", ("logs", "10", "-", "yes", "a")),
    ("docker", ("logs", "10", "-", "0", "-f")),
    ("docker", ("logs", "10", "-", "0")),
    ("shield", ("guard", "/etc/passwd")),
    ("shield", ("guard", "/etc/a b", "--")),
    ("shield", ("guard", "/etc/a\tb", "--")),
    ("shield", ("guard", "/etc/a\nb", "--")),
    ("shield", ("guard", "/etc/../shadow", "--")),
    ("shield", ("guard", "etc/passwd", "--")),
    ("shield", ("guard", "/etc/passwd", "--", "/usr/bin/*")),
    ("shield", ("guard", "/etc/$(id)", "--")),
    ("shield", ("detect-templates", "x")),
    ("shield", ("refresh-packages", "x")),
    ("shield", ("quarantine", "a/b", "name")),
    ("shield", ("quarantine", "/tmp/a", "a/b")),
    ("shield", ("quarantine", "/tmp/a", ".a")),
    ("shield", ("quarantine", "/tmp/a")),
    *[("shield", (verb, name)) for verb in ("restore", "delete", "quarantine-state")
      for name in ("a/b", ".a", "..", "a b", "a;b", "$(id)", "")],
    ("shield", ("ban", "1" * 46, "sshd")),
    ("shield", ("ban", "1.2.3.x", "sshd")),
    ("shield", ("ban", "1.2.3.4", "a" * 65)),
    ("shield", ("ban", "1.2.3.4", "a/b")),
    ("shield", ("ban", "1.2.3.4", "-x")),
    ("shield", ("unban", "", "sshd")),
    ("shield", ("unban", "1.2.3.4")),
    ("shield", ("install", "clamav; rm -rf /")),
    ("shield", ("install", "unknown")),
    ("shield", ("install", "")),
    ("shield", ("install", "clamav", "lynis")),
    ("shield", ("reboot", "now")),
    ("shield", ("upgrade-start", RID_UPGRADE, "yum", "all", "plain")),
    ("shield", ("upgrade-start", RID_UPGRADE, "apt", "foo", "plain")),
    ("shield", ("upgrade-start", RID_UPGRADE, "apt", "security", "dist", "a")),
    ("shield", ("upgrade-start", RID_UPGRADE, "apt", "security", "plain")),
    ("shield", ("upgrade-start", RID_UPGRADE, "apt", "security", "plain", "a;b")),
    ("shield", ("upgrade-start", RID_UPGRADE, "apt", "security", "plain", "$(id)")),
    ("shield", ("upgrade-start", RID_UPGRADE, "apt", "security", "plain", "a b")),
    ("shield", ("upgrade-start", RID_UPGRADE, "apt", "security", "plain", "-y")),
    ("shield", ("upgrade-start", RID_UPGRADE, "apt", "all", "plain", "extra")),
    ("shield", ("upgrade-start", "upd_0123", "apt", "all", "plain")),
    ("shield", ("upgrade-start", "upd_0123456789ABCDEF", "apt", "all", "plain")),
    ("shield", ("upgrade-start", RID_AUDIT, "apt", "all", "plain")),
    ("shield", ("upgrade-run", RID_UPGRADE, "apt", "all")),
    ("shield", ("upgrade-poll",)),
    ("shield", ("upgrade-poll", "upd_x")),
    ("shield", ("upgrade-poll", RID_AUDIT)),
    ("shield", ("upgrade-poll", RID_UPGRADE, "x")),
    ("shield", ("audit-start",)),
    ("shield", ("audit-start", RID_UPGRADE)),
    ("shield", ("audit-start", "audit_0123456789ABCDEF")),
    ("shield", ("audit-run", "audit_x")),
    ("shield", ("audit-poll", "../x")),
    ("shield", ("audit-stop", "audit_0123")),
    ("shield", ("scan", "@@scan-rc-x=", "1", "1", "1", "--", "/")),
    ("shield", ("scan", "@@scan-rc-0123456789ABCDEF=", "1", "1", "1", "--", "/")),
    ("shield", ("scan", MARK, "0", "1", "1", "--", "/")),
    ("shield", ("scan", MARK, "1234567", "1", "1", "--", "/")),
    ("shield", ("scan", MARK, "01", "1", "1", "--", "/")),
    ("shield", ("scan", MARK, "1", "1", "1", "/tmp", "--", "/")),
    ("shield", ("scan", MARK, "1", "1", "1", "--", "tmp")),
    ("shield", ("scan", MARK, "1", "1", "1", "^/tmp", "/")),
    ("shield", ("watch", MARK, "1", "1", "1", "2", "--", "/")),
    ("shield", ("watch", MARK, "1", "1", "1", "0", "tmp", "--", "/")),
    ("shield", ("watch", MARK, "1", "1", "1", "0", "/tmp", "/")),
    ("shield", ("scan-run", "x", MARK, "1", "--", "/")),
    ("shield", ("scan-run", "w0", MARK, "1", "--", "/")),
    ("shield", ("watch-run", "-", MARK, "1", "1", "2", "--", "/")),
    ("shield", ("odd-scan", "/run/tmp.0123456789", "/run/tmp.0123456789")),
    ("shield", ("odd-scan", "/tmp/a", "/run/tmp.0123456789", "/")),
    ("shield", ("odd-scan", "/run/tmp.0123456789", "/run/../tmp.012345", "/")),
    ("shield", ("odd-scan", "/run/tmp.nicht-da-1", "/run/tmp.nicht-da-2", "/")),
    ("shield", ("realtime-on", "0", "--", "/home")),
    ("shield", ("realtime-on", "1001", "--", "/home")),
    ("shield", ("realtime-on", "10", "--", "home")),
    ("shield", ("realtime-on", "10", "--", "/home/../etc")),
    ("shield", ("realtime-on", "10", "--", "/home\n/etc")),
    ("shield", ("realtime-on", "10", "--", "/" + "a" * 4096)),
    ("shield", ("realtime-on", "10", "/home")),
    ("shield", ("realtime-off", "x")),
    ("shield", ("realtime-log", "01")),
    ("shield", ("realtime-log", "1" * 13)),
    ("shield", ("realtime-log", "-1")),
    ("shield", ("realtime-log", "")),
    ("shield", ("realtime-confirm",)),
    ("shield", ("realtime-confirm", *["/a"] * 51)),
    ("shield", ("realtime-confirm", "/a", "b")),
    ("shield", ("realtime-confirm", "/a/../b")),
]

REFUSED = [
    ("system", ("service-restart", unit)) for unit in (
        "ssh.service", "sshd.service", "sshd@1.service", "dbus.service", "dbus-broker.service",
        "systemd-journald.service", "networking.service", "NetworkManager.service", "dhcpcd.service",
        "wpa_supplicant.service", "nodvard.service", "nodvard-deck.service", "lattice.service",
        "lattice-backend.service",
    )
]


def _shells() -> list[str]:
    shells = [path for name in ("sh", "dash") if (path := shutil.which(name))]
    if not shells:
        pytest.skip("braucht eine POSIX-Shell")
    return shells


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("hilfsskripte")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    log = root / "calls.log"
    for name in PURE:
        if real := shutil.which(name):
            (bin_dir / name).symlink_to(real)
    for name in SIDE_EFFECTS:
        stub = bin_dir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 1\n')
        stub.chmod(0o755)
    scripts = {}
    for ext_id in ("docker", "shield", "system"):
        text = (REPO_EXTENSIONS_DIR / ext_id / "root-helper" / f"{ext_id}-root.sh").read_text()
        assert text.count(f"\n{SCRIPT_PATH}\n") == 1, ext_id
        copy = root / f"{ext_id}-root"
        copy.write_text(text.replace(f"\n{SCRIPT_PATH}\n", f"\nPATH={bin_dir}\n"))
        scripts[ext_id] = copy
    return {"log": log, **scripts}


def _run(shell: str, script: Path, args: tuple[str, ...], log: Path) -> subprocess.CompletedProcess[str]:
    log.write_text("")
    return subprocess.run(
        [shell, str(script), *args], capture_output=True, text=True, timeout=30, check=False,
        env={"PATH": "/nicht-da", "LANG": "C"},
    )


def _assert_nothing_ran(sandbox, ext_id: str, args: tuple[str, ...], done, code: int) -> None:
    calls = sandbox["log"].read_text()
    assert (done.returncode, done.stdout, calls) == (code, "", ""), (ext_id, args, done.stderr)
    assert done.stderr.startswith(f"{ext_id}-root: ") and "not found" not in done.stderr, (ext_id, args, done.stderr)


@pytest.mark.parametrize(("ext_id", "args"), CASES, ids=[f"{e}-{i}" for i, (e, _) in enumerate(CASES)])
def test_a_bad_argument_is_refused_before_anything_runs(sandbox, ext_id, args):
    for shell in _shells():
        done = _run(shell, sandbox[ext_id], args, sandbox["log"])
        _assert_nothing_ran(sandbox, ext_id, args, done, USAGE_EXIT)


@pytest.mark.parametrize(("ext_id", "args"), REFUSED, ids=[a[1] for _, a in REFUSED])
def test_a_service_the_dashboard_never_restarts_is_refused_by_the_helper_too(sandbox, ext_id, args):
    for shell in _shells():
        done = _run(shell, sandbox[ext_id], args, sandbox["log"])
        _assert_nothing_ran(sandbox, ext_id, args, done, REFUSED_EXIT)


def test_the_sandbox_sees_a_call_that_gets_through(sandbox):
    # Gegenprobe: Ein gültiger Aufruf erreicht die Attrappe, die Prüfung oben sieht also wirklich etwas.
    for shell in _shells():
        done = _run(shell, sandbox["docker"], ("start", "web"), sandbox["log"])
        assert done.returncode == 1 and sandbox["log"].read_text() == "docker start -- web\n"
