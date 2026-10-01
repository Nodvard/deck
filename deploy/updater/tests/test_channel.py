"""`channel.py`: der Kanal zwischen Dashboard und Helfer (Bauplan 2c-2, Ebene 2 der Teststrategie).

Echtes Dateisystem unter `tmp_path`. Das Dashboard gilt als Angreifer: Symlinks, FIFOs, Hardlinks, riesige und
duenne Dateien, ausgetauschte Ordner, vorab hingelegte Temp-Namen, sehr viele Eintraege. Der Helfer darf dabei
nie blockieren, nie einem Link folgen, nie in Anforderungen schreiben und nur feste Codes in den Status schreiben.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import textwrap
import threading
import time

import pytest
from nodvard_deck_updater import channel, policy, state
from nodvard_deck_updater.channel import Channel
from nodvard_deck_updater.policy import Refusal
from updater_support import NOW, UPDATER_DIR, fake_owner, load_vectors, needs_root

RID = "6f1c2b0e-9a4d-4c1e-8f00-1b2c3d4e5f60"
STATUS = load_vectors("status.json")


def rid(i: int) -> str:
    return f"{i:08x}-0000-4000-8000-000000000000"


def request_doc(request_id: str = RID, **over) -> bytes:
    doc = {"v": 1, "id": request_id, "action": "update", "version": "0.7.1", "created_at": NOW}
    doc.update(over)
    return json.dumps(doc).encode()


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "channel"
    path.mkdir()
    path.chmod(0o755)
    return path


@pytest.fixture
def ch(root, uid):
    c = Channel(root, expected_uid=uid)
    c.setup()
    yield c
    c.close()


def requests_dir(root):
    return root / "requests"


def put(root, name: str, data: bytes = b"") -> None:
    (requests_dir(root) / name).write_bytes(data)


def poll(c: Channel, now: float = NOW) -> channel.Poll:
    return c.poll(now=now)


def unsafe(func, *args, **kwargs) -> str | None:
    try:
        func(*args, **kwargs)
    except Refusal as exc:
        assert exc.code == "channel_unsafe"
        return exc.detail
    return None


def run_with_timeout(func, timeout: float = 10.0):
    """Fuehrt `func` in einem Thread aus und schlaegt fehl, wenn er blockiert (statt die Tests haengen zu lassen)."""
    result: dict = {}

    def target():
        try:
            result["value"] = func()
        except BaseException as exc:  # noqa: BLE001 - wird im Haupt-Thread erneut geworfen
            result["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "blockiert"
    if "error" in result:
        raise result["error"]
    return result["value"]


# ---------------------------------------------------------------------------
# Einrichten (M4)
# ---------------------------------------------------------------------------


def test_setup_creates_sticky_requests_dir(root, uid):
    with Channel(root, expected_uid=uid) as c:
        c.setup()
        assert c.ready
        st = os.lstat(requests_dir(root))
        assert stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o1777 and st.st_uid == uid


def test_setup_fixes_mode_of_own_existing_requests_dir(root, uid):
    requests_dir(root).mkdir(mode=0o755)
    Channel(root, expected_uid=uid).setup()
    assert stat.S_IMODE(os.lstat(requests_dir(root)).st_mode) == 0o1777


def test_root_of_another_owner_is_unsafe(root, uid):
    c = Channel(root, expected_uid=uid + 1)
    assert unsafe(c.setup) == "root_owner"
    assert not c.ready and not requests_dir(root).exists()


@needs_root
def test_root_owned_by_dashboard_user_is_unsafe(root):
    os.chown(root, 1000, 1000)
    c = Channel(root)
    assert unsafe(c.setup) == "root_owner"
    assert not c.ready


@pytest.mark.parametrize("bad_mode", [0o775, 0o757, 0o777, 0o1777])
def test_root_writable_for_group_or_world_is_unsafe(root, uid, bad_mode):
    root.chmod(bad_mode)
    c = Channel(root, expected_uid=uid)
    assert unsafe(c.setup) == "root_mode"
    assert not requests_dir(root).exists(), "in einer unsicheren Wurzel wird nichts angelegt"


def test_root_symlink_missing_or_file_is_unsafe(tmp_path, root, uid):
    link = tmp_path / "link"
    link.symlink_to(root)
    a_file = tmp_path / "file"
    a_file.write_text("x")
    for path in (link, tmp_path / "fehlt", a_file):
        assert unsafe(Channel(path, expected_uid=uid).setup) == "root_open"


def test_requests_as_symlink_to_state_is_unsafe_and_state_untouched(tmp_path, root, uid):
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    requests_dir(root).symlink_to(state_dir)
    c = Channel(root, expected_uid=uid)
    assert unsafe(c.setup) == "requests_open"
    assert stat.S_IMODE(os.lstat(state_dir).st_mode) == 0o700, "das Ziel des Symlinks bekommt nie 1777"


def test_requests_as_file_is_unsafe(root, uid):
    requests_dir(root).write_text("x")
    assert unsafe(Channel(root, expected_uid=uid).setup) == "requests_open"


@needs_root
def test_requests_dir_of_dashboard_user_is_unsafe(root):
    requests_dir(root).mkdir(mode=0o1777)
    os.chown(requests_dir(root), 1000, 1000)
    assert unsafe(Channel(root).setup) == "requests_owner"


def test_production_defaults_are_the_strict_ones():
    # Testbarkeit ohne root nur ueber den Konstruktor (nie ueber die Umgebung); im Betrieb gelten die Vorgaben.
    params = inspect.signature(Channel.__init__).parameters
    assert params["root"].default == "/channel"
    assert params["expected_uid"].default == 0 and params["expected_uid"].kind is inspect.Parameter.KEYWORD_ONLY


def test_expected_uid_must_be_a_plain_int(root):
    for bad in (-1, "0", None, True):
        with pytest.raises(ValueError):
            Channel(root, expected_uid=bad)


def test_poll_before_setup_is_refused(root, uid):
    assert unsafe(Channel(root, expected_uid=uid).poll) == "not_ready"


# ---------------------------------------------------------------------------
# Anforderungen lesen (M3)
# ---------------------------------------------------------------------------


def test_valid_request_is_read_and_removed(ch, root):
    data = request_doc()
    put(root, f"{RID}.json", data)
    result = poll(ch)
    assert result.items == (channel.Incoming(id=RID, raw=data),)
    assert policy.parse_request(result.items[0].raw, now=NOW, file_id=RID).version == "0.7.1"
    assert os.listdir(requests_dir(root)) == []


def test_exactly_4096_bytes_is_read_4097_is_refused(ch, root):
    put(root, f"{rid(1)}.json", b"x" * 4096)
    put(root, f"{rid(2)}.json", b"x" * 4097)
    items = {item.id: item for item in poll(ch).items}
    assert items[rid(1)].raw == b"x" * 4096
    assert items[rid(2)] == channel.Incoming(id=rid(2), raw=None, code="bad_request")
    assert os.listdir(requests_dir(root)) == []


def test_sparse_one_gigabyte_file_is_never_read(ch, root):
    with open(requests_dir(root) / f"{RID}.json", "wb") as fh:
        fh.truncate(1024**3)
    started = time.monotonic()
    assert poll(ch).items == (channel.Incoming(id=RID, raw=None, code="bad_request"),)
    assert time.monotonic() - started < 2
    assert os.listdir(requests_dir(root)) == []


def test_symlink_request_is_never_followed(ch, root, tmp_path):
    victim = tmp_path / "state.json"
    victim.write_bytes(request_doc())
    (requests_dir(root) / f"{RID}.json").symlink_to(victim)
    assert poll(ch).items == (channel.Incoming(id=RID, raw=None, code="bad_request"),)
    assert os.listdir(requests_dir(root)) == []
    assert victim.read_bytes() == request_doc(), "das Ziel bleibt unberuehrt"


def test_fifo_request_does_not_block(ch, root):
    os.mkfifo(requests_dir(root) / f"{RID}.json")
    result = run_with_timeout(lambda: poll(ch))
    assert result.items == (channel.Incoming(id=RID, raw=None, code="bad_request"),)
    assert os.listdir(requests_dir(root)) == []


def test_fifo_swapped_in_after_lstat_does_not_block(root, tmp_path, uid):
    # Der Wettlauf: zwischen lstat und open tauscht das Dashboard die Datei gegen ein FIFO. O_NONBLOCK + fstat
    # fangen das ab; hier direkt am Lese-Helfer geprueft.
    fifo_dir = tmp_path / "fifo"
    fifo_dir.mkdir()
    os.mkfifo(fifo_dir / "x.json")
    dfd = os.open(fifo_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(state.UnsafeFile):
            run_with_timeout(lambda: state.read_regular(dfd, "x.json", max_bytes=4096))
    finally:
        os.close(dfd)


def test_file_swapped_after_lstat_is_refused(ch, root, monkeypatch):
    # lstat sieht die eine Datei, geoeffnet wird eine andere (anderes Inode) -> abgelehnt.
    put(root, f"{RID}.json", request_doc())
    real_stat = os.stat

    def swapping_stat(name, *args, **kwargs):
        result = real_stat(name, *args, **kwargs)
        if name == f"{RID}.json":
            dfd = kwargs["dir_fd"]
            fd = os.open("neu.tmp", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=dfd)  # neues Inode, solange das alte lebt
            os.write(fd, request_doc())
            os.close(fd)
            os.rename("neu.tmp", name, src_dir_fd=dfd, dst_dir_fd=dfd)
        return result

    monkeypatch.setattr(os, "stat", swapping_stat)
    assert poll(ch).items == (channel.Incoming(id=RID, raw=None, code="bad_request"),)


def test_hardlink_to_a_file_outside_is_refused(ch, root, tmp_path):
    victim = tmp_path / "geheim.json"
    victim.write_bytes(request_doc())
    os.link(victim, requests_dir(root) / f"{RID}.json")
    assert poll(ch).items == (channel.Incoming(id=RID, raw=None, code="bad_request"),)
    assert os.listdir(requests_dir(root)) == []
    assert victim.read_bytes() == request_doc() and os.lstat(victim).st_nlink == 1, "nur der Link ist weg"


def test_empty_directories_are_removed_with_rmdir_nothing_else(ch, root, tmp_path):
    victim = tmp_path / "ausserhalb"
    victim.mkdir()
    (victim / "datei").write_text("unberuehrt")
    (requests_dir(root) / f"{RID}.json").mkdir()
    (requests_dir(root) / "fremder-ordner").mkdir()
    (requests_dir(root) / "link-auf-ordner").symlink_to(victim)
    result = poll(ch)
    assert result.items == () and result.directories == 0 and not result.cluttered
    assert result.removed == 3, "zwei leere Ordner und der Symlink (der Symlink selbst, nie sein Ziel)"
    assert os.listdir(requests_dir(root)) == []
    assert (victim / "datei").read_text() == "unberuehrt"


def test_non_empty_directories_stay_and_are_reported_never_removed_recursively(ch, root):
    full = requests_dir(root) / f"{RID}.json"
    full.mkdir()
    (full / "innen").write_text("x")
    other = requests_dir(root) / "fremder-ordner"
    other.mkdir()
    (other / "tiefer").mkdir()
    result = poll(ch)
    assert result.items == () and result.directories == 2 and result.cluttered
    assert (full / "innen").read_text() == "x" and (other / "tiefer").is_dir(), "nie rekursiv"
    assert poll(ch).directories == 2, "jede Runde wieder gemeldet"
    (full / "innen").unlink()
    (other / "tiefer").rmdir()
    assert poll(ch).directories == 0, "nach dem Aufraeumen von Hand ist der Kanal wieder frei"
    assert os.listdir(requests_dir(root)) == []


def test_directories_never_crowd_out_real_requests(ch, root):
    # Fund 1: mehr als 32 Ordner `<uuid4>.json` und mehr als 1000 fremde Ordner (alle nicht leer, damit sie
    # stehen bleiben) belegten frueher die Plaetze von MAX_HANDLE/MAX_SCAN -- echte Anforderungen kamen nie dran.
    # Echte Anforderungen vor *und* nach den Ordnern, damit es von der Reihenfolge im Dateisystem unabhaengig ist.
    wanted = {rid(i) for i in range(5)} | {rid(i) for i in range(5, 10)}
    for i in range(5):
        put(root, f"{rid(i)}.json", request_doc(rid(i)))
    for i in range(channel.MAX_HANDLE + 8):
        folder = requests_dir(root) / f"{rid(1000 + i)}.json"
        folder.mkdir()
        (folder / "x").write_text("x")
    for i in range(channel.MAX_SCAN + 100):
        folder = requests_dir(root) / f"fremd-{i}"
        folder.mkdir()
        (folder / "x").write_text("x")
    for i in range(5, 10):
        put(root, f"{rid(i)}.json", request_doc(rid(i)))
    result = poll(ch)
    assert {item.id for item in result.items} == wanted, "alle in der ersten Runde"
    assert all(item.raw is not None for item in result.items)
    assert result.directories == channel.MAX_HANDLE + 8 + channel.MAX_SCAN + 100
    assert result.scanned == result.directories + 10
    assert sorted(p for p in os.listdir(requests_dir(root)) if not p.startswith(("fremd-", "0000"))) == []


def test_reading_the_directory_has_its_own_upper_bound(ch, root, monkeypatch):
    monkeypatch.setattr(channel, "MAX_READ", 50)
    for i in range(200):
        folder = requests_dir(root) / f"fremd-{i}"
        folder.mkdir()
        (folder / "x").write_text("x")
    result = poll(ch)
    assert result.scanned <= 50 and result.directories <= 50


@pytest.fixture
def rmdir_calls(monkeypatch):
    """Alle `os.rmdir`-Aufrufe (jeder ist ein Systemaufruf, auch wenn er an einem vollen Ordner scheitert)."""
    calls: list[object] = []
    real = os.rmdir

    def counting(path, *args, **kwargs):
        calls.append(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "rmdir", counting)
    return calls


def make_full_folders(root, count: int, prefix: str = "fremd") -> None:
    for i in range(count):
        folder = requests_dir(root) / f"{prefix}-{i}"
        folder.mkdir()
        (folder / "x").write_text("x")


def test_rmdir_attempts_per_round_are_bounded_but_every_folder_still_counts(ch, root, rmdir_calls):
    # Befund: 100 500 volle Ordner kosteten jede Runde 100 500 `rmdir` (0,6 s). Jetzt hoechstens `MAX_RMDIR`;
    # der Rest wird ohne Systemaufruf als uebrig gezaehlt, `directories` und `cluttered` bleiben richtig.
    assert 0 < channel.MAX_RMDIR <= channel.MAX_SCAN
    total = channel.MAX_RMDIR + 50
    make_full_folders(root, total)
    put(root, f"{RID}.json", request_doc())
    result = poll(ch)
    assert len(rmdir_calls) == channel.MAX_RMDIR
    assert result.directories == total and result.cluttered
    assert result.scanned == total + 1 and [item.id for item in result.items] == [RID]
    rmdir_calls.clear()
    again = poll(ch)
    assert len(rmdir_calls) == channel.MAX_RMDIR, "auch jede weitere Runde: gleich viele, nie mehr"
    assert again.directories == total


def test_rmdir_budget_is_spent_on_the_listing_not_on_the_whole_directory(ch, root, rmdir_calls, monkeypatch):
    monkeypatch.setattr(channel, "MAX_READ", 50)
    monkeypatch.setattr(channel, "MAX_RMDIR", 20)
    make_full_folders(root, 200)
    result = poll(ch)
    assert len(rmdir_calls) == 20
    assert result.scanned == 50 and result.directories == 50, "20 versucht, 30 ohne Aufruf uebrig"


def test_empty_folders_beyond_the_rmdir_budget_go_in_later_rounds(ch, root, rmdir_calls, monkeypatch):
    monkeypatch.setattr(channel, "MAX_RMDIR", 20)
    for i in range(50):
        (requests_dir(root) / f"leer-{i}").mkdir()
    first = poll(ch)
    assert (first.removed, first.directories, len(rmdir_calls)) == (20, 30, 20)
    assert first.cluttered, "was noch steht, wird auch gemeldet"
    second = poll(ch)
    assert (second.removed, second.directories, len(rmdir_calls)) == (20, 10, 40)
    third = poll(ch)
    assert (third.removed, third.directories, len(rmdir_calls)) == (10, 0, 50)
    assert not third.cluttered and os.listdir(requests_dir(root)) == []


def test_entry_that_becomes_a_directory_after_the_listing_is_left_alone(ch, root, monkeypatch):
    # Zwischen Durchlesen (Datei) und Bearbeiten (Ordner) getauscht: weder gelesen noch geloescht, nicht als bad_request
    # gemeldet; der Ordner wird in der naechsten Runde als Ordner behandelt.
    put(root, f"{RID}.json", request_doc())
    put(root, "fremd", b"x")
    swapped = {f"{RID}.json", "fremd"}
    real_stat = os.stat

    def now_a_directory(name, *args, **kwargs):
        st = real_stat(name, *args, **kwargs)
        if name in swapped:
            return os.stat_result((stat.S_IFDIR | 0o755, st.st_ino, st.st_dev, 2, st.st_uid, st.st_gid, 4096,
                                   int(st.st_atime), int(st.st_mtime), int(st.st_ctime)))
        return st

    monkeypatch.setattr(os, "stat", now_a_directory)
    result = poll(ch)
    assert result.items == () and result.removed == 0
    assert sorted(os.listdir(requests_dir(root))) == sorted([f"{RID}.json", "fremd"])


def test_vanished_or_swapped_entries_do_not_count_as_handled(ch, root, monkeypatch):
    # `handled` zaehlt erst, wenn wirklich gelesen oder geloescht wurde: Eintraege, die zwischen Durchlesen und
    # Bearbeiten verschwinden oder zu Ordnern werden, nehmen keinen der 32 Plaetze.
    for i in range(channel.MAX_HANDLE + 15):
        put(root, f"{rid(i)}.json", request_doc(rid(i)))
    ghosts = {f"{rid(i)}.json" for i in range(10)}
    real_stat = os.stat

    def vanishing(name, *args, **kwargs):
        if name in ghosts:
            raise FileNotFoundError(name)
        return real_stat(name, *args, **kwargs)

    monkeypatch.setattr(os, "stat", vanishing)
    result = poll(ch)
    assert len(result.items) == channel.MAX_HANDLE
    assert not {item.id for item in result.items} & {rid(i) for i in range(10)}


def test_foreign_entries_are_removed_but_young_dashboard_tmp_files_stay(ch, root):
    put(root, "notiz.txt", b"x")
    put(root, f"{RID.upper()}.json", request_doc())
    put(root, f"{RID}.json.bak", request_doc())
    (requests_dir(root) / "link").symlink_to("/etc/passwd")
    os.mkfifo(requests_dir(root) / "fifo")
    young = f".{rid(1)}.tmp"
    old = f".{rid(2)}.tmp"
    future = f".{rid(3)}.tmp"
    for name in (young, old, future):
        put(root, name, b"{")
    os.utime(requests_dir(root) / young, (NOW - 30, NOW - 30))
    os.utime(requests_dir(root) / old, (NOW - 601, NOW - 601))
    os.utime(requests_dir(root) / future, (NOW + 3600, NOW + 3600))
    result = poll(ch)
    assert result.items == () and result.removed == 7
    assert os.listdir(requests_dir(root)) == [young]


def test_names_that_are_not_valid_text_are_removed(ch, root):
    bad_name = os.fsdecode(b"\xff\xfe.json")
    fd = os.open(os.path.join(os.fsencode(requests_dir(root)), b"\xff\xfe.json"), os.O_WRONLY | os.O_CREAT, 0o644)
    os.close(fd)
    assert poll(ch).removed == 1
    assert bad_name not in os.listdir(requests_dir(root))


def test_never_writes_into_requests(ch, root, monkeypatch):
    for i in range(3):
        put(root, f"{rid(i)}.json", request_doc(rid(i)))
    put(root, "fremd", b"x")
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    seen = []
    real_open = os.open

    def spy(path, flags, *args, **kwargs):
        seen.append((path, flags))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    poll(ch)
    assert seen, "es wurde gelesen"
    assert all(flags & write_flags == 0 for _, flags in seen)
    assert all(flags & os.O_NOFOLLOW for _, flags in seen)
    file_opens = [flags for path, flags in seen if path != channel.REQUESTS_DIR]
    assert file_opens and all(flags & os.O_NONBLOCK and flags & os.O_CLOEXEC for flags in file_opens)


def test_requests_dir_of_another_owner_is_unsafe_without_root_too(root, uid, monkeypatch):
    # Die Besitzerpruefung an `requests/` (M4) auch ohne root: `fstat` meldet einen anderen Besitzer.
    c = Channel(root, expected_uid=uid)
    c.setup()
    fake_owner(monkeypatch, {requests_dir(root): uid + 1000})
    assert unsafe(c.poll, now=NOW) == "requests_owner"
    assert unsafe(Channel(root, expected_uid=uid).setup) == "requests_owner"


def test_request_files_of_the_dashboard_user_are_read_without_root_too(ch, root, uid, monkeypatch):
    # Im Betrieb gehoeren Anforderungen uid 1000, nicht dem Helfer: der Besitzer einer Anforderung ist egal
    # (Fremdes wird ueber Typ, Links und Groesse abgewehrt). Wuerde `_take` einen Besitzer verlangen, lehnte der
    # Helfer im Betrieb jede Anforderung ab.
    put(root, f"{RID}.json", request_doc())
    fake_owner(monkeypatch, {requests_dir(root) / f"{RID}.json": uid + 1000})
    assert poll(ch).items == (channel.Incoming(id=RID, raw=request_doc()),)


@needs_root
@pytest.mark.skipif(shutil.which("setpriv") is None, reason="braucht setpriv (util-linux)")
def test_operating_profile_root_without_capabilities(root):
    # Das Betriebsbild: Helfer = root OHNE Capabilities (`cap_drop: ALL`), Anforderungen von uid 1000. Eine 0644-Datei
    # liest root auch ohne CAP_DAC_OVERRIDE, eine 0600-Datei nicht (-> bad_request, geloescht); FIFO, Symlink und
    # Hardlink bleiben bad_request. Der Helfer laeuft mit den Vorgaben (`expected_uid=0`), nichts wird ueberschrieben.
    Channel(root).setup()
    requests = requests_dir(root)
    for name, data, mode in ((rid(1), request_doc(rid(1)), 0o644), (rid(2), request_doc(rid(2)), 0o600)):
        (requests / f"{name}.json").write_bytes(data)
        os.chmod(requests / f"{name}.json", mode)
        os.chown(requests / f"{name}.json", 1000, 1000)
    os.mkfifo(requests / f"{rid(3)}.json")
    (requests / f"{rid(4)}.json").symlink_to("/etc/passwd")
    script = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(UPDATER_DIR)!r})
        from nodvard_deck_updater.channel import Channel
        with Channel({str(root)!r}) as ch:
            ch.setup()
            result = ch.poll(now={NOW})
        print(json.dumps([[i.id, i.raw is not None, i.code] for i in result.items]))
    """)
    out = subprocess.run(
        ["setpriv", "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all", sys.executable, "-I", "-c", script],
        capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    got = {entry[0]: entry[1:] for entry in json.loads(out.stdout)}
    assert got == {rid(1): [True, None], rid(2): [False, "bad_request"], rid(3): [False, "bad_request"],
                   rid(4): [False, "bad_request"]}
    assert os.listdir(requests) == []


def test_protocol_vector_files_section_matches_the_channel():
    # Fund 14: der Abschnitt `files` ist der Vertrag fuer das Dashboard (PR 5); weichen Helfer und Vektor ab, bleiben
    # beide Seiten gruen und arbeiten trotzdem nicht zusammen (z. B. 0600 statt 0644, anderes Muster fuer Temp-Dateien).
    files = load_vectors("protocol.json")["files"]
    assert set(files) == {"status", "requests_dir", "request_name_pattern", "dashboard_tmp_pattern", "requests_mode",
                          "status_mode", "request_mode"}
    assert files["status"] == channel.STATUS_NAME
    assert files["requests_dir"] == channel.REQUESTS_DIR
    assert int(files["requests_mode"], 8) == channel.REQUESTS_MODE == 0o1777
    assert int(files["status_mode"], 8) == channel.STATUS_MODE == 0o644
    assert int(files["request_mode"], 8) == channel.REQUEST_MODE == 0o644
    assert files["request_name_pattern"] == policy.UUID4_RE.pattern + r"\.json"
    assert files["dashboard_tmp_pattern"] == r"\." + policy.UUID4_RE.pattern + r"\.tmp"
    # Gleichwertig nicht nur als Text: dieselben Namen passen bzw. passen nicht.
    name_ok = [f"{RID}.json", f"{rid(1)}.json"]
    name_bad = [f"{RID.upper()}.json", f"{RID}.json\n", f".{RID}.json", f"{RID}.json.tmp", f"{RID}", "x.json",
                f"{RID[:14]}1{RID[15:]}.json", f"{RID[:19]}c{RID[20:]}.json", f"{RID}.JSON", f"{RID}\u0660.json"]
    tmp_ok = [f".{RID}.tmp", f".{rid(1)}.tmp"]
    tmp_bad = [f"{RID}.tmp", f".{RID}.json", f".{RID}.tmp\n", f".{RID.upper()}.tmp", f".{RID}.json.tmp", f".{RID}"]
    for pattern, compiled, good, bad in (
            (files["request_name_pattern"], channel.REQUEST_NAME_RE, name_ok, name_bad),
            (files["dashboard_tmp_pattern"], channel.DASHBOARD_TMP_RE, tmp_ok, tmp_bad)):
        for text in good:
            assert re.fullmatch(pattern, text, re.ASCII) and compiled.fullmatch(text), text
        for text in bad:
            assert not re.fullmatch(pattern, text, re.ASCII) and not compiled.fullmatch(text), text


@needs_root
def test_requests_of_another_user_in_the_sticky_dir_are_removed(ch, root):
    put(root, f"{RID}.json", request_doc())
    put(root, "fremd", b"x")
    (requests_dir(root) / "ordner-des-dashboards").mkdir()
    for name in (f"{RID}.json", "fremd", "ordner-des-dashboards"):
        os.chown(requests_dir(root) / name, 1000, 1000)
    result = poll(ch)
    assert result.items == (channel.Incoming(id=RID, raw=request_doc()),)
    assert result.directories == 0
    assert os.listdir(requests_dir(root)) == []


# ---------------------------------------------------------------------------
# Austausch und Grenzen je Runde
# ---------------------------------------------------------------------------


def test_replaced_requests_dir_is_detected(ch, root):
    os.rename(requests_dir(root), root / "alt")
    requests_dir(root).mkdir()
    requests_dir(root).chmod(0o1777)
    put(root, f"{RID}.json", request_doc())
    assert unsafe(ch.poll, now=NOW) == "requests_replaced"
    assert os.listdir(requests_dir(root)) == [f"{RID}.json"], "nichts gelesen, nichts geloescht"


def test_requests_replaced_by_symlink_is_detected(ch, root, tmp_path):
    other = tmp_path / "anderswo"
    other.mkdir(mode=0o1777)
    os.rename(requests_dir(root), root / "alt")
    requests_dir(root).symlink_to(other)
    assert unsafe(ch.poll, now=NOW) == "requests_open"


def test_mode_changes_after_setup_are_detected(ch, root):
    requests_dir(root).chmod(0o777)
    assert unsafe(ch.poll, now=NOW) == "requests_mode"
    requests_dir(root).chmod(0o1777)
    poll(ch)
    root.chmod(0o777)
    assert unsafe(ch.poll, now=NOW) == "root_mode"


def test_at_most_32_requests_per_round(ch, root):
    for i in range(40):
        put(root, f"{rid(i)}.json", request_doc(rid(i)))
    first = poll(ch)
    assert len(first.items) == 32
    second = poll(ch)
    assert len(second.items) == 8
    assert {item.id for item in first.items + second.items} == {rid(i) for i in range(40)}


def test_ten_thousand_entries_do_not_stall_a_round(ch, root):
    for i in range(10_000):
        put(root, f"fremd-{i}", b"")
    for i in range(5):
        put(root, f"{rid(i)}.json", request_doc(rid(i)))
    started = time.monotonic()
    result = poll(ch)
    assert time.monotonic() - started < 5
    assert result.scanned == channel.MAX_SCAN and result.removed <= channel.MAX_SCAN
    found = {item.id for item in result.items}
    rounds = 1
    while len(found) < 5:
        found |= {item.id for item in poll(ch).items}
        rounds += 1
        assert rounds <= 12
    assert found == {rid(i) for i in range(5)}


# ---------------------------------------------------------------------------
# Status (1.5, M4, M17)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", STATUS["valid"], ids=lambda c: c["name"])
def test_status_vectors_valid(case):
    channel.validate_status(case["doc"])


@pytest.mark.parametrize("case", STATUS["invalid"], ids=lambda c: c["name"])
def test_status_vectors_invalid(case):
    with pytest.raises(ValueError):
        channel.validate_status(case["doc"])


def test_status_is_written_atomically_world_readable_and_compact(ch, root):
    doc = STATUS["valid"][0]["doc"]
    ch.write_status(doc)
    path = root / "status.json"
    assert json.loads(path.read_bytes()) == doc
    assert stat.S_IMODE(os.lstat(path).st_mode) == 0o644
    assert path.read_bytes().isascii() and b" " not in path.read_bytes()
    assert sorted(os.listdir(root)) == ["requests", "status.json"]


def test_status_mode_is_0644_whatever_the_umask(ch, root):
    # `mode=0o644` beim Anlegen reicht nicht (die umask nimmt davon weg): nur `fchmod` am Deskriptor sorgt dafuer, dass
    # das Dashboard (uid 1000) den Status lesen kann.
    old = os.umask(0o277)
    try:
        ch.write_status(STATUS["valid"][0]["doc"])
        ch.write_status(STATUS["valid"][1]["doc"], durable=False)
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.lstat(root / "status.json").st_mode) == 0o644


