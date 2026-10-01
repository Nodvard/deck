"""Steht NICHT unter backend/src/nodvard_deck -- reiner Dev-/Demo-Helfer, kein Teil des
Kerns oder seiner Tests (die haben ihren eigenen, gleich gebauten Mock direkt in
backend/tests/test_ext_proxmox.py). Startet einen realistischen lokalen Proxmox-VE-
API-Mock fuer die manuelle Pruefung der proxmox-Extension auf einem festen Port,
damit man ihn parallel zum echten Backend laufen lassen kann.

Die Daten sind typische Homelab-Beispieldaten, frei erfunden:
- zwei VMs (100, 102) mit je einem Backup-Job fuer die backups-Extension
  (`/cluster/backup`, `/cluster/tasks`, `POST .../vzdump`): der Job von VM 100
  schlaegt fehl, der von VM 102 laeuft sauber durch;
- eine gestoppte Windows-VM (110, "game-win") fuer die gameserver-Extension;
- zwei LXC-Container (201, 204) mit eigenen VMIDs -- echtes Proxmox teilt sich EINEN
  VMID-Raum ueber qemu+lxc auf einem Knoten, eine Kollision waere unrealistisch.

    python scripts/dev_mock_proxmox.py --port 8899
"""

from __future__ import annotations

import argparse
import time

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request

TOKEN_ID = "root@pam!lattice"
TOKEN_SECRET = "dev-secret-uuid"
EXPECTED_AUTH = f"PVEAPIToken={TOKEN_ID}={TOKEN_SECRET}"

_now = int(time.time())

state = {
    "nodes": {"pve1": {"node": "pve1", "status": "online", "cpu": 0.18, "maxmem": 34_000_000_000}},
    "vms": {
        "100": {
            "vmid": 100, "name": "demo-vm", "status": "running",
            "cpu": 0.07, "mem": 700_000_000, "maxmem": 2_147_483_648, "uptime": 4200,
        },
        "102": {
            "vmid": 102, "name": "monitoring", "status": "running",
            "cpu": 0.02, "mem": 500_000_000, "maxmem": 1_073_741_824, "uptime": 4200,
        },
        "110": {
            "vmid": 110, "name": "game-win", "status": "stopped",
            "cpu": 0.0, "mem": 0, "maxmem": 6_442_450_944, "uptime": 0,
        },
    },
    "containers": {
        "201": {
            "vmid": 201, "name": "docker-lxc", "status": "running",
            "cpu": 0.05, "mem": 900_000_000, "maxmem": 2_147_483_648, "uptime": 51200,
        },
        "204": {
            "vmid": 204, "name": "bastion", "status": "running",
            "cpu": 0.01, "mem": 200_000_000, "maxmem": 536_870_912, "uptime": 51200,
        },
    },
    "backup_jobs": [
        {"id": "backup-demo-vm", "vmid": "100", "storage": "backup-nas", "schedule": "*-*-* 01:00", "enabled": 1, "node": "pve1"},
        {"id": "backup-monitoring", "vmid": "102", "storage": "backup-nas", "schedule": "*-*-* 01:30", "enabled": 1, "node": "pve1"},
    ],
    "backup_tasks": [
        {
            "upid": "UPID:pve1:AAAA:vzdump:100:", "node": "pve1", "type": "vzdump", "id": "100",
            "status": "vma_queue_write: write error - Broken pipe",
            "starttime": _now - 3600, "endtime": _now - 3300,
        },
        {
            "upid": "UPID:pve1:BBBB:vzdump:102:", "node": "pve1", "type": "vzdump", "id": "102",
            "status": "OK", "starttime": _now - 3600, "endtime": _now - 3500,
        },
    ],
}

app = FastAPI(title="Proxmox-API-Mock (Dev)")


def _check_auth(authorization: str | None = Header(default=None)) -> None:
    if authorization != EXPECTED_AUTH:
        raise HTTPException(status_code=401, detail="Ungueltiges PVEAPIToken.")


@app.get("/api2/json/version")
async def version(_: None = Depends(_check_auth)) -> dict:
    return {"data": {"version": "8.1.3"}}


