"""Der alte Name `lattice_sdk` ist ein Alias von `nodvard_sdk` (Umbenennung Lattice -> Nodvard Deck).

Gepruft wird, was fuer Fremd-Extensions zaehlt, die noch `from lattice_sdk import LatticeExtension` schreiben:
**dieselben Objekte** (nicht Kopien: sonst brechen `isinstance`, `except` und Registries), `__spec__` bleibt
das echte, genau **eine** Warnung, beide Importreihenfolgen. Mechanik: `sdk/python/_nodvard_alias.py`.

Diese Datei darf `lattice_sdk` importieren (Ausnahme im Waechter `scripts/check_legacy_names.py`: `test_shim_*`).
"""

from __future__ import annotations

import importlib
import pkgutil
import subprocess
import sys
import textwrap
import warnings

import pytest

import nodvard_sdk

REAL_MODULES = [m.name for m in pkgutil.walk_packages(nodvard_sdk.__path__, "nodvard_sdk.")]


def _old(real_name: str) -> str:
    return "lattice_sdk" + real_name[len("nodvard_sdk"):]


def _import_old(name: str):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return importlib.import_module(name)


def test_the_package_has_the_expected_submodules():
    # Schutz davor, dass `walk_packages` leise nichts findet und alle Tests unten leer laufen.
    assert {"nodvard_sdk.errors", "nodvard_sdk.extension", "nodvard_sdk.capabilities", "nodvard_sdk.widgets"} <= set(REAL_MODULES)


def test_the_old_package_is_the_same_module_object():
    old = _import_old("lattice_sdk")
    assert old is nodvard_sdk
    assert old.__name__ == "nodvard_sdk"
    assert old.__all__ == nodvard_sdk.__all__


@pytest.mark.parametrize("real_name", REAL_MODULES)
def test_every_submodule_is_the_same_object_under_the_old_name(real_name):
    real = importlib.import_module(real_name)
    old = _import_old(_old(real_name))
    assert old is real
    # importlib setzt `__spec__` beim Alias-Import auf den Alias-Spec; der Finder muss es zuruecksetzen.
    assert real.__spec__.name == real_name
    assert real.__name__ == real_name


@pytest.mark.parametrize("real_name", REAL_MODULES)
def test_every_public_name_is_the_same_object_under_the_old_name(real_name):
    real = importlib.import_module(real_name)
    old = _import_old(_old(real_name))
    names = [n for n in vars(real) if not n.startswith("_")]
    assert names, real_name
    for name in names:
        assert getattr(old, name) is getattr(real, name), f"{_old(real_name)}.{name}"


def test_every_name_in_all_is_the_same_object_under_the_old_name():
    old = _import_old("lattice_sdk")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # LatticeExtension/LatticeError warnen absichtlich
        for name in nodvard_sdk.__all__:
            assert getattr(old, name) is getattr(nodvard_sdk, name), name


def test_api_version_is_unchanged():
    assert _import_old("lattice_sdk.version").API_VERSION == nodvard_sdk.API_VERSION == "0.1.0"


def test_an_unknown_submodule_is_a_normal_import_error_with_the_old_name():
    with pytest.raises(ModuleNotFoundError) as info:
        _import_old("lattice_sdk.gibt_es_nicht")
    assert info.value.name == "lattice_sdk.gibt_es_nicht"


def test_the_alias_finder_is_installed_exactly_once():
    _import_old("lattice_sdk")
    finders = [f for f in sys.meta_path if type(f).__name__ == "AliasFinder" and f.alias == "lattice_sdk"]
    assert len(finders) == 1


# --------------------------------------------------------------------------- alte Klassennamen


def test_the_old_class_names_are_the_same_classes_with_a_deprecation_warning():
    with pytest.warns(DeprecationWarning, match="NodvardExtension"):
        old_ext = nodvard_sdk.LatticeExtension
    with pytest.warns(DeprecationWarning, match="NodvardError"):
        old_err = nodvard_sdk.LatticeError
    assert old_ext is nodvard_sdk.NodvardExtension
    assert old_err is nodvard_sdk.NodvardError


@pytest.mark.parametrize(
    ("module", "old", "new"),
    [
        ("nodvard_sdk.extension", "LatticeExtension", "NodvardExtension"),
        ("nodvard_sdk.errors", "LatticeError", "NodvardError"),
        ("lattice_sdk.extension", "LatticeExtension", "NodvardExtension"),
        ("lattice_sdk.errors", "LatticeError", "NodvardError"),
    ],
)
def test_the_old_class_names_also_work_in_the_submodules(module, old, new):
    mod = _import_old(module)
    with pytest.warns(DeprecationWarning):
        old_obj = getattr(mod, old)
    assert old_obj is getattr(mod, new)


def test_the_old_class_names_stay_in_all():
    assert {"LatticeExtension", "LatticeError", "NodvardExtension", "NodvardError"} <= set(nodvard_sdk.__all__)


