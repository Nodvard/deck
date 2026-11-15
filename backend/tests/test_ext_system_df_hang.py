"""Ein hängendes Netzlaufwerk darf Server-Zustand und Live-Ansicht nicht lahmlegen."""

from __future__ import annotations

import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")

SRC = Path(__file__).resolve().parents[2] / "extensions" / "system" / "src"

# Echte Ausgabe von `df -P -T` auf einem Proxmox-Knoten (Debian 13), Namen und Adressen ersetzt.
DF_WITH_NFS = """Filesystem                  Type  1024-blocks      Used Available Capacity Mounted on
/dev/mapper/pve-root        ext4     98497780  12345678  81100000      14% /
/dev/nvme0n1p2              vfat      1046508     11880   1034628       2% /boot/efi
nas.example:/export/backup  nfs4   3906887680 100000000 3806887680       3% /mnt/pve/nas
"""

# Auszug aus /proc/mounts desselben Knotens (Leerzeichen im Pfad als \040).
PROC_MOUNTS = """/dev/mapper/pve-root / ext4 rw,relatime 0 0
nas.example:/export/backup /mnt/pve/nas nfs4 rw,relatime,vers=4.2 0 0
//nas.example/share /mnt/pve/my\\040share cifs rw,relatime 0 0
"""


@pytest.fixture
def system_ext(monkeypatch):
    monkeypatch.syspath_prepend(str(SRC))
    yield
    for name in [n for n in sys.modules if n.startswith("nodvard_deck_ext_system")]:
        del sys.modules[name]


def _fake_df(tmp_path: Path, body: str) -> dict[str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    df = bindir / "df"
    df.write_text("#!/bin/sh\n" + body)
    df.chmod(df.stat().st_mode | stat.S_IEXEC)
    return {"PATH": f"{bindir}:/usr/bin:/bin", "LC_ALL": "C"}


def _scripts(system_ext):
    from nodvard_deck_ext_system import live, sysinfo, warnung

    return {"SCRIPT": sysinfo.SCRIPT, "LIVE_SCRIPT": live.LIVE_SCRIPT, "WARN_SCRIPT": warnung.WARN_SCRIPT}


@pytest.mark.parametrize("name", ["SCRIPT", "LIVE_SCRIPT", "WARN_SCRIPT"])
@pytest.mark.parametrize("hang", ["sleep 60\n", "(sleep 60 &) ; sleep 60\n"], ids=["hängt", "Kindprozess"])
def test_hanging_df_only_empties_the_disk_section(system_ext, tmp_path, name, hang):
    env = _fake_df(tmp_path, hang)
    started = time.monotonic()
    out = subprocess.run(["bash", "-c", _scripts(system_ext)[name]], env=env, capture_output=True, text=True, timeout=25).stdout
    assert time.monotonic() - started < 20
    assert "@@end" in out
    assert "@@dfstatus\ntimeout" in out


def test_healthy_df_reports_ok(system_ext, tmp_path):
    env = _fake_df(tmp_path, f"cat <<'X'\n{DF_WITH_NFS}X\n")
    out = subprocess.run(["bash", "-c", _scripts(system_ext)["SCRIPT"]], env=env, capture_output=True, text=True, timeout=25).stdout
    assert "@@dfstatus\nok" in out
    assert "/dev/mapper/pve-root" in out


def test_df_skips_network_filesystems(system_ext, tmp_path):
    seen = tmp_path / "args"
    env = _fake_df(tmp_path, f'echo "$@" > {seen}\n')
    subprocess.run(["bash", "-c", _scripts(system_ext)["SCRIPT"]], env=env, capture_output=True, text=True, timeout=25)
    args = seen.read_text().split()
    for fs in ("nfs", "nfs4", "cifs", "smb3", "fuse.sshfs"):
        assert fs in args


@pytest.mark.skipif(not Path("/bin/dash").exists(), reason="dash fehlt")
def test_plain_sh_still_lists_disks(system_ext, tmp_path):
    env = _fake_df(tmp_path, f"cat <<'X'\n{DF_WITH_NFS}X\n")
    out = subprocess.run(["dash", "-c", _scripts(system_ext)["SCRIPT"]], env=env, capture_output=True, text=True, timeout=25).stdout
    assert "/boot/efi" in out


def test_real_df_output_with_nfs4_row_parses(system_ext):
    from nodvard_deck_ext_system.sysinfo import parse_system_info

    body = "@@disks\n" + "".join(DF_WITH_NFS.splitlines(keepends=True)[1:]) + "@@dfstatus\nok\n@@end\n"
    info = parse_system_info(body)
    assert {d["mount"] for d in info["disks"]} >= {"/", "/boot/efi"}
    assert info["df_failed"] is False


def test_network_mounts_listed_from_proc_mounts(system_ext):
    from nodvard_deck_ext_system.sysinfo import parse_system_info

    lines = [f"{p[2]} {p[1]} {p[0]}" for p in (l.split() for l in PROC_MOUNTS.splitlines()) if p[2] in ("nfs4", "cifs")]
    info = parse_system_info("@@netmounts\n" + "\n".join(lines) + "\n@@end\n")
    assert info["network_mounts"] == [
        {"fstype": "nfs4", "mount": "/mnt/pve/nas", "device": "nas.example:/export/backup"},
        {"fstype": "cifs", "mount": "/mnt/pve/my share", "device": "//nas.example/share"},
    ]


def test_awk_selects_only_network_rows(system_ext, tmp_path):
    from nodvard_deck_ext_system.sysinfo import DF_CMD

    mounts = tmp_path / "mounts"
    mounts.write_text(PROC_MOUNTS)
    env = _fake_df(tmp_path, "true\n")
    cmd = DF_CMD.replace("/proc/mounts", str(mounts))
    out = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True, timeout=25).stdout
    assert "nfs4 /mnt/pve/nas nas.example:/export/backup" in out
    assert "ext4" not in out


