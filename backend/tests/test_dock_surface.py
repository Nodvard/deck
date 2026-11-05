from __future__ import annotations

import pytest
from nodvard_deck.models import ExtensionRecord
from test_edition import _owner


@pytest.mark.parametrize("path", [
    "/apps", "/overview", "/files/sources", "/console/hosts", "/demo/seed",
    "/hosts/metrics/latest", "/hosts/example/metrics", "/hosts/example/tools",
    "/system/updates/helper", "/terminal/hosts",
])
async def test_dock_rejects_unavailable_routes(client, test_settings, monkeypatch, path):
    monkeypatch.setattr(test_settings, "edition", "dock")
    owner = await _owner(client)
    response = await client.request("POST" if path == "/demo/seed" else "GET", "/api/v1" + path, headers=owner)
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Diese Funktion ist in dieser Ausgabe nicht verfügbar."


async def test_dock_cannot_disable_fixed_modules(client, db_session, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "edition", "dock")
    record = ExtensionRecord(id="docker", version="0.1.0", api_version="0.1.0", state="enabled",
        source="bundled", manifest={}, settings={})
    db_session.add(record)
    await db_session.flush()
    owner = await _owner(client)
    response = await client.post("/api/v1/extensions/docker/disable", headers=owner)
    assert response.status_code == 409
    assert response.json()["detail"] == "Die Module stehen in dieser Ausgabe fest."
    assert record.state == "enabled"


async def test_dock_does_not_offer_module_takeovers(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "edition", "dock")
    owner = await _owner(client)
    response = await client.post("/api/v1/extensions/other/takeover", json={"old_id": "docker"}, headers=owner)
    assert response.status_code == 409


async def test_dock_does_not_list_missing_modules(client, db_session, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "edition", "dock")
    owner = await _owner(client)
    db_session.add(ExtensionRecord(
        id="documents", version="0.1.0", api_version="0.1.0", state="disabled", source="bundled",
        manifest={"name": "Dokumente"}, settings={},
    ))
    await db_session.flush()
    assert (await client.get("/api/v1/extensions", headers=owner)).json() == []
    assert (await client.get("/api/v1/extensions/documents", headers=owner)).status_code == 404


async def test_dock_does_not_issue_host_shell_tickets(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "edition", "dock")
    owner = await _owner(client)
    host = await client.post("/api/v1/hosts", json={
        "name": "containers", "display_name": "Container", "address": "192.0.2.10", "tags": ["docker"],
    }, headers=owner)
    assert host.status_code == 201, host.text
    response = await client.post("/api/v1/terminal/sessions", json={"host_id": host.json()["id"]}, headers=owner)
    assert response.status_code == 404


async def test_deck_still_offers_core_routes(client):
    owner = await _owner(client)
    for path in ["/apps", "/overview", "/files/sources", "/console/hosts"]:
        assert (await client.get("/api/v1" + path, headers=owner)).status_code == 200


async def test_dock_settings_name_the_product_without_changing_values(client, db_session, test_settings, monkeypatch):
    from nodvard_deck.api.v1 import extension_settings
    from nodvard_deck.ext.runtime import get_extension_runtime

    monkeypatch.setattr(test_settings, "edition", "dock")
    get_extension_runtime().discovered["docker"] = object()
    monkeypatch.setattr(extension_settings, "schema_for", lambda _: {
        "type": "object", "properties": {"label": {"type": "string", "description": "Nodvard Deck prüft die Images. Deck verwaltet die Server."}},
    })
    db_session.add(ExtensionRecord(id="docker", version="0.1.0", api_version="0.1.0", state="enabled",
        source="bundled", manifest={}, settings={"label": "Nodvard Deck"}))
    await db_session.flush()
    owner = await _owner(client)
    data = (await client.get("/api/v1/extensions/docker/settings", headers=owner)).json()
    assert data["schema"]["properties"]["label"]["description"] == "Nodvard Dock prüft die Images. Dock verwaltet die Server."
    assert data["values"]["label"] == "Nodvard Deck"
    assert extension_settings.schema_for("docker")["properties"]["label"]["description"] == "Nodvard Deck prüft die Images. Deck verwaltet die Server."


