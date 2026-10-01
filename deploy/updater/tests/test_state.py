"""`state.py`: eigener Zustand des Helfers in `/state` (Bauplan 2c-2, Ebene 2 der Teststrategie).

Echtes Dateisystem unter `tmp_path`: Rechte, Sperre (zweite Instanz), atomares Schreiben mit fsync, kaputte
Dateien -> definierter sicherer Zustand, Journal (Write-ahead, Schritte nur vorwaerts), Grenzen ueber Neustarts.
"""

from __future__ import annotations

import errno
import inspect
import json
import os
import stat
import subprocess
import sys
import textwrap

import pytest
from nodvard_deck_updater import policy, state
from nodvard_deck_updater.policy import Refusal
from nodvard_deck_updater.state import (
    Journal,
    NewImage,
    OldContainer,
    State,
    StateStore,
)
from updater_support import NOW, UPDATER_DIR, fake_owner, needs_root

RID = "6f1c2b0e-9a4d-4c1e-8f00-1b2c3d4e5f60"
RID2 = "0f1c2b0e-9a4d-4c1e-8f00-1b2c3d4e5f60"
CID_OLD = "a" * 64
CID_NEW = "b" * 64
IMG_OLD = "sha256:" + "1" * 64
IMG_NEW = "sha256:" + "2" * 64
DIGEST = "sha256:" + "3" * 64


@pytest.fixture
def state_dir(tmp_path):
    path = tmp_path / "state"
    path.mkdir()
    path.chmod(0o755)  # wie ein frisches Volume
    return path


@pytest.fixture
def store(state_dir, uid):
    s = StateStore(state_dir, expected_uid=uid)
    s.open()
    yield s
    s.close()


def mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def files(path) -> list[str]:
    return sorted(p.name for p in path.iterdir())


def make_slot(now=NOW) -> policy.Slot:
    return policy.new_slot(from_version="0.7.0", image_id=IMG_OLD, repo_digest="ghcr.io/nodvard/deck@" + DIGEST,
                           installed_container_id=CID_NEW, installed_image_id=IMG_NEW, now=now)


def make_request(action="update", version="0.7.1", rid=RID) -> policy.Request:
    return policy.Request(v=1, id=rid, action=action, version=version, created_at=NOW)


def make_journal(step="begin", action="update") -> Journal:
    old = OldContainer(id=CID_OLD, name="nodvard-deck-nodvard-deck-1", image_id=IMG_OLD, version="0.7.0",
                       restart_policy=("unless-stopped", 0), tag_text="ghcr.io/nodvard/deck:latest")
    journal = Journal(request_id=RID, action=action, step="begin", started_at=NOW, deadline=NOW + 900, old=old)
    journal.validate()
    if step == "begin":
        return journal
    new = NewImage(image_id=IMG_NEW, digest=DIGEST, version="0.7.1")
    for next_step in policy.STEPS[1:policy.STEPS.index(step) + 1]:
        if next_step == "created":
            new = NewImage(image_id=IMG_NEW, digest=DIGEST, version="0.7.1", id=CID_NEW)
        journal = journal.advance(next_step, new=new)
    return journal


# ---------------------------------------------------------------------------
# Ordner, Rechte, Sperre
# ---------------------------------------------------------------------------


def test_open_sets_private_modes_and_creates_lock(state_dir, uid):
    with StateStore(state_dir, expected_uid=uid):
        assert mode(state_dir) == 0o700
        assert mode(state_dir / "lock") == 0o600
        assert stat.S_ISREG(os.lstat(state_dir / "lock").st_mode)


def test_second_instance_gets_second_instance(state_dir, uid):
    first = StateStore(state_dir, expected_uid=uid)
    first.open()
    second = StateStore(state_dir, expected_uid=uid)
    with pytest.raises(Refusal) as info:
        second.open()
    assert info.value.code == "second_instance"
    first.close()
    second.open()  # nach dem Ende der ersten Instanz geht es
    second.close()


def test_second_instance_in_another_process(state_dir, uid):
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(UPDATER_DIR)!r})
        from nodvard_deck_updater.state import StateStore
        from nodvard_deck_updater.policy import Refusal
        try:
            StateStore({str(state_dir)!r}, expected_uid={uid}).open()
        except Refusal as exc:
            print(exc.code)
        else:
            print("offen")
    """)
    with StateStore(state_dir, expected_uid=uid):
        out = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60, check=False)
        assert out.stdout.strip() == "second_instance", out.stderr
    out = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60, check=False)
    assert out.stdout.strip() == "offen", out.stderr


def test_lock_ends_with_the_process_even_without_close(state_dir, uid):
    script = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(UPDATER_DIR)!r})
        from nodvard_deck_updater.state import StateStore
        StateStore({str(state_dir)!r}, expected_uid={uid}).open()
        os._exit(3)  # Absturz ohne Aufraeumen
    """)
    out = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, timeout=60, check=False)
    assert out.returncode == 3
    with StateStore(state_dir, expected_uid=uid):
        pass


def test_wrong_owner_is_state_unsafe(state_dir, uid):
    with pytest.raises(Refusal) as info:
        StateStore(state_dir, expected_uid=uid + 1).open()
    assert (info.value.code, info.value.detail) == ("state_unsafe", "dir_owner")
    assert mode(state_dir) == 0o755, "an einem fremden Ordner wird nichts geaendert"


@needs_root
def test_state_dir_owned_by_dashboard_user_is_unsafe(state_dir):
    os.chown(state_dir, 1000, 1000)
    with pytest.raises(Refusal) as info:
        StateStore(state_dir).open()
    assert info.value.code == "state_unsafe"


def test_symlink_missing_or_file_is_state_unsafe(tmp_path, state_dir, uid):
    link = tmp_path / "link"
    link.symlink_to(state_dir)
    a_file = tmp_path / "file"
    a_file.write_text("x")
    for path, detail in ((link, "dir_open"), (tmp_path / "fehlt", "dir_open"), (a_file, "dir_open")):
        with pytest.raises(Refusal) as info:
            StateStore(path, expected_uid=uid).open()
        assert (info.value.code, info.value.detail) == ("state_unsafe", detail)
    assert mode(state_dir) == 0o755


def test_lock_as_symlink_or_hardlink_is_unsafe(state_dir, tmp_path, uid):
    victim = tmp_path / "victim"
    victim.write_text("x")
    victim.chmod(0o644)
    (state_dir / "lock").symlink_to(victim)
    with pytest.raises(Refusal) as info:
        StateStore(state_dir, expected_uid=uid).open()
    assert (info.value.code, info.value.detail) == ("state_unsafe", "lock_open")
    (state_dir / "lock").unlink()
    os.link(victim, state_dir / "lock")
    with pytest.raises(Refusal) as info:
        StateStore(state_dir, expected_uid=uid).open()
    assert (info.value.code, info.value.detail) == ("state_unsafe", "lock_file")
    assert victim.read_text() == "x" and mode(victim) == 0o644, "das Ziel bleibt unveraendert"


