"""„Verbindung prüfen“ und Server-Schlüssel bestätigen (docs/04-API.md §3):
`POST /hosts/{id}/check`, `POST /hosts/{id}/known-hosts`, make-default mit `delete_previous`.

Läuft gegen echte lokale asyncssh-Server (`local_ssh_server`, `local_ssh_key_server`), nicht gegen
die echte Flotte. Die wichtigste Zusicherung steht mehrfach da: **an einen Server mit
unbestätigtem Schlüssel geht keine Anmeldung** (`ssh_server_stats["begin_auth"] == 0`).
"""

from __future__ import annotations

import asyncio
import socket

import asyncssh
import pytest
from nodvard_sdk import HostRequirementSpec
from sqlalchemy import select

from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import AuditEntry, Host, HostCredential, KnownHostKey
from nodvard_deck.services import host_check

CHECK = "/api/v1/hosts/{}/check"
PIN = "/api/v1/hosts/{}/known-hosts"
GENERATE = "/api/v1/hosts/{}/credentials/generate-key"
CREDENTIALS = "/api/v1/hosts/{}/credentials"
SUDO = host_check.SUDO_COMMAND


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
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


async def _make_host(client, token, name="bastel-pi", address="127.0.0.1", **extra) -> dict:
    r = await client.post("/api/v1/hosts", json={"name": name, "address": address, **extra}, headers=_auth(token))
    assert r.status_code == 201, r.text
    return r.json()


