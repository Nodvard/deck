"""`nodvard_deck_ext_scripts.repo.ScriptRepo` -- das Git-gestuetzte Skript-Repository.
Reine Dateisystem-/dulwich-Logik, kein `ctx`
noetig -- wie `nodvard_deck_ext_nexus_soc.parsing` bewusst isoliert testbar gehalten.
"""

from __future__ import annotations

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
