"""Git-Einstellungen in Sicherungen: weder sichern noch einspielen.

Eine fremde Sicherung darf unter `files/ext/` kein Repository mit Filtern, Hooks oder Attributen
unterbringen (Git wuerde sie beim naechsten Speichern ausfuehren). Die Daten des Verlaufs
(Objekte, Verweise) bleiben, und AELTERE Sicherungen, die solche Dateien noch enthalten, lassen
sich weiter einspielen -- die Dateien werden einfach uebersprungen.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "scripts" / "src"))

from nodvard_deck.core.backup import format as fmt
from nodvard_deck.core.backup import restore
from nodvard_deck.core.backup.errors import DamagedBackup
from nodvard_deck.migrate import known_revisions
from nodvard_deck_ext_scripts.repo import ScriptMeta, ScriptRepo
from restore_helpers import (
    CREATED,
    REPO_ROOT,
    craft_backup,
    current_heads,
    limits,
    make_backup,
    make_db,
    make_layout,
    stage,
    tar_member,
)

KNOWN = known_revisions(REPO_ROOT)[0]
SHA = "ab" + "c" * 38
PACK = "pack-" + "d" * 40


@pytest.fixture
def layout(tmp_path):
    lay = make_layout(tmp_path)
    restore.make_private_dir(lay.restore_dir)
    return lay


# ---------------------------------------------------------------------------
# Namensregeln
# ---------------------------------------------------------------------------

UNWANTED_FILES = [
    "files/ext/scripts/repo/.git/config",
    "files/ext/scripts/repo/.git/hooks/pre-commit",
    "files/ext/scripts/repo/.git/info/attributes",
    "files/ext/scripts/repo/.git/info/exclude",
    "files/ext/scripts/repo/.git/commondir",
    "files/ext/scripts/repo/.git/config.worktree",
    "files/ext/scripts/repo/.git/objects/info/alternates",
    "files/ext/scripts/repo/.git/objects/ab/not-a-hash",
    "files/ext/scripts/repo/.git/objects/zz/" + "c" * 38,
    "files/ext/scripts/repo/.git/worktrees/x/gitdir",
    "files/ext/scripts/repo/.git/logs/HEAD",
    "files/ext/scripts/repo/.git/HEAD/zusatz",
    "files/ext/scripts/repo/.git",  # als Datei (Verweis auf einen anderen Ordner)
    "files/ext/scripts/repo/.GIT/config",
    "files/ext/scripts/repo/.gitattributes",
    "files/ext/scripts/repo/sub/.gitattributes",
    "files/ext/scripts/repo/.gitmodules",
    "files/ext/scripts/repo/.gitconfig",
    "files/branding/.git/config",
    "files/runs/x/.git/hooks/post-commit",
]

WANTED_FILES = [
    "files/ext/scripts/repo/.git/HEAD",
    "files/ext/scripts/repo/.git/index",
    "files/ext/scripts/repo/.git/packed-refs",
    "files/ext/scripts/repo/.git/refs/heads/master",
    f"files/ext/scripts/repo/.git/objects/{SHA[:2]}/{SHA[2:]}",
    f"files/ext/scripts/repo/.git/objects/pack/{PACK}.pack",
    f"files/ext/scripts/repo/.git/objects/pack/{PACK}.idx",
    "files/ext/scripts/repo/a/script.sh",
    "files/ext/scripts/repo/.gitignore",
    "files/ext/documents/.github/workflow.yml",
    "files/ext/documents/notizen.git/liste.txt",
]


@pytest.mark.parametrize("name", UNWANTED_FILES)
def test_git_settings_are_not_restorable_and_get_skipped(name):
    assert fmt.is_restore_file(name) is False
    assert fmt.is_unwanted_restore_entry(name, False) is True


@pytest.mark.parametrize("name", WANTED_FILES)
def test_history_data_and_ordinary_files_stay_restorable(name):
    assert fmt.is_restore_file(name) is True
    assert fmt.is_unwanted_restore_entry(name, False) is False


@pytest.mark.parametrize("name,wanted", [
    ("files/ext/scripts/repo/.git", True),
    ("files/ext/scripts/repo/.git/objects", True),
    ("files/ext/scripts/repo/.git/objects/pack", True),
    ("files/ext/scripts/repo/.git/objects/ab", True),
    ("files/ext/scripts/repo/.git/refs/heads", True),
    ("files/ext/scripts/repo/.git/hooks", False),
    ("files/ext/scripts/repo/.git/info", False),
    ("files/ext/scripts/repo/.git/logs", False),
])
def test_git_directories(name, wanted):
    assert fmt.is_restore_dir(name) is wanted
    assert fmt.is_unwanted_restore_entry(name, True) is (not wanted)


@pytest.mark.parametrize("name", [
    "files/ext/scripts/repo/.git./config",
    "files/ext/scripts/repo/.git /config",
    "files/ext/scripts/repo/.git::$INDEX_ALLOCATION/config",
    "files/ext/scripts/repo/.gitattributes.",
    "files/ext/scripts/repo/.gitattributes:stream",
    # NTFS-Kurznamen: `GIT~1/config` ist dieselbe Datei wie `.git/config`.
    "files/ext/scripts/repo/GIT~1/config",
    "files/ext/scripts/repo/git~1/hooks/pre-commit",
    "files/ext/scripts/repo/GIT~2/info/attributes",
    "files/ext/scripts/repo/GITATT~1",
    "files/ext/scripts/repo/gitatt~1",
    "files/ext/scripts/repo/GITMOD~1",
    "files/ext/scripts/repo/GITCON~1",
    "files/ext/scripts/repo/GITCON~1.",
    "files/ext/scripts/repo/GIT~1 /config",
    "files/ext/scripts/repo/GIT~1::$INDEX_ALLOCATION/config",
    # Ersatzform nach mehreren gleichen Kurznamen (zwei Zeichen, vier Hex-Ziffern).
    "files/ext/scripts/repo/GI7EBA~1",
    "files/ext/scripts/repo/gi7d29~1",
    "files/ext/scripts/repo/GI1F2E~1/config",
    "files/ext/scripts/repo/GI1F2E~3/hooks/post-commit",
])
def test_names_windows_reads_as_git_settings_are_skipped_too(name):
    assert fmt.is_restore_file(name) is False
    assert fmt.is_unwanted_restore_entry(name, False) is True


@pytest.mark.parametrize("name", [
    f"files/ext/scripts/repo/GIT~1/objects/{SHA[:2]}/{SHA[2:]}",
    f"files/ext/scripts/repo/GIT~1/objects/pack/{PACK}.pack",
    "files/ext/scripts/repo/GIT~1/refs/heads/master",
    "files/ext/scripts/repo/GIT~1/HEAD",
    "files/ext/scripts/repo/GITHUB~1/workflow.yml",
    "files/ext/scripts/repo/gitignore~1",
    "files/ext/scripts/repo/GITATTRIBUTES~1",
    "files/ext/scripts/repo/mygit~1/config",
    "files/ext/scripts/repo/git~/config",
    "files/ext/scripts/repo/git~x/config",
    f"files/ext/scripts/repo/GI1F2E~1/objects/{SHA[:2]}/{SHA[2:]}",
    "files/ext/scripts/repo/GI1F2E~1/refs/heads/master",
    "files/ext/scripts/repo/GI7EBX~1",
    "files/ext/scripts/repo/GI7EB~1",
    "files/ext/scripts/repo/XY7EBA~1",
])
def test_ntfs_short_names_do_not_catch_history_or_other_names(name):
    """`GIT~1` zaehlt wie `.git`: der Verlauf darin bleibt erlaubt, andere Namen auch."""
    assert fmt.is_restore_file(name) is True
    assert fmt.is_unwanted_restore_entry(name, False) is False


@pytest.mark.parametrize("name,wanted", [
    ("files/ext/scripts/repo/GIT~1", True),
    ("files/ext/scripts/repo/GIT~1/objects", True),
    ("files/ext/scripts/repo/GIT~1/hooks", False),
    ("files/ext/scripts/repo/GIT~1/info", False),
])
def test_ntfs_short_name_directories(name, wanted):
    assert fmt.is_restore_dir(name) is wanted
    assert fmt.is_unwanted_restore_entry(name, True) is (not wanted)


def test_names_that_are_rejected_anyway_are_not_reported_as_skippable():
    for name in ("files/other/.git/config", "files/ext/../.git/config", "db/.git/config", "files/master.key"):
        assert fmt.is_unwanted_restore_entry(name, False) is False


def test_backup_exclusion_covers_git_settings_but_not_history():
    from pathlib import PurePosixPath

    assert fmt.is_excluded(PurePosixPath("ext/scripts/repo/.git/config"))
    assert fmt.is_excluded(PurePosixPath("ext/scripts/repo/.git/hooks/pre-commit"))
    assert fmt.is_excluded(PurePosixPath("ext/scripts/repo/.gitattributes"))
    assert fmt.is_excluded(PurePosixPath("ext/scripts/repo/GIT~1/config"))
    assert fmt.is_excluded(PurePosixPath("ext/scripts/repo/GITATT~1"))
    assert fmt.is_excluded(PurePosixPath("ext/scripts/repo/GI1F2E~1/config"))
    assert not fmt.is_excluded(PurePosixPath("ext/scripts/repo/.git/refs/heads/master"))
    assert not fmt.is_excluded(PurePosixPath(f"ext/scripts/repo/.git/objects/{SHA[:2]}/{SHA[2:]}"))
    assert not fmt.is_excluded(PurePosixPath("master.key"))


# ---------------------------------------------------------------------------
# Wiederherstellen einer (aelteren oder praeparierten) Sicherung
# ---------------------------------------------------------------------------


def _craft(tmp_path: Path, layout, extra: dict[str, bytes], *, tamper: dict[str, bytes] | None = None,
           hide_from_manifest: set[str] | None = None) -> Path:
    """Eine gueltige Sicherung mit beliebigen zusaetzlichen Dateien unter `files/` (auch solchen, die
    `make_backup` selbst nicht mehr schreiben wuerde)."""
    tag = str(len(list(tmp_path.glob("basis*.ndbak"))))
    base_backup = make_backup(tmp_path / f"basis{tag}.ndbak", db=make_db(tmp_path / f"q{tag}.db"), files={"ext/leer/x.txt": b"x"})
    _rid, staged = stage(layout, base_backup)
    root = layout.restore_dir / _rid / restore.STAGING_NAME
    db_bytes = (root / "db" / "lattice.db").read_bytes()
    members = [tar_member("db/lattice.db", db_bytes)]
    manifest_files = []
    for name, data in extra.items():
        shown = (tamper or {}).get(name, data)
        members.append(tar_member(name, shown))
        if name not in (hide_from_manifest or set()):
            manifest_files.append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "mode": 420})
    manifest = {
        "format": 1, "app_version": "0.5.0", "created_at": "2026-10-01T03:00:00Z", "instance_id": "inst-1",
        "jwt_from_env": False, "build": None,
        "header": fmt.build_header(created_at=CREATED, app_version="0.5.0", mode="passwort"),
        "db": {"dialect": "sqlite", "sha256": staged.files["db/lattice.db"][0], "size": staged.files["db/lattice.db"][1],
               "alembic_heads": current_heads()},
        "extensions": [], "files": manifest_files,
    }
    shutil.rmtree(layout.restore_dir / _rid)
    return craft_backup(tmp_path / f"praepariert{tag}.ndbak", members, manifest=manifest)


EVIL_FILES = {
    "files/ext/scripts/repo/.git/config": b'[filter "evil"]\n\tclean = touch MARKER\n',
    "files/ext/scripts/repo/.gitattributes": b"* filter=evil\n",
    "files/ext/scripts/repo/.git/hooks/pre-commit": b"#!/bin/sh\ntouch MARKER\n",
    "files/ext/scripts/repo/.git/info/attributes": b"* filter=evil\n",
}
GOOD_FILES = {
    "files/ext/scripts/repo/.git/HEAD": b"ref: refs/heads/master\n",
    "files/ext/scripts/repo/.git/refs/heads/master": b"a" * 40 + b"\n",
    f"files/ext/scripts/repo/.git/objects/{SHA[:2]}/{SHA[2:]}": b"objekt",
    "files/ext/scripts/repo/a/script.sh": b"echo hallo\n",
    "files/ext/scripts/repo/a/meta.json": b'{"id": "a", "name": "A"}\n',
    "files/ext/scripts/repo/.gitignore": b"*.tmp\n",
}


def test_staging_skips_git_settings_and_keeps_the_rest(layout, tmp_path):
    path = _craft(tmp_path, layout, {**EVIL_FILES, **GOOD_FILES})
    rid, staged = stage(layout, path)
    base = layout.restore_dir / rid / restore.STAGING_NAME
    for name in EVIL_FILES:
        assert not (base / name).exists(), name
        assert name not in staged.files
    assert not (base / "files/ext/scripts/repo/.git/hooks").exists()
    for name, data in GOOD_FILES.items():
        assert (base / name).read_bytes() == data
        assert name in staged.files
    # Die nachtraegliche Pruefung beim Einspielen sieht genau dasselbe wie das Vormerken.
    assert restore.hash_tree(base) == staged.files


def test_skipped_entries_are_logged_with_their_count(layout, tmp_path, caplog):
    path = _craft(tmp_path, layout, {**EVIL_FILES, **GOOD_FILES})
    with caplog.at_level("WARNING", logger="nodvard_deck.restore"):
        stage(layout, path)
    assert any("restore_skipped_vcs_entries count=4" in r.getMessage() for r in caplog.records)


def test_clean_backup_logs_nothing(layout, tmp_path, caplog):
    path = _craft(tmp_path, layout, dict(GOOD_FILES))
    with caplog.at_level("WARNING", logger="nodvard_deck.restore"):
        stage(layout, path)
    assert not any("restore_skipped" in r.getMessage() for r in caplog.records)


def test_skipped_git_files_show_up_in_the_summary_warnings(layout, tmp_path):
    """Die Zusammenfassung vor dem Vormerken (Oberflaeche und Kommandozeile lesen `warnings`) nennt die
    Zahl der uebersprungenen Git-Dateien -- nicht nur das Protokoll auf dem Server."""
    path = _craft(tmp_path, layout, {**EVIL_FILES, **GOOD_FILES})
    _rid, staged = stage(layout, path)
    assert "4 Git-Einstellungen aus der Sicherung wurden nicht übernommen, der Verlauf bleibt." in staged.summary["warnings"]


def test_one_skipped_git_file_is_reported_in_the_singular(layout, tmp_path):
    path = _craft(tmp_path, layout, {"files/ext/scripts/repo/.git/config": b"[core]\n", **GOOD_FILES})
    _rid, staged = stage(layout, path)
    assert "1 Git-Einstellung aus der Sicherung wurde nicht übernommen, der Verlauf bleibt." in staged.summary["warnings"]


def test_clean_backup_has_no_git_warning(layout, tmp_path):
    path = _craft(tmp_path, layout, dict(GOOD_FILES))
    _rid, staged = stage(layout, path)
    assert not any("Git-Einstellung" in text for text in staged.summary["warnings"])


def test_skipped_entries_still_count_against_the_manifest_checksums(layout, tmp_path):
    """Ein uebersprungener Eintrag wird gelesen und geprueft wie jeder andere: ein veraenderter Inhalt
    oder ein Eintrag, der im Inhaltsverzeichnis fehlt, macht die Sicherung ungueltig."""
    name = "files/ext/scripts/repo/.git/config"
    tampered = _craft(tmp_path, layout, {name: b"[core]\n"}, tamper={name: b"[filter]\n"})
    rid = restore.new_id()
    with pytest.raises(DamagedBackup, match="Prüfsummen"):
        stage(layout, tampered, restore_id=rid)
    assert not (layout.restore_dir / rid / restore.STAGING_NAME).exists()
    hidden = _craft(tmp_path, layout, {name: b"[core]\n"}, hide_from_manifest={name})
    with pytest.raises(DamagedBackup, match="Prüfsummen"):
        stage(layout, hidden)


def test_skipping_does_not_widen_what_is_accepted(layout, tmp_path):
    """Nur normale Dateien und Ordner werden uebersprungen: ein Link unter demselben Namen bleibt ein Fehler."""
    link = tar_member("files/ext/scripts/repo/.git/config", None, type_=__import__("tarfile").SYMTYPE, linkname="/etc/passwd")
    path = craft_backup(tmp_path / "link.ndbak", [link])
    with pytest.raises(DamagedBackup, match="unerwarteter Eintrag"):
        stage(layout, path)


def test_skipped_entries_count_against_the_size_limits(layout, tmp_path):
    from nodvard_deck.core.backup.errors import BackupTooLarge

    path = _craft(tmp_path, layout, {"files/ext/scripts/repo/.git/hooks/gross": b"x" * 5000})
    with pytest.raises(BackupTooLarge):
        stage(layout, path, lim=limits(max_file_bytes=1000))


def test_full_restore_leaves_no_git_settings_on_disk(layout, tmp_path):
    from restore_helpers import fill_live

    fill_live(layout, "alt")
    path = _craft(tmp_path, layout, {**EVIL_FILES, **GOOD_FILES})
    rid, staged = stage(layout, path)
    restore.write_pending(
        layout, restore_id=rid, source="owner", actor={"type": "user", "id": "u1", "label": "anna"},
        sign_out_all=True, staged=staged,
    )
    applied = restore.apply_pending(layout, known=KNOWN, current_version="0.5.0")
    assert applied is not None
    repo_dir = layout.ext_dir / "scripts" / "repo"
    assert (repo_dir / "a" / "script.sh").read_bytes() == b"echo hallo\n"
    assert (repo_dir / ".git" / "HEAD").is_file()
    assert not (repo_dir / ".git" / "config").exists()
    assert not (repo_dir / ".git" / "hooks").exists()
    assert not (repo_dir / ".git" / "info").exists()
    assert not (repo_dir / ".gitattributes").exists()


# ---------------------------------------------------------------------------
# Sichern und Wiederherstellen mit echten Skripten (Verlauf bleibt)
# ---------------------------------------------------------------------------


def _files_of(root: Path, prefix: str) -> dict[str, bytes]:
    return {f"{prefix}/{p.relative_to(root).as_posix()}": p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_real_scripts_with_history_survive_backup_and_restore(layout, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    source = tmp_path / "quelle"
    repo = ScriptRepo(source / "ext" / "scripts" / "repo")
    repo.save(ScriptMeta(id="a", name="A"), "echo eins\n", commit_message="eins")
    repo.save(ScriptMeta(id="a", name="A"), "echo zwei\n", commit_message="zwei")
    repo.save(ScriptMeta(id="b", name="B"), "echo b\n", commit_message="b")
    live_files = _files_of(source / "ext", "ext")
    assert "ext/scripts/repo/.git/config" in live_files  # das echte Repo hat sie

    backup = make_backup(tmp_path / "echt.ndbak", db=make_db(tmp_path / "q.db"), files=live_files)
    rid, _staged = stage(layout, backup)
    base = layout.restore_dir / rid / restore.STAGING_NAME
    # Beim Sichern sind die Einstellungen schon weggelassen, also gibt es beim Einspielen nichts zu ueberspringen.
    assert not (base / "files/ext/scripts/repo/.git/config").exists()
    assert (base / "files/ext/scripts/repo/.git/HEAD").is_file()
    assert (base / "files/ext/scripts/repo/a/script.sh").is_file()

    restored_root = tmp_path / "wieder" / "ext" / "scripts" / "repo"
    shutil.copytree(base / "files" / "ext" / "scripts" / "repo", restored_root)
    reopened = ScriptRepo(restored_root)
    assert [s.meta.id for s in reopened.list_all()] == ["a", "b"]
    assert [h["message"] for h in reopened.history("a")] == ["zwei", "eins"]
    reopened.save(ScriptMeta(id="a", name="A"), "echo drei\n", commit_message="drei")
    assert [h["message"] for h in reopened.history("a")] == ["drei", "zwei", "eins"]
    assert reopened.delete("b") is True
    assert (restored_root / ".git" / "config").is_file()


def test_a_scripts_repo_without_commits_still_works_after_backup_and_restore(layout, tmp_path, monkeypatch):
    """Ein frisch angelegtes Repo (noch kein Skript gespeichert) hat nur leere Ordner und `HEAD`;
    in der Sicherung bleibt davon nur `HEAD`."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    source = tmp_path / "quelle"
    ScriptRepo(source / "ext" / "scripts" / "repo")
    live_files = _files_of(source / "ext", "ext")
    backup = make_backup(tmp_path / "leer.ndbak", db=make_db(tmp_path / "q.db"), files=live_files)
    rid, _staged = stage(layout, backup)
    base = layout.restore_dir / rid / restore.STAGING_NAME
    restored_root = tmp_path / "wieder" / "ext" / "scripts" / "repo"
    shutil.copytree(base / "files" / "ext" / "scripts" / "repo", restored_root)
    assert not (restored_root / ".git" / "objects").exists()

    reopened = ScriptRepo(restored_root)
    reopened.save(ScriptMeta(id="a", name="A"), "echo eins\n", commit_message="eins")
    assert [h["message"] for h in reopened.history("a")] == ["eins"]
