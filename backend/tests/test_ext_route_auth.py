"""Sicherheitsluecke (beim Bau der Container-Verwaltung live gefunden): mehrere
Extension-Router hingen ohne `permission=` -- laut `ApiHandle.include_router()`-
Docstring heisst das "oeffentlich". Live auf der lokalen Instanz nachgewiesen:
`GET /ext/gameserver/servers` lieferte ohne Login 200 samt Join-Code, und ueber
denselben Router liessen sich Start/Stop VORSCHLAGEN (bei `autonomy.mode=full` fuehrt
das Gate den risikoarmen Start sofort aus). Dieser Test haelt fest: ohne Login kommt
bei keiner dieser Routen mehr etwas an -- auch kein Vorschlag im Aktions-Journal.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from nodvard_deck.models import Action
from nodvard_deck.services import extensions as extensions_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


ROUTES = [
    ("GET", "/api/v1/ext/gameserver/servers"),
    ("GET", "/api/v1/ext/gameserver/widgets/servers"),
    ("POST", "/api/v1/ext/gameserver/servers/some-host/start"),
    ("POST", "/api/v1/ext/gameserver/servers/some-host/stop"),
    ("GET", "/api/v1/ext/backups/jobs"),
    ("GET", "/api/v1/ext/backups/jobs/x/history"),
    ("GET", "/api/v1/ext/backups/widgets/summary"),
    ("POST", "/api/v1/ext/backups/jobs/x/retry"),
    ("GET", "/api/v1/ext/proxmox/widgets/overview"),
    ("GET", "/api/v1/ext/proxmox/widgets/node-load"),
    ("GET", "/api/v1/ext/service-matrix/widgets/matrix"),
    ("GET", "/api/v1/ext/service-matrix/containers/h/web/logs"),
    ("GET", "/api/v1/ext/service-matrix/image-updates"),
    ("POST", "/api/v1/ext/service-matrix/image-updates/check"),
    ("GET", "/api/v1/ext/documents/widgets/untagged"),
    ("GET", "/api/v1/ext/inventory/widgets/warranty"),
]


@pytest.mark.asyncio
async def test_extension_routes_reject_anonymous_requests(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    for ext_id in ("proxmox", "gameserver", "backups", "service-matrix", "documents", "inventory"):
        enabled = await client.post(f"/api/v1/extensions/{ext_id}/enable", headers=headers)
        assert enabled.status_code == 200, (ext_id, enabled.text)

    results = {f"{m} {p}": (await client.request(m, p)).status_code for m, p in ROUTES}
    assert results == {key: 401 for key in results}

    # Und nichts ist ueber die Hintertuer im Aktions-Journal gelandet.
    assert (await db_session.execute(select(Action))).scalars().all() == []
