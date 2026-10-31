"""Python-Pakete der Erweiterungen: die eingebauten heißen `nodvard_deck_ext_<id>`, Fremd-Erweiterungen wählen ihren
Paketnamen selbst, auch mit dem alten Präfix `OLD_PREFIX`."""

from __future__ import annotations

import importlib
import importlib.util
import shutil
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import FastAPI
from nodvard_deck.ext import discovery
from nodvard_deck.ext.old_names import OLD_ENTRY_POINT_GROUP, OLD_GROUP_ERROR
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import ExtensionRecord
from nodvard_deck.services import extensions as extensions_service
from nodvard_sdk import NodvardExtension, load_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_EXTENSIONS_DIR = REPO_ROOT / "extensions"
BUILT_IN = sorted(path.parent.name for path in REPO_EXTENSIONS_DIR.glob("*/extension.toml"))

NEW_PREFIX = "nodvard_deck_ext_"
OLD_PREFIX = "lattice_ext_"

_guard_spec = importlib.util.spec_from_file_location(
    "check_legacy_names_script_pkg", REPO_ROOT / "scripts" / "check_legacy_names.py"
)
_guard = importlib.util.module_from_spec(_guard_spec)
sys.modules["check_legacy_names_script_pkg"] = _guard  # dataclasses schlagen ihre Klasse hier nach
_guard_spec.loader.exec_module(_guard)

FOREIGN = '''
from nodvard_sdk import ExtensionContext, HealthReport, NodvardExtension


class Extension(NodvardExtension):
    where = __file__

    async def setup(self, ctx: ExtensionContext) -> None:
        self.logger_name = ctx.logger.name

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        return HealthReport(healthy=True)
'''


def module_name(ext_id: str, prefix: str = NEW_PREFIX) -> str:
    return prefix + ext_id.replace("-", "_")


@pytest.fixture(autouse=True)
def _clean_runtime_and_modules():
    reset_extension_runtime()
    before = list(sys.path)
    yield
    reset_extension_runtime()
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith((NEW_PREFIX, OLD_PREFIX)):
            del sys.modules[name]
    importlib.invalidate_caches()


def test_the_known_built_in_extensions_are_all_there():
    expected = {
        "backups", "documents", "gameserver", "hello-world", "inventory", "network", "nextcloud",
        "ntfy", "proxmox", "scripts", "docker", "shield", "system", "terminal", "uptime-kuma",
    }
    assert expected <= set(BUILT_IN), expected - set(BUILT_IN)


@pytest.mark.parametrize("ext_id", BUILT_IN)
def test_the_manifest_and_the_package_folder_follow_the_convention(ext_id):
    manifest = load_manifest(REPO_EXTENSIONS_DIR / ext_id / "extension.toml")
    module = module_name(ext_id)
    assert manifest.id == ext_id
    assert manifest.entrypoint == f"{module}:Extension"
    assert (REPO_EXTENSIONS_DIR / ext_id / "src" / module / "__init__.py").is_file()
    # Gezählt wird, was Git kennt: ein liegengebliebenes `__pycache__` einer alten Arbeitskopie zählt nicht.
    prefix = f"extensions/{ext_id}/src/"
    top = {rel[len(prefix):].split("/")[0] for rel in _guard.tracked_files() if rel.startswith(prefix)}
    assert top == {module}, top


@pytest.mark.asyncio
@pytest.mark.parametrize("ext_id", BUILT_IN)
async def test_every_built_in_extension_loads_under_its_package_name(ext_id, db_session, test_settings):
    settings = test_settings.model_copy(update={"extensions_dir": REPO_EXTENSIONS_DIR})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, ext_id)

    record = await db_session.get(ExtensionRecord, ext_id)
    assert record is not None and record.state == "enabled", record.last_error if record else None
    assert record.source == "bundled"
    module = module_name(ext_id)
    instance = get_extension_runtime().loaded[ext_id].instance
    assert isinstance(instance, NodvardExtension)
    assert type(instance).__module__.split(".")[0] == module
    package_file = REPO_EXTENSIONS_DIR / ext_id / "src" / module / "__init__.py"
    assert Path(sys.modules[module].__file__).resolve() == package_file.resolve()
    assert not [name for name in sys.modules if name.startswith(OLD_PREFIX)]

    await extensions_service.disable_extension(app, db_session, ext_id)


@pytest.mark.asyncio
async def test_the_old_package_name_of_a_built_in_extension_is_no_alias(db_session, test_settings):
    settings = test_settings.model_copy(update={"extensions_dir": REPO_EXTENSIONS_DIR})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "hello-world")
    assert module_name("hello-world") in sys.modules
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name("hello-world", OLD_PREFIX))
    await extensions_service.disable_extension(app, db_session, "hello-world")


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


def _foreign_dir(root: Path, ext_id: str, module: str) -> None:
    ext_dir = root / ext_id
    (ext_dir / "src" / module).mkdir(parents=True)
    (ext_dir / "extension.toml").write_text(_manifest(ext_id, module), encoding="utf-8")
    (ext_dir / "src" / module / "__init__.py").write_text(FOREIGN, encoding="utf-8")


