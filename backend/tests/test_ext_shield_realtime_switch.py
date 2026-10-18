"""Viren-Wächter in Echtzeit ein- und ausschalten: Befehl, Hilfsskript `shield-root` (ab Version 11), Route und Ausführung.

Gleichlauf: Der Befehl des Moduls und das Skript laufen gegen gleich vorbereitete Ordner mit Attrappen für systemctl,
journalctl, clamd und clamonacc. Ausgabe (nach `helper_text`), Rückgabewert, geschriebene Dateien und die Aufrufe von
systemctl müssen gleich sein."""

from __future__ import annotations

import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from shield_helper_kit import EXT_DIR, SH, helper_copy, stubs

from nodvard_deck.core.deny_patterns import match_deny_patterns

needs_sh = pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None, reason="braucht eine echte POSIX-Shell")

WATCH = ["/home", "/srv/Neue Dateien", "/opt/it's"]


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


# --- Ordner und Befehl als Text -----------------------------------------------------------------------------------


def test_clean_paths_keeps_order_and_drops_doubles_and_trailing_slashes():
    rt = _rt()
    assert rt.clean_paths(["/home/", "/srv/a b", "/home", "/"]) == ["/home", "/srv/a b", "/"]
    assert rt.clean_paths([]) == []


@pytest.mark.parametrize("bad", ["home", "/srv/../etc", "/srv/..", "/a\nb", "/a\rb", "/a\x00b", "/" + "a" * 4096, 7])
def test_clean_paths_refuses_what_could_break_a_list_file(bad):
    with pytest.raises(ValueError, match="Ungültiger Ordner"):
        _rt().clean_paths([bad])


def test_the_enable_command_never_blocks_or_moves_and_quotes_every_folder():
    rt = _rt()
    command = rt.enable_command(WATCH, [], max_filesize_mb=50)
    assert "--fdpass" in command and "--move" not in command and "--remove" not in command
    wanted = ("'OnAccessPrevention no'", "'OnAccessMaxFileSize 50M'", "'OnAccessExtraScanning yes'", "'LogFileMaxSize 0'")
    for line in wanted:
        assert line in command
    assert "--exclude-list" not in command and 'rm -f "$exclude_file"' in command
    assert "'/srv/Neue Dateien'" in command and "'/opt/it'\"'\"'s'" in command
    # Die Konfiguration des Pakets wird nur gelesen.
    assert "clamd_conf=/etc/clamav/clamd.conf" in command and '> "$clamd_conf"' not in command
    assert "--exclude-list=$exclude_file" in rt.enable_command(WATCH, ["/home/cache"], max_filesize_mb=50)
    for text in (command, rt.disable_command()):
        assert match_deny_patterns(text) is None


@pytest.mark.parametrize(("watch", "size", "message"), [
    ([], 50, "Keine Ordner"),
    (["/home"], 0, "Dateigröße"),
    (["/home"], 1001, "Dateigröße"),
    (["/home"], True, "Dateigröße"),
    (["/srv/../etc"], 50, "Ungültiger Ordner"),
])
def test_the_enable_command_refuses_bad_input(watch, size, message):
    with pytest.raises(ValueError, match=message):
        _rt().enable_command(watch, [], max_filesize_mb=size)


def test_helper_args_carry_the_cleaned_folders():
    rt = _rt()
    assert rt.helper_args(True, ["/home/", "/home"], ["/home/cache/"], max_filesize_mb=50) == [
        "realtime-on", "50", "/home/cache", "--", "/home",
    ]
    assert rt.helper_args(False, ["kaputt"], [], max_filesize_mb=0) == ["realtime-off"]


def test_switched_needs_the_mark_on_its_own_line():
    rt = _rt()
    assert rt.switched("bla\n@@realtime-on\n", True)
    assert not rt.switched("@@realtime-on\n", False)
    assert not rt.switched("x@@realtime-on\n", True)


# --- Echter Lauf: Befehl des Moduls und Hilfsskript ---------------------------------------------------------------

