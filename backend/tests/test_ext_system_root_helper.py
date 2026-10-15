"""Server-Zustand: Dienst neu starten über das Hilfsskript `system-root` (`service-restart`, ab Version 2).

Ohne root-Anmeldung nimmt der Befehl zuerst das Hilfsskript, wenn es Byte für Byte das mitgelieferte ist (sha256) und
sudo es erlaubt, sonst den alten Weg (`sudo -n sh -c`), sonst NO_ROOT (bei einem unpassenden Skript mit Hinweis darauf).
Ein verändertes oder veraltetes Skript läuft nie, auch nicht für `version`. Das Skript prüft Name und Sperrliste selbst, genau wie
`actions.py`. Die Tests lassen den gebauten Befehl und das echte Skript in einer echten Shell laufen, mit Attrappen für
`id`, `sudo` und `systemctl`."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "system"
SCRIPT = EXT_DIR / "root-helper" / "system-root.sh"
FIXED_PATH = "PATH=/usr/sbin:/usr/bin:/sbin:/bin"

SH = shutil.which("sh") or "sh"
needs_sh = pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None, reason="braucht eine echte POSIX-Shell")


@pytest.fixture(autouse=True)
def _ext_system_on_path(monkeypatch, tmp_path):
    """Die Erweiterung ist kein installiertes Paket: Pfad nur für diese Tests setzen und die Module danach vergessen.
    Das Hilfsskript liegt im Test nicht unter /usr/local/lib, sondern in `tmp_path`."""
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    from nodvard_deck_ext_system import actions

    monkeypatch.setattr(actions, "HELPER", str(tmp_path / "system-root"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_system"):
            del sys.modules[name]


def _actions():
    from nodvard_deck_ext_system import actions

    return actions


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _world(
    tmp_path: Path, *, helper_version: int | None, full_rule: bool, uid: str = "1000", restart_rc: int = 0,
    installed: bool = True, extra: str = "",
):
    """Attrappen im PATH. Das Hilfsskript ist eine Kopie des echten, dessen fester PATH auf die Attrappen zeigt; es liegt
    unter `sa.HELPER` (für den Test in `tmp_path` umgebogen), wenn `installed`. Als mitgeliefert gilt diese Kopie mit
    Version 2 (`sa.HELPER_SHA256`). `helper_version`: so meldet es sich, jede andere Zahl ist ein anderer Inhalt (`None`:
    Version 2, aber sudo erlaubt es nicht). `extra`: Zeile hinter dem Kopf, auch ein anderer Inhalt. `full_rule`:
    `sudo -n true` und `sudo -n sh -c` klappen."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    log.write_text("")
    _stub(bin_dir / "id", f"echo {uid}\n")
    _stub(bin_dir / "systemctl", f'echo "systemctl $*" >> "{log}"\n[ "$1" = restart ] && exit {restart_rc}\n[ "$1" = is-active ] && echo active\nexit 0\n')
    helper = Path(_actions().HELPER)
    text = SCRIPT.read_text(encoding="ascii").replace(FIXED_PATH, f"PATH={bin_dir}:/usr/bin:/bin")
    _actions().HELPER_SHA256 = hashlib.sha256(text.encode("ascii")).hexdigest()
    if helper_version is not None:
        text = text.replace("HELPER_VERSION=2", f"HELPER_VERSION={helper_version}")
    if extra:
        text = text.replace("\n", f"\n{extra}\n", 1)
    helper.write_text(text)
    helper.chmod(0o755 if installed else 0o644)
    helper_ok = "1" if helper_version is not None else ""
    _stub(bin_dir / "sudo", f'''echo "sudo $*" >> "{log}"
[ "$1" = -n ] || exit 1
shift
case "$1" in
  {_actions().HELPER}) [ -n "{helper_ok}" ] || {{ echo "sudo: a password is required" >&2; exit 1; }}; shift; exec sh "{helper}" "$@";;
  true) [ -n "{'1' if full_rule else ''}" ];;
  sh) [ -n "{'1' if full_rule else ''}" ] || exit 1; exec "$@";;
  *) exit 1;;
esac
''')
    return bin_dir, log


def _run(command: str, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", "-c", command], capture_output=True, text=True, timeout=30, check=False,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "LC_ALL": "C"},
    )


