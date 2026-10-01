"""Einbruchschutz und Datei-Waechter -- die REINEN Teile: ein einziger Befehl je
Server sammelt SSH-Anmeldungen (fehlgeschlagen/erfolgreich), Fail2ban-Status, offene
Ports und Pruefsummen wichtiger Dateien; die Funktionen hier werten das aus und
vergleichen mit dem zuletzt bestaetigten Stand. Ausfuehren, Speichern und Melden
steht in `guard.py`.

Im Original gab es dafuer nur den Satz "Fail2ban IPS: Aktiv und wachsam" im
Morgen-Briefing -- geprueft wurde nichts davon.

Programmdateien (sudo, sshd, ps ...) werden zusaetzlich gegen die Pruefsummen des
Pakets geprueft (`/var/lib/dpkg/info/*.md5sums`): aendert sich eine solche Datei durch
ein normales Update, passt sie zum Paket und wird still uebernommen; passt sie NICHT,
ist das ein typisches Zeichen fuer ein Rootkit.
"""

from __future__ import annotations

import ipaddress
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

DEFAULT_WATCH_FILES = [
    "/etc/passwd", "/etc/shadow", "/etc/group", "/etc/gshadow",
    "/etc/sudoers", "/etc/sudoers.d/*",
    "/etc/ssh/sshd_config", "/etc/ssh/sshd_config.d/*",
    "/root/.ssh/authorized_keys", "/home/*/.ssh/authorized_keys",
    "/etc/crontab", "/etc/cron.d/*", "/var/spool/cron/crontabs/*",
    "/etc/ld.so.preload", "/etc/hosts", "/etc/pam.d/sshd", "/etc/pam.d/common-auth",
    "/etc/profile", "/etc/bash.bashrc", "/root/.bashrc", "/etc/rc.local",
]

WATCH_BINARIES = [
    "/usr/bin/sudo", "/usr/sbin/sshd", "/usr/bin/ssh", "/usr/bin/passwd", "/usr/bin/login", "/usr/bin/su",
    "/usr/bin/ls", "/usr/bin/ps", "/usr/bin/ss", "/usr/bin/top", "/usr/bin/find", "/usr/sbin/cron",
]

# Programme, die bei jedem Start neue zufaellige Ports waehlen (NFS-Server, mDNS). Ihre
# Ports im dynamischen Bereich zaehlen als ein einziger Eintrag ("udp/dyn/rpc.mountd").
DEFAULT_DYNAMIC_PROCESSES = ["rpc.mountd", "rpc.statd", "avahi-daemon"]
# Untergrenze des dynamischen Bereichs, falls ip_local_port_range nicht lesbar ist.
DEFAULT_DYNAMIC_PORT_MIN = 32768

_SAFE_GLOB_RE = re.compile(r"^/[A-Za-z0-9_./*@+-]+$")