# systemctl als Attrappe: Zustände als Dateien `<units>/active.<unit>` und `<units>/enabled.<unit>`, jeder Aufruf im Log.
# Mit `<units>/never.<unit>` wird die Unit nach einem Neustart nie aktiv. `show -p MainPID --value` nennt 4242, solange sie
# läuft; mit `dies.<unit>` ist sie beim zweiten Nachsehen weg (PID 0), mit `loops.<unit>` hat sie dann eine neue PID, mit
# `slow.<unit>` ist sie erst ab zwei echten Sekunden nach dem Neustart weg.
SYSTEMCTL = """
echo "$*" >> {log}
D={units}
case $1 in
    is-active) [ -e "$D/active.$3" ] ;;
    is-enabled) [ -e "$D/enabled.$3" ] ;;
    enable)
        if [ "$2" = --now ]; then touch "$D/enabled.$3" "$D/active.$3"; else touch "$D/enabled.$2"; fi ;;
    disable)
        if [ "$2" = --now ]; then rm -f "$D/enabled.$3" "$D/active.$3" "$D/shown.$3"; else rm -f "$D/enabled.$2"; fi ;;
    restart)
        rm -f "$D/shown.$2"; [ ! -e "$D/slow.$2" ] || date +%s > "$D/started.$2"
        [ -e "$D/never.$2" ] || touch "$D/active.$2" ;;
    show)
        if [ ! -e "$D/active.$5" ]; then echo 0
        elif [ ! -e "$D/shown.$5" ]; then touch "$D/shown.$5"; echo 4242
        elif [ -e "$D/slow.$5" ] && [ $(( $(date +%s) - $(cat "$D/started.$5") )) -ge 2 ]; then rm -f "$D/active.$5"; echo 0
        elif [ -e "$D/slow.$5" ]; then echo 4242
        elif [ -e "$D/dies.$5" ]; then rm -f "$D/active.$5"; echo 0
        elif [ -e "$D/loops.$5" ]; then echo 4243
        else echo 4242; fi ;;
    stop) rm -f "$D/active.$2" ;;
    daemon-reload) ;;
    *) exit 1 ;;
esac
"""


def _side(base: Path, *, clamonacc: bool = True, systemd: bool = True, units: tuple[str, ...] = (), settle: int = 0):
    """Ordner einer Seite: Attrappen, Zustand der Units, ein /etc mit der Konfiguration des Pakets."""
    log, unit_dir = base / "systemctl.log", base / "units"
    unit_dir.mkdir(parents=True)
    for name in units:
        (unit_dir / name).touch()
    tools = {
        "systemctl": SYSTEMCTL.format(log=log, units=unit_dir),
        "journalctl": "echo 'clamonacc[1]: ERROR: Could not connect to clamd'\n",
        "clamd": "exit 0\n",
    }
    if clamonacc:
        tools["clamonacc"] = "exit 0\n"
    bin_dir = stubs(base, tools)
    (base / "etc" / "clamav").mkdir(parents=True)
    (base / "etc" / "clamav" / "clamd.conf").write_text("# Paket\nLocalSocket /var/run/clamav/clamd.ctl\nUser clamav\n")
    sdmark = base / "sd"
    if systemd:
        sdmark.mkdir()
    return SimpleNamespace(base=base, log=log, units=unit_dir, path=f"{bin_dir}:/usr/bin:/bin", sdmark=sdmark,
                           helper=helper_copy(base, path=f"{bin_dir}:/usr/bin:/bin", sdmark=sdmark, rt_settle=settle))


def _run_module(side, command: str) -> tuple[int, str]:
    # Ohne die echten sbin-Ordner der Testmaschine: Dort könnte ein echtes clamd liegen.
    empty = side.base / "no-sbin"
    empty.mkdir(exist_ok=True)
    command = command.replace("/usr/local/sbin:/usr/sbin:/sbin", str(empty))
    done = subprocess.run([SH, "-c", command], capture_output=True, text=True, timeout=60, check=False,
                          env={"PATH": side.path, "LC_ALL": "C"})
    return done.returncode, done.stdout


