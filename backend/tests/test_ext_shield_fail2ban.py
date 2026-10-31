"""Nodvard Shield, Fail2ban: Schreibweise der Adressen und der Schutz davor, dass Deck sich selbst aussperrt."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EXT_SRC = Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"


@pytest.fixture
def ix(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_SRC))
    from nodvard_deck_ext_shield import intrusion

    yield intrusion
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


@pytest.mark.parametrize(("raw", "want"), [
    (" 203.0.113.9 ", "203.0.113.9"), ("2001:DB8:0:0::7", "2001:db8::7"), ("::ffff:203.0.113.9", "203.0.113.9"),
    ("fe80::1", "fe80::1"),
])
def test_canonical_ip_writes_addresses_like_fail2ban(ix, raw, want):
    assert ix.canonical_ip(raw) == want


@pytest.mark.parametrize("raw", ["", "kein-ip", "203.0.113.9/24", "fe80::1%eth0", "010.0.0.1", "1.2.3.4; id"])
def test_canonical_ip_refuses_everything_else(ix, raw):
    with pytest.raises(ValueError, match="Ungültige IP-Adresse"):
        ix.canonical_ip(raw)


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.8.8.8", "::1", "0.0.0.0", "::", "224.0.0.1", "ff02::1", "255.255.255.255", "::ffff:127.0.0.1",
])
def test_only_addresses_of_other_machines_are_banned(ix, ip):
    with pytest.raises(ValueError, match="keine Adresse eines anderen Rechners"):
        ix.ban_command(ip)
    assert ix.ban_command(ip, unban=True).endswith(f"unbanip {ip}")


def test_a_mapped_address_counts_like_its_ipv4_address(ix):
    assert ix.ban_command("::ffff:203.0.113.9").endswith("banip ::ffff:203.0.113.9")
    with pytest.raises(ValueError, match="keine Adresse eines anderen Rechners"):
        ix.ban_command("::ffff:127.0.0.1")


@pytest.mark.parametrize(("seen", "ip", "same"), [
    ("203.0.113.9\n", "203.0.113.9", True),
    ("::ffff:203.0.113.9", "203.0.113.9", True),
    ("fe80::5%eth0", "fe80::5", True),
    ("2001:db8::9", "2001:DB8::9", True),
    ("203.0.113.10", "203.0.113.9", False),
    ("", "203.0.113.9", False),
    ("$(id)", "203.0.113.9", False),
])
def test_is_deck_address(ix, seen, ip, same):
    assert ix.is_deck_address(ip, seen) is same


def _run_guarded(ix, tmp_path: Path, ip: str, peer: dict[str, str]) -> tuple[subprocess.CompletedProcess, list[str]]:
    """`deck_address_guard` vor einem Aufruf von fail2ban-client (Attrappe), mit den Variablen, die sshd setzt."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "aufrufe"
    stub = bindir / "fail2ban-client"
    stub.write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
    stub.chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "LC_ALL": "C", **peer}
    done = subprocess.run(["sh", "-c", ix.deck_address_guard(ip) + ix.ban_command(ip)], capture_output=True, text=True,
                          env=env, timeout=30, check=False)
    return done, log.read_text().splitlines() if log.exists() else []


@pytest.mark.skipif(shutil.which("sh") is None, reason="braucht eine POSIX-Shell")
@pytest.mark.parametrize(("ip", "peer"), [
    ("203.0.113.9", {"SSH_CLIENT": "203.0.113.9 51515 22"}),
    ("203.0.113.9", {"SSH_CLIENT": "::ffff:203.0.113.9 51515 22"}),
    ("fe80::5", {"SSH_CLIENT": "fe80::5%eth0 51515 22"}),
    ("2001:db8::9", {"SSH_CLIENT": "2001:db8::9 51515 22"}),
    ("203.0.113.9", {"SSH_CONNECTION": "203.0.113.9 51515 198.51.100.2 22"}),
    ("203.0.113.9", {"SSH_CLIENT": "", "SSH_CONNECTION": "203.0.113.9 51515 198.51.100.2 22"}),
])
def test_the_server_refuses_to_ban_the_address_deck_comes_from(ix, tmp_path, ip, peer):
    done, calls = _run_guarded(ix, tmp_path, ip, peer)
    assert done.returncode == 5 and done.stderr.strip() == ix.DECK_SELF_REFUSED
    assert calls == []


