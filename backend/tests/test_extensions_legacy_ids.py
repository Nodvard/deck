"""Umbenannte Erweiterungen (`legacy_ids` im Manifest): Speicher-Kennung und alte Kennungen im Kern.

Grundidee: Die Kennung wechselt, die gespeicherten Daten nicht. Gibt es keine Registry-Zeile mit der
neuen Kennung, nutzt die Erweiterung die Zeile einer alten Kennung weiter (Einstellungen, Zeitplaene,
Merker), ohne etwas zu kopieren oder umzubenennen -- ein Rueckweg aufs alte Image findet genau seine
Zeile wieder. Ohne `legacy_ids` aendert sich fuer eine Erweiterung nichts.

Test-Erweiterung im tmp-Ordner: Kennung `renamed-ext`, `legacy_ids = ["old-ext"]`, eine Tabelle mit dem
alten Praefix, ein Job, eine Route, eine Seite.
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from nodvard_deck.core.scheduler import get_scheduler_service, reset_scheduler_service
from nodvard_deck.ext.runtime import (
    ExtensionRuntime,
    get_extension_runtime,
    reset_extension_runtime,
)
from nodvard_deck.models import ExtensionRecord, Job, Setting
from nodvard_deck.services import extension_setup
from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services import jobs as jobs_service
from nodvard_deck.services import settings as settings_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
SERVICE_LOGGER = "nodvard_deck.services.extensions"


@pytest.fixture(autouse=True)
def _reset_runtime_and_sys_path(monkeypatch):
    # Ein frueherer Test, der Alembic im Prozess laufen liess, kann vorhandene Logger abgeschaltet haben.
    monkeypatch.setattr(logging.getLogger(SERVICE_LOGGER), "disabled", False)
    reset_extension_runtime()
    reset_scheduler_service()
    before = list(sys.path)
    yield
    reset_extension_runtime()
    reset_scheduler_service()
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


_CODE = '''
from fastapi import APIRouter
from nodvard_sdk import PageSpec
from sqlalchemy import Column, MetaData, String, Table

metadata = MetaData()
for table in TABLES:
    Table(table, metadata, Column("id", String(36), primary_key=True))


class _Tick:
    id = "tick"
    name = "Tick"
    schedule = "*/5 * * * *"
    params = {}
    enabled = True

    async def handler(self, **kwargs):
        return {}


class Extension:
    async def setup(self, ctx):
        ctx.db.declare_tables(metadata)
        router = APIRouter()

        @router.get("/x")
        async def x():
            return {"settings": await ctx.settings.get()}

        ctx.api.include_router(router)
        ctx.ui.register_page(PageSpec(id="main", path="/main", title="Haupt", component="Main"))
        await ctx.scheduler.register_job(_Tick())

    async def on_start(self, ctx):
        pass

    async def on_stop(self, ctx):
        pass
'''


def _write_ext(
    root: Path, ext_id: str, *, legacy_ids: list[str] | None = None, enable_on: list[str] | None = None,
    permissions: list[str] | None = None,
) -> Path:
    module = "nodvard_deck_ext_" + ext_id.replace("-", "_")
    ext_dir = root / ext_id
    (ext_dir / "src" / module).mkdir(parents=True)
    extra = ""
    if legacy_ids:
        extra += "legacy_ids = [" + ", ".join(f'"{x}"' for x in legacy_ids) + "]\n"
    if enable_on:
        extra += "enable_on = [" + ", ".join(f'"{x}"' for x in enable_on) + "]\n"
    granted = ", ".join(f'"{x}"' for x in permissions or ["schedule.register"])
    (ext_dir / "extension.toml").write_text(
        f'[extension]\nid = "{ext_id}"\nname = "Test {ext_id}"\nversion = "0.2.0"\napi_version = "0.1"\n'
        f'entrypoint = "{module}:Extension"\npermissions = [{granted}]\n{extra}',
        encoding="utf-8",
    )
    # Tabellen mit dem Praefix jeder Kennung (nach einer Umbenennung: alte Tabelle plus eine neue).
    tables = [f"ext_{x.replace('-', '_')}_items" for x in [*(legacy_ids or []), ext_id]]
    (ext_dir / "src" / module / "__init__.py").write_text(f"TABLES = {tables!r}\n" + _CODE, encoding="utf-8")
    return ext_dir


def _settings(test_settings, tmp_path):
    return test_settings.model_copy(
        update={"extensions_dir": tmp_path / "extensions", "ext_data_dir": tmp_path / "ext-data"}
    )


async def _boot(app, db_session, settings) -> None:
    """Ein Start des Dashboards: frischer Prozess, Entdecken, eingeschaltete Erweiterungen laden."""
    reset_extension_runtime()
    reset_scheduler_service()
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_"):
            del sys.modules[name]
    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.load_enabled_from_registry(app, db_session, settings)
    await db_session.commit()


def _app() -> FastAPI:
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    return app


def _service_messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == SERVICE_LOGGER]


async def _row_ids(db_session) -> list[str]:
    return sorted((await db_session.execute(select(ExtensionRecord.id))).scalars().all())


async def _setting_keys(db_session) -> list[str]:
    return sorted((await db_session.execute(select(Setting.key))).scalars().all())


async def _jobs(db_session) -> list[tuple[str, str | None, str | None]]:
    rows = (await db_session.execute(select(Job).where(Job.kind == "ext"))).scalars().all()
    return sorted((j.id, j.ext_id, j.ext_job_key) for j in rows)


async def _old_installation(db_session, *, state: str = "enabled") -> str:
    """Stand vor der Umbenennung: Zeile `old-ext` mit Einstellungen und ihrem Job. -> Job-ID."""
    db_session.add(ExtensionRecord(
        id="old-ext", version="0.1.0", api_version="0.1", state=state, source="bundled",
        settings={"wert": 1}, manifest={"id": "old-ext", "name": "Test old-ext"},
        granted_permissions=["schedule.register"],
    ))
    job = await jobs_service.upsert_job(
        db_session, ext_id="old-ext", ext_job_key="tick", name="Tick", kind="ext", schedule="*/5 * * * *",
        params={}, enabled=True,
    )
    await db_session.commit()
    return job.id


async def _stored(db_session, ext_id: str) -> dict:
    """Die Zeile so, wie sie in der Datenbank steht (nicht aus der Identity Map)."""
    await db_session.commit()
    db_session.expire_all()
    record = await db_session.get(ExtensionRecord, ext_id)
    assert record is not None, ext_id
    return {
        "state": record.state, "last_error": record.last_error, "settings": dict(record.settings or {}),
        "version": record.version, "manifest": dict(record.manifest or {}),
    }


# -- bestehende Installation -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renamed_extension_keeps_using_the_row_of_its_old_id(tmp_path, db_session, test_settings, caplog):
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"])
    settings = _settings(test_settings, tmp_path)
    (settings.ext_data_dir / "old-ext").mkdir(parents=True)
    (settings.ext_data_dir / "old-ext" / "merker.txt").write_text("alt", encoding="utf-8")
    job_id = await _old_installation(db_session)
    app = _app()

    with caplog.at_level(logging.INFO, logger=SERVICE_LOGGER):
        await _boot(app, db_session, settings)

    runtime = get_extension_runtime()
    loaded = runtime.loaded["renamed-ext"]
    assert loaded.store_id == "old-ext"
    assert runtime.store_ids["renamed-ext"] == "old-ext" and runtime.legacy_owner == {"old-ext": "renamed-ext"}
    row = await _stored(db_session, "old-ext")
    assert row["state"] == "enabled" and row["last_error"] is None
    assert row["settings"] == {"wert": 1}
    assert row["version"] == "0.2.0" and row["manifest"]["id"] == "renamed-ext"
    assert row["manifest"]["legacy_ids"] == ["old-ext"]
    assert await loaded.ctx.settings.get() == {"wert": 1}
    # Keine zweite Zeile, kein Merker und kein Job unter der neuen Kennung.
    assert await _row_ids(db_session) == ["old-ext"]
    assert not [k for k in await _setting_keys(db_session) if "renamed-ext" in k]
    assert await _jobs(db_session) == [(job_id, "old-ext", "tick")]
    assert runtime.scheduler.get("old-ext", "tick") is not None
    assert get_scheduler_service()._scheduler.get_job(job_id) is not None
    # Alles Sichtbare laeuft unter der neuen Kennung.
    assert [ext_id for ext_id, _ in runtime.ui.all_pages()] == ["renamed-ext"]
    from nodvard_deck.api.deps import get_current_user

    app.dependency_overrides[get_current_user] = lambda: object()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/api/v1/ext/renamed-ext/x")
    assert r.status_code == 200 and r.json() == {"settings": {"wert": 1}}
    # Der Datenordner der alten Kennung gilt weiter; nichts wird verschoben oder neu angelegt.
    assert Path(str(loaded.ctx.data_dir)) == settings.ext_data_dir / "old-ext"
    assert (settings.ext_data_dir / "old-ext" / "merker.txt").read_text(encoding="utf-8") == "alt"
    assert not (settings.ext_data_dir / "renamed-ext").exists()
    assert "Erweiterung 'renamed-ext' nutzt den gespeicherten Stand von 'old-ext'." in caplog.messages

    # Ein zweiter Start ist idempotent.
    await _boot(app, db_session, settings)
    assert get_extension_runtime().loaded["renamed-ext"].store_id == "old-ext"
    assert await _row_ids(db_session) == ["old-ext"]
    assert await _jobs(db_session) == [(job_id, "old-ext", "tick")]
    assert (await _stored(db_session, "old-ext"))["state"] == "enabled"

    await extensions_service.disable_extension(app, db_session, "renamed-ext")


@pytest.mark.asyncio
async def test_rollback_to_the_old_image_and_forward_again_use_the_same_row(tmp_path, db_session, test_settings, caplog):
    """Update, Rueckweg aufs alte Image, erneutes Update -- jeweils ein frischer Start der App. Das alte Image kennt
    die neue Kennung nicht: dieselbe Erweiterung heisst dort `old-ext`, ohne `legacy_ids`. Sie findet ihre Zeile
    unter der alten Kennung wieder, mit den zwischendurch geaenderten Einstellungen, ohne zweiten Job und ohne
    Fehlerzustand; das neue Image danach ebenso. Die Zeile `renamed-ext` entsteht nie."""
    extensions = tmp_path / "extensions"
    settings = _settings(test_settings, tmp_path)
    job_id = await _old_installation(db_session)

    async def start(ext_id: str, *, legacy_ids: list[str] | None = None):
        for folder in extensions.glob("*"):
            shutil.rmtree(folder)
        _write_ext(extensions, ext_id, legacy_ids=legacy_ids)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
            await _boot(_app(), db_session, settings)
        runtime = get_extension_runtime()
        assert list(runtime.loaded) == [ext_id] and runtime.loaded[ext_id].store_id == "old-ext"
        assert not [r.getMessage() for r in caplog.records if r.name == SERVICE_LOGGER], caplog.messages
        assert await _row_ids(db_session) == ["old-ext"]
        assert await _jobs(db_session) == [(job_id, "old-ext", "tick")]
        assert runtime.scheduler.get("old-ext", "tick") is not None
        assert get_scheduler_service()._scheduler.get_job(job_id) is not None
        row = await _stored(db_session, "old-ext")
        assert row["state"] == "enabled" and row["last_error"] is None
        assert row["manifest"]["id"] == ext_id
        assert ("legacy_ids" in row["manifest"]) == bool(legacy_ids)
        ctx = runtime.loaded[ext_id].ctx
        assert Path(str(ctx.data_dir)) == settings.ext_data_dir / "old-ext"
        return ctx, row

    # 1. Update: die Erweiterung heisst jetzt `renamed-ext`, die Zeile `old-ext` gilt weiter.
    ctx, row = await start("renamed-ext", legacy_ids=["old-ext"])
    assert row["settings"] == {"wert": 1}
    await ctx.settings.set({"wert": 2, "neu": "a"})

    # 2. Rueckweg aufs alte Image: dieselbe Zeile, die Einstellung von eben.
    ctx, row = await start("old-ext")
    assert row["settings"] == {"wert": 2, "neu": "a"} and await ctx.settings.get() == row["settings"]
    await ctx.settings.set({"wert": 3, "neu": "b"})

    # 3. Wieder das neue Image: noch immer dieselbe Zeile, mit der Einstellung aus dem alten Image.
    ctx, row = await start("renamed-ext", legacy_ids=["old-ext"])
    assert row["settings"] == {"wert": 3, "neu": "b"} and await ctx.settings.get() == row["settings"]
    assert not (settings.ext_data_dir / "renamed-ext").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("given_id", ["renamed-ext", "old-ext"])
async def test_disabling_a_renamed_extension_removes_its_jobs(tmp_path, db_session, test_settings, given_id):
    """Ausschalten (ueber die neue oder die alte Kennung) nimmt Handler und Zeitplan der Speicher-Kennung
    weg -- sonst liefen die Jobs nach dem Ausschalten weiter."""
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"])
    settings = _settings(test_settings, tmp_path)
    job_id = await _old_installation(db_session)
    app = _app()
    await _boot(app, db_session, settings)
    runtime = get_extension_runtime()
    assert get_scheduler_service()._scheduler.get_job(job_id) is not None

    await extensions_service.disable_extension(app, db_session, given_id)

    assert runtime.loaded == {}
    assert runtime.scheduler.get("old-ext", "tick") is None
    assert get_scheduler_service()._scheduler.get_job(job_id) is None
    assert (await _stored(db_session, "old-ext"))["state"] == "disabled"
    assert await _row_ids(db_session) == ["old-ext"]

    # Wieder einschalten, auch ueber die alte Kennung: dieselbe Zeile, derselbe Job.
    await extensions_service.enable_extension(app, db_session, settings, given_id)
    assert runtime.loaded["renamed-ext"].store_id == "old-ext"
    assert (await _stored(db_session, "old-ext"))["state"] == "enabled"
    assert await _jobs(db_session) == [(job_id, "old-ext", "tick")]
    await extensions_service.disable_extension(app, db_session, "renamed-ext")


@pytest.mark.asyncio
async def test_renamed_extension_missing_at_boot_keeps_the_old_row_enabled(tmp_path, db_session, test_settings):
    """Fehlt die umbenannte Erweiterung beim Start, bleibt die alte Zeile eingeschaltet (nur `last_error`
    sagt, warum); kommt der Ordner zurueck, laedt der naechste Start sie wieder."""
    ext_dir = _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"])
    settings = _settings(test_settings, tmp_path)
    await _old_installation(db_session)
    app = _app()
    away = tmp_path / "weg"
    shutil.move(str(ext_dir), str(away))

    await _boot(app, db_session, settings)
    row = await _stored(db_session, "old-ext")
    assert row["state"] == "enabled" and row["last_error"] and "nicht gefunden" in row["last_error"]
    assert get_extension_runtime().loaded == {}

    shutil.move(str(away), str(ext_dir))
    await _boot(app, db_session, settings)
    row = await _stored(db_session, "old-ext")
    assert (row["state"], row["last_error"]) == ("enabled", None)
    assert get_extension_runtime().loaded["renamed-ext"].store_id == "old-ext"
    await extensions_service.disable_extension(app, db_session, "renamed-ext")


# -- Neuinstallation und verwaiste Zwillinge -----------------------------------------------------------


@pytest.mark.asyncio
async def test_new_installation_creates_a_row_with_the_new_id(tmp_path, db_session, test_settings):
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"])
    settings = _settings(test_settings, tmp_path)

    await extensions_service.discover_and_sync(db_session, settings)
    await db_session.commit()

    assert await _row_ids(db_session) == ["renamed-ext"]
    assert (await _stored(db_session, "renamed-ext"))["state"] == "disabled"
    assert await _setting_keys(db_session) == ["extension.untouched.renamed-ext"]
    runtime = get_extension_runtime()
    assert runtime.store_id("renamed-ext") == runtime.store_id("old-ext") == "renamed-ext"

    await extensions_service.enable_extension(_app(), db_session, settings, "renamed-ext")
    assert Path(str(runtime.loaded["renamed-ext"].ctx.data_dir)) == settings.ext_data_dir / "renamed-ext"
    assert [(ext_id, key) for _, ext_id, key in await _jobs(db_session)] == [("renamed-ext", "tick")]


@pytest.mark.asyncio
async def test_stale_twin_row_is_never_touched_or_loaded(tmp_path, db_session, test_settings, caplog):
    """Neuinstallation (Zeile `renamed-ext`), Rueckweg aufs alte Image (legte `old-ext` an und schaltete sie
    ein), dann wieder neu: die Zeile der neuen Kennung gilt. Der Zwilling bleibt, wie er ist, und laedt nicht
    -- auch nicht, wenn er eingeschaltet ist und die gueltige Zeile aus."""
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"])
    settings = _settings(test_settings, tmp_path)
    db_session.add(ExtensionRecord(
        id="renamed-ext", version="0.2.0", api_version="0.1", state="disabled", source="bundled",
        settings={"neu": 1}, manifest={"id": "renamed-ext"},
    ))
    await _old_installation(db_session)
    twin_before = await _stored(db_session, "old-ext")
    app = _app()

    with caplog.at_level(logging.INFO, logger=SERVICE_LOGGER):
        await _boot(app, db_session, settings)
    assert _service_messages(caplog) == [
        (
            "Erweiterung 'renamed-ext' nutzt die Zeile 'renamed-ext'; die Zeile der alten Kennung 'old-ext' "
            "bleibt unverändert liegen und wird nicht geladen."
        ),
    ]

    runtime = get_extension_runtime()
    assert runtime.loaded == {}, "der eingeschaltete Zwilling darf die Erweiterung nicht laden"
    assert runtime.is_stale_twin("old-ext")
    assert await _stored(db_session, "old-ext") == twin_before
    assert (await extensions_service.get_record(db_session, "old-ext")).id == "renamed-ext"

    await extensions_service.enable_extension(app, db_session, settings, "old-ext")
    loaded = runtime.loaded["renamed-ext"]
    assert loaded.store_id == "renamed-ext"
    assert await loaded.ctx.settings.get() == {"neu": 1}
    assert (await _stored(db_session, "renamed-ext"))["state"] == "enabled"
    assert await _stored(db_session, "old-ext") == twin_before
    await extensions_service.disable_extension(app, db_session, "renamed-ext")
    assert await _stored(db_session, "old-ext") == twin_before


@pytest.mark.asyncio
async def test_several_old_ids_use_the_first_existing_row_in_order(tmp_path, db_session, test_settings, caplog):
    """Zweimal umbenannt (`legacy_ids = ["mid-ext", "old-ext"]`, die juengste zuerst) und beide alten Zeilen
    sind da: es gilt die erste in der Reihenfolge von `legacy_ids`. Die andere ist ein verwaister Zwilling,
    bleibt unveraendert und laedt nicht; das Protokoll nennt beide."""
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["mid-ext", "old-ext"])
    settings = _settings(test_settings, tmp_path)
    db_session.add(ExtensionRecord(
        id="mid-ext", version="0.1.5", api_version="0.1", state="enabled", source="bundled",
        settings={"wert": 2}, manifest={"id": "mid-ext"}, granted_permissions=["schedule.register"],
    ))
    await _old_installation(db_session)
    twin_before = await _stored(db_session, "old-ext")
    jobs_before = await _jobs(db_session)
    app = _app()

    with caplog.at_level(logging.INFO, logger=SERVICE_LOGGER):
        await _boot(app, db_session, settings)

    runtime = get_extension_runtime()
    assert runtime.store_ids == {"renamed-ext": "mid-ext"}
    assert runtime.legacy_owner == {"mid-ext": "renamed-ext", "old-ext": "renamed-ext"}
    assert runtime.is_stale_twin("old-ext") and not runtime.is_stale_twin("mid-ext")
    loaded = runtime.loaded["renamed-ext"]
    assert loaded.store_id == "mid-ext"
    assert await loaded.ctx.settings.get() == {"wert": 2}
    assert await _stored(db_session, "old-ext") == twin_before
    assert await _row_ids(db_session) == ["mid-ext", "old-ext"]
    # Der Job des Zwillings bleibt, wie er ist; der Job der Erweiterung haengt an `mid-ext`.
    jobs_after = await _jobs(db_session)
    assert [j for j in jobs_after if j[1] == "old-ext"] == jobs_before
    assert [(ext_id, key) for _, ext_id, key in jobs_after if ext_id != "old-ext"] == [("mid-ext", "tick")]
    assert _service_messages(caplog) == [
        "Erweiterung 'renamed-ext' nutzt den gespeicherten Stand von 'mid-ext'.",
        (
            "Erweiterung 'renamed-ext' nutzt die Zeile 'mid-ext'; die Zeile der alten Kennung 'old-ext' "
            "bleibt unverändert liegen und wird nicht geladen."
        ),
    ]
    await extensions_service.disable_extension(app, db_session, "old-ext")
    assert (await _stored(db_session, "mid-ext"))["state"] == "disabled"
    assert await _stored(db_session, "old-ext") == twin_before


@pytest.mark.asyncio
async def test_auto_enable_skips_a_stale_twin(tmp_path, db_session, test_settings):
    """Der Zwilling traegt noch den Merker "unberuehrt" (das alte Image hat ihn angelegt): ein neuer
    SSH-Zugang darf darueber nicht die bewusst ausgeschaltete Erweiterung einschalten."""
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"], enable_on=["host_credential"])
    settings = _settings(test_settings, tmp_path)
    db_session.add(ExtensionRecord(
        id="renamed-ext", version="0.2.0", api_version="0.1", state="disabled", source="bundled", manifest={},
    ))
    db_session.add(ExtensionRecord(
        id="old-ext", version="0.1.0", api_version="0.1", state="disabled", source="bundled",
        manifest={"enable_on": ["host_credential"]},
    ))
    await settings_service.set_global(db_session, extensions_service.UNTOUCHED_KEY_PREFIX + "old-ext", True)
    await db_session.commit()
    await extensions_service.discover_and_sync(db_session, settings)
    await db_session.commit()

    enabled = await extensions_service.auto_enable_for(_app(), db_session, settings, "host_credential")

    assert enabled == []
    assert get_extension_runtime().loaded == {}
    assert (await _stored(db_session, "renamed-ext"))["state"] == "disabled"
    assert (await _stored(db_session, "old-ext"))["state"] == "disabled"
    assert "extension.untouched.old-ext" in await _setting_keys(db_session)


@pytest.mark.asyncio
async def test_auto_enable_of_a_renamed_extension_uses_the_old_row(tmp_path, db_session, test_settings):
    """Die alte Zeile ist noch unberuehrt: das automatische Einschalten laedt die umbenannte Erweiterung
    und raeumt den Merker der Speicher-Kennung weg."""
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"], enable_on=["host_credential"])
    settings = _settings(test_settings, tmp_path)
    db_session.add(ExtensionRecord(
        id="old-ext", version="0.1.0", api_version="0.1", state="disabled", source="bundled", manifest={},
    ))
    await settings_service.set_global(db_session, extensions_service.UNTOUCHED_KEY_PREFIX + "old-ext", True)
    await db_session.commit()
    await extensions_service.discover_and_sync(db_session, settings)
    await db_session.commit()

    app = _app()
    enabled = await extensions_service.auto_enable_for(app, db_session, settings, "host_credential")

    assert enabled == ["renamed-ext"]
    assert get_extension_runtime().loaded["renamed-ext"].store_id == "old-ext"
    assert (await _stored(db_session, "old-ext"))["state"] == "enabled"
    assert await _row_ids(db_session) == ["old-ext"]
    assert not [k for k in await _setting_keys(db_session) if k.startswith("extension.untouched.")]
    await extensions_service.disable_extension(app, db_session, "renamed-ext")


# -- Konflikte ----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_old_folder_left_behind_refuses_the_new_extension(tmp_path, db_session, test_settings, caplog):
    """Liegt der alte Ordner noch da, laeuft die alte Erweiterung unveraendert weiter; die neue wird nicht
    geladen und bekommt keine Zeile. Das Protokoll sagt, warum."""
    _write_ext(tmp_path / "extensions", "old-ext")
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"])
    settings = _settings(test_settings, tmp_path)
    job_id = await _old_installation(db_session)
    app = _app()

    with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        await _boot(app, db_session, settings)

    runtime = get_extension_runtime()
    assert list(runtime.loaded) == ["old-ext"]
    assert runtime.loaded["old-ext"].store_id == "old-ext"
    assert runtime.legacy_owner == {} and "renamed-ext" not in runtime.discovered
    assert await _row_ids(db_session) == ["old-ext"]
    row = await _stored(db_session, "old-ext")
    assert (row["state"], row["last_error"], row["manifest"]["id"]) == ("enabled", None, "old-ext")
    assert await _jobs(db_session) == [(job_id, "old-ext", "tick")]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("'renamed-ext' wird nicht geladen" in w and "„old-ext“" in w for w in warnings), warnings
    await extensions_service.disable_extension(app, db_session, "old-ext")


@pytest.mark.asyncio
async def test_refused_extension_with_its_own_row_shows_why(tmp_path, db_session, test_settings):
    """Neuinstallation (eingeschaltete Zeile `renamed-ext`), danach liegt der alte Ordner wieder da: die neue
    Erweiterung wird abgelehnt. Ihre Zeile bleibt eingeschaltet und nennt den eigentlichen Grund (nicht nur
    "nicht gefunden"), auch beim ausdruecklichen Einschalten."""
    _write_ext(tmp_path / "extensions", "old-ext")
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"])
    settings = _settings(test_settings, tmp_path)
    db_session.add(ExtensionRecord(
        id="renamed-ext", version="0.2.0", api_version="0.1", state="enabled", source="bundled",
        settings={"neu": 1}, manifest={"id": "renamed-ext"},
    ))
    await db_session.commit()
    app = _app()

    await _boot(app, db_session, settings)

    runtime = get_extension_runtime()
    assert runtime.loaded == {}
    row = await _stored(db_session, "renamed-ext")
    assert row["state"] == "enabled" and row["settings"] == {"neu": 1}
    assert "„old-ext“" in row["last_error"] and "gehört noch zu einer installierten Erweiterung" in row["last_error"]
    assert "nicht gefunden" not in row["last_error"]
    with pytest.raises(extensions_service.ExtensionLoadError, match="gehört noch zu einer installierten Erweiterung"):
        await extensions_service.enable_extension(app, db_session, settings, "renamed-ext")


HOSTS_WRITE_REFUSAL = (
    "Erweiterungen, die Server anlegen, können noch nicht umbenannt werden (legacy_ids zusammen mit hosts.write)."
)


@pytest.mark.asyncio
async def test_renamed_extension_that_creates_hosts_is_refused_and_the_old_row_stays_as_it_is(
    tmp_path, db_session, test_settings, caplog
):
    """`hosts.write` speichert Server unter der Kennung der Erweiterung (`provider_ext_id`): eine Umbenennung
    liesse die alten Server ohne Anbieter zurueck und legte doppelte an. Der Kern laedt so eine Erweiterung
    nicht, nennt den Grund (Zeile, Protokoll, ausdrueckliches Einschalten) und fasst nichts an."""
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"], permissions=["schedule.register", "hosts.write"])
    settings = _settings(test_settings, tmp_path)
    job_id = await _old_installation(db_session)
    app = _app()

    with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        await _boot(app, db_session, settings)

    runtime = get_extension_runtime()
    assert runtime.loaded == {} and runtime.legacy_owner == {} and "renamed-ext" not in runtime.discovered
    assert runtime.refused == {"renamed-ext": HOSTS_WRITE_REFUSAL}
    assert await _row_ids(db_session) == ["old-ext"]
    row = await _stored(db_session, "old-ext")
    assert row["state"] == "enabled" and row["settings"] == {"wert": 1}  # Entscheidung und Stand bleiben
    assert await _jobs(db_session) == [(job_id, "old-ext", "tick")]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("'renamed-ext' wird nicht geladen" in w and HOSTS_WRITE_REFUSAL in w for w in warnings), warnings


@pytest.mark.parametrize(
    ("extensions", "reason"),
    [
        pytest.param(
            {"renamed-ext": ["schedule.register", "hosts.write"]},
            f"Die neue Version heißt „renamed-ext“. {HOSTS_WRITE_REFUSAL}",
            id="hosts-write",
        ),
        pytest.param(
            {"first-ext": None, "second-ext": None},
            "Die alte Kennung „old-ext“ wird von mehreren Erweiterungen beansprucht („first-ext“, „second-ext“).",
            id="two-claimants",
        ),
    ],
)
@pytest.mark.asyncio
async def test_old_row_of_a_refused_renamed_extension_names_the_reason(
    tmp_path, db_session, test_settings, caplog, extensions, reason
):
    """Update einer bestehenden Installation, die neue Version wird abgelehnt: Die Zeile der alten Kennung bleibt
    eingeschaltet und nennt den eigentlichen Grund (bei einer neuen Version auch deren Kennung) statt "nicht
    gefunden", beim Start und beim ausdruecklichen Einschalten. Im Protokoll steht nur die Ablehnung selbst."""
    for ext_id, permissions in extensions.items():
        _write_ext(tmp_path / "extensions", ext_id, legacy_ids=["old-ext"], permissions=permissions)
    settings = _settings(test_settings, tmp_path)
    job_id = await _old_installation(db_session)
    app = _app()

    with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        await _boot(app, db_session, settings)

    runtime = get_extension_runtime()
    assert runtime.loaded == {} and runtime.legacy_owner == {}
    assert runtime.refused_old == {"old-ext": reason}
    assert await _row_ids(db_session) == ["old-ext"]
    row = await _stored(db_session, "old-ext")
    assert row["state"] == "enabled" and row["settings"] == {"wert": 1}
    assert row["last_error"] == (
        f"Die Erweiterung wird nicht geladen: {reason} "
        "Sie bleibt eingeschaltet und wird beim nächsten Start wieder geladen, sobald das behoben ist."
    )
    assert await _jobs(db_session) == [(job_id, "old-ext", "tick")]
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING and r.name == SERVICE_LOGGER]
    assert sorted(w.split("'")[1] for w in warnings) == sorted(extensions), warnings
    assert all(" wird nicht geladen: " in w for w in warnings), warnings

    with pytest.raises(extensions_service.ExtensionLoadError) as raised:
        await extensions_service.enable_extension(app, db_session, settings, "old-ext")
    assert str(raised.value) == f"Extension 'old-ext' wird nicht geladen: {reason}"
    assert runtime.loaded == {}
    assert await _stored(db_session, "old-ext") == row