def _run_helper(side, *args: str) -> tuple[int, str]:
    done = subprocess.run([SH, str(side.helper), *args], capture_output=True, text=True, timeout=60, check=False,
                          env={"LC_ALL": "C"})
    return done.returncode, _av().helper_text(done.stdout)


def _state(side) -> dict:
    """Was auf der Seite danach da ist, mit `<B>` statt des eigenen Ordners."""
    def norm(text: str) -> str:
        return text.replace(str(side.base), "<B>")

    etc = side.base / "etc"
    files = {norm(str(p)): norm(p.read_text()) for p in sorted(etc.rglob("*")) if p.is_file()}
    dirs = sorted(norm(str(p)) for p in etc.rglob("*") if p.is_dir())
    calls = side.log.read_text().splitlines() if side.log.exists() else []
    return {"files": files, "dirs": dirs, "calls": calls, "units": sorted(p.name for p in side.units.iterdir())}


def _both(tmp_path, steps, **side_kwargs):
    """Führt dieselben Schritte (`("on", watch, exclude, mb)` oder `("off",)`) auf beiden Seiten aus und vergleicht.
    `settle=<s>` wartet nach dem Start echt so viele Sekunden (sonst sofort)."""
    rt = _rt()
    module, helper = _side(tmp_path / "modul", **side_kwargs), _side(tmp_path / "skript", **side_kwargs)
    results = []
    for step in steps:
        if step[0] == "on":
            _, watch, exclude, mb = step
            command = rt.enable_command(watch, exclude, max_filesize_mb=mb, etc=str(module.base / "etc"),
                                        systemd_dir=str(module.sdmark), wait_s=4, settle_s=side_kwargs.get("settle", 0),
                                        log_path=str(module.base / "onaccess.log"))
            args = rt.helper_args(True, watch, exclude, max_filesize_mb=mb)
        else:
            command = rt.disable_command(etc=str(module.base / "etc"))
            args = rt.helper_args(False, [], [], max_filesize_mb=50)
        rc_m, out_m = _run_module(module, command)
        rc_h, out_h = _run_helper(helper, *args)
        out_m, out_h = out_m.replace(str(module.base), "<B>"), out_h.replace(str(helper.base), "<B>")
        assert (rc_h, out_h) == (rc_m, out_m), step
        assert _state(helper) == _state(module), step
        results.append((rc_m, out_m, _state(module)))
    return results


@needs_sh
def test_switching_on_writes_our_files_and_remembers_how_clamd_was(tmp_path):
    rc, out, state = _both(tmp_path, [("on", WATCH, [], 50)])[0]
    assert rc == 0 and out.splitlines()[-1] == "@@realtime-on", out
    files = state["files"]
    assert files["<B>/etc/clamav/nodvard-onaccess.conf"] == (
        "LocalSocket /var/run/clamav/clamd.ctl\nOnAccessExcludeUname clamav\nOnAccessMaxFileSize 50M\n"
        "OnAccessPrevention no\nOnAccessExtraScanning yes\nLogFileMaxSize 0\n"
    )
    assert files["<B>/etc/clamav/nodvard-onaccess.watch"] == "/home\n/srv/Neue Dateien\n/opt/it's\n"
    assert files["<B>/etc/clamav/nodvard-onaccess.state"] == "clamd_active=no\nclamd_enabled=no\n"
    assert files["<B>/etc/systemd/system/clamav-clamonacc.service.d/nodvard.conf"] == (
        "[Service]\nExecStart=\nExecStart=<B>/bin/clamonacc -F --fdpass --config-file=<B>/etc/clamav/nodvard-onaccess.conf "
        "--watch-list=<B>/etc/clamav/nodvard-onaccess.watch --log=<B>/onaccess.log\n"
    )
    assert "<B>/etc/clamav/nodvard-onaccess.exclude" not in files
    # Die Konfiguration des Pakets bleibt, wie sie war.
    assert files["<B>/etc/clamav/clamd.conf"].startswith("# Paket\n")
    # Nach dem Start noch einmal nachsehen: dieselbe PID wie direkt danach.
    assert state["calls"][-8:] == [
        "daemon-reload", "enable --now clamav-daemon", "enable clamav-clamonacc", "restart clamav-clamonacc",
        "is-active --quiet clamav-clamonacc", "is-active --quiet clamav-clamonacc",
        "show -p MainPID --value clamav-clamonacc", "show -p MainPID --value clamav-clamonacc",
    ]


