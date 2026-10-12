"""backups-Extension: Gast aus einer Sicherung als Kopie wiederherstellen.

Gegen einen echten, lokal laufenden Proxmox-Nachbau (echtes HTTP/JSON, wie test_ext_backups.py). Er verhaelt
sich wie Proxmox, wo es fuer die Regeln zaehlt: `nextid?vmid=` antwortet 400 fuer vergebene Nummern (auch fuer
Gaeste, die der Token nicht sieht), das Anlegen lehnt eine vergebene Nummer ab, `unique=1` vergibt neue MACs,
`onboot`/`name` aus dem Aufruf gehen vor die Werte aus der Sicherung, `PUT .../config` prueft den `digest`,
`/access/permissions` liefert die eigenen Rechte des Tokens je Pfad.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from nodvard_deck.models import Action, AuditEntry, ExtensionRecord
from nodvard_deck.services import extensions as extensions_service
from sqlalchemy import select

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
_TOKEN_ID = "nodvard@pve!dashboard"
_TOKEN_SECRET = "test-secret-uuid"
_EXPECTED_AUTH = f"PVEAPIToken={_TOKEN_ID}={_TOKEN_SECRET}"
GIB = 1024**3
ALL_RIGHTS = (
    "VM.Allocate", "VM.Audit", "VM.Config.Options", "VM.Config.Network", "Datastore.AllocateSpace", "Sys.Modify",
    "Mapping.Use", "SDN.Use",
)
POOL = "nodvard-kopien"
POOL_PATH = f"/pool/{POOL}"

QEMU_VOLID = "backup-nas:backup/vzdump-qemu-101-2026_10_01-02_00_00.vma.zst"
LXC_VOLID = "backup-nas:backup/vzdump-lxc-104-2026_10_01-03_00_00.tar.zst"
LOCAL_VOLID = "local:backup/vzdump-qemu-120-2026_09_20-02_00_00.vma.zst"

QEMU_CONFIG = """#Webserver im Keller
boot: order=scsi0
cores: 2
memory: 2048
name: web
net0: virtio=BC:24:11:00:00:01,bridge=vmbr0,firewall=1
net1: virtio=BC:24:11:00:00:02,bridge=vmbr1
onboot: 1
scsi0: local-lvm:vm-101-disk-0,size=32G
scsi1: local-lvm:vm-101-disk-1,backup=0,size=100G
ide2: none,media=cdrom
#qmdump#map:scsi0:drive-scsi0:local-lvm:raw:
"""

LXC_CONFIG = """arch: amd64
hostname: dns
memory: 512
net0: name=eth0,bridge=vmbr0,hwaddr=BC:24:11:00:00:04,ip=192.168.2.53/24,gw=192.168.2.1,type=veth
onboot: 1
ostype: debian
rootfs: local-lvm:vm-104-disk-0,size=8G
unprivileged: 1
"""


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _form(raw: bytes) -> dict[str, str]:
    return {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode(), keep_blank_values=True).items()}


def _config_dict(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for line in text.splitlines():
        if line.startswith("#qmdump#"):
            continue
        if line.startswith("#"):
            out["description"] = (out.get("description", "") + "\n" + line[1:]).strip()
            continue
        key, _, value = line.partition(":")
        if key:
            out[key.strip()] = value.strip()
    return out


def _build_proxmox() -> tuple[FastAPI, dict]:
    state: dict[str, Any] = {
        "nodes": [{"node": "pve1", "status": "online"}, {"node": "pve2", "status": "online"}],
        "backup_storages": {
            node: [{"storage": "backup-nas", "shared": 1, "active": 1}, {"storage": "local", "shared": 0, "active": 1}]
            for node in ("pve1", "pve2")
        },
        "backup_files": {
            ("*", "backup-nas"): [
                {"volid": QEMU_VOLID, "vmid": 101, "subtype": "qemu", "ctime": 1790740800, "size": 4 * GIB, "format": "vma.zst"},
                {"volid": LXC_VOLID, "vmid": 104, "subtype": "lxc", "ctime": 1790744400, "size": 1 * GIB, "format": "tar.zst"},
                # Sicherung eines laengst geloeschten Gastes 102: die Nummer ist frei, aber nicht "unbelegt".
                {"volid": "backup-nas:backup/vzdump-qemu-102-2026_01_01-02_00_00.vma.zst", "vmid": 102, "subtype": "qemu",
                 "ctime": 1767232800, "size": 2 * GIB},
            ],
            ("pve2", "local"): [
                {"volid": LOCAL_VOLID, "vmid": 120, "subtype": "qemu", "ctime": 1789900000, "size": 2 * GIB},
            ],
        },
        "guest_storages": {
            node: [
                {"storage": "local-lvm", "type": "lvmthin", "content": "images,rootdir", "avail": 100 * GIB, "total": 200 * GIB, "active": 1, "enabled": 1},
                {"storage": "local", "type": "dir", "content": "iso,vztmpl,backup", "avail": 50 * GIB, "total": 60 * GIB, "active": 1, "enabled": 1},
                {"storage": "small", "type": "lvmthin", "content": "images,rootdir", "avail": 3 * GIB, "total": 10 * GIB, "active": 1, "enabled": 1},
                {"storage": "vmonly", "type": "lvm", "content": "images", "avail": 20 * GIB, "total": 40 * GIB, "active": 1, "enabled": 1},
            ]
            for node in ("pve1", "pve2")
        },
        "resources": [
            {"vmid": 101, "node": "pve1", "type": "qemu", "name": "web"},
            {"vmid": 104, "node": "pve1", "type": "lxc", "name": "dns"},
            {"vmid": 103, "node": "pve2", "type": "qemu", "name": "db"},
        ],
        # Gaeste, die der Token nicht sehen darf (fehlen in /cluster/resources), deren Nummer aber vergeben ist.
        "hidden_vmids": [],
        "backup_configs": {QEMU_VOLID: QEMU_CONFIG, LXC_VOLID: LXC_CONFIG},
        "task_script": [{"status": "stopped", "exitstatus": "OK"}],
        "task_calls": 0,
        "ignore_overrides": False,
        "guest_configs": {},
        "created": [],
        "puts": [],
        "calls": [],
        "deny": [],
        "version": "8.2.4",
        # Rechte, die dem Token auf einem Pfad fehlen (Pfad -> Rechte); sonst hat er alle aus ALL_RIGHTS.
        "missing_rights": {},
        # Weitere Rechte je Pfad (etwa Permissions.Modify), die ALL_RIGHTS nicht kennt.
        "extra_rights": {},
        # Pools, die es gibt; die Kopie darf der Token nur in einem Pool anlegen (VM.Allocate nur dort, wie eingerichtet).
        "pools": {POOL: []},
        "allocate_only_in_pool": True,
        "version_status": None,
        "ha": [],
        # Wiederherstellungs-Aufgaben je Knoten, wie `GET /nodes/{n}/tasks` sie liefert.
        "task_index": [],
        # Anlegen klappt, aber die Antwort kommt als Fehler an (Proxy dazwischen): HTTP-Status.
        "answer_after_create": None,
        # Pfade, deren Rechte der Token nicht abfragen kann (Proxmox antwortet 400): `GET /access/permissions?path=...`.
        "unreadable_permission_paths": [],
    }
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _proxmox_error(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": exc.detail}
        return JSONResponse({"data": None, **detail}, status_code=exc.status_code)

    @app.middleware("http")
    async def _record_and_guard(request: Request, call_next):
        if request.headers.get("authorization") != _EXPECTED_AUTH:
            return JSONResponse({"data": None, "message": "authentication failure"}, status_code=401)
        path = request.url.path.removeprefix("/api2/json")
        state["calls"].append((request.method, path, dict(request.query_params)))
        for method, prefix in state["deny"]:
            if request.method == method and path.startswith(prefix):
                return JSONResponse(
                    {"data": None, "message": "Permission check failed (/vms/900, VM.Allocate)"}, status_code=403
                )
        return await call_next(request)

    def _taken() -> set[int]:
        return {int(r["vmid"]) for r in state["resources"]} | set(state["hidden_vmids"])

    @app.get("/api2/json/nodes")
    async def nodes() -> dict:
        return {"data": state["nodes"]}

    @app.get("/api2/json/cluster/backup")
    async def backup_jobs() -> dict:
        return {"data": []}  # fuer den Backup-Waechter, der offene Kopien mit nachbereitet

    @app.get("/api2/json/version")
    async def version() -> dict:
        if state["version_status"]:
            raise HTTPException(status_code=state["version_status"], detail="nicht erlaubt")
        return {"data": {"version": state["version"], "release": state["version"].rsplit(".", 1)[0]}}

    @app.get("/api2/json/access/permissions")
    async def permissions(request: Request) -> dict:
        path = request.query_params["path"]
        if path in state["unreadable_permission_paths"]:
            raise HTTPException(status_code=400, detail={"message": "Parameter verification failed.", "errors": {"path": "invalid ACL path"}})
        missing = set(state["missing_rights"].get(path, ()))
        rights = {r: 1 for r in (*ALL_RIGHTS, *state["extra_rights"].get(path, ())) if r not in missing}
        return {"data": {path: rights}}

    @app.get("/api2/json/cluster/ha/resources")
    async def ha_resources() -> dict:
        return {"data": state["ha"]}

    @app.get("/api2/json/nodes/{node}/tasks")
    async def node_tasks(node: str, request: Request) -> dict:
        params = request.query_params
        rows = [
            t for t in state["task_index"]
            if t["node"] == node and t["type"] == params.get("typefilter") and str(t["id"]) == params.get("vmid")
        ]
        return {"data": rows}

    @app.get("/api2/json/nodes/{node}/storage")
    async def node_storages(node: str, request: Request) -> dict:
        content = request.query_params.get("content")
        if content == "backup":
            return {"data": state["backup_storages"][node]}
        rows = [s for s in state["guest_storages"][node] if content in s["content"].split(",")]
        return {"data": rows}

    @app.get("/api2/json/nodes/{node}/storage/{storage}/content")
    async def storage_content(node: str, storage: str, request: Request) -> dict:
        assert request.query_params.get("content") == "backup"
        entry = next((s for s in state["backup_storages"][node] if s["storage"] == storage), None)
        if entry is None:
            raise HTTPException(status_code=500, detail=f"storage '{storage}' does not exist")
        rows = state["backup_files"].get(("*" if entry["shared"] else node, storage), [])
        vmid = request.query_params.get("vmid")
        if vmid is not None:
            rows = [r for r in rows if str(r["vmid"]) == vmid]
        return {"data": rows}

    @app.get("/api2/json/nodes/{node}/storage/{storage}/status")
    async def storage_status(node: str, storage: str) -> dict:
        row = next((s for s in state["guest_storages"][node] if s["storage"] == storage), None)
        if row is None:
            raise HTTPException(status_code=400, detail={"message": "Parameter verification failed.", "errors": {"storage": "No such storage."}})
        return {"data": {**row, "shared": 0}}

    @app.get("/api2/json/cluster/resources")
    async def resources(request: Request) -> dict:
        assert request.query_params.get("type") == "vm"
        return {"data": state["resources"]}

    @app.get("/api2/json/cluster/nextid")
    async def nextid(request: Request) -> dict:
        vmid = request.query_params.get("vmid")
        if vmid is not None:
            if int(vmid) in _taken():
                raise HTTPException(status_code=400, detail={"message": "Parameter verification failed.", "errors": {"vmid": f"VM {vmid} already exists"}})
            return {"data": vmid}
        candidate = 100
        while candidate in _taken():
            candidate += 1
        return {"data": candidate}

    @app.get("/api2/json/nodes/{node}/vzdump/extractconfig")
    async def extractconfig(node: str, request: Request) -> dict:
        text = state["backup_configs"].get(request.query_params.get("volume"))
        if text is None:
            raise HTTPException(status_code=500, detail="unable to extract configuration")
        return {"data": text}

    async def _create(node: str, kind: str, request: Request) -> dict:
        params = _form(await request.body())
        state["created"].append((node, kind, params))
        vmid = int(params["vmid"])
        pool = params.get("pool")
        if pool is not None and pool not in state["pools"]:
            raise HTTPException(status_code=403, detail=f"pool '{pool}' does not exist")
        if state["allocate_only_in_pool"] and pool != POOL:
            raise HTTPException(status_code=403, detail=f"Permission check failed (/vms/{vmid}, VM.Allocate)")
        if vmid in _taken():
            raise HTTPException(status_code=500, detail=f"unable to restore {kind} {vmid} - VM {vmid} already exists on node '{node}'")
        volid = params.get("archive") or params.get("ostemplate")
        config = _config_dict(state["backup_configs"][volid])
        for key in [k for k in config if k.startswith("net")]:
            if params.get("unique") == "1":
                config[key] = config[key].replace("BC:24:11:00:00:0", "BC:24:11:AA:BB:0")
        if not state["ignore_overrides"]:
            for key in ("name", "hostname", "onboot"):
                if key in params:
                    config[key] = params[key]
        config["digest"] = "d1"
        state["guest_configs"][(kind, vmid)] = config
        row = {"vmid": vmid, "node": node, "type": kind, "name": config.get("name") or config.get("hostname")}
        if pool is not None:
            row["pool"] = pool  # wie Proxmox: `/cluster/resources` nennt den Pool eines Gastes
            state["pools"][pool].append(vmid)
        state["resources"].append(row)
        worker = "vzrestore" if kind == "lxc" else "qmrestore"
        upid = f"UPID:{node}:0000ABCD:00C0FFEE:{int(time.time()):08X}:{worker}:{vmid}:{_TOKEN_ID}:"
        state["task_index"].append({"upid": upid, "node": node, "type": worker, "id": str(vmid), "starttime": int(time.time())})
        if state["answer_after_create"]:
            return JSONResponse({"data": None, "message": "proxy error"}, status_code=state["answer_after_create"])
        return {"data": state.get("upid") or upid}

    @app.post("/api2/json/nodes/{node}/qemu")
    async def create_qemu(node: str, request: Request) -> dict:
        return await _create(node, "qemu", request)

    @app.post("/api2/json/nodes/{node}/lxc")
    async def create_lxc(node: str, request: Request) -> dict:
        return await _create(node, "lxc", request)

    @app.get("/api2/json/nodes/{node}/tasks/{upid}/status")
    async def task_status(node: str, upid: str) -> dict:
        assert upid.startswith(f"UPID:{node}:")
        script = state["task_script"]
        index = min(state["task_calls"], len(script) - 1)
        state["task_calls"] += 1
        return {"data": {"upid": upid, "node": node, **script[index]}}

    @app.get("/api2/json/nodes/{node}/{kind}/{vmid}/config")
    async def guest_config(node: str, kind: str, vmid: int) -> dict:
        config = state["guest_configs"].get((kind, vmid))
        if config is None:
            raise HTTPException(status_code=500, detail=f"Configuration file '{kind}/{vmid}.conf' does not exist")
        return {"data": config}

    @app.put("/api2/json/nodes/{node}/{kind}/{vmid}/config")
    async def update_config(node: str, kind: str, vmid: int, request: Request) -> dict:
        params = _form(await request.body())
        state["puts"].append((kind, vmid, params))
        config = state["guest_configs"][(kind, vmid)]
        if params.get("digest") and params["digest"] != config["digest"]:
            raise HTTPException(status_code=500, detail="detected modified configuration - file changed by other user?")
        config.update({k: v for k, v in params.items() if k != "digest"})
        config["digest"] += "+"
        return {"data": None}

    return app, state


@pytest_asyncio.fixture
async def proxmox():
    app, state = _build_proxmox()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    serve_task = asyncio.ensure_future(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}", state
    finally:
        server.should_exit = True
        await serve_task


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _setup(client, db_session, test_settings, base_url: str) -> str:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    token = login.json()["access_token"]
    enabled = await client.post("/api/v1/extensions/backups/enable", headers=_auth(token))
    assert enabled.status_code == 200, enabled.text
    record = await db_session.get(ExtensionRecord, "backups")
    record.settings = {"connections": [{"name": "pve", "base_url": base_url, "token_id": _TOKEN_ID}]}
    await db_session.flush()
    res = await client.post("/api/v1/ext/backups/connections/pve/token", json={"value": _TOKEN_SECRET}, headers=_auth(token))
    assert res.status_code == 204, res.text
    return token


def _restore_module():
    return sys.modules["nodvard_deck_ext_backups.restore"]


def _writes(state: dict) -> list[tuple[str, str]]:
    return [(m, p) for m, p, _q in state["calls"] if m != "GET"]


QEMU_BODY = {"connection": "pve", "volid": QEMU_VOLID, "node": "pve1", "storage": "local-lvm", "vmid": 900, "name": "web-kopie", "link_down": True}
LXC_BODY = {"connection": "pve", "volid": LXC_VOLID, "node": "pve1", "storage": "local-lvm", "vmid": 901, "name": "dns-kopie", "link_down": True}


async def _propose(client, token: str, body: dict) -> dict:
    res = await client.post("/api/v1/ext/backups/restore", json=body, headers=_auth(token))
    assert res.status_code == 200, res.text
    return res.json()


async def _propose_and_approve(client, token: str, body: dict) -> dict:
    proposal = await _propose(client, token, body)
    assert proposal["status"] == "proposed" and proposal["risk"] == "high"
    res = await client.post(f"/api/v1/actions/{proposal['action_id']}/approve", headers=_auth(token))
    assert res.status_code == 200, res.text
    return res.json()


# --- Auswahl im Dialog -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dialog_lists_the_guests_backups_and_suggests_a_free_number_name_and_fitting_storage(
    client, db_session, test_settings, proxmox
):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)

    listing = await client.get("/api/v1/ext/backups/restore/backups", params={"connection": "pve", "vmid": "101"}, headers=_auth(token))
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["kind"] == "qemu" and body["exists"] is True
    assert [b["volid"] for b in body["backups"]] == [QEMU_VOLID]

    res = await client.get("/api/v1/ext/backups/restore/options", params={"connection": "pve", "volid": QEMU_VOLID}, headers=_auth(token))
    assert res.status_code == 200, res.text
    options = res.json()
    assert options["suggested_vmid"] == 100  # die kleinste freie Nummer ohne alte Sicherungen
    assert options["suggested_name"] == "web-kopie"
    assert options["source_vmid"] == "101" and options["kind"] == "qemu"
    assert options["disk_bytes"] == 32 * GIB  # die Platte mit backup=0 und das CD-Laufwerk zaehlen nicht
    assert options["default_node"] == "pve1" and options["default_storage"] == "local-lvm"
    assert [n["node"] for n in options["nodes"]] == ["pve1", "pve2"]  # gemeinsamer Backup-Speicher
    storages = {s["storage"]: s for s in options["nodes"][0]["storages"]}
    assert set(storages) == {"local-lvm", "small", "vmonly"}  # "local" nimmt keine VM-Platten
    assert storages["local-lvm"]["fits"] is True
    assert storages["small"]["fits"] is False  # 4 GB Sicherung + Reserve passen nicht in 3 GB
    # Dicker Speicher (LVM): dort zaehlt die volle Plattengroesse, 32 GB passen nicht in 20 GB.
    assert storages["vmonly"]["fits"] is False
    assert any("im Gast-System" in w for w in options["warnings"])
    assert _writes(state) == []


@pytest.mark.asyncio
async def test_suggestion_skips_numbers_with_old_backups_of_a_deleted_guest(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["resources"].append({"vmid": 100, "node": "pve1", "type": "qemu", "name": "router"})

    res = await client.get("/api/v1/ext/backups/restore/options", params={"connection": "pve", "volid": QEMU_VOLID}, headers=_auth(token))
    # 100 vergeben, 101 ist das Original, unter 102 liegen alte Sicherungen, 103 vergeben -> 105 (104 vergeben).
    assert res.json()["suggested_vmid"] == 105


@pytest.mark.asyncio
async def test_backup_on_a_node_local_storage_offers_only_that_node(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    res = await client.get("/api/v1/ext/backups/restore/options", params={"connection": "pve", "volid": LOCAL_VOLID}, headers=_auth(token))
    assert res.status_code == 200, res.text
    assert [n["node"] for n in res.json()["nodes"]] == ["pve2"]

    wrong_node = await client.post(
        "/api/v1/ext/backups/restore",
        json={**QEMU_BODY, "volid": LOCAL_VOLID, "node": "pve1"}, headers=_auth(token),
    )
    assert wrong_node.status_code == 409
    assert "von Knoten pve1 aus gesehen" in wrong_node.json()["detail"]
    assert _writes(state) == []


@pytest.mark.asyncio
async def test_lxc_options_name_the_fixed_address_from_the_backup(client, db_session, test_settings, proxmox):
    base_url, _state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    res = await client.get("/api/v1/ext/backups/restore/options", params={"connection": "pve", "volid": LXC_VOLID}, headers=_auth(token))
    options = res.json()
    assert options["kind"] == "lxc" and options["suggested_name"] == "dns-kopie"
    assert options["nets"] == [{"key": "net0", "bridge": "vmbr0", "tag": None, "addresses": ["192.168.2.53/24"]}]
    assert any("192.168.2.53/24" in w and "dieselbe" in w for w in options["warnings"])
    assert "vmonly" not in {s["storage"] for s in options["nodes"][0]["storages"]}  # nimmt keine Container


# --- Erfolg -----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qemu_copy_through_the_gate_with_exact_parameters_and_the_original_untouched(
    client, db_session, test_settings, proxmox
):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)

    action = await _propose_and_approve(client, token, QEMU_BODY)
    assert action["status"] == "succeeded", action

    assert state["created"] == [("pve1", "qemu", {
        "vmid": "900", "archive": QEMU_VOLID, "storage": "local-lvm", "unique": "1", "name": "web-kopie", "onboot": "0",
        "pool": POOL,
    })]
    assert state["pools"][POOL] == [900]
    # Nachbereitung nur auf der neuen Nummer: beide Netzwerkkarten getrennt, MACs neu, Notiz vor die alte.
    [(kind, vmid, params)] = state["puts"]
    assert (kind, vmid) == ("qemu", 900)
    assert params["net0"] == "virtio=BC:24:11:AA:BB:01,bridge=vmbr0,firewall=1,link_down=1"
    assert params["net1"] == "virtio=BC:24:11:AA:BB:02,bridge=vmbr1,link_down=1"
    assert params["digest"] == "d1"
    assert params["description"].startswith("Kopie aus der Sicherung vom 30.09.2026 04:00 UTC von VM 101 (web)")
    assert params["description"].endswith("\n\nWebserver im Keller")
    assert "onboot" not in params  # schon beim Anlegen aus
    config = state["guest_configs"][("qemu", 900)]
    assert config["onboot"] == "0" and config["name"] == "web-kopie"

    output = action["result"]["output"]
    assert "Kopie angelegt: VM 900 „web-kopie“" in output
    assert "startet nicht von selbst" in output and "neue MAC-Adressen" in output
    assert "Getrennt" in output and "Das Original ist unberührt" in output

    # Das Original: kein einziger Aufruf auf seine Nummer, schon gar kein schreibender.
    assert not [c for c in state["calls"] if "/qemu/101" in c[1] or "/lxc/101" in c[1]]
    assert _writes(state) == [("POST", "/nodes/pve1/qemu"), ("PUT", "/nodes/pve1/qemu/900/config")]

    audit = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "backups.restore_copy"))).scalars().all()
    assert [(a.outcome, a.target_type, a.target_id) for a in audit] == [("success", "restore", "pve/900")]


@pytest.mark.asyncio
async def test_lxc_copy_with_exact_parameters(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)

    action = await _propose_and_approve(client, token, LXC_BODY)
    assert action["status"] == "succeeded", action
    assert state["created"] == [("pve1", "lxc", {
        "vmid": "901", "ostemplate": LXC_VOLID, "restore": "1", "storage": "local-lvm", "unique": "1",
        "hostname": "dns-kopie", "onboot": "0", "pool": POOL,
    })]
    [(_kind, _vmid, params)] = state["puts"]
    assert params["net0"] == "name=eth0,bridge=vmbr0,hwaddr=BC:24:11:AA:BB:04,ip=192.168.2.53/24,gw=192.168.2.1,type=veth,link_down=1"
    assert "Container 901 „dns-kopie“" in action["result"]["output"]
    assert "„Netzwerk“" in action["result"]["output"]


@pytest.mark.asyncio
async def test_without_link_down_the_nics_stay_but_autostart_is_still_switched_off(client, db_session, test_settings, proxmox):
    """Auch wenn Proxmox den Autostart aus dem Aufruf nicht uebernimmt (aeltere Version), macht die Nachbereitung ihn aus."""
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["ignore_overrides"] = True

    action = await _propose_and_approve(client, token, {**QEMU_BODY, "link_down": False})
    assert action["status"] == "succeeded", action
    [(_kind, _vmid, params)] = state["puts"]
    assert params["onboot"] == "0" and params["name"] == "web-kopie"
    assert not any(k.startswith("net") for k in params)
    assert "Achtung: Im Gast gilt noch die Netzwerk-Einstellung" in action["result"]["output"]


# --- Nummer belegt ------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("vmid", "hidden", "expected"),
    [
        (101, [], "nicht die des Originals (101)"),
        (103, [], "schon vergeben (VM „db“ auf pve2)"),
        (950, [950], "in Proxmox schon vergeben"),  # Gast, den der Token nicht sieht
        (102, [], "Sicherungen eines früheren Gastes (backup-nas)"),
    ],
)
async def test_taken_number_creates_no_proposal(client, db_session, test_settings, proxmox, vmid, hidden, expected):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["hidden_vmids"] = hidden

    res = await client.post("/api/v1/ext/backups/restore", json={**QEMU_BODY, "vmid": vmid}, headers=_auth(token))
    assert res.status_code == 409, res.text
    assert expected in res.json()["detail"]
    assert (await db_session.execute(select(Action).where(Action.action_type == "backup.restore_copy"))).scalars().all() == []
    assert _writes(state) == []


@pytest.mark.asyncio
async def test_number_taken_after_the_proposal_aborts_without_creating_anything(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    proposal = await _propose(client, token, QEMU_BODY)
    state["hidden_vmids"] = [900]  # inzwischen anderweitig vergeben

    res = await client.post(f"/api/v1/actions/{proposal['action_id']}/approve", headers=_auth(token))
    action = res.json()
    assert action["status"] == "failed"
    assert "Die Nummer 900 ist in Proxmox schon vergeben" in action["result"]["error"]
    assert "Es wurde nichts angelegt" in action["result"]["error"]
    assert state["created"] == [] and _writes(state) == []


@pytest.mark.asyncio
async def test_proxmox_refusing_a_taken_number_at_creation_is_reported_and_nothing_is_overwritten(
    client, db_session, test_settings, proxmox, monkeypatch
):
    """Wettlauf: die Nummer wird zwischen der letzten Pruefung und dem Anlegen vergeben. Ohne `force` lehnt Proxmox ab."""
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    proposal = await _propose(client, token, QEMU_BODY)
    restore = _restore_module()
    real_check = restore.check_restore

    async def check_then_race(*args, **kwargs):
        plan = await real_check(*args, **kwargs)
        state["hidden_vmids"] = [900]
        return plan

    monkeypatch.setattr(restore, "check_restore", check_then_race)
    action = (await client.post(f"/api/v1/actions/{proposal['action_id']}/approve", headers=_auth(token))).json()
    assert action["status"] == "failed"
    assert "Proxmox hat die Wiederherstellung abgelehnt" in action["result"]["error"]
    assert "already exists" in action["result"]["error"]
    assert "force" not in state["created"][0][2]
    assert state["puts"] == []


# --- Platz und Speicher ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_too_little_space_is_refused_at_proposal_and_at_execution(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)

    res = await client.post("/api/v1/ext/backups/restore", json={**QEMU_BODY, "storage": "small"}, headers=_auth(token))
    assert res.status_code == 409
    assert "Zu wenig Platz auf „small“" in res.json()["detail"]
    assert "mindestens 6.4 GB" in res.json()["detail"]

    proposal = await _propose(client, token, QEMU_BODY)
    state["guest_storages"]["pve1"][0]["avail"] = 5 * GIB  # inzwischen vollgelaufen
    action = (await client.post(f"/api/v1/actions/{proposal['action_id']}/approve", headers=_auth(token))).json()
    assert action["status"] == "failed"
    assert "Zu wenig Platz auf „local-lvm“" in action["result"]["error"]
    assert state["created"] == []


@pytest.mark.asyncio
async def test_thick_storage_needs_the_full_disk_size(client, db_session, test_settings, proxmox):
    base_url, _state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    res = await client.post("/api/v1/ext/backups/restore", json={**QEMU_BODY, "storage": "vmonly"}, headers=_auth(token))
    assert res.status_code == 409
    assert "mindestens 34 GB" in res.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({**QEMU_BODY, "storage": "local"}, "keine VM-Platten ablegen"),
        ({**LXC_BODY, "storage": "vmonly"}, "keine Container ablegen"),
        ({**QEMU_BODY, "storage": "nirgends"}, "gibt es auf Knoten pve1 nicht"),
    ],
)
async def test_wrong_storage_type_is_refused(client, db_session, test_settings, proxmox, body, expected):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    res = await client.post("/api/v1/ext/backups/restore", json=body, headers=_auth(token))
    assert res.status_code == 409
    assert expected in res.json()["detail"]
    assert _writes(state) == []


@pytest.mark.asyncio
async def test_container_with_bind_mount_is_refused_before_anything_happens(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["backup_configs"][LXC_VOLID] = LXC_CONFIG + "mp0: /mnt/daten,mp=/daten\n"
    res = await client.post("/api/v1/ext/backups/restore", json=LXC_BODY, headers=_auth(token))
    assert res.status_code == 409
    assert "bindet Ordner oder Geräte des Servers ein (mp0)" in res.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"vmid": 99}, "Zahl von 100 bis 999999999"),
        ({"vmid": "9a0"}, "Zahl von 100 bis 999999999"),
        ({"name": "web kopie"}, "Der Name darf nur"),
        ({"name": "-web"}, "Der Name darf nur"),
        ({"name": "x" * 64}, "höchstens 63 Zeichen"),
        ({"node": "../pve1"}, "Zielknoten"),
        ({"storage": "a/b"}, "Zielspeicher"),
        ({"volid": "backup-nas:../../etc/passwd"}, "ungültige Kennung"),
        ({"link_down": "nein"}, "Netzwerkkarten"),
    ],
)
async def test_invalid_input_is_refused_in_plain_german(client, db_session, test_settings, proxmox, change, expected):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    res = await client.post("/api/v1/ext/backups/restore", json={**QEMU_BODY, **change}, headers=_auth(token))
    assert res.status_code == 422, res.text
    assert expected in res.json()["detail"]
    assert state["calls"] == []


# --- Proxmox-Aufgabe ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_proxmox_task_is_reported_honestly_and_nothing_is_changed_afterwards(
    client, db_session, test_settings, proxmox, monkeypatch
):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["task_script"] = [{"status": "running"}, {"status": "stopped", "exitstatus": "command 'vma extract' failed: exit code 133"}]
    monkeypatch.setattr(_restore_module(), "POLL_INTERVAL_S", 0.01)

    action = await _propose_and_approve(client, token, QEMU_BODY)
    assert action["status"] == "failed"
    error = action["result"]["error"]
    assert "Proxmox konnte die Sicherung nicht wiederherstellen. Grund laut Proxmox: command 'vma extract' failed: exit code 133." in error
    assert "einen Rest (Eintrag, teils Platten) unter der Nummer 900" in error and "selbst löschen" in error
    assert "Das Original ist unberührt" in error
    assert state["puts"] == []


@pytest.mark.asyncio
async def test_restore_still_running_after_the_time_limit_says_so_and_touches_nothing(
    client, db_session, test_settings, proxmox, monkeypatch
):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["task_script"] = [{"status": "running"}]
    restore = _restore_module()
    monkeypatch.setattr(restore, "RESTORE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(restore, "POLL_INTERVAL_S", 0.01)

    action = await _propose_and_approve(client, token, QEMU_BODY)
    assert action["status"] == "failed"
    error = action["result"]["error"]
    assert "läuft nach" in error and "Minuten noch; Proxmox macht weiter" in error
    # Nichts versprechen, was noch nicht stimmt: was Nodvard Deck nachholt und bis dahin gilt.
    assert "sobald Proxmox fertig ist" in error and "nicht starten" in error
    assert "Autostart aus" not in error
    assert state["puts"] == []


@pytest.mark.asyncio
async def test_missing_rights_403_names_the_rights_and_nothing_is_created(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["deny"] = [("POST", "/nodes/pve1/qemu")]

    action = await _propose_and_approve(client, token, QEMU_BODY)
    assert action["status"] == "failed"
    error = action["result"]["error"]
    assert error.startswith("Proxmox hat die Wiederherstellung abgelehnt (keine Berechtigung), es wurde nichts angelegt.")
    assert f"es gibt den Pool {POOL} nicht: VM.Allocate" in error and "SDN.Use" in error
    assert "HTTP 403" in error
    assert state["guest_configs"] == {}


@pytest.mark.asyncio
async def test_copy_that_cannot_be_finished_is_reported_as_created_but_not_ready(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["deny"] = [("PUT", "/nodes/pve1/qemu/900/config")]

    action = await _propose_and_approve(client, token, QEMU_BODY)
    assert action["status"] == "failed"
    error = action["result"]["error"]
    assert error.startswith("Die Kopie ist angelegt (VM 900 „web-kopie“ auf pve1, ausgeschaltet)")
    assert "Netzwerkkarten trennen" in error
    assert action["result"]["detail"]["created"] is True


@pytest.mark.asyncio
async def test_answer_that_is_not_this_restore_task_leaves_every_guest_untouched(client, db_session, test_settings, proxmox):
    """Nur wenn Proxmox' Aufgabe nachweislich die Wiederherstellung DIESER Nummer ist, wird danach etwas eingerichtet."""
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["upid"] = f"UPID:pve1:0000ABCD:00C0FFEE:67000000:qmrestore:101:{_TOKEN_ID}:"

    action = await _propose_and_approve(client, token, QEMU_BODY)
    assert action["status"] == "failed"
    assert "keine gültige Aufgaben-Kennung" in action["result"]["error"]
    assert state["task_calls"] == 0 and state["puts"] == []


