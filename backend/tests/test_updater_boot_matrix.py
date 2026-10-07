"""Update-Helfer und Datenpfad: Was tut die alte Version beim Start, nachdem der Helfer zu ihr zurueckgegangen ist?

Jede Zeile der Tabelle laeuft durch das echte `boot.decide()`; der Rueckweg auf Wunsch zusaetzlich so, wie ihn das
Dashboard anlegt (`services.update_helper.data_plan` + Vormerkung). Zwei Zeilen laufen ausserdem Ende zu Ende mit
echten Prozessen, echtem Alembic und echten Dateien (Aufbau aus `test_boot_e2e.py`)."""

from __future__ import annotations

import sqlite3
import sys

import pytest
from nodvard_deck import boot
from nodvard_deck.config import Settings
from nodvard_deck.core import bootstate, updates
from nodvard_deck.core.backup import premigrate
from nodvard_deck.services import update_helper

OLD, NEW = "0.7.0", "0.7.1"
OLD_HEADS, NEW_HEADS = ["a1"], ["b2"]
KNOWN_TO_OLD = {"a1"}
COPY = "20261001T030000Z_0.7.0_0.7.1.db"


def migration(state: str = "ok", *, copy: str | None = COPY) -> dict:
    return {"at": "2026-10-01T03:00:00Z", "from_version": OLD, "to_version": NEW, "from_heads": OLD_HEADS,
            "to_heads": NEW_HEADS, "copy": copy, "state": state, "had_data": True}


def old_version_decides(live_heads: list[str], state: dict, *, rollback: dict | None = None,
                        copy_heads: list[str] | None = OLD_HEADS) -> boot.Plan:
    """So entscheidet die ALTE Version beim Start (kennt nur `a1`)."""
    live = premigrate.LiveDb(True, sorted(live_heads), True)
    return boot.decide(live, KNOWN_TO_OLD, set(OLD_HEADS), state, copy_heads=lambda _name: copy_heads,
                       rollback=rollback)


# Ausgang beim neuen Container -> Stand danach -> was die alte Version tut
MATRIX = [
    pytest.param(OLD_HEADS, {"started_ok": True, "last_migration": migration("reverted")}, None, ("noop", ""),
                 id="Migration gescheitert: boot hat selbst zurueckgesetzt"),
    pytest.param(OLD_HEADS, {"started_ok": True}, None, ("noop", ""),
                 id="zu wenig Platz oder keine Kopie: keine Migration gelaufen"),
    pytest.param(NEW_HEADS, {"started_ok": False, "last_migration": migration()}, None, ("revert", "downgrade"),
                 id="Abbruch in der Lifespan, Notseite oder Frist beim Start"),
    pytest.param(OLD_HEADS, {"started_ok": False, "last_migration": migration("running")}, None,
                 ("revert", "interrupted"), id="Stopp mitten in der Migration"),
    pytest.param(NEW_HEADS, {"started_ok": True, "last_migration": migration()}, None, ("refuse", "newer_data"),
                 id="Absturz nach started_ok, vor dem ersten gesund: Notseite, Nutzer entscheidet"),
    pytest.param(NEW_HEADS, {"started_ok": True, "last_migration": migration()},
                 {"copy": COPY, "by": update_helper.ROLLBACK_BY, "requested_at": 0.0}, ("revert", "rollback_requested"),
                 id="ausdruecklicher Rueckweg"),
    pytest.param(OLD_HEADS, {"started_ok": True, "app_version": NEW}, None, ("noop", ""),
                 id="Update ohne Migration"),
]


@pytest.mark.parametrize(("live_heads", "state", "rollback", "expected"), MATRIX)
def test_what_the_old_version_does(live_heads, state, rollback, expected):
    plan = old_version_decides(live_heads, state, rollback=rollback)
    assert (plan.action, plan.reason or plan.kind) == expected
    if expected == ("refuse", "newer_data"):
        assert plan.rollback["copy"] == COPY, "die Notseite bietet den Stand vor dem Update an"


def test_a_marker_for_another_copy_does_not_revert():
    plan = old_version_decides(NEW_HEADS, {"started_ok": True, "last_migration": migration()},
                               rollback={"copy": "20260901T030000Z_0.6.0_0.7.0.db", "by": "x", "requested_at": 0.0})
    assert (plan.action, plan.kind) == ("refuse", "newer_data")


def _settings(tmp_path, version: str) -> Settings:
    return Settings(data_dir=tmp_path, image=updates.OFFICIAL_IMAGE, build=version, image_info_path=tmp_path / "keine.json",
                    updater_dir=tmp_path / "updater")


