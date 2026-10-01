"""Server-Zugaenge: GET /hosts/{id}/requirements, SSH-Schluessel erzeugen,
Einrichtungsbefehl, Zugang als Standard setzen (docs/04-API.md §3)."""

from __future__ import annotations

import pytest
from nodvard_sdk import HostRequirementSpec

from nodvard_deck.ext.runtime import get_extension_runtime


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


async def _make_host(client, token, name="bastel-pi", **extra) -> dict:
    r = await client.post("/api/v1/hosts", json={"name": name, "address": "10.0.0.5", **extra}, headers=_auth(token))
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(autouse=True)
def _cleanup_runtime():
    from nodvard_deck.services import host_check

    host_check.reset_state()
    yield
    host_check.reset_state()
    for ext_id in ("fake-a", "fake-b"):
        get_extension_runtime().ui.clear_extension(ext_id)


# ---------------------------------------------------------------------------
# GET /hosts/{id}/requirements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requirements_match_tags_and_os_family_and_are_sorted(client):
    token = await _bootstrap_owner(client)
    ui = get_extension_runtime().ui
    ui.register_host_requirement("fake-a", HostRequirementSpec(
        id="docker-group", label="Docker ohne sudo", check_command="docker ps -q", ok_text="Docker klappt.",
        fail_hint="sudo usermod -aG docker {user}", unix_group="docker", tags=["docker"], order=20,
    ))
    ui.register_host_requirement("fake-a", HostRequirementSpec(
        id="root", label="Root-Rechte", needs_root=True, root_reason="Updates einspielen", order=10,
    ))
    ui.register_host_requirement("fake-b", HostRequirementSpec(id="win", label="Nur Windows", os_families=["windows"]))
    ui.register_host_requirement("fake-b", HostRequirementSpec(id="other-tag", label="Anderes Tag", tags=["gameserver"]))

    docker = await _make_host(client, token, "docker", tags=["docker"])
    plain = await _make_host(client, token, "plain")
    windows = await _make_host(client, token, "winbox", os_family="windows", tags=["docker"])

    r = await client.get(f"/api/v1/hosts/{docker['id']}/requirements", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert [(x["ext_id"], x["id"]) for x in r.json()] == [("fake-a", "root"), ("fake-a", "docker-group")]
    first, second = r.json()
    assert first["needs_root"] is True and first["root_reason"] == "Updates einspielen"
    assert first["unix_group"] is None and first["check_command"] is None
    assert second == {
        "ext_id": "fake-a", "id": "docker-group", "label": "Docker ohne sudo", "check_command": "docker ps -q",
        "ok_text": "Docker klappt.", "fail_hint": "sudo usermod -aG docker {user}", "unix_group": "docker",
        "needs_root": False, "root_reason": None, "order": 20,
    }

    # Ohne das Tag passt nur, was keine Tags verlangt.
    r = await client.get(f"/api/v1/hosts/{plain['id']}/requirements", headers=_auth(token))
    assert [x["id"] for x in r.json()] == ["root"]
    # Windows-Host: die Standard-Anforderungen gelten nur fuer Linux.
    r = await client.get(f"/api/v1/hosts/{windows['id']}/requirements", headers=_auth(token))
    assert [x["id"] for x in r.json()] == ["win"]


@pytest.mark.asyncio
async def test_requirements_ignore_invalid_group_names(client):
    """Der Gruppenname landet spaeter in einem Shell-Befehl -- eine Extension kann daher
    keinen beliebigen Text einschleusen: ungueltige Namen ignoriert der Kern."""
    token = await _bootstrap_owner(client)
    ui = get_extension_runtime().ui
    for i, bad in enumerate(["docker; rm -rf /", "Docker", "a b", "x" * 40, "$(id)", "docker\nroot", ""]):
        ui.register_host_requirement("fake-a", HostRequirementSpec(id=f"bad{i}", label="Kaputt", unix_group=bad))
    ui.register_host_requirement("fake-a", HostRequirementSpec(id="ok", label="Gut", unix_group="_svc-1"))
    host = await _make_host(client, token)
    r = await client.get(f"/api/v1/hosts/{host['id']}/requirements", headers=_auth(token))
    groups = {x["id"]: x["unix_group"] for x in r.json()}
    assert groups["ok"] == "_svc-1"
    assert all(groups[f"bad{i}"] is None for i in range(7)), groups


@pytest.mark.asyncio
async def test_requirements_ignore_privileged_groups_except_docker(client, caplog):
    """Eine Erweiterung darf den Einrichtungsbefehl nicht dazu bringen, den Benutzer in eine
    Gruppe mit Root-Rechten zu stecken (sudo, shadow, disk, ...). "docker" ist bewusst erlaubt
    (praktisch root, der Hinweis steht im Befehl)."""
    import logging

    caplog.set_level(logging.WARNING)
    token = await _bootstrap_owner(client)
    ui = get_extension_runtime().ui
    blocked = ["root", "sudo", "wheel", "admin", "shadow", "disk", "adm", "staff", "lxd", "libvirt", "kvm"]
    for name in blocked:
        ui.register_host_requirement("fake-a", HostRequirementSpec(id=f"g-{name}", label=name, unix_group=name))
    ui.register_host_requirement("fake-a", HostRequirementSpec(id="g-docker", label="Docker", unix_group="docker"))
    host = await _make_host(client, token)
    r = await client.get(f"/api/v1/hosts/{host['id']}/requirements", headers=_auth(token))
    groups = {x["id"]: x["unix_group"] for x in r.json()}
    assert groups["g-docker"] == "docker"
    assert all(groups[f"g-{name}"] is None for name in blocked), groups
    for name in blocked:
        assert f"'{name}'" in caplog.text and "fake-a" in caplog.text, name

    # Auch im Einrichtungsbefehl: nicht anbietbar, selbst wenn man sie anfordert.
    gen = (await client.post(GENERATE.format(host["id"]), json={}, headers=_auth(token))).json()
    r = await client.get(
        _setup_url(host["id"], gen["credential"]["id"]), params={"groups": "docker,sudo,shadow,disk"}, headers=_auth(token)
    )
    assert r.json()["groups"] == ["docker"]
    assert "usermod -aG sudo" not in r.json()["script"] and "shadow" not in r.json()["script"]


@pytest.mark.asyncio
async def test_requirements_need_login_read_permission_and_known_host(client, db_session):
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    assert (await client.get(f"/api/v1/hosts/{host['id']}/requirements")).status_code == 401
    assert (await client.get("/api/v1/hosts/nope/requirements", headers=_auth(token))).status_code == 404
    viewer = await _role_token(client, db_session, "viewer", "gast")
    assert (await client.get(f"/api/v1/hosts/{host['id']}/requirements", headers=_auth(viewer))).status_code == 200


# ---------------------------------------------------------------------------
# Hilfen fuer Schluessel, Vault und Protokoll
# ---------------------------------------------------------------------------


async def _audit_rows(db_session, prefix="host."):
    from sqlalchemy import select

    from nodvard_deck.models import AuditEntry

    rows = (await db_session.execute(select(AuditEntry).order_by(AuditEntry.ts, AuditEntry.id))).scalars().all()
    return [r for r in rows if r.action.startswith(prefix)]


async def _private_key_of(db_session, test_settings, credential_id: str) -> str:
    from nodvard_deck.core import vault
    from nodvard_deck.models import HostCredential

    credential = await db_session.get(HostCredential, credential_id)
    return await vault.read_secret_plaintext(db_session, vault.load_keyring(test_settings), credential.secret_id)


def _key_body(private_key: str) -> str:
    """Der Teil zwischen BEGIN und END -- daran erkennt man Schluesselmaterial in jedem Text."""
    lines = [ln for ln in private_key.strip().splitlines() if not ln.startswith("-----")]
    return "".join(lines)[:60]


async def _everything_we_could_leak(db_session, response_texts: list[str], caplog) -> str:
    dump = " ".join(f"{r.action} {r.reason} {r.detail} {r.target_id}" for r in await _audit_rows(db_session, ""))
    return "\n".join([*response_texts, dump, caplog.text])


GENERATE = "/api/v1/hosts/{}/credentials/generate-key"


# ---------------------------------------------------------------------------
# POST /hosts/{id}/credentials/generate-key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_key_stores_private_key_in_the_vault_and_returns_only_the_public_part(
    client, db_session, test_settings, caplog
):
    import logging

    import asyncssh

    caplog.set_level(logging.DEBUG)
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    r = await client.post(GENERATE.format(host["id"]), json={}, headers=_auth(token))
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"credential", "public_key", "fingerprint"}
    cred = body["credential"]
    assert (cred["kind"], cred["username"], cred["port"], cred["is_default"], cred["host_id"]) == (
        "ssh_key", "lattice", 22, True, host["id"],
    )
    assert "secret_value" not in cred and "secret_id" not in cred
    assert body["public_key"].startswith("ssh-ed25519 ") and body["public_key"].endswith(" lattice@bastel-pi")
    assert "\n" not in body["public_key"]

    # Der private Schluessel liegt im Vault, und der oeffentliche Teil gehoert dazu.
    private = await _private_key_of(db_session, test_settings, cred["id"])
    assert "BEGIN OPENSSH PRIVATE KEY" in private
    key = asyncssh.import_private_key(private)
    assert key.get_algorithm() == "ssh-ed25519"
    assert key.export_public_key("openssh").decode().split()[:2] == body["public_key"].split()[:2]
    assert key.get_fingerprint() == body["fingerprint"] and body["fingerprint"].startswith("SHA256:")

    from sqlalchemy import select

    from nodvard_deck.models import HostCredential, Secret

    secret = (await db_session.execute(select(Secret).where(Secret.id == (await db_session.get(HostCredential, cred["id"])).secret_id))).scalar_one()
    assert secret.kind == "ssh_key"
    assert secret.description == "Von Nodvard Deck erzeugter SSH-Schlüssel für Host 'bastel-pi'"
    assert _key_body(private) not in secret.ciphertext.decode("latin-1"), "im Vault nur verschluesselt"

    # Weder Antworten noch Protokoll noch Log enthalten den privaten Schluessel.
    listing = await client.get(f"/api/v1/hosts/{host['id']}/credentials", headers=_auth(token))
    detail = await client.get(f"/api/v1/hosts/{host['id']}", headers=_auth(token))
    secrets_api = await client.get("/api/v1/secrets", headers=_auth(token))
    dump = await _everything_we_could_leak(db_session, [r.text, listing.text, detail.text, secrets_api.text], caplog)
    assert "PRIVATE KEY" not in dump and _key_body(private) not in dump

    [row] = await _audit_rows(db_session, "host.key_generated")
    assert row.detail == {"credential_id": cred["id"], "username": "lattice", "port": 22, "fingerprint": body["fingerprint"]}
    assert (row.outcome, row.target_type, row.target_id) == ("success", "host", host["id"])
    me = (await client.get("/api/v1/me", headers=_auth(token))).json()
    assert row.actor_id == me["id"]


