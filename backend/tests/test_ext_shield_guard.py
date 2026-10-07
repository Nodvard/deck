"""Einbruchschutz und Datei-Waechter in Nodvard Shield: Auswertung und Ablauf mit Fake-Kontext."""

from __future__ import annotations

import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"))

from nodvard_deck.core.deny_patterns import match_deny_patterns  # noqa: E402
from nodvard_deck_ext_shield import intrusion as ix  # noqa: E402

SHA_A = "a" * 64
SHA_B = "b" * 64


def _output(*, ssh: str = "", f2b: str = "none", ports: str = "", files: str = "", bins: str = "") -> str:
    return f"@@uid\n0\n@@ssh\n{ssh}\n@@f2b\n{f2b}\n@@ports\n{ports}\n@@files\n{files}\n@@bins\n{bins}\n@@end\n"


SSH_LINES = "\n".join([
    "1758800000.100000 pve2 sshd[100]: Invalid user admin from 203.0.113.9 port 40000",
    "1758800001.100000 pve2 sshd[100]: Failed password for invalid user admin from 203.0.113.9 port 40000 ssh2",
    "1758800002.100000 pve2 sshd[101]: Failed password for root from 203.0.113.9 port 40001 ssh2",
    "1758800003.100000 pve2 sshd[102]: Failed password for root from 198.51.100.7 port 50000 ssh2",
    "1758800004.100000 pve2 sshd[103]: Connection closed by authenticating user root 198.51.100.7 port 50001 [preauth]",
    "1758800100.100000 pve2 sshd[104]: Accepted publickey for root from 192.168.1.20 port 51000 ssh2: ED25519 SHA256:x",
    "Sep 25 10:00:00 pve2 sshd[105]: Accepted password for anna from 192.168.1.21 port 51001 ssh2",
])

F2B = """running
@@jail sshd
Status for the jail: sshd
|- Filter
|  |- Currently failed:	1
|  |- Total failed:	57
|  `- Journal matches:	_SYSTEMD_UNIT=sshd.service + _COMM=sshd
`- Actions
   |- Currently banned:	2
   |- Total banned:	9
   `- Banned IP list:	198.51.100.7 2001:db8::1"""

PORTS = "\n".join([
    'tcp   LISTEN 0      128          0.0.0.0:22        0.0.0.0:*    users:(("sshd",pid=812,fd=3))',
    'tcp   LISTEN 0      128             [::]:22           [::]:*    users:(("sshd",pid=812,fd=4))',
    'tcp   LISTEN 0      4096       127.0.0.1:5432      0.0.0.0:*    users:(("postgres",pid=900,fd=5))',
    'udp   UNCONN 0      0            0.0.0.0:68        0.0.0.0:*    users:(("dhclient",pid=77,fd=6))',
])


def test_parse_ssh_counts_each_attempt_once_and_keeps_logins():
    failures, logins = ix.parse_ssh(SSH_LINES.splitlines())
    assert failures["203.0.113.9"].count == 2 and failures["203.0.113.9"].users == {"admin", "root"}
    assert failures["198.51.100.7"].count == 2
    assert failures["203.0.113.9"].last_ts == pytest.approx(1758800002.1)
    assert [(lg.user, lg.ip, lg.method) for lg in logins] == [
        ("root", "192.168.1.20", "publickey"), ("anna", "192.168.1.21", "password"),
    ]
    assert logins[1].ts is None  # auth.log-Format ohne Unix-Zeit


def test_parse_fail2ban_ports_and_files():
    snap = ix.parse_guard(_output(
        ssh=SSH_LINES, f2b=F2B, ports=PORTS,
        files=f"{SHA_A} 644 root 1758800000 1238 - /etc/passwd",
        bins=f"{SHA_B} 755 root 1758800000 99000 pkg-ok /usr/bin/sudo",
    ))
    assert snap.is_root and snap.fail2ban == "running"
    assert snap.jails["sshd"]["total_failed"] == 57 and snap.jails["sshd"]["currently_banned"] == 2
    assert snap.banned == {"198.51.100.7", "2001:db8::1"}
    assert [(p.port, p.proto, p.process, p.public) for p in snap.ports] == [
        (22, "tcp", "sshd", True), (68, "udp", "dhclient", True), (5432, "tcp", "postgres", False),
    ]
    assert snap.files["/etc/passwd"].sha256 == SHA_A and not snap.files["/etc/passwd"].binary
    assert snap.files["/usr/bin/sudo"].binary and snap.files["/usr/bin/sudo"].verify == "pkg-ok"


def test_diff_files_rates_changes():
    accepted = {
        "/etc/passwd": {"sha256": SHA_A, "mode": "644", "owner": "root"},
        "/usr/bin/sudo": {"sha256": SHA_A, "mode": "4755", "owner": "root"},
        "/usr/bin/ps": {"sha256": SHA_A, "mode": "755", "owner": "root"},
        "/etc/cron.d/old": {"sha256": SHA_A, "mode": "644", "owner": "root"},
    }
    current = ix.parse_files([
        f"{SHA_A} 666 root 1 10 - /etc/passwd",
        f"{SHA_B} 644 root 1 10 - /root/.ssh/authorized_keys",
    ], binary=False)
    current.update(ix.parse_files([
        f"{SHA_B} 4755 root 1 10 pkg-ok /usr/bin/sudo",
        f"{SHA_B} 755 root 1 10 pkg-mismatch /usr/bin/ps",
    ], binary=True))
    changes = {c.path: c for c in ix.diff_files(accepted, current)}
    assert changes["/etc/passwd"].note == "Rechte 644 → 666 geändert" and changes["/etc/passwd"].severity == "warning"
    assert changes["/root/.ssh/authorized_keys"].change == "added" and "SSH-Schlüssel" in changes["/root/.ssh/authorized_keys"].note
    assert changes["/usr/bin/sudo"].auto_accept and changes["/usr/bin/sudo"].severity == "info"
    assert changes["/usr/bin/ps"].severity == "critical" and "Rootkit" in changes["/usr/bin/ps"].note
    assert changes["/etc/cron.d/old"].change == "removed"


