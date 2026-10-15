"""Nodvard Shield: Lynis-Audit starten, abfragen und beenden über das Hilfsskript `shield-root` (ab Version 5).

Gleichlauf: Der bisherige Befehl (`detached.launch_command` mit `audit_job.lynis_command`, `audit_job.poll_command`,
`audit_job.stop_command`) und das Skript laufen gegen dieselben Attrappen in gleich vorbereiteten Ordnern. Ausgabe,
Rückgabecode, Protokoll und rc-Datei müssen gleich sein; nur die Prozessnummer darf sich unterscheiden."""

from __future__ import annotations

import fcntl
import os
import shutil
import signal
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from shield_helper_kit import (
    EXT_DIR,
    SCRIPT,
    SH,
    helper_copy,
    lynis_pid_files,
    pid_free,
    run_sh,
    side,
    wait_for,
)

RID = "audit_0123456789abcdef"

needs_sh = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None or shutil.which("setsid") is None
    or shutil.which("lynis") is not None,
    reason="braucht eine echte POSIX-Shell mit setsid und kein echtes Lynis",
)


@pytest.fixture(autouse=True)
def _ext_shield_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


def _mods():
    from nodvard_deck_ext_shield import antivirus, audit_job, detached

    return antivirus, audit_job, detached


def _lynis(base: Path, *, report: bool = True, rc: int = 0) -> str:
    """Attrappe für Lynis: nennt ihre Argumente, schreibt (mit `report`) einen Bericht mit Zeitstempel in der Zukunft
    (damit `-nt` sicher greift) und endet mit `rc`."""
    rep = base / "lynis-report.dat"
    write = (
        f"printf 'warning[]=SSH-7408|x|\\nsuggestion[]=BOOT-5122|y|\\nhardening_index=71\\nfinish=true\\nother=1\\n' > {rep}; "
        f"touch -d \"@$(( $(date +%s) + 5 ))\" {rep}; "
    ) if report else ""
    return f'echo "lynis $*"; {write}exit {rc}\n'


def _old_launch(base: Path, sdmark: Path) -> str:
    _, aj, dt = _mods()
    command = aj.lynis_command(RID, base_dir=str(base / "audit"), report=str(base / "lynis-report.dat"),
                               pid_files=lynis_pid_files(base))
    return dt.launch_command(RID, command, base_dir=str(base / "audit"), systemd_marker=str(sdmark), kind=aj.AUDITS)


def _run_both(tmp_path: Path, tools: dict[str, str] | None = None, *, systemd: str | None = None, prepare=None,
              lynis: dict[str, object] | None = None) -> list[dict[str, object]]:
    _, aj, _ = _mods()
    results = []
    for name in ("alt", "neu"):
        base = tmp_path / name
        scripts = {"lynis": _lynis(base, **(lynis or {}))} if lynis is not None or tools is None else {}
        _, sdmark, path = side(base, {**scripts, **(tools or {})}, systemd=systemd)
        audit = base / "audit"
        if prepare is not None:
            prepare(base)
        if name == "alt":
            launch = run_sh([SH, "-c", _old_launch(base, sdmark)], path)
        else:
            helper = helper_copy(base, path=path, sdmark=sdmark)
            launch = run_sh([SH, str(helper), "audit-start", RID], "/nicht-da")
        wait_for(audit / f"{RID}.rc")
        if name == "alt":
            poll = run_sh([SH, "-c", aj.poll_command(RID, base_dir=str(audit))], path)
        else:
            poll = run_sh([SH, str(helper), "audit-poll", RID], "/nicht-da")
        sd_log = base / "systemd-run.log"
        results.append({
            "launch": (launch[0], pid_free(launch[1])),
            "log": (audit / f"{RID}.log").read_text(),
            "rc": (audit / f"{RID}.rc").read_text(),
            "poll": poll,
            "files": sorted(p.name for p in audit.iterdir()),
            "systemd-run": sd_log.read_text() if sd_log.exists() else None,
        })
    return results


@needs_sh
def test_an_audit_behaves_like_the_old_command(tmp_path):
    old, new = _run_both(tmp_path, lynis={})
    assert new == old
    assert old["launch"] == (0, "@@launch\n@@method=setsid\n@@pid=N\n")
    log = old["log"]
    assert "lynis audit system --quick --quiet --no-colors\n@@lynis-rc=0\n@@report\n" in log
    assert log.endswith("warning[]=SSH-7408|x|\nsuggestion[]=BOOT-5122|y|\nhardening_index=71\nfinish=true\n")
    assert old["files"] == [f"{RID}.log", f"{RID}.pid", f"{RID}.rc", f"{RID}.start", "lynis.lock"]
    _, aj, _ = _mods()
    assert aj.parse_result(old["poll"][1].split("@@tail\n", 1)[1]).status == "ok"


