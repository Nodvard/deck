"""Volle Platte beim Schreiben: der Nutzer bekommt einen lesbaren Grund (507) statt
eines nackten "HTTP 500" -- live gefunden auf dem Pi, als ein abgebrochenes Backup
die Platte gefuellt hatte und "Bestaetigen" auf der Aktionen-Seite nur 500 lieferte."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from nodvard_deck.api.deps import SessionDep
from nodvard_deck.db.session import get_session
from nodvard_deck.main import create_app


def _mount(app, path, endpoint):
    app.add_api_route(path, endpoint)
    # Wie Extension-Routen: VOR dem SPA-Catch-all einhaengen (main.py, ext_mount_index).
    app.router.routes.insert(app.state.ext_mount_index, app.router.routes.pop())


def _sql_error(message: str) -> OperationalError:
    return OperationalError("COMMIT", {}, sqlite3.OperationalError(message))


def test_disk_full_in_route_is_readable():
    app = create_app()

    async def _boom() -> None:
        raise _sql_error("database or disk is full")

    _mount(app, "/api/v1/test-disk-full", _boom)
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/test-disk-full")
    assert response.status_code == 507
    assert "Speicherplatz auf dem Server ist voll" in response.json()["detail"]


def test_disk_full_in_session_commit_is_readable():
    """Genau der Live-Fall: der Fehler kommt erst beim Commit im Teardown der
    Session-Dependency, NACH dem eigentlichen Routen-Code."""
    app = create_app()

    async def _session_failing_on_commit():
        # Wie db.session.session_scope(): erst nach dem Routen-Code wird committet.
        yield None
        raise _sql_error("database or disk is full")

    async def _route(_s: SessionDep) -> dict:
        return {"ok": True}

    app.dependency_overrides[get_session] = _session_failing_on_commit
    _mount(app, "/api/v1/test-disk-full-commit", _route)
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/test-disk-full-commit")
    assert response.status_code == 507
    assert "Speicherplatz auf dem Server ist voll" in response.json()["detail"]


def test_other_operational_errors_stay_plain_500():
    app = create_app()

    async def _boom() -> None:
        raise _sql_error("database is locked")

    _mount(app, "/api/v1/test-locked", _boom)
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/test-locked")
    assert response.status_code == 500
    assert "Speicherplatz" not in response.text