@needs_sh
def test_switching_on_twice_keeps_the_first_state_and_off_restores_it(tmp_path):
    results = _both(tmp_path, [("on", WATCH, ["/home/cache"], 20), ("on", ["/home"], [], 20), ("off",)])
    first, second, off = (r[2] for r in results)
    state_file = "<B>/etc/clamav/nodvard-onaccess.state"
    assert first["files"]["<B>/etc/clamav/nodvard-onaccess.exclude"] == "/home/cache\n"
    assert "--exclude-list=<B>/etc/clamav/nodvard-onaccess.exclude" in first["files"][
        "<B>/etc/systemd/system/clamav-clamonacc.service.d/nodvard.conf"]
    # Beim zweiten Mal lief clamd schon; gemerkt bleibt der Stand von vor dem ersten Mal.
    assert second["files"][state_file] == "clamd_active=no\nclamd_enabled=no\n"
    assert "<B>/etc/clamav/nodvard-onaccess.exclude" not in second["files"]
    assert results[2][0] == 0 and results[2][1].splitlines()[-1] == "@@realtime-off"
    assert list(off["files"]) == ["<B>/etc/clamav/clamd.conf"]
    assert "<B>/etc/systemd/system/clamav-clamonacc.service.d" not in off["dirs"]
    calls = off["calls"][len(second["calls"]):]
    assert calls == ["disable --now clamav-clamonacc", "daemon-reload", "stop clamav-daemon", "disable clamav-daemon"]
    assert off["units"] == []


@needs_sh
def test_off_leaves_a_clamd_alone_that_ran_before(tmp_path):
    results = _both(tmp_path, [("on", ["/home"], [], 50), ("off",)],
                    units=("active.clamav-daemon", "enabled.clamav-daemon"))
    assert results[0][2]["files"]["<B>/etc/clamav/nodvard-onaccess.state"] == "clamd_active=yes\nclamd_enabled=yes\n"
    off = results[1][2]
    assert "stop clamav-daemon" not in off["calls"] and "disable clamav-daemon" not in off["calls"]
    assert off["units"] == ["active.clamav-daemon", "enabled.clamav-daemon"]


@needs_sh
def test_off_without_our_state_file_does_not_touch_clamd(tmp_path):
    results = _both(tmp_path, [("off",)], units=("active.clamav-daemon",))
    assert results[0][0] == 0
    assert results[0][2]["calls"] == ["disable --now clamav-clamonacc", "daemon-reload"]


@needs_sh
@pytest.mark.parametrize(("kwargs", "message"), [
    ({"clamonacc": False}, "Auf dem Server fehlt das Paket clamav-daemon."),
    ({"systemd": False}, "Ohne systemd lässt sich Echtzeit nicht einrichten."),
])
def test_without_the_package_or_systemd_nothing_is_written(tmp_path, kwargs, message):
    rc, out, state = _both(tmp_path, [("on", ["/home"], [], 50)], **kwargs)[0]
    assert (rc, out) == (3, message + "\n")
    assert list(state["files"]) == ["<B>/etc/clamav/clamd.conf"] and state["calls"] == []


