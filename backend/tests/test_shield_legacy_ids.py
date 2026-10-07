"""Nodvard Shield zieht von `nexus-soc` auf `shield` um -- mit den echten Daten einer bestehenden Installation.

Die Kennung wechselt, die gespeicherten Daten nicht: Die Zeile `nexus-soc` in der Registry (mit den Einstellungen),
ihre Zeitplaene, die Tabellen `ext_nexus_soc_*`, die Aktionsarten `nexus_soc.*` und das Geheimnis
`nexus-soc-ollama-key` behalten ihren Namen (`legacy_ids` im Manifest, `ExtensionManifest`). Der Test legt den Stand
von 0.6 in die Datenbank (Zeile mit Einstellungen, alle Zeitplaene, ein Fund in der Quarantaene, offene Vorschlaege)
und startet mit dem heutigen Code der Erweiterung:

* Shield laedt, Einstellungen und Zeitplaene sind dieselben (keine zweite Zeile `shield`, keine doppelten Zeitplaene),
* der offene Vorschlag der alten Version laesst sich freigeben und ausfuehren,
* die alten und die neuen Adressen antworten, die alten stehen im OpenAPI-Schema als veraltet,
* der Rueckweg aufs 0.6-Image (dieselbe Erweiterung unter der Kennung `nexus-soc` ohne `legacy_ids`) findet seine Zeile
  und nichts steht auf `error`; ein erneutes Update danach ebenso, mit den Aenderungen aus der Zeit dazwischen.

Den echten 0.6-Code ersetzt hier eine Kopie des Erweiterungsordners mit der alten Kennung (die Generalprobe mit den
echten Images ist ein eigener Schritt vor dem Deploy).
"""

from __future__ import annotations

import importlib
import importlib.util
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from nodvard_deck.core.gate import find_executor
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import Action, ExtensionRecord, Job, Secret
from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services import jobs as jobs_service
from nodvard_sdk.errors import PermissionDenied
from sqlalchemy import select

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
SHIELD_SRC = REPO_EXTENSIONS_DIR / "shield" / "src"

JOB_KEYS = sorted([
    "defender-quick", "defender-deep", "defender-watch", "defender-briefing", "defender-audit",
    "defender-updates-check", "defender-updates-auto", "defender-guard",
])
SETTINGS_OF_0_6 = {
    "ollama_url": "http://192.168.2.42:11434", "docker_host_tag": "box", "quick_scan_cron": "30 3 * * *",
}
SECRET_LABEL = "nexus-soc-ollama-key"
OLD_ID = "nexus-soc"
OLD_PACKAGE = "nodvard_deck_ext_" + OLD_ID.replace("-", "_")
"""Das Python-Paket der Erweiterung in 0.6. Zusammengesetzt: Der Waechter `scripts/check_legacy_names.py` verbietet den alten
Paketnamen als Text (er soll in keinem Code und keinem Manifest mehr stehen); hier entsteht er nur in einem Wegwerf-Ordner."""


@pytest.fixture(autouse=True)
def _isolate_extension_modules():
    """Die Module der Erweiterungen und `sys.path` nach dem Test so, wie sie vorher waren (andere Tests der Datei-Reihe
    halten Verweise auf ihre Module)."""
    path = list(sys.path)
    modules = {name: module for name, module in sys.modules.items() if name.startswith("nodvard_deck_ext_")}
    yield
    sys.path[:] = path
    for name in [name for name in sys.modules if name.startswith("nodvard_deck_ext_")]:
        del sys.modules[name]
    sys.modules.update(modules)


@pytest.fixture
async def shield_models(db_session):
    """Die Tabellen `ext_nexus_soc_*` (die Test-Datenbank legt nur die Kern-Tabellen an) -- ihr Modul, fuer Zeilen."""
    name = "_shield_models_for_legacy_tests"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, SHIELD_SRC / "nodvard_deck_ext_shield" / "models.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # SQLAlchemy loest `Mapped[...]` ueber sys.modules auf
        spec.loader.exec_module(module)
    conn = await db_session.connection()
    await conn.run_sync(module.Base.metadata.create_all)
    return module


