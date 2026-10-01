"""GET /hosts/{id}/tools -- Werkzeug-Kacheln der Server-Seite (Plesk-Stil).

Beweist die Filterlogik (Host-Art, Tags, Herkunft, Rechte) und die Pfad-Ersetzung mit
Test-Registrierungen -- ohne dass der Kern eine echte Extension kennt."""

from __future__ import annotations

import pytest

from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.services import hosts as hosts_service
from nodvard_sdk import HostToolSpec


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register_tools() -> None:
    """Erst NACH dem Aufsetzen des Test-Clients (der die Laufzeit neu anlegt)."""
    ui = get_extension_runtime().ui
    ui.register_host_tool("fake-hv", HostToolSpec(id="vm-settings", title="VM-Einstellungen", path="/nodes?host={host_id}", kinds=["vm", "lxc"], own_hosts_only=True, category="settings", icon="server"))
    ui.register_host_tool("fake-hv", HostToolSpec(id="node-health", title="Knoten-Gesundheit", path="/nodes?host={host_id}", kinds=["hypervisor"], own_hosts_only=True, category="monitoring"))
    ui.register_host_tool("fake-game", HostToolSpec(id="game", title="Gameserver", path="/servers?host={host_id}", tags=["gameserver"], category="services", order=5))
    ui.register_host_tool("fake-scripts", HostToolSpec(id="run", title="Skript ausführen", path="/scripts?host={host_id}", category="control", permissions=["hosts.execute"]))


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for ext_id in ("fake-hv", "fake-game", "fake-scripts"):
        get_extension_runtime().ui.clear_extension(ext_id)


@pytest.mark.asyncio
async def test_tools_match_kind_tags_and_origin_and_are_grouped(client, db_session):
    token = await _bootstrap_owner(client)
    _register_tools()
    vm = await hosts_service.create_host(db_session, name="game-win", address="10.0.0.9", tags=["gameserver"])
    vm.kind = "vm"
    vm.provider_ext_id = "fake-hv"
    foreign_vm = await hosts_service.create_host(db_session, name="fremd", address="10.0.0.8")
    foreign_vm.kind = "vm"
    foreign_vm.provider_ext_id = "andere-extension"
    await db_session.commit()

    r = await client.get(f"/api/v1/hosts/{vm.id}/tools", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert [(t["ext_id"], t["id"], t["category"]) for t in r.json()] == [
        ("fake-scripts", "run", "control"),
        ("fake-game", "game", "services"),
        ("fake-hv", "vm-settings", "settings"),
    ], "Reihenfolge: Steuerung, Ueberwachung, Dienste, Daten, Einstellungen"
    assert r.json()[2]["href"] == f"/ext/fake-hv/nodes?host={vm.id}"

    other = (await client.get(f"/api/v1/hosts/{foreign_vm.id}/tools", headers=_auth(token))).json()
    assert [t["id"] for t in other] == ["run"], "fremde VM: keine VM-Einstellungen, kein Gameserver-Tag"

    assert (await client.get("/api/v1/hosts/nope/tools", headers=_auth(token))).status_code == 404
    assert (await client.get(f"/api/v1/hosts/{vm.id}/tools")).status_code == 401


@pytest.mark.asyncio
async def test_tools_respect_the_users_permissions(client, db_session):
    """Ein Betrachter ohne hosts.execute sieht "Skript ausfuehren" nicht."""
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    viewer = User(username="gast", password_hash=security.hash_password("gast-passwort-123"), is_active=True)
    viewer.roles.append(roles["viewer"])
    db_session.add(viewer)
    host = await hosts_service.create_host(db_session, name="pi", address="10.0.0.2")
    await db_session.flush()
    login = await client.post("/api/v1/auth/login", json={"username": "gast", "password": "gast-passwort-123"})
    token = login.json()["access_token"]
    _register_tools()

    tools = (await client.get(f"/api/v1/hosts/{host.id}/tools", headers=_auth(token))).json()
    assert "run" not in [t["id"] for t in tools]