def test_an_unknown_attribute_is_still_an_attribute_error():
    with pytest.raises(AttributeError, match="gibt_es_nicht"):
        nodvard_sdk.gibt_es_nicht  # noqa: B018
    with pytest.raises(ImportError):
        exec("from nodvard_sdk import gibt_es_nicht")  # noqa: S102


def test_a_foreign_extension_written_against_the_old_sdk_is_a_nodvard_extension():
    old = _import_old("lattice_sdk")
    with pytest.warns(DeprecationWarning):
        base = old.LatticeExtension

    class Fremd(base):  # so schreibt eine Extension von Dritten sie heute
        async def setup(self, ctx):
            return None

    assert issubclass(Fremd, nodvard_sdk.NodvardExtension)
    assert isinstance(Fremd(), nodvard_sdk.NodvardExtension)
    assert nodvard_sdk.NodvardExtension in Fremd.__mro__


def test_errors_raised_by_the_core_are_caught_by_the_old_error_name():
    with pytest.warns(DeprecationWarning):
        old_error = _import_old("lattice_sdk.errors").LatticeError
    for exc in (nodvard_sdk.PermissionDenied("x", "hosts.read"), nodvard_sdk.ActionBlocked("regel"), nodvard_sdk.HostUnreachable("h")):
        with pytest.raises(old_error):
            raise exc


# --------------------------------------------------------------------------- Subprozesse: frischer Interpreter


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-W", "always::DeprecationWarning", "-c", textwrap.dedent(code)],
        capture_output=True, text=True, timeout=120,
    )


def _package_warnings(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stderr.splitlines() if "Paketname 'lattice_sdk'" in line]


IMPORT_ORDERS = {
    "neu zuerst": """
        import nodvard_sdk
        import lattice_sdk.errors
        from lattice_sdk.capabilities import ConsoleTarget
        from lattice_sdk import ExtensionContext
        import lattice_sdk
    """,
    "alt zuerst": """
        import lattice_sdk.errors
        from lattice_sdk.capabilities import ConsoleTarget
        import lattice_sdk
        from lattice_sdk import ExtensionContext
        import nodvard_sdk
    """,
    "nur alt, tiefer Import": """
        from lattice_sdk.widgets import WidgetSpec, StatView
        from lattice_sdk.types import Host
        import lattice_sdk.manifest
        import nodvard_sdk
    """,
    "from-Import des Untermoduls": """
        from lattice_sdk import errors, capabilities
        import nodvard_sdk
    """,
}


@pytest.mark.parametrize("order", IMPORT_ORDERS)
def test_both_import_orders_give_the_same_objects_and_exactly_one_warning(order):
    check = """
        import sys, nodvard_sdk, lattice_sdk, lattice_sdk.errors, lattice_sdk.capabilities, lattice_sdk.widgets
        assert lattice_sdk is nodvard_sdk
        assert sys.modules["lattice_sdk.errors"] is sys.modules["nodvard_sdk.errors"]
        assert lattice_sdk.errors.PermissionDenied is nodvard_sdk.errors.PermissionDenied
        assert lattice_sdk.capabilities.ConsoleTarget is nodvard_sdk.capabilities.ConsoleTarget
        assert lattice_sdk.WidgetSpec is nodvard_sdk.WidgetSpec
        for name, mod in sorted(sys.modules.items()):
            if name.startswith("lattice_sdk"):
                assert mod.__spec__.name.startswith("nodvard_sdk"), (name, mod.__spec__.name)
        print("ok")
    """
    result = _run(textwrap.dedent(IMPORT_ORDERS[order]) + textwrap.dedent(check))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert len(_package_warnings(result)) == 1, result.stderr


def test_the_warning_points_at_the_importing_line():
    result = _run("import lattice_sdk\n")
    (line,) = _package_warnings(result)
    assert line.startswith("<string>:1:"), line  # nicht in importlib oder im Alias-Paket


def test_the_alias_import_does_not_change_the_real_modules_spec_when_walking_everything():
    result = _run(
        """
        import importlib, pkgutil, sys, nodvard_sdk
        for info in pkgutil.walk_packages(nodvard_sdk.__path__, "nodvard_sdk."):
            old = importlib.import_module("lattice_sdk" + info.name[len("nodvard_sdk"):])
            real = importlib.import_module(info.name)
            assert old is real and real.__spec__.name == info.name, info.name
        print("ok")
        """
    )
    assert result.returncode == 0, result.stderr
    assert len(_package_warnings(result)) == 1


def test_python_dash_w_error_turns_the_old_import_into_an_error():
    result = subprocess.run(
        [sys.executable, "-W", "error::DeprecationWarning", "-c", "import lattice_sdk"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode != 0 and "DeprecationWarning" in result.stderr
