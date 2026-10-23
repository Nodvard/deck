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
        self._settings_value = settings or {"bruteforce_threshold": 3}
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
            "1758800203.1 pve2 sshd[5]: Failed password for root from 203.0.113.9 port 5 ssh2",
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
    assert view["files_pending"] == ["/etc/passwd"] and view["failed_24h"] == 3
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


FILES_BEFORE = f"{SHA_A} 644 root 1 10 - /etc/passwd\n{SHA_A} 640 root 1 10 - /etc/shadow"
PASSWD_CHANGED = f"{SHA_B} 644 root 2 11 - /etc/passwd\n{SHA_A} 640 root 1 10 - /etc/shadow"
BOTH_CHANGED = f"{SHA_B} 644 root 2 11 - /etc/passwd\n{SHA_B} 640 root 2 11 - /etc/shadow"


async def _two_file_events(ctx):
    """Zwei offene Ereignisse zu geänderten Dateien auf demselben Server."""
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    ctx.outputs[:] = [_output(files=FILES_BEFORE), _output(files=PASSWD_CHANGED), _output(files=BOTH_CHANGED)]
    guard = Guard(ctx, Defender(ctx))
    for _ in range(3):
        await guard.inspect(ctx._host)
    events = sorted(await guard.list_events(), key=lambda e: e["title"])
    assert [e["title"] for e in events] == ["Wichtige Datei geändert: /etc/passwd", "Wichtige Datei geändert: /etc/shadow"]
    return guard, events


async def _files(ctx):
    from nodvard_deck_ext_shield.patching import load_baseline

    return await load_baseline(ctx, "h1", "files"), await load_baseline(ctx, "h1", "guard")


@pytest.mark.asyncio
async def test_a_cut_off_answer_raises_no_file_alarm():
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import GUARD_INCOMPLETE, Guard

    whole = _output(files=FILES_BEFORE)
    cut = whole[: whole.index("/etc/passwd\n") + len("/etc/passwd\n")]
    engine, sm = await _engine()
    ctx = _Ctx(sm, [whole, cut])
    guard = Guard(ctx, Defender(ctx))

    await guard.inspect(ctx._host)
    assert await guard.inspect(ctx._host) == {"error": GUARD_INCOMPLETE}
    files, view = await _files(ctx)
    assert await guard.list_events() == [] and not ctx.notes
    assert set(files["accepted"]) == {"/etc/passwd", "/etc/shadow"} and view["error"] == GUARD_INCOMPLETE
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [OSError("timeout"), "@@uid\n0\n@@ssh\n"], ids=["nicht-erreichbar", "abgebrochen"])
async def test_acknowledging_a_file_change_keeps_it_open_when_the_server_does_not_deliver(reply):
    from nodvard_deck_ext_shield.guard import GuardUnavailable

    engine, sm = await _engine()
    ctx = _Ctx(sm, [])
    guard, events = await _two_file_events(ctx)
    before = await _files(ctx)

    async def run(host, command, timeout_s=60):
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(exit_code=0, stdout=reply, stderr="", duration_ms=5)

    ctx.exec.run = run
    with pytest.raises(GuardUnavailable, match="Das Ereignis bleibt offen"):
        await guard.acknowledge(events[0]["id"])
    assert len(await guard.list_events()) == 2
    assert await _files(ctx) == before  # nichts aus dem bestätigten Stand entfernt
    calls = []
    ctx.exec.run = lambda host, command, timeout_s=60: calls.append(command) or run(host, command, timeout_s)
    assert await guard.acknowledge_all() == (0, 2)
    assert len(calls) == 1  # einmal warten reicht
    await engine.dispose()


@pytest.mark.asyncio
async def test_acknowledging_a_file_change_of_a_removed_server_still_works():
    engine, sm = await _engine()
    ctx = _Ctx(sm, [])
    guard, events = await _two_file_events(ctx)
    ctx.hosts.get = lambda host_id: _none()
    assert await guard.acknowledge(events[0]["id"])
    await engine.dispose()


async def _none():
    return None


