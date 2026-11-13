"""CVE-Tests halten Erweiterungsimporte aus der Testsammlung heraus."""

import runpy
import sys
from pathlib import Path


def test_cve_collection_keeps_import_state():
    path = Path(__file__).with_name("test_ext_shield_cve.py")
    before_path = list(sys.path)
    before_modules = {name for name in sys.modules if name.startswith("nodvard_deck_ext_shield")}
    try:
        runpy.run_path(str(path))
        assert sys.path == before_path
        assert {name for name in sys.modules if name.startswith("nodvard_deck_ext_shield")} == before_modules
    finally:
        sys.path[:] = before_path
        for name in set(sys.modules) - before_modules:
            if name.startswith("nodvard_deck_ext_shield"):
                del sys.modules[name]