# --- Gate ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_approval_nothing_is_written_to_proxmox(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)

    proposal = await _propose(client, token, QEMU_BODY)
    assert proposal["status"] == "proposed" and proposal["risk"] == "high"
    row = await db_session.get(Action, proposal["action_id"])
    assert "neue Nummer 900" in row.reason and "Das Original bleibt unberührt" in row.reason
    assert row.payload["kind"] == "qemu" and row.payload["source_vmid"] == "101"

    rejected = await client.post(f"/api/v1/actions/{proposal['action_id']}/reject", json={"reason": "doch nicht"}, headers=_auth(token))
    assert rejected.status_code == 200, rejected.text
    assert _writes(state) == []


def _executor(base_url: str, data_dir: str | None = None):
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "backups" / "src"))
    from nodvard_deck_ext_backups.connector import ProxmoxBackupConnector
    from nodvard_deck_ext_backups.restore import RestoreCopyExecutor

    class _Http:
        async def request(self, method, url, *, insecure_tls=False, **kwargs):
            async with httpx.AsyncClient() as http:
                return await http.request(method, url, **kwargs)

    class _Audit:
        def __init__(self):
            self.entries: list = []

        async def log(self, **kwargs):
            self.entries.append(kwargs)

    class _Notify:
        def __init__(self):
            self.sent: list = []

        async def send(self, notification):
            self.sent.append(notification)

    ctx = type("Ctx", (), {
        "http": _Http(), "audit": _Audit(), "notify": _Notify(), "data_dir": data_dir or tempfile.mkdtemp(prefix="rk-"),
    })()
    connector = ProxmoxBackupConnector(ctx, base_url=base_url, token_id=_TOKEN_ID, token_secret=_TOKEN_SECRET)

    async def factory():
        return {"pve": connector}

    return RestoreCopyExecutor(ctx, factory)