@pytest.mark.asyncio
async def test_generate_key_default_only_for_the_first_ssh_credential(client):
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    first = (await client.post(GENERATE.format(host["id"]), json={}, headers=_auth(token))).json()
    second = (await client.post(GENERATE.format(host["id"]), json={"username": "root", "port": 2222}, headers=_auth(token))).json()
    assert first["credential"]["is_default"] is True
    assert second["credential"]["is_default"] is False, "Schluesselwechsel ohne sich auszusperren"
    assert (second["credential"]["username"], second["credential"]["port"]) == ("root", 2222)
    assert first["public_key"] != second["public_key"]
    default = (await client.get(f"/api/v1/hosts/{host['id']}", headers=_auth(token))).json()["credential"]
    assert default["id"] == first["credential"]["id"]

    # Ein schon vorhandener Passwort-Zugang zaehlt als SSH-Zugang.
    other = await _make_host(client, token, "zweiter")
    pw = await client.post(
        f"/api/v1/hosts/{other['id']}/credentials",
        json={"kind": "ssh_password", "username": "root", "secret_value": "geheim-123"}, headers=_auth(token),
    )
    assert pw.status_code == 201
    gen = (await client.post(GENERATE.format(other["id"]), json={}, headers=_auth(token))).json()
    assert gen["credential"]["is_default"] is False
    assert (await client.get(f"/api/v1/hosts/{other['id']}", headers=_auth(token))).json()["credential"]["kind"] == "ssh_password"


