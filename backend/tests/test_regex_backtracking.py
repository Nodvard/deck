"""Die umgebauten Prüfungen verhalten sich wie die alten regulären Ausdrücke und bleiben bei langen Eingaben schnell."""

from __future__ import annotations

import importlib
import itertools
import random
import re
import sys
import time
from pathlib import Path

import pytest

EXTENSIONS = Path(__file__).resolve().parents[2] / "extensions"

OLD_WILDCARD = re.compile(r"(/\*+)+/*$")
OLD_VOLID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*[A-Za-z0-9]:[A-Za-z0-9_.:/+-]{1,240}")


@pytest.fixture
def av(monkeypatch):
    monkeypatch.syspath_prepend(str(EXTENSIONS / "shield" / "src"))
    yield importlib.import_module("nodvard_deck_ext_shield.antivirus")
    for name in [n for n in sys.modules if n.startswith("nodvard_deck_ext_shield")]:
        del sys.modules[name]


@pytest.fixture
def backup_restore(monkeypatch):
    monkeypatch.syspath_prepend(str(EXTENSIONS / "backups" / "src"))
    yield importlib.import_module("nodvard_deck_ext_backups.restore")
    for name in [n for n in sys.modules if n.startswith("nodvard_deck_ext_backups")]:
        del sys.modules[name]


def test_trailing_wildcards_like_the_old_regex(av):
    for n in range(1, 9):
        for chars in itertools.product("/*a", repeat=n):
            text = "".join(chars)
            assert av._strip_trailing_wildcards(text) == OLD_WILDCARD.sub("", text), text


def test_volid_like_the_old_regex(backup_restore):
    rng = random.Random(7)
    for _ in range(30000):
        text = "".join(rng.choice("aZ09_.-:/+ ") for _ in range(rng.randint(1, 12)))
        assert bool(backup_restore._VOLID_RE.fullmatch(text)) == bool(OLD_VOLID.fullmatch(text)), text
    for volid in ("local:backup/vzdump-qemu-100-2026_10_01-03_00_00.vma.zst", "pbs-main:backup/vm/100/2026-10-01T03:00:00Z"):
        assert backup_restore._VOLID_RE.fullmatch(volid)


def test_long_inputs_stay_fast(av, backup_restore):
    start = time.monotonic()
    assert av._strip_trailing_wildcards("/srv" + "/*" * 100_000) == "/srv"
    assert backup_restore._VOLID_RE.fullmatch("a" + "-" * 100_000) is None
    assert backup_restore._VOLID_RE.fullmatch("a" * 100_000) is None
    assert time.monotonic() - start < 1
