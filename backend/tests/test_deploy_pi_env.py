"""scripts/deploy_pi.sh: DNS-Werte neu (`NODVARD_DECK_DNS_*`) oder alt (`LATTICE_DNS_*`).

Das Skript laeuft echt per Bash in einem Temp-Repository; `ssh`, `docker` und `curl`
sind Attrappen im PATH (kein Netzwerk, kein Docker). Geprueft wird, was auf dem Ziel in
`deploy/.env` landet: immer BEIDE Namen, damit Compose (neu, mit Rueckfall auf alt) und
ein Rueckweg aufs alte Image dieselben Werte sehen.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy_pi.sh"

# Unter Windows startet "bash" immer die WSL-Bash aus System32 (oft ohne Distribution),
# nicht Git Bash -- das Skript laeuft dort also nicht; das Ziel ist ohnehin Linux.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None or shutil.which("git") is None or not SCRIPT.exists(),
    reason="bash, git und scripts/deploy_pi.sh werden gebraucht (unter Windows nicht: dort startet bash die WSL)",
)

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "Test Autor",
    "GIT_AUTHOR_EMAIL": "autor@example.org",
    "GIT_COMMITTER_NAME": "Test Autor",
    "GIT_COMMITTER_EMAIL": "autor@example.org",
}

SSH_STUB = """#!/usr/bin/env bash
# Attrappe: letztes Argument ist der Befehl auf dem Ziel.
cmd="${@: -1}"
echo "$cmd" >> "$STUB_DIR/ssh_commands"
case "$cmd" in
  *"cat > "*"/.env"*) cat > "$STUB_DIR/env_written" ;;
  *"curl -s"*) echo '{"status":"ok"}' ;;
  *"grep -c Traceback"*) echo 0 ;;
  *"hostname"*) echo pi-attrappe ;;
  *) cat > /dev/null ;;
esac
exit 0
"""

DOCKER_STUB = """#!/usr/bin/env bash
case "$*" in
  *Architecture*) echo arm64 ;;
esac
exit 0
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def deploy(tmp_path: Path):
    """Temp-Repository mit Kopie des Skripts; liefert `run(env, env_deploy=None)`."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "deploy").mkdir()
    (repo / "deploy" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    shutil.copy(SCRIPT, repo / "scripts" / "deploy_pi.sh")
    env = {**os.environ, **GIT_ENV}
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "x"]):
        assert subprocess.run(cmd, cwd=repo, env=env, capture_output=True, check=False).returncode == 0

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _executable(stubs / "ssh", SSH_STUB)
    _executable(stubs / "docker", DOCKER_STUB)
    state = tmp_path / "state"
    state.mkdir()

    def run(variables: dict[str, str], env_deploy: str | None = None) -> tuple[subprocess.CompletedProcess, str | None]:
        if env_deploy is not None:
            (repo / "deploy" / ".env.deploy").write_text(env_deploy, encoding="utf-8")
        clean = {k: v for k, v in env.items() if not k.upper().startswith(("NODVARD_DECK_", "LATTICE_", "PI_HOST"))}
        clean.update(PATH=f"{stubs}{os.pathsep}{clean['PATH']}", STUB_DIR=str(state), PI_HOST="admin@192.0.2.1", **variables)
        result = subprocess.run(
            ["bash", str(repo / "scripts" / "deploy_pi.sh"), "--no-build"],
            cwd=repo,
            env=clean,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        written = state / "env_written"
        return result, written.read_text(encoding="utf-8") if written.exists() else None

    return run


def _pairs(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if line)


def test_only_old_names_are_written_under_both_names(deploy):
    result, written = deploy({"LATTICE_DNS_1": "192.0.2.10", "LATTICE_DNS_2": "192.0.2.11"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEPLOY-OK" in result.stdout
    assert _pairs(written) == {
        "NODVARD_DECK_DNS_1": "192.0.2.10",
        "NODVARD_DECK_DNS_2": "192.0.2.11",
        "LATTICE_DNS_1": "192.0.2.10",
        "LATTICE_DNS_2": "192.0.2.11",
    }


def test_only_new_names_are_written_under_both_names(deploy):
    result, written = deploy({"NODVARD_DECK_DNS_1": "198.51.100.10", "NODVARD_DECK_DNS_2": "198.51.100.11"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert _pairs(written) == {
        "NODVARD_DECK_DNS_1": "198.51.100.10",
        "NODVARD_DECK_DNS_2": "198.51.100.11",
        "LATTICE_DNS_1": "198.51.100.10",
        "LATTICE_DNS_2": "198.51.100.11",
    }


def test_new_name_wins_when_both_are_set(deploy):
    result, written = deploy({"LATTICE_DNS_1": "192.0.2.10", "NODVARD_DECK_DNS_1": "198.51.100.10"})
    assert result.returncode == 0, result.stdout + result.stderr
    pairs = _pairs(written)
    assert pairs["NODVARD_DECK_DNS_1"] == pairs["LATTICE_DNS_1"] == "198.51.100.10"
    # Nicht gesetzter zweiter Resolver: bisheriger Standard, unter beiden Namen.
    assert pairs["NODVARD_DECK_DNS_2"] == pairs["LATTICE_DNS_2"] == "1.0.0.1"


def test_old_name_in_env_deploy_file(deploy):
    result, written = deploy({}, env_deploy="LATTICE_DNS_1=192.0.2.20\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _pairs(written) == {
        "NODVARD_DECK_DNS_1": "192.0.2.20",
        "NODVARD_DECK_DNS_2": "1.0.0.1",
        "LATTICE_DNS_1": "192.0.2.20",
        "LATTICE_DNS_2": "1.0.0.1",
    }


def test_new_name_in_env_deploy_file_and_environment_wins(deploy):
    result, written = deploy(
        {"NODVARD_DECK_DNS_1": "198.51.100.30"},
        env_deploy="NODVARD_DECK_DNS_1=198.51.100.99\nLATTICE_DNS_2=192.0.2.21\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    pairs = _pairs(written)
    assert pairs["NODVARD_DECK_DNS_1"] == pairs["LATTICE_DNS_1"] == "198.51.100.30"
    assert pairs["NODVARD_DECK_DNS_2"] == pairs["LATTICE_DNS_2"] == "192.0.2.21"


def test_nothing_is_written_without_any_dns_value(deploy):
    result, written = deploy({})
    assert result.returncode == 0, result.stdout + result.stderr
    assert written is None
