"""Update-Zentrale in nexus-soc: Update-Stand auswerten, Befehle bauen, Ablauf mit Fake-Kontext."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "nexus-soc" / "src"))

from nodvard_deck.core.deny_patterns import match_deny_patterns  # noqa: E402
from nodvard_deck_ext_nexus_soc import updates as up  # noqa: E402

BERLIN = ZoneInfo("Europe/Berlin")


def _berlin(hour: int, minute: int = 0, day: int = 2) -> float:
    """Unix-Zeit von `day`. Oktober 2026, `hour:minute` Uhr Berlin (Sommerzeit)."""
    return datetime(2026, 10, day, hour, minute, tzinfo=BERLIN).timestamp()


_BERLIN_0600 = _berlin(6)

APT_STATUS = """@@pm
apt
@@refresh
@@list
base-files/stable 12.4+deb12u7 amd64 [upgradable from: 12.4+deb12u6]
openssl/stable-security 3.0.14-1~deb12u2 amd64 [upgradable from: 3.0.13-1~deb12u1]
libssl3/bookworm-security 3.0.14-1~deb12u2 amd64 [upgradable from: 3.0.13-1~deb12u1]
@@security
@@reboot
no
@@kernel
6.8.12-4-pve
@@kernels
6.8.12-10-pve
6.8.12-4-pve
@@uptime
123456.78
@@unattended
no
@@end
"""


def test_parse_apt_status_detects_security_and_new_kernel():
    st = up.parse_status(APT_STATUS)
    assert st.manager == "apt"
    assert [p.name for p in st.packages] == ["libssl3", "openssl", "base-files"]  # Sicherheit zuerst
    assert st.security_count == 2
    assert st.packages[2].current_version == "12.4+deb12u6" and st.packages[2].new_version == "12.4+deb12u7"
    assert st.reboot_required and "6.8.12-10-pve" in st.reboot_reasons[0]
    assert st.uptime_s == pytest.approx(123456.78)
    assert st.unattended is False
    d = st.as_dict()
    assert (d["count"], d["security_count"]) == (3, 2)


def test_parse_reboot_file_refresh_error_and_other_managers():
    st = up.parse_status(
        "@@pm\napt\n@@refresh\nE: Could not open lock file /var/lib/apt/lists/lock - open (13: Permission denied)\n"
        "@@list\n@@reboot\nyes\nlinux-image-6.1.0-26-amd64\n@@kernel\n6.1.0-25-amd64\n@@kernels\n6.1.0-25-amd64\n@@end"
    )
    assert st.packages == [] and st.reboot_required and st.reboot_reasons == ["linux-image-6.1.0-26-amd64"]
    assert st.refresh_error and "root" in st.refresh_error

    dnf = up.parse_status(
        "@@pm\ndnf\n@@list\nkernel-core.x86_64   6.8.9-300.fc40   updates\nopenssl-libs.x86_64  1:3.2.1-2.fc40  updates\n"
        "@@security\nFEDORA-2024-1a2b Important/Sec. openssl-libs-1:3.2.1-2.fc40.x86_64\n@@reboot\nno\n@@end"
    )
    assert [(p.name, p.security) for p in dnf.packages] == [("openssl-libs", True), ("kernel-core", False)]

    apk = up.parse_status("@@pm\napk\n@@list\nbusybox-1.36.1-r15 < 1.36.1-r16\n@@reboot\nno\n@@end")
    assert (apk.packages[0].name, apk.packages[0].current_version, apk.packages[0].new_version) == ("busybox", "1.36.1-r15", "1.36.1-r16")

    none = up.parse_status("@@pm\nnone\n@@end")
    assert none.manager is None and "Paketmanager" in none.error
    assert none.unsupported is True and none.as_dict()["unsupported"] is True
    assert up.parse_status("@@pm\napt\n@@end").as_dict()["unsupported"] is False


def test_upgrade_commands_are_safe_and_security_only_names_packages():
    sec = up.upgrade_command("apt", "security", ["openssl", "libssl3", "evil; rm -rf /"])
    assert "install --only-upgrade openssl libssl3" in sec and "evil" not in sec
    full = up.upgrade_command("apt", "all")
    assert "upgrade --with-new-pkgs" in full and "--force-confold" in full
    for cmd in (sec, full, up.upgrade_command("apt", "cleanup"), up.upgrade_command("dnf", "security"), up.REBOOT_COMMAND,
                up.status_command(refresh=True)):
        assert match_deny_patterns(cmd) is None, cmd
    with pytest.raises(ValueError):
        up.upgrade_command("apt", "security", [])


# --- "Alle Updates" als dist-upgrade (Einstellung, Standard aus) --------------


def test_dist_upgrade_command_is_opt_in_and_only_for_apt_all():
    plain = up.upgrade_command("apt", "all")
    assert plain == up.upgrade_command("apt", "all", dist_upgrade=False)
    assert "dist-upgrade" not in plain and "upgrade --with-new-pkgs" in plain

    dist = up.upgrade_command("apt", "all", dist_upgrade=True)
    assert "command -v pveversion" in dist
    assert f"apt-get {up._APT_OPTS} dist-upgrade" in dist  # gleiche Optionen wie bisher
    # Auf Nicht-Proxmox-Servern bleibt es beim bisherigen Befehl, davor derselbe Start und danach derselbe Schluss.
    assert f"else apt-get {up._APT_OPTS} upgrade --with-new-pkgs; fi" in dist
    assert dist.startswith("export DEBIAN_FRONTEND=noninteractive; apt-get update -q && ")
    assert dist.endswith(plain[plain.index("; R=$?"):])
    assert match_deny_patterns(dist) is None
    # Alles andere ignoriert die Angabe: kein dist-upgrade fuer Sicherheit, Aufraeumen, dnf, apk.
    for manager, mode, pkgs in (("apt", "security", ["openssl"]), ("apt", "cleanup", None), ("dnf", "all", None), ("apk", "all", None)):
        assert up.upgrade_command(manager, mode, pkgs, dist_upgrade=True) == up.upgrade_command(manager, mode, pkgs)


def test_wants_dist_upgrade_needs_the_setting_apt_and_all():
    on, off = {"proxmox_dist_upgrade": True}, {"proxmox_dist_upgrade": False}
    assert up.wants_dist_upgrade(on, "apt", "all") is True
    for settings in ({}, off, {"proxmox_dist_upgrade": None}):
        assert up.wants_dist_upgrade(settings, "apt", "all") is False
    assert up.wants_dist_upgrade(on, "apt", "security") is False
    assert up.wants_dist_upgrade(on, "apt", "cleanup") is False
    assert up.wants_dist_upgrade(on, "dnf", "all") is False


def _apt_stubs(tmp_path: Path, *, pve: bool, apt_rc: int = 0) -> tuple[Path, Path]:
    """apt-get-Attrappe (schreibt ihre Argumente mit) und optional `pveversion`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "apt.log"
    (bin_dir / "apt-get").write_text(f"#!/bin/sh\necho \"apt-get $*\" >> '{log}'\ncase \"$*\" in *upgrade*) exit {apt_rc};; esac\n")
    (bin_dir / "apt-get").chmod(0o755)
    if pve:
        (bin_dir / "pveversion").write_text("#!/bin/sh\necho pve-manager/9.0.3/abc (running kernel: 6.14.8-2-pve)\n")
        (bin_dir / "pveversion").chmod(0o755)
    return bin_dir, log


def _run_upgrade(bin_dir: Path, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True, timeout=30, check=False,
                          env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(bin_dir)})


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize(("pve", "expected"), [(True, "dist-upgrade"), (False, "upgrade --with-new-pkgs")])
def test_dist_upgrade_runs_only_on_proxmox_in_a_real_shell(tmp_path, pve, expected):
    bin_dir, log = _apt_stubs(tmp_path, pve=pve)
    if not pve and shutil.which("pveversion", path=f"{bin_dir}:/usr/bin:/bin"):
        pytest.skip("dieser Rechner ist selbst ein Proxmox-Server")
    out = _run_upgrade(bin_dir, up.upgrade_command("apt", "all", dist_upgrade=True))
    calls = log.read_text().splitlines()
    assert calls[0] == "apt-get update -q"
    assert len(calls) == 2 and calls[1].endswith(expected), calls  # genau ein Upgrade-Aufruf
    assert (calls[1].endswith(" dist-upgrade")) is pve
    assert out.returncode == 0 and up.UPGRADE_DONE in out.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_without_the_setting_proxmox_still_gets_plain_upgrade(tmp_path):
    bin_dir, log = _apt_stubs(tmp_path, pve=True)
    _run_upgrade(bin_dir, up.upgrade_command("apt", "all"))
    assert log.read_text().splitlines()[1].endswith("upgrade --with-new-pkgs")
    assert "dist-upgrade" not in log.read_text()


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_a_failing_dist_upgrade_keeps_its_exit_code(tmp_path):
    """Der Rueckgabecode kommt aus dem if-Zweig durch (`R=$?` steht hinter `fi`)."""
    bin_dir, _log = _apt_stubs(tmp_path, pve=True, apt_rc=100)
    out = _run_upgrade(bin_dir, up.upgrade_command("apt", "all", dist_upgrade=True))
    assert out.returncode == 100 and up.UPGRADE_DONE in out.stdout


# apt haelt Pakete zurueck, deren Update andere Pakete ersetzen/entfernen muesste.
APT_KEPT_BACK = """Reading package lists...
Building dependency tree...
Calculating upgrade...
The following packages have been kept back:
  proxmox-ve pve-manager
The following packages will be upgraded:
  base-files openssl
2 upgraded, 0 newly installed, 0 to remove and 2 not upgraded.
@@upgrade-done
"""


def test_kept_back_packages_are_named_in_the_summary():
    res = up.parse_upgrade_output(APT_KEPT_BACK, 0)
    assert res.ok and res.kept_back == ["proxmox-ve", "pve-manager"]
    assert res.summary == "2 Paket(e) aktualisiert – 2 zurückgehalten (proxmox-ve, pve-manager)"
    # Deutsche Ausgabe, Namen ueber mehrere Zeilen, Reboot-Hinweis am Ende
    de = up.parse_upgrade_output(
        "Die folgenden Pakete wurden zurückgehalten:\n  pve-manager\n  libpve-common-perl\n"
        "Die folgenden Pakete werden aktualisiert:\n  openssl\n1 aktualisiert, 0 neu installiert, 0 zu entfernen und 2 nicht aktualisiert.\n"
        "@@upgrade-done\n@@reboot-required\n", 0)
    assert de.kept_back == ["pve-manager", "libpve-common-perl"]
    assert de.summary == "1 Paket(e) aktualisiert – 2 zurückgehalten (pve-manager, libpve-common-perl) – Neustart nötig"
    # Die vom Abfrage-Befehl gezogene Zeile (Abschnitt steht nicht mehr im Ende des Protokolls)
    marker = up.parse_upgrade_output("@@kept-back: pve-manager proxmox-ve\n12 upgraded, 0 newly installed, 0 to remove and 2 not upgraded.\n", 0)
    assert marker.kept_back == ["pve-manager", "proxmox-ve"] and "2 zurückgehalten" in marker.summary
    # Nichts zurueckgehalten: Zusammenfassung wie bisher. "N not upgraded" allein zaehlt nicht
    # (bei "install --only-upgrade" sind das alle uebrigen offenen Updates).
    plain = up.parse_upgrade_output("1 upgraded, 0 newly installed, 0 to remove and 14 not upgraded.\n", 0)
    assert plain.kept_back == [] and plain.summary == "1 Paket(e) aktualisiert"
    # Lange Listen werden gekuerzt, Doppelte (Abschnitt + Zeile) zaehlen einmal
    many = " ".join(f"pkg{i}" for i in range(8))
    long = up.parse_upgrade_output(f"@@kept-back: {many}\nThe following packages have been kept back:\n  {many}\n", 0)
    assert len(long.kept_back) == 8
    assert long.summary.endswith("8 zurückgehalten (pkg0, pkg1, pkg2, pkg3, pkg4 und 3 weitere)")
    # Bei einem Fehler zaehlt nur der Fehler
    bad = up.parse_upgrade_output(APT_KEPT_BACK + "E: Sub-process /usr/bin/dpkg returned an error code (1)\n", 100)
    assert not bad.ok and bad.kept_back == [] and bad.summary.startswith("E: Sub-process")


