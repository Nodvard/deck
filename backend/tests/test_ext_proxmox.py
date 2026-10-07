"""proxmox-Extension -- die erste echte Vendor-Extension.
Getestet gegen einen echten, lokal laufenden Proxmox-API-Mock (echtes HTTP/
JSON auf einem echten TCP-Port, kein `respx`/Monkeypatch von `httpx`) -- dasselbe
Prinzip wie `test_ext_terminal.py` gegen einen echten Test-SSH-Server statt eines
echten Proxmox-VE-Clusters.

Nutzt bewusst die ECHTE `extensions/proxmox`-Extension (Repo-Pfad) und die `client`-
Fixture (echte App, echte Auth-/Host-/Aktions-Endpunkte) -- wie `test_extensions_api.py`
es fuer hello-world tut, statt einer isolierten `FastAPI()`-Instanz ohne Auth-Router
(die `test_extensions_service.py`s engeres Muster nur fuer Mounting-Verhalten braucht).

**Ehrlich offen gelassen:** es gibt noch keinen API-Weg, die
`connections`-Einstellung einer Extension zu setzen (`PUT .../settings` ist
explizit zurueckgestellt, siehe api/v1/extensions.py-Modul-Docstring) --
dieser Test setzt `ExtensionRecord.settings` deshalb direkt ueber die DB, wie es
andere Tests bereits fuer `provider_ext_id` etc. tun. Jedes TOKEN-Geheimnis dagegen
geht ueber den echten `POST /ext/proxmox/connections/{name}/token`-Endpunkt (Vault,
kein Direktzugriff).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from raw_http_helpers import BROKEN_ANSWERS, REDIRECT_ANSWERS, raw_http_server
from nodvard_sdk.capabilities import NotificationChannel
from sqlalchemy import select

from nodvard_deck.config import LOCAL_TIMEZONE
from nodvard_deck.db import utcnow
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import ExtensionRecord, Host
from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services import settings as settings_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
_TOKEN_ID = "root@pam!lattice"
_TOKEN_SECRET = "test-secret-uuid"
_EXPECTED_AUTH = f"PVEAPIToken={_TOKEN_ID}={_TOKEN_SECRET}"
# Echte Proxmox-VNC-Tickets enthalten `+`, `/` und `=` -- genau die Zeichen, die ohne
# vollstaendiges URL-Kodieren veraendert ankommen.
_VNC_TICKET = "PVEVNC:6512ABCD::a+b/c=d=="


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _build_mock_proxmox_app() -> tuple[FastAPI, dict]:
    """Realistisch genug, um den echten Connector-Code zu beweisen: echte JSON-
    Umschlaege (`{"data": ...}`), echte Auth-Pruefung des `PVEAPIToken`-Headers, echter
    Zustand (Start/Stop aendert wirklich `status`, ein Snapshot wird wirklich vermerkt)."""
    state = {
        "nodes": {"pve1": {"node": "pve1", "status": "online", "cpu": 0.12, "maxmem": 34_000_000_000}},
        "vms": {
            "100": {
                "vmid": 100, "name": "test-vm", "status": "running",
                "cpu": 0.05, "mem": 512_000_000, "maxmem": 2_147_483_648, "uptime": 1000,
            }
        },
        "containers": {
            "201": {
                "vmid": 201, "name": "test-lxc", "status": "running",
                "cpu": 0.03, "mem": 256_000_000, "maxmem": 1_073_741_824, "uptime": 2000,
            }
        },
        "calls": [],
        "snapshots": [],
        # Task-Abschluss-Tracking: Standard ist sofortiger Erfolg beim ersten Poll -- Tests
        # ueberschreiben das gezielt fuer den Fehlschlag-Fall.
        "task_result": {"status": "stopped", "exitstatus": "OK"},
        # Konsole: jeder vncproxy-Aufruf und jede vncwebsocket-Verbindung.
        "vnc_calls": [],
        "vnc_ws": [],
        # Gast-Adressen: vmid -> Schnittstellen (qemu: per Agent; ohne Eintrag
        # "QEMU guest agent is not running" wie bei einer VM ohne Agent).
        "guest_agent": {},
        "lxc_ifaces": {},
        # Gast-Konfiguration in der Form der echten API (VM auf pve2, Container auf pve1) --
        # MIT Cloud-Init-Passwort und SSH-Schluessel, um zu beweisen, dass sie nie
        # herauskommen.
        "guest_configs": {
            "qemu/100": {
                "name": "test-vm", "cores": 8, "sockets": 1, "cpu": "host", "memory": "5120", "balloon": 3072,
                "ostype": "l26", "bios": "ovmf", "machine": "q35", "agent": "enabled=1", "onboot": 1, "startup": "order=2",
                "scsi0": "local-lvm:vm-100-disk-0,size=540G", "efidisk0": "local:100/vm-100-disk-1.raw,efitype=4m,size=528K",
                "ide2": "local:100/vm-100-cloudinit.qcow2,media=cdrom", "ide0": "local:iso/debian-13.iso,media=cdrom,size=755M",
                "unused0": "local-lvm:vm-100-disk-9",
                "net0": "virtio=02:A4:B5:0C:07:7F,bridge=vmbr0,firewall=1", "hostpci0": "0000:01:00,pcie=1",
                "tags": "community-script;docker", "cipassword": "GEHEIM-cloudinit", "sshkeys": "ssh-ed25519%20AAAAGEHEIM",
                "description": "Notiz mit GEHEIM-Zugang", "digest": "abc",
            },
            "lxc/201": {
                "hostname": "test-lxc", "cores": 2, "memory": 4096, "swap": 1024, "ostype": "debian", "unprivileged": 1,
                "features": "nesting=1,keyctl=1", "onboot": 1,
                "rootfs": "local-lvm:vm-201-disk-0,size=25G", "mp0": "/var/lib/vz/dump,mp=/mnt/pve1-backups",
                "net0": "name=eth0,bridge=vmbr0,gw=192.168.1.1,hwaddr=BC:24:11:A2:3F:11,ip=192.168.1.65/24,type=veth",
            },
        },
        # Speicher + Snapshots, Felder wie von der echten API geliefert.
        "storage": [
            {"storage": "local-lvm", "type": "lvmthin", "content": "images,rootdir", "active": 1, "shared": 0,
             "total": 1000, "used": 950, "avail": 50},
            {"storage": "local", "type": "dir", "content": "iso,backup,images", "active": 1, "shared": 0,
             "total": 1000, "used": 400, "avail": 600},
            {"storage": "backup-pi", "type": "nfs", "content": "backup", "active": 1, "shared": 1,
             "total": 1000, "used": 100, "avail": 900},
        ],
        "storage_content": {
            "local-lvm": [{"volid": "local-lvm:vm-100-disk-0", "content": "images", "vmid": 100, "size": 500}],
            "local": [
                {"volid": "local:201/vm-201-disk-0.qcow2", "content": "images", "vmid": 201, "size": 45},
                {"volid": "local:iso/debian.iso", "content": "iso", "size": 7},
                {"volid": "local:backup/a.vma.zst", "content": "backup", "vmid": 100, "size": 13},
                {"volid": "local:backup/b.vma.zst", "content": "backup", "vmid": 100, "size": 17},
            ],
            "backup-pi": [],
        },
        "snapshot_list": {"100": [
            {"name": "vor-update", "description": "Vor dem Update\n", "snaptime": 1790000000, "vmstate": 1},
            {"name": "aelter", "snaptime": 1780000000},
            {"name": "current", "description": "You are here!", "parent": "vor-update"},
        ]},
        "snapshot_calls": [],
        "config_updates": [],
        "node_calls": [],
        "pending": {},
        # Aufgabenverlauf, Felder wie von der echten API geliefert (neueste zuerst).
        "task_list": [
            {"upid": "UPID:pve1:0001:A:1:vncproxy:100:root@pam!lattice:", "type": "vncproxy", "id": "100",
             "user": "root@pam!lattice", "status": "OK", "starttime": 3000, "endtime": 3060},
            {"upid": "UPID:pve1:0002:B:2:qmstart:100:root@pam:", "type": "qmstart", "id": "100",
             "user": "root@pam", "status": "OK", "starttime": 2000, "endtime": 2004},
            {"upid": "UPID:pve1:0003:C:3:vzdump:201:root@pam:", "type": "vzdump", "id": "201",
             "user": "root@pam", "starttime": 2500},
            {"upid": "UPID:pve1:0004:D:4:stopall::root@pam:", "type": "stopall", "id": "",
             "user": "root@pam", "status": "unexpected status", "starttime": 1000, "endtime": 1100},
            # Proxmox' naechtlicher Update-Check (pve-daily-update.timer).
            {"upid": "UPID:pve1:0009:E:9:aptupdate::root@pam:", "type": "aptupdate", "id": "",
             "user": "root@pam", "status": "OK", "starttime": 900, "endtime": 910},
        ],
        # Paket-Updates in der Form der echten API (4 Pakete, neuer Kernel dabei).
        "apt_updates": [
            {"Package": "pve-firewall", "Title": "Proxmox VE Firewall", "OldVersion": "6.0.5", "Version": "6.0.6", "Origin": "Proxmox"},
            {"Package": "proxmox-kernel-7.0", "Title": "Latest Proxmox Kernel Image", "OldVersion": "7.0.14-16", "Version": "7.0.14-19", "Origin": "Proxmox"},
            {"Package": "proxmox-kernel-7.0.14-19-pve-signed", "Title": "Proxmox Kernel Image (signed)", "Version": "7.0.14-19", "Origin": "Proxmox"},
        ],
        "apt_versions": [
            {"Package": "proxmox-ve", "Version": "9.2.0", "CurrentState": "Installed", "RunningKernel": "7.0.14-16-pve"},
            {"Package": "proxmox-kernel-7.0", "Version": "7.0.14-16", "CurrentState": "Installed"},
            {"Package": "proxmox-kernel-7.0.14-16-pve-signed", "Version": "7.0.14-16", "CurrentState": "Installed"},
            {"Package": "proxmox-kernel-7.0.14-14-pve-signed", "Version": "7.0.14-14", "CurrentState": "Installed"},
            {"Package": "proxmox-kernel-6.8.12-9-pve-signed", "Version": "6.8.12-9", "CurrentState": "Installed"},
        ],
        "running_kernel": "7.0.14-16-pve",
        # Datentraeger in der Form der echten API: NVMe (SMART als Freitext), SATA-SSD
        # (SMART als Attributliste, Abnutzung "N/A").
        "disks": [
            {"devpath": "/dev/nvme0n1", "model": "Example_NVMe_1TB", "type": "nvme", "size": 1024209543168,
             "health": "PASSED", "wearout": 97, "serial": "SERIAL-1", "used": "BIOS boot"},
            {"devpath": "/dev/sda", "model": "Example_SATA_SSD_128GB", "type": "ssd", "size": 128035676160,
             "health": "PASSED", "wearout": "N/A", "serial": "SERIAL-2", "used": "BIOS boot"},
        ],
        "smart": {
            "/dev/nvme0n1": {"health": "PASSED", "type": "text", "wearout": 97, "text": (
                "\nSMART/Health Information (NVMe Log 0x02, NSID 0xffffffff)\nCritical Warning:                   0x00\n"
                "Temperature:                        34 Celsius\nPercentage Used:                    3%\n"
                "Power On Hours:                     12,345\n"
            )},
            "/dev/sda": {"health": "PASSED", "type": "ata", "attributes": [
                {"name": "Reallocated_Sector_Ct", "raw": "0"},
                {"name": "Power_On_Hours", "raw": "706"},
                {"name": "Temperature_Celsius", "raw": "32 (Min/Max 19/48)"},
            ]},
        },
        "task_logs": {"UPID:pve1:0002:B:2:qmstart:100:root@pam:": [
            {"n": 2, "t": "TASK OK"}, {"n": 1, "t": "starting VM 100"},
        ]},
    }
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _proxmox_error(request: Request, exc: HTTPException) -> JSONResponse:
        """So meldet Proxmox einen Fehler: `{"data": null, "message": "<Grund>"}` (nicht FastAPIs `detail`)."""
        return JSONResponse({"data": None, "message": exc.detail}, status_code=exc.status_code)

    @app.middleware("http")
    async def _forced_answer(request: Request, call_next):
        """Tests setzen `state["forced"]`, um jede Anfrage mit einer festen Antwort zu beantworten
        (Weiterleitung, HTML statt JSON). `state["forced_hits"]` zaehlt die Anfragen, die ankamen."""
        forced = state.get("forced")
        if forced is None:
            return await call_next(request)
        state.setdefault("forced_hits", []).append(request.url.path)
        return Response(content=forced.get("body", ""), status_code=forced["status"], headers=forced.get("headers"),
                        media_type=forced.get("media_type"))


    def _check_auth(authorization: str | None = Header(default=None)) -> None:
        if authorization != _EXPECTED_AUTH:
            raise HTTPException(status_code=401, detail="Ungueltiges PVEAPIToken.")

    @app.get("/api2/json/version")
    async def version(_: None = Depends(_check_auth)) -> dict:
        return {"data": {"version": "8.1.3"}}

    @app.get("/api2/json/nodes")
    async def list_nodes(_: None = Depends(_check_auth)) -> dict:
        if state.get("api_down"):
            # Wie waehrend des naechtlichen Neustarts: die API selbst antwortet nicht.
            raise HTTPException(status_code=503, detail="Service Unavailable")
        return {"data": list(state["nodes"].values())}

    @app.get("/api2/json/nodes/{node}/rrddata")
    async def node_rrd(node: str, request: Request, _: None = Depends(_check_auth)) -> dict:
        """Form wie Proxmox 9: Knoten melden memused/memtotal, iowait/cpu als Anteil,
        fehlende Werte (NaN) fehlen einfach im Objekt."""
        state.setdefault("rrd_calls", []).append(("node", node, dict(request.query_params)))
        import time as _t

        now = int(_t.time())
        return {"data": [
            {"time": now - 7200, "cpu": 0.9},  # ausserhalb 1 h -> faellt weg
            {"time": now - 120, "cpu": 0.05, "iowait": 0.01, "loadavg": 1.5, "memused": 8e9, "memtotal": 16e9, "netin": 1000.0, "netout": 2000.0},
            {"time": now - 60, "loadavg": 1.2, "memused": 8.5e9, "memtotal": 16e9},
        ]}

    @app.get("/api2/json/nodes/{node}/qemu/{vmid}/rrddata")
    async def qemu_rrd(node: str, vmid: str, request: Request, _: None = Depends(_check_auth)) -> dict:
        state.setdefault("rrd_calls", []).append(("qemu", vmid, dict(request.query_params)))
        import time as _t

        now = int(_t.time())
        return {"data": [
            {"time": now - 120, "cpu": 0.25, "mem": 1e9, "maxmem": 4e9, "diskread": 5000.0, "diskwrite": 7000.0},
            {"time": now - 60, "cpu": 0.5, "mem": 1.1e9, "maxmem": 4e9, "diskread": 0.0, "diskwrite": 100.0},
        ]}

    @app.get("/api2/json/nodes/{node}/status")
    async def node_status(node: str, _: None = Depends(_check_auth)) -> dict:
        # Pro Knoten unterschiedlicher CPU-Wert (Fallback 0.12 fuer bestehende Tests,
        # die `state["nodes"]` nicht anfassen) -- noetig, um echte Durchschnittsbildung
        # ueber mehrere Knoten zu beweisen, nicht nur denselben Wert mehrfach.
        cpu = state["nodes"].get(node, {}).get("cpu", 0.12)
        return {"data": {
            # Echte Form: RAM steht unter memory.used/total (weiter unten), NICHT mem/maxmem.
            "cpu": cpu, "uptime": 555555,
            "pveversion": "pve-manager/9.2.20/49318c671b82f31e",
            "current-kernel": {"release": state["running_kernel"], "sysname": "Linux"},
            "cpuinfo": {"model": "AMD Ryzen 9 7940HS w/ Radeon 780M Graphics", "cores": 8, "cpus": 16, "sockets": 1},
            "loadavg": ["0.22", "0.22", "0.29"],
            "memory": {"total": 14354522112, "used": 11764932608, "free": 2020405248, "available": 2589589504},
            "swap": {"total": 8589930496, "used": 798912512, "free": 7791017984},
            "rootfs": {"total": 100861726720, "used": 46791352320},
            "ksm": {"shared": 1355227136},
            "wait": 0.0142,
            "boot-info": {"mode": "efi", "secureboot": 0},
        }}

    @app.get("/api2/json/nodes/{node}/disks/list")
    async def disk_list(node: str, _: None = Depends(_check_auth)) -> dict:
        return {"data": state["disks"]}

    @app.get("/api2/json/nodes/{node}/disks/smart")
    async def disk_smart(node: str, disk: str, _: None = Depends(_check_auth)) -> dict:
        if disk not in state["smart"]:
            raise HTTPException(status_code=500, detail="no smart data")
        return {"data": state["smart"][disk]}

    @app.get("/api2/json/nodes/{node}/apt/update")
    async def apt_update_list(node: str, _: None = Depends(_check_auth)) -> dict:
        return {"data": state["apt_updates"]}

    @app.get("/api2/json/nodes/{node}/apt/versions")
    async def apt_version_list(node: str, _: None = Depends(_check_auth)) -> dict:
        return {"data": state["apt_versions"]}

    def _node_reachable(node: str) -> None:
        # Wie ein ausgefallener Knoten im Cluster: Proxmox antwortet mit 595.
        if node in state.get("offline_nodes", ()):
            raise HTTPException(status_code=595, detail="No route to host")

    @app.get("/api2/json/nodes/{node}/qemu")
    async def list_qemu(node: str, _: None = Depends(_check_auth)) -> dict:
        _node_reachable(node)
        return {"data": list(state["vms"].values())}

    @app.get("/api2/json/nodes/{node}/qemu/{vmid}/status/current")
    async def qemu_status(node: str, vmid: str, _: None = Depends(_check_auth)) -> dict:
        return {"data": state["vms"][vmid]}

    @app.post("/api2/json/nodes/{node}/qemu/{vmid}/status/{action}")
    async def qemu_action(node: str, vmid: str, action: str, request: Request, _: None = Depends(_check_auth)) -> dict:
        state["calls"].append((action, node, vmid))
        raw = await request.body()
        if raw:
            state.setdefault("action_bodies", {})[(action, node, vmid)] = json.loads(raw)
        vm = state["vms"][vmid]
        if action == "start":
            vm["status"] = "running"
        elif action in ("stop", "shutdown"):
            vm["status"] = "stopped"
        return {"data": f"UPID:mock:{action}:1:{vmid}::"}

    @app.post("/api2/json/nodes/{node}/qemu/{vmid}/snapshot")
    async def qemu_snapshot(node: str, vmid: str, request: Request, _: None = Depends(_check_auth)) -> dict:
        body = await request.json()
        state["snapshots"].append(body.get("snapname"))
        return {"data": "UPID:mock:snapshot:1::"}

    @app.get("/api2/json/nodes/{node}/lxc")
    async def list_lxc(node: str, _: None = Depends(_check_auth)) -> dict:
        _node_reachable(node)
        return {"data": list(state["containers"].values())}

    @app.get("/api2/json/nodes/{node}/lxc/{vmid}/status/current")
    async def lxc_status(node: str, vmid: str, _: None = Depends(_check_auth)) -> dict:
        return {"data": state["containers"][vmid]}

    @app.post("/api2/json/nodes/{node}/lxc/{vmid}/status/{action}")
    async def lxc_action(node: str, vmid: str, action: str, request: Request, _: None = Depends(_check_auth)) -> dict:
        state["calls"].append((action, node, vmid))
        raw = await request.body()
        if raw:
            state.setdefault("action_bodies", {})[(action, node, vmid)] = json.loads(raw)
        ct = state["containers"][vmid]
        if action == "start":
            ct["status"] = "running"
        elif action in ("stop", "shutdown"):
            ct["status"] = "stopped"
        return {"data": f"UPID:mock:{action}:1:{vmid}::"}

    @app.post("/api2/json/nodes/{node}/lxc/{vmid}/snapshot")
    async def lxc_snapshot(node: str, vmid: str, request: Request, _: None = Depends(_check_auth)) -> dict:
        body = await request.json()
        state["snapshots"].append(body.get("snapname"))
        return {"data": "UPID:mock:snapshot:1::"}

    @app.get("/api2/json/nodes/{node}/storage")
    async def node_storage(node: str, _: None = Depends(_check_auth)) -> dict:
        return {"data": state["storage"]}

    @app.get("/api2/json/nodes/{node}/storage/{storage}/content")
    async def storage_content(node: str, storage: str, _: None = Depends(_check_auth)) -> dict:
        return {"data": state["storage_content"].get(storage, [])}

    @app.get("/api2/json/nodes/{node}/{kind}/{vmid}/snapshot")
    async def list_snapshots(node: str, kind: str, vmid: str, _: None = Depends(_check_auth)) -> dict:
        return {"data": state["snapshot_list"].get(vmid, [{"name": "current"}])}

    @app.post("/api2/json/nodes/{node}/{kind}/{vmid}/snapshot/{snapname}/rollback")
    async def rollback(node: str, kind: str, vmid: str, snapname: str, _: None = Depends(_check_auth)) -> dict:
        state["snapshot_calls"].append(("rollback", kind, vmid, snapname))
        return {"data": f"UPID:mock:rollback:{vmid}::"}

    @app.delete("/api2/json/nodes/{node}/{kind}/{vmid}/snapshot/{snapname}")
    async def delete_snapshot(node: str, kind: str, vmid: str, snapname: str, _: None = Depends(_check_auth)) -> dict:
        state["snapshot_calls"].append(("delete", kind, vmid, snapname))
        return {"data": f"UPID:mock:delsnapshot:{vmid}::"}

    @app.get("/api2/json/nodes/{node}/{kind}/{vmid}/config")
    async def guest_config(node: str, kind: str, vmid: str, _: None = Depends(_check_auth)) -> dict:
        return {"data": state["guest_configs"].get(f"{kind}/{vmid}", {})}

    @app.put("/api2/json/nodes/{node}/{kind}/{vmid}/config")
    async def update_config(node: str, kind: str, vmid: str, request: Request, _: None = Depends(_check_auth)) -> dict:
        params = await request.json()
        current = state["guest_configs"].setdefault(f"{kind}/{vmid}", {})
        # Wie Proxmox: veralteter digest -> Ablehnung statt stilles Ueberschreiben.
        if params.get("digest") and params["digest"] != current.get("digest"):
            raise HTTPException(status_code=500, detail="config file has been modified by another user")
        state["config_updates"].append((kind, vmid, params))
        for key in str(params.get("delete") or "").split(","):
            current.pop(key, None)
        current.update({k: v for k, v in params.items() if k not in ("delete", "digest")})
        current["digest"] = f"{current.get('digest', '')}+"
        return {"data": None}

    @app.post("/api2/json/nodes/{node}/apt/update")
    async def apt_update(node: str, _: None = Depends(_check_auth)) -> dict:
        state["node_calls"].append(("apt_update", node))
        return {"data": f"UPID:{node}:aptupdate::"}

    @app.post("/api2/json/nodes/{node}/status")
    async def node_status_cmd(node: str, request: Request, _: None = Depends(_check_auth)) -> dict:
        state["node_calls"].append(((await request.json()).get("command"), node))
        return {"data": None}

    @app.get("/api2/json/nodes/{node}/{kind}/{vmid}/pending")
    async def pending(node: str, kind: str, vmid: str, _: None = Depends(_check_auth)) -> dict:
        return {"data": state["pending"].get(f"{kind}/{vmid}", [])}

    @app.get("/api2/json/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
    async def agent_ifaces(node: str, vmid: str, _: None = Depends(_check_auth)) -> dict:
        if vmid not in state["guest_agent"]:
            raise HTTPException(status_code=500, detail="QEMU guest agent is not running")
        return {"data": {"result": state["guest_agent"][vmid]}}

    @app.get("/api2/json/nodes/{node}/lxc/{vmid}/interfaces")
    async def lxc_ifaces(node: str, vmid: str, _: None = Depends(_check_auth)) -> dict:
        return {"data": state["lxc_ifaces"].get(vmid, [])}

    @app.post("/api2/json/nodes/{node}/{kind}/{vmid}/vncproxy")
    async def vncproxy(node: str, kind: str, vmid: str, request: Request, _: None = Depends(_check_auth)) -> dict:
        if state.get("vnc_forbidden"):
            raise HTTPException(status_code=403, detail=f"Permission check failed (/vms/{vmid}, VM.Console)")
        body = await request.json()
        state["vnc_calls"].append((kind, node, vmid, body))
        data = {"port": "5901", "ticket": _VNC_TICKET, "user": _TOKEN_ID, "upid": "UPID:mock:vncproxy:1::"}
        if body.get("generate-password"):
            data["password"] = "gen12345"
        return {"data": data}

    @app.websocket("/api2/json/nodes/{node}/{kind}/{vmid}/vncwebsocket")
    async def vncwebsocket(websocket: WebSocket, node: str, kind: str, vmid: str, port: str, vncticket: str) -> None:
        """Wie pveproxy: Token-Header UND das (unveraendert angekommene) Ticket sind
        Pflicht, sonst wird der Handshake abgelehnt (close() vor accept() -> HTTP 403)."""
        if state.pop("vnc_redirect_once", False):
            # Ein Server (oder ein Proxy davor), der den Handshake mit einer Weiterleitung auf dieselbe
            # Adresse beantwortet. Der zweite Versuch wuerde normal durchgehen.
            state["vnc_ws"].append({"redirected": True})
            await websocket.send_denial_response(Response(status_code=302, headers={"location": str(websocket.url)}))
            return
        if websocket.headers.get("authorization") != _EXPECTED_AUTH or vncticket != _VNC_TICKET or port != "5901":
            state["vnc_ws"].append({"rejected": True, "vncticket": vncticket})
            await websocket.close(code=1008)
            return
        requested = websocket.scope.get("subprotocols") or []
        await websocket.accept(subprotocol="binary" if "binary" in requested else None)
        state["vnc_ws"].append({"kind": kind, "node": node, "vmid": vmid, "subprotocols": requested})
        await websocket.send_bytes(b"RFB 003.008\n")
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await websocket.send_bytes(b"pve-echo:" + message["bytes"])

    @app.get("/api2/json/nodes/{node}/tasks")
    async def node_task_list(node: str, request: Request, _: None = Depends(_check_auth)) -> dict:
        limit = int(request.query_params.get("limit", 50))
        rows = state["task_list"]
        typefilter = request.query_params.get("typefilter")
        if typefilter:
            rows = [r for r in rows if r["type"] == typefilter]
        return {"data": rows[:limit]}

    @app.get("/api2/json/nodes/{node}/tasks/{upid}/log")
    async def node_task_log(node: str, upid: str, _: None = Depends(_check_auth)) -> dict:
        return {"data": state["task_logs"].get(upid, [])}

    @app.get("/api2/json/nodes/{node}/tasks/{upid}/status")
    async def task_status(node: str, upid: str, _: None = Depends(_check_auth)) -> dict:
        state["calls"].append(("task_status", node, upid))
        return {"data": state["task_result"]}

    return app, state


@pytest_asyncio.fixture
async def mock_proxmox():
    app, state = _build_mock_proxmox_app()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
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


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _setup_proxmox(
    client, db_session, test_settings, base_url: str, *, extra_settings: dict | None = None, name: str = "primary"
) -> str:
    """Aktiviert die echte proxmox-Extension gegen den Mock (ueber die echte API,
    wie ein Admin es taete), setzt Einstellungen + Vault-Token, bootstrapt einen
    Owner. Gibt den Access-Token zurueck. Fuer den (haeufigeren) Ein-Verbindungs-Fall
    -- siehe `_setup_proxmox_connections()` fuer mehrere gleichzeitige Verbindungen."""
    token = await _setup_proxmox_connections(
        client, db_session, test_settings,
        [{"name": name, "base_url": base_url, "token_id": _TOKEN_ID, **(extra_settings or {})}],
    )
    return token


async def _setup_proxmox_connections(client, db_session, test_settings, connections: list[dict]) -> str:
    """Aktiviert proxmox gegen BELIEBIG VIELE gleichzeitige
    Verbindungen (`settings.connections`, siehe config.py). Jede Verbindung bekommt
    ihr eigenes Token ueber den echten `POST .../connections/{name}/token`-Endpunkt."""
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)

    token = await _bootstrap_owner(client)
    enabled = await client.post("/api/v1/extensions/proxmox/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["state"] == "enabled"

    # Siehe Modul-Docstring: kein API-Weg fuer settings ausserhalb von Token/Secrets
    # in dieser Runde.
    record = await db_session.get(ExtensionRecord, "proxmox")
    record.settings = {"connections": connections}
    await db_session.flush()

    for conn in connections:
        token_resp = await client.post(
            f"/api/v1/ext/proxmox/connections/{conn['name']}/token",
            json={"value": _TOKEN_SECRET}, headers=_auth_header(token),
        )
        assert token_resp.status_code == 204, token_resp.text
    return token


async def _run_discovery() -> dict:
    handler = get_extension_runtime().scheduler.get("proxmox", "discovery")
    assert handler is not None
    return await handler()


def test_new_node_identifiers_are_lowercase_and_not_doubled():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "proxmox" / "src"))  # _cleanup_sys_path raeumt auf
    from nodvard_deck_ext_proxmox.capabilities import guest_host_name, node_host_name

    assert node_host_name("lab", "lab") == "pve-lab"
    assert node_host_name("Lab", "lab") == "pve-lab"
    assert node_host_name("primary", "Pve1") == "pve-primary-pve1"
    assert node_host_name("lab 1", "node.a") == "pve-lab-1-node-a"
    assert guest_host_name("Primary", "vm", "100") == "proxmox-primary-vm-100"


@pytest.mark.asyncio
async def test_discovery_names_new_nodes_uniformly_but_never_renames_known_ones(client, db_session, test_settings, mock_proxmox):
    """Verbindung und Knoten heissen gleich: neu eingelesen `pve-pve1` statt `pve-pve1-pve1`.
    Ein schon eingelesener Knoten behaelt seine Kennung (daran haengen Verlauf, Zugaenge ...),
    und eine belegte Kennung loest keinen Fehler aus."""
    base_url, _state = mock_proxmox
    await _setup_proxmox_connections(client, db_session, test_settings, [{"name": "pve1", "base_url": base_url, "token_id": _TOKEN_ID}])

    await _run_discovery()
    names = {h.provider_ref: h.name for h in (await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox"))).scalars()}
    assert names["pve1/node/pve1"] == "pve-pve1"
    assert names["pve1/qemu/pve1/100"] == "proxmox-pve1-vm-100"

    # Zweiter Lauf: nichts wird umbenannt oder doppelt angelegt.
    await _run_discovery()
    again = {h.provider_ref: h.name for h in (await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox"))).scalars()}
    assert again == names

    # Alter Bestand: der Knoten steht schon mit der alten Kennung in der Datenbank.
    node = (await db_session.execute(select(Host).where(Host.provider_ref == "pve1/node/pve1"))).scalar_one()
    node.name = "pve-pve1-pve1"
    await db_session.commit()
    await _run_discovery()
    db_session.expire_all()
    assert (await db_session.execute(select(Host.name).where(Host.provider_ref == "pve1/node/pve1"))).scalar_one() == "pve-pve1-pve1"


@pytest.mark.asyncio
async def test_discovery_falls_back_to_the_old_identifier_when_the_new_one_is_taken(client, db_session, test_settings, mock_proxmox):
    base_url, _state = mock_proxmox
    await _setup_proxmox_connections(client, db_session, test_settings, [{"name": "pve1", "base_url": base_url, "token_id": _TOKEN_ID}])
    db_session.add(Host(name="pve-pve1", display_name="von Hand", address="10.0.0.9", kind="server"))
    await db_session.commit()

    result = await _run_discovery()
    assert result == {"discovered": 3}
    name = (await db_session.execute(select(Host.name).where(Host.provider_ref == "pve1/node/pve1"))).scalar_one()
    assert name == "pve-pve1-pve1"


@pytest.mark.asyncio
async def test_discovery_adds_a_number_when_new_and_old_identifier_are_both_taken(client, db_session, test_settings, mock_proxmox):
    """Beide Formen vergeben: eine Zahl dahinter statt eines Abbruchs des ganzen Abgleichs."""
    base_url, _state = mock_proxmox
    await _setup_proxmox_connections(client, db_session, test_settings, [{"name": "pve1", "base_url": base_url, "token_id": _TOKEN_ID}])
    db_session.add(Host(name="pve-pve1", display_name="von Hand", address="10.0.0.9", kind="server"))
    db_session.add(Host(name="pve-pve1-pve1", display_name="auch von Hand", address="10.0.0.10", kind="server"))
    await db_session.commit()

    assert await _run_discovery() == {"discovered": 3}
    name = (await db_session.execute(select(Host.name).where(Host.provider_ref == "pve1/node/pve1"))).scalar_one()
    assert name == "pve-pve1-pve1-2"


@pytest.mark.asyncio
async def test_discovery_ignores_a_matching_provider_ref_of_another_extension(client, db_session, test_settings, mock_proxmox):
    """Nur eigene Hosts behalten ihre Kennung; ein fremder Host mit zufaellig gleichem
    `provider_ref` darf seinen Namen nicht vererben (der waere ja schon belegt)."""
    base_url, _state = mock_proxmox
    await _setup_proxmox_connections(client, db_session, test_settings, [{"name": "pve1", "base_url": base_url, "token_id": _TOKEN_ID}])
    db_session.add(Host(name="fremd", display_name="fremd", address="10.0.0.9", provider_ext_id="andere", provider_ref="pve1/node/pve1"))
    await db_session.commit()

    assert await _run_discovery() == {"discovered": 3}
    name = (await db_session.execute(
        select(Host.name).where(Host.provider_ext_id == "proxmox", Host.provider_ref == "pve1/node/pve1")
    )).scalar_one()
    assert name == "pve-pve1"


@pytest.mark.asyncio
async def test_proxmox_discovery_creates_node_and_vm_hosts_with_tags(client, db_session, test_settings, mock_proxmox):
    base_url, _state = mock_proxmox
    await _setup_proxmox(client, db_session, test_settings, base_url)

    result = await _run_discovery()
    assert result == {"discovered": 3}  # Knoten + VM + LXC

    hosts = (await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox"))).scalars().all()
    by_kind = {h.kind: h for h in hosts}
    assert set(by_kind) == {"hypervisor", "vm", "lxc"}
    assert by_kind["hypervisor"].provider_ref == "primary/node/pve1"
    assert by_kind["vm"].provider_ref == "primary/qemu/pve1/100"
    assert {t.tag for t in by_kind["vm"].tags} == {"proxmox", "vm"}
    assert by_kind["vm"].status == "up"
    assert by_kind["lxc"].provider_ref == "primary/lxc/pve1/201"
    assert {t.tag for t in by_kind["lxc"].tags} == {"proxmox", "lxc"}
    assert by_kind["lxc"].status == "up"


@pytest.mark.asyncio
async def test_proxmox_discovers_and_dispatches_actions_across_two_simultaneous_connections(
    client, db_session, test_settings, mock_proxmox
):
    """Mehrere Verbindungen, der eigentliche Beweis, weswegen das ueberhaupt gebaut
    wurde -- ZWEI unabhaengige, gleichzeitig konfigurierte Proxmox-Instanzen (zwei
    echte, getrennte Mock-Server, echtes HTTP), die BEIDE dieselbe VMID vergeben
    (hier: qemu/100 und lxc/201 auf beiden) -- real beobachtet an pve2 (VM100
    "docker") und pve1 (CT100 "docker-lxc"). Beweist: (1) `discover_hosts()` erzeugt
    fuer beide Verbindungen eigenstaendige, nicht kollidierende Host-Zeilen, (2) eine
    Aktion gegen den 'pve1'-Host trifft wirklich den zweiten Mock, nicht den ersten."""
    base_url_a, state_a = mock_proxmox
    app_b, state_b = _build_mock_proxmox_app()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port_b = sock.getsockname()[1]
    sock.close()
    config_b = uvicorn.Config(app_b, host="127.0.0.1", port=port_b, log_level="warning", lifespan="off")
    server_b = uvicorn.Server(config_b)
    serve_task_b = asyncio.ensure_future(server_b.serve())
    for _ in range(200):
        if server_b.started:
            break
        await asyncio.sleep(0.01)

    try:
        base_url_b = f"http://127.0.0.1:{port_b}"
        token = await _setup_proxmox_connections(
            client, db_session, test_settings,
            [
                {"name": "pve2", "base_url": base_url_a, "token_id": _TOKEN_ID},
                {"name": "pve1", "base_url": base_url_b, "token_id": _TOKEN_ID},
            ],
        )

        result = await _run_discovery()
        assert result == {"discovered": 6}  # (Knoten+VM+LXC) je Verbindung, 2 Verbindungen

        hosts = (await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox"))).scalars().all()
        assert len(hosts) == 6
        refs = {h.provider_ref for h in hosts}
        assert refs == {
            "pve2/node/pve1", "pve2/qemu/pve1/100", "pve2/lxc/pve1/201",
            "pve1/node/pve1", "pve1/qemu/pve1/100", "pve1/lxc/pve1/201",
        }
        names = {h.name for h in hosts}
        assert len(names) == 6  # keine Namenskollision trotz identischer VMIDs auf beiden Seiten

        mini_vm_host = next(h for h in hosts if h.provider_ref == "pve1/qemu/pve1/100")

        stop = await client.post(
            f"/api/v1/hosts/{mini_vm_host.id}/actions/vm.stop",
            json={"reason": "Testabschaltung auf pve1"},
            headers=_auth_header(token),
        )
        assert stop.status_code == 202, stop.text
        action_id = stop.json()["id"]
        approve = await client.post(f"/api/v1/actions/{action_id}/approve", headers=_auth_header(token))
        assert approve.status_code == 200, approve.text
        assert approve.json()["status"] == "succeeded"

        # Die Aktion muss den ZWEITEN Mock (pve1) getroffen haben, nicht den ersten (pve2).
        assert ("stop", "pve1", "100") in state_b["calls"]
        assert state_b["vms"]["100"]["status"] == "stopped"
        assert ("stop", "pve1", "100") not in state_a["calls"]
        assert state_a["vms"]["100"]["status"] == "running"
    finally:
        server_b.should_exit = True
        await serve_task_b


def _dead_url() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"


@pytest.mark.asyncio
async def test_discovery_keeps_going_when_one_connection_or_one_node_is_unreachable(
    client, db_session, test_settings, mock_proxmox
):
    """pve1 aus (oder im Neustart) -- vorher brach JEDER Discovery-Lauf
    ab, auch pve2 wurde nicht mehr eingelesen, alle Status froren ein. Ebenso ein
    offline Knoten im Cluster (list_qemu wirft dort). bekannte Gaeste von pve1 bleiben
    unangetastet stehen (nicht geloescht)."""
    base_url, state = mock_proxmox
    state["nodes"]["pve2"] = {"node": "pve2", "status": "offline"}
    state["offline_nodes"] = {"pve2"}
    token = await _setup_proxmox_connections(
        client, db_session, test_settings,
        [
            {"name": "pve1", "base_url": _dead_url(), "token_id": _TOKEN_ID},
            {"name": "pve2", "base_url": base_url, "token_id": _TOKEN_ID},
        ],
    )
    known_mini = Host(
        name="proxmox-pve1-lxc-104", display_name="Teleport", address="192.168.1.89", kind="lxc", status="up",
        provider_ext_id="proxmox", provider_ref="pve1/lxc/pve1/104", host_metadata={"connection": "pve1"},
    )
    db_session.add(known_mini)
    await db_session.flush()

    result = await _run_discovery()
    assert result == {"discovered": 4}  # pve2: pve1 + VM + LXC, dazu der offline Knoten pve2

    hosts = {h.provider_ref: h for h in (await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox"))).scalars().all()}
    assert set(hosts) == {"pve2/node/pve1", "pve2/qemu/pve1/100", "pve2/lxc/pve1/201", "pve2/node/pve2", "pve1/lxc/pve1/104"}
    assert hosts["pve2/node/pve2"].status == "down"
    assert hosts["pve2/qemu/pve1/100"].status == "up"
    await db_session.refresh(known_mini)
    assert (known_mini.status, known_mini.display_name) == ("up", "Teleport")

    # Auch die Statusauffrischung nach einer Aktion darf an pve1 nicht scheitern.
    vm = hosts["pve2/qemu/pve1/100"]
    stop = await client.post(f"/api/v1/hosts/{vm.id}/actions/vm.stop", json={"reason": "Test"}, headers=_auth_header(token))
    assert stop.status_code == 202, stop.text
    approve = await client.post(f"/api/v1/actions/{stop.json()['id']}/approve", headers=_auth_header(token))
    assert approve.json()["status"] == "succeeded", approve.text
    await db_session.refresh(vm)
    assert vm.status == "down"


@pytest.mark.asyncio
async def test_proxmox_vm_actions_go_through_gate_and_reach_the_real_mock(client, db_session, test_settings, mock_proxmox):
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()

    vm_host = (
        await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox", Host.kind == "vm"))
    ).scalars().one()

    actions = await client.get(f"/api/v1/hosts/{vm_host.id}/actions", headers=_auth_header(token))
    assert actions.status_code == 200
    action_types = {s["action_type"] for s in actions.json()}
    assert action_types == {"vm.start", "vm.shutdown", "vm.stop", "vm.reboot", "vm.snapshot", "vm.snapshot_rollback", "vm.snapshot_delete", "vm.config_set"}

    stop = await client.post(
        f"/api/v1/hosts/{vm_host.id}/actions/vm.stop",
        json={"reason": "Testabschaltung"},
        headers=_auth_header(token),
    )
    assert stop.status_code == 202, stop.text  # autonomy.mode=propose ist der Default

    action_id = stop.json()["id"]
    approve = await client.post(f"/api/v1/actions/{action_id}/approve", headers=_auth_header(token))
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "succeeded"

    assert ("stop", "pve1", "100") in state["calls"]
    assert state["vms"]["100"]["status"] == "stopped"

    snapshot = await client.post(
        f"/api/v1/hosts/{vm_host.id}/actions/vm.snapshot",
        json={"reason": "Vor einem Update"},
        headers=_auth_header(token),
    )
    assert snapshot.status_code == 202
    snap_action_id = snapshot.json()["id"]
    await client.post(f"/api/v1/actions/{snap_action_id}/approve", headers=_auth_header(token))
    assert len(state["snapshots"]) == 1


@pytest.mark.asyncio
async def test_proxmox_action_reports_real_task_failure_not_just_accepted(client, db_session, test_settings, mock_proxmox):
    """Vorher hiess
    `success=True` nur "Proxmox hat die UPID angenommen" -- ein Task, der DANACH
    tatsaechlich fehlschlaegt (z. B. eine gesperrte VM), wurde als Erfolg gemeldet.
    `wait_for_task()` pollt jetzt den echten Ausgang."""
    base_url, state = mock_proxmox
    state["task_result"] = {"status": "stopped", "exitstatus": "VM is locked (backup)"}
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()

    vm_host = (
        await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox", Host.kind == "vm"))
    ).scalars().one()

    stop = await client.post(
        f"/api/v1/hosts/{vm_host.id}/actions/vm.stop", json={"reason": "Testabschaltung"}, headers=_auth_header(token),
    )
    action_id = stop.json()["id"]
    approve = await client.post(f"/api/v1/actions/{action_id}/approve", headers=_auth_header(token))
    assert approve.status_code == 200, approve.text
    body = approve.json()
    assert body["status"] == "failed"
    assert "VM is locked" in body["result"]["error"]


@pytest.mark.asyncio
async def test_wait_for_task_returns_last_known_status_on_timeout_without_raising(
    client, db_session, test_settings, mock_proxmox
):
    """`wait_for_task()` direkt am echten Connector (ueber die echte, aktivierte
    Extension geholt, nicht selbst konstruiert -- `ctx.http` braucht eine echte
    Berechtigungspruefung dahinter), nicht ueber den ganzen Gate-Umweg -- ein Task,
    der laenger als das Zeitlimit laeuft, ist kein Fehler, nur "noch nicht fertig".
    Absichtlich winzige Zeiten (0.05s/0.01s), damit der Test nicht wirklich 30s
    wartet."""
    base_url, state = mock_proxmox
    state["task_result"] = {"status": "running"}
    await _setup_proxmox(client, db_session, test_settings, base_url)

    from nodvard_deck_ext_proxmox.config import build_connector

    runtime = get_extension_runtime()
    loaded = runtime.loaded["proxmox"]
    connector = await build_connector(loaded.ctx, "primary")

    status = await connector.wait_for_task("pve1", "UPID:mock:stop:1:100::", timeout_s=0.05, poll_interval_s=0.01)
    assert status["status"] == "running"


@pytest.mark.asyncio
async def test_proxmox_lxc_actions_go_through_gate_and_reach_the_real_mock(client, db_session, test_settings, mock_proxmox):
    """Derselbe Nachweis wie fuer QEMU-VMs oben, aber fuer einen
    LXC-Container -- beweist, dass `ActionExecutor.execute()` wirklich auf `kind`
    verzweigt (`connector.lxc_action()`/`lxc_snapshot()`), statt qemu-Endpunkte gegen
    einen LXC-VMID zu rufen (die auf einem echten Proxmox schlicht 404 waeren)."""
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()

    lxc_host = (
        await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox", Host.kind == "lxc"))
    ).scalars().one()

    actions = await client.get(f"/api/v1/hosts/{lxc_host.id}/actions", headers=_auth_header(token))
    assert actions.status_code == 200
    action_types = {s["action_type"] for s in actions.json()}
    assert action_types == {"vm.start", "vm.shutdown", "vm.stop", "vm.reboot", "vm.snapshot", "vm.snapshot_rollback", "vm.snapshot_delete", "vm.config_set"}  # dieselben Typen wie bei QEMU

    stop = await client.post(
        f"/api/v1/hosts/{lxc_host.id}/actions/vm.stop",
        json={"reason": "Testabschaltung"},
        headers=_auth_header(token),
    )
    assert stop.status_code == 202, stop.text
    action_id = stop.json()["id"]
    approve = await client.post(f"/api/v1/actions/{action_id}/approve", headers=_auth_header(token))
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "succeeded"

    assert ("stop", "pve1", "201") in state["calls"]
    assert state["containers"]["201"]["status"] == "stopped"


@pytest.mark.asyncio
async def test_proxmox_vm_shutdown_is_a_clean_shutdown_with_medium_risk(client, db_session, test_settings, mock_proxmox):
    """Die Aktion "Herunterfahren" ruft /status/shutdown (nicht /status/stop), hat
    mittleres Risiko und wartet mit dem laengeren Zeitlimit auf den Ausgang -- fuer VM UND
    Container."""
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()

    from nodvard_deck_ext_proxmox import capabilities
    from nodvard_deck_ext_proxmox.connector import ProxmoxConnector

    waits: list[float] = []
    real_wait = ProxmoxConnector.wait_for_task

    async def spy(self, node, upid, *, timeout_s=30.0, poll_interval_s=1.0):
        waits.append(timeout_s)
        return await real_wait(self, node, upid, timeout_s=timeout_s, poll_interval_s=poll_interval_s)

    ProxmoxConnector.wait_for_task = spy  # type: ignore[method-assign]
    try:
        for kind, vmid, table in (("vm", "100", "vms"), ("lxc", "201", "containers")):
            host = (await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox", Host.kind == kind))).scalars().one()
            specs = {s["action_type"]: s for s in (await client.get(f"/api/v1/hosts/{host.id}/actions", headers=_auth_header(token))).json()}
            assert specs["vm.shutdown"]["label"] == "Herunterfahren"
            assert specs["vm.shutdown"]["default_risk"] == "medium"
            assert specs["vm.stop"]["default_risk"] == "high"  # hart ausschalten bleibt hoch

            proposed = await client.post(f"/api/v1/hosts/{host.id}/actions/vm.shutdown", json={"reason": "Test"}, headers=_auth_header(token))
            assert proposed.status_code == 202, proposed.text
            assert proposed.json()["risk"] == "medium"
            approve = await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=_auth_header(token))
            assert approve.status_code == 200, approve.text
            body = approve.json()
            assert body["status"] == "succeeded", body
            assert body["result"]["output"] == "Herunterfahren abgeschlossen."

            node_vmid = ("pve1", vmid)
            assert ("shutdown", *node_vmid) in state["calls"]
            assert ("stop", *node_vmid) not in state["calls"]
            # Proxmox bekommt dasselbe Zeitlimit mit, sonst bricht es schon nach ~60 s ab
            assert state["action_bodies"][("shutdown", *node_vmid)] == {"timeout": 180}
            assert state[table][vmid]["status"] == "stopped"
            await db_session.refresh(host)
            assert host.status == "down"
    finally:
        ProxmoxConnector.wait_for_task = real_wait  # type: ignore[method-assign]
    # Das Dashboard wartet etwas laenger als Proxmox' eigenes Zeitlimit
    assert waits == [capabilities._SHUTDOWN_WAIT_S + capabilities._SHUTDOWN_GRACE_S] * 2
    assert capabilities._SHUTDOWN_WAIT_S == 180.0 > capabilities._TASK_WAIT_S


@pytest.mark.asyncio
async def test_proxmox_vm_shutdown_reports_still_running_instead_of_failing(client, db_session, test_settings, mock_proxmox, monkeypatch):
    """Faehrt der Gast nach dem Zeitlimit noch nicht herunter, ist das kein Fehler --
    die Aktion meldet ehrlich "laeuft noch", der Task laeuft in Proxmox weiter. Das
    Zeitlimit ist hier winzig, damit der Test nicht 180 s wartet."""
    base_url, state = mock_proxmox
    state["task_result"] = {"status": "running"}
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()

    # erst nach dem Laden der Extension importierbar
    from nodvard_deck_ext_proxmox import capabilities

    monkeypatch.setattr(capabilities, "_SHUTDOWN_WAIT_S", 0.05)
    monkeypatch.setattr(capabilities, "_SHUTDOWN_GRACE_S", 0.0)

    vm_host = (await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox", Host.kind == "vm"))).scalars().one()
    proposed = await client.post(f"/api/v1/hosts/{vm_host.id}/actions/vm.shutdown", json={"reason": "Test"}, headers=_auth_header(token))
    approve = await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=_auth_header(token))
    assert approve.status_code == 200, approve.text
    body = approve.json()
    assert body["status"] == "succeeded", body
    assert "läuft nach 0.05s noch" in body["result"]["output"]
    assert "noch nicht aus" in body["result"]["output"]
    assert body["result"]["detail"]["task_status"] == "running"


@pytest.mark.asyncio
async def test_proxmox_vm_shutdown_reports_real_task_failure(client, db_session, test_settings, mock_proxmox):
    """Ein Herunterfahren, das Proxmox ablehnt (z. B. gesperrte VM), ist ein Fehlschlag."""
    base_url, state = mock_proxmox
    state["task_result"] = {"status": "stopped", "exitstatus": "VM quit/powerdown failed - got timeout"}
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()

    vm_host = (await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox", Host.kind == "vm"))).scalars().one()
    proposed = await client.post(f"/api/v1/hosts/{vm_host.id}/actions/vm.shutdown", json={"reason": "Test"}, headers=_auth_header(token))
    approve = await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=_auth_header(token))
    body = approve.json()
    assert body["status"] == "failed"
    assert "Herunterfahren fehlgeschlagen" in body["result"]["error"]
    assert "got timeout" in body["result"]["error"]
    # verstaendlich erklaert: der Gast ist nach der Wartezeit nicht aus, Ausweg genannt
    assert "nach 180s noch nicht aus" in body["result"]["error"]
    assert "hart ausschalten" in body["result"]["error"]


@pytest.mark.asyncio
async def test_proxmox_metrics_endpoint_returns_real_sampled_values(client, db_session, test_settings, mock_proxmox):
    base_url, _state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()

    vm_host = (
        await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox", Host.kind == "vm"))
    ).scalars().one()

    metrics = await client.get(f"/api/v1/hosts/{vm_host.id}/metrics", headers=_auth_header(token))
    assert metrics.status_code == 200
    values = metrics.json()["values"]
    assert values["cpu_percent"] == 5.0
    assert values["mem_used_bytes"] == 512_000_000.0

    # Knoten zeigten RAM "-" -- sie melden memory.used/total.
    node_host = (
        await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox", Host.kind == "hypervisor"))
    ).scalars().first()
    node_values = (await client.get(f"/api/v1/hosts/{node_host.id}/metrics", headers=_auth_header(token))).json()["values"]
    assert (node_values["mem_used_bytes"], node_values["mem_total_bytes"]) == (11764932608.0, 14354522112.0)


@pytest.mark.asyncio
async def test_proxmox_history_comes_from_proxmox_rrd(client, db_session, test_settings, mock_proxmox):
    """Verlauf fuer Proxmox-Hosts direkt aus Proxmox' RRD-Daten -- Nodvard Deck speichert nichts."""
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    vm_host = (await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox", Host.kind == "vm"))).scalars().one()
    node_host = (await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox", Host.kind == "hypervisor"))).scalars().first()

    vm = (await client.get(f"/api/v1/hosts/{vm_host.id}/metrics/history?range=1h", headers=_auth_header(token))).json()
    assert vm["source"] == "provider" and vm["step_s"] == 60
    assert vm["series"]["cpu_percent"] == [25.0, 50.0], "Proxmox liefert Anteile, angezeigt wird Prozent"
    assert vm["series"]["mem_used_bytes"] == [1e9, 1.1e9]
    assert vm["series"]["disk_write_bps"] == [7000.0, 100.0]
    assert "cpu_iowait_percent" not in vm["series"], "ein Gast hat kein iowait -- keine leere Kurve"

    node = (await client.get(f"/api/v1/hosts/{node_host.id}/metrics/history?range=1h", headers=_auth_header(token))).json()
    assert len(node["timestamps"]) == 2, "Werte ausserhalb des Zeitraums fallen weg"
    assert node["series"]["cpu_percent"] == [5.0, None], "fehlender Wert bleibt Luecke"
    assert node["series"]["mem_used_bytes"] == [8e9, 8.5e9]

    await client.get(f"/api/v1/hosts/{vm_host.id}/metrics/history?range=7d", headers=_auth_header(token))
    assert state["rrd_calls"][-1][2] == {"timeframe": "week", "cf": "AVERAGE"}


@pytest.mark.asyncio
async def test_proxmox_health_reports_real_connectivity(client, db_session, test_settings, mock_proxmox):
    """`Extension.health()` ist der Weg, auf dem ein Admin (kuenftig ueber eine
    Health-Uebersicht) sieht, ob eine Extension ihre externe Abhaengigkeit erreicht --
    hier direkt gegen die echte `LoadedExtension.instance` geprueft."""
    base_url, _state = mock_proxmox
    await _setup_proxmox(client, db_session, test_settings, base_url)

    runtime = get_extension_runtime()
    loaded = runtime.loaded["proxmox"]
    report = await loaded.instance.health(loaded.ctx)
    assert report.healthy is True
    assert report.details["primary"]["version"] == "8.1.3"


@pytest.mark.asyncio
async def test_proxmox_health_does_not_hide_one_broken_connection_behind_a_working_one(
    client, db_session, test_settings, mock_proxmox
):
    """Korrektur an der ersten Multi-Instanz-Fassung: `healthy=True`, sobald
    IRGENDEINE Verbindung erreichbar war, verdeckte einen echten Teilausfall (pve1
    unerreichbar, pve2 laeuft) hinter dem Erfolg der anderen. Beweist die Schaerfung:
    EINE kaputte von ZWEI Verbindungen macht die gesamte Extension `healthy=False`,
    UND `details` zeigt fuer jede Verbindung einzeln und explizit, welche es war."""
    import socket as _socket

    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()

    base_url, _state = mock_proxmox
    await _setup_proxmox_connections(
        client, db_session, test_settings,
        [
            {"name": "pve2", "base_url": base_url, "token_id": _TOKEN_ID},
            {"name": "pve1", "base_url": f"http://127.0.0.1:{dead_port}", "token_id": _TOKEN_ID},
        ],
    )

    runtime = get_extension_runtime()
    loaded = runtime.loaded["proxmox"]
    report = await loaded.instance.health(loaded.ctx)

    assert report.healthy is False  # nicht "True, weil pve2 ja laeuft"
    assert report.details["pve2"]["healthy"] is True
    assert report.details["pve2"]["version"] == "8.1.3"
    assert report.details["pve1"]["healthy"] is False
    assert "pve1" in report.message
    assert "pve2" in report.message  # auch der funktionierende Teil bleibt sichtbar


@pytest.mark.asyncio
async def test_node_load_widget_returns_empty_value_instead_of_500_when_proxmox_unreachable(
    client, db_session, test_settings
):
    """Live gefunden (Boot-Test): ein NICHT erreichbarer Proxmox
    (Verbindung abgelehnt -- kein Mock-Server laeuft) liess `node_load_widget_data()`
    mit HTTP 500 abstuerzen, statt sauber `{"value": None}` zu melden. Ursache:
    `ProxmoxConnector._request()` fing nur HTTP-Fehlerstatus ab, keine rohen
    Netzwerkfehler (Verbindung abgelehnt/Timeout/DNS) -- der Aufrufer faengt nur
    `ProxmoxApiError`/`RuntimeError` ab, keine beliebige `httpx`-Exception."""
    import socket

    from nodvard_deck.models import Host as HostModel, HostTag

    # Ein Port, auf dem garantiert niemand lauscht -- oeffnen und sofort schliessen.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()

    token = await _setup_proxmox(client, db_session, test_settings, f"http://127.0.0.1:{dead_port}")

    node_host = HostModel(name="pve-pve1", display_name="Proxmox-Knoten pve1", address="127.0.0.1", kind="hypervisor", provider_ext_id="proxmox", provider_ref="primary/node/pve1")
    db_session.add(node_host)
    await db_session.flush()
    db_session.add(HostTag(host_id=node_host.id, tag="node"))
    await db_session.flush()

    r = await client.get("/api/v1/ext/proxmox/widgets/node-load", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["value"] is None


@pytest.mark.asyncio
async def test_node_load_widget_averages_across_all_known_nodes(client, db_session, test_settings, mock_proxmox):
    """Korrektur: vorher nahm die Kachel immer `nodes[0]` -- bei mehreren
    Proxmox-Knoten (pve2/pve1, siehe capabilities.py) eine willkuerliche
    Einzelmessung, nur ehrlich als "erster Knoten" gelabelt. Jetzt: Durchschnitt
    ueber ALLE Knoten. Zwei echte Knoten mit unterschiedlichem CPU-Wert (20 %, 40 %)
    -- beweist einen ECHTEN Durchschnitt (30 %), nicht nur denselben Mock-Wert
    zweimal gemittelt."""
    from nodvard_deck.models import Host as HostModel, HostTag

    base_url, state = mock_proxmox
    state["nodes"] = {
        "pve1": {"node": "pve1", "status": "online", "cpu": 0.20, "maxmem": 34_000_000_000},
        "pve2": {"node": "pve2", "status": "online", "cpu": 0.40, "maxmem": 34_000_000_000},
    }
    token = await _setup_proxmox(client, db_session, test_settings, base_url)

    for node_name in ("pve1", "pve2"):
        node_host = HostModel(
            name=f"pve-{node_name}", display_name=f"Proxmox-Knoten {node_name}", address="127.0.0.1",
            kind="hypervisor", provider_ext_id="proxmox", provider_ref=f"primary/node/{node_name}",
        )
        db_session.add(node_host)
        await db_session.flush()
        db_session.add(HostTag(host_id=node_host.id, tag="node"))
    await db_session.flush()

    r = await client.get("/api/v1/ext/proxmox/widgets/node-load", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["value"] == 30.0
    assert body["node_count"] == 2


@pytest.mark.asyncio
async def test_node_load_widget_averages_only_the_nodes_that_answered(client, db_session, test_settings, mock_proxmox):
    """Ein einzelner nicht erreichbarer Knoten darf die ganze Kachel nicht leer
    raeumen -- derselbe Anspruch wie beim Multi-Instanz-`health()`-Fund (siehe dort),
    nur fuer diese Kachel. Der zweite Host haengt an einer zweiten Verbindung, die auf
    einen toten Port zeigt; ihr Ausfall wird uebersprungen
    (`asyncio.gather(..., return_exceptions=True)`), nur der tatsaechlich geantwortete
    Knoten fliesst in den Durchschnitt ein."""
    import socket

    from nodvard_deck.models import Host as HostModel, HostTag

    base_url, state = mock_proxmox
    state["nodes"] = {"pve1": {"node": "pve1", "status": "online", "cpu": 0.50, "maxmem": 34_000_000_000}}

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()

    token = await _setup_proxmox_connections(
        client, db_session, test_settings,
        [
            {"name": "primary", "base_url": base_url, "token_id": _TOKEN_ID},
            {"name": "dead", "base_url": f"http://127.0.0.1:{dead_port}", "token_id": _TOKEN_ID},
        ],
    )

    working_host = HostModel(
        name="pve-pve1", display_name="Proxmox-Knoten pve1", address="127.0.0.1",
        kind="hypervisor", provider_ext_id="proxmox", provider_ref="primary/node/pve1",
    )
    db_session.add(working_host)
    await db_session.flush()
    db_session.add(HostTag(host_id=working_host.id, tag="node"))

    dead_host = HostModel(
        name="pve-pve2", display_name="Proxmox-Knoten pve2 (nicht erreichbar)", address="127.0.0.1",
        kind="hypervisor", provider_ext_id="proxmox", provider_ref="dead/node/pve2",
    )
    db_session.add(dead_host)
    await db_session.flush()
    db_session.add(HostTag(host_id=dead_host.id, tag="node"))
    await db_session.flush()

    r = await client.get("/api/v1/ext/proxmox/widgets/node-load", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["value"] == 50.0
    assert body["node_count"] == 1


def _write_self_signed_cert(tmp_path: Path) -> tuple[str, str]:
    """Ein ECHTES selbstsigniertes Zertifikat fuer `127.0.0.1` (nicht simuliert) --
    ein Proxmox-Knoten hat in aller Regel das Standard-Selbstsignat, der Fix muss also
    gegen einen echten TLS-Handshake bewiesen werden, der ohne Vertrauensanker
    tatsaechlich scheitert -- kein `respx`/Monkeypatch von `httpx.AsyncClient.get`."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "proxmox-mock-selfsigned.crt"
    key_path = tmp_path / "proxmox-mock-selfsigned.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return str(cert_path), str(key_path)


@pytest_asyncio.fixture
async def mock_proxmox_https(tmp_path: Path):
    """Wie `mock_proxmox`, aber echtes TLS mit einem echten selbstsignierten
    Zertifikat -- siehe `_write_self_signed_cert()`."""
    app, state = _build_mock_proxmox_app()
    cert_path, key_path = _write_self_signed_cert(tmp_path)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="off",
        ssl_certfile=cert_path, ssl_keyfile=key_path,
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.ensure_future(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)

    try:
        yield f"https://127.0.0.1:{port}", state
    finally:
        server.should_exit = True
        await serve_task


@pytest.mark.asyncio
async def test_proxmox_https_self_signed_cert_fails_by_default(client, db_session, test_settings, mock_proxmox_https):
    """Der Kern des Problems: OHNE `tls_insecure_skip_verify`
    schlaegt JEDE Verbindung zu einem selbstsignierten Zertifikat fehl -- ein Knoten
    mit Standard-Zertifikat war damit vor diesem Fix nie erreichbar. `Extension.health()`
    (ruft `connector.version()`) ist der ehrlichste Ort, das zu zeigen: kein 500er,
    ein sauberes `healthy=False`, genau wie fuer "Proxmox nicht erreichbar"."""
    base_url, _state = mock_proxmox_https
    await _setup_proxmox(client, db_session, test_settings, base_url)

    runtime = get_extension_runtime()
    loaded = runtime.loaded["proxmox"]
    report = await loaded.instance.health(loaded.ctx)
    assert report.healthy is False


@pytest.mark.asyncio
async def test_proxmox_https_self_signed_cert_succeeds_with_insecure_tls_setting(
    client, db_session, test_settings, mock_proxmox_https
):
    """Mit `tls_insecure_skip_verify=true` gesetzt: derselbe echte TLS-Handshake
    gegen dasselbe echte selbstsignierte Zertifikat gelingt jetzt -- das ist der
    tatsaechliche Fix, nicht nur eine verdrahtete Option."""
    base_url, _state = mock_proxmox_https
    await _setup_proxmox(
        client, db_session, test_settings, base_url, extra_settings={"tls_insecure_skip_verify": True}
    )

    runtime = get_extension_runtime()
    loaded = runtime.loaded["proxmox"]
    report = await loaded.instance.health(loaded.ctx)
    assert report.healthy is True
    assert report.details["primary"]["version"] == "8.1.3"


# Verbindungen ueber API/UI verwalten: bisher gab es dafuer nur direkten DB-Schreibzugriff (siehe
# `_setup_proxmox_connections()` oben, weiterhin als Fixture-Abkuerzung genutzt --
# die Tests hier beweisen den echten API-Weg, den ein Admin jetzt tatsaechlich hat).


@pytest.mark.asyncio
async def test_connections_crud_full_round_trip(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    enabled = await client.post("/api/v1/extensions/proxmox/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text
    headers = _auth_header(token)

    empty = await client.get("/api/v1/ext/proxmox/connections", headers=headers)
    assert empty.status_code == 200, empty.text
    assert empty.json() == []

    created = await client.post(
        "/api/v1/ext/proxmox/connections",
        json={"name": "pve2", "base_url": "https://192.168.1.23:8006", "token_id": _TOKEN_ID},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body == {
        "name": "pve2", "base_url": "https://192.168.1.23:8006", "token_id": _TOKEN_ID,
        "tls_insecure_skip_verify": False, "enabled": True, "has_token": False,
    }

    listed = await client.get("/api/v1/ext/proxmox/connections", headers=headers)
    assert listed.json() == [body]

    token_resp = await client.post(
        "/api/v1/ext/proxmox/connections/pve2/token", json={"value": _TOKEN_SECRET}, headers=headers,
    )
    assert token_resp.status_code == 204, token_resp.text

    after_token = await client.get("/api/v1/ext/proxmox/connections", headers=headers)
    assert after_token.json()[0]["has_token"] is True

    updated = await client.put(
        "/api/v1/ext/proxmox/connections/pve2",
        json={"tls_insecure_skip_verify": True},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["tls_insecure_skip_verify"] is True
    assert updated.json()["base_url"] == "https://192.168.1.23:8006"  # unveraendert, nur ein Feld gepatcht

    deleted = await client.delete("/api/v1/ext/proxmox/connections/pve2", headers=headers)
    assert deleted.status_code == 204, deleted.text
    after_delete = await client.get("/api/v1/ext/proxmox/connections", headers=headers)
    assert after_delete.json() == []


@pytest.mark.asyncio
async def test_add_connection_rejects_duplicate_name(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/extensions/proxmox/enable", headers=_auth_header(token))
    headers = _auth_header(token)
    payload = {"name": "pve1", "base_url": "https://192.168.1.63:8006", "token_id": _TOKEN_ID}

    first = await client.post("/api/v1/ext/proxmox/connections", json=payload, headers=headers)
    assert first.status_code == 201, first.text
    second = await client.post("/api/v1/ext/proxmox/connections", json=payload, headers=headers)
    assert second.status_code == 409, second.text


@pytest.mark.asyncio
async def test_verbindung_testen_je_verbindung(client, db_session, test_settings, mock_proxmox):
    """`POST /extensions/proxmox/test`: die echte health() der Extension, je Verbindung
    uebersetzt -- und das Token steht nirgends im Text."""
    base_url, _state = mock_proxmox
    dead = _dead_url()
    token = await _setup_proxmox_connections(
        client, db_session, test_settings,
        [
            {"name": "pve1", "base_url": base_url, "token_id": _TOKEN_ID},
            {"name": "pve2", "base_url": dead, "token_id": _TOKEN_ID},
        ],
    )
    r = await client.post("/api/v1/extensions/proxmox/test", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["message"] == "1 von 2 Verbindungen funktionieren nicht – Einzelheiten unten."
    by_name = {d["name"]: d for d in body["details"]}
    assert by_name["pve1"] == {"name": "pve1", "ok": True, "message": "Verbindung funktioniert."}
    assert by_name["pve2"]["ok"] is False and dead.removeprefix("http://") in by_name["pve2"]["message"]
    assert _TOKEN_SECRET not in r.text

    listing = await client.get("/api/v1/extensions/proxmox", headers=_auth_header(token))
    assert listing.json()["needs_setup"] is True
    assert "Der letzte Verbindungstest ist fehlgeschlagen" in listing.json()["setup_reasons"][-1]


@pytest.mark.asyncio
async def test_token_ersetzen_ueber_einstellungs_endpunkt(client, db_session, test_settings):
    """"Token setzen" lieferte beim zweiten Mal 409: `POST .../connections/{name}/token`
    legt nur an (`ctx.secrets` kennt kein Ersetzen). Die Oberflaeche nimmt deshalb
    `PUT /extensions/proxmox/secrets`, das anlegt ODER ersetzt."""
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    headers = _auth_header(token)
    await client.post("/api/v1/extensions/proxmox/enable", headers=headers)
    payload = {"name": "pve1", "base_url": "https://192.168.1.63:8006", "token_id": _TOKEN_ID}
    assert (await client.post("/api/v1/ext/proxmox/connections", json=payload, headers=headers)).status_code == 201

    first = await client.post("/api/v1/ext/proxmox/connections/pve1/token", json={"value": "erst-geheim"}, headers=headers)
    assert first.status_code == 204, first.text
    # Der alte Weg bleibt, wie er war (Kompatibilitaet): ein zweites Anlegen ist ein Konflikt.
    second = await client.post("/api/v1/ext/proxmox/connections/pve1/token", json={"value": "zweit-geheim"}, headers=headers)
    assert second.status_code == 409, second.text

    body = {"label": "proxmox-token:pve1", "value": "zweit-geheim"}
    replaced = await client.put("/api/v1/extensions/proxmox/secrets", json=body, headers=headers)
    assert replaced.status_code == 204, replaced.text
    listed = await client.get("/api/v1/ext/proxmox/connections", headers=headers)
    assert listed.json()[0]["has_token"] is True
    assert "geheim" not in listed.text


@pytest.mark.asyncio
async def test_update_and_delete_unknown_connection_return_404(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/extensions/proxmox/enable", headers=_auth_header(token))
    headers = _auth_header(token)

    update = await client.put("/api/v1/ext/proxmox/connections/ghost", json={"enabled": False}, headers=headers)
    assert update.status_code == 404
    delete = await client.delete("/api/v1/ext/proxmox/connections/ghost", headers=headers)
    assert delete.status_code == 404


@pytest.mark.asyncio
async def test_disabling_a_connection_removes_it_from_health_without_losing_its_token(
    client, db_session, test_settings, mock_proxmox
):
    """Kern des Punkts "unabhaengiges Aktivieren/Deaktivieren": eine deaktivierte
    Verbindung verschwindet ueberall, wo `build_connectors()` die Grundlage ist
    (hier per `health()` bewiesen), OHNE dass ihr Token-Secret verloren geht --
    ein erneutes Aktivieren braucht keine Neuanlage des Tokens."""
    base_url, _state = mock_proxmox
    token = await _setup_proxmox_connections(
        client, db_session, test_settings, [{"name": "primary", "base_url": base_url, "token_id": _TOKEN_ID}],
    )
    headers = _auth_header(token)
    runtime = get_extension_runtime()
    loaded = runtime.loaded["proxmox"]

    report_enabled = await loaded.instance.health(loaded.ctx)
    assert report_enabled.healthy is True

    disabled = await client.put("/api/v1/ext/proxmox/connections/primary", json={"enabled": False}, headers=headers)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    assert disabled.json()["has_token"] is True  # Token bleibt erhalten

    report_disabled = await loaded.instance.health(loaded.ctx)
    assert report_disabled.healthy is False
    assert report_disabled.message == "Keine Verbindung konfiguriert."

    re_enabled = await client.put("/api/v1/ext/proxmox/connections/primary", json={"enabled": True}, headers=headers)
    assert re_enabled.status_code == 200, re_enabled.text
    report_re_enabled = await loaded.instance.health(loaded.ctx)
    assert report_re_enabled.healthy is True


# ---------------------------------------------------------------------------
# Konsole: VM-/Container-Bildschirm ueber Nodvard Deck statt Proxmox-Login
# ---------------------------------------------------------------------------


async def _open_console_through_the_dashboard(running_app, db_session, test_settings, mock_base: str, host_name: str):
    """Die komplette Kette: Login -> POST /console/sessions -> WS /ws/console
    -> (Nodvard-Deck-Backend) -> vncwebsocket am Proxmox-Mock."""
    from httpx import AsyncClient

    http_base, ws_base = running_app
    async with AsyncClient(base_url=http_base) as ac:
        token = await _setup_proxmox(ac, db_session, test_settings, mock_base)
        await _run_discovery()
        host = (await db_session.execute(select(Host).where(Host.name == host_name))).scalar_one()

        listed = await ac.get("/api/v1/console/hosts", headers=_auth_header(token))
        assert host.id in listed.json()

        created = await ac.post("/api/v1/console/sessions", json={"host_id": host.id}, headers=_auth_header(token))
    assert created.status_code == 200, created.text
    body = created.json()
    return body, f"{ws_base}{body['ws_url']}"


@pytest.mark.asyncio
async def test_vm_console_is_relayed_through_the_dashboard_with_token_auth(running_app, db_session, test_settings, mock_proxmox):
    import websockets

    base_url, state = mock_proxmox
    body, ws_url = await _open_console_through_the_dashboard(
        running_app, db_session, test_settings, base_url, "proxmox-primary-vm-100"
    )

    # QEMU: Proxmox erzeugt ein eigenes Einmal-Kennwort (generate-password), das der
    # Browser fuer die RFB-Auth bekommt -- weder das Ticket noch das API-Token.
    assert state["vnc_calls"] == [("qemu", "pve1", "100", {"websocket": 1, "generate-password": 1})]
    assert body["protocol"] == "vnc"
    assert body["password"] == "gen12345"
    assert _TOKEN_SECRET not in str(body)

    # Das Ticket kam unveraendert an (sonst haette der Mock abgelehnt), mit Token-Header
    # und Subprotokoll "binary" wie bei Proxmox' eigener Oberflaeche.
    assert state["vnc_ws"] == [{"kind": "qemu", "node": "pve1", "vmid": "100", "subprotocols": ["binary"]}]

    async with websockets.connect(ws_url, subprotocols=["binary"]) as ws:
        assert await ws.recv() == b"RFB 003.008\n"
        await ws.send(b"RFB 003.008\n")
        assert await ws.recv() == b"pve-echo:RFB 003.008\n"


@pytest.mark.asyncio
async def test_lxc_console_uses_ticket_as_password(running_app, db_session, test_settings, mock_proxmox):
    base_url, state = mock_proxmox
    body, _ws_url = await _open_console_through_the_dashboard(
        running_app, db_session, test_settings, base_url, "proxmox-primary-lxc-201"
    )

    # Der LXC-Endpunkt kennt `generate-password` nicht -- dort ist das Ticket das Kennwort.
    assert state["vnc_calls"] == [("lxc", "pve1", "201", {"websocket": 1})]
    assert body["password"] == _VNC_TICKET


@pytest.mark.asyncio
async def test_nodes_get_no_console(running_app, db_session, test_settings, mock_proxmox):
    from httpx import AsyncClient

    base_url, state = mock_proxmox
    http_base, _ = running_app
    async with AsyncClient(base_url=http_base) as ac:
        token = await _setup_proxmox(ac, db_session, test_settings, base_url)
        await _run_discovery()
        node = (await db_session.execute(select(Host).where(Host.kind == "hypervisor"))).scalar_one()
        listed = await ac.get("/api/v1/console/hosts", headers=_auth_header(token))
        created = await ac.post("/api/v1/console/sessions", json={"host_id": node.id}, headers=_auth_header(token))
    assert node.id not in listed.json()
    assert created.status_code == 404
    assert state["vnc_calls"] == []


@pytest.mark.asyncio
async def test_console_permission_error_from_proxmox_reaches_the_user(
    running_app, db_session, test_settings, mock_proxmox
):
    """Z. B. ein Token ohne `VM.Console`-Recht: Proxmox lehnt vncproxy mit 403 ab --
    die Meldung muss beim Nutzer ankommen, nicht als nackter 500er."""
    from httpx import AsyncClient

    base_url, state = mock_proxmox
    http_base, _ = running_app

    async with AsyncClient(base_url=http_base) as ac:
        token = await _setup_proxmox(ac, db_session, test_settings, base_url)
        await _run_discovery()
        vm = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-vm-100"))).scalar_one()
        state["vnc_forbidden"] = True
        created = await ac.post("/api/v1/console/sessions", json={"host_id": vm.id}, headers=_auth_header(token))
    assert created.status_code == 502
    assert "VM.Console" in created.json()["detail"]
    assert state["vnc_ws"] == []


@pytest.mark.asyncio
async def test_console_does_not_follow_a_redirect_of_the_vnc_websocket(
    running_app, db_session, test_settings, mock_proxmox
):
    """Proxmox antwortet auf `vncwebsocket` normalerweise nie mit 3xx. Tut es der Server (oder ein Proxy
    davor) doch, folgt `ctx.http.websocket` nicht: kein zweiter Verbindungsaufbau, die Meldung kommt als
    verstaendlicher deutscher Satz beim Nutzer an."""
    from httpx import AsyncClient

    base_url, state = mock_proxmox
    http_base, _ = running_app

    async with AsyncClient(base_url=http_base) as ac:
        token = await _setup_proxmox(ac, db_session, test_settings, base_url)
        await _run_discovery()
        vm = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-vm-100"))).scalar_one()
        state["vnc_redirect_once"] = True
        created = await ac.post("/api/v1/console/sessions", json={"host_id": vm.id}, headers=_auth_header(token))
    assert created.status_code == 502, created.text
    detail = created.json()["detail"]
    assert "umgeleitet" in detail and "HTTP 302" in detail
    # Nur der erste Versuch kam beim Server an -- die Weiterleitung wurde nicht verfolgt.
    assert state["vnc_ws"] == [{"redirected": True}]


@pytest.mark.asyncio
async def test_overview_tiles_show_german_kind_labels(client, db_session, test_settings, mock_proxmox):
    """Die Proxmox-Uebersicht zeigte "hypervisor" als Untertitel --
    ein interner Wert, auf der Proxmox-Seite heisst dasselbe schon "Knoten"."""
    base_url, _state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()

    rows = (await client.get("/api/v1/ext/proxmox/widgets/overview", headers=_auth_header(token))).json()["data"]
    assert {(r["kind"], r["kind_label"]) for r in rows} == {("hypervisor", "Knoten"), ("vm", "VM"), ("lxc", "LXC")}
    widgets = (await client.get("/api/v1/widgets", headers=_auth_header(token))).json()
    spec = next(w for w in widgets if w["ext_id"] == "proxmox" and w["id"] == "overview")
    assert spec["view"]["tile_subtitle"] == "{{ kind_label }} · {{ connection }}"


# ---------------------------------------------------------------------------
# Echte Gast-Adressen (vorher standen VMs mit der Knoten-IP in der
# Terminal-Liste)
# ---------------------------------------------------------------------------


def _agent_iface(name: str, *ips: str) -> dict:
    return {"name": name, "ip-addresses": [{"ip-address-type": "ipv4", "ip-address": ip} for ip in ips]}


def test_pick_guest_ipv4_prefers_the_nodes_subnet_and_never_a_docker_bridge():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "proxmox" / "src"))  # _cleanup_sys_path raeumt auf
    from nodvard_deck_ext_proxmox.capabilities import pick_guest_ipv4

    # Typischer Fall: die VM "docker" meldet fuenf Docker-Bruecken neben der echten Adresse.
    docker_vm = [("lo", "127.0.0.1"), ("br-00053c833bb8", "172.22.0.1"), ("docker0", "172.17.0.1"), ("eth0", "192.168.1.85")]
    assert pick_guest_ipv4(docker_vm, "192.168.1.23") == "192.168.1.85"
    # LXC liefert CIDR-Schreibweise.
    assert pick_guest_ipv4([("lo", "127.0.0.1/8"), ("eth0", "192.168.1.89/24")], "192.168.1.63") == "192.168.1.89"
    # Knoten per DNS-Name: kein Subnetz-Vergleich moeglich -> erste Adresse ausserhalb von Bruecken/VPNs.
    assert pick_guest_ipv4([("docker0", "172.17.0.1"), ("tailscale0", "100.64.0.3"), ("ens18", "10.0.0.7")], "pve.home.example") == "10.0.0.7"
    assert pick_guest_ipv4([("lo", "127.0.0.1"), ("veth1", None)], "192.168.1.23") is None


@pytest.mark.asyncio
async def test_discovery_takes_the_address_the_guest_reports(client, db_session, test_settings, mock_proxmox):
    base_url, state = mock_proxmox
    state["guest_agent"]["100"] = [_agent_iface("lo", "127.0.0.1"), _agent_iface("docker0", "172.17.0.1"), _agent_iface("eth0", "10.0.0.5")]
    state["lxc_ifaces"]["201"] = [{"name": "lo", "inet": "127.0.0.1/8"}, {"name": "eth0", "inet": "10.0.0.6/24"}]
    await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()

    vm = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-vm-100"))).scalar_one()
    ct = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-lxc-201"))).scalar_one()
    await db_session.refresh(vm)
    await db_session.refresh(ct)
    assert (vm.address, vm.host_metadata["address_source"]) == ("10.0.0.5", "guest-agent")
    assert (ct.address, ct.host_metadata["address_source"]) == ("10.0.0.6", "container")


@pytest.mark.asyncio
async def test_a_manually_corrected_address_survives_until_the_guest_itself_reports_one(
    client, db_session, test_settings, mock_proxmox
):
    """Die alte Regel bleibt: ein Platzhalter ueberschreibt nie eine von Hand
    korrigierte Adresse (game-win: kein Gast-Agent, .92 von Hand). Meldet der Gast
    spaeter selbst eine Adresse, ist DAS die Wahrheit."""
    base_url, state = mock_proxmox
    await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    vm = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-vm-100"))).scalar_one()
    assert vm.address == "127.0.0.1"  # Platzhalter (kein Agent)
    vm.address = "192.168.1.92"
    await db_session.commit()

    await _run_discovery()
    await db_session.refresh(vm)
    assert vm.address == "192.168.1.92"

    state["guest_agent"]["100"] = [_agent_iface("Ethernet", "192.168.1.93")]
    await _run_discovery()
    await db_session.refresh(vm)
    assert vm.address == "192.168.1.93"


@pytest.mark.asyncio
async def test_stopped_guests_are_not_asked_for_an_address(client, db_session, test_settings, mock_proxmox):
    base_url, state = mock_proxmox
    state["vms"]["100"]["status"] = "stopped"
    calls: list[str] = []
    state["guest_agent"]["100"] = [_agent_iface("eth0", "10.0.0.5")]
    await _setup_proxmox(client, db_session, test_settings, base_url)

    import nodvard_deck_ext_proxmox.connector as connector_module

    original = connector_module.ProxmoxConnector.qemu_agent_interfaces

    async def _spy(self, node, vmid):  # noqa: ANN001, ANN202
        calls.append(vmid)
        return await original(self, node, vmid)

    connector_module.ProxmoxConnector.qemu_agent_interfaces = _spy
    try:
        await _run_discovery()
    finally:
        connector_module.ProxmoxConnector.qemu_agent_interfaces = original
    assert calls == []


# ---------------------------------------------------------------------------
# Speicher-Uebersicht + Snapshot-Verwaltung
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_overview_shows_usage_and_which_guest_disks_live_where(client, db_session, test_settings, mock_proxmox):
    """Live gefunden beim Bau: auf einem Knoten lag eine Gast-Disk auf `local` statt
    `local-lvm` -- die Uebersicht muss je Pool zeigen, WELCHE Gast-Disks darauf liegen."""
    base_url, _state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()

    r = await client.get("/api/v1/ext/proxmox/storage", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    pools = {p["storage"]: p for p in r.json()["pools"]}
    assert (pools["local-lvm"]["used_percent"], pools["local-lvm"]["tone"]) == (95.0, "danger")
    assert (pools["local"]["used_percent"], pools["local"]["tone"]) == (40.0, "good")
    assert pools["local-lvm"]["content_labels"] == ["VM-Disks", "Container-Disks"]
    # Gast-Disks mit Klarnamen aus der Discovery.
    assert [(v["name"], v["size"]) for v in pools["local"]["volumes"]] == [("test-lxc", 45)]
    assert [(v["name"], v["size"]) for v in pools["local-lvm"]["volumes"]] == [("test-vm", 500)]
    assert pools["local"]["other"]["backup"] == {"label": "Backups", "count": 2, "size": 30}
    assert pools["backup-pi"]["shared"] is True

    widget = await client.get("/api/v1/ext/proxmox/widgets/storage", headers=_auth_header(token))
    rows = widget.json()["data"]
    assert {r["storage"] for r in rows} == {"local-lvm", "local", "backup-pi"}
    assert "volumes" not in rows[0], "das Widget fragt den Pool-Inhalt bewusst nicht ab"


@pytest.mark.asyncio
async def test_updates_and_storage_tiles_show_an_unreachable_connection(client, db_session, test_settings, mock_proxmox):
    """pve1 aus -- die Kacheln "Proxmox-Updates" und "Proxmox-Speicher"
    zeigten nur pve2 und wirkten gruen; der Fehler stand nur in `meta`, das kein
    Widget liest. Jetzt eine eigene rote Zeile je nicht erreichbarer Verbindung, wie
    bei der Kachel "Datentraeger"."""
    base_url, _state = mock_proxmox
    token = await _setup_proxmox_connections(
        client, db_session, test_settings,
        [
            {"name": "pve2", "base_url": base_url, "token_id": _TOKEN_ID},
            {"name": "pve1", "base_url": _dead_url(), "token_id": _TOKEN_ID},
        ],
    )
    headers = _auth_header(token)

    updates = (await client.get("/api/v1/ext/proxmox/widgets/updates", headers=headers)).json()["data"]
    assert [(r["node"], r["badge"], r["tone"]) for r in updates] == [("pve1", "nicht erreichbar", "danger"), ("pve1", "3 Updates", "warn")]
    assert updates[0]["connection"] == "pve1" and updates[0]["summary"]

    storage = (await client.get("/api/v1/ext/proxmox/widgets/storage", headers=headers)).json()["data"]
    assert (storage[0]["storage"], storage[0]["connection"], storage[0]["badge"], storage[0]["tone"]) == ("pve1", "pve1", "nicht erreichbar", "danger")
    assert storage[0]["summary"]
    pools = {r["storage"]: r for r in storage[1:]}
    assert set(pools) == {"local-lvm", "local", "backup-pi"}
    assert (pools["local-lvm"]["summary"], pools["local-lvm"]["badge"]) == ("pve2 · 950 B von 1000 B", "95 %")

    # Die Kachel zeigt die vom Backend fertig formulierten Felder -- auch fuer die Fehlerzeile.
    widgets = (await client.get("/api/v1/widgets", headers=headers)).json()
    spec = next(w for w in widgets if w["ext_id"] == "proxmox" and w["id"] == "storage")
    assert (spec["view"]["item"]["subtitle"], spec["view"]["item"]["badge"]["text"]) == ("{{ summary }}", "{{ badge }}")


@pytest.mark.asyncio
async def test_shared_storage_is_listed_once_with_the_nodes_it_is_available_on(client, db_session, test_settings, mock_proxmox):
    """Ein NFS-Speicher steht in der Antwort jedes Knotens. Er darf nur einmal erscheinen,
    mit Hinweis "geteilt" und den Knoten; Belegung und Groesse werden nicht addiert."""
    base_url, state = mock_proxmox
    state["nodes"] = {
        "pve1": {"node": "pve1", "status": "online"},
        "pve2": {"node": "pve2", "status": "online"},
    }
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    rows = (await client.get("/api/v1/ext/proxmox/widgets/storage", headers=headers)).json()["data"]
    nfs = [r for r in rows if r["storage"] == "backup-pi"]
    assert len(nfs) == 1
    assert nfs[0]["nodes"] == ["pve1", "pve2"]
    assert nfs[0]["summary"] == "geteilt, verfügbar auf pve1, pve2 · 100 B von 1000 B"
    assert (nfs[0]["used"], nfs[0]["total"]) == (100, 1000)
    # Nicht geteilte Speicher bleiben je Knoten.
    assert len([r for r in rows if r["storage"] == "local"]) == 2

    pools = (await client.get("/api/v1/ext/proxmox/storage", headers=headers)).json()["pools"]
    assert len([p for p in pools if p["storage"] == "backup-pi"]) == 1


@pytest.mark.asyncio
async def test_shared_storage_is_merged_across_connections_in_the_tile_only(client, db_session, test_settings, mock_proxmox):
    """Zwei eigenstaendige Proxmox-Server binden dieselbe Freigabe ein: die Kachel zeigt
    sie einmal, die Seite (die nach Verbindung gruppiert) weiter je Verbindung. Auch ein
    NFS-Speicher ohne `shared`-Markierung gilt als geteilt."""
    base_url, state = mock_proxmox
    state["storage"][2]["shared"] = 0
    token = await _setup_proxmox_connections(
        client, db_session, test_settings,
        [
            {"name": "pve-a", "base_url": base_url, "token_id": _TOKEN_ID},
            {"name": "pve-b", "base_url": base_url, "token_id": _TOKEN_ID},
        ],
    )
    headers = _auth_header(token)

    rows = (await client.get("/api/v1/ext/proxmox/widgets/storage", headers=headers)).json()["data"]
    nfs = [r for r in rows if r["storage"] == "backup-pi"]
    assert len(nfs) == 1 and nfs[0]["shared"] is True
    assert nfs[0]["connections"] == ["pve-a", "pve-b"]
    assert len([r for r in rows if r["storage"] == "local"]) == 2

    pools = (await client.get("/api/v1/ext/proxmox/storage", headers=headers)).json()["pools"]
    assert sorted(p["connection"] for p in pools if p["storage"] == "backup-pi") == ["pve-a", "pve-b"]


@pytest.mark.asyncio
async def test_shared_storage_stays_one_row_when_a_later_node_reports_it_inactive(monkeypatch):
    """Ueber Verbindungen zusammengefuehrt; meldet ein weiterer Knoten der zweiten Verbindung
    den Speicher gerade inaktiv (Groesse 0), darf daraus keine zweite Zeile werden."""
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "proxmox" / "src"))  # _cleanup_sys_path raeumt auf
    from nodvard_deck_ext_proxmox import storage as storage_mod

    class FakeConnector:
        def __init__(self, nodes: dict[str, int]) -> None:
            self._nodes = nodes

        async def list_nodes(self) -> list[dict]:
            return [{"node": n, "status": "online"} for n in self._nodes]

        async def node_storage(self, node: str) -> list[dict]:
            total = self._nodes[node]
            return [{"storage": "nfs-backup", "type": "nfs", "content": "backup", "shared": 1,
                     "active": 1 if total else 0, "total": total, "used": 100 if total else 0}]

    async def fake_connectors(_ctx):
        return {"pve-a": FakeConnector({"a1": 1000, "a2": 1000}), "pve-b": FakeConnector({"b1": 1000, "b2": 0})}

    monkeypatch.setattr(storage_mod, "build_connectors", fake_connectors)
    overview = await storage_mod.collect_storage(None, details=False, merge_connections=True)
    rows = [p for p in overview["pools"] if p["storage"] == "nfs-backup"]
    assert len(rows) == 1
    assert rows[0]["nodes"] == ["a1", "a2", "b1", "b2"]
    assert rows[0]["connections"] == ["pve-a", "pve-b"]


def test_storage_tile_formats_like_the_bytes_filter():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "proxmox" / "src"))  # _cleanup_sys_path raeumt auf
    from nodvard_deck_ext_proxmox.storage import format_bytes, percent_badge

    assert [format_bytes(v) for v in (None, 0, 1000, 1536, 46 * 1024**3, 1.96 * 1024**4)] == ["?", "0 B", "1000 B", "1,5 KB", "46 GB", "2 TB"]
    assert [percent_badge(v) for v in (None, 95.0, 46.4)] == ["unbekannt", "95 %", "46,4 %"]


@pytest.mark.asyncio
async def test_snapshots_are_listed_newest_first_without_the_current_pseudo_entry(
    client, db_session, test_settings, mock_proxmox
):
    base_url, _state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    vm = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-vm-100"))).scalar_one()
    node = (await db_session.execute(select(Host).where(Host.kind == "hypervisor"))).scalar_one()

    r = await client.get(f"/api/v1/ext/proxmox/guests/{vm.id}/snapshots", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert [(s["name"], s["description"], s["with_ram"]) for s in r.json()] == [
        ("vor-update", "Vor dem Update", True), ("aelter", "", False),
    ]
    assert (await client.get(f"/api/v1/ext/proxmox/guests/{node.id}/snapshots", headers=_auth_header(token))).status_code == 404


@pytest.mark.asyncio
async def test_snapshot_rollback_and_delete_go_through_the_gate_and_reach_proxmox(
    client, db_session, test_settings, mock_proxmox
):
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    vm = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-vm-100"))).scalar_one()

    for action_type in ("vm.snapshot_rollback", "vm.snapshot_delete"):
        proposed = await client.post(
            f"/api/v1/hosts/{vm.id}/actions/{action_type}",
            json={"payload": {"snapname": "vor-update"}, "reason": "Test"}, headers=_auth_header(token),
        )
        assert proposed.status_code == 202, proposed.text
        assert state["snapshot_calls"] == [] or action_type == "vm.snapshot_delete"
        approved = await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=_auth_header(token))
        assert approved.json()["status"] == "succeeded", approved.text

    assert state["snapshot_calls"] == [("rollback", "qemu", "100", "vor-update"), ("delete", "qemu", "100", "vor-update")]
    # Zurueckrollen ist hohes Risiko, Loeschen mittleres.
    specs = {s["action_type"]: s for s in (await client.get(f"/api/v1/hosts/{vm.id}/actions", headers=_auth_header(token))).json()}
    assert (specs["vm.snapshot_rollback"]["default_risk"], specs["vm.snapshot_delete"]["default_risk"]) == ("high", "medium")


@pytest.mark.asyncio
async def test_hardware_change_goes_through_the_gate_with_digest_and_reports_pending(
    client, db_session, test_settings, mock_proxmox
):
    """Kerne/RAM/Autostart aendern -- ueber das Gate, nur Whitelist,
    mit dem digest der gelesenen Konfiguration, und ehrlich, was erst nach einem
    Neustart greift."""
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    vm = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-vm-100"))).scalar_one()
    state["pending"]["qemu/100"] = [{"key": "memory", "value": "5120", "pending": 6144}, {"key": "cores", "value": 8}]

    proposed = await client.post(
        f"/api/v1/hosts/{vm.id}/actions/vm.config_set",
        json={"payload": {"changes": {"memory": 6144, "startup_order": 5, "cores": 8}}, "reason": "Test"},
        headers=_auth_header(token),
    )
    assert proposed.status_code == 202, proposed.text
    assert proposed.json()["risk"] == "medium"
    assert state["config_updates"] == [], "vor der Freigabe darf nichts geschrieben sein"
    approved = (await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=_auth_header(token))).json()
    assert approved["status"] == "succeeded", approved
    assert state["config_updates"] == [("qemu", "100", {"memory": 6144, "startup": "order=5", "digest": "abc"})]
    assert approved["result"]["output"] == (
        "Geändert: RAM 5120 MB → 6144 MB, Startreihenfolge 2 → 5. Wirksam erst nach einem Neustart des Gasts: RAM."
    )

    # Alles ausserhalb der Whitelist scheitert im Executor -- Proxmox sieht nichts.
    for changes in ({"net0": "virtio,bridge=vmbr9"}, {"cipassword": "x"}, {"cores": "8,hostpci1=0000:02:00"}):
        proposed = await client.post(
            f"/api/v1/hosts/{vm.id}/actions/vm.config_set",
            json={"payload": {"changes": changes}, "reason": "Test"}, headers=_auth_header(token),
        )
        result = (await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=_auth_header(token))).json()
        assert result["status"] == "failed", changes
    assert len(state["config_updates"]) == 1


@pytest.mark.asyncio
async def test_hardware_change_is_refused_when_someone_else_changed_the_config_meanwhile(
    client, db_session, test_settings, mock_proxmox, monkeypatch
):
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    vm = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-vm-100"))).scalar_one()

    import nodvard_deck_ext_proxmox.capabilities as caps

    real_build = caps.build_update

    def build_then_someone_else_saves(kind, config, changes):
        params = real_build(kind, config, changes)
        state["guest_configs"]["qemu/100"]["digest"] = "von-jemand-anderem"
        return params

    monkeypatch.setattr(caps, "build_update", build_then_someone_else_saves)
    proposed = await client.post(
        f"/api/v1/hosts/{vm.id}/actions/vm.config_set",
        json={"payload": {"changes": {"cores": 4}}, "reason": "Test"}, headers=_auth_header(token),
    )
    result = (await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=_auth_header(token))).json()
    assert result["status"] == "failed"
    assert "modified by another user" in result["result"]["error"]
    assert state["config_updates"] == []


@pytest.mark.asyncio
async def test_node_actions_refresh_packages_and_reboot_only_for_nodes(client, db_session, test_settings, mock_proxmox):
    """Knoten: Paketlisten aktualisieren (niedrig) und Neustart (kritisch) -- ueber das
    Gate, nur auf Knoten, nicht auf Gaesten."""
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    node = (await db_session.execute(select(Host).where(Host.kind == "hypervisor"))).scalars().first()
    vm = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-vm-100"))).scalar_one()
    headers = _auth_header(token)

    specs = {s["action_type"]: s for s in (await client.get(f"/api/v1/hosts/{node.id}/actions", headers=headers)).json()}
    assert (specs["node.apt_refresh"]["default_risk"], specs["node.reboot"]["default_risk"]) == ("low", "critical")
    assert specs["node.reboot"]["source"] == "host"
    vm_specs = {s["action_type"] for s in (await client.get(f"/api/v1/hosts/{vm.id}/actions", headers=headers)).json()}
    assert "node.reboot" not in vm_specs

    for action_type in ("node.apt_refresh", "node.reboot"):
        proposed = await client.post(f"/api/v1/hosts/{node.id}/actions/{action_type}", json={"payload": {}, "reason": "Test"}, headers=headers)
        assert proposed.status_code == 202, proposed.text
        approved = (await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=headers)).json()
        assert approved["status"] == "succeeded", approved
    node_name = node.provider_ref.split("/")[2]
    assert state["node_calls"] == [("apt_update", node_name), ("reboot", node_name)]


@pytest.mark.asyncio
async def test_snapshot_names_that_could_escape_the_url_path_are_rejected(client, db_session, test_settings, mock_proxmox):
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    vm = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-vm-100"))).scalar_one()

    proposed = await client.post(
        f"/api/v1/hosts/{vm.id}/actions/vm.snapshot_delete",
        json={"payload": {"snapname": "../../status/stop"}, "reason": "Test"}, headers=_auth_header(token),
    )
    approved = await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=_auth_header(token))
    assert approved.json()["status"] == "failed"
    assert "Ungültiger Snapshot-Name" in approved.json()["result"]["error"]
    assert state["snapshot_calls"] == []
    assert state["calls"] == []


@pytest.mark.asyncio
async def test_task_history_shows_what_happened_who_did_it_and_whether_it_worked(
    client, db_session, test_settings, mock_proxmox
):
    base_url, _state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()

    r = await client.get("/api/v1/ext/proxmox/tasks", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    tasks = r.json()["tasks"]
    # Konsolen-Oeffnungen standardmaessig ausgeblendet, neueste zuerst.
    assert [t["type"] for t in tasks] == ["vzdump", "qmstart", "stopall", "aptupdate"]
    by_type = {t["type"]: t for t in tasks}
    assert (by_type["qmstart"]["type_label"], by_type["qmstart"]["guest_name"]) == ("VM gestartet", "test-vm")
    assert (by_type["qmstart"]["ok"], by_type["qmstart"]["duration_s"], by_type["qmstart"]["user"]) == (True, 4, "root@pam")
    assert (by_type["vzdump"]["running"], by_type["vzdump"]["ok"], by_type["vzdump"]["guest_name"]) == (True, None, "test-lxc")
    assert (by_type["stopall"]["type_label"], by_type["stopall"]["ok"]) == ("Alle Gäste gestoppt (Knoten fährt herunter)", False)

    with_console = await client.get("/api/v1/ext/proxmox/tasks", params={"include_console": True}, headers=_auth_header(token))
    assert with_console.json()["tasks"][0]["type_label"] == "Konsole geöffnet"


@pytest.mark.asyncio
async def test_task_log_lines_in_order_and_upid_must_belong_to_the_node(client, db_session, test_settings, mock_proxmox):
    base_url, _state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    upid = "UPID:pve1:0002:B:2:qmstart:100:root@pam:"

    r = await client.get("/api/v1/ext/proxmox/tasks/primary/pve1/log", params={"upid": upid}, headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert r.json() == {"lines": ["starting VM 100", "TASK OK"]}

    wrong = await client.get("/api/v1/ext/proxmox/tasks/primary/pve1/log", params={"upid": "UPID:other:1"}, headers=_auth_header(token))
    assert wrong.status_code == 400
    anonymous = await client.get("/api/v1/ext/proxmox/tasks")
    assert anonymous.status_code == 401


# ---------------------------------------------------------------------------
# Paket-Updates je Knoten (rein lesend)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_updates_show_waiting_packages_and_that_a_new_kernel_needs_a_reboot(
    client, db_session, test_settings, mock_proxmox
):
    base_url, _state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)

    r = await client.get("/api/v1/ext/proxmox/updates", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    (node,) = r.json()["nodes"]
    assert (node["connection"], node["node"], node["count"]) == ("primary", "pve1", 3)
    assert (node["kernel_update"], node["reboot_pending"]) == (True, False)
    assert (node["pve_version"], node["running_kernel"], node["newest_kernel"]) == ("9.2.20", "7.0.14-16-pve", "7.0.14-16")
    assert node["summary"] == "3 Updates verfügbar, darunter ein neuer Kernel (danach Neustart nötig)"
    assert (node["badge"], node["tone"]) == ("3 Updates", "warn")
    assert (node["last_check"], node["last_check_ok"], node["last_check_status"]) == (900, True, "OK")
    assert node["last_check_stale"] is True and node["last_check_age_s"] > 36 * 3600  # der Test-Task stammt von 1970
    firewall = next(p for p in node["packages"] if p["package"] == "pve-firewall")
    assert (firewall["old_version"], firewall["version"], firewall["new_package"]) == ("6.0.5", "6.0.6", False)
    signed = next(p for p in node["packages"] if p["package"].endswith("-signed"))
    assert signed["new_package"] is True

    widget = await client.get("/api/v1/ext/proxmox/widgets/updates", headers=_auth_header(token))
    (row,) = widget.json()["data"]
    assert row["badge"] == "3 Updates"
    assert "packages" not in row, "das Widget braucht die Paketliste nicht"


@pytest.mark.asyncio
async def test_updates_installed_kernel_not_yet_running_means_reboot_pending(
    client, db_session, test_settings, mock_proxmox
):
    """Nach der Installation: Kernel 7.0.14-19 liegt bereit, der Knoten laeuft aber noch
    auf -16 -- das ist der Zustand, den man sonst leicht wochenlang uebersieht."""
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    state["apt_updates"] = []
    state["apt_versions"].append({"Package": "proxmox-kernel-7.0.14-19-pve-signed", "Version": "7.0.14-19", "CurrentState": "Installed"})

    (node,) = (await client.get("/api/v1/ext/proxmox/updates", headers=_auth_header(token))).json()["nodes"]
    assert (node["count"], node["reboot_pending"], node["newest_kernel"]) == (0, True, "7.0.14-19")
    assert (node["badge"], node["tone"]) == ("Neustart", "warn")
    assert node["summary"].startswith("Neustart ausstehend: Kernel 7.0.14-19")

    state["running_kernel"] = "7.0.14-19-pve"
    (node,) = (await client.get("/api/v1/ext/proxmox/updates", headers=_auth_header(token))).json()["nodes"]
    assert (node["summary"], node["badge"], node["tone"]) == ("Auf dem neuesten Stand", "aktuell", "good")


@pytest.mark.asyncio
async def test_updates_offline_node_is_reported_not_hidden(client, db_session, test_settings, mock_proxmox):
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    state["nodes"]["pve2"] = {"node": "pve2", "status": "offline"}

    nodes = {n["node"]: n for n in (await client.get("/api/v1/ext/proxmox/updates", headers=_auth_header(token))).json()["nodes"]}
    assert (nodes["pve2"]["summary"], nodes["pve2"]["tone"]) == ("Knoten offline", "danger")
    assert nodes["pve1"]["count"] == 3
    assert (await client.get("/api/v1/ext/proxmox/updates")).status_code == 401


@pytest.mark.asyncio
async def test_updates_show_why_the_nightly_check_failed_and_when_it_is_overdue(client, db_session, test_settings, mock_proxmox):
    import time

    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    now = int(time.time())
    check = next(t for t in state["task_list"] if t["type"] == "aptupdate")
    check.update(starttime=now - 3600, endtime=now - 3590, status="command 'apt-get update' failed: exit code 100")

    (node,) = (await client.get("/api/v1/ext/proxmox/updates", headers=_auth_header(token))).json()["nodes"]
    assert node["last_check_ok"] is False
    assert node["last_check_status"] == "command 'apt-get update' failed: exit code 100"
    assert node["last_check_stale"] is False and 3500 < node["last_check_age_s"] < 3700

    # Noch laufend: kein Ergebnis, aber auch kein Fehler
    check.pop("endtime"), check.pop("status")
    (node,) = (await client.get("/api/v1/ext/proxmox/updates", headers=_auth_header(token))).json()["nodes"]
    assert (node["last_check_ok"], node["last_check_status"]) == (None, None)


def test_nightly_check_is_overdue_only_well_beyond_one_day():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "proxmox" / "src"))  # _cleanup_sys_path raeumt auf
    from nodvard_deck_ext_proxmox.updates import check_age

    now = 1_000_000.0
    assert check_age(now - 600, now) == (600, False)
    # Proxmox startet den Check mit zufaelliger Verzoegerung: 27 Stunden Abstand sind noch gesund
    assert check_age(now - 27 * 3600, now) == (27 * 3600, False)
    assert check_age(now - 37 * 3600, now) == (37 * 3600, True)
    assert check_age(now + 50, now) == (0, False)  # Uhr des Knotens geht vor
    assert check_age(None, now) == (None, False) and check_age("x", now) == (None, False) and check_age(True, now) == (None, False)


def test_kernel_version_compare_is_numeric_not_lexical():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "proxmox" / "src"))  # _cleanup_sys_path raeumt auf
    from nodvard_deck_ext_proxmox.updates import newest_installed_kernel, version_key

    assert version_key("7.0.14-19") > version_key("7.0.14-9")
    assert newest_installed_kernel([
        {"Package": "proxmox-kernel-7.0.14-9-pve-signed", "CurrentState": "Installed"},
        {"Package": "proxmox-kernel-7.0.14-19-pve-signed", "CurrentState": "Installed"},
        {"Package": "proxmox-kernel-7.0.14-21-pve-signed", "CurrentState": "ConfigFiles"},
        {"Package": "pve-kernel-5.15.108-1-pve", "CurrentState": "Installed"},
        {"Package": "proxmox-kernel-7.0", "CurrentState": "Installed"},
    ]) == "7.0.14-19"


# ---------------------------------------------------------------------------
# Gast-Details (Kerne, RAM, Disks, Netz, Agent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vm_details_show_hardware_disks_with_storage_and_ips_per_nic_but_never_secrets(
    client, db_session, test_settings, mock_proxmox
):
    base_url, state = mock_proxmox
    state["guest_agent"]["100"] = [
        {"name": "lo", "hardware-address": "00:00:00:00:00:00", "ip-addresses": [{"ip-address-type": "ipv4", "ip-address": "127.0.0.1", "prefix": 8}]},
        {"name": "ens18", "hardware-address": "02:a4:b5:0c:07:7f", "ip-addresses": [
            {"ip-address-type": "ipv4", "ip-address": "192.168.1.85", "prefix": 24},
            {"ip-address-type": "ipv6", "ip-address": "fe80::1", "prefix": 64},
        ]},
        {"name": "docker0", "hardware-address": "02:42:00:00:00:01", "ip-addresses": [{"ip-address-type": "ipv4", "ip-address": "172.17.0.1", "prefix": 16}]},
    ]
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    vm = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-vm-100"))).scalar_one()

    r = await client.get(f"/api/v1/ext/proxmox/guests/{vm.id}/details", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    d = r.json()
    for secret in ("GEHEIM", "cipassword", "sshkeys", "description"):
        assert secret not in r.text, f"{secret} darf Nodvard Deck nie verlassen"
    assert (d["cores"], d["sockets"], d["cpu_type"], d["memory_mb"], d["balloon_mb"]) == (8, 1, "host", 5120, 3072)
    assert (d["os"], d["bios"], d["machine"], d["onboot"], d["startup_order"]) == ("Linux", "UEFI", "q35", True, 2)
    assert (d["agent_enabled"], d["agent_responding"], d["running"]) == (True, True, True)
    assert d["tags"] == ["community-script", "docker"]
    assert d["passthrough"] == ["hostpci0: 0000:01:00"]
    disks = {x["slot"]: x for x in d["disks"]}
    assert (disks["scsi0"]["storage"], disks["scsi0"]["size"], disks["scsi0"]["kind"]) == ("local-lvm", "540G", "disk")
    assert (disks["efidisk0"]["storage"], disks["efidisk0"]["kind"]) == ("local", "disk")
    assert disks["ide2"]["kind"] == "cloudinit"
    assert (disks["ide0"]["kind"], disks["ide0"]["volume"]) == ("cdrom", "iso/debian-13.iso")
    assert (disks["unused0"]["kind"], disks["unused0"]["storage"]) == ("unused", "local-lvm")
    assert [x["slot"] for x in d["disks"]][-1] == "unused0", "ungenutzte Disks ans Ende"
    (nic,) = d["networks"]
    assert (nic["model"], nic["mac"], nic["bridge"], nic["firewall"]) == ("virtio", "02:A4:B5:0C:07:7F", "vmbr0", True)
    assert nic["ips"] == ["192.168.1.85/24"], "Adresse ueber die MAC der Karte zugeordnet, nur IPv4"
    assert d["other_ips"] == ["172.17.0.1/16 (docker0)"]


@pytest.mark.asyncio
async def test_vm_details_agent_configured_but_not_answering_is_said_plainly(client, db_session, test_settings, mock_proxmox):
    base_url, _state = mock_proxmox  # kein guest_agent-Eintrag -> Proxmox antwortet 500
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    vm = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-vm-100"))).scalar_one()

    d = (await client.get(f"/api/v1/ext/proxmox/guests/{vm.id}/details", headers=_auth_header(token))).json()
    assert (d["agent_enabled"], d["agent_responding"]) == (True, False)
    assert d["networks"][0]["ips"] == []


@pytest.mark.asyncio
async def test_container_details_rootfs_bind_mount_and_configured_ip(client, db_session, test_settings, mock_proxmox):
    base_url, state = mock_proxmox
    state["lxc_ifaces"]["201"] = [
        {"name": "lo", "hwaddr": "00:00:00:00:00:00", "inet": "127.0.0.1/8"},
        {"name": "eth0", "hwaddr": "bc:24:11:a2:3f:11", "inet": "192.168.1.65/24"},
    ]
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    ct = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-lxc-201"))).scalar_one()

    r = await client.get(f"/api/v1/ext/proxmox/guests/{ct.id}/details", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["kind"], d["cores"], d["memory_mb"], d["swap_mb"], d["sockets"]) == ("lxc", 2, 4096, 1024, None)
    assert (d["unprivileged"], d["features"], d["os"]) == (True, ["nesting", "keyctl"], "debian")
    assert "agent_enabled" not in d
    disks = {x["slot"]: x for x in d["disks"]}
    assert (disks["rootfs"]["storage"], disks["rootfs"]["size"]) == ("local-lvm", "25G")
    assert (disks["mp0"]["kind"], disks["mp0"]["volume"], disks["mp0"]["mountpoint"]) == ("bind", "/var/lib/vz/dump", "/mnt/pve1-backups")
    (nic,) = d["networks"]
    assert (nic["name"], nic["configured_ip"], nic["gateway"], nic["ips"]) == ("eth0", "192.168.1.65/24", "192.168.1.1", ["192.168.1.65/24"])

    assert (await client.get(f"/api/v1/ext/proxmox/guests/{ct.id}/details")).status_code == 401
    assert (await client.get("/api/v1/ext/proxmox/guests/does-not-exist/details", headers=_auth_header(token))).status_code == 404


# ---------------------------------------------------------------------------
# Knoten-Gesundheit (Datentraeger, SMART)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_health_shows_cpu_memory_and_disks_with_smart_for_nvme_and_sata(
    client, db_session, test_settings, mock_proxmox
):
    base_url, _state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    node = (await db_session.execute(select(Host).where(Host.kind == "hypervisor"))).scalar_one()

    r = await client.get(f"/api/v1/ext/proxmox/nodes/{node.id}/health", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    h = r.json()
    assert (h["cpu_model"], h["cpu_cores"], h["cpu_threads"]) == ("AMD Ryzen 9 7940HS w/ Radeon 780M Graphics", 8, 16)
    assert h["loadavg"] == [0.22, 0.22, 0.29]
    assert (h["mem_total"], h["mem_available"], h["swap_used"]) == (14354522112, 2589589504, 798912512)
    assert (h["io_wait_percent"], h["boot_mode"], h["pve_version"], h["kernel"]) == (1.4, "EFI", "9.2.20", "7.0.14-16-pve")
    assert "SERIAL" not in r.text, "Seriennummern braucht die Anzeige nicht"
    nvme, sata = h["disks"]
    assert (nvme["model"], nvme["life_left_percent"], nvme["temperature_c"], nvme["power_on_hours"]) == ("Example NVMe 1TB", 97, 34, 12345)
    assert (nvme["summary"], nvme["badge"], nvme["tone"]) == ("NVMe · 1.0 TB · 97 % Restlebensdauer · 34 °C", "gesund", "good")
    assert (sata["life_left_percent"], sata["temperature_c"], sata["power_on_hours"]) == (None, 32, 706)
    assert sata["summary"] == "SSD · 128 GB · 32 °C"

    vm = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-vm-100"))).scalar_one()
    assert (await client.get(f"/api/v1/ext/proxmox/nodes/{vm.id}/health", headers=_auth_header(token))).status_code == 404
    assert (await client.get(f"/api/v1/ext/proxmox/nodes/{node.id}/health")).status_code == 401


@pytest.mark.asyncio
async def test_disk_widget_puts_the_worst_disk_first(client, db_session, test_settings, mock_proxmox):
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    state["disks"][1]["health"] = "FAILED"
    del state["smart"]["/dev/nvme0n1"]  # SMART-Details nicht lesbar -> Liste bleibt trotzdem

    rows = (await client.get("/api/v1/ext/proxmox/widgets/disks", headers=_auth_header(token))).json()["data"]
    assert [(r["model"], r["badge"], r["tone"]) for r in rows] == [
        ("Example SATA SSD 128GB", "SMART: FAILED", "danger"),
        ("Example NVMe 1TB", "gesund", "good"),
    ]
    assert rows[1]["temperature_c"] is None


def test_disk_assessment_order_smart_then_wear_then_temperature():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "proxmox" / "src"))  # _cleanup_sys_path raeumt auf
    from nodvard_deck_ext_proxmox.node_health import disk_assessment, smart_details

    assert disk_assessment("FAILED", 90, 40) == ("SMART: FAILED", "danger")
    assert disk_assessment("PASSED", 5, 40) == ("5 % Rest", "danger")
    assert disk_assessment("PASSED", 25, 40) == ("25 % Rest", "warn")
    assert disk_assessment("PASSED", 80, 72) == ("72 °C", "warn")
    assert disk_assessment("OK", None, None) == ("gesund", "good")
    assert disk_assessment(None, None, None) == ("unbekannt", "neutral")
    assert smart_details({"attributes": [{"name": "Airflow_Temperature_Cel", "raw": "41"}]}) == {"temperature_c": 41, "power_on_hours": None}


# ---------------------------------------------------------------------------
# Zustandswaechter: meldet Wechsel, nicht Dauerzustaende; naechtlicher Neustart still
# ---------------------------------------------------------------------------


async def _run_watch() -> dict:
    handler = get_extension_runtime().scheduler.get("proxmox", "watch")
    assert handler is not None
    return await handler()


async def _watch_notifications(db_session) -> list[tuple[str, str]]:
    from nodvard_deck.models import Notification as NotificationRow

    rows = (await db_session.execute(
        select(NotificationRow).where(NotificationRow.source_ext_id == "proxmox").order_by(NotificationRow.ts)
    )).scalars().all()
    return [(r.severity, r.title) for r in rows]


@pytest.mark.asyncio
async def test_watch_reports_a_failing_disk_once_and_its_recovery_once(client, db_session, test_settings, mock_proxmox):
    base_url, state = mock_proxmox
    await _setup_proxmox(client, db_session, test_settings, base_url)

    assert (await _run_watch())["notified"] == 0, "gesunder Ausgangszustand meldet nichts"
    state["disks"][1]["health"] = "FAILED"
    assert (await _run_watch())["notified"] == 1
    assert (await _run_watch())["notified"] == 0, "derselbe Zustand wird nicht alle 5 Minuten erneut gemeldet"
    state["disks"][1]["health"] = "PASSED"
    assert (await _run_watch())["notified"] == 1

    assert await _watch_notifications(db_session) == [
        ("critical", "Datenträger auf pve1: SMART: FAILED"),
        ("info", "Datenträger auf pve1 wieder unauffällig"),
    ]


@pytest.mark.asyncio
async def test_watch_stays_silent_through_a_nightly_reboot_but_reports_a_real_outage(
    client, db_session, test_settings, mock_proxmox
):
    base_url, state = mock_proxmox
    await _setup_proxmox(client, db_session, test_settings, base_url)

    # Naechtlicher Neustart: zwei Pruefungen ohne Antwort, dann wieder da -> still.
    state["api_down"] = True
    await _run_watch()
    await _run_watch()
    state["api_down"] = False
    await _run_watch()
    assert await _watch_notifications(db_session) == []

    # Echter Ausfall: ab der dritten Fehlpruefung in Folge genau EINE Meldung.
    state["api_down"] = True
    for _ in range(5):
        await _run_watch()
    state["api_down"] = False
    await _run_watch()
    assert await _watch_notifications(db_session) == [
        ("critical", "Proxmox 'primary' nicht erreichbar"),
        ("info", "Proxmox 'primary' wieder erreichbar"),
    ]


@pytest.mark.asyncio
async def test_watch_does_not_alert_on_temperature_alone(client, db_session, test_settings, mock_proxmox):
    """Temperatur schwankt -- als Alarmgrund wuerde sie flattern. Sie steht im Widget,
    nicht in den Meldungen."""
    base_url, state = mock_proxmox
    await _setup_proxmox(client, db_session, test_settings, base_url)
    state["smart"]["/dev/nvme0n1"]["text"] = state["smart"]["/dev/nvme0n1"]["text"].replace("34 Celsius", "75 Celsius")

    assert (await _run_watch())["notified"] == 0


async def _node_host_id(db_session, provider_ref: str = "primary/node/pve1") -> str:
    return (await db_session.execute(select(Host.id).where(Host.provider_ref == provider_ref))).scalar_one()


async def _watch_payloads(db_session) -> dict[str, dict]:
    from nodvard_deck.models import Notification as NotificationRow

    rows = (await db_session.execute(
        select(NotificationRow).where(NotificationRow.source_ext_id == "proxmox").order_by(NotificationRow.ts)
    )).scalars().all()
    return {r.title: r.payload for r in rows}


@pytest.mark.asyncio
async def test_watch_messages_carry_the_host_of_their_node(client, db_session, test_settings, mock_proxmox):
    """Wartungsfenster unterdruecken nur Meldungen mit `payload.host_id`.
    Knoten-, Verbindungs- und Datentraeger-Meldungen (auch die Entwarnungen) gehoeren zum
    Host, der den Knoten in Nodvard Deck vertritt."""
    base_url, state = mock_proxmox
    await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    node_id = await _node_host_id(db_session)

    state["disks"][1]["health"] = "FAILED"
    await _run_watch()
    state["disks"][1]["health"] = "PASSED"
    await _run_watch()
    state["nodes"]["pve1"]["status"] = "offline"
    for _ in range(3):
        await _run_watch()
    state["nodes"]["pve1"]["status"] = "online"
    await _run_watch()
    state["api_down"] = True
    for _ in range(3):
        await _run_watch()
    state["api_down"] = False
    await _run_watch()

    payloads = await _watch_payloads(db_session)
    assert set(payloads) == {
        "Datenträger auf pve1: SMART: FAILED", "Datenträger auf pve1 wieder unauffällig",
        "Knoten pve1 offline", "Knoten pve1 wieder online",
        "Proxmox 'primary' nicht erreichbar", "Proxmox 'primary' wieder erreichbar",
    }
    assert all(p == {"host_id": node_id} for p in payloads.values()), payloads


@pytest.mark.asyncio
async def test_watch_messages_without_a_known_node_host_stay_without_host_id(
    client, db_session, test_settings, mock_proxmox
):
    """Solange die Discovery den Knoten noch nicht als Host angelegt hat, gibt es nichts
    zuzuordnen: die Meldung kommt wie bisher ohne Host-Bezug (und wird nie faelschlich still)."""
    base_url, state = mock_proxmox
    await _setup_proxmox(client, db_session, test_settings, base_url)
    state["disks"][1]["health"] = "FAILED"
    await _run_watch()

    assert await _watch_payloads(db_session) == {"Datenträger auf pve1: SMART: FAILED": {}}


def test_connection_host_id_only_when_the_connection_has_exactly_one_node():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "proxmox" / "src"))
    from nodvard_deck_ext_proxmox.watch import _connection_host_id

    nodes = {"pve2/node/pve": "h1", "pve1/node/pve1": "h2", "cluster/node/a": "h3", "cluster/node/b": "h4"}
    assert _connection_host_id(nodes, "pve2") == "h1"
    assert _connection_host_id(nodes, "cluster") is None, "bei mehreren Knoten steht kein einzelner Host fuer die Verbindung"
    assert _connection_host_id(nodes, "gibt-es-nicht") is None


class _RecordingChannel:
    channel_id = "test-kanal"
    label = "Test-Kanal"

    def __init__(self) -> None:
        self.received: list = []

    async def send(self, notification) -> None:
        self.received.append(notification)

    async def test(self):
        raise NotImplementedError


def _window_now(host_ids) -> list[dict]:
    """Wartungsfenster, das gerade laeuft (Start vor 5 Minuten, Ortszeit wie der Waehler)."""
    start = (utcnow() - timedelta(minutes=5)).astimezone(ZoneInfo(LOCAL_TIMEZONE))
    return [{"cron": f"{start.minute} {start.hour} * * *", "duration_minutes": 60, "host_ids": host_ids}]


@pytest.mark.asyncio
async def test_watch_message_is_silenced_end_to_end_by_the_maintenance_window_of_its_node(
    client, db_session, test_settings, mock_proxmox
):
    from nodvard_deck.models import NotificationDelivery

    base_url, state = mock_proxmox
    await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    node_id = await _node_host_id(db_session)
    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)

    # Fenster nur fuer einen anderen Host: die Meldung kommt durch.
    await settings_service.set_global(db_session, "maintenance.windows", _window_now(["anderer-host"]))
    state["disks"][1]["health"] = "FAILED"
    await _run_watch()
    assert [n.title for n in channel.received] == ["Datenträger auf pve1: SMART: FAILED"]

    # Fenster des Knotens: die Entwarnung wird nicht zugestellt, bleibt aber im Verlauf.
    await settings_service.set_global(db_session, "maintenance.windows", _window_now([node_id]))
    state["disks"][1]["health"] = "PASSED"
    await _run_watch()
    assert [n.title for n in channel.received] == ["Datenträger auf pve1: SMART: FAILED"]
    assert "Datenträger auf pve1 wieder unauffällig" in await _watch_payloads(db_session)
    statuses = (await db_session.execute(select(NotificationDelivery.status).order_by(NotificationDelivery.id))).scalars().all()
    assert statuses == ["sent", "suppressed"]


def _window_over(host_ids) -> list[dict]:
    """Dasselbe Fenster, aber schon vorbei (Start vor 90 Minuten, 60 Minuten lang)."""
    start = (utcnow() - timedelta(minutes=90)).astimezone(ZoneInfo(LOCAL_TIMEZONE))
    return [{"cron": f"{start.minute} {start.hour} * * *", "duration_minutes": 60, "host_ids": host_ids}]


def _node_offline(state, bad: bool) -> None:
    state["nodes"]["pve1"]["status"] = "offline" if bad else "online"


def _disk_failed(state, bad: bool) -> None:
    state["disks"][1]["health"] = "FAILED" if bad else "PASSED"


def _api_down(state, bad: bool) -> None:
    state["api_down"] = bad


_WINDOW_CASES = [
    pytest.param(_node_offline, 3, "Knoten pve1 offline", "Knoten pve1 wieder online", id="knoten"),
    pytest.param(_disk_failed, 1, "Datenträger auf pve1: SMART: FAILED", "Datenträger auf pve1 wieder unauffällig", id="datentraeger"),
    pytest.param(_api_down, 3, "Proxmox 'primary' nicht erreichbar", "Proxmox 'primary' wieder erreichbar", id="verbindung"),
]


async def _watch_deliveries(db_session) -> list[tuple[str, str]]:
    """(Titel, Zustellstatus) aller Waechter-Meldungen in Reihenfolge."""
    from nodvard_deck.models import Notification as NotificationRow
    from nodvard_deck.models import NotificationDelivery

    rows = (await db_session.execute(
        select(NotificationRow.title, NotificationDelivery.status)
        .join(NotificationDelivery, NotificationDelivery.notification_id == NotificationRow.id)
        .where(NotificationRow.source_ext_id == "proxmox").order_by(NotificationRow.ts, NotificationDelivery.id)
    )).all()
    return [(title, status) for title, status in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize(("make_bad", "polls", "alert_title", "ok_title"), _WINDOW_CASES)
async def test_watch_announces_a_problem_from_the_window_once_after_it_if_it_persists(
    client, db_session, test_settings, mock_proxmox, make_bad, polls, alert_title, ok_title
):
    """PR #41, bekannte Grenze: faellt ein Knoten im Wartungsfenster aus und bleibt aus,
    kam danach nie eine Ausfallmeldung. Jetzt: im Fenster genau EIN stummer Eintrag (auch
    bei vielen Pruefungen), nach dem Fenster genau EINE hoerbare Meldung. Der Zustand
    liegt in watch-state.json und wird bei jedem Lauf neu gelesen -- jeder Lauf hier ist
    also zugleich ein "Neustart"."""
    base_url, state = mock_proxmox
    await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    node_id = await _node_host_id(db_session)
    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)

    await settings_service.set_global(db_session, "maintenance.windows", _window_now([node_id]))
    make_bad(state, True)
    for _ in range(polls + 4):
        await _run_watch()
    assert channel.received == []
    assert await _watch_deliveries(db_session) == [(alert_title, "suppressed")], "ein stummer Eintrag, nicht einer je Prüfung"

    await settings_service.set_global(db_session, "maintenance.windows", _window_over([node_id]))
    assert (await _run_watch())["notified"] == 1
    for _ in range(3):
        assert (await _run_watch())["notified"] == 0, "nach dem Fenster genau einmal"
    assert [n.title for n in channel.received] == [f"{alert_title} (seit dem Wartungsfenster)"]
    assert "Wartungsfenster" in channel.received[0].body
    assert channel.received[0].payload == {"host_id": node_id}

    make_bad(state, False)
    await _run_watch()
    assert [n.title for n in channel.received][1:] == [ok_title]
    assert await _watch_deliveries(db_session) == [
        (alert_title, "suppressed"),
        (f"{alert_title} (seit dem Wartungsfenster)", "sent"),
        (ok_title, "sent"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("make_bad", "polls", "alert_title", "ok_title"), _WINDOW_CASES)
async def test_watch_problem_resolved_within_the_window_is_not_announced_afterwards(
    client, db_session, test_settings, mock_proxmox, make_bad, polls, alert_title, ok_title
):
    """Ausfall und Entwarnung beide im Fenster: beides steht still im Verlauf, nach dem
    Fenster kommt nichts nach."""
    base_url, state = mock_proxmox
    await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    node_id = await _node_host_id(db_session)
    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)

    await settings_service.set_global(db_session, "maintenance.windows", _window_now([node_id]))
    make_bad(state, True)
    for _ in range(polls):
        await _run_watch()
    make_bad(state, False)
    await _run_watch()

    await settings_service.set_global(db_session, "maintenance.windows", _window_over([node_id]))
    for _ in range(3):
        assert (await _run_watch())["notified"] == 0
    assert channel.received == []
    assert await _watch_deliveries(db_session) == [(alert_title, "suppressed"), (ok_title, "suppressed")]


@pytest.mark.asyncio
async def test_watch_recovery_noticed_only_after_the_window_says_it_was_in_the_window(
    client, db_session, test_settings, mock_proxmox
):
    """Grenzfall: stumm gemeldet, wieder gut erst zwischen letzter Pruefung im Fenster und
    erster danach bemerkt. Die Entwarnung laeuft wie jede Meldung durch die Fensterpruefung
    (symmetrisch) und kommt darum hoerbar -- mit dem Hinweis auf das Fenster."""
    base_url, state = mock_proxmox
    await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    node_id = await _node_host_id(db_session)
    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)

    await settings_service.set_global(db_session, "maintenance.windows", _window_now([node_id]))
    _node_offline(state, True)
    for _ in range(3):
        await _run_watch()
    await settings_service.set_global(db_session, "maintenance.windows", _window_over([node_id]))
    _node_offline(state, False)
    await _run_watch()

    assert [n.title for n in channel.received] == ["Knoten pve1 wieder online"]
    assert "Wartungsfenster" in channel.received[0].body


async def _watch_with_channel(client, db_session, test_settings, mock_proxmox):
    base_url, state = mock_proxmox
    await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    node_id = await _node_host_id(db_session)
    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)
    return state, node_id, channel


@pytest.mark.asyncio
@pytest.mark.parametrize(("make_bad", "polls", "alert_title", "ok_title"), _WINDOW_CASES)
async def test_watch_all_clear_for_a_heard_alarm_comes_after_the_window(
    client, db_session, test_settings, mock_proxmox, make_bad, polls, alert_title, ok_title
):
    """Aus dem Review: der Ausfall kam hoerbar, die Entwarnung faellt ins Fenster. Im Fenster
    bleibt sie still (niemand wird nachts geweckt), nach dem Fenster kommt sie genau
    einmal -- sonst bliebe der gehoerte Alarm auf dem Handy offen."""
    state, node_id, channel = await _watch_with_channel(client, db_session, test_settings, mock_proxmox)

    make_bad(state, True)
    for _ in range(polls):
        await _run_watch()
    assert [n.title for n in channel.received] == [alert_title]

    await settings_service.set_global(db_session, "maintenance.windows", _window_now([node_id]))
    make_bad(state, False)
    await _run_watch()
    for _ in range(2):
        assert (await _run_watch())["notified"] == 0, "im Fenster keine weitere Meldung"
    assert [n.title for n in channel.received] == [alert_title]

    await settings_service.set_global(db_session, "maintenance.windows", _window_over([node_id]))
    assert (await _run_watch())["notified"] == 1
    for _ in range(2):
        assert (await _run_watch())["notified"] == 0, "nach dem Fenster genau einmal"
    assert [n.title for n in channel.received][1:] == [f"{ok_title} (im Wartungsfenster)"]
    assert channel.received[1].payload == {"host_id": node_id}
    assert await _watch_deliveries(db_session) == [
        (alert_title, "sent"),
        (ok_title, "suppressed"),
        (f"{ok_title} (im Wartungsfenster)", "sent"),
    ]


@pytest.mark.asyncio
async def test_watch_pending_all_clear_is_dropped_when_the_problem_returns(
    client, db_session, test_settings, mock_proxmox
):
    """Ausstehende Entwarnung, aber der Knoten faellt noch im Fenster wieder aus: keine
    Entwarnung mehr, sondern der neue Ausfall (still im Fenster, danach einmal hoerbar).
    Ein kurzer Aussetzer unter der Schwelle laesst die Entwarnung dagegen stehen."""
    state, node_id, channel = await _watch_with_channel(client, db_session, test_settings, mock_proxmox)
    _node_offline(state, True)
    for _ in range(3):
        await _run_watch()

    await settings_service.set_global(db_session, "maintenance.windows", _window_now([node_id]))
    _node_offline(state, False)
    await _run_watch()
    _node_offline(state, True)
    await _run_watch()  # ein Aussetzer: unter der Schwelle
    _node_offline(state, False)
    await _run_watch()
    _node_offline(state, True)
    for _ in range(3):
        await _run_watch()

    await settings_service.set_global(db_session, "maintenance.windows", _window_over([node_id]))
    await _run_watch()
    await _run_watch()
    assert [n.title for n in channel.received] == ["Knoten pve1 offline", "Knoten pve1 offline (seit dem Wartungsfenster)"]


@pytest.mark.asyncio
async def test_watch_unreadable_hosts_do_not_end_the_window_early(
    client, db_session, test_settings, mock_proxmox, monkeypatch
):
    """Aus dem Review: sind die Hosts gerade nicht lesbar, fehlt die Host-Zuordnung. Die
    stumme Meldung darf deshalb nicht sofort hoerbar kommen -- das Fenster laeuft ja noch.
    Nach dem Fenster kommt sie mit dem gemerkten Host; die Verbindungsmeldung dann ohne
    "~10 Minuten" (nach einem einstuendigen Fenster waere das falsch)."""
    from nodvard_deck.ext.context import HostsHandle

    state, node_id, channel = await _watch_with_channel(client, db_session, test_settings, mock_proxmox)
    await settings_service.set_global(db_session, "maintenance.windows", _window_now([node_id]))
    _api_down(state, True)
    for _ in range(3):
        await _run_watch()
    assert await _watch_deliveries(db_session) == [("Proxmox 'primary' nicht erreichbar", "suppressed")]

    async def broken_list(self, **_kwargs):
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(HostsHandle, "list", broken_list)
    for _ in range(2):
        assert (await _run_watch())["notified"] == 0, "Fenster läuft noch"
    assert channel.received == []

    await settings_service.set_global(db_session, "maintenance.windows", _window_over([node_id]))
    assert (await _run_watch())["notified"] == 1
    late = channel.received[0]
    assert late.title == "Proxmox 'primary' nicht erreichbar (seit dem Wartungsfenster)"
    assert late.payload == {"host_id": node_id}
    assert "Minuten" not in late.body and "Keine Antwort" in late.body


@pytest.mark.asyncio
async def test_watch_after_window_message_that_lands_in_a_window_again_stays_muted(
    client, db_session, test_settings, mock_proxmox, monkeypatch
):
    """Faellt die Nachmeldung selbst in ein Fenster (ein zweites schliesst direkt an, oder
    es beginnt genau zwischen Frage und Versand), bleibt sie still und kommt erst nach
    diesem Fenster -- einmal."""
    from nodvard_deck.ext.context import NotifyHandle

    state, node_id, channel = await _watch_with_channel(client, db_session, test_settings, mock_proxmox)
    await settings_service.set_global(db_session, "maintenance.windows", _window_now([node_id]))
    _node_offline(state, True)
    for _ in range(3):
        await _run_watch()

    # Erstes Fenster vorbei, zweites laeuft schon: weiter still, kein neuer Eintrag.
    await settings_service.set_global(
        db_session, "maintenance.windows", _window_over([node_id]) + _window_now([node_id])
    )
    for _ in range(2):
        assert (await _run_watch())["notified"] == 0
    assert channel.received == []

    # Wettlauf: die Frage sagt "vorbei", beim Versand laeuft das Fenster doch.
    real_would_suppress = NotifyHandle.would_suppress
    calls = {"n": 0}

    async def once_false(self, **kwargs):
        calls["n"] += 1
        return False if calls["n"] == 1 else await real_would_suppress(self, **kwargs)

    monkeypatch.setattr(NotifyHandle, "would_suppress", once_false)
    assert (await _run_watch())["notified"] == 1
    assert (await _run_watch())["notified"] == 0
    assert channel.received == []

    await settings_service.set_global(db_session, "maintenance.windows", _window_over([node_id]))
    assert (await _run_watch())["notified"] == 1
    assert (await _run_watch())["notified"] == 0
    late = "Knoten pve1 offline (seit dem Wartungsfenster)"
    assert [n.title for n in channel.received] == [late]
    assert await _watch_deliveries(db_session) == [
        ("Knoten pve1 offline", "suppressed"), (late, "suppressed"), (late, "sent"),
    ]


@pytest.mark.asyncio
async def test_watch_runs_against_an_older_core_without_the_new_answers(
    client, db_session, test_settings, mock_proxmox, monkeypatch
):
    """Aelterer Kern: `send()` liefert nichts, `would_suppress()` gibt es nicht. Der
    Waechter laeuft weiter wie vor der Nachmeldung (kein Absturz, nichts nachgeholt)."""
    from nodvard_deck.ext.context import NotifyHandle

    real_send = NotifyHandle.send

    async def old_send(self, notification, *, raise_on_failure=False):
        await real_send(self, notification, raise_on_failure=raise_on_failure)

    monkeypatch.setattr(NotifyHandle, "send", old_send)
    monkeypatch.delattr(NotifyHandle, "would_suppress")

    state, node_id, channel = await _watch_with_channel(client, db_session, test_settings, mock_proxmox)
    await settings_service.set_global(db_session, "maintenance.windows", _window_now([node_id]))
    _node_offline(state, True)
    for _ in range(4):
        await _run_watch()
    await settings_service.set_global(db_session, "maintenance.windows", _window_over([node_id]))
    assert (await _run_watch())["notified"] == 0
    _node_offline(state, False)
    assert (await _run_watch())["notified"] == 1
    assert await _watch_deliveries(db_session) == [
        ("Knoten pve1 offline", "suppressed"), ("Knoten pve1 wieder online", "sent"),
    ]


def test_os_family_from_proxmox_ostype():
    """Eine Windows-VM (ostype win11) stand als "linux" in Nodvard Deck -- System-Kachel und
    -Widget haetten dort Linux-Befehle abgesetzt."""
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "proxmox" / "src"))
    from nodvard_deck_ext_proxmox.capabilities import os_family_from_ostype

    assert [os_family_from_ostype(v) for v in ("win11", "win10", "w2k8", "wxp", "l26", "l24", "solaris", "other", None)] == [
        "windows", "windows", "windows", "windows", "linux", "linux", None, None, None,
    ]


@pytest.mark.asyncio
async def test_discovery_sets_os_family_and_keeps_manual_value_when_unknown(client, db_session, test_settings, mock_proxmox):
    base_url, state = mock_proxmox
    await _setup_proxmox(client, db_session, test_settings, base_url)
    state["guest_configs"]["qemu/100"]["ostype"] = "win11"
    await _run_discovery()
    vm = (await db_session.execute(select(Host).where(Host.name == "proxmox-primary-vm-100"))).scalar_one()
    await db_session.refresh(vm)
    assert vm.os_family == "windows"

    # Unbekannter Typ: Discovery meldet nichts -> eine manuelle Angabe bleibt stehen.
    vm.os_family = "bsd"
    await db_session.commit()
    state["guest_configs"]["qemu/100"]["ostype"] = "other"
    await _run_discovery()
    await db_session.refresh(vm)
    assert vm.os_family == "bsd"


_UMLEITUNG = "Der Server hat die Verbindung auf eine andere Adresse umgeleitet"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [301, 302, 307])
async def test_proxmox_redirect_gives_a_readable_german_error_and_is_not_followed(
    client, db_session, test_settings, mock_proxmox, status_code
):
    """Eine 3xx-Antwort (typisch: http:// statt https:// eingetragen) ergibt den deutschen Satz mit dem
    Statuscode. Die Adresse aus `Location` steht nicht darin, ihr wird nicht gefolgt, und der Rumpf
    (hier absichtlich gueltiges JSON) wird nicht als Antwort gelesen."""
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    state["forced"] = {
        "status": status_code,
        # Dieselbe Adresse wie der Testserver: folgte der Client der Weiterleitung, kaeme `/andere-seite` an
        # und stuende in `forced_hits` (siehe die Gegenprobe unten).
        "headers": {"Location": f"{base_url}/andere-seite"},
        "body": '{"data": {"version": "8.1.3"}}',
        "media_type": "application/json",
    }
    r = await client.post("/api/v1/extensions/proxmox/test", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert _UMLEITUNG in body["message"] and f"HTTP {status_code}" in body["message"]
    assert body["message"] == (
        f"Der Server hat die Verbindung auf eine andere Adresse umgeleitet (HTTP {status_code}). "
        "Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
        "Prüfe die eingetragene Adresse des Servers."
    )
    assert base_url not in r.text and "andere-seite" not in r.text
    assert "Traceback" not in r.text and "JSONDecodeError" not in r.text
    # Genau ein Aufruf je Anfrage: die Weiterleitung wurde nicht verfolgt.
    assert "/andere-seite" not in state["forced_hits"]
    assert state["forced_hits"] and all(path.startswith("/api2/json/") for path in state["forced_hits"])
    # Gegenprobe: ein Client, der Weiterleitungen folgt, haette die zweite Seite abgerufen und sie waere
    # in `forced_hits` aufgetaucht. Ohne diese Probe wuerde der Test auch ein Verfolgen der Weiterleitung uebersehen.
    import contextlib

    import httpx

    state["forced_hits"].clear()
    async with httpx.AsyncClient(follow_redirects=True, max_redirects=2, trust_env=False) as following:
        with contextlib.suppress(httpx.TooManyRedirects):  # der Testserver leitet jede Seite wieder um
            await following.get(f"{base_url}/api2/json/version")
    assert "/andere-seite" in state["forced_hits"]


@pytest.mark.asyncio
async def test_proxmox_answer_without_valid_json_gives_a_readable_error(client, db_session, test_settings, mock_proxmox):
    """Ein 200 mit HTML (z. B. die Anmeldeseite eines Proxys) ergibt einen lesbaren Satz statt eines Tracebacks."""
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    state["forced"] = {"status": 200, "body": "<html><body>Anmelden</body></html>", "media_type": "text/html"}
    r = await client.post("/api/v1/extensions/proxmox/test", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["message"] == "Die Antwort von Proxmox hat nicht das erwartete Format (HTTP 200). Stimmt die Adresse?"
    assert "Traceback" not in r.text and "JSONDecodeError" not in r.text and "Expecting value" not in r.text


_UMLEITUNG_OHNE_CODE = (
    "Der Server hat die Verbindung auf eine andere Adresse umgeleitet. "
    "Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
    "Prüfe die eingetragene Adresse des Servers."
)
_KAPUTTE_ANTWORT = (
    "Der Server hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt. "
    "Prüfe die Adresse in den Einstellungen (http:// oder https://, Port)."
)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [*REDIRECT_ANSWERS, *BROKEN_ANSWERS])
async def test_proxmox_broken_answers_never_reach_the_test_or_the_pages(client, db_session, test_settings, answer):
    """Kaputte Antworten (der echte Parser zitiert Zeilen des Servers, auch `Location`) ergeben in Verbindungstest,
    Aktualisierungs-Uebersicht und Datentraeger-Kachel einen festen Satz. "angreifer" steht nirgends."""
    answers = {**REDIRECT_ANSWERS, **BROKEN_ANSWERS}
    async with raw_http_server(answers[answer]) as base:
        token = await _setup_proxmox(client, db_session, test_settings, base)
        headers = _auth_header(token)
        tested = await client.post("/api/v1/extensions/proxmox/test", headers=headers)
        updates = await client.get("/api/v1/ext/proxmox/updates", headers=headers)
        disks = await client.get("/api/v1/ext/proxmox/widgets/disks", headers=headers)
    for response in (tested, updates, disks):
        assert response.status_code == 200, response.text
        assert "angreifer" not in response.text.lower() and "bytearray" not in response.text
        assert "Technische Meldung" not in response.text and "Traceback" not in response.text
    expected = _UMLEITUNG_OHNE_CODE if answer in REDIRECT_ANSWERS else _KAPUTTE_ANTWORT
    assert tested.json()["message"] == expected
    (error,) = updates.json()["errors"]
    assert error["error"].endswith(
        _UMLEITUNG_OHNE_CODE if answer in REDIRECT_ANSWERS else "Prüfe die Adresse in den Einstellungen (https:// und Port 8006)."
    )
    (row,) = disks.json()["data"]
    assert row["summary"] == error["error"]


@pytest.mark.asyncio
async def test_proxmox_error_reason_from_the_json_reaches_the_pages_but_no_raw_text(
    client, db_session, test_settings, mock_proxmox
):
    """Proxmox nennt den Grund im JSON (`message`): er steht gekuerzt in der Meldung. Ein HTML-Koerper (z. B. die
    Fehlerseite eines Proxys davor) dagegen nie."""
    base_url, state = mock_proxmox
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    state["forced"] = {
        "status": 403,
        "body": '{"data": null, "message": "Permission check failed (/nodes/pve1, Sys.Audit)"}',
        "media_type": "application/json",
    }
    updates = (await client.get("/api/v1/ext/proxmox/updates", headers=headers)).json()
    assert updates["errors"][0]["error"] == (
        "GET /nodes -> HTTP 403: Dem Token fehlt ein Recht für diese Abfrage. "
        "Grund laut Proxmox: Permission check failed (/nodes/pve1, Sys.Audit)."
    )
    state["forced"] = {"status": 502, "body": "<html><h1>angreifer</h1> Bad Gateway</html>", "media_type": "text/html"}
    updates = (await client.get("/api/v1/ext/proxmox/updates", headers=headers)).json()
    assert updates["errors"][0]["error"] == "GET /nodes -> HTTP 502: Proxmox oder ein Proxy davor meldet einen Fehler."


@pytest.mark.asyncio
async def test_proxmox_task_outcome_is_one_short_line(client, db_session, test_settings, mock_proxmox):
    """Der Ausgang eines Tasks sagt, warum etwas nicht ging, und bleibt darum stehen -- einzeilig, ohne
    Steuerzeichen und gekuerzt."""
    base_url, state = mock_proxmox
    state["task_result"] = {"status": "stopped", "exitstatus": "command 'qm start 100' failed\n\x1b[31mexit code 255\x00" + " x" * 400}
    token = await _setup_proxmox(client, db_session, test_settings, base_url)
    await _run_discovery()
    vm_host = (await db_session.execute(select(Host).where(Host.provider_ext_id == "proxmox", Host.kind == "vm"))).scalars().one()
    stop = await client.post(
        f"/api/v1/hosts/{vm_host.id}/actions/vm.stop", json={"reason": "Test"}, headers=_auth_header(token),
    )
    approve = await client.post(f"/api/v1/actions/{stop.json()['id']}/approve", headers=_auth_header(token))
    body = approve.json()
    assert body["status"] == "failed"
    error = body["result"]["error"]
    assert error.startswith("'stop' fehlgeschlagen: command 'qm start 100' failed [31mexit code 255 x x")
    assert "\n" not in error and "\x1b" not in error and "\x00" not in error
    assert len(error) < 260 and error.endswith("…")


def _proxmox_connector_answering(status_code: int, content: bytes, headers: dict | None = None):
    """Ein echter Connector, dessen HTTP-Schicht eine feste `httpx.Response` liefert."""
    import httpx

    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "proxmox" / "src"))
    from nodvard_deck_ext_proxmox.connector import ProxmoxApiError, ProxmoxConnector

    class _Http:
        async def request(self, method, url, **kwargs):
            return httpx.Response(status_code, content=content, headers=headers, request=httpx.Request(method, url))

    connector = ProxmoxConnector(type("C", (), {"http": _Http()})(), base_url="https://x:8006", token_id="a@pve!t", token_secret="geheim-1234")
    return connector, ProxmoxApiError


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [300, 304, 308, 399])
async def test_proxmox_connector_every_3xx_is_a_readable_error(status_code):
    """Der ganze Bereich 300 bis 399 gilt als Weiterleitung, auch 304. Der Rumpf wird nicht gelesen,
    Location und Zugangsdaten stehen nicht im Text."""
    connector, error = _proxmox_connector_answering(
        status_code, b'{"data": {"version": "8.1.3"}}', {"Location": "https://192.168.2.99/andere-seite"}
    )
    with pytest.raises(error) as caught:
        await connector._request("GET", "/version")
    text = str(caught.value)
    assert text.startswith(f"Der Server hat die Verbindung auf eine andere Adresse umgeleitet (HTTP {status_code}).")
    assert "andere-seite" not in text and "geheim-1234" not in text and "PVEAPIToken" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"", b"<html></html>", b"[1, 2]", b"null", b'"text"'])
async def test_proxmox_connector_2xx_without_json_object_is_a_readable_error(content):
    """Leerer Rumpf, HTML oder JSON, das kein Objekt ist: ein lesbarer Satz statt eines rohen Fehlers."""
    connector, error = _proxmox_connector_answering(200, content)
    with pytest.raises(error, match=r"^Die Antwort von Proxmox hat nicht das erwartete Format \(HTTP 200\)\. Stimmt die Adresse\?$"):
        await connector._request("DELETE", "/x")


@pytest.mark.asyncio
async def test_proxmox_connector_4xx_gives_a_fixed_sentence_and_normal_answers_pass():
    """4xx nennt Methode, Pfad, Status und einen festen Satz; der Antworttext (hier Klartext) steht nicht darin.
    `{"data": null}` ist ein Erfolg."""
    connector, error = _proxmox_connector_answering(403, b"Permission check failed")
    with pytest.raises(error, match=r"^GET /version -> HTTP 403: Dem Token fehlt ein Recht für diese Abfrage\.$"):
        await connector._request("GET", "/version")
    connector, _ = _proxmox_connector_answering(200, b'{"data": null}')
    assert await connector._request("DELETE", "/x") is None