def _request(payload: dict, risk: str = "high"):
    from nodvard_sdk import Actor, Risk
    from nodvard_sdk.actions import ActionRequest

    return ActionRequest(
        action_type="backup.restore_copy", payload=payload, risk=Risk(risk),
        proposed_by=Actor.extension("backups"), reason="Test",
    )


@pytest.mark.asyncio
async def test_payload_from_the_gate_is_rechecked_and_never_trusted(proxmox):
    """Was im Gate steht, wird beim Ausfuehren frisch geprueft; fremde Schluessel gehen nie an Proxmox."""
    base_url, state = proxmox
    executor = _executor(base_url)
    base = {**QEMU_BODY, "kind": "qemu", "source_vmid": "101", "backup_at": 1790740800}

    low = await executor.execute(_request(base, risk="medium"))
    assert not low.success and "nicht mit hohem Risiko freigegeben" in low.error

    for change, expected in (
        ({"vmid": 101}, "nicht die des Originals"),
        ({"kind": "lxc"}, "passt nicht mehr zum Vorschlag"),
        ({"backup_at": 1}, "passt nicht mehr zum Vorschlag"),
        ({"connection": "andere"}, "nicht (mehr) konfiguriert"),
        ({"storage": "small"}, "Zu wenig Platz"),
    ):
        result = await executor.execute(_request({**base, **change}))
        assert not result.success and expected in result.error, (change, result.error)
    assert _writes(state) == []

    sneaky = await executor.execute(_request({**base, "force": 1, "start": 1, "live-restore": 1, "pool": "x", "vmid": 902}))
    assert sneaky.success, sneaky.error
    [(_node, _kind, params)] = state["created"]
    assert set(params) == {"vmid", "archive", "storage", "unique", "name", "onboot", "pool"}
    assert params["vmid"] == "902" and params["pool"] == POOL  # nie der Pool aus dem Payload


@pytest.mark.asyncio
async def test_task_answers_lost_for_a_while_count_only_after_several_errors(proxmox, monkeypatch):
    base_url, state = proxmox
    executor = _executor(base_url)
    restore = sys.modules["nodvard_deck_ext_backups.restore"]
    monkeypatch.setattr(restore, "POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(restore, "MAX_POLL_ERRORS", 3)
    state["deny"] = [("GET", "/nodes/pve1/tasks/")]

    result = await executor.execute(_request({**QEMU_BODY, "kind": "qemu", "source_vmid": "101"}))
    assert not result.success
    assert "Proxmox antwortet nicht mehr" in result.error and "Kopie bitte nicht starten" in result.error
    assert "Autostart aus" not in result.error
    assert state["puts"] == []


# --- Bausteine ----------------------------------------------------------------------------------------


def _restore_unit():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "backups" / "src"))
    from nodvard_deck_ext_backups import restore

    return restore


def test_link_down_is_added_or_replaced_never_duplicated():
    restore = _restore_unit()
    assert restore.with_link_down("virtio=BC:24:11:00:00:01,bridge=vmbr0") == "virtio=BC:24:11:00:00:01,bridge=vmbr0,link_down=1"
    assert restore.with_link_down("virtio=X,link_down=0,bridge=vmbr0") == "virtio=X,link_down=1,bridge=vmbr0"
    assert restore.with_link_down("virtio=X,link_down=1") == "virtio=X,link_down=1"


def test_backup_config_is_read_up_to_the_first_section():
    restore = _restore_unit()
    config = restore.parse_backup_config("#Notiz\nname: web\nnet0: virtio=X,bridge=vmbr0\n[snap1]\nname: alt\n")
    assert config == {"name": "web", "net0": "virtio=X,bridge=vmbr0"}


def test_space_minimum_is_backup_plus_reserve_and_full_disks_on_thick_storage():
    restore = _restore_unit()
    assert restore.needed_bytes(10 * GIB, 100 * GIB, "lvmthin") == 11 * GIB + 2 * GIB
    assert restore.needed_bytes(10 * GIB, 100 * GIB, "lvm") == 102 * GIB
    assert restore.space_warning(100 * GIB, 50 * GIB, "lvmthin").startswith("Kann knapp werden")
    assert restore.space_warning(100 * GIB, 500 * GIB, "lvmthin") is None


def test_cloud_init_address_of_a_vm_is_named_and_dhcp_is_not():
    restore = _restore_unit()
    info = restore.describe_config("qemu", {
        "name": "web", "net0": "virtio=X,bridge=vmbr0", "ipconfig0": "ip=192.168.2.20/24,gw=192.168.2.1",
        "net1": "virtio=Y,bridge=vmbr1", "ipconfig1": "ip=dhcp",
    })
    assert info.nets == [
        {"key": "net0", "bridge": "vmbr0", "tag": None, "addresses": ["192.168.2.20/24"]},
        {"key": "net1", "bridge": "vmbr1", "tag": None, "addresses": []},
    ]
    assert any("192.168.2.20/24" in w for w in info.warnings)
    assert not any("im Gast-System" in w for w in info.warnings)


def test_suggested_name_is_a_valid_proxmox_name():
    restore = _restore_unit()
    assert restore.suggest_name("Mein Server (alt)", "101") == "Mein-Server-alt-kopie"
    assert restore.suggest_name(None, "101") == "gast-101-kopie"
    long = restore.suggest_name("a" * 80, "101")
    assert len(long) <= 63 and long.endswith("-kopie")