@pytest.mark.asyncio
async def test_switched_off_old_row_of_a_refused_renamed_extension_names_the_reason(
    tmp_path, db_session, test_settings
):
    """Dasselbe mit ausgeschalteter alter Zeile: Der Start fasst sie nicht an, das ausdrueckliche Einschalten
    nennt den Grund und aendert nichts."""
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"], permissions=["hosts.write"])
    settings = _settings(test_settings, tmp_path)
    await _old_installation(db_session, state="disabled")
    app = _app()
    await _boot(app, db_session, settings)

    with pytest.raises(extensions_service.ExtensionLoadError, match="„renamed-ext“. Erweiterungen, die Server anlegen"):
        await extensions_service.enable_extension(app, db_session, settings, "old-ext")

    row = await _stored(db_session, "old-ext")
    assert (row["state"], row["last_error"]) == ("disabled", None)
    assert get_extension_runtime().loaded == {}


@pytest.mark.asyncio
async def test_refused_renamed_extension_shows_the_reason_in_its_own_row(tmp_path, db_session, test_settings):
    """Neuinstallation: die Zeile `renamed-ext` ist schon eingeschaltet. Sie nennt den Grund statt "nicht gefunden"
    und bleibt eingeschaltet, bis das Manifest stimmt; auch ein ausdrueckliches Einschalten nennt ihn."""
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"], permissions=["hosts.write"])
    settings = _settings(test_settings, tmp_path)
    db_session.add(ExtensionRecord(
        id="renamed-ext", version="0.2.0", api_version="0.1", state="enabled", source="bundled",
        settings={"neu": 1}, manifest={"id": "renamed-ext"},
    ))
    await db_session.commit()

    app = _app()
    await _boot(app, db_session, settings)

    row = await _stored(db_session, "renamed-ext")
    assert row["state"] == "enabled" and row["settings"] == {"neu": 1}
    assert HOSTS_WRITE_REFUSAL in row["last_error"] and "nicht gefunden" not in row["last_error"]
    with pytest.raises(extensions_service.ExtensionLoadError, match="können noch nicht umbenannt werden"):
        await extensions_service.enable_extension(app, db_session, settings, "renamed-ext")


