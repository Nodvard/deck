"""Update-Zentrale: Updates, die apt wegen Phasing noch zurückhält, an echten Aufnahmen aus Ubuntu und Debian."""

from __future__ import annotations

import re
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
    assert len(st.packages) == 8
    assert sorted(p.name for p in st.packages if p.phased) == PHASED
    d = st.as_dict()
    assert (d["count"], d["phased_count"]) == (8, 4)
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
    assert len(st.packages) == 8
    assert st.phased_count == 0


def test_phased_updates_that_apt_installs_anyway_do_not_count():
    st = _parse("ubuntu-24.04-immer-alle")
    assert len(st.packages) == 8
    assert st.phased_count == 0


def test_broken_dependencies_are_not_passed_off_as_phasing():
    st = _parse("ubuntu-24.04-kaputt")
    assert len(st.packages) == 8
    assert st.phased_count == 0


def test_a_status_without_the_phasing_section_has_no_phased_updates():
    text = (FIXTURES / "ubuntu-24.04.txt").read_text(encoding="utf-8")
    without = re.sub(r"@@phasing\n.*?(?=@@security)", "", text, flags=re.DOTALL)
    st = _updates().parse_status(without)
    assert len(st.packages) == 8
    assert st.phased_count == 0


def test_only_valid_package_names_count_as_phased():
    lines = ["all evil;rm", "all -h", "all demo-phased", "inst demo-normal", "all demo-normal", "other demo-x"]
    assert _updates()._phased_names(lines) == {"demo-phased"}


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
