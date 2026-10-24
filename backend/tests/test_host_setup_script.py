"""services/host_setup -- der Einrichtungsbefehl fuer einen Server.

Jeder Wert, der in das Skript gelangt (Benutzer, Schluessel, Gruppen), wird streng geprueft
und das Ganze fuer `sh -c` mit `shlex.quote` verpackt. Die Tests hier prueft mit boesen Werten
(Anfuehrungszeichen, Zeilenumbrueche, `$(...)`), mit `sh -n` auf Syntax und fuehren das
Skript gegen Attrappen der Systembefehle wirklich aus."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import asyncssh
import pytest
from test_core_ssh_stdin import LOGIN_SHELLS, login_shell

from nodvard_deck.core import ssh
from nodvard_deck.services import host_setup
from nodvard_deck.services.host_setup import SetupError, build_setup_script

needs_sh = pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None, reason="braucht eine echte POSIX-Shell")

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPlatzhalterPlatzhalterPlatzhalterPlatzhalter lattice@bastel-pi"

EVIL_VALUES = [
    "a'; rm -rf / #",
    "lattice\nroot",
    "lattice\r",
    "$(id)",
    "`id`",
    "lattice;id",
    "lattice && id",
    "lattice\"x",
    "lattice x",
    "lattice$IFS",
    "lattice|id",
    "-lattice",
    "Lattice",
    "x" * 33,
    "",
    "max mustermann",
    "user@domain",
    "DOMAIN\\user",
    "lattice\x00",
]


NO_PROCESSES = "/nodvard-test-ohne-prozesse/[0-9]*/comm"
"""Statt /proc: Auf dem Testrechner darf ein laufender Dropbear das Ergebnis nicht ändern."""


def _script(user="lattice", key=KEY, *, processes: str = NO_PROCESSES, **kw) -> str:
    return build_setup_script(user, key, **kw)[0].replace(host_setup._PROC_COMMS, processes)


# ---------------------------------------------------------------------------
# Aufbau des Skripts
# ---------------------------------------------------------------------------


def test_script_has_no_single_quote_in_any_variant():
    for user in ("lattice", "root", "svc_1-a"):
        for groups in ((), ("docker",), ("docker", "video")):
            for sudo in (False, True):
                script, one_liner = build_setup_script(user, KEY, groups=groups, sudo=sudo)
                assert "'" not in script, (user, groups, sudo)
                # Im Einzeiler kommen Hochkommas nur als aeusserer Rahmen um das Skript vor.
                assert one_liner.count("'") == 2, (user, groups, sudo)


def test_user_variant_creates_user_with_locked_password_and_restricted_key():
    script = _script()
    assert 'id "$U" >/dev/null 2>&1 || {' in script
    assert 'useradd -m -s /bin/bash -c "Nodvard Deck" "$U"' in script
    # "*" statt "!": sshd mit `UsePAM no` behandelt "!" als gesperrtes Konto.
    assert 'usermod -p "*" "$U"' in script
    assert 'KO="restrict,pty"' in script and 'printf "%s\\n" "$KO $K"' in script
    assert script.startswith("set -e\n")
    assert "U=lattice\n" in script
    assert f'K="{KEY}"\n' in script
    assert "sudoers" not in script and "visudo" not in script
    assert "usermod -aG" not in script


def test_root_key_file_is_not_touched_when_it_is_a_symlink():
    """Proxmox: /root/.ssh/authorized_keys ist ein Link nach /etc/pve/priv/ -- dort scheitert chmod."""
    script = _script("root")
    assert '[ -L "$A" ] || { chown "$U:$G" "$A"; chmod 600 "$A"; }' in script
    # ... und auch beim Anlegen darf eine vorhandene Datei nicht angefasst werden.
    assert '[ -e "$A" ] || touch "$A"' in script
    # Der Eintrag wird angehaengt (`>>`), nie die Datei ersetzt.
    assert '>> "$A"' in script and "> \"$A\"" not in script.replace('>> "$A"', "")


def test_non_root_file_operations_all_run_as_that_user_and_never_chown():
    """Ein Link in ~/.ssh darf dem Benutzer keine Rechte an fremden Dateien geben: alles, was
    `$H/.ssh` und authorized_keys anfasst, laeuft als der Benutzer, nie als root."""
    script = _script()
    assert "runuser -u " in script and "command -v runuser" in script
    assert "chown" not in script and "install -d" not in script
    assert '[ ! -L "$S" ]' in script and '[ ! -L "$A" ]' in script
    for needle in ('R mkdir -p -m 700 "$S"', 'R chmod 700 "$S"', 'R chmod 600 "$A"', '| R tee -a "$A" >/dev/null'):
        assert needle in script, needle
    # Kein direktes Anhaengen als root.
    assert '>> "$A"' not in script
    # Der Link-Test steht vor der ersten Aenderung.
    assert script.index('[ ! -L "$S" ]') < script.index('R mkdir')
    assert script.index('[ ! -L "$A" ]') < script.index('R mkdir')


def test_root_variant_skips_useradd_groups_and_sudo():
    script = _script("root", groups=("docker",), sudo=True)
    assert "U=root\n" in script
    assert "useradd" not in script and "usermod" not in script
    assert "sudoers" not in script and "visudo" not in script and "docker" not in script
    assert "restrict,pty" in script


def test_groups_are_added_only_if_the_group_exists():
    script = _script(groups=("docker", "video", "docker"))
    assert script.count("getent group docker >/dev/null") == 1, "doppelte Gruppe nur einmal"
    assert 'usermod -aG docker "$U"' in script and 'usermod -aG video "$U"' in script
    assert "Gruppe docker gibt es hier nicht" in script
    assert "sudoers" not in script


def test_sudo_rule_is_checked_with_visudo_before_install_and_rolled_back():
    script = _script(sudo=True)
    before = script.index("visudo -c -q >/dev/null 2>&1 && VP=1")
    check = script.index('visudo -c -q -f "$T"')
    install = script.index('install -m 0440 -o root -g root "$T" "$N"')
    whole = script.index('if VC "$N"; then', install)
    rollback = script.index('rm -f "$N"; F=1', whole)
    assert before < check < install < whole < rollback
    # VC prüft die eigene Datei, die ganze Konfiguration nur, wenn sie schon vorher in Ordnung war.
    assert 'VG() { [ -z "$VP" ] || visudo -c -q; }; VC() { visudo -c -q -f "$1" && VG; }' in script
    assert 'N="/etc/sudoers.d/nodvard-$U"' in script and 'O="/etc/sudoers.d/lattice-$U"' in script
    # Die alte Regel wird erst nach der Pruefung der ganzen Konfiguration angefasst und nur nach Vergleich.
    assert whole < script.index('cmp -s "$T" "$O"') < script.index('rm -f "$O"') < rollback
    assert 'echo "$U ALL=(root) NOPASSWD: ALL" > "$T"' in script
    assert "command -v visudo" in script
    assert "sudo ist nicht installiert" in script
    # Die Temp-Datei wird auch bei einem Abbruch aufgeraeumt; Ablehnung durch visudo ist ein Fehler.
    assert 'trap "rm -f \\"\\$T\\"" EXIT' in script
    assert "abgelehnt" in script and "FEHLER" in script
    # Dateiname ohne Punkt: sudo ueberspringt sonst die Datei.
    assert "nodvard-$U" in script and "nodvard.$U" not in script and "lattice.$U" not in script


def test_one_liner_runs_as_root_directly_and_otherwise_via_sudo():
    script, one_liner = build_setup_script("lattice", KEY, groups=("docker",), sudo=True)
    assert one_liner.startswith('S=; [ "$(id -u)" = 0 ] || S=sudo; $S sh -c ')
    # Der Rest ist EIN Argument fuer `sh -c`: das Skript, Anweisungen mit "; " verbunden.
    words = shlex.split(one_liner.split("$S sh -c ", 1)[1])
    assert len(words) == 1
    assert words[0] == "; ".join(script.split("\n"))
    assert "\n" not in one_liner


def test_script_and_one_liner_are_in_the_same_order_and_single_line_statements():
    script, _ = build_setup_script("lattice", KEY, groups=("docker",), sudo=True)
    lines = script.split("\n")
    assert all(line.strip() for line in lines), "keine leeren Zeilen"
    assert lines[0] == "set -e" and lines[-1].startswith('echo "Fertig.')
    assert any(line.startswith('[ -z "$F" ] || { echo "Nicht alles') and line.endswith("exit 1; }") for line in lines)


def test_notes_warn_that_replaced_keys_stay_on_the_server():
    for user in ("lattice", "root"):
        notes = host_setup.setup_notes(user, groups=(), sudo=False)
        assert any("Verbindung prüfen" in n and "authorized_keys" in n and "bleibt" in n for n in notes), user


def test_notes_are_honest_about_the_rights():
    plain = host_setup.setup_notes("lattice", groups=(), sudo=False)
    assert any("kein Weiterleiten" in n and "Dropbear" in n and "noch einmal einrichten" in n for n in plain)
    assert not any("praktisch alles" in n for n in plain)
    full = host_setup.setup_notes("lattice", groups=("docker",), sudo=True)
    assert any("Gruppe docker ist oder sudo ohne Passwort darf" in n and "praktisch alles" in n for n in full)
    assert any("Wer sudo ohne Passwort darf, kann" in n for n in host_setup.setup_notes("lattice", groups=(), sudo=True))
    assert any("Wer in der Gruppe docker ist, kann" in n for n in host_setup.setup_notes("lattice", groups=("docker",), sudo=False))
    # Die Einrichtungsseite (SetupCommand.tsx) erkennt diesen Satz an „sudo sh -c“ und zeigt ihn nur einmal.
    assert any("sudo sh -c" in n for n in full), "kein Schein von einer engen sudo-Regel"
    root = host_setup.setup_notes("root", groups=(), sudo=False)
    assert any("Cluster" in n and "alle Knoten" in n for n in root)
    assert not any("Cluster" in n for n in full)


# ---------------------------------------------------------------------------
# Eingaben: nichts Unueberpruefes im Skript
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", EVIL_VALUES)
def test_invalid_usernames_are_rejected(value):
    with pytest.raises(SetupError):
        build_setup_script(value, KEY)


@pytest.mark.parametrize("value", ["docker; rm -rf /", "a'b", "$(id)", "Docker", "a b", "x" * 33, "", "docker\nroot", "-x", "a`b`"])
def test_invalid_group_names_are_rejected(value):
    with pytest.raises(SetupError):
        build_setup_script("lattice", KEY, groups=(value,))


@pytest.mark.parametrize(
    "key",
    [
        "",
        "ssh-ed25519",
        "ssh-ed25519 AAAA'; rm -rf / #",
        "ssh-ed25519 AAAA\nssh-ed25519 BBBB",
        "ssh-ed25519 AAAA lattice@x\n",
        'command="id" ssh-ed25519 AAAA lattice@x',
        "ssh-ed25519 AAAA$(id) lattice@x",
        "ssh-ed25519 AAAA `id`",
        "ssh-ed25519 AAAA lattice@x; rm -rf /",
        "ssh-ed25519 AAAA lattice@x $(id)",
        'ssh-ed25519 AAAA lattice@"x',
        "ssh-ed25519 AAAA lattice@x extra",
        "ssh-ed25519  AAAA lattice@x",
        "ssh-evil AAAA lattice@x",
        "ssh-ed25519 AAAA\tlattice@x",
        "ssh-ed25519 AAAA lattice\\@x",
        "ssh-ed25519 AAAA lattice@x\r",
        "restrict ssh-ed25519 AAAA lattice@x",
    ],
)
def test_invalid_public_keys_are_rejected(key):
    with pytest.raises(SetupError):
        build_setup_script("lattice", key)


def test_error_messages_never_contain_the_rejected_value():
    for bad in ("a'; rm -rf / #", "GEHEIM-$(id)"):
        with pytest.raises(SetupError) as exc:
            build_setup_script(bad, KEY)
        assert bad not in str(exc.value) and "GEHEIM" not in str(exc.value)
        with pytest.raises(SetupError) as exc:
            build_setup_script("lattice", f"ssh-ed25519 AAAA {bad}")
        assert bad not in str(exc.value) and "GEHEIM" not in str(exc.value)


def test_valid_key_types_and_comment_are_accepted():
    for alg in ("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521"):
        assert f'K="{alg} AAAAB3Nz+a/b= x"' in _script(key=f"{alg} AAAAB3Nz+a/b= x")
    assert 'K="ssh-ed25519 AAAA"' in _script(key="ssh-ed25519 AAAA"), "Kommentar ist optional"


def test_malicious_values_cannot_reach_the_script_even_when_validation_is_bypassed():
    """Zweite Sicherung: der Einbau prueft nochmals und verweigert gefaehrliche Zeichen."""
    with pytest.raises(SetupError):
        host_setup._dq('x"; id; "')
    with pytest.raises(SetupError):
        host_setup._dq("x$(id)")
    with pytest.raises(SetupError):
        host_setup._dq("x`id`")
    with pytest.raises(SetupError):
        host_setup._dq("x\\")
    with pytest.raises(SetupError):
        host_setup._dq("a\nb")
    assert host_setup._dq("ssh-ed25519 AAAA+/= lattice@pi") == "ssh-ed25519 AAAA+/= lattice@pi"


# ---------------------------------------------------------------------------
# Schluessel aus dem Vault -> oeffentlicher Schluessel
# ---------------------------------------------------------------------------


def _private_key(comment: str | None = None) -> tuple[str, asyncssh.SSHKey]:
    key = asyncssh.generate_private_key("ssh-ed25519", comment=comment)
    return key.export_private_key("openssh").decode(), key


def test_public_key_is_derived_with_our_own_comment_not_the_one_in_the_key():
    """Bei einem eingefuegten Schluessel steht der Kommentar in der Hand des Nutzers --
    er darf nie im Skript landen."""
    private, key = _private_key(comment="evil'; rm -rf / # $(id)\nssh-rsa AAAA")
    info = host_setup.derive_public_key(private, comment="lattice@bastel-pi")
    alg, blob, comment = info.public_key.split(" ")
    assert alg == "ssh-ed25519" and comment == "lattice@bastel-pi"
    assert blob == key.export_public_key("openssh").decode().split()[1]
    assert info.fingerprint == key.get_fingerprint() and info.fingerprint.startswith("SHA256:")
    assert "evil" not in info.public_key and "\n" not in info.public_key
    build_setup_script("lattice", info.public_key)  # besteht die eigene Pruefung


def test_public_key_derivation_hides_details_of_broken_keys():
    # Kopf und Fuß zusammengesetzt, damit Geheimnis-Scanner die Attrappe nicht für einen echten Schlüssel halten.
    kind = "OPENSSH " + "PRIVATE KEY"
    for bad in ("kein Schluessel GEHEIM", "", f"-----BEGIN {kind}-----\nGEHEIM\n-----END {kind}-----\n"):
        with pytest.raises(SetupError) as exc:
            host_setup.derive_public_key(bad, comment="x")
        assert "GEHEIM" not in str(exc.value)


def test_key_comment_is_made_safe_from_the_host_name():
    assert host_setup.key_comment("bastel-pi") == "nodvard@bastel-pi"
    assert host_setup.key_comment("pve node\n'$(id)") == "nodvard@pve-node----id-"
    assert host_setup.key_comment("x" * 200) == "nodvard@" + "x" * 64


# ---------------------------------------------------------------------------
# Syntax und echter Lauf gegen Attrappen
# ---------------------------------------------------------------------------


@needs_sh
@pytest.mark.parametrize("user,groups,sudo", [("lattice", (), False), ("lattice", ("docker", "video"), True), ("root", (), False)])
def test_script_and_one_liner_are_valid_shell_syntax(user, groups, sudo):
    script, one_liner = build_setup_script(user, KEY, groups=groups, sudo=sudo)
    for args in (["sh", "-n", "-c", script], ["sh", "-n", "-c", "; ".join(script.split("\n"))], ["sh", "-n", "-c", one_liner]):
        done = subprocess.run(args, capture_output=True, text=True, timeout=20)
        assert done.returncode == 0, done.stderr


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _sandbox(
    tmp_path: Path, *, user="lattice", existing_user=True, with_docker_group=True, with_visudo=True,
    visudo_whole_fails=False, visudo_rule_fails=False, runuser="runuser", sudoers_dir: Path | None = None,
    visudo_whole_fails_on_call: int | None = None,
):
    """Attrappen fuer alles, was das Skript am System aendern wuerde. Sie schreiben nur in
    `calls.log`; `getent` zeigt auf ein Home in `tmp_path`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "calls.log"
    log.write_text("")
    created = tmp_path / "created"
    if existing_user:
        created.write_text("")
    _stub(bin_dir / "id", f'''case "$1" in
  -gn) echo {user};;
  *) [ -e "{created}" ] || exit 1;;
esac
''')
    _stub(bin_dir / "getent", f'''case "$1" in
  passwd) echo "{user}:x:1000:1000::{home}:/bin/sh";;
  group) {'[ "$2" = docker ] || [ "$2" = video ]' if with_docker_group else 'exit 2'};;
esac
''')
    for name in ("useradd", "chown"):
        extra = f'touch "{created}"' if name == "useradd" else ":"
        _stub(bin_dir / name, f'echo "{name} $*" >> "{log}"; {extra}\n')
    _stub(bin_dir / "chmod", f'echo "chmod $*" >> "{log}"; exec /bin/chmod "$@"\n')
    if runuser == "runuser":
        # Wie `runuser -u BENUTZER -- BEFEHL ...`; der Test laeuft als der aktuelle Benutzer.
        _stub(bin_dir / "runuser", f'echo "runuser $*" >> "{log}"\nshift 2\n[ "$1" = "--" ] && shift\nexec "$@"\n')
    elif runuser == "sufail":
        _stub(bin_dir / "su", f'echo "su $*" >> "{log}"\nexit 1\n')
    elif runuser == "su":
        # Wie util-linux `su -s SHELL BENUTZER -c BEFEHL -- ARG...`: die Argumente gehen an die Shell.
        _stub(bin_dir / "su", f'echo "su $*" >> "{log}"\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec /bin/sh -c "exec \\"\\$@\\"" "$@"\n')
    _stub(bin_dir / "usermod", f'echo "usermod $*" >> "{log}"\n')
    # `install -d` legt das Verzeichnis an, sonst wird nur protokolliert (kein Schreiben nach /etc).
    # Mit `sudoers_dir` kopiert `install -m 0440 QUELLE ZIEL` wirklich, wenn das Ziel dort liegt.
    sudoers_case = f'"{sudoers_dir}"/*) cp "$src" "$dest";;' if sudoers_dir else ""
    _stub(bin_dir / "install", f'''echo "install $*" >> "{log}"
[ "$1" = "-d" ] && eval "mkdir -p \\"\\${{$#}}\\""
for a in "$@"; do src=$dest; dest=$a; done
case "$dest" in {sudoers_case} esac
exit 0
''')
    if with_visudo:
        whole = "exit 0"
        if visudo_whole_fails:
            whole = 'case "$*" in *-f*) exit 0;; *) exit 1;; esac'
        if visudo_rule_fails:
            whole = "exit 1"
        if visudo_whole_fails_on_call is not None:
            counter = tmp_path / "visudo-calls"
            whole = (
                f'case "$*" in *-f*) exit 0;; esac\nn=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n + 1)); '
                f'echo $n > "{counter}"\n[ "$n" != {visudo_whole_fails_on_call} ]'
            )
        # Ohne -q (Bericht für die Meldung): wie auf einem Raspberry Pi mit einer fremden Datei im Modus 0644.
        report = (
            'case "$*" in *-q*) ;; *) echo "/etc/sudoers: parsed OK"; '
            'echo "/etc/sudoers.d/010_nopasswd: bad permissions, should be mode 0440" >&2; exit 1;; esac'
        )
        _stub(bin_dir / "visudo", f'echo "visudo $*" >> "{log}"\n{report}\n{whole}\n')
    # Unter /etc wird nur protokolliert: ohne root (z. B. auf einem CI-Runner) scheitert ein echtes
    # `rm -f /etc/sudoers.d/...` schon am fehlenden Leserecht des Ordners. Alles andere (mktemp-Dateien) wirklich loeschen.
    _stub(bin_dir / "rm", f'echo "rm $*" >> "{log}"\ncase " $* " in *" /etc/"*) exit 0;; esac\nexec /bin/rm "$@"\n')
    return bin_dir, home, log