@needs_sh
@pytest.mark.parametrize("unit", ["never", "dies", "loops"], ids=["startet-nie", "stirbt-gleich", "startet-immer-neu"])
def test_a_clamonacc_that_does_not_keep_running_ends_with_the_journal(tmp_path, unit):
    """„active“ direkt nach dem Start reicht nicht: Stirbt clamonacc kurz danach oder läuft es mit neuer PID, ist der
    Wächter nicht an (sonst merkte sich Deck „an“, obwohl nichts läuft)."""
    rc, out, after = _both(tmp_path, [("on", ["/home"], [], 50)], units=(f"{unit}.clamav-clamonacc",))[0]
    assert rc == 4
    assert out == "clamonacc startet nicht:\nclamonacc[1]: ERROR: Could not connect to clamd\n"
    assert "@@realtime-on" not in out
    # Danach aufgeräumt wie beim Ausschalten: kein Drop-in, keine Konfiguration, clamonacc aus, clamd wie vorher (aus).
    assert list(after["files"]) == ["<B>/etc/clamav/clamd.conf"]
    assert not any(d.endswith("clamav-clamonacc.service.d") for d in after["dirs"])
    assert [u for u in after["units"] if u.startswith(("active.", "enabled."))] == []
    tail = after["calls"][after["calls"].index("restart clamav-clamonacc"):]
    assert ["disable --now clamav-clamonacc", "stop clamav-daemon", "disable clamav-daemon"] == [
        c for c in tail if c.startswith(("disable", "stop"))
    ]


@needs_sh
def test_the_check_after_the_start_really_waits(tmp_path):
    """clamonacc stirbt erst zwei Sekunden nach dem Start: Nur wer wirklich wartet, merkt es. Sofort nachgesehen (0 s)
    liefe er noch, und der Wächter stünde fälschlich auf „an“."""
    units = ("slow.clamav-clamonacc",)
    rc, out, _ = _both(tmp_path / "sofort", [("on", ["/home"], [], 50)], units=units, settle=0)[0]
    assert rc == 0 and "@@realtime-on" in out
    rc, out, after = _both(tmp_path / "gewartet", [("on", ["/home"], [], 50)], units=units, settle=3)[0]
    assert rc == 4 and out.startswith("clamonacc startet nicht:")
    assert [u for u in after["units"] if u.startswith(("active.", "enabled."))] == []


@needs_sh
@pytest.mark.parametrize("args", [
    ["realtime-on", "50", "--", "home"],
    ["realtime-on", "50", "--", "/srv/../etc"],
    ["realtime-on", "50", "../x", "--", "/home"],
    ["realtime-on", "50", "--", "/a\nb"],
    ["realtime-on", "50", "/home"],
    ["realtime-on", "50", "--"],
    ["realtime-on", "0", "--", "/home"],
    ["realtime-on", "1001", "--", "/home"],
    ["realtime-on", "050", "--", "/home"],
    ["realtime-off", "x"],
])
def test_the_helper_refuses_bad_arguments_before_doing_anything(tmp_path, args):
    side = _side(tmp_path)
    done = subprocess.run([SH, str(side.helper), *args], capture_output=True, text=True, timeout=60, check=False,
                          env={"LC_ALL": "C"})
    assert done.returncode == 64 and done.stderr.startswith("shield-root: "), (done.returncode, done.stderr)
    assert not side.log.exists()


# --- Route und Ausführung -----------------------------------------------------------------------------------------


def _readiness(*, virt="none", clamd="/usr/sbin/clamd", root="helper", onacc="inactive") -> str:
    return (f"@@virt\n{virt}\n@@mem\nMemTotal: 8388608 kB\nMemAvailable: 4194304 kB\n@@fanotify\nyes\n@@clamd\n{clamd}\n"
            f"@@clamonacc\n/usr/sbin/clamonacc\n@@clamd_state\ninactive\n@@clamonacc_state\n{onacc}\n"
            f"@@root\n{root}\n@@apt\nyes\n@@end\n")


