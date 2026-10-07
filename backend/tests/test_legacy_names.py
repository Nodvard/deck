"""Der Waechter `scripts/check_legacy_names.py`: der alte Name `lattice` und die alte Shield-Kennung `nexus-soc` kommen nicht zurueck.

Zwei Teile: (1) das Repository selbst ist sauber (so laeuft der Waechter in der CI mit, ohne Eintrag unter `.github/`),
(2) die Regeln des Waechters werden an Beispielzeilen geprueft, damit er nicht still nichts mehr findet. Regel 1 und 2 gelten
dem Namen `lattice`, Regel 3 (Abschnitt am Ende) der alten Kennung von Nodvard Shield.

Die Beispiele mit dem alten Namen stehen als Text in dieser Datei; sie ist vom Waechter ausgenommen
(`scripts/check_legacy_names.py`, `EXEMPT_PREFIXES`).
"""

from __future__ import annotations

import importlib.util
import re
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


# --------------------------------------------------------------------------- Regel 3: die alte Shield-Kennung


def shield_kinds(text: str, rel: str = "backend/src/nodvard_deck/x.py") -> list[str]:
    return [f.rule for f in guard.check_shield_text(rel, text)]


def shield_findings() -> list:
    return [f for f in guard.find_violations() if f.rule.startswith("shield-")]


def test_the_repository_does_not_use_the_old_shield_name():
    findings = shield_findings()
    assert not findings, "\n" + "\n".join(f"  {f}" for f in findings)


@pytest.mark.parametrize(
    "line",
    [
        "from nodvard_deck_ext_nexus_soc import ids",
        'entrypoint = "nodvard_deck_ext_nexus_soc:Extension"',
        'monkeypatch.setattr("nodvard_deck_ext_nexus_soc.patching.REACH_GRACE_S", 0)',
        'src = tmp_path / "src" / "nodvard_deck_ext_nexus_soc"',
        "siehe extensions/shield/src/nodvard_deck_ext_nexus_soc/ids.py",
    ],
)
def test_the_old_package_name_of_shield_is_found_everywhere(line):
    """Hart: auch in den Alias-Tests und an Stellen, die sonst auf der Positivliste stuenden."""
    for rel in (
        "backend/src/nodvard_deck/x.py",
        "extensions/shield/src/nodvard_deck_ext_shield/ids.py",
        "backend/tests/test_ext_shield.py",
        "backend/tests/test_shield_legacy_ids.py",
        "extensions/shield/extension.toml",
    ):
        assert shield_kinds(line, rel) == ["shield-paket"], (line, rel)


@pytest.mark.parametrize(
    "line",
    [
        'const url = "/api/v1/ext/nexus-soc/defender/findings";',
        'renderAt(["/ext/nexus-soc/soc?tab=guard#oben"]);',
        'await client.put("/api/v1/extensions/nexus-soc/settings", json={})',
        'vi.mock("/api/v1/extensions/nexus-soc/frontend/index.js?v=dev", () => ({}));',
        "href: `/ext/nexus-soc/soc`",
    ],
)
def test_the_old_addresses_stand_only_in_alias_tests(line):
    for rel in (
        "backend/src/nodvard_deck/services/x.py",
        "extensions/shield/src/nodvard_deck_ext_shield/__init__.py",
        "extensions/shield/frontend/src/SocPage.tsx",
        "frontend/src/routes/RequireAuth.test.tsx",  # ein Test, aber nicht der Alias-Tests
        "backend/tests/test_ext_shield.py",
        "backend/tests/legacy_helpers.py",  # beginnt nicht mit test_
        "backend/src/nodvard_deck/services/test_x_legacy.py",  # Name wie ein Alias-Test, aber nicht unter tests/
    ):
        assert shield_kinds(line, rel) == ["shield-adresse"], (line, rel)
    for rel in (
        "backend/tests/test_shield_legacy_ids.py",
        "backend/tests/test_extensions_legacy_ids.py",
        "backend/tests/test_legacy.py",
        "sdk/python/tests/test_contract_legacy.py",
        "frontend/src/routes/ExtensionPageLegacy.test.tsx",
        "frontend/src/routes/settings/AuditSettingsLegacy.test.tsx",
        "frontend/src/routes/legacyRedirect.test.ts",
        "extensions/shield/frontend/src/legacy.test.tsx",
    ):
        assert shield_kinds(line, rel) == [], (line, rel)


