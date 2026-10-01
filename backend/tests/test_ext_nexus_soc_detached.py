"""Einspiel-Laeufe entkoppelt vom SSH-Kanal -- mit einer echten Shell.

Statt apt laufen kleine Stub-Befehle, statt /var/lib ein Testordner, ohne systemd
(der setsid-Weg wird echt geprueft, systemd-run nur als Fake auf PATH). Bewusst ohne
`as_root`: auf CI-Runnern mit sudo ohne Passwort liefe sonst das echte apt-get."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "nexus-soc" / "src"))

from nodvard_deck.core.deny_patterns import match_deny_patterns
from nodvard_deck_ext_nexus_soc import detached as dt
from nodvard_deck_ext_nexus_soc import updates as up

RID = "upd_0123456789abcdef"

posix = pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")


def _stub(bindir: Path, name: str, body: str) -> None:
    bindir.mkdir(exist_ok=True)
    (bindir / name).write_text("#!/bin/sh\n" + body)
    (bindir / name).chmod(0o755)


def _env(bindir: Path) -> dict[str, str]:
    return {"PATH": f"{bindir}:/usr/local/bin:/usr/bin:/bin", "HOME": str(bindir)}


def _poll(jobs: Path, env: dict[str, str], *, run_id: str = RID, timeout: float = 20) -> dt.Poll:
    end = time.monotonic() + timeout
    while True:
        out = subprocess.run(["sh", "-c", dt.poll_command(run_id, base_dir=str(jobs))], capture_output=True, text=True,
                             timeout=10, env=env, check=False)
        p = dt.parse_poll(out.stdout)
        if p.state != "running" or time.monotonic() > end:
            return p
        time.sleep(0.2)


def _launch(jobs: Path, command: str, env: dict[str, str], *, marker: Path, run_id: str = RID) -> tuple[dt.Launch, float]:
    t0 = time.monotonic()
    out = subprocess.run(["sh", "-c", dt.launch_command(run_id, command, base_dir=str(jobs), systemd_marker=str(marker))],
                         capture_output=True, text=True, timeout=30, env=env, check=False)
    assert out.returncode == 0, out.stdout + out.stderr
    return dt.parse_launch(out.stdout), time.monotonic() - t0


@posix
def test_the_run_survives_when_the_launching_shell_and_its_channel_die(tmp_path):
    """Der echte apt-Befehl (Sicherheitsupdates) mit einem apt-get-Stub: Die
    startende Shell wird samt Prozessgruppe per SIGHUP/SIGKILL beendet und ihre
    Ausgabe-Pipe geschlossen, waehrend das "Update" noch laeuft -- es laeuft trotzdem
    zu Ende und legt Protokoll und Rueckgabecode ab."""
    bindir, jobs = tmp_path / "bin", tmp_path / "jobs"
    _stub(bindir, "apt-get", (
        'echo "apt-get $* FRONTEND=$DEBIAN_FRONTEND"\n'
        'case "$*" in *only-upgrade*) sleep 2; echo "Setting up openssl ..."; '
        'echo "2 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.";; esac\n'
    ))
    command = up.upgrade_command("apt", "security", ["openssl", "libssl3"])
    script = dt.launch_command(RID, command, base_dir=str(jobs), systemd_marker=str(tmp_path / "kein-systemd"))
    proc = subprocess.Popen(["sh", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env=_env(bindir), start_new_session=True)
    seen = []
    for line in proc.stdout:
        seen.append(line.strip())
        if line.startswith(("@@pid=", "@@nopid")):
            break
    # Kanal weg: Pipe zu, SIGHUP und SIGKILL an die ganze Gruppe der startenden Shell.
    proc.stdout.close()
    for sig in (signal.SIGHUP, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
    proc.wait(timeout=10)
    launch = dt.parse_launch("\n".join(seen))
    assert launch.started and launch.method == "setsid", seen
    assert not (jobs / f"{RID}.rc").exists()  # laeuft noch

    p = _poll(jobs, _env(bindir))
    assert (p.state, p.rc) == ("done", 0), p
    res = up.parse_upgrade_output(p.output, p.rc)
    assert res.ok and res.summary == "2 Paket(e) aktualisiert"
    log = (jobs / f"{RID}.log").read_text()
    assert "install --only-upgrade openssl libssl3" in log and "FRONTEND=noninteractive" in log
    assert log.rstrip().endswith(up.UPGRADE_DONE)
    assert (jobs / f"{RID}.rc").read_text().strip() == "0"
    assert oct((jobs).stat().st_mode & 0o777) == "0o700"


@posix
def test_the_launch_returns_at_once_and_does_not_hold_the_channel(tmp_path):
    """Kein Datei-Deskriptor des Kanals wird vererbt: der Start kehrt zurueck, obwohl
    der Lauf noch Sekunden dauert (sonst wartete `conn.run` auf ihn)."""
    bindir, jobs = tmp_path / "bin", tmp_path / "jobs"
    launch, took = _launch(jobs, "sleep 4; echo fertig", _env(bindir), marker=tmp_path / "kein-systemd")
    assert launch.started and took < 3.5
    p = _poll(jobs, _env(bindir))
    assert (p.state, p.rc) == ("done", 0) and "fertig" in p.output


@posix
def test_a_failed_run_keeps_its_exit_code_and_error(tmp_path):
    bindir, jobs = tmp_path / "bin", tmp_path / "jobs"
    command = "echo \"E: dpkg was interrupted, you must manually run 'dpkg --configure -a'\"; exit 100"
    launch, _ = _launch(jobs, command, _env(bindir), marker=tmp_path / "kein-systemd")
    assert launch.started
    p = _poll(jobs, _env(bindir))
    assert (p.state, p.rc) == ("done", 100)
    res = up.parse_upgrade_output(p.output, p.rc)
    assert not res.ok and res.summary.startswith("E: dpkg was interrupted")


@posix
def test_a_killed_run_is_reported_as_lost_not_as_running(tmp_path):
    bindir, jobs = tmp_path / "bin", tmp_path / "jobs"
    launch, _ = _launch(jobs, "echo angefangen; sleep 30", _env(bindir), marker=tmp_path / "kein-systemd")
    assert launch.started
    time.sleep(0.3)
    assert _poll(jobs, _env(bindir), timeout=0).state == "running"
    os.killpg(launch.pid, signal.SIGKILL)  # eigene Sitzung (setsid): Wrapper samt sleep
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and Path(f"/proc/{launch.pid}").exists():
        time.sleep(0.1)
    p = _poll(jobs, _env(bindir), timeout=0)
    assert p.state == "lost" and "angefangen" in p.output
    assert _poll(jobs, _env(bindir), run_id="upd_ffffffffffffffff", timeout=0).state == "unknown"


# Wie systemd-executor (replace_env_argv): `$NAME`/`${NAME}` werden durch die Umgebung
# ersetzt (unbekannt -> leer), aus `$$` wird `$`. Ohne das merkte der Test nicht, dass
# ein unverdoppeltes `$$` im Wrapper als `$` in der pid-Datei landet.
_SYSTEMD_EXPAND = r"""
import os, re, subprocess, sys
pat = re.compile(r"\$(\$|\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)")
def expand(s):
    return pat.sub(lambda m: "$" if m.group(1) == "$" else os.environ.get(m.group(1).strip("{}"), ""), s)
