"""`nodvard_deck.boot`: der Start vor der Anwendung -- vorgemerkte Wiederherstellung einspielen, danach
wie bisher migrieren. Die Migration selbst ist hier durch eine Attrappe ersetzt (die echte laeuft im
End-zu-End-Test `test_restore_e2e.py`)."""

from __future__ import annotations

import sqlite3
import time

import pytest
from nodvard_deck import boot, config
from nodvard_deck.core.backup import restore
from restore_helpers import (
    REPO_ROOT,
    fill_live,
    make_backup,
    make_db,
    make_layout,
    settings_for,
    snapshot_tree,
    stage,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    layout = make_layout(tmp_path)
    restore.make_private_dir(layout.restore_dir)
    settings = settings_for(layout)
    monkeypatch.setattr(boot, "get_settings", lambda: settings)
    monkeypatch.chdir(REPO_ROOT)
    calls: list[str] = []
    monkeypatch.setattr(boot.migrate, "main", lambda: calls.append("migrate"))
    return layout, calls, tmp_path


def _schedule(layout, tmp_path, source="owner", **kw):
    db = make_db(tmp_path / "q.db", owner="anna", users=2)
    backup = make_backup(tmp_path / "b.ndbak", db=db, files={"master.key": b"MASTER-NEU"})
    rid, staged = stage(layout, backup)
    restore.write_pending(layout, restore_id=rid, source=source, actor={"type": "user", "id": "u1", "label": "anna"},
                          sign_out_all=True, staged=staged, **kw)
    return rid


def test_without_pending_restore_it_just_migrates(env):
    layout, calls, _ = env
    assert boot.main() == 0 and calls == ["migrate"]
    assert restore.read_result(layout) is None


def test_pending_restore_is_applied_before_the_migration_and_final_only_after_it(env, monkeypatch):
    layout, calls, tmp_path = env
    fill_live(layout, "alt")
    _schedule(layout, tmp_path)
    seen = {}

    def migrate():
        # Mitten in der Migration: neuer Stand liegt da, aber das Ergebnis steht noch nicht fest.
        seen["owner"] = restore.inspect_db(layout.db_path).owner_name
        seen["result"] = restore.read_result(layout)
        seen["journal"] = (layout.restore_dir / restore.JOURNAL_NAME).exists()
        calls.append("migrate")

    monkeypatch.setattr(boot.migrate, "main", migrate)
    assert boot.main() == 0
    assert seen == {"owner": "anna", "result": None, "journal": True}
    result = restore.read_result(layout)
    assert result["ok"] is True and layout.master_key.read_bytes() == b"MASTER-NEU"
    assert not (layout.restore_dir / restore.JOURNAL_NAME).exists() and not restore.pending_exists(layout)


def test_failing_migration_after_a_restore_brings_the_old_state_back(env, monkeypatch):
    layout, calls, tmp_path = env
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir, ignore_sidecars=True)
    _schedule(layout, tmp_path)

    def migrate():
        calls.append("migrate")
        if len(calls) == 1:
            raise RuntimeError("Migration kaputt")

    monkeypatch.setattr(boot.migrate, "main", migrate)
    assert boot.main() == 0
    assert calls == ["migrate", "migrate"], "danach wird der alte Stand ganz normal migriert"
    assert snapshot_tree(layout.data_dir, ignore_sidecars=True) == before
    result = restore.read_result(layout)
    assert result["ok"] is False and "Stand dieser Version" in result["message"] and result["rolled_back"] is True


def test_failing_migration_without_a_restore_still_fails(env, monkeypatch):
    layout, calls, _ = env

    def migrate():
        raise RuntimeError("kaputt")

    monkeypatch.setattr(boot.migrate, "main", migrate)
    assert boot.main() == 1, "kein Absturz mehr, sondern Rueckgabewert 1 (-> Notseite, siehe test_boot_guard.py)"


def test_failed_rollback_stops_the_start(env, monkeypatch):
    layout, calls, tmp_path = env
    fill_live(layout, "alt")
    _schedule(layout, tmp_path)
    monkeypatch.setattr(boot.migrate, "main", lambda: (_ for _ in ()).throw(RuntimeError("kaputt")))

    def broken(*a, **k):
        raise restore.RollbackFailed("Rueckweg kaputt")

    monkeypatch.setattr(restore.Applied, "rollback", lambda self, reason: broken())
    assert boot.main() == 1


def test_rollback_failure_while_applying_stops_the_start_without_migrating(env, monkeypatch):
    layout, calls, tmp_path = env
    fill_live(layout, "alt")
    _schedule(layout, tmp_path)
    real = restore._move
    n = {"i": 0}

    def move(src, dst):
        n["i"] += 1
        if n["i"] >= 3:
            raise OSError(5, "E/A-Fehler")
        real(src, dst)

    monkeypatch.setattr(restore, "_move", move)
    assert boot.main() == 1
    assert calls == [], "auf einem halben Stand wird nicht migriert"


def test_tampered_staging_does_not_stop_the_start(env):
    layout, calls, tmp_path = env
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir, ignore_sidecars=True)
    rid = _schedule(layout, tmp_path)
    (layout.restore_dir / rid / restore.STAGING_NAME / "files" / "master.key").write_bytes(b"MANIPULIERT")
    assert boot.main() == 0 and calls == ["migrate"]
    assert snapshot_tree(layout.data_dir, ignore_sidecars=True) == before
    assert restore.read_result(layout)["ok"] is False


def test_interrupted_apply_from_an_earlier_start_is_rolled_back_first(env):
    layout, calls, tmp_path = env
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir, ignore_sidecars=True)
    _schedule(layout, tmp_path)
    applied = restore.apply_pending(layout, known=None, current_version="0.5.0")
    assert applied is not None  # der Prozess stirbt hier, bevor commit() kommt
    assert boot.main() == 0
    assert snapshot_tree(layout.data_dir, ignore_sidecars=True) == before
    assert restore.read_result(layout)["ok"] is False and calls == ["migrate"]


def test_expired_pending_is_dropped_at_boot(env):
    layout, calls, tmp_path = env
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir, ignore_sidecars=True)
    _schedule(layout, tmp_path, now=time.time() - restore.PENDING_TTL_S - 60)
    assert boot.main() == 0 and snapshot_tree(layout.data_dir, ignore_sidecars=True) == before
    assert "abgelaufen" in restore.read_result(layout)["message"]


def test_unreadable_migrations_mean_nothing_is_applied(env, monkeypatch):
    layout, calls, tmp_path = env
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir, ignore_sidecars=True)
    _schedule(layout, tmp_path)
    monkeypatch.setattr(boot.migrate, "known_revisions", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaputt")))
    assert boot.main() == 1, "ohne lesbare Migrationen geht auch die Migration nicht"
    assert snapshot_tree(layout.data_dir, ignore_sidecars=True) == before and not restore.pending_exists(layout)
    assert "nichts eingespielt" in restore.read_result(layout)["message"]


def test_non_sqlite_database_has_nothing_to_restore(tmp_path, monkeypatch):
    settings = config.Settings(env="dev", data_dir=tmp_path, database_url="postgresql+asyncpg://x/y")
    monkeypatch.setattr(boot, "get_settings", lambda: settings)
    monkeypatch.setattr(boot.migrate, "main", lambda: None)
    assert boot.main() == 0


def test_restore_dir_without_pending_is_left_alone(env):
    layout, calls, _ = env
    (layout.restore_dir / "replaced-20260101T000000").mkdir()
    assert boot.main() == 0 and (layout.restore_dir / "replaced-20260101T000000").is_dir()
    _ = sqlite3
