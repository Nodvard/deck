"""GET /overview -- Cockpit-Uebersicht der Startseite.

Beweist die Herstellerneutralitaet: die Dienste/Backups kommen hier aus Test-
Implementierungen der SDK-Capabilities, nicht aus service-matrix/backups."""

from __future__ import annotations

import asyncio

import pytest

from nodvard_deck.api.v1 import overview as overview_api
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import Action
from nodvard_deck.services import notifications as notifications_service
from nodvard_sdk import BackupProvider, ServiceCatalog


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _Services:
    def __init__(self) -> None:
        self.calls = 0

    async def list_services(self):
        self.calls += 1
        return [
            {"id": "h1:grafana", "name": "grafana", "host": "docker", "host_id": "h1", "state": "running", "tone": "good", "url": "http://10.0.0.5:3000", "image": "grafana/grafana"},
            {"id": "h1:alt", "name": "alt", "host": "docker", "host_id": "h1", "state": "exited", "tone": "danger", "url": None, "image": "busybox"},
            # Fehlerkachel einer Extension ("Host nicht erreichbar") -- kein Dienst.
            {"id": "h2:error", "name": "", "host": "pi", "state": "error"},
        ]


class _ServicesWithDeadHost:
    """Wie DockerServiceCatalog.list_services(), wenn ein Server nicht antwortet: Platzhalterkachel mit Namen
    und `state="error"`, aber ohne Container."""

    def __init__(self, with_live_host: bool) -> None:
        self._live = with_live_host

    async def list_services(self):
        rows = [
            {"id": "h2:__error__", "name": "⚠ pi-test", "host": "pi-test", "state": "error", "tone": "danger", "url": None, "unreachable": True},
            {"id": "h3:__error__", "name": "⚠ pi-test", "host": "pi-test", "state": "error", "tone": "danger", "url": None, "unreachable": True},
        ]
        if self._live:
            rows.append({"id": "h1:grafana", "name": "grafana", "host": "docker-test", "host_id": "h1", "state": "running", "tone": "good"})
        return rows


class _HangingServices:
    async def list_services(self):
        await asyncio.sleep(3600)


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, True])
async def test_unreachable_hosts_are_reported_not_counted_as_stopped(client, live):
    token = await _bootstrap_owner(client)
    get_extension_runtime().capabilities.provide("fake-services", ServiceCatalog, _ServicesWithDeadHost(live))

    body = (await client.get("/api/v1/overview", headers=_auth_header(token))).json()
    assert body["services_unreachable_hosts"] == ["pi-test"], "jeder Server nur einmal"
    assert [s["name"] for s in body["services"] if not s["unreachable"]] == (["grafana"] if live else [])
    assert all(s["unreachable"] for s in body["services"] if s["state"] == "error")
    assert body["services_running"] == (1 if live else 0)


@pytest.mark.asyncio
async def test_overview_without_dead_hosts_has_empty_unreachable_list(client):
    token = await _bootstrap_owner(client)
    get_extension_runtime().capabilities.provide("fake-services", ServiceCatalog, _Services())
    body = (await client.get("/api/v1/overview", headers=_auth_header(token))).json()
    assert body["services_unreachable_hosts"] == []
    assert not any(s["unreachable"] for s in body["services"])


class _Backups:
    async def list_jobs(self):
        return [
            {"name": "Zabbix", "last_status": "ok"},
            {"name": "ki-server", "last_status": "ok"},
            {"name": "docker", "last_status": "failed"},
            {"name": "neu", "last_status": "unknown"},
        ]

    async def job_history(self, job_ref):
        return []

    async def retry(self, job_ref):
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _fresh_cache():
    overview_api.reset_cache()
    yield
    runtime = get_extension_runtime()
    for ext_id in ("fake-services", "fake-hang", "fake-backups"):
        runtime.capabilities.clear_extension(ext_id)
    overview_api.reset_cache()


