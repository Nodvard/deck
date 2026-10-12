"""Proxmox in einem Schritt verbinden (`POST /ext/proxmox/connect...`, password_connect.py).

Gegen einen Proxmox-Nachbau auf einem echten TCP-Port mit echtem TLS und echtem selbstsigniertem Zertifikat (wie
`mock_proxmox_https` in test_ext_proxmox.py): Anmeldung mit Ticket und CSRF-Schutz, Zwei-Faktor-Aufforderung im
Format ab Proxmox VE 7.1, Rollen, Benutzer, Berechtigungen und API-Token mit Privilegientrennung. Die echte
Erweiterung laeuft in der echten App (`client`-Fixture).
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import ipaddress
import json
import logging
import socket
import sys
from pathlib import Path
from urllib.parse import quote

import pytest
import pytest_asyncio
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from nodvard_sdk import PeerCertificate
from sqlalchemy import select

from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import ExtensionRecord
from nodvard_deck.models.security import AuditEntry
from nodvard_deck.services import extensions as extensions_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
PASSWORD = "Sehr-Geheim-Pw!7"
TICKET = "PVE:root@pam:6512ABCD::FAKE-SIGNATUR+/="
CSRF = "6512ABCD:FAKE-CSRF"
TFA_CODE = "123456"
LEAK = "INTERNES-PROXMOX-DETAIL"
ALL_PRIVS = (
    "Datastore.Allocate,Datastore.AllocateSpace,Datastore.Audit,Permissions.Modify,Pool.Allocate,SDN.Use,Sys.Audit,"
    "Sys.Console,Sys.Modify,Sys.PowerMgmt,User.Modify,VM.Allocate,VM.Audit,VM.Backup,VM.Config.CPU,VM.Config.Disk,"
    "VM.Config.Memory,VM.Config.Network,VM.Config.Options,VM.Console,VM.GuestAgent.Audit,VM.PowerMgmt,VM.Snapshot"
)
STORAGES = [
    {"storage": "local", "type": "dir", "content": "iso,vztmpl,backup"},
    {"storage": "local-lvm", "type": "lvmthin", "content": "images,rootdir"},
    {"storage": "nas", "type": "nfs", "content": "backup"},
]


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _cert_files(tmp_path: Path, name: str, *, ca: tuple | None = None) -> tuple[Path, Path, str]:
    """Selbstsigniert -- oder, mit `ca=(Name, Schluessel)`, von dieser Zertifizierungsstelle unterschrieben."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pve1.example")])
    issuer, signer = ca if ca is not None else (subject, key)
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(signer, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / f"{name}.crt", tmp_path / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    digest = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).digest()
    return cert_path, key_path, ":".join(f"{b:02X}" for b in digest)


def _build_fake_pve() -> tuple[FastAPI, dict]:
    state: dict = {
        "tfa": None,
        "roles": {"Administrator": set(ALL_PRIVS.split(",")), "PVEAuditor": {"Sys.Audit", "VM.Audit"}},
        "users": {"root@pam": {"enable": 1}},
        "tokens": {},
        "acl": set(),
        "requests": [],
        "ticket_forms": [],
        "fail": {},
        "deleted_tokens": [],
        "set_cookie": False,
        "token_with_cookie": [],
        "storages": [dict(row) for row in STORAGES],
        "pools": {},
        "ticket_redirect": None,
        "swap_after": None,
        "swap": None,
        "token_requests": [],
    }
    app = FastAPI()

    def maybe_swap(path: str, answer):
        """Tauscht nach dieser Antwort das Zertifikat des Servers (`state["swap_after"]` = Pfad) und schliesst die
        Verbindung: jede weitere Anfrage braucht einen neuen Handshake und sieht das neue Zertifikat."""
        if state["swap_after"] == path and state["swap"] is not None:
            state["swap_after"] = None
            state["swap"]()
            answer = answer if isinstance(answer, JSONResponse) else JSONResponse(answer)
            answer.headers["connection"] = "close"
        return answer

    def ticket_ok(request: Request) -> bool:
        if request.cookies.get("PVEAuthCookie") != TICKET:
            return False
        return request.method == "GET" or request.headers.get("CSRFPreventionToken") == CSRF

    def token_of(request: Request) -> str | None:
        header = request.headers.get("Authorization", "")
        if not header.startswith("PVEAPIToken="):
            return None
        token_id, _, value = header[len("PVEAPIToken="):].rpartition("=")
        token = state["tokens"].get(token_id)
        if token and token["value"] == value and "PVEAuthCookie" in request.headers.get("cookie", ""):
            state["token_with_cookie"].append(request.url.path)
        if token and token["value"] == value:
            state["token_requests"].append((token_id, request.method, request.url.path))
        return token_id if token and token["value"] == value else None

    def may(principal: tuple[str, str], priv: str, at: str = "/") -> bool:
        return any(
            kind == principal[0] and who == principal[1] and priv in state["roles"].get(role, set())
            for path, kind, who, role in state["acl"]
            if path == at
        )

    def token_may(token_id: str, priv: str, at: str = "/") -> bool:
        user = token_id.split("!")[0]
        if state["tokens"][token_id]["privsep"]:
            return may(("token", token_id), priv, at) and may(("user", user), priv, at)
        return may(("user", user), priv, at)

    def denied(status: int = 401, message: str = "authentication failure") -> JSONResponse:
        return JSONResponse({"data": None, "message": f"{message} {LEAK}"}, status_code=status)

    @app.middleware("http")
    async def record(request: Request, call_next):
        form = None
        if request.method in ("POST", "PUT"):
            body = await request.body()
            form = body.decode()
        state["requests"].append((request.method, request.url.path, form))
        failing = state["fail"].get((request.method, request.url.path.removeprefix("/api2/json")))
        if failing is not None:
            return JSONResponse({"data": None, "message": LEAK, "errors": {"x": LEAK}}, status_code=failing)
        return await call_next(request)

    @app.post("/api2/json/access/ticket")
    async def ticket(request: Request):
        form = dict(await request.form())
        state["ticket_forms"].append(form)
        if state["ticket_redirect"]:
            return JSONResponse({"data": None}, status_code=307, headers={"location": state["ticket_redirect"]})
        if "tfa-challenge" in form:
            if form.get("tfa-challenge") != state.get("challenge") or form.get("password") != f"totp:{TFA_CODE}":
                return denied()
            return {"data": {"username": "root@pam", "ticket": TICKET, "CSRFPreventionToken": CSRF}}
        if form.get("username") != "root@pam" or form.get("password") != PASSWORD:
            return denied()
        if state["tfa"] is not None:
            challenge = "PVE:!tfa!" + quote(json.dumps(state["tfa"]), safe="") + ":6512ABCD::TFA-SIG"
            state["challenge"] = challenge
            return maybe_swap(
                "/access/ticket",
                {"data": {"username": "root@pam", "ticket": challenge, "CSRFPreventionToken": CSRF, "NeedTFA": 1}},
            )
        answer = JSONResponse({"data": {"username": "root@pam", "ticket": TICKET, "CSRFPreventionToken": CSRF, "cap": {}}})
        if state["set_cookie"]:
            # Die echte API setzt kein Cookie -- ein Server, der es doch tut, darf die Token-Probe nicht beeinflussen.
            answer.headers["set-cookie"] = f"PVEAuthCookie={TICKET}; Path=/; Secure"
        return maybe_swap("/access/ticket", answer)

    @app.get("/api2/json/access/roles")
    async def roles(request: Request):
        if not ticket_ok(request):
            return denied()
        return {"data": [{"roleid": r, "privs": ",".join(sorted(p))} for r, p in state["roles"].items()]}

    @app.post("/api2/json/access/roles")
    async def add_role(request: Request):
        if not ticket_ok(request):
            return denied()
        form = dict(await request.form())
        privs = set(form["privs"].split(","))
        unknown = privs - state["roles"]["Administrator"]
        if unknown:
            return denied(400, f"invalid privilege {sorted(unknown)}")
        state["roles"][form["roleid"]] = privs
        return {"data": None}

    @app.put("/api2/json/access/roles/{roleid}")
    async def change_role(roleid: str, request: Request):
        if not ticket_ok(request):
            return denied()
        form = dict(await request.form())
        privs = set(form["privs"].split(","))
        state["roles"][roleid] = (state["roles"][roleid] | privs) if form.get("append") == "1" else privs
        return {"data": None}

    @app.get("/api2/json/access/users")
    async def users(request: Request):
        if not ticket_ok(request):
            return denied()
        return {"data": [{"userid": u, **v} for u, v in state["users"].items()]}

    @app.post("/api2/json/access/users")
    async def add_user(request: Request):
        if not ticket_ok(request):
            return denied()
        form = dict(await request.form())
        assert "password" not in form
        state["users"][form["userid"]] = {"enable": int(form.get("enable", 1)), "comment": form.get("comment")}
        return {"data": None}

    @app.put("/api2/json/access/users/{userid}")
    async def change_user(userid: str, request: Request):
        if not ticket_ok(request):
            return denied()
        form = dict(await request.form())
        state["users"][userid]["enable"] = int(form["enable"])
        return {"data": None}

    @app.put("/api2/json/access/acl")
    async def acl(request: Request):
        if not ticket_ok(request):
            return denied()
        form = dict(await request.form())
        assert form["propagate"] == "1"
        for kind in ("users", "tokens"):
            if form.get(kind):
                state["acl"].add((form["path"], kind[:-1], form[kind], form["roles"]))
        return {"data": None}

    @app.get("/api2/json/access/acl")
    async def acl_list(request: Request):
        if not ticket_ok(request):
            return denied()
        return {"data": [
            {"path": path, "type": kind, "ugid": who, "roleid": role, "propagate": 1}
            for path, kind, who, role in sorted(state["acl"])
        ]}

    def token_info(full: str) -> dict:
        token = state["tokens"][full]
        return {"privsep": token["privsep"], "expire": 0, "comment": token.get("comment", "")}

    @app.get("/api2/json/access/users/{userid}/token")
    async def tokens(userid: str, request: Request):
        if not ticket_ok(request):
            return denied()
        return {"data": [
            {"tokenid": t.split("!")[1], **token_info(t)} for t in state["tokens"] if t.startswith(userid + "!")
        ]}

    @app.get("/api2/json/access/users/{userid}")
    async def read_user(userid: str, request: Request):
        token_id = token_of(request)
        if token_id is None and not ticket_ok(request):
            return denied()
        if token_id is not None and not token_may(token_id, "Sys.Audit"):
            return denied(403, "Permission check failed (/access/groups, User.Modify|Sys.Audit)")
        if userid not in state["users"]:
            return denied(500, f"no such user ('{userid}')")
        return {"data": {
            "enable": 1,
            "tokens": {t.split("!")[1]: token_info(t) for t in state["tokens"] if t.startswith(userid + "!")},
        }}

    @app.post("/api2/json/access/users/{userid}/token/{tokenid}")
    async def add_token(userid: str, tokenid: str, request: Request):
        if not ticket_ok(request):
            return denied()
        form = dict(await request.form())
        full = f"{userid}!{tokenid}"
        if full in state["tokens"]:
            return denied(500, "Token already exists")
        value = f"wert-{len(state['tokens']) + 1}-0000-geheim"
        state["tokens"][full] = {"value": value, "privsep": int(form["privsep"]), "comment": form.get("comment", "")}
        return maybe_swap(
            f"/access/users/{userid}/token/{tokenid}",
            {"data": {"full-tokenid": full, "info": {"privsep": form["privsep"]}, "value": value}},
        )

    @app.delete("/api2/json/access/users/{userid}/token/{tokenid}")
    async def delete_token(userid: str, tokenid: str, request: Request):
        if not ticket_ok(request):
            return denied()
        state["tokens"].pop(f"{userid}!{tokenid}", None)
        state["deleted_tokens"].append(f"{userid}!{tokenid}")
        return {"data": None}

    @app.get("/api2/json/cluster/status")
    async def cluster_status(request: Request):
        if not ticket_ok(request):
            return denied()
        return maybe_swap("/cluster/status", {"data": [{"type": "node", "name": "pve1", "local": 1, "online": 1}]})

    @app.get("/api2/json/nodes")
    async def nodes(request: Request):
        if token_of(request) is None and not ticket_ok(request):
            return denied()
        return {"data": [{"node": "pve1", "status": "online"}]}

    @app.get("/api2/json/nodes/{node}/status")
    async def node_status(node: str, request: Request):
        token_id = token_of(request)
        if token_id is None:
            return denied()
        if not token_may(token_id, "Sys.Audit"):
            return denied(403, "Permission check failed (/nodes/pve1, Sys.Audit)")
        return {"data": {"cpu": 0.1}}

    @app.get("/api2/json/version")
    async def version(request: Request):
        if token_of(request) is None:
            return denied()
        return {"data": {"version": "9.0.3"}}

    @app.get("/api2/json/storage")
    async def storage(request: Request):
        if not ticket_ok(request):
            return denied()
        return {"data": state["storages"]}

    @app.get("/api2/json/pools")
    async def pools(request: Request):
        if not ticket_ok(request):
            return denied()
        return {"data": [{"poolid": k, "comment": v.get("comment", "")} for k, v in state["pools"].items()]}

    @app.post("/api2/json/pools")
    async def add_pool(request: Request):
        if not ticket_ok(request):
            return denied()
        form = dict(await request.form())
        if form["poolid"] in state["pools"]:
            return denied(500, "pool already exists")
        state["pools"][form["poolid"]] = {"comment": form.get("comment", "")}
        return {"data": None}

    @app.get("/api2/json/pools/{poolid}")
    async def pool(poolid: str, request: Request):
        if not ticket_ok(request):
            return denied()
        if poolid not in state["pools"]:
            return denied(500, "pool does not exist")
        entry = state["pools"][poolid]
        return {"data": {"comment": entry.get("comment", ""), "members": list(entry.get("members", []))}}

    @app.get("/api2/json/cluster/backup")
    async def backup_jobs(request: Request):
        token_id = token_of(request)
        if token_id is None:
            return denied()
        if not token_may(token_id, "Sys.Audit"):
            return denied(403, "Permission check failed (/, Sys.Audit)")
        return {"data": []}

    @app.get("/api2/json/nodes/{node}/{kind}")
    async def guests(node: str, kind: str, request: Request):
        if token_of(request) is None or kind not in ("qemu", "lxc"):
            return denied()
        return {"data": []}

    return app, state