@pytest.mark.asyncio
async def test_generate_key_comment_is_made_safe_for_odd_host_names(client, db_session):
    """Von einer Erweiterung entdeckte Server koennen beliebige Namen tragen."""
    from nodvard_deck.services import hosts as hosts_service

    token = await _bootstrap_owner(client)
    host = await hosts_service.create_host(db_session, name="PVE node\n'$(id)", address="10.0.0.9")
    await db_session.commit()
    r = await client.post(GENERATE.format(host.id), json={}, headers=_auth(token))
    assert r.status_code == 201, r.text
    assert r.json()["public_key"].endswith(" lattice@PVE-node----id-")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"username": "a'; rm -rf / #"},
        {"username": "lattice\nroot"},
        {"username": "$(id)"},
        {"username": "x\"y"},
        {"username": "a`id`"},
        {"username": ""},
        {"username": "-x"},
        {"username": "x" * 65},
        {"port": 0},
        {"port": 70000},
        {"port": "abc"},
    ],
)
async def test_generate_key_rejects_bad_input_without_echoing_it(client, db_session, payload):
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    r = await client.post(GENERATE.format(host["id"]), json=payload, headers=_auth(token))
    assert r.status_code == 422, r.text
    for value in payload.values():
        if isinstance(value, str) and len(value) > 3:
            assert value not in r.text, "Eingabe wird nicht zurueckgespiegelt"
    assert "PRIVATE KEY" not in r.text
    assert (await client.get(f"/api/v1/hosts/{host['id']}/credentials", headers=_auth(token))).json() == []
    assert await _audit_rows(db_session, "host.key_generated") == []