def guard_command(watch_files: list[str] | None = None) -> str:
    files = [f for f in (watch_files or DEFAULT_WATCH_FILES) if _SAFE_GLOB_RE.match(f) and ".." not in f]
    # Globs duerfen von der Shell aufgeloest werden -- deshalb NICHT gequotet, aber
    # vorher streng gefiltert (nur Pfadzeichen und *).
    file_list = " ".join(files)
    bin_list = " ".join(shlex.quote(b) for b in WATCH_BINARIES)
    return (
        "echo @@uid; id -u; "
        "echo @@ssh; L=$(journalctl -t sshd -t sshd-session -t sshd-auth --since '24 hours ago' --no-pager -q -o short-unix 2>/dev/null); "
        "if [ -z \"$L\" ]; then L=$(tail -q -n 20000 /var/log/auth.log /var/log/secure 2>/dev/null); fi; "
        "printf '%s\\n' \"$L\" | grep -E 'Failed password|Failed publickey|Invalid user|Accepted |authenticating user' | tail -n 8000; "
        # Ohne root scheitert `ping` am Socket-Recht ("Permission denied ... you must be
        # root") -- das heisst "nicht lesbar", nicht "gestoppt".
        "echo @@f2b; if command -v fail2ban-client >/dev/null 2>&1; then "
        "o=$(fail2ban-client ping 2>&1); rc=$?; "
        "if [ \"$rc\" -eq 0 ]; then echo running; "
        "for j in $(fail2ban-client status 2>/dev/null | sed -n 's/.*Jail list:[[:space:]]*//p' | tr ',' ' '); do "
        "echo \"@@jail $j\"; fail2ban-client status \"$j\" 2>/dev/null; done; "
        "elif printf '%s' \"$o\" | grep -qiE 'permission denied|you must be root'; then echo noaccess; "
        "else echo stopped; fi; else echo none; fi; "
        "echo @@sshd; (sshd -T 2>/dev/null || /usr/sbin/sshd -T 2>/dev/null) | grep -E "
        "'^(port|permitrootlogin|passwordauthentication|permitemptypasswords|pubkeyauthentication|maxauthtries|x11forwarding|kbdinteractiveauthentication) '; "
        "echo @@range; cat /proc/sys/net/ipv4/ip_local_port_range 2>/dev/null; "
        # Der Prozessname in `users:(("name",...` stammt vom Programm selbst und darf Zeilenumbrueche
        # enthalten ("a\n@@ssh\nb"). Nur Zeilen, die mit tcp/udp beginnen, kommen durch: sonst koennte ein
        # Prozess hier eine Abschnittsmarke vortaeuschen und die SSH-/Fail2ban-Daten ueberschreiben.
        "echo @@ports; (ss -H -tulnp 2>/dev/null || ss -tulnp 2>/dev/null | tail -n +2) | grep -E '^(tcp|udp)[[:space:]]'; "
        "echo @@files; for f in " + file_list + "; do [ -f \"$f\" ] || continue; "
        "echo \"$(sha256sum \"$f\" 2>/dev/null | cut -d' ' -f1) $(stat -c '%a %U %Y %s' \"$f\" 2>/dev/null) - $f\"; done; "
        "echo @@bins; for f in " + bin_list + "; do [ -f \"$f\" ] || continue; v=-; "
        "if command -v dpkg >/dev/null 2>&1; then r=$(readlink -f \"$f\"); "
        "p=$(dpkg -S \"$r\" 2>/dev/null | head -n 1 | cut -d: -f1); [ -z \"$p\" ] && p=$(dpkg -S \"$f\" 2>/dev/null | head -n 1 | cut -d: -f1); "
        "if [ -n \"$p\" ]; then m=$(md5sum \"$f\" | cut -d' ' -f1); "
        "if grep -qE \"^$m  (${r#/}|${f#/})$\" /var/lib/dpkg/info/$p.md5sums /var/lib/dpkg/info/$p:*.md5sums 2>/dev/null; "
        "then v=pkg-ok; else v=pkg-mismatch; fi; fi; fi; "
        "echo \"$(sha256sum \"$f\" 2>/dev/null | cut -d' ' -f1) $(stat -c '%a %U %Y %s' \"$f\" 2>/dev/null) $v $f\"; done; "
        "echo @@end"
    )


# --- Auswerten -----------------------------------------------------------------


@dataclass
class Login:
    user: str
    ip: str
    method: str
    ts: float | None


@dataclass
class Attacker:
    ip: str
    count: int = 0
    users: set[str] = field(default_factory=set)
    last_ts: float | None = None


@dataclass
class Port:
    proto: str
    address: str
    port: int
    process: str | None
    # Zufaelliger Port (Programm mit wechselnden Ports oder Kernel-Socket ohne Prozess):
    # ein Eintrag steht dann fuer alle Ports dieser Art, `count` sagt wie viele es sind.
    dynamic: bool = False
    count: int = 1

    @property
    def key(self) -> str:
        if self.dynamic:
            return f"{self.proto}/dyn/{self.process or '-'}"
        return f"{self.proto}/{self.port}/{self.process or '?'}"

    @property
    def label(self) -> str:
        if self.dynamic:
            return f"wechselnde Ports/{self.proto} ({self.process or 'ohne Programm'})"
        return f"{self.port}/{self.proto} ({self.process or '?'})"

    @property
    def public(self) -> bool:
        return not (self.address.startswith("127.") or self.address in ("::1", "[::1]") or self.address.startswith("[::ffff:127."))


