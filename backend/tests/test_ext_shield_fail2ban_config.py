"""Fail2ban-Konfiguration und die begrenzten Unterbefehle des gebündelten Hilfsskripts."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from shield_helper_kit import helper_copy, stubs, wait_for

EXT = Path(__file__).resolve().parents[2] / "extensions" / "shield"
PEER = "203.0.113.9"
READ = """@@bantime
600
@@findtime
600
@@maxretry
5
@@ignoreip
These IP addresses/networks are ignored:
|- 127.0.0.1/8
`- ::1
@@available
sshd
nginx-http-auth
@@status
Status
`- Jail list: sshd
@@managed
"""


@pytest.fixture
def f2b(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT / "src"))
    from nodvard_deck_ext_shield import fail2ban

    return fail2ban


def values(**changes):
    return {"bantime": 3600, "findtime": 600, "maxretry": 3, "ignoreip": ["127.0.0.1/8", "2001:db8::/64"],
            "jails": ["sshd"], "available_jails": ["sshd", "nginx-http-auth"], "deck_address": PEER, **changes}


def test_content_protects_the_peer_and_configures_every_detected_jail(f2b):
    text = f2b.content(values())
    assert text == ("[nginx-http-auth]\nenabled = false\n\n[sshd]\nenabled = true\n"
                    "bantime = 3600\nfindtime = 600\nmaxretry = 3\n"
                    "ignoreip = 127.0.0.0/8 2001:db8::/64 203.0.113.9\n")
    both = f2b.content(values(jails=["sshd", "nginx-http-auth"]))
    assert both.count("ignoreip = 127.0.0.0/8 2001:db8::/64 203.0.113.9") == 2


@pytest.mark.parametrize("changes", [
    {"bantime": True}, {"bantime": 0}, {"bantime": 59}, {"findtime": 1}, {"findtime": 31536001},
    {"ignoreip": ["0.0.0.0/0"]},
    {"ignoreip": ["fe80::%x\n[DEFAULT]\nbanaction = evil/64"]}, {"ignoreip": ["fe80::%eth0/64"]}, {"ignoreip": ["10.0.0.1 "]},
    {"ignoreip": ["2000::/3"]}, {"ignoreip": ["8.0.0.0/8", "9.0.0.0/8"]}, {"ignoreip": ["::ffff:12.0.0.0/104", "::ffff:11.0.0.0/104"]}, {"ignoreip": ["::/0"]}, {"ignoreip": ["10.0.0.0/7"]}, {"ignoreip": ["2001::/31"]},
    {"ignoreip": [f"{i}.0.0.0/8" for i in range(1, 12)]}, {"ignoreip": ["12.0.0.0/8", "11.0.0.0/8"]},
    {"ignoreip": ["2001:db8::/32", "2001:db9::/32"]}, {"maxretry": 21}, {"maxretry": 1000},
    {"ignoreip": ["::ffff:0.0.0.0/96"]}, {"ignoreip": ["127.0.0.0/8", "::1", "::ffff:10.0.0.0/103"]}, {"ignoreip": ["::ffff:0:0/96"]}, {"ignoreip": ["::/96"]}, {"ignoreip": ["::/64"]},
    {"ignoreip": ["::ffff:12.0.0.0/104", "::ffff:11.0.0.0/104"]}, {"maxretry": 1001}, {"maxretry": "5"},
    {"ignoreip": ["$(id)"]}, {"ignoreip": ["1.2.3.4;id"]}, {"ignoreip": ["bad/24"]},
    {"ignoreip": "1.2.3.4"}, {"jails": []}, {"jails": ["sshd", "unknown"]},
    {"available_jails": ["DEFAULT"]}, {"available_jails": ["loglevel"]},
    {"deck_address": ""}, {"sshd_backend": "$(id)"},
])
def test_untrusted_settings_cannot_build_a_command(f2b, changes):
    with pytest.raises(ValueError):
        f2b.command(values(**changes))


def test_smallest_allowed_limits_are_accepted(f2b):
    text = f2b.content(values(bantime=60, findtime=60, ignoreip=["10.0.0.0/9", "::ffff:10.128.0.0/105", "2001:db8::/32", "::1"], maxretry=20))
    assert "bantime = 60" in text and "findtime = 60" in text and all(x in text for x in ("10.0.0.0/9", "2001:db8::/32", "::1", "203.0.113.9"))


def test_usual_home_network_exceptions_are_accepted(f2b):
    nets = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10", "fd00::/8", "fe80::/10"]
    text = f2b.content(values(ignoreip=nets))
    assert all(n in text for n in nets)


def test_injection_through_an_exception_adds_no_lines(f2b):
    with pytest.raises(ValueError):
        f2b.content(values(ignoreip=["fe80::%x\n[DEFAULT]\nbanaction = evil/64"]))


def test_oversized_content_is_refused_before_proposing(f2b):
    jails = ["sshd", *(f"web{i}" for i in range(100))]
    with pytest.raises(ValueError, match="zu groß"):
        f2b.command(values(ignoreip=[f"2001:db8::{i:x}" for i in range(256)], jails=jails, available_jails=jails))


def test_read_parses_status_exceptions_and_preserves_our_systemd_backend(f2b):
    s = f2b.parse(READ, "::ffff:203.0.113.9")
    assert s["deck_address"] == PEER
    assert s["ignoreip"] == ["127.0.0.0/8", PEER, "::1"]
    assert s["available_jails"] == ["nginx-http-auth", "sshd"]
    assert s["jails"] == ["sshd"]
    installed = f2b.parse(READ + "[sshd]\nenabled = true\nbackend = systemd\n", PEER)
    assert "backend = systemd" in f2b.content(installed)
    own = f2b.content(values(jails=["sshd", "nginx-http-auth"]))
    output = READ.replace("Jail list: sshd", "Jail list: sshd, nginx-http-auth")
    assert f2b.parse(output + own, PEER)["jails"] == ["nginx-http-auth", "sshd"]


@pytest.mark.parametrize("runtime", [0, 1])
def test_active_jails_survive_changing_bantime(f2b, runtime):
    output = READ.replace("Jail list: sshd", "Jail list: sshd, nginx-http-auth")
    output = output.replace("@@managed\n", f"@@runtime\n{runtime}\n@@managed\n")
    parsed = f2b.parse(output + "[sshd]\nenabled = true\nbackend = systemd\n", PEER)
    assert parsed["jails"] == ["nginx-http-auth", "sshd"]
    saved = f2b.content({**parsed, "bantime": 7200})
    assert "[nginx-http-auth]\nenabled = true\nbantime = 7200\n" in saved
    assert "[sshd]\nenabled = true\nbackend = systemd\nbantime = 7200\n" in saved


def test_saved_jails_are_used_when_status_is_unavailable(f2b):
    output = READ.replace("Status\n`- Jail list: sshd\n", "")
    output = output.replace("@@managed\n", "@@runtime\n0\n@@managed\n")
    saved = f2b.content(values(jails=["sshd", "nginx-http-auth"]))
    parsed = f2b.parse(output + saved, PEER)
    assert parsed["jails"] == ["nginx-http-auth", "sshd"]
    assert parsed["runtime"] is False


@pytest.mark.parametrize("runtime", [None, 1])
def test_runtime_values_take_precedence_over_saved_settings(f2b, runtime):
    output = READ.replace("@@maxretry\n5\n", "@@maxretry\n100\n")
    if runtime is not None:
        output = output.replace("@@managed\n", f"@@runtime\n{runtime}\n@@managed\n")
    parsed = f2b.parse(output + f2b.content(values()), PEER)
    assert parsed["runtime"] is True
    assert (parsed["bantime"], parsed["findtime"], parsed["maxretry"]) == (600, 600, 100)
    assert parsed["ignoreip"] == ["127.0.0.0/8", PEER, "::1"]


def test_saved_values_are_marked_as_fallback_when_runtime_is_unavailable(f2b):
    output = READ.replace("@@managed\n", "@@runtime\n0\n@@managed\n")
    parsed = f2b.parse(output + f2b.content(values()), PEER)
    assert parsed["runtime"] is False
    assert (parsed["bantime"], parsed["findtime"], parsed["maxretry"]) == (3600, 600, 3)
    assert parsed["ignoreip"] == ["127.0.0.0/8", "2001:db8::/64", PEER]


def test_commands_require_the_new_bundled_helper_and_pass_the_gate(f2b):
    from nodvard_deck.core.deny_patterns import match_deny_patterns
    from nodvard_deck_ext_shield import antivirus as av

    assert av.HELPER_BUNDLED[0] >= 22
    assert f2b.HELPER_VERSION == 19
    for command in (f2b.command(values()), f2b.read_command()):
        assert match_deny_patterns(command) is None
        assert "sudo -n sh -c" not in command
        assert f'" -ge {f2b.HELPER_VERSION} ]' in command
        assert av.HELPER_BUNDLED[1] in command


@pytest.mark.parametrize("version", [None, 18])
def test_commands_refuse_missing_or_outdated_bundled_helper(f2b, monkeypatch, version):
    av = f2b.av
    bundled = (version, av.HELPER_BUNDLED[1]) if version is not None else None
    monkeypatch.setattr(av, "HELPER_BUNDLED", bundled)
    for build in (lambda: f2b.command(values()), f2b.read_command):
        with pytest.raises(ValueError) as exc:
            build()
        assert str(exc.value) == av.HELPER_NOT_BUNDLED


needs_sh = pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None, reason="braucht eine POSIX-Shell")


@pytest.fixture
def world(tmp_path):
    directory = tmp_path / "etc" / "fail2ban" / "jail.d"
    directory.mkdir(parents=True)
    (directory / "web.conf").write_text("[nginx-http-auth]\nenabled = false\n")
    for name in ("jail.conf", "jail.local"):
        (directory.parent / name).write_text("[sshd]\nmaxretry = 9\n")
    log = tmp_path / "calls"
    tools = stubs(tmp_path, {"fail2ban-client": f'''echo "$*" >> {log}
case "$*" in
  '-t') [ ! -e {tmp_path / "bad-test"} ] ;;
  '-d')
    if [ -f {tmp_path / "dump"} ]; then cat {tmp_path / "dump"}; exit 0; fi
    awk '
      /^\\[/ {{ jail=substr($0, 2, length($0)-2); on=0 }}
      $1 == "enabled" && $3 == "true" {{ on=1; print "[\\047add\\047, \\047" jail "\\047, \\047systemd\\047]" }}
      on && $1 ~ /^(bantime|findtime|maxretry)$/ {{ print "[\\047set\\047, \\047" jail "\\047, \\047" $1 "\\047, " $3 "]" }}
      on && $1 == "ignoreip" {{
        for (i=3; i<=NF; i++) print "[\\047set\\047, \\047" jail "\\047, \\047addignoreip\\047, \\047" $i "\\047]"
      }}
    ' {directory / "nodvard.local"} | {{
      if [ -e {tmp_path / "unprotected"} ]; then grep -v addignoreip; else cat; fi
    }}
    exit 0 ;;
  reload) [ ! -e {tmp_path / "bad-reload"} ] ;;
  'get sshd bantime' | 'get sshd findtime') [ ! -e {tmp_path / "stopped"} ] || exit 1; echo 600 ;;
  'get sshd maxretry') [ ! -e {tmp_path / "stopped"} ] || exit 1; echo 5 ;;
  'get sshd ignoreip') [ ! -e {tmp_path / "stopped"} ] || exit 1; printf '127.0.0.1/8\\n::1\\n' ;;
  status) [ ! -e {tmp_path / "stopped"} ] || exit 1; echo 'Jail list: sshd' ;;
  *) exit 65 ;;
esac
'''})
    helper = helper_copy(tmp_path, path=f"{tools}:/usr/bin:/bin", sdmark=tmp_path / "no-systemd")
    return helper, directory, log


def run(helper, *args, peer=None):
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
    if peer:
        env["SSH_CONNECTION"] = f"{peer} 50000 198.51.100.1 22"
    return subprocess.run(["sh", str(helper), *args], capture_output=True, text=True, env=env, timeout=30, check=False)


@needs_sh
def test_read_subcommand_only_reads_with_stubs(f2b, world):
    helper, directory, log = world
    before = {p: p.read_bytes() for p in directory.parent.rglob("*") if p.is_file()}
    result = run(helper, "fail2ban-config-read")
    assert result.returncode == 0, result.stderr
    assert f2b.parse(result.stdout, PEER)["available_jails"] == ["nginx-http-auth", "sshd"]
    assert log.read_text().splitlines() == ["get sshd bantime", "get sshd findtime", "get sshd maxretry", "get sshd ignoreip", "status"]
    assert before == {p: p.read_bytes() for p in directory.parent.rglob("*") if p.is_file()}


@needs_sh
def test_stopped_service_still_exposes_saved_settings_readonly(f2b, tmp_path, world):
    helper, directory, log = world
    (tmp_path / "stopped").touch()
    target = directory / "nodvard.local"
    saved = f2b.content(values())
    target.write_text(saved)
    result = run(helper, "fail2ban-config-read")
    assert result.returncode == 0
    parsed = f2b.parse(result.stdout, PEER)
    assert parsed["runtime"] is False and parsed["bantime"] == 3600 and parsed["maxretry"] == 3
    assert target.read_text() == saved
    assert "reload" not in log.read_text() and "-t" not in log.read_text()


@needs_sh
@pytest.mark.parametrize("old", [None, "[sshd]\nenabled = true\nmaxretry = 9\n"])
@pytest.mark.parametrize("failure", [None, "bad-test", "bad-reload"])
def test_write_tests_before_reload_and_restores_on_failure(f2b, tmp_path, world, old, failure):
    helper, directory, log = world
    target = directory / "nodvard.local"
    if old is not None:
        target.write_text(old)
    if failure:
        (tmp_path / failure).touch()
    result = run(helper, "fail2ban-config-write", PEER, f2b.content(values()))
    assert (result.returncode == 0) is (failure is None), result.stderr
    assert log.read_text().splitlines() == (["-t"] if failure == "bad-test" else ["-t", "-d", "reload"])
    if failure:
        assert target.read_text() == old if old is not None else not target.exists()
    else:
        assert target.read_text() == f2b.content(values()) + "\n"
        assert target.stat().st_mode & 0o777 == 0o644
    for name in ("jail.conf", "jail.local"):
        assert (directory.parent / name).read_text() == "[sshd]\nmaxretry = 9\n"
    assert sorted(p.name for p in directory.iterdir()) == (["nodvard.local", "web.conf"] if target.exists() else ["web.conf"])


@needs_sh
def test_configuring_fail2ban_preserves_lock_between_watch_and_scan(f2b, tmp_path, world):
    helper, _, _ = world
    assert run(helper, "fail2ban-config-write", PEER, f2b.content(values())).returncode == 0
    tree = tmp_path / "files"
    tree.mkdir()
    (tree / "sample").write_text("clean")
    started, release = tmp_path / "started", tmp_path / "release"
    stubs(tmp_path, {"clamscan": f'''case "$*" in
  *--file-list=*) touch {started}; while [ ! -e {release} ]; do sleep 0.05; done ;;
esac
echo 'Scanned files: 1'
'''})
    mark = "@@scan-rc-0123456789abcdef="
    args = f2b.av.watch_helper_args([str(tree)], minutes=10, max_filesize_mb=50, use_clamd=False,
                                   mark=mark, skip_folders=[], limit_s=300)
    watcher = subprocess.Popen(["sh", str(helper), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_for(started)
        args = f2b.av.scan_helper_args([str(tree)], max_filesize_mb=50, exclude=[], mark=mark, limit_s=300, wait_s=1)
        quick = run(helper, *args)
        assert quick.returncode == 0, quick.stderr
        assert quick.stdout == "@@scan-busy-0123456789abcdef\n"
        assert (tmp_path / "lock").stat().st_mode & 0o7777 == 0o700
    finally:
        release.touch()
        stdout, stderr = watcher.communicate(timeout=10)
    assert watcher.returncode == 0, stderr
    assert stdout.endswith(mark + "0\n")


@needs_sh
@pytest.mark.parametrize("unsafe", ["permissions", "link"])
def test_write_refuses_an_unsafe_shared_lock_directory(f2b, tmp_path, world, unsafe):
    helper, directory, log = world
    lock = tmp_path / "lock"
    if unsafe == "link":
        other = tmp_path / "other"
        other.mkdir(mode=0o700)
        lock.symlink_to(other, target_is_directory=True)
    else:
        lock.mkdir(mode=0o755)
        lock.chmod(0o755)
    result = run(helper, "fail2ban-config-write", PEER, f2b.content(values()))
    assert result.returncode == 64
    assert not (directory / "nodvard.local").exists()
    assert not (lock / "fail2ban-config.lock").exists()
    assert not log.exists()


@needs_sh
def test_overridden_peer_exception_prevents_reload_and_restores_the_file(f2b, tmp_path, world):
    helper, directory, log = world
    (tmp_path / "unprotected").touch()
    target = directory / "nodvard.local"
    target.write_text("[sshd]\nenabled = true\n")
    result = run(helper, "fail2ban-config-write", PEER, f2b.content(values()))
    assert result.returncode != 0 and "@@msg=fail2ban_unprotected" in result.stdout
    assert target.read_text() == "[sshd]\nenabled = true\n"
    assert log.read_text().splitlines() == ["-t", "-d"]


@needs_sh
@pytest.mark.parametrize("old", [None, "[sshd]\nenabled = true\nignoreip = 198.51.100.0/24\n"])
@pytest.mark.parametrize("jail", ["sshd", "nginx-http-auth"])
@pytest.mark.parametrize("change", ["removed-network", "missing-network", "replaced-network", "matching"])
def test_effective_exceptions_must_match_the_approved_set(f2b, tmp_path, world, old, jail, change):
    helper, directory, log = world
    target = directory / "nodvard.local"
    if old is not None:
        target.write_text(old)
    requested = values(jails=["sshd", "nginx-http-auth"])
    approved = ["127.0.0.0/8", "2001:db8::/64", PEER]
    lines = []
    for active in requested["jails"]:
        lines.append(f"['add', '{active}', 'systemd']")
        for key in ("bantime", "findtime", "maxretry"):
            lines.append(f"['set', '{active}', '{key}', {requested[key]}]")
        effective = list(reversed(approved))
        if active == jail:
            if change in ("removed-network", "replaced-network"):
                effective.append("198.51.100.0/24")
            if change in ("missing-network", "replaced-network"):
                effective.remove("2001:db8::/64")
        quoted = ", ".join(repr(ip) for ip in effective)
        lines.append(f"['set', '{active}', 'addignoreip', {quoted}]")
    (tmp_path / "dump").write_text("\n".join(lines) + "\n")
    result = run(helper, "fail2ban-config-write", PEER, f2b.content(requested))
    if change == "matching":
        assert result.returncode == 0, result.stderr
        assert target.read_text() == f2b.content(requested) + "\n"
        assert log.read_text().splitlines() == ["-t", "-d", "reload"]
    else:
        assert result.returncode != 0 and "@@msg=fail2ban_conflict" in result.stdout
        assert target.read_text() == old if old is not None else not target.exists()
        assert log.read_text().splitlines() == ["-t", "-d"]
    assert not list(directory.glob(".nodvard*"))


@needs_sh
@pytest.mark.parametrize("old", [None, "[sshd]\nenabled = true\nmaxretry = 9\n"])
@pytest.mark.parametrize("jail", ["sshd", "nginx-http-auth"])
@pytest.mark.parametrize("key, effective", [("bantime", 100), ("findtime", 100), ("maxretry", 100)])
def test_overridden_numeric_settings_prevent_reload_and_restore_the_file(f2b, tmp_path, world, old, jail, key, effective):
    helper, directory, log = world
    target = directory / "nodvard.local"
    if old is not None:
        target.write_text(old)
    requested = values(jails=["sshd", "nginx-http-auth"])
    lines = []
    for active in requested["jails"]:
        lines.append(f"['add', '{active}', 'systemd']")
        lines.append(f"['set', '{active}', 'addignoreip', '127.0.0.0/8', '2001:db8::/64', '{PEER}']")
        for field in ("bantime", "findtime", "maxretry"):
            value = effective if (active, field) == (jail, key) else requested[field]
            lines.append(f"['set', '{active}', '{field}', {value}]")
    (tmp_path / "dump").write_text("\n".join(lines) + "\n")
    result = run(helper, "fail2ban-config-write", PEER, f2b.content(requested))
    assert result.returncode != 0 and "@@msg=fail2ban_conflict" in result.stdout
    assert target.read_text() == old if old is not None else not target.exists()
    assert log.read_text().splitlines() == ["-t", "-d"]
    assert not list(directory.glob(".nodvard*"))


@needs_sh
def test_matching_numeric_settings_are_accepted_for_every_enabled_jail(f2b, world):
    helper, directory, log = world
    requested = values(bantime=7200, findtime=900, maxretry=7, jails=["sshd", "nginx-http-auth"])
    result = run(helper, "fail2ban-config-write", PEER, f2b.content(requested))
    assert result.returncode == 0, result.stderr
    assert (directory / "nodvard.local").read_text() == f2b.content(requested) + "\n"
    assert log.read_text().splitlines() == ["-t", "-d", "reload"]


@needs_sh
@pytest.mark.parametrize("change", ["matching", "missing-number", "disabled", "extra-jail"])
def test_effective_jails_and_numbers_must_match_the_approved_settings(f2b, tmp_path, world, change):
    helper, directory, log = world
    requested = values(jails=["sshd", "nginx-http-auth"])
    content = f2b.content(requested)
    lines = []
    for jail in requested["jails"]:
        if change != "disabled" or jail != "nginx-http-auth":
            lines.append(f"['add', '{jail}', 'systemd']")
        lines.append(f"['set', '{jail}', 'addignoreip', '127.0.0.0/8', '2001:db8::/64', '{PEER}']")
        for key in ("bantime", "findtime", "maxretry"):
            if change != "missing-number" or (jail, key) != ("sshd", "maxretry"):
                lines.append(f"['set', '{jail}', '{key}', {requested[key]}]")
    if change == "extra-jail":
        # Die übrigen Werte bleiben gleich, damit nur die Jail-Prüfung greift.
        content = content.replace("[nginx-http-auth]\nenabled = true", "[nginx-http-auth]\nenabled = false")
    (tmp_path / "dump").write_text("\n".join(lines) + "\n")
    result = run(helper, "fail2ban-config-write", PEER, content)
    if change == "matching":
        assert result.returncode == 0, result.stderr
        assert (directory / "nodvard.local").read_text() == content + "\n"
        assert log.read_text().splitlines() == ["-t", "-d", "reload"]
    else:
        assert result.returncode != 0 and "@@msg=fail2ban_conflict" in result.stdout
        assert not (directory / "nodvard.local").exists()
        assert log.read_text().splitlines() == ["-t", "-d"]
    assert not list(directory.glob(".nodvard*"))


def test_config_conflict_has_a_readable_message(f2b):
    message = f2b.av.helper_text("@@msg=fail2ban_conflict\n")
    assert "vorrangige Jail-Einstellungen" in message
    assert "zurückgesetzt" in message
    assert "@@msg=" not in message


@needs_sh
@pytest.mark.parametrize("replacement", [
    ("maxretry = 3", "action = %(action_mwl)s"), ("maxretry = 3", "maxretry = 3; id"),
    ("maxretry = 3", "maxretry = $(id)"), ("[sshd]", "[DEFAULT]"),
    ("[nginx-http-auth]", "[unknown]"), ("maxretry = 3", "maxretry = 0"),
    ("enabled = true", "enabled = false"), (PEER, "203.0.113.90"),
    ("findtime = 600\n", ""), ("maxretry = 3", "maxretry = 3\nmaxretry = 5"),
])
def test_write_refuses_untrusted_content_before_writing(f2b, world, replacement):
    helper, directory, log = world
    text = f2b.content(values()).replace(*replacement)
    result = run(helper, "fail2ban-config-write", PEER, text)
    assert result.returncode == 64, result.stderr
    assert not (directory / "nodvard.local").exists()
    assert not log.exists()


@needs_sh
@pytest.mark.parametrize("args", [("fail2ban-config-read", "extra"), ("fail2ban-config-write",),
                                  ("fail2ban-config-write", "$(id)", ""), ("fail2ban-config-write", PEER, "", "extra")])
def test_subcommands_refuse_extra_or_missing_arguments(world, args):
    helper, directory, log = world
    assert run(helper, *args).returncode == 64
    assert not (directory / "nodvard.local").exists() and not log.exists()


@needs_sh
@pytest.mark.parametrize("peer", [None, "203.0.113.90", "$(id)"])
def test_source_guard_refuses_changed_or_unknown_connection_before_privileged_commands(f2b, tmp_path, peer):
    result = subprocess.run(["sh", "-c", f2b.command(values())], capture_output=True, text=True, timeout=30,
                            env={"PATH": "/usr/bin:/bin", **({"SSH_CLIENT": f"{peer} 5000 22"} if peer else {})}, check=False)
    assert result.returncode == 5
    assert "SSH-Quelladresse" in result.stderr


@needs_sh
@pytest.mark.parametrize(("expected", "seen"), [(PEER, PEER), (PEER, "::ffff:203.0.113.9"),
                                               ("fe80::1", "fe80::1"), ("fe80::1", "fe80::1%eth0"),
                                               ("2001:db8::1", "2001:0db8:0000:0000:0000:0000:0000:0001")])
def test_source_guard_accepts_the_approved_source_in_the_executing_connection(f2b, expected, seen):
    guard = f2b.command(values(deck_address=expected)).split('if [ "$(id -u)"', 1)[0]
    result = subprocess.run(["sh", "-c", guard + "echo reached"], capture_output=True, text=True, timeout=30,
                            env={"PATH": "/usr/bin:/bin", "SSH_CONNECTION": f"{seen} 5000 198.51.100.1 22"}, check=False)
    assert result.returncode == 0 and result.stdout == "reached\n", result.stderr
