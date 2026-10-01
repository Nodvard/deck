"""`main.lifespan` und `nodvard_deck.boot`: die Anwendung haelt die Sperre und meldet `started_ok`."""

from __future__ import annotations

import pytest
import pytest_asyncio
from nodvard_deck import config
from nodvard_deck.core import bootstate


@pytest_asyncio.fixture
async def started_app(tmp_path, monkeypatch):
    """Liefert eine Funktion, die den echten Lifespan startet (leere Datei-Datenbank, eigener Datenordner)."""
    from nodvard_deck.core.events import reset_event_bus
    from nodvard_deck.core.metrics_history import reset_metrics_collector
    from nodvard_deck.core.scheduler import reset_scheduler_service
    from nodvard_deck.db.session import create_engine_for, reset_engine_cache, set_engine_for_testing
    from nodvard_deck.main import app, lifespan
    from nodvard_deck.models import Base

    settings = config.Settings(
        env="dev", data_dir=tmp_path, database_url=f"sqlite+aiosqlite:///{tmp_path / 'boot.db'}",
        master_key_path=tmp_path / "master.key", vault_keyring_path=tmp_path / "vault_keyring.json",
        jwt_secret_path=tmp_path / "jwt_secret.key", extensions_dir=tmp_path / "extensions", ext_data_dir=tmp_path / "ext-data",
        metrics_interval_s=0,
    )
    monkeypatch.setattr(config, "_settings", settings)
    engine = create_engine_for(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    set_engine_for_testing(engine)
    original_routes = list(app.router.routes)
    reset_metrics_collector()
    try:
        yield lambda: lifespan(app), tmp_path
    finally:
        app.router.routes[:] = original_routes
        await engine.dispose()
        reset_engine_cache()
        reset_scheduler_service()
        reset_metrics_collector()
        reset_event_bus()


@pytest.mark.asyncio
async def test_a_good_start_sets_started_ok_and_holds_the_lock_while_running(started_app):
    make, data = started_app
    bootstate.write_state(data, {"app_version": "0.6.0", "started_ok": False})
    async with make():
        assert bootstate.read_state(data)["started_ok"] is True
        assert bootstate.read_state(data)["started_at"]
        assert bootstate.is_locked(data) is True, "boot und admin sehen: die Anwendung laeuft"
    assert bootstate.is_locked(data) is False, "nach dem Ende ist die Sperre frei"


@pytest.mark.asyncio
async def test_without_a_boot_state_nothing_is_written_but_the_lock_still_works(started_app):
    make, data = started_app
    async with make():
        assert bootstate.is_locked(data) is True
    assert bootstate.read_state(data) == {}, "kein Zustand, wenn `boot` nie lief (Entwicklung, Tests)"


@pytest.mark.asyncio
async def test_started_ok_is_not_set_before_the_application_is_fully_up(started_app, monkeypatch):
    from nodvard_deck import main

    make, data = started_app
    bootstate.write_state(data, {"app_version": "0.6.0", "started_ok": False})
    seen = []
    real = main.register_core_jobs

    async def spy(runs_dir):
        seen.append(bootstate.read_state(data)["started_ok"])  # mitten im Start
        await real(runs_dir)

    monkeypatch.setattr(main, "register_core_jobs", spy)
    async with make():
        pass
    assert seen == [False], "erst ganz am Ende des Starts, nicht frueher"


@pytest.mark.asyncio
async def test_a_failing_state_write_never_stops_the_application(started_app, monkeypatch):
    make, data = started_app
    bootstate.write_state(data, {"app_version": "0.6.0", "started_ok": False})
    monkeypatch.setattr(bootstate, "mark_started_ok", lambda *a, **k: (_ for _ in ()).throw(OSError(30, "Nur lesbar")))
    async with make():
        pass


@pytest.mark.asyncio
async def test_a_failed_state_write_is_tried_again_until_it_works(started_app, monkeypatch):
    # Fund: Ein einziger gescheiterter Versuch liess `started_ok=false` stehen. Eine spaeter zurueckgestellte aeltere Version
    # haette die neue dann fuer "nie gestartet" gehalten und die Daten seit dem Update automatisch verworfen.
    import asyncio

    from nodvard_deck import main

    make, data = started_app
    bootstate.write_state(data, {"app_version": "0.6.0", "started_ok": False})
    monkeypatch.setattr(main, "STARTED_OK_RETRY_S", 0.05)
    real = bootstate.mark_started_ok
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError(28, "Kein Platz")
        return real(*args, **kwargs)

    monkeypatch.setattr(bootstate, "mark_started_ok", flaky)
    async with make():
        for _ in range(100):
            if bootstate.read_state(data).get("started_ok"):
                break
            await asyncio.sleep(0.02)
        assert bootstate.read_state(data)["started_ok"] is True
    assert calls["n"] == 3, "danach nicht weiter versucht"


@pytest.mark.asyncio
async def test_a_lock_held_by_someone_else_is_reported_but_does_not_stop_the_application(started_app, monkeypatch, caplog):
    from nodvard_deck import main

    make, data = started_app
    monkeypatch.setattr(main, "APP_LOCK_WAIT_S", 0.2)
    other = bootstate.acquire_lock(data, purpose="boot")
    try:
        with caplog.at_level("ERROR"):
            async with make():
                pass
        assert any("Sperre" in r.getMessage() or "lock" in r.getMessage().lower() for r in caplog.records)
    finally:
        other.release()
