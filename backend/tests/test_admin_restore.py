"""`python -m nodvard_deck.admin restore-backup <datei>`: Sicherung pruefen und vormerken, ohne Oberflaeche."""

from __future__ import annotations

import io
import sys
import time

import pytest
from nodvard_deck import admin, boot
from nodvard_deck.core.backup import restore
from restore_helpers import PASSWORD, REPO_ROOT, fill_live, make_backup, make_db, make_layout, settings_for, snapshot_tree


@pytest.fixture(autouse=True)
def _no_privilege_switch(monkeypatch):
    monkeypatch.setattr(admin, "drop_root_to_data_owner", lambda settings: None)
    monkeypatch.chdir(REPO_ROOT)


@pytest.fixture
def env(tmp_path):
    layout = make_layout(tmp_path)
    return layout, settings_for(layout), tmp_path


def good_backup(tmp_path, name="cli.ndbak"):
    db = make_db(tmp_path / f"{name}.db", owner="clara", users=2, hosts=5)
    return make_backup(tmp_path / name, db=db, files={"master.key": b"MASTER-CLI"})


def stdin(monkeypatch, text: str, tty: bool = False):
    stream = io.StringIO(text)
    stream.isatty = lambda: tty  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdin", stream)


def test_schedules_with_yes_and_password_from_stdin(env, monkeypatch, capsys):
    layout, settings, tmp_path = env
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir)
    stdin(monkeypatch, PASSWORD + "\n")
    assert admin.main(["restore-backup", str(good_backup(tmp_path)), "--yes"], settings) == 0
    out = capsys.readouterr().out
    assert "clara" in out and "5" in out and "age prüft nicht" in out and "ersetzt ALLES" in out
    assert "Vorgemerkt" in out and "neu" in out
    assert PASSWORD not in out
    pending = restore.read_pending(layout)
    assert pending["source"] == "cli" and pending["actor"]["label"].startswith("admin")
    assert snapshot_tree(layout.data_dir) == before, "bis zum Neustart aendert sich nichts"
    # Neustart: boot spielt ein
    monkeypatch.setattr(boot, "get_settings", lambda: settings)
    monkeypatch.setattr(boot.migrate, "main", lambda: None)
    assert boot.main() == 0
    assert restore.inspect_db(layout.db_path).owner_name == "clara" and layout.master_key.read_bytes() == b"MASTER-CLI"
    result = restore.read_result(layout)
    assert result["ok"] and result["source"] == "cli"


def test_interactive_confirmation(env, monkeypatch, capsys):
    layout, settings, tmp_path = env
    path = good_backup(tmp_path)
    stdin(monkeypatch, "", tty=True)
    monkeypatch.setattr(admin.getpass, "getpass", lambda prompt: PASSWORD)
    monkeypatch.setattr("builtins.input", lambda prompt: "nein")
    assert admin.main(["restore-backup", str(path)], settings) == 1
    assert "Abgebrochen" in capsys.readouterr().err and not restore.pending_exists(layout) and restore.list_ids(layout) == []
    monkeypatch.setattr("builtins.input", lambda prompt: "ja")
    assert admin.main(["restore-backup", str(path)], settings) == 0
    assert restore.pending_exists(layout)


def test_wrong_password_leaves_nothing(env, monkeypatch, capsys):
    layout, settings, tmp_path = env
    stdin(monkeypatch, "falsches-passwort-123\n")
    assert admin.main(["restore-backup", str(good_backup(tmp_path)), "--yes"], settings) == 1
    err = capsys.readouterr().err
    assert "passt nicht" in err and "falsches-passwort-123" not in err
    assert not restore.pending_exists(layout) and restore.list_ids(layout) == []


def test_without_a_terminal_it_insists_on_yes(env, monkeypatch, capsys):
    layout, settings, tmp_path = env
    stdin(monkeypatch, PASSWORD + "\n")
    assert admin.main(["restore-backup", str(good_backup(tmp_path))], settings) == 1
    assert "--yes" in capsys.readouterr().err and not restore.pending_exists(layout) and restore.list_ids(layout) == []


