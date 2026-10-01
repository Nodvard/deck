"""Alte Paketnamen als Alias der neuen (Umbenennung Lattice -> Nodvard Deck).

Gemeinsamer Unterbau der beiden Uebergangs-Pakete `lattice_sdk` (-> `nodvard_sdk`) und `lattice`
(-> `nodvard_deck`). Absichtlich ein eigenes Modul auf oberster Ebene und **nur Standardbibliothek**:
Das Alias-Paket `lattice` des Kerns darf nichts nachladen, was Pydantic oder das SDK mitbringt
(`python -m lattice.rescue` soll auch dann starten, wenn im Image etwas fehlt).

Ein Alias-Paket ist ein winziges echtes Paket, dessen `__init__.py` `alias_package()` aufruft. Das tut dreierlei:

1. **Eine** DeprecationWarning (Python fuehrt ein Paket nur einmal aus, also nie zweimal).
2. Das Alias-Paket ersetzt sich in `sys.modules` durch das neue Paket: `import lattice_sdk` liefert
   dasselbe Modulobjekt wie `import nodvard_sdk`.
3. Ein `MetaPathFinder` bildet `lattice_sdk.x.y` auf `nodvard_sdk.x.y` ab. Er liefert das **echte** Modul,
   keine Kopie: `lattice_sdk.errors.PermissionDenied is nodvard_sdk.errors.PermissionDenied`, es gibt nur eine
   SQLAlchemy-`Base`, `isinstance` und Registries sehen dieselben Objekte.

Zwei Feinheiten der Importmaschine:

* `importlib` setzt `module.__spec__` beim Anlegen auf den Alias-Spec, auch wenn `create_module` ein fertiges
  Modul liefert. `_AliasLoader.exec_module` stellt den urspruenglichen Spec wieder her (sonst hiesse
  `nodvard_sdk.errors.__spec__.name` ploetzlich `lattice_sdk.errors`, und `reload`/`find_spec` verwirren sich).
* `python -m lattice.migrate` braucht ECHTEN Code: `runpy` holt sich den Code ueber `spec.loader.get_code`.
  Diese Startdateien (`launchers`, zum Beispiel `lattice/migrate.py`) hat der Finder deshalb vorrangig;
  sie stehen mit `__main__`-Schutz im Alias-Ordner und ersetzen sich beim normalen Import selbst durch das
  echte Modul. Sie werden **namentlich** uebergeben, nicht per Ordner-Scan: Liegen im Alias-Ordner alte Dateien
  herum (zum Beispiel nach `tar -x` ueber einen alten Stand, Rueckfallweg in deploy/README.md), landen sie zwar
  im Wheel, werden aber nie geladen -- sonst gaebe es `lattice.config` als zweite Kopie mit eigenen Einstellungen.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import warnings
from pathlib import Path
from types import ModuleType
from typing import Any


class _AliasLoader(importlib.abc.Loader):
    """Liefert das echte Modul unter dem alten Namen."""

    def __init__(self, real_name: str) -> None:
        self._real_name = real_name
        self._real_spec: Any = None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        module = importlib.import_module(self._real_name)
        self._real_spec = module.__spec__
        return module

    def exec_module(self, module: ModuleType) -> None:
        # Nichts auszufuehren (das echte Modul ist fertig). Aber: `module_from_spec` hat `__spec__`
        # gerade auf den Alias-Spec gesetzt -- zurueck auf das Original.
        if self._real_spec is not None:
            module.__spec__ = self._real_spec


class AliasFinder(importlib.abc.MetaPathFinder):
    """Bildet `<alias>.x.y` auf `<real>.x.y` ab. `own`: direkte Untermodule, die als echte Datei im
    Alias-Ordner liegen (Name -> Pfad) und deshalb nicht abgebildet werden."""

    def __init__(self, alias: str, real: str, own: dict[str, Path] | None = None) -> None:
        self.alias = alias
        self.real = real
        self.own = own or {}
        self._prefix = alias + "."

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> importlib.machinery.ModuleSpec | None:
        if not fullname.startswith(self._prefix):
            return None
        rest = fullname[len(self._prefix):]
        own_file = self.own.get(rest)
        if own_file is not None:
            return importlib.util.spec_from_file_location(fullname, own_file)
        real_name = f"{self.real}.{rest}"
        try:
            real_spec = importlib.util.find_spec(real_name)
        except (ImportError, ValueError):  # Elternpaket fehlt: dann gibt es das Modul auch nicht
            return None
        if real_spec is None:
            return None  # normaler "No module named 'lattice_sdk.xyz'"
        return importlib.machinery.ModuleSpec(
            fullname, _AliasLoader(real_name), is_package=real_spec.submodule_search_locations is not None
        )


def alias_package(
    alias: str, real: ModuleType, *, shim_dir: Path | None = None, launchers: tuple[str, ...] = ()
) -> None:
    """Aus der `__init__.py` des Alias-Pakets aufrufen (siehe Modul-Docstring). `launchers`: Namen der Startdateien
    in `shim_dir` (fuer `python -m <alias>.<name>`); jede andere Datei dort wird ignoriert."""
    own: dict[str, Path] = {}
    if shim_dir is not None:
        own = {name: shim_dir / f"{name}.py" for name in launchers if (shim_dir / f"{name}.py").is_file()}
    if not any(isinstance(f, AliasFinder) and f.alias == alias for f in sys.meta_path):
        # Vorn einhaengen: Der Elternpfad des Alias ist der des echten Pakets, der normale Pfad-Finder
        # wuerde dort die echten Dateien ein zweites Mal (als Kopie!) unter dem alten Namen laden.
        sys.meta_path.insert(0, AliasFinder(alias, real.__name__, own))
    sys.modules[alias] = real
    warnings.warn(
        f"Der Paketname '{alias}' ist veraltet, er heisst jetzt '{real.__name__}'. Der alte Name bleibt als Alias"
        f" (dieselben Objekte) mehrere Versionen lang erhalten. Bitte '{real.__name__}' importieren.",
        DeprecationWarning,
        stacklevel=3,  # alias_package -> __init__ des Alias-Pakets -> (Importmaschine, uebersprungen) -> Importeur
    )
