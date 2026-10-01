"""Fixtures und Sammelregeln fuer die Tests des Update-Helfers (Gemeinsames steht in `updater_support.py`)."""

from __future__ import annotations

import os

import pytest
import updater_support

if os.name != "posix":
    # Der Helfer laeuft nur im Linux-Container (fcntl, dir_fd, O_NOFOLLOW, ...): die anderen Module werden unter
    # Windows nicht eingesammelt. `test_platform.py` bleibt und meldet "uebersprungen" -- damit endet
    # `python -m pytest deploy/updater/tests` dort mit Exit 0 statt mit 5 ("no tests ran").
    collect_ignore = updater_support.posix_only_modules()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "needs_root: braucht root mit CAP_CHOWN (Dateien eines anderen Besitzers); sonst uebersprungen")


@pytest.fixture
def uid() -> int:
    """Die eigene uid: Besitzerpruefungen der Tests bekommen sie als `expected_uid` (im Betrieb ist es 0)."""
    return os.geteuid()
