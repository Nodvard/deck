"""Haertungs-Audit (Lynis) auf dem Server: der Befehl in einer echten Shell (Stub-Programme statt Lynis, Testordner statt
/var/lib und /var/log, ohne `as_root`, wie in test_ext_shield_detached.py). Lynis laeuft entkoppelt, mit niedriger
Prioritaet und Obergrenze, und nur ein frischer Bericht zaehlt. Den Ablauf im Dashboard prueft test_ext_shield_audit.py."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"))

from nodvard_deck.core.deny_patterns import match_deny_patterns  # noqa: E402
from nodvard_deck_ext_shield import antivirus as av  # noqa: E402
from nodvard_deck_ext_shield import audit_job as aj  # noqa: E402
from nodvard_deck_ext_shield import detached as dt  # noqa: E402

RID = "audit_0123456789abcdef"
REPORT = (
    "warning[]=SSH-7408|Consider hardening SSH configuration|-|-|\n"
    "suggestion[]=AUTH-9286|Configure maximum password age|-|-|\n"
    "hardening_index=68\nfinish=true\n"
)

posix = pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")


# --- Befehl auf dem Server, echte Shell -------------------------------------------------------------------------------


def _stub(bindir: Path, name: str, body: str) -> Path:
    bindir.mkdir(parents=True, exist_ok=True)
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def _env(bindir: Path) -> dict[str, str]:
    return {"PATH": f"{bindir}:/usr/local/bin:/usr/bin:/bin", "HOME": str(bindir)}


def _alive(pid: int) -> bool:
    """Lebt der Prozess noch (ein Zombie zaehlt als beendet)?"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _wait_for(check, timeout: float = 20.0) -> None:
    end = time.monotonic() + timeout
    while not check():
        if time.monotonic() > end:
            raise AssertionError("Zeitueberschreitung beim Warten")
        time.sleep(0.05)


def _lynis_stub(bindir: Path, tmp: Path, *, report: str | None = REPORT, sleep_s: float = 1, rc: int = 0) -> None:
    """Stub fuer `lynis audit system ...`: merkt Aufruf, Prioritaet und I/O-Klasse, wartet, schreibt (oder nicht) den
    Bericht. Laeuft er lange (`sleep_s`), haengt ein Unterprozess (`sleep`) an ihm, wie bei Lynis."""
    body = (
        f'echo "$*" >>{tmp}/lynis.calls\n'
        f'echo $$ >{tmp}/lynis.pid.seen\n'
        f'nice >{tmp}/lynis.nice\n'
        f'sleep {sleep_s} &\n'
        f'echo $! >{tmp}/lynis.child\n'
        'wait\n'
    )
    if report is not None:
        body += f"cat >{tmp}/report.dat <<'EOF'\n{report}EOF\n"
    body += f"exit {rc}\n"
    _stub(bindir, "lynis", body)


def _command(tmp: Path, **kw) -> str:
    return aj.lynis_command(RID, base_dir=str(tmp / "jobs"), report=str(tmp / "report.dat"),
                            pid_files=(str(tmp / "run-lynis.pid"),), **kw)


def _run_inner(tmp: Path, env: dict[str, str], **kw) -> str:
    (tmp / "jobs").mkdir(exist_ok=True)
    out = subprocess.run(["/bin/sh", "-c", _command(tmp, **kw)], capture_output=True, text=True, timeout=60, env=env,
                         check=False)
    return out.stdout + out.stderr


def _launch(tmp: Path, env: dict[str, str], command: str) -> dt.Launch:
    jobs = tmp / "jobs"
    script = dt.launch_command(RID, command, base_dir=str(jobs), systemd_marker=str(tmp / "kein-systemd"), kind=aj.AUDITS)
    out = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, timeout=30, env=env, check=False)
    assert out.returncode == 0, out.stdout + out.stderr
    return dt.parse_launch(out.stdout)