def _run(script: str, bin_dir: Path):
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "LC_ALL": "C.UTF-8"}
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env, timeout=30)


@needs_sh
def test_run_creates_user_once_and_adds_the_key_once(tmp_path):
    bin_dir, home, log = _sandbox(tmp_path, existing_user=False)
    script = _script(groups=("docker",))
    done = _run(script, bin_dir)
    assert done.returncode == 0, done.stderr
    authorized = home / ".ssh" / "authorized_keys"
    assert authorized.read_text() == f"restrict,pty {KEY}\n"
    calls = log.read_text()
    assert "useradd -m -s /bin/bash -c Nodvard Deck lattice" in calls
    assert "usermod -p * lattice" in calls
    assert "usermod -aG docker lattice" in calls
    assert "runuser -u lattice -- mkdir -p -m 700 " in calls and "runuser -u lattice -- chmod 600 " in calls
    assert "chown" not in calls and "install" not in calls, "als Benutzer, nie als root mit chown/install"
    assert "Benutzer lattice angelegt." in done.stdout and "Fertig." in done.stdout

    # Zweiter Lauf: Benutzer existiert, Schluessel steht schon -- nichts doppelt.
    log.write_text("")
    done = _run(script, bin_dir)
    assert done.returncode == 0, done.stderr
    assert authorized.read_text() == f"restrict,pty {KEY}\n"
    assert "useradd" not in log.read_text()


LOGIN_DEFS_WARNING = "configuration error - unknown item 'minlen' (notify administrator)"