@pytest.mark.asyncio
async def test_generate_key_allows_windows_style_user_names(client):
    """Fuer Windows-Server gibt es keinen Einrichtungsbefehl, aber den Schluessel schon."""
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token, "winbox", os_family="windows")
    r = await client.post(GENERATE.format(host["id"]), json={"username": "DOMAIN\\Max Mustermann"}, headers=_auth(token))
    assert r.status_code == 201, r.text
    assert r.json()["credential"]["username"] == "DOMAIN\\Max Mustermann"


@pytest.mark.asyncio
async def test_generate_key_needs_login_write_permission_and_known_host(client, db_session):
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    assert (await client.post(GENERATE.format(host["id"]), json={})).status_code == 401
    assert (await client.post(GENERATE.format("nope"), json={}, headers=_auth(token))).status_code == 404
    for role in ("operator", "viewer"):
        other = await _role_token(client, db_session, role, f"u-{role}")
        assert (await client.post(GENERATE.format(host["id"]), json={}, headers=_auth(other))).status_code == 403
    assert (await client.get(f"/api/v1/hosts/{host['id']}/credentials", headers=_auth(token))).json() == []


@pytest.mark.asyncio
async def test_generate_key_is_rate_limited_per_user(client, db_session):
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    for _ in range(10):
        assert (await client.post(GENERATE.format(host["id"]), json={}, headers=_auth(token))).status_code == 201
    r = await client.post(GENERATE.format(host["id"]), json={}, headers=_auth(token))
    assert r.status_code == 429, r.text
    assert int(r.headers["Retry-After"]) >= 1
    assert r.json()["detail"].startswith("Zu viele Schlüssel erzeugt. Bitte in ")
    assert len((await client.get(f"/api/v1/hosts/{host['id']}/credentials", headers=_auth(token))).json()) == 10
    # Ein anderer Nutzer ist davon nicht betroffen.
    admin = await _role_token(client, db_session, "admin", "admin2")
    assert (await client.post(GENERATE.format(host["id"]), json={}, headers=_auth(admin))).status_code == 201