@app.get("/api2/json/nodes")
async def list_nodes(_: None = Depends(_check_auth)) -> dict:
    return {"data": list(state["nodes"].values())}


@app.get("/api2/json/nodes/{node}/status")
async def node_status(node: str, _: None = Depends(_check_auth)) -> dict:
    return {"data": {"cpu": 0.18, "mem": 14_000_000_000, "maxmem": 34_000_000_000, "uptime": 987654}}


@app.get("/api2/json/nodes/{node}/qemu")
async def list_qemu(node: str, _: None = Depends(_check_auth)) -> dict:
    return {"data": list(state["vms"].values())}


@app.get("/api2/json/nodes/{node}/qemu/{vmid}/status/current")
async def qemu_status(node: str, vmid: str, _: None = Depends(_check_auth)) -> dict:
    return {"data": state["vms"][vmid]}


@app.post("/api2/json/nodes/{node}/qemu/{vmid}/status/{action}")
async def qemu_action(node: str, vmid: str, action: str, _: None = Depends(_check_auth)) -> dict:
    vm = state["vms"][vmid]
    print(f"[mock-proxmox] {action} node={node} vmid={vmid}")
    if action == "start":
        vm["status"] = "running"
    elif action == "stop":
        vm["status"] = "stopped"
    return {"data": f"UPID:mock:{action}:1:{vmid}::"}


@app.post("/api2/json/nodes/{node}/qemu/{vmid}/snapshot")
async def qemu_snapshot(node: str, vmid: str, request: Request, _: None = Depends(_check_auth)) -> dict:
    body = await request.json()
    print(f"[mock-proxmox] snapshot node={node} vmid={vmid} name={body.get('snapname')}")
    return {"data": "UPID:mock:snapshot:1::"}


@app.get("/api2/json/nodes/{node}/lxc")
async def list_lxc(node: str, _: None = Depends(_check_auth)) -> dict:
    return {"data": list(state["containers"].values())}


@app.get("/api2/json/nodes/{node}/lxc/{vmid}/status/current")
async def lxc_status(node: str, vmid: str, _: None = Depends(_check_auth)) -> dict:
    return {"data": state["containers"][vmid]}


@app.post("/api2/json/nodes/{node}/lxc/{vmid}/status/{action}")
async def lxc_action(node: str, vmid: str, action: str, _: None = Depends(_check_auth)) -> dict:
    ct = state["containers"][vmid]
    print(f"[mock-proxmox] lxc {action} node={node} vmid={vmid}")
    if action == "start":
        ct["status"] = "running"
    elif action == "stop":
        ct["status"] = "stopped"
    return {"data": f"UPID:mock:{action}:1:{vmid}::"}


@app.post("/api2/json/nodes/{node}/lxc/{vmid}/snapshot")
async def lxc_snapshot(node: str, vmid: str, request: Request, _: None = Depends(_check_auth)) -> dict:
    body = await request.json()
    print(f"[mock-proxmox] lxc snapshot node={node} vmid={vmid} name={body.get('snapname')}")
    return {"data": "UPID:mock:snapshot:1::"}


@app.get("/api2/json/cluster/backup")
async def list_backup_jobs(_: None = Depends(_check_auth)) -> dict:
    return {"data": state["backup_jobs"]}


@app.get("/api2/json/cluster/tasks")
async def list_cluster_tasks(_: None = Depends(_check_auth)) -> dict:
    return {"data": state["backup_tasks"]}


@app.post("/api2/json/nodes/{node}/vzdump")
async def run_vzdump(node: str, request: Request, _: None = Depends(_check_auth)) -> dict:
    form = await request.form()
    vmid = form.get("vmid", "?")
    upid = f"UPID:{node}:retry:vzdump:{vmid}:"
    print(f"[mock-proxmox] vzdump (Retry) node={node} vmid={vmid}")
    state["backup_tasks"].insert(0, {
        "upid": upid, "node": node, "type": "vzdump", "id": str(vmid),
        "status": "OK", "starttime": int(time.time()), "endtime": int(time.time()) + 5,
    })
    return {"data": upid}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8899)
    args = parser.parse_args()
    print(f"token_id={TOKEN_ID} token_secret={TOKEN_SECRET}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