@pytest.mark.asyncio
async def test_two_acknowledgements_at_once_both_stick():
    import asyncio

    engine, sm = await _engine()
    ctx = _Ctx(sm, [])
    guard, events = await _two_file_events(ctx)
    arrived, both = [], asyncio.Event()

    async def run(host, command, timeout_s=60):
        # Beide Bestätigungen warten gleichzeitig auf den Server.
        arrived.append(command)
        if len(arrived) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 2)
        return SimpleNamespace(exit_code=0, stdout=_output(files=BOTH_CHANGED), stderr="", duration_ms=5)

    ctx.exec.run = run
    assert await asyncio.gather(*(guard.acknowledge(e["id"]) for e in events)) == [True, True]
    files, view = await _files(ctx)
    assert files["alerted"] == {} and view["files_pending"] == []
    assert {p: e["sha256"] for p, e in files["accepted"].items()} == {"/etc/passwd": SHA_B, "/etc/shadow": SHA_B}
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_check_during_an_acknowledgement_keeps_what_it_found():
    import asyncio

    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    group_changed = f"{SHA_B} 644 root 2 11 - /etc/passwd\n{SHA_B} 644 root 2 11 - /etc/group"
    engine, sm = await _engine()
    ctx = _Ctx(sm, [_output(files=f"{SHA_A} 644 root 1 10 - /etc/passwd\n{SHA_A} 644 root 1 10 - /etc/group"),
                    _output(files=f"{SHA_B} 644 root 2 11 - /etc/passwd\n{SHA_A} 644 root 1 10 - /etc/group")])
    guard = Guard(ctx, Defender(ctx))
    await guard.inspect(ctx._host)
    await guard.inspect(ctx._host)
    [passwd] = await guard.list_events()
    in_ack, release = asyncio.Event(), asyncio.Event()

    async def run(host, command, timeout_s=60):
        if not in_ack.is_set():
            in_ack.set()
            await asyncio.wait_for(release.wait(), 2)
        return SimpleNamespace(exit_code=0, stdout=_output(files=group_changed), stderr="", duration_ms=5)

    ctx.exec.run = run
    ack = asyncio.ensure_future(guard.acknowledge(passwd["id"]))
    await in_ack.wait()
    assert (await guard.inspect(ctx._host))["events"] == 1  # /etc/group geändert, während der Server antwortet
    release.set()
    assert await ack
    files, view = await _files(ctx)
    assert files["accepted"]["/etc/passwd"]["sha256"] == SHA_B
    assert files["alerted"] == {"/etc/group": f"{SHA_B}644root"} and view["files_pending"] == ["/etc/group"]
    assert (await guard.inspect(ctx._host))["events"] == 0  # nicht noch einmal gemeldet
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_check_waits_while_an_acknowledgement_writes():
    import asyncio

    engine, sm = await _engine()
    ctx = _Ctx(sm, [])
    guard, _events = await _two_file_events(ctx)
    before = await _files(ctx)
    lock = guard._lock("h1")
    await lock.acquire()
    check = asyncio.ensure_future(guard.inspect(ctx._host))
    done, _ = await asyncio.wait({check}, timeout=0.3)
    assert not done and await _files(ctx) == before
    lock.release()
    await check
    await engine.dispose()


@pytest.mark.asyncio
async def test_if_saving_fails_the_event_stays_open():
    from nodvard_deck_ext_shield import guard as guard_mod

    engine, sm = await _engine()
    ctx = _Ctx(sm, [])
    guard, events = await _two_file_events(ctx)
    ctx.outputs[:] = [_output(files=BOTH_CHANGED)]
    save = guard_mod.save_baseline

    async def broken(ctx_, host_id, kind, data):
        if kind == "files":
            raise OSError("Platte voll")
        await save(ctx_, host_id, kind, data)

    guard_mod.save_baseline = broken
    try:
        with pytest.raises(OSError):
            await guard.acknowledge(events[0]["id"])
    finally:
        guard_mod.save_baseline = save
    assert len(await guard.list_events()) == 2
    assert await guard.acknowledge(events[0]["id"])  # ein zweiter Versuch geht
    await engine.dispose()


