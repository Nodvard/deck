"""Modul `docker` mit der alten Kennung `service-matrix`: Gespeichertes bleibt unter der alten Kennung, alte Adressen
antworten weiter. Das alte Image ersetzt eine Kopie des Modulordners mit der alten Kennung."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from nodvard_deck.core.gate import find_executor
from nodvard_deck.ext.context import CapabilitiesHandle
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import Action, ExtensionRecord, Job
from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services import hosts as hosts_service
from nodvard_deck.services import jobs as jobs_service
from nodvard_sdk import ServiceCatalog
from nodvard_sdk.types import ExecResult
from sqlalchemy import select

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"

OLD_ID = "service-matrix"
OLD_PACKAGE = "nodvard_deck_ext_" + OLD_ID.replace("-", "_")
"""Zusammengesetzt, weil `scripts/check_legacy_names.py` den alten Paketnamen als Text verbietet."""
SETTINGS_BEFORE = {
    "docker_host_tag": "box",
    "image_updates_enabled": True,
    "image_updates_cron": "15 5 * * *",
    "url_overrides": {"h1:web": "https://web.example.org"},
}
STATE_FILE = {"notified": {"h1": ["web|sha256:abc"]}}


@pytest.fixture(autouse=True)
def _isolate_extension_modules():
    path = list(sys.path)
    modules = {name: module for name, module in sys.modules.items() if name.startswith("nodvard_deck_ext_")}
    yield
    sys.path[:] = path
    for name in [name for name in sys.modules if name.startswith("nodvard_deck_ext_")]:
        del sys.modules[name]
    sys.modules.update(modules)


def _old_image_folder(tmp_path: Path) -> Path:
    """Das Modul, wie es vor dem Umzug ausgeliefert wurde: Ordner, Paket und Kennung `service-matrix`, kein `legacy_ids`."""
    root = tmp_path / "old-image" / "extensions"
    target = root / OLD_ID
    shutil.copytree(
        REPO_EXTENSIONS_DIR / "docker", target, ignore=shutil.ignore_patterns("__pycache__", "node_modules", "*.test.tsx")
    )
    (target / "src" / "nodvard_deck_ext_docker").rename(target / "src" / OLD_PACKAGE)
    manifest = target / "extension.toml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace('id          = "docker"\nlegacy_ids  = ["service-matrix"]\n', f'id          = "{OLD_ID}"\n')
    text = text.replace('"nodvard_deck_ext_docker:Extension"', f'"{OLD_PACKAGE}:Extension"')
    assert f'id          = "{OLD_ID}"' in text and "legacy_ids" not in text and OLD_PACKAGE in text
    manifest.write_text(text, encoding="utf-8")
    return root


async def _owner_headers(client) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _boot(db_session, test_settings, extensions_dir: Path) -> None:
    from nodvard_deck.main import app

    test_settings.extensions_dir = extensions_dir
    await extensions_service.discover_and_sync(db_session, test_settings)
    await extensions_service.load_enabled_from_registry(app, db_session, test_settings)
    await db_session.commit()


async def _stop_process(db_session) -> None:
    from nodvard_deck.main import app

    for ext_id in list(get_extension_runtime().loaded):
        await extensions_service._unload_extension(app, db_session, ext_id)
    reset_extension_runtime()


async def _state_before(db_session, test_settings) -> dict:
    """Eine Installation vor dem Umzug: eingeschaltete Zeile mit Einstellungen, Zeitplan, Datenordner, offener Vorschlag."""
    host = await hosts_service.create_host(db_session, name="pi-host", address="10.0.0.5", tags=["box"])
    db_session.add(ExtensionRecord(
        id=OLD_ID, version="0.1.0", api_version="0.1.0", state="enabled", source="bundled",
        settings=json.loads(json.dumps(SETTINGS_BEFORE)), manifest={"id": OLD_ID, "name": "Service-Matrix"},
        granted_permissions=[],
    ))
    job = await jobs_service.upsert_job(
        db_session, ext_id=OLD_ID, ext_job_key="image-updates", name="Image-Updates prüfen", kind="ext",
        schedule="15 5 * * *", params={}, enabled=True,
    )
    data_dir = test_settings.ext_data_dir / OLD_ID
    data_dir.mkdir(parents=True)
    (data_dir / "image-updates-state.json").write_text(json.dumps(STATE_FILE), encoding="utf-8")
    proposal = Action(
        ext_id=OLD_ID, action_type="container.stop", host_id=host.id, risk="medium", status="proposed",
        payload={"container": "web"}, proposed_by_type="extension", proposed_by_id=OLD_ID, reason="Stoppen",
        gate_decision={"rule": "autonomy:propose"},
    )
    db_session.add(proposal)
    await db_session.commit()
    return {"host": host.id, "job": job.id, "proposal": proposal.id}


async def _rows(db_session) -> dict[str, ExtensionRecord]:
    await db_session.commit()
    db_session.expire_all()
    return {r.id: r for r in (await db_session.execute(select(ExtensionRecord))).scalars().all()}


async def _ext_jobs(db_session) -> dict[tuple[str, str], str]:
    await db_session.commit()
    db_session.expire_all()
    rows = (await db_session.execute(select(Job).where(Job.kind == "ext"))).scalars().all()
    return {(job.ext_id, job.ext_job_key): job.id for job in rows}


async def _assert_same_state(db_session, state: dict, *, settings: dict) -> None:
    rows = await _rows(db_session)
    assert "docker" not in rows, "keine zweite Zeile `docker`"
    assert (rows[OLD_ID].state, rows[OLD_ID].last_error) == ("enabled", None)
    assert rows[OLD_ID].settings == settings
    assert not [r for r in rows.values() if r.state == "error"]
    assert await _ext_jobs(db_session) == {(OLD_ID, "image-updates"): state["job"]}


def _openapi_paths() -> dict:
    from nodvard_deck.main import app

    app.openapi_schema = None
    try:
        return app.openapi()["paths"]
    finally:
        app.openapi_schema = None


@pytest.mark.asyncio
async def test_an_existing_installation_keeps_settings_job_data_and_proposals(
    client, db_session, test_settings, monkeypatch
):
    headers = await _owner_headers(client)
    state = await _state_before(db_session, test_settings)

    await _boot(db_session, test_settings, REPO_EXTENSIONS_DIR)

    runtime = get_extension_runtime()
    loaded = runtime.loaded["docker"]
    assert loaded.store_id == OLD_ID and runtime.legacy_owner[OLD_ID] == "docker"
    await _assert_same_state(db_session, state, settings=SETTINGS_BEFORE)
    assert await loaded.ctx.settings.get() == SETTINGS_BEFORE
    assert runtime.scheduler.get(OLD_ID, "image-updates") is not None
    assert runtime.scheduler.get("docker", "image-updates") is None
    assert Path(str(loaded.ctx.data_dir)) == test_settings.ext_data_dir / OLD_ID
    assert json.loads((Path(str(loaded.ctx.data_dir)) / "image-updates-state.json").read_text()) == STATE_FILE

    new = await client.get("/api/v1/ext/docker/image-updates", headers=headers)
    old = await client.get(f"/api/v1/ext/{OLD_ID}/image-updates", headers=headers)
    assert new.status_code == old.status_code == 200, (new.text, old.text)
    assert old.json() == new.json()
    assert (await client.get(f"/api/v1/ext/{OLD_ID}/image-updates")).status_code == 401
    paths = _openapi_paths()
    assert paths[f"/api/v1/ext/{OLD_ID}/image-updates"]["get"].get("deprecated") is True
    assert not paths["/api/v1/ext/docker/image-updates"]["get"].get("deprecated")
    info = (await client.get(f"/api/v1/extensions/{OLD_ID}", headers=headers)).json()
    assert (info["id"], info["legacy_ids"], info["state"]) == ("docker", [OLD_ID], "enabled")
    old_values = (await client.get(f"/api/v1/extensions/{OLD_ID}/settings", headers=headers)).json()["values"]
    new_values = (await client.get("/api/v1/extensions/docker/settings", headers=headers)).json()["values"]
    assert old_values == new_values and old_values["url_overrides"] == SETTINGS_BEFORE["url_overrides"]

    # Kachel und Seite tragen die alte Kennung mit, damit gespeicherte Cockpits und Links sie finden.
    widget = next(w for w in (await client.get("/api/v1/widgets", headers=headers)).json() if w["ext_id"] == "docker")
    page = next(p for p in (await client.get("/api/v1/pages", headers=headers)).json() if p["ext_id"] == "docker")
    assert (widget["id"], widget["legacy_ext_ids"]) == ("matrix", [OLD_ID])
    assert (page["path"], page["component"], page["legacy_ext_ids"]) == ("/matrix", "ContainersPage", [OLD_ID])

    # Module, die `service-matrix` in `requires` nennen (Netzwerk, NPM), finden den Dienstkatalog weiter.
    catalogs = await CapabilitiesHandle(runtime, "network", [OLD_ID]).query(ServiceCatalog)
    assert catalogs == [loaded.instance._catalog]

    # Der offene Vorschlag von vor dem Umzug läuft durch.
    assert await find_executor("container.stop") is not None
    shown = (await client.get(f"/api/v1/actions/{state['proposal']}", headers=headers)).json()
    assert (shown["ext_id"], shown["ext_name"], shown["action_label"]) == (OLD_ID, "Docker", "Container stoppen")
    commands: list[str] = []

    async def fake_run(host, command, **_kwargs):
        commands.append(command)
        return ExecResult(exit_code=0, stdout="0123456789ab\n" if "inspect" in command else "web\n", stderr="", duration_ms=1)

    monkeypatch.setattr(loaded.ctx.exec, "run", fake_run)
    approved = await client.post(f"/api/v1/actions/{state['proposal']}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "succeeded"
    assert commands[-1] == "docker stop web"

    await _stop_process(db_session)
    await _boot(db_session, test_settings, REPO_EXTENSIONS_DIR)
    assert get_extension_runtime().loaded["docker"].store_id == OLD_ID
    await _assert_same_state(db_session, state, settings=SETTINGS_BEFORE)
    await _stop_process(db_session)


@pytest.mark.asyncio
async def test_changes_under_the_new_id_land_in_the_old_row(client, db_session, test_settings):
    headers = await _owner_headers(client)
    state = await _state_before(db_session, test_settings)
    await _boot(db_session, test_settings, REPO_EXTENSIONS_DIR)

    listed = (await client.get("/api/v1/extensions", headers=headers)).json()
    assert [(e["id"], e["legacy_ids"]) for e in listed if e["id"] in ("docker", OLD_ID)] == [("docker", [OLD_ID])]

    changed = {**SETTINGS_BEFORE, "docker_host_tag": "neu"}
    saved = await client.put("/api/v1/extensions/docker/settings", json={"values": changed}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert (await client.post("/api/v1/extensions/docker/disable", headers=headers)).status_code == 200
    assert (await _rows(db_session))[OLD_ID].state == "disabled"
    assert (await client.post("/api/v1/extensions/docker/enable", headers=headers)).status_code == 200
    await _assert_same_state(db_session, state, settings=changed)

    # Ein Tab von vor dem Update lädt das Bundle über die alte Adresse und fragt nach dem alten Komponentennamen.
    bundle = await client.get(f"/api/v1/extensions/{OLD_ID}/frontend/index.js", headers=headers)
    assert bundle.status_code == 200 and "ServiceMatrixPage" in bundle.text and "ContainersPage" in bundle.text
    await _stop_process(db_session)


@pytest.mark.asyncio
async def test_a_disabled_old_row_stays_disabled(client, db_session, test_settings):
    await _owner_headers(client)
    await _state_before(db_session, test_settings)
    record = (await _rows(db_session))[OLD_ID]
    record.state = "disabled"
    await db_session.commit()

    await _boot(db_session, test_settings, REPO_EXTENSIONS_DIR)

    assert "docker" not in get_extension_runtime().loaded
    rows = await _rows(db_session)
    assert rows[OLD_ID].state == "disabled" and "docker" not in rows
    await _stop_process(db_session)


@pytest.mark.asyncio
async def test_rollback_to_the_old_image_and_the_update_again_use_the_same_row(
    client, db_session, test_settings, tmp_path
):
    headers = await _owner_headers(client)
    state = await _state_before(db_session, test_settings)
    old_image = _old_image_folder(tmp_path)

    await _boot(db_session, test_settings, REPO_EXTENSIONS_DIR)
    await _assert_same_state(db_session, state, settings=SETTINGS_BEFORE)
    await _stop_process(db_session)

    await _boot(db_session, test_settings, old_image)
    runtime = get_extension_runtime()
    assert list(runtime.loaded) == [OLD_ID] and runtime.loaded[OLD_ID].store_id == OLD_ID
    assert not runtime.refused and not runtime.legacy_owner
    await _assert_same_state(db_session, state, settings=SETTINGS_BEFORE)
    assert (await client.get(f"/api/v1/ext/{OLD_ID}/image-updates", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/ext/docker/image-updates", headers=headers)).status_code == 404
    during = {**SETTINGS_BEFORE, "docker_host_tag": "waehrend-des-rueckwegs"}
    await runtime.loaded[OLD_ID].ctx.settings.set(during)
    await _stop_process(db_session)

    await _boot(db_session, test_settings, REPO_EXTENSIONS_DIR)
    assert get_extension_runtime().loaded["docker"].store_id == OLD_ID
    await _assert_same_state(db_session, state, settings=during)
    await _stop_process(db_session)


@pytest.mark.asyncio
async def test_a_new_installation_stores_under_docker_and_the_old_addresses_still_work(client, db_session, test_settings):
    headers = await _owner_headers(client)
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    rows = await _rows(db_session)
    assert "docker" in rows and OLD_ID not in rows

    enabled = await client.post("/api/v1/extensions/docker/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text
    assert get_extension_runtime().loaded["docker"].store_id == "docker"
    saved = await client.put(
        f"/api/v1/extensions/{OLD_ID}/settings", json={"values": {"docker_host_tag": "box"}}, headers=headers
    )
    assert saved.status_code == 200, saved.text
    rows = await _rows(db_session)
    assert rows["docker"].settings["docker_host_tag"] == "box" and OLD_ID not in rows
    assert {ext for ext, _ in await _ext_jobs(db_session)} == {"docker"}

    new = await client.get("/api/v1/ext/docker/image-updates", headers=headers)
    old = await client.get(f"/api/v1/ext/{OLD_ID}/image-updates", headers=headers)
    assert new.status_code == old.status_code == 200 and new.json() == old.json()
    await _stop_process(db_session)


def test_the_manifest_names_its_old_id():
    from nodvard_sdk import load_manifest

    manifest = load_manifest(REPO_EXTENSIONS_DIR / "docker" / "extension.toml")
    assert (manifest.id, manifest.legacy_ids, manifest.name) == ("docker", [OLD_ID], "Docker")
    assert manifest.entrypoint == "nodvard_deck_ext_docker:Extension"
