"""Die gebauten Wheels: Inhalt und Aufbau (so, wie das Docker-Image sie per `pip install ./backend` installiert).

Die Kernpakete heissen `nodvard_deck` und `nodvard_sdk`; die alten Namen `lattice` und `lattice_sdk` liegen als
winzige Alias-Pakete in **derselben** Distribution (`nodvard-deck` bzw. `nodvard-sdk`), damit der Installationsbefehl
`pip install -e sdk/python -e "backend[dev]"` unveraendert bleibt.

Wichtigster Punkt: die Aenderungsprotokoll-Dateien (`versions/*.toml`, `unreleased/*.toml`) sind keine Python-Pakete und
kommen nur ueber `[tool.setuptools.package-data]` ins Wheel. Steht dort der alte Paketname, ist die Seite "Ueber" im Image
leer -- und kein anderer Test merkt es, weil die Tests aus dem Quellbaum laufen.

Gebaut wird mit `pip wheel --no-deps` (sonst `uv build`) in einer Kopie der Quellen, damit im Repo nichts liegen bleibt.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from restore_helpers import REPO_ROOT

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Prozesse wie im Container (POSIX)")

BACKEND = REPO_ROOT / "backend"
SDK = REPO_ROOT / "sdk" / "python"
BUILD_IGNORE = shutil.ignore_patterns("*.egg-info", "build", "dist", "__pycache__", "*.pyc", "tests", "migrations", ".venv")


def _wheel_commands(work: Path, out: Path) -> list[list[str]]:
    """Mit Build-Isolation (holt `setuptools` aus dem Index, wie `pip install ./backend` im Image), ohne Netz und mit
    vorhandenem `setuptools` ersatzweise ohne Isolation."""
    commands: list[list[str]] = []
    if importlib.util.find_spec("pip") is not None:
        base = [sys.executable, "-m", "pip", "wheel", "--no-deps", "--disable-pip-version-check", "-w", str(out)]
        commands.append([*base, str(work)])
        if importlib.util.find_spec("setuptools") is not None:
            commands.append([*base, "--no-build-isolation", str(work)])
    elif shutil.which("uv"):
        commands.append([str(shutil.which("uv")), "build", "--wheel", "--out-dir", str(out), str(work)])
    return commands


def _build_wheel(source: Path, workdir: Path) -> Path:
    work = workdir / f"quelle-{source.name}"
    shutil.copytree(source, work, ignore=BUILD_IGNORE)
    out = workdir / f"wheels-{source.name}"
    out.mkdir()
    commands = _wheel_commands(work, out)
    if not commands:
        pytest.skip("weder pip noch uv zum Bauen eines Wheels vorhanden")
    result = None
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600, check=False)
        if result.returncode == 0:
            break
    assert result is not None and result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    (wheel,) = out.glob("*.whl")
    return wheel


@pytest.fixture(scope="module")
def wheels(tmp_path_factory) -> dict[str, Path]:
    workdir = tmp_path_factory.mktemp("wheels")
    return {"backend": _build_wheel(BACKEND, workdir), "sdk": _build_wheel(SDK, workdir)}


def _names(wheel: Path) -> set[str]:
    with zipfile.ZipFile(wheel) as zf:
        return set(zf.namelist())


def _metadata(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as zf:
        (name,) = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA")]
        return zf.read(name).decode("utf-8")


def test_the_core_wheel_carries_the_changelog_files(wheels):
    names = _names(wheels["backend"])
    source = BACKEND / "src" / "nodvard_deck" / "changelog"
    versions = sorted(p.name for p in (source / "versions").glob("*.toml"))
    unreleased = sorted(p.name for p in (source / "unreleased").glob("*.toml"))
    # Direkt nach einem Release (scripts/release.py) ist unreleased/ gewollt leer; dann prueft der Test
    # nur die Versionsdateien. Die muessen immer da sein, sonst prueft dieser Test nichts.
    assert versions, "versions/ muss Dateien haben, sonst prueft dieser Test nichts"
    for name in versions:
        assert f"nodvard_deck/changelog/versions/{name}" in names, name
    for name in unreleased:
        assert f"nodvard_deck/changelog/unreleased/{name}" in names, name
    assert not [n for n in names if n.startswith("lattice/changelog")], "der alte Ordner darf nicht mehr mitkommen"


def test_the_core_wheel_has_every_module_of_the_core(wheels):
    names = _names(wheels["backend"])
    source = BACKEND / "src" / "nodvard_deck"
    missing = [str(p.relative_to(BACKEND / "src")) for p in source.rglob("*.py") if str(p.relative_to(BACKEND / "src")) not in names]
    assert missing == []


def test_the_core_wheel_holds_only_the_launcher_files_under_the_old_name(wheels):
    names = {n for n in _names(wheels["backend"]) if n.startswith("lattice/")}
    assert names == {f"lattice/{n}.py" for n in ("__init__", "migrate", "boot", "admin", "rescue")}


def test_the_core_wheel_metadata(wheels):
    meta = _metadata(wheels["backend"])
    assert "Name: nodvard-deck" in meta
    assert "Requires-Dist: nodvard-sdk" in meta
    assert "lattice-sdk" not in meta


def test_the_sdk_wheel_holds_the_contract_the_alias_and_the_shared_helper(wheels):
    names = _names(wheels["sdk"])
    source = SDK / "nodvard_sdk"
    missing = [f"nodvard_sdk/{p.name}" for p in source.glob("*.py") if f"nodvard_sdk/{p.name}" not in names]
    assert missing == []
    assert "_nodvard_alias.py" in names
    assert {n for n in names if n.startswith("lattice_sdk/")} == {"lattice_sdk/__init__.py"}
    assert "Name: nodvard-sdk" in _metadata(wheels["sdk"])


def test_the_installed_layout_works_without_the_source_tree(wheels, tmp_path):
    """Die Wheels entpackt wie in `site-packages` (nicht editierbar, wie im Image): alter und neuer Name, die
    Aenderungsprotokoll-Dateien und `python -m lattice.migrate` gegen eine leere Datenbank."""
    site = tmp_path / "site"
    for wheel in wheels.values():
        with zipfile.ZipFile(wheel) as zf:
            zf.extractall(site)
    data = tmp_path / "data"
    data.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NODVARD_DECK_", "LATTICE_"))}
    env.update({
        "PYTHONPATH": str(site),
        "NODVARD_DECK_DATA_DIR": str(data),
        "NODVARD_DECK_DATABASE_URL": f"sqlite+aiosqlite:///{data / 'lattice.db'}",
    })
    check = (
        "import warnings; warnings.simplefilter('ignore')\n"
        "import nodvard_deck, nodvard_sdk, lattice, lattice_sdk, lattice.db.base, nodvard_deck.db.base\n"
        f"site = {str(site)!r}\n"
        "assert nodvard_deck.__file__.startswith(site) and nodvard_sdk.__file__.startswith(site), (nodvard_deck.__file__, nodvard_sdk.__file__)\n"
        "assert lattice is nodvard_deck and lattice_sdk is nodvard_sdk\n"
        "assert lattice.db.base.IdMixin is nodvard_deck.db.base.IdMixin\n"
        "from nodvard_deck.changelog import get_changelog\n"
        "log = get_changelog()\n"
        # unreleased darf direkt nach einem Release leer sein (scripts/release.py raeumt es ab).
        "assert log.latest and len(log.versions) >= 5, (log.latest, len(log.versions), len(log.unreleased))\n"
        "print('ok', log.latest)\n"
    )
    result = subprocess.run([sys.executable, "-c", check], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ok ")
    for module in ("lattice.migrate", "nodvard_deck.migrate"):
        migrated = subprocess.run([sys.executable, "-m", module], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=240)
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr
        assert "Migrations-Verzeichnis" in migrated.stdout