def test_dock_api_document_only_offers_available_routes(test_settings, monkeypatch):
    from nodvard_deck import config
    from nodvard_deck.main import create_app

    monkeypatch.setattr(test_settings, "edition", "dock")
    monkeypatch.setattr(config, "_settings", test_settings)
    schema = create_app().openapi()
    assert schema["info"]["title"] == "Nodvard Dock"
    assert "/api/v1/files/sources" not in schema["paths"]
    assert "/api/v1/extensions/{ext_id}/disable" not in schema["paths"]
    assert "/api/v1/hosts" in schema["paths"]
    assert "/api/v1/terminal/sessions" in schema["paths"]


@pytest.mark.parametrize("action", ["apply", "rollback"])
async def test_dock_refuses_repo_update_actions(client, test_settings, monkeypatch, action):
    monkeypatch.setattr(test_settings, "edition", "dock")
    owner = await _owner(client)
    response = await client.post("/api/v1/system/updates/" + action, json={}, headers=owner)
    assert response.status_code == 404


@pytest.mark.parametrize("sudo", [False, True])
async def test_dock_setup_notes_only_describe_container_access(client, test_settings, monkeypatch, sudo):
    monkeypatch.setattr(test_settings, "edition", "dock")
    owner = await _owner(client)
    host = (await client.post("/api/v1/hosts", json={"name": "containers", "address": "192.0.2.10"}, headers=owner)).json()
    key = await client.post(f"/api/v1/hosts/{host['id']}/credentials/generate-key", json={}, headers=owner)
    assert key.status_code == 201, key.text
    credential = key.json()["credential"]["id"]
    response = await client.get(f"/api/v1/hosts/{host['id']}/credentials/{credential}/setup", params={"sudo": str(sudo).lower()}, headers=owner)
    assert response.status_code == 200, response.text
    notes = " ".join(response.json()["notes"])
    assert "Container-Konsole" in notes
    assert "Nodvard Dock" in notes
    assert "Dateien" not in notes and "Volle Rechte" not in notes and "Nodvard Deck" not in notes


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_dock_refuses_full_sudo_setup(client, test_settings, monkeypatch, method):
    monkeypatch.setattr(test_settings, "edition", "dock")
    owner = await _owner(client)
    path = "/api/v1/hosts/unknown/credentials/unknown/"
    if method == "GET":
        response = await client.get(path + "setup", params={"full_sudo": "true"}, headers=owner)
    else:
        response = await client.post(path + "setup-with-password", json={"full_sudo": True, "password": "einmalig"}, headers=owner)
    assert response.status_code == 422
    assert response.json()["detail"] == "Volle sudo-Rechte sind in dieser Ausgabe nicht verfügbar."


@pytest.mark.parametrize("method,suffix", [
    ("GET", ""), ("POST", ""), ("PATCH", "/{group}"), ("DELETE", "/{group}"),
    ("POST", "/{group}/members/{host}"), ("DELETE", "/{group}/members/{host}"),
])
async def test_dock_rejects_script_groups(client, test_settings, monkeypatch, method, suffix):
    monkeypatch.setattr(test_settings, "edition", "deck")
    owner = await _owner(client)
    group = (await client.post("/api/v1/host-groups", json={"name": "Gruppe"}, headers=owner)).json()
    host = (await client.post("/api/v1/hosts", json={"name": "containers", "address": "192.0.2.10"}, headers=owner)).json()
    member = f"/api/v1/host-groups/{group['id']}/members/{host['id']}"
    assert (await client.post(member, headers=owner)).status_code == 204
    monkeypatch.setattr(test_settings, "edition", "dock")
    response = await client.request(method, "/api/v1/host-groups" + suffix.format(group=group["id"], host=host["id"]),
                                    json={"name": "Andere Gruppe"}, headers=owner)
    assert response.status_code == 404
    assert response.json()["detail"] == "Diese Funktion ist in dieser Ausgabe nicht verfügbar."
