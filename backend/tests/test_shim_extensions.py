"""Fremd-Extensions, die noch die alten Namen benutzen (`lattice_sdk`, `lattice.db.base`, Entry-Point-Gruppe
`lattice.extensions`), werden weiter gefunden und geladen.

Dazu: die neue Gruppe `nodvard_deck.extensions` wird ebenfalls gelesen, doppelte Eintraege (eine Extension, die fuer
alte UND neue Kerne in beiden Gruppen steht) zaehlen einmal. Die Extensions im Repo selbst laden weiter per
Verzeichnis-Scan (`test_extensions_service.py`).

Diese Datei darf `lattice` und `lattice_sdk` importieren (Ausnahme im Waechter `scripts/check_legacy_names.py`: `test_shim_*`).
"""

from __future__ import annotations

import importlib
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import FastAPI
from nodvard_deck.ext import discovery
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import ExtensionRecord
from nodvard_deck.services import extensions as extensions_service

# Die Fremd-Extensions nutzen absichtlich die alten Namen (DeprecationWarning ist dort das erwartete Ergebnis).
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

FOREIGN_MODULES = ("fremd_ext", "pip_neu", "pip_alt", "pip_beide")


@pytest.fixture(autouse=True)
def _clean_runtime_and_modules():
    reset_extension_runtime()
    before = list(sys.path)
    yield
    reset_extension_runtime()
    sys.path[:] = before
    for name in FOREIGN_MODULES:
        sys.modules.pop(name, None)
    importlib.invalidate_caches()


# Eine Extension, wie sie ein Dritter heute geschrieben hat: nur die ALTEN Namen.
FOREIGN_CODE = '''
from lattice_sdk import ExtensionContext, HealthReport, LatticeExtension
from lattice_sdk.errors import LatticeError
from lattice.db.base import Base as CoreBase, IdMixin, UTCDateTime, utcnow
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Item(Base, IdMixin):
    __tablename__ = "ext_{ext}_items"
    name: Mapped[str] = mapped_column(String(50))


class Extension(LatticeExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        import nodvard_deck.db.base as real
        import nodvard_sdk

        # Dieselben Objekte wie im Kern: sonst gaebe es eine zweite `Base` und `isinstance` brache.
        if CoreBase is not real.Base or LatticeExtension is not nodvard_sdk.NodvardExtension:
            raise LatticeError("Alias liefert Kopien statt derselben Objekte")
        if not issubclass(type(self), nodvard_sdk.NodvardExtension):
            raise LatticeError("Fremd-Extension ist keine NodvardExtension")
        ctx.db.declare_tables(Base.metadata)

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        return HealthReport(healthy=True)
'''


def _manifest(ext_id: str, module: str) -> str:
    return textwrap.dedent(
        f"""
        [extension]
        id = "{ext_id}"
        name = "Fremd {ext_id}"
        version = "1.0.0"
        api_version = "0.1.0"
        entrypoint = "{module}:Extension"
        permissions = []
        """
    )


def _settings(test_settings, extensions_dir: Path):
    return test_settings.model_copy(update={"extensions_dir": extensions_dir})


@pytest.mark.asyncio
async def test_a_foreign_extension_in_a_directory_that_imports_the_old_names_loads(db_session, test_settings, tmp_path):
    ext_dir = tmp_path / "erweiterungen" / "fremd"
    (ext_dir / "src" / "fremd_ext").mkdir(parents=True)
    (ext_dir / "extension.toml").write_text(_manifest("fremd", "fremd_ext"), encoding="utf-8")
    (ext_dir / "src" / "fremd_ext" / "__init__.py").write_text(FOREIGN_CODE.replace("{ext}", "fremd"), encoding="utf-8")

    settings = _settings(test_settings, tmp_path / "erweiterungen")
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "fremd")

    record = await db_session.get(ExtensionRecord, "fremd")
    assert record is not None and record.state == "enabled", record.last_error
    assert "fremd" in get_extension_runtime().loaded
    await extensions_service.disable_extension(app, db_session, "fremd")


# --------------------------------------------------------------------------- Entry-Points


def _make_dist(root: Path, dist: str, module: str, ext_id: str, groups: tuple[str, ...]) -> None:
    """Eine installierte Distribution (nur Metadaten, wie nach `pip install`): `<root>/<dist>.dist-info` mit
    `entry_points.txt`, daneben `extension.toml` im Wurzelordner und das Modul."""
    root.mkdir(parents=True)
    info = root / f"{dist.replace('-', '_')}-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {dist}\nVersion: 1.0\n", encoding="utf-8")
    sections = "".join(f"[{group}]\n{ext_id} = {module}:Extension\n\n" for group in groups)
    (info / "entry_points.txt").write_text(sections, encoding="utf-8")
    (root / "extension.toml").write_text(_manifest(ext_id, module), encoding="utf-8")
    (root / f"{module}.py").write_text(FOREIGN_CODE.replace("{ext}", ext_id.replace("-", "_")), encoding="utf-8")


