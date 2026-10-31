"""scripts/release_newest.py: nur die höchste fertige Version bekommt `latest` und die Tags ohne Nummer."""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("release_newest", REPO_ROOT / "scripts" / "release_newest.py")
newest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(newest)


@pytest.mark.parametrize(
    ("version", "tags", "expected"),
    [
        ("0.13.0", ["0.12.0", "0.12.1", "0.13.0"], True),
        ("0.12.2", ["0.12.1", "0.13.0", "0.12.2"], False),
        ("0.12.2", ["0.12.1", "0.13.0-rc1", "0.12.2"], True),
        ("0.10.0", ["0.9.9", "0.10.0"], True),
        ("1.0.0", ["0.12.3", "1.0.0", "1.0.0-rc2"], True),
        ("0.9.0", ["0.10.0", "0.9.0"], False),
        ("0.12.2", ["0.12.2", "0.12.10"], False),
    ],
    ids=["hoechste", "aeltere-reihe", "rc-darueber", "zahl-statt-text", "nach-rc", "zahl-statt-text-2", "patch-zahl"],
)
def test_is_newest(version, tags, expected):
    assert newest.is_newest(version, tags) is expected


@pytest.mark.parametrize("version", ["0.13.0-rc1", "1.0.0-beta.2"])
def test_a_prerelease_is_never_newest(version):
    assert newest.is_newest(version, ["0.12.0", version]) is False


def test_tags_that_are_no_version_do_not_count():
    assert newest.is_newest("0.12.0", ["0.12.0", "vtest", "0.13", "1.2.3.4", "09.1.0", "99.0.0+x"]) is True


@pytest.mark.parametrize("version", ["", "0.13", "v0.13.0", "0.13.0+b1", "01.0.0"])
def test_an_invalid_version_is_refused(version):
    with pytest.raises(ValueError):
        newest.is_newest(version, [version])


def test_without_the_own_tag_the_list_counts_as_incomplete():
    with pytest.raises(ValueError, match="nicht in der Liste"):
        newest.is_newest("0.13.0", [])


def test_tag_versions_reads_ls_remote_and_plain_lines():
    lines = [f"{'a' * 40}\trefs/tags/v0.12.1", "v0.13.0\n", "", f"{'b' * 40}\trefs/tags/other", "  v1.0.0-rc1  "]
    assert newest.tag_versions(lines) == ["0.12.1", "0.13.0", "1.0.0-rc1"]


def test_main_prints_one_output_line(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"{'a' * 40}\trefs/tags/v0.12.2\n{'a' * 40}\trefs/tags/v0.13.0\n"))
    assert newest.main(["release_newest.py", "0.12.2"]) == 0
    assert capsys.readouterr().out == "newest=false\n"


def test_main_fails_without_a_complete_list(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert newest.main(["release_newest.py", "0.13.0"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "nicht in der Liste" in captured.err
