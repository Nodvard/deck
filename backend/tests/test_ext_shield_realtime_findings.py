"""Viren-Wächter in Echtzeit: Funde aus dem Protokoll von clamonacc abholen, über clamd gegenprüfen und wie beim
Minuten-Wächter behandeln (Hilfsskript `shield-root` ab Version 12).

Gleichlauf: Der Befehl des Moduls und das Skript laufen gegen dasselbe Protokoll und dieselbe Attrappe von clamdscan;
Ausgabe und Rückgabewert müssen gleich sein."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from shield_helper_kit import EXT_DIR, SH, helper_copy, stubs

needs_sh = pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None, reason="braucht eine echte POSIX-Shell")

EICAR = "Win.Test.EICAR_HDB-1"


@pytest.fixture(autouse=True)
def _ext_shield_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


def _rt():
    from nodvard_deck_ext_shield import realtime

    return realtime


def _av():
    from nodvard_deck_ext_shield import antivirus

    return antivirus


def _log(start: int, lines: list[str], *, active: bool = True) -> str:
    head = f"@@active={'yes' if active else 'no'}\n@@lines={start}-{start + len(lines)}\n"
    return head + "".join(line + "\n" for line in lines) + "@@end\n"


# --- Auswertung ---------------------------------------------------------------------------------------------------


def test_parse_log_reads_header_body_and_the_last_end():
    rt = _rt()
    chunk = rt.parse_log(_log(4, ["/tmp/a: X FOUND", "ScanOnAccess: started"], active=False))
    assert (chunk.active, chunk.start, chunk.end, chunk.lines) == (False, 4, 6, ["/tmp/a: X FOUND", "ScanOnAccess: started"])
    # Ein Dateiname kann eine Zeile "@@end" ins Protokoll bringen; das Ende ist die letzte.
    fake = rt.parse_log(_log(0, ["/tmp/x", "@@end"]))
    assert fake.lines == ["/tmp/x", "@@end"] and fake.end == 2
    assert rt.parse_log(_log(0, [])).lines == []


@pytest.mark.parametrize("output", [
    "",
    "@@active=yes\n@@lines=0-1\n/tmp/a\n",
    "@@active=vielleicht\n@@lines=0-0\n@@end\n",
    "@@active=yes\n@@lines=0-2\n/tmp/a\n@@end\n",
    "@@active=yes\n@@lines=3-1\n@@end\n",
    "/tmp/a\n@@active=yes\n@@lines=0-0\n@@end\n",
])
def test_parse_log_refuses_incomplete_or_odd_answers(output):
    assert _rt().parse_log(output) is None


def test_found_lines_takes_each_path_once_and_ignores_the_rest():
    rt = _rt()
    lines = [
        f"/tmp/eicar.com: {EICAR} FOUND",
        f"Tue Oct 14 13:00:01 2026 -> /home/anna/Downloads/a: b.txt: {EICAR} FOUND",
        "/tmp/eicar.com: Other.Sig FOUND",
        "ERROR: Can't access file /tmp/x",
        "/tmp/ok: OK",
        f"/tmp/../etc/passwd: {EICAR} FOUND",
        f"relativ: {EICAR} FOUND",
    ]
    assert rt.found_lines(lines) == [("/tmp/eicar.com", EICAR), ("/home/anna/Downloads/a: b.txt", EICAR)]


def test_parse_confirm_only_counts_asked_paths_and_needs_a_usable_return_code():
    rt = _rt()
    out = f"/tmp/a: {EICAR} FOUND\n/tmp/b: OK\n/etc/shadow: {EICAR} FOUND\n@@rc=1\n"
    assert rt.parse_confirm(out, ["/tmp/a", "/tmp/b"]) == {"/tmp/a": EICAR}
    assert rt.parse_confirm("/tmp/b: OK\n@@rc=0\n", ["/tmp/b"]) == {}
    assert rt.parse_confirm("ERROR: Could not connect to clamd\n@@rc=2\n", ["/tmp/a"]) is None
    assert rt.parse_confirm(f"/tmp/a: {EICAR} FOUND\n", ["/tmp/a"]) is None


def test_commands_refuse_bad_input():
    rt = _rt()
    for line in (-1, True, "3", rt.MAX_LINE + 1):
        with pytest.raises(ValueError):
            rt.log_command(line)
    for paths in ([], [f"/tmp/{i}" for i in range(51)], ["tmp/a"], ["/tmp/a\nb"]):
        with pytest.raises(ValueError):
            rt.confirm_command(paths)


def test_outside_watch():
    rt = _rt()
    assert not rt.outside_watch("/home/a/b", ["/home/"], [])
    assert rt.outside_watch("/homeoffice/b", ["/home"], [])
    assert rt.outside_watch("/home/cache/x", ["/home"], ["/home/cache"])
    assert not rt.outside_watch("/srv/x", ["/"], [])


# --- Echter Lauf: Befehl des Moduls und Hilfsskript ---------------------------------------------------------------

SYSTEMCTL = "[ -e {flag} ]\n"
CLAMDSCAN = """
rc=0
for f in "$@"; do
    case $f in
        --*) continue ;;
    esac
    if [ -e {down} ]; then echo "ERROR: Could not connect to clamd on LocalSocket"; exit 2; fi
    if grep -q EICAR "$f" 2>/dev/null; then echo "$f: Win.Test.EICAR_HDB-1 FOUND"; rc=1; else echo "$f: OK"; fi