def test_an_address_with_more_after_the_name_is_not_the_old_address():
    """`/ext/nexus-soc-x` ist keine alte Adresse, aber der Name ist trotzdem nicht erlaubt (Positivliste)."""
    assert shield_kinds('x = "/ext/nexus-soc-neu/soc"', "backend/tests/test_ext_shield.py") == ["shield-name"]


@pytest.mark.parametrize(
    "line",
    [
        'EXT_ID = "nexus-soc"',
        'ext_id="nexus-soc"',
        'QUARANTINE = "/var/lib/nexus-scratch"',
        'action = "nexus_soc.scan"',
        'action = f"nexus_soc.{name}"',
        "x = nexus_soc",
        'TABLE = "ext_nexus_soc_neu"',
        "log = getLogger('nodvard_deck.ext.nexus-soc')",
        "NEXUS_SOC = 1",
        'x = "nexus"',
        'DENY = ("nexus", "proxmox")',
        "from nodvard_deck_ext_shield.nexus_helper import x",
    ],
)
def test_a_new_use_of_the_old_shield_name_is_found(line):
    assert shield_kinds(line) == ["shield-name"], line
    assert shield_kinds(line, "backend/tests/test_ext_system.py") == ["shield-name"], line


@pytest.mark.parametrize(
    ("line", "rel"),
    [
        # Tabellen und Indizes
        ('__tablename__ = "ext_nexus_soc_scans"', "extensions/shield/src/nodvard_deck_ext_shield/models.py"),
        ("Index('ix_ext_nexus_soc_scans_host_id', 'host_id')", "extensions/shield/src/nodvard_deck_ext_shield/models.py"),
        ('text("SELECT * FROM ext_nexus_soc_incident_queue")', "backend/tests/test_ext_shield.py"),
        ('assert "ext_nexus_soc_incidents" in tables', "backend/tests/test_migrate.py"),
        # Aktionsarten
        ('ACTION_PREFIX = "nexus_soc"', "extensions/shield/src/nodvard_deck_ext_shield/ids.py"),
        ('assert ids.ACTION_PREFIX == "nexus_soc"', "backend/tests/test_shield_legacy_ids.py"),
        ('res = await soc.executor.execute(_req("nexus_soc.upgrade", payload))', "backend/tests/test_ext_shield_actions.py"),
        ('expect(screen.queryByText(/nexus_soc\\.upgrade/)).toBeNull();', "frontend/src/routes/ActionsPage.test.tsx"),
        ('const BASE = { ext_id: "shield", action_type: "nexus_soc.upgrade" };', "frontend/src/lib/actionNames.test.ts"),
        # alte Audit-Namen: nur die Beschriftung kennt sie
        ('"nexus_soc.incident_status": LABEL_STATUS,', "extensions/shield/frontend/src/ContainerWatch.tsx"),
        ("expect(auditActionLabel(`nexus_soc.${name}`)).toBe(label);", "extensions/shield/frontend/src/ContainerWatch.test.tsx"),
        # Geheimnis
        ('_TOKEN_LABEL = "nexus-soc-ollama-key"', "extensions/shield/src/nodvard_deck_ext_shield/ollama.py"),
        ('  "secrets.read:nexus-soc-*",', "extensions/shield/extension.toml"),
        ('"label": "nexus-soc-ollama-key",', "extensions/shield/settings.schema.json"),
        # Ordner auf den Servern
        ('QUARANTINE_DIR = "/var/lib/nexus-quarantine"', "extensions/shield/src/nodvard_deck_ext_shield/antivirus.py"),
        ('JOB_DIR = "/var/lib/nexus-updates"', "extensions/shield/src/nodvard_deck_ext_shield/detached.py"),
        ("expect(screen.queryByText(/\\/var\\/lib\\/nexus-quarantine/)).toBeNull();", "extensions/shield/frontend/src/SocPage.test.tsx"),
        # alte feste Scan-Marke
        ('old = "/tmp/x@@nexus-rc=0: Win.Test FOUND"', "backend/tests/test_ext_shield_antivirus.py"),
        # Manifest, Herkunft, Alembic
        ('legacy_ids  = ["nexus-soc"]', "extensions/shield/extension.toml"),
        ("Die Erweiterung hiess bis 0.6 `nexus-soc` (`legacy_ids` im Manifest).", "extensions/shield/src/nodvard_deck_ext_shield/ids.py"),
        ("/** Kennung von Nodvard Shield (bis 0.6 `nexus-soc`). */", "extensions/shield/frontend/src/ids.ts"),
        ('assert labels == {"documents", "inventory", "nexus-soc"}', "backend/tests/contract/test_alembic_revisions.py"),
        ('VERSCHOBEN = {"extensions/nexus-soc/": "extensions/shield/"}', "backend/tests/contract/test_alembic_revisions.py"),
        # alte Kennung als Wert in den Tests
        ('{ id: "shield", legacy_ids: ["nexus-soc"], state: "enabled" }', "frontend/src/lib/extensionNames.test.ts"),
        ('rows[1] = { ...rows[1], source_ext_id: "nexus-soc" };', "frontend/src/components/NotificationBell.test.tsx"),
        ('record = ExtensionRecord(id="nexus-soc", state="enabled")', "backend/tests/test_shield_legacy_ids.py"),
        ('record = ExtensionRecord(id="nexus-soc", state="enabled")', "backend/tests/test_actor_labels_legacy_ids.py"),
        # Herkunft
        ('"reference/nexus/hub.py": "print(1)"', "backend/tests/test_export_public.py"),
    ],
)
def test_shield_names_that_stay_are_allowed_where_they_are_listed(line, rel):
    assert shield_kinds(line, rel) == [], (line, rel)


