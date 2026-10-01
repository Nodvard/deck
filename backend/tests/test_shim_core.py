"""Der alte Name `lattice` ist ein Alias von `nodvard_deck` (Umbenennung Lattice -> Nodvard Deck).

Fremd-Extensions schreiben `from lattice.db.base import IdMixin`, die Hand `uvicorn lattice.main:app` und
`python -m lattice.migrate`. Das muss unveraendert gehen -- mit **denselben Objekten** (nicht Kopien: sonst gaebe es
eine zweite SQLAlchemy-`Base`, "Table already defined", kaputte `isinstance`). Mechanik: `sdk/python/_nodvard_alias.py`.

Diese Datei darf `lattice` importieren (Ausnahme im Waechter `scripts/check_legacy_names.py`: `test_shim_*`).
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import sqlite3
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import nodvard_deck
import pytest
from restore_helpers import REPO_ROOT

# Echte Prozesse (`python -m ...`, frischer Interpreter) wie im Container; unter Windows laufen nur die Identitaetstests.
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="Prozesse wie im Container (POSIX)")

REAL_MODULES = [m.name for m in pkgutil.walk_packages(nodvard_deck.__path__, "nodvard_deck.")]


def _old(real_name: str) -> str:
    return "lattice" + real_name[len("nodvard_deck"):]


def _import_old(name: str):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return importlib.import_module(name)


# --------------------------------------------------------------------------- dieselben Objekte


def test_the_package_has_the_expected_submodules():
    assert {"nodvard_deck.main", "nodvard_deck.db.base", "nodvard_deck.migrate", "nodvard_deck.rescue"} <= set(REAL_MODULES)


def test_the_old_package_is_the_same_module_object():
    old = _import_old("lattice")
    assert old is nodvard_deck
    assert old.__version__ == nodvard_deck.__version__


@pytest.mark.parametrize("real_name", REAL_MODULES)
def test_every_module_is_the_same_object_under_the_old_name(real_name):
    real = importlib.import_module(real_name)
    old = _import_old(_old(real_name))
    assert old is real, real_name
    assert real.__spec__.name == real_name  # der Alias-Finder stellt `__spec__` wieder her
    assert real.__name__ == real_name


@pytest.mark.parametrize("real_name", ["nodvard_deck.db.base", "nodvard_deck.models", "nodvard_deck.config", "nodvard_deck.ext.discovery"])
def test_every_public_name_is_the_same_object_under_the_old_name(real_name):
    real = importlib.import_module(real_name)
    old = _import_old(_old(real_name))
    names = [n for n in vars(real) if not n.startswith("_")]
    assert names
    for name in names:
        assert getattr(old, name) is getattr(real, name), f"{_old(real_name)}.{name}"


def test_what_a_foreign_extension_imports_from_the_core_is_the_real_thing():
    old_base = _import_old("lattice.db.base")
    real_base = importlib.import_module("nodvard_deck.db.base")
    assert old_base.IdMixin is real_base.IdMixin
    assert old_base.UTCDateTime is real_base.UTCDateTime
    assert old_base.utcnow is real_base.utcnow
    assert old_base.Base is real_base.Base
    assert old_base.Base.metadata is real_base.Base.metadata  # nur eine `Base.metadata`
    assert _import_old("lattice.models").Base is importlib.import_module("nodvard_deck.models").Base
    assert _import_old("lattice.models").Base.metadata is real_base.Base.metadata


def test_the_app_is_one_object_so_uvicorn_lattice_main_app_still_works():
    from uvicorn.importer import import_from_string

    real = importlib.import_module("nodvard_deck.main")
    old = _import_old("lattice.main")
    assert old.app is real.app
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert import_from_string("lattice.main:app") is real.app  # so ruft uvicorn es auf
    assert import_from_string("nodvard_deck.main:app") is real.app


def test_there_is_exactly_one_models_registry():
    """Alle Kern-Tabellen stehen genau einmal in der einen Metadata -- auch nach dem Import ueber den alten Namen."""
    models = importlib.import_module("nodvard_deck.models")
    _import_old("lattice.models")
    _import_old("lattice.db.base")
    tables_before = sorted(models.Base.metadata.tables)
    for name in sorted(sys.modules):
        if name.startswith("lattice."):
            assert sys.modules[name].__name__.startswith("nodvard_deck"), name
    assert sorted(models.Base.metadata.tables) == tables_before
    assert "users" in tables_before


def test_the_launcher_modules_are_replaced_by_the_real_module_on_import():
    for name in ("migrate", "boot", "admin", "rescue"):
        old = _import_old(f"lattice.{name}")
        assert old is importlib.import_module(f"nodvard_deck.{name}"), name
        assert old.__spec__.name == f"nodvard_deck.{name}"


# --------------------------------------------------------------------------- frischer Interpreter


def _run(code: str, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-W", "always::DeprecationWarning", "-c", textwrap.dedent(code)],
        capture_output=True, text=True, timeout=180, **kwargs,
    )


def _package_warnings(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stderr.splitlines() if "Paketname 'lattice'" in line]


IMPORT_ORDERS = {
    "neu zuerst": "import nodvard_deck.main\nimport lattice.db.base\nfrom lattice.models import Base\nimport lattice.main\nimport lattice",
    "alt zuerst": "import lattice.db.base\nfrom lattice.models import Base\nimport lattice.main\nimport lattice\nimport nodvard_deck.main",
    "nur alt, tief": "from lattice.core.backup import format as fmt\nfrom lattice.services import auth\nfrom lattice.api.v1 import auth as api_auth\nimport nodvard_deck.db.base",
}


@posix_only
@pytest.mark.parametrize("order", IMPORT_ORDERS)
def test_both_import_orders_give_the_same_objects_and_exactly_one_warning(order):
    check = """
    import sys, nodvard_deck.db.base, nodvard_deck.models, nodvard_deck.main, lattice.db.base, lattice.models, lattice.main
    assert lattice.db.base.IdMixin is nodvard_deck.db.base.IdMixin
    assert lattice.main.app is nodvard_deck.main.app
    assert lattice.models.Base.metadata is nodvard_deck.db.base.Base.metadata
    for name, mod in sorted(sys.modules.items()):
        if name.startswith("lattice."):
            assert mod.__spec__.name == "nodvard_deck" + name[len("lattice"):], (name, mod.__spec__.name)
    print("ok")
    """
    result = _run(IMPORT_ORDERS[order] + "\n" + textwrap.dedent(check))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert len(_package_warnings(result)) == 1, result.stderr


@posix_only
def test_stray_old_files_in_the_alias_folder_are_never_loaded(tmp_path):
    """Rueckfallweg (deploy/README.md): `tar -x` ueber den alten Stand loescht nichts, alte Dateien wie
    `lattice/config.py` liegen dann neben dem Alias und kommen mit ins Wheel. Nur die vier Startdateien duerfen
    geladen werden -- eine alte `config.py` als `lattice.config` waere eine zweite Kopie mit eigenen Einstellungen."""
    alias = tmp_path / "lattice"
    alias.mkdir()
    for name in ("__init__", "migrate", "boot", "admin", "rescue"):
        source = REPO_ROOT / "backend" / "src" / "lattice" / f"{name}.py"
        (alias / f"{name}.py").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    for name in ("config", "main", "version"):
        (alias / f"{name}.py").write_text("raise RuntimeError('alte Datei geladen')\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in (str(tmp_path), os.environ.get("PYTHONPATH")) if p)}
    result = _run(
        """
        import sys
        import lattice
        assert sys.modules["lattice"] is sys.modules["nodvard_deck"]
        import lattice.config, lattice.main, lattice.version, lattice.migrate
        for name in ("config", "main", "version", "migrate"):
            assert sys.modules["lattice." + name] is sys.modules["nodvard_deck." + name], name
        print("ok")
        """,
        cwd=tmp_path, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


@posix_only
def test_the_warning_points_at_the_importing_line():
    (line,) = _package_warnings(_run("import lattice\n"))
    assert line.startswith("<string>:1:"), line


@posix_only
def test_python_dash_w_error_turns_the_old_import_into_an_error():
    result = subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", "import lattice"], capture_output=True, text=True, timeout=60)
    assert result.returncode != 0 and "DeprecationWarning" in result.stderr


@posix_only
def test_the_old_rescue_page_still_needs_nothing_but_the_standard_library():
    """`python -m lattice.rescue` ist die Notseite fuer den Fall, dass im Image etwas fehlt: der Alias-Weg darf
    daran nichts aendern (kein pydantic, kein SDK, nichts aus dem Kern)."""
    result = _run(
        """
        import sys
        import lattice.rescue as old
        import nodvard_deck.rescue as real
        assert old is real
        heavy = {"pydantic", "nodvard_sdk", "lattice_sdk", "sqlalchemy", "fastapi", "alembic"} & set(sys.modules)
        assert not heavy, heavy
        print("ok")
        """
    )
    assert result.returncode == 0, result.stderr
    assert len(_package_warnings(result)) == 1


@posix_only
def test_the_old_rescue_page_starts_with_the_old_command():
    old = subprocess.run([sys.executable, "-m", "lattice.rescue", "--help"], capture_output=True, text=True, timeout=60)
    new = subprocess.run([sys.executable, "-m", "nodvard_deck.rescue", "--help"], capture_output=True, text=True, timeout=60)
    assert old.returncode == new.returncode == 0, old.stderr + new.stderr
    assert old.stdout == new.stdout and "--port" in old.stdout


# --------------------------------------------------------------------------- python -m ...


def _env(tmp_path: Path) -> dict[str, str]:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NODVARD_DECK_", "LATTICE_", "PYTHONPATH"))}
    env.update({
        "NODVARD_DECK_ENV": "dev",
        "NODVARD_DECK_DATA_DIR": str(data),
        "NODVARD_DECK_DATABASE_URL": f"sqlite+aiosqlite:///{data / 'lattice.db'}",
        "NODVARD_DECK_MASTER_KEY_PATH": str(data / "master.key"),
        "NODVARD_DECK_VAULT_KEYRING_PATH": str(data / "vault_keyring.json"),
        "NODVARD_DECK_JWT_SECRET_PATH": str(data / "jwt_secret.key"),
        "NODVARD_DECK_EXT_DATA_DIR": str(data / "ext"),
        "NODVARD_DECK_EXTENSIONS_DIR": str(tmp_path / "erweiterungen"),
        "PYTHONUNBUFFERED": "1",
    })
    return env


def _versions(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return sorted(row[0] for row in conn.execute("SELECT version_num FROM alembic_version"))
    finally:
        conn.close()


@posix_only
@pytest.mark.parametrize("module", ["nodvard_deck.migrate", "lattice.migrate"])
def test_python_dash_m_migrate_works_under_both_names(module, tmp_path):
    env = _env(tmp_path)
    result = subprocess.run([sys.executable, "-m", module], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=240)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Migrations-Verzeichnis" in result.stdout
    db = tmp_path / "data" / "lattice.db"
    assert db.is_file() and _versions(db), "die Migration hat die Tabellen und alembic_version angelegt"
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM users").fetchone() == (0,)
    finally:
        conn.close()


@posix_only
def test_both_names_migrate_to_the_same_alembic_heads(tmp_path):
    """Kein neuer Alembic-Stand durch die Umbenennung: der alte und der neue Aufruf enden auf denselben Staenden."""
    heads = []
    for module in ("lattice.migrate", "nodvard_deck.migrate"):
        root = tmp_path / module
        root.mkdir()
        result = subprocess.run([sys.executable, "-m", module], cwd=REPO_ROOT, env=_env(root), capture_output=True, text=True, timeout=240)
        assert result.returncode == 0, result.stderr
        heads.append(_versions(root / "data" / "lattice.db"))
    assert heads[0] == heads[1] and heads[0]


@posix_only
def test_python_dash_m_boot_and_admin_work_under_the_old_name(tmp_path):
    env = _env(tmp_path)
    boot = subprocess.run([sys.executable, "-m", "lattice.boot"], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=240)
    assert boot.returncode == 0, boot.stdout + boot.stderr
    assert _versions(tmp_path / "data" / "lattice.db")
    # `--help` fasst den Datenordner nicht an (die Anmeldung als root gegen einen Ordner von root bricht `admin` sonst ab).
    outputs = []
    for module in ("lattice.admin", "nodvard_deck.admin"):
        result = subprocess.run([sys.executable, "-m", module, "--help"], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1] and "list-users" in outputs[0]


@posix_only
def test_a_missing_launcher_name_is_a_normal_error():
    result = subprocess.run([sys.executable, "-m", "lattice.gibt_es_nicht"], capture_output=True, text=True, timeout=60)
    assert result.returncode != 0 and "No module named" in result.stderr
