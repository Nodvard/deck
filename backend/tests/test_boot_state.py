"""`core/bootstate.py`: Zustand des Starts (`data/.boot/`) -- state.json, Sperrdatei, Rueckweg-Vormerkung
und die Bereinigung von Fehlerprotokollen."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from nodvard_deck.core import bootstate

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# state.json
# ---------------------------------------------------------------------------


def test_missing_state_reads_as_empty(tmp_path):
    assert bootstate.read_state(tmp_path) == {}


def test_state_roundtrip_is_private_and_atomic(tmp_path):
    bootstate.write_state(tmp_path, {"app_version": "0.6.0", "started_ok": False, "db_heads": ["b", "a"]})
    state = bootstate.read_state(tmp_path)
    assert state["app_version"] == "0.6.0" and state["started_ok"] is False and state["db_heads"] == ["a", "b"]
    path = tmp_path / ".boot" / "state.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert [p.name for p in path.parent.iterdir() if p.name != "state.json"] == [], "keine Reste einer Temp-Datei"


@pytest.mark.parametrize("garbage", [b"", b"nicht json", b"[1, 2]", b'"text"', b"null", b"\xff\xfe", b"{" * 100_000])
def test_garbage_state_reads_as_empty(tmp_path, garbage):
    (tmp_path / ".boot").mkdir()
    (tmp_path / ".boot" / "state.json").write_bytes(garbage)
    assert bootstate.read_state(tmp_path) == {}


def test_state_drops_unknown_keys_and_wrong_types(tmp_path):
    (tmp_path / ".boot").mkdir()
    (tmp_path / ".boot" / "state.json").write_text(json.dumps({
        "version": 1, "app_version": 12, "started_ok": "ja", "db_heads": ["a", 5, None],
        "evil": {"x": 1},
        "last_migration": {"from_version": "0.5.0", "to_version": "0.6.0", "from_heads": ["a"], "to_heads": ["b"],
                           "copy": "../../etc/passwd", "state": "ok", "at": "2026-10-01T03:00:00Z"},
        "failure": {"kind": "kaputt", "reason": "x" * 5000, "log": ["a"] * 500},
    }))
    state = bootstate.read_state(tmp_path)
    assert "evil" not in state
    assert "app_version" not in state and "started_ok" not in state
    assert state["db_heads"] == ["a"]
    assert state["last_migration"]["copy"] is None, "ein Name mit Pfad wird nie uebernommen"
    assert state["last_migration"]["state"] == "ok"
    assert state["failure"]["kind"] == "unexpected", "unbekannte Arten werden zu `unexpected`"
    assert len(state["failure"]["reason"]) <= 1000 and len(state["failure"]["log"]) <= 50


def test_symlinked_state_is_not_followed(tmp_path):
    target = tmp_path / "geheim.json"
    target.write_text(json.dumps({"app_version": "SOLL-NICHT-GELESEN-WERDEN"}))
    (tmp_path / ".boot").mkdir()
    (tmp_path / ".boot" / "state.json").symlink_to(target)
    assert bootstate.read_state(tmp_path) == {}


def test_update_state_merges(tmp_path):
    bootstate.write_state(tmp_path, {"app_version": "0.5.0", "started_ok": True})
    bootstate.update_state(tmp_path, started_ok=False, db_heads=["x"])
    state = bootstate.read_state(tmp_path)
    assert state["app_version"] == "0.5.0" and state["started_ok"] is False and state["db_heads"] == ["x"]
    bootstate.update_state(tmp_path, failure=None)
    assert "failure" not in bootstate.read_state(tmp_path)


def test_mark_started_ok_only_touches_an_existing_state(tmp_path):
    assert bootstate.mark_started_ok(tmp_path) is False
    assert not (tmp_path / ".boot").exists(), "ohne vorherigen Start (Entwicklung, Tests) entsteht nichts"
    bootstate.write_state(tmp_path, {"app_version": "0.6.0", "started_ok": False})
    assert bootstate.mark_started_ok(tmp_path, now=1_700_000_000) is True
    state = bootstate.read_state(tmp_path)
    assert state["started_ok"] is True and state["started_at"] == "2023-11-14T22:13:20Z"


# ---------------------------------------------------------------------------
# Sperre
# ---------------------------------------------------------------------------


def test_lock_is_exclusive_and_released(tmp_path):
    first = bootstate.acquire_lock(tmp_path, purpose="app")
    assert first.held
    with pytest.raises(bootstate.LockHeld):
        bootstate.acquire_lock(tmp_path, purpose="boot")
    assert bootstate.is_locked(tmp_path) is True
    first.release()
    assert bootstate.is_locked(tmp_path) is False
    second = bootstate.acquire_lock(tmp_path, purpose="boot")
    assert second.held
    second.release()
    second.release()  # doppelt ist harmlos


def test_lock_wait_gives_up_after_the_time(tmp_path):
    first = bootstate.acquire_lock(tmp_path)
    started = time.monotonic()
    with pytest.raises(bootstate.LockHeld):
        bootstate.acquire_lock(tmp_path, wait_s=0.5)
    assert 0.4 <= time.monotonic() - started < 3
    first.release()


def test_lock_wait_gets_the_lock_when_it_is_released_meanwhile(tmp_path):
    import threading

    first = bootstate.acquire_lock(tmp_path)
    threading.Timer(0.3, first.release).start()
    second = bootstate.acquire_lock(tmp_path, wait_s=5)
    assert second.held
    second.release()


@pytest.mark.skipif(sys.platform == "win32", reason="flock")
def test_lock_is_held_by_another_process_and_dies_with_it(tmp_path):
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(REPO / 'backend' / 'src')!r})
        from pathlib import Path
        from nodvard_deck.core import bootstate
        lock = bootstate.acquire_lock(Path({str(tmp_path)!r}), purpose="app")
        print("held", flush=True)
        time.sleep(60)
    """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                            env={**os.environ, "PYTHONPATH": str(REPO / "sdk" / "python")})
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(bootstate.LockHeld):
            bootstate.acquire_lock(tmp_path)
        assert bootstate.is_locked(tmp_path) is True
    finally:
        proc.kill()
        proc.wait()
    assert bootstate.is_locked(tmp_path) is False, "stirbt der Halter, ist die Sperre frei"