@pytest.mark.asyncio
async def test_overview_aggregates_services_backups_approvals_and_warnings(client, db_session):
    token = await _bootstrap_owner(client)
    services = _Services()
    runtime = get_extension_runtime()
    runtime.capabilities.provide("fake-services", ServiceCatalog, services)
    runtime.capabilities.provide("fake-backups", BackupProvider, _Backups())
    await notifications_service.send(db_session, title="Datenträger auf pve2: SMART: FAILED", body="", severity="critical")
    await notifications_service.send(db_session, title="Info", body="", severity="info")
    db_session.add(Action(ext_id="proxmox", action_type="vm.stop", payload={}, risk="high", status="proposed",
                          proposed_by_type="user", proposed_by_id="u1", reason="Test"))
    await db_session.flush()

    r = await client.get("/api/v1/overview", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["name"] for s in body["services"]] == ["grafana", "alt"], "laufende zuerst, Fehlerkacheln raus"
    assert body["services_running"] == 1
    assert body["backups"] == {"total": 4, "ok": 2, "failed": 1, "running": 0, "unknown": 1, "failed_names": ["docker"], "unreachable_names": []}
    assert body["pending_actions"] == 1
    assert body["unread_notifications"] == 2
    assert [a["title"] for a in body["attention"]] == ["Datenträger auf pve2: SMART: FAILED"], "nur Warnungen/Kritisches"
    assert body["errors"] == []

    # Zwischengespeichert: zweiter Aufruf fragt die Quelle nicht erneut.
    await client.get("/api/v1/overview", headers=_auth_header(token))
    assert services.calls == 1


class _BackupsWithDeadConnection:
    """Wie ProxmoxBackupProvider.list_jobs() bei einer toten Verbindung: die
    Platzhalterzeile hat `last_status="unreachable"` und leeres `job_ref`."""

    def __init__(self, rows):
        self._rows = rows

    async def list_jobs(self):
        return self._rows

    async def job_history(self, job_ref):
        return []

    async def retry(self, job_ref):
        raise NotImplementedError


_DEAD_MINI = {
    "job_ref": "", "vmid": "", "name": "Verbindung pve1", "host_id": None, "connection": "pve1",
    "last_status": "unreachable", "error": "Timeout",
}


@pytest.mark.asyncio
async def test_unreachable_backup_connection_is_a_warning_not_a_job(client):
    token = await _bootstrap_owner(client)
    get_extension_runtime().capabilities.provide("fake-backups", BackupProvider, _BackupsWithDeadConnection([
        {"name": "Zabbix", "last_status": "ok"},
        {"name": "neu", "last_status": "unknown"},
        _DEAD_MINI,
    ]))

    body = (await client.get("/api/v1/overview", headers=_auth_header(token))).json()
    # Die Platzhalterzeile zaehlt weder als Job noch als "unbekannter" Lauf.
    assert body["backups"] == {
        "total": 2, "ok": 1, "failed": 0, "running": 0, "unknown": 1,
        "failed_names": [], "unreachable_names": ["pve1"],
    }


@pytest.mark.asyncio
async def test_backups_with_only_unreachable_connections_still_show_a_summary(client):
    token = await _bootstrap_owner(client)
    get_extension_runtime().capabilities.provide("fake-backups", BackupProvider, _BackupsWithDeadConnection([
        _DEAD_MINI,
        {**_DEAD_MINI, "name": "Verbindung pve2", "connection": "pve2"},
    ]))

    body = (await client.get("/api/v1/overview", headers=_auth_header(token))).json()
    assert body["backups"]["total"] == 0
    assert body["backups"]["unknown"] == 0
    assert body["backups"]["unreachable_names"] == ["pve1", "pve2"]


@pytest.mark.asyncio
async def test_a_hanging_source_is_reported_not_blocking(client, db_session, monkeypatch):
    monkeypatch.setattr(overview_api, "SOURCE_TIMEOUT_S", 0.2)
    token = await _bootstrap_owner(client)
    runtime = get_extension_runtime()
    runtime.capabilities.provide("fake-hang", ServiceCatalog, _HangingServices())
    runtime.capabilities.provide("fake-services", ServiceCatalog, _Services())

    body = (await client.get("/api/v1/overview", headers=_auth_header(token))).json()
    assert [s["name"] for s in body["services"]] == ["grafana", "alt"]
    assert len(body["errors"]) == 1 and body["errors"][0].startswith("Dienste:")
    assert body["backups"] is None, "ohne BackupProvider keine Backup-Kachel statt falscher Nullen"


@pytest.mark.asyncio
async def test_overview_requires_login(client):
    assert (await client.get("/api/v1/overview")).status_code == 401
