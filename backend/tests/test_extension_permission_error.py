"""Fehlt einer Extension eine Berechtigung, bekommt der Nutzer einen lesbaren Grund
statt eines nackten "Internal Server Error"."""

from __future__ import annotations

from fastapi.testclient import TestClient
from nodvard_sdk.errors import PermissionDenied, SecretNotOwned

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


def test_a_secret_of_someone_else_is_a_conflict_not_a_missing_permission():
    """Liegt unter dem Label einer Erweiterung ein fremdes Geheimnis (etwa von Hand angelegt), ist das kein
    Programmfehler: 409 mit dem Grund und dem Weg, es aufzuloesen -- statt 500 "fehlt eine Berechtigung"."""
    app = create_app()

    async def _foreign() -> None:
        raise SecretNotOwned("demo", "demo-token:pve9")

    async def _login() -> None:
        raise SecretNotOwned("demo", "totp:nutzer:1", login_secret=True)

    for path, handler in (("/api/v1/test-foreign", _foreign), ("/api/v1/test-login", _login)):
        app.add_api_route(path, handler)
        app.router.routes.insert(app.state.ext_mount_index, app.router.routes.pop())
    client = TestClient(app, raise_server_exceptions=False)
    foreign = client.get("/api/v1/test-foreign")
    assert foreign.status_code == 409
    assert foreign.json()["detail"].startswith("Das Geheimnis „demo-token:pve9“ gehört nicht der Erweiterung „demo“.")
    assert "DELETE /api/v1/secrets/{id}" in foreign.json()["detail"]
    assert "fehlt eine Berechtigung" not in foreign.text
    login = client.get("/api/v1/test-login")
    assert login.status_code == 409
    assert "Zwei-Faktor" in login.json()["detail"] and "DELETE" not in login.json()["detail"]