@needs_sh
def test_run_hides_login_defs_warnings_of_useradd_and_usermod(tmp_path):
    """Raspberry Pi OS: useradd und usermod beschweren sich über Einträge in /etc/login.defs, die Nodvard Deck nicht
    geschrieben hat. Diese Zeilen fallen weg, andere Meldungen bleiben."""
    bin_dir, _, log = _sandbox(tmp_path, existing_user=False)
    created = tmp_path / "created"
    _stub(bin_dir / "useradd", f'''echo "useradd $*" >> "{log}"; touch "{created}"
echo "useradd: {LOGIN_DEFS_WARNING}" >&2
echo "useradd: Warnung: noch etwas anderes" >&2
''')
    _stub(bin_dir / "usermod", f'''echo "usermod $*" >> "{log}"
echo "usermod: {LOGIN_DEFS_WARNING}" >&2
''')
    done = _run(_script(groups=("docker",)), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "unknown item" not in done.stdout + done.stderr
    assert "useradd: Warnung: noch etwas anderes" in done.stdout
    calls = log.read_text()
    assert "useradd -m -s /bin/bash -c Nodvard Deck lattice" in calls
    assert "usermod -p * lattice" in calls and "usermod -aG docker lattice" in calls
    assert "Benutzer lattice angelegt." in done.stdout and "Zur Gruppe docker hinzugefügt." in done.stdout


@needs_sh
def test_run_still_stops_when_useradd_fails(tmp_path):
    bin_dir, _, _ = _sandbox(tmp_path, existing_user=False)
    _stub(bin_dir / "useradd", f'echo "useradd: {LOGIN_DEFS_WARNING}" >&2; echo "useradd: user lattice exists" >&2; exit 9\n')
    done = _run(_script(), bin_dir)
    assert done.returncode == 9
    assert "useradd: user lattice exists" in done.stdout and "unknown item" not in done.stdout
    assert "angelegt" not in done.stdout and "Fertig." not in done.stdout


@needs_sh
def test_run_keeps_other_keys_even_without_trailing_newline(tmp_path):
    bin_dir, home, _ = _sandbox(tmp_path)
    (home / ".ssh").mkdir()
    authorized = home / ".ssh" / "authorized_keys"
    authorized.write_text("ssh-rsa AAAAold nico@pc")  # kein Zeilenende am Schluss
    done = _run(_script(), bin_dir)
    assert done.returncode == 0, done.stderr
    assert authorized.read_text().split("\n") == ["ssh-rsa AAAAold nico@pc", f"restrict,pty {KEY}", ""]


@needs_sh
def test_run_follows_a_symlinked_authorized_keys_without_chmod(tmp_path):
    """Proxmox: die Datei ist ein Link ins Cluster-Dateisystem."""
    bin_dir, home, log = _sandbox(tmp_path, user="root")
    (home / ".ssh").mkdir()
    target = tmp_path / "pve-priv-authorized_keys"
    target.write_text("ssh-rsa AAAAcluster admin\n")
    (home / ".ssh" / "authorized_keys").symlink_to(target)
    done = _run(_script("root"), bin_dir)
    assert done.returncode == 0, done.stderr
    assert target.read_text() == f"ssh-rsa AAAAcluster admin\nrestrict,pty {KEY}\n"
    calls = log.read_text()
    assert "chmod" not in calls and "chown" not in calls
    assert "useradd" not in calls and "visudo" not in calls


@needs_sh
def test_run_skips_unknown_group_with_a_hint(tmp_path):
    bin_dir, _, log = _sandbox(tmp_path, with_docker_group=False)
    done = _run(_script(groups=("docker",)), bin_dir)
    assert done.returncode == 0, done.stderr
    assert "usermod -aG" not in log.read_text()
    assert "Hinweis: Gruppe docker gibt es hier nicht" in done.stdout


@needs_sh
def test_run_installs_sudo_rule_after_successful_check(tmp_path):
    bin_dir, _, log = _sandbox(tmp_path)
    done = _run(_script(sudo=True), bin_dir)
    assert done.returncode == 0, done.stderr
    calls = log.read_text().splitlines()
    install = [c for c in calls if c.startswith("install -m 0440 -o root -g root ")]
    assert len(install) == 1 and install[0].endswith(" /etc/sudoers.d/nodvard-lattice")
    assert any(c.startswith("visudo -c -q -f ") for c in calls)
    assert "visudo -c -q -f /etc/sudoers.d/nodvard-lattice" in calls, "die installierte Datei selbst"
    assert calls.count("visudo -c -q") == 2, "die ganze Konfiguration vorher und danach"
    assert "FEHLER" not in done.stdout and "Hinweis" not in done.stdout


@needs_sh
def test_run_rolls_back_the_sudo_rule_when_it_breaks_the_whole_config(tmp_path):
    # Vorher in Ordnung (1. Gesamtprüfung), nach dem Eintragen nicht mehr (2.).
    bin_dir, _, log = _sandbox(tmp_path, visudo_whole_fails_on_call=2)
    done = _run(_script(sudo=True), bin_dir)
    assert done.returncode != 0, "eine zurueckgenommene sudo-Regel ist kein Erfolg"
    assert "rm -f /etc/sudoers.d/nodvard-lattice" in log.read_text()
    assert "FEHLER: sudo-Regel zurückgenommen. visudo meldet:" in done.stdout
    assert "visudo: /etc/sudoers.d/010_nopasswd: bad permissions, should be mode 0440" in done.stdout
    assert "Fertig." not in done.stdout and "Nicht alles hat geklappt" in done.stdout


@needs_sh
def test_run_keeps_the_rule_when_another_file_already_breaks_visudo(tmp_path):
    """Eine fremde Datei mit Modus 0644 lässt `visudo -c` scheitern, schon bevor der Befehl etwas ändert. Die eigene
    Regel ist in Ordnung und bleibt; visudo nennt die Datei."""
    bin_dir, _, log = _sandbox(tmp_path, visudo_whole_fails=True)
    done = _run(_script(sudo=True), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    calls = log.read_text().splitlines()
    assert "visudo -c -q -f /etc/sudoers.d/nodvard-lattice" in calls
    assert not any(c.startswith("rm -f /etc/sudoers.d/") for c in calls), "nichts zurückgenommen"
    assert "FEHLER" not in done.stdout and "Fertig." in done.stdout
    messages = host_setup.setup_messages(done.stdout)
    assert messages == [
        host_setup.KEY_ADDED,
        "Hinweis: visudo meldet schon vor der Einrichtung einen Fehler, der nicht von Nodvard Deck kommt:",
        "visudo: /etc/sudoers.d/010_nopasswd: bad permissions, should be mode 0440",
        (
            "Hinweis: Die Regeln von Nodvard Deck werden deshalb einzeln geprüft. Steht oben „bad permissions“, hilft für "
            "die genannte Datei: chmod 0440 und chown root:root."
        ),
    ]


@needs_sh
def test_run_fails_when_visudo_rejects_the_rule_and_cleans_up_the_temp_file(tmp_path):
    bin_dir, _, log = _sandbox(tmp_path, visudo_rule_fails=True)
    done = _run(_script(sudo=True), bin_dir)
    assert done.returncode != 0
    assert "abgelehnt" in done.stdout and "Fertig." not in done.stdout
    calls = log.read_text()
    assert "install -m 0440" not in calls
    temp = [line.split()[-1] for line in calls.splitlines() if line.startswith("visudo -c -q -f ")][0]
    assert not os.path.exists(temp), "Temp-Datei ist weg"


@needs_sh
def test_run_cleans_up_the_temp_file_even_when_install_fails(tmp_path):
    bin_dir, _, log = _sandbox(tmp_path)
    _stub(bin_dir / "install", f'echo "install $*" >> "{log}"\n[ "$1" = "-d" ] && eval "mkdir -p \\"\\${{$#}}\\"" && exit 0\nexit 1\n')
    done = _run(_script(sudo=True), bin_dir)
    assert done.returncode != 0
    temp = [line.split()[-1] for line in log.read_text().splitlines() if line.startswith("visudo -c -q -f ")][0]
    assert not os.path.exists(temp)


@needs_sh
def test_run_without_visudo_only_gives_a_hint(tmp_path):
    bin_dir, _, log = _sandbox(tmp_path, with_visudo=False)
    done = _run(_script(sudo=True), bin_dir)
    assert done.returncode == 0, done.stderr
    assert "sudo ist nicht installiert" in done.stdout
    assert "install -m 0440" not in log.read_text()


@needs_sh
def test_run_stops_when_the_home_directory_is_missing(tmp_path):
    bin_dir, home, _ = _sandbox(tmp_path)
    shutil.rmtree(home)
    done = _run(_script(), bin_dir)
    assert done.returncode != 0
    assert "Home-Verzeichnis" in done.stdout


@needs_sh
def test_key_comment_with_shell_characters_cannot_run_anything(tmp_path):
    """Selbst wenn ein Kommentar Sonderzeichen enthielte, wird er nicht als Befehl ausgefuehrt:
    die Pruefung lehnt ihn ab, und ein gueltiger bleibt reiner Text."""
    marker = tmp_path / "pwned"
    with pytest.raises(SetupError):
        build_setup_script("lattice", f"ssh-ed25519 AAAA x$(touch {marker})")
    bin_dir, home, _ = _sandbox(tmp_path)
    done = _run(_script(key="ssh-ed25519 AAAA lattice@bastel-pi"), bin_dir)
    assert done.returncode == 0 and not marker.exists()
    assert os.path.exists(home / ".ssh" / "authorized_keys")


# ---------------------------------------------------------------------------
# Links in ~/.ssh: keine Rechte an fremden Dateien
# ---------------------------------------------------------------------------


@needs_sh
def test_run_refuses_a_symlinked_dot_ssh_directory(tmp_path):
    """Zeigt ~/.ssh eines bestehenden Benutzers auf /root/.ssh oder /etc, duerfte weder install -d
    noch chown dem Link folgen. Das Skript bricht ab und aendert am Ziel nichts."""
    bin_dir, home, log = _sandbox(tmp_path)
    foreign = tmp_path / "foreign-dir"
    foreign.mkdir()
    (foreign / "authorized_keys").write_text("ssh-rsa AAAAroot root@server\n")
    (home / ".ssh").symlink_to(foreign)
    done = _run(_script(), bin_dir)
    assert done.returncode != 0
    assert "FEHLER" in done.stdout and ".ssh" in done.stdout and "Link" in done.stdout
    assert (foreign / "authorized_keys").read_text() == "ssh-rsa AAAAroot root@server\n"
    assert sorted(p.name for p in foreign.iterdir()) == ["authorized_keys"]
    calls = log.read_text()
    assert "chown" not in calls and "install" not in calls and "runuser" not in calls and "chmod" not in calls
    assert "Fertig." not in done.stdout


@needs_sh
def test_run_refuses_a_symlinked_authorized_keys_file(tmp_path):
    """Ein Link auf z. B. eine sudoers-Datei: anhaengen wuerde dort schreiben."""
    bin_dir, home, log = _sandbox(tmp_path)
    (home / ".ssh").mkdir()
    foreign = tmp_path / "sudoers"
    foreign.write_text("root ALL=(ALL:ALL) ALL\n")
    before = foreign.stat().st_mode
    (home / ".ssh" / "authorized_keys").symlink_to(foreign)
    done = _run(_script(), bin_dir)
    assert done.returncode != 0
    assert "FEHLER" in done.stdout and "authorized_keys" in done.stdout and "Link" in done.stdout
    assert foreign.read_text() == "root ALL=(ALL:ALL) ALL\n"
    assert foreign.stat().st_mode == before
    calls = log.read_text()
    assert "chown" not in calls and "chmod" not in calls and "runuser" not in calls
    assert "Fertig." not in done.stdout


@needs_sh
def test_run_without_runuser_falls_back_to_su(tmp_path):
    bin_dir, home, log = _sandbox(tmp_path, runuser="su")
    done = _run(_script(), bin_dir)
    assert done.returncode == 0, done.stderr
    assert (home / ".ssh" / "authorized_keys").read_text() == f"restrict,pty {KEY}\n"
    calls = log.read_text()
    assert "su -s /bin/sh lattice -c " in calls and "runuser -u" not in calls and "chown" not in calls
    assert oct((home / ".ssh" / "authorized_keys").stat().st_mode & 0o777) == "0o600"
    assert oct((home / ".ssh").stat().st_mode & 0o777) == "0o700"


@needs_sh
def test_run_stops_when_the_user_switch_fails(tmp_path):
    bin_dir, home, _ = _sandbox(tmp_path, runuser="sufail")
    done = _run(_script(), bin_dir)
    assert done.returncode != 0 and "Fertig." not in done.stdout
    assert not (home / ".ssh" / "authorized_keys").exists()


# ---------------------------------------------------------------------------
# sudo-Regel: neuer Name nodvard-<Benutzer>, alte lattice-<Benutzer> sauber ablösen
# ---------------------------------------------------------------------------

SUDO_RULE = "lattice ALL=(root) NOPASSWD: ALL\n"


def _sudoers_run(tmp_path: Path, old_rule: str | None, *, old_link_to: Path | None = None, **sandbox_kw):
    """Fuehrt den Befehl mit sudo gegen ein echtes Verzeichnis anstelle von /etc/sudoers.d aus."""
    sudoers = tmp_path / "sudoers.d"
    sudoers.mkdir()
    if old_rule is not None:
        (sudoers / "lattice-lattice").write_text(old_rule)
    if old_link_to is not None:
        (sudoers / "lattice-lattice").symlink_to(old_link_to)
    bin_dir, _, log = _sandbox(tmp_path, sudoers_dir=sudoers, **sandbox_kw)
    script = _script(sudo=True).replace("/etc/sudoers.d/", f"{sudoers}/")
    assert str(sudoers) in script
    return _run(script, bin_dir), sudoers, log


@needs_sh
def test_run_new_server_gets_only_the_nodvard_sudo_rule(tmp_path):
    done, sudoers, _ = _sudoers_run(tmp_path, None)
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in sudoers.iterdir()) == ["nodvard-lattice"]
    assert (sudoers / "nodvard-lattice").read_text() == SUDO_RULE
    assert "Hinweis" not in done.stdout and "FEHLER" not in done.stdout


@needs_sh
def test_run_replaces_the_identical_old_sudo_rule(tmp_path):
    done, sudoers, log = _sudoers_run(tmp_path, SUDO_RULE)
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in sudoers.iterdir()) == ["nodvard-lattice"], "keine doppelte Regel"
    assert "wurde durch nodvard-lattice ersetzt" in done.stdout
    calls = log.read_text().splitlines()
    # Erst die neue Datei pruefen und installieren, dann die ganze Konfiguration pruefen, erst dann die alte entfernen.
    first_check = next(i for i, c in enumerate(calls) if c.startswith("visudo -c -q -f "))
    install = next(i for i, c in enumerate(calls) if c.startswith("install -m 0440"))
    whole = next(i for i, c in enumerate(calls) if c == "visudo -c -q" and i > install)
    removal = next(i for i, c in enumerate(calls) if c.startswith("rm -f ") and c.endswith("/lattice-lattice"))
    assert first_check < install < whole < removal
    visudo_calls = [c for c in calls if c.startswith("visudo")]
    assert visudo_calls[-1] == "visudo -c -q" and calls.index(visudo_calls[-1], removal) > removal, (
        "nach dem Entfernen wird die ganze Konfiguration nochmals geprueft"
    )