def _poll(tmp: Path, env: dict[str, str], *, timeout: float = 30) -> dt.Poll:
    end = time.monotonic() + timeout
    while True:
        out = subprocess.run(["/bin/sh", "-c", aj.poll_command(RID, base_dir=str(tmp / "jobs"))], capture_output=True,
                             text=True, timeout=10, env=env, check=False)
        p = dt.parse_poll(out.stdout)
        if p.state != "running" or time.monotonic() > end:
            return p
        time.sleep(0.1)


@posix
def test_lynis_runs_detached_with_low_priority_and_its_fresh_report_is_taken(tmp_path):
    """Der ganze Weg auf dem Server: entkoppelt starten, die startende Shell ist sofort zurueck, Lynis laeuft mit
    `nice -n 19` und `ionice -c3` unter `timeout`, danach steht der frische Bericht im Protokoll des Laufs."""
    bindir = tmp_path / "bin"
    _lynis_stub(bindir, tmp_path)
    _stub(bindir, "ionice", f'echo "$*" >>{tmp_path}/ionice.calls\nshift\nexec "$@"\n')
    env = _env(bindir)
    launch = _launch(tmp_path, env, _command(tmp_path))
    assert launch.started and launch.method == "setsid"
    p = _poll(tmp_path, env)
    assert (p.state, p.rc) == ("done", 0), p
    assert "@@prio=nice -n 19 ionice -c3" in p.output
    res = aj.parse_result(p.output)
    assert (res.status, res.hardening_index) == ("ok", 68)
    assert res.warnings == ["SSH-7408: Consider hardening SSH configuration"]
    assert res.suggestions == ["AUTH-9286: Configure maximum password age"]
    assert (tmp_path / "lynis.calls").read_text() == "audit system --quick --quiet --no-colors\n"
    assert (tmp_path / "lynis.nice").read_text().strip() == "19"
    assert "-c3 lynis audit system --quick --quiet --no-colors" in (tmp_path / "ionice.calls").read_text()
    assert oct((tmp_path / "jobs").stat().st_mode & 0o777) == "0o700"


@posix
def test_without_a_usable_ionice_lynis_runs_with_nice_only(tmp_path):
    """`ionice` gibt es nicht ueberall, und in manchen Umgebungen laesst es sich nicht setzen: Lynis laeuft trotzdem."""
    bindir = tmp_path / "bin"
    _lynis_stub(bindir, tmp_path)
    _stub(bindir, "ionice", "exit 1\n")
    out = _run_inner(tmp_path, _env(bindir))
    assert "@@prio=nice -n 19\n" in out, out
    assert aj.parse_result(out).status == "ok"
    assert (tmp_path / "lynis.nice").read_text().strip() == "19"


@posix
@pytest.mark.skipif(any(Path(d, n).exists() for d in ("/usr/local/sbin", "/usr/sbin", "/sbin") for n in ("nice", "ionice")),
                    reason="nice/ionice liegen hier in einem sbin-Ordner, den der Befehl selbst ergaenzt")
def test_without_nice_and_ionice_lynis_still_runs(tmp_path):
    """Sehr kleine Systeme: weder `nice` noch `ionice` im Suchpfad."""
    bindir, tools = tmp_path / "bin", tmp_path / "tools"
    _lynis_stub(bindir, tmp_path)
    tools.mkdir()
    for name in ("cat", "tr", "grep", "flock", "timeout", "sleep"):
        found = shutil.which(name)
        if found:
            (tools / name).symlink_to(found)
    out = _run_inner(tmp_path, {"PATH": f"{bindir}:{tools}", "HOME": str(bindir)})
    assert "@@prio=normal" in out, out
    assert aj.parse_result(out).status == "ok"


@posix
def test_at_the_limit_timeout_ends_lynis_and_its_children_on_the_server(tmp_path):
    """Obergrenze erreicht: `timeout` beendet Lynis samt Unterprozess auf dem Server (nicht nur die Verbindung), und die
    Meldung ist verstaendlich."""
    bindir = tmp_path / "bin"
    _lynis_stub(bindir, tmp_path, sleep_s=60)
    env = _env(bindir)
    assert _launch(tmp_path, env, _command(tmp_path, limit_s=2)).started
    p = _poll(tmp_path, env)
    assert p.state == "done" and "@@timeout" in p.output, p
    for name in ("lynis.pid.seen", "lynis.child"):
        pid = int((tmp_path / name).read_text())
        _wait_for(lambda pid=pid: not _alive(pid), timeout=10)
    res = aj.parse_result(p.output)
    assert res.status == "error"
    assert res.error == "Das Audit hat länger als 3 Stunden gedauert und wurde auf dem Server beendet."


