"""`.dockerignore`: Laufzeitdaten und Schluessel kommen in keinem Unterordner ins Image, alles Noetige schon.

Docker wertet Muster ohne `**/` nur vom obersten Ordner des Build-Kontexts aus. Wer Backend oder alembic aus
`backend/` heraus gestartet hat, hat dort `data/` mit Master-Key, Geheimnissen und Datenbank -- die darf kein
`COPY backend backend` mitnehmen. Der Abgleich unten bildet Dockers Regeln nach (`*` und `?` enden an `/`, `**/`
steht fuer beliebig viele Ordner, ein Muster trifft einen Pfad auch ueber einen Elternordner, `!` hebt auf).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERIGNORE = ROOT / ".dockerignore"


def _regex(pattern: str) -> re.Pattern[str]:
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


def _rules() -> list[tuple[bool, re.Pattern[str]]]:
    rules = []
    for raw in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        rules.append((negate, _regex(line.lstrip("!").strip("/"))))
    return rules


def ignored(path: str) -> bool:
    """Wird `path` (relativ zum Wurzelordner, mit `/`) aus dem Build-Kontext ausgeschlossen?"""
    parts = path.split("/")
    candidates = ["/".join(parts[: n + 1]) for n in range(len(parts))]
    result = False
    for negate, regex in _rules():
        if any(regex.match(c) for c in candidates):
            result = not negate
    return result


@pytest.mark.parametrize(
    "path",
    [
        "data/master.key",
        "backend/data/master.key",
        "backend/data/lattice.db",
        "backend/data/lattice.db-wal",
        "backend/data/lattice.db-shm",
        "backend/data/jwt_secret.key",
        "backend/data/vault_keyring.json",
        "backend/data/backups/vor-update/2026.ndbak",
        "backend/data/ext/documents/abc.pdf",
        "backend/.env",
        "backend/.env.local",
        "extensions/documents/data/master.key",
        "extensions/documents/.env",
        "sdk/python/nodvard_sdk/data/lattice.db",
        "backend/lattice.db",
        "backend/master.key",
        "backend/jwt_secret.key",
        "backend/jwt_secret",
        "backend/anderswo/schluessel.key",
        "backend/export.sqlite3",
        ".env",
        "lattice.db",
    ],
)
def test_runtime_data_and_secrets_are_excluded_in_every_folder(path):
    assert ignored(path), f"{path} landet sonst im Image"


@pytest.mark.parametrize(
    "path",
    [
        "backend/pyproject.toml",
        "backend/alembic.ini",
        "backend/src/nodvard_deck/main.py",
        "backend/migrations/env.py",
        "backend/migrations/versions/c3f1a7d92b64_beispiel.py",
        "sdk/python/pyproject.toml",
        "sdk/python/nodvard_sdk/__init__.py",
        "extensions/documents/extension.toml",
        "extensions/documents/settings.schema.json",
        "extensions/documents/frontend/dist/index.js",
        "frontend/package.json",
        "frontend/src/main.tsx",
        "deploy/constraints.txt",
        "deploy/entrypoint.sh",
        "THIRD_PARTY_LICENSES",
        "LICENSE",
    ],
)
def test_what_the_build_needs_is_not_excluded(path):
    assert not ignored(path), f"{path} wird im Build gebraucht"


def test_every_tracked_file_the_dockerfile_copies_stays_in_the_build_context():
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("kein Git-Checkout")
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8")
    copied = ("backend/", "sdk/python/", "extensions/", "frontend/")
    exact = {"THIRD_PARTY_LICENSES", "LICENSE", "deploy/constraints.txt", "deploy/entrypoint.sh"}
    # frontend/dist (gebaut, nicht eingecheckt) und node_modules baut die erste Stufe selbst.
    lost = [
        p for p in out.split("\0")
        if p and (p in exact or p.startswith(copied)) and ignored(p)
        and not p.startswith(("frontend/dist/", "frontend/node_modules/"))
        and "__pycache__" not in p and not p.endswith((".pyc", ".pyo"))
    ]
    assert lost == [], f"eingecheckte Dateien, die der Build braucht, wuerden ausgeschlossen: {lost[:10]}"