# ---------------------------------------------------------------------------
# GET /hosts/{id}/credentials/{cid}/setup
# ---------------------------------------------------------------------------


def _setup_url(host_id: str, credential_id: str) -> str:
    return f"/api/v1/hosts/{host_id}/credentials/{credential_id}/setup"


async def _docker_host_with_key(client, token, name="docker", **gen) -> tuple[dict, dict]:
    get_extension_runtime().ui.register_host_requirement("fake-a", HostRequirementSpec(
        id="docker-group", label="Docker ohne sudo (Service-Matrix)", unix_group="docker", tags=["docker"],
    ))
    get_extension_runtime().ui.register_host_requirement("fake-a", HostRequirementSpec(
        id="root", label="Root-Rechte", needs_root=True, root_reason="Updates einspielen",
    ))
    host = await _make_host(client, token, name, tags=["docker"])
    generated = await client.post(GENERATE.format(host["id"]), json=gen, headers=_auth(token))
    assert generated.status_code == 201, generated.text
    return host, generated.json()


@pytest.mark.asyncio
async def test_setup_returns_one_liner_script_and_the_derived_public_key(client, caplog):
    import logging
    import shlex

    caplog.set_level(logging.DEBUG)
    token = await _bootstrap_owner(client)
    host, gen = await _docker_host_with_key(client, token)
    cid = gen["credential"]["id"]

    r = await client.get(_setup_url(host["id"], cid), params={"sudo": "true", "groups": "docker"}, headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"username", "public_key", "fingerprint", "one_liner", "script", "notes", "groups", "sudo"}
    assert body["public_key"] == gen["public_key"] and body["fingerprint"] == gen["fingerprint"]
    assert (body["username"], body["groups"], body["sudo"]) == ("lattice", ["docker"], True)
    assert body["one_liner"].startswith('S=; [ "$(id -u)" = 0 ] || S=sudo; $S sh -c ')
    inner = shlex.split(body["one_liner"].split("$S sh -c ", 1)[1])[0]
    assert f'K="{gen["public_key"]}"' in inner and "NOPASSWD: ALL" in inner and 'usermod -aG docker "$U"' in inner
    assert "'" not in body["script"] and "restrict,pty" in body["script"]
    assert any("praktisch alles" in n for n in body["notes"])
    assert "PRIVATE KEY" not in r.text and "PRIVATE KEY" not in caplog.text

    # Ohne Schalter: keine Gruppe, keine sudo-Regel.
    plain = (await client.get(_setup_url(host["id"], cid), headers=_auth(token))).json()
    assert (plain["groups"], plain["sudo"]) == ([], False)
    assert "sudoers" not in plain["script"] and "usermod -aG" not in plain["script"]
    # Die Abfrage aendert nichts: kein neuer Zugang.
    assert len((await client.get(f"/api/v1/hosts/{host['id']}/credentials", headers=_auth(token))).json()) == 1