@posix
def test_without_timeout_the_stop_command_ends_lynis_and_its_children(tmp_path):
    """Ohne `timeout` aus den coreutils (z. B. BusyBox) beendet das Dashboard den Lauf nach der Obergrenze selbst."""
    bindir = tmp_path / "bin"
    _lynis_stub(bindir, tmp_path, sleep_s=60)
    _stub(bindir, "timeout", "exit 1\n")  # kein coreutils-timeout: `--version` nennt es nicht
    _stub(bindir, "systemctl", f'echo "$*" >>{tmp_path}/systemctl.calls\nexit 1\n')
    env = _env(bindir)
    launch = _launch(tmp_path, env, _command(tmp_path, limit_s=2))
    assert launch.started
    _wait_for(lambda: (tmp_path / "lynis.child").exists() and (tmp_path / "lynis.child").read_text().strip() != "")
    time.sleep(2.5)  # laenger als die Obergrenze: ohne timeout laeuft Lynis weiter
    lynis_pid, child_pid = (int((tmp_path / n).read_text()) for n in ("lynis.pid.seen", "lynis.child"))
    assert _alive(lynis_pid) and _alive(child_pid)
    assert _poll(tmp_path, env, timeout=0).state == "running"

    out = subprocess.run(["/bin/sh", "-c", aj.stop_command(RID, base_dir=str(tmp_path / "jobs"))], capture_output=True,
                         text=True, timeout=60, env=env, check=False)
    assert out.stdout.split() == ["@@stopped"], out.stdout + out.stderr
    for pid in (launch.pid, lynis_pid, child_pid):
        _wait_for(lambda pid=pid: not _alive(pid), timeout=10)
    assert f"stop --no-block nodvard-shield-audit-{RID}.service" in (tmp_path / "systemctl.calls").read_text()
    assert _poll(tmp_path, env, timeout=0).state == "lost"
    # Ein zweites Mal: nichts mehr zu beenden.
    again = subprocess.run(["/bin/sh", "-c", aj.stop_command(RID, base_dir=str(tmp_path / "jobs"))], capture_output=True,
                           text=True, timeout=60, env=env, check=False)
    assert again.stdout.split() == ["@@notrunning"]


@posix
def test_an_old_report_never_counts_as_the_result(tmp_path):
    """Steht von einem frueheren Lauf noch ein Bericht da und schreibt Lynis diesmal keinen (oder scheitert vorher),
    ist das kein Ergebnis."""
    bindir = tmp_path / "bin"
    report = tmp_path / "report.dat"
    report.write_text(REPORT)
    old = time.time() - 86400
    os.utime(report, (old, old))
    _lynis_stub(bindir, tmp_path, report=None, sleep_s=0, rc=1)
    out = _run_inner(tmp_path, _env(bindir))
    assert "@@stale" in out and "@@report" not in out, out
    res = aj.parse_result(out)
    assert res.status == "error" and res.hardening_index is None
    assert res.error.startswith("Lynis hat keinen neuen Bericht geschrieben (Rückgabecode 1).")

    # Ein Verweis statt einer Datei zaehlt auch nicht (der Bericht wird als root gelesen).
    report.unlink()
    real = tmp_path / "anderer-bericht.dat"
    real.write_text(REPORT)
    report.symlink_to(real)
    _lynis_stub(bindir, tmp_path, report=None, sleep_s=0)
    assert "@@stale" in _run_inner(tmp_path, _env(bindir))


@posix
def test_a_report_that_lynis_did_not_finish_is_not_a_result(tmp_path):
    bindir = tmp_path / "bin"
    _lynis_stub(bindir, tmp_path, report="warning[]=SSH-7408|Consider hardening SSH configuration|-|-|\n")
    res = aj.parse_result(_run_inner(tmp_path, _env(bindir)))
    assert (res.status, res.error) == ("error", aj.INCOMPLETE_MESSAGE)


