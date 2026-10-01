"""Ende zu Ende, OHNE Docker: echte Prozesse, echte Migrationen, echte Dateien.

1. Eine Installation (`quelle`) mit Konto, Zugangsdaten im Vault (`secret`), Erweiterungsdaten und einer
   Anmeldung wird gestartet (`nodvard_deck.boot`, dann uvicorn) und ueber die ECHTEN Endpunkte gesichert.
2. Der Datenordner einer zweiten, ganz leeren Installation (`ziel`) wird frisch angelegt.
3. Assistent: hochladen, pruefen, vormerken -- mit Einrichtungscode -- und neu starten (Prozess endet mit 75).
4. `nodvard_deck.boot` laeuft wie im Container-Entrypoint und spielt ein.
5. Start: die Anmeldung mit dem alten Konto geht, der Vault entschluesselt das Secret mit dem
   mitgesicherten Schluessel, alte Anmeldungen sind beendet, das Ergebnis steht im Audit-Protokoll.

Dazu die Gegenprobe: ein kaputter Upload wird abgelehnt und veraendert nichts.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from nodvard_deck.config import Settings
from nodvard_deck.core import vault
from nodvard_deck.db.session import create_engine_for
from nodvard_deck.models import Secret
from restore_helpers import REPO_ROOT
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

SETUP_CODE = "E2E-CODE-2345"
OWNER_PW = "ein-sehr-langes-konto-passwort"
BACKUP_PW = "einmal-passwort-fuer-diese-datei"
SECRET_VALUE = "super-geheimer-api-schluessel-4711"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Prozesse und Signale wie im Container (POSIX)")


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
        "NODVARD_DECK_SETUP_CODE": SETUP_CODE,
        "NODVARD_DECK_METRICS_INTERVAL_S": "0",
        "PYTHONUNBUFFERED": "1",
    })
    return env


def run_boot(env: dict[str, str]) -> subprocess.CompletedProcess:
    """Genau das, was `deploy/entrypoint.sh` vor der Anwendung tut."""
    return subprocess.run([sys.executable, "-m", "nodvard_deck.boot"], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=180, check=False)


class Server:
    def __init__(self, env: dict[str, str]) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}/api/v1"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "nodvard_deck.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(f"Server beendet (Code {self.proc.returncode}):\n{self.proc.stdout.read()}")
            try:
                if httpx.get(f"{self.url}/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        self.stop()
        raise AssertionError("Server wurde nicht rechtzeitig bereit")

    def stop(self) -> int:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
        try:
            return self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait()

    def output(self) -> str:
        return self.proc.stdout.read() if self.proc.stdout else ""


def login(server: Server, username: str, password: str) -> httpx.Response:
    return httpx.post(f"{server.url}/auth/login", json={"username": username, "password": password}, timeout=30)


def auth(response: httpx.Response) -> dict:
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def make_source(root: Path) -> tuple[bytes, str]:
    """Installation 1: Konto, Secret, Erweiterungsdaten, eine Anmeldung -> verschluesselte Sicherung."""
    env = instance_env(root)
    ext = root / "data" / "ext" / "beispiel"
    ext.mkdir(parents=True)
    (ext / "dokument.txt").write_text("Inhalt aus der Quelle")
    booted = run_boot(env)
    assert booted.returncode == 0, booted.stdout + booted.stderr
    server = Server(env)
    try:
        r = httpx.post(f"{server.url}/auth/bootstrap", json={"username": "nico", "password": OWNER_PW, "setup_code": SETUP_CODE}, timeout=30)
        assert r.status_code == 201, r.text
        logged = login(server, "nico", OWNER_PW)
        assert logged.status_code == 200, logged.text
        headers = auth(logged)
        made = httpx.post(f"{server.url}/secrets", json={"label": "proxmox-token", "kind": "api_token", "value": SECRET_VALUE}, headers=headers, timeout=30)
        assert made.status_code == 201, made.text
        started = httpx.post(
            f"{server.url}/system/backups/download",
            json={"current_password": OWNER_PW, "mode": "passwort", "password": BACKUP_PW}, headers=headers, timeout=30,
        )
        assert started.status_code == 202, started.text
        ticket = started.json()
        for _ in range(240):
            state = httpx.get(f"{server.url}/system/backups/download-jobs/{ticket['job_id']}", headers=headers, timeout=30).json()
            if state["status"] != "building":
                break
            time.sleep(0.5)
        assert state["status"] == "ready", state
        blob = httpx.get(f"http://127.0.0.1:{server.port}{ticket['url']}", timeout=60)
        assert blob.status_code == 200
        refresh = logged.cookies.get("lattice_refresh") or ""
        assert logged.cookies.get("nodvard_deck_refresh") == refresh, "ueber echtes HTTP: beide Cookies, gleicher Wert"
        # Gegenprobe fuer spaeter: SOLANGE nichts eingespielt ist, gilt dieses Cookie (die Sicherung ist schon fertig).
        assert httpx.post(f"{server.url}/auth/refresh", json={}, cookies={"lattice_refresh": refresh}, timeout=30).status_code == 200
        return blob.content, refresh
    finally:
        server.stop()


def test_backup_restore_through_the_wizard_end_to_end(tmp_path):
    blob, old_refresh = make_source(tmp_path / "quelle")
    assert blob.startswith(b"NODVARD-DECK-BACKUP/1\n") and SECRET_VALUE.encode() not in blob and OWNER_PW.encode() not in blob

    # --- eine ganz neue, leere Installation -------------------------------------------------------
    root = tmp_path / "ziel"
    env = instance_env(root)
    data = root / "data"
    assert not (data / "lattice.db").exists()
    booted = run_boot(env)
    assert booted.returncode == 0, booted.stdout + booted.stderr
    server = Server(env)
    try:
        assert httpx.get(f"{server.url}/auth/bootstrap", timeout=30).json()["needed"] is True
        wizard = f"{server.url}/auth/bootstrap/restore"
        octet = {"Content-Type": "application/octet-stream"}
        # Ohne Code geht nichts, auch ein Upload nicht.
        assert httpx.put(f"{wizard}/upload", content=blob, headers=octet, timeout=60).status_code == 403
        code = {"X-Setup-Code": SETUP_CODE}
        up = httpx.put(f"{wizard}/upload", content=blob, headers={**octet, **code}, timeout=60)
        assert up.status_code == 201, up.text
        rid = up.json()["id"]
        wrong = httpx.post(f"{wizard}/{rid}/inspect", json={"password": "falsches-passwort-123"}, headers=code, timeout=60)
        assert wrong.status_code == 400
        inspected = httpx.post(f"{wizard}/{rid}/inspect", json={"password": BACKUP_PW}, headers=code, timeout=120)
        assert inspected.status_code == 200, inspected.text
        summary = inspected.json()["summary"]
        assert summary["owner_name"] == "nico" and summary["users"] == 1 and summary["created_at"]
        scheduled = httpx.post(f"{wizard}/{rid}/schedule", json={}, headers=code, timeout=60)
        assert scheduled.status_code == 200, scheduled.text
        assert httpx.post(f"{server.url}/auth/bootstrap/restart", headers=code, timeout=60).status_code == 202
        exit_code = server.proc.wait(timeout=60)
        assert exit_code == 75, f"Neustart muss mit 75 enden, war {exit_code}:\n{server.output()}"
    finally:
        if server.proc.poll() is None:
            server.stop()

    # --- Container startet neu: Entrypoint -> boot spielt ein, dann Migration --------------------
    assert (data / "restore" / "pending.json").exists()
    booted = run_boot(env)
    assert booted.returncode == 0, booted.stdout + booted.stderr
    assert "Sicherung eingespielt" in booted.stdout and "Wiederherstellung abgeschlossen" in booted.stdout
    assert not (data / "restore" / "pending.json").exists() and not (data / "restore" / "journal.json").exists()
    assert sqlite3.connect(data / "lattice.db").execute("SELECT count(*) FROM users").fetchone()[0] == 1
    assert (data / "ext" / "beispiel" / "dokument.txt").read_text() == "Inhalt aus der Quelle"
    assert not (data / "setup_code.txt").exists(), "es gibt jetzt Konten"

    server = Server(env)
    try:
        # Das alte Konto geht ...
        assert login(server, "nico", "falsches-passwort-123").status_code == 401
        logged = login(server, "nico", OWNER_PW)
        assert logged.status_code == 200, logged.text
        headers = auth(logged)
        assert httpx.get(f"{server.url}/auth/bootstrap", timeout=30).json() == {"needed": False}
        # ... die alte Anmeldung ist beendet ...
        assert old_refresh, "die Quelle hat eine Anmeldung (Cookie) bekommen"
        stale = httpx.post(f"{server.url}/auth/refresh", json={}, cookies={"lattice_refresh": old_refresh}, timeout=30)
        assert stale.status_code == 401, "Anmeldungen aus der Zeit der Sicherung muessen beendet sein"
        # ... das Ergebnis ist sichtbar und steht im Audit-Protokoll der eingespielten Datenbank.
        status = httpx.get(f"{server.url}/system/restore/status", headers=headers, timeout=30).json()
        assert status["result"]["ok"] is True and status["result"]["source"] == "bootstrap" and status["pending"] is None
        assert status["replaced"] is not None
        audit = httpx.get(f"{server.url}/audit?action=system.restore.applied", headers=headers, timeout=30).json()
        assert len(audit) == 1 and audit[0]["outcome"] == "success"
        assert OWNER_PW not in json.dumps(audit) and BACKUP_PW not in json.dumps(audit)
    finally:
        server.stop()

    # ... und der Vault entschluesselt das Secret mit dem mitgesicherten Schluessel.
    settings = Settings(
        env="dev", data_dir=data, database_url=f"sqlite+aiosqlite:///{data / 'lattice.db'}", master_key_path=data / "master.key",
        vault_keyring_path=data / "vault_keyring.json", jwt_secret_path=data / "jwt_secret.key",
    )

    async def read_secret() -> str:
        engine = create_engine_for(settings)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                row = (await session.execute(select(Secret).where(Secret.label == "proxmox-token"))).scalar_one()
                return await vault.read_secret_plaintext(session, vault.load_keyring(settings), row.id)
        finally:
            await engine.dispose()

    assert asyncio.run(read_secret()) == SECRET_VALUE


def test_owner_flow_replaces_an_installation_that_already_has_accounts(tmp_path):
    blob, _old_refresh = make_source(tmp_path / "quelle")

    root = tmp_path / "ziel"
    env = instance_env(root)
    data = root / "data"
    assert run_boot(env).returncode == 0
    other_pw = "ganz-anderes-passwort-123"
    server = Server(env)
    try:
        r = httpx.post(f"{server.url}/auth/bootstrap", json={"username": "bernd", "password": other_pw, "setup_code": SETUP_CODE}, timeout=30)
        assert r.status_code == 201
        headers = auth(login(server, "bernd", other_pw))
        octet = {**headers, "Content-Type": "application/octet-stream"}
        from urllib.parse import quote

        # Falsches Passwort: abgelehnt, bevor der Body gelesen wird; nichts liegt danach da.
        bad = httpx.put(f"{server.url}/system/restore/upload", content=blob, headers={**octet, "X-Confirm-Password": quote("falsch")}, timeout=60)
        assert bad.status_code == 400
        assert not (data / "restore").exists() or not any(p.is_dir() and len(p.name) == 32 for p in (data / "restore").iterdir())
        up = httpx.put(f"{server.url}/system/restore/upload", content=blob, headers={**octet, "X-Confirm-Password": quote(other_pw)}, timeout=60)
        assert up.status_code == 201, up.text
        rid = up.json()["id"]
        info = httpx.post(f"{server.url}/system/restore/{rid}/inspect", json={"password": BACKUP_PW}, headers=headers, timeout=120)
        assert info.status_code == 200, info.text
        assert info.json()["summary"]["owner_name"] == "nico"
        assert any("anderen Installation" in w for w in info.json()["summary"]["warnings"]), "fremde Sicherung: Warnung"
        sched = httpx.post(f"{server.url}/system/restore/{rid}/schedule", json={"current_password": other_pw}, headers=headers, timeout=60)
        assert sched.status_code == 200, sched.text
        assert httpx.post(f"{server.url}/system/restart", json={"current_password": other_pw}, headers=headers, timeout=60).status_code == 202
        assert server.proc.wait(timeout=60) == 75
    finally:
        if server.proc.poll() is None:
            server.stop()

    booted = run_boot(env)
    assert booted.returncode == 0, booted.stdout + booted.stderr
    server = Server(env)
    try:
        assert login(server, "bernd", other_pw).status_code == 401, "die Konten der Sicherung ersetzen die alten"
        headers = auth(login(server, "nico", OWNER_PW))
        status = httpx.get(f"{server.url}/system/restore/status", headers=headers, timeout=30).json()
        assert status["result"]["ok"] and status["result"]["source"] == "owner" and status["result"]["actor"] == "bernd"
        old_name = status["replaced"]["name"]
        # Der alte Stand liegt noch da -- samt Konto "bernd" -- und laesst sich auf Wunsch loeschen.
        old_db = data / "restore" / old_name / "lattice.db"
        assert sqlite3.connect(old_db).execute("SELECT username FROM users").fetchall() == [("bernd",)]
        deleted = httpx.request("DELETE", f"{server.url}/system/restore/replaced", json={"current_password": OWNER_PW}, headers=headers, timeout=30)
        assert deleted.status_code == 204 and not old_db.exists()
        audit = httpx.get(f"{server.url}/audit?action=system.restore.applied", headers=headers, timeout=30).json()
        assert len(audit) == 1 and audit[0]["detail"]["source"] == "owner"
    finally:
        server.stop()


def test_a_broken_upload_changes_nothing_and_the_install_still_starts_normally(tmp_path):
    root = tmp_path / "ziel"
    env = instance_env(root)
    assert run_boot(env).returncode == 0
    data = root / "data"
    server = Server(env)
    try:
        code = {"X-Setup-Code": SETUP_CODE, "Content-Type": "application/octet-stream"}
        wizard = f"{server.url}/auth/bootstrap/restore"
        assert httpx.put(f"{wizard}/upload", content=b"kein Backup", headers=code, timeout=30).status_code == 422
        assert httpx.post(f"{server.url}/auth/bootstrap/restart", headers=code, timeout=30).status_code == 409, "nichts vorgemerkt: kein Neustart"
        assert httpx.get(f"{server.url}/health", timeout=30).status_code == 200
    finally:
        server.stop()
    assert not (data / "restore" / "pending.json").exists()
    shutil.rmtree(root, ignore_errors=True)
