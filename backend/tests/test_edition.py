"""Ausgabe Nodvard Deck oder Nodvard Dock: `GET /edition` und der Name im Branding."""

from __future__ import annotations

import pytest
from nodvard_deck import edition

_OWNER_PW = "correct-horse-battery"


async def _owner(client) -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": _OWNER_PW, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": _OWNER_PW})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.parametrize("value", [None, "", "Dock", "portainer"])
def test_without_a_known_edition_it_is_deck(value):
    class _Settings:
        edition = value

    assert edition.of(_Settings()) is edition.DECK


@pytest.mark.asyncio
async def test_deck_without_login(client):
    response = await client.get("/api/v1/edition")
    assert response.status_code == 200
    assert response.json() == {
        "id": "deck", "product_name": "Nodvard Deck", "service": "nodvard-deck", "image": "ghcr.io/nodvard/deck",
        "choose_modules": True, "repo_tools": True,
    }
    assert (await client.get("/api/v1/branding")).json()["product_name"] == "Nodvard Deck"


@pytest.mark.asyncio
async def test_dock_names_itself(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "edition", "dock")
    assert (await client.get("/api/v1/edition")).json() == {
        "id": "dock", "product_name": "Nodvard Dock", "service": "nodvard-dock", "image": "ghcr.io/nodvard/dock",
        "choose_modules": False, "repo_tools": False,
    }
    branding = (await client.get("/api/v1/branding")).json()
    assert branding["product_name"] == "Nodvard Dock" and branding["short_name"] == "Nodvard Dock"
    manifest = (await client.get("/api/v1/app/manifest.webmanifest")).json()
    assert manifest["name"] == "Nodvard Dock"


@pytest.mark.asyncio
async def test_own_branding_beats_the_edition_name(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "edition", "dock")
    owner = await _owner(client)
    current = (await client.get("/api/v1/branding")).json()
    response = await client.put("/api/v1/branding", json={**current, "product_name": "Container im Keller"}, headers=owner)
    assert response.status_code == 200, response.text
    assert (await client.get("/api/v1/branding")).json()["product_name"] == "Container im Keller"


@pytest.mark.asyncio
async def test_dock_names_itself_in_the_authenticator_app(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "edition", "dock")
    owner = await _owner(client)
    response = await client.post("/api/v1/me/totp/setup", json={"current_password": _OWNER_PW}, headers=owner)
    assert response.status_code == 200, response.text
    assert "issuer=Nodvard%20Dock" in response.json()["otpauth_uri"]


def test_setup_code_banner_names_the_edition():
    from nodvard_deck.core import setup_code

    assert "Nodvard Dock: Ersteinrichtung" in setup_code.banner("K7MQ-X2VD-H9PA", "Nodvard Dock")
    assert "Nodvard Deck: Ersteinrichtung" in setup_code.banner("K7MQ-X2VD-H9PA")