@pytest.fixture
def three_dists(tmp_path, monkeypatch):
    """Eine Extension nur in der neuen Gruppe, eine nur in der alten, eine in beiden."""
    base = tmp_path / "site"
    _make_dist(base / "neu", "ext-neu", "pip_neu", "ext-neu", (discovery.DEFAULT_ENTRY_POINT_GROUPS[0],))
    _make_dist(base / "alt", "ext-alt", "pip_alt", "ext-alt", (discovery.DEFAULT_ENTRY_POINT_GROUPS[1],))
    _make_dist(base / "beide", "ext-beide", "pip_beide", "ext-beide", discovery.DEFAULT_ENTRY_POINT_GROUPS)
    for sub in ("neu", "alt", "beide"):
        monkeypatch.syspath_prepend(str(base / sub))
    importlib.invalidate_caches()
    return base


def test_the_default_groups_are_the_new_name_first_and_the_old_one():
    assert discovery.DEFAULT_ENTRY_POINT_GROUPS == ("nodvard_deck.extensions", "lattice.extensions")


def test_entry_points_in_both_groups_are_found_and_doubles_count_once(three_dists):
    found = discovery.scan_entry_points()
    ids = [f.id for f in found]
    assert all(f.ok for f in found), [(f.id, f.error) for f in found]
    assert sorted(ids) == ["ext-alt", "ext-beide", "ext-neu"], "ext-beide steht in beiden Gruppen, zaehlt aber einmal"
    assert ids[-1] == "ext-alt", "erst die neue Gruppe, dann die alte"
    assert all(f.source == "pip" for f in found)


def test_a_single_group_can_still_be_asked_for(three_dists):
    assert sorted(f.id for f in discovery.scan_entry_points("nodvard_deck.extensions")) == ["ext-beide", "ext-neu"]
    assert sorted(f.id for f in discovery.scan_entry_points(group="lattice.extensions")) == ["ext-alt", "ext-beide"]
    both = [f.id for f in discovery.scan_entry_points(["lattice.extensions", "nodvard_deck.extensions"])]
    assert sorted(both) == ["ext-alt", "ext-beide", "ext-neu"] and both[-1] == "ext-neu"  # Reihenfolge der Gruppen zaehlt


def test_discover_all_returns_the_directory_scan_and_both_entry_point_groups(three_dists, tmp_path):
    (tmp_path / "verz" / "lokal").mkdir(parents=True)
    (tmp_path / "verz" / "lokal" / "extension.toml").write_text(_manifest("lokal", "lokal_ext"), encoding="utf-8")
    found = discovery.discover_all(tmp_path / "verz")
    assert (found[0].id, found[0].source) == ("lokal", "bundled")
    assert sorted((f.id, f.source) for f in found[1:]) == [("ext-alt", "pip"), ("ext-beide", "pip"), ("ext-neu", "pip")]


@pytest.mark.asyncio
async def test_extensions_from_both_entry_point_groups_load(three_dists, db_session, test_settings, tmp_path):
    settings = _settings(test_settings, tmp_path / "leer")
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    await extensions_service.discover_and_sync(db_session, settings)
    for ext_id in ("ext-neu", "ext-alt", "ext-beide"):
        await extensions_service.enable_extension(app, db_session, settings, ext_id)
        record = await db_session.get(ExtensionRecord, ext_id)
        assert record.state == "enabled", (ext_id, record.last_error)
        assert record.source == "pip"
    assert set(get_extension_runtime().loaded) == {"ext-neu", "ext-alt", "ext-beide"}
    for ext_id in ("ext-neu", "ext-alt", "ext-beide"):
        await extensions_service.disable_extension(app, db_session, ext_id)


def test_the_same_distribution_with_a_different_manifest_id_is_not_merged(tmp_path, monkeypatch):
    """Zusammengefasst wird nur dieselbe Distribution mit derselben ID, nie zwei verschiedene Extensions."""
    base = tmp_path / "site"
    _make_dist(base / "a", "ext-a", "pip_neu", "id-a", ("nodvard_deck.extensions",))
    _make_dist(base / "b", "ext-b", "pip_alt", "id-a", ("lattice.extensions",))  # gleiche ID, andere Distribution
    for sub in ("a", "b"):
        monkeypatch.syspath_prepend(str(base / sub))
    importlib.invalidate_caches()
    found = discovery.scan_entry_points()
    assert [f.id for f in found] == ["id-a", "id-a"], "zwei Distributionen bleiben zwei Funde (den Namenskonflikt loest die Registry)"
