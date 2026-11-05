"""Shield erkennt rpm-ostree und überlässt die Installation dem Nutzer."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shield_helper_kit import SCRIPT, stubs

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "shield"
SH = shutil.which("sh")
needs_sh = pytest.mark.skipif(sys.platform == "win32" or SH is None, reason="braucht eine POSIX-Shell")
MESSAGE = (
    "unveränderliches System (rpm-ostree): Installation von Hand, dann Neustart. "
    "Auf dem Server ausführen: rpm-ostree install clamav clamav-update lynis fail2ban. Danach neu starten."
)


@pytest.fixture
def av(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    from nodvard_deck_ext_shield import antivirus

    yield antivirus
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


@pytest.mark.parametrize("marker, expected", [("yes", True), ("no", False), ("", False)])
async def test_host_status_exposes_rpm_ostree(av, marker, expected):
    from nodvard_deck_ext_shield.defender import Defender

    output = f"@@clam\nnone\n@@lynis\nnone\n@@os\nfedora\n@@rpm_ostree\n{marker}\n@@end\n"
    run = AsyncMock(return_value=SimpleNamespace(stdout=output))
    status = await Defender(SimpleNamespace(exec=SimpleNamespace(run=run))).host_status(SimpleNamespace(id="h1"))
    assert status["rpm_ostree"] is expected
    assert status["clamav_installed"] is False
    assert status["lynis_installed"] is False
    assert "sudo" not in run.call_args.args[1]


def test_helper_message_contains_manual_install_and_reboot(av):
    assert av.helper_text("@@msg=rpm_ostree\n") == MESSAGE + "\n"


@needs_sh
@pytest.mark.parametrize("detection", ["command", "file", "neither"])
def test_status_detects_rpm_ostree_without_root(av, tmp_path, detection):
    tools = {"rpm-ostree": "exit 99\n"} if detection == "command" else {}
    bin_dir = stubs(tmp_path, tools)
    booted = tmp_path / "ostree-booted"
    if detection == "file":
        booted.touch()
    command = av.STATUS_COMMAND.replace(
        'PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin";', ""
    ).replace("/run/ostree-booted", str(booted))
    done = subprocess.run([SH, "-c", command], env={"PATH": str(bin_dir)}, capture_output=True, text=True, check=False)
    assert done.returncode == 0
    assert av.parse_status(done.stdout).rpm_ostree is (detection != "neither")


@needs_sh
@pytest.mark.parametrize("package", ["clamav", "lynis", "fail2ban"])
@pytest.mark.parametrize("detection", ["command", "file", "neither"])
@pytest.mark.parametrize("route", ["direct", "helper"])
def test_install_stops_on_ostree_and_keeps_fedora_dnf(av, tmp_path, package, detection, route):
    calls = tmp_path / "calls"
    tools = {name: f'echo "{name} $*" >> "{calls}"\n' for name in ("dnf", "systemctl", "fail2ban-client")}
    if detection == "command":
        tools["rpm-ostree"] = f'echo unexpected >> "{calls}"; exit 99\n'
    bin_dir = stubs(tmp_path, tools)
    booted = tmp_path / "ostree-booted"
    if detection == "file":
        booted.touch()
    jail = tmp_path / "jail.local"
    if route == "direct":
        command = av.INSTALL_COMMANDS[package]
    else:
        helper = tmp_path / "shield-root"
        text = SCRIPT.read_text(encoding="ascii").replace("PATH=/usr/sbin:/usr/bin:/sbin:/bin", f"PATH={bin_dir}")
        helper.write_text(text.replace("/run/ostree-booted", str(booted)).replace("/etc/fail2ban/jail.local", str(jail)))
        command = f'"{SH}" "{helper}" install {package}'
    command = command.replace("/run/ostree-booted", str(booted)).replace("/etc/fail2ban/jail.local", str(jail))
    done = subprocess.run([SH, "-c", command], env={"PATH": str(bin_dir)}, capture_output=True, text=True, check=False)
    if detection != "neither":
        assert done.returncode == 2
        assert av.helper_text(done.stdout) == MESSAGE + "\n"
        assert not calls.exists()
        assert not jail.exists()
    else:
        assert done.returncode == 0, done.stderr
        packages = "clamav clamav-update" if package == "clamav" else package
        assert calls.read_text().splitlines()[0] == f"dnf install -y {packages}"
        assert (package == "fail2ban") is jail.exists()


@pytest.mark.parametrize("package", ["clamav", "lynis", "fail2ban"])
async def test_install_action_returns_manual_instruction(av, package):
    from nodvard_sdk import ActionRequest, Actor, Risk
    from nodvard_deck_ext_shield.defender_api import ACTION_INSTALL, DefenderExecutor

    host = SimpleNamespace(id="h1")
    run = AsyncMock(return_value=SimpleNamespace(exit_code=2, stdout="@@msg=rpm_ostree\n", stderr="", duration_ms=1))
    ctx = SimpleNamespace(hosts=SimpleNamespace(get=AsyncMock(return_value=host)), exec=SimpleNamespace(run=run))
    defender = SimpleNamespace(forget_status=lambda host_id: None)
    executor = DefenderExecutor(ctx, defender)
    command = av.INSTALL_COMMANDS[package]
    request = ActionRequest(action_type=ACTION_INSTALL, host_ref="h1", payload={"package": package, "command": command},
                            risk=Risk.LOW, proposed_by=Actor.user("u1", "nico"), reason="Test")
    result = await executor.execute(request)
    assert result.success is False
    assert result.error == MESSAGE
    assert run.call_args.args[1].startswith(av.RPM_OSTREE_GUARD)