@pytest.mark.asyncio
async def test_refused_renamed_extension_gets_no_row(tmp_path, db_session, test_settings):
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"], permissions=["hosts.write"])
    settings = _settings(test_settings, tmp_path)

    await _boot(_app(), db_session, settings)

    assert await _row_ids(db_session) == []
    assert get_extension_runtime().discovered == {}


@pytest.mark.asyncio
async def test_hosts_write_alone_is_still_fine(tmp_path, db_session, test_settings):
    """Ohne `legacy_ids` aendert sich nichts: eine Erweiterung mit `hosts.write` laedt wie bisher."""
    _write_ext(tmp_path / "extensions", "plain-ext", permissions=["schedule.register", "hosts.write"])
    settings = _settings(test_settings, tmp_path)
    app = _app()
    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "plain-ext")
    await db_session.commit()

    runtime = get_extension_runtime()
    assert list(runtime.loaded) == ["plain-ext"] and runtime.refused == {}
    assert (await _stored(db_session, "plain-ext"))["state"] == "enabled"
    await extensions_service.disable_extension(app, db_session, "plain-ext")


@pytest.mark.asyncio
async def test_renamed_extension_with_other_permissions_loads(tmp_path, db_session, test_settings):
    """Gegenprobe zur Ablehnung: dieselbe Umbenennung mit Berechtigungen, die nichts unter der Kennung
    speichern (Lesen, Meldungen, Protokoll, Geheimnisse), laeuft wie bisher."""
    _write_ext(
        tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"],
        permissions=["schedule.register", "hosts.read", "hosts.execute", "notify.send", "audit.write", "secrets.read:x-*"],
    )
    settings = _settings(test_settings, tmp_path)
    await _old_installation(db_session)

    app = _app()
    await _boot(app, db_session, settings)

    runtime = get_extension_runtime()
    assert list(runtime.loaded) == ["renamed-ext"] and runtime.refused == {}
    assert runtime.loaded["renamed-ext"].store_id == "old-ext"
    await extensions_service.disable_extension(app, db_session, "renamed-ext")