async def _add_password(client, token, host_id, port, username="testuser", password="test-password-123") -> dict:
    r = await client.post(
        CREDENTIALS.format(host_id),
        json={"kind": "ssh_password", "username": username, "port": port, "secret_value": password},
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _check(client, token, host_id, **body):
    return await client.post(CHECK.format(host_id), json=body or None, headers=_auth(token))


def _items(response) -> dict[str, dict]:
    return {item["id"]: item for item in response.json()["items"]}


def _statuses(response) -> dict[str, str]:
    return {item_id: item["status"] for item_id, item in _items(response).items()}


async def _audit_rows(db_session, action: str) -> list[AuditEntry]:
    db_session.expire_all()
    return list((await db_session.execute(select(AuditEntry).where(AuditEntry.action == action))).scalars().all())


async def _known_rows(db_session) -> list[KnownHostKey]:
    db_session.expire_all()
    return list((await db_session.execute(select(KnownHostKey))).scalars().all())


async def _pin_seen_key(client, token, host_id, response) -> dict:
    """Bestätigt genau den Schlüssel aus der Antwort einer Prüfung."""
    key = response.json()["host_key"]
    r = await client.post(PIN.format(host_id), json={"key_type": key["key_type"], "fingerprint": key["fingerprint"]}, headers=_auth(token))
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(autouse=True)
def _clean_state():
    host_check.reset_state()
    yield
    host_check.reset_state()
    for ext_id in ("fake-a", "fake-b"):
        get_extension_runtime().ui.clear_extension(ext_id)


@pytest.fixture
def server_port(monkeypatch):
    """Ohne Zugang prüft Nodvard Deck Port 22 -- im Test ist das der Port des Test-Servers."""

    def _set(port: int) -> None:
        monkeypatch.setattr(host_check, "DEFAULT_SSH_PORT", port)

    return _set


@pytest.fixture
def pool(monkeypatch):
    from nodvard_deck.core import ssh

    class _Recorder:
        def __init__(self) -> None:
            self.dropped: list[str] = []
            self.retired: list[str] = []

        async def drop_host(self, host_id: str) -> None:
            self.dropped.append(host_id)

        def retire_host(self, host_id: str, grace_s: float = 900.0) -> None:
            self.retired.append(host_id)

        def generation(self, host_id: str) -> int:
            return 0

    recorder = _Recorder()
    monkeypatch.setattr(ssh, "_pool", recorder)
    return recorder


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# ---------------------------------------------------------------------------
# Ohne Zugang: Schlüssel lesen, nichts senden
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_without_credential_asks_to_confirm_the_key_and_writes_nothing(
    client, db_session, local_ssh_server, ssh_server_stats, server_port
):
    _, port, *_ = local_ssh_server
    server_port(port)
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)

    r = await _check(client, token, host["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False, "„bestätigen“ ist noch nicht in Ordnung"
    assert body["credential_id"] is None and body["os"] is None
    assert _statuses(r) == {"reachable": "ok", "host_key": "confirm"}, "kein Anmelde-Schritt ohne bestätigten Schlüssel"
    item = _items(r)["host_key"]
    assert body["host_key"]["status"] == "new" and body["host_key"]["expected"] is None
    assert body["host_key"]["key_type"] == "ssh-ed25519" and body["host_key"]["fingerprint"].startswith("SHA256:")
    assert body["host_key"]["fingerprint"] in item["detail"]
    assert "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub" in item["hint"]
    assert "SSH-2.0" in _items(r)["reachable"]["detail"], "das Banner wird (gekürzt) gezeigt"
    # Nichts gespeichert, nie angemeldet.
    assert await _known_rows(db_session) == []
    assert ssh_server_stats["begin_auth"] == 0
    assert (await db_session.get(Host, host["id"])).status == "unknown"


@pytest.mark.asyncio
async def test_pin_needs_the_fresh_fingerprint_from_the_check_and_works_once(
    client, db_session, local_ssh_server, ssh_server_stats, server_port
):
    _, port, *_ = local_ssh_server
    server_port(port)
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)

    # Ohne vorherige Prüfung: abgelehnt, auch mit einem echten Fingerabdruck.
    real = (await _check(client, token, host["id"])).json()["host_key"]
    host_check.reset_state()
    r = await client.post(PIN.format(host["id"]), json={"key_type": real["key_type"], "fingerprint": real["fingerprint"]}, headers=_auth(token))
    assert r.status_code == 409 and "Bitte zuerst „Verbindung prüfen“" in r.json()["detail"]
    assert await _known_rows(db_session) == []

    # Nach der Prüfung, aber mit einem anderen (mitgebrachten) Fingerabdruck: abgelehnt, nichts gemerkt.
    await _check(client, token, host["id"])
    for bad in (
        {"key_type": real["key_type"], "fingerprint": "SHA256:selbst-ausgedacht"},
        {"key_type": "ssh-rsa", "fingerprint": real["fingerprint"]},
    ):
        r = await client.post(PIN.format(host["id"]), json=bad, headers=_auth(token))
        assert r.status_code == 409, r.text
    assert await _known_rows(db_session) == []

    # Der richtige Fingerabdruck geht -- und merkt, wer ihn bestätigt hat.
    r = await client.post(PIN.format(host["id"]), json={"key_type": real["key_type"], "fingerprint": real["fingerprint"]}, headers=_auth(token))
    assert r.status_code == 201, r.text
    [row] = await _known_rows(db_session)
    assert (row.host_id, row.key_type, row.fingerprint) == (host["id"], real["key_type"], real["fingerprint"])
    me = (await client.get("/api/v1/me", headers=_auth(token))).json()
    assert row.accepted_by_user_id == me["id"]
    assert r.json()["accepted_by_user_id"] == me["id"] and r.json()["accepted_by_label"] == "owner1"
    [audit] = await _audit_rows(db_session, "host.known_key_pinned")
    assert audit.detail == {"key_type": real["key_type"], "fingerprint": real["fingerprint"]}

    # Ein zweites Mal: der Merkzettel ist verbraucht.
    r = await client.post(PIN.format(host["id"]), json={"key_type": real["key_type"], "fingerprint": real["fingerprint"]}, headers=_auth(token))
    assert r.status_code == 409
    assert len(await _known_rows(db_session)) == 1
    assert ssh_server_stats["begin_auth"] == 0

    # Die nächste Prüfung kennt den Schlüssel.
    r = await _check(client, token, host["id"])
    assert _statuses(r) == {"reachable": "ok", "host_key": "ok", "login": "skipped"}
    assert r.json()["host_key"]["status"] == "known" and r.json()["ok"] is True


@pytest.mark.asyncio
async def test_pin_refuses_when_a_key_of_that_type_is_already_remembered(client, db_session, local_ssh_server, server_port):
    _, port, *_ = local_ssh_server
    server_port(port)
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    real = (await _check(client, token, host["id"])).json()["host_key"]
    db_session.add(KnownHostKey(host_id=host["id"], key_type=real["key_type"], fingerprint="SHA256:schon-da"))
    await db_session.flush()
    r = await client.post(PIN.format(host["id"]), json={"key_type": real["key_type"], "fingerprint": real["fingerprint"]}, headers=_auth(token))
    assert r.status_code == 409 and "schon ein Schlüssel gemerkt" in r.json()["detail"] and "vergessen" in r.json()["detail"]
    [row] = await _known_rows(db_session)
    assert row.fingerprint == "SHA256:schon-da"


@pytest.mark.asyncio
async def test_pin_expires_after_15_minutes_and_after_an_address_change(client, db_session, local_ssh_server, server_port, monkeypatch):
    _, port, *_ = local_ssh_server
    server_port(port)
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    real = (await _check(client, token, host["id"])).json()["host_key"]
    body = {"key_type": real["key_type"], "fingerprint": real["fingerprint"]}

    now = host_check._now()
    monkeypatch.setattr(host_check, "_now", lambda: now + host_check.SEEN_KEY_TTL_S + 1)
    assert (await client.post(PIN.format(host["id"]), json=body, headers=_auth(token))).status_code == 409
    monkeypatch.setattr(host_check, "_now", lambda: now)

    await _check(client, token, host["id"])  # wieder gemerkt
    assert (await client.patch(f"/api/v1/hosts/{host['id']}", json={"address": "localhost"}, headers=_auth(token))).status_code == 200
    r = await client.post(PIN.format(host["id"]), json=body, headers=_auth(token))
    assert r.status_code == 409, "der Schlüssel gehörte zur alten Adresse"
    assert await _known_rows(db_session) == []


@pytest.mark.asyncio
async def test_pin_needs_the_port_of_the_current_default_credential(client, local_ssh_server, server_port, monkeypatch):
    """Der Merkzettel gilt für Adresse UND Port: ohne Zugang ist es Port 22, sonst der Port des
    Standard-Zugangs."""
    _, port, *_ = local_ssh_server
    server_port(port)
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    key = (await _check(client, token, host["id"])).json()["host_key"]
    body = {"key_type": key["key_type"], "fingerprint": key["fingerprint"]}
    monkeypatch.setattr(host_check, "DEFAULT_SSH_PORT", 22)  # „ohne Zugang“ heißt jetzt wieder Port 22
    assert (await client.post(PIN.format(host["id"]), json=body, headers=_auth(token))).status_code == 409
    monkeypatch.setattr(host_check, "DEFAULT_SSH_PORT", port)
    await _check(client, token, host["id"])
    assert (await client.post(PIN.format(host["id"]), json=body, headers=_auth(token))).status_code == 201

    # Mit Zugang: geprüft auf Port `port`, dann wird ein neuer Standard-Zugang mit anderem Port angelegt.
    other = await _make_host(client, token, "zweiter")
    await _add_password(client, token, other["id"], port)
    key = (await _check(client, token, other["id"])).json()["host_key"]
    await _add_password(client, token, other["id"], port + 1)  # wird der neue Standard
    r = await client.post(PIN.format(other["id"]), json={"key_type": key["key_type"], "fingerprint": key["fingerprint"]}, headers=_auth(token))
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_pin_is_refused_when_any_key_is_already_remembered_even_of_another_type(client, db_session, local_ssh_server, server_port):
    _, port, *_ = local_ssh_server
    server_port(port)
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    key = (await _check(client, token, host["id"])).json()["host_key"]
    assert key["key_type"] == "ssh-ed25519"
    db_session.add(KnownHostKey(host_id=host["id"], key_type="ssh-rsa", fingerprint="SHA256:rsa-gemerkt"))
    await db_session.flush()
    r = await client.post(PIN.format(host["id"]), json={"key_type": key["key_type"], "fingerprint": key["fingerprint"]}, headers=_auth(token))
    assert r.status_code == 409 and "schon ein Schlüssel gemerkt" in r.json()["detail"]
    assert [k.key_type for k in await _known_rows(db_session)] == ["ssh-rsa"], "ein zusätzlicher Typ wird nie per Bestätigen hinzugefügt"


@pytest.mark.asyncio
@pytest.mark.parametrize("with_credential", [True, False])
async def test_check_with_another_key_type_than_the_pinned_one_is_changed_and_cannot_be_pinned(
    client, db_session, ssh_server_factory, ssh_server_stats, server_port, with_credential
):
    """Gemerkt ist ed25519, der Server bietet nur ecdsa an (Mittelsmann!): Fehler „geändert“, kein
    Bestätigen, keine Anmeldung, nichts gemerkt."""
    _, port, _, _, keys = await ssh_server_factory(("ecdsa-sha2-nistp256",))
    server_port(port)
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    if with_credential:
        await _add_password(client, token, host["id"], port)
    db_session.add(KnownHostKey(host_id=host["id"], key_type="ssh-ed25519", fingerprint="SHA256:gemerkt-ed25519"))
    await db_session.flush()

    r = await _check(client, token, host["id"])
    assert r.status_code == 200, r.text
    assert _statuses(r) == {"reachable": "ok", "host_key": "fail"}
    assert r.json()["ok"] is False
    info = r.json()["host_key"]
    assert (info["status"], info["key_type"], info["fingerprint"]) == ("changed", "ecdsa-sha2-nistp256", keys["ecdsa-sha2-nistp256"])
    assert "SHA256:gemerkt-ed25519" in info["expected"]
    assert "anderen Schlüssel als bisher" in _items(r)["host_key"]["detail"]
    assert ssh_server_stats["begin_auth"] == 0 and ssh_server_stats["password_tries"] == 0
    pin = await client.post(PIN.format(host["id"]), json={"key_type": info["key_type"], "fingerprint": info["fingerprint"]}, headers=_auth(token))
    assert pin.status_code == 409
    assert [(k.key_type, k.fingerprint) for k in await _known_rows(db_session)] == [("ssh-ed25519", "SHA256:gemerkt-ed25519")]
    db_session.expire_all()
    assert (await db_session.get(Host, host["id"])).status == "unknown"


@pytest.mark.asyncio
async def test_check_server_with_several_key_types_works_over_the_pinned_one(client, db_session, ssh_server_factory):
    _, port, _, _, keys = await ssh_server_factory(("ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"))
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    await _add_password(client, token, host["id"], port)
    db_session.add(KnownHostKey(host_id=host["id"], key_type="ecdsa-sha2-nistp256", fingerprint=keys["ecdsa-sha2-nistp256"]))
    await db_session.flush()
    r = await _check(client, token, host["id"])
    assert _statuses(r)["host_key"] == "ok" and _statuses(r)["login"] == "ok"
    assert r.json()["host_key"]["key_type"] == "ecdsa-sha2-nistp256"


# ---------------------------------------------------------------------------
# Mit Passwort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_with_password_and_unconfirmed_key_sends_no_password(
    client, db_session, local_ssh_server, ssh_server_stats
):
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    cred = await _add_password(client, token, host["id"], port)

    r = await _check(client, token, host["id"])
    assert r.status_code == 200, r.text
    assert _statuses(r) == {"reachable": "ok", "host_key": "confirm"}
    assert r.json()["credential_id"] == cred["id"]
    # Der Kern: weder Benutzername noch Passwort gingen an den Server, nichts gemerkt.
    assert ssh_server_stats["begin_auth"] == 0 and ssh_server_stats["password_tries"] == 0
    assert await _known_rows(db_session) == []
    assert "test-password-123" not in r.text

    # Bestätigen, dann geht die Anmeldung.
    await _pin_seen_key(client, token, host["id"], r)
    r = await _check(client, token, host["id"])
    assert _statuses(r)["login"] == "ok" and _statuses(r)["host_key"] == "ok"
    assert ssh_server_stats["begin_auth"] == 1 and ssh_server_stats["password_tries"] == 1


