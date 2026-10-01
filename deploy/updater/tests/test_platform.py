"""Plattform: der Helfer laeuft nur unter Linux (POSIX).

Dieses Modul ist das einzige, das unter Windows eingesammelt wird (siehe `conftest.py`): dort meldet es
"uebersprungen", und `python -m pytest deploy/updater/tests` endet mit Exit 0 statt mit 5 ("no tests ran").
"""

from __future__ import annotations

import os

import pytest
import updater_support


def test_the_helper_runs_on_posix_only():
    if os.name != "posix":
        pytest.skip("Der Helfer laeuft nur im Linux-Container (fcntl, dir_fd, O_NOFOLLOW); Tests nur unter Linux/macOS")
    # Kein Testmodul muss hier eingetragen werden: `conftest.py` ueberspringt unter anderen Plattformen alle ausser diesem.
    modules = updater_support.posix_only_modules()
    assert {"test_channel.py", "test_policy.py", "test_state.py", "test_code_guard.py"} <= set(modules)
    assert "test_platform.py" not in modules