@pytest.mark.asyncio
async def test_setup_offers_only_groups_that_extensions_ask_for(client):
    token = await _bootstrap_owner(client)
    host, gen = await _docker_host_with_key(client, token)
    cid = gen["credential"]["id"]
    get_extension_runtime().ui.register_host_requirement("fake-b", HostRequirementSpec(
        id="video", label="Video", unix_group="video", tags=["gameserver"],
    ))
    get_extension_runtime().ui.register_host_requirement("fake-b", HostRequirementSpec(
        id="kaputt", label="Kaputt", unix_group="docker; rm -rf /", tags=["docker"],
    ))
    url = _setup_url(host["id"], cid)
    # Mehrfach und kommagetrennt; unbekannte, fremde (anderes Tag) und boese Namen fallen weg.
    r = await client.get(
        url, params=[("groups", "docker,video"), ("groups", "sudo"), ("groups", "docker; rm -rf /"), ("groups", "docker")],
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["groups"] == ["docker"]
    assert "rm -rf" not in r.text and "video" not in r.text and "usermod -aG sudo" not in r.text
    # Ein Host ohne das Tag bekommt die Gruppe nicht angeboten.
    plain = await _make_host(client, token, "plain")
    gen2 = (await client.post(GENERATE.format(plain["id"]), json={}, headers=_auth(token))).json()
    r = await client.get(_setup_url(plain["id"], gen2["credential"]["id"]), params={"groups": "docker"}, headers=_auth(token))
    assert r.json()["groups"] == [] and "usermod -aG" not in r.json()["script"]


@pytest.mark.asyncio
async def test_setup_for_root_has_no_useradd_groups_or_sudo(client):
    token = await _bootstrap_owner(client)
    host, gen = await _docker_host_with_key(client, token, username="root")
    r = await client.get(
        _setup_url(host["id"], gen["credential"]["id"]), params={"sudo": "true", "groups": "docker"}, headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["username"], body["groups"], body["sudo"]) == ("root", [], False)
    for word in ("useradd", "usermod", "sudoers", "visudo"):
        assert word not in body["script"]
    assert any("Cluster" in n and "alle Knoten" in n for n in body["notes"])


@pytest.mark.asyncio
async def test_setup_rejects_password_credentials_and_non_linux_hosts(client):
    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    pw = (await client.post(
        f"/api/v1/hosts/{host['id']}/credentials",
        json={"kind": "ssh_password", "username": "lattice", "secret_value": "GEHEIM-PW-123"}, headers=_auth(token),
    )).json()
    r = await client.get(_setup_url(host["id"], pw["id"]), headers=_auth(token))
    assert r.status_code == 422 and "Schlüssel" in r.json()["detail"] and "GEHEIM-PW-123" not in r.text

    win = await _make_host(client, token, "winbox", os_family="windows")
    key = (await client.post(GENERATE.format(win["id"]), json={"username": "Administrator"}, headers=_auth(token))).json()
    r = await client.get(_setup_url(win["id"], key["credential"]["id"]), headers=_auth(token))
    assert r.status_code == 422
    assert r.json()["detail"] == "Den Einrichtungsbefehl gibt es nur für Linux-Server."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "username",
    ["a'; rm -rf / #", "lattice\nroot", "$(id)", "`id`", "max mustermann", "user@domain", "DOMAIN\\user", "Lattice", "x" * 40, "lattice;id"],
)
async def test_setup_refuses_usernames_that_are_not_plain_linux_names(client, db_session, username):
    """Seit der Erweiterung der Benutzernamen (Windows) koennen Zugaenge Leerzeichen, @ und \\
    tragen, und ueber die Datenbank koennte alles drinstehen -- in den Befehl kommt nichts davon."""
    from nodvard_deck.models import HostCredential

    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    gen = (await client.post(GENERATE.format(host["id"]), json={}, headers=_auth(token))).json()
    credential = await db_session.get(HostCredential, gen["credential"]["id"])
    credential.username = username
    await db_session.commit()
    r = await client.get(_setup_url(host["id"], credential.id), headers=_auth(token))
    assert r.status_code == 422, r.text
    assert "rm -rf" not in r.text and "id)" not in r.text
    assert "kein üblicher Linux-Benutzername" in r.json()["detail"]


