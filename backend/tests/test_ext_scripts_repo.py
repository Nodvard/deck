"""`nodvard_deck_ext_scripts.repo.ScriptRepo` -- das Git-gestuetzte Skript-Repository.
Reine Dateisystem-/dulwich-Logik, kein `ctx`
noetig -- wie `nodvard_deck_ext_nexus_soc.parsing` bewusst isoliert testbar gehalten.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "scripts" / "src"))

from nodvard_deck_ext_scripts.repo import ScriptMeta, ScriptRepo  # noqa: E402


@pytest.fixture
def repo(tmp_path) -> ScriptRepo:
    return ScriptRepo(tmp_path / "repo")


def _meta(script_id: str = "s1", **overrides) -> ScriptMeta:
    defaults = dict(id=script_id, name="Skript 1")
    defaults.update(overrides)
    return ScriptMeta(**defaults)


def test_new_repo_is_empty(repo: ScriptRepo):
    assert repo.list_ids() == []
    assert repo.list_all() == []
    assert repo.get("does-not-exist") is None


def test_save_then_get_roundtrips_meta_and_content(repo: ScriptRepo):
    repo.save(_meta(schedule="0 1 * * *", target={"kind": "all"}), "echo hi\n", commit_message="initial")

    script = repo.get("s1")
    assert script is not None
    assert script.content == "echo hi\n"
    assert script.meta.name == "Skript 1"
    assert script.meta.schedule == "0 1 * * *"
    assert script.meta.target == {"kind": "all"}
    assert repo.list_ids() == ["s1"]


def test_save_twice_updates_in_place_and_keeps_history(repo: ScriptRepo):
    repo.save(_meta(), "echo v1\n", commit_message="v1")
    repo.save(_meta(name="Skript 1 (neu)"), "echo v2\n", commit_message="v2")

    script = repo.get("s1")
    assert script is not None
    assert script.content == "echo v2\n"
    assert script.meta.name == "Skript 1 (neu)"

    history = repo.history("s1")
    assert [h["message"] for h in history] == ["v2", "v1"]  # neueste zuerst


def test_history_only_returns_commits_touching_this_script(repo: ScriptRepo):
    repo.save(_meta("a"), "echo a\n", commit_message="a-commit")
    repo.save(_meta("b"), "echo b\n", commit_message="b-commit")
    repo.save(_meta("a", name="A2"), "echo a2\n", commit_message="a-commit-2")

    history_a = [h["message"] for h in repo.history("a")]
    assert history_a == ["a-commit-2", "a-commit"]
    history_b = [h["message"] for h in repo.history("b")]
    assert history_b == ["b-commit"]


def test_delete_removes_script_and_commits_the_removal(repo: ScriptRepo):
    repo.save(_meta(), "echo hi\n", commit_message="initial")
    assert repo.delete("s1") is True

    assert repo.get("s1") is None
    assert repo.list_ids() == []
    assert repo.delete("s1") is False  # zweites Loeschen: kein Fehler, nur False


def test_save_stages_content_even_when_given_a_relative_root_path(tmp_path, monkeypatch):
    """Live gefunden gegen den echten Dev-Server: `Settings.ext_data_dir`
    ist standardmaessig relativ ("./data/ext") -- `dulwich.porcelain.add()` staged
    dann STILL NICHTS (kein Fehler, nur ein leerer Baum, `git log --follow` faende
    ergo nie einen Commit fuer irgendein Skript), sobald der Repo-Root relativ
    bleibt. `pytest`s `tmp_path` ist immer absolut, deshalb hat kein anderer Test
    das gezeigt -- dieser hier erzwingt bewusst einen relativen Pfad, indem er das
    Arbeitsverzeichnis wechselt und `ScriptRepo` mit einem RELATIVEN `Path` aufruft."""
    monkeypatch.chdir(tmp_path)
    repo = ScriptRepo(Path("ext-data") / "scripts" / "repo")
    repo.save(_meta(), "echo hi\n", commit_message="initial")

    assert repo.history("s1") != []
    assert repo.get("s1").content == "echo hi\n"


def test_reopening_repo_at_same_path_sees_prior_commits(tmp_path):
    """Live relevant: die Extension oeffnet das Repo bei JEDEM Extension-Reload neu
    (siehe `Extension.setup()`) -- ein neues `ScriptRepo(root)` MUSS den bereits
    bestehenden `.git`-Zustand wiederfinden, statt ihn zu ueberschreiben."""
    root = tmp_path / "repo"
    ScriptRepo(root).save(_meta(), "echo hi\n", commit_message="initial")

    reopened = ScriptRepo(root)
    assert reopened.list_ids() == ["s1"]
    assert reopened.get("s1").content == "echo hi\n"


# ---------------------------------------------------------------------------
# Kein Programmaufruf durch Git-Einstellungen im Repo-Ordner
# ---------------------------------------------------------------------------


def _plant(root: Path, marker: Path) -> None:
    """Ein Repo, wie ein praeparierter Datenordner es haette: Filter, Attribute, Hook, Signierprogramm."""
    ScriptRepo(root)  # legt das Repo an
    git = root / ".git"
    git.joinpath("config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\thooksPath = " + str(git / "evil-hooks") + "\n"
        f'[filter "evil"]\n\tclean = touch {marker}\n\tsmudge = touch {marker}\n'
        f'[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = {git / "evil-gpg"}\n',
        encoding="utf-8",
    )
    (root / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    git.joinpath("info").mkdir(exist_ok=True)
    git.joinpath("info", "attributes").write_text("* filter=evil\n", encoding="utf-8")
    for hooks_dir in (git / "hooks", git / "evil-hooks"):
        hooks_dir.mkdir(exist_ok=True)
        for hook in ("pre-commit", "commit-msg", "post-commit"):
            path = hooks_dir / hook
            path.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
            path.chmod(0o755)
    gpg = git / "evil-gpg"
    gpg.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    gpg.chmod(0o755)


@pytest.mark.skipif(sys.platform == "win32", reason="Hooks und Filter brauchen eine POSIX-Shell")
def test_git_settings_in_the_repo_folder_never_run_a_program(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    marker = tmp_path / "MARKER"
    root = tmp_path / "repo"
    _plant(root, marker)

    opened = ScriptRepo(root)  # das Oeffnen raeumt auf
    opened.save(_meta(), "echo hi\n", commit_message="neu")
    opened.save(_meta(), "echo ho\n", commit_message="nochmal")
    assert opened.history("s1")[0]["message"] == "nochmal"
    assert opened.delete("s1") is True
    assert not marker.exists(), "ein Programm aus den Git-Einstellungen wurde ausgefuehrt"


@pytest.mark.skipif(sys.platform == "win32", reason="Hooks und Filter brauchen eine POSIX-Shell")
def test_even_without_cleanup_nothing_runs(tmp_path, monkeypatch):
    """Zweite Schicht: selbst wenn jemand die Einstellungen NACH dem Oeffnen hineinlegt, starten weder
    Filter noch Hooks (auch `post-commit` und `commit-msg`, die `no_verify` nicht abschaltet) noch das
    Signierprogramm -- beim Speichern genauso wie beim Loeschen."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    marker = tmp_path / "MARKER"
    root = tmp_path / "repo"
    opened = ScriptRepo(root)
    _plant_after_open(root, marker)
    assert sorted(p.name for p in (root / ".git" / "hooks").iterdir()) == ["commit-msg", "post-commit", "pre-commit"]
    opened.save(_meta(), "echo hi\n", commit_message="neu")
    assert not marker.exists(), "ein Programm lief beim Speichern"
    opened.save(_meta(), "echo ho\n", commit_message="nochmal")
    assert opened.history("s1")[0]["message"] == "nochmal"
    assert opened.delete("s1") is True
    assert not marker.exists(), "ein Programm lief beim Loeschen"


