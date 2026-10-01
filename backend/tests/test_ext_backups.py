"""backups-Extension -- Proxmox-VZDump-Sichtbarkeit + Retry.
Getestet gegen einen echten, lokal laufenden
Proxmox-Backup-API-Mock (echtes HTTP/JSON, kein Monkeypatch von `httpx`) --
dasselbe Prinzip wie `test_ext_proxmox.py`.
"""

from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

import pytest
import pytest_asyncio
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy import select

from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import ExtensionRecord
from nodvard_deck.services import extensions as extensions_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
_TOKEN_ID = "root@pam!lattice"
_TOKEN_SECRET = "test-secret-uuid"
_EXPECTED_AUTH = f"PVEAPIToken={_TOKEN_ID}={_TOKEN_SECRET}"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _build_mock_backup_app() -> tuple[FastAPI, dict]:
    """Realistisch genug: `id`/`vmid`/`storage`/`schedule`/`enabled` fuer Jobs
    (`/cluster/backup`), `type`/`id`/`node`/`status`/`starttime`/`endtime` fuer
    Tasks (`/cluster/tasks`, kein server-seitiger Typ-Filter -- genau wie die echte
    Proxmox-API), `POST /nodes/{node}/vzdump` legt einen neuen erfolgreichen Task an."""
    state = {
        "jobs": [
            {"id": "backup-nextcloud", "vmid": "100", "storage": "backup-pve1", "schedule": "*-*-* 02:30,22:30", "enabled": 1, "node": "pve2",
             "prune-backups": "keep-last=3,keep-weekly=2", "mode": "snapshot", "digest": "d1"},
            {"id": "backup-zabbix", "vmid": "102", "storage": "backup-pve1", "schedule": "*-*-* 22:30", "enabled": 1, "node": "pve2"},
        ],
        "tasks": [
            # VM 100: Schreibfehler mit Broken Pipe.
            {"upid": "UPID:pve2:1", "node": "pve2", "type": "vzdump", "id": "100", "status": "vma_queue_write: write error - Broken pipe", "starttime": 1000, "endtime": 3280},
            # VM 102: gesund.
            {"upid": "UPID:pve2:2", "node": "pve2", "type": "vzdump", "id": "102", "status": "OK", "starttime": 2000, "endtime": 2300},
        ],
        "vzdump_calls": [],
        # Andere Tasks, die sich im Ringpuffer von /cluster/tasks vordraengeln
        # (vncproxy, qmstart ...) -- nur dort sichtbar, nicht im vzdump-Index.
        "ring_noise": [],
        # Backup-Speicher und -Dateien in der Form der echten API (backup-pve1 + backup-pi
        # sind NFS = shared, `local` ist knotenlokal).
        "backup_storages": [
            {"storage": "backup-pve1", "shared": 1, "active": 1},
            {"storage": "backup-pi", "shared": 1, "active": 1},
            {"storage": "local", "shared": 0, "active": 1},
            {"storage": "alt-offline", "shared": 1, "active": 0},
        ],
        "backup_files": {
            "backup-pve1": [
                {"volid": "backup-pve1:backup/vzdump-qemu-102-a.vma.zst", "vmid": 102, "subtype": "qemu", "ctime": 1000, "size": 3_800_000_000},
                {"volid": "backup-pve1:backup/vzdump-qemu-102-b.vma.zst", "vmid": 102, "subtype": "qemu", "ctime": 3000, "size": 3_900_000_000},
            ],
            "backup-pi": [
                # LXC 104 auf pve1 -- sichtbar ueber die pve2-Verbindung.
                {"volid": "backup-pi:backup/vzdump-lxc-104-alt.tar.zst", "vmid": 104, "subtype": "lxc", "ctime": 500, "size": 700_000_000},
                {"volid": "backup-pi:backup/vzdump-qemu-102-c.vma.zst", "vmid": 102, "subtype": "qemu", "ctime": 2000, "size": 3_850_000_000},
                # VMID 100 als CONTAINER -- auf "primary" ist 100 eine VM: nicht deren Backup.
                {"volid": "backup-pi:backup/vzdump-lxc-100-x.tar.zst", "vmid": 100, "subtype": "lxc", "ctime": 700, "size": 100_000_000},
            ],
            "local": [
                # Gast gibt es nirgends (mehr) -> verwaist.
                {"volid": "local:backup/vzdump-qemu-999-old.vma.zst", "vmid": 999, "ctime": 100, "size": 5_000_000_000},
            ],
        },
        # Proxmox' eigene Auswertung "von keinem Job erfasst" -- Form wie die echte API.
        "not_backed_up": [],
        # /cluster/resources?type=vm -- nur fuer Jobs ohne VMID-Liste (all/pool).
        "resources": [
            {"vmid": 100, "node": "pve2", "type": "qemu", "name": "docker"},
            {"vmid": 102, "node": "pve2", "type": "qemu", "name": "Zabbix"},
            {"vmid": 103, "node": "pve2", "type": "qemu", "name": "ki-server", "pool": "kritisch"},
            {"vmid": 104, "node": "pve1", "type": "lxc", "name": "Teleport", "pool": "kritisch"},
        ],
    }
    app = FastAPI()

    def _check_auth(authorization: str | None = Header(default=None)) -> None:
        if authorization != _EXPECTED_AUTH:
            raise HTTPException(status_code=401, detail="Ungueltiges PVEAPIToken.")

    @app.get("/api2/json/cluster/backup")
    async def list_jobs(_: None = Depends(_check_auth)) -> dict:
        return {"data": state["jobs"]}

    @app.get("/api2/json/cluster/backup/{job_id}")
    async def get_job(job_id: str, _: None = Depends(_check_auth)) -> dict:
        job = next((j for j in state["jobs"] if j["id"] == job_id), None)
        if job is None:
            raise HTTPException(status_code=500, detail=f"job '{job_id}' does not exist")
        return {"data": job}

    @app.post("/api2/json/cluster/backup")
    async def create_job(request: Request, _: None = Depends(_check_auth)) -> dict:
        state.setdefault("job_creates", []).append(await request.json())
        return {"data": None}

    @app.put("/api2/json/cluster/backup/{job_id}")
    async def update_job(job_id: str, request: Request, _: None = Depends(_check_auth)) -> dict:
        params = await request.json()
        job = next(j for j in state["jobs"] if j["id"] == job_id)
        if params.get("digest") and params["digest"] != job.get("digest"):
            raise HTTPException(status_code=500, detail="detected modified configuration - file changed by other user?")
        state.setdefault("job_updates", []).append((job_id, params))
        for key in str(params.get("delete") or "").split(","):
            job.pop(key, None)
        job.update({k: v for k, v in params.items() if k not in ("delete", "digest")})
        job["digest"] = job.get("digest", "") + "+"
        return {"data": None}

    @app.get("/api2/json/nodes/{node}/storage")
    async def node_storages(node: str, request: Request, _: None = Depends(_check_auth)) -> dict:
        assert request.query_params.get("content") == "backup"
        return {"data": state["backup_storages"]}

    @app.get("/api2/json/nodes/{node}/storage/{storage}/content")
    async def storage_content(node: str, storage: str, request: Request, _: None = Depends(_check_auth)) -> dict:
        assert request.query_params.get("content") == "backup"
        state.setdefault("content_calls", []).append((node, storage))
        return {"data": state["backup_files"].get(storage, [])}

    @app.get("/api2/json/cluster/backup-info/not-backed-up")
    async def not_backed_up(_: None = Depends(_check_auth)) -> dict:
        return {"data": state["not_backed_up"]}

    @app.get("/api2/json/cluster/resources")
    async def resources(request: Request, _: None = Depends(_check_auth)) -> dict:
        assert request.query_params.get("type") == "vm"
        return {"data": state["resources"]}

    @app.get("/api2/json/nodes/{node}/{kind}/{vmid}/config")
    async def guest_config(node: str, kind: str, vmid: str, _: None = Depends(_check_auth)) -> dict:
        config = state.get("configs", {}).get((kind, vmid))
        if config is None:
            raise HTTPException(status_code=500, detail=f"Configuration file '{kind}/{vmid}.conf' does not exist")
        return {"data": config}

    @app.get("/api2/json/nodes")
    async def list_nodes(_: None = Depends(_check_auth)) -> dict:
        names = sorted({t["node"] for t in state["tasks"]} | {j["node"] for j in state["jobs"] if j.get("node")})
        return {"data": [{"node": n, "status": "online"} for n in names]}

    @app.get("/api2/json/nodes/{node}/tasks")
    async def node_tasks(node: str, request: Request, _: None = Depends(_check_auth)) -> dict:
        """Wie die echte API: voller Task-Index des Knotens, serverseitig nach
        `typefilter` gefiltert, neueste zuerst; `source=archive` (Default) zeigt nur
        abgeschlossene Tasks."""
        params = request.query_params
        rows = [t for t in state["tasks"] if t["node"] == node]
        if params.get("typefilter"):
            rows = [t for t in rows if t["type"] == params["typefilter"]]
        if params.get("source", "archive") == "archive":
            rows = [t for t in rows if "endtime" in t]
        rows.sort(key=lambda t: t.get("starttime", 0), reverse=True)
        return {"data": rows[: int(params.get("limit", 50))]}

    @app.get("/api2/json/cluster/tasks")
    async def list_tasks(request: Request, _: None = Depends(_check_auth)) -> dict:
        # Live gegen einen echten Proxmox-Knoten gefunden: die echte
        # Proxmox-VE-9.2-API lehnt JEDEN Query-Parameter auf diesem Endpunkt ab
        # ("limit": "property is not defined in schema"). Ein bisher grosszuegiger
        # Mock, der `limit` klaglos schluckte, haette diesen Bug nie gezeigt --
        # deshalb hier bewusst strikt wie das echte Verhalten.
        if request.query_params:
            raise HTTPException(
                status_code=400,
                detail={"message": "Parameter verification failed.\n", "errors": {k: "property is not defined in schema" for k in request.query_params}},
            )
        # Ringpuffer wie echt: nur die letzten 25 Tasks, beliebigen Typs.
        return {"data": (state["tasks"] + state["ring_noise"])[-25:]}

    @app.post("/api2/json/nodes/{node}/vzdump")
    async def run_vzdump(node: str, _: None = Depends(_check_auth)) -> dict:
        upid = f"UPID:{node}:new"
        state["vzdump_calls"].append({"node": node})
        state["tasks"].append({"upid": upid, "node": node, "type": "vzdump", "id": "100", "status": "OK", "starttime": 9000, "endtime": 9300})
        return {"data": upid}

    return app, state