def test_the_command_tries_the_helper_first_then_the_old_way(monkeypatch):
    sa = _actions()
    from nodvard_sdk import root_helper_path

    monkeypatch.setattr(sa, "HELPER", root_helper_path("system"))
    assert sa.HELPER == "/usr/local/lib/nodvard-deck/system-root"
    old = sa.restart_command("nginx.service")
    matches = (
        "[ -x /usr/local/lib/nodvard-deck/system-root ] && "
        f"[ \"$(sha256sum < /usr/local/lib/nodvard-deck/system-root 2>/dev/null)\" = \"{sa.HELPER_SHA256}  -\" ]"
    )
    assert sa.restart_as_root("nginx.service") == (
        f"if [ \"$(id -u)\" -eq 0 ]; then sh -c '{old}'; "
        f"elif {matches} && "
        "[ \"$(sudo -n /usr/local/lib/nodvard-deck/system-root version 2>/dev/null)\" -ge 2 ] 2>/dev/null; then "
        "sudo -n /usr/local/lib/nodvard-deck/system-root service-restart nginx.service; "
        f"elif sudo -n true 2>/dev/null; then sudo -n sh -c '{old}'; "
        f"else echo '@@noroot'; if [ -e /usr/local/lib/nodvard-deck/system-root ] && ! {{ {matches}; }}; then "
        "echo '@@noroot-helper'; fi; exit 126; fi"
    )
    with pytest.raises(sa.PayloadError):
        sa.restart_as_root("nginx.service; reboot")


def test_the_needed_version_is_one_the_module_brings():
    manifest = tomllib.loads((EXT_DIR / "extension.toml").read_text(encoding="utf-8"))["extension"]
    assert _actions().HELPER_RESTART_VERSION <= manifest["root_helper"]["version"]


def test_the_module_compares_against_the_file_the_setup_brings():
    assert _actions().HELPER_SHA256 == hashlib.sha256(SCRIPT.read_bytes()).hexdigest()


@needs_sh
@pytest.mark.parametrize("full_rule", [True, False], ids=["neben-voller-regel", "nur-enge-regel"])
def test_with_a_fitting_helper_only_the_helper_runs_as_root(tmp_path, full_rule):
    sa = _actions()
    bin_dir, log = _world(tmp_path, helper_version=2, full_rule=full_rule)
    done = _run(sa.restart_as_root("nginx.service"), bin_dir)
    assert sa.parse_restart_output(done.stdout) == (0, "active"), done.stderr
    assert log.read_text().splitlines() == [
        f"sudo -n {sa.HELPER} version",
        f"sudo -n {sa.HELPER} service-restart nginx.service",
        "systemctl restart -- nginx.service",
        "systemctl is-active -- nginx.service",
    ]


@needs_sh
def test_a_failed_restart_is_reported_like_before(tmp_path):
    sa = _actions()
    bin_dir, _ = _world(tmp_path, helper_version=2, full_rule=False, restart_rc=5)
    done = _run(sa.restart_as_root("nginx.service"), bin_dir)
    assert sa.parse_restart_output(done.stdout)[0] == 5


@needs_sh
def test_a_fitting_helper_that_sudo_does_not_allow_leads_to_the_old_way(tmp_path):
    sa = _actions()
    bin_dir, log = _world(tmp_path, helper_version=None, full_rule=True)
    done = _run(sa.restart_as_root("nginx.service"), bin_dir)
    assert sa.parse_restart_output(done.stdout) == (0, "active")
    calls = log.read_text().splitlines()
    assert calls[0] == f"sudo -n {sa.HELPER} version" and calls[1] == "sudo -n true" and calls[2].startswith("sudo -n sh -c ")
    assert not any("service-restart" in c for c in calls)


@needs_sh
@pytest.mark.parametrize(("helper_version", "extra"), [(1, ""), (3, ""), (2, 'touch "$0.lief"')],
                         ids=["altes-skript", "neueres-skript", "veraendert"])
def test_a_helper_that_is_not_the_shipped_one_never_runs(tmp_path, helper_version, extra):
    """Nicht einmal `version`: Ein verändertes Skript könnte mit jeder Zeile alles tun, auch beim Fragen nach der Version."""
    sa = _actions()
    bin_dir, log = _world(tmp_path, helper_version=helper_version, full_rule=True, extra=extra)
    done = _run(sa.restart_as_root("nginx.service"), bin_dir)
    assert sa.parse_restart_output(done.stdout) == (0, "active")
    calls = log.read_text().splitlines()
    assert calls[0] == "sudo -n true" and calls[1].startswith("sudo -n sh -c ")
    assert not any(sa.HELPER in c for c in calls)
    assert not Path(f"{sa.HELPER}.lief").exists()


