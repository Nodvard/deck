"""Update-Zentrale: Updates, die apt wegen Phasing noch zurückhält, an echten Aufnahmen aus Ubuntu und Debian."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

EXT_SRC = Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "shield_apt"


@pytest.fixture(autouse=True)
def _ext_shield_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_SRC))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


def _updates():
    from nodvard_deck_ext_shield import updates

    return updates


def _parse(recording: str):
    return _updates().parse_status((FIXTURES / f"{recording}.txt").read_text(encoding="utf-8"))


PHASED = ["demo-foreign", "demo-phased", "demo-phased-newdep", "demo-phased-two"]


@pytest.mark.parametrize("recording", ["ubuntu-22.04", "ubuntu-24.04", "ubuntu-26.04"])
def test_ubuntu_counts_the_updates_held_back_by_phasing(recording):
    st = _parse(recording)
    assert len(st.packages) == 9
    assert sorted(p.name for p in st.packages if p.phased) == PHASED
    d = st.as_dict()
    assert (d["count"], d["phased_count"], d["phasing"]) == (9, 4, "checked")
    assert {p["name"]: p["phased"] for p in d["packages"]}["demo-phased"] is True


def test_only_what_phasing_holds_back_counts():
    phased = {p.name: p.phased for p in _parse("ubuntu-24.04").packages}
    assert phased["demo-phased-full"] is False  # an alle verteilt
    assert phased["demo-keptback"] is False  # neue Abhängigkeit, „Alle einspielen“ zieht sie mit
    assert phased["demo-held"] is False  # gesperrt, käme auch ohne Phasing nicht
    assert phased["demo-phased-newdep"] is True
    assert phased["demo-foreign"] is True  # i386, apt meldet "demo-foreign:i386"


@pytest.mark.parametrize("recording", ["debian-12", "debian-13"])
def test_debian_without_phasing_shows_none(recording):
    st = _parse(recording)
    assert len(st.packages) == 9
    assert (st.phased_count, st.phasing) == (0, "checked")


def test_phased_updates_that_apt_installs_anyway_do_not_count():
    st = _parse("ubuntu-24.04-immer-alle")
    assert len(st.packages) == 9
    assert st.phased_count == 0


def test_broken_dependencies_make_the_phasing_check_unavailable_not_zero():
    st = _parse("ubuntu-24.04-kaputt")
    assert len(st.packages) == 9
    assert not any(p.phased for p in st.packages)
    d = st.as_dict()
    assert (d["phasing"], d["phased_count"]) == ("unavailable", None)


def test_a_status_without_the_phasing_section_is_unavailable():
    text = (FIXTURES / "ubuntu-24.04.txt").read_text(encoding="utf-8")
    without = re.sub(r"@@phasing\n.*?(?=@@security)", "", text, flags=re.DOTALL)
    st = _updates().parse_status(without)
    assert len(st.packages) == 9
    assert (st.phasing, st.phased_count) == ("unavailable", None)


def test_without_apt_there_is_no_phasing():
    st = _updates().parse_status("@@pm\ndnf\n@@list\nkernel-core.x86_64   6.8.9-300.fc40   updates\n@@phasing\n@@end\n")
    assert (st.phasing, st.phased_count) == ("none", 0)


def test_only_plain_spaces_make_an_exit_code_line():
    lines = ["rc inst 100", "rc all 100", "rc\xa0inst\xa00", "rc\tall\t0"]
    assert _updates()._trial_runs(lines) is None


def _apt_stub(tmp_path: Path, plain_rc: int, all_rc: int) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "apt-get"
    stub.write_text(
        "#!/bin/sh\n"
        'case "$*" in *Always-Include-Phased-Updates*)\n'
        f'  echo "Inst demo-normal [1.0] (1.1)"; echo "Inst demo-phased [1.0] (1.1)"; exit {all_rc};;\n'
        "esac\n"
        f'echo "Inst demo-normal [1.0] (1.1)"; exit {plain_rc}\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bin_dir


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize(("plain_rc", "all_rc", "expected"), [
    (0, 0, {"demo-phased"}),
    (100, 0, None),
    (0, 100, None),
])
def test_a_failed_trial_run_leaves_the_result_unknown_in_a_real_shell(tmp_path, plain_rc, all_rc, expected):
    bin_dir = _apt_stub(tmp_path, plain_rc, all_rc)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    out = subprocess.run(["/bin/sh", "-c", _updates().PHASING_COMMAND], capture_output=True, text=True, timeout=30,
                         check=True, env=env).stdout
    lines = [line.strip() for line in out.splitlines() if line.strip() and line.strip() != "@@phasing"]
    runs = _updates()._trial_runs(lines)
    assert (None if runs is None else runs["all"] - runs["inst"]) == expected


@pytest.mark.parametrize(("recording", "installable"), [
    ("ubuntu-22.04", 3), ("ubuntu-24.04", 3), ("ubuntu-26.04", 3), ("ubuntu-24.04-immer-alle", 7), ("debian-12", 7),
    ("debian-13", 7),
])
def test_kept_back_and_held_updates_are_their_own_state(recording, installable):
    st = _parse(recording)
    by_name = {p.name: p for p in st.packages}
    transition, held = by_name["demo-transition"], by_name["demo-held"]
    assert (transition.kept_back, transition.held, transition.phased) == (True, False, False)
    assert (held.held, held.kept_back, held.phased) == (True, False, False)
    assert by_name["demo-keptback"].installable  # neue Abhängigkeit: „Alle einspielen“ zieht sie mit
    d = st.as_dict()
    assert (d["kept_back_count"], d["held_count"], d["installable_count"]) == (1, 1, installable)
    assert {p["name"]: p["kept_back"] for p in d["packages"]}["demo-transition"] is True


def test_without_trial_runs_held_stays_known_and_kept_back_unknown():
    d = _parse("ubuntu-24.04-kaputt").as_dict()
    assert (d["held_count"], d["kept_back_count"], d["installable_count"]) == (1, None, 8)
    assert not any(p["kept_back"] for p in d["packages"])


def test_security_runs_leave_out_phased_held_and_kept_back_packages():
    packages = [
        {"name": "openssl", "security": True},
        {"name": "libcamera-ipa", "security": True, "kept_back": True},
        {"name": "openssh-server", "security": True, "held": True},
        {"name": "libc6", "security": True, "phased": True},
        {"name": "base-files", "security": False},
    ]
    assert _updates().security_names(packages) == ["openssl"]


def test_what_a_run_leaves_out_is_named_by_reason():
    up = _updates()
    st = up.parse_status((FIXTURES / "ubuntu-24.04.txt").read_text(encoding="utf-8")).as_dict()
    left = up.not_installable(st, "all")
    assert left == {"phased": st["phased_count"], "kept_back": 1, "held": 1}
    assert up.runnable(st, "all") + sum(left.values()) == st["count"]
    assert up.not_installable(st, "all", full_upgrade=True)["kept_back"] == 0
    assert up.reasons_text(left) == f"{st['phased_count']} durch Phasing verzögert, 1 zurückgehalten, 1 gesperrt"
    assert up.reasons_text({"phased": 0, "kept_back": 0, "held": 0}) == ""
    packages = [{"name": "a", "security": True, "phased": True}, {"name": "b", "security": True, "held": True},
                {"name": "c", "security": True}, {"name": "d", "phased": True}]
    sec = {"installable_count": 1, "packages": packages}
    assert up.not_installable(sec, "security") == {"phased": 1, "kept_back": 0, "held": 1}
    assert up.held_back_message(sec, "security") == (
        "Die offenen Sicherheitsupdates lassen sich hier gerade nicht einspielen (1 durch Phasing verzögert, 1 gesperrt). "
        "Was zu tun ist, steht beim Server in der Update-Zentrale.")
    assert up.not_installable({"count": 3, "phased_count": 1}, "all") == {"phased": 1, "kept_back": 0, "held": 0}  # 0.12.0
    assert up.not_installable({"count": 3, "security_count": 1}, "security") == {"phased": 0, "kept_back": 0, "held": 0}
    unknown = {**sec, "phasing": "unavailable", "security_count": 3,
               "packages": [{"name": "a", "security": True}, {"name": "b", "security": True, "held": True}, {"name": "c", "security": True}]}
    assert up.runnable(unknown, "security") == 0
    assert up.not_installable(unknown, "security") == {"phased": 0, "kept_back": 0, "held": 1, "unknown": 2}
    assert up.reasons_text(up.not_installable(unknown, "security")) == "1 gesperrt, 2 nicht prüfbar"


@pytest.mark.parametrize("name", sorted(p.stem for p in FIXTURES.glob("*.txt")))
def test_every_package_has_at_most_one_reason(name):
    up = _updates()
    st = up.parse_status((FIXTURES / f"{name}.txt").read_text(encoding="utf-8")).as_dict()
    for p in st["packages"]:
        assert sum(bool(p[flag]) for flag in ("phased", "kept_back", "held")) <= 1, p
    for mode in ("all", "security"):
        total = st["count"] if mode == "all" else st["security_count"]
        assert up.runnable(st, mode) + sum(up.not_installable(st, mode).values()) == total


def test_runnable_counts_what_a_run_would_install():
    up = _updates()
    st = up.parse_status((FIXTURES / "debian-12.txt").read_text(encoding="utf-8")).as_dict()
    assert up.runnable(st, "all") == 7
    assert up.runnable(st, "all", full_upgrade=True) == 8  # dist-upgrade nimmt das zurückgehaltene mit, nie das gesperrte
    assert up.runnable(st, "security") == 0
    older = {"count": 3, "security_count": 1}  # Stand einer älteren Version, ohne Einteilung
    assert (up.runnable(older, "all"), up.runnable(older, "security")) == (3, 1)


@pytest.mark.parametrize(("settings", "status", "expected"), [
    ({"proxmox_dist_upgrade": True}, {"manager": "apt", "pve": True}, True),
    ({"proxmox_dist_upgrade": True}, {"manager": "apt", "pve": False, "kernel": "6.8.12-4-pve"}, False),  # etwa Backup Server
    ({"proxmox_dist_upgrade": True}, {"manager": "apt", "kernel": "6.8.12-4-pve"}, False),  # älterer Stand
    ({}, {"manager": "apt", "pve": True}, False),
    ({"proxmox_dist_upgrade": True}, None, False),
])
def test_full_upgrade_is_only_expected_on_proxmox_with_the_setting(settings, status, expected):
    assert _updates().full_upgrade_here(settings, status) is expected


def test_proxmox_is_recognized_by_pveversion_like_in_the_upgrade_command():
    up = _updates()
    cmd = up.status_command(refresh=False)
    assert "echo @@pve; if command -v pveversion" in cmd
    assert "command -v pveversion" in up.upgrade_command("apt", "all", dist_upgrade=True)
    base = (FIXTURES / "debian-12.txt").read_text(encoding="utf-8")
    assert up.parse_status(base).pve is False
    assert up.parse_status(base.replace("@@pve\nno", "@@pve\nyes")).as_dict()["pve"] is True


def test_an_older_state_from_0_12_0_counts_without_the_delayed_ones():
    st = {"count": 4, "security_count": 1, "phased_count": 2}  # 0.12.0 kannte Phasing, aber noch keine Einteilung
    assert (_updates().runnable(st, "all"), _updates().runnable(st, "security")) == (2, 1)


def test_the_status_reads_held_packages_without_root():
    cmd = _updates().status_command(refresh=False)
    assert "apt-mark showhold" in cmd and "sudo" not in cmd
    assert cmd.index("echo @@phasing;") < cmd.index("echo @@held;") < cmd.index("echo @@security;")


def test_held_names_drop_the_architecture_and_odd_lines():
    assert _updates()._held_names(["demo-held", "demo-foreign:i386", "evil;rm", "-h"]) == {"demo-held", "demo-foreign"}


def test_only_valid_package_names_count_as_phased():
    lines = ["all evil;rm", "all -h", "all demo-phased", "inst demo-normal", "all demo-normal", "other demo-x"]
    runs = _updates()._run_names(lines)
    assert runs["all"] - runs["inst"] == {"demo-phased"}


def test_the_phasing_check_only_simulates_in_english_without_root():
    up = _updates()
    cmd = up.PHASING_COMMAND
    runs = re.findall(r"LC_ALL=C apt-get (.*?) 2>/dev/null", cmd)
    assert runs == ["-s upgrade --with-new-pkgs",
                    "-s -o APT::Get::Always-Include-Phased-Updates=true upgrade --with-new-pkgs"]
    assert cmd.count("apt-get") == len(runs) + 1  # dazu nur `command -v apt-get`
    assert "upgrade --with-new-pkgs" in up.upgrade_command("apt", "all")  # wie „Alle einspielen“
    assert "sudo" not in cmd and "shield-root" not in cmd
    for refresh in (False, True):
        status = up.status_command(refresh=refresh)
        assert status.index("echo @@list;") < status.index(cmd) < status.index("echo @@security;")