async def _background_done(loaded, timeout_s: float = 15.0) -> None:
    pending = [t.task for t in loaded.tasks if t.task is not None and not t.task.done()]
    if pending:
        await asyncio.wait(pending, timeout=timeout_s)


def _ca(tmp_path: Path) -> tuple:
    """Eigene Zertifizierungsstelle; ihre Datei steht danach in `SSL_CERT_FILE` (der normale Weg, ihr zu vertrauen)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test-CA")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    (tmp_path / "ca.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return name, key


@pytest.fixture
def trusted_ca(tmp_path: Path, monkeypatch):
    ca = _ca(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "ca.crt"))
    return ca


@pytest_asyncio.fixture
async def fake_pve(request, tmp_path: Path):
    app, state = _build_fake_pve()
    ca = request.getfixturevalue("trusted_ca") if "trusted_ca" in request.fixturenames else None
    cert_path, key_path, fingerprint = _cert_files(tmp_path, "pve", ca=ca)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="off",
        ssl_certfile=str(cert_path), ssl_keyfile=str(key_path),
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.ensure_future(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)
    swapped: list[str] = []

    def swap() -> None:
        """Neues Zertifikat (anderer Schluessel) fuer jeden neuen Handshake -- wie ein Server, der mitten im Ablauf
        ein anderes zeigt."""
        new_cert, new_key, new_fingerprint = _cert_files(tmp_path, f"pve-neu-{len(swapped)}", ca=ca)
        config.ssl.load_cert_chain(str(new_cert), str(new_key))
        swapped.append(new_fingerprint)

    state["swap"] = swap
    try:
        yield {
            "base_url": f"https://127.0.0.1:{port}", "port": port, "state": state, "fingerprint": fingerprint,
            "tmp": tmp_path, "swapped": swapped,
        }
    finally:
        # Offene Verbindungen der Erweiterung zuerst schliessen: sonst wartet der Server beim Herunterfahren bis zu 30 s
        # auf das TLS-Ende der Gegenseite (eine ruhende Verbindung im Pool antwortet darauf nicht).
        for ext_id in ("proxmox", "backups"):
            loaded = get_extension_runtime().loaded.get(ext_id)
            if loaded is None:
                continue
            # Das Einlesen nach dem Verbinden laeuft im Hintergrund: erst abwarten, sonst oeffnet es nach dem
            # Schliessen noch eine Verbindung (und schreibt nach dem Test in die Datenbank).
            await _background_done(loaded)
            if loaded.ctx is not None:
                await loaded.ctx.http.aclose()
        server.should_exit = True
        await serve_task


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _user_token(client, db_session, *, username: str, role: str) -> str:
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.flush()
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": "whatever123"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _owner_with_proxmox(
    client, db_session, test_settings, connections: list[dict] | None = None, *, backups: list[dict] | None = None,
) -> str:
    """Owner anmelden, Proxmox VE einschalten; mit `backups` (Verbindungsliste) auch Proxmox-Backups."""
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post(
        "/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    token = login.json()["access_token"]
    enabled = await client.post("/api/v1/extensions/proxmox/enable", headers=_h(token))
    assert enabled.status_code == 200, enabled.text
    record = await db_session.get(ExtensionRecord, "proxmox")
    record.settings = {"connections": list(connections or [])}
    if backups is not None:
        enabled = await client.post("/api/v1/extensions/backups/enable", headers=_h(token))
        assert enabled.status_code == 200, enabled.text
        backups_record = await db_session.get(ExtensionRecord, "backups")
        backups_record.settings = {"connections": list(backups)}
    await db_session.flush()
    return token


async def _backups_settings(db_session) -> dict:
    record = await db_session.get(ExtensionRecord, "backups")
    await db_session.refresh(record)
    return dict(record.settings or {})


async def _stored_secret(ext_id: str, label: str) -> str | None:
    ctx = get_extension_runtime().loaded[ext_id].ctx
    if not await ctx.secrets.exists(label):
        return None
    async with ctx.vault_use(await ctx.secrets.get_handle(label)) as value:
        return value


async def _connect(client, token: str, base_url: str, **extra):
    body = {"base_url": base_url, "username": "root@pam", "password": PASSWORD, **extra}
    answer = await client.post("/api/v1/ext/proxmox/connect", json=body, headers=_h(token))
    # Das Einlesen danach laeuft im Hintergrund weiter; fuer feste Reihenfolgen im Test hier abwarten.
    loaded = get_extension_runtime().loaded.get("proxmox")
    if loaded is not None:
        await _background_done(loaded)
    return answer


async def _settings(db_session) -> dict:
    record = await db_session.get(ExtensionRecord, "proxmox")
    await db_session.refresh(record)
    return dict(record.settings or {})


async def _audit_rows(db_session) -> list[AuditEntry]:
    return list((await db_session.execute(
        select(AuditEntry).where(AuditEntry.action == "proxmox.connect_with_password").order_by(AuditEntry.ts)
    )).scalars())


def _sent_password(state: dict) -> int:
    return sum(1 for form in state["ticket_forms"] if form.get("password") == PASSWORD)


# ---------------------------------------------------------------------------
# Zertifikat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_certificate_step_shows_the_fingerprint_without_sending_anything(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await client.post(
        "/api/v1/ext/proxmox/connect/certificate", json={"base_url": f"127.0.0.1:{fake_pve['port']}"}, headers=_h(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["base_url"] == fake_pve["base_url"]
    assert body["certificate"]["sha256"] == fake_pve["fingerprint"]
    assert body["certificate"]["trusted"] is False
    assert "pve1.example" in body["certificate"]["subject"]
    assert fake_pve["state"]["requests"] == []


@pytest.mark.asyncio
async def test_unconfirmed_certificate_stops_before_the_password_goes_out(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"])
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "certificate_unconfirmed"
    assert r.json()["certificate"]["sha256"] == fake_pve["fingerprint"]
    assert "nicht gesendet" in r.json()["detail"]
    assert fake_pve["state"]["requests"] == []
    assert await _audit_rows(db_session) == []


@pytest.mark.asyncio
async def test_certificate_changed_between_showing_and_login_sends_nothing(
    client, db_session, test_settings, fake_pve, monkeypatch,
):
    """Beim Anzeigen sah der Server noch nach dem bestaetigten Zertifikat aus, bei der Anmeldung zeigt er ein anderes:
    die gepinnte Verbindung scheitert im Handshake, das Passwort geht nicht hinaus."""
    token = await _owner_with_proxmox(client, db_session, test_settings)
    confirmed = "AB:" * 31 + "AB"
    loaded = get_extension_runtime().loaded["proxmox"]
    real = loaded.ctx.http.peer_certificate
    calls = []

    async def peer_certificate(url, *, timeout_s=10.0):
        calls.append(url)
        if len(calls) == 1:
            return PeerCertificate(sha256=confirmed, trusted=False)
        return await real(url, timeout_s=timeout_s)

    monkeypatch.setattr(loaded.ctx.http, "peer_certificate", peer_certificate)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=confirmed.replace(":", "").lower())
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "certificate_changed"
    assert r.json()["certificate"]["sha256"] == fake_pve["fingerprint"]
    assert fake_pve["state"]["requests"] == []
    assert (await _settings(db_session))["connections"] == []
    assert await _audit_rows(db_session) == []


@pytest.mark.asyncio
async def test_confirmed_fingerprint_that_does_not_match_is_refused(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint="CD" * 32)
    assert r.status_code == 409
    assert r.json()["code"] == "certificate_changed"
    assert fake_pve["state"]["requests"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["http://127.0.0.1:8006", "ws://127.0.0.1:8006"])
async def test_plain_http_is_refused(client, db_session, test_settings, fake_pve, address):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    for path, body in (
        ("/api/v1/ext/proxmox/connect/certificate", {"base_url": address}),
        ("/api/v1/ext/proxmox/connect", {"base_url": address, "password": PASSWORD}),
    ):
        r = await client.post(path, json=body, headers=_h(token))
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "https_required"
    assert fake_pve["state"]["requests"] == []


# ---------------------------------------------------------------------------
# Erfolg und Wiederholung
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_creates_role_user_token_and_saves_the_connection(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "connected"
    assert body["connection"] == {
        "name": "pve1", "base_url": fake_pve["base_url"], "token_id": "nodvard@pve!deck",
        "tls_insecure_skip_verify": False, "tls_fingerprint_sha256": fake_pve["fingerprint"],
        "tls_fingerprint_invalid": False, "enabled": True, "has_token": True,
    }
    assert body["nodes"] == ["pve1"]
    state = fake_pve["state"]
    # Genau die Rechte der Erweiterung, keine fuer Sicherungen.
    assert state["roles"]["NodvardDeck"] == {
        "Sys.Audit", "VM.Audit", "VM.GuestAgent.Audit", "Datastore.Audit", "VM.PowerMgmt", "VM.Snapshot",
        "VM.Config.CPU", "VM.Config.Memory", "VM.Config.Options", "VM.Console", "Sys.PowerMgmt", "Sys.Modify",
        "VM.Config.Disk",
    }
    assert state["users"]["nodvard@pve"]["enable"] == 1
    assert state["tokens"]["nodvard@pve!deck"]["privsep"] == 1
    assert state["acl"] == {("/", "user", "nodvard@pve", "NodvardDeck"), ("/", "token", "nodvard@pve!deck", "NodvardDeck")}
    assert _sent_password(state) == 1

    # Eingetragen wie im Einstellungsformular, mit dem bestaetigten Fingerabdruck statt "nicht pruefen", und die
    # Erweiterung arbeitet damit (Token aus dem Tresor, jede Anfrage an genau dieses Zertifikat gebunden).
    assert (await _settings(db_session))["connections"] == [{
        "name": "pve1", "base_url": fake_pve["base_url"], "token_id": "nodvard@pve!deck",
        "tls_insecure_skip_verify": False, "tls_fingerprint_sha256": fake_pve["fingerprint"], "enabled": True,
    }]
    listed = await client.get("/api/v1/ext/proxmox/connections", headers=_h(token))
    assert listed.json()[0]["has_token"] is True
    loaded = get_extension_runtime().loaded["proxmox"]
    report = await loaded.instance.health(loaded.ctx)
    assert report.healthy is True, report.message

    (row,) = await _audit_rows(db_session)
    assert row.outcome == "success"
    assert row.actor_type == "user"
    assert row.detail["token_id"] == "nodvard@pve!deck"
    assert row.detail["fingerprint"] == fake_pve["fingerprint"]
    assert PASSWORD not in json.dumps(row.detail)


@pytest.mark.asyncio
async def test_a_certificate_from_a_known_authority_needs_no_confirmation(
    client, db_session, test_settings, trusted_ca, fake_pve,
):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    cert = await client.post("/api/v1/ext/proxmox/connect/certificate", json={"base_url": fake_pve["base_url"]}, headers=_h(token))
    assert cert.json()["certificate"]["trusted"] is True
    r = await _connect(client, token, fake_pve["base_url"])
    assert r.status_code == 200, r.text
    assert r.json()["connection"]["tls_insecure_skip_verify"] is False
    assert not any("Selbstsigniertes" in note for note in r.json()["notes"])
    loaded = get_extension_runtime().loaded["proxmox"]
    assert (await loaded.instance.health(loaded.ctx)).healthy is True
    (row,) = await _audit_rows(db_session)
    assert row.detail["certificate"] == "von einer Zertifizierungsstelle bestätigt"


@pytest.mark.asyncio
async def test_the_token_probe_never_carries_a_session_cookie(client, db_session, test_settings, fake_pve):
    fake_pve["state"]["set_cookie"] = True
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    assert fake_pve["state"]["token_with_cookie"] == []


@pytest.mark.asyncio
async def test_the_token_probe_never_carries_a_session_cookie_with_a_known_authority(
    client, db_session, test_settings, trusted_ca, fake_pve,
):
    fake_pve["state"]["set_cookie"] = True
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"])
    assert r.status_code == 200, r.text
    assert r.json()["connection"]["tls_insecure_skip_verify"] is False
    assert fake_pve["state"]["token_with_cookie"] == []


@pytest.mark.asyncio
async def test_read_only_access_gets_its_own_smaller_role(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"], access="read", name="lab")
    assert r.status_code == 200, r.text
    assert r.json()["connection"]["name"] == "lab"
    assert fake_pve["state"]["roles"]["NodvardDeckRO"] == {"Sys.Audit", "VM.Audit", "VM.GuestAgent.Audit", "Datastore.Audit"}
    assert ("/", "token", "nodvard@pve!deck", "NodvardDeckRO") in fake_pve["state"]["acl"]


@pytest.mark.asyncio
async def test_a_role_with_more_rights_than_needed_is_named(client, db_session, test_settings, fake_pve):
    fake_pve["state"]["roles"]["NodvardDeck"] = {"Sys.Audit", "VM.Backup", "Permissions.Modify"}
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert "VM.Backup" in body["privileges"] and "Permissions.Modify" in body["privileges"]
    assert set(body["privileges"]) == fake_pve["state"]["roles"]["NodvardDeck"]
    note = next(n for n in body["notes"] if "mehr" in n)
    assert "Permissions.Modify, VM.Backup" in note
    (row,) = await _audit_rows(db_session)
    assert row.detail["extra_privileges"] == ["Permissions.Modify", "VM.Backup"]


@pytest.mark.asyncio
async def test_privileges_this_version_does_not_know_are_left_out(client, db_session, test_settings, fake_pve):
    """Proxmox VE 8 kennt `VM.GuestAgent.Audit` noch nicht (und lehnt die ganze Rolle sonst ab)."""
    fake_pve["state"]["roles"]["Administrator"].discard("VM.GuestAgent.Audit")
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    assert "VM.GuestAgent.Audit" not in fake_pve["state"]["roles"]["NodvardDeck"]
    assert "VM.Monitor" not in fake_pve["state"]["roles"]["NodvardDeck"]
    assert any("VM.GuestAgent.Audit" in note for note in r.json()["notes"])


@pytest.mark.asyncio
async def test_running_it_again_reuses_everything_and_never_deletes_the_old_token(client, db_session, test_settings, fake_pve):
    state = fake_pve["state"]
    # Von Hand erweiterte Rolle und gesperrter Benutzer von frueher.
    state["roles"]["NodvardDeck"] = {"Sys.Audit", "VM.Backup"}
    state["users"]["nodvard@pve"] = {"enable": 0}
    token = await _owner_with_proxmox(client, db_session, test_settings)
    first = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert first.status_code == 200, first.text
    assert "VM.Backup" in state["roles"]["NodvardDeck"] and "VM.PowerMgmt" in state["roles"]["NodvardDeck"]
    assert state["users"]["nodvard@pve"]["enable"] == 1

    second = await _connect(client, token, f"127.0.0.1:{fake_pve['port']}/", fingerprint=fake_pve["fingerprint"])
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["connection"]["name"] == "pve1"
    assert body["connection"]["token_id"] == "nodvard@pve!deck-2"
    assert body["replaced_token_id"] == "nodvard@pve!deck"
    assert set(state["tokens"]) == {"nodvard@pve!deck", "nodvard@pve!deck-2"}
    assert state["deleted_tokens"] == []
    connections = (await _settings(db_session))["connections"]
    assert [c["token_id"] for c in connections] == ["nodvard@pve!deck-2"]
    loaded = get_extension_runtime().loaded["proxmox"]
    assert (await loaded.instance.health(loaded.ctx)).healthy is True


@pytest.mark.asyncio
async def test_name_conflicts_are_found_before_the_password_goes_out(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(
        client, db_session, test_settings,
        [{"name": "pve1", "base_url": "https://192.168.2.11:8006", "token_id": "x@pve!y"},
         {"name": "alt", "base_url": fake_pve["base_url"], "token_id": "x@pve!z"}],
    )
    taken = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"], name="pve1")
    assert taken.status_code == 409 and taken.json()["code"] == "name_taken"
    other = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"], name="neu")
    assert other.status_code == 409 and other.json()["code"] == "already_connected"
    assert "„alt“" in other.json()["detail"]
    assert fake_pve["state"]["requests"] == []


# ---------------------------------------------------------------------------
# Fehler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_password_creates_nothing_and_passes_no_proxmox_text_on(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await client.post(
        "/api/v1/ext/proxmox/connect",
        json={"base_url": fake_pve["base_url"], "password": "falsch", "fingerprint": fake_pve["fingerprint"]},
        headers=_h(token),
    )
    assert r.status_code == 400, r.text
    assert r.json() == {"detail": "Proxmox hat die Anmeldung abgelehnt: Benutzer oder Passwort stimmt nicht.", "code": "wrong_password"}
    assert set(fake_pve["state"]["roles"]) == {"Administrator", "PVEAuditor"}
    assert fake_pve["state"]["tokens"] == {}
    assert (await _settings(db_session))["connections"] == []
    (row,) = await _audit_rows(db_session)
    assert (row.outcome, row.reason) == ("failure", "wrong_password")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failing", "code", "status"),
    [
        (("POST", "/access/roles"), "not_allowed", 403),
        (("POST", "/access/users"), "proxmox_error", 500),
    ],
)
async def test_proxmox_errors_become_fixed_sentences(client, db_session, test_settings, fake_pve, failing, code, status):
    fake_pve["state"]["fail"][failing] = status
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.json()["code"] == code, r.text
    assert LEAK not in r.text
    assert (await _settings(db_session))["connections"] == []
    (row,) = await _audit_rows(db_session)
    assert LEAK not in json.dumps([row.reason, row.detail])


@pytest.mark.asyncio
async def test_a_failed_probe_removes_the_new_token_and_saves_nothing(client, db_session, test_settings, fake_pve):
    fake_pve["state"]["fail"][("GET", "/nodes/pve1/status")] = 403
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 502, r.text
    assert r.json()["code"] == "probe_failed"
    assert r.json()["detail"].endswith("Der neue Token wurde in Proxmox wieder entfernt.")
    assert "leftover_token" not in r.json()
    assert fake_pve["state"]["deleted_tokens"] == ["nodvard@pve!deck"]
    assert fake_pve["state"]["tokens"] == {}
    assert (await _settings(db_session))["connections"] == []


@pytest.mark.asyncio
async def test_a_token_that_could_not_be_removed_is_named_so_it_can_be_deleted_by_hand(
    client, db_session, test_settings, fake_pve,
):
    fake_pve["state"]["fail"][("GET", "/nodes/pve1/status")] = 403
    fake_pve["state"]["fail"][("DELETE", "/access/users/nodvard@pve/token/deck")] = 500
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.json()["code"] == "probe_failed", r.text
    assert "wieder entfernt" not in r.json()["detail"]
    assert "„nodvard@pve!deck“" in r.json()["detail"] and "selbst" in r.json()["detail"]
    assert r.json()["leftover_token"] == "nodvard@pve!deck"
    assert "nodvard@pve!deck" in fake_pve["state"]["tokens"]
    (row,) = await _audit_rows(db_session)
    assert row.detail["leftover_token"] == "nodvard@pve!deck"


@pytest.mark.asyncio
async def test_a_connection_lost_right_after_creating_the_token_says_it_may_exist(
    client, db_session, test_settings, fake_pve, monkeypatch,
):
    import httpx

    token = await _owner_with_proxmox(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded["proxmox"]
    real_request = loaded.ctx.http.request

    async def request(method, url, **kwargs):
        response = await real_request(method, url, **kwargs)
        if method == "POST" and url.endswith("/token/deck"):
            raise httpx.ReadTimeout("Antwort kam nicht an")
        return response

    monkeypatch.setattr(loaded.ctx.http, "request", request)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.json()["code"] == "unreachable", r.text
    assert "Vielleicht hat Proxmox den Token „nodvard@pve!deck“ trotzdem angelegt" in r.json()["detail"]
    assert r.json()["leftover_token"] == "nodvard@pve!deck"
    assert (await _settings(db_session))["connections"] == []


@pytest.mark.asyncio
async def test_if_saving_fails_the_new_token_goes_and_the_old_connection_stays(
    client, db_session, test_settings, fake_pve, monkeypatch,
):
    old = {"name": "pve1", "base_url": fake_pve["base_url"], "token_id": "nodvard@pve!alt", "tls_insecure_skip_verify": True}
    token = await _owner_with_proxmox(client, db_session, test_settings, [old])
    loaded = get_extension_runtime().loaded["proxmox"]

    async def broken_create(**_kwargs):
        raise RuntimeError("Tresor kaputt " + PASSWORD)

    monkeypatch.setattr(loaded.ctx.secrets, "create", broken_create)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 500, r.text
    assert r.json()["code"] == "save_failed"
    assert PASSWORD not in r.text
    assert fake_pve["state"]["deleted_tokens"] == ["nodvard@pve!deck"]
    assert (await _settings(db_session))["connections"] == [old]


@pytest.mark.asyncio
async def test_a_working_connection_keeps_its_secret_if_storing_the_new_one_fails(
    client, db_session, test_settings, fake_pve, monkeypatch,
):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    first = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert first.status_code == 200, first.text
    loaded = get_extension_runtime().loaded["proxmox"]

    async def broken(**_kwargs):
        raise RuntimeError("Tresor kaputt")

    monkeypatch.setattr(loaded.ctx.secrets, "create", broken)
    monkeypatch.setattr(loaded.ctx.secrets, "replace", broken, raising=False)  # aeltere Kerne kennen replace nicht
    second = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert second.json()["code"] == "save_failed", second.text
    assert fake_pve["state"]["deleted_tokens"] == ["nodvard@pve!deck-2"]
    # Die Verbindung arbeitet weiter mit dem alten Token -- Einstellungen und Geheimnis wie vorher.
    assert [c["token_id"] for c in (await _settings(db_session))["connections"]] == ["nodvard@pve!deck"]
    assert await loaded.ctx.secrets.exists("proxmox-token:pve1")
    assert (await loaded.instance.health(loaded.ctx)).healthy is True


@pytest.mark.asyncio
async def test_a_name_taken_during_the_login_is_not_overwritten(client, db_session, test_settings, fake_pve, monkeypatch):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded["proxmox"]
    real_get = loaded.ctx.settings.get
    reads = []

    async def get():
        reads.append(1)
        values = await real_get()
        if len(reads) > 1:  # nach der Pruefung vor dem Senden traegt jemand "pve1" fuer einen anderen Server ein
            values["connections"] = [{"name": "pve1", "base_url": "https://192.168.2.99:8006", "token_id": "a@pve!b"}]
        return values

    monkeypatch.setattr(loaded.ctx.settings, "get", get)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"], name="pve1")
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "name_taken"
    assert fake_pve["state"]["deleted_tokens"] == ["nodvard@pve!deck"]


@pytest.mark.asyncio
async def test_only_admins_may_connect(client, db_session, test_settings, fake_pve):
    await _owner_with_proxmox(client, db_session, test_settings)
    operator = await _user_token(client, db_session, username="op1", role="operator")
    for path in ("/api/v1/ext/proxmox/connect", "/api/v1/ext/proxmox/connect/certificate", "/api/v1/ext/proxmox/connect/tfa"):
        r = await client.post(path, json={"base_url": fake_pve["base_url"], "password": PASSWORD, "flow": "x", "code": "1"}, headers=_h(operator))
        assert r.status_code == 403, (path, r.text)
    assert fake_pve["state"]["requests"] == []


@pytest.mark.asyncio
async def test_attempts_are_limited_per_target(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    for _ in range(5):
        r = await client.post(
            "/api/v1/ext/proxmox/connect",
            json={"base_url": fake_pve["base_url"], "password": "falsch", "fingerprint": fake_pve["fingerprint"]},
            headers=_h(token),
        )
        assert r.json()["code"] == "wrong_password"
    sent_before = len(fake_pve["state"]["ticket_forms"])
    for _ in range(2):
        blocked = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
        assert blocked.status_code == 429
        assert blocked.json()["code"] == "rate_limited"
        assert "Minuten" in blocked.json()["detail"]
    assert len(fake_pve["state"]["ticket_forms"]) == sent_before
    rows = await _audit_rows(db_session)
    assert [r.reason for r in rows].count("rate_limited") == 1


@pytest.mark.asyncio
async def test_another_spelling_of_the_same_server_does_not_get_new_attempts(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    for _ in range(5):
        r = await client.post(
            "/api/v1/ext/proxmox/connect",
            json={"base_url": fake_pve["base_url"], "password": "falsch", "fingerprint": fake_pve["fingerprint"]},
            headers=_h(token),
        )
        assert r.json()["code"] == "wrong_password"
    sent_before = len(fake_pve["state"]["ticket_forms"])
    alias = await _connect(client, token, f"https://localhost:{fake_pve['port']}", fingerprint=fake_pve["fingerprint"])
    assert alias.status_code == 429, alias.text
    assert alias.json()["code"] == "rate_limited"
    assert len(fake_pve["state"]["ticket_forms"]) == sent_before


@pytest.mark.asyncio
async def test_only_one_run_at_a_time(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    module = sys.modules["nodvard_deck_ext_proxmox.password_connect"]
    module._busy.add("connect")
    try:
        r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    finally:
        module._busy.discard("connect")
    assert r.status_code == 429 and r.json()["code"] == "busy"
    assert fake_pve["state"]["requests"] == []


# ---------------------------------------------------------------------------
# Zwei-Faktor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_factor_login_in_two_steps_sends_the_password_once(client, db_session, test_settings, fake_pve):
    fake_pve["state"]["tfa"] = {"totp": True, "recovery": ["a1"], "webauthn": {"challenge": "x"}}
    token = await _owner_with_proxmox(client, db_session, test_settings)
    first = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "tfa_required"
    tfa = first.json()["tfa"]
    assert tfa["kinds"] == ["totp", "recovery"]

    wrong = await client.post(
        "/api/v1/ext/proxmox/connect/tfa", json={"flow": tfa["flow"], "code": "000000"}, headers=_h(token),
    )
    assert wrong.status_code == 400 and wrong.json()["code"] == "tfa_wrong"
    # Der Vorgang ist danach weg: noch einmal mit Passwort.
    again = await client.post(
        "/api/v1/ext/proxmox/connect/tfa", json={"flow": tfa["flow"], "code": TFA_CODE}, headers=_h(token),
    )
    assert again.status_code == 409 and again.json()["code"] == "tfa_expired"

    first = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    done = await client.post(
        "/api/v1/ext/proxmox/connect/tfa", json={"flow": first.json()["tfa"]["flow"], "code": " 123 456 ", "kind": "totp"},
        headers=_h(token),
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "connected"
    assert done.json()["connection"]["token_id"] == "nodvard@pve!deck"

    forms = fake_pve["state"]["ticket_forms"]
    assert _sent_password(fake_pve["state"]) == 2  # je Anmeldung genau einmal
    second_steps = [f for f in forms if "tfa-challenge" in f]
    assert len(second_steps) == 2
    assert all(PASSWORD not in json.dumps(f) for f in second_steps)
    reasons = [(r.outcome, r.reason) for r in await _audit_rows(db_session)]
    assert reasons == [("success", "tfa_required"), ("failure", "tfa_wrong"), ("success", "tfa_required"), ("success", None)]


@pytest.mark.asyncio
async def test_a_second_factor_without_a_typeable_code_points_to_the_token_way(client, db_session, test_settings, fake_pve):
    fake_pve["state"]["tfa"] = {"webauthn": {"challenge": "x"}}
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 409
    assert r.json()["code"] == "tfa_unsupported"
    assert "API-Token" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_pending_second_factor_belongs_to_the_user_who_started_it(client, db_session, test_settings, fake_pve):
    fake_pve["state"]["tfa"] = {"totp": True}
    token = await _owner_with_proxmox(client, db_session, test_settings)
    other = await _user_token(client, db_session, username="admin2", role="admin")
    first = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    r = await client.post(
        "/api/v1/ext/proxmox/connect/tfa", json={"flow": first.json()["tfa"]["flow"], "code": TFA_CODE}, headers=_h(other),
    )
    assert r.status_code == 409 and r.json()["code"] == "tfa_expired"
    assert not any("tfa-challenge" in f for f in fake_pve["state"]["ticket_forms"])
    # Der Versuch eines anderen nimmt dem Besitzer den Vorgang nicht weg.
    own = await client.post(
        "/api/v1/ext/proxmox/connect/tfa", json={"flow": first.json()["tfa"]["flow"], "code": TFA_CODE}, headers=_h(token),
    )
    assert own.status_code == 200, own.text
    assert own.json()["status"] == "connected"


# ---------------------------------------------------------------------------
# Das Passwort taucht nirgends auf
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_password_never_shows_up_in_logs_audit_answers_or_settings(client, db_session, test_settings, fake_pve, caplog):
    caplog.set_level(logging.DEBUG)
    fake_pve["state"]["tfa"] = {"totp": True}
    token = await _owner_with_proxmox(client, db_session, test_settings)
    answers = []
    answers.append(await _connect(client, token, fake_pve["base_url"]))  # Zertifikat unbestaetigt
    answers.append(await client.post(
        "/api/v1/ext/proxmox/connect",
        json={"base_url": fake_pve["base_url"], "password": PASSWORD + "x", "fingerprint": fake_pve["fingerprint"]},
        headers=_h(token),
    ))
    first = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    answers.append(first)
    answers.append(await client.post(
        "/api/v1/ext/proxmox/connect/tfa", json={"flow": first.json()["tfa"]["flow"], "code": TFA_CODE}, headers=_h(token),
    ))
    answers.append(await client.post(  # zu langes Passwort: Pruefung von pydantic, Eingabe kommt nicht zurueck
        "/api/v1/ext/proxmox/connect", json={"base_url": fake_pve["base_url"], "password": PASSWORD * 100}, headers=_h(token),
    ))
    assert answers[3].json()["status"] == "connected"
    assert answers[4].status_code == 422

    token_value = next(iter(fake_pve["state"]["tokens"].values()))["value"]
    secrets = (PASSWORD, TICKET, f"totp:{TFA_CODE}", token_value, fake_pve["state"]["challenge"])
    everything = "\n".join(a.text for a in answers)
    everything += caplog.text
    rows = list((await db_session.execute(select(AuditEntry))).scalars())
    everything += json.dumps([[r.action, r.reason, r.detail] for r in rows], default=str, ensure_ascii=False)
    everything += json.dumps(await _settings(db_session))
    for secret in secrets:
        assert secret not in everything, secret


# ---------------------------------------------------------------------------
# Zertifikat wechselt NACH der Anmeldung
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_certificate_change_after_the_login_says_the_password_went_only_to_the_confirmed_server(
    client, db_session, test_settings, fake_pve,
):
    """Der Server tauscht direkt nach der Anmeldung sein Zertifikat: Das Passwort ging an das bestaetigte (die
    Meldung darf nicht "wurde nicht gesendet" sagen), danach geht nichts mehr hinaus, und die Antwort nennt den neuen
    Fingerabdruck."""
    state = fake_pve["state"]
    state["swap_after"] = "/access/ticket"
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "certificate_changed_after_login"
    assert "nicht gesendet" not in body["detail"]
    assert "ging vorher nur an den Server mit dem bestätigten Zertifikat" in body["detail"]
    (new_fingerprint,) = fake_pve["swapped"]
    assert body["certificate"]["sha256"] == new_fingerprint != fake_pve["fingerprint"]
    assert _sent_password(state) == 1
    # Nach dem Wechsel kam keine Anfrage mehr an (die Rollenliste scheiterte im Handshake).
    assert [path for _m, path, _f in state["requests"]] == ["/api2/json/access/ticket"]
    assert state["tokens"] == {}
    assert (await _settings(db_session))["connections"] == []
    (row,) = await _audit_rows(db_session)
    assert (row.outcome, row.reason) == ("failure", "certificate_changed_after_login")


@pytest.mark.asyncio
async def test_a_certificate_change_after_the_token_was_created_names_what_stays_in_proxmox(
    client, db_session, test_settings, fake_pve,
):
    state = fake_pve["state"]
    state["swap_after"] = "/access/users/nodvard@pve/token/deck"
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "certificate_changed_after_login"
    assert body["certificate"]["sha256"] == fake_pve["swapped"][0]
    # Der neue Token liess sich nicht mehr loeschen (wieder am Zertifikat gescheitert): er wird genannt.
    assert body["leftover_tokens"] == ["nodvard@pve!deck"]
    assert "„nodvard@pve!deck“" in body["detail"] and "selbst" in body["detail"]
    assert body["created"] == [
        "Rolle NodvardDeck", "Benutzer nodvard@pve", "Berechtigung auf / für nodvard@pve (Rolle NodvardDeck)",
        "API-Token nodvard@pve!deck",
    ]
    assert "In Proxmox angelegt: Rolle NodvardDeck, Benutzer nodvard@pve" in body["detail"]
    assert (await _settings(db_session))["connections"] == []


@pytest.mark.asyncio
async def test_a_token_whose_handshake_failed_is_not_reported_as_maybe_created(client, db_session, test_settings, fake_pve):
    """Scheitert schon der Handshake (anderes Zertifikat), ging die Anfrage nie hinaus: es gibt sicher keinen Token."""
    state = fake_pve["state"]
    state["swap_after"] = "/cluster/status"
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.json()["code"] == "certificate_changed_after_login", r.text
    assert "leftover_tokens" not in r.json()
    assert "Vielleicht" not in r.json()["detail"]
    assert state["tokens"] == {}


@pytest.mark.asyncio
async def test_a_certificate_change_before_the_second_factor_sends_no_code(client, db_session, test_settings, fake_pve):
    state = fake_pve["state"]
    state["tfa"] = {"totp": True}
    state["swap_after"] = "/access/ticket"
    token = await _owner_with_proxmox(client, db_session, test_settings)
    first = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert first.json()["status"] == "tfa_required", first.text
    r = await client.post(
        "/api/v1/ext/proxmox/connect/tfa", json={"flow": first.json()["tfa"]["flow"], "code": TFA_CODE}, headers=_h(token),
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "certificate_changed_after_login"
    assert r.json()["certificate"]["sha256"] == fake_pve["swapped"][0]
    assert not any("tfa-challenge" in f for f in state["ticket_forms"])
    reasons = [(row.outcome, row.reason) for row in await _audit_rows(db_session)]
    assert reasons == [("success", "tfa_required"), ("failure", "certificate_changed_after_login")]


# ---------------------------------------------------------------------------
# Was in Proxmox liegen blieb, steht in jeder Meldung
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failure_after_creating_things_names_what_stays_in_proxmox(client, db_session, test_settings, fake_pve):
    fake_pve["state"]["fail"][("POST", "/access/users/nodvard@pve/token/deck")] = 500
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.json()["code"] == "proxmox_error", r.text
    created = ["Rolle NodvardDeck", "Benutzer nodvard@pve", "Berechtigung auf / für nodvard@pve (Rolle NodvardDeck)"]
    assert r.json()["created"] == created
    assert (
        "In Proxmox angelegt: Rolle NodvardDeck, Benutzer nodvard@pve, Berechtigung auf / für nodvard@pve "
        "(Rolle NodvardDeck). Das bleibt bestehen, ein neuer Versuch nutzt es weiter."
    ) in r.json()["detail"]
    assert LEAK not in r.text
    (row,) = await _audit_rows(db_session)
    assert row.detail["created"] == created


@pytest.mark.asyncio
async def test_a_removed_token_is_not_named_as_left_in_proxmox(client, db_session, test_settings, fake_pve):
    fake_pve["state"]["fail"][("GET", "/nodes/pve1/status")] = 403
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.json()["code"] == "probe_failed", r.text
    assert r.json()["created"] == [
        "Rolle NodvardDeck", "Benutzer nodvard@pve", "Berechtigung auf / für nodvard@pve (Rolle NodvardDeck)",
    ]
    assert "API-Token" not in r.json()["detail"].split("Das bleibt bestehen")[0]


@pytest.mark.asyncio
async def test_a_second_run_names_only_what_is_new(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    first = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert first.status_code == 200, first.text
    second = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert second.json()["created"] == [
        "API-Token nodvard@pve!deck-2", "Berechtigung auf / für nodvard@pve!deck-2 (Rolle NodvardDeck)",
    ]


# ---------------------------------------------------------------------------
# Keine Weiterleitung beim Anmelden
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_redirect_of_the_login_is_not_followed(client, db_session, test_settings, fake_pve):
    """307 heisst "dieselbe Anfrage samt Passwort an die neue Adresse". Nodvard Deck folgt nicht: beim Ziel der
    Umleitung kommt nichts an, auch keine Verbindung."""
    hits: list[int] = []

    async def elsewhere(_reader, writer):
        hits.append(1)
        writer.close()

    other = await asyncio.start_server(elsewhere, "127.0.0.1", 0)
    other_port = other.sockets[0].getsockname()[1]
    try:
        fake_pve["state"]["ticket_redirect"] = f"https://127.0.0.1:{other_port}/api2/json/access/ticket"
        token = await _owner_with_proxmox(client, db_session, test_settings)
        r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
        await asyncio.sleep(0.05)
    finally:
        other.close()
        await other.wait_closed()
    assert r.status_code == 502, r.text
    assert r.json()["code"] == "redirect"
    assert hits == []
    assert _sent_password(fake_pve["state"]) == 1  # nur an den bestaetigten Server
    assert fake_pve["state"]["tokens"] == {}
    (row,) = await _audit_rows(db_session)
    assert (row.outcome, row.reason) == ("failure", "redirect")


# ---------------------------------------------------------------------------
# Voreinstellung, Einlesen danach
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_a_choice_deck_may_view_and_control(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await client.post(
        "/api/v1/ext/proxmox/connect",
        json={"base_url": fake_pve["base_url"], "password": PASSWORD, "fingerprint": fake_pve["fingerprint"]},
        headers=_h(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["access"] == "full"
    assert ("/", "token", "nodvard@pve!deck", "NodvardDeck") in fake_pve["state"]["acl"]
    assert "VM.PowerMgmt" in r.json()["privileges"]


@pytest.mark.asyncio
async def test_after_connecting_the_servers_are_read_in_right_away(client, db_session, test_settings, fake_pve):
    from nodvard_deck.models import Host

    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    assert r.json()["discovery"] == "started"
    await _background_done(get_extension_runtime().loaded["proxmox"])
    refs = [h.provider_ref for h in (await db_session.execute(select(Host))).scalars()]
    assert "pve1/node/pve1" in refs
    # Gelesen mit dem neuen, gespeicherten Token (nicht mit dem Ticket).
    assert ("nodvard@pve!deck", "GET", "/api2/json/nodes/pve1/qemu") in fake_pve["state"]["token_requests"]


@pytest.mark.asyncio
async def test_a_failing_read_in_does_not_break_the_connection(client, db_session, test_settings, fake_pve, monkeypatch):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    loaded = get_extension_runtime().loaded["proxmox"]
    calls = []

    async def broken_trigger(job_id, **_params):
        calls.append(job_id)
        raise RuntimeError("Abgleich kaputt")

    monkeypatch.setattr(loaded.ctx.scheduler, "trigger", broken_trigger)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "connected"
    await _background_done(loaded)
    assert calls == ["discovery"]
    assert [c["token_id"] for c in (await _settings(db_session))["connections"]] == ["nodvard@pve!deck"]




# ---------------------------------------------------------------------------
# Zugang fuer Proxmox-Backups
# ---------------------------------------------------------------------------

BACKUP_TOKEN = "nodvard-backup@pve!deck"


@pytest.mark.asyncio
async def test_the_certificate_step_says_whether_backups_can_be_connected(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await client.post("/api/v1/ext/proxmox/connect/certificate", json={"base_url": fake_pve["base_url"]}, headers=_h(token))
    assert r.json()["backups_available"] is False
    enabled = await client.post("/api/v1/extensions/backups/enable", headers=_h(token))
    assert enabled.status_code == 200, enabled.text
    r = await client.post("/api/v1/ext/proxmox/connect/certificate", json={"base_url": fake_pve["base_url"]}, headers=_h(token))
    assert r.json()["backups_available"] is True


@pytest.mark.asyncio
async def test_backups_get_their_own_user_role_token_and_rights_only_on_backup_storages(
    client, db_session, test_settings, fake_pve,
):
    state = fake_pve["state"]
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[])
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["backups"] == {
        "name": "pve1", "token_id": BACKUP_TOKEN, "replaced_token_id": None,
        "privileges": ["Datastore.AllocateSpace", "Datastore.Audit", "SDN.Use", "Sys.Audit", "Sys.Modify", "VM.Audit", "VM.Backup"],
        "storages": ["local", "nas"], "pool": "nodvard-kopien", "tls_insecure_skip_verify": False,
        "tls_fingerprint_sha256": fake_pve["fingerprint"], "enabled": True,
    }
    assert state["roles"]["NodvardDeckBackup"] == {
        "Sys.Audit", "VM.Audit", "Datastore.Audit", "VM.Backup", "Sys.Modify", "Datastore.AllocateSpace", "SDN.Use",
    }
    assert state["roles"]["NodvardDeckBackupStore"] == {"Datastore.Audit", "Datastore.AllocateSpace", "Datastore.Allocate"}
    assert state["roles"]["NodvardDeckRestore"] == {"VM.Allocate", "VM.Audit", "VM.Config.Options", "VM.Config.Network"}
    assert not {"VM.Backup", "Datastore.AllocateSpace", "Datastore.Allocate", "VM.Allocate"} & state["roles"]["NodvardDeck"]
    assert state["pools"] == {"nodvard-kopien": {"comment": "Nodvard Deck: wiederhergestellte Kopien"}}
    assert state["users"]["nodvard-backup@pve"]["enable"] == 1
    assert state["tokens"][BACKUP_TOKEN]["privsep"] == 1
    # Eigener Benutzer: nodvard@pve und sein Token bekommen keine Backup-Rechte.
    assert state["acl"] == {
        ("/", "user", "nodvard@pve", "NodvardDeck"),
        ("/", "token", "nodvard@pve!deck", "NodvardDeck"),
        ("/", "user", "nodvard-backup@pve", "NodvardDeckBackup"),
        ("/", "token", BACKUP_TOKEN, "NodvardDeckBackup"),
        ("/storage/local", "user", "nodvard-backup@pve", "NodvardDeckBackupStore"),
        ("/storage/local", "token", BACKUP_TOKEN, "NodvardDeckBackupStore"),
        ("/storage/nas", "user", "nodvard-backup@pve", "NodvardDeckBackupStore"),
        ("/storage/nas", "token", BACKUP_TOKEN, "NodvardDeckBackupStore"),
        ("/pool/nodvard-kopien", "user", "nodvard-backup@pve", "NodvardDeckRestore"),
        ("/pool/nodvard-kopien", "token", BACKUP_TOKEN, "NodvardDeckRestore"),
    }
    assert "Benutzer nodvard-backup@pve" in body["created"]

    # Auch hier der bestaetigte Fingerabdruck statt "nicht pruefen" -- und die Verbindung arbeitet damit (Health).
    assert (await _backups_settings(db_session))["connections"] == [{
        "name": "pve1", "base_url": fake_pve["base_url"], "token_id": BACKUP_TOKEN,
        "tls_insecure_skip_verify": False, "enabled": True, "tls_fingerprint_sha256": fake_pve["fingerprint"],
    }]
    assert await _stored_secret("backups", "backups-token:pve1") == state["tokens"][BACKUP_TOKEN]["value"]
    assert await _stored_secret("proxmox", "proxmox-token:pve1") == state["tokens"]["nodvard@pve!deck"]["value"]
    loaded = get_extension_runtime().loaded["backups"]
    assert (await loaded.instance.health(loaded.ctx)).healthy is True
    (row,) = await _audit_rows(db_session)
    assert row.detail["backup_token_id"] == BACKUP_TOKEN
    assert row.detail["restore_pool"] == "nodvard-kopien"
    assert state["tokens"][BACKUP_TOKEN]["value"] not in json.dumps(row.detail)
    # Auch Proxmox-Backups schreibt die Uebernahme ins Protokoll (ohne Geheimnis).
    received = list((await db_session.execute(
        select(AuditEntry).where(AuditEntry.action == "backups.connection_received")
    )).scalars())
    assert [(r.detail["connection"], r.detail["token_id"], r.detail["address"]) for r in received] == [
        ("pve1", BACKUP_TOKEN, fake_pve["base_url"]),
    ]
    assert state["tokens"][BACKUP_TOKEN]["value"] not in json.dumps([r.detail for r in received])


@pytest.mark.asyncio
async def test_a_token_of_nodvard_without_privilege_separation_gets_no_backup_rights(client, db_session, test_settings, fake_pve):
    """Ein Token von nodvard@pve ohne Privilegientrennung hat alle Rechte seines Benutzers -- die Backup-Rechte haengen
    deshalb an einem eigenen Benutzer, nicht an nodvard@pve."""
    _old_token(fake_pve["state"], "voll", privsep=0)
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[])
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    assert r.json()["backups"]["token_id"] == BACKUP_TOKEN
    assert {role for path, kind, who, role in fake_pve["state"]["acl"] if who == "nodvard@pve"} == {"NodvardDeck"}


@pytest.mark.asyncio
async def test_a_backup_user_with_an_unseparated_token_gets_no_backup_access(client, db_session, test_settings, fake_pve):
    state = fake_pve["state"]
    state["users"]["nodvard-backup@pve"] = {"enable": 1}
    _old_token(state, "voll", privsep=0, user="nodvard-backup@pve")
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[])
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    assert r.json()["backups"] is None
    note = next(n for n in r.json()["notes"] if "nodvard-backup@pve" in n)
    assert "„nodvard-backup@pve!voll“" in note and "keinen Zugang" in note
    assert "NodvardDeckBackup" not in state["roles"]
    assert not any(who.startswith("nodvard-backup@pve") for _p, _k, who, _r in state["acl"])


@pytest.mark.asyncio
async def test_view_only_backups_get_only_read_rights(client, db_session, test_settings, fake_pve):
    state = fake_pve["state"]
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[])
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"], access="read")
    assert r.status_code == 200, r.text
    assert r.json()["backups"]["storages"] == [] and r.json()["backups"]["pool"] is None
    assert state["roles"]["NodvardDeckBackupRO"] == {"Sys.Audit", "VM.Audit", "Datastore.Audit"}
    assert "NodvardDeckBackupStore" not in state["roles"] and "NodvardDeckRestore" not in state["roles"]
    assert state["pools"] == {}
    assert {acl for acl in state["acl"] if acl[2].startswith("nodvard-backup@pve")} == {
        ("/", "user", "nodvard-backup@pve", "NodvardDeckBackupRO"),
        ("/", "token", BACKUP_TOKEN, "NodvardDeckBackupRO"),
    }


@pytest.mark.asyncio
async def test_without_the_backups_module_or_when_switched_off_no_backup_access_is_made(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    quiet = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert quiet.status_code == 200, quiet.text
    assert quiet.json()["backups"] is None
    assert not any("Proxmox-Backups" in note for note in quiet.json()["notes"])
    asked = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"], backups=True)
    assert any("Proxmox-Backups ist nicht eingeschaltet" in note for note in asked.json()["notes"])
    assert "nodvard-backup@pve" not in fake_pve["state"]["users"]

    await client.post("/api/v1/extensions/backups/enable", headers=_h(token))
    off = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"], backups=False)
    assert off.status_code == 200, off.text
    assert off.json()["backups"] is None
    assert "nodvard-backup@pve" not in fake_pve["state"]["users"]
    assert "NodvardDeckBackup" not in fake_pve["state"]["roles"]


@pytest.mark.asyncio
async def test_a_backups_name_of_another_server_creates_no_backup_access(client, db_session, test_settings, fake_pve):
    other = {"name": "pve1", "base_url": "https://192.168.2.99:8006", "token_id": "a@pve!b"}
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[other])
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    assert r.json()["backups"] is None
    assert any("schon zu einem anderen Server" in note for note in r.json()["notes"])
    assert "NodvardDeckBackup" not in fake_pve["state"]["roles"]
    assert (await _backups_settings(db_session))["connections"] == [other]


@pytest.mark.asyncio
async def test_if_backups_cannot_store_its_access_only_the_backup_token_goes(
    client, db_session, test_settings, fake_pve, monkeypatch,
):
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[])
    backups_ctx = get_extension_runtime().loaded["backups"].ctx

    async def broken(**_kwargs):
        raise RuntimeError("Tresor kaputt " + PASSWORD)

    monkeypatch.setattr(backups_ctx.secrets, "create", broken)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    assert r.json()["backups"] is None
    assert any("Proxmox-Backups konnte Nodvard Deck nicht speichern" in note for note in r.json()["notes"])
    assert PASSWORD not in r.text and "Tresor kaputt" not in r.text
    assert fake_pve["state"]["deleted_tokens"] == [BACKUP_TOKEN]
    assert (await _backups_settings(db_session))["connections"] == []
    assert [c["token_id"] for c in (await _settings(db_session))["connections"]] == ["nodvard@pve!deck"]


@pytest.mark.asyncio
async def test_a_failed_backup_probe_keeps_the_proxmox_connection(client, db_session, test_settings, fake_pve):
    fake_pve["state"]["fail"][("GET", "/cluster/backup")] = 403
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[])
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    assert r.json()["backups"] is None
    assert "Der neue Zugang für Proxmox-Backups ließ sich nicht benutzen." in r.json()["notes"]
    assert fake_pve["state"]["deleted_tokens"] == [BACKUP_TOKEN]
    assert f"API-Token {BACKUP_TOKEN}" not in r.json()["created"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pool", "hint"),
    [
        ({"comment": "Testmaschinen"}, "nicht von Nodvard Deck"),
        ({"comment": "Nodvard Deck: wiederhergestellte Kopien (alt)"}, "nicht von Nodvard Deck"),
        ({"comment": "Nodvard Deck: wiederhergestellte Kopien", "members": [{"type": "storage", "storage": "nas"}]},
         "liegt schon etwas"),
        ({"comment": "Nodvard Deck: wiederhergestellte Kopien", "members": [{"type": "qemu", "vmid": 100, "id": "qemu/100"}]},
         "liegt schon etwas"),
    ],
    ids=["fremder-kommentar", "kommentar-nicht-genau", "speicher-darin", "gast-ohne-vorherige-rechte"],
)
async def test_a_pool_not_made_or_used_only_by_deck_gets_no_rights(client, db_session, test_settings, fake_pve, pool, hint):
    fake_pve["state"]["pools"]["nodvard-kopien"] = pool
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[])
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    assert r.json()["backups"]["pool"] is None
    assert any(hint in note for note in r.json()["notes"]), r.json()["notes"]
    assert not any(path.startswith("/pool/") for path, *_ in fake_pve["state"]["acl"])
    assert "NodvardDeckRestore" not in fake_pve["state"]["roles"]


@pytest.mark.asyncio
async def test_the_own_pool_with_its_copies_is_taken_over_again(client, db_session, test_settings, fake_pve):
    state = fake_pve["state"]
    state["pools"]["nodvard-kopien"] = {
        "comment": "Nodvard Deck: wiederhergestellte Kopien", "members": [{"type": "qemu", "vmid": 9001, "id": "qemu/9001"}],
    }
    state["acl"].add(("/pool/nodvard-kopien", "user", "nodvard-backup@pve", "NodvardDeckRestore"))
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[])
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    assert r.json()["backups"]["pool"] == "nodvard-kopien"
    assert ("/pool/nodvard-kopien", "token", BACKUP_TOKEN, "NodvardDeckRestore") in state["acl"]


@pytest.mark.asyncio
async def test_connecting_again_gives_the_backups_connection_a_new_token(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[])
    first = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert first.status_code == 200, first.text
    second = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert second.status_code == 200, second.text
    assert second.json()["backups"]["token_id"] == "nodvard-backup@pve!deck-2"
    assert second.json()["backups"]["replaced_token_id"] == BACKUP_TOKEN
    assert [c["token_id"] for c in (await _backups_settings(db_session))["connections"]] == ["nodvard-backup@pve!deck-2"]
    state = fake_pve["state"]
    assert await _stored_secret("backups", "backups-token:pve1") == state["tokens"]["nodvard-backup@pve!deck-2"]["value"]
    assert [t["token_id"] for t in second.json()["old_tokens"]] == ["nodvard@pve!deck", BACKUP_TOKEN]
    assert second.json()["old_tokens"][1]["command"] == "pveum user token remove nodvard-backup@pve deck"


@pytest.mark.asyncio
async def test_a_strict_or_switched_off_backups_connection_stays_so(client, db_session, test_settings, fake_pve):
    """Eine Backup-Verbindung, die das Zertifikat streng prueft, wird nie still auf "Selbstsigniertes Zertifikat
    erlauben" umgestellt -- sie bekommt den bestaetigten Fingerabdruck (auch streng); eine ausgeschaltete bleibt aus."""
    existing = {
        "name": "pve1", "base_url": fake_pve["base_url"], "token_id": "x@pve!y", "tls_insecure_skip_verify": False,
        "enabled": False,
    }
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[existing])
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    backups = r.json()["backups"]
    assert (backups["tls_insecure_skip_verify"], backups["enabled"]) == (False, False)
    assert (await _backups_settings(db_session))["connections"] == [
        {**existing, "token_id": BACKUP_TOKEN, "tls_fingerprint_sha256": fake_pve["fingerprint"]}
    ]
    notes = " ".join(r.json()["notes"])
    assert "das Zertifikat weiter so, wie dort eingestellt" not in notes
    assert "ausgeschaltet und bleibt es" in notes


