"""Benutzernamen: „Kollege Max“ wurde als Konto „kollege max“ angelegt, obwohl der
Hinweis „nur Kleinbuchstaben“ sagt -- und jeder Notfall-Befehl braeuchte dann Anfuehrungszeichen.
Neue Konten: nur `a-z 0-9 . - _`. Bestehende Konten mit Leerzeichen melden sich weiter an.
"""

from __future__ import annotations

import pytest

from nodvard_deck.core import security
from nodvard_deck.models import User

SETUP_CODE = "TEST-CODE-2345"


async def _bootstrap(client, username="nico", password="correct-horse-battery"):
    return await client.post(
        "/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": SETUP_CODE}
    )


async def _owner_token(client) -> str:
    assert (await _bootstrap(client, "owner1")).status_code == 201
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    return login.json()["access_token"]


# --- Benutzername: nur a-z 0-9 . - _ ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Kollege Max", "kollege max", "max mustermann ", "mäx", "a b c", "-max", ".max", "max@home", "max/x"])
async def test_einrichtung_lehnt_unzulaessige_benutzernamen_ab(client, name):
    res = await _bootstrap(client, username=name)
    assert res.status_code == 422
    (item,) = res.json()["detail"]
    assert item["loc"] == ["body", "username"]
    assert item["msg"].startswith("Benutzername: Nur Kleinbuchstaben, Ziffern sowie . - und _ erlaubt")
    assert "Leerzeichen" in item["msg"]
    # Es wurde kein Konto angelegt, der Assistent kann es noch einmal versuchen.
    assert (await client.get("/api/v1/auth/bootstrap")).json() == {"needed": True}


@pytest.mark.asyncio
async def test_einrichtung_nach_dem_trimmen_zu_kurz(client):
    res = await _bootstrap(client, username="  ab ")
    assert res.status_code == 422
    assert res.json()["detail"][0]["msg"] == "Benutzername: Mindestens 3 Zeichen."


@pytest.mark.asyncio
@pytest.mark.parametrize(("typed", "stored"), [("Kollege", "kollege"), (" admin ", "admin"), ("max.mustermann", "max.mustermann"), ("a-b_c9", "a-b_c9"), ("007", "007")])
async def test_einrichtung_nimmt_erlaubte_namen_an_und_schreibt_sie_klein(client, typed, stored):
    res = await _bootstrap(client, username=typed)
    assert res.status_code == 201, res.text
    assert res.json()["username"] == stored


@pytest.mark.asyncio
async def test_benutzer_anlegen_lehnt_leerzeichen_ab_und_nimmt_erlaubte_namen_an(client):
    token = await _owner_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    bad = await client.post("/api/v1/users", json={"username": "Kollege Max", "password": "correct-horse-battery"}, headers=headers)
    assert bad.status_code == 422
    assert bad.json()["detail"][0]["msg"].startswith("Benutzername: Nur Kleinbuchstaben, Ziffern sowie . - und _ erlaubt")
    users = (await client.get("/api/v1/users", headers=headers)).json()
    assert [u["username"] for u in users] == ["owner1"]

    ok = await client.post("/api/v1/users", json={"username": "Kollege.Max", "password": "correct-horse-battery"}, headers=headers)
    assert ok.status_code == 201, ok.text
    assert ok.json()["username"] == "kollege.max"


@pytest.mark.asyncio
async def test_bestehendes_konto_mit_leerzeichen_meldet_sich_weiter_an(client, db_session):
    """Konten von vor der Pruefung (z. B. „kollege max“) bleiben nutzbar: Anmelden prueft die
    Schreibweise des Namens nicht, nur Passwort und Konto."""
    db_session.add(User(
        username="kollege max", password_hash=security.hash_password("correct-horse-battery"),
        display_name="", email=None, is_active=True, is_owner=False,
    ))
    await db_session.flush()
    res = await client.post("/api/v1/auth/login", json={"username": " Kollege Max ", "password": "correct-horse-battery"})
    assert res.status_code == 200, res.text
    assert res.json()["user"]["username"] == "kollege max"
