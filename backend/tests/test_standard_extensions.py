"""Standardauslieferung und Abschalten gespeicherter Module nach einem Update."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import ExtensionRecord
from nodvard_deck.services import extensions as extensions_service
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
OMITTED = {"network", "hello-world"}
pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="braucht eine POSIX-Shell")


@pytest.fixture(autouse=True)
def _cleanup_imports():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_"):
            del sys.modules[name]


@pytest.fixture
def selected_extensions(tmp_path, request):
    folder = tmp_path / "selected-extensions"
    shutil.copytree(ROOT / "extensions", folder, ignore=shutil.ignore_patterns("node_modules", "__pycache__", "dist"))
    done = subprocess.run(
        ["sh", str(ROOT / "deploy" / "select_extensions.sh"), getattr(request, "param", ""), str(folder)],
        capture_output=True, text=True, check=False,
    )
    assert done.returncode == 0, done.stderr
    return folder


async def _owner_headers(client):
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def test_standard_catalog_keeps_functional_modules(client, db_session, test_settings, selected_extensions):
    test_settings.extensions_dir = selected_extensions
    found = await extensions_service.discover_and_sync(db_session, test_settings)
    assert not OMITTED.intersection(found)
    assert {"pihole", "npm", "docker", "documents"} <= found.keys()
    stored = set((await db_session.execute(select(ExtensionRecord.id))).scalars())
    assert stored == set(found)


@pytest.mark.parametrize("ext_id", sorted(OMITTED))
async def test_missing_standard_module_can_still_be_disabled(
    client, db_session, test_settings, selected_extensions, ext_id,
):
    from nodvard_deck.main import app

    headers = await _owner_headers(client)
    test_settings.extensions_dir = ROOT / "extensions"
    await extensions_service.discover_and_sync(db_session, test_settings)
    record = await db_session.get(ExtensionRecord, ext_id)
    record.state = "enabled"
    record.settings = {"kept": "value"}
    await db_session.commit()

    test_settings.extensions_dir = selected_extensions
    found = await extensions_service.discover_and_sync(db_session, test_settings)
    assert ext_id not in found
    await extensions_service.load_enabled_from_registry(app, db_session, test_settings)
    await db_session.commit()
    assert record.state == "enabled"
    assert record.last_error
    assert ext_id not in get_extension_runtime().loaded

    rows = (await client.get("/api/v1/extensions", headers=headers)).json()
    assert next(row for row in rows if row["id"] == ext_id)["state"] == "error"
    disabled = await client.post(f"/api/v1/extensions/{ext_id}/disable", headers=headers)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["state"] == "disabled"
    assert record.state == "disabled" and record.last_error is None
    assert record.settings == {"kept": "value"}


@pytest.mark.parametrize("selected_extensions", ["network"], indirect=True)
async def test_explicit_network_build_remains_switchable(client, db_session, test_settings, selected_extensions):
    headers = await _owner_headers(client)
    test_settings.extensions_dir = selected_extensions
    found = await extensions_service.discover_and_sync(db_session, test_settings)
    assert set(found) == {"network"}

    enabled = await client.post("/api/v1/extensions/network/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["state"] == "enabled"
    pages = (await client.get("/api/v1/pages", headers=headers)).json()
    assert [(page["ext_id"], page["component"]) for page in pages] == [("network", "NetworkPage")]

    disabled = await client.post("/api/v1/extensions/network/disable", headers=headers)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["state"] == "disabled"
    assert (await client.get("/api/v1/pages", headers=headers)).json() == []
