"""`core/backup/premigrate.py`: Kopie der Datenbank vor jeder Migration, Platzpruefung, Aufbewahrung
und der Rueckweg auf eine Kopie (mit Journal -- auch ein Absturz mittendrin ist heilbar)."""

from __future__ import annotations

import errno
import math
import os
import sqlite3
import stat
from pathlib import Path

import pytest
from nodvard_deck.core import bootstate
from nodvard_deck.core.backup import premigrate, restore, snapshot, store
from nodvard_deck.core.backup.errors import DamagedBackup, UnusableBackup
from restore_helpers import make_db, make_layout

HEADS = ["a1b2c3d4e5f6"]
NOW = 1_790_000_000.0  # 2026-09-21 ...


@pytest.fixture
def layout(tmp_path):
    lay = make_layout(tmp_path)
    make_db(lay.db_path, owner="alt-owner", users=1, hosts=3)
    return lay


def dump(path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def make(layout, now=NOW, **kw):
    args = dict(from_version="0.5.0", to_version="0.6.0", from_heads=HEADS, to_heads=["ffff00000000"], now=now)
    args.update(kw)
    return premigrate.make_copy(layout, **args)


# ---------------------------------------------------------------------------
# Kopie anlegen
# ---------------------------------------------------------------------------


def test_copy_is_a_complete_private_copy_with_a_manifest(layout):
    before = dump(layout.db_path)
    info = make(layout)
    assert info.name == "20260921T141320Z_0.5.0_0.6.0.db"
    path = layout.data_dir / "backups" / "vor-update" / info.name
    assert path == info.path and path.is_file()
    assert dump(path) == before, "inhaltlich dieselbe Datenbank"
    snapshot.integrity_check(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    sidecar = restore.read_json(Path(str(path) + ".json"))
    assert sidecar["from_version"] == "0.5.0" and sidecar["to_version"] == "0.6.0" and sidecar["from_heads"] == HEADS
    assert sorted(p.name for p in path.parent.iterdir()) == [info.name, info.name + ".json"], "keine Reste (.part, -journal)"
    assert dump(layout.db_path) == before, "die Datenbank selbst bleibt unberuehrt"


def test_copy_contains_what_is_still_in_the_wal(layout):
    live = sqlite3.connect(layout.db_path)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("UPDATE hosts SET name = 'nur-im-wal' WHERE rowid = 1")
    live.commit()
    assert os.path.getsize(str(layout.db_path) + "-wal") > 0
    try:
        info = make(layout)
    finally:
        live.close()
    conn = sqlite3.connect(f"file:{info.path}?mode=ro&immutable=1", uri=True)
    try:
        assert conn.execute("SELECT count(*) FROM hosts WHERE name = 'nur-im-wal'").fetchone()[0] == 1
    finally:
        conn.close()


def test_version_labels_are_made_safe_for_file_names(layout):
    info = make(layout, from_version="../../etc/pass wd; rm -rf", to_version=None)
    assert premigrate.NAME_RE.match(info.name), info.name
    assert "/" not in info.name and ".." not in info.name
    info2 = make(layout, now=NOW + 5, from_version=None, to_version="x" * 100)
    assert premigrate.NAME_RE.match(info2.name) and "unbekannt" in info2.name


def test_two_copies_in_the_same_second_do_not_overwrite_each_other(layout):
    first = make(layout)
    second = make(layout)
    assert first.name != second.name and first.path.is_file() and second.path.is_file()


# ---------------------------------------------------------------------------
# Platz
# ---------------------------------------------------------------------------


def test_required_space_is_one_point_two_times_database_and_wal(layout):
    size = layout.db_path.stat().st_size
    assert premigrate.required_bytes(layout.db_path) == math.ceil(size * 1.2)
    Path(str(layout.db_path) + "-wal").write_bytes(b"x" * 10_000)
    assert premigrate.required_bytes(layout.db_path) >= (size + 10_000) * 1.2


def test_not_enough_space_writes_nothing_and_says_how_much(layout, monkeypatch):
    needed = premigrate.required_bytes(layout.db_path)
    monkeypatch.setattr(store, "free_bytes", lambda path: needed - 1)
    with pytest.raises(premigrate.NoSpaceForCopy) as excinfo:
        make(layout)
    assert excinfo.value.needed == needed and excinfo.value.free == needed - 1
    assert "Platz" in str(excinfo.value)
    assert not (layout.data_dir / "backups" / "vor-update").exists() or list((layout.data_dir / "backups" / "vor-update").iterdir()) == []


def test_exactly_enough_space_is_enough(layout, monkeypatch):
    needed = premigrate.required_bytes(layout.db_path)
    monkeypatch.setattr(store, "free_bytes", lambda path: needed)
    assert make(layout).path.is_file()


def test_unknown_free_space_does_not_block(layout, monkeypatch):
    def broken(path):
        raise OSError("statvfs geht hier nicht")

    monkeypatch.setattr(store, "free_bytes", broken)
    assert make(layout).path.is_file()


# ---------------------------------------------------------------------------
# Aufbewahrung
# ---------------------------------------------------------------------------


def test_only_the_three_newest_copies_stay(layout):
    names = [make(layout, now=NOW + 60 * i).name for i in range(5)]
    left = sorted(p.name for p in (layout.data_dir / "backups" / "vor-update").iterdir())
    assert left == sorted([*names[2:], *(n + ".json" for n in names[2:])])


def test_a_failed_copy_keeps_the_older_ones_and_leaves_no_traces(layout, monkeypatch):
    old = [make(layout, now=NOW + 60 * i).name for i in range(3)]

    def bad(src, dst):
        Path(dst).write_bytes(b"halb")
        raise snapshot.IntegrityCheckFailed("kaputt")

    monkeypatch.setattr(snapshot, "online_copy", bad)
    with pytest.raises(snapshot.IntegrityCheckFailed):
        make(layout, now=NOW + 999)
    directory = layout.data_dir / "backups" / "vor-update"
    assert sorted(p.name for p in directory.iterdir()) == sorted([*old, *(n + ".json" for n in old)]), "nichts gelöscht, nichts übrig"


def test_stale_part_files_are_swept(layout):
    make(layout)
    directory = layout.data_dir / "backups" / "vor-update"
    (directory / "20260101T000000Z_a_b.db.part").write_bytes(b"alt")
    make(layout, now=NOW + 60)
    assert not [p for p in directory.iterdir() if p.name.endswith(".part")]


# ---------------------------------------------------------------------------
# Auflisten und Namen
# ---------------------------------------------------------------------------


def test_list_copies_newest_first_ignores_foreign_things(layout):
    first = make(layout, now=NOW)
    second = make(layout, now=NOW + 3600, from_version="0.6.0", to_version="0.7.0")
    directory = layout.data_dir / "backups" / "vor-update"
    (directory / "evil.db").write_bytes(b"x")
    (directory / "20260101T000000Z_a_b.db").symlink_to(first.path)
    (directory / "20260102T000000Z_a_b.db").mkdir()
    listing = premigrate.list_copies(layout.data_dir)
    assert [c["name"] for c in listing] == [second.name, first.name]
    assert listing[0]["from_version"] == "0.6.0" and listing[0]["to_version"] == "0.7.0"
    assert listing[0]["created_at"] == "2026-09-21T15:13:20Z"
    assert isinstance(listing[0]["size"], int) and listing[0]["size"] > 0
    assert set(listing[0]) == {"name", "created_at", "from_version", "to_version", "size"}


def test_list_copies_without_a_directory(tmp_path):
    assert premigrate.list_copies(tmp_path) == []


def test_copy_path_only_for_real_files_with_our_names(layout):
    info = make(layout)
    assert premigrate.copy_path(layout.data_dir, info.name) == info.path
    assert premigrate.copy_path(layout.data_dir, "../" + info.name) is None
    assert premigrate.copy_path(layout.data_dir, "nicht-unser-name.db") is None
    assert premigrate.copy_path(layout.data_dir, "20260101T000000Z_a_b.db") is None
    link = info.path.with_name("20260102T000000Z_a_b.db")
    link.symlink_to(info.path)
    assert premigrate.copy_path(layout.data_dir, link.name) is None


def test_read_heads_of_a_copy_and_of_the_live_database(layout):
    info = make(layout)
    heads = premigrate.read_heads(info.path)
    assert heads == premigrate.read_live(layout.db_path).heads and heads, "die echten Staende aus make_db"


def test_read_live_distinguishes_nothing_empty_and_data(tmp_path):
    assert premigrate.read_live(tmp_path / "gibt-es-nicht.db") == premigrate.LiveDb(has_data=False, heads=[])
    empty = tmp_path / "leer.db"
    empty.write_bytes(b"")
    assert premigrate.read_live(empty).has_data is False
    only_sqlite = tmp_path / "leer2.db"
    sqlite3.connect(only_sqlite).close()
    assert premigrate.read_live(only_sqlite).has_data is False
    plain = tmp_path / "ohne-alembic.db"
    conn = sqlite3.connect(plain)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    assert premigrate.read_live(plain) == premigrate.LiveDb(has_data=True, heads=[], accounts=False), "ohne Tabelle users: kein Konto"
    conn = sqlite3.connect(plain)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    conn.commit()
    assert premigrate.read_live(plain).accounts is False
    conn.execute("INSERT INTO users (username) VALUES ('nico')")
    conn.commit()
    conn.close()
    assert premigrate.read_live(plain).accounts is True


def test_read_live_of_garbage_raises(tmp_path):
    garbage = tmp_path / "muell.db"
    garbage.write_bytes(b"das ist keine sqlite-datei " * 50)
    with pytest.raises(DamagedBackup):
        premigrate.read_live(garbage)


# ---------------------------------------------------------------------------
# Rueckweg auf eine Kopie
# ---------------------------------------------------------------------------


def with_newer_state(layout, tmp_path):
    """Live: Stand mit neuen Daten und fremden Nebendateien; Kopie: der Stand davor."""
    old_dump = dump(layout.db_path)
    info = make(layout)
    conn = sqlite3.connect(layout.db_path)
    conn.execute("DELETE FROM alembic_version")
    conn.execute("INSERT INTO alembic_version VALUES ('ffff00000000')")
    conn.execute("UPDATE users SET username = 'neu-owner'")
    conn.commit()
    conn.close()
    Path(str(layout.db_path) + "-wal").write_bytes(b"neue-wal")
    Path(str(layout.db_path) + "-shm").write_bytes(b"neue-shm")
    restore.make_private_dir(layout.restore_dir)
    return info, old_dump


def test_revert_puts_the_copy_in_place_and_removes_foreign_sidecars(layout, tmp_path):
    info, old_dump = with_newer_state(layout, tmp_path)
    premigrate.restore_copy(layout, info.name, keep_discarded=False, reason="migration_failed", now=NOW)
    assert dump(layout.db_path) == old_dump
    assert not os.path.lexists(str(layout.db_path) + "-wal") and not os.path.lexists(str(layout.db_path) + "-shm")
    assert not info.path.exists() and not os.path.lexists(str(info.path) + ".json"), "die Kopie ist jetzt die Datenbank"
    assert premigrate.read_live(layout.db_path).heads == HEADS or premigrate.read_live(layout.db_path).has_data
    assert "revert" not in bootstate.read_state(layout.data_dir)
    assert not [p for p in layout.restore_dir.iterdir() if p.name.startswith("replaced-")]
    snapshot.integrity_check(layout.db_path)


def test_revert_can_keep_the_discarded_state_under_replaced(layout, tmp_path):
    info, old_dump = with_newer_state(layout, tmp_path)
    newer = dump(layout.db_path)
    premigrate.restore_copy(layout, info.name, keep_discarded=True, reason="rollback", now=NOW)
    assert dump(layout.db_path) == old_dump
    replaced = [p for p in layout.restore_dir.iterdir() if p.name.startswith("replaced-")]
    assert len(replaced) == 1 and replaced[0].name == "replaced-20260921T141320"
    assert dump(replaced[0] / "lattice.db") == newer, "der verworfene neuere Stand liegt dort"
    assert (replaced[0] / "lattice.db-wal").read_bytes() == b"neue-wal"
    assert stat.S_IMODE(replaced[0].stat().st_mode) == 0o700


def test_revert_refuses_a_damaged_copy_before_touching_anything(layout, tmp_path):
    info, _ = with_newer_state(layout, tmp_path)
    info.path.write_bytes(b"kaputt" * 100)
    live_before = layout.db_path.read_bytes()
    with pytest.raises(DamagedBackup):
        premigrate.restore_copy(layout, info.name, keep_discarded=False, reason="x", now=NOW)
    assert layout.db_path.read_bytes() == live_before
    assert os.path.lexists(str(layout.db_path) + "-wal")
    assert "revert" not in bootstate.read_state(layout.data_dir), "kein Journal, wenn gar nichts angefasst wurde"


def test_revert_refuses_a_missing_or_foreign_copy(layout, tmp_path):
    info, _ = with_newer_state(layout, tmp_path)
    with pytest.raises(UnusableBackup):
        premigrate.restore_copy(layout, "20260101T000000Z_a_b.db", keep_discarded=False, reason="x", now=NOW)
    with pytest.raises(UnusableBackup):
        premigrate.restore_copy(layout, "../../etc/passwd", keep_discarded=False, reason="x", now=NOW)


def test_crash_in_the_middle_of_a_revert_is_finished_by_the_next_start(layout, tmp_path, monkeypatch):
    info, old_dump = with_newer_state(layout, tmp_path)
    real = premigrate._move
    calls = {"n": 0}

    def move(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(5, "Stromausfall")
        real(src, dst)

    monkeypatch.setattr(premigrate, "_move", move)
    with pytest.raises(OSError):
        premigrate.restore_copy(layout, info.name, keep_discarded=True, reason="rollback", now=NOW)
    state = bootstate.read_state(layout.data_dir)
    assert state["revert"]["copy"] == info.name and state["revert"]["keep"] is True, "das Journal steht"
    assert info.path.exists(), "die Kopie ist noch da"
    monkeypatch.setattr(premigrate, "_move", real)
    assert premigrate.finish_revert(layout) is True
    assert dump(layout.db_path) == old_dump
    assert not os.path.lexists(str(layout.db_path) + "-wal") and not os.path.lexists(str(layout.db_path) + "-shm")
    assert "revert" not in bootstate.read_state(layout.data_dir)
    replaced = [p for p in layout.restore_dir.iterdir() if p.name.startswith("replaced-")]
    assert len(replaced) == 1 and sorted(p.name for p in replaced[0].iterdir()) == ["lattice.db", "lattice.db-shm", "lattice.db-wal"]


def test_crash_after_the_rename_but_before_clearing_the_journal(layout, tmp_path, monkeypatch):
    info, old_dump = with_newer_state(layout, tmp_path)

    def boom(*args):
        raise OSError(5, "Absturz")

    monkeypatch.setattr(premigrate, "_clear_revert", boom)
    with pytest.raises(OSError):
        premigrate.restore_copy(layout, info.name, keep_discarded=False, reason="x", now=NOW)
    assert not info.path.exists() and bootstate.read_state(layout.data_dir)["revert"]
    monkeypatch.undo()
    assert premigrate.finish_revert(layout) is True
    assert dump(layout.db_path) == old_dump and "revert" not in bootstate.read_state(layout.data_dir)


def test_finish_revert_without_copy_and_with_a_wrong_database_fails_loudly(layout, tmp_path, monkeypatch):
    info, _ = with_newer_state(layout, tmp_path)
    monkeypatch.setattr(premigrate, "_clear_revert", lambda *a: (_ for _ in ()).throw(OSError(5, "x")))
    with pytest.raises(OSError):
        premigrate.restore_copy(layout, info.name, keep_discarded=False, reason="x", now=NOW)
    monkeypatch.undo()
    conn = sqlite3.connect(layout.db_path)  # jetzt steht dort etwas anderes als erwartet
    conn.execute("DELETE FROM alembic_version")
    conn.execute("INSERT INTO alembic_version VALUES ('zzzzzzzzzzzz')")
    conn.commit()
    conn.close()
    with pytest.raises(premigrate.RevertFailed):
        premigrate.finish_revert(layout)
    assert bootstate.read_state(layout.data_dir)["revert"], "das Journal bleibt stehen"


def test_finishing_a_revert_marks_its_own_migration_as_reverted_in_the_same_write(layout, tmp_path, monkeypatch):
    info, _ = with_newer_state(layout, tmp_path)
    record = {"from_version": "0.5.0", "to_version": "0.6.0", "from_heads": HEADS, "to_heads": ["ffff00000000"], "copy": info.name, "state": "running"}
    bootstate.write_state(layout.data_dir, {"started_ok": False, "last_migration": record})
    written = []
    real_write = bootstate.write_state
    monkeypatch.setattr(bootstate, "write_state", lambda d, st: (written.append(bootstate.clean_state(st)), real_write(d, st)))
    premigrate.restore_copy(layout, info.name, keep_discarded=False, reason="interrupted", now=NOW)
    state = bootstate.read_state(layout.data_dir)
    assert "revert" not in state and state["last_migration"]["state"] == "reverted" and state["started_ok"] is True
    for snapshot_of_state in written:  # nie ein Stand "Journal weg, Migration noch running"
        assert "revert" in snapshot_of_state or snapshot_of_state["last_migration"]["state"] == "reverted"


def test_finishing_a_revert_leaves_the_record_of_another_copy_alone(layout, tmp_path):
    info, _ = with_newer_state(layout, tmp_path)
    record = {"from_version": "0.5.0", "to_version": "0.6.0", "from_heads": HEADS, "to_heads": HEADS, "copy": "20200101T000000Z_a_b.db", "state": "ok"}
    bootstate.write_state(layout.data_dir, {"started_ok": False, "last_migration": record})
    premigrate.restore_copy(layout, info.name, keep_discarded=False, reason="x", now=NOW)
    state = bootstate.read_state(layout.data_dir)
    assert state["last_migration"]["state"] == "ok" and state["started_ok"] is False


def test_setting_aside_across_file_systems_copies_first_and_can_be_repeated(layout, tmp_path, monkeypatch):
    # Datenbank auf einem anderen Laufwerk: umbenennen geht nicht (EXDEV). Bricht das Kopieren ab, bleibt die Quelle stehen,
    # das Journal auch, und der naechste Lauf macht es fertig.
    info, old_dump = with_newer_state(layout, tmp_path)
    newer = dump(layout.db_path)
    monkeypatch.setattr(premigrate, "_move", lambda src, dst: (_ for _ in ()).throw(OSError(errno.EXDEV, "Invalid cross-device link")))
    real_copy = premigrate.shutil.copyfile
    calls = {"n": 0}

    def copy_breaks_once(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            Path(dst).write_bytes(b"halb")
            raise OSError(5, "E/A-Fehler")
        return real_copy(src, dst)

    monkeypatch.setattr(premigrate.shutil, "copyfile", copy_breaks_once)
    with pytest.raises(OSError):
        premigrate.restore_copy(layout, info.name, keep_discarded=True, reason="rollback", now=NOW)
    assert dump(layout.db_path) == newer and bootstate.read_state(layout.data_dir)["revert"], "nichts verloren, das Journal steht"
    assert premigrate.finish_revert(layout) is True
    assert dump(layout.db_path) == old_dump
    replaced = [p for p in layout.restore_dir.iterdir() if p.name.startswith("replaced-")]
    assert len(replaced) == 1 and dump(replaced[0] / "lattice.db") == newer
    assert (replaced[0] / "lattice.db-wal").read_bytes() == b"neue-wal"
    assert not [p for p in replaced[0].iterdir() if p.name.endswith(premigrate.PART_SUFFIX)]


class HardStop(BaseException):
    """Prozess weg (SIGKILL, Strom aus): kein `except Exception` faengt das."""


def _across_file_systems(layout, info, monkeypatch):
    """Datenbank auf einem anderen Laufwerk: Beiseitelegen und Einsetzen der Kopie gehen nur ueber Kopieren."""
    monkeypatch.setattr(premigrate, "_move", lambda src, dst: (_ for _ in ()).throw(OSError(errno.EXDEV, "Invalid cross-device link")))
    real_replace = os.replace

    def replace(src, dst):
        if Path(src) == info.path and Path(dst) == layout.db_path:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        real_replace(src, dst)

    monkeypatch.setattr(premigrate.os, "replace", replace)


def _stop_when_unlinking(monkeypatch, victim: Path):
    real_unlink = os.unlink

    def unlink(path, *args, **kwargs):
        if Path(path) == victim:
            raise HardStop()
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(premigrate.os, "unlink", unlink)
    return lambda: monkeypatch.setattr(premigrate.os, "unlink", real_unlink)


@pytest.mark.parametrize("stop_at", ["copy", "live"], ids=["kopie_eingesetzt_nicht_geloescht", "beiseite_quelle_nicht_geloescht"])
def test_a_second_run_never_overwrites_what_was_already_set_aside(layout, tmp_path, monkeypatch, stop_at):
    # Fund: Auf einem anderen Laufwerk setzt `_install` die Kopie per Kopieren ein und loescht sie erst danach. Brach es
    # genau dazwischen ab, legte der naechste Lauf die schon eingesetzte Kopie "beiseite" -- ueber den neueren Stand.
    # Gegenprobe: Abbruch beim Beiseitelegen selbst, nach dem fertigen Ziel und vor dem Loeschen der Quelle.
    info, old_dump = with_newer_state(layout, tmp_path)
    newer = dump(layout.db_path)
    _across_file_systems(layout, info, monkeypatch)
    resume = _stop_when_unlinking(monkeypatch, info.path if stop_at == "copy" else layout.db_path)
    with pytest.raises(HardStop):
        premigrate.restore_copy(layout, info.name, keep_discarded=True, reason="rollback_requested", now=NOW)
    resume()
    assert bootstate.read_state(layout.data_dir)["revert"] and info.path.exists(), "das Journal steht, die Kopie ist noch da"
    replaced = [p for p in layout.restore_dir.iterdir() if p.name.startswith("replaced-")]
    assert len(replaced) == 1 and dump(replaced[0] / "lattice.db") == newer

    assert premigrate.finish_revert(layout) is True
    assert dump(layout.db_path) == old_dump and not info.path.exists()
    assert [p.name for p in layout.restore_dir.iterdir() if p.name.startswith("replaced-")] == [replaced[0].name]
    assert dump(replaced[0] / "lattice.db") == newer, "der neuere Stand liegt noch beiseite, nicht die Kopie darueber"
    assert (replaced[0] / "lattice.db-wal").read_bytes() == b"neue-wal"
    assert not os.path.lexists(str(layout.db_path) + "-wal") and not os.path.lexists(str(layout.db_path) + "-shm")
    assert "revert" not in bootstate.read_state(layout.data_dir)


def test_a_sidecar_that_is_already_set_aside_is_never_replaced(layout, tmp_path, monkeypatch):
    # Abbruch nach dem fertigen Beiseitelegen der WAL, vor dem Loeschen ihrer Quelle: Der naechste Lauf loescht nur noch die
    # Quelle (nie darueber kopieren) und legt danach die Datenbank beiseite.
    info, old_dump = with_newer_state(layout, tmp_path)
    newer = dump(layout.db_path)
    _across_file_systems(layout, info, monkeypatch)
    wal = Path(str(layout.db_path) + "-wal")
    resume = _stop_when_unlinking(monkeypatch, wal)
    with pytest.raises(HardStop):
        premigrate.restore_copy(layout, info.name, keep_discarded=True, reason="rollback_requested", now=NOW)
    resume()
    (replaced,) = [p for p in layout.restore_dir.iterdir() if p.name.startswith("replaced-")]
    wal.write_bytes(b"darf-nicht-darueber")
    assert premigrate.finish_revert(layout) is True
    assert (replaced / "lattice.db-wal").read_bytes() == b"neue-wal"
    assert dump(replaced / "lattice.db") == newer and dump(layout.db_path) == old_dump
    assert not wal.exists()


def test_finish_revert_without_a_journal_does_nothing(layout):
    assert premigrate.finish_revert(layout) is False


def test_revert_across_file_systems_copies_via_a_neighbour_file(layout, tmp_path, monkeypatch):
    info, old_dump = with_newer_state(layout, tmp_path)
    real = os.replace
    calls = {"n": 0}

    def replace(src, dst):
        if Path(dst) == layout.db_path and not calls["n"]:  # das Umbenennen der Kopie ueber die Dateisystemgrenze
            calls["n"] += 1
            raise OSError(18, "Invalid cross-device link")
        real(src, dst)

    monkeypatch.setattr(premigrate.os, "replace", replace)
    premigrate.restore_copy(layout, info.name, keep_discarded=False, reason="x", now=NOW)
    assert calls["n"] == 1 and dump(layout.db_path) == old_dump
    assert not info.path.exists() and not (layout.data_dir / ".lattice.db.reverting").exists()