@pytest.mark.parametrize("case", ["fehlt", "kein-backup", "schon-vorgemerkt", "kein-sqlite"])
def test_refusals(env, monkeypatch, capsys, case):
    layout, settings, tmp_path = env
    stdin(monkeypatch, PASSWORD + "\n")
    path = good_backup(tmp_path)
    if case == "fehlt":
        path = tmp_path / "gibt-es-nicht.ndbak"
    elif case == "kein-backup":
        path = tmp_path / "text.ndbak"
        path.write_text("hallo")
    elif case == "schon-vorgemerkt":
        restore.make_private_dir(layout.restore_dir)
        restore.write_json_atomic(layout.restore_dir / restore.PENDING_NAME, {"x": 1})
    elif case == "kein-sqlite":
        settings = settings.model_copy(update={"database_url": "postgresql+asyncpg://x/y"})
    assert admin.main(["restore-backup", str(path), "--yes"], settings) == 1
    assert capsys.readouterr().err.startswith("Fehler:")
    assert restore.read_json(layout.restore_dir / restore.PENDING_NAME) in (None, {"x": 1})


def test_expired_by_the_time_of_the_restart_is_not_applied(env, monkeypatch):
    layout, settings, tmp_path = env
    fill_live(layout, "alt")
    stdin(monkeypatch, PASSWORD + "\n")
    assert admin.main(["restore-backup", str(good_backup(tmp_path)), "--yes"], settings) == 0
    restore.sweep(layout, now=time.time() + restore.PENDING_TTL_S + 5)
    assert not restore.pending_exists(layout)


def _running_upload(layout):
    """Ein Upload, den die laufende Anwendung gerade bearbeitet (frisch, mit Metadaten)."""
    rid = restore.new_id()
    restore.make_private_dir(layout.restore_dir)
    folder = layout.restore_dir / rid
    folder.mkdir()
    (folder / restore.UPLOAD_NAME).write_bytes(b"halber upload")
    restore.write_meta(layout, rid, {"id": rid, "scope": "owner", "created_at": time.time(), "state": "uploading"})
    return folder


def test_while_the_application_runs_a_running_upload_of_the_application_is_not_deleted(env, monkeypatch):
    from nodvard_deck.core import bootstate

    layout, settings, tmp_path = env
    fill_live(layout, "alt")
    upload = _running_upload(layout)
    lock = bootstate.acquire_lock(layout.data_dir, purpose="app")  # die Anwendung laeuft (im selben Container: `compose exec`)
    try:
        stdin(monkeypatch, PASSWORD + "\n")
        assert admin.main(["restore-backup", str(good_backup(tmp_path)), "--yes"], settings) == 0
    finally:
        lock.release()
    assert upload.is_dir() and (upload / restore.UPLOAD_NAME).read_bytes() == b"halber upload"
    assert restore.pending_exists(layout), "vorgemerkt wird trotzdem"


def test_while_the_application_runs_only_expired_leftovers_are_swept(env, monkeypatch):
    from nodvard_deck.core import bootstate

    layout, settings, tmp_path = env
    fill_live(layout, "alt")
    old = _running_upload(layout)
    restore.write_meta(layout, old.name, {"id": old.name, "scope": "owner", "created_at": time.time() - restore.STAGING_TTL_S - 100, "state": "uploaded"})
    lock = bootstate.acquire_lock(layout.data_dir, purpose="app")
    try:
        stdin(monkeypatch, PASSWORD + "\n")
        assert admin.main(["restore-backup", str(good_backup(tmp_path)), "--yes"], settings) == 0
    finally:
        lock.release()
    assert not old.exists(), "was laengst abgelaufen ist, geht auch dann weg"


def test_without_a_running_application_old_leftovers_are_cleaned_up_as_before(env, monkeypatch):
    layout, settings, tmp_path = env
    fill_live(layout, "alt")
    leftover = _running_upload(layout)
    stdin(monkeypatch, PASSWORD + "\n")
    assert admin.main(["restore-backup", str(good_backup(tmp_path)), "--yes"], settings) == 0
    assert not leftover.exists()