def test_commands_are_safe():
    cmd = ix.guard_command(["/etc/passwd", "/home/*/.ssh/authorized_keys", "/tmp/x; rm -rf /", "/etc/../../x"])
    assert "rm -rf" not in cmd and "/etc/../../x" not in cmd and "/home/*/.ssh/authorized_keys" in cmd
    assert match_deny_patterns(cmd) is None
    assert ix.ban_command("203.0.113.9") == "fail2ban-client set sshd banip 203.0.113.9"
    assert ix.ban_command("2001:db8::1", unban=True) == "fail2ban-client set sshd unbanip 2001:db8::1"
    for bad in ("1.2.3.4; reboot", "evil"):
        with pytest.raises(ValueError):
            ix.ban_command(bad)


# Der Benutzername kommt vom Angreifer. "x from fe80::1%$(...)" als Name
# ergab bisher die "IP" fe80::1%$(...) (IPv6-Scope-ID laesst ipaddress durch), und
# "Sperren" fuehrte das $(...) als root aus.
INJECTED_SSH = [
    "1758800000.100000 pve2 sshd[100]: Invalid user x from fe80::1%$(touch${IFS}zz_pwned_marker) from 203.0.113.5 port 4242",
    # Per `logger -t sshd` kann jeder lokale Nutzer beliebige Zeilen ins Journal schreiben.
    "1758800001.100000 pve2 sshd[101]: Failed password for root from fe80::1%$(id) port 22 ssh2",
    "1758800002.100000 pve2 sshd[102]: Connection closed by authenticating user root fe80::1%a;id port 22 [preauth]",
    "1758800003.100000 pve2 sshd[103]: Accepted password for root from fe80::1%`id` port 22 ssh2",
]


def test_injected_scope_id_never_shows_up_as_an_address():
    failures, logins = ix.parse_ssh(INJECTED_SSH)
    assert not [ip for ip in failures if "%" in ip or "$" in ip]
    assert not [lg.ip for lg in logins if "%" in lg.ip]
    _state, jails = ix.parse_jails(["running", "@@jail sshd", "`- Banned IP list:\tfe80::1%$(id) 198.51.100.7"])
    assert jails["sshd"]["banned"] == ["198.51.100.7"]


def test_attacker_chosen_username_cannot_fake_addresses_or_logins():
    """Der Benutzername steht mitten in der Zeile: er darf weder eine fremde Adresse
    zum Angreifer machen (der Admin sperrt sich sonst selbst aus) noch eine Anmeldung
    vortaeuschen (die Adresse gaelte danach als bekannt, ein echter Einbruch von dort
    meldete sich nicht mehr)."""
    lines = [
        "1758800000.1 pve2 sshd[1]: Invalid user x from 192.168.1.20 port 1 from 203.0.113.5 port 4242",
        "1758800001.1 pve2 sshd[2]: Invalid user x Accepted password for root from 198.51.100.1 port 1 from 203.0.113.5 port 4243",
        ("1758800002.1 pve2 sshd[3]: Failed password for invalid user x Connection closed by authenticating user root "
         "192.168.1.21 port 1 from 203.0.113.5 port 4244 ssh2"),
        ("1758800003.1 pve2 sshd-session[4]: Invalid user x]: Accepted password for root from 198.51.100.2 port 1 "
         "from 203.0.113.5 port 4245"),
        "Sep 25 10:00:00 pve2 sshd[5]: Invalid user a b from 203.0.113.5",  # aelteres sshd ohne Port
    ]
    failures, logins = ix.parse_ssh(lines)
    assert logins == []
    assert set(failures) == {"203.0.113.5"} and failures["203.0.113.5"].count == 4
    assert "x from 192.168.1.20 port 1" in failures["203.0.113.5"].users


@pytest.mark.parametrize("bad", ["fe80::1%$(id)", "fe80::1%eth0", "fe80::1%a;id", "fe80::1%`id`", "", " 1.2.3.4"])
def test_ban_command_rejects_scope_ids_and_junk(bad):
    with pytest.raises(ValueError):
        ix.ban_command(bad)


@pytest.mark.parametrize("jail", ["sshd; id", "-x", "", "sshd$(id)", "a" * 65])
def test_ban_command_rejects_bad_jail_names(jail):
    with pytest.raises(ValueError):
        ix.ban_command("203.0.113.9", jail)


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_ban_command_never_executes_anything_in_a_real_shell(tmp_path):
    from nodvard_deck_ext_shield.antivirus import as_root

    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "fail2ban-client"
    stub.write_text("#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done > \"$ARGS_FILE\"\n")
    stub.chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "ARGS_FILE": str(tmp_path / "args")}
    fake_root = "id() { echo 0; }; "  # wie beim Dashboard als root: as_root nimmt den direkten Zweig
    candidates = [
        ("203.0.113.9", "sshd"), ("2001:db8::1", "sshd"),
        ("fe80::1%$(touch${IFS}zz_pwned_marker)", "sshd"), ("fe80::1%`touch${IFS}zz_pwned_marker`", "sshd"),
        ("fe80::1%a;touch${IFS}zz_pwned_marker", "sshd"), ("203.0.113.9", "sshd;touch${IFS}zz_pwned_marker"),
        ("203.0.113.9", "$(touch${IFS}zz_pwned_marker)"),
    ]
    ran = []
    for ip, jail in candidates:
        try:
            cmd = ix.ban_command(ip, jail)
        except ValueError:
            continue
        out = subprocess.run(["sh", "-c", fake_root + as_root(cmd)], cwd=tmp_path, env=env, capture_output=True, text=True,
                             timeout=30, check=False)
        assert out.returncode == 0, out.stderr
        assert (tmp_path / "args").read_text().splitlines() == ["set", jail, "banip", ip]
        ran.append(ip)
    assert not (tmp_path / "zz_pwned_marker").exists()
    assert ran == ["203.0.113.9", "2001:db8::1"]


