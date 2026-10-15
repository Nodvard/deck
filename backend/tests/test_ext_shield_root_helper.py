"""Nodvard Shield: die nur lesenden Abfragen über das Hilfsskript `shield-root` (ab Version 2): `guard` (Einbruchschutz
und Datei-Wächter), `detect-templates` (Wächter-Vorlagen) und `refresh-packages` (Paketlisten der Update-Zentrale).

Ohne root-Anmeldung nimmt der Befehl zuerst das Hilfsskript, wenn es Byte für Byte das mitgelieferte ist (sha256) und sudo
es erlaubt, sonst den alten Weg. Ein verändertes oder veraltetes Skript läuft nie, auch nicht für `version`; ohne anderen
Weg sagt die Meldung, dass der Server neu eingerichtet werden muss. Das Skript muss genau dasselbe ausgeben wie der
bisherige Befehl: Das prüfen die Gleichlauf-Tests, die beide in derselben Umgebung (Attrappen im PATH) laufen lassen."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "shield"
SCRIPT = EXT_DIR / "root-helper" / "shield-root.sh"
FIXED_PATH = "PATH=/usr/sbin:/usr/bin:/sbin:/bin"
SH = shutil.which("sh") or "sh"

needs_sh = pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None, reason="braucht eine echte POSIX-Shell")


@pytest.fixture(autouse=True)
def _ext_shield_on_path(monkeypatch, tmp_path):
    """Die Erweiterung ist kein installiertes Paket: Pfad nur für diese Tests setzen und die Module danach vergessen.
    Das Hilfsskript liegt im Test nicht unter /usr/local/lib, sondern in `tmp_path`."""
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    from nodvard_deck_ext_shield import antivirus

    monkeypatch.setattr(antivirus, "HELPER", str(tmp_path / "shield-root"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


def _av():
    from nodvard_deck_ext_shield import antivirus

    return antivirus


def _ix():
    from nodvard_deck_ext_shield import intrusion

    return intrusion


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _helper_copy(
    tmp_path: Path, bin_dir: Path, *, version: int | None = None, installed: bool = True, extra: str = ""
) -> Path:
    """Kopie des echten Skripts unter `antivirus.HELPER`, deren fester PATH zuerst auf die Attrappen zeigt; `extra` ist
    eine Zeile hinter dem Kopf."""
    helper = Path(_av().HELPER)
    text = _copy_text(bin_dir, version)
    if extra:
        text = text.replace("\n", f"\n{extra}\n", 1)
    helper.write_text(text)
    helper.chmod(0o755 if installed else 0o644)
    return helper


def _copy_text(bin_dir: Path, version: int | None) -> str:
    text = SCRIPT.read_text(encoding="ascii").replace(FIXED_PATH, f"PATH={bin_dir}:/usr/bin:/bin")
    if version is not None:
        text = re.sub(r"(?m)^HELPER_VERSION=\d+$", f"HELPER_VERSION={version}", text)
    return text


def _env(bin_dir: Path) -> dict[str, str]:
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "LC_ALL": "C"}


def _sh(command: str, bin_dir: Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([SH, "-c", command], capture_output=True, text=True, timeout=60, check=False, env=_env(bin_dir), cwd=cwd)


def _helper(helper: Path, bin_dir: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([SH, str(helper), *args], capture_output=True, text=True, timeout=60, check=False, env=_env(bin_dir), cwd=cwd)


# --- Der Befehl: erst das Hilfsskript, dann der alte Weg -----------------------------------------------------------


def test_the_command_tries_the_helper_first(monkeypatch):
    av = _av()
    from nodvard_sdk import root_helper_path

    monkeypatch.setattr(av, "HELPER", root_helper_path("shield"))
    cmd = av.via_helper(["guard", "/etc/passwd", "--", "/usr/bin/sudo"], "echo alt", version=2, required=False)
    path = "/usr/local/lib/nodvard-deck/shield-root"
    matches = f"[ -x {path} ] && [ \"$(sha256sum < {path} 2>/dev/null)\" = \"{av.HELPER_BUNDLED[1]}  -\" ]"
    assert av.helper_matches() == matches
    assert cmd == (
        "if [ \"$(id -u)\" -eq 0 ]; then sh -c 'echo alt'; "
        f"elif {matches} && [ \"$(sudo -n {path} version 2>/dev/null)\" -ge 2 ] 2>/dev/null; then "
        f"sudo -n {path} guard /etc/passwd -- /usr/bin/sudo; "
        "elif sudo -n true 2>/dev/null; then sudo -n sh -c 'echo alt'; "
        "else sh -c 'echo alt'; fi"
    )
    assert av.via_helper(["x"], "echo alt", version=2).endswith(
        f"else echo '@@noroot'; if [ -e {path} ] && ! {{ {matches}; }}; then "
        f"echo \"@@noroot-helper $(sed -n 's/^HELPER_VERSION=//p' {path} 2>/dev/null | head -n 1)\"; fi; exit 126; fi"
    )


def test_the_module_compares_against_the_file_the_setup_brings():
    manifest = tomllib.loads((EXT_DIR / "extension.toml").read_text(encoding="utf-8"))["extension"]
    assert _av().HELPER_BUNDLED == (manifest["root_helper"]["version"], hashlib.sha256(SCRIPT.read_bytes()).hexdigest())


def test_without_the_shipped_file_only_the_old_way_is_left(monkeypatch):
    av = _av()
    monkeypatch.setattr(av, "HELPER_BUNDLED", None)
    assert av.helper_matches() is None
    assert av.via_helper(["x"], "echo alt", version=2) == av.as_root("echo alt")
    # Ein Lauf, den das mitgelieferte Skript nicht kann, geht auch nie an ein Skript auf dem Server.
    monkeypatch.setattr(av, "HELPER_BUNDLED", (2, "0" * 64))
    assert av.via_helper(["x"], "echo alt", version=3, required=False) == av.as_root("echo alt", required=False)


def test_the_needed_version_is_one_the_module_brings():
    manifest = tomllib.loads((EXT_DIR / "extension.toml").read_text(encoding="utf-8"))["extension"]
    assert _av().HELPER_READ_VERSION <= manifest["root_helper"]["version"]


def test_the_commands_pass_the_deny_patterns():
    from nodvard_deck.core.deny_patterns import match_deny_patterns
    from nodvard_deck_ext_shield import guard, updates, watch_templates

    for cmd in (guard.guard_as_root(None), watch_templates.detect_command(), updates.status_command(refresh=True)):
        assert match_deny_patterns(cmd) is None, cmd


def test_a_trailing_newline_in_a_watched_file_is_dropped():
    """`re.match` mit `$` ließ `/etc/x\\n` durch; der Befehl brach dann mit einem Syntaxfehler ab."""
    ix = _ix()
    assert ix.guard_files(["/etc/x\n", "/etc/passwd", "/home/*/.ssh/authorized_keys"]) == ["/etc/passwd", "/home/*/.ssh/authorized_keys"]
    assert ix.guard_args(["/etc/passwd"]) == ["guard", "/etc/passwd", "--", *ix.WATCH_BINARIES]


def _routing_world(
    tmp_path: Path, *, helper_version: int | None, full_rule: bool, installed: bool = True, extra: str = ""
):
    """`sudo` als Attrappe: erlaubt das Hilfsskript (mit `helper_version`) und, bei `full_rule`, `true` und `sh -c`.
    Als mitgeliefert gilt die Kopie mit Version 2 (`HELPER_BUNDLED`); jede andere Version oder `extra` ist ein anderer
    Inhalt. `None`: Version 2, aber sudo erlaubt das Skript nicht."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    log.write_text("")
    _stub(bin_dir / "id", 'if [ "$1" = -u ]; then echo 1000; else exec /usr/bin/id "$@"; fi\n')
    _av().HELPER_BUNDLED = (2, hashlib.sha256(_copy_text(bin_dir, 2).encode("ascii")).hexdigest())
    helper = _helper_copy(tmp_path, bin_dir, version=helper_version or 2, installed=installed, extra=extra)
    allowed = "1" if helper_version is not None else ""
    full = "1" if full_rule else ""
    _stub(bin_dir / "sudo", f'''echo "sudo $*" >> "{log}"
[ "$1" = -n ] || exit 1
shift
case "$1" in
  {helper}) [ -n "{allowed}" ] || exit 1; shift; exec sh "{helper}" "$@";;
  true) [ -n "{full}" ];;
  sh) [ -n "{full}" ] || exit 1; exec "$@";;
  *) exit 1;;
esac
''')
    return bin_dir, log


