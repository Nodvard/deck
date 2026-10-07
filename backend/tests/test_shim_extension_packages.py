"""Python-Pakete der Extensions nach der Umbenennung (Umbenennung Teil B, PR 6).

Die eingebauten Extensions heissen jetzt `nodvard_deck_ext_<id>` (vorher `lattice_ext_<id>`). Dieser Test haelt fest:

* Jede eingebaute Extension laedt unter dem neuen Paketnamen, ohne dass irgendwo noch ein `lattice_ext_*` entsteht.
* **Fremd-Extensions mit der alten Konvention** (`entrypoint = "lattice_ext_foo:Extension"`, Paket `lattice_ext_foo`)
  laden unveraendert weiter: Der Kern importiert genau das Modul aus `entrypoint`, das Manifest kommt bei jedem Start
  frisch von der Platte, und nirgends wird nach dem Praefix gefiltert. Das gilt fuer den Verzeichnis-Scan und fuer
  Entry-Points (Gruppe `lattice.extensions`), mit alten und mit neuen SDK-Namen.
* **Es gibt bewusst keinen Alias** `lattice_ext_<id>` -> `nodvard_deck_ext_<id>` fuer die eingebauten Extensions (anders
  als `lattice` und `lattice_sdk`): Extensions importieren einander nicht (docs/02, "Nicht am Kontext"), und ein
  Alias, der alle `lattice_ext_*`-Namen abfaengt, wuerde die Pakete von Fremd-Extensions mit der alten Konvention
  gefaehrden. Der Test unten haelt das fest: Ein Fremdpaket `lattice_ext_proxmox` laedt neben der eingebauten Extension
  `proxmox` und ist ein eigenes Modul, keine Kopie und kein Alias von `nodvard_deck_ext_proxmox`.

Diese Datei darf den alten Namen `lattice_ext_` nennen (Ausnahme im Waechter `scripts/check_legacy_names.py`: `test_shim_*`).
"""

from __future__ import annotations

import importlib
import importlib.util
import shutil
import sys
import textwrap
import warnings
from pathlib import Path

import pytest
from fastapi import FastAPI
from nodvard_deck.ext import discovery
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import ExtensionRecord
from nodvard_deck.services import extensions as extensions_service
from nodvard_sdk import NodvardExtension, load_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_EXTENSIONS_DIR = REPO_ROOT / "extensions"
BUILT_IN = sorted(path.parent.name for path in REPO_EXTENSIONS_DIR.glob("*/extension.toml"))

NEW_PREFIX = "nodvard_deck_ext_"
OLD_PREFIX = "lattice_ext_"

_guard_spec = importlib.util.spec_from_file_location("check_legacy_names_script_ext", REPO_ROOT / "scripts" / "check_legacy_names.py")
_guard = importlib.util.module_from_spec(_guard_spec)
sys.modules["check_legacy_names_script_ext"] = _guard  # dataclasses schlagen ihre Klasse hier nach
_guard_spec.loader.exec_module(_guard)


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


# --------------------------------------------------------------------------- die eingebauten Extensions


def test_the_known_built_in_extensions_are_all_there():
    expected = {
        "backups", "documents", "gameserver", "hello-world", "inventory", "network", "nextcloud",
        "ntfy", "proxmox", "scripts", "service-matrix", "shield", "system", "terminal",
    }
    assert expected <= set(BUILT_IN), expected - set(BUILT_IN)


@pytest.mark.parametrize("ext_id", BUILT_IN)
def test_the_manifest_and_the_package_folder_follow_the_new_convention(ext_id):
    manifest = load_manifest(REPO_EXTENSIONS_DIR / ext_id / "extension.toml")
    module = module_name(ext_id)
    assert manifest.id == ext_id
    assert manifest.entrypoint == f"{module}:Extension"
    assert (REPO_EXTENSIONS_DIR / ext_id / "src" / module / "__init__.py").is_file()
    # Nach dem Verschieben liegt unter src/ kein Paket mit dem alten Namen mehr (gezaehlt wird, was Git kennt: ein altes
    # `__pycache__`-Verzeichnis in einer Arbeitskopie, die vom alten Stand kommt, soll den Test nicht rot machen).
    prefix = f"extensions/{ext_id}/src/"
    top = {rel[len(prefix):].split("/")[0] for rel in _guard.tracked_files() if rel.startswith(prefix)}
    assert top == {module}, top