@pytest.mark.asyncio
async def test_setup_ignores_the_comment_of_a_pasted_key(client):
    """Bei einem eingefuegten Schluessel bestimmt der Nutzer den Kommentar im Schluessel --
    im Befehl steht immer `lattice@<Kurzname>`."""
    import asyncssh

    token = await _bootstrap_owner(client)
    host = await _make_host(client, token, "bastel-pi")
    evil = asyncssh.generate_private_key("ssh-ed25519", comment="x'; touch /tmp/pwned #$(id)\nssh-rsa AAAA evil")
    created = await client.post(
        f"/api/v1/hosts/{host['id']}/credentials",
        json={"kind": "ssh_key", "username": "lattice", "secret_value": evil.export_private_key("openssh").decode()},
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    r = await client.get(_setup_url(host["id"], created.json()["id"]), headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["public_key"].endswith(" lattice@bastel-pi") and len(body["public_key"].split(" ")) == 3
    for text in (body["one_liner"], body["script"]):
        assert "touch /tmp" not in text and "evil" not in text and "pwned" not in text
    assert body["one_liner"].count("'") == 2


@pytest.mark.asyncio
async def test_setup_with_a_broken_stored_key_gives_a_fixed_message(client, db_session, test_settings):
    from nodvard_deck.core import vault
    from nodvard_deck.models import HostCredential

    token = await _bootstrap_owner(client)
    host = await _make_host(client, token)
    gen = (await client.post(GENERATE.format(host["id"]), json={}, headers=_auth(token))).json()
    credential = await db_session.get(HostCredential, gen["credential"]["id"])
    await vault.replace_secret_value(db_session, vault.load_keyring(test_settings), credential.secret_id, "GEHEIM kein Schluessel")
    await db_session.commit()
    r = await client.get(_setup_url(host["id"], credential.id), headers=_auth(token))
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "Der gespeicherte Schlüssel lässt sich nicht lesen."
    assert "GEHEIM" not in r.text


@pytest.mark.asyncio
async def test_setup_needs_write_permission_and_matching_host_and_credential(client, db_session):
    token = await _bootstrap_owner(client)
    host, gen = await _docker_host_with_key(client, token)
    other = await _make_host(client, token, "anderer")
    cid = gen["credential"]["id"]
    assert (await client.get(_setup_url(host["id"], cid))).status_code == 401
    assert (await client.get(_setup_url("nope", cid), headers=_auth(token))).status_code == 404
    assert (await client.get(_setup_url(host["id"], "nope"), headers=_auth(token))).status_code == 404
    assert (await client.get(_setup_url(other["id"], cid), headers=_auth(token))).status_code == 404, "Zugang eines anderen Servers"
    for role in ("operator", "viewer"):
        tok = await _role_token(client, db_session, role, f"s-{role}")
        assert (await client.get(_setup_url(host["id"], cid), headers=_auth(tok))).status_code == 403
    assert (await client.get(_setup_url(host["id"], cid), params={"sudo": "vielleicht"}, headers=_auth(token))).status_code == 422


# ---------------------------------------------------------------------------
# POST /hosts/{id}/credentials/{cid}/make-default
# ---------------------------------------------------------------------------


def _default_url(host_id: str, credential_id: str) -> str:
    return f"/api/v1/hosts/{host_id}/credentials/{credential_id}/make-default"


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


async def _mark_login_ok(db_session, host: dict, credential: dict) -> None:
    """So, als haette „Verbindung pruefen“ mit diesem Zugang gerade die Anmeldung geschafft
    (die echte Pruefung steht in test_host_check_api.py)."""
    from nodvard_deck.models import Host, HostCredential
    from nodvard_deck.services import host_check

    host_check.mark_login_ok(await db_session.get(Host, host["id"]), await db_session.get(HostCredential, credential["id"]))


async def _two_keys(client, token):
    host = await _make_host(client, token)
    old = (await client.post(GENERATE.format(host["id"]), json={}, headers=_auth(token))).json()["credential"]
    new = (await client.post(GENERATE.format(host["id"]), json={}, headers=_auth(token))).json()["credential"]
    assert (old["is_default"], new["is_default"]) == (True, False)
    return host, old, new


@pytest.mark.asyncio
async def test_make_default_switches_and_keeps_the_old_credential(client, db_session, pool):
    token = await _bootstrap_owner(client)
    host, old, new = await _two_keys(client, token)
    pool.dropped.clear()
    pool.retired.clear()
    r = await client.post(_default_url(host["id"], new["id"]), json={}, headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["id"] == new["id"] and r.json()["is_default"] is True
    assert r.json()["notice"] is None, "nichts geloescht, kein Hinweis noetig"
    listed = {c["id"]: c["is_default"] for c in (await client.get(f"/api/v1/hosts/{host['id']}/credentials", headers=_auth(token))).json()}
    assert listed == {old["id"]: False, new["id"]: True}
    assert (await client.get(f"/api/v1/hosts/{host['id']}", headers=_auth(token))).json()["credential"]["id"] == new["id"]
    # Nichts wird geloescht: offene Terminals laufen weiter, der alte Zugang wird nur ausgemustert.
    assert pool.retired == [host["id"]] and pool.dropped == []
    [row] = await _audit_rows(db_session, "host.credential_made_default")
    assert row.detail == {"credential_id": new["id"], "previous_credential_id": old["id"], "deleted_previous": False}
    assert await _audit_rows(db_session, "host.credential_deleted") == []


@pytest.mark.asyncio
async def test_make_default_with_delete_previous_removes_the_old_credential_and_its_secret(client, db_session, pool):
    from sqlalchemy import select

    from nodvard_deck.models import HostCredential, Secret

    token = await _bootstrap_owner(client)
    host, old, new = await _two_keys(client, token)
    old_secret_id = (await db_session.get(HostCredential, old["id"])).secret_id
    new_secret_id = (await db_session.get(HostCredential, new["id"])).secret_id
    pool.dropped.clear()
    pool.retired.clear()
    await _mark_login_ok(db_session, host, new)  # `delete_previous` verlangt eine frische, erfolgreiche Pruefung

    r = await client.post(_default_url(host["id"], new["id"]), json={"delete_previous": True}, headers=_auth(token))
    assert r.status_code == 200, r.text
    notice = r.json()["notice"]
    assert "authorized_keys" in notice and "bleibt" in notice and "entfernen" in notice
    assert [c["id"] for c in (await client.get(f"/api/v1/hosts/{host['id']}/credentials", headers=_auth(token))).json()] == [new["id"]]
    db_session.expire_all()
    assert await db_session.get(Secret, old_secret_id) is None, "das alte Geheimnis ist aus dem Vault"
    assert await db_session.get(Secret, new_secret_id) is not None
    assert (await db_session.execute(select(HostCredential).where(HostCredential.id == old["id"]))).first() is None
    assert pool.dropped and set(pool.dropped) == {host["id"]}, "der geloeschte Zugang darf nicht weiterlaufen"
    assert pool.retired == []

    [made] = await _audit_rows(db_session, "host.credential_made_default")
    assert made.detail == {"credential_id": new["id"], "previous_credential_id": old["id"], "deleted_previous": True}
    [deleted] = await _audit_rows(db_session, "host.credential_deleted")
    assert deleted.detail == {"credential_id": old["id"], "kind": "ssh_key", "username": "lattice", "port": 22}


@pytest.mark.asyncio
async def test_make_default_on_the_current_default_changes_nothing(client, db_session, pool):
    token = await _bootstrap_owner(client)
    host, old, new = await _two_keys(client, token)
    # Body darf ganz fehlen.
    r = await client.post(_default_url(host["id"], old["id"]), headers=_auth(token))
    assert r.status_code == 200 and r.json()["is_default"] is True
    r = await client.post(_default_url(host["id"], old["id"]), json={"delete_previous": True}, headers=_auth(token))
    assert r.status_code == 200
    ids = {c["id"] for c in (await client.get(f"/api/v1/hosts/{host['id']}/credentials", headers=_auth(token))).json()}
    assert ids == {old["id"], new["id"]}, "delete_previous loescht nichts, wenn der Zugang schon Standard ist"
    assert await _audit_rows(db_session, "host.credential_made_default") == []


@pytest.mark.asyncio
async def test_make_default_needs_write_permission_and_matching_credential(client, db_session):
    token = await _bootstrap_owner(client)
    host, old, new = await _two_keys(client, token)
    other = await _make_host(client, token, "anderer")
    assert (await client.post(_default_url(host["id"], new["id"]))).status_code == 401
    assert (await client.post(_default_url("nope", new["id"]), headers=_auth(token))).status_code == 404
    assert (await client.post(_default_url(host["id"], "nope"), headers=_auth(token))).status_code == 404
    assert (await client.post(_default_url(other["id"], new["id"]), headers=_auth(token))).status_code == 404
    for role in ("operator", "viewer"):
        tok = await _role_token(client, db_session, role, f"m-{role}")
        assert (await client.post(_default_url(host["id"], new["id"]), headers=_auth(tok))).status_code == 403
    assert (await client.get(f"/api/v1/hosts/{host['id']}", headers=_auth(token))).json()["credential"]["id"] == old["id"]