@needs_sh
def test_with_a_fitting_helper_only_the_helper_runs(tmp_path):
    av = _av()
    bin_dir, log = _routing_world(tmp_path, helper_version=2, full_rule=False)
    done = _sh(av.via_helper(["refresh-packages"], "echo alt", version=2, required=True), bin_dir)
    assert done.returncode == 0 and "alt" not in done.stdout
    assert log.read_text().splitlines() == [f"sudo -n {av.HELPER} version", f"sudo -n {av.HELPER} refresh-packages"]


@needs_sh
@pytest.mark.parametrize(
    ("helper_version", "installed", "extra"),
    [(1, True, ""), (3, True, ""), (2, True, 'touch "$0.lief"'), (None, True, ""), (2, False, "")],
    ids=["alt", "neuer", "veraendert", "nicht-erlaubt", "nicht-ausfuehrbar"],
)
def test_otherwise_the_old_way_runs(tmp_path, helper_version, installed, extra):
    """Nur das mitgelieferte Skript wird gefragt (`version`), ein anderes läuft nie, auch nicht für die Version."""
    av = _av()
    bin_dir, log = _routing_world(tmp_path, helper_version=helper_version, full_rule=True, installed=installed, extra=extra)
    done = _sh(av.via_helper(["refresh-packages"], "echo alt", version=2, required=True), bin_dir)
    assert done.stdout.strip() == "alt"
    calls = log.read_text().splitlines()
    assert calls[-2:] == ["sudo -n true", "sudo -n sh -c echo alt"]
    assert (f"sudo -n {av.HELPER} version" in calls) == (helper_version is None)
    assert not Path(f"{av.HELPER}.lief").exists()