# -- Einhaengen der Routen unter den alten Kennungen ------------------------------------------------------


def _ext_prefixes(app) -> list[str]:
    """Adress-Praefixe (`/api/v1/ext/<kennung>`) der eingehaengten Erweiterungs-Router, sortiert."""
    prefixes = []
    for route in app.router.routes:
        prefix = getattr(getattr(route, "include_context", None), "prefix", None) or getattr(route, "path", "")
        if prefix.startswith("/api/v1/ext/"):
            prefixes.append(prefix)
    return sorted(prefixes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failing_call", "mounted_before_failure"),
    [(1, []), (2, ["older-ext"]), (3, ["older-ext", "old-ext"])],
    ids=["erste-alte-kennung", "zweite-alte-kennung", "heutige-kennung"],
)
async def test_failing_route_mounting_leaves_no_orphan_routes(
    tmp_path, db_session, test_settings, monkeypatch, failing_call, mounted_before_failure
):
    """Wirft das Einhaengen mitten in der Schleife ueber die alten Kennungen, werden die in diesem Aufruf
    schon eingehaengten Routen wieder entfernt und der Fehler geht weiter nach oben. Danach laesst sich die
    Erweiterung wieder einschalten, jede Kennung steht genau einmal da."""
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext", "older-ext"])
    settings = _settings(test_settings, tmp_path)
    app = _app()
    await extensions_service.discover_and_sync(db_session, settings)
    original = ExtensionRuntime.mount_router
    calls: list[str] = []

    def flaky(self, app_, ext_id, router, **kwargs):
        calls.append(ext_id)
        if len(calls) == failing_call:
            raise RuntimeError("Einhängen kaputt")
        return original(self, app_, ext_id, router, **kwargs)

    monkeypatch.setattr(ExtensionRuntime, "mount_router", flaky)
    routes_before = list(app.router.routes)

    with pytest.raises(RuntimeError, match="Einhängen kaputt"):
        await extensions_service.enable_extension(app, db_session, settings, "renamed-ext")

    assert calls == ["older-ext", "old-ext", "renamed-ext"][:failing_call]
    assert calls[:-1] == mounted_before_failure
    assert app.router.routes == routes_before and _ext_prefixes(app) == []
    assert "renamed-ext" not in get_extension_runtime().loaded

    monkeypatch.setattr(ExtensionRuntime, "mount_router", original)
    await extensions_service.enable_extension(app, db_session, settings, "renamed-ext")
    assert _ext_prefixes(app) == ["/api/v1/ext/old-ext", "/api/v1/ext/older-ext", "/api/v1/ext/renamed-ext"]
    await extensions_service.disable_extension(app, db_session, "renamed-ext")
    assert _ext_prefixes(app) == []