@needs_sh
@pytest.mark.parametrize("systemd", ["ok", "fail"], ids=["systemd", "systemd-scheitert"])
def test_with_systemd_the_unit_has_the_same_name(tmp_path, systemd):
    old, new = _run_both(tmp_path, lynis={}, systemd=systemd)
    assert new == old
    assert old["systemd-run"] == f"--unit=nodvard-shield-audit-{RID} --collect --quiet --description=Nodvard Shield Audit {RID}\n"


@needs_sh
@pytest.mark.parametrize(("lynis", "marker"), [
    ({"report": False}, "@@lynis-rc=0\n@@stale\n"),
    ({"rc": 137, "report": False}, "@@killed\n"),
    ({"rc": 3}, "@@lynis-rc=3\n@@report\n"),
], ids=["kein-neuer-bericht", "beendet", "fehlercode"])
def test_the_outcomes_of_lynis_are_reported_like_before(tmp_path, lynis, marker):
    old, new = _run_both(tmp_path, lynis=lynis)
    assert new == old and marker in old["log"]


@needs_sh
def test_without_lynis_nothing_runs(tmp_path):
    old, new = _run_both(tmp_path, {"date": "exit 1\n"})
    assert new == old and old["log"] == "@@nolynis\n" and old["rc"] == "0\n"


def _hold_lock(base: Path):
    (base / "audit").mkdir(mode=0o700)
    fh = open(base / "audit" / "lynis.lock", "a")  # noqa: SIM115 - bleibt bis zum Ende des Tests offen
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _held.append(fh)


_held: list = []


@needs_sh
def test_a_running_audit_blocks_a_second_one(tmp_path):
    try:
        old, new = _run_both(tmp_path, lynis={}, prepare=_hold_lock)
    finally:
        while _held:
            _held.pop().close()
    assert new == old and old["log"].endswith("@@busy\n")


@needs_sh
def test_a_lynis_started_by_hand_blocks_too(tmp_path):
    proc = subprocess.Popen([SH, "-c", "sleep 30; : lynis audit system"], start_new_session=True)
    try:
        def prepare(base: Path) -> None:
            Path(lynis_pid_files(base)[1]).write_text(f"{proc.pid}\n")

        old, new = _run_both(tmp_path, lynis={}, prepare=prepare)
    finally:
        proc.kill()
        proc.wait()
    assert new == old and old["log"].endswith("@@busy\n")


@needs_sh
@pytest.mark.parametrize("state", ["running", "done", "lost", "unknown"])
def test_the_poll_behaves_like_the_old_command(tmp_path, state):
    _, aj, _ = _mods()
    proc = subprocess.Popen([SH, "-c", f"sleep 30; : {RID}"], start_new_session=True) if state == "running" else None
    try:
        outs = []
        for name in ("alt", "neu"):
            base = tmp_path / name
            _, sdmark, path = side(base, {}, systemd=None)
            audit = base / "audit"
            audit.mkdir(mode=0o700)
            (audit / f"{RID}.log").write_text("".join(f"Zeile {i} {'x' * 70}\n" for i in range(4000)) + "@@report\nfinish=true\n")
            if state == "running":
                (audit / f"{RID}.pid").write_text(f"{proc.pid}\n")
            elif state in ("done", "lost"):
                (audit / f"{RID}.pid").write_text("999999\n")
                if state == "done":
                    (audit / f"{RID}.rc").write_text("0\n")
            if name == "alt":
                outs.append(run_sh([SH, "-c", aj.poll_command(RID, base_dir=str(audit))], path))
            else:
                outs.append(run_sh([SH, str(helper_copy(base, path=path, sdmark=sdmark)), "audit-poll", RID], "/nicht-da"))
    finally:
        if proc is not None:
            proc.kill()
            proc.wait()
    assert outs[1] == outs[0]
    assert outs[0][1].splitlines()[0] == {"running": "@@running", "done": "@@rc=0", "lost": "@@lost", "unknown": "@@unknown"}[state]
    if state == "done":
        assert len(outs[0][1]) > aj.TAIL_BYTES and outs[0][1].endswith("finish=true\n")


# Startet den Lauf (eigene Session, mit Unterprozessen) unter einem Prozess, der alle Waisen sofort abholt. Ohne ihn
# blieben beendete Unterprozesse im Test-Container als Zombies in der Session stehen (`pgrep -s` fände sie), weil dort
# kein init sie abholt.
REAPER = """
import ctypes, os, subprocess, sys
ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
p = subprocess.Popen([sys.argv[1], "-c", sys.argv[2]], start_new_session=True)
print(p.pid, flush=True)
while True:
    try:
        os.wait()
    except ChildProcessError:
        break
"""