def test_note_goes_before_the_old_description_and_stays_within_the_proxmox_limit():
    restore = _restore_unit()
    inp = restore.RestoreInput(volid=QEMU_VOLID, node="pve1", storage="local-lvm", vmid=900, name="web-kopie", link_down=True)
    plan = restore.RestorePlan(
        connection="pve", inp=inp, kind="qemu", source_vmid="101", source_name="web", backup_at=1790740800,
        backup_size=GIB, disk_bytes=None, need_bytes=3 * GIB, avail_bytes=10 * GIB, storage_type="lvmthin", warnings=[],
    )
    target = plan.target(1.0)
    changes = restore.finish_changes(target, {"description": "ä" * 9000, "net0": "virtio=X,bridge=vmbr0", "name": "web-kopie", "digest": "d"})
    assert changes["description"].startswith(plan.note() + "\n\n")
    assert len(changes["description"].encode()) <= 8000
    assert changes["net0"] == "virtio=X,bridge=vmbr0,link_down=1" and changes["digest"] == "d"
    assert "name" not in changes and "onboot" not in changes
    # Schon fertig eingerichtet: nichts mehr zu tun.
    done = {"description": plan.note(), "net0": "virtio=X,bridge=vmbr0,link_down=1", "name": "web-kopie", "onboot": "0"}
    assert restore.finish_changes(target, done) == {}
    assert restore.finish_problems(target, done) == []
    assert restore.finish_problems(target, {**done, "onboot": "1", "net1": "virtio=Y"}) == [
        "Netzwerkkarten nicht getrennt (net1)", "Autostart ist an",
    ]


def test_restore_task_id_must_name_node_kind_and_new_number():
    restore = _restore_unit()
    upid = "UPID:pve1:0000ABCD:00C0FFEE:67000000:qmrestore:900:nodvard@pve!dashboard:"
    assert restore.upid_is_restore_of(upid, "pve1", "qemu", 900)
    assert not restore.upid_is_restore_of(upid, "pve2", "qemu", 900)
    assert not restore.upid_is_restore_of(upid, "pve1", "lxc", 900)
    assert not restore.upid_is_restore_of(upid, "pve1", "qemu", 901)
    assert not restore.upid_is_restore_of("UPID:pve1:x/../y:0:0:qmrestore:900:u:", "pve1", "qemu", 900)


def test_nothing_that_overwrites_or_starts_can_reach_proxmox():
    _restore_unit()
    from nodvard_deck_ext_backups.connector import RESTORE_KEYS

    for keys in RESTORE_KEYS.values():
        assert not keys & {"force", "start", "live-restore", "ha-managed"}


# --- Nachbereiten offener Kopien (Abbruch, Zeitgrenze, Neustart des Dashboards) ------------------------


def _pending(data_dir) -> dict:
    path = Path(str(data_dir)) / "restore-pending.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


QEMU_PAYLOAD = {**QEMU_BODY, "kind": "qemu", "source_vmid": "101", "backup_at": 1790740800}


@pytest.mark.asyncio
async def test_dashboard_restart_mid_restore_is_finished_later_by_the_watch(proxmox, monkeypatch, tmp_path):
    """Wird das Dashboard beendet, waehrend Proxmox noch herstellt, bleibt ein Vermerk; der Waechter richtet die Kopie
    danach fertig ein (Netzwerkkarten getrennt), meldet das und raeumt den Vermerk weg."""
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    monkeypatch.setattr(restore, "POLL_INTERVAL_S", 0.01)
    state["task_script"] = [{"status": "running"}]

    task = asyncio.ensure_future(executor.execute(_request(QEMU_PAYLOAD)))
    for _ in range(500):
        if state["task_calls"] >= 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()  # so endet die Aktion, wenn das Dashboard beendet wird
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state["puts"] == []
    [record] = _pending(tmp_path).values()
    assert record["vmid"] == 900 and record["upid"].startswith("UPID:pve1:") and record["link_down"] is True
    assert restore.pending.ACTIVE == set()

    calls_before = len(state["calls"])
    state["task_script"] = [{"status": "stopped", "exitstatus": "OK"}]
    state["task_calls"] = 0
    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {"finished": 1}
    [(_kind, vmid, params)] = state["puts"]
    assert vmid == 900 and params["net0"].endswith(",link_down=1") and params["net1"].endswith(",link_down=1")
    assert _pending(tmp_path) == {}
    [note] = executor._ctx.notify.sent
    assert note.title == "Kopie fertig eingerichtet: VM 900 „web-kopie“"
    assert note.severity.value == "info" and note.payload == {"path": "/ext/backups/backups"}
    assert "Netzwerkkarten sind getrennt" in note.body
    assert executor._ctx.audit.entries[-1]["detail"]["followup"] is True
    # Nur die neue Nummer: kein Aufruf auf das Original, kein Loeschen.
    assert not [c for c in state["calls"][calls_before:] if "/101" in c[1] or c[0] == "DELETE"]
    # Ohne Vermerk kein einziger Aufruf an Proxmox.
    calls_before = len(state["calls"])
    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {}
    assert len(state["calls"]) == calls_before


@pytest.mark.asyncio
async def test_time_limit_leaves_the_copy_to_the_backup_watch(client, db_session, test_settings, proxmox, monkeypatch):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    restore = _restore_module()
    monkeypatch.setattr(restore, "RESTORE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(restore, "POLL_INTERVAL_S", 0.01)
    state["task_script"] = [{"status": "running"}]

    action = await _propose_and_approve(client, token, QEMU_BODY)
    assert action["status"] == "failed"
    assert "der Backup-Wächter sieht alle 15 Minuten nach" in action["result"]["error"]
    data_dir = test_settings.ext_data_dir / "backups"
    assert list(_pending(data_dir)) == ["pve/pve1/900"]

    state["task_script"] = [{"status": "stopped", "exitstatus": "OK"}]
    state["task_calls"] = 0
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck.models import Notification as NotificationRow

    await get_extension_runtime().scheduler.get("backups", "watch")()
    assert _pending(data_dir) == {}
    assert state["puts"][0][2]["net0"].endswith(",link_down=1")
    rows = (await db_session.execute(select(NotificationRow).where(NotificationRow.source_ext_id == "backups"))).scalars().all()
    assert [r.title for r in rows] == ["Kopie fertig eingerichtet: VM 900 „web-kopie“"]


@pytest.mark.asyncio
async def test_follow_up_reports_a_failed_restore_and_never_deletes(proxmox, monkeypatch, tmp_path):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    monkeypatch.setattr(restore, "RESTORE_TIMEOUT_S", 0.0)
    state["task_script"] = [{"status": "running"}]
    assert not (await executor.execute(_request(QEMU_PAYLOAD))).success
    state["task_script"] = [{"status": "stopped", "exitstatus": "unable to restore VM 900 - no space left"}]
    state["task_calls"] = 0

    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {"failed": 1}
    [note] = executor._ctx.notify.sent
    assert note.title == "Wiederherstellung gescheitert: VM 900 „web-kopie“" and note.severity.value == "warning"
    assert "no space left" in note.body and "selbst löschen" in note.body
    assert state["puts"] == [] and not [c for c in state["calls"] if c[0] == "DELETE"]
    assert _pending(tmp_path) == {}


@pytest.mark.asyncio
async def test_follow_up_waits_while_proxmox_works_and_gives_up_after_a_day(proxmox, monkeypatch, tmp_path):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    monkeypatch.setattr(restore, "RESTORE_TIMEOUT_S", 0.0)
    state["task_script"] = [{"status": "running"}]
    await executor.execute(_request(QEMU_PAYLOAD))
    [record] = _pending(tmp_path).values()

    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {"open": 1}
    assert executor._ctx.notify.sent == [] and len(_pending(tmp_path)) == 1

    later = record["created_at"] + 25 * 3600
    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory, now=later) == {"given_up": 1}
    [note] = executor._ctx.notify.sent
    assert note.title == "Kopie nicht fertig eingerichtet: VM 900 „web-kopie“"
    assert "Nach 24 Stunden gibt Nodvard Deck auf" in note.body and "Netzwerkkarten trennen" in note.body
    assert _pending(tmp_path) == {} and state["puts"] == []


@pytest.mark.asyncio
async def test_follow_up_leaves_copies_alone_that_a_running_action_handles(proxmox, tmp_path):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    target = restore.CopyTarget(
        connection="pve", node="pve1", kind="qemu", vmid=900, name="web-kopie", link_down=True, note="n",
        source_text="VM 101", created_at=time.time(),
    )
    restore.pending.remember(executor._ctx, target.key, target.record())
    restore.pending.ACTIVE.add(target.key)
    try:
        assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {}
    finally:
        restore.pending.ACTIVE.discard(target.key)
    assert [c for c in state["calls"] if c[1] != "/nodes"] == [] and len(_pending(tmp_path)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [502, 503, 504, 595, 596])
async def test_unclear_answer_on_creation_is_not_reported_as_nothing_created(proxmox, tmp_path, answer):
    """Ein Proxy (oder Proxmox' eigener Weiterleiter) meldet einen Fehler, Proxmox hat aber angefangen: unklar, und der
    Waechter findet die Aufgabe ueber die Aufgabenliste und richtet die Kopie fertig ein."""
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    state["answer_after_create"] = answer

    result = await executor.execute(_request(QEMU_PAYLOAD))
    assert not result.success
    assert "Keine klare Antwort von Proxmox" in result.error and "nichts angelegt" not in result.error
    assert "sobald Proxmox fertig ist" in result.error
    [record] = _pending(tmp_path).values()
    assert record["upid"] is None

    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {"finished": 1}
    assert state["puts"][0][2]["net0"].endswith(",link_down=1")


@pytest.mark.asyncio
async def test_follow_up_without_task_after_a_while_says_nothing_was_created_or_asks_to_check(proxmox, tmp_path):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    old = time.time() - 3600
    for vmid in (950, 103):  # 950 gibt es nicht, 103 gibt es (aber ohne Wiederherstellung von hier)
        target = restore.CopyTarget(
            connection="pve", node="pve1", kind="qemu", vmid=vmid, name="web-kopie", link_down=True, note="n",
            source_text="VM 101", created_at=old,
        )
        restore.pending.remember(executor._ctx, target.key, target.record())

    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {"not_created": 1, "unclear": 1}
    titles = sorted(n.title for n in executor._ctx.notify.sent)
    assert titles == ["Wiederherstellung nicht begonnen: VM 950 „web-kopie“", "Wiederherstellung unklar: VM 103 „web-kopie“"]
    assert _pending(tmp_path) == {} and state["puts"] == []


@pytest.mark.asyncio
async def test_without_a_writable_data_folder_nothing_is_created(proxmox, tmp_path):
    base_url, state = proxmox
    blocked = tmp_path / "datei"
    blocked.write_text("kein Ordner")
    executor = _executor(base_url, str(blocked))
    result = await executor.execute(_request(QEMU_PAYLOAD))
    assert not result.success and "kann sich die Wiederherstellung nicht merken" in result.error
    assert "Es wurde nichts angelegt" in result.error
    assert state["created"] == []


@pytest.mark.asyncio
async def test_task_answer_without_status_is_not_a_failure(proxmox, monkeypatch, tmp_path):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    monkeypatch.setattr(restore, "POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(restore, "MAX_POLL_ERRORS", 3)
    state["task_script"] = [{}]

    result = await executor.execute(_request(QEMU_PAYLOAD))
    assert not result.success
    assert "Proxmox antwortet nicht mehr" in result.error
    assert "konnte die Sicherung nicht wiederherstellen" not in result.error
    assert len(_pending(tmp_path)) == 1  # der Waechter sieht nach


# --- Rechte, HA, durchgereichte Hardware --------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "missing", "expected"),
    [
        (QEMU_BODY, {POOL_PATH: ["VM.Config.Network"]}, f"VM.Config.Network auf dem Pool „{POOL}“"),
        (QEMU_BODY, {POOL_PATH: ["VM.Config.Options", "VM.Audit"]}, f"VM.Audit, VM.Config.Options auf dem Pool „{POOL}“"),
        (QEMU_BODY, {"/storage/local-lvm": ["Datastore.AllocateSpace"]}, "Datastore.AllocateSpace auf dem Zielspeicher „local-lvm“"),
        (QEMU_BODY, {"/sdn/zones/localnetwork/vmbr1": ["SDN.Use"]}, "SDN.Use für die Bridge vmbr1"),
        ({**LXC_BODY, "link_down": False}, {POOL_PATH: ["VM.Config.Network"]}, f"VM.Config.Network auf dem Pool „{POOL}“"),
    ],
)
async def test_missing_rights_are_found_before_anything_is_created(
    client, db_session, test_settings, proxmox, body, missing, expected
):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["missing_rights"] = missing
    res = await client.post("/api/v1/ext/backups/restore", json=body, headers=_auth(token))
    assert res.status_code == 409, res.text
    assert f"Dem Zugang fehlt ein Recht: {expected}" in res.json()["detail"]
    assert _writes(state) == []