@posix
def test_no_second_lynis_while_one_is_running(tmp_path):
    """Laeuft schon ein Lynis-Audit (von Hand, von einer frueheren Version oder ein zweiter Lauf von hier), startet kein
    zweites: beide schrieben sonst in denselben Bericht."""
    bindir = tmp_path / "bin"
    _lynis_stub(bindir, tmp_path)
    env = _env(bindir)
    # 1. Ein anderes Lynis laeuft, seine PID-Datei steht da.
    other = _stub(tmp_path / "anderes", "lynis", "sleep 30\n:\n")
    proc = subprocess.Popen([str(other), "audit", "system"], start_new_session=True)
    try:
        (tmp_path / "run-lynis.pid").write_text(f"{proc.pid}\n")
        _wait_for(lambda: b"lynis\x00audit" in Path(f"/proc/{proc.pid}/cmdline").read_bytes())
        out = _run_inner(tmp_path, env)
        assert out.split() == ["@@busy"], out
        assert aj.parse_result(out).error == aj.BUSY_MESSAGE
        assert not (tmp_path / "lynis.calls").exists()
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
    # Ein Rest der PID-Datei (Prozess weg) haelt nichts auf.
    assert aj.parse_result(_run_inner(tmp_path, env)).status == "ok"

    # 2. Die Sperre im eigenen Ordner ist belegt (ein zweiter Lauf von hier).
    if shutil.which("flock", path=env["PATH"]):
        holder = subprocess.Popen(["flock", str(tmp_path / "jobs" / "lynis.lock"), "sleep", "30"], start_new_session=True)
        try:
            time.sleep(0.5)
            assert _run_inner(tmp_path, env).split() == ["@@busy"]
        finally:
            os.killpg(holder.pid, signal.SIGKILL)
            holder.wait(timeout=10)


@posix
def test_without_lynis_the_run_ends_at_once_with_a_clear_message(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    out = _run_inner(tmp_path, {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(bindir)})
    assert aj.parse_result(out).error == "Lynis ist auf diesem Server nicht installiert."


def test_audit_commands_are_validated_quoted_and_pass_the_deny_list():
    inner = aj.lynis_command(RID)
    launch = dt.launch_command(RID, inner, kind=aj.AUDITS)
    for cmd in (inner, launch, aj.poll_command(RID), aj.stop_command(RID)):
        assert match_deny_patterns(av.as_root(cmd)) is None, cmd
    assert "nice -n 19" in inner and "ionice -c3" in inner and "timeout -k 60 10800" in inner
    assert "/var/log/lynis-report.dat -nt /var/lib/nodvard-shield-audit/" in inner
    assert f"systemd-run --unit=nodvard-shield-audit-{RID} --collect --quiet --description='Nodvard Shield Audit {RID}'" in launch
    assert "d=/var/lib/nodvard-shield-audit;" in launch and "-name 'audit_*' -mtime +30" in launch
    for bad in ("upd_0123456789abcdef", "audit_123", "audit_0123456789ABCDEF", "x; rm -rf /", "../audit_0123456789abcdef"):
        for build in (aj.lynis_command, aj.poll_command, aj.stop_command, lambda r: dt.launch_command(r, "true", kind=aj.AUDITS)):
            with pytest.raises(ValueError):
                build(bad)
    for bad_path in ("relativ", "/tmp/a b", "/tmp/'x'", "/tmp/$(id)", "/tmp/../etc/shadow"):
        with pytest.raises(ValueError):
            aj.lynis_command(RID, report=bad_path)
        with pytest.raises(ValueError):
            aj.lynis_command(RID, pid_files=(bad_path,))
    with pytest.raises(ValueError):
        aj.lynis_command(RID, limit_s=0)
    # Die Update-Laeufe bleiben, wie sie waren.
    assert "--unit=lattice-upgrade-upd_0123456789abcdef" in dt.launch_command("upd_0123456789abcdef", "true")
    with pytest.raises(ValueError):
        dt.launch_command(RID, "true")