def test_invalid_status_is_never_written(ch, root):
    ch.write_status(STATUS["valid"][0]["doc"])
    before = (root / "status.json").read_bytes()
    with pytest.raises(ValueError):
        ch.write_status({**STATUS["valid"][0]["doc"], "reason": "Engine: permission denied /var/run/x"})
    assert (root / "status.json").read_bytes() == before


def test_status_size_limit(ch, monkeypatch):
    assert len(channel.encode_status(STATUS["valid"][0]["doc"])) > 100
    monkeypatch.setattr(policy, "STATUS_MAX_BYTES", 100)
    with pytest.raises(ValueError):
        ch.write_status(STATUS["valid"][0]["doc"])
    monkeypatch.undo()
    biggest = {**STATUS["valid"][0]["doc"], "results": STATUS["valid"][0]["doc"]["results"] * policy.RESULTS_MAX}
    assert len(channel.encode_status(biggest)) <= policy.STATUS_MAX_BYTES


def test_status_durable_fsyncs_file_and_dir_heartbeat_does_not(ch, monkeypatch):
    kinds = []
    real_fsync = os.fsync

    def spy(fd):
        kinds.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    ch.write_status(STATUS["valid"][0]["doc"])
    assert kinds == ["file", "dir"]
    kinds.clear()
    ch.write_status(STATUS["valid"][0]["doc"], durable=False)
    assert kinds == []