@pytest.mark.asyncio
async def test_check_with_password_and_pinned_key_reports_login_root_and_os(
    client, db_session, local_ssh_server, fake_probe, pool
):
    _, port, *_ = local_ssh_server
    fake_probe[host_check.OS_COMMAND] = (
        0,
        'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\n@@arch\naarch64\n@@model\nRaspberry Pi 4 Model B Rev 1.4\x00',
        "",
    )
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    cred = await _add_password(client, token, host["id"], port)
    await _pin_seen_key(client, token, host["id"], await _check(client, token, host["id"]))

    r = await _check(client, token, host["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["credential_id"] == cred["id"]
    assert _statuses(r) == {
        "reachable": "ok", "host_key": "ok", "login": "ok", "root": "ok", "os": "ok",
    }
    items = _items(r)
    assert items["login"]["label"] == "Anmeldung als testuser" and "Passwort" in items["login"]["detail"]
    assert items["root"]["detail"] == "sudo ohne Passwort klappt." and items["root"]["label"] == "Root-Rechte"
    assert body["os"] == {"pretty_name": "Debian GNU/Linux 12 (bookworm)", "arch": "aarch64", "model": "Raspberry Pi 4 Model B Rev 1.4"}
    assert items["os"]["detail"] == "Debian GNU/Linux 12 (bookworm) · aarch64 · Raspberry Pi 4 Model B Rev 1.4"
    assert body["host_key"]["status"] == "known" and body["host_key"]["expected"] == body["host_key"]["fingerprint"]
    # Der Server ist oben, und neue Verbindungen nutzen die neuen Rechte.
    db_session.expire_all()
    stored = await db_session.get(Host, host["id"])
    assert stored.status == "up" and stored.last_seen_at is not None
    assert pool.retired == [host["id"]]
    assert "test-password-123" not in r.text


@pytest.mark.asyncio
async def test_check_sudo_asks_for_a_password(client, local_ssh_server, fake_probe):
    _, port, *_ = local_ssh_server
    fake_probe[SUDO] = (1, "", "sudo: a password is required\n")
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    await _add_password(client, token, host["id"], port)
    await _pin_seen_key(client, token, host["id"], await _check(client, token, host["id"]))

    r = await _check(client, token, host["id"])
    root = _items(r)["root"]
    assert root["status"] == "warn" and root["detail"] == "sudo verlangt ein Passwort."
    assert "Root-Rechte ohne Passwort" in root["hint"]
    assert r.json()["ok"] is True, "ein Hinweis ist kein Fehler"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "detail"),
    [
        ((127, "", "env: 'sudo': No such file or directory"), "sudo ist nicht installiert."),
        ((1, "", "sudo: testuser is not in the sudoers file."), "Dieser Benutzer darf kein sudo."),
        ((1, "", "irgendetwas anderes"), "sudo ohne Passwort klappt nicht."),
    ],
)
async def test_check_sudo_other_outcomes(client, local_ssh_server, fake_probe, result, detail):
    _, port, *_ = local_ssh_server
    fake_probe[SUDO] = result
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    await _add_password(client, token, host["id"], port)
    await _pin_seen_key(client, token, host["id"], await _check(client, token, host["id"]))
    root = _items(await _check(client, token, host["id"]))["root"]
    assert (root["status"], root["detail"]) == ("warn", detail)
    assert "irgendetwas" not in root["detail"] + root["hint"], "Ausgaben des Servers werden nicht durchgereicht"


