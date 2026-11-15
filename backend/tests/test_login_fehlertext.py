"""Die Antwort auf eine abgelehnte Anmeldung nennt immer denselben festen Satz."""

from __future__ import annotations

import pytest

from nodvard_deck.services import auth as auth_service

MARKER = "dummy-geheimnis-aus-der-ausnahme"


@pytest.mark.asyncio
async def test_login_refusal_never_carries_the_exception_text(client, monkeypatch):
    async def refuse(*_args, **_kwargs):
        raise auth_service.InvalidCredentials(f"Traceback (most recent call last): {MARKER}")

    monkeypatch.setattr(auth_service, "login", refuse)
    r = await client.post("/api/v1/auth/login", json={"username": "nico", "password": "correct-horse-battery"})
    assert r.status_code == 401
    assert MARKER not in r.text and "Traceback" not in r.text
    assert r.json() == {"detail": "Ungültiger Benutzername oder Passwort."}


@pytest.mark.asyncio
async def test_unknown_user_gets_the_fixed_sentence(client):
    r = await client.post("/api/v1/auth/login", json={"username": "niemand", "password": "correct-horse-battery"})
    assert r.status_code == 401
    assert auth_service.LOGIN_FAILED_MESSAGE == "Ungültiger Benutzername oder Passwort."
    assert r.json() == {"detail": auth_service.LOGIN_FAILED_MESSAGE}
