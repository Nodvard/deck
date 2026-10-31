"""Gemeinsames für die Tests des Update-Helfers (Konstanten, Vektoren, `sys.path`).

Bewusst **kein** `from conftest import ...` in den Tests: Läuft pytest zusammen mit `backend/tests` in einem
Prozess, gibt es zwei Module namens `conftest`, und der Import landet beim falschen. Dieses Modul hat einen
eigenen Namen; `conftest.py` hier hat nur noch Fixtures.

Das Paket `nodvard_deck_updater` ist nicht installiert (im Image liegt es später direkt in `site-packages`);
dieses Modul macht es aus `deploy/updater/` importierbar. So laufen die Tests mit
`python -m pytest deploy/updater/tests` aus dem Repo-Wurzelordner, ohne die Backend-Tests zu berühren.

Die meisten Tests laufen ohne root: Besitzerprüfungen bekommen die eigene uid als `expected_uid` (im Betrieb
ist es 0), und `fake_owner` lässt `fstat` für einzelne Dateien einen anderen Besitzer melden. Wo nur root
wirklich etwas prüfen kann (Dateien eines anderen Besitzers), ist der Test mit `needs_root` markiert: er ist ein
echter Marker (`pytest -m needs_root`) und wird übersprungen, wenn der Lauf kein `chown` darf (kein root oder
root ohne `CAP_CHOWN`). Die CI führt diese Tests zusätzlich mit `sudo` aus (Schritt in `ci.yml`).
"""

from __future__ import annotations

import contextlib
import hashlib
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
    """Marker `needs_root` (für `pytest -m needs_root`) und Skip, wenn der Lauf keine Dateien umhängen darf."""
    skip = pytest.mark.skipif(not CAN_CHOWN, reason="braucht root mit CAP_CHOWN (Dateien eines anderen Besitzers)")
    return pytest.mark.needs_root(skip(func))


def posix_only_modules() -> list[str]:
    """Alle Testmodule außer `test_platform.py`: sie laufen nur unter POSIX (Linux-Container)."""
    return sorted(path.name for path in TESTS_DIR.glob("test_*.py") if path.name != "test_platform.py")


def fake_owner(monkeypatch: pytest.MonkeyPatch, owners: dict[Path, int]) -> None:
    """Lässt `os.fstat` für die genannten Dateien/Ordner (Pfad -> uid) einen anderen Besitzer melden.

    Ohne root geht kein `chown`; so lassen sich die Besitzerprüfungen des Helfers trotzdem ohne root prüfen. Erkannt
    wird die Datei am Inode, die Pfade müssen beim Aufruf schon existieren."""
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


SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
OTHER_SEED = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
"""Geheime Schlüssel aus RFC 8032 (Test 1 und 2), also öffentlich bekannt: nur für Tests."""


def _secret_scalar(seed: bytes) -> tuple[int, bytes]:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def public_key(seed: bytes) -> bytes:
    from nodvard_deck_updater import ed25519

    return ed25519._encode(ed25519._multiply(_secret_scalar(seed)[0], ed25519._BASE))


def sign(seed: bytes, message: bytes) -> bytes:
    """Ed25519-Signatur nach RFC 8032, damit die Tests ohne `cryptography` signieren können."""
    from nodvard_deck_updater import ed25519

    scalar, prefix = _secret_scalar(seed)
    public = ed25519._encode(ed25519._multiply(scalar, ed25519._BASE))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % ed25519._L
    encoded_r = ed25519._encode(ed25519._multiply(r, ed25519._BASE))
    k = int.from_bytes(hashlib.sha512(encoded_r + public + message).digest(), "little") % ed25519._L
    return encoded_r + ((r + k * scalar) % ed25519._L).to_bytes(32, "little")