@pytest.mark.asyncio
async def test_ports_and_a_double_click_need_no_answer_from_the_server():
    import asyncio

    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    base_ports = 'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))'
    engine, sm = await _engine()
    ctx = _Ctx(sm, [_output(ports=base_ports, files=FILES_BEFORE),
                    _output(ports=base_ports + '\ntcp LISTEN 0 128 0.0.0.0:4444 0.0.0.0:* users:(("nc",pid=66,fd=3))',
                            files=PASSWD_CHANGED)])
    guard = Guard(ctx, Defender(ctx))
    await guard.inspect(ctx._host)
    await guard.inspect(ctx._host)
    kinds = {e["kind"]: e["id"] for e in await guard.list_events()}

    async def unreachable(host, command, timeout_s=60):
        raise OSError("timeout")

    ctx.exec.run = unreachable
    assert await asyncio.gather(guard.acknowledge(kinds["new_port"]), guard.acknowledge(kinds["new_port"])) in (
        [True, False], [False, True])
    assert [a["action"] for a in ctx.audits].count("shield.guard_acknowledged") == 1
    assert [e["kind"] for e in await guard.list_events()] == ["file_changed"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_acknowledge_all_reports_what_it_did_and_what_stays_open():
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    base_ports = 'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))'
    engine, sm = await _engine()
    ctx = _Ctx(sm, [_output(ports=base_ports, files=FILES_BEFORE),
                    _output(ports=base_ports + '\ntcp LISTEN 0 128 0.0.0.0:4444 0.0.0.0:* users:(("nc",pid=66,fd=3))',
                            files=PASSWD_CHANGED)])
    guard = Guard(ctx, Defender(ctx))
    await guard.inspect(ctx._host)
    await guard.inspect(ctx._host)

    async def unreachable(host, command, timeout_s=60):
        raise OSError("timeout")

    ctx.exec.run = unreachable
    assert await guard.acknowledge_all() == (1, 1)
    await engine.dispose()


@pytest.mark.asyncio
async def test_acknowledge_all_for_one_server_leaves_the_others_open():
    from datetime import UTC, datetime

    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard
    from nodvard_deck_ext_shield.models import SecurityEventRecord

    engine, sm = await _engine()
    ctx = _Ctx(sm, [_output()])
    async with sm() as session:
        for event_id, host_id in (("e1", "h1"), ("e2", "h2"), ("e3", "h1")):
            session.add(SecurityEventRecord(id=event_id, host_id=host_id, host_name=host_id, kind="login",
                                            severity="critical", title="Anmeldung", detail={},
                                            created_at=datetime.now(UTC)))
        await session.commit()
    guard = Guard(ctx, Defender(ctx))
    assert await guard.acknowledge_all(host_id="h1") == (2, 0)
    assert [e["id"] for e in await guard.list_events()] == ["e2"]
    await engine.dispose()


def test_acknowledge_routes_say_what_stays_open():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from nodvard_deck_ext_shield.defender_api import build_routers
    from nodvard_deck_ext_shield.guard import GuardUnavailable
    from nodvard_sdk import Actor

    async def acknowledge(event_id, actor=None):
        raise GuardUnavailable("pve2 ist gerade nicht erreichbar (timeout). Das Ereignis bleibt offen.")

    hosts_asked: list[str | None] = []

    async def acknowledge_all(actor=None, host_id=None):
        hosts_asked.append(host_id)
        return 3, 1

    ctx = SimpleNamespace(api=SimpleNamespace(current_actor=lambda: Actor.user("u1", "anna")))
    guard = SimpleNamespace(acknowledge=acknowledge, acknowledge_all=acknowledge_all)
    _, manage = build_routers(ctx, SimpleNamespace(), guard=guard)
    app = FastAPI()
    app.include_router(manage)
    client = TestClient(app)
    one = client.post("/defender/events/ev1/acknowledge")
    assert one.status_code == 503 and one.json()["detail"].startswith("pve2 ist gerade nicht erreichbar")
    assert client.post("/defender/events/acknowledge-all").json() == {"acknowledged": 3, "kept_open": 1}
    client.post("/defender/events/acknowledge-all?host=h2")
    assert hosts_asked == [None, "h2"]


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


@pytest.mark.parametrize("proc", ["tailscaled", "rpcbind", "avahi-daemon", "dhclient", "dhcpcd", "chronyd", "Tailscaled",
                                  "pihole-FTL", "dnsmasq", "unbound", "named"])
def test_builtin_programs_with_changing_udp_ports_collapse(proc):
    first = ix.parse_ports([_ss("udp", 41641, proc), _ss("udp", 50123, proc, "[::]")])
    second = ix.parse_ports([_ss("udp", 36001, proc)])
    assert [p.dynamic for p in first] == [True] and [p.key for p in first] == [p.key for p in second]
    assert first[0].count == 2


@pytest.mark.parametrize("proc", ["chronyd", "dhclient", "dhcpcd", "avahi-daemon", "rpcbind", "tailscaled", "pihole-FTL",
                                  "dnsmasq", "unbound", "named"])
@pytest.mark.parametrize("port", [4444, 40000])
def test_fake_builtin_name_on_tcp_port_is_reported(proc, port):
    # Den Programmnamen kann jeder setzen (`exec -a chronyd`): ein TCP-Dienst unter diesem Namen
    # bleibt einzeln sichtbar und meldet als neuer Port.
    ports = ix.parse_ports([_ss("tcp", port, proc)])
    assert [(p.key, p.dynamic) for p in ports] == [(f"tcp/{port}/{proc}", False)]
    assert not ix.port_is_known(ports[0], {"tcp/dyn/chronyd", f"tcp/{port + 1}/{proc}"})
    # Auch wenn der Name (alter gespeicherter Wert) zusaetzlich in der Einstellung steht
    assert not ix.parse_ports([_ss("tcp", port, proc)], dynamic_processes=[proc, "rpc.statd"])[0].dynamic


PIHOLE_FIXED = [_ss("tcp", 53, "pihole-FTL"), _ss("udp", 53, "pihole-FTL"), _ss("udp", 67, "pihole-FTL"),
                _ss("tcp", 4711, "pihole-FTL", "127.0.0.1")]


def test_pihole_query_ports_collapse_but_its_fixed_ports_stay_visible():
    first = ix.parse_ports([*PIHOLE_FIXED, _ss("udp", 48593, "pihole-FTL"), _ss("udp", 58697, "pihole-FTL")])
    second = ix.parse_ports([*PIHOLE_FIXED, _ss("udp", 33017, "pihole-FTL")])
    fixed = {"tcp/53/pihole-FTL", "udp/53/pihole-FTL", "udp/67/pihole-FTL", "tcp/4711/pihole-FTL"}
    assert {p.key for p in first} == {p.key for p in second} == {*fixed, "udp/dyn/pihole-FTL"}
    assert next(p for p in first if p.dynamic).count == 2
    # Unterhalb des dynamischen Bereichs bleibt auch ein UDP-Port einzeln sichtbar
    assert {p.key for p in ix.parse_ports([_ss("udp", 5335, "pihole-FTL")])} == {"udp/5335/pihole-FTL"}
    # Wer die Zufallsports früher einzeln bestätigt hat, bekommt keine neue Meldung
    assert ix.port_is_known(next(p for p in second if p.dynamic), {"udp/48593/pihole-FTL"})


@pytest.mark.asyncio
async def test_pihole_reports_no_new_port_after_its_next_queries():
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    engine, sm = await _engine()
    scans = [[*PIHOLE_FIXED, _ss("udp", 41000 + n * 1111, "pihole-FTL")] for n in range(3)]
    ctx = _Ctx(sm, [_output(ports="\n".join(lines)) for lines in scans])
    guard = Guard(ctx, Defender(ctx))
    assert [(await guard.inspect(ctx._host))["events"] for _ in scans] == [0, 0, 0]
    await engine.dispose()


GAME_RANGE = {"name": "srcds_linux", "protocol": "udp", "from": 27015, "to": 27030}


def test_port_ranges_are_checked_strictly():
    assert ix.port_ranges([GAME_RANGE]) == [ix.PortRange("srcds_linux", "udp", 27015, 27030)]
    assert ix.port_ranges([{**GAME_RANGE, "protocol": "both", "from": 27030, "to": 27015}]) == [
        ix.PortRange("srcds_linux", "udp", 27015, 27030), ix.PortRange("srcds_linux", "tcp", 27015, 27030)]
    assert ix.port_ranges([{"name": "Mein-Dienst", "from": 5000, "to": 5000}]) == [ix.PortRange("mein-dienst", "udp", 5000, 5000)]
    # Unter 1024 nur mit Häkchen: ohne zählt der Bereich erst ab 1024, ganz darunter gar nicht
    assert ix.port_ranges([{**GAME_RANGE, "from": 100, "to": 1500}]) == [ix.PortRange("srcds_linux", "udp", 1024, 1500)]
    assert ix.port_ranges([{**GAME_RANGE, "from": 100, "to": 200}]) == []
    assert ix.port_ranges([{**GAME_RANGE, "from": 100, "to": 200, "allow_low": True}]) == [
        ix.PortRange("srcds_linux", "udp", 100, 200)]
    assert ix.port_ranges([{**GAME_RANGE, "from": 100, "to": 200, "allow_low": "true"}]) == []


def test_a_wide_port_range_needs_its_own_tick_and_never_reaches_below_1024():
    everything = {**GAME_RANGE, "from": 1, "to": 65535}
    assert ix.port_ranges([everything]) == [ix.PortRange("srcds_linux", "udp", 1024, 2023)]
    assert ix.port_ranges([{**everything, "allow_low": True}]) == [ix.PortRange("srcds_linux", "udp", 1, 1000)]
    assert ix.port_ranges([{**everything, "wide": True}]) == [ix.PortRange("srcds_linux", "udp", 1024, 65535)]
    assert ix.port_ranges([{**everything, "wide": True, "allow_low": True}]) == [ix.PortRange("srcds_linux", "udp", 1024, 65535)]
    assert ix.port_ranges([{**everything, "wide": "true"}]) == [ix.PortRange("srcds_linux", "udp", 1024, 2023)]
    # Genau 1000 Ports gehen ohne Häkchen, einer mehr wird gekürzt.
    assert ix.port_ranges([{**GAME_RANGE, "from": 10000, "to": 10999}]) == [ix.PortRange("srcds_linux", "udp", 10000, 10999)]
    assert ix.port_ranges([{**GAME_RANGE, "from": 10000, "to": 11000}]) == [ix.PortRange("srcds_linux", "udp", 10000, 10999)]
    assert ix.port_ranges([{**GAME_RANGE, "from": 1, "to": 1023, "wide": True, "allow_low": True}]) == []


def test_only_the_first_50_port_ranges_count():
    entries = [{**GAME_RANGE, "name": f"dienst{i}"} for i in range(60)]
    assert [r.process for r in ix.port_ranges(entries)] == [f"dienst{i}" for i in range(ix.PORT_RANGES_MAX)]
    assert ix.PORT_RANGES_MAX == 50
    # Auch fehlerhafte Einträge zählen mit: Was nach den ersten 50 steht, gilt nie.
    assert ix.port_ranges(["kaputt"] * 50 + [GAME_RANGE]) == []


def test_ssh_stays_visible_next_to_a_wide_range():
    ranges = ix.port_ranges([{"name": "sshd", "protocol": "tcp", "from": 1, "to": 65535, "wide": True, "allow_low": True}])
    (port,) = ix.parse_ports([_ss("tcp", 22, "sshd")], ranges=ranges)
    assert port.span is None and port.key == "tcp/22/sshd"


@pytest.mark.parametrize("entry", [
    {**GAME_RANGE, "name": "zwei worte"},
    {**GAME_RANGE, "name": "a" * 16},
    {**GAME_RANGE, "name": ""},
    {**GAME_RANGE, "name": None},
    {**GAME_RANGE, "protocol": "icmp"},
    {**GAME_RANGE, "from": 0},
    {**GAME_RANGE, "to": 65536},
    {**GAME_RANGE, "from": True},
    {**GAME_RANGE, "from": "27015"},
    {**GAME_RANGE, "from": 27015.0},
    {"name": "srcds_linux", "from": 27015},
    "srcds_linux 27015-27030",
])
def test_a_broken_port_range_does_not_count(entry):
    assert ix.port_ranges([entry]) == []
    assert ix.port_ranges(entry) == []


def test_ports_in_an_own_range_count_as_one_entry():
    ranges = ix.port_ranges([GAME_RANGE])
    lines = [_ss("udp", 27015, "srcds_linux"), _ss("udp", 27016, "SRCDS_linux", "[::]"), _ss("udp", 27031, "srcds_linux"),
             _ss("tcp", 27015, "srcds_linux"), _ss("udp", 27020, "nc"), _ss("udp", 27021, None)]
    ports = {p.key: p for p in ix.parse_ports(lines, ranges=ranges)}
    assert set(ports) == {"udp/27015-27030/srcds_linux", "udp/27015-27030/SRCDS_linux", "udp/27031/srcds_linux",
                          "tcp/27015/srcds_linux", "udp/27020/nc", "udp/27021/?"}
    collective = ports["udp/27015-27030/srcds_linux"]
    assert collective.dynamic and collective.span == (27015, 27030)
    assert collective.label == "Ports 27015–27030/udp (srcds_linux, eigener Bereich)"
    later = ix.parse_ports([_ss("udp", 27029, "srcds_linux")], ranges=ranges)
    assert [p.key for p in later] == ["udp/27015-27030/srcds_linux"]
    # Ohne root kein Programmname, also auch kein eigener Bereich
    assert [p.key for p in ix.parse_ports([_ss("udp", 27015, None)], ranges=ranges, is_root=False)] == ["udp/27015/?"]


def test_ports_confirmed_one_by_one_cover_the_new_range():
    port = ix.parse_ports([_ss("udp", 27020, "srcds_linux")], ranges=ix.port_ranges([GAME_RANGE]))[0]
    assert ix.port_is_known(port, {"udp/27016/srcds_linux"})
    assert not ix.port_is_known(port, {"udp/27031/srcds_linux"})
    assert not ix.port_is_known(port, {"tcp/27016/srcds_linux", "udp/27016/nc", "udp/dyn/srcds_linux"})


@pytest.mark.asyncio
async def test_a_game_server_in_its_range_reports_only_once_and_ports_outside_still_do():
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard

    engine, sm = await _engine()
    base = [_ss("tcp", 22, "sshd")]
    scans = [base, [*base, _ss("udp", 27015, "srcds_linux")], [*base, _ss("udp", 27022, "srcds_linux")],
             [*base, _ss("udp", 27040, "srcds_linux")]]
    ctx = _Ctx(sm, [_output(ports="\n".join(lines)) for lines in scans],
               settings={"bruteforce_threshold": 3, "guard_port_ranges": [GAME_RANGE]})
    guard = Guard(ctx, Defender(ctx))
    assert (await guard.inspect(ctx._host))["events"] == 0
    assert (await guard.inspect(ctx._host))["events"] == 1
    assert (await guard.list_events())[0]["title"] == "Neuer offener Port: Ports 27015–27030/udp (srcds_linux, eigener Bereich)"
    assert (await guard.inspect(ctx._host))["events"] == 0
    assert (await guard.inspect(ctx._host))["events"] == 1
    assert (await guard.list_events())[0]["title"] == "Neuer offener Port: 27040/udp (srcds_linux)"
    view = (await guard.overview())["hosts"][0]["view"]
    assert {p["key"]: p["span"] for p in view["ports"]} == {"tcp/22/sshd": None, "udp/27040/srcds_linux": None}
    await engine.dispose()


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
                settings={"bruteforce_threshold": 3, "guard_dynamic_processes": dyn})
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