@pytest.mark.asyncio
@pytest.mark.parametrize("ext_id", BUILT_IN)
async def test_every_built_in_extension_loads_under_the_new_package_name(ext_id, db_session, test_settings):
    settings = test_settings.model_copy(update={"extensions_dir": REPO_EXTENSIONS_DIR})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    await extensions_service.discover_and_sync(db_session, settings)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await extensions_service.enable_extension(app, db_session, settings, ext_id)

    record = await db_session.get(ExtensionRecord, ext_id)
    assert record is not None and record.state == "enabled", record.last_error if record else None
    assert record.source == "bundled"

    module = module_name(ext_id)
    instance = get_extension_runtime().loaded[ext_id].instance
    assert isinstance(instance, NodvardExtension)
    assert type(instance).__module__.split(".")[0] == module
    assert Path(sys.modules[module].__file__).resolve() == (REPO_EXTENSIONS_DIR / ext_id / "src" / module / "__init__.py").resolve()
    assert not [name for name in sys.modules if name.startswith(OLD_PREFIX)], "nichts entsteht unter dem alten Namen"
    assert not [w for w in caught if "Paketname" in str(w.message) and "veraltet" in str(w.message)], "keine Alias-Warnung"

    await extensions_service.disable_extension(app, db_session, ext_id)


@pytest.mark.asyncio
async def test_the_old_name_of_a_built_in_extension_is_deliberately_not_an_alias(db_session, test_settings):
    """Siehe Modul-Docstring: kein Alias, also bleibt `import lattice_ext_hello_world` ein ModuleNotFoundError."""
    settings = test_settings.model_copy(update={"extensions_dir": REPO_EXTENSIONS_DIR})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "hello-world")
    assert module_name("hello-world") in sys.modules
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name("hello-world", OLD_PREFIX))
    await extensions_service.disable_extension(app, db_session, "hello-world")


# --------------------------------------------------------------------------- Fremd-Extensions mit der alten Konvention

FOREIGN_OLD_SDK = '''
from lattice_sdk import ExtensionContext, HealthReport, LatticeExtension


class Extension(LatticeExtension):
    where = __file__

    async def setup(self, ctx: ExtensionContext) -> None:
        self.logger_name = ctx.logger.name

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        return HealthReport(healthy=True)
'''

