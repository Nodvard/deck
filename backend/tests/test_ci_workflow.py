"""`.github/workflows/ci.yml`: Rechte des Laufs.

Die CI checkt nur aus, baut und testet. Das `GITHUB_TOKEN`, das GitHub jedem Lauf gibt, darf darum nur lesen: fuer den
ganzen Workflow festgelegt, damit auch ein spaeter dazukommender Job nicht still mit den Standardrechten des
Repositorys laeuft (die koennen Schreibrechte sein). Die Tests lesen nur die YAML-Struktur.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def test_the_token_may_only_read_for_the_whole_workflow():
    assert _workflow()["permissions"] == {"contents": "read"}


def test_no_job_asks_for_more():
    for name, job in _workflow()["jobs"].items():
        permissions = job.get("permissions", {})
        assert permissions in ({}, {"contents": "read"}), (name, permissions)


def test_no_secrets_in_the_ci():
    assert re.findall(r"\bsecrets\b", CI.read_text(encoding="utf-8")) == []