@pytest.mark.skipif(shutil.which("sh") is None, reason="braucht eine POSIX-Shell")
@pytest.mark.parametrize(("ip", "peer"), [
    ("203.0.113.9", {"SSH_CLIENT": "203.0.113.90 51515 22"}),
    ("203.0.113.9", {"SSH_CLIENT": "198.51.100.1 51515 22"}),
    ("fe80::5", {"SSH_CLIENT": "fe80::55%eth0 51515 22"}),
    ("203.0.113.9", {"SSH_CONNECTION": "198.51.100.1 51515 198.51.100.2 22"}),
])
def test_other_addresses_are_banned_as_before(ix, tmp_path, ip, peer):
    done, calls = _run_guarded(ix, tmp_path, ip, peer)
    assert done.returncode == 0, done.stderr
    assert calls == [f"set sshd banip {ip}"]


@pytest.mark.skipif(shutil.which("sh") is None, reason="braucht eine POSIX-Shell")
@pytest.mark.parametrize("peer", [{}, {"SSH_CLIENT": ""}, {"SSH_CLIENT": "", "SSH_CONNECTION": ""}])
def test_without_ssh_client_nothing_is_banned(ix, tmp_path, peer):
    done, calls = _run_guarded(ix, tmp_path, "203.0.113.9", peer)
    assert done.returncode == 5 and done.stderr.strip() == ix.DECK_ADDRESS_UNKNOWN
    assert calls == []


@pytest.mark.skipif(shutil.which("sh") is None, reason="braucht eine POSIX-Shell")
@pytest.mark.parametrize(("peer", "seen"), [
    ({"SSH_CLIENT": "203.0.113.9 51515 22"}, "203.0.113.9"),
    ({"SSH_CONNECTION": "fe80::5%eth0 51515 fe80::1%eth0 22"}, "fe80::5%eth0"),
    ({}, ""),
])
def test_the_address_command_reads_the_other_end_of_the_connection(ix, peer, seen):
    done = subprocess.run(["sh", "-c", ix.DECK_ADDRESS_COMMAND], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", **peer}, timeout=30, check=False)
    assert done.returncode == 0 and done.stdout == f"{seen}\n"


@pytest.mark.parametrize(("exit_code", "output", "quiet"), [
    (0, "0\n", True),
    (255, "ERROR  NOK: ('IP 203.0.113.9 is not banned',)\n", True),
    (0, "1\n", False),
    (255, "ERROR  NOK: ('sshd',)\n", False),
    (1, "0\n", False),
])
def test_was_not_banned(ix, exit_code, output, quiet):
    assert ix.was_not_banned(exit_code, output) is quiet


@pytest.mark.parametrize(("a", "b", "same"), [
    ("203.0.113.9", "::ffff:203.0.113.9", True),
    ("2001:db8::1", "2001:DB8:0::1", True),
    ("203.0.113.9", "203.0.113.90", False),
    ("fe80::1%eth0", "fe80::1", False),
    ("", "203.0.113.9", False),
])
def test_same_address(ix, a, b, same):
    assert ix.same_address(a, b) is same


@pytest.mark.parametrize("name", ["logtarget", "loglevel", "syslogsocket", "allowipv6", "dbfile", "dbpurgeage",
                                  "dbmaxmatches", "thread", "LogTarget"])
def test_settings_of_fail2ban_are_no_jails(ix, name):
    assert not ix.valid_jail(name)
    with pytest.raises(ValueError, match="Ungültiger Jail-Name"):
        ix.ban_command("203.0.113.9", name)
    with pytest.raises(ValueError, match="Ungültiger Jail-Name"):
        ix.ban_args("203.0.113.9", name, unban=True)
    assert ix.valid_jail("sshd") and ix.valid_jail("nginx-http-auth")


@pytest.mark.parametrize(("values", "hosts"), [
    (["203.0.113.9"], ["203.0.113.9"]),
    (["203.0.113.9:51515, 198.51.100.1"], ["203.0.113.9", "198.51.100.1"]),
    (["[2001:db8::9]:443", "2001:db8::a"], ["2001:db8::9", "2001:db8::a"]),
    ([" , unbekannt"], ["unbekannt"]),
])
def test_forwarded_hosts(ix, values, hosts):
    assert ix.forwarded_hosts(values) == hosts


def test_the_guard_only_takes_valid_addresses(ix):
    for bad in ("203.0.113.9) id;; *", "$(id)", "fe80::1%eth0"):
        with pytest.raises(ValueError):
            ix.deck_address_guard(bad)