def test_lock_that_cannot_be_created_is_a_no_op_not_an_error(tmp_path):
    blocker = tmp_path / "daten"
    blocker.write_text("ich bin eine Datei, kein Ordner")
    lock = bootstate.acquire_lock(blocker)
    assert not lock.held, "ohne Sperre weitermachen statt abzustuerzen"
    lock.release()
    assert bootstate.is_locked(blocker) is False


def test_lock_file_is_private(tmp_path):
    lock = bootstate.acquire_lock(tmp_path, purpose="boot")
    try:
        assert stat.S_IMODE((tmp_path / ".boot" / "app.lock").stat().st_mode) == 0o600
        assert (tmp_path / ".boot" / "app.lock").read_text().split()[1] == "boot"
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Rueckweg-Vormerkung
# ---------------------------------------------------------------------------


COPY = "20261001T030000Z_0.5.0_0.6.0.db"


def test_rollback_request_roundtrip_and_expiry(tmp_path):
    assert bootstate.read_rollback(tmp_path) is None
    bootstate.request_rollback(tmp_path, COPY, by="notseite", now=1000.0)
    request = bootstate.read_rollback(tmp_path, now=1000.0 + 60)
    assert request == {"copy": COPY, "by": "notseite", "requested_at": 1000.0}
    assert bootstate.read_rollback(tmp_path, now=1000.0 + bootstate.ROLLBACK_TTL_S + 1) is None
    bootstate.clear_rollback(tmp_path)
    assert bootstate.read_rollback(tmp_path, now=1000.0) is None
    bootstate.clear_rollback(tmp_path)  # nichts da: kein Fehler


def test_rollback_request_from_the_future_or_with_a_bad_name_is_ignored(tmp_path):
    (tmp_path / ".boot").mkdir()
    path = tmp_path / ".boot" / "rollback.json"
    for payload in (
        {"copy": COPY, "by": "x", "requested_at": 5000.0 + 999_999},
        {"copy": "../" + COPY, "by": "x", "requested_at": 5000.0},
        {"copy": "kopie.txt", "by": "x", "requested_at": 5000.0},
        {"copy": COPY, "by": "x"},
        ["x"],
    ):
        path.write_text(json.dumps(payload))
        assert bootstate.read_rollback(tmp_path, now=5000.0) is None, payload
    with pytest.raises(ValueError):
        bootstate.request_rollback(tmp_path, "../bad.db", by="x")