def test_ban_route_refuses_injected_addresses_before_proposing():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from nodvard_deck_ext_shield.defender_api import build_routers
    from nodvard_sdk import Actor, Host

    proposed = []

    async def _get(host_id):
        return Host(id="h1", name="pve2", display_name="pve2", address="10.0.0.2") if host_id == "h1" else None

    async def _propose(req, **_kwargs):
        proposed.append(req)
        return SimpleNamespace(action_id="a1", status=SimpleNamespace(value="proposed"))

    ctx = SimpleNamespace(api=SimpleNamespace(current_actor=lambda: Actor.user("u1", "anna")), hosts=SimpleNamespace(get=_get),
                          actions=SimpleNamespace(propose=_propose))
    _, manage = build_routers(ctx, SimpleNamespace())
    app = FastAPI()
    app.include_router(manage)
    client = TestClient(app)
    bad = client.post("/defender/hosts/h1/ban", json={"ip": "fe80::1%$(id)"})
    assert bad.status_code == 400 and bad.json()["detail"] == "Ungültige IP-Adresse."
    bad_jail = client.post("/defender/hosts/h1/ban", json={"ip": "203.0.113.9", "jail": "sshd;id"})
    assert bad_jail.status_code == 400 and bad_jail.json()["detail"] == "Ungültiger Jail-Name."
    assert proposed == []
    ok = client.post("/defender/hosts/h1/ban", json={"ip": " 2001:db8::1 ", "unban": True})
    assert ok.status_code == 200, ok.text
    # jail und unban stehen mit im Payload -- daraus baut der Executor den Befehl neu.
    assert proposed[0].payload == {"command": "fail2ban-client set sshd unbanip 2001:db8::1", "ip": "2001:db8::1",
                                   "jail": "sshd", "unban": True}


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell (Git-Bash-sh scheitert an Windows-Pfaden)")
def test_guard_command_runs_in_a_real_shell(tmp_path):
    f = tmp_path / "watched.conf"
    f.write_text("x=1\n")
    out = subprocess.run(["sh", "-c", ix.guard_command([str(f)])], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    snap = ix.parse_guard(out.stdout)
    assert str(f) in snap.files and snap.files[str(f)].size == 4


# Ohne root scheitert `fail2ban-client ping` am Socket-Recht (rc 255) --
# das wurde bisher als "gestoppt" gewertet und loeste taeglich einen Fehlalarm aus.
F2B_NO_ACCESS = "ERROR Permission denied to socket: /var/run/fail2ban/fail2ban.sock, (you must be root)"
F2B_NOT_RUNNING = "ERROR Failed to access socket path: /var/run/fail2ban/fail2ban.sock. Is fail2ban running?"


def _f2b_stub(tmp_path: Path, *, ping_output: str, ping_rc: int) -> dict[str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "fail2ban-client"
    stub.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = ping ]; then\n"
        f"  if [ {ping_rc} -eq 0 ]; then echo 'Server replied: pong'; else echo '{ping_output}' >&2; fi\n"
        f"  exit {ping_rc}\n"
        "fi\n"
        "if [ \"$1\" = status ] && [ -z \"$2\" ]; then printf 'Status\\n|- Number of jail:\\t1\\n`- Jail list:\\tsshd\\n'; exit 0; fi\n"
        "printf 'Status for the jail: %s\\n`- Actions\\n   |- Currently banned:\\t1\\n   `- Banned IP list:\\t203.0.113.9\\n' \"$2\"\n"
    )
    stub.chmod(0o755)
    return {"PATH": f"{bindir}:/usr/bin:/bin"}


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize(("ping_output", "ping_rc", "expected"), [
    (F2B_NO_ACCESS, 255, "noaccess"),
    (F2B_NOT_RUNNING, 255, "stopped"),
    ("", 0, "running"),
])
def test_guard_command_tells_missing_rights_from_a_stopped_fail2ban(tmp_path, ping_output, ping_rc, expected):
    env = _f2b_stub(tmp_path, ping_output=ping_output, ping_rc=ping_rc)
    out = subprocess.run(["sh", "-c", ix.guard_command([])], cwd=tmp_path, env=env, capture_output=True, text=True,
                         timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    snap = ix.parse_guard(out.stdout)
    assert snap.fail2ban == expected
    if expected == "running":
        assert snap.banned == {"203.0.113.9"}
    else:
        assert snap.jails == {}


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_guard_command_ignores_section_markers_smuggled_in_via_a_process_name(tmp_path):
    """Gleiche Fehlerart wie bei der Scan-Marke im Dateinamen: Der Prozessname in `ss -p` kommt vom
    Programm selbst und darf Zeilenumbrueche enthalten ("a\\n@@ssh\\nb"). Ohne Filter fiele daraus eine Zeile
    `@@ssh` in die Ausgabe, die Auswertung finge den Abschnitt neu an, und alle SSH-Fehlversuche
    und Anmeldungen waeren weg (ebenso `@@f2b` fuer Fail2ban)."""
    env = _f2b_stub(tmp_path, ping_output="", ping_rc=0)
    bindir = tmp_path / "bin"
    (bindir / "ss").write_text(
        "#!/bin/sh\n"
        "cat <<'EOF'\n"
        'tcp LISTEN 0 128 0.0.0.0:4444 0.0.0.0:* users:(("a\n'
        "@@ssh\n"
        "@@f2b\n"
        'b",pid=9,fd=3))\n'
        'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n'
        "EOF\n"
    )
    (bindir / "journalctl").write_text(
        "#!/bin/sh\necho '1758800002.100000 pve2 sshd[101]: Failed password for root from 198.51.100.7 port 50000 ssh2'\n"
    )
    for name in ("ss", "journalctl"):
        (bindir / name).chmod(0o755)
    out = subprocess.run(["sh", "-c", ix.guard_command([])], cwd=tmp_path, env=env, capture_output=True, text=True,
                         timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert lines.count("@@ssh") == 1 and lines.count("@@f2b") == 1  # nur die echten Abschnittsmarken
    snap = ix.parse_guard(out.stdout)
    assert set(snap.failures) == {"198.51.100.7"}  # die SSH-Fehlversuche sind noch da
    assert (snap.fail2ban, snap.banned) == ("running", {"203.0.113.9"})  # ebenso Fail2ban
    assert {p.port for p in snap.ports} == {4444, 22}  # der Port des Angreifers bleibt sichtbar


def test_parse_guard_keeps_the_unreadable_fail2ban_state():
    assert ix.parse_guard(_output(f2b="noaccess")).fail2ban == "noaccess"
    assert ix.parse_guard(_output(f2b="stopped")).fail2ban == "stopped"
    assert ix.parse_guard(_output(f2b="kaputt")).fail2ban == "none"


# --- Ablauf ---------------------------------------------------------------------


class _Ctx:
    def __init__(self, sessionmaker, outputs: list[str], settings=None):
        from nodvard_sdk import Host

        self._sm = sessionmaker
        self.outputs = outputs
        self.commands: list[str] = []
        self.audits: list[dict] = []
        self.notes: list = []
        self._settings_value = settings or {"bruteforce_threshold": 2}
        self._host = Host(id="h1", name="pve2", display_name="pve2", address="10.0.0.2")
        self.settings = SimpleNamespace(get=self._settings)
        self.hosts = SimpleNamespace(list=self._list, get=self._get)
        self.exec = SimpleNamespace(run=self._run)
        self.db = SimpleNamespace(session=self._session)
        self.audit = SimpleNamespace(log=self._audit)
        self.notify = SimpleNamespace(send=self._notify)
        self.ws = SimpleNamespace(broadcast=self._ws)

    async def _settings(self):
        return self._settings_value

    async def _list(self, tag=None):
        return [self._host]

    async def _get(self, host_id):
        return self._host if host_id == "h1" else None

    async def _run(self, host, command, timeout_s=60):
        self.commands.append(command)
        out = self.outputs[0] if len(self.outputs) == 1 else self.outputs.pop(0)
        return SimpleNamespace(exit_code=0, stdout=out, stderr="", duration_ms=5)

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
    from nodvard_deck_ext_shield.models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_warning_events_belong_to_their_host_so_a_maintenance_window_can_silence_them():
    """Ein neuer Port (Warnung) nach einem Dienst-Neustart im Wartungsfenster
    darf stumm bleiben -- die Meldung traegt die Host-ID des Servers."""
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    base_ports = 'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))'
    second = _output(ports=base_ports + '\ntcp LISTEN 0 128 0.0.0.0:8080 0.0.0.0:* users:(("app",pid=66,fd=3))')
    engine, sm = await _engine()
    ctx = _Ctx(sm, [_output(ports=base_ports), second])
    guard = Guard(ctx, Defender(ctx))

    await guard.inspect(ctx._host)
    assert (await guard.inspect(ctx._host))["events"] == 1
    assert len(ctx.notes) == 1 and ctx.notes[0].severity.value == "warning"
    assert ctx.notes[0].payload["host_id"] == "h1"
    await engine.dispose()


@pytest.mark.asyncio
async def test_first_look_only_learns_then_changes_raise_events_once():
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    base_ports = 'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))'
    first = _output(ports=base_ports, files=f"{SHA_A} 644 root 1 10 - /etc/passwd",
                    ssh="1758800100.1 pve2 sshd[1]: Accepted publickey for root from 192.168.1.20 port 1 ssh2")
    second = _output(
        ports=base_ports + '\ntcp LISTEN 0 128 0.0.0.0:4444 0.0.0.0:* users:(("nc",pid=66,fd=3))',
        files=f"{SHA_B} 644 root 2 11 - /etc/passwd",
        ssh="\n".join([
            "1758800100.1 pve2 sshd[1]: Accepted publickey for root from 192.168.1.20 port 1 ssh2",
            "1758800200.1 pve2 sshd[2]: Accepted password for root from 45.66.77.88 port 2 ssh2",
            "1758800201.1 pve2 sshd[3]: Failed password for root from 203.0.113.9 port 3 ssh2",
            "1758800202.1 pve2 sshd[4]: Failed password for root from 203.0.113.9 port 4 ssh2",
        ]),
    )
    engine, sm = await _engine()
    ctx = _Ctx(sm, [first, second, second])
    guard = Guard(ctx, Defender(ctx))

    assert (await guard.inspect(ctx._host))["events"] == 0  # erster Blick: nur merken
    assert (await guard.inspect(ctx._host))["events"] == 4
    kinds = {e["kind"]: e for e in await guard.list_events()}
    assert set(kinds) == {"new_port", "new_login_ip", "bruteforce", "file_changed"}
    assert kinds["new_port"]["title"] == "Neuer offener Port: 4444/tcp (nc)"
    assert kinds["bruteforce"]["severity"] == "critical"  # ohne laufendes Fail2ban
    assert kinds["new_login_ip"]["detail"]["logins"][0]["ip"] == "45.66.77.88"
    assert ctx.notes and "4 Sicherheitsereignisse auf pve2" == ctx.notes[0].title
    # Kritisches (hier: Angriff auf SSH ohne Fail2ban) traegt keinen Host-Bezug und wird
    # deshalb nie von einem Wartungsfenster stumm geschaltet.
    assert ctx.notes[0].severity.value == "critical" and "host_id" not in ctx.notes[0].payload

    assert (await guard.inspect(ctx._host))["events"] == 0  # dieselben Befunde melden nicht erneut

    o = await guard.overview()
    view = o["hosts"][0]["view"]
    assert [p["port"] for p in view["ports"] if p["new"]] == [4444]
    assert view["files_pending"] == ["/etc/passwd"] and view["failed_24h"] == 2
    assert o["summary"]["open_events"] == 4

    # Bestaetigen uebernimmt den neuen Stand
    assert await guard.acknowledge(kinds["new_port"]["id"])
    assert await guard.acknowledge(kinds["file_changed"]["id"])
    view = (await guard.overview())["hosts"][0]["view"]
    assert view["files_pending"] == []
    assert (await guard.inspect(ctx._host))["events"] == 0
    view = (await guard.overview())["hosts"][0]["view"]
    assert not any(p["new"] for p in view["ports"])
    assert "shield.guard_acknowledged" in [a["action"] for a in ctx.audits]
    await engine.dispose()


@pytest.mark.asyncio
async def test_package_updates_to_binaries_are_accepted_silently():
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    engine, sm = await _engine()
    ctx = _Ctx(sm, [
        _output(bins=f"{SHA_A} 755 root 1 10 pkg-ok /usr/bin/sudo"),
        _output(bins=f"{SHA_B} 755 root 2 10 pkg-ok /usr/bin/sudo"),
    ])
    guard = Guard(ctx, Defender(ctx))
    await guard.inspect(ctx._host)
    assert (await guard.inspect(ctx._host))["events"] == 0
    assert ctx.notes == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_unreadable_sshd_config_is_not_shown_as_safe():
    """Ohne root liefert `sshd -T` nichts -- die Oberflaeche zeigte trotzdem
    gruen "Sicher eingestellt", obwohl gar nichts geprueft wurde."""
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    no_root = _output().replace("@@uid\n0\n", "@@uid\n1000\n").replace("@@ports", "@@sshd\n\n@@ports")
    safe = _output().replace("@@ports", "@@sshd\nport 22\npermitrootlogin prohibit-password\npasswordauthentication no\n@@ports")
    engine, sm = await _engine()
    ctx = _Ctx(sm, [no_root, safe])
    guard = Guard(ctx, Defender(ctx))

    await guard.inspect(ctx._host)
    view = (await guard.overview())["hosts"][0]["view"]
    assert view["is_root"] is False and view["ssh_findings"] is None

    await guard.inspect(ctx._host)
    view = (await guard.overview())["hosts"][0]["view"]
    assert view["ssh_findings"] == [] and view["ssh_port"] == "22"
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(("f2b", "bruteforce_severity", "f2b_down"), [
    ("noaccess", "warning", False),
    ("stopped", "critical", True),
    ("none", "critical", False),
    ("running", "warning", False),
])
async def test_unreadable_fail2ban_raises_no_false_alarm(f2b, bruteforce_severity, f2b_down):
    """Ohne root ist der Fail2ban-Zustand nur unbekannt -- kein taegliches
    "Fail2ban laeuft nicht" und kein "kritisch" fuer Fehlversuche deswegen."""
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    attempts = "\n".join(f"17588002{i:02d}.1 pve2 sshd[{i}]: Failed password for root from 203.0.113.9 port {i} ssh2" for i in range(3))
    engine, sm = await _engine()
    ctx = _Ctx(sm, [_output(f2b=f2b), _output(f2b=f2b, ssh=attempts)])
    guard = Guard(ctx, Defender(ctx))

    await guard.inspect(ctx._host)
    await guard.inspect(ctx._host)
    kinds = {e["kind"]: e for e in await guard.list_events()}
    assert kinds["bruteforce"]["severity"] == bruteforce_severity
    assert ("fail2ban_down" in kinds) is f2b_down
    o = await guard.overview()
    assert o["hosts"][0]["view"]["fail2ban"] == f2b
    assert o["summary"]["fail2ban_running"] == (1 if f2b == "running" else 0)
    assert o["summary"]["fail2ban_unreadable"] == (1 if f2b == "noaccess" else 0)
    await engine.dispose()


def test_briefing_includes_guard_line():
    from nodvard_deck_ext_shield.defender import build_briefing

    overview = {"summary": {"score": 90, "protected": 1, "hosts": 1, "open_threats": 0, "quarantined": 0, "findings_30d": 0}, "hosts": []}
    _title, body, level = build_briefing(overview, [], None, ("Einbruchschutz: 12 fehlgeschlagene SSH-Anmeldungen", ["1 neue offene Port(s) prüfen"]))
    assert "Einbruchschutz: 12" in body and "Zu tun: 1 neue offene Port(s) prüfen" in body and level == "warning"


def test_security_widget_combines_updates_and_guard():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from nodvard_deck_ext_shield.defender_api import build_routers

    class _Upd:
        async def overview(self):
            return {"summary": {"checked": 2, "hosts": 2, "up_to_date": 1, "packages": 3, "security": 2, "reboot": 1}}

    class _Guard:
        async def overview(self):
            return {"hosts": [{"view": {"failed_24h": 57}}],
                    "summary": {"open_events": 1, "failed_24h": 57, "banned": 1, "fail2ban_running": 1, "fail2ban_unreadable": 1, "hosts": 2}}

        async def list_events(self, limit=4):
            return [{"title": "Neuer offener Port: 4444/tcp (nc)", "host_name": "pve2", "kind_label": "Neuer offener Port", "severity": "warning"}]

    ctx = SimpleNamespace(api=SimpleNamespace(current_actor=lambda: None))
    read, _ = build_routers(ctx, object(), _Upd(), _Guard())
    app = FastAPI()
    app.include_router(read)
    rows = TestClient(app).get("/defender/widgets/security").json()["data"]
    assert rows[0] == {"title": "Updates", "label": "2 Sicherheit", "tone": "danger", "subtitle": "1/2 Server aktuell · Neustart nötig: 1"}
    assert rows[1]["title"] == "Einbruchschutz" and rows[1]["label"] == "1 Ereignis(se)"
    assert "57 SSH-Fehlversuche" in rows[1]["subtitle"]
    assert rows[1]["subtitle"].endswith("Fail2ban 1/2 (1 nicht lesbar)")  # ohne root nur unbekannt
    assert rows[2] == {"title": "Neuer offener Port: 4444/tcp (nc)", "subtitle": "pve2", "label": "Neuer offener Port", "tone": "warn"}


def test_sshd_config_findings():
    cfg, findings = ix.check_sshd([
        "port 22", "permitrootlogin yes", "passwordauthentication yes", "permitemptypasswords no",
        "pubkeyauthentication yes", "maxauthtries 10", "x11forwarding yes",
    ])
    assert cfg["port"] == "22"
    by_key = {f.key: f for f in findings}
    assert by_key["permitrootlogin"].severity == "critical"
    assert by_key["passwordauthentication"].severity == "warning"
    assert by_key["maxauthtries"].advice == "MaxAuthTries 3" and "x11forwarding" in by_key

    _cfg, safe = ix.check_sshd(["port 2222", "permitrootlogin prohibit-password", "passwordauthentication no",
                                 "kbdinteractiveauthentication no", "maxauthtries 3", "x11forwarding no"])
    assert safe == []
    assert ix.check_sshd([]) == ({}, [])
    snap = ix.parse_guard("@@uid\n0\n@@sshd\npermitrootlogin yes\npasswordauthentication no\n@@ports\n@@end\n")
    assert [f.severity for f in snap.ssh_findings] == ["info"]


# --- Zufaellige Ports (NFS-Server) -----------------------------------------


def _ss(proto: str, port: int, proc: str | None, addr: str = "0.0.0.0") -> str:
    users = f' users:(("{proc}",pid=1{port % 100},fd=4))' if proc else ""
    state = "LISTEN" if proto == "tcp" else "UNCONN"
    return f"{proto}   {state} 0      64           {addr}:{port}        0.0.0.0:*{users}"


def _nfs_server_ports(mountd_udp, mountd_tcp, statd, lockd, avahi, statd_udp=723) -> str:
    """So sieht `ss -tulnp` auf dem NFS-Server (Raspberry Pi) aus -- nach jedem Start andere Ports."""
    lines = [_ss("tcp", 22, "sshd"), _ss("tcp", 2049, None), _ss("tcp", 111, "rpcbind"), _ss("udp", 5353, "avahi-daemon")]
    lines += [_ss("udp", p, "rpc.mountd") for p in mountd_udp] + [_ss("udp", p, "rpc.mountd", "[::]") for p in mountd_udp]
    lines += [_ss("tcp", p, "rpc.mountd") for p in mountd_tcp]
    lines += [_ss("udp", statd_udp, "rpc.statd")] + [_ss("tcp", p, "rpc.statd") for p in statd]
    lines += [_ss("tcp", p, None) for p in lockd] + [_ss("udp", p, "avahi-daemon") for p in avahi]
    return "\n".join(lines)


BOOT_1 = _nfs_server_ports(
    [58094, 37692, 44294, 52320, 52424, 55807], [37767, 54617, 60843, 53249, 59599, 48301],
    [57590, 50491, 42071, 40503], [39259, 46145, 53530, 50468], [57581, 44628],
)
BOOT_2 = _nfs_server_ports(
    [40001, 41002, 42003, 43004, 44005, 45006], [46007, 47008, 48009, 49010, 50011, 51012],
    [52013, 53014, 54015, 55016], [56017, 57018, 58019, 59020], [60021, 33022], statd_udp=801,
)


def test_random_ports_of_nfs_and_kernel_share_one_key_each():
    ports = ix.parse_ports(BOOT_1.splitlines())
    keys = {p.key for p in ports}
    assert keys == {
        "tcp/22/sshd", "tcp/111/rpcbind", "tcp/2049/?", "udp/5353/avahi-daemon", "udp/dyn/rpc.statd",
        "udp/dyn/rpc.mountd", "tcp/dyn/rpc.mountd", "tcp/dyn/rpc.statd", "tcp/dyn/-", "udp/dyn/avahi-daemon",
    }
    by_key = {p.key: p for p in ports}
    assert by_key["udp/dyn/rpc.mountd"].count == 6  # IPv4 und IPv6 desselben Ports zaehlen einmal
    assert by_key["tcp/dyn/-"].count == 4 and by_key["tcp/dyn/-"].label == "wechselnde Ports/tcp (ohne Programm)"
    assert by_key["tcp/22/sshd"].label == "22/tcp (sshd)" and not by_key["tcp/22/sshd"].dynamic
    # Nach dem "Neustart" mit ganz anderen Ports entstehen dieselben Schluessel
    assert {p.key for p in ix.parse_ports(BOOT_2.splitlines())} == keys


def test_rpc_port_in_reserved_range_changes_per_start_but_fixed_ports_stay_visible():
    # rpc.statd waehlt seinen UDP-Port zuerst im Bereich 600-1023: jeder Start eine andere Nummer
    assert {p.key for p in ix.parse_ports([_ss("udp", 723, "rpc.statd")])} == {"udp/dyn/rpc.statd"}
    assert {p.key for p in ix.parse_ports([_ss("udp", 801, "rpc.statd")])} == {"udp/dyn/rpc.statd"}
    # feste Ports (rpcbind 111, SSH 22) und fremde Programme im niedrigen Bereich bleiben einzeln
    keys = {p.key for p in ix.parse_ports([_ss("tcp", 111, "rpcbind"), _ss("udp", 111, "rpcbind"), _ss("tcp", 700, "nc"),
                                           _ss("tcp", 4444, "rpc.mountd")])}
    assert keys == {"tcp/111/rpcbind", "udp/111/rpcbind", "tcp/700/nc", "tcp/4444/rpc.mountd"}
    # Ein alter Stand mit der Portnummer deckt den Sammeleintrag ab
    dyn = ix.parse_ports([_ss("udp", 801, "rpc.statd")])[0]
    assert ix.port_is_known(dyn, {"udp/723/rpc.statd"})
    assert not ix.port_is_known(dyn, {"udp/723/nc"})


@pytest.mark.parametrize("proc", ["tailscaled", "rpcbind", "avahi-daemon", "dhclient", "dhcpcd", "chronyd", "Tailscaled"])
def test_builtin_programs_with_changing_udp_ports_collapse(proc):
    first = ix.parse_ports([_ss("udp", 41641, proc), _ss("udp", 50123, proc, "[::]")])
    second = ix.parse_ports([_ss("udp", 36001, proc)])
    assert [p.dynamic for p in first] == [True] and [p.key for p in first] == [p.key for p in second]
    assert first[0].count == 2


@pytest.mark.parametrize("proc", ["chronyd", "dhclient", "dhcpcd", "avahi-daemon", "rpcbind", "tailscaled"])
@pytest.mark.parametrize("port", [4444, 40000])
def test_fake_builtin_name_on_tcp_port_is_reported(proc, port):
    # Den Programmnamen kann jeder setzen (`exec -a chronyd`): ein TCP-Dienst unter diesem Namen
    # bleibt einzeln sichtbar und meldet als neuer Port.
    ports = ix.parse_ports([_ss("tcp", port, proc)])
    assert [(p.key, p.dynamic) for p in ports] == [(f"tcp/{port}/{proc}", False)]
    assert not ix.port_is_known(ports[0], {"tcp/dyn/chronyd", f"tcp/{port + 1}/{proc}"})
    # Auch wenn der Name (alter gespeicherter Wert) zusaetzlich in der Einstellung steht
    assert not ix.parse_ports([_ss("tcp", port, proc)], dynamic_processes=[proc, "rpc.statd"])[0].dynamic


def test_tailscaled_tcp_counts_as_changing_only_on_its_tailscale_address():
    def dyn(addr):
        return ix.parse_ports([_ss("tcp", 45678, "tailscaled", addr)])[0].dynamic

    assert dyn("100.64.0.7") and dyn("100.127.255.1") and dyn("[fd7a:115c:a1e0::5]")
    assert not dyn("0.0.0.0") and not dyn("[::]") and not dyn("192.168.2.10") and not dyn("100.128.0.1")
    # NFS-Hilfsdienste oeffnen wirklich zufaellige TCP-Ports
    assert ix.parse_ports([_ss("tcp", 45678, "rpc.statd")])[0].dynamic


def test_setting_adds_to_builtin_list_instead_of_replacing_it():
    # Ein frueher gespeicherter Wert (nur die alten drei) darf tailscaled nicht wieder ausschliessen
    old_saved = ["rpc.mountd", "rpc.statd", "avahi-daemon"]
    assert ix.parse_ports([_ss("udp", 41641, "tailscaled")], dynamic_processes=old_saved)[0].dynamic
    assert ix.parse_ports([_ss("udp", 41641, "tailscaled")], dynamic_processes=[])[0].dynamic
    assert ix.parse_ports([_ss("tcp", 50000, "mein-dienst")], dynamic_processes=[])[0].dynamic is False
    assert ix.parse_ports([_ss("tcp", 50000, "mein-dienst")], dynamic_processes=["Mein-Dienst"])[0].dynamic


def test_without_root_random_udp_ports_collapse_but_tcp_stays_visible():
    unprivileged = "@@uid\n1000\n"
    lines = [_ss("tcp", 22, None), _ss("udp", 41641, None), _ss("udp", 52000, None), _ss("udp", 5353, None),
             _ss("tcp", 45000, None), _ss("tcp", 8081, None)]
    snap = ix.parse_guard(unprivileged + _output(ports="\n".join(lines))[len("@@uid\n0\n"):])
    keys = sorted(p.key for p in snap.ports)
    assert keys == ["tcp/22/?", "tcp/45000/?", "tcp/8081/?", "udp/5353/?", "udp/dyn/?"]
    collective = next(p for p in snap.ports if p.dynamic)
    assert collective.count == 2 and collective.label == "wechselnde Ports/udp (Programm nicht lesbar)"
    # Alte Staende mit einzelnen "udp/<Port>/?" decken den Sammeleintrag ab
    assert ix.port_is_known(collective, {"udp/41641/?"})


@pytest.mark.asyncio
async def test_port_rows_say_when_the_program_is_unreadable_because_there_is_no_root():
    """Der Sammeleintrag "udp/dyn/?" ohne root steht fuer Programme, die nicht lesbar sind -- nicht fuer
    Kernel-Sockets. Die Oberflaeche braucht dafuer `unreadable` in der Port-Zeile und im Ereignis."""
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    def as_user(text: str) -> str:
        return text.replace("@@uid\n0\n", "@@uid\n1000\n")

    base = [_ss("tcp", 22, None)]
    engine, sm = await _engine()
    ctx = _Ctx(sm, [as_user(_output(ports="\n".join(base))), as_user(_output(ports="\n".join([*base, _ss("udp", 41641, None)])))])
    guard = Guard(ctx, Defender(ctx))
    assert (await guard.inspect(ctx._host))["events"] == 0
    assert (await guard.inspect(ctx._host))["events"] == 1
    [event] = await guard.list_events()
    [port] = event["detail"]["ports"]
    assert port["key"] == "udp/dyn/?" and port["dynamic"] is True and port["unreadable"] is True
    rows = {p["key"]: p for p in (await guard.overview())["hosts"][0]["view"]["ports"]}
    assert rows["udp/dyn/?"]["unreadable"] is True and rows["tcp/22/?"]["unreadable"] is False
    await engine.dispose()

    # Mit root sind es Kernel-Sockets (ohne Prozess): nicht "unreadable"
    engine, sm = await _engine()
    ctx = _Ctx(sm, [_output(ports="\n".join(base)), _output(ports="\n".join([*base, _ss("udp", 41641, None)]))])
    guard = Guard(ctx, Defender(ctx))
    await guard.inspect(ctx._host)
    await guard.inspect(ctx._host)
    rows = {p["key"]: p for p in (await guard.overview())["hosts"][0]["view"]["ports"]}
    assert rows["udp/dyn/-"]["dynamic"] is True and rows["udp/dyn/-"]["unreadable"] is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_random_udp_ports_of_tailscale_and_rpc_raise_no_alarm_but_new_web_server_does():
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    def host(ts_udp, ts_tcp, statd_udp, mountd, extra=""):
        lines = [_ss("tcp", 22, "sshd"), _ss("udp", ts_udp, "tailscaled"), _ss("udp", ts_udp, "tailscaled", "[::]"),
                 _ss("tcp", ts_tcp, "tailscaled", "100.64.0.7"), _ss("udp", statd_udp, "rpc.statd"),
                 _ss("udp", mountd, "rpc.mountd"), _ss("tcp", mountd + 1, "rpc.mountd")]
        return _output(ports="\n".join(lines) + extra)

    engine, sm = await _engine()
    web = "\n" + _ss("tcp", 8081, "python3")
    ctx = _Ctx(sm, [host(41641, 34567, 723, 40001), host(55102, 41234, 801, 52000), host(36000, 47000, 650, 60000),
                    host(36000, 47000, 650, 60000, web), host(36000, 47000, 650, 60000, web)])
    guard = Guard(ctx, Defender(ctx))
    assert (await guard.inspect(ctx._host))["events"] == 0
    assert (await guard.inspect(ctx._host))["events"] == 0
    assert (await guard.inspect(ctx._host))["events"] == 0
    assert (await guard.inspect(ctx._host))["events"] == 1
    assert (await guard.list_events())[0]["title"] == "Neuer offener Port: 8081/tcp (python3)"
    assert (await guard.inspect(ctx._host))["events"] == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_backdoor_named_like_builtin_program_on_tcp_is_reported():
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    base = [_ss("tcp", 22, "sshd"), _ss("udp", 41641, "chronyd")]
    engine, sm = await _engine()
    ctx = _Ctx(sm, [_output(ports="\n".join(base)), _output(ports="\n".join([*base, _ss("tcp", 4444, "chronyd")])),
                    _output(ports="\n".join([*base, _ss("tcp", 40000, "chronyd")]))])
    guard = Guard(ctx, Defender(ctx))
    assert (await guard.inspect(ctx._host))["events"] == 0
    assert (await guard.inspect(ctx._host))["events"] == 1
    assert (await guard.list_events())[0]["title"] == "Neuer offener Port: 4444/tcp (chronyd)"
    assert (await guard.inspect(ctx._host))["events"] == 1
    assert (await guard.list_events())[0]["title"] == "Neuer offener Port: 40000/tcp (chronyd)"
    await engine.dispose()


def test_dynamic_range_and_root_decide_what_is_random():
    lines = [_ss("tcp", 40000, "rpc.mountd"), _ss("tcp", 45000, None), _ss("tcp", 4444, "rpc.mountd")]
    # Untergrenze aus ip_local_port_range: 40000 liegt darunter, 45000 darueber
    snap = ix.parse_guard(_output(ports="\n".join(lines)).replace("@@ports", "@@range\n49152\t65535\n@@ports"))
    assert snap.dynamic_min == 49152 and not any(p.dynamic for p in snap.ports)
    snap = ix.parse_guard(_output(ports="\n".join(lines)).replace("@@ports", "@@range\n40000\t60999\n@@ports"))
    assert snap.dynamic_min == 40000
    assert {p.key for p in snap.ports} == {"tcp/dyn/rpc.mountd", "tcp/dyn/-", "tcp/4444/rpc.mountd"}
    # Ohne Angabe (oder Unsinn) gilt 32768
    assert ix.parse_guard(_output()).dynamic_min == 32768
    assert ix.parse_port_range(["1 2"]) == 32768
    # Eine zu niedrige Untergrenze (1024) wuerde feste Kernel-Ports wie 2049 verschlucken
    assert ix.parse_port_range(["1024 65535"]) == 32768
    # Ein Eintrag, der kein Text ist, legt den Guard nicht lahm
    assert ix.parse_ports([_ss("tcp", 45000, "rpc.mountd")], dynamic_processes=[None, 5, "rpc.mountd"])[0].dynamic
    # Ohne root sind Programme namenlos -- dann darf nichts hinter "tcp/dyn/-" verschwinden
    unprivileged = _output(ports=_ss("tcp", 45000, None)).replace("@@uid\n0\n", "@@uid\n1000\n")
    assert [p.key for p in ix.parse_guard(unprivileged).ports] == ["tcp/45000/?"]


def test_legacy_entry_below_range_does_not_cover_collective_entry():
    dyn = next(p for p in ix.parse_ports([_ss("udp", 40000, "avahi-daemon")]) if p.dynamic)
    assert ix.port_is_known(dyn, {"udp/40000/avahi-daemon"})
    assert not ix.port_is_known(dyn, {"udp/5353/avahi-daemon"})


def test_guard_command_reads_the_dynamic_port_range():
    cmd = ix.guard_command()
    assert "@@range" in cmd and cmd.index("@@range") < cmd.index("@@ports")
    assert "/proc/sys/net/ipv4/ip_local_port_range" in cmd


@pytest.mark.asyncio
async def test_reboot_with_new_random_ports_raises_no_event_but_new_program_does():
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    fixed_new = BOOT_2 + "\n" + _ss("tcp", 4444, "nc")
    random_new = BOOT_2 + "\n" + _ss("tcp", 41000, "nc")
    engine, sm = await _engine()
    ctx = _Ctx(sm, [_output(ports=BOOT_1), _output(ports=BOOT_2), _output(ports=BOOT_1), _output(ports=fixed_new),
                    _output(ports=fixed_new), _output(ports=random_new)])
    guard = Guard(ctx, Defender(ctx))

    assert (await guard.inspect(ctx._host))["events"] == 0  # erster Blick: nur merken
    assert (await guard.inspect(ctx._host))["events"] == 0  # "Neustart": andere Zufallsports
    assert (await guard.inspect(ctx._host))["events"] == 0  # und noch einmal
    assert not any(p["new"] for p in (await guard.overview())["hosts"][0]["view"]["ports"])

    assert (await guard.inspect(ctx._host))["events"] == 1  # neues Programm auf festem Port
    ev = (await guard.list_events())[0]
    assert ev["title"] == "Neuer offener Port: 4444/tcp (nc)"
    assert (await guard.inspect(ctx._host))["events"] == 0

    # Ein unbekanntes Programm in Zufallsbereich meldet auch (es steht nicht in der Liste)
    assert (await guard.inspect(ctx._host))["events"] == 1
    assert (await guard.list_events())[0]["title"] == "Neuer offener Port: 41000/tcp (nc)"

    view = (await guard.overview())["hosts"][0]["view"]
    dyn = {p["key"]: p for p in view["ports"] if p["dynamic"]}
    assert dyn["udp/dyn/rpc.mountd"]["count"] == 6 and not dyn["udp/dyn/rpc.mountd"]["new"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_setting_extends_the_list_and_old_baselines_still_match():
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard
    from nodvard_deck_ext_shield.patching import save_baseline

    engine, sm = await _engine()
    # Stand von vor dem Update: je Zufallsport ein Eintrag
    old_known = ["tcp/22/sshd", "tcp/2049/?", "tcp/111/rpcbind", "udp/5353/avahi-daemon", "udp/723/rpc.statd",
                 "udp/58094/rpc.mountd", "tcp/37767/rpc.mountd", "tcp/57590/rpc.statd", "tcp/39259/?", "udp/57581/avahi-daemon"]
    extra = _ss("tcp", 50123, "mein-dienst")
    ctx = _Ctx(sm, [_output(ports=BOOT_2), _output(ports=BOOT_2 + "\n" + extra), _output(ports=BOOT_2 + "\n" + extra)])
    guard = Guard(ctx, Defender(ctx))
    await save_baseline(ctx, "h1", "ports", {"known": old_known, "alerted": []})

    assert (await guard.inspect(ctx._host))["events"] == 0  # alte Eintraege decken die neuen Sammelschluessel ab
    assert (await guard.inspect(ctx._host))["events"] == 1  # mein-dienst ist noch nicht in der Liste
    assert (await guard.list_events())[0]["title"] == "Neuer offener Port: 50123/tcp (mein-dienst)"

    await engine.dispose()

    # Mit der Einstellung zaehlt auch mein-dienst als "wechselnde Ports": einmal melden, dann derselbe Eintrag
    engine, sm = await _engine()
    dyn = ["rpc.mountd", "rpc.statd", "avahi-daemon", "mein-dienst"]
    ctx2 = _Ctx(sm, [_output(ports=BOOT_2), _output(ports=BOOT_2 + "\n" + _ss("tcp", 50123, "mein-dienst")),
                     _output(ports=BOOT_2 + "\n" + _ss("tcp", 51999, "mein-dienst"))],
                settings={"bruteforce_threshold": 2, "guard_dynamic_processes": dyn})
    guard2 = Guard(ctx2, Defender(ctx2))
    assert (await guard2.inspect(ctx2._host))["events"] == 0
    assert (await guard2.inspect(ctx2._host))["events"] == 1
    assert (await guard2.list_events())[0]["title"] == "Neuer offener Port: wechselnde Ports/tcp (mein-dienst)"
    assert await guard2.acknowledge((await guard2.list_events())[0]["id"])
    assert (await guard2.inspect(ctx2._host))["events"] == 0  # anderer Zufallsport, derselbe Eintrag
    await engine.dispose()


@pytest.mark.parametrize("sep", ["\u2028", "\u0085", "\x0c", "\u2029", "\x1c"])
def test_sections_split_only_at_newline(sep):
    """Ein Name mit einem Unicode-Zeilentrenner darf keine Abschnittsmarke vortaeuschen (grep trennt nur an \\n)."""
    out = f"@@ports\ntcp LISTEN 0 0 0.0.0.0:22 users:((\"x{sep}@@ssh{sep}y\",pid=1))\n@@end\n"
    sections = ix._sections(out)
    assert "ssh" not in sections
    assert any(":22" in line for line in sections["ports"])