@pytest.mark.asyncio
async def test_a_push_names_the_kind_of_each_event_only_once():
    """Bisher stand vor jedem Titel noch einmal die Art: „Neuer offener Port: Neuer offener Port: …“, „Datei geändert:
    Wichtige Datei geändert: …“. Jetzt nur dort, wo der Titel sie nicht selbst nennt. Alle Arten in einer Meldung."""
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import EVENT_LABEL, Guard

    base_ports = 'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))'
    accepted = "1758800100.1 pve2 sshd[1]: Accepted publickey for root from 192.168.1.20 port 1 ssh2"
    first = _output(ports=base_ports, files=f"{SHA_A} 644 root 1 10 - /etc/passwd", ssh=accepted)
    second = _output(
        f2b="stopped",
        ports=base_ports + '\ntcp LISTEN 0 128 0.0.0.0:4444 0.0.0.0:* users:(("nc",pid=66,fd=3))',
        files=f"{SHA_B} 644 root 2 11 - /etc/passwd",
        ssh="\n".join([
            accepted,
            "1758800200.1 pve2 sshd[2]: Accepted password for root from 45.66.77.88 port 2 ssh2",
            *(f"175880020{n}.1 pve2 sshd[{n}]: Failed password for root from 203.0.113.9 port {n} ssh2" for n in (3, 4, 5)),
        ]),
    )
    engine, sm = await _engine()
    ctx = _Ctx(sm, [first, second])
    guard = Guard(ctx, Defender(ctx))
    await guard.inspect(ctx._host)
    await guard.inspect(ctx._host)

    assert {e["kind"] for e in await guard.list_events()} == set(EVENT_LABEL), "jede Art kommt vor"
    assert sorted(ctx.notes[0].body.splitlines()) == [
        "- Angriff auf SSH: 3 fehlgeschlagene SSH-Anmeldungen von 1 Adresse(n)",
        "- Anmeldung von neuer Adresse: root@45.66.77.88",
        "- Fail2ban ist installiert, läuft aber nicht",
        "- Neuer offener Port: 4444/tcp (nc)",
        "- Wichtige Datei geändert: /etc/passwd",
    ]
    await engine.dispose()


