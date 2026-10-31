"""Wer ohne `deploy/constraints.txt` installiert, bekommt keine Fassung ohne die bekannten Sicherheitskorrekturen."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

SECURITY_FLOORS = {"dulwich": "1.2.19"}


def _requirements() -> dict[str, Requirement]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    reqs = (Requirement(line) for line in data["project"]["dependencies"])
    return {req.name.lower(): req for req in reqs}


def test_security_fixes_are_the_lower_bound():
    reqs = _requirements()
    for name, floor in SECURITY_FLOORS.items():
        lower = [Version(s.version) for s in reqs[name].specifier if s.operator in (">=", ">", "==", "~=")]
        assert lower and max(lower) >= Version(floor), f"{name}: Untergrenze unter {floor}"
