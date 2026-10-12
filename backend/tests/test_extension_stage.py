"""Reifegrad einer Erweiterung (`stage` im Manifest): `stable` (Vorgabe) oder `beta`. Die Oberfläche kennzeichnet
Beta-Module mit einem ruhigen Abzeichen; der Kern kennt dafür kein Modul beim Namen, er liest nur das Manifest.
Dazu: das Modul `backups` heißt in der Oberfläche „Proxmox-Backups“ (Kennung, Adressen und Tabellen bleiben)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from nodvard_sdk import ExtensionManifest, load_manifest
from pydantic import ValidationError

from nodvard_deck.ext import discovery
from nodvard_deck.services import extensions as extensions_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
BASE = {"id": "demo", "name": "Demo", "version": "1", "api_version": "0.1.0", "entrypoint": "x:Y"}


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def test_manifest_without_stage_is_stable():
    assert ExtensionManifest(**BASE).stage == "stable"


def test_manifest_accepts_stable_and_beta():
    assert ExtensionManifest(**BASE, stage="stable").stage == "stable"
    assert ExtensionManifest(**BASE, stage="beta").stage == "beta"


@pytest.mark.parametrize("unknown", ["bta", "Beta", "alpha", ""])
def test_an_unknown_stage_counts_as_beta_and_does_not_break_loading(unknown):
    """Ein Reifegrad, den dieser Kern nicht kennt (Tippfehler oder ein spaeterer Wert), gilt als `beta`."""
    assert ExtensionManifest(**BASE, stage=unknown).stage == "beta"


def test_load_manifest_reads_stage_from_toml(tmp_path):
    path = tmp_path / "extension.toml"
    path.write_text(
        '[extension]\nid = "demo"\nname = "Demo"\nversion = "1"\napi_version = "0.1.0"\nentrypoint = "x:Y"\nstage = "beta"\n',
        encoding="utf-8",
    )
    assert load_manifest(path).stage == "beta"


def test_unknown_stage_in_a_manifest_still_loads_as_beta(tmp_path):
    for ext_id, extra in (("spaeter", 'stage = "experimental"'), ("gesund", 'stage = "beta"')):
        folder = tmp_path / ext_id
        folder.mkdir()
        (folder / "extension.toml").write_text(
            f'[extension]\nid = "{ext_id}"\nname = "X"\nversion = "1"\napi_version = "0.1.0"\nentrypoint = "x:Y"\n{extra}\n',
            encoding="utf-8",
        )
    found = {d.source_path.name: d for d in discovery.scan_directory(tmp_path)}
    assert found["spaeter"].ok is True
    assert found["spaeter"].manifest.stage == "beta"
    assert found["gesund"].ok is True
    assert found["gesund"].manifest.stage == "beta"


def test_bundled_extensions_have_a_known_stage_and_only_gameserver_and_network_are_beta():
    manifests = {m.id: m for m in (load_manifest(p) for p in sorted(REPO_EXTENSIONS_DIR.glob("*/extension.toml")))}
    assert {i for i, m in manifests.items() if m.stage == "beta"} == {"gameserver", "network"}


def test_backups_module_is_called_proxmox_backups_but_keeps_its_id():
    manifest = load_manifest(REPO_EXTENSIONS_DIR / "backups" / "extension.toml")
    assert manifest.name == "Proxmox-Backups"
    assert manifest.id == "backups"
    assert manifest.api_prefix == "/api/v1/ext/backups"
    assert manifest.table_prefix == "ext_backups_"


async def _owner_headers(client) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def test_extensions_api_lists_the_stage_and_the_new_name(client, db_session, test_settings):
    headers = await _owner_headers(client)
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await db_session.commit()

    rows = {row["id"]: row for row in (await client.get("/api/v1/extensions", headers=headers)).json()}
    assert rows["gameserver"]["stage"] == "beta"
    assert rows["network"]["stage"] == "beta"
    assert rows["proxmox"]["stage"] == "stable"
    assert rows["system"]["stage"] == "stable"
    assert rows["backups"]["name"] == "Proxmox-Backups"
    assert rows["backups"]["stage"] == "stable"
    # Die Kennung hinter dem neuen Namen ist unverändert: Adresse und Einzelabruf gehen wie vorher.
    one = await client.get("/api/v1/extensions/backups", headers=headers)
    assert one.status_code == 200
    assert one.json()["name"] == "Proxmox-Backups"
    assert (await client.get("/api/v1/extensions/gameserver", headers=headers)).json()["stage"] == "beta"


async def test_extensions_api_says_stable_for_a_stored_manifest_without_stage(client, db_session, test_settings):
    """Zeilen aus der Zeit vor dem Feld (und Werte, die der Kern nicht kennt) zählen als fertig."""
    from nodvard_deck.models import ExtensionRecord

    headers = await _owner_headers(client)
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    for ext_id, manifest_stage in (("gameserver", None), ("network", "zukunft")):
        record = await db_session.get(ExtensionRecord, ext_id)
        manifest = dict(record.manifest)
        manifest.pop("stage", None)
        if manifest_stage:
            manifest["stage"] = manifest_stage
        record.manifest = manifest
    await db_session.commit()

    rows = {row["id"]: row for row in (await client.get("/api/v1/extensions", headers=headers)).json()}
    assert rows["gameserver"]["stage"] == "stable"
    assert rows["network"]["stage"] == "stable"


async def test_pages_api_carries_the_stage_of_the_extension(client, db_session, test_settings):
    headers = await _owner_headers(client)
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    for ext_id in ("gameserver", "network", "inventory", "backups"):
        enabled = await client.post(f"/api/v1/extensions/{ext_id}/enable", headers=headers)
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["state"] == "enabled", enabled.text

    pages = (await client.get("/api/v1/pages", headers=headers)).json()
    stage_of = {p["ext_id"]: p["stage"] for p in pages}
    assert stage_of["gameserver"] == "beta"
    assert stage_of["network"] == "beta"
    assert stage_of["inventory"] == "stable"
    assert stage_of["backups"] == "stable"
    assert next(p for p in pages if p["ext_id"] == "backups")["title"] == "Proxmox-Backups"
    # Auch das Werkzeug auf den Server-Seiten trägt den neuen Namen.
    from nodvard_deck.ext.runtime import get_extension_runtime

    tools = {(ext_id, spec.id): spec.title for ext_id, spec in get_extension_runtime().ui.all_host_tools()}
    assert tools[("backups", "backups")] == "Proxmox-Backups"