# -- Merker, Testergebnisse, oeffentliche Adressen -----------------------------------------------------


@pytest.mark.asyncio
async def test_untouched_and_test_keys_use_the_store_id(tmp_path, db_session, test_settings):
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"])
    settings = _settings(test_settings, tmp_path)
    await _old_installation(db_session, state="disabled")
    await settings_service.set_global(db_session, extensions_service.UNTOUCHED_KEY_PREFIX + "old-ext", True)
    await db_session.commit()
    await extensions_service.discover_and_sync(db_session, settings)

    await extensions_service.record_user_choice(db_session, "renamed-ext")
    await extension_setup.save_last_test(db_session, "renamed-ext", ok=False, message="kaputt")
    await db_session.commit()
    assert await _setting_keys(db_session) == ["extension.test.old-ext"]
    tests = await extension_setup.load_last_tests(db_session)
    assert tests["old-ext"]["message"] == "kaputt"

    await extension_setup.clear_last_test(db_session, "old-ext")
    await db_session.commit()
    assert await _setting_keys(db_session) == []


def test_public_route_prefixes_accept_the_old_id():
    class _Api:
        public_prefixes = ["/api/v1/ext/renamed-ext/hook"]

    class _Ctx:
        api = _Api()

    class _Loaded:
        ctx = _Ctx()

    runtime = get_extension_runtime()
    runtime.loaded["renamed-ext"] = _Loaded()  # type: ignore[assignment]
    runtime.legacy_owner = {"old-ext": "renamed-ext"}
    assert extensions_service.public_route_prefixes("old-ext") == ["/api/v1/ext/renamed-ext/hook"]
    assert extensions_service.public_route_prefixes("renamed-ext") == ["/api/v1/ext/renamed-ext/hook"]