@needs_sh
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="braucht prctl")
@pytest.mark.parametrize("running", [True, False], ids=["laeuft", "laeuft-nicht"])
def test_stop_behaves_like_the_old_command(tmp_path, running):
    """Ein Lauf in eigener Session (wie nach dem Start): Beide Wege beenden ihn samt Unterprozessen."""
    _, aj, _ = _mods()
    outs = []
    for name in ("alt", "neu"):
        base = tmp_path / name
        _, sdmark, path = side(base, {"systemctl": f'echo "systemctl $*" >> {base / "systemctl.log"}\n'}, systemd=None)
        audit = base / "audit"
        audit.mkdir(mode=0o700)
        reaper = pid = None
        if running:
            reaper = subprocess.Popen([sys.executable, "-c", REAPER, SH, f"sleep 60 & sleep 60; : {RID}"],
                                      stdout=subprocess.PIPE, text=True)
            pid = int(reaper.stdout.readline())
            (audit / f"{RID}.pid").write_text(f"{pid}\n")
        try:
            if name == "alt":
                outs.append(run_sh([SH, "-c", aj.stop_command(RID, base_dir=str(audit))], path))
            else:
                outs.append(run_sh([SH, str(helper_copy(base, path=path, sdmark=sdmark)), "audit-stop", RID], "/nicht-da"))
        finally:
            if reaper is not None:
                left = subprocess.run(["pgrep", "-s", str(pid)], capture_output=True, text=True, check=False).stdout.split()
                for other in left:
                    os.kill(int(other), signal.SIGKILL)
                reaper.wait(timeout=10)
                reaper.stdout.close()
        log = base / "systemctl.log"
        outs.append(log.read_text() if log.exists() else "")
        if running:
            assert left == [], "nichts aus der Session des Laufs lebt noch"
    assert outs[2:] == outs[:2]
    if running:
        assert outs[0] == (0, "@@stopped\n") and outs[1] == f"systemctl stop --no-block nodvard-shield-audit-{RID}.service\n"
    else:
        assert outs[0] == (0, "@@notrunning\n") and outs[1] == ""


@pytest.mark.parametrize(
    "args",
    [
        ["audit-start"], ["audit-start", RID, "x"], ["audit-start", "upd_0123456789abcdef"], ["audit-start", "audit_0123"],
        ["audit-start", "audit_0123456789ABCDEF"], ["audit-start", "audit_0123456789abcdef0"], ["audit-start", "../x"],
        ["audit-run"], ["audit-run", "audit_x"], ["audit-poll"], ["audit-poll", "upd_0123456789abcdef"],
        ["audit-poll", RID, RID], ["audit-stop"], ["audit-stop", "audit_;id"], ["audit-stop", RID, "--force"],
        ["upgrade-poll", RID], ["upgrade-start", RID, "apt", "all", "plain"],
    ],
)
def test_strange_arguments_are_refused(tmp_path, args):
    helper = helper_copy(tmp_path, path="/nicht-da", sdmark=tmp_path / "sd")
    done = subprocess.run([SH, str(helper), *args], capture_output=True, text=True, timeout=30, check=False)
    assert (done.returncode, done.stdout) == (64, ""), (args, done.stderr)
    assert not (tmp_path / "audit").exists() and not (tmp_path / "jobs").exists()


def test_the_dashboard_asks_the_helper_only_for_the_fixed_limit():
    av, aj, dt = _mods()
    launch = dt.launch_command(RID, aj.lynis_command(RID), kind=aj.AUDITS)
    assert aj.launch_as_root(RID) == av.via_helper(["audit-start", RID], launch, version=av.HELPER_AUDIT_VERSION)
    short = dt.launch_command(RID, aj.lynis_command(RID, limit_s=60), kind=aj.AUDITS)
    assert aj.launch_as_root(RID, limit_s=60) == av.as_root(short), "das Skript kennt nur die feste Obergrenze"
    assert aj.poll_as_root(RID) == av.via_helper(["audit-poll", RID], aj.poll_command(RID), version=av.HELPER_AUDIT_VERSION)
    assert aj.stop_as_root(RID) == av.via_helper(["audit-stop", RID], aj.stop_command(RID), version=av.HELPER_AUDIT_VERSION)
    for bad in ("upd_0123456789abcdef", "audit_x; id"):
        for build in (aj.launch_as_root, aj.poll_as_root, aj.stop_as_root):
            with pytest.raises(ValueError):
                build(bad)


def test_the_script_uses_the_places_and_limits_of_the_module():
    av, aj, _ = _mods()
    manifest = tomllib.loads((EXT_DIR / "extension.toml").read_text(encoding="utf-8"))["extension"]
    assert av.HELPER_AUDIT_VERSION <= manifest["root_helper"]["version"]
    text = SCRIPT.read_text(encoding="ascii")
    assert f"AUDITDIR={aj.AUDIT_DIR}\n" in text and f"LYNIS_REPORT={aj.REPORT_FILE}\n" in text
    assert f"LYNIS_PIDS='{' '.join(aj.LYNIS_PID_FILES)}'\n" in text and f"LYNIS_LIMIT={aj.LIMIT_S}\n" in text
    assert f"JU={aj.AUDITS.unit_prefix}\n" in text and f"JL='{aj.AUDITS.label}'\n" in text and f"JP={aj.AUDITS.prefix}\n" in text
    assert f'"nice -n {aj.NICE}"' in text and f'"timeout -k {aj.KILL_AFTER_S} $LYNIS_LIMIT"' in text
    assert f"tail -c {aj.TAIL_BYTES} " in text