@pytest.mark.asyncio
async def test_rights_are_checked_again_when_the_action_runs(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    proposal = await _propose(client, token, QEMU_BODY)
    state["missing_rights"] = {POOL_PATH: ["VM.Config.Network"]}
    action = (await client.post(f"/api/v1/actions/{proposal['action_id']}/approve", headers=_auth(token))).json()
    assert action["status"] == "failed" and "VM.Config.Network" in action["result"]["error"]
    assert state["created"] == []


@pytest.mark.asyncio
async def test_rights_that_are_not_needed_or_not_known_do_not_block(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    # Ohne getrennte Netzwerkkarten braucht eine VM VM.Config.Network nicht; vor VE 8 gibt es SDN.Use nicht.
    state["missing_rights"] = {POOL_PATH: ["VM.Config.Network"], "/sdn/zones/localnetwork/vmbr0": ["SDN.Use"]}
    state["version"] = "7.4-3"
    assert (await _propose(client, token, {**QEMU_BODY, "link_down": False}))["status"] == "proposed"
    # Nicht lesbare Rechte: kein Hindernis, aber ein Hinweis.
    state["deny"] = [("GET", "/access/permissions")]
    proposal = await _propose(client, token, {**QEMU_BODY, "vmid": 902})
    assert any("ließen sich vorab nicht prüfen" in w for w in proposal["warnings"])


@pytest.mark.asyncio
async def test_privileged_container_needs_sys_modify_up_front(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["backup_configs"][LXC_VOLID] = LXC_CONFIG.replace("unprivileged: 1\n", "")
    state["missing_rights"] = {"/": ["Sys.Modify"]}
    res = await client.post("/api/v1/ext/backups/restore", json=LXC_BODY, headers=_auth(token))
    assert res.status_code == 409 and "Sys.Modify auf „/“ (privilegierter Container)" in res.json()["detail"]


@pytest.mark.asyncio
async def test_ha_rule_for_the_new_number_blocks_and_is_skipped_by_the_suggestion(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["ha"] = [{"sid": "vm:900", "state": "started"}, {"sid": "ct:100"}]

    res = await client.post("/api/v1/ext/backups/restore", json=QEMU_BODY, headers=_auth(token))
    assert res.status_code == 409 and "HA-Regel" in res.json()["detail"] and "sofort starten" in res.json()["detail"]
    options = (await client.get("/api/v1/ext/backups/restore/options", params={"connection": "pve", "volid": QEMU_VOLID}, headers=_auth(token))).json()
    assert options["suggested_vmid"] == 105  # 100 hat eine HA-Regel, 101-104 vergeben oder mit alten Sicherungen

    state["deny"] = [("GET", "/cluster/ha/resources")]
    proposal = await _propose(client, token, {**QEMU_BODY, "vmid": 902})
    assert any("HA-Regel" in w and "Sys.Audit" in w for w in proposal["warnings"])
    assert _writes(state) == []


@pytest.mark.asyncio
async def test_passed_through_hardware_is_refused_or_named(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)

    state["backup_configs"][QEMU_VOLID] = QEMU_CONFIG + "hostpci0: 0000:01:00.0,pcie=1\n"
    res = await client.post("/api/v1/ext/backups/restore", json=QEMU_BODY, headers=_auth(token))
    assert res.status_code == 409 and "reicht Geräte des Servers direkt durch (hostpci0)" in res.json()["detail"]

    state["backup_configs"][QEMU_VOLID] = QEMU_CONFIG + "hostpci0: mapping=gpu,pcie=1\nusb0: spice\nsata1: /dev/disk/by-id/ata-x,backup=0,size=1T\n"
    options = (await client.get("/api/v1/ext/backups/restore/options", params={"connection": "pve", "volid": QEMU_VOLID}, headers=_auth(token))).json()
    assert options["refuse"] is None
    assert any("hostpci0 reicht ein Gerät des Servers durch (Zuordnung „gpu“)" in w for w in options["warnings"])
    assert any("sata1 ist eine Platte des Servers" in w and "bekommt sie nicht" in w for w in options["warnings"])
    assert not any("usb0" in w for w in options["warnings"])
    state["missing_rights"] = {"/mapping/pci/gpu": ["Mapping.Use"]}
    res = await client.post("/api/v1/ext/backups/restore", json=QEMU_BODY, headers=_auth(token))
    assert res.status_code == 409 and "Mapping.Use auf /mapping/pci/gpu" in res.json()["detail"]

    state["backup_configs"][LXC_VOLID] = LXC_CONFIG + "dev0: /dev/ttyUSB0,mode=0660\n"
    options = (await client.get("/api/v1/ext/backups/restore/options", params={"connection": "pve", "volid": LXC_VOLID}, headers=_auth(token))).json()
    assert any("dev0 reicht ein Gerät des Servers in den Container durch (/dev/ttyUSB0)" in w for w in options["warnings"])


@pytest.mark.asyncio
async def test_dialog_routes_need_the_right_to_trigger_actions(client, db_session, test_settings, proxmox):
    """Die Auswahl zeigt feste Adressen und Geraete aus der Gast-Konfiguration: nur fuer `hosts.execute`."""
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    base_url, _state = proxmox
    await _setup(client, db_session, test_settings, base_url)
    roles = await auth_service.ensure_builtin_roles(db_session)
    for username, role in (("gast", "viewer"), ("helfer", "operator")):
        user = User(username=username, password_hash=security.hash_password("whatever123"), is_active=True)
        user.roles.append(roles[role])
        db_session.add(user)
    await db_session.flush()
    for username, expected in (("gast", 403), ("helfer", 200)):
        login = await client.post("/api/v1/auth/login", json={"username": username, "password": "whatever123"})
        headers = _auth(login.json()["access_token"])
        options = await client.get("/api/v1/ext/backups/restore/options", params={"connection": "pve", "volid": QEMU_VOLID}, headers=headers)
        listing = await client.get("/api/v1/ext/backups/restore/backups", params={"connection": "pve", "vmid": "101"}, headers=headers)
        assert (options.status_code, listing.status_code) == (expected, expected), username


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"volid": QEMU_VOLID + "\n"}, {"volid": " " + QEMU_VOLID}, {"node": "pve1\n"}, {"storage": "local-lvm\n"},
        {"vmid": "900\n"}, {"vmid": " 900"}, {"vmid": 900.0}, {"vmid": "٩٠٠"}, {"name": "web-kopie\n"}, {"name": " web-kopie"},
    ],
)
async def test_values_must_match_exactly_not_after_trimming(proxmox, tmp_path, change):
    base_url, state = proxmox
    result = await _executor(base_url, str(tmp_path)).execute(_request({**QEMU_PAYLOAD, **change}))
    assert not result.success and "Es wurde nichts angelegt" in result.error
    assert _writes(state) == []


@pytest.mark.asyncio
async def test_connector_sends_only_the_finishing_keys_and_checks_task_ids():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "backups" / "src"))
    from nodvard_deck_ext_backups.connector import (
        ProxmoxBackupApiError,
        ProxmoxBackupConnector,
    )

    sent: list = []

    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {"data": None}

    class _Http:
        async def request(self, method, url, **kwargs):
            sent.append((method, url.split("/api2/json")[1], kwargs.get("data")))
            return _Response()

    connector = ProxmoxBackupConnector(type("C", (), {"http": _Http()})(), base_url="http://x", token_id="a", token_secret="b")
    await connector.update_guest_config("pve1", "qemu", "900", {
        "net0": "virtio=X,link_down=1", "onboot": 0, "name": "n", "description": "d", "digest": "x",
        "delete": "net0", "hookscript": "local:snippets/x.sh", "scsi0": "local-lvm:1", "net0x": "y",
    })
    assert set(sent[0][2]) == {"net0", "onboot", "name", "description", "digest"}
    for bad in ("UPID:pve1:0000ABCD:00C0FFEE:67000000:qmrestore:900:u?x:", "UPID:pve1:0000ABCD:00C0FFEE:67000000:qmrestore:900:u/../x:",
                "UPID:pve1:0000ABCD:00C0FFEE:67000000:qmrestore:900:u%2F:", "UPID:pve1:0000ABCD:00C0FFEE:67000000:qmrestore:900:u#x:"):
        with pytest.raises(ProxmoxBackupApiError):
            await connector.task_status("pve1", bad)
    assert len(sent) == 1


# --- Nur im eigenen Pool ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copies_always_and_only_go_into_the_copy_pool_whatever_the_request_says(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["pools"]["anderer"] = []

    proposal = await _propose(client, token, {**QEMU_BODY, "pool": "anderer", "force": 1, "start": 1})
    row = await db_session.get(Action, proposal["action_id"])
    assert "pool" not in row.payload and "force" not in row.payload  # der Pool steht nie im Gate

    action = (await client.post(f"/api/v1/actions/{proposal['action_id']}/approve", headers=_auth(token))).json()
    assert action["status"] == "succeeded", action
    [(_node, _kind, params)] = state["created"]
    assert params["pool"] == POOL and not {"force", "start"} & set(params)
    assert state["pools"][POOL] == [900] and state["pools"]["anderer"] == []
    assert "Pool nodvard-kopien" in action["result"]["output"]


@pytest.mark.asyncio
async def test_connector_sets_the_pool_itself_even_when_the_caller_names_another():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "backups" / "src"))
    from nodvard_deck_ext_backups.connector import COPY_POOL, RESTORE_KEYS, ProxmoxBackupConnector

    sent: list = []

    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {"data": "UPID:pve1:0000ABCD:00C0FFEE:67000000:qmrestore:900:u@pve!t:"}

    class _Http:
        async def request(self, method, url, **kwargs):
            sent.append((method, url.split("/api2/json")[1], kwargs.get("data")))
            return _Response()

    connector = ProxmoxBackupConnector(type("C", (), {"http": _Http()})(), base_url="http://x", token_id="a@pve!t", token_secret="b")
    for kind in ("qemu", "lxc"):
        await connector.restore_guest("pve1", kind, {"vmid": 900, "pool": "fremd", "force": 1, "start": 1, "live-restore": 1})
        await connector.restore_guest("pve1", kind, {"vmid": 900})  # auch ohne Angabe
    assert COPY_POOL == "nodvard-kopien"
    assert [data for _m, _p, data in sent] == [{"vmid": 900, "pool": COPY_POOL}] * 4
    assert all("pool" in keys for keys in RESTORE_KEYS.values())
    assert not any({"force", "start", "live-restore"} & keys for keys in RESTORE_KEYS.values())