@dataclass
class FileEntry:
    path: str
    sha256: str
    mode: str
    owner: str
    mtime: float
    size: int
    verify: str  # pkg-ok | pkg-mismatch | -
    binary: bool = False


@dataclass
class SshFinding:
    key: str
    value: str
    severity: str  # info | warning | critical
    text: str
    advice: str


def check_sshd(lines: list[str]) -> tuple[dict[str, str], list[SshFinding]]:
    """Wertet `sshd -T` aus (die wirksame Konfiguration, inkl. Include-Dateien)."""
    cfg: dict[str, str] = {}
    for line in lines:
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            cfg[parts[0].lower()] = parts[1].strip().lower()
    out: list[SshFinding] = []
    if not cfg:
        return cfg, out
    password = cfg.get("passwordauthentication") == "yes" or cfg.get("kbdinteractiveauthentication") == "yes"
    root = cfg.get("permitrootlogin", "")
    if cfg.get("permitemptypasswords") == "yes":
        out.append(SshFinding("permitemptypasswords", "yes", "critical", "Anmeldung mit LEEREM Passwort erlaubt",
                              "PermitEmptyPasswords no"))
    if root == "yes" and password:
        out.append(SshFinding("permitrootlogin", root, "critical", "root darf sich mit Passwort anmelden",
                              "PermitRootLogin prohibit-password (nur mit Schlüssel)"))
    elif root == "yes":
        out.append(SshFinding("permitrootlogin", root, "info", "root-Anmeldung erlaubt (Passwort ist aber aus)",
                              "PermitRootLogin prohibit-password"))
    if password:
        out.append(SshFinding("passwordauthentication", "yes", "warning", "Anmeldung mit Passwort erlaubt",
                              "Mit SSH-Schlüsseln anmelden und PasswordAuthentication no setzen"))
    try:
        tries = int(cfg.get("maxauthtries", "6"))
    except ValueError:
        tries = 6
    if tries > 6:
        out.append(SshFinding("maxauthtries", str(tries), "info", f"{tries} Versuche je Verbindung erlaubt", "MaxAuthTries 3"))
    if cfg.get("x11forwarding") == "yes":
        out.append(SshFinding("x11forwarding", "yes", "info", "X11-Weiterleitung an", "X11Forwarding no"))
    return cfg, out


F2B_STATES = ("none", "stopped", "running", "noaccess")


@dataclass
class GuardSnapshot:
    is_root: bool = False
    failures: dict[str, Attacker] = field(default_factory=dict)
    logins: list[Login] = field(default_factory=list)
    fail2ban: str = "none"  # none | stopped | running | noaccess (ohne root nicht lesbar)
    jails: dict[str, dict[str, Any]] = field(default_factory=dict)
    ports: list[Port] = field(default_factory=list)
    files: dict[str, FileEntry] = field(default_factory=dict)
    ssh_log_found: bool = False
    sshd: dict[str, str] = field(default_factory=dict)
    ssh_findings: list[SshFinding] = field(default_factory=list)
    dynamic_min: int = DEFAULT_DYNAMIC_PORT_MIN

    @property
    def banned(self) -> set[str]:
        return {ip for j in self.jails.values() for ip in j.get("banned", [])}

    @property
    def failed_total(self) -> int:
        return sum(a.count for a in self.failures.values())