def _old_image_folder(tmp_path: Path) -> Path:
    """Die Erweiterung, wie 0.6 sie auslieferte: Ordner `nexus-soc`, Paket `OLD_PACKAGE`, Kennung `nexus-soc`, kein
    `legacy_ids`. -> der Ordner, der als `extensions_dir` dient."""
    root = tmp_path / "old-image" / "extensions"
    target = root / "nexus-soc"
    source = REPO_EXTENSIONS_DIR / "shield"
    shutil.copytree(
        source, target, ignore=shutil.ignore_patterns("__pycache__", "node_modules", "migrations", "*.test.tsx")
    )
    (target / "src" / "nodvard_deck_ext_shield").rename(target / "src" / OLD_PACKAGE)
    manifest = target / "extension.toml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace('id          = "shield"\nlegacy_ids  = ["nexus-soc"]\n', 'id          = "nexus-soc"\n')
    text = text.replace('"nodvard_deck_ext_shield:Extension"', f'"{OLD_PACKAGE}:Extension"')
    assert 'id          = "nexus-soc"' in text and "legacy_ids" not in text and OLD_PACKAGE in text
    manifest.write_text(text, encoding="utf-8")
    ids = target / "src" / OLD_PACKAGE / "ids.py"
    ids.write_text(ids.read_text(encoding="utf-8").replace('EXT_ID = "shield"', 'EXT_ID = "nexus-soc"'), encoding="utf-8")
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
    """Ein Start des Dashboards: Entdecken, eingeschaltete Erweiterungen laden (an der App der `client`-Fixture)."""
    from nodvard_deck.main import app

    test_settings.extensions_dir = extensions_dir
    await extensions_service.discover_and_sync(db_session, test_settings)
    await extensions_service.load_enabled_from_registry(app, db_session, test_settings)
    await db_session.commit()


async def _stop_process(db_session) -> None:
    """Der Prozess endet: alles Geladene wird entladen, die Registry-Zeilen bleiben wie sie sind (anders als beim
    Ausschalten durch einen Nutzer)."""
    from nodvard_deck.main import app

    for ext_id in list(get_extension_runtime().loaded):
        await extensions_service._unload_extension(app, db_session, ext_id)
    reset_extension_runtime()  # wie ein neuer Prozess: nichts vom letzten Lauf bleibt stehen


