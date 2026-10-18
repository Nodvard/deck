"""Image nur mit einigen Modulen: `deploy/select_extensions.sh` und das Build-Argument `NODVARD_DECK_EXTENSIONS`."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "select_extensions.sh"
DOCKERFILE = ROOT / "deploy" / "Dockerfile"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="braucht eine POSIX-Shell")


def _tree(tmp_path: Path) -> Path:
    folder = tmp_path / "extensions"
    for name in ("_shared", "docker", "documents", "network"):
        (folder / name / "frontend").mkdir(parents=True)
        if name != "_shared":
            (folder / name / "extension.toml").write_text(f'[extension]\nid = "{name}"\n', encoding="utf-8")
    (folder / "tsconfig.json").write_text("{}", encoding="utf-8")
    return folder


def _select(modules: str, folder: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(SCRIPT), modules, str(folder)], capture_output=True, text=True, check=False)


def _left(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


@pytest.mark.parametrize(("modules", "left"), [
    ("", ["_shared", "docker", "documents", "network", "tsconfig.json"]),
    ("docker", ["_shared", "docker", "tsconfig.json"]),
    ("docker,network", ["_shared", "docker", "network", "tsconfig.json"]),
    ("docker,,", ["_shared", "docker", "tsconfig.json"]),
])
def test_keeps_the_chosen_modules_and_the_kit(tmp_path, modules, left):
    folder = _tree(tmp_path)
    done = _select(modules, folder)
    assert done.returncode == 0, done.stderr
    assert _left(folder) == left


@pytest.mark.parametrize(("modules", "error"), [
    ("nichtda", "Das Modul nichtda gibt es nicht"),
    ("_shared", "Ungültige Modulliste"),
    ("Docker", "Ungültige Modulliste"),
    ("../docker", "Ungültige Modulliste"),
    ("docker network", "Ungültige Modulliste"),
    ("docker;rm", "Ungültige Modulliste"),
    (",", "Die Modulliste nennt kein Modul"),
    (",,,", "Die Modulliste nennt kein Modul"),
])
def test_a_wrong_list_stops_the_build_and_removes_nothing(tmp_path, modules, error):
    folder = _tree(tmp_path)
    done = _select(modules, folder)
    assert done.returncode == 1 and error in done.stderr
    assert _left(folder) == ["_shared", "docker", "documents", "network", "tsconfig.json"]


def test_the_docker_module_can_be_chosen_from_the_repository(tmp_path):
    folder = tmp_path / "extensions"
    shutil.copytree(ROOT / "extensions", folder, ignore=shutil.ignore_patterns("node_modules", "__pycache__", "dist"))
    done = _select("docker", folder)
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in folder.iterdir() if p.is_dir()) == ["_shared", "docker"]


def test_the_script_is_copied_executable():
    assert 'COPY --chmod=755 deploy/select_extensions.sh /usr/local/bin/select_extensions.sh' in DOCKERFILE.read_text(encoding="utf-8")


def _ocr_step() -> str:
    """Der RUN-Schritt für Tesseract, mit `echo` statt `apt-get`."""
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    step = next(line for line in text.splitlines() if line.startswith("RUN case") and "tesseract-ocr" in line)
    return re.sub(r"apt-get update && .*? && rm -rf /var/lib/apt/lists/\* ;;", "echo ocr ;;", step.removeprefix("RUN "))


@pytest.mark.parametrize(("modules", "ocr"), [("", True), ("documents", True), ("docker,documents", True), ("docker", False)])
def test_tesseract_only_comes_with_the_documents_module(modules, ocr):
    done = subprocess.run(["sh", "-c", _ocr_step()], env={"NODVARD_DECK_EXTENSIONS": modules, "PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True, check=True)
    assert (done.stdout.strip() == "ocr") is ocr


def test_both_stages_know_the_argument():
    text = DOCKERFILE.read_text(encoding="utf-8")
    stages = re.split(r"^FROM ", text, flags=re.MULTILINE)[1:]
    assert len(stages) == 2
    for stage in stages:
        assert 'ARG NODVARD_DECK_EXTENSIONS=""' in stage
    build = stages[0]
    assert build.index("COPY extensions/ extensions/") < build.index('select_extensions.sh "$NODVARD_DECK_EXTENSIONS" extensions')
    assert build.index("select_extensions.sh \"$NODVARD_DECK_EXTENSIONS\"") < build.index("RUN cd frontend && npm run build")
    assert "select_extensions" not in stages[1]
    assert stages[1].index("tesseract-ocr") < stages[1].index("COPY deploy/constraints.txt"), "sonst läuft apt nach jeder Backend-Änderung"
