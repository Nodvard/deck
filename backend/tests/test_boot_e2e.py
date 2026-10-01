"""Ende zu Ende, OHNE Docker: echte Prozesse, echtes Alembic, echte Dateien.

1. **Kaputte Migration**: Eine absichtlich kaputte Revision (sie aendert Daten und wirft dann) stoppt `nodvard_deck.boot`.
   Danach: Rueckgabe 1, die Datenbank ist wieder GENAU der Stand von vor der Migration, der Grund (bereinigt) steht
   in `state.json`, und `deploy/entrypoint.sh` startet die Notseite: `GET /api/v1/health` -> 503 `{"status":"rescue"}`.
2. **Update und Rueckweg**: Eine neue (gute) Revision wird migriert (Kopie vorher), die neue Version startet nie ->
   das Image wird auf die alte Version zurueckgestellt -> die Kopie kommt automatisch zurueck. Hat die neue Version
   dagegen gearbeitet (`started_ok`), zeigt die alte nur die Notseite; "Stand vor dem Update wiederherstellen" mit
   dem Notfallcode merkt den Rueckweg vor, der naechste Start spielt ihn ein.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from nodvard_deck.core import bootstate
from nodvard_deck.core.backup import premigrate
from nodvard_deck.migrate import alembic_config
from restore_helpers import REPO_ROOT

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Prozesse und Signale wie im Container (POSIX)")

SECRET_TEXT = "absichtlich kaputt: /home/nico/geheim postgresql://admin:Sup3rGeheim@10.0.0.5/deck"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def instance_env(root: Path) -> dict[str, str]:
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    (root / "erweiterungen").mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NODVARD_DECK_", "LATTICE_"))}
    env.update({
        "PYTHONPATH": os.pathsep.join([str(REPO_ROOT / "backend" / "src"), str(REPO_ROOT / "sdk" / "python")]),
        "NODVARD_DECK_ENV": "dev",
        "NODVARD_DECK_DATA_DIR": str(data),
        "NODVARD_DECK_DATABASE_URL": f"sqlite+aiosqlite:///{data / 'lattice.db'}",
        "NODVARD_DECK_MASTER_KEY_PATH": str(data / "master.key"),
        "NODVARD_DECK_VAULT_KEYRING_PATH": str(data / "vault_keyring.json"),
        "NODVARD_DECK_JWT_SECRET_PATH": str(data / "jwt_secret.key"),
        "NODVARD_DECK_EXT_DATA_DIR": str(data / "ext"),
        "NODVARD_DECK_EXTENSIONS_DIR": str(root / "erweiterungen"),
        "PYTHONUNBUFFERED": "1",
    })
    return env


def run_boot(env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "nodvard_deck.boot"], cwd=cwd, env=env, capture_output=True, text=True, timeout=240, check=False)


def dump(path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def make_repo(root: Path, revisions: dict[str, str]) -> Path:
    """Ein Arbeitsordner wie das Repository, mit zusaetzlichen Revisionen (Dateiname -> Inhalt) im Kern-Zweig."""
    repo = root / "repo"
    (repo / "backend").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "backend" / "alembic.ini", repo / "backend" / "alembic.ini")
    shutil.copytree(REPO_ROOT / "backend" / "migrations", repo / "backend" / "migrations", ignore=shutil.ignore_patterns("__pycache__"))
    for ext in sorted((REPO_ROOT / "extensions").iterdir()):
        versions = ext / "migrations" / "versions"
        if versions.is_dir():
            target = repo / "extensions" / ext.name / "migrations"
            target.mkdir(parents=True)
            (target / "versions").symlink_to(versions)
    for name, text in revisions.items():
        (repo / "backend" / "migrations" / "versions" / name).write_text(text, encoding="utf-8")
    return repo


def core_head() -> str:
    from alembic.script import ScriptDirectory

    cfg, _ = alembic_config(repo_root=REPO_ROOT)
    script = ScriptDirectory.from_config(cfg)
    heads = [h for h in script.get_heads() if "backend" + os.sep + "migrations" in str(script.get_revision(h).path)]
    assert len(heads) == 1, heads
    return heads[0]


def revision_text(revision: str, body: str) -> str:
    return (
        f'"""Test-Revision"""\nfrom alembic import op\nrevision = "{revision}"\ndown_revision = "{core_head()}"\n'
        f"branch_labels = None\ndepends_on = None\n\ndef upgrade():\n{body}\n\ndef downgrade():\n    pass\n"
    )


BROKEN = revision_text("bad000000001", f"""    op.execute("CREATE TABLE halb_migriert (x INTEGER)")
    op.execute("UPDATE hosts SET name = 'zerstoert'")
    raise RuntimeError("{SECRET_TEXT}")""")
GOOD = revision_text("good00000001", """    op.execute("CREATE TABLE neu_angelegt (x INTEGER)")""")


def real_database(env: dict[str, str]) -> Path:
    """Eine Datenbank, wie sie die echten Migrationen dieses Repos anlegen -- samt Konto und Servern."""
    result = run_boot(env, REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    db = Path(env["NODVARD_DECK_DATA_DIR"]) / "lattice.db"
    from nodvard_deck.models import Host, User
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL
    from sqlalchemy.orm import Session

    engine = create_engine(URL.create("sqlite", database=str(db)))
    with Session(engine) as session:
        session.add(User(username="nico", password_hash="x", is_owner=True, is_active=True))
        session.add(Host(name="server1", address="10.0.0.1"))
        session.commit()
    engine.dispose()
    return db


def get(port: int, path: str, headers: dict | None = None) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path, headers=headers or {})
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def wait_for(port: int, proc: subprocess.Popen, path: str = "/api/v1/health", timeout: float = 90) -> tuple[int, bytes]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"Prozess endete mit {proc.returncode}: {proc.stderr.read() if proc.stderr else ''}")
        try:
            return get(port, path)
        except OSError:
            time.sleep(0.2)
    raise AssertionError("keine Antwort")


def post(port: int, path: str, body: str = "", cookie: str | None = None) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        conn.request("POST", path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        conn.close()


def start_rescue(env: dict[str, str], port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "nodvard_deck.rescue", "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=REPO_ROOT,
    )


def unlock(port: int, data: Path) -> str:
    code = (data / ".boot" / "rescue_code.txt").read_text().strip()
    status, headers, _ = post(port, "/rescue/unlock", f"code={code}")
    assert status == 303, status
    return headers["set-cookie"].split(";")[0]


# ---------------------------------------------------------------------------
# 1. Kaputte Migration -> Notseite
# ---------------------------------------------------------------------------


def test_a_broken_migration_leaves_the_database_as_before_and_ends_in_the_rescue_page(tmp_path):
    env = instance_env(tmp_path)
    data = tmp_path / "data"
    db = real_database(env)
    before = dump(db)
    repo = make_repo(tmp_path, {"zz_bad.py": BROKEN})
    bootstate.write_state(data, {"app_version": "0.5.0", "started_ok": True, "db_heads": premigrate.read_live(db).heads})

    result = run_boot(env, repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert dump(db) == before, "die Datenbank ist wieder GENAU der Stand von vor der Migration (nichts 'zerstoert', keine halbe Tabelle)"
    assert not os.path.lexists(str(db) + "-wal") or os.path.getsize(str(db) + "-wal") == 0
    state = bootstate.read_state(data)
    failure = state["failure"]
    assert failure["kind"] == "migration_failed" and "zurückgesetzt" in failure["reason"]
    text = "\n".join(failure["log"]) + failure["reason"]
    assert "absichtlich kaputt" in text, "die Ursache steht im Protokoll"
    for secret in ("Sup3rGeheim", "/home/nico", str(data), str(tmp_path)):
        assert secret not in text, secret
    assert state["last_migration"]["state"] == "reverted" and state["started_ok"] is True

    # Genau das, was der Entrypoint danach tut: die Notseite auf dem Port der Anwendung.
    port = free_port()
    proc = start_rescue(env, port)
    try:
        status, body = wait_for(port, proc)
        assert status == 503 and json.loads(body) == {"status": "rescue"} and b'"ok"' not in body
        status, page = get(port, "/")
        assert status == 503 and b"GEHEIM" not in page and b"halb_migriert" not in page and b"migration_failed" not in page, "ohne Code nichts Genaues"
        cookie = unlock(port, data)
        status, page = get(port, "/", {"Cookie": cookie})
        assert status == 503 and "Das Update der Datenbank ist gescheitert".encode() in page and b"Sup3rGeheim" not in page
        status, log = get(port, "/rescue/log", {"Cookie": cookie})
        assert status == 200 and b"absichtlich kaputt" in log and b"Sup3rGeheim" not in log
        status, _, _ = post(port, "/rescue/retry", cookie=cookie)
        assert status == 202
        assert proc.wait(15) == 75, "Neustart gewuenscht"
    finally:
        if proc.poll() is None:
            proc.kill()

    # Nach "neu versuchen" laeuft boot noch einmal -- mit derselben Datenbank, ohne Aenderung.
    again = run_boot(env, repo)
    assert again.returncode == 1 and dump(db) == before
    # Ist der Fehler behoben (die Revision ist weg), klappt der Start und der alte Fehler ist vergessen.
    fixed = run_boot(env, REPO_ROOT)
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert "failure" not in bootstate.read_state(data) and not (data / ".boot" / "rescue_code.txt").exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="braucht eine POSIX-Shell")
def test_the_real_entrypoint_script_ends_in_the_rescue_page_and_a_lock_blocks_a_second_boot(tmp_path):
    """`deploy/entrypoint.sh` mit echtem Python: boot scheitert -> Notseite auf dem Port aus dem Startbefehl."""
    env = instance_env(tmp_path)
    data = tmp_path / "data"
    db = real_database(env)
    before = dump(db)
    repo = make_repo(tmp_path, {"zz_bad.py": BROKEN})

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to(sys.executable)
    (bin_dir / "id").write_text("#!/bin/sh\n[ \"$*\" = \"-u\" ] && echo 1000 || exit 2\n")
    (bin_dir / "id").chmod(0o755)
    script = tmp_path / "entrypoint.sh"
    text = (REPO_ROOT / "deploy" / "entrypoint.sh").read_text(encoding="utf-8")
    script.write_text(text.replace("/app/data", str(data)).replace("cd /app", f"cd {repo}"), encoding="utf-8")
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    port = free_port()
    proc = subprocess.Popen(["sh", str(script), "uvicorn", "nodvard_deck.main:app", "--host", "127.0.0.1", "--port", str(port)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        status, body = wait_for(port, proc, timeout=120)
        assert status == 503 and json.loads(body) == {"status": "rescue"}
        # Ein zweites boot (compose run neben dem laufenden Container) fasst nichts an: Sperre.
        snapshot = dump(db)
        other = run_boot(env, REPO_ROOT)
        assert other.returncode == 75 and "anderen Prozess" in other.stdout
        assert dump(db) == snapshot == before
        # docker stop -> SIGTERM an das Skript: die Notseite endet ordentlich.
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    assert not bootstate.is_locked(data)


# ---------------------------------------------------------------------------
# 2. Update und Rueckweg
# ---------------------------------------------------------------------------


KILL_DURING_FIRST_MIGRATION = """
import os, sys
from alembic.operations import Operations
count = {"n": 0}
for name in ("create_table", "create_index"):
    real = getattr(Operations, name)
    def wrapped(self, *args, _real=real, **kwargs):
        count["n"] += 1
        if count["n"] == 5:
            os._exit(137)  # wie SIGKILL: nichts wird mehr aufgeraeumt
        return _real(self, *args, **kwargs)
    setattr(Operations, name, wrapped)