@pytest.mark.asyncio
async def test_check_as_root_needs_no_sudo():
    # Der Test-Server nimmt nur "testuser"; der Root-Schritt braucht als root keine Verbindung.
    state = host_check._State()
    await host_check._step_root(state, None, "root", [])  # type: ignore[arg-type]
    assert [(i.id, i.status, i.detail) for i in state.items] == [("root", "ok", "Angemeldet als root – sudo nicht nötig.")]


@pytest.mark.asyncio
async def test_check_wrong_password_fails_the_login_only(client, db_session, local_ssh_server, ssh_server_stats):
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    await _add_password(client, token, host["id"], port, password="falsches-passwort-xyz")
    await _pin_seen_key(client, token, host["id"], await _check(client, token, host["id"]))

    r = await _check(client, token, host["id"])
    assert r.status_code == 200
    assert _statuses(r) == {"reachable": "ok", "host_key": "ok", "login": "fail"}, "danach wird nichts mehr versucht"
    login = _items(r)["login"]
    assert "Passwort falsch" in login["hint"] and "keine Anmeldung mit Passwort" in login["hint"]
    assert r.json()["ok"] is False
    assert "falsches-passwort-xyz" not in r.text
    db_session.expire_all()
    assert (await db_session.get(Host, host["id"])).status != "up"


@pytest.mark.asyncio
async def test_check_tampered_fingerprint_is_a_failure_without_pin_button_and_without_login(
    client, db_session, local_ssh_server, ssh_server_stats
):
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    await _add_password(client, token, host["id"], port)
    real = (await _check(client, token, host["id"])).json()["host_key"]
    db_session.add(KnownHostKey(host_id=host["id"], key_type=real["key_type"], fingerprint="SHA256:absichtlich-falsch"))
    await db_session.flush()

    r = await _check(client, token, host["id"])
    assert _statuses(r) == {"reachable": "ok", "host_key": "fail"}
    item = _items(r)["host_key"]
    assert "anderen Schlüssel als bisher" in item["detail"]
    assert "SHA256:absichtlich-falsch" in item["detail"] and real["fingerprint"] in item["detail"]
    assert "vergessen" in item["hint"]
    assert r.json()["host_key"] == {
        "status": "changed", "key_type": real["key_type"], "fingerprint": real["fingerprint"], "expected": "SHA256:absichtlich-falsch",
    }
    assert ssh_server_stats["begin_auth"] == 0 and ssh_server_stats["password_tries"] == 0
    [row] = await _known_rows(db_session)
    assert row.fingerprint == "SHA256:absichtlich-falsch", "der gemerkte Schlüssel bleibt"
    # Auch das Bestätigen des geänderten Schlüssels gibt es nicht.
    pin = await client.post(PIN.format(host["id"]), json={"key_type": real["key_type"], "fingerprint": real["fingerprint"]}, headers=_auth(token))
    assert pin.status_code == 409
    db_session.expire_all()
    assert (await db_session.get(Host, host["id"])).status == "unknown"


@pytest.mark.asyncio
async def test_check_changed_key_without_credential_is_a_failure_too(client, db_session, local_ssh_server, server_port):
    _, port, *_ = local_ssh_server
    server_port(port)
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    real = (await _check(client, token, host["id"])).json()["host_key"]  # merkt sich den Schlüssel für das Bestätigen
    db_session.add(KnownHostKey(host_id=host["id"], key_type=real["key_type"], fingerprint="SHA256:alt"))
    await db_session.flush()
    r = await _check(client, token, host["id"])
    assert _statuses(r) == {"reachable": "ok", "host_key": "fail"}
    pin = await client.post(PIN.format(host["id"]), json={"key_type": real["key_type"], "fingerprint": real["fingerprint"]}, headers=_auth(token))
    assert pin.status_code == 409, "die Prüfung hat den alten Merkzettel verworfen"


# ---------------------------------------------------------------------------
# Mit Schlüssel (generiert)
# ---------------------------------------------------------------------------


async def _key_host(client, token, port, name="bastel-pi"):
    host = await _make_host(client, token, name)
    r = await client.post(GENERATE.format(host["id"]), json={"port": port}, headers=_auth(token))
    assert r.status_code == 201, r.text
    body = r.json()
    fingerprint = asyncssh.import_public_key(body["public_key"]).get_fingerprint()
    return host, body["credential"], fingerprint