SHA_C = "c" * 64


def _full_rule(user: str) -> str:
    import hashlib

    return hashlib.sha256(f"{user} ALL=(root) NOPASSWD: ALL\n".encode()).hexdigest()


def _state(sha: str, mode: str, owner: str) -> dict:
    return {"sha256": sha, "mode": mode, "owner": owner}


def _setup_run(**over):
    """Ein Lauf von „Mit Passwort einrichten“ für den Benutzer `nodvard`, wie der Kern ihn festhält: Benutzer angelegt,
    Schlüssel eingetragen, sudo-Regel und Hilfsskript-Regel geschrieben, die alte Regel ersetzt."""
    from nodvard_deck_ext_shield import intrusion as ix

    run = {
        "from": "2026-10-16T08:00:00+00:00", "to": "2026-10-16T08:00:40+00:00", "user": "nodvard",
        "actions": ["key_added", "legacy_rule_removed", "user_created"],
        "files": {
            "/etc/passwd": {"before": _state(SHA_A, "644", "root"), "after": _state(SHA_B, "644", "root")},
            "/etc/shadow": {"before": _state(SHA_A, "640", "root"), "after": _state(SHA_B, "640", "root")},
            "/home/nodvard/.ssh/authorized_keys": {"before": None, "after": _state(SHA_B, "600", "nodvard")},
        },
        "sudoers": {"/etc/sudoers.d/nodvard-nodvard": _full_rule("nodvard"), "/etc/sudoers.d/nodvard_shield_nodvard": SHA_C},
        "legacy_sudoers": {"/etc/sudoers.d/lattice-nodvard": _full_rule("nodvard")},
    }
    run.update(over)
    return ix.setup_run({"setup_run": run})