def test_production_defaults_are_the_strict_ones():
    # Testbarkeit ohne root nur ueber den Konstruktor (nie ueber die Umgebung); im Betrieb gelten die Vorgaben.
    params = inspect.signature(StateStore.__init__).parameters
    assert params["path"].default == "/state"
    assert params["expected_uid"].default == 0 and params["expected_uid"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["repository"].default == policy.REPOSITORY


def test_expected_uid_must_be_a_plain_int(state_dir):
    for bad in (-1, "0", None, True, 0.0):
        with pytest.raises(ValueError):
            StateStore(state_dir, expected_uid=bad)


def test_not_opened_store_refuses_to_work(state_dir, uid):
    s = StateStore(state_dir, expected_uid=uid)
    with pytest.raises(RuntimeError):
        s.load_state(NOW)


def test_stale_tmp_files_are_removed_on_open(state_dir, uid):
    (state_dir / ".state-0123456789abcdef.tmp").write_text("halb")
    (state_dir / ".journal-0123456789abcdef.tmp").write_text("halb")
    (state_dir / "notizen.txt").write_text("bleibt")
    with StateStore(state_dir, expected_uid=uid):
        assert files(state_dir) == ["lock", "notizen.txt"]


# ---------------------------------------------------------------------------
# state.json
# ---------------------------------------------------------------------------


def test_missing_state_is_empty_and_not_a_problem(store):
    loaded = store.load_state(NOW)
    assert loaded == State() and store.problem is None


def test_state_roundtrip_is_private_and_leaves_no_tmp(store, state_dir):
    s = State(slot=make_slot(), actions=[NOW - 10], blocked={"0.7.2": NOW + 100}, seen={RID: NOW - 5})
    store.save_state(s)
    assert store.load_state(NOW) == s
    assert mode(state_dir / "state.json") == 0o600
    assert files(state_dir) == ["lock", "state.json"]


def test_save_fsyncs_file_and_directory(store, monkeypatch):
    kinds = []
    real_fsync = os.fsync

    def spy(fd):
        kinds.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    store.save_state(State())
    assert kinds == ["file", "dir"]


def test_failed_replace_keeps_old_state_and_leaves_no_tmp(store, state_dir, monkeypatch):
    store.save_state(State(actions=[NOW]))

    def broken_replace(*args, **kwargs):
        raise OSError(28, "kein Platz")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        store.save_state(State())
    monkeypatch.undo()
    assert store.load_state(NOW).actions == [NOW]
    assert files(state_dir) == ["lock", "state.json"]


def test_never_writes_a_state_it_could_not_read_back(store, state_dir):
    with pytest.raises(ValueError):
        store.save_state(State(seen={"keine-uuid": NOW}))
    with pytest.raises(ValueError):
        store.save_state(State(actions=[True]))
    assert not (state_dir / "state.json").exists()


GOOD = State(slot=make_slot(), actions=[NOW], blocked={"0.7.2": NOW + 9}, seen={RID: NOW}).to_json()

CORRUPT = {
    "leer": b"",
    "kein JSON": b"kaputt",
    "abgeschnitten": json.dumps(GOOD).encode()[:-5],
    "Liste": b"[]",
    "null": b"null",
    "kein UTF-8": b"\xff\xfe{}",
    "100000-fach verschachtelt": b"[" * 100_000 + b"]" * 100_000,
    "doppelter Schluessel": json.dumps(GOOD).encode()[:-1] + b',"seen":{}}',
    "Zusatzfeld": json.dumps({**GOOD, "x": 1}).encode(),
    "fehlendes Feld": json.dumps({k: v for k, v in GOOD.items() if k != "seen"}).encode(),
    "format 2": json.dumps({**GOOD, "format": 2}).encode(),
    "format true": json.dumps({**GOOD, "format": True}).encode(),
    "Aktion als Text": json.dumps({**GOOD, "actions": ["1"]}).encode(),
    "Aktion true": json.dumps({**GOOD, "actions": [True]}).encode(),
    "Aktion negativ": json.dumps({**GOOD, "actions": [-1]}).encode(),
    "Sperre fuer keine Version": json.dumps({**GOOD, "blocked": {"latest": NOW}}).encode(),
    "ID gross": json.dumps({**GOOD, "seen": {RID.upper(): NOW}}).encode(),
    "zu viele IDs": json.dumps({**GOOD, "seen": {f"{i:08x}-0000-4000-8000-000000000000": NOW for i in range(1025)}}).encode(),
    "zu viele Aktionen": json.dumps({**GOOD, "actions": [NOW] * (state.MAX_ACTIONS + 1)}).encode(),
    "zu viele Sperren": json.dumps({**GOOD, "blocked": {f"1.0.{i}": NOW for i in range(state.MAX_BLOCKED + 1)}}).encode(),
    "kaputter Slot": json.dumps({**GOOD, "slot": {**GOOD["slot"], "repo_digest": "docker.io/x@" + DIGEST}}).encode(),
    "hold_until Text": json.dumps({**GOOD, "hold_until": "morgen"}).encode(),
    "NaN": json.dumps(GOOD).encode().replace(b'"hold_until": null', b'"hold_until": NaN'),
}
assert b"NaN" in CORRUPT["NaN"]


@pytest.mark.parametrize("raw", list(CORRUPT.values()), ids=list(CORRUPT))
def test_corrupt_state_becomes_a_defined_safe_state(store, state_dir, raw):
    (state_dir / "state.json").write_bytes(raw)
    loaded = store.load_state(NOW)
    assert store.problem == "state_corrupt"
    assert loaded == State(hold_until=NOW + 24 * 3600), "kein Slot, keine Aktionen fuer 24 h"
    assert (state_dir / "state.json.broken").read_bytes() == raw, "der kaputte Inhalt bleibt zur Ansicht"
    assert mode(state_dir / "state.json.broken") == 0o600
    # Der sichere Zustand ist sofort gespeichert: ein Neustart setzt die Sperre nicht zurueck.
    store.close()
    store.open()
    assert store.load_state(NOW + 60) == State(hold_until=NOW + 24 * 3600) and store.problem is None
    # Bis dahin wird jede Anforderung abgelehnt, danach geht es normal weiter (ohne Rueckweg).
    with pytest.raises(Refusal) as info:
        store.check(loaded, make_request(), NOW + 24 * 3600 - 1)
    assert info.value.code == "state_unsafe"
    store.check(loaded, make_request(), NOW + 24 * 3600)


def test_unsafe_state_files_are_corrupt_too(store, state_dir, tmp_path):
    victim = tmp_path / "victim.json"
    victim.write_text(json.dumps(GOOD))
    for make in (
        lambda p: p.symlink_to(victim),                       # Symlink
        lambda p: os.link(victim, p),                          # Hardlink
        lambda p: p.mkdir(),                                   # Ordner
        lambda p: os.mkfifo(p),                                # FIFO (blockiert nicht)
        lambda p: p.write_bytes(b" " * (256 * 1024 + 1)),      # zu gross
    ):
        target = state_dir / "state.json"
        make(target)
        assert store.load_state(NOW) == State(hold_until=NOW + 24 * 3600)
        assert store.problem == "state_corrupt"
        assert stat.S_ISREG(os.lstat(target).st_mode)
        assert json.loads(victim.read_text()) == GOOD, "das Ziel eines Links wird nie veraendert"
        target.unlink()
        broken = state_dir / "state.json.broken"
        if broken.is_dir():
            broken.rmdir()
        elif broken.exists() or broken.is_symlink():
            broken.unlink()


def test_sparse_state_file_is_not_read(store, state_dir):
    with open(state_dir / "state.json", "wb") as fh:
        fh.truncate(1024**3)  # 1 GiB, duenn belegt
    assert store.load_state(NOW).hold_until == NOW + 24 * 3600
    assert not (state_dir / "state.json.broken").exists(), "zu grosse Dateien werden nicht kopiert"


@needs_root
def test_state_file_of_another_owner_is_corrupt(store, state_dir):
    (state_dir / "state.json").write_text(json.dumps(GOOD))
    os.chown(state_dir / "state.json", 1000, 1000)
    assert store.load_state(NOW).hold_until == NOW + 24 * 3600


def test_hold_is_extended_not_shortened(store):
    store.save_state(State(hold_until=NOW + 24 * 3600 + 30))
    assert store.hold(NOW).hold_until == NOW + 24 * 3600 + 30
    store.save_state(State(hold_until=NOW + 5))
    assert store.hold(NOW).hold_until == NOW + 24 * 3600


# ---------------------------------------------------------------------------
# Lesefehler sind keine kaputten Dateien (Fund 2), der Notweg laesst es gesperrt (Fund 3), die Sperre geht nicht
# verloren (Fund 4)
# ---------------------------------------------------------------------------

TRANSIENT = [errno.EMFILE, errno.ENFILE, errno.ENOMEM, errno.EIO, errno.ESTALE, errno.EAGAIN]


def fail_reads(monkeypatch, name, err, *, times=1):
    """`times`-mal scheitert das Oeffnen von `name` (zum Lesen) mit `err`; danach geht es wieder."""
    real_open = os.open
    left = [times]

    def flaky(path, flags, *args, **kwargs):
        if path == name and not flags & os.O_CREAT and left[0] > 0:
            left[0] -= 1
            raise OSError(err, os.strerror(err))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", flaky)


def fail_once(monkeypatch, target, err):
    """Der naechste Aufruf von `os.<target>` scheitert mit `err`; danach geht es wieder."""
    real = getattr(os, target)
    left = [1]

    def flaky(*args, **kwargs):
        if left[0] > 0:
            left[0] -= 1
            raise OSError(err, os.strerror(err))
        return real(*args, **kwargs)

    monkeypatch.setattr(os, target, flaky)


def unsafe_detail(func, *args) -> str | None:
    with pytest.raises(Refusal) as info:
        func(*args)
    assert info.value.code == "state_unsafe"
    return info.value.detail


@pytest.mark.parametrize("err", TRANSIENT, ids=errno.errorcode.get)
def test_transient_open_error_does_not_quarantine_a_valid_journal(store, state_dir, monkeypatch, err):
    journal = make_journal("old_stopped")  # alter Container steht, neuer noch nicht gestartet: das Dashboard ist aus
    store.write_journal(journal)
    before = (state_dir / "journal.json").read_bytes()
    fail_reads(monkeypatch, "journal.json", err)
    assert unsafe_detail(store.load_journal, NOW) == "journal_read"
    assert store.problem is None
    assert files(state_dir) == ["journal.json", "lock"], "kein .broken, keine Sperre geschrieben"
    assert (state_dir / "journal.json").read_bytes() == before
    assert store.load_journal(NOW + 5) == journal, "der naechste Versuch liest das Journal ganz normal"
    assert store.load_state(NOW + 5) == State(), "und es gibt keine 24-h-Sperre"


@pytest.mark.parametrize("target", ["fstat", "read"])
def test_transient_fstat_or_read_error_does_not_quarantine_a_valid_journal(store, state_dir, monkeypatch, target):
    journal = make_journal("created")
    store.write_journal(journal)
    before = (state_dir / "journal.json").read_bytes()
    fail_once(monkeypatch, target, errno.EIO)
    assert unsafe_detail(store.load_journal, NOW) == "journal_read"
    assert files(state_dir) == ["journal.json", "lock"] and (state_dir / "journal.json").read_bytes() == before
    assert store.problem is None
    assert store.load_journal(NOW + 5) == journal


@pytest.mark.parametrize("err", TRANSIENT, ids=errno.errorcode.get)
def test_transient_open_error_does_not_replace_a_valid_state(store, state_dir, monkeypatch, err):
    valid = State(slot=make_slot(), actions=[NOW - 10], blocked={"0.7.2": NOW + 100}, seen={RID: NOW - 5})
    store.save_state(valid)
    before = (state_dir / "state.json").read_bytes()
    fail_reads(monkeypatch, "state.json", err)
    assert unsafe_detail(store.load_state, NOW) == "state_read"
    assert store.problem is None
    assert files(state_dir) == ["lock", "state.json"] and (state_dir / "state.json").read_bytes() == before
    assert store.load_state(NOW + 5) == valid, "der Slot ist noch da"


@pytest.mark.parametrize("target", ["fstat", "read"])
def test_transient_fstat_or_read_error_does_not_replace_a_valid_state(store, state_dir, monkeypatch, target):
    valid = State(slot=make_slot())
    store.save_state(valid)
    before = (state_dir / "state.json").read_bytes()
    fail_once(monkeypatch, target, errno.EIO)
    assert unsafe_detail(store.load_state, NOW) == "state_read"
    assert files(state_dir) == ["lock", "state.json"] and (state_dir / "state.json").read_bytes() == before
    assert store.load_state(NOW + 5) == valid


def test_read_and_write_errors_together_never_move_a_valid_state_aside(store, state_dir, monkeypatch):
    # Probe EMFILE: der Deskriptor-Vorrat ist leer, Lesen *und* Schreiben scheitern. Frueher wurde dabei sogar der
    # gueltige Zustand nach state.json.broken geschoben und nie ersetzt.
    valid = State(slot=make_slot())
    store.save_state(valid)
    real_open = os.open

    def exhausted(path, flags, *args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            raise OSError(errno.EMFILE, os.strerror(errno.EMFILE))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", exhausted)
    assert unsafe_detail(store.load_state, NOW) == "state_read"
    monkeypatch.undo()
    assert files(state_dir) == ["lock", "state.json"]
    assert store.load_state(NOW + 1) == valid


@pytest.mark.parametrize("name", ["state.json", "journal.json"])
@pytest.mark.parametrize("err", [errno.EACCES, errno.EPERM, errno.ENXIO, errno.ENODEV, errno.EINVAL])
def test_open_errors_about_the_file_itself_still_count_as_invalid(store, state_dir, monkeypatch, name, err):
    # Keine Rechte, kein Datei-Typ (Socket, Geraet): daran aendert ein zweiter Versuch nichts -> kaputt.
    if name == "state.json":
        store.save_state(State(slot=make_slot()))
    else:
        store.write_journal(make_journal("begin"))
    fail_reads(monkeypatch, name, err, times=99)
    if name == "state.json":
        assert store.load_state(NOW).hold_until == NOW + 24 * 3600 and store.problem == "state_corrupt"
    else:
        assert unsafe_detail(store.load_journal, NOW) == "journal_corrupt" and store.problem == "journal_corrupt"


def refuse_creating_files(monkeypatch, err=errno.ENOSPC):
    real_open = os.open

    def full(path, flags, *args, **kwargs):
        if flags & os.O_CREAT and kwargs.get("dir_fd") is not None:
            raise OSError(err, os.strerror(err))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", full)


@pytest.mark.parametrize("err", [errno.ENOSPC, errno.EMFILE, errno.EIO, errno.EROFS], ids=errno.errorcode.get)
def test_failed_safe_state_write_keeps_the_hold_after_a_restart(store, state_dir, monkeypatch, err):
    # Fund 3: kaputtes state.json, und das Schreiben des sicheren Zustands scheitert. Frueher wurde die Datei
    # trotzdem nach .broken geschoben -> beim naechsten Start gab es `State()` ohne Sperre.
    (state_dir / "state.json").write_bytes(b"kaputt")
    refuse_creating_files(monkeypatch, err)
    assert unsafe_detail(store.load_state, NOW) == "state_write"
    monkeypatch.undo()
    assert (state_dir / "state.json").read_bytes() == b"kaputt", "die kaputte Datei bleibt, wo sie ist"
    store.close()
    store.open()
    again = store.load_state(NOW + 60)
    assert again == State(hold_until=NOW + 60 + 24 * 3600) and store.problem == "state_corrupt"
    with pytest.raises(Refusal) as info:
        store.check(again, make_request(), NOW + 120)
    assert info.value.code == "state_unsafe"


def test_failed_dir_fsync_after_the_safe_state_was_written_does_not_move_it_aside(store, state_dir, monkeypatch):
    (state_dir / "state.json").write_bytes(b"kaputt")
    real_fsync = os.fsync

    def dir_fsync_fails(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "kein fsync")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", dir_fsync_fails)
    assert unsafe_detail(store.load_state, NOW) == "state_write"
    monkeypatch.undo()
    assert stat.S_ISREG(os.lstat(state_dir / "state.json").st_mode)
    assert (state_dir / "state.json.broken").read_bytes() == b"kaputt", "nur die kaputte Kopie liegt in .broken"
    assert store.load_state(NOW + 1) == State(hold_until=NOW + 24 * 3600), "der sichere Zustand liegt an seinem Platz"


def test_state_json_as_directory_and_failing_second_write_still_ends_up_held(store, state_dir, monkeypatch):
    # Der gedachte Notweg: state.json ist ein Ordner. Scheitert danach auch das zweite Speichern, fehlt state.json --
    # `state.json.broken` verraet, dass der Zustand schon kaputt war: beim naechsten Laden gilt der sichere Zustand.
    (state_dir / "state.json").mkdir()
    refuse_creating_files(monkeypatch)
    assert unsafe_detail(store.load_state, NOW) == "state_write"
    monkeypatch.undo()
    assert not (state_dir / "state.json").exists() and (state_dir / "state.json.broken").is_dir()
    store.close()
    store.open()
    again = store.load_state(NOW + 60)
    assert again == State(hold_until=NOW + 60 + 24 * 3600) and store.problem == "state_corrupt"
    assert stat.S_ISREG(os.lstat(state_dir / "state.json").st_mode)


def test_missing_state_is_a_clean_start_only_without_a_broken_leftover(store, state_dir):
    assert store.load_state(NOW) == State() and store.problem is None
    (state_dir / "state.json.broken").write_bytes(b"alt")
    assert store.load_state(NOW).hold_until == NOW + 24 * 3600 and store.problem == "state_corrupt"
    assert (state_dir / "state.json").exists()


def corrupt_journal_file(state_dir):
    (state_dir / "journal.json").write_bytes(b"{")


def test_hold_from_a_corrupt_journal_survives_saving_an_older_state(store, state_dir):
    # Fund 4: die Schleife haelt einen frueher geladenen State; load_journal findet ein kaputtes Journal und setzt die
    # Sperre. Speichert die Schleife danach ihren alten State, darf die Sperre nicht verschwinden.
    mem = store.load_state(NOW)
    corrupt_journal_file(state_dir)
    with pytest.raises(Refusal) as info:
        store.load_journal(NOW)
    assert info.value.state is not None and info.value.state.hold_until == NOW + 24 * 3600
    mem.mark_seen(RID, NOW)
    store.save_state(mem)
    assert mem.hold_until == NOW + 24 * 3600, "auch im uebergebenen Objekt"
    held = store.load_state(NOW + 1)
    assert held.hold_until == NOW + 24 * 3600 and held.seen == {RID: NOW}
    with pytest.raises(Refusal) as refused:
        store.check(held, make_request(rid=RID2), NOW + 2)
    assert refused.value.code == "state_unsafe"


def test_hold_floor_never_lowers_what_is_saved_and_ends_with_the_hold(store):
    store.hold(NOW)
    store.save_state(State(hold_until=NOW + 5))
    assert store.load_state(NOW).hold_until == NOW + 24 * 3600
    store.save_state(State(hold_until=NOW + 24 * 3600 + 30))
    assert store.load_state(NOW).hold_until == NOW + 24 * 3600 + 30, "was mehr sperrt, bleibt"
    later = NOW + 24 * 3600 + 1
    fresh = store.load_state(later)
    fresh.prune(later + 100)  # die Sperre ist abgelaufen und wird beim Aufraeumen entfernt
    store.save_state(fresh)
    assert store.load_state(later + 100).hold_until is None, "danach haelt der Store sie nicht mehr fest"


def test_floor_does_not_outlive_the_store_session(state_dir, uid):
    first = StateStore(state_dir, expected_uid=uid)
    first.open()
    first.hold(NOW)
    first.close()
    second = StateStore(state_dir, expected_uid=uid)
    second.open()
    assert second.load_state(NOW).hold_until == NOW + 24 * 3600, "die Sperre steht auf der Platte"
    second.close()


def test_check_with_a_state_loaded_before_the_journal_broke_still_sees_the_hold(store, state_dir):
    # Gegenpruefung: die Schleife laedt den State, danach erkennt `load_journal` ein kaputtes Journal und setzt die
    # Sperre. `check` am alten State (frueher `State.check`) sah sie nicht und nahm die Anforderung an.
    mem = store.load_state(NOW)
    corrupt_journal_file(state_dir)
    with pytest.raises(Refusal) as info:
        store.load_journal(NOW)
    assert info.value.detail == "journal_corrupt"
    assert mem.hold_until is None, "der alte State kennt die Sperre nicht"
    request = make_request(rid=RID2)
    assert refused(store, mem, request, NOW + 5) == "state_unsafe"
    assert mem.hold_until == NOW + 24 * 3600, "auch im uebergebenen Objekt: spaeteres save_state faellt nicht dahinter"
    assert refused(store, mem, request, NOW + 24 * 3600 - 1) == "state_unsafe"
    assert refused(store, mem, request, NOW + 24 * 3600) is None, "nach 24 h ist die Sperre vorbei"
    assert not hasattr(State, "check"), "kein Weg am Store vorbei"


def test_check_applies_the_hold_of_a_corrupt_state_file_to_an_older_state_too(store, state_dir):
    mem = store.load_state(NOW)
    (state_dir / "state.json").write_bytes(b"kaputt")
    assert store.load_state(NOW + 1).hold_until == NOW + 1 + 24 * 3600 and store.problem == "state_corrupt"
    assert refused(store, mem, make_request(rid=RID2), NOW + 2) == "state_unsafe"


def count_state_writes(monkeypatch) -> list[str]:
    writes: list[str] = []
    real = state.write_atomic

    def counting(dir_fd, name, *args, **kwargs):
        if name == "state.json":
            writes.append(name)
        return real(dir_fd, name, *args, **kwargs)

    monkeypatch.setattr(state, "write_atomic", counting)
    return writes


def test_clock_set_back_caps_the_hold_floor_too_and_loading_stops_writing(store, state_dir, monkeypatch):
    # Befund: kaputtes state.json -> Sperre bis NOW + 24 h; die Uhr wird um drei Tage zurueckgestellt. Die Sperre des
    # Speichers (`_hold_floor`) hob die Kappung von `hold_until` immer wieder auf: 96 h statt 24 h, und jedes
    # `load_state` schrieb die Datei neu.
    (state_dir / "state.json").write_bytes(b"kaputt")
    assert store.load_state(NOW).hold_until == NOW + 24 * 3600
    back = NOW - 3 * 86400
    writes = count_state_writes(monkeypatch)
    first = store.load_state(back)
    assert first.hold_until == back + 24 * 3600, "auf die Hoechstfrist gekappt, nicht 96 h"
    assert len(writes) == 1, "der gekappte Stand wird einmal festgehalten"
    for i in range(1, 6):
        again = store.load_state(back + i)
        assert again.hold_until == back + 24 * 3600
    assert len(writes) == 1, "danach schreibt kein Laden mehr"
    disk = json.loads((state_dir / "state.json").read_text())["hold_until"]
    assert disk == back + 24 * 3600, "auf der Platte dasselbe wie im Speicher"
    request = make_request(rid=RID2)
    assert refused(store, again, request, back + 24 * 3600 - 1) == "state_unsafe"
    assert refused(store, again, request, back + 24 * 3600) is None, "ein Uhrsprung sperrt nie laenger als die Frist"
    assert len(writes) == 1


def test_clock_set_back_caps_the_hold_floor_for_check_without_loading(store, state_dir):
    store.hold(NOW)
    mem = State()
    back = NOW - 3 * 86400
    assert refused(store, mem, make_request(rid=RID2), back + 1) == "state_unsafe"
    assert mem.hold_until == back + 1 + 24 * 3600
    assert refused(store, mem, make_request(rid=RID2), back + 1 + 24 * 3600) is None


def test_load_state_writes_only_when_something_changed(store, monkeypatch):
    store.hold(NOW)
    writes = count_state_writes(monkeypatch)
    for i in range(1, 4):
        assert store.load_state(NOW + i).hold_until == NOW + 24 * 3600
    assert writes == [], "nichts geaendert, nichts geschrieben"
    # Eine kleine Rueckstellung (innerhalb der Toleranz) kappt nichts und schreibt nichts.
    assert store.load_state(NOW - 30).hold_until == NOW + 24 * 3600
    assert writes == []


def test_load_state_brings_the_hold_floor_into_the_returned_state_and_saves_it_once(store, state_dir, monkeypatch):
    store.hold(NOW)
    (state_dir / "state.json").write_text(json.dumps({**GOOD, "hold_until": NOW + 5}))  # von aussen ueberschrieben
    writes = count_state_writes(monkeypatch)
    assert store.load_state(NOW + 1).hold_until == NOW + 24 * 3600, "die Sperre des Speichers gilt im State"
    assert len(writes) == 1
    assert json.loads((state_dir / "state.json").read_text())["hold_until"] == NOW + 24 * 3600
    assert store.load_state(NOW + 2).hold_until == NOW + 24 * 3600
    assert len(writes) == 1, "nur einmal"


# ---------------------------------------------------------------------------
# Zustand: Grenzen, Slot, Aufraeumen
# ---------------------------------------------------------------------------


def test_limits_survive_a_restart(store):
    s = store.load_state(NOW)
    s.mark_seen(RID, NOW)
    s.record_action(NOW)
    s.block_version("0.7.1", NOW)
    store.save_state(s)
    store.close()
    store.open()
    again = store.load_state(NOW + 1)
    for request, code in ((make_request(rid=RID), "replay"), (make_request(rid=RID2), "rate_limited")):
        with pytest.raises(Refusal) as info:
            store.check(again, request, NOW + 1)
        assert info.value.code == code
    with pytest.raises(Refusal) as info:
        store.check(again, make_request(rid=RID2), NOW + 601)
    assert info.value.code == "blocked_version"
    store.check(again, make_request(rid=RID2, version="0.7.2"), NOW + 601)


def test_refused_requests_do_not_count_against_the_rate_limit(store):
    # M14: "Abgelehnte Anforderungen zaehlen nicht". Abgelehntes wird nur als gesehen gemerkt (Replay), nur eine
    # angenommene Aktion (`record_action`) sperrt die naechsten 10 Minuten.
    s = State()
    s.mark_seen(RID, NOW)
    store.check(s, make_request(rid=RID2), NOW + 1)  # keine rate_limited
    with pytest.raises(Refusal) as info:
        store.check(s, make_request(rid=RID), NOW + 1)
    assert info.value.code == "replay", "dieselbe ID bleibt eine Stunde gesperrt"
    s.record_action(NOW + 2)
    with pytest.raises(Refusal) as info:
        store.check(s, make_request(rid=RID2), NOW + 3)
    assert info.value.code == "rate_limited"
    store.check(s, make_request(rid=RID2), NOW + 2 + 600)


def test_rollback_slot_is_single_use_and_never_chained():
    s = State()
    s.commit_update(make_slot())
    request = make_request(action="rollback", version="0.7.0")
    assert policy.check_rollback(request, s.slot, target_container_id=CID_NEW, target_image_id=IMG_NEW,
                                 now=NOW + 60) == s.slot
    s.commit_rollback("0.7.1", NOW + 120)
    assert s.slot is None, "einmal benutzt, kein neuer Slot"
    assert s.blocked == {"0.7.1": NOW + 120 + 24 * 3600}
    with pytest.raises(Refusal) as info:
        policy.check_rollback(request, s.slot, target_container_id=CID_NEW, target_image_id=IMG_NEW, now=NOW + 180)
    assert info.value.code == "no_previous"


def test_prune_drops_only_what_has_expired():
    s = State(slot=make_slot(NOW - 10 * 86400), actions=[NOW - 600, NOW - 599, NOW + 50],
              blocked={"0.7.1": NOW, "0.7.2": NOW + 1}, seen={RID: NOW - 3600, RID2: NOW - 3599},
              hold_until=NOW)
    s.prune(NOW)
    assert s.actions == [NOW - 599, NOW + 50]
    assert s.blocked == {"0.7.2": NOW + 1}
    assert s.seen == {RID2: NOW - 3599}
    assert s.hold_until is None
    assert s.slot is not None, "der abgelaufene Slot gehoert dem Ablauf (Schutz-Tag entfernen)"


def test_caps_keep_the_newest_entries(store):
    # Zeiten in der Vergangenheit: Zeitstempel weit in der Zukunft kappt `load_state` (siehe unten).
    base = NOW - 1500
    s = State()
    for i in range(state.MAX_SEEN + 5):
        s.mark_seen(f"{i:08x}-0000-4000-8000-000000000000", base + i)
    assert len(s.seen) == state.MAX_SEEN
    assert "00000000-0000-4000-8000-000000000000" not in s.seen
    for i in range(state.MAX_ACTIONS + 3):
        s.record_action(base + 1000 + i)
    assert len(s.actions) == state.MAX_ACTIONS and s.actions[-1] == base + 1000 + state.MAX_ACTIONS + 2
    for i in range(state.MAX_BLOCKED + 2):
        s.block_version(f"1.0.{i}", base + i)
    assert len(s.blocked) == state.MAX_BLOCKED
    store.save_state(s)
    assert store.load_state(NOW) == s


# ---------------------------------------------------------------------------
# Zeitstempel in der Zukunft (Fund 5): sperren, aber hoechstens bis zur Hoechstfrist
# ---------------------------------------------------------------------------

FAR = NOW + 10 * 365 * 86400


def test_far_future_timestamps_are_capped_to_the_maximum_period():
    s = State(actions=[FAR], blocked={"0.7.1": FAR}, seen={RID: FAR}, hold_until=FAR)
    s.prune(NOW)
    assert s.actions == [NOW] and s.seen == {RID: NOW}
    assert s.blocked == {"0.7.1": NOW + 24 * 3600} and s.hold_until == NOW + 24 * 3600


def test_timestamps_within_the_clock_slack_stay_as_they_are():
    s = State(actions=[NOW + 30, NOW + 60], seen={RID: NOW + 60}, blocked={"0.7.1": NOW + 24 * 3600 + 60},
              hold_until=NOW + 24 * 3600 + 60)
    changed = s.cap_future(NOW)
    assert not changed
    assert s.actions == [NOW + 30, NOW + 60] and s.blocked == {"0.7.1": NOW + 24 * 3600 + 60}
    assert s.seen == {RID: NOW + 60} and s.hold_until == NOW + 24 * 3600 + 60
    assert State(actions=[NOW + 61]).cap_future(NOW), "eine Sekunde mehr: gekappt"


def refused(store, s, request, now) -> str | None:
    try:
        store.check(s, request, now)
    except Refusal as exc:
        return exc.code
    return None


def test_after_a_clock_jump_the_block_ends_with_the_maximum_period_not_in_years(store):
    # Die Uhr sprang vor, eine Aktion wurde angenommen, die Uhr wurde zurueckgestellt: frueher sperrte das bis
    # `FAR`. Jetzt: 10 Minuten ab dem ersten Pruefen bei richtiger Uhr, 24 h bzw. 1 h fuer die anderen Sperren.
    request = make_request(rid=RID2)
    rate = State(actions=[FAR])
    assert refused(store, rate, request, NOW) == "rate_limited"
    assert refused(store, rate, request, NOW + 599) == "rate_limited"
    assert refused(store, rate, request, NOW + 600) is None
    hold = State(hold_until=FAR)
    assert refused(store, hold, request, NOW) == "state_unsafe"
    assert refused(store, hold, request, NOW + 24 * 3600 - 1) == "state_unsafe"
    assert refused(store, hold, request, NOW + 24 * 3600) is None
    block = State(blocked={"0.7.1": FAR})
    assert refused(store, block, request, NOW) == "blocked_version"
    assert refused(store, block, request, NOW + 24 * 3600 - 1) == "blocked_version"
    assert refused(store, block, request, NOW + 24 * 3600) is None
    seen = State(seen={RID2: FAR})
    assert refused(store, seen, request, NOW) == "replay"
    assert refused(store, seen, request, NOW + 3599) == "replay"
    assert refused(store, seen, request, NOW + 3600) is None


def test_load_state_caps_the_future_and_keeps_the_capped_state(store, state_dir):
    store.save_state(State(actions=[FAR], blocked={"0.7.1": FAR}, seen={RID: FAR}))
    first = store.load_state(NOW)
    assert first.actions == [NOW] and first.blocked == {"0.7.1": NOW + 24 * 3600} and first.seen == {RID: NOW}
    # Gespeichert: ein Neustart bei spaeterer Uhrzeit rechnet vom ersten Mal an, nicht erneut von "jetzt".
    store.close()
    store.open()
    later = store.load_state(NOW + 601)
    assert later.actions == [NOW] and later.seen == {RID: NOW}
    assert refused(store, later, make_request(rid=RID2, version="0.7.2"), NOW + 601) is None, "10 Minuten sind um"


def test_failed_save_of_the_capped_state_is_not_an_error(store, state_dir, monkeypatch):
    store.save_state(State(actions=[FAR]))
    refuse_creating_files(monkeypatch)
    assert store.load_state(NOW).actions == [NOW], "im Speicher gekappt, auch wenn das Zurueckschreiben scheitert"
    monkeypatch.undo()
    assert json.loads((state_dir / "state.json").read_text())["actions"] == [FAR]


def test_state_methods_reject_garbage():
    s = State()
    with pytest.raises(ValueError):
        s.mark_seen("keine-id", NOW)
    with pytest.raises(ValueError):
        s.block_version("latest", NOW)


# ---------------------------------------------------------------------------
# journal.json
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("step", policy.STEPS)
def test_journal_roundtrip_for_every_step(store, state_dir, step):
    journal = make_journal(step)
    store.write_journal(journal)
    assert store.load_journal(NOW) == journal
    assert mode(state_dir / "journal.json") == 0o600


def test_rollback_journal_may_have_no_digest(store):
    journal = make_journal("begin", action="rollback").advance(
        "pulled", new=NewImage(image_id=IMG_OLD, digest=None, version="0.7.0"))
    store.write_journal(journal)
    assert store.load_journal(NOW) == journal


def test_journal_write_ahead_fsyncs_file_and_directory(store, monkeypatch):
    kinds = []
    real_fsync = os.fsync

    def spy(fd):
        kinds.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    store.write_journal(make_journal("begin"))
    assert kinds == ["file", "dir"]
    kinds.clear()
    store.clear_journal()
    assert kinds == ["dir"]


def test_clear_journal_is_idempotent(store, state_dir):
    store.write_journal(make_journal("pulled"))
    store.clear_journal()
    store.clear_journal()
    assert store.load_journal(NOW) is None
    assert files(state_dir) == ["lock"]


def test_journal_steps_only_go_forward():
    journal = make_journal("created")
    with pytest.raises(ValueError):
        journal.advance("tagged")
    assert journal.advance("created", deadline=NOW + 1000).deadline == NOW + 1000
    with pytest.raises(ValueError):
        journal.advance("unbekannt")


def test_journal_consistency_rules():
    begin = make_journal("begin")
    new = NewImage(image_id=IMG_NEW, digest=DIGEST, version="0.7.1")
    with_id = NewImage(image_id=IMG_NEW, digest=DIGEST, version="0.7.1", id=CID_NEW)
    bad = [
        lambda: begin.advance("begin", new=new),                       # new vor `pulled`
        lambda: begin.advance("pulled"),                               # new fehlt ab `pulled`
        lambda: begin.advance("pulled", new=with_id),                  # Container-ID vor `created`
        lambda: begin.advance("created", new=new),                     # Container-ID fehlt ab `created`
        lambda: begin.advance("pulled", new=NewImage(image_id=IMG_NEW, digest=None, version="0.7.1")),
        lambda: begin.advance("begin", request_id="x"),
        lambda: begin.advance("begin", deadline=-1),
        lambda: begin.advance("begin", action="downgrade"),
        lambda: begin.advance("begin", old=OldContainer(CID_OLD, "/mit-schraegstrich", IMG_OLD, "0.7.0",
                                                         ("no", 0), "ghcr.io/nodvard/deck")),
        lambda: begin.advance("begin", old=OldContainer(CID_OLD, "n", IMG_OLD, "0.7.0", ("no", 0),
                                                         "ghcr.io/nodvard/deck:0.7.0")),  # gepinnt
        lambda: begin.advance("begin", old=OldContainer(CID_OLD, "n", IMG_OLD, "0.7.0", ("sometimes", 0),
                                                         "ghcr.io/nodvard/deck")),
        lambda: begin.advance("begin", old=OldContainer(CID_OLD[:12], "n", IMG_OLD, "0.7.0", ("no", 0),
                                                         "ghcr.io/nodvard/deck")),
    ]
    for make in bad:
        with pytest.raises((ValueError, TypeError)):
            make()
    assert begin.floating_tag == "latest"


TEST_REPO = "registry.test/nd/deck"


def make_test_repo_journal(step="begin") -> Journal:
    old = OldContainer(id=CID_OLD, name="nodvard-deck-nodvard-deck-1", image_id=IMG_OLD, version="0.7.0",
                       restart_policy=("unless-stopped", 0), tag_text=TEST_REPO + ":0.9")
    journal = Journal(request_id=RID, action="update", step="begin", started_at=NOW, deadline=NOW + 900, old=old,
                      repository=TEST_REPO)
    journal.validate()
    if step != "begin":
        new = NewImage(image_id=IMG_NEW, digest=DIGEST, version="0.7.1")
        for next_step in policy.STEPS[1:policy.STEPS.index(step) + 1]:
            if next_step == "created":
                new = NewImage(image_id=IMG_NEW, digest=DIGEST, version="0.7.1", id=CID_NEW)
            journal = journal.advance(next_step, new=new)
    return journal


def test_journal_floating_tag_and_advance_respect_the_repository(store):
    # Fund 6: `validate(repository=...)` pruefte gegen das Test-Repository, `floating_tag` und `advance()` immer
    # gegen die Konstante -- mit einem Test-Repository (lokale Registry in der CI) scheiterten beide.
    journal = make_test_repo_journal()
    assert journal.floating_tag == "0.9"
    advanced = journal.advance("pulled", new=NewImage(image_id=IMG_NEW, digest=DIGEST, version="0.7.1"))
    assert advanced.floating_tag == "0.9" and advanced.repository == TEST_REPO
    assert Journal.from_json(advanced.to_json(), repository=TEST_REPO).floating_tag == "0.9"
    assert "repository" not in advanced.to_json() and "registry.test" not in json.dumps(
        {k: v for k, v in advanced.to_json().items() if k != "old"})


def test_journal_roundtrip_through_a_store_with_a_test_repository(state_dir, uid):
    with StateStore(state_dir, expected_uid=uid, repository=TEST_REPO) as store:
        journal = make_test_repo_journal("created")
        store.write_journal(journal)
        loaded = store.load_journal(NOW)
        assert loaded == journal and loaded.floating_tag == "0.9"
        assert loaded.advance("old_stopped").floating_tag == "0.9"


def test_journal_with_the_default_repository_still_refuses_foreign_images():
    # Gegenprobe: ohne Test-Repository gilt die Konstante, und ein fremdes Image bleibt fremd.
    foreign = OldContainer(id=CID_OLD, name="n", image_id=IMG_OLD, version="0.7.0", restart_policy=("no", 0),
                           tag_text=TEST_REPO + ":0.9")
    journal = Journal(request_id=RID, action="update", step="begin", started_at=NOW, deadline=NOW + 900, old=foreign)
    with pytest.raises(ValueError):
        journal.validate()
    with pytest.raises(Refusal) as info:
        journal.floating_tag  # noqa: B018 - die Property wirft
    assert info.value.code == "foreign_image"
    with pytest.raises(ValueError):
        Journal.from_json(make_test_repo_journal().to_json())


def corrupt_journals() -> dict[str, bytes]:
    good = make_journal("created").to_json()
    return {
        "kein JSON": b"{",
        "leer": b"",
        "Zusatzfeld": json.dumps({**good, "x": 1}).encode(),
        "Schritt unbekannt": json.dumps({**good, "step": "fertig"}).encode(),
        "format 2": json.dumps({**good, "format": 2}).encode(),
        "fremdes Image": json.dumps({**good, "old": {**good["old"], "tag_text": "docker.io/evil/deck"}}).encode(),
        "Restart-Policy kaputt": json.dumps({**good, "old": {**good["old"], "restart_policy": {"Name": "no"}}}).encode(),
        "neue ID fehlt": json.dumps({**good, "new": {**good["new"], "id": None}}).encode(),
        "Digest kaputt": json.dumps({**good, "new": {**good["new"], "digest": "sha256:abc"}}).encode(),
        "Digest in Grossbuchstaben": json.dumps({**good, "new": {**good["new"], "digest": "sha256:" + "A" * 64}}).encode(),
        "doppelter Schluessel": json.dumps(good).encode()[:-1] + b',"step":"begin"}',
    }


@pytest.mark.parametrize("name", list(corrupt_journals()))
def test_corrupt_journal_sets_hold_and_is_moved_aside(store, state_dir, name):
    raw = corrupt_journals()[name]
    store.save_state(State(slot=make_slot(), seen={RID2: NOW}))
    (state_dir / "journal.json").write_bytes(raw)
    with pytest.raises(Refusal) as info:
        store.load_journal(NOW)
    assert (info.value.code, info.value.detail) == ("state_unsafe", "journal_corrupt")
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert store.problem == "journal_corrupt"
    assert (state_dir / "journal.json.broken").read_bytes() == raw
    held = store.load_state(NOW)
    assert held.hold_until == NOW + 24 * 3600
    assert held.slot == make_slot() and held.seen == {RID2: NOW}, "der Rest des Zustands bleibt"
    assert store.load_journal(NOW) is None, "beim naechsten Mal gibt es keinen halben Vorgang mehr"


@pytest.fixture
def strict_umask():
    # Eine strenge umask (0o277: nicht einmal Gruppe/Welt lesen, nicht einmal der Besitzer schreiben) macht aus
    # jedem mit `mode=` angelegten Modus etwas Kleineres. Nur `fchmod` am Deskriptor stellt den Soll-Modus her.
    old = os.umask(0o277)
    try:
        yield
    finally:
        os.umask(old)


def test_state_and_journal_get_their_mode_whatever_the_umask(store, state_dir, strict_umask):
    store.save_state(State(slot=make_slot()))
    store.write_journal(make_journal("begin"))
    assert mode(state_dir / "state.json") == 0o600 and mode(state_dir / "journal.json") == 0o600


def test_broken_copy_gets_its_mode_whatever_the_umask(store, state_dir, strict_umask):
    (state_dir / "state.json").write_bytes(b"kaputt")
    store.load_state(NOW)
    assert mode(state_dir / "state.json.broken") == 0o600 and mode(state_dir / "state.json") == 0o600


def test_existing_lock_with_loose_mode_is_set_to_0600_whatever_the_umask(state_dir, uid, strict_umask):
    (state_dir / "lock").write_text("")
    (state_dir / "lock").chmod(0o644)
    with StateStore(state_dir, expected_uid=uid):
        assert mode(state_dir / "lock") == 0o600


def test_lock_file_of_another_owner_is_unsafe_without_root_too(state_dir, uid, monkeypatch):
    (state_dir / "lock").write_text("")
    fake_owner(monkeypatch, {state_dir / "lock": uid + 1000})
    with pytest.raises(Refusal) as info:
        StateStore(state_dir, expected_uid=uid).open()
    assert (info.value.code, info.value.detail) == ("state_unsafe", "lock_file")


def test_journal_file_of_another_owner_is_corrupt_without_root_too(store, state_dir, uid, monkeypatch):
    store.write_journal(make_journal("begin"))
    fake_owner(monkeypatch, {state_dir / "journal.json": uid + 1000})
    with pytest.raises(Refusal) as info:
        store.load_journal(NOW)
    assert (info.value.code, info.value.detail) == ("state_unsafe", "journal_corrupt")
    assert store.load_state(NOW).hold_until == NOW + 24 * 3600, "und die Sperre ist gesetzt"


def test_state_file_of_another_owner_is_corrupt_without_root_too(store, state_dir, uid, monkeypatch):
    store.save_state(State(slot=make_slot()))
    fake_owner(monkeypatch, {state_dir / "state.json": uid + 1000})
    assert store.load_state(NOW) == State(hold_until=NOW + 24 * 3600) and store.problem == "state_corrupt"


@needs_root
def test_lock_file_owned_by_the_dashboard_user_is_unsafe(state_dir):
    (state_dir / "lock").write_text("")
    os.chown(state_dir / "lock", 1000, 1000)
    with pytest.raises(Refusal) as info:
        StateStore(state_dir).open()
    assert (info.value.code, info.value.detail) == ("state_unsafe", "lock_file")


@needs_root
def test_journal_file_owned_by_the_dashboard_user_is_corrupt(state_dir):
    with StateStore(state_dir) as root_store:
        root_store.write_journal(make_journal("begin"))
        os.chown(state_dir / "journal.json", 1000, 1000)
        with pytest.raises(Refusal) as info:
            root_store.load_journal(NOW)
        assert (info.value.code, info.value.detail) == ("state_unsafe", "journal_corrupt")
        assert root_store.load_state(NOW).hold_until == NOW + 24 * 3600


def test_journal_symlink_is_never_followed(store, state_dir, tmp_path):
    victim = tmp_path / "victim.json"
    victim.write_text(json.dumps(make_journal("begin").to_json()))
    (state_dir / "journal.json").symlink_to(victim)
    with pytest.raises(Refusal):
        store.load_journal(NOW)
    assert victim.read_text() == json.dumps(make_journal("begin").to_json())
    assert (state_dir / "journal.json.broken").is_symlink()
    store.write_journal(make_journal("begin"))
    assert stat.S_ISREG(os.lstat(state_dir / "journal.json").st_mode)
