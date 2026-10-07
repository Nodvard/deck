"""`nodvard_deck.boot` -- Kopie vor jeder Migration, Downgrade-Erkennung, Fehlerprotokoll, Sperre.

Die Migration selbst ist hier durch Attrappen ersetzt (`fake_ok` bringt die Datenbank auf die Koepfe dieses
Images, `fake_fail` hinterlaesst einen halben Stand und wirft); die echte Alembic-Migration mit einer absichtlich
kaputten Revision laeuft in `test_boot_e2e.py`."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest
from nodvard_deck import boot
from nodvard_deck.core import bootstate
from nodvard_deck.core.backup import premigrate, restore, snapshot, store
from nodvard_deck.migrate import known_revisions
from restore_helpers import REPO_ROOT, current_heads, fill_live, make_db, make_layout, settings_for, snapshot_tree

HEADS = current_heads()
FUTURE = ["f00000000000"]
OLD_KNOWN = sorted(known_revisions(REPO_ROOT)[0] - set(HEADS))[:1]


def dump(path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def set_heads(db: Path, heads: list[str]) -> None:
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM alembic_version")
    conn.executemany("INSERT INTO alembic_version VALUES (?)", [(h,) for h in heads])
    conn.commit()
    conn.close()


class Ctx:
    def __init__(self, tmp_path, monkeypatch):
        self.layout = make_layout(tmp_path)
        self.settings = settings_for(self.layout)
        self.data = self.layout.data_dir
        self.calls: list[str] = []
        self.on_migrate = self.fake_ok
        monkeypatch.setattr(boot, "get_settings", lambda: self.settings)
        monkeypatch.setattr(boot, "__version__", "0.6.0")
        monkeypatch.setattr(boot, "LOCK_WAIT_S", 0)
        monkeypatch.chdir(REPO_ROOT)
        monkeypatch.setattr(boot.migrate, "main", self._migrate)
        self.monkeypatch = monkeypatch

    def _migrate(self):
        self.calls.append("migrate")
        self.on_migrate()

    # --- Attrappen der Migration ---
    def fake_ok(self):
        if self.layout.db_path.exists():
            conn = sqlite3.connect(self.layout.db_path)
            conn.execute("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
            conn.commit()
            conn.close()
            set_heads(self.layout.db_path, HEADS)

    def fake_fail(self):
        conn = sqlite3.connect(self.layout.db_path)
        conn.execute("CREATE TABLE halb_migriert (x)")
        conn.execute("DELETE FROM alembic_version")
        conn.execute("INSERT INTO alembic_version VALUES ('halbfertig0000')")
        conn.commit()
        conn.close()
        raise RuntimeError("Migration kaputt: Tabelle ist /home/user/geheim/lattice.db und postgresql://admin:Sup3rGeheim@10.0.0.5/deck")

    def old_db(self, **kw):
        """Eine Datenbank auf einem aelteren (bekannten) Stand."""
        assert OLD_KNOWN, "dieses Repo hat keine aeltere Revision"
        make_db(self.layout.db_path, versions=OLD_KNOWN, **kw)
        return dump(self.layout.db_path)

    def state(self):
        return bootstate.read_state(self.data)

    def copies(self):
        return premigrate.list_copies(self.data)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    return Ctx(tmp_path, monkeypatch)


# ---------------------------------------------------------------------------
# Kopie vor der Migration
# ---------------------------------------------------------------------------


def test_database_on_the_current_heads_needs_neither_copy_nor_new_state_flags(ctx):
    make_db(ctx.layout.db_path)
    assert boot.main() == 0 and ctx.calls == ["migrate"]
    assert ctx.copies() == []
    state = ctx.state()
    assert state["app_version"] == "0.6.0" and state["db_heads"] == HEADS and state["started_ok"] is True
    assert "failure" not in state and "last_migration" not in state


def test_official_image_records_its_exact_version_in_copy_and_state(ctx):
    """Eine Vorabversion (0.6.0-rc2) traegt dieselbe Zahl wie die fertige Version im Code: Kopie und Startzustand
    nennen trotzdem die genaue Version des Images, wie "Nach Updates suchen"."""
    from nodvard_deck.core import updates

    ctx.old_db()
    ctx.settings.image = updates.OFFICIAL_IMAGE
    ctx.settings.build = "0.6.0-rc2"
    bootstate.write_state(ctx.data, {"app_version": "0.6.0-rc1", "started_ok": True, "db_heads": OLD_KNOWN})
    assert boot.main() == 0
    (copy,) = ctx.copies()
    assert copy["from_version"] == "0.6.0-rc1" and copy["to_version"] == "0.6.0-rc2"
    state = ctx.state()
    assert state["app_version"] == "0.6.0-rc2"
    assert state["last_migration"]["to_version"] == "0.6.0-rc2"


def test_self_built_image_records_the_code_version(ctx):
    ctx.old_db()
    ctx.settings.build = "abc123"  # eine Kennung, keine Version, und kein offizielles Image
    bootstate.write_state(ctx.data, {"app_version": "0.5.0", "started_ok": True, "db_heads": OLD_KNOWN})
    assert boot.main() == 0
    (copy,) = ctx.copies()
    assert copy["to_version"] == "0.6.0" and ctx.state()["app_version"] == "0.6.0"


def test_the_copy_exists_BEFORE_the_migration_runs(ctx):
    old = ctx.old_db()
    seen = {}

    def during():
        seen["copies"] = ctx.copies()
        seen["state"] = ctx.state()
        seen["copy_dump"] = dump(ctx.layout.data_dir / "backups" / "vor-update" / seen["copies"][0]["name"])
        ctx.fake_ok()

    ctx.on_migrate = during
    ctx.monkeypatch.setattr(boot, "__version__", "0.6.0")
    bootstate.write_state(ctx.data, {"app_version": "0.5.0", "started_ok": True, "db_heads": OLD_KNOWN})
    assert boot.main() == 0
    assert len(seen["copies"]) == 1 and seen["copy_dump"] == old, "die Kopie ist der Stand VOR der Migration"
    assert seen["copies"][0]["from_version"] == "0.5.0" and seen["copies"][0]["to_version"] == "0.6.0"
    during_state = seen["state"]
    assert during_state["started_ok"] is False, "ab hier hat die neue Version noch nie erfolgreich gestartet"
    migration = during_state["last_migration"]
    assert migration["state"] == "running" and migration["from_heads"] == OLD_KNOWN and migration["to_heads"] == HEADS
    assert migration["copy"] == seen["copies"][0]["name"]
    after = ctx.state()
    assert after["last_migration"]["state"] == "ok" and after["db_heads"] == HEADS and after["started_ok"] is False
    assert len(ctx.copies()) == 1, "nach einer gelungenen Migration bleibt die Kopie"


def test_without_a_copy_there_is_no_migration_when_there_is_no_space(ctx, monkeypatch):
    old = ctx.old_db()
    needed = premigrate.required_bytes(ctx.layout.db_path)
    monkeypatch.setattr(store, "free_bytes", lambda p: needed - 1)
    assert boot.main() == 1
    assert ctx.calls == [], "keine Migration"
    assert dump(ctx.layout.db_path) == old
    failure = ctx.state()["failure"]
    assert failure["kind"] == "no_space" and failure["needed_bytes"] == needed and failure["free_bytes"] == needed - 1
    assert ctx.copies() == []


def test_without_a_copy_there_is_no_migration_when_the_copy_fails(ctx, monkeypatch):
    old = ctx.old_db()

    def bad(src, dst):
        raise snapshot.IntegrityCheckFailed("kaputt")

    monkeypatch.setattr(snapshot, "online_copy", bad)
    assert boot.main() == 1 and ctx.calls == []
    assert dump(ctx.layout.db_path) == old
    assert ctx.state()["failure"]["kind"] == "no_copy"
    assert not (ctx.data / "backups" / "vor-update").exists() or not list((ctx.data / "backups" / "vor-update").iterdir())


def test_a_failing_migration_returns_to_the_state_before_it(ctx):
    old = ctx.old_db()
    ctx.on_migrate = ctx.fake_fail
    assert boot.main() == 1
    assert dump(ctx.layout.db_path) == old, "die Datenbank ist wieder genau der Stand VOR der Migration"
    assert not os.path.lexists(str(ctx.layout.db_path) + "-wal")
    state = ctx.state()
    assert state["failure"]["kind"] == "migration_failed"
    assert state["last_migration"]["state"] == "reverted" and state["started_ok"] is True
    assert "revert" not in state
    text = " ".join(state["failure"]["log"] + [state["failure"]["reason"]])
    assert "Migration kaputt" in text
    for secret in ("Sup3rGeheim", "/home/user", str(ctx.data)):
        assert secret not in text, secret
    assert len(state["failure"]["log"]) <= 50


def test_the_failure_stays_after_the_traceback_and_names_the_exception(ctx):
    ctx.old_db()
    ctx.on_migrate = ctx.fake_fail
    boot.main()
    log = ctx.state()["failure"]["log"]
    assert any("RuntimeError" in line for line in log)


def test_when_the_way_back_fails_too_the_failure_says_so_and_keeps_the_journal(ctx, monkeypatch):
    ctx.old_db()
    ctx.on_migrate = ctx.fake_fail
    monkeypatch.setattr(premigrate, "_install", lambda layout, src: (_ for _ in ()).throw(OSError(28, "Kein Platz")))
    assert boot.main() == 1
    state = ctx.state()
    assert state["failure"]["kind"] == "revert_failed"
    assert state["revert"]["copy"], "das Journal bleibt, der naechste Start macht weiter"


def test_next_start_finishes_an_interrupted_revert_first(ctx, monkeypatch):
    old = ctx.old_db()
    ctx.on_migrate = ctx.fake_fail
    real = premigrate._install
    monkeypatch.setattr(premigrate, "_install", lambda layout, src: (_ for _ in ()).throw(OSError(28, "Kein Platz")))
    assert boot.main() == 1
    monkeypatch.setattr(premigrate, "_install", real)
    ctx.on_migrate = ctx.fake_ok
    ctx.calls.clear()
    assert boot.main() == 0
    state = ctx.state()
    assert "revert" not in state and "failure" not in state
    assert ctx.calls == ["migrate"]
    assert premigrate.read_live(ctx.layout.db_path).heads == HEADS
    _ = old


def test_the_emergency_exit_skips_the_copy_but_not_the_migration(ctx):
    ctx.old_db()
    ctx.settings.skip_pre_migrate_backup = True
    assert boot.main() == 0 and ctx.calls == ["migrate"]
    assert ctx.copies() == []


def test_the_emergency_exit_has_no_way_back_when_the_migration_fails(ctx):
    ctx.old_db()
    ctx.settings.skip_pre_migrate_backup = True
    ctx.on_migrate = ctx.fake_fail
    assert boot.main() == 1
    assert premigrate.read_live(ctx.layout.db_path).heads == ["halbfertig0000"], "nichts wurde zurueckgesetzt (keine Kopie)"
    assert ctx.state()["failure"]["kind"] == "migration_failed"


@pytest.mark.parametrize("name", ["NODVARD_DECK_SKIP_PRE_MIGRATE_BACKUP", "LATTICE_SKIP_PRE_MIGRATE_BACKUP"])
def test_the_emergency_exit_variable_has_a_new_and_an_old_name(name, monkeypatch, tmp_path):
    from nodvard_deck import config

    for key in list(os.environ):
        if key.upper().startswith(("NODVARD_DECK_", "LATTICE_")):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    assert config.Settings().skip_pre_migrate_backup is False
    monkeypatch.setenv(name, "1")
    assert config.Settings().skip_pre_migrate_backup is True


def test_a_new_installation_migrates_without_a_copy(ctx):
    assert not ctx.layout.db_path.exists()
    ctx.on_migrate = lambda: make_db(ctx.layout.db_path)
    assert boot.main() == 0 and ctx.calls == ["migrate"]
    assert ctx.copies() == []
    assert ctx.state()["started_ok"] is False and ctx.state()["last_migration"]["copy"] is None


def test_an_empty_database_file_is_nothing_worth_a_copy(ctx):
    sqlite3.connect(ctx.layout.db_path).close()
    assert boot.main() == 0 and ctx.copies() == []


def test_an_unreadable_database_stops_the_start_without_touching_it(ctx):
    ctx.layout.db_path.write_bytes(b"das ist keine datenbank " * 100)
    before = ctx.layout.db_path.read_bytes()
    assert boot.main() == 1 and ctx.calls == []
    assert ctx.layout.db_path.read_bytes() == before
    assert ctx.state()["failure"]["kind"] == "db_unreadable"


def test_a_successful_start_clears_the_failure_the_rescue_code_and_a_stale_request(ctx):
    make_db(ctx.layout.db_path)
    bootstate.write_state(ctx.data, {"app_version": "0.5.0", "started_ok": True, "failure": {"kind": "no_space", "reason": "alt", "log": []}})
    (ctx.data / ".boot" / "rescue_code.txt").write_text("ABCD-EFGH-JKLM\n")
    bootstate.request_rollback(ctx.data, "20260101T000000Z_a_b.db", by="test")
    assert boot.main() == 0
    assert "failure" not in ctx.state()
    assert not (ctx.data / ".boot" / "rescue_code.txt").exists()
    assert bootstate.read_rollback(ctx.data) is None


def test_an_unexpected_error_becomes_a_failure_with_return_code_one(ctx, monkeypatch):
    monkeypatch.setattr(boot.migrate, "known_revisions", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("alembic kaputt")))
    assert boot.main() == 1
    assert ctx.state()["failure"]["kind"] == "unexpected"


def test_duplicate_migrations_show_their_clear_message_on_the_rescue_page(ctx, monkeypatch):
    """Doppelte Migrationen in zwei Erweiterungsordnern: die Notseite zeigt die Meldung mit beiden Ordnern,
    nicht nur den Namen des Fehlers."""
    message = "Die Migrationen der Erweiterungsordner „old-ext“ und „renamed-ext“ sind doppelt."
    monkeypatch.setattr(
        boot.migrate, "known_revisions", lambda *a, **k: (_ for _ in ()).throw(boot.migrate.DuplicateRevisions(message))
    )
    assert boot.main() == 1
    assert ctx.state()["failure"]["kind"] == "unexpected"
    assert ctx.state()["failure"]["reason"] == message


def test_a_failed_record_never_hides_the_failure(ctx, monkeypatch):
    ctx.old_db()
    ctx.on_migrate = ctx.fake_fail
    monkeypatch.setattr(bootstate, "write_state", lambda *a, **k: (_ for _ in ()).throw(OSError(30, "Nur lesbar")))
    assert boot.main() == 1


# ---------------------------------------------------------------------------
# Downgrade: die Daten sind neuer als dieses Image
# ---------------------------------------------------------------------------


def newer_world(ctx, *, started_ok: bool, state_extra: dict | None = None):
    """Ein Datenstand wie nach dem Update einer NEUEREN Version: Kopie (Stand davor) liegt da, die Datenbank
    steht auf einem Stand, den dieses Image nicht kennt, und hat neue Daten."""
    make_db(ctx.layout.db_path, owner="alt-owner")
    old = dump(ctx.layout.db_path)
    info = premigrate.make_copy(ctx.layout, from_version="0.6.0", to_version="0.7.0", from_heads=HEADS, to_heads=FUTURE, now=1_790_000_000)
    set_heads(ctx.layout.db_path, FUTURE)
    conn = sqlite3.connect(ctx.layout.db_path)
    conn.execute("UPDATE users SET username = 'neu-owner'")
    conn.commit()
    conn.close()
    state = {
        "app_version": "0.7.0", "started_ok": started_ok, "db_heads": FUTURE,
        "last_migration": {"at": "2026-10-01T03:00:00Z", "from_version": "0.6.0", "to_version": "0.7.0", "from_heads": HEADS,
                           "to_heads": FUTURE, "copy": info.name, "state": "ok"},
    }
    if started_ok:
        state["started_at"] = "2026-10-01T03:01:00Z"
    state.update(state_extra or {})
    bootstate.write_state(ctx.data, state)
    return old, info


def test_the_new_version_never_started_so_the_copy_comes_back_automatically(ctx):
    old, info = newer_world(ctx, started_ok=False)
    assert boot.main() == 0
    assert premigrate.read_live(ctx.layout.db_path).heads == HEADS
    assert dump(ctx.layout.db_path) == old
    assert not info.path.exists(), "die Kopie ist jetzt die Datenbank"
    state = ctx.state()
    assert state["last_migration"]["state"] == "reverted" and state["started_ok"] is True and "failure" not in state
    assert ctx.calls == ["migrate"], "danach geht es ganz normal weiter"


def test_the_automatic_way_back_never_deletes_the_newer_database(ctx):
    # Fund: `started_ok` wird nur nach bestem Bemuehen gesetzt. Steht es faelschlich noch auf false, waere die Arbeit seit dem
    # Update ohne Rueckfrage geloescht worden. Jetzt liegt sie 30 Tage unter restore/replaced-… (nur umbenannt, kein Platz).
    newer_world(ctx, started_ok=False)
    newer = dump(ctx.layout.db_path)
    assert boot.main() == 0
    replaced = list(ctx.layout.restore_dir.glob("replaced-*"))
    assert len(replaced) == 1 and dump(replaced[0] / "lattice.db") == newer


def test_the_way_back_after_a_dead_migration_sets_the_half_state_aside_too(ctx):
    ctx.old_db()
    info = premigrate.make_copy(ctx.layout, from_version="0.5.0", to_version="0.6.0", from_heads=OLD_KNOWN, to_heads=HEADS, now=1_790_000_000)
    conn = sqlite3.connect(ctx.layout.db_path)
    conn.execute("CREATE TABLE halb_migriert (x)")
    conn.commit()
    conn.close()
    half = dump(ctx.layout.db_path)
    bootstate.write_state(ctx.data, {
        "app_version": "0.5.0", "started_ok": False,
        "last_migration": {"from_version": "0.5.0", "to_version": "0.6.0", "from_heads": OLD_KNOWN, "to_heads": HEADS, "copy": info.name, "state": "running"},
    })
    assert boot.main() == 0
    replaced = list(ctx.layout.restore_dir.glob("replaced-*"))
    assert len(replaced) == 1 and dump(replaced[0] / "lattice.db") == half, "Sicherheitsnetz: nie loeschen, nur beiseitelegen"


def test_the_new_version_did_start_so_there_is_only_the_rescue_page(ctx):
    _, info = newer_world(ctx, started_ok=True)
    before = dump(ctx.layout.db_path)
    assert boot.main() == 1 and ctx.calls == []
    assert dump(ctx.layout.db_path) == before, "nichts veraendert: auf der Datenbank sind inzwischen Nutzerdaten"
    assert info.path.exists()
    failure = ctx.state()["failure"]
    assert failure["kind"] == "newer_data"
    assert failure["data_version"] == "0.7.0" and failure["previous_version"] == "0.6.0" and failure["app_version"] == "0.6.0"
    assert failure["rollback"]["copy"] == info.name and failure["rollback"]["started_ok"] is True
    assert failure["rollback"]["started_at"] == "2026-10-01T03:01:00Z"


def test_an_explicit_rollback_request_restores_the_copy_even_after_a_good_start(ctx):
    old, info = newer_world(ctx, started_ok=True)
    newer = dump(ctx.layout.db_path)
    bootstate.request_rollback(ctx.data, info.name, by="notseite")
    assert boot.main() == 0
    assert dump(ctx.layout.db_path) == old
    assert bootstate.read_rollback(ctx.data) is None, "die Vormerkung ist verbraucht"
    replaced = list(ctx.layout.restore_dir.glob("replaced-*"))
    assert len(replaced) == 1 and dump(replaced[0] / "lattice.db") == newer, "die verworfenen neueren Daten bleiben als alter Stand liegen"
    assert ctx.state()["last_migration"]["state"] == "reverted"


def test_a_rollback_request_for_another_copy_does_nothing(ctx):
    _, info = newer_world(ctx, started_ok=True)
    bootstate.request_rollback(ctx.data, "20200101T000000Z_x_y.db", by="test")
    assert boot.main() == 1 and info.path.exists()


def test_an_expired_rollback_request_does_nothing(ctx):
    _, info = newer_world(ctx, started_ok=True)
    bootstate.request_rollback(ctx.data, info.name, by="test", now=time.time() - bootstate.ROLLBACK_TTL_S - 10)
    assert boot.main() == 1 and info.path.exists()


def test_a_rollback_request_is_ignored_when_the_data_are_not_newer(ctx):
    make_db(ctx.layout.db_path)
    bootstate.request_rollback(ctx.data, "20260101T000000Z_a_b.db", by="test")
    assert boot.main() == 0
    assert bootstate.read_rollback(ctx.data) is None, "ueberfluessig geworden, nicht stehen lassen"


def test_without_a_state_file_unknown_revisions_are_only_a_rescue_page(ctx):
    make_db(ctx.layout.db_path, versions=FUTURE)
    before = dump(ctx.layout.db_path)
    assert boot.main() == 1 and ctx.calls == []
    assert dump(ctx.layout.db_path) == before
    failure = ctx.state()["failure"]
    assert failure["kind"] == "newer_data" and failure["rollback"] is None


def test_unknown_revisions_that_the_last_migration_did_not_produce_are_not_touched(ctx):
    _, info = newer_world(ctx, started_ok=False)
    set_heads(ctx.layout.db_path, ["e11111111111"])  # ein ganz anderer Stand als nach der letzten Migration
    before = dump(ctx.layout.db_path)
    assert boot.main() == 1 and dump(ctx.layout.db_path) == before and info.path.exists()
    assert ctx.state()["failure"]["rollback"] is None


def test_two_versions_back_is_not_automatic_because_the_copy_is_not_from_our_heads(ctx):
    _, info = newer_world(ctx, started_ok=False)
    state = ctx.state()
    state["last_migration"]["from_heads"] = ["c0ffee000000"]  # die Kopie stammt von einem Stand, den WIR nicht kennen
    bootstate.write_state(ctx.data, state)
    assert boot.main() == 1
    assert ctx.state()["failure"]["kind"] == "newer_data" and ctx.state()["failure"]["rollback"] is None
    assert info.path.exists()


def test_a_missing_copy_is_a_rescue_page_with_the_right_reason(ctx):
    _, info = newer_world(ctx, started_ok=False)
    info.path.unlink()
    before = dump(ctx.layout.db_path)
    assert boot.main() == 1 and dump(ctx.layout.db_path) == before
    failure = ctx.state()["failure"]
    assert failure["kind"] == "copy_unusable" and failure["rollback"] is None


def test_a_damaged_copy_is_never_used(ctx):
    _, info = newer_world(ctx, started_ok=False)
    info.path.write_bytes(b"kaputt" * 200)
    before = dump(ctx.layout.db_path)
    assert boot.main() == 1 and dump(ctx.layout.db_path) == before
    assert ctx.state()["failure"]["kind"] == "copy_unusable"


def test_a_copy_with_other_heads_than_the_state_says_is_never_used(ctx):
    _, info = newer_world(ctx, started_ok=False)
    # Die Kopie ist ein ganz anderer Stand als im Zustand eingetragen.
    other = sqlite3.connect(info.path)
    other.execute("DELETE FROM alembic_version")
    other.execute("INSERT INTO alembic_version VALUES ('d00000000000')")
    other.commit()
    other.close()
    assert boot.main() == 1 and ctx.state()["failure"]["kind"] == "copy_unusable"


def test_a_symlinked_copy_is_never_used(ctx, tmp_path):
    _, info = newer_world(ctx, started_ok=False)
    real = tmp_path / "echte-kopie.db"
    info.path.rename(real)
    info.path.symlink_to(real)
    assert boot.main() == 1 and ctx.state()["failure"]["kind"] == "copy_unusable"
    assert real.exists()


@pytest.fixture
def cross_device_ctx(ctx, monkeypatch):
    """Die Datenbank liegt auf einem ANDEREN Dateisystem als der Datenordner (eigener `NODVARD_DECK_DATABASE_URL`,
    z. B. eine USB-SSD am Pi): hier echt, mit /dev/shm."""
    import dataclasses
    import shutil
    import uuid

    shm = Path("/dev/shm")
    if not shm.is_dir() or os.stat(shm).st_dev == os.stat(ctx.data).st_dev:
        pytest.skip("kein zweites Dateisystem fuer den Test")
    folder = shm / f"nodvard-test-{uuid.uuid4().hex}"
    folder.mkdir()
    ctx.layout = dataclasses.replace(ctx.layout, db_path=folder / "lattice.db")
    ctx.settings = settings_for(ctx.layout)
    monkeypatch.setattr(boot, "get_settings", lambda: ctx.settings)
    yield ctx
    shutil.rmtree(folder, ignore_errors=True)


def test_the_explicit_way_back_works_when_the_database_lives_on_another_drive(cross_device_ctx):
    # Fund: `_discard_live` wollte die Datenbank per rename nach restore/replaced-… legen -> EXDEV, das Journal blieb,
    # und jeder Start (auch mit der neuen Version) scheiterte an genau diesem Schritt.
    ctx = cross_device_ctx
    old, info = newer_world(ctx, started_ok=True)
    newer = dump(ctx.layout.db_path)
    bootstate.request_rollback(ctx.data, info.name, by="notseite")
    assert boot.main() == 0, ctx.state().get("failure")
    assert dump(ctx.layout.db_path) == old
    replaced = list(ctx.layout.restore_dir.glob("replaced-*"))
    assert len(replaced) == 1 and dump(replaced[0] / "lattice.db") == newer, "die neueren Daten liegen beiseite, nicht geloescht"
    assert "revert" not in ctx.state()
    assert not [p for p in replaced[0].iterdir() if p.name.endswith(premigrate.PART_SUFFIX)]


def test_without_room_to_set_the_database_aside_nothing_is_touched_and_no_journal_stays(cross_device_ctx, monkeypatch):
    ctx = cross_device_ctx
    _, info = newer_world(ctx, started_ok=True)
    before = dump(ctx.layout.db_path)
    bootstate.request_rollback(ctx.data, info.name, by="notseite")
    real_free = store.free_bytes
    monkeypatch.setattr(store, "free_bytes", lambda path: 10 if Path(path) == ctx.data else real_free(path))
    assert boot.main() == 1
    state = ctx.state()
    assert state["failure"]["kind"] == "no_space" and state["failure"]["free_bytes"] == 10 and state["failure"]["needed_bytes"] > 10
    assert "revert" not in state, "kein Journal, das jeden weiteren Start blockiert"
    assert dump(ctx.layout.db_path) == before and info.path.exists()


@pytest.mark.parametrize("reason", ["rollback_requested", "downgrade"])
def test_a_stop_after_the_copy_is_in_place_on_another_drive_never_overwrites_the_newer_data(cross_device_ctx, monkeypatch, reason):
    # Fund: Auf einem anderen Laufwerk wird die Kopie per Kopieren eingesetzt und erst danach geloescht. Stirbt der Prozess
    # genau dazwischen, steht das Journal noch und die Kopie ist noch da -- der naechste Start legte die schon eingesetzte
    # Kopie erneut "beiseite" und ueberschrieb damit den neueren Stand unter restore/replaced-….
    ctx = cross_device_ctx
    old, info = newer_world(ctx, started_ok=reason == "rollback_requested")
    newer = dump(ctx.layout.db_path)
    if reason == "rollback_requested":
        bootstate.request_rollback(ctx.data, info.name, by="notseite")
    real_unlink = os.unlink
    copy = info.path

    def unlink(path, *args, **kwargs):
        if Path(path) == copy:
            raise HardStop()
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(premigrate.os, "unlink", unlink)
    with pytest.raises(HardStop):
        boot.main()
    monkeypatch.setattr(premigrate.os, "unlink", real_unlink)
    assert ctx.state()["revert"]["reason"] == reason and copy.exists()
    assert dump(ctx.layout.db_path) == old, "die Kopie ist schon eingesetzt"

    assert boot.main() == 0, ctx.state().get("failure")
    assert dump(ctx.layout.db_path) == old and not copy.exists()
    replaced = list(ctx.layout.restore_dir.glob("replaced-*"))
    assert len(replaced) == 1 and dump(replaced[0] / "lattice.db") == newer, "der neuere Stand liegt weiter beiseite"
    assert "revert" not in ctx.state() and ctx.state()["last_migration"]["state"] == "reverted"


def test_a_dead_migration_on_another_drive_without_room_for_the_half_state_still_starts(cross_device_ctx, monkeypatch, capsys):
    # Fund: Seit der halbe Stand aufgehoben wird, brauchte der Rueckweg nach einer abgebrochenen Migration Platz im Datenordner
    # (die Datenbank wird dafuer kopiert). Fehlte er -- gerade weil die Kopie vor dem Update ihn belegt hat --, endete jeder Start
    # auf der Notseite "zu wenig Platz". Der halbe Stand ist nur ein Sicherheitsnetz: dann wird er wie frueher verworfen.
    ctx = cross_device_ctx
    old = ctx.old_db()
    info = premigrate.make_copy(ctx.layout, from_version="0.5.0", to_version="0.6.0", from_heads=OLD_KNOWN, to_heads=HEADS, now=1_790_000_000)
    conn = sqlite3.connect(ctx.layout.db_path)
    conn.execute("CREATE TABLE halb_migriert (x)")
    conn.commit()
    conn.close()
    bootstate.write_state(ctx.data, {
        "app_version": "0.5.0", "started_ok": False,
        "last_migration": {"from_version": "0.5.0", "to_version": "0.6.0", "from_heads": OLD_KNOWN, "to_heads": HEADS, "copy": info.name, "state": "running"},
    })
    seen = {}

    def during():
        seen["before_migration"] = dump(ctx.layout.db_path)
        ctx.fake_ok()

    ctx.on_migrate = during
    real_free = store.free_bytes
    monkeypatch.setattr(store, "free_bytes", lambda path: 10 if Path(path) == ctx.data else real_free(path))
    assert boot.main() == 0, ctx.state().get("failure")
    assert seen["before_migration"] == old, "erst zurueck auf die Kopie, dann neu migriert"
    assert premigrate.read_live(ctx.layout.db_path).heads == HEADS
    assert not list(ctx.layout.restore_dir.glob("replaced-*")), "kein Platz: der halbe Stand ist verworfen"
    assert "revert" not in ctx.state() and "failure" not in ctx.state()
    assert "Der halbe Stand wird nicht aufgehoben" in capsys.readouterr().out


@pytest.mark.parametrize("reason", ["rollback_requested", "downgrade"])
def test_newer_data_on_another_drive_are_never_dropped_for_lack_of_room(cross_device_ctx, monkeypatch, reason):
    # Gegenprobe: Neuere Daten werden nie verworfen, nur weil der Platz zum Beiseitelegen fehlt -- dann die Notseite.
    ctx = cross_device_ctx
    _, info = newer_world(ctx, started_ok=reason == "rollback_requested")
    before = dump(ctx.layout.db_path)
    if reason == "rollback_requested":
        bootstate.request_rollback(ctx.data, info.name, by="notseite")
    real_free = store.free_bytes
    monkeypatch.setattr(store, "free_bytes", lambda path: 10 if Path(path) == ctx.data else real_free(path))
    assert boot.main() == 1
    assert ctx.state()["failure"]["kind"] == "no_space" and "revert" not in ctx.state()
    assert dump(ctx.layout.db_path) == before and info.path.exists()


# ---------------------------------------------------------------------------
# Eine Migration, die mittendrin gestorben ist
# ---------------------------------------------------------------------------


def test_a_migration_that_died_in_the_middle_is_undone_before_the_next_try(ctx):
    old = ctx.old_db()
    info = premigrate.make_copy(ctx.layout, from_version="0.5.0", to_version="0.6.0", from_heads=OLD_KNOWN, to_heads=HEADS, now=1_790_000_000)
    conn = sqlite3.connect(ctx.layout.db_path)  # der Prozess starb nach dem ersten Schritt
    conn.execute("CREATE TABLE halb_migriert (x)")
    conn.commit()
    conn.close()
    bootstate.write_state(ctx.data, {
        "app_version": "0.5.0", "started_ok": False,
        "last_migration": {"from_version": "0.5.0", "to_version": "0.6.0", "from_heads": OLD_KNOWN, "to_heads": HEADS, "copy": info.name, "state": "running"},
    })
    seen = {}

    def during():
        seen["tables"] = [r[0] for r in sqlite3.connect(ctx.layout.db_path).execute("SELECT name FROM sqlite_master WHERE type='table'")]
        ctx.fake_ok()

    ctx.on_migrate = during
    assert boot.main() == 0
    assert "halb_migriert" not in seen["tables"], "die Migration startet auf dem sauberen Stand, nicht auf dem Trümmerhaufen"
    assert len(ctx.copies()) == 1 and ctx.copies()[0]["name"] != info.name, "und mit einer frischen Kopie des sauberen Stands"
    _ = old


def test_a_dead_migration_without_a_copy_is_just_tried_again(ctx):
    ctx.old_db()
    bootstate.write_state(ctx.data, {
        "app_version": "0.5.0", "started_ok": False,
        "last_migration": {"from_version": "0.5.0", "to_version": "0.6.0", "from_heads": OLD_KNOWN, "to_heads": HEADS, "copy": None, "state": "running"},
    })
    assert boot.main() == 0 and ctx.calls == ["migrate"]


def test_a_first_migration_that_died_in_the_middle_starts_over_on_a_fresh_database(ctx):
    # Fund: Frische Installation, die allererste Migration bricht ab (Strom weg, `docker restart`). SQLite legt Tabellen
    # sofort an, `alembic_version` kommt erst am Ende -- jeder weitere Versuch scheiterte an "table already exists".
    def dies():
        conn = sqlite3.connect(ctx.layout.db_path)
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
        conn.execute("CREATE TABLE halb_angelegt (x INTEGER)")
        conn.commit()
        conn.close()
        raise HardStopFirst()

    ctx.on_migrate = dies
    with pytest.raises(HardStopFirst):
        boot.main()
    migration = ctx.state()["last_migration"]
    assert migration["state"] == "running" and migration["copy"] is None and migration["had_data"] is False
    half = dump(ctx.layout.db_path)
    seen = {}

    def like_alembic():
        tables = [r[0] for r in sqlite3.connect(ctx.layout.db_path).execute("SELECT name FROM sqlite_master WHERE type='table'")]
        seen["tables"] = tables
        if "halb_angelegt" in tables:
            raise RuntimeError("table halb_angelegt already exists")
        make_db(ctx.layout.db_path)

    ctx.on_migrate = like_alembic
    assert boot.main() == 0, ctx.state().get("failure")
    assert seen["tables"] == [], "frisch migriert, nicht auf dem halben Stand"
    assert premigrate.read_live(ctx.layout.db_path).heads == HEADS
    replaced = list(ctx.layout.restore_dir.glob("replaced-*"))
    assert len(replaced) == 1 and dump(replaced[0] / "lattice.db") == half, "der halbe Stand liegt beiseite, nicht geloescht"
    assert ctx.copies() == [], "es gab nichts, wovon sich eine Kopie lohnt"


def test_a_database_with_accounts_is_never_set_aside_even_if_the_record_says_first_migration(ctx):
    # Gegenprobe: Steht noch ein alter Eintrag "erste Migration" da, die Datenbank hat aber Konten (z. B. von Hand neu
    # angelegt und mit einem anderen Image benutzt), wird sie normal behandelt: Kopie, dann Migration.
    ctx.old_db()
    before = dump(ctx.layout.db_path)
    bootstate.write_state(ctx.data, {"last_migration": {"from_heads": [], "to_heads": HEADS, "copy": None, "state": "running", "had_data": False}})
    assert boot.main() == 0
    assert not (ctx.layout.restore_dir.exists() and list(ctx.layout.restore_dir.glob("replaced-*")))
    assert len(ctx.copies()) == 1 and dump(ctx.layout.data_dir / "backups" / "vor-update" / ctx.copies()[0]["name"]) == before


class HardStopFirst(BaseException):
    """Wie SIGKILL mitten in der ersten Migration."""


class StopInTheWayBack(BaseException):
    """Strom weg oder SIGKILL mitten im Rueckweg auf die Kopie (kein `except Exception` greift)."""


def stop_in_the_way_back(ctx, monkeypatch, where: str) -> None:
    """Baut den Abbruch an einer Stelle des Rueckwegs ein: waehrend der Pruefung der Kopie (das Journal steht schon),
    direkt nach dem Einsetzen der Kopie, oder direkt nachdem das Journal geloescht ist."""
    if where == "pruefung":
        real = premigrate.snapshot.integrity_check

        def check(path):
            if bootstate.read_state(ctx.data).get("revert"):
                raise StopInTheWayBack()
            return real(path)

        monkeypatch.setattr(premigrate.snapshot, "integrity_check", check)
    elif where == "eingesetzt":
        real_install = premigrate._install

        def install(layout, src):
            real_install(layout, src)
            raise StopInTheWayBack()

        monkeypatch.setattr(premigrate, "_install", install)
    else:
        real_clear = premigrate._clear_revert

        def clear(*args):
            real_clear(*args)
            raise StopInTheWayBack()

        monkeypatch.setattr(premigrate, "_clear_revert", clear)


@pytest.mark.parametrize("where", ["pruefung", "eingesetzt", "journal_weg"])
def test_a_stop_in_the_way_back_after_a_failed_migration_never_locks_the_start(ctx, monkeypatch, where):
    # Fund: Der Rueckweg war fertig (oder wurde beim naechsten Start fertig), aber `last_migration` stand noch auf
    # `running` mit der verbrauchten Kopie -- jeder weitere Start endete auf der Notseite (`copy_unusable`).
    old = ctx.old_db()
    ctx.on_migrate = ctx.fake_fail
    with monkeypatch.context() as patched:
        stop_in_the_way_back(ctx, patched, where)
        with pytest.raises(StopInTheWayBack):
            boot.main()
    ctx.on_migrate = ctx.fake_ok
    ctx.calls.clear()
    assert boot.main() == 0, ctx.state().get("failure")
    assert ctx.calls == ["migrate"] and premigrate.read_live(ctx.layout.db_path).heads == HEADS
    state = ctx.state()
    assert state["last_migration"]["state"] == "ok" and "revert" not in state and "failure" not in state
    _ = old


@pytest.mark.parametrize("where", ["pruefung", "eingesetzt", "journal_weg"])
def test_a_stop_in_the_way_back_after_a_dead_migration_never_locks_the_start(ctx, monkeypatch, where):
    ctx.old_db()
    info = premigrate.make_copy(ctx.layout, from_version="0.5.0", to_version="0.6.0", from_heads=OLD_KNOWN, to_heads=HEADS, now=1_790_000_000)
    bootstate.write_state(ctx.data, {
        "app_version": "0.5.0", "started_ok": False,
        "last_migration": {"from_version": "0.5.0", "to_version": "0.6.0", "from_heads": OLD_KNOWN, "to_heads": HEADS, "copy": info.name, "state": "running"},
    })
    with monkeypatch.context() as patched:
        stop_in_the_way_back(ctx, patched, where)
        with pytest.raises(StopInTheWayBack):
            boot.main()
    assert boot.main() == 0, ctx.state().get("failure")
    assert premigrate.read_live(ctx.layout.db_path).heads == HEADS and ctx.state()["last_migration"]["state"] == "ok"


# ---------------------------------------------------------------------------
# Sperre
# ---------------------------------------------------------------------------


def test_a_running_application_blocks_boot_completely(ctx):
    ctx.old_db()
    lock = bootstate.acquire_lock(ctx.data, purpose="app")
    try:
        before = snapshot_tree(ctx.data, include_boot=True)
        assert boot.main() == boot.EXIT_LOCKED
        assert ctx.calls == []
        assert snapshot_tree(ctx.data, include_boot=True) == before, "nichts angefasst: keine Kopie, keine Migration, kein Zustand"
    finally:
        lock.release()
    assert boot.main() == 0


def test_a_running_application_blocks_a_pending_restore_too(ctx, tmp_path):
    from restore_helpers import make_backup, stage

    fill_live(ctx.layout, "alt")
    db = make_db(tmp_path / "q.db", owner="anna", users=2)
    backup = make_backup(tmp_path / "b.ndbak", db=db, files={"master.key": b"MASTER-NEU"})
    rid, staged = stage(ctx.layout, backup)
    restore.make_private_dir(ctx.layout.restore_dir)
    restore.write_pending(ctx.layout, restore_id=rid, source="owner", actor={"type": "user", "id": "u1", "label": "anna"}, sign_out_all=True, staged=staged)
    before = snapshot_tree(ctx.data)
    lock = bootstate.acquire_lock(ctx.data, purpose="app")
    try:
        assert boot.main() == boot.EXIT_LOCKED
        assert restore.pending_exists(ctx.layout) and snapshot_tree(ctx.data) == before
        assert ctx.layout.master_key.read_bytes() == b"master-alt"
    finally:
        lock.release()


def test_boot_waits_a_little_for_a_lock_that_is_just_being_released(ctx, monkeypatch):
    import threading

    make_db(ctx.layout.db_path)
    lock = bootstate.acquire_lock(ctx.data, purpose="app")
    threading.Timer(0.4, lock.release).start()
    monkeypatch.setattr(boot, "LOCK_WAIT_S", 10)
    assert boot.main() == 0


def test_boot_lets_go_of_the_lock_when_it_is_done(ctx):
    make_db(ctx.layout.db_path)
    assert boot.main() == 0
    assert bootstate.is_locked(ctx.data) is False, "die Anwendung muss die Sperre gleich bekommen"
    ok = bootstate.acquire_lock(ctx.data)
    ok.release()


# ---------------------------------------------------------------------------
# Wiederherstellung (2a-2) zusammen mit der Kopie
# ---------------------------------------------------------------------------


def test_rollback_failed_of_a_restore_becomes_a_failure_not_a_crash(ctx, monkeypatch):
    def broken(settings):
        raise restore.RollbackFailed(f"Rueckweg kaputt, der alte Stand liegt unter {ctx.data}/restore/replaced-X")

    monkeypatch.setattr(boot, "restore_step", broken)
    assert boot.main() == 1 and ctx.calls == []
    failure = ctx.state()["failure"]
    assert failure["kind"] == "rollback_failed"
    assert str(ctx.data) not in failure["reason"]


def test_an_unreadable_restore_journal_is_a_rescue_page_not_a_restart_loop(ctx):
    make_db(ctx.layout.db_path)
    restore.make_private_dir(ctx.layout.restore_dir)
    (ctx.layout.restore_dir / restore.JOURNAL_NAME).write_text("{kaputt")
    assert boot.main() == 1 and ctx.calls == []
    assert ctx.state()["failure"]["kind"] == "rollback_failed"


def test_after_a_committed_restore_the_old_migration_record_is_gone(ctx, tmp_path):
    from restore_helpers import make_backup, stage

    fill_live(ctx.layout, "alt")
    bootstate.write_state(ctx.data, {"app_version": "0.5.0", "started_ok": True, "last_migration": {
        "from_version": "0.4.0", "to_version": "0.5.0", "from_heads": HEADS, "to_heads": HEADS, "copy": "20260101T000000Z_a_b.db", "state": "ok"}})
    db = make_db(tmp_path / "q.db", owner="anna", users=2)
    backup = make_backup(tmp_path / "b.ndbak", db=db, files={"master.key": b"MASTER-NEU"})
    rid, staged = stage(ctx.layout, backup)
    restore.make_private_dir(ctx.layout.restore_dir)
    restore.write_pending(ctx.layout, restore_id=rid, source="owner", actor={"type": "user", "id": "u1", "label": "anna"}, sign_out_all=True, staged=staged)
    assert boot.main() == 0
    assert restore.read_result(ctx.layout)["ok"] is True
    assert "last_migration" not in ctx.state(), "der Eintrag gehoerte zur ALTEN Datenbank"


def test_failing_migration_of_a_restored_database_goes_back_to_the_restore_copy_then_to_the_old_state(ctx, tmp_path):
    from restore_helpers import make_backup, stage

    fill_live(ctx.layout, "alt")
    live_before = dump(ctx.layout.db_path)
    db = make_db(tmp_path / "q.db", owner="anna", users=2, versions=OLD_KNOWN)
    backup = make_backup(tmp_path / "b.ndbak", db=db, files={"master.key": b"MASTER-NEU"})
    rid, staged = stage(ctx.layout, backup)
    restore.make_private_dir(ctx.layout.restore_dir)
    restore.write_pending(ctx.layout, restore_id=rid, source="owner", actor={"type": "user", "id": "u1", "label": "anna"}, sign_out_all=True, staged=staged)

    def first_fails_then_ok():
        if len(ctx.calls) == 1:
            ctx.fake_fail()
        ctx.fake_ok()

    ctx.on_migrate = first_fails_then_ok
    assert boot.main() == 0
    assert ctx.calls == ["migrate", "migrate"]
    assert dump(ctx.layout.db_path) == live_before, "der alte Stand ist zurueck"
    result = restore.read_result(ctx.layout)
    assert result["ok"] is False and result["rolled_back"] is True
    assert "failure" not in ctx.state(), "der Start selbst ist ja gelungen"


class HardStop(BaseException):
    """Wie SIGKILL oder Strom weg: kein `except Exception` greift, nichts wird mehr festgehalten."""


OLD_RECORD = {"at": "2026-09-01T03:00:00Z", "from_version": "0.5.0", "to_version": "0.6.0", "from_heads": OLD_KNOWN, "to_heads": HEADS,
              "copy": None, "state": "ok"}


def users_of(db: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute("SELECT username FROM users ORDER BY id")]
    finally:
        conn.close()


def older_backup_pending(ctx, tmp_path) -> None:
    """Laufende Installation mit eigenen Daten (Konto `alt-owner`, Tabelle `marker`, Schluessel `master-alt`) und einer
    vorgemerkten Sicherung auf einem AELTEREN Stand: nach dem Einspielen muss sie erst migriert werden."""
    from restore_helpers import make_backup, stage

    fill_live(ctx.layout, "alt")
    conn = sqlite3.connect(ctx.layout.db_path)
    conn.execute("CREATE TABLE marker (v TEXT)")
    conn.execute("INSERT INTO marker VALUES ('aktuelle Daten')")
    conn.commit()
    conn.close()
    bootstate.write_state(ctx.data, {"app_version": "0.6.0", "started_ok": True, "db_heads": HEADS, "last_migration": OLD_RECORD})
    db = make_db(tmp_path / "q.db", owner="anna", users=2, versions=OLD_KNOWN)
    backup = make_backup(tmp_path / "b.ndbak", db=db, files={"master.key": b"MASTER-NEU"})
    rid, staged = stage(ctx.layout, backup)
    restore.make_private_dir(ctx.layout.restore_dir)
    restore.write_pending(ctx.layout, restore_id=rid, source="owner", actor={"type": "user", "id": "u1", "label": "anna"}, sign_out_all=True, staged=staged)


def assert_old_state_is_back(ctx) -> None:
    db = ctx.layout.db_path
    assert users_of(db) == ["alt-owner"], "die Datenbank der laufenden Installation ist zurueck"
    assert "marker" in [r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert ctx.layout.master_key.read_bytes() == b"master-alt", "und passt zu ihren Schluesseln"
    state = ctx.state()
    assert state["last_migration"] == OLD_RECORD, "der Eintrag ueber die Migration gehoert wieder zur alten Datenbank"
    assert state["started_ok"] is True
    assert "revert" not in state and "pre_restore" not in state


def test_a_hard_stop_during_the_migration_of_a_restored_backup_never_costs_the_old_database(ctx, tmp_path):
    # Fund: Der naechste Start nahm das Einspielen zurueck -- und setzte danach die Kopie der SICHERUNG ein (der Eintrag
    # `running` gehoerte zu ihr), die alte Datenbank war geloescht.
    older_backup_pending(ctx, tmp_path)

    def stopped():
        conn = sqlite3.connect(ctx.layout.db_path)
        conn.execute("CREATE TABLE halb_migriert (x)")
        conn.commit()
        conn.close()
        raise HardStop()

    ctx.on_migrate = stopped
    with pytest.raises(HardStop):
        boot.main()
    ctx.on_migrate = ctx.fake_ok
    assert boot.main() == 0
    assert_old_state_is_back(ctx)
    result = restore.read_result(ctx.layout)
    assert result["ok"] is False and result["rolled_back"] is True


def test_a_failed_way_back_during_a_restore_leaves_no_journal_for_a_later_start(ctx, tmp_path, monkeypatch):
    # Fund: Migration der Sicherung scheitert, das Zuruecksetzen auf ihre Kopie einmal auch. Der Start nahm das Einspielen
    # zurueck und lief -- aber das Rueckweg-Journal blieb und ersetzte beim NAECHSTEN Start die alte Datenbank.
    older_backup_pending(ctx, tmp_path)
    real_install = premigrate._install
    calls = {"n": 0}

    def install_fails_once(layout, src):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(5, "E/A-Fehler")
        return real_install(layout, src)

    monkeypatch.setattr(premigrate, "_install", install_fails_once)

    def first_fails_then_ok():
        if len(ctx.calls) == 1:
            ctx.fake_fail()
        ctx.fake_ok()

    ctx.on_migrate = first_fails_then_ok
    assert boot.main() == 0
    assert_old_state_is_back(ctx)
    assert boot.main() == 0, "ein spaeterer, ganz normaler Neustart"
    assert_old_state_is_back(ctx)


def test_a_hard_stop_right_after_taking_back_a_restore_keeps_the_old_database(ctx, tmp_path, monkeypatch):
    # Migration der Sicherung scheitert, der Rueckweg auf ihre Kopie auch; das Einspielen wird zurueckgenommen -- und
    # direkt danach ist der Strom weg.
    older_backup_pending(ctx, tmp_path)
    real_install, real_rollback = premigrate._install, restore.Applied.rollback
    monkeypatch.setattr(premigrate, "_install", lambda layout, src: (_ for _ in ()).throw(OSError(5, "E/A-Fehler")))

    def rollback_then_stop(self, reason):
        real_rollback(self, reason)
        raise HardStop()

    monkeypatch.setattr(restore.Applied, "rollback", rollback_then_stop)
    ctx.on_migrate = ctx.fake_fail
    with pytest.raises(HardStop):
        boot.main()
    monkeypatch.setattr(restore.Applied, "rollback", real_rollback)
    monkeypatch.setattr(premigrate, "_install", real_install)
    ctx.on_migrate = ctx.fake_ok
    assert boot.main() == 0
    assert_old_state_is_back(ctx)


def test_a_hard_stop_right_after_a_committed_restore_keeps_the_record_of_the_restored_database(ctx, tmp_path, monkeypatch):
    older_backup_pending(ctx, tmp_path)
    real_commit = restore.Applied.commit

    def commit_then_stop(self):
        real_commit(self)
        raise HardStop()

    monkeypatch.setattr(restore.Applied, "commit", commit_then_stop)
    with pytest.raises(HardStop):
        boot.main()
    monkeypatch.setattr(restore.Applied, "commit", real_commit)
    assert boot.main() == 0
    assert sorted(users_of(ctx.layout.db_path)) == ["anna", "nutzer1"], "die Sicherung bleibt eingespielt"
    state = ctx.state()
    assert state["last_migration"]["state"] == "ok" and state["last_migration"]["from_heads"] == OLD_KNOWN
    assert "pre_restore" not in state


# ---------------------------------------------------------------------------
# Keine SQLite-Datenbank
# ---------------------------------------------------------------------------


def test_a_non_sqlite_database_is_migrated_without_a_copy(tmp_path, monkeypatch):
    from nodvard_deck import config

    settings = config.Settings(env="dev", data_dir=tmp_path, database_url="postgresql+asyncpg://x/y")
    monkeypatch.setattr(boot, "get_settings", lambda: settings)
    calls = []
    monkeypatch.setattr(boot.migrate, "main", lambda: calls.append(1))
    assert boot.main() == 0 and calls == [1]
    assert not (tmp_path / "backups").exists()


def test_a_failing_migration_of_a_non_sqlite_database_is_a_failure_record(tmp_path, monkeypatch):
    from nodvard_deck import config

    settings = config.Settings(env="dev", data_dir=tmp_path, database_url="postgresql+asyncpg://x/y")
    monkeypatch.setattr(boot, "get_settings", lambda: settings)
    monkeypatch.setattr(boot.migrate, "main", lambda: (_ for _ in ()).throw(RuntimeError("kaputt")))
    assert boot.main() == 1
    assert bootstate.read_state(tmp_path)["failure"]["kind"] == "migration_failed"