def test_rescue_code_file_is_cleared(tmp_path):
    (tmp_path / ".boot").mkdir()
    (tmp_path / ".boot" / "rescue_code.txt").write_text("ABCD-EFGH-JKLM\n")
    assert bootstate.clear_rescue_code(tmp_path) is True
    assert bootstate.clear_rescue_code(tmp_path) is False


# ---------------------------------------------------------------------------
# Bereinigung des Fehlerprotokolls
# ---------------------------------------------------------------------------


def test_sanitize_removes_paths_secrets_and_sql_parameters(tmp_path):
    data = tmp_path / "daten"
    raw = [
        f'  File "{data}/ext/dokumente/geheim.py", line 3, in run',
        '  File "/usr/local/lib/python3.12/site-packages/alembic/runtime/migration.py", line 622, in run_migrations',
        "sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) UNIQUE constraint failed: users.username",
        "[SQL: INSERT INTO users (username, password_hash) VALUES (?, ?)]",
        "[parameters: ('nico', '$argon2id$v=19$m=65536,t=3,p=1$c29tZXNhbHQ$RdescudvJCsgt3ub+b+dWRWJTmaaJObG')]",
        "connect to postgresql://admin:Sup3rGeheim@10.0.0.5:5432/deck failed",
        "NODVARD_DECK_JWT_SECRET=hunter2hunter2 war gesetzt",
        "Authorization: Bearer abcdef0123456789abcdef0123456789abcdef0123456789",
        'token="x-y-z" und password: meinpasswort',
        "Fernet-Schluessel gAAAAABk" + "A" * 60,
        "C:\\Users\\nico\\lattice\\data\\lattice.db nicht gefunden",
    ]
    out = bootstate.sanitize_lines(raw, data_dir=data)
    text = "\n".join(out)
    for secret in ("Sup3rGeheim", "hunter2hunter2", "abcdef0123456789abcdef", "meinpasswort", "argon2id", "x-y-z", "nico", "gAAAAABk", str(data)):
        assert secret not in text, secret
    assert "<Daten>" in text
    assert "migration.py" in text, "der Dateiname bleibt fuer die Fehlersuche"
    assert "site-packages/alembic" not in text and "/usr/local" not in text
    assert "UNIQUE constraint failed: users.username" in text, "die eigentliche Fehlermeldung bleibt"
    assert "10.0.0.5" in text


def test_sanitize_limits_lines_and_length_and_strips_control_characters():
    raw = [f"zeile {i} " + "x" * 1000 + "\x1b[31mrot\x1b[0m\x00\x07" for i in range(500)]
    out = bootstate.sanitize_lines(raw, data_dir=Path("/nirgends"))
    assert len(out) == 50 and out[-1].startswith("zeile 499"), "die LETZTEN 50 Zeilen"
    assert all(len(line) <= 300 for line in out)
    assert all("\x1b" not in line and "\x00" not in line and "\x07" not in line for line in out)


def test_sanitize_splits_multiline_entries_and_drops_blank_lines():
    out = bootstate.sanitize_lines(["a\nb\n\n\nc", "", "   ", "d"], data_dir=Path("/x"))
    assert out == ["a", "b", "c", "d"]


def test_sanitize_text_is_idempotent():
    once = bootstate.sanitize_text("Pfad /app/data/lattice.db und password=abc", data_dir=Path("/app/data"))
    assert bootstate.sanitize_text(once, data_dir=Path("/app/data")) == once


def test_the_record_of_the_old_database_during_a_restore_is_kept_and_checked(tmp_path):
    record = {"at": "2026-10-01T03:00:00Z", "from_version": "0.5.0", "to_version": "0.6.0", "from_heads": ["a"], "to_heads": ["b"],
              "copy": None, "state": "running"}
    bootstate.write_state(tmp_path, {"pre_restore": {"id": "0123456789abcdef0123456789abcdef", "last_migration": record, "started_ok": False}})
    pre = bootstate.read_state(tmp_path)["pre_restore"]
    assert pre == {"id": "0123456789abcdef0123456789abcdef", "last_migration": record, "started_ok": False}
    for bad in ({"id": "../../x"}, {"id": 5}, "text", {"last_migration": record}):
        bootstate.write_state(tmp_path, {"pre_restore": bad})
        assert "pre_restore" not in bootstate.read_state(tmp_path)
    bootstate.write_state(tmp_path, {"pre_restore": {"id": "f" * 32, "last_migration": "kaputt", "started_ok": "ja"}})
    assert bootstate.read_state(tmp_path)["pre_restore"] == {"id": "f" * 32, "last_migration": None, "started_ok": None}