async def _enable(app: FastAPI, db_session, settings, ext_id: str) -> ExtensionRecord:
    await extensions_service.enable_extension(app, db_session, settings, ext_id)
    record = await db_session.get(ExtensionRecord, ext_id)
    assert record is not None and record.state == "enabled", record.last_error if record else None
    return record


@pytest.mark.asyncio
async def test_a_foreign_extension_with_the_old_package_prefix_loads(db_session, test_settings, tmp_path):
    module = module_name("foo", OLD_PREFIX)
    _foreign_dir(tmp_path / "erweiterungen", "fremd", module)
    settings = test_settings.model_copy(update={"extensions_dir": tmp_path / "erweiterungen"})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    await extensions_service.discover_and_sync(db_session, settings)

    record = await _enable(app, db_session, settings, "fremd")

    assert record.source == "bundled"
    instance = get_extension_runtime().loaded["fremd"].instance
    assert type(instance).__module__ == module
    package_file = tmp_path / "erweiterungen" / "fremd" / "src" / module / "__init__.py"
    assert Path(instance.where).resolve() == package_file.resolve()
    assert instance.logger_name == "nodvard_deck.ext.fremd", "der Logger-Name hängt an der Kennung, nicht am Paket"
    await extensions_service.disable_extension(app, db_session, "fremd")


@pytest.mark.asyncio
async def test_a_foreign_package_named_like_a_built_in_one_is_its_own_module(db_session, test_settings, tmp_path):
    root = tmp_path / "erweiterungen"
    shutil.copytree(
        REPO_EXTENSIONS_DIR / "proxmox", root / "proxmox", ignore=shutil.ignore_patterns("frontend", "__pycache__")
    )
    _foreign_dir(root, "proxmox-fremd", module_name("proxmox", OLD_PREFIX))
    settings = test_settings.model_copy(update={"extensions_dir": root})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    await extensions_service.discover_and_sync(db_session, settings)
    await _enable(app, db_session, settings, "proxmox")
    await _enable(app, db_session, settings, "proxmox-fremd")

    new, old = sys.modules[module_name("proxmox")], sys.modules[module_name("proxmox", OLD_PREFIX)]
    assert new is not old
    assert Path(new.__file__).resolve() == (root / "proxmox" / "src" / module_name("proxmox") / "__init__.py").resolve()
    assert Path(old.__file__).resolve() == (root / "proxmox-fremd" / "src" / old.__name__ / "__init__.py").resolve()
    runtime = get_extension_runtime()
    assert type(runtime.loaded["proxmox"].instance).__module__ == new.__name__
    assert type(runtime.loaded["proxmox-fremd"].instance).__module__ == old.__name__
    for ext_id in ("proxmox", "proxmox-fremd"):
        await extensions_service.disable_extension(app, db_session, ext_id)


def _install(root: Path, ext_id: str, module: str, group: str) -> None:
    """Eine installierte Distribution wie nach `pip install`: Metadaten, Entry-Point, Manifest und Modul."""
    root.mkdir(parents=True)
    info = root / f"{ext_id.replace('-', '_')}-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {ext_id}\nVersion: 1.0\n", encoding="utf-8")
    (info / "entry_points.txt").write_text(f"[{group}]\n{ext_id} = {module}:Extension\n", encoding="utf-8")
    (root / "extension.toml").write_text(_manifest(ext_id, module), encoding="utf-8")
    (root / f"{module}.py").write_text(FOREIGN, encoding="utf-8")


@pytest.mark.asyncio
async def test_a_pip_installed_foreign_extension_with_the_old_package_prefix_loads(
    db_session, test_settings, tmp_path, monkeypatch
):
    module = module_name("pipfoo", OLD_PREFIX)
    site = tmp_path / "site" / "pipfoo"
    _install(site, "ext-pipfoo", module, discovery.DEFAULT_ENTRY_POINT_GROUPS[0])
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()

    found = discovery.scan_entry_points()
    assert [(f.id, f.source, f.ok) for f in found] == [("ext-pipfoo", "pip", True)]

    settings = test_settings.model_copy(update={"extensions_dir": tmp_path / "leer"})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    await extensions_service.discover_and_sync(db_session, settings)
    record = await _enable(app, db_session, settings, "ext-pipfoo")
    assert record.source == "pip"
    assert type(get_extension_runtime().loaded["ext-pipfoo"].instance).__module__ == module
    await extensions_service.disable_extension(app, db_session, "ext-pipfoo")


def test_a_pip_installed_extension_only_in_the_old_group_is_refused(tmp_path, monkeypatch):
    site = tmp_path / "site" / "altgruppe"
    _install(site, "ext-altgruppe", module_name("altgruppe"), OLD_ENTRY_POINT_GROUP)
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()

    assert discovery.scan_entry_points() == []
    found = discovery.discover_all(tmp_path / "leer")
    assert [(f.id, f.ok, f.refused, f.error) for f in found] == [("ext-altgruppe", False, True, OLD_GROUP_ERROR)]