class _Ctx:
    """Nur was Route, Executor und Defender hier brauchen."""

    def __init__(self, sessionmaker) -> None:
        from nodvard_sdk import Actor, Host

        self._sm = sessionmaker
        self.host = Host(id="h1", name="pi", display_name="Raspberry Pi", address="10.0.0.2")
        self.readiness = _readiness()
        self.answer = SimpleNamespace(exit_code=0, stdout="@@realtime-on\n", stderr="", duration_ms=5)
        self.commands: list[str] = []
        self.proposals: list = []
        self.settings_value: dict = {}
        self.api = SimpleNamespace(current_actor=lambda: Actor.user("u1", "anna"))
        self.hosts = SimpleNamespace(get=self._get, list=self._list)
        self.exec = SimpleNamespace(run=self._run)
        self.db = SimpleNamespace(session=self._session)
        self.actions = SimpleNamespace(propose=self._propose)
        self.settings = SimpleNamespace(get=self._settings)
        self.ws = SimpleNamespace(broadcast=self._noop)
        self.audit = SimpleNamespace(log=self._noop)
        self.notify = SimpleNamespace(send=self._noop)

    async def _get(self, host_id):
        return self.host if host_id == "h1" else None

    async def _list(self, tag=None):
        return [self.host]

    async def _run(self, host, command, timeout_s=60):
        self.commands.append(command)
        if command == _rt().READINESS_COMMAND:
            return SimpleNamespace(exit_code=0, stdout=self.readiness, stderr="", duration_ms=5)
        return self.answer

    @asynccontextmanager
    async def _session(self):
        async with self._sm() as s:
            yield s
            await s.commit()

    async def _propose(self, req, **_kwargs):
        from nodvard_sdk import GateDecision, GateOutcome
        from nodvard_sdk.actions import ActionStatus

        self.proposals.append(req)
        return GateDecision(action_id=f"a{len(self.proposals)}", outcome=GateOutcome.REQUIRE_CONFIRMATION,
                            status=ActionStatus.PROPOSED, rule="test")

    async def _settings(self):
        return self.settings_value

    async def _noop(self, *a, **k):
        return None


@pytest.fixture
async def soc():
    import httpx
    from fastapi import FastAPI
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.defender_api import DefenderExecutor, build_routers
    from nodvard_deck_ext_shield.models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ctx = _Ctx(async_sessionmaker(engine, expire_on_commit=False))
    defender = Defender(ctx)
    read, manage = build_routers(ctx, defender, None, None)
    app = FastAPI()
    app.include_router(read)
    app.include_router(manage)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://soc") as client:
        yield SimpleNamespace(ctx=ctx, client=client, executor=DefenderExecutor(ctx, defender, None))
    await engine.dispose()


async def _switch(soc, enable: bool):
    r = await soc.client.post("/defender/realtime/h1", json={"enable": enable})
    return r, (soc.ctx.proposals[-1] if r.status_code == 200 else None)


async def _realtime_on(soc) -> bool:
    rows = (await soc.client.get("/defender/realtime/readiness")).json()["hosts"]
    return rows[0]["realtime_on"]


@pytest.mark.asyncio
async def test_the_click_proposes_the_command_from_the_settings(soc):
    rt, av = _rt(), _av()
    soc.ctx.settings_value = {
        "watch_paths": ["/home", "/srv/upload/"], "exclude_paths": ["/home/cache"], "max_filesize_mb": 80,
    }
    r, req = await _switch(soc, True)
    assert r.status_code == 200, r.text
    assert r.json() == {"action_id": "a1", "status": "proposed", "risk": "medium"}
    from nodvard_deck_ext_shield.ids import ACTION_REALTIME

    assert req.action_type == ACTION_REALTIME and req.reason == "Viren-Wächter auf Raspberry Pi in Echtzeit"
    fields = {"enable": True, "watch": ["/home", "/srv/upload/"], "exclude": ["/home/cache"], "max_mb": 80}
    command = rt.enable_command(["/home", "/srv/upload"], ["/home/cache"], max_filesize_mb=80)
    assert req.payload == {**fields, "command": command}

    # Ohne eigene Einstellung: die Ordner des Wächters, keine Ausnahmen, 50 MB.
    soc.ctx.settings_value = {}
    _, req = await _switch(soc, True)
    assert (req.payload["watch"], req.payload["exclude"], req.payload["max_mb"]) == (av.WATCH_PATHS, [], 50)

    # Zurück fragt den Server nicht erst nach seiner Eignung.
    before = len(soc.ctx.commands)
    _, req = await _switch(soc, False)
    assert req.reason == "Viren-Wächter auf Raspberry Pi zurück auf Intervall"
    assert req.payload["command"] == rt.disable_command() and len(soc.ctx.commands) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(("readiness", "detail"), [
    (_readiness(virt="lxc"), "Läuft in einem Container (lxc): Echtzeit braucht fanotify, das geht dort nicht."),
    (_readiness(clamd="none"), (
        "Auf dem Server fehlt das Paket clamav-daemon. Bitte zuerst installieren; Nodvard Deck installiert es nicht selbst."
    )),
])
async def test_an_unsuitable_server_gets_no_proposal(soc, readiness, detail):
    soc.ctx.readiness = readiness
    r, _ = await _switch(soc, True)
    assert (r.status_code, r.json()["detail"]) == (409, detail)
    assert soc.ctx.proposals == []


