"""Eingabefehler: zu kurze Passwoerter und Benutzernamen kamen als englischer
pydantic-Text bzw. als "HTTP 422" bei der Person an. Jetzt steht `msg` auf Deutsch.
"""

from __future__ import annotations

from typing import Literal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field

from nodvard_deck.api.errors import install_validation_error_handler

SETUP_CODE = "TEST-CODE-2345"


async def _bootstrap(client, username="nico", password="correct-horse-battery"):
    return await client.post(
        "/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": SETUP_CODE}
    )


async def _owner_token(client) -> str:
    assert (await _bootstrap(client, "owner1")).status_code == 201
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    return login.json()["access_token"]


# --- Standard-Pruefungen von pydantic auf Deutsch ---------------------------------------------------


@pytest.mark.asyncio
async def test_zu_kurzes_passwort_kommt_auf_deutsch_und_ohne_eingabe_zurueck(client):
    res = await _bootstrap(client, password="kurz")
    assert res.status_code == 422
    (item,) = res.json()["detail"]
    assert item["loc"] == ["body", "password"]
    assert item["msg"] == "Mindestens 8 Zeichen."
    # Nichts von der Eingabe (und nichts vom Pruefungs-Zusatz) darf zurueckkommen.
    assert set(item) == {"type", "loc", "msg"}
    assert "kurz" not in res.text
    assert (await client.get("/api/v1/auth/bootstrap")).json() == {"needed": True}


@pytest.mark.asyncio
async def test_zu_kurzer_benutzername_kommt_auf_deutsch(client):
    res = await _bootstrap(client, username="Ko")
    assert res.status_code == 422
    (item,) = res.json()["detail"]
    assert item["loc"] == ["body", "username"]
    assert item["msg"] == "Mindestens 3 Zeichen."


@pytest.mark.asyncio
async def test_fehlende_angabe_auf_deutsch(client):
    res = await client.post("/api/v1/auth/bootstrap", json={"username": "nico"})
    assert res.status_code == 422
    assert [(i["loc"][-1], i["msg"]) for i in res.json()["detail"]] == [("password", "Pflichtangabe fehlt.")]


class _Probe(BaseModel):
    name: str = Field(min_length=1, max_length=5)
    count: int = Field(ge=1, le=10)
    mode: Literal["web", "android", "cli"]
    items: list[int] = Field(min_length=1)
    flag: bool


@pytest.mark.asyncio
async def test_weitere_standard_pruefungen_auf_deutsch():
    app = FastAPI()
    install_validation_error_handler(app)

    @app.post("/probe")
    async def probe(body: _Probe) -> dict:
        return {}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        bad = await ac.post("/probe", json={"name": "", "count": 0, "mode": "x", "items": [], "flag": "vielleicht"})
        too_big = await ac.post("/probe", json={"name": "abcdefg", "count": 11, "mode": "web", "items": [1], "flag": True})
        wrong_type = await ac.post("/probe", json={"name": "a", "count": "viele", "mode": "web", "items": [1], "flag": True})
    messages = {i["loc"][-1]: i["msg"] for i in bad.json()["detail"]}
    assert messages == {
        "name": "Darf nicht leer sein.",
        "count": "Mindestens 1.",
        "mode": "Erlaubt ist nur: web, android oder cli.",
        "items": "Mindestens 1 Eintrag.",
        "flag": "Muss „ja“ oder „nein“ sein.",
    }
    assert {i["loc"][-1]: i["msg"] for i in too_big.json()["detail"]} == {"name": "Höchstens 5 Zeichen.", "count": "Höchstens 10."}
    assert {i["loc"][-1]: i["msg"] for i in wrong_type.json()["detail"]} == {"count": "Muss eine ganze Zahl sein."}


@pytest.mark.asyncio
async def test_eigene_pruefungen_behalten_ihren_deutschen_text(client):
    token = await _owner_token(client)
    res = await client.post(
        "/api/v1/hosts", json={"name": "Mein Server", "address": "192.168.2.10"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 422
    assert res.json()["detail"][0]["msg"].startswith("Kurzname: nur Kleinbuchstaben")
