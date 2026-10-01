"""Modul-Auswahl im Einrichtungsassistenten: Gruppe und Reihenfolge kommen aus dem Manifest
(`category`, `sort_order`), der Kern kennt keine Erweiterung beim Namen."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nodvard_deck.services import extensions as extensions_service
from nodvard_sdk import ExtensionManifest, load_manifest

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
KNOWN_CATEGORIES = {"servers", "security", "tools", "connections", "example"}


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def test_manifest_category_is_optional():
    base = {"id": "demo", "name": "Demo", "version": "1", "api_version": "0.1.0", "entrypoint": "x:Y"}
    manifest = ExtensionManifest(**base)
    assert manifest.category is None
    assert manifest.sort_order == 100
    manifest = ExtensionManifest(**base, category="tools", sort_order=5)
    assert (manifest.category, manifest.sort_order) == ("tools", 5)


def test_every_bundled_extension_has_a_known_category():
    manifests = [load_manifest(path) for path in sorted(REPO_EXTENSIONS_DIR.glob("*/extension.toml"))]
    assert len(manifests) >= 10
    for manifest in manifests:
        assert manifest.category in KNOWN_CATEGORIES, f"{manifest.id}: Gruppe {manifest.category!r}"
    # Je Gruppe eindeutige Reihenfolge, damit die Anzeige nicht vom Zufall abhaengt.
    seen = {(m.category, m.sort_order) for m in manifests}
    assert len(seen) == len(manifests)


async def test_extensions_api_lists_category_and_order(client, db_session, test_settings):
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await db_session.commit()

    rows = {row["id"]: row for row in (await client.get("/api/v1/extensions", headers=headers)).json()}
    assert rows["system"]["category"] == "servers"
    assert rows["system"]["sort_order"] < rows["proxmox"]["sort_order"]
    assert rows["ntfy"]["category"] == "connections"