def _manifest_renamed(legacy_ids=("old-ext",)):
    from nodvard_sdk import ExtensionManifest

    return ExtensionManifest(
        id="renamed-ext", name="x", version="0.1.0", api_version="0.1", entrypoint="m:E", legacy_ids=list(legacy_ids),
    )


def test_data_dir_of_a_new_installation_prefers_its_own_folder(tmp_path):
    """Eigene Zeile (Neuinstallation oder verwaister Zwilling): der eigene Ordner, sonst der vorhandene einer
    alten Kennung -- dort liegen die Dateien zu den Tabellenzeilen, die das alte Image nach einem Rueckweg
    geschrieben hat (die Tabellen teilen alle Kennungen). Verschoben wird nichts."""
    from nodvard_deck.config import Settings

    settings = Settings(data_dir=tmp_path, ext_data_dir=tmp_path / "ext")
    manifest = _manifest_renamed()
    assert extensions_service._data_dir(settings, manifest, "renamed-ext") == tmp_path / "ext" / "renamed-ext"
    (tmp_path / "ext" / "old-ext").mkdir(parents=True)
    assert extensions_service._data_dir(settings, manifest, "renamed-ext") == tmp_path / "ext" / "old-ext"
    (tmp_path / "ext" / "renamed-ext").mkdir()
    assert extensions_service._data_dir(settings, manifest, "renamed-ext") == tmp_path / "ext" / "renamed-ext"
    assert (tmp_path / "ext" / "old-ext").is_dir()