def _entry(path: str, sha: str, mode: str, owner: str):
    from nodvard_deck_ext_shield import intrusion as ix

    return ix.FileEntry(path=path, sha256=sha, mode=mode, owner=owner, mtime=0.0, size=1, verify="-")


def _adopted(run, path, old, new) -> bool:
    from nodvard_deck_ext_shield import intrusion as ix

    change = ix.diff_files({path: old} if old else {}, {path: new} if new else {})
    assert len(change) == 1
    return ix.adopted_by_setup(change[0], new, old, run)


def test_setup_run_is_read_from_the_host_metadata():
    from nodvard_deck_ext_shield import intrusion as ix

    assert ix.setup_run(None) is None
    assert ix.setup_run({"setup_run": {"from": "kaputt", "to": "2026-10-16T08:00:00+00:00"}}) is None
    run = _setup_run()
    assert run.end - run.start == 40 and run.user == "nodvard" and "user_created" in run.actions
    # Ohne Zeitzone gilt UTC (wie `utcnow()` im Kern).
    naive = ix.setup_run({"setup_run": {"from": "2026-10-16T08:00:00", "to": "2026-10-16T08:00:40"}})
    assert naive.start == run.start and naive.actions == frozenset() and naive.files == {}


def test_own_setup_is_adopted_only_exactly_as_reported():
    run = _setup_run()
    old_pw, new_pw = _state(SHA_A, "644", "root"), _entry("/etc/passwd", SHA_B, "644", "root")
    assert _adopted(run, "/etc/passwd", old_pw, new_pw)
    assert _adopted(run, "/etc/shadow", _state(SHA_A, "640", "root"), _entry("/etc/shadow", SHA_B, "640", "root"))
    assert _adopted(run, "/home/nodvard/.ssh/authorized_keys", None, _entry("/home/nodvard/.ssh/authorized_keys", SHA_B, "600", "nodvard"))
    rule = _entry("/etc/sudoers.d/nodvard-nodvard", _full_rule("nodvard"), "440", "root")
    assert _adopted(run, rule.path, None, rule)
    assert _adopted(run, "/etc/sudoers.d/nodvard_shield_nodvard", None, _entry("/etc/sudoers.d/nodvard_shield_nodvard", SHA_C, "440", "root"))
    assert _adopted(run, "/etc/sudoers.d/lattice-nodvard", _state(_full_rule("nodvard"), "440", "root"), None)


def test_foreign_changes_are_never_adopted_even_right_after_the_setup():
    """Gegenproben: alles, was der Befehl nicht genau so gemeldet hat, meldet der Wächter weiter."""
    run = _setup_run()
    # Nur Rechte geändert (chmod 666 /etc/shadow), auch mit passender Aktion.
    assert not _adopted(run, "/etc/shadow", _state(SHA_A, "640", "root"), _entry("/etc/shadow", SHA_A, "666", "root"))
    # Inhalt wie gemeldet, aber zusätzlich andere Rechte.
    assert not _adopted(run, "/etc/shadow", _state(SHA_A, "640", "root"), _entry("/etc/shadow", SHA_B, "666", "root"))
    # Beliebiger Inhalt in /etc/passwd (nicht der gemeldete Stand danach), und ein anderer Stand davor.
    assert not _adopted(run, "/etc/passwd", _state(SHA_A, "644", "root"), _entry("/etc/passwd", SHA_C, "644", "root"))
    assert not _adopted(run, "/etc/passwd", _state(SHA_C, "644", "root"), _entry("/etc/passwd", SHA_B, "644", "root"))
    # Ein fremder Schlüssel (anderer Inhalt als gemeldet), egal welche Änderungszeit die Datei trägt.
    keys = "/home/nodvard/.ssh/authorized_keys"
    assert not _adopted(run, keys, None, _entry(keys, SHA_C, "600", "nodvard"))
    assert not _adopted(run, keys, None, _entry(keys, SHA_B, "644", "nodvard"))
    assert not _adopted(run, "/home/anna/.ssh/authorized_keys", None, _entry("/home/anna/.ssh/authorized_keys", SHA_B, "600", "anna"))
    # Neue fremde sudo-Regeln, auch mit Namen, die nach Nodvard Deck klingen.
    for path in ("/etc/sudoers.d/nodvard-evil", "/etc/sudoers.d/lattice-evil", "/etc/sudoers.d/nodvard_shield_evil", "/etc/sudoers.d/backdoor"):
        assert not _adopted(run, path, None, _entry(path, _full_rule("nodvard"), "440", "root")), path
    # Die eigene Regel mit anderem Inhalt oder anderen Rechten.
    own = "/etc/sudoers.d/nodvard-nodvard"
    assert not _adopted(run, own, None, _entry(own, _full_rule("evil"), "440", "root"))
    assert not _adopted(run, own, None, _entry(own, _full_rule("nodvard"), "644", "root"))
    # Die alte Regel nur gelöscht und nur, wenn sie genau die ersetzte war; angelegt wird sie nie still.
    legacy = "/etc/sudoers.d/lattice-nodvard"
    assert not _adopted(run, legacy, _state(SHA_C, "440", "root"), None)
    assert not _adopted(run, legacy, None, _entry(legacy, _full_rule("nodvard"), "440", "root"))