@pytest.mark.asyncio
async def test_check_with_generated_key_against_the_key_server(client, db_session, local_ssh_key_server, ssh_server_stats):
    _, port, _, authorized = local_ssh_key_server
    token = await _bootstrap_owner(client)
    host, _cred, fingerprint = await _key_host(client, token, port)

    # 1. Unbestätigter Server-Schlüssel: nichts gesendet -- auch nicht der Schlüssel.
    r = await _check(client, token, host["id"])
    assert _statuses(r) == {"reachable": "ok", "host_key": "confirm"}
    assert ssh_server_stats["begin_auth"] == 0
    await _pin_seen_key(client, token, host["id"], r)

    # 2. Der Einrichtungsbefehl ist noch nicht gelaufen: der Server kennt den Schlüssel nicht.
    r = await _check(client, token, host["id"])
    assert _statuses(r) == {"reachable": "ok", "host_key": "ok", "login": "fail"}
    login = _items(r)["login"]
    assert login["label"] == "Anmeldung als lattice"
    assert "Einrichtungsbefehl" in login["hint"] and "Benutzer" in login["hint"]

    # 3. Eingetragen: die Anmeldung klappt.
    authorized.append(fingerprint)
    r = await _check(client, token, host["id"])
    assert r.json()["ok"] is True
    assert _statuses(r)["login"] == "ok" and "Schlüssel" in _items(r)["login"]["detail"]
    assert "PRIVATE KEY" not in r.text


@pytest.mark.asyncio
async def test_check_with_an_explicit_credential_id(client, db_session, local_ssh_key_server):
    _, port, _, authorized = local_ssh_key_server
    token = await _bootstrap_owner(client)
    host, old, _old_fp = await _key_host(client, token, port)
    second = (await client.post(GENERATE.format(host["id"]), json={"port": port}, headers=_auth(token))).json()
    new, new_fp = second["credential"], asyncssh.import_public_key(second["public_key"]).get_fingerprint()
    assert (old["is_default"], new["is_default"]) == (True, False)
    authorized.append(new_fp)  # nur der NEUE Zugang ist auf dem Server eingetragen
    await _pin_seen_key(client, token, host["id"], await _check(client, token, host["id"]))

    default = await _check(client, token, host["id"])
    assert default.json()["credential_id"] == old["id"] and _statuses(default)["login"] == "fail"
    explicit = await _check(client, token, host["id"], credential_id=new["id"])
    assert explicit.json()["credential_id"] == new["id"] and _statuses(explicit)["login"] == "ok"


# ---------------------------------------------------------------------------
# Was Erweiterungen brauchen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requirements_show_up_as_items(client, local_ssh_server, fake_probe):
    _, port, *_ = local_ssh_server
    ui = get_extension_runtime().ui
    ui.register_host_requirement("fake-a", HostRequirementSpec(
        id="docker-group", label="Docker ohne sudo", check_command="docker ps -q", ok_text="Docker klappt.",
        fail_hint="Benutzer zur Gruppe docker hinzufügen: sudo usermod -aG docker {user}", unix_group="docker", order=20,
    ))
    ui.register_host_requirement("fake-a", HostRequirementSpec(id="root", label="Root", needs_root=True, root_reason="Updates einspielen", order=10))
    ui.register_host_requirement("fake-b", HostRequirementSpec(id="root2", label="Root", needs_root=True, root_reason="Quarantäne", order=11))
    ui.register_host_requirement("fake-b", HostRequirementSpec(id="gone", label="Nicht installiert", check_command="nichtda --version", order=30))
    ui.register_host_requirement("fake-b", HostRequirementSpec(
        id="evil", label="Komisch", check_command="true-ish", fail_hint="{user} {0.__class__} {x}", order=40,
    ))
    fake_probe["docker ps -q"] = (1, "", "permission denied")
    fake_probe["nichtda --version"] = (127, "", "not found")
    fake_probe["true-ish"] = (2, "", "")
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    await _add_password(client, token, host["id"], port)
    await _pin_seen_key(client, token, host["id"], await _check(client, token, host["id"]))

    r = await _check(client, token, host["id"])
    items = _items(r)
    assert items["root"]["label"] == "Root-Rechte (für Updates einspielen, Quarantäne)"
    docker = items["req:fake-a:docker-group"]
    assert (docker["status"], docker["label"]) == ("warn", "Docker ohne sudo")
    assert docker["hint"] == "Benutzer zur Gruppe docker hinzufügen: sudo usermod -aG docker testuser"
    assert (items["req:fake-b:gone"]["status"], items["req:fake-b:gone"]["detail"]) == ("skipped", "Nicht installiert.")
    assert items["req:fake-b:evil"]["hint"] == "testuser {0.__class__} {x}", "Platzhalter werden nicht ausgewertet"
    assert [i["id"] for i in r.json()["items"]] == [
        "reachable", "host_key", "login", "root", "req:fake-a:docker-group", "req:fake-b:gone", "req:fake-b:evil", "os",
    ]
    assert "req:fake-a:root" not in items, "ohne check_command gibt es keinen Punkt"
    assert r.json()["ok"] is True, "Warnungen machen die Prüfung nicht rot"

    fake_probe["docker ps -q"] = (0, "", "")
    assert _items(await _check(client, token, host["id"]))["req:fake-a:docker-group"]["detail"] == "Docker klappt."


@pytest.mark.asyncio
async def test_windows_hosts_skip_shell_checks(client, local_ssh_server):
    _, port, *_ = local_ssh_server
    get_extension_runtime().ui.register_host_requirement("fake-a", HostRequirementSpec(
        id="win", label="Windows-Sache", check_command="dir", os_families=["windows"],
    ))
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token, os_family="windows")
    await _add_password(client, token, host["id"], port)
    await _pin_seen_key(client, token, host["id"], await _check(client, token, host["id"]))
    r = await _check(client, token, host["id"])
    assert _statuses(r) == {
        "reachable": "ok", "host_key": "ok", "login": "ok", "root": "skipped", "os": "skipped", "req:fake-a:win": "skipped",
    }


# ---------------------------------------------------------------------------
# Nicht erreichbar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_closed_port(client, db_session):
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    port = _closed_port()
    await _add_password(client, token, host["id"], port)
    r = await _check(client, token, host["id"])
    assert r.status_code == 200, r.text
    assert _statuses(r) == {"reachable": "fail"}
    item = _items(r)["reachable"]
    assert item["detail"] == f"Der Server lehnt Verbindungen auf Port {port} ab."
    assert "openssh-server" in item["hint"] and "raspi-config" in item["hint"]
    db_session.expire_all()
    assert (await db_session.get(Host, host["id"])).status == "down"