def test_kept_back_names_ignore_anything_that_is_not_a_package_name():
    out = "@@kept-back: ok-pkg $(id) ;rm libfoo:amd64\nThe following packages have been kept back:\n  a+b x/y\nfertig\n"
    assert up.parse_kept_back(out) == ["ok-pkg", "libfoo:amd64", "a+b"]


def _kernel_status(running: str, installed: list[str]) -> str:
    return "@@pm\napt\n@@list\n@@reboot\nno\n@@kernel\n" + running + "\n@@kernels\n" + "\n".join(installed) + "\n@@end\n"


# Auf einem Pi 5 sind beide Kernel-Varianten installiert (rpi-2712 und
# rpi-v8); `sort -V` nannte "6.12.47+rpt-rpi-v8" als neuesten, und der Vergleich mit
# "6.12.47+rpt-rpi-2712" warf TypeError ('v8' gegen 2712) -- die Pruefung scheiterte immer.
@pytest.mark.parametrize(("running", "installed", "reboot", "latest"), [
    # Proxmox: nur die ABI-Nummer aendert sich (4 -> 10), Reihenfolge der Liste egal
    ("6.8.12-4-pve", ["6.8.12-10-pve", "6.8.12-4-pve"], True, "6.8.12-10-pve"),
    ("6.8.12-10-pve", ["6.8.12-4-pve", "6.8.12-10-pve", "6.8.12-9-pve"], False, "6.8.12-10-pve"),
    # Debian
    ("6.1.0-25-amd64", ["6.1.0-25-amd64", "6.1.0-26-amd64"], True, "6.1.0-26-amd64"),
    # Pi 4: laeuft mit rpi-v8, der 2712er-Kernel ist nur mitinstalliert
    ("6.12.47+rpt-rpi-v8", ["6.12.47+rpt-rpi-2712", "6.12.47+rpt-rpi-v8"], False, "6.12.47+rpt-rpi-v8"),
    ("6.12.34+rpt-rpi-v8", ["6.12.34+rpt-rpi-2712", "6.12.34+rpt-rpi-v8", "6.12.47+rpt-rpi-2712", "6.12.47+rpt-rpi-v8"],
     True, "6.12.47+rpt-rpi-v8"),
    # Pi 5: laeuft schon der neueste 2712er -> kein Neustart (und kein Absturz)
    ("6.12.47+rpt-rpi-2712", ["6.12.47+rpt-rpi-2712", "6.12.47+rpt-rpi-v8"], False, "6.12.47+rpt-rpi-2712"),
    ("6.12.34+rpt-rpi-2712", ["6.12.34+rpt-rpi-2712", "6.12.47+rpt-rpi-2712", "6.12.47+rpt-rpi-v8"], True, "6.12.47+rpt-rpi-2712"),
    # Kein Kernel dieser Art in /boot (z. B. im Container) -> nichts zu vergleichen
    ("6.8.12-4-pve", [], False, None),
])
def test_new_kernel_is_only_compared_within_the_same_kind(running, installed, reboot, latest):
    st = up.parse_status(_kernel_status(running, installed))
    assert (st.reboot_required, st.latest_kernel) == (reboot, latest)
    if reboot:
        assert st.reboot_reasons == [f"neuer Kernel {latest} (läuft: {running})"]


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_pi5_kernel_list_from_a_real_shell(tmp_path):
    """Der echte Befehl, nur mit /boot in einem Testordner und einem uname-Stub (Pi 5)."""
    boot = tmp_path / "boot"
    boot.mkdir()
    for k in ("6.12.47+rpt-rpi-2712", "6.12.47+rpt-rpi-v8", "6.12.34+rpt-rpi-2712"):
        (boot / f"vmlinuz-{k}").write_text("")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "uname").write_text("#!/bin/sh\necho 6.12.47+rpt-rpi-2712\n")
    (bindir / "uname").chmod(0o755)
    cmd = up.status_command(refresh=False)
    assert "/boot/vmlinuz-*" in cmd
    cmd = cmd.replace("/boot/vmlinuz-*", f"{boot}/vmlinuz-*")
    out = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=60, check=False,
                         env={"PATH": f"{bindir}:/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr
    st = up.parse_status(out.stdout)
    assert st.kernel == "6.12.47+rpt-rpi-2712" and st.latest_kernel == "6.12.47+rpt-rpi-2712"
    assert not any("Kernel" in r for r in st.reboot_reasons)


def test_version_key_never_compares_numbers_with_text():
    for a, b in [("6.12.47+rpt-rpi-v8", "6.12.47+rpt-rpi-2712"), ("6.8.12-4-pve", "6.8.12-pve"), ("6.1.0-rc1", "6.1.0-1")]:
        assert isinstance(up._version_key(a) > up._version_key(b), bool)
    assert up._version_key("6.8.12-10-pve") > up._version_key("6.8.12-4-pve")


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell (Git-Bash-sh scheitert an Windows-Pfaden)")
def test_status_command_runs_in_a_real_shell():
    out = subprocess.run(["sh", "-c", up.status_command(refresh=False)], capture_output=True, text=True, timeout=60)
    st = up.parse_status(out.stdout)
    assert out.returncode == 0
    assert "@@end" in out.stdout and st.kernel  # uname -r gibt es ueberall


def test_parse_upgrade_output():
    ok = up.parse_upgrade_output("...\n12 upgraded, 1 newly installed, 0 to remove and 0 not upgraded.\n@@upgrade-done\n@@reboot-required\n", 0)
    assert (ok.ok, ok.upgraded, ok.reboot_required) == (True, 13, True)
    assert ok.summary == "13 Paket(e) aktualisiert – Neustart nötig"
    bad = up.parse_upgrade_output("E: dpkg was interrupted, you must manually run 'dpkg --configure -a'\n@@upgrade-done\n", 100)
    assert not bad.ok and bad.summary.startswith("E: dpkg was interrupted")
    # Protokoll und Meldungen bekommen keine Zeile vom Server: bei Erfolg derselbe Text, bei einem Fehler ein fester.
    assert ok.public == ok.summary
    assert bad.public == "Fehlgeschlagen (Rückgabecode 100). Einzelheiten stehen in der Update-Zentrale."
    assert up.parse_upgrade_output("x", None).public == "Fehlgeschlagen. Einzelheiten stehen in der Update-Zentrale."


def test_update_report_levels():
    from nodvard_deck_ext_nexus_soc.patching import build_update_report

    h1 = SimpleNamespace(name="a", display_name="pve2")
    h2 = SimpleNamespace(name="b", display_name="Pi")
    title, body, level = build_update_report([
        (h1, {"manager": "apt", "count": 3, "security_count": 2, "reboot_required": True}),
        (h2, {"manager": "apt", "count": 0, "security_count": 0}),
    ])
    assert title == "2 Sicherheitsupdate(s) offen" and level == "warning"
    assert body == "- pve2: 3 Update(s), davon 2 Sicherheit, Neustart nötig"
    assert build_update_report([(h2, {"manager": "apt", "count": 0})])[2] == "none"
    # Ein NAS mit eigener Firmware (kein apt/dnf/apk) ist kein Fehler und kommt nicht jeden Tag als Warnung.
    nas = {"manager": None, "error": "Kein unterstützter Paketmanager (apt, dnf, apk) gefunden.", "unsupported": True}
    assert build_update_report([(h2, nas)])[2] == "none"
    title, body, level = build_update_report([(h1, {"manager": "apt", "count": 1, "security_count": 0}), (h2, nas)])
    assert (title, body, level) == ("1 Update(s) offen", "- pve2: 1 Update(s)", "info")
    # Ein echter Fehler (z. B. Zeitüberschreitung) bleibt eine Warnung.
    assert build_update_report([(h2, {"manager": None, "error": "Zeitüberschreitung"})])[2] == "warning"
    # Auch mit einem alten guten Stand (Paketmanager gesetzt) zählt eine fehlgeschlagene Prüfung als Fehler:
    # ohne Zahlen, mit dem Zeitpunkt des Stands.
    stale = {"manager": "apt", "count": 4, "security_count": 2, "error": "Verbindung abgelehnt", "checked_at": _BERLIN_0600}
    title, body, level = build_update_report([(h2, stale)], zone=ZoneInfo("Europe/Berlin"))
    assert (title, level) == ("Update-Prüfung: 1 Fehler", "warning")
    assert body == "- Pi: Prüfung fehlgeschlagen (Verbindung abgelehnt), Stand von 02.10.2026 06:00"
    title, body, _ = build_update_report([(h1, {"manager": "apt", "count": 1, "security_count": 0}), (h2, stale)], zone=ZoneInfo("Europe/Berlin"))
    assert title == "1 Update(s) offen" and body.splitlines() == ["- pve2: 1 Update(s)", "- Pi: Prüfung fehlgeschlagen (Verbindung abgelehnt), Stand von 02.10.2026 06:00"]
    assert build_update_report([(h2, {**stale, "checked_at": None})])[1] == "- Pi: Prüfung fehlgeschlagen (Verbindung abgelehnt)"
    assert build_update_report([(h2, stale)])[1].endswith("Stand von 02.10.2026 04:00 UTC")  # ohne Zone: UTC, so beschriftet


# --- Ablauf mit Fake-Kontext -------------------------------------------------------

# Antworten des Servers auf die Abfrage eines entkoppelten Laufs (detached.py).
POLL_RUNNING = "@@running\n@@tail\nUnpacking openssl ...\n"
POLL_DONE = ("@@rc=0\n@@summary\n2 upgraded, 0 newly installed, 0 to remove\n@@tail\nSetting up openssl ...\n"
             "2 upgraded, 0 newly installed, 0 to remove\n@@upgrade-done\n")
POLL_LOST = "@@lost\n@@summary\n@@tail\nSetting up docker-ce ...\n"
POLL_UNKNOWN = "@@unknown\n@@tail\n"
LAUNCH_OK = "@@launch\n@@method=systemd\n@@pid=42\n"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Die Abfrage-Pausen (10 s) und die Wartezeit beim Wiederaufnehmen weglassen."""
    from nodvard_deck_ext_nexus_soc import patching

    monkeypatch.setattr(patching, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(patching, "RESUME_DELAY_S", 0)
    monkeypatch.setattr(patching, "LATE_POLL_INTERVAL_S", 0)
    monkeypatch.setattr(patching, "LATE_FOLLOW_S", 0)  # Nachfrage nach der Wartezeit: einmal, dann Schluss


class _Ctx:
    def __init__(self, sessionmaker, settings=None):
        from nodvard_sdk import Host

        self._sm = sessionmaker
        self._settings_value = settings or {}
        self.commands: list[str] = []
        self.audits: list[dict] = []
        self.notes: list = []
        self.after_upgrade = False
        # Entkoppelte Laeufe: Antwort auf den Start und die Abfragen der Reihe nach
        # (Text oder Ausnahme; die letzte Antwort bleibt stehen).
        self.launch_reply: str | Exception = LAUNCH_OK
        self.polls: list[str | Exception] = [POLL_RUNNING, POLL_DONE]
        self.timeouts: list[int] = []
        self.center = None
        self.busy_seen: list[dict] = []
        self._host = Host(id="h1", name="pi", display_name="Raspberry Pi", address="10.0.0.2", tags=["auto-update"])
        self.core_settings: dict[str, object] = {"system.timezone": "Europe/Berlin"}
        self.settings = SimpleNamespace(get=self._settings, core=self._core_setting)
        self.hosts = SimpleNamespace(list=self._list, get=self._get)
        self.exec = SimpleNamespace(run=self._run)
        self.db = SimpleNamespace(session=self._session)
        self.audit = SimpleNamespace(log=self._audit)
        self.notify = SimpleNamespace(send=self._notify)
        self.ws = SimpleNamespace(broadcast=self._ws)
        self.requirements: list = []
        self.ui = SimpleNamespace(register_host_requirement=self.requirements.append)

    async def _settings(self):
        return self._settings_value

    async def _core_setting(self, key):
        # Wie der Kern (`ext/context.py`, `SettingsHandle.core`): die gespeicherte Zeile `{"value": ...}`
        return {"value": self.core_settings[key]} if key in self.core_settings else None

    async def _list(self, tag=None):
        return [self._host]

    async def _get(self, host_id):
        return self._host if host_id == "h1" else None

    async def _run(self, host, command, timeout_s=60):
        self.commands.append(command)
        self.timeouts.append(timeout_s)
        if "@@launch" in command:
            if "only-upgrade" in command:
                self.after_upgrade = True
            if isinstance(self.launch_reply, Exception):
                raise self.launch_reply
            return SimpleNamespace(exit_code=0, stdout=self.launch_reply, stderr="", duration_ms=5)
        if "@@running" in command:
            if self.center is not None:
                self.busy_seen.append(dict(self.center._busy))
            reply = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
            if isinstance(reply, Exception):
                raise reply
            if isinstance(reply, SimpleNamespace):  # fertige Antwort (z. B. Kanal verloren: -1, leer)
                return reply
            return SimpleNamespace(exit_code=0, stdout=reply, stderr="", duration_ms=5)
        if "only-upgrade" in command:
            self.after_upgrade = True
            return SimpleNamespace(exit_code=0, stdout="2 upgraded, 0 newly installed, 0 to remove\n@@upgrade-done\n", stderr="", duration_ms=5)
        if "@@pm" in command:
            text = APT_STATUS
            if self.after_upgrade:
                text = text.replace("openssl/stable-security", "#").replace("libssl3/bookworm-security", "#")
            return SimpleNamespace(exit_code=0, stdout=text, stderr="", duration_ms=5)
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

    async def _ws(self, *a, **k):
        return None


async def _engine():
    from nodvard_deck_ext_nexus_soc.models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_check_then_security_upgrade_is_recorded_and_rechecked():
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter

    engine, sm = await _engine()
    ctx = _Ctx(sm)
    center = UpdateCenter(ctx, Defender(ctx))
    host = ctx._host

    [st] = await center.check([host])
    assert (st["count"], st["security_count"]) == (3, 2)
    o = await center.overview()
    assert o["summary"] == {"hosts": 1, "checked": 1, "up_to_date": 0, "packages": 3, "security": 2, "reboot": 1, "errors": 0}

    command = await center.command_for(host, "security")
    assert "install --only-upgrade libssl3 openssl" in command
    ok, summary, _out, _rc = await center.execute(host, "security", command=command, trigger="manual")
    assert ok and summary == "2 Paket(e) aktualisiert"
    assert any("sudo -n" in c and "only-upgrade" in c for c in ctx.commands)  # laeuft als root
    # Entkoppelt gestartet (systemd-run), dann abgefragt -- nie im Kanal
    [launch] = [c for c in ctx.commands if "@@launch" in c]
    assert "systemd-run --unit=lattice-upgrade-upd_" in launch and "only-upgrade" in launch
    assert sum("@@running" in c for c in ctx.commands) == 2
    o = await center.overview()
    assert o["hosts"][0]["status"]["security_count"] == 0
    assert o["runs"][0]["status"] == "ok" and o["runs"][0]["mode_label"] == "Sicherheitsupdates"
    assert ctx.audits[-1]["action"] == "nexus_soc.updates_security"
    await engine.dispose()


@pytest.mark.asyncio
async def test_server_without_a_known_package_manager_is_unsupported_not_failed():
    """Ohne Proxmox/Debian: ein NAS mit eigener Firmware. Die Update-Zentrale sagt „nicht unterstützt“, zählt es
    nicht als Fehler und meldet bei den automatischen Updates nichts."""
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter

    engine, sm = await _engine()
    ctx = _Ctx(sm, {"auto_updates_mode": "security", "auto_updates_host_tag": "auto-update"})

    async def nas(host, command, timeout_s=60):
        ctx.commands.append(command)
        return SimpleNamespace(exit_code=0, stdout="@@pm\nnone\n@@end\n", stderr="", duration_ms=5)

    ctx.exec = SimpleNamespace(run=nas)
    center = UpdateCenter(ctx, Defender(ctx))
    [st] = await center.check([ctx._host])
    assert st["unsupported"] is True and st["manager"] is None
    # "Kein Paketmanager" ist ein gueltiges Ergebnis: geprueft, nicht "noch nie erfolgreich geprueft"
    assert isinstance(st["checked_at"], float) and st["attempted_at"] == st["checked_at"]
    summary = (await center.overview())["summary"]
    assert (summary["errors"], summary["checked"], summary["hosts"]) == (0, 0, 1)

    assert await center.auto_update() == {"hosts": 1, "failed": 0}
    assert ctx.notes == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_server_that_turns_out_to_have_no_known_package_manager_replaces_its_old_state():
    """Der alte Stand (apt) bleibt nur bei einem Fehlschlag stehen. "Kein Paketmanager" ist keiner:
    sonst zeigte die Karte ewig die Pakete eines Servers, auf dem es keinen Paketmanager mehr gibt."""
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter

    engine, sm = await _engine()
    ctx = _Ctx(sm)
    center = UpdateCenter(ctx, Defender(ctx))
    assert (await center.check_host(ctx._host))["count"] == 3

    async def nas(host, command, timeout_s=60):
        return SimpleNamespace(exit_code=0, stdout="@@pm\nnone\n@@end\n", stderr="", duration_ms=5)

    ctx.exec = SimpleNamespace(run=nas)
    st = await center.check_host(ctx._host)
    assert st["unsupported"] is True and st["manager"] is None and st["count"] == 0 and st["checked_at"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_overview_config_tells_the_page_about_the_dist_upgrade_option():
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter

    engine, sm = await _engine()
    for settings, want in (({}, False), ({"proxmox_dist_upgrade": True}, True), ({"proxmox_dist_upgrade": False}, False)):
        ctx = _Ctx(sm, settings)
        assert (await UpdateCenter(ctx, Defender(ctx)).overview())["config"]["proxmox_dist_upgrade"] is want
    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_update_runs_only_on_tagged_hosts_and_reports():
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter

    engine, sm = await _engine()
    ctx = _Ctx(sm, {"auto_updates_mode": "security", "auto_updates_host_tag": "auto-update"})
    center = UpdateCenter(ctx, Defender(ctx))
    res = await center.auto_update()
    assert res == {"hosts": 1, "failed": 0}
    assert ctx.notes and "Raspberry Pi: 2 Paket(e) aktualisiert" in ctx.notes[-1].body
    assert ctx.notes[-1].payload["host_id"] == "h1"  # ein Server im Bericht: die naechtliche Meldung gehoert ihm
    assert not any("shutdown" in c for c in ctx.commands)  # ohne auto_reboot kein Neustart
    assert any("systemd-run" in c and "only-upgrade" in c and "sudo -n" in c for c in ctx.commands)

    ctx2 = _Ctx(sm, {"auto_updates_host_tag": "gibt-es-nicht"})
    assert (await UpdateCenter(ctx2, Defender(ctx2)).auto_update())["hosts"] == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_update_report_over_several_servers_names_all_of_them():
    """Der naechtliche Bericht ueber mehrere Server traegt alle (`host_ids`),
    nicht nur einen -- ein Wartungsfenster haelt ihn nur zurueck, wenn es fuer alle gilt."""
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter
    from nodvard_sdk import Host

    engine, sm = await _engine()
    ctx = _Ctx(sm, {"auto_updates_mode": "security", "auto_updates_host_tag": "auto-update"})
    pve2 = Host(id="h2", name="pve2", display_name="pve2", address="10.0.0.3", tags=["auto-update"])
    ctx.hosts.list = lambda tag=None: _hosts(ctx._host, pve2)
    res = await UpdateCenter(ctx, Defender(ctx)).auto_update()
    assert res["hosts"] == 2
    assert ctx.notes[-1].title.startswith("Automatische Updates: 2 Server")
    assert ctx.notes[-1].payload["host_ids"] == ["h1", "h2"] and "host_id" not in ctx.notes[-1].payload
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_automatic_run_never_uses_dist_upgrade_even_when_the_setting_is_on():
    """dist-upgrade darf Pakete entfernen und gehoert nur in eine Aktion, die jemand
    freigibt. Der Weg der Oberflaeche (Vorschlag -> Freigabe -> Ausfuehrung) steht in
    test_ext_nexus_soc_actions.py; hier: die automatische Nacht-Aktualisierung ignoriert die Einstellung."""
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter

    engine, sm = await _engine()
    ctx = _Ctx(sm, {"auto_updates_mode": "all", "auto_updates_host_tag": "auto-update", "proxmox_dist_upgrade": True})
    center = UpdateCenter(ctx, Defender(ctx))
    ctx.polls = [POLL_RUNNING, POLL_DONE]
    res = await center.auto_update()
    assert res == {"hosts": 1, "failed": 0}
    [launch] = [c for c in ctx.commands if "@@launch" in c]
    assert "upgrade --with-new-pkgs" in launch and "dist-upgrade" not in launch and "pveversion" not in launch
    assert "sudo -n" in launch  # laeuft wie immer als root
    await engine.dispose()


def test_briefing_mentions_updates():
    from nodvard_deck_ext_nexus_soc.defender import build_briefing

    overview = {"summary": {"score": 90, "protected": 1, "hosts": 1, "open_threats": 0, "quarantined": 0, "findings_30d": 0}, "hosts": []}
    updates = {"summary": {"checked": 1, "packages": 3, "security": 2, "reboot": 1},
               "hosts": [{"host_name": "pve2", "status": {"security_count": 2}}]}
    title, body, level = build_briefing(overview, [], updates)
    assert "Updates: 3 offen, davon 2 Sicherheit · Neustart nötig: 1 Server" in body
    assert "pve2: 2 Sicherheitsupdate(s)" in body and level == "warning"


def test_refresh_errors_name_the_real_cause():
    base = "@@pm\napt\n@@refresh\n{}\n@@list\n@@reboot\nno\n@@end"
    pve = up.parse_status(base.format(
        "E: Failed to fetch https://enterprise.proxmox.com/debian/pve/dists/bookworm/InRelease  401  Unauthorized [IP: 1.2.3.4 443]"))
    assert "Abo" in pve.refresh_error
    other = up.parse_status(base.format("Err:1 http://deb.example.org stable InRelease\n  Temporary failure resolving 'deb.example.org'"))
    assert other.refresh_error.startswith("Nicht alle Paketquellen erreichbar: Err:1")
    root = up.parse_status(base.format("E: Could not open lock file /var/lib/apt/lists/lock - open (13: Permission denied)"))
    assert "root-Rechte" in root.refresh_error


@pytest.mark.asyncio
async def test_runs_left_over_from_a_restart_are_marked_as_aborted_on_start():
    """Wird das Dashboard mitten in einem Scan oder Update-Lauf neu
    gestartet, kommt der Lauf nie zurueck. Beim Start werden solche Reste als
    abgebrochen markiert, statt fuer immer auf "laeuft" zu stehen."""
    from datetime import UTC, datetime

    from nodvard_deck_ext_nexus_soc import Extension
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.models import ScanRecord, UpdateRunRecord
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter

    engine, sm = await _engine()
    ctx = _Ctx(sm)
    spawned: list[str] = []

    def _spawn(coro, name=None):
        spawned.append(name)
        coro.close()  # Hintergrund-Schleifen interessieren hier nicht

    ctx.spawn = _spawn
    started = datetime(2026, 9, 28, 3, 30, tzinfo=UTC)
    async with sm() as s:
        s.add_all([
            ScanRecord(id="scan_run", host_id="h1", host_name="Raspberry Pi", kind="watch", paths=["/tmp"], trigger="schedule",
                       status="running", infected=0, started_at=started),
            ScanRecord(id="scan_done", host_id="h1", host_name="Raspberry Pi", kind="quick", paths=["/tmp"], trigger="manual",
                       status="clean", infected=0, started_at=started, finished_at=started),
            UpdateRunRecord(id="upd_run", host_id="h1", host_name="Raspberry Pi", mode="security", trigger="schedule",
                            status="running", upgraded=0, started_at=started),
            # Laeuft entkoppelt auf dem Server weiter -> nicht abbrechen, wieder aufnehmen
            UpdateRunRecord(id="upd_0123456789abcdef", host_id="h1", host_name="Raspberry Pi", mode="all", trigger="manual",
                            status="running", upgraded=0, started_at=started, remote_id="upd_0123456789abcdef"),
            UpdateRunRecord(id="upd_done", host_id="h1", host_name="Raspberry Pi", mode="all", trigger="manual",
                            status="ok", upgraded=3, summary="3 Paket(e) aktualisiert", started_at=started, finished_at=started),
        ])
        await s.commit()

    ext = Extension()
    ext._defender = Defender(ctx)
    ext._updates = UpdateCenter(ctx, ext._defender)
    ext._watcher = SimpleNamespace(run_forever=lambda: asyncio.sleep(0))
    await ext.on_start(ctx)

    async with sm() as s:
        scan = await s.get(ScanRecord, "scan_run")
        run = await s.get(UpdateRunRecord, "upd_run")
        assert (scan.status, scan.error) == ("error", "Abgebrochen – Dashboard wurde neu gestartet")
        assert scan.finished_at is not None
        assert (run.status, run.summary) == ("error", "Abgebrochen – Dashboard wurde neu gestartet")
        assert run.finished_at is not None
        # Abgeschlossene Laeufe bleiben unberuehrt.
        done_scan = await s.get(ScanRecord, "scan_done")
        done_run = await s.get(UpdateRunRecord, "upd_done")
        assert (done_scan.status, done_scan.error) == ("clean", None)
        assert (done_run.status, done_run.summary) == ("ok", "3 Paket(e) aktualisiert")
        remote = await s.get(UpdateRunRecord, "upd_0123456789abcdef")
        assert (remote.status, remote.summary) == ("running", None)
    assert "nexus-soc-docker-watcher" in spawned and "nexus-soc-update-resume" in spawned
    # on_start meldet die Gruppe "docker" fuer die Container-Wache mit dem eingestellten Tag.
    assert [(r.id, r.unix_group, r.tags) for r in ctx.requirements] == [("docker-group", "docker", ["docker"])]
    await engine.dispose()


# --- Nach "Neu starten" nicht weiter "Neustart noetig" zeigen -----------------

REBOOTED_STATUS = APT_STATUS.replace("@@kernel\n6.8.12-4-pve", "@@kernel\n6.8.12-10-pve").replace("123456.78", "42.0")


class _RebootCtx(_Ctx):
    """Wie `_Ctx`, aber nach dem Neustart-Befehl meldet der Server den neuen Kernel und
    eine kurze Laufzeit -- sobald `back_online` gesetzt ist."""

    def __init__(self, sessionmaker, settings=None):
        super().__init__(sessionmaker, settings)
        self.rebooted = False
        self.back_online = asyncio.Event()
        self.back_online.set()

    async def _run(self, host, command, timeout_s=60):
        if "shutdown -r" in command:
            self.commands.append(command)
            self.rebooted = True
            return SimpleNamespace(exit_code=0, stdout="Shutdown scheduled", stderr="", duration_ms=5)
        if "@@pm" in command and self.rebooted:
            self.commands.append(command)
            await self.back_online.wait()
            return SimpleNamespace(exit_code=0, stdout=REBOOTED_STATUS, stderr="", duration_ms=5)
        return await super()._run(host, command, timeout_s)


@pytest.mark.asyncio
async def test_a_check_before_the_server_went_down_keeps_the_reboot_note():
    import time

    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import (
        UpdateCenter,
        load_baseline,
        save_baseline,
    )

    engine, sm = await _engine()
    ctx = _RebootCtx(sm)
    center = UpdateCenter(ctx, Defender(ctx))
    host = ctx._host
    [st] = await center.check([host])
    assert st["reboot_required"] and (await center.overview())["summary"]["reboot"] == 1

    # Neustart ausgeloest (shutdown -r +1), der Server laeuft aber noch -- eine Pruefung
    # in dieser Minute sieht noch den alten Kernel und eine lange Laufzeit.
    since = time.time() - 30
    await save_baseline(ctx, "h1", "updates", {**st, "reboot_pending_since": since})
    [st] = await center.check([host], refresh=False)
    assert st["reboot_required"] and st["reboot_pending_since"] == since
    o = await center.overview()
    assert o["summary"]["reboot"] == 0  # nicht mehr als "Neustart noetig" gezaehlt
    assert o["hosts"][0]["status"]["reboot_pending_since"] == since

    # Ist der Neustart laengst her und der Server trotzdem nicht neu gestartet, gilt
    # wieder der gepruefte Stand.
    await save_baseline(ctx, "h1", "updates", {**st, "reboot_pending_since": time.time() - 3 * 3600})
    [st] = await center.check([host], refresh=False)
    assert st["reboot_required"] and "reboot_pending_since" not in st
    assert (await center.overview())["summary"]["reboot"] == 1

    # Nach dem echten Neustart: neuer Kernel, kurze Laufzeit -> alles erledigt.
    await save_baseline(ctx, "h1", "updates", {**st, "reboot_pending_since": time.time() - 300})
    ctx.rebooted = True
    [st] = await center.check([host], refresh=False)
    assert not st["reboot_required"] and "reboot_pending_since" not in st
    assert await load_baseline(ctx, "h1", "updates") == st
    await engine.dispose()


@pytest.mark.asyncio
async def test_reboot_is_rechecked_once_the_server_is_back(monkeypatch):
    from nodvard_deck_ext_nexus_soc import patching
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter, load_baseline

    monkeypatch.setattr(patching, "RECHECK_AFTER_REBOOT_S", 0)
    engine, sm = await _engine()
    ctx = _RebootCtx(sm)
    center = UpdateCenter(ctx, Defender(ctx))
    host = ctx._host
    await center.check([host])

    ctx.back_online.clear()  # der Server ist nach dem Neustart erst einmal weg
    ok, summary, _out, _rc = await center.execute(host, "reboot", command=up.REBOOT_COMMAND, trigger="manual")
    assert ok and summary == "Neustart in einer Minute."
    st = await load_baseline(ctx, "h1", "updates")
    assert st["reboot_required"] and st["reboot_pending_since"]
    assert (await center.overview())["summary"]["reboot"] == 0

    ctx.back_online.set()
    await asyncio.wait_for(asyncio.gather(*list(center._rechecks)), timeout=10)
    st = await load_baseline(ctx, "h1", "updates")
    assert not st["reboot_required"] and "reboot_pending_since" not in st and st["kernel"] == "6.8.12-10-pve"
    assert not center._rechecks  # erledigte Nachpruefungen werden nicht aufgehoben
    await engine.dispose()


@pytest.mark.asyncio
async def test_daily_check_skips_a_server_that_is_being_checked_but_still_reports_it():
    """Laeuft um 06:00 noch eine Pruefung fuer einen Server, ueberspringt
    check() ihn -- der Tagesjob paarte danach Server und Ergebnisse mit strict=True und
    brach mit ValueError ab (keine Meldung an diesem Tag)."""
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter, save_baseline
    from nodvard_sdk import Host

    engine, sm = await _engine()
    ctx = _Ctx(sm)
    pve2 = Host(id="h2", name="pve2", display_name="pve2", address="10.0.0.3")
    ctx.hosts.list = lambda tag=None: _hosts(ctx._host, pve2)
    await save_baseline(ctx, "h2", "updates", {"manager": "apt", "count": 1, "security_count": 0, "reboot_required": True})
    center = UpdateCenter(ctx, Defender(ctx))
    center._checking.add("h2")  # z. B. eine manuelle "Pruefen"-Anfrage laeuft noch

    res = await center.scheduled_check()
    assert res["title"] == "2 Sicherheitsupdate(s) offen"
    body = ctx.notes[-1].body
    assert "- Raspberry Pi: 3 Update(s), davon 2 Sicherheit, Neustart nötig" in body
    assert "- pve2: 1 Update(s), Neustart nötig" in body  # aus dem zuletzt gespeicherten Stand
    assert sum("@@pm" in c for c in ctx.commands) == 1  # pve2 wurde nicht doppelt geprueft
    assert ctx.notes[-1].payload["host_ids"] == ["h1", "h2"]  # Sammelbericht: still nur, wenn beide im Fenster liegen
    assert "host_id" not in ctx.notes[-1].payload
    await engine.dispose()


@pytest.mark.asyncio
async def test_scheduled_check_report_of_a_single_server_belongs_to_that_host():
    """Genau ein Server im Tagesbericht -> die Meldung traegt seine
    Host-ID, damit ein Wartungsfenster sie stumm schalten kann."""
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter

    engine, sm = await _engine()
    ctx = _Ctx(sm)
    await UpdateCenter(ctx, Defender(ctx)).scheduled_check()
    assert ctx.notes[-1].payload["host_id"] == "h1"
    assert ctx.notes[-1].payload["path"].endswith("?tab=updates")
    await engine.dispose()


@pytest.mark.asyncio
async def test_daily_report_keeps_naming_a_server_that_is_down_after_one_good_check(monkeypatch):
    """Nach einer guten Pruefung blieb der Stand samt Paketmanager erhalten, der Tagesbericht zaehlte
    einen Server nur ohne Paketmanager als fehlgeschlagen: ein dauerhaft nicht erreichbarer Server
    stand nie mehr als Fehler da, sondern mit alten Zahlen oder als "Alle Server aktuell"."""
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter
    from nodvard_sdk import Severity

    engine, sm = await _engine()
    ctx = _Ctx(sm)
    center = UpdateCenter(ctx, Defender(ctx))
    clock = _pin_clock(monkeypatch, _berlin(6, day=1))
    await center.check_host(ctx._host)  # ein guter Lauf: 3 Updates, davon 2 Sicherheit

    async def down(host, command, timeout_s=60):
        raise ConnectionError("Server nicht erreichbar")

    ctx.exec = SimpleNamespace(run=down)
    for day in (2, 3, 4):  # drei Tage in Folge nicht erreichbar
        clock["t"] = _berlin(6, day=day)
        res = await center.scheduled_check()
        note = ctx.notes[-1]
        assert res["title"] == note.title == "Update-Prüfung: 1 Fehler"
        assert note.severity == Severity.WARNING
        assert note.body.startswith("- Raspberry Pi: Prüfung fehlgeschlagen (")
        assert "Server nicht erreichbar" in note.body
        assert note.body.endswith("), Stand von 01.10.2026 06:00")  # Zeit der letzten guten Pruefung, in der Zone des Dashboards
        assert "Sicherheit" not in note.body  # die alten Zahlen zaehlen nicht mit
    await engine.dispose()


async def _hosts(*hosts):
    return list(hosts)


# --- Tagespruefung: Erfolg und Versuch trennen, ausgebliebene Server nachholen --------------


@pytest.mark.asyncio
async def test_a_failed_check_keeps_the_time_of_the_last_success_and_names_the_attempt(monkeypatch):
    """Frueher stand nach einem Fehlschlag "gerade geprueft" neben altem Stand: `checked_at` wurde bei
    jedem Versuch ueberschrieben. Jetzt bleibt es der Zeitpunkt der letzten erfolgreichen Pruefung."""
    from nodvard_deck_ext_nexus_soc import patching
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter

    engine, sm = await _engine()
    ctx = _Ctx(sm)
    center = UpdateCenter(ctx, Defender(ctx))
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr("nodvard_deck_ext_nexus_soc.defender._now", lambda: datetime.fromtimestamp(clock["t"], UTC))

    good = await center.check_host(ctx._host)
    assert (good["checked_at"], good["attempted_at"], good["error"]) == (1_000_000.0, 1_000_000.0, None)

    ok_run = ctx._run

    async def down(host, command, timeout_s=60):
        raise ConnectionError("Server nicht erreichbar")

    ctx.exec = SimpleNamespace(run=down)
    clock["t"] += 3 * 3600
    failed = await center.check_host(ctx._host)
    assert "Server nicht erreichbar" in failed["error"]
    assert failed["checked_at"] == 1_000_000.0 and failed["attempted_at"] == 1_000_000.0 + 3 * 3600
    assert failed["count"] == 3  # der letzte gute Stand bleibt sichtbar

    # Auch ein zweiter Fehlschlag in Folge verliert den guten Stand nicht
    clock["t"] += 3600
    again = await center.check_host(ctx._host)
    assert again["checked_at"] == 1_000_000.0 and again["attempted_at"] == 1_000_000.0 + 4 * 3600 and again["count"] == 3

    ctx.exec = SimpleNamespace(run=ok_run)
    clock["t"] += 3600
    back = await center.check_host(ctx._host)
    assert back["error"] is None and back["checked_at"] == back["attempted_at"] == 1_000_000.0 + 5 * 3600
    # Der Versuch liegt nach dem zuletzt faelligen Zeitpunkt: nichts ausgeblieben
    assert patching.check_overdue(back, back["attempted_at"] - 60) is False
    assert patching.check_overdue(back, back["attempted_at"] + 60) is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_server_that_never_got_a_good_check_has_no_success_time():
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter

    engine, sm = await _engine()
    ctx = _Ctx(sm)

    async def down(host, command, timeout_s=60):
        raise ConnectionError("keine Verbindung")

    ctx.exec = SimpleNamespace(run=down)
    st = await UpdateCenter(ctx, Defender(ctx)).check_host(ctx._host)
    assert st["checked_at"] is None and st["attempted_at"] and st["error"]
    await engine.dispose()


def test_check_overdue_compares_the_last_attempt_with_the_last_due_time():
    from nodvard_deck_ext_nexus_soc.patching import check_overdue

    due = 5_000_000.0
    assert check_overdue(None, due) is False and check_overdue({}, due) is False
    assert check_overdue({"checked_at": due + 60}, due) is False
    assert check_overdue({"checked_at": due - 60}, due) is True
    assert check_overdue({"checked_at": due - 60}, None) is False  # kein lesbarer Zeitplan
    # Stand von vor dem Update kennt nur `checked_at`; ein juengerer Fehlversuch zaehlt als Versuch
    assert check_overdue({"checked_at": due - 5 * 86400, "attempted_at": due + 60}, due) is False
    assert check_overdue({"checked_at": due - 5 * 86400, "attempted_at": due - 60}, due) is True
    assert check_overdue({"checked_at": None, "attempted_at": None}, due) is False


def test_last_due_is_the_latest_scheduled_moment_in_the_schedule_zone():
    from nodvard_deck_ext_nexus_soc.patching import last_due

    assert last_due("0 6 * * *", _berlin(6, 5), BERLIN) == _berlin(6)
    assert last_due("0 6 * * *", _berlin(6), BERLIN) == _berlin(6)  # genau zur Zeit: faellig
    assert last_due("0 6 * * *", _berlin(5, 59), BERLIN) == _berlin(6, day=1)  # heute noch nicht dran: gestern
    assert last_due("0 */6 * * *", _berlin(13), BERLIN) == _berlin(12)
    assert last_due("15 4,16 * * *", _berlin(10), BERLIN) == _berlin(4, 15)
    assert last_due("*/20 * * * *", _berlin(9, 50), BERLIN) == _berlin(9, 40)
    assert last_due("10-30/10 5 * * *", _berlin(8), BERLIN) == _berlin(5, 30)
    # Die Zone entscheidet: 06:00 Uhr in UTC ist eine andere Zeit als 06:00 Uhr in Berlin
    assert last_due("0 6 * * *", _berlin(8, 5), ZoneInfo("UTC")) == datetime(2026, 10, 2, 6, tzinfo=UTC).timestamp()
    # Ueber eine Zeitumstellung hinweg (Ende der Sommerzeit am 25.10.2026)
    after = datetime(2026, 10, 26, 6, 5, tzinfo=BERLIN).timestamp()
    assert last_due("0 6 * * *", after, BERLIN) == datetime(2026, 10, 26, 6, tzinfo=BERLIN).timestamp()
    assert last_due("0 6 * * *", datetime(2026, 10, 26, 5, tzinfo=BERLIN).timestamp(), BERLIN) == datetime(2026, 10, 25, 6, tzinfo=BERLIN).timestamp()
    for cron in ("0 6 * * 1", "0 6 1 * *", "kaputt", "61 6 * * *", "0 25 * * *", "0 6 * * * *", "0 */0 * * *", "a-b 6 * * *", "30-10 6 * * *", ""):
        assert last_due(cron, _berlin(7), BERLIN) is None, cron


@pytest.mark.asyncio
async def test_schedule_zone_reads_the_zone_set_in_the_dashboard_through_the_real_core(db_session):
    """Der Kern speichert Einstellungen als `{"value": ...}`, und `ctx.settings.core()` gibt genau das
    zurueck. Die eingestellte Zone muss trotzdem ankommen, sonst rechnet die Pruefung in der
    Vorgabe-Zone statt in der Zone, in der der Zeitplaner laeuft."""
    from nodvard_deck.core import timezone as tz_service
    from nodvard_deck.ext.context import SettingsHandle
    from nodvard_deck_ext_nexus_soc.patching import schedule_zone

    ctx = SimpleNamespace(settings=SettingsHandle(None, "nexus-soc"))
    assert await schedule_zone(ctx) == ZoneInfo("Europe/Berlin")  # nichts eingestellt: Vorgabe
    await tz_service.set_timezone(db_session, "Asia/Tokyo")
    assert await ctx.settings.core("system.timezone") == {"value": "Asia/Tokyo"}
    assert await schedule_zone(ctx) == ZoneInfo("Asia/Tokyo")


@pytest.mark.asyncio
async def test_schedule_zone_prefers_the_core_setting_and_skips_unknown_names(monkeypatch):
    from nodvard_deck_ext_nexus_soc.patching import DEFAULT_ZONE, schedule_zone

    engine, sm = await _engine()
    ctx = _Ctx(sm)
    for name in ("NODVARD_DECK_TIMEZONE", "LATTICE_TIMEZONE", "TZ"):
        monkeypatch.delenv(name, raising=False)
    assert await schedule_zone(ctx) == ZoneInfo("Europe/Berlin")
    ctx.core_settings["system.timezone"] = "Asia/Tokyo"
    assert await schedule_zone(ctx) == ZoneInfo("Asia/Tokyo")
    # Nichts oder Unbrauchbares gespeichert: Umgebung, zuletzt die Vorgabe des Kerns
    ctx.core_settings["system.timezone"] = "Mars/Base"
    monkeypatch.setenv("TZ", "America/New_York")
    assert await schedule_zone(ctx) == ZoneInfo("America/New_York")
    monkeypatch.setenv("NODVARD_DECK_TIMEZONE", "Europe/Lisbon")
    assert await schedule_zone(ctx) == ZoneInfo("Europe/Lisbon")
    monkeypatch.setenv("NODVARD_DECK_TIMEZONE", "unsinn")
    monkeypatch.setenv("TZ", "auch/unsinn")
    assert await schedule_zone(ctx) == ZoneInfo(DEFAULT_ZONE)
    ctx.settings = SimpleNamespace(get=ctx._settings)  # alter Kern ohne `core`
    assert await schedule_zone(ctx) == ZoneInfo(DEFAULT_ZONE)
    await engine.dispose()


def _pin_clock(monkeypatch, t: float) -> dict[str, float]:
    clock = {"t": t}
    monkeypatch.setattr("nodvard_deck_ext_nexus_soc.defender._now", lambda: datetime.fromtimestamp(clock["t"], UTC))
    return clock


@pytest.mark.asyncio
async def test_overview_flags_a_server_whose_daily_check_did_not_happen(monkeypatch):
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter, save_baseline
    from nodvard_sdk import Host

    engine, sm = await _engine()
    ctx = _Ctx(sm)
    pve2 = Host(id="h2", name="pve2", display_name="pve2", address="10.0.0.3")
    ctx.hosts.list = lambda tag=None: _hosts(ctx._host, pve2)
    center = UpdateCenter(ctx, Defender(ctx))
    clock = _pin_clock(monkeypatch, _berlin(6, 30))
    await save_baseline(ctx, "h1", "updates", {"manager": "apt", "count": 0, "security_count": 0, "checked_at": _berlin(6, 1), "attempted_at": _berlin(6, 1)})
    await save_baseline(ctx, "h2", "updates", {"manager": "apt", "count": 0, "security_count": 0, "checked_at": _berlin(6, 1, day=1)})

    async def flags():
        return {r["host_id"]: r["check_overdue"] for r in (await center.overview())["hosts"]}

    assert await flags() == {"h1": False, "h2": True}

    # Direkt nach dem Zeitpunkt laeuft die Tagespruefung noch: noch kein Hinweis
    clock["t"] = _berlin(6, 5)
    assert await flags() == {"h1": False, "h2": False}
    # Ein Server, der gerade geprueft wird, bekommt keinen Hinweis
    clock["t"] = _berlin(6, 30)
    center._checking.add("h2")
    assert await flags() == {"h1": False, "h2": False}
    center._checking.discard("h2")

    # Ein Versuch heute nach dem Zeitpunkt (auch ein fehlgeschlagener) genuegt
    await save_baseline(ctx, "h2", "updates", {"manager": "apt", "count": 0, "checked_at": _berlin(6, 1, day=1), "attempted_at": _berlin(6, 2), "error": "weg"})
    assert await flags() == {"h1": False, "h2": False}
    await save_baseline(ctx, "h2", "updates", {"manager": "apt", "count": 0, "checked_at": _berlin(6, 1, day=1)})

    # Ausgeschaltete oder nur woechentliche Pruefung: ein alter Stand ist gewollt, kein Hinweis
    for settings in ({"updates_check_enabled": False}, {"updates_check_cron": "0 6 * * 0"}):
        ctx._settings_value = settings
        assert await flags() == {"h1": False, "h2": False}, settings
    ctx._settings_value = {"updates_check_cron": "0 */6 * * *"}  # der letzte Zeitpunkt war 06:00 Uhr
    assert await flags() == {"h1": False, "h2": True}
    clock["t"] = _berlin(12, 30)  # 12:00 Uhr verpasst: auch h1 (letzter Versuch 06:01 Uhr)
    assert await flags() == {"h1": True, "h2": True}
    await engine.dispose()


def test_overdue_applies_only_to_an_enabled_daily_or_more_frequent_check():
    from nodvard_deck_ext_nexus_soc.patching import overdue_applies

    assert overdue_applies({}) and overdue_applies({"updates_check_cron": "15 4 * * *"})
    assert overdue_applies({"updates_check_cron": "0 */6 * * *"})
    assert not overdue_applies({"updates_check_enabled": False})
    assert not overdue_applies({"updates_check_cron": "0 6 * * 1"}) and not overdue_applies({"updates_check_cron": "0 6 1 * *"})
    assert not overdue_applies({"updates_check_cron": "kaputt"})


@pytest.mark.asyncio
async def test_one_server_failing_inside_the_check_does_not_stop_the_others(monkeypatch):
    """Ein Fehler beim Speichern eines Servers (nicht bei der SSH-Abfrage) brach frueher die ganze
    Sammelpruefung samt Tagesbericht ab."""
    from nodvard_deck_ext_nexus_soc import patching
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter
    from nodvard_sdk import Host

    engine, sm = await _engine()
    ctx = _Ctx(sm)
    pve2 = Host(id="h2", name="pve2", display_name="pve2", address="10.0.0.3")
    ctx.hosts.list = lambda tag=None: _hosts(ctx._host, pve2)
    real_save = patching.save_baseline

    async def flaky(c, host_id, kind, data):
        if host_id == "h2":
            raise RuntimeError("Datenbank gesperrt")
        await real_save(c, host_id, kind, data)

    monkeypatch.setattr(patching, "save_baseline", flaky)
    center = UpdateCenter(ctx, Defender(ctx))
    res = await center.scheduled_check()
    assert res["hosts"] == 2
    body = ctx.notes[-1].body
    assert "- Raspberry Pi: 3 Update(s), davon 2 Sicherheit" in body
    assert "- pve2: Prüfung fehlgeschlagen" in body and "Datenbank gesperrt" in body
    await engine.dispose()


@pytest.mark.asyncio
async def test_after_a_start_the_missed_daily_check_is_made_up_for(monkeypatch):
    """Der Zeitplaner holt einen verpassten Lauf nicht nach (Dashboard lief um 06:00 nicht): Server mit
    ausgebliebener Pruefung werden kurz nach dem Start nachgeholt, frische nicht."""
    from nodvard_deck_ext_nexus_soc import patching
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter, save_baseline
    from nodvard_sdk import Host

    monkeypatch.setattr(patching, "CATCH_UP_DELAY_S", 0)
    clock = _pin_clock(monkeypatch, _berlin(9))
    engine, sm = await _engine()
    ctx = _Ctx(sm)
    pve2 = Host(id="h2", name="pve2", display_name="pve2", address="10.0.0.3")
    ctx.hosts.list = lambda tag=None: _hosts(ctx._host, pve2)
    old = _berlin(6, 1, day=1)  # gestern, kurz nach der Pruefzeit
    await save_baseline(ctx, "h1", "updates", {"manager": "apt", "count": 0, "checked_at": old})
    await save_baseline(ctx, "h2", "updates", {"manager": "apt", "count": 0, "checked_at": _berlin(6, 1), "attempted_at": _berlin(6, 1)})
    center = UpdateCenter(ctx, Defender(ctx))

    center.schedule_catch_up()
    center.schedule_catch_up()  # doppelt aufgerufen (Start und Einstellungsaenderung): nur ein Lauf
    await center._catch_up
    assert sum("@@pm" in c for c in ctx.commands) == 1  # nur h1
    assert (await patching.load_baseline(ctx, "h1", "updates"))["checked_at"] == clock["t"] > old
    assert (await patching.load_baseline(ctx, "h2", "updates"))["count"] == 0

    # Mit ausgeschalteter Tagespruefung wird nichts nachgeholt
    ctx.commands.clear()
    ctx._settings_value = {"updates_check_enabled": False}
    await save_baseline(ctx, "h1", "updates", {"manager": "apt", "count": 0, "checked_at": old})
    center.schedule_catch_up()
    await center._catch_up
    assert ctx.commands == []

    # Woechentlicher Zeitplan: nach einem Tag ist nichts ausgeblieben
    ctx._settings_value = {"updates_check_cron": "0 6 * * 0"}
    center.schedule_catch_up()
    await center._catch_up
    assert ctx.commands == []
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(("start", "attempt", "expect_catch_up"), [
    # Dashboard um 06:00 kurz aus, Start um 06:05: der Versuch von gestern (06:02) liegt vor dem Zeitpunkt von heute
    (_berlin(6, 5), _berlin(6, 2, day=1), True),
    # Auch ein langsamer gestriger Lauf (fertig erst 06:04) aendert daran nichts
    (_berlin(6, 1), _berlin(6, 4, day=1), True),
    # Start vor der Pruefzeit: heute ist noch nichts faellig
    (_berlin(5, 30), _berlin(6, 2, day=1), False),
    # Heute schon geprueft (Start nach der Pruefzeit, Lauf lief)
    (_berlin(6, 5), _berlin(6, 1), False),
    # Gestern Abend von Hand geprueft, um 06:00 nichts: ausgeblieben
    (_berlin(9), _berlin(21, day=1), True),
])
async def test_catch_up_follows_the_schedule_not_a_fixed_age(monkeypatch, start, attempt, expect_catch_up):
    """Eine feste Altersgrenze (frueher 30 Stunden) holte im Hauptfall nichts nach: der Versuch von
    gestern ist beim Start knapp 24 Stunden alt."""
    from nodvard_deck_ext_nexus_soc import patching
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter, save_baseline

    monkeypatch.setattr(patching, "CATCH_UP_DELAY_S", 0)
    _pin_clock(monkeypatch, start)
    engine, sm = await _engine()
    ctx = _Ctx(sm)  # Zeitplan der Tagespruefung: Vorgabe, 06:00 Uhr in der Zone des Dashboards
    await save_baseline(ctx, "h1", "updates", {"manager": "apt", "count": 0, "checked_at": attempt, "attempted_at": attempt})
    center = UpdateCenter(ctx, Defender(ctx))
    center.schedule_catch_up()
    await center._catch_up
    assert sum("@@pm" in c for c in ctx.commands) == (1 if expect_catch_up else 0)
    await engine.dispose()


@pytest.mark.asyncio
async def test_catch_up_leaves_servers_alone_that_the_running_daily_check_still_has_to_do(monkeypatch):
    """Laeuft die Tagespruefung gerade (z. B. Einstellung kurz nach 06:00 Uhr geaendert), warten Server
    noch auf ihre Reihe -- ihr letzter Versuch liegt vor dem Zeitpunkt. Das Nachholen darf sie nicht
    parallel ein zweites Mal pruefen, und die Seite meldet sie nicht als ausgeblieben."""
    from nodvard_deck_ext_nexus_soc import patching
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter, save_baseline
    from nodvard_sdk import Host

    monkeypatch.setattr(patching, "CATCH_UP_DELAY_S", 0)
    monkeypatch.setattr(patching, "MAX_PARALLEL", 1)
    clock = _pin_clock(monkeypatch, _berlin(6, 1))
    engine, sm = await _engine()
    ctx = _Ctx(sm)
    pve2 = Host(id="h2", name="pve2", display_name="pve2", address="10.0.0.3")
    ctx.hosts.list = lambda tag=None: _hosts(ctx._host, pve2)
    for host_id in ("h1", "h2"):
        await save_baseline(ctx, host_id, "updates", {"manager": "apt", "count": 0, "checked_at": _berlin(6, 1, day=1), "attempted_at": _berlin(6, 1, day=1)})
    center = UpdateCenter(ctx, Defender(ctx))
    gate, seen, plain = asyncio.Event(), [], ctx._run

    async def slow(host, command, timeout_s=60):
        if "@@pm" in command:
            seen.append(host.id)
            if host.id == "h1":
                await gate.wait()
        return await plain(host, command, timeout_s)

    ctx.exec = SimpleNamespace(run=slow)
    daily = asyncio.ensure_future(center.scheduled_check())
    for _ in range(50):
        if seen:
            break
        await asyncio.sleep(0)
    assert seen == ["h1"]  # h2 wartet auf seine Reihe

    clock["t"] = _berlin(6, 30)  # ein langsamer Lauf, laenger als die Schonfrist der Seite
    assert {r["host_id"]: r["check_overdue"] for r in (await center.overview())["hosts"]} == {"h1": False, "h2": False}
    center.schedule_catch_up()
    await center._catch_up
    assert seen == ["h1"]
    gate.set()
    await daily
    assert seen == ["h1", "h2"]  # jeder Server genau einmal
    await engine.dispose()


@pytest.mark.asyncio
async def test_stopping_the_extension_cancels_a_pending_catch_up(monkeypatch):
    from nodvard_deck_ext_nexus_soc import patching
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter, save_baseline

    monkeypatch.setattr(patching, "CATCH_UP_DELAY_S", 0.05)
    engine, sm = await _engine()
    ctx = _Ctx(sm)
    await save_baseline(ctx, "h1", "updates", {"manager": "apt", "count": 0, "checked_at": time.time() - 3 * 86400})
    center = UpdateCenter(ctx, Defender(ctx))
    center.schedule_catch_up()
    center.cancel_catch_up()
    await asyncio.sleep(0.1)
    assert center._catch_up.cancelled() and ctx.commands == []
    center.cancel_catch_up()  # zweimal ist harmlos
    await engine.dispose()




# --- Updates laufen entkoppelt auf dem Server ------------------------------


async def _center(ctx_cls=None, **kw):
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter

    engine, sm = await _engine()
    ctx = (ctx_cls or _Ctx)(sm, **kw)
    center = UpdateCenter(ctx, Defender(ctx))
    ctx.center = center
    await center.check([ctx._host])
    ctx.commands.clear()
    ctx.timeouts.clear()
    return engine, ctx, center


async def _runs(ctx):
    from nodvard_deck_ext_nexus_soc.models import UpdateRunRecord
    from sqlalchemy import select

    async with ctx._sm() as s:
        return list((await s.execute(select(UpdateRunRecord).order_by(UpdateRunRecord.started_at))).scalars())


def _polls(ctx):
    return [c for c in ctx.commands if "@@running" in c]


@pytest.mark.asyncio
async def test_a_dropped_connection_between_polls_does_not_fail_the_update():
    engine, ctx, center = await _center()
    ctx.polls = [POLL_RUNNING, OSError("Verbindung zurückgesetzt"), TimeoutError(), POLL_RUNNING, POLL_DONE]
    command = await center.command_for(ctx._host, "security")
    ok, summary, out, rc = await center.execute(ctx._host, "security", command=command, trigger="manual")
    assert (ok, summary, rc) == (True, "2 Paket(e) aktualisiert", 0)
    assert "@@upgrade-done" in out
    assert len(_polls(ctx)) == 5 and all("sudo -n" in c for c in _polls(ctx))
    # Start und Abfragen sind kurze Aufrufe -- keiner haengt 45 Minuten am Kanal.
    assert max(ctx.timeouts) < 45 * 60
    assert all(b == {"h1": "security"} for b in ctx.busy_seen) and center._busy == {}
    [run] = await _runs(ctx)
    assert (run.status, run.remote_id, run.upgraded) == ("ok", run.id, 2)
    assert run.id in _polls(ctx)[0]  # Abfrage nach genau diesem Lauf
    assert ctx.audits[-1]["action"] == "nexus_soc.updates_security"
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_run_that_died_on_the_server_is_reported_with_a_dpkg_hint():
    from nodvard_deck_ext_nexus_soc.patching import LOST_MESSAGE

    engine, ctx, center = await _center()
    ctx.polls = [POLL_RUNNING, POLL_LOST, POLL_LOST]
    ok, summary, out, rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert (ok, summary, rc) == (False, LOST_MESSAGE, None)
    assert "dpkg --configure -a" in summary and "docker-ce" in out
    [run] = await _runs(ctx)
    assert run.status == "error" and "docker-ce" in run.output_tail
    await engine.dispose()


@pytest.mark.asyncio
async def test_after_the_wait_limit_the_run_is_left_alone_on_the_server(monkeypatch):
    from nodvard_deck_ext_nexus_soc import patching

    monkeypatch.setattr(patching, "UPGRADE_TIMEOUT", 0)
    engine, ctx, center = await _center()
    ctx.polls = [POLL_RUNNING]
    ok, summary, _out, _rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert not ok and "noch weiterlaufen" in summary and "/var/lib/nexus-updates/upd_" in summary
    # Start, eine Abfrage, danach nur noch die Pruefung -- kein Abbruch-Befehl (kill/systemctl stop).
    assert ["@@launch" in c for c in ctx.commands[:1]] == [True]
    assert len(_polls(ctx)) == 1 and "@@pm" in ctx.commands[-1] and len(ctx.commands) == 3
    assert not any("kill" in c or "systemctl stop" in c for c in ctx.commands)
    # Der Server bleibt belegt, solange die gedrosselte Nachfrage laeuft.
    assert center._busy == {"h1": "all"} and len(center._late) == 1
    await asyncio.gather(*center._late)
    assert center._busy == {} and len(_polls(ctx)) == 2
    [run] = await _runs(ctx)
    assert run.status == "error" and "noch weiterlaufen" in run.summary  # Eintrag bleibt, keine Meldung
    assert not ctx.notes
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_run_that_finishes_after_the_wait_limit_is_corrected_and_reported(monkeypatch):
    """Ein langer Lauf (Kernel, Docker, langsame SD-Karte) wird nach der Wartezeit nicht
    fuer immer als Fehler gefuehrt: die Nachfrage berichtigt den Eintrag und meldet ihn."""
    from nodvard_deck_ext_nexus_soc import patching

    monkeypatch.setattr(patching, "UPGRADE_TIMEOUT", 0)
    monkeypatch.setattr(patching, "LATE_FOLLOW_S", 3600)
    engine, ctx, center = await _center()
    ctx.center = center
    ctx.polls = [POLL_RUNNING, POLL_RUNNING, POLL_DONE]
    ok, summary, _out, _rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert not ok and "noch weiterlaufen" in summary
    # Zweiter Lauf wird waehrend der Nachfrage abgelehnt.
    assert center._busy == {"h1": "all"}
    assert (await center.execute(ctx._host, "all", command="x", trigger="manual"))[1] == patching.BUSY_MESSAGE
    await asyncio.gather(*center._late)
    assert center._busy == {}
    [run] = await _runs(ctx)
    assert (run.status, run.summary, run.upgraded) == ("ok", "2 Paket(e) aktualisiert", 2)
    assert [a["action"] for a in ctx.audits] == ["nexus_soc.updates_all"] * 2
    [note] = ctx.notes
    assert note.title == "Alle Updates auf Raspberry Pi: fertig" and "länger gedauert" in note.body
    assert note.payload["host_id"] == "h1"  # sonst kommt sie im Wartungsfenster trotzdem als Push
    await engine.dispose()


@pytest.mark.asyncio
async def test_without_root_or_without_a_started_run_nothing_is_polled():
    from nodvard_deck_ext_nexus_soc.antivirus import NO_ROOT, NO_ROOT_MESSAGE

    engine, ctx, center = await _center()
    ctx.launch_reply = f"{NO_ROOT}\n"
    ok, summary, _out, _rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert (ok, summary) == (False, NO_ROOT_MESSAGE) and not _polls(ctx)

    ctx.launch_reply = "@@launch\nmkdir: cannot create directory '/var/lib/nexus-updates': Read-only file system\n@@nopid\n"
    ok, summary, _out, _rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert not ok and summary == ("Update-Lauf konnte nicht gestartet werden: mkdir: cannot create directory "
                                  "'/var/lib/nexus-updates': Read-only file system")
    assert not _polls(ctx)
    assert [r.status for r in await _runs(ctx)] == ["error", "error"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_slow_start_without_pid_is_checked_on_the_server(monkeypatch):
    """`@@nopid` mit `@@method=...`: systemd-run/setsid lief, nur die pid-Datei kam nicht in
    5 s (langsamer Pi). Der Lauf kann laufen -- nicht abbrechen, sondern nachsehen."""
    from nodvard_deck_ext_nexus_soc import patching

    engine, ctx, center = await _center()
    ctx.launch_reply = "@@launch\n@@method=systemd\n@@nopid\n"
    ctx.polls = [POLL_UNKNOWN, POLL_RUNNING, POLL_DONE]
    ok, summary, _out, _rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert ok and summary == "2 Paket(e) aktualisiert" and len(_polls(ctx)) == 3

    # Taucht der Lauf gar nicht auf, gilt er nach der Karenz als nicht gestartet.
    monkeypatch.setattr(patching, "UNKNOWN_GRACE_S", 0)
    ctx.polls = [POLL_UNKNOWN]
    ok, summary, _out, _rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert not ok and "nicht gefunden" in summary and center._busy == {}
    await engine.dispose()


@pytest.mark.asyncio
async def test_an_empty_poll_answer_is_a_lost_connection_not_an_unknown_run(monkeypatch):
    """Verliert ssh.run den Kanal, kommt Rueckgabecode -1 mit leerer Ausgabe. Das darf
    weder einen bestaetigten Lauf noch (beim Wiederaufnehmen) einen laufenden Lauf abbrechen."""
    from nodvard_deck_ext_nexus_soc import patching

    monkeypatch.setattr(patching, "UNKNOWN_GRACE_S", 0)
    engine, ctx, center = await _center()
    ctx.polls = [SimpleNamespace(exit_code=-1, stdout="", stderr=""), POLL_RUNNING, SimpleNamespace(exit_code=-1, stdout="", stderr=""), POLL_DONE]
    ok, summary, _out, _rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert ok and summary == "2 Paket(e) aktualisiert" and len(_polls(ctx)) == 4

    rid = "upd_0123456789abcdef"
    await _add_running(ctx, rid, remote_id=rid)
    ctx.commands.clear()
    ctx.polls = [SimpleNamespace(exit_code=-1, stdout="", stderr=""), POLL_RUNNING, POLL_DONE]
    assert await center.resume_interrupted() == 1
    assert len(_polls(ctx)) == 3
    assert {r.id: r.status for r in await _runs(ctx)}[rid] == "ok"
    await engine.dispose()


@pytest.mark.asyncio
async def test_two_simultaneous_calls_start_only_one_run():
    from nodvard_deck_ext_nexus_soc.patching import BUSY_MESSAGE

    engine, ctx, center = await _center()
    command = up.upgrade_command("apt", "all")
    results = await asyncio.gather(*(center.execute(ctx._host, "all", command=command, trigger=t) for t in ("manual", "schedule")))
    assert sorted(r[1] == BUSY_MESSAGE for r in results) == [False, True]
    assert len([c for c in ctx.commands if "@@launch" in c]) == 1 and len(await _runs(ctx)) == 1
    assert center._busy == {}
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_lost_launch_answer_is_checked_on_the_server(monkeypatch):
    """Reisst die Verbindung genau beim Start ab, kann der Lauf trotzdem laufen: erst
    nachsehen. Nur wenn der Server ihn nicht kennt, gilt der Start als gescheitert."""
    from nodvard_deck_ext_nexus_soc import patching

    engine, ctx, center = await _center()
    ctx.launch_reply = ConnectionResetError("Verbindung weg")
    ctx.polls = [POLL_DONE]
    ok, summary, _out, _rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert ok and summary == "2 Paket(e) aktualisiert"

    monkeypatch.setattr(patching, "UNKNOWN_GRACE_S", 0)
    ctx.polls = [POLL_UNKNOWN]
    ok, summary, _out, _rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert (ok, summary) == (False, "Start fehlgeschlagen: Verbindung weg")
    await engine.dispose()


@pytest.mark.asyncio
async def test_an_unreachable_server_fails_fast_instead_of_waiting_the_full_time(monkeypatch):
    """Scheitert schon der Start und antwortet auch keine Nachfrage,
    ist der Server aus oder nicht erreichbar. Dann nicht 45 Minuten "laeuft" anzeigen und
    den Server danach stundenlang sperren, sondern nach kurzer Zeit sauber scheitern."""
    from nodvard_deck_ext_nexus_soc import patching

    monkeypatch.setattr(patching, "REACH_GRACE_S", 0)
    monkeypatch.setattr(patching, "UPGRADE_TIMEOUT", 0.3)  # steht fuer 45 Minuten
    engine, ctx, center = await _center()
    ctx.launch_reply = ConnectionResetError("Verbindung weg")
    ctx.polls = [OSError("Verbindung zu 10.0.0.2:22 fehlgeschlagen")]
    ok, summary, _out, rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert (ok, summary, rc) == (False, "Start fehlgeschlagen: Verbindung weg", None)
    # Kein "laeuft noch": der Server ist sofort wieder frei, es folgt keine spaete Nachfrage.
    assert center._busy == {} and not center._late
    [run] = await _runs(ctx)
    assert run.status == "error" and "Keine Rückmeldung" not in run.summary
    ok, summary, _out, _rc = await center.execute(ctx._host, "reboot", command=up.REBOOT_COMMAND, trigger="manual")
    assert ok and summary != patching.BUSY_MESSAGE

    # Kanal ohne Antwort (Rueckgabecode -1, leer) zaehlt genauso als "nicht erreichbar".
    ctx.polls = [SimpleNamespace(exit_code=-1, stdout="", stderr="")]
    ok, summary, _out, _rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert (ok, summary) == (False, "Start fehlgeschlagen: Verbindung weg") and center._busy == {}
    for task in list(center._rechecks):
        task.cancel()
    await engine.dispose()


@pytest.mark.asyncio
async def test_once_the_server_answered_a_failed_start_is_followed_for_the_full_time(monkeypatch):
    """Hat nach einem gescheiterten Start auch nur eine Nachfrage eine Antwort gebracht,
    laeuft der Lauf womoeglich: wie bisher lange warten und Verbindungsfehler aussitzen."""
    from nodvard_deck_ext_nexus_soc import patching

    monkeypatch.setattr(patching, "REACH_GRACE_S", 0)
    engine, ctx, center = await _center()
    ctx.launch_reply = ConnectionResetError("Verbindung weg")
    ctx.polls = [POLL_RUNNING, OSError("weg"), TimeoutError(), POLL_DONE]
    ok, summary, _out, _rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert ok and summary == "2 Paket(e) aktualisiert" and len(_polls(ctx)) == 4
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_failed_start_is_saved_at_once_so_a_restart_keeps_the_short_wait(monkeypatch):
    """Startet das Dashboard nach einem gescheiterten Start neu, ist die
    Schonfrist nur im Speicher weg -- der Lauf wuerde 45 Minuten "laufen" und den Server danach
    2 h 15 min sperren. Darum steht "Start fehlgeschlagen" schon in der Lauf-Zeile, bevor gewartet wird."""
    engine, ctx, center = await _center()
    ctx.launch_reply = ConnectionResetError("Verbindung weg")
    seen: list[tuple[str, str | None]] = []
    real_wait = center._wait_detached

    async def spy(*a, **kw):
        run = (await _runs(ctx))[-1]
        seen.append((run.status, run.summary))
        return await real_wait(*a, **kw)

    monkeypatch.setattr(center, "_wait_detached", spy)
    ctx.polls = [OSError("weg")]
    monkeypatch.setattr("nodvard_deck_ext_nexus_soc.patching.REACH_GRACE_S", 0)
    await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert seen == [("running", "Start fehlgeschlagen: Verbindung weg")]
    # Ein gelungener Start schreibt nichts vorab.
    seen.clear()
    ctx.launch_reply = LAUNCH_OK
    ctx.polls = [POLL_DONE]
    await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert seen == [("running", None)]
    for task in list(center._rechecks):
        task.cancel()
    await engine.dispose()


@pytest.mark.asyncio
async def test_after_a_restart_a_run_with_a_failed_start_ends_fast_when_the_server_stays_silent(monkeypatch):
    from nodvard_deck_ext_nexus_soc import patching

    monkeypatch.setattr(patching, "REACH_GRACE_S", 0)
    monkeypatch.setattr(patching, "RESUME_MIN_WAIT_S", 0.3)  # steht fuer 5 Minuten
    monkeypatch.setattr(patching, "UPGRADE_TIMEOUT", 0.3)  # steht fuer 45 Minuten
    engine, ctx, center = await _center()
    rid = "upd_0123456789abcdef"
    await _add_running(ctx, rid, remote_id=rid, minutes_ago=0)
    async with ctx._sm() as s:
        from nodvard_deck_ext_nexus_soc.models import UpdateRunRecord

        (await s.get(UpdateRunRecord, rid)).summary = "Start fehlgeschlagen: Verbindung weg"
        await s.commit()
    ctx.polls = [OSError("Verbindung zu 10.0.0.2:22 fehlgeschlagen")]

    assert await center.resume_interrupted() == 1
    [run] = await _runs(ctx)
    assert (run.status, run.summary) == ("error", "Start fehlgeschlagen: Verbindung weg")
    assert center._busy == {} and not center._late
    assert [n.title for n in ctx.notes] == ["Sicherheitsupdates auf Raspberry Pi: fehlgeschlagen"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_started_run_is_followed_even_if_the_server_stops_answering(monkeypatch):
    """Ist der Start gelungen, laeuft der Lauf sicher: die Schonfrist gilt nur nach einem
    gescheiterten Start, sonst bleibt es bei der vollen Wartezeit und der spaeten Nachfrage."""
    from nodvard_deck_ext_nexus_soc import patching

    monkeypatch.setattr(patching, "REACH_GRACE_S", 0)
    monkeypatch.setattr(patching, "UPGRADE_TIMEOUT", 0.2)
    engine, ctx, center = await _center()
    ctx.polls = [OSError("Verbindung weg")]
    ok, summary, _out, _rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert not ok and "noch weiterlaufen" in summary and "Verbindung weg" in summary
    assert center._busy == {"h1": "all"} and len(center._late) == 1
    await asyncio.gather(*center._late)
    await engine.dispose()


@pytest.mark.asyncio
async def test_no_second_run_while_one_is_busy_and_reboot_stays_in_the_channel():
    from nodvard_deck_ext_nexus_soc.patching import BUSY_MESSAGE

    engine, ctx, center = await _center()
    center._busy["h1"] = "all"
    assert await center.execute(ctx._host, "security", command="x", trigger="manual") == (False, BUSY_MESSAGE, "", None)
    assert not ctx.commands and not await _runs(ctx)
    center._busy.clear()

    ok, _summary, _out, _rc = await center.execute(ctx._host, "reboot", command=up.REBOOT_COMMAND, trigger="manual")
    assert ok and len(ctx.commands) == 1 and "shutdown -r" in ctx.commands[0] and "systemd-run" not in ctx.commands[0]
    assert ctx.timeouts == [60]
    [run] = await _runs(ctx)
    assert run.remote_id is None  # Neustart wird nach einem Dashboard-Neustart nicht wieder aufgenommen
    for task in list(center._rechecks):
        task.cancel()
    await engine.dispose()


async def _add_running(ctx, run_id, *, mode="security", remote_id=None, host_id="h1", minutes_ago=10):
    from datetime import UTC, datetime, timedelta

    from nodvard_deck_ext_nexus_soc.models import UpdateRunRecord

    async with ctx._sm() as s:
        s.add(UpdateRunRecord(id=run_id, host_id=host_id, host_name="Raspberry Pi", mode=mode, trigger="manual", status="running",
                              upgraded=0, started_at=datetime.now(UTC) - timedelta(minutes=minutes_ago), remote_id=remote_id))
        await s.commit()


@pytest.mark.asyncio
async def test_after_a_dashboard_restart_the_run_is_resumed_and_reported_once():
    """Docker-Update auf dem Raspberry Pi: dockerd startet neu, der Dashboard-Container mit.
    Nach dem Neustart fragt das Dashboard den Lauf weiter ab, schliesst ihn ab wie
    sonst (Audit, neue Pruefung) und meldet das Ergebnis einmal per Push."""
    engine, ctx, center = await _center()
    rid = "upd_0123456789abcdef"
    await _add_running(ctx, rid, remote_id=rid)
    ctx.after_upgrade = True  # auf dem Server ist das Update inzwischen durch
    ctx.polls = [OSError("Netz noch nicht da"), POLL_RUNNING, POLL_DONE]

    assert await center.resume_interrupted() == 1
    assert len(_polls(ctx)) == 3 and all(rid in c for c in _polls(ctx))
    assert not any("@@launch" in c for c in ctx.commands)  # nie ein zweiter Start
    assert all(b == {"h1": "security"} for b in ctx.busy_seen) and center._busy == {}
    [run] = await _runs(ctx)
    assert (run.status, run.summary, run.upgraded) == ("ok", "2 Paket(e) aktualisiert", 2)
    assert ctx.audits[-1]["action"] == "nexus_soc.updates_security" and ctx.audits[-1]["correlation_id"] == rid
    [note] = ctx.notes
    assert note.title == "Sicherheitsupdates auf Raspberry Pi: fertig"
    assert note.body == "2 Paket(e) aktualisiert\nDas Dashboard wurde währenddessen neu gestartet."
    # Mit Server-Bezug, sonst kann ein Wartungsfenster die Meldung nie stummschalten.
    assert note.payload == {"path": "/ext/nexus-soc/soc?tab=updates", "tags": ["package"], "host_id": "h1"}
    assert (await center.overview())["hosts"][0]["status"]["security_count"] == 0  # neu geprueft
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_server_counts_as_busy_while_a_resume_is_still_waiting(monkeypatch):
    """In der Wartezeit vor dem Wiederaufnehmen darf kein zweiter Lauf starten."""
    from nodvard_deck_ext_nexus_soc import patching

    monkeypatch.setattr(patching, "RESUME_DELAY_S", 0.3)
    engine, ctx, center = await _center()
    rid = "upd_0123456789abcdef"
    await _add_running(ctx, rid, remote_id=rid)
    ctx.polls = [POLL_DONE]
    task = asyncio.ensure_future(center.resume_interrupted())
    await asyncio.sleep(0.1)
    assert center._busy == {"h1": "security"} and not ctx.commands
    assert (await center.execute(ctx._host, "all", command="x", trigger="manual"))[1] == patching.BUSY_MESSAGE
    assert await task == 1
    assert center._busy == {} and [r.status for r in await _runs(ctx)] == ["ok"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_resume_without_a_run_on_the_server_or_without_the_server_marks_it_aborted():
    engine, ctx, center = await _center()
    await _add_running(ctx, "upd_1111111111111111", remote_id="upd_1111111111111111")
    await _add_running(ctx, "upd_2222222222222222", remote_id="upd_2222222222222222", host_id="weg")
    await _add_running(ctx, "upd_3333333333333333", mode="reboot")  # ohne remote_id: macht abort_interrupted_runs
    ctx.polls = [POLL_UNKNOWN]

    assert await center.resume_interrupted() == 2
    runs = {r.id: r for r in await _runs(ctx)}
    for rid in ("upd_1111111111111111", "upd_2222222222222222"):
        assert (runs[rid].status, runs[rid].summary) == ("error", "Abgebrochen – Dashboard wurde neu gestartet")
    assert runs["upd_3333333333333333"].status == "running"
    assert [n.title for n in ctx.notes] == ["Sicherheitsupdates auf Raspberry Pi: fehlgeschlagen"]
    assert center._busy == {}

    assert await center.abort_interrupted_runs() == 1
    runs = {r.id: r for r in await _runs(ctx)}
    assert runs["upd_3333333333333333"].status == "error"
    await engine.dispose()


# --- Zeilen vom Server stehen nur im Ergebnis der Aktion --------------------------

SERVER_LINE = "E: Unable to locate package GEHEIM-AUS-DER-AUSGABE"
POLL_FAILED = f"@@rc=100\n@@summary\n@@tail\n{SERVER_LINE}\n@@upgrade-done\n"
PUBLIC_FAILURE = "Fehlgeschlagen (Rückgabecode 100). Einzelheiten stehen in der Update-Zentrale."


def _nothing_from_the_server(ctx, secret="GEHEIM-AUS-DER-AUSGABE"):
    """Protokoll (Audit) und Meldungen (Push) tragen keine Zeile vom Server."""
    for row in ctx.audits:
        assert secret not in (row["reason"] or "") and secret not in str(row["detail"])
    for note in ctx.notes:
        assert secret not in note.title and secret not in note.body


@pytest.mark.asyncio
async def test_a_failed_update_keeps_the_server_line_in_the_result_but_not_in_the_log():
    engine, ctx, center = await _center()
    ctx.polls = [POLL_FAILED]
    ok, summary, out, rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert (ok, rc) == (False, 100)
    assert summary == SERVER_LINE and "GEHEIM-AUS-DER-AUSGABE" in out, "das Ergebnis der Aktion bleibt vollstaendig"
    [row] = ctx.audits
    assert row["outcome"] == "failure" and row["reason"] == f"Alle Updates auf Raspberry Pi: {PUBLIC_FAILURE}"
    _nothing_from_the_server(ctx)
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_start_error_line_is_not_written_to_the_log():
    engine, ctx, center = await _center()
    ctx.launch_reply = "@@launch\nmkdir: cannot create directory '/var/lib/nexus-updates': GEHEIM-AUS-DER-AUSGABE\n@@nopid\n"
    ok, summary, _out, _rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert not ok and "GEHEIM-AUS-DER-AUSGABE" in summary
    assert ctx.audits[-1]["reason"] == (
        "Alle Updates auf Raspberry Pi: Update-Lauf konnte nicht gestartet werden. Einzelheiten stehen in der Update-Zentrale."
    )
    _nothing_from_the_server(ctx)
    await engine.dispose()


class _FailingReboot(_Ctx):
    async def _run(self, host, command, timeout_s=60):
        if "shutdown -r" in command:
            self.commands.append(command)
            return SimpleNamespace(exit_code=1, stdout="Failed to connect to bus: GEHEIM-AUS-DER-AUSGABE", stderr="", duration_ms=5)
        return await super()._run(host, command, timeout_s)


@pytest.mark.asyncio
async def test_a_failed_reboot_keeps_the_server_line_in_the_result_but_not_in_the_log():
    engine, ctx, center = await _center(_FailingReboot)
    ok, summary, _out, rc = await center.execute(ctx._host, "reboot", command=up.REBOOT_COMMAND, trigger="manual")
    assert (ok, rc) == (False, 1) and "GEHEIM-AUS-DER-AUSGABE" in summary
    assert ctx.audits[-1]["reason"] == "Neustart auf Raspberry Pi: Neustart fehlgeschlagen. Einzelheiten stehen in der Update-Zentrale."
    _nothing_from_the_server(ctx)
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_failure_reported_after_a_dashboard_restart_names_no_server_line():
    engine, ctx, center = await _center()
    rid = "upd_0123456789abcdef"
    await _add_running(ctx, rid, remote_id=rid)
    ctx.polls = [POLL_FAILED]
    assert await center.resume_interrupted() == 1
    [note] = ctx.notes
    assert note.title == "Sicherheitsupdates auf Raspberry Pi: fehlgeschlagen"
    assert note.body == f"{PUBLIC_FAILURE}\nDas Dashboard wurde währenddessen neu gestartet."
    _nothing_from_the_server(ctx)
    [run] = await _runs(ctx)
    assert run.summary == SERVER_LINE, "die Update-Zentrale selbst zeigt den Lauf weiter vollstaendig"
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_failure_found_after_the_wait_limit_names_no_server_line(monkeypatch):
    from nodvard_deck_ext_nexus_soc import patching

    monkeypatch.setattr(patching, "UPGRADE_TIMEOUT", 0)
    monkeypatch.setattr(patching, "LATE_FOLLOW_S", 3600)
    engine, ctx, center = await _center()
    ctx.center = center
    ctx.polls = [POLL_RUNNING, POLL_RUNNING, POLL_FAILED]
    ok, summary, _out, _rc = await center.execute(ctx._host, "all", command=up.upgrade_command("apt", "all"), trigger="manual")
    assert not ok and "noch weiterlaufen" in summary
    await asyncio.gather(*center._late)
    [note] = ctx.notes
    assert note.title == "Alle Updates auf Raspberry Pi: fehlgeschlagen"
    assert note.body == f"{PUBLIC_FAILURE}\nDer Lauf hat länger gedauert, als das Dashboard gewartet hat."
    _nothing_from_the_server(ctx)
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_automatic_run_reports_a_failure_without_the_server_line():
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.patching import UpdateCenter

    engine, sm = await _engine()
    ctx = _Ctx(sm, {"auto_updates_mode": "security", "auto_updates_host_tag": "auto-update"})
    center = UpdateCenter(ctx, Defender(ctx))
    ctx.polls = [POLL_RUNNING, POLL_FAILED]
    assert await center.auto_update() == {"hosts": 1, "failed": 1}
    [note] = ctx.notes
    assert note.title == "Automatische Updates: 1 Server, 1 Fehler"
    assert note.body == f"- Raspberry Pi: FEHLGESCHLAGEN – {PUBLIC_FAILURE}"
    _nothing_from_the_server(ctx)
    await engine.dispose()
