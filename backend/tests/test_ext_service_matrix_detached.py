"""service-matrix: Image-Update-Laeufe entkoppelt vom SSH-Kanal -- mit einer echten Shell.

Statt Docker laeuft ein kleines `docker` auf PATH, das seine Argumente aufschreibt; statt
des echten Home-Ordners ein Testordner (`HOME`). Der setsid-Weg wird echt geprueft."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "extensions" / "service-matrix" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nodvard_deck.core.deny_patterns import match_deny_patterns
from nodvard_deck_ext_service_matrix import detached as dt
from nodvard_deck_ext_service_matrix import image_apply as ia

RID = "imgupd_0123456789abcdef"

posix = pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")


def _stub(bindir: Path, name: str, body: str) -> None:
    bindir.mkdir(exist_ok=True)
    (bindir / name).write_text("#!/bin/sh\n" + body)
    (bindir / name).chmod(0o755)


def _env(home: Path, bindir: Path | None = None) -> dict[str, str]:
    path = f"{bindir}:" if bindir else ""
    return {"PATH": f"{path}/usr/local/bin:/usr/bin:/bin", "HOME": str(home)}


def _jobs(home: Path) -> Path:
    return home / ".local" / "state" / "lattice-image-updates"


def _poll(env: dict[str, str], *, run_id: str = RID, timeout: float = 20) -> dt.Poll:
    end = time.monotonic() + timeout
    while True:
        out = subprocess.run(["sh", "-c", dt.poll_command(run_id)], capture_output=True, text=True, timeout=10, env=env, check=False)
        p = dt.parse_poll(out.stdout)
        if p.state != "running" or time.monotonic() > end:
            return p
        time.sleep(0.2)


def _launch(script: str, env: dict[str, str], *, run_id: str = RID) -> tuple[dt.Launch, float]:
    t0 = time.monotonic()
    out = subprocess.run(["sh", "-c", dt.launch_command(run_id, script)], capture_output=True, text=True, timeout=30, env=env, check=False)
    assert out.returncode == 0, out.stdout + out.stderr
    return dt.parse_launch(out.stdout), time.monotonic() - t0


@posix
def test_the_run_survives_when_the_launching_shell_and_its_channel_die(tmp_path):
    env = _env(tmp_path)
    script = 'echo @@step=pull; sleep 2; echo "Pulled"; echo @@step=up; echo "Recreated"; echo @@step=done'
    proc = subprocess.Popen(["sh", "-c", dt.launch_command(RID, script)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env, start_new_session=True)
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
    assert launch.started and launch.method in ("setsid", "nohup"), seen
    assert not (_jobs(tmp_path) / f"{RID}.rc").exists()  # laeuft noch
    p = _poll(env)
    assert (p.state, p.rc, p.step) == ("done", 0, "done"), p
    assert "Recreated" in p.output
    assert oct(_jobs(tmp_path).stat().st_mode & 0o777) == "0o700"


@posix
def test_the_launch_returns_at_once_and_reports_the_running_step(tmp_path):
    env = _env(tmp_path)
    launch, took = _launch("echo @@step=pull; echo lade; sleep 4; echo @@step=done", env)
    assert launch.started and took < 3.5
    time.sleep(0.3)
    p = _poll(env, timeout=0)
    assert (p.state, p.step, p.progress) == ("running", "pull", "lade")
    done = _poll(env)
    assert (done.state, done.rc) == ("done", 0)


@posix
@pytest.mark.parametrize("rc", [ia.RC_PULL, ia.RC_UP])
def test_failed_steps_keep_their_exit_code(tmp_path, rc):
    env = _env(tmp_path)
    launch, _ = _launch(f"echo @@step=pull; echo 'Error: kaputt'; exit {rc}", env)
    assert launch.started
    p = _poll(env)
    assert (p.state, p.rc, p.step) == ("done", rc, "pull")
    assert "Error: kaputt" in p.output


@posix
def test_a_killed_run_is_reported_as_lost_not_as_running(tmp_path):
    env = _env(tmp_path)
    launch, _ = _launch("echo @@step=pull; echo angefangen; sleep 30", env)
    assert launch.started
    time.sleep(0.3)
    assert _poll(env, timeout=0).state == "running"
    os.killpg(launch.pid, signal.SIGKILL)  # eigene Sitzung (setsid): Wrapper samt sleep
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and Path(f"/proc/{launch.pid}").exists():
        time.sleep(0.1)
    p = _poll(env, timeout=0)
    assert p.state == "lost" and "angefangen" in p.output and p.step == "pull"
    assert _poll(env, run_id="imgupd_ffffffffffffffff", timeout=0).state == "unknown"


@posix
def test_the_same_run_is_never_started_twice(tmp_path):
    env = _env(tmp_path)
    counter = tmp_path / "count"
    script = f"echo x >>{counter}"
    first, _ = _launch(script, env)
    assert _poll(env).state == "done"
    again, _ = _launch(script, env)
    assert again.exists and again.pid == first.pid and again.method is None
    assert counter.read_text() == "x\n"


@posix
def test_old_logs_are_cleaned_up_on_each_new_run(tmp_path):
    env = _env(tmp_path)
    jobs = _jobs(tmp_path)
    jobs.mkdir(parents=True)
    old = time.time() - 40 * 86400
    for name in ("imgupd_aaaaaaaaaaaaaaaa.log", "imgupd_aaaaaaaaaaaaaaaa.rc", "fremd.log"):
        (jobs / name).write_text("alt")
        os.utime(jobs / name, (old, old))
    (jobs / "imgupd_bbbbbbbbbbbbbbbb.log").write_text("neu")
    _launch("true", env)
    names = sorted(p.name for p in jobs.iterdir())
    assert "imgupd_aaaaaaaaaaaaaaaa.log" not in names and "imgupd_aaaaaaaaaaaaaaaa.rc" not in names
    assert "fremd.log" in names and "imgupd_bbbbbbbbbbbbbbbb.log" in names


@posix
def test_an_unusable_home_means_not_started(tmp_path):
    home = tmp_path / "keine-mappe"
    home.write_text("ich bin eine Datei")  # dort laesst sich kein Ordner anlegen
    out = subprocess.run(["sh", "-c", dt.launch_command(RID, "true")], capture_output=True, text=True, timeout=30,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(home)}, check=False)
    assert dt.parse_launch(out.stdout).started is False


def test_commands_are_validated_and_pass_the_deny_list(tmp_path):
    launch = dt.launch_command(RID, "true")
    assert "nohup setsid /bin/sh -c" in launch and "-mtime +30" in launch and "lattice-image-updates" in launch
    for cmd in (launch, dt.poll_command(RID)):
        assert match_deny_patterns(cmd) is None, cmd
    for bad in ("imgupd_123", "imgupd_0123456789ABCDEF", "x; rm -rf /", "../imgupd_0123456789abcdef", RID + "\n", "upd_0123456789abcdef"):
        for build in (lambda r: dt.launch_command(r, "true"), dt.poll_command, dt.log_path):
            with pytest.raises(ValueError):
                build(bad)
    assert dt.log_path(RID) == f"~/.local/state/lattice-image-updates/{RID}.log"


def test_poll_parsing_handles_every_state():
    assert dt.parse_poll("@@running\n@@step=up\n@@tail\nCreating web\n") == dt.Poll("running", progress="Creating web", step="up")
    done = dt.parse_poll("@@rc=20\n@@step=up\n@@tail\nErrors\nmore\n")
    assert (done.state, done.rc, done.step, done.output) == ("done", 20, "up", "Errors\nmore\n")
    assert dt.parse_poll("@@rc=0\n@@step=done\n@@tail\n").state == "done"
    assert dt.parse_poll("@@lost\n@@tail\nletzte Zeile\n").state == "lost"
    assert dt.parse_poll("@@unknown\n@@tail\n").state == "unknown"
    assert dt.parse_poll("").state == "noreply" and dt.parse_poll("Connection reset\n").state == "noreply"
    # Text aus dem Protokoll darf den Kopf nicht vortaeuschen.
    fake = dt.parse_poll("@@running\n@@tail\n@@rc=0\n")
    assert fake.state == "running" and fake.rc is None
    assert dt.parse_launch("@@launch\n@@method=setsid\n@@pid=42\n") == dt.Launch("setsid", 42)
    assert dt.parse_launch("@@launch\n@@nopid\n").started is False
    assert dt.parse_launch("@@launch\n@@exists\n@@pid=7\n").exists


@posix
def test_quoting_end_to_end_paths_with_spaces_reach_docker_exactly(tmp_path):
    """`apply_script` durch `launch_command` (sh -c in setsid): ein `docker`-Stub schreibt
    seine Argumente Zeile fuer Zeile auf -- sie muessen genau stimmen."""
    bindir = tmp_path / "bin"
    record = tmp_path / "argv"
    # `docker image inspect` meldet eine ANDERE ID als die laufende: der Pull hat etwas Neues gebracht.
    _stub(bindir, "docker", (
        f'if [ "$1 $2" = "image inspect" ]; then echo sha256:{"9" * 64}; exit 0; fi\n'
        f'for a in "$@"; do printf "%s\\n" "$a" >>{record}; done; printf "%s\\n" -- >>{record}\n'
    ))
    t = ia.ComposeTarget(
        container="web", container_id="a" * 64, image="nginx:1.27", image_id="sha256:" + "1" * 64, project="mein-projekt", service="web",
        working_dir="/opt/mein stack", config_files=("/opt/mein stack/docker-compose.yml", "/opt/mein stack/override 2.yml"),
        env_files=("/opt/mein stack/prod.env",), config_hash=None,
    )
    script = ia.apply_script(t, old_image_id="sha256:" + "1" * 64, rollback=ia.rollback_ref("web"))
    env = _env(tmp_path, bindir)
    launch, _ = _launch(script, env)
    assert launch.started
    p = _poll(env)
    assert (p.state, p.rc, p.step) == ("done", 0, "done"), p
    calls = [c.split("\n") for c in record.read_text().split("\n--\n") if c]
    common = ["compose", "--ansi", "never", "-p", "mein-projekt", "--project-directory", "/opt/mein stack",
              "-f", "/opt/mein stack/docker-compose.yml", "-f", "/opt/mein stack/override 2.yml", "--env-file", "/opt/mein stack/prod.env"]
    assert calls == [
        common + ["pull", "web"],
        ["tag", "sha256:" + "1" * 64, "lattice-rollback/web-4b5e57f6:previous"],
        common + ["up", "-d", "--no-deps", "--no-build", "web"],
    ]


@posix
def test_a_failing_pull_stops_the_script_with_rc_10_and_never_recreates(tmp_path):
    bindir = tmp_path / "bin"
    record = tmp_path / "calls"
    _stub(bindir, "docker", f'echo "$*" >>{record}; case "$*" in *pull*) echo "toomanyrequests: rate limit" >&2; exit 1;; esac\n')
    t = ia.ComposeTarget("web", "a" * 64, "nginx:1.27", "sha256:" + "1" * 64, "p", "web", "/opt/p", ("/opt/p/c.yml",), (), None)
    env = _env(tmp_path, bindir)
    _launch(ia.apply_script(t, old_image_id=t.image_id, rollback=ia.rollback_ref("web")), env)
    p = _poll(env)
    assert (p.state, p.rc, p.step) == ("done", ia.RC_PULL, "pull")
    assert "toomanyrequests" in p.output
    calls = record.read_text().splitlines()
    assert any(" pull web" in c for c in calls) and not any(" up " in c for c in calls)
    assert ia.describe_pull_failure(p.output).startswith("Abruflimit der Registry")


# --- Nachbesserungen: Sperre je Projekt auf dem Host, Sicherung erst nach dem Pull ---------------


def _launch_out(script: str, env: dict[str, str], *, run_id: str, lock: str | None) -> tuple[dt.Launch, str]:
    out = subprocess.run(["sh", "-c", dt.launch_command(run_id, script, lock)], capture_output=True, text=True, timeout=30, env=env, check=False)
    assert out.returncode == 0, out.stdout + out.stderr
    return dt.parse_launch(out.stdout), out.stdout


def _busy_probe(env: dict[str, str], project: str = "web") -> bool:
    out = subprocess.run(["sh", "-c", dt.busy_check_command(project)], capture_output=True, text=True, timeout=10, env=env, check=False)
    return "@@busy" in out.stdout.splitlines()


@posix
def test_a_second_update_of_the_same_project_is_refused_on_the_host_and_the_lock_goes_away(tmp_path):
    env = _env(tmp_path)
    marker = tmp_path / "zweiter-lauf"
    first, out = _launch_out("echo A; sleep 3", env, run_id="imgupd_aaaaaaaaaaaaaaaa", lock="web")
    assert first.started and not first.busy
    lock = _jobs(tmp_path) / "lock-web"
    assert lock.is_dir() and (lock / "pid").read_text().strip() == str(first.pid)
    assert _busy_probe(env) is True and _busy_probe(env, "anderes") is False

    second, out = _launch_out(f"touch {marker}", env, run_id="imgupd_bbbbbbbbbbbbbbbb", lock="web")
    assert second.busy and not second.started and "@@method" not in out
    time.sleep(0.5)
    assert not marker.exists() and not (_jobs(tmp_path) / "imgupd_bbbbbbbbbbbbbbbb.pid").exists()
    # Ein anderes Projekt ist nicht betroffen.
    other, _ = _launch_out("true", env, run_id="imgupd_cccccccccccccccc", lock="db")
    assert other.started and not other.busy

    done = _poll(env, run_id="imgupd_aaaaaaaaaaaaaaaa")
    assert (done.state, done.rc) == ("done", 0)
    deadline = time.monotonic() + 5
    while lock.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not lock.exists(), "die Sperre wird am Ende des Laufs entfernt"
    assert _busy_probe(env) is False
    again, _ = _launch_out(f"touch {marker}", env, run_id="imgupd_dddddddddddddddd", lock="web")
    assert again.started and _poll(env, run_id="imgupd_dddddddddddddddd").state == "done" and marker.exists()


@posix
def test_a_lock_of_a_dead_run_is_cleaned_up_and_never_blocks(tmp_path):
    env = _env(tmp_path)
    launch, _ = _launch_out("echo angefangen; sleep 30", env, run_id="imgupd_aaaaaaaaaaaaaaaa", lock="web")
    assert launch.started and _busy_probe(env) is True
    os.killpg(launch.pid, signal.SIGKILL)  # Absturz: die Sperre bleibt liegen
    deadline = time.monotonic() + 5
    while Path(f"/proc/{launch.pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert (_jobs(tmp_path) / "lock-web").is_dir()
    assert _busy_probe(env) is False, "ein toter Lauf haelt die Sperre nicht"
    fresh, _ = _launch_out("echo neu", env, run_id="imgupd_bbbbbbbbbbbbbbbb", lock="web")
    assert fresh.started and not fresh.busy
    assert _poll(env, run_id="imgupd_bbbbbbbbbbbbbbbb").rc == 0
    # Auch eine wiederverwendete Prozessnummer eines fremden Programms haelt sie nicht.
    (_jobs(tmp_path) / "lock-web").mkdir(exist_ok=True)
    (_jobs(tmp_path) / "lock-web" / "pid").write_text(str(os.getpid()))
    assert _busy_probe(env) is False


@posix
def test_the_relaunch_of_the_same_run_is_not_reported_as_busy(tmp_path):
    env = _env(tmp_path)
    first, _ = _launch_out("sleep 2", env, run_id=RID, lock="web")
    again, _ = _launch_out("sleep 2", env, run_id=RID, lock="web")
    assert again.exists and not again.busy and again.pid == first.pid


def test_lock_names_are_checked():
    for bad in ("x; rm -rf /", "Web", "a b", "web\n", "-p", "../x", ""):
        with pytest.raises(ValueError):
            dt.launch_command(RID, "true", bad)
        with pytest.raises(ValueError):
            dt.busy_check_command(bad)
    from nodvard_deck.core.deny_patterns import match_deny_patterns

    for cmd in (dt.launch_command(RID, "true", "web"), dt.busy_check_command("web")):
        assert match_deny_patterns(cmd) is None
    assert dt.parse_launch("@@launch\n@@busy\n").busy is True


def _docker_stub(bindir: Path, record: Path, *, inspect_id: str, pull_rc: int = 0) -> None:
    _stub(bindir, "docker", (
        f'if [ "$1 $2" = "image inspect" ]; then echo {inspect_id}; exit 0; fi\n'
        f'echo "$*" >>{record}\n'
        f'case "$*" in *" pull "*) exit {pull_rc};; esac\n'
    ))


def _run_script(tmp_path: Path, bindir: Path) -> dt.Poll:
    t = ia.ComposeTarget("web", "a" * 64, "nginx:1.27", "sha256:" + "1" * 64, "p", "web", "/opt/p", ("/opt/p/c.yml",), (), None)
    env = _env(tmp_path, bindir)
    _launch(ia.apply_script(t, old_image_id=t.image_id, rollback=ia.rollback_ref("web")), env)
    return _poll(env)


@posix
def test_the_rollback_is_tagged_after_the_pull_and_before_the_recreate(tmp_path):
    bindir, record = tmp_path / "bin", tmp_path / "calls"
    _docker_stub(bindir, record, inspect_id="sha256:" + "9" * 64)
    assert _run_script(tmp_path, bindir).rc == 0
    kinds = [next(w for w in ("pull", "tag", "up") if f" {w} " in f" {c} ") for c in record.read_text().splitlines()]
    assert kinds == ["pull", "tag", "up"]


@posix
def test_no_rollback_is_tagged_when_the_pull_brought_nothing_new_or_failed(tmp_path):
    bindir, record = tmp_path / "bin", tmp_path / "calls"
    _docker_stub(bindir, record, inspect_id="sha256:" + "1" * 64)  # dieselbe ID wie das laufende Image
    assert _run_script(tmp_path, bindir).rc == 0
    calls = record.read_text().splitlines()
    assert not any(c.startswith("tag ") for c in calls) and any(" up " in f" {c} " for c in calls)

    tmp2 = tmp_path / "zwei"
    tmp2.mkdir()
    bindir2, record2 = tmp2 / "bin", tmp2 / "calls"
    _docker_stub(bindir2, record2, inspect_id="sha256:" + "9" * 64, pull_rc=1)  # Pull scheitert
    poll = _run_script(tmp2, bindir2)
    assert poll.rc == ia.RC_PULL
    assert not any(c.startswith("tag ") or " up " in f" {c} " for c in record2.read_text().splitlines())