@needs_sh
def test_without_any_root_a_read_runs_as_the_user(tmp_path):
    av = _av()
    bin_dir, _ = _routing_world(tmp_path, helper_version=None, full_rule=False)
    assert _sh(av.via_helper(["x"], "echo als-benutzer", version=2, required=False), bin_dir).stdout.strip() == "als-benutzer"
    required = _sh(av.via_helper(["x"], "echo als-benutzer", version=2, required=True), bin_dir)
    assert (required.stdout.strip(), required.returncode) == (av.NO_ROOT, 126)
    assert av.no_root_message(required.stdout) == av.NO_ROOT_MESSAGE


@needs_sh
@pytest.mark.parametrize(
    ("helper_version", "extra", "what"),
    [
        (1, "", "veraltet (Version 1, dieses Dashboard bringt Version 2 mit)"),
        (2, "# geaendert", "verändert (Version 2, aber mit anderem Inhalt)"),
        (3, "", "verändert oder neuer als dieses Dashboard"),
    ],
    ids=["alt", "veraendert", "neuer"],
)
def test_with_only_a_foreign_helper_the_message_asks_to_set_up_again(tmp_path, helper_version, extra, what):
    """Nur die enge Regel und ein Skript, das nicht das mitgelieferte ist: nicht „keine root-Rechte“, sondern der Hinweis
    auf das Skript mit „neu einrichten“."""
    av = _av()
    bin_dir, log = _routing_world(tmp_path, helper_version=helper_version, full_rule=False, extra=extra)
    done = _sh(av.via_helper(["refresh-packages"], "echo alt", version=2, required=True), bin_dir)
    assert done.returncode == 126 and done.stdout.split()[0] == av.NO_ROOT
    message = av.no_root_message(done.stdout)
    assert message.startswith(f"Das Hilfsskript auf diesem Server ist {what}, darum ruft Nodvard Deck es nicht auf")
    assert "Einrichtungsbefehl" in message
    assert not any(av.HELPER in c for c in log.read_text().splitlines())


def test_the_message_believes_only_a_plain_version_number():
    av = _av()
    unknown = "Das Hilfsskript auf diesem Server ist verändert oder neuer als dieses Dashboard"
    for line in ("@@noroot-helper 8; rm -rf /", "@@noroot-helper ", "@@noroot-helper", "@@noroot-helper 99999"):
        assert av.no_root_message(f"@@noroot\n{line}\n").startswith(unknown), line
    assert av.no_root_message("@@noroot\n") == av.NO_ROOT_MESSAGE


# --- Gleichlauf: das Skript gibt aus, was der alte Befehl ausgibt -----------------------------------------------------


def _guard_world(tmp_path: Path) -> tuple[Path, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir / "journalctl", 'printf "%s\\n" "1700000000.0 host sshd[1]: Failed password for root from 203.0.113.9 port 22 ssh2" '
          '"1700000001.0 host sshd[1]: Accepted publickey for deck from 198.51.100.7 port 22 ssh2" "kein Treffer"\n')
    _stub(bin_dir / "fail2ban-client", '''case "$1 $2" in
  "ping ") echo pong;;
  "status ") printf "Status\\n|- Number of jail:\\t1\\n\\`- Jail list:\\tsshd\\n";;
  "status sshd") printf "Status for the jail: sshd\\n|- Currently banned:\\t1\\n";;
esac
''')
    _stub(bin_dir / "sshd", 'printf "port 22\\npermitrootlogin no\\nusepam yes\\npasswordauthentication no\\n"\n')
    _stub(bin_dir / "ss", 'printf "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:((\\"sshd\\",pid=1,fd=3))\\nNetid x\\n"\n')
    files_dir = tmp_path / "etc"
    (files_dir / "conf.d").mkdir(parents=True)
    for name in ("passwd", "conf.d/a.conf", "conf.d/b.conf"):
        (files_dir / name).write_text(name + "\n")
    patterns = [str(files_dir / "passwd"), str(files_dir / "conf.d" / "*.conf"), str(files_dir / "fehlt"), str(files_dir / "leer" / "*")]
    return bin_dir, patterns