def _sections(output: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    # Nur am Zeilenvorschub trennen wie grep in den Befehlen: `splitlines()` trennt auch an U+2028,
    # U+0085, Formularvorschub usw.; ein Prozess- oder Benutzername mit so einem Zeichen koennte sonst
    # eine Abschnittsmarke `@@…` vortaeuschen.
    for raw in output.split("\n"):
        line = raw.rstrip("\r")
        stripped = line.strip()
        if re.fullmatch(r"@@[a-z0-9]+", stripped):
            current = stripped[2:]
            sections[current] = []
        elif current is not None and stripped:
            sections[current].append(line)
    return sections


_TS_UNIX_RE = re.compile(r"^(\d{9,11}(?:\.\d+)?)\s")
# Die Meldung beginnt nach dem ERSTEN "sshd[pid]: " -- davor stehen nur Zeit und
# Servername. Dahinter kann der (vom Angreifer gewaehlte) Benutzername alles
# enthalten, auch ein zweites "from <ip>" oder eine ganze "Accepted ..."-Meldung.
# Deshalb: Muster nur am Meldungsanfang, und bei "Invalid user" gilt das LETZTE
# "from <ip>" (das haengt sshd selbst an).
_MSG_START_RE = re.compile(r"^.*?\bsshd(?:-session|-auth)?\[\d+\]: ")
_FAILED_RE = re.compile(r"Failed (?:password|publickey) for (?P<invalid>invalid user )?(?P<user>\S+) from (?P<ip>\S+) port")
_INVALID_RE = re.compile(r"Invalid user (?P<user>.*) from (?P<ip>\S+)(?: port \d+)?\s*$")
_AUTHUSER_RE = re.compile(r"Connection closed by authenticating user (?P<user>\S+) (?P<ip>\S+) port")
_ACCEPTED_RE = re.compile(r"Accepted (?P<method>\S+) for (?P<user>\S+) from (?P<ip>\S+) port")


_IP_CHARS_RE = re.compile(r"[0-9A-Fa-f:.]+")


def _valid_ip(ip: str) -> bool:
    """Nur echte Adressen, OHNE IPv6-Scope-ID: `ipaddress` laesst hinter `%` fast
    alles durch (`fe80::1%$(id)`), und der Benutzername in den SSH-Zeilen kommt vom
    Angreifer -- so eine "IP" darf nie als Angreifer erscheinen oder gesperrt werden."""
    if not _IP_CHARS_RE.fullmatch(ip):
        return False
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def parse_ssh(lines: list[str]) -> tuple[dict[str, Attacker], list[Login]]:
    failures: dict[str, Attacker] = {}
    logins: list[Login] = []
    for line in lines:
        m_ts = _TS_UNIX_RE.match(line)
        ts = float(m_ts.group(1)) if m_ts else None
        m_msg = _MSG_START_RE.match(line)
        if not m_msg:
            continue
        msg = line[m_msg.end():]
        m = _ACCEPTED_RE.match(msg)
        if m:
            if _valid_ip(m.group("ip")):
                logins.append(Login(user=m.group("user"), ip=m.group("ip"), method=m.group("method"), ts=ts))
            continue
        m = _FAILED_RE.match(msg)
        if m and m.group("invalid"):
            continue  # schon ueber die "Invalid user"-Zeile gezaehlt
        m = m or _INVALID_RE.match(msg) or _AUTHUSER_RE.match(msg)
        if not m or not _valid_ip(m.group("ip")):
            continue
        a = failures.setdefault(m.group("ip"), Attacker(ip=m.group("ip")))
        a.count += 1
        if m.group("user"):
            a.users.add(m.group("user")[:64])
        if ts:
            a.last_ts = max(a.last_ts or 0, ts)
    return failures, logins


_JAIL_FIELD_RE = re.compile(r"(Currently failed|Total failed|Currently banned|Total banned|Banned IP list):\s*(.*)$")


def parse_jails(lines: list[str]) -> tuple[str, dict[str, dict[str, Any]]]:
    state = lines[0].strip() if lines else "none"
    jails: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for line in lines[1:]:
        s = line.strip()
        if s.startswith("@@jail "):
            current = jails.setdefault(s[7:].strip(), {"banned": [], "currently_failed": 0, "total_failed": 0, "total_banned": 0})
            continue
        m = _JAIL_FIELD_RE.search(s)
        if current is None or not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key == "Banned IP list":
            current["banned"] = [ip for ip in value.split() if _valid_ip(ip)]
        else:
            try:
                current[key.lower().replace(" ", "_")] = int(value)
            except ValueError:
                pass
    return state, jails


# "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=812,fd=3))"
_PORT_RE = re.compile(r"^(?P<proto>tcp|udp)\s+\S+\s+\d+\s+\d+\s+(?P<local>\S+)\s+\S+(?:\s+users:\(\(\"(?P<proc>[^\"]+)\")?")


def parse_port_range(lines: list[str]) -> int:
    """Untergrenze des dynamischen Portbereichs aus `ip_local_port_range` ("32768 60999")."""
    for line in lines:
        m = re.match(r"^\s*(\d+)\s+(\d+)\s*$", line)
        # Untergrenze mindestens 10000, sonst rutschen feste Kernel-Ports (z. B. 2049) in den Sammeleintrag
        if m and 10000 <= int(m.group(1)) <= int(m.group(2)) <= 65535:
            return int(m.group(1))
    return DEFAULT_DYNAMIC_PORT_MIN


def parse_ports(lines: list[str], *, dynamic_processes: list[str] | None = None, dynamic_min: int = DEFAULT_DYNAMIC_PORT_MIN,
                is_root: bool = True) -> list[Port]:
    """Wertet `ss -tulnp` aus.

    Zufaellige Ports (>= `dynamic_min`) von Programmen aus `dynamic_processes` sowie von
    Sockets ohne Prozess (Kernel, z. B. lockd) bekommen einen Schluessel ohne Port, sonst
    meldet jeder Neustart 15 "neue" Ports. Sockets ohne Prozess gelten nur mit root als
    Kernel-Sockets -- ohne root sind auch normale Programme namenlos, dann bliebe eine
    echte neue Hintertuer hinter dem Sammeleintrag verborgen.
    """
    names = {n.strip().lower() for n in (DEFAULT_DYNAMIC_PROCESSES if dynamic_processes is None else dynamic_processes)
             if isinstance(n, str) and n.strip()}
    ports: dict[str, Port] = {}
    seen: dict[str, set[int]] = {}
    for line in lines:
        m = _PORT_RE.match(line.strip())
        if not m:
            continue
        local = m.group("local")
        addr, _, port = local.rpartition(":")
        if not port.isdigit():
            continue
        addr = addr.split("%")[0]
        proc = m.group("proc")
        num = int(port)
        dynamic = num >= dynamic_min and ((proc.lower() in names) if proc else is_root)
        p = Port(proto=m.group("proto"), address=addr, port=num, process=proc, dynamic=dynamic)
        # IPv4 und IPv6 desselben Dienstes nur einmal zeigen -- oeffentlich gewinnt.
        existing = ports.get(p.key)
        seen.setdefault(p.key, set()).add(num)
        if existing is None or (p.public and not existing.public):
            ports[p.key] = p
    for key, p in ports.items():
        if p.dynamic:
            p.count = len(seen[key])
    return sorted(ports.values(), key=lambda p: (p.dynamic, p.port, p.proto))


_LEGACY_PORT_KEY_RE = re.compile(r"^(?P<proto>tcp|udp)/(?P<port>\d+)/(?P<proc>.+)$")


def port_is_known(port: Port, known: set[str], *, dynamic_min: int = DEFAULT_DYNAMIC_PORT_MIN) -> bool:
    """Ist der Port im bestaetigten Stand? Alte Staende kennen Ports mit Nummer je Programm
    ("udp/58094/rpc.mountd"); sie decken den neuen Sammeleintrag ab, damit das Update
    nicht ueberall auf einmal "neue" Ports meldet."""
    if port.key in known:
        return True
    if not port.dynamic:
        return False
    legacy_proc = port.process or "?"
    for k in known:
        m = _LEGACY_PORT_KEY_RE.match(k)
        if m and m.group("proto") == port.proto and m.group("proc") == legacy_proc and int(m.group("port")) >= dynamic_min:
            return True
    return False


def parse_files(lines: list[str], *, binary: bool) -> dict[str, FileEntry]:
    out: dict[str, FileEntry] = {}
    for line in lines:
        parts = line.strip().split(" ", 6)
        if len(parts) < 7 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            continue
        sha, mode, owner, mtime, size, verify, path = parts
        try:
            out[path] = FileEntry(path=path, sha256=sha, mode=mode, owner=owner, mtime=float(mtime), size=int(size),
                                  verify=verify, binary=binary)
        except ValueError:
            continue
    return out


def parse_guard(output: str, dynamic_processes: list[str] | None = None) -> GuardSnapshot:
    s = _sections(output)
    uid = (s.get("uid") or ["?"])[0].strip()
    dynamic_min = parse_port_range(s.get("range", []))
    failures, logins = parse_ssh(s.get("ssh", []))
    state, jails = parse_jails(s.get("f2b", []))
    files = parse_files(s.get("files", []), binary=False)
    files.update(parse_files(s.get("bins", []), binary=True))
    sshd, ssh_findings = check_sshd(s.get("sshd", []))
    return GuardSnapshot(
        sshd=sshd, ssh_findings=ssh_findings,
        is_root=uid == "0", failures=failures, logins=logins, fail2ban=state if state in F2B_STATES else "none",
        jails=jails, ports=parse_ports(s.get("ports", []), dynamic_processes=dynamic_processes, dynamic_min=dynamic_min,
                                       is_root=uid == "0"),
        files=files, ssh_log_found=bool(s.get("ssh")), dynamic_min=dynamic_min,
    )


# --- Vergleich mit dem bestaetigten Stand -----------------------------------------


@dataclass
class Change:
    path: str
    change: str  # changed | added | removed
    severity: str  # info | warning | critical
    note: str
    auto_accept: bool = False


_CRITICAL_FILES = ("/etc/ld.so.preload", "authorized_keys", "/etc/sudoers", "/etc/passwd", "/etc/shadow")


def diff_files(accepted: dict[str, dict[str, Any]], current: dict[str, FileEntry]) -> list[Change]:
    changes: list[Change] = []
    for path, entry in current.items():
        old = accepted.get(path)
        if old is None:
            sev = "critical" if path == "/etc/ld.so.preload" else "warning"
            note = "neu angelegt"
            if "authorized_keys" in path:
                note = "neue SSH-Schlüsseldatei – wer darf sich hier anmelden?"
            changes.append(Change(path=path, change="added", severity=sev, note=note))
            continue
        if old.get("sha256") == entry.sha256 and old.get("mode") == entry.mode and old.get("owner") == entry.owner:
            continue
        if entry.binary:
            if entry.verify == "pkg-ok":
                changes.append(Change(path=path, change="changed", severity="info", note="durch Paket-Update geändert (passt zum Paket)", auto_accept=True))
            elif entry.verify == "pkg-mismatch":
                changes.append(Change(path=path, change="changed", severity="critical",
                                      note="Programmdatei passt NICHT zum installierten Paket – mögliches Rootkit!"))
            else:
                changes.append(Change(path=path, change="changed", severity="warning", note="Programmdatei geändert"))
            continue
        what = []
        if old.get("sha256") != entry.sha256:
            what.append("Inhalt")
        if old.get("mode") != entry.mode:
            what.append(f"Rechte {old.get('mode')} → {entry.mode}")
        if old.get("owner") != entry.owner:
            what.append(f"Besitzer {old.get('owner')} → {entry.owner}")
        sev = "critical" if path == "/etc/ld.so.preload" else "warning"
        changes.append(Change(path=path, change="changed", severity=sev, note=", ".join(what) + " geändert"))
    for path in accepted:
        if path not in current:
            changes.append(Change(path=path, change="removed", severity="warning" if any(k in path for k in _CRITICAL_FILES) else "info",
                                  note="gelöscht"))
    return changes


def file_entry_dict(e: FileEntry) -> dict[str, Any]:
    return {"sha256": e.sha256, "mode": e.mode, "owner": e.owner, "mtime": e.mtime, "size": e.size, "verify": e.verify, "binary": e.binary}


_JAIL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


def ban_command(ip: str, jail: str = "sshd", *, unban: bool = False) -> str:
    """Laeuft als root -- deshalb IP und Jail streng pruefen UND quoten."""
    if not _valid_ip(ip):
        raise ValueError("Ungültige IP-Adresse.")
    if not _JAIL_RE.fullmatch(jail):
        raise ValueError("Ungültiger Jail-Name.")
    return f"fail2ban-client set {shlex.quote(jail)} {'unbanip' if unban else 'banip'} {shlex.quote(ip)}"