def test_data_dir_of_an_existing_installation_follows_the_store_id(tmp_path):
    """Bestand (Zeile der alten Kennung): der Ordner der Speicher-Kennung, wie Einstellungen und Jobs. Fehlt
    er, wird er angelegt -- nicht der neue: sonst saehe das alte Image nach einem Rueckweg die Dateien
    nicht, die zu den Tabellenzeilen gehoeren."""
    from nodvard_deck.config import Settings

    settings = Settings(data_dir=tmp_path, ext_data_dir=tmp_path / "ext")
    manifest = _manifest_renamed()
    assert extensions_service._data_dir(settings, manifest, "old-ext") == tmp_path / "ext" / "old-ext"
    (tmp_path / "ext" / "old-ext").mkdir(parents=True)
    (tmp_path / "ext" / "renamed-ext").mkdir()
    assert extensions_service._data_dir(settings, manifest, "old-ext") == tmp_path / "ext" / "old-ext"


def test_data_dir_without_legacy_ids_is_always_the_own_folder(tmp_path):
    from nodvard_sdk import ExtensionManifest

    from nodvard_deck.config import Settings

    settings = Settings(data_dir=tmp_path, ext_data_dir=tmp_path / "ext")
    manifest = ExtensionManifest(id="plain-ext", name="x", version="0.1.0", api_version="0.1", entrypoint="m:E")
    assert extensions_service._data_dir(settings, manifest, "plain-ext") == tmp_path / "ext" / "plain-ext"
    (tmp_path / "ext" / "plain-ext").mkdir(parents=True)
    assert extensions_service._data_dir(settings, manifest, "plain-ext") == tmp_path / "ext" / "plain-ext"