@pytest.mark.asyncio
async def test_check_timeout_is_reported_not_raised(client, db_session, monkeypatch):
    async def _hang(*args, **kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr(asyncio, "open_connection", _hang)
    monkeypatch.setattr(host_check, "TCP_TIMEOUT_S", 0.2)
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    r = await _check(client, token, host["id"])
    assert r.status_code == 200
    item = _items(r)["reachable"]
    assert item["status"] == "fail" and item["detail"] == "Keine Antwort von 127.0.0.1:22."
    assert "Firewall" in item["hint"] and "Port 22" in item["hint"]
    db_session.expire_all()
    assert (await db_session.get(Host, host["id"])).status == "down"


@pytest.mark.asyncio
async def test_check_overall_budget(client, monkeypatch):
    async def _hang(*args, **kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr(asyncio, "open_connection", _hang)
    monkeypatch.setattr(host_check, "TCP_TIMEOUT_S", 60)
    monkeypatch.setattr(host_check, "OVERALL_TIMEOUT_S", 0.2)
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    r = await _check(client, token, host["id"])
    assert r.status_code == 200
    assert _statuses(r) == {"timeout": "fail"} and "zu lange gedauert" in _items(r)["timeout"]["detail"]


@pytest.mark.asyncio
async def test_check_name_that_does_not_resolve(client, monkeypatch):
    async def _no_such_name(*args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(asyncio, "open_connection", _no_such_name)
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token, address="gibt-es-nicht.example")
    item = _items(await _check(client, token, host["id"]))["reachable"]
    assert item["detail"] == "Den Namen gibt-es-nicht.example kennt das Netz nicht."
    assert "IP-Adresse" in item["hint"]


@pytest.mark.asyncio
async def test_check_port_without_ssh(client):
    async def _handle(reader, writer):
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        token = await _bootstrap_owner(client)
        host = await _make_host(client, token)
        await _add_password(client, token, host["id"], port)
        r = await _check(client, token, host["id"])
    finally:
        server.close()
        await server.wait_closed()
    assert _statuses(r) == {"reachable": "fail"}
    assert _items(r)["reachable"]["detail"] == f"Auf Port {port} antwortet kein SSH-Dienst."
    assert "HTTP" not in r.text, "fremde Antworten werden nicht gezeigt"


# ---------------------------------------------------------------------------
# Mengenbegrenzung, Rechte, Protokoll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_rate_limit_per_user(client, db_session):
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    await _add_password(client, token, host["id"], _closed_port())
    for _ in range(host_check.CHECKS_PER_USER):
        assert (await _check(client, token, host["id"])).status_code == 200
    r = await _check(client, token, host["id"])
    assert r.status_code == 429
    assert r.json()["detail"].startswith("Zu viele Prüfungen. Bitte in ") and "Minuten erneut versuchen." in r.json()["detail"]
    assert int(r.headers["Retry-After"]) > 0
    # Ein anderer Nutzer ist nicht betroffen.
    other = await _role_token(client, db_session, "admin", "admin2")
    assert (await _check(client, other, host["id"])).status_code == 200


@pytest.mark.asyncio
async def test_check_refuses_a_second_check_on_the_same_host(client, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow(*args, **kwargs):
        started.set()
        await release.wait()
        raise ConnectionRefusedError

    monkeypatch.setattr(asyncio, "open_connection", _slow)
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    first = asyncio.create_task(_check(client, token, host["id"]))
    await asyncio.wait_for(started.wait(), 5)
    second = await _check(client, token, host["id"])
    assert second.status_code == 429 and second.json()["detail"] == "Für diesen Server läuft gerade schon eine Prüfung."
    release.set()
    assert (await first).status_code == 200
    assert (await _check(client, token, host["id"])).status_code == 200, "danach geht es wieder"


@pytest.mark.asyncio
async def test_check_limits_parallel_checks_on_different_hosts(monkeypatch):
    """Höchstens zwei Prüfungen gleichzeitig (der Bastel-Pi ist klein); die dritte wartet kurz und
    wird dann mit 429 abgewiesen. (Ohne HTTP: die Test-Datenbank teilt eine Sitzung, gleichzeitige
    Anfragen würden sich darin gegenseitig stören.)"""
    monkeypatch.setattr(host_check, "SEMAPHORE_WAIT_S", 0.2)
    release = asyncio.Event()
    running = 0

    async def _slow(*args, **kwargs):
        nonlocal running
        running += 1
        await release.wait()
        return "fertig"

    monkeypatch.setattr(host_check, "_run", _slow)

    class _Host:
        def __init__(self, id_: str) -> None:
            self.id = id_

    async def _go(i: int):
        try:
            return await host_check.run_check(None, None, _Host(f"h{i}"), None, user_id=f"u{i}")  # type: ignore[arg-type]
        except host_check.CheckRefused as exc:
            return exc

    tasks = [asyncio.create_task(_go(i)) for i in range(3)]
    await asyncio.sleep(0.5)
    assert running == host_check.MAX_PARALLEL_CHECKS
    [refused] = [t.result() for t in tasks if t.done()]
    assert isinstance(refused, host_check.CheckRefused) and "mehrere Prüfungen" in str(refused) and refused.retry_after
    release.set()
    assert sorted(str(r) for r in await asyncio.gather(*tasks) if not isinstance(r, host_check.CheckRefused)) == ["fertig", "fertig"]
    assert not host_check._busy, "auch abgewiesene Prüfungen geben ihren Server wieder frei"


@pytest.mark.asyncio
async def test_check_and_pin_permissions_and_errors(client, db_session):
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    other = await _make_host(client, token, "anderer")
    other_cred = await _add_password(client, token, other["id"], 22)
    token_cred = (await client.post(
        CREDENTIALS.format(host["id"]), json={"kind": "api_token", "secret_value": "geheim"}, headers=_auth(token)
    )).json()

    assert (await client.post(CHECK.format(host["id"]))).status_code == 401
    assert (await client.post(PIN.format(host["id"]), json={"key_type": "a", "fingerprint": "b"})).status_code == 401
    for role in ("operator", "viewer"):
        tok = await _role_token(client, db_session, role, f"c-{role}")
        assert (await _check(client, tok, host["id"])).status_code == 403
        assert (await client.post(PIN.format(host["id"]), json={"key_type": "a", "fingerprint": "b"}, headers=_auth(tok))).status_code == 403
        # Lesen bleibt erlaubt.
        assert (await client.get(f"/api/v1/hosts/{host['id']}/known-hosts", headers=_auth(tok))).status_code == 200
    assert (await _check(client, token, "nope")).status_code == 404
    assert (await client.post(PIN.format("nope"), json={"key_type": "a", "fingerprint": "b"}, headers=_auth(token))).status_code == 404
    assert (await _check(client, token, host["id"], credential_id="nope")).status_code == 404
    assert (await _check(client, token, host["id"], credential_id=other_cred["id"])).status_code == 404, "Zugang eines anderen Servers"
    assert (await _check(client, token, host["id"], credential_id=token_cred["id"])).status_code == 422
    assert (await client.post(PIN.format(host["id"]), json={"key_type": "", "fingerprint": "x"}, headers=_auth(token))).status_code == 422
    assert (await client.post(PIN.format(host["id"]), json={"key_type": "a", "fingerprint": "x" * 200}, headers=_auth(token))).status_code == 422


@pytest.mark.asyncio
async def test_check_with_only_an_api_token_behaves_like_no_credential(client, local_ssh_server, server_port):
    _, port, *_ = local_ssh_server
    server_port(port)
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    await client.post(CREDENTIALS.format(host["id"]), json={"kind": "api_token", "secret_value": "geheim"}, headers=_auth(token))
    r = await _check(client, token, host["id"])
    assert _statuses(r) == {"reachable": "ok", "host_key": "confirm"} and r.json()["credential_id"] is None


@pytest.mark.asyncio
async def test_check_is_audited_without_secrets(client, db_session, local_ssh_server, fake_probe):
    _, port, *_ = local_ssh_server
    fake_probe[SUDO] = (1, "", "sudo: a password is required SECRET-STDERR")
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    cred = await _add_password(client, token, host["id"], port)
    confirm = await _check(client, token, host["id"])
    await _pin_seen_key(client, token, host["id"], confirm)
    await _check(client, token, host["id"])

    rows = sorted(await _audit_rows(db_session, "host.connection_checked"), key=lambda r: r.ts)
    checked = [(r.outcome, r.target_id, r.detail) for r in rows]
    [pinned] = await _audit_rows(db_session, "host.known_key_pinned")
    assert [(outcome, target) for outcome, target, _ in checked] == [("failure", host["id"]), ("success", host["id"])]
    assert checked[0][2] == {"credential_id": cred["id"], "items": {"reachable": "ok", "host_key": "confirm"}}
    assert checked[1][2]["items"] == {"reachable": "ok", "host_key": "ok", "login": "ok", "root": "warn", "os": "ok"}
    everything = " ".join(str(detail) for *_, detail in checked) + str(pinned.detail)
    for secret in ("test-password-123", "SECRET-STDERR", "SSH-2.0", "PRIVATE KEY"):
        assert secret not in everything
    assert pinned.actor_type == "user" and pinned.outcome == "success"


# ---------------------------------------------------------------------------
# make-default mit delete_previous: nur nach frischer, erfolgreicher Prüfung
# ---------------------------------------------------------------------------


def _default_url(host_id: str, credential_id: str) -> str:
    return f"/api/v1/hosts/{host_id}/credentials/{credential_id}/make-default"


async def _two_keys(client, token, port):
    host, old, _ = await _key_host(client, token, port)
    second = (await client.post(GENERATE.format(host["id"]), json={"port": port}, headers=_auth(token))).json()
    new, new_fp = second["credential"], asyncssh.import_public_key(second["public_key"]).get_fingerprint()
    return host, old, new, new_fp


async def _creds(client, token, host_id) -> set[str]:
    return {c["id"] for c in (await client.get(CREDENTIALS.format(host_id), headers=_auth(token))).json()}


@pytest.mark.asyncio
async def test_delete_previous_needs_a_fresh_successful_check_of_the_new_credential(client, db_session, local_ssh_key_server):
    _, port, _, authorized = local_ssh_key_server
    token = await _bootstrap_owner(client)
    host, old, new, new_fp = await _two_keys(client, token, port)

    # Noch nie geprüft: abgelehnt, nichts gelöscht, nichts umgestellt.
    r = await client.post(_default_url(host["id"], new["id"]), json={"delete_previous": True}, headers=_auth(token))
    assert r.status_code == 409, r.text
    assert r.json()["detail"].startswith("Bitte zuerst „Verbindung prüfen“")
    assert "10 Minuten" in r.json()["detail"]
    assert await _creds(client, token, host["id"]) == {old["id"], new["id"]}
    assert (await client.get(f"/api/v1/hosts/{host['id']}", headers=_auth(token))).json()["credential"]["id"] == old["id"]
    assert await _audit_rows(db_session, "host.credential_made_default") == []

    # Geprüft, aber die Anmeldung scheitert (neuer Schlüssel nicht auf dem Server): weiter abgelehnt.
    await _pin_seen_key(client, token, host["id"], await _check(client, token, host["id"], credential_id=new["id"]))
    failed = await _check(client, token, host["id"], credential_id=new["id"])
    assert _statuses(failed)["login"] == "fail"
    r = await client.post(_default_url(host["id"], new["id"]), json={"delete_previous": True}, headers=_auth(token))
    assert r.status_code == 409

    # Eingetragen und erfolgreich geprüft: geht. Ohne `delete_previous` ging es auch vorher.
    authorized.append(new_fp)
    ok = await _check(client, token, host["id"], credential_id=new["id"])
    assert _statuses(ok)["login"] == "ok"
    r = await client.post(_default_url(host["id"], new["id"]), json={"delete_previous": True}, headers=_auth(token))
    assert r.status_code == 200, r.text
    assert await _creds(client, token, host["id"]) == {new["id"]}
    assert r.json()["notice"] and "authorized_keys" in r.json()["notice"]


@pytest.mark.asyncio
async def test_delete_previous_check_of_another_credential_does_not_count(client, local_ssh_key_server):
    _, port, _, authorized = local_ssh_key_server
    token = await _bootstrap_owner(client)
    host, old, new, new_fp = await _two_keys(client, token, port)
    authorized.extend([new_fp, asyncssh.import_public_key(
        (await client.get(f"/api/v1/hosts/{host['id']}/credentials/{old['id']}/setup", headers=_auth(token))).json()["public_key"]
    ).get_fingerprint()])
    await _pin_seen_key(client, token, host["id"], await _check(client, token, host["id"]))
    # Geprüft wird der Standard (= der ALTE Zugang): das sagt nichts über den neuen.
    assert _statuses(await _check(client, token, host["id"]))["login"] == "ok"
    r = await client.post(_default_url(host["id"], new["id"]), json={"delete_previous": True}, headers=_auth(token))
    assert r.status_code == 409
    assert await _creds(client, token, host["id"]) == {old["id"], new["id"]}


@pytest.mark.asyncio
async def test_delete_previous_check_goes_stale_and_dies_with_an_address_change(client, db_session, local_ssh_key_server, monkeypatch):
    _, port, _, authorized = local_ssh_key_server
    token = await _bootstrap_owner(client)
    host, old, new, new_fp = await _two_keys(client, token, port)
    authorized.append(new_fp)
    await _pin_seen_key(client, token, host["id"], await _check(client, token, host["id"], credential_id=new["id"]))
    assert _statuses(await _check(client, token, host["id"], credential_id=new["id"]))["login"] == "ok"

    now = host_check._now()
    monkeypatch.setattr(host_check, "_now", lambda: now + host_check.LOGIN_FRESH_S + 1)
    assert (await client.post(_default_url(host["id"], new["id"]), json={"delete_previous": True}, headers=_auth(token))).status_code == 409, "zu alt"
    monkeypatch.setattr(host_check, "_now", lambda: now + host_check.LOGIN_FRESH_S - 1)
    await _check(client, token, host["id"], credential_id=new["id"])  # frisch (Zeitpunkt bleibt im Fenster)
    assert (await client.patch(f"/api/v1/hosts/{host['id']}", json={"address": "localhost"}, headers=_auth(token))).status_code == 200
    assert (await client.post(_default_url(host["id"], new["id"]), json={"delete_previous": True}, headers=_auth(token))).status_code == 409, "Adresse geändert"
    assert await _creds(client, token, host["id"]) == {old["id"], new["id"]}


@pytest.mark.asyncio
async def test_make_default_without_delete_previous_needs_no_check(client, local_ssh_key_server):
    _, port, *_ = local_ssh_key_server
    token = await _bootstrap_owner(client)
    host, old, new, _ = await _two_keys(client, token, port)
    r = await client.post(_default_url(host["id"], new["id"]), json={}, headers=_auth(token))
    assert r.status_code == 200 and r.json()["is_default"] is True
    assert await _creds(client, token, host["id"]) == {old["id"], new["id"]}


# ---------------------------------------------------------------------------
# Bausteine
# ---------------------------------------------------------------------------


def test_parse_os_variants():
    pi = 'NAME="Raspbian"\nPRETTY_NAME="Raspbian GNU/Linux 12 (bookworm)"\n@@arch\naarch64\n@@model\nRaspberry Pi 5\x00'
    assert host_check.parse_os(pi).model_dump() == {
        "pretty_name": "Raspbian GNU/Linux 12 (bookworm)", "arch": "aarch64", "model": "Raspberry Pi 5",
    }
    assert host_check.parse_os("@@arch\nx86_64\n@@model\n").model_dump() == {"pretty_name": None, "arch": "x86_64", "model": None}
    assert host_check.parse_os("\n@@arch\n\n@@model\n") is None
    assert host_check.parse_os("PRETTY_NAME='Alpine'\n@@arch\nx86_64\n@@model\n").pretty_name == "Alpine"
    # Text vom Server wird auf druckbares ASCII und 80 Zeichen gekürzt.
    evil = host_check.parse_os('PRETTY_NAME="' + "A\x1b[31mB" * 50 + '"\n@@arch\nx\n@@model\n')
    assert "\x1b" not in evil.pretty_name and len(evil.pretty_name) <= 80


def test_seen_key_is_single_use_and_ignores_wrong_attempts(monkeypatch):
    class _H:
        id = "h1"
        address = "10.0.0.1"

    host = _H()
    host_check.remember_seen_key(host, 22, "ssh-ed25519", "SHA256:abc")
    assert host_check.consume_seen_key(host, 22, "ssh-ed25519", "SHA256:falsch") is False
    assert host_check.consume_seen_key(host, 22, "ssh-ed25519", "SHA256:abc") is True, "ein falscher Versuch verbraucht nichts"
    assert host_check.consume_seen_key(host, 22, "ssh-ed25519", "SHA256:abc") is False, "nur einmal"
    host_check.remember_seen_key(host, 22, "ssh-ed25519", "SHA256:abc")
    assert host_check.consume_seen_key(host, 2222, "ssh-ed25519", "SHA256:abc") is False, "anderer Port"


def test_host_key_file_names():
    assert host_check._host_key_file("ssh-ed25519").endswith("ssh_host_ed25519_key.pub")
    assert host_check._host_key_file("ecdsa-sha2-nistp256").endswith("ssh_host_ecdsa_key.pub")
    assert host_check._host_key_file("ssh-rsa").endswith("ssh_host_rsa_key.pub")


def test_check_module_does_not_leak_exception_text():
    """Die Fehlertexte sind feste Sätze: im Quelltext wird nirgends `str(exc)` in ein Ergebnis gelegt."""
    import inspect

    source = inspect.getsource(host_check)
    assert "str(exc)" not in source and "{exc}" not in source
    assert "HostCredential" in source and "secret_value" not in source


@pytest.mark.asyncio
async def test_check_keeps_a_credential_row_untouched(client, db_session, local_ssh_server):
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    cred = await _add_password(client, token, host["id"], port)
    await _pin_seen_key(client, token, host["id"], await _check(client, token, host["id"]))
    await _check(client, token, host["id"])
    db_session.expire_all()
    row = await db_session.get(HostCredential, cred["id"])
    assert (row.is_default, row.username, row.port) == (True, "testuser", port)
