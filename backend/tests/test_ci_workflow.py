"""`.github/workflows/ci.yml`: Rechte des Laufs.

Die CI checkt nur aus, baut und testet. Das `GITHUB_TOKEN`, das GitHub jedem Lauf gibt, darf darum nur lesen: fuer den
ganzen Workflow festgelegt, damit auch ein spaeter dazukommender Job nicht still mit den Standardrechten des
Repositorys laeuft (die koennen Schreibrechte sein). Die Tests lesen nur die YAML-Struktur.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def test_the_token_may_only_read_for_the_whole_workflow():
    assert _workflow()["permissions"] == {"contents": "read"}


def test_no_job_asks_for_more():
    for name, job in _workflow()["jobs"].items():
        permissions = job.get("permissions", {})
        assert permissions in ({}, {"contents": "read"}), (name, permissions)


def test_no_secrets_in_the_ci():
    assert re.findall(r"\bsecrets\b", CI.read_text(encoding="utf-8")) == []


def _root_step_files() -> set[str]:
    """Testdateien, die der Lauf als root in der CI mit `-m needs_root` ausführt (Schritte mit `sudo ... -m needs_root`)."""
    text = CI.read_text(encoding="utf-8")
    steps = re.findall(r"^\s*- run: (sudo .*-m needs_root\b.*)$", text, re.MULTILINE)
    return {name for step in steps for name in re.findall(r"backend/tests/(test_\w+\.py)", step)}


def test_every_file_with_the_needs_root_marker_runs_in_the_root_step_of_the_ci():
    tests = CI.parents[2] / "backend" / "tests"
    in_ci = _root_step_files()
    assert in_ci, "kein Schritt mit sudo und -m needs_root in der CI"
    used = {p.name for p in tests.glob("test_*.py") if p.name != Path(__file__).name and "pytest.mark.needs_root" in p.read_text(encoding="utf-8")}
    assert used <= in_ci, f"Dateien mit dem Marker needs_root, die die CI nie als root ausführt: {sorted(used - in_ci)}"


_ROOT_ONLY = re.compile(r"getuid\(\)\)?\s*!=\s*0|geteuid\(\)\s*!=\s*0")


# Bekannte Ausnahmen, bewusst und einzeln: Datei::Test -> Grund. Jeder Eintrag muss noch zutreffen (sonst rot), damit die
# Liste nicht verstaubt. Neue Tests gehören nicht hierher, sondern markiert und in den root-Schritt.
_KNOWN_ROOT_GAPS = {
    "test_ext_docker_editor.py::test_real_sftp_keeps_the_owner":
        "Besitzer über echtes SFTP übertragen (funktional). 1.0: in root-Schritt aufnehmen",
    "test_ext_docker_git_apply.py::test_the_fixed_scripts_refuse_unreadable_files_and_folders_as_targets":
        "Git-Apply-Skripte mit setpriv (funktional). 1.0: in root-Schritt aufnehmen",
    "test_deploy_switch.py::test_restore_names_a_file_the_caller_cannot_read":
        "Zweig im Testkörper, der nur ohne root läuft. 1.0: in root-Schritt aufnehmen",
}


def _root_only_tests_without_marker_or_ci_step() -> list[str]:
    """Alle Tests in backend/tests, die nur als root laufen (Skip bei `getuid() != 0`, direkt am Test, an einem
    Parameter oder über eine Modulvariable), ohne Marker `needs_root` oder in einer Datei, die der root-Schritt nie nennt."""
    import ast

    tests = CI.parents[2] / "backend" / "tests"
    in_ci = _root_step_files()
    problems = []
    seen_gaps: set[str] = set()
    for path in sorted(tests.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        lines = source.splitlines()
        root_vars = {
            target.id
            for node in tree.body if isinstance(node, ast.Assign) and _ROOT_ONLY.search(ast.get_source_segment(source, node) or "")
            for target in node.targets if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")):
                continue
            decorators = " ".join(ast.get_source_segment(source, d) or "" for d in node.decorator_list)
            names = set(re.findall(r"\w+", decorators))
            body = " ".join(lines[node.body[0].lineno - 1:node.end_lineno])
            if not (_ROOT_ONLY.search(decorators) or names & root_vars or _ROOT_ONLY.search(body)):
                continue
            key = f"{path.name}::{node.name}"
            if key in _KNOWN_ROOT_GAPS:
                seen_gaps.add(key)
                continue
            if "needs_root" not in decorators:
                problems.append(f"{path.name}::{node.name}: ohne Marker needs_root")
            elif path.name not in in_ci:
                problems.append(f"{path.name}::{node.name}: Datei fehlt im root-Schritt der CI")
    problems += [f"{key}: Ausnahme trifft nicht mehr zu" for key in sorted(set(_KNOWN_ROOT_GAPS) - seen_gaps)]
    return problems


def test_root_only_tests_carry_the_marker_and_run_in_the_root_step():
    """`-m needs_root` wählt nur markierte Tests aus. Ein Test, der ohne root übersprungen wird, aber den Marker nicht
    trägt oder dessen Datei der root-Schritt nicht nennt, läuft in der CI nie."""
    assert not _root_only_tests_without_marker_or_ci_step()