@pytest.mark.skipif(sys.platform == "win32", reason="Hooks und Filter brauchen eine POSIX-Shell")
def test_planted_hooks_would_run_in_a_plain_dulwich_repo(tmp_path, monkeypatch):
    """Gegenprobe zum Test davor: ohne `_SafeRepo` starten die eingelegten Hooks. Wird dieser Test rot,
    kennt dulwich den Weg nicht mehr, und `test_even_without_cleanup_nothing_runs` sagt nichts mehr aus."""
    from dulwich import porcelain
    from dulwich.repo import Repo

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    marker = tmp_path / "MARKER"
    root = tmp_path / "repo"
    ScriptRepo(root)
    _plant_after_open(root, marker)
    (root / "datei").write_text("x\n", encoding="utf-8")
    repo = Repo(str(root))
    try:
        porcelain.add(repo, paths=[str(root / "datei")])
        author = b"Test <test@example.invalid>"
        porcelain.commit(repo, message=b"x", author=author, committer=author, no_verify=True, sign=False)
    finally:
        repo.close()
    assert marker.exists(), "dulwich fuehrt den eingelegten post-commit-Hook nicht mehr aus"


def _plant_after_open(root: Path, marker: Path) -> None:
    git = root / ".git"
    git.joinpath("config").write_text(
        f'[core]\n\trepositoryformatversion = 0\n[filter "evil"]\n\tclean = touch {marker}\n'
        f'[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = {git / "evil-gpg"}\n',
        encoding="utf-8",
    )
    (root / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    git.joinpath("hooks").mkdir(exist_ok=True)
    for name in ("pre-commit", "commit-msg", "post-commit"):
        hook = git / "hooks" / name
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
        hook.chmod(0o755)
    gpg = git / "evil-gpg"
    gpg.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    gpg.chmod(0o755)


def test_opening_replaces_a_foreign_git_config_and_drops_redirects(tmp_path):
    root = tmp_path / "repo"
    ScriptRepo(root)
    (root / ".git" / "config").write_text("[include]\n\tpath = /etc/hostname\n", encoding="utf-8")
    (root / ".git" / "commondir").write_text("../elsewhere\n", encoding="utf-8")
    (root / ".git" / "objects" / "info").mkdir(parents=True, exist_ok=True)
    (root / ".git" / "objects" / "info" / "alternates").write_text("/tmp\n", encoding="utf-8")
    ScriptRepo(root)
    text = (root / ".git" / "config").read_text(encoding="utf-8")
    assert "include" not in text and "[core]" in text
    assert not (root / ".git" / "commondir").exists()
    assert not (root / ".git" / "objects" / "info" / "alternates").exists()


def test_a_git_file_instead_of_a_directory_is_replaced_by_a_fresh_repo(tmp_path):
    root = tmp_path / "repo"
    other = tmp_path / "anderswo"
    other.mkdir()
    root.mkdir()
    (root / ".git").write_text(f"gitdir: {other}\n", encoding="utf-8")
    repo = ScriptRepo(root)
    assert (root / ".git").is_dir()
    repo.save(_meta(), "echo\n", commit_message="x")
    assert list(other.iterdir()) == []


def test_a_repo_shape_in_the_work_tree_is_never_opened_instead_of_dot_git(tmp_path, monkeypatch):
    """Fehlt `.git/objects` (eine Sicherung bringt keine leeren Ordner mit), nahm dulwich den
    Arbeitsordner selbst als Repo, sobald dort `objects/` und `refs/` liegen -- samt `config`
    (Filter, `core.worktree`) und `info/attributes` aus dem Arbeitsordner."""
    from dulwich import porcelain
    from dulwich.repo import Repo

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    marker = tmp_path / "MARKER"
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
    for sub in ("objects/info", "objects/pack", "refs/heads", "refs/tags", "info"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
    (root / "config").write_text(
        f'[core]\n\trepositoryformatversion = 0\n\tbare = false\n\tworktree = .\n[filter "evil"]\n\tclean = touch {marker}\n',
        encoding="utf-8",
    )
    (root / "info" / "attributes").write_text("* filter=evil\n", encoding="utf-8")

    opened = ScriptRepo(root)
    opened.save(_meta(), "echo hi\n", commit_message="neu")
    assert opened.history("s1")[0]["message"] == "neu"
    # Auch ein gewoehnliches dulwich-Repo findet danach `.git` und nicht den Arbeitsordner.
    plain = Repo(str(root))
    try:
        assert Path(plain.controldir()) == root / ".git"
        (root / "s1" / "x").write_text("x\n", encoding="utf-8")
        porcelain.add(plain, paths=[str(root / "s1" / "x")])
    finally:
        plain.close()
    assert not marker.exists()


def test_a_restored_repo_without_commits_can_save_again(tmp_path):
    """Ein Repo ohne Commit hat nur leere Ordner und `HEAD`; die Sicherung bringt davon nur `HEAD` mit."""
    root = tmp_path / "repo"
    ScriptRepo(root)
    for path in sorted((root / ".git").iterdir()):
        if path.name != "HEAD":
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    opened = ScriptRepo(root)
    opened.save(_meta(), "echo hi\n", commit_message="neu")
    assert [entry["message"] for entry in opened.history("s1")] == ["neu"]


def test_a_missing_head_keeps_the_history(tmp_path):
    root = tmp_path / "repo"
    first = ScriptRepo(root)
    first.save(_meta(), "echo 1\n", commit_message="eins")
    first.save(_meta(), "echo 2\n", commit_message="zwei")
    (root / ".git" / "HEAD").unlink()
    opened = ScriptRepo(root)
    opened.save(_meta(), "echo 3\n", commit_message="drei")
    assert [entry["message"] for entry in opened.history("s1")] == ["drei", "zwei", "eins"]
