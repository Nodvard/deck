"""Erweiterungen mit den alten Paket- und Klassennamen und der alten Entry-Point-Gruppe."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from nodvard_deck.ext import discovery
from nodvard_deck.ext.old_names import (
    OLD_ENTRY_POINT_GROUP,
    OLD_GROUP_ERROR,
    old_import_error,
)
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import ExtensionRecord
from nodvard_deck.services import extensions as extensions_service
from sqlalchemy import select

MANIFEST = """
[extension]
id = "{id}"
name = "Alt"
version = "0.1.0"
api_version = "0.1"
entrypoint = "{package}:Extension"
"""


@pytest.fixture(autouse=True)
def _reset_runtime_and_sys_path():
    reset_extension_runtime()
    before = list(sys.path)
    yield
    reset_extension_runtime()
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith("old_names_ext_"):
            del sys.modules[name]


@pytest.mark.parametrize(
    ("name", "old", "new"),
    [
        ("lattice_sdk", "lattice_sdk", "nodvard_sdk"),
        ("lattice_sdk.errors", "lattice_sdk", "nodvard_sdk"),
        ("lattice", "lattice", "nodvard_deck"),
        ("lattice.db.base", "lattice", "nodvard_deck"),
    ],
)
def test_an_old_package_name_gets_a_clear_reason(name, old, new):
    reason = old_import_error(ModuleNotFoundError(f"No module named '{name}'", name=name))
    assert f"Paketnamen „{old}“" in reason and f"„{new}“" in reason


def _raised(tmp_path: Path, monkeypatch, code: str) -> BaseException:
    """Der Fehler beim Import eines Moduls mit `code`, so wie beim Laden einer Erweiterung."""
    (tmp_path / "old_names_ext_probe.py").write_text(code + "\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    with pytest.raises((ImportError, AttributeError)) as info:
        importlib.import_module("old_names_ext_probe")
    return info.value


@pytest.mark.parametrize(
    ("code", "old", "new"),
    [
        ("from nodvard_sdk import LatticeExtension", "LatticeExtension", "NodvardExtension"),
        ("from nodvard_sdk import NodvardExtension, LatticeError", "LatticeError", "NodvardError"),
        ("from nodvard_sdk.errors import LatticeError", "LatticeError", "NodvardError"),
        ("import nodvard_sdk\nnodvard_sdk.LatticeExtension", "LatticeExtension", "NodvardExtension"),
    ],
)
def test_an_old_class_name_from_the_new_package_gets_a_clear_reason(tmp_path, monkeypatch, code, old, new):
    reason = old_import_error(_raised(tmp_path, monkeypatch, code))
    assert f"Klassennamen „{old}“" in reason and f"„{new}“" in reason


@pytest.mark.parametrize(
    "exc",
    [
        ModuleNotFoundError("No module named 'latticex'", name="latticex"),
        ModuleNotFoundError("No module named 'requests'", name="requests"),
        ModuleNotFoundError("ohne Namen"),
        ImportError("cannot import name 'x' from 'lattice'", name="lattice"),
        ImportError("cannot import name 'LatticeExtension' from 'anderes'", name="anderes"),
        RuntimeError("lattice"),
    ],
)
def test_other_errors_keep_their_own_text(exc):
    assert old_import_error(exc) is None


@pytest.mark.parametrize(
    "code",
    [
        "from nodvard_sdk import GibtEsNicht",
        "import nodvard_sdk\nnodvard_sdk.GibtEsNicht",
        "import json\njson.LatticeError",
    ],
)
def test_other_missing_names_keep_their_own_text(tmp_path, monkeypatch, code):
    assert old_import_error(_raised(tmp_path, monkeypatch, code)) is None


def _write_extension(root: Path, ext_id: str, body: str) -> None:
    package = "old_names_ext_" + ext_id.replace("-", "_")
    ext_dir = root / "extensions" / ext_id
    (ext_dir / "src" / package).mkdir(parents=True)
    (ext_dir / "extension.toml").write_text(MANIFEST.format(id=ext_id, package=package), encoding="utf-8")
    (ext_dir / "src" / package / "__init__.py").write_text(body, encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("from lattice_sdk import NodvardExtension", "alten Paketnamen „lattice_sdk“"),
        ("from lattice.db.base import Base", "alten Paketnamen „lattice“"),
        ("from nodvard_sdk import LatticeExtension", "alten Klassennamen „LatticeExtension“"),
    ],
)
async def test_an_extension_importing_an_old_name_names_it(
    tmp_path, db_session, test_settings, monkeypatch, line, reason
):
    # Eine alte Arbeitskopie kann die Pakete noch installiert haben; dass sie im Wheel fehlen, prüft test_wheel.py.
    for name in ("lattice", "lattice_sdk"):
        monkeypatch.setitem(sys.modules, name, None)
    _write_extension(tmp_path, "alt-ext", f"{line}\n\nclass Extension:\n    pass\n")
    settings = test_settings.model_copy(update={"extensions_dir": tmp_path / "extensions"})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)

    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "alt-ext")

    record = await db_session.get(ExtensionRecord, "alt-ext")
    assert record.state == "error"
    assert reason in record.last_error
    assert "Import von" not in record.last_error
    assert "alt-ext" not in get_extension_runtime().loaded


class _Dist:
    def __init__(self, name: str, root: Path) -> None:
        self.name = name
        self._root = root

    def locate_file(self, path: str) -> Path:
        return self._root / path


class _EntryPoint:
    def __init__(self, name: str, dist: _Dist) -> None:
        self.name = name
        self.dist = dist


def _pip_package(tmp_path: Path, ext_id: str) -> _EntryPoint:
    root = tmp_path / f"{ext_id}-dist"
    root.mkdir()
    (root / "extension.toml").write_text(MANIFEST.format(id=ext_id, package="irgendwas"), encoding="utf-8")
    return _EntryPoint(ext_id, _Dist(f"{ext_id}-dist", root))


def _groups(monkeypatch, **by_group: list[_EntryPoint]) -> None:
    groups = {name.replace("__", "."): eps for name, eps in by_group.items()}
    monkeypatch.setattr(discovery.importlib.metadata, "entry_points", lambda group: groups.get(group, []))


def test_the_old_group_is_no_longer_read():
    assert OLD_ENTRY_POINT_GROUP not in discovery.DEFAULT_ENTRY_POINT_GROUPS


def test_a_package_only_in_the_old_group_is_refused_with_the_reason(tmp_path, monkeypatch):
    _groups(monkeypatch, lattice__extensions=[_pip_package(tmp_path, "alt-pip")])

    found = discovery.discover_all(tmp_path / "keine")

    assert [(d.id, d.ok, d.refused, d.error) for d in found] == [("alt-pip", False, True, OLD_GROUP_ERROR)]


def test_a_package_in_both_groups_loads_normally(tmp_path, monkeypatch):
    ep = _pip_package(tmp_path, "beide-pip")
    _groups(monkeypatch, nodvard_deck__extensions=[ep], lattice__extensions=[ep])

    found = discovery.discover_all(tmp_path / "keine")

    assert [(d.id, d.ok, d.refused) for d in found] == [("beide-pip", True, False)]


@pytest.mark.asyncio
async def test_an_enabled_package_only_in_the_old_group_shows_the_reason(
    tmp_path, db_session, test_settings, monkeypatch
):
    settings = test_settings.model_copy(update={"extensions_dir": tmp_path / "keine"})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    ep = _pip_package(tmp_path, "alt-pip")
    _groups(monkeypatch, nodvard_deck__extensions=[ep])
    await extensions_service.discover_and_sync(db_session, settings)
    record = await db_session.get(ExtensionRecord, "alt-pip")
    record.state = "enabled"
    await db_session.commit()

    reset_extension_runtime()
    _groups(monkeypatch, lattice__extensions=[ep])
    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.load_enabled_from_registry(app, db_session, settings)
    await db_session.commit()

    state, last_error = (
        await db_session.execute(
            select(ExtensionRecord.state, ExtensionRecord.last_error).where(ExtensionRecord.id == "alt-pip")
        )
    ).one()
    assert state == "enabled"
    assert OLD_GROUP_ERROR in last_error
