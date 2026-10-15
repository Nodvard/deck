"""Nodvard Shield: Updates einspielen, Neustart und die Abfrage entkoppelter Läufe über das Hilfsskript `shield-root`
(ab Version 4).

Gleichlauf: Der bisherige Befehl (`detached.launch_command` mit `updates.upgrade_command`, `detached.poll_command`,
`updates.REBOOT_COMMAND`) und das Skript laufen gegen dieselben Attrappen in gleich vorbereiteten Ordnern. Ausgabe,
Rückgabecode, Protokoll und rc-Datei des Laufs müssen gleich sein; nur die Prozessnummer darf sich unterscheiden."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from shield_helper_kit import EXT_DIR, SCRIPT, SH
from shield_helper_kit import helper_copy as _helper
from shield_helper_kit import pid_free as _pid_free
from shield_helper_kit import run_sh as _sh
from shield_helper_kit import side as _side
from shield_helper_kit import stubs as _stubs
from shield_helper_kit import wait_for as _wait_for

RID = "upd_0123456789abcdef"

needs_sh = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None or shutil.which("setsid") is None,
    reason="braucht eine echte POSIX-Shell mit setsid",
)


@pytest.fixture(autouse=True)
def _ext_shield_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


def _mods():
    from nodvard_deck_ext_shield import antivirus, detached, patching, updates

    return antivirus, detached, patching, updates


APT = 'echo "apt-get $* FE=${DEBIAN_FRONTEND:-}"; [ "$*" = "update -q" ] && exit "${APT_UPDATE_RC:-0}"; exit 0\n'
PACKAGE_TOOLS = {
    "apt-get": APT,
    "dnf": 'echo "dnf $*"; exit 3\n',
    "apk": 'echo "apk $*"\n',
}

PLANS = [
    ("apt", "all", [], False, {}),
    ("apt", "all", [], True, {}),
    ("apt", "all", [], True, {"pveversion": "exit 0\n"}),
    ("apt", "security", ["libssl3", "openssl", "a+b.c:d~e-f"], False, {}),
    ("apt", "cleanup", [], False, {}),
    ("dnf", "all", [], False, {}),
    ("dnf", "security", [], False, {}),
    ("dnf", "cleanup", [], False, {}),
    ("apk", "all", [], False, {}),
    ("apk", "security", [], False, {}),
    ("apk", "cleanup", [], False, {}),
]
PLAN_IDS = [
    "apt-all", "apt-dist-ohne-proxmox", "apt-dist-proxmox", "apt-security", "apt-cleanup", "dnf-all", "dnf-security",
    "dnf-cleanup", "apk-all", "apk-security", "apk-cleanup",
]


def _run_both(tmp_path: Path, manager: str, mode: str, packages: list[str], dist: bool, tools: dict[str, str], *,
              systemd: str | None = None) -> list[dict[str, object]]:
    """Startet denselben Lauf auf dem alten Weg und über das Skript, wartet auf das Ende und sammelt das Ergebnis."""
    _, dt, _, up = _mods()
    command = up.upgrade_command(manager, mode, packages, dist_upgrade=dist)
    plan = up.upgrade_args(manager, mode, packages, dist_upgrade=dist)
    assert plan is not None
    results = []
    for side in ("alt", "neu"):
        base = tmp_path / side
        _, sdmark, path = _side(base, {**PACKAGE_TOOLS, **tools}, systemd=systemd)
        jobs = base / "jobs"
        if side == "alt":
            launch = _sh([SH, "-c", dt.launch_command(RID, command, base_dir=str(jobs), systemd_marker=str(sdmark))], path)
        else:
            helper = _helper(base, path=path, sdmark=sdmark)
            launch = _sh([SH, str(helper), "upgrade-start", RID, *plan], "/nicht-da")
        _wait_for(jobs / f"{RID}.rc")
        if side == "alt":
            poll = _sh([SH, "-c", dt.poll_command(RID, base_dir=str(jobs))], path)
        else:
            poll = _sh([SH, str(helper), "upgrade-poll", RID], "/nicht-da")
        sd_log = base / "systemd-run.log"
        results.append({
            "launch": (launch[0], _pid_free(launch[1])),
            "log": (jobs / f"{RID}.log").read_text(),
            "rc": (jobs / f"{RID}.rc").read_text(),
            "poll": poll,
            "files": sorted(p.name for p in jobs.iterdir()),
            "mode": oct((jobs.stat().st_mode) & 0o777),
            "systemd-run": sd_log.read_text() if sd_log.exists() else None,
        })
    return results


@needs_sh
@pytest.mark.parametrize(("manager", "mode", "packages", "dist", "tools"), PLANS, ids=PLAN_IDS)
def test_an_update_run_behaves_like_the_old_command(tmp_path, manager, mode, packages, dist, tools):
    old, new = _run_both(tmp_path, manager, mode, packages, dist, tools)
    assert new == old
    assert old["launch"] == (0, "@@launch\n@@method=setsid\n@@pid=N\n")
    assert "@@upgrade-done" in old["log"] and old["poll"][1].startswith(f"@@rc={old['rc'].strip()}\n@@summary\n")


@needs_sh
@pytest.mark.parametrize("systemd", ["ok", "fail"], ids=["systemd", "systemd-scheitert"])
def test_with_systemd_the_unit_has_the_same_name(tmp_path, systemd):
    """Gleiche Unit (`lattice-upgrade-<lauf-id>`, alte Läufe bleiben auffindbar), gleiche Beschreibung, gleicher Rückweg."""
    old, new = _run_both(tmp_path, "apt", "all", [], False, {}, systemd=systemd)
    assert new == old
    assert old["systemd-run"] == f"--unit=lattice-upgrade-{RID} --collect --quiet --description=Nodvard Deck Update {RID}\n"
    method = "systemd" if systemd == "ok" else "setsid-fallback"
    assert old["launch"] == (0, f"@@launch\n@@method={method}\n@@pid=N\n")


@needs_sh
def test_a_failed_package_list_update_is_reported_like_before(tmp_path):
    tools = {"apt-get": APT.replace("${APT_UPDATE_RC:-0}", "100")}
    old, new = _run_both(tmp_path, "apt", "all", [], False, tools)
    assert new == old and old["rc"] == "100\n"


@needs_sh
def test_an_already_started_run_is_not_started_twice(tmp_path):
    _, dt, _, up = _mods()
    command = up.upgrade_command("apt", "all")
    outs = []
    for side in ("alt", "neu"):
        base = tmp_path / side
        _, sdmark, path = _side(base, PACKAGE_TOOLS, systemd=None)
        (base / "jobs").mkdir(mode=0o700)
        (base / "jobs" / f"{RID}.pid").write_text("4242\n")
        if side == "alt":
            outs.append(_sh([SH, "-c", dt.launch_command(RID, command, base_dir=str(base / "jobs"),
                                                         systemd_marker=str(sdmark))], path))
        else:
            outs.append(_sh([SH, str(_helper(base, path=path, sdmark=sdmark)), "upgrade-start", RID, "apt", "all", "plain"],
                            "/nicht-da"))
        assert sorted(p.name for p in (base / "jobs").iterdir()) == [f"{RID}.pid"]
    assert outs[1] == outs[0] == (0, "@@launch\n@@exists\n@@pid=4242\n")


@needs_sh
def test_without_the_folder_nothing_starts(tmp_path):
    _, dt, _, up = _mods()
    command = up.upgrade_command("apt", "all")
    outs = []
    for side in ("alt", "neu"):
        base = tmp_path / side
        _, sdmark, path = _side(base, PACKAGE_TOOLS, systemd=None)
        (base / "jobs").write_text("eine Datei, kein Ordner")
        if side == "alt":
            outs.append(_sh([SH, "-c", dt.launch_command(RID, command, base_dir=str(base / "jobs"),
                                                         systemd_marker=str(sdmark))], path))
        else:
            outs.append(_sh([SH, str(_helper(base, path=path, sdmark=sdmark)), "upgrade-start", RID, "apt", "all", "plain"],
                            "/nicht-da"))
    assert outs[1] == outs[0] == (1, "@@launch\n@@nopid\n")


LONG_LOG = (
    "Reading package lists...\nThe following packages have been kept back:\n  linux-image-amd64 pve-kernel\n"
    "  firmware-misc\nThe following packages will be upgraded:\n  openssl\n"
    "2 upgraded, 1 newly installed, 0 to remove and 3 not upgraded.\nE: kaputt\n"
    + "".join(f"Zeile {i} {'x' * 60}\n" for i in range(600))
    + "@@upgrade-done\n"
)


@needs_sh
@pytest.mark.parametrize("state", ["running", "done", "lost", "unknown", "empty_pid"])
def test_the_poll_behaves_like_the_old_command(tmp_path, state):
    _, dt, _, _ = _mods()
    # Ein Prozess, dessen Befehlszeile die Lauf-ID trägt, wie der entkoppelte Lauf.
    proc = subprocess.Popen([SH, "-c", f"sleep 30; : {RID}"], start_new_session=True) if state == "running" else None
    try:
        outs = []
        for side in ("alt", "neu"):
            base = tmp_path / side
            _, sdmark, path = _side(base, {"dpkg": 'echo "dpkg $*"; echo "Paket halb konfiguriert"\n'}, systemd=None)
            jobs = base / "jobs"
            jobs.mkdir(mode=0o700)
            (jobs / f"{RID}.log").write_text(LONG_LOG)
            if state == "running":
                (jobs / f"{RID}.pid").write_text(f"{proc.pid}\n")
            elif state == "done":
                (jobs / f"{RID}.pid").write_text("999999\n")
                (jobs / f"{RID}.rc").write_text("0\n")
            elif state == "lost":
                (jobs / f"{RID}.pid").write_text("999999\n")
            elif state == "empty_pid":
                (jobs / f"{RID}.pid").write_text("")
            if side == "alt":
                outs.append(_sh([SH, "-c", dt.poll_command(RID, base_dir=str(jobs))], path))
            else:
                outs.append(_sh([SH, str(_helper(base, path=path, sdmark=sdmark)), "upgrade-poll", RID], "/nicht-da"))
    finally:
        if proc is not None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    assert outs[1] == outs[0]
    first = outs[0][1].splitlines()[0]
    assert first == {"running": "@@running", "done": "@@rc=0", "lost": "@@lost", "unknown": "@@unknown",
                     "empty_pid": "@@unknown"}[state]
    if state == "done":
        assert "@@kept-back: linux-image-amd64 pve-kernel firmware-misc" in outs[0][1]


@needs_sh
@pytest.mark.parametrize("tools", [
    {"shutdown": 'echo "shutdown $*"\n'},
    {"shutdown": 'echo "nicht erlaubt" >&2; exit 1\n'},
    {"reboot": "exit 0\n"},
], ids=["shutdown", "shutdown-scheitert", "ohne-shutdown"])
def test_reboot_behaves_like_the_old_command(tmp_path, tools):
    _, _, _, up = _mods()
    bin_dir = _stubs(tmp_path, {"reboot": "exit 0\n", **tools})
    old = _sh([SH, "-c", up.REBOOT_COMMAND], str(bin_dir))
    new = _sh([SH, str(_helper(tmp_path, path=str(bin_dir), sdmark=tmp_path / "sd")), "reboot"], "/nicht-da")
    assert new == old
    if "shutdown" in tools and "exit 1" not in tools["shutdown"]:
        assert old == (0, "shutdown -r +1 Neustart durch Nodvard Deck nach Updates\n")


@pytest.mark.parametrize(
    "args",
    [
        ["upgrade-start"], ["upgrade-start", RID], ["upgrade-start", RID, "apt", "all"],
        ["upgrade-start", "upd_0123", "apt", "all", "plain"], ["upgrade-start", "upd_0123456789ABCDEF", "apt", "all", "plain"],
        ["upgrade-start", "aud_0123456789abcdef", "apt", "all", "plain"], ["upgrade-start", "../../x", "apt", "all", "plain"],
        ["upgrade-start", RID, "yum", "all", "plain"], ["upgrade-start", RID, "apt", "reboot", "plain"],
        ["upgrade-start", RID, "apt", "all", "yes"], ["upgrade-start", RID, "apt", "security", "dist", "openssl"],
        ["upgrade-start", RID, "dnf", "all", "dist"], ["upgrade-start", RID, "apt", "all", "plain", "openssl"],
        ["upgrade-start", RID, "apt", "security", "plain"], ["upgrade-start", RID, "apt", "security", "plain", "-o"],
        ["upgrade-start", RID, "apt", "security", "plain", "a;id"], ["upgrade-start", RID, "apt", "security", "plain", "a b"],
        ["upgrade-start", RID, "apt", "security", "plain", "a$(id)"], ["upgrade-start", RID, "apt", "security", "plain", ""],
        ["upgrade-start", RID, "apt", "security", "plain", "ok", "a\nb"],
        ["upgrade-run", RID, "apt", "all"], ["upgrade-run", "x", "apt", "all", "plain"],
        ["upgrade-poll"], ["upgrade-poll", "upd_x"], ["upgrade-poll", RID, RID], ["reboot", "now"],
    ],
)
def test_strange_arguments_are_refused(tmp_path, args):
    helper = _helper(tmp_path, path="/nicht-da", sdmark=tmp_path / "sd")
    done = subprocess.run([SH, str(helper), *args], capture_output=True, text=True, timeout=30, check=False)
    assert (done.returncode, done.stdout) == (64, ""), (args, done.stderr)
    assert not (tmp_path / "jobs").exists()


def test_upgrade_args_describe_the_same_run_as_the_command():
    _, _, pa, up = _mods()
    for manager, mode, packages, dist, _ in PLANS:
        plan = up.upgrade_args(manager, mode, packages, dist_upgrade=dist)
        assert pa._plan_matches(plan, up.upgrade_command(manager, mode, packages, dist_upgrade=dist)), plan
    assert up.upgrade_args("apt", "security", ["openssl", "-x", "a b"]) == ["apt", "security", "plain", "openssl"]
    assert up.upgrade_args("dnf", "security", ["openssl"]) == ["dnf", "security", "plain"]
    assert up.upgrade_args("dnf", "all", dist_upgrade=True) == ["dnf", "all", "plain"]
    assert up.upgrade_args("apt", "security", ["openssl\n"]) is None, "das Skript lehnte den Namen ab: alter Weg"
    for bad in (("apt", "security", []), ("yum", "all", None), ("apt", "reboot", None)):
        with pytest.raises(ValueError):
            up.upgrade_args(*bad)


def test_the_helper_only_gets_a_plan_that_matches_the_approved_command(monkeypatch):
    av, dt, pa, up = _mods()
    command = up.upgrade_command("apt", "security", ["openssl"])
    launch = dt.launch_command(RID, command)
    plan = ["apt", "security", "plain", "openssl"]
    assert pa.launch_as_root(RID, command, plan) == av.via_helper(
        ["upgrade-start", RID, *plan], launch, version=av.HELPER_UPDATE_VERSION,
    )
    for other in (None, ["apt", "security", "plain", "libssl3"], ["apt", "all", "plain"], ["apt", "security", "x", "openssl"],
                  ["apt"], ["yum", "all", "plain"]):
        assert pa.launch_as_root(RID, command, other) == av.as_root(launch), other
    assert pa.poll_as_root(RID) == av.via_helper(["upgrade-poll", RID], dt.poll_command(RID), version=av.HELPER_UPDATE_VERSION)
    assert pa.reboot_as_root(up.REBOOT_COMMAND) == av.via_helper(["reboot"], up.REBOOT_COMMAND, version=av.HELPER_UPDATE_VERSION)
    assert pa.reboot_as_root("reboot") == av.as_root("reboot")
    with pytest.raises(ValueError):
        pa.poll_as_root("upd_x; id")


def test_the_needed_version_is_one_the_module_brings():
    av, _, _, _ = _mods()
    manifest = tomllib.loads((EXT_DIR / "extension.toml").read_text(encoding="utf-8"))["extension"]
    assert av.HELPER_UPDATE_VERSION <= manifest["root_helper"]["version"]
    text = SCRIPT.read_text(encoding="ascii")
    _, dt, _, up = _mods()
    assert f"JOBDIR={dt.JOB_DIR}\n" in text and f"SDMARK={dt.SYSTEMD_MARKER}\n" in text
    assert f"SELF={av.HELPER}\n" in text and f"JU={dt.UNIT_PREFIX}\n" in text and f"JL='{dt.UPDATES.label}'\n" in text
    assert f"JP={dt.UPDATES.prefix}\n" in text and f"-mtime +{dt.KEEP_DAYS}" in text
    assert up.UPGRADE_DONE in text and up.REBOOT_MARK in text