@pytest.mark.parametrize(
    ("line", "listed_in", "elsewhere"),
    [
        ('__tablename__ = "ext_nexus_soc_scans"', "extensions/shield/src/nodvard_deck_ext_shield/models.py", "extensions/system/src/nodvard_deck_ext_system/x.py"),
        ('ACTION_PREFIX = "nexus_soc"', "extensions/shield/src/nodvard_deck_ext_shield/ids.py", "extensions/shield/src/nodvard_deck_ext_shield/__init__.py"),
        ('a = "nexus_soc.upgrade"', "backend/tests/test_ext_shield_actions.py", "backend/tests/test_core_gate.py"),
        ('a = "nexus_soc.upgrade"', "backend/tests/test_shield_legacy_ids.py", "backend/src/nodvard_deck/core/gate.py"),
        ('"nexus_soc.incident": L,', "extensions/shield/frontend/src/ContainerWatch.tsx", "extensions/shield/frontend/src/SocPage.tsx"),
        ('_TOKEN_LABEL = "nexus-soc-ollama-key"', "extensions/shield/src/nodvard_deck_ext_shield/ollama.py", "extensions/system/src/nodvard_deck_ext_system/x.py"),
        ('JOB_DIR = "/var/lib/nexus-updates"', "extensions/shield/src/nodvard_deck_ext_shield/detached.py", "extensions/shield/src/nodvard_deck_ext_shield/updates.py"),
        ('QUARANTINE_DIR = "/var/lib/nexus-quarantine"', "extensions/shield/src/nodvard_deck_ext_shield/antivirus.py", "extensions/system/src/nodvard_deck_ext_system/x.py"),
        ('legacy_ids = ["nexus-soc"]', "extensions/shield/extension.toml", "extensions/proxmox/extension.toml"),
        ('x = "@@nexus-rc=0"', "backend/tests/test_ext_shield_antivirus.py", "extensions/shield/src/nodvard_deck_ext_shield/antivirus.py"),
        ('labels = {"nexus-soc"}', "backend/tests/test_migrate.py", "backend/tests/test_hosts_api.py"),
        ('source_ext_id: "nexus-soc"', "frontend/src/routes/Cockpit.test.tsx", "frontend/src/routes/ActionsPage.test.tsx"),
        ('"reference/nexus/hub.py"', "backend/tests/test_export_public.py", "backend/tests/test_ext_shield.py"),
    ],
)
def test_a_listed_shield_name_is_a_finding_in_any_other_file(line, listed_in, elsewhere):
    """Die Positivliste gilt nur fuer die Dateien, die sie nennt: derselbe Name woanders ist ein Fund."""
    assert shield_kinds(line, listed_in) == [], line
    assert shield_kinds(line, elsewhere) == ["shield-name"], (line, elsewhere)