@pytest.mark.asyncio
async def test_existing_installation_without_a_data_folder_creates_the_old_one(tmp_path, db_session, test_settings):
    """Bestand, die alte Erweiterung lief nie (kein `data/ext/old-ext`): die umbenannte legt ihren Datenordner
    unter der Speicher-Kennung an, damit ihn das alte Image nach einem Rueckweg wiederfindet."""
    _write_ext(tmp_path / "extensions", "renamed-ext", legacy_ids=["old-ext"])
    settings = _settings(test_settings, tmp_path)
    await _old_installation(db_session)
    app = _app()

    await _boot(app, db_session, settings)

    loaded = get_extension_runtime().loaded["renamed-ext"]
    assert loaded.store_id == "old-ext"
    assert Path(str(loaded.ctx.data_dir)) == settings.ext_data_dir / "old-ext"
    assert (settings.ext_data_dir / "old-ext").is_dir()
    assert not (settings.ext_data_dir / "renamed-ext").exists()
    await extensions_service.disable_extension(app, db_session, "renamed-ext")


# -- ohne legacy_ids aendert sich nichts --------------------------------------------------------------


@pytest.mark.asyncio
async def test_extension_without_legacy_ids_behaves_exactly_as_before(tmp_path, db_session, test_settings, caplog):
    """Die wichtigste Eigenschaft: fuer eine Erweiterung ohne `legacy_ids` sind Zeile, gespeichertes
    Manifest, Merker, Testergebnis, Job, Datenordner und Protokoll genau wie vor dieser Funktion."""
    from nodvard_sdk import load_manifest

    ext_dir = _write_ext(tmp_path / "extensions", "plain-ext")
    settings = _settings(test_settings, tmp_path)
    app = _app()

    with caplog.at_level(logging.INFO, logger=SERVICE_LOGGER):
        await extensions_service.discover_and_sync(db_session, settings)
        await db_session.commit()
        expected_manifest = load_manifest(ext_dir / "extension.toml").model_dump(mode="json")
        expected_manifest.pop("legacy_ids")
        assert (await _stored(db_session, "plain-ext"))["manifest"] == expected_manifest
        assert await _setting_keys(db_session) == ["extension.untouched.plain-ext"]

        await extensions_service.enable_extension(app, db_session, settings, "plain-ext")
        await extensions_service.record_user_choice(db_session, "plain-ext")
        await extension_setup.save_last_test(db_session, "plain-ext", ok=True, message="ok")
        await db_session.commit()

    runtime = get_extension_runtime()
    assert runtime.legacy_owner == {} and runtime.store_ids == {"plain-ext": "plain-ext"}
    loaded = runtime.loaded["plain-ext"]
    assert loaded.store_id == "plain-ext"
    assert Path(str(loaded.ctx.data_dir)) == settings.ext_data_dir / "plain-ext"
    assert await _row_ids(db_session) == ["plain-ext"]
    assert await _setting_keys(db_session) == ["extension.test.plain-ext"]
    assert [(ext_id, key) for _, ext_id, key in await _jobs(db_session)] == [("plain-ext", "tick")]
    assert runtime.scheduler.get("plain-ext", "tick") is not None
    assert not [r for r in caplog.records if r.name == SERVICE_LOGGER], [r.getMessage() for r in caplog.records]

    await extensions_service.disable_extension(app, db_session, "plain-ext")
    assert runtime.scheduler.get("plain-ext", "tick") is None


@pytest.mark.asyncio
async def test_only_shield_uses_legacy_ids_among_the_bundled_extensions(db_session, test_settings):
    """Alle mitgelieferten Erweiterungen ausser Nodvard Shield (bis 0.6 `nexus-soc`): Speicher-Kennung = Kennung, kein
    `legacy_ids` in den Zeilen. Shield nennt seine alte Kennung; auf einer neuen Installation liegt seine Zeile unter
    `shield` (die Zeile `nexus-soc` einer bestehenden Installation deckt `test_shield_legacy_ids.py` ab)."""
    settings = test_settings.model_copy(update={"extensions_dir": REPO_EXTENSIONS_DIR})
    found = await extensions_service.discover_and_sync(db_session, settings)
    await db_session.commit()

    runtime = get_extension_runtime()
    assert found and runtime.legacy_owner == {"nexus-soc": "shield"}
    assert runtime.store_ids == {ext_id: ext_id for ext_id in found}
    rows = (await db_session.execute(select(ExtensionRecord))).scalars().all()
    assert sorted(r.id for r in rows) == sorted(found)
    assert [r.id for r in rows if "legacy_ids" in (r.manifest or {})] == ["shield"]
