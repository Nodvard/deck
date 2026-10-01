"""services.extensions: Registry-Sync, Laden/Entladen, Fehler-Isolation.

Nutzt bewusst die ECHTE `extensions/hello-world`-Referenz-Extension (Repo-Pfad, nicht
`test_settings.extensions_dir`, das per Default isoliert/leer ist, siehe conftest.py)
-- sie IST der Integrationstest der Schnittstelle, ihn nur synthetisch nachzubauen
haette den eigentlichen Code nie ausgefuehrt.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import AuditEntry, ExtensionRecord, Job
from nodvard_deck.services import extensions as extensions_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


@pytest.fixture(autouse=True)
def _reset_runtime_and_sys_path():
    reset_extension_runtime()
    before = list(sys.path)
    yield
    reset_extension_runtime()
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _real_settings(test_settings):
    return test_settings.model_copy(update={"extensions_dir": REPO_EXTENSIONS_DIR})


@pytest.mark.asyncio
async def test_discover_and_sync_creates_disabled_record_for_hello_world(db_session, test_settings):
    settings = _real_settings(test_settings)
    await extensions_service.discover_and_sync(db_session, settings)

    record = await db_session.get(ExtensionRecord, "hello-world")
    assert record is not None
    assert record.state == "disabled"
    assert record.source == "bundled"


@pytest.mark.asyncio
async def test_enable_hello_world_registers_page_widget_and_mounts_router(db_session, test_settings):
    settings = _real_settings(test_settings)
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)

    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "hello-world")

    record = await db_session.get(ExtensionRecord, "hello-world")
    assert record.state == "enabled", record.last_error
    assert record.granted_permissions == [
        "schedule.register", "audit.write", "secrets.read:hello-*", "notify.send",
    ]

    runtime = get_extension_runtime()
    assert "hello-world" in runtime.loaded
    assert [p.id for _, p in runtime.ui.all_pages()] == ["hello"]
    assert [w.id for _, w in runtime.ui.all_widgets()] == ["hello"]

    # NICHT ueber `route.path` introspizieren: FastAPI 0.141 haelt inkludierte Router
    # als lazy `_IncludedRouter`-Platzhalter ohne eigenes `.path`-Attribut (dieselbe
    # Eigenheit, die schon beim ersten Boot-Test auffiel) -- der einzige verlaessliche
    # Nachweis, dass die Route wirklich dispatcht, ist eine echte Anfrage.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/ext/hello-world/widgets/hello")
    assert r.status_code == 200
    assert r.json()["data"][0]["title"] == "Hallo Welt"

    jobs = (await db_session.execute(select(Job).where(Job.ext_id == "hello-world"))).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].name == "Hello-Ping"

    audit_rows = (
        await db_session.execute(select(AuditEntry).where(AuditEntry.action == "hello.started"))
    ).scalars().all()
    assert len(audit_rows) == 1

    await extensions_service.disable_extension(app, db_session, "hello-world")


@pytest.mark.asyncio
async def test_enable_and_disable_publish_events_for_live_catalog_refresh(db_session, test_settings):
    """Das Dashboard invalidiert seinen Seiten-/Widget-Katalog live ueber den
    "events"-WS-Kanal (core/events.py) statt zu pollen -- ohne dieses Event wuesste ein
    offener Tab erst nach einem manuellen Reload von einer Aktivierung/Deaktivierung."""
    from nodvard_deck.core.events import get_event_bus
    from nodvard_sdk.types import Event

    received: list[Event] = []

    async def _handler(event: Event) -> None:
        received.append(event)

    get_event_bus().subscribe("extension.*", _handler)
    try:
        settings = _real_settings(test_settings)
        app = FastAPI()
        app.state.ext_mount_index = len(app.router.routes)

        await extensions_service.discover_and_sync(db_session, settings)
        await extensions_service.enable_extension(app, db_session, settings, "hello-world")
        await extensions_service.disable_extension(app, db_session, "hello-world")

        assert [(e.name, e.payload) for e in received] == [
            ("extension.enabled", {"ext_id": "hello-world"}),
            ("extension.disabled", {"ext_id": "hello-world"}),
        ]
    finally:
        get_event_bus().unsubscribe("extension.*", _handler)


@pytest.mark.asyncio
async def test_disable_hello_world_unmounts_routes_and_clears_ui(db_session, test_settings):
    settings = _real_settings(test_settings)
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)

    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "hello-world")
    before_disable = len(app.router.routes)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        still_up = await ac.get("/api/v1/ext/hello-world/widgets/hello")
    assert still_up.status_code == 200

    await extensions_service.disable_extension(app, db_session, "hello-world")

    record = await db_session.get(ExtensionRecord, "hello-world")
    assert record.state == "disabled"
    assert len(app.router.routes) < before_disable
    assert get_extension_runtime().ui.all_pages() == []
    assert "hello-world" not in get_extension_runtime().loaded

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        gone = await ac.get("/api/v1/ext/hello-world/widgets/hello")
    assert gone.status_code == 404, "die Route muss nach disable() tatsaechlich weg sein, nicht nur gezaehlt"


@pytest.mark.asyncio
async def test_enable_unknown_extension_raises(db_session, test_settings):
    with pytest.raises(extensions_service.ExtensionLoadError):
        await extensions_service.enable_extension(FastAPI(), db_session, test_settings, "does-not-exist")


@pytest.mark.asyncio
async def test_broken_extension_is_isolated_and_does_not_block_others(tmp_path, db_session, test_settings):
    """Docs/02 §1: eine kaputte Extension darf das Laden anderer nicht verhindern."""
    broken_dir = tmp_path / "extensions" / "broken-ext"
    (broken_dir / "src" / "broken_ext").mkdir(parents=True)
    (broken_dir / "extension.toml").write_text(
        """
        [extension]
        id = "broken-ext"
        name = "Broken"
        version = "0.1.0"
        api_version = "0.1"
        entrypoint = "broken_ext:Extension"
        """,
        encoding="utf-8",
    )
    (broken_dir / "src" / "broken_ext" / "__init__.py").write_text(
        "class Extension:\n"
        "    async def setup(self, ctx):\n"
        "        raise RuntimeError('absichtlich kaputt fuer den Isolationstest')\n"
        "    async def on_start(self, ctx):\n"
        "        pass\n"
        "    async def on_stop(self, ctx):\n"
        "        pass\n",
        encoding="utf-8",
    )

    settings = test_settings.model_copy(update={"extensions_dir": tmp_path / "extensions"})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)

    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "broken-ext")

    record = await db_session.get(ExtensionRecord, "broken-ext")
    assert record.state == "error"
    assert "absichtlich kaputt" in record.last_error
    assert "broken-ext" not in get_extension_runtime().loaded


@pytest.mark.asyncio
async def test_incompatible_api_version_is_rejected(tmp_path, db_session, test_settings):
    ext_dir = tmp_path / "extensions" / "future-ext"
    ext_dir.mkdir(parents=True)
    (ext_dir / "extension.toml").write_text(
        """
        [extension]
        id = "future-ext"
        name = "Future"
        version = "0.1.0"
        api_version = "9.9"
        entrypoint = "future_ext:Extension"
        """,
        encoding="utf-8",
    )

    settings = test_settings.model_copy(update={"extensions_dir": tmp_path / "extensions"})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)

    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "future-ext")

    record = await db_session.get(ExtensionRecord, "future-ext")
    assert record.state == "incompatible"


@pytest.mark.asyncio
async def test_enable_is_idempotent(db_session, test_settings):
    settings = _real_settings(test_settings)
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)

    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "hello-world")
    routes_after_first = len(app.router.routes)
    await extensions_service.enable_extension(app, db_session, settings, "hello-world")

    assert len(app.router.routes) == routes_after_first, "zweites enable() darf nicht doppelt mounten"

    await extensions_service.disable_extension(app, db_session, "hello-world")


def _write_flaky_extension(tmp_path: Path) -> Path:
    """Extension, deren on_start() fehlschlaegt, solange `fail.flag` existiert. Jeder
    Aufruf von on_start()/on_stop() landet in `calls.log` -- so sieht der Test, ob beim
    erneuten Einschalten wirklich sauber gestoppt und neu gestartet wurde."""
    ext_dir = tmp_path / "extensions" / "flaky-ext"
    (ext_dir / "src" / "nodvard_deck_ext_flaky").mkdir(parents=True)
    (ext_dir / "extension.toml").write_text(
        """
        [extension]
        id = "flaky-ext"
        name = "Flaky"
        version = "0.1.0"
        api_version = "0.1"
        entrypoint = "nodvard_deck_ext_flaky:Extension"
        """,
        encoding="utf-8",
    )
    flag = tmp_path / "fail.flag"
    log = tmp_path / "calls.log"
    (ext_dir / "src" / "nodvard_deck_ext_flaky" / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"FLAG = Path({str(flag)!r})\n"
        f"LOG = Path({str(log)!r})\n"
        "def _note(what):\n"
        "    with LOG.open('a') as fh:\n"
        "        fh.write(what + '\\n')\n"
        "class Extension:\n"
        "    async def setup(self, ctx):\n"
        "        _note('setup')\n"
        "        ctx.events.subscribe('flaky.ping', self._on_ping)\n"
        "    async def _on_ping(self, event):\n"
        "        _note('ping')\n"
        "    async def on_start(self, ctx):\n"
        "        _note('start')\n"
        "        if FLAG.exists():\n"
        "            raise RuntimeError('Dienst gerade nicht erreichbar')\n"
        "    async def on_stop(self, ctx):\n"
        "        _note('stop')\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.asyncio
async def test_enable_after_on_start_crash_unloads_and_starts_again(tmp_path, db_session, test_settings):
    """Stuerzt on_start() ab, bleibt die Extension mit state=error geladen (damit
    disable() sie aufraeumen kann). Ein erneutes "Einschalten" darf dann nicht
    stillschweigend nichts tun: sie wird sauber entladen und neu gestartet."""
    _write_flaky_extension(tmp_path)
    flag = tmp_path / "fail.flag"
    log = tmp_path / "calls.log"
    flag.write_text("", encoding="utf-8")

    settings = test_settings.model_copy(update={"extensions_dir": tmp_path / "extensions"})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)

    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "flaky-ext")

    record = await db_session.get(ExtensionRecord, "flaky-ext")
    assert record.state == "error"
    assert "Dienst gerade nicht erreichbar" in record.last_error
    assert "flaky-ext" in get_extension_runtime().loaded

    # Ursache behoben -> erneutes Einschalten startet wirklich neu.
    flag.unlink()
    await extensions_service.enable_extension(app, db_session, settings, "flaky-ext")

    record = await db_session.get(ExtensionRecord, "flaky-ext")
    assert record.state == "enabled"
    assert record.last_error is None
    assert record.last_error_at is None
    assert "flaky-ext" in get_extension_runtime().loaded
    assert log.read_text(encoding="utf-8").split() == ["setup", "start", "stop", "setup", "start"]

    await extensions_service.disable_extension(app, db_session, "flaky-ext")


@pytest.mark.asyncio
async def test_reenable_after_crash_and_disable_drop_old_event_handlers(tmp_path, db_session, test_settings):
    """`ctx.events.subscribe()` haengt am globalen Bus: beim Entladen muessen die
    Handler der alten Instanz weg, sonst reagiert jedes Ereignis nach dem erneuten
    Einschalten doppelt (und nach dem Ausschalten weiter)."""
    from nodvard_deck.core.events import get_event_bus
    from nodvard_sdk.types import Event

    _write_flaky_extension(tmp_path)
    flag = tmp_path / "fail.flag"
    log = tmp_path / "calls.log"
    flag.write_text("", encoding="utf-8")

    settings = test_settings.model_copy(update={"extensions_dir": tmp_path / "extensions"})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)

    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "flaky-ext")  # stuerzt ab
    flag.unlink()
    await extensions_service.enable_extension(app, db_session, settings, "flaky-ext")  # startet neu

    await get_event_bus().publish(Event(name="flaky.ping", payload={}))
    assert log.read_text(encoding="utf-8").split().count("ping") == 1

    await extensions_service.disable_extension(app, db_session, "flaky-ext")
    await get_event_bus().publish(Event(name="flaky.ping", payload={}))
    assert log.read_text(encoding="utf-8").split().count("ping") == 1


@pytest.mark.asyncio
async def test_enable_after_on_start_crash_keeps_error_when_it_still_fails(tmp_path, db_session, test_settings):
    _write_flaky_extension(tmp_path)
    (tmp_path / "fail.flag").write_text("", encoding="utf-8")

    settings = test_settings.model_copy(update={"extensions_dir": tmp_path / "extensions"})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)

    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "flaky-ext")
    await extensions_service.enable_extension(app, db_session, settings, "flaky-ext")

    record = await db_session.get(ExtensionRecord, "flaky-ext")
    assert record.state == "error"
    assert "Dienst gerade nicht erreichbar" in record.last_error
    # Weiterhin verfolgt, damit "Ausschalten" sie sauber entfernen kann -- und nicht doppelt.
    assert "flaky-ext" in get_extension_runtime().loaded

    await extensions_service.disable_extension(app, db_session, "flaky-ext")
    record = await db_session.get(ExtensionRecord, "flaky-ext")
    assert record.state == "disabled"
    assert "flaky-ext" not in get_extension_runtime().loaded


def _write_setup_flaky_extension(tmp_path: Path) -> Path:
    """Extension, deren setup() erst etwas anmeldet (Ereignis-Abo, Hintergrundaufgabe) und
    dann abstuerzt, solange `fail.flag` existiert."""
    ext_dir = tmp_path / "extensions" / "sflaky-ext"
    (ext_dir / "src" / "nodvard_deck_ext_sflaky").mkdir(parents=True)
    (ext_dir / "extension.toml").write_text(
        """
        [extension]
        id = "sflaky-ext"
        name = "SetupFlaky"
        version = "0.1.0"
        api_version = "0.1"
        entrypoint = "nodvard_deck_ext_sflaky:Extension"
        """,
        encoding="utf-8",
    )
    flag = tmp_path / "fail.flag"
    log = tmp_path / "calls.log"
    (ext_dir / "src" / "nodvard_deck_ext_sflaky" / "__init__.py").write_text(
        "import asyncio\n"
        "from pathlib import Path\n"
        f"FLAG = Path({str(flag)!r})\n"
        f"LOG = Path({str(log)!r})\n"
        "def _note(what):\n"
        "    with LOG.open('a') as fh:\n"
        "        fh.write(what + '\\n')\n"
        "class Extension:\n"
        "    async def setup(self, ctx):\n"
        "        ctx.events.subscribe('sflaky.ping', self._on_ping)\n"
        "        ctx.spawn(self._tick, name='tick')\n"
        "        if FLAG.exists():\n"
        "            raise RuntimeError('Datenbank gerade gesperrt')\n"
        "    async def _tick(self):\n"
        "        while True:\n"
        "            _note('tick')\n"
        "            await asyncio.sleep(0.02)\n"
        "    async def _on_ping(self, event):\n"
        "        _note('ping')\n"
        "    async def on_start(self, ctx):\n"
        "        pass\n"
        "    async def on_stop(self, ctx):\n"
        "        pass\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.asyncio
async def test_a_crashing_setup_leaves_no_half_registered_handlers_or_tasks(tmp_path, db_session, test_settings):
    """Stuerzt setup() nach dem ersten Anmelden ab, darf nichts davon liegen bleiben: sonst
    laeuft nach dem erneuten Einschalten jedes Ereignis doppelt, nach dem Ausschalten noch
    einmal, und die Hintergrundaufgabe laeuft unkuendbar weiter."""
    import asyncio

    from nodvard_deck.core.events import get_event_bus
    from nodvard_sdk.types import Event

    _write_setup_flaky_extension(tmp_path)
    flag = tmp_path / "fail.flag"
    log = tmp_path / "calls.log"
    flag.write_text("", encoding="utf-8")
    settings = test_settings.model_copy(update={"extensions_dir": tmp_path / "extensions"})
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)

    def count(what: str) -> int:
        return log.read_text(encoding="utf-8").split().count(what) if log.exists() else 0

    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "sflaky-ext")  # setup() stuerzt ab
    record = await db_session.get(ExtensionRecord, "sflaky-ext")
    assert record.state == "error" and "setup() fehlgeschlagen" in record.last_error
    assert "sflaky-ext" not in get_extension_runtime().loaded

    # Schon jetzt: kein verwaistes Abo, keine weiterlaufende Aufgabe.
    await get_event_bus().publish(Event(name="sflaky.ping", payload={}))
    await asyncio.sleep(0.1)
    ticks = count("tick")
    await asyncio.sleep(0.1)
    assert count("ping") == 0 and count("tick") == ticks

    flag.unlink()
    await extensions_service.enable_extension(app, db_session, settings, "sflaky-ext")
    assert (await db_session.get(ExtensionRecord, "sflaky-ext")).state == "enabled"
    await get_event_bus().publish(Event(name="sflaky.ping", payload={}))
    assert count("ping") == 1  # genau ein Handler

    await extensions_service.disable_extension(app, db_session, "sflaky-ext")
    await get_event_bus().publish(Event(name="sflaky.ping", payload={}))
    assert count("ping") == 1


@pytest.mark.asyncio
async def test_enable_extension_does_not_deadlock_with_caller_session_held_open(tmp_path, test_settings):
    """Live gefunden (proxmox-Extension) -- dieselbe Root-Cause wie der Fix in
    `core.gate.execute_action()`, siehe dortigen Docstring und docs/00-DECISIONS.md
    D-14: `enable_extension()` haelt `session` mit einem anstehenden Schreibvorgang
    (`record.granted_permissions`) offen, waehrend `instance.setup()`/`on_start()` --
    Extension-Code -- selbst unabhaengige Sessions oeffnen koennen. hello-worlds
    `on_start()` tut das bereits (`ctx.audit.log()`) -- kein synthetischer Doppelgaenger
    noetig. Gegen eine echte Datei-DB (die In-Memory-StaticPool-Fixture `db_session`
    teilt eine physische Verbindung und haette das nie gezeigt, siehe test_core_vault.py
    `test_vault_use_audit_survives_caller_session_rollback`) reproduzierte das
    zuverlaessig "database is locked" -- rueckwirkend die wahrscheinlichste Erklaerung
    fuer die wiederholt beobachteten, nie sicher diagnostizierten Vorfaelle
    beim Neustart."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck.db.session import create_engine_for, reset_engine_cache, set_engine_for_testing
    from nodvard_deck.models import Base

    db_path = tmp_path / "enable-isolation.db"
    settings = test_settings.model_copy(
        update={"extensions_dir": REPO_EXTENSIONS_DIR, "database_url": f"sqlite+aiosqlite:///{db_path}"}
    )
    engine = create_engine_for(settings)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        set_engine_for_testing(engine)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        app = FastAPI()
        app.state.ext_mount_index = len(app.router.routes)

        async with sessionmaker() as session:
            await extensions_service.discover_and_sync(session, settings)
            await extensions_service.enable_extension(app, session, settings, "hello-world")
            await session.commit()

        async with sessionmaker() as verify_session:
            record = await verify_session.get(ExtensionRecord, "hello-world")
            assert record.state == "enabled", record.last_error
            entries = (
                await verify_session.execute(select(AuditEntry).where(AuditEntry.action == "hello.started"))
            ).scalars().all()
            assert len(entries) == 1
    finally:
        await engine.dispose()
        reset_engine_cache()