subprocess.Popen(["setsid"] + [expand(a) for a in sys.argv[1:]], stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
"""


def _fake_systemd_run(bindir: Path, args_file: Path, *, fail: bool) -> None:
    body = f'printf "%s\\n" "$@" >{args_file}\n'
    if fail:
        body += 'echo "Failed to connect to bus" >&2; exit 1\n'
    else:
        # Optionen weglassen, den Rest ("/bin/sh -c <wrapper>") wie systemd expandiert
        # und entkoppelt starten.
        bindir.mkdir(parents=True, exist_ok=True)
        (bindir / "_systemd_expand.py").write_text(_SYSTEMD_EXPAND)
        body += f'while [ "${{1#--}}" != "$1" ]; do shift; done\npython3 {bindir / "_systemd_expand.py"} "$@"\n'
    _stub(bindir, "systemd-run", body)


@posix
def test_systemd_run_is_preferred_when_systemd_runs(tmp_path):
    bindir, jobs, marker = tmp_path / "bin", tmp_path / "jobs", tmp_path / "systemd"
    marker.mkdir()
    args = tmp_path / "args"
    _fake_systemd_run(bindir, args, fail=False)
    # `$x` im Befehl und `$$` im Wrapper muessen die Expansion durch systemd heil
    # ueberstehen: echte Prozessnummer in der pid-Datei, "wert=5" im Protokoll.
    launch, _ = _launch(jobs, 'x=5; echo "per-systemd wert=$x"; sleep 2', _env(bindir), marker=marker)
    assert (launch.method, launch.started) == ("systemd", True)
    assert launch.pid is not None and str(launch.pid).isdigit()
    assert _poll(jobs, _env(bindir), timeout=0).state == "running"
    argv = args.read_text().splitlines()
    assert argv[:3] == [f"--unit=lattice-upgrade-{RID}", "--collect", "--quiet"]
    assert argv[-3:-1] == ["/bin/sh", "-c"] and f"{RID}.rc" in argv[-1]
    p = _poll(jobs, _env(bindir))
    assert (p.state, p.rc) == ("done", 0) and "per-systemd wert=5" in p.output


@posix
def test_a_failing_systemd_run_falls_back_to_setsid(tmp_path):
    bindir, jobs, marker = tmp_path / "bin", tmp_path / "jobs", tmp_path / "systemd"
    marker.mkdir()
    _fake_systemd_run(bindir, tmp_path / "args", fail=True)
    launch, _ = _launch(jobs, "echo ersatzweise", _env(bindir), marker=marker)
    assert (launch.method, launch.started) == ("setsid-fallback", True)
    p = _poll(jobs, _env(bindir))
    assert (p.state, p.rc) == ("done", 0) and "ersatzweise" in p.output


@posix
def test_the_same_run_is_never_started_twice(tmp_path):
    bindir, jobs, counter = tmp_path / "bin", tmp_path / "jobs", tmp_path / "count"
    command = f"echo x >>{counter}"
    first, _ = _launch(jobs, command, _env(bindir), marker=tmp_path / "kein-systemd")
    assert _poll(jobs, _env(bindir)).state == "done"
    again, _ = _launch(jobs, command, _env(bindir), marker=tmp_path / "kein-systemd")
    assert again.exists and again.pid == first.pid and again.method is None
    assert counter.read_text() == "x\n"


@posix
def test_old_logs_are_cleaned_up_on_each_new_run(tmp_path):
    bindir, jobs = tmp_path / "bin", tmp_path / "jobs"
    jobs.mkdir()
    old = time.time() - 40 * 86400
    for name in ("upd_aaaaaaaaaaaaaaaa.log", "upd_aaaaaaaaaaaaaaaa.rc", "fremd.log"):
        (jobs / name).write_text("alt")
        os.utime(jobs / name, (old, old))
    (jobs / "upd_bbbbbbbbbbbbbbbb.log").write_text("neu")
    _launch(jobs, "true", _env(bindir), marker=tmp_path / "kein-systemd")
    names = sorted(p.name for p in jobs.iterdir())
    assert "upd_aaaaaaaaaaaaaaaa.log" not in names and "upd_aaaaaaaaaaaaaaaa.rc" not in names
    assert "fremd.log" in names and "upd_bbbbbbbbbbbbbbbb.log" in names


def test_commands_are_validated_quoted_and_pass_the_deny_list():
    command = up.upgrade_command("apt", "all")
    launch = dt.launch_command(RID, command)
    assert f"systemd-run --unit=lattice-upgrade-{RID} --collect --quiet" in launch
    assert "nohup setsid /bin/sh -c" in launch and "/var/lib/nexus-updates" in launch
    assert "-mtime +30" in launch
    for cmd in (launch, dt.poll_command(RID)):
        assert match_deny_patterns(cmd) is None, cmd
    for bad in ("upd_123", "upd_0123456789ABCDEF", "x; rm -rf /", "../upd_0123456789abcdef"):
        with pytest.raises(ValueError):
            dt.launch_command(bad, "true")
        with pytest.raises(ValueError):
            dt.poll_command(bad)
    for bad_dir in ("relativ", "/tmp/a b", "/tmp/'x'", "/tmp/$(id)", "/tmp/../etc"):
        with pytest.raises(ValueError):
            dt.poll_command(RID, base_dir=bad_dir)
    assert dt.paths(RID)["log"] == f"/var/lib/nexus-updates/{RID}.log"


def test_parse_poll_and_launch():
    assert dt.parse_launch("@@launch\n@@method=systemd\n@@pid=42\n") == dt.Launch(method="systemd", pid=42)
    assert not dt.parse_launch("@@launch\n@@method=setsid\n@@nopid\n").started
    run = dt.parse_poll("@@running\n@@tail\nUnpacking openssl ...\n")
    assert (run.state, run.progress) == ("running", "Unpacking openssl ...")
    done = dt.parse_poll("@@rc=0\n@@summary\n2 upgraded, 0 newly installed, 0 to remove\n@@tail\nSetting up x\n@@upgrade-done\n")
    assert (done.state, done.rc) == ("done", 0) and done.output.startswith("2 upgraded") and "@@upgrade-done" in done.output
    assert dt.parse_poll("@@rc=\n@@summary\n@@tail\n").rc is None
    lost = dt.parse_poll("@@lost\n@@summary\nThe following packages are only half configured\n@@tail\nSetting up docker-ce\n")
    assert lost.state == "lost" and "half configured" in lost.output
    assert dt.parse_poll("@@unknown\n@@tail\n").state == "unknown"
    # Leere oder fremde Antwort (Kanal verloren) ist etwas anderes als "der Server kennt den Lauf nicht".
    assert dt.parse_poll("").state == "noreply"
    assert dt.parse_poll("Connection to host closed.").state == "noreply"


@posix
@pytest.mark.parametrize("lang", ["en", "de"])
def test_the_poll_still_finds_kept_back_packages_when_the_log_is_long(tmp_path, lang):
    """Der apt-Abschnitt "kept back" steht frueh im Protokoll und liegt nach einem
    langen Lauf ausserhalb der letzten 20 000 Zeichen. Die Abfrage zieht ihn deshalb in
    eine Zeile; daraus entsteht die Meldung im Ergebnis."""
    bindir, jobs = tmp_path / "bin", tmp_path / "jobs"
    jobs.mkdir()
    head = {
        "en": "The following packages have been kept back:\n  proxmox-ve pve-manager\nThe following packages will be upgraded:\n  openssl\n",
        "de": "Die folgenden Pakete wurden zurückgehalten:\n  proxmox-ve\n  pve-manager\nDie folgenden Pakete werden aktualisiert:\n  openssl\n",
    }[lang]
    filler = "".join(f"Setting up libfoo{i} (1.{i}) ...\n" for i in range(1500))  # weit ueber 20 000 Zeichen
    (jobs / f"{RID}.log").write_text(
        "Reading package lists...\n" + head + "1 upgraded, 0 newly installed, 0 to remove and 2 not upgraded.\n" + filler + up.UPGRADE_DONE + "\n",
        encoding="utf-8")
    (jobs / f"{RID}.rc").write_text("0\n")
    out = subprocess.run(["sh", "-c", dt.poll_command(RID, base_dir=str(jobs))], capture_output=True, text=True,
                         timeout=10, env=_env(bindir), check=False)
    assert len(filler) > 20000 and "kept back" not in out.stdout.split("@@tail")[1] and "zurückgehalten" not in out.stdout.split("@@tail")[1]
    p = dt.parse_poll(out.stdout)
    assert (p.state, p.rc) == ("done", 0)
    assert "@@kept-back: proxmox-ve pve-manager" in p.output
    res = up.parse_upgrade_output(p.output, p.rc)
    assert res.ok and res.kept_back == ["proxmox-ve", "pve-manager"]
    assert res.summary == "1 Paket(e) aktualisiert – 2 zurückgehalten (proxmox-ve, pve-manager)"


@posix
def test_the_poll_reports_no_kept_back_line_when_apt_kept_nothing_back(tmp_path):
    bindir, jobs = tmp_path / "bin", tmp_path / "jobs"
    jobs.mkdir()
    (jobs / f"{RID}.log").write_text("2 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\n" + up.UPGRADE_DONE + "\n")
    (jobs / f"{RID}.rc").write_text("0\n")
    out = subprocess.run(["sh", "-c", dt.poll_command(RID, base_dir=str(jobs))], capture_output=True, text=True,
                         timeout=10, env=_env(bindir), check=False)
    assert "kept-back" not in out.stdout
    assert up.parse_upgrade_output(dt.parse_poll(out.stdout).output, 0).summary == "2 Paket(e) aktualisiert"


@posix
def test_two_kept_back_headings_in_a_row_stay_on_separate_lines(tmp_path):
    """Kein `@@kept-back: a b@@kept-back: c` in einer Zeile -- sonst ginge "b" verloren."""
    bindir, jobs = tmp_path / "bin", tmp_path / "jobs"
    jobs.mkdir()
    (jobs / f"{RID}.log").write_text(
        "The following packages have been kept back:\n  a b\nThe following packages have been kept back:\n  c\n"
        "1 upgraded, 0 newly installed, 0 to remove and 3 not upgraded.\n" + up.UPGRADE_DONE + "\n")
    (jobs / f"{RID}.rc").write_text("0\n")
    out = subprocess.run(["sh", "-c", dt.poll_command(RID, base_dir=str(jobs))], capture_output=True, text=True,
                         timeout=10, env=_env(bindir), check=False)
    lines = [ln for ln in out.stdout.splitlines() if ln.startswith("@@kept-back")]
    assert lines == ["@@kept-back: a b", "@@kept-back: c"], out.stdout
    assert up.parse_upgrade_output(dt.parse_poll(out.stdout).output, 0).kept_back == ["a", "b", "c"]
