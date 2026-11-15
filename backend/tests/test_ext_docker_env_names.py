"""Namen von Variablen für neue Stacks: gleiche Regel wie bisher, feste Länge, kein teurer Vergleich."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest

EXT_SRC = Path(__file__).resolve().parents[2] / "extensions/docker/src"
FORMER_RULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@pytest.fixture
def env_values(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_SRC))
    from nodvard_deck_ext_docker import env_values as module

    yield module
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


NAMES = [
    "A", "_", "a", "DB_PASSWORD", "_x9", "a" * 255, "Z9_", "", "9A", "A-B", "A B", "A\n", "\nA", "A.B", "Ä", "AÄ", "A١", "ａ",
    "A=B", "A$", " A", "A\x00", "1", "-", "á",
]


@pytest.mark.parametrize("name", NAMES)
def test_names_follow_the_former_rule(env_values, name):
    expected = FORMER_RULE.fullmatch(name) is not None
    assert env_values.valid_env_name(name) is expected
    assert env_values.valid_env_names([name]) is expected
    if expected:
        assert env_values.env_file({name: "wert"}) == f'{name}="wert"\n'.encode()
    else:
        with pytest.raises(ValueError, match="Variablennamen dürfen nur Buchstaben"):
            env_values.env_file({name: "wert"})


def test_first_character_may_not_be_a_digit(env_values):
    assert not env_values.valid_env_name("1ABC")
    assert env_values.valid_env_name("ABC1")


def test_name_length_is_limited(env_values):
    limit = env_values.MAX_NAME_LENGTH
    assert limit == 255
    assert env_values.valid_env_names(["A" * limit])
    assert not env_values.valid_env_names(["A" * (limit + 1)])
    with pytest.raises(ValueError, match="höchstens 255 Zeichen"):
        env_values.env_file({"A" * (limit + 1): "wert"})
    with pytest.raises(ValueError):
        env_values.with_saved_env(["docker", "compose"], ("A" * (limit + 1),))


@pytest.mark.parametrize("tail", ["", "!", "\n"])
def test_long_names_are_refused_quickly(env_values, tail):
    name = "A" * 100_000 + tail
    started = time.perf_counter()
    assert not env_values.valid_env_name(name)
    assert not env_values.valid_env_names([name] * 100)
    with pytest.raises(ValueError):
        env_values.env_file({name: "wert"})
    assert time.perf_counter() - started < 1


def test_names_are_checked_without_a_pattern(env_values):
    assert not hasattr(env_values, "re")
