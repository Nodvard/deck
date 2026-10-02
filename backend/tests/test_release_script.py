"""scripts/release.py: Bruchstuecke -> neue Version, Versionsnummern anheben.

Alle Tests laufen auf einer Kopie der echten Dateien in einem Temp-Ordner -- so wird
gleich mit geprueft, dass das Skript die echten Formate von pyproject.toml, package.json
und package-lock.json versteht.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

from nodvard_deck import __version__, changelog

REPO_ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("nodvard_deck_release_script", REPO_ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(_spec)
sys.modules["nodvard_deck_release_script"] = release  # dataclasses schlagen ihre Klasse hier nach
_spec.loader.exec_module(release)

BASE_VERSION = "0.4.0"

_COPIED = (
    "backend/src/nodvard_deck/version.py",
    "deploy/updater/nodvard_deck_updater/__init__.py",
    "backend/pyproject.toml",
    "frontend/package.json",
    "frontend/package-lock.json",
)


@pytest.fixture
def tree(tmp_path) -> release.Tree:
    root = tmp_path / "repo"
    for rel in _COPIED:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, root / rel)
    shutil.copytree(REPO_ROOT / "backend/src/nodvard_deck/changelog/versions", root / "backend/src/nodvard_deck/changelog/versions")
    tree = release.Tree(root)
    # Die Tests gehen von Stand 0.4.0 aus: die Kopie wird dorthin zurueckgesetzt, damit sie
    # auch nach einem echten Release (0.5.0, ...) noch dasselbe pruefen.
    for path, text in release.bumped_files(tree, BASE_VERSION).items():
        release._write(path, text)
    for later in tree.versions_dir.glob("*.toml"):
        if release.parse_version(later.stem) > release.parse_version(BASE_VERSION):
            later.unlink()
    unreleased = root / "backend/src/nodvard_deck/changelog/unreleased"
    unreleased.mkdir(parents=True)
    (unreleased / "README.md").write_text("# Hinweis\n", encoding="utf-8")
    return tree


def _fragment(tree: release.Tree, name: str, body: str) -> Path:
    path = tree.unreleased_dir / f"{name}.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _snapshot(tree: release.Tree) -> dict[str, bytes]:
    return {p.relative_to(tree.root).as_posix(): p.read_bytes() for p in tree.root.rglob("*") if p.is_file()}


TWO_FRAGMENTS = {
    "b-zweiter": '[[entries]]\nkind = "behoben"\ntext = "Zweiter."\nprs = [51]\n',
    "a-erster": '[[entries]]\nkind = "neu"\ntext = "Erster."\nprs = [50, 52]\n\n[[entries]]\nkind = "sicherheit"\ntext = "Dritter."\n',
}


def _with_fragments(tree: release.Tree) -> None:
    for name, body in TWO_FRAGMENTS.items():
        _fragment(tree, name, body)


def test_release_writes_version_file_bumps_everything_and_removes_fragments(tree, capsys):
    _with_fragments(tree)
    before = _snapshot(tree)

    assert release.main(["0.5.0", "--title", "Neuer Stand", "--date", "2026-10-02"], root=tree.root) == 0

    new_file = tree.versions_dir / "0.5.0.toml"
    parsed = changelog.read_release(new_file)
    assert (parsed.version, parsed.date, parsed.title) == ("0.5.0", "2026-10-02", "Neuer Stand")
    # Reihenfolge: nach Dateiname der Bruchstuecke, dann wie in der Datei.
    assert [(e.kind, e.text, e.prs) for e in parsed.entries] == [
        ("neu", "Erster.", (50, 52)),
        ("sicherheit", "Dritter.", ()),
        ("behoben", "Zweiter.", (51,)),
    ]
    # Bruchstuecke weg, README bleibt.
    assert sorted(p.name for p in tree.unreleased_dir.iterdir()) == ["README.md"]
    # Alle Versionsstellen zeigen die neue Nummer und stimmen wieder ueberein.
    assert release.current_version(tree) == "0.5.0"
    assert set(release.read_versions(tree).values()) == {"0.5.0"}
    loaded = changelog.load_changelog(tree.changelog_dir)
    assert [r.version for r in loaded.versions][:2] == ["0.5.0", "0.4.0"]
    assert loaded.unreleased == ()
    assert "3 Eintraege aus 2 Bruchstuecken, 0.4.0 -> 0.5.0" in capsys.readouterr().out

    # Sonst hat sich nichts veraendert: in den kopierten Dateien genau die Versionszeilen.
    after = _snapshot(tree)
    changed = {k for k in before if k in after and before[k] != after[k]}
    assert changed == set(_COPIED)
    for rel in _COPIED:
        old_lines, new_lines = before[rel].decode().splitlines(), after[rel].decode().splitlines()
        assert len(old_lines) == len(new_lines)
        diff = [(o, n) for o, n in zip(old_lines, new_lines) if o != n]
        assert diff, rel
        assert all("0.4.0" in o and "0.5.0" in n for o, n in diff), (rel, diff)
    assert len([1 for k in after if k not in before]) == 1  # nur die neue Versionsdatei


def test_pure_fix_round_and_default_date(tree):
    _fragment(tree, "fix", '[[entries]]\nkind = "behoben"\ntext = "Fehler."\n')
    assert release.main(["0.4.1"], root=tree.root) == 0
    parsed = changelog.read_release(tree.versions_dir / "0.4.1.toml")
    assert parsed.date == release.today()
    assert parsed.title is None
    assert "title" not in (tree.versions_dir / "0.4.1.toml").read_text(encoding="utf-8")


def test_dry_run_changes_nothing(tree, capsys):
    _with_fragments(tree)
    before = _snapshot(tree)
    assert release.main(["0.5.0", "--dry-run"], root=tree.root) == 0
    assert _snapshot(tree) == before
    assert "Wuerde anlegen" in capsys.readouterr().out


@pytest.mark.parametrize("version", ["0.4.0", "0.3.9", "0.4", "v0.5.0", "0.5.0-rc1", "1.2.3.4", "abc"])
def test_refuses_versions_that_are_not_greater_or_not_valid(tree, capsys, version):
    _with_fragments(tree)
    before = _snapshot(tree)
    assert release.main([version], root=tree.root) == 1
    assert _snapshot(tree) == before
    assert "Abgebrochen" in capsys.readouterr().err


def test_numeric_comparison_not_text(tree):
    """0.10.0 ist groesser als 0.4.0 (als Text waere es kleiner)."""
    _with_fragments(tree)
    assert release.main(["0.10.0"], root=tree.root) == 0
    assert release.current_version(tree) == "0.10.0"


def test_refuses_without_fragments(tree, capsys):
    (tree.unreleased_dir / "irgendwas.md").write_text("keine toml\n", encoding="utf-8")
    before = _snapshot(tree)
    assert release.main(["0.5.0"], root=tree.root) == 1
    assert _snapshot(tree) == before
    assert "keine Eintraege" in capsys.readouterr().err


def test_missing_unreleased_folder_is_refused_too(tree):
    shutil.rmtree(tree.unreleased_dir)
    assert release.main(["0.5.0"], root=tree.root) == 1


def test_invalid_fragment_stops_everything(tree, capsys):
    _with_fragments(tree)
    _fragment(tree, "c-kaputt", '[[entries]]\nkind = "kaputt"\ntext = "x"\n')
    before = _snapshot(tree)
    assert release.main(["0.5.0"], root=tree.root) == 1
    assert _snapshot(tree) == before  # auch die guten Bruchstuecke bleiben liegen
    err = capsys.readouterr().err
    assert "c-kaputt.toml" in err and "kind" in err


def test_refuses_when_version_numbers_are_out_of_step(tree, capsys):
    _with_fragments(tree)
    package = tree.package_json
    package.write_text(package.read_text(encoding="utf-8").replace('"version": "0.4.0"', '"version": "0.3.0"', 1), encoding="utf-8")
    before = _snapshot(tree)
    assert release.main(["0.5.0"], root=tree.root) == 1
    assert _snapshot(tree) == before
    err = capsys.readouterr().err
    assert "Gleichschritt" in err and "frontend/package.json: 0.3.0" in err


def test_update_helper_version_is_bumped_with_the_rest(tree):
    """Der Update-Helfer meldet dieselbe Nummer wie das Dashboard: das Release hebt auch seine Datei an."""
    _with_fragments(tree)
    assert 'nodvard_deck_updater/__init__.py' in " ".join(release.read_versions(tree))
    assert '__version__ = "0.4.0"' in tree.updater_init.read_text(encoding="utf-8")
    assert release.main(["0.5.0"], root=tree.root) == 0
    assert '__version__ = "0.5.0"' in tree.updater_init.read_text(encoding="utf-8")


def test_refuses_when_update_helper_version_is_out_of_step(tree, capsys):
    """Von Hand geaendert oder vergessen: ohne den Gleichschritt gibt es kein Release."""
    _with_fragments(tree)
    init = tree.updater_init
    init.write_text(init.read_text(encoding="utf-8").replace('__version__ = "0.4.0"', '__version__ = "0.7.0"', 1),
                    encoding="utf-8")
    before = _snapshot(tree)
    assert release.main(["0.5.0"], root=tree.root) == 1
    assert _snapshot(tree) == before
    err = capsys.readouterr().err
    assert "Gleichschritt" in err and "nodvard_deck_updater/__init__.py: 0.7.0" in err


def test_refuses_when_newest_version_file_disagrees(tree):
    _with_fragments(tree)
    stale = tree.versions_dir / "0.4.0.toml"
    stale.rename(tree.versions_dir / "0.3.5.toml")
    stale = tree.versions_dir / "0.3.5.toml"
    stale.write_text(stale.read_text(encoding="utf-8").replace('version = "0.4.0"', 'version = "0.3.5"', 1), encoding="utf-8")
    assert release.main(["0.5.0"], root=tree.root) == 1


def test_refuses_invalid_date_and_title(tree):
    _with_fragments(tree)
    before = _snapshot(tree)
    assert release.main(["0.5.0", "--date", "2.10.2026"], root=tree.root) == 1
    assert release.main(["0.5.0", "--title", "x" * 101], root=tree.root) == 1
    assert _snapshot(tree) == before


def test_other_versions_in_pyproject_and_lock_are_left_alone(tree):
    """Nur [project].version und die zwei Wurzel-Eintraege des Lockfiles -- nicht die
    Versionen der Abhaengigkeiten (im Lockfile stehen hunderte)."""
    _with_fragments(tree)
    lock_before = tree.package_lock.read_text(encoding="utf-8")
    assert release.main(["0.5.0"], root=tree.root) == 0
    lock_after = tree.package_lock.read_text(encoding="utf-8")
    assert lock_after.count('"version"') == lock_before.count('"version"')
    assert lock_after.count('"version": "0.5.0"') == 2
    assert lock_before.count('"version": "0.4.0"') == 2


def test_rendered_file_survives_special_characters(tree):
    text = 'Anführungszeichen "so", Backslash \\ und „Umlaute“ äöü ß.'
    entry = changelog.Entry("neu", text, (1,))
    rendered = release.render_release("0.5.0", "2026-10-02", 'Titel "mit" Zeichen', [entry])
    parsed = changelog.parse_release(tomllib.loads(rendered))
    assert parsed.entries == (entry,)
    assert parsed.title == 'Titel "mit" Zeichen'


def test_files_keep_their_line_endings(tree):
    """Windows-Zeilenenden (CRLF) bleiben erhalten -- das Skript liest und schreibt Bytes."""
    _with_fragments(tree)
    path = tree.version_py
    path.write_bytes(b'__version__ = "0.4.0"\r\n')
    assert release.main(["0.5.0"], root=tree.root) == 0
    assert path.read_bytes() == b'__version__ = "0.5.0"\r\n'


def test_the_real_repository_passes_the_sync_check():
    """Auf den echten Dateien: alle Stellen im Gleichschritt, das Skript findet sie alle."""
    found = release.read_versions(release.Tree(REPO_ROOT))
    assert len(found) == 6 and None not in found.values()
    assert release.current_version(release.Tree(REPO_ROOT)) == __version__
    assert found["deploy/updater/nodvard_deck_updater/__init__.py"] == __version__
