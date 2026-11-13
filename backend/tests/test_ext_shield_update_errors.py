"""Fehlgeschlagene Paketprüfungen bleiben von leeren und nicht unterstützten Ergebnissen getrennt."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

EXT_SRC = Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"


@pytest.fixture
def updates(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_SRC))
    from nodvard_deck_ext_shield import updates

    yield updates
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


ERRORS = [
    ("E: dpkg was interrupted, you must manually run 'sudo dpkg --configure -a'", "dpkg", "sudo dpkg --configure -a"),
    ("E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 123 (apt-get)", "locked", "warten"),
    ("E: Could not open lock file /var/lib/apt/lists/lock - open (13: Permission denied)", "permission", "Root-Rechte"),
    ("command timed out", "timeout", "erneut"),
]


@pytest.mark.parametrize(("line", "kind", "step"), ERRORS)
@pytest.mark.parametrize("section", [None, "list", "refresh", "phasing"])
def test_package_failures_have_a_reason_and_next_step(updates, line, kind, step, section):
    output = line if section is None else f"@@pm\napt\n@@list\n@@{section}\n{line}\n@@end\n"
    status = updates.parse_status(output)
    assert status.error and status.error_kind == kind
    assert step in status.error
    assert status.unsupported is False
    assert status.as_dict()["error_kind"] == kind


@pytest.mark.parametrize("output", ["", "unbekannte Ausgabe", "@@pm\n", "@@pm\napt\n@@list\n", "@@pm\nother\n@@end\n",
                                         "@@pm\nnone\n", "@@pm\napt\n@@end\n"])
def test_incomplete_detection_is_an_error(updates, output):
    status = updates.parse_status(output)
    assert status.error_kind == "detection" and status.error
    assert "erneut" in status.error
    assert status.unsupported is False


def test_only_completed_explicit_none_is_unsupported(updates):
    status = updates.parse_status("@@pm\nnone\n@@end\n")
    assert status.unsupported and status.manager is None and status.error_kind is None
    assert updates.parse_status("@@pm\nnone\n@@end\n", exit_code=1).unsupported is False


@pytest.mark.parametrize(("manager", "code", "failed"), [("apt", 0, False), ("apt", 100, True), ("dnf", 0, False),
                                                              ("dnf", 100, False), ("dnf", 1, True), ("apk", 1, True)])
def test_list_exit_codes_are_checked_per_manager(updates, manager, code, failed):
    status = updates.parse_status(f"@@pm\n{manager}\n@@list\nrc list {code}\n@@end\n")
    assert bool(status.error) is failed
    assert not status.unsupported


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine POSIX-Shell")
@pytest.mark.parametrize(("line", "kind", "step"), ERRORS)
def test_real_status_command_preserves_package_errors(updates, tmp_path, line, kind, step):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in {
        "apt-get": "exit 0",
        "apt": f"printf '%s\\n' {shlex.quote(line)} >&2; exit 100",
    }.items():
        script = bindir / name
        script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        script.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:/usr/bin:/bin"}
    result = subprocess.run(["sh", "-c", updates.status_command(refresh=False)], capture_output=True, text=True,
                            env=env, timeout=15, check=True)
    status = updates.parse_status(result.stdout, exit_code=result.returncode)
    assert status.error_kind == kind and step in status.error
    assert "rc list 100" in result.stdout


@pytest.mark.parametrize(("failure", "kind", "step"), [(TimeoutError(), "timeout", "erneut"),
                                                            (PermissionError("denied"), "permission", "Root-Rechte")])
@pytest.mark.parametrize("old", [None, {"manager": "apt", "count": 2, "packages": [], "checked_at": 123.0}])
async def test_check_host_keeps_last_success_but_records_failure(updates, monkeypatch, failure, kind, step, old):
    from nodvard_deck_ext_shield import patching

    load = AsyncMock(return_value=old)
    save = AsyncMock()
    monkeypatch.setattr(patching, "load_baseline", load)
    monkeypatch.setattr(patching, "save_baseline", save)
    ctx = SimpleNamespace(exec=SimpleNamespace(read_access=lambda host: nullcontext(), run=AsyncMock(side_effect=failure)))
    center = patching.UpdateCenter(ctx, SimpleNamespace())
    host = SimpleNamespace(id="h1")
    state = await center.check_host(host)
    ctx.exec.run.assert_awaited_once_with(host, updates.status_command(refresh=True), timeout_s=patching.CHECK_TIMEOUT)
    assert state["error_kind"] == kind and step in state["error"]
    assert state["attempted_at"] and state["checked_at"] == (old["checked_at"] if old else None)
    assert not state.get("unsupported")
    if old:
        assert state["count"] == 2
    save.assert_awaited_once_with(ctx, "h1", "updates", state)
    assert center._checking == set()


@pytest.mark.parametrize("exit_code", [124, 137])
def test_timeout_exit_code_is_not_an_empty_result(updates, exit_code):
    status = updates.parse_status("@@pm\napt\n@@list\n@@end\n", exit_code=exit_code)
    assert status.error_kind == "timeout" and not status.unsupported


@pytest.mark.parametrize(("line", "kind", "step"), ERRORS)
def test_known_failure_takes_priority_over_exit_code(updates, line, kind, step):
    status = updates.parse_status(f"@@pm\napt\n@@list\n{line}\n@@end\n", exit_code=100)
    assert status.error_kind == kind and step in status.error
    unsupported = updates.parse_status(f"@@pm\nnone\n@@list\n{line}\n@@end\n")
    assert unsupported.error_kind == kind and not unsupported.unsupported


@pytest.mark.parametrize("name", ["timeout", "python3-timeout", "superuser", "liberror-dpkg"])
def test_package_names_are_not_diagnostics(updates, name):
    status = updates.parse_status(f"@@pm\napt\n@@list\n{name}/stable 1.2 amd64 [upgradable from: 1.1]\n"
                                  f"@@phasing\nrc inst 0\ninst {name}\nrc all 0\nall {name}\n@@end\n")
    assert status.error is None and status.error_kind is None
    assert len(status.packages) == 1


@pytest.mark.parametrize("mode", ["all", "security"])
async def test_plan_rechecks_failed_state_and_refuses_stale_packages(updates, monkeypatch, mode):
    from nodvard_deck_ext_shield import patching

    state = {"manager": "apt", "count": 2, "security_count": 2, "error": updates.STATUS_ERRORS["dpkg"], "packages": []}
    monkeypatch.setattr(patching, "load_baseline", AsyncMock(return_value=state))
    center = patching.UpdateCenter(SimpleNamespace(), SimpleNamespace())
    center.check_host = AsyncMock(return_value=state)
    host = SimpleNamespace(id="h1")
    with pytest.raises(ValueError, match="dpkg"):
        await center.plan(host, mode)
    center.check_host.assert_awaited_once_with(host, refresh=True)


@pytest.mark.parametrize(("line", "kind", "step"), ERRORS)
@pytest.mark.parametrize("old", [None, {"manager": "apt", "count": 2, "packages": [], "checked_at": 123.0}])
async def test_failed_command_never_becomes_a_successful_empty_check(updates, monkeypatch, line, kind, step, old):
    from nodvard_deck_ext_shield import patching

    monkeypatch.setattr(patching, "load_baseline", AsyncMock(return_value=old))
    save = AsyncMock()
    monkeypatch.setattr(patching, "save_baseline", save)
    result = SimpleNamespace(stdout=f"@@pm\napt\n@@list\n{line}\nrc list 100\n@@end\n", stderr="", exit_code=0)
    ctx = SimpleNamespace(exec=SimpleNamespace(read_access=lambda host: nullcontext(), run=AsyncMock(return_value=result)))
    host = SimpleNamespace(id="h1")
    state = await patching.UpdateCenter(ctx, SimpleNamespace()).check_host(host)
    ctx.exec.run.assert_awaited_once_with(host, updates.status_command(refresh=True), timeout_s=patching.CHECK_TIMEOUT)
    assert state["error_kind"] == kind and step in state["error"]
    assert state["checked_at"] == (old["checked_at"] if old else None)
    assert state["attempted_at"] and not state.get("unsupported")
    save.assert_awaited_once_with(ctx, "h1", "updates", state)


def test_successful_empty_list_remains_supported(updates):
    status = updates.parse_status("@@pm\napt\n@@list\nListing... Done\nrc list 0\n@@end\n")
    assert status.manager == "apt" and status.packages == []
    assert status.error is None and status.error_kind is None and not status.unsupported


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine POSIX-Shell")
@pytest.mark.parametrize(("line", "kind"), [("dpkg: error: failed to read package database", "dpkg"),
                                              ("open: Permission denied", "permission"),
                                              ("command timed out", "timeout"),
                                              ("E: Could not get lock /var/lib/dpkg/lock-frontend", "locked")])
@pytest.mark.parametrize("refresh", [False, True])
def test_real_command_keeps_refresh_and_trial_diagnostics(updates, tmp_path, line, kind, refresh):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in {
        "apt": "printf 'Listing... Done\\n'; exit 0",
        "apt-get": f"printf '%s\\n' {shlex.quote(line)} >&2; exit 2",
    }.items():
        script = bindir / name
        script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        script.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:/usr/bin:/bin"}
    command = updates.status_command(refresh=refresh)
    # Der Lesebefehl läuft unverändert; nur die privilegierte Hülle wird im Test durch den gleichen Befehl ersetzt.
    if refresh:
        from nodvard_deck_ext_shield.antivirus import HELPER_READ_VERSION, via_helper

        wrapped = via_helper(["refresh-packages"], updates.REFRESH_COMMAND, version=HELPER_READ_VERSION, required=False)
        command = command.replace(wrapped, f"sh -c {shlex.quote(updates.REFRESH_COMMAND)}")
    result = subprocess.run(["sh", "-c", command], capture_output=True, text=True, env=env, timeout=15, check=True)
    status = updates.parse_status(result.stdout, exit_code=result.returncode)
    assert line in result.stdout and status.error_kind == kind
    if refresh:
        assert "rc refresh 2" in result.stdout


@pytest.mark.parametrize("section", ["list", "refresh", "phasing"])
def test_inner_timeout_codes_are_not_empty_successes(updates, section):
    marker = "inst" if section == "phasing" else section
    status = updates.parse_status(f"@@pm\napt\n@@list\n@@{section}\nrc {marker} 124\n@@end\n")
    assert status.error_kind == "timeout"


def test_failed_refresh_without_diagnostic_is_still_a_failure(updates):
    status = updates.parse_status("@@pm\napt\n@@refresh\nrc refresh 1\n@@list\nrc list 0\n@@end\n")
    assert status.error_kind == "detection" and not status.unsupported


@pytest.mark.parametrize(("manager", "line", "package", "code"), [
    ("apt", "E: Failed to fetch https://mirror.example/debian/InRelease 503 Service Unavailable",
     "openssl/stable-security 3.0.14 amd64 [upgradable from: 3.0.13]", 100),
    ("dnf", "Error: Failed to download metadata for repo 'updates'", "openssl.x86_64 3.0.14 updates", 1),
    ("apk", "ERROR: https://mirror.example/alpine: temporary error", "openssl-3.0.13-r0 < 3.0.14-r0", 1),
])
def test_refresh_source_failure_keeps_a_readable_package_list(updates, manager, line, package, code):
    status = updates.parse_status(f"@@pm\n{manager}\n@@refresh\n{line}\nrc refresh {code}\n"
                                  f"@@list\n{package}\nrc list 0\n@@end\n")
    assert status.error is None and status.error_kind is None and not status.unsupported
    assert status.refresh_error == f"Nicht alle Paketquellen erreichbar: {line}"
    assert [(p.name, p.new_version) for p in status.packages] == [("openssl", "3.0.14" if manager != "apk" else "3.0.14-r0")]


@pytest.mark.parametrize("old", [None, {"manager": "apt", "count": 2, "packages": [], "checked_at": 123.0}])
@pytest.mark.parametrize("reason", ["503 Service Unavailable", "Connection timed out", "Resource temporarily unavailable"])
@pytest.mark.parametrize("section", ["refresh", "list"])
async def test_check_host_saves_new_packages_and_refresh_warning(updates, monkeypatch, old, reason, section):
    from nodvard_deck_ext_shield import patching

    monkeypatch.setattr(patching, "load_baseline", AsyncMock(return_value=old))
    save = AsyncMock()
    monkeypatch.setattr(patching, "save_baseline", save)
    warning = f"E: Failed to fetch https://mirror.example/debian/InRelease {reason}\n"
    refresh = warning if section == "refresh" else ""
    listing = warning if section == "list" else ""
    result = SimpleNamespace(stdout=f"@@pm\napt\n@@refresh\n{refresh}"
                             f"rc refresh {100 if refresh else 0}\n@@list\n{listing}"
                             "openssl/stable-security 3.0.14 amd64 [upgradable from: 3.0.13]\nrc list 0\n@@end\n",
                             stderr="", exit_code=0)
    ctx = SimpleNamespace(exec=SimpleNamespace(read_access=lambda host: nullcontext(), run=AsyncMock(return_value=result)))
    host = SimpleNamespace(id="h1")
    state = await patching.UpdateCenter(ctx, SimpleNamespace()).check_host(host)
    ctx.exec.run.assert_awaited_once_with(host, updates.status_command(refresh=True), timeout_s=patching.CHECK_TIMEOUT)
    assert state["error"] is None and state["error_kind"] is None
    assert state["count"] == state["security_count"] == 1
    assert state["packages"][0]["name"] == "openssl" and state["packages"][0]["new_version"] == "3.0.14"
    assert "mirror.example" in state["refresh_error"]
    assert state["checked_at"] == state["attempted_at"] and state["checked_at"] != 123.0
    save.assert_awaited_once_with(ctx, "h1", "updates", state)


def test_enterprise_refresh_failure_keeps_the_usable_partial_result(updates):
    status = updates.parse_status("@@pm\napt\n@@refresh\n"
                                  "E: Failed to fetch https://enterprise.proxmox.com/debian/pve/dists/bookworm/InRelease 401 Unauthorized\n"
                                  "rc refresh 100\n@@list\nrc list 0\n@@end\n")
    assert status.error is None and "Abo" in status.refresh_error


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine POSIX-Shell")
def test_successful_trial_package_summary_is_not_a_dpkg_error(updates, tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in {
        "apt": "printf 'Listing... Done\\n'; exit 0",
        "apt-get": "printf 'The following packages will be upgraded:\\n  dpkg liberror-perl\\nInst dpkg [1] (2)\\n'; exit 0",
    }.items():
        script = bindir / name
        script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        script.chmod(0o755)
    result = subprocess.run(["sh", "-c", updates.status_command(refresh=False)], capture_output=True, text=True,
                            env={**os.environ, "PATH": f"{bindir}:/usr/bin:/bin"}, timeout=15, check=True)
    status = updates.parse_status(result.stdout)
    assert status.error is None and status.phasing == "checked"


@pytest.mark.parametrize(("manager", "line", "package", "code"), [
    ("apt", "E: Failed to fetch https://mirror.example/debian/InRelease", "openssl/stable 3.0.14 amd64", 0),
    ("apt", "W: Failed to fetch https://mirror.example/debian/InRelease", "openssl/stable 3.0.14 amd64", 0),
    ("dnf", "Error: Failed to download metadata for repo 'updates'", "openssl.x86_64 3.0.14 updates", 100),
    ("apk", "ERROR: https://mirror.example/alpine", "openssl-3.0.13-r0 < 3.0.14-r0", 0),
])
@pytest.mark.parametrize("reason", ["503 Service Unavailable", "Connection timed out", "Resource temporarily unavailable"])
@pytest.mark.parametrize("section", ["refresh", "list"])
def test_source_diagnostics_keep_successful_lists(updates, manager, line, package, code, reason, section):
    warning = f"{line} {reason}"
    refresh = f"{warning}\nrc refresh 1" if section == "refresh" else "rc refresh 0"
    listing = f"{warning}\n" if section == "list" else ""
    status = updates.parse_status(f"@@pm\n{manager}\n@@refresh\n{refresh}\n@@list\n{listing}{package}\n"
                                  f"rc list {code}\n@@end\n")
    assert status.error is None and status.error_kind is None
    assert len(status.packages) == 1 and status.packages[0].name == "openssl"
    assert status.refresh_error == f"Nicht alle Paketquellen erreichbar: {warning}"


@pytest.mark.parametrize("reason", ["Connection timed out", "Resource temporarily unavailable"])
@pytest.mark.parametrize("section", ["refresh", "list"])
def test_source_diagnostic_continuations_keep_successful_lists(updates, reason, section):
    warning = f"Err:1 https://mirror.example/debian bookworm InRelease\n  {reason}\n"
    refresh = warning if section == "refresh" else ""
    listing = warning if section == "list" else ""
    status = updates.parse_status(f"@@pm\napt\n@@refresh\n{refresh}rc refresh {100 if refresh else 0}\n"
                                  f"@@list\n{listing}openssl/stable 3.0.14 amd64\nrc list 0\n@@end\n")
    assert status.error is None and status.error_kind is None
    assert [p.name for p in status.packages] == ["openssl"]
    assert "mirror.example" in status.refresh_error


@pytest.mark.parametrize("line", ["E: Failed to fetch https://mirror.example/InRelease Connection timed out",
                                  "E: Failed to fetch https://mirror.example/InRelease Resource temporarily unavailable"])
def test_source_warning_does_not_hide_a_failed_list(updates, line):
    status = updates.parse_status(f"@@pm\napt\n@@list\n{line}\nrc list 100\n@@end\n")
    assert status.error_kind == "detection" and status.error


@pytest.mark.parametrize("line", ["dpkg: error: failed to read package database", "E: Sub-process /usr/bin/dpkg returned an error code (1)"])
def test_dpkg_diagnostics_remain_errors(updates, line):
    assert updates.status_error_kind(line) == "dpkg"


def test_failed_trial_package_summary_is_not_a_dpkg_error(updates):
    status = updates.parse_status("@@pm\napt\n@@list\nopenssl/stable 3.0.14 amd64\nrc list 0\n"
                                  "@@phasing\nrc inst 100\nThe following packages will be upgraded:\n  dpkg liberror-perl\n"
                                  "rc all 100\n  dpkg liberror-perl\n@@end\n")
    assert status.error is None and status.error_kind is None and status.phasing == "unavailable"
    assert [p.name for p in status.packages] == ["openssl"]


def test_enterprise_hit_does_not_hide_an_unexplained_refresh_failure(updates):
    status = updates.parse_status("@@pm\napt\n@@refresh\nHit:1 https://enterprise.proxmox.com/debian/pve bookworm InRelease\n"
                                  "rc refresh 100\n@@list\nrc list 0\n@@end\n")
    assert status.error_kind == "detection" and status.error and status.refresh_error is None


@pytest.mark.parametrize("section", ["refresh", "list"])
def test_source_warning_hides_url_credentials(updates, section):
    line = "E: Failed to fetch https://user:" "secret@mirror.example/debian/InRelease 503 Service Unavailable"
    output = (f"@@pm\napt\n@@refresh\n{line}\nrc refresh 100\n@@list\nrc list 0\n@@end\n" if section == "refresh"
              else f"@@pm\napt\n@@list\n{line}\nrc list 0\n@@end\n")
    status = updates.parse_status(output)
    assert status.error is None
    assert status.refresh_error == ("Nicht alle Paketquellen erreichbar: E: Failed to fetch "
                                    "https://mirror.example/debian/InRelease 503 Service Unavailable")