done
exit $rc
"""


def _side(base: Path, *, active: bool = True, clamd_down: bool = False):
    flag, down = base / "active", base / "down"
    base.mkdir(parents=True)
    if active:
        flag.touch()
    if clamd_down:
        down.touch()
    bin_dir = stubs(base, {"systemctl": SYSTEMCTL.format(flag=flag), "clamdscan": CLAMDSCAN.format(down=down)})
    sdmark = base / "sd"
    sdmark.mkdir()
    path = f"{bin_dir}:/usr/bin:/bin"
    return SimpleNamespace(base=base, path=path, log=base / "onaccess.log",
                           helper=helper_copy(base, path=path, sdmark=sdmark))


def _run(side, command: str | None, args: list[str] | None) -> tuple[int, str]:
    if command is not None:
        done = subprocess.run([SH, "-c", command], capture_output=True, text=True, timeout=60, check=False,
                              env={"PATH": side.path, "LC_ALL": "C"})
        return done.returncode, done.stdout
    done = subprocess.run([SH, str(side.helper), *args], capture_output=True, text=True, timeout=60, check=False,
                          env={"LC_ALL": "C"})
    return done.returncode, _av().helper_text(done.stdout)


def _log_both(tmp_path, content: str | None, line: int, **kwargs) -> str:
    rt = _rt()
    module, helper = _side(tmp_path / "modul", **kwargs), _side(tmp_path / "skript", **kwargs)
    for side in (module, helper):
        if content is not None:
            side.log.write_text(content)
    got_m = _run(module, rt.log_command(line, log_path=str(module.log), batch=3), None)
    got_h = _run(helper, None, ["realtime-log", str(line)])
    assert got_h == got_m
    assert got_m[0] == 0
    return got_m[1]


LOG = "".join(f"zeile {i}\n" for i in range(1, 6))


@needs_sh
@pytest.mark.parametrize(("content", "line", "start", "lines"), [
    (None, 0, 0, []),
    (LOG, 0, 0, ["zeile 1", "zeile 2", "zeile 3"]),
    (LOG, 3, 3, ["zeile 4", "zeile 5"]),
    (LOG, 5, 5, []),
    # Weniger Zeilen als gemerkt: neues Protokoll, also von vorn.
    (LOG, 9, 0, ["zeile 1", "zeile 2", "zeile 3"]),
    # Die Zeile, die clamonacc gerade schreibt (ohne Zeilenende), kommt erst beim nächsten Mal.
    ("a\nb\nhalb", 0, 0, ["a", "b"]),
    ("/tmp/x\n@@end\n/tmp/y\n", 0, 0, ["/tmp/x", "@@end", "/tmp/y"]),
])
def test_reading_the_log_is_the_same_in_module_and_helper(tmp_path, content, line, start, lines):
    out = _log_both(tmp_path, content, line)
    chunk = _rt().parse_log(out)
    assert (chunk.active, chunk.start, chunk.lines) == (True, start, lines), out


@needs_sh
def test_the_log_says_when_clamonacc_is_not_running(tmp_path):
    chunk = _rt().parse_log(_log_both(tmp_path, LOG, 5, active=False))
    assert (chunk.active, chunk.lines) == (False, [])


def _confirm_both(tmp_path, paths: list[str], **kwargs) -> tuple[int, str]:
    rt = _rt()
    module, helper = _side(tmp_path / "modul", **kwargs), _side(tmp_path / "skript", **kwargs)
    got_m = _run(module, rt.confirm_command(paths), None)
    got_h = _run(helper, None, ["realtime-confirm", *paths])
    assert got_h == got_m
    return got_m


@needs_sh
def test_the_confirmation_is_the_same_in_module_and_helper(tmp_path):
    files = tmp_path / "dateien"
    files.mkdir()
    (files / "eicar.com").write_text("X5O!P%@AP EICAR\n")
    (files / "sauber .txt").write_text("hallo\n")
    paths = [str(files / "eicar.com"), str(files / "sauber .txt")]
    rc, out = _confirm_both(tmp_path, paths)
    assert rc == 0 and _rt().parse_confirm(out, paths) == {paths[0]: EICAR}

    rc, out = _confirm_both(tmp_path / "zwei", paths, clamd_down=True)
    assert rc == 0 and _rt().parse_confirm(out, paths) is None


@needs_sh
@pytest.mark.parametrize("args", [
    ["realtime-log"],
    ["realtime-log", "01"],
    ["realtime-log", "-1"],
    ["realtime-log", "x"],
    ["realtime-log", "1234567890123"],
    ["realtime-log", "1", "2"],
    ["realtime-confirm"],
    ["realtime-confirm", "tmp/a"],
    ["realtime-confirm", "/tmp/a\nb"],
    ["realtime-confirm", "/tmp/../etc/shadow"],
    ["realtime-confirm", *[f"/tmp/{i}" for i in range(51)]],
])
def test_the_helper_refuses_bad_arguments(tmp_path, args):
    side = _side(tmp_path / "seite")
    done = subprocess.run([SH, str(side.helper), *args], capture_output=True, text=True, timeout=60, check=False,
                          env={"LC_ALL": "C"})
    assert done.returncode == 64 and done.stderr.startswith("shield-root: "), (done.returncode, done.stderr)


# --- Wächter-Lauf im Defender -------------------------------------------------------------------------------------


class _Ctx:
    """Attrappe: Antworten je Teil des Befehls (`realtime-log`, `realtime-confirm`, Quarantäne), alles mitgeschrieben."""

    def __init__(self, sessionmaker) -> None:
        from nodvard_sdk import Host

        self._sm = sessionmaker
        self.host = Host(id="h1", name="pi", display_name="Raspberry Pi", address="10.0.0.2")
        self.answers: dict[str, str] = {}
        self.commands: list[str] = []
        self.notes: list = []
        self.audits: list = []
        self.settings_value = {"auto_quarantine": True, "watch_paths": ["/tmp", "/home"]}
        self.settings = SimpleNamespace(get=self._settings)
        self.hosts = SimpleNamespace(list=self._list, get=self._get)
        self.exec = SimpleNamespace(run=self._run)
        self.db = SimpleNamespace(session=self._session)
        self.audit = SimpleNamespace(log=self._audit)
        self.notify = SimpleNamespace(send=self._notify)
        self.ws = SimpleNamespace(broadcast=self._noop)

    async def _settings(self):
        return self.settings_value

    async def _list(self, tag=None):
        return [self.host]

    async def _get(self, host_id):
        return self.host if host_id == "h1" else None

    async def _run(self, host, command, timeout_s=60):
        self.commands.append(command)
        for key, out in self.answers.items():
            if key in command:
                return SimpleNamespace(exit_code=0, stdout=out, stderr="", duration_ms=5)
        return SimpleNamespace(exit_code=0, stdout="", stderr="", duration_ms=5)

    @asynccontextmanager
    async def _session(self):
        async with self._sm() as s:
            yield s
            await s.commit()

    async def _audit(self, **kw):
        self.audits.append(kw)

    async def _notify(self, n):
        self.notes.append(n)

    async def _noop(self, *a, **k):
        return None


@pytest.fixture
async def shield():
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ctx = _Ctx(async_sessionmaker(engine, expire_on_commit=False))
    yield SimpleNamespace(ctx=ctx, defender=Defender(ctx))
    await engine.dispose()


async def _realtime(shield, enabled: bool = True, line: int | None = None) -> None:
    from nodvard_deck_ext_shield.patching import save_baseline

    await save_baseline(shield.ctx, "h1", _rt().STATE_KIND, {"enabled": enabled})
    if line is not None:
        await save_baseline(shield.ctx, "h1", _rt().POSITION_KIND, {"line": line})


async def _watch(shield) -> dict:
    await shield.defender.start_scans([shield.ctx.host], "watch", paths=None, trigger="schedule")
    for _ in range(200):
        await asyncio.sleep(0.01)
        if not shield.defender.is_running("h1", "watch"):
            break
    return (await shield.defender.list_scans(include_watch=True))[0]


async def _position(shield) -> int | None:
    from nodvard_deck_ext_shield.patching import load_baseline

    return ((await load_baseline(shield.ctx, "h1", _rt().POSITION_KIND)) or {}).get("line")


def _minute_runs(ctx) -> int:
    return sum("realtime" not in c and ("--file-list" in c or " watch " in c) for c in ctx.commands)


@pytest.mark.asyncio
async def test_a_confirmed_finding_is_quarantined_and_reported_like_the_watch_does(shield):
    await _realtime(shield)
    ctx = shield.ctx
    ctx.answers = {
        "realtime-log": _log(0, ["ScanOnAccess: started", f"/tmp/eicar.com: {EICAR} FOUND"]),
        "realtime-confirm": f"/tmp/eicar.com: {EICAR} FOUND\n@@rc=1\n",
        "mv -f": "@@mode=644\n",
    }
    scan = await _watch(shield)
    assert (scan["kind"], scan["status"], scan["infected"]) == ("watch", "infected", 1)
    findings = await shield.defender.list_findings()
    assert [(f["path"], f["signature"], f["status"]) for f in findings] == [("/tmp/eicar.com", EICAR, "quarantined")]
    assert ctx.notes and "1 in Quarantäne" in ctx.notes[0].body
    assert await _position(shield) == 2
    assert _minute_runs(ctx) == 0
    av, rt = _av(), _rt()
    version = av.HELPER_REALTIME_LOG_VERSION
    assert ctx.commands[0] == av.via_helper(["realtime-log", "0"], rt.log_command(0), version=version)
    assert ctx.commands[1] == av.via_helper(
        ["realtime-confirm", "/tmp/eicar.com"], rt.confirm_command(["/tmp/eicar.com"]), version=version,
    )


@pytest.mark.asyncio
async def test_the_next_run_starts_where_the_last_one_stopped(shield):
    await _realtime(shield, line=40)
    shield.ctx.answers = {"realtime-log": _log(40, ["ScanOnAccess: läuft"])}
    scan = await _watch(shield)
    assert scan["status"] == "clean" and await _position(shield) == 41
    assert "realtime-log 40" in shield.ctx.commands[0]
    assert not any("realtime-confirm" in c for c in shield.ctx.commands)


@pytest.mark.asyncio
async def test_a_line_that_the_confirmation_does_not_find_again_is_no_finding(shield):
    """Ein Dateiname mit Zeilenumbruch kann im Protokoll einen Fund für eine fremde Datei vortäuschen."""
    await _realtime(shield)
    shield.ctx.answers = {
        "realtime-log": _log(0, ["/tmp/x", f"/home/anna/.ssh/authorized_keys: {EICAR} FOUND"]),
        "realtime-confirm": "/home/anna/.ssh/authorized_keys: OK\n@@rc=0\n",
    }
    scan = await _watch(shield)
    assert (scan["status"], scan["infected"]) == ("clean", 0)
    assert await shield.defender.list_findings() == []
    assert not any("mv -f" in c for c in shield.ctx.commands) and shield.ctx.notes == []


@pytest.mark.asyncio
async def test_findings_outside_the_watched_folders_are_kept_but_never_moved(shield):
    from nodvard_deck_ext_shield.defender import REALTIME_OUTSIDE

    await _realtime(shield)
    shield.ctx.answers = {
        "realtime-log": _log(0, [f"/srv/upload/a.exe: {EICAR} FOUND"]),
        "realtime-confirm": f"/srv/upload/a.exe: {EICAR} FOUND\n@@rc=1\n",
    }
    await _watch(shield)
    findings = await shield.defender.list_findings()
    assert [(f["path"], f["status"]) for f in findings] == [("/srv/upload/a.exe", "detected")]
    assert not any("mv -f" in c for c in shield.ctx.commands)
    assert findings[0]["note"] == REALTIME_OUTSIDE and shield.ctx.notes[0].body.endswith(REALTIME_OUTSIDE)


@pytest.mark.asyncio
async def test_without_a_confirmation_the_findings_stay_with_a_note(shield):
    from nodvard_deck_ext_shield.defender import REALTIME_UNCHECKED

    await _realtime(shield)
    shield.ctx.answers = {
        "realtime-log": _log(0, [f"/tmp/eicar.com: {EICAR} FOUND"]),
        "realtime-confirm": "ERROR: Could not connect to clamd\n@@rc=2\n",
    }
    scan = await _watch(shield)
    assert scan["status"] == "infected" and scan["error"] == REALTIME_UNCHECKED
    findings = await shield.defender.list_findings()
    assert [(f["path"], f["status"], f["note"]) for f in findings] == [("/tmp/eicar.com", "detected", REALTIME_UNCHECKED)]
    assert not any("mv -f" in c for c in shield.ctx.commands)


@pytest.mark.asyncio
async def test_finding_lines_in_an_unknown_format_are_a_visible_error(shield):
    from nodvard_deck_ext_shield.defender import REALTIME_UNREADABLE

    await _realtime(shield)
    shield.ctx.answers = {"realtime-log": _log(0, ["ScanOnAccess: Eicar mit Leerzeichen FOUND", "fd[7]: Eicar FOUND"])}
    scan = await _watch(shield)
    assert (scan["status"], scan["error"]) == ("error", REALTIME_UNREADABLE.format(count=2))
    assert await _position(shield) == 2 and await shield.defender.list_findings() == []


@pytest.mark.asyncio
async def test_many_findings_are_confirmed_in_batches(shield):
    await _realtime(shield)
    paths = [f"/tmp/f{i}" for i in range(60)]
    shield.ctx.answers = {
        "realtime-log": _log(0, [f"{p}: {EICAR} FOUND" for p in paths]),
        "realtime-confirm": "".join(f"{p}: {EICAR} FOUND\n" for p in paths) + "@@rc=1\n",
        "mv -f": "@@mode=644\n",
    }
    scan = await _watch(shield)
    assert scan["infected"] == 60
    assert sum("realtime-confirm" in c for c in shield.ctx.commands) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("enabled", "answer"), [
    (False, None),
    # clamonacc läuft nicht und das Protokoll ist abgeholt: dieser Lauf wie bisher im Minuten-Takt.
    (True, _log(7, [], active=False)),
])
async def test_otherwise_the_minute_watch_runs_as_before(shield, enabled, answer):
    await _realtime(shield, enabled=enabled, line=7)
    if answer is not None:
        shield.ctx.answers = {"realtime-log": answer}
    await _watch(shield)
    assert _minute_runs(shield.ctx) == 1
    assert sum("realtime-log" in c for c in shield.ctx.commands) == (1 if enabled else 0)
    assert await _position(shield) == 7


@pytest.mark.asyncio
async def test_lines_written_before_clamonacc_stopped_are_still_collected(shield):
    await _realtime(shield)
    shield.ctx.answers = {
        "realtime-log": _log(0, [f"/tmp/eicar.com: {EICAR} FOUND"], active=False),
        "realtime-confirm": f"/tmp/eicar.com: {EICAR} FOUND\n@@rc=1\n",
        "mv -f": "@@mode=644\n",
    }
    scan = await _watch(shield)
    assert scan["infected"] == 1 and _minute_runs(shield.ctx) == 0 and await _position(shield) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("answer", "error"), [
    ("@@noroot\n", "NO_ROOT"),
    ("@@active=yes\n@@lines=0-1\n", "BROKEN"),
])
async def test_a_failed_read_is_an_error_row_and_keeps_the_position(shield, answer, error):
    from nodvard_deck_ext_shield.defender import REALTIME_LOG_BROKEN

    await _realtime(shield, line=3)
    shield.ctx.answers = {"realtime-log": answer}
    scan = await _watch(shield)
    assert scan["status"] == "error"
    assert scan["error"] == (_av().NO_ROOT_MESSAGE if error == "NO_ROOT" else REALTIME_LOG_BROKEN)
    assert await _position(shield) == 3 and _minute_runs(shield.ctx) == 0