@needs_sh
def test_guard_gives_the_same_output_as_the_old_command(tmp_path):
    ix = _ix()
    bin_dir, patterns = _guard_world(tmp_path)
    helper = _helper_copy(tmp_path, bin_dir)
    old = _sh(ix.guard_command(patterns), bin_dir, cwd=tmp_path)
    new = _helper(helper, bin_dir, *ix.guard_args(patterns), cwd=tmp_path)
    assert new.returncode == 0, new.stderr
    assert new.stdout == old.stdout
    # Nicht nur leere Abschnitte: Die Attrappen und Dateien kommen wirklich an.
    for expected in ("Failed password for root", "running", "@@jail sshd", "permitrootlogin no", "0.0.0.0:22", "conf.d/b.conf"):
        assert expected in new.stdout, expected


@needs_sh
def test_guard_with_the_default_lists_gives_the_same_output(tmp_path):
    """Die echten Standardlisten (Systemdateien, Programme mit dpkg-Abgleich), so wie sie auf dem Server laufen."""
    ix = _ix()
    bin_dir, _ = _guard_world(tmp_path)
    helper = _helper_copy(tmp_path, bin_dir)
    old = _sh(ix.guard_command(None), bin_dir, cwd=tmp_path)
    new = _helper(helper, bin_dir, *ix.guard_args(None), cwd=tmp_path)
    assert new.returncode == 0, new.stderr
    assert new.stdout == old.stdout and "@@bins" in new.stdout


@needs_sh
@pytest.mark.parametrize(
    "args",
    [
        [], ["/etc/passwd"], ["etc/passwd", "--"], ["/etc/../shadow", "--"], ["/etc/x;id", "--"], ["/etc/$(id)", "--"],
        ["/etc/a b", "--"], ["--", "/usr/bin/*"], ["--", "relativ"], ["/etc/x\n", "--"],
    ],
)
def test_guard_refuses_strange_arguments(tmp_path, args):
    bin_dir, _ = _guard_world(tmp_path)
    helper = _helper_copy(tmp_path, bin_dir)
    done = _helper(helper, bin_dir, "guard", *args)
    assert (done.returncode, done.stdout) == (64, ""), (args, done.stderr)


def _detect_world(tmp_path: Path) -> Path:
    """`grep` und `sed` lesen statt der festen Systempfade Dateien im tmp-Ordner."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    root = tmp_path / "root"
    (root / "etc" / "samba").mkdir(parents=True)
    (root / "etc" / "samba" / "smb.conf").write_text("[global]\n   workgroup = X\n[daten]\n   path = /srv/daten\n")
    (root / "etc" / "exports").write_text("# Kommentar\n/srv/nfs 10.0.0.0/8(rw)\n")
    (root / "var" / "www" / "nextcloud" / "config").mkdir(parents=True)
    (root / "var" / "www" / "nextcloud" / "config" / "config.php").write_text("  'datadirectory' => '/srv/nextcloud',\n")
    for tool in ("grep", "sed"):
        real = shutil.which(tool, path="/usr/bin:/bin")
        _stub(bin_dir / tool, f'''n=0
for a in "$@"; do
  case "$a" in
    /etc/*|/var/www/*) a="{root}$a";;
  esac
  if [ "$n" -eq 0 ]; then set --; n=1; fi
  set -- "$@" "$a"
done
exec {real} "$@"
''')
    return bin_dir


@needs_sh
def test_detect_templates_gives_the_same_output_as_the_old_command(tmp_path):
    from nodvard_deck_ext_shield import watch_templates as wt

    bin_dir = _detect_world(tmp_path)
    helper = _helper_copy(tmp_path, bin_dir)
    old = _sh(wt.DETECT_SCRIPT, bin_dir)
    new = _helper(helper, bin_dir, "detect-templates")
    assert new.returncode == 0, new.stderr
    assert new.stdout == old.stdout
    for expected in ("[daten]", "path = /srv/daten", "/srv/nfs", "/srv/nextcloud"):
        assert expected in new.stdout, expected
    assert _helper(helper, bin_dir, "detect-templates", "x").returncode == 64


@needs_sh
def test_refresh_packages_gives_the_same_output_as_the_old_command(tmp_path):
    from nodvard_deck_ext_shield import updates as up

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir / "apt-get", 'printf "Hit:1 x\\nE: kaputt\\nW: Warnung\\nErr:2 y\\n" >&2\n')
    helper = _helper_copy(tmp_path, bin_dir)
    old = _sh(up.REFRESH_COMMAND, bin_dir)
    new = _helper(helper, bin_dir, "refresh-packages")
    assert new.returncode == 0, new.stderr
    assert new.stdout == old.stdout == "E: kaputt\nW: Warnung\nErr:2 y\n"
    assert _helper(helper, bin_dir, "refresh-packages", "x").returncode == 64