from nodvard_deck import boot
sys.exit(boot.main())
"""


def test_a_fresh_installation_whose_first_migration_was_killed_starts_on_the_next_try(tmp_path):
    # Fund: Ein harter Abbruch mitten in der ersten Migration liess angelegte Tabellen ohne `alembic_version` zurueck;
    # jeder weitere Start scheiterte an "table already exists" und blieb auf der Notseite.
    env = instance_env(tmp_path)
    data = tmp_path / "data"
    killed = subprocess.run([sys.executable, "-c", KILL_DURING_FIRST_MIGRATION], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=240, check=False)
    assert killed.returncode == 137, killed.stdout + killed.stderr
    db = data / "lattice.db"
    assert premigrate.read_live(db).has_data, "die halbe Migration hat schon Tabellen angelegt"
    again = run_boot(env, REPO_ROOT)
    assert again.returncode == 0, again.stdout + again.stderr
    assert premigrate.read_live(db).heads, "die Migration ist durchgelaufen"
    state = bootstate.read_state(data)
    assert state["last_migration"]["state"] == "ok" and "failure" not in state
    assert len(list((data / "restore").glob("replaced-*"))) == 1, "der halbe Stand liegt beiseite"
    assert run_boot(env, REPO_ROOT).returncode == 0


def test_update_then_back_to_the_old_version_restores_the_copy_automatically_when_the_new_one_never_started(tmp_path):
    env = instance_env(tmp_path)
    data = tmp_path / "data"
    db = real_database(env)
    old_dump = dump(db)
    old_heads = premigrate.read_live(db).heads
    new_repo = make_repo(tmp_path, {"zz_good.py": GOOD})

    # Update auf die neue Version (0.9.0 im Zustand): Kopie vorher, Migration, "noch nie gestartet".
    updated = run_boot(env, new_repo)
    assert updated.returncode == 0, updated.stdout + updated.stderr
    assert "neu_angelegt" in "".join(dump(db))
    state = bootstate.read_state(data)
    assert state["started_ok"] is False and state["last_migration"]["state"] == "ok"
    copies = premigrate.list_copies(data)
    assert len(copies) == 1 and state["last_migration"]["copy"] == copies[0]["name"]
    assert dump(data / "backups" / "vor-update" / copies[0]["name"]) == old_dump, "die Kopie ist der Stand VOR dem Update"

    # Die neue Version startet nie (Absturz). Das Image wird auf die alte Version zurueckgestellt:
    back = run_boot(env, REPO_ROOT)
    assert back.returncode == 0, back.stdout + back.stderr
    assert dump(db) == old_dump, "Daten wie vor dem Update"
    assert premigrate.read_live(db).heads == old_heads
    assert "neu_angelegt" not in "".join(dump(db))
    assert bootstate.read_state(data)["last_migration"]["state"] == "reverted"
    kept = list((data / "restore").glob("replaced-*"))
    assert len(kept) == 1 and "neu_angelegt" in "".join(dump(kept[0] / "lattice.db")), "der neuere Stand liegt nur beiseite"


def test_work_after_an_update_survives_going_back_even_if_started_ok_was_never_written(tmp_path):
    # Fund: Die neue Version laeuft und es wird gearbeitet, aber `started_ok` liess sich beim Start nicht schreiben
    # (z. B. Speicher kurz voll). Beim Zurueckstellen auf die alte Version war die Arbeit ohne Rueckfrage geloescht.
    env = instance_env(tmp_path)
    data = tmp_path / "data"
    db = real_database(env)
    old_dump = dump(db)
    assert run_boot(env, make_repo(tmp_path, {"zz_good.py": GOOD})).returncode == 0
    assert bootstate.read_state(data)["started_ok"] is False  # so bleibt es, wenn das Festhalten scheitert
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO neu_angelegt VALUES (42)")
    conn.commit()
    conn.close()
    newer = dump(db)

    back = run_boot(env, REPO_ROOT)
    assert back.returncode == 0, back.stdout + back.stderr
    assert dump(db) == old_dump
    kept = list((data / "restore").glob("replaced-*"))
    assert len(kept) == 1 and dump(kept[0] / "lattice.db") == newer, "die Arbeit seit dem Update liegt 30 Tage beiseite"


def test_after_a_good_start_the_old_version_only_shows_the_rescue_page_and_the_explicit_rollback_works(tmp_path):
    env = instance_env(tmp_path)
    data = tmp_path / "data"
    db = real_database(env)
    old_dump = dump(db)
    new_repo = make_repo(tmp_path, {"zz_good.py": GOOD})
    assert run_boot(env, new_repo).returncode == 0
    assert bootstate.mark_started_ok(data), "die neue Version ist erfolgreich gestartet ..."
    conn = sqlite3.connect(db)  # ... und es wurde darin gearbeitet
    conn.execute("INSERT INTO neu_angelegt VALUES (42)")
    conn.commit()
    conn.close()
    newer = dump(db)

    # Zurueck auf die alte Version: nichts wird automatisch verworfen.
    refused = run_boot(env, REPO_ROOT)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert dump(db) == newer, "die Nutzerdaten sind unangetastet"
    failure = bootstate.read_state(data)["failure"]
    assert failure["kind"] == "newer_data" and failure["rollback"]["started_ok"] is True

    # Notseite: mit dem Notfallcode den Rueckweg vormerken.
    port = free_port()
    proc = start_rescue(env, port)
    try:
        assert wait_for(port, proc)[0] == 503
        cookie = unlock(port, data)
        page = get(port, "/", {"Cookie": cookie})[1]
        assert b"/rescue/rollback" in page and "geht dabei verloren".encode() in page
        status, _, _ = post(port, "/rescue/rollback", cookie=cookie)
        assert status == 202 and proc.wait(15) == 75
    finally:
        if proc.poll() is None:
            proc.kill()
    assert bootstate.read_rollback(data)["copy"] == failure["rollback"]["copy"]

    # Der Neustart: boot spielt die Kopie ein.
    restored = run_boot(env, REPO_ROOT)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert dump(db) == old_dump
    assert bootstate.read_rollback(data) is None and "failure" not in bootstate.read_state(data)
    kept = list((data / "restore").glob("replaced-*"))
    assert len(kept) == 1 and dump(kept[0] / "lattice.db") == newer, "die neueren Daten bleiben als alter Stand liegen"
    _ = re