@pytest_asyncio.fixture
async def mock_backup_api():
    app, state = _build_mock_backup_app()

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


async def _setup_backups(client, db_session, test_settings, base_url: str, *, name: str = "primary") -> str:
    """Ein-Verbindungs-Fall -- siehe `_setup_backups_connections()` fuer mehrere
    gleichzeitige Verbindungen."""
    return await _setup_backups_connections(
        client, db_session, test_settings, [{"name": name, "base_url": base_url, "token_id": _TOKEN_ID}]
    )


async def _setup_backups_connections(client, db_session, test_settings, connections: list[dict]) -> str:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)

    token = await _bootstrap_owner(client)
    enabled = await client.post("/api/v1/extensions/backups/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text

    record = await db_session.get(ExtensionRecord, "backups")
    record.settings = {"connections": connections}
    await db_session.flush()

    for conn in connections:
        token_resp = await client.post(
            f"/api/v1/ext/backups/connections/{conn['name']}/token",
            json={"value": _TOKEN_SECRET}, headers=_auth_header(token),
        )
        assert token_resp.status_code == 204, token_resp.text
    return token


@pytest.mark.asyncio
async def test_list_jobs_shows_the_real_original_finding_structured_not_as_prose(
    client, db_session, test_settings, mock_backup_api
):
    """Der Kernfall dieser Extension: VM 100 hat einen fehlgeschlagenen
    Lauf (Broken Pipe), VM 102 einen erfolgreichen -- beide muessen als
    klar unterscheidbare, strukturierte Datensaetze erscheinen."""
    base_url, _state = mock_backup_api
    token = await _setup_backups(client, db_session, test_settings, base_url)

    res = await client.get("/api/v1/ext/backups/jobs", headers=_auth_header(token))
    assert res.status_code == 200, res.text
    by_vmid = {j["vmid"]: j for j in res.json()}

    assert by_vmid["100"]["last_status"] == "failed"
    assert by_vmid["100"]["storage"] == "backup-pve1"
    assert by_vmid["102"]["last_status"] == "ok"


@pytest.mark.asyncio
async def test_job_history_returns_all_matching_tasks_newest_first(client, db_session, test_settings, mock_backup_api):
    base_url, state = mock_backup_api
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    jobs = (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json()
    job_ref = next(j["job_ref"] for j in jobs if j["vmid"] == "100")

    # Zweiter, aelterer Task fuer dieselbe VMID, damit "newest first" pruefbar ist.
    state["tasks"].insert(0, {"upid": "UPID:pve2:0", "node": "pve2", "type": "vzdump", "id": "100", "status": "OK", "starttime": 500, "endtime": 600})

    history = await client.get(f"/api/v1/ext/backups/jobs/{job_ref}/history", headers=headers)
    assert history.status_code == 200, history.text
    body = history.json()
    assert [h["started_at"] for h in body] == [1000, 500]
    assert body[0]["status"] == "failed"
    assert body[1]["status"] == "ok"


@pytest.mark.asyncio
async def test_retry_proposes_through_the_gate_and_reaches_the_real_mock(
    client, db_session, test_settings, mock_backup_api
):
    base_url, state = mock_backup_api
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    jobs = (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json()
    job_ref = next(j["job_ref"] for j in jobs if j["vmid"] == "100")

    retry = await client.post(f"/api/v1/ext/backups/jobs/{job_ref}/retry", headers=headers)
    assert retry.status_code == 200, retry.text
    body = retry.json()
    assert body["status"] == "proposed"  # autonomy.mode=propose ist der Default
    # "risk" steht mit drin, damit BackupsPage.tsx bei
    # status="proposed" pruefen kann, ob der Nutzer actions.approve:<risk> hat,
    # bevor sie automatisch nachbestaetigt.
    assert body["risk"] == "medium"

    action_id = body["action_id"]
    approve = await client.post(f"/api/v1/actions/{action_id}/approve", headers=headers)
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "succeeded"
    assert state["vzdump_calls"] == [{"node": "pve2"}]


@pytest.mark.asyncio
async def test_job_edit_goes_through_the_gate_and_less_retention_is_high_risk(
    client, db_session, test_settings, mock_backup_api
):
    """Backup-Jobs bearbeiten. Zeitplan aendern = mittel; weniger
    aufbewahren = HOCH (Proxmox loescht beim naechsten Lauf). digest wird mitgeschickt."""
    base_url, state = mock_backup_api
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    jobs = (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json()
    job_ref = next(j["job_ref"] for j in jobs if j["vmid"] == "100")

    config = (await client.get(f"/api/v1/ext/backups/jobs/{job_ref}/config", headers=headers)).json()
    assert config == {
        "job_id": "backup-nextcloud", "schedule": "*-*-* 02:30,22:30", "enabled": True, "storage": "backup-pve1",
        "mode": "snapshot", "keep-last": 3, "keep-daily": 0, "keep-weekly": 2, "keep-monthly": 0, "keep-yearly": 0,
    }

    r = await client.post(f"/api/v1/ext/backups/jobs/{job_ref}/edit", json={"changes": {"schedule": "sun 03:00", "keep-monthly": 6}}, headers=headers)
    assert r.status_code == 200, r.text
    proposed = r.json()
    assert (proposed["status"], proposed["risk"]) == ("proposed", "medium")
    assert "job_updates" not in state, "vor der Freigabe darf nichts geschrieben sein"
    approved = (await client.post(f"/api/v1/actions/{proposed['action_id']}/approve", headers=headers)).json()
    assert approved["status"] == "succeeded", approved
    assert state["job_updates"] == [("backup-nextcloud", {"schedule": "sun 03:00", "prune-backups": "keep-last=3,keep-weekly=2,keep-monthly=6", "digest": "d1"})]
    assert approved["result"]["output"] == "Geändert: Zeitplan *-*-* 02:30,22:30 → sun 03:00, Monatliche – → 6."

    shrink = (await client.post(f"/api/v1/ext/backups/jobs/{job_ref}/edit", json={"changes": {"keep-last": 1}}, headers=headers)).json()
    assert shrink["risk"] == "high"

    for bad in ({"notes-template": "x"}, {"schedule": "02:30; rm -rf /"}, {"keep-last": -1}, {"storage": "../etc"}):
        r = await client.post(f"/api/v1/ext/backups/jobs/{job_ref}/edit", json={"changes": bad}, headers=headers)
        assert r.status_code == 422, bad
    assert (await client.get("/api/v1/ext/backups/jobs/pve2--gibtsnicht--1/config", headers=headers)).status_code == 404


def test_job_edit_reads_the_retention_object_and_keeps_the_other_rules():
    """Proxmox liefert `prune-backups` als Objekt ({"keep-daily": "7", ...}).
    Vorher las parse_prune() daraus ueberall 0 -- "Letzte behalten: 2" schickte dann
    nur noch keep-last=2, taegliche/woechentliche Regeln verschwanden still, als MITTEL."""
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "backups" / "src"))
    from nodvard_deck_ext_backups.job_edit import (
        JobEditError,
        build_job_update,
        current_values,
        parse_prune,
    )

    job = {"id": "j", "schedule": "02:00", "prune-backups": {"keep-daily": "7", "keep-weekly": "4"}, "digest": "d"}
    values = current_values(job)
    assert (values["keep-last"], values["keep-daily"], values["keep-weekly"], values["keep-monthly"]) == (0, 7, 4, 0)
    assert "keep-all" not in values

    params, _diff, destructive = build_job_update(job, {"keep-last": 2})
    assert params == {"prune-backups": "keep-last=2,keep-daily=7,keep-weekly=4", "digest": "d"}
    assert destructive is False  # eine Regel mehr behaelt mehr, nicht weniger

    _params, _diff, destructive = build_job_update(job, {"keep-daily": 3})
    assert destructive is True

    # Die Textform gibt es weiterhin.
    assert parse_prune("keep-last=3, keep-daily=7") == {"keep-last": 3, "keep-daily": 7}

    # Stuendliche Regel ist in Nodvard Deck nicht editierbar -- sie darf aber auch nicht verschwinden.
    params, _diff, _destructive = build_job_update({"prune-backups": {"keep-hourly": "24", "keep-daily": "7"}}, {"keep-weekly": 2})
    assert params["prune-backups"] == "keep-hourly=24,keep-daily=7,keep-weekly=2"

    # "Alle behalten": Aufbewahrung nur in Proxmox aenderbar, anderes schon.
    keep_all = {"schedule": "02:00", "prune-backups": {"keep-all": "1"}}
    assert current_values(keep_all)["keep-all"] is True
    with pytest.raises(JobEditError, match="alle Sicherungen"):
        build_job_update(keep_all, {"keep-last": 2})
    params, _diff, _destructive = build_job_update(keep_all, {"schedule": "03:00"})
    assert params == {"schedule": "03:00"}

    # Ohne eigene Regel galt die (unbekannte) des Speichers -- eine eigene Regel kann weniger behalten.
    _params, _diff, destructive = build_job_update({"schedule": "02:00"}, {"keep-last": 2})
    assert destructive is True


@pytest.mark.asyncio
async def test_job_edit_with_retention_object_through_the_gate(client, db_session, test_settings, mock_backup_api):
    """Retention-Objekt ueber die echte Route: Formular zeigt 7/4, weniger Taegliche ist HOCH."""
    base_url, state = mock_backup_api
    state["jobs"][0]["prune-backups"] = {"keep-daily": "7", "keep-weekly": "4"}
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    job_ref = next(j["job_ref"] for j in (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json() if j["vmid"] == "100")

    config = (await client.get(f"/api/v1/ext/backups/jobs/{job_ref}/config", headers=headers)).json()
    assert (config["keep-last"], config["keep-daily"], config["keep-weekly"]) == (0, 7, 4)

    shrink = (await client.post(f"/api/v1/ext/backups/jobs/{job_ref}/edit", json={"changes": {"keep-daily": 2}}, headers=headers)).json()
    assert shrink["risk"] == "high"

    more = (await client.post(f"/api/v1/ext/backups/jobs/{job_ref}/edit", json={"changes": {"keep-last": 2}}, headers=headers)).json()
    assert more["risk"] == "medium"
    approved = (await client.post(f"/api/v1/actions/{more['action_id']}/approve", headers=headers)).json()
    assert approved["status"] == "succeeded", approved
    assert state["job_updates"][-1] == ("backup-nextcloud", {"prune-backups": "keep-last=2,keep-daily=7,keep-weekly=4", "digest": "d1"})


@pytest.mark.asyncio
async def test_create_job_for_an_unprotected_guest_through_the_gate(client, db_session, test_settings, mock_backup_api):
    base_url, state = mock_backup_api
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    connection = (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json()[0]["connection"]
    values = {"schedule": "sun 02:00", "storage": "backup-pve1", "keep-last": 3}
    r = await client.post(f"/api/v1/ext/backups/unprotected/{connection}/104/job", json={"values": values}, headers=headers)
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["risk"]) == ("proposed", "low")
    assert "job_creates" not in state
    # D-15: vorgeschlagen hat der klickende Nutzer, nicht "die Extension".
    proposed = (await client.get(f"/api/v1/actions/{r.json()['action_id']}", headers=headers)).json()
    assert (proposed["proposed_by_type"], proposed["ext_id"]) == ("user", "backups")
    approved = (await client.post(f"/api/v1/actions/{r.json()['action_id']}/approve", headers=headers)).json()
    assert approved["status"] == "succeeded", approved
    assert state["job_creates"] == [{"schedule": "sun 02:00", "storage": "backup-pve1", "prune-backups": "keep-last=3", "vmid": "104"}]
    assert approved["result"]["output"] == "Backup-Job für VMID 104 angelegt: sun 02:00 auf backup-pve1, behält 3× letzte behalten."

    for bad_vmid, bad_values in (("104", {"storage": "backup-pve1"}), ("1;2", values), ("104", {**values, "notes-template": "x"})):
        bad = await client.post(f"/api/v1/ext/backups/unprotected/{connection}/{bad_vmid}/job", json={"values": bad_values}, headers=headers)
        assert bad.status_code == 422, (bad_vmid, bad_values)
    assert (await client.post("/api/v1/ext/backups/unprotected/gibtsnicht/104/job", json={"values": values}, headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_retry_unknown_job_ref_returns_404(client, db_session, test_settings, mock_backup_api):
    base_url, _state = mock_backup_api
    token = await _setup_backups(client, db_session, test_settings, base_url)

    res = await client.post("/api/v1/ext/backups/jobs/primary--does-not-exist--999/retry", headers=_auth_header(token))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_job_without_any_task_history_shows_unknown_not_a_crash(client, db_session, test_settings, mock_backup_api):
    """Ein Job, der noch NIE gelaufen ist, darf nicht wie ein fehlgeschlagener
    aussehen -- das waere eine falsche Alarmierung."""
    base_url, state = mock_backup_api
    state["jobs"].append({"id": "backup-new", "vmid": "999", "storage": "backup-pve1", "schedule": "*-*-* 03:00", "enabled": 1, "node": "pve2"})
    token = await _setup_backups(client, db_session, test_settings, base_url)

    res = await client.get("/api/v1/ext/backups/jobs", headers=_auth_header(token))
    by_vmid = {j["vmid"]: j for j in res.json()}
    assert by_vmid["999"]["last_status"] == "unknown"
    assert by_vmid["999"]["last_run_at"] is None


@pytest.mark.asyncio
async def test_widget_data_endpoint_returns_the_wrapped_envelope(client, db_session, test_settings, mock_backup_api):
    base_url, _state = mock_backup_api
    token = await _setup_backups(client, db_session, test_settings, base_url)

    res = await client.get("/api/v1/ext/backups/widgets/summary", headers=_auth_header(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert "data" in body and "meta" in body
    assert len(body["data"]) == 2
    # Angezeigt wird ein deutsches Wort, die Farbe haengt weiter am
    # Maschinenwert -- "ok"/"failed" standen vorher roh auf dem Dashboard.
    labels = {"ok": "erfolgreich", "failed": "fehlgeschlagen", "running": "läuft", "unknown": "unbekannt"}
    for job in body["data"]:
        assert job["last_status_label"] == labels[job["last_status"]]
    widgets = (await client.get("/api/v1/widgets", headers=_auth_header(token))).json()
    spec = next(w for w in widgets if w["ext_id"] == "backups")
    assert spec["view"]["item"]["badge"] == {"text": "{{ last_status_label }}", "tone": "{{ tone }}"}
    assert {job["last_status"]: job["tone"] for job in body["data"]} == {"failed": "danger", "ok": "good"}


@pytest.mark.asyncio
async def test_health_does_not_hide_one_broken_connection_behind_a_working_one(
    client, db_session, test_settings, mock_backup_api
):
    """Korrektur an der ersten Multi-Instanz-Fassung: `healthy=True`, sobald
    IRGENDEINE Verbindung erreichbar war, verdeckte einen echten Teilausfall hinter
    dem Erfolg der anderen. Beweist die Schaerfung: EINE kaputte von ZWEI
    Verbindungen macht die gesamte Extension `healthy=False`, UND `details` zeigt
    fuer jede Verbindung einzeln und explizit, welche es war."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()

    base_url, _state = mock_backup_api
    await _setup_backups_connections(
        client, db_session, test_settings,
        [
            {"name": "pve2", "base_url": base_url, "token_id": _TOKEN_ID},
            {"name": "pve1", "base_url": f"http://127.0.0.1:{dead_port}", "token_id": _TOKEN_ID},
        ],
    )

    runtime = get_extension_runtime()
    loaded = runtime.loaded["backups"]
    report = await loaded.instance.health(loaded.ctx)

    assert report.healthy is False  # nicht "True, weil pve2 ja laeuft"
    assert report.details["pve2"]["healthy"] is True
    assert report.details["pve1"]["healthy"] is False
    assert "pve1" in report.message
    assert "pve2" in report.message  # auch der funktionierende Teil bleibt sichtbar


@pytest.mark.asyncio
async def test_widget_and_jobs_endpoints_degrade_cleanly_instead_of_500_on_api_error(
    client, db_session, test_settings
):
    """Live gegen einen echten Proxmox-Knoten gefunden: `ProxmoxBackupApiError`
    fehlte in VIER `except (RuntimeError, NodvardError)`-Klauseln in __init__.py -- ein
    echter Proxmox-API-Fehler (hier: unerreichbar, im echten Fund ein abgelehnter
    Query-Parameter) lief als roher 500 durch statt sauber behandelt zu werden.
    Statt einer leeren Liste ("Keine Backup-Jobs konfiguriert") bzw. 409
    zeigen `/widgets/summary` und `/jobs` jetzt eine Zeile "nicht erreichbar" je
    toter Verbindung -- weiterhin nie ein 500 mit Traceback."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()

    token = await _setup_backups(client, db_session, test_settings, f"http://127.0.0.1:{dead_port}")

    widget = await client.get("/api/v1/ext/backups/widgets/summary", headers=_auth_header(token))
    assert widget.status_code == 200, widget.text
    [row] = widget.json()["data"]
    assert (row["connection"], row["job_ref"], row["last_status_label"], row["tone"]) == ("primary", "", "nicht erreichbar", "danger")

    jobs = await client.get("/api/v1/ext/backups/jobs", headers=_auth_header(token))
    assert jobs.status_code == 200, jobs.text
    [row] = jobs.json()
    assert (row["connection"], row["last_status"]) == ("primary", "unreachable")
    assert "/cluster/backup" in row["error"]


@pytest.mark.asyncio
async def test_one_unreachable_connection_does_not_hide_the_other_jobs_or_stop_the_watch(
    client, db_session, test_settings, mock_backup_api
):
    """pve1 aus, und das Backup von VM 100 auf pve2 schlaegt fehl (Broken
    Pipe). Vorher: Seite "HTTP 409", Kachel "Keine
    Backup-Jobs konfiguriert", Waechter stuerzte ab -- keine Meldung. Jetzt bleiben
    Jobs von pve2 sichtbar, pve1 steht als "nicht erreichbar" da, und der Waechter meldet
    den Fehlschlag, ohne die bereits gemeldeten Fehlschlaege von pve1 zu vergessen."""
    import json as _json

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()

    base_url, _state = mock_backup_api
    token = await _setup_backups_connections(
        client, db_session, test_settings,
        [
            {"name": "pve1", "base_url": f"http://127.0.0.1:{dead_port}", "token_id": _TOKEN_ID},
            {"name": "primary", "base_url": base_url, "token_id": _TOKEN_ID},
        ],
    )
    headers = _auth_header(token)

    res = await client.get("/api/v1/ext/backups/jobs", headers=headers)
    assert res.status_code == 200, res.text
    rows = res.json()
    real = {j["vmid"]: j for j in rows if j["connection"] == "primary"}
    assert (real["100"]["last_status"], real["102"]["last_status"]) == ("failed", "ok")
    [dead] = [j for j in rows if j["connection"] == "pve1"]
    assert (dead["job_ref"], dead["last_status"]) == ("", "unreachable")

    widget = (await client.get("/api/v1/ext/backups/widgets/summary", headers=headers)).json()["data"]
    assert (widget[0]["connection"], widget[0]["last_status_label"], widget[0]["tone"]) == ("pve1", "nicht erreichbar", "danger")
    assert {r["vmid"] for r in widget if r["connection"] == "primary"} == {"100", "102"}

    # Ein frueher gemeldeter Fehlschlag auf pve1 darf nicht vergessen werden, nur weil
    # pve1 gerade nicht antwortet -- sonst kaeme die Meldung beim naechsten Lauf erneut.
    state_file = Path(str(get_extension_runtime().loaded["backups"].ctx.data_dir)) / "watch-state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(_json.dumps({"pve1--backup-x--104": 500, "primary--weg--999": 1}), encoding="utf-8")

    result = await _run_backup_watch()
    assert result == {"checked": 2, "notified": 1}
    notes = await _backup_notifications(db_session)
    assert [n[0] for n in notes] == ["critical"]
    assert notes[0][1].startswith("Backup fehlgeschlagen: ")
    alerted = _json.loads(state_file.read_text(encoding="utf-8"))
    assert set(alerted) == {"pve1--backup-x--104", real["100"]["job_ref"]}  # der verschwundene pve2-Job ist vergessen


@pytest.mark.asyncio
async def test_lists_and_retries_jobs_across_two_simultaneous_connections_with_colliding_ids(
    client, db_session, test_settings, mock_backup_api
):
    """Mehrere Verbindungen: wie proxmoxs gleichnamiger Test -- ZWEI unabhaengige,
    gleichzeitig konfigurierte Backup-Verbindungen (zwei echte, getrennte Mock-
    Server), die BEIDE denselben Job-Namen und dieselbe VMID vergeben (real
    plausibel: derselbe Job-Name/dieselbe VMID auf pve2 UND pve1). Beweist: (1)
    `list_jobs()` zeigt beide Verbindungen mit unterscheidbarem `connection`-Feld UND
    eindeutigem `job_ref`, (2) ein Retry gegen die 'pve1'-Verbindung trifft wirklich
    den zweiten Mock, nicht den ersten."""
    base_url_a, state_a = mock_backup_api
    app_b, state_b = _build_mock_backup_app()

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
        token = await _setup_backups_connections(
            client, db_session, test_settings,
            [
                {"name": "pve2", "base_url": base_url_a, "token_id": _TOKEN_ID},
                {"name": "pve1", "base_url": base_url_b, "token_id": _TOKEN_ID},
            ],
        )
        headers = _auth_header(token)

        jobs = (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json()
        assert len(jobs) == 4  # 2 Jobs je Verbindung, 2 Verbindungen
        job_refs = {j["job_ref"] for j in jobs}
        assert len(job_refs) == 4  # trotz identischer Job-IDs/VMIDs auf beiden Seiten eindeutig
        by_connection_and_vmid = {(j["connection"], j["vmid"]): j for j in jobs}
        assert by_connection_and_vmid[("pve2", "100")]["last_status"] == "failed"
        assert by_connection_and_vmid[("pve1", "100")]["last_status"] == "failed"

        mini_job_ref = by_connection_and_vmid[("pve1", "100")]["job_ref"]
        retry = await client.post(f"/api/v1/ext/backups/jobs/{mini_job_ref}/retry", headers=headers)
        assert retry.status_code == 200, retry.text
        action_id = retry.json()["action_id"]
        approve = await client.post(f"/api/v1/actions/{action_id}/approve", headers=headers)
        assert approve.status_code == 200, approve.text
        assert approve.json()["status"] == "succeeded"

        # Der Retry muss den ZWEITEN Mock (pve1) getroffen haben, nicht den ersten (pve2).
        assert state_b["vzdump_calls"] == [{"node": "pve2"}]
        assert state_a["vzdump_calls"] == []
    finally:
        server_b.should_exit = True
        await serve_task_b


@pytest.mark.asyncio
async def test_vm_name_is_enriched_from_a_discovered_proxmox_host(client, db_session, test_settings, mock_backup_api):
    """`ctx.hosts` kennt Proxmox nicht -- die Anreicherung ueber `provider_ref`
    (`qemu/<node>/<vmid>`) ist reine Bonus-Logik, kein harter Vertrag. Simuliert
    hier direkt ueber die DB (wie proxmoxs eigene Discovery es normalerweise
    schreiben wuerde), ohne die proxmox-Extension selbst mit aufzuziehen."""
    from nodvard_deck.models import Host

    base_url, _state = mock_backup_api
    token = await _setup_backups(client, db_session, test_settings, base_url)

    host = Host(name="docker", display_name="Docker-VM", address="10.0.0.85", provider_ext_id="proxmox", provider_ref="primary/qemu/pve2/100")
    db_session.add(host)
    await db_session.flush()

    res = await client.get("/api/v1/ext/backups/jobs", headers=_auth_header(token))
    by_vmid = {j["vmid"]: j for j in res.json()}
    assert by_vmid["100"]["name"] == "Docker-VM"
    assert by_vmid["100"]["host_id"] == host.id
    assert by_vmid["102"]["name"] == "VM 102"  # kein Host entdeckt -> Fallback auf die VMID


# Verbindungen ueber API/UI verwalten -- identisches Muster wie test_ext_proxmox.py, backups haelt eine
# eigene, unabhaengige Verbindungsliste (siehe connector.py-Docstring).


@pytest.mark.asyncio
async def test_connections_crud_full_round_trip(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    enabled = await client.post("/api/v1/extensions/backups/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text
    headers = _auth_header(token)

    empty = await client.get("/api/v1/ext/backups/connections", headers=headers)
    assert empty.status_code == 200, empty.text
    assert empty.json() == []

    created = await client.post(
        "/api/v1/ext/backups/connections",
        json={"name": "pve2", "base_url": "https://192.168.1.23:8006", "token_id": _TOKEN_ID},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body == {
        "name": "pve2", "base_url": "https://192.168.1.23:8006", "token_id": _TOKEN_ID,
        "tls_insecure_skip_verify": False, "enabled": True, "has_token": False,
    }

    token_resp = await client.post(
        "/api/v1/ext/backups/connections/pve2/token", json={"value": _TOKEN_SECRET}, headers=headers,
    )
    assert token_resp.status_code == 204, token_resp.text
    after_token = await client.get("/api/v1/ext/backups/connections", headers=headers)
    assert after_token.json()[0]["has_token"] is True

    updated = await client.put(
        "/api/v1/ext/backups/connections/pve2", json={"tls_insecure_skip_verify": True}, headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["tls_insecure_skip_verify"] is True

    deleted = await client.delete("/api/v1/ext/backups/connections/pve2", headers=headers)
    assert deleted.status_code == 204, deleted.text
    after_delete = await client.get("/api/v1/ext/backups/connections", headers=headers)
    assert after_delete.json() == []


@pytest.mark.asyncio
async def test_add_connection_rejects_duplicate_name(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/extensions/backups/enable", headers=_auth_header(token))
    headers = _auth_header(token)
    payload = {"name": "pve1", "base_url": "https://192.168.1.63:8006", "token_id": _TOKEN_ID}

    first = await client.post("/api/v1/ext/backups/connections", json=payload, headers=headers)
    assert first.status_code == 201, first.text
    second = await client.post("/api/v1/ext/backups/connections", json=payload, headers=headers)
    assert second.status_code == 409, second.text


@pytest.mark.asyncio
async def test_token_ersetzen_ueber_einstellungs_endpunkt(client, db_session, test_settings):
    """"Token setzen" lieferte beim zweiten Mal 409: `POST .../connections/{name}/token`
    legt nur an (`ctx.secrets` kennt kein Ersetzen). Die Oberflaeche nimmt deshalb
    `PUT /extensions/backups/secrets`, das anlegt ODER ersetzt."""
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    headers = _auth_header(token)
    await client.post("/api/v1/extensions/backups/enable", headers=headers)
    payload = {"name": "pve1", "base_url": "https://192.168.1.63:8006", "token_id": _TOKEN_ID}
    assert (await client.post("/api/v1/ext/backups/connections", json=payload, headers=headers)).status_code == 201

    first = await client.post("/api/v1/ext/backups/connections/pve1/token", json={"value": "erst-geheim"}, headers=headers)
    assert first.status_code == 204, first.text
    # Der alte Weg bleibt, wie er war (Kompatibilitaet): ein zweites Anlegen ist ein Konflikt.
    second = await client.post("/api/v1/ext/backups/connections/pve1/token", json={"value": "zweit-geheim"}, headers=headers)
    assert second.status_code == 409, second.text

    body = {"label": "backups-token:pve1", "value": "zweit-geheim"}
    replaced = await client.put("/api/v1/extensions/backups/secrets", json=body, headers=headers)
    assert replaced.status_code == 204, replaced.text
    listed = await client.get("/api/v1/ext/backups/connections", headers=headers)
    assert listed.json()[0]["has_token"] is True
    assert "geheim" not in listed.text


@pytest.mark.asyncio
async def test_update_and_delete_unknown_connection_return_404(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/extensions/backups/enable", headers=_auth_header(token))
    headers = _auth_header(token)

    update = await client.put("/api/v1/ext/backups/connections/ghost", json={"enabled": False}, headers=headers)
    assert update.status_code == 404
    delete = await client.delete("/api/v1/ext/backups/connections/ghost", headers=headers)
    assert delete.status_code == 404


@pytest.mark.asyncio
async def test_disabling_a_connection_removes_it_from_health_without_losing_its_token(
    client, db_session, test_settings, mock_backup_api
):
    base_url, _state = mock_backup_api
    token = await _setup_backups_connections(
        client, db_session, test_settings, [{"name": "primary", "base_url": base_url, "token_id": _TOKEN_ID}],
    )
    headers = _auth_header(token)
    runtime = get_extension_runtime()
    loaded = runtime.loaded["backups"]

    report_enabled = await loaded.instance.health(loaded.ctx)
    assert report_enabled.healthy is True

    disabled = await client.put("/api/v1/ext/backups/connections/primary", json={"enabled": False}, headers=headers)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["has_token"] is True

    report_disabled = await loaded.instance.health(loaded.ctx)
    assert report_disabled.healthy is False
    assert report_disabled.message == "Keine Verbindung konfiguriert."

    re_enabled = await client.put("/api/v1/ext/backups/connections/primary", json={"enabled": True}, headers=headers)
    assert re_enabled.status_code == 200, re_enabled.text
    report_re_enabled = await loaded.instance.health(loaded.ctx)
    assert report_re_enabled.healthy is True


@pytest.mark.asyncio
async def test_status_survives_a_task_ring_buffer_full_of_console_sessions(
    client, db_session, test_settings, mock_backup_api
):
    """Live gefunden: `/cluster/tasks` ist ein Ringpuffer der
    letzten ~25 Tasks -- nach ein paar Konsolen-Oeffnungen (vncproxy) war kein
    vzdump mehr darin, ein gesundes Backup stand auf "unbekannt". Der Status muss
    aus dem vollstaendigen Task-Verlauf des Knotens kommen."""
    base_url, state = mock_backup_api
    state["ring_noise"] = [
        {"upid": f"UPID:pve2:vnc{i}", "node": "pve2", "type": "vncproxy", "id": "110", "status": "OK", "starttime": 5000 + i, "endtime": 5001 + i}
        for i in range(30)
    ]
    token = await _setup_backups(client, db_session, test_settings, base_url)

    jobs = (await client.get("/api/v1/ext/backups/jobs", headers=_auth_header(token))).json()
    by_vmid = {j["vmid"]: j for j in jobs}
    assert by_vmid["102"]["last_status"] == "ok"
    assert by_vmid["100"]["last_status"] == "failed"


@pytest.mark.asyncio
async def test_a_running_backup_is_reported_as_running(client, db_session, test_settings, mock_backup_api):
    base_url, state = mock_backup_api
    state["tasks"].append({"upid": "UPID:pve2:run", "node": "pve2", "type": "vzdump", "id": "102", "starttime": 99999})
    token = await _setup_backups(client, db_session, test_settings, base_url)

    jobs = (await client.get("/api/v1/ext/backups/jobs", headers=_auth_header(token))).json()
    assert {j["vmid"]: j["last_status"] for j in jobs}["102"] == "running"


# ---------------------------------------------------------------------------
# Backup-Abdeckung: was NICHT gesichert wird
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guests_without_any_backup_job_are_listed_and_lead_the_widget(client, db_session, test_settings, mock_backup_api):
    base_url, state = mock_backup_api
    state["not_backed_up"] = [
        {"vmid": 110, "name": "game-win", "type": "qemu"},
        {"vmid": 104, "name": "Teleport", "type": "lxc"},
    ]
    token = await _setup_backups(client, db_session, test_settings, base_url)

    r = await client.get("/api/v1/ext/backups/unprotected", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errors"] == []
    assert [(g["vmid"], g["name"], g["kind"]) for g in body["guests"]] == [("104", "Teleport", "Container"), ("110", "game-win", "VM")]

    rows = (await client.get("/api/v1/ext/backups/widgets/summary", headers=_auth_header(token))).json()["data"]
    assert [r["name"] for r in rows[:2]] == ["Teleport", "game-win"], "ungesicherte Gaeste zuerst"
    assert (rows[0]["last_status_label"], rows[0]["tone"], rows[0]["job_ref"], rows[0]["storage"]) == ("kein Backup-Job", "warn", "", "kein Backup-Job")
    assert len(rows) == 4
    widgets = (await client.get("/api/v1/widgets", headers=_auth_header(token))).json()
    spec = next(w for w in widgets if w["ext_id"] == "backups")
    # "Erneut versuchen" nur, wo es einen Job gibt.
    assert spec["view"]["item"]["actions"][0]["show_if"] == "{{ job_ref }}"
    assert (await client.get("/api/v1/ext/backups/unprotected")).status_code == 401


@pytest.mark.asyncio
async def test_jobs_for_all_guests_or_a_pool_are_expanded_not_silently_dropped(client, db_session, test_settings, mock_backup_api):
    """Vorher: `vmid` fehlt bei "alle Gaeste"/Pool-Jobs -> der Job ergab KEINE Zeile."""
    base_url, state = mock_backup_api
    state["jobs"] = [
        {"id": "backup-all-pve2", "all": 1, "exclude": "102", "node": "pve2", "storage": "backup-pi", "schedule": "sun 01:00", "enabled": 1},
        {"id": "backup-pool", "pool": "kritisch", "storage": "backup-pve1", "schedule": "daily", "enabled": 1},
    ]
    token = await _setup_backups(client, db_session, test_settings, base_url)

    jobs = (await client.get("/api/v1/ext/backups/jobs", headers=_auth_header(token))).json()
    by_job = {}
    for row in jobs:
        by_job.setdefault(row["job_ref"].split("--")[1], []).append(row["vmid"])
    # all + exclude 102 + nur Knoten "pve2": 100 und 103, nicht 104 (pve1).
    assert by_job == {"backup-all-pve2": ["100", "103"], "backup-pool": ["103", "104"]}
    assert next(r for r in jobs if r["vmid"] == "100")["last_status"] == "failed"


@pytest.mark.asyncio
async def test_unprotected_reports_a_broken_connection_instead_of_hiding_it(client, db_session, test_settings, mock_backup_api):
    base_url, state = mock_backup_api
    state["not_backed_up"] = [{"vmid": 110, "name": "game-win", "type": "qemu"}]
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()
    token = await _setup_backups_connections(client, db_session, test_settings, [
        {"name": "pve2", "base_url": base_url, "token_id": _TOKEN_ID},
        {"name": "kaputt", "base_url": f"http://127.0.0.1:{dead_port}", "token_id": _TOKEN_ID},
    ])

    body = (await client.get("/api/v1/ext/backups/unprotected", headers=_auth_header(token))).json()
    assert [g["connection"] for g in body["guests"]] == ["pve2"]
    assert [e["connection"] for e in body["errors"]] == ["kaputt"]


# ---------------------------------------------------------------------------
# Backup-Waechter: Fehlschlag einmal melden, Erholung einmal melden
# ---------------------------------------------------------------------------


async def _run_backup_watch() -> dict:
    handler = get_extension_runtime().scheduler.get("backups", "watch")
    assert handler is not None
    return await handler()


async def _backup_notifications(db_session) -> list[tuple[str, str]]:
    from nodvard_deck.models import Notification as NotificationRow

    rows = (await db_session.execute(
        select(NotificationRow).where(NotificationRow.source_ext_id == "backups").order_by(NotificationRow.ts)
    )).scalars().all()
    return [(r.severity, r.title) for r in rows]


@pytest.mark.asyncio
async def test_backup_watch_reports_a_failure_once_and_the_recovery_once(client, db_session, test_settings, mock_backup_api):
    base_url, state = mock_backup_api
    await _setup_backups(client, db_session, test_settings, base_url)

    # Ausgangslage im Mock: VM100 zuletzt fehlgeschlagen (Broken pipe), VM102 ok.
    assert (await _run_backup_watch())["notified"] == 1
    assert (await _run_backup_watch())["notified"] == 0, "derselbe Fehlschlag wird nicht erneut gemeldet"

    # Ein laufender Wiederholungsversuch ist noch kein "wieder in Ordnung".
    state["tasks"].append({"upid": "UPID:pve2:3", "node": "pve2", "type": "vzdump", "id": "100", "starttime": 5000})
    assert (await _run_backup_watch())["notified"] == 0

    state["tasks"][-1].update({"status": "OK", "endtime": 5300})
    assert (await _run_backup_watch())["notified"] == 1

    notes = await _backup_notifications(db_session)
    assert [n[0] for n in notes] == ["critical", "info"]
    assert notes[0][1].startswith("Backup fehlgeschlagen: ")
    assert notes[1][1].startswith("Backup wieder erfolgreich: ")


# ---------------------------------------------------------------------------
# Wartungsfenster: stumme Meldung nach dem Fenster einmal nachholen (PR #41, bekannte Grenze)
# ---------------------------------------------------------------------------


class _RecordingChannel:
    channel_id = "test-kanal"
    label = "Test-Kanal"

    def __init__(self) -> None:
        self.received: list = []

    async def send(self, notification) -> None:
        self.received.append(notification)

    async def test(self):
        raise NotImplementedError


def _window(host_ids, *, minutes_ago: int) -> list[dict]:
    """Fenster von 60 Minuten, Start vor `minutes_ago` Minuten (Ortszeit wie der Waehler):
    5 -> laeuft gerade, 90 -> schon vorbei."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from nodvard_deck.config import LOCAL_TIMEZONE
    from nodvard_deck.db import utcnow

    start = (utcnow() - timedelta(minutes=minutes_ago)).astimezone(ZoneInfo(LOCAL_TIMEZONE))
    return [{"cron": f"{start.minute} {start.hour} * * *", "duration_minutes": 60, "host_ids": host_ids}]


async def _backup_window_setup(client, db_session, test_settings, base_url) -> tuple[str, _RecordingChannel]:
    """Backups-Verbindung, VM 100 als entdeckter Host (Meldungen tragen dessen ID), ein
    Test-Kanal und ein laufendes Wartungsfenster fuer genau diesen Host."""
    from nodvard_sdk.capabilities import NotificationChannel

    from nodvard_deck.models import Host
    from nodvard_deck.services import settings as settings_service

    await _setup_backups(client, db_session, test_settings, base_url)
    host = Host(name="docker", display_name="Docker-VM", address="10.0.0.85", provider_ext_id="proxmox", provider_ref="primary/qemu/pve2/100")
    db_session.add(host)
    await db_session.flush()
    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)
    await settings_service.set_global(db_session, "maintenance.windows", _window([host.id], minutes_ago=5))
    return host.id, channel


async def _end_window(db_session, host_id: str) -> None:
    from nodvard_deck.services import settings as settings_service

    await settings_service.set_global(db_session, "maintenance.windows", _window([host_id], minutes_ago=90))


async def _backup_deliveries(db_session) -> list[tuple[str, str]]:
    from nodvard_deck.models import Notification as NotificationRow
    from nodvard_deck.models import NotificationDelivery

    rows = (await db_session.execute(
        select(NotificationRow.title, NotificationDelivery.status)
        .join(NotificationDelivery, NotificationDelivery.notification_id == NotificationRow.id)
        .where(NotificationRow.source_ext_id == "backups").order_by(NotificationRow.ts, NotificationDelivery.id)
    )).all()
    return [(title, status) for title, status in rows]


@pytest.mark.asyncio
async def test_backup_failure_in_the_window_is_announced_once_after_it_if_still_failed(
    client, db_session, test_settings, mock_backup_api
):
    """Das Backup laeuft nachts im Fenster und schlaegt fehl: im Fenster ein stummer
    Eintrag (auch bei vielen Pruefungen), danach genau eine hoerbare Meldung."""
    base_url, state = mock_backup_api
    host_id, channel = await _backup_window_setup(client, db_session, test_settings, base_url)

    for _ in range(4):
        await _run_backup_watch()
    assert channel.received == []
    assert await _backup_deliveries(db_session) == [("Backup fehlgeschlagen: Docker-VM", "suppressed")]

    await _end_window(db_session, host_id)
    assert (await _run_backup_watch())["notified"] == 1
    for _ in range(3):
        assert (await _run_backup_watch())["notified"] == 0, "nach dem Fenster genau einmal"
    assert [n.title for n in channel.received] == ["Backup fehlgeschlagen: Docker-VM (seit dem Wartungsfenster)"]
    assert "Wartungsfenster" in channel.received[0].body
    assert channel.received[0].payload == {"host_id": host_id}

    state["tasks"].append({"upid": "UPID:pve2:3", "node": "pve2", "type": "vzdump", "id": "100", "status": "OK", "starttime": 5000, "endtime": 5300})
    await _run_backup_watch()
    assert [(n.title, n.payload) for n in channel.received][1:] == [("Backup wieder erfolgreich: Docker-VM", {"host_id": host_id})]


@pytest.mark.asyncio
async def test_backup_retried_successfully_in_the_window_brings_no_push_afterwards(
    client, db_session, test_settings, mock_backup_api
):
    """Fehlschlag und erfolgreicher Wiederholungsversuch beide im Fenster: nur Verlauf,
    kein Push -- auch die Entwarnung nicht (sie gehoert jetzt ebenfalls zum Host)."""
    base_url, state = mock_backup_api
    host_id, channel = await _backup_window_setup(client, db_session, test_settings, base_url)

    await _run_backup_watch()
    state["tasks"].append({"upid": "UPID:pve2:3", "node": "pve2", "type": "vzdump", "id": "100", "status": "OK", "starttime": 5000, "endtime": 5300})
    await _run_backup_watch()
    await _end_window(db_session, host_id)
    for _ in range(3):
        assert (await _run_backup_watch())["notified"] == 0
    assert channel.received == []
    assert await _backup_deliveries(db_session) == [
        ("Backup fehlgeschlagen: Docker-VM", "suppressed"),
        ("Backup wieder erfolgreich: Docker-VM", "suppressed"),
    ]


async def _start_window(db_session, host_id: str) -> None:
    from nodvard_deck.services import settings as settings_service

    await settings_service.set_global(db_session, "maintenance.windows", _window([host_id], minutes_ago=5))


_OK_TASK = {"upid": "UPID:pve2:3", "node": "pve2", "type": "vzdump", "id": "100", "status": "OK", "starttime": 5000, "endtime": 5300}


@pytest.mark.asyncio
async def test_backup_all_clear_for_a_heard_failure_comes_after_the_window(
    client, db_session, test_settings, mock_backup_api
):
    """Aus dem Review: der Fehlschlag kam hoerbar (kein Fenster), der erfolgreiche Lauf
    faellt ins Fenster. Im Fenster bleibt die Entwarnung still, danach kommt sie genau
    einmal -- sonst bliebe der gehoerte Alarm auf dem Handy offen."""
    base_url, state = mock_backup_api
    host_id, channel = await _backup_window_setup(client, db_session, test_settings, base_url)
    await _end_window(db_session, host_id)
    assert (await _run_backup_watch())["notified"] == 1
    assert [n.title for n in channel.received] == ["Backup fehlgeschlagen: Docker-VM"]

    await _start_window(db_session, host_id)
    state["tasks"].append(dict(_OK_TASK))
    assert (await _run_backup_watch())["notified"] == 1  # die stumme Entwarnung im Verlauf
    for _ in range(2):
        assert (await _run_backup_watch())["notified"] == 0, "im Fenster keine weitere Meldung"
    assert len(channel.received) == 1

    await _end_window(db_session, host_id)
    assert (await _run_backup_watch())["notified"] == 1
    for _ in range(2):
        assert (await _run_backup_watch())["notified"] == 0, "nach dem Fenster genau einmal"
    assert [(n.title, n.payload) for n in channel.received][1:] == [
        ("Backup wieder erfolgreich: Docker-VM (im Wartungsfenster)", {"host_id": host_id}),
    ]
    assert await _backup_deliveries(db_session) == [
        ("Backup fehlgeschlagen: Docker-VM", "sent"),
        ("Backup wieder erfolgreich: Docker-VM", "suppressed"),
        ("Backup wieder erfolgreich: Docker-VM (im Wartungsfenster)", "sent"),
    ]


@pytest.mark.asyncio
async def test_backup_push_from_after_the_window_is_closed_by_the_next_nights_success(
    client, db_session, test_settings, mock_backup_api
):
    """Der Ablauf, den die Nachmeldung selbst erzeugt: Fehlschlag im Fenster (still), nach
    dem Fenster hoerbar nachgemeldet, der naechste nachtliche Lauf gelingt wieder im Fenster.
    Die Entwarnung kommt danach -- der Push bleibt nicht offen."""
    base_url, state = mock_backup_api
    host_id, channel = await _backup_window_setup(client, db_session, test_settings, base_url)
    await _run_backup_watch()
    await _end_window(db_session, host_id)
    await _run_backup_watch()

    await _start_window(db_session, host_id)
    state["tasks"].append(dict(_OK_TASK))
    await _run_backup_watch()
    await _end_window(db_session, host_id)
    await _run_backup_watch()
    await _run_backup_watch()
    assert [n.title for n in channel.received] == [
        "Backup fehlgeschlagen: Docker-VM (seit dem Wartungsfenster)",
        "Backup wieder erfolgreich: Docker-VM (im Wartungsfenster)",
    ]


@pytest.mark.asyncio
async def test_backup_pending_all_clear_is_dropped_when_the_job_fails_again(
    client, db_session, test_settings, mock_backup_api
):
    base_url, state = mock_backup_api
    host_id, channel = await _backup_window_setup(client, db_session, test_settings, base_url)
    await _end_window(db_session, host_id)
    await _run_backup_watch()
    await _start_window(db_session, host_id)
    state["tasks"].append(dict(_OK_TASK))
    await _run_backup_watch()
    state["tasks"].append({**_OK_TASK, "upid": "UPID:pve2:4", "status": "job errors", "starttime": 6000, "endtime": 6300})
    await _run_backup_watch()  # neuer Fehlschlag, still im Fenster
    await _end_window(db_session, host_id)
    await _run_backup_watch()
    await _run_backup_watch()
    assert [n.title for n in channel.received] == [
        "Backup fehlgeschlagen: Docker-VM",
        "Backup fehlgeschlagen: Docker-VM (seit dem Wartungsfenster)",
    ]


@pytest.mark.asyncio
async def test_backup_watch_runs_against_an_older_core_without_the_new_answers(
    client, db_session, test_settings, mock_backup_api, monkeypatch
):
    """Aelterer Kern: `send()` liefert nichts, `would_suppress()` gibt es nicht. Kein
    Absturz, der Waechter laeuft wie vor der Nachmeldung."""
    from nodvard_deck.ext.context import NotifyHandle

    real_send = NotifyHandle.send

    async def old_send(self, notification, *, raise_on_failure=False):
        await real_send(self, notification, raise_on_failure=raise_on_failure)

    monkeypatch.setattr(NotifyHandle, "send", old_send)
    monkeypatch.delattr(NotifyHandle, "would_suppress")

    base_url, state = mock_backup_api
    host_id, channel = await _backup_window_setup(client, db_session, test_settings, base_url)
    for _ in range(2):
        await _run_backup_watch()
    state["tasks"].append(dict(_OK_TASK))
    assert (await _run_backup_watch())["notified"] == 1
    assert await _backup_deliveries(db_session) == [
        ("Backup fehlgeschlagen: Docker-VM", "suppressed"), ("Backup wieder erfolgreich: Docker-VM", "suppressed"),
    ]


# ---------------------------------------------------------------------------
# Backup-Details: naechster Lauf, Aufbewahrung, was tatsaechlich auf den Speichern liegt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jobs_show_next_run_and_retention_from_the_job_definition(client, db_session, test_settings, mock_backup_api):
    base_url, state = mock_backup_api
    state["jobs"][1].update({"next-run": 1790281800, "prune-backups": {"keep-last": "3"}})
    state["jobs"][0].update({"prune-backups": "keep-daily=7,keep-weekly=4"})
    token = await _setup_backups(client, db_session, test_settings, base_url)

    jobs = {j["vmid"]: j for j in (await client.get("/api/v1/ext/backups/jobs", headers=_auth_header(token))).json()}
    assert (jobs["102"]["next_run_at"], jobs["102"]["retention"]) == (1790281800, "letzte 3")
    assert (jobs["100"]["next_run_at"], jobs["100"]["retention"]) == (None, "7 tägl., 4 wöchentl.")


@pytest.mark.asyncio
async def test_inventory_attributes_files_by_vmid_and_type_across_connections(client, db_session, test_settings, mock_backup_api):
    """Live gefunden: Backup-Speicher sind geteilt, VMIDs nur je Instanz eindeutig --
    VMID 100 ist auf pve2 eine VM, auf pve1 ein Container. Zuordnung ueber VMID + Typ."""
    from nodvard_deck.models import Host

    base_url, state = mock_backup_api
    token = await _setup_backups(client, db_session, test_settings, base_url)
    for name, ref in [("Zabbix", "primary/qemu/pve2/102"), ("docker", "primary/qemu/pve2/100"),
                      ("Teleport", "pve1/lxc/pve1/104"), ("docker-lxc", "pve1/lxc/pve1/100")]:
        db_session.add(Host(name=name, display_name=name, address="10.0.0.1", provider_ext_id="proxmox", provider_ref=ref))
    await db_session.flush()

    r = await client.get("/api/v1/ext/backups/inventory", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errors"] == []
    by_key = {(g["connection"], g["vmid"]): g for g in body["guests"]}
    zabbix = by_key[("primary", "102")]
    assert (zabbix["count"], zabbix["newest_at"], zabbix["oldest_at"], zabbix["total_size"]) == (3, 3000, 1000, 11_550_000_000)
    assert zabbix["storages"] == ["backup-pi", "backup-pve1"]
    # Ueber "primary" gelesen, gehoert aber dem Container von pve1 -> dort einsortiert.
    assert (by_key[("pve1", "104")]["count"], by_key[("pve1", "104")]["newest_at"]) == (1, 500)
    assert by_key[("pve1", "100")]["count"] == 1, "lxc-100 ist docker-lxc, nicht die VM docker"
    assert ("primary", "100") not in by_key
    assert [(o["connection"], o["vmid"], o["kind"], o["total_size"]) for o in body["orphans"]] == [("primary", "999", "qemu", 5_000_000_000)]
    # Inaktiver Speicher wird nicht angefasst; gemeinsame Speicher nur einmal gelesen.
    assert "alt-offline" not in {c[1] for c in state["content_calls"]}
    assert sorted(c[1] for c in state["content_calls"]) == ["backup-pi", "backup-pve1", "local"]
    assert (await client.get("/api/v1/ext/backups/inventory")).status_code == 401


def test_retention_label_handles_object_string_and_keep_all():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "backups" / "src"))
    from nodvard_deck_ext_backups.inventory import retention_label

    assert retention_label({"keep-last": "3"}) == "letzte 3"
    assert retention_label("keep-last=2,keep-monthly=1") == "letzte 2, 1 monatl."
    assert retention_label({"keep-all": 1}) == "alle behalten"
    assert retention_label(None) is None


# ---------------------------------------------------------------------------
# "Bewusst ohne Backup": eine bewusste Entscheidung warnt nicht mehr
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acknowledged_unprotected_guest_stays_listed_but_leaves_the_widget(client, db_session, test_settings, mock_backup_api):
    base_url, state = mock_backup_api
    state["not_backed_up"] = [
        {"vmid": 100, "name": "docker", "type": "qemu"},
        {"vmid": 110, "name": "game-win", "type": "qemu"},
    ]
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    r = await client.put(
        "/api/v1/ext/backups/unprotected/primary/100/acknowledged",
        json={"note": "  Nextcloud: Risiko bewusst akzeptiert  "}, headers=headers,
    )
    assert r.status_code == 204, r.text

    guests = {g["vmid"]: g for g in (await client.get("/api/v1/ext/backups/unprotected", headers=headers)).json()["guests"]}
    assert (guests["100"]["acknowledged"], guests["100"]["note"]) == (True, "Nextcloud: Risiko bewusst akzeptiert")
    assert (guests["110"]["acknowledged"], guests["110"]["note"]) == (False, None)
    widget_names = [row["name"] for row in (await client.get("/api/v1/ext/backups/widgets/summary", headers=headers)).json()["data"]]
    assert "game-win" in widget_names and "docker" not in widget_names

    # Nochmal bestaetigen ersetzt die Notiz statt einen Doppeleintrag anzulegen.
    await client.put("/api/v1/ext/backups/unprotected/primary/100/acknowledged", json={"note": "neu"}, headers=headers)
    record = await db_session.get(ExtensionRecord, "backups")
    await db_session.refresh(record)
    assert record.settings["acknowledged_unprotected"] == [{"connection": "primary", "vmid": "100", "note": "neu"}]

    assert (await client.delete("/api/v1/ext/backups/unprotected/primary/100/acknowledged", headers=headers)).status_code == 204
    guests = {g["vmid"]: g for g in (await client.get("/api/v1/ext/backups/unprotected", headers=headers)).json()["guests"]}
    assert guests["100"]["acknowledged"] is False

    assert (await client.put("/api/v1/ext/backups/unprotected/primary/abc/acknowledged", json={}, headers=headers)).status_code == 422
    assert (await client.put("/api/v1/ext/backups/unprotected/primary/100/acknowledged", json={})).status_code == 401


@pytest.mark.asyncio
async def test_a_job_for_several_guests_counts_its_run_for_each_guest(client, db_session, test_settings, mock_backup_api):
    """Ein Container stand auf "nie gelaufen", obwohl gesichert -- Proxmox fuehrt
    einen Job fuer mehrere Gaeste als EINE Aufgabe ohne Gast-ID. Die zaehlt jetzt fuer
    jeden Gast des Jobs, aber nicht fuer Gaeste anderer Jobs oder anderer Knoten."""
    base_url, state = mock_backup_api
    state["jobs"].append({"id": "group-pve1", "vmid": "200,204", "storage": "backup-pi", "schedule": "01:30", "enabled": 1, "node": "pve1"})
    state["tasks"] += [
        {"upid": "UPID:pve1:9", "node": "pve1", "type": "vzdump", "id": "", "status": "OK", "starttime": 5000, "endtime": 5600},
        {"upid": "UPID:other:9", "node": "other", "type": "vzdump", "id": "", "status": "job errors", "starttime": 6000, "endtime": 6100},
    ]
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    rows = {j["vmid"]: j for j in (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json()}
    assert (rows["200"]["last_status"], rows["200"]["last_run_at"]) == ("ok", 5000)
    assert (rows["204"]["last_status"], rows["204"]["last_run_at"]) == ("ok", 5000)
    assert rows["102"]["last_run_at"] == 2000, "Einzel-Jobs bleiben bei ihren eigenen Laeufen"
    history = (await client.get(f"/api/v1/ext/backups/jobs/{rows['200']['job_ref']}/history", headers=headers)).json()
    assert [h["upid"] for h in history] == ["UPID:pve1:9"]


@pytest.mark.asyncio
async def test_retry_of_a_group_job_without_node_uses_the_guests_node(client, db_session, test_settings, mock_backup_api):
    """Job fuer mehrere Gaeste mit Knoten "-- Alle --" (PVE-Standard). Die Seite
    zeigte den Knoten, "Erneut versuchen" endete aber mit "Kein Proxmox-Knoten bekannt" --
    die Sammel-Aufgabe hat keine Gast-ID. Der Knoten kommt jetzt vom Gast selbst, sonst
    aus den Laeufen des Jobs."""
    base_url, state = mock_backup_api
    state["jobs"] = [{"id": "backup-multi", "vmid": "100,104,300", "storage": "backup-pi", "schedule": "01:30", "enabled": 1}]
    state["tasks"] = [{"upid": "UPID:pve2:7", "node": "pve2", "type": "vzdump", "id": "", "status": "job errors", "starttime": 5000, "endtime": 5600}]
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    rows = {j["vmid"]: j for j in (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json()}
    assert (rows["100"]["last_status"], rows["100"]["node"]) == ("failed", "pve2")

    # 104 liegt laut /cluster/resources auf pve1, 300 kennt Proxmox dort nicht (mehr):
    # dann zaehlt der Knoten der letzten Sammel-Aufgabe.
    for vmid in ("100", "104", "300"):
        retry = await client.post(f"/api/v1/ext/backups/jobs/{rows[vmid]['job_ref']}/retry", headers=headers)
        assert retry.status_code == 200, retry.text
        approve = await client.post(f"/api/v1/actions/{retry.json()['action_id']}/approve", headers=headers)
        assert approve.json()["status"] == "succeeded", approve.text
    assert state["vzdump_calls"] == [{"node": "pve2"}, {"node": "pve1"}, {"node": "pve2"}]


@pytest.mark.asyncio
async def test_history_of_an_all_guests_job_shows_its_runs(client, db_session, test_settings, mock_backup_api):
    """Bei "alle Gaeste"-Jobs zeigte die Zeile "erfolgreich", der aufgeklappte
    Verlauf aber keinen Lauf -- die Historie kannte die Gaeste des Jobs nicht."""
    base_url, state = mock_backup_api
    state["jobs"] = [{"id": "backup-all-pve2", "all": 1, "node": "pve2", "storage": "backup-pi", "schedule": "sun 01:00", "enabled": 1}]
    state["tasks"] = [{"upid": "UPID:pve2:8", "node": "pve2", "type": "vzdump", "id": "", "status": "OK", "starttime": 5000, "endtime": 5600}]
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    rows = {j["vmid"]: j for j in (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json()}
    assert sorted(rows) == ["100", "102", "103"]
    assert rows["100"]["last_status"] == "ok"
    history = await client.get(f"/api/v1/ext/backups/jobs/{rows['100']['job_ref']}/history", headers=headers)
    assert history.status_code == 200, history.text
    assert [h["upid"] for h in history.json()] == ["UPID:pve2:8"]



_GB = 1024**3


def _space_state(state: dict, *, nextcloud_disk_backup: bool = True) -> None:
    """VM 100 mit grosser Datenplatte, auf dem geteilten
    Backup-Speicher nur noch 146 GB frei."""
    state["backup_storages"][1].update({"avail": 146 * _GB, "total": 234 * _GB})
    data_disk = "local-lvm:vm-100-disk-1,size=500G" + ("" if nextcloud_disk_backup else ",backup=0")
    state["configs"] = {
        ("qemu", "100"): {
            "scsi0": "local-lvm:vm-100-disk-0,iothread=1,size=32G",
            "scsi1": data_disk,
            "ide2": "local:iso/debian.iso,media=cdrom,size=600M",
            "memory": "8192",
        },
    }


@pytest.mark.asyncio
async def test_create_job_warns_when_the_guest_does_not_fit_on_the_storage(client, db_session, test_settings, mock_backup_api):
    base_url, state = mock_backup_api
    _space_state(state)
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    connection = (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json()[0]["connection"]
    values = {"schedule": "sun 02:00", "storage": "backup-pi", "keep-last": 2}

    r = await client.post(f"/api/v1/ext/backups/unprotected/{connection}/100/job", json={"values": values}, headers=headers)
    assert r.status_code == 409, r.text
    warning = r.json()["space_warning"]
    assert (warning["guest_bytes"], warning["avail_bytes"]) == (532 * _GB, 146 * _GB)
    assert r.json()["detail"].startswith("'docker' hat bis zu 532 GB zu sichern, auf Speicher 'backup-pi' sind aber nur noch 146 GB frei.")
    assert (await client.get("/api/v1/actions", headers=headers)).json() == [], "ohne 'trotzdem' kein Vorschlag"

    forced = await client.post(
        f"/api/v1/ext/backups/unprotected/{connection}/100/job", json={"values": values, "ignore_space": True}, headers=headers
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["status"] == "proposed"

    # Anderer Speicher ohne bekannte Groesse (backup-pve1 meldet kein `avail`): keine Warnung.
    other = await client.post(
        f"/api/v1/ext/backups/unprotected/{connection}/100/job", json={"values": {**values, "storage": "backup-pve1"}}, headers=headers
    )
    assert other.status_code == 200, other.text


@pytest.mark.asyncio
async def test_disk_excluded_from_backup_does_not_count(client, db_session, test_settings, mock_backup_api):
    """Der empfohlene Weg fuer VM 100: die grosse Datenplatte in Proxmox mit backup=0
    ausnehmen -- dann passt das System-Backup (32 GB) und es kommt keine Warnung."""
    base_url, state = mock_backup_api
    _space_state(state, nextcloud_disk_backup=False)
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    connection = (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json()[0]["connection"]

    r = await client.post(
        f"/api/v1/ext/backups/unprotected/{connection}/100/job",
        json={"values": {"schedule": "sun 02:00", "storage": "backup-pi"}}, headers=headers,
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_retry_warns_when_the_guest_does_not_fit_on_the_storage(client, db_session, test_settings, mock_backup_api):
    base_url, state = mock_backup_api
    _space_state(state)
    state["jobs"][0]["storage"] = "backup-pi"
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    job_ref = next(j["job_ref"] for j in (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json() if j["vmid"] == "100")

    r = await client.post(f"/api/v1/ext/backups/jobs/{job_ref}/retry", headers=headers)
    assert r.status_code == 409, r.text
    assert "space_warning" in r.json()

    forced = await client.post(f"/api/v1/ext/backups/jobs/{job_ref}/retry", json={"ignore_space": True}, headers=headers)
    assert forced.status_code == 200, forced.text
    assert forced.json()["status"] == "proposed"


def test_backed_up_disk_bytes_counts_only_what_vzdump_saves():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "backups" / "src"))
    from nodvard_deck_ext_backups.space import backed_up_disk_bytes

    assert backed_up_disk_bytes("qemu", {
        "scsi0": "local-lvm:vm-1-disk-0,size=32G",
        "virtio1": "local-lvm:vm-1-disk-1,size=1T,backup=0",
        "efidisk0": "local-lvm:vm-1-disk-2,efitype=4m,size=4M",
        "ide2": "none,media=cdrom",
        "net0": "virtio=AA:BB,bridge=vmbr0",
    }) == 32 * _GB + 4 * 1024**2
    # Container: rootfs immer, Mountpoints nur mit backup=1.
    assert backed_up_disk_bytes("lxc", {
        "rootfs": "local-lvm:vm-2-disk-0,size=8G",
        "mp0": "local-lvm:vm-2-disk-1,mp=/data,size=100G",
        "mp1": "local-lvm:vm-2-disk-2,mp=/db,backup=1,size=2G",
    }) == 10 * _GB
    assert backed_up_disk_bytes("qemu", {"memory": "2048"}) is None


@pytest.mark.asyncio
async def test_job_edit_warns_when_the_new_storage_is_too_small(client, db_session, test_settings, mock_backup_api):
    """Speicher eines bestehenden Jobs wechseln: alle Gaeste des Jobs muessen dort Platz haben."""
    base_url, state = mock_backup_api
    _space_state(state)
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    job_ref = next(j["job_ref"] for j in (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json() if j["vmid"] == "100")

    r = await client.post(f"/api/v1/ext/backups/jobs/{job_ref}/edit", json={"changes": {"storage": "backup-pi"}}, headers=headers)
    assert r.status_code == 409, r.text
    assert r.json()["space_warning"]["storage"] == "backup-pi"
    forced = await client.post(
        f"/api/v1/ext/backups/jobs/{job_ref}/edit", json={"changes": {"storage": "backup-pi"}, "ignore_space": True}, headers=headers
    )
    assert forced.status_code == 200, forced.text

    # Andere Aenderungen (Zeitplan) pruefen keinen Platz.
    same = await client.post(f"/api/v1/ext/backups/jobs/{job_ref}/edit", json={"changes": {"schedule": "sun 03:00"}}, headers=headers)
    assert same.status_code == 200, same.text
