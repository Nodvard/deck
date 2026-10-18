"""Viren-Wächter in Echtzeit: die Abfrage, ob ein Server dafür taugt, ihre Auswertung und die Route dazu."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nodvard_deck.core.deny_patterns import match_deny_patterns

EXT_SRC = Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"
GB_KB = 1024 * 1024


@pytest.fixture(autouse=True)
def _ext_shield_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_SRC))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


class _Realtime:
    """Das Modul `realtime` erst beim Zugriff, also nach dem Fixture oben."""

    def __getattr__(self, name):
        from nodvard_deck_ext_shield import realtime

        return getattr(realtime, name)


rt = _Realtime()


def _out(*, virt="none", total_kb: int | None = 8 * GB_KB, avail_kb: int | None = 4 * GB_KB,
         fanotify="yes\nCONFIG_FANOTIFY=y", clamd="/usr/sbin/clamd", clamonacc="/usr/sbin/clamonacc",
         state="inactive", onacc_state="inactive", root="sudo", apt="yes", end=True) -> str:
    mem = "".join(f"{name}:  {kb} kB\n" for name, kb in (("MemTotal", total_kb), ("MemAvailable", avail_kb)) if kb)
    text = (f"@@virt\n{virt}\n@@mem\n{mem}@@fanotify\n{fanotify}\n@@clamd\n{clamd}\n@@clamonacc\n{clamonacc}\n"
            f"@@clamd_state\n{state}\n@@clamonacc_state\n{onacc_state}\n@@root\n{root}\n@@apt\n{apt}\n")
    return text + "@@end\n" if end else text


def test_a_good_server_can_run_it():
    r = rt.parse_readiness(_out())
    assert (r.possible, r.reasons, r.notes) == (True, [], [])
    assert (r.container, r.mem_total_mb, r.mem_available_mb, r.fanotify) == (None, 8192, 4096, True)
    assert (r.clamd_installed, r.clamonacc_installed, r.clamd_active, r.root, r.apt) == (True, True, False, "sudo", True)


def test_a_container_is_refused():
    r = rt.parse_readiness(_out(virt="lxc"))
    assert not r.possible and r.container == "lxc"
    assert r.reasons == ["Läuft in einem Container (lxc): Echtzeit braucht fanotify, das geht dort nicht."]


def test_too_little_memory_names_the_amount():
    r = rt.parse_readiness(_out(total_kb=GB_KB))
    assert not r.possible
    assert r.reasons == ["Zu wenig Arbeitsspeicher: 1,0 GB insgesamt, nötig sind mindestens 2 GB."]


def test_the_memory_limits_are_inclusive():
    ok = rt.parse_readiness(_out(total_kb=rt.MIN_TOTAL_MB * 1024, avail_kb=rt.MIN_AVAILABLE_MB * 1024))
    assert ok.possible, ok.reasons
    low = rt.parse_readiness(_out(total_kb=rt.MIN_TOTAL_MB * 1024 - 1024, avail_kb=rt.MIN_AVAILABLE_MB * 1024 - 1024))
    assert len(low.reasons) == 2


def test_little_free_memory_only_counts_while_clamd_is_not_running():
    off = rt.parse_readiness(_out(avail_kb=GB_KB, state="inactive"))
    assert not off.possible
    assert off.reasons == ["Zu wenig freier Arbeitsspeicher: 1,0 GB frei, clamd braucht etwa 1,2 GB."]
    assert rt.parse_readiness(_out(avail_kb=GB_KB, state="active")).possible
    # Ein unbekannter Zustand (kein systemd) zählt nicht als "läuft".
    assert not rt.parse_readiness(_out(avail_kb=GB_KB, state="")).possible


def test_unreadable_memory_says_no():
    for kwargs in ({"total_kb": None}, {"avail_kb": None}, {"total_kb": None, "avail_kb": None}):
        r = rt.parse_readiness(_out(**kwargs))
        assert not r.possible
        assert r.reasons == ["Der Arbeitsspeicher des Servers ließ sich nicht lesen."], kwargs


def test_without_root_helper_or_sudo_it_is_refused():
    r = rt.parse_readiness(_out(root="none"))
    assert not r.possible and r.root == "none"
    assert r.reasons == [(
        "Keine Rechte als root: Auf dem Server fehlt das aktuelle Hilfsskript von Nodvard Shield "
        "(Einrichtung erneut ausführen) oder sudo ohne Passwort."
    )]
    for root in ("root", "helper", "sudo"):
        r = rt.parse_readiness(_out(root=root))
        assert r.possible and r.root == root


@pytest.mark.parametrize(("fanotify", "expected", "refused"), [
    ("yes", True, False),
    ("CONFIG_FANOTIFY=y", True, False),
    ("yes\nCONFIG_FANOTIFY=y", True, False),
    ("# CONFIG_FANOTIFY is not set", False, True),
    ("CONFIG_FANOTIFY=n", False, True),
    # Der Ordner unter /proc beweist es, auch wenn die Kernel-Konfiguration etwas anderes zu sagen scheint.
    ("yes\n# CONFIG_FANOTIFY is not set", True, False),
    # Keine Angaben (Konfiguration nicht lesbar, Ordner fehlt): unbekannt, kein Grund.
    ("", None, False),
])
def test_fanotify_is_only_refused_when_the_kernel_says_no(fanotify, expected, refused):
    r = rt.parse_readiness(_out(fanotify=fanotify))
    assert r.fanotify is expected
    assert (r.reasons == ["Der Kernel des Servers bietet kein fanotify."]) is refused
    assert r.possible is (not refused)


def test_an_incomplete_answer_is_refused():
    r = rt.parse_readiness(_out(end=False))
    assert not r.possible
    assert r.reasons == ["Die Abfrage kam nicht vollständig zurück."]
    assert r.mem_total_mb == 8192 and r.clamd_installed
    assert rt.parse_readiness("").reasons == ["Die Abfrage kam nicht vollständig zurück."]


@pytest.mark.parametrize("apt", ["yes", "no"])
def test_a_missing_clamd_is_only_a_hint_because_deck_installs_nothing(apt):
    r = rt.parse_readiness(_out(clamd="none", clamonacc="none", apt=apt))
    assert r.possible and r.reasons == []
    assert r.notes == ["Das Paket clamav-daemon fehlt noch; Nodvard Deck installiert es nicht selbst."]
    assert not r.clamd_installed and not r.clamonacc_installed


def test_a_missing_clamonacc_is_only_a_hint():
    r = rt.parse_readiness(_out(clamonacc="none"))
    assert r.possible and not r.clamonacc_installed
    assert r.notes == ["clamonacc fehlt, er gehört zum Paket clamav-daemon."]


def test_readiness_dict_carries_every_field():
    data = rt.readiness_dict(rt.parse_readiness(_out()))
    assert set(data) == {
        "possible", "reasons", "notes", "container", "mem_total_mb", "mem_available_mb", "fanotify", "clamd_installed",
        "clamonacc_installed", "clamd_active", "clamonacc_active", "root", "apt",
    }


@pytest.mark.parametrize(("state", "active"), [
    ("active", True), ("inactive", False), ("failed", False), ("activating", False), ("", None), ("reloading", None),
])
def test_readiness_says_whether_clamonacc_runs(state, active):
    assert rt.parse_readiness(_out(onacc_state=state)).clamonacc_active is active


def test_the_command_passes_the_deny_list():
    assert match_deny_patterns(rt.READINESS_COMMAND) is None


def _stub(bin_dir: Path, name: str, body: str) -> None:
    (bin_dir / name).write_text("#!/bin/sh\n" + body)
    (bin_dir / name).chmod(0o755)


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_the_command_in_a_real_shell(tmp_path):
    bin_dir, sbin = tmp_path / "bin", tmp_path / "sbin"
    bin_dir.mkdir()
    sbin.mkdir()
    _stub(bin_dir, "systemd-detect-virt", "echo lxc\n")
    _stub(bin_dir, "sudo", "exit 1\n")
    _stub(bin_dir, "systemctl", "echo inactive\nexit 3\n")
    _stub(bin_dir, "clamd", "exit 0\n")
    # Auch als root getestet (wie im Container): `id -u` soll nicht 0 sein.
    _stub(bin_dir, "id", "echo 1000\n")
    # Die sbin-Ordner werden durch einen leeren ersetzt, damit ein echtes clamonacc der Testmaschine nichts verfälscht.
    assert "/usr/local/sbin:/usr/sbin:/sbin" in rt.READINESS_COMMAND
    command = rt.READINESS_COMMAND.replace("/usr/local/sbin:/usr/sbin:/sbin", str(sbin))
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}
    out = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True, env=env, timeout=60, check=False)
    assert "@@end" in out.stdout, out.stdout
    r = rt.parse_readiness(out.stdout)
    assert r.container == "lxc" and r.root == "none"
    assert r.clamd_active is False and r.clamd_installed and not r.clamonacc_installed
    assert r.clamonacc_active is False
    assert not r.possible and r.mem_total_mb, out.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize(("shipped", "behind", "root"), [(True, 0, "helper"), (True, 1, "none"), (False, 0, "none")],
                         ids=["mitgeliefert", "mitgeliefert-zu-alt", "anderer-inhalt"])
def test_the_root_helper_counts_only_as_the_shipped_file(tmp_path, shipped, behind, root):
    """Zählt nur, wenn die Prüfsumme stimmt und es mindestens `HELPER_REALTIME_LOG_VERSION` meldet (Einschalten und
    Funde brauchen beides); ein anderes Skript wird nicht einmal nach der Version gefragt."""
    from nodvard_deck_ext_shield import antivirus as av

    version = av.HELPER_REALTIME_LOG_VERSION - behind
    bin_dir, sbin = tmp_path / "bin", tmp_path / "sbin"
    bin_dir.mkdir()
    sbin.mkdir()
    helper, ran = tmp_path / "shield-root", tmp_path / "lief"
    _stub(tmp_path, "shield-root", f'touch {ran}\n[ "$1" = version ] && echo "{version}"\n')
    # sudo gibt es nur für das Hilfsskript, wie mit der engen sudo-Regel.
    _stub(bin_dir, "sudo", f'[ "$1" = -n ] && shift\n[ "$1" = {helper} ] || exit 1\nexec "$@"\n')
    _stub(bin_dir, "id", "echo 1000\n")
    command = rt.READINESS_COMMAND.replace("/usr/local/sbin:/usr/sbin:/sbin", str(sbin))
    assert rt.HELPER in command and av.HELPER_BUNDLED[1] in command
    command = command.replace(rt.HELPER, str(helper))
    if shipped:
        command = command.replace(av.HELPER_BUNDLED[1], hashlib.sha256(helper.read_bytes()).hexdigest())
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}
    out = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True, env=env, timeout=60, check=False)
    assert rt.parse_readiness(out.stdout).root == root, out.stdout
    assert ran.exists() is shipped


# --- Route ---------------------------------------------------------------------------------------------------------


class _Ctx:
    def __init__(self, *, broken: tuple[str, ...] = ()):
        from nodvard_sdk import Host

        self.hosts_all = [Host(id="h1", name="pi", display_name="Pi", address="10.0.0.2"),
                          Host(id="h2", name="nas", display_name="", address="10.0.0.3")]
        self.broken = broken
        self.calls: list[str] = []
        self.settings = SimpleNamespace(get=self._settings)
        self.hosts = SimpleNamespace(list=self._list)
        self.exec = SimpleNamespace(run=self._run)
        self.api = SimpleNamespace(current_actor=lambda: None)

    async def _settings(self):
        return {}

    async def _list(self, tag=None):
        return self.hosts_all

    async def _run(self, host, command, timeout_s=60):
        self.calls.append(host.id)
        if host.id in self.broken:
            raise ConnectionError("Verbindung abgelehnt")
        return SimpleNamespace(exit_code=0, stdout=_out(), stderr="", duration_ms=5)


@pytest.mark.asyncio
async def test_the_route_lists_every_target_host_and_caches_the_answer(monkeypatch):
    import httpx
    from fastapi import FastAPI
    from nodvard_deck_ext_shield import defender_api
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.defender_api import build_routers

    async def no_baselines(ctx, kind):
        assert kind in ("realtime", "realtime_down")
        return {}

    monkeypatch.setattr(defender_api, "load_baselines", no_baselines)
    ctx = _Ctx(broken=("h2",))
    read, _manage = build_routers(ctx, Defender(ctx))
    app = FastAPI()
    app.include_router(read)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://deck") as client:
        first = (await client.get("/defender/realtime/readiness")).json()
        rows = {r["host_id"]: r for r in first["hosts"]}
        assert list(rows) == ["h1", "h2"]
        assert (rows["h1"]["host_name"], rows["h1"]["reachable"], rows["h1"]["possible"]) == ("Pi", True, True)
        assert rows["h1"]["mem_total_mb"] == 8192 and rows["h1"]["reasons"] == []
        assert (rows["h2"]["host_name"], rows["h2"]["reachable"], rows["h2"]["possible"]) == ("nas", False, False)
        assert rows["h2"]["reasons"] == ["Server nicht erreichbar."] and "Verbindung abgelehnt" in rows["h2"]["error"]
        assert [r["realtime_on"] for r in first["hosts"]] == [False, False]
        assert sorted(ctx.calls) == ["h1", "h2"]

        await client.get("/defender/realtime/readiness")
        assert sorted(ctx.calls) == ["h1", "h2"]

        await client.get("/defender/realtime/readiness", params={"refresh": "true"})
        assert sorted(ctx.calls) == ["h1", "h1", "h2", "h2"]