@pytest.mark.asyncio
async def test_a_broken_folder_in_the_settings_is_a_clear_400(soc):
    soc.ctx.settings_value = {"watch_paths": ["/srv/../etc"]}
    r, _ = await _switch(soc, True)
    assert (r.status_code, r.json()["detail"]) == (400, "Ungültiger Ordner: /srv/../etc")


@pytest.mark.asyncio
async def test_unknown_host_is_404(soc):
    r = await soc.client.post("/defender/realtime/nope", json={"enable": True})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_after_approval_it_runs_over_the_helper_and_remembers_the_state(soc):
    rt, av = _rt(), _av()
    assert await _realtime_on(soc) is False
    _, req = await _switch(soc, True)
    res = await soc.executor.execute(req)
    assert res.success, res.error
    args = rt.helper_args(True, req.payload["watch"], [], max_filesize_mb=50)
    assert soc.ctx.commands[-1] == av.via_helper(args, req.payload["command"], version=av.HELPER_REALTIME_VERSION)
    # Die gemerkte Eignung ist verworfen: Die Liste fragt den Server neu (und der meldet clamonacc jetzt als laufend).
    soc.ctx.readiness = _readiness(onacc="active")
    runs = len(soc.ctx.commands)
    assert await _realtime_on(soc) is True
    assert len(soc.ctx.commands) == runs + 1

    _, req = await _switch(soc, False)
    soc.ctx.answer = SimpleNamespace(exit_code=0, stdout="@@realtime-off\n", stderr="", duration_ms=5)
    assert (await soc.executor.execute(req)).success
    assert soc.ctx.commands[-1] == av.via_helper(["realtime-off"], rt.disable_command(), version=av.HELPER_REALTIME_VERSION)
    assert await _realtime_on(soc) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(("answer", "error"), [
    (SimpleNamespace(exit_code=4, stdout="@@msg=realtime_not_started\njournal\n", stderr="", duration_ms=5),
     "clamonacc startet nicht:\njournal"),
    (SimpleNamespace(exit_code=0, stdout="fertig\n", stderr="", duration_ms=5), "fertig"),
    (SimpleNamespace(exit_code=126, stdout="@@noroot\n", stderr="", duration_ms=5), None),
])
async def test_a_failed_switch_remembers_nothing(soc, answer, error):
    _, req = await _switch(soc, True)
    soc.ctx.answer = answer
    res = await soc.executor.execute(req)
    assert not res.success
    assert res.error == (error if error is not None else _av().NO_ROOT_MESSAGE)
    assert await _realtime_on(soc) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(("answer", "still_on"), [
    # clamonacc startete nicht: Der Server hat wie beim Ausschalten aufgeräumt, also gilt „aus“.
    (SimpleNamespace(exit_code=4, stdout="@@msg=realtime_not_started\njournal\n", stderr="", duration_ms=5), False),
    # Etwas anderes schlug fehl, bevor der Server etwas umstellte: Der alte Stand bleibt.
    (SimpleNamespace(exit_code=3, stdout="@@msg=realtime_no_package\n", stderr="", duration_ms=5), True),
])
async def test_a_failed_switch_on_after_it_was_on(soc, answer, still_on):
    soc.ctx.readiness = _readiness(onacc="active")
    _, req = await _switch(soc, True)
    assert (await soc.executor.execute(req)).success and await _realtime_on(soc) is True
    _, req = await _switch(soc, True)
    soc.ctx.answer = answer
    assert not (await soc.executor.execute(req)).success
    row = (await soc.client.get("/defender/realtime/readiness")).json()["hosts"][0]
    assert (row["realtime_on"], row["realtime_requested"]) == (still_on, still_on)


