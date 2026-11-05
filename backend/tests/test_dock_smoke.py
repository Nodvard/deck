"""Die Modulprüfungen des Rauchtests laufen gegen die echte API."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient
from nodvard_deck.services import extensions as extensions_service
from nodvard_sdk.capabilities import TerminalTarget

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def extension_imports(monkeypatch):
    for name in ("proxmox", "terminal"):
        monkeypatch.syspath_prepend(str(ROOT / "extensions" / name / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_"):
            del sys.modules[name]


@pytest.mark.parametrize("module", ["proxmox", "terminal"])
async def test_smoke_rejects_available_modules(running_app, db_session, test_settings, extension_imports, module):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js wird für den Rauchtest gebraucht")
    base, _ = running_app
    async with AsyncClient(base_url=base) as client:
        created = await client.post("/api/v1/auth/bootstrap", json={
            "username": "owner1", "password": "correct-horse-battery", "setup_code": test_settings.setup_code,
        })
        assert created.status_code == 201, created.text
        login = await client.post("/api/v1/auth/login", json={
            "username": "owner1", "password": "correct-horse-battery",
        })
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]

    script = """
import { assertExcludedModuleApis } from './frontend/smoke/dockModules.mjs';
await assertExcludedModuleApis(path => fetch(new URL(path, process.env.DOCK_SMOKE_URL), {
  headers: { Authorization: `Bearer ${process.env.DOCK_SMOKE_TOKEN}` },
}));
"""

    async def run_check():
        return await asyncio.to_thread(
            subprocess.run, [node, "--input-type=module", "-e", script], cwd=ROOT,
            env={**os.environ, "DOCK_SMOKE_URL": base, "DOCK_SMOKE_TOKEN": token},
            capture_output=True, text=True, timeout=30, check=False,
        )

    absent = await run_check()
    assert absent.returncode == 0, absent.stdout + absent.stderr

    from nodvard_deck.main import app

    settings = test_settings.model_copy(update={"extensions_dir": ROOT / "extensions"})
    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, module)

    from nodvard_deck.ext.runtime import get_extension_runtime

    assert module in get_extension_runtime().loaded
    if module == "terminal":
        assert get_extension_runtime().capabilities.query(TerminalTarget)
    else:
        async with AsyncClient(base_url=base) as client:
            response = await client.get(
                "/api/v1/ext/proxmox/widgets/overview", headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200, response.text

    available = await run_check()
    assert available.returncode != 0
    assert module in available.stderr, available.stdout + available.stderr
