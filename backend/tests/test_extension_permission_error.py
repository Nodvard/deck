"""Fehlt einer Extension eine Berechtigung, bekommt der Nutzer einen lesbaren Grund
statt eines nackten "Internal Server Error"."""

from __future__ import annotations

from fastapi.testclient import TestClient
from nodvard_sdk.errors import PermissionDenied

from nodvard_deck.main import create_app


def test_extension_permission_denied_is_readable():
    app = create_app()

    async def _boom() -> None:
        raise PermissionDenied("backups", "settings.write")

    app.add_api_route("/api/v1/test-boom", _boom)
    # Wie Extension-Routen: VOR dem SPA-Catch-all einhaengen (main.py, ext_mount_index).
    app.router.routes.insert(app.state.ext_mount_index, app.router.routes.pop())
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/test-boom")
    assert response.status_code == 500
    assert response.json()["detail"].startswith(
        "Einer Erweiterung fehlt eine Berechtigung: Extension 'backups' hat die Permission 'settings.write' nicht."
    )