@pytest.mark.asyncio
@pytest.mark.parametrize(("onacc", "down_since", "on"), [
    ("active", None, True),
    ("inactive", None, False),  # der Server meldet clamonacc als gestoppt
    ("activating", None, False),  # Neustart in Schleife
    ("", None, True),  # unbekannt: der gemerkte Stand zählt
    ("active", "2026-10-16T08:00:00+00:00", False),  # der Wächter-Lauf ist auf den Minuten-Takt zurück
])
async def test_the_readiness_shows_whether_realtime_really_runs(soc, onacc, down_since, on):
    from nodvard_deck_ext_shield.patching import save_baseline

    soc.ctx.readiness = _readiness(onacc=onacc)
    changed_at = "2026-10-16T07:00:00+00:00"
    await save_baseline(soc.ctx, "h1", _rt().STATE_KIND, {"enabled": True, "changed_at": changed_at})
    if down_since:
        await save_baseline(soc.ctx, "h1", _rt().DOWN_KIND, {"since": down_since, "changed_at": changed_at})
    row = (await soc.client.get("/defender/realtime/readiness?refresh=true")).json()["hosts"][0]
    assert row["realtime_on"] is on and row["realtime_requested"] is True
    assert row["realtime_down_since"] == down_since
    assert row["clamonacc_active"] == {"active": True, "": None}.get(onacc, False)


@pytest.mark.asyncio
async def test_a_fallback_from_an_earlier_switch_on_does_not_count(soc):
    """Der Rückfall gehört zu einem Einschalten (`changed_at`): Nach Aus und wieder Ein zählt ein alter nicht mehr."""
    from nodvard_deck_ext_shield.patching import save_baseline

    soc.ctx.readiness = _readiness(onacc="active")
    await save_baseline(soc.ctx, "h1", _rt().STATE_KIND, {"enabled": True, "changed_at": "2026-10-16T09:00:00+00:00"})
    await save_baseline(soc.ctx, "h1", _rt().DOWN_KIND, {"since": "2026-10-16T08:00:00+00:00",
                                                          "changed_at": "2026-10-16T07:00:00+00:00"})
    row = (await soc.client.get("/defender/realtime/readiness?refresh=true")).json()["hosts"][0]
    assert row["realtime_on"] is True and row["realtime_down_since"] is None


@pytest.mark.asyncio
async def test_the_executor_rebuilds_the_command_and_refuses_changed_or_old_proposals(soc):
    from nodvard_deck_ext_shield.defender_api import OLD_VERSION_MESSAGE, STALE_MESSAGE

    _, req = await _switch(soc, True)
    changed = req.model_copy(update={"payload": {**req.payload, "watch": ["/"]}})
    assert (await soc.executor.execute(changed)).error == STALE_MESSAGE
    old = req.model_copy(update={"payload": {"enable": True, "command": req.payload["command"]}})
    assert (await soc.executor.execute(old)).error == OLD_VERSION_MESSAGE
    bad = req.model_copy(update={"payload": {**req.payload, "max_mb": "50"}})
    assert (await soc.executor.execute(bad)).error == "Ungültige Dateigröße."
    assert soc.ctx.commands == [_rt().READINESS_COMMAND]