def test_the_dashboard_plans_exactly_what_the_old_version_needs(tmp_path):
    """Rueckweg auf Wunsch: Das Dashboard der NEUEN Version legt die Vormerkung so an, dass die alte Version genau die
    Kopie einspielt; ohne Migration legt es keine an, und die alte Version startet normal."""
    settings = _settings(tmp_path, NEW)
    copies = premigrate.copies_dir(tmp_path)
    copies.mkdir(parents=True)
    (copies / COPY).write_bytes(b"kopie")
    bootstate.write_state(tmp_path, {"started_ok": True, "app_version": NEW, "last_migration": migration()})
    data_revert, copy = update_helper.data_plan(settings, OLD)
    assert (data_revert, copy) == (True, COPY)
    bootstate.request_rollback(tmp_path, copy, by=update_helper.ROLLBACK_BY)
    plan = old_version_decides(NEW_HEADS, bootstate.read_state(tmp_path), rollback=bootstate.read_rollback(tmp_path))
    assert (plan.action, plan.reason, plan.copy_name) == ("revert", "rollback_requested", COPY)

    # Ohne Vormerkung (z. B. vom Helfer abgelehnt und wieder geloescht) bliebe nur die Notseite.
    bootstate.clear_rollback(tmp_path)
    plan = old_version_decides(NEW_HEADS, bootstate.read_state(tmp_path), rollback=bootstate.read_rollback(tmp_path))
    assert (plan.action, plan.kind) == ("refuse", "newer_data")

    # Update ohne Migration: der letzte Umbau gehoert zu einer frueheren Version.
    older = {**migration(), "from_version": "0.6.9", "to_version": OLD}
    bootstate.write_state(tmp_path, {"started_ok": True, "app_version": NEW, "last_migration": older})
    assert update_helper.data_plan(settings, OLD) == (False, None)
    plan = old_version_decides(OLD_HEADS, bootstate.read_state(tmp_path), rollback=None)
    assert plan.action == "noop"


# ---------------------------------------------------------------------------
# Ende zu Ende
# ---------------------------------------------------------------------------

e2e = pytest.mark.skipif(sys.platform == "win32", reason="Prozesse wie im Container (POSIX)")


def _versioned(env: dict, version: str) -> dict:
    return {**env, "NODVARD_DECK_IMAGE": updates.OFFICIAL_IMAGE, "NODVARD_DECK_BUILD": version}


@e2e
def test_end_to_end_rollback_with_data_via_the_dashboard_plan(tmp_path):
    from restore_helpers import REPO_ROOT
    from test_boot_e2e import (
        GOOD,
        dump,
        instance_env,
        make_repo,
        real_database,
        run_boot,
    )

    env = instance_env(tmp_path)
    data = tmp_path / "data"
    db = real_database(_versioned(env, OLD))
    assert run_boot(_versioned(env, OLD), REPO_ROOT).returncode == 0
    old_dump = dump(db)

    updated = run_boot(_versioned(env, NEW), make_repo(tmp_path, {"zz_good.py": GOOD}))
    assert updated.returncode == 0, updated.stdout + updated.stderr
    assert bootstate.mark_started_ok(data), "die neue Version ist gestartet ..."
    conn = sqlite3.connect(db)  # ... und es wurde darin gearbeitet
    conn.execute("INSERT INTO neu_angelegt VALUES (42)")
    conn.commit()
    conn.close()

    settings = _settings(data, NEW)
    data_revert, copy = update_helper.data_plan(settings, OLD)
    assert data_revert is True and copy == bootstate.read_state(data)["last_migration"]["copy"]
    bootstate.request_rollback(data, copy, by=update_helper.ROLLBACK_BY)

    back = run_boot(_versioned(env, OLD), REPO_ROOT)  # der Helfer hat den Container der alten Version gestartet
    assert back.returncode == 0, back.stdout + back.stderr
    assert dump(db) == old_dump, "der Stand vor dem Update ist zurueck"
    assert bootstate.read_rollback(data) is None
    assert len(list((data / "restore").glob("replaced-*"))) == 1, "die neueren Daten liegen nur beiseite"


@e2e
def test_end_to_end_rollback_after_an_update_without_migration(tmp_path):
    from restore_helpers import REPO_ROOT
    from test_boot_e2e import dump, instance_env, real_database, run_boot

    env = instance_env(tmp_path)
    data = tmp_path / "data"
    db = real_database(_versioned(env, OLD))
    assert run_boot(_versioned(env, OLD), REPO_ROOT).returncode == 0
    assert run_boot(_versioned(env, NEW), REPO_ROOT).returncode == 0, "gleiche Migrationen: nichts umgebaut"
    bootstate.mark_started_ok(data)
    before = dump(db)
    assert update_helper.data_plan(_settings(data, NEW), OLD) == (False, None)
    back = run_boot(_versioned(env, OLD), REPO_ROOT)
    assert back.returncode == 0, back.stdout + back.stderr
    assert dump(db) == before, "nichts geht verloren"
    assert not (data / "restore").exists() or not list((data / "restore").glob("replaced-*"))