def test_a_key_line_repaired_for_dropbear_is_adopted_like_an_added_key():
    from nodvard_deck.services import host_setup

    keys = "/home/nodvard/.ssh/authorized_keys"
    mark = host_setup.STATE_MARK
    out = f"{host_setup.KEY_FIXED}\n{mark} before {SHA_A} 600 nodvard {keys}\n{mark} after {SHA_B} 600 nodvard {keys}\n"
    run = _setup_run(**host_setup.setup_record("nodvard", host_setup.setup_report(out, user="nodvard"), sudo=False))
    assert run.actions == frozenset({"key_fixed"})
    assert _adopted(run, keys, _state(SHA_A, "600", "nodvard"), _entry(keys, SHA_B, "600", "nodvard"))
    assert not _adopted(run, keys, _state(SHA_A, "600", "nodvard"), _entry(keys, SHA_C, "600", "nodvard"))
    assert not _adopted(run, keys, _state(SHA_C, "600", "nodvard"), _entry(keys, SHA_B, "600", "nodvard"))


def test_without_the_matching_action_nothing_is_adopted():
    run = _setup_run(actions=["groups_added"])
    assert not _adopted(run, "/etc/passwd", _state(SHA_A, "644", "root"), _entry("/etc/passwd", SHA_B, "644", "root"))
    assert not _adopted(
        run, "/home/nodvard/.ssh/authorized_keys", None, _entry("/home/nodvard/.ssh/authorized_keys", SHA_B, "600", "nodvard")
    )
    assert not _adopted(run, "/etc/sudoers.d/lattice-nodvard", _state(_full_rule("nodvard"), "440", "root"), None)
    # Ohne Stand vorher/nachher (Bericht fehlt, etwa ohne sha256sum auf dem Server) auch nicht.
    bare = _setup_run(files={})
    assert not _adopted(bare, "/etc/passwd", _state(SHA_A, "644", "root"), _entry("/etc/passwd", SHA_B, "644", "root"))


@pytest.mark.asyncio
async def test_own_setup_is_taken_over_quietly_but_other_changes_still_alert():
    """Nach „Mit Passwort einrichten“ übernimmt der Wächter genau das, was der Befehl gemeldet hat, still (im Verlauf,
    schon erledigt, keine Meldung). Eine fremde Datei, ein abweichender Stand und alles nach der ersten Prüfung melden
    weiter."""
    import time
    from datetime import UTC, datetime

    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard
    from nodvard_deck_ext_shield.patching import schedule_zone
    from nodvard_sdk import Host

    t = float(int(time.time())) - 100  # der Lauf ist vorbei, die nächste Prüfung verbraucht ihn
    rule = _full_rule("nodvard")
    record = {
        "from": datetime.fromtimestamp(t, UTC).isoformat(), "to": datetime.fromtimestamp(t + 40, UTC).isoformat(),
        "user": "nodvard", "actions": ["user_created", "key_added", "legacy_rule_removed"],
        "files": {
            "/etc/passwd": {"before": _state(SHA_A, "644", "root"), "after": _state(SHA_B, "644", "root")},
            # gemeldet, aber danach noch einmal geändert: nicht mehr von der Einrichtung
            "/etc/shadow": {"before": _state(SHA_A, "640", "root"), "after": _state(SHA_B, "640", "root")},
            "/home/nodvard/.ssh/authorized_keys": {"before": _state(SHA_A, "600", "nodvard"), "after": _state(SHA_B, "600", "nodvard")},
        },
        "sudoers": {"/etc/sudoers.d/nodvard-nodvard": rule},
        "legacy_sudoers": {"/etc/sudoers.d/lattice-nodvard": rule},
    }
    before = "\n".join([
        f"{SHA_A} 644 root 1 10 - /etc/passwd",
        f"{SHA_A} 640 root 1 10 - /etc/shadow",
        f"{SHA_A} 600 nodvard 1 10 - /home/nodvard/.ssh/authorized_keys",
        f"{SHA_A} 644 root 1 10 - /etc/crontab",
        f"{rule} 440 root 1 10 - /etc/sudoers.d/lattice-nodvard",
    ])
    after = "\n".join([
        f"{SHA_B} 644 root {t + 5} 11 - /etc/passwd",
        f"{SHA_C} 640 root {t + 6} 11 - /etc/shadow",
        f"{SHA_B} 600 nodvard {t + 20} 11 - /home/nodvard/.ssh/authorized_keys",
        f"{SHA_B} 644 root {t + 10} 11 - /etc/crontab",  # im Zeitfenster, aber nicht von der Einrichtung
        f"{rule} 440 root {t + 30} 11 - /etc/sudoers.d/nodvard-nodvard",
        f"{rule} 440 root {t + 30} 11 - /etc/sudoers.d/nodvard-evil",
    ])
    later = after.replace(f"{SHA_B} 644 root {t + 5} 11 - /etc/passwd", f"{SHA_C} 644 root {t + 5} 11 - /etc/passwd")
    engine, sm = await _engine()
    ctx = _Ctx(sm, [_output(files=before), _output(files=after), _output(files=later)])
    ctx._host = Host(id="h1", name="pve2", display_name="pve2", address="10.0.0.2", credential_username="nodvard",
                     metadata={"setup_run": record})
    guard = Guard(ctx, Defender(ctx))

    # Erster Blick vor der Einrichtung: nur merken. Im Test läuft er erst danach, darum zurückdatiert.
    await guard.inspect(ctx._host)
    files = await load_files(ctx)
    files["checked_at"] = t - 100
    files.pop("setup_seen", None)
    await save_files(ctx, files)
    assert (await guard.inspect(ctx._host))["events"] == 2

    at = datetime.fromtimestamp(t, await schedule_zone(ctx)).strftime("%H:%M")
    events = await guard.list_events(include_acknowledged=True)
    quiet = next(e for e in events if e["detail"].get("by_setup"))
    loud = next(e for e in events if not e["detail"].get("by_setup"))
    assert quiet["severity"] == "info" and quiet["acknowledged"] is True
    assert quiet["title"].startswith(f"Nach der Einrichtung durch Nodvard Deck um {at} übernommen: ")
    assert {c["path"] for c in quiet["detail"]["changes"]} == {
        "/etc/passwd", "/home/nodvard/.ssh/authorized_keys", "/etc/sudoers.d/nodvard-nodvard", "/etc/sudoers.d/lattice-nodvard",
    }
    assert all(f"durch die Einrichtung von Nodvard Deck um {at}" in c["note"] for c in quiet["detail"]["changes"])
    assert {c["path"] for c in loud["detail"]["changes"]} == {"/etc/shadow", "/etc/crontab", "/etc/sudoers.d/nodvard-evil"}
    assert len(ctx.notes) == 1 and "/etc/passwd" not in ctx.notes[0].body and "/etc/crontab" in ctx.notes[0].body

    # Der Lauf ist verbraucht: Eine spätere Änderung an derselben Datei meldet der Wächter, auch wenn sie noch so aussähe.
    assert (await load_files(ctx))["setup_seen"] == record["to"]
    assert (await guard.inspect(ctx._host))["events"] == 1
    events = await guard.list_events(include_acknowledged=True)
    newest = max(events, key=lambda e: e["created_at"])
    assert not newest["detail"].get("by_setup") and {c["path"] for c in newest["detail"]["changes"]} == {"/etc/passwd"}

    o = await guard.overview()
    assert o["hosts"][0]["open_events"] == 2, "die übernommene Einrichtung zählt nicht als offen"
    await engine.dispose()