@pytest.mark.asyncio
async def test_rights_for_the_new_guest_are_asked_for_the_pool_not_for_the_new_number(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    await _propose(client, token, QEMU_BODY)
    asked = [q["path"] for m, p, q in state["calls"] if p == "/access/permissions"]
    assert POOL_PATH in asked
    assert "/vms/900" not in asked  # vor dem Anlegen steckt die Nummer in keinem Pool, dort gibt es nichts zu pruefen


@pytest.mark.asyncio
async def test_missing_pool_or_rights_on_it_say_how_to_set_it_up_and_create_nothing(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["missing_rights"] = {POOL_PATH: list(ALL_RIGHTS)}  # kein Pool, oder keine Rechte darauf: fuer den Zugang dasselbe

    for body in (QEMU_BODY, LXC_BODY):
        res = await client.post("/api/v1/ext/backups/restore", json=body, headers=_auth(token))
        assert res.status_code == 409, res.text
        text = res.json()["detail"]
        assert f"Für Kopien braucht Nodvard Deck den Pool {POOL}" in text
        assert "Verbinde Proxmox einmal neu mit Passwort, dann legt Nodvard Deck ihn an" in text
        assert f"pveum pool add {POOL}" in text
        assert "pveum role add NodvardDeckRestore --privs 'VM.Allocate VM.Audit VM.Config.Options VM.Config.Network'" in text
        assert f"pveum acl modify {POOL_PATH} --roles NodvardDeckRestore --users nodvard@pve --tokens 'nodvard@pve!dashboard'" in text
        assert "Es wurde nichts angelegt" in text
        assert "der Pool oder das Recht fehlt" in text and "beides fehlt" not in text  # der Zugang sieht nicht, welches von beiden
    assert (await db_session.execute(select(Action).where(Action.action_type == "backup.restore_copy"))).scalars().all() == []
    assert _writes(state) == []


@pytest.mark.asyncio
async def test_pool_rights_that_vanish_after_the_proposal_stop_the_execution(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    proposal = await _propose(client, token, QEMU_BODY)
    state["missing_rights"] = {POOL_PATH: ["VM.Allocate"]}
    action = (await client.post(f"/api/v1/actions/{proposal['action_id']}/approve", headers=_auth(token))).json()
    assert action["status"] == "failed" and f"Pool {POOL}" in action["result"]["error"]
    assert state["created"] == [] and _writes(state) == []


@pytest.mark.asyncio
async def test_pool_that_proxmox_does_not_know_is_reported_with_the_pool_and_nothing_is_created(proxmox, tmp_path):
    """Der Zugang hat VM.Allocate anderswo (etwa auf "/"), den Pool gibt es aber nicht: Proxmox antwortet 403."""
    base_url, state = proxmox
    state["pools"] = {}
    executor = _executor(base_url, str(tmp_path))
    result = await executor.execute(_request(QEMU_PAYLOAD))
    assert not result.success
    assert result.error.startswith("Proxmox hat die Wiederherstellung abgelehnt (keine Berechtigung), es wurde nichts angelegt.")
    assert f"es gibt den Pool {POOL} nicht" in result.error and "Verbinde Proxmox einmal neu mit Passwort" in result.error
    assert state["guest_configs"] == {} and state["puts"] == []
    assert _pending(tmp_path) == {}
    [note] = executor._ctx.notify.sent
    assert note.title == "Wiederherstellung abgelehnt: VM 900 „web-kopie“" and note.severity.value == "warning"


@pytest.mark.asyncio
async def test_options_tell_the_dialog_about_the_pool_and_block_it_when_the_pool_is_missing(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    params = {"connection": "pve", "volid": QEMU_VOLID}
    options = (await client.get("/api/v1/ext/backups/restore/options", params=params, headers=_auth(token))).json()
    assert options["pool"] == POOL and options["blocked"] is None
    assert "Mitglieder" in options["keep_hint"] and "„Entfernen“" in options["keep_hint"]

    state["missing_rights"] = {POOL_PATH: ["VM.Allocate"]}
    options = (await client.get("/api/v1/ext/backups/restore/options", params=params, headers=_auth(token))).json()
    assert f"Für Kopien braucht Nodvard Deck den Pool {POOL}" in options["blocked"]
    assert _writes(state) == []


@pytest.mark.asyncio
async def test_pool_sentence_is_in_the_success_message_the_watch_message_and_the_note(client, db_session, test_settings, proxmox, monkeypatch, tmp_path):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    action = await _propose_and_approve(client, token, QEMU_BODY)
    sentence = "in Proxmox unter Rechenzentrum → Rechte → Pools → nodvard-kopien → Mitglieder die Kopie auswählen und „Entfernen“ klicken"
    assert action["status"] == "succeeded"
    assert sentence in action["result"]["output"]
    assert sentence in state["puts"][0][2]["description"]  # die Notiz der Kopie

    # Und in der Meldung des Waechters, wenn der Nachbereiter die Kopie fertig einrichtet.
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    monkeypatch.setattr(restore, "RESTORE_TIMEOUT_S", 0.0)
    state["task_script"] = [{"status": "running"}]
    await executor.execute(_request({**QEMU_PAYLOAD, "vmid": 902}))
    state["task_script"] = [{"status": "stopped", "exitstatus": "OK"}]
    state["task_calls"] = 0
    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {"finished": 1}
    [note] = executor._ctx.notify.sent
    assert sentence in note.body


# --- Genau eine Meldung am Ende, in jedem Weg ------------------------------------------------------------


def _titles(executor) -> list[str]:
    return [n.title for n in executor._ctx.notify.sent]


def test_time_limit_stays_at_45_minutes_below_the_gates_emergency_brake():
    restore = _restore_unit()
    from nodvard_deck.core.gate import EXECUTE_TIMEOUT_S

    assert restore.RESTORE_TIMEOUT_S == 45 * 60.0
    assert restore.RESTORE_TIMEOUT_S < EXECUTE_TIMEOUT_S


@pytest.mark.asyncio
async def test_a_restore_that_finishes_in_the_action_ends_with_exactly_one_message_and_no_leftover_note(proxmox, tmp_path):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    result = await executor.execute(_request(QEMU_PAYLOAD))
    assert result.success, result.error
    assert _titles(executor) == ["Kopie fertig eingerichtet: VM 900 „web-kopie“"]
    assert executor._ctx.notify.sent[0].severity.value == "info"
    assert _pending(tmp_path) == {}
    # Der Waechter findet nichts mehr zu tun und meldet nichts ein zweites Mal.
    assert await _restore_module().follow_up_open_copies(executor._ctx, executor._connectors_factory) == {}
    assert len(executor._ctx.notify.sent) == 1


@pytest.mark.asyncio
async def test_a_proxmox_error_in_the_action_ends_with_exactly_one_message(proxmox, tmp_path):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    state["task_script"] = [{"status": "stopped", "exitstatus": "unable to restore VM 900 - no space left"}]
    result = await executor.execute(_request(QEMU_PAYLOAD))
    assert not result.success
    assert _titles(executor) == ["Wiederherstellung gescheitert: VM 900 „web-kopie“"]
    [note] = executor._ctx.notify.sent
    assert note.severity.value == "warning" and "no space left" in note.body and "selbst löschen" in note.body
    assert _pending(tmp_path) == {}
    assert await _restore_module().follow_up_open_copies(executor._ctx, executor._connectors_factory) == {}
    assert len(executor._ctx.notify.sent) == 1


@pytest.mark.asyncio
async def test_a_copy_that_cannot_be_finished_for_good_ends_with_exactly_one_message(proxmox, tmp_path):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    state["deny"] = [("PUT", "/nodes/pve1/qemu/900/config")]  # 403: ein weiterer Versuch waere sinnlos
    result = await executor.execute(_request(QEMU_PAYLOAD))
    assert not result.success and result.detail["created"] is True
    assert _titles(executor) == ["Kopie nicht fertig eingerichtet: VM 900 „web-kopie“"]
    assert _pending(tmp_path) == {}
    assert await _restore_module().follow_up_open_copies(executor._ctx, executor._connectors_factory) == {}
    assert len(executor._ctx.notify.sent) == 1


@pytest.mark.asyncio
async def test_a_copy_that_is_only_late_is_reported_once_by_the_watch_and_not_by_the_action(proxmox, tmp_path, monkeypatch):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    original = restore.finish_copy

    async def failing_once(connector, target):
        return "Proxmox meldet einen Fehler", False  # kein 403: ein weiterer Versuch lohnt sich

    monkeypatch.setattr(restore, "finish_copy", failing_once)
    result = await executor.execute(_request(QEMU_PAYLOAD))
    assert not result.success and "versucht es weiter" in result.error
    assert executor._ctx.notify.sent == [] and len(_pending(tmp_path)) == 1

    monkeypatch.setattr(restore, "finish_copy", original)
    for _ in range(3):
        await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory)
    assert _titles(executor) == ["Kopie fertig eingerichtet: VM 900 „web-kopie“"] and _pending(tmp_path) == {}


@pytest.mark.asyncio
async def test_a_refusal_by_proxmox_ends_with_exactly_one_message_but_a_failed_check_creates_no_record(proxmox, tmp_path):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    state["deny"] = [("POST", "/nodes/pve1/qemu")]
    result = await executor.execute(_request(QEMU_PAYLOAD))
    assert not result.success
    assert _titles(executor) == ["Wiederherstellung abgelehnt: VM 900 „web-kopie“"] and _pending(tmp_path) == {}

    # Scheitert schon die Pruefung (Nummer vergeben), hat Proxmox nie angefangen: kein Vermerk, keine Meldung.
    state["deny"] = []
    result = await executor.execute(_request({**QEMU_PAYLOAD, "vmid": 103}))
    assert not result.success and "schon vergeben" in result.error
    assert len(executor._ctx.notify.sent) == 1 and _pending(tmp_path) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("script", [[{"status": "running"}], [{}]])
async def test_time_limit_and_lost_connection_end_with_exactly_one_message_from_the_watch(proxmox, tmp_path, monkeypatch, script):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    monkeypatch.setattr(restore, "RESTORE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(restore, "POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(restore, "MAX_POLL_ERRORS", 3)
    state["task_script"] = script
    result = await executor.execute(_request(QEMU_PAYLOAD))
    assert not result.success
    assert executor._ctx.notify.sent == [] and len(_pending(tmp_path)) == 1  # noch nichts gemeldet: der Waechter ist dran

    state["task_script"] = [{"status": "stopped", "exitstatus": "OK"}]
    state["task_calls"] = 0
    for _ in range(3):  # jede Runde des Waechters
        await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory)
    assert _titles(executor) == ["Kopie fertig eingerichtet: VM 900 „web-kopie“"] and _pending(tmp_path) == {}


@pytest.mark.asyncio
async def test_after_a_dashboard_restart_the_watch_reports_exactly_once_even_when_cleaning_up_fails(proxmox, tmp_path, monkeypatch):
    """Der Datenordner laesst sich nicht mehr beschreiben: der Vermerk bleibt liegen, die Meldung kommt trotzdem nur einmal
    -- in dieser Sitzung (Speicher) und, wenn die Datei den Stand traegt, auch nach einem Neustart."""
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    monkeypatch.setattr(restore, "RESTORE_TIMEOUT_S", 0.0)
    state["task_script"] = [{"status": "running"}]
    await executor.execute(_request(QEMU_PAYLOAD))
    state["task_script"] = [{"status": "stopped", "exitstatus": "OK"}]
    state["task_calls"] = 0

    real_save = restore.pending._save

    def read_only(ctx, data):
        raise OSError("read-only")

    monkeypatch.setattr(restore.pending, "_save", read_only)
    for _ in range(3):
        await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory)
    assert len(executor._ctx.notify.sent) == 1 and len(_pending(tmp_path)) == 1

    monkeypatch.setattr(restore.pending, "_save", real_save)
    await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory)
    assert len(executor._ctx.notify.sent) == 1 and _pending(tmp_path) == {}


@pytest.mark.asyncio
async def test_the_mark_in_the_note_keeps_a_restarted_dashboard_from_reporting_twice(proxmox, tmp_path, monkeypatch):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    monkeypatch.setattr(restore, "RESTORE_TIMEOUT_S", 0.0)
    state["task_script"] = [{"status": "running"}]
    await executor.execute(_request(QEMU_PAYLOAD))
    state["task_script"] = [{"status": "stopped", "exitstatus": "OK"}]
    state["task_calls"] = 0

    monkeypatch.setattr(restore.pending, "forget", lambda ctx, key: False)  # Wegraeumen scheitert, die Markierung ist da
    await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory)
    assert len(executor._ctx.notify.sent) == 1
    [record] = _pending(tmp_path).values()
    assert record["announced"] == "finished"

    restore.pending.ANNOUNCED.clear()  # so sieht ein neu gestartetes Dashboard aus
    monkeypatch.undo()
    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {"finished": 1}
    assert len(executor._ctx.notify.sent) == 1 and _pending(tmp_path) == {}


@pytest.mark.asyncio
async def test_a_message_that_could_not_be_sent_is_tried_again_by_the_watch_and_comes_once(proxmox, tmp_path, monkeypatch):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    real_send = executor._ctx.notify.send
    attempts: list[int] = []

    async def flaky(notification):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("Datenbank gesperrt")
        await real_send(notification)

    executor._ctx.notify.send = flaky
    result = await executor.execute(_request(QEMU_PAYLOAD))
    assert result.success and executor._ctx.notify.sent == [] and len(_pending(tmp_path)) == 1  # Vermerk bleibt

    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {"finished": 1}
    assert _titles(executor) == ["Kopie fertig eingerichtet: VM 900 „web-kopie“"] and _pending(tmp_path) == {}
    await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory)
    assert len(executor._ctx.notify.sent) == 1


@pytest.mark.asyncio
async def test_giving_up_after_a_day_is_one_message_too(proxmox, tmp_path, monkeypatch):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    monkeypatch.setattr(restore, "RESTORE_TIMEOUT_S", 0.0)
    state["task_script"] = [{"status": "running"}]
    await executor.execute(_request(QEMU_PAYLOAD))
    [record] = _pending(tmp_path).values()
    later = record["created_at"] + 25 * 3600
    for _ in range(3):
        await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory, now=later)
    assert _titles(executor) == ["Kopie nicht fertig eingerichtet: VM 900 „web-kopie“"] and _pending(tmp_path) == {}


# --- Proxmox-Version --------------------------------------------------------------------------------------


UPDATES_SENTENCE = "Spiel die Updates ein (Shield → Updates), dann geht es."
MAJOR_UPGRADE_SENTENCE = (
    "Dafür ist ein großes Update auf eine neue Hauptversion von Proxmox VE nötig; die normalen Updates (Shield → Updates) "
    "schaffen das nicht. Proxmox beschreibt den Weg in seiner Upgrade-Anleitung im Proxmox-Wiki."
)
OLD_VM_SENTENCE = "Eine VM als Kopie wiederherstellen braucht Proxmox VE 7.2 oder neuer. Dein Server hat {version}. {fix}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "fix"),
    [("7.1-12", UPDATES_SENTENCE), ("7.0-14", UPDATES_SENTENCE), ("6.4-13", MAJOR_UPGRADE_SENTENCE), ("5.4-15", MAJOR_UPGRADE_SENTENCE)],
)
async def test_a_vm_copy_needs_proxmox_7_2_and_nothing_is_created_on_older_servers(
    client, db_session, test_settings, proxmox, version, fix
):
    """Bis 7.1 holen die normalen Updates die Version ein; VE 6 und älter brauchen ein großes Update auf eine neue Hauptversion --
    das spielt die Update-Zentrale nicht ein, und der Satz sagt es ehrlich."""
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["version"] = version

    res = await client.post("/api/v1/ext/backups/restore", json=QEMU_BODY, headers=_auth(token))
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == OLD_VM_SENTENCE.format(version=version, fix=fix)
    assert (await db_session.execute(select(Action).where(Action.action_type == "backup.restore_copy"))).scalars().all() == []
    assert _writes(state) == []

    options = (await client.get("/api/v1/ext/backups/restore/options", params={"connection": "pve", "volid": QEMU_VOLID}, headers=_auth(token))).json()
    assert options["blocked"] == OLD_VM_SENTENCE.format(version=version, fix=fix)  # der Dialog sperrt mit demselben Satz