def test_only_the_listed_part_of_a_line_is_allowed():
    """Wie bei `lattice`: nur der Treffer wird aus der Zeile genommen, der Rest wird weiter geprueft."""
    rel = "backend/tests/test_ext_shield.py"
    assert shield_kinds('x = "ext_nexus_soc_scans"  # und "nexus-soc"', rel) == ["shield-name"]
    assert shield_kinds('x = "ext_nexus_soc_scans"  # und "ext_nexus_soc_events"', rel) == []
    assert shield_kinds('x = "/var/lib/nexus-updates/x"; y = "nexus_soc.scan"', rel) == ["shield-name"]


def test_capitalised_product_name_is_not_checked_by_the_identifier_rule():
    """Der Produktname "Nexus" gehoert zu `test_no_visible_nexus.py`; Regel 3 prueft nur die technische Schreibweise."""
    assert shield_kinds("# Das alte Nexus-Skript") == []
    assert shield_kinds("Nexus SOC") == []
    assert shield_kinds('marker = "NEXUS-Entscheidung"') == []
    assert shield_kinds("# Nexus_SOC", "backend/src/nodvard_deck/x.py") == ["shield-name"], "als Kennung geschrieben schon"


def test_the_shield_rule_applies_to_workflows_and_scripts_too():
    """`.github/` ist fuer `lattice` ausgenommen, fuer die Shield-Kennung nicht: ein Pfadfilter auf den alten Ordner faellt auf."""
    assert shield_kinds("      - 'extensions/nexus-soc/**'", ".github/workflows/ci.yml") == ["shield-name"]
    assert shield_kinds("cd extensions/nexus-soc/frontend", "scripts/build_something.mjs") == ["shield-name"]
    assert not guard.is_shield_exempt(".github/workflows/ci.yml")
    assert not guard.is_shield_exempt("scripts/update_api_contract.py")


def test_what_the_shield_rule_does_not_check():
    for rel in (
        "docs/01-ARCHITECTURE.md",
        "docs/11-ERST-EINRICHTUNG.md",
        "reference/nexus/hub.py",
        "CLAU" "DE.md",  # zusammengesetzt: export_public.sh sucht den Namen im Schnappschuss
        "README.md",
        "backend/src/nodvard_deck/changelog/versions/0.6.0.toml",
        "extensions/shield/frontend/dist/index.js",
        "extensions/shield/migrations/versions/e5f6a7b8c9d0_nexus_soc_antivirus.py",
        "backend/tests/contract/api_v1.json",
        "backend/tests/contract/alembic_revisions.json",
        "frontend/package-lock.json",
        # die Waechter selbst und ihre Tests
        "scripts/check_legacy_names.py",
        "backend/tests/test_legacy_names.py",
        "scripts/check_core_purity.py",
        "backend/tests/test_no_visible_nexus.py",
    ):
        assert guard.is_shield_exempt(rel), rel
    for rel in (
        "backend/tests/contract/test_alembic_revisions.py",  # nur die beiden Schnappschuesse sind ausgenommen
        "backend/tests/contract/sdk_public.json",
        "extensions/shield/src/nodvard_deck_ext_shield/__init__.py",
        "extensions/shield/frontend/src/SocPage.tsx",
        "extensions/shield/extension.toml",
        "extensions/shield/settings.schema.json",
        "extensions/proxmox/migrations/versions/x.py",  # eingefroren ist nur der Ordner von Shield
        "backend/src/nodvard_deck/changelog/unreleased/feat-x.toml",
        "backend/tests/test_core_purity.py",
        "backend/tests/test_shim_core.py",
    ):
        assert not guard.is_shield_exempt(rel), rel