def test_timeout_flag_reaches_both_parsers(system_ext):
    from nodvard_deck_ext_system.live import parse_live
    from nodvard_deck_ext_system.sysinfo import parse_system_info

    text = "@@dfstatus\ntimeout\n@@netmounts\nnfs4 /mnt/x srv:/x\n@@end\n"
    assert parse_system_info(text)["df_failed"] is True
    assert parse_live(text)["df_failed"] is True
    assert parse_live(text)["network_mounts"][0]["mount"] == "/mnt/x"


def _gone(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except (FileNotFoundError, ProcessLookupError):
        return True


def test_no_fixed_fallback_when_mktemp_fails(system_ext, tmp_path):
    called = tmp_path / "df-called"
    env = _fake_df(tmp_path, f"touch {called}\n") | {"TMPDIR": str(tmp_path / "gibt-es-nicht")}
    before = set(Path("/tmp").glob(".nodvard-df*"))
    out = subprocess.run(["bash", "-c", _scripts(system_ext)["SCRIPT"]], env=env, capture_output=True, text=True, timeout=25).stdout
    assert "@@dfstatus\nunavailable" in out and "@@end" in out
    assert not called.exists()
    assert set(Path("/tmp").glob(".nodvard-df*")) == before


def test_df_without_any_rows_and_exit_1_is_failed(system_ext, tmp_path):
    env = _fake_df(tmp_path, "echo 'df: /srv: Input/output error' >&2\nexit 1\n")
    out = subprocess.run(["bash", "-c", _scripts(system_ext)["SCRIPT"]], env=env, capture_output=True, text=True, timeout=25).stdout
    assert "@@dfstatus\nfailed" in out


def test_rows_with_exit_1_for_one_unreadable_mount_still_count_as_ok(system_ext, tmp_path):
    # GNU df endet mit 1, wenn nur eine Einhängung nicht lesbar war (Portal-FUSE-Mount unter /run/user).
    env = _fake_df(tmp_path, f"echo 'df: /run/user/1000/doc: Operation not permitted' >&2\ncat <<'X'\n{DF_WITH_NFS}X\nexit 1\n")
    out = subprocess.run(["bash", "-c", _scripts(system_ext)["SCRIPT"]], env=env, capture_output=True, text=True, timeout=25).stdout
    assert "@@dfstatus\nok" in out and "/boot/efi" in out


def test_warn_parser_carries_df_status(system_ext):
    from nodvard_deck_ext_system.warnung import parse_warn_output

    assert parse_warn_output("@@disks\n@@dfstatus\ntimeout\n@@end\n")["df_status"] == "timeout"
    assert "df_status" not in parse_warn_output("@@disks\n@@end\n")


def test_whole_seconds_when_sleep_has_no_fractions(system_ext, tmp_path):
    env = _fake_df(tmp_path, "sleep 1\ncat <<'X'\n" + DF_WITH_NFS + "X\n")
    sleep = tmp_path / "bin" / "sleep"
    sleep.write_text('#!/bin/sh\ncase "$1" in *.*) exit 1;; esac\nexec /bin/sleep "$@"\n')
    sleep.chmod(sleep.stat().st_mode | stat.S_IEXEC)
    out = subprocess.run(["bash", "-c", _scripts(system_ext)["SCRIPT"]], env=env, capture_output=True, text=True, timeout=25).stdout
    assert "@@dfstatus\nok" in out


def test_abort_removes_file_and_stops_df(system_ext, tmp_path):
    pidfile = tmp_path / "df.pid"
    env = _fake_df(tmp_path, f"echo $$ > {pidfile}\nsleep 60\n")
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    env["TMPDIR"] = str(scratch)
    proc = subprocess.Popen(["bash", "-c", _scripts(system_ext)["SCRIPT"]], env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    assert any(scratch.iterdir()) and pidfile.exists()
    pid = int(pidfile.read_text())
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)
    time.sleep(0.3)
    assert not any(scratch.iterdir())
    assert _gone(pid)


@pytest.mark.parametrize("name", ["SCRIPT", "LIVE_SCRIPT", "WARN_SCRIPT"])
@pytest.mark.parametrize("end", ["", "exit 1\n"], ids=["ok", "failed"])
def test_normal_run_leaves_no_file(system_ext, tmp_path, name, end):
    target = tmp_path / "df-target"
    env = _fake_df(tmp_path, f"readlink /proc/$$/fd/1 | cat > {target}\ncat <<'X'\n{DF_WITH_NFS}X\n{end}")
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    env["TMPDIR"] = str(scratch)
    out = subprocess.run(["bash", "-c", _scripts(system_ext)[name]], env=env, capture_output=True, text=True, timeout=25).stdout
    assert "@@end" in out and "/dev/mapper/pve-root" in out
    assert Path(target.read_text().strip()).parent == scratch
    assert not any(scratch.iterdir())


@pytest.mark.parametrize("shell", ["bash", "dash"])
def test_signal_after_df_ended_kills_no_process(system_ext, tmp_path, shell):
    from nodvard_deck_ext_system.sysinfo import DF_CMD

    if shutil.which(shell) is None:
        pytest.skip(f"{shell} fehlt")
    kills = tmp_path / "kills"
    env = _fake_df(tmp_path, "true\n")
    # Das Signal kommt, während `tail` läuft: `df` ist dann schon abgeholt, seine Nummer kann neu vergeben sein.
    tail = tmp_path / "bin" / "tail"
    tail.write_text("#!/bin/sh\nkill -TERM $PPID\n")
    tail.chmod(tail.stat().st_mode | stat.S_IEXEC)
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    env["TMPDIR"] = str(scratch)
    spy = f'kill() {{ echo "$*" >> {kills}; command kill "$@"; }}\n'
    proc = subprocess.run([shell, "-c", spy + DF_CMD], env=env, capture_output=True, text=True, timeout=25)
    assert proc.returncode == 1 and "@@dfstatus" not in proc.stdout
    assert kills.read_text().splitlines()[-1].split() == ["-9"]
    assert not any(scratch.iterdir())