@pytest.mark.asyncio
async def test_the_version_is_checked_again_when_the_action_runs(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    proposal = await _propose(client, token, QEMU_BODY)
    state["version"] = "7.1-12"
    action = (await client.post(f"/api/v1/actions/{proposal['action_id']}/approve", headers=_auth(token))).json()
    assert action["status"] == "failed" and OLD_VM_SENTENCE.format(version="7.1-12", fix=UPDATES_SENTENCE) in action["result"]["error"]
    assert state["created"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["7.2-3", "7.4-3", "8.2.4", "9.0.3"])
async def test_a_vm_copy_works_from_proxmox_7_2(client, db_session, test_settings, proxmox, version):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["version"] = version
    assert (await _propose_and_approve(client, token, QEMU_BODY))["status"] == "succeeded"


@pytest.mark.asyncio
async def test_a_container_copy_still_works_on_proxmox_7_1(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["version"] = "7.1-12"
    options = (await client.get("/api/v1/ext/backups/restore/options", params={"connection": "pve", "volid": LXC_VOLID}, headers=_auth(token))).json()
    assert options["blocked"] is None
    action = await _propose_and_approve(client, token, LXC_BODY)
    assert action["status"] == "succeeded", action
    assert state["created"][0][2]["pool"] == POOL


@pytest.mark.asyncio
async def test_before_proxmox_7_1_a_container_in_a_pool_needs_more_rights_than_we_give(client, db_session, test_settings, proxmox):
    """pve-container verlangte bis 4.1-4 beim Anlegen in einem Pool Permissions.Modify oder Pool.Allocate auf dem Pool."""
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)

    state["version"] = "7.0-14"  # dieselbe Hauptversion: die normalen Updates holen es ein
    res = await client.post("/api/v1/ext/backups/restore", json=LXC_BODY, headers=_auth(token))
    assert res.status_code == 409
    assert "Permissions.Modify oder Pool.Allocate auf dem Pool nodvard-kopien" in res.json()["detail"]
    assert UPDATES_SENTENCE in res.json()["detail"] and MAJOR_UPGRADE_SENTENCE not in res.json()["detail"]
    assert _writes(state) == []

    state["version"] = "6.4-13"  # erst eine neue Hauptversion bringt es
    res = await client.post("/api/v1/ext/backups/restore", json=LXC_BODY, headers=_auth(token))
    assert res.status_code == 409
    assert "Permissions.Modify oder Pool.Allocate auf dem Pool nodvard-kopien" in res.json()["detail"]
    assert MAJOR_UPGRADE_SENTENCE in res.json()["detail"] and "Spiel die Updates ein" not in res.json()["detail"]
    assert _writes(state) == []

    state["extra_rights"] = {POOL_PATH: ["Pool.Allocate"]}  # wer es vergeben hat, darf
    assert (await _propose_and_approve(client, token, LXC_BODY))["status"] == "succeeded"


@pytest.mark.asyncio
async def test_a_container_copy_needs_proxmox_6_for_new_mac_addresses(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["version"] = "5.4-15"
    res = await client.post("/api/v1/ext/backups/restore", json=LXC_BODY, headers=_auth(token))
    assert res.status_code == 409
    assert "Einen Container als Kopie wiederherstellen braucht Proxmox VE 6 oder neuer" in res.json()["detail"]
    assert "Dein Server hat 5.4-15" in res.json()["detail"]
    assert MAJOR_UPGRADE_SENTENCE in res.json()["detail"] and "Spiel die Updates ein" not in res.json()["detail"]
    assert _writes(state) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("unreadable", [{"version_status": 403}, {"version": "unbekannt"}, {"version": ""}])
async def test_unreadable_version_goes_on_with_a_note_and_proxmox_checks_itself(client, db_session, test_settings, proxmox, unreadable):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state.update(unreadable)

    proposal = await _propose(client, token, QEMU_BODY)
    assert any("Die Proxmox-Version ließ sich nicht lesen" in w and "7.2" in w for w in proposal["warnings"])
    options = (await client.get("/api/v1/ext/backups/restore/options", params={"connection": "pve", "volid": QEMU_VOLID}, headers=_auth(token))).json()
    assert options["blocked"] is None and any("Version ließ sich nicht lesen" in w for w in options["warnings"])
    action = (await client.post(f"/api/v1/actions/{proposal['action_id']}/approve", headers=_auth(token))).json()
    assert action["status"] == "succeeded", action


def test_versions_are_read_from_both_proxmox_formats_and_shown_without_foreign_text():
    restore = _restore_unit()
    assert restore.parse_version("8.2.4") == (8, 2) and restore.parse_version("7.4-3") == (7, 4)
    assert restore.parse_version("9.0.3 <b>") == (9, 0)
    assert restore.parse_version("") is None and restore.parse_version("x") is None and restore.parse_version(None) is None
    assert restore.parse_version("7.10-1") > restore.parse_version("7.2-1")  # Zahlen, nicht Text


# --- Ein veränderter Vermerk darf nie einen fremden Gast verändern ---------------------------------------


GOOD_UPID = "UPID:pve1:0000ABCD:00C0FFEE:67000000:qmrestore:900:nodvard@pve!dashboard:"


def _record(**changes) -> dict:
    """Ein Vermerk, so wie Nodvard Deck ihn schreibt (pending.py)."""
    base = {
        "connection": "pve", "node": "pve1", "kind": "qemu", "vmid": 900, "name": "web-kopie", "link_down": True,
        "note": "Notiz", "source_text": "VM 101 (web)", "created_at": time.time() - 60, "upid": GOOD_UPID, "announced": None,
    }
    return {**base, **changes}


@pytest.mark.asyncio
async def test_a_tampered_record_with_a_foreign_task_never_touches_a_foreign_guest(proxmox, tmp_path, caplog):
    """Der Vermerk nennt einen vorhandenen, fremden Gast und eine fertige Aufgabe, die keine Wiederherstellung dieser Nummer
    ist (hier eine Sicherung): Der Wächter richtet den fremden Gast nicht ein (Netzwerkkarten, Name, Notiz) und fasst ihn nicht an."""
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    state["guest_configs"][("qemu", 103)] = {"name": "db", "net0": "virtio=BC:24:11:00:00:09,bridge=vmbr0", "onboot": "1", "digest": "d9"}
    foreign = "UPID:pve2:0000ABCD:00C0FFEE:67000000:vzdump:103:root@pam:"
    record = _record(node="pve2", vmid=103, name="pwned", note="BÖSE NOTIZ", upid=foreign)
    (tmp_path / "restore-pending.json").write_text(json.dumps({"pve/pve2/103": record}))

    with caplog.at_level(logging.WARNING, logger="nodvard_deck.ext.backups.restore"):
        counts = await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory)
    assert counts == {} and state["puts"] == [] and _writes(state) == []
    assert executor._ctx.notify.sent == []
    assert _pending(tmp_path) == {}  # der Eintrag fällt weg ...
    assert "restore_pending_dropped" in caplog.text  # ... und das steht im Protokoll
    assert not [c for c in state["calls"] if "/103" in c[1]]  # auch kein lesender Aufruf auf den fremden Gast


@pytest.mark.parametrize(
    "upid",
    [
        "UPID:pve1:0000ABCD:00C0FFEE:67000000:vzdump:900:root@pam:",  # eine Sicherung, keine Wiederherstellung
        "UPID:pve1:0000ABCD:00C0FFEE:67000000:qmrestore:901:root@pam:",  # eine andere Nummer
        "UPID:pve2:0000ABCD:00C0FFEE:67000000:qmrestore:900:root@pam:",  # ein anderer Knoten
        "UPID:pve1:0000ABCD:00C0FFEE:67000000:vzrestore:900:root@pam:",  # Container statt VM
        "UPID:pve1:0000ABCD:00C0FFEE:67000000:qmrestore:900:root@pam:/../x",  # keine Aufgaben-Kennung
    ],
)
def test_a_record_is_only_taken_over_with_the_task_of_its_own_restore(upid):
    restore = _restore_unit()
    assert restore.CopyTarget.from_record(_record(upid=upid)) is None
    assert restore.CopyTarget.from_record(_record(upid=None)) is not None  # noch keine Kennung ist in Ordnung
    assert restore.CopyTarget.from_record(_record(upid=GOOD_UPID)) is not None


def test_note_and_source_text_of_a_record_are_cut_when_loading():
    restore = _restore_unit()
    target = restore.CopyTarget.from_record(_record(note="x" * 100_000, source_text="y" * 100_000))
    assert target is not None
    assert 0 < len(target.note) <= restore.NOTE_MAX and 0 < len(target.source_text) <= restore.SOURCE_TEXT_MAX
    # Was Nodvard Deck selbst schreibt, bleibt unverändert.
    ok = restore.CopyTarget.from_record(_record(note="Kopie aus der Sicherung. " + restore.POOL_KEEP_HINT))
    assert ok is not None and ok.note == "Kopie aus der Sicherung. " + restore.POOL_KEEP_HINT


@pytest.mark.parametrize(
    "change",
    [
        {"name": "a" * 64}, {"node": "p" * 64}, {"connection": "x" * 101}, {"connection": "mit Leerzeichen"},
        {"connection": "pve\n"}, {"note": ""}, {"note": "\n\t "},
    ],
)
def test_a_record_with_oversized_or_odd_fields_is_dropped(change):
    restore = _restore_unit()
    assert restore.CopyTarget.from_record(_record(**change)) is None


@pytest.mark.parametrize("created_at", [float("nan"), float("inf"), -float("inf"), time.time() + 10 * 86400, 10**400])
def test_a_record_needs_a_finite_time_that_is_not_in_the_future(created_at):
    """Eine Zeit in der Zukunft hielte den Vermerk für immer am Leben (die Frist von 24 Stunden liefe nie ab)."""
    restore = _restore_unit()
    assert restore.CopyTarget.from_record(_record(created_at=created_at)) is None
    assert restore.CopyTarget.from_record(_record(created_at=time.time() - 3 * 86400)) is not None  # alt ist in Ordnung


@pytest.mark.asyncio
@pytest.mark.parametrize("pool", [None, "anderer-pool"])
async def test_the_watch_sets_up_only_a_guest_that_is_in_the_copy_pool(proxmox, tmp_path, pool):
    """Die Aufgabe passt zur Nummer, aber der Gast gehört nicht in den Pool der Kopien (ein fremder Gast, oder die Kopie ist
    aus dem Pool genommen): nichts wird verändert, eine Meldung, der Vermerk fällt weg."""
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    state["guest_configs"][("qemu", 103)] = {"name": "db", "net0": "virtio=BC:24:11:00:00:09,bridge=vmbr0", "onboot": "1", "digest": "d9"}
    if pool:
        next(r for r in state["resources"] if r["vmid"] == 103)["pool"] = pool
    upid = "UPID:pve2:0000ABCD:00C0FFEE:67000000:qmrestore:103:nodvard@pve!dashboard:"
    restore.pending.remember(executor._ctx, "pve/pve2/103", _record(node="pve2", vmid=103, name="db-kopie", upid=upid))

    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {"not_found": 1}
    assert state["puts"] == [] and _writes(state) == []
    [note] = executor._ctx.notify.sent
    assert note.title == "Kopie nicht gefunden: VM 103 „db-kopie“" and note.severity.value == "warning"
    assert f"im Pool {POOL}" in note.body and "nichts verändert" in note.body and "Das Original ist unberührt" in note.body
    assert _pending(tmp_path) == {}
    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {}
    assert len(executor._ctx.notify.sent) == 1


@pytest.mark.asyncio
async def test_the_watch_still_sets_up_a_copy_in_the_pool(proxmox, tmp_path, monkeypatch):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    monkeypatch.setattr(restore, "RESTORE_TIMEOUT_S", 0.0)
    state["task_script"] = [{"status": "running"}]
    await executor.execute(_request(QEMU_PAYLOAD))
    assert next(r for r in state["resources"] if r["vmid"] == 900)["pool"] == POOL
    state["task_script"] = [{"status": "stopped", "exitstatus": "OK"}]
    state["task_calls"] = 0
    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {"finished": 1}
    assert state["puts"][0][1] == 900


# --- Rechte: bekannt Fehlendes zählt, Namen aus der Sicherung gehen nie in einen Pfad -----------------------


@pytest.mark.asyncio
async def test_a_right_that_is_known_to_be_missing_still_blocks_when_a_later_permission_read_fails(
    client, db_session, test_settings, proxmox
):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["missing_rights"] = {POOL_PATH: ["VM.Config.Network"]}
    state["unreadable_permission_paths"] = ["/storage/local-lvm"]

    res = await client.post("/api/v1/ext/backups/restore", json=QEMU_BODY, headers=_auth(token))
    assert res.status_code == 409, res.text
    assert f"Dem Zugang fehlt ein Recht: VM.Config.Network auf dem Pool „{POOL}“" in res.json()["detail"]
    assert _writes(state) == []

    # Fehlt nichts, was sich lesen ließ, bleibt es bei dem Hinweis "nicht prüfbar".
    state["missing_rights"] = {}
    proposal = await _propose(client, token, QEMU_BODY)
    assert any("ließen sich vorab nicht prüfen" in w for w in proposal["warnings"])


def _asked_paths(state: dict) -> list[str]:
    return [q["path"] for _m, p, q in state["calls"] if p == "/access/permissions"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bridge",
    ["../../x", "vmbr0/../..", "vm br", "vm\tbr", "vmbr0%2F..", "..", "a" * 80],
)
async def test_a_bridge_name_from_the_backup_never_reaches_the_permission_path(client, db_session, test_settings, proxmox, bridge):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["backup_configs"][QEMU_VOLID] = QEMU_CONFIG.replace("bridge=vmbr1", f"bridge={bridge}")

    proposal = await _propose(client, token, QEMU_BODY)
    asked = _asked_paths(state)
    assert "/sdn/zones/localnetwork/vmbr0" in asked  # die gültige Bridge wird weiter geprüft
    assert not [a for a in asked if bridge in a or ".." in a or "%" in a or " " in a or "\n" in a]
    assert [w for w in proposal["warnings"] if "ungültigen Namen" in w and "SDN.Use" in w and "nicht prüfen" in w]
    assert bridge not in " ".join(proposal["warnings"])  # der fremde Name steht auch in keinem Hinweis


@pytest.mark.asyncio
async def test_a_vlan_tag_from_the_backup_must_be_a_number_before_it_goes_into_a_path(client, db_session, test_settings, proxmox):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["backup_configs"][QEMU_VOLID] = QEMU_CONFIG.replace("bridge=vmbr1", "bridge=vmbr1,tag=5").replace(
        "bridge=vmbr0,", "bridge=vmbr0,tag=1/../..,"
    )
    proposal = await _propose(client, token, QEMU_BODY)
    asked = _asked_paths(state)
    assert "/sdn/zones/localnetwork/vmbr1/5" in asked
    assert not [a for a in asked if ".." in a]
    assert any("ungültigen Namen" in w for w in proposal["warnings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mapping", ["../../..", "gpu/../../..", "a b", "x" * 80, ".."])
async def test_a_mapping_name_from_the_backup_never_reaches_the_permission_path(client, db_session, test_settings, proxmox, mapping):
    base_url, state = proxmox
    token = await _setup(client, db_session, test_settings, base_url)
    state["backup_configs"][QEMU_VOLID] = QEMU_CONFIG + f"hostpci0: mapping={mapping},pcie=1\nvirtiofs0: dirid={mapping}\n"

    options = (await client.get("/api/v1/ext/backups/restore/options", params={"connection": "pve", "volid": QEMU_VOLID}, headers=_auth(token))).json()
    proposal = await _propose(client, token, QEMU_BODY)
    assert not [a for a in _asked_paths(state) if a.startswith("/mapping/")]
    for warnings in (options["warnings"], proposal["warnings"]):
        assert any("hostpci0" in w and "ungültigem Namen" in w and "Mapping.Use" in w for w in warnings)
        assert any("virtiofs0" in w and "ungültigem Namen" in w for w in warnings)
        assert mapping not in " ".join(warnings)


# --- Der Wächter nimmt nichts doppelt, und ein kaputter Vermerk blockiert die anderen nicht --------------


@pytest.mark.asyncio
async def test_a_copy_that_the_action_concluded_meanwhile_is_not_reported_a_second_time_by_the_watch(proxmox, tmp_path, monkeypatch):
    """Der Wächter hat die Vermerke geladen und wartet (etwa auf die Verbindungen); inzwischen schließt die Aktion die Kopie
    ab (Meldung, Vermerk weg). Mit dem alten Stand von vorhin hätte der Wächter ein zweites Mal gemeldet."""
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    monkeypatch.setattr(restore, "RESTORE_TIMEOUT_S", 0.0)
    state["task_script"] = [{"status": "running"}]
    await executor.execute(_request(QEMU_PAYLOAD))  # Vermerk bleibt, noch nichts gemeldet
    state["task_script"] = [{"status": "stopped", "exitstatus": "OK"}]
    state["task_calls"] = 0
    [(_key, record)] = restore.pending.load(executor._ctx).items()
    target = restore.CopyTarget.from_record(record)
    real_factory = executor._connectors_factory

    async def factory():
        title, body = restore.finished_message(target)
        await restore._conclude(executor._ctx, target, "finished", title, body, success=True)
        return await real_factory()

    assert await restore.follow_up_open_copies(executor._ctx, factory) == {}
    assert _titles(executor) == ["Kopie fertig eingerichtet: VM 900 „web-kopie“"] and _pending(tmp_path) == {}


@pytest.mark.asyncio
async def test_a_record_that_fails_every_round_does_not_block_the_others_and_expires_after_the_time_limit(proxmox, tmp_path, monkeypatch, caplog):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    monkeypatch.setattr(restore, "RESTORE_TIMEOUT_S", 0.0)
    broken = restore.CopyTarget(
        connection="pve", node="pve1", kind="qemu", vmid=800, name="kaputt-kopie", link_down=True, note="n",
        source_text="VM 101", created_at=time.time() - 60,
    )
    restore.pending.remember(executor._ctx, broken.key, broken.record())  # sortiert vor der guten Kopie (900)
    state["task_script"] = [{"status": "running"}]
    await executor.execute(_request(QEMU_PAYLOAD))
    state["task_script"] = [{"status": "stopped", "exitstatus": "OK"}]
    state["task_calls"] = 0
    assert list(_pending(tmp_path)) == [broken.key, "pve/pve1/900"]

    real_find = restore._find_restore_task

    async def find(connector, target):
        if target.vmid == 800:
            raise RuntimeError("unerwartete Antwort")
        return await real_find(connector, target)

    monkeypatch.setattr(restore, "_find_restore_task", find)
    with caplog.at_level(logging.ERROR, logger="nodvard_deck.ext.backups.restore"):
        counts = await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory)
    assert counts == {"finished": 1}  # die gute Kopie kommt trotzdem dran
    assert "restore_followup_failed" in caplog.text
    assert list(_pending(tmp_path)) == [broken.key] and _titles(executor) == ["Kopie fertig eingerichtet: VM 900 „web-kopie“"]

    # Nach der Frist läuft der kaputte Vermerk ab: eine Meldung, der Vermerk fällt weg.
    later = broken.created_at + 25 * 3600
    for _ in range(2):
        await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory, now=later)
    assert _titles(executor)[1:] == ["Kopie nicht fertig eingerichtet: VM 800 „kaputt-kopie“"]
    assert _pending(tmp_path) == {}