FOREIGN_NEW_SDK = '''
from nodvard_sdk import ExtensionContext, HealthReport, NodvardExtension


class Extension(NodvardExtension):
    where = __file__

    async def setup(self, ctx: ExtensionContext) -> None:
        self.logger_name = ctx.logger.name

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


def _foreign_dir(root: Path, ext_id: str, module: str, code: str) -> None:
    ext_dir = root / ext_id
    (ext_dir / "src" / module).mkdir(parents=True)
    (ext_dir / "extension.toml").write_text(_manifest(ext_id, module), encoding="utf-8")
    (ext_dir / "src" / module / "__init__.py").write_text(code, encoding="utf-8")


async def _enable(app: FastAPI, db_session, settings, ext_id: str) -> ExtensionRecord:
    await extensions_service.enable_extension(app, db_session, settings, ext_id)
    record = await db_session.get(ExtensionRecord, ext_id)
    assert record is not None and record.state == "enabled", record.last_error if record else None
    return record


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore::DeprecationWarning")  # die alten SDK-Namen warnen, das ist hier das erwartete Ergebnis
@pytest.mark.parametrize(
    ("ext_id", "code"), [("fremd-alt", FOREIGN_OLD_SDK), ("fremd-neu", FOREIGN_NEW_SDK)], ids=["altes-sdk", "neues-sdk"]
)
async def test_a_foreign_extension_with_an_old_style_package_name_loads(ext_id, code, db_session, test_settings, tmp_path):
    """`entrypoint = "lattice_ext_foo:Extension"` aus einem Verzeichnis, mit alten und mit neuen SDK-Namen."""
    module = module_name(ext_id.replace("fremd-", "foo_"), OLD_PREFIX)
    assert module.startswith(OLD_PREFIX) and not module.startswith(NEW_PREFIX)
    _foreign_dir(tmp_path / "erweiterungen", ext_id, module, code)

    settings = test_settings.model_copy(update={"extensions_dir": tmp_path / "erweiterungen"})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    await extensions_service.discover_and_sync(db_session, settings)
    discovered = get_extension_runtime().discovered[ext_id]
    assert discovered.ok and discovered.manifest.entrypoint == f"{module}:Extension"

    record = await _enable(app, db_session, settings, ext_id)
    assert record.source == "bundled"
    instance = get_extension_runtime().loaded[ext_id].instance
    assert isinstance(instance, NodvardExtension), "auch eine Extension mit alten SDK-Namen ist eine NodvardExtension"
    assert type(instance).__module__ == module and module in sys.modules
    assert Path(instance.where).resolve() == (tmp_path / "erweiterungen" / ext_id / "src" / module / "__init__.py").resolve()
    assert instance.logger_name == f"nodvard_deck.ext.{ext_id}", "der Logger-Name haengt an der ID, nicht am Paketnamen"
    await extensions_service.disable_extension(app, db_session, ext_id)


@pytest.mark.asyncio
async def test_a_foreign_package_named_like_a_built_in_one_is_its_own_module(db_session, test_settings, tmp_path):
    """Fremdpaket `lattice_ext_proxmox` (id `proxmox-fremd`) neben der eingebauten Extension `proxmox`
    (`nodvard_deck_ext_proxmox`): zwei verschiedene Module, jedes aus seiner Datei. Mit einem Alias, der den alten Namen auf
    die eingebaute Extension abbildet, bekaeme die Fremd-Extension das falsche Modul."""
    root = tmp_path / "erweiterungen"
    shutil.copytree(
        REPO_EXTENSIONS_DIR / "proxmox", root / "proxmox", ignore=shutil.ignore_patterns("frontend", "__pycache__")
    )
    _foreign_dir(root, "proxmox-fremd", module_name("proxmox", OLD_PREFIX), FOREIGN_NEW_SDK)

    settings = test_settings.model_copy(update={"extensions_dir": root})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    await extensions_service.discover_and_sync(db_session, settings)
    await _enable(app, db_session, settings, "proxmox")
    await _enable(app, db_session, settings, "proxmox-fremd")

    new, old = sys.modules[module_name("proxmox")], sys.modules[module_name("proxmox", OLD_PREFIX)]
    assert new is not old
    assert Path(new.__file__).resolve() == (root / "proxmox" / "src" / module_name("proxmox") / "__init__.py").resolve()
    assert Path(old.__file__).resolve() == (root / "proxmox-fremd" / "src" / module_name("proxmox", OLD_PREFIX) / "__init__.py").resolve()
    runtime = get_extension_runtime()
    assert type(runtime.loaded["proxmox"].instance).__module__ == new.__name__
    assert type(runtime.loaded["proxmox-fremd"].instance).__module__ == old.__name__
    for ext_id in ("proxmox", "proxmox-fremd"):
        await extensions_service.disable_extension(app, db_session, ext_id)


def _make_dist(root: Path, dist: str, module: str, ext_id: str, group: str, code: str) -> None:
    """Eine installierte Distribution (nur Metadaten, wie nach `pip install`) mit einem Modul `<module>.py` im Wurzelordner."""
    root.mkdir(parents=True)
    info = root / f"{dist.replace('-', '_')}-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {dist}\nVersion: 1.0\n", encoding="utf-8")
    (info / "entry_points.txt").write_text(f"[{group}]\n{ext_id} = {module}:Extension\n", encoding="utf-8")
    (root / "extension.toml").write_text(_manifest(ext_id, module), encoding="utf-8")
    (root / f"{module}.py").write_text(code, encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_a_pip_installed_foreign_extension_with_an_old_style_package_name_loads(
    db_session, test_settings, tmp_path, monkeypatch
):
    """Entry-Point in der alten Gruppe `lattice.extensions`, Modul `lattice_ext_pipfoo`."""
    module = module_name("pipfoo", OLD_PREFIX)
    site = tmp_path / "site" / "pipfoo"
    _make_dist(site, "ext-pipfoo", module, "ext-pipfoo", discovery.DEFAULT_ENTRY_POINT_GROUPS[1], FOREIGN_OLD_SDK)
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()

    found = discovery.scan_entry_points()
    assert [(f.id, f.source, f.ok) for f in found] == [("ext-pipfoo", "pip", True)]
    assert found[0].manifest.entrypoint == f"{module}:Extension"

    settings = test_settings.model_copy(update={"extensions_dir": tmp_path / "leer"})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    await extensions_service.discover_and_sync(db_session, settings)
    record = await _enable(app, db_session, settings, "ext-pipfoo")
    assert record.source == "pip"
    assert type(get_extension_runtime().loaded["ext-pipfoo"].instance).__module__ == module
    await extensions_service.disable_extension(app, db_session, "ext-pipfoo")