@needs_sh
@pytest.mark.parametrize(
    "old_rule",
    ["lattice ALL=(ALL) ALL\n", "lattice ALL=(root) NOPASSWD: ALL\n# von Hand\n", "lattice ALL=(root) NOPASSWD: ALL", ""],
)
def test_run_keeps_a_foreign_old_sudo_rule_with_a_hint(tmp_path, old_rule):
    done, sudoers, _ = _sudoers_run(tmp_path, old_rule)
    assert done.returncode == 0, done.stderr
    assert (sudoers / "lattice-lattice").read_text() == old_rule, "fremde Regel unveraendert"
    assert (sudoers / "nodvard-lattice").read_text() == SUDO_RULE
    assert "lattice-lattice ist eine andere Regel" in done.stdout and "bleibt stehen" in done.stdout
    assert "ersetzt" not in done.stdout


@needs_sh
def test_run_keeps_an_old_sudo_rule_that_is_a_symlink(tmp_path):
    target = tmp_path / "ziel"
    target.write_text(SUDO_RULE)
    done, sudoers, _ = _sudoers_run(tmp_path, None, old_link_to=target)
    assert done.returncode == 0, done.stderr
    assert (sudoers / "lattice-lattice").is_symlink() and target.read_text() == SUDO_RULE
    assert "ist ein Link und bleibt stehen" in done.stdout


@needs_sh
def test_run_removes_nothing_when_visudo_rejects_the_new_rule(tmp_path):
    done, sudoers, _ = _sudoers_run(tmp_path, SUDO_RULE, visudo_rule_fails=True)
    assert done.returncode != 0
    assert sorted(p.name for p in sudoers.iterdir()) == ["lattice-lattice"], "alte Regel bleibt, neue wurde nie angelegt"
    assert "abgelehnt" in done.stdout


@needs_sh
def test_run_removes_nothing_when_the_whole_config_is_broken_after_install(tmp_path):
    done, sudoers, _ = _sudoers_run(tmp_path, SUDO_RULE, visudo_whole_fails_on_call=2)
    assert done.returncode != 0
    # Die neue Regel wird zurueckgenommen, die alte bleibt: der Server hat danach genau den Stand von vorher.
    assert sorted(p.name for p in sudoers.iterdir()) == ["lattice-lattice"]
    assert (sudoers / "lattice-lattice").read_text() == SUDO_RULE
    assert "sudo-Regel zurückgenommen" in done.stdout and "ersetzt" not in done.stdout


@needs_sh
def test_run_restores_the_old_rule_when_the_config_breaks_after_removing_it(tmp_path):
    # Dritter Aufruf der ganzen Pruefung (vorher, nach dem Eintragen, nach dem Entfernen) scheitert.
    done, sudoers, _ = _sudoers_run(tmp_path, SUDO_RULE, visudo_whole_fails_on_call=3)
    assert done.returncode != 0
    assert (sudoers / "lattice-lattice").read_text() == SUDO_RULE, "alte Regel wiederhergestellt"
    assert "Die alte sudo-Regel wurde wiederhergestellt" in done.stdout and "Fertig." not in done.stdout


@needs_sh
def test_run_rerun_with_only_the_nodvard_rule_changes_nothing(tmp_path):
    done, sudoers, _ = _sudoers_run(tmp_path, None)
    assert done.returncode == 0
    bin_dir = tmp_path / "bin"
    script = _script(sudo=True).replace("/etc/sudoers.d/", f"{sudoers}/")
    again = _run(script, bin_dir)
    assert again.returncode == 0, again.stderr
    assert sorted(p.name for p in sudoers.iterdir()) == ["nodvard-lattice"]
    assert "Hinweis" not in again.stdout


@needs_sh
def test_run_does_not_add_the_key_again_when_it_is_there_with_the_old_comment(tmp_path):
    """Schluessel, die vor der Umstellung eingetragen wurden, tragen `lattice@...`; der Befehl leitet jetzt
    `nodvard@...` ab -- derselbe Schluessel darf deshalb nicht zum zweiten Mal in die Datei."""
    bin_dir, home, _ = _sandbox(tmp_path)
    (home / ".ssh").mkdir()
    authorized = home / ".ssh" / "authorized_keys"
    old_line = f"restrict,pty {KEY}\n"  # KEY traegt den alten Kommentar lattice@bastel-pi
    authorized.write_text(old_line)
    new_key = KEY.replace("lattice@bastel-pi", "nodvard@bastel-pi")
    done = _run(_script(key=new_key), bin_dir)
    assert done.returncode == 0, done.stderr
    assert authorized.read_text() == old_line
    # Ein anderer Schluessel wird dagegen angehaengt.
    other = new_key.replace("Platzhalter", "Anderslautend", 1)
    done = _run(_script(key=other), bin_dir)
    assert done.returncode == 0, done.stderr
    assert authorized.read_text() == old_line + f"restrict,pty {other}\n"


@needs_sh
def test_run_root_does_not_add_the_key_again_when_it_is_there_with_the_old_comment(tmp_path):
    bin_dir, home, _ = _sandbox(tmp_path, user="root")
    (home / ".ssh").mkdir()
    authorized = home / ".ssh" / "authorized_keys"
    authorized.write_text(f"restrict,pty {KEY}\n")
    done = _run(_script("root", key=KEY.replace("lattice@", "nodvard@")), bin_dir)
    assert done.returncode == 0, done.stderr
    assert authorized.read_text() == f"restrict,pty {KEY}\n"


# ---------------------------------------------------------------------------
# Dropbear: Optionen, die er versteht, und die alte eigene Zeile
# ---------------------------------------------------------------------------

OLD_LINE = f"restrict,pty {KEY}"
DROPBEAR_LINE = f"{host_setup.DROPBEAR_KEY_OPTIONS} {KEY}"
SCRIPT_SHELLS = ("sh", "dash", "bash")
"""Shells für das ganze Skript; busybox sh nimmt seine eigenen Befehle statt der Attrappen und prüft die neuen Teile
für sich (`test_detection_and_repair_work_in_every_login_shell`)."""


def _processes(tmp_path: Path, *names: str) -> str:
    """Eine Prozessliste wie /proc mit diesen Namen; zurück kommt das Muster, das im Skript /proc ersetzt."""
    root = tmp_path / "proc"
    (root / "self").mkdir(parents=True)
    for pid, name in enumerate(names, start=100):
        (root / str(pid)).mkdir()
        (root / str(pid) / "comm").write_text(f"{name}\n")
    return f"{root}/[0-9]*/comm"