@pytest.mark.asyncio
async def test_a_data_folder_that_cannot_be_written_is_not_reported_as_proxmox_not_answering(proxmox, tmp_path, monkeypatch):
    """Die Aufgabe wurde über die Aufgabenliste gefunden, nur die Kennung ließ sich nicht abspeichern: Der Wächter macht mit der
    Kennung im Speicher weiter und meldet nicht "Proxmox antwortet nicht" (auch nicht nach 24 Stunden)."""
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    state["answer_after_create"] = 502
    result = await executor.execute(_request(QEMU_PAYLOAD))  # Aufgabe da, Kennung kam nie an
    assert not result.success and _pending(tmp_path)["pve/pve1/900"]["upid"] is None

    def read_only(ctx, data):
        raise OSError("read-only")

    monkeypatch.setattr(restore.pending, "_save", read_only)
    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {"finished": 1}
    assert _titles(executor) == ["Kopie fertig eingerichtet: VM 900 „web-kopie“"]
    assert all("antwortet nicht" not in n.body for n in executor._ctx.notify.sent)

    # Auch nach einem Tag, wenn es nie klappt: der Grund ist nicht "Proxmox antwortet nicht".
    executor2 = _executor(base_url, str(tmp_path))
    old = restore.CopyTarget(
        connection="pve", node="pve1", kind="qemu", vmid=901, name="x-kopie", link_down=True, note="n", source_text="VM 101",
        created_at=time.time() - 25 * 3600,
    )
    monkeypatch.undo()
    restore.pending.remember(executor2._ctx, old.key, old.record())
    state["task_index"].append({
        "upid": "UPID:pve1:0000ABCD:00C0FFEE:67000000:qmrestore:901:nodvard@pve!dashboard:", "node": "pve1", "type": "qmrestore",
        "id": "901", "starttime": int(time.time()) - 25 * 3600,
    })
    state["resources"].append({"vmid": 901, "node": "pve1", "type": "qemu", "name": "x-kopie", "pool": POOL})
    state["guest_configs"][("qemu", 901)] = {"name": "x-kopie", "net0": "virtio=X,bridge=vmbr0", "onboot": "0", "digest": "d1"}
    monkeypatch.setattr(restore.pending, "_save", read_only)
    await restore.follow_up_open_copies(executor2._ctx, executor2._connectors_factory)
    assert all("antwortet nicht" not in n.body for n in executor2._ctx.notify.sent)


# --- Zwei Läufe zur selben Nummer ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_second_run_for_a_number_that_is_already_being_restored_stops_and_leaves_the_first_one_alone(proxmox, tmp_path):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    first = restore.CopyTarget(
        connection="pve", node="pve1", kind="qemu", vmid=900, name="web-kopie", link_down=True, note="n", source_text="VM 101",
        created_at=time.time(),
    )
    restore.pending.remember(executor._ctx, first.key, first.record())
    restore.pending.ACTIVE.add(first.key)  # der erste Lauf ist gerade dabei
    try:
        result = await executor.execute(_request(QEMU_PAYLOAD))
        assert not result.success
        assert "Für diese Nummer läuft schon eine Wiederherstellung." in result.error
        assert "Es wurde nichts angelegt, das Original ist unberührt." in result.error
        assert first.key in restore.pending.ACTIVE  # der zweite Lauf hat den ersten nicht aus der Liste genommen
        assert _pending(tmp_path) == {first.key: first.record()}  # und seinen Vermerk nicht angefasst
        assert state["created"] == [] and _writes(state) == [] and executor._ctx.notify.sent == []
    finally:
        restore.pending.ACTIVE.discard(first.key)


@pytest.mark.asyncio
async def test_two_runs_at_the_same_time_for_the_same_number_create_one_copy_and_send_one_message(proxmox, tmp_path, monkeypatch):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    monkeypatch.setattr(restore, "POLL_INTERVAL_S", 0.05)
    state["task_script"] = [{"status": "running"}, {"status": "running"}, {"status": "stopped", "exitstatus": "OK"}]

    first, second = await asyncio.gather(executor.execute(_request(QEMU_PAYLOAD)), executor.execute(_request(QEMU_PAYLOAD)))
    assert sorted([first.success, second.success]) == [False, True]
    failed = first if not first.success else second
    assert "Für diese Nummer läuft schon eine Wiederherstellung." in failed.error
    assert len(state["created"]) == 1 and [w for w in _writes(state) if w[0] == "POST"] == [("POST", "/nodes/pve1/qemu")]
    assert _titles(executor) == ["Kopie fertig eingerichtet: VM 900 „web-kopie“"]
    assert restore.pending.ACTIVE == set() and _pending(tmp_path) == {}


# --- Versionen ------------------------------------------------------------------------------------------


def test_versions_with_a_flood_of_digits_or_foreign_digits_are_not_versions():
    restore = _restore_unit()
    assert restore.parse_version("9" * 5000 + ".1") is None  # sonst ValueError beim Umwandeln der Ziffernflut
    assert restore.parse_version("1." + "9" * 5000) is None
    assert restore.parse_version("12345.1") is None and restore.parse_version("7.12345") is None
    assert restore.parse_version("٨.٠") is None  # arabisch-indische Ziffern sind keine Version
    assert restore.parse_version("８.２") is None  # Ziffern in voller Breite auch nicht
    assert restore.parse_version("8.2.4") == (8, 2) and restore.parse_version(" 7.4-3") == (7, 4)
    assert restore.parse_version("0007.0002") == (7, 2) and restore.parse_version("9999.9999") == (9999, 9999)


# --- Namen aus der Sicherung ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_long_name_in_the_backup_is_cut_in_the_message_the_note_and_the_record(proxmox, tmp_path):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    long_name = "A" * 20000
    state["backup_configs"][QEMU_VOLID] = state["backup_configs"][QEMU_VOLID].replace("name: web", f"name: {long_name}")

    result = await executor.execute(_request(QEMU_PAYLOAD))
    assert result.success, result.error
    description = state["puts"][0][2]["description"]
    [note] = executor._ctx.notify.sent
    for text in (description, note.body, result.output):
        assert "A" * 64 not in text and "A" * 62 + "…" in text
    assert len(description.encode()) < 1200 and len(note.body) < 1200 and len(result.output) < 1500
    assert description.endswith("\n\nWebserver im Keller") and "Entfernen" in description  # der Hinweis am Ende bleibt

    # Dasselbe im Dialog.
    restore = _restore_unit()
    info = restore.describe_config("qemu", {"name": "B" * 500, "net0": "virtio=X,bridge=vmbr0"})
    assert info.name == "B" * 62 + "…" and len(info.name) == 63
    assert restore.describe_config("lxc", {"hostname": "\n\t "}).name is None


# --- Texte ------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_outcome_of_a_failed_task_is_shown_like_proxmox_reasons_in_other_errors(proxmox, tmp_path):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    state["task_script"] = [{"status": "stopped", "exitstatus": "unable to restore VM 900 - no space left"}]
    result = await executor.execute(_request(QEMU_PAYLOAD))
    [note] = executor._ctx.notify.sent
    assert "Proxmox konnte die Sicherung nicht wiederherstellen. Grund laut Proxmox: unable to restore VM 900 - no space left. " in note.body
    assert result.error == note.body

    # HTML und Adressen mit Schema stammen nicht von Proxmox' Fehlertext: Es bleibt der feste Satz.
    for bad in ("<b>kaputt</b>", "siehe https://evil.example/login", "x" * 5 + "<script>"):
        executor = _executor(base_url, str(tmp_path / "b"))
        state["task_script"] = [{"status": "stopped", "exitstatus": bad}]
        state["resources"] = [r for r in state["resources"] if r["vmid"] != 900]
        result = await executor.execute(_request(QEMU_PAYLOAD))
        [note] = executor._ctx.notify.sent
        assert "<" not in note.body and "://" not in note.body and bad not in note.body
        assert "nennt aber keinen Grund" in note.body and "Aufgabenprotokoll" in note.body
        assert "Grund laut Proxmox" not in note.body

    # Lang ist gekürzt, Zeilenumbrüche sind Leerzeichen.
    executor = _executor(base_url, str(tmp_path / "c"))
    state["task_script"] = [{"status": "stopped", "exitstatus": "zeile eins\nzeile zwei " + "y" * 500}]
    state["resources"] = [r for r in state["resources"] if r["vmid"] != 900]
    await executor.execute(_request(QEMU_PAYLOAD))
    [note] = executor._ctx.notify.sent
    assert "Grund laut Proxmox: zeile eins zeile zwei yyy" in note.body and "\n" not in note.body and len(note.body) < 700


@pytest.mark.asyncio
async def test_the_watch_shows_a_failed_task_the_same_way(proxmox, tmp_path, monkeypatch):
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    monkeypatch.setattr(restore, "RESTORE_TIMEOUT_S", 0.0)
    state["task_script"] = [{"status": "running"}]
    await executor.execute(_request(QEMU_PAYLOAD))
    state["task_script"] = [{"status": "stopped", "exitstatus": "<html>Bad Gateway</html> https://evil.example"}]
    state["task_calls"] = 0
    assert await restore.follow_up_open_copies(executor._ctx, executor._connectors_factory) == {"failed": 1}
    [note] = executor._ctx.notify.sent
    assert "<" not in note.body and "://" not in note.body and "nennt aber keinen Grund" in note.body


def test_the_menu_for_permissions_is_called_rechte_in_every_text():
    """In Proxmox' eigener deutscher Übersetzung (proxmox-i18n, de.po) heißt "Permissions" im Menü des Rechenzentrums "Rechte",
    nicht "Berechtigungen"."""
    restore = _restore_unit()
    assert "Rechenzentrum → Rechte → Pools" in restore.POOL_KEEP_HINT and "Berechtigungen" not in restore.POOL_KEEP_HINT
    docs = REPO_EXTENSIONS_DIR.parent / "docs"
    for name in ("10-PROXMOX-TOKEN.md", "en/PROXMOX-TOKEN.md"):
        text = (docs / name).read_text(encoding="utf-8")
        assert "Rechenzentrum → Berechtigungen" not in text, name
        assert "Rechenzentrum → Rechte" in text, name
        assert "**Berechtigungen**" not in text and "(„Berechtigungen“)" not in text, name


# --- Eine neue Wiederherstellung unter einer Nummer, deren alter Vermerk liegen blieb ----------------------


@pytest.mark.asyncio
async def test_a_number_that_is_used_again_gets_its_message_even_when_the_old_note_could_not_be_removed(proxmox, tmp_path, monkeypatch):
    """Meldung raus, Vermerk nicht weggeräumt (Datenordner nicht beschreibbar), Kopie in Proxmox gelöscht, dieselbe Nummer wieder
    benutzt: Der Merker "Meldung ist raus" gilt nur für die alte Kopie und muss zu Beginn des neuen Laufs weg."""
    base_url, state = proxmox
    executor = _executor(base_url, str(tmp_path))
    restore = _restore_module()
    real_forget = restore.pending.forget
    monkeypatch.setattr(restore.pending, "forget", lambda ctx, key: False)

    assert (await executor.execute(_request(QEMU_PAYLOAD))).success
    assert _titles(executor) == ["Kopie fertig eingerichtet: VM 900 „web-kopie“"]
    [record] = _pending(tmp_path).values()
    assert record["announced"] == "finished" and restore.pending.ANNOUNCED == {"pve/pve1/900": "finished"}

    # Die Kopie wird in Proxmox gelöscht; der Vermerk liegt noch.
    state["resources"] = [r for r in state["resources"] if r["vmid"] != 900]
    state["guest_configs"].pop(("qemu", 900))
    state["pools"][POOL].remove(900)
    monkeypatch.setattr(restore.pending, "forget", real_forget)

    assert (await executor.execute(_request(QEMU_PAYLOAD))).success
    assert _titles(executor) == ["Kopie fertig eingerichtet: VM 900 „web-kopie“"] * 2  # die neue Kopie meldet sich wieder
    assert _pending(tmp_path) == {} and restore.pending.ANNOUNCED == {}