@pytest.mark.asyncio
async def test_own_setup_is_not_adopted_when_a_check_ran_after_it():
    """Lag die vorige Prüfung schon nach dem Lauf, hätte sie seine Änderungen gesehen: Was jetzt neu ist, stammt nicht
    von ihm, auch wenn es zum gemeldeten Stand passt."""
    import time
    from datetime import UTC, datetime

    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard
    from nodvard_sdk import Host

    t = float(int(time.time())) - 600
    record = {
        "from": datetime.fromtimestamp(t, UTC).isoformat(), "to": datetime.fromtimestamp(t + 40, UTC).isoformat(),
        "user": "nodvard", "actions": ["user_created"],
        "files": {"/etc/passwd": {"before": _state(SHA_A, "644", "root"), "after": _state(SHA_B, "644", "root")}},
    }
    engine, sm = await _engine()
    ctx = _Ctx(sm, [_output(files=f"{SHA_A} 644 root 1 10 - /etc/passwd"), _output(files=f"{SHA_B} 644 root 2 11 - /etc/passwd")])
    ctx._host = Host(id="h1", name="pve2", display_name="pve2", address="10.0.0.2", credential_username="nodvard",
                     metadata={"setup_run": record})
    guard = Guard(ctx, Defender(ctx))
    await guard.inspect(ctx._host)  # erster Blick: nach dem Lauf, verbraucht ihn
    assert (await guard.inspect(ctx._host))["events"] == 1
    events = await guard.list_events(include_acknowledged=True)
    assert len(events) == 1 and not events[0]["detail"].get("by_setup")
    await engine.dispose()


async def load_files(ctx):
    from nodvard_deck_ext_shield.patching import load_baseline

    return await load_baseline(ctx, "h1", "files")


async def save_files(ctx, files):
    from nodvard_deck_ext_shield.patching import save_baseline

    await save_baseline(ctx, "h1", "files", files)


def test_the_core_record_is_what_the_guard_reads():
    """Der Kern schreibt `setup_run`, der Wächter liest ihn: dasselbe Format, dieselbe Prüfsumme der sudo-Regel."""
    from nodvard_deck.services import host_setup

    mark = host_setup.STATE_MARK
    out = "\n".join([
        "Benutzer nodvard angelegt.",
        "Die alte sudo-Regel lattice-nodvard wurde durch nodvard-nodvard ersetzt.",
        f"{mark} before {SHA_A} 644 root /etc/passwd",
        f"{mark} after {SHA_B} 644 root /etc/passwd",
    ])
    record = host_setup.setup_record("nodvard", host_setup.setup_report(out, user="nodvard"), sudo=True)
    run = _setup_run(**record)
    assert _adopted(run, "/etc/passwd", _state(SHA_A, "644", "root"), _entry("/etc/passwd", SHA_B, "644", "root"))
    rule = _entry("/etc/sudoers.d/nodvard-nodvard", _full_rule("nodvard"), "440", "root")
    assert _adopted(run, rule.path, None, rule)
    assert _adopted(run, "/etc/sudoers.d/lattice-nodvard", _state(_full_rule("nodvard"), "440", "root"), None)