# ---------------------------------------------------------------------------
# Ein Geheimnis, das nicht von der Erweiterung stammt
# ---------------------------------------------------------------------------


async def _foreign_secret(client, token: str, label: str) -> None:
    """Wie von Hand ueber die Tresor-Schnittstelle angelegt: ohne Besitzer-Erweiterung."""
    r = await client.post(
        "/api/v1/secrets", json={"label": label, "kind": "api_token", "value": "von-hand"}, headers=_h(token),
    )
    assert r.status_code == 201, r.text


async def _secret_labels(client, token: str) -> list[str]:
    return [s["label"] for s in (await client.get("/api/v1/secrets", headers=_h(token))).json()]


@pytest.mark.asyncio
async def test_a_foreign_secret_under_the_given_name_stops_before_the_password(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    await _foreign_secret(client, token, "proxmox-token:pve9")
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"], name="pve9")
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "secret_foreign"
    assert "„proxmox-token:pve9“" in r.json()["detail"] and "DELETE /api/v1/secrets/{id}" in r.json()["detail"]
    assert fake_pve["state"]["requests"] == []
    assert "proxmox-token:pve9" in await _secret_labels(client, token)


@pytest.mark.asyncio
async def test_a_foreign_secret_under_the_derived_name_stops_before_a_token_is_made(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    await _foreign_secret(client, token, "proxmox-token:pve1")
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "secret_foreign"
    assert fake_pve["state"]["tokens"] == {}
    assert (await _settings(db_session))["connections"] == []
    assert "In Proxmox angelegt: Rolle NodvardDeck" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_foreign_secret_of_an_existing_connection_leaves_everything_as_it_was(client, db_session, test_settings, fake_pve):
    old = {"name": "pve9", "base_url": fake_pve["base_url"], "token_id": "x@pve!alt", "tls_insecure_skip_verify": True}
    token = await _owner_with_proxmox(client, db_session, test_settings, [old])
    await _foreign_secret(client, token, "proxmox-token:pve9")
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "secret_foreign"
    assert (await _settings(db_session))["connections"] == [old]
    assert fake_pve["state"]["deleted_tokens"] == ["nodvard@pve!deck"]
    assert "proxmox-token:pve9" in await _secret_labels(client, token)


@pytest.mark.asyncio
async def test_a_foreign_backups_secret_skips_only_the_backup_access(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[])
    await _foreign_secret(client, token, "backups-token:pve1")
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    assert r.json()["backups"] is None
    assert any("„backups-token:pve1“" in note for note in r.json()["notes"])
    assert fake_pve["state"]["deleted_tokens"] == [BACKUP_TOKEN]
    assert (await _backups_settings(db_session))["connections"] == []
    assert [c["token_id"] for c in (await _settings(db_session))["connections"]] == ["nodvard@pve!deck"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext_id", "prefix"), [("proxmox", "proxmox-token"), ("backups", "backups-token")])
async def test_connection_routes_leave_a_foreign_secret_alone_and_say_so(client, db_session, test_settings, ext_id, prefix):
    conn = {"name": "pve9", "base_url": "https://192.168.2.19:8006", "token_id": "x@pve!y"}
    token = await _owner_with_proxmox(client, db_session, test_settings, [conn], backups=[conn])
    await _foreign_secret(client, token, f"{prefix}:pve9")
    await _foreign_secret(client, token, f"{prefix}:pve8")

    async def stored() -> list[dict]:
        record = await db_session.get(ExtensionRecord, ext_id)
        await db_session.refresh(record)
        return list((record.settings or {}).get("connections") or [])

    # Neue Adresse: abgelehnt, bevor sich etwas aendert.
    moved = await client.put(
        f"/api/v1/ext/{ext_id}/connections/pve9", json={"base_url": "https://192.168.2.20:8006"}, headers=_h(token),
    )
    assert moved.status_code == 409, moved.text
    assert f"„{prefix}:pve9“" in moved.json()["detail"]
    assert await stored() == [conn]
    # Neue Verbindung unter dem Namen: ebenso.
    added = await client.post(
        f"/api/v1/ext/{ext_id}/connections",
        json={"name": "pve8", "base_url": "https://192.168.2.18:8006", "token_id": "x@pve!z"}, headers=_h(token),
    )
    assert added.status_code == 409, added.text
    assert "DELETE /api/v1/secrets/{id}" in added.json()["detail"]
    assert await stored() == [conn]
    # Entfernen: die Verbindung ist weg, das fremde Geheimnis bleibt.
    removed = await client.delete(f"/api/v1/ext/{ext_id}/connections/pve9", headers=_h(token))
    assert removed.status_code == 204, removed.text
    assert await stored() == []
    assert {f"{prefix}:pve9", f"{prefix}:pve8"} <= set(await _secret_labels(client, token))


# ---------------------------------------------------------------------------
# Alte Tokens und Tokens ohne Privilegientrennung
# ---------------------------------------------------------------------------


def _old_token(state: dict, name: str, *, privsep: int = 1, user: str = "nodvard@pve") -> None:
    state["users"].setdefault(user, {"enable": 1})
    state["tokens"][f"{user}!{name}"] = {"value": f"alt-{user}-{name}", "privsep": privsep, "comment": "alt"}


@pytest.mark.asyncio
async def test_old_deck_tokens_are_listed_with_the_command_to_remove_them(client, db_session, test_settings, fake_pve):
    state = fake_pve["state"]
    for name in ("deck", "deck-2", "skript"):
        _old_token(state, name)
    _old_token(state, "deck", user="nodvard-backup@pve")
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    assert r.json()["connection"]["token_id"] == "nodvard@pve!deck-3"
    # Fremde Tokens nie; die von Proxmox-Backups nicht, solange das Modul aus ist (sie koennten dort stecken).
    assert r.json()["old_tokens"] == [
        {"token_id": "nodvard@pve!deck", "user": "nodvard@pve", "name": "deck", "privsep": True,
         "command": "pveum user token remove nodvard@pve deck"},
        {"token_id": "nodvard@pve!deck-2", "user": "nodvard@pve", "name": "deck-2", "privsep": True,
         "command": "pveum user token remove nodvard@pve deck-2"},
    ]
    assert state["deleted_tokens"] == []

    # Spaeter mit dem gespeicherten Token: dieselbe Liste, ohne Passwort und ohne etwas zu loeschen.
    before = len(state["requests"])
    listed = await client.get("/api/v1/ext/proxmox/connections/pve1/old-tokens", headers=_h(token))
    assert listed.status_code == 200, listed.text
    assert [t["token_id"] for t in listed.json()["tokens"]] == ["nodvard@pve!deck", "nodvard@pve!deck-2"]
    assert listed.json()["backups_checked"] is False
    new_requests = state["requests"][before:]
    assert [(m, p) for m, p, _f in new_requests] == [("GET", "/api2/json/access/users/nodvard@pve")]
    assert ("nodvard@pve!deck-3", "GET", "/api2/json/access/users/nodvard@pve") in state["token_requests"]
    assert state["deleted_tokens"] == []


@pytest.mark.asyncio
async def test_old_tokens_skip_the_ones_still_used_by_backups(client, db_session, test_settings, fake_pve):
    state = fake_pve["state"]
    _old_token(state, "deck")
    for name in ("deck", "deck-2"):
        _old_token(state, name, user="nodvard-backup@pve")
    in_use = {"name": "pve1", "base_url": fake_pve["base_url"], "token_id": "nodvard-backup@pve!deck-2"}
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[in_use])
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"], backups=False)
    assert r.status_code == 200, r.text
    listed = await client.get("/api/v1/ext/proxmox/connections/pve1/old-tokens", headers=_h(token))
    assert listed.json()["backups_checked"] is True
    assert [t["token_id"] for t in listed.json()["tokens"]] == ["nodvard@pve!deck", "nodvard-backup@pve!deck"]


@pytest.mark.asyncio
async def test_old_tokens_need_the_admin_right_and_a_known_connection(client, db_session, test_settings, fake_pve):
    token = await _owner_with_proxmox(client, db_session, test_settings)
    missing = await client.get("/api/v1/ext/proxmox/connections/gibtsnicht/old-tokens", headers=_h(token))
    assert missing.status_code == 404
    operator = await _user_token(client, db_session, username="op2", role="operator")
    denied_r = await client.get("/api/v1/ext/proxmox/connections/pve1/old-tokens", headers=_h(operator))
    assert denied_r.status_code == 403


@pytest.mark.asyncio
async def test_a_token_without_privilege_separation_is_named(client, db_session, test_settings, fake_pve):
    _old_token(fake_pve["state"], "voll", privsep=0)
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    note = next(n for n in r.json()["notes"] if "ohne Privilegientrennung" in n)
    assert "„nodvard@pve!voll“" in note
    assert r.json()["old_tokens"] == []  # fremd benannt: kein Vorschlag zum Loeschen
    (row,) = await _audit_rows(db_session)
    assert row.detail["privsep_off"] == ["nodvard@pve!voll"]


# ---------------------------------------------------------------------------
# Was aus Antworten von Proxmox weitergeht, und was in repr steht
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_privilege_like_names_from_proxmox_go_on_and_only_so_many(client, db_session, test_settings, fake_pve):
    weird = "<img src=x onerror=alert(1)>"
    many = {f"Extra.Recht{i:03d}" for i in range(400)}
    fake_pve["state"]["roles"]["NodvardDeck"] = {"Sys.Audit", weird, "Mit Leerzeichen", *many}
    token = await _owner_with_proxmox(client, db_session, test_settings)
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    assert weird not in r.text and "Mit Leerzeichen" not in r.text
    # Aus der Antwort von Proxmox hoechstens 200, dazu die eigenen (bekannten) Rechte.
    assert len(r.json()["privileges"]) <= 200 + 13
    (row,) = await _audit_rows(db_session)
    assert weird not in json.dumps(row.detail)
    assert len(row.detail["extra_privileges"]) <= 200


def test_repr_never_shows_the_ticket_the_challenge_or_a_token_value():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "proxmox" / "src"))
    import importlib

    module = importlib.import_module("nodvard_deck_ext_proxmox.password_connect")
    api = module._Proxmox(None, "https://192.168.2.10:8006", "AB" * 32, auth={"Cookie": f"PVEAuthCookie={TICKET}"})
    pending = module._Pending(
        user_id="u1", api=api, target=module._Target("https://192.168.2.10:8006", "192.168.2.10:8006"), trusted=False,
        username="root@pam", plan=module._Plan(name=None), access="full", backups=None,
        challenge="PVE:!tfa!GEHEIME-AUFFORDERUNG", kinds=("totp",), expires=0.0,
    )
    backup = module._BackupAccess(
        receiver=None, token=module._NewToken("nodvard-backup@pve", "deck", True), token_value="TOKEN-WERT-GEHEIM",
        role=module._Role("NodvardDeckBackup", [], [], []), storages=[], pool=None,
    )
    shown = repr(api) + repr(pending) + repr(backup)
    for secret in (TICKET, "GEHEIME-AUFFORDERUNG", "TOKEN-WERT-GEHEIM"):
        assert secret not in shown
