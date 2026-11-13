import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nodvard_deck.services import extensions as extensions_service

EXTENSIONS = Path(__file__).resolve().parents[2] / "extensions"


@pytest.fixture(autouse=True)
def system_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXTENSIONS / "system" / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_system"):
            del sys.modules[name]


def test_reachability_depends_on_the_query_not_on_findings():
    from nodvard_deck_ext_system import widget_row

    host = SimpleNamespace(id="h1", name="heimserver", display_name="Heimserver")
    assert widget_row(host, None, "Anmeldung abgelehnt")["reachable"] is False
    for findings in ([], [{"tone": "danger", "text": "Dienst ausgefallen"}]):
        assert widget_row(host, {"findings": findings}, None)["reachable"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["live", "info"])
async def test_missing_access_returns_conflict_without_ssh(client, db_session, test_settings, monkeypatch, endpoint):
    from nodvard_deck.ext.runtime import get_extension_runtime

    await client.post("/api/v1/auth/bootstrap", json={
        "username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345",
    })
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    test_settings.extensions_dir = EXTENSIONS
    await extensions_service.discover_and_sync(db_session, test_settings)
    assert (await client.post("/api/v1/extensions/system/enable", headers=headers)).status_code == 200
    ctx = get_extension_runtime().loaded["system"].ctx
    host = SimpleNamespace(id="h1", name="heimserver", display_name="Heimserver", os_family="linux", has_credential=False)
    monkeypatch.setattr(ctx.hosts, "get", AsyncMock(return_value=host))
    run = AsyncMock(side_effect=RuntimeError("Kein SSH-Zugang"))
    monkeypatch.setattr(ctx.exec, "run", run)

    response = await client.get(f"/api/v1/ext/system/hosts/h1/{endpoint}", headers=headers)

    assert response.status_code == 409
    assert "Heimserver" in response.json()["detail"]
    assert "SSH-Zugang" in response.json()["detail"]
    assert "Einstellungen → Server & Zugänge" in response.json()["detail"]
    run.assert_not_awaited()
