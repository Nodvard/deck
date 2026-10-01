"""Eine Installation OHNE Proxmox (Pflicht-Durchgang vor jeder Veröffentlichung):
nur von Hand angelegte Server, die Module Proxmox und Backups sind aus. Nichts darf so tun, als sei Proxmox nötig:
keine Proxmox-Seite im Menü, keine Proxmox-Kacheln, keine Fehler in der Übersicht, und die Module, die per SSH
arbeiten, brauchen keine Angaben mehr.

Die Antworten kommen hier aus den echten Modulen (nicht aus Test-Doubles), nur ohne ein Wort Proxmox."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nodvard_deck.api.v1 import overview as overview_api
from nodvard_deck.core.metrics_history import resolve_metrics_provider
from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services import hosts as hosts_service
from nodvard_deck.services.hosts import host_to_sdk

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
SSH_MODULES = ("terminal", "system", "service-matrix", "nexus-soc", "scripts")


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    overview_api.reset_cache()
    yield
    overview_api.reset_cache()
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


async def _owner_token(client) -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _manual_host(db_session, test_settings, name: str, address: str, *, tags: list[str] | None = None):
    host = await hosts_service.create_host(db_session, name=name, display_name=name.title(), address=address, tags=tags or [])
    await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password", username="admin", port=22, secret_value="geheim-123",
    )
    return host


async def _setup(client, db_session, test_settings, *, enable: tuple[str, ...]) -> dict:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    headers = await _owner_token(client)
    for ext_id in enable:
        res = await client.post(f"/api/v1/extensions/{ext_id}/enable", headers=headers)
        assert res.status_code == 200 and res.json()["state"] == "enabled", res.text
    return headers


@pytest.mark.asyncio
async def test_menu_widgets_and_overview_have_no_proxmox_and_no_errors(client, db_session, test_settings):
    pi = await _manual_host(db_session, test_settings, "raspberry-pi", "192.168.2.72", tags=["docker"])
    await _manual_host(db_session, test_settings, "nas", "192.168.2.20")
    await db_session.commit()
    headers = await _setup(client, db_session, test_settings, enable=SSH_MODULES)

    pages = (await client.get("/api/v1/pages", headers=headers)).json()
    shown = {p["ext_id"] for p in pages}
    assert {"system", "service-matrix", "nexus-soc", "scripts"} <= shown
    assert not shown & {"proxmox", "backups"}, "Menüeinträge ausgeschalteter Module dürfen nicht erscheinen"

    widgets = (await client.get("/api/v1/widgets", headers=headers)).json()
    assert widgets, "die SSH-Module bringen Widgets mit"
    assert not {w["ext_id"] for w in widgets} & {"proxmox", "backups"}

    # Cockpit: keine Backups-Kachel (kein Anbieter), kein Fehler -- auch wenn der Docker-Server nicht antwortet.
    overview = await client.get("/api/v1/overview", headers=headers)
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["backups"] is None
    assert body["pending_actions"] == 0 and body["attention"] == []

    # Die Module, die nur per SSH arbeiten, brauchen keine Angaben: „Erste Schritte“ nennt kein „Modul einrichten“.
    rows = {e["id"]: e for e in (await client.get("/api/v1/extensions", headers=headers)).json()}
    for ext_id in SSH_MODULES:
        assert rows[ext_id]["state"] == "enabled" and not rows[ext_id]["needs_setup"], ext_id
    assert rows["proxmox"]["state"] == "disabled" and rows["backups"]["state"] == "disabled"

    # Die Server-Seite eines von Hand angelegten Servers bietet die SSH-Werkzeuge an, keine von Proxmox.
    tools = (await client.get(f"/api/v1/hosts/{pi.id}/tools", headers=headers)).json()
    offered = {t["ext_id"] for t in tools}
    assert {"system", "service-matrix", "scripts"} <= offered
    assert not offered & {"proxmox", "backups"}


@pytest.mark.asyncio
async def test_manual_server_gets_metrics_over_ssh_only_with_the_system_module(client, db_session, test_settings):
    """Ist-Stand: Auslastung und Verlauf eines Servers ohne Proxmox kommen vom Modul „System“ (SSH). Ohne
    dieses Modul antwortet der Kern mit 404 -- die Server-Seite zeigt dafür einen Hinweis statt einer Lücke."""
    host = await _manual_host(db_session, test_settings, "raspberry-pi", "192.168.2.72")
    await db_session.commit()
    headers = await _setup(client, db_session, test_settings, enable=("terminal",))

    for path in (f"/api/v1/hosts/{host.id}/metrics", f"/api/v1/hosts/{host.id}/metrics/history?range=1h"):
        res = await client.get(path, headers=headers)
        assert res.status_code == 404, path
    assert await resolve_metrics_provider(host_to_sdk(host), None) is None

    assert (await client.post("/api/v1/extensions/system/enable", headers=headers)).status_code == 200
    provider = await resolve_metrics_provider(host_to_sdk(host), None)
    assert type(provider).__name__ == "SshMetricsProvider"
    # Kein eigener Verlauf des Anbieters: der Kern führt ihn selbst (alle 30 s), unabhängig von Proxmox.
    assert getattr(provider, "history", None) is None
    # Windows-Server und Server ohne SSH-Zugang misst es nicht.
    windows = await hosts_service.create_host(db_session, name="spiele-pc", address="192.168.2.50", os_family="windows")
    bare = await hosts_service.create_host(db_session, name="ohne-zugang", address="192.168.2.60")
    await db_session.commit()
    assert await resolve_metrics_provider(host_to_sdk(windows), None) is None
    assert await resolve_metrics_provider(host_to_sdk(bare), None) is None


@pytest.mark.asyncio
async def test_status_of_manual_servers_only_changes_when_someone_checks(client, db_session, test_settings):
    """Ist-Stand: ein von Hand angelegter Server hat den Zustand „unknown“. Daran ändert
    sich nichts von selbst -- es gibt keine regelmäßige Erreichbarkeitsprüfung. `GET /hosts/{id}/status` prüft
    live, aber die Oberfläche ruft es nicht auf. Dieser Test hält das fest, damit eine spätere Prüfung
    (siehe Bericht) sichtbar etwas ändert."""
    host = await _manual_host(db_session, test_settings, "raspberry-pi", "192.168.2.72")
    await db_session.commit()
    headers = await _setup(client, db_session, test_settings, enable=SSH_MODULES)

    listed = (await client.get("/api/v1/hosts", headers=headers)).json()
    assert [h["status"] for h in listed if h["id"] == host.id] == ["unknown"]
    assert [h["provider_ext_id"] for h in listed if h["id"] == host.id] == [None]
