"""Der Waechter `scripts/check_legacy_names.py`: der alte Name `lattice` kommt nicht zurueck.

Zwei Teile: (1) das Repository selbst ist sauber (so laeuft der Waechter in der CI mit, ohne Eintrag unter `.github/`),
(2) die Regeln des Waechters werden an Beispielzeilen geprueft, damit er nicht still nichts mehr findet.

Die Beispiele mit dem alten Namen stehen als Text in dieser Datei; sie ist vom Waechter ausgenommen
(`scripts/check_legacy_names.py`, `EXEMPT_PREFIXES`).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_legacy_names.py"

_spec = importlib.util.spec_from_file_location("check_legacy_names_script", SCRIPT)
guard = importlib.util.module_from_spec(_spec)
sys.modules["check_legacy_names_script"] = guard  # dataclasses schlagen ihre Klasse hier nach
_spec.loader.exec_module(guard)


def kinds(text: str, rel: str = "backend/src/nodvard_deck/x.py") -> list[str]:
    return [f.rule for f in guard.check_text(rel, text)]


# --------------------------------------------------------------------------- das Repository


def test_the_repository_does_not_use_the_old_name():
    findings = guard.find_violations()
    assert not findings, "\n" + "\n".join(f"  {f}" for f in findings)


def test_the_script_exits_with_zero_on_the_repository():
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("OK")


def test_the_alias_folders_hold_only_the_alias_files():
    """In die Alias-Ordner gehoert nichts Neues: ein Aenderungsprotokoll-Eintrag, der aus einem aelteren Zweig im alten Pfad
    landet (`backend/src/lattice/changelog/unreleased/...`), wuerde still weder gelesen noch ins Wheel gepackt.

    Gezaehlt werden die Dateien, die Git kennt (versioniert oder neu), nicht der Ordnerinhalt: Nach dem Umstieg bleiben
    in einer alten Arbeitskopie leere Ordner wie `backend/src/lattice/db/__pycache__/` stehen (Git loescht keine
    ignorierten Dateien) -- die gehoeren zu niemandes Aenderung und sollen den Test nicht rot machen."""
    files = guard.tracked_files()

    def names(folder: str) -> set[str]:
        prefix = folder + "/"
        return {rel[len(prefix):].split("/")[0] for rel in files if rel.startswith(prefix)}

    assert names("backend/src/lattice") == {"__init__.py", "migrate.py", "boot.py", "admin.py", "rescue.py"}, (
        "neue Dateien gehoeren nach backend/src/nodvard_deck/ (Aenderungsprotokoll: backend/src/nodvard_deck/changelog/unreleased/)"
    )
    assert names("sdk/python/lattice_sdk") == {"__init__.py"}, "neue SDK-Dateien gehoeren nach sdk/python/nodvard_sdk/"


def test_every_entry_of_the_kept_list_is_still_needed():
    """Wird ein Name umbenannt, muss sein Eintrag aus der Positivliste fliegen -- sonst bliebe sie ein Freibrief."""
    used = {name for item in guard.list_kept() for name in item.kept.split(", ")}
    unused = [k.name for k in guard.KEPT_NAMES if k.name not in used]
    assert not unused, f"nicht mehr gebraucht, bitte aus KEPT_NAMES streichen: {unused}"


def test_every_entry_of_the_kept_list_has_a_reason():
    assert all(k.reason.strip() and len(k.reason) > 40 for k in guard.KEPT_NAMES)


# --------------------------------------------------------------------------- Regel 1: Imports und Modulpfade


@pytest.mark.parametrize(
    "line",
    [
        "from lattice import config",
        "from lattice.db.base import IdMixin",
        "import lattice",
        "import lattice.core.gate",
        "    from lattice.services import auth  # eingerueckt, in einer Funktion",
        "import lattice_sdk",
        "from lattice_sdk import ExtensionContext",
        "from lattice_sdk.errors import PermissionDenied",
        # nicht am Zeilenanfang: hinter `;` und in Anfuehrungszeichen (Shell-Skripte mit `python -c`)
        "x = 1; import lattice",
        'python -c "import lattice.migrate"',
        "docker compose exec x python -c 'from lattice_sdk import API_VERSION'",
    ],
)
def test_imports_of_the_old_packages_are_found(line):
    assert kinds(line) == ["import"]


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ('monkeypatch.setattr("lattice.db.session.session_scope", fake)', "modul"),
        ('patch("lattice.core.gate.run")', "modul"),
        ('CMD ["uvicorn", "lattice.main:app", "--host", "0.0.0.0"]', "modul"),
        ("python -m lattice.migrate", "modul"),
        ("docker compose exec x python -m lattice.admin list-users", "modul"),
        ("pip install -e sdk/python  # liefert lattice_sdk", "modul"),
        ("siehe backend/src/lattice/ext/discovery.py", "modul"),
        ("Pfad sdk/python/lattice_sdk/widgets.py", "modul"),
        ('sys.modules["lattice.models"]', "modul"),
        # Logger heissen jetzt nodvard_deck.*: ein Logger mit dem alten Namen ist ein harter Fund.
        ('logger = logging.getLogger("lattice.files")', "modul"),
        # der Paketname allein als Text, und zusammengesetzte Modulpfade (f-String, +, %)
        ('logger = logging.getLogger("lattice")', "modul"),
        ('log = structlog.get_logger("lattice")', "modul"),
        ('importlib.import_module("lattice")', "modul"),
        ('__import__("lattice")', "modul"),
        ('importlib.util.find_spec("lattice")', "modul"),
        ('importlib.import_module(f"lattice.{name}")', "modul"),
        ('importlib.import_module("lattice." + name)', "modul"),
        ('module = "lattice.%s" % name', "modul"),
        ('exec python -m "lattice.$cmd" "$@"', "modul"),
        ("exec python -m 'lattice' --help", "modul"),
        ('patch("lattice.db.engine.create")', "modul"),
    ],
)
def test_module_paths_as_text_are_found(line, rule):
    assert kinds(line) == [rule], line


@pytest.mark.parametrize(
    "line",
    [
        # Die Pakete der eingebauten Extensions heissen nodvard_deck_ext_<id>; die alte Schreibweise ist ein harter Fund.
        "from lattice_ext_scripts.repo import ScriptRepo",
        "    import lattice_ext_proxmox",
        'entrypoint = "lattice_ext_proxmox:Extension"',
        'module = importlib.import_module("lattice_ext_hello_world")',
        'monkeypatch.setattr("lattice_ext_nexus_soc.patching.REACH_GRACE_S", 0)',
        'npm = sys.modules["lattice_ext_network.npm"]',
        'src = tmp_path / "src" / "lattice_ext_flaky"',
        "siehe extensions/proxmox/src/lattice_ext_proxmox/config.py",
    ],
)
def test_the_old_package_names_of_the_built_in_extensions_are_found(line):
    assert kinds(line) == ["modul"], line
    assert kinds(line, rel="backend/tests/test_ext_x.py") == ["modul"], "auch in den Tests, die Positivliste gilt hier nicht"


@pytest.mark.parametrize(
    "line",
    [
        'entrypoint = "nodvard_deck_ext_proxmox:Extension"',
        "from nodvard_deck_ext_scripts.repo import ScriptRepo",
        'module = importlib.import_module("nodvard_deck_ext_hello_world")',
        "src = tmp_path / 'src' / 'nodvard_deck_ext_flaky'",
    ],
)
def test_the_new_package_names_of_the_extensions_are_fine(line):
    assert kinds(line) == [], line


def test_only_the_bare_old_prefix_may_stay_and_only_in_the_tests():
    """`startswith(("nodvard_deck_ext_", "lattice_ext_"))` raeumt in den Tests `sys.modules` auf (alte und neue Konvention)."""
    cleanup = 'if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):'
    assert kinds(cleanup, rel="backend/tests/test_ext_ntfy.py") == []
    assert kinds("if name.startswith('lattice_ext_'):", rel="backend/tests/test_ext_ntfy.py") == []
    assert kinds(cleanup, rel="backend/src/nodvard_deck/services/extensions.py") == ["name"], "im Programmcode nicht"
    assert kinds(cleanup, rel="extensions/proxmox/src/nodvard_deck_ext_proxmox/__init__.py") == ["name"]
    # mit einem Namen dahinter ist es schon ein konkretes altes Paket
    assert kinds('if name.startswith("lattice_ext_proxmox"):', rel="backend/tests/test_ext_ntfy.py") == ["modul"]


@pytest.mark.parametrize("line", ["class Fremd(LatticeExtension):", "except LatticeError:", "from x import LatticeError"])
def test_the_old_class_names_are_found(line):
    assert "klasse" in kinds(line) or "import" in kinds(line)


def test_the_alias_packages_and_their_tests_may_use_the_old_names():
    text = "from lattice_sdk import LatticeExtension\nimport lattice.db.base\n"
    for rel in (
        "backend/src/lattice/__init__.py",
        "sdk/python/lattice_sdk/__init__.py",
        "sdk/python/_nodvard_alias.py",
        "sdk/python/tests/test_shim_sdk.py",
        "backend/tests/test_shim_core.py",
        "backend/tests/test_shim_anything.py",
    ):
        assert guard.is_exempt(rel), rel
    assert guard.find_violations(REPO_ROOT, files=[]) == []
    assert not guard.is_exempt("backend/tests/test_something_else.py")
    assert not guard.is_exempt("backend/src/nodvard_deck/main.py")
    assert text  # (die Pruefung des Inhalts ist is_exempt; Alias-Dateien werden gar nicht gelesen)


def test_a_new_file_with_an_old_import_is_a_finding(tmp_path):
    (tmp_path / "backend" / "src" / "nodvard_deck").mkdir(parents=True)
    (tmp_path / "extensions" / "x" / "src").mkdir(parents=True)
    bad = tmp_path / "extensions" / "x" / "src" / "ext.py"
    bad.write_text("from lattice_sdk import LatticeExtension\nfrom lattice.db.base import IdMixin\n", encoding="utf-8")
    ok = tmp_path / "extensions" / "x" / "src" / "fine.py"
    ok.write_text("from nodvard_sdk import NodvardExtension\nfrom nodvard_deck.db.base import IdMixin\n", encoding="utf-8")
    found = guard.find_violations(tmp_path, files=["extensions/x/src/ext.py", "extensions/x/src/fine.py"])
    assert [(f.path, f.line, f.rule) for f in found] == [("extensions/x/src/ext.py", 1, "import"), ("extensions/x/src/ext.py", 2, "import")]


# --------------------------------------------------------------------------- Regel 2: die Positivliste


@pytest.mark.parametrize(
    "line",
    [
        # Daten
        'database_url: str = "sqlite+aiosqlite:///./data/lattice.db"',
        'ARC_DB = "db/lattice.db"',
        'reverting = data_dir / "lattice.db.reverting"',
        "- lattice_data:/app/data",
        "name: deploy_lattice_data",
        # Umgebungsvariablen (Rueckfall)
        'LEGACY_ENV_PREFIX = "LATTICE_"',
        "export LATTICE_DNS_1=1.1.1.1",
        # Cookie (Rueckfall neben `nodvard_deck_refresh`)
        'LEGACY_COOKIE_NAME = "lattice_refresh"',
        'COOKIE_NAMES = ("nodvard_deck_refresh", "lattice_refresh")',
        'client.cookies.set("lattice_refresh", value)',
        # Frontend-Vertrag
        "const shell = window.__lattice;",
        'window.dispatchEvent(new Event("lattice:navigate"))',
        ".lattice-focus { outline: 2px solid; }",
        'const url = "/lattice-shim/react.js";',
        'localStorage.getItem("lattice.changelogSeen")',
        # Namen auf fremden Rechnern und im Container
        "RUN groupadd --gid 1000 lattice && useradd --uid 1000 --gid lattice lattice",
        "chown -R lattice:lattice /app",
        "ssh-ed25519 AAAA lattice@server1",
        'username: str = "lattice"',
        "systemd-run --unit=lattice-upgrade-upd_1 --collect",
        "lattice-rollback/web:previous",
        "~/.local/state/lattice-image-updates/run.log",
        "docker stop deploy-lattice-1",
        "docker tag lattice:latest nodvard-deck:previous",
        "DEPLOY_ROOT=lattice-deploy-test",
        'source: Literal["provider", "lattice"]',
        'DENY_DASHBOARD = (frozenset({"nodvard"}), ("lattice", "nodvard-deck"))',
        "(\"lattice.service\", REFUSAL_DASHBOARD)",
        'DEFAULT_ENTRY_POINT_GROUPS = ("nodvard_deck.extensions", "lattice.extensions")',
    ],
)
def test_names_that_stay_are_allowed(line):
    assert kinds(line) == [], line


@pytest.mark.parametrize(
    "line",
    [
        "settings = lattice_settings()",
        "lattice-newthing = 3",
        'path = "/var/lib/lattice-data"',
        "x = LATTICE",
        "from_lattice_to_nodvard = 1",
        'token = request.cookies.get("lattice_session")',
        "docker run lattice/extra:1",
    ],
)
def test_a_new_use_of_the_old_name_is_found(line):
    assert kinds(line) == ["name"], line


def test_the_old_sudo_rule_name_is_only_allowed_where_the_setup_command_replaces_it():
    line = 'O="/etc/sudoers.d/lattice-$U"'
    assert kinds(line, rel="backend/src/nodvard_deck/services/host_setup.py") == []
    assert kinds("assert sorted(names) == ['lattice-lattice']", rel="backend/tests/test_host_setup_script.py") == []
    assert kinds(line) == ["name"], "neuer Code legt keine sudo-Regel unter dem alten Namen an"
    assert kinds('install -m 0440 "$T" "/etc/sudoers.d/lattice-$U"', rel="extensions/system/src/x.py") == ["name"]


def test_capitalised_prose_is_not_checked():
    assert kinds("# Lattice startet nicht, wenn die Datenbank fehlt.") == []
    assert kinds("Lattice-Hosts werden verwaltet") == []


def test_documentation_published_changelogs_and_bundles_are_not_checked():
    for rel in (
        "docs/02-EXTENSION-API.md",
        "README.md",
        "CONTRIBUTING.md",
        "backend/src/nodvard_deck/changelog/versions/0.5.0.toml",
        "extensions/proxmox/frontend/dist/index.js",
        "frontend/package-lock.json",
        "backend/tests/contract/sdk_public.json",
        ".github/workflows/ci.yml",
    ):
        assert guard.is_exempt(rel), rel
    assert not guard.is_exempt("backend/src/nodvard_deck/changelog/unreleased/feat-x.toml")
    assert not guard.is_exempt("extensions/proxmox/frontend/src/ProxmoxPage.tsx")