def test_new_files_with_the_old_shield_name_are_findings(tmp_path):
    """Je Art ein kuenstlicher Verstoss in einem neuen Ordner; die saubere Datei bleibt ohne Fund."""
    files = {
        "extensions/x/src/ext.py": "from nodvard_deck_ext_nexus_soc import ids\nok = 1\n",
        "frontend/src/routes/Page.test.tsx": "const a = 1;\nrenderAt(['/ext/nexus-soc/soc']);\n",
        "backend/src/nodvard_deck/services/y.py": "ok = 1\nTYPE = 'nexus_soc.scan'\n",
        "extensions/x/src/fine.py": "from nodvard_deck_ext_shield import ids\nTYPE = 'shield.scan'\n",
        "frontend/src/routes/PageLegacy.test.tsx": "renderAt(['/ext/nexus-soc/soc']);  // Alias-Test: Adresse und Kennung erlaubt\n",
    }
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    found = guard.find_violations(tmp_path, files=list(files))
    assert sorted((f.path, f.line, f.rule) for f in found) == [
        ("backend/src/nodvard_deck/services/y.py", 2, "shield-name"),
        ("extensions/x/src/ext.py", 1, "shield-paket"),
        ("frontend/src/routes/Page.test.tsx", 2, "shield-adresse"),
    ]


def test_a_shield_finding_makes_the_script_fail_with_a_hint(monkeypatch, capsys):
    finding = guard.Finding("extensions/x/src/ext.py", 3, "shield-paket", "import nodvard_deck_ext_nexus_soc")
    monkeypatch.setattr(guard, "find_violations", lambda: [finding])
    assert guard.main([]) == 1
    out = capsys.readouterr().out
    assert "Nodvard Shield" in out and "extensions/x/src/ext.py:3: [shield-paket]" in out and "KEPT_SHIELD" in out
    assert "`lattice` ist (wieder)" not in out, "nur der Teil mit dem Fund wird ausgegeben"


def test_the_script_reports_both_rules_when_it_is_clean(monkeypatch, capsys):
    monkeypatch.setattr(guard, "find_violations", list)
    assert guard.main([]) == 0
    out = capsys.readouterr().out
    assert out.count("OK:") == 2 and "lattice" in out and "Shield" in out


# --------------------------------------------------------------------------- Regel 3: die Positivliste


def _shield_usage() -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for item in guard.list_kept():
        if item.rule == "shield-bleibt":
            for name in item.kept.split(", "):
                used.setdefault(name, set()).add(item.path)
    return used


def test_every_entry_of_the_shield_list_is_still_needed():
    unused = [k.name for k in guard.KEPT_SHIELD if k.name not in _shield_usage()]
    assert not unused, f"nicht mehr gebraucht, bitte aus KEPT_SHIELD streichen: {unused}"


def test_every_file_of_the_shield_list_still_needs_its_entry():
    """Sonst bliebe nach einem Umzug ein Freibrief fuer eine Datei, in der der Name gar nicht mehr steht."""
    usage = _shield_usage()
    stale = [
        f"{entry.name}: {where}"
        for entry in guard.KEPT_SHIELD
        for where in entry.where
        if not any(re.search(where, path) for path in usage.get(entry.name, set()))
    ]
    assert not stale, "diese Dateiangaben werden nicht mehr gebraucht:\n" + "\n".join(stale)


def test_every_entry_of_the_shield_list_names_its_reason_and_its_files():
    names = [k.name for k in guard.KEPT_SHIELD]
    assert len(names) == len(set(names)), "Namen der Eintraege sind eindeutig"
    assert not set(names) & {k.name for k in guard.KEPT_NAMES}, "und kollidieren nicht mit der lattice-Liste"
    for entry in guard.KEPT_SHIELD:
        assert entry.where and entry.paths is not None, f"{entry.name}: ohne Dateiangabe gaelte der Eintrag ueberall"
        assert len(entry.reason) > 80, f"{entry.name}: Begruendung fehlt oder ist zu kurz"
        assert not any(w in ("", ".*", "^.*$") for w in entry.where), f"{entry.name}: Dateiangabe ist ein Freibrief"


def test_shield_exemptions_are_only_what_the_rule_names():
    """Die Ausnahmen der Regel 3 bleiben ueberschaubar (keine neue Datei still dazugeschrieben)."""
    assert guard.SHIELD_EXEMPT_PREFIXES == (
        "docs/",
        "reference/",
        "backend/src/nodvard_deck/changelog/versions/",
        "extensions/shield/migrations/",
        "backend/tests/contract/api_v1.json",
        "backend/tests/contract/alembic_revisions.json",
        "scripts/check_legacy_names.py",
        "backend/tests/test_legacy_names.py",
        "scripts/check_core_purity.py",
        "backend/tests/test_no_visible_nexus.py",
    )