@needs_sh
def test_without_the_file_sudo_is_not_asked_for_it(tmp_path):
    """Kein zusätzlicher sudo-Aufruf, wenn das Skript gar nicht da ist: Jeder landet im Protokoll."""
    sa = _actions()
    bin_dir, log = _world(tmp_path, helper_version=2, full_rule=True, installed=False)
    done = _run(sa.restart_as_root("nginx.service"), bin_dir)
    assert sa.parse_restart_output(done.stdout) == (0, "active")
    calls = log.read_text().splitlines()
    assert calls[0] == "sudo -n true" and not any(sa.HELPER in c for c in calls)


@needs_sh
@pytest.mark.parametrize(("helper_version", "installed", "lines"), [
    (1, True, ["@@noroot", "@@noroot-helper"]),
    (None, True, ["@@noroot"]),
    (2, False, ["@@noroot", "@@noroot-helper"]),
], ids=["altes-skript", "passendes-ohne-sudo", "nicht-ausfuehrbar"])
def test_without_any_way_nothing_runs(tmp_path, helper_version, installed, lines):
    """Nur ein Skript, das nicht wie mitgeliefert da liegt (anderer Inhalt, nicht ausführbar), bringt den Hinweis „neu
    einrichten“; ein passendes, das sudo nicht erlaubt, nicht."""
    sa = _actions()
    bin_dir, log = _world(tmp_path, helper_version=helper_version, full_rule=False, installed=installed)
    done = _run(sa.restart_as_root("nginx.service"), bin_dir)
    assert done.stdout.split() == lines and done.returncode == 126
    assert not any(c.startswith("systemctl") for c in log.read_text().splitlines())


@needs_sh
def test_as_root_the_helper_is_not_needed(tmp_path):
    sa = _actions()
    bin_dir, log = _world(tmp_path, helper_version=2, full_rule=False, uid="0")
    done = _run(sa.restart_as_root("nginx.service"), bin_dir)
    assert sa.parse_restart_output(done.stdout) == (0, "active")
    assert not any(c.startswith("sudo") for c in log.read_text().splitlines())


NAMES = [
    "nginx.service", "getty@tty1.service", "Getty@TTY1.service", "foo.socket", "foo.timer", "a:b.service", "..service",
    "nodvard-link.service", "docker.service", "containerd.service",
    "ssh.service", "sshd.service", "SSH.service", "sshd-keygen.service", "ssh-agent.service", "dbus.service",
    "dbus-broker.service", "dbus-org.freedesktop.service", "systemd-journald.service", "systemd-logind.socket",
    "networking.service", "NetworkManager.service", "dhcpcd.service", "wpa_supplicant.service", "nodvard.service",
    "nodvard-deck.service", "nodvard-deck-updater.service", "lattice.service", "lattice-backend.service",
    "-x.service", "a b.service", ".service", "x;id.service", "x$(id).service", "foo", "foo.mount", "x" * 128 + ".service",
    "x" * 129 + ".service", "nginx.service\n", "",
]


@needs_sh
@pytest.mark.parametrize("unit", NAMES)
def test_the_helper_refuses_exactly_what_the_dashboard_refuses(tmp_path, unit):
    """Gleiche Regeln an zwei Stellen: ungültiger Name -> Exit 64, gesperrt -> Exit 77, sonst Neustart. Docker sperrt nur
    das Dashboard (auf seinem eigenen Rechner), das Skript kann das nicht wissen."""
    sa = _actions()
    try:
        sa.validate_unit(unit)
    except sa.PayloadError:
        expected = 64
    else:
        expected = 77 if sa.deny_reason(unit, dashboard_host=False) else 0
    _, log = _world(tmp_path, helper_version=2, full_rule=False)
    done = subprocess.run(
        [SH, _actions().HELPER, "service-restart", unit], capture_output=True, text=True, timeout=30,
        check=False, env={"PATH": "/nicht-da"},
    )
    assert done.returncode == expected, (unit, done.stdout, done.stderr)
    restarted = "systemctl restart -- " + unit in log.read_text()
    assert restarted == (expected == 0)
    if expected:
        assert done.stdout == "" and done.stderr.startswith("system-root: ")


@needs_sh
@pytest.mark.parametrize("args", [[], ["a.service", "b.service"]])
def test_service_restart_takes_exactly_one_name(tmp_path, args):
    _, log = _world(tmp_path, helper_version=2, full_rule=False)
    done = subprocess.run([SH, _actions().HELPER, "service-restart", *args], capture_output=True, text=True, timeout=30, check=False)
    assert done.returncode == 64 and log.read_text() == ""