def test_failed_status_replace_keeps_old_status(ch, root, monkeypatch):
    ch.write_status(STATUS["valid"][0]["doc"])
    before = (root / "status.json").read_bytes()

    def broken_replace(*args, **kwargs):
        raise OSError(28, "kein Platz")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        ch.write_status(STATUS["valid"][1]["doc"])
    monkeypatch.undo()
    assert (root / "status.json").read_bytes() == before
    assert sorted(os.listdir(root)) == ["requests", "status.json"]


def test_planted_tmp_symlink_for_status_is_never_followed(ch, root, tmp_path, monkeypatch):
    victim = tmp_path / "victim"
    victim.write_text("unberuehrt")
    monkeypatch.setattr(state.secrets, "token_hex", lambda n: "0123456789abcdef")
    (root / ".status-0123456789abcdef.tmp").symlink_to(victim)
    with pytest.raises(FileExistsError):
        ch.write_status(STATUS["valid"][0]["doc"])
    assert victim.read_text() == "unberuehrt"
    assert not (root / "status.json").exists()


def test_status_json_symlink_is_replaced_not_followed(ch, root, tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("unberuehrt")
    (root / "status.json").symlink_to(victim)
    ch.write_status(STATUS["valid"][0]["doc"])
    assert victim.read_text() == "unberuehrt"
    assert stat.S_ISREG(os.lstat(root / "status.json").st_mode)


def test_stale_status_tmp_files_are_removed_on_setup(root, uid):
    root.joinpath(".status-0123456789abcdef.tmp").write_text("halb")
    root.joinpath("andere-datei").write_text("bleibt")
    Channel(root, expected_uid=uid).setup()
    assert sorted(os.listdir(root)) == ["andere-datei", "requests"]


def test_status_can_still_report_an_unsafe_root(root, uid):
    root.chmod(0o775)
    c = Channel(root, expected_uid=uid)
    assert unsafe(c.setup) == "root_mode"
    doc = STATUS["valid"][2]["doc"]
    assert doc["reason"] == "channel_unsafe"
    c.write_status(doc)
    assert json.loads((root / "status.json").read_text())["reason"] == "channel_unsafe"
    assert unsafe(c.poll, now=NOW) == "not_ready"


def test_write_status_without_root_is_refused(tmp_path, uid):
    c = Channel(tmp_path / "fehlt", expected_uid=uid)
    assert unsafe(c.setup) == "root_open"
    assert unsafe(c.write_status, STATUS["valid"][0]["doc"]) == "not_open"