def _run_in(shell: str, script: str, bin_dir: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    argv, path = login_shell(shell, tmp_path, path="/usr/bin:/bin")
    env = {"PATH": f"{bin_dir}:{path}", "LC_ALL": "C.UTF-8"}
    return subprocess.run([*argv, "-c", script], capture_output=True, text=True, env=env, timeout=30, check=False)


def _keys_file(home: Path, text: str) -> Path:
    (home / ".ssh").mkdir()
    authorized = home / ".ssh" / "authorized_keys"
    authorized.write_text(text)
    authorized.chmod(0o600)
    return authorized


@needs_sh
@pytest.mark.parametrize("shell", SCRIPT_SHELLS)
def test_without_dropbear_the_key_gets_restrict_pty(tmp_path, shell):
    bin_dir, home, _ = _sandbox(tmp_path)
    processes = _processes(tmp_path, "systemd", "sshd", "sshd-session", "drop")
    done = _run_in(shell, _script(processes=processes), bin_dir, tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (home / ".ssh" / "authorized_keys").read_text() == f"{OLD_LINE}\n"


@needs_sh
@pytest.mark.parametrize("shell", SCRIPT_SHELLS)
@pytest.mark.parametrize("user", ["lattice", "root"])
@pytest.mark.parametrize("name", ["dropbear", "dropbearmulti"])
def test_with_dropbear_running_the_key_gets_options_dropbear_knows(tmp_path, shell, user, name):
    bin_dir, home, _ = _sandbox(tmp_path, user=user)
    processes = _processes(tmp_path, "systemd", "sshd", name)
    done = _run_in(shell, _script(user, processes=processes), bin_dir, tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (home / ".ssh" / "authorized_keys").read_text() == f"{DROPBEAR_LINE}\n"
    assert host_setup.setup_messages(done.stdout) == [host_setup.KEY_ADDED]


@needs_sh
@pytest.mark.parametrize("shell", SCRIPT_SHELLS)
@pytest.mark.parametrize("user", ["lattice", "root"])
def test_with_dropbear_the_old_own_line_is_replaced_and_nothing_else(tmp_path, shell, user):
    bin_dir, home, _ = _sandbox(tmp_path, user=user)
    before = [
        "ssh-rsa AAAAold nico@pc",
        "",
        "  # Notiz mit\tTab, \\Backslash, $HOME, `id` und *",
        "-n",
        OLD_LINE,
        f'from="192.0.2.0/24" {KEY}',
        "ssh-ed25519 AAAAzweite ohne-zeilenende",
    ]
    authorized = _keys_file(home, "\n".join(before))
    inode = authorized.stat().st_ino
    script = _script(user, processes=_processes(tmp_path, "dropbear"))
    done = _run_in(shell, script, bin_dir, tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    after = [DROPBEAR_LINE if line == OLD_LINE else line for line in before]
    assert authorized.read_text() == "\n".join(after) + "\n"
    assert authorized.stat().st_ino == inode and stat.S_IMODE(authorized.stat().st_mode) == 0o600
    assert host_setup.setup_messages(done.stdout) == [host_setup.KEY_FIXED]

    again = _run_in(shell, script, bin_dir, tmp_path)
    assert again.returncode == 0, again.stdout + again.stderr
    assert authorized.read_text() == "\n".join(after) + "\n"
    assert host_setup.setup_messages(again.stdout) == []


def _detect_and_repair(shell: str, tmp_path: Path, keys: Path, processes: str) -> subprocess.CompletedProcess[str]:
    """Nur Erkennung und Reparatur aus dem Skript, in einer Login-Shell mit ihren eigenen Befehlen."""
    argv, path = login_shell(shell, tmp_path, path="/usr/bin:/bin")
    snippet = "; ".join([
        "set -e", f'K="{KEY}"', 'B=$(echo "$K" | cut -d" " -f1,2)', f'A="{keys}"',
        host_setup._KEY_OPTIONS.replace(host_setup._PROC_COMMS, processes),
        host_setup._KEY_FIX_FUNCTION, host_setup._key_fix(""), 'echo "KO=$KO"',
    ])
    return subprocess.run(
        [*argv, "-c", snippet], capture_output=True, text=True, env={"PATH": path, "LC_ALL": "C.UTF-8"}, timeout=30,
        check=False,
    )


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
@pytest.mark.parametrize("running", [("sshd",), ("sshd", "dropbear")], ids=["openssh", "dropbear"])
def test_detection_and_repair_work_in_every_login_shell(tmp_path, shell, running):
    keys = tmp_path / "authorized_keys"
    keys.write_text(f"ssh-rsa AAAAold nico@pc\n{OLD_LINE}\n")
    done = _detect_and_repair(shell, tmp_path, keys, _processes(tmp_path, *running))
    assert (done.returncode, done.stderr) == (0, "")
    if "dropbear" in running:
        assert done.stdout.splitlines() == [host_setup.KEY_FIXED, f"KO={host_setup.DROPBEAR_KEY_OPTIONS}"]
        assert keys.read_text() == f"ssh-rsa AAAAold nico@pc\n{DROPBEAR_LINE}\n"
    else:
        assert done.stdout.splitlines() == [f"KO={host_setup.OPENSSH_KEY_OPTIONS}"]
        assert keys.read_text() == f"ssh-rsa AAAAold nico@pc\n{OLD_LINE}\n"


@needs_sh
@pytest.mark.parametrize("line", [
    f'from="192.0.2.0/24" {KEY}',
    f"restrict {KEY}",
    f"restrict,pty {KEY} ",
    f"no-pty,restrict,pty {KEY}",
    f"restrict,pty {KEY.rsplit(' ', 1)[0]} nico@pc",
    f"restrict,pty {KEY.rsplit(' ', 1)[0]} nodvard@pi zusatz",
    f"restrict,pty {KEY.rsplit(' ', 1)[0]}  nodvard@pi",
    f"restrict,pty {KEY.rsplit(' ', 1)[0]} nodvard@pi\tx",
])
def test_with_dropbear_a_line_of_the_user_with_the_same_key_stays(tmp_path, line):
    bin_dir, home, _ = _sandbox(tmp_path)
    authorized = _keys_file(home, f"{line}\n")
    done = _run(_script(processes=_processes(tmp_path, "dropbear")), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert authorized.read_text() == f"{line}\n"
    assert host_setup.setup_messages(done.stdout) == []


@needs_sh
@pytest.mark.parametrize("shell", SCRIPT_SHELLS)
@pytest.mark.parametrize("comment", ["lattice@bastel-pi", "nodvard@alter-name"])
def test_with_dropbear_an_old_own_line_with_another_comment_is_replaced(tmp_path, shell, comment):
    bin_dir, home, _ = _sandbox(tmp_path)
    key = KEY.rsplit(" ", 1)[0]
    authorized = _keys_file(home, f"ssh-rsa AAAAold nico@pc\nrestrict,pty {key} {comment}\n")
    script = _script(key=f"{key} nodvard@bastel-pi", processes=_processes(tmp_path, "dropbear"))
    done = _run_in(shell, script, bin_dir, tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert authorized.read_text() == f"ssh-rsa AAAAold nico@pc\n{host_setup.DROPBEAR_KEY_OPTIONS} {key} {comment}\n"
    assert host_setup.setup_messages(done.stdout) == [host_setup.KEY_FIXED]


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
def test_with_dropbear_a_key_file_that_read_would_change_is_left_alone(tmp_path, shell):
    keys = tmp_path / "authorized_keys"
    original = f"ssh-rsa AAAAold nico@a\0versteckt\n{OLD_LINE}\n".encode()
    keys.write_bytes(original)
    done = _detect_and_repair(shell, tmp_path, keys, _processes(tmp_path, "dropbear"))
    assert done.returncode == 1
    assert keys.read_bytes() == original
    error, hint = host_setup.setup_messages(done.stdout)
    assert error.startswith("FEHLER: ") and hint.startswith("Hinweis: Von Hand beheben:")


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
@pytest.mark.parametrize("namespace", ["eigener", "fremder"])
def test_a_dropbear_in_another_pid_namespace_does_not_count(tmp_path, shell, namespace):
    processes = _processes(tmp_path, "sshd", "dropbear")
    proc = tmp_path / "proc"
    for pid, target in (("self", "eigener"), ("100", "eigener"), ("101", namespace)):
        (tmp_path / target).touch()
        (proc / pid / "ns").mkdir()
        (proc / pid / "ns" / "pid").symlink_to(tmp_path / target)
    keys = tmp_path / "authorized_keys"
    keys.write_text(f"{OLD_LINE}\n")
    done = _detect_and_repair(shell, tmp_path, keys, processes)
    assert (done.returncode, done.stderr) == (0, "")
    if namespace == "eigener":
        assert done.stdout.splitlines() == [host_setup.KEY_FIXED, f"KO={host_setup.DROPBEAR_KEY_OPTIONS}"]
    else:
        assert done.stdout.splitlines() == [f"KO={host_setup.OPENSSH_KEY_OPTIONS}"]
        assert keys.read_text() == f"{OLD_LINE}\n"


@needs_sh
def test_with_dropbear_only_the_first_old_line_is_replaced(tmp_path):
    bin_dir, home, _ = _sandbox(tmp_path)
    authorized = _keys_file(home, f"{OLD_LINE}\n{OLD_LINE}\n")
    done = _run(_script(processes=_processes(tmp_path, "dropbear")), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert authorized.read_text() == f"{DROPBEAR_LINE}\n{OLD_LINE}\n"


@needs_sh
def test_without_dropbear_the_old_line_stays(tmp_path):
    bin_dir, home, _ = _sandbox(tmp_path)
    authorized = _keys_file(home, f"ssh-rsa AAAAold nico@pc\n{OLD_LINE}\n")
    done = _run(_script(processes=_processes(tmp_path, "sshd")), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert authorized.read_text() == f"ssh-rsa AAAAold nico@pc\n{OLD_LINE}\n"
    assert host_setup.setup_messages(done.stdout) == []


@needs_sh
def test_with_dropbear_a_linked_root_key_file_stays_a_link(tmp_path):
    bin_dir, home, log = _sandbox(tmp_path, user="root")
    (home / ".ssh").mkdir()
    target = tmp_path / "pve-priv-authorized_keys"
    target.write_text(f"ssh-rsa AAAAcluster admin\n{OLD_LINE}\n")
    (home / ".ssh" / "authorized_keys").symlink_to(target)
    done = _run(_script("root", processes=_processes(tmp_path, "dropbear")), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (home / ".ssh" / "authorized_keys").is_symlink()
    assert target.read_text() == f"ssh-rsa AAAAcluster admin\n{DROPBEAR_LINE}\n"
    calls = log.read_text()
    assert "chmod" not in calls and "chown" not in calls


@needs_sh
def test_with_dropbear_a_linked_key_file_of_a_user_is_still_refused(tmp_path):
    bin_dir, home, log = _sandbox(tmp_path)
    (home / ".ssh").mkdir()
    foreign = tmp_path / "fremd"
    foreign.write_text(f"{OLD_LINE}\n")
    (home / ".ssh" / "authorized_keys").symlink_to(foreign)
    done = _run(_script(processes=_processes(tmp_path, "dropbear")), bin_dir)
    assert done.returncode != 0 and "ist ein Link" in done.stdout
    assert foreign.read_text() == f"{OLD_LINE}\n" and "runuser" not in log.read_text()


@needs_sh
def test_with_dropbear_a_write_that_does_not_check_out_restores_the_old_content(tmp_path):
    bin_dir, home, _ = _sandbox(tmp_path)
    _stub(bin_dir / "cmp", "exit 1\n")
    original = f"ssh-rsa AAAAold nico@pc\n{OLD_LINE}\n"
    authorized = _keys_file(home, original)
    done = _run(_script(processes=_processes(tmp_path, "dropbear")), bin_dir)
    assert done.returncode == 1
    assert authorized.read_text() == original
    [message] = host_setup.setup_messages(done.stdout)
    assert message.startswith("FEHLER: ") and "nicht sicher schreiben" in message and "Fertig." not in done.stdout


@needs_sh
def test_with_dropbear_an_unreadable_key_file_is_left_alone(tmp_path):
    bin_dir, home, _ = _sandbox(tmp_path)
    _stub(bin_dir / "cat", "exit 1\n")
    authorized = _keys_file(home, f"{OLD_LINE}\n")
    done = _run(_script(processes=_processes(tmp_path, "dropbear")), bin_dir)
    assert done.returncode == 1
    assert authorized.read_text() == f"{OLD_LINE}\n"
    error, hint = host_setup.setup_messages(done.stdout)
    assert error.startswith(f"FEHLER: Die Schlüsselzeile in {authorized} ließ sich nicht für Dropbear anpassen")
    assert error.endswith("sie ist unverändert.")
    assert hint.startswith("Hinweis: Von Hand beheben:") and host_setup.DROPBEAR_KEY_OPTIONS in hint


@needs_sh
def test_the_repair_is_reported_for_the_file_guard(tmp_path):
    bin_dir, home, _ = _sandbox(tmp_path)
    authorized = _keys_file(home, f"{OLD_LINE}\n")
    done = _run(_script(report_state=True, processes=_processes(tmp_path, "dropbear")), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    report = host_setup.setup_report(done.stdout, user="lattice")
    assert report["actions"] == ["key_fixed"]
    states = report["files"][str(authorized)]
    assert states["before"]["sha256"] != states["after"]["sha256"]
    run = host_setup.evaluate_setup_run(0, f"{host_setup.SETUP_STARTED}\n{done.stdout}", "", via_sudo=False, sudo_with_password=False)
    assert run.outcome == "ok" and run.messages == [host_setup.KEY_FIXED]


# ---------------------------------------------------------------------------
# Der Schlüssel steht schon ohne Optionen in der Datei
# ---------------------------------------------------------------------------

BARE = KEY.rsplit(" ", 1)[0]
"""Art und Schlüsseltext ohne Kommentar, wie `B` im Skript."""


@needs_sh
@pytest.mark.parametrize("shell", SCRIPT_SHELLS)
@pytest.mark.parametrize("user", ["lattice", "root"])
@pytest.mark.parametrize("running", [("sshd",), ("sshd", "dropbear")], ids=["openssh", "dropbear"])
def test_a_line_with_the_key_but_without_options_gets_them(tmp_path, shell, user, running):
    bin_dir, home, _ = _sandbox(tmp_path, user=user)
    options = host_setup.DROPBEAR_KEY_OPTIONS if "dropbear" in running else host_setup.OPENSSH_KEY_OPTIONS
    before = [
        "ssh-rsa AAAAold nico@pc",
        "  # Notiz mit\tTab, \\Backslash, $HOME, `id` und *",
        KEY,
        f'from="192.0.2.0/24" {KEY}',
        "",
    ]
    authorized = _keys_file(home, "\n".join(before))
    inode = authorized.stat().st_ino
    script = _script(user, key=f"{BARE} nodvard@bastel-pi", processes=_processes(tmp_path, *running))
    done = _run_in(shell, script, bin_dir, tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    after = [f"{options} {KEY}" if line == KEY else line for line in before]
    assert authorized.read_text() == "\n".join(after)
    assert authorized.stat().st_ino == inode and stat.S_IMODE(authorized.stat().st_mode) == 0o600
    assert host_setup.setup_messages(done.stdout) == [host_setup.KEY_RESTRICTED]

    again = _run_in(shell, script, bin_dir, tmp_path)
    assert again.returncode == 0, again.stdout + again.stderr
    assert authorized.read_text() == "\n".join(after)
    assert host_setup.setup_messages(again.stdout) == []


@needs_sh
def test_every_line_with_the_bare_key_gets_options(tmp_path):
    bin_dir, home, _ = _sandbox(tmp_path)
    authorized = _keys_file(home, f"{KEY}\nssh-rsa AAAAold nico@pc\n{BARE}\n{BARE}\tvom Laptop\n  {BARE} eingerückt\n")
    done = _run(_script(processes=_processes(tmp_path, "sshd")), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert authorized.read_text() == (
        f"{OLD_LINE}\nssh-rsa AAAAold nico@pc\nrestrict,pty {BARE}\nrestrict,pty {BARE}\tvom Laptop\n"
        f"restrict,pty {BARE} eingerückt\n"
    )
    assert host_setup.setup_messages(done.stdout) == [host_setup.KEY_RESTRICTED]


@needs_sh
@pytest.mark.parametrize("line", [
    f'from="192.0.2.0/24" {KEY}',
    f"no-port-forwarding {KEY}",
    f"# {KEY}",
    f"{BARE}x weiterer-schluessel",
    f"ssh-ed25519x {BARE.split(' ', 1)[1]} andere-art",
])
def test_a_line_that_is_not_the_bare_key_stays(tmp_path, line):
    bin_dir, home, _ = _sandbox(tmp_path)
    authorized = _keys_file(home, f"{OLD_LINE}\n{line}\n")
    done = _run(_script(processes=_processes(tmp_path, "sshd")), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert authorized.read_text() == f"{OLD_LINE}\n{line}\n"
    assert host_setup.setup_messages(done.stdout) == []


def _restrict(shell: str, tmp_path: Path, keys: Path, path: str = "/usr/bin:/bin") -> subprocess.CompletedProcess[str]:
    """Nur das Einschränken aus dem Skript, in einer Login-Shell mit ihren eigenen Befehlen."""
    argv, path = login_shell(shell, tmp_path, path=path)
    snippet = "; ".join([
        "set -e", f'K="{KEY}"', 'B=$(echo "$K" | cut -d" " -f1,2)', f'A="{keys}"',
        host_setup._KEY_OPTIONS.replace(host_setup._PROC_COMMS, _processes(tmp_path, "sshd")),
        host_setup._KEY_FIX_FUNCTION, host_setup._key_restrict(""), 'echo "KO=$KO"',
    ])
    return subprocess.run(
        [*argv, "-c", snippet], capture_output=True, text=True, env={"PATH": path, "LC_ALL": "C.UTF-8"}, timeout=30,
        check=False,
    )


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
def test_restricting_works_in_every_login_shell(tmp_path, shell):
    keys = tmp_path / "authorized_keys"
    keys.write_text(f"ssh-rsa AAAAold Nicos Rechner – Büro\n{BARE} Schlüssel für Nodvard Deck\n")
    done = _restrict(shell, tmp_path, keys)
    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout.splitlines() == [host_setup.KEY_RESTRICTED, f"KO={host_setup.OPENSSH_KEY_OPTIONS}"]
    assert keys.read_text() == f"ssh-rsa AAAAold Nicos Rechner – Büro\nrestrict,pty {BARE} Schlüssel für Nodvard Deck\n"


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
def test_a_key_file_that_read_would_change_is_not_restricted(tmp_path, shell):
    keys = tmp_path / "authorized_keys"
    original = f"ssh-rsa AAAAold nico@a\0versteckt\n{KEY}\n".encode()
    keys.write_bytes(original)
    done = _restrict(shell, tmp_path, keys)
    assert done.returncode == 1
    assert keys.read_bytes() == original
    error, hint = host_setup.setup_messages(done.stdout)
    assert error.startswith("FEHLER: In ") and error.endswith("sie ist unverändert.")
    assert hint.startswith("Hinweis: Von Hand beheben:") and "„restrict,pty“" in hint


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
def test_windows_line_ends_and_other_blanks_after_the_type_are_restricted_too(tmp_path, shell):
    kind, text = BARE.split(" ", 1)
    keys = tmp_path / "authorized_keys"
    keys.write_bytes(
        f"ssh-rsa AAAAold nico@pc\r\n{BARE}\r\n{BARE} vom Laptop\r\n{kind}\t{text}\n{kind}  {text} doppelt\n".encode()
    )
    done = _restrict(shell, tmp_path, keys)
    assert (done.returncode, done.stderr) == (0, "")
    assert keys.read_bytes() == (
        f"ssh-rsa AAAAold nico@pc\r\nrestrict,pty {BARE}\r\nrestrict,pty {BARE} vom Laptop\r\n"
        f"restrict,pty {kind}\t{text}\nrestrict,pty {kind}  {text} doppelt\n"
    ).encode()


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
def test_only_space_and_tab_count_as_blank(tmp_path, shell):
    keys = tmp_path / "authorized_keys"
    keys.write_text(f"\u3000{BARE} breit\n\u00a0{BARE} geschützt\n{BARE}\n", encoding="utf-8")
    done = _restrict(shell, tmp_path, keys)
    assert (done.returncode, done.stderr) == (0, "")
    assert keys.read_text(encoding="utf-8") == f"\u3000{BARE} breit\n\u00a0{BARE} geschützt\nrestrict,pty {BARE}\n"


@needs_sh
def test_without_cmp_the_file_stays_and_the_message_says_why(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("cat", "cut", "grep", "mktemp", "rm", "rmdir", "tee", "wc"):
        (tools / name).symlink_to(shutil.which(name))
    keys = tmp_path / "authorized_keys"
    keys.write_text(f"{BARE}\n")
    done = _restrict("sh", tmp_path, keys, path=str(tools))
    assert done.returncode == 1 and keys.read_text() == f"{BARE}\n"
    error, hint = host_setup.setup_messages(done.stdout)
    assert "cmp (Paket diffutils)" in error and hint.startswith("Hinweis: diffutils installieren")


@needs_sh
@pytest.mark.parametrize("runuser", ["runuser", "su"])
@pytest.mark.parametrize(
    "content,running", [(f"{BARE}\n", ("sshd",)), (f"{OLD_LINE}\n", ("sshd", "dropbear"))], ids=["ohne-optionen", "dropbear"]
)
def test_the_key_file_of_another_user_is_changed_as_that_user(tmp_path, runuser, content, running):
    bin_dir, home, log = _sandbox(tmp_path, runuser=runuser)
    authorized = _keys_file(home, content)
    done = _run(_script(processes=_processes(tmp_path, *running)), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert authorized.read_text() != content
    as_user = "runuser -u lattice -- " if runuser == "runuser" else "su -s /bin/sh lattice -c "
    calls = [line for line in log.read_text().splitlines() if line.startswith(as_user)]
    for command in ("grep -q", "cat ", "tee ", "cmp -s"):
        assert any(f"-- {command}" in line or f"-- sh {command}" in line for line in calls), command


@needs_sh
def test_restricting_with_a_write_that_does_not_check_out_restores_the_old_content(tmp_path):
    bin_dir, home, _ = _sandbox(tmp_path)
    _stub(bin_dir / "cmp", "exit 1\n")
    original = f"ssh-rsa AAAAold nico@pc\n{KEY}\n"
    authorized = _keys_file(home, original)
    done = _run(_script(processes=_processes(tmp_path, "sshd")), bin_dir)
    assert done.returncode == 1
    assert authorized.read_text() == original
    [message] = host_setup.setup_messages(done.stdout)
    assert message.startswith("FEHLER: ") and "nicht sicher schreiben" in message and "Fertig." not in done.stdout


@needs_sh
def test_restricting_a_linked_root_key_file_keeps_the_link(tmp_path):
    bin_dir, home, log = _sandbox(tmp_path, user="root")
    (home / ".ssh").mkdir()
    target = tmp_path / "pve-priv-authorized_keys"
    target.write_text(f"ssh-rsa AAAAcluster admin\n{KEY}\n")
    (home / ".ssh" / "authorized_keys").symlink_to(target)
    done = _run(_script("root", processes=_processes(tmp_path, "sshd")), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (home / ".ssh" / "authorized_keys").is_symlink()
    assert target.read_text() == f"ssh-rsa AAAAcluster admin\n{OLD_LINE}\n"
    calls = log.read_text()
    assert "chmod" not in calls and "chown" not in calls


@needs_sh
def test_restricting_is_reported_for_the_file_guard(tmp_path):
    bin_dir, home, _ = _sandbox(tmp_path)
    authorized = _keys_file(home, f"{KEY}\n")
    done = _run(_script(report_state=True, processes=_processes(tmp_path, "sshd")), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    report = host_setup.setup_report(done.stdout, user="lattice")
    assert report["actions"] == ["key_fixed"]
    states = report["files"][str(authorized)]
    assert states["before"]["sha256"] != states["after"]["sha256"]
    run = host_setup.evaluate_setup_run(0, f"{host_setup.SETUP_STARTED}\n{done.stdout}", "", via_sudo=False, sudo_with_password=False)
    assert run.outcome == "ok" and run.messages == [host_setup.KEY_RESTRICTED]


# ---------------------------------------------------------------------------
# Nodvard Deck fuehrt den Befehl selbst aus (Einrichten mit Passwort)
# ---------------------------------------------------------------------------

SECRET = "Pw-nur-einmal-4711"
LONG_TAIL = "\n# " + "x" * 9000
"""Macht ein Skript zu lang für die Befehlszeile, ohne etwas zu tun."""


def _inner(script: str) -> str:
    return f"exec </dev/null; echo {host_setup.SETUP_STARTED}\n{script}"


@pytest.mark.parametrize("as_root,with_password,prefix", [
    (True, False, ["sh", "-c"]),
    (False, True, ["env", "LC_ALL=C", "sudo", "-S", "-p", "", "sh", "-c"]),
    (False, False, ["env", "LC_ALL=C", "sudo", "-n", "sh", "-c"]),
])
def test_remote_command_wraps_the_unchanged_script(as_root, with_password, prefix):
    script = _script(groups=("docker",), sudo=True)
    command, command_input = host_setup.remote_setup_command(script, as_root=as_root, sudo_with_password=with_password)
    parts = shlex.split(command)
    assert parts[:-1] == prefix
    # Davor nur: Standardeingabe zu, Marke. Danach das Skript Zeichen fuer Zeichen.
    assert parts[-1] == _inner(script)
    assert command_input == b""


@pytest.mark.parametrize("as_root,with_password,start", [
    (True, False, 'sh -c "$__nd"'),
    (False, True, "env LC_ALL=C sudo -S -p '' sh -c \"$__nd\""),
    (False, False, 'env LC_ALL=C sudo -n sh -c "$__nd"'),
])
def test_a_long_script_travels_as_input_and_the_command_line_stays_short(as_root, with_password, start):
    script = _script(groups=("docker",), sudo=True) + LONG_TAIL
    command, command_input = host_setup.remote_setup_command(script, as_root=as_root, sudo_with_password=with_password)
    assert len(command.encode()) <= 600
    assert command.endswith(f"echo {host_setup.SETUP_INPUT_READ} && exec {start}")
    assert KEY.split()[1] not in command and "xxxx" not in command
    sent = re.fullmatch(r"(@@nodvard-ende-[0-9a-f]{32})\n(.*)\n\1\n", command_input.decode(), re.DOTALL)
    assert sent is not None and sent.group(2) == _inner(script)
    assert command.count(f'[ "$__l" = {sent.group(1)} ]') == 2


def test_the_setup_uses_the_same_limit_as_every_other_command():
    def script_for(size: int) -> str:
        rest = size - len(f"sh -c '{_inner('')}'".encode())
        return "ä" * (rest // 2) + "x" * (rest % 2)

    at_limit = script_for(ssh.INLINE_COMMAND_MAX)
    command, command_input = host_setup.remote_setup_command(at_limit, as_root=True, sudo_with_password=False)
    assert len(command.encode()) == ssh.INLINE_COMMAND_MAX and command_input == b""
    command, command_input = host_setup.remote_setup_command(at_limit + "x", as_root=True, sudo_with_password=False)
    assert command.startswith("IFS= read -r __l") and command_input


def test_the_length_limit_of_the_one_liner_counts_bytes(monkeypatch):
    _, one_liner = build_setup_script("lattice", KEY, sudo=True)
    assert len(one_liner.encode()) > len(one_liner), "der Befehl enthält Umlaute"
    monkeypatch.setattr(host_setup, "MAX_ONE_LINER", len(one_liner))
    with pytest.raises(SetupError, match="zu lang"):
        build_setup_script("lattice", KEY, sudo=True)


def _sudo_stub(bin_dir: Path, log: Path, *, needs_password: bool) -> Path:
    """Wie sudo: mit -S liest es bis zu drei Versuche Zeile für Zeile von der Standardeingabe (nur wenn es ein Passwort
    braucht), dann startet es den Befehl (`sh -c ...`); jeder Versuch landet in der zurückgegebenen Datei."""
    attempts = log.parent / "sudo-attempts"
    check = (
        f'n=0; while :; do IFS= read -r pw || {{ echo "sudo: no password was provided" >&2; exit 1; }}; '
        f'n=$((n + 1)); echo "$n" >> "{attempts}"; [ "$pw" = "{SECRET}" ] && break; echo "Sorry, try again." >&2; '
        f'[ "$n" -lt 3 ] || {{ echo "sudo: 3 incorrect password attempts" >&2; exit 1; }}; done'
        if needs_password else ":"
    )
    # Steht das Passwort in der Befehlszeile irgendeines Prozesses, landet LEAK im Protokoll. Das Muster kommt aus
    # einer Datei, sonst fände grep es in seiner eigenen Befehlszeile.
    pattern = log.parent / "secret-pattern"
    pattern.write_text(SECRET + "\n")
    no_password = 'echo "sudo: a password is required" >&2; exit 1' if needs_password else ":"
    _stub(bin_dir / "sudo", (
        f'echo "sudo $*" >> "{log}"\n'
        f'[ ! -d /proc/self ] || ! grep -qaFf "{pattern}" /proc/[0-9]*/cmdline 2>/dev/null || echo LEAK >> "{log}"\n'
        f'case "$1" in -S) {check};; -n) {no_password};; esac\n'
        'while [ "$1" != "sh" ]; do shift; done\n'
        'exec "$@"\n'
    ))
    return attempts


def _useradd_reading_stdin(bin_dir: Path, tmp_path: Path, log: Path) -> Path:
    """`useradd`, das wie ein unachtsamer Befehl seine Standardeingabe liest -- dort darf nie das Passwort stehen."""
    seen = tmp_path / "useradd-stdin"
    _stub(bin_dir / "useradd", f'echo "useradd $*" >> "{log}"; cat > "{seen}"; touch "{tmp_path / "created"}"\n')
    return seen


@needs_sh
@pytest.mark.parametrize("sudo_needs_password", [True, False])
def test_remote_run_via_sudo_reads_the_password_from_stdin_and_the_script_never_sees_it(tmp_path, sudo_needs_password):
    bin_dir, home, log = _sandbox(tmp_path, existing_user=False)
    attempts = _sudo_stub(bin_dir, log, needs_password=sudo_needs_password)
    seen = _useradd_reading_stdin(bin_dir, tmp_path, log)
    command, command_input = host_setup.remote_setup_command(_script(), as_root=False, sudo_with_password=True)
    assert SECRET not in command and command_input == b""

    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "LC_ALL": "C.UTF-8"}
    done = subprocess.run(["sh", "-c", command], input=f"{SECRET}\n", capture_output=True, text=True, env=env, timeout=30, check=False)
    assert done.returncode == 0, done.stderr
    assert (home / ".ssh" / "authorized_keys").read_text() == f"restrict,pty {KEY}\n", "das Skript lief wirklich"
    assert seen.read_text() == "", "das Skript bekommt das Passwort nie zu lesen, auch wenn sudo es nicht braucht"
    assert "sudo -S -p  sh -c" in log.read_text() and SECRET not in log.read_text() and "LEAK" not in log.read_text()
    assert (attempts.read_text().split() if attempts.exists() else []) == (["1"] if sudo_needs_password else [])
    run = host_setup.evaluate_setup_run(done.returncode, done.stdout, done.stderr, via_sudo=True, sudo_with_password=True)
    assert run.outcome == "ok" and run.messages == ["Benutzer lattice angelegt.", host_setup.KEY_ADDED]


@needs_sh
def test_remote_run_with_a_wrong_sudo_password_never_starts_the_script(tmp_path):
    bin_dir, home, log = _sandbox(tmp_path, existing_user=False)
    _sudo_stub(bin_dir, log, needs_password=True)
    command, _ = host_setup.remote_setup_command(_script(), as_root=False, sudo_with_password=True)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "LC_ALL": "C.UTF-8"}
    done = subprocess.run(["sh", "-c", command], input="falsch\n", capture_output=True, text=True, env=env, timeout=30, check=False)
    assert done.returncode == 1
    assert not (home / ".ssh").exists()
    run = host_setup.evaluate_setup_run(done.returncode, done.stdout, done.stderr, via_sudo=True, sudo_with_password=True)
    assert (run.outcome, run.reason, run.messages) == ("sudo_failed", "sudo hat das Passwort nicht angenommen.", [])


@needs_sh
def test_remote_run_as_root_runs_the_script_directly(tmp_path):
    bin_dir, home, _log = _sandbox(tmp_path, user="root")
    command, _ = host_setup.remote_setup_command(_script("root"), as_root=True, sudo_with_password=False)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "LC_ALL": "C.UTF-8"}
    done = subprocess.run(["sh", "-c", command], input="", capture_output=True, text=True, env=env, timeout=30, check=False)
    assert done.returncode == 0, done.stderr
    assert (home / ".ssh" / "authorized_keys").read_text() == f"restrict,pty {KEY}\n"
    run = host_setup.evaluate_setup_run(done.returncode, done.stdout, done.stderr, via_sudo=False, sudo_with_password=False)
    assert run.outcome == "ok" and run.messages == [host_setup.KEY_ADDED]


@needs_sh
def test_remote_run_reports_a_failing_script_with_its_own_lines(tmp_path):
    bin_dir, _home, _log = _sandbox(tmp_path, user="root")
    (tmp_path / "home").rmdir()  # das Home fehlt: das Skript bricht mit FEHLER ab
    command, _ = host_setup.remote_setup_command(_script("root"), as_root=True, sudo_with_password=False)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "LC_ALL": "C.UTF-8"}
    done = subprocess.run(["sh", "-c", command], input="", capture_output=True, text=True, env=env, timeout=30, check=False)
    run = host_setup.evaluate_setup_run(done.returncode, done.stdout, done.stderr, via_sudo=False, sudo_with_password=False)
    assert run.outcome == "setup_failed"
    assert len(run.messages) == 1 and run.messages[0].startswith("FEHLER: Das Home-Verzeichnis ")


# ---------------------------------------------------------------------------
# Langes Skript über die Standardeingabe, in jeder Login-Shell
# ---------------------------------------------------------------------------


def _remote_long(
    tmp_path: Path, shell: str, bin_dir: Path, script: str, *, as_root: bool = False, with_password: bool = True,
    password: str | None = SECRET, cut: int | None = None, before: str = "",
) -> subprocess.CompletedProcess[bytes]:
    argv, path = login_shell(shell, tmp_path, path="/usr/bin:/bin")
    command, command_input = host_setup.remote_setup_command(script, as_root=as_root, sudo_with_password=with_password)
    assert command_input, "der Weg über die Standardeingabe"
    data = command_input + (f"{password}\n".encode() if password is not None else b"")
    return subprocess.run(
        [*argv, "-c", before + command], input=data[:cut], capture_output=True, timeout=60, check=False,
        env={"PATH": f"{bin_dir}:{path}", "LC_ALL": "C.UTF-8"},
    )


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
@pytest.mark.parametrize("sudo_needs_password", [True, False])
def test_long_remote_run_leaves_the_password_to_sudo(tmp_path, shell, sudo_needs_password):
    bin_dir, home, log = _sandbox(tmp_path, existing_user=False)
    attempts = _sudo_stub(bin_dir, log, needs_password=sudo_needs_password)
    seen = _useradd_reading_stdin(bin_dir, tmp_path, log)
    done = _remote_long(tmp_path, shell, bin_dir, _script() + LONG_TAIL)
    stdout, stderr = done.stdout.decode(), done.stderr.decode()
    assert done.returncode == 0, stderr
    assert (home / ".ssh" / "authorized_keys").read_text() == f"restrict,pty {KEY}\n", "das Skript lief wirklich"
    assert seen.read_text() == "", "das Skript bekommt das Passwort nie zu lesen"
    calls = log.read_text()
    assert "LEAK" not in calls, "das Passwort stand in keiner Befehlszeile"
    assert SECRET not in calls
    assert (attempts.read_text().split() if attempts.exists() else []) == (["1"] if sudo_needs_password else [])
    run = host_setup.evaluate_setup_run(
        done.returncode, stdout, stderr, via_sudo=True, sudo_with_password=True, long_command=True
    )
    assert run.outcome == "ok" and run.messages == ["Benutzer lattice angelegt.", host_setup.KEY_ADDED]


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
def test_without_a_password_prompt_the_password_line_is_never_run(tmp_path, shell):
    bin_dir, home, log = _sandbox(tmp_path, existing_user=False)
    _sudo_stub(bin_dir, log, needs_password=False)
    pwned = tmp_path / "PWNED"
    done = _remote_long(tmp_path, shell, bin_dir, _script() + LONG_TAIL, password=f"touch {pwned}")
    assert done.returncode == 0, done.stderr
    assert (home / ".ssh" / "authorized_keys").exists()
    assert not pwned.exists()


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
def test_long_remote_run_with_a_wrong_password_gets_exactly_one_attempt(tmp_path, shell):
    bin_dir, home, log = _sandbox(tmp_path, existing_user=False)
    attempts = _sudo_stub(bin_dir, log, needs_password=True)
    done = _remote_long(tmp_path, shell, bin_dir, _script() + LONG_TAIL, password="falsch")
    assert done.returncode == 1
    assert attempts.read_text().split() == ["1"], "keine Zeile des Skripts wird zum zweiten Versuch"
    assert not (home / ".ssh").exists()
    run = host_setup.evaluate_setup_run(
        done.returncode, done.stdout.decode(), done.stderr.decode(), via_sudo=True, sudo_with_password=True,
        long_command=True,
    )
    assert (run.outcome, run.reason) == ("sudo_failed", "sudo hat das Passwort nicht angenommen.")


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
@pytest.mark.parametrize("as_root", [False, True])
def test_an_incomplete_setup_input_runs_nothing(tmp_path, shell, as_root):
    bin_dir, home, log = _sandbox(tmp_path, existing_user=False)
    _sudo_stub(bin_dir, log, needs_password=True)
    script = _script() + LONG_TAIL
    _, command_input = host_setup.remote_setup_command(script, as_root=as_root, sudo_with_password=not as_root)
    for cut in (10, len(command_input) // 2, len(command_input) - 1):
        done = _remote_long(
            tmp_path, shell, bin_dir, script, as_root=as_root, with_password=not as_root, password=None, cut=cut
        )
        stdout, stderr = done.stdout.decode(), done.stderr.decode()
        assert done.returncode == ssh.STDIN_INCOMPLETE_EXIT, (cut, stderr)
        assert (stdout, stderr.splitlines()) == ("", [ssh.STDIN_INCOMPLETE])
        assert log.read_text() == "" and not (home / ".ssh").exists(), "weder sudo noch das Skript liefen"
        run = host_setup.evaluate_setup_run(
            done.returncode, stdout, stderr, via_sudo=not as_root, sudo_with_password=not as_root, long_command=True
        )
        assert (run.outcome, run.messages) == ("setup_failed", [])
        assert run.reason.startswith("Der Einrichtungsbefehl kam nicht vollständig auf dem Server an")


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
@pytest.mark.parametrize("as_root", [False, True])
def test_input_read_before_the_setup_runs_nothing(tmp_path, shell, as_root):
    bin_dir, home, log = _sandbox(tmp_path, existing_user=False)
    _sudo_stub(bin_dir, log, needs_password=False)
    # Wie eine ~/.bashrc, die eine Zeile von der Standardeingabe liest: Ohne die Marke vorn liefe das Skript ohne
    # `exec </dev/null` und könnte die Passwortzeile lesen.
    done = _remote_long(
        tmp_path, shell, bin_dir, _script() + LONG_TAIL, as_root=as_root, before="IFS= read -r _vorher; "
    )
    stdout, stderr = done.stdout.decode(), done.stderr.decode()
    assert done.returncode == ssh.STDIN_INCOMPLETE_EXIT, stderr
    assert (stdout, stderr.splitlines()) == ("", [ssh.STDIN_INCOMPLETE])
    assert log.read_text() == "" and not (home / ".ssh").exists(), "weder sudo noch das Skript liefen"
    run = host_setup.evaluate_setup_run(
        done.returncode, stdout, stderr, via_sudo=not as_root, sudo_with_password=True, long_command=True
    )
    assert run.reason.startswith("Der Einrichtungsbefehl kam nicht vollständig auf dem Server an")


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
def test_long_remote_run_with_sudo_n_stops_when_sudo_wants_a_password(tmp_path, shell):
    bin_dir, home, log = _sandbox(tmp_path, existing_user=False)
    _sudo_stub(bin_dir, log, needs_password=True)
    done = _remote_long(tmp_path, shell, bin_dir, _script() + LONG_TAIL, with_password=False, password=None)
    assert done.returncode == 1
    assert "sudo -n sh -c" in log.read_text() and not (home / ".ssh").exists()
    run = host_setup.evaluate_setup_run(
        done.returncode, done.stdout.decode(), done.stderr.decode(), via_sudo=True, sudo_with_password=False,
        long_command=True,
    )
    assert (run.outcome, run.reason) == (
        "sudo_failed", "sudo verlangt ein Passwort. Setze den Haken „Für sudo dasselbe Passwort nehmen“."
    )


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
def test_long_remote_run_as_root_runs_the_script_directly(tmp_path, shell):
    bin_dir, home, _log = _sandbox(tmp_path, user="root")
    script = _script("root") + LONG_TAIL
    done = _remote_long(tmp_path, shell, bin_dir, script, as_root=True, with_password=False, password=None)
    assert done.returncode == 0, done.stderr
    assert (home / ".ssh" / "authorized_keys").read_text() == f"restrict,pty {KEY}\n"
    run = host_setup.evaluate_setup_run(
        done.returncode, done.stdout.decode(), done.stderr.decode(), via_sudo=False, sudo_with_password=False,
        long_command=True,
    )
    assert run.outcome == "ok" and run.messages == [host_setup.KEY_ADDED]


@needs_sh
@pytest.mark.parametrize("shell", LOGIN_SHELLS)
def test_the_real_setup_with_all_helpers_reaches_sudo_unchanged(tmp_path, shell):
    from test_bundled_root_helpers import _helpers

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    seen = tmp_path / "sudo-sah"
    _stub(bin_dir / "sudo", f'IFS= read -r pw; mkdir "{seen}"; printf %s "$pw" > "{seen}/pw"; '
          f'printf "%s\\n" "$1" "$2" "$3" "$4" "$5" > "{seen}/args"; printf %s "$6" > "{seen}/script"\n')
    script, _ = build_setup_script("deck", KEY, sudo=True, helpers=list(_helpers().values()), report_state=True)
    assert len(script.encode()) > 50_000
    done = _remote_long(tmp_path, shell, bin_dir, script)
    assert done.returncode == 0, done.stderr
    assert (seen / "args").read_text().split("\n")[:5] == ["-S", "-p", "", "sh", "-c"]
    assert (seen / "pw").read_text() == SECRET
    assert (seen / "script").read_text() == _inner(script) + "\n"


@pytest.mark.parametrize("stderr,code,with_password,reason", [
    ("Sorry, user admin is not allowed to execute '/bin/sh' as root on pve1.", 1, True, "Das Anmelde-Konto darf kein sudo"),
    ("admin is not in the sudoers file.  This incident will be reported.", 1, True, "Das Anmelde-Konto darf kein sudo"),
    ("sudo: a password is required", 1, False, "sudo verlangt ein Passwort. Setze den Haken"),
    ("sudo: no tty present and no askpass program specified", 1, True, "sudo verlangt auf diesem Server ein Terminal"),
    ("sh: 1: sudo: not found", 127, True, "sudo ist auf dem Server nicht installiert"),
    ("etwas ganz anderes", 1, True, "sudo hat nicht geklappt"),
])
def test_sudo_failures_are_fixed_sentences(stderr, code, with_password, reason):
    run = host_setup.evaluate_setup_run(code, "", stderr, via_sudo=True, sudo_with_password=with_password)
    assert run.outcome == "sudo_failed" and run.reason.startswith(reason) and run.messages == []
    assert stderr not in run.reason


def test_an_incomplete_input_counts_only_with_its_line_and_exit_code():
    line = f"{ssh.STDIN_INCOMPLETE}\n"
    run = host_setup.evaluate_setup_run(ssh.STDIN_INCOMPLETE_EXIT, "", line, via_sudo=True, sudo_with_password=True)
    assert (run.outcome, run.messages) == ("setup_failed", []) and "nichts ausgeführt" in run.reason
    assert host_setup.evaluate_setup_run(1, "", line, via_sudo=True, sudo_with_password=True).outcome == "sudo_failed"
    other = host_setup.evaluate_setup_run(
        ssh.STDIN_INCOMPLETE_EXIT, "", "kaputt", via_sudo=False, sudo_with_password=False
    )
    assert other.reason == "Der Befehl ließ sich auf dem Server nicht starten."


# Was fish 3.7 und tcsh 6.24 als Login-Shell auf die Hülle antworten; gelaufen ist dabei nichts.
FISH = (127, "fish: Unsupported use of '='. In fish, please use 'set __nd '.\nIFS= read -r __l && [ \"$__l\" ...\n")
TCSH = (1, "Unmatched '\"'.\nUnmatched '\"'.\n")


@pytest.mark.parametrize("code,stderr", [FISH, TCSH])
@pytest.mark.parametrize("via_sudo,outcome", [(True, "sudo_failed"), (False, "setup_failed")])
def test_a_login_shell_that_cannot_read_the_long_command_is_named(code, stderr, via_sudo, outcome):
    run = host_setup.evaluate_setup_run(
        code, "", stderr, via_sudo=via_sudo, sudo_with_password=True, long_command=True
    )
    assert (run.outcome, run.messages) == (outcome, [])
    assert run.reason.startswith("Die Login-Shell dieses Kontos konnte den langen Einrichtungsbefehl nicht lesen")
    assert "fish oder tcsh" in run.reason and "sudo ist" not in run.reason


def test_only_a_long_command_without_its_read_mark_blames_the_login_shell():
    code, stderr = FISH
    short = host_setup.evaluate_setup_run(code, "", stderr, via_sudo=True, sudo_with_password=True)
    assert short.reason == "sudo ist auf dem Server nicht installiert. Melde dich als root an."
    read = f"{host_setup.SETUP_INPUT_READ}\n"
    sudo = host_setup.evaluate_setup_run(
        1, read, "sudo: 1 incorrect password attempt\n", via_sudo=True, sudo_with_password=True, long_command=True
    )
    assert sudo.reason == "sudo hat das Passwort nicht angenommen."


def test_without_the_marker_as_root_the_command_did_not_start():
    run = host_setup.evaluate_setup_run(126, "", "sh: permission denied", via_sudo=False, sudo_with_password=False)
    assert (run.outcome, run.reason) == ("setup_failed", "Der Befehl ließ sich auf dem Server nicht starten.")


def test_a_failed_script_without_own_lines_does_not_point_to_messages_below():
    # Gestartet (Marke da), Ausgang 1, aber nur Fremdes in der Ausgabe: unten steht nichts zu lesen.
    stdout = f"{host_setup.SETUP_STARTED}\nuseradd: Fehler beim Anlegen\n"
    run = host_setup.evaluate_setup_run(1, stdout, "useradd: kaputt", via_sudo=False, sudo_with_password=False)
    assert run.outcome == "setup_failed" and run.messages == []
    assert "unten" not in run.reason, "es gibt keine Meldungen, auf die der Satz verweisen koennte"
    assert "keine Meldung hinterlassen" in run.reason and "Befehl selbst einfügen" in run.reason
    assert "useradd" not in run.reason, "nie Text vom Server"
    # Mit eigenen Zeilen bleibt es beim Verweis.
    own = f"{host_setup.SETUP_STARTED}\nFEHLER: Das Home-Verzeichnis /home/x gibt es nicht.\n"
    run = host_setup.evaluate_setup_run(1, own, "", via_sudo=False, sudo_with_password=False)
    assert run.outcome == "setup_failed" and "Die Meldungen unten sagen, woran es lag." in run.reason


def test_setup_messages_keep_only_the_scripts_own_lines():
    stdout = "\n".join([
        host_setup.SETUP_STARTED,
        "Benutzer nodvard angelegt.",
        "useradd: warning: something",
        "Zur Gruppe docker hinzugefügt.",
        "Zur Gruppe docker hinzugefügt.",
        "Hinweis: sudo ist nicht installiert (als root: apt install sudo).",
        "FEHLER: x\x1b[31m rot",
        "Fertig. Jetzt im Dashboard „Verbindung prüfen“ drücken.",
        "irgendwas anderes",
        "FEHLER: " + "y" * 1000,
    ])
    assert host_setup.setup_messages(stdout) == [
        "Benutzer nodvard angelegt.",
        "Zur Gruppe docker hinzugefügt.",
        "Hinweis: sudo ist nicht installiert (als root: apt install sudo).",
        "FEHLER: x[31m rot",
        "FEHLER: " + "y" * 292,
    ]
    assert len(host_setup.setup_messages("\n".join(f"FEHLER: {i}" for i in range(100)))) == 20


# ---------------------------------------------------------------------------
# Bericht für den Datei-Wächter (nur wenn Nodvard Deck das Skript selbst ausführt)
# ---------------------------------------------------------------------------


def test_state_report_is_only_in_the_script_deck_runs_itself():
    for user, sudo in (("lattice", True), ("root", False)):
        plain, _ = build_setup_script(user, KEY, sudo=sudo)
        reported, one_liner = build_setup_script(user, KEY, sudo=sudo, report_state=True)
        assert "ST before" not in plain and host_setup.STATE_MARK not in plain
        assert "'" not in reported and one_liner.count("'") == 2
        lines = reported.split("\n")
        first_change = next(i for i, line in enumerate(lines) if "useradd" in line or "authorized_keys" in line and "ST " not in line)
        assert lines.index(next(line for line in lines if line.startswith("for f in") and "ST before" in line)) < first_change
        assert any("ST after" in line for line in lines[-4:]), "der Stand danach steht am Ende, vor der Abschlussprüfung"


@needs_sh
@pytest.mark.parametrize("user,sudo", [("lattice", True), ("root", False)])
def test_state_report_script_is_valid_shell_syntax(user, sudo):
    script, one_liner = build_setup_script(user, KEY, sudo=sudo, report_state=True)
    for args in (["sh", "-n", "-c", script], ["sh", "-n", "-c", one_liner]):
        done = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)
        assert done.returncode == 0, done.stderr


@needs_sh
def test_run_reports_the_state_before_and_after_exactly_like_the_file_guard(tmp_path):
    """Der Bericht nennt Prüfsumme, Rechte und Besitzer in genau der Form, in der der Datei-Wächter sie erfasst."""
    import hashlib
    import pwd

    bin_dir, home, _ = _sandbox(tmp_path, existing_user=False)
    done = _run(_script(report_state=True), bin_dir)
    assert done.returncode == 0, done.stderr
    report = host_setup.setup_report(done.stdout, user="lattice")
    assert report["actions"] == ["key_added", "user_created"]
    keys = str(home / ".ssh" / "authorized_keys")
    owner = pwd.getpwuid(os.getuid()).pw_name
    assert report["files"][keys] == {
        "before": None,
        "after": {"sha256": hashlib.sha256(f"restrict,pty {KEY}\n".encode()).hexdigest(), "mode": "600", "owner": owner},
    }
    passwd = Path("/etc/passwd")
    if os.access(passwd, os.R_OK):
        mode = format(stat.S_IMODE(passwd.stat().st_mode), "o")
        state = {"sha256": hashlib.sha256(passwd.read_bytes()).hexdigest(), "mode": mode, "owner": pwd.getpwuid(passwd.stat().st_uid).pw_name}
        assert report["files"]["/etc/passwd"] == {"before": state, "after": state}
    assert host_setup.STATE_MARK not in "\n".join(host_setup.setup_messages(done.stdout)), "keine Zeile für die Oberfläche"

    # Zweiter Lauf: Der Schlüssel steht schon da, also keine Meldung, und vorher = nachher.
    done = _run(_script(report_state=True), bin_dir)
    report = host_setup.setup_report(done.stdout, user="lattice")
    assert "key_added" not in report["actions"] and host_setup.KEY_ADDED not in done.stdout
    assert report["files"][keys]["before"] == report["files"][keys]["after"]


@needs_sh
def test_setup_record_knows_the_exact_sudo_rules_the_script_writes(tmp_path):
    import hashlib

    done, sudoers, _ = _sudoers_run(tmp_path, SUDO_RULE)
    assert done.returncode == 0, done.stderr
    report = host_setup.setup_report(done.stdout, user="lattice")
    assert "legacy_rule_removed" in report["actions"]
    record = host_setup.setup_record("lattice", report, sudo=True)
    written = hashlib.sha256((sudoers / "nodvard-lattice").read_bytes()).hexdigest()
    assert record["sudoers"] == {"/etc/sudoers.d/nodvard-lattice": written}
    assert record["legacy_sudoers"] == {"/etc/sudoers.d/lattice-lattice": hashlib.sha256(SUDO_RULE.encode()).hexdigest()}
    assert host_setup.setup_record("root", report, sudo=True)["sudoers"] == {}


def test_setup_record_counts_only_installed_helpers_that_were_offered():
    import hashlib

    from nodvard_deck.ext.root_helpers import RootHelperFile
    from nodvard_sdk import ROOT_HELPER_DIR

    helper = RootHelperFile(
        ext_id="shield", ext_name="Nodvard Shield", version=3, install_path=f"{ROOT_HELPER_DIR}/shield-root",
        text="#!/bin/sh\n", sha256="0" * 64,
    )
    out = (
        "Hilfsskript shield-root (Version 3) eingerichtet, mit eigener sudo-Regel.\n"
        "Hilfsskript fremd-root (Version 1) eingerichtet, mit eigener sudo-Regel.\n"
        "Hinweis: Das Hilfsskript docker-root wurde nicht eingerichtet.\n"
    )
    record = host_setup.setup_record("deck", host_setup.setup_report(out, user="deck"), sudo=True, helpers=[helper])
    expected = hashlib.sha256(f"deck ALL=(root) NOPASSWD: {ROOT_HELPER_DIR}/shield-root\n".encode()).hexdigest()
    assert record["sudoers"]["/etc/sudoers.d/nodvard_shield_deck"] == expected
    assert set(record["sudoers"]) == {"/etc/sudoers.d/nodvard-deck", "/etc/sudoers.d/nodvard_shield_deck"}


def test_setup_report_takes_only_fixed_forms():
    mark = host_setup.STATE_MARK
    sha = "a" * 64
    out = "\n".join([
        "Benutzer anderer angelegt.",
        "Benutzer lattice angelegt. und mehr",
        "Die alte sudo-Regel lattice-anderer wurde durch nodvard-anderer ersetzt.",
        f"{mark} before {sha} 644 root /etc/sudoers",
        f"{mark} before {sha} 644 root /etc/../etc/passwd",
        f"{mark} before {sha} 644 root /home/x/../root/.ssh/authorized_keys",
        f"{mark} before {sha[:-1]} 644 root /etc/passwd",
        f"{mark} before {sha} 9999 root /etc/group",
        f"{mark} before {sha} 644 ro;ot /etc/gshadow",
        f"{mark} sometime {sha} 644 root /etc/shadow",
        f"{mark} after - - - /home/lattice/.ssh/authorized_keys",
        f"  {mark} after {sha} 640 root /etc/shadow  ",
    ])
    report = host_setup.setup_report(out, user="lattice")
    assert report["actions"] == [] and report["helpers"] == []
    assert report["files"] == {
        "/home/lattice/.ssh/authorized_keys": {"after": None},
        "/etc/shadow": {"after": {"sha256": sha, "mode": "640", "owner": "root"}},
    }
