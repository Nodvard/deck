"""Gemeinsames fuer die Tests des Update-Helfers (Konstanten, Vektoren, `sys.path`).

Bewusst **kein** `from conftest import ...` in den Tests: Laeuft pytest zusammen mit `backend/tests` in einem
Prozess, gibt es zwei Module namens `conftest`, und der Import landet beim falschen. Dieses Modul hat einen
eigenen Namen; `conftest.py` hier hat nur noch Fixtures.

Das Paket `nodvard_deck_updater` ist nicht installiert (im Image liegt es spaeter direkt in `site-packages`);
dieses Modul macht es aus `deploy/updater/` importierbar. So laufen die Tests mit
`python -m pytest deploy/updater/tests` aus dem Repo-Wurzelordner, ohne die Backend-Tests zu beruehren.

Die meisten Tests laufen ohne root: Besitzerpruefungen bekommen die eigene uid als `expected_uid` (im Betrieb
ist es 0), und `fake_owner` laesst `fstat` fuer einzelne Dateien einen anderen Besitzer melden. Wo nur root
wirklich etwas pruefen kann (Dateien eines anderen Besitzers), ist der Test mit `needs_root` markiert: er ist ein
echter Marker (`pytest -m needs_root`) und wird uebersprungen, wenn der Lauf kein `chown` darf (kein root oder
root ohne `CAP_CHOWN`). Die CI fuehrt diese Tests zusaetzlich mit `sudo` aus (Schritt in `ci.yml`).
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

UPDATER_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
VECTORS = TESTS_DIR / "vectors"

if str(UPDATER_DIR) not in sys.path:
    sys.path.insert(0, str(UPDATER_DIR))


def _can_chown() -> bool:
    """root mit `CAP_CHOWN`: nur dann kann ein Test Dateien einem anderen Besitzer geben (Probe-chown)."""
    if os.name != "posix" or os.geteuid() != 0:
        return False
    fd, path = tempfile.mkstemp()
    try:
        os.close(fd)
        os.chown(path, 1000, 1000)
    except OSError:
        return False
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)
    return True


CAN_CHOWN = _can_chown()


def needs_root(func):
    """Marker `needs_root` (fuer `pytest -m needs_root`) und Skip, wenn der Lauf keine Dateien umhaengen darf."""
    skip = pytest.mark.skipif(not CAN_CHOWN, reason="braucht root mit CAP_CHOWN (Dateien eines anderen Besitzers)")
    return pytest.mark.needs_root(skip(func))


def posix_only_modules() -> list[str]:
    """Alle Testmodule ausser `test_platform.py`: sie laufen nur unter POSIX (Linux-Container)."""
    return sorted(path.name for path in TESTS_DIR.glob("test_*.py") if path.name != "test_platform.py")


def fake_owner(monkeypatch: pytest.MonkeyPatch, owners: dict[Path, int]) -> None:
    """Laesst `os.fstat` fuer die genannten Dateien/Ordner (Pfad -> uid) einen anderen Besitzer melden.

    Ohne root geht kein `chown`; so lassen sich die Besitzerpruefungen des Helfers trotzdem ohne root pruefen. Erkannt
    wird die Datei am Inode, die Pfade muessen beim Aufruf schon existieren."""
    inodes = {}
    for path, uid in owners.items():
        st = os.stat(path)
        inodes[(st.st_dev, st.st_ino)] = uid
    real_fstat = os.fstat

    def fstat(fd):
        st = real_fstat(fd)
        uid = inodes.get((st.st_dev, st.st_ino))
        if uid is None:
            return st
        return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, uid, st.st_gid, st.st_size,
                               int(st.st_atime), int(st.st_mtime), int(st.st_ctime)))

    monkeypatch.setattr(os, "fstat", fstat)


NOW = 1790812400
"""Feste Uhrzeit fuer die Tests (wie in den Vektoren)."""


def load_vectors(name: str) -> dict:
    return json.loads((VECTORS / name).read_text(encoding="ascii"))
