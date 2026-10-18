"""Server einrichten mit einem einmaligen Passwort:
`POST /hosts/{id}/credentials/{cid}/setup-with-password` und `services/one_time_password.py`.

Laeuft gegen einen echten lokalen asyncssh-Server, der sich wie ein frischer Linux-Server verhaelt:
Anmeldung mit Passwort fuer das Anmelde-Konto; ein sudo, das das Passwort von der Standardeingabe liest;
und erst NACH dem Einrichtungsbefehl die Anmeldung mit dem neuen Schluessel. Die wichtigsten
Zusicherungen: das Passwort geht nur an einen Server mit bekanntem (gemerktem) Schluessel, nur einmal, nie in die
Befehlszeile, und es steht danach nirgends -- nicht in der Datenbank, nicht im Protokoll, nicht im Log,
nicht in einer Antwort (auch nicht in einer 422).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shlex
import socket
from dataclasses import dataclass, field

import asyncssh
import pytest
from nodvard_deck.core import rate_limit, ssh
from nodvard_deck.models import AuditEntry, Base, Host, HostCredential, KnownHostKey
from nodvard_deck.services import host_check, host_setup, one_time_password
from sqlalchemy import select, text

PASSWORD = "Einmal-Pw-7f3e9c!"
"""Kommt in keinem anderen Text vor -- so faellt jede Stelle auf, an der es landet."""
LOGIN = "/api/v1/hosts/{}/credentials/{}/setup-with-password"
SETUP = "/api/v1/hosts/{}/credentials/{}/setup"
GENERATE = "/api/v1/hosts/{}/credentials/generate-key"
PIN = "/api/v1/hosts/{}/known-hosts"


# ---------------------------------------------------------------------------
# Ein lokaler „frischer Linux-Server“
# ---------------------------------------------------------------------------


@dataclass
class ServerState:
    passwords: dict[str, str] = field(default_factory=lambda: {"root": PASSWORD, "admin": PASSWORD})
    sudo_password: str = PASSWORD
    """Was das sudo dieses Servers als Passwort erwartet."""
    sudo_needs_password: bool = True
    behaviour: str = "ok"
    """`ok`, `fail` (Skript bricht mit eigener Meldung ab), `fail_silent` (bricht ohne eigene Meldung ab), `leak`
    (der Server gibt das Passwort aus), `hang` oder `drop` (die Verbindung bricht nach dem Start des Befehls ab)."""
    authorized: dict[str, set[str]] = field(default_factory=dict)
    """Fingerabdruecke erlaubter Schluessel je Benutzer -- fuellt erst der Einrichtungsbefehl."""
    connections: int = 0
    """Jede TCP-Verbindung mit SSH-Handshake, auch das blosse Lesen des Server-Schluessels."""
    begin_auth: int = 0
    password_tries: list[tuple[str, str]] = field(default_factory=list)
    key_logins: list[str] = field(default_factory=list)
    runs: list[dict] = field(default_factory=list)
    """Jeder Lauf des Einrichtungsbefehls: Benutzer, Befehlszeile, Standardeingabe."""


_SCRIPT_KEY_RE = re.compile(r'K="(ssh-ed25519 [A-Za-z0-9+/=]+)')
_SCRIPT_USER_RE = re.compile(r"U=([a-z_][a-z0-9_-]*)")


class _Server(asyncssh.SSHServer):
    def __init__(self, state: ServerState) -> None:
        self.state = state

    def connection_made(self, conn) -> None:
        self.state.connections += 1

    def begin_auth(self, username: str) -> bool:
        self.state.begin_auth += 1
        return True

    def password_auth_supported(self) -> bool:
        return True

    async def validate_password(self, username: str, password: str) -> bool:
        self.state.password_tries.append((username, password))
        return self.state.passwords.get(username) == password

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_public_key(self, username: str, key) -> bool:
        ok = key.get_fingerprint() in self.state.authorized.get(username, set())
        if ok:
            self.state.key_logins.append(username)
        return ok


def _process_handler(state: ServerState):
    async def handle(process) -> None:
        command = process.command or ""
        if host_setup.SETUP_STARTED not in command:
            # Die Befehle von „Verbindung prüfen“ (sudo -n true, os-release): alles in Ordnung.
            process.stdout.write(f"ran:{command}\n".encode())
            await process.stdout.drain()
            process.exit(0)
            return
        stdin = await process.stdin.read()
        state.runs.append({"user": process.get_extra_info("username"), "command": command, "stdin": stdin})
        if state.behaviour == "drop":
            # Die Verbindung reisst mitten im Lauf ab (kein sauberes Ende), nachdem der Befehl schon etwas getan hat.
            process.stdout.write(f"{host_setup.SETUP_STARTED}\nBenutzer nodvard angelegt.\n".encode())
            await process.stdout.drain()
            process.channel.get_connection().abort()
            return
        out, err, code = await _run_setup(state, command, stdin)
        if out:
            process.stdout.write(out.encode())
        if err:
            process.stderr.write(err.encode())
        await process.stdout.drain()
        process.exit(code)

    return handle


async def _run_setup(state: ServerState, command: str, stdin: bytes) -> tuple[str, str, int]:
    """Was der Server auf den Einrichtungsbefehl antwortet: wie sudo (Passwort von der Standardeingabe) und das
    Skript (traegt den Schluessel fuer den Benutzer ein)."""
    via_sudo_s = command.startswith("env LC_ALL=C sudo -S ")
    if via_sudo_s and state.sudo_needs_password and stdin.decode().split("\n")[0] != state.sudo_password:
        return "", "Sorry, try again.\nsudo: no password was provided\nsudo: 1 incorrect password attempt\n", 1
    if command.startswith("env LC_ALL=C sudo -n ") and state.sudo_needs_password:
        return "", "sudo: a password is required\n", 1
    if state.behaviour == "hang":
        await asyncio.sleep(30)
    started = f"{host_setup.SETUP_STARTED}\n"
    if state.behaviour == "fail":
        return started + "FEHLER: Das Home-Verzeichnis /home/nodvard gibt es nicht.\n", "useradd: kaputt\n", 1
    if state.behaviour == "fail_silent":
        return started, "useradd: kaputt\n", 1
    if state.behaviour == "leak":
        leaked = stdin.decode().strip() or PASSWORD
        return (
            started + f"FEHLER: {leaked}\nirgendwas {leaked}\nFEHLER: Das Home-Verzeichnis fehlt.\n",
            f"sudo: {leaked}\n",
            1,
        )
    key = _SCRIPT_KEY_RE.search(command)
    user = _SCRIPT_USER_RE.search(command)
    assert key and user, "das Skript traegt Benutzer und Schluessel"
    fingerprint = asyncssh.import_public_key(key.group(1)).get_fingerprint()
    state.authorized.setdefault(user.group(1), set()).add(fingerprint)
    name = user.group(1)
    report = ""
    if "ST before" in command:
        # Wie `ST` im Skript: Stand vorher und nachher, hier mit festen Prüfsummen.
        keys = "/root/.ssh/authorized_keys" if name == "root" else f"/home/{name}/.ssh/authorized_keys"
        mark = host_setup.STATE_MARK
        report = (
            f"{mark} before {'a' * 64} 644 root /etc/passwd\n{mark} before - - - {keys}\n"
            f"{host_setup.KEY_ADDED}\n{mark} after {'b' * 64} 644 root /etc/passwd\n{mark} after {'c' * 64} 600 {name} {keys}\n"
        )
    return started + f"Benutzer {name} angelegt.\n" + report + "Fertig. Jetzt im Dashboard „Verbindung prüfen“ drücken.\n", "", 0


@pytest.fixture
async def setup_server():
    """`(port, state, host_key_fingerprint)` eines lokalen SSH-Servers wie oben."""
    state = ServerState()
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.listen(
        "127.0.0.1", 0, server_host_keys=[host_key], server_factory=lambda: _Server(state),
        process_factory=_process_handler(state), encoding=None,
    )
    port = server.sockets[0].getsockname()[1]
    try:
        yield port, state, host_key.get_fingerprint()
    finally:
        server.close()
        try:
            await asyncio.wait_for(server.wait_closed(), timeout=5.0)
        except asyncio.TimeoutError:
            pass


@pytest.fixture(autouse=True)
def _clean_state():
    host_check.reset_state()
    one_time_password.reset_state()
    yield
    host_check.reset_state()
    one_time_password.reset_state()


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery") -> str:
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _role_token(client, db_session, role: str, username: str) -> str:
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": "whatever123"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _permission_token(client, db_session, username: str, *permissions: str) -> str:
    """Ein Nutzer, dessen einzige Rolle genau diese Rechte hat."""
    from nodvard_deck.core import security
    from nodvard_deck.models import Role, RolePermission, User

    user = User(username=username, password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(Role(name=f"rolle-{username}", permissions=[RolePermission(permission=p) for p in permissions]))
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": "whatever123"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _key_host(client, token, port: int, name="bastel-pi", address="127.0.0.1", username="nodvard"):
    r = await client.post("/api/v1/hosts", json={"name": name, "address": address}, headers=_auth(token))
    assert r.status_code == 201, r.text
    host = r.json()
    r = await client.post(GENERATE.format(host["id"]), json={"username": username, "port": port}, headers=_auth(token))
    assert r.status_code == 201, r.text
    return host, r.json()


async def _pin(db_session, host_id: str, fingerprint: str, key_type: str = "ssh-ed25519") -> None:
    db_session.add(KnownHostKey(host_id=host_id, key_type=key_type, fingerprint=fingerprint))
    await db_session.commit()


async def _setup(client, token, host, gen, **body):
    payload = {"login_user": "root", "password": PASSWORD, **body}
    return await client.post(LOGIN.format(host["id"], gen["credential"]["id"]), json=payload, headers=_auth(token))


async def _audit(db_session, action="host.setup_with_password") -> list[AuditEntry]:
    db_session.expire_all()
    rows = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == action).order_by(AuditEntry.ts, AuditEntry.id)))
    return list(rows.scalars().all())


async def _database_dump(db_session) -> str:
    """Jede Zeile jeder Tabelle als Text -- wo immer das Passwort gelandet waere, stuende es hier."""
    parts: list[str] = []
    for table in Base.metadata.sorted_tables:
        for row in (await db_session.execute(text(f'SELECT * FROM "{table.name}"'))).all():
            parts.append(f"{table.name}: {tuple(row)!r}")
    assert len(parts) > 10, "die Datenbank ist nicht leer -- der Abzug sieht wirklich etwas"
    return "\n".join(parts)


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# ---------------------------------------------------------------------------
# Erfolg
# ---------------------------------------------------------------------------


async def _setup_run(db_session, host_id: str) -> dict | None:
    db_session.expire_all()
    return ((await db_session.get(Host, host_id)).host_metadata or {}).get("setup_run")


@pytest.mark.asyncio
async def test_as_root_runs_exactly_the_setup_command_once_and_deck_then_logs_in_with_the_key(client, db_session, setup_server):
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)

    # 1. Server-Schluessel noch unbekannt: Fingerabdruck zum Bestaetigen, das Passwort ging nirgends hin.
    r = await _setup(client, token, host, gen)
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "host_key_unconfirmed"
    assert body["host_key"] == {"status": "new", "key_type": "ssh-ed25519", "fingerprint": server_fp, "expected": None}
    assert "dein Passwort wurde nicht gesendet" in body["detail"]
    assert state.begin_auth == 0 and state.password_tries == [] and state.runs == []

    # 2. Bestaetigen ueber den vorhandenen Weg (derselbe Merkzettel wie bei „Verbindung prüfen“).
    r = await client.post(PIN.format(host["id"]), json={"key_type": "ssh-ed25519", "fingerprint": server_fp}, headers=_auth(token))
    assert r.status_code == 201, r.text

    # 3. Jetzt: eine Anmeldung als root, genau ein Lauf, danach meldet sich Deck mit dem Schluessel an.
    r = await _setup(client, token, host, gen, sudo=True)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True and out["made_default"] is False, "der erzeugte Schluessel war schon Standard"
    assert out["messages"] == ["Benutzer nodvard angelegt.", host_setup.KEY_ADDED], "nur die eigenen Zeilen des Skripts, ohne „Fertig.“"
    items = {i["id"]: i for i in out["check"]["items"]}
    assert items["login"]["status"] == "ok" and items["login"]["detail"] == "Mit Schlüssel angemeldet."
    assert out["check"]["credential_id"] == gen["credential"]["id"]
    assert any("restrict,pty" in n for n in out["notes"])

    assert state.password_tries == [("root", PASSWORD)], "genau eine Anmeldung mit Passwort"
    assert len(state.runs) == 1 and state.runs[0]["user"] == "root"
    assert state.runs[0]["stdin"] == b"", "als root bekommt der Befehl keine Eingabe"
    assert state.key_logins and set(state.key_logins) == {"nodvard"}, "die Pruefung lief mit dem neuen Schluessel"

    # Genau der Befehl von GET .../setup, nur verpackt (als root: sh -c) und mit den Zeilen, die den Stand der Dateien
    # vorher und nachher melden.
    script = (await client.get(SETUP.format(host["id"], gen["credential"]["id"]), params={"sudo": "true"}, headers=_auth(token))).json()["script"]
    expected = host_setup.remote_setup_command(script, as_root=True, sudo_with_password=False)
    assert _without_state_report(state.runs[0]["command"]) == shlex.split(expected)[-1]
    assert "ST before" not in script, "der Befehl zum Selbst-Einfügen meldet keinen Stand"
    assert state.runs[0]["command"].startswith("sh -c ")

    rows = await _audit(db_session)
    assert [(a.outcome, a.detail["result"], a.detail["login_user"], a.target_id) for a in rows] == [
        ("failure", "host_key_unconfirmed", "root", host["id"]),
        ("success", "ok", "root", host["id"]),
    ]
    assert rows[-1].detail["check_ok"] is True and rows[-1].detail["credential_id"] == gen["credential"]["id"]
    assert len(await _audit(db_session, "host.connection_checked")) == 1
    # Was die eigene Einrichtung getan hat, für den Datei-Wächter von Nodvard Shield (nur der eine echte Lauf).
    run = await _setup_run(db_session, host["id"])
    assert run is not None and run["from"] <= run["to"]
    assert run["user"] == "nodvard" and run["actions"] == ["key_added", "user_created"]
    assert run["files"] == {
        "/etc/passwd": {"before": {"sha256": "a" * 64, "mode": "644", "owner": "root"}, "after": {"sha256": "b" * 64, "mode": "644", "owner": "root"}},
        "/home/nodvard/.ssh/authorized_keys": {"before": None, "after": {"sha256": "c" * 64, "mode": "600", "owner": "nodvard"}},
    }
    full_rule = hashlib.sha256(b"nodvard ALL=(root) NOPASSWD: ALL\n").hexdigest()
    assert run["sudoers"] == {"/etc/sudoers.d/nodvard-nodvard": full_rule}
    assert run["legacy_sudoers"] == {"/etc/sudoers.d/lattice-nodvard": full_rule}


def _without_state_report(command: str) -> str:
    inner = shlex.split(command)[-1]
    return "\n".join(line for line in inner.split("\n") if not line.startswith("ST()") and "ST before" not in line and "ST after" not in line)


@pytest.mark.asyncio
async def test_non_root_with_sudo_sends_the_password_on_stdin_never_in_the_command_line(client, db_session, setup_server):
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)

    r = await _setup(client, token, host, gen, login_user="admin")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    run = state.runs[0]
    assert run["user"] == "admin"
    assert run["command"].startswith("env LC_ALL=C sudo -S -p '' sh -c "), run["command"][:80]
    assert PASSWORD not in run["command"], "nie in der Befehlszeile"
    assert run["stdin"] == f"{PASSWORD}\n".encode(), "nur ueber die Standardeingabe, fuer sudo -S"


@pytest.mark.asyncio
async def test_non_root_without_sudo_password_uses_sudo_n_and_sends_nothing_on_stdin(client, db_session, setup_server):
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)

    # Ein Konto, das sudo ohne Passwort darf.
    state.sudo_needs_password = False
    r = await _setup(client, token, host, gen, login_user="admin", sudo_password_same=False)
    assert r.status_code == 200, r.text
    assert state.runs[0]["command"].startswith("env LC_ALL=C sudo -n sh -c ")
    assert state.runs[0]["stdin"] == b""

    # Verlangt sudo doch ein Passwort: fester Satz, der zum Haken fuehrt.
    state.sudo_needs_password = True
    r = await _setup(client, token, host, gen, login_user="admin", sudo_password_same=False)
    assert r.status_code == 400 and r.json()["code"] == "sudo_failed"
    assert r.json()["detail"] == "sudo verlangt ein Passwort. Setze den Haken „Für sudo dasselbe Passwort nehmen“."


@pytest.mark.asyncio
async def test_made_default_only_when_the_host_has_no_default(client, db_session, setup_server):
    port, _state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, first = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    second = (await client.post(GENERATE.format(host["id"]), json={"username": "nodvard", "port": port}, headers=_auth(token))).json()
    assert second["credential"]["is_default"] is False

    # Es gibt einen Standard: der neue Zugang bleibt, was er ist (dafuer gibt es den Knopf).
    r = await _setup(client, token, host, second)
    assert r.status_code == 200, r.text
    assert r.json()["made_default"] is False
    db_session.expire_all()
    assert (await db_session.get(HostCredential, second["credential"]["id"])).is_default is False

    # Ohne Standard (der alte ist geloescht): wie make-default.
    assert (await client.request("DELETE",f"/api/v1/hosts/{host['id']}/credentials/{first['credential']['id']}", json={"current_password": "correct-horse-battery"}, headers=_auth(token))).status_code == 204
    r = await _setup(client, token, host, second)
    assert r.status_code == 200, r.text
    assert r.json()["made_default"] is True
    db_session.expire_all()
    assert (await db_session.get(HostCredential, second["credential"]["id"])).is_default is True
    shown = (await client.get(f"/api/v1/hosts/{host['id']}", headers=_auth(token))).json()
    assert shown["login_ok_at"] is not None, "die gerade gelungene Pruefung zaehlt als Beleg"
    made = await _audit(db_session, "host.credential_made_default")
    assert [a.detail["credential_id"] for a in made] == [second["credential"]["id"]]


# ---------------------------------------------------------------------------
# Server-Schluessel: ohne gemerkten Schluessel kein Passwort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unconfirmed_host_key_sends_no_password_stores_nothing_and_costs_no_attempt(client, db_session, setup_server):
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)

    # Mehr Versuche als die Grenze erlaubt: keiner zaehlt, denn keiner erreicht die Anmeldung.
    for _ in range(one_time_password.ATTEMPTS_PER_TARGET + 2):
        r = await _setup(client, token, host, gen)
        assert r.status_code == 409 and r.json()["code"] == "host_key_unconfirmed", r.text
        assert r.json()["host_key"]["fingerprint"] == server_fp
    assert state.begin_auth == 0, "der Server hat keine Anmeldung gesehen"
    assert state.password_tries == [] and state.runs == []
    db_session.expire_all()
    assert (await db_session.execute(select(KnownHostKey))).scalars().all() == [], "nichts still gemerkt"
    assert await _setup_run(db_session, host["id"]) is None, "nichts gelaufen, kein Einrichtungsfenster"


@pytest.mark.asyncio
async def test_changed_host_key_sends_no_password(client, db_session, setup_server, ssh_server_factory):
    port, state, _server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    # Gemerkt ist der Schluessel eines anderen Servers desselben Typs.
    *_, other = await ssh_server_factory()
    await _pin(db_session, host["id"], other["ssh-ed25519"])

    r = await _setup(client, token, host, gen)
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "host_key_changed" and body["host_key"]["status"] == "changed"
    assert body["host_key"]["expected"] == other["ssh-ed25519"]
    assert state.begin_auth == 0 and state.password_tries == []


# ---------------------------------------------------------------------------
# Falsches Passwort und Grenzen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_password_is_a_fixed_sentence_counts_and_the_limit_stops_further_tries(client, db_session, setup_server):
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)

    for i in range(one_time_password.ATTEMPTS_PER_TARGET):
        r = await _setup(client, token, host, gen, password=f"falsch-{i}")
        assert r.status_code == 400, r.text
        assert r.json() == {
            "detail": "Der Server hat die Anmeldung abgelehnt: Benutzer oder Passwort stimmt nicht – oder der Server "
            "erlaubt keine Anmeldung mit Passwort.",
            "code": "wrong_password",
        }
    for _ in range(3):
        r = await _setup(client, token, host, gen)  # jetzt sogar das richtige -- trotzdem gesperrt
        assert r.status_code == 429, r.text
        assert r.json()["code"] == "rate_limited" and "Zu viele Versuche mit Passwort" in r.json()["detail"]
        assert int(r.headers["Retry-After"]) > 0
    assert len(state.password_tries) == one_time_password.ATTEMPTS_PER_TARGET, "der Versuch ueber der Grenze ging nicht raus"
    results = [a.detail["result"] for a in await _audit(db_session)]
    # Die Sperre steht einmal im Protokoll, nicht bei jedem weiteren Klopfen.
    assert results == ["wrong_password"] * one_time_password.ATTEMPTS_PER_TARGET + ["rate_limited"]


def test_limits_count_per_target_and_per_user():
    rate_limit.reset_all()
    # Je Ziel: egal, wer es versucht, und egal, unter welchem Server-Eintrag (gleiche Adresse und Port).
    for i in range(one_time_password.ATTEMPTS_PER_TARGET):
        one_time_password.claim_attempt(address="192.168.2.10", port=22, user_id=f"u{i}")
    with pytest.raises(one_time_password.PasswordUseError) as exc:
        one_time_password.claim_attempt(address="192.168.2.10", port=22, user_id="noch-einer")
    assert exc.value.code == "rate_limited" and exc.value.status == 429 and exc.value.retry_after
    one_time_password.claim_attempt(address="192.168.2.10", port=2222, user_id="u0")  # anderer Port: anderes Ziel

    # Je Nutzer: egal, an welchem Server.
    rate_limit.reset_all()
    for i in range(one_time_password.ATTEMPTS_PER_USER):
        one_time_password.claim_attempt(address=f"192.168.2.{20 + i}", port=22, user_id="u1")
    with pytest.raises(one_time_password.PasswordUseError):
        one_time_password.claim_attempt(address="192.168.2.99", port=22, user_id="u1")
    one_time_password.claim_attempt(address="192.168.2.99", port=22, user_id="u2")


@pytest.mark.asyncio
async def test_one_run_per_server_at_a_time(client, db_session, setup_server, monkeypatch):
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    one_time_password._busy.add(host["id"])
    r = await _setup(client, token, host, gen)
    assert r.status_code == 429 and r.json()["code"] == "busy"
    assert state.begin_auth == 0


# ---------------------------------------------------------------------------
# Das Passwort steht nirgends
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_is_nowhere_afterwards_db_audit_log_responses(client, db_session, setup_server, caplog):
    caplog.set_level(logging.DEBUG)
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    secrets_before = (await db_session.execute(text("SELECT COUNT(*) FROM secrets"))).scalar_one()
    responses = []

    responses.append(await _setup(client, token, host, gen))  # Schluessel noch nicht gemerkt
    await _pin(db_session, host["id"], server_fp)
    # Ein falsches Passwort, das das richtige enthaelt: auch dessen Spur faellt hier auf.
    responses.append(await _setup(client, token, host, gen, password=PASSWORD + "-falsch"))
    state.behaviour = "leak"
    responses.append(await _setup(client, token, host, gen, login_user="admin"))  # der Server gibt es aus
    state.behaviour = "fail"
    responses.append(await _setup(client, token, host, gen))
    state.behaviour = "ok"
    state.sudo_password = "anderes"
    responses.append(await _setup(client, token, host, gen, login_user="admin"))  # sudo lehnt ab
    state.sudo_password = PASSWORD
    responses.append(await _setup(client, token, host, gen, login_user="admin", sudo=True))  # klappt

    assert [r.status_code for r in responses] == [409, 400, 502, 502, 400, 200], [r.text for r in responses]
    assert PASSWORD in [p for _u, p in state.password_tries], "es wurde wirklich benutzt"
    for r in responses:
        assert PASSWORD not in r.text
    assert PASSWORD not in await _database_dump(db_session)
    assert (await db_session.execute(text("SELECT COUNT(*) FROM secrets"))).scalar_one() == secrets_before, "nichts in den Tresor"
    assert PASSWORD not in " ".join(f"{a.action} {a.reason} {a.detail}" for a in await _audit(db_session, ""))
    assert PASSWORD not in " ".join(f"{a.action} {a.reason} {a.detail}" for a in (await db_session.execute(select(AuditEntry))).scalars())
    assert PASSWORD not in caplog.text
    assert caplog.records, "das Log wurde wirklich mitgeschnitten"


@pytest.mark.asyncio
async def test_failed_setup_returns_only_the_scripts_own_lines_and_masks_the_password(client, db_session, setup_server):
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    state.behaviour = "leak"

    r = await _setup(client, token, host, gen, login_user="admin")
    assert r.status_code == 502, r.text
    body = r.json()
    assert body["code"] == "setup_failed"
    assert body["messages"] == [f"FEHLER: {one_time_password.MASK}", "FEHLER: Das Home-Verzeichnis fehlt."]
    assert "irgendwas" not in r.text and "sudo:" not in r.text, "keine fremden Zeilen, kein stderr"
    assert await _setup_run(db_session, host["id"]) is not None, "gelaufen ist er trotzdem: das Fenster zählt"


@pytest.mark.asyncio
async def test_a_connection_that_breaks_off_while_the_command_runs_is_connection_lost_not_a_failed_script(
    client, db_session, setup_server, caplog
):
    caplog.set_level(logging.DEBUG)
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    state.behaviour = "drop"

    r = await _setup(client, token, host, gen)
    assert r.status_code == 502, r.text
    body = r.json()
    assert body["code"] == "connection_lost", "nicht „setup_failed“: der Befehl ist nicht gescheitert, er wurde unterbrochen"
    assert "teilweise gelaufen sein" in body["detail"] and "„Verbindung prüfen“" in body["detail"]
    assert "gefahrlos noch einmal" in body["detail"]
    assert "Bitte versuche es noch einmal" not in body["detail"], "kein Satz, der so tut, als sei nichts passiert"
    assert "messages" not in body, "nichts vom Server"
    assert len(state.runs) == 1, "der Befehl wurde gestartet"
    assert PASSWORD not in r.text and PASSWORD not in caplog.text
    assert [(a.outcome, a.detail["result"]) for a in await _audit(db_session)] == [("failure", "connection_lost")]


def _lost_session_server(monkeypatch, error: BaseException):
    """`run_once` ohne Netz: die Anmeldung gelingt, `conn.run` wirft `error`."""
    import types

    class Conn:
        def __init__(self) -> None:
            self.closed = False

        async def run(self, *_args, **_kwargs):
            raise error

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            return None

    conn = Conn()

    async def known(_session, _host_id):
        return {"ssh-ed25519": "SHA256:bekannt"}

    async def connect(*_args, **_kwargs):
        return conn

    monkeypatch.setattr(one_time_password.ssh, "load_known_fingerprints", known)
    monkeypatch.setattr(one_time_password.ssh, "connect", connect)
    return types.SimpleNamespace(id="host-1", address="192.168.2.10"), conn


@pytest.mark.asyncio
@pytest.mark.parametrize("error,code", [
    (asyncssh.ConnectionLost("weg"), "connection_lost"),
    (asyncssh.DisconnectError(2, "weg"), "connection_lost"),
    (BrokenPipeError(), "connection_lost"),
    (ValueError("unerwartet"), "connection_lost"),
    # Der Server nimmt den Befehl gar nicht erst an: es ist nichts gelaufen, ein neuer Versuch ist harmlos.
    (asyncssh.ChannelOpenError(2, "Command request failed"), "connection_failed"),
])
async def test_an_error_after_the_login_is_lost_only_when_the_command_may_have_started(monkeypatch, error, code):
    from pydantic import SecretStr

    host, conn = _lost_session_server(monkeypatch, error)
    with pytest.raises(one_time_password.PasswordUseError) as exc:
        await one_time_password.run_once(
            None, host=host, port=22, user_id="u1", login_user="root", password=SecretStr(PASSWORD), command="true"
        )
    assert exc.value.code == code and exc.value.status == 502
    assert PASSWORD not in str(exc.value) and exc.value.__cause__ is None
    assert conn.closed, "die Verbindung wird in jedem Fall geschlossen"


@pytest.mark.asyncio
async def test_extract_reads_the_unmasked_output_and_hands_back_only_its_result(monkeypatch):
    """Das Passwort wird in der Ausgabe unkenntlich gemacht; eine Prüfsumme, in der es zufällig vorkommt, ginge dabei
    kaputt. `extract` sieht deshalb die unveränderte Ausgabe, zurück kommt nur sein Ergebnis."""
    import types

    from pydantic import SecretStr

    secret = "beef42"
    line = f"{host_setup.STATE_MARK} after {'0' * 10}{secret}{'0' * 48} 644 root /etc/passwd"

    class Conn:
        async def run(self, *_args, **_kwargs):
            return types.SimpleNamespace(exit_status=0, stdout=f"{host_setup.SETUP_STARTED}\n{line}\n".encode(), stderr=b"")

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    async def known(_session, _host_id):
        return {"ssh-ed25519": "SHA256:bekannt"}

    async def connect(*_args, **_kwargs):
        return Conn()

    monkeypatch.setattr(one_time_password.ssh, "load_known_fingerprints", known)
    monkeypatch.setattr(one_time_password.ssh, "connect", connect)
    host = types.SimpleNamespace(id="host-2", address="192.168.2.11")
    result = await one_time_password.run_once(
        None, host=host, port=22, user_id="u2", login_user="root", password=SecretStr(secret), command="true",
        extract=lambda out: host_setup.setup_report(out, user="nodvard"),
    )
    assert secret not in result.stdout
    assert result.extracted["files"]["/etc/passwd"]["after"]["sha256"] == f"{'0' * 10}{secret}{'0' * 48}"

    def broken(_out: str):
        raise ValueError("kaputt")

    result = await one_time_password.run_once(
        None, host=host, port=22, user_id="u2", login_user="root", password=SecretStr(secret), command="true", extract=broken,
    )
    assert result.extracted is None and result.exit_status == 0


@pytest.mark.asyncio
async def test_failed_setup_without_own_lines_does_not_point_to_messages_that_are_not_there(client, db_session, setup_server):
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    state.behaviour = "fail_silent"

    r = await _setup(client, token, host, gen)
    assert r.status_code == 502, r.text
    body = r.json()
    assert body["code"] == "setup_failed" and body["messages"] == []
    assert "unten" not in body["detail"] and "keine Meldung hinterlassen" in body["detail"]
    assert "useradd" not in r.text, "nichts vom Server"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"login_user": "root; id", "password": PASSWORD},
    {"login_user": "root", "password": PASSWORD * 40},
    {"login_user": "root", "password": PASSWORD + "\nnoch was"},
    {"login_user": "root", "password": PASSWORD, "groups": "docker"},
    {"login_user": "root", "password": PASSWORD, "sudo": "vielleicht"},
    {"login_user": ["root"], "password": PASSWORD},
    {"password": [PASSWORD]},
])
async def test_422_never_echoes_the_password(client, db_session, setup_server, caplog, body):
    caplog.set_level(logging.DEBUG)
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    r = await client.post(LOGIN.format(host["id"], gen["credential"]["id"]), json=body, headers=_auth(token))
    assert r.status_code == 422, r.text
    assert PASSWORD not in r.text and PASSWORD not in caplog.text
    assert state.begin_auth == 0


@pytest.mark.asyncio
async def test_422_for_broken_json_never_echoes_the_password(client, db_session, setup_server):
    port, _state, _fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    raw = '{"login_user": "root", "password": "' + PASSWORD + '"'  # schliessende Klammer fehlt
    r = await client.post(
        LOGIN.format(host["id"], gen["credential"]["id"]), content=raw,
        headers={**_auth(token), "Content-Type": "application/json"},
    )
    assert r.status_code == 422, r.text
    assert PASSWORD not in r.text


def test_password_is_not_in_the_repr_of_request_or_connection_target():
    from nodvard_deck.api.v1.host_access import SetupWithPasswordIn

    payload = SetupWithPasswordIn(login_user="root", password=PASSWORD)
    assert PASSWORD not in repr(payload) and PASSWORD not in str(payload.model_dump())
    target = ssh.ConnectionTarget(host_id="h", address="a", port=22, username="root", kind="ssh_password", secret_value=PASSWORD)
    assert PASSWORD not in repr(target)


# ---------------------------------------------------------------------------
# Weitere Fehlerwege
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sudo_rejects_the_password(client, db_session, setup_server):
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    state.sudo_password = "ein-anderes"
    r = await _setup(client, token, host, gen, login_user="admin")
    assert r.status_code == 400, r.text
    assert r.json() == {"detail": "sudo hat das Passwort nicht angenommen.", "code": "sudo_failed", "messages": []}
    assert state.authorized == {}, "das Skript lief nicht"


@pytest.mark.asyncio
async def test_unreachable_server(client, db_session):
    token = await _bootstrap_owner(client)
    port = _closed_port()
    host, gen = await _key_host(client, token, port)
    # Ohne gemerkten Schluessel: schon das Lesen des Schluessels scheitert.
    r = await _setup(client, token, host, gen)
    assert r.status_code == 502 and r.json()["code"] == "unreachable", r.text
    # Mit gemerktem Schluessel: die Anmeldung scheitert am Netz.
    await _pin(db_session, host["id"], "SHA256:irgendwas")
    r = await _setup(client, token, host, gen)
    assert r.status_code == 502 and r.json()["code"] == "unreachable", r.text


@pytest.mark.asyncio
async def test_a_hanging_command_runs_into_the_time_limit(client, db_session, setup_server, monkeypatch):
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    monkeypatch.setattr(one_time_password, "COMMAND_TIMEOUT_S", 0.5)
    state.behaviour = "hang"
    r = await _setup(client, token, host, gen)
    assert r.status_code == 504 and r.json()["code"] == "timeout", r.text
    assert one_time_password._busy == set(), "nach dem Fehler ist der Server wieder frei"


@pytest.mark.asyncio
async def test_extra_fields_cannot_redirect_the_password_to_another_target(client, db_session, setup_server, ssh_server_factory, ssh_server_stats):
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    _addr, other_port, *_ = await ssh_server_factory()
    r = await _setup(client, token, host, gen, address="127.0.0.1", port=other_port, hostname="127.0.0.1")
    assert r.status_code == 200, r.text
    assert ssh_server_stats["begin_auth"] == 0, "der andere Server sah nichts"
    assert state.password_tries == [("root", PASSWORD)]


# ---------------------------------------------------------------------------
# Rechte und Vorbedingungen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_needs_hosts_write_and_the_same_preconditions_as_the_command(client, db_session, setup_server):
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    cid = gen["credential"]["id"]
    body = {"login_user": "root", "password": PASSWORD}

    assert (await client.post(LOGIN.format(host["id"], cid), json=body)).status_code == 401
    for role in ("operator", "viewer"):
        tok = await _role_token(client, db_session, role, f"s-{role}")
        r = await client.post(LOGIN.format(host["id"], cid), json=body, headers=_auth(tok))
        assert r.status_code == 403, r.text
    assert (await client.post(LOGIN.format("nope", cid), json=body, headers=_auth(token))).status_code == 404
    assert (await client.post(LOGIN.format(host["id"], "nope"), json=body, headers=_auth(token))).status_code == 404

    # Ein Passwort-Zugang hat keinen Einrichtungsbefehl -- also auch nichts auszufuehren.
    r = await client.post(
        f"/api/v1/hosts/{host['id']}/credentials",
        json={"kind": "ssh_password", "username": "root", "port": port, "secret_value": "gespeichert-123", "is_default": False},
        headers=_auth(token),
    )
    r = await client.post(LOGIN.format(host["id"], r.json()["id"]), json=body, headers=_auth(token))
    assert r.status_code == 422 and "nur für Zugänge mit SSH-Schlüssel" in r.json()["detail"]
    # Windows-Server ebenso.
    assert (await client.patch(f"/api/v1/hosts/{host['id']}", json={"os_family": "windows"}, headers=_auth(token))).status_code == 200
    r = await client.post(LOGIN.format(host["id"], cid), json=body, headers=_auth(token))
    assert r.status_code == 422 and "nur für Linux-Server" in r.json()["detail"]

    assert state.begin_auth == 0, "keiner dieser Aufrufe hat den Server erreicht"
    assert await _audit(db_session) == []


@pytest.mark.asyncio
async def test_hosts_write_alone_is_not_enough_it_also_needs_hosts_execute(client, db_session, setup_server):
    """Der Befehl laeuft als root auf dem Server: wie Terminal und Konsole verlangt die Route `hosts.execute`."""
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    cid = gen["credential"]["id"]
    body = {"login_user": "root", "password": PASSWORD}

    only_write = await _permission_token(client, db_session, "nur-schreiben", "hosts.read", "hosts.write")
    r = await client.post(LOGIN.format(host["id"], cid), json=body, headers=_auth(only_write))
    assert r.status_code == 403, r.text
    assert "hosts.execute" in r.json()["detail"]
    assert state.begin_auth == 0, "der Server wurde nicht erreicht, das Passwort nicht benutzt"
    assert state.runs == []
    assert await _audit(db_session) == []

    # Mit beiden Rechten geht es (Rolle „admin“ hat `*`, der Owner umgeht die Pruefung).
    both = await _permission_token(client, db_session, "schreiben-und-ausfuehren", "hosts.read", "hosts.write", "hosts.execute")
    r = await client.post(LOGIN.format(host["id"], cid), json=body, headers=_auth(both))
    assert r.status_code == 200, r.text
    assert len(state.runs) == 1
    admin = await _role_token(client, db_session, "admin", "s-admin")
    second, second_gen = await _key_host(client, token, port, name="bastel-pi-2")
    await _pin(db_session, second["id"], server_fp)
    r = await client.post(LOGIN.format(second["id"], second_gen["credential"]["id"]), json=body, headers=_auth(admin))
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Randfaelle: Zeichen, Drossel, Grenzen, Maske, Bestaetigen mit anderem Port
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_password_that_cannot_be_sent_is_a_fixed_422_without_traceback(client, db_session, setup_server, caplog):
    """Ein einzelnes Surrogat (JSON `\\ud800`) laesst sich nicht als UTF-8 senden: fester Satz statt 500."""
    caplog.set_level(logging.DEBUG)
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    for login_user in ("admin", "root"):
        raw = '{"login_user": "' + login_user + '", "password": "ab\\ud800cd"}'
        r = await client.post(
            LOGIN.format(host["id"], gen["credential"]["id"]), content=raw,
            headers={**_auth(token), "Content-Type": "application/json"},
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"][0]["msg"] == "Das Passwort enthält ein Zeichen, das sich nicht senden lässt."
        assert "ud800" not in r.text
    assert "Traceback" not in caplog.text
    assert [rec for rec in caplog.records if rec.levelno >= logging.ERROR] == []
    assert state.begin_auth == 0 and await _audit(db_session) == []
    # Kein Versuch verbraucht: die Grenze ist noch ganz frei.
    for i in range(one_time_password.ATTEMPTS_PER_TARGET):
        assert (await _setup(client, token, host, gen, password=f"falsch-{i}")).status_code == 400


@pytest.mark.asyncio
async def test_run_once_itself_refuses_a_password_that_cannot_be_sent(client, db_session, setup_server):
    """Auch ohne das Formular davor (spaetere Aufrufer): kein Absturz, kein gezaehlter Versuch, nichts gesendet."""
    from nodvard_deck.models import Host
    from pydantic import SecretStr

    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, _gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    db_host = await db_session.get(Host, host["id"])
    for on_stdin in (True, False):
        with pytest.raises(one_time_password.PasswordUseError) as exc:
            await one_time_password.run_once(
                db_session, host=db_host, port=port, user_id="u1", login_user="admin",
                password=SecretStr("ab\ud800cd"), command="true", password_on_stdin=on_stdin,
            )
        assert exc.value.code == "invalid_password" and exc.value.status == 400
    assert state.begin_auth == 0 and one_time_password._busy == set()
    for i in range(one_time_password.ATTEMPTS_PER_TARGET):
        one_time_password.claim_attempt(address="127.0.0.1", port=port, user_id=f"x{i}")


@pytest.mark.asyncio
async def test_every_request_is_throttled_per_user_even_when_no_attempt_is_counted(client, db_session, setup_server, monkeypatch):
    """Noch nicht gemerkter Schluessel, nicht erreichbar, „laeuft gerade“: zaehlt nicht als Versuch -- trotzdem darf
    niemand Nodvard Deck beliebig oft Verbindungen zu einer Adresse aufbauen lassen."""
    port, state, _server_fp = setup_server
    monkeypatch.setattr(one_time_password, "_requests_per_user", rate_limit.SlidingWindow(3, 300), raising=False)
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)

    statuses = [(await _setup(client, token, host, gen)).status_code for _ in range(6)]
    assert statuses == [409, 409, 409, 429, 429, 429]
    assert state.connections == 3, "nach der Drossel liest Nodvard Deck den Schluessel nicht mehr"
    results = [a.detail["result"] for a in await _audit(db_session)]
    assert results == ["host_key_unconfirmed"] * 3 + ["rate_limited"]


def test_a_user_over_the_limit_does_not_use_up_the_budget_of_a_target():
    rate_limit.reset_all()
    for i in range(one_time_password.ATTEMPTS_PER_USER):
        one_time_password.claim_attempt(address=f"192.168.2.{30 + i}", port=22, user_id="u1")
    for _ in range(3):  # u1 ist ueber seiner Grenze und klopft weiter bei einem anderen Server
        with pytest.raises(one_time_password.PasswordUseError):
            one_time_password.claim_attempt(address="192.168.2.99", port=22, user_id="u1")
    for i in range(one_time_password.ATTEMPTS_PER_TARGET):  # der Server hat sein volles Budget fuer andere
        one_time_password.claim_attempt(address="192.168.2.99", port=22, user_id=f"andere-{i}")


def test_a_trailing_dot_or_case_is_the_same_target():
    rate_limit.reset_all()
    names = ["pve1.example", "PVE1.example.", " pve1.EXAMPLE ", "pve1.example.", "Pve1.Example"]
    for i, name in enumerate(names):
        one_time_password.claim_attempt(address=name, port=22, user_id=f"u{i}")
    with pytest.raises(one_time_password.PasswordUseError):
        one_time_password.claim_attempt(address="pve1.example.", port=22, user_id="noch-einer")


@pytest.mark.asyncio
@pytest.mark.parametrize("login_user,password", [("root", "setup"), ("admin", "setup"), ("admin", "@@"), ("root", "e")])
async def test_a_password_that_is_part_of_the_marker_does_not_spoil_a_good_run(client, db_session, setup_server, login_user, password):
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    state.passwords[login_user] = password
    state.sudo_password = password
    r = await _setup(client, token, host, gen, login_user=login_user, password=password)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_sudo_output_is_judged_before_the_password_is_masked(client, db_session, setup_server):
    """Mit dem Passwort „a“ waere nach dem Unkenntlichmachen keine sudo-Meldung mehr zu erkennen."""
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, gen = await _key_host(client, token, port)
    await _pin(db_session, host["id"], server_fp)
    state.passwords["admin"] = "a"
    state.sudo_password = "ein-anderes"
    r = await _setup(client, token, host, gen, login_user="admin", password="a")
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "sudo hat das Passwort nicht angenommen."


@pytest.mark.asyncio
async def test_confirming_the_key_works_for_a_second_access_on_another_port(client, db_session, setup_server):
    """Der Standard-Zugang steht auf einem anderen Port als der neue Zugang: Bestaetigen nennt den Zugang."""
    port, state, server_fp = setup_server
    token = await _bootstrap_owner(client)
    host, first = await _key_host(client, token, _closed_port())  # Standard-Zugang, anderer Port
    second = (await client.post(GENERATE.format(host["id"]), json={"username": "nodvard", "port": port}, headers=_auth(token))).json()
    assert first["credential"]["is_default"] is True and second["credential"]["is_default"] is False

    r = await _setup(client, token, host, second)
    assert r.status_code == 409 and r.json()["code"] == "host_key_unconfirmed", r.text
    key = r.json()["host_key"]
    pin = {"key_type": key["key_type"], "fingerprint": key["fingerprint"]}
    # Ohne Angabe gilt wie bisher der Port des Standard-Zugangs -- der passt hier nicht (und der Merkzettel ist
    # danach verbraucht, wie bei jedem Versuch mit anderem Ziel). Ein neuer Lauf merkt den Schluessel wieder vor.
    r = await client.post(PIN.format(host["id"]), json=pin, headers=_auth(token))
    assert r.status_code == 409, r.text
    assert (await _setup(client, token, host, second)).json()["code"] == "host_key_unconfirmed"
    # Ein fremder Zugang (anderer Server) wird nicht angenommen.
    _other, other_gen = await _key_host(client, token, port, name="anderer")
    r = await client.post(PIN.format(host["id"]), json={**pin, "credential_id": other_gen["credential"]["id"]}, headers=_auth(token))
    assert r.status_code == 404, r.text
    r = await client.post(PIN.format(host["id"]), json={**pin, "credential_id": second["credential"]["id"]}, headers=_auth(token))
    assert r.status_code == 201, r.text
    assert r.json()["fingerprint"] == server_fp

    r = await _setup(client, token, host, second)
    assert r.status_code == 200, r.text
    assert state.password_tries == [("root", PASSWORD)]
