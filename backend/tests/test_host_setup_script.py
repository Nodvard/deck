"""services/host_setup -- der Einrichtungsbefehl fuer einen Server.

Jeder Wert, der in das Skript gelangt (Benutzer, Schluessel, Gruppen), wird streng geprueft
und das Ganze fuer `sh -c` mit `shlex.quote` verpackt. Die Tests hier prueft mit boesen Werten
(Anfuehrungszeichen, Zeilenumbrueche, `$(...)`), mit `sh -n` auf Syntax und fuehren das
Skript gegen Attrappen der Systembefehle wirklich aus."""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import asyncssh
import pytest

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


def _script(user="lattice", key=KEY, **kw) -> str:
    return build_setup_script(user, key, **kw)[0]


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
    assert 'printf "%s\\n" "restrict,pty $K"' in script
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
    check = script.index('visudo -c -q -f "$T"')
    install = script.index('install -m 0440 -o root -g root "$T" "$N"')
    whole = script.index("if visudo -c -q; then", install)
    rollback = script.index('else rm -f "$N"', whole)
    assert check < install < whole < rollback
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
    assert any("restrict" in n and "pty" in n for n in plain)
    assert not any("praktisch alles" in n for n in plain)
    full = host_setup.setup_notes("lattice", groups=("docker",), sudo=True)
    assert any("Gruppe docker ist oder sudo ohne Passwort darf" in n and "praktisch alles" in n for n in full)
    assert any("Wer sudo ohne Passwort darf, kann" in n for n in host_setup.setup_notes("lattice", groups=(), sudo=True))
    assert any("Wer in der Gruppe docker ist, kann" in n for n in host_setup.setup_notes("lattice", groups=("docker",), sudo=False))
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
    for bad in ("kein Schluessel GEHEIM", "", "-----BEGIN OPENSSH PRIVATE KEY-----\nGEHEIM\n-----END OPENSSH PRIVATE KEY-----\n"):
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
        _stub(bin_dir / "visudo", f'echo "visudo $*" >> "{log}"\n{whole}\n')
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
    assert "visudo -c -q" in calls, "danach wird die ganze Konfiguration nochmals geprueft"
    assert "FEHLER" not in done.stdout


@needs_sh
def test_run_rolls_back_the_sudo_rule_when_the_whole_config_is_broken(tmp_path):
    bin_dir, _, log = _sandbox(tmp_path, visudo_whole_fails=True)
    done = _run(_script(sudo=True), bin_dir)
    assert done.returncode != 0, "eine zurueckgenommene sudo-Regel ist kein Erfolg"
    assert "rm -f /etc/sudoers.d/nodvard-lattice" in log.read_text()
    assert "FEHLER: sudo-Regel zurückgenommen." in done.stdout
    assert "Fertig." not in done.stdout and "Nicht alles hat geklappt" in done.stdout


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
    whole = next(i for i, c in enumerate(calls) if c == "visudo -c -q")
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
    done, sudoers, _ = _sudoers_run(tmp_path, SUDO_RULE, visudo_whole_fails=True)
    assert done.returncode != 0
    # Die neue Regel wird zurueckgenommen, die alte bleibt: der Server hat danach genau den Stand von vorher.
    assert sorted(p.name for p in sudoers.iterdir()) == ["lattice-lattice"]
    assert (sudoers / "lattice-lattice").read_text() == SUDO_RULE
    assert "sudo-Regel zurückgenommen" in done.stdout and "ersetzt" not in done.stdout


@needs_sh
def test_run_restores_the_old_rule_when_the_config_breaks_after_removing_it(tmp_path):
    # Zweiter Aufruf der ganzen Pruefung (nach dem Entfernen) scheitert.
    done, sudoers, _ = _sudoers_run(tmp_path, SUDO_RULE, visudo_whole_fails_on_call=2)
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