async def _create_host(client, headers) -> str:
    r = await client.post("/api/v1/hosts", json={"name": "bastel-pi", "address": "192.168.2.10"}, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _shield_module(name: str):
    """Ein Modul der Erweiterung (`name` ohne das Paket davor)."""
    if str(SHIELD_SRC) not in sys.path:
        sys.path.insert(0, str(SHIELD_SRC))  # `_isolate_extension_modules` stellt `sys.path` wieder her
    return importlib.import_module(f"nodvard_deck_ext_shield.{name}")


def _upgrade_command() -> str:
    """Der Befehl, den die Erweiterung fuer `nexus_soc.upgrade` (alle Updates, apt) baut."""
    return _shield_module("updates").upgrade_command("apt", "all", [])


async def _state_of_0_6(db_session, shield_models, host_id: str) -> dict:
    """Die Datenbank einer Installation mit 0.6: Zeile `nexus-soc` (eingeschaltet, mit Einstellungen), ihre Zeitplaene,
    ein Fund in der Quarantaene und ein offener Vorschlag `nexus_soc.upgrade`. -> Zeitplan-Kennungen je Schluessel
    und die Kennung des Vorschlags."""
    db_session.add(ExtensionRecord(
        id="nexus-soc", version="0.1.0", api_version="0.1.0", state="enabled", source="bundled",
        settings=dict(SETTINGS_OF_0_6), manifest={"id": "nexus-soc", "name": "Nodvard Shield"}, granted_permissions=[],
    ))
    job_ids = {}
    for key in JOB_KEYS:
        job = await jobs_service.upsert_job(
            db_session, ext_id="nexus-soc", ext_job_key=key, name=key, kind="ext", schedule="0 3 * * *", params={},
            enabled=True,
        )
        job_ids[key] = job.id
    db_session.add(shield_models.FindingRecord(
        id="f1", host_id=host_id, host_name="bastel-pi", path="/tmp/eicar.com", signature="EICAR",
        status="quarantined", quarantine_path="/var/lib/nexus-quarantine/f1_eicar.com", original_mode="644",
        detected_at=datetime.now(timezone.utc),
    ))
    proposal = Action(
        ext_id="nexus-soc", action_type="nexus_soc.upgrade", host_id=host_id, risk="medium", status="proposed",
        payload={"mode": "all", "manager": "apt", "packages": [], "command": _upgrade_command()},
        proposed_by_type="extension", proposed_by_id="nexus-soc", reason="Updates einspielen",
        gate_decision={"rule": "autonomy:propose"},
    )
    db_session.add(proposal)
    await db_session.commit()
    return {"jobs": job_ids, "proposal": proposal.id}


async def _jobs(db_session) -> dict[tuple[str, str], str]:
    """(Erweiterung, Schluessel) -> Kennung aller Zeitplaene von Erweiterungen -- in der Datenbank, nicht aus der Identity
    Map."""
    await db_session.commit()
    db_session.expire_all()
    rows = (await db_session.execute(select(Job).where(Job.kind == "ext"))).scalars().all()
    return {(job.ext_id, job.ext_job_key): job.id for job in rows}


async def _row(db_session, ext_id: str) -> ExtensionRecord:
    await db_session.commit()
    db_session.expire_all()
    record = await db_session.get(ExtensionRecord, ext_id)
    assert record is not None, ext_id
    return record


async def _row_ids(db_session) -> list[str]:
    return sorted((await db_session.execute(select(ExtensionRecord.id))).scalars().all())


def _openapi_paths() -> dict:
    from nodvard_deck.main import app

    app.openapi_schema = None
    try:
        return app.openapi()["paths"]
    finally:
        app.openapi_schema = None


async def _assert_same_state(db_session, state: dict, *, expected_settings: dict) -> None:
    """Nach jedem Start: eine Zeile (die alte), nicht auf `error`, dieselben Einstellungen und dieselben Zeitplaene."""
    assert "shield" not in await _row_ids(db_session), "keine zweite Zeile `shield`"
    record = await _row(db_session, "nexus-soc")
    assert (record.state, record.last_error) == ("enabled", None)
    assert record.settings == expected_settings
    assert not [r for r in (await db_session.execute(select(ExtensionRecord))).scalars().all() if r.state == "error"]
    jobs = await _jobs(db_session)
    assert jobs == {("nexus-soc", key): job_id for key, job_id in state["jobs"].items()}, "dieselben Zeitplaene, keine doppelten"


# -- bestehende Installation: 0.6 -> heutiger Stand -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_installation_of_0_6_keeps_its_settings_jobs_findings_and_proposals(
    client, db_session, test_settings, shield_models, monkeypatch
):
    headers = await _owner_headers(client)
    host_id = await _create_host(client, headers)
    state = await _state_of_0_6(db_session, shield_models, host_id)

    await _boot(db_session, test_settings, REPO_EXTENSIONS_DIR)

    runtime = get_extension_runtime()
    loaded = runtime.loaded["shield"]
    assert loaded.store_id == "nexus-soc" and runtime.legacy_owner == {"nexus-soc": "shield"}
    await _assert_same_state(db_session, state, expected_settings=SETTINGS_OF_0_6)
    assert await loaded.ctx.settings.get() == SETTINGS_OF_0_6
    # Der Zeitplan hat die Einstellung uebernommen (Zeile des Jobs bleibt, nur der Plan wird nachgezogen).
    quick = await db_session.get(Job, state["jobs"]["defender-quick"])
    assert quick.schedule == "30 3 * * *"
    assert runtime.scheduler.get("nexus-soc", "defender-quick") is not None
    assert runtime.scheduler.get("shield", "defender-quick") is None

    # Alte und neue Adressen antworten gleich; die alten stehen im Schema als veraltet.
    new = await client.get("/api/v1/ext/shield/defender/findings", headers=headers)
    old = await client.get("/api/v1/ext/nexus-soc/defender/findings", headers=headers)
    assert new.status_code == old.status_code == 200, (new.text, old.text)
    assert [f["id"] for f in new.json()] == ["f1"] and old.json() == new.json()
    paths = _openapi_paths()
    assert paths["/api/v1/ext/nexus-soc/defender/findings"]["get"].get("deprecated") is True
    assert not paths["/api/v1/ext/shield/defender/findings"]["get"].get("deprecated")
    assert (await client.get("/api/v1/ext/nexus-soc/defender/findings")).status_code == 401, "dieselbe Anmeldepruefung"
    info = (await client.get("/api/v1/extensions/nexus-soc", headers=headers)).json()
    assert (info["id"], info["legacy_ids"], info["state"]) == ("shield", ["nexus-soc"], "enabled")
    values = (await client.get("/api/v1/extensions/nexus-soc/settings", headers=headers)).json()["values"]
    assert values == SETTINGS_OF_0_6
    assert values == (await client.get("/api/v1/extensions/shield/settings", headers=headers)).json()["values"]

    # Der offene Vorschlag der alten Version: Ausfuehrer gefunden, Name statt Kennungen, freigeben und ausfuehren.
    assert await find_executor("nexus_soc.upgrade") is not None
    shown = (await client.get(f"/api/v1/actions/{state['proposal']}", headers=headers)).json()
    assert (shown["ext_id"], shown["proposed_by_label"]) == ("nexus-soc", "Nodvard Shield")
    assert (shown["ext_name"], shown["action_label"]) == ("Nodvard Shield", "System-Updates einspielen")
    calls: list[tuple] = []

    async def fake_execute(host, mode, *, command, trigger):
        calls.append((host.id, mode, command, trigger))
        return True, "Updates eingespielt", "fertig\n", 0

    monkeypatch.setattr(loaded.instance._updates, "execute", fake_execute)
    approved = await client.post(f"/api/v1/actions/{state['proposal']}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "succeeded"
    assert calls == [(host_id, "all", _upgrade_command(), "manual")]

    # Ein zweiter Start ist derselbe Stand.
    await _stop_process(db_session)
    await _boot(db_session, test_settings, REPO_EXTENSIONS_DIR)
    assert get_extension_runtime().loaded["shield"].store_id == "nexus-soc"
    await _assert_same_state(db_session, state, expected_settings=SETTINGS_OF_0_6)
    await _stop_process(db_session)


@pytest.mark.asyncio
async def test_rollback_to_0_6_and_the_update_again_use_the_same_row_and_leave_nothing_in_error(
    client, db_session, test_settings, shield_models, tmp_path
):
    """Rueckweg aufs alte Image und erneutes Update: jeweils ein frischer Start. Das alte Image kennt die neue Kennung
    nicht (dieselbe Erweiterung heisst dort `nexus-soc`, ohne `legacy_ids`); es findet seine Zeile mit den Einstellungen
    und Zeitplaenen wieder, nichts steht auf `error`. Eine Einstellung aus der Zeit dazwischen gilt auch im neuen Image."""
    headers = await _owner_headers(client)
    host_id = await _create_host(client, headers)
    state = await _state_of_0_6(db_session, shield_models, host_id)
    old_image = _old_image_folder(tmp_path)

    await _boot(db_session, test_settings, REPO_EXTENSIONS_DIR)
    await _assert_same_state(db_session, state, expected_settings=SETTINGS_OF_0_6)
    await _stop_process(db_session)

    # Rueckweg: 0.6 laedt dieselbe Zeile, nichts steht auf `error`, alles Alte laeuft (auch der Vorschlag).
    await _boot(db_session, test_settings, old_image)
    runtime = get_extension_runtime()
    assert list(runtime.loaded) == ["nexus-soc"] and runtime.loaded["nexus-soc"].store_id == "nexus-soc"
    assert not runtime.refused and not runtime.legacy_owner
    await _assert_same_state(db_session, state, expected_settings=SETTINGS_OF_0_6)
    assert await find_executor("nexus_soc.upgrade") is not None
    found = await client.get("/api/v1/ext/nexus-soc/defender/findings", headers=headers)
    assert found.status_code == 200 and [f["id"] for f in found.json()] == ["f1"]
    assert (await client.get("/api/v1/ext/shield/defender/findings", headers=headers)).status_code == 404
    during = {**SETTINGS_OF_0_6, "docker_host_tag": "waehrend-des-rueckwegs"}
    await runtime.loaded["nexus-soc"].ctx.settings.set(during)
    await _stop_process(db_session)

    # Erneutes Update: wieder dieselbe Zeile, mit der Einstellung aus dem alten Image.
    await _boot(db_session, test_settings, REPO_EXTENSIONS_DIR)
    assert get_extension_runtime().loaded["shield"].store_id == "nexus-soc"
    await _assert_same_state(db_session, state, expected_settings=during)
    assert (await client.get("/api/v1/ext/shield/defender/findings", headers=headers)).status_code == 200
    await _stop_process(db_session)


@pytest.mark.asyncio
async def test_shield_still_reads_its_ai_key_under_the_old_label(client, db_session, test_settings, shield_models):
    """Das Geheimnis liegt unter `nexus-soc-ollama-key`. Das Recht dazu steht im Manifest (`secrets.read:nexus-soc-*`),
    es haengt nicht an der Kennung: Shield liest den Schluessel, den die Installation vor dem Umzug gespeichert hat; ein
    Label ausserhalb des Musters bleibt gesperrt."""
    headers = await _owner_headers(client)
    host_id = await _create_host(client, headers)
    await _state_of_0_6(db_session, shield_models, host_id)
    await _boot(db_session, test_settings, REPO_EXTENSIONS_DIR)
    loaded = get_extension_runtime().loaded["shield"]

    # Auch das Speichern ueber die alte Adresse der Erweiterung geht (die Seite eines alten Bundles).
    stored = await client.put(
        "/api/v1/extensions/nexus-soc/secrets", json={"label": SECRET_LABEL, "value": "key-geheim"}, headers=headers
    )
    assert stored.status_code == 204, stored.text
    assert (await db_session.execute(select(Secret.owner_ext_id).where(Secret.label == SECRET_LABEL))).scalar_one() == "shield"

    handle = await loaded.ctx.secrets.get_handle(SECRET_LABEL)
    assert handle.label == SECRET_LABEL
    ollama = _shield_module("ollama")
    assert await ollama._auth_headers(loaded.ctx) == {"Authorization": "Bearer key-geheim"}
    for other in ("shield-ollama-key", "proxmox-token:pve1"):
        with pytest.raises(PermissionDenied):
            await loaded.ctx.secrets.get_handle(other)
    await _stop_process(db_session)


# -- neue Installation ------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_installation_stores_everything_under_shield_and_the_old_addresses_still_work(
    client, db_session, test_settings, shield_models
):
    headers = await _owner_headers(client)
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    ids = await _row_ids(db_session)
    assert "shield" in ids and "nexus-soc" not in ids

    enabled = await client.post("/api/v1/extensions/shield/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text
    loaded = get_extension_runtime().loaded["shield"]
    assert loaded.store_id == "shield"
    saved = await client.put(
        "/api/v1/extensions/nexus-soc/settings", json={"values": {"docker_host_tag": "box"}}, headers=headers
    )
    assert saved.status_code == 200, saved.text
    assert (await _row(db_session, "shield")).settings["docker_host_tag"] == "box"
    assert "nexus-soc" not in await _row_ids(db_session)
    jobs = await _jobs(db_session)
    assert {ext for ext, _ in jobs} == {"shield"} and sorted(key for _, key in jobs) == JOB_KEYS

    new = await client.get("/api/v1/ext/shield/defender/findings", headers=headers)
    old = await client.get("/api/v1/ext/nexus-soc/defender/findings", headers=headers)
    assert new.status_code == old.status_code == 200 and new.json() == old.json() == []

    # Neue Vorschlaege tragen die heutige Kennung; ihre Anzeige nennt den Namen, nicht die Kennung.
    proposal = Action(
        ext_id="shield", action_type="nexus_soc.reboot", host_id=None, risk="high", status="proposed", payload={},
        proposed_by_type="extension", proposed_by_id="shield", reason="Neustart", gate_decision={"rule": "autonomy:propose"},
    )
    db_session.add(proposal)
    await db_session.commit()
    shown = (await client.get(f"/api/v1/actions/{proposal.id}", headers=headers)).json()
    assert (shown["proposed_by_label"], shown["ext_name"]) == ("Nodvard Shield", "Nodvard Shield")
    assert shown["action_label"] == "Server neu starten"
    await _stop_process(db_session)


def test_the_manifest_of_the_real_extension_names_its_old_id_and_keeps_the_stored_names():
    """Die Eckdaten der Erweiterung, auf denen alles oben beruht: Kennung, alte Kennung, Einstiegspunkt, Rechte."""
    from nodvard_sdk import load_manifest

    manifest = load_manifest(REPO_EXTENSIONS_DIR / "shield" / "extension.toml")
    assert (manifest.id, manifest.legacy_ids) == ("shield", ["nexus-soc"])
    assert manifest.entrypoint == "nodvard_deck_ext_shield:Extension"
    assert "secrets.read:nexus-soc-*" in manifest.permissions
    assert manifest.table_prefixes == ("ext_shield_", "ext_nexus_soc_")
    assert not (REPO_EXTENSIONS_DIR / "nexus-soc").exists(), "der alte Ordner darf nicht liegen bleiben"
    ids = _shield_module("ids")
    assert (ids.EXT_ID, ids.SOC_PATH, ids.LOGGER) == ("shield", "/ext/shield/soc", "nodvard_deck.ext.shield")
    assert ids.ACTION_PREFIX == "nexus_soc"
